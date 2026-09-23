# Output targets — SQL procedure, Snowpark Python, dbt

What the pipeline can produce for a migrated workflow, how it decides which one, where each
artefact lives, and what the offline checks do **not** prove. The design this implements is
`docs/superpowers/specs/2026-09-22-output-targets-design.md`; the program spec under `docs/spec/`
is unchanged and stays the older, single-target document.

**Honesty note (repo-wide).** Nothing in this repository has ever run against a real Snowflake
account or real Alteryx. Every verdict here comes from local doubles — DuckDB for SQL, the
Snowpark Local Testing Framework for Python, and dbt-duckdb for a dbt project. §6 says exactly what
those do not cover.

## 1. Vocabulary

| Term | Values | Where it is recorded |
|---|---|---|
| node class | `sql`, `snowpark`, `manual`, `unknown` | the analyzer's classification; the machine-readable table is `TARGET_CLASS` in `scripts/parsers/plugin_map.py` |
| segment target | `sql`, `snowpark` (the analyzer may also lower one to `manual`) | proposed in `segments/targets.json`; settled in `segments/<seg>/contract.json`'s `"target"` |
| workflow output kind | `procedures`, `dbt` | `segments/targets.json.output_kind`, mirrored to `manifest.json.output_kind` by the orchestrator |
| output-target preference | `procedures`, `dbt` | `manifest.json.output_target` (from `sample.json` or an intake answer), else `mappings/global.yaml`'s `program.output_target` (default `procedures`) |
| tier | `T1`, `T2`, `T3` | unchanged: T1 every node `sql`, T2 any `snowpark`, T3 any `manual` |

A workflow is either a set of stored procedures — some of which may be Snowpark Python — or a dbt
project. Never a mix.

## 2. The decision, step by step

1. **`scripts/segment.py`** cuts the workflow into segments and writes `segments/order.json`.
2. **`scripts/target_check.py <wf> --prefer auto`** (the orchestrator runs it next, and passes
   `auto` every time) reads `parsed/dag.json`, the per-segment DAGs, the contracts if they exist
   and `intake/mappings.yaml`, and writes `segments/targets.json`:

   ```json
   {
     "preference": "dbt",
     "output_kind": "procedures",
     "reason": "dbt refused: seg_02 target is snowpark",
     "dbt_blockers": [{"segment": "seg_02", "kind": "snowpark_segment"}],
     "segments": {"seg_01": "sql", "seg_02": "snowpark"},
     "nodes": {"7": "snowpark"}
   }
   ```

   `--prefer auto` resolves the preference itself: `manifest.json.output_target` first, then
   `mappings/global.yaml`'s `program.output_target`, then `procedures`. A segment is `snowpark` if
   any of its data nodes is; `output_kind` is `dbt` only when the preference is `dbt` **and** there
   are no blockers (`snowpark_segment`, `manual_node`, `unknown_node`, `merge_without_keys`,
   `write_mode_unsupported`, `presql_not_plain`, `postsql_not_plain`, `no_outputs`). Exit 0 written,
   1 written but the workflow has `unknown` nodes, 2 usage.

   Only **exit 2** stops the stage (`script-error`). Exit 1 is not a failure: `targets.json` is
   written either way, and it is written precisely so the analyzer can see the `unknown` nodes and
   record them in `analysis.md` / `unsupported.json`. The orchestrator logs one line and carries on.
3. **The analyzer** copies each proposal into that segment's `contract.json` as `"target"`. It may
   only **lower** a target — `sql` → `snowpark`, or either → `manual` — with a sentence in
   `analysis.md` and a `parity_risks` entry. It may never raise one and never set `dbt` when the
   script refused it.
4. **The orchestrator verifies** (`checkTargets` in `orchestrator/stages.ts`) before anything is
   translated:

   | Situation | Result |
   |---|---|
   | a contract has no `target` (or an unknown value) | analyze `NEEDS_HUMAN`, reason `target-missing: <seg>` |
   | `targets.json` proposes nothing for a segment (missing, unreadable, or a segment it does not list) | analyze `NEEDS_HUMAN`, reason `target-missing: <seg>` — nothing was verified; the detail is in the log line |
   | a contract ranks above the proposal (`sql` is the highest, then `snowpark`, then `manual`) | analyze `NEEDS_HUMAN`, reason `target-mismatch: <seg> raised <proposal> to <contract>` |
   | every contract is at or below its proposal | analyze `DONE`; each lowering is logged |

   Those two strings are the only reason formats: anything the orchestrator wants to add about a
   particular case goes to the log, never into a third format.

   It then sets `manifest.json.output_kind`: `dbt` when `targets.json.output_kind` is `dbt` **and**
   every contract target is still `sql`, otherwise `procedures` — an analyzer lowering one segment
   to Snowpark legitimately makes the whole workflow non-dbt, and that fallback is logged.

   **A tier-T3 workflow has no contracts** (it stops at `MANUAL` and is never translated), so none
   of the verification above applies to it — but `target_check.py` wrote its `targets.json` all the
   same, and §3.3's mirror is not conditional: `manifest.json.output_kind` is set to
   `targets.json.output_kind` verbatim. Every workflow the analyzer finishes therefore carries the
   decided kind, whether or not anything was built from it.

   A parked workflow is reopened the usual way: `--from-stage analyze --only <wf>`.

## 3. Artefacts and file layout

**A macro is classified by what is inside it.** `target_check.py` walks each node of
`parsed/dag.json` and each segment DAG and then, recursively, the nodes of a resolved macro's own
`sub_dag`. A node found inside a macro is keyed by its path, `"<macro_tool_id>/<sub_tool_id>"`
(nested again for a macro inside a macro), both in `targets.json.nodes` and in the `tool_id` of any
blocker that names it:

```json
{ "segments": { "seg_02": "snowpark" },
  "nodes": { "2/3": "snowpark", "2/4": "manual" },
  "dbt_blockers": [{ "segment": "seg_02", "kind": "manual_node", "tool_id": "2/4" }] }
```

The macro's own node stays `sql` in `plugin_map.TARGET_CLASS` — it carries no transformation of its
own — and a segment's class is the highest of all its nodes, sub-DAG nodes included. A macro whose
`sub_dag` is `null` (the `.yxmc` could not be resolved) is classified `unknown`, which is an
`unknown_node` blocker and exit 1: nothing is known about what it does, so `sql` would be a guess.

**The output-target preference is asked once, only when nothing else already answers it.**
Interactive `intake_prompt.py` (`intake_prompt.ask_output_target`) asks "Output target for this
workflow? procedures | dbt [procedures]:" before it asks about any touchpoint, but only when
`manifest.json` has no `output_target` of its own (a `sample.json` override, or a value an earlier
run already recorded) AND `mappings/global.yaml`'s `program.output_target` is unset — the committed
`global.yaml` always carries one, so in practice the question only fires for a program whose own
`global.yaml` was never filled in. Enter takes `procedures`; an answer outside
`procedures`/`dbt` is asked once more, and a second bad answer, or a closed stdin, leaves the key
unset rather than guessing. A non-interactive run (`--no-interactive`, or no TTY) never asks at
all. Whatever is decided — asked, defaulted, or left unset — is written to
`manifest.json.output_target`, which `target_check.py --prefer auto` already reads first, ahead of
`global.yaml`; an unset key simply falls through to `global.yaml`, then `"procedures"`, exactly as
it did before the question existed.

### 3.1 `target: "sql"` — a SQL stored procedure (unchanged)

`workflows/<wf>/segments/<seg>/proc.sql`, per contract C4: `MIG_WORK.<WF>_<SEG>(SRC_DB STRING,
SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING) RETURNS STRING LANGUAGE SQL
EXECUTE AS CALLER`, one CTE per Alteryx tool. `procs/master.sql` calls every segment in wave order.

### 3.2 `target: "snowpark"` — a Python stored procedure

- **Source of truth: `segments/<seg>/proc.py`** — a module with exactly one public entry point,
  `def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id) -> str`, returning `"OK"`.
- **Rules** (every one is a named check in `compile_check.py --target snowpark`; the spec's own
  wording is design §4.2, copied verbatim into `translator.agent.md`): imports only from
  `snowflake.snowpark`, `snowflake.snowpark.functions`, `snowflake.snowpark.types`, `pandas`,
  `numpy`, `re`, `math`, `datetime`, `decimal`; no `exec`, `eval`, `open`, `__import__`, `compile`,
  `globals`, `locals`, `getattr`, `setattr`, `delattr`, `vars`, `os`, `sys`, `subprocess`,
  `socket`, `urllib`, `requests`, `http`, `shutil`, `pathlib`, no raw-SQL escape hatch
  (`sql_expr`, `call_function`, `call_builtin`, `function`, `call_udf`, `call_table_function`,
  `table_function`), and no name or attribute starting with `__`; one `# tool <id>: …` comment per
  data node of the segment (the CTE rule's equivalent). `pandas` is allowed for row-sequential
  logic through `to_pandas()` / `session.create_dataframe(pdf)`, and the translation notes must say
  which Alteryx tool needed it.
- **The session is handed to the procedure, never fetched.** `session` may appear only as the
  receiver of `session.table(...)` or `session.create_dataframe(...)`, written directly in the one
  top-level `run` — not aliased, not passed on, and not read off another object, because
  `DataFrame.session` *is* the live session. The name `Session` is refused outright (bare name,
  attribute, or `from … import` alias), and `session.sql`, `session.call`, `add_packages`,
  `add_import`, `add_requirements`, `udf`, `sproc`, `udtf`, `udaf`, `file`, `query_history`,
  `use_database`, `use_schema`, `use_role`, `use_warehouse` and `close` are refused as an attribute
  of **any** receiver. No `session.sql` is a deliberate ruling, not an oversight: the DataFrame API
  is what keeps these procedures locally testable and reviewable.
- **Reads and the one sink.** Every source read is
  `session.table(f"{src_db}.{src_schema}.<LOGICAL>")` — where `<LOGICAL>` must be an
  `inputs[].logical` the contract declares — or a literal `session.table("MIG_WORK.<WF>_<SEG>_OUT…")`
  naming the segment's own input or work stream. A procedure has **exactly one sink**:
  `.write.mode("overwrite" | "append").save_as_table(<one positional literal>)` (or `.merge(...)`
  on `session.table(f"{tgt_db}.{tgt_schema}.<LOGICAL>")`), with `<LOGICAL>` an `outputs[].logical`
  the contract declares. `saveAsTable` is a real alias and is held to the same rule; `table`,
  `save_as_table`, `saveAsTable` and `create_dataframe` must be *called* where they are written,
  never referenced, and may not take keyword arguments. Every other way of putting something
  somewhere is refused wherever the name appears, whatever the receiver is called:
  `insert_into`, `insertInto`, `copy_into_location`, `copyIntoLocation`, `copy_into_table`, `csv`,
  `json`, `parquet`, `orc`, `save`, `create_or_replace_view`, `create_or_replace_temp_view`,
  `create_or_replace_dynamic_table`, `write_pandas`, `cache_result`, and the `pandas` writers a
  `to_pandas()` frame carries (`to_csv`, `to_parquet`, `to_json`, `to_excel`, `to_pickle`,
  `to_sql`, `to_feather`, `to_hdf`, `to_clipboard`, `to_html`, `to_latex`, `to_markdown`, `to_xml`,
  `to_stata`, `to_gbq`) — and the `numpy` file writers a `.to_numpy()`/`.values` array carries
  (`savetxt`, `savez`, `savez_compressed`, `tofile`, `dump`). The view and dynamic-table forms
  matter most: they *succeed* in the Local Testing Framework, so before this rule an undeclared
  object was created and the segment still PASSed.
- **Column order is part of the schema.** The output `StructType` must list the columns in the
  contract's declared `outputs[].columns` order: a different order is a schema FAIL, reported as a
  `TYPE` difference rather than as an ordering one. A SQL segment transcribes the contract's column
  list into its final `SELECT`, so this rarely bites there; a Snowpark procedure hand-builds the
  `StructType`, where it is an easy slip — and the diagnosis the fixer receives names types, not
  order, so the rule is stated rather than left to be inferred from a diff.
- **`segments/<seg>/proc.sql` is RENDERED, never hand-written.** `scripts/render_snowpark.py <wf>
  <seg>` writes it from `proc.py`:

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

  It is deterministic — byte-identical for the same `proc.py` and the same
  `mappings/global.yaml` `program.snowpark_runtime` — and it is committed next to `proc.py`, so a
  reviewer sees the deployable object without running anything. `render_snowpark.py` exits 1 if
  `proc.py` contains `$$` (it would end the procedure body) and 2 on a usage error.
- `master.sql` calls a Snowpark segment exactly like a SQL one — same name, same five arguments.

### 3.3 `output_kind: "dbt"` — a dbt project

A workflow whose `manifest.json.output_kind` is `dbt` is not a set of procedures: it is ONE dbt project for the
whole workflow, under `workflows/<wf>/dbt/`, translated, checked, reviewed and validated as one unit. Every segment
still has its own `contract.json` (all of them `"target": "sql"` — a Snowpark or manual segment makes the workflow
`procedures`, §2) and still gets its own `validation.json`; only the artefact is shared. Design §4.3 is the
contract; the deviations the phase-2 spikes forced (DV1–DV7 in the phase-2 plan) are folded in below.

| Path (under `workflows/<wf>/`) | Content | Written by |
|---|---|---|
| `dbt/dbt_project.yml` | `name: <wf>`, `profile: alteryx_migration`, `model-paths: [models]`, `vars: {src_schema: null, tgt_schema: null}` | translator |
| `dbt/profiles.yml` | the fixed template below, byte for byte — never a credential (DV2) | translator (copied, never edited) |
| `dbt/models/sources.yml` | one source `src` with `schema: "{{ var('src_schema') }}"` and a table per logical name `intake/mappings.yaml` maps as a source, columns from the contracts | translator |
| `dbt/models/<table>.sql` | one `materialized='table'` model per work stream, named the `MIG_WORK` table lower-cased (`MIG_WORK.WF0007_SEG_01_OUT` → `wf0007_seg_01_out.sql`) | translator |
| `dbt/models/<logical>.sql` | one model per final target, named the logical name lower-cased, with `alias='<LOGICAL>'` in upper case (DV4) and the config its write mode needs | translator |
| `dbt/models/schema.yml` | every model with the contract's columns in order; `not_null` for a `nullable: false` column, `unique` for a single-column key | translator |
| `dbt/README.md`, `dbt/translation_notes.md` | the run command per target and what the two `--vars` mean; every assumption, one per line | translator |
| `dbt/fix_log.md` | one entry per repair | fixer |
| `dbt/review.json` | the reviewer's verdict on the whole project, in a segment review's shape | reviewer only |
| `dbt/compile_check.json` | `compile_check.py --target dbt`'s report | the script only |
| `dbt/logs/validate_<set>.log` | `validate_dbt.py`'s dbt output per golden set, plus `validate_<first set>_rerun.log` (git-ignored) | the script only |
| `dbt_sandbox_<set>.duckdb` | the DuckDB sandbox `validate_dbt.py` built for that golden set, kept for inspection (git-ignored) | the script only |
| `segments/<seg>/validation*.json` | one report per segment, the SQL validator's shape plus `"target": "dbt"` | `validate_dbt.py` |
| `procs/README.md` | the deployment command; it replaces `master.sql`, which a dbt workflow does not have | the orchestrator |

**The narrowed lane (DV5, final fix wave C1.8).** The translator and fixer may write only `dbt/dbt_project.yml`,
`dbt/profiles.yml`, `dbt/README.md`, `dbt/translation_notes.md`, `dbt/fix_log.md`, `.sql` models under `dbt/models/`
and exactly `dbt/models/sources.yml` and `dbt/models/schema.yml` — never a Python model, a macro, a `packages.yml`
or any other file dbt would read, never `dbt/review.json` (the reviewer's own) and never `dbt/compile_check.json`
(the script's own), so the translator cannot write the verdict that judges it. §4 has the policy side; the closed
surface below is the same set, enforced again before dbt ever runs.

**The closed surface (final fix wave C1).** dbt runs Python models, project and model hooks and SQL headers, and
renders Jinja in every YAML file it reads, so a dbt project is judged as a closed surface BEFORE any dbt process
starts: `scripts/lib/dbt_project.py`'s `check_surface` (implemented in `scripts/lib/dbt_surface.py`) is called
first by `run_dbt` itself, so no caller — `compile_check.py`, `validate_dbt.py`, `deploy.py` — can run dbt over a
project outside it (`DbtUnsafe`, a `ValueError`, and no process starts). `compile_check.py --target dbt` reports its
errors and then does not run `dbt parse` at all; `validate_dbt.py` FAILs every segment on every set with an
`error` naming `dbt:surface` and exits 1. Its checks:

| Check | What it refuses |
|---|---|
| `dbt:surface` | any file outside `dbt_project.yml`, `profiles.yml`, `README.md`, `translation_notes.md`, `fix_log.md`, `compile_check.json`, `review.json`, `models/**/*.sql`, `models/sources.yml`, `models/schema.yml` and the output directories `logs/` and `target/` — any `.py` anywhere, `macros/`, `packages.yml`, any other YAML — and any symbolic link or junction |
| `dbt:profiles` | a `profiles.yml` that is not the template, byte for byte (the same check `compile_check.py` always made, now also before every run) |
| `dbt:project_yml` | a `dbt_project.yml` with any key but `name`, `version`, `config-version`, `profile`, `model-paths`, `vars` (so `on-run-start`, `on-run-end`, `models:` and its `+pre-hook`/`+post-hook`/`+sql_header`, `dispatch`, other `*-paths`, `flags` …), `vars` other than exactly `src_schema`/`tgt_schema`, `model-paths` other than `[models]`, or any Jinja delimiter |
| `dbt:yaml` | in `sources.yml`/`schema.yml`: any key a migration does not need, Jinja anywhere (except each source's `schema: "{{ var('src_schema') }}"`, which every source must carry), a `config:` block, a test other than `not_null`, `unique`, `accepted_values` or `relationships` with literal arguments, a YAML anchor, alias, tag or duplicate key |
| `dbt:model_jinja` | (as below) also a `config(...)` key other than `materialized`, `incremental_strategy`, `unique_key`, `alias`, `pre_hook`, `post_hook`, or a value that is not a string literal or a list of them; `alias`/`unique_key` must be plain identifiers (dbt splices them into SQL), `materialized` one of `table`/`view`/`incremental`/`ephemeral`, `incremental_strategy` one of `append`/`merge`/`delete+insert` |
| `dbt:hook_sql` | a `pre_hook`/`post_hook` that is not ONE string holding ONE `DELETE`, `UPDATE`, `INSERT` or `TRUNCATE` whose only Jinja is `{{ this }}` and whose every table (subqueries included) is `{{ this }}` or its own CTE — any other table, `COPY`/`ATTACH`/`INSTALL`/`LOAD`/`PRAGMA`/`SET`/`CALL`/`CREATE`/`DROP`/…, a second statement, a comment, a file/settings/network function (`read_*`, `*_scan`, `glob`, `getenv`, `current_setting`, `query`, …) or a function sqlglot does not know |
| `dbt:model_sql` | a model that, rendered as dbt renders it (with `is_incremental()` true and false), is not exactly ONE query reading only through `source()`, `ref()`, `{{ this }}` and its visible CTEs — a raw table, a table function (`read_csv`, `range`, `FROM 'file.csv'` …), a denied or unknown function |

Both SQL checks are made twice, with sqlglot's DuckDB dialect and with DuckDB's own parser (statement count and type,
and for a model its parse tree's table and function references), and either refusing is enough. **A PreSQL/PostSQL
that touches another table cannot be expressed on the dbt target**: its hook would be refused. Such a workflow belongs
on the SQL target (procedures), or with a human; the translator records it as `needs_human` in
`translation_notes.md` (`docs/production-backlog.md`, "dbt hooks that touch another table").

**The profile (DV2).** `profiles.yml` is always exactly `PROFILES_TEMPLATE` from `scripts/lib/dbt_project.py`, and
`compile_check.py --target dbt` refuses any other byte (`dbt:profiles`, whose message never quotes the file, so a
pasted credential never reaches a report, a log or an agent's context):

```yaml
# The dbt profile of every migrated workflow: scripts/lib/dbt_project.py PROFILES_TEMPLATE, and
# compile_check.py --target dbt refuses any other content. No credential is ever written here:
# every Snowflake value is read from the environment when dbt runs. `local` is the DuckDB double
# scripts/validate_dbt.py uses; `snowflake` has never been run (dbt-snowflake is not installed).
alteryx_migration:
  target: local
  outputs:
    local:
      type: duckdb
      path: "{{ env_var('MIG_DBT_DUCKDB_PATH', 'dbt_sandbox.duckdb') }}"
      schema: "{{ var('tgt_schema') }}"
      threads: 1
    snowflake:
      type: snowflake
      account: "{{ env_var('SNOWFLAKE_ACCOUNT') }}"
      user: "{{ env_var('SNOWFLAKE_USER') }}"
      authenticator: "{{ env_var('SNOWFLAKE_AUTHENTICATOR', 'externalbrowser') }}"
      private_key_path: "{{ env_var('SNOWFLAKE_PRIVATE_KEY_PATH', '') }}"
      role: "{{ env_var('SNOWFLAKE_ROLE') }}"
      warehouse: "{{ env_var('SNOWFLAKE_WAREHOUSE') }}"
      database: "{{ env_var('SNOWFLAKE_DATABASE') }}"
      schema: "{{ var('tgt_schema') }}"
      threads: 4
```

`local` is the DuckDB double `validate_dbt.py` uses. `snowflake` reads every connection value from a `SNOWFLAKE_*`
environment variable and has never run: `dbt-snowflake` is not installed in this repository.

**Naming and the alias (DV4).** A final target's model file is its logical name lower-cased, and its config always
carries `alias='<LOGICAL>'` in upper case. The alias is not style. For an append or merge the target table already
exists under its upper-case logical name (`load_golden.load_set` creates it from `targets_before`, as a real account
holds it), and dbt refuses to adopt it for a lower-case model — spike S3, dbt-duckdb 1.11:

```
When searching for a relation, dbt found an approximate match. … Searched for: "sb1"."MIGDB__MIG_WORK"."attainment_history" Found: "sb1"."MIGDB__MIG_WORK"."ATTAINMENT_HISTORY"
```

A project-level `quoting: {identifier: false}` does not fix it; the alias does. The config per write mode is
`dbt_project.expected_model_config`:

| `intake/mappings.yaml` mode | Model config |
|---|---|
| overwrite | `materialized='table', alias='<LOGICAL>'` |
| append | `materialized='incremental', incremental_strategy='append', alias='<LOGICAL>'` |
| merge | `materialized='incremental', incremental_strategy='merge', unique_key=[<the mapping's keys>], alias='<LOGICAL>'` |
| update_only | none — `target_check.py` never proposes dbt for it (`write_mode_unsupported`) |

A merge's `unique_key` must be exactly the mapping's keys: a narrower one runs with exit 0 and silently loses rows
(S3), which only the golden comparison catches. No model filters on `is_incremental()`: an Alteryx append or merge
writes the whole result of every run, and so does the model. PreSQL/PostSQL become a non-blank
`pre_hook`/`post_hook` rewritten against `{{ this }}`, each ONE plain statement against `{{ this }}` alone
(`dbt:hook_sql`, above). Sources are read only through
`{{ source('src', '<LOGICAL>') }}` and upstream streams only through `{{ ref('<model>') }}`; one CTE per tool, each
with its `-- tool <id>:` comment, exactly as in a procedure.

**Model Jinja is a closed list** (`dbt:model_jinja`). A model may use `{{ config(...) }}` (plain `key=value`
arguments, where a hook string may hold one `{{ this }}`), `{{ source('src', '<LOGICAL>') }}`, `{{ ref('<model>') }}`,
`{{ this }}`, `{% if is_incremental() %}` / `{% else %}` / `{% endif %}` and `{# … #}` comments, with or without
whitespace control (`{{- this -}}`). Everything else is refused, in the SQL and inside a hook string alike —
`env_var`, `var`, `run_query`, `statement`, `adapter`, `{% for %}` — and so is a refused call nested inside an
allowed one, such as `{{ source('src', env_var('X')) }}`.

**Sandboxes and `--vars` (DV1, DV3).** A local run's database is the DuckDB file `MIG_DBT_DUCKDB_PATH` names.
`validate_dbt.py` builds one per golden set at `workflows/<wf>/dbt_sandbox_<set>.duckdb`: a dot-prefixed name such as
`.sandbox.normal.duckdb` breaks dbt-duckdb's catalog naming (`Binder Error: Catalog ".sandbox.normal" does not
exist!`, spike S2). `lib.backend.DuckDBBackend` stores `MIGDB.<SCHEMA>.<T>` as schema `MIGDB__<SCHEMA>` inside that
file, so a local run passes the flattened names `dbt_project.local_vars` computes —
`{"src_schema": "MIGDB__MIG_GOLDEN_<WF>_<SET>", "tgt_schema": "MIGDB__MIG_WORK"}` — which are exactly
`MIG_GOLDEN_<WF>_<SET>` and `MIG_WORK` in database `MIGDB` in Snowflake terms. `var()` inside `profiles.yml` renders
only from `--vars` on the command line, so every run passes both. Every model lands in `tgt_schema`.

**`scripts/compile_check.py <wf> --target dbt`** takes no segment (DV6: the project is one unit). It runs
`dbt parse` through `dbt_project.run_dbt` — the ONE place this repository invokes dbt: a subprocess of the `dbt`
console script beside the interpreter, anonymous usage statistics and colours off, its `target/` and `logs/` in a
temporary directory outside the project (`dbt parse` never opens the DuckDB file) — and sixteen named checks, each a
`dbt:<name>` error in `dbt/compile_check.json`: the closed surface's seven above (`dbt:surface`, `dbt:profiles`,
`dbt:project_yml`, `dbt:yaml`, `dbt:model_jinja`, `dbt:hook_sql`, `dbt:model_sql`; any one of them and `dbt parse`
is not run) and these:

| Check | What it refuses |
|---|---|
| `dbt:layout` | a missing project file, or a `dbt_project.yml` without the name, profile, model-paths and vars above |
| `dbt:profiles` | a `profiles.yml` that is not the template, byte for byte |
| `dbt:sources` | `sources.yml`'s `src` tables not exactly the mapped source logical names, or columns not the contract's |
| `dbt:model_jinja` | any Jinja outside the closed list — checked from the files, so it fires even when parse fails |
| `dbt:parse` | `dbt parse` itself failing |
| `dbt:model_missing` | a contract output with no model |
| `dbt:model_orphan` | a model that is no contract output's — it would deploy a table nothing tracks |
| `dbt:model_config` | `materialized`, `incremental_strategy`, `unique_key` or `alias` not what the write mode needs |
| `dbt:columns` | `schema.yml`'s columns not the contract's, in order — for a work model as much as a target |
| `dbt:tool_comments` | a data node with no `-- tool <id>` comment in any model |
| `dbt:hooks` | PreSQL/PostSQL with no matching non-blank `pre_hook`/`post_hook` (`pre_hook=''` is not a hook) |

Exit 0 every check passed; 1 a check failed (a domain failure: the fixer gets a turn); 2 usage or crash (no project,
no contract, no `dbt` console script beside the interpreter), which parks the stage with `dbt: script-error`.

**`scripts/validate_dbt.py <wf> [--set …] [--project DIR]`** validates the whole project per golden set: a FRESH
sandbox loaded exactly as `load_golden.load_set` loads one for the SQL validator (golden inputs, and `targets_before`
under `MIG_WORK` for an append or merge output), one `dbt run` of the whole project through `run_dbt`, then every
`contract.outputs[]` table of every segment judged by the unchanged `compare.py` through `scripts/lib/validation.py`.
No golden intermediate is loaded: a downstream model reads its upstream model through `ref()` in the same run, so
the chain is the real one. A `dbt run` that fails is a domain FAIL for every segment of that set, naming the failed
models from dbt's `run_results.json`. Idempotency (DV7): the first golden set runs again from a second FRESH
sandbox, and every output table is compared as a row multiset — the design's "same starting state"; a shared
sandbox would double an append output by design. Exit codes as the other validators: 0 every segment passed, 1 a
FAIL, 2 usage or crash. `--project DIR` validates a copy of the project instead (how a broken variant is tested
without touching the one under test).

## 4. What the orchestrator does per target

`stageAnalyze` runs `target_check.py --prefer auto` between `segment.py` and the analyzer, then
verifies the contracts as in §2. `stageTranslate` then branches on `manifest.json.output_kind`. For
`procedures`, `migrateSegment` re-reads `contract.json` on every iteration and dispatches on its
`target`; for `dbt`, `translateDbt` runs the same loop ONCE for the whole workflow:

| Step | `sql` segment | `snowpark` segment | `dbt` workflow |
|---|---|---|---|
| agent writes | `proc.sql` | `proc.py` | `dbt/**` — the narrowed lane of §3.3 — once for the whole workflow, no segment in context |
| render | — | `scripts/render_snowpark.py <wf> <seg>` (exit 2 → the segment needs a human with reason `script-error`; exit 1 → the iteration ends like a compile failure and the fixer gets another turn) | — |
| compile check | `scripts/compile_check.py <wf> <seg>` | `scripts/compile_check.py <wf> <seg> --target snowpark` | `scripts/compile_check.py <wf> --target dbt` (no segment) |
| review | reviewer, `review.json` | same, against `proc.py` and the `proc.sql` ↔ `proc.py` pairing | reviewer once per workflow, `dbt/review.json` |
| validate | `scripts/validate_segment.py` (DuckDB) | `scripts/validate_snowpark.py` (Snowpark Local Testing) | `scripts/validate_dbt.py <wf>` (dbt-duckdb), writing every segment's `validation.json` |
| loop | one per segment, wave by wave | same | ONE per workflow |
| park reason (`reasons.translate`) | `<seg>: <reason>` | same | `dbt: <reason>` |
| chain test (large-workflows.md "The chain test") | `scripts/validate_workflow.py <wf>` once, after every segment PASSed: every segment on its upstream segments' actual output | same (a Snowpark seam crosses through `scripts/lib/handoff.py`) | no extra run: `validate_dbt.py` writes `validation_workflow.json` from its own run, and it must be `PASS…` |
| after the last PASS | `procs/master.sql`, once the chain PASSes | same | `procs/README.md` (no `master.sql`) |

All three validators judge with the same unchanged `compare.py`, use the same verdict vocabulary and
write the same `validation.json` shape; the Snowpark one records `"target": "snowpark"` in it and
`validate_dbt.py` records `"target": "dbt"`. Everything else — the fix-iteration budget, the tool-call
budget, parked escalations, `--from-stage`, crash resume — is unchanged.

**A dbt workflow is one loop.** `migrateDbt` runs translator (iteration 0) or fixer, then
`compile_check.py <wf> --target dbt`, then the reviewer, then the validator — every agent with the
dbt scope and no segment — up to `maxFixIterations` times. After a validation FAIL the fixer's task
names the failing models as `Failing models: <model> (<segment>), …` (every contract output of every
segment whose verdict is not `PASS…`); after a compile failure it quotes the check's own words and
points at `dbt/compile_check.json`, exactly as the procedures loop does. Per-segment statuses are
still recorded, from the reports the validator's run wrote: a segment whose last verdict is `PASS…`
keeps it, every other segment of a parked project is `NEEDS_HUMAN` — and a verdict only counts if it
describes the project as it now stands, so an iteration that changes the project and never gets back
to validation leaves no `PASS` behind. `needs_human: true` in any report wins over its verdict and
stops the loop at once. The park reasons are `dbt: <reason>`, one per workflow:

| `reasons.translate` | When |
|---|---|
| `dbt: validation FAIL after <n> iterations` | the loop was spent and the last iteration reached validation |
| `dbt: compile check failed after <n> iterations` | …and the last one failed `compile_check.py --target dbt` (exit 1) |
| `dbt: reviewer BLOCK after <n> iterations` | …and the last one ended on a `BLOCK` in `dbt/review.json` |
| `dbt: needs_human` | a report said `needs_human: true`; no fixer runs |
| `dbt: script-error` | `compile_check.py --target dbt` exited 2 |
| `dbt: <role> <error>`, `dbt: budget` | an agent did not complete (`translator missing-output`, `fixer denied`, …) or the tool-call budget stopped it |
| `dbt: needs_human (recorded)` | a plain re-run found a segment already recorded `NEEDS_HUMAN` (the crash window F11 closes for procedures) |
| `dbt: chain FAIL` | every segment PASSed, but `validation_workflow.json` (the chain report `validate_dbt.py` wrote from the same run) is missing or not `PASS…` |

A project whose every segment is already recorded `PASS…` (saved, then interrupted before
`VALIDATED`) is not translated again on resume — the project is one unit, so it is all or nothing —
and `--from-stage translate` clears `segment_status`, so the whole project is translated afresh.

**`target: "manual"` is never translated.** `migrateSegment` stops before dispatching anything: no
agent runs, no script runs, nothing is written, and the segment's verdict is `NEEDS_HUMAN` with
reason `manual-segment`, which parks the translate stage the same way any other stuck segment does.
A workflow with a manual node is normally tier T3 and never reaches translate at all (the analyzer
owns that through `unsupported.json`); this guard exists because nothing forces the tier and an
individual contract to agree.

**A failure before review is quoted to the fixer.** A render exit 1 or a compile-check failure
happens before `review.json` and `validation.json` exist, so the next fixer task carries one extra
sentence naming the script that failed and what it said — passed through `auditArgs` (the same
redact-then-bound helper `CopilotRunner` applies to `AgentResult.detail`: redaction first, then a
500-character cut, so a truncated secret cannot survive). The sentence is dropped again as soon as
an iteration gets past the compile check.

**Permissions.** `orchestrator/policy.ts`'s `ROLE_SCRIPTS`: the analyzer may run
`scripts/target_check.py`, the translator and fixer `scripts/render_snowpark.py` (and
`scripts/compile_check.py`, `--target dbt` included), the validator `scripts/validate_snowpark.py`
and `scripts/validate_dbt.py`; the reviewer runs none of them. The translator/fixer write lane
already admits both `proc.py` and `proc.sql` of their own segment, which is what lets the renderer's
output land legitimately. A dbt workflow's agents run with the dbt scope (`AgentCtx.dbt`,
`PolicyOptions.dbtProject`; every audit line then carries `"dbt": true`), which swaps the segment
lanes for the project's: translator and fixer the narrowed lane of §3.3, the reviewer
`dbt/review.json` alone, the validator every segment's `validation*.json`; every other role keeps its
own lanes. **No role may run `dbt`**, however it is spelled — `dbt`, `dbt.exe`, a path ending in
either (`.exe`, `.cmd`, `.bat`), `python -m dbt…` — the policy denies it with "dbt is run only by
scripts/compile_check.py and scripts/validate_dbt.py, never by an agent". Every allow-listed script
that takes the workflow id first (all but `compare.py`) must be given the session's own workflow as
its first bare argument — `validate_dbt.py wf_0002` in a `wf_0001` session is denied
`cross-workflow: scripts/validate_dbt.py wf_0002 in a wf_0001 session` (the first bare argument is
the one judged, so an agent writes the id before any flag). No agent script call may carry `--root`
in any spelling (`--root X`, `--root=X`, the abbreviations `--r`/`--ro`/`--roo`): an agent's working
directory is already the root — `script-root: <script> may not be given --root from an agent session`.
The orchestrator's own script calls are not agent tool calls and are unaffected. A write call's content keys
(`file_text`, `content`, `old_str`/`new_str`, `old_string`/`new_string`, `text`, `insert_line`) are
never judged as paths; the `path` beside them always is.

**Mock replay.** `MockRunner` copies `samples/<wf>/canned/segments/<seg>/proc.py` when the canned
tree has one (else `proc.sql`, as before) and never fabricates a `proc.sql` for a Snowpark segment —
in an offline run that file can only have come from `render_snowpark.py`. A broken variant under
`samples/<wf>/broken_sql/<seg>/` is the first file in name order whatever its extension, so a `.py`
variant is served as the deliberately-wrong first attempt. In dbt scope the translator and fixer
replay every file under `samples/<wf>/canned/dbt/` into `workflows/<wf>/dbt/` at the same relative
path, the reviewer replays `canned/review.json` to `dbt/review.json`, and the validator runs the real
`scripts/validate_dbt.py <wf>`; on the `fix-loop:dbt` / `never-fixed:dbt` scenarios the first file in
name order under `samples/<wf>/broken_sql/dbt/` is laid over the project at its own relative path,
and each fixer turn appends to `dbt/fix_log.md`.

## 5. Deployment

Nothing here deploys anything; this is what a human or CI would do with the files. Every migrated
workflow's `docs/migration.md` carries a `## Deployment` section that restates the steps for its kind
(the documenter agent is told which artefact to quote).

**A procedures workflow** (`output_kind: "procedures"`):

1. `workflows/<wf>/procs/master.sql` and each `segments/<seg>/proc.sql` are the deployable objects —
   **including for a Snowpark segment**, whose `proc.sql` is the `LANGUAGE PYTHON` wrapper. There is
   no separate step to "install" `proc.py`: it travels inside that wrapper's `$$ … $$` body.
2. For a Snowpark segment, check two things against the target account before running the DDL:
   - `RUNTIME_VERSION` — `mappings/global.yaml`'s `program.snowpark_runtime` (currently `"3.11"`) must
     be a Python runtime your account offers. Change it there and re-run `render_snowpark.py`; never
     edit `proc.sql`.
   - `PACKAGES` — `('snowflake-snowpark-python', 'pandas')` must be available in your account's
     Anaconda channel, and any other import the procedure uses must be added to that list.
3. `EXECUTE AS CALLER` is contract C4 for every procedure here, SQL or Python (README §9.1 says why).
   A caller's-rights procedure never exceeds the access its caller already has.
4. Deploy under `MIGRATION_CI`, not the role agent sessions use (README §8). Nothing is deployed
   from an agent session at all: agents produce files and a PR.

**A dbt workflow** (`output_kind: "dbt"`) has no `master.sql` and no per-segment procedure:

1. `workflows/<wf>/procs/README.md` holds the one command (the orchestrator writes it after the last
   PASS):

   ```
   dbt run --project-dir workflows/<wf>/dbt --profiles-dir workflows/<wf>/dbt --target snowflake --vars '{"src_schema": "<SRC>", "tgt_schema": "<TGT>"}'
   ```

   `<SRC>` is the schema that holds the mapped source tables under their logical names; `<TGT>` is
   where the models are written — including the pre-existing target tables an append or merge model
   writes into, under their upper-case logical names (the `alias` of §3.3).
2. Install `dbt-snowflake` first. It is not in `requirements.txt` and has never been installed or run
   in this repository, so the profile's `snowflake` output is unverified.
3. Set the environment variables the profile reads — `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`,
   `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, and `SNOWFLAKE_AUTHENTICATOR`
   (default `externalbrowser`) or `SNOWFLAKE_PRIVATE_KEY_PATH`. No value is ever written into
   `profiles.yml`; `compile_check.py --target dbt` refuses the file if one is.
4. Run it under `MIGRATION_CI`, never from an agent session (the policy denies `dbt` to every agent).
   An append model appends on every run, as the Alteryx Output tool did; a merge model merges on its
   `unique_key`.

## 6. What the local doubles do not prove

- **Snowpark Local Testing Framework** (`scripts/validate_snowpark.py`): it implements a *subset* of
  Snowflake's SQL functions and types; it has no `session.sql`; it resolves no real
  `RUNTIME_VERSION` or `PACKAGES`; and it represents no performance characteristic. A `PASS` here
  means the logic matched the golden data in that subset, not that the procedure will create, let
  alone run, on your account.
- **DuckDB** (`scripts/validate_segment.py`): DuckDB types and identifier casing differ from
  Snowflake's, and `VARCHAR(n)` length is not enforced the way a real account enforces it.
- **The Alteryx simulator** is a model of Alteryx's tools, not the tools
  (`docs/reference/simulator-semantics.md`).
- **The `TARGET_CLASS` table and the dbt blocker list are policy**, verified only by their own
  tests (`tests/test_target_check.py`) — they encode what this project decided a tool class means,
  not a measurement of Alteryx or Snowflake.
- **"Idempotent" compares two *independent* runs, not a run repeated against persisted state.**
  Both validators execute the segment twice from the same inputs — `validate_snowpark.py` in two
  fresh `local_testing` sessions, `validate_segment.py` against two fresh DuckDB backends — and
  compare the outputs as row multisets. A procedure that writes `.mode("append")` where it meant
  `.mode("overwrite")` is therefore `idempotent: true` here, although a second real run against a
  persistent `MIG_WORK` would double the table. This is the SQL twin's pre-existing semantics, not
  something the Snowpark target introduced; what the Snowpark target adds is that the write mode is
  now chosen in Python rather than fixed by the procedure template, so it is worth saying out loud.
  `validate_dbt.py` has the same semantics for a dbt project: its second run starts from a second
  fresh sandbox (DV7), so an append model is `idempotent: true` although it appends on every real run
  — by design, as the Alteryx Output tool did.
- **dbt-duckdb** (`scripts/validate_dbt.py`): DuckDB types and case folding differ from Snowflake's
  (DuckDB matches identifiers case-insensitively even when they are quoted — spike S4); the `merge`
  strategy's semantics are the adapter's — dbt-duckdb emits DuckDB's `MERGE INTO … UPDATE BY NAME /
  INSERT BY NAME`, so a target column the model does not produce is left untouched, which is a
  statement about DuckDB, not about Snowflake's `MERGE`; hooks run on DuckDB, so a PreSQL/PostSQL
  written for Snowflake is only proved to run there; the tests `models/schema.yml` declares
  (`not_null`, `unique`) are not executed by `validate_dbt.py`, which runs `dbt run`, never `dbt test`
  or `dbt build`; and the profile's `snowflake` output has never run — `dbt-snowflake` is not
  installed.

## 7. Where each piece lives

| Path | What |
|---|---|
| `scripts/target_check.py` | proposes segment targets and the workflow output kind |
| `scripts/render_snowpark.py` | renders `proc.sql` from `proc.py` |
| `scripts/compile_check.py --target sql\|snowpark\|auto` | static checks per target (`auto` reads the contract) |
| `scripts/compile_check.py <wf> --target dbt` | the dbt project's sixteen named checks (§3.3), no segment |
| `scripts/validate_snowpark.py` | the Snowpark validator; `scripts/lib/validation.py` is shared with the SQL one |
| `scripts/validate_dbt.py` | the dbt validator: the whole project per golden set on dbt-duckdb, every segment's report |
| `scripts/lib/dbt_project.py` | the dbt layout, naming, `PROFILES_TEMPLATE`, `local_vars`, and `run_dbt` — the one dbt invocation |
| `orchestrator/stages.ts` | `checkTargets` (the §2 verify), `migrateSegment`'s dispatch, `translateDbt`/`migrateDbt` (the dbt loop) |
| `orchestrator/policy.ts` | `ROLE_SCRIPTS`, the write lanes, the dbt lanes and the dbt deny |
| `workflows/<wf>/segments/targets.json` | the proposal |
| `workflows/<wf>/segments/<seg>/contract.json` | `"target"`, the settled decision |
| `workflows/<wf>/manifest.json` | `output_target` (asked for) and `output_kind` (decided) |
| `workflows/<wf>/dbt/` | a dbt workflow's project (§3.3), with `review.json`, `compile_check.json` and the git-ignored `logs/` |
| `workflows/<wf>/procs/README.md` | a dbt workflow's deployment command, in place of `master.sql` |
