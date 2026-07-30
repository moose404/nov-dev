"""
Databricks SQL Warehouse client for the RLS Manager app.

Uses databricks-sdk's WorkspaceClient, which auto-detects the app's own
service-principal credentials when running inside a Databricks App — no
manual credential wiring needed, unlike the Azure-side auth in the other app.

Required environment variable:
    DATABRICKS_WAREHOUSE_ID - the SQL warehouse to run queries against
    (the app's service principal must have "Can Use" on this warehouse, and
    the relevant Unity Catalog grants — see schema.sql)
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

CATALOG = "rtl_vs"
SCHEMA = "rls_admin"
ALL_SENTINEL = "ALL"

# Order matters: this is the hierarchy from broadest to narrowest, used both for
# display and for cascading the dropdown options in the UI.
HIERARCHY_COLUMNS = [
    "reporting_group_name",
    "customer_id",
    "customer_name",
    "level1_name",
    "level2_code",
    "level2_name",
    "level3_code",
    "level3_name",
    "structure_name",
    "level_name",
]

COLUMN_LABELS = {
    "reporting_group_name": "Reporting Group Name",
    "customer_id": "Customer ID",
    "customer_name": "Customer Name",
    "level1_name": "Level 1 Name",
    "level2_code": "Level 2 Code",
    "level2_name": "Level 2 Name",
    "level3_code": "Level 3 Code",
    "level3_name": "Level 3 Name",
    "structure_name": "Structure Name",
    "level_name": "Level Name",
}

# Maps our snake_case column names to the actual column names in the hierarchy query.
BASE_COLUMN_MAP = {
    "reporting_group_name": "ReportingGroupName",
    "customer_id": "CustomerID",
    "customer_name": "CustomerName",
    "level1_name": "Level1Name",
    "level2_code": "Level2Code",
    "level2_name": "Level2Name",
    "level3_code": "Level3Code",
    "level3_name": "Level3Name",
    "structure_name": "StructureName",
    "level_name": "LevelName",
}

BASE_HIERARCHY_QUERY = """
SELECT * FROM (
  SELECT
     UDC.ReportingGroupName
    ,CS.CustomerID
    ,C.AccountName AS CustomerName
    ,UDC.Level1Name
    ,UDC.Level2Code
    ,UDC.Level2Name
    ,UDC.Level3Code
    ,UDC.Level3Name
    ,CS.Name AS StructureName
    ,CSL.LevelName
    ,CSL.LevelNumber
  FROM rtl_vs.base_key2_stream.CustomerStructure CS
  LEFT JOIN rtl_vs.base_key2_stream.CustomerStructureLevels CSL ON CS.LevelID = CSL.RecordID
  LEFT JOIN rtl_vs.base_key2_stream.Customer C ON C.RecordID = CS.CustomerID
  LEFT JOIN rtl_vs.base_key2_stream.UD_Customer UDC ON UDC.ParentID = C.RecordID
  WHERE CSL.LevelNumber = 2

  UNION ALL

  SELECT
     UDC.ReportingGroupName
    ,CS.CustomerID
    ,C.AccountName AS CustomerName
    ,UDC.Level1Name
    ,UDC.Level2Code
    ,UDC.Level2Name
    ,UDC.Level3Code
    ,UDC.Level3Name
    ,CS.Name AS StructureName
    ,CSL.LevelName
    ,CSL.LevelNumber
  FROM rtl_vs.base_key2_stream.CustomerStructure CS
  LEFT JOIN rtl_vs.base_key2_stream.CustomerStructureLevels CSL ON CS.LevelID = CSL.RecordID
  LEFT JOIN rtl_vs.base_key2_stream.Customer C ON C.RecordID = CS.CustomerID
  LEFT JOIN rtl_vs.base_key2_stream.UD_Customer UDC ON UDC.ParentID = C.RecordID
  WHERE CSL.LevelNumber = 1
  AND NOT EXISTS (
    SELECT 1
    FROM rtl_vs.base_key2_stream.CustomerStructure x
    LEFT JOIN rtl_vs.base_key2_stream.CustomerStructureLevels CSL ON CSL.RecordID = x.LevelID
    WHERE x.Ref1 = CS.Ref1
    AND CSL.LevelNumber = 2
  )
)
"""


class RlsAdminError(RuntimeError):
    pass


def _escape(value: str) -> str:
    return value.replace("'", "''")


def _warehouse_id() -> str:
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not warehouse_id:
        raise RlsAdminError("Missing required environment variable: DATABRICKS_WAREHOUSE_ID")
    return warehouse_id


def run_query(sql: str) -> list[dict]:
    w = WorkspaceClient()
    resp = w.statement_execution.execute_statement(
        warehouse_id=_warehouse_id(),
        statement=sql,
        wait_timeout="30s",
    )
    while resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(1)
        resp = w.statement_execution.get_statement(resp.statement_id)

    if resp.status.state != StatementState.SUCCEEDED:
        error_message = resp.status.error.message if resp.status.error else "unknown error"
        raise RlsAdminError(f"Query failed: {error_message}\n{sql}")

    if resp.manifest is None or resp.result is None or resp.result.data_array is None:
        return []

    columns = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(columns, row)) for row in resp.result.data_array]


def run_statement(sql: str) -> None:
    run_query(sql)


# --- Users -------------------------------------------------------------


def list_users() -> list[dict]:
    return run_query(
        f"SELECT user_id, name, email, tenant FROM {CATALOG}.{SCHEMA}.rls_users ORDER BY name"
    )


def create_user(name: str, email: str, tenant: str) -> str:
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    run_statement(
        f"""
        INSERT INTO {CATALOG}.{SCHEMA}.rls_users (user_id, name, email, tenant, created_at, updated_at)
        VALUES ('{user_id}', '{_escape(name)}', '{_escape(email)}', '{_escape(tenant)}', '{now}', '{now}')
        """
    )
    return user_id


def update_user(user_id: str, name: str, email: str, tenant: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    run_statement(
        f"""
        UPDATE {CATALOG}.{SCHEMA}.rls_users
        SET name = '{_escape(name)}', email = '{_escape(email)}', tenant = '{_escape(tenant)}', updated_at = '{now}'
        WHERE user_id = '{user_id}'
        """
    )


def delete_user(user_id: str) -> None:
    run_statement(f"DELETE FROM {CATALOG}.{SCHEMA}.rls_selections WHERE user_id = '{user_id}'")
    run_statement(f"DELETE FROM {CATALOG}.{SCHEMA}.rls_resolved_access WHERE user_id = '{user_id}'")
    run_statement(f"DELETE FROM {CATALOG}.{SCHEMA}.rls_users WHERE user_id = '{user_id}'")


# --- Hierarchy lookups (cascading dropdown options) ---------------------


def get_distinct_values(column: str, filters: dict[str, str]) -> list[str]:
    """Distinct values for `column`, filtered by whichever upstream (non-ALL) columns are already selected."""
    base_col = BASE_COLUMN_MAP[column]
    where_clauses = [
        f"{BASE_COLUMN_MAP[col]} = '{_escape(val)}'"
        for col, val in filters.items()
        if val and val != ALL_SENTINEL
    ]
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    sql = f"""
        WITH hierarchy AS ({BASE_HIERARCHY_QUERY})
        SELECT DISTINCT {base_col} AS value
        FROM hierarchy
        {where_sql}
        ORDER BY value
    """
    rows = run_query(sql)
    return [r["value"] for r in rows if r["value"] is not None]


# --- Selections + resolved access ---------------------------------------


def get_selection(user_id: str) -> dict | None:
    rows = run_query(
        f"SELECT * FROM {CATALOG}.{SCHEMA}.rls_selections WHERE user_id = '{user_id}'"
    )
    return rows[0] if rows else None


def get_resolved_access_count(user_id: str) -> int:
    rows = run_query(
        f"SELECT COUNT(*) AS n FROM {CATALOG}.{SCHEMA}.rls_resolved_access WHERE user_id = '{user_id}'"
    )
    return int(rows[0]["n"]) if rows else 0


def save_selection(user_id: str, selection: dict[str, str]) -> int:
    """Persists the raw selection and materializes it into rls_resolved_access.
    Returns the number of resolved rows (allowed CustomerIDs) for this user."""
    now = datetime.now(timezone.utc).isoformat()

    columns = ["user_id"] + HIERARCHY_COLUMNS + ["updated_at"]
    values = [user_id] + [selection.get(col, ALL_SENTINEL) for col in HIERARCHY_COLUMNS] + [now]
    escaped_values = ", ".join(f"'{_escape(v)}'" for v in values)

    run_statement(f"DELETE FROM {CATALOG}.{SCHEMA}.rls_selections WHERE user_id = '{user_id}'")
    run_statement(
        f"""
        INSERT INTO {CATALOG}.{SCHEMA}.rls_selections ({', '.join(columns)})
        VALUES ({escaped_values})
        """
    )

    return _resolve_and_store_access(user_id, selection)


def _resolve_and_store_access(user_id: str, selection: dict[str, str]) -> int:
    user_rows = run_query(
        f"SELECT email FROM {CATALOG}.{SCHEMA}.rls_users WHERE user_id = '{user_id}'"
    )
    if not user_rows:
        raise RlsAdminError(f"User {user_id} not found")
    email = user_rows[0]["email"]

    where_clauses = [
        f"{BASE_COLUMN_MAP[col]} = '{_escape(selection.get(col, ALL_SENTINEL))}'"
        for col in HIERARCHY_COLUMNS
        if selection.get(col, ALL_SENTINEL) != ALL_SENTINEL
    ]
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    now = datetime.now(timezone.utc).isoformat()

    run_statement(f"DELETE FROM {CATALOG}.{SCHEMA}.rls_resolved_access WHERE user_id = '{user_id}'")
    run_statement(
        f"""
        WITH hierarchy AS ({BASE_HIERARCHY_QUERY})
        INSERT INTO {CATALOG}.{SCHEMA}.rls_resolved_access
            (user_id, email, customer_id, reporting_group_name, customer_name, structure_name, level_name, resolved_at)
        SELECT DISTINCT
            '{user_id}' AS user_id,
            '{_escape(email)}' AS email,
            CustomerID,
            ReportingGroupName,
            CustomerName,
            StructureName,
            LevelName,
            TIMESTAMP('{now}')
        FROM hierarchy
        {where_sql}
        """
    )

    return get_resolved_access_count(user_id)
