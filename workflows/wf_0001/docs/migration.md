# wf_0001 — migration record

Restates `parsed/dag.json`, `analysis.md`, `segments/seg_01/translation_notes.md`,
`segments/seg_01/validation.json`, `intake/mappings.yaml` and `manifest.json`. It adds no claim
about behaviour that those artifacts do not support, and it quotes no number about data: every
count, diff and verdict lives in `segments/seg_01/validation*.json`, written by
`scripts/validate_segment.py`.

**Nothing in this migration has been run on a real Snowflake account or a real Alteryx engine.**
"Validated" here means the translated procedure was executed on the local DuckDB double against
golden data produced by `scripts/dev/alteryx_sim.py`, the project's stand-in for the Alteryx
engine.

## Overview

| | |
|---|---|
| Workflow | `wf_0001` — Sales summary by region and size band |
| Source | `sales_summary.yxmd`, `yxmdVer 2023.1`, AMP engine |
| Owner | `wf_owner` |
| Schedule | `0 6 * * 1-5` (the Alteryx Server schedule recorded at parse time) |
| Consumers | none recorded in `manifest.json` |
| Tier | T1 — every tool has a SQL pattern |
| Segments | `seg_01`, one wave |
| Procedure | `MIG_WORK.WF0001_SEG_01(SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA, RUN_ID)`, `EXECUTE AS CALLER` |

## Source mappings

| Alteryx touchpoint | Logical name | Snowflake | Write mode | Confirmed by |
|---|---|---|---|---|
| `C:\data\sales\orders.yxdb` | `ORDERS` | `SALES.RAW.ORDERS` | read | owner |
| `C:\data\out\sales_summary.yxdb` (tool 7) | `SALES_SUMMARY` | `ANALYTICS.CURATED.SALES_SUMMARY` | overwrite | owner |
| `C:\data\out\excluded_orders.csv` (tool 8) | `EXCLUDED_ORDERS` | `ANALYTICS.CURATED.EXCLUDED_ORDERS` | overwrite | owner |

The procedure never names any of these tables. Sources are read as
`IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.<LOGICAL>')` and targets written as
`IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.<LOGICAL>')`, so the same body runs against the
golden view schema in validation and against the real schema in production.

## Tool → CTE map

| Tool | Type | Segment | CTE |
|------|------|---------|-----|
| 1 | `input` | `seg_01` | `t1_input` |
| 2 | `select` | `seg_01` | `t2_select` |
| 3 | `filter` | `seg_01` | `t3_filter_t` (True anchor) and `t3_filter_f` (False anchor) |
| 4 | `formula` | `seg_01` | `t4_formula` |
| 5 | `summarize` | `seg_01` | `t5_summarize` |
| 6 | `sort` | `seg_01` | `t6_sort` |
| 7 | `output` | `seg_01` | no CTE — the `CREATE OR REPLACE TABLE … AS` statement that ends at `t6_sort` |
| 8 | `output` | `seg_01` | no CTE — the `CREATE OR REPLACE TABLE … AS` statement that ends at `t3_filter_f` |

Two Output tools terminate two branches of one chain, so tools 1–3 appear as CTEs in both
statements. No work table is materialised for the shared prefix; the duplication is the
documented merge recorded in `translation_notes.md`.

## Assumptions

Every assumption is listed one per line in `segments/seg_01/translation_notes.md`. The ones a
reader of this document should know about:

- `ToNumber` is translated as `TRY_TO_DOUBLE`; on the local runtime it accepts `inf` and `nan`
  where the model would give NULL, and no golden set contains either, so that divergence is
  recorded rather than guarded against.
- `CAST(<double> AS NUMBER(38,10))` is assumed to reproduce the model's decimal conversion of a
  stored `Double`. It does for every amount in the golden sets, each of which has at most three
  decimal places; a value needing more than ten would round here and not there.
- The local runtime gives `NUMBER(38,10) * NUMBER(38,10)` scale 20; Snowflake documents scale
  `min(S1 + S2, max(S1, S2, 12))` for multiplication, so the product carries scale 12 there.
  That is enough for this workflow's arithmetic and needs verification on Snowflake.
- `TRIM`, `UPPER` and sort collation are Snowflake's own; the model's are Python's.
- The tool 8 target declares no keys, so its comparison is a row multiset: exact about whether
  the rows match; a difference is attributed to a column by nearest-match pairing, which is a
  heuristic.

## Accepted differences

None. `mappings/global.yaml` accepts the classes `ROUNDING` and `ORDERING` in principle, but
`manifest.accepted_diffs` is empty for this workflow and no cluster has been approved, so a
difference of any class would be a `FAIL`.

## Unsupported / manual items

None. `unsupported.json` is `{"tier": "T1", "unsupported": [], "unknown": []}`; no tool needs
Snowpark and none needs a human rewrite.

## Validation summary

`scripts/validate_segment.py wf_0001 seg_01` compares every entry of `contract.outputs[]` against
its golden file for each golden set and writes the verdicts to
`segments/seg_01/validation.<set>.json`, with the segment-level report in
`segments/seg_01/validation.json`. Read the verdicts, counts and any diff clusters there — this
document does not restate them, because a number about data may come only from a script.

The golden sets exercised are the four `manifest.golden_sets` names: `normal`, `period_end`,
`empty` and `edge`. Idempotency is checked by running the procedure twice from the same starting
state in two fresh sandboxes and comparing the outputs as row multisets.

Deliberately broken copies of this procedure live in `samples/wf_0001/broken_sql/seg_01/`, each
with one realistic translator mistake and a `broken.json` row naming the diff class the validator
reports for it. They exist so the comparison itself stays honest.

## Runbook

1. Deploy `MIG_WORK.WF0001_SEG_01` from `segments/seg_01/proc.sql`.
2. Load or refresh the golden inputs if running against the sandbox:
   `python scripts/load_golden.py wf_0001 <set>`.
3. Call it with the four schema arguments and a run id:
   `CALL MIG_WORK.WF0001_SEG_01('<SRC_DB>', '<SRC_SCHEMA>', '<TGT_DB>', '<TGT_SCHEMA>', '<RUN_ID>')`.
   In validation `SRC_SCHEMA` is the golden view schema `MIG_GOLDEN_WF0001_<SET>`; in production
   it is the schema that exposes `ORDERS` under that logical name.
4. The procedure sets `TIMEZONE` and `WEEK_START` itself, which is why contract C4 requires
   `EXECUTE AS CALLER`: an owner's-rights procedure cannot `ALTER SESSION`.
5. Check parity: `python scripts/validate_segment.py wf_0001 seg_01`. Exit 0 is a pass, 1 a
   domain failure, 2 a usage error.
6. Rollback: both outputs use write mode `overwrite`, so the procedure replaces each target
   wholesale and holds no prior state. Restore a target with Snowflake Time Travel
   (`CREATE OR REPLACE TABLE … CLONE … BEFORE (STATEMENT => …)`) or re-run the Alteryx workflow.
   There is nothing to undo in `MIG_WORK`: this segment materialises no work table.

## Open items

- No step of this migration has run against a real Snowflake account or a real Alteryx engine.
  Every rule the translation relies on is an assumption recorded in
  `docs/reference/simulator-semantics.md`, to be checked one by one when an engine is available.
- The exact-decimal arithmetic the oracle uses is a deliberate simplification and is the single
  largest known parity risk in the project; see that document's §1.1.
- The tool 8 target has no natural key, so its parity check cannot localise a difference to a
  column. A key would need golden data without byte-identical duplicate rows, which the `edge`
  set carries on purpose.
