# wf_0001 — sales summary

`source/sales_summary.yxmd`, `yxmdVer 2023.1`, AMP engine, 8 nodes, no containers.
Input (tool 1) → select (2) → filter (3) → formula (4) → summarize (5) → sort (6) → yxdb output (7);
the filter's `False` branch goes straight to the CSV output (8).

**This file was written by hand to `docs/reference/dag-contract.md`. It has never been produced
or opened by Alteryx.** Only inputs live under `golden_inputs/`; expected outputs come later from
`scripts/dev/alteryx_sim.py`.

## What each tool is here to exercise

| Tool | Behaviour under test |
|------|----------------------|
| 2 select | `String(10)` truncation of `CUSTOMER`, rename `STATUS` → `ORDER_STATUS`, and `*Unknown` selected — only two fields are listed, so the other five are appended in incoming order and the output order becomes `CUSTOMER, ORDER_STATUS, ORDER_ID, REGION, AMOUNT_TXT, QTY, ORDER_DATE` |
| 3 filter | `[REGION] != "WEST"`; a NULL `REGION` makes the expression NULL, which must go to `False` |
| 4 formula | three formulas in order, the second reading the first's `AMOUNT`: `ToNumber`, `Round(…, 0.01)` half-away-from-zero, and an `IF/ELSEIF/ELSE` band |
| 5 summarize | `Count` vs `CountNonNull` differ whenever `AMOUNT` is NULL; `Max` over a Date |
| 6 sort | two keys, one descending |

## `normal` (20 rows)

| Row (`ORDER_ID`) | Why it is there |
|---|---|
| 5 | `REGION` is NULL → filter expression is NULL → **must reach tool 8** |
| 4, 12, 20 | `REGION` is `WEST` → filter `False` → tool 8 |
| 6 | `AMOUNT_TXT` `abc` → `ToNumber` gives NULL |
| 7 | `AMOUNT_TXT` `1,200.50` → NULL as well: a thousands separator is not a plain decimal |
| 18 | `AMOUNT_TXT` is the empty string (not NULL) → `ToNumber` gives NULL |
| 1, 3, 5, 7, 9, 11, 15, 17, 19 | `CUSTOMER` longer than 10 characters → truncated by tool 2 |
| 3, 16 | `QTY` is exactly 10 → the `>= 10` branch of `IIF` (0.9) |
| 13 | `QTY` is NULL → the condition is NULL → `IIF` takes the false branch (×1) |
| 2 | `250.005 × 1` → `NET` lands on a half cent → 250.01 |
| 3 | `33.35 × 0.9 = 30.015` → half cent → 30.02 |
| 15 | `2500.75 × 0.9 = 2250.675` → half cent → 2250.68 |
| 8, 9, 10 | `SIZE_BAND` boundaries: 99.99 `SMALL`, 100.00 `MEDIUM`, 1000.00 `LARGE` |
| 14 | negative `AMOUNT`, so a group's `Sum` can shrink |
| 6, 7, 18 | NULL `AMOUNT` rows still count in `ORDERS` but not in `PRICED_ORDERS`, and fall to `SMALL` because a NULL comparison is not true |

## `period_end` (10 rows)

Every `ORDER_DATE` is a month or quarter end (2026-03-31, 2026-06-30, 2026-07-31, 2026-08-31,
2026-09-30, 2026-12-31) so `LAST_ORDER` (`Max ORDER_DATE`) is itself a period end. Row 107 carries
`2.675`, the classic binary-float rounding case; row 105 keeps a NULL `REGION` in the set and 109 a
non-numeric amount.

## `empty`

Header row, zero data rows, same schema.

## `edge` (8 rows)

| Row | Why it is there |
|---|---|
| 1 (all `\N`) | NULL in every column at once |
| `ORDER_ID` 101 | non-ASCII `CUSTOMER` (Latin-1, Latin Extended and CJK), leap day `2024-02-29`, `AMOUNT_TXT` `0.005` → rounds up to 0.01 |
| 102 | `CUSTOMER` at exactly 50 characters, `REGION` at 20, `STATUS` at 10; `-0.0` with `QTY` 10, so `NET` keeps a negative zero |
| 103 (twice) | exact duplicate rows |
| 104 | `2.675` → 2.68 under half-away-from-zero, 2.67 under banker's rounding |
| 105 | `AMOUNT` exactly 1000.00, the `LARGE` boundary, with `QTY` exactly 10 |
| 106 | `0.004` → rounds down to 0.00 |

## Hand migration

`canned/` holds the artifacts the orchestrator's mock runner replays instead of calling an agent:
`intake/plan.md`, `analysis.md`, `unsupported.json`, `docs/migration.md`, and per segment
`segments/seg_01/{contract.json,proc.sql,translation_notes.md,review.json}`. `broken_sql/` holds
copies of the procedure with one deliberate translator mistake each, and `broken_sql/broken.json`
records which diff class `scripts/compare.py` reports for each of them. `tests/test_e2e_parity.py`
runs both; `tests/test_canned_artifacts.py` checks their shape. **Nothing here has run on
Snowflake or on Alteryx.**
