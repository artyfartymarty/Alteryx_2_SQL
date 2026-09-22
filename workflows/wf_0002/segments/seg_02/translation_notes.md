# wf_0002 / seg_02 — translation notes

One assumption per line. Nothing below has been checked against a real Alteryx engine or a real
Snowflake account; every claim is against `docs/reference/simulator-semantics.md` (the parity
oracle), `docs/reference/dag-contract.md` §4 and program spec §8.

- The segment is Tool Container 210 ("Type the CSV export") inside container 200, tools 3 and 4; neither container carries data, so neither gets a CTE.
- Its one outbound stream, `4_Output`, feeds the Join's Right anchor in seg_03, so it is materialised as the primary work table `MIG_WORK.WF0002_SEG_02_OUT` (contract C3) with `CREATE OR REPLACE TRANSIENT TABLE`.
- Tool 3 reads `ORDERS_EXPORT`, the logical name `intake/mappings.yaml` records for `crm/orders_export.csv`; the procedure never names the mapped Snowflake table.
- Tool 3 is a CSV input with `FieldLen` 254, so every column arrives as `V_String(254)` and the source table is entirely text — including the ones whose names read like numbers and dates.
- Tool 4 selects all four fields and leaves `*Unknown` unselected, so the output is exactly those four in configuration order; there is nothing to append.
- Every retype is a `TRY_` form, because Alteryx's coercion warns and nulls rather than failing: `abc` and the empty string give a NULL `AMOUNT`, and `2026-02-30` — a date that does not exist — gives a NULL `ORDER_DATE`.
- `ORDER_ID` and `CUST_ID` are `TRY_TO_NUMBER(x, 38, 0)`: Alteryx's integer coercion rounds half away from zero, which this form also does, though the sample data is whole numbers and so does not prove it.
- `ORDER_DATE` is `TRY_TO_DATE(x, 'YYYY-MM-DD')` with the format spelled out; the local runtime translates it to a plain `TRY_CAST(... AS DATE)` and ignores the format, which agrees for the ISO text in these golden sets but would not for a text in another layout that ISO parsing happens to accept.
- `AMOUNT` is `TRY_TO_DOUBLE`, which keeps `-0.0` as a negative zero and `0.005` as itself, both of which the `edge` golden set checks.
- Assumption, unproven by this sample's data: `TRY_TO_DOUBLE` accepts `inf` and `nan` where the oracle would give NULL; no golden set contains either.
- `contract.output.keys` is `[]`: the `edge` golden set holds `ORDER_ID` 3003 twice as byte-identical rows, so no column set identifies a row and the comparison falls back to a row multiset.
- `RUN_ID` is unused: contract C4 fixes the parameter list, and this segment has no query tag, no audit row and no pre/post SQL to put it in.
