# Production backlog: the gaps only the company's setting can close

This is the first-week checklist that `docs/handoff-production.md` points to. Every item below is
written down and **not built** here: each one needs something this repository cannot have — the
company's data, systems, accounts, release process or decisions. Each item says what is missing, why
it matters, what already exists to build on (with repository paths) and a first concrete step. Every
version or number in this file was copied from a command run at write time (2026-09-23), and the
command is named next to it; re-run the command rather than trusting the number. Nothing here has run
against a real Snowflake account, a real Alteryx engine or a GitHub-hosted model.

## Alteryx tool coverage

**What is missing.** `scripts/parsers/plugin_map.py` maps 27 plugin types and no In-Database tool
(`.venv/Scripts/python.exe -c "import sys; sys.path.insert(0, 'scripts'); from parsers import plugin_map; print(len(plugin_map.PLUGIN_TYPES), [p for p in plugin_map.PLUGIN_TYPES if 'indb' in p.lower()])"`
printed `27 []`). A real corpus will use tools this map has never seen — the In-Database family,
spatial, reporting, predictive, connectors — and every one of them forces its workflow to tier T3
until someone maps it.

**Why it matters.** An unmapped plugin is `unknown`: the parser cannot say what it does, the survey
sends the workflow to T3, and the pipeline migrates nothing of it. Coverage decides how much of the
corpus the pipeline can take at all.

**What exists to build on.** `scripts/survey_corpus.py` (the corpus-wide plugin frequency table,
`docs/reference/real-workflows.md` §1–2), `scripts/parsers/plugin_map.py` (`PLUGIN_TYPES`,
`TARGET_CLASS`), `scripts/parsers/ext/README.md` (the parser-recovery extension contract for a
third-party plugin), `cookbook/index.md` and the per-tool pages, `tests/cookbook_examples/` (an
executable example per page), `scripts/dev/alteryx_sim.py` (only where a sample needs simulated data),
`tests/test_survey_corpus.py`.

**First concrete step.** Run the survey over the real corpus
(`docs/handoff-production.md` §3.1), take the ten most frequent unknown plugins from its frequency
table, and extend them in the order parser map → `cookbook/<tool>.md` with an executable example →
simulator only if a test needs it, re-running the survey after each.

## Sources and sinks outside Snowflake

**What is missing.** Alteryx workflows read and write file shares, Excel and CSV files, ODBC
databases and APIs, and produce files that people and dashboards consume. The pipeline migrates only
what lives in Snowflake: every input must already be a Snowflake table and every output becomes one.
How each outside source gets into Snowflake (stages, `COPY INTO`, a connector, a replication tool) and
how each file output is still delivered (an unload, a share, a report) is not decided here.

**Why it matters.** A migrated procedure whose input is still a CSV on a file share has nothing to
read, and a consumer who opened an Excel file every morning gets nothing. Deliberately, no role agent
may reach an external location (the SQL policy denies external `COPY INTO` targets and external
stages as `external-location`), so this has to be designed and run by people.

**What exists to build on.** Intake lists every touchpoint of a workflow with its format and path:
`scripts/intake_touchpoints.py` writes `intake/touchpoints.json`, and `scripts/intake_prompt.py`
records the Snowflake table for each (or `!` — "cannot exist in Snowflake"). `scripts/gen_source_views.py`
maps logical source names to the real tables. `cookbook/input.md` and `cookbook/output.md` describe
the Input and Output tools. `docs/reference/dag-contract.md` defines the touchpoint fields.

**First concrete step.** Bring every workflow of the corpus into a run root
(`docs/handoff-production.md` §3.3), run
`.venv/Scripts/python.exe scripts/parse.py <wf> --check --root "$RUN"` and then
`.venv/Scripts/python.exe scripts/intake_touchpoints.py <wf> --root "$RUN"` for each, and tabulate
every touchpoint's kind, format and path with the workflow's owner from the survey; hand that table
to whoever owns ingestion.

## Golden-data governance

**What is missing.** Real golden data is production data, and may be personal data. There is no rule
yet for where captured goldens live, who may read them, how long they are kept, whether they must be
masked, and how a large table is sampled without breaking its joins.

**Why it matters.** The pipeline's proof is a comparison against golden data, so goldens are copied
wherever a validation runs. Committed by mistake, they leak production data into a repository.
Sampled naively (each table on its own), they lose the rows that join, and a correct translation
fails — or a wrong one passes on too little data.

**What exists to build on.** `scripts/inject_outputs.py` (the capture and import), `scripts/lib/typed_csv.py`
and `scripts/lib/yxdb.py` (the golden formats), run roots outside the checkout
(`docs/handoff-production.md` §0.3), `.gitignore`, and the scans in `tests/test_committed_workflows.py`
that already walk every tracked file for machine paths and login names — the natural place for a guard
against committed captured data.

**First concrete step.** Agree with the data owner where captured goldens may live and who may read
them; then add a scan to `tests/test_committed_workflows.py` that fails when a tracked `golden/` CSV
belongs to a workflow that is not one of the samples.

## Parallel run and reconciliation before switching off Alteryx

**What is missing.** No workflow has run side by side with its Alteryx original on live data. There
is no reconciliation report per run and no rule for how many clean cycles retire an Alteryx
workflow.

**Why it matters.** Golden sets are a handful of snapshots; live data brings the cases no one thought
to capture. Switching Alteryx off on the strength of four golden sets alone is the risk this backlog
exists to avoid.

**What exists to build on.** `scripts/compare.py` (the same verdict and difference classes the
validators use, including the keyless nearest-match compare, `tests/test_compare_keyless.py`),
`scripts/lib/validation.py`, and the DDL templates `snowflake/01_ops_tables.sql` (`OPS.RUN_LOG`,
`OPS.RECON_RESULTS`), `snowflake/02_shadow_table_template.sql`,
`snowflake/03_reconciliation_task_template.sql` and `snowflake/04_alerts.sql` — never executed
anywhere.

**First concrete step.** Pick one workflow that passed rung 4 of the ladder, deploy it to a shadow
table, and schedule both it and the Alteryx original for one cycle; compare the two outputs with
`scripts/compare.py` and record the verdict.

## Scheduling

**What is missing.** Alteryx Server schedules are not read from anywhere. For the samples,
`scripts/dev/build_samples.py` copies `sample.json`'s `schedule` into `manifest.json`
(`source.server_schedule`); a real workflow's manifest gets nothing. Nothing generates Snowflake
tasks, and nothing knows that one workflow's output is another's input.

**Why it matters.** A migrated procedure that nobody calls on time does not replace the Alteryx job,
and one that runs before its upstream workflow has written reads yesterday's data.

**What exists to build on.** `source.server_schedule` in every sample manifest (for example
`workflows/wf_0001/manifest.json`), each workflow's `procs/master.sql` (one call runs the whole
workflow), `snowflake/03_reconciliation_task_template.sql` (a Snowflake task template, with an
`AFTER`-dependency alternative), `scripts/deploy.py`, and every workflow's `intake/mappings.yaml`
(sources and outputs under their Snowflake names — the table-level dependency graph).

**First concrete step.** Export the Alteryx Server schedules for the corpus, and list the
table-level dependencies between workflows by joining each workflow's outputs with every other
workflow's sources in their `intake/mappings.yaml` files.

## Environment promotion and rollback

**What is missing.** `scripts/deploy.py` deploys one workflow into one database and schema. There is
no dev → test → prod promotion, no versioning of deployed procedures and no rollback: every procedure
is `CREATE OR REPLACE`, and the previous version survives only if someone saved its DDL first.

**Why it matters.** A migration that cannot be rolled back has to be right the first time in
production. The company's release process, not this repository, decides who deploys what, where and
when.

**What exists to build on.** `scripts/deploy.py` (dry run by default, refuses anything not
`VALIDATED` with a passing chain report), `scripts/gen_source_views.py` (the per-environment source
views), `docs/reference/snowflake-backend.md` §4–5 (the deployment and its grants, the `GET_DDL`
backup), `snowflake/05_roles.sql` (the program spec's `MIGRATION_CI` / `MIGRATION_RUN` roles, to be
reconciled with §5's validation and deploying roles), `tests/test_deploy.py`.

**First concrete step.** Take `deploy.py`'s dry-run output for one validated workflow and map every
statement in it onto the company's release process: which environment, which role, which approval,
and where the previous DDL is kept.

## Continuous integration

**What is missing.** The repository has no CI pipeline: nothing runs the tests on a change (there is
no workflow file under the .github directory, only the agents and `copilot-instructions.md`).

**Why it matters.** Every guarantee this repository makes is a test: the policy lanes, the
compile checks, the parity of every sample, the hand-off scans. Without CI, a change that breaks one
is found only when someone happens to run the suites.

**What exists to build on.** The three commands in `README.md` §4 (pytest, the node suite,
`tsc --noEmit`), `pyproject.toml` (pytest configuration), `package.json` and `tsconfig.json`,
`requirements.txt`. The suites need no account, no login and no network by design: every external
system is a local double or a fake. The whole pytest suite ran in 6 min 59 s on the build machine
(the last line of `.venv/Scripts/python.exe -m pytest` on 2026-09-23: `1908 passed in 419.28s (0:06:59)`).

**First concrete step.** One CI job, on the company's runner, that installs from the lock file (next
item) and runs the three commands from `README.md` §4, failing on any failure or skip.

## Reproducible installs

**What is missing.** `requirements.txt` holds lower bounds only (`duckdb>=1.4`, `dbt-core>=1.12`, …),
so a fresh install today can resolve different versions from the ones every test here ran on. The
Node side is locked (`package-lock.json`, installed with `npm ci`); the Python side is not.

**Why it matters.** dbt, DuckDB, sqlglot and the Snowpark local-testing mock each change behaviour
between releases, and several documented facts here are pinned to one version (the two Snowpark mock
limits hold for 1.55.0; the dbt catalog-naming rule was measured on dbt-duckdb 1.11.0).

**What exists to build on.** `requirements.txt`, `package-lock.json`, and these lines of
`.venv/Scripts/python.exe -m pip freeze` on the build machine (Python 3.14.2, from
`.venv/Scripts/python.exe --version`):

```
dbt-adapters==1.24.5
dbt-core==1.12.5
dbt-duckdb==1.11.0
duckdb==1.5.5
numpy==2.5.3
pandas==2.3.3
pyarrow==23.0.1
pytest==9.1.1
PyYAML==6.0.3
snowflake-connector-python==4.7.5
snowflake-snowpark-python==1.55.0
sqlglot==30.18.0
```

**First concrete step.** Write `constraints.txt` from the full output of
`.venv/Scripts/python.exe -m pip freeze`, commit it, and install with
`.venv/Scripts/python.exe -m pip install -r requirements.txt -c constraints.txt`; add `dbt-snowflake`
there too once the human has chosen its version (the dbt-snowflake item below).

## Cost and status visibility

**What is missing.** Spend is visible only per workflow and per role, as tool calls, durations,
compactions and peak input tokens in each `manifest.json` (`manifest.metrics.<role>`: `toolCalls`,
`lastMs`, `compactions`, `peakInputTokens`); reports carry `credits: null`. There is no token or AI-unit
accounting, no spend cap beyond the tool-call budget, no cost report, no corpus-wide status board and
no queue of the workflows that need a person.

**Why it matters.** Hosted models are billed; a corpus of hundreds of workflows needs a number before
it starts and a report after. The workflows parked at `NEEDS_HUMAN`, `MANUAL`, `QUARANTINED` or
`BLOCKED` are the migration team's actual work list, and today they are scattered across manifests.

**What exists to build on.** The session-telemetry design and its plan, approved and parked:
`docs/superpowers/specs/2026-09-22-session-telemetry-design.md` and
`docs/superpowers/plans/2026-09-22-session-telemetry.md` (token and AI-unit figures from the SDK's
event stream, per-workflow spend caps with a kill switch, and a cost-report script). What is already
recorded: `orchestrator/hooks.ts` and `orchestrator/runner.ts` (metrics per role, including the
compaction count and peak input tokens added later), `orchestrator/manifest.ts`, and every
`manifest.json`'s `status` and `reasons`. The status table in `docs/handoff-production.md` §4 prints
them for one run root.

**First concrete step.** Reconcile the telemetry plan with what is already recorded (it predates the
`compactions` and `peakInputTokens` fields), then execute it.

## Model evaluation

**What is missing.** No hosted model has been measured on this pipeline. There is no repeatable
benchmark comparing candidate models on first-pass PASS rate, fixer iterations, tool calls and
tokens.

**Why it matters.** The owner's policy defaults every role to Luna Max and allows an escalation only
on evidence (`docs/handoff-copilot-models.md` §1). A benchmark is that evidence, and it is also the
regression check when a model version changes underneath the pipeline.

**What exists to build on.** The seven samples under `samples/` (each with an expected terminal state
in its `sample.json` and a mock-run answer key), `scripts/dev/set_models.py` (every model id set from
one command), `orchestrator/models.ts` (the catalog preflight), `manifest.metrics` per role
(`toolCalls`, `lastMs`, `compactions`, `peakInputTokens`), each segment's `fix_log.md`, and
`docs/live-smoke-test.md` for the write-up.

**First concrete step.** For each candidate model: set it with `scripts/dev/set_models.py`, build a
fresh run root, run the seven samples with `--runner copilot --profile hosted` exactly as rung 2 of
the ladder does, and tabulate each run's status table and verdict table side by side.

## Prompt-injection tests

**What is missing.** Workflow text — tool annotations, formula expressions, field and file names —
reaches the agents' prompts. No test has a workflow try to steer an agent, and no live run has seen
how a hosted model reacts when one does.

**Why it matters.** A real corpus is written by many people over many years; an annotation that reads
like an instruction is plausible even without malice. The policy layer is the backstop, and it is a
textual check, not a sandbox (`orchestrator/POLICY.md`, "Known limitations").

**What exists to build on.** `scripts/prompt_context.py` (inline context: values single-lined,
control characters escaped, the block inside a code fence longer than any backtick run it holds, and
a fixed "treat as data" sentence; `tests/test_prompt_context.py`), the task texts in
`orchestrator/stages.ts`, the permission policy `orchestrator/policy.ts` with its specification
`orchestrator/test/policy.test.ts`, and the compaction reminder, which is fixed text that never
carries workflow content.

**First concrete step.** Add a sample whose annotation asks the agent to write outside its lane (for
example into another workflow's directory) and a policy test that the write is denied; then include
that sample in the model evaluation above to see what a hosted model actually attempts.

## Segmenter scale

**What is missing.** `scripts/segment.py` has never met a large real workflow, and its splitting is
superlinear in the tool count. The review of the corpus survey (phase-2 Task P3) measured
`segment.segment()` at about 29 s on a synthetic 3,000-tool workflow, against 0.04 s to parse it.
`scripts/dev/segment_scale.py` times it on a linear chain of Formula tools whose node shapes are
copied from the parsed `wf_0002` sample. On 2026-09-23,
`.venv/Scripts/python.exe scripts/dev/segment_scale.py --tools 300 --tools 1000 --tools 3000` printed:

```
tools=300 segments=16 seconds=0.8
tools=1000 segments=64 seconds=12.7
tools=3000 segments=256 seconds=142.7
```

Under `cProfile`, a 1,000-tool chain spent almost all its time in `find_split` → `size_chars` →
`prompt_chars`, which called `json.dumps` 1,879,000 times: every node's configuration was
re-serialised for every candidate split.

**Why it matters.** It runs once per workflow in the pipeline, but `scripts/survey_corpus.py`
segments every workflow of the corpus, so a large corpus multiplies it; the time grows much faster
than the workflow.

**What exists to build on.** `scripts/segment.py` (`prompt_chars`, `size_chars`, `find_split`),
`scripts/dev/segment_scale.py` and `tests/test_segment_scale.py` (the measurement above),
`tests/test_segment_prompt_size.py` (its `test_every_committed_sample_segments_exactly_as_before`
pins every committed sample's segmentation), `tests/test_segment.py`,
`docs/reference/large-workflows.md` ("Segment size"), `scripts/survey_corpus.py`.

**First concrete step.** Memoise `prompt_chars` per node for one `segment()` call, re-run
`scripts/dev/segment_scale.py` with the same three sizes and time the survey's largest real
workflows, and keep every committed sample's segmentation byte-identical under the existing tests.

## The dbt-snowflake adapter

**What is missing.** `dbt-snowflake` is not installed and not in `requirements.txt`
(`.venv/Scripts/python.exe -m pip freeze` lists `dbt-core==1.12.5`, `dbt-adapters==1.24.5` and
`dbt-duckdb==1.11.0`, and no `dbt-snowflake`). The `snowflake` output of the one fixed dbt profile —
key-pair or browser authentication through `SNOWFLAKE_*` environment variables — has never run.

**Why it matters.** Without it, a dbt workflow can be validated only on DuckDB:
`validate_dbt.py --backend snowflake` and `deploy.py --execute` for a dbt workflow both stop with
exit 2 naming the missing package. dbt-duckdb's `merge`, types and case folding are not Snowflake's
(`docs/reference/output-targets.md` §6).

**What exists to build on.** `scripts/lib/dbt_project.py` (`PROFILES_TEMPLATE`, `run_dbt` — the one
place dbt is invoked), `scripts/validate_dbt.py` (`--backend snowflake`), `scripts/deploy.py` (the dbt
deployment command), `docs/reference/output-targets.md` §5 (the environment variables),
`tests/test_deploy.py`, and the sample `samples/wf_0007`.

**First concrete step.** The human picks the `dbt-snowflake` release compatible with `dbt-core`
1.12, installs it into `.venv`, pins it in `constraints.txt`, and runs the dbt sample on the sandbox
(`docs/handoff-production.md` §2.4 and rung 3).

## dbt hooks that touch another table

**What is missing.** On the dbt target a PreSQL/PostSQL becomes a `pre_hook`/`post_hook` of the target
model, and a hook may only be ONE `DELETE`, `UPDATE`, `INSERT` or `TRUNCATE` against the model's own
table (`dbt:hook_sql`, final fix wave C1): dbt runs hook SQL as written, before and after the model,
so anything wider would run unchecked during local validation. `scripts/target_check.py` still counts
any single `DELETE`/`UPDATE`/`INSERT`/`TRUNCATE`/`CALL` as plain, so a workflow whose PreSQL touches
another table, or CALLs a procedure, is proposed for dbt and then parks at translate (`dbt:hook_sql`,
recorded as `needs_human` by the translator) instead of going to the SQL target from the start.

**Why it matters.** Every such workflow costs a translate attempt and a human's routing decision.

**What exists to build on.** `scripts/target_check.py` (`_plain`, the `presql_not_plain` blockers),
`scripts/lib/dbt_surface.py` (`hook_problems`, the exact rule a hook must meet),
`tests/test_target_check.py`, `tests/test_dbt_surface.py`.

**First concrete step.** Have `target_check.py` block dbt with `presql_not_plain` whenever
`dbt_surface.hook_problems` would refuse the PreSQL/PostSQL rewritten against `{{ this }}` (a table
other than the Output tool's own, or a `CALL`), so such a workflow is routed to procedures by
`target_check` itself.

## The dbt surface gate's strictness

**What is missing.** The closed-surface gate that runs before dbt ever starts
(`scripts/lib/dbt_surface.py`, final fix wave C1) is fail-closed, and the adversarial re-review of it
found no bypass, but it also refuses some dbt that would be safe: `var( 'src_schema' )` written with
spaces inside the parentheses, a dash in a model file name, and a model calling a function that
sqlglot's DuckDB dialect does not know (for example `iff`). Model SQL is also parsed through DuckDB's
`json_serialize_sql`; if that function were missing, every model would be refused. Two limits are
disclosed rather than closed: the gate reads the project once, just before dbt starts, so a process
writing into the project at that moment could race it (nothing does in the orchestrated flow), and
NTFS alternate data streams and hard links are not detected (dbt reads only a file's main stream, and
the agent tools cannot create either).

**Why it matters.** A real translator that meets one of these refusals spends fixer iterations on a
construct that was harmless, and may park the workflow for a human. Loosening the gate carelessly
would reopen the hole it closed: an agent-written dbt project runs on the validation host.

**What exists to build on.** `scripts/lib/dbt_surface.py` (the rules and their check names),
`scripts/compile_check.py` (`compile_check_dbt`), `scripts/lib/dbt_project.py` (`run_dbt`, which calls
the gate first), and `tests/test_dbt_surface.py` (every attack the gate refuses, as a test).

**First concrete step.** During the pilot, collect every `dbt:model_sql`, `dbt:model_jinja`,
`dbt:yaml` and `dbt:surface` refusal from real translations. For each one that refused safe dbt, allow
exactly that construct with a new test, and keep every existing attack in `tests/test_dbt_surface.py`
failing.

## Golden capture for database targets

**What is missing.** For a database Output tool that appends or merges (update/insert), or runs
PreSQL or PostSQL, the validators compare the target's state AFTER the write and load its state
BEFORE the run from `golden/targets_before/<set>/<LOGICAL>.csv` (`scripts/load_golden.py`;
`sim_output` in `scripts/dev/alteryx_sim.py`), while `scripts/inject_outputs.py` captures only the
stream that feeds the Output tool. Nothing captures either table state. (Recording an imported set in
`manifest.json`'s `golden_sets`, the other half of this item when it was first written, is fixed:
`--import-set` does it.) `docs/handoff-production.md` §3.5 gives the manual procedure.

**Why it matters.** A workflow that appends to or merges into a table cannot be validated honestly
without both states: its golden output would be the wrong thing.

**What exists to build on.** `scripts/inject_outputs.py` (capture points, `import_captures`),
`scripts/load_golden.py` (how a set is loaded, `targets_before` included), `scripts/lib/typed_csv.py`
(CSV plus `.schema.json` sidecar), `scripts/dev/alteryx_sim.py` (the reference semantics of a
database write), `tests/test_inject_outputs.py`, `orchestrator/stages.ts`.

**First concrete step.** For each appending or merging database output in the pilot, agree with its
owner how the target table is snapshotted before and after the Alteryx run (a table export, or a
second instrumented Input tool reading the target), then teach `inject_outputs.py` to import those
two snapshots as `targets_before` and as the golden output, with a test in
`tests/test_inject_outputs.py`.
