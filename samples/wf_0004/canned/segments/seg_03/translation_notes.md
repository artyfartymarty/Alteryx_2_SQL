# wf_0004 / seg_03 — translation notes

One assumption per line. Nothing below has been checked against a real Alteryx engine or a real
Snowflake account; every claim is against `docs/reference/simulator-semantics.md` (the parity
oracle) and program spec §8.

- The segment holds tools 3–7 and reads `MIG_WORK.WF0004_SEG_02_OUT`, the macro segment's work table, written as a literal `MIG_WORK.…` name because contract C4 reserves `IDENTIFIER(…)` for mapped sources and targets.
- Tools 3 and 4 appear as CTEs in both statements, because two Output tools terminate two branches of the same chain (tool 6 off the Cross Tab, tool 7 off the Transpose below it) and contract C4 allows no variable to hold a shared result (its only `LET`s build table names); no work table is materialised for the shared prefix, and a reviewer should read the duplication as the documented merge, not as two different translations.
- Tool 3's RegEx is method `Parse`, so it appends `FAMILY` and `ITEM_NO` from the two capture groups and never drops a row: a `SKU` the pattern does not match keeps its row with both fields NULL.
- Local-runtime fact that forced a spelling: on Snowflake `REGEXP_SUBSTR` returns NULL when nothing matched, but the local DuckDB double returns an **empty string**. The match is therefore tested explicitly with `REGEXP_LIKE` and the NULL written out, which is correct on both. `NULLIF(…, '')` would have worked on this data but would be wrong for a pattern whose group can legitimately capture an empty string.
- `REGEXP_LIKE` on Snowflake requires the pattern to match the **whole** subject, where the RegEx tool searches. The pattern is anchored `^…$`, so the two rules coincide here; an unanchored pattern would need `REGEXP_INSTR(…) > 0` instead.
- `ITEM_NO` is computed and then dropped by the Cross Tab. It is kept in `t3_regex` rather than left out, because the tool really does produce it and a reader comparing the CTE with `parsed/dag.json` should see the same field list.
- Tool 3's pattern is case-**sensitive** (`'c'`), unlike the macro's. Every SKU reaching it has already been upper-cased inside the macro, so the flag changes nothing on this data; the `edge` set's `ÄB-12` is outside `[A-Z]` under either flag, which is what leaves its `FAMILY` NULL.
- Tool 4's Cross Tab header columns are the **frozen** list the tool's own `MetaInfo` records (`EAST`, `NORTH`, `WEST`), not something discovered from the data, so the pivot is written as one conditional aggregate per column. A warehouse outside that list has no column and is dropped with a warning — the tool's own rule, and the reason every golden set keeps `WAREHOUSE` inside those three values.
- A group/header combination with no rows is NULL and **not** zero, which `SUM` over an empty set already gives; nothing here may wrap the pivot in `COALESCE`, and the broken variant `01_cross_tab_fills_missing_with_zero.sql` is what that mistake looks like.
- A combination whose only rows have a NULL `QTY` also sums to NULL, in the oracle and in SQL alike.
- Cross Tab's `Sum` produces a `Double` whatever the data field's type, so the integer quantities land in a `FLOAT` column; the sum is taken over the exact `NUMBER` values and cast once, never accumulated in binary.
- The Cross Tab emits one row per group in ascending group-key order with NULL first, which the first statement's `ORDER BY SKU ASC NULLS FIRST, FAMILY ASC NULLS FIRST` reproduces. Row order is not part of parity for either target — `ordering.order_dependent_columns` is empty, because no column's *value* depends on it — but the order is cheap to keep and makes the written table read like the Alteryx one.
- Tool 5's Transpose has `SKU` as its only key field, so `FAMILY` is dropped from the long table, and it emits one `Name`/`Value` row per incoming row per data field in the configured order `EAST`, `NORTH`, `WEST`.
- Transpose **keeps** NULL values. A bare `UNPIVOT` drops a row whose value is NULL, but `UNPIVOT INCLUDE NULLS` keeps it (see `cookbook/transpose.md`, which documents and runs it) -- so `UNPIVOT` was not ruled out for being incapable of this, only not chosen. This segment instead stacks three projections with `UNION ALL`, which also keeps every cell, and the broken variant `02_transpose_drops_null_values.sql` is what reaching for a bare `UNPIVOT` costs.
- `NAME` and `VALUE` are the `sanitize` column-name policy applied to Alteryx's `Name` and `Value`; the golden file carries the Alteryx spelling and `compare.py` matches column names case-insensitively.
- Both Output tools write mode `Overwrite`, so each target is replaced wholesale, neither has a prior state, and neither has PreSQL, PostSQL or update keys — they are file outputs in Alteryx, mapped to Snowflake tables at intake.
- `contract.outputs[0].keys` is `["SKU", "FAMILY"]`, the Cross Tab's own group-by fields, and `outputs[1].keys` is `["SKU", "NAME"]`. Neither combination repeats in any golden set, so both comparisons are keyed diffs and can name the column behind a difference; `FAMILY` is nullable and `compare.py` matches a NULL key to a NULL key.
- `SKU` is declared `NOT NULL` on both targets: the macro's Filter removes the only row that ever had a NULL one. That was checked against all four golden sets.
- `NAME` is declared `NOT NULL` on the long target: it is one of three literals this statement writes.
- Needs verification on Snowflake: `REGEXP_SUBSTR(subject, pattern, position, occurrence, parameters, group_num)` and `REGEXP_LIKE` are documented Snowflake SQL and both transpile to the local runtime; no statement in this file has been executed on an account.
- Needs verification on Snowflake: `CREATE OR REPLACE TABLE … AS` does not enforce the `VARCHAR(n)`/`NUMBER(p,s)` widths the contract declares, so the `edge` set's 30-character `SKU` sitting exactly on its limit proves nothing about what Snowflake would do with a 31-character one.
- Nothing in this segment multiplies or divides, so Snowflake's multiplication-scale rule (`min(S1 + S2, max(S1, S2, 12))`, unlike the local runtime's `S1 + S2`) is not reached.
- `RUN_ID` is unused: contract C4 fixes the parameter list, and this segment has no query tag, no audit row and no pre/post SQL to put it in.

## Nodes with no CTE

- tool 6 has no CTE: an Output Data tool is a write, not a computation — it is the `CREATE OR REPLACE TABLE … AS` statement whose `SELECT` ends at `t4_cross_tab`.
- tool 7 has no CTE: same reason — it is the `CREATE OR REPLACE TABLE … AS` statement whose `SELECT` ends at `t5_transpose`.
