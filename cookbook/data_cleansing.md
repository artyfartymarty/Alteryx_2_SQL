# Data Cleansing  (macro: Cleanse.yxmc)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

A macro (`Cleanse.yxmc`, no `Plugin` GuiSettings of its own — the parser recognizes it by
`<EngineSettings Macro="Cleanse.yxmc">`) that applies a fixed set of options, in a fixed order, to
each field it is configured for and leaves every other field untouched
(`docs/reference/dag-contract.md` §4): **replace NULL strings with blank**, remove
tabs/linebreaks/duplicate-spaces, remove all whitespace, trim, remove letters, remove numbers,
remove punctuation, modify case. "Replace nulls with blank" applies to string fields only and
"replace nulls with 0" to numeric fields only — the two null-replacement options are mutually
exclusive by field type, not by configuration. Every character/case option applies to string fields
only. A numeric field's *value* passes through the numeric-only options unchanged (only its NULL
replacement, if configured, can touch it).

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2: Data Cleansing (Cleanse.yxmc) on NAME only -- the macro's own option order: replace
-- NULL strings with blank, then remove tabs/linebreaks/duplicate spaces, then trim, then modify
-- case. COALESCE is innermost: trimming a NULL would leave it NULL, and the tool promises blank.
-- CODE is not in the field list, so it passes through untouched.
WITH t2_data_cleansing AS (
    SELECT
        UPPER(TRIM(REGEXP_REPLACE(REGEXP_REPLACE(COALESCE(NAME, ''), '[\t\r\n]', ' '), ' {2,}', ' '))) AS NAME,
        CODE
    FROM MIG_COOKBOOK.IN_1
)
SELECT
    NAME,
    CODE
FROM t2_data_cleansing
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **The options apply in a fixed order, and nesting them in any other order changes the result.**
   `COALESCE` must be innermost — trimming or upper-casing a NULL leaves it NULL, not blank, so the
   NULL replacement has to happen before anything else touches the value.
   `tests/cookbook_examples/data_cleansing/` includes a value with both a leading/trailing space
   *and* an internal run of tabs/newlines/duplicate spaces (`'\ta\tb  c\n'`) precisely so
   "collapse duplicate whitespace" and "trim" have to run in the right relative order to both take
   effect (collapsing an internal run does not by itself remove leading/trailing space, and
   trimming first would leave an internal double space that trimming alone cannot fix).
2. **Only the fields in `fields[]` are touched — every other column on the row passes through
   byte-for-byte**, including a column that could itself use cleansing. The example's `CODE` column
   is deliberately outside the field list to make this pass-through checkable rather than assumed.
3. **NULL-replacement is type-gated, not a single on/off switch.** `replace_null_strings_blank` and
   `replace_null_numeric_zero` are independent options that each apply only to fields of the
   matching kind — configuring the string option does nothing for a NULL in a numeric field in the
   same tool, and vice versa.

## Config fields that change the pattern

- `fields`: which columns the whole option set applies to; every other column is untouched.
- `replace_null_strings_blank` / `replace_null_numeric_zero`: which `COALESCE` (if any) wraps a
  string field's or a numeric field's expression — string fields get `COALESCE(x, '')`, numeric
  fields get `COALESCE(x, 0)`, and neither applies to the other field kind.
- `remove_tabs_linebreaks_dupspaces` / `remove_all_whitespace` / `trim_whitespace`: each an
  independent `REGEXP_REPLACE`/`TRIM` layer, in the fixed order the "What Alteryx does" section
  states.
- `remove_letters` / `remove_numbers` / `remove_punctuation`: each a `REGEXP_REPLACE` stripping a
  fixed character class (`[A-Za-z]`, `[0-9]`, ASCII punctuation respectively).
- `modify_case`: `upper`/`lower`/`title` → `UPPER`/`LOWER`/`INITCAP`, applied last.

## Do not  (known wrong translations)

- Do not apply `TRIM` before collapsing internal duplicate whitespace (or vice versa without
  checking); the macro's own fixed order is not interchangeable, and swapping two adjacent steps
  can look identical on tidy data and diverge only on messy data — the entire reason
  `tests/cookbook_examples/data_cleansing/` includes a genuinely messy value.
- Do not apply a string-only option (case, letter/number/punctuation removal) to a numeric field,
  or a numeric NULL replacement to a string field — the macro itself never does either.
- Do not cleanse a column the tool's `fields[]` does not list; an unlisted column, even one that
  obviously "should" be cleaned, must pass through unchanged.
