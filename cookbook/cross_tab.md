# Cross Tab  (plugin: AlteryxBasePluginsGui.CrossTab.CrossTab)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Pivots one data field's values into columns named after another field's distinct values, grouped
by the configured key fields, aggregated by the configured method
(`docs/reference/dag-contract.md` §4). Output rows are ordered by the group-by fields ascending,
NULL first — the same rule as Summarize. Header values are sanitized: every character outside
`[A-Za-z0-9_]` becomes `_`. **The header column set is decided at design time in Alteryx** (the
tool freezes it from a sample run, in `meta.Output`) — a header value that shows up later and was
not in that frozen set is dropped, with a warning, from every pivoted column; it does not grow the
table with a new column at run time. A `(group, header)` combination with no rows — or whose only
row's data value is NULL — is NULL in the output, not zero. Only the tool's *first* configured
aggregation method is applied even if more than one is configured.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2: Cross Tab -- pivot QTY by WH into a frozen header list (EAST, NORTH, WEST); a WH value
-- outside that list (SOUTH here) is not one of the pivoted columns and contributes to none of
-- them, same as the oracle's own silent-drop rule for an unrecognised header. A (SKU, header)
-- combination with no rows, or only a NULL QTY, is NULL -- never zero -- which SQL's own SUM
-- already gives for free.
WITH t2_cross_tab AS (
    SELECT
        SKU,
        CAST(SUM(CASE WHEN WH = 'EAST'  THEN QTY END) AS FLOAT) AS EAST,
        CAST(SUM(CASE WHEN WH = 'NORTH' THEN QTY END) AS FLOAT) AS NORTH,
        CAST(SUM(CASE WHEN WH = 'WEST'  THEN QTY END) AS FLOAT) AS WEST
    FROM MIG_COOKBOOK.IN_1
    GROUP BY SKU
)
SELECT
    SKU,
    EAST,
    NORTH,
    WEST
FROM t2_cross_tab
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **The header list must be frozen at translation time, from the tool's own `meta.Output`, never
   discovered dynamically by the translated SQL** — the tool map calls this out directly ("dynamic
   header set → dynamic SQL or frozen list"). `tests/cookbook_examples/cross_tab/` fixes its own
   `node.meta.Output` to three columns; a header value the data contains but the frozen list does
   not (`SOUTH` in the example) must be silently excluded from every pivoted column, exactly as the
   oracle does, rather than raising or growing a new column.
2. **A `CASE WHEN` conditional `SUM` gives NULL for "nothing contributed" for free — do not
   special-case it.** Both an empty `(SKU, header)` combination and one whose only row has a NULL
   data value land on `SUM` over an all-NULL (or empty) set, which is NULL in standard SQL — the
   same NULL the oracle documents, with no extra `CASE`/`COALESCE` needed. Reaching for
   `COALESCE(..., 0)` here would be **wrong**: Cross Tab's NULL-vs-zero distinction is real, unlike
   Summarize's `Concat` (see [summarize.md](summarize.md) Parity risk 4, which needed the opposite
   fix).
3. **The aggregate's Snowflake type comes from the *method*, not from the source column's type.**
   `QTY` is an integer field, but `Sum`/`Avg` in a Cross Tab are always `Double` regardless of the
   source column — the explicit `CAST(... AS FLOAT)` above is required, not stylistic; without it
   the pivoted columns come back as a `NUMBER` family and fail the schema check against a contract
   that (correctly) declares them `Double`.

## Config fields that change the pattern

- `methods`: which aggregate fills each `CASE WHEN` (`Sum`/`Count`/`CountNonNull`/`CountDistinct`/
  `Avg`/`Concat`/…) — only the first configured method is used even if the tool lists several.
  `Concat`'s column type is `V_String`, not `Double`, so its `CAST` target differs from Sum/Avg's.
- `group_by`: the `GROUP BY` list and the output's leading (key) columns.
- `header_field` / `data_field`: which field names the pivoted columns and which field feeds each
  `CASE WHEN`'s aggregated expression.
- The **frozen header list itself** (from `meta.Output`) is config in the sense that it must be
  re-derived whenever the workflow's own header values could change; the translated SQL has no way
  to notice on its own.

## Do not  (known wrong translations)

- Do not use dynamic SQL (`PIVOT` with a runtime-discovered column list) unless the header set is
  genuinely expected to grow; a frozen, explicit `CASE WHEN` per header column is simpler to review
  and matches what Alteryx itself froze at design time.
- Do not default a missing `(group, header)` combination to `0`; Alteryx's own rule is NULL, and
  `COALESCE`ing it to zero would misrepresent "no data" as "measured zero".
- Do not let the source column's own type decide the pivoted columns' Snowflake type; use the
  aggregation method's type (index.md's type map plus this page's Parity risk 3).
