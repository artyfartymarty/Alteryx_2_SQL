# wf_0003 — GL period close

`source/gl_period_close.yxmd`, AMP engine, 12 nodes: 10 tools plus Tool Container 100 "Extract"
(holding 1–3) and Tool Container 200 "Publish" (holding 9–10). Workflow constants
`User.Region` = `EMEA` and `User.PeriodEnd` = `2026-08-31`.

**This file was written by hand to `docs/reference/dag-contract.md`. It has never been produced
or opened by Alteryx.** The ODBC string carries deliberately fake placeholders
(`UID=svc_alx;PWD=__EncPwd2__`) so the parser's scrubber has something to remove; the SQL sits in
CDATA after the `|||` separator because it contains a `<`.

## Segmentation this sample is shaped for

With `min_tools: 3`, the container cuts give 1–3, 4–8 and 9–10. Ordering protection walks upstream
from the `Last RUN_BAL` in tool 9 through 8, 7, 6 and 5 until it meets the sort at 4, so 9 joins
the 4–8 chain; the lone tool 10 is then below `min_tools` and merges across the soft cut. The
result is `seg_01` = 1–3 and `seg_02` = 4–10.

## What each tool is here to exercise

| Tool | Behaviour under test |
|------|----------------------|
| 1 input | a DB source: alias `prod_fin`, credentials to scrub, and an embedded query kept verbatim |
| 2 filter | a comparison against a **workflow constant**; a NULL `REGION` is neither true nor false and is dropped (the `False` anchor is not wired) |
| 3 datetime | `%d/%m/%Y` text → `DateTime`; an impossible date becomes NULL |
| 4 sort → 5 unique | which duplicate survives depends entirely on the sort, including `ENTRY_ID` descending |
| 6 multi-row formula | a running balance per `ACCT` where "rows that don't exist" are 0, and each row sees the value just computed |
| 7 record id | an order-dependent column, first position |
| 8 formula | `DateTimeFormat` compared with the `User.PeriodEnd` constant |
| 9 summarize | `Last` depends on incoming order — the reason 9 may not be split from 4–8 |
| 10 output | `Update; Insert if new` on keys `ACCT, PERIOD`, with PreSQL and PostSQL |

## A constraint on every set

The input tool's own SQL ends in `WHERE AMOUNT <> 0`, and the translated procedure re-applies that
predicate, so **no set may contain a row whose `AMOUNT` is 0 or NULL** — the simulator does not
run the query, and such a row would make the two sides disagree for a reason that is not a bug.
That is why `AMOUNT` is the one column with no NULL in the `edge` set.

## `normal` (14 rows)

| `ENTRY_ID` | Why it is there |
|---|---|
| 1, 2 | duplicate `(ACCT, POSTED)` = `(4000, 03/08/2026)` with different `ENTRY_ID`; the descending `ENTRY_ID` sort means **2 survives to `Unique` and 1 goes to `Duplicates`** |
| 11, 12 | the same again on `7000`; 12 survives, 11 is the duplicate |
| 6 | `POSTED` `31/02/2026` is unparseable → `POSTED_DT` NULL, which sorts first and makes `PERIOD_END_FLAG` `N` |
| 8 | leap day `29/02/2024` |
| 10 | `REGION` is NULL → the filter expression is NULL → the row is dropped |
| 9 | `REGION` `AMER` → dropped by the region filter |
| 3, 5 | posted on `31/08/2026`, so `PERIOD_END_FLAG` is `Y` and `HAS_PERIOD_END` becomes `Y` for their groups |
| 4 | a negative amount, so the running balance goes down before it goes up |
| 7 | `5000`'s only `2026-07` row, and it sits **between** two `2026-08` rows in `RUN_BAL` order — so the `Last RUN_BAL` of `(5000, 2026-08)` is not simply the group's largest balance |
| 13 | a second period on `7000`, whose `RUN_BAL` carries over from the previous period |
| 14 | a fifth `(4000, 2026-08)` row, posted `01/09/2026` so it sorts after entries 1–4 in `RUN_BAL` order, and negative enough (`-400.00`) to pull the group's running balance back down below the peak entry 3 set. This is the row that separates Summarize's `Last` (`MAX_BY(RUN_BAL, RECORD_ID)`, the group's actual last row) from a plain `MAX(RUN_BAL)`: without it, every group's running balance happened to peak on its own last row, so the two gave the same `CLOSING_BAL` and a `Last`-as-`MAX` mistake passed every golden set undetected (fix round 1; see `broken_sql/seg_02/04_last_translated_as_max.sql`) |

After the filter, sort and unique, ten rows reach the summarize and produce six
`(ACCT, PERIOD)` groups.

## `targets_before/<set>/GL_SUMMARY.csv` (3 rows)

| Row | Why it is there |
|---|---|
| `4000` / `2026-08` | a group the run also produces → **updated** in place |
| `9999` / `2026-08` | no matching group → **untouched**; its `LOADED_FLAG` is `N`, so the PostSQL (which only fills NULLs) must leave it alone |
| `4000` / `2019-12` | `PERIOD < '2020-01'` → **deleted by the PreSQL** before anything is written |

Rows the run inserts have no `LOADED_FLAG` of their own, so they arrive NULL and the PostSQL sets
them to `Y`. The `empty` set's target file is empty, so an empty run leaves an empty table.

## `period_end` (8 rows)

Everything is posted on a month or quarter end (2026-03-31, 2026-06-30, 2026-07-31, 2026-08-31),
so `PERIOD_END_FLAG` is `Y` almost everywhere. `ENTRY_ID` 27 and 28 are the duplicate pair; 26 is
the `AMER` row the filter drops.

## `empty`

Header row, zero data rows.

## `edge` (8 rows)

| `ENTRY_ID` | Why it is there |
|---|---|
| NULL (row 1) | NULL `ACCT`, `PERIOD`, `POSTED` and `ENTRY_ID` in one row that still passes the `EMEA` filter, so the NULLs reach the group-by, the sort and the record id |
| 31 | non-ASCII `ACCT` that is also exactly 20 characters, on the leap day |
| 32 | `ACCT` at the column maximum of 20 characters |
| 33 (twice) | exact duplicate rows |
| 34 | `-0.01`, the smallest negative the scale allows |
| 35 | `REGION` at its 10-character maximum, and not `EMEA`, so it is filtered out |
| 36 | NULL `REGION` → filtered out |
