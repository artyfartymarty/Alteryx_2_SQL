-- 05_roles.sql — Enterprise governance roles (program spec docs/spec/00-README.md §11.1).
--
-- NOT executed against any Snowflake account. Nothing in this repo has run on real Snowflake;
-- review and adapt (database name, existing role hierarchy, resource monitors, warehouse grants)
-- before running this against a real account.
--
-- <DB> is this program's own sandbox/ops database (holding MIG_WORK, MIG_GOLDEN, OPS and
-- ANALYTICS as schemas) -- fill it in once, consistently, everywhere it appears below; the
-- program spec names no concrete database. MIGRATION_WH is this program's warehouse.

CREATE ROLE IF NOT EXISTS MIGRATION_AGENT;   -- used by the MCP server behind intake/validator sessions
CREATE ROLE IF NOT EXISTS MIGRATION_CI;      -- deploys procedures from the PR the pipeline opens
CREATE ROLE IF NOT EXISTS MIGRATION_RUN;     -- executes migrated procedures in production

-- ---------------------------------------------------------------------------------------------
-- MIGRATION_AGENT — sandbox only, least privilege: USAGE on the sandbox database and on schemas
-- MIG_WORK/MIG_GOLDEN, only the table/view/procedure privileges a contract-C4 procedure run
-- actually needs inside them (no TRUNCATE, no DROP -- the permission policy denies both), and
-- USAGE on the warehouse. Nothing on any production database or schema.
-- ---------------------------------------------------------------------------------------------
GRANT USAGE ON DATABASE <DB> TO ROLE MIGRATION_AGENT;
GRANT USAGE ON WAREHOUSE MIGRATION_WH TO ROLE MIGRATION_AGENT;
GRANT USAGE ON SCHEMA MIG_WORK TO ROLE MIGRATION_AGENT;
GRANT USAGE ON SCHEMA MIG_GOLDEN TO ROLE MIGRATION_AGENT;

-- validator deploys procedures and their work tables under MIG_WORK (program spec §5, validator agent)
GRANT CREATE TABLE ON SCHEMA MIG_WORK TO ROLE MIGRATION_AGENT;
GRANT CREATE VIEW ON SCHEMA MIG_WORK TO ROLE MIGRATION_AGENT;
GRANT CREATE PROCEDURE ON SCHEMA MIG_WORK TO ROLE MIGRATION_AGENT;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA MIG_WORK TO ROLE MIGRATION_AGENT;
GRANT SELECT, INSERT, UPDATE, DELETE ON FUTURE TABLES IN SCHEMA MIG_WORK TO ROLE MIGRATION_AGENT;
GRANT USAGE ON ALL PROCEDURES IN SCHEMA MIG_WORK TO ROLE MIGRATION_AGENT;
GRANT USAGE ON FUTURE PROCEDURES IN SCHEMA MIG_WORK TO ROLE MIGRATION_AGENT;

-- golden data is read-only for the agent; gen_source_views.py (run under this role) still needs
-- CREATE VIEW to materialize each workflow/set's logical-name view over it (contract C3). A
-- contract-C4 procedure run only ever *reads* MIG_GOLDEN (SRC_DB/SRC_SCHEMA point at it; test
-- targets are written to MIG_WORK per contract C3), so no INSERT/UPDATE/DELETE is granted here.
GRANT CREATE VIEW ON SCHEMA MIG_GOLDEN TO ROLE MIGRATION_AGENT;
GRANT SELECT ON ALL TABLES IN SCHEMA MIG_GOLDEN TO ROLE MIGRATION_AGENT;
GRANT SELECT ON FUTURE TABLES IN SCHEMA MIG_GOLDEN TO ROLE MIGRATION_AGENT;
GRANT SELECT ON ALL VIEWS IN SCHEMA MIG_GOLDEN TO ROLE MIGRATION_AGENT;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA MIG_GOLDEN TO ROLE MIGRATION_AGENT;

-- Intake's source-candidate lookups (program spec §5.3, intake agent step 3: "Query
-- INFORMATION_SCHEMA.COLUMNS ... for tables whose columns cover those fields") must NOT be served
-- by granting MIGRATION_AGENT USAGE on any production database: a database's INFORMATION_SCHEMA is
-- visible to any role with USAGE on it, so that would let the agent enumerate every table and
-- column in that database. An earlier draft of this file granted IMPORTED PRIVILEGES ON DATABASE
-- SNOWFLAKE instead, which is worse: it exposes all of SNOWFLAKE.ACCOUNT_USAGE account-wide
-- (every role's query text, login history, and object metadata) to a role reachable from agent/MCP
-- sessions. Neither is least privilege (program spec §11.1, principle 5). Recommended pattern:
--
-- A separate job -- run by a human, or by MIGRATION_CI, never by an agent session -- publishes a
-- sanitized column catalog (structure only, no row data) into a sandbox table MIGRATION_AGENT
-- already has SELECT on via the MIG_WORK grants above. This mirrors this repo's local stand-in,
-- catalog/columns.csv.
--
-- CREATE TABLE MIG_WORK.CATALOG_COLUMNS (
--   SOURCE_DATABASE STRING, SOURCE_SCHEMA STRING, SOURCE_TABLE STRING,
--   COLUMN_NAME STRING, DATA_TYPE STRING, ROW_COUNT NUMBER
-- );
--
-- -- run periodically (by MIGRATION_CI or a human), once per source database being catalogued;
-- -- <SOURCE_DB> is filled in per source -- this is a template, never executed as written:
-- INSERT INTO MIG_WORK.CATALOG_COLUMNS
-- SELECT c.TABLE_CATALOG, c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, t.ROW_COUNT
-- FROM <SOURCE_DB>.INFORMATION_SCHEMA.COLUMNS c
-- JOIN <SOURCE_DB>.INFORMATION_SCHEMA.TABLES t
--   ON t.TABLE_CATALOG = c.TABLE_CATALOG AND t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME;
--
-- The validator's "credits from QUERY_HISTORY by query tag" (program spec, validator agent step 5)
-- needs no extra grant either: TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION()) (or
-- QUERY_HISTORY()) surfaces the calling role's own queries, filterable by QUERY_TAG, for free.

-- ---------------------------------------------------------------------------------------------
-- MIGRATION_CI — deploys. Owns the procedure and table DDL delivered through the PR pipeline
-- (schemachange / Snowflake CLI), across every environment (program spec §10.1). Never used from
-- an agent session.
-- ---------------------------------------------------------------------------------------------
GRANT USAGE ON DATABASE <DB> TO ROLE MIGRATION_CI;
GRANT USAGE ON WAREHOUSE MIGRATION_WH TO ROLE MIGRATION_CI;
GRANT USAGE ON SCHEMA MIG_WORK TO ROLE MIGRATION_CI;
GRANT USAGE ON SCHEMA MIG_GOLDEN TO ROLE MIGRATION_CI;
GRANT CREATE PROCEDURE, CREATE TABLE, CREATE VIEW ON SCHEMA MIG_WORK TO ROLE MIGRATION_CI;

GRANT USAGE ON SCHEMA OPS TO ROLE MIGRATION_CI;
GRANT CREATE TABLE, CREATE TASK, CREATE ALERT ON SCHEMA OPS TO ROLE MIGRATION_CI;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA OPS TO ROLE MIGRATION_CI;
GRANT SELECT, INSERT ON FUTURE TABLES IN SCHEMA OPS TO ROLE MIGRATION_CI;

GRANT USAGE ON SCHEMA ANALYTICS TO ROLE MIGRATION_CI;
GRANT CREATE TABLE ON SCHEMA ANALYTICS TO ROLE MIGRATION_CI;   -- shadow tables (02_shadow_table_template.sql)

-- Deploying a migrated procedure into its own production target schema (CREATE OR REPLACE
-- PROCEDURE <that schema>.<WF>_<SEG>) happens per workflow, at onboarding, once that workflow's
-- production database/schema is known; those grants travel with that workflow's PR and are not
-- enumerated here, since this file only covers what every workflow needs unconditionally.

-- ---------------------------------------------------------------------------------------------
-- MIGRATION_RUN — executes migrated procedures in production. Every procedure in this project is
-- EXECUTE AS CALLER, never EXECUTE AS OWNER (contract C4; mappings/global.yaml program.execute_as:
-- CALLER; scripts/compile_check.py rejects anything else, because an owner's-rights procedure
-- cannot ALTER SESSION to set TIMEZONE/WEEK_START). Running as the caller means MIGRATION_RUN
-- itself needs the privileges on every source and target object a procedure touches -- it does not
-- inherit them from a procedure owner the way an EXECUTE AS OWNER procedure's caller would. This
-- role therefore holds the run/reconciliation bookkeeping privileges below plus, per workflow at
-- deployment (see the MIGRATION_CI note above), SELECT/INSERT/UPDATE on that workflow's specific
-- production source and target tables and EXECUTE on its procedure -- verify the exact privilege
-- set against contract.json for each workflow on the target account before granting it for real.
-- ---------------------------------------------------------------------------------------------
GRANT USAGE ON DATABASE <DB> TO ROLE MIGRATION_RUN;
GRANT USAGE ON WAREHOUSE MIGRATION_WH TO ROLE MIGRATION_RUN;
GRANT USAGE ON SCHEMA OPS TO ROLE MIGRATION_RUN;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA OPS TO ROLE MIGRATION_RUN;
GRANT SELECT, INSERT ON FUTURE TABLES IN SCHEMA OPS TO ROLE MIGRATION_RUN;

GRANT USAGE ON SCHEMA ANALYTICS TO ROLE MIGRATION_RUN;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA ANALYTICS TO ROLE MIGRATION_RUN;    -- shadow writes, recon reads
GRANT SELECT, INSERT ON FUTURE TABLES IN SCHEMA ANALYTICS TO ROLE MIGRATION_RUN;

-- EXECUTE on each migrated procedure, and USAGE plus the SWAP privilege on its production target
-- schema/tables for cutover (program spec §10.5: ALTER TABLE ... SWAP WITH), are granted per
-- workflow when MIGRATION_CI deploys that procedure — that schema is workflow-specific and not
-- named here, for the same reason as MIGRATION_CI above.
