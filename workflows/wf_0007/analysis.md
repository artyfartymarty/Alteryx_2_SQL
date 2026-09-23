# wf_0007 — analysis

Reads `parsed/dag.json`, `intake/mappings.yaml`, `segments/` (the cuts `scripts/segment.py`
proposed) and `segments/targets.json` (the targets `scripts/target_check.py` proposed). Restates
them; adds no claim about behaviour that no artifact supports, and no number about data — every
count, diff and tolerance in this project comes from `scripts/`.

## Tool classification

Every node is `sql`, so the workflow is **T1**.

| Tool | Type | Class | Rule applied |
|------|------|-------|--------------|
| 1 | `input` | `sql` | Input Data → a read of the mapped source by its logical name, `TARGETS` |
| 2 | `input` | `sql` | Input Data → a read of `ACTUALS` |
| 3 | `join` | `sql` | Join on `REGION, PERIOD`, J anchor only (L and R are wired to nothing) → an inner join on both keys; `Right_REGION`, `Right_PERIOD` deselected |
| 4 | `filter` | `sql` | `WHERE LEFT(PERIOD, 4) = '2026'` for the True anchor; the False anchor is wired to nothing |
| 5 | `summarize` | `sql` | Group by `REGION, PERIOD`; `Sum` → `SUM`, `Count` → `COUNT(*)` (Alteryx counts rows, not values) |
| 6 | `output` | `sql` | Output Data, `Overwrite`, a yxdb file target |
| 7 | `output` | `sql` | Output Data, `Update; Insert if new` on `REGION, PERIOD`, a database target with prior state |

No node is `manual` or `unknown`, so `unsupported.json` lists nothing.

## Output target per segment, and the output kind

`segments/targets.json` (written by `scripts/target_check.py`, not by this document) proposes a
target per segment and the workflow's output kind. The analyzer may only lower a proposal, never
raise one; nothing here was lowered.

| Segment | Target | Why |
|---------|--------|-----|
| `seg_01` | `sql` | Tools 1–3 are all `sql` |
| `seg_02` | `sql` | Tools 4–7 are all `sql` |

**Output kind `dbt`.** The preference is `dbt` (`manifest.json`'s `output_target`, from
`sample.json`), and `targets.json` grants it with an empty `dbt_blockers` list and the reason
"preference dbt; every segment is sql and every output is dbt-expressible". Both segments are
`sql`; neither Output tool has PreSQL or PostSQL; tool 6's `overwrite` maps to a `table` model and
tool 7's `merge` has its keys, `REGION, PERIOD`. So the whole workflow is translated as **one dbt
project** under `dbt/`, with one model per contract output: the work stream model
`wf0007_seg_01_out` and the two target models `region_attainment` and `attainment_history`.
There is no procedure per segment and no `master.sql`.

## Segmentation

`scripts/segment.py` proposed two segments and both are confirmed unchanged.

- `seg_01` — tools 1, 2, 3 (Tool Container 10, "Pair plan targets with actuals"). Wave 1. The Join
  is never separated from its two inputs.
- `seg_02` — tools 4, 5, 6, 7 (Tool Container 11, "Current-year attainment"). Wave 2.
- Nothing was split and nothing was merged. `min_tools: 2` keeps the two containers apart, which
  puts the year filter in the same segment as the two outputs.
- `seg_01` has one outbound edge, the Join's `J` anchor, so its stream is `3_J` and it
  materialises at `MIG_WORK.WF0007_SEG_01_OUT`; `seg_02`'s `inputs[0]` carries `from`, `stream` and
  that table.
- Tool 5's single output feeds both Output tools, so `seg_02` has two `target` outputs on the same
  stream, `5_Output` — the case `validate_dbt.py` reports as two checks, `5_Output:target:6` and
  `5_Output:target:7`.

## Contract notes

- Both contracts carry `"target": "sql"`: dbt is the workflow's output kind, not a segment target.
- Column types come from the golden schema files through `types_map.alteryx_to_snowflake`
  (`V_String` → `VARCHAR`, `FixedDecimal(19,2)` → `NUMBER(19,2)`, `Int64` → `NUMBER(38,0)`).
- `REGION` and `PERIOD` are `NOT NULL` on the `3_J` stream: the inner join refuses a NULL key, so
  none survives it. `TARGET` and `ACTUAL` stay nullable (the `normal` set has a NULL actual).
- `REGION_ATTAINMENT` declares `REGION`, `PERIOD` and `LINES` `NOT NULL` (group keys that are never
  NULL, and a row count); `ATTAINMENT_HISTORY` declares every column nullable, because its final
  state holds rows this run never wrote.
- Both targets declare `["REGION", "PERIOD"]` as their keys, so each comparison is keyed and a
  difference names the region and period that moved. `ATTAINMENT_HISTORY`'s keys are also its
  merge keys, from tool 7's `UpdateKeys`.
- `row_relation` is `expand` for `seg_01` (the join can fan a target out over several actual
  lines) and `aggregate` for `seg_02`.
- Nothing numbers rows, takes a first or last row or runs a window, so both contracts'
  `ordering.order_dependent_columns` are empty.

## Parity risks

| Tool | Class | Risk |
|------|-------|------|
| 3 | `NULL_SEMANTICS` | A NULL `REGION` or `PERIOD` matches nothing, so that target (or actual) leaves on an unwired anchor and is lost; `=` in an inner join does the same, `IS NOT DISTINCT FROM` would not |
| 3 | `LOGIC` | The join fans out: a key with two actual lines pairs its target with both, so the target is summed twice downstream. That is Alteryx's result and the golden data keeps it; pre-aggregating the actuals before the join would disagree |
| 3 | `LOGIC` | Key comparison is exact and case-sensitive, with no trimming |
| 4 | `NULL_SEMANTICS` | `LEFT(NULL, 4)` is NULL and a NULL condition is not true, so the row is dropped (none reaches this tool: the join has already refused a NULL `PERIOD`) |
| 4 | `LOGIC` | `User.CurrentYear` is a text constant, inlined as `'2026'`; `LEFT` keeps a malformed `PERIOD` that starts with `2026` |
| 5 | `LOGIC` | Alteryx's `Count` counts rows, so `LINES` is `COUNT(*)` |
| 5 | `NULL_SEMANTICS` | `Sum` skips NULLs and an all-NULL group sums to NULL, not 0 |
| 5 | `ROUNDING` | The sums are exact `FixedDecimal(19,2)` arithmetic on both sides |
| 6 | `LOGIC` | `Overwrite`: the target holds this run's rows and nothing else |
| 7 | `LOGIC` | `Update; Insert if new` on both key columns: matched rows are overwritten, new keys inserted, every other row — prior-year history included — left as it was. A narrower merge key would overwrite a region's other periods and lose rows |
| 7 | `NULL_SEMANTICS` | A NULL update key would never match; the stream carries none |
