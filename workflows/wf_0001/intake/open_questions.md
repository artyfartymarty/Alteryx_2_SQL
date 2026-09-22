# Open questions - wf_0001 (owner: @wf_owner)

## Blocking (translation waits)
- [x] Q1 - Tool 1 Input Data reads C:\data\sales\orders.yxdb (7 fields, 20 rows).
      Proposed: SALES.RAW.ORDERS (7/7 columns match).
      Confirm or supply another: SALES.RAW.ORDERS
- [x] Q2 - Tool 7 Output Data writes C:\data\out\sales_summary.yxdb (6 fields), mode overwrite.
      Proposed: ANALYTICS.CURATED.SALES_SUMMARY (naming convention; not verified to exist).
      Confirm or supply another: ANALYTICS.CURATED.SALES_SUMMARY
- [x] Q3 - Tool 8 Output Data writes C:\data\out\excluded_orders.csv (7 fields), mode overwrite.
      Proposed: ANALYTICS.CURATED.EXCLUDED_ORDERS (naming convention; not verified to exist).
      Confirm or supply another: ANALYTICS.CURATED.EXCLUDED_ORDERS

## Non-blocking (proceeds with the assumption)
(none)
