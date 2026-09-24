# Production hand-off: the guide for the agent that takes this pipeline into the company

This guide is written for an AI coding agent working in the company's own copy of this repository,
next to a human employee who holds the access. A person can follow it too. Follow it literally, in
the **execution order** below, not in reading order: Parts 1–3 are procedures, and the verification
ladder in §4 decides when each of their steps runs. Every path, script, flag and file it names is
pinned by `tests/test_handoff_production.py`. If a command here does not work as written, the guide
is wrong: stop, report it, and fix the guide in the same change as the cause.

**Where this repository stands.** Nothing in it has run against a real Snowflake account, a real
Alteryx engine or a GitHub-hosted Copilot model. The committed `workflows/` trees are the product of
one offline run. In that run the eight pipeline agents were replayed from hand-written answers
(`--runner mock`), the golden data came from a model of Alteryx (`scripts/dev/alteryx_sim.py`), and
every SQL statement ran on DuckDB. The live tests in `docs/live-smoke-test.md` drove the real Copilot
SDK against a small local model; no hosted model has run. §4 is the order in which to prove the rest,
and §5 lists everything that has never been proven.

## First week

Read this guide once from start to finish before you run anything. Then use
`docs/production-backlog.md` as the **first-week checklist**. It lists the gaps that only the
company's setting can close: tool coverage, sources outside Snowflake, golden-data governance,
parallel running, scheduling, promotion, CI, pinned installs, cost visibility, model evaluation,
prompt injection, segmenter scale, the dbt adapter and real golden capture for database targets. For
each gap it names the files to build on and a first concrete step. Parts 1–3 below connect the
pipeline to the company's systems: models, Snowflake and the real Alteryx corpus. Part 4 proves the
connection one rung at a time. Do not start a rung until every rung below it has passed and been
recorded.

## Execution order

Run exactly this sequence. Each item names the part or rung to carry out; do not start an item until
the one before it has passed and its evidence is recorded. The parts are not run in reading order:
some of their steps belong to a rung and say so at the top ("Run this only at rung …").

1. §0 — who runs what, the prerequisites and the first run root.
2. Rung 1 (§4) — the offline mock run reproduces the committed `workflows/`. No login, no network.
3. §1.1–1.4 — the human signs in, the live catalog, every model id in one command, the preflight.
4. Rung 2 (§4) — hosted models on the samples; §1.5 settles the SDK's tool names during it.
5. §2.1–2.5 — the grants, the human's named connection, the sandbox allow-list, dbt-snowflake, the
   real column catalog.
6. Rung 3 (§4) — the real Snowflake sandbox on the samples, with §2.6's commands.
7. §3.1–3.3 — survey the corpus, teach the parser, bring one workflow into its own run root.
8. Rung 4 (§4) — that one workflow, through §3.4–3.7.
9. Rung 5 (§4) — a batch, each workflow through §3.3–3.6, then §3.7.

`docs/production-backlog.md` runs alongside from the first day; none of its items blocks a rung.

## 0. Before anything: who runs what, and the run root

### 0.1 Who runs what

- **You, the agent.** You read. You change the repository through reviewed commits. You run the
  offline commands. You prepare every other command and hand it to the human. You read the results
  and write them down.
- **The human.** An employee with the company's access. They own the GitHub Copilot sign-in,
  `connections.toml` and every credential, and they decide every escalation.
- **The pipeline's role agents.** These are the eight roles in `.github/agents/` that
  `orchestrate.ts` starts with `--runner copilot`. `orchestrator/policy.ts` confines them. Their
  script calls may not carry `--root`, `--backend`, `--connection` or `--sandbox-database`; the policy
  denies these with `script-root` and `script-backend`. They may not run `dbt`. They may not write
  outside their lane. They may not send SQL that names an external location, such as a `COPY INTO`
  to a URL or an external stage (`external-location`). Never widen any of this.

**The rule: a step that needs a login or a credential is the human's.** That is:
- the Copilot sign-in, so `scripts/dev/list_models.ts`, `orchestrate.ts --check-models` and every
  `--runner copilot --profile hosted` run;
- a Snowflake connection, so anything with `--backend snowflake`, `--connection` or
  `--sandbox-database`, `deploy.py --execute` and the dbt-snowflake install;
- an Alteryx licence, so every Alteryx run.

Everything offline is yours: `--runner mock` runs, the scripts on local files, dry runs and the
tests. When a step says **Who: human**, print the exact command for the human and wait until they
say it has run. Then read what it wrote. Never run it yourself, even if your shell could.

### 0.2 Prerequisites and variables

The toolchain is the one in `README.md` §3: the Python 3.14 `.venv`, Node 22 through `fnm` (the
WinGet install lives at `C:\Users\<you>\AppData\Local\Microsoft\WinGet\Links\fnm.exe`), and the
Copilot CLI. Every command in this guide runs in Git Bash from the root of the repository checkout.

```bash
REPO="$(pwd -W 2>/dev/null || pwd)"   # the checkout in drive-letter form (C:/...): Node cannot resolve /c/...
RUN=C:/mig/runs/<run name>            # a run root: OUTSIDE the checkout, one per run
EVIDENCE=C:/mig/evidence/<run name>   # this run's saved evidence: OUTSIDE the checkout
```

Keep the run root's path short, as here (`C:\mig\runs\<name>`). In the third live test
(`docs/live-smoke-test.md`, "Third live test") the model mangled a scratch path of about 150
characters when it built absolute paths, and the policy correctly denied those reads. Since
Task L1 every task tells the model to use relative paths, and a refused read no longer parks the
stage by itself; it is counted against `budgets.maxReadDenialsPerSession` (README §6).

Run the three suites once so you know the checkout is green before you change anything:

```bash
.venv/Scripts/python.exe -m pytest
fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts
fnm exec --using=22 node.exe node_modules/typescript/bin/tsc --noEmit -p .
```

**Verify.**
- [ ] pytest ends with `passed` and no `skipped` and no `failed`. The node suite reports `# fail 0`.
      `tsc` prints nothing.
- [ ] `git status --porcelain` prints nothing.

### 0.3 Build a run root

The orchestrator and every script write only under `--root`. **Never run the pipeline in the repo
root.** Doing so rewrites `mappings/global.yaml`, overwrites the committed `workflows/` and leaves run
artefacts in the tree. A run root is a copy of everything a run reads:

```bash
mkdir -p "$RUN/docs" "$RUN/tests" "$EVIDENCE"
cp -r scripts mappings catalog cookbook .github config.json orchestrator.config.json pyproject.toml "$RUN/"
cp -r docs/reference "$RUN/docs/"
cp -r tests/parser_corpus "$RUN/tests/"
rm -f "$RUN/scripts/parsers/ext/acme_dedupe.py"
.venv/Scripts/python.exe - "$RUN" "$REPO" <<'EOF'
import json, pathlib, sys
run, repo = pathlib.Path(sys.argv[1]), sys.argv[2]
path = run / "orchestrator.config.json"
config = json.loads(path.read_text(encoding="utf-8"))
config["python"] = f"{repo}/.venv/Scripts/python.exe"
config["samplesDir"] = f"{repo}/samples"
config["parallelism"] = 1
path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
EOF
```

Why each piece is there:
- `scripts/`: the orchestrator runs every Python script with the run root as its working directory.
- The config's `python`: the run root has no `.venv` of its own, so the config names the checkout's
  interpreter by absolute path. The orchestrator runs its own script calls with it, and it puts that
  interpreter's directory first on `PATH` for every agent session (`sessionEnvironment` in
  `orchestrator/cli.ts`), so an agent's `python scripts/<name>.py …` is this interpreter, with the
  project's packages. The system `python` on `PATH` has none of them. This was verified live in the
  fourth live test (`docs/live-smoke-test.md`): the intake agent's two script calls succeeded
  although the system interpreter has neither `yaml` nor `duckdb`.
- `mappings/` and `catalog/`: intake's program-wide answers and its column catalog.
- `.github/`: the agents. `orchestrator/agents.ts` loads them from `<root>/.github/agents`. Without
  them, a Copilot session gets no custom agent at all.
- `cookbook/` and `docs/reference/`: the pages the agents are told to read.
- `tests/parser_corpus/` and `pyproject.toml`: the parser-recovery agent's regression suite.
- `config.json` and a COPY of `orchestrator.config.json`, never a hand-written one. `orchestrate.ts`
  fills every missing key from `DEFAULT_CONFIG` in `orchestrator/cli.ts`, so a hand-written config
  without a hosted profile routes every role to the placeholder id `gpt-5.6-luna`, not to the ids
  §1.3 writes.
- `samples/` stays out. `samplesDir` points at the checkout's copy. A live session in a root that holds
  `samples/` can read the canned answer key, as the second live test did (`docs/live-smoke-test.md`,
  "Notable (non-critical) observation").
- `acme_dedupe.py` is removed because it is a leftover of an offline run. A copy of it would let
  `wf_0005` skip the parser-recovery path it exists to test.
- `parallelism` is 1 until the spend per role is known.

**GitHub integration is off.** With it on, the orchestrator opens a GitHub issue with a workflow's
`intake/open_questions.md` as the body whenever intake waits for answers, and its `pr` stage runs
`gh pr create --fill` (`stageIntake` and `stagePr` in `orchestrator/stages.ts`). It is off unless
`github.enabled` is `true` in `orchestrator.config.json` or a run passes `--gh`. Off, the
orchestrator never invokes `gh` at all, not even to see whether it is installed: the stages log
`gh: disabled` and carry on. Leave it off. Turning it on publishes to GitHub, which needs the human's
approval. Keeping run roots outside every git work tree is still a good habit, and every orchestrate
command in this guide stops at `document` or earlier anyway.

**Verify.**
- [ ] `ls -A "$RUN"` lists `.github`, `catalog`, `config.json`, `cookbook`, `docs`, `mappings`,
      `orchestrator.config.json`, `pyproject.toml`, `scripts` and `tests`, and nothing else.
- [ ] `grep -A2 '"github"' "$RUN/orchestrator.config.json"` shows `"enabled": false`. The first
      orchestrate command prints `gh=disabled` in its opening line.
- [ ] `.venv/Scripts/python.exe -c "import json,sys; c=json.load(open(sys.argv[1], encoding='utf-8')); print(c['python'], c['samplesDir'], c['parallelism'], c['profiles']['hosted']['model'])" "$RUN/orchestrator.config.json"`
      prints the checkout's own interpreter and `samples` in drive-letter form, then `1`, then the
      hosted id from §1.3 (a placeholder until then). `test -f` on the printed interpreter succeeds.

## 1. Copilot Enterprise models

The owner's model policy in `docs/handoff-copilot-models.md` §1 is binding. In short:
- Every role defaults to Luna Max.
- The long-context tier (the 1M window) goes only to intake, analyzer, fixer, parser-recovery and
  validator, and always on the same model. It is never a way onto a pricier model.
- Nothing escalates automatically. A move to a pricier model is a human decision, taken after two
  failures on the long-context tier and recorded with the evidence.
- Reasoning effort is `low` for the documenter and reviewer and `medium` for every other role.
- Spend is measured before anything changes.

The four steps below put that policy into the repository.

### 1.1 The human signs in

**Who: human.** Run `copilot`, then `/login` inside it. Confirm that `/model` offers Luna Max.

You never run `/login` or `gh auth login`. You never read, copy, print or store a token.

**Verify.**
- [ ] The human confirms the sign-in is done.
- [ ] `git status --porcelain` shows no new file. No credential landed in the checkout.

### 1.2 Read the live catalog

**Who: human**, because the command uses the human's login.

```bash
fnm exec --using=22 node.exe --experimental-strip-types scripts/dev/list_models.ts > "$EVIDENCE/models.txt"
```

Read `$EVIDENCE/models.txt`. For each model it lists `id`, `name`, `context_window`, `billing_x`,
the supported reasoning efforts and `policy`. Decide the following as `docs/handoff-copilot-models.md`
§2 describes:
- Is Luna Max its own `id`, or is it Luna with reasoning effort `max`?
- Does the chosen id offer the long-context tier?

Use only ids from this file. Never type an id from memory or from the program spec.

**Verify.**
- [ ] The id you chose appears in `models.txt` with `policy` `enabled`. If it does not, the human
      enables it in the account's Copilot settings and repeats 1.2.
- [ ] Write in `$EVIDENCE/notes.md` which form Luna Max takes and the window listed for it.

### 1.3 Write every model id with one command

**Who: agent.** The command edits three places and contacts nothing. Run it with `--dry-run`
first, then run it again without `--dry-run`:

```bash
.venv/Scripts/python.exe scripts/dev/set_models.py --default <Luna Max id> \
  --long-context-roles intake,analyzer,fixer,parser-recovery,validator \
  --effort documenter=low --effort reviewer=low --dry-run
```

It writes `orchestrator.config.json`, `config.json` and the `model:` line of every
`.github/agents/*.agent.md` in one step, so the three never drift apart.

Each call REPLACES the per-role maps from its own flags. Always pass the full set of flags in a single
call. Without `--role`, the command removes `roleModels` from `orchestrator.config.json`, and every
role then runs on `profiles.hosted.model`. `loadConfig` in `orchestrator/cli.ts` takes each per-role
map from the file whole or not at all, and the built-in defaults carry no `roleModels`, so nothing
brings a per-role split back. `orchestrator/test/integration.test.ts` pins this: it runs the real
`set_models.py` on a copy of the committed configuration and checks every role.

**Verify.**
- [ ] `git diff --stat` names only `orchestrator.config.json`, `config.json` and files under
      `.github/agents/`.
- [ ] `grep -c '"roleModels"' orchestrator.config.json` prints `0`, and
      `grep -A1 '"hosted"' orchestrator.config.json` shows the Luna Max id as `"model"`.
- [ ] `.venv/Scripts/python.exe -m pytest tests/test_agents_config.py tests/test_set_models.py`
      passes. So do the node suite and `tsc` from §0.2.
- [ ] Commit locally, for example `chore: hosted model ids from the live catalog`. Never push.
- [ ] Every run root built before this step is stale for any run that calls a hosted model: build a fresh one
      (§0.3) for every such run from now on. Rung 1's root is the one kept on purpose: rung 3 reuses it for its
      reproduced trees, and rung 3 makes no hosted call, so the model ids that went stale here never matter there.

### 1.4 Check the ids against the catalog

**Who: human.** Run this from the checkout root:

```bash
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --check-models --profile hosted
```

It first prints one `effective <role>: <id>` line per role: the model that role's session will ask
for. Then it lists the live catalog through the SDK and checks every id that `orchestrator.config.json`
(merged with its defaults), the agent files and `config.json` configure for the hosted profile.

**Verify.**
- [ ] Each of the eight `effective <role>: <id>` lines shows the Luna Max id.
- [ ] Exit 0. The last line reads `orchestrate: every configured model id is enabled (<n> checked)`.
- [ ] Exit 1 prints a `<file + role>: <id> — missing` line or a `— disabled` line. Go back to 1.3,
      or have the human enable the model.
- [ ] Exit 2 means the catalog could not be listed. The human is not signed in (1.1).

### 1.5 Settle the SDK tool names on the first hosted run

**Run this only at rung 2**, during its first hosted session.

The tool names in `orchestrator/policy.ts` (`WRITE_TOOL`, `SQL_TOOL`) were first written from the SDK's
type definitions. The third live test (`docs/live-smoke-test.md`, "Third live test") observed the real
names against a local BYOK model: `view` {path, view_range}, `create` {path, file_text},
`powershell` {command, description}, `grep` {pattern, paths, output_mode, -n/-A/-C},
`glob` {pattern, paths} and `ask_user` {question, choices}. The write tool is `create`, which
`WRITE_TOOL` already recognises. The hosted models run through the same SDK, so rung 2 confirms these
names on them rather than discovering them:

1. After the first `--stop-after intake` run of rung 2, read
   `"$RUN/workflows/wf_0001/audit.jsonl"`. Note each line's `ev`, `tool` and argument keys, and
   above all the first write.
2. For every write or SQL tool name, or argument shape, that the policy did not recognise, tighten
   `WRITE_TOOL` / `SQL_TOOL` to recognise exactly that. An unrecognised name is audited as
   `ev: "unrecognized-tool"`. Add one test per observed name or shape to
   `orchestrator/test/policy.test.ts`.
3. Never loosen a deny to let a model pass. A write that the policy allowed but should not have
   allowed is a critical finding. Stop and report it.

**The SDK's own `sql` tool is not Snowflake.** The SDK ships a built-in tool named exactly `sql`: a
per-session SQLite store whose description tells the model to keep its todos there. `policy.ts`
refuses a tool named exactly `sql` with "the session's SQL todo store is not used here; keep your plan
in your notes file" (`SQL_TODO_TOOL`), an ordinary budgeted refusal, instead of judging it as
Snowflake SQL. **If the production Snowflake tool turns out to be named exactly `sql`, this rule must
change before rung 2**: until it does, every statement sent through that tool is refused as a todo.
Change the rule (`SQL_TODO_TOOL` in `orchestrator/policy.ts`, and its tests in
`orchestrator/test/policy.test.ts`) and have the human review the diff.

Tightening `WRITE_TOOL` / `SQL_TOOL` from observed evidence is the one change to
`orchestrator/policy.ts` this guide sanctions. The human reviews the diff before any re-run.

**`excludedTools`: confirm the names, on this runtime.** Live hardening, Task L9 (R1): every
session's `createSession` call also carries the SDK's `excludedTools` (`policy.ts`'s
`ALWAYS_EXCLUDED_BUILTIN_TOOLS`, prefixed `builtin:` by `sessionExcludedTools`) -- `web_fetch`,
`web_search`, `sql` and `write_agent` are never even offered, on top of `judge` still refusing each
of them outright by name (POLICY.md, item 9). That list was written from a mix of live evidence
(`web_fetch`: `task-L9-brief.md`, a documenter session parked on it) and the SDK's own generated
types (`web_search`: `session-events.d.ts`'s `AssistantServerToolProgressData.kind`). Corrected (L9
fix round 2 review): the runtime that parses `excludedTools` is the bundled LOCAL binary for BOTH
profiles, not a "hosted-runtime" surface with nothing local to check against -- `sql` and
`write_agent`'s existence, and an unrecognised name being a harmless no-op rather than a
`createSession` error, are now VERIFIED against that bundled runtime offline (POLICY.md's item 9 has
the evidence); only whether the HOSTED model's own session actually offers all four (never calls one
despite the exclusion) still needs confirming here, at rung 2:
1. After the first hosted run, read `audit.jsonl` for any `pre` line whose `tool` is `web_fetch`,
   `web_search`, `sql` or `write_agent` -- **not only `"ev":"unrecognized-tool"` lines**: only
   `web_fetch`/`web_search` produce that event when refused; `sql` is refused with its own
   `SQL_TODO_REASON` and `write_agent` with its own reason, so both are refused-but-never
   "unrecognized". If the SDK offered one of them (and the model called it) despite `excludedTools`,
   `ALWAYS_EXCLUDED_BUILTIN_TOOLS` did not name it as the runtime spells it; fix the spelling, not the
   policy rule that refused it.
2. If `createSession` itself rejected an `excludedTools` entry (a thrown error naming the option),
   the session that hit it ends `error` -- which PARKS the workflow (`NEEDS_HUMAN`) at that session,
   `error` is not retried -- but drop or fix the offending name in `ALWAYS_EXCLUDED_BUILTIN_TOOLS`
   before continuing, and note here which of the four this runtime does and does not recognise.

**Verify.**
- [ ] The node suite and `tsc` pass after the change.
- [ ] On a re-run, `audit.jsonl` shows the write allowed inside its lane.
- [ ] The new tests prove that a write outside the lane is still denied.
- [ ] The Snowflake tool is not named exactly `sql`, or `SQL_TODO_TOOL` was changed as above.
- [ ] `web_fetch`, `web_search`, `sql` and `write_agent` were confirmed as above (offered-and-called,
      or rejected by `createSession`) against the hosted runtime; any renamed or missing one is
      fixed in `ALWAYS_EXCLUDED_BUILTIN_TOOLS`.

## 2. Snowflake access

Read `docs/reference/snowflake-backend.md` first. It is the reference for everything in this part.

- Validation on the account runs in ONE sandbox database. Its `MIG_GOLDEN`,
  `MIG_GOLDEN_<WF>_<SET>`, `MIG_WORK` and `MIG_COMPARE` schemas are replaced (dropped and recreated)
  by every validation.
- The pipeline reaches the account only through the NAME of an entry in the human's own
  `connections.toml`.
- A validator's default is still DuckDB. Every Snowflake path is opt-in and run by the human.

### 2.1 The sandbox database, the roles and the grants

**Who: a Snowflake administrator** (a human). Give them `docs/reference/snowflake-backend.md` §5.
It holds the validation role, the deploying role and the grants each needs, under illustrative
names (`MIGDB_SANDBOX`, `MIGRATION_VALIDATOR`, `MIGRATION_DEPLOYER`, `MIGRATION_WH`). The
administrator adapts the names to the company's conventions. An organisation administrator must
accept the Anaconda terms before a Snowpark segment's `LANGUAGE PYTHON` procedure can be created.

The older templates under `snowflake/` (for example `snowflake/05_roles.sql`, with
`MIGRATION_AGENT`, `MIGRATION_CI` and `MIGRATION_RUN` from the program spec) were written before the
validators could reach Snowflake. `--backend snowflake` and `deploy.py` need the §5 grants.

**Verify.**
- [ ] The human runs `SHOW GRANTS TO ROLE <validation role>;` and saves the output to
      `$EVIDENCE/grants.txt`. It shows `USAGE` on the warehouse and `USAGE` and `CREATE SCHEMA` on the
      sandbox database, and nothing on any production database.
- [ ] The sandbox database holds nothing of value. Every validation replaces its schemas.

### 2.2 The human's named connection

**Who: human.** Add an entry for each role to their own `connections.toml`. The file lives outside
every repository: `~/.snowflake/connections.toml`, or the directory that `SNOWFLAKE_HOME` names. Use
key-pair authentication (`authenticator = "SNOWFLAKE_JWT"` and `private_key_file`) or SSO
(`authenticator = "externalbrowser"`). Never put a password in any file. There is a template in
`docs/reference/snowflake-backend.md` §1. The pipeline only ever receives the entry's name, through
`--connection <name>` or `MIG_SNOWFLAKE_CONNECTION`.

You never open, read or copy that file or a key.

**Verify.**
- [ ] `git ls-files | grep -iE "connections\.toml|\.p8$"` prints nothing.
      `ls -A "$RUN"` shows neither `connections.toml` nor a key file.

### 2.3 Allow-list the sandbox database

**Who: agent**, once **the human** has confirmed the database name in writing. A validator accepts a
sandbox database only when `policy.sandboxDatabases` in the run root's `orchestrator.config.json`
lists it, and every validation replaces that database's schemas. The list also bounds which
databases the role agents' SQL may name, so widening it is the human's decision. Add the confirmed
name to the run root's copy:

```bash
.venv/Scripts/python.exe - "$RUN/orchestrator.config.json" <SANDBOX_DB> <<'EOF'
import json, pathlib, sys
path, database = pathlib.Path(sys.argv[1]), sys.argv[2].upper()
config = json.loads(path.read_text(encoding="utf-8"))
databases = config.setdefault("policy", {}).setdefault("sandboxDatabases", [])
if database not in databases:
    databases.append(database)
path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
EOF
```

Then add the same name by hand to the `"sandboxDatabases"` list in the checkout's
`orchestrator.config.json`, so that every later run root inherits it. Commit that locally and never
push it. Never list a database that holds anything of value.

**Verify.**
- [ ] `grep -A3 sandboxDatabases "$RUN/orchestrator.config.json"` shows the name in upper case.
- [ ] The refusal of any database outside the list happens before anything connects (exit 2). It is
      pinned by `tests/test_snowflake_validators.py`
      (`test_a_sandbox_database_outside_the_policy_is_refused_before_anything_connects`), which
      passes in the full suite.

### 2.4 dbt-snowflake and the dbt profile's environment

**Who: human.** This step is needed only for a dbt workflow (`output_kind: "dbt"`, such as the one
seeded from `samples/wf_0007`).

`dbt-snowflake` is not installed and is not in `requirements.txt`. Adding it is the human's decision.
Pick the release that matches the installed `dbt-core` (read it with
`.venv/Scripts/python.exe -m pip freeze`), install it into `.venv`, and pin it in the constraints file
that `docs/production-backlog.md` asks for.

The dbt profile is the fixed template in `scripts/lib/dbt_project.py`. It reads `SNOWFLAKE_ACCOUNT`,
`SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_AUTHENTICATOR` (default
`externalbrowser`) and `SNOWFLAKE_PRIVATE_KEY_PATH` from the human's environment. The validator sets
`SNOWFLAKE_DATABASE` itself. None of these values is ever written into a file, and
`compile_check.py --target dbt` refuses a `profiles.yml` that contains one.

**Verify.**
- [ ] `.venv/Scripts/python.exe -m pip show dbt-snowflake` prints a version. That is the whole check.
      Do not probe the package by running `validate_dbt.py --backend snowflake`: it clears that
      workflow's reports before anything else. Without the package it stops with exit 2 and names
      it; that is expected.

### 2.5 The real column catalog, for intake

**Who: human**, with a read-only role. Intake proposes a Snowflake table for every input and output
from `catalog/columns.csv`, which is a hand-written stand-in. Run the export query in
`catalog/README.md` against the databases the migration cares about. Save the result as CSV with the
header `database,schema,table,column,data_type,row_count`. Then:

```bash
cp <the exported CSV> "$RUN/catalog/columns.csv"
```

This goes into run roots only. The checkout's stand-in feeds the sample tests.

**Verify.**
- [ ] `head -1 "$RUN/catalog/columns.csv"` prints exactly that header.
- [ ] In §3.4, `intake/touchpoints.json` proposes real tables.

### 2.6 The Snowflake-path commands

**Run this only at rung 3** (and at rung 4 for the real workflow). **Who: human, every one.** You
prepare them with the real names filled in, and you read what they write. There are two kinds.

**Validation**, which writes the same reports as the local run under `"$RUN/workflows/<wf>/"`:

```bash
export MIG_SNOWFLAKE_CONNECTION=<validation entry> MIG_SANDBOX_DATABASE=<SANDBOX_DB>
.venv/Scripts/python.exe scripts/validate_segment.py <wf> <seg> --root "$RUN" --backend snowflake
.venv/Scripts/python.exe scripts/validate_snowpark.py <wf> <seg> --root "$RUN" --backend snowflake
.venv/Scripts/python.exe scripts/validate_workflow.py <wf> --root "$RUN" --backend snowflake
.venv/Scripts/python.exe scripts/validate_dbt.py <wf> --root "$RUN" --backend snowflake
```

`--connection <entry>` and `--sandbox-database <DB>` override the two variables for a single run. Run
one validation at a time per sandbox database. Two validations in the same database destroy each
other's schemas, and nothing enforces this.

**Deployment**, of a workflow that is `VALIDATED` with a passing chain report (`deploy.py`
refuses anything else):

```bash
.venv/Scripts/python.exe scripts/gen_source_views.py <wf> --root "$RUN"
.venv/Scripts/python.exe scripts/deploy.py <wf> --root "$RUN" --database <DB> --schema <SCHEMA> --src-schema MIG_SRC_<WF>
.venv/Scripts/python.exe scripts/deploy.py <wf> --root "$RUN" --database <DB> --schema <SCHEMA> --src-schema MIG_SRC_<WF> \
    --connection <deploying entry> --execute
```

`gen_source_views.py` runs nothing: it prints the DDL that creates the schema
`<target_database>.MIG_SRC_<WF>`, one view per source under its logical name, where
`<target_database>` is `program.target_database` in the run root's `mappings/global.yaml`. The human
runs that DDL. Then pass that same database as `deploy.py --database`: the example `CALL` that
`deploy.py` prints passes `--database` as both the source and the target database, so
`--src-schema MIG_SRC_<WF>` then names the views the DDL created.

The `deploy.py` dry run connects to nothing, so you may run it and hand the printout to the human.

**`--execute` overwrites.** Every procedure is a `CREATE OR REPLACE PROCEDURE`, and nothing keeps a
history. Before every `--execute`, the human:
1. reads the dry run;
2. saves the current DDL of each procedure the workflow already has, with
   `SELECT GET_DDL('PROCEDURE', '<DB>.MIG_WORK.<WF>_<SEG>(VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR)')`;
3. only then runs it.

A deployment to anything other than the sandbox goes through the company's release process
(`docs/production-backlog.md`, "Environment promotion and rollback"). It never comes from an agent
session.

**Verify.** After each human run:
- [ ] Note the exit code: 0 PASS or done, 1 FAIL or refused, 2 usage error or crash (the traceback
      is redacted).
- [ ] Read the reports the run wrote.
- [ ] `grep -rn "PRIVATE KEY" "$RUN/workflows"` prints nothing.
- [ ] In any report or log, every credential-looking key is followed by `<redacted>`.

## 3. Real Alteryx workflows

The how-to is `docs/reference/real-workflows.md`. This part is its checklist, plus one gap that
`docs/reference/real-workflows.md` does not state (§3.5).

Use a run root of its own for real workflows (§0.3). The samples need the simulator, and real
workflows need `golden.producer: "alteryx"`.

### 3.1 Survey the corpus

**Who: agent.** The company's exports (`.yxmd`, `.yxwz` and `.yxzp` files, in any layout) sit in a
directory outside the checkout. The reports name real workflow files, so they go to `$EVIDENCE`
and are never committed:

```bash
.venv/Scripts/python.exe scripts/survey_corpus.py <export directory> --out "$EVIDENCE/survey.md" --json "$EVIDENCE/survey.json"
```

Add `--prefer dbt` if the organisation prefers dbt projects to procedures. The survey never runs
Alteryx and never touches `workflows/`.

**Verify.**
- [ ] Exit 0.
- [ ] `survey.md` has one row per workflow and the corpus-wide plugin frequency table.
- [ ] Copy into `$EVIDENCE/notes.md` the number of workflows, the number per proposed tier, and
      every `error` row.

### 3.2 Teach the parser the plugins that matter

**Who: agent**, as reviewed commits in the checkout. Work down the plugin frequency table
(`docs/reference/real-workflows.md` §2). For each plugin, in this order:

1. **Map it.** Add the plugin to `PLUGIN_TYPES` in `scripts/parsers/plugin_map.py`. If its target
   class is not `sql`, also add it to `TARGET_CLASS`. Mark every new entry
   `# verify against your Alteryx version`.
2. **Document it.** Write its `cookbook/<tool>.md` page (the translator and fixer take tool semantics
   only from the cookbook; `cookbook/index.md` links every page). Put an executable example under
   `tests/cookbook_examples/<tool>/`. The cookbook is read-only to the role agents. The
   `cookbook-curator` agent only proposes changes under `cookbook/proposals/`, and a human merges them.
3. **Simulate it, only if needed.** Change `scripts/dev/alteryx_sim.py` only when a sample or a test
   needs simulated golden data for the tool. Real workflows get real golden data (§3.5).

A third-party or custom plugin gets a parser-recovery extension instead (`scripts/parsers/ext/README.md`),
never a guessed mapping. `tests/corpus_fixtures/unknown_plugin.yxmd` shows the shape of a fixture.

**Verify.**
- [ ] The full pytest suite passes.
- [ ] Re-run §3.1. The plugin no longer appears among the unknown plugins, and the count of
      workflows forced to T3 by an `unknown` node drops.

### 3.3 Bring one workflow into the run root

**Who: agent.** Pick a pilot from the survey: tier T1, few segments, no unresolved macro and no dbt
blocker.

Give it an id of the form `wf_` plus digits that no sample uses, for example `wf_1001`. Upper-cased
without underscores, the id names every procedure (`MIG_WORK.WF1001_SEG_01`), so use only letters,
digits and `_`.

```bash
mkdir -p "$RUN/workflows/wf_1001/source"
cp "<export directory>/<the workflow>.yxmd" "$RUN/workflows/wf_1001/source/"
.venv/Scripts/python.exe - "$RUN/orchestrator.config.json" <<'EOF'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8"))
config["golden"] = {"producer": "alteryx"}
path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
EOF
```

Copy every `.yxmc` macro the workflow references to the same relative path that its
`<EngineSettings Macro="...">` attribute uses.

`scripts/parse.py` scrubs connection strings and passwords out of the XML when it parses. Nothing
scrubs comments or annotations.

**Verify.**
- [ ] `ls -R "$RUN/workflows/wf_1001/source"` shows the workflow and every macro.
- [ ] `grep -il "password" "$RUN/workflows/wf_1001/source"/*` prints nothing. If it prints a file,
      the human re-exports the workflow without the credential and you start the step again.
- [ ] `golden.producer` in `"$RUN/orchestrator.config.json"` is `alteryx`.
- [ ] `.venv/Scripts/python.exe scripts/parse.py wf_1001 --check --root "$RUN"` exits 0 before any
      model is called (the orchestrator's parse stage runs the same check again itself).
      Exit 1 means the parser's invariants failed, usually on an unknown plugin: go back to §3.2, or
      let §3.4's parser-recovery agent try.

### 3.4 Parse, intake and analyze

**Run this only at rung 4** (and, for each workflow of the batch, at rung 5). **Who: human** runs the
orchestrate command, which uses the Copilot login, and answers the intake questions. You prepare the
command and read what it writes.

```bash
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$RUN" --only wf_1001 --runner copilot --profile hosted --no-interactive --stop-after analyze
```

**The first pass parks intake at `WAITING_FOR_ANSWERS`.** `"$RUN/workflows/wf_1001/intake/open_questions.md"`
proposes a Snowflake table for every input and output. The human answers in one of two ways:
- in that file: tick `[x]` and fill in `Confirm or supply another:`;
- or at the interactive prompt, which asks the same questions:

```bash
.venv/Scripts/python.exe scripts/intake_prompt.py wf_1001 --interactive --user <their name> --root "$RUN"
```

`README.md` §5 is a worked transcript of that prompt. A bare Enter never accepts an unverified
output guess. After the human answers, run the same orchestrate command again.

The organisation's default target preference is `program.output_target` in
`"$RUN/mappings/global.yaml"` (`procedures` unless changed). Intake records a per-workflow answer
(`docs/reference/output-targets.md` §2).

Two outcomes stop this workflow:
- **parse `QUARANTINED`**: an unknown plugin the parser-recovery agent could not explain. Go back to §3.2.
- **translate `MANUAL`** (tier T3): the workflow goes to the manual queue. Pick another pilot.

**Verify.**
- [ ] `"$RUN/workflows/wf_1001/manifest.json"` shows `parse` `PARSED`, `intake` `READY` and
      `analyze` `DONE`.
- [ ] `segments/targets.json` exists, and every `segments/<seg>/contract.json` has a `target`.
- [ ] `segments/seams.json` has `"ok": true`.
- [ ] You can explain every `"decision": "deny"` line in `audit.jsonl`.
- [ ] `manifest.metrics` for each role (`toolCalls`, `lastMs`, `compactions`, `peakInputTokens`) is
      copied into `$EVIDENCE/notes.md`.

### 3.5 Capture golden data with real Alteryx

**Run this only at rung 4** (and, for each workflow of the batch, at rung 5). **Who: agent**
instruments and imports. **The human** runs Alteryx on a licensed machine, with their
own credentials for the workflow's inputs.

Choose a capture directory outside the checkout, and name it the way the machine with Alteryx sees
it: the instrumented copy writes its captures there. Then:

```bash
.venv/Scripts/python.exe scripts/inject_outputs.py wf_1001 --capture-dir <capture directory> --root "$RUN"
```

This writes `source/<name>.instrumented.yxmd` and `golden/capture_map.json`, and prints the
`AlteryxEngineCmd.exe` command line. The human runs that command against the INSTRUMENTED copy,
never the original. Then import the captures:

```bash
.venv/Scripts/python.exe scripts/inject_outputs.py wf_1001 --capture-dir <capture directory> --import-set normal --root "$RUN"
```

If the captures are copied somewhere else before the import, pass that directory to the import
instead: it finds each capture by file name (`import_captures` in `scripts/inject_outputs.py`).

Repeat the cycle (instrument, run, import), each time into a capture directory of its own, for every
set the workflow needs: `normal`, `period_end`, `empty` and `edge`.
Each set is a separate real run, with inputs chosen for that set.

Each successful import also records its set in `manifest.json`'s `golden_sets`, once and in import
order; a failed import records nothing. That list is what the golden stage (`stageGolden` in
`orchestrator/stages.ts`) reads before it lets the workflow move on. Import `normal` first: the
chain's idempotency re-run uses the first set.

**One gap you close by hand.** No script closes it, and it is in `docs/production-backlog.md`.
Some database outputs cannot be captured this way. This applies to a database Output tool whose
write mode is append, merge (update/insert) or truncate_append (Delete Data & Append: its `TRUNCATE`
needs the table to exist, and no golden set creates it otherwise), or that carries PreSQL or
PostSQL. For such an output
the validators need two things:
- the target's state BEFORE the run, from `golden/targets_before/<set>/<LOGICAL>.csv`
  (`scripts/load_golden.py`);
- the target's state AFTER the write, as the golden output (`sim_output` in
  `scripts/dev/alteryx_sim.py`).

The capture only taps the stream that feeds the Output tool. For such an output, stop and escalate.
The human exports the target table before and after that Alteryx run, as typed CSV with its
`.schema.json` sidecar (`scripts/lib/typed_csv.py`). The before-state becomes
`golden/targets_before/<set>/<LOGICAL>.csv`, and the after-state replaces
`golden/outputs/<set>/<tool id>.csv`. Until then the workflow cannot be validated honestly.

Real golden data is production data. It stays in the run root and never enters the checkout
(`docs/production-backlog.md`, "Golden-data governance").

**Verify.**
- [ ] `ls "$RUN/workflows/wf_1001/golden/inputs/normal" "$RUN/workflows/wf_1001/golden/outputs/normal"`
      shows a `.csv` and a `.schema.json` for every input and output capture. Each segment
      boundary's capture lands in `"$RUN/workflows/wf_1001/golden/intermediates/<seg>/normal/"`, one
      `<stream>.csv` per stream.
- [ ] `grep -A5 '"golden_sets"' "$RUN/workflows/wf_1001/manifest.json"` lists every imported set,
      with `normal` first.
- [ ] The human confirms that the row counts match the Alteryx run's own log.

### 3.6 Translate, validate and document

**Run this only at rung 4** (and, for each workflow of the batch, at rung 5). **Who: human** runs the
orchestrate command, which uses the Copilot login. You prepare it and read what it writes.

```bash
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$RUN" --only wf_1001 --runner copilot --profile hosted --no-interactive --from-stage golden --stop-after document
```

`--from-stage golden` reopens the `BLOCKED` golden stage, which a plain re-run never retries. It also
grants a fresh tool-call budget, and it logs that it did so.

**Verify.**
- [ ] `status.translate` is `VALIDATED` and every `segment_status` is `PASS`.
- [ ] `validation_workflow.json` has verdict `PASS` and `first_divergence: null`.
- [ ] `"$RUN/workflows/wf_1001/docs/migration.md"` has a `## Deployment` section.
- [ ] If the workflow parked at `NEEDS_HUMAN`, copy `manifest.reasons` into the evidence and stop.
      A `chain-drift` park is an approval decision for the human and never a fixer task
      (`docs/reference/large-workflows.md`, "The chain test").

### 3.7 Review

**Run this only at rung 4** (and for the batch at rung 5). **Who: human.** You prepare the list. The
human reviews:
- every translated artefact: `proc.sql`, `proc.py` or the dbt models;
- each `review.json`, `validation*.json` and `translation_notes.md`;
- the workflow's migration document, `"$RUN/workflows/wf_1001/docs/migration.md"`;
- every mapped table, with the workflow's owner.

The human approves or rejects each `needs_human` difference. `scripts/compare.py` never lets a row- or
schema-scope difference be approved. After that come the Snowflake validation (rung 4) and deployment
through the company's release process.

**Verify.**
- [ ] The human's sign-off is written in `$EVIDENCE/notes.md`, with the manifest and the commit it
      covers.

## 4. The verification ladder

Climb one rung at a time. Every rung has a pass rule and a list of what to record. Save evidence
under `$EVIDENCE`. Write the dated summary of every rung from 2 up into `docs/live-smoke-test.md`,
the repository's record of live runs. Copy every number from a log, a manifest or a report, never
from memory. Commit no row values, no credentials and no account identifiers.

Two helpers are used below. **The verdict table** prints every report's verdict:

```bash
.venv/Scripts/python.exe - "$RUN/workflows" > "$EVIDENCE/verdicts-<label>.txt" <<'EOF'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
reports = sorted(root.glob("*/segments/*/validation*.json")) + sorted(root.glob("*/validation_workflow*.json"))
for path in reports:
    print(path.relative_to(root).as_posix(), json.loads(path.read_text(encoding="utf-8"))["verdict"])
EOF
```

**The status table** prints each workflow's state, reasons and tool calls:

```bash
.venv/Scripts/python.exe - "$RUN/workflows" > "$EVIDENCE/status-<label>.txt" <<'EOF'
import json, pathlib, sys
for path in sorted(pathlib.Path(sys.argv[1]).glob("*/manifest.json")):
    m = json.loads(path.read_text(encoding="utf-8"))
    calls = sum((role or {}).get("toolCalls", 0) for role in (m.get("metrics") or {}).values())
    print(m.get("id"), m.get("tier"), json.dumps(m.get("status")), json.dumps(m.get("reasons") or {}), calls)
EOF
```

### Rung 1. The offline mock run reproduces the committed `workflows/`

**Who: agent.** It needs no model, no login and no network. Build a fresh run root (§0.3), then run
the sequence from `README.md` §6 against it:

```bash
.venv/Scripts/python.exe scripts/dev/build_samples.py seed --root "$RUN" --samples "$REPO/samples"
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$RUN" --runner mock --no-interactive --stop-after document
.venv/Scripts/python.exe scripts/dev/answer_samples.py --root "$RUN" --samples "$REPO/samples"
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$RUN" --runner mock --no-interactive --stop-after document
```

Then compare against the committed trees. Two fields change on every run and are removed from
both sides first: `updated_at` and `runtime_ms`.

```bash
CMP="$EVIDENCE/rung1"
mkdir -p "$CMP" && cp -r workflows "$CMP/committed" && cp -r "$RUN/workflows" "$CMP/run"
.venv/Scripts/python.exe - "$CMP/committed" "$CMP/run" <<'EOF'
import json, pathlib, sys
VOLATILE = {"updated_at", "runtime_ms"}
def strip(value):
    if isinstance(value, dict):
        return {key: strip(item) for key, item in value.items() if key not in VOLATILE}
    if isinstance(value, list):
        return [strip(item) for item in value]
    return value
for tree in sys.argv[1:]:
    for path in pathlib.Path(tree).rglob("*.json"):
        path.write_text(json.dumps(strip(json.loads(path.read_text(encoding="utf-8"))), indent=2) + "\n",
                        encoding="utf-8")
EOF
diff -r -x audit.jsonl -x '*.duckdb' -x '*.duckdb.wal' -x logs "$CMP/committed" "$CMP/run" > "$EVIDENCE/rung1.diff"
echo "diff exit $?"
```

**Pass.**
- `diff exit 0`, and `$EVIDENCE/rung1.diff` is empty.
- Every sample reaches the terminal state in `README.md` §6's table. That includes the dbt sample
  seeded from `samples/wf_0007`, which reaches `VALIDATED`.

**Record.**
- the checkout's commit (`git rev-parse HEAD`);
- the date;
- `.venv/Scripts/python.exe -m pip freeze`, saved to `$EVIDENCE/pip-freeze.txt`;
- `fnm exec --using=22 node.exe -v`;
- both passes' console summaries;
- the verdict table (label `mock`).

### Rung 2. Hosted models on the samples

**Who: human** runs each orchestrate command below, which uses the Copilot login. You build the run
root, seed it, feed the recorded answers (all offline) and read every result. Part 1 must be done,
and the preflight (§1.4) must have exited 0. Build a fresh run root and seed it with
`build_samples.py` as in rung 1. Then take one workflow and one stage at a time:

```bash
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$RUN" --only wf_0001 --runner copilot --profile hosted --no-interactive --stop-after intake
```

1. Read `audit.jsonl` and `manifest.json` (§1.5).
2. Feed the recorded answers:
   `.venv/Scripts/python.exe scripts/dev/answer_samples.py --root "$RUN" --samples "$REPO/samples" --only wf_0001`.
3. Re-run with `--stop-after analyze`, then with `--stop-after document`.
4. Repeat for the other samples, one `--only` at a time, from `samples/wf_0002` to `samples/wf_0007`.

Raise `parallelism` only after the spend per role is known.

**Pass.**
- Every sample reaches the same terminal state as in rung 1.
- The verdict table (label `hosted`) equals the `mock` one line for line.

A difference is a finding about the model, not about the validator: the validator's numbers never
come from an agent.

**Record.**
- the model ids that actually served, from the session's own reporting;
- for each role, `toolCalls`, `lastMs`, `compactions`, `peakInputTokens`, `readDenials`,
  `actDenials` and `severeDenials` from `manifest.metrics`;
- every park and its `manifest.reasons`;
- every `tool` name in `audit.jsonl`, with the write and SQL shapes (§1.5) and every denial;
- whether a `session.compaction_complete` event ever arrived, and whether the model re-read its
  notes file afterwards;
- `peakInputTokens` set against the character budgets: 16 000 for `PROMPT_CONTEXT_CHARS` and
  60 000 for the analyzer batch (`docs/reference/large-workflows.md`).

### Rung 3. The real Snowflake sandbox, on the samples

**Who: human** runs every command. You prepare the commands and read the results.

Before you start:
- Part 2 is done.
- `$RUN` is rung 1's run root, reused on purpose: it still holds its reproduced trees, and nothing in
  this rung calls a hosted model, so the model ids that went stale at §1.3 do not matter here. Its config
  lists the sandbox database (§2.3).
- Save the DuckDB verdict table (label `duckdb`) FIRST. The Snowflake runs overwrite the same report
  files.

**3a. The procedure syntax, on `wf_0001`.** This is the first test on a real account of the SQL form
every procedure uses:
- Each mapped table's name is built in a variable, as in
  `LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>';`.
- That name is then used as `IDENTIFIER(:<LOGICAL>_SRC)`.
- Every body is `$$`-delimited, `procs/master.sql` included.

This is Snowflake's documented grammar. `IDENTIFIER` takes one string literal, session variable,
bind variable or Snowflake Scripting variable (docs.snowflake.com/en/sql-reference/identifier-literal),
and a variable is named without a colon inside an expression
(docs.snowflake.com/en/developer-guide/snowflake-scripting/variables). None of it has run on an
account. Phase 2 replaced the earlier form, an expression written inside `IDENTIFIER`, which is not in
that grammar: `compile_check.py` now refuses it as `c4:identifier_expression`, and the policy's SQL
judge denies it.

```bash
export MIG_SNOWFLAKE_CONNECTION=<validation entry> MIG_SANDBOX_DATABASE=<SANDBOX_DB>
.venv/Scripts/python.exe scripts/deploy.py wf_0001 --root "$RUN" --database <SANDBOX_DB> --schema MIG_WORK --src-schema MIG_SRC_WF0001
.venv/Scripts/python.exe scripts/deploy.py wf_0001 --root "$RUN" --database <SANDBOX_DB> --schema MIG_WORK --src-schema MIG_SRC_WF0001 --execute
.venv/Scripts/python.exe scripts/validate_segment.py wf_0001 seg_01 --root "$RUN" --backend snowflake
```

The first command is the dry run. The human reads it before the second.
- The `--execute` creates `wf_0001`'s procedures in the sandbox. A syntax error in a body can appear
  here, at `CREATE`. It runs through the validation connection on purpose: the sandbox is the
  validation role's database, and the deploying role has no grant there
  (`docs/reference/snowflake-backend.md` §5).
- It also creates the master procedure, `WF0001_MASTER` from `procs/master.sql`. No rung CALLs a
  master: the validators run each segment themselves. A master's CALL chain first runs in a real
  scheduled run (`docs/production-backlog.md`, "Parallel run and reconciliation").
- `validate_segment.py` then creates and CALLs the segment procedure on every golden set, in fresh
  sandbox schemas, and judges each output there. This exercises the `LET` / `IDENTIFIER(:…)` form at
  run time, and `ALTER SESSION` inside a caller's-rights procedure.

If either step fails, stop. Record the redacted error. The fix is a change to the SQL form everywhere
it lives: `scripts/compile_check.py`, `orchestrator/policy.ts`, the translator agent, the cookbook
and the canned procedures, all together. The human rules on that change. It is never a local edit to
one procedure.

**3b. Every sample.** Run this only after 3a passes:

```bash
for wf in wf_0001 wf_0002 wf_0003 wf_0004 wf_0006; do
  for dir in "$RUN/workflows/$wf/segments"/seg_*/; do
    seg=$(basename "$dir")
    if test -f "$dir/proc.py"; then script=validate_snowpark.py; else script=validate_segment.py; fi
    .venv/Scripts/python.exe scripts/$script "$wf" "$seg" --root "$RUN" --backend snowflake
  done
  .venv/Scripts/python.exe scripts/validate_workflow.py "$wf" --root "$RUN" --backend snowflake
done
.venv/Scripts/python.exe scripts/validate_dbt.py wf_0007 --root "$RUN" --backend snowflake
```

The last line is the dbt sample (seeded from `samples/wf_0007`). It needs §2.4.

**Pass.**
- 3a succeeds: the deploy exits 0 and the validation reports `PASS` on every set.
- The verdict table (label `snowflake`) equals the `duckdb` one for every set, or each difference is
  explained in writing. The usual causes are type names from a real `DESCRIBE`, identifier case,
  rounding and row order.
- The Snowflake re-run perturbs row order more weakly than the local reversal does
  (`docs/reference/snowflake-backend.md` §3). The local run remains the order check.

**Record.**
- the connection's role and warehouse, but never the account locator or any secret;
- for each command, the exit code and the verdict;
- every difference and its explanation;
- in `docs/reference/snowflake-backend.md` §7, next to each item this rung exercised, the date and
  the evidence file.

### Rung 4. One real workflow

**Who: agent and human**, following Part 3 from start to finish for the pilot.

**Pass.**
- `validate_workflow.py wf_1001` gives `PASS` on the captured real goldens, locally: the orchestrator
  reached `VALIDATED` (§3.6).
- The human's
  `.venv/Scripts/python.exe scripts/validate_workflow.py wf_1001 --root "$RUN" --backend snowflake`
  gives the same verdict for every set.
- The review (§3.7) is signed off.

**Record.**
- the survey row;
- tier, output kind and segment count;
- `manifest.metrics` for each role;
- fixer iterations (`fix_log.md`);
- the verdict for each set, local against Snowflake;
- every `needs_human` decision and who took it;
- wall time.

No data values.

### Rung 5. A batch

**Who: human** runs each orchestrate command (the Copilot login), answers the intake questions and
reviews; you do the offline steps and read the results. Take five to twenty workflows from the
survey, T1 first. Each one goes through §3.3–3.6 exactly as the pilot did: bring it in, parse,
intake and analyze, capture its golden sets, then translate. Here is §3.4's command for the whole
batch at once; §3.6's `--from-stage golden` runs one workflow at a time, with `--only`, once that
workflow's captures are imported. Raise `parallelism` in the run root's `orchestrator.config.json`
only as far as rung 4's spend per role justifies.

```bash
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$RUN" --runner copilot --profile hosted --no-interactive --tier T1 --stop-after document
```

**Pass.**
- Every workflow ends terminal (`VALIDATED`, or `MANUAL` for tier T3) or parked with its reason in
  `manifest.reasons`. Two parks carry no reason by design and are to-do items, not failures: intake
  at `WAITING_FOR_ANSWERS` (the human still has questions to answer) and golden at `BLOCKED` with
  `golden.producer: "alteryx"` (captures not yet imported). The orchestrator exits 0 or 1 and never 2.
- No workflow's total `toolCalls` exceeds `budgets.maxToolCallsPerWorkflow`. A `budget` park is a
  finding.
- Every workflow that reaches `VALIDATED` also passes rung 4's Snowflake check.

**Record.**
- the status table (label `batch`);
- the verdict table;
- every park grouped by reason, which becomes the manual-migration queue;
- the total tool calls for each role against the budget.

## 5. What has never been proven

Everything in this section is unverified. Where the ladder can settle an item, it names the rung.

**The local doubles** (the output-targets design spec §9,
`docs/superpowers/specs/2026-09-22-output-targets-design.md`; `docs/reference/output-targets.md` §6):

- **The Snowpark Local Testing Framework.**
  - It implements only a subset of Snowflake's functions and types.
  - It has no `session.sql`.
  - It resolves no real `RUNTIME_VERSION` or `PACKAGES`.
  - It says nothing about performance.
  - **It runs the agent's `proc.py` as ordinary Python on the machine you run the validator on.** The
    self-test isolates that in a child process under a static gate and an audit hook
    (`scripts/lib/snowpark_sandbox.py`), both now allow-lists (the static gate accepts only the
    Snowpark/pandas surface the benign corpus uses; the hook is default-deny), which is defence in
    depth, not a boundary — it was attacked three times and hardened (live hardening L4 fix rounds
    2–5). On the company's own machines, run the validators (and the
    whole pipeline) inside the program spec's container, only `workflows/` writable and no network, or
    validate Snowpark procedures only on Snowflake (rung 3), where the code runs in Snowflake, not on
    the host. Same for the parser-recovery agent's `scripts/parsers/ext/*.py`, which `scripts/parse.py`
    imports. See `docs/production-backlog.md`, "Isolate agent-code execution".
  - Two mock limits are pinned for `snowflake-snowpark-python` 1.55.0. First, `==` and `!=` do not
    propagate NULL, so the cookbook teaches the explicit-NULL filter form. Second, there is no working
    `round()`: decimal narrowing goes through binary-float rounding, so a half-way value such as
    `1.005` can round the wrong way locally. No local test can prove that half-boundary for Snowpark.
- **dbt-duckdb against Snowflake.**
  - DuckDB's types and case folding differ from Snowflake's.
  - The `merge` semantics are the adapter's. dbt-duckdb emits `UPDATE BY NAME` / `INSERT BY NAME`.
  - Hooks run on DuckDB.
  - The tests that `schema.yml` declares never run.
  - The `snowflake` output of the profile has never run, and `dbt-snowflake` is not installed (rung 3).
- **DuckDB against Snowflake.** Types, identifier casing and `VARCHAR(n)` enforcement all differ.
- **The simulator against Alteryx.** `scripts/dev/alteryx_sim.py` is a model written from documents,
  not from an observed run (`docs/reference/simulator-semantics.md`). It computes formula arithmetic
  in exact decimal, where Alteryx uses binary64. `README.md` §2 gives the measured size of that gap.
- **Policy tables.** The `TARGET_CLASS` table and the dbt blocker list are policy, verified only by
  their own tests.

**Snowflake** (every item in `docs/reference/snowflake-backend.md` §7; rungs 3 and 4):

- any connection at all;
- the procedure bodies on the account: the `LET` / `IDENTIFIER(:<LOGICAL>_SRC)` form, `ALTER SESSION`
  in a caller's-rights procedure, and the `$$` master through the connector (rung 3a);
- a CALL of any master procedure: rung 3a creates one, and no rung calls it;
- creating the `LANGUAGE PYTHON` wrapper, plus runtime, packages and Anaconda terms;
- real `DESCRIBE` type names reaching `scripts/compare.py`;
- Snowpark `to_pandas()` data types;
- mixed-case golden column names;
- `executemany` loading at scale;
- a missing privilege that looks like a missing table;
- a dropped connection that reads as a FAIL;
- concurrency, which is documented and not enforced;
- credits, which are always `null`;
- the weaker order perturbation.

**Hosted models** (rung 2):

- No hosted model has run. The ids in the repository are placeholders until §1.3.
- Which model serves when the session `model` and the agent file's `model` differ.
- Whether the CLI honours `config.json`'s `subagents.agents` routing.
- The real size of the long-context window.
- The tool names on the hosted models. The third live test observed them on a local BYOK model
  (§1.5).
- Whether `session.compaction_complete` and `assistant.usage` arrive in the assumed shapes, and
  whether a model actually re-reads its notes file after a compaction
  (`docs/reference/large-workflows.md`, "Compaction").
- The character-to-token estimate (characters ÷ 4) behind every character budget. The ratio has
  never been measured against `peakInputTokens`.

**Alteryx** (rung 4):

- the printed `AlteryxEngineCmd.exe` command line, the `.yxdb` captures and their import;
- plugin ids in the company's Alteryx version, since every mapping carries
  `# verify against your Alteryx version`;
- Alteryx Server schedules, which are not read from anywhere (`README.md` §11);
- the capture gap in §3.5: database targets that append, merge or carry pre/post SQL cannot be
  captured.

**Around the pipeline:**

- `gh` issue and PR creation, which is off by default (`gh` was never installed where this was built);
- an MCP server behind the role agents, and the `MIGRATION_AGENT` role;
- prompt injection beyond the policy tests (`docs/production-backlog.md`);
- the policy itself. It is a textual check and not a sandbox (`orchestrator/POLICY.md`, "Known
  limitations").

## Never do

- Never type, print, copy, store or commit a credential, token, password, private key or
  `connections.toml`. The human's named connection is the only way in.
- Never run `copilot /login`, `gh auth login` or any other sign-in as the agent. The human signs in.
- Never push, and never open a pull request or an issue. Nothing reaches a remote without the
  human's explicit approval. Never pass `--gh` or set `github.enabled` without that approval.
- Never loosen a deny in `orchestrator/policy.ts`, never change a verdict rule in
  `scripts/compare.py`, and never change a tolerance to make a model or a workflow pass. Report the
  failure instead. The one sanctioned `orchestrator/policy.ts` change is §1.5's tightening, reviewed
  by the human before any re-run.
- Never run the pipeline in the repo root. Run roots only (§0.3).
- Never edit anything under `docs/spec/`. The owner's program spec is kept verbatim.
- Never run `deploy.py` with `--execute` without the human having read its dry run and saved the
  previous DDL. Never deploy from an agent session.
- Never run anything against a database that is not the listed sandbox. Never add a database to
  `policy.sandboxDatabases` that the human has not confirmed, or that holds anything of value.
- Never run a step that needs a login or a credential (§0.1). That includes every
  `--runner copilot --profile hosted` run, anything with `--backend snowflake`, `--connection` or
  `--sandbox-database`, and every Alteryx run.
- Never commit real golden data, survey reports, catalog exports or anything else copied from the
  company's systems into the checkout.
- Never choose, switch or escalate a model on your own. Escalation is a human decision with the
  evidence (`docs/handoff-copilot-models.md` §1).
