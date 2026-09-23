# wf_0006 / seg_03 — translation notes

One assumption per line. Nothing below has been checked against a real Alteryx engine or a real
Snowflake account; every claim is against `docs/reference/simulator-semantics.md` (the parity
oracle) and program spec §8.

- The segment holds tools 4 and 5. It has no outbound stream and one `kind: "target"` output: tool 5, logical `REVENUE_BY_PERIOD`, write mode `overwrite`.
- tool 5 has no CTE: it is the Output tool, and what it becomes is the `CREATE OR REPLACE TABLE … AS` statement wrapped around tool 4's result.
- The segment reads `MIG_WORK.WF0006_SEG_02_OUT` by its literal name. That table is written by a Snowpark Python procedure rather than a SQL one, which this procedure neither knows nor needs to: contract C3 makes a work table a work table whatever produced it, and the column names and types the contract's `inputs[0]` declares are the same ones a SQL predecessor would have had to produce.
- Alteryx's `Count` counts **rows**, not values, so `CUSTOMERS` is `COUNT(*)` and not `COUNT(CUSTOMER)`. The two happen to agree here because `CUSTOMER` is `NOT NULL` on the incoming stream; writing `COUNT(*)` is what makes the agreement independent of that.
- Grouping is by `PERIOD` alone, so `CUSTOMERS` is the number of subscription rows that fell in the period. It equals the number of distinct customers only because `(CUSTOMER, PERIOD)` is unique upstream — that is a property of the data, not of this statement.
- `Sum` over a `Double` field produces a `Double` field (dag-contract §4 summarize), and the oracle adds the exact decimal value of each input. The addition therefore goes through `NUMBER(38,10)` and is cast back to `FLOAT`, rather than accumulating in `FLOAT`, where a different addition order would not give the same last bit.
- No `ORDER BY` is added. Alteryx emits Summarize groups ascending with NULL first, and SQL's `GROUP BY` promises no order at all, but the target declares `PERIOD` as its key, so the comparison joins on it and never reads a physical order. Adding a sort to imitate one nothing consumes would be decoration.
- `PERIOD` is declared nullable on the target: nothing upstream guarantees a non-NULL `PERIOD`, so a NULL group is possible in principle. No golden set contains one.
- `TOTAL_RECOGNIZED`, `TOTAL_DEFERRED` and `CUSTOMERS` are declared `NOT NULL`: a group exists only because it has at least one row, `RECOGNIZED` and `DEFERRED` are `NOT NULL` on the incoming stream, and `COUNT(*)` is never NULL.
- `contract.outputs[0].keys` is `["PERIOD"]`, unique in every golden set because the Summarize collapses each period into one row.
- `row_relation` is `aggregate`: the segment collapses rows into period groups.
- Write mode `overwrite` means the target is replaced wholesale, so it has no prior state, there is no `targets_before` file for this workflow, and re-running the segment from the same work table is idempotent.
- `ALTER SESSION SET TIMEZONE`/`WEEK_START` is set for the same reason every procedure in this project sets it, and contract C4 requires `EXECUTE AS CALLER` so that it is allowed to. This workflow has no date or timestamp column, so neither setting can affect its result. `PERIOD` is text (`2026-01`), never a date.
- `RUN_ID` is unused: contract C4 fixes the parameter list, and this segment has no query tag, no audit row and no pre/post SQL to put it in.
