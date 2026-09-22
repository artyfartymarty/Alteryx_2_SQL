# wf_0002 — migration record

Restates `parsed/dag.json`, `analysis.md`, every `segments/*/translation_notes.md`, every
`segments/*/validation.json`, `intake/mappings.yaml` and `manifest.json`. It adds no claim about
behaviour that those artifacts do not support, and it quotes no number about data: every count,
diff and verdict lives in `segments/<seg>/validation*.json`, written by
`scripts/validate_segment.py`.

**Nothing in this migration has been run on a real Snowflake account or a real Alteryx engine.**
"Validated" here means the translated procedures were executed on the local DuckDB double against
golden data produced by `scripts/dev/alteryx_sim.py`, the project's stand-in for the Alteryx
engine.

## Overview

| | |
|---|---|
| Workflow | `wf_0002` — Customer orders fact load |
| Source | `customer_orders.yxmd`, `yxmdVer 2023.1`, E1 engine |
| Owner | `wf_owner` |
| Schedule | `0 6 * * 1-5` (the Alteryx Server schedule recorded at parse time) |
| Consumers | none recorded in `manifest.json` |
| Tier | T1 — every tool has a SQL pattern |
| Segments | `seg_01`, `seg_02` (wave 1, independent), then `seg_03` (wave 2) |
| Procedures | `MIG_WORK.WF0002_SEG_01`, `…_SEG_02`, `…_SEG_03`, each taking `(SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA, RUN_ID)` and declaring `EXECUTE AS CALLER` |

## Source mappings

| Alteryx touchpoint | Logical name | Snowflake | Write mode | Confirmed by |
|---|---|---|---|---|
| `\\fileserver\crm\customers.yxdb` (tool 1) | `CUSTOMERS` | `CRM.RAW.CUSTOMERS` | read | owner |
| `\\fileserver\crm\orders_export.csv` (tool 3) | `ORDERS_EXPORT` | `CRM.RAW.ORDERS_EXPORT` | read | owner |
| ODBC `DW_SALES`, `dbo.CUSTOMER_ORDER_FACT` (tool 10) | `CUSTOMER_ORDER_FACT` | `ANALYTICS.CURATED.CUSTOMER_ORDER_FACT` | append | owner |

Tool 10's connection string carried credentials in the source file; the parser scrubbed it to
`<scrubbed:dw_sales>` before anything was written under `workflows/`, and only the alias is used
as the touchpoint key. The procedures never name any of these tables: sources are read as
`IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.<LOGICAL>')` and the target written as
`IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.<LOGICAL>')`.

Between segments the work tables are written and read at their literal contract C3 names:
`MIG_WORK.WF0002_SEG_01_OUT` and `MIG_WORK.WF0002_SEG_02_OUT`.

## Tool → CTE map

| Tool | Type | Segment | CTE |
|------|------|---------|-----|
| 100, 200, 210 | `container` | — | none — containers carry no data |
| 300 | `comment` | — | none — a TextBox carries no data |
| 1 | `input` | `seg_01` | `t1_input` |
| 2 | `data_cleansing` | `seg_01` | `t2_data_cleansing` |
| 3 | `input` | `seg_02` | `t3_input` |
| 4 | `select` | `seg_02` | `t4_select` |
| 5 | `join` | `seg_03` | `t5_join_j`, `t5_join_l`, `t5_join_r` (one per anchor) |
| 6 | `formula` | `seg_03` | `t6_formula` |
| 7 | `formula` | `seg_03` | `t7_formula` |
| 8 | `formula` | `seg_03` | `t8_formula` |
| 9 | `union` | `seg_03` | `t9_union` |
| 10 | `output` | `seg_03` | no CTE — the `INSERT … SELECT` that ends at `t9_union` |
| 11 | `browse` | `seg_03` | **no CTE** — a Browse emits nothing, so there is nothing to compute |

## Assumptions

Every assumption is listed one per line in the three `translation_notes.md` files. The ones a
reader of this document should know about:

- Data Cleansing's options are nested in the macro's own order — replace NULL strings, then trim,
  then modify case — which is what makes `NAME` and `CITY` never NULL downstream.
- `TRIM` is Snowflake's and removes spaces; the model's removes whitespace generally, so a tab or
  newline around a value would survive here and not there. No golden set contains one.
- `UPPER` uses Snowflake's Unicode case rules, which are not guaranteed to match the model's for
  every script; the `edge` golden set carries Latin Extended and CJK text.
- `TRY_TO_DATE(x, 'YYYY-MM-DD')` is translated by the local runtime to a plain `TRY_CAST` to
  `DATE`, ignoring the format. That agrees for the ISO text in these golden sets but would not
  for text in another layout that ISO parsing happens to accept.
- The `L` and `R` anchors are `NOT EXISTS` anti-joins, never `NOT IN`, so a NULL key behaves as
  the model says it does.
- The Union writes typed NULLs for the columns a branch lacks, so the result's column types come
  from the declaration rather than from whichever branch is first.
- No output declares keys, so every comparison in this workflow is a row multiset: exact about
  whether the rows match; a difference is attributed to a column by nearest-match pairing, which
  is a heuristic.

## Accepted differences

None. `mappings/global.yaml` accepts the classes `ROUNDING` and `ORDERING` in principle, but
`manifest.accepted_diffs` is empty for this workflow and no cluster has been approved, so a
difference of any class would be a `FAIL`.

## Unsupported / manual items

None. `unsupported.json` is `{"tier": "T1", "unsupported": [], "unknown": []}`. The Data
Cleansing tool is a macro by construction, but the parser reads its options from the
configuration's `<Value name="…">` pairs, so it needs no macro file and no human rewrite.

## Validation summary

`scripts/validate_segment.py wf_0002 <seg>` compares every entry of `contract.outputs[]` against
its golden file for each golden set and writes the verdicts to
`segments/<seg>/validation.<set>.json`, with the segment-level report in
`segments/<seg>/validation.json`. Read the verdicts, counts and any diff clusters there — this
document does not restate them, because a number about data may come only from a script.

The golden sets exercised are the four `manifest.golden_sets` names: `normal`, `period_end`,
`empty` and `edge`. Each segment is validated in isolation: `seg_03`'s two inputs are filled from
`golden/intermediates/seg_01/<set>/2_Output.csv` and
`golden/intermediates/seg_02/<set>/4_Output.csv` rather than by running the upstream procedures,
and its target starts from `golden/targets_before/<set>/CUSTOMER_ORDER_FACT.csv`.

Idempotency for `seg_03` means "the same starting state gives the same final state": the
procedure is run twice, each time from scratch in its own fresh sandbox loaded from the same
starting state. Running an `append` twice against one un-reset target would legitimately double
the rows, so that can never be the test.

Deliberately broken copies of these procedures live in `samples/wf_0002/broken_sql/<seg>/`, each
with one realistic translator mistake and a `broken.json` row naming the diff class the validator
reports for it. They exist so the comparison itself stays honest.

## Runbook

1. Deploy the three procedures from `segments/*/proc.sql`.
2. Load or refresh the golden inputs if running against the sandbox:
   `python scripts/load_golden.py wf_0002 <set>` — which also loads
   `targets_before/<set>/CUSTOMER_ORDER_FACT.csv` into `MIG_WORK`, the state the append adds to.
3. Run wave 1, in either order or in parallel: `CALL MIG_WORK.WF0002_SEG_01(…)` and
   `CALL MIG_WORK.WF0002_SEG_02(…)`.
4. Run wave 2 once both have finished: `CALL MIG_WORK.WF0002_SEG_03(…)`. Each call takes
   `('<SRC_DB>', '<SRC_SCHEMA>', '<TGT_DB>', '<TGT_SCHEMA>', '<RUN_ID>')`.
5. The procedures set `TIMEZONE` and `WEEK_START` themselves, which is why contract C4 requires
   `EXECUTE AS CALLER`: an owner's-rights procedure cannot `ALTER SESSION`.
6. Check parity per segment: `python scripts/validate_segment.py wf_0002 <seg>`. Exit 0 is a
   pass, 1 a domain failure, 2 a usage error.
7. Rollback: `seg_03` appends, so a re-run without a reset adds the rows again. Restore the
   target from Snowflake Time Travel before re-running. The two work tables are transient and are
   replaced wholesale by their own segment, so nothing has to be cleaned up between runs.

## Open items

- No step of this migration has run against a real Snowflake account or a real Alteryx engine.
  Every rule the translation relies on is an assumption recorded in
  `docs/reference/simulator-semantics.md`, to be checked one by one when an engine is available.
- Unicode case folding and whitespace trimming are the two rules most likely to differ between
  Snowflake and Alteryx, and this workflow depends on both.
- No stream in this workflow has a natural key, so no parity check here can localise a difference
  to a column. A key would need golden data without byte-identical duplicate rows, which every
  `edge` set carries on purpose.
