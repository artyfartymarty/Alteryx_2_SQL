# RegEx  (plugin: AlteryxBasePluginsGui.RegEx.RegEx)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Three methods on one field (`docs/reference/dag-contract.md` §4). `Parse` appends one output field
per capture group; a row that does not match leaves every appended field NULL, and the row is never
dropped. `Replace` rewrites the field in place for a match; a row that does not match either keeps
its original value (`CopyUnmatched`) or becomes NULL. `Match` appends one `Bool` field saying
whether the pattern was found anywhere in the value. The regular expression dialect is Perl-like;
`CaseInsensitve` (Alteryx really spells it that way in the XML) makes matching case-insensitive when
set, case-sensitive otherwise.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2: RegEx (Parse) -- ^([A-Z]+)-(\d+)$ on SKU appends FAMILY (group 1) and ITEM_NO (group
-- 2); a non-matching row leaves both NULL. NULLIF(..., '') is a local-runtime workaround: this
-- runtime's REGEXP_SUBSTR returns '' rather than NULL for "no match" once translated to DuckDB
-- (a documented deviation, index.md's "Local verification" note); it is a no-op on real Snowflake,
-- where REGEXP_SUBSTR already returns NULL there.
WITH t2_regex AS (
    SELECT
        SKU,
        NULLIF(REGEXP_SUBSTR(SKU, '^([A-Z]+)-(\d+)$', 1, 1, 'e', 1), '') AS FAMILY,
        NULLIF(REGEXP_SUBSTR(SKU, '^([A-Z]+)-(\d+)$', 1, 1, 'e', 2), '') AS ITEM_NO
    FROM MIG_COOKBOOK.IN_1
)
SELECT
    SKU,
    FAMILY,
    ITEM_NO
FROM t2_regex
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **A non-matching row's parsed fields must be NULL, and this runtime needs an explicit
   `NULLIF(..., '')` to get there.** `tests/cookbook_examples/regex/` includes a value (`'bad'`)
   that does not match the pattern; without the `NULLIF` wrapper this runtime's translation path
   (Snowflake's `REGEXP_SUBSTR` → DuckDB's `REGEXP_EXTRACT`) returns an **empty string** for that
   row instead of NULL — found by this page's own test failing without it, not by inspection.
   Real Snowflake's own `REGEXP_SUBSTR` already returns NULL for "no match" without any wrapper, so
   the `NULLIF` is a defensive no-op there, not a correction to Snowflake's own behavior.
2. **`REGEXP_SUBSTR`'s group-number argument is 1-based and must line up with `Parse`'s configured
   `output_fields` order exactly** — the *n*-th output field is the *n*-th capture group, in the
   order the fields are listed, not the order the groups happen to appear stylistically in the
   pattern (which is normally the same thing, but not guaranteed once a pattern uses a
   non-capturing group `(?:...)`).
3. **The regex dialect itself is a parity risk, not just the NULL handling.** Alteryx's engine,
   Python's `re` (what the oracle uses), and Snowflake's regex engine are three different PCRE-like
   dialects that agree on the common subset and can disagree at the edges (possessive quantifiers,
   some Unicode property escapes, lookbehind support) — index.md's tool map flags this generally;
   nothing about this specific pattern tests dialect divergence, since the example's pattern uses
   only the common, unambiguous subset.

## Config fields that change the pattern

- `method`: `parse` → the pattern above; `replace` →
  `REGEXP_REPLACE(field, pattern, replacement)`, with `CopyUnmatched` deciding whether a
  non-matching row keeps its value (the default `REGEXP_REPLACE` behavior) or needs an explicit
  `CASE WHEN REGEXP_LIKE(field, pattern) THEN REGEXP_REPLACE(...) ELSE NULL END` when
  `CopyUnmatched` is off; `match` → `REGEXP_LIKE(field, pattern)` as a `BOOLEAN` column.
- `case_insensitive`: Snowflake's regex parameter string's `'i'` flag, appended to the `'e'` (or
  other) flags already in use — e.g. `'ei'` for parse, case-insensitive.
- `output_fields`: the `Parse` method's appended column list and their 1-based group numbers.

## Do not  (known wrong translations)

- Do not assume `REGEXP_SUBSTR`/`REGEXP_EXTRACT` return NULL for "no match" on every engine without
  checking; this page's own example needed an explicit guard against a real, observed exception.
- Do not translate `Replace` without checking `CopyUnmatched`; the two settings produce genuinely
  different SQL, not just a different value on the same expression.
- Do not carry a Python-`re`-only construct (some lookbehind forms, possessive quantifiers) into
  Snowflake's `REGEXP_*` functions assuming it is supported; verify against Snowflake's own regex
  documentation, since this project's local double translates the SQL but does not police the
  pattern text's own dialect.
