# Phase-2 final fix wave — implementer report

Worktree `.worktrees/p2-ffix`, branch `wt/p2-ffix`, from the integration head `a7aa602`. Every item of
`final-fix-wave.md` is done, RED first (new tests written and run before any fix; RED output kept in the
session scratch directory, quoted below).

## Commits

| sha | subject |
|---|---|
| `e612696` | fix: dbt lanes admit only SQL models and the two YAML files; a dbt chain report saying needs_human parks translate (C1.8, N-dbt) |
| `cdeb5d7` | fix: the segmenter warns once about a group that stays above its size cap (M3) |
| `7611429` | fix: a dbt project is a closed surface, judged before dbt ever runs (C1.1–C1.7, C1.9, M1, M2, docs) |
| `ae28b01` | docs: the hand-off guide says rung 3 reuses rung 1's run root on purpose (carry-over 9) |
| `f81c47f` | fix: compile_check never writes its report through a link (hardening found in self-review) |
| `fd40971` | docs: the translator's closed-surface bullet names every allowed key |

## C1 — the dbt project is a closed surface

**Design.** `scripts/lib/dbt_project.py` gains `check_surface(project) -> list[str]` and `DbtUnsafe(ValueError)`;
the checks live in a new focused module `scripts/lib/dbt_surface.py` (≈990 lines; `dbt_project.check_surface` is a
thin lazy-import wrapper, so the ruling's one choke point is `dbt_project.check_surface`). `run_dbt` calls it FIRST
(before `dbt_executable()`, before building any argument) and raises `DbtUnsafe` listing the errors, bounded to 2000
characters, each naming only the file and the offending construct. Callers: `compile_check.py`, `validate_dbt.py`,
`deploy.py` (grep: no other caller outside tests) — all gated.

- **C1.1** `compile_check_dbt` runs `check_surface` beside `dbt:layout`/`dbt:sources`; any surface error → `dbt parse`
  is not called (test monkeypatches `run_dbt` to fail if called). `validate_dbt` judges the surface after the usage
  prerequisites and before any sandbox: `_refuse` writes a FAIL report per segment per set whose `error` starts
  `dbt:surface: …` and names the finer checks, `idempotent: null`, a FAIL chain report (boundary divergence at the
  first segment, `stream: null`), CLI exit 1; no sandbox file, no process (a `subprocess.run` spy records zero calls).
- **C1.2 `dbt:surface`** — the closed file set exactly as ruled; any `.py` anywhere (logs/target included), any
  symbolic link or junction anywhere (never followed; the project directory itself included); model files
  `[A-Za-z0-9_]+\.sql`, model directories `[A-Za-z0-9_-]+`; `logs/`, `target/` contents allowed (never read by dbt).
  **Empirical check:** `dbt parse` and `dbt run` through `run_dbt` (compile_check + validate_dbt, all four golden
  sets) on a scratch copy of `wf_0007` left nothing in the project but our own `compile_check.json` and `logs/*.log`
  — no `.user.yml` or other dbt-written file, so no dbt-written name is allowed.
- **C1.3 `dbt:project_yml`** — keys exactly the six, `vars` exactly `src_schema`/`tgt_schema`, no `{{`/`{%`/`{#`
  anywhere (raw text), plus `model-paths` exactly `[models]` (see Ruling corrections). Layout checks unchanged.
- **C1.4 `dbt:yaml`** — `sources.yml`: `version: 2`, `sources`; source ⊆ {name, schema, description, tables};
  table ⊆ {name, description, columns}; column ⊆ {name, description}; the only Jinja is a source's
  `schema: "{{ var('src_schema') }}"` (whitespace inside the braces tolerated), checked on every decoded string key
  and value AND on the raw text (Jinja in a YAML comment is refused too). `schema.yml`: `version: 2`, `models`;
  model ⊆ {name, description, columns}; column ⊆ {name, description, data_tests, tests}; tests `not_null`,
  `unique` (bare), `accepted_values` {values: plain literals, quote: bool}, `relationships` {to: exactly
  `ref('<model>')`, field: identifier}; no `config:` anywhere; no Jinja anywhere. Anchors, aliases, explicit tags
  and duplicate keys refused. Allowed sets confirmed against `samples/wf_0007/canned/dbt/`, `workflows/wf_0007/dbt/`,
  Task A's fixture template `tests/dbt_fixtures.py`, `tests/cookbook_examples/dbt/merge/project/` and
  `cookbook/dbt.md`: no committed file needed another key (all pass; `test_every_committed_dbt_project_passes_the_gate`).
- **C1.5 `dbt:model_jinja`** — `config(...)` kwargs parsed with `ast` (`config(<args>)` must be one Call, keyword
  arguments only, no `**`, no repeated key), keys ⊆ the six, values a string literal or a non-empty list of them;
  then Jinja's own lexer must see only names-before-`=`, `=`, `,`, brackets and strings, and Jinja's evaluation must
  equal the `ast` values (so `r'…'`, escape tricks or anything Jinja reads differently is refused). The old
  regex that refused any `name(` inside config arguments (hook strings included) is replaced by this.
- **C1.6 `dbt:hook_sql`** — each hook ONE string; its decoded value's only Jinja is exact `{{ this }}`; with
  `{{ this }}` → an unforgeable quoted placeholder, sqlglot (DuckDB dialect, fail closed) must see ONE
  `Delete`/`Update`/`Insert`/`TruncateTable` whose every `Table` is the placeholder or a CTE visible where it is
  used (no qualified name, no table-valued function, no `Unnest`/`Lateral`/`Generator`), no `Anonymous` function,
  nothing on the deny-list `DENIED_FUNCTION` (the ruled names plus `duckdb_*`, `pragma_*`, `which_secret`,
  `json_execute_serialized_sql`, `iceberg_*`, `delta_*`, `sqlite_*`, `postgres_*`, `mysql_*`, `st_read*`,
  `*_metadata`, `checkpoint`, `force_checkpoint`, `enable_*`, `disable_*`), no comment; DuckDB's own parser must see
  exactly one DELETE/UPDATE/INSERT, and DuckDB's tokenizer no comment and no denied name applied to `(`. COPY,
  ATTACH, …, multi-statement are all refused (tested one by one: `HOOK_REFUSED`, 40 cases). Docs: output-targets.md
  (closed-surface table + "cannot be expressed on the dbt target"), cookbook/dbt.md (Hooks), translator (route to the
  SQL target or a human, record `needs_human` in translation_notes.md and stop), reviewer (FAIL with needs_human),
  fixer (not yours to fix: NEEDS_HUMAN). The backlog did not cover it: new item "dbt hooks that touch another table"
  (target_check's `_plain` still admits other-table statements and CALL to dbt).
- **C1.7 `dbt:model_sql`** — the model is rendered with Jinja itself (sandboxed, dbt's defaults, file stripped as
  dbt does) with `source()`/`ref()`/`this` as quoted random-nonce placeholders and `config()` as empty, once with
  `is_incremental()` true and once false (see Ruling corrections); nested/unbalanced/stray `is_incremental` tags
  refused. Each variant: sqlglot must see exactly ONE `exp.Query`; every `Table` a placeholder or a visible CTE
  (scope-aware: a CTE body sees only earlier CTEs, so forward and recursive references fall through to a real table
  and are refused; a CTE outside its scope is refused); same function rules; AND DuckDB's parser must see one SELECT
  whose `json_serialize_sql` tree has only BASE_TABLE (unqualified, placeholder or visible CTE), SUBQUERY, JOIN,
  EMPTY, EXPRESSION_LIST table references and no denied or schema-qualified function. Every committed model
  (`samples/wf_0007/canned/dbt/**`, `workflows/wf_0007/dbt/**`, the cookbook example and its code block) passes;
  both broken `wf_0007` variants pass the gate (`test_every_broken_wf_0007_variant_still_passes_the_gate`) and still
  FAIL with their recorded class (`test_e2e_parity.py::test_broken_migration_fails_with_the_right_class`, green).
- **C1.8** policy: `dbtModelLanes` = `dbt/models/(<dir>/)*<name>.sql` + `dbt/models/(sources|schema).yml` for the
  translator and fixer; the top-level file lane unchanged. Node tests: 17 denials (`x.py`, `sub/x.py`,
  `macros/x.sql`, `packages.yml`, `dependencies.yml`, `selectors.yml`, `extra.yml`, `sub/schema.yml`,
  `sources.yaml`, `docs.md`, `seed.csv`, snapshots/seeds/analyses/tests, `dbt_packages/…`, `x.sql.py`) and 4 allowed
  paths, both roles. POLICY.md item 2 names the lanes and says compile_check/run_dbt enforce the same set.
- **C1.9** `tests/test_dbt_surface.py` (223 tests): the review's 11 cases on scratch copies of the canned `wf_0007`,
  each asserted twice — compile_check refuses it by its check name, writes compile_check.json, exits 1 and starts no
  process; `validate_dbt.main --set normal` exits 1, starts no process, writes FAIL (naming `dbt:surface` and the
  finer check) for both segments and the chain, builds no sandbox, and the payload's marker file (always inside the
  test's `tmp_path`) does not exist. Plus: `run_dbt` raises `DbtUnsafe` with no process; `deploy._execute_dbt` is
  gated; the pristine copy passes the gate and compile_check with exactly one dbt parse; the finer rules on small
  projects (file set incl. junction/symlink, profiles, project yml, both YAML files, config kwargs, 11 accepted and 40
  refused hooks, 8 accepted and 32 refused model bodies, no file content in messages); every committed dbt tree.

### RED evidence (before any fix)

Node, `policy.test.ts` + `stages.test.ts`: 217 pass, 2 fail — `the dbt models lane admits only SQL models and the
two YAML files` (`'allow' !== 'deny'`), `a dbt chain report that PASSes but says needs_human parks translate`
(`'VALIDATED' !== 'NEEDS_HUMAN'`).

Python, every new test: `230 failed in 153.00s`. The scenario matrix, as the pre-fix code behaved:

| case | compile_check (pre-fix) | validate_dbt (pre-fix) | payload ran? |
|---|---|---|---|
| python_model | only `dbt:tool_comments` (the `.py` model itself accepted) | dbt started | **yes** (marker written) |
| on_run_start_copy | `OK` | PASS, exit 0 | **yes** |
| models_block_post_hook | `OK` | PASS, exit 0 | **yes** |
| post_hook_copy | `OK` | PASS, exit 0 | **yes** |
| sql_header | `OK` | PASS, exit 0 | **yes** |
| macro_generate_alias_name | `OK` | PASS, exit 0 | no (`run_query` at parse time is a no-op) |
| schema_yml_run_query | `dbt:parse` only | dbt started | no (dbt parse failed on it) |
| packages_yml | `dbt:parse` only ("run dbt deps") | dbt started | no |
| model_read_csv | `OK` | dbt started (read the file) | read-only payload |
| model_raw_table | `OK` | dbt started | read-only payload |
| hook_reads_another_table | `dbt:model_jinja` via the old `name(` regex (wrong check) | dbt started | read-only payload |

`test_the_pristine_scratch_copy…`/`test_run_dbt_refuses…`/the unit tests: `AttributeError: module
'lib.dbt_project' has no attribute 'check_surface'`. M1: `validate_dbt` on an order flattening to `[]` crashed with
`IndexError: list index out of range` (the CLI printed a traceback, exit 2 — not the exit-0 PASS the review
predicted, but no usage message either); `validate_workflow` on a dbt workflow likewise. M2: the raw
`[Errno 2] … intake\mappings.yaml` message (no `intake/mappings.yaml`), docstring without prerequisites. M3: the
over-cap warning appeared three times.

### GREEN

`tests/test_dbt_surface.py` 223 passed; the related files (`test_compile_check_dbt`, `test_validate_dbt`,
`test_validate_workflow`, `test_dbt_project`, `test_cookbook_dbt`, `test_agents_config`, `test_canned_artifacts`,
`test_deploy`, `test_snowflake_validators`, `test_e2e_parity`): 315 passed after the agent-file/check-name update.
Whole suites: see "Test counts".

## Other rulings

- **N-dbt** — `translateDbt` parks with `dbt: chain needs_human` when the chain report PASSes but says
  `needs_human`; no `procs/README.md`, no documenter. Fake scenario `chain-needs-human:dbt`; node test RED above.
- **M1** — `validate_dbt._segments` raises `ValueError` naming `segments/order.json` when it flattens to no segment;
  `validate_workflow` delegates a dbt workflow there and already refused it for procedures. Tests: `[]`, `[[]]`,
  `[[], []]` for validate_dbt (in-process ValueError + CLI exit 2, no `validation*.json`, no sandbox, no logs) and
  `[]`, `[[]]` for validate_workflow on both output kinds.
- **M2** — `compile_check_dbt` raises `FileNotFoundError` naming `intake/mappings.yaml` and its path (and
  `segments/order.json` likewise), before anything is written; the docstring lists the prerequisites. CLI exit 2.
- **M3** — `segment.py` step 6 remembers the unsplittable groups it already warned about. Test with a two-component
  DAG (an ordering-protected chain of 7 and a formula chain of 12 at `max_tools=3`): exactly one warning.
- **Carry-over 9** — picked "reuse on purpose": §1.3's stale-root checkbox now says stale for hosted calls, rung 1's
  root kept on purpose for rung 3; Rung 3's "Before you start" says the same. `test_handoff_production.py` green.

## Ruling corrections

1. **C1.7 "drop the `is_incremental` tags, keeping both branches' text".** Concatenating both branches refuses a
   legitimate, committed model: `tests/test_compile_check_dbt.py`'s `_JINJA_POSITIVE_CONTROL` (dbt's standard
   if/else with a full SELECT in each branch) becomes `select … from "src"\n\nselect … from "ref"`, and both parsers
   refuse it — sqlglot `ParseError Invalid expression / Unexpected token. Line 3, Col: 6.`, DuckDB
   `Parser Error: syntax error at or near "select"` (run in this session). The smallest correction: render the model
   twice with Jinja itself, `is_incremental()` true and false, and judge each variant completely. Every byte of branch
   text is in at least one variant because nested blocks are refused; and this judges exactly what dbt executes in
   each case (Jinja's own whitespace control included), so it is strictly more faithful, not weaker.
2. **"exactly one top-level SELECT (a WITH … SELECT counts)"** read as exactly one query statement: sqlglot
   `exp.Query` (a SELECT, a set operation such as a top-level `UNION ALL` — the natural translation of Alteryx's
   Union tool — or a parenthesised query), which DuckDB's parser types as SELECT. Every table reference inside is
   still judged.
3. **Tightenings the ruling's goal needs** (none weakens it): `model-paths` must be exactly `[models]` in
   `dbt:project_yml` too — `run_dbt` does not run `dbt:layout`, and any other value would make dbt read models this
   gate never saw; `profiles.yml` = the template is part of the surface (rendered on every run; a dbt-duckdb profile
   can load plugins and extensions); every source must carry the `var('src_schema')` schema (a literal schema would
   let `source()` read any sandbox schema) and its table names are identifiers (dbt quotes them into SQL without
   escaping); `alias`/`unique_key` must be identifiers and `materialized`/`incremental_strategy` closed sets (dbt's
   merge/delete+insert macros splice `unique_key` unquoted — SQL injection — and dbt-duckdb's `external`
   materialisation writes files); a test argument string may hold no `(` (dbt wraps an `env_var(…)`/`ref(…)`-looking
   test kwarg in `{{ }}` and renders it: `dbt/clients/jinja.py` `add_rendered_test_kwargs`); YAML anchors, aliases,
   tags and duplicate keys are refused; a hook may hold no comment; both SQL judges also consult DuckDB's own parser.
   A **blank** hook is allowed at the surface (it runs nothing; `dbt:hooks` still refuses it where a PreSQL/PostSQL
   needs a real hook — keeps `test_a_blank_hook_is_not_a_hook`).

## Files changed

`scripts/lib/dbt_surface.py` (new), `scripts/lib/dbt_project.py`, `scripts/compile_check.py`,
`scripts/validate_dbt.py`, `scripts/segment.py`, `orchestrator/policy.ts`, `orchestrator/stages.ts`,
`orchestrator/POLICY.md`, `orchestrator/test/{policy,stages}.test.ts`, `orchestrator/test/fakes.ts`,
`tests/test_dbt_surface.py` (new), `tests/test_compile_check_dbt.py`, `tests/test_validate_dbt.py`,
`tests/test_validate_workflow.py`, `tests/test_segment.py`, `tests/test_agents_config.py` (the check-name pin now
reads both files and expects the sixteen names), `.github/agents/{translator,reviewer,fixer}.agent.md`,
`docs/reference/output-targets.md`, `cookbook/dbt.md`, `README.md` (the "eleven named checks" sentence),
`docs/production-backlog.md`, `docs/handoff-production.md`. No committed `workflows/**` or `samples/**` file
changed: none violated the new gate.

## Test counts

- node `npm test`: 333 pass / 0 fail / 0 skipped (baseline 331 + 2 new).
- tsc `--noEmit`: clean.
- pytest (whole suite, at `f81c47f`): **2167 passed, 0 failed, 0 skipped in 424 s**. The later `fd40971` only
  rewords one translator bullet; `tests/test_agents_config.py` re-run after it: 62 passed.

## Concerns and what I could not do

- `scripts/lib/dbt_surface.py` is ≈990 lines, larger than the rulings anticipated ("`check_surface` in
  `dbt_project.py`"). It is one responsibility in sections (file set, profile, project YAML, the two YAML files, model
  Jinja/config, hook judge, model judge); I did not split it further on my own.

- **Time of check vs time of use.** The gate judges the files when it is called; a concurrent writer could change the
  project between `check_surface` and dbt's own read. In the orchestrated flow nothing writes while a script runs
  (an agent waits for its tool call), and the policy lanes are the second line.
- `json_serialize_sql` needs DuckDB's json extension (built into the pinned wheel). Without it every model is refused
  — fail closed, but it would stop every dbt workflow.
- A model using a function sqlglot's DuckDB dialect does not know (e.g. `iff`) is now refused (`dbt:model_sql`), as
  the ruling requires; the translator is told to use a known equivalent.
- `tests/test_compile_check_dbt.py::test_model_jinja_allows_whitespace_control_markers` (fix round 2) puts
  `{{+ this +}}` in its positive model, which Jinja itself rejects (`+` is a block-tag marker only). The test asserts
  only "no `dbt:model_jinja`", which still holds; the new `dbt:model_sql` reports "does not render", exactly what dbt
  would do. Left as it is.
- `deploy.py` reports a `DbtUnsafe` as exit 2 ("cannot deploy") rather than 1; nothing runs either way.
- `scripts/target_check.py` still proposes dbt for a PreSQL touching another table or a CALL (they then park at
  translate): recorded as a backlog item, not changed (not ruled).
