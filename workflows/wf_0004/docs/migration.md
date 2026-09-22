# wf_0004 — migration record

Restates `parsed/dag.json`, `analysis.md`, `segments/*/translation_notes.md`,
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
| Workflow | `wf_0004` — Inventory by warehouse |
| Source | `inventory.yxmd`, `yxmdVer 2023.1`, E1 engine, plus `Supporting_Macros/clean_codes.yxmc` |
| Owner | `wf_owner` |
| Schedule | `0 6 * * 1-5` (the Alteryx Server schedule recorded at parse time) |
| Consumers | none recorded in `manifest.json` |
| Tier | T1 — every tool, the macro's four included, has a SQL pattern |
| Segments | `seg_01` (tool 1), `seg_02` (tool 2, the macro), `seg_03` (tools 3–7), three waves |
| Procedures | `MIG_WORK.WF0004_SEG_01`, `…_SEG_02`, `…_SEG_03`, all `EXECUTE AS CALLER` |

## Source mappings

| Alteryx touchpoint | Logical name | Snowflake | Write mode | Confirmed by |
|---|---|---|---|---|
| `C:\data\wh\stock.yxdb` (tool 1) | `STOCK` | `WH.RAW.STOCK` | read | owner |
| `C:\data\out\inventory_by_wh.yxdb` (tool 6) | `INVENTORY_BY_WH` | `ANALYTICS.CURATED.INVENTORY_BY_WH` | overwrite | owner |
| `C:\data\out\inventory_long.yxdb` (tool 7) | `INVENTORY_LONG` | `ANALYTICS.CURATED.INVENTORY_LONG` | overwrite | owner |

No procedure names any of these tables. The source is read as
`IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.STOCK')` and the targets written as
`IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.<LOGICAL>')`, so the same bodies run against the
golden view schema in validation and against the real schema in production.

`Supporting_Macros/clean_codes.yxmc` is recorded under `macros:` in `intake/mappings.yaml` as
`inline: true`. It is a **shared** macro: it sits beside the workflow rather than inside it, so
any other workflow in the estate that uses it is translated against the same sub-DAG, and editing
it changes this migration even though `inventory.yxmd` did not change.

## Tool → CTE map

| Tool | Type | Segment | CTE |
|------|------|---------|-----|
| 1 | `input` | `seg_01` | `t1_input` |
| 2 | `macro` | `seg_02` | five CTEs, one per inner tool of `clean_codes.yxmc`: `t2_macro_m1_macro_input`, `t2_macro_m2_regex`, `t2_macro_m3_formula`, `t2_macro_m4_filter_t`, and inner tool 5 (`macro_output`) as the statement's final `SELECT` |
| 3 | `regex` | `seg_03` | `t3_regex` (in both statements) |
| 4 | `cross_tab` | `seg_03` | `t4_cross_tab` (in both statements) |
| 5 | `transpose` | `seg_03` | `t5_transpose` |
| 6 | `output` | `seg_03` | no CTE — the `CREATE OR REPLACE TABLE … AS` statement that ends at `t4_cross_tab` |
| 7 | `output` | `seg_03` | no CTE — the `CREATE OR REPLACE TABLE … AS` statement that ends at `t5_transpose` |

The macro's CTE names carry the parent tool id first (`t2_macro_…`) and then the macro's own tool
id (`m2`, `m3`, `m4`), so both the segment DAG and `clean_codes.yxmc` can be lined up against the
procedure. In `seg_03`, two Output tools terminate two branches of one chain, so tools 3 and 4
appear as CTEs in both statements; no work table is materialised for the shared prefix, and the
duplication is the documented merge recorded in `translation_notes.md`.

## Assumptions

Every assumption is listed one per line in each segment's `translation_notes.md`. The ones a
reader of this document should know about:

- The macro's question `MinQty` is `1`, taken from the calling tool, not the `0` the macro itself
  defaults to. Nothing in `inventory.yxmd` shows the default and nothing in `clean_codes.yxmc`
  shows the override, so both files have to be read together.
- `CopyUnmatched` on the macro's RegEx means an unmatched SKU is **kept**. `REGEXP_REPLACE`
  already returns the subject unchanged when nothing matches, so the two agree — but the flag is
  written down rather than assumed, because `CopyUnmatched` `False` would mean NULL.
- Snowflake's regex dialect is not the Python `re` the oracle uses. `\d` and `\s` are ASCII-only
  in the local engine and Unicode-aware in the oracle, and case-insensitive `[A-Za-z]` folds
  differently at a few Unicode edges. No golden set contains a non-ASCII digit or a non-breaking
  space, so the divergence is recorded rather than guarded against.
- On Snowflake `REGEXP_SUBSTR` returns NULL when nothing matched; the local DuckDB double returns
  an empty string. Tool 3's parse therefore tests the match with `REGEXP_LIKE` and writes the NULL
  out explicitly, which is correct on both.
- The Cross Tab's header columns are the frozen list the tool's `MetaInfo` records, so the pivot
  is one conditional aggregate per column and a warehouse outside that list is dropped — in
  Alteryx as well as here.
- A group/warehouse combination with no rows is NULL and not zero, and the Transpose keeps those
  NULL cells. A bare `UNPIVOT` drops them, but `UNPIVOT INCLUDE NULLS` keeps them too (see
  `cookbook/transpose.md`); this segment instead uses `UNION ALL`, which keeps them as well.
- `CREATE OR REPLACE [TRANSIENT] TABLE … AS` takes its column types from the expressions that
  produced them and does not enforce the `VARCHAR(n)`/`NUMBER(p,s)` widths the contracts declare.
  The `edge` set's 30-character `SKU` and 100-character `NOTE` sit exactly on their limits and
  prove nothing about what Snowflake would do one character further.
- The Snowflake multiplication rule — scale `min(S1 + S2, max(S1, S2, 12))`, not the local
  runtime's `S1 + S2` — is not reached anywhere in this workflow: nothing here multiplies or
  divides.
- Both work streams declare no keys, so their comparisons are row multisets: exact about whether
  the rows match; a difference is attributed to a column by nearest-match pairing, which is a
  heuristic. Both final targets are keyed, so their comparisons can name a column outright.

## Accepted differences

None. `mappings/global.yaml` accepts the classes `ROUNDING` and `ORDERING` in principle, but
`manifest.accepted_diffs` is empty for this workflow and no cluster has been approved, so a
difference of any class would be a `FAIL`.

## Unsupported / manual items

None. `unsupported.json` is `{"tier": "T1", "unsupported": [], "unknown": []}`; no tool needs
Snowpark and none needs a human rewrite. That answer depends on the macro resolving: an
unresolvable `macro_path` would have made tool 2 `manual`, the tier T3 and the status
`NEEDS_HUMAN`, because nothing in this pipeline guesses what a macro does.

## Validation summary

`scripts/validate_segment.py wf_0004 <seg>` compares every entry of `contract.outputs[]` against
its golden file for each golden set and writes the verdicts to
`segments/<seg>/validation.<set>.json`, with the segment-level report in
`segments/<seg>/validation.json`. Read the verdicts, counts and any diff clusters there — this
document does not restate them, because a number about data may come only from a script.

The golden sets exercised are the four `manifest.golden_sets` names: `normal`, `period_end`,
`empty` and `edge`. This workflow has no date column, so `period_end` is a month-end stock count
rather than a set of period-end dates. Idempotency is checked by running each procedure twice from
the same starting state in two fresh sandboxes and comparing the outputs as row multisets.

Deliberately broken copies of the procedures live in `samples/wf_0004/broken_sql/`, each with one
realistic translator mistake and a `broken.json` row naming the diff class the validator reports
for it. They exist so the comparison itself stays honest.

## Runbook

1. Deploy `MIG_WORK.WF0004_SEG_01`, `…_SEG_02` and `…_SEG_03` from each segment's `proc.sql`.
2. Load or refresh the golden inputs if running against the sandbox:
   `python scripts/load_golden.py wf_0004 <set>`. Neither target has a prior state, so there is no
   `targets_before` file to load.
3. Call the three procedures **in order**, `seg_01` then `seg_02` then `seg_03` — they are three
   waves, not one — each with the four schema arguments and a run id:
   `CALL MIG_WORK.WF0004_SEG_01('<SRC_DB>', '<SRC_SCHEMA>', '<TGT_DB>', '<TGT_SCHEMA>', '<RUN_ID>')`.
   In validation `SRC_SCHEMA` is the golden view schema `MIG_GOLDEN_WF0004_<SET>`; in production it
   is the schema that exposes `STOCK` under that logical name.
4. Each procedure sets `TIMEZONE` and `WEEK_START` itself, which is why contract C4 requires
   `EXECUTE AS CALLER`. Neither setting can affect this workflow's result: it has no date column.
5. Check parity: `python scripts/validate_segment.py wf_0004 seg_01`, then `seg_02`, then
   `seg_03`. Exit 0 is a pass, 1 a domain failure, 2 a usage error.
6. Rollback: both outputs use write mode `overwrite`, so `seg_03` replaces each target wholesale
   and holds no prior state. Restore a target with Snowflake Time Travel
   (`CREATE OR REPLACE TABLE … CLONE … BEFORE (STATEMENT => …)`) or re-run the Alteryx workflow.
   `seg_01` and `seg_02` write only their own transient work tables, which the next run replaces.

## Open items

- No step of this migration has run against a real Snowflake account or a real Alteryx engine.
  Every rule the translation relies on is an assumption recorded in
  `docs/reference/simulator-semantics.md`, to be checked one by one when an engine is available.
- The exact-decimal arithmetic the oracle uses is a deliberate simplification and is the single
  largest known parity risk in the project; see that document's §1.1.
- `clean_codes.yxmc` is inlined, not translated once and shared. If other workflows in the estate
  use the same macro, each will carry its own copy of these five CTEs and they will drift apart.
  A Snowflake UDTF or a shared view would keep one definition; that decision was not made here.
- The regex dialect gap is the one assumption in this workflow that no golden set exercises. Any
  SKU containing a non-ASCII digit, a Unicode space or a character whose case folding differs
  between Python and Snowflake would need checking on an account before this goes live.
