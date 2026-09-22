# Task 6 — SQL runtime — implementation report

Worktree: `.worktrees/task-6`, branch `wt/task-6`, commit `74db82f`.

## What I implemented

Six new modules and four test files, exactly the list the brief names. Nothing outside them was
touched.

### `scripts/lib/types_map.py`

`alteryx_to_snowflake(field)` and `type_family(sql_type)` per the brief. An Alteryx type the map
does not cover raises `ValueError` naming it rather than falling back to `VARCHAR` — the plan's
"scripts never guess" rule. `type_family` strips the parenthesised part and matches a base-name
table, so `TIMESTAMP_NTZ` is `timestamp` and `TIME` is `time` (ordering is by exact base name, not
by prefix, so there is no `TIME`/`TIMESTAMP` collision).

### `scripts/lib/backend.py`

`SANDBOX_DB`, `local_name`, `BackendError`, `DuckDBBackend`, `SnowflakeBackend`, `get_backend`.

`translate` parses with `sqlglot.parse_one(sql, read="snowflake")`, applies the AST transforms
below, and generates with `dialect="duckdb"` **and `unsupported_level=ErrorLevel.RAISE`**. That
last choice is the main judgement call in this module: by default sqlglot logs a warning and
silently emits degraded SQL (for example dropping a `TO_CHAR` format). Raising turns every such
case into a `BackendError` carrying the original statement, so a construct we have not handled
cannot produce quietly wrong parity results. It also keeps test output pristine — no stray sqlglot
warnings, which `test_translate_emits_no_sqlglot_warnings` pins with `caplog`.

Four AST transforms, each with a test:

| Transform | Why sqlglot alone is not enough |
|---|---|
| `CATALOG.SCHEMA.TABLE` → `CATALOG__SCHEMA.TABLE` | DuckDB catalogs are attached files, not namespaces. CTE names are collected across the statement and such tables are left untouched, per the brief. |
| drop `exp.TransientProperty` | DuckDB has no `TRANSIENT`; sqlglot would otherwise warn/raise "Unsupported property transientproperty". |
| `exp.ToNumber(safe=True)` → `exp.TryCast(... AS DECIMAL(p,s))` | sqlglot renders `TRY_TO_NUMBER` as a plain `CAST` for DuckDB, which *raises* on bad input instead of returning NULL — the exact opposite of the `TRY_` contract. This is what makes the brief's own `TRY_TO_NUMBER('abc') IS NULL` assertion pass. |
| `exp.ToChar(format=…)` → `STRFTIME(x, '<strftime>')` | sqlglot drops the format argument for DuckDB (`CAST(x AS TEXT)`). A Snowflake→strftime token map (`YYYY MM DD HH24 HH12 MI SS MON MMMM DY FF3 AM/PM …`) is applied; an element not in the map raises `BackendError` naming it rather than emitting a format that would print different text from Snowflake. |

`execute` runs `CREATE SCHEMA IF NOT EXISTS` for the local schema of any `CREATE TABLE/VIEW` or
`MERGE` target before running the statement (verified for all four shapes).

`load_table` builds `CREATE OR REPLACE TABLE <fqn> ("COL" <snowflake type>, …)` from
`alteryx_to_snowflake` and routes it through `execute`, so the DuckDB equivalent comes from the
same translation path as everything else: a `FixedDecimal(19,2)` column really is `DECIMAL(19,2)`,
`Date` is `DATE`, `DateTime` is `TIMESTAMP` (`test_load_table_creates_typed_columns` asserts the
whole map). Rows go in with `executemany`; ISO date/time strings (contract C1) are cast by DuckDB.

**Documented limitation** (module docstring): DuckDB accepts `VARCHAR(20)` and does not enforce the
length. Snowflake would raise on overflow; the local runtime cannot reproduce that. Also documented:
Snowflake upper-cases unquoted identifiers while DuckDB preserves the case written, so
`table_columns` reports names exactly as DuckDB has them and callers compare case-insensitively.

`SnowflakeBackend` imports `snowflake.connector` lazily and raises
`BackendError("snowflake-connector-python is not installed")` when absent. Its docstring says
plainly that it has never been run and should be treated as unverified.

### `scripts/lib/proc_runner.py`

`ProcInfo`, `ProcError`, `parse_proc`, `bind`, `run_proc`.

Quoting is handled by one small scanner (`_next_span`) that classifies each span of text as code,
string or comment, understanding `''`, `""` and Snowflake's backslash escapes. Statement splitting,
`ALTER SESSION` assignment splitting, `||` splitting and parameter substitution all reuse it, which
is why the brief's adversarial PROC works: a `;` and a `'` inside a `--` comment, a `;` inside a
string literal, and a literal `':SRC_DB'` that must not be substituted.

`bind` substitutes only parameters present in `args` (case-insensitively), skips `::` casts, then
folds `IDENTIFIER(<literals joined by ||>)`; any other `IDENTIFIER(` form raises `ProcError`.
Values are emitted as SQL string literals with quotes doubled.

`run_proc` additionally raises `ProcError` naming any declared parameter with no supplied argument
— behaviour the brief describes only implicitly, but a silent `:TGT_SCHEMA` left in the SQL would
be a confusing downstream failure.

### `scripts/load_golden.py`, `scripts/gen_source_views.py`

`load_set` / `load_intermediate` / `production_views_sql` as specified, plus a small `--root` CLI
each (Global Constraint: every script takes `--root`). `load_golden.py` defaults its DuckDB file to
`workflows/<wf>/.sandbox.duckdb` (contract C3) and prints the returned dict as JSON;
`gen_source_views.py` prints the DDL or writes it with `--out`, with a header comment saying the
DDL has not been executed against a Snowflake account.

### `scripts/compile_check.py`

The five steps in order, writing `segments/<seg>/compile_check.json`, CLI exit 0 / 1 / 2.
sqlglot's ANSI underlining is stripped from error text before it goes into JSON.

## Brief corrections / clarifications

1. **Statements are parsed as Snowflake *after* binding, not before.** The brief's step (2) says
   "every statement must parse as Snowflake with no `exp.Command` node". `INSERT INTO
   IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.ITEMS_OUT') SELECT …` — which is contract C4's
   own shape for a final target, and appears verbatim in the brief's `PROC` — does **not** parse in
   sqlglot 30.18 in its unbound form (`ParseError: Expecting ). Line 1, Col: 33`). The same
   statement parses cleanly once `bind` has folded the `IDENTIFIER(…)`. `compile_check` therefore
   binds first and parses the bound statement, which is also the text that will actually run.
   (`MERGE INTO IDENTIFIER(…)` and `FROM IDENTIFIER(…)` do parse unbound; only the `INSERT INTO`
   target position fails.) No test in the brief changed.

2. **`load_set` also requires `logical` on outputs.** The brief states the rule only for sources.
   An output without a `logical` name has no discoverable `targets_before/<set>/<LOGICAL>.csv` and
   no table to write into, so it raises the same `ValueError` naming the tool id. Contract C6 puts
   `logical:` on every source *and* output, so this is consistent rather than an extension.

3. **`load_set` with several `tool_ids` on one source** loads a golden table per tool id (contract
   C2 keys inputs by tool id) but points the single logical view at the first of them, since they
   are by definition the same file. Commented in the code.

4. **`parse_proc` rejects a nested `BEGIN`.** The brief lists `LET DECLARE IF FOR WHILE EXECUTE
   IMMEDIATE CALL`. A `BEGIN` after the leading one is equally outside contract C4 (it is a
   Scripting block), and treating it as a statement would send `BEGIN` to the backend. It raises
   the same "outside the supported procedure subset" error.

5. **`execute_as` defaults to `"OWNER"`** when the header has no `EXECUTE AS` clause, because
   owner's rights is Snowflake's documented default. Contract C4 mandates `CALLER`; enforcing that
   is the reviewer's job, not the parser's.

## Snowflake constructs verified

Each of these is executed on DuckDB in a test and checked against a hand-computed value. **All of
them work; none had to be given up.**

`IFF` · `TRY_TO_NUMBER` (0-arg and with precision/scale) · `TRY_TO_DOUBLE` ·
`TRY_TO_DATE(x,'DD/MM/YYYY')` (match and non-match) · `TO_VARCHAR`/`TO_CHAR(date,'YYYY-MM-DD')` and
`TO_CHAR(ts,'YYYY-MM-DD HH24:MI:SS')` and the no-format form · `LEFT` · `LPAD` · `UPPER` · `TRIM` ·
`REGEXP_REPLACE` with `\\1`/`\\2` backreferences · `REGEXP_SUBSTR` with a group argument ·
`DATEADD` · `DATEDIFF` · `QUALIFY ROW_NUMBER() OVER (…)` ·
`SUM() OVER (PARTITION BY … ORDER BY … ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)` ·
`LISTAGG(x,'|') WITHIN GROUP (ORDER BY …)` · `COUNT_IF` · `NULLIF` · `IS DISTINCT FROM` ·
`UNION ALL` · `MERGE` (matched update + not-matched insert, three-part names both sides) ·
`DELETE` · `UPDATE` · `INSERT INTO … (cols) SELECT` · `CREATE OR REPLACE TRANSIENT TABLE … AS` ·
`ROUND(CAST(x AS NUMBER(38,10)), 2)`.

Two of these needed the AST transforms described above (`TRY_TO_NUMBER`, `TO_CHAR` with a format);
one needed the `TRANSIENT` property removal. The rest transpile correctly out of the box in
sqlglot 30.18 / DuckDB 1.5.5.

## TDD evidence

**RED** — all four test files written first, then:

```
$ .venv/Scripts/python.exe -m pytest tests/test_backend.py tests/test_proc_runner.py \
      tests/test_load_golden.py tests/test_compile_check.py
tests\test_load_golden.py:4: in <module>
    from lib.backend import DuckDBBackend
E   ModuleNotFoundError: No module named 'lib.backend'
tests\test_compile_check.py:10: in <module>
    import compile_check as cc
E   ModuleNotFoundError: No module named 'compile_check'
ERROR tests/test_backend.py
ERROR tests/test_proc_runner.py
ERROR tests/test_load_golden.py
ERROR tests/test_compile_check.py
!!!!!!!!!!!!!!!!!!! Interrupted: 4 errors during collection !!!!!!!!!!!!!!!!!!!
4 errors in 0.14s
```

Expected: the brief's step 2 says "Expected: FAIL on import" — none of the six modules existed yet.

**GREEN** — after implementing, the four task files with warnings promoted to errors:

```
$ .venv/Scripts/python.exe -m pytest tests/test_backend.py tests/test_proc_runner.py \
      tests/test_load_golden.py tests/test_compile_check.py -W error -p no:cacheprovider
77 passed in 2.21s
```

Whole suite, confirming nothing regressed in tasks 1–4:

```
$ .venv/Scripts/python.exe -m pytest
243 passed in 2.69s
```

Breakdown: `test_backend.py` 39, `test_proc_runner.py` 21, `test_compile_check.py` 9,
`test_load_golden.py` 8 (the last also covers `gen_source_views.py`, since the brief's file list
has no separate test file for it and both read `intake/mappings.yaml`).

## Files changed

All new; nothing existing was modified.

```
scripts/lib/types_map.py
scripts/lib/backend.py
scripts/lib/proc_runner.py
scripts/load_golden.py
scripts/gen_source_views.py
scripts/compile_check.py
tests/test_backend.py
tests/test_proc_runner.py
tests/test_load_golden.py
tests/test_compile_check.py
```

## Self-review findings (fixed before committing)

- `query` originally evaluated `_prepare(sql)` twice through a leftover expression; collapsed to one
  call.
- Unused `re` import in `backend.py`, unused `seg_token` import in `load_golden.py`; removed.
- `SnowflakeBackend.load_table` used `?` placeholders; the connector's default paramstyle is
  pyformat, so it now uses `%s` with a comment saying the whole class is unverified.
- The unsupported-format error pointed at a vague "types/backend format map"; it now names
  `_FORMAT_TOKENS in scripts/lib/backend.py`.
- A test table was named `COPY`, a DuckDB keyword; renamed to avoid testing sqlglot's quoting
  instead of the construct under test.

## Concerns

1. **`VARCHAR(n)` overflow cannot be reproduced locally.** Stated in the brief, documented in the
   module docstring, repeated here: a Select-tool truncation that Snowflake would reject as a
   string-too-long error will pass silently through the local runtime, and only surface in
   `compare.py` as a `TRUNCATION` diff if the golden data happens to exercise it. This is the
   sharpest edge of the DuckDB double.

2. **The CTE-shadowing rule can produce SQL DuckDB rejects.** The brief says never to touch a table
   whose name matches a CTE alias. If a statement has a CTE named `GL` *and* also reads a real
   three-part `FIN.RAW.GL`, the real table is left unflattened and DuckDB will fail on the unknown
   catalog. Implemented as specified, and tested
   (`test_translate_does_not_qualify_a_cte_that_shadows_a_table_name`) so the behaviour is visible
   rather than accidental. CTE names are collected across the whole statement, not per scope — the
   conservative direction, and noted in the docstring.

3. **`unsupported_level=RAISE` is stricter than the brief asked for.** A Snowflake construct beyond
   the list above that sqlglot can parse but not express in DuckDB will now raise a `BackendError`
   where the default would have emitted degraded SQL and logged a warning. I believe loud is right
   for a parity harness, but later tasks adding new constructs should expect an error, not a
   warning, and fix it with a transform here plus a test.

4. **`SnowflakeBackend` is unexercised code.** Every method is written against the documented
   connector API and none has run. It is marked as such in its docstring and in the commit message.

## Fix round 1 (written by the controller: the implementer session was killed by a usage limit after committing and before reporting)

Commit `2c40f71` on `wt/task-6-fix`: fix: enforce contract C4 signatures, flatten qualified names, harden CLIs.
Files: mappings/global.yaml, scripts/compile_check.py, scripts/gen_source_views.py, scripts/lib/backend.py,
scripts/load_golden.py, tests/test_backend.py, tests/test_compile_check.py, tests/test_load_golden.py (+250/-27).

Controller verification, run in `.worktrees/task-6-fix`:
    "<main>/.venv/Scripts/python.exe" -m pytest -q -W error
    -> all tests passed (141 dots, no warnings, no failures)

Covering tests observed in the diff: `test_round_on_an_exact_number_goes_half_away_from_zero` (6 cases incl. 1.005 and
-1.005, the pair that separates half-away from banker's and from binary-float rounding),
`test_round_without_the_cast_rounds_the_stored_binary_value_instead`, a QUALIFY test with duplicate partition keys,
qualified-name flattening despite a same-named CTE, compile_check signature violations, load_golden exit codes.
RED evidence is not available (the session died before recording it).
