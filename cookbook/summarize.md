# Summarize  (plugin: AlteryxSpatialPluginsGui.Summarize.Summarize)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Groups rows by the configured `GroupBy` fields and emits one row per group, in **ascending
group-key order with NULL sorting first** (`docs/reference/dag-contract.md` §4). `Count` counts
**rows**, including ones whose value is NULL; `CountNonNull` counts **values**, skipping NULL —
these are genuinely different aggregations, not two names for the same thing. `Sum` and `Avg`
ignore NULL and are NULL for a group whose values are all NULL. `Concat` skips NULL, joins the
non-NULL values with the configured separator (a comma when none is configured), and is `""` for a
group with nothing to join — and because it is a positional join of text, its result depends on
**the order the rows arrived in**, the same order-dependence `First`/`Last` and percentiles have.
`Sum` of a `FixedDecimal` field stays `FixedDecimal`; every other `Sum`/`Avg` is `Double`; every
`Count*` is `Int64`; `Concat` is `V_String`; every other action keeps its source column's type.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2: Summarize -- group by REGION. Alteryx's Count counts rows and CountNonNull counts
-- values, so ORDER_COUNT is COUNT(*) and PRICED_COUNT is COUNT(AMOUNT); Concat skips NULLs and
-- joins in incoming order, which SEQ (an upstream Record ID/Sort's ordinal) makes explicit --
-- SQL has no notion of "the order rows arrived in" on its own. A group with nothing to join is
-- '' in Alteryx; LISTAGG gives NULL there instead, so it is wrapped in COALESCE(..., '').
WITH t2_summarize AS (
    SELECT
        REGION,
        CAST(SUM(CAST(AMOUNT AS NUMBER(38,10))) AS FLOAT)             AS TOTAL_AMOUNT,
        COUNT(*)                                                      AS ORDER_COUNT,
        COUNT(AMOUNT)                                                 AS PRICED_COUNT,
        COALESCE(LISTAGG(NOTE, ',') WITHIN GROUP (ORDER BY SEQ), '')  AS NOTES
    FROM MIG_COOKBOOK.IN_1
    GROUP BY REGION
)
SELECT
    REGION,
    TOTAL_AMOUNT,
    ORDER_COUNT,
    PRICED_COUNT,
    NOTES
FROM t2_summarize
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **`Concat`, like `First`/`Last`, needs a real ordering column — SQL has no memory of "the order
   rows arrived in" the way Alteryx's engine does.** `tests/cookbook_examples/summarize/` carries an
   explicit `SEQ` column (standing in for an upstream Record ID or Sort) and uses `LISTAGG(...)
   WITHIN GROUP (ORDER BY SEQ)`; a plain `LISTAGG` with no `WITHIN GROUP` clause is not guaranteed
   to agree with Alteryx's row order even when it happens to today, on this data, on this engine.
2. **`SUM`/`AVG` on a `Double` column must go through the `NUMBER` idiom, exactly like Formula's**
   (see [formula.md](formula.md) Parity risk 1) — accumulating in `FLOAT` compounds the same
   binary/decimal disagreement across every row in the group, not just one value.
3. **`Count` and `CountNonNull` are not interchangeable, and picking the wrong one is a silent
   off-by-however-many-NULLs bug.** The example's `EAST` group has one NULL `AMOUNT` among three
   rows, so `ORDER_COUNT` (3) and `PRICED_COUNT` (2) genuinely disagree — a translation that uses
   `COUNT(AMOUNT)` for both looks identical to a reviewer skimming the SQL and is wrong for exactly
   the rows that matter.
4. **A group with nothing to `Concat` is `''` in Alteryx, but `NULL` from a bare `LISTAGG`.** The
   example's NULL-region group has exactly one row and its `NOTE` is NULL, so nothing is joined;
   `LISTAGG` alone returns NULL there, which the pattern above catches with
   `COALESCE(LISTAGG(...), '')` — found by this page's own test failing without it, not by
   inspection.

## Config fields that change the pattern

- `fields[].action`: `GroupBy` → a `GROUP BY` column (and part of the output's natural key);
  `Sum`/`Avg`/`Min`/`Max`/`Count`/`CountNonNull`/`CountDistinct`/`Concat`/`First`/`Last` → one
  aggregate expression each, per the table above.
- `fields[].separator`: `Concat`'s join separator (defaults to a comma when not configured).
- The **group-by columns are the only safe SQL key** for this stream: they are unique by
  construction (that is what `GROUP BY` guarantees), unlike almost every other tool's output in
  this cookbook (index.md's "Local verification" note on keys).

## Do not  (known wrong translations)

- Do not use `COUNT(<col>)` when the config says `Count`; `Count` means `COUNT(*)` — Alteryx counts
  rows, not non-NULL values, for that action specifically.
- Do not `LISTAGG`/`STRING_AGG` without an explicit `WITHIN GROUP (ORDER BY …)` when anything
  downstream depends on the concatenated text's exact order — an unordered `Concat` translation
  that happens to match today is not a translation that is guaranteed to keep matching.
- Do not `ROUND`/accumulate a `Sum`/`Avg` over a raw `FLOAT` column; cast to `NUMBER` first, the
  same as [formula.md](formula.md)'s idiom.
