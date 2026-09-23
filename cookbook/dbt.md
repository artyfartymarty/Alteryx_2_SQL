# dbt

`output_kind: dbt` migrates a whole workflow into one dbt project, `workflows/<wf>/dbt/`
(design spec §4.3), rather than a segment's `proc.sql`/`proc.py`. There is no `master.sql`: dbt's
own DAG (`{{ ref(...) }}`/`{{ source(...) }}`) is the execution order. Everything on this page is
enforced by two things -- `compile_check.py --target dbt`'s sixteen named `dbt:<check>` static
checks (`scripts/compile_check.py`) and `scripts/lib/dbt_project.py`, the ONE way this project ever
invokes `dbt` (a subprocess of the console script beside the interpreter, telemetry and colours
off, `target`/`logs` outside the project) -- so every rule below names the check that would refuse
a translation that broke it.

## Materialisations

Every target model's `{{ config(...) }}` mirrors its mapped write mode exactly
(`dbt_project.expected_model_config`, checked as `dbt:model_config`):

| Alteryx write mode | dbt config |
|---|---|
| Overwrite / Create New Table | `materialized='table'` |
| Append Existing | `materialized='incremental', incremental_strategy='append'` |
| Update; Insert if new | `materialized='incremental', incremental_strategy='merge', unique_key=[<keys>]` |

**The alias is never optional.** Every target model's config also carries `alias='<LOGICAL>'` in
upper case, whatever the model's own (lower-cased) file name is. Spike S3 (2026-09-22, dbt-core
1.12.5 + dbt-duckdb 1.11.0) found why: a model named `attainment_history` targeting the
pre-existing table `ATTAINMENT_HISTORY` fails outright --

```
When searching for a relation, dbt found an approximate match.
Searched for: "sb1"."MIGDB__MIG_WORK"."attainment_history"
Found: "sb1"."MIGDB__MIG_WORK"."ATTAINMENT_HISTORY"
```

-- and a project-level `quoting: {identifier: false}` does **not** fix it; only
`config(alias='ATTAINMENT_HISTORY')` does. `compile_check.py` refuses a target model with no
upper-case `alias` (`dbt:model_config`), and `dbt_project.model_name`/`model_relation` are the one
place that rule is encoded in code, not just in prose.

`merge`'s `unique_key` matters for real: S3 also found that `unique_key=['REGION']` where the
mapping's real key is `['REGION', 'PERIOD']` runs with exit **0** and silently loses rows (a second
period for a region already merged never gets inserted, and one row is wrongly updated instead) --
a row-level LOGIC difference with no error at all. `compile_check.py --target dbt`'s
`dbt:model_config` check compares `unique_key` against the mapping's own declared keys for exactly
this reason: this class of mistake produces no dbt error to catch it by hand.

**Two independent lines of defence catch a narrowed `unique_key`, not one restating the other.**
`dbt:model_config` is the static one, refusing a model whose `unique_key` disagrees with the
mapping's declared keys before anything ever runs; `validate_dbt.py`'s (and this page's own)
runtime `compare.py` judgment is the other, over real merged data -- and the two genuinely differ:
a narrowed key that happens not to change any actual row's outcome on a given golden set (this
page's own executable example, before fix round 1's `history_before.csv` gave `EAST` two rows)
passes the runtime compare while still failing the static one, which is why both exist.



## Hooks

PreSQL/PostSQL on a mapped Output tool become `pre_hook`/`post_hook` on that tool's target model,
rewritten against `{{ this }}` (the model's own relation) rather than a literal table name --
`dbt:hooks` refuses a node with non-blank PreSQL/PostSQL whose model has no matching hook, and
(fix round 1, finding I1) an explicitly blank hook (`pre_hook=""`) does not count as one: dbt's own
manifest turns even a blank hook into a non-empty list, so the check reads each hook's own `sql`
text, not just whether the list is empty.

```
-- pre_hook, PreSQL "DELETE FROM dbo.GL_SUMMARY WHERE ACCT = 'OLD'" rewritten against {{ this }}
{{ config(..., pre_hook="delete from {{ this }} where ACCT = 'OLD'") }}
```

**A hook is one plain statement against `{{ this }}` and nothing else** (`dbt:hook_sql`, final fix wave C1): ONE
`DELETE`, `UPDATE`, `INSERT` or `TRUNCATE`, whose every table -- subqueries included -- is `{{ this }}` or a CTE of
the same statement, with no comment, no Jinja but `{{ this }}`, and no function that reads files, settings or the
network. dbt runs hooks as they are written, so `COPY {{ this }} TO '...'`, a second statement, or a
`WHERE k IN (SELECT k FROM other)` is refused before dbt ever runs. **A PreSQL/PostSQL that genuinely touches
another table cannot be expressed on the dbt target**: route the workflow to the SQL target, or to a human.

## Sources and refs

`models/sources.yml` declares one source `src` with `schema: "{{ var('src_schema') }}"` and one
table per mapped input logical name (`dbt:sources` checks the declared names and columns against
the contracts). A model reads a mapped input through `{{ source('src', '<LOGICAL>') }}` and an
upstream work stream through `{{ ref('<wf>_<seg>_out…') }}` -- never a literal schema-qualified
table name, the same "no hand-written FQN" rule contract C4 gives the SQL and Snowpark targets.

**The whole project is a closed surface** (final fix wave C1), checked by `scripts/lib/dbt_project.py` before
any dbt process starts, whoever runs it: only the project files, `.sql` models and the two YAML files
(`dbt:surface` -- no Python model, macro or `packages.yml`), a `dbt_project.yml` with only the template's keys
(`dbt:project_yml` -- no `on-run-start`, no `models:` block), YAML with no Jinja but the source `schema`
(`dbt:yaml`), and each model exactly ONE query that reads only through `source()`, `ref()` and `{{ this }}`, with no
file, settings or table function (`dbt:model_sql`).

**The model's own Jinja is a closed surface** (`dbt:model_jinja`, fix round 1 finding M3): the
*only* constructs allowed anywhere in a model file, hook strings included, are `{{ config(...) }}`,
`{{ source('src', '<LOGICAL>') }}`, `{{ ref('<model>') }}`, `{{ this }}`, `{% if is_incremental()
%}`/`{% else %}`/`{% endif %}` (whitespace-control forms `{{- ... -}}`/`{%- ... -%}` allowed too,
fix round 2), and `{# comments #}` -- nothing else, and nothing else nested inside one of those
either: `{{ source('src', env_var('X')) }}` is refused for the same reason `{{ env_var('X') }}` on
its own is, with no special-case code needed to catch the nested form. `env_var`, `run_query`,
`statement`, `adapter.*`, a bare `var(...)`, and `{% for %}` are all refused. This is why every
piece of environment-dependent configuration (the DuckDB sandbox path, every Snowflake credential)
lives in `profiles.yml`'s `env_var()` calls instead, never in a model. `config(...)` itself takes only
`materialized`, `incremental_strategy`, `unique_key`, `alias`, `pre_hook` and `post_hook`, as string literals (final
fix wave C1.5): `sql_header`, `database`, `schema`, `grants` and the rest are refused.

## Naming

A model's file name is always lower-case (`dbt_project.model_name`): an intermediate work stream's
is its `MIG_WORK` table name lower-cased (`models/<wf>_<seg>_out[_<stream>].sql`); a final target's
is its logical name lower-cased (`models/<logical>.sql`, `alias='<LOGICAL>'` restoring the case).

**Sandbox naming (DV1, spike S2).** A local validation run's DuckDB sandbox is
`workflows/<wf>/dbt_sandbox_<set>.duckdb`, never a dot-prefixed name: dbt-duckdb 1.11 names the
attached catalog after the whole file stem while DuckDB itself drops a leading dot, so
`.sandbox.normal.duckdb` fails with `Binder Error: Catalog ".sandbox.normal" does not exist!` and
setting `database:` in the profile to work around it is refused outright (`Inconsistency detected
between 'path' and 'database' fields in profile`).

**Schema naming (DV3, spike S2).** `--vars` are never the spec's bare `MIG_GOLDEN_<WF>_<SET>` /
`MIG_WORK` locally -- they are the *flattened* names `lib.backend.DuckDBBackend` actually uses,
`dbt_project.local_vars`: `{"src_schema": "MIGDB__MIG_GOLDEN_<WF>_<SET>", "tgt_schema":
"MIGDB__MIG_WORK"}`. That is where `load_golden.load_set` puts the data in the DuckDB double, and
it is exactly what `MIGDB.<SCHEMA>.<T>` flattens to (`lib.backend.local_name`) -- in Snowflake
terms this is still `MIG_GOLDEN_<WF>_<SET>` / `MIG_WORK` under database `MIGDB`, the flattening is
a DuckDB-double fact, not a naming rule for a real account.

## Tests

`models/schema.yml` carries one test per column the contract's nullability and keys imply
(`dbt:columns` checks the columns themselves match the contract, in order, for a work model and a
target one alike): `not_null` for every `nullable: false` column, `unique` for a single-column key.
These are declared for documentation and for a real `dbt test` run against a real account -- **this
project's own `validate_dbt.py` never runs `dbt test`**, only `dbt run` followed by `compare.py`
against the golden data, so a `not_null`/`unique` test here is not part of this repository's own
pass/fail signal.

## Executable example: a merge model

The one pattern on this page that is checked end to end (`tests/test_cookbook_dbt.py`): a merge
model over `tests/cookbook_examples/dbt/merge/`'s incoming rows, run for real through
`lib.dbt_project.run_dbt` against a DuckDB sandbox seeded exactly as `load_golden.load_set` seeds
one, judged by the unchanged `compare.py`. The project's own `profiles.yml` is
`dbt_project.PROFILES_TEMPLATE`, copied verbatim -- never hand-written (`dbt:profiles` refuses
anything else, byte for byte, so no credential can ever reach a committed file).

```sql
-- models/history.sql
-- tool 2: Output Data (Update; Insert if new on ID -> merge, logical HISTORY). The alias is not
-- optional: dbt refuses to adopt a pre-existing upper-case HISTORY table for a lower-case model
-- name ("approximate match"), so every target model names itself explicitly.
{{ config(materialized='incremental', incremental_strategy='merge', unique_key=['ID'], alias='HISTORY') }}
select ID, REGION, AMOUNT from {{ source('src', 'IN_1') }}
```

The incoming rows (`tests/cookbook_examples/dbt/merge/input.csv`) carry both a NULL (`REGION` on
`ID` 2, a brand-new key -- an insert) and a duplicate row (`ID` 1 twice, byte-identical, matching
an existing `HISTORY` row): both incoming rows update the same target row to the same value, so the
merge is not ambiguous and the final table still has one row per `ID` -- confirmed directly against
both engines (the oracle's own `update_insert` write mode and a real `dbt run` against DuckDB) as
part of building this example. The pre-existing `HISTORY` rows (`history_before.csv`) give `EAST`
two `ID`s (1 and 3), specifically so a translation that narrowed `unique_key` to `['REGION']`
cannot hide behind a fixture where every region happens to be 1:1 with one `ID` --
`test_the_merge_example_catches_a_narrowed_unique_key` runs that exact narrowed model for real and
confirms the runtime compare fails, not just the static `dbt:model_config` check (fix round 1,
finding I3). `test_running_the_example_writes_nothing_into_the_committed_project` confirms the
committed `project/` directory is byte-for-byte unchanged after the run: every artefact (`target/`,
`logs/`) lands outside it, exactly like every other `dbt_project.run_dbt` call.

## What dbt-duckdb does not prove

`validate_dbt.py`'s DuckDB sandbox (and this page's own executable example) runs through
`dbt-duckdb`, an adapter, not Snowflake (design spec §9): its **types and case folding differ from
Snowflake's** (spike S4 -- DuckDB matches identifiers case-insensitively even when quoted, and a
model's declared columns keep whatever case the SQL wrote, which `compare.py` already upper-cases
before comparing, so this has not yet surfaced a real difference, but it is the adapter's rule, not
Snowflake's); its **`merge` incremental strategy's exact semantics are the adapter's own** (spike S3
-- dbt-duckdb emits `MERGE INTO ... UPDATE BY NAME / INSERT BY NAME`, which is why a target column
a model does not produce is left untouched rather than nulled, and why a duplicate *new* key in one
`dbt run` inserts twice rather than raising -- both confirmed empirically against this adapter
version, never checked against Snowflake's own `MERGE` semantics, which is unverified); and its
**hooks run on DuckDB**, so a `pre_hook`/`post_hook` written in Snowflake SQL is never actually
exercised against the engine it is meant for. `dbt-snowflake` is not installed in this environment
at all -- the profile's `snowflake` output (§4.3's table) has never been run once.
