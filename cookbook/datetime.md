# DateTime  (plugin: AlteryxBasePluginsGui.DateTime.DateTime)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Converts text to a `DateTime` value, or a `DateTime` value to text, using one configured format
string (`docs/reference/dag-contract.md` §4). Text the format cannot read becomes NULL — including
a date that does not exist, such as 31 February — never an error. A parsed value always carries a
full `YYYY-MM-DD HH:MM:SS`, midnight when the format supplied no time component. The tool always
**appends** its output field; it never overwrites the source field in place.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2: DateTime -- text to DateTime, format DD/MM/YYYY (Snowflake token style; the Alteryx
-- tool's own config uses %d/%m/%Y, Python strftime style -- see Parity risk 1). Unparseable text,
-- and a date that does not exist (31 February), both become NULL.
WITH t2_datetime AS (
    SELECT
        POSTED,
        TRY_TO_TIMESTAMP(POSTED, 'DD/MM/YYYY') AS POSTED_DT
    FROM MIG_COOKBOOK.IN_1
)
SELECT
    POSTED,
    POSTED_DT
FROM t2_datetime
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **Two different format-token languages are in play, and neither is what the other side
   expects.** The Alteryx DateTime tool's own `Format` field uses `strftime`-style tokens
   (`%d/%m/%Y`); Snowflake's `TO_TIMESTAMP`/`TO_CHAR` use their own tokens (`DD/MM/YYYY`). A
   translator has to convert one to the other by hand for every format string — there is no shared
   syntax to copy verbatim. `docs/reference/dag-contract.md`'s own datetime example uses
   `%d/%m/%Y`, which is why this page's SQL spells the same format `'DD/MM/YYYY'` instead.
2. **`TRY_TO_TIMESTAMP`, never `TO_TIMESTAMP`, is what reproduces "unparseable → NULL".**
   `tests/cookbook_examples/datetime/` includes a genuinely invalid date (`31/02/2026`, a February
   31st) specifically because it is a case a naive `TRY_` vs plain distinction can miss on
   review — the string *looks* well-formed for the format, and only fails because the calendar day
   does not exist. `TO_TIMESTAMP` would raise on that row instead of returning NULL, which is not
   what Alteryx does.
3. **A parsed value always carries `00:00:00`**, never a NULL or missing time component, once the
   format has no time tokens — a downstream comparison against a `DATE` column (rather than a
   `TIMESTAMP`) needs an explicit `::DATE` cast if the workflow's own logic expected one, since
   Alteryx's own `DateTime` type always carries a time part internally.

## Config fields that change the pattern

- `direction`: `to_datetime` → `TRY_TO_TIMESTAMP(field, fmt)` (the pattern above); `to_string` →
  `TO_CHAR(field, fmt)` (not `TRY_TO_CHAR` — formatting an already-valid `DateTime` value does not
  need warn-and-null semantics the way parsing text does).
- `format`: translate every token by hand (Parity risk 1); do not assume a format string can be
  copied between the two token languages unexamined, even when it happens to look similar (`YYYY`
  and `%Y` both mean "4-digit year", but nothing else lines up character-for-character).
- `out_field`: the appended column's name; the source field is never modified in place.

## Do not  (known wrong translations)

- Do not translate the Alteryx format string into Snowflake's token language by find-and-replace
  without checking each token individually; `%y` (2-digit year) and `%Y` (4-digit year) map to
  different Snowflake tokens (`YY` vs `YYYY`) and are easy to swap by accident.
- Do not use plain `TO_TIMESTAMP`/`TO_DATE` where the field might contain unparseable or
  calendar-invalid text; use the `TRY_` form so a bad value becomes NULL instead of failing the
  whole statement.
- Do not assume the appended field replaces the source field's position or content; it is always a
  new, appended column, and the source field passes through untouched.
