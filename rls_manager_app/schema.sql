-- Run this once (e.g. in a Databricks SQL editor / notebook) to provision the tables
-- the RLS Manager app reads and writes. Adjust the catalog/schema names if you don't
-- want to use rtl_vs.rls_admin.

CREATE SCHEMA IF NOT EXISTS rtl_vs.rls_admin;

CREATE TABLE IF NOT EXISTS rtl_vs.rls_admin.rls_users (
    user_id     STRING NOT NULL,
    name        STRING NOT NULL,
    email       STRING NOT NULL,
    tenant      STRING NOT NULL,
    created_at  TIMESTAMP,
    updated_at  TIMESTAMP
) USING DELTA;

-- One row per user: their selection for each hierarchy column. A value of the
-- literal string 'ALL' means "no restriction on this column" (except
-- reporting_group_name, which must always be a specific value).
CREATE TABLE IF NOT EXISTS rtl_vs.rls_admin.rls_selections (
    user_id               STRING NOT NULL,
    reporting_group_name  STRING NOT NULL,
    customer_id           STRING,
    customer_name         STRING,
    level1_name           STRING,
    level2_code           STRING,
    level2_name           STRING,
    level3_code           STRING,
    level3_name           STRING,
    structure_name        STRING,
    level_name            STRING,
    updated_at            TIMESTAMP
) USING DELTA;

-- Materialized result of resolving each user's selection (with 'ALL' wildcards
-- expanded) against the hierarchy view. This is the table Power BI's RLS role
-- filters against directly — one row per (user, allowed CustomerID).
CREATE TABLE IF NOT EXISTS rtl_vs.rls_admin.rls_resolved_access (
    user_id               STRING NOT NULL,
    email                 STRING NOT NULL,
    customer_id           STRING NOT NULL,
    reporting_group_name  STRING,
    customer_name         STRING,
    structure_name        STRING,
    level_name            STRING,
    resolved_at           TIMESTAMP
) USING DELTA;

-- Grant the app's service principal what it needs. Replace <APP_SERVICE_PRINCIPAL>
-- with the application ID shown on the app's details page.
-- GRANT USE CATALOG ON CATALOG rtl_vs TO `<APP_SERVICE_PRINCIPAL>`;
-- GRANT USE SCHEMA ON SCHEMA rtl_vs.rls_admin TO `<APP_SERVICE_PRINCIPAL>`;
-- GRANT USE SCHEMA ON SCHEMA rtl_vs.base_key2_stream TO `<APP_SERVICE_PRINCIPAL>`;
-- GRANT SELECT ON SCHEMA rtl_vs.base_key2_stream TO `<APP_SERVICE_PRINCIPAL>`;
-- GRANT SELECT, MODIFY ON SCHEMA rtl_vs.rls_admin TO `<APP_SERVICE_PRINCIPAL>`;
-- Also grant the service principal "Can Use" on the SQL warehouse itself
-- (SQL Warehouses -> select warehouse -> Permissions), which isn't a SQL grant.
