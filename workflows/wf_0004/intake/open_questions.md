# Open questions - wf_0004 (owner: @wf_owner)

## Blocking (translation waits)
- [x] Q1 - Tool 1 Input Data reads C:\data\wh\stock.yxdb (4 fields, 14 rows).
      Proposed: WH.RAW.STOCK (4/4 columns match).
      Confirm or supply another: WH.RAW.STOCK
- [x] Q3 - Tool 6 Output Data writes C:\data\out\inventory_by_wh.yxdb (5 fields), mode overwrite.
      Proposed: ANALYTICS.CURATED.INVENTORY_BY_WH (naming convention; not verified to exist).
      Confirm or supply another: ANALYTICS.CURATED.INVENTORY_BY_WH
- [x] Q4 - Tool 7 Output Data writes C:\data\out\inventory_long.yxdb (3 fields), mode overwrite.
      Proposed: ANALYTICS.CURATED.INVENTORY_LONG (naming convention; not verified to exist).
      Confirm or supply another: ANALYTICS.CURATED.INVENTORY_LONG

## Non-blocking (proceeds with the assumption)
- [x] Q2 - Tool 2 macro: Clean SKU codes, drop empty stock -- translated inline as a shared-procedure candidate.
