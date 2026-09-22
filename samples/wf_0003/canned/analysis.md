# wf_0003 — analysis

Reads `parsed/dag.json`, `intake/mappings.yaml` and `segments/` (the cuts `scripts/segment.py`
proposed). Restates them; adds no claim about behaviour that no artifact supports, and no number
about data — every count, diff and tolerance in this project comes from `scripts/`.

## Tool classification

Every node is `sql`: the cookbook has a pattern for all of them, so the workflow is **T1**.

| Tool | Type | Class | Rule applied |
|------|------|-------|--------------|
| 1 | `input` | `sql` | Input Data → table read; the source is mapped in `mappings.yaml`, so the segment reads it by its logical name. The tool's own query contributes its projection and its `WHERE` |
| 2 | `filter` | `sql` | Filter → `WHERE` for the True anchor; the False anchor is unwired, so no second branch is built |
| 3 | `datetime` | `sql` | DateTime, text → DateTime → `TRY_TO_TIMESTAMP_NTZ(x, fmt)`, the warn-and-null form |
| 4 | `sort` | `sql` | Sort → `ORDER BY` with explicit NULL placement |
| 5 | `unique` | `sql` | Unique → `QUALIFY ROW_NUMBER() OVER (PARTITION BY <keys> ORDER BY <the sort's keys>) = 1` |
| 6 | `multi_row_formula` | `sql` | `[Row-1:F] + [x]` with `OtherRows` 0 → a running `SUM` window over the same order |
| 7 | `record_id` | `sql` | Record ID → `ROW_NUMBER() OVER (ORDER BY <the sort's keys>)`, first position |
| 8 | `formula` | `sql` | Formula → one expression per column; `DateTimeFormat` → `TO_CHAR(x, fmt)`, `IIF` → `IFF` |
| 9 | `summarize` | `sql` | Summarize → `GROUP BY`; `Count` → `COUNT(*)`, `Sum` over a FixedDecimal stays NUMBER, `Last` → `MAX_BY(col, <the record id>)`, `Max` over a string → `MAX` |
| 10 | `output` | `sql` | Output Data, write mode `update_insert` → PreSQL, `MERGE` on the update keys, PostSQL — three statements |
| 100, 200 | `container` | — | Tool Containers carry no data; they are layout, and they are what the segmenter cuts on |

No node is `snowpark`, `manual` or `unknown`, so `unsupported.json` lists nothing and the status
does not go to `NEEDS_HUMAN`.

## Segmentation

`scripts/segment.py` proposed two segments and both are confirmed unchanged.

- `seg_01` — tools 1, 2, 3. Wave 1.
- `seg_02` — tools 4, 5, 6, 7, 8, 9, 10. Wave 2.
- Nothing was split and nothing needed merging on review: the ordering dependency that matters
  (tool 9's `Last`, reached back through 8, 7, 6 and 5 to the Sort at 4) is already inside one
  segment, which is why the "Publish" container's own cut at 9–10 does not survive. Nothing
  approaches the forty-tool ceiling and there is no macro needing a segment of its own.
- `seg_01` has one outbound edge and `seg_02` one inbound edge, so `seg_01` materialises
  `MIG_WORK.WF0003_SEG_01_OUT` and `seg_02`'s `inputs[0]` carries `from`, `stream` and that table.

## Contract notes

- `seg_01.outputs[0]` is the work stream `3_Output`; `seg_02.outputs[0]` is the tool 10 target,
  fed by stream `9_Output`. Each segment has exactly one output, so `output` equals `outputs[0]`
  in both.
- The `seg_02` target is keyed on `ACCT, PERIOD`, the Output tool's own update keys. Both columns
  are nullable: the `edge` golden set has a row whose `ACCT` and `PERIOD` are NULL, which is the
  row that proves a NULL update key matches nothing and is inserted.
- `seg_01`'s work stream declares no keys. The `edge` golden set carries two byte-identical `DUP`
  rows by design, so no column set identifies a row there and the comparison falls back to a row
  multiset — exact about whether the rows match, and able to attribute a difference to a column
  only through nearest-match pairing.
- `AMOUNT` and `REGION` are declared `NOT NULL` on the work stream: the Input tool's own
  `WHERE AMOUNT <> 0` removes every NULL amount and the Filter keeps only `EMEA`. Both were
  checked against all four golden sets.
- `seg_02.ordering.order_dependent_columns` is `["CLOSING_BAL"]`. The Record ID is the textbook
  order-dependent column, but tool 9 drops it; what survives to the target is the `Last` it makes
  possible. `TOTAL`, `ENTRIES` and `HAS_PERIOD_END` are order-free aggregates.
- `row_relation` is `filter` for `seg_01` (the region Filter removes rows; the DateTime tool adds
  a column) and `aggregate` for `seg_02` (the Summarize collapses entries into account/period
  groups).

## Parity risks

| Tool | Class | Risk |
|------|-------|------|
| 1 | `LOGIC` | The Input tool's embedded `WHERE AMOUNT <> 0` is part of what Alteryx read and has to be re-applied |
| 2 | `NULL_SEMANTICS` | A NULL `REGION` makes the Filter expression NULL, and NULL leaves on the False anchor |
| 2 | `LOGIC` | `[User.Region]` is a workflow constant resolved at translation time, not an argument |
| 3 | `NULL_SEMANTICS` | Text the format cannot read becomes NULL; `31/02/2026` is the row that proves it |
| 3 | `TYPE` | A parsed Alteryx date carries midnight, so the column is `TIMESTAMP_NTZ` and not `DATE` |
| 4 | `ORDERING` | NULL placement differs between ascending and descending, and this Sort is not cosmetic |
| 5 | `LOGIC` | Unique keeps the first row in incoming order, so the Sort decides which duplicate survives |
| 5 | `NULL_SEMANTICS` | Two NULL key values count as the same key |
| 6 | `ROUNDING` | A running total stored into a `Double` field: the model adds exact decimals and converts once per row |
| 7 | `ORDERING` | A Record ID's value is defined only relative to an explicit `ORDER BY` |
| 8 | `NULL_SEMANTICS` | A NULL date makes the comparison NULL, and a NULL condition takes the else branch |
| 9 | `LOGIC` | `Count` counts rows where `CountNonNull` counts values |
| 9 | `ORDERING` | `Last` is the group's last row in incoming order, which is why 9 cannot be split from 4–8 |
| 10 | `NULL_SEMANTICS` | Columns are matched to the target by name; a column the stream does not carry is left alone on an update and arrives NULL on an insert |
| 10 | `LOGIC` | PreSQL and PostSQL are part of the write, and the golden output is the target's state after all three steps |
