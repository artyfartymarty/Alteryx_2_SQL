# Sample  (plugin: AlteryxBasePluginsGui.Sample.Sample)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Keeps the first N, the last N, all but the first N, or every Nth row of each group (optionally the
whole table, when no group fields are configured), and emits what it kept in **incoming order** —
not grouped together (`docs/reference/dag-contract.md` §4). A group with fewer rows than the
configured count simply keeps what it has; nothing is padded or dropped for being "short". Like
Unique and Sort, which row counts as "first" or "last" depends entirely on the order the rows
arrived in.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2: Sample -- first 2 rows per GRP, kept in incoming order (SEQ, an upstream ordinal); a
-- group with fewer than 2 rows just keeps what it has.
WITH t2_sample AS (
    SELECT
        GRP,
        VAL,
        SEQ
    FROM MIG_COOKBOOK.IN_1
    QUALIFY ROW_NUMBER() OVER (PARTITION BY GRP ORDER BY SEQ) <= 2
)
SELECT
    GRP,
    VAL,
    SEQ
FROM t2_sample
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **Same ordinal requirement as [unique.md](unique.md) and [summarize.md](summarize.md)'s
   `Concat`: the window function's `ORDER BY` must be a real, deterministic column**, not omitted.
   `tests/cookbook_examples/sample/` gives group `A` four rows (kept, kept, dropped, dropped in
   `SEQ` order) specifically so a translation missing the `ORDER BY` — or ordering by the wrong
   column — has a real chance of keeping the wrong two.
2. **A NULL group is a group like any other**, kept or trimmed by the same rule — the example's
   `GRP IS NULL` row is its own group of one and is kept (a group of one is always "first 1" no
   matter the configured N).
3. **`mode: last`, `skip`, and `one_in_n` are three genuinely different filters, not variations on a
   theme that can share one translation.** `last` needs `ROW_NUMBER() OVER (... ORDER BY <ordinal>
   DESC) <= n`; `skip` needs `> n` on the ascending order; `one_in_n` needs `MOD(ROW_NUMBER() OVER
   (... ORDER BY <ordinal>) - 1, n) = 0` — getting the off-by-one on any of these wrong keeps or
   drops one extra row per group, easy to miss on a quick read.
4. **`tests/cookbook_examples/sample/` only exercises `mode: first`.** The three spellings in risk 3
   are written from the oracle's rule and have not been run against it; a translation that uses one
   is checked for the first time by its own segment's golden data.

## Config fields that change the pattern

- `mode`: `first`/`last`/`skip`/`one_in_n`, each its own `QUALIFY` predicate (Parity risk 3).
- `n`: the count or the stride, depending on `mode`.
- `group_by`: the `PARTITION BY` list; empty means one partition covering the whole table.

## Do not  (known wrong translations)

- Do not translate `Sample` as a plain `LIMIT n` unless `group_by` is genuinely empty **and** the
  upstream order is already established and intentional — `LIMIT` has no `PARTITION BY` equivalent
  for a grouped sample.
- Do not omit the window function's `ORDER BY`; an unordered `ROW_NUMBER()` is nondeterministic by
  the SQL standard, which defeats the entire purpose of translating an order-dependent tool.
- Do not reorder the output to "look like" the grouped view Alteryx's canvas might suggest; kept
  rows are emitted in incoming order, not grouped together.
