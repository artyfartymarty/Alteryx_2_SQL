# Filter  (plugin: AlteryxBasePluginsGui.Filter.Filter)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Evaluates one expression per row and sends it to one of two outputs: `True` for rows where the
expression is really true, `False` for **everything else, including a NULL evaluation**
(`docs/reference/dag-contract.md` §4, program spec §8.5). A comparison against NULL is NULL, never
true and never false (`docs/reference/simulator-semantics.md` §2), so a row whose filtered column is
NULL always lands on `False` regardless of what the rest of the expression says. Row order is
preserved within each output; no row is duplicated or dropped beyond this one split. A condition
that is neither boolean, numeric nor numeric text is an authoring error the oracle refuses to guess
at, not a silent NULL.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2 (anchor T): Filter [REGION] != "WEST" -- the True branch keeps only rows where the
-- expression is really true; a NULL REGION makes it NULL, which is not true, so SQL's own
-- three-valued WHERE already drops those rows here.
WITH t2_filter_t AS (
    SELECT
        ID,
        REGION,
        AMOUNT
    FROM MIG_COOKBOOK.IN_1
    WHERE REGION <> 'WEST'
)
SELECT
    ID,
    REGION,
    AMOUNT
FROM t2_filter_t
```

```sql
-- tool 2 (anchor F): Filter [REGION] != "WEST" -- the False branch takes the rows the expression
-- makes false AND the rows it makes NULL; the IS NULL half is the whole point, and a plain
-- WHERE REGION = 'WEST' would silently lose the NULL-region rows.
WITH t2_filter_f AS (
    SELECT
        ID,
        REGION,
        AMOUNT
    FROM MIG_COOKBOOK.IN_1
    WHERE NOT (REGION <> 'WEST') OR (REGION) IS NULL
)
SELECT
    ID,
    REGION,
    AMOUNT
FROM t2_filter_f
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **The False branch's `IS NULL` half is not optional decoration.** `tests/cookbook_examples/filter/`
   puts a NULL `REGION` in the input; the True-branch pattern's own three-valued `WHERE` already
   excludes it (SQL agrees with Alteryx there for free), but a False-branch translation that reads
   `WHERE REGION = 'WEST'` instead of the pattern above would silently lose that row — `compare.py`
   would classify the difference `NULL_SEMANTICS`.
2. **The two branches must be translated as a pair, not derived from one by prepending `NOT`.**
   `WHERE NOT (<True's condition>)` alone reproduces `False` but not NULL, because `NOT NULL` is
   still NULL — exactly the row `IS NULL` exists to catch. Both queries above are checked in the
   same test case so a fix to one that forgets the other fails loudly.
3. **A duplicate input row (`ID` 1 twice in the example) stays duplicated on whichever branch it
   lands on.** Filter never deduplicates, so a key on a non-unique column does not identify a row
   — this is why both streams here compare keyless (`"keys": []`). A contract that keys such a
   stream anyway is compared keyless all the same, with a `keys_not_unique` advisory in its report
   (live hardening, Task L11; it used to be a `GOLDEN_DATA` failure that had nothing to do with the
   translation).

## Config fields that change the pattern

- `expression`: the whole `WHERE` predicate; anything the expression language supports (program
  spec §8.4's function map) may appear, and a function the map does not cover is a parity risk to
  flag, not to guess at.
- Which output anchors are actually wired downstream: an unwired `False` anchor still needs a
  correct translation if any later validation checks it, since the oracle always computes both.

## Do not  (known wrong translations)

- Do not write the False branch as `WHERE NOT (<expression>)`; use
  `WHERE NOT (<expression>) OR (<expression's operand>) IS NULL` so NULL rows are included.
- Do not assume an unwired anchor can be skipped in the procedure; if nothing downstream reads it,
  say so in `translation_notes.md` instead of inventing an empty result.
- Do not fold a Filter into its upstream `WHERE` clause without keeping both branches available if
  anything downstream consumes the `False` output — folding only the `True` path silently discards
  a stream the contract may still need.
