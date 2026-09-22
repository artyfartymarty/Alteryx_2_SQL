# wf_0002 — customer orders

`source/customer_orders.yxmd`, E1 engine, 15 nodes: 11 tools, three Tool Containers
(100 "Prep customers" holds 1–2; 200 "Prep orders" holds container 210, which holds 3–4) and
TextBox 300. Join (5) fans out to three formulas (6 `MATCHED`, 7 `NO_ORDERS`, 8 `ORPHAN`) which a
Union (9) stacks by name before the database output (10) and a Browse (11).

**This file was written by hand to `docs/reference/dag-contract.md`. It has never been produced
or opened by Alteryx.** The connection string carries deliberately fake placeholders
(`UID=etl_user;PWD=__EncPwd1__`) so the parser's scrubber has something to remove.

## Segmentation this sample is shaped for

`sample.json` sets `min_tools: 2` here, where the other four samples use 3. The container
boundaries are soft cuts, so the groups start as {1, 2}, {3, 4} and {5…11} — and at `min_tools: 3`
both two-tool prep groups would fall under the floor and merge into the main group, collapsing the
workflow to a single segment and leaving nothing to demonstrate. At 2 they stand, which is the
point of this sample: two prep segments in a parallel wave, then the join's segment. Tool 11 is a
Browse, so it rides with its upstream group and never counts toward a size.

## What each tool is here to exercise

| Tool | Behaviour under test |
|------|----------------------|
| containers 100 / 200 / 210 | nested `<ChildNodes>`, and the soft segment cuts that container boundaries create |
| 2 data cleansing | no `Plugin` attribute — classified by `<EngineSettings Macro="Cleanse.yxmc">`; NULL strings → blank, trim, upper case, on `NAME` and `CITY` only |
| 3 input | CSV format, so **every column arrives as `V_String(254)`** — the golden data for this tool is all text, including numbers and dates |
| 4 select | the string→`Int32`/`Double`/`Date` conversion; bad text becomes NULL |
| 5 join | `L`, `J` and `R` all used; the right side's `CUST_ID` collides and becomes `Right_CUST_ID`, which the `J` select then drops |
| 9 union | by name, three inputs in `#1`, `#2`, `#3` order, each with a different field set |
| 10 output | `Append Existing` against `dbo.CUSTOMER_ORDER_FACT`, so `targets_before/` matters |

## `normal` — customers (tool 1, 6 rows)

| `CUST_ID` | Why it is there |
|---|---|
| 1 | `NAME` `"  aCme corporation  "` — surrounding spaces **and** mixed case, so trim and upper case are both visible |
| 4 | no orders at all → the join's `L` output → tool 7 `NO_ORDERS` |
| 5 | `NAME` is NULL → cleansing turns it into the empty string |
| 6 | `CITY` `"  adelaide  "` — the second cleansed field |
| 2, 3 | ordinary matched customers |

## `normal` — orders export (tool 3, 9 rows, all text)

| `ORDER_ID` | Why it is there |
|---|---|
| 1001, 1002 | duplicate `CUST_ID` on the order side → one customer fans out to two joined rows |
| 1004 | `AMOUNT` `abc` → `Double` conversion gives NULL |
| 1005 | `ORDER_DATE` `2026-02-30` → an invalid date → `Date` conversion gives NULL |
| 1006 | `CUST_ID` 99, an unknown customer → the join's `R` output → tool 8 `ORPHAN` |
| 1008 | `AMOUNT` is the empty string, which is not the same as NULL on the way in |
| 1003, 1007, 1009 | ordinary matches, including customer 5 whose name cleansed to blank |

That gives 8 rows on `J`, 1 on `L` and 1 on `R`, so the union emits 10 rows and the append leaves
12 rows in the target.

## `targets_before/<set>/CUSTOMER_ORDER_FACT.csv`

Two pre-existing rows from an earlier run: one `MATCHED` row with every column filled, and one
`ORPHAN` row whose `NAME`, `CITY` and `TIER` are NULL — which is what the `ORPHAN` branch really
writes, since those columns do not exist on that stream. The `empty` set's target file is empty
too, so an empty run leaves an empty table.

## `period_end`

The same six customers (a dimension does not change), with orders dated on month and quarter ends
(2026-03-31, 2026-06-30, 2026-07-31, 2026-08-31, 2026-09-30). Order 2005 keeps an orphan in the set.

## `empty`

Header rows, zero data rows, for both inputs and for the target.

## `edge`

Customers: an all-NULL row; `CUST_ID` 10 with non-ASCII `NAME` and `CITY`; `CUST_ID` 11 with
`NAME` at exactly 60 characters, `CITY` at 40 and `TIER` at 10; `CUST_ID` 12 twice, an exact
duplicate that makes the join fan out on both sides.

Orders: an all-NULL row; `-0.0` and `0.005` amounts; a leap day `2024-02-29`; `3003` twice as an
exact duplicate; `3004` with an `ORDER_DATE` of 254 characters (the column's maximum) that cannot
parse; `3005` with both a non-numeric amount and a non-ISO date.

## Hand migration

`canned/` holds the artifacts the orchestrator's mock runner replays instead of calling an agent:
`intake/plan.md`, `analysis.md`, `unsupported.json`, `docs/migration.md`, and per segment
`segments/<seg>/{contract.json,proc.sql,translation_notes.md,review.json}`. `broken_sql/` holds
copies of a procedure with one deliberate translator mistake each, and `broken_sql/broken.json`
records which diff class `scripts/compare.py` reports for each of them. `tests/test_e2e_parity.py`
runs both; `tests/test_canned_artifacts.py` checks their shape. **Nothing here has run on
Snowflake or on Alteryx.**
