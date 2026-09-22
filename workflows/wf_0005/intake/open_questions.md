# Open questions - wf_0005 (owner: @wf_owner)

## Blocking (translation waits)
- [x] Q1 - Tool 1 Input Data reads C:\data\vendor\accounts.yxdb (3 fields, 6 rows).
      Proposed: VENDOR.RAW.ACCOUNTS (3/3 columns match).
      Confirm or supply another: VENDOR.RAW.ACCOUNTS
- [x] Q4 - Tool 4 Output Data writes C:\data\out\accounts_clean.yxdb (3 fields), mode overwrite.
      Proposed: ANALYTICS.CURATED.ACCOUNTS_CLEAN (naming convention; not verified to exist).
      Confirm or supply another: ANALYTICS.CURATED.ACCOUNTS_CLEAN

## Non-blocking (proceeds with the assumption)
- [x] Q2 - Tool 2 manual tool: Acme Dedupe (third party) -- requires manual migration; does not block intake.
- [x] Q3 - Tool 3 manual tool: Tell the vendor team -- requires manual migration; does not block intake.
