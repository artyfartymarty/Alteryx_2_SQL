# wf_0001 / seg_01 — translation notes

One assumption per line. Nothing below has been checked against a real Alteryx engine or a real
Snowflake account; every claim is against `docs/reference/simulator-semantics.md` (the parity
oracle) and program spec §8.

- The segment holds all eight tools, so it has no outbound stream and materialises no `_OUT` table; both Output tools are final targets written through `IDENTIFIER(:SALES_SUMMARY_TGT)` and `IDENTIFIER(:EXCLUDED_ORDERS_TGT)`, whose names the body's `LET`s build from `TGT_DB`, `TGT_SCHEMA` (named without a colon inside a LET) and the logical name -- Snowflake's documented `IDENTIFIER` form, one value and never an expression (not yet run on a real account; the first real-account run confirms it).
- Tools 1–3 appear as CTEs in both statements, because two Output tools terminate two branches of the same chain and contract C4 allows no variable to hold a shared result (its only `LET`s build table names); no work table is materialised for the shared prefix, and a reviewer should read the duplication as the documented merge, not as two different translations.
- Tool 3 has one CTE per anchor, `t3_filter_t` and `t3_filter_f`, because the Filter's two outputs feed different branches; the `t<id>_<type>` name carries the Alteryx anchor letter as a suffix.
- Tool 1 reads `ORDERS`, the logical name `intake/mappings.yaml` records for `sales/orders.yxdb`; the procedure never names the mapped Snowflake table.
- Tool 2's `String(10)` retype of `CUSTOMER` is `LEFT(CUSTOMER, 10)`: Alteryx truncates silently where a Snowflake `VARCHAR(10)` would raise, and `LEFT` counts characters, so the non-ASCII customer in the `edge` set truncates by character and not by byte.
- Tool 2's `*Unknown` selection appends the five unlisted fields in incoming order, which fixes the output order as `CUSTOMER, ORDER_STATUS, ORDER_ID, REGION, AMOUNT_TXT, QTY, ORDER_DATE`.
- Tool 3's True branch is `WHERE REGION <> 'WEST'`: SQL's own three-valued `WHERE` already drops the rows whose expression is NULL, which is what Alteryx does with them.
- Tool 3's False branch is `WHERE NOT (REGION <> 'WEST') OR (REGION) IS NULL`, because Alteryx sends both false AND NULL evaluations to the `F` anchor; the `IS NULL` half is the whole point and a plain `WHERE REGION = 'WEST'` would lose those rows.
- Tool 4's three formulas run in order and the later two read the `AMOUNT` the first wrote, so `AMOUNT` is computed in a nested `SELECT` and `NET`/`SIZE_BAND` read it from there rather than repeating the expression.
- `ToNumber([AMOUNT_TXT])` is `TRY_TO_DOUBLE`, the warn-and-null form: `abc`, `1,200.50` and the empty string all give NULL on this runtime, which is what the oracle says Alteryx does with text that is not a plain decimal.
- Assumption, unproven by this sample's data: `TRY_TO_DOUBLE` accepts `inf` and `nan` where the oracle would give NULL; no golden set contains either, so the divergence is recorded rather than guarded against.
- `AMOUNT` is a `Double` field, so the converted value is stored as a `FLOAT` before anything else reads it — the oracle converts an exact decimal to the nearest binary double once, at the field boundary.
- `NET`'s arithmetic is done in `NUMBER(38,10)` and only the rounded result is cast back to `FLOAT`: the oracle computes formula arithmetic in exact decimals, and `ROUND` on a `FLOAT` drifts from that (`ROUND(1.005::FLOAT, 2)` is `1.00` on this runtime where the exact form is `1.01`).
- Needs verification on Snowflake: the local runtime types `NUMBER(38,10) * NUMBER(38,10)` as scale 20, but Snowflake's documented rule for multiplication is scale `min(S1 + S2, max(S1, S2, 12))`, so the same product carries scale 12 there. That is enough here (an amount with at most three decimals times a one-decimal factor), but a product that needs more than twelve decimals before `ROUND` would differ; cast operands to the scale they really have so that `S1 + S2 <= 12`.
- `CAST(AMOUNT AS NUMBER(38,10))` is assumed to reproduce the oracle's `Decimal(repr(x))` step: it does for every amount in the four golden sets, because each has at most three decimal places, but a `Double` whose shortest representation needs more than ten decimals would round here and not there.
- `Round(x, 0.01)` is `ROUND(<number expression>, 2)`, which is half away from zero on this runtime — `2.675` gives `2.68`, `-2.675` gives `-2.68` — matching the oracle's rule.
- `IIF([QTY] >= 10, 0.9, 1)` is `IFF(QTY >= 10, …)`: a NULL `QTY` makes the condition NULL, and both Alteryx's `IIF` and SQL's `CASE` take the else branch, so the factor is 1.
- The `IF/ELSEIF/ELSE` band is a `CASE`: a NULL `AMOUNT` makes both comparisons NULL, so those rows fall through to `SMALL`, which is what the oracle does with a NULL condition.
- Tool 5's `Count` is `COUNT(*)` and its `CountNonNull` is `COUNT(AMOUNT)`: Alteryx's `Count` counts rows, not values.
- Tool 5's `Sum` is taken over `CAST(NET AS NUMBER(38,10))` and cast back to `FLOAT`, so the total is the exact sum of the stored cent values rather than a binary accumulation; `SUM` over an all-NULL group is NULL, as it is in the oracle.
- Tool 6's `ORDER BY` spells out `DESC NULLS LAST` and `ASC NULLS FIRST`, the oracle's rule (NULL first ascending, last descending); row order is not part of parity for this contract, since `ordering.order_dependent_columns` is empty.
- `contract.outputs[1].keys` is `[]` for the tool 8 target: the `edge` golden set holds two byte-identical rows (`ORDER_ID` 103), so no column set identifies a row and the comparison falls back to a row multiset, which is exact about whether the rows match and attributes a difference to a column only by nearest-match pairing, a heuristic.
- `REGION` and `SIZE_BAND` are declared `NOT NULL` on the tool 7 target because the Filter removes every NULL-region row before the Summarize; that is a real check, and the broken variant `04_filter_true_keeps_null_region.sql` trips it.
- `RUN_ID` is unused: contract C4 fixes the parameter list, and this segment has no query tag, no audit row and no pre/post SQL to put it in.

## Nodes with no CTE

- tool 7 has no CTE: an Output Data tool is a write, not a computation — it is the `CREATE OR REPLACE TABLE … AS` statement whose `SELECT` ends at `t6_sort`.
- tool 8 has no CTE: same reason — it is the `CREATE OR REPLACE TABLE … AS` statement whose `SELECT` ends at `t3_filter_f`.
