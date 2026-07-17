#!/usr/bin/env python3
"""
Test script for reading/adding/removing members of an Azure AD B2C group
via Microsoft Graph API, using a service principal (client credentials flow).

Setup:
    pip install msal requests

Required environment variables:
    AZURE_TENANT_ID       - tenant ID (or B2C tenant domain, e.g. contoso.onmicrosoft.com)
    AZURE_CLIENT_ID       - service principal (app registration) client ID
    AZURE_CLIENT_SECRET   - service principal client secret

Required Graph API application permissions (admin-consented), e.g.:
    GroupMember.ReadWrite.All  (or Group.ReadWrite.All)

Usage examples:
    python graph_group_test.py list-members --group-id <GROUP_ID>
    python graph_group_test.py add-member --group-id <GROUP_ID> --user-id <USER_ID>
    python graph_group_test.py remove-member --group-id <GROUP_ID> --user-id <USER_ID>
"""

import argparse
import os
import sys

import msal
import requests

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
SCOPES = ["https://graph.microsoft.com/.default"]


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
        sys.exit(f"Missing required environment variable(s): {', '.join(missing)}")

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.ConfidentialClientApplication(
        client_id, authority=authority, client_credential=client_secret
    )

    result = app.acquire_token_for_client(scopes=SCOPES)

    if "access_token" not in result:
        sys.exit(
            "Failed to acquire token: "
            f"{result.get('error')} - {result.get('error_description')}"
        )

    return result["access_token"]


def graph_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def list_group_members(token: str, group_id: str) -> None:
    url = f"{GRAPH_BASE_URL}/groups/{group_id}/members"
    headers = graph_headers(token)

    members = []
    while url:
        resp = requests.get(url, headers=headers)
        if not resp.ok:
            sys.exit(f"Failed to list members ({resp.status_code}): {resp.text}")
        data = resp.json()
        members.extend(data.get("value", []))
        url = data.get("@odata.nextLink")

    if not members:
        print("No members found.")
        return

    print(f"Found {len(members)} member(s):")
    for m in members:
        display_name = m.get("displayName", "<no display name>")
        upn = m.get("userPrincipalName", "<no upn>")
        obj_id = m.get("id")
        print(f"  - {display_name} | {upn} | {obj_id}")


def add_group_member(token: str, group_id: str, user_id: str) -> None:
    url = f"{GRAPH_BASE_URL}/groups/{group_id}/members/$ref"
    headers = graph_headers(token)
    body = {"@odata.id": f"{GRAPH_BASE_URL}/directoryObjects/{user_id}"}

    resp = requests.post(url, headers=headers, json=body)
    if resp.status_code == 204:
        print(f"Added user {user_id} to group {group_id}.")
    else:
        sys.exit(f"Failed to add member ({resp.status_code}): {resp.text}")


def remove_group_member(token: str, group_id: str, user_id: str) -> None:
    url = f"{GRAPH_BASE_URL}/groups/{group_id}/members/{user_id}/$ref"
    headers = graph_headers(token)

    resp = requests.delete(url, headers=headers)
    if resp.status_code == 204:
        print(f"Removed user {user_id} from group {group_id}.")
    else:
        sys.exit(f"Failed to remove member ({resp.status_code}): {resp.text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Azure AD B2C group membership test tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser("list-members", help="List members of a group")
    p_list.add_argument("--group-id", required=True, help="Object ID of the group")

    p_add = subparsers.add_parser("add-member", help="Add a user to a group")
    p_add.add_argument("--group-id", required=True, help="Object ID of the group")
    p_add.add_argument("--user-id", required=True, help="Object ID of the user")

    p_remove = subparsers.add_parser("remove-member", help="Remove a user from a group")
    p_remove.add_argument("--group-id", required=True, help="Object ID of the group")
    p_remove.add_argument("--user-id", required=True, help="Object ID of the user")

    args = parser.parse_args()
    token = get_access_token()

    if args.command == "list-members":
        list_group_members(token, args.group_id)
    elif args.command == "add-member":
        add_group_member(token, args.group_id, args.user_id)
    elif args.command == "remove-member":
        remove_group_member(token, args.group_id, args.user_id)


if __name__ == "__main__":
    main()
