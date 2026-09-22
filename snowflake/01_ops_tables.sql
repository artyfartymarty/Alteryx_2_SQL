-- 01_ops_tables.sql — Operational bookkeeping tables (program spec docs/spec/00-README.md §10.2).
--
-- NOT executed against any Snowflake account. Nothing in this repo has run on real Snowflake;
-- review and adapt (retention, clustering, warehouse sizing, access policies) before running this
-- against a real account.
--
-- One row per procedure execution, and one row per reconciliation between the Alteryx output and
-- its Snowflake shadow (see 02_shadow_table_template.sql and 03_reconciliation_task_template.sql).
-- Both tables live in OPS, alongside every migrated workflow's run history.

CREATE SCHEMA IF NOT EXISTS OPS;

-- one row per procedure execution
CREATE TABLE OPS.RUN_LOG (
  RUN_ID STRING, WF_ID STRING, PROC_VERSION STRING, MODE STRING,        -- SHADOW | PROD
  STARTED_AT TIMESTAMP_LTZ, ENDED_AT TIMESTAMP_LTZ, STATUS STRING, ERROR STRING,
  INPUT_SNAPSHOT_TS TIMESTAMP_LTZ,                                       -- time-travel point used for inputs
  INPUT_COUNTS VARIANT, OUTPUT_COUNTS VARIANT, KEY_AGGREGATES VARIANT,   -- per table/column
  CREDITS FLOAT, WAREHOUSE STRING
);

-- one row per reconciliation
CREATE TABLE OPS.RECON_RESULTS (
  RUN_ID STRING, WF_ID STRING, TARGET_TABLE STRING, CHECKED_AT TIMESTAMP_LTZ,
  VERDICT STRING, ROW_DELTA NUMBER, ONLY_IN_ALTERYX NUMBER, ONLY_IN_SNOWFLAKE NUMBER,
  COLUMN_MISMATCHES VARIANT, EXAMPLES VARIANT
);
