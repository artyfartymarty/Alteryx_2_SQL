# Task A — fix round 1 (rulings on the task review of 321dc21)

The reviewer verified the brief is implemented faithfully (dbt_project.py verbatim, both brief corrections reproduced against
real dbt, project tree untouched, exit codes, credential-free messages, spec text accurate; 1330 / 0 skipped). Five findings;
apply every ruling RED-first, then run `tests/test_dbt_project.py tests/test_compile_check_dbt.py` and the whole suite.

## I1 — an empty or whitespace hook is not a hook (`scripts/compile_check.py:338-339`)
dbt's manifest turns `pre_hook=""` into `[{"sql": "", ...}]`, a non-empty list, so `if not hooks` passes it. RULING: a hook
counts only if at least one entry's `sql` is non-blank: `if not hooks or not any(str(h.get("sql", "")).strip() for h in hooks)`.
Tests: `pre_hook=""` and `pre_hook="   "` on a tool whose DAG node carries PreSQL → `dbt:hooks` error; same for post_hook.

## I2 — every model must belong to a contract output (new check `dbt:model_orphan`)
A leftover or invented `models/*.sql` that builds cleanly passes today and would deploy an untracked table. RULING: every model
node in the parsed manifest must equal `model_name(output)` for some output of some segment's contract (work streams and targets
alike); anything else is `dbt:model_orphan: models/<file>.sql is not a contract output`. Name the check in the spec §5.1 bullet
list next to the other nine. Test: the fixture project plus `models/extra_model.sql` → exactly that error, exit 1.

## M3 — model SQL is a closed Jinja surface (new check `dbt:model_jinja`)
A model may call `env_var(...)`, `run_query(...)`, `statement(...)` or `adapter.*` and pass. RULING (allow-list, mirroring the
Snowpark rules' conservatism): inside `models/*.sql` (including hook strings in `config(...)`), the only Jinja allowed is
`{{ config(...) }}`, `{{ source('src', '<LOGICAL>') }}`, `{{ ref('<model>') }}`, `{{ this }}`, `{% if is_incremental() %}`,
`{% else %}`, `{% endif %}`, and `{# comments #}`. Any other `{{ … }}` expression or `{% … %}` statement is
`dbt:model_jinja: models/<file>.sql uses <construct>, which a migration model may not`. `profiles.yml` keeps its template
(`env_var` there is the template's, compared byte for byte). Implement with a small tokenizer over `{{`/`{%`/`{#` delimiters
(not a full Jinja parser); inside an allowed `{{ … }}`, refuse nested calls other than the ones named (e.g.
`{{ source('src', env_var('X')) }}` is refused). Tests: `env_var`, `run_query`, `statement` block, `adapter.execute`, `var(...)`
in a model, a nested `env_var` inside `source(...)`, `{% for %}`, and a positive control using every allowed construct → only
the positive control passes. Add the allow-list to spec §4.3 (one bullet) so Tasks C, D and E (canned project, translator agent,
cookbook) write to it.

## M4 — test a work-output column mismatch (self-disclosed gap)
Add a test where a WORK stream model's `schema.yml` columns differ from its contract → the columns check fires.

## M5 — test the hook branch with no mapping (self-disclosed gap)
Add a test for a PreSQL/PostSQL node whose tool id is in no `mappings.yaml` output; assert what the code does (read it) and that
the message is clear.

## Not a finding
The reviewer's note that `alias` relies on upper-case logical names: every logical in this codebase is upper-case by
construction; no action.

## Report
Append "## Fix round 1" to `task-A-report.md`. Commit as `wip: fix round 1 (I1, I2, M3, M4, M5) — <what>`. Same worktree
and rules as before.
