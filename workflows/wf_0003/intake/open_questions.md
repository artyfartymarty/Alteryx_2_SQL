# Open questions - wf_0003 (owner: @wf_owner)

## Blocking (translation waits)
- [x] Q1 - Tool 1 Input Data reads dbo.GL_LEDGER via alias:prod_fin (6 fields).
      Proposed: FINANCE.RAW.GL_LEDGER (6/6 columns match).
      Confirm or supply another: FINANCE.RAW.GL_LEDGER
- [x] Q2 - Tool 10 Output Data writes dbo.GL_SUMMARY via alias:prod_fin/dbo.gl_summary (6 fields), mode update_insert.
      Proposed: ANALYTICS.CURATED.GL_SUMMARY (6/6 columns match).
      Confirm or supply another: ANALYTICS.CURATED.GL_SUMMARY

## Non-blocking (proceeds with the assumption)
- [ ] Q3 - Workflow constant User.Region is currently `EMEA`; used as-is unless overridden.
      Override: 
- [ ] Q4 - Workflow constant User.PeriodEnd is currently `2026-08-31`; used as-is unless overridden.
      Override: 
