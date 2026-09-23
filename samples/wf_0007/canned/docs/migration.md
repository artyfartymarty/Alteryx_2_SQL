# wf_0007 — migration record

Restates `parsed/dag.json`, `analysis.md`, `segments/targets.json`, `dbt/translation_notes.md`,
`dbt/README.md`, `procs/README.md`, `segments/*/validation.json`, `intake/mappings.yaml` and
`manifest.json`. It adds no claim about behaviour that those artifacts do not support, and it
quotes no number about data: every count, diff and verdict lives in
`segments/<seg>/validation*.json`, written by `scripts/validate_dbt.py`.

**Nothing in this migration has been run on a real Snowflake account or a real Alteryx engine.**
"Validated" here means the dbt project was run with `dbt-duckdb` against a local DuckDB sandbox
per golden set, with golden data produced by `scripts/dev/alteryx_sim.py`, the project's stand-in
for the Alteryx engine. The project's `snowflake` target has never been run.

## Overview

| | |
|---|---|
| Workflow | `wf_0007` — Regional targets and attainment |
| Source | `regional_targets.yxmd`, `yxmdVer 2023.1`, E1 engine |
| Owner | `wf_owner` |
| Schedule | `0 6 * * 1-5` (the Alteryx Server schedule recorded at parse time) |
| Consumers | none recorded in `manifest.json` |
| Tier | T1 — every tool has a SQL pattern |
| Output kind | `dbt` — asked for by `manifest.json`'s `output_target`, granted by `segments/targets.json` with no blocker |
| Segments | `seg_01` (tools 1–3) and `seg_02` (tools 4–7), both `sql`, two waves |
| Project | `dbt/` — models `wf0007_seg_01_out`, `region_attainment`, `attainment_history`; no procedure, no `master.sql` |

## Source mappings

| Alteryx touchpoint | Logical name | Snowflake | Write mode | Confirmed by |
|---|---|---|---|---|
| `C:\data\plan\targets.yxdb` (tool 1) | `TARGETS` | `PLANNING.RAW.TARGETS` | read | owner |
| `C:\data\sales\actuals.yxdb` (tool 2) | `ACTUALS` | `SALES.RAW.ACTUALS` | read | owner |
| `C:\data\out\region_attainment.yxdb` (tool 6) | `REGION_ATTAINMENT` | `ANALYTICS.CURATED.REGION_ATTAINMENT` | overwrite | owner |
| ODBC `PROD_PLAN`, `dbo.ATTAINMENT_HISTORY` (tool 7) | `ATTAINMENT_HISTORY` | `ANALYTICS.CURATED.ATTAINMENT_HISTORY` | merge on `REGION, PERIOD` | owner |

No model names any of these tables. Sources are read through `{{ source('src', '<LOGICAL>') }}`,
whose schema is the `src_schema` variable, and every model is written to the `tgt_schema`
variable's schema under its alias — the upper-case logical name for a target. The same project
runs against the golden view schema in validation and against the real schemas in production.

## Tool → CTE map

| Tool | Type | Segment | Model | CTE |
|------|------|---------|-------|-----|
| 1 | `input` | `seg_01` | `wf0007_seg_01_out` | `t1_input` |
| 2 | `input` | `seg_01` | `wf0007_seg_01_out` | `t2_input` |
| 3 | `join` | `seg_01` | `wf0007_seg_01_out` | `t3_join_j` (J anchor; L and R are wired to nothing) |
| 4 | `filter` | `seg_02` | `region_attainment`, `attainment_history` | `t4_filter_t` in each (True anchor) |
| 5 | `summarize` | `seg_02` | `region_attainment`, `attainment_history` | `t5_summarize` in each |
| 6 | `output` | `seg_02` | `region_attainment` | no CTE — the model's final `SELECT`, materialised as a `table` |
| 7 | `output` | `seg_02` | `attainment_history` | no CTE — the model's final `SELECT`, merged into the existing table |

Tools 4 and 5 appear in both target models. A model is one CTE per tool and there is one model
per contract output, so no intermediate model may hold tool 5's stream; the duplication is recorded
in `dbt/translation_notes.md`.

## Deployment

A dbt workflow deploys as its project, not as procedures: there is no `procs/master.sql` and no
`segments/<seg>/proc.sql`. `procs/README.md` holds the one command, run from the repository root:

```
dbt run --project-dir workflows/wf_0007/dbt --profiles-dir workflows/wf_0007/dbt --target snowflake --vars '{"src_schema": "<SRC>", "tgt_schema": "<TGT>"}'
```

- `<SRC>` is the schema that exposes `TARGETS` and `ACTUALS` under those logical names; `<TGT>` is
  where `WF0007_SEG_01_OUT`, `REGION_ATTAINMENT` and `ATTAINMENT_HISTORY` are written.
  `ATTAINMENT_HISTORY` must already exist there with its history: the model merges into it.
- Every connection value comes from the environment — `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`,
  `SNOWFLAKE_AUTHENTICATOR`, `SNOWFLAKE_PRIVATE_KEY_PATH`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`,
  `SNOWFLAKE_DATABASE` — through `dbt/profiles.yml`, which is `scripts/lib/dbt_project.py`'s
  `PROFILES_TEMPLATE` byte for byte and holds no credential.
- `dbt-snowflake` is **not installed** in this repository and is not in `requirements.txt`; install
  it before running the command. The `snowflake` output has never been run from here.
- Nothing is deployed from an agent session — an agent never runs `dbt` at all. The run above is a
  human's or CI's, under the deploying role `MIGRATION_CI` (README §8).

## Assumptions

Every assumption is listed one per line in `dbt/translation_notes.md`. The ones a reader of this
document should know about:

- Tool 3's join is an inner join on both keys. A NULL key matches nothing, and a target with
  several actual lines on its key is paired with each of them, so the Summarize adds that target
  once per line — Alteryx's result, which the golden data keeps.
- `User.CurrentYear` is inlined as the text literal `'2026'`, and `Left([PERIOD], 4)` is
  `LEFT(PERIOD, 4)`: a malformed `PERIOD` such as `'2026-1'` is kept, exactly as Alteryx keeps it.
- Alteryx's `Count` counts rows, so `LINES` is `COUNT(*)`; `Sum` skips NULLs and an all-NULL
  group sums to NULL, which `SUM` does too.
- The sums are cast to `DECIMAL(19,2)`, Snowflake's `NUMBER(19,2)` under the one spelling DuckDB
  also accepts: dbt-duckdb runs the model text as written.
- `attainment_history` merges on both `REGION` and `PERIOD` and has no `is_incremental()` filter:
  every row the run produces is merged, and every other row, prior-year history included, stays.
- dbt-duckdb's merge updates and inserts by column name; Snowflake's own `MERGE` for the same model
  has not been checked from here.

## Accepted differences

None. `mappings/global.yaml` accepts the classes `ROUNDING` and `ORDERING` in principle, but
`manifest.accepted_diffs` is empty for this workflow and no cluster has been approved, so a
difference of any class would be a `FAIL`.

## Unsupported / manual items

None. `unsupported.json` is `{"tier": "T1", "unsupported": [], "unknown": []}`, and
`segments/targets.json` lists no dbt blocker.

## Validation summary

`scripts/validate_dbt.py wf_0007` validates the whole project at once: for each golden set it
builds a fresh DuckDB sandbox, `workflows/wf_0007/dbt_sandbox_<set>.duckdb`, runs the project once
through `scripts/lib/dbt_project.run_dbt`, and compares every `contract.outputs[]` table of both
segments against its golden file with the unchanged `scripts/compare.py`. `seg_02`'s two targets
share the stream `5_Output`, so its reports carry two checks, `5_Output:target:6` and
`5_Output:target:7`. It writes `segments/<seg>/validation.<set>.json` per set and
`segments/<seg>/validation.json` per segment, each marked `"target": "dbt"`. Read the verdicts,
counts and any diff clusters there — this document does not restate them, because a number about
data may come only from a script.

The golden sets exercised are the four `manifest.golden_sets` names: `normal`, `period_end`,
`empty` and `edge`. `period_end` straddles the year boundary (`2025-12` against `2026-01`), which is
where the filter matters most. Idempotency is checked by running the project twice from two fresh
sandboxes built the same way and comparing every output table as a row multiset.

Deliberately broken models live in `samples/wf_0007/broken_sql/dbt/models/`, each the canned model
with one realistic translator mistake, and `broken.json` names the diff class the validator
reports for each: a `region_attainment` that forgets the year filter, and an `attainment_history`
whose merge key has lost `PERIOD`.

## Runbook

1. Static check: `python scripts/compile_check.py wf_0007 --target dbt`. Exit 0 is OK, 1 a check
   failed, 2 a usage error.
2. Parity: `python scripts/validate_dbt.py wf_0007`. Exit 0 is a pass for every segment, 1 a
   domain failure, 2 a usage error.
3. Deploy: the `dbt run … --target snowflake` command under Deployment, with the `SNOWFLAKE_*`
   variables set and `dbt-snowflake` installed.
4. The models have no dependency outside the project: dbt runs `wf0007_seg_01_out` first and the
   two target models after it, because both `ref()` it.
5. Rollback: `REGION_ATTAINMENT` is rebuilt wholesale on every run and holds no prior state.
   `ATTAINMENT_HISTORY` is merged into, so a bad run has overwritten matched rows and inserted new
   ones: restore it with Snowflake Time Travel (`CREATE OR REPLACE TABLE … CLONE … BEFORE
   (STATEMENT => …)`) or re-run the Alteryx workflow. `WF0007_SEG_01_OUT` is a work table that the
   next run replaces.

## Open items

- No step of this migration has run against a real Snowflake account or a real Alteryx engine.
  Every rule the translation relies on is an assumption recorded in
  `docs/reference/simulator-semantics.md`, to be checked one by one when an engine is available.
- `dbt-duckdb` is not Snowflake: its types and identifier case folding are DuckDB's, and its
  `merge` strategy is the adapter's own. A green validation says the models matched the golden
  data on DuckDB, not that the `snowflake` target will build them the same way.
- `models/schema.yml` declares `not_null` tests for documentation and for a real `dbt test`;
  `validate_dbt.py` never runs `dbt test`, so they are not part of this repository's pass/fail
  signal.
- The exact-decimal arithmetic the oracle uses is a deliberate simplification about `Double`
  values (`docs/reference/simulator-semantics.md` §1.1). This workflow has no `Double` column —
  both sums are `FixedDecimal(19,2)` — so that risk does not reach it; the rest of that document's
  assumptions (the join, the filter, the Summarize, the merge) still do.
