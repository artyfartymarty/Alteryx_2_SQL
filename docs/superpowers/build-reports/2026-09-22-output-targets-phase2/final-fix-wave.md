# Phase-2 final fix wave — rulings on the final whole-branch review

Base: the integration head `a7aa602` (every phase-2 task merged, P5 included). Worktree `.worktrees/p2-ffix`, branch
`wt/p2-ffix`. Read `final-review-report.md` (beside this file) first; its C1 section is the evidence, including the
verified failure scenario. Everything below is RULED — implement it RED-first (a failing test before each fix, the RED run
recorded in your report). Rules of the house: `implementer-rules.md`.

## C1 (Critical) — the dbt project is a CLOSED surface, enforced before dbt ever runs

The design promises that a dbt migration project is a closed surface (spec §4.3; Task A's `dbt:model_jinja` allow-list):
only `config(...)`, `source('src', …)`, `ref(…)`, `this`, the `is_incremental` if/else, and comments. The review proved
that a Python model, an `on-run-start`/`on-run-end` hook, or a `pre_hook`/`post_hook` carrying arbitrary SQL all pass
`compile_check.py --target dbt` and RUN during `validate_dbt.py`. The same class covers a model's own SQL: a SELECT can
call DuckDB file/table functions (`read_csv('…')`, `read_text('…')`, `glob(…)`) or name a raw table without `source()`,
and nothing inspects table references. Rulings:

### C1.1 One choke point: `scripts/lib/dbt_project.py` `check_surface(project) -> list[str]`
- `run_dbt` calls `check_surface` FIRST, before building any argument or starting any process. If it returns errors,
  no dbt subprocess starts: `run_dbt` raises a new `DbtUnsafe` exception (a `ValueError` subclass) whose message lists
  the errors (bounded, never quoting file content beyond the offending construct). Every caller of `run_dbt`
  (`compile_check.py`, `validate_dbt.py`, and any other — grep) is therefore gated, whoever runs it.
- `compile_check_dbt` runs `check_surface` with the other file-level checks and reports its errors as `dbt:surface: …`
  (and the finer names below); when any surface error exists it does NOT call `dbt parse` at all (report ERROR, exit 1,
  as for any compile error).
- `validate_dbt.py`: a project that fails the gate never produces a PASS: every segment FAILs naming `dbt:surface`, the
  chain report is FAIL, the exit code is 1 (a domain failure, like a failed `dbt run`); no dbt process starts. A test
  proves "no process started" (e.g. monkeypatch `subprocess.run` to fail the test if called).

### C1.2 The closed file set (`dbt:surface`)
The project directory may contain ONLY: `dbt_project.yml`, `profiles.yml`, `README.md`, `translation_notes.md`,
`fix_log.md`, `compile_check.json`, `review.json`; `models/**/*.sql`; exactly `models/sources.yml` and
`models/schema.yml`; and the git-ignored OUTPUT directories `logs/` and `target/` (never read as input: `run_dbt` points
`--target-path`/`--log-path` elsewhere). Anything else is refused by relative path: any `.py` anywhere, any other `.yml`/
`.yaml`/`.csv`/`.md` under `models/`, `macros/`, `snapshots/`, `seeds/`, `analyses/`, `tests/`, `dbt_packages/`,
`packages.yml`, `dependencies.yml`, `package-lock.yml`, `selectors.yml`, any symlink or junction. Check empirically what a
real `dbt parse`/`dbt run` (through `run_dbt`, on a scratch copy of `wf_0007`) leaves in the project directory (e.g. a
`.user.yml`); allow such a file by exact name only if dbt itself writes it there, and say so in the report.

### C1.3 `dbt_project.yml` is the template (`dbt:project_yml`)
Keys exactly `{name, version, config-version, profile, model-paths, vars}`; `vars` keys exactly `{src_schema,
tgt_schema}`; no Jinja delimiter (`{{`, `{%`, `{#`) anywhere in the file. So `on-run-start`, `on-run-end`, `models:`
(and its `+pre-hook`/`+post-hook`/`+sql_header`), `dispatch`, `*-paths` other than `model-paths`, `flags`,
`query-comment`, … are all refused. Keep the existing name/profile/model-paths checks.

### C1.4 The two YAML files are closed (`dbt:yaml`)
- `models/sources.yml`: `version`, `sources`; a source's keys ⊆ `{name, schema, description, tables}`, a table's ⊆
  `{name, description, columns}`, a column's ⊆ `{name, description}`. The ONLY Jinja permitted anywhere in the file is
  a `schema` value exactly equal to `{{ var('src_schema') }}` (whitespace inside the braces tolerated). Derive and
  confirm the allowed sets against `samples/wf_0007/canned/dbt/`, the template Task A wrote, `cookbook/dbt.md` and
  `tests/cookbook_examples/dbt/**`; if a committed example needs another key, allow that key and say which file needed it.
- `models/schema.yml`: `version`, `models`; a model's keys ⊆ `{name, description, columns}`, a column's ⊆ `{name,
  description, data_tests, tests}`; tests only dbt's built-in generic tests (`not_null`, `unique`, `accepted_values`,
  `relationships`) with literal arguments; no `config:` block anywhere; no Jinja anywhere.
- dbt renders Jinja in YAML at parse time (a `description: "{{ run_query(…) }}"` executes), which is why "no Jinja"
  is the rule, not an allow-list of calls.

### C1.5 Model `config(...)` keyword arguments are closed (`dbt:model_jinja`)
Keys ⊆ `{materialized, incremental_strategy, unique_key, alias, pre_hook, post_hook}` (the keys
`dbt_project.expected_model_config` produces, plus the two hooks the PreSQL/PostSQL rule needs); every value a string
literal or a list of string literals. So `sql_header`, `database`, `schema`, `grants`, `post-hook`-style spellings,
`on_schema_change`, `full_refresh`, `meta`, … are refused. Parse the kwargs properly (Python's `ast` on the argument
text, with the nested `{{ this }}` scrubbed first, is acceptable); a malformed argument list is refused.

### C1.6 Hook SQL is judged (`dbt:hook_sql`)
PreSQL/PostSQL stay supported (cookbook/dbt.md, the translator and reviewer agents, `dbt:hooks`). Each `pre_hook` /
`post_hook` value must be ONE string literal holding ONE SQL statement whose only Jinja is `{{ this }}`. With
`{{ this }}` replaced by a placeholder identifier, parse it with sqlglot (fail closed: unparseable → refused) and require:
- the statement is a `DELETE`, `UPDATE`, `INSERT` or `TRUNCATE`;
- every table reference in it (including inside subqueries) is the placeholder — no other table, no table-valued
  function, no CTE that reads anything else;
- no function whose name is on a file/env/network deny-list (at least `read_*`, `*_scan`, `glob`, `getenv`,
  `current_setting`, `sniff_csv`, `parquet_*`, `load*`, `install*`, `http*`, `query`, `query_table`), and no sqlglot
  `Anonymous` function (a name sqlglot does not know) — fail closed.
- COPY, ATTACH, DETACH, INSTALL, LOAD, PRAGMA, SET, RESET, CALL, EXPORT, IMPORT, CREATE, DROP, ALTER, GRANT, USE, and any
  multi-statement string are refused.
A PreSQL/PostSQL that genuinely touches another table therefore cannot be expressed on the dbt target: say so in
`docs/reference/output-targets.md`, `cookbook/dbt.md`, and the translator/reviewer agent files (route it to the SQL target
or a human); add it to `docs/production-backlog.md` only if it is not already covered there.

### C1.7 Model SQL reads only through `source()`, `ref()` and `{{ this }}` (`dbt:model_sql`)
Replace each allowed Jinja construct with a placeholder identifier (`source('src','X')` → a source placeholder,
`ref('m')` → a ref placeholder, `{{ this }}` → the this placeholder; drop the `config(...)` span and the
`is_incremental` tags, keeping both branches' text), parse the result with sqlglot (fail closed), and require: every
table reference is a placeholder or a CTE name defined in the same statement; no table-valued function; the same
function deny-list and `Anonymous` refusal as C1.6; exactly one top-level SELECT (a WITH … SELECT counts). Check every
committed dbt model (`samples/wf_0007/canned/dbt/**`, `workflows/wf_0007/dbt/**`, `tests/cookbook_examples/dbt/**`,
the cookbook's code blocks) passes, and that each broken `wf_0007` variant still FAILs with its recorded class
(`broken.json`).

### C1.8 Defence in depth in the policy (`orchestrator/policy.ts`)
Narrow the translator/fixer dbt write lanes from `dbt/models/**` to `dbt/models/**/*.sql` plus exactly
`dbt/models/sources.yml` and `dbt/models/schema.yml`; a write of `dbt/models/x.py`, `dbt/macros/x.sql`,
`dbt/packages.yml`, `dbt/models/extra.yml` is denied. The top-level file lane stays as it is. Node tests for each denial
and for each still-allowed path. POLICY.md says what the lanes are and that `compile_check`/`run_dbt` enforce the same set.

### C1.9 Tests (the review's scenario, committed)
In `tests/test_compile_check_dbt.py` / `tests/test_validate_dbt.py` / a new `tests/test_dbt_surface.py`, on scratch
copies of `wf_0007`: a `models/<m>.py` model; an `on-run-start` COPY; a `models:` block with `+post-hook`; a
`post_hook="COPY {{ this }} TO '<file>'"`; a `sql_header=`; a schema.yml `description: "{{ run_query('select 1') }}"`; a
`macros/x.sql` overriding `generate_alias_name`; a `packages.yml`; a model `select * from read_csv('<file>')`; a model
that reads a raw table without `source()`; a hook `DELETE FROM {{ this }} WHERE k IN (SELECT k FROM other)`. Each is
refused by compile_check with its check name, and — for every case that previously EXECUTED — `validate_dbt.py` starts no
dbt process and writes no PASS (assert the marker file the payload would have written does NOT exist). The committed
`wf_0007` and every cookbook dbt example still pass compile_check and validate_dbt.

## Other rulings (minor; same dispatch)

- **N-dbt (from the P5 review):** `translateDbt` (`orchestrator/stages.ts` ~1049) parks when the chain report says
  `needs_human`, even with a PASS verdict — reason `dbt: chain needs_human`, the same M6 rule `chainCheck` follows. A node
  test with a PASS + needs_human chain report.
- **M1:** `validate_dbt.py` and `validate_workflow.py` treat an `order.json` that flattens to no segment as a usage error
  (exit 2, nothing written), like a missing one. Tests.
- **M2:** `compile_check_dbt` raises `FileNotFoundError` naming the path when `intake/mappings.yaml` is missing (as it
  already does for contracts), and its docstring lists the prerequisites. Test (CLI exit 2).
- **M3:** the segmenter emits each over-cap group's "stays above its size cap" warning once, not once per outer iteration
  of step 6. Test.
- **Hand-off guide (carry-over 9):** in `docs/handoff-production.md` Rung 3 "Before you start", say that rung 1's run root
  is reused on purpose (rung 3 makes no hosted calls, so its stale hosted-profile ids do not matter), or build a fresh one
  — pick one and make §1.3 and Rung 3 agree. Keep `tests/test_handoff_production.py` green.

## Parked (no change; recorded in the ledger)
Carry-overs 1 (documented), 3 (duplicate zip-slip guard, hygiene), 5, 6, 8 — not load-bearing per the final review.

## Acceptance
Every C1.9 case refused and non-executing; committed trees unchanged unless a committed file itself violated the new
gate (then stop and report — do not rewrite committed `workflows/**` or `samples/**` without saying why); node, tsc and
the whole pytest suite green, 0 skipped. Commit in coherent steps (`fix: …`), then append a report to
`final-fix-report.md` beside this file: each item, its RED evidence, the files changed, test counts, and anything you
could not do.
