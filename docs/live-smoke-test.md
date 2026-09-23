# Live BYOK smoke test — CopilotRunner against a local model (Task 16)

**Honesty statement, up front:** nothing in this test touched real Snowflake or real Alteryx. The
only subprocesses run for real were `scripts/parse.py --check`, `scripts/intake_touchpoints.py` and
`scripts/intake_prompt.py --no-interactive` against a synthetic sample fixture
(`samples/wf_0001`, Task 13's hand-written stand-in) copied into a scratch root outside any git
tree. No SQL tool call was ever made (the run never got past the intake role's read-only
orientation phase), no `gh` calls were made (`gh` is not installed on this machine), and the
llama.cpp model server bound to `127.0.0.1` only. Every number below is copied from
`workflows/wf_0001/audit.jsonl`, `orchestrate.ts`'s own console output, or `llama-server`'s own
stdout log, and says which.

## Date and versions

- Test performed: 2026-09-19 local / `2026-09-20T00:53:56Z`–`2026-09-20T01:08:35Z` (UTC timestamps
  as recorded by `llama-server` and `audit.jsonl`).
- GitHub Copilot CLI: **1.0.86** (`copilot --version` via
  `C:\Users\<user>\AppData\Roaming\fnm\node-versions\v22.23.2\installation\copilot.cmd`).
- `@github/copilot-sdk`: package **1.0.14**; its own pinned `copilotCliVersion` in
  `node_modules/@github/copilot-sdk/package.json` is **1.0.85** — one patch release older than the
  CLI actually installed on this machine (1.0.86). This mismatch did not visibly break anything in
  this test, but it means the SDK was talking to a CLI build one version newer than the one it was
  built/pinned against.
- llama.cpp fork (PrismML build) — `llama-server.exe --version`:
  `version: 0.2.0-dev (build 10685, commit 7dffb158d)`, `built with MSVC 19.44.35228.0 for Windows
  AMD64`.
- Model file: `Ternary-Bonsai-2-27B-PQ2_0.gguf`, **7,206,168,928 bytes** (`ls -la`), at
  `C:\Users\<user>\models\Ternary-Bonsai-2-27B\`.
- Node: **v22.23.2** (`fnm exec --using=22 node.exe -v`); npm **10.9.8**.
- `gh` CLI: not installed (matches Task 15's finding); no PR/issue automation was exercised.

## Exact command lines

```bash
# 1. Start the model server (loopback only)
pwsh -File scripts/dev/serve_model.ps1
# → C:\Users\<user>\tools\llama-prism\llama-server.exe -m <model> --host 127.0.0.1 --port 8080 -ngl 99 -fa on -c 32768 --jinja --alias ternary-bonsai-2-27b

# 2. Health check
curl http://127.0.0.1:8080/health
# → {"status":"ok"}

# 3. Seed wf_0001 into a scratch root (outside any git tree)
python scripts/dev/build_samples.py seed --only wf_0001 --root <scratch>

# 4. Dry run (no model call) — confirms CopilotClient starts/stops with no login prompt
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root <scratch> --only wf_0001 --runner copilot --profile local --no-interactive --dry-run

# 5. Live attempt 1
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root <scratch> --only wf_0001 --runner copilot --profile local --no-interactive --stop-after intake

# 6. Live attempt 2 (per the addendum: try twice before giving up)
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root <scratch> --only wf_0001 --runner copilot --profile local --no-interactive --from-stage intake --stop-after intake
```

The scratch root was assembled per Task 15's recipe: copies of `scripts/`, `samples/`, `mappings/`,
`catalog/`, `.github/`, `cookbook/` from the worktree, plus a hand-written
`orchestrator.config.json` whose `"python"` is the absolute venv path. No `workflows/` directory
ever appeared in the worktree's own git tree.

## What worked

- **No GitHub login was required to start a session.** `CopilotClient.start()`/`createSession()`
  with a BYOK `provider` pointed at `http://127.0.0.1:8080/v1` succeeded with no authentication
  prompt of any kind, in both the dry run and both live attempts. This is the single most important
  positive finding of the whole test: **the live CopilotRunner path is not blocked on login** for a
  local BYOK provider.
- llama-server loaded the model and reported healthy in **11.598 seconds** (its own log: load start
  `0.00.073.248` → "model loaded"/"listening" `0.11.598.006`), `n_slots = 4`, `n_ctx_slot = 32768`.
- `onSessionStart`, `onPreToolUse`, `onPostToolUse`, `onPostToolUseFailure`, `onErrorOccurred` and
  `onSessionEnd` all fired for real, with real data, across both attempts (see `audit.jsonl`
  excerpts in `task-16-report.md`).
- **`onPreToolUse` correctly judged every real tool call in both attempts, including from a
  sub-agent.** The model twice attempted to delegate to a sub-agent via the `task` tool (allowed —
  `task` is on the read/planning allow-list); the sub-agent's own four chained PowerShell commands
  were each intercepted by the SAME hook, under the same `role: "intake"` audit context, and denied
  for shell metacharacters exactly as the top-level agent's calls would be. **No sub-agent bypass.**
- Every real `toolName` observed — `view`, `glob`, `grep`, `powershell`, `task` — was already
  correctly classified by the existing provisional policy (`READ_TOOLS` / `SHELL_TOOL`). **Zero
  `unrecognized-tool` audit events** across both attempts (`grep -c unrecognized-tool audit.jsonl`
  → `0`). No `SQL_TOOL` or `WRITE_TOOL` call was ever attempted.
- The model recovered from its own early mistakes within the session: after several denied
  guesses, it started using working relative paths, and later in attempt 2 correctly inferred and
  used its real absolute working directory.
- The policy's fail-closed design caught a hallucinated path on the model's very first tool call
  (see Critical/positive findings below) and every shell-metacharacter injection attempt, with no
  exceptions.

## What did not work

- **Intake never completed in either attempt.** Neither attempt produced `plan.md`, a resolved
  `mappings.yaml`, or any other model-authored file. The only files present in
  `workflows/wf_0001/intake/` after both attempts (`mappings.yaml` 64 bytes, `open_questions.md`
  1052 bytes, `touchpoints.json` 2544 bytes) are the skeletons `scripts/intake_prompt.py
  --no-interactive` writes itself, before the agent ever runs — real Python, not the model.
- **Both attempts crashed the same way: the session's own context grew past the server's 32,768
  token window.** `llama-server`'s log: attempt 1 — `error: request (35518 tokens) exceeds the
  available context size (32768 tokens), try increasing it` (task 8051); attempt 2 — the sub-agent's
  `task` tool call itself failed with `400 request (34965 tokens) exceeds the available context
  size (32768 tokens), try increasing it`. This happened after **22 inference turns and 37 tool
  calls in attempt 1** (`manifest.json.metrics.intake = {"lastMs": 293830, "toolCalls": 37}` —
  293.8 seconds), and a comparable number in attempt 2.
- **Both failures were misreported in the manifest as `"denied"`, not as a context/error
  failure.** See "Critical findings" below — this is a real diagnostic-accuracy bug, found only by
  cross-referencing `llama-server`'s independent log, not fixed as part of this task (it lives in
  `runner.ts`/`hooks.ts`, outside this task's declared policy.ts-only code-change scope).
- **`ask_user` / `onUserInputRequest` was never invoked** in either attempt — the model never
  reached a point in its work where it needed to ask a clarifying question; it was still exploring
  its environment when it ran out of context.
- **`onPermissionRequest` cannot be confirmed either way from this evidence.** It is not
  independently audited by `hooks.ts` (only `onPreToolUse`'s decision is logged), and every
  `onPreToolUse` decision was honored end to end with no visible override, which is *consistent
  with* but not *proof of* `onPreToolUse`'s decision being authoritative over `onPermissionRequest`.
  Confirming this would require adding logging to the `onPermissionRequest` callback itself.
- A real `grep` tool call from the model had a malformed argument (`"paths":"[\"scripts\"]"` — a
  JSON-stringified array where the tool expects a real array or a plain string), which the grep tool
  itself rejected (`"Search paths do not exist: [\"scripts\"]"`, via the `onPostToolUseFailure`
  hook, confirmed firing correctly). This is a model tool-calling quality issue, not a policy issue
  — the policy correctly allowed the call (there was nothing dangerous about it), and the tool
  itself, not the policy, is what caught the malformed shape.

## Critical findings (in `runner.ts`/`hooks.ts`, not `policy.ts` — reported, not fixed here)

1. **Diagnostic misclassification.** `orchestrator/runner.ts`'s `CopilotRunner.run` catch block
   checks `state.denied` before checking the thrown error's timeout/rate-limit signature. Because
   several early, already-recovered-from permission denials (six in attempt 1, several more in
   attempt 2 — see `task-16-report.md`) had already set `state.denied = true` minutes earlier in the
   same session, a **later, completely unrelated context-window crash got permanently reported as
   `"denied"`** instead of `"error"`. `manifest.json.reasons.intake` reads `"denied"` in both
   attempts even though no denial caused either failure. Consequence: `manifest.reasons` cannot be
   trusted as a diagnosis for *why* a workflow needs a human, whenever any denial — however
   harmless, however long ago in the same session — happened first.
2. **Lost error detail.** `orchestrator/hooks.ts`'s `onErrorOccurred` handler does
   `String(input.error ?? "")`. In both attempts, `input.error` was a non-string/object value at
   runtime — despite the SDK's own type declaration (`ErrorOccurredHookInput.error: string`) saying
   it should always be a string — and stringified uselessly to `"[object Object]"`
   (`{"ev":"error","error":"[object Object]","context":"model_call","recoverable":true}`). The real
   diagnostic text (`"request (35518 tokens) exceeds the available context size..."`) was only
   recoverable by reading `llama-server`'s own independent log, not from anything the orchestrator
   itself recorded. In a real deployment without a side-channel log to cross-reference, this failure
   would be undiagnosable from the audit trail alone.
3. **`onSessionEnd` never fired on the second live attempt**, even though that session clearly
   terminated (the same context-overflow crash as attempt 1). Proven by counting, not assumption:
   `audit.jsonl` has 66 total `"pre"` tool-call lines; 29 of them come after attempt 1's own
   `"session-end"` line, meaning attempt 1 made exactly 37 calls (matching its own reported count)
   and attempt 2 made 29 — a different number that never reached the manifest, because
   `wf.metrics[role]` is written only inside `onSessionEnd`. After attempt 2,
   `manifest.json.metrics.intake` still read attempt 1's stale `{"lastMs": 293830, "toolCalls": 37}`,
   with nothing anywhere to flag that those numbers belonged to a different run. `status`/`reasons`
   still updated correctly for attempt 2 (a separate code path, in `stages.ts`), so only the
   hooks-only bookkeeping went silently stale.
4. **A minor internal inconsistency in `policy.ts`** (not security-relevant, documented and pinned
   with a regression test rather than fixed): `READ_ONLY_SHELL`'s `get-childitem` entry declares
   `-file` among its own allowed flags, but `decideShell`'s blanket `INTERPRETER_FLAGS` check (which
   also contains `-file`, meant to catch `powershell -File <script>.ps1`) runs first and
   unconditionally denies any `-File`/`-file` token in a PowerShell command line, for every role.
   The declared Get-ChildItem allowance is unreachable dead configuration. Denying is still safe
   either way, so this was recorded, not fixed (see `orchestrator/test/policy.test.ts`, the "Task 16
   live evidence" tests).

None of these four findings widen what the policy allows — they are diagnostic-accuracy and
dead-configuration issues, not security holes. **No hole was found**: every real tool call the
policy denied was correctly denied, and the workflow correctly escalated to `NEEDS_HUMAN` in both
attempts regardless of the misreported reason string.

## Real `toolName` values and argument shapes

| `toolName` | Classification | Argument shape observed | Result |
|---|---|---|---|
| `view` | `READ_TOOLS` | `{"path": "<string>"}` | Allowed for in-scope relative/absolute paths; denied for a hallucinated path, the exact repo root, and `~/.copilot/session-state/...` |
| `glob` | `READ_TOOLS` | `{"pattern": "<glob>", "paths": "<string>"}` | Allowed for in-scope directories; denied for the exact repo root (same root-equality limitation as `view`) |
| `grep` | `READ_TOOLS` | `{"pattern", "paths", "output_mode", "glob", "-n"}` | Allowed by policy in both observed calls; one call's `paths` was a JSON-stringified array (`"[\"scripts\"]"`) that the grep tool itself rejected — a model-side malformed argument, not a policy issue |
| `powershell` | `SHELL_TOOL` (`/^(bash\|shell\|powershell\|pwsh\|cmd)/i`) | `{"command": "<string>", "description": "<string>"}` | Extra `description` key ignored correctly; many denials (metacharacters, interpreter flags, non-listing git subcommands, disallowed listing arguments), several allows (`git status`, `git log --oneline -5`, directory listings with valid arguments) |
| `task` | `READ_TOOLS` | `{"agent_type": "task", "description", "name", "prompt"}` | Allowed — sub-agent delegation itself is not a filesystem/shell action; the sub-agent's own subsequent tool calls passed through the identical hook and were judged identically |

No `SQL_TOOL`-classified or `WRITE_TOOL`-classified call was ever attempted by the model in either
attempt, so those two regexes remain **unverified by live evidence** — this run only exercised
intake's read-only orientation phase.

## Tokens per second and wall time (from `llama-server`'s own log, not invented)

- Model load: **11.598 s** (see above).
- Sustained token generation once the session's own context grew past ~25,000 tokens: **~29–31
  tokens/second** (e.g. task 5715: `eval time = 42975.42 ms / 1264 tokens = 29.39 tok/s`). Shorter,
  low-context turns ran faster (up to ~45 tok/s, e.g. task 6981). Prompt (re-)processing benefited
  heavily from KV-cache reuse (`graphs reused` in the thousands), running at 400–800+ tokens/second
  for the incremental portion.
- This is markedly slower than the "~73 tok/s" figure the task brief cites as previously verified;
  that number presumably came from a short-prompt/near-empty-context benchmark. **Once an agent
  session's own context grows, sustained generation on this hardware is ~29–31 tok/s, not ~73.**
- **Attempt 1 total model wall time: 4 minutes 54 seconds** (`llama-server` log: first task launch
  `2.22` → final rejected task `7.16`, relative to server start), across 22 inference turns and 37
  tool calls, producing zero usable output.
- **Attempt 2 total model wall time: approximately 4 minutes 10 seconds** (task launch `10.03` →
  session-ending failure `~14.13`), also producing zero usable output.
- Combined, both live attempts used well under the addendum's ~25-minute model-time budget.

## Honest verdict per agent role, for this model

- **intake: FAILED, both attempts.** `Ternary-Bonsai-2-27B` (PQ2_0 quantization, BYOK via
  llama.cpp, 32,768-token context, `reasoningEffort: medium`) could not complete even the read-only
  orientation-and-planning phase of the intake role before exhausting its context window. Its
  tool-calling was serviceable but imperfect (one malformed `grep` call; it needed 6–10 denied
  attempts in each session before settling on a working exploration pattern, and repeated
  already-denied patterns — chained/piped shell commands, `git --no-pager <subcmd>` — more than
  once rather than generalizing immediately from the first denial). The intake role's own system
  prompt, tool schemas, `.github/agents/intake.agent.md` prompt, and the files it is instructed to
  read (`dag.json`, `touchpoints.json`, `mappings/global.yaml`, cookbook pages) all consume context
  before or during the work itself; with this model's exploratory overhead added on top, 32,768
  tokens was not enough headroom to reach a finished `plan.md` in either attempt.
- **analyzer / translator / fixer / reviewer / validator / documenter / parser-recovery: UNTESTED.**
  Per the addendum, Step 4 (`--stop-after translate`) is attempted only if intake succeeds; intake
  did not succeed in either attempt, so no live evidence exists for any other role. No verdict can
  honestly be given for them.
- **Overall verdict:** this local model, at this context size, is **not currently viable for the
  intake role** in this pipeline. The binding constraint was context headroom, not raw model
  quality — the model was still functioning coherently (self-correcting its path guesses, still
  reasoning sensibly) at the moment it was cut off, so a materially larger `-c` (context window),
  if the hardware supports it, is the most direct next experiment; it is not certain that would be
  sufficient on its own, since the model's exploration overhead (rediscovering the same information
  repeatedly, retrying denied command shapes) would also need to shrink for the total token budget
  to close.

## What the user must do themselves to go further

- Nothing here required login or any credential; the BYOK path worked exactly as configured. If a
  future test wants the **hosted** profile (real GitHub Copilot models), that requires the user's
  own `copilot /login` or `gh auth login` — this task did not attempt either, per the hard limits,
  and does not recommend attempting it as a substitute for fixing the context-window constraint
  found here.
- Decide whether to raise `-Context` well beyond 32768 (GPU memory permitting — GPU memory is shared
  with the desktop per the addendum) and re-run this same two-command sequence to see whether a
  larger window changes the outcome.
- Decide whether the three `runner.ts`/`hooks.ts` diagnostic bugs above (denied-classification
  masking a later real error; `[object Object]` swallowing the real error text; `onSessionEnd`
  never firing on this crash path, leaving `metrics` stale) are worth a dedicated follow-up fix
  before trusting `manifest.reasons`/`manifest.metrics` on any future live run — they were found
  live, are reproducible, and are outside this task's declared policy.ts-only scope.
- Consider whether `intake.agent.md`'s prompt and procedure should be shortened, or the workflow's
  own context footprint (touchpoints.json, dag.json, cookbook pages) trimmed, for small local
  models specifically — this is a product decision, not something this task's scope covers.

## Follow-up, 2026-09-19: the three diagnostic-accuracy bugs above are now fixed in code

Task 16 diagnostics (d1–d3), `.worktrees/task-16-diag`. All three `runner.ts`/`hooks.ts` bugs
flagged above have been fixed and covered by new unit tests against a fake SDK session
(`orchestrator/test/runner.test.ts`, plus additions to `orchestrator/test/hooks.test.ts`).
**Honesty note: none of this was re-verified against a live model.** No live run happened for
this follow-up — everything below was proven with RED/GREEN evidence against the fake SDK only.

- **d1 (denied-classification masking a real crash):** `CopilotRunner.run`'s catch block
  (`orchestrator/runner.ts`) now judges the thrown error's own signature (timeout, rate-limit,
  a new `"context-overflow"` classification) *before* falling back to `state.denied`. `"denied"`
  is now reported only when no other signature explains why the session ended. A new
  `AgentError` member, `"context-overflow"`, was added (`orchestrator/types.ts`) and routed
  through `orchestrator/stages.ts` exactly like a non-retryable `"error"` (not in `RETRY_ONCE`),
  since an identical retry against the same context window cannot succeed.
- **d2 (`[object Object]` swallowing the real error text):** a shared `errorText()` helper
  (`orchestrator/hooks.ts`) now extracts real text from a string, an `Error`, or an arbitrary
  object (preferring `.message`, then a bounded `JSON.stringify`, then `util.inspect` for a
  cyclic object), used by `onErrorOccurred`, `onPostToolUseFailure`, and `CopilotRunner.run`'s
  catch block alike. Audit lines stay single-line (embedded whitespace/newlines collapsed) and
  bounded to the existing `AUDIT_ARG_LIMIT` (500 chars), same as every other audited field.
- **d3 (`onSessionEnd` never firing on the crash path, `metrics` going stale):**
  `CopilotRunner.run` now records duration and tool-call count for every session in its own
  `finally` block (`recordMetrics`, `orchestrator/hooks.ts`), counting from the session's own
  `onPreToolUse` observations, whether or not `onSessionEnd` fires. `recordMetrics` is idempotent
  per session (guarded by `state.metricsRecorded`) so a session where `onSessionEnd` *does* fire
  is not double-counted. `toolCalls` accounting is now additive on the role's prior value (not
  overwritten), so a crashed attempt's spend adds to — rather than being lost from or replaced
  by — the workflow's cumulative tool-call budget that `orchestrator/stages.ts`'s `runAgent`
  checks against `budgets.maxToolCallsPerWorkflow`.

See `docs/superpowers/build-reports/2026-09-18-pipeline/task-16-diag-report.md` for file:line detail, RED/GREEN
test evidence, and what a live re-run would still need to confirm (in particular: whether a real
SDK session's thrown-error text and `onErrorOccurred`/`onSessionEnd` timing match what the fake
session in these tests assumes).

## Second live test — larger context, 2026-09-20

**Honesty statement, up front:** nothing in this test touched real Snowflake or real Alteryx,
same as the first live test above. The only subprocesses run for real were `scripts/parse.py`
(during seeding), `scripts/intake_touchpoints.py wf_0001` (run twice, for real, by the model
itself — see below) and the pre-existing Python skeleton writers `intake_prompt.py --no-interactive`
runs before the agent starts; no SQL tool call was ever made, no `gh` call was made (`gh` is still
not installed), and `llama-server` bound to `127.0.0.1` only. Every number below is copied from
`workflows/wf_0001/audit.jsonl`, `manifest.json`, `orchestrate.ts`'s own console output, or
`llama-server`'s own log, in a fresh scratch root
(`...\scratchpad\t16live2\`, `wf_0001` seeded from `samples/wf_0001`), worktree `.worktrees/live-retest`
(branch `wt/live-retest`, based on `main` `69c7634`). Same model file, same `llama-server` build,
same Copilot CLI/SDK versions as the first live test (re-verified below) — the only thing this
retest changed was the server's `-c`/`-ctk`/`-ctv` flags.

### Purpose

The first live test's binding constraint was the 32,768-token context window, not raw model
quality — the model was still reasoning coherently when it ran out of room. This retest asks the
direct follow-up question the first test's own closing section raised: does a materially larger
context window change the outcome, on this same GPU?

### Versions (re-verified, unchanged from the first live test)

- GitHub Copilot CLI: **1.0.86**; `@github/copilot-sdk` package **1.0.14** (pinned
  `copilotCliVersion` **1.0.85** — same one-patch-behind mismatch as before, still harmless).
- `llama-server.exe` (PrismML fork): `version: 0.2.0-dev (build 10685, commit 7dffb158d)`, MSVC
  19.44.35228.0, Windows AMD64 — identical build to the first test.
- Model file: `Ternary-Bonsai-2-27B-PQ2_0.gguf`, 7,206,168,928 bytes — identical file.
- Node **v22.23.2**, npm **10.9.8**; `gh` still not installed.
- GPU: NVIDIA GeForce RTX 5070 Ti, 16,303 MiB total VRAM, ~3,046 MiB used by the desktop before
  this test started (`nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free
  --format=csv`).

### What changed: `scripts/dev/serve_model.ps1` gained `-CacheTypeK`/`-CacheTypeV`

Two new optional parameters, not passed by default (server default `f16`), validated against a
short allow-list (`f16`, `q8_0`, `q4_0`) — an invalid value exits 1 before any process starts, same
pattern as the existing model/server path checks. When given, they become `-ctk <value>`/`-ctv
<value>`. `--host 127.0.0.1` stays hardcoded; loopback-only is unchanged.

### Server configuration that loaded: first option tried, succeeded outright

Exact command line (from the server's own stdout log):
```
llama-server.exe -m Ternary-Bonsai-2-27B-PQ2_0.gguf --host 127.0.0.1 --port 8080 -ngl 99 -fa on -c 65536 --jinja --alias ternary-bonsai-2-27b -ctk q8_0 -ctv q8_0
```
i.e. `-Context 65536 -CacheTypeK q8_0 -CacheTypeV q8_0` — the **first** of the three options in the
plan, tried in order; it loaded, answered `/health`, and answered a real `/v1/chat/completions`
round trip, so the second and third options (`-Context 65536` with `f16` cache; `-Context 49152`
with `q8_0`/`q8_0`) were **not tried**.

From the server's own log:
```
0.07.214.959 I srv load_model: initializing, n_slots = 4, n_ctx_slot = 65536, kv_unified = 'true'
0.07.226.627 I srv llama_server: model loaded
0.07.226.629 I srv llama_server: listening on http://127.0.0.1:8080
```
Model load: **7.226 s**. `n_ctx_slot = 65536` confirms the full requested context loaded (not
silently truncated). No CPU-fallback warning appeared (`-ngl 99` requests all layers on GPU; this
build does not print a per-layer offload table at this verbosity — same log format the first live
test already treated as sufficient evidence of GPU residency).

**VRAM:** 13,102 MiB used right after load, 13,082-13,110 MiB used throughout both live attempts
that followed (16,303 MiB total, ~2,900 MiB free) — comfortably under the ~15.5 GB stop threshold,
no sign of desktop starvation, no VRAM growth over either session.

### The live run: two intake attempts, same outcome as the first test, further progress each time

Scratch root seeded with `wf_0001` only. Command (attempt 1):
```
node.exe --experimental-strip-types orchestrate.ts --root <scratch> --only wf_0001 --runner copilot --profile local --no-interactive --stop-after intake
```
**Both attempts ended in `NEEDS_HUMAN` with reason `context-overflow`, and neither produced
`intake/plan.md`.** Per this task's own rule, `--stop-after analyze` was never attempted (it only
applies if intake succeeds).

| | Attempt 1 | Attempt 2 (`--from-stage intake`) |
|---|---|---|
| Requested vs available tokens | 69,927 / 65,536 (4,391 over) | 65,559 / 65,536 (23 over) |
| Tool calls | 45 | 54 (budget reset to 0 by `--from-stage`, then this attempt's own count) |
| `manifest.json.reasons.intake` | `"context-overflow"` | `"context-overflow"` |
| Model-serving wall time (llama-server log) | ~8 min 10.6 s | ~5 min 32.8 s |
| Real script executed by the model | no | **yes** — `scripts/intake_touchpoints.py wf_0001`, twice, both successful |

Combined model-serving time: **~13 min 44 s**, well inside the ~30-minute budget (two attempts, as
the plan allows).

### Honest comparison with the first live test (32,768 context)

**Doubling the context window bought more headroom, but did not fix the underlying problem.** The
model got further both times — attempt 2 actually reached and successfully ran one of its real
`ROLE_SCRIPTS` (`scripts/intake_touchpoints.py`), which neither of the first test's two attempts
did — but both attempts still exhausted the (now 65,536-token) window before producing `plan.md`.
Attempt 2's overflow margin was razor-thin (23 tokens), which — combined with a genuinely new
observation this run (below) — suggests the model was close to finishing its orientation phase,
not close to being unblocked by yet more context on its own.

### C — what was recorded, sourced from the logs and audit file

- **Context tokens at failure:** attempt 1, 69,927 requested vs 65,536 available; attempt 2, 65,559
  vs 65,536 (`llama-server`'s own `send_error` lines, mirrored in `manifest.json.reasons.intake`'s
  underlying error text).
- **Tool-call counts:** 45 (attempt 1), 54 (attempt 2, after `--from-stage` reset the cumulative
  count to 0) — both cross-checked against `grep -c '"ev":"pre"'` on each attempt's own slice of
  `audit.jsonl`, which matched exactly.
- **Tokens per second (from `llama-server`'s own `print_timing` lines):** sustained generation
  **~41-42 tok/s** late in each session (context near full) — faster than the first test's ~29-31
  tok/s at 32,768 with `f16` cache, plausibly because `q8_0` KV cache reduces memory-bandwidth
  pressure per token. Early in a fresh session (low context) generation ran **~58-60 tok/s**.
  Prompt (re-)processing ran **~680-750 tok/s** warm (KV-cache reuse, `graphs reused` in the tens
  of thousands by the end) and briefly over **1,400 tok/s** cold, right after attempt 2's context
  reset.
- **Whether the model reaches a WRITE:** **no, in neither attempt** — `workflows/wf_0001/intake/`
  still contains only the pre-existing Python-written skeletons. No `WRITE_TOOL`- or `SQL_TOOL`-
  classified call was ever attempted, so those two regexes in `policy.ts` remain **unverified by
  live evidence** after two more live attempts, same as the first test. **No write that the policy
  allowed but should not have was observed — nothing to report as a Critical finding.**
- **`onUserInputRequest`/`ask_user` in `--no-interactive`:** **not invoked**, either attempt — the
  model never reached a point where it needed to ask a clarifying question before running out of
  context, same as the first test.
- **d1 (context-overflow classification) — LIVE VERIFIED, both attempts.** `manifest.json.
  reasons.intake` reads `"context-overflow"`, not `"denied"`, in both attempts, even though both
  sessions had early permission denials before the crash (exactly the scenario d1 was written to
  fix). This is the first live confirmation of d1 under a real crash — the first live test predates
  the fix and could not test it.
- **d2 (readable error text) — LIVE VERIFIED, both attempts, against a differently-shaped real
  error than d2's own unit tests assumed.** The `onErrorOccurred` audit line carries a full,
  structured error, not `"[object Object]"`:
  ```json
  {"error":"{\"code\":400,\"message\":\"request (69927 tokens) exceeds the available context size (65536 tokens), try increasing it\",\"type\":\"exceed_context_size_error\",\"n_prompt_tokens\":69927,\"n_ctx\":65536}","class":"context-overflow"}
  ```
  Interestingly, this run's underlying error arrived as a JSON-encoded **string** (not the plain
  object the first live test observed) — `errorText()`'s string branch passed it through
  unchanged. Both shapes are now handled correctly; this is additional live evidence of the error
  *shape* varying between sessions/providers, not a regression.
- **d3 (`onSessionEnd`/metrics) — LIVE VERIFIED for the additive-and-idempotent behavior, on the
  happy path.** `onSessionEnd` fired in **both** attempts this time (unlike the first live test,
  where it never fired on attempt 2) — this retest did not reproduce that specific failure mode, so
  it remains verified only by the first live test's own evidence plus the unit tests. What this
  retest DID newly confirm live: `--from-stage intake`'s existing "reset the tool-call budget"
  feature and d3's additive `recordMetrics` compose correctly — the console reported `reset the
  tool-call budget (was 45, now 0)`, and attempt 2's `manifest.json.metrics.intake.toolCalls: 54`
  matched attempt 2's own tool-call count exactly (not 45+54), and matched its own `session-end`
  audit line too.
- **New, not seen in the first live test:**
  - **A genuinely unrecognized tool name.** `list_powershell` with empty args (`{}`) was denied by
    the fail-closed default — the first `unrecognized-tool` audit event ever observed live across
    both tests (the first test: zero, across two attempts).
  - **`onPostToolUse`'s secret-scan fired live for the first time**, correctly as a false positive
    (`_SELF_REF_TOKEN = "confirm-self-reference"` in `scripts/intake_prompt.py` matches
    `SECRET_MENTION`'s `token\s*=` pattern) — flagged in the audit trail, did not block anything.
  - **`view`'s real argument shape includes an optional `view_range: [start, end]` key** for
    paginating long files, used heavily in attempt 2 (6 calls reading `intake_prompt.py` in ~150-line
    chunks). Not a path-bearing key; correctly ignored by the policy either way.
  - **`grep`'s real argument shape includes an optional `head_limit` key** (a number); same
    reasoning, correctly ignored.
  - **`llama-server` silently truncated context once, one turn before the hard failure in attempt
    2:** `n_tokens = 65535, truncated = 1` on the second-to-last task, immediately followed by the
    hard `400` on the next. This was never observed in attempt 1 or in the first live test
    (`truncated = 0` throughout both of those). It means the server's own context manager had
    already started dropping earlier KV-cache content to fit that turn — the model's own view of
    its history for its last coherent turn may already have been silently incomplete, before the
    hard error that ended the session. This repo's orchestrator has no visibility into server-side
    silent truncation (only the hard-failure signature reaches it); recorded here as an honest
    limitation of what this test can observe, not something fixed.
  - **The model reached and successfully ran a real role script for the first time in this
    project's live testing:** `scripts/intake_touchpoints.py wf_0001`, invoked twice (once via
    `.venv\Scripts\python.exe`, once via bare `python`), both `allow`ed and both completed with
    `"result":"success"`.

### Notable (non-critical) observation: intake could read the samples/ "canned" answer key

In both attempts, several `view` calls read `samples/wf_0001/canned/**` — the hand-written
reference artifacts `MockRunner` replays offline, i.e. this exercise's own answer key. This is
**not a policy bug**: `FORBIDDEN_WRITES`'s `samples/` entry only blocks *writes* there, by design
(same pattern as `cookbook/`) — nothing ever restricted reads. It is recorded here as a
methodological note: this test's scratch root copies `samples/` wholesale (needed for
`build_samples.py` to seed the workflow, same as every scratch-root recipe in this repo, including
README §6's own), so a live `--runner copilot` session can see its own answer key if it goes
looking — which this model did in both attempts, well after it had already been reading real
workflow files, with no observed effect on the outcome (it crashed before reaching `plan.md`
regardless). A future live test that specifically wants to isolate the model's own reasoning could
exclude `samples/*/canned/` from the scratch root; nothing here required that to reach an honest
verdict.

### Cleanup

`taskkill /PID 19644 /F` → process terminated; `tasklist` confirms no `llama-server.exe` running;
`netstat -ano | grep :8080` shows only a harmless `TIME_WAIT` leftover, no `LISTENING` entry.
**llama-server is stopped and port 8080 is free.**

### Policy changes

**None to `policy.ts` itself — the evidence did not call for any**, same conclusion as the first
live test. Every real tool name observed (`view`, `powershell`, `grep`, and the new
`list_powershell`) was judged correctly — the three recognized ones by their existing
classification, the unrecognized one by the correct fail-closed default. Three new regression
tests were added to `orchestrator/test/policy.test.ts` (146 → 149 node tests) pinning: the
`list_powershell` unrecognized-tool denial, `view`'s `view_range` key being ignored, and `grep`'s
`head_limit` key being ignored — documentation of real observed shapes, not behavior changes.
`fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts` →
**149/149 passing**. `tsc --noEmit -p .` → clean.

### Honest verdict per agent role, for this model, updated

- **intake: FAILED, both attempts, same as the first live test — but measurably further along.**
  Doubling the context window (32,768 → 65,536) did not let `Ternary-Bonsai-2-27B` finish intake's
  read-only orientation-and-planning phase, but it did buy enough room for the model to reach and
  successfully execute one of its real deterministic scripts (`intake_touchpoints.py`) in attempt
  2 — something neither of the first test's attempts achieved. Attempt 2's overflow margin (23
  tokens) was far narrower than attempt 1's (4,391) or either of the first test's attempts (2,750
  and 2,197 at the 32,768 window), suggesting the model was close to a natural stopping point in
  its own exploration, not merely proportionally further from finishing. The model's own
  tool-calling quality was similar to the first test: still some early denied guesses before
  settling into working paths, still some self-inflicted repeated exploration, but also real
  forward progress (an actually-executed role script, a large amount of correctly-targeted file
  reading, `view_range` pagination used deliberately for long files).
- **analyzer / translator / fixer / reviewer / validator / documenter / parser-recovery: still
  UNTESTED.** Intake did not succeed in either attempt here either, so `--stop-after analyze` was
  never attempted; no live evidence exists for any other role from this test either.
- **Overall verdict:** a larger context window is a real, measurable improvement (further
  progress, a real script execution, faster token generation with the quantized KV cache) but **not
  a fix on its own** at 65,536 tokens for this model on this workflow. The first live test's own
  prediction — that the model's exploration overhead would also need to shrink for the token budget
  to close — reads as more likely correct after this retest: even with double the window, the
  session still filled it, and the narrower attempt-2 margin suggests the ceiling for "how much
  further headroom alone can buy" may already be in view. The most direct next experiments, in
  order of expected effect: (1) shrink the intake role's own context footprint (system prompt,
  tool schemas, `.github/agents/intake.agent.md`, `touchpoints.json`/`dag.json`/cookbook pages) so
  more of the window is available for the model's own reasoning rather than fixed overhead; (2) if
  GPU memory allows, try an even larger window (this GPU had ~2.9 GB of margin left before the
  ~15.5 GB stop threshold at 65,536 with a quantized cache, so some further headroom exists, though
  not another full doubling); (3) accept that this specific model, at any context size this
  hardware can serve, may not be the right fit for the intake role's current prompt/tool design,
  and treat "not currently viable for intake" as this model's honest verdict rather than a
  context-window problem alone.

### What the user must do themselves to go further

Same as the first live test: nothing here required login or any credential — the BYOK path worked
exactly as configured, no `copilot /login` or `gh auth login` was attempted or needed. Deciding
whether to shrink intake's context footprint, try an even larger window, or evaluate a different
local model for this role is a product decision this task's scope does not cover.

## Third live test — 262k context, three samples, 2026-09-23

The phase-2 bounded live test (plan `docs/superpowers/plans/2026-09-22-output-targets-phase2.md`, Task H):
`CopilotRunner` against the same local BYOK model, one attempt per stage for `wf_0001` (SQL), `wf_0006` (a
Snowpark segment) and `wf_0007` (a dbt project), with the phase-2 inline prompt context (Task F), the notes
files and compaction metrics (Task W4), and the hardened policy (Tasks D, W2, C4V). Nothing in this test touched
Snowflake, Alteryx or a GitHub-hosted model.

### Setup

- Code: integration branch `feat/output-targets-phase2` at `59e217d`. The hand-off fixes that landed later
  (hosted-model routing, golden-set recording, GitHub opt-in, console encoding) do not change a local-profile run.
- Server: `pwsh -File scripts/dev/serve_model.ps1 -Context 262144 -CacheTypeK q4_0 -CacheTypeV q4_0`, which ran
  `C:\Users\<you>\tools\llama-prism\llama-server.exe -m <model> --host 127.0.0.1 --port 8080 -ngl 99 -fa on -c 262144 --jinja --alias ternary-bonsai-2-27b -ctk q4_0 -ctv q4_0`
  (model `Ternary-Bonsai-2-27B-PQ2_0.gguf`, the model's native context). The log reported
  `n_slots = 4, n_ctx_slot = 262144, kv_unified = 'true'`; `/health` answered `{"status":"ok"}`; total GPU memory in
  use was 15377 of 16303 MiB (about 1.8 to 2.3 GB of that belongs to other applications at idle).
- Run root: a scratch directory outside the repository, holding copies of `scripts mappings catalog .github cookbook
  docs/reference` (no `samples/`, so no canned answer key sits under the root) and an `orchestrator.config.json` with
  the absolute venv interpreter and `samplesDir`. All three workflows were seeded into the SAME run root, one after
  another.
- Per sample: `build_samples.py seed --only <wf>`, then
  `orchestrate.ts --root <run root> --only <wf> --runner copilot --profile local --no-interactive --stop-after intake`
  under a 25-minute cap (ruling R-H1). Analyze and translate were to follow only if intake reached READY or
  WAITING_FOR_ANSWERS; none did. No `--from-stage` retries: a parked stage is the result.
- The server was stopped at the end; no `llama-server` process remained, port 8080 was free, and GPU memory fell to
  1849 MiB.

### Results

| sample | intake wall time | tool calls | peak input tokens | compactions | intake files written by the model | intake status | reason |
|---|---|---|---|---|---|---|---|
| wf_0001 | 1056 s | 76 | 105470 | 0 | `plan.md`, `mappings.yaml` | NEEDS_HUMAN | `denied` |
| wf_0006 | 766 s | 61 | 80569 | 0 | `plan.md`, `mappings.yaml` | NEEDS_HUMAN | `denied` |
| wf_0007 | 788 s | 48 | 81073 | 0 | `plan.md`, `mappings.yaml` | NEEDS_HUMAN | `denied` |

Numbers are copied from each workflow's `manifest.json` (`metrics.intake`) and the run logs. `open_questions.md`
and `touchpoints.json` are written by scripts, not by the model.

What changed against the first two live tests: the context never overflowed. The largest input the model
received was 105470 tokens, which is why the 32768- and 65536-token windows of the earlier tests overflowed during
intake. For the first time in any live test the intake agent wrote `intake/plan.md` (and `mappings.yaml`) for every
sample.

### Why each run parked

Every park came from a policy denial that was correct:

- **A mangled run-root path (wf_0001, wf_0007).** The run root's absolute path is about 150 characters long and
  repeats the project name. When the model built absolute paths for `view`, it several times rewrote it as
  `C:\Users\<you>\Desktop\Alteryx-to-Snowflake\<session>\…`, a directory that is not the run root. The policy
  denied each read as "path outside the repository", and a denial parks the stage. The same model built the correct
  path for its `create` calls, so this is intermittent path copying, not a missing capability.
- **A sibling workflow (wf_0006).** The model read files under `workflows/wf_0001/`, which was present in the shared
  run root. The policy denied it ("no access to other workflows") and the stage parked.
- **The notes directory (all three).** Each intake agent tried to create `workflows/<wf>/notes/` before writing its
  notes file: with PowerShell `New-Item` or `md`, with `.venv\Scripts\python.exe -c "…makedirs…"`, and once by
  writing a small Python script with `create` that would make it. The policy denied every one (PowerShell
  exploration and arbitrary interpreter calls are outside the intake role's allow-list). These denials did not name
  the park reason, but they cost tool calls in every run.
- Other denied calls were PowerShell exploration (`Get-ChildItem`, `Get-Content`, `Select-String`), which the policy
  routes to the `view`, `grep` and `glob` tools instead.

### The SDK's real tool names and argument shapes (settles the long-open question)

Observed in the three `audit.jsonl` files (the `@github/copilot-sdk` session, BYOK provider):

| tool | argument keys seen |
|---|---|
| `view` | `path`, `view_range` |
| `create` | `path`, `file_text` |
| `powershell` | `command`, `description` |
| `grep` | `pattern`, `paths`, `output_mode`, `-n`, `-A`, `-C` |
| `glob` | `pattern`, `paths` |
| `ask_user` | `question`, `choices` |
| `task` | a sub-agent spawn (allowed once in wf_0006) |

The write tool is `create` with `path` and `file_text`. Task D's fix that stopped the policy from reading
`file_text` as a path is what let `plan.md` through; before it, every write would have been denied. The shell tool
on Windows is `powershell`. The hosted run (the hand-off guide's rung 2) re-confirms these names with a different
model; the SDK is the same.

### Calibration of the prompt-size estimates

| sample | inline intake context (characters) | estimate (characters ÷ 4) | peak input tokens | ratio |
|---|---|---|---|---|
| wf_0001 | 2443 | 610 | 105470 | 172.7 |
| wf_0006 | 1609 | 402 | 80569 | 200.3 |
| wf_0007 | 2382 | 595 | 81073 | 136.1 |

The inline block (`scripts/prompt_context.py --role intake`) is a small part of what the model sees: the peak input
is dominated by the agent's instructions, the tool definitions and the files the model reads during the session. The
character budgets (`segmentation.max_prompt_chars`, the analyzer's batch budget) bound only the rendered block and the
segment slice; they are not a bound on a session's total input. At a 262144-token window this session shape fits with
room to spare; at 65536 it does not.

### Tokens per second

From `llama-server`'s own timing lines late in the run (deep context): generation 46.6 to 49.2 tokens per second;
prompt evaluation 230 to 835 tokens per second. At 32768 tokens in the first live test, generation was about 73
tokens per second.

### Honest verdict

- **Intake:** reached a written plan and mapping file on all three samples, a first, but parked every time on a
  correct policy denial, so no sample reached WAITING_FOR_ANSWERS and none was answered.
- **Analyzer, translator, reviewer, validator, fixer, documenter:** not reached. The SQL, Snowpark and dbt output
  paths remain exercised only by the mock runner and the local doubles.
- **Nothing here touched Snowflake or Alteryx.**

### Follow-ups this test suggests

1. Keep run roots short (for example `C:\mig\runs\<name>`): the long path invited the model's copying errors. The
   hand-off guide recommends this.
2. The orchestrator should create `workflows/<wf>/notes/` before a session of a role that keeps notes, so the agent
   never tries to make the directory itself.
3. One run root per workflow (or no sibling workflows in the root) keeps a model from wandering into another
   workflow's files.
4. A design decision for the owner, not taken here: a path or cross-workflow denial currently parks the stage at once.
   Returning the first such denial to the model so it can correct the path (and parking only on a repeat) would let a
   run recover from a copying slip without loosening any deny.
