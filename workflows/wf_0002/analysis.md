# wf_0002 — analysis

Reads `parsed/dag.json`, `intake/mappings.yaml` and `segments/` (the cuts `scripts/segment.py`
proposed). Restates them; adds no claim about behaviour that no artifact supports, and no number
about data — every count, diff and tolerance in this project comes from `scripts/`.

## Tool classification

Every node that carries data is `sql`, so the workflow is **T1**. Containers and the TextBox
carry no data: the dag contract keeps them as nodes, they never appear in `edges`, and they are
ignored by segment size counts.

| Tool | Type | Class | Rule applied |
|------|------|-------|--------------|
| 100 | `container` | n/a | Tool Container — carries no data, gets no CTE |
| 200 | `container` | n/a | Tool Container — carries no data, gets no CTE |
| 210 | `container` | n/a | nested Tool Container — carries no data, gets no CTE |
| 300 | `comment` | n/a | TextBox — carries no data, gets no CTE |
| 1 | `input` | `sql` | Input Data → table read by its logical name from `mappings.yaml` |
| 2 | `data_cleansing` | `sql` | Data Cleansing → one transform per option, in the macro's own order: `COALESCE` for replace-nulls, `TRIM`, then `UPPER` |
| 3 | `input` | `sql` | Input Data (CSV) → table read by its logical name; `FieldLen` 254 means every column is text |
| 4 | `select` | `sql` | Select → projection + `CAST`; warn-and-null retypes become `TRY_TO_NUMBER` / `TRY_TO_DOUBLE` / `TRY_TO_DATE` |
| 5 | `join` | `sql` | Join → `INNER JOIN` for `J` plus anti-joins for `L` and `R`; duplicate right-side names would arrive `Right_`-prefixed |
| 6 | `formula` | `sql` | Formula → one literal column |
| 7 | `formula` | `sql` | Formula → one literal column |
| 8 | `formula` | `sql` | Formula → one literal column |
| 9 | `union` | `sql` | Union → `UNION ALL` with name alignment against input #1's field order |
| 10 | `output` | `sql` | Output Data, write mode `append` → `INSERT … SELECT` with the target's columns named |
| 11 | `browse` | `sql` | Browse → emits nothing; no CTE and no statement |

No node is `snowpark`, `manual` or `unknown`, so `unsupported.json` lists nothing and the status
does not go to `NEEDS_HUMAN`.

## Segmentation

`scripts/segment.py` proposed three segments in two waves and they are confirmed unchanged.

- `seg_01` — tools 1, 2 (Tool Container 100). Outbound stream `2_Output` → `seg_03`.
- `seg_02` — tools 3, 4 (Tool Container 210). Outbound stream `4_Output` → `seg_03`.
- `seg_03` — tools 5, 6, 7, 8, 9, 10, 11. Inbound from both.
- Waves: `[[seg_01, seg_02], [seg_03]]`.
- Nothing was merged or split. Each container is kept intact, none is oversized, there is no
  ordering dependency spanning a boundary — no tool in this workflow numbers rows, takes a first
  or last row, or runs a window — and there is no macro needing a segment of its own. The Browse
  rides with its upstream group, which is `seg_03`.

## Contract notes

- `seg_01` and `seg_02` each have one `kind: "work"` output, at the primary work-table name
  contract C3 gives (`MIG_WORK.WF0002_SEG_0N_OUT`), and no target.
- `seg_03` has no outbound stream and one `kind: "target"` output: tool 10, logical
  `CUSTOMER_ORDER_FACT`, fed by stream `9_Output`. Its two inputs carry `from`, `stream` and the
  literal upstream `table`, so the validator knows which golden intermediate fills each one.
- No output declares keys. Every `edge` golden set in this workflow carries byte-identical
  duplicate rows by design — `CUST_ID` 12 on the customer side, `ORDER_ID` 3003 on the order
  side, and four identical joined rows in the target — so no column set identifies a row and the
  comparison falls back to a row multiset, which is exact about whether the rows match and
  attributes a difference to a column only by nearest-match pairing, a heuristic.
- `ordering.order_dependent_columns` is empty for all three segments: no column's value depends
  on row order anywhere in this workflow.
- `NAME` and `CITY` are declared `NOT NULL` on `2_Output`, because Data Cleansing's
  replace-nulls-with-blank option runs before everything else; `MATCH_FLAG` is declared `NOT NULL`
  on the target, because all three union branches write a literal.
- `row_relation` is `1:1` for the two prep segments and `expand` for `seg_03`, where a
  customer matched by several orders produces several rows.

## Parity risks

| Tool | Class | Risk |
|------|-------|------|
| 2 | `NULL_SEMANTICS` | Replace-nulls-with-blank runs first, so a NULL `NAME` becomes an empty string and never stays NULL |
| 2 | `LOGIC` | The options have a defined order, and Unicode case folding is the engine's own |
| 4 | `NULL_SEMANTICS` | Warn-and-null coercion of text to Int32 / Double / Date |
| 4 | `TYPE` | The whole source is `V_String(254)`; an invalid date and a non-numeric amount must land as NULL |
| 5 | `NULL_SEMANTICS` | A NULL join key matches nothing and leaves on `L` or `R` |
| 5 | `LOGIC` | Key comparison is exact and case-sensitive; duplicates on both sides fan out |
| 9 | `LOGIC` | Union by name: field order comes from input #1 and a missing field arrives NULL |
| 10 | `LOGIC` | `append` keeps the target's prior state and maps columns by name, so the expected result is the state after the insert |
