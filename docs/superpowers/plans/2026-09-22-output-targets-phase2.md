# Output Targets, Phase 2 — dbt, Cookbook, Prompt Context, Large Workflows, Production Hand-off, Live Test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dbt project a real third output target (artefact contract, static check, DuckDB validation, a sample, orchestrator dispatch), finish the phase-2 items of the output-targets design (cookbook pages, inline prompt context, the phase-1 leftovers, the refreshed offline run, the bounded live test), make large workflows safe to migrate in chunks (workflow-level chain validation, seam checks, batched analysis, size-aware segmentation, compaction notes), and leave the repository ready for a production hand-off (hosted Copilot models, a real Snowflake backend, real Alteryx corpus triage, an agent-facing hand-off guide).

**Architecture:** dbt is invoked only through one helper (`scripts/lib/dbt_project.py`) that runs the `dbt` console script as a subprocess with telemetry off and every artefact outside the project; `compile_check.py --target dbt` checks the project against the contracts through `dbt parse`'s manifest, and `validate_dbt.py` builds a DuckDB sandbox per golden set exactly as `load_golden.load_set` does, runs the whole project, and judges every contract output with the unchanged `compare.py` through the shared `scripts/lib/validation.py`. The orchestrator dispatches a dbt workflow as ONE translate iteration for the whole workflow and records per-segment statuses from the reports. A new workflow-level validator runs every segment on the *actual* output of its upstream segments and localises the first divergence; the analyzer is batched above a character budget with a deterministic seam check and stitch. Production seams (per-role model settings, a Snowflake backend behind named connections, a corpus survey) are code with fake-backed tests; nothing claims a real run.

**Tech Stack:** Python 3.14.2 (`.venv`), dbt-core 1.12.5 + dbt-duckdb 1.11.0, duckdb 1.5.5, sqlglot 30.18.0, snowflake-snowpark-python 1.55.0 (local testing), pytest 9; TypeScript on Node 22 (`node --experimental-strip-types`, `node:test`), `@github/copilot-sdk` ^1.0.14.

**Spec:** [docs/superpowers/specs/2026-09-22-output-targets-design.md](../specs/2026-09-22-output-targets-design.md) — phase 2 = §4.3, §5.1 (`dbt`), §5.3, §6 (`output_kind: dbt`, `canned/dbt/**`, the dbt policy lane), §7.3, §8, §9, §10 for dbt. Binding with it: the 43 phase-1 rulings in [docs/superpowers/rulings/2026-09-22-output-targets-rulings.md](../rulings/2026-09-22-output-targets-rulings.md). Two user scope additions (group P: production hand-off; group W: large workflows) are binding too and are placed in their own task groups.

> **Where this plan was superseded during execution:** every coordinator decision made after this plan was written — including two tasks the controller added mid-build, **C4V** (the documented `IDENTIFIER` form for every SQL procedure) and **N1** (the orchestrator creates the notes directory) — is recorded in [docs/superpowers/rulings/2026-09-22-output-targets-phase2-rulings.md](../rulings/2026-09-22-output-targets-phase2-rulings.md). The passages below that a ruling replaced carry a blockquote naming the ruling number(s); this plan's own prose is left as written everywhere else, as the historical argument for what was proposed.

## Global Constraints

- The design spec and the phase-1 rulings are binding over this plan. Where a spike forced a deviation from the spec's text, it is listed under **Spike-forced deviations** below and needs the controller's ruling before Task A starts; Task A amends our own spec text (never `docs/spec/**`, which is frozen), as phase-1 ruling 43 did for §4.2.
- Python: always `.venv/Scripts/python.exe` from the repo root, never bare `python`. Tests: `.venv/Scripts/python.exe -m pytest <paths>` (`addopts = "-q"` is set; never add another `-q`).
- Node: `"C:\Users\<you>\AppData\Local\Microsoft\WinGet\Links\fnm.exe" exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts`; typecheck `… exec --using=22 node.exe node_modules/typescript/bin/tsc --noEmit -p .`. In a worktree, `node_modules` and `tsc` come from the main checkout.
- Platform: Windows 10 + Git Bash + Python 3.14.2 + Node 22 via fnm; PowerShell 7 only for `scripts/dev/serve_model.ps1`.
- Scripts: `main(argv=None) -> int`; exit `0` success / `1` domain failure / `2` usage or crash; every script takes `--root` and writes only under it.
- Files UTF-8 with LF; JSON through `lib.io.write_json` (indent 2, trailing newline).
- Hand-off hygiene: no absolute machine path, no OS login name and no pointer to an agent's scratch directory in ANY committed file — this plan included (`tests/test_committed_workflows.py`'s three scans walk every tracked file). Write `C:\Users\<you>\…` and "a scratch root outside the repo".
- `docs/spec/**` is frozen. `scripts/compare.py` is not modified by any task.
- `requirements.txt` already carries `dbt-core>=1.12` and `dbt-duckdb>=1.11`; no task adds a dependency or downloads anything. `dbt-snowflake` is NOT installed; the `snowflake` output of the dbt profile is documented as unverified.
- Honesty: nothing claims a run on real Snowflake, real Alteryx or GitHub-hosted models. Every doc that relies on a local double says what that double does not prove (spec §9). Numbers about data come only from scripts.
- dbt is only ever run through `scripts/lib/dbt_project.run_dbt`; an agent never runs `dbt` (policy denies it).
- TDD: every new test is written first and run RED with the failure named in the step; zero skips (every suite run reports `0 skipped`); vocabularies (statuses, verdicts, diff classes, targets) unchanged.
- Git: conventional commits, `wip:` commits allowed, trailer `Co-Authored-By: <model-accurate trailer>`; never `git stash`, `git checkout --` or `git reset --hard`; no push. Parallel tasks run in their own worktree `.worktrees/p2-<task>` on branch `wt/p2-<task>`, merged by the controller with `--no-ff`; at most TWO implementer agents at a time (phase-1 rulings 1–2).
- Never run the pipeline in the repo root; offline and live runs happen in a scratch root outside the repo.
- Baseline at the dispatch base (`feat/output-targets-phase2` = `main` @ `c0c02a2`): pytest 1306 passed / 0 skipped, node 179/179, tsc clean. Re-measure before Task A; every task's final step re-runs the suites it touched plus the full pytest suite.
- The dbt tests add roughly 3–4 minutes to the pytest suite (each `dbt` invocation measured at 2.4–3.4 s on this machine); they are NOT marked or skipped.

## Spike results (facts, measured 2026-09-22 in a throwaway directory outside the repo)

Versions: Python 3.14.2, dbt-core 1.12.5, dbt-duckdb 1.11.0, dbt-adapters 1.24.5, duckdb 1.5.5, sqlglot 30.18.0, snowflake-snowpark-python 1.55.0, `@github/copilot-sdk` as installed under `node_modules/`.

The spike project was a four-model dbt-duckdb project (`wf0099_seg_01_out` table reading `{{ source('src','TARGETS') }}`, a `table` model, a `merge` incremental with `unique_key=['REGION','PERIOD']`, an `append` incremental) over a sandbox DuckDB file built with the repo's own `lib.backend.DuckDBBackend` exactly as `load_golden.load_set` builds one (an input table under `MIG_GOLDEN`, a view `MIGDB.MIG_GOLDEN_WF0099_NORMAL.TARGETS`, two `targets_before` tables under `MIGDB.MIG_WORK`).

**S1 — invoking dbt.** Both paths work.

```bash
export DBT_SEND_ANONYMOUS_USAGE_STATS=false MIG_DBT_DUCKDB_PATH="$SPIKE/dbt_sandbox_normal.duckdb"
.venv/Scripts/dbt.exe parse --project-dir proj --profiles-dir proj --target local \
  --target-path "$SPIKE/tgt" --log-path "$SPIKE/logs" --no-use-colors \
  --vars '{"src_schema": "MIGDB__MIG_GOLDEN_WF0099_NORMAL", "tgt_schema": "MIGDB__MIG_WORK"}'
# exit 0 in 3.07 s; `dbt run` of the four models: exit 0 in 2.9-3.4 s
```

- `subprocess.run([<.venv/Scripts/dbt.exe>, "run", …])` from Python, with the project in a directory whose path contains spaces: exit 0 in 3.02 s; `run_results.json` in `--target-path` lists every model with its `status`; the project directory is byte-identical afterwards (with `--target-path`/`--log-path` outside it); `--no-use-colors` leaves no ANSI escape; output lines end in CRLF on Windows.
- `dbt.cli.main.dbtRunner().invoke([...])` in-process: 1.39 s first call, 0.67 s after; the DuckDB file could be deleted right after. `python -m dbt.cli.main` works but warns (`RuntimeWarning: 'dbt.cli.main' found in sys.modules …`); there is no `dbt/__main__.py`.
- Exit codes: `0` success; `1` a model failed at run time (`run_results.json` has `status: "error"` and the DuckDB message for that model); `2` an error before any model runs — e.g. `{{ ref('nope') }}` gives `Compilation Error … depends on a node named 'nope' which was not found` and writes no `run_results.json`. `dbt parse` returns the same `2` for that error.
- `dbt parse` never opens or creates the DuckDB file (the directory named by `MIG_DBT_DUCKDB_PATH` did not exist and was not created) and writes `manifest.json` into `--target-path`: every model's `config` (`materialized`, `incremental_strategy`, `unique_key`, `alias`, `pre-hook`), its `columns` from `schema.yml`, and every source with its rendered schema and columns.
- With `DBT_SEND_ANONYMOUS_USAGE_STATS=false` nothing was written under the home directory's `.dbt/`.
- **Decision:** a subprocess of the console script found through `sysconfig.get_path("scripts")` — the spec asks for a subprocess, it isolates dbt's process-global state per invocation, and its exit code plus `run_results.json` give the failing model names. The in-process runner is ~2.3 s faster per call and is recorded here as the fallback if suite time ever matters.

**S2 — catalog, schemas, sources.**

- `DuckDBBackend` flattens `MIGDB.<SCHEMA>.<T>` to schema `MIGDB__<SCHEMA>` inside the file's own catalog (`local_name("MIGDB.MIG_GOLDEN_WF0099_NORMAL.TARGETS") == ("MIGDB__MIG_GOLDEN_WF0099_NORMAL", "TARGETS")`); a two-part `MIG_WORK.X` stays schema `MIG_WORK`.
- The file's catalog is named from the file name. DuckDB 1.5.5 names `.sandbox.normal.duckdb`'s catalog `sandbox`; dbt-duckdb 1.11.0 derives `.sandbox.normal` → `Binder Error: Catalog ".sandbox.normal" does not exist!`. `.sandbox.duckdb` fails the same way (`Catalog ".sandbox"`). Setting `database: sandbox` in the profile is refused (`Inconsistency detected between 'path' and 'database' fields in profile`). `dbt_sandbox_normal.duckdb` works (catalog `dbt_sandbox_normal` on both sides).
- `var()` inside `profiles.yml` renders only from `--vars` on the command line: project-level `vars: {tgt_schema: null}` does not reach the profile (`Required var 'tgt_schema' not found in config`).
- `sources.yml` with `schema: "{{ var('src_schema') }}"` and `--vars src_schema=MIGDB__MIG_GOLDEN_WF0099_NORMAL` read the view `load_set` created; the view's own reference to `MIG_GOLDEN.WF0099_NORMAL_IN_1` resolved inside the attached file.
- **Decision:** sandboxes are `workflows/<wf>/dbt_sandbox_<set>.duckdb`; the local output's `path` is `"{{ env_var('MIG_DBT_DUCKDB_PATH', 'dbt_sandbox.duckdb') }}"`; the `--vars` a local run passes are the flattened names from `backend.local_name` (`src_schema = MIGDB__MIG_GOLDEN_<WF>_<SET>`, `tgt_schema = MIGDB__MIG_WORK`), which are exactly the spec's `MIG_GOLDEN_<WF>_<SET>` / `MIG_WORK` under database `MIGDB` in Snowflake terms.

**S3 — materialisations against `targets_before`, and idempotency.**

- A model named `attainment_history` against the pre-existing `ATTAINMENT_HISTORY` (created by `load_set`) fails: `When searching for a relation, dbt found an approximate match. … Searched for: "sb1"."MIGDB__MIG_WORK"."attainment_history" Found: "sb1"."MIGDB__MIG_WORK"."ATTAINMENT_HISTORY"`. A project-level `quoting: {identifier: false}` does NOT fix it. `config(alias='ATTAINMENT_HISTORY')` does.
- With the alias: `incremental_strategy='merge', unique_key=['REGION','PERIOD']` updated the matched `targets_before` row (`EAST 2026-01: 1.00 → 100.00`), inserted new keys and kept an unmatched old row (`NORTH 2025-01`); dbt-duckdb emits `MERGE INTO … UPDATE BY NAME / INSERT BY NAME`, so a target column the model does not produce is left untouched. `append` inserted after the old row.
- Second `dbt run` on the SAME sandbox: `table` and `merge` models identical, `append` doubled (expected). Second run from a FRESH sandbox built the same way: every table identical (`diff --strip-trailing-cr` of the dumps empty).
- `unique_key=['REGION']` (too narrow) runs with exit 0 and silently loses rows (`EAST 2026-02` never inserted, one `EAST` row updated) — a row-level LOGIC difference, no error.

**S4 — case.** Model columns keep the case written in the SQL; DuckDB matches identifiers case-insensitively even when quoted (`select count(*) from "MIGDB__MIG_WORK"."WF0099_SEG_01_OUT"` found `wf0099_seg_01_out`); `DuckDBBackend(<sandbox>).table_exists("MIGDB.MIG_WORK.WF0099_SEG_01_OUT")` is true and `compare.compare(backend, "MIG_COMPARE.EXPECTED_0", "MIGDB.MIG_WORK.WF0099_SEG_01_OUT", …)` over the dbt-built table returned `PASS` (compare upper-cases column names itself).

**S5 — SDK events (for W4) and GPU facts (for H).** `node_modules/@github/copilot-sdk/dist/generated/session-events.d.ts` defines `"session.compaction_start"`, `"session.compaction_complete"` (`data.success: boolean`, `data.preCompactionTokens`, `data.tokensRemoved`) and `"assistant.usage"` (`data.inputTokens`); `dist/session.d.ts:190` has `on<K extends SessionEventType>(eventType: K, handler) => () => void`; `dist/types.d.ts:2743` `MessageOptions.mode?: "enqueue" | "immediate"`; `dist/types.d.ts:1907` `contextTier?: ContextTier`. Controller measurement (RTX 5070 Ti, 16303 MiB, ~1816 MiB used by other apps at idle, llama-server build b10685, same flags as `scripts/dev/serve_model.ps1`): `-Context 131072 -CacheTypeK q4_0 -CacheTypeV q4_0` loads at 12321 MiB total; `-Context 262144 -CacheTypeK q4_0 -CacheTypeV q4_0` loads and answered a chat request at 15265–15273 MiB total; the script already accepts both parameters.

### Spike-forced deviations (controller ruling needed before Task A)

| # | Spec text | This plan | Why |
|---|---|---|---|
| DV1 | §5.3 sandbox `workflows/<wf>/.sandbox.<set>.duckdb` | `workflows/<wf>/dbt_sandbox_<set>.duckdb` | S2: dot-prefixed names break dbt-duckdb's catalog name |
| DV2 | §4.3 profile `local`: `path: ../.sandbox.duckdb`, `schema: MIG_WORK` | `path: "{{ env_var('MIG_DBT_DUCKDB_PATH', 'dbt_sandbox.duckdb') }}"`, `schema: "{{ var('tgt_schema') }}"`; the whole file is one fixed template (`dbt_project.PROFILES_TEMPLATE`) that `compile_check` compares byte for byte | S2; a template makes "no credentials in the file, ever" mechanically true |
| DV3 | §5.3 `--vars '{"src_schema": "MIG_GOLDEN_<WF>_<SET>", "tgt_schema": "MIG_WORK"}'` | locally the flattened names `MIGDB__MIG_GOLDEN_<WF>_<SET>` / `MIGDB__MIG_WORK` (`dbt_project.local_vars`) | S2: that is where `load_set` puts them in the DuckDB double |
| DV4 | §4.3 `models/<logical>.sql` | same file name, lower-cased, plus `alias='<LOGICAL>'` (upper case) on every target model | S3: dbt's approximate-match refusal |
| DV5 | §6 translator lane "`dbt/**`" | a strict subset: `dbt/{dbt_project.yml,profiles.yml,README.md,translation_notes.md,fix_log.md}` and `dbt/models/**`; the reviewer writes `dbt/review.json`, the script writes `dbt/compile_check.json` | the translator must not be able to write the review or the check report |
| DV6 | §5.1 `compile_check.py … --target dbt` (segment unspecified) | `compile_check.py <wf> --target dbt` takes no segment | a dbt project is one unit |
| DV7 | §5.3 "a second `dbt run` from the same starting state" | two runs from two FRESH sandboxes, compared as row multisets (phase-1 ruling 39's semantics) | S3: that is "the same starting state"; an append doubles on a shared sandbox by design |

> **Superseded during execution (rulings, see the phase's rulings file):** the "Spec text" column above is
> `docs/superpowers/specs/2026-09-22-output-targets-design.md`'s own wording; DV1-DV7 are the controller's rulings 5-11,
> accepted before Task A started, and the design spec's text stays as written (it is never edited) because the rulings
> file is now the record of the difference.

Other rulings this plan asks for are listed where they arise: R-H1 (live-test time budget, Task H), R-C1 (every translated sample's `docs/migration.md` gains a `## Deployment` section, Task C), R-B1 (`validation.combine` disambiguates a duplicate checks key, Task B), R-W1 (chain classification, Task W1).

## Decomposition

The requested groups A–H are kept, with four re-cuts, and the user's two scope additions become groups **W** (large workflows) and **P** (production hand-off):

- The Deployment-section requirement for canned docs moves into **C** (it owns every `samples/**` edit); the documenter agent text stays in **D** (it owns `.github/**` in its wave).
- The three phase-1 leftovers go into **F** with `prompt_context.py`, because the deny-list edit touches `.github/agents/translator.agent.md` and `docs/reference/output-targets.md`, which **D** owns one wave earlier; F runs after D.
- `lib.validation.combine`'s duplicate-key fix goes into **B**: `wf_0007` feeds two targets from one stream, the first sample that does.
- `prompt_context.py` is created in **F** and extended with `--batch` in **W2**; `validate_dbt.py` is created in **B**, gains `validation_workflow.json` in **W1** and `--backend` in **P2**. Each file has one owner per wave.
- The third scope addition splits in two: `docs/production-backlog.md` goes into **P4** (it is the hand-off guide's first-week checklist and shares its path-pinning test); the README's Mermaid diagrams become their own final task **P5**, after every other task including the live test, because they must draw what was actually built.

### Execution order, parallel groups, dependencies, model tier

At most two implementers run at once, each in its own worktree; the two tasks in a wave touch disjoint files (the ownership column proves it).

| Wave | Task | Title | Depends on | Tier | Owns (files it creates or modifies) |
|---|---|---|---|---|---|
| 1 | A | dbt artefact contract + `compile_check --target dbt` | — | standard | `scripts/lib/dbt_project.py`, `scripts/compile_check.py`, `tests/dbt_fixtures.py`, `tests/test_dbt_project.py`, `tests/test_compile_check_dbt.py`, `.gitignore`, spec §4.3/§5.3/§6 text |
| 1 | W3 | size-aware segmentation | — | standard | `scripts/segment.py`, `mappings/global.yaml`, `tests/test_segment_prompt_size.py`, `tests/test_foundations.py`, `docs/reference/large-workflows.md` (new) |
| 2 | B | `validate_dbt.py` | A | standard | `scripts/validate_dbt.py`, `scripts/lib/validation.py`, `scripts/validate_segment.py` (re-export only), `tests/test_validate_dbt.py`, `tests/test_validation_combine.py` |
| 2 | P3 | real Alteryx corpus triage | — | standard | `scripts/survey_corpus.py`, `tests/test_survey_corpus.py`, `tests/corpus_fixtures/**`, `docs/reference/real-workflows.md` (new) |
| 3 | C | sample `wf_0007` (dbt) | A, B | most capable | `samples/wf_0007/**`, `samples/_tools/make_golden_inputs.py`, `samples/wf_000{1,2,3,4}/canned/docs/migration.md`, `tests/helpers.py`, `tests/test_e2e_parity.py`, `tests/test_canned_artifacts.py`, `tests/test_target_check.py` |
| 3 | D | orchestrator dbt dispatch, agents, reference docs | A, B | most capable | `orchestrator/{types,stages,runner,policy,hooks}.ts`, `orchestrator/test/{fakes,stages.test,policy.test,runner.test}.ts`, `.github/agents/{translator,reviewer,validator,fixer,documenter}.agent.md`, `.github/copilot-instructions.md`, `tests/test_agents_config.py`, `docs/reference/output-targets.md`, `README.md` (§1 targets section) |
| 4 | E | `cookbook/snowpark.md`, `cookbook/dbt.md` + executable examples | A | standard | `cookbook/{snowpark,dbt,index}.md`, `tests/cookbook_examples/{snowpark,dbt}/**`, `tests/test_cookbook_snowpark.py`, `tests/test_cookbook_dbt.py`, `tests/test_cookbook_examples.py` |
| 4 | F | `prompt_context.py` + orchestrator use; deny list; intake `output_target` question | D | standard | `scripts/prompt_context.py`, `tests/test_prompt_context.py`, `orchestrator/stages.ts`, `orchestrator/test/{fakes,stages.test}.ts`, `scripts/intake_prompt.py`, `tests/test_intake_output_target.py`, `scripts/lib/snowpark_rules.py`, `tests/test_snowpark_rules.py`, `tests/test_agents_config.py`, spec §4.2, `.github/agents/translator.agent.md`, `docs/reference/output-targets.md` |
| 5 | W1 | workflow-level chain validation | B, D, F | most capable | `scripts/validate_workflow.py`, `scripts/lib/handoff.py`, `scripts/lib/types_map.py`, `scripts/lib/validation.py`, `scripts/validate_snowpark.py`, `scripts/validate_dbt.py`, `orchestrator/stages.ts`, `orchestrator/test/{fakes,stages.test}.ts`, `tests/chain_fixtures.py`, `tests/test_handoff.py`, `tests/test_validate_workflow.py`, `tests/test_e2e_chain.py`, `docs/reference/large-workflows.md` |
| 5 | P1 | hosted-model hook-up as configuration | D | standard | `orchestrator/{types,runner,cli}.ts`, `orchestrator/models.ts` (new), `orchestrator/test/{runner,cli,models}.test.ts`, `orchestrator.config.json`, `scripts/dev/set_models.py`, `tests/test_set_models.py`, `docs/handoff-copilot-models.md` |
| 6 | W2 | seam check, batched analyzer, stitch | F, W1, P1 | most capable | `scripts/{check_seams,plan_batches,stitch_analysis}.py`, `scripts/prompt_context.py`, `orchestrator/{stages,policy,hooks,types,runner,cli}.ts`, `orchestrator.config.json`, `orchestrator/test/{fakes,stages.test,policy.test,runner.test}.ts`, `.github/agents/analyzer.agent.md`, `tests/test_agents_config.py`, `tests/test_{check_seams,plan_batches,stitch_analysis}.py`, `tests/test_prompt_context.py`, `docs/reference/large-workflows.md` |
| 6 | P2 | real Snowflake backend + `deploy.py` | B, W1 | most capable | `scripts/lib/{backend,snowflake_conn,snowflake_sandbox,validation}.py`, `scripts/{load_golden,validate_segment,validate_snowpark,validate_dbt,validate_workflow,deploy}.py`, `tests/fake_snowflake.py`, `tests/test_{snowflake_conn,snowflake_validators,deploy}.py`, `docs/reference/snowflake-backend.md` (new) |
| 7 | W4 | compaction memory aid | W2, P1 | standard | `orchestrator/{runner,hooks,policy,stages}.ts`, `orchestrator/test/{runner,policy,stages.test}.ts`, `.github/agents/{intake,analyzer,fixer}.agent.md`, `tests/test_agents_config.py`, `docs/reference/large-workflows.md` |
| 8 | G | offline run for all seven samples; committed `workflows/wf_0007/` | A–F, W1–W4, P1–P3 | most capable | `workflows/**`, `tests/test_committed_workflows.py`, `README.md` (§6) |
| 9 | P4 | `docs/handoff-production.md` + `docs/production-backlog.md` | G | standard | `docs/handoff-production.md` (new), `docs/production-backlog.md` (new), `tests/test_handoff_production.py`, `docs/handoff-copilot-models.md`, `README.md` (pointers) |
| 10 | H | bounded live test (controller) | G, P4 | controller | `docs/live-smoke-test.md` |
| 11 | P5 | README architecture diagrams, last | every other task, H included | standard | `README.md` (§1 "Architecture at a glance" only), `tests/test_readme_diagrams.py` |

No task needs the cheap tier: every one writes tests first against contracts other tasks rely on.

## File structure

| File | Responsibility | Task |
|---|---|---|
| `scripts/lib/dbt_project.py` (new) | dbt layout, naming, the profile template, the one dbt invocation | A |
| `scripts/compile_check.py` | `--target dbt`, `compile_check_dbt` | A |
| `scripts/validate_dbt.py` (new) | whole-project validation per golden set | B (W1, P2 extend) |
| `scripts/lib/validation.py` | shared report shaping; `ordered_rows`; `chain_report`; `write_workflow_reports` | B, W1, P2 |
| `scripts/validate_workflow.py` (new) | the stitched-whole chain test | W1 (P2 extends) |
| `scripts/lib/handoff.py` (new) | one typed hand-off DuckDB ⇄ Snowpark | W1 |
| `scripts/prompt_context.py` (new) | inline intake/analyzer context, batch context | F, W2 |
| `scripts/check_seams.py`, `plan_batches.py`, `stitch_analysis.py` (new) | seam check, analyzer batches, stitched analysis | W2 |
| `scripts/segment.py` | prompt-size-aware splitting | W3 |
| `scripts/lib/snowflake_conn.py`, `snowflake_sandbox.py` (new), `scripts/deploy.py` (new) | named connections only; per-run sandbox; DDL deployment | P2 |
| `scripts/survey_corpus.py` (new) | corpus triage report | P3 |
| `scripts/dev/set_models.py` (new), `orchestrator/models.ts` (new) | model ids in all three places; catalog preflight | P1 |
| `orchestrator/stages.ts` | dbt branch, prompt context, chain check, batched analyze, notes text | D, F, W1, W2, W4 |
| `orchestrator/runner.ts` | MockRunner dbt/batch replay; per-role tier/effort; compaction | D, P1, W2, W4 |
| `orchestrator/policy.ts`, `hooks.ts`, `types.ts` | dbt lanes, batch lanes, notes lanes, ctx flags | D, W2, W4 (types also P1) |
| `samples/wf_0007/**` (new) | the dbt sample | C |
| `cookbook/snowpark.md`, `cookbook/dbt.md` (new) | target idioms with executable examples | E |
| `docs/reference/output-targets.md` | dbt sections, deny list, intake question | D, F |
| `docs/reference/large-workflows.md` (new) | chunking, seams, chain, compaction | W3, W1, W2, W4 |
| `docs/reference/snowflake-backend.md`, `docs/reference/real-workflows.md`, `docs/handoff-production.md`, `docs/production-backlog.md` (new) | production hand-off; the gaps only the company's setting can close | P2, P3, P4 |
| `README.md` §1 "Architecture at a glance" | the three Mermaid diagrams, redrawn last | P5 |

## Risks and mitigations (large workflows and the new targets)

| Risk | Mitigation (task) | Test that pins it |
|---|---|---|
| Seam schema drift between chunks: a producer's work stream and its consumer's declared input disagree on columns, type family, nullability or keys | `check_seams.py` in analyze's verify callback, park `seam-mismatch: <p>-><c> <stream>` after one retry (W2) | `tests/test_check_seams.py::test_a_type_family_change_across_a_seam_is_a_mismatch` (+ nullability, keys, table, producers); `orchestrator/test/stages.test.ts` "a seam mismatch parks analyze with seam-mismatch after one analyzer retry"; `tests/test_check_seams.py::test_every_committed_sample_has_clean_seams` |
| Composition errors invisible to per-segment tests (each segment is fed GOLDEN intermediates) | `validate_workflow.py` runs every segment on the actual upstream output (W1); translate is `VALIDATED` only if the chain passes | `tests/test_validate_workflow.py::test_each_segment_passes_alone_but_the_chain_fails_at_the_first_boundary` |
| Tolerance accumulation across chunks | `divergence_kind: "chain_drift"` → park `chain-drift: <output>` (an approval decision, never a fixer task) (W1) | `tests/test_validate_workflow.py::test_accumulated_rounding_is_chain_drift_not_a_boundary`; stages test "a chain drift parks translate with chain-drift and runs no fixer" |
| Lost cross-chunk context | every batch gets the compact whole-workflow map, `targets.json`, full detail for its segments and the producer contracts at its input seams (W2) | `tests/test_prompt_context.py::test_batch_context_carries_the_global_map_and_the_upstream_producer_contracts` |
| Non-deterministic chunking | `plan_batches.py` and `segment.py` are pure functions of their inputs (W2, W3) | `tests/test_plan_batches.py::test_batches_are_deterministic`, `tests/test_segment_prompt_size.py::test_prompt_size_splitting_is_deterministic` |
| Stitched output duplicates or drops a segment | `stitch_analysis.py` refuses (exit 1) anything but every segment exactly once (W2) | `tests/test_stitch_analysis.py::test_every_segment_appears_exactly_once_in_segment_order`, `…::test_a_segment_in_two_batches_is_refused` |
| Compaction drops decisions mid-session | durable files stay the record (contracts, `analysis/<batch>.md`), plus `notes/<role>.md` and a re-read nudge after every compaction (W4) | `orchestrator/test/runner.test.ts` "a compaction sends one notes reminder and is counted in metrics" |
| Prompt-size estimate inaccuracy (characters ≠ tokens) | conservative defaults (60 000 characters ≈ 15 000 tokens for both the segment and the analyzer budget); calibration from `assistant.usage.inputTokens` recorded per role (W4) and compared with the estimate in the live test (H) | `orchestrator/test/runner.test.ts` "assistant.usage input tokens are recorded as peakInputTokens"; H step 6 records the ratio |
| Extra sessions cost more | batching only above the budget; the seven samples keep one analyzer call; the existing tool-call budget and iteration caps apply unchanged (W2) | `tests/test_plan_batches.py::test_every_committed_sample_is_one_batch_under_the_default_budget`; stages test "a small workflow keeps one analyzer call" |
| Chain non-idempotency | the chain runs twice from fresh backends; any differing output FAILs (W1) | `tests/test_validate_workflow.py::test_a_non_deterministic_segment_makes_the_chain_non_idempotent` |
| Engine crossing at a Snowpark seam masks a type problem | one typed hand-off helper reads the REAL schema on both sides, never the contract (W1) | `tests/test_handoff.py::test_round_trip_duckdb_snowpark_duckdb_keeps_types_and_values`, `tests/test_validate_workflow.py::test_a_snowpark_segment_writing_the_wrong_type_is_a_type_difference_in_the_chain` |
| dbt adapter semantics differ from Snowflake (case folding, merge semantics, catalog naming) | alias rule, sandbox naming, documented caveats (A, D) | `tests/test_compile_check_dbt.py::test_a_target_model_without_its_upper_case_alias_is_refused`; `tests/test_dbt_project.py::test_sandbox_path_is_a_catalog_friendly_file_name` |
| A credential leaks into a file, argv, a log or a report | fixed profile template (A); named connections only, redaction (P2) | `tests/test_compile_check_dbt.py::test_a_literal_credential_in_profiles_yml_is_refused`; `tests/test_snowflake_validators.py::test_no_credential_reaches_argv_logs_or_reports` |
| An agent runs dbt or writes outside its dbt lane | policy lanes and a dbt deny (D) | `orchestrator/test/policy.test.ts` "no role may run dbt, however it is spelled" |

---

### Task A: The dbt artefact contract and `compile_check.py --target dbt`

**Tier:** standard · **Depends on:** — · **Wave 1, parallel with W3**

**Files:**
- Create: `scripts/lib/dbt_project.py`, `tests/dbt_fixtures.py`, `tests/test_dbt_project.py`, `tests/test_compile_check_dbt.py`
- Modify: `scripts/compile_check.py` (`compile_check` at :73, CLI at :293–313), `.gitignore`
- Modify (our spec, after the controller's DV ruling): `docs/superpowers/specs/2026-09-22-output-targets-design.md` §4.3 table rows `profiles.yml` and `models/<logical>.sql`, §5.1 `dbt` bullet, §5.3 first two sentences, §6 policy sentence — each amended to the DV1–DV7 text, nothing else touched

**Interfaces:**

> **Superseded during execution (rulings, see the phase's rulings file):** the final whole-branch review found that
> this task's own `dbt:model_jinja` allow-list (added in its own fix round) did not stop a Python model, an
> `on-run-start`/`on-run-end` hook, a `pre_hook`/`post_hook` carrying arbitrary SQL, or a model `SELECT` reading a raw
> file or an undeclared table — all of these passed `compile_check.py --target dbt` and some of them actually ran
> during validation. The final fix wave (rulings C1.1-C1.9) closes the whole project directory to a named file set,
> makes `dbt_project.yml` and the two project YAML files template-shaped with no Jinja outside one named variable
> reference, judges a model's `config(...)` kwargs, a hook's SQL and a model's own `SELECT` by parsing them, and adds
> six more named checks below `dbt:hooks` — `dbt:surface`, `dbt:project_yml`, `dbt:yaml`, `dbt:model_jinja` (tightened),
> `dbt:hook_sql`, `dbt:model_sql` — all gated through one choke point, `dbt_project.check_surface`, called before any
> dbt subprocess starts.

- Consumes: `lib.backend.SANDBOX_DB`, `lib.backend.local_name(fqn) -> (schema, table)`, `lib.validation.actual_table(wf_id, seg, output)`, `load_golden.golden_view_schema(wf_id, golden_set)`, `lib.paths.Repo`, `lib.vocab.DATA_LESS_TYPES`.
- Produces (`scripts/lib/dbt_project.py`): `PROFILE = "alteryx_migration"`, `DUCKDB_PATH_ENV = "MIG_DBT_DUCKDB_PATH"`, `WORK_SCHEMA = "MIG_WORK"`, `COMPILE_SRC_SCHEMA = "MIG_COMPILE"`, `DBT_TIMEOUT_S = 600`, `PROJECT_FILES`, `FAILED_STATUSES`, `PROFILES_TEMPLATE: str`, `class DbtUnavailable(RuntimeError)`, `@dataclass(frozen=True) class DbtResult(code: int, output: str, results: list[dict], manifest: dict | None)` with properties `ok`, `failed_models: list[str]`, `skipped_models: list[str]`; `project_dir(repo, wf_id) -> Path`; `model_name(output: dict) -> str`; `model_relation(wf_id, seg, output, database=SANDBOX_DB) -> str`; `local_vars(src_schema: str, tgt_schema: str = WORK_SCHEMA) -> dict[str, str]`; `sandbox_path(repo, wf_id, golden_set, suffix="") -> Path`; `dbt_executable() -> Path`; `run_dbt(command, project, *, vars, duckdb_path, target="local", log_file=None, extra_env=None, timeout=DBT_TIMEOUT_S) -> DbtResult`; `expected_model_config(mode, keys, logical) -> dict`; `tail(text, lines=5) -> str`; `bounded(text, limit=500) -> str`.
- Produces (`scripts/compile_check.py`): `compile_check(repo, wf_id, seg: str | None, target="auto") -> dict`; `compile_check_dbt(repo, wf_id) -> dict` writing `workflows/<wf>/dbt/compile_check.json` = `{"status": "OK"|"ERROR", "target": "dbt", "errors": [...], "statements": 0, "models": int}`; CLI `compile_check.py <wf> --target dbt` (no segment); named checks `dbt:layout`, `dbt:profiles`, `dbt:parse`, `dbt:model_missing`, `dbt:model_config`, `dbt:columns`, `dbt:sources`, `dbt:tool_comments`, `dbt:hooks`.
- Produces (`tests/dbt_fixtures.py`): `WF = "wf_0009"`, `SEGMENTS = ["seg_01", "seg_02"]`, `MODEL_FILES: dict[str, str]` (relative path under `dbt/` → text), `build_dbt_workflow(tmp_path, *, replace: dict[str, str | None] | None = None, sets=("normal", "second")) -> Repo` (a `None` value deletes that project file).

- [ ] **Step 1: The fixture** — `tests/dbt_fixtures.py` builds a hand-made two-segment dbt workflow the way `tests/test_validate_segment.py`'s `build()` builds `wf_0009` (read it first and reuse its manifest/mappings shapes). Data (all through `lib.typed_csv.write_table`):

| File | Fields | `normal` rows | `second` rows |
|---|---|---|---|
| `golden/inputs/<set>/1.csv` (ITEMS) | `ID Int64`, `NOTE V_String 10`, `AMOUNT FixedDecimal 19,2` | `[1,"keep","10.00"]`, `[2,"drop","5.00"]`, `[3,"keep",None]`, `[4,None,"1.00"]` | `[5,"keep","2.50"]`, `[6,"keep","3.50"]` |
| `golden/intermediates/seg_01/<set>/2_T.csv` | same | rows 1 and 3 | rows 5 and 6 |
| `golden/outputs/<set>/4.csv` (ITEMS_OUT) | same | rows 1 and 3 | rows 5 and 6 |
| `golden/targets_before/<set>/ITEMS_HIST.csv` | same | `[1,"old","0.00"]`, `[7,"x","7.00"]` | same |
| `golden/outputs/<set>/5.csv` (ITEMS_HIST after the merge on ID) | same | `[1,"keep","10.00"]`, `[3,"keep",None]`, `[7,"x","7.00"]` | `[1,"old","0.00"]`, `[5,"keep","2.50"]`, `[6,"keep","3.50"]`, `[7,"x","7.00"]` |

`manifest.json`: `{"id": "wf_0009", "golden_sets": [...sets], "output_kind": "dbt", "status": {}, "metrics": {}}`. `intake/mappings.yaml`: source `items.yxdb` → `{snowflake: SRC.RAW.ITEMS, logical: ITEMS, tool_ids: ["1"]}`; outputs `out/items_out.yxdb` → `{logical: ITEMS_OUT, tool_ids: ["4"], mode: overwrite, keys: []}` and `alias:dw/dbo.items_hist` → `{logical: ITEMS_HIST, tool_ids: ["5"], mode: merge, keys: [ID]}`. `segments/order.json` `[["seg_01"], ["seg_02"]]`; `segments/seg_01/dag.json` nodes `1 input`, `2 filter`; `segments/seg_02/dag.json` nodes `4 output`, `5 output` (config `{"pre_sql": None, "post_sql": None, "write_mode": …}`). Contracts: `seg_01` `inputs [{"logical": "ITEMS", "tool_id": "1", "columns": [ID NUMBER(38,0), NOTE VARCHAR(10), AMOUNT NUMBER(19,2)], "keys": []}]`, `outputs [{"stream": "2_T", "kind": "work", "table": "MIG_WORK.WF0009_SEG_01_OUT", "logical": None, "columns": [...], "keys": []}]`; `seg_02` `inputs [{"from": "seg_01", "stream": "2_T", "table": "MIG_WORK.WF0009_SEG_01_OUT", "columns": [...], "keys": []}]`, `outputs` two targets on the SAME stream `2_T`: `tool_id "4"`, `logical ITEMS_OUT`, and `tool_id "5"`, `logical ITEMS_HIST`, `keys ["ID"]`, ID `nullable: false`. Every contract has `"target": "sql"`. `MODEL_FILES` (under `dbt/`):

```sql
-- models/wf0009_seg_01_out.sql
-- wf_0009 / seg_01: tools 1-2, the work stream MIG_WORK.WF0009_SEG_01_OUT (stream 2_T)
{{ config(materialized='table') }}
with
-- tool 1: Input Data -- logical ITEMS
t1_input as (
    select ID, NOTE, AMOUNT from {{ source('src', 'ITEMS') }}
),
-- tool 2 (anchor T): Filter NOTE = 'keep' -- a NULL NOTE is not true, so the row is dropped
t2_filter_t as (
    select ID, NOTE, AMOUNT from t1_input where NOTE = 'keep'
)
select ID, NOTE, AMOUNT from t2_filter_t
```

```sql
-- models/items_out.sql
-- tool 4: Output Data (overwrite, logical ITEMS_OUT)
{{ config(materialized='table', alias='ITEMS_OUT') }}
select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}
```

```sql
-- models/items_hist.sql
-- tool 5: Output Data (Update; Insert if new on ID -> merge, logical ITEMS_HIST)
{{ config(materialized='incremental', incremental_strategy='merge', unique_key=['ID'], alias='ITEMS_HIST') }}
select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}
```

plus `dbt_project.yml` (`name: wf_0009`, `version: "1.0.0"`, `config-version: 2`, `profile: alteryx_migration`, `model-paths: [models]`, `vars: {src_schema: null, tgt_schema: null}`), `profiles.yml` = `dbt_project.PROFILES_TEMPLATE`, `models/sources.yml` (source `src`, `schema: "{{ var('src_schema') }}"`, table `ITEMS` with columns ID, NOTE, AMOUNT), `models/schema.yml` (the three models with their columns in contract order; `items_hist.ID` has `tests: [not_null, unique]`), `README.md` (two lines). The fixture imports `PROFILES_TEMPLATE` from `lib.dbt_project`, so it fails to import until Step 3 — that is the RED.

- [ ] **Step 2: Failing tests**

`tests/test_dbt_project.py`:

```python
"""scripts/lib/dbt_project.py: layout, naming and the one way dbt is run."""
from __future__ import annotations

import subprocess
import sysconfig

import pytest

from lib import dbt_project as dp
from lib.backend import DuckDBBackend
from load_golden import load_set
from tests.dbt_fixtures import WF, build_dbt_workflow

WORK = {"stream": "2_T", "kind": "work", "table": "MIG_WORK.WF0009_SEG_01_OUT", "logical": None}
TARGET = {"stream": "2_T", "kind": "target", "tool_id": "5", "logical": "ITEMS_HIST", "table": None}


def test_model_name_is_the_logical_or_the_work_table_lower_cased():
    assert dp.model_name(TARGET) == "items_hist"
    assert dp.model_name(WORK) == "wf0009_seg_01_out"


def test_model_relation_puts_every_model_in_mig_work():
    assert dp.model_relation(WF, "seg_02", TARGET) == "MIGDB.MIG_WORK.ITEMS_HIST"
    assert dp.model_relation(WF, "seg_01", WORK) == "MIGDB.MIG_WORK.WF0009_SEG_01_OUT"
    assert dp.model_relation(WF, "seg_01", WORK, database="SANDBOX") == "SANDBOX.MIG_WORK.WF0009_SEG_01_OUT"


def test_local_vars_are_the_flattened_duckdb_schema_names():
    assert dp.local_vars("MIG_GOLDEN_WF0009_NORMAL") == {
        "src_schema": "MIGDB__MIG_GOLDEN_WF0009_NORMAL", "tgt_schema": "MIGDB__MIG_WORK"}


def test_sandbox_path_is_a_catalog_friendly_file_name(tmp_path):
    repo = build_dbt_workflow(tmp_path)
    path = dp.sandbox_path(repo, WF, "period_end", "_rerun")
    assert path == repo.wf(WF, "dbt_sandbox_period_end_rerun.duckdb")
    assert not path.name.startswith(".") and path.name.count(".") == 1


def test_expected_model_config_per_write_mode():
    assert dp.expected_model_config("overwrite", [], "A") == {"materialized": "table", "alias": "A"}
    assert dp.expected_model_config("append", [], "A") == {
        "materialized": "incremental", "incremental_strategy": "append", "alias": "A"}
    assert dp.expected_model_config("merge", ["id", "K"], "A") == {
        "materialized": "incremental", "incremental_strategy": "merge", "unique_key": ["ID", "K"], "alias": "A"}
    with pytest.raises(ValueError, match="update_only"):
        dp.expected_model_config("update_only", [], "A")


def _sandbox(repo, name="normal"):
    path = dp.sandbox_path(repo, WF, name)
    backend = DuckDBBackend(str(path))
    try:
        load_set(backend, repo, WF, name)
    finally:
        backend.close()
    return path


def test_run_dbt_runs_the_project_and_writes_nothing_into_it(tmp_path):
    repo = build_dbt_workflow(tmp_path)
    project = dp.project_dir(repo, WF)
    before = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
    log = project / "logs" / "validate_normal.log"
    result = dp.run_dbt("run", project, vars=dp.local_vars("MIG_GOLDEN_WF0009_NORMAL"),
                        duckdb_path=_sandbox(repo), log_file=log)
    assert result.ok and result.code == 0 and result.failed_models == []
    assert sorted(r["name"] for r in result.results) == ["items_hist", "items_out", "wf0009_seg_01_out"]
    after = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
    assert after == sorted(before + ["logs", "logs/validate_normal.log"])
    text = log.read_text(encoding="utf-8")
    assert "\x1b[" not in text and "\r\n" not in text and str(tmp_path) not in result.output


def test_run_dbt_names_the_model_that_failed(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/items_out.sql": "{{ config(materialized='table', alias='ITEMS_OUT') }}\n"
                                "select NO_SUCH_COLUMN from {{ ref('wf0009_seg_01_out') }}\n"})
    result = dp.run_dbt("run", dp.project_dir(repo, WF), vars=dp.local_vars("MIG_GOLDEN_WF0009_NORMAL"),
                        duckdb_path=_sandbox(repo))
    assert result.code == 1 and result.failed_models == ["items_out"]
    assert "NO_SUCH_COLUMN" in next(r["message"] for r in result.results if r["name"] == "items_out")


def test_run_dbt_turns_telemetry_and_colours_off(tmp_path, monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"], seen["env"] = args, kwargs["env"]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(dp.subprocess, "run", fake_run)
    dp.run_dbt("parse", tmp_path, vars={"src_schema": "S", "tgt_schema": "T"}, duckdb_path=tmp_path / "x.duckdb")
    assert seen["env"]["DBT_SEND_ANONYMOUS_USAGE_STATS"] == "false"
    assert seen["env"][dp.DUCKDB_PATH_ENV] == str((tmp_path / "x.duckdb").resolve())
    for flag in ("--no-use-colors", "--target-path", "--log-path", "--profiles-dir", "--vars"):
        assert flag in seen["args"]


def test_a_missing_console_script_is_dbt_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(tmp_path))
    with pytest.raises(dp.DbtUnavailable, match="dbt"):
        dp.dbt_executable()
```

`tests/test_compile_check_dbt.py` (each builds the fixture, optionally with `replace=`, and calls `compile_check.compile_check_dbt(repo, WF)`; the name is what it asserts):

- `test_the_fixture_project_passes_every_check` — `status == "OK"`, `target == "dbt"`, `errors == []`, `models == 3`, and `read_json(repo.wf(WF, "dbt", "compile_check.json")) == report`.
- `test_a_project_dbt_cannot_parse_is_a_dbt_parse_error` — `items_out.sql` uses `{{ ref('nope') }}`: `status == "ERROR"` and exactly one error, starting `dbt:parse: dbt parse exited 2`, containing `nope`.
- `test_a_missing_model_is_named` — `replace={"models/items_hist.sql": None}` (and its `schema.yml` entry removed): an error starting `dbt:model_missing` that names `items_hist` and `seg_02`.
- `test_a_merge_model_with_the_wrong_unique_key_is_refused` — `unique_key=['NOTE']`: an error starting `dbt:model_config` naming `items_hist` and `unique_key`.
- `test_an_overwrite_target_materialised_incremental_is_refused` — `items_out.sql` with `materialized='incremental', incremental_strategy='append'`: `dbt:model_config` naming `materialized`.
- `test_a_target_model_without_its_upper_case_alias_is_refused` — `items_out.sql` without `alias=`: `dbt:model_config` naming `alias` and `ITEMS_OUT`.
- `test_a_work_model_must_be_a_table` — `wf0009_seg_01_out.sql` with `materialized='view'`: `dbt:model_config`.
- `test_schema_yml_columns_must_equal_the_contract_columns_in_order` — `items_out`'s columns listed `NOTE, ID, AMOUNT`: `dbt:columns` naming both orders.
- `test_profiles_yml_must_be_the_template` — one space changed: `dbt:profiles`.
- `test_a_literal_credential_in_profiles_yml_is_refused` — the template plus `      password: hunter2` under `snowflake`: `dbt:profiles`, and `"hunter2"` appears in no error text (the message names the file, never its content).
- `test_sources_yml_must_declare_every_mapped_source` — `ITEMS` renamed `ITEMZ` in `sources.yml`: `dbt:sources` naming `ITEMS` (plus the resulting `dbt:parse`, since the model's `source()` breaks — assert only that a `dbt:sources` error exists).
- `test_every_data_node_needs_a_tool_comment` — `-- tool 2` removed: `dbt:tool_comments` naming tool `2`.
- `test_a_presql_needs_a_pre_hook` — seg_02 `dag.json` node 4 gets `"pre_sql": "DELETE FROM X"`: `dbt:hooks` naming tool `4`; then with `pre_hook="delete from {{ this }} where 1 = 0"` added to `items_out`'s config: `OK`.
- `test_dbt_parse_leaves_the_project_as_it_was` — the file list of `dbt/` after the check is the list before plus `compile_check.json`.
- `test_cli_exit_codes` — `main([WF, "--target", "dbt", "--root", str(tmp_path)]) == 0`; with a broken model `== 1`; `main([WF, "seg_01", "--target", "dbt", …]) == 2` (a segment with dbt); `main([WF, "--root", …]) == 2` (auto needs a segment); a workflow with no `dbt/dbt_project.yml` `== 2`; stderr of the last one names `dbt_project.yml`.

Run: `.venv/Scripts/python.exe -m pytest tests/test_dbt_project.py tests/test_compile_check_dbt.py`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'lib.dbt_project'`.

- [ ] **Step 3: `scripts/lib/dbt_project.py`**

```python
"""The dbt project an `output_kind: dbt` workflow is migrated into (design §4.3), and the ONE way
this repository runs dbt (design §5.1/§5.3).

dbt runs as a subprocess of the console script installed beside the running interpreter
(`sysconfig.get_path("scripts")`), with anonymous usage statistics and colours off and its
`target/` and `logs/` directories in a temporary directory, so a run never writes into the project
and one invocation never inherits another's process state.

Facts this module encodes (spike 2026-09-22; dbt-core 1.12.5, dbt-duckdb 1.11.0, duckdb 1.5.5):
* dbt-duckdb names the attached catalog after the whole file stem while DuckDB drops a leading
  dot, so `.sandbox.<set>.duckdb` fails: sandboxes are `dbt_sandbox_<set>.duckdb`.
* `lib.backend.DuckDBBackend` stores `MIGDB.<SCHEMA>.<T>` as schema `MIGDB__<SCHEMA>`; a local
  run's `--vars` carry those flattened names (`local_vars`).
* dbt will not adopt a pre-existing upper-case target table for a lower-case model name
  ("approximate match"), so every target model carries `alias='<LOGICAL>'`.
Nothing here has run against Snowflake; the profile's `snowflake` output is unverified
(dbt-snowflake is not installed in this environment).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sysconfig
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .backend import SANDBOX_DB, local_name
from .paths import Repo
from .validation import actual_table

PROFILE = "alteryx_migration"
DUCKDB_PATH_ENV = "MIG_DBT_DUCKDB_PATH"
WORK_SCHEMA = "MIG_WORK"
COMPILE_SRC_SCHEMA = "MIG_COMPILE"
DBT_TIMEOUT_S = 600
PROJECT_FILES = ("dbt_project.yml", "profiles.yml", "models/sources.yml", "models/schema.yml", "README.md")
FAILED_STATUSES = frozenset({"error", "fail", "runtime error"})
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

PROFILES_TEMPLATE = """\
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
"""


class DbtUnavailable(RuntimeError):
    """No dbt console script beside this interpreter: a usage problem, never a domain verdict."""


@dataclass(frozen=True)
class DbtResult:
    code: int
    output: str                 # stdout+stderr: ANSI stripped, LF endings, temp/project paths redacted
    results: list[dict]         # run_results.json: {"name", "unique_id", "status", "message"} per node
    manifest: dict | None       # target/manifest.json when dbt wrote one (parse, run)

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def failed_models(self) -> list[str]:
        return sorted(r["name"] for r in self.results if r["status"] in FAILED_STATUSES)

    @property
    def skipped_models(self) -> list[str]:
        return sorted(r["name"] for r in self.results if r["status"] == "skipped")


def project_dir(repo: Repo, wf_id: str) -> Path:
    return repo.wf(wf_id, "dbt")


def model_name(output: dict) -> str:
    """A target's model is its logical name lower-cased; a work stream's is its MIG_WORK table name
    lower-cased (design §4.3)."""
    if output.get("kind") == "target":
        return str(output["logical"]).lower()
    return str(output["table"]).split(".")[-1].lower()


def model_relation(wf_id: str, seg: str, output: dict, database: str = SANDBOX_DB) -> str:
    """Where a model's table is read back: every model lands in `tgt_schema` (DV2), which is
    `<database>.MIG_WORK` for a validation run."""
    if output.get("kind") == "target":
        return actual_table(wf_id, seg, output).replace(f"{SANDBOX_DB}.", f"{database}.", 1)
    return f"{database}.{WORK_SCHEMA}.{str(output['table']).split('.')[-1].upper()}"


def local_vars(src_schema: str, tgt_schema: str = WORK_SCHEMA) -> dict[str, str]:
    return {"src_schema": local_name(f"{SANDBOX_DB}.{src_schema}.X")[0],
            "tgt_schema": local_name(f"{SANDBOX_DB}.{tgt_schema}.X")[0]}


def sandbox_path(repo: Repo, wf_id: str, golden_set: str, suffix: str = "") -> Path:
    return repo.wf(wf_id, f"dbt_sandbox_{golden_set}{suffix}.duckdb")


def dbt_executable() -> Path:
    scripts = Path(sysconfig.get_path("scripts"))
    for name in ("dbt.exe", "dbt"):
        if (scripts / name).is_file():
            return scripts / name
    raise DbtUnavailable(f"no dbt console script in {scripts.name}/ beside this interpreter; "
                         f"install requirements.txt into it")


def expected_model_config(mode: str, keys: list[str], logical: str) -> dict:
    if mode == "overwrite":
        return {"materialized": "table", "alias": logical}
    if mode == "append":
        return {"materialized": "incremental", "incremental_strategy": "append", "alias": logical}
    if mode == "merge":
        return {"materialized": "incremental", "incremental_strategy": "merge",
                "unique_key": sorted(str(k).upper() for k in keys), "alias": logical}
    raise ValueError(f"write mode {mode!r} has no dbt model config (target_check.py blocks it)")


def tail(text: str, lines: int = 5) -> str:
    return " | ".join(line.strip() for line in text.strip().splitlines()[-lines:])


def bounded(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clean(text: str, replacements: dict[str, str]) -> str:
    text = _ANSI.sub("", text).replace("\r\n", "\n")
    for raw, placeholder in replacements.items():
        for form in {raw, raw.replace("\\", "/"), raw.replace("/", "\\")}:
            text = text.replace(form, placeholder)
    return text


def run_dbt(command: str, project: Path, *, vars: dict[str, str], duckdb_path: Path | None,
            target: str = "local", log_file: Path | None = None,
            extra_env: dict[str, str] | None = None, timeout: int = DBT_TIMEOUT_S) -> DbtResult:
    project = Path(project).resolve()
    executable = dbt_executable()
    with tempfile.TemporaryDirectory(prefix="dbt-") as tmp:
        target_path, log_path = Path(tmp, "target"), Path(tmp, "logs")
        args = [str(executable), command, "--project-dir", str(project), "--profiles-dir", str(project),
                "--target", target, "--target-path", str(target_path), "--log-path", str(log_path),
                "--vars", json.dumps(vars, sort_keys=True), "--no-use-colors"]
        env = {**os.environ, "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DO_NOT_TRACK": "1",
               **(extra_env or {})}
        if duckdb_path is not None:
            env[DUCKDB_PATH_ENV] = str(Path(duckdb_path).resolve())
        completed = subprocess.run(args, cwd=project, env=env, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout)
        redact = {tmp: "<dbt-temp>", str(project): "<project>"}
        output = _clean((completed.stdout or "") + (completed.stderr or ""), redact)
        results = []
        run_results = target_path / "run_results.json"
        if run_results.is_file():
            for r in json.loads(run_results.read_text(encoding="utf-8")).get("results") or []:
                results.append({"name": r["unique_id"].split(".")[-1], "unique_id": r["unique_id"],
                                "status": str(r.get("status")), "message": _clean(r.get("message") or "", redact)})
        manifest_path = target_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(output, encoding="utf-8", newline="\n")
    return DbtResult(completed.returncode, output, results, manifest)
```

(`lib.validation` imports `compare`, which is on `sys.path` wherever `lib` is — `pyproject.toml`'s `pythonpath = ["scripts", "."]` and every script's own directory.)

- [ ] **Step 4: `compile_check_dbt`**

In `scripts/compile_check.py` (new imports: `import tempfile`, `from pathlib import Path`, `from lib import dbt_project`): `compile_check(repo, wf_id, seg, target="auto")` gains `if target == "dbt": return compile_check_dbt(repo, wf_id)` first (and raises `ValueError("… needs a segment …")` when `seg is None` for any other target); the CLI makes `seg` `nargs="?"`, adds `dbt` to `--target`'s choices, and routes the two invalid combinations to `parser.error` (exit 2); `DbtUnavailable` joins the `(FileNotFoundError, KeyError)` tuple that exits 2. Update the module docstring with a "The `dbt` target" paragraph listing the nine named checks.

```python
def compile_check_dbt(repo: Repo, wf_id: str) -> dict:
    """The dbt target's gate (design §5.1): the project is the template layout, `dbt parse` accepts
    it, and its manifest agrees with every contract -- a model per output, the config its write mode
    needs, the contract's columns in order, every mapped source, a `-- tool <id>:` comment per data
    node, a hook per PreSQL/PostSQL. Writes dbt/compile_check.json."""
    project = dbt_project.project_dir(repo, wf_id)
    if not (project / "dbt_project.yml").is_file():
        raise FileNotFoundError(f"{wf_id} has no dbt project to check: {project / 'dbt_project.yml'}")
    order = read_json(repo.wf(wf_id, "segments", "order.json"))
    segments = [seg for wave in order for seg in wave]
    contracts = {}
    for seg in segments:
        path = repo.seg(wf_id, seg, "contract.json")
        if not path.is_file():
            raise FileNotFoundError(f"{wf_id}/{seg} has no contract to check the project against: {path}")
        contracts[seg] = read_json(path)
    mappings = read_yaml(repo.wf(wf_id, "intake", "mappings.yaml")) or {}
    dags = {seg: (read_json(repo.seg(wf_id, seg, "dag.json")) if repo.seg(wf_id, seg, "dag.json").is_file()
                  else {"nodes": []}) for seg in segments}

    errors = _dbt_layout_errors(project, wf_id) + _dbt_profile_errors(project)
    models = 0
    with tempfile.TemporaryDirectory(prefix="dbt-compile-") as tmp:
        result = dbt_project.run_dbt("parse", project,
                                     vars=dbt_project.local_vars(dbt_project.COMPILE_SRC_SCHEMA),
                                     duckdb_path=Path(tmp) / "dbt_sandbox_compile.duckdb")
    if result.code != 0 or result.manifest is None:
        errors.append(f"dbt:parse: dbt parse exited {result.code}: {dbt_project.bounded(dbt_project.tail(result.output))}")
    else:
        nodes = {n["name"]: n for n in result.manifest.get("nodes", {}).values() if n.get("resource_type") == "model"}
        models = len(nodes)
        errors += _dbt_model_errors(nodes, contracts, mappings)
        errors += _dbt_source_errors(result.manifest, contracts, mappings)
        errors += _dbt_tool_comment_errors(project, dags)
        errors += _dbt_hook_errors(nodes, dags, mappings)
    report = {"status": "ERROR" if errors else "OK", "target": "dbt", "errors": sorted(set(errors)),
              "statements": 0, "models": models}
    write_json(project / "compile_check.json", report)
    return report
```

The helpers, each returning `list[str]` of `"dbt:<check>: <detail>"`:

- `_dbt_layout_errors(project, wf_id)`: every `PROJECT_FILES` entry exists (`dbt:layout: missing <file>`); `dbt_project.yml` (read with `read_yaml`) has `name == wf_id`, `profile == "alteryx_migration"`, `model-paths == ["models"]`, and a `vars` mapping containing both `src_schema` and `tgt_schema`.
- `_dbt_profile_errors(project)`: `(project / "profiles.yml").read_text(encoding="utf-8").replace("\r\n", "\n") != dbt_project.PROFILES_TEMPLATE` → `dbt:profiles: profiles.yml is not the template in scripts/lib/dbt_project.py (PROFILES_TEMPLATE); it may never carry a credential` — the message never quotes the file. (`.gitattributes` already checks text out with LF; the fixture and every writer of a project file use `newline="\n"`.)
- `_dbt_model_errors(nodes, contracts, mappings)`: for every contract output, `name = model_name(output)`; absent → `dbt:model_missing: no model models/<name>.sql for <seg> output <stream> (<kind>)`. A work output needs `config.materialized == "table"`. A target output looks up its mapping by `logical` in `mappings["outputs"]` (none → `dbt:model_config: no intake/mappings.yaml output has logical <L>`), builds `expected_model_config(mode, keys, logical)` and compares each key — `unique_key` normalised to a sorted upper-case list whether the config holds a string or a list — as `dbt:model_config: models/<name>.sql has <key>=<actual!r>; the <mode> write mode needs <expected!r>`. Columns: `[c.upper() for c in node["columns"]]` must equal the contract's column names upper-cased, in order → else `dbt:columns: models/schema.yml lists <actual> for <name>; the contract declares <expected>`.
- `_dbt_source_errors(manifest, contracts, mappings)`: the `src` source's table names equal `{s["logical"] for s in mappings["sources"].values()}` (`dbt:sources: …` naming the missing and the extra); each table's columns (upper, in order) equal the columns of any contract input with that `logical`.
- `_dbt_tool_comment_errors(project, dags)`: the concatenated text of `models/**/*.sql`; every node of every segment `dag.json` whose type is not in `DATA_LESS_TYPES` needs a match of `rf"--\s*tool\s+{re.escape(tool_id)}\b"` → else `dbt:tool_comments: no "-- tool <id>:" comment in any model`.
- `_dbt_hook_errors(nodes, dags, mappings)`: for each `output` node with a non-empty `pre_sql`/`post_sql`, its model (the mapping whose `tool_ids` contain the node's id → `logical.lower()`) needs a non-empty `config["pre-hook"]` / `config["post-hook"]` (dbt's manifest key spelling) → else `dbt:hooks: models/<name>.sql has no pre_hook for tool <id>'s PreSQL`.

`.gitignore` gains, with a comment saying validate_dbt.py keeps its console logs there and a human's own `dbt run` writes the other two:

```
workflows/*/dbt/logs/
workflows/*/dbt/target/
workflows/*/dbt/dbt_packages/
tests/cookbook_examples/dbt/*/project/logs/
```

(the sandboxes are `*.duckdb`, already ignored).

- [ ] **Step 5: Spec text** — apply DV1–DV7 to the spec sections named under **Files** (e.g. §4.3's `profiles.yml` row becomes "the fixed template `PROFILES_TEMPLATE` in `scripts/lib/dbt_project.py` (`local`: type `duckdb`, `path` from `env_var('MIG_DBT_DUCKDB_PATH', 'dbt_sandbox.duckdb')`, `schema: "{{ var('tgt_schema') }}"`; `snowflake`: every connection value from `env_var()`) — no credentials in the file, ever"). Add one sentence under §4.3: "Spike 2026-09-22 (plan phase 2): these names are what dbt-duckdb 1.11 and DuckDB 1.5 accept." Nothing outside those sections changes; `tests/test_agents_config.py::_spec_rules_bullet` slices §4.2 only, so it is unaffected.

- [ ] **Step 6: Green** — `.venv/Scripts/python.exe -m pytest tests/test_dbt_project.py tests/test_compile_check_dbt.py tests/test_compile_check.py tests/test_compile_check_snowpark.py tests/test_agents_config.py` → all pass, 0 skipped; then the full suite.

- [ ] **Step 7: Commit**

```bash
git add scripts/lib/dbt_project.py scripts/compile_check.py tests/dbt_fixtures.py tests/test_dbt_project.py tests/test_compile_check_dbt.py .gitignore docs/superpowers/specs/2026-09-22-output-targets-design.md
git commit -m "feat: dbt project contract — one dbt invocation helper, the profile template, compile_check --target dbt"
```

---

### Task B: `scripts/validate_dbt.py`

**Tier:** standard · **Depends on:** A · **Wave 2, parallel with P3**

**Files:**
- Create: `scripts/validate_dbt.py`, `tests/test_validate_dbt.py`, `tests/test_validation_combine.py`
- Modify: `scripts/lib/validation.py` (`ordered_rows` moved in; `combine` key disambiguation at :128–129), `scripts/validate_segment.py` (`_read_ordered_rows = ordered_rows` alias at :138, behaviour unchanged)

**Interfaces:**
- Consumes: Task A's `dbt_project.*`, `tests/dbt_fixtures.build_dbt_workflow`; `load_golden.load_set`, `golden_view_schema`; `lib.validation.{clear_stale_reports, combine, fail_report, missing_table_report, expected_fqn, golden_path, worst_verdict, write_reports}`; `compare.compare`.
- Produces: `validation.ordered_rows(backend, table) -> list[tuple]`; `validate_dbt.DbtRunFailed(Exception)`; `validate_dbt.validate_dbt(repo, wf_id, golden_sets=None, *, project_dir=None) -> dict[str, dict]` (segment → top-level report, each with `"target": "dbt"`); CLI `validate_dbt.py <wf> [--set NAME]... [--project DIR] [--root .]`, exit 0 every segment PASS*, 1 any FAIL, 2 usage (also `DbtUnavailable`). Files: `segments/<seg>/validation.json` + `validation.<set>.json` per segment; `dbt/logs/validate_<set>.log` and `validate_<first set>_rerun.log`; `workflows/<wf>/dbt_sandbox_<set>.duckdb` kept for inspection, the `_rerun` one deleted.

- [ ] **Step 1: Failing tests**

`tests/test_validation_combine.py` (Ruling R-B1: a `checks` key that would collide gets `:<tool_id>` appended on EVERY colliding entry; a non-colliding key is unchanged, so no committed report changes — Task G's classification pins that):

```python
from lib import validation as v

OK = {"verdict": "PASS", "checks": {"row_count": "PASS"}, "diff_clusters": [],
      "normalizations_applied": [], "needs_human": False, "truncated": False}


def test_two_targets_on_one_stream_keep_both_checks():
    outputs = [({"stream": "5_Output", "kind": "target", "tool_id": "6"}, OK),
               ({"stream": "5_Output", "kind": "target", "tool_id": "7"}, OK),
               ({"stream": "3_J", "kind": "work"}, OK)]
    report = v.combine({"segment": "seg_02"}, "normal", outputs)
    assert set(report["checks"]) == {"5_Output:target:6", "5_Output:target:7", "3_J:work"}


def test_ordered_rows_sorts_by_every_column():
    from lib.backend import DuckDBBackend
    b = DuckDBBackend()
    b.execute("CREATE TABLE MIG_WORK.T AS SELECT * FROM (VALUES (2, 'b'), (1, NULL), (1, 'a')) AS x(A, B)")
    assert v.ordered_rows(b, "MIG_WORK.T")[0][0] == 1
    b.close()
```

`tests/test_validate_dbt.py` (fixture from `tests/dbt_fixtures.py`; `vd = validate_dbt`):

- `test_a_correct_project_passes_every_set_and_every_segment` — `reports = vd.validate_dbt(repo, WF)`; `set(reports) == {"seg_01", "seg_02"}`; for each: `sets == {"normal": "PASS", "second": "PASS"}`, `idempotent is True`, `idempotency_diff == []`, `target == "dbt"`, `diff_clusters == []`; `segments/<seg>/validation.json` equals the returned report and `validation.normal.json` exists WITHOUT a `target` key; `dbt/logs/validate_normal.log`, `validate_second.log`, `validate_normal_rerun.log` exist; `dbt_sandbox_normal.duckdb` and `dbt_sandbox_second.duckdb` exist; no `*_rerun.duckdb` remains.
- `test_wrong_logic_fails_with_clusters_on_the_right_stream` — the filter becomes `NOTE = 'drop'`: `seg_01` verdict `FAIL` and every cluster has `stream == "2_T"`; `seg_02` is `FAIL` too.
- `test_a_model_that_fails_to_run_is_a_domain_fail_naming_it` — `items_out.sql` selects `NO_SUCH_COLUMN`: every segment's report `verdict == "FAIL"`, `diff_clusters == []`, `error` contains `failed models: items_out` and `NO_SUCH_COLUMN`, and does not contain `str(tmp_path)`; `vd.main([WF, "--root", str(tmp_path)]) == 1` with no `Traceback` in captured stderr.
- `test_a_non_deterministic_model_is_not_idempotent` — `items_out.sql` selects `cast(random() * 100 as decimal(19,2)) as AMOUNT`: `seg_02` has `idempotent is False`, `"MIGDB.MIG_WORK.ITEMS_OUT" in idempotency_diff`, verdict `FAIL`; `seg_01` stays `idempotent is True`.
- `test_a_missing_output_table_is_a_fail_naming_it` — `items_out.sql` deleted (and its `schema.yml` entry): `seg_02`'s `error` contains `MIGDB.MIG_WORK.ITEMS_OUT does not exist`.
- `test_usage_errors_leave_no_report` — a stale `segments/seg_01/validation.json` written first; then each of: unknown workflow (`FileNotFoundError`), `golden_sets=[]` (`ValueError`), `dbt/dbt_project.yml` deleted (`FileNotFoundError`) raises and leaves NO `validation*.json` under any segment; the CLI returns 2 for each.
- `test_a_dropped_golden_set_loses_its_stale_report` — run with both sets, then with `["normal"]`: `validation.second.json` is gone for both segments.
- `test_the_project_option_validates_another_directory` — copy `dbt/` to `tmp_path / "other"`, break the filter there, `vd.validate_dbt(repo, WF, ["normal"], project_dir=tmp_path / "other")` FAILs while `dbt/models/wf0009_seg_01_out.sql` is unchanged and `other/logs/validate_normal.log` exists.
- `test_dbt_unavailable_is_a_usage_error` — `monkeypatch.setattr(vd.dbt_project, "dbt_executable", raise DbtUnavailable)`: `main` returns 2, nothing written.

Run: `.venv/Scripts/python.exe -m pytest tests/test_validate_dbt.py tests/test_validation_combine.py`
Expected: `ModuleNotFoundError: No module named 'validate_dbt'`; `AttributeError: module 'lib.validation' has no attribute 'ordered_rows'`; the combine test FAILs with 2 keys instead of 3.

- [ ] **Step 2: `lib/validation.py`** — move `_read_ordered_rows` (validate_segment.py:138–146) into `lib/validation.py` as public `ordered_rows(backend, table)`; `validate_segment.py` imports it and keeps `_read_ordered_rows = ordered_rows`. In `combine`, build the keys first, then for every key that occurs more than once append `:<tool_id>` to each occurrence:

```python
    keys = [f"{output.get('stream')}:{output.get('kind')}" for output, _ in output_reports]
    repeated = {key for key in keys if keys.count(key) > 1}
    checks = {(f"{key}:{output.get('tool_id')}" if key in repeated else key): report["checks"]
              for key, (output, report) in zip(keys, output_reports)}
```

Run `tests/test_validate_segment.py tests/test_validate_segment_fix_round_1.py tests/test_validate_snowpark.py tests/test_validation_combine.py` → green.

- [ ] **Step 3: `scripts/validate_dbt.py`** — imports: `argparse`, `sys`, `time`, `traceback`, `Path`, `Sequence`, `compare`, `from lib import dbt_project, validation as v`, `from lib.backend import DuckDBBackend`, `from lib.dbt_project import DbtResult, DbtUnavailable`, `from lib.io import load_manifest, read_json, read_yaml`, `from lib.paths import Repo, add_root_arg`, `from lib.typed_csv import read_table`, `from load_golden import golden_view_schema, load_set`. Module docstring: what it does (spec §5.3), DV1/DV3/DV7, "the per-set dbt log is kept at `dbt/logs/validate_<set>.log` (git-ignored)", "Nothing here has run against Snowflake; dbt-duckdb's types, case folding and MERGE are DuckDB's (design §9)". The per-set loop:

```python
class DbtRunFailed(Exception):
    """`dbt run` exited non-zero: a domain FAIL for every segment of the project (it runs as one unit)."""


def _load_sandbox(repo: Repo, wf_id: str, golden_set: str, path: Path) -> None:
    for stale in (path, path.with_name(path.name + ".wal")):
        if stale.exists():
            stale.unlink()
    backend = DuckDBBackend(str(path))
    try:
        load_set(backend, repo, wf_id, golden_set)        # exactly as validate_segment loads a set
    finally:
        backend.close()


def _run_project(repo, wf_id, golden_set, project, sandbox, log_name) -> DbtResult:
    _load_sandbox(repo, wf_id, golden_set, sandbox)
    return dbt_project.run_dbt("run", project, vars=dbt_project.local_vars(golden_view_schema(wf_id, golden_set)),
                               duckdb_path=sandbox, log_file=project / "logs" / f"{log_name}.log")


def _run_error(result: DbtResult, golden_set: str) -> str:
    failed = ", ".join(result.failed_models) or "none named by dbt"
    skipped = f"; skipped: {', '.join(result.skipped_models)}" if result.skipped_models else ""
    detail = next((r["message"] for r in result.results
                   if r["status"] in dbt_project.FAILED_STATUSES and r["message"]), None) or dbt_project.tail(result.output)
    return (f"dbt run exited {result.code} on golden set {golden_set}: failed models: {failed}{skipped}. "
            f"{dbt_project.bounded(detail)}")


def _snapshot(sandbox: Path, relations: dict[str, list[tuple[dict, str]]]) -> dict[str, list[tuple] | None]:
    backend = DuckDBBackend(str(sandbox))
    try:
        return {fqn: (v.ordered_rows(backend, fqn) if backend.table_exists(fqn) else None)
                for pairs in relations.values() for _, fqn in pairs}
    finally:
        backend.close()


def _compare_set(repo, wf_id, golden_set, sandbox, segments, contracts, relations, settings, manifest) -> dict[str, dict]:
    reports: dict[str, dict] = {}
    backend = DuckDBBackend(str(sandbox))
    try:
        index = 0
        for seg in segments:
            dag_path = repo.seg(wf_id, seg, "dag.json")
            segment_dag = read_json(dag_path) if dag_path.is_file() else None
            approvals = [a for a in (manifest.get("accepted_diffs") or []) if a.get("segment") == seg]
            output_reports = []
            for output, fqn in relations[seg]:
                expected = v.expected_fqn(index)
                index += 1
                backend.load_table(expected, read_table(v.golden_path(repo, wf_id, seg, golden_set, output)))
                report = (compare.compare(backend, expected, fqn, contracts[seg], settings["tolerances"],
                                          output=output, accepted_classes=settings["accepted"],
                                          approvals=approvals, segment_dag=segment_dag, golden_set=golden_set)
                          if backend.table_exists(fqn) else v.missing_table_report(fqn))
                output_reports.append((output, report))
            reports[seg] = v.combine(contracts[seg], golden_set, output_reports)
    finally:
        backend.close()
    return reports


def validate_dbt(repo: Repo, wf_id: str, golden_sets: Sequence[str] | None = None, *,
                 project_dir: Path | None = None) -> dict[str, dict]:
    segments = _segments(repo, wf_id)                        # order.json, wave order; FileNotFoundError
    for seg in segments:
        v.clear_stale_reports(repo, wf_id, seg)              # before any prerequisite check
    project = Path(project_dir) if project_dir is not None else dbt_project.project_dir(repo, wf_id)
    contracts, sets, settings, manifest = _prerequisites(repo, wf_id, project, segments, golden_sets)
    dbt_project.dbt_executable()                             # DbtUnavailable before anything runs
    for stale in repo.wf(wf_id).glob("dbt_sandbox_*.duckdb*"):
        stale.unlink()
    relations = {seg: [(o, dbt_project.model_relation(wf_id, seg, o)) for o in contracts[seg]["outputs"]]
                 for seg in segments}
    idempotent: dict[str, tuple[bool | None, list[str]]] = {seg: (None, []) for seg in segments}
    per_set: dict[str, dict[str, dict]] = {}
    for index, golden_set in enumerate(sets):
        started = time.perf_counter()
        sandbox = dbt_project.sandbox_path(repo, wf_id, golden_set)
        result = _run_project(repo, wf_id, golden_set, project, sandbox, f"validate_{golden_set}")
        if result.code != 0:
            error = DbtRunFailed(_run_error(result, golden_set))
            reports = {seg: v.fail_report(contracts[seg], golden_set, error) for seg in segments}
        else:
            reports = _compare_set(repo, wf_id, golden_set, sandbox, segments, contracts, relations, settings, manifest)
            if index == 0:                                   # DV7: a second run from a FRESH sandbox
                first = _snapshot(sandbox, relations)
                rerun = dbt_project.sandbox_path(repo, wf_id, golden_set, "_rerun")
                again = _run_project(repo, wf_id, golden_set, project, rerun, f"validate_{golden_set}_rerun")
                second = _snapshot(rerun, relations) if again.code == 0 else None
                for path in (rerun, rerun.with_name(rerun.name + ".wal")):
                    if path.exists():
                        path.unlink()
                for seg in segments:
                    fqns = [fqn for _, fqn in relations[seg]]
                    diverging = fqns if second is None else [
                        f for f in fqns if first[f] is None or second[f] is None or first[f] != second[f]]
                    idempotent[seg] = (not diverging, diverging)
                    if diverging:
                        reports[seg]["verdict"] = v.worst_verdict([reports[seg]["verdict"], "FAIL"])
        elapsed = int(round((time.perf_counter() - started) * 1000))
        for seg in segments:
            reports[seg]["runtime_ms"] = elapsed             # the whole project ran for this set
        per_set[golden_set] = reports
    return {seg: v.write_reports(repo, wf_id, seg, sets, {s: per_set[s][seg] for s in sets},
                                 idempotent[seg], extra={"target": "dbt"})
            for seg in segments}
```

`_segments(repo, wf_id)` reads `segments/order.json` (missing → `FileNotFoundError` naming it). `_prerequisites` mirrors `validate_segment.validate_segment`'s checks (:250–285): `dbt/dbt_project.yml` under `project`, every segment's `contract.json` with non-empty `outputs`, `intake/mappings.yaml`, non-empty sets (`--set` or `manifest.golden_sets`), `settings = {"tolerances": …, "accepted": …}` from `repo.global_mappings`. `main` mirrors `validate_segment.main` (:309–331): `parser.error` for `FileNotFoundError`/`ValueError`/`DbtUnavailable` (exit 2), `traceback.print_exc(); return 2` for anything else, prints one line per segment (`wf/seg: VERDICT {sets} (idempotent=…)`), returns 0 only if every verdict starts with `PASS`.

- [ ] **Step 4: Green** — `tests/test_validate_dbt.py tests/test_validation_combine.py tests/test_validate_segment*.py tests/test_validate_snowpark.py tests/test_e2e_parity.py` → pass; full suite; 0 skipped.

- [ ] **Step 5: Commit**

```bash
git add scripts/validate_dbt.py scripts/lib/validation.py scripts/validate_segment.py tests/test_validate_dbt.py tests/test_validation_combine.py
git commit -m "feat: validate_dbt.py runs the whole dbt project per golden set on DuckDB and judges every contract output with compare.py"
```

---

### Task C: Sample `wf_0007` — regional targets as a dbt project

**Tier:** most capable · **Depends on:** A, B · **Wave 3, parallel with D**

**Files:**
- Create: `samples/wf_0007/{sample.json, README.md, source/regional_targets.yxmd}`, `samples/wf_0007/canned/{intake/plan.md, analysis.md, unsupported.json, review.json, docs/migration.md, segments/seg_01/contract.json, segments/seg_02/contract.json, dbt/**}`, `samples/wf_0007/broken_sql/{broken.json, dbt/models/attainment_history.sql, dbt/models/region_attainment.sql}`
- Modify: `samples/_tools/make_golden_inputs.py` (wf_0007 block + `SAMPLE_JSON` entry), `samples/wf_000{1,2,3,4}/canned/docs/migration.md` (a `## Deployment` section each — Ruling R-C1), `tests/helpers.py` (`prepare_workflow` copies `canned/dbt/`; new `overlay_dbt_project`), `tests/test_e2e_parity.py`, `tests/test_canned_artifacts.py`, `tests/test_target_check.py`

**Interfaces:**
- Consumes: Tasks A, B; `dev.build_samples`, `intake_prompt.apply_answers`, `target_check.target_check`.
- Produces: `tests/helpers.overlay_dbt_project(repo, wf_id, dest: Path, replacements: dict[str, Path]) -> Path`; the sample Task D's fakes mirror and Task G commits.

- [ ] **Step 1: The workflow** — `regional_targets.yxmd`, written in the XML shape of `samples/wf_0002/source/customer_orders.yxmd` (read its Join node :187–230 for `JoinInfo`/`SelectConfiguration`, and `samples/wf_0003/source/gl_period_close.yxmd` :336–365 for a db Output with `UpdateKeys` and :431–444 for `<Constants>`):

| Tool | Plugin / config | Output MetaInfo |
|---|---|---|
| Container A (ToolID 10), "Pair plan targets with actuals" | holds tools 1–3 | |
| 1 | DbFileInput `C:\data\plan\targets.yxdb` | `REGION V_String 10`, `PERIOD V_String 7`, `TARGET FixedDecimal 19,2` |
| 2 | DbFileInput `C:\data\sales\actuals.yxdb` | `REGION V_String 10`, `PERIOD V_String 7`, `ACTUAL FixedDecimal 19,2` |
| 3 | Join, `Left` on `REGION`, `PERIOD` = `Right` on `REGION`, `PERIOD`; `Right_REGION`, `Right_PERIOD` deselected | `J`: `REGION, PERIOD, TARGET, ACTUAL` |
| Container B (ToolID 11), "Current-year attainment" | holds tools 4–7 | |
| 4 | Filter `Left([PERIOD], 4) = [User.CurrentYear]` | same as J |
| 5 | Summarize: group by `REGION`, `PERIOD`; `Sum TARGET → TARGET_TOTAL`; `Sum ACTUAL → ACTUAL_TOTAL`; `Count → LINES` | as the simulator types them |
| 6 | DbFileOutput `C:\data\out\region_attainment.yxdb`, Overwrite | |
| 7 | DbFileOutput `odbc:DSN=PROD_PLAN;UID=svc_alx;PWD=__EncPwd2__|||dbo.ATTAINMENT_HISTORY`, `Update; Insert if new`, `UpdateKeys` `REGION`, `PERIOD` | |

`<Constants>`: `User.CurrentYear` = `2026`, `IsNumeric False`. `sample.json`: `{"id": "wf_0007", "title": "Regional targets and attainment", "owner": "wf_owner", "schedule": "0 6 * * 1-5", "segmentation": {"min_tools": 2, "max_tools": 40}, "expected_terminal": "VALIDATED", "output_target": "dbt", "answers": {"plan/targets.yxdb": "PLANNING.RAW.TARGETS", "sales/actuals.yxdb": "SALES.RAW.ACTUALS", "out/region_attainment.yxdb": "ANALYTICS.CURATED.REGION_ATTAINMENT", "7": "ANALYTICS.CURATED.ATTAINMENT_HISTORY"}, "logical": {"1": "TARGETS", "2": "ACTUALS", "6": "REGION_ATTAINMENT", "7": "ATTAINMENT_HISTORY"}}` — written through `make_golden_inputs.SAMPLE_JSON` like the others. Segmentation MUST come out as `seg_01 = {1, 2, 3}` (work stream `3_J`, table `MIG_WORK.WF0007_SEG_01_OUT`) and `seg_02 = {4, 5, 6, 7}`, so the year filter lives in the target models (the spec's first broken variant needs it there); if `segment.py` groups differently, adjust the containers or `min_tools`, never the tool chain.

`make_golden_inputs.py`, wf_0007 block (fields exactly the input MetaInfo; each set exercises the named behaviour; the README lists which row does what):

- `normal`: TARGETS `EAST 2026-01 100.00`, `EAST 2026-02 120.00`, `WEST 2026-01 80.00`, `WEST 2025-12 75.00` (prior year: filtered), `NORTH 2026-01 50.00` (no actual: the inner join drops it), `None 2026-01 10.00` (NULL key matches nothing); ACTUALS `EAST 2026-01 90.00`, `EAST 2026-01 15.00` (two lines on one key: the join fans out, so TARGET is summed twice — a parity risk the analysis names), `EAST 2026-02 130.00`, `WEST 2026-01 None` (NULL actual), `WEST 2025-12 70.00`, `SOUTH 2026-01 40.00` (no target). `targets_before/ATTAINMENT_HISTORY`: `EAST 2026-01` (matched: updated), `WEST 2025-12` (prior year: kept), `NORTH 2025-11` (kept), typed exactly like tool 5's output fields.
- `period_end`: December/January boundary rows (`2025-12` vs `2026-01`) for two regions, plus a before-row for `2026-01` of one of them.
- `empty`: no input rows; `targets_before` empty.
- `edge`: a byte-identical duplicate target row (the join fans it out), a region name with non-ASCII letters, a negative `ACTUAL`, and `PERIOD` `2026-1` (malformed, but its first four characters are `2026`, so the filter keeps it; the golden records what the simulator does with it).

Run the generator; `build_samples.py build --only wf_0007` must succeed and produce `golden/outputs/<set>/6.csv`, `7.csv` and `golden/intermediates/seg_01/<set>/3_J.csv`.

- [ ] **Step 2: Canned artefacts**

`canned/segments/seg_01/contract.json`, `seg_02/contract.json`: `"target": "sql"`, the usual C5 keys (copy the shape of `samples/wf_0002/canned/segments/*/contract.json`); seg_01 outputs the work stream `3_J`; seg_02 inputs `{"from": "seg_01", "stream": "3_J", "table": "MIG_WORK.WF0007_SEG_01_OUT", …}` and outputs two targets on stream `5_Output`: `tool_id "6"`, `logical "REGION_ATTAINMENT"`, `write_mode "overwrite"`, `keys ["REGION", "PERIOD"]`; `tool_id "7"`, `logical "ATTAINMENT_HISTORY"`, `write_mode "update_insert"`, `keys ["REGION", "PERIOD"]`; column types copied from the golden `.schema.json` files through `types_map.alteryx_to_snowflake`. `unsupported.json` `{"tier": "T1", "tools": []}` (match the existing key shape). `analysis.md` says both segments are `sql`, the output kind is `dbt` (quote `targets.json`'s reason), names the join fan-out and the NULL-key risks. `review.json` at the canned ROOT (`{"verdict": "PASS", "findings": []}`) — MockRunner replays it to `dbt/review.json`.

`canned/dbt/` — exactly what a translator writes:
- `dbt_project.yml` (`name: wf_0007`, the Task A shape), `profiles.yml` = `PROFILES_TEMPLATE`, `README.md` (the §4.3 content: the `local` and `snowflake` run commands, what `src_schema`/`tgt_schema` mean, `MIG_DBT_DUCKDB_PATH`), `translation_notes.md` (one assumption per line; says the seg_02 CTE chain is repeated in both target models because each model is one CTE per tool, and that `User.CurrentYear` is inlined as `'2026'` as wf_0003 inlines its constants).
- `models/sources.yml` (`TARGETS`, `ACTUALS`), `models/schema.yml` (every model, columns in contract order; `not_null` on non-nullable columns; no `unique` — the keys are composite).
- `models/wf0007_seg_01_out.sql` — `{{ config(materialized='table') }}`; CTEs `t1_input`, `t2_input`, `t3_join_j` each preceded by `-- tool <id>:`; inner join on both keys.
- `models/region_attainment.sql` — `{{ config(materialized='table', alias='REGION_ATTAINMENT') }}`; CTEs `t4_filter_t` (`where left(PERIOD, 4) = '2026'`), `t5_summarize`; `-- tool 6: Output Data (overwrite, logical REGION_ATTAINMENT)`.
- `models/attainment_history.sql` — `{{ config(materialized='incremental', incremental_strategy='merge', unique_key=['REGION', 'PERIOD'], alias='ATTAINMENT_HISTORY') }}`; the same two CTEs; `-- tool 7: Output Data (Update; Insert if new on REGION, PERIOD -> merge, logical ATTAINMENT_HISTORY)`; no `is_incremental()` filter.

`canned/intake/plan.md`, `canned/docs/migration.md` (every heading `test_the_documenter_sections_are_all_present` requires, including `## Deployment`: the `dbt run --target snowflake` command from `procs/README.md`, the `SNOWFLAKE_*` variables, that `dbt-snowflake` is not installed here and the `snowflake` output never ran).

Every golden set must PASS through `validate_dbt` with `idempotent: true`, and `compile_check.compile_check_dbt` must be `OK`, before Step 3.

- [ ] **Step 3: Broken variants** — `broken_sql/dbt/models/region_attainment.sql`: the canned model without the `t4_filter_t` CTE; `broken_sql/dbt/models/attainment_history.sql`: `unique_key=['REGION']`. Each starts with the SQL broken header `test_canned_artifacts._BROKEN_HEADER` uses, then the `{{ config(...) }}` line. Run each through `validate_dbt` on its golden set (`normal`) with `overlay_dbt_project`, THREE times, and record the observed `expect` (`class`, `columns`, `stream`) in `broken.json`; both must FAIL with `needs_human: false` and the same class every time (S3 says the narrow key is a silent row loss — the spec expects `LOGIC`, rows). Rows: `{"segment": "seg_02", "target": "dbt", "file": "dbt/models/region_attainment.sql", "golden_set": "normal", "expect": {…}, "note": "…"}` and the same for `attainment_history.sql`. For `target: "dbt"` rows, `file` is relative to `broken_sql/` (not `broken_sql/<segment>/`).

- [ ] **Step 4: Deployment sections (Ruling R-C1)** — `samples/wf_000{1,2,3,4}/canned/docs/migration.md` each gain `## Deployment` (placed before `## Assumptions`, as wf_0006's is): `procs/master.sql` and each `segments/<seg>/proc.sql` are the DDL, deployed under `MIGRATION_CI`, never from an agent session, nothing here ran on Snowflake. Only restate what the artefacts say.

- [ ] **Step 5: Tests (RED first)**

`tests/helpers.py`: in `prepare_workflow`, `dbt_canned = SAMPLES / wf_id / "canned" / "dbt"`; for a dbt sample copy only `contract.json` per segment, then `shutil.copytree(dbt_canned, repo.wf(wf_id, "dbt"))`; add

```python
def overlay_dbt_project(repo: Repo, wf_id: str, dest: Path, replacements: dict[str, Path]) -> Path:
    """A copy of the workflow's dbt project at `dest` with some files replaced -- how a broken
    dbt variant is validated without touching the project under test."""
    shutil.copytree(repo.wf(wf_id, "dbt"), dest)
    for relative, source in replacements.items():
        shutil.copy(source, dest / relative)
    return dest
```

`tests/test_e2e_parity.py`:

```python
from validate_dbt import validate_dbt
from tests.helpers import overlay_dbt_project, prepare_workflow


def _is_dbt(wf):
    return (SAMPLES / wf / "canned" / "dbt").is_dir()


@pytest.mark.parametrize("wf", ["wf_0001", "wf_0002", "wf_0003", "wf_0004", "wf_0006", "wf_0007"])
def test_hand_migration_passes_every_golden_set(tmp_path, wf):
    repo = prepare_workflow(tmp_path, wf)
    if _is_dbt(wf):
        reports = validate_dbt(repo, wf)
    else:
        reports = {seg: validate(repo, wf, seg)
                   for wave in io.read_json(repo.wf(wf, "segments", "order.json")) for seg in wave}
    for seg, r in reports.items():
        assert r["sets"] == {"normal": "PASS", "period_end": "PASS", "empty": "PASS", "edge": "PASS"}, json.dumps(r, indent=1, default=str)[:3000]
        assert r["idempotent"] is True and r["diff_clusters"] == []


@pytest.mark.parametrize("wf,case", list(broken_cases()))
def test_broken_migration_fails_with_the_right_class(tmp_path, wf, case):
    repo = prepare_workflow(tmp_path, wf)
    if case["target"] == "dbt":
        variant = SAMPLES / wf / "broken_sql" / case["file"]
        project = overlay_dbt_project(repo, wf, tmp_path / "broken_project",
                                      {case["file"].removeprefix("dbt/"): variant})
        r = validate_dbt(repo, wf, [case["golden_set"]], project_dir=project)[case["segment"]]
    else:
        r = _validator(case["target"])(repo, wf, case["segment"], [case["golden_set"]],
                                       proc_path=SAMPLES / wf / "broken_sql" / case["segment"] / case["file"])
    assert r["verdict"] == "FAIL"
    hits = [c for c in r["diff_clusters"] if c["class"] == case["expect"]["class"] and set(case["expect"]["columns"]) <= set(c["columns"])]
    assert hits, json.dumps(r["diff_clusters"], indent=1, default=str)[:3000]
```

`tests/test_canned_artifacts.py`:
- add `TARGETS = frozenset({"sql", "snowpark", "dbt"})` for broken rows only (contracts keep `{"sql", "snowpark"}` — rename the existing constant `CONTRACT_TARGETS` where contracts are checked); `DBT_WORKFLOWS = sorted(p.parents[2].name for p in SAMPLES.glob("wf_*/canned/dbt/dbt_project.yml"))`; `PROCEDURE_WORKFLOWS = [wf for wf in TRANSLATED_WORKFLOWS if wf not in DBT_WORKFLOWS]`; switch every procedure-shaped test (`…c4_signature`, `…cte_or_a_documented_exemption`, `…snowpark_segment…`, `…artifact_set_is_complete`, `…do_not_skip…`, the broken-variant tests for `.sql`/`.py`) to `PROCEDURE_WORKFLOWS`.
- `test_every_dbt_sample_passes_compile_check_dbt[wf]` — `build_workflow(wf)`, copy canned contracts and `canned/dbt/` in, `compile_check.compile_check_dbt(repo, wf)["status"] == "OK"` (errors printed on failure).
- `test_the_dbt_artifact_set_is_complete[wf]` — the five `PROJECT_FILES` plus `translation_notes.md` under `canned/dbt/`; a model file for `dbt_project.model_name(o)` of every contract output; `canned/review.json == {"verdict": "PASS", "findings": []}`; no `proc.sql`/`proc.py` under `canned/segments/`; every contract's `target == "sql"`.
- `test_every_dbt_broken_variant_is_the_canned_model_with_one_documented_mistake[wf]` — the header present; the file differs from `canned/dbt/<same path>`; its CTE names (`_cte_names`) are a subset of the canned model's.
- `test_every_broken_json_row_points_at_a_real_file_and_segment` — for `target == "dbt"` the file is `broken_sql/<file>` and starts `dbt/models/`, the workflow is in `DBT_WORKFLOWS`; for the others the existing rule; `on_disk` also globs `broken_sql/dbt/**/*.sql`.
- `test_the_documenter_sections_are_all_present` — `"deployment"` added to `required`.

`tests/test_target_check.py`:

```python
def test_the_wf_0007_sample_is_proposed_as_dbt(tmp_path):
    from tests.helpers import prepare_workflow
    repo = prepare_workflow(tmp_path, "wf_0007")
    result = tc.target_check(repo, "wf_0007", "auto")
    assert result["preference"] == "dbt"
    assert result["output_kind"] == "dbt" and result["dbt_blockers"] == []
    assert result["segments"] == {"seg_01": "sql", "seg_02": "sql"}
```

Run the new tests before Steps 1–4 exist (write them first): RED with `FileNotFoundError: samples/wf_0007/…` / missing `deployment` sections. Then: `tests/test_e2e_parity.py tests/test_canned_artifacts.py tests/test_target_check.py tests/test_samples_wellformed.py tests/test_build_samples.py` → green; full suite, 0 skipped.

- [ ] **Step 6: Commit**

```bash
git add samples/wf_0007 samples/_tools/make_golden_inputs.py samples/wf_0001/canned/docs samples/wf_0002/canned/docs samples/wf_0003/canned/docs samples/wf_0004/canned/docs tests/helpers.py tests/test_e2e_parity.py tests/test_canned_artifacts.py tests/test_target_check.py
git commit -m "feat: sample wf_0007 — a two-segment workflow migrated as one dbt project (overwrite + merge), with goldens, canned project and broken models"
```

---

### Task D: Orchestrator dbt dispatch, mock replay, policy, agents, reference docs

**Tier:** most capable · **Depends on:** A, B · **Wave 3, parallel with C**

**Files:**
- Modify: `orchestrator/types.ts` (`AgentCtx`, `AgentRunner.run`), `orchestrator/stages.ts` (`stageTranslate` :614, `runAgent` :100 ctx type, `stageDocument` :677; new `dbtReadme`, `dbtModelName`, `migrateDbt`, `translateDbt`, `failingModels`), `orchestrator/runner.ts` (`MockRunner.run`/`replay` :97–157, new `replayDbt`; `CopilotRunner.run` :291 passes `ctx?.dbt`), `orchestrator/hooks.ts` (`hooksFor(role, wf, env, segment?, dbt?)` :117; audit line carries `dbt`), `orchestrator/policy.ts` (`PolicyOptions.dbtProject`, `writeLanes` :177, `decideWrite` :785, `ROLE_SCRIPTS` :701, a dbt-executable deny in `decideShell` :889)
- Modify tests: `orchestrator/test/fakes.ts`, `orchestrator/test/stages.test.ts`, `orchestrator/test/policy.test.ts`, `orchestrator/test/runner.test.ts`
- Modify docs/agents: `.github/agents/{translator,reviewer,validator,fixer,documenter}.agent.md` (sections marked `<!-- amended: output targets phase 2 -->`), `.github/copilot-instructions.md`, `tests/test_agents_config.py`, `docs/reference/output-targets.md` (§3.3 rewritten, §4, §5, §6, §7), `README.md` ("Three output targets" section only)

**Interfaces:**
- Consumes: CLIs `compile_check.py <wf> --target dbt`, `validate_dbt.py <wf>`; file names `dbt/{compile_check.json, review.json, translation_notes.md, fix_log.md}`, `segments/<seg>/validation.json` with `"target": "dbt"`.
- Produces (TS): `export interface AgentCtx { segment?: string; iteration?: number; dbt?: boolean }`; `AgentRunner.run(role, wf, task, ctx?: AgentCtx)`; `export function dbtReadme(wfId: string): string`; `export function dbtModelName(output: { kind?: string; logical?: string | null; table?: string | null }): string` (mirror of `dbt_project.model_name`); `PolicyOptions.dbtProject?: boolean`; reasons `dbt: <reason>` on `reasons.translate`.

- [ ] **Step 1: Failing node tests.** Extend `fakes.ts` first:
  - `seedSamples`: when the scenario starts with `dbt` (`dbt`, `fix-loop:dbt`, `never-fixed:dbt`, `compile-fails:dbt`, `compile-crashes:dbt`, `needs-human:dbt`) write `canned/dbt/{dbt_project.yml, profiles.yml, README.md, translation_notes.md, models/sources.yml, models/schema.yml, models/wf0001_seg_01_out.sql, models/orders_out.sql}`, `canned/review.json` (PASS), and `broken_sql/dbt/models/orders_out.sql` (content `BROKEN_DBT`); contracts' seg_02 gets a target output `{"kind": "target", "logical": "ORDERS_OUT", "stream": "2_T", "tool_id": "5"}`.
  - fake `compile_check.py`: when `args` contain `--target dbt`, write `workflows/<id>/dbt/compile_check.json` and return 1 for `compile-fails:dbt`, 2 for `compile-crashes:dbt`, else 0.
  - fake `validate_dbt.py`: for every segment in `order.json` write `validation.json` `{segment, target: "dbt", verdict, needs_human, diff_clusters, idempotent: true}` — `FAIL` when `workflows/<id>/dbt/models/orders_out.sql` equals `BROKEN_DBT` or the scenario is `needs-human:dbt` (then `needs_human: true`), else `PASS`; exit 1 on FAIL.
  - `recording.run` records `ctx?.dbt` into `calls.tasks[i].dbt`.

`orchestrator/test/stages.test.ts` (each `makeEnv({ wf: "wf_0001", twoWaves: true, manifest: { output_target: "dbt" }, scenario })`):
  1. "a dbt workflow is translated ONCE for the whole workflow" — scenario `dbt`: `m.status.translate === "VALIDATED"`; `calls.roles` is `[intake, analyzer, translator, reviewer, validator, documenter]`; the translator/reviewer/validator tasks all have `dbt: true` and no `segment`; `calls.py` contains `["scripts/compile_check.py", ["wf_0001", "--target", "dbt"]]` exactly once and `["scripts/validate_dbt.py", ["wf_0001"]]` exactly once; no `render_snowpark.py`, no per-segment `compile_check.py`/`validate_segment.py`; `m.segment_status` is `{seg_01: "PASS", seg_02: "PASS"}`; `procs/README.md` equals `dbtReadme("wf_0001")`; `procs/master.sql` does not exist.
  2. "dbtReadme carries the exact deployment command of design §4.3" — contains `dbt run --project-dir workflows/wf_0007/dbt --profiles-dir workflows/wf_0007/dbt --target snowflake --vars '{"src_schema": "<SRC>", "tgt_schema": "<TGT>"}'` and `dbt-snowflake`.
  3. "a failing dbt project gets a fixer turn that names the failing models" — `fix-loop:dbt`: VALIDATED; the fixer ran once, with `dbt: true`, and its task contains `Failing models: orders_out (seg_02)` and `dbt/review.json`; `dbt/fix_log.md` exists.
  4. "a dbt project that never passes parks translate with a dbt reason" — `never-fixed:dbt`: `NEEDS_HUMAN`, `reasons.translate === "dbt: validation FAIL after 3 iterations"`, every `segment_status` value is `NEEDS_HUMAN` except PASSing segments; a second plain run makes no new agent call.
  5. "a dbt compile failure is quoted to the fixer" — `compile-fails:dbt`: the fixer's task contains `scripts/compile_check.py --target dbt failed` and `dbt/compile_check.json`; after 3: `dbt: compile check failed after 3 iterations`.
  6. "compile_check --target dbt exiting 2 is a script error" — `compile-crashes:dbt`: `dbt: script-error`, no reviewer call.
  7. "needs_human from validate_dbt stops after one iteration" — `needs-human:dbt`: `dbt: needs_human`, no fixer.
  8. "a dbt workflow whose segments all PASSed is not re-translated on resume" — seed `segment_status {seg_01: PASS, seg_02: PASS}`, `status.analyze/golden DONE`: no translator call, `procs/README.md` written, VALIDATED.
  9. "the documenter is told where a dbt deployment is" — its task contains `procs/README.md` and `dbt/translation_notes.md`; for a procedures workflow it contains `procs/master.sql`.
  10. "dbtModelName mirrors dbt_project.model_name" — `{kind: "target", logical: "ITEMS_HIST"}` → `items_hist`; `{kind: "work", table: "MIG_WORK.WF0009_SEG_01_OUT"}` → `wf0009_seg_01_out`.

`orchestrator/test/policy.test.ts` (with `decide(role, "wf_0007", "create", { path }, undefined, root, { dbtProject: true })`):
  - "the translator and fixer may write the dbt project files and models" — `workflows/wf_0007/dbt/models/region_attainment.sql`, `dbt/models/sub/x.sql`, `dbt/dbt_project.yml`, `dbt/profiles.yml`, `dbt/README.md`, `dbt/translation_notes.md`, `dbt/fix_log.md` → allow.
  - "…but never the review, the check report, logs or a segment's procedure" — `dbt/review.json`, `dbt/compile_check.json`, `dbt/logs/validate_normal.log`, `dbt/target/x.json`, `segments/seg_01/proc.sql` → deny.
  - "the reviewer in dbt scope writes only dbt/review.json"; "the validator in dbt scope writes every segment's validation reports" (`segments/seg_02/validation.normal.json` allow, `dbt/models/x.sql` deny).
  - "without dbt scope and without a segment the translator may write nothing" (unchanged behaviour).
  - "the validator may run validate_dbt.py; the translator may run compile_check.py --target dbt" — `.venv/Scripts/python.exe scripts/validate_dbt.py wf_0007` allow for validator, deny for reviewer; `… scripts/compile_check.py wf_0007 --target dbt` allow for translator/fixer.
  - "no role may run dbt, however it is spelled" — for every role: `dbt run --project-dir workflows/wf_0007/dbt`, `.venv/Scripts/dbt.exe parse`, `.venv\Scripts\dbt run`, `python -m dbt.cli.main run` → deny, and the reason of the first three contains `dbt is run only by scripts/compile_check.py and scripts/validate_dbt.py`.

`orchestrator/test/runner.test.ts` (real `MockRunner`, a temp samples tree):
  - "MockRunner replays canned/dbt/** for the translator in dbt scope" — every canned file lands under `workflows/<wf>/dbt/` at the same relative path.
  - "the first broken dbt variant is served on the fix-loop:dbt scenario" — translator iteration 0: `dbt/models/orders_out.sql === BROKEN_DBT`; fixer iteration 1: the canned model again and `dbt/fix_log.md` appended.
  - "the reviewer in dbt scope replays canned/review.json to dbt/review.json".
  - "the validator in dbt scope runs scripts/validate_dbt.py <wf>" — an `env.py` stub records the call; exit 2 → `{ok: false, error: "error"}`.
  - "CopilotRunner passes the dbt scope to the policy" — the fake client's session calls `onPreToolUse` with a `create` of `workflows/wf_0001/dbt/models/x.sql`: with `ctx {dbt: true}` the result is ok with no denial; with `ctx {}` it is `denied`.

Run node tests: FAIL (`dbtReadme` not exported, fakes unknown scripts, …).

- [ ] **Step 2: Policy and hooks.** `policy.ts`:

```ts
export interface PolicyOptions {
  sandboxDatabases?: string[];
  /** The role acts on the workflow's dbt project (output_kind dbt), not on one segment. */
  dbtProject?: boolean;
}

// in writeLanes(role, id, segment, dbtProject = false), before the switch:
if (dbtProject) {
  switch (role) {
    case "translator":
    case "fixer":
      return [
        new RegExp(`${wf}/dbt/(dbt_project\\.yml|profiles\\.yml|README\\.md|translation_notes\\.md|fix_log\\.md)$`),
        new RegExp(`${wf}/dbt/models/${UNDER}$`),
      ];
    case "reviewer":
      return [new RegExp(`${wf}/dbt/review\\.json$`)];
    case "validator":
      return [new RegExp(`${wf}/segments/${COMPONENT}/validation[a-z0-9._-]*\\.json$`)];
    default:
      break; // every other role keeps its own lanes
  }
}

/** dbt is run only by scripts/compile_check.py and scripts/validate_dbt.py, never by an agent. */
export const DBT_EXECUTABLE = /^(?:.*\/)?dbt(?:\.exe)?$/;
// in decideShell, right after the listing/git checks and before the PYTHON_EXES check:
if (DBT_EXECUTABLE.test(head[0])) {
  return denied("dbt is run only by scripts/compile_check.py and scripts/validate_dbt.py, never by an agent");
}
```

`ROLE_SCRIPTS.validator` gains `"scripts/validate_dbt.py"`; `decide`/`decideWrite` thread `options?.dbtProject`. `hooks.ts`: `hooksFor(role, wf, env, segment?, dbt?)` passes `{ sandboxDatabases, dbtProject: dbt }` and records `dbt` in each audit line when true.

- [ ] **Step 3: MockRunner and CopilotRunner.** `runner.ts`: `run(role, wf, task, ctx?: AgentCtx)`; `replay` routes `ctx.dbt` first:

```ts
if (ctx.dbt) {
  switch (role) {
    case "translator":
    case "fixer":
      return await this.replayDbt(role, wf, ctx.iteration ?? 0);
    case "reviewer":
      return (await copyInto(this.canned(wf, "review.json"), wfDir(this.root, wf.id, "dbt", "review.json")))
        ? OK
        : missing("review.json");
    case "validator":
      return await this.runValidator(wf, "scripts/validate_dbt.py", [wf.id]);
    default:
      break;
  }
}

private async replayDbt(role: Role, wf: Manifest, iteration: number): Promise<Outcome> {
  const from = this.canned(wf, "dbt");
  const files = await filesUnder(from);
  if (files.length === 0) return missing("dbt/**");
  const into = (rel: string) => wfDir(this.root, wf.id, "dbt", ...rel.split("/"));
  for (const file of files) await copyInto(file, into(path.relative(from, file).split(path.sep).join("/")));
  const fixLoop = scenarioFor(this.scenario, "fix-loop");
  const neverFixed = scenarioFor(this.scenario, "never-fixed");
  const serveBroken =
    (role === "translator" && iteration === 0 && (fixLoop.segment === "dbt" || neverFixed.segment === "dbt")) ||
    (role === "fixer" && neverFixed.segment === "dbt");
  if (serveBroken) {
    const dir = path.join(this.samplesDir, wf.id, "broken_sql", "dbt");
    const broken = (await filesUnder(dir))[0];               // first in name order (design §6)
    if (!broken) return missing("broken_sql/dbt/**");
    await copyInto(broken, into(path.relative(dir, broken).split(path.sep).join("/")));
  }
  if (role === "fixer") {
    const fixLog = into("fix_log.md");
    await mkdir(path.dirname(fixLog), { recursive: true });
    await appendFile(fixLog, `## iteration ${iteration} — dbt project\n- fix: replayed from samples/${wf.id}/canned/dbt\n- status: ${serveBroken ? "UNFIXED" : "FIXED"}\n\n`, "utf8");
  }
  return OK;
}
```

`replayValidator`'s spawn-and-classify body is factored into `runValidator(wf, script, args)` and reused. `CopilotRunner.run` calls `hooksFor(role, wf, env, ctx?.segment, ctx?.dbt)`.

- [ ] **Step 4: The translate branch** (`stages.ts`). `runAgent`'s `ctx` parameter becomes `AgentCtx`. At the top of `stageTranslate`, after the no-segments check: `if (m.output_kind === "dbt") return await translateDbt(env, m, order);`.

```ts
/** design §4.3 / §6: a dbt workflow deploys with one command; this file replaces master.sql. */
export function dbtReadme(wfId: string): string {
  return [
    `# ${wfId} — deploy as a dbt project`,
    ``,
    `Generated by orchestrate.ts. This workflow's output kind is \`dbt\`: there is no master.sql and no per-segment`,
    `procedure. Nothing in this repository has run it against Snowflake.`,
    ``,
    "```",
    `dbt run --project-dir workflows/${wfId}/dbt --profiles-dir workflows/${wfId}/dbt --target snowflake --vars '{"src_schema": "<SRC>", "tgt_schema": "<TGT>"}'`,
    "```",
    ``,
    `<SRC> is the schema that holds the mapped source tables under their logical names; <TGT> is where the models`,
    `are written. Every connection value comes from the SNOWFLAKE_* variables named in workflows/${wfId}/dbt/profiles.yml.`,
    `dbt-snowflake must be installed first; it is not part of requirements.txt.`,
    ``,
  ].join("\n");
}

export function dbtModelName(output: { kind?: string; logical?: string | null; table?: string | null }): string {
  return output.kind === "target" ? String(output.logical).toLowerCase() : String(output.table).split(".").pop()!.toLowerCase();
}

async function failingModels(env: Env, m: Manifest, segments: string[]): Promise<string> {
  const names: string[] = [];
  for (const segment of segments) {
    const contract = await readJsonOr<{ outputs?: { kind?: string; logical?: string; table?: string }[] }>(
      wfDir(env.root, m.id, "segments", segment, "contract.json"), {});
    for (const output of contract.outputs ?? []) names.push(`${dbtModelName(output)} (${segment})`);
  }
  return names.join(", ");
}

interface DbtOutcome { verdicts: Record<string, string>; reason?: string }

async function migrateDbt(env: Env, m: Manifest, segments: string[]): Promise<DbtOutcome> {
  const iterations = env.config.maxFixIterations;
  const dbtFile = (...rest: string[]) => wfDir(env.root, m.id, "dbt", ...rest);
  let lastReason = `validation FAIL after ${iterations} iterations`;
  let failedBeforeReview: string | undefined;
  let failing = "";
  let verdicts: Record<string, string> = {};
  for (let iteration = 0; iteration < iterations; iteration++) {
    const role: Role = iteration === 0 ? "translator" : "fixer";
    const task =
      iteration === 0
        ? `Translate ${m.id} into ONE dbt project under workflows/${m.id}/dbt/ (its output kind is dbt): read every ` +
          `segment's contract.json and dag.json and follow docs/reference/output-targets.md §3.3 and cookbook/dbt.md.`
        : `Repair the dbt project of ${m.id}: read workflows/${m.id}/dbt/review.json and every segment's ` +
          `validation.json first and change only what their diagnosis points at.` +
          (failing ? ` Failing models: ${failing}.` : "") + (failedBeforeReview ? ` ${failedBeforeReview}` : "");
    const written = await runAgent(env, m, role, "translate", task, { iteration, dbt: true },
      () => fileExists(dbtFile("dbt_project.yml")));
    if (!written.ok) return { verdicts, reason: agentFailureReason(role, written) };

    const compiled = await env.py("scripts/compile_check.py", [m.id, "--target", "dbt"]);
    if (compiled.code === 2) {
      env.log(`${m.id}: compile_check.py --target dbt exited 2 — ${compiled.err.trim()}`);
      return { verdicts, reason: "script-error" };
    }
    if (!compiled.ok) {
      lastReason = `compile check failed after ${iterations} iterations`;
      failedBeforeReview = `The previous attempt failed before review: scripts/compile_check.py --target dbt failed; ` +
        `read dbt/compile_check.json. It said: ${diagnosis(compiled)}`;
      continue;
    }
    failedBeforeReview = undefined;

    const reviewed = await runAgent(env, m, "reviewer", "translate",
      `Review the dbt project of ${m.id} against every segment's contract and write workflows/${m.id}/dbt/review.json.`,
      { iteration, dbt: true }, () => fileExists(dbtFile("review.json")));
    if (!reviewed.ok) return { verdicts, reason: agentFailureReason("reviewer", reviewed) };
    if ((await readJsonOr<{ verdict?: string }>(dbtFile("review.json"), {})).verdict === "BLOCK") {
      lastReason = `reviewer BLOCK after ${iterations} iterations`;
      continue;
    }

    const validated = await runAgent(env, m, "validator", "translate",
      `Validate the dbt project of ${m.id} against every golden set with scripts/validate_dbt.py; it writes every ` +
        `segment's validation.json.`,
      { iteration, dbt: true },
      async () => {
        for (const s of segments) if (!(await fileExists(wfDir(env.root, m.id, "segments", s, "validation.json")))) return false;
        return true;
      });
    if (!validated.ok) return { verdicts, reason: agentFailureReason("validator", validated) };
    const reports = await Promise.all(segments.map((s) =>
      readJsonOr<{ verdict?: string; needs_human?: boolean }>(wfDir(env.root, m.id, "segments", s, "validation.json"), {})));
    verdicts = Object.fromEntries(segments.map((s, i) => [s, String(reports[i].verdict ?? "")]));
    if (reports.some((r) => r.needs_human)) return { verdicts, reason: "needs_human" };
    if (segments.every((s) => verdicts[s].startsWith("PASS"))) return { verdicts };
    failing = await failingModels(env, m, segments.filter((s) => !verdicts[s].startsWith("PASS")));
    lastReason = `validation FAIL after ${iterations} iterations`;
  }
  return { verdicts, reason: lastReason };
}

async function translateDbt(env: Env, m: Manifest, order: string[][]): Promise<Step> {
  const segments = order.flat();
  m.segment_status ??= {};
  const stuck = segments.filter((s) => m.segment_status![s] === "NEEDS_HUMAN");
  if (stuck.length > 0) {
    m.status.translate = "NEEDS_HUMAN";
    reasons(m).translate = "dbt: needs_human (recorded)";
    env.log(`${m.id}: the dbt project has a recorded NEEDS_HUMAN segment — stopping the workflow`);
    return "stop";
  }
  if (!segments.every((s) => String(m.segment_status![s] ?? "").startsWith("PASS"))) {
    const outcome = await migrateDbt(env, m, segments);
    for (const s of segments) {
      const verdict = outcome.verdicts[s] ?? "";
      m.segment_status[s] = outcome.reason && !verdict.startsWith("PASS") ? "NEEDS_HUMAN" : verdict;
    }
    if (outcome.reason) {
      m.status.translate = "NEEDS_HUMAN";
      reasons(m).translate = `dbt: ${outcome.reason}`;
      await saveManifest(env.root, m);
      env.log(`${m.id}: the dbt project needs a human (${outcome.reason}) — stopping the workflow`);
      return "stop";
    }
    await saveManifest(env.root, m);
  }
  const procs = wfDir(env.root, m.id, "procs");
  await mkdir(procs, { recursive: true });
  await rm(path.join(procs, "master.sql"), { force: true });   // a dbt workflow has none (design §4.3)
  await writeFile(path.join(procs, "README.md"), dbtReadme(m.id), "utf8");
  m.status.translate = "VALIDATED";
  return "continue";
}
```

(`rm` joins the `node:fs/promises` import.) `stageDocument`'s task becomes kind-aware: dbt → `… from the parsed dag, the contracts, workflows/<id>/dbt/translation_notes.md and the validation reports. Its Deployment section is the dbt run command in workflows/<id>/procs/README.md. Restate only what those artifacts say.`; procedures → the existing text plus ` Its Deployment section names procs/master.sql and every segments/<seg>/proc.sql.`

- [ ] **Step 5: Agents and docs.** Add, each marked `<!-- amended: output targets phase 2 -->`:
  - `translator.agent.md` — a `## dbt projects (`manifest.json`'s `"output_kind": "dbt"`)` section: called ONCE per workflow with no segment in context; the layout (`dbt_project.yml` keys; `profiles.yml` = the template in `docs/reference/output-targets.md` §3.3, byte for byte, never a credential; `models/sources.yml`; one `materialized='table'` model per work stream named the MIG_WORK table lower-cased; one model per final target named the logical lower-cased with `alias='<LOGICAL>'` in upper case and the config its write mode needs — overwrite `materialized='table'`, append `incremental`+`append`, merge `incremental`+`merge`+`unique_key=[…]`; no `is_incremental()` filter; PreSQL/PostSQL as `pre_hook`/`post_hook` against `{{ this }}`; `models/schema.yml` columns in contract order with `not_null`/`unique`; one CTE per tool with `-- tool <id>:`; sources only via `{{ source('src', …) }}`, streams only via `{{ ref(…) }}`; `README.md`, `translation_notes.md`), cookbook pointers to `cookbook/snowpark.md` and `cookbook/dbt.md`, "Never run `dbt` yourself", and the done criterion `.venv/Scripts/python.exe scripts/compile_check.py <id> --target dbt` exits 0.
  - `reviewer.agent.md` — `## Blocking checks for a dbt project`: one model per contract output, config ↔ write mode (merge keys = the mapping's keys), the upper-case alias, `profiles.yml` is the template, schema.yml columns = contract columns in order, sources only via `source()`, every data node has a `-- tool <id>:` comment; writes `workflows/<id>/dbt/review.json`.
  - `validator.agent.md` — output kind `dbt` → `.venv/Scripts/python.exe scripts/validate_dbt.py <id>` once for the whole workflow; it writes every segment's `validation.json` with `"target": "dbt"`; exit codes as the others; the dbt-duckdb caveats of design §9.
  - `fixer.agent.md` — `## dbt projects`: repair the models the task names, smallest change, never `profiles.yml`, never run dbt, log to `dbt/fix_log.md`.
  - `documenter.agent.md` — a `## Deployment` section per output kind (procedures: `procs/master.sql` + each `segments/<seg>/proc.sql`, a Snowpark segment's `proc.sql` is its `LANGUAGE PYTHON` wrapper, `RUNTIME_VERSION`/`PACKAGES` to verify; dbt: the command in `procs/README.md`, the `SNOWFLAKE_*` variables, `dbt-snowflake` not installed here). Keep the sentence `test_unamended_agents_keep_their_spec_bodies` pins.
  - `.github/copilot-instructions.md` — replace "`dbt` is a workflow-level output kind that phase 1 records but does not yet build" with a sentence saying a dbt workflow is one project under `workflows/<id>/dbt/`, checked by `compile_check.py <id> --target dbt`, validated by `validate_dbt.py`, and that agents never run dbt.
  - `tests/test_agents_config.py` — `test_translator_carries_the_dbt_rules` (asserts `alias='<LOGICAL>'`, `Never run \`dbt\` yourself`, `compile_check.py <id> --target dbt`, `one model per final target`), `test_reviewer_blocks_on_the_dbt_project_shape`, `test_validator_picks_validate_dbt_for_a_dbt_workflow`, `test_fixer_repairs_dbt_models_and_never_the_profile`, `test_documenter_requires_a_deployment_section_per_output_kind`, `test_copilot_instructions_describe_dbt_as_built` (asserts `scripts/validate_dbt.py` present and `does not yet build` absent), `test_phase_2_amendments_are_marked` (each of the five agent files contains `<!-- amended: output targets phase 2 -->`).
  - `docs/reference/output-targets.md` — §3.3 rewritten as "`output_kind: "dbt"` — a dbt project": the layout table (design §4.3 with DV2/DV4/DV5), `PROFILES_TEMPLATE` verbatim in a code block, naming and alias rule with the S3 error text, the sandbox and `--vars` rule with the S2 facts (DV1, DV3), the nine `compile_check --target dbt` checks, what `validate_dbt.py` does (DV7) and where logs/sandboxes live; §4 gains a dbt column (agent writes `dbt/**`; no render; `compile_check.py <wf> --target dbt`; reviewer per workflow; `validate_dbt.py`; ONE iteration loop per workflow; reasons `dbt: <reason>`); §5 gains "a dbt workflow" deployment steps; §6 replaces "`dbt-duckdb` … not exercised" with spec §9's dbt bullet plus: tests declared in `schema.yml` are not executed by `validate_dbt.py`; the profile's `snowflake` output never ran; a MERGE's semantics are DuckDB's `UPDATE BY NAME`/`INSERT BY NAME`; §7 table rows for `scripts/lib/dbt_project.py`, `scripts/validate_dbt.py`, `workflows/<wf>/dbt/`, `workflows/<wf>/procs/README.md`.
  - `README.md` "Three output targets" — the dbt row becomes: built; the committed worked example is `workflows/wf_0007/` (§6); `workflows/<wf>/dbt/**`; `compile_check.py <wf> --target dbt`; `validate_dbt.py` (dbt-duckdb); the "in phase 1 that is as far as it goes" paragraph replaced by one saying what a dbt workflow produces and that `dbt-snowflake` is not installed.

- [ ] **Step 6: Green** — node suite, tsc, `tests/test_agents_config.py`, full pytest. The existing test "a SQL workflow's script calls are exactly what they were, plus the one target_check.py call" must stay green unchanged.

- [ ] **Step 7: Commit**

```bash
git add orchestrator .github tests/test_agents_config.py docs/reference/output-targets.md README.md
git commit -m "feat: output_kind dbt — one translate iteration for the whole workflow, dbt lanes and denies, mock replay of canned/dbt, agents and reference docs"
```

---

### Task E: `cookbook/snowpark.md` and `cookbook/dbt.md` with executable examples

**Tier:** standard · **Depends on:** A · **Wave 4, parallel with F**

**Files:**
- Create: `cookbook/snowpark.md`, `cookbook/dbt.md`, `tests/cookbook_examples/snowpark/{filter,formula,summarize,sort,python_carry_over}/{case.json, example.py, input.csv, input.schema.json}`, `tests/cookbook_examples/dbt/merge/{case.json, input.csv, input.schema.json, history_before.csv, history_before.schema.json, project/dbt_project.yml, project/profiles.yml, project/models/sources.yml, project/models/history.sql}`, `tests/test_cookbook_snowpark.py`, `tests/test_cookbook_dbt.py`
- Modify: `tests/test_cookbook_examples.py` (`test_every_cookbook_tool_has_a_page` excludes the target pages), `cookbook/index.md` (a "Target pages" section)

**Interfaces:**
- Consumes: `tests/test_cookbook_examples._dag`, `_expected_table`, `_contract`, `TOLERANCES`; `dev.alteryx_sim.simulate`; `validate_snowpark._session`, `_save`, `_read_back`; `dbt_project.run_dbt`, `local_vars`; `lib.backend.DuckDBBackend`.
- Produces: `TARGET_PAGES = ("snowpark", "dbt")` in `tests/test_cookbook_examples.py`.

- [ ] **Step 1: Failing tests.** `tests/test_cookbook_examples.py`: `TARGET_PAGES = ("snowpark", "dbt")` and `pages = {…} - set(TARGET_PAGES)`. `tests/test_cookbook_snowpark.py`:

```python
"""cookbook/snowpark.md's DataFrame idioms, each checked against the simulator (spec §8) like the
SQL patterns: the oracle is scripts/dev/alteryx_sim.py, the actual is the example run in a local
Snowpark session, the judge is compare.py on DuckDB."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

import compare as cmp
import validate_snowpark as vsp
from dev.alteryx_sim import simulate
from lib.backend import DuckDBBackend
from lib.io import read_json
from lib.typed_csv import read_table
from tests.test_cookbook_examples import TOLERANCES, _contract, _dag, _expected_table

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "tests" / "cookbook_examples" / "snowpark"
TOOLS = ("filter", "formula", "summarize", "sort", "python_carry_over")
_PY_FENCE = re.compile(r"```python\n(.*?)```", re.DOTALL)


def _module(path: Path):
    spec = importlib.util.spec_from_file_location(f"cookbook_{path.parent.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_listed_tool_has_an_example_and_a_section():
    page = (ROOT / "cookbook" / "snowpark.md").read_text(encoding="utf-8")
    assert sorted(p.parent.name for p in EXAMPLES.glob("*/case.json")) == sorted(TOOLS)
    blocks = [b.rstrip("\n") for b in _PY_FENCE.findall(page)]
    for tool in TOOLS:
        assert (EXAMPLES / tool / "example.py").read_text(encoding="utf-8").rstrip("\n") in blocks, tool


@pytest.mark.parametrize("tool", TOOLS)
def test_snowpark_example_matches_the_simulator(tool):
    directory = EXAMPLES / tool
    case = read_json(directory / "case.json")
    seed = {tool_id: read_table(directory / name) for tool_id, name in case["inputs"].items()}
    sim = simulate(_dag(case), seed)
    module = _module(directory / "example.py")
    session = vsp._session()
    backend = DuckDBBackend()
    try:
        for tool_id, table in seed.items():
            vsp._save(session, f"MIG_COOKBOOK.IN_{tool_id}", table)
        for index, entry in enumerate(case["compare"]):
            expected = _expected_table(sim, case["node"], entry["stream"])
            assert expected["rows"], f"{tool}/{entry['stream']}: the oracle produced no rows"
            getattr(module, entry["function"])(session).write.mode("overwrite").save_as_table(f"MIG_COOKBOOK.ACTUAL_{index}")
            actual = vsp._read_back(session, f"MIG_COOKBOOK.ACTUAL_{index}")
            backend.load_table(f"MIG_COMPARE.EXPECTED_{index}", expected)
            backend.load_table(f"MIG_COMPARE.ACTUAL_{index}", actual)
            report = cmp.compare(backend, f"MIG_COMPARE.EXPECTED_{index}", f"MIG_COMPARE.ACTUAL_{index}",
                                 _contract(expected, entry.get("keys") or []), TOLERANCES)
            assert report["verdict"] == "PASS", json.dumps(report, indent=2, default=str)
    finally:
        backend.close()
        session.close()
```

`tests/test_cookbook_dbt.py`:
- `test_the_dbt_page_shows_the_executable_merge_model` — `cookbook/dbt.md`'s one ```sql block equals `project/models/history.sql`; the page has the headings `## Materialisations`, `## Hooks`, `## Sources and refs`, `## Naming`, `## Tests`, `## What dbt-duckdb does not prove`.
- `test_the_merge_example_matches_the_simulator` — `simulate(_dag(case), seed, targets_before={"HISTORY": …}, logical_by_tool={"2": "HISTORY"})`; a sandbox `tmp_path / "dbt_sandbox_cookbook.duckdb"` through `DuckDBBackend`: input loaded as `MIGDB.MIG_COOKBOOK.IN_1`, `history_before.csv` as `MIGDB.MIG_WORK.HISTORY`; `run_dbt("run", project, vars=local_vars("MIG_COOKBOOK"), duckdb_path=sandbox)` exit 0; `compare(backend, expected, "MIGDB.MIG_WORK.HISTORY", contract keyed on ID)` PASS.
- `test_running_the_example_writes_nothing_into_the_committed_project` — the file list of `project/` is unchanged after the run.
- `test_the_example_inputs_carry_a_null_and_a_duplicate_row` — as the SQL harness asserts.

Run: FAIL (`cookbook/snowpark.md` missing, `test_every_cookbook_tool_has_a_page` still passes only after the exclusion).

- [ ] **Step 2: The examples.** Each `case.json` uses the SQL cases' schema (`inputs`, `node`, `edges`, `compare`) with `compare[]` entries `{"stream": …, "py": "example.py", "function": …, "keys": […]}`. Reuse the SQL example's input CSV and node for filter/formula/summarize/sort (copy the files; the page says so). `python_carry_over`'s node is `{"tool_id": "2", "type": "python", "config": {"script": <the script in samples/wf_0006/source/subscription_revenue.yxmd>}}` over a SUBSCRIPTIONS-shaped input with a NULL and a duplicate row. Every `example.py` defines plain functions `(session) -> DataFrame` reading `session.table("MIG_COOKBOOK.IN_1")`, commented with `# tool <id>: …`, using the DataFrame API only (no `session.sql`) and, for the carry-over, `to_pandas()` + `session.create_dataframe(pdf, schema=StructType([...]))` exactly as `samples/wf_0006/canned/segments/seg_02/proc.py` does, with the `StructType` in the output column order. Filter has `true_branch` / `false_branch` (a NULL condition goes to False, via `~(cond) | cond.is_null()`); summarize `transform`; sort `transform` (`.sort(col(...).asc_nulls_first())` — the page states that a Sort's order survives only through an explicit window or `ORDER BY` downstream, and that the harness compares row multisets).

`cookbook/snowpark.md`: opening paragraph (design §4.2's rules are the contract; these are fragments that go inside `run`), a section per tool (`## Filter`, `## Formula`, `## Summarize`, `## Sort`, `## Python tool with carry-over (pandas)`) with "What the SQL page does", the ```python block (= `example.py`), parity notes, and a closing "What the local double does not prove" (spec §9, Snowpark bullet).

`cookbook/dbt.md`: `## Materialisations` (table / incremental append / incremental merge with `unique_key`, the alias rule and the S3 error), `## Hooks` (`pre_hook`/`post_hook` against `{{ this }}`), `## Sources and refs`, `## Naming` (model names, DV1/DV3 sandbox rules), `## Tests` (`not_null`/`unique` from the contract; not executed by `validate_dbt.py`), `## Executable example: a merge model` (the ```sql block = `history.sql`), `## What dbt-duckdb does not prove` (spec §9 dbt bullet). The example project's `profiles.yml` is `PROFILES_TEMPLATE`.

`cookbook/index.md`: a `## Target pages` section linking both pages.

- [ ] **Step 3: Green** — `tests/test_cookbook_examples.py tests/test_cookbook_snowpark.py tests/test_cookbook_dbt.py`; full suite.

- [ ] **Step 4: Commit**

```bash
git add cookbook tests/cookbook_examples/snowpark tests/cookbook_examples/dbt tests/test_cookbook_snowpark.py tests/test_cookbook_dbt.py tests/test_cookbook_examples.py
git commit -m "docs: cookbook pages for the Snowpark and dbt targets, every example checked against the simulator"
```

---

### Task F: `prompt_context.py` and its orchestrator use; the three phase-1 leftovers

**Tier:** standard · **Depends on:** D · **Wave 4, parallel with E**

**Files:**
- Create: `scripts/prompt_context.py`, `tests/test_prompt_context.py`, `tests/test_intake_output_target.py`
- Modify: `orchestrator/stages.ts` (`stageIntake` :194–219, `stageAnalyze` :350–367; new `PROMPT_CONTEXT_CHARS`, `inlineContext`), `orchestrator/test/fakes.ts` (fake `prompt_context.py`), `orchestrator/test/stages.test.ts`
- Modify: `scripts/intake_prompt.py` (`run` :933, new `ask_output_target`), `scripts/lib/snowpark_rules.py` (new `NUMPY_WRITERS`), `tests/test_snowpark_rules.py`, `tests/test_agents_config.py` (`_refused_names` includes `rules.NUMPY_WRITERS`), spec §4.2 sink bullet, `.github/agents/translator.agent.md` (re-copied bullet), `docs/reference/output-targets.md` (§3.2 list, §3 intake paragraph)

**Interfaces:**
- Produces: `prompt_context.DEFAULT_BUDGET_CHARS = 16000`, `ROLES = ("intake", "analyzer")`, `TRUNCATION_MARKER`, `dag_summary_lines(dag) -> list[str]`, `touchpoint_lines(touchpoints) -> list[str]`, `targets_lines(targets) -> list[str]`, `render(repo, wf_id, role, budget_chars=DEFAULT_BUDGET_CHARS) -> str`; CLI `prompt_context.py <wf> --role intake|analyzer [--budget-chars N] [--root .]` (prints; writes nothing; exit 2 when `parsed/dag.json` is missing); TS `PROMPT_CONTEXT_CHARS = 16000`; `intake_prompt.ask_output_target(ask, out) -> str | None`; `snowpark_rules.NUMPY_WRITERS`.

- [ ] **Step 1: Failing tests.**

`tests/test_prompt_context.py` (fixture: `prepare_workflow(tmp_path, "wf_0006")` then `segment.py` and `target_check.target_check(repo, "wf_0006", "auto")` so `targets.json` exists):
- `test_intake_context_lists_every_touchpoint_then_the_dag` — output starts with `## Inline context for intake`; contains `Q1 input yxdb billing/subscriptions.yxdb (tool 1)`; contains `tool 3 python`; the touchpoints section precedes the DAG section; `targets.json` is not mentioned.
- `test_analyzer_context_leads_with_the_targets` — contains `seg_02: snowpark` and `output_kind procedures`; the targets section comes first.
- `test_the_dag_summary_names_in_and_out_columns` — the line for tool 2 is exactly `- tool 2 filter "Only billed periods": in Input <- 1.Output; out T[CUSTOMER, PERIOD, BILLED, CAP, CANCELLED] F[CUSTOMER, PERIOD, BILLED, CAP, CANCELLED]` (derive the exact line from the committed `workflows/wf_0006/parsed/dag.json` and pin it).
- `test_a_small_budget_truncates_deterministically` — `render(…, budget_chars=400)` has `len <= 400`, ends with `TRUNCATION_MARKER`'s first words, and two calls are equal.
- `test_the_output_carries_no_absolute_path` — `str(tmp_path)` and `:\\` absent.
- `test_cli` — exit 0 and prints; unknown workflow exit 2; `--role translator` exit 2 (argparse).

`tests/test_intake_output_target.py` (fixture as `tests/test_intake_prompt.py`'s `repo`, then `program.output_target` REMOVED from the tmp `mappings/global.yaml` through `io.write_global_mappings`):
- `test_the_question_is_asked_first_when_global_yaml_has_no_value` — scripted answers `["dbt", "n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"]`; the first prompt contains `Output target`; `io.load_manifest(repo, "wf_0001")["output_target"] == "dbt"`.
- `test_enter_takes_procedures` — `""` first → `"procedures"`.
- `test_an_invalid_answer_is_asked_again_once` — `"sql", "dbt"` → `dbt`; `"x", "y"` → the key stays absent and the output says so.
- `test_never_asked_when_global_yaml_has_a_value` — pristine global.yaml: no prompt contains `Output target`.
- `test_never_asked_when_the_manifest_already_has_one` and `test_never_asked_non_interactively`.
- `test_a_closed_stdin_is_no_answer` — `ask` raises `EOFError` → key absent, run continues to the touchpoints (which also see EOF).

`tests/test_snowpark_rules.py`:

```python
@pytest.mark.parametrize("line", [
    "pdf.to_numpy().tofile('x.bin')", "np.savetxt('x.txt', pdf.to_numpy())", "np.savez('x', a=1)",
    "np.savez_compressed('x', a=1)", "pdf.values.dump('x.pkl')", "savetxt('x', 1)",
])
def test_numpy_file_writers_are_refused(line):
    source = GOOD.replace("    return \"OK\"", f"    {line}\n    return \"OK\"").replace(
        "import pandas as pd", "import pandas as pd\nimport numpy as np")
    errors = rules.check_proc_py(source, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:no_io") for e in errors), errors


def test_to_numpy_itself_stays_legal():
    source = GOOD.replace("    return \"OK\"", "    arr = pdf.to_numpy()\n    return \"OK\"")
    assert rules.check_proc_py(source, "wf_0006", "seg_02", CONTRACT) == []
```

`tests/test_agents_config.py::_refused_names` gains `| rules.NUMPY_WRITERS` (RED: the three documentation pins then fail until the docs name them).

Node (`stages.test.ts`; the fake `prompt_context.py` returns `result(0, "## Inline context for <role> (fake)\n- tool 1 input")`, or exit 2 on scenario `context-fails`):
- "intake and the analyzer get their inputs inline" — `py:scripts/prompt_context.py` with `["wf_0001", "--role", "intake", "--budget-chars", "16000"]` runs before `agent:intake`, and the intake task ends with the fake output; the same for the analyzer with `--role analyzer`, after `target_check.py`.
- "a prompt_context failure is logged and the agent still runs without the block" — `context-fails`: both agents ran, their tasks do not contain `Inline context`, `calls.logs` has one line per role naming `prompt_context.py`.
- the existing "a SQL workflow's script calls are exactly what they were, plus the one target_check.py call" is updated to also expect the two `prompt_context.py` calls (rename it "… plus target_check.py and the two prompt_context.py calls").

Run: FAIL (`ModuleNotFoundError: prompt_context`; `AttributeError: … NUMPY_WRITERS`; `ask_output_target` missing; node assertions).

- [ ] **Step 2: `scripts/prompt_context.py`**

```python
"""Render a compact context block for the intake or analyzer task prompt (design §8).

    prompt_context.py <wf_id> --role intake|analyzer [--budget-chars N] [--root .]

Prints Markdown and writes nothing. The files it summarises stay the source of truth; this only
saves a model from spending its context window finding them tool call by tool call (the live
tests in docs/live-smoke-test.md overflowed exactly there). Truncation is deterministic.
"""
DEFAULT_BUDGET_CHARS = 16000
ROLES = ("intake", "analyzer")
TRUNCATION_MARKER = "…truncated: {shown} of {total} characters shown; the files named above are complete."


def _names(fields: list[dict] | None) -> str:
    return ", ".join(str(f["name"]) for f in fields or [])


def dag_summary_lines(dag: dict) -> list[str]:
    nodes = {n["tool_id"]: n for n in dag.get("nodes") or []}
    lines = []
    for node in sorted(nodes.values(), key=lambda n: _tool_key(n["tool_id"])):
        if node.get("type") in DATA_LESS_TYPES:
            continue
        inbound = [f"{e['dst_anchor']} <- {e['src']}.{e['src_anchor']}" for e in dag.get("edges") or []
                   if e["dst"] == node["tool_id"]]
        outs = [f"{anchor}[{_names(fields)}]" for anchor, fields in (node.get("meta") or {}).items()]
        label = f' "{node["annotation"][:60]}"' if node.get("annotation") else ""
        macro = " (macro; its sub-DAG is in parsed/dag.json)" if node.get("type") == "macro" else ""
        lines.append(f"- tool {node['tool_id']} {node['type']}{label}{macro}: in {', '.join(inbound) or 'none'}; "
                     f"out {' '.join(outs) or 'none'}")
    return lines
```

`touchpoint_lines`: one line per touchpoint — `- <id> <kind> <format> <key> (tool <tool_id>)`, then `fields [..]`, for outputs `mode <write_mode> keys [..]`, `blocking yes|no`, `resolved <value or no>`, `top candidate <snowflake> (<basis>)` when any. `targets_lines`: `output_kind <k> (preference <p>): <reason>`, `- <seg>: <target>` per segment, `- blocker <segment> <kind>` per blocker, `- node <id>: <class>` per non-sql node. `render(repo, wf_id, role, budget_chars)`: header `## Inline context for <role> (scripts/prompt_context.py — the files it names are the source of truth)`; sections `### Touchpoints (workflows/<wf>/intake/touchpoints.json)`, `### DAG summary (workflows/<wf>/parsed/dag.json)`, `### Target proposal (workflows/<wf>/segments/targets.json)` — intake: touchpoints, DAG; analyzer: targets, DAG, touchpoints; a missing optional file renders one line `(absent)`; lines are appended while `len(text) + len(line) + 1 <= budget_chars - len(<formatted marker>)`, and the marker is appended once something did not fit. `main` prints `render(...)` and returns 0; `FileNotFoundError` for `parsed/dag.json` → stderr + 2.

- [ ] **Step 3: Orchestrator wiring** (`stages.ts`):

```ts
/** Characters of inline context the intake and analyzer tasks carry (≈ 4 000 tokens). */
export const PROMPT_CONTEXT_CHARS = 16000;

async function inlineContext(env: Env, m: Manifest, role: "intake" | "analyzer"): Promise<string> {
  const rendered = await env.py("scripts/prompt_context.py",
    [m.id, "--role", role, "--budget-chars", String(PROMPT_CONTEXT_CHARS)]);
  if (!rendered.ok) {
    env.log(`${m.id}: prompt_context.py --role ${role} exited ${rendered.code}; the ${role} task goes without inline context`);
    return "";
  }
  const text = rendered.out.trim();
  return text ? `\n\n${text}` : "";
}
```

`stageIntake`: inside the `if (!plan.md exists)` block, `const context = await inlineContext(env, m, "intake");` and the task string gets `+ context`. `stageAnalyze`: after `target_check.py`, `const context = await inlineContext(env, m, "analyzer");` appended to the analyzer task.

- [ ] **Step 4: The intake question** (`intake_prompt.py`):

```python
OUTPUT_TARGETS = ("procedures", "dbt")


def ask_output_target(ask: Callable[[str], str], out: Callable[[str], None]) -> str | None:
    """Design §3.3: one program-level question, asked only when mappings/global.yaml has no
    program.output_target. Enter takes `procedures`; an answer outside the vocabulary is asked once
    more; a second bad answer or a closed stdin is no answer at all (target_check.py --prefer auto
    then falls back to procedures)."""
    for _ in range(2):
        try:
            text = (ask("Output target for this workflow? procedures | dbt [procedures]: ") or "").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if not text:
            return "procedures"
        if text in OUTPUT_TARGETS:
            return text
        out(f"  {text!r} is not one of: {', '.join(OUTPUT_TARGETS)}")
    out("  no output target recorded; target_check.py will use the program default")
    return None
```

In `run`, interactive branch, before `prompt_touchpoints`: `if not program.get("output_target") and not manifest.get("output_target"): choice = ask_output_target(ask, out); if choice: manifest["output_target"] = choice` (the manifest is saved at the end of `run` as today). `docs/reference/output-targets.md` §3's "There is no interactive question…" paragraph becomes a description of the question (when it is asked, what Enter means, where the answer goes).

- [ ] **Step 5: The deny list** — `snowpark_rules.py`: `NUMPY_WRITERS = frozenset({"savetxt", "savez", "savez_compressed", "tofile", "dump"})` with a comment (phase-1 residual, ledger: filesystem-write routes through allowed modules; accident guard, not a security boundary), refused as a `Name` and as an `Attribute` under `rule:no_io` with the pandas-writer message shape. Spec §4.2's sink bullet gains, after the pandas writers, "and the `numpy` file writers — `savetxt`, `savez`, `savez_compressed`, `tofile`, `dump`"; re-copy the bullet byte-identically into `translator.agent.md`; add the five names to `docs/reference/output-targets.md` §3.2's list.

- [ ] **Step 6: Green** — the three new/changed Python test files, `tests/test_agents_config.py`, `tests/test_intake_prompt.py`, node suite, tsc, full pytest.

- [ ] **Step 7: Commit**

```bash
git add scripts/prompt_context.py tests/test_prompt_context.py orchestrator/stages.ts orchestrator/test scripts/intake_prompt.py tests/test_intake_output_target.py scripts/lib/snowpark_rules.py tests/test_snowpark_rules.py tests/test_agents_config.py docs/superpowers/specs/2026-09-22-output-targets-design.md .github/agents/translator.agent.md docs/reference/output-targets.md
git commit -m "feat: inline prompt context for intake and the analyzer; the output_target intake question; numpy file writers refused"
```

---

### Task W1: Workflow-level chain validation

**Tier:** most capable · **Depends on:** B, D, F · **Wave 5, parallel with P1**

**Files:**
- Create: `scripts/validate_workflow.py`, `scripts/lib/handoff.py`, `tests/chain_fixtures.py`, `tests/test_handoff.py`, `tests/test_validate_workflow.py`, `tests/test_e2e_chain.py`
- Modify: `scripts/lib/types_map.py` (`duckdb_to_alteryx`), `scripts/lib/validation.py` (`chain_report`, `write_workflow_reports`, `clear_stale_workflow_reports`), `scripts/validate_snowpark.py` (`_save`/`_read_back` move to `lib/handoff.py` and are re-exported; `_session`, `_load_module` gain public aliases `new_local_session`, `load_module`), `scripts/validate_dbt.py` (writes `validation_workflow.json` from the same run), `orchestrator/stages.ts` (`chainCheck`, `migrateSegment` options), `orchestrator/test/{fakes,stages.test}.ts`, `docs/reference/large-workflows.md`

**Interfaces:**
- Produces (`lib/handoff.py`): `class HandoffError(ValueError)`, `table_from_backend(backend, fqn) -> dict`, `table_from_snowpark(session, fqn) -> dict | None`, `load_into_backend(backend, fqn, table) -> None`, `load_into_snowpark(session, fqn, table) -> None` — none takes a contract.
- Produces (`types_map`): `duckdb_to_alteryx(type_str: str) -> dict` (`{"type", "size", "scale"}`): `BIGINT|INTEGER|SMALLINT|TINYINT|HUGEINT|UBIGINT|UINTEGER|USMALLINT|UTINYINT` → `Int64`; `DECIMAL(p,s)` → `FixedDecimal(p,s)`; `DOUBLE|FLOAT|REAL` → `Double`; `VARCHAR` (any length) → `V_String`; `BOOLEAN` → `Bool`; `DATE` → `Date`; `TIMESTAMP*` → `DateTime`; `TIME` → `Time`; anything else → `ValueError`.
- Produces (`lib/validation.py`): `chain_report(wf_id, golden_set, entries, error=None) -> dict`; `write_workflow_reports(repo, wf_id, sets, reports, idempotent) -> dict`; `clear_stale_workflow_reports(repo, wf_id)`. Entries: `{"segment", "stream", "kind": "work"|"target", "output": logical-or-table, "relation", "report": <compare report>}` in chain order.
- Produces: `validate_workflow.validate_workflow(repo, wf_id, golden_sets=None) -> dict`; CLI `validate_workflow.py <wf> [--set NAME]... [--backend duckdb] [--root .]` (P2 adds `snowflake`); exit 0 chain PASS*, 1 FAIL, 2 usage. Files `workflows/<wf>/validation_workflow.json` + `validation_workflow.<set>.json`.
- Report shape (Ruling R-W1): the shared shape plus `"workflow"`, `"boundaries": [{"segment", "stream", "verdict"}]` (work streams), `"finals": [{"segment", "stream", "output", "verdict"}]` (targets), `"divergence_kind": null | "boundary" | "chain_drift"`, `"first_divergence": null | {"segment", "stream", "output", "set"}`. Rule: if any boundary FAILs → `boundary` at the FIRST failing boundary in chain order (waves, then segments in a wave, then contract output order); else if any final FAILs → `chain_drift` at the first failing final (every boundary was within tolerance); a segment that raises while chained → `boundary` with `stream: null` and `error` naming the segment.
- Orchestrator: `translate = VALIDATED` only after the chain PASSes; a `boundary` divergence gets ONE fixer round on that segment (then the segment's own compile/review/validate, then the chain again); `chain_drift` parks `NEEDS_HUMAN` with `reasons.translate = "chain-drift: <output>"`; a second failure `chain: <seg> <stream> after 1 fixer round`; exit 2 `chain: script-error`. dbt: no extra run — `validate_dbt.py` wrote `validation_workflow.json`; translate requires it to be PASS*.

- [ ] **Step 1: Hand-built chain fixtures** — `tests/chain_fixtures.py`, in the style of `tests/test_validate_segment.py`'s `build()` (copy its manifest/mappings/dag shapes), with hand-written C4 SQL procedures and golden data:
  - `build_composition(tmp_path) -> Repo` — `wf_0008`, three segments in three waves. Source ITEMS `(ID Int64, PRICE FixedDecimal 19,3)`: `(1, 1.005)`, `(2, 2.115)`, `(3, 0.125)`. seg_01 `SELECT ID, ROUND(PRICE, 2) AS PRICE` → `MIG_WORK.WF0008_SEG_01_OUT`; golden intermediate keeps the unrounded prices; `manifest.accepted_diffs = [{"segment": "seg_01", "class": "ROUNDING", "columns": ["PRICE"], "approver": "fixture", "date": "2026-09-22"}]`. seg_02 `SELECT ID, PRICE * 1000 AS MILLI` → `WF0008_SEG_02_OUT` (golden from the unrounded prices: 1005, 2115, 125). seg_03 copies `ID, MILLI` to target `MILLI_OUT` (tool 9).
  - `build_drift(tmp_path) -> Repo` — `wf_0010`, two segments: seg_01 as above; seg_02 `SELECT SUM(PRICE) AS TOTAL` → target `TOTALS` (golden 3.245).
  - The fixture's premise is asserted in the tests, not assumed: every segment PASSes (`PASS` or `PASS_WITH_ACCEPTED_DIFF`) through `validate_segment` alone. If compare classifies the seg_01 difference as something other than `ROUNDING`, change the data values (never the assertions) until the premise holds.

- [ ] **Step 2: Failing tests.**

`tests/test_handoff.py`:
- `test_round_trip_duckdb_snowpark_duckdb_keeps_types_and_values` — a DuckDB table with `BIGINT`, `DECIMAL(19,2)`, `DOUBLE`, `VARCHAR`, `BOOLEAN`, `DATE`, `TIMESTAMP` columns and a NULL in each → `table_from_backend` → `load_into_snowpark` → `table_from_snowpark` → `load_into_backend` into a fresh backend: `table_columns` type families equal the original's and `ordered_rows` equal.
- `test_the_real_schema_wins_over_any_declaration` — a Snowpark table written with `StringType` for a numeric-looking column comes back `V_String`, not a number.
- `test_an_unmapped_duckdb_type_is_a_handoff_error` — a `BLOB` column → `HandoffError`.
- `test_no_helper_takes_a_contract` — `inspect.signature` of the four helpers has no parameter named `contract`.

`tests/test_validate_workflow.py`:
- `test_each_segment_passes_alone_but_the_chain_fails_at_the_first_boundary` — composition fixture: `validate_segment` of each segment starts with `PASS`; `r = validate_workflow(repo, "wf_0008", ["normal"])`: `verdict == "FAIL"`, `divergence_kind == "boundary"`, `first_divergence == {"segment": "seg_02", "stream": <seg_02's stream>, "output": "MIG_WORK.WF0008_SEG_02_OUT", "set": "normal"}`, `boundaries[0]["verdict"] == "PASS_WITH_ACCEPTED_DIFF"`.
- `test_accumulated_rounding_is_chain_drift_not_a_boundary` — drift fixture: `divergence_kind == "chain_drift"`, `first_divergence["output"] == "TOTALS"`, every boundary verdict starts with `PASS`.
- `test_a_chain_that_matches_passes_and_writes_its_reports` — the composition fixture with seg_01 NOT rounding: `PASS`, `first_divergence is None`, `validation_workflow.json` and `validation_workflow.normal.json` written, `idempotent is True`.
- `test_a_non_deterministic_segment_makes_the_chain_non_idempotent` — seg_02 adds `+ UNIFORM(0, 1, RANDOM()) * 0.001` (use whichever random form `lib/backend.py` translates; `validate_segment`'s own tests show one): `idempotent is False`, `FAIL`.
- `test_a_segment_that_raises_in_the_chain_is_a_boundary_divergence` — seg_03 selects a column seg_02 does not produce (make seg_03's isolated golden intermediate carry it so it passes alone): `divergence_kind == "boundary"`, `first_divergence["segment"] == "seg_03"`, `first_divergence["stream"] is None`, `error` contains `seg_03`.
- `test_usage_errors_leave_no_report` — missing `order.json`, a missing `proc.sql`, `[]` sets: each raises, a pre-existing `validation_workflow.json` is deleted first, CLI exit 2.
- `test_a_snowpark_segment_writing_the_wrong_type_is_a_type_difference_in_the_chain` — `prepare_workflow(tmp_path, "wf_0006")` with seg_02's `proc.py` changed so `RECOGNIZED` is `StringType()`: the chain FAILs with a `TYPE` cluster on `RECOGNIZED` whose `segment` is `seg_02` (the hand-off did not coerce it to the contract's `FLOAT`).

`tests/test_e2e_chain.py` (`pytestmark = pytest.mark.e2e`):
- `test_every_procedure_sample_passes_as_a_chain[wf]` for `wf_0001`, `wf_0002`, `wf_0003`, `wf_0004`, `wf_0006` — every set PASS*, `first_divergence is None`, `idempotent is True`. **A FAIL here is a real composition finding: stop and report it to the controller with the report; do not change the canned artefacts or the assertion without a ruling.**
- `test_the_dbt_sample_writes_its_chain_report_from_its_own_run` — `validate_dbt(repo, "wf_0007")` then `validation_workflow.json` exists, PASS, `boundaries` holds `seg_01`'s `3_J`; `validate_workflow(repo, "wf_0007")` returns the same verdict (it delegates).

Node (`stages.test.ts`; fake `validate_workflow.py` writes `validation_workflow.json` PASS by default; scenarios `chain-boundary:seg_02` (FAIL `boundary` at `seg_02`/`2_T` on the first call, PASS after), `chain-boundary-stuck:seg_02`, `chain-drift` (FAIL `chain_drift`, `output: "ORDERS_OUT"`), `chain-crash` (exit 2)):
- "the chain runs once after every segment PASSed and before master.sql" — order: the last `agent:validator` < `py:scripts/validate_workflow.py`; VALIDATED; `master.sql` written.
- "a boundary divergence gets one fixer round on that segment" — `chain-boundary:seg_02`: exactly one extra fixer call with `segment: "seg_02"` whose task contains `seg_02/2_T` and `validation_workflow.json`; seg_02's `compile_check.py` and validator ran again; `validate_workflow.py` ran twice; VALIDATED.
- "a boundary that survives the round parks translate" — `chain-boundary-stuck:seg_02`: `NEEDS_HUMAN`, `reasons.translate === "chain: seg_02 2_T after 1 fixer round"`, one extra fixer call only.
- "a chain drift parks translate with chain-drift and runs no fixer" — `reasons.translate === "chain-drift: ORDERS_OUT"`, no fixer call after the chain check, no `master.sql`.
- "a chain script error parks with chain: script-error".
- "a dbt workflow does not run validate_workflow.py; translate needs its chain report" — scenario `dbt` (fake `validate_dbt.py` now also writes `validation_workflow.json` PASS): no `validate_workflow.py` call; VALIDATED; with the fake writing FAIL: `NEEDS_HUMAN` `dbt: chain FAIL`.
- Update the "script calls are exactly what they were" test to expect one `validate_workflow.py` call.

Run: FAIL (modules missing; node assertions).

> **Superseded during execution (rulings, see the phase's rulings file):** feeding a chain segment's upstream rows
> through `validation.ordered_rows` (sorted by every column) was the review's own I2 finding — deterministic, but it
> hid a Snowpark procedure's reliance on physical input order and gave a false `PASS` on two adversarial cases. The
> merged `lib/handoff.py` hands a chain segment its upstream rows in PHYSICAL ARRIVAL order on the chain's first pass,
> and in REVERSED order on the idempotency re-run, so real order-dependence surfaces as a non-idempotent boundary
> instead of staying hidden; a vanished Snowpark output is judged from the fact that it is missing, never from a stale
> backend copy. See rulings 52, 53 and 55.

- [ ] **Step 3: `lib/handoff.py`** — `table_from_backend`: `backend.table_columns(fqn)` → fields via `types_map.duckdb_to_alteryx` (a `ValueError` → `HandoffError(f"{fqn}.{column}: {exc}")`), rows from `validation.ordered_rows(backend, fqn)` converted to `typed_csv` values (`Decimal` kept, `date`/`datetime`/`time` → ISO text as `typed_csv` writes it, `bool`/`int`/`float`/`str` as is). `table_from_snowpark` and `load_into_snowpark` are `validate_snowpark._read_back` and `_save` moved verbatim (`validate_snowpark` keeps `_read_back = handoff.table_from_snowpark`, `_save = handoff.load_into_snowpark`; `ReadBackError` stays in `validate_snowpark` as a subclass alias of `HandoffError` so its tests keep passing). `load_into_backend` is `backend.load_table`.

- [ ] **Step 4: `scripts/validate_workflow.py`** — the chain for one golden set:

```python
class ChainError(Exception):
    def __init__(self, segment: str, cause: Exception):
        super().__init__(f"chain stopped at {segment}: {type(cause).__name__}: {cause}")
        self.segment = segment


def _run_snowpark_segment(backend, repo, wf_id, seg, golden_set, contract, proc_path, args) -> None:
    session = vsp.new_local_session()
    try:
        vsp.load_set_snowpark(session, repo, wf_id, golden_set)               # raw inputs + targets_before
        for entry in contract.get("inputs") or []:
            if entry.get("stream"):                                           # the ACTUAL upstream output
                handoff.load_into_snowpark(session, entry["table"], handoff.table_from_backend(backend, entry["table"]))
        for output in contract.get("outputs") or []:                          # a target's CURRENT chain state
            fqn = v.actual_table(wf_id, seg, output)
            if output.get("kind") == "target" and backend.table_exists(fqn):
                handoff.load_into_snowpark(session, fqn, handoff.table_from_backend(backend, fqn))
        module = vsp.load_module(proc_path)
        module.run(session, args["SRC_DB"], args["SRC_SCHEMA"], args["TGT_DB"], args["TGT_SCHEMA"], args["RUN_ID"])
        for output in contract.get("outputs") or []:
            fqn = v.actual_table(wf_id, seg, output)
            table = handoff.table_from_snowpark(session, fqn)                  # the REAL written schema
            if table is not None:
                handoff.load_into_backend(backend, fqn, table)
    finally:
        session.close()


def _run_chain(repo, wf_id, golden_set, order, contracts, procs, settings, manifest):
    """One pass from the raw golden inputs: every segment in wave order on the actual output of its
    upstream segments. Returns (entries, backend); the caller closes the backend."""
    backend = DuckDBBackend()
    info = load_set(backend, repo, wf_id, golden_set)
    args = {**info["args"], "RUN_ID": f"validate_workflow_{wf_id}_{golden_set}"}
    entries, index = [], 0
    for wave in order:
        for seg in wave:
            contract = contracts[seg]
            try:
                if contract.get("target") == "snowpark":
                    _run_snowpark_segment(backend, repo, wf_id, seg, golden_set, contract, procs[seg], args)
                else:
                    run_proc(backend, procs[seg].read_text(encoding="utf-8"), args)
            except Exception as exc:                                          # noqa: BLE001 -- a domain FAIL
                raise ChainError(seg, exc) from exc
            for output in contract.get("outputs") or []:
                entries.append(_judge(backend, repo, wf_id, seg, golden_set, contract, output, settings, manifest, index))
                index += 1
    return entries, backend
```

`_judge` loads the golden file (`v.golden_path`) as `MIG_COMPARE.CHAIN_EXPECTED_<index>` and calls `compare.compare` with that segment's contract, `output`, tolerances, accepted classes, `approvals` for that segment and its `dag.json` — exactly the arguments `validate_segment._run_one_set` passes (:191–194) — or `v.missing_table_report`. `validate_workflow(repo, wf_id, golden_sets)`: `v.clear_stale_workflow_reports` first; for a dbt workflow (`manifest.output_kind == "dbt"`) return `validate_dbt.validate_dbt(...)`'s workflow report (read back from `validation_workflow.json`); else prerequisites (`order.json`, every contract and its `proc.sql` or `proc.py` by target, mappings, sets); per set `_run_chain` → `v.chain_report(...)` (a `ChainError` → `chain_report(…, error=exc)` with the entries judged before it); for the first set a second independent `_run_chain` and a row-multiset comparison of every entry's relation (`v.ordered_rows`) → `(idempotent, diverging)`, forcing `FAIL` when not idempotent; `v.write_workflow_reports`. `chain_report` builds the shape in **Interfaces** with checks keyed `f"{segment}:{stream}:{kind}:{output}"` and each cluster tagged with `segment` and `stream`. `write_workflow_reports` = `write_reports`' logic writing `validation_workflow.json`/`validation_workflow.<set>.json` at the workflow root (factor the shared tail so both use it).

`validate_dbt.py` builds `chain_report` entries from its own per-output reports in chain order and writes the workflow reports in the same call (one dbt run: "the chain IS the full dbt run").

- [ ] **Step 5: Orchestrator** (`stages.ts`). `migrateSegment(env, m, segment, opts: { firstIteration?: number; iterations?: number; note?: string } = {})` — the loop runs `for (let iteration = opts.firstIteration ?? 0; iteration < (opts.iterations ?? env.config.maxFixIterations); …)` and the fixer task appends `opts.note`; defaults keep today's behaviour byte for byte. After the wave loop in `stageTranslate` (procedures) and before `master.sql`:

```ts
interface ChainReport {
  verdict?: string; needs_human?: boolean; divergence_kind?: "boundary" | "chain_drift" | null;
  first_divergence?: { segment?: string; stream?: string | null; output?: string | null; set?: string } | null;
}

async function chainCheck(env: Env, m: Manifest): Promise<Step> {
  const park = (why: string): Step => {
    m.status.translate = "NEEDS_HUMAN";
    reasons(m).translate = why;
    env.log(`${m.id}: translate → NEEDS_HUMAN (${why})`);
    return "stop";
  };
  for (let round = 0; round < 2; round++) {
    const ran = await env.py("scripts/validate_workflow.py", [m.id]);
    if (ran.code === 2) return park("chain: script-error");
    if (ran.ok) return "continue";
    const report = await readJsonOr<ChainReport>(wfDir(env.root, m.id, "validation_workflow.json"), {});
    const at = report.first_divergence ?? {};
    if (report.divergence_kind === "chain_drift") return park(`chain-drift: ${at.output ?? at.stream ?? "unknown output"}`);
    if (report.needs_human) return park("chain: needs_human");
    if (round === 1 || !at.segment) return park(`chain: ${at.segment ?? "?"} ${at.stream ?? "(raised)"} after 1 fixer round`);
    const note = `The workflow chain (scripts/validate_workflow.py) diverges first at ${at.segment}/${at.stream ?? "(it raised)"} ` +
      `on golden set ${at.set}: read workflows/${m.id}/validation_workflow.json before anything else.`;
    const fixed = await migrateSegment(env, m, at.segment, { firstIteration: 1, iterations: 2, note });
    if (!String(fixed.verdict).startsWith("PASS")) return park(`chain: ${at.segment}: ${fixed.reason ?? "needs_human"}`);
    m.segment_status![at.segment] = fixed.verdict;
    await saveManifest(env.root, m);
  }
  return park("chain: FAIL");
}
```

> **Superseded during execution (rulings, see the phase's rulings file):** the sentence below checks only the chain
> report's PASS/FAIL verdict for a dbt workflow, unlike `chainCheck` above which also parks on `needs_human` alongside
> a `PASS`. The final whole-branch review found the dbt path could reach `VALIDATED` with `needs_human` still set;
> `translateDbt` now parks `dbt: chain needs_human` the same way `chainCheck` does. See ruling N-dbt (ruling 115).

`stageTranslate`: `if ((await chainCheck(env, m)) === "stop") return "stop";` before writing `master.sql`. `translateDbt` (Task D): after a PASS, read `validation_workflow.json`; absent or not `PASS*` → park `dbt: chain FAIL`.

- [ ] **Step 6: Docs** — `docs/reference/large-workflows.md` §"The chain test": why per-segment validation cannot see composition (golden intermediates), what `validate_workflow.py` runs, the report fields, R-W1's classification with the two fixture examples, the typed hand-off at Snowpark seams, the orchestrator routes and reasons, and that nothing here ran on Snowflake.

- [ ] **Step 7: Green** — the new test files, `tests/test_validate_snowpark.py tests/test_validate_dbt.py tests/test_e2e_parity.py`, node suite, tsc, full pytest.

- [ ] **Step 8: Commit**

```bash
git add scripts/validate_workflow.py scripts/lib/handoff.py scripts/lib/types_map.py scripts/lib/validation.py scripts/validate_snowpark.py scripts/validate_dbt.py orchestrator tests/chain_fixtures.py tests/test_handoff.py tests/test_validate_workflow.py tests/test_e2e_chain.py docs/reference/large-workflows.md
git commit -m "feat: validate_workflow.py tests the stitched whole — every segment on its upstream's actual output, first divergence localised, chain drift parked"
```

---

### Task W2: Seam check, batched analyzer, stitched analysis

**Tier:** most capable · **Depends on:** F, W1, P1 · **Wave 6, parallel with P2**

**Files:**
- Create: `scripts/check_seams.py`, `scripts/plan_batches.py`, `scripts/stitch_analysis.py`, `tests/test_check_seams.py`, `tests/test_plan_batches.py`, `tests/test_stitch_analysis.py`
- Modify: `scripts/prompt_context.py` (`--batch`, `render_global`, `segment_detail_chars`), `tests/test_prompt_context.py`, `orchestrator/stages.ts` (`stageAnalyze`; new `analyzeInBatches`), `orchestrator/types.ts` (`AgentCtx.batch`, `OrchestratorConfig.analyzerBudgetChars`), `orchestrator/cli.ts` (`DEFAULT_CONFIG.analyzerBudgetChars = 60000`), `orchestrator.config.json` (same key), `orchestrator/hooks.ts`, `orchestrator/policy.ts` (`PolicyOptions.analyzerBatch`), `orchestrator/runner.ts` (MockRunner batch replay), `orchestrator/test/{fakes,stages.test,policy.test,runner.test}.ts`, `.github/agents/analyzer.agent.md`, `tests/test_agents_config.py`, `docs/reference/large-workflows.md`

**Interfaces:**
- `check_seams.check_seams(repo, wf_id, segments: list[str] | None = None) -> dict` writing `segments/seams.json` = `{"ok": bool, "seams": [{"producer", "consumer", "stream", "table", "status": "ok"|"mismatch", "problems": [str]}], "duplicates": [{"table", "producers": [seg]}]}`; CLI `check_seams.py <wf> [--segments seg_01,seg_02] [--root .]`, exit 0 ok, 1 any mismatch (first stderr line `seam-mismatch: <producer>-><consumer> <stream>`), 2 usage. `--segments` restricts to seams whose CONSUMER is listed (producers must exist).
- `plan_batches.DEFAULT_ANALYZER_BUDGET_CHARS = 60000`; `plan_batches.plan_batches(repo, wf_id, budget_chars) -> dict` writing `segments/batches.json` = `{"budget_chars", "estimate_note", "batches": [{"id": "batch_01", "waves": [0, 1], "segments": [...], "estimate_chars": int}], "warnings": [...]}`; CLI `plan_batches.py <wf> --budget-chars N [--root .]`.
- `stitch_analysis.stitch(repo, wf_id) -> dict` reading `analysis/<batch>.md` and `analysis/<batch>.unsupported.json`, writing `analysis.md` and `unsupported.json`; CLI exit 0 / 1 (a segment missing or duplicated across batches, a fragment missing) / 2.
- `prompt_context.render(repo, wf_id, role, budget_chars, batch: str | None = None)`, `render_global(repo, wf_id) -> str`, `segment_detail_chars(repo, wf_id, seg) -> int`; CLI `--batch batch_02`.
- TS: `AgentCtx.batch?: { id: string; segments: string[] }`; `PolicyOptions.analyzerBatch?: { id: string; segments: string[] }`; reason `seam-mismatch: <producer>-><consumer> <stream>`.

- [ ] **Step 1: Failing tests.**

`tests/test_check_seams.py` (hand-built two- and three-segment workflows written directly as contracts + `order.json`):
- `test_matching_seams_are_ok` — `ok is True`, one `ok` seam per stream input, `seams.json` written.
- `test_a_type_family_change_across_a_seam_is_a_mismatch` — producer `FLOAT`, consumer `NUMBER(38,0)`: `mismatch`, problem names the column and both families; CLI exit 1, first stderr line `seam-mismatch: seg_01->seg_02 2_T`.
- `test_nullability_keys_order_and_table_mismatches_are_each_named` — four sub-cases, one problem each.
- `test_a_consumer_without_a_producer_and_a_stream_produced_twice_are_refused`.
- `test_segments_restricts_the_check_to_those_consumers` — a mismatch on `seg_03`'s input, `--segments seg_02`: exit 0.
- `test_every_committed_sample_has_clean_seams[wf]` — `prepare_workflow(tmp_path, wf)` for every sample with two or more segments (wf_0002, wf_0003, wf_0004, wf_0006, wf_0007): `ok is True`. **A mismatch here is a finding about a canned contract: stop and report it; fixing a canned contract needs a ruling because it changes the committed run.**

`tests/test_plan_batches.py`:
- `test_a_generous_budget_is_one_batch` and `test_every_committed_sample_is_one_batch_under_the_default_budget[wf]` (all seven, via `prepare_workflow` + `segment.py`).
- `test_a_small_budget_cuts_at_wave_boundaries_in_order` — a synthetic five-wave workflow with known `segment_detail_chars`: batches are consecutive wave ranges, every segment exactly once, each `estimate_chars <= budget` unless a single wave exceeds it.
- `test_a_wave_over_budget_is_its_own_batch_with_a_warning`.
- `test_batches_are_deterministic` — two runs, identical `batches.json` bytes.

`tests/test_stitch_analysis.py`:
- `test_every_segment_appears_exactly_once_in_segment_order` — two fragments → `analysis.md` has `## batch_01: segments seg_01, seg_02` then `## batch_02: segments seg_03`; the fragment bodies verbatim.
- `test_a_segment_in_two_batches_is_refused` and `test_a_missing_fragment_is_refused` — exit 1, nothing written.
- `test_unsupported_json_takes_the_highest_tier_and_every_tool_once`.
- `test_stitching_is_deterministic`.

`tests/test_prompt_context.py` add:
- `test_batch_context_carries_the_global_map_and_the_upstream_producer_contracts` — a three-segment workflow, `batch_02 = [seg_03]`: the text has a `### Workflow map` line for EVERY segment, `### Target proposal`, full DAG lines only for seg_03's tools, and `### Producer contracts at this batch's input seams` with seg_02's work output (stream, table, columns, keys).

Node (`stages.test.ts`; fakes: `plan_batches.py` writes one batch unless scenario `batched` (two, one per wave with `twoWaves`); `check_seams.py` exit 0, or 1 with `seams.json` on `seam-mismatch:seg_02`; `stitch_analysis.py` writes `analysis.md` and `unsupported.json`):
- "a small workflow keeps one analyzer call" — one analyzer call, no `batch` ctx, `check_seams.py [wf]` ran inside verify, `plan_batches.py [wf, "--budget-chars", "60000"]` ran before the analyzer.
- "a workflow over the budget is analysed batch by batch and stitched" — `batched`: two analyzer calls with `batch.id` `batch_01`/`batch_02` and their segment lists; `prompt_context.py … --batch batch_0N` before each; `check_seams.py [wf, "--segments", <that batch's segments>]` in each verify; `stitch_analysis.py` after both; analyze DONE; `m.tier` from the stitched `unsupported.json`.
- "a seam mismatch parks analyze with seam-mismatch after one analyzer retry" — two analyzer calls, then `NEEDS_HUMAN`, `reasons.analyze === "seam-mismatch: seg_01->seg_02 2_T"`.
- "a stitch failure parks analyze" — scenario `stitch-fails`: `NEEDS_HUMAN`, reason `stitch`.

`policy.test.ts`: "the analyzer in a batch writes only its batch's contracts and fragments" — with `{ analyzerBatch: { id: "batch_01", segments: ["seg_01"] } }`: `segments/seg_01/contract.json`, `analysis/batch_01.md`, `analysis/batch_01.unsupported.json` allow; `segments/seg_02/contract.json`, `analysis.md`, `unsupported.json`, `manifest.json`, `analysis/batch_02.md` deny. Without a batch: today's lanes.

`runner.test.ts`: "MockRunner's analyzer in a batch replays only that batch" — contracts only for the batch's segments; `analysis/<id>.md` and `analysis/<id>.unsupported.json` written from the canned files.

`tests/test_agents_config.py`: `test_analyzer_documents_batches_and_seams` — `analysis/<batch>.md`, `check_seams.py`, `seam-mismatch`.

Run: FAIL.

- [ ] **Step 2: Scripts.** `check_seams.py`:

```python
def _column_problems(produced: list[dict], consumed: list[dict]) -> list[str]:
    problems = []
    names_p = [str(c["name"]).upper() for c in produced]
    names_c = [str(c["name"]).upper() for c in consumed]
    if names_p != names_c:
        return [f"columns {names_p} produced but {names_c} consumed"]
    for p, c in zip(produced, consumed):
        if type_family(p["type"]) != type_family(c["type"]):
            problems.append(f"{p['name']}: {p['type']} ({type_family(p['type'])}) produced, "
                            f"{c['type']} ({type_family(c['type'])}) consumed")
        if bool(p.get("nullable", True)) != bool(c.get("nullable", True)):
            problems.append(f"{p['name']}: nullable {p.get('nullable', True)} produced, {c.get('nullable', True)} consumed")
    return problems


def check_seams(repo: Repo, wf_id: str, segments: list[str] | None = None) -> dict:
    order = read_json(repo.wf(wf_id, "segments", "order.json"))
    every = [s for wave in order for s in wave]
    contracts = {s: read_json(p) for s in every if (p := repo.seg(wf_id, s, "contract.json")).is_file()}
    producers: dict[str, list[tuple[str, dict]]] = {}
    for seg, contract in contracts.items():
        for output in contract.get("outputs") or []:
            if output.get("kind") == "work" and output.get("table"):
                producers.setdefault(str(output["table"]).upper(), []).append((seg, output))
    seams = []
    for seg in every:
        if (segments is not None and seg not in segments) or seg not in contracts:
            continue
        for entry in contracts[seg].get("inputs") or []:
            if not entry.get("stream"):
                continue
            found = producers.get(str(entry.get("table") or "").upper(), [])
            problems = []
            if not found:
                problems.append(f"no segment produces {entry.get('table')}")
            elif len(found) > 1:
                problems.append(f"{entry.get('table')} is produced by {sorted(s for s, _ in found)}")
            else:
                producer, output = found[0]
                if producer != entry.get("from"):
                    problems.append(f"declared from {entry.get('from')} but produced by {producer}")
                if output.get("stream") != entry.get("stream"):
                    problems.append(f"stream {output.get('stream')} produced, {entry.get('stream')} consumed")
                problems += _column_problems(output.get("columns") or [], entry.get("columns") or [])
                if sorted(str(k).upper() for k in output.get("keys") or []) != sorted(str(k).upper() for k in entry.get("keys") or []):
                    problems.append(f"keys {output.get('keys')} produced, {entry.get('keys')} consumed")
            seams.append({"producer": found[0][0] if len(found) == 1 else None, "consumer": seg,
                          "stream": entry.get("stream"), "table": entry.get("table"),
                          "status": "mismatch" if problems else "ok", "problems": problems})
    duplicates = [{"table": t, "producers": sorted(s for s, _ in ps)} for t, ps in sorted(producers.items()) if len(ps) > 1]
    result = {"ok": all(s["status"] == "ok" for s in seams) and not duplicates, "seams": seams, "duplicates": duplicates}
    write_json(repo.wf(wf_id, "segments", "seams.json"), result)
    return result
```

`plan_batches.py`:

```python
def plan_batches(repo: Repo, wf_id: str, budget_chars: int) -> dict:
    order = read_json(repo.wf(wf_id, "segments", "order.json"))
    base = len(prompt_context.render_global(repo, wf_id))       # map + targets, carried by every batch
    size = {seg: prompt_context.segment_detail_chars(repo, wf_id, seg) for wave in order for seg in wave}
    groups, current, current_chars, warnings = [], [], base, []
    for index, wave in enumerate(order):
        wave_chars = sum(size[s] for s in wave)
        if current and current_chars + wave_chars > budget_chars:
            groups.append(current)
            current, current_chars = [], base
        if base + wave_chars > budget_chars:
            warnings.append(f"wave {index + 1} ({', '.join(wave)}) alone is estimated at {base + wave_chars} "
                            f"characters, over the budget of {budget_chars}")
        current.append(index)
        current_chars += wave_chars
    if current:
        groups.append(current)
    result = {"budget_chars": budget_chars,
              "estimate_note": "characters of the rendered context; tokens are roughly characters / 4",
              "batches": [{"id": f"batch_{n:02d}", "waves": waves,
                           "segments": [s for w in waves for s in order[w]],
                           "estimate_chars": base + sum(size[s] for w in waves for s in order[w])}
                          for n, waves in enumerate(groups, 1)],
              "warnings": warnings}
    write_json(repo.wf(wf_id, "segments", "batches.json"), result)
    return result
```

`prompt_context`: `render_global` = `### Workflow map` (one line per segment: `seg_02 [tools 4, 5] reads 3_J from seg_01; writes 5_Output`) + `### Target proposal`; `segment_detail_chars(seg)` = the length of the DAG-summary lines for that segment's tools; `render(…, batch=)` = header, global part, `### DAG detail for <batch>` (only its segments' tools), `### Producer contracts at this batch's input seams` (for every stream input of the batch whose producer is in an earlier batch: that producer contract's output entry as compact JSON). `stitch_analysis.py` per **Interfaces**; `analysis.md` header `# <wf> analysis (stitched from N batches by scripts/stitch_analysis.py)`.

- [ ] **Step 3: Orchestrator.** In `stageAnalyze`, after `target_check.py`:

```ts
const planned = await env.py("scripts/plan_batches.py",
  [m.id, "--budget-chars", String(env.config.analyzerBudgetChars ?? 60000)]);
if (planned.code === 2) return scriptError(env, m, "analyze", "scripts/plan_batches.py", planned);
const batches = (await readJsonOr<{ batches?: { id: string; segments: string[] }[] }>(
  wfDir(env.root, m.id, "segments", "batches.json"), {})).batches ?? [];
if (batches.length > 1) return await analyzeInBatches(env, m, batches, segments);
```

The single-call verify callback gains, after `checkTargets`, `const seams = await env.py("scripts/check_seams.py", [m.id]); if (seams.code === 2) { reasons(m).analyze = "script-error"; return false; } if (!seams.ok) { reasons(m).analyze = await seamReason(env, m); return false; }` where `seamReason` reads `segments/seams.json` and returns `seam-mismatch: <producer>-><consumer> <stream>` for the first mismatch. `analyzeInBatches`: for each batch, the task names the batch's segments and says to write their `contract.json` files plus `analysis/<id>.md` and `analysis/<id>.unsupported.json` and nothing else, with `prompt_context.py <wf> --role analyzer --batch <id> --budget-chars <analyzerBudgetChars>`'s output appended; `runAgent(…, { batch }, verify)` where verify = those files exist ∧ `check_seams.py <wf> --segments <batch>` exits 0 (1 → `reasons.analyze = seam reason`); then `stitch_analysis.py <wf>` (1 → `domainFailure(env, m, "analyze", "stitch", r)`, 2 → `scriptError`); then the existing post-verify tail (`checkTargets` over all segments, tier from `unsupported.json`, T3, `output_kind`) factored out of `stageAnalyze` as `finishAnalyze(env, m, verdict)` and shared by both paths. `policy.ts`: with `analyzerBatch`, the analyzer's lanes are `segments/(<escaped ids joined by |>)/contract\.json$` and `analysis/<id>\.(md|unsupported\.json)$` only. `hooks.ts`/`runner.ts` pass `ctx.batch` through as `analyzerBatch`; `MockRunner.replayAnalyzer` with `ctx.batch` copies only those contracts and writes the two fragment files from the canned `analysis.md` / `unsupported.json`.

- [ ] **Step 4: Docs** — `analyzer.agent.md` (amended phase 2): the batch instructions, "a seam is the producer's `outputs[]` entry and the consumer's `inputs[]` entry for one work stream: same columns in order, same type family, same nullability, same keys", the reason format; `docs/reference/large-workflows.md` §"Batched analysis and seams" (budget, estimate note, determinism, when it batches, what each call gets, stitching, the pin that the samples stay single-call).

- [ ] **Step 5: Green** — the new Python tests, `tests/test_prompt_context.py`, node, tsc, `tests/test_agents_config.py`, full pytest.

- [ ] **Step 6: Commit**

```bash
git add scripts/check_seams.py scripts/plan_batches.py scripts/stitch_analysis.py scripts/prompt_context.py orchestrator orchestrator.config.json .github/agents/analyzer.agent.md tests/test_check_seams.py tests/test_plan_batches.py tests/test_stitch_analysis.py tests/test_prompt_context.py tests/test_agents_config.py docs/reference/large-workflows.md
git commit -m "feat: deterministic seam check in analyze; the analyzer runs batch by batch above a character budget and its fragments are stitched"
```

---

### Task W3: Size-aware segmentation

**Tier:** standard · **Depends on:** — · **Wave 1, parallel with A**

**Files:**
- Modify: `scripts/segment.py` (`segment()` :64 signature, `merge_pass` :200, `needs_split` :242, `find_split` :246, the warnings, CLI and the manifest/global resolution), `mappings/global.yaml` (new `segmentation:` block), `tests/test_foundations.py` (the pinned key list)
- Create: `tests/test_segment_prompt_size.py`, `docs/reference/large-workflows.md`

**Interfaces:**
- Produces: `segment.DEFAULT_MAX_PROMPT_CHARS = 60000`; `segment.prompt_chars(node: dict) -> int` = `len(json.dumps({"type": node.get("type"), "config": node.get("config"), "meta": node.get("meta")}, sort_keys=True, separators=(",", ":")))`; `segment.segment(dag, *, min_tools=15, max_tools=40, formula_heavy_cap=20, max_prompt_chars=DEFAULT_MAX_PROMPT_CHARS)`; CLI `--max-prompt-chars N`; resolution `--max-prompt-chars` → `manifest.segmentation.max_prompt_chars` → `global.yaml segmentation.max_prompt_chars` → default. The measured committed samples: whole-DAG estimates 1 254–7 187 characters, largest single node 1 239 — far under the default.

- [ ] **Step 1: Failing tests** (`tests/test_segment_prompt_size.py`):
  - `test_prompt_chars_is_the_compact_json_of_type_config_and_meta`.
  - `test_a_group_under_max_tools_but_over_the_prompt_budget_is_split` — a linear chain `input → 6 formula tools (each with a 20 000-character expression) → output`, `min_tools=1, max_tools=40, max_prompt_chars=45000`: every resulting segment's `sum(prompt_chars)` ≤ 45 000 or it is a single tool; tool count per segment < 40.
  - `test_merging_never_crosses_the_prompt_budget` — two undersized neighbours whose combined estimate exceeds the budget stay apart.
  - `test_a_single_tool_over_budget_stands_alone_with_a_warning` — one 70 000-character formula: its own segment and a warning `tool <id> alone is estimated at <n> characters, over segmentation.max_prompt_chars 60000`.
  - `test_prompt_size_splitting_is_deterministic` — two calls, identical results.
  - `test_every_committed_sample_segments_exactly_as_before[wf]` — for every `workflows/<wf>/segments/order.json` in the repo: `segment.segment(parsed dag, **the manifest's segmentation params)` gives the committed `order` and the committed per-segment node ids (from each `segments/<seg>/dag.json`). This parametrisation picks up `wf_0007` automatically once Task G commits it.
  - `tests/test_foundations.py`: the key-list test expects `segmentation` with `max_prompt_chars`.

Run: FAIL (`prompt_chars` missing).

- [ ] **Step 2: Implement.** `size_chars(members) = sum(prompt_chars(nodes_by_id[t]) for t in members)`; `needs_split` adds `or size_chars(members) > max_prompt_chars`; `merge_pass` skips a neighbour when `size_chars(members) + size_chars(nb_members) > max_prompt_chars`; `find_split` balances by characters (`key = (abs(size_chars(a) - size_chars(b)), …)`) when the group is over the character budget, by tool count otherwise; when a group over budget has no splittable bridge, the existing "stays above its size cap" warning names the character estimate; after step 6, for every node with `prompt_chars > max_prompt_chars`, append the single-tool warning. `mappings/global.yaml`:

```yaml
segmentation:
  max_prompt_chars: 60000   # a segment's estimated prompt size (characters of its dag slice: config, expressions, field lists); tokens ≈ chars / 4. Conservative; calibrate from assistant.usage (docs/live-smoke-test.md).
```

Module docstring + `docs/reference/large-workflows.md` §"Segment size" (the estimate, the knobs, the pin).

- [ ] **Step 3: Green** — `tests/test_segment_prompt_size.py tests/test_segment.py tests/test_foundations.py tests/test_io_global_mappings.py`, full suite.

- [ ] **Step 4: Commit**

```bash
git add scripts/segment.py mappings/global.yaml tests/test_segment_prompt_size.py tests/test_foundations.py docs/reference/large-workflows.md
git commit -m "feat: segment.py splits a group over a prompt-size budget even under max_tools; committed samples unchanged"
```

---

### Task W4: Compaction memory aid

**Tier:** standard · **Depends on:** W2, P1 · **Wave 7**

**Files:**
- Modify: `orchestrator/runner.ts` (`CopilotRunner.run`: event subscriptions after `createSession`), `orchestrator/hooks.ts` (`HookState.compactions`, `HookState.peakInputTokens`, `recordMetrics`), `orchestrator/policy.ts` (notes lane), `orchestrator/stages.ts` (intake/analyzer/fixer task text), `orchestrator/test/{runner,policy,stages}.test.ts`, `.github/agents/{intake,analyzer,fixer}.agent.md`, `tests/test_agents_config.py`, `docs/reference/large-workflows.md`

**Interfaces:**
- Produces: `NOTES_ROLES: Role[] = ["intake", "analyzer", "fixer"]`; `notesPath(wfId, role) = workflows/<wf>/notes/<role>.md`; `notesReminder(role, wfId): string`; metrics `manifest.metrics.<role>.compactions` (cumulative) and `.peakInputTokens` (maximum).

- [ ] **Step 1: Failing tests.** Extend `runner.test.ts`'s `fakeClient` with `on(type, handler)` (store handlers, return an unsubscribe), a `send(options)` recorder, and scenario fields `compactions?: { success: boolean }[]` and `usage?: number[]` fired during `sendAndWait`:
  - "a compaction sends one notes reminder and is counted in metrics" — role `analyzer`, two successful compactions: `send` called twice with `mode: "immediate"` and a prompt containing `workflows/wf_0001/notes/analyzer.md`; `wf.metrics.analyzer.compactions === 2`; a log line per compaction.
  - "a failed compaction is logged, not counted, and sends nothing".
  - "a role without notes is counted but not reminded" — role `reviewer`: `compactions === 1`, `send` never called.
  - "assistant.usage input tokens are recorded as peakInputTokens" — usage `[1200, 9000, 4000]` → `9000`; across two sessions the maximum is kept.
  - "handlers are unsubscribed when the session ends".
  - "MockRunner writes no notes and records no compactions".
  - `policy.test.ts`: "intake, analyzer and fixer may write their own notes file" — `workflows/wf_0001/notes/analyzer.md` allow for analyzer, deny for translator and for `notes/fixer.md` as analyzer.
  - `stages.test.ts`: "the intake, analyzer and fixer tasks name their notes file".
  - `tests/test_agents_config.py`: `test_intake_analyzer_and_fixer_keep_notes` (`notes/<role>.md` in each, and "the durable record stays in the contract and the files").

- [ ] **Step 2: Implement.**

```ts
export const NOTES_ROLES: Role[] = ["intake", "analyzer", "fixer"];
export const notesReminder = (role: Role, wfId: string): string =>
  `Your context was just compacted. Re-read workflows/${wfId}/notes/${role}.md (your decisions and open items) ` +
  `and the files you have already written before continuing; they are the record, not your memory.`;

// in CopilotRunner.run, right after createSession:
const unsubscribe: (() => void)[] = [];
unsubscribe.push(session.on("session.compaction_complete", (event) => {
  if (!event.data.success) {
    env.log(`${wf.id}: ${role} context compaction failed`);
    return;
  }
  state.compactions += 1;
  env.log(`${wf.id}: ${role} context compacted (${state.compactions} so far)`);
  if (NOTES_ROLES.includes(role)) {
    void session.send({ prompt: notesReminder(role, wf.id), mode: "immediate" }).catch(() => undefined);
  }
}));
unsubscribe.push(session.on("assistant.usage", (event) => {
  const input = event.data.inputTokens;
  if (typeof input === "number" && input > state.peakInputTokens) state.peakInputTokens = input;
}));
// in finally: for (const off of unsubscribe) off();
```

> **Superseded during execution (rulings, see the phase's rulings file):** the fixed sentence below is what this task
> shipped, but the third live test (Task H) found the model repeatedly tried to CREATE `notes/` itself, burning tool
> calls on denials every time. Task N1 (controller-added) makes the orchestrator create `workflows/<wf>/notes/` before
> a notes role's session starts, and the sentence gains a second half: "The directory already exists; write the file
> with your file-writing tool; do not create directories." The policy is unchanged — directory creation by an agent
> stays denied. See rulings 93 and 97-99.

`recordMetrics` adds `compactions` (accumulate) and `peakInputTokens` (`Math.max`). `policy.ts`: each of the three roles gains the lane `${wf}/notes/${role}\\.md$`. `stages.ts`: the intake, analyzer and fixer tasks (all their forms, batched included) end with ` Keep your decisions and open items in workflows/<id>/notes/<role>.md as you go; the durable record stays in the contract and the files you write.` Agent files (amended phase 2) say the same. `docs/reference/large-workflows.md` §"Compaction" (cites `session-events.d.ts` for the event names; unverified live until Task H).

- [ ] **Step 3: Green** — node, tsc, `tests/test_agents_config.py`, full pytest.

- [ ] **Step 4: Commit**

```bash
git add orchestrator .github/agents tests/test_agents_config.py docs/reference/large-workflows.md
git commit -m "feat: notes files for intake, analyzer and fixer; a re-read nudge after every context compaction; compactions and peak input tokens in metrics"
```

---

### Task P1: Hosted-model hook-up as configuration

**Tier:** standard · **Depends on:** D · **Wave 5, parallel with W1**

**Files:**
- Modify: `orchestrator/types.ts` (`ProfileConfig`, `RunOptions.checkModels`), `orchestrator/runner.ts` (`createSession` call :306–318), `orchestrator/cli.ts` (`parseArgs` :57, `main` :263 — `--check-models`; `main(argv, deps: { env?; listModels? })`), `orchestrator.config.json` (`profiles.hosted.roleContextTiers`, `roleReasoningEffort` per `docs/handoff-copilot-models.md` §4 item 4), `docs/handoff-copilot-models.md` (§4 says done; §2/§5 name `--check-models` and `set_models.py`)
- Create: `orchestrator/models.ts`, `orchestrator/test/models.test.ts`, `scripts/dev/set_models.py`, `tests/test_set_models.py`
- Modify tests: `orchestrator/test/runner.test.ts`, `orchestrator/test/cli.test.ts`

**Interfaces:**
- `ProfileConfig.roleContextTiers?: Partial<Record<Role, "default" | "long_context">>`; `ProfileConfig.roleReasoningEffort?: Partial<Record<Role, ReasoningEffort>>`.
- `models.ts`: `interface ConfiguredModel { where: string; id: string }`; `interface CatalogModel { id: string; policy?: { state?: string } }`; `interface ModelCheck { ok: boolean; missing: ConfiguredModel[]; disabled: ConfiguredModel[] }`; `configuredModels(config: OrchestratorConfig, profile: Profile, agentModels: Record<string, string>, cliConfig: { subagents?: { agents?: Record<string, { model?: string }> } }): ConfiguredModel[]`; `checkModels(configured: ConfiguredModel[], catalog: CatalogModel[]): ModelCheck`.
- CLI `orchestrate.ts --check-models --profile hosted` → exit 0 all configured ids exist and are `enabled`; 1 otherwise (prints each `where: id — missing|disabled`); 2 for `--profile local` or when the catalog cannot be listed (needs the human's login; never run by an agent).
- `set_models.py --default <id> [--role <role>=<id>]... [--long-context-roles r1,r2] [--effort <role>=<low|medium|high|xhigh|max>]... [--root .] [--dry-run]` — Python because it edits JSON and Markdown frontmatter and needs no SDK; it never contacts the network (validation is `--check-models`' job).

> **Superseded during execution (rulings, see the phase's rulings file):** `test_one_command_sets_every_place_consistently`
> (Step 1, below) passed here, but Task P4 found `--default <id>` in fact left five of the nine hosted roles on their
> placeholder model in a real config load — the orchestrator's config loader deep-merges `DEFAULT_CONFIG`'s own
> per-role model map back over a file profile's role map. The fix (ruling 83): `DEFAULT_CONFIG.profiles.hosted` carries
> NO `roleModels` at all, and a file profile's `roleModels`/`roleContextTiers`/`roleReasoningEffort` are taken as whole
> maps, never merged key by key with the defaults.

- [ ] **Step 1: Failing tests.**
  - `runner.test.ts` — the fake client records the `createSession` config: "per-role context tier and effort reach createSession" — hosted-shaped profile `{ model: "m", reasoningEffort: "medium", roleContextTiers: { analyzer: "long_context" }, roleReasoningEffort: { reviewer: "low" } }`: analyzer → `contextTier: "long_context"`, `reasoningEffort: "medium"`; reviewer → `contextTier` absent, `reasoningEffort: "low"`; "the local profile is unchanged" — no `contextTier` key at all.
  - `models.test.ts` — "every configured id is in the enabled catalog → ok"; "a missing id and a disabled id are each reported with where they live"; "configuredModels collects profile.model, roleModels, every agent frontmatter model and every config.json sub-agent model, de-duplicated by (where, id)".
  - `cli.test.ts` — "--check-models lists the catalog through the injected listModels and exits 1 on a missing id" (deps `listModels: async () => [{ id: "a", policy: { state: "enabled" } }]`); "--check-models with --profile local is a usage error (exit 2)"; "a listModels failure exits 2 and names the login step".
  - `tests/test_set_models.py` (on tmp copies of the three files):
    - `test_one_command_sets_every_place_consistently` — `--default luna-x --role translator=astra-y --long-context-roles intake,analyzer,fixer,parser-recovery,validator --effort documenter=low --effort reviewer=low`: `orchestrator.config.json` `profiles.hosted.model == "luna-x"`, `roleModels == {"translator": "astra-y"}`, `roleContextTiers` five entries, `roleReasoningEffort` two; each `.github/agents/<role>.agent.md` frontmatter `model:` is the role's id and the body is byte-identical; `config.json` `subagents.agents.<name>.model` is the role's id for the nine custom agents and the default for the five built-ins, `contextTier` `long_context` exactly for the listed roles, no `modelPolicy` key anywhere; every other key of all three files unchanged (parsed equality).
    - `test_the_local_profile_is_never_touched`.
    - `test_dry_run_writes_nothing_and_prints_the_changes`.
    - `test_an_unknown_role_or_effort_is_a_usage_error` (exit 2, nothing written).
    - `test_running_it_twice_is_a_no_op`.
    - `tests/test_agents_config.py` must still pass on the result (run its frontmatter checks against the tmp copy).

Run: FAIL.

- [ ] **Step 2: Implement** — `types.ts` fields; `runner.ts`:

```ts
session = await this.client.createSession({
  workingDirectory: this.root,
  model: profile.roleModels?.[role] ?? profile.model,
  reasoningEffort: profile.roleReasoningEffort?.[role] ?? profile.reasoningEffort,
  ...(profile.roleContextTiers?.[role] ? { contextTier: profile.roleContextTiers[role] } : {}),
  provider: profile.provider,
  // …unchanged
});
```

`models.ts` pure functions; `cli.ts` `--check-models` branch before any workflow selection (reads `config.json` at the root if present, `loadAgents(root, "hosted")` frontmatter models, `deps.listModels ?? (start a CopilotClient, listModels, stop)`); `orchestrator.config.json` hosted profile gains the two maps exactly as `docs/handoff-copilot-models.md` §4 item 4 lists them; `set_models.py` rewrites only `model:` in frontmatter (line-based, preserving everything else) and JSON through `json.loads`/`json.dumps(indent=2)` + newline (formatting of `config.json` changes to indent-2; its parsed content is the contract). `docs/handoff-copilot-models.md` §4 rewritten as "done" with the command.

- [ ] **Step 3: Green** — node, tsc, `tests/test_set_models.py tests/test_agents_config.py`, full pytest.

- [ ] **Step 4: Commit**

```bash
git add orchestrator orchestrator.config.json scripts/dev/set_models.py tests/test_set_models.py docs/handoff-copilot-models.md
git commit -m "feat: per-role context tier and effort for hosted sessions; set_models.py sets model ids in all three places; --check-models preflight"
```

---

### Task P2: A real Snowflake backend for the validators, and `deploy.py`

**Tier:** most capable · **Depends on:** B, W1 · **Wave 6, parallel with W2**

**Files:**
- Create: `scripts/lib/snowflake_conn.py`, `scripts/lib/snowflake_sandbox.py`, `scripts/deploy.py`, `tests/fake_snowflake.py`, `tests/test_snowflake_conn.py`, `tests/test_snowflake_validators.py`, `tests/test_deploy.py`, `docs/reference/snowflake-backend.md`
- Modify: `scripts/lib/backend.py` (`SnowflakeBackend.__init__(connection_name=None)`, `call_procedure`, error wrapping), `scripts/load_golden.py` (`load_set(…, *, database=SANDBOX_DB)`), `scripts/lib/validation.py` (`actual_table(…, database=SANDBOX_DB)`), `scripts/validate_segment.py`, `scripts/validate_snowpark.py`, `scripts/validate_dbt.py`, `scripts/validate_workflow.py` (each `--backend duckdb|snowflake`, `--connection NAME`, `--sandbox-database DB`)

**Interfaces:**
- `snowflake_conn`: `CONNECTION_ENV = "MIG_SNOWFLAKE_CONNECTION"`, `SANDBOX_DB_ENV = "MIG_SANDBOX_DATABASE"`; `class ConnectionRefused(ValueError)`; `connection_name(explicit: str | None) -> str` (argument, else the env var, else `ConnectionRefused`); `connect(name: str)` → `snowflake.connector.connect(connection_name=name)` and nothing else; `snowpark_session(name: str)` → `Session.builder.config("connection_name", name).create()`; `redact(text: str) -> str` (masks `password=…`, `token=…`, `private_key…=…`, `PRIVATE KEY` blocks).
- `snowflake_sandbox`: `ident(name: str) -> str` (`^[A-Z_][A-Z0-9_$]*$` or `ValueError`); `class SnowflakeSandbox(connection_name: str, database: str)` with `fresh(extra_schemas: Sequence[str] = ()) -> SnowflakeBackend` (`USE DATABASE <db>`, then `CREATE OR REPLACE SCHEMA <db>.<s>` for `MIG_GOLDEN`, `MIG_WORK`, `MIG_COMPARE` and the extras).
- `SnowflakeBackend.call_procedure(proc_sql: str, args: dict[str, str]) -> None` (executes the DDL, then `CALL <parse_proc(proc_sql).name>(%s, %s, %s, %s, %s)` with the args in the C4 order); connector errors become `BackendError` with `redact`ed text; passing `password=` raises `BackendError("passwords are never passed as arguments; use a named connection")`.
- Validators: the snowflake path is selected only by `--backend snowflake`; default unchanged (byte-identical reports for the DuckDB path — pinned by the existing suites). One validation at a time per sandbox database (documented; `MIG_WORK` is shared).
- `deploy.py <wf> --database DB --schema SCHEMA [--src-schema S] [--connection NAME] [--execute] [--root .]` — dry-run by default (prints, connects to nothing); refuses a workflow whose `status.translate` is not `VALIDATED` (exit 1); procedures: `USE DATABASE DB`, `CREATE SCHEMA IF NOT EXISTS MIG_WORK`, every `segments/<seg>/proc.sql` in `order.json` order, `procs/master.sql`, then a commented example `CALL MIG_WORK.<WF>_MASTER('<SRC_DB>', '<SRC_SCHEMA>', 'DB', 'SCHEMA', '<run id>')`; dbt: the `procs/README.md` command with `<SRC>`/`<TGT>` filled from `--src-schema`/`--schema`; `--execute` runs the statements through `SnowflakeBackend(connection_name=…)` or, for dbt, `dbt_project.run_dbt("run", …, target="snowflake", duckdb_path=None, extra_env={"SNOWFLAKE_DATABASE": DB})` (dbt-snowflake missing → exit 2 naming it).

- [ ] **Step 1: The fake** — `tests/fake_snowflake.py`: a DuckDB-backed Snowflake double. `FakeConnectorModule.connect(**kwargs)` records `kwargs` and returns a `FakeConnection` whose cursors: record every statement and its params verbatim in `statements`; track the current database from `USE DATABASE X`; qualify two-part names with it and translate through `DuckDBBackend.translate` (sqlglot) before running on one in-memory DuckDB; store `CREATE OR REPLACE PROCEDURE` text by name and run `CALL name(...)` through `lib.proc_runner.run_proc` with the bound params; answer `DESCRIBE TABLE` from `information_schema.columns` in the connector's row shape (name, type, kind, null?); map `%s` to `?`. `FakeSession` for Snowpark = a `local_testing` session with a recorder of the connection name it was built for, and `sql()` statements recorded instead of executed.

- [ ] **Step 2: Failing tests.**
  - `tests/test_snowflake_conn.py` — `test_connect_passes_only_the_connection_name` (`monkeypatch.setitem(sys.modules, "snowflake.connector", fake)`; kwargs `== {"connection_name": "sandbox"}`); `test_the_name_comes_from_the_argument_or_the_environment_and_nothing_else` (no name → `ConnectionRefused` naming `MIG_SNOWFLAKE_CONNECTION` and `connections.toml`); `test_redact_masks_passwords_tokens_and_keys`; `test_a_password_argument_is_refused_by_the_backend`.
  - `tests/test_snowflake_validators.py` — with the fake connector patched in:
    - `test_validate_segment_on_snowflake_deploys_calls_and_judges` — `validate_segment.main([wf, "seg_01", "--backend", "snowflake", "--connection", "sandbox", "--sandbox-database", "MIGDB_SANDBOX", "--set", "normal", "--root", …])` on `prepare_workflow(tmp_path, "wf_0001")`: exit 0; the recorded statements start `USE DATABASE MIGDB_SANDBOX`, then `CREATE OR REPLACE SCHEMA MIGDB_SANDBOX.MIG_GOLDEN`, `…MIG_WORK`, `…MIG_COMPARE`, `…MIG_GOLDEN_WF0001_NORMAL`; contain the exact `proc.sql` text and one `CALL MIG_WORK.WF0001_SEG_01(%s, %s, %s, %s, %s)` with params `["MIGDB_SANDBOX", "MIG_GOLDEN_WF0001_NORMAL", "MIGDB_SANDBOX", "MIG_WORK", "validate_wf_0001_seg_01_normal"]`; the report equals (minus `runtime_ms`) the DuckDB-path report.
    - `test_validate_snowpark_on_snowflake_builds_its_session_from_the_connection` — `snowflake_conn.snowpark_session` patched to return a `FakeSession`: called once per run with `"sandbox"`; PASS.
    - `test_validate_dbt_on_snowflake_uses_the_snowflake_target` — `dbt_project.run_dbt` patched to record: `target == "snowflake"`, `vars == {"src_schema": "MIG_GOLDEN_WF0007_NORMAL", "tgt_schema": "MIG_WORK"}`, `duckdb_path is None`, `extra_env == {"SNOWFLAKE_DATABASE": "MIGDB_SANDBOX"}`; the fake then populates the model tables so the comparison runs.
    - `test_validate_workflow_on_snowflake_runs_the_chain_in_one_sandbox`.
    - `test_no_credential_reaches_argv_logs_or_reports` — `SNOWFLAKE_PASSWORD=hunter2-secret` in the environment: absent from captured stdout/stderr, every written report, every recorded statement and the connector kwargs; `parser.parse_args(["--password", "x"])` exits 2 for all four validators.
    - `test_the_duckdb_default_is_unchanged` — `--backend` omitted: no fake connection is created.
  - `tests/test_deploy.py` — `test_dry_run_prints_the_procedures_in_order_and_connects_to_nothing` (committed `workflows/wf_0006` copied to tmp: the printed statements are exactly the three `proc.sql` files in order then `master.sql`, each preceded by `-- workflows/wf_0006/…`; the fake connector was never called); `test_execute_runs_exactly_those_statements` (recorded list equals `[USE DATABASE ANALYTICS, CREATE SCHEMA IF NOT EXISTS MIG_WORK, <three procs>, <master>]`); `test_a_dbt_workflow_prints_its_run_command` (committed `wf_0007` copied — this test runs after Task G; until then build it with `prepare_workflow` + `validate_dbt` + a manifest marked VALIDATED); `test_an_unvalidated_workflow_is_refused` (exit 1); `test_execute_needs_a_connection` (exit 2).

Run: FAIL.

- [ ] **Step 3: Implement** per **Interfaces**. In `validate_segment._run_one_set` the two `DuckDBBackend()` constructions become `backend_factory()` (a parameter threaded from `validate_segment(…, backend="duckdb", connection=None, sandbox_database=None)`); for Snowflake, `_load_and_run` calls `backend.call_procedure(proc_sql, args)` instead of `run_proc`, `load_set(…, database=db)` and `actual_table(…, database=db)` use the sandbox database, and idempotency snapshots the first run's outputs (`v.ordered_rows`) before the second `fresh()` resets the schemas. `validate_snowpark` for Snowflake: `session = snowflake_conn.snowpark_session(name)`, schemas prepared through a `_prepare_schemas(session, db, schemas)` helper (`session.sql("CREATE OR REPLACE SCHEMA …").collect()`), `load_set_snowpark(…, database=db)`. `validate_dbt` for Snowflake: goldens through `SnowflakeSandbox.fresh` + `load_set`, `run_dbt(target="snowflake", vars={"src_schema": golden_view_schema(...), "tgt_schema": "MIG_WORK"}, duckdb_path=None, extra_env={"SNOWFLAKE_DATABASE": db})`, reads back through `SnowflakeBackend`. `validate_workflow` for Snowflake: one `SnowflakeSandbox.fresh()` per chain pass; SQL segments through `call_procedure`; a Snowpark segment runs in `snowflake_conn.snowpark_session(name)` against the same sandbox database, where its upstream tables already are — on a real account there is one engine, so the typed hand-off is not used; `test_validate_workflow_on_snowflake_runs_the_chain_in_one_sandbox` asserts the `CALL`s are in wave order, that every chain pass starts with the four `CREATE OR REPLACE SCHEMA` statements, and that no `lib.handoff` function was called. `docs/reference/snowflake-backend.md`: the role and grants the pipeline needs (USAGE on warehouse and sandbox database, CREATE SCHEMA in it, CREATE TABLE/VIEW/PROCEDURE in its schemas, SELECT on the mapped source schemas for a real run), the sandbox layout, the human's `connections.toml` entry (key-pair or SSO, never a password in a repo file), every command, "one validation at a time per sandbox database", and "nothing here has run against a real account".

- [ ] **Step 4: Green** — the three new files plus `tests/test_validate_segment*.py tests/test_validate_snowpark.py tests/test_validate_dbt.py tests/test_validate_workflow.py tests/test_backend.py tests/test_load_golden.py`, full pytest.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/backend.py scripts/lib/snowflake_conn.py scripts/lib/snowflake_sandbox.py scripts/lib/validation.py scripts/load_golden.py scripts/validate_segment.py scripts/validate_snowpark.py scripts/validate_dbt.py scripts/validate_workflow.py scripts/deploy.py tests/fake_snowflake.py tests/test_snowflake_conn.py tests/test_snowflake_validators.py tests/test_deploy.py docs/reference/snowflake-backend.md
git commit -m "feat: --backend snowflake for every validator through named connections only; deploy.py prints or executes a validated workflow's DDL"
```

---

### Task P3: Real Alteryx corpus triage

**Tier:** standard · **Depends on:** — · **Wave 2, parallel with B**

**Files:**
- Create: `scripts/survey_corpus.py`, `tests/test_survey_corpus.py`, `tests/corpus_fixtures/unknown_plugin.yxmd`, `docs/reference/real-workflows.md`

**Interfaces:**
- `survey_corpus.survey(directory: Path, *, prefer: str = "procedures") -> dict` = `{"workflows": [{"path" (relative, `/`), "status": "ok"|"error", "error"?, "tools": int, "classes": {"sql", "snowpark", "manual", "unknown": int}, "unknown_plugins": [str], "unresolved_macros": [str], "tier": "T1"|"T2"|"T3", "output_kind": "procedures"|"dbt", "dbt_blockers": [str], "segments": int}], "plugins": [{"plugin", "type", "count"}]}`; `render_markdown(report) -> str`; CLI `survey_corpus.py <dir> [--out report.md] [--json report.json] [--prefer procedures|dbt]`, exit 0 (parse failures are reported, not fatal), 2 usage.
- Uses `parse.parse_file(path)` (`.yxmd`, `.yxwz`; a `.yxzp` is extracted to a temp dir first and its workflow found with `parse.workflow_file`), `plugin_map.node_class`, `target_check.expand`/`classify` for macro sub-DAGs, `segment.segment(dag)` with its defaults for the estimate, `target_check.dbt_blockers`-equivalent rules on the DAG alone (write modes from output configs: `update_insert` without keys → `merge_without_keys`, …).

- [ ] **Step 1: Failing tests** — `test_the_committed_samples_survey_as_expected` (over a tmp copy of `samples/*/source/`: seven `ok` rows; `wf_0005`'s row has `unknown_plugins == ["AcmeAnalytics.Dedupe.DedupeTool"]` and tier `T3`; `wf_0006` tier `T2`; `wf_0007` (after C; parametrise over what exists) `output_kind == "dbt"` with `--prefer dbt`); `test_an_unknown_plugin_is_listed_by_name_and_counted` (the synthetic fixture); `test_an_unparsable_file_is_an_error_row_not_a_crash`; `test_the_plugin_table_is_sorted_by_count_then_name`; `test_the_report_carries_no_absolute_path`; `test_output_is_deterministic`; `test_cli_writes_markdown_and_json`.

- [ ] **Step 2: Implement**; `docs/reference/real-workflows.md`: how to take one real workflow end to end — survey → extend `scripts/parsers/plugin_map.py` for the reported plugins (with the "verify against your Alteryx version" marker) → copy the source into `workflows/<wf>/source/` → interactive intake (README §5) → golden capture with `inject_outputs.py --capture-dir` then `AlteryxEngineCmd.exe` on the instrumented copy (a human runs it) then `--import-set` → the orchestrator with `golden.producer: "alteryx"` → review. States what has never been exercised (a real Alteryx engine run).

- [ ] **Step 3: Green; Step 4: Commit**

```bash
git add scripts/survey_corpus.py tests/test_survey_corpus.py tests/corpus_fixtures docs/reference/real-workflows.md
git commit -m "feat: survey_corpus.py triages a directory of real Alteryx workflows — classes, unknown plugins, tiers, output kinds, segment estimates"
```

---

### Task G: The offline run for all seven samples and the committed `workflows/wf_0007/`

**Tier:** most capable · **Depends on:** A–F, W1–W4, P1–P3 · **Wave 8**

**Files:**
- Modify: `workflows/**` (committed product), `tests/test_committed_workflows.py`, `README.md` (§6)

- [ ] **Step 1: Failing tests** (`tests/test_committed_workflows.py`, written and run against the OLD tree first):
  - `EXPECTED_TERMINAL["wf_0007"] = "VALIDATED"`; `DBT_WORKFLOW_IDS = ["wf_0007"]`; module docstring says seven.
  - `test_a_committed_dbt_workflow_is_a_project_with_a_readme_and_no_procedures` — `manifest.output_kind == "dbt"` and `output_target == "dbt"`; `dbt/` holds the five `PROJECT_FILES`, `translation_notes.md`, `compile_check.json` (`target: dbt`, `status: OK`) and `review.json` (PASS); `procs/README.md == dbtReadme`-equivalent text containing the §4.3 command; no `procs/master.sql`; no `segments/*/proc.sql` or `proc.py`; every segment's `validation.json` has `"target": "dbt"` and a passing verdict.
  - `test_a_committed_sql_segment_s_validation_report_carries_no_target_key` — iterate procedure workflows only (skip nothing: filter `manifest.output_kind != "dbt"`).
  - `test_every_validated_workflow_has_a_passing_chain_report` — `validation_workflow.json` exists, verdict PASS*, `first_divergence is None`, `idempotent is True`, and one `validation_workflow.<set>.json` per golden set.
  - `test_every_committed_workflow_has_a_batch_plan_and_translated_ones_have_clean_seams` — `segments/batches.json` has one batch; every non-T3 workflow's `segments/seams.json` has `ok: true`.
  - `test_no_duckdb_or_audit_log_files_are_tracked` — also no tracked path containing `/dbt/logs/`, `/dbt/target/`, `dbt_sandbox_`.
  Expected on the old tree: failures for `wf_0007` and the new files only.

- [ ] **Step 2: The run** — the phase-1 6B procedure, in a scratch root outside the repo, with this branch's `scripts mappings catalog` copied and `samplesDir` pointing at this branch's `samples/`:

```bash
REPO="$(pwd -W)"
SCRATCH="<a scratch root outside the repo>/p2-run1"
mkdir -p "$SCRATCH" && cp -r scripts mappings catalog "$SCRATCH/"
printf '{"python": "%s/.venv/Scripts/python.exe", "samplesDir": "%s/samples"}' "$REPO" "$REPO" > "$SCRATCH/orchestrator.config.json"
.venv/Scripts/python.exe scripts/dev/build_samples.py seed --root "$SCRATCH" --samples "$REPO/samples"
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --runner mock --no-interactive
.venv/Scripts/python.exe scripts/dev/answer_samples.py --root "$SCRATCH" --samples "$REPO/samples"
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --runner mock --no-interactive
cp -r "$SCRATCH/workflows" "$SCRATCH/before-pass3"
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --runner mock --no-interactive
```

Expected: seeds seven; pass 1 parks all at `WAITING_FOR_ANSWERS`; answers `wf_0007: 4 answered, 0 unanswered`; pass 2 terminal states wf_0001–4, wf_0006, wf_0007 `VALIDATED`, wf_0005 `MANUAL`; pass 3 changes only `updated_at` (diff against `before-pass3`).

- [ ] **Step 3: Classify old vs new BEFORE overwriting** — key by key over both trees (the 6B script). Allowed kinds only: (a) new tree `wf_0007/`; (b) new files `validation_workflow.json` + `validation_workflow.<set>.json` in every VALIDATED procedure workflow, `segments/batches.json` in all seven, `segments/seams.json` in every non-T3 workflow; (c) `docs/migration.md` of wf_0001–4 gaining exactly the canned `## Deployment` section; (d) `runtime_ms` values; (e) `updated_at`. Anything else (a changed `validation.json` key, a changed `proc.sql`, a changed `checks` key from Task B's disambiguation, a manifest field) is a regression: stop and report it with the diff.

- [ ] **Step 4: Copy and check** — copy `$SCRATCH/workflows/` over the repo's `workflows/` (never `.duckdb`, `audit.jsonl`, `dbt/logs/`; `git check-ignore` over every new file prints nothing unexpected); `git status` shows no deletion. Run `tests/test_committed_workflows.py` → green.

- [ ] **Step 5: Reproducibility** — a second, independent scratch root from `git archive HEAD` of the commit (its own `samples/ scripts/ mappings/ catalog/`, its `workflows/` deleted first), the same sequence, then `diff -rq -x audit.jsonl -x '*.duckdb' -x '*.duckdb.wal' -x logs workflows "$S2/workflows"`: only `runtime_ms`/`updated_at` differ; with those normalised the diff is empty. Record both counts in the commit message body.

- [ ] **Step 6: README §6** — "seven sample workflows"; the terminal-states row `| wf_0007 | T1 | PARSED | READY | DONE | DONE | **VALIDATED** | seg_01/02: PASS (one dbt project) |`; a paragraph "**The worked dbt example is `workflows/wf_0007/dbt/`**" (what each file is, `procs/README.md` instead of `master.sql`, `validation.json` carrying `"target": "dbt"`, `validation_workflow.json`); a sentence that every VALIDATED workflow now carries `validation_workflow.json`, `batches.json` and `seams.json`; the sample table (wherever README lists samples) gains `wf_0007`.

- [ ] **Step 7: Full suites** — pytest (0 skipped), node, tsc.

- [ ] **Step 8: Commit**

```bash
git add workflows tests/test_committed_workflows.py README.md
git commit -m "feat: offline run refreshed for all seven samples — wf_0007's dbt project committed; chain reports, batch plans and seam checks in every workflow"
```

---

### Task P4: `docs/handoff-production.md` and `docs/production-backlog.md`

**Tier:** standard · **Depends on:** G (and everything it documents) · **Wave 9**

**Files:**
- Create: `docs/handoff-production.md`, `docs/production-backlog.md`, `tests/test_handoff_production.py`
- Modify: `docs/handoff-copilot-models.md` (a pointer at the top; §4 already done by P1), `README.md` (pointers from §1's targets section, §7 and §8 — not the diagrams, which are P5's)

- [ ] **Step 1: Failing test** — `tests/test_handoff_production.py`:

```python
"""docs/handoff-production.md and docs/production-backlog.md are followed literally by an agent,
so nothing they name may rot."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "handoff-production.md"
BACKLOG = ROOT / "docs" / "production-backlog.md"
DOCS = (GUIDE, BACKLOG)
_PATH = re.compile(r"`((?:scripts|orchestrator|docs|tests|mappings|samples|cookbook|workflows|\.github)/[^`\s*<>]+"
                   r"|orchestrate\.ts|config\.json|orchestrator\.config\.json|requirements\.txt)`")
_PY = re.compile(r"\.venv/Scripts/python\.exe (scripts/[\w/]+\.py)")
_FLAG = re.compile(r"orchestrate\.ts[^\n`]*?(--[a-z-]+)")
GUIDE_SECTIONS = ("## 1. Copilot Enterprise models", "## 2. Snowflake access", "## 3. Real Alteryx workflows",
                  "## 4. The verification ladder", "## 5. What has never been proven", "## Never do")
BACKLOG_ITEMS = ("Alteryx tool coverage", "Sources and sinks outside Snowflake", "Golden-data governance",
                 "Parallel run and reconciliation", "Scheduling", "Environment promotion",
                 "Continuous integration", "Reproducible installs", "Cost and status visibility",
                 "Model evaluation", "Prompt-injection tests")
BACKLOG_FIELDS = ("**What is missing.**", "**Why it matters.**", "**What exists to build on.**",
                  "**First concrete step.**")


def _all_text() -> str:
    return "\n".join(doc.read_text(encoding="utf-8") for doc in DOCS)


def test_the_guide_sections_are_present_in_order():
    text = GUIDE.read_text(encoding="utf-8")
    positions = [text.index(s) for s in GUIDE_SECTIONS]
    assert positions == sorted(positions)


def test_the_guide_links_the_backlog_as_the_first_week_checklist():
    text = GUIDE.read_text(encoding="utf-8")
    assert "`docs/production-backlog.md`" in text and "first-week checklist" in text


def test_every_backlog_item_has_its_four_fields():
    sections = re.split(r"^## ", BACKLOG.read_text(encoding="utf-8"), flags=re.MULTILINE)
    for item in BACKLOG_ITEMS:
        body = next((s for s in sections if s.startswith(item)), None)
        assert body is not None, f"production-backlog.md has no '## {item}' section"
        for field in BACKLOG_FIELDS:
            assert field in body, f"'{item}' lacks {field}"


def test_every_named_path_exists():
    missing = [p for p in sorted(set(_PATH.findall(_all_text()))) if not (ROOT / p.rstrip("/.,:")).exists()]
    assert not missing, missing


@pytest.mark.parametrize("script", sorted(set(_PY.findall(
    "\n".join(d.read_text(encoding="utf-8") for d in DOCS if d.exists())))))
def test_every_named_script_answers_help(script):
    done = subprocess.run([sys.executable, str(ROOT / script), "--help"], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stderr


def test_every_orchestrate_flag_is_parsed():
    cli = (ROOT / "orchestrator" / "cli.ts").read_text(encoding="utf-8")
    for flag in sorted(set(_FLAG.findall(_all_text()))):
        assert f'case "{flag}"' in cli, flag
```

Run: FAIL (the docs do not exist).

- [ ] **Step 2: Write the guide** — imperative, for an agent, exact paths and commands, a "Verify" checklist after every step, and a closing `## Never do` (never type or store a credential, never run `copilot /login` or `gh auth`, never push, never loosen `orchestrator/policy.ts` or `compare.py`, never run the pipeline in the repo root, never edit `docs/spec/**`). Sections: **1. Copilot Enterprise models** — the human logs in; `fnm exec --using=22 node.exe --experimental-strip-types scripts/dev/list_models.ts`; `.venv/Scripts/python.exe scripts/dev/set_models.py --default <id> …` (the owner's policy from `docs/handoff-copilot-models.md` §1); `orchestrate.ts --check-models --profile hosted`; the unverified write/SQL tool names and how the first hosted run settles them (§5 of that doc). **2. Snowflake access** — the grants from `docs/reference/snowflake-backend.md`; the human creates the sandbox database and a `connections.toml` entry; `--backend snowflake --connection <name>` for `validate_segment.py`, `validate_snowpark.py`, `validate_dbt.py`, `validate_workflow.py`; `deploy.py` dry-run, then `--execute` only when the human says so; `dbt-snowflake` is a human install. **3. Real Alteryx workflows** — `survey_corpus.py`, `plugin_map.py`, intake, `inject_outputs.py` + `AlteryxEngineCmd.exe`, `golden.producer: "alteryx"`, the run (from `docs/reference/real-workflows.md`). **4. The verification ladder** — (1) offline mock run reproduces the committed `workflows/` (pass: README §6 reproducibility diff empty after normalising), (2) hosted models on the samples (pass: every stage reached, every `validation.json` verdict equal to the mock run's), (3) real Snowflake sandbox on the samples (pass: `--backend snowflake` verdicts equal the DuckDB ones for every set, or each difference explained in writing), (4) one real workflow (pass: `validate_workflow.py` PASS on captured goldens), (5) a batch (pass: per-workflow terminal states, spend within budgets). **5. What has never been proven** — spec §9, the Snowflake backend, dbt-snowflake, hosted models, SDK tool names, compaction events live, real Alteryx engine output, the character-to-token estimate.

The guide opens with a short "First week" paragraph that links `docs/production-backlog.md` as the **first-week checklist**.

- [ ] **Step 3: `docs/production-backlog.md`** — the gaps only the company's production setting can close, written down and NOT built here. An opening paragraph says so, and that every version or number in it was copied from a command run at write time. One `## <item>` per gap, each with the four bold-led fields the test pins (**What is missing.** / **Why it matters.** / **What exists to build on.** with repo paths / **First concrete step.**):
  1. **Alteryx tool coverage** — `scripts/parsers/plugin_map.py` knows a few dozen plugin types and no In-Database tools; drive extension by `survey_corpus.py`'s plugin frequency table, in the order parser map → `cookbook/<tool>.md` → `scripts/dev/alteryx_sim.py`; builds on `scripts/survey_corpus.py`, `scripts/parsers/`, `cookbook/`, `tests/cookbook_examples/`. First step: run the survey over the real corpus and take the top ten unknown plugins.
  2. **Sources and sinks outside Snowflake** — file shares, Excel/CSV, ODBC databases, APIs, file outputs people or dashboards consume; intake's touchpoints already list every one (`scripts/intake_touchpoints.py`, `workflows/<wf>/intake/touchpoints.json`); the ingestion/unload plan (stages, `COPY INTO`) is the company's call. First step: export every touchpoint of the corpus with its format and owner.
  3. **Golden-data governance** — real goldens are production data, possibly personal data: run roots outside the repo, a guard against committing captured data (extend `tests/test_committed_workflows.py`'s scans and `.gitignore`), a join-preserving sampling method for large volumes; builds on `scripts/inject_outputs.py`. First step: agree where captured goldens may live and who may read them.
  4. **Parallel run and reconciliation before switching off Alteryx** — both versions on live data for several cycles, a reconciliation report per run; `compare.py`'s keyless compare can power it (`scripts/compare.py`, `scripts/lib/validation.py`). First step: pick one migrated workflow and schedule both for one cycle.
  5. **Scheduling** — Alteryx Server schedules are recorded in `manifest.json` (from `sample.json`'s `schedule`); generate Snowflake tasks with inter-workflow dependencies. First step: list the schedules and the table-level dependencies between workflows.
  6. **Environment promotion** — dev/test/prod, versioning, rollback, fitted to the company's release process; `scripts/deploy.py` targets one database and schema. First step: map the release process onto `deploy.py`'s dry-run output.
  7. **Continuous integration** — the repo has no CI pipeline; run pytest, the node suite and `tsc --noEmit` on every change (commands in `README.md` §4). First step: one CI job running the three.
  8. **Reproducible installs** — `requirements.txt` holds floors only; pin a lock or constraints file with the versions verified here. Take them from `.venv/Scripts/python.exe -m pip freeze` at write time; at plan time they were snowflake-snowpark-python 1.55.0, dbt-core 1.12.5, dbt-duckdb 1.11.0, duckdb 1.5.5, sqlglot 30.18.0, PyYAML 6.0.3, pandas 2.3.3, pytest 9.1.1 — the doc quotes the `pip freeze` lines, not this list. First step: add `constraints.txt` from `pip freeze` and install with `-c`.
  9. **Cost and status visibility** — session telemetry is designed but parked (`docs/superpowers/specs/2026-09-22-session-telemetry-design.md`, `docs/superpowers/plans/2026-09-22-session-telemetry.md`); a corpus-wide status board and a manual-migration queue (every `manifest.json`'s `status`/`reasons`, `manifest.metrics`). First step: execute the telemetry plan.
  10. **Model evaluation** — a repeatable benchmark on the samples comparing hosted models on first-pass PASS rate, fix iterations and tokens (`samples/`, `manifest.metrics`, `peakInputTokens`, `scripts/dev/set_models.py`). First step: run the seven samples per candidate model with `--runner copilot --profile hosted` in scratch roots and tabulate the manifests.
  11. **Prompt-injection tests** — workflow annotations and formula text flow into prompts (`scripts/prompt_context.py`, the task texts in `orchestrator/stages.ts`); test the policy layer (`orchestrator/policy.ts`, `orchestrator/test/policy.test.ts`) with a sample whose annotations try to steer the agent. First step: a sample whose annotation asks the agent to write outside its lane, and a test that the write is denied.

- [ ] **Step 4: Pointers** — `docs/handoff-copilot-models.md` opens with "Production hand-off in full: `docs/handoff-production.md`"; README §1 (targets section), §7 and §8 point at the guide, and §7 also at the backlog.

- [ ] **Step 5: Green** — `tests/test_handoff_production.py`, the hand-off scans in `tests/test_committed_workflows.py`, full suite.

- [ ] **Step 6: Commit**

```bash
git add docs/handoff-production.md docs/production-backlog.md tests/test_handoff_production.py docs/handoff-copilot-models.md README.md
git commit -m "docs: production hand-off guide for an agent and the production backlog; every path and command pinned"
```

---

### Task H: The bounded live test (controller task)

**Tier:** controller (needs the GPU and the local server; not dispatched) · **Depends on:** G, P4 (and F's inline context, W4's metrics)

**Files:** `docs/live-smoke-test.md` (append one dated section)

**Hard limits (the `task-16-addendum` limits, restated):** never authenticate (no `copilot /login`, no `gh auth`, no tokens; a login demand is a finding, `BLOCKED_ON_LOGIN`); no downloads; `llama-server` on `127.0.0.1` only; the pipeline runs only in a scratch root outside the repo; one server instance; the server is stopped at the end and the port checked free; a failing model is a reportable result — do not tune prompts or loosen the policy to make it pass. **Ruling R-H1 needed:** the addendum capped model time at ~25 minutes for its single workflow; this test has three — the plan proposes 25 minutes of model time per sample (75 in total), a sample's attempt ending the moment it exceeds its share.

- [ ] **Step 1: Serve** the model at the largest context that fits 15 GB (S5):

```powershell
pwsh -File scripts/dev/serve_model.ps1 -Context 262144 -CacheTypeK q4_0 -CacheTypeV q4_0
```

Then `curl http://127.0.0.1:8080/health` → `{"status":"ok"}` and `nvidia-smi --query-gpu=memory.used,memory.total --format=csv` recorded. Fallback on an allocation failure, or if total use crosses ~15 500 MiB: stop it, then `-Context 131072 -CacheTypeK q4_0 -CacheTypeV q4_0`.

- [ ] **Step 2: Scratch root** — copy `scripts mappings catalog .github cookbook docs/reference` into it (NOT `samples/`, so no canned answer key is under the root — the second live test's methodological note) and write `orchestrator.config.json` with the absolute venv python and `samplesDir` pointing at the repo's `samples/`.

- [ ] **Step 3: Per sample** (`wf_0001`, then `wf_0006`, then `wf_0007`), one attempt per stage:

```bash
.venv/Scripts/python.exe scripts/dev/build_samples.py seed --only <wf> --root "$SCRATCH" --samples "$REPO/samples"
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --only <wf> --runner copilot --profile local --no-interactive --stop-after intake
# intake parks WAITING_FOR_ANSWERS once plan.md exists; feed the recorded answers, then re-run the scripts (no new agent call)
.venv/Scripts/python.exe scripts/dev/answer_samples.py --root "$SCRATCH" --samples "$REPO/samples" --only <wf>
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --only <wf> --runner copilot --profile local --no-interactive --stop-after intake
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --only <wf> --runner copilot --profile local --no-interactive --stop-after analyze
# only if analyze is DONE (golden runs through the simulator):
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --only <wf> --runner copilot --profile local --no-interactive --stop-after translate
```

No `--from-stage` retries: a parked stage is the result.

- [ ] **Step 4: Stop** the server (`taskkill` by PID), confirm no `llama-server.exe` and no `LISTENING` on 8080.

- [ ] **Step 5: Record** in `docs/live-smoke-test.md` a section "Third live test — larger context, three targets, <date>": versions; the exact server command line and the load/VRAM lines from its log; per sample and stage: status, `manifest.reasons`, `manifest.metrics.<role>` (`toolCalls`, `lastMs`, `compactions`, `peakInputTokens`), wall time, the `toolName` values in `audit.jsonl`, any denial and its reason, whether inline context appeared in the task (audit), whether `plan.md`, contracts, `dbt/**` or `proc.py` were written; tokens/second from the server log.

- [ ] **Step 6: Calibrate** — for intake and the analyzer, the ratio `peakInputTokens / (characters of the prompt_context block ÷ 4)`, copied numbers only; say whether `segmentation.max_prompt_chars` and `analyzerBudgetChars` look conservative against it. Honest verdict per role and per target; "nothing in this test touched Snowflake or Alteryx".

- [ ] **Step 7: Commit**

```bash
git add docs/live-smoke-test.md
git commit -m "docs: third live test — 262k context, wf_0001/wf_0006/wf_0007 through intake, analyze and translate where reached"
```

---

### Task P5: The README architecture diagrams, redrawn last

**Tier:** standard · **Depends on:** every other task, H included · **Wave 11 (last)**

**Files:**
- Modify: `README.md` §1 "Architecture at a glance" (the three ```mermaid blocks at README.md:30, :87 and :125 and their lead-in sentences only)
- Create: `tests/test_readme_diagrams.py`

Mermaid is not installed in this repository and nothing may be downloaded, so the check is two-part: an offline structural lint that runs in the suite, and the controller's render check in a browser with Mermaid 11.

- [ ] **Step 1: Failing test** — `tests/test_readme_diagrams.py`:

```python
"""README §1's diagrams draw what the code does; this pins their content and a structural lint.
Rendering is checked by the controller in a browser with Mermaid 11 (Task P5 Step 4)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
_TYPES = ("flowchart ", "sequenceDiagram", "stateDiagram-v2")
MUST_SHOW = ("validate_segment.py", "validate_snowpark.py", "validate_dbt.py", "validate_workflow.py",
             "check_seams.py", "plan_batches.py", "stitch_analysis.py", "--backend snowflake",
             "compile_check.py", "dbt/", "procs/README.md", "chain-drift")


def _blocks() -> list[str]:
    return _BLOCK.findall((ROOT / "README.md").read_text(encoding="utf-8"))


def test_every_block_is_a_known_diagram_type_and_structurally_balanced():
    blocks = _blocks()
    assert len(blocks) >= 3
    for block in blocks:
        assert block.lstrip().startswith(_TYPES), block[:40]
        assert "\t" not in block
        for opening, closing in ("[]", "()", "{}"):
            assert block.count(opening) == block.count(closing), (opening, block[:60])
        assert block.count('"') % 2 == 0
        subgraphs = len(re.findall(r"^\s*subgraph\b", block, re.MULTILINE))
        ends = len(re.findall(r"^\s*end\s*$", block, re.MULTILINE))
        loops = len(re.findall(r"^\s*(loop|alt|opt|par|critical|rect)\b", block, re.MULTILINE))
        assert ends == subgraphs + loops, block[:60]


def test_the_diagrams_show_the_targets_the_chain_the_batches_and_the_backend_switch():
    text = "\n".join(_blocks())
    missing = [item for item in MUST_SHOW if item not in text]
    assert not missing, missing
```

Run: FAIL on the second test (`validate_dbt.py`, `validate_workflow.py`, … missing).

- [ ] **Step 2: Redraw** — keep the three diagrams and their roles, drawn from the code as it stands after every other task:
  - **Components** — the Python subgraph gains `compile_check.py (--target sql | snowpark | dbt)`, `validate_segment.py · validate_snowpark.py · validate_dbt.py → compare.py`, `validate_workflow.py (the stitched whole)`, `check_seams.py · plan_batches.py · stitch_analysis.py`, `prompt_context.py`, `lib/dbt_project.py → dbt (subprocess)`; a backend node `DuckDB double | Snowflake (--backend snowflake, named connection)` both validators point at; `FS` lists `dbt/`, `procs/README.md`, `validation_workflow.json`.
  - **One agent call** — unchanged flow plus the compaction branch: `session.compaction_complete → notes reminder (intake, analyzer, fixer)` and `assistant.usage → peakInputTokens`.
  - **Stages** — analyze shows `plan_batches.py → one call, or one call per batch → stitch_analysis.py → check_seams.py` with `seam-mismatch` parking; translate splits by `output_kind`: procedures (per segment: `validate_segment.py (sql) / validate_snowpark.py (snowpark)`, then `validate_workflow.py` with the one-fixer-round and `chain-drift` routes) and dbt (one loop for the whole project: `compile_check.py --target dbt → reviewer → validate_dbt.py`, `procs/README.md` instead of `master.sql`).
  The lead-in sentences say what changed and still say GitHub renders them inline.

- [ ] **Step 3: Green** — `tests/test_readme_diagrams.py`, the hand-off scans, full suite.

- [ ] **Step 4: Render check (controller)** — open the README's three blocks in a browser with Mermaid 11 (a Markdown preview that bundles Mermaid 11, or the Mermaid Live Editor with the blocks pasted in); every block renders without a parse error. Record which renderer and version in the commit message body. Any parse error is fixed in `README.md` and the lint test gains the construct that broke.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_readme_diagrams.py
git commit -m "docs: README architecture diagrams show the three targets, the chained whole-workflow validation, the batched analyzer with its seam check and the Snowflake backend switch"
```

---

## Self-review

**1. Spec coverage (phase-2 requirement → task).**

| Requirement | Task |
|---|---|
| §4.3 dbt project layout, profile, sources, models, schema.yml, README, CTE comments, `procs/README.md` | A (contract + template), C (sample), D (`dbtReadme`, agents, reference) |
| §5.1 `compile_check --target dbt`: parse exit 0, a model per output, config ↔ write mode, schema.yml columns | A |
| §5.3 `validate_dbt.py`: per-set sandbox via `load_set`, `targets_before`, subprocess `dbt run`, logs git-ignored, per-segment reports with the same shape, domain FAIL naming models, idempotency | B (DV1, DV3, DV7) |
| §5.4 same `compare.py`, tolerances, verdicts | B, W1 |
| §6 `output_kind: dbt` → one translate iteration, reviewer/fixer per workflow naming models, per-segment statuses, no `master.sql` | D |
| §6 MockRunner `canned/dbt/**` + dbt broken variants in `broken.json` | D (mock), C (variants) |
| §6 policy lane `dbt/**`, `validate_dbt` on the validator list, dbt never run by an agent | D (DV5) |
| §7.3 `wf_0007` sample, `output_target: dbt`, goldens, canned, two broken variants, `target_check` proposes dbt | C |
| §8 agent files (analyzer, translator, reviewer, validator, fixer, documenter), copilot-instructions | D (+ W2 analyzer, W4 notes, F translator bullet) |
| §8 `cookbook/snowpark.md` (filter, formula, summarize, sort, python carry-over) and `cookbook/dbt.md` with a merge example | E |
| §8 `docs/reference/output-targets.md`, README targets section, sample table, deployment per kind | D, G |
| §8 `prompt_context.py` + orchestrator use for intake/analyzer | F |
| §8 live tests, 262144/q4_0 (fallback 131072), `wf_0001`/`wf_0006`/`wf_0007`, `docs/live-smoke-test.md` | H |
| §3.3 interactive `output_target` question | F |
| §9 statements in every relevant doc | D, E, P2, P4 |
| §10 tests: `test_validate_dbt`, compile-check dbt, Node dispatch/policy/prompt_context, e2e `wf_0007`, canned dbt shape, cookbook harness | A, B, C, D, E, F |
| Phase-1 residual: `savetxt`, `savez`, `savez_compressed`, `tofile`, `dump` refused and documented | F |
| Offline run for seven samples, committed `wf_0007`, `test_committed_workflows` | G |
| Scope P1–P4 | P1, P2, P3, P4 |
| Scope addition 3: `docs/production-backlog.md` (eleven items, four fields each, linked as the first-week checklist, path-pinned) | P4 |
| Scope addition 3: README Mermaid diagrams updated last, with a render check | P5 (lint in the suite; Mermaid 11 render by the controller) |
| Scope W1–W4 + risk register | W1, W2, W3, W4; "Risks and mitigations" |

Not placed: design §8's "`docs/handoff-copilot-models.md`: unchanged except a pointer" conflicts with the user's scope addition (P1 rewrites its §4 as done); the scope addition wins, noted here for the ruling.

**2. Placeholder scan.** The only deliberate "decide at implementation" points each carry a rule and a pinning test: the broken variants' observed classes (C Step 3, recorded after three identical runs, pinned by `test_broken_migration_fails_with_the_right_class`); the chain fixtures' exact values (W1 Step 1, the premise asserted in the tests); the committed samples passing as chains and having clean seams (W1, W2: stop-and-report findings, never silently fixed). No "TBD", no "add error handling", no test named without its assertion.

**3. Type and name consistency.** Used identically across tasks: `dbt_project.{PROFILE, DUCKDB_PATH_ENV, WORK_SCHEMA, COMPILE_SRC_SCHEMA, PROFILES_TEMPLATE, PROJECT_FILES, FAILED_STATUSES, DbtResult, DbtUnavailable, project_dir, model_name, model_relation, local_vars, sandbox_path, dbt_executable, run_dbt, expected_model_config, tail, bounded}`; `compile_check.compile_check_dbt`; `validate_dbt.validate_dbt(repo, wf_id, golden_sets=None, *, project_dir=None) -> dict[str, dict]`; `validation.{ordered_rows, chain_report, write_workflow_reports, clear_stale_workflow_reports}`; `validate_workflow.validate_workflow`; `handoff.{table_from_backend, table_from_snowpark, load_into_backend, load_into_snowpark, HandoffError}`; `types_map.duckdb_to_alteryx`; `prompt_context.{render, render_global, segment_detail_chars, DEFAULT_BUDGET_CHARS, TRUNCATION_MARKER}`; `check_seams.check_seams`; `plan_batches.{plan_batches, DEFAULT_ANALYZER_BUDGET_CHARS}`; `stitch_analysis.stitch`; `segment.{prompt_chars, DEFAULT_MAX_PROMPT_CHARS}`; TS `AgentCtx {segment, iteration, dbt, batch}`, `PolicyOptions {sandboxDatabases, dbtProject, analyzerBatch}`, `dbtReadme`, `dbtModelName`, `PROMPT_CONTEXT_CHARS`, `NOTES_ROLES`, `notesReminder`, `checkModels`, `configuredModels`; reasons `dbt: <reason>`, `chain-drift: <output>`, `chain: <seg> <stream> after 1 fixer round`, `seam-mismatch: <producer>-><consumer> <stream>`; files `workflows/<wf>/dbt_sandbox_<set>.duckdb`, `dbt/{compile_check.json, review.json, translation_notes.md, fix_log.md, logs/validate_<set>.log}`, `validation_workflow.json`, `segments/{batches.json, seams.json}`, `analysis/<batch>.md`, `notes/<role>.md`, `procs/README.md`.
