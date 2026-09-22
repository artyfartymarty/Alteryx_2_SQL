# Record ID  (plugin: AlteryxBasePluginsGui.RecordID.RecordID)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Numbers every row sequentially from a configured start value, in incoming order, and inserts the
new field either first or last (`docs/reference/dag-contract.md` §4). The numbering never skips,
repeats, or depends on any other field's value — it is purely positional. Because it is purely
positional, it is also purely order-dependent: the same rows in a different incoming order produce
different numbers on the same rows.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2: Record ID -- numbers rows from 100 in incoming order (SEQ, an upstream ordinal) and
-- puts the field first. Snowflake tables carry no implicit row order, so a real segment needs
-- this same explicit ordering column from whatever established determinism upstream (a Sort, or
-- the source query's own ORDER BY) -- see Parity risk 1.
WITH t2_record_id AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY SEQ) + 99 AS RID,
        CODE,
        VAL,
        SEQ
    FROM MIG_COOKBOOK.IN_1
)
SELECT
    RID,
    CODE,
    VAL,
    SEQ
FROM t2_record_id
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **A Snowflake table has no implicit row order at all** — not "an order that happens to match
   the load order today", genuinely none, by the relational model Snowflake (like every SQL
   database) is built on. `ROW_NUMBER() OVER (ORDER BY <a real column>)` is not optional
   decoration; without a real deterministic ordering column, two runs of the same `SELECT` over the
   same data can legally return different numbers on different rows. `tests/cookbook_examples/record_id/`
   carries an explicit `SEQ` column standing in for whatever upstream tool (a Sort, or the source
   query's own `ORDER BY`) established the order Alteryx's engine actually saw.
2. **`start` is an offset, not a literal `ROW_NUMBER()` replacement.** `ROW_NUMBER()` always begins
   at 1; the pattern above adds `start - 1` (`99` for a configured start of `100`) rather than
   assuming Snowflake has some way to seed a window function's starting value directly.
3. **The new field's *position* (`first`/`last`) changes the column list order, and a downstream
   consumer that reads columns by position rather than by name will see a different column at a
   given index depending on it** — always name every column explicitly in the `SELECT` list, never
   rely on `SELECT *` putting the new field where Alteryx would have.

## Config fields that change the pattern

- `start`: the arithmetic offset added to `ROW_NUMBER()`.
- `position`: `first` puts the generated column at the head of the `SELECT` list; `last` puts it at
  the tail — a pure list-ordering change, no effect on the numbering itself.
- `type`/`field`: the generated column's Alteryx type (almost always an integer type) and name.

## Do not  (known wrong translations)

- Do not omit the window function's `ORDER BY`, or order by a column that does not actually
  reproduce Alteryx's incoming order (for example, ordering by a business key instead of the true
  arrival sequence) — the numbers would be internally consistent but would not match the oracle's.
- Do not use `start` as `ROW_NUMBER()`'s own starting point via some dialect-specific `START WITH`
  syntax; add the offset arithmetically, since `ROW_NUMBER()` itself always starts at 1.
- Do not key a downstream comparison on this stream's generated field without checking the field is
  genuinely unique on both sides first — it is, by construction, unlike almost every other stream
  in this cookbook (index.md's "Local verification" note on keys).
