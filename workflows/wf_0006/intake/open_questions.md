# Open questions - wf_0006 (owner: @wf_owner)

## Blocking (translation waits)
- [x] Q1 - Tool 1 Input Data reads C:\data\billing\subscriptions.yxdb (5 fields, 9 rows).
      Proposed: ANALYTICS.RAW.SUBSCRIPTIONS (naming convention; not verified to exist).
      Confirm or supply another: BILLING.RAW.SUBSCRIPTIONS
- [x] Q2 - Tool 5 Output Data writes C:\data\out\revenue_by_period.yxdb (4 fields), mode overwrite.
      Proposed: ANALYTICS.CURATED.REVENUE_BY_PERIOD (naming convention; not verified to exist).
      Confirm or supply another: ANALYTICS.CURATED.REVENUE_BY_PERIOD

## Non-blocking (proceeds with the assumption)
(none)
