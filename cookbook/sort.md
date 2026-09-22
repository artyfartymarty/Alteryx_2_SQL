# Sort  (plugin: AlteryxBasePluginsGui.Sort.Sort)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Orders rows by one or more fields, each ascending or descending, stably — equal rows keep their
incoming relative order (`docs/reference/dag-contract.md` §4). **NULL sorts first on ascending and
last on descending**, for every key independently; strings sort by code point (dictionary order is
not the default). Multi-key sorting applies the last-listed key as the least significant and the
first-listed key as most significant, exactly like a SQL `ORDER BY a, b, c` — earlier keys win ties
before later ones are consulted at all.

Sort's own output has the *same rows and the same values* as its input; only their order changes.
That has a direct consequence for how this tool is verified: a **set**- or **multiset**-based
comparison (which is what `scripts/compare.py` does whenever a stream has no usable key, and Sort's
output almost never does — see index.md's "Local verification" note) cannot see row order at all.
Sort is "only meaningful when a downstream tool is order-dependent" (index.md's tool map) in a very
literal sense: the *data* proves a Sort translation right or wrong only once a Record ID, Unique,
Sample, Multi-Row Formula, or Summarize `First`/`Last` downstream turns that order into a value.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2: Sort -- AMOUNT descending then ACCT ascending. Alteryx sorts NULL first ascending and
-- last descending, which the explicit NULLS clauses spell out.
WITH t2_sort AS (
    SELECT
        ACCT,
        AMOUNT
    FROM MIG_COOKBOOK.IN_1
    ORDER BY AMOUNT DESC NULLS LAST, ACCT ASC NULLS FIRST
)
SELECT
    ACCT,
    AMOUNT
FROM t2_sort
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **This page's own executable example cannot, by itself, prove the `ORDER BY` is correct.**
   `tests/cookbook_examples/sort/` compares this stream keyless, which is a row-*multiset*
   comparison — it verifies the pattern selects the right rows, with the right NULL handling on
   `AMOUNT`, `ACCT`, and neither drops nor duplicates anything, but a multiset has no concept of
   sequence, so it cannot fail on a wrong or missing `ORDER BY`. `record_id.md` and `unique.md`'s
   own examples are what actually exercise sequence-sensitive translation end to end, because their
   outputs encode order into a value (`compare.py`'s module docstring — data, not agents, produce
   every number this program trusts).
2. **`NULLS LAST`/`NULLS FIRST` must be spelled out explicitly**, never assumed from the dialect's
   default — Snowflake's own default NULL ordering is `NULLS FIRST` ascending and `NULLS LAST`
   descending, which happens to already match Alteryx, but relying on an unstated default is still
   a claim about an engine's implicit behavior rather than a checked translation; the example puts
   a NULL in each of the two sort keys precisely so the explicit clauses have something to prove.
3. **A composite sort's key order in `ORDER BY` must match Alteryx's configured order exactly**,
   first listed = most significant; swapping two keys produces a different, and generally silently
   wrong, tie-breaking order that only shows up once a downstream tool makes it observable (Parity
   risk 1 again).

## Config fields that change the pattern

- `fields[].order`: `asc`/`desc` per key, independently — a workflow can sort one field ascending
  and another descending in the same tool.
- The **listed order of `fields[]`** is the `ORDER BY` clause's own column order — first is most
  significant.

## Do not  (known wrong translations)

- Do not omit `NULLS FIRST`/`NULLS LAST`, even where today's dialect default happens to agree with
  Alteryx — a dialect default is not a documented contract the way an explicit clause is.
- Do not assume a keyless `compare.py` PASS on a Sort's own stream proves its `ORDER BY` is right;
  it only proves the values are right (Parity risk 1). Check a downstream order-dependent tool's
  result instead when the question is really about sequence.
- Do not reorder the `fields[]` list "for readability" when translating; the listed order is
  semantically the sort's significance order, not a stylistic choice.
