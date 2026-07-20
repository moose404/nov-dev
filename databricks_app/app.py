"""
Databricks App (Streamlit) for managing Azure AD B2C group membership via Microsoft Graph.

Lets you:
  - List all groups (with optional search)
  - List all members of a selected group
  - Add a member to a group (by object ID, UPN, or email)
  - Remove a member from a group
"""

from __future__ import annotations

import streamlit as st

import graph_client as graph

st.set_page_config(page_title="Azure AD Group Manager", layout="wide")


@st.cache_resource(ttl=3000)  # tokens are valid ~1hr; refresh well before expiry
def get_token() -> str:
    return graph.get_access_token()


@st.cache_data(ttl=60)
def cached_list_groups(search: str | None) -> list[dict]:
    return graph.list_all_groups(get_token(), search or None)


@st.cache_data(ttl=30)
def cached_list_members(group_id: str) -> list[dict]:
    return graph.list_group_members(get_token(), group_id)


def clear_member_cache() -> None:
    cached_list_members.clear()


st.title("Azure AD B2C Group Manager")

try:
    get_token()
except graph.GraphApiError as e:
    st.error(f"Authentication failed: {e}")
    st.stop()

with st.sidebar:
    st.header("Groups")
    search = st.text_input("Search by display name", value="")
    if st.button("Refresh groups"):
        cached_list_groups.clear()

    try:
        groups = cached_list_groups(search)
    except graph.GraphApiError as e:
        st.error(str(e))
        st.stop()

    if not groups:
        st.info("No groups found.")
        st.stop()

    group_labels = {f"{g['displayName']} ({g['id']})": g for g in groups}
    selected_label = st.radio("Select a group", list(group_labels.keys()))
    selected_group = group_labels[selected_label]

st.subheader(f"Group: {selected_group['displayName']}")
st.caption(f"Object ID: `{selected_group['id']}`")
if selected_group.get("description"):
    st.write(selected_group["description"])

col_refresh, _ = st.columns([1, 5])
with col_refresh:
    if st.button("Refresh members"):
        clear_member_cache()

try:
    members = cached_list_members(selected_group["id"])
except graph.GraphApiError as e:
    st.error(str(e))
    members = []

st.markdown(f"**{len(members)} member(s)**")

for member in members:
    c1, c2, c3 = st.columns([3, 3, 1])
    with c1:
        st.write(member.get("displayName", "<no display name>"))
    with c2:
        st.write(member.get("userPrincipalName", member.get("id")))
    with c3:
        if st.button("Remove", key=f"remove-{member['id']}"):
            try:
                graph.remove_group_member(get_token(), selected_group["id"], member["id"])
                st.success(f"Removed {member.get('displayName', member['id'])}")
                clear_member_cache()
                st.rerun()
            except graph.GraphApiError as e:
                st.error(str(e))

st.divider()
st.subheader("Add a member")
with st.form("add-member-form", clear_on_submit=True):
    identifier = st.text_input("User object ID, UPN, or email")
    submitted = st.form_submit_button("Add to group")

    if submitted and identifier:
        try:
            user = graph.resolve_user(get_token(), identifier.strip())
            graph.add_group_member(get_token(), selected_group["id"], user["id"])
            st.success(f"Added {user.get('displayName', user['id'])} to group")
            clear_member_cache()
            st.rerun()
        except graph.GraphApiError as e:
            st.error(str(e))
