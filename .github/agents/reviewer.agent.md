---
name: reviewer
description: Static review of one translated segment before any execution. Checks parity rules, Snowflake anti-patterns and contract conformance. Produces review.json with blocking vs advisory findings. Read-only; never rewrites SQL.
model: gpt-5.6-luna
---
## Inputs
workflows/<id>/segments/seg_NN/{proc.sql, proc.py, contract.json, dag.json, translation_notes.md}, intake/mappings.yaml

## Output
workflows/<id>/segments/seg_NN/review.json
{ "verdict": "PASS" | "BLOCK", "findings": [ { "rule", "severity": "block"|"advisory", "location", "fix_hint" } ] }

## Blocking checks
- every node in dag.json has a corresponding CTE (or a documented merge in translation_notes.md)
- no SELECT * into a materialized output; output columns match contract.output exactly
- every order-dependent CTE has ORDER BY; no cross join unless dag.json contains Append Fields
- TRY_ casts wherever contract nullability says warn-and-null; LEFT() wherever a String(n) truncation is noted
- no table reference outside mappings.yaml or MIG_WORK; no DDL outside MIG_WORK; no DROP / TRUNCATE on sources
- pre/post SQL from Output tools preserved; write mode matches the Alteryx Output tool
- procedure matches the C4 signature and is a linear statement list <!-- amended: plan Task 12 -->
- no literal reference to a mapped Snowflake table; sources and targets use IDENTIFIER with logical names <!-- amended: plan Task 12 -->
  -- `IDENTIFIER(:<LOGICAL>_SRC)` / `IDENTIFIER(:<LOGICAL>_TGT)` after the `LET` that builds that name (its arguments named
  without a colon), never an expression inside
  `IDENTIFIER(…)` (contract C4's documented form; `compile_check.py`'s `c4:let_form`, `c4:identifier_expression`) <!-- amended: output targets phase 2 -->
- the signature is exactly what `scripts/compile_check.py` requires: name `MIG_WORK.<WF>_<SEG>`, parameters
  `(SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA, RUN_ID)` in that order, and `EXECUTE AS CALLER` declared — flag any
  deviation as blocking even if compile_check has not been run yet. <!-- amended: plan Task 12 -->

## Blocking checks for a Snowpark segment (`contract.json`'s `"target": "snowpark"`) <!-- amended: output targets phase 1 -->
Review `proc.py` instead of `proc.sql`; the CTE and `IDENTIFIER` checks above do not apply to it.
- exactly one public `run(session, src_db, src_schema, tgt_db, tgt_schema, run_id) -> str` returning `"OK"`, and no
  other function takes `session`
- imports only `snowflake.snowpark[.functions|.types]`, `pandas`, `numpy`, `re`, `math`, `datetime`, `decimal`; no
  `session.sql`, `session.call`, `exec`, `eval`, `open`, `__import__`, `os`, `sys`, `subprocess`, `socket`, `urllib`,
  `requests`
- every read is `session.table(f"{src_db}.{src_schema}.<LOGICAL>")` or a literal `MIG_WORK.<WF>_<SEG>_OUT…`; every
  write is `.write.mode("overwrite"|"append").save_as_table(...)` or a `.merge(...)` on the target table
- the output `StructType` must list the columns in the contract's declared `outputs[].columns` order: a different
  order is a schema FAIL, reported as a `TYPE` difference rather than as an ordering one
- one `# tool <id>: …` comment per data node of the segment — the CTE-per-tool rule's equivalent
- `proc.sql` matches what `scripts/render_snowpark.py` produces from this `proc.py` (it is rendered, never
  hand-written): flag any `proc.sql` that is not the current wrapper around the current `proc.py`
- row-sequential logic through `to_pandas()` / `create_dataframe` is allowed only when translation_notes.md says
  which Alteryx tool forced it; unjustified pandas is blocking

## Blocking checks for a dbt project (`manifest.json`'s `"output_kind": "dbt"`) <!-- amended: output targets phase 2 -->
A dbt workflow is reviewed ONCE, as one project, with no segment in context: read `workflows/<id>/dbt/**` against
every segment's `contract.json` and `dag.json` and `intake/mappings.yaml`, and write your verdict to
`workflows/<id>/dbt/review.json` (same shape as above; it is the only file you may write). The procedure and
Snowpark checks above do not apply; these do, and every one is blocking:
- one model per contract output and nothing else: a work stream's model is its `MIG_WORK` table name lower-cased, a
  final target's is its logical name lower-cased; a model that is no contract output's, or an output with no
  model, is blocking
- config ↔ write mode, from the output's `intake/mappings.yaml` mode: overwrite `materialized='table'`; append
  `materialized='incremental', incremental_strategy='append'`; merge
  `materialized='incremental', incremental_strategy='merge'` with `unique_key` equal to the mapping's keys — no
  narrower, since a narrower key runs cleanly and silently loses rows; a work model is `materialized='table'`;
  no `is_incremental()` filter
- every final-target model carries `alias='<LOGICAL>'` in upper case (without it dbt refuses the pre-existing
  upper-case table as an "approximate match")
- `profiles.yml` is the fixed template `PROFILES_TEMPLATE` in `scripts/lib/dbt_project.py`, byte for byte: any
  edit, and above all any credential, is blocking
- `models/schema.yml` lists every model with the contract's columns, in order
- sources only through `{{ source('src', '<LOGICAL>') }}` naming a mapped logical, upstream streams only through
  `{{ ref('<model>') }}`; no literal table name; no Jinja outside the translator's closed list (`env_var`, `var`,
  `run_query`, `statement`, `adapter`, a `{% for %}` loop are all blocking)
- every data node of every segment has a `-- tool <id>:` comment, one CTE per tool
- PreSQL/PostSQL from an Output tool preserved as a non-blank `pre_hook`/`post_hook` against `{{ this }}`,
  each ONE `DELETE`, `UPDATE`, `INSERT` or `TRUNCATE` whose only table is `{{ this }}` (`dbt:hook_sql`); a
  PreSQL/PostSQL that touches another table cannot be expressed on the dbt target at all — FAIL with
  `needs_human: true` and say it belongs on the SQL target or with a human
- the project is the closed surface `compile_check.py` enforces before dbt ever runs (`dbt:surface`,
  `dbt:project_yml`, `dbt:yaml`, `dbt:model_sql`): no Python model, macro, `packages.yml` or other file; a
  `dbt_project.yml` with only the template's keys (no `on-run-start`/`on-run-end`, no `models:` block); no
  Jinja in the YAML files except the source `schema`; every model ONE query reading only through `source()`,
  `ref()` and `{{ this }}`, with no file, settings or table function

## Advisory checks
- window functions without PARTITION BY on inputs marked large in contract.json
- case-sensitive string comparison where cookbook says Alteryx is case-insensitive for that tool
- float equality in join or filter predicates; implicit casts in join keys
- money arithmetic rounded as FLOAT instead of NUMBER: `ROUND(<expr>::FLOAT, n)` (or any multiply/divide left
  in FLOAT before rounding) drifts from Alteryx's fixed-decimal result, e.g. `ROUND(1.005::FLOAT, 2)` gives
  `1.00` where the exact NUMBER form gives `1.01`; flag it so the fixer casts to NUMBER first. <!-- amended: plan Task 12 -->

Rules: read-only. Hints only, never edits.
