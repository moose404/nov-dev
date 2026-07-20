"""
Microsoft Graph API client for reading/adding/removing Azure AD B2C group members,
using a service principal (client credentials flow).

Required environment variables:
    AZURE_TENANT_ID       - tenant ID (or B2C tenant domain, e.g. contoso.onmicrosoft.com)
    AZURE_CLIENT_ID       - service principal (app registration) client ID
    AZURE_CLIENT_SECRET   - service principal client secret

Required Graph API application permissions (admin-consented):
    Group.Read.All (or Group.ReadWrite.All)  - to list groups
    GroupMember.ReadWrite.All                - to list/add/remove members
    User.Read.All                            - to resolve users by email/UPN
"""

from __future__ import annotations

import os

import msal
import requests

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
SCOPES = ["https://graph.microsoft.com/.default"]


class GraphApiError(RuntimeError):
    pass


def get_access_token() -> str:
    tenant_id = os.environ.get("AZURE_TENANT_ID")
    client_id = os.environ.get("AZURE_CLIENT_ID")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET")

    missing = [
        name
        for name, val in (
            ("AZURE_TENANT_ID", tenant_id),
            ("AZURE_CLIENT_ID", client_id),
            ("AZURE_CLIENT_SECRET", client_secret),
        )
        if not val
    ]
    if missing:
        raise GraphApiError(f"Missing required environment variable(s): {', '.join(missing)}")

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.ConfidentialClientApplication(
        client_id, authority=authority, client_credential=client_secret
    )

    result = app.acquire_token_for_client(scopes=SCOPES)

    if "access_token" not in result:
        raise GraphApiError(
            f"Failed to acquire token: {result.get('error')} - {result.get('error_description')}"
        )

    return result["access_token"]


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
