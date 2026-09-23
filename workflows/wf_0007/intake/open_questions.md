# Open questions - wf_0007 (owner: @wf_owner)

## Blocking (translation waits)
- [x] Q1 - Tool 1 Input Data reads C:\data\plan\targets.yxdb (3 fields, 6 rows).
      Proposed: FINANCE.RAW.GL_LEDGER (2/3 columns match; missing TARGET).
      Confirm or supply another: PLANNING.RAW.TARGETS
- [x] Q2 - Tool 2 Input Data reads C:\data\sales\actuals.yxdb (3 fields, 6 rows).
      Proposed: FINANCE.RAW.GL_LEDGER (2/3 columns match; missing ACTUAL).
      Confirm or supply another: SALES.RAW.ACTUALS
- [x] Q3 - Tool 6 Output Data writes C:\data\out\region_attainment.yxdb (5 fields), mode overwrite.
      Proposed: ANALYTICS.CURATED.REGION_ATTAINMENT (naming convention; not verified to exist).
      Confirm or supply another: ANALYTICS.CURATED.REGION_ATTAINMENT
- [x] Q4 - Tool 7 Output Data writes dbo.ATTAINMENT_HISTORY via alias:prod_plan/dbo.attainment_history (5 fields), mode update_insert.
      Proposed: ANALYTICS.CURATED.ATTAINMENT_HISTORY (naming convention; not verified to exist).
      Confirm or supply another: ANALYTICS.CURATED.ATTAINMENT_HISTORY

## Non-blocking (proceeds with the assumption)
- [ ] Q5 - Workflow constant User.CurrentYear is currently `2026`; used as-is unless overridden.
      Override: 
