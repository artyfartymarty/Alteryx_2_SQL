# Union  (plugin: AlteryxBasePluginsGui.Union.Union)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Stacks two or more inputs into one output, in `dst_order` (Alteryx's `#1`, `#2`, … connection
order) — `docs/reference/dag-contract.md` §4. **By name** (the default), the output keeps the
*first* input's field order; a later input's field that the first input lacks is appended when that
input is reached, and a field an input does not have arrives as a typed NULL for that input's rows —
never a bare untyped NULL, and never a dropped column. **By position**, the first input's fields set
the output's names and every other input is lined up by ordinal position regardless of its own
field names. Nothing here deduplicates: a row that was a duplicate in one input is still a duplicate
in the output, and a value the two inputs happen to share is not merged.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 3: Union by name, inputs stacked in #1, #2 order. The output keeps the first input's field
-- order and a field an input lacks arrives as a typed NULL, never a bare NULL.
WITH t3_union AS (
    SELECT
        CUST_ID,
        NAME,
        MATCH_FLAG,
        CAST(NULL AS NUMBER(38,0)) AS ORDER_ID
    FROM MIG_COOKBOOK.IN_1
    UNION ALL
    SELECT
        CUST_ID,
        CAST(NULL AS VARCHAR) AS NAME,
        MATCH_FLAG,
        ORDER_ID
    FROM MIG_COOKBOOK.IN_2
)
SELECT
    CUST_ID,
    NAME,
    MATCH_FLAG,
    ORDER_ID
FROM t3_union
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **`UNION ALL`, never bare `UNION`.** Plain `UNION` deduplicates, which Alteryx's Union tool never
   does. `tests/cookbook_examples/union/` puts a duplicate row in each input; a translation that
   used `UNION` would silently drop one copy of each and still "pass" a naive row-count check while
   failing `compare.py`'s row multiset.
2. **A missing field's fill-in must be `CAST(NULL AS <type>)`, typed to the *output* column's own
   type, not a bare `NULL` literal.** A bare `NULL` in a `UNION ALL` branch lets the database infer
   whatever type it wants for that column, which can silently change the merged column's type (and
   therefore its Snowflake type family) once every branch is stacked — this is why the pattern
   above casts `ORDER_ID`'s and `NAME`'s fill-ins explicitly rather than leaving them bare.
3. **By-name order is the *first* input's order, permanently** — adding a field to the second input
   never moves it earlier than every field the first input already had. A translation that sorts
   columns alphabetically, or takes "whichever input happens to be listed first in the XML", can
   silently disagree with Alteryx's actual `dst_order`.

## Config fields that change the pattern

- `mode`: `name` is the pattern above; `position` instead takes the first input's column list and
  lines up every other input by ordinal position, ignoring names entirely — a field name mismatch
  that `by name` would catch (and NULL-fill) becomes a silent value swap under `by position`.
- The **edge `dst_order`** (Alteryx's `#1`, `#2`, `#3` connection labels) fixes which input's
  `SELECT` comes first in the `UNION ALL` chain — it decides the output's column order under `name`
  mode and the "whichever input notionally came first" question under `position` mode alike.

## Do not  (known wrong translations)

- Do not use `UNION` where `UNION ALL` is meant; Alteryx's Union never deduplicates and a plain
  `UNION` silently invents a different row count.
- Do not leave a missing column's fill-in as a bare, untyped `NULL` — cast it to the output
  column's declared type so every branch of the `UNION ALL` agrees on that column's type.
- Do not assume the inputs are safe to reorder because "the data looks the same either way" — under
  `by name` mode the *first* input's field order becomes the output's field order, which a
  downstream Union-by-position or a fixed-position consumer depends on.
