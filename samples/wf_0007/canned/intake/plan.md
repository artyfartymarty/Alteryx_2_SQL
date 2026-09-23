# wf_0007 — intake plan

Regional targets and attainment · owner `wf_owner` · schedule `0 6 * * 1-5` ·
source `regional_targets.yxmd`, `yxmdVer 2023.1`, E1 engine.

This plan restates `parsed/dag.json`, `intake/touchpoints.json` and `mappings/global.yaml`.
Nothing here has run on Alteryx or on Snowflake, and no count, diff or tolerance in this project
comes from an agent — those come only from `scripts/`.

## Tier

**T1 — every tool has a SQL pattern.** Input Data (twice), Join, Filter, Summarize and Output Data
(twice) are all in the cookbook, so nothing needs Snowpark and nothing needs a human rewrite.

## Output kind asked for

`sample.json` asks for this workflow to be migrated as **one dbt project** (`"output_target":
"dbt"`, copied into `manifest.json`), rather than as a procedure per segment. Whether it can be is
`scripts/target_check.py`'s call, made after segmentation; nothing in this workflow is on the dbt
blocker list (no Python tool, no manual or unknown tool, no PreSQL or PostSQL, and both write modes
have a dbt materialisation).

## Touchpoints and how they resolve

| Tool | Touchpoint | Resolution |
|------|-----------|------------|
| 1 | `C:\data\plan\targets.yxdb` (key `plan/targets.yxdb`) | `PLANNING.RAW.TARGETS`, logical `TARGETS` — confirmed by the owner |
| 2 | `C:\data\sales\actuals.yxdb` (key `sales/actuals.yxdb`) | `SALES.RAW.ACTUALS`, logical `ACTUALS` — confirmed |
| 6 | `C:\data\out\region_attainment.yxdb` (key `out/region_attainment.yxdb`) | `ANALYTICS.CURATED.REGION_ATTAINMENT`, logical `REGION_ATTAINMENT`, write mode `overwrite` — confirmed |
| 7 | ODBC `PROD_PLAN`, table `dbo.ATTAINMENT_HISTORY` (key `alias:prod_plan/dbo.attainment_history`) | `ANALYTICS.CURATED.ATTAINMENT_HISTORY`, logical `ATTAINMENT_HISTORY`, write mode `merge` on `REGION, PERIOD` — confirmed, answered by tool id |

The ODBC string's user and password are fake placeholders and the parser scrubs them; nothing
about the connection reaches a mapping except the alias.

One workflow constant, `User.CurrentYear` = `2026` (`IsNumeric False`), a non-blocking touchpoint
recorded in `mappings.yaml` as it stands. The Filter compares against it, so the migration carries
its value, as text.

## Expected segment cuts

Two segments in two waves, following the two Tool Containers: `seg_01` holding tools **1, 2, 3**
("Pair plan targets with actuals") and `seg_02` holding tools **4, 5, 6, 7** ("Current-year
attainment"). The Join stays with both of its inputs. `sample.json` sets `min_tools: 2`, so neither
container is merged into the other and the year filter lands in `seg_02`, beside the outputs.
`seg_01` hands `seg_02` the Join's `J` stream as a work table.

## Unsupported or manual tools

None. `unsupported.json` is `{"tier": "T1", "unsupported": [], "unknown": []}`.

## Parity risks by tool id

| Tool | Risk | Why it matters |
|------|------|----------------|
| 3 | A NULL join key matches nothing | A target with no region leaves on the unwired L anchor and is lost, in Alteryx as in SQL |
| 3 | The join fans out | A target with two actual lines on its key is paired with both, so its value reaches the Summarize twice |
| 4 | `User.CurrentYear` is text | The filter compares the first four characters of `PERIOD` with the string `2026`, so a malformed period that starts with `2026` is kept |
| 5 | Alteryx's `Count` counts rows | `LINES` is `COUNT(*)` |
| 5 | `Sum` of all-NULL values is NULL | A key whose actual lines are all NULL has a NULL `ACTUAL_TOTAL`, not 0 |
| 7 | `Update; Insert if new` on `REGION, PERIOD` | The history table keeps every row this run does not produce; a merge on a narrower key would overwrite or lose rows |

## Fix-loop budget

Three iterations. The risk is in `seg_02`: the year filter is easy to lose because it reads like a
report parameter, and the merge key has two columns, of which a hurried translation keeps one.
`broken_sql/broken.json` records both mistakes, made on purpose, so that the parity run is shown
catching them.

## Dependencies, owner and consumers

No macros, no shared sub-workflows, no upstream workflow producing either yxdb. `manifest.json`
records no consumers. Owner `wf_owner`; the schedule `0 6 * * 1-5` is the Alteryx Server schedule
recorded at parse time.
