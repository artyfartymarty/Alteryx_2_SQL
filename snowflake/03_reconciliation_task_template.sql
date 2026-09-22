-- 03_reconciliation_task_template.sql — Reconciliation task, one per migrated workflow target
-- (program spec docs/spec/00-README.md §10.4).
--
-- NOT executed against any Snowflake account. Nothing in this repo has run on real Snowflake;
-- review and adapt (schedule, warehouse, the AFTER-dependency alternative below) before running
-- this against a real account.
--
-- Placeholders — fill in once per (workflow, target):
--   <WF_ID>    the workflow id, e.g. wf_0007
--   <TARGET>   the target table name in ANALYTICS.CURATED, e.g. CUSTOMER_SUMMARY
--
-- Runs after both sides complete (program spec §10.3: the Alteryx-completion event, a fixed delay
-- after the shared cron, or an AFTER <upstream_task> dependency — pick the strategy recorded in
-- that workflow's manifest.production.trigger and adjust SCHEDULE below accordingly).
CREATE OR REPLACE TASK OPS.RECON_TASK_<WF_ID>
  WAREHOUSE = MIGRATION_WH
  SCHEDULE = 'USING CRON 0 * * * * UTC'
AS
-- counts + full-row hash on both sides; EXCEPT-based per-key diffs and column-level mismatch
-- counts are a fuller reconciliation than this template attempts — ONLY_IN_ALTERYX,
-- ONLY_IN_SNOWFLAKE, COLUMN_MISMATCHES and EXAMPLES are left NULL here because a count+hash check
-- cannot decompose *which* rows or columns differ, only *whether* they do; a stricter
-- reconciliation would replace the hash comparison with the EXCEPT-both-ways query the program
-- spec describes and populate those columns from it.
INSERT INTO OPS.RECON_RESULTS
SELECT :run_id, '<WF_ID>', '<TARGET>', CURRENT_TIMESTAMP(),
       IFF(a.n = s.n AND a.h = s.h, 'PASS', 'FAIL'), s.n - a.n, NULL, NULL, NULL, NULL
FROM (SELECT COUNT(*) n, HASH_AGG(*) h FROM ANALYTICS.CURATED.<TARGET>) a,
     (SELECT COUNT(*) n, HASH_AGG(*) h FROM ANALYTICS.CURATED.<TARGET>__SHADOW) s;
