# wf_0004 — analysis

Reads `parsed/dag.json`, `intake/mappings.yaml` and `segments/` (the cuts `scripts/segment.py`
proposed). Restates them; adds no claim about behaviour that no artifact supports, and no number
about data — every count, diff and tolerance in this project comes from `scripts/`.

## Tool classification

Every node is `sql`, so the workflow is **T1**. Tool 2 is a macro: it has no pattern of its own,
and it is classified by the tools inside `clean_codes.yxmc`, all four of which are `sql`.

| Tool | Type | Class | Rule applied |
|------|------|-------|--------------|
| 1 | `input` | `sql` | Input Data → table read; the source is mapped in `mappings.yaml`, so the segment reads it by its logical name |
| 2 | `macro` | `sql` | Resolved on disk (`macro_path` `Supporting_Macros/clean_codes.yxmc`, `unresolved` false), so its `sub_dag` is inlined one CTE per inner tool. The question `MinQty` is `1` from the caller, not the macro's own default `0` |
| 2 → m1 | `macro_input` | `sql` | A parameter, not a read: carries the incoming stream through |
| 2 → m2 | `regex` | `sql` | Method `Replace`, case-insensitive, `CopyUnmatched` → `REGEXP_REPLACE(x, p, r, 1, 0, 'i')`, which already keeps an unmatched subject |
| 2 → m3 | `formula` | `sql` | `Uppercase([SKU])` → `UPPER(SKU)`, updating the field in place |
| 2 → m4 | `filter` | `sql` | `WHERE` for the True anchor; a NULL quantity is not true, so it leaves on the unwired False anchor |
| 2 → m5 | `macro_output` | `sql` | The anchor the parent reads: the statement's final `SELECT` |
| 2 → m10 | `interface` | — | A question tool carries no data |
| 3 | `regex` | `sql` | Method `Parse`, case-sensitive → one `REGEXP_SUBSTR(…, group)` per capture group, guarded by `REGEXP_LIKE` so a non-match is NULL |
| 4 | `cross_tab` | `sql` | Frozen header list from `meta.Output` → one conditional aggregate per header column; an empty combination is NULL |
| 5 | `transpose` | `sql` | Key fields, then `Name`/`Value` per data field → three projections stacked with `UNION ALL` (a bare `UNPIVOT` would drop the NULL cells) |
| 6 | `output` | `sql` | Output Data, write mode `overwrite` → `CREATE OR REPLACE TABLE … AS` |
| 7 | `output` | `sql` | Output Data, write mode `overwrite` → `CREATE OR REPLACE TABLE … AS` |

No node is `snowpark`, `manual` or `unknown`, so `unsupported.json` lists nothing and the status
does not go to `NEEDS_HUMAN`. Had the macro been unresolvable, tool 2 would have been `manual`,
the tier T3 and the status `NEEDS_HUMAN`: nothing in this pipeline guesses what a macro does.

## Segmentation

`scripts/segment.py` proposed three segments and all three are confirmed unchanged.

- `seg_01` — tool 1. Wave 1.
- `seg_02` — tool 2, the macro. Wave 2.
- `seg_03` — tools 3, 4, 5, 6, 7. Wave 3.
- Nothing was split and nothing was merged. The rule that fixes this shape is "give every macro a
  segment of its own": a macro contributes a whole sub-workflow, not a clause, so tool 2 cannot
  share a statement with its neighbours. That leaves `seg_01` at one tool, below the `min_tools`
  floor of 3, and it stays there — there is nothing upstream to merge it with, and merging it
  forwards would defeat the rule that put the macro on its own.
- `seg_01` and `seg_02` each have one outbound edge, so each materialises a work table
  (`MIG_WORK.WF0004_SEG_01_OUT`, `MIG_WORK.WF0004_SEG_02_OUT`) and the next segment's `inputs[0]`
  carries `from`, `stream` and that table. The macro's anchors are named after its own tools, so
  the stream out of tool 2 is `2_Output5`, not `2_Output`.

## Contract notes

- `seg_01` and `seg_02` each have one output, a work stream, so `output` equals `outputs[0]`.
  `seg_03` has two, both `kind: "target"`: `outputs[0]` is tool 6 fed by stream `4_Output` and
  `outputs[1]` is tool 7 fed by stream `5_Output`.
- Neither work stream declares keys. The `edge` golden set carries two byte-identical `DUP-1` rows
  by design, so no column set identifies a row there and the comparison falls back to a row
  multiset — exact about whether the rows match, and able to attribute a difference to a column
  only through nearest-match pairing.
- Both targets **are** keyed: tool 6 on `SKU, FAMILY` (the Cross Tab's own group-by fields) and
  tool 7 on `SKU, NAME`. Neither combination repeats in any golden set, because the Cross Tab
  collapsed the duplicates that made the upstream streams unkeyable.
- `SKU`, `WAREHOUSE` and `QTY` are declared `NOT NULL` on `seg_02`'s stream, and `SKU` on both
  targets: the macro's Filter removes the only row that ever had a NULL one (the `edge` set's
  all-NULL row, dropped because `NULL >= 1` is not true). All of them were checked against all
  four golden sets.
- `ordering.order_dependent_columns` is empty in all three segments: nothing here numbers rows,
  takes a first or last row, or runs a window, so no column's value depends on row order. The
  Cross Tab's group order and the Transpose's field order are still reproduced, because they are
  free to reproduce.
- `row_relation` is `1:1` for `seg_01` (a read), `filter` for `seg_02` (the macro's Filter removes
  rows) and `aggregate` for `seg_03` (the Cross Tab collapses rows into SKU groups; the Transpose
  then expands them again, but the segment as a whole is an aggregation).

## Parity risks

| Tool | Class | Risk |
|------|-------|------|
| 2 | `LOGIC` | The question `MinQty` is `1` in the caller and `0` in the macro; the caller wins |
| 2 | `LOGIC` | RegEx `Replace` with `CopyUnmatched`: an unmatched SKU is kept, not nulled |
| 2 | `LOGIC` | Case-insensitive here, case-sensitive at tool 3 — opposite flags on two RegEx tools |
| 2 | `NULL_SEMANTICS` | `NULL >= 1` is NULL, so a NULL quantity leaves on the Filter's False anchor |
| 2 | `LOGIC` | Snowflake's regex dialect is not the Python `re` the oracle uses: `\d` and `\s` are ASCII-only in one and Unicode-aware in the other |
| 3 | `NULL_SEMANTICS` | RegEx `Parse` keeps a non-matching row with both parsed fields NULL; `REGEXP_SUBSTR` returns NULL on Snowflake but an empty string on the local double |
| 4 | `NULL_SEMANTICS` | A group/header combination with no rows is NULL, not zero |
| 4 | `LOGIC` | The header list is frozen by the tool's MetaInfo; a warehouse outside it has no column |
| 4 | `ROUNDING` | Cross Tab's `Sum` produces a `Double` whatever the data field's type |
| 5 | `NULL_SEMANTICS` | Transpose keeps NULL values; a bare `UNPIVOT` drops them, but `UNPIVOT INCLUDE NULLS` keeps them too (this segment uses `UNION ALL`, which keeps them as well) |
| 5 | `LOGIC` | The key field is `SKU` only, so `FAMILY` is dropped from the long table |
| 6 | `LOGIC` | Two Output tools terminate two branches of one chain, so tools 3 and 4 are computed twice |
