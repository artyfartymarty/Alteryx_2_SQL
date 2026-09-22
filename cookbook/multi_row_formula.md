# Multi-Row Formula  (plugin: AlteryxBasePluginsGui.MultiRowFormula.MultiRowFormula)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Computes one expression per row that may reference `[Row-n:FIELD]` / `[Row+n:FIELD]` — another
row's value of any field, offset by up to "Num rows" within the same group, in incoming order
(`docs/reference/dag-contract.md` §4). Rows are processed **in incoming order within each group**,
so a reference to the field being computed itself reads the value that was **just computed** for
that earlier row, not the value it held on the way in. Rows are emitted in the order they arrived,
not grouped together. Outside the group (near a group's edge, where `Row-n` would reach before the
first row or `Row+n` past the last), `OtherRows` decides the value: `"null"` gives NULL, `"zero"`
gives `0` for a numeric field and `""` otherwise, `"nearest"` gives the group's first or last row's
value. A newly created field reads as NULL on any row whose own value has not been computed yet.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2: Multi-Row Formula -- RUN = [Row-1:RUN] + [AMT], grouped by ACCT, ordered by SEQ (an
-- upstream ordinal). This is genuinely recursive: each row reads the *previous row's own computed
-- RUN*, not a source column, so a windowed SUM() is not equivalent -- see Parity risk 1. A NULL
-- AMT makes RUN NULL, and NULL then poisons every later row's RUN in the same group, matching
-- Alteryx's own [Row-1:RUN] + [AMT] arithmetic (NULL propagates, it is never skipped).
WITH RECURSIVE
ranked AS (
    SELECT
        ACCT,
        AMT,
        SEQ,
        ROW_NUMBER() OVER (PARTITION BY ACCT ORDER BY SEQ) AS RN
    FROM MIG_COOKBOOK.IN_1
),
t2_multi_row_formula (ACCT, AMT, SEQ, RN, RUN) AS (
    SELECT
        ACCT,
        AMT,
        SEQ,
        RN,
        CAST(0 AS FLOAT) + AMT
    FROM ranked
    WHERE RN = 1
    UNION ALL
    SELECT
        r.ACCT,
        r.AMT,
        r.SEQ,
        r.RN,
        p.RUN + r.AMT
    FROM ranked r
    JOIN t2_multi_row_formula p
      ON r.ACCT = p.ACCT AND r.RN = p.RN + 1
)
SELECT
    ACCT,
    AMT,
    SEQ,
    RUN
FROM t2_multi_row_formula
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **A `[Row-1:X]` reference to the field being computed is a genuine recursion, not a window
   function in disguise.** `SUM(AMT) OVER (PARTITION BY ACCT ORDER BY SEQ ROWS UNBOUNDED
   PRECEDING)` looks like the same running total, but a window `SUM` **skips NULL** and keeps
   accumulating — Alteryx's own arithmetic (`[Row-1:RUN] + [AMT]`) does not: a NULL `RUN` stays NULL
   forever after, because `NULL + anything` is `NULL`. `tests/cookbook_examples/multi_row_formula/`
   puts a NULL `AMT` in the middle of a group specifically to prove the pattern reproduces that
   "poisoned" tail rather than the window function's silently different "skip and keep going"
   answer — a mistake that would look identical for a group with no NULLs and only surface on
   whatever data first hits one.
2. **A Snowflake recursive CTE needs an explicit column list** (`cte_name (col1, col2, …) AS (…)`)
   whenever the anchor and recursive branches would otherwise need to infer names — spelling it out
   avoids a class of translation bugs where a column silently lines up by position instead of name.
3. **Group boundaries need `OtherRows`' rule, not a NULL by default.** The example's first row in
   each group has no `Row-1`; `unknown_rows: "zero"` makes that read as `0`, which is why the
   pattern's anchor branch is `CAST(0 AS FLOAT) + AMT`, not `NULL + AMT` — using the wrong
   `OtherRows` default changes every group's first (or last) row.

## Config fields that change the pattern

- `expression`: which field(s) and offset(s) it references decide how many prior computed rows the
  recursive branch must join back to; `num_rows > 1` needs a lookback of that many prior computed
  rows, not just one.
- `group_by`: the recursion's `PARTITION BY`/join-equality columns; empty means one group covering
  the whole table.
- `unknown_rows`: `null`/`zero`/`nearest` — decides the anchor branch's (and, for `Row+n`, the tail
  rows') starting value at a group's edge.
- `update_existing`: `true` updates a field in place (keeping its position); `false` appends a new
  field at the end — the same append/update rule as [formula.md](formula.md).

## Do not  (known wrong translations)

- Do not translate a self-referential `[Row-n:X]` as a window aggregate (`SUM`, `LAG` chained
  arithmetically, etc.) without checking whether NULL is supposed to propagate or be skipped —
  these are different operations that happen to agree whenever there is no NULL in the data.
- Do not forget the recursive CTE's explicit column list; an anchor and recursive branch that
  disagree on implicit column names can silently misalign a column.
- Do not assume `Row+n` (a forward reference) can share the exact same recursive shape as `Row-n`
  without checking the direction of the join and the `ORDER BY` — a forward lookback recurses from
  the *last* row of the group backward, not from the first row forward.
