# wf_0003 — intake plan

GL period close · owner `wf_owner` · schedule `0 6 * * 1-5` ·
source `gl_period_close.yxmd`, `yxmdVer 2023.1`, AMP engine.

This plan restates `parsed/dag.json`, `intake/touchpoints.json` and `mappings/global.yaml`.
Nothing here has run on Alteryx or on Snowflake, and no count, diff or tolerance in this project
comes from an agent — those come only from `scripts/`.

## Tier

**T1 — pure SQL.** Every tool in the workflow is one the cookbook has a SQL pattern for: Input
Data, Filter, DateTime, Sort, Unique, Multi-Row Formula, Record ID, Formula, Summarize and one
Output Data tool. Nothing needs Snowpark (no Python, R, Download, Run Command or spatial tool) and
nothing needs a human rewrite (no unknown plugin, no macro, no dynamic rename or dynamic input).
The two Tool Containers carry no data; they are layout.

## Touchpoints and how they resolve

| Tool | Touchpoint | Resolution |
|------|-----------|------------|
| 1 | ODBC alias `prod_fin` (key `alias:prod_fin`), embedded query against `dbo.GL_LEDGER` | `FINANCE.RAW.GL_LEDGER`, logical `GL_LEDGER` — confirmed by the owner |
| 10 | ODBC alias `prod_fin`, table `dbo.GL_SUMMARY` (key `alias:prod_fin/dbo.gl_summary`) | `ANALYTICS.CURATED.GL_SUMMARY`, logical `GL_SUMMARY`, write mode `merge` on keys `ACCT, PERIOD` — confirmed |

The connection string carried a user id and a password. Both are removed by the parser's scrubber
before anything is written under `workflows/`, and the alias `prod_fin` is all that survives into
`parsed/dag.json`, `raw_config` included.

Two workflow constants have to be resolved at translation time, because Snowflake has no
equivalent of an Alteryx constant: `User.Region` = `EMEA` (tool 2's filter) and
`User.PeriodEnd` = `2026-08-31` (tool 8's formula). Both are recorded under `constants:` in
`intake/mappings.yaml`. There are no app parameters and no macros.

Tool 10 is a **database** output, which brings three things a file output does not have: a write
mode of `Update; Insert if new` with update keys `ACCT, PERIOD`, a PreSQL, and a PostSQL. All
three are part of the write and all three have to be translated; the target also has a prior
state, so `golden/targets_before/<set>/GL_SUMMARY.csv` is what the run merges into.

Program-level answers all come from `mappings/global.yaml` and none had to be asked again: target
`ANALYTICS.CURATED`, work schema `MIG_WORK`, warehouse `MIG_WH`, owning role `MIGRATION_ROLE`,
`EXECUTE AS CALLER`, column-name policy `sanitize`, session `TIMEZONE America/New_York` and
`WEEK_START 1`, accepted diff classes `ROUNDING` and `ORDERING`.

## Expected segment cuts

Two segments in two waves: `seg_01` holding tools **1, 2, 3** and `seg_02` holding tools
**4, 5, 6, 7, 8, 9, 10**.

`min_tools` is 3 and `max_tools` 40. The two Tool Containers propose cuts at 1–3 ("Extract"),
4–8 and 9–10 ("Publish"). The Summarize's `Last RUN_BAL` is order-dependent, so protection walks
upstream from tool 9 through 8, 7, 6 and 5 until it meets the Sort at tool 4, which pulls 9 into
the 4–8 chain; tool 10 alone is then under the floor and merges across the soft cut. `seg_01`
hands `seg_02` one work table.

## Unsupported or manual tools

None. `unsupported.json` is `{"tier": "T1", "unsupported": [], "unknown": []}`.

## Parity risks by tool id

| Tool | Risk | Why it matters |
|------|------|----------------|
| 1 | The Input tool's own `WHERE AMOUNT <> 0` | The procedure reads the mapped table, not the query, so the predicate has to be written out again or rows Alteryx never saw reach the segment |
| 2 | Filter against a **workflow constant**, and NULL is not false | `[REGION] = [User.Region]` is NULL for a NULL region; Alteryx sends those rows to the unwired False anchor, which a three-valued `WHERE` also does |
| 3 | `%d/%m/%Y` text to `DateTime` | Day-first, not month-first, and a date that does not exist must become NULL rather than raise |
| 4, 5 | Which duplicate survives depends entirely on the Sort | Unique keeps the first row in incoming order, and incoming order is `ENTRY_ID` **descending** |
| 6 | Multi-Row Formula with "rows that don't exist" worth 0 | A running total per `ACCT` in incoming order, stored into a `Double` field |
| 7 | Record ID is order-dependent | Its value is meaningless without an explicit `ORDER BY`, and tool 9's `Last` is defined by it |
| 8 | `DateTimeFormat` compared with a constant, on a nullable date | A NULL date makes the comparison NULL, and a NULL condition takes the else branch |
| 9 | `Last` depends on incoming order; `Count` counts rows | This is the reason tool 9 may not be split away from 4–8 |
| 10 | `Update; Insert if new`, PreSQL, PostSQL, columns matched by name | The golden output is the target's state **after** all three, including rows the run never touched |

## Fix-loop budget

Three iterations. The workflow is small, but it concentrates the two things a first pass most
often gets wrong: the order-dependent chain (Sort → Unique → Multi-Row Formula → Record ID →
`Last`), where every tool has to re-state the same `ORDER BY`, and the database Output tool, whose
PreSQL and PostSQL are easy to read as connection housekeeping rather than as part of the write.

## Dependencies, owner and consumers

No shared macros and no upstream workflow: tool 1 reads a GL extract straight from the
`prod_fin` connection. `manifest.json` records no consumers for the output. Owner `wf_owner`; the
schedule `0 6 * * 1-5` is the Alteryx Server schedule recorded at parse time.
