# Transpose  (plugin: AlteryxBasePluginsGui.Transpose.Transpose)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Keeps the configured key fields, then emits one `Name`/`Value` row per configured data field, per
input row, in the data fields' configured order (`docs/reference/dag-contract.md` §4). **NULL data
values are kept** — a data field whose value is NULL still produces a `Name`/`Value` row, with
`Value` NULL, not a row that is silently skipped. `Value`'s type is the data fields' common Alteryx
type when they all agree, or `V_String` when they differ (since a single column now has to hold
values that used to live in columns of different types).

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2: Transpose -- keys SKU, one Name/Value row per data field (EAST, WEST) per input row, in
-- configured order. INCLUDE NULLS is required: a plain UNPIVOT drops a row whose value is NULL
-- entirely, but Alteryx keeps it.
WITH t2_transpose AS (
    SELECT
        SKU,
        NAME,
        VALUE
    FROM MIG_COOKBOOK.IN_1
    UNPIVOT INCLUDE NULLS (VALUE FOR NAME IN (EAST, WEST))
)
SELECT
    SKU,
    NAME,
    VALUE
FROM t2_transpose
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **A bare `UNPIVOT` silently drops a row whose value is NULL — `INCLUDE NULLS` is required, not
   optional.** `tests/cookbook_examples/transpose/` puts a NULL `EAST` value on one input row; a
   translation missing `INCLUDE NULLS` produces one fewer row than the oracle for that input row,
   which is a row-count difference `compare.py` catches as a `set_diff`, not a `NULL_SEMANTICS`
   column mismatch — the row is simply gone, not present-with-a-NULL.
2. **The `IN (…)` list's order is the data fields' *configured* order, not alphabetical or the
   source table's column order.** The example lists `EAST` before `WEST` because that is the
   tool's own `data_fields` order; swapping them changes which `Name`/`Value` pair a downstream
   row-position-dependent consumer would see first for a given key.
3. **When the data fields do not all share one Alteryx type, `Value`'s Snowflake type must be
   `VARCHAR` (widened), not whichever field's type happens to come first.** This page's own example
   keeps both data fields `Double` to isolate the NULL-handling risk above; a workflow that
   transposes a mix of numeric and text fields needs an explicit `CAST` of every branch to a common
   text representation before the `UNPIVOT`, mirroring Union's typed-NULL-fill rule
   ([union.md](union.md) Parity risk 2).

## Config fields that change the pattern

- `key_fields`: carried through unchanged, once per output row (not deduplicated further).
- `data_fields`: both the `UNPIVOT ... IN (...)` list and its order.

## Do not  (known wrong translations)

- Do not omit `INCLUDE NULLS`; Alteryx's Transpose never drops a row for having a NULL value.
- Do not assume `UNPIVOT`'s default row order matches "one input row, all its data fields, then the
  next input row" without checking on the target engine — the pattern above happens to preserve it
  on the local runtime, but this is exactly the kind of engine behavior index.md's "Local
  verification" note says is unconfirmed on real Snowflake.
- Do not transpose a mix of differently-typed data fields into `Value` without an explicit common
  cast; letting the database infer `Value`'s type from whichever column it processes first is not
  the same as Alteryx's own declared widening rule.
