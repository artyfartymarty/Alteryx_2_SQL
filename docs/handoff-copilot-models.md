# Hand-off: running this pipeline on GitHub Copilot's hosted models

Production hand-off in full: `docs/handoff-production.md` (models, Snowflake, the real Alteryx
corpus, the verification ladder, what has never been proven). This file is its model reference.

For the agent or person who takes this repo from its offline state to real GitHub Copilot
sessions. Written 2026-09-22. Read `README.md` §2 (honesty note) and §8 first.

## 0. Where things stand (do not assume more than this)

- **Nothing has run against GitHub-hosted Copilot models yet.** The two live tests in
  `docs/live-smoke-test.md` drove the real `@github/copilot-sdk` path (`CopilotRunner`,
  `--runner copilot --profile local`) against a *local* BYOK model, which overflowed its context
  window during intake both times. That proved session creation, agent loading, the policy hooks
  and the diagnostics; it proved nothing about any hosted model.
- **The model ids in this repo are placeholders from the program spec** (`gpt-5.6-luna`,
  `gpt-6-astra` in `orchestrator.config.json`, `config.json` and `.github/agents/*.agent.md`).
  They were never checked against a live catalog. Step 2 below replaces them.
- **Still unverified:** the SDK's file-write and SQL tool names and argument shapes
  (`WRITE_TOOL` / `SQL_TOOL` in `orchestrator/policy.ts` were written from type definitions; no
  live session ever attempted a write). The first hosted run settles this — see step 5.
- Everything committed under `workflows/` came from `--runner mock` (hand-written canned agent
  outputs) plus the real Python scripts. `--runner mock` keeps working unchanged after this
  hand-off; it is the regression baseline.
- **A segment is no longer always SQL.** `contract.json`'s `"target"` decides whether the translator
  writes `proc.sql` or a Snowpark Python `proc.py`, and the validator role runs
  `validate_segment.py` or `validate_snowpark.py` accordingly — read
  `docs/reference/output-targets.md` before a hosted run, because it changes what the translator,
  reviewer, validator and fixer prompts ask of a model and adds three scripts to the per-role
  allow-lists in `orchestrator/policy.ts`. A Snowpark segment is a longer, denser prompt than a SQL
  one (the §4.2 rule list travels in the translator agent file), which matters for the
  context-window budgeting in §1 and §4 below. The dbt target is built too
  (`docs/reference/output-targets.md` §3.3).

## 1. The owner's model policy (binding)

1. **Default every role to Luna Max.** Do not route any role to a more expensive model by
   default, whatever the program spec's table (`docs/spec/01-copilot-setup.md`, lines 13–21) says
   about `gpt-6-astra (required)`. The spec is kept verbatim as history; this policy supersedes
   it for model routing.
2. **Use the 1M-token context tier only when a role needs it**, and only as the *same* model with
   a larger window — never as a jump to a pricier model. Roles whose input is large: intake
   (whole DAG + touchpoints), analyzer (whole workflow), fixer (SQL + validation report + dag),
   parser-recovery (raw XML + parser source + corpus), validator (large diff reports).
   Roles that stay on the default tier: translator (one segment at a time), reviewer, documenter.
   Evidence for the split: `docs/live-smoke-test.md` — intake alone consumed 35k–70k tokens with
   a small model that explored the repo tool call by tool call.
3. **No automatic escalation.** The pipeline never picks a different model on its own (no
   `model: "auto"`, no auto-tier routing, no retry on another model). Escalating a role to a
   pricier model is a human decision, taken after Luna Max has failed that role twice *with* the
   long-context tier, and recorded in the build ledger
   (`docs/superpowers/build-reports/2026-09-18-pipeline/ledger.md`) with the evidence
   (`manifest.metrics`, `audit.jsonl`).
4. **Reasoning effort:** `low` for documenter and reviewer, `medium` for everything else; raise a
   role to `high` only with the same two-failures evidence. See the note on "Max" in step 2.
5. **Spend is measured, not guessed:** after every hosted run read `manifest.metrics.<role>`
   (`lastMs`, cumulative `toolCalls`) and `audit.jsonl` before changing anything.

## 2. First: discover the real catalog

The human signs in to the Copilot CLI (`copilot`, then `/login`); an agent never does this and
never handles a token. Then:

    fnm exec --using=22 node.exe --experimental-strip-types scripts/dev/list_models.ts

`scripts/dev/list_models.ts` (type-checked against SDK 1.0.14, not yet run live because no login
existed on the build machine) prints every model the account can use: `id`, `name`,
`context_window` (`capabilities.limits.max_context_window_tokens`), `billing_x` (the
premium-request multiplier the catalog reports), the supported `reasoningEffort` values, and
`policy` (`enabled` / `disabled` / `unconfigured` — a model that is not `enabled` has to be
switched on in the account's Copilot settings first, by the human).

From that list:

- **Luna Max.** If the catalog shows Luna Max as its own `id`, use that id everywhere below. If
  instead Luna appears once and lists `max` among its supported reasoning efforts, "Luna Max" is
  Luna with `reasoningEffort: "max"` — then use Luna's id with effort `medium` per policy 4 and
  reserve `max` for the two-failures case. Write down which of the two it turned out to be, in
  `docs/live-smoke-test.md`.
- **The 1M window.** The SDK exposes `contextTier: "long_context"`, which "pins the session to
  the long-context tier when the selected model supports it". Check `context_window` for the
  chosen id; if the listed number is not the long-context figure, run one session with the tier
  set and read the window from the session's own reporting before relying on it.
- Use only ids from this list. Never type an id from memory or from the spec.

Then set every id at once, in every place §3's table names, with one command (never by hand):

    .venv/Scripts/python.exe scripts/dev/set_models.py --default <Luna Max id> \
      --long-context-roles intake,analyzer,fixer,parser-recovery,validator \
      --effort documenter=low --effort reviewer=low

With no `--role`, every role runs on the default id: `loadConfig` (`orchestrator/cli.ts`) takes a
profile's `roleModels`, `roleContextTiers` and `roleReasoningEffort` from the file whole or not at
all, and `DEFAULT_CONFIG`'s hosted profile carries no `roleModels`, so nothing brings the program
spec's per-role split back (pinned by `orchestrator/test/integration.test.ts`, which runs this very
command on a copy of the committed configuration).

`--dry-run` prints the changes without writing anything; `--role <role>=<id>` overrides one role
away from `--default` (needed only if a role is ever escalated per policy 3). It edits
`orchestrator.config.json`, every `.github/agents/*.agent.md` frontmatter and `config.json`
together, so the three never drift out of the sync §3 requires. It never contacts the network —
it only writes the ids it is given.

Finally, run the preflight before trusting any of it:

    fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --check-models --profile hosted

`orchestrate.ts --check-models` lists the same live catalog through the SDK and checks every id
`set_models.py` just wrote against it, printing `<file + role>: <id> — missing` or `— disabled`
for anything wrong and exiting 1; exit 0 means every configured id exists and is `enabled`. Like
`list_models.ts`, this needs the human's own login and is never run by an agent.

## 3. Where a model is chosen in this repo (precedence, as the code stands)

| File | Field | Reaches the SDK as | Current value | Set it to |
|---|---|---|---|---|
| `orchestrator.config.json` | `profiles.hosted.roleModels.<role>`, else `profiles.hosted.model` | `createSession({ model })` in `orchestrator/runner.ts` (`profile.roleModels?.[role] ?? profile.model`) | astra for intake / analyzer / translator / fixer / parser-recovery, luna otherwise | the Luna Max id for every role (drop `roleModels` entirely, keep `model`) |
| `orchestrator.config.json` | `profiles.hosted.reasoningEffort`, else per-role `profiles.hosted.roleReasoningEffort.<role>` | `createSession({ reasoningEffort })` (`profile.roleReasoningEffort?.[role] ?? profile.reasoningEffort`) | `medium` profile-wide (the baseline every role without its own `roleReasoningEffort` entry falls back to — never `undefined`), `low` for documenter/reviewer | unchanged — already matches policy 4 (§4, done) |
| `.github/agents/<role>.agent.md` | frontmatter `model:` | `customAgents[].model` (hosted profile only; `loadAgents` in `orchestrator/agents.ts`) | astra / luna per spec | the same Luna Max id, so the session model and the agent model never disagree |
| `config.json` (Copilot CLI config, copied into `COPILOT_HOME` per the spec) | `subagents.agents.<name>.{model, effortLevel, contextTier, modelPolicy}` and `planModel` | the CLI's own sub-agent spawning (the `task` tool inside a session) and planning | astra with `modelPolicy: required` for four roles; `contextTier` per spec | the Luna Max id everywhere; remove `modelPolicy: required`; `contextTier: long_context` only for the roles in policy 2 |

Which wins when the session `model` and the custom agent's `model` differ is an SDK question
this build never had to answer. Keep them identical (the table does) and, on the first hosted
run, confirm from the session's reporting which model actually served.

`tests/test_agents_config.py` checks the frontmatter keys (`name`, `description`, `model` only),
that `config.json` parses, `maxConcurrency == 4`, `maxDepth == 2`, and that all nine agents plus
the five built-ins are listed. It pins no model id. If you lower `maxConcurrency` for cost (every
sub-agent spawn is a premium request), update that assertion in the same commit.

## 4. Per-role context tier and effort — done

This is implemented; nothing here is still pending.

`CopilotRunner.run`'s `createSession` call (`orchestrator/runner.ts`) passes `model`,
`reasoningEffort`, `contextTier`, `provider`, `customAgents`, `agent` and the hooks:

    session = await this.client.createSession({
      workingDirectory: this.root,
      model: profile.roleModels?.[role] ?? profile.model,
      reasoningEffort: profile.roleReasoningEffort?.[role] ?? profile.reasoningEffort,
      ...(profile.roleContextTiers?.[role] ? { contextTier: profile.roleContextTiers[role] } : {}),
      provider: profile.provider,
      // …unchanged
    });

`contextTier` is only spread into the call when `profile.roleContextTiers[role]` is set — the SDK
sees no `contextTier` key at all otherwise, not merely an `undefined` one. This is what implements
policies 2 and 4:

1. `orchestrator/types.ts` — `ProfileConfig` carries
   `roleContextTiers?: Partial<Record<Role, "default" | "long_context">>` and
   `roleReasoningEffort?: Partial<Record<Role, ReasoningEffort>>`.
2. `orchestrator.config.json`, under `profiles.hosted`:

        "reasoningEffort": "medium",
        "roleContextTiers": {
          "intake": "long_context", "analyzer": "long_context", "fixer": "long_context",
          "parser-recovery": "long_context", "validator": "long_context"
        },
        "roleReasoningEffort": { "documenter": "low", "reviewer": "low" }

   `reasoningEffort` is the baseline every role without its own `roleReasoningEffort` entry falls
   back to (`profile.roleReasoningEffort?.[role] ?? profile.reasoningEffort`) — without it, the
   six roles policy 4 calls "medium" would resolve to `undefined` instead.

3. `scripts/dev/set_models.py` writes both maps (and the matching model id in every
   `.github/agents/*.agent.md` and in `config.json`) from one command — see §2. Hand-editing the
   three files individually is no longer how this is done.
4. `orchestrate.ts --check-models --profile hosted` (`orchestrator/models.ts`, `orchestrator/cli.ts`)
   is the preflight: it prints `effective <role>: <id>` for every role (the model that role's
   session will ask for), lists the live catalog through the SDK's `client.listModels()` and checks
   every id currently configured for the hosted profile — `orchestrator.config.json`'s
   `profiles.hosted.model`/`roleModels`, every agent's frontmatter `model:`, and every
   `config.json` sub-agent `model` — against it, naming the file and role for anything `missing`
   from the catalog or present but not `enabled` (exit 1). Exit 2 for `--profile local` (there is
   nothing hosted to check) or when the catalog itself cannot be listed (not signed in). It is a
   preflight only — it never selects or runs a workflow — and the real catalog call happens only
   under a human's own login, exactly like `list_models.ts`; every automated test injects a fake
   catalog instead (`orchestrator/test/models.test.ts`, `orchestrator/test/cli.test.ts`).

The `local` BYOK profile sets neither map, by construction (one model, one window), so it reaches
`createSession` exactly as before: no `contextTier` key, and `reasoningEffort` from the profile
alone.

Tests: `orchestrator/test/runner.test.ts` ("per-role context tier and effort reach createSession",
"the local profile is unchanged"), `orchestrator/test/models.test.ts`, the `--check-models` cases
in `orchestrator/test/cli.test.ts`, and `tests/test_set_models.py` (which also re-checks
`tests/test_agents_config.py`'s frontmatter contract on the files `set_models.py` just rewrote).
`tsc --noEmit` and `npm test` stay green.

## 5. First hosted run — do it in this order

1. Human: `copilot`, then `/login`; confirm `/model` shows Luna Max. (Agents never run `/login`
   or `gh auth login`, and never touch tokens.)
2. Run `scripts/dev/list_models.ts`; set every id at once with `scripts/dev/set_models.py` (§2,
   §4) using the ids §3 says to set; then run `orchestrate.ts --check-models --profile hosted`
   (§4 item 4) and confirm it exits 0; run
   `.venv/Scripts/python.exe -m pytest tests/test_agents_config.py tests/test_set_models.py`,
   `npm test`, `tsc --noEmit`.
3. Set `"parallelism": 1` in `orchestrator.config.json` for the first run (raise it only after
   the per-role spend is known).
4. Build a run root exactly as `docs/handoff-production.md` §0.3 shows, then seed it with
   `build_samples.py seed`. `README.md` §6's minimal recipe is for `--runner mock` only: it copies
   no `.github/agents/` (a Copilot session would get no custom agents) and writes an
   `orchestrator.config.json` whose hosted profile falls back to the placeholder ids. Never run
   this inside the repo tree.
5. The human runs (it uses their Copilot login; `docs/handoff-production.md` §0.1):

        fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root <scratch> --only wf_0001 --runner copilot --profile hosted --no-interactive --stop-after intake

6. Read `<scratch>/workflows/wf_0001/audit.jsonl` and `manifest.json`:
   - every `toolName` the session used; in particular the **write** tool's name and argument
     keys the first time the model writes a file, and the SQL tool's if any. Tighten
     `WRITE_TOOL` / `SQL_TOOL` in `orchestrator/policy.ts` from that evidence, one unit test per
     observed name or shape (`orchestrator/test/policy.test.ts`). Never loosen a deny to help the
     model; a write the policy allowed but should not have is a critical finding.
   - `manifest.metrics.intake` — `toolCalls` and `lastMs`; `manifest.reasons` if it parked.
   - whether `intake/plan.md` exists and `manifest.status.intake` is `READY` or
     `WAITING_FOR_ANSWERS` (expected on a fresh root: answers come from
     `scripts/dev/answer_samples.py`, then re-run).
7. Continue with `--stop-after analyze`, then the full run, then `wf_0002` to `wf_0005`. Compare
   every hosted `validation.json` verdict with the committed mock-run one; a difference is a
   finding about the model, not about the validator (the validator's numbers never come from an
   agent).
8. Append a dated section to `docs/live-smoke-test.md`: exact commands, the model ids that
   served, per-role `toolCalls` / `lastMs`, what worked, what did not, the honest verdict per
   role. No number in prose that is not copied from a log or a manifest.

## 6. Cost controls already in the repo — keep them

- `budgets.maxToolCallsPerWorkflow` (400): per workflow, accumulates across sessions and roles;
  exceeding it parks the workflow at `NEEDS_HUMAN` with reason `budget`. An explicit
  `--from-stage <stage>` grants a fresh budget (and logs that it did).
- `maxFixIterations` (3), `maxParseRecovery` (2), `sessionTimeoutMs` (20 min), `parallelism` (3;
  use 1 at first) in `orchestrator.config.json`; `maxConcurrency` (4) and `maxDepth` (2) in
  `config.json`.
- Parked escalations: a workflow at `NEEDS_HUMAN`, `QUARANTINED` or `BLOCKED` is not re-run by a
  plain invocation, so re-running the orchestrator over a backlog spends nothing on parked ones.
- Context overflow, timeout and rate limit are classified separately (`manifest.reasons`);
  only timeout and missing-output get one retry, rate limit gets backoff, overflow none.
- Segments whose `segment_status` already starts with `PASS` are never re-translated on a plain
  run; only `--from-stage translate` redoes them.

## 7. Do not

- Do not edit anything under `docs/spec/` (the owner's spec, verbatim).
- Do not let the pipeline choose models dynamically, retry on a different model, or use the
  SDK's `auto` routing.
- Do not weaken `orchestrator/policy.ts` denies, `scripts/compare.py` verdict rules, or the
  validator's fail-closed behaviour to get a green run.
- Do not write model ids, context sizes, prices or pass counts into docs from memory; copy them
  from `list_models.ts` output, logs or manifests.
- Do not run the pipeline in the repo root (it would rewrite `mappings/global.yaml`'s fixture and
  leave replayed files behind); scratch roots only.
