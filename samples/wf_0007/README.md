# wf_0007 — regional targets and attainment (the dbt sample)

`source/regional_targets.yxmd`, E1 engine, 9 nodes: 7 tools plus Tool Container 10 "Pair plan
targets with actuals" (holding 1–3) and Tool Container 11 "Current-year attainment" (holding 4–7).
Workflow constant `User.CurrentYear` = `2026` (`IsNumeric False`).

**This file was written by hand to `docs/reference/dag-contract.md`. It has never been produced
or opened by Alteryx.** The ODBC string on tool 7 carries deliberately fake placeholders
(`UID=svc_alx;PWD=__EncPwd2__`) so the parser's scrubber has something to remove.

## Why this sample exists

It is the only sample migrated as a **dbt project** rather than as procedures (output-targets
design §7.3). `sample.json` sets `"output_target": "dbt"`, `build_samples.seed` copies it into the
manifest, and `scripts/target_check.py --prefer auto` grants `output_kind: dbt` because both
segments are `sql`, neither output has PreSQL or PostSQL, and both write modes (`overwrite`,
`merge` with keys) have a dbt materialisation. `canned/dbt/**` is the project a correct translator
writes; `canned/review.json` is the reviewer's verdict on it; the two canned contracts are the
analyzer's. There is no `proc.sql` anywhere.

## The tool chain

| Tool | Behaviour under test |
|------|----------------------|
| 1 input | plan targets, `REGION`, `PERIOD`, `TARGET` |
| 2 input | booked actuals, one row per sales line, `REGION`, `PERIOD`, `ACTUAL` |
| 3 join | inner join on **two** keys (`REGION`, `PERIOD`); a NULL key matches nothing; a key with several actual lines fans the target out; `Right_REGION`, `Right_PERIOD` deselected |
| 4 filter | `Left([PERIOD], 4) = [User.CurrentYear]` — a text constant, compared as text |
| 5 summarize | group by `REGION`, `PERIOD`; `Sum` of both amounts (FixedDecimal stays FixedDecimal), `Count` → `LINES` (rows) |
| 6 output | `region_attainment.yxdb`, `Overwrite` → a `table` model |
| 7 output | ODBC `dbo.ATTAINMENT_HISTORY`, `Update; Insert if new` on `REGION, PERIOD` → an incremental `merge` model, with prior state in `targets_before` |

## Segmentation this sample is shaped for

With `min_tools: 2`, the container cuts give `seg_01` = 1–3 (the Join stays with both of its
inputs, and its `J` stream `3_J` becomes the work table `MIG_WORK.WF0007_SEG_01_OUT`) and
`seg_02` = 4–7. Keeping the containers apart is what puts the year filter in the target models,
where the first broken variant needs it. Tool 5's one output feeds both Output tools, so `seg_02`
has two targets on the stream `5_Output`.

## `normal`

`golden_inputs/normal/1.csv` (targets, 6 rows):

| Row | Why it is there |
|---|---|
| `EAST` `2026-01` `100.00` | joins **two** actual lines, so the fan-out sends it to the Summarize twice (`TARGET_TOTAL` 200.00, `LINES` 2) |
| `EAST` `2026-02` `120.00` | a second period of a region the history already holds — the row a merge keyed on `REGION` alone never inserts |
| `WEST` `2026-01` `80.00` | its only actual is NULL, so `ACTUAL_TOTAL` is NULL, not 0 |
| `WEST` `2025-12` `75.00` | prior year: joined, then dropped by the filter |
| `NORTH` `2026-01` `50.00` | no actual: the inner join drops it |
| NULL `2026-01` `10.00` | a NULL key matches nothing |

`golden_inputs/normal/2.csv` (actuals, 6 rows): `EAST 2026-01` twice (`90.00`, `15.00` — the
fan-out), `EAST 2026-02`, `WEST 2026-01` with a NULL `ACTUAL`, `WEST 2025-12`, and `SOUTH 2026-01`,
which has no target and is dropped by the join.

`golden_inputs/targets_before/normal/ATTAINMENT_HISTORY.csv` (3 rows, typed exactly like tool 5's
output):

| Row | Why it is there |
|---|---|
| `EAST` / `2026-01` | a key the run also produces → **updated** |
| `WEST` / `2025-12` | prior year, filtered out of the run → **kept** untouched; a merge keyed on `REGION` alone overwrites it with `WEST 2026-01` |
| `NORTH` / `2025-11` | a key the run never produces → **kept** |

## `period_end`

The year boundary: `EAST` and `WEST` each have a `2025-12` and a `2026-01` target with one actual
line apiece, so only the two January keys survive the filter. `targets_before` holds `WEST 2026-01`
(updated) and `EAST 2025-12` (kept as it was: the run filters December out, so the history row is
never touched, although the run's own December figures differ from it).

## `empty`

Header rows only, for both inputs and for `targets_before`, so an empty run leaves both targets
empty.

## `edge`

| Row | Why it is there |
|---|---|
| `DUP` `2026-03` `10.00`, twice | a byte-identical duplicate target: both copies join the one actual line, so `TARGET_TOTAL` is 20.00 and `LINES` 2 |
| `NORDÖST` `2026-02` | non-ASCII letters in a join and group key |
| `NEG` `2026-04` | a negative `ACTUAL` (`-3.25`) |
| `BAD` `2026-1` | a malformed `PERIOD` whose first four characters are `2026`, so the filter keeps it; the golden data records it as a group of its own |

`targets_before/edge` holds `DUP 2026-03` (updated) and `OLD 2025-06` (kept).

## Broken variants

`broken_sql/dbt/models/region_attainment.sql` drops the year filter and
`broken_sql/dbt/models/attainment_history.sql` narrows the merge key to `['REGION']`; each is the
canned model with that one mistake, and `broken_sql/broken.json` records the diff class
`validate_dbt.py` actually reported for it on the `normal` set.
