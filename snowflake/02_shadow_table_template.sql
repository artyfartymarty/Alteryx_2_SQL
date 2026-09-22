-- 02_shadow_table_template.sql — Shadow output table, one per migrated workflow target
-- (program spec docs/spec/00-README.md §10.2 and §10.5 cutover).
--
-- NOT executed against any Snowflake account. Nothing in this repo has run on real Snowflake;
-- review and adapt before running this against a real account.
--
-- Placeholders — fill in once per (workflow, target) at shadow-run setup, then run:
--   <WF_ID>    the workflow id, e.g. wf_0007 (recorded here only in comments, for traceability)
--   <TARGET>   the target table name in ANALYTICS.CURATED, e.g. CUSTOMER_SUMMARY
--
-- The shadow table starts as an empty structural copy; the procedure (run in MODE = 'SHADOW')
-- populates it on the same cadence as the real Alteryx output, so 03_reconciliation_task_template.sql
-- can compare the two without ever touching the production table.

-- shadow output for <WF_ID>'s target <TARGET>, lives beside the real one
CREATE TABLE ANALYTICS.CURATED.<TARGET>__SHADOW LIKE ANALYTICS.CURATED.<TARGET>;
