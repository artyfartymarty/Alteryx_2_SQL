-- 04_alerts.sql — Reconciliation-failure alert (program spec docs/spec/00-README.md §10.4).
--
-- NOT executed against any Snowflake account. Nothing in this repo has run on real Snowflake;
-- review and adapt (the notification action, the condition's incremental-check logic, the
-- schedule) before running this against a real account.
--
-- Fires on OPS.RECON_RESULTS.VERDICT = 'FAIL' so the fixer loop can be rerun with a new golden
-- set captured from that day (program spec §10.4). As written the condition re-scans the whole
-- table on every schedule tick, which will re-fire on the same old failures; a real deployment
-- should narrow it to rows since the alert's own last successful run (or drive it off a stream on
-- OPS.RECON_RESULTS instead) before use. The notification action below sends email; swap it for
-- the account's Slack/Teams notification integration if one exists.
CREATE OR REPLACE ALERT OPS.RECON_FAIL_ALERT
  WAREHOUSE = MIGRATION_WH
  SCHEDULE = '5 MINUTE'
  IF (EXISTS (
        SELECT 1
        FROM OPS.RECON_RESULTS
        WHERE VERDICT = 'FAIL'
      ))
  THEN
    CALL SYSTEM$SEND_EMAIL(
      'migration_notifications',
      'migration-alerts@example.com',
      'Alteryx -> Snowflake reconciliation FAILURE',
      'One or more reconciliation checks failed. See OPS.RECON_RESULTS for the verdict and row-count '
      || 'delta from the count+hash comparison (03_reconciliation_task_template.sql does not populate '
      || 'row- or column-level detail). For that, re-run scripts/compare.py against the live table and '
      || 'its __SHADOW pair.'
    );
