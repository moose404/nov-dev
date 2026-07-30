"""
Databricks App (Streamlit) for managing RLS (row-level security) access.

Lets you:
  - Add, edit, and remove users (name, email, tenant)
  - For a selected user, pick a value (or ALL, for no restriction) at each level
    of the customer hierarchy, including Reporting Group Name — useful for
    internal users who need visibility across every reporting group. The
    resulting set of matching CustomerIDs is materialized into
    rtl_vs.rls_admin.rls_resolved_access, which Power BI's RLS role filters
    against directly.
"""

from __future__ import annotations

import streamlit as st

import db_client as db

st.set_page_config(page_title="RLS Manager", layout="wide")
st.title("RLS Manager")

try:
    users = db.list_users()
except db.RlsAdminError as e:
    st.error(f"Could not connect to the SQL warehouse: {e}")
    st.stop()

users_tab, rls_tab = st.tabs(["Users", "RLS Access"])

# --- Users tab -----------------------------------------------------------

with users_tab:
    st.subheader("Users")

    if users:
        for user in users:
            c1, c2, c3, c4, c5 = st.columns([2, 3, 2, 1, 1])
            with c1:
                st.write(user["name"])
            with c2:
                st.write(user["email"])
            with c3:
                st.write(user["tenant"])
            with c4:
                if st.button("Edit", key=f"edit-{user['user_id']}"):
                    st.session_state["editing_user_id"] = user["user_id"]
            with c5:
                if st.button("Delete", key=f"delete-{user['user_id']}"):
                    db.delete_user(user["user_id"])
                    st.success(f"Deleted {user['name']}")
                    st.rerun()
    else:
        st.info("No users yet.")

    editing_id = st.session_state.get("editing_user_id")
    editing_user = next((u for u in users if u["user_id"] == editing_id), None)

    st.divider()
    st.subheader("Edit user" if editing_user else "Add a user")

    with st.form("user-form", clear_on_submit=not editing_user):
        name = st.text_input("Name", value=editing_user["name"] if editing_user else "")
        email = st.text_input("Email", value=editing_user["email"] if editing_user else "")
        tenant = st.text_input("Tenant", value=editing_user["tenant"] if editing_user else "")
        submitted = st.form_submit_button("Save user")

        if submitted and name and email and tenant:
            if editing_user:
                db.update_user(editing_user["user_id"], name, email, tenant)
                st.success(f"Updated {name}")
                st.session_state["editing_user_id"] = None
            else:
                db.create_user(name, email, tenant)
                st.success(f"Added {name}")
            st.rerun()

    if editing_user and st.button("Cancel edit"):
        st.session_state["editing_user_id"] = None
        st.rerun()

# --- RLS Access tab --------------------------------------------------------

with rls_tab:
    st.subheader("RLS Access")

    if not users:
        st.info("Add a user first.")
        st.stop()

    @st.cache_data(ttl=120)
    def cached_distinct_values(column: str, filters_tuple: tuple[tuple[str, str], ...]) -> list[str]:
        return db.get_distinct_values(column, dict(filters_tuple))

    user_labels = {f"{u['name']} ({u['email']})": u for u in users}
    selected_label = st.selectbox("Select a user", list(user_labels.keys()))
    selected_user = user_labels[selected_label]

    existing_selection = db.get_selection(selected_user["user_id"]) or {}

    st.caption("'ALL' means no restriction at that level, including Reporting Group Name.")

    current_selection: dict[str, str] = {}
    for column in db.HIERARCHY_COLUMNS:
        label = db.COLUMN_LABELS[column]
        widget_key = f"rls-{selected_user['user_id']}-{column}"
        filters_tuple = tuple(sorted(current_selection.items()))
        distinct_values = cached_distinct_values(column, filters_tuple)
        options = [db.ALL_SENTINEL] + distinct_values

        if not options:
            st.warning(f"No values available for {label} given the selections above.")
            break

        default_value = existing_selection.get(column) if existing_selection else None
        index = options.index(default_value) if default_value in options else 0

        value = st.selectbox(label, options, index=index, key=widget_key)
        current_selection[column] = value

    if current_selection.get("reporting_group_name") == db.ALL_SENTINEL:
        st.warning(
            "Reporting Group Name is set to ALL — this grants access to every customer "
            "across all reporting groups. Use only for internal users who should see everything."
        )

    if len(current_selection) == len(db.HIERARCHY_COLUMNS):
        if existing_selection:
            resolved_count = db.get_resolved_access_count(selected_user["user_id"])
            st.caption(f"Currently resolves to {resolved_count} customer ID(s).")

        if st.button("Save RLS selection"):
            try:
                count = db.save_selection(selected_user["user_id"], current_selection)
                st.success(f"Saved. This grants access to {count} customer ID(s).")
            except db.RlsAdminError as e:
                st.error(str(e))
