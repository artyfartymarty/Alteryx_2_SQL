# wf_0002 / seg_03 — translation notes

One assumption per line. Nothing below has been checked against a real Alteryx engine or a real
Snowflake account; every claim is against `docs/reference/simulator-semantics.md` (the parity
oracle), `docs/reference/dag-contract.md` §4 and program spec §8.

- The segment holds tools 5–11 and has no outbound stream: it ends at Output tool 10, so it materialises no `_OUT` table and writes only the mapped target.
- Tool 11 is a Browse. The oracle says a Browse emits nothing, so it has no CTE and no statement — that is the documented merge a reviewer should expect, not a missing translation.
- The two inputs are read at their literal upstream names, `MIG_WORK.WF0002_SEG_01_OUT` and `MIG_WORK.WF0002_SEG_02_OUT` (contract C4: only mapped sources and final targets go through `IDENTIFIER`).
- Tool 5 has one CTE per anchor, `t5_join_j`, `t5_join_l` and `t5_join_r`, because all three feed different Formula tools; the `t<id>_<type>` name carries the Alteryx anchor letter as a suffix.
- The `J` anchor is a plain `INNER JOIN` on the one configured key pair, and the right side's colliding `CUST_ID` would arrive as `Right_CUST_ID` — which the Join's own Select deselects, so it is simply not projected.
- The `L` and `R` anchors are `NOT EXISTS` anti-joins, not `NOT IN`: a NULL key matches nothing in Alteryx, so a NULL-`CUST_ID` customer must leave on `L` and a NULL-`CUST_ID` order on `R`, and `NOT IN` against a column holding a NULL would instead drop every row.
- The `L` and `R` anchors carry their own side's fields untouched and in their own side's order, which is why `t5_join_r` starts with `ORDER_ID` and not with `CUST_ID`.
- Join key comparison is exact: both sides are already `NUMBER` here, so there is no trimming, no case folding and no implicit cast in the predicate.
- A customer matched by several orders fans out, and the `edge` golden set fans out on both sides at once (`CUST_ID` 12 twice on the left, `ORDER_ID` 3003 twice on the right), giving four identical joined rows — which the append must preserve.
- Tools 6, 7 and 8 each append one constant `MATCH_FLAG`, so each is a single literal column on its branch.
- Tool 9 is a Union by name with three inputs in `#1`, `#2`, `#3` order: the output keeps input #1's field order, and a field an input lacks arrives NULL, so each branch is projected into tool 6's column list before the `UNION ALL`.
- The missing columns are written as typed NULLs (`CAST(NULL AS DATE)` and so on) rather than bare NULLs, so the union's column types come from the declaration and not from whichever branch happens to be first.
- `UNION ALL`, never `UNION`: Alteryx's Union stacks rows and does not de-duplicate, and the `edge` golden set depends on that.
- Tool 10 is `Append Existing`, so the statement is an `INSERT` into the existing target and the golden output is the target's state AFTER the append — the two rows `golden/targets_before/<set>/CUSTOMER_ORDER_FACT.csv` already holds are part of the expected result.
- The `INSERT` names its target columns explicitly, because Alteryx maps incoming columns to the target by name and not by position.
- The Output tool has no PreSQL and no PostSQL in this workflow, so there is no statement before or after the insert.
- `MATCH_FLAG` is declared `NOT NULL` on the target: every branch writes a literal, and the rows already in the target carry one too.
- `contract.output.keys` is `[]`: the `edge` golden set holds four byte-identical `(12, 3003)` rows, so no column set identifies a row and the comparison falls back to a row multiset, which is exact about whether the rows match and attributes a difference to a column only by nearest-match pairing, a heuristic.
- Idempotency for an Append output means "the same starting state gives the same final state", which is how `validate_segment.py` checks it — running this procedure twice against one un-reset target would legitimately double the rows.
- `RUN_ID` is unused: contract C4 fixes the parameter list, and this segment has no query tag, no audit row and no pre/post SQL to put it in.

## Nodes with no CTE

- tool 10 has no CTE: an Output Data tool is a write, not a computation — it is the `INSERT … SELECT` statement whose `SELECT` ends at `t9_union`.
- tool 11 has no CTE: a Browse emits nothing, so there is nothing for a CTE to compute; it rides with this segment only because its upstream tool does.
