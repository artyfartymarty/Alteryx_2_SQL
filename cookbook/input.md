# Input Data  (plugin: AlteryxBasePluginsGui.DbFileInput.DbFileInput)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Reads a file (yxdb, csv, xlsx) or a database query and emits it on `Output`, typed by the tool's own
field list (`meta.Output` in `dag.json` — `docs/reference/dag-contract.md` §1/§4). Values keep
whatever the source already had: a NULL cell stays NULL, a duplicate row stays duplicated, and no
reordering happens here — order is whatever the source produced it in (a CSV's row order, a yxdb's
storage order, or a database's own, which is not guaranteed unless the query itself sorts).

CSV specifics: header row on/off, delimiter and code page are configuration (`config.csv`); a
missing value is empty text, not NULL, unless the column's declared type coerces it (contract C1
uses a different rule for *golden data*, `\N`, because a CSV without a schema sidecar has no other
way to say NULL). Excel: sheet name, header row, and Excel's own type inference per column. A
database source's query text is kept verbatim in `config.query` and its `FileFormat`/alias identify
the connection (dag contract §4); nothing here is guessed.

## Snowflake pattern  (SQL, with placeholders)

The mapped source is read by its **logical** name (plan contract C6), never by the Snowflake table
the translator resolved it to — `IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.<LOGICAL>')` (contract
C4). In this page's own example there is no procedure around the fragment, so it reads the cookbook
harness's own input table directly; a real procedure's CTE differs only in the `FROM`:

```sql
-- tool 1: Input Data -- the orders extract, read by its logical name (mappings.yaml, contract C6).
WITH t1_input AS (
    SELECT
        ORDER_ID,
        CUSTOMER,
        REGION,
        AMOUNT
    FROM MIG_COOKBOOK.IN_1
)
SELECT
    ORDER_ID,
    CUSTOMER,
    REGION,
    AMOUNT
FROM t1_input
```

In a real segment procedure, `FROM MIG_COOKBOOK.IN_1` becomes
`FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS')` — see
`samples/wf_0001/canned/segments/seg_01/proc.sql`'s `t1_input` CTE for the pattern actually used in a
hand migration, checked the same way this page's own example is.

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **Column order and typing come from the tool's own field list, not from the source.** A source
   whose physical column order differs from `meta.Output` still emits `meta.Output`'s order; the
   parser is the thing that resolved that mismatch, not this SQL. `tests/cookbook_examples/input/`
   demonstrates the tool passing NULL, a duplicate row, and a boundary value (`AMOUNT = 0.0`)
   through unchanged — nothing here transforms a value, so any transformation seen downstream did
   not happen at this tool.
2. **CSV NULL vs empty string is a real distinction Alteryx keeps**, and it is easy to translate
   wrong: an empty CSV cell is `''`, not `NULL`, unless the target column's Snowflake type coerces
   it. Golden data's `\N` token (contract C1) is a *test-fixture* convention, not something a real
   CSV file contains — a real `COPY INTO` needs its own `NULL_IF` setting.
3. **A database source's row order is whatever the query returns**, which SQL does not guarantee
   without an `ORDER BY`. A downstream tool that depends on order (Sample, Record ID, Unique's
   "first" row, Multi-Row Formula) inherits this tool's nondeterminism unless a Sort is inserted.

## Config fields that change the pattern

- `format`: `yxdb`/`csv`/`xlsx` read from a stage with `COPY INTO`; `db` reads `config.query`
  verbatim (translated to Snowflake dialect if the source wasn't already Snowflake) or a plain table
  reference.
- `csv.delimiter` / `csv.header` / `csv.codepage`: `COPY INTO`'s `FILE_FORMAT` options; `codepage`
  decides whether a `COPY INTO` needs an explicit `ENCODING`.
- `record_limit`: becomes a `LIMIT`, applied *after* whatever order the source has — the same
  ordering caveat as Parity risk 3 applies to which rows get kept.

## Do not  (known wrong translations)

- Do not read the mapped Snowflake table by its literal name. Every source is read through
  `IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.<LOGICAL>')` (contract C4); a literal reference is
  a blocking reviewer finding (`.github/agents/reviewer.agent.md`).
- Do not add an `ORDER BY` that was not in the original query just to make output "look" stable —
  that changes behavior a downstream Sample/Unique/Record ID tool depends on, silently.
- Do not treat an empty CSV cell as NULL without checking the target column's coercion rule; the
  two are different values with different downstream behavior (`IsNull` vs `IsEmpty`,
  `docs/reference/simulator-semantics.md` §4).
