# wf_0005 — intake plan

Vendor account dedupe · owner `wf_owner` · schedule `0 6 * * 1-5` ·
source `vendor_dedupe.yxmd`, `yxmdVer 2023.1`, E1 engine.

This plan restates `parsed/dag.json`, `intake/touchpoints.json` and `mappings/global.yaml`.
Nothing here has run on Alteryx or on Snowflake, and no count, diff or tolerance in this project
comes from an agent — those come only from `scripts/`.

## Tier

**T3 — manual.** This workflow does not migrate, and the plan says so up front rather than after
four agents have worked on it.

Two tools put it there, and either one would be enough:

- **Tool 3, Run Command.** It shells out to `C:\scripts\notify.bat`. A Snowflake stored procedure
  cannot start a process on a host, so there is no pattern for this and there never will be. The
  program spec puts `run_command` in T3 by name.
- **Tool 2, `AcmeAnalytics.Dedupe.DedupeTool`.** A third-party plugin the parser does not know.
  `scripts/parsers/ext/acme_dedupe.py` (written during parse recovery — see
  `parsed/parse_diagnosis.md`) records what its configuration appears to do and stops there,
  because three things the XML does not say would each change the result: what happens to a row
  whose date is NULL, what happens when two rows tie on the date, and whether the comparison is by
  date or by full timestamp.

The expected terminal state is therefore `MANUAL`. What that means concretely: no contract, no
`proc.sql`, no `broken_sql`, and no golden outputs — `scripts/dev/alteryx_sim.py` refuses both
tools (`UnsupportedTool`), so `manifest.golden_sets` is empty and there is nothing for
`scripts/validate_segment.py` to compare. Only the `normal` and `empty` golden **inputs** exist;
`period_end` and `edge` would have nothing to prove.

## Touchpoints and how they resolve

| Tool | Touchpoint | Resolution |
|------|-----------|------------|
| 1 | `C:\data\vendor\accounts.yxdb` (key `vendor/accounts.yxdb`) | `VENDOR.RAW.ACCOUNTS`, logical `ACCOUNTS` — confirmed by the owner |
| 4 | `C:\data\out\accounts_clean.yxdb` (key `out/accounts_clean.yxdb`) | `ANALYTICS.CURATED.ACCOUNTS_CLEAN`, logical `ACCOUNTS_CLEAN`, write mode `overwrite` — confirmed |

Both touchpoints resolve cleanly and are recorded anyway: intake reaches `READY` and the workflow
still parks at `MANUAL`. That is deliberate — the mappings are the part of this migration that
*is* usable, and a human picking the workflow up later should not have to re-establish them.

Tool 3's `C:\scripts\notify.bat` is not an intake touchpoint: it is a command, not a data source
or a target, so nothing is mapped for it. It is recorded in `unsupported.json` instead.

There are no constants, no app parameters and no macros.

Program-level answers all come from `mappings/global.yaml` and none had to be asked again: target
`ANALYTICS.CURATED`, work schema `MIG_WORK`, warehouse `MIG_WH`, owning role `MIGRATION_ROLE`,
`EXECUTE AS CALLER`, column-name policy `sanitize`, session `TIMEZONE America/New_York` and
`WEEK_START 1`, accepted diff classes `ROUNDING` and `ORDERING`.

## Expected segment cuts

None proposed. Segmentation does not run for a workflow that parks at `MANUAL`: there is nothing
to translate, so there is nothing to cut. If the two blocking tools are ever resolved, four tools
against a `min_tools` of 3 would be one segment.

## Unsupported or manual tools

Both are listed in `unsupported.json` with `blocks_migration: true`:

| Tool | Type | Why |
|------|------|-----|
| 3 | `run_command` | No Snowflake equivalent; the notification has to be rebuilt outside the warehouse |
| 2 | `unknown` | A third-party plugin whose semantics the XML does not settle |

## Questions for the owner

Neither of these is a mapping question, so neither is in `open_questions.md`'s usual shape — they
are what a human has to decide before this workflow can move at all:

1. **What does the Acme Dedupe tool do with a NULL `UPDATED`, with a tie on `UPDATED`, and does it
   compare the date or a full timestamp?** The `normal` golden input has an account (`D-400`) with
   a NULL `UPDATED` and two duplicate pairs, so the three answers are directly visible in the data
   once somebody knows them.
2. **What should replace `notify.bat`?** A Snowflake task, an external function, a step in the
   orchestrator that runs the migrated procedures, or nothing at all if the notification is no
   longer wanted.

## Fix-loop budget

Zero. There is no procedure to fix. The fix loop exists to iterate a translation against
validation, and this workflow produces neither.

## Dependencies, owner and consumers

No shared macros and no upstream workflow. `manifest.json` records no consumers for the output.
Owner `wf_owner`; the schedule `0 6 * * 1-5` is the Alteryx Server schedule recorded at parse time —
worth noting, because a workflow that parks at `MANUAL` is still running nightly in Alteryx and
will keep running until somebody retires it.
