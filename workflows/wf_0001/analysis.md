# wf_0001 — analysis

Reads `parsed/dag.json`, `intake/mappings.yaml` and `segments/` (the cuts `scripts/segment.py`
proposed). Restates them; adds no claim about behaviour that no artifact supports, and no number
about data — every count, diff and tolerance in this project comes from `scripts/`.

## Tool classification

Every node is `sql`: the cookbook has a pattern for all of them, so the workflow is **T1**.

| Tool | Type | Class | Rule applied |
|------|------|-------|--------------|
| 1 | `input` | `sql` | Input Data → table read; the source is mapped in `mappings.yaml`, so the segment reads it by its logical name |
| 2 | `select` | `sql` | Select → projection + `CAST` + rename; the fixed-width `String(n)` retype becomes `LEFT(x, n)` |
| 3 | `filter` | `sql` | Filter → `WHERE` for the True anchor, `WHERE NOT (…) OR (…) IS NULL` for the False anchor |
| 4 | `formula` | `sql` | Formula → one expression per column, in order, a later one reading the earlier one's result; `ToNumber` → `TRY_TO_*`, `IIF` → `IFF`, `IF/ELSEIF` → `CASE`, `Round(x, m)` over `NUMBER` |
| 5 | `summarize` | `sql` | Summarize → `GROUP BY`; `Count` → `COUNT(*)`, `CountNonNull` → `COUNT(col)`, `Max` over a Date → `MAX` |
| 6 | `sort` | `sql` | Sort → `ORDER BY` with explicit NULL placement |
| 7 | `output` | `sql` | Output Data, write mode `overwrite` → `CREATE OR REPLACE TABLE … AS` |
| 8 | `output` | `sql` | Output Data, write mode `overwrite` → `CREATE OR REPLACE TABLE … AS` |

No node is `snowpark`, `manual` or `unknown`, so `unsupported.json` lists nothing and the status
does not go to `NEEDS_HUMAN`.

## Segmentation

`scripts/segment.py` proposed one segment and it is confirmed unchanged.

- `seg_01` — tools 1, 2, 3, 4, 5, 6, 7, 8. One wave.
- No cut was merged and none was split: there is no ordering dependency spanning a proposed
  boundary, nothing approaches the forty-tool ceiling, there are no Tool Containers to keep
  intact, and there is no macro that would need a segment of its own.
- The segment has no inbound and no outbound edges, so it materialises no `_OUT` work table and
  `contract.outputs[]` holds only the two final targets.

## Contract notes

- `outputs[0]` is the tool 7 target, fed by stream `6_Output`; `outputs[1]` is the tool 8 target,
  fed by stream `3_F`. `output` equals `outputs[0]`.
- The tool 7 target is keyed on `REGION, SIZE_BAND`, the Summarize's own group-by pair, and both
  are declared `NOT NULL` because the Filter removes every NULL-region row before the Summarize.
- The tool 8 target declares no keys. The `edge` golden set carries an exact duplicate row by
  design, so no column set identifies a row there and the comparison falls back to a row
  multiset — exact, but unable to attribute a difference to a column.
- `ordering.order_dependent_columns` is empty: nothing in this workflow numbers rows, takes a
  first or last row, or runs a window over them, so no column's value depends on row order. The
  Sort is cosmetic for parity purposes and is still translated with an explicit `ORDER BY`.
- `row_relation` is `aggregate`, because the Summarize collapses orders into region/band groups.

## Parity risks

| Tool | Class | Risk |
|------|-------|------|
| 2 | `TRUNCATION` | `String(10)` on `CUSTOMER`: Alteryx truncates silently, Snowflake would raise |
| 3 | `NULL_SEMANTICS` | A NULL `REGION` makes the Filter expression NULL, and NULL leaves on the False anchor |
| 4 | `NULL_SEMANTICS` | `ToNumber` warn-and-null on text that is not a plain decimal, and the NULL that follows through the band comparison |
| 4 | `ROUNDING` | `Round(x, 0.01)` is half away from zero on exact decimals; FLOAT arithmetic drifts from it |
| 5 | `LOGIC` | `Count` counts rows where `CountNonNull` counts values |
| 5 | `ROUNDING` | `Sum` over a `Double` column accumulates; the model sums exact decimals and stores once |
| 6 | `ORDERING` | NULL placement differs between ascending and descending |
