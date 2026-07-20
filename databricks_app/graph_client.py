"""
Microsoft Graph API client for reading/adding/removing Azure AD B2C group members,
using a service principal (client credentials flow).

Deliberately uses only the `requests` library (no msal / azure-identity /
azure-keyvault-secrets) — the Databricks Apps build environment only has a
curated/cached subset of PyPI available and could not install those SDKs.
Token acquisition and Key Vault secret retrieval are done as plain REST calls.

The tenant_id/client_id/client_secret used to call Graph are stored in Azure Key
Vault, not in Databricks secrets. To reach Key Vault in the first place, the app
still needs one bootstrap credential (the AZURE_TENANT_ID / AZURE_CLIENT_ID /
AZURE_CLIENT_SECRET below, sourced from Databricks secrets) — this must belong to
a service principal granted "Key Vault Secrets User" on the vault.

Required environment variables:
    AZURE_TENANT_ID        - bootstrap SP tenant ID (used to authenticate to Key Vault)
    AZURE_CLIENT_ID         - bootstrap SP client ID
    AZURE_CLIENT_SECRET     - bootstrap SP client secret
    AZURE_KEY_VAULT_URL     - e.g. https://<your-vault-name>.vault.azure.net/

Key Vault secret names (override via env vars if yours differ):
    AZURE_KV_TENANT_ID_SECRET_NAME      (default: "graph-tenant-id")
    AZURE_KV_CLIENT_ID_SECRET_NAME      (default: "graph-client-id")
    AZURE_KV_CLIENT_SECRET_SECRET_NAME  (default: "graph-client-secret")

Required Graph API application permissions (admin-consented) on the Graph SP:
    Group.Read.All (or Group.ReadWrite.All)  - to list groups
    GroupMember.ReadWrite.All                - to list/add/remove members
    User.Read.All                            - to resolve users by email/UPN
"""

from __future__ import annotations

import os

import requests

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
KEY_VAULT_API_VERSION = "7.4"


class GraphApiError(RuntimeError):
    pass


def _get_oauth_token(tenant_id: str, client_id: str, client_secret: str, scope: str) -> str:
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope,
    }
    resp = requests.post(url, data=data)
    if not resp.ok:
        raise GraphApiError(f"Failed to acquire token ({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


def _load_graph_credentials() -> tuple[str, str, str]:
    vault_url = os.environ.get("AZURE_KEY_VAULT_URL")
    bootstrap_tenant_id = os.environ.get("AZURE_TENANT_ID")
    bootstrap_client_id = os.environ.get("AZURE_CLIENT_ID")
    bootstrap_client_secret = os.environ.get("AZURE_CLIENT_SECRET")

    missing = [
        name
        for name, val in (
            ("AZURE_KEY_VAULT_URL", vault_url),
            ("AZURE_TENANT_ID", bootstrap_tenant_id),
            ("AZURE_CLIENT_ID", bootstrap_client_id),
            ("AZURE_CLIENT_SECRET", bootstrap_client_secret),
        )
        if not val
    ]
    if missing:
        raise GraphApiError(f"Missing required environment variable(s): {', '.join(missing)}")

    tenant_secret_name = os.environ.get("AZURE_KV_TENANT_ID_SECRET_NAME", "graph-tenant-id")
    client_id_secret_name = os.environ.get("AZURE_KV_CLIENT_ID_SECRET_NAME", "graph-client-id")
    client_secret_secret_name = os.environ.get(
        "AZURE_KV_CLIENT_SECRET_SECRET_NAME", "graph-client-secret"
    )

    kv_token = _get_oauth_token(
        bootstrap_tenant_id, bootstrap_client_id, bootstrap_client_secret, "https://vault.azure.net/.default"
    )
    kv_headers = {"Authorization": f"Bearer {kv_token}"}

    def get_secret(name: str) -> str:
        url = f"{vault_url.rstrip('/')}/secrets/{name}"
        resp = requests.get(url, headers=kv_headers, params={"api-version": KEY_VAULT_API_VERSION})
        if not resp.ok:
            raise GraphApiError(f"Failed to read secret '{name}' from Key Vault ({resp.status_code}): {resp.text}")
        return resp.json()["value"]

    tenant_id = get_secret(tenant_secret_name)
    client_id = get_secret(client_id_secret_name)
    client_secret = get_secret(client_secret_secret_name)

    return tenant_id, client_id, client_secret


def get_access_token() -> str:
    tenant_id, client_id, client_secret = _load_graph_credentials()
    return _get_oauth_token(tenant_id, client_id, client_secret, "https://graph.microsoft.com/.default")


def _headers(token: str, consistency: bool = False) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if consistency:
        headers["ConsistencyLevel"] = "eventual"
    return headers


def _get_paged(url: str, headers: dict, params: dict | None = None) -> list[dict]:
    items: list[dict] = []
    resp = requests.get(url, headers=headers, params=params)
    while True:
        if not resp.ok:
            raise GraphApiError(f"Graph request failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        items.extend(data.get("value", []))
        next_link = data.get("@odata.nextLink")
        if not next_link:
            break
        resp = requests.get(next_link, headers=headers)
    return items


def list_all_groups(token: str, search: str | None = None) -> list[dict]:
    url = f"{GRAPH_BASE_URL}/groups"
    params = {"$select": "id,displayName,mailNickname,description"}

    if search:
        headers = _headers(token, consistency=True)
        params["$search"] = f'"displayName:{search}"'
    else:
        headers = _headers(token)
        params["$top"] = "999"

    return _get_paged(url, headers, params)


def list_group_members(token: str, group_id: str) -> list[dict]:
    url = f"{GRAPH_BASE_URL}/groups/{group_id}/members"
    return _get_paged(url, _headers(token))


def resolve_user(token: str, identifier: str) -> dict:
    """Look up a user by object ID, UPN, or email address."""
    headers = _headers(token, consistency=True)

    # Object IDs are GUIDs; try a direct lookup first.
    resp = requests.get(f"{GRAPH_BASE_URL}/users/{identifier}", headers=_headers(token))
    if resp.ok:
        return resp.json()

    params = {
        "$filter": f"mail eq '{identifier}' or userPrincipalName eq '{identifier}'",
        "$select": "id,displayName,userPrincipalName,mail",
    }
    resp = requests.get(f"{GRAPH_BASE_URL}/users", headers=headers, params=params)
    if not resp.ok:
        raise GraphApiError(f"Failed to resolve user '{identifier}' ({resp.status_code}): {resp.text}")

    results = resp.json().get("value", [])
    if not results:
        raise GraphApiError(f"No user found matching '{identifier}'")
    return results[0]


def add_group_member(token: str, group_id: str, user_id: str) -> None:
    url = f"{GRAPH_BASE_URL}/groups/{group_id}/members/$ref"
    body = {"@odata.id": f"{GRAPH_BASE_URL}/directoryObjects/{user_id}"}

    resp = requests.post(url, headers=_headers(token), json=body)
    if resp.status_code != 204:
        raise GraphApiError(f"Failed to add member ({resp.status_code}): {resp.text}")


def remove_group_member(token: str, group_id: str, user_id: str) -> None:
    url = f"{GRAPH_BASE_URL}/groups/{group_id}/members/{user_id}/$ref"

    resp = requests.delete(url, headers=_headers(token))
    if resp.status_code != 204:
        raise GraphApiError(f"Failed to remove member ({resp.status_code}): {resp.text}")
