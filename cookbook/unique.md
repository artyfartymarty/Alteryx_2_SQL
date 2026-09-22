# Unique  (plugin: AlteryxBasePluginsGui.Unique.Unique)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Sends the **first** row of each distinct key combination, in incoming order, to `U`; every later
row sharing that same combination goes to `D` (`docs/reference/dag-contract.md` §4). Comparison is
case-sensitive and exact; a NULL in a key field is a value like any other for grouping purposes —
two rows that both have a NULL in the same key field are the same key combination, not "unknown
and therefore never equal" the way a NULL comparison in a `WHERE` clause behaves. Which row counts
as "first" depends entirely on the order the rows arrived in, so — the same caveat
[sort.md](sort.md) states — Unique's own correctness depends on whatever established that order
upstream.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2 (anchor U): Unique on ACCT, DAY -- the first row per key combination in incoming order
-- (SEQ, an upstream ordinal) is kept; comparison is case-sensitive and a NULL key is its own
-- distinct value, grouped with any other NULL-key row the same way any other value would be.
WITH t2_unique_u AS (
    SELECT
        ACCT,
        DAY,
        VALUE,
        SEQ
    FROM MIG_COOKBOOK.IN_1
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ACCT, DAY ORDER BY SEQ) = 1
)
SELECT
    ACCT,
    DAY,
    VALUE,
    SEQ
FROM t2_unique_u
```

```sql
-- tool 2 (anchor D): Unique on ACCT, DAY -- every row after the first per key combination, in
-- the same incoming order (SEQ).
WITH t2_unique_d AS (
    SELECT
        ACCT,
        DAY,
        VALUE,
        SEQ
    FROM MIG_COOKBOOK.IN_1
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ACCT, DAY ORDER BY SEQ) > 1
)
SELECT
    ACCT,
    DAY,
    VALUE,
    SEQ
FROM t2_unique_d
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **`ROW_NUMBER() OVER (... ORDER BY <a real ordinal>)`, never a bare `PARTITION BY` with no
   `ORDER BY`.** Without an `ORDER BY`, which row a window function calls "first" inside a
   partition is undefined by the SQL standard, even though a given engine may happen to be
   consistent about it today. `tests/cookbook_examples/unique/` carries an explicit `SEQ` column
   (standing in for an upstream Record ID or Sort, same as [summarize.md](summarize.md)'s `Concat`) so "first" is
   pinned to the same row the oracle picks.
2. **A NULL key groups with other NULLs, the opposite of a `WHERE key = key2` comparison.**
   `PARTITION BY` (like `GROUP BY`) treats NULL as an ordinary, self-equal grouping value — the
   example's `ACCT IS NULL` row is genuinely unique on its own, not silently excluded the way it
   would be from an equi-join.
3. **`U`'s output is the one stream in this cookbook safely keyed by the tool's own dedup fields**
   (`ACCT, DAY` here) — `D` is not: a key that appears three or more times in the input leaves more
   than one row in `D`, so `D` stays keyless by default (index.md's "Local verification" note).

## Config fields that change the pattern

- `fields`: the `PARTITION BY` column list; order does not matter to `PARTITION BY` the way it
  matters to `ORDER BY`, but should still match the tool's configured list exactly for review
  clarity.

## Do not  (known wrong translations)

- Do not use `QUALIFY ROW_NUMBER() OVER (PARTITION BY ...) = 1` without an `ORDER BY` inside the
  window — that reintroduces exactly the order-dependence risk this tool exists to resolve
  deterministically in Alteryx.
- Do not use `DISTINCT` or `GROUP BY` in place of the window-function pattern; `DISTINCT` collapses
  every column, not just the configured key fields, and neither `DISTINCT` nor `GROUP BY` can
  produce the `D` (duplicates) output at all.
- Do not assume the `D` output is safe to key on the same fields as `U`; only `U` is guaranteed
  unique by the tool's own definition.
