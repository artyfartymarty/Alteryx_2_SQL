# Task 4 review findings (reviewer verdict on f342cac, condensed)

Critical 1: `_read_back` (scripts/validate_snowpark.py:198-209) builds the actual table's fields from the CONTRACT's declared types (`_alteryx_for(c["type"])`, size/scale None), never from `session.table(fqn).schema`. A handler that casts `ID` (contract NUMBER(38,0)) to DoubleType yields verdict PASS, schema PASS (`_coerce` did int(12.0)). compare.py's schema check never sees the real type.
Critical 2: same root cause hides EXTRA actual columns: `names = [c["name"] for c in columns]` only reads contract columns; a handler adding a `SURPRISE` column -> PASS.
Important 3: a non-convertible actual value (`ID` written as "not-a-number") raises ValueError inside `_read_back`'s loop, propagates to main()'s `except (FileNotFoundError, ValueError)` -> parser.error -> exit 2, NO validation.json written. Should be a domain schema failure: exit 1 with a report.
Important 4: FixedDecimal: `_read_back` sets size/scale None; `alteryx_to_snowflake` renders "NUMBER(None,0)" -> DuckDB parser error on CREATE TABLE -> bare `except Exception` -> exit 2, no report. Contract `AMT NUMBER(19,2)` reproduces it.
Minor 5: `run_handler` has 7 args (contract added) vs the brief's 6. Disclosed; accepted.
Minor 6: `GOLDEN_SCHEMA` imported but unused (validate_snowpark.py:19).
Minor 7: the self-review named the FixedDecimal gap but not the schema-bypass class; calibration note only.
Verified clean: extraction is a verbatim move; validate_segment tests 31/31; validate_snowpark 20/20; `"target": "snowpark"` top-level only; clear_stale_reports first; compare.py untouched; scope exact; fresh session per run mirrors the SQL twin; missing-table FAIL; NOT NULL caught.
