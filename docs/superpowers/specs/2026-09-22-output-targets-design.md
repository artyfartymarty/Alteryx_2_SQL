# Three output targets — SQL procedure, Snowpark Python, dbt — design

Approved 2026-09-22 (sections 1–8). Supersedes the single-target assumption of the original
design (`2026-09-18-alteryx-snowflake-pipeline-design.md`, contract C4) for the parts named here;
everything not named is unchanged. Session telemetry (`2026-09-22-session-telemetry-design.md`)
is parked and is not a dependency.

Nothing here has run on a real Snowflake account; the two new local doubles are the Snowpark
Local Testing Framework (`snowflake-snowpark-python`) and `dbt-duckdb`, both installed into
`.venv` on Python 3.14.2 and probed on this machine (§9 lists what they do not prove);
`requirements.txt` gains `snowflake-snowpark-python[pandas]>=1.55`, `dbt-core>=1.12`, `dbt-duckdb>=1.11`.

## 1. Goals and non-goals

Goals
- The pipeline decides, per workflow and per segment, whether the migration output is a SQL
  stored procedure (today's target), a Snowpark Python stored procedure (the program spec's tier
  T2, previously unbuilt), or a dbt project (new). The decision is deterministic where it can be,
  reviewable where it cannot, and always recorded with its reason.
- All three targets are validated offline against the same golden data by the same
  `compare.py`, with the same verdict vocabulary, and are exercised end to end by the mock
  runner and the parity tests through two new sample workflows.
- One bounded live attempt per target on the local BYOK model at the largest context window
  15 GB of VRAM allows, after a small prompt-assembly change that gives intake and the analyzer
  their inputs inline.
- Hand-off quality: no machine paths, every new file and field documented, zero test skips,
  honest statements of what the local doubles do not prove.

Non-goals
- The R tool, spatial, download, email, render and Run Command tools stay tier T3 (`manual`).
- Running Snowpark or dbt against a real Snowflake account; producing dbt tests beyond
  `not_null`/`unique` from the contract; dbt packages/macros beyond what the samples need.
- Mixing dbt with procedures inside one workflow (a workflow is either a dbt project or a set of
  procedures, some of which may be Snowpark).

## 2. Vocabulary

| Term | Values | Where recorded |
|---|---|---|
| node class | `sql`, `snowpark`, `manual`, `unknown` | analyzer's classification (existing) |
| segment target | `sql`, `snowpark` | `segments/<seg>/contract.json.target`; proposed in `segments/targets.json` |
| workflow output kind | `procedures`, `dbt` | `segments/targets.json.output_kind`, mirrored to `manifest.output_kind` by the orchestrator |
| output-target preference | `procedures`, `dbt` | `manifest.output_target` (from `sample.json` or an intake answer) else `mappings/global.yaml program.output_target` (default `procedures`) |
| tier | `T1`, `T2`, `T3` | unchanged: T1 all `sql`, T2 any `snowpark`, T3 any `manual` |

## 3. Decision rules

### 3.1 Node class → segment target (`scripts/target_check.py`)

`scripts/target_check.py <wf> [--prefer procedures|dbt] [--root .]` reads `parsed/dag.json`,
`segments/order.json`, every `segments/<seg>/dag.json`, the contracts if present, and
`intake/mappings.yaml`, and writes `segments/targets.json`:

```json
{
  "preference": "dbt",
  "output_kind": "procedures",
  "reason": "dbt refused: seg_02 target is snowpark; out/ledger.yxdb mode is update_insert without keys",
  "dbt_blockers": [
    {"segment": "seg_02", "kind": "snowpark_segment"},
    {"segment": "seg_03", "kind": "merge_without_keys", "output": "out/ledger.yxdb"}
  ],
  "segments": {"seg_01": "sql", "seg_02": "snowpark", "seg_03": "sql"},
  "nodes": {"7": "snowpark"}
}
```

- Node class table (the cookbook's §8.2 map, made machine-readable in
  `scripts/parsers/plugin_map.py` as `TARGET_CLASS`): `python` → `snowpark`; `r`, `run_command`,
  `download`, `email`, `render`, `spatial` → `manual`; every other known type → `sql`; unknown → `unknown`.
- A segment is `snowpark` if any of its data nodes is `snowpark`; `manual` nodes make the
  workflow T3 exactly as today (the script still reports them under `nodes`).
- `output_kind` is `dbt` only if the preference is `dbt` **and** there are no blockers. Blockers,
  each with the segment (and output key where relevant): `snowpark_segment`, `manual_node`,
  `unknown_node`, `merge_without_keys`, `write_mode_unsupported` (anything but overwrite / append /
  merge), `presql_not_plain` / `postsql_not_plain` (more than one statement, or a statement that is
  not `DELETE`, `UPDATE`, `INSERT`, `TRUNCATE` or `CALL`), `no_outputs`. Otherwise `procedures`
  with `reason: "preference procedures"` or `"dbt refused: …"`.
- Exit codes: 0 written; 1 the workflow has `unknown` nodes (still written, so the analyzer can
  see them); 2 usage.

### 3.2 The analyzer's part

The analyzer runs `target_check.py`, reads `targets.json`, and writes each segment's target into
`contract.json.target`. It may **lower** a target (`sql` → `snowpark`, or either → `manual`) with
a sentence in `analysis.md` and a `parity_risks` entry; it may never raise one and never set
`output_kind: dbt` when the script refused it. The orchestrator verifies after the analyzer:
every contract has `target`; no contract target is higher than the proposal; if the proposal was
`dbt` and every contract target is still `sql`, `manifest.output_kind = "dbt"`, else `procedures`.
A contradiction (a contract raised a target; a dbt kind with a non-sql contract) parks the
workflow `NEEDS_HUMAN` with reason `target-mismatch: <detail>`.

### 3.3 Preference plumbing

- `mappings/global.yaml` `program` block gains `output_target: procedures` (comment: `procedures |
  dbt`) and `snowpark_runtime: "3.11"` (comment: the Python runtime named in `CREATE PROCEDURE …
  LANGUAGE PYTHON`; verify the version list on your Snowflake account).
- `samples/<wf>/sample.json` may carry `"output_target": "dbt"`; `scripts/dev/build_samples.py`'s
  seed copies it into `manifest.output_target` (next to `owner` and `server_schedule`), so
  `tests/helpers.py::prepare_workflow` and the offline run see it without further plumbing. Interactive
  intake asks it as one program-level question only when `global.yaml` has no value (the
  program-level questions already exist for target database/schema).
- The orchestrator passes `--prefer <manifest.output_target ?? global>` to `target_check.py`.

## 4. Artefacts

### 4.1 SQL procedure (unchanged)

`segments/<seg>/proc.sql` per contract C4; `procs/master.sql` calls the segments in waves.

### 4.2 Snowpark Python procedure

- Source of truth: `segments/<seg>/proc.py`, a module with exactly one public entry point
  `def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id) -> str` returning `"OK"`.
- Rules (checked by `compile_check.py --target snowpark`, §5.1), every one of them a named check:
  - **Imports** only from `snowflake.snowpark`, `snowflake.snowpark.functions`,
    `snowflake.snowpark.types`, `pandas`, `numpy`, `re`, `math`, `datetime`, `decimal`; no `exec`,
    `eval`, `open`, `__import__`, `compile`, `globals`, `locals`, `getattr`, `setattr`, `delattr`,
    `vars`; no `os`, `sys`, `subprocess`, `socket`, `urllib`, `requests`, `http`, `shutil`,
    `pathlib`; no raw-SQL escape hatch (`sql_expr`, `call_function`, `call_builtin`, `function`,
    `call_udf`, `call_table_function`, `table_function`), even merely imported; and no name or
    attribute starting with `__` anywhere in the module.
  - **The session is handed to you, never fetched.** `session` may appear ONLY as the receiver of
    `session.table(...)` or `session.create_dataframe(...)`, written directly in the one top-level
    `run` — not aliased, not passed as an argument, not taken by any other function or lambda, and
    not read off another object, because `DataFrame.session` IS the live session. The name
    `Session` is refused outright (bare name, attribute, or `from … import` alias), and so are
    `session.sql` and `session.call` and the other session methods — `add_packages`, `add_import`,
    `add_requirements`, `udf`, `sproc`, `udtf`, `udaf`, `file`, `query_history`, `use_database`,
    `use_schema`, `use_role`, `use_warehouse`, `close` — as an attribute of ANY receiver, whatever
    the session happens to be bound to.
  - **Every source read** is `session.table(f"{src_db}.{src_schema}.<LOGICAL>")`, where `<LOGICAL>`
    must be an `inputs[].logical` this contract declares, or a literal
    `session.table("MIG_WORK.<WF>_<SEG>_OUT…")` naming this segment's own declared input or work
    stream. Shape alone is not enough: an undeclared name is refused in either direction.
  - **Exactly one sink**: `.write.mode("overwrite" | "append").save_as_table(<one positional
    literal>)`, the table named in the same two forms, with `<LOGICAL>` an `outputs[].logical` this
    contract declares for the `{tgt_db}.{tgt_schema}` form; `.merge(...)` on
    `session.table(f"{tgt_db}.{tgt_schema}.<LOGICAL>")` is that same shape. A final target's write
    mode picks which (`rule:write_mode`, live hardening L4): `overwrite` and `append` their own
    `.mode(...)`, `truncate_append` `.mode("truncate")`, `update_insert` the `.merge(...)` on exactly
    the contract's keys; a `.update(...)` or `.delete(...)` of the target is the Output tool's
    PreSQL or PostSQL, and only when the tool has one. `saveAsTable` is a real
    alias and is held to the same rule. Every OTHER way of putting something somewhere is refused
    wherever the name appears, as an attribute or as a bare name, whatever the receiver is called:
    `insert_into`, `insertInto`, `copy_into_location`, `copyIntoLocation`, `copy_into_table`,
    `csv`, `json`, `parquet`, `orc`, `save`, `create_or_replace_view`,
    `create_or_replace_temp_view`, `create_or_replace_dynamic_table`, `write_pandas`,
    `cache_result`, and the `pandas` writers a `to_pandas()` frame carries — `to_csv`,
    `to_parquet`, `to_json`, `to_excel`, `to_pickle`, `to_sql`, `to_feather`, `to_hdf`,
    `to_clipboard`, `to_html`, `to_latex`, `to_markdown`, `to_xml`, `to_stata`, `to_gbq` — and the
    `numpy` file writers — `savetxt`, `savez`, `savez_compressed`, `tofile`, `dump`.
  - **A write method is called where it is written**, never referenced: `table`, `save_as_table`,
    `saveAsTable` and `create_dataframe` appearing as an attribute that is not a call's own
    function is refused (`sink = w.save_as_table; sink(...)` carries the write past every argument
    check), and none of them may take keyword arguments.
  - **Column order is part of the schema**: the output `StructType` must list the columns in the
    contract's declared `outputs[].columns` order: a different order is a schema FAIL, reported as
    a `TYPE` difference rather than as an ordering one.
  - One `# tool <id>: …` comment per data node of the segment (the CTE rule's equivalent).
    `pandas` is allowed for row-sequential logic through `to_pandas()` /
    `session.create_dataframe(pdf)`; the notes must say which tool needed it.
- Rendered artefact: `scripts/render_snowpark.py <wf> <seg>` writes `segments/<seg>/proc.sql`:

```sql
CREATE OR REPLACE PROCEDURE MIG_WORK.<WF>_<SEG>(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '<program.snowpark_runtime>'
PACKAGES = ('snowflake-snowpark-python', 'pandas')
HANDLER = 'run'
EXECUTE AS CALLER
AS
$$
<proc.py verbatim>
$$;
```

  Deterministic (byte-identical for the same `proc.py` and config); `proc.sql` is committed next
  to `proc.py` and `test_canned_artifacts` asserts they agree. `master.sql` calls it exactly like
  a SQL segment; `parse_proc` learns `LANGUAGE PYTHON` (records `language`, does not split the
  body into statements).

### 4.3 dbt project

`workflows/<wf>/dbt/`:

| Path | Content |
|---|---|
| `dbt_project.yml` | `name: <wf>`, `profile: alteryx_migration`, `model-paths: [models]`, `vars: {src_schema: null, tgt_schema: null}` |
| `profiles.yml` | the fixed template `PROFILES_TEMPLATE` in `scripts/lib/dbt_project.py` (`local`: type `duckdb`, `path` from `env_var('MIG_DBT_DUCKDB_PATH', 'dbt_sandbox.duckdb')`, `schema: "{{ var('tgt_schema') }}"`; `snowflake`: every connection value from `env_var()`) — no credentials in the file, ever, and `compile_check.py --target dbt` compares the file byte for byte against the template |
| `models/sources.yml` | one source `src` with `schema: "{{ var('src_schema') }}"` and a table per mapped input logical name, columns from the contracts |
| `models/<wf>_<seg>_out[_<stream>].sql` | one `table` model per intermediate stream (name = the `MIG_WORK` table name lower-cased) |
| `models/<logical>.sql` | one model per final target, its file name the logical name lower-cased, with `alias='<LOGICAL>'` (upper case) in every model's config — dbt refuses to adopt a pre-existing upper-case target table for a lower-case model name ("approximate match"), so the alias is not optional: `materialized='table'` (overwrite); `materialized='incremental', incremental_strategy='append'` (append); `materialized='incremental', incremental_strategy='merge', unique_key=[keys]` (merge); `pre_hook`/`post_hook` for PreSQL/PostSQL rewritten against `{{ this }}` |
| `models/schema.yml` | models and columns from the contracts; `not_null` for `nullable: false` columns, `unique` for a single-column key |
| `README.md` | the run command per target and what `--vars` mean |

Spike 2026-09-22 (plan phase 2): these names are what dbt-duckdb 1.11 and DuckDB 1.5 accept.

One CTE per tool inside each model, same `-- tool <id>:` comments; a model reads sources through
`{{ source('src', '<LOGICAL>') }}` and upstream streams through `{{ ref('<wf>_<seg>_out…') }}`.
No `master.sql`; `procs/README.md` says `dbt run --project-dir workflows/<wf>/dbt --profiles-dir
workflows/<wf>/dbt --target snowflake --vars '{"src_schema": "<SRC>", "tgt_schema": "<TGT>"}'`.

A model's Jinja is a closed surface, checked as `dbt:model_jinja` (fix round 1, finding M3): the
only constructs `models/*.sql` may use, anywhere including inside a hook string, are `{{
config(...) }}`, `{{ source('src', '<LOGICAL>') }}`, `{{ ref('<model>') }}`, `{{ this }}`, `{% if
is_incremental() %}`/`{% else %}`/`{% endif %}`, and `{# comments #}` — nothing else, and nothing
else nested inside one of those either (`{{ source('src', env_var('X')) }}` is refused, not just
`{{ env_var('X') }}` on its own). Tasks C, D and E (the canned sample, the translator agent, the
cookbook) write to this list.

## 5. Static checks and validation

### 5.1 `compile_check.py --target sql|snowpark|dbt` (default from the contract)

- `sql`: unchanged.
- `snowpark`: `py_compile` of `proc.py`; an AST walk enforcing every rule of §4.2 (each
  violation is a named check in `compile_check.json`); `proc.sql` matches `render_snowpark.py`'s
  output byte for byte; the C4 signature and `EXECUTE AS CALLER` on the wrapper.
- `dbt` (`compile_check.py <wf> --target dbt`, no segment: a dbt project is one unit): `lib.
  dbt_project.run_dbt("parse", …)` — the ONE dbt invocation, a subprocess of the console script
  with telemetry and colours off and its `target`/`logs` outside the project — exits 0 and writes a
  manifest; eleven named checks against it (`dbt:layout`, `dbt:profiles`, `dbt:sources`,
  `dbt:model_jinja`, `dbt:parse`, `dbt:model_missing`, `dbt:model_orphan`, `dbt:model_config`,
  `dbt:columns`, `dbt:tool_comments`, `dbt:hooks`): every contract output has a model and every
  model is some contract output's (`dbt:model_orphan`, fix round 1 finding I2 — a leftover or
  invented model would otherwise deploy an untracked table); every model's `config` matches the
  contract's write mode (including the `alias='<LOGICAL>'` every target model needs); `schema.yml`
  columns equal the contract columns, in order, for a work model as much as a target one;
  `profiles.yml` is byte-identical to `PROFILES_TEMPLATE` — never a credential; every model's Jinja
  stays inside the closed allow-list §4.3 names (`dbt:model_jinja`); a node's PreSQL/PostSQL needs a
  matching hook whose `sql` is more than blank — `pre_hook=""` is not a hook (`dbt:hooks`, fix round
  1 finding I1).
- Exit codes as today: 0 pass, 1 a failed check (domain), 2 usage/crash.

### 5.2 `scripts/validate_snowpark.py <wf> <seg> [--set …] [--proc FILE]`

Per golden set: a fresh local Snowpark session (`local_testing`); golden inputs loaded as
`MIGDB.MIG_GOLDEN_<WF>_<SET>.<LOGICAL>` tables and upstream intermediates as their literal
`MIG_WORK` names, both from the same CSV readers `load_golden.py` uses, with a Snowpark `StructType` mapping added to
`scripts/lib/types_map.py` beside the SQL one; `targets_before` loaded under `MIGDB.MIG_WORK.<LOGICAL>` for append/merge outputs;
`proc.py` imported from a temp copy and `run(session, "MIGDB", "MIG_GOLDEN_<WF>_<SET>", "MIGDB",
"MIG_WORK", "validate_<wf>_<seg>_<set>")` called; every `contract.outputs[]` table read back with
`to_pandas()`, converted to a typed table by the contract's declared column types (a value that
does not convert is a schema failure, reported through `compare.py`'s own schema check by loading
it as VARCHAR), written into a fresh DuckDB backend and judged by `compare.compare()`.
Idempotency, worst-of-sets, stale-report deletion, missing-table FAIL, empty-outputs usage error
and the report shape are the ones `validate_segment.py` implements — extracted into a shared
`scripts/lib/validation.py` used by both, not copied. A Snowpark error inside `run` is a domain
FAIL with `error`, like a `ProcError`.

### 5.3 `scripts/validate_dbt.py <wf> [--set …]`

Per golden set: a fresh sandbox DuckDB file (`workflows/<wf>/dbt_sandbox_<set>.duckdb` — a
dot-prefixed name breaks dbt-duckdb's catalog naming) with the goldens loaded exactly as
`load_golden.load_set` does and `targets_before` in `MIG_WORK`; `lib.dbt_project.run_dbt("run", …)`
with `--vars` the flattened local schema names `dbt_project.local_vars` reads off
`lib.backend.local_name` (locally `{"src_schema": "MIGDB__MIG_GOLDEN_<WF>_<SET>", "tgt_schema":
"MIGDB__MIG_WORK"}`, which is where `load_golden.load_set` puts the data in the DuckDB double) as a
subprocess (its log kept as `dbt/logs/validate_<set>.log`, git-ignored); then, for every
segment, every contract output compared with `compare.compare()` against the model's table; the
segment reports are written to `segments/<seg>/validation*.json` in the same shape, so the
orchestrator, fixer and cost report see no difference. `dbt run` exit ≠ 0 is a domain FAIL naming
the failed models. Idempotency: a second `dbt run` from the same starting state must leave every
output table identical (append/merge models included). Per DV7, as amended by the controller in
Task W1 fix round 2, that second run starts from a second FRESH sandbox whose raw golden inputs and
`targets_before` tables are loaded in REVERSED row order (the first keeps file order), compared as row
multisets, so a model that depends on row order is non-idempotent and FAILs.

### 5.4 What is compared

Same `compare.py`, same tolerances from `global.yaml`, same accepted-diff rules, same verdicts.

## 6. Orchestrator and mock runner

- `stageAnalyze`: runs `target_check.py --prefer …` after `segment.py`; the analyzer prompt names
  `targets.json`; the verify callback applies §3.2 and sets `manifest.output_kind`.
- `stageTranslate`: reads `contract.json.target` per segment. `sql` → today's path. `snowpark` →
  translator writes `proc.py`; the orchestrator runs `render_snowpark.py`, then
  `compile_check.py --target snowpark`, then `validate_snowpark.py`; the fixer prompt says it is a
  Python procedure. `output_kind: dbt` → one translate iteration for the whole workflow: the
  translator writes `dbt/**`; `compile_check.py --target dbt`; `validate_dbt.py`; reviewer and
  fixer run per workflow with the failing models named; segment statuses are still recorded per
  segment from the validation reports. `master.sql` only for procedures; `procs/README.md` for
  dbt. Parked-escalation, budget, `--from-stage` and crash-resume rules apply unchanged.
- `MockRunner`: replays `canned/segments/<seg>/proc.py` and `canned/dbt/**` as it replays
  `proc.sql`; broken variants stay indexed in `broken_sql/broken.json` (rows may name `.py` or
  `dbt/models/*.sql` files; the first variant in name order is the bad first attempt).
- Policy: the translator's lane admits `segments/<seg>/proc.py` of its workflow and, for a dbt
  workflow, a narrow subset of `dbt/**` rather than the whole tree —
  `dbt/{dbt_project.yml,profiles.yml,README.md,translation_notes.md,fix_log.md}` and
  `dbt/models/**` — never `dbt/review.json` (the reviewer's own) or `dbt/compile_check.json` (the
  script's own), so the translator cannot write the verdict that judges it; the new scripts
  (`target_check`, `render_snowpark`, `validate_snowpark`, `validate_dbt`) join the per-role script
  allow-lists in the `.venv/Scripts/python.exe scripts/<x>.py` shape; `dbt` itself is never invoked
  by an agent, only by the scripts.
- `docs/spec/**` stays verbatim; the target vocabulary and file layouts are documented in
  `docs/reference/output-targets.md`.

## 7. Samples and the simulator

### 7.1 Simulator: the `python` tool

`scripts/dev/alteryx_sim.py` gains `sim_python`: the node's config carries `script` (the tool's
embedded Python) and its anchors; the script runs with restricted builtins, imports only from the
§4.2 allow-list minus Snowpark, and dunder-attribute access refused at the AST level. **This is an
accident guard, not a security boundary**: an allowed library can still reach the filesystem and
load native code (for example `DataFrame.to_csv`, or a submodule the allow-list admits by its
top-level package alone, such as `numpy.ctypeslib` or `pandas.io.common`), so the simulator runs
only this repository's own committed sample scripts and must never be pointed at an untrusted
workflow's Python tool. The docs say so in those words. The script sees an `Alteryx` shim:
`Alteryx.read("#1")` returns the input as a `pandas.DataFrame` (typed from the field list),
`Alteryx.write(df, 1)` captures the output. dtypes map back to Alteryx types (`int64` → Int64,
`float64` → Double, `object`/`string` → V_WString, `bool` → Bool, `datetime64` → DateTime); NaN/NaT →
NULL; column order is the DataFrame's. `docs/reference/simulator-semantics.md` records this as an
assumption about the real tool. The parser gets a `python` type in `plugin_map.py` for the
plugin name the sample's XML uses (marked "verify against your Alteryx version", as the other
plugin names are), and `dag-contract.md` documents the config keys.

### 7.2 `wf_0006` — subscription revenue recognition (procedures + one Snowpark segment)

Input `subscriptions.yxdb` (customer, plan, start date, months, amount) → Filter (active) →
Python tool computing a per-customer revenue schedule with carry-over rules that depend on the
previous row's state (deferred balance rolls into the next period unless a cancellation flag
resets it) → Summarize by customer and period → Output (overwrite). Segments: `seg_01` (input,
filter) `sql`; `seg_02` (python tool) `snowpark`; `seg_03` (summarize, output) `sql`. Tier T2,
`output_kind: procedures`. Golden sets from the simulator (the python tool runs the same script).
Canned artefacts: `proc.sql` for seg_01/seg_03, `proc.py` + rendered `proc.sql` for seg_02, the
usual intake/analysis/review/docs files. Broken variants: `seg_02/01_carry_over_ignored.py`
(`LOGIC`), `seg_03/01_summarize_drops_period.sql` (`LOGIC`, rows).

### 7.3 `wf_0007` — regional targets (dbt)

Inputs `targets.yxdb`, `actuals.yxdb` → Join on region+period → Filter (current year) →
Summarize → two outputs: `REGION_ATTAINMENT` (overwrite) and `ATTAINMENT_HISTORY` (merge on
region+period, with `targets_before`). `sample.json` has `"output_target": "dbt"`. Tier T1,
`output_kind: dbt`. Canned `dbt/**`; broken variants `dbt/models/region_attainment.sql` without
the year filter (`LOGIC`) and `attainment_history.sql` with a wrong `unique_key` (`LOGIC`, rows).
The committed offline run (`workflows/wf_0007/`) shows the dbt project and its validation reports.

## 8. Agents, cookbook, docs, live tests

- Agent files: analyzer (run `target_check.py`; lower-only rule; `contract.json.target`),
  translator (three target sections with the exact rules of §4), reviewer (per-target blocking
  checks: AST rules; one model per output and config ↔ write mode; `proc.sql` ↔ `proc.py`),
  validator (which script per target), fixer (per-target repair notes), documenter (deployment
  section per kind). `.github/copilot-instructions.md`: one paragraph on targets.
- `cookbook/snowpark.md`: DataFrame idioms per tool (from `cookbook/<tool>.md`'s SQL patterns),
  with executable examples under `tests/cookbook_examples/snowpark/<tool>/` for the tools
  `wf_0006` uses (filter, formula, summarize, sort, python-carry-over), checked by the local
  session against the simulator like the SQL examples. `cookbook/dbt.md`: materialisations,
  hooks, sources/refs, naming, tests, with one executable example (a merge model on DuckDB).
- `docs/reference/output-targets.md` (vocabulary, decision rules, file layouts, deployment per
  kind, what the doubles do not prove). README: a "Three output targets" section, `wf_0006`/`wf_0007`
  in the sample table, deployment steps per kind. `docs/handoff-copilot-models.md`: unchanged
  except a pointer.
- Live tests (bounded, same hard limits as `task-16-addendum.md`): `scripts/prompt_context.py
  <wf> --role intake|analyzer [--budget-chars N]` renders `intake/touchpoints.json`, a compact DAG
  summary (tool id, type, in/out columns) and `targets.json` into the task prompt; the orchestrator
  uses it for those two roles. `serve_model.ps1 -Context 131072 -CacheTypeK q4_0 -CacheTypeV q4_0`
  (fallback 98304); one attempt per sample `wf_0001`, `wf_0006`, `wf_0007` with `--stop-after
  intake`, then `analyze`, then translate if reached; every observation in `docs/live-smoke-test.md`.

## 9. What the local doubles do not prove (stated in every relevant doc)

- Snowpark Local Testing Framework: a subset of Snowflake's SQL functions and types; no
  `session.sql`; no real `RUNTIME_VERSION`/`PACKAGES` resolution; performance not represented.
  Two further gaps found and version-pinned while building `cookbook/snowpark.md` (Task E, fix
  round 1, `snowflake-snowpark-python` 1.55.0): `==`/`!=` do not propagate NULL through a
  comparison (a NULL operand compares as an ordinary True/False, never NULL, so a derived
  condition's own `.is_null()` never fires); and there is no working `round()` -- every
  decimal-narrowing path (`cast`/`try_cast` to a smaller-scale `DecimalType`, `to_decimal`,
  `to_char`/`to_varchar`) converts through Python's binary-float `round()` regardless of the
  source type, so a value whose nearest double sits a hair below an exact decimal midpoint (e.g.
  `1.005`) rounds the wrong way locally with no DataFrame-API workaround.
- `dbt-duckdb`: DuckDB types and case folding differ from Snowflake's; `merge` strategy semantics
  are the adapter's; hooks run on DuckDB.
- The python-tool simulator is a model of Alteryx's tool, not the tool.
- The `TARGET_CLASS` table and the dbt blocker list are policy, verified only by their tests.

## 10. Testing

- `tests/test_target_check.py`: every blocker; preference resolution; `unknown` exit 1; determinism.
- `tests/test_render_snowpark.py`: byte-identical output; `parse_proc` on the wrapper.
- `tests/test_compile_check_targets.py`: each AST rule refused with its check name; dbt parse,
  model↔contract config and columns.
- `tests/test_validate_snowpark.py`, `tests/test_validate_dbt.py`: hand-built workflows (as
  `test_validate_segment.py` does): PASS, wrong logic FAIL with clusters, runtime error FAIL with
  `error`, non-idempotent FAIL, missing output table FAIL, usage errors exit 2, stale reports gone.
- `tests/test_alteryx_sim_python.py`: the shim, dtype mapping, NaN → NULL, sandbox refusals
  (`import os`, `open`, network).
- Node: dispatch per target with fakes (targets.json verify rules, lower-only, dbt single
  iteration, prompt_context in the intake/analyzer tasks), policy lanes for the new files/scripts.
- `tests/test_e2e_parity.py`: `wf_0006` and `wf_0007` run, every golden set PASS, broken
  variants FAIL with the recorded class; `tests/test_canned_artifacts.py` extended (`proc.py`
  rules, `proc.sql` ↔ render, dbt project shape). Zero skips.
- Cookbook harness: the Snowpark and dbt examples checked against the simulator.

## 11. Rulings carried into this design

- Deterministic scripts propose targets; the analyzer may only lower; the orchestrator verifies.
- dbt is an organisational preference honoured only when feasible, with blockers recorded.
- `proc.py` is the Snowpark source of truth; `proc.sql` is rendered, committed and checked.
- DataFrame API only (no `session.sql`): keeps procedures locally testable and reviewable.
- One shared validation library for all three validators; `compare.py` unchanged.
- The mock path proves mechanics; live runs report what the model manages; hard limits unchanged.
