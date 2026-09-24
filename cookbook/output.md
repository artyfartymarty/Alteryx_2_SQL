# Output Data  (plugin: AlteryxBasePluginsGui.DbFileOutput.DbFileOutput)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Writes the incoming records to a file or a database table (`docs/reference/dag-contract.md` §4).
Four write modes: `Overwrite`/`Create New Table`/`Overwrite Table (Drop)` replace the target
entirely with the incoming schema and data; `Append Existing` inserts every incoming row as-is;
`Update; Insert if new` updates the non-key columns of rows whose keys match and inserts the rest,
leaving any target-only column NULL on an inserted row; `Delete Data & Append` empties the target
first, then inserts. Columns are matched to the target **by name**, not position — an incoming
column the target does not have is silently not written. A NULL update key never matches an
existing row (the same rule a SQL `=` gives a translated `MERGE`), so such a row is always inserted,
never updated. `PreSQL` and `PostSQL` run, in the source database's own dialect, before and after
the write respectively. A file target (or any target the run has no prior state for) simply becomes
the incoming table.

## Snowflake pattern  (SQL, with placeholders)

An Output tool is a **write**, not a computation, so — unlike every other tool on this page's
siblings — it has no CTE of its own in a real procedure; it is the statement itself
(`samples/wf_0001/canned/segments/seg_01/translation_notes.md`'s "no CTE" convention). This page's
own runnable example shows `Overwrite`, the common case:

```sql
-- tool 2: Output Data (write mode Overwrite, logical CUSTOMERS_OUT) -- an Output tool is a write,
-- not a computation: it has no CTE of its own (translation_notes.md's "no CTE" convention), it is
-- this CREATE OR REPLACE TABLE statement whose SELECT ends at the upstream CTE.
CREATE OR REPLACE TABLE MIG_COOKBOOK.OUT_2 AS
SELECT
    CUST_ID,
    NAME,
    TIER,
    CREDIT_LIMIT
FROM MIG_COOKBOOK.IN_1
```

In a real segment procedure the target's name is built once at the top of the body,
`LET <LOGICAL>_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.<LOGICAL>';`, and written through
`IDENTIFIER(:<LOGICAL>_TGT)` (contract C4), never a literal Snowflake table name — see
`samples/wf_0001/canned/segments/seg_01/proc.sql`'s `LET` block and its two `CREATE OR REPLACE TABLE
IDENTIFIER(:…_TGT) AS` statements. Snowflake documents `IDENTIFIER(` with one value -- a string literal, session variable, bind variable or Snowflake Scripting variable -- not an expression, which is why the name is built first. Inside the `LET` the procedure's arguments are named without a colon (Snowflake's expression syntax); the colon binds a variable inside a SQL statement, which is why `IDENTIFIER(:<LOGICAL>_TGT)` keeps it. This is the documented form; nothing here has run on Snowflake, and the first real-account run confirms it. `Append Existing` is an `INSERT INTO … (<named columns>) <select>`
(`samples/wf_0002/canned/segments/seg_03/proc.sql`'s tool 10, which names every column explicitly
because Alteryx maps by name); `Update; Insert if new` is a `MERGE` on the configured keys. Neither
of those two is exercised by this page's own executable example — see Parity risk 3.

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **`tests/cookbook_examples/output/` only exercises `Overwrite`.** The oracle
   (`scripts/dev/alteryx_sim.py`'s `sim_output`) replays `Append Existing` and `Update; Insert if
   new` by loading the target's prior state into an in-memory database, running `PreSQL`, applying
   the write mode, then `PostSQL` — but this cookbook page's one-tool harness has no prior target
   state to give it (see the case.json format `tests/test_cookbook_examples.py` reads). The pattern
   above is checked; the write-mode branches below are not checked by *this* example, only by the
   full hand migrations cited above.
2. **A NULL update key inserts rather than updates or errors.** A translated `MERGE ... WHEN
   MATCHED ... ON target.K = source.K` behaves the same way SQL always does with a NULL comparison —
   silently correct, but easy to assume is a bug when reviewing the SQL in isolation.
3. **Column mapping is by name.** A Formula or Select upstream that renames a column to something
   the target table does not have means that column is quietly dropped from the write, not an
   error — `docs/reference/simulator-semantics.md` §8 states it and `scripts/dev/alteryx_sim.py`'s
   `_write_to_database` warns about it, but a warning is not a failure, so nothing stops the run.

## Config fields that change the pattern

- `write_mode`: `overwrite` → `CREATE OR REPLACE TABLE … AS`; `append`/`truncate_append` →
  `INSERT INTO … (cols) SELECT …` (the second preceded by a `DELETE FROM`/`TRUNCATE`); `update_insert`
  → `MERGE … WHEN MATCHED THEN UPDATE … WHEN NOT MATCHED THEN INSERT …` on `keys`.
  `scripts/compile_check.py` holds every final target to exactly this form (`c4:write_mode`, reading
  the contract's `write_mode`, else `intake/mappings.yaml`'s `mode`, where `merge` is `update_insert`):
  one such write of `IDENTIFIER(:<LOGICAL>_TGT)`, the `MERGE` on exactly the contract's keys, and no
  other statement on the target but the tool's own PreSQL before it and PostSQL after it. A
  `truncate_append` target, like an append or update_insert one, needs its before-state in the golden
  data (`golden/targets_before/<set>/<LOGICAL>.csv`, `docs/handoff-production.md`): its `TRUNCATE`
  needs the table to exist, and `scripts/load_golden.py` creates a target only from that file.
- `keys`: the `MERGE`'s `ON` clause for `update_insert`; meaningless for the other three modes.
- `pre_sql` / `post_sql`: separate statements run before/after the write, in the procedure's own
  statement list — never inlined into the write statement itself.
- `format`: a file target (`yxdb`/`csv`/`xlsx`) becomes a stage `COPY INTO` or an artifact this
  program does not manage at all; a `db` target is the pattern above.

## Do not  (known wrong translations)

- Do not write the target by its literal Snowflake name; use `IDENTIFIER(:<LOGICAL>_TGT)` after
  `LET <LOGICAL>_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.<LOGICAL>';` (contract C4) exactly
  as every source read does, and never an expression inside `IDENTIFIER(…)`
  (`scripts/compile_check.py`'s `c4:identifier_expression`).
- Do not write an `overwrite` target as `TRUNCATE` + `INSERT INTO`: an overwrite replaces the table
  with the incoming schema and data, and the table need not exist beforehand -- the golden sets hold
  no prior state for an overwrite target (`golden/targets_before/` is exported only for a target that
  keeps rows or whose write needs the table: append, update_insert, truncate_append, or one with a
  PreSQL/PostSQL), so the validator creates none and the `TRUNCATE` fails where `CREATE OR REPLACE
  TABLE … AS` would not. `c4:write_mode` refuses it.
- Do not translate `Update; Insert if new` as two separate statements (`UPDATE` then `INSERT ...
  WHERE NOT EXISTS`) instead of one `MERGE` unless the target engine cannot express `MERGE` — the
  two-statement form is not atomic and can double-count a row that a concurrent writer inserts
  between them.
- Do not assume `SELECT *` maps columns correctly into the target; name every column explicitly,
  because Alteryx's own column mapping is by name and a same-position, different-name column would
  silently write into the wrong place.
