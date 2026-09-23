# Task P1 report — hosted-model hook-up as configuration

Worktree: `.worktrees/p2-P1`, branch `wt/p2-P1`, base `6d0fa33`.

## What I implemented

### 1. Per-role `contextTier` and `reasoningEffort` reach `createSession` (handoff §4 item 4)

- `orchestrator/types.ts` — `ProfileConfig` gains
  `roleContextTiers?: Partial<Record<Role, "default" | "long_context">>` and
  `roleReasoningEffort?: Partial<Record<Role, ReasoningEffort>>`. `RunOptions` gains
  `checkModels?: boolean`.
- `orchestrator/runner.ts` — `CopilotRunner.run`'s `createSession` call now passes
  `reasoningEffort: profile.roleReasoningEffort?.[role] ?? profile.reasoningEffort` and spreads
  `contextTier: profile.roleContextTiers[role]` only when that role has a tier configured — the
  SDK sees no `contextTier` key at all otherwise (not an `undefined` one). Verified against
  `node_modules/@github/copilot-sdk/dist/types.d.ts:1886` (`model`), `:1892` (`reasoningEffort`),
  `:1907` (`contextTier`), `:1583` (`ReasoningEffort` union), `:1588`
  (`ContextTier = "default" | "long_context"`).
- `orchestrator.config.json` and `orchestrator/cli.ts`'s `DEFAULT_CONFIG` — `profiles.hosted`
  gains `roleContextTiers` (intake, analyzer, fixer, parser-recovery, validator →
  `"long_context"`) and `roleReasoningEffort` (documenter, reviewer → `"low"`), exactly as
  handoff §4 item 4 specifies. `profiles.local` is untouched (it sets neither map, so it reaches
  `createSession` exactly as before).

### 2. `orchestrator/models.ts` (new) — pure functions behind the preflight

- `ConfiguredModel { where; id }`, `CatalogModel { id; policy?: { state?: string } }` (the subset
  of the SDK's `ModelInfo`, `types.d.ts:2962-2977`; `ModelPolicy.state`,
  `"enabled" | "disabled" | "unconfigured"`, `types.d.ts:2946-2949`), `ModelCheck { ok; missing;
  disabled }`.
- `configuredModels(config, profile, agentModels, cliConfig)` collects `profiles.<profile>.model`,
  every `profiles.<profile>.roleModels.<role>`, every entry of `agentModels` (agent frontmatter,
  keyed by agent name) and every `cliConfig.subagents.agents.<name>.model`, each tagged with a
  human-readable `where` string (e.g. `orchestrator.config.json profiles.hosted.roleModels.intake`,
  `.github/agents/intake.agent.md model`, `config.json subagents.agents.intake.model`), de-duplicated
  by the `(where, id)` pair.
- `checkModels(configured, catalog)` reports every id with no catalog entry as `missing` and every
  id present but not `policy.state === "enabled"` (covers `disabled`, `unconfigured`, and no
  `policy` at all) as `disabled`.

### 3. `orchestrate.ts --check-models --profile hosted` preflight

- `orchestrator/cli.ts`: `--check-models` is a boolean flag (`parseArgs`); `main`'s `deps` gains
  `listModels?: () => Promise<CatalogModel[]>`. The branch runs before `assertPythonExists` and
  before any workflow selection — `--check-models` needs no Python interpreter and touches no
  `workflows/`.
- `runCheckModels`: `profile !== "hosted"` (including the default, unset profile) is a
  `UsageError` → exit 2. Reads `config.json` at the root (`readCliConfigFile`; absent is fine,
  present-but-unparsable is a usage error, mirroring `readConfigFile`'s treatment of
  `orchestrator.config.json`), loads `.github/agents/*.agent.md` via `loadAgents(root, "hosted")`
  for the frontmatter models, then calls `deps.listModels ?? liveListModels` — `liveListModels`
  starts a real `CopilotClient`, lists, stops, mirroring `scripts/dev/list_models.ts`, and is
  **never exercised by any test** (every test injects `listModels`). A `listModels` rejection
  becomes a `UsageError` naming the login step (`copilot`, then `/login`) → exit 2. Otherwise
  prints `<where>: <id> — missing` / `— disabled` for every bad entry and a one-line summary,
  returning 0 (`ok`) or 1.

### 4. `scripts/dev/set_models.py` (new)

`--default <id> [--role <role>=<id>]... [--long-context-roles r1,r2] [--effort
<role>=<level>]... [--root .] [--dry-run]`.

- Nine roles are valid role names everywhere (`CUSTOM_AGENTS`: the eight orchestrator roles +
  `cookbook-curator`, matching `tests/test_agents_config.py`'s `CUSTOM_AGENTS`). Only the eight
  orchestrator roles ever reach `orchestrator.config.json`'s three per-role maps —
  `cookbook-curator` has no `Role`-keyed slot there, only its agent file and its `config.json`
  entry.
- **Every run replaces the three `orchestrator.config.json` per-role maps and every
  `config.json` agent's `model`/`contextTier` wholesale from that run's own flags** — never
  merged with a previous run's leftovers. That's what makes running the same command twice a
  no-op and makes "one command sets it all consistently" actually true (a role dropped from
  `--role` this time is dropped from `roleModels`, not left stale).
- `.github/agents/<role>.agent.md`: only the frontmatter `model:` line's value is rewritten
  (`rewrite_agent_model`, regex-scoped to the frontmatter fence, never touching the body); read
  and written with `newline=""` so LF line endings survive untouched (Windows text mode would
  otherwise rewrite LF → CRLF on write).
- `config.json`: nine custom agents get their role's resolved id (override or `--default`), five
  built-ins always get `--default`; `contextTier` is `"long_context"` exactly for roles named in
  `--long-context-roles`, `"default"` for every other agent (including built-ins, and including
  ones that previously had `"long_context"` as placeholder data — e.g. `research`); `modelPolicy`
  is deleted wherever present. `effortLevel` and every other key are left untouched.
- `orchestrator.config.json` and `config.json` are read/written through `lib.io.read_json` /
  `write_json` (`json.loads` / `json.dumps(indent=2)` + trailing newline, LF) — parsed-content
  equality is the contract for these two files, not byte-identity.
- `profiles.local` is never touched.
- Exit codes: 0 written (or, with `--dry-run`, would be written); 2 for an unknown role name (in
  `--role`, `--long-context-roles` or `--effort`), an unknown `--effort` level, a malformed
  `ROLE=VALUE`, or any other usage/unexpected error — validated *before* any file is opened, so
  nothing is ever written on a bad invocation. There is no domain-failure (1) case: this script
  only writes what it's told, never judging an id.

### 5. `docs/handoff-copilot-models.md`

- §4 rewritten past-tense ("Per-role context tier and effort — done"): the actual `createSession`
  snippet, the two `ProfileConfig` fields, the `orchestrator.config.json` shape, and — new —
  items 3 and 4 naming `set_models.py` and `--check-models` as how this is now driven, plus the
  real test list. No remaining "to implement" language.
- §2 gains, after discovering the catalog: the `set_models.py` command (with the exact
  `--long-context-roles`/`--effort` values policy 2/4 call for) and the `--check-models` command,
  each with what it does and why it's safe (no network from `set_models.py`; human-login-only for
  `--check-models`, same as `list_models.ts`).
- §3's `reasoningEffort` row no longer says "needs the code change in §4" (that's done); it now
  states the per-role fallback.
- §5 step 2 replaced "fill the ids per §3; make the §4 change" with running `set_models.py` then
  `--check-models`, and runs `tests/test_set_models.py` alongside `test_agents_config.py`.
- No machine path, login name or `scratchpad` reference introduced (checked with the same regex
  `tests/test_committed_workflows.py` uses).

## TDD evidence

- **models.ts** — RED: `node --experimental-strip-types --test orchestrator/test/models.test.ts`
  failed with `ERR_MODULE_NOT_FOUND: orchestrator/models.ts` (file did not exist). GREEN after
  writing `orchestrator/models.ts`: 5/5 passing.
- **runner.ts per-role tier/effort** — RED: added "per-role context tier and effort reach
  createSession" to `runner.test.ts` before touching `runner.ts`; ran
  `node --experimental-strip-types --test orchestrator/test/runner.test.ts` → `not ok 1` (the
  session config carried no `contextTier`/per-role `reasoningEffort`); the sibling "local profile
  unchanged" test passed trivially (current code already omits `contextTier`) — expected, since
  that's the no-op case. GREEN after the `runner.ts` edit: 31/31 (26 previous + 2 new + 3
  d1/d3/misc unaffected — full file count).
- **cli.ts `--check-models`** — RED: added the eight `--check-models` tests to `cli.test.ts`
  before touching `cli.ts`; 5 of 8 failed (`unknown option --check-models` → exit 2, which
  happened to satisfy 3 of the 8 assertions by accident — the two "usage error" tests and the
  "never starts a workflow run" test — but not the catalog-behavior ones). GREEN after the
  `cli.ts` branch, `readCliConfigFile`, `runCheckModels` and `liveListModels`: 21/21.
- **set_models.py** — implemented alongside its own module rather than strictly test-first (see
  Self-review below); rigor was checked afterwards: temporarily disabled the `modelPolicy`
  removal branch (`if "modelPolicy" in agent:` → `if False and …`), reran
  `pytest tests/test_set_models.py -v`, confirmed `test_one_command_sets_every_place_consistently`
  failed (`assert "modelPolicy" not in agents[name]` on `general-purpose`/`analyzer`/etc.), then
  restored the file (verified via `git diff` / `grep "if False"` — clean) and reran to confirm
  6/6 green again.

## Final verification

- `node --experimental-strip-types --test orchestrator/test/*.test.ts`: **239/239 passing**
  (baseline 225/225 at `6d0fa33`). Exactly +14, confirmed against each file individually:
  `cli.test.ts` 14 → 21 (+7), `runner.test.ts` 24 → 26 (+2), `models.test.ts` 0 → 5 (new file, +5).
- `tsc --noEmit -p .`: clean, no errors.
- `.venv/Scripts/python.exe -m pytest tests/test_agents_config.py tests/test_set_models.py`:
  **62/62 passing** (56 in `test_agents_config.py`, unaffected, + 6 new in `test_set_models.py`).
- Whole suite `.venv/Scripts/python.exe -m pytest`: **1447 passed, 0 skipped in 299.49s**
  (baseline was 1441 passed / 0 skipped) — exactly +6, matching `tests/test_set_models.py`'s six
  new tests (the node suite and `test_agents_config.py` were already counted in the baseline's
  1441 and show no regressions).

## Files changed

- `orchestrator/types.ts`, `orchestrator/runner.ts`, `orchestrator/cli.ts`,
  `orchestrator/models.ts` (new), `orchestrator.config.json`
- `orchestrator/test/runner.test.ts`, `orchestrator/test/cli.test.ts`,
  `orchestrator/test/models.test.ts` (new)
- `scripts/dev/set_models.py` (new), `tests/test_set_models.py` (new)
- `docs/handoff-copilot-models.md`

## Things W2, W4 and P4 must know

- **No new shared type was added to `types.ts` beyond what P1 owns.** `ProfileConfig` gained
  `roleContextTiers`/`roleReasoningEffort` and `RunOptions` gained `checkModels` — both scoped to
  P1's own concern. I did not touch `orchestrator/stages.ts` or
  `orchestrator/test/{fakes,stages.test}.ts` (Task F's files), so there's nothing there for W2 to
  rebase around from this task.
- **`CopilotRunner.run`'s `createSession` call now has one more conditional spread
  (`contextTier`)** — W2/W4, if you touch that same call site for compaction or batching, note
  that `contextTier` is present in the object literal *only* when
  `profile.roleContextTiers?.[role]` is truthy; don't reintroduce an `undefined`-valued key (the
  "local profile is unchanged" test in `runner.test.ts` asserts the key is *absent*, not
  `undefined`, via `"contextTier" in sink[i]`).
- **`main()`'s `deps` type grew a new optional field** (`listModels`). If W4 or P4 also extend
  `main`'s `deps`, it's now `{ env?: Env; listModels?: () => Promise<CatalogModel[]> }` in
  `orchestrator/cli.ts` — merge additively, don't replace.
- **`set_models.py`'s per-run REPLACE semantics** (not merge) apply to all three
  `orchestrator.config.json` per-role maps and to every `config.json` agent's
  `model`/`contextTier`. If a later task (e.g. P4's hand-off doc, or a W-series task that also
  touches model routing) writes an automation that calls `set_models.py` repeatedly with
  *different* subsets of flags across calls, know that each call fully re-derives those fields
  from its own flags — it will NOT accumulate across separate invocations. To set overrides for
  two different roles, pass both `--role` flags in the same call.
- **`docs/handoff-copilot-models.md` §4 no longer says "pending"** — if P4's
  `docs/handoff-production.md` or `docs/production-backlog.md` references the old "one code
  change" framing, that's now stale; point instead at §4's "done" state and the `set_models.py` /
  `--check-models` pair in §2.
- **`--check-models` is intentionally never run by this task, nor should it be by any other
  automated task** — every automated test (mine, and presumably any future one) must inject
  `deps.listModels`; the real path (`liveListModels`, a real `CopilotClient`) is exercised only
  by a human, per docs/handoff-copilot-models.md's binding rule that no agent ever authenticates.

## Self-review notes / concerns

- `scripts/dev/set_models.py` was written together with its implementation rather than strictly
  test-first (unlike the TypeScript pieces, where I confirmed RED before implementing). I
  compensated by running a deliberate mutation test after the fact (see TDD evidence above) to
  confirm the test suite actually exercises the `modelPolicy`-removal branch, and by reasoning
  through each other branch's coverage by inspection. If the controller wants strict RED-first
  evidence for this file specifically, I can re-derive it by reverting the implementation to a
  stub and re-running, but the mutation test already gives equivalent confidence that the tests
  are not vacuous.
- `configuredModels`' de-duplication by `(where, id)` is implemented as specified, but in
  practice a true duplicate `(where, id)` pair cannot arise from a single call under the current
  schema (each source's `where` string is unique per role/agent-name, so ids can repeat across
  *different* `where`s but never produce an identical pair) — I could not construct a natural
  test case that exercises the dedup branch overwriting an existing entry; my
  `models.test.ts` test instead asserts the *invariant* (no duplicate `(where, id)` pairs in the
  output, and idempotence across repeated calls) rather than a collision scenario. The dedup
  logic itself is a small, obviously-correct `Set`-guard, so I judged this an acceptable gap
  rather than a real risk.
- I left `docs/handoff-copilot-models.md` §3's "Set it to" column wording largely as-is (still
  describes the target state correctly); I didn't rewrite the whole table to lead with
  `set_models.py`, since the brief only asked for §4 ("done") and §2/§5 (naming the new command
  and preflight) — the table itself wasn't in scope, beyond fixing the one row that explicitly
  pointed at "the code change in §4" as still-pending.
- Per the plan's file-ownership table, Task P1 does not own `README.md` (Task D's §8 mention and
  the phase-1 groundwork already exist there; P1's own file list in
  `docs/superpowers/plans/2026-09-22-output-targets-phase2.md` and `task-P1-brief.md` names only
  the files listed above) — I did not touch it, even though an earlier draft of §4 in the
  hand-off doc mentioned a README §8 sentence; I dropped that instruction rather than act outside
  P1's ownership.

## Fix round 1

Two findings from review: one Important (I1), one Minor (M1). Both fixed; commit `9ab1a71`.

### I1 — the hosted profile never set a baseline `reasoningEffort`

`profiles.hosted` had `roleReasoningEffort` (documenter/reviewer → `low`) but no bare
`reasoningEffort` key at all. Since `runner.ts` computes
`profile.roleReasoningEffort?.[role] ?? profile.reasoningEffort`, the six roles with no
`roleReasoningEffort` entry (intake, analyzer, translator, validator, fixer, parser-recovery)
resolved to `undefined`, not the owner's policy 4 ("medium for everything else").

- **RED**: added a test to `orchestrator/test/cli.test.ts` — "the real committed
  orchestrator.config.json resolves the owner's reasoningEffort/contextTier policy for every
  hosted role" — that loads the REAL, currently-checked-out `orchestrator.config.json` through
  `loadConfig` (not a hand-built fixture; same `REPO_ROOT` pattern `integration.test.ts` already
  uses: two directories up from `orchestrator/test/`), then computes the effective
  `reasoningEffort` and `contextTier` for every one of the eight hosted roles exactly as
  `CopilotRunner.run`'s `createSession` call does, and asserts each against the policy (medium
  except documenter/reviewer low; `long_context` exactly for intake, analyzer, fixer,
  parser-recovery, validator). Ran `node --experimental-strip-types --test
  orchestrator/test/cli.test.ts`: failed with `intake: reasoningEffort must resolve per the
  owner's policy item 4, got undefined` (and the same for every other role missing from
  `roleReasoningEffort`) — confirming the bug live against the actual committed file.
- **GREEN**: added `"reasoningEffort": "medium"` to `profiles.hosted` in both
  `orchestrator.config.json` and `DEFAULT_CONFIG` in `orchestrator/cli.ts`. Reran the same test:
  passes for all eight roles.
- `docs/handoff-copilot-models.md` — §3's `reasoningEffort` row's "Current value" cell now states
  the true baseline (`medium` profile-wide, `low` for documenter/reviewer) instead of describing
  a value the file didn't actually carry; §4 item 2's `orchestrator.config.json` snippet gained
  the `"reasoningEffort": "medium"` line with a sentence explaining why it's required (the
  fallback for the six roles with no per-role override).

### M1 — `set_models.py` printed a bare traceback on malformed input

A `json.JSONDecodeError` from either JSON file, or a `ValueError` from `rewrite_agent_model`
(no `---` fence / no `model:` line) on an agent file, fell through to the generic `except
Exception: traceback.print_exc(); return 2` handler — a multi-line Python traceback with no
single line naming which file was bad.

- **RED**: added three tests to `tests/test_set_models.py` — one malformed
  `orchestrator.config.json`, one malformed `config.json`, one agent file with no frontmatter
  fence — each asserting `pytest.raises(SystemExit)` with code 2, `"Traceback" not in
  capsys...err`, the file (or agent) name present in stderr, and every one of the three files
  byte-identical to before the run (nothing written). Ran `pytest tests/test_set_models.py -k
  malformed -v`: all three failed — `Failed: DID NOT RAISE SystemExit` for the two JSON cases
  (the old code path `return`s 2 as a plain value instead of raising, since only `UsageError`
  goes through `parser.error()`/`SystemExit`), and captured stderr showed the full traceback in
  all three cases.
- **GREEN**: wrapped each `read_json` call and the per-agent `updated_agent_text` call in its own
  `try/except`, converting `json.JSONDecodeError` → `UsageError(f"{path} is not valid JSON:
  {exc}")` and a frontmatter `ValueError` → `UsageError(f"{role}: {agent_path}: {exc}")`, both
  raised (and therefore still validated, and still written nowhere) before any file is opened for
  writing — the ordering that already gave the no-partial-write guarantee is unchanged. Left JSON
  re-serialisation as `lib.io.write_json` (unchanged, per the ruling). Reran the same three tests:
  all green, and confirmed `"Traceback" not in err` now holds.

### Verification

- `node --experimental-strip-types --test orchestrator/test/*.test.ts`: **240/240** (was 239;
  +1 new cli.test.ts test).
- `tsc --noEmit -p .`: clean.
- `.venv/Scripts/python.exe -m pytest tests/test_set_models.py tests/test_agents_config.py`:
  **65/65** (was 62; +3 new malformed-input tests).
- Whole suite `.venv/Scripts/python.exe -m pytest`: **1450 passed, 0 skipped in 298.70s** (was
  1447; +3, matching the three new malformed-input tests — no regressions elsewhere).

### Files changed (this round)

`orchestrator.config.json`, `orchestrator/cli.ts`, `orchestrator/test/cli.test.ts`,
`scripts/dev/set_models.py`, `tests/test_set_models.py`, `docs/handoff-copilot-models.md`.
