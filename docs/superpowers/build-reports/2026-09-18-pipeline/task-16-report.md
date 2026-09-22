# Task 16 — Live BYOK smoke test — DONE

Worktree: `.worktrees/task-16`, branch `wt/task-16`, based on `2033036`.

This report was written incrementally as work happened (session-kill safety per the coordinator).
The narrative sections below are the raw, chronological record of the two live attempts; the
"Status: DONE" section near the end is the summary — commits, verification commands, files changed,
self-review.

## Environment verified (before touching anything)

- Model file exists: `C:\Users\<user>\models\Ternary-Bonsai-2-27B\Ternary-Bonsai-2-27B-PQ2_0.gguf`
  (7,206,168,928 bytes).
- `llama-server.exe` exists: `C:\Users\<user>\tools\llama-prism\llama-server.exe`.
- Port 8080: free (`netstat -ano | grep :8080` → no match) before starting anything.
- Node: `fnm exec --using=22 node.exe -v` → `v22.23.2`. `npm.cmd -v` → `10.9.8`.
- Copilot CLI found via fnm's node-versions install: `C:\Users\<user>\AppData\Roaming\fnm\node-versions\v22.23.2\installation\copilot.cmd`,
  `copilot --version` → `GitHub Copilot CLI 1.0.86.` (SDK's pinned `copilotCliVersion` in
  `node_modules/@github/copilot-sdk/package.json` is `1.0.85`; SDK package `version` is `1.0.14`).
- `@github/copilot-sdk-win32-x64` (the platform runtime package) is present in `node_modules/@github/`.
- `gh` CLI: not on PATH (matches Task 15's `hasGh=no` finding).
- `~/.copilot` exists with only an `ide` subdirectory — no login attempted, none inspected beyond
  listing the directory name.
- pwsh 7: `C:\Users\<user>\AppData\Local\Microsoft\WindowsApps\pwsh.exe` → resolves to
  PowerShell 7.6.6.0.

## Deliverable 1: scripts/dev/serve_model.ps1 — written

Parameters and defaults exactly per the brief (`-Model`, `-Server`, `-Port 8080`, `-Context
32768`); validates both paths with `Test-Path -PathType Leaf` and exits 1 with a named-path error
if either is missing, before starting any process. Binds `--host 127.0.0.1` unconditionally (not
a parameter) — loopback only per the addendum's hard limit. Does not daemonize; the caller runs it
as a background process and stops it. Not yet tested at this point in the report — see below.

## llama-server started — PID recorded immediately (session-kill safety)

Started via `pwsh -File scripts/dev/serve_model.ps1` (all defaults) as a background process.
**`llama-server.exe` PID = 40420** (`tasklist /FI "IMAGENAME eq llama-server.exe"`), port 8080.
**If this session dies before it says "llama-server is stopped" below, kill PID 40420 / run
`taskkill /IM llama-server.exe /F` and confirm port 8080 is free.**

## Server health confirmed

`curl http://127.0.0.1:8080/health` → `{"status":"ok"}` on the first attempt (server had already
finished loading by the time of the check). From the server's own log (`llama-server.exe` stdout,
captured by the background task): model load took **11.598 seconds** wall time
(`0.00.073.248` load start → `0.11.598.006` "model loaded" / "listening on http://127.0.0.1:8080"),
`n_slots = 4`, `n_ctx_slot = 32768`. Server logged a NOTICE that CORS allows all origins and no API
key is set (expected/acceptable for a loopback-only dev server) and a NOTICE that the default port
will change to :9931 in a future release (informational, does not affect this run since the port is
pinned explicitly).

## Scratch root assembled

`C:\Users\<user>\AppData\Local\Temp\claude\C--Users-<user>-Desktop-Alteryx-to-Snowflake\2c0681b4-b739-4ab0-9ad6-5f1f179982b7\scratchpad\t16live\`
— copies of `scripts/`, `samples/`, `mappings/`, `catalog/`, `.github/`, `cookbook/` from the
worktree, plus a hand-written `orchestrator.config.json` whose `"python"` is the absolute venv path
`C:\Users\<user>\Desktop\Alteryx to Snowflake\.venv\Scripts\python.exe` (everything else copied
verbatim from the worktree's `orchestrator.config.json`, same `local` profile pointing at
`http://127.0.0.1:8080/v1`). Seeded with:
`python scripts/dev/build_samples.py seed --only wf_0001 --root <scratch>` → `wf_0001: seeded
(AMP)`, exit 0. `workflows/wf_0001/{manifest.json,source/,golden/}` now exist under the scratch
root only — confirmed the worktree's own `git status` stays clean throughout (no `workflows/` in
any git tree).

## Dry-run smoke test (no model call) — passed

```
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root <scratch> --only wf_0001 --runner copilot --profile local --no-interactive --dry-run
```
→ `orchestrate: root=<scratch> runner=copilot profile=local gh=no interactive=no workflows=1` /
`wf_0001: would run parse → intake → analyze → golden → translate → document → pr` / `wf_0001  not
started`, exit 0. This confirms `CopilotClient` constructs, `.start()` and `.stop()` succeed with no
CLI/login error even before any session is created — the runtime process itself launches fine on
this machine.

## Live run attempt — starting now

About to run (real model call, budget ~25 min of model time total per the addendum):
```
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root <scratch> --only wf_0001 --runner copilot --profile local --no-interactive --stop-after intake
```

## LIVE SESSION IS RUNNING — real audit.jsonl events observed (raw, as they arrived)

The CopilotRunner session for role `intake` did start against the local llama.cpp BYOK model — this
is real, not blocked on login. Tailing `workflows/wf_0001/audit.jsonl` live:

```
{"t":"2026-09-20T00:56:15.937Z","role":"intake","ev":"pre","tool":"view","args":"{\"path\":\"C:\\\\Users\\\\<user>\\\\Desktop\\\\Alteryx-to-Snowflake\\\\workflows\\\\wf_0001\\\\manifest.json\"}","decision":"deny"}
{"t":"2026-09-20T00:56:15.939Z","role":"intake","ev":"pre","tool":"view","args":"{\"path\":\"C:\\\\Users\\\\<user>\\\\Desktop\\\\Alteryx-to-Snowflake\\\\mappings\\\\global.yaml\"}","decision":"deny"}
{"t":"2026-09-20T00:56:23.890Z","role":"intake","ev":"pre","tool":"powershell","args":"{\"command\":\"Get-ChildItem . -Recurse -Depth 2 | Select-Object FullName, Length | Format-List; git rev-parse --show-toplevel\",\"description\":\"List scratchpad contents and git root\"}","decision":"deny"}
```

Real `toolName` values observed so far: **`view`**, **`powershell`**. Neither is in `READ_TOOLS` or
matched by `WRITE_TOOL`; `powershell` IS matched by `SHELL_TOOL` (`/^(bash|shell|powershell|pwsh|cmd)/i`).

Observations:
1. **`view`'s argument shape is `{"path": "<absolute path>"}`** — not in `READ_TOOLS` at all
   currently (`view` is actually already listed in `READ_TOOLS`! — wait: checking policy.ts,
   `"view"` IS in `READ_TOOLS`. Re-examine: the decision was still `deny`. This means the denial
   came from an EARLIER rule than the `READ_TOOLS` allow-list — rule 1b (path normalization) runs
   before the tool-name classification in `decide()`. The model's guessed absolute path
   `C:\Users\<user>\Desktop\Alteryx-to-Snowflake\...` (hyphenated, no spaces) does NOT match the
   real scratch root
   (`C:\Users\<user>\AppData\Local\Temp\claude\...\scratchpad\t16live\`), so
   `normalizeToolPath` denies it as `path outside the repository` before `view`'s allow-listed
   status is ever consulted. **This is the policy working correctly** — the model hallucinated a
   plausible-looking absolute path (based on the real project's name pattern, hyphenated instead of
   the real path's spaces) instead of using a relative path or the real working directory it was
   given, and the fail-closed path rule caught it.
2. **`powershell` is a real tool name** distinct from `shell`/`bash`/`cmd` that the provisional
   `SHELL_TOOL` regex already happens to match (`^(bash|shell|powershell|pwsh|cmd)/i`) — good, no
   policy gap here, but confirms the regex guess was right for this CLI build.
3. **Real shell-tool argument shape: `{"command": "...", "description": "..."}`** — an extra
   `description` key alongside `command` that `policy.ts`'s `firstString(args, /^(command|cmd|script|input)$/i)`
   correctly ignores (it only reads the `command` key). No policy change needed for this shape.
4. The command `Get-ChildItem . -Recurse -Depth 2 | Select-Object FullName, Length | Format-List;
   git rev-parse --show-toplevel` was denied for `shell metacharacter in command` (`;` and `|`
   present) — correct behavior per ruling C; the model tried to pipe/chain commands, which is
   exactly what the metacharacter rule exists to stop.

More raw events, in order:

```
{"t":"2026-09-20T00:56:26.449Z","role":"intake","ev":"pre","tool":"powershell","args":"{\"command\":\"Get-ChildItem -Path . -Recurse -Depth 2 | Format-List FullName,Length\",\"description\":\"List scratchpad directory tree\"}","decision":"deny"}
{"t":"2026-09-20T00:56:29.282Z","role":"intake","ev":"pre","tool":"powershell","args":"{\"command\":\"Get-Location\",\"description\":\"Show current directory\"}","decision":"deny"}
{"t":"2026-09-20T00:56:37.900Z","role":"intake","ev":"pre","tool":"view","args":"{\"path\":\"C:\\\\Users\\\\<user>\\\\AppData\\\\Local\\\\Temp\\\\claude\\\\C--Users-<user>-Desktop-Alteryx-to-Snowflake\\\\2c0681b4-b739-4ab0-9ad6-5f1f179982b7\\\\scratchpad\\\\t16live\"}","decision":"deny"}
{"t":"2026-09-20T00:56:42.647Z","role":"intake","ev":"pre","tool":"powershell","args":"{\"command\":\"Get-ChildItem -Name .\",\"description\":\"List current directory entries\"}","decision":"deny"}
{"t":"2026-09-20T00:56:49.824Z","role":"intake","ev":"pre","tool":"powershell","args":"{\"command\":\"git rev-parse --show-toplevel\",\"description\":\"Show git repository root\"}","decision":"deny"}
{"t":"2026-09-20T00:56:52.121Z","role":"intake","ev":"pre","tool":"powershell","args":"{\"command\":\"git status --short --no-pager\",\"description\":\"Show git repo status\"}","decision":"deny"}
{"t":"2026-09-20T00:56:54.047Z","role":"intake","ev":"pre","tool":"powershell","args":"{\"command\":\"git status\",\"description\":\"Show git repo status\"}","decision":"allow"}
{"t":"2026-09-20T00:56:55.080Z","role":"intake","ev":"post","tool":"powershell","bytes":4509,"result":"success"}
```

Analysis:
- **The model is spending its whole turn trying to orient itself** (where am I?), not reading the
  task. This is the dominant behavior pattern so far: `view` on a hallucinated path, `view` on the
  real root directory itself (denied — see below), `Get-Location`, `Get-ChildItem -Name .`,
  `git rev-parse --show-toplevel`, several git status variants — six denials before one command
  (`git status` with no flags) finally succeeded.
- **`view` on the root directory itself is denied — a real (non-security) limitation, not a hole.**
  `normalizeToolPath` requires `normalizedPath.startsWith(root + "/")`; when the candidate equals
  `root` exactly (no trailing segment), that `startsWith` check fails and it is denied as "path
  outside the repository", even though the root IS the repository. Recorded as a known limitation,
  **not fixed** — the addendum says never to loosen a deny to help the model, and this has no
  security implication either way (denying is safe; nothing here suggests the check should widen).
- **`Get-Location` is refused as an unrecognized shell invocation** (not in `READ_ONLY_SHELL`, not
  a role script, not pytest) — correct per the fail-closed shape rule; recorded, not changed.
- **`git rev-parse --show-toplevel` is refused** — `GIT_SUBCOMMANDS` is exactly `status`/`diff`/`log`
  by design; this is the policy working as specified, not a gap.
- **`git status --short --no-pager` is refused for one flag: `--no-pager`** is not on
  `git status`'s flag allow-list (`-s --short --porcelain -b --branch`). `--short` alone would have
  been fine. This is a real, safe, commonly-used flag the model reached for and got denied — but per
  the addendum ("never loosening a deny rule to help the model... do not tune the policy to make the
  model pass") this is being **recorded, not added**. No security reason to add it (it cannot write
  anything), but the instruction is about not helping the model past friction it caused itself by
  not just using the plain form, which then worked one call later.
- **The bare `git status` succeeded** (`decision: allow`, then `post` with 4509 bytes, `result:
  "success"`) — the first evidence in this whole test that `onPreToolUse` allow, tool execution, and
  `onPostToolUse` all fire correctly end to end against a real session.

Still watching — appending as more events arrive.

## Model orientation phase — recovers on its own

After the six early denials above, the model settled into using relative paths and succeeded from
`Get-ChildItem -Name workflows` (00:57:29) onward: it listed `workflows/`, `workflows/wf_0001/`,
`workflows/wf_0001/parsed/`, `workflows/wf_0001/intake/`, `mappings/`, then read
`workflows/wf_0001/manifest.json` (486 bytes), `mappings/global.yaml` (18902 bytes),
`workflows/wf_0001/parsed/dag.json` (2544 bytes), `workflows/wf_0001/intake/touchpoints.json` (467
bytes) — exactly the inputs `intake.agent.md` and `stageIntake`'s task prompt named. It then listed
`scripts/`, `docs/` (does not exist in the scratch root — copy list per Task 15's recipe omits
`docs/`; the listing command still succeeds, just returns nothing/an error as ordinary output text,
not a tool failure), `cookbook/`, `workflows/wf_0001/golden/`, `workflows/wf_0001/source/`, and
`scripts/dev`, `scripts/parsers`, `scripts/lib`, `scripts/proposals` — a broad self-directed
exploration pass, not just the documented inputs. All of these were correctly `allow`ed, one-line
audit entries each. Every `view`/`powershell` call using a workflow-relative path from this point
succeeded.

## Real token-generation numbers, from llama-server's own log (not invented)

`llama-server`'s stdout (background task `blr8lrjp2`) `slot print_timing` lines, several inference
turns into the session:

```
6.28.536.494 I slot print_timing: id  3 | task 5715 | n_gen =    898, tg =  29.50 t/s, tg_3s =  26.13 t/s
6.34.603.216 I slot print_timing: id  3 | task 5715 | n_gen =   1070, tg =  29.31 t/s, tg_3s =  31.12 t/s
6.41.104.221 I slot print_timing: id  3 | task 5715 | prompt eval time =     785.02 ms /   488 tokens (1.61 ms/tok, 621.64 tok/s)
6.41.104.228 I slot print_timing: id  3 | task 5715 |        eval time =   42975.42 ms /  1264 tokens (34.03 ms/tok, 29.39 tok/s)
6.41.104.229 I slot print_timing: id  3 | task 5715 |       total time =   43760.43 ms /  1752 tokens
6.41.104.979 I slot      release: id  3 | task 5715 | stop processing: n_tokens = 28255, truncated = 0
```

**Sustained generation speed for this session: ~29-31 tokens/second**, prompt (re-)evaluation
~450-620 tokens/second (helped heavily by prompt-cache reuse — "graphs reused" counts in the
thousands). This is markedly slower than the "~73 tok/s" figure the task brief cites as previously
verified — that number presumably came from a short-prompt/empty-context benchmark; **once the
session's own context grows past ~25,000-28,000 tokens** (system prompt + tool schemas +
`.github/agents/*.agent.md` custom-agent prompt + growing tool-call history, all inside the
32768-token `-c` window this server was started with), sustained token generation on this GPU is
~29-31 t/s, not ~73. Each agent turn here took roughly 30-45 seconds of generation time alone (not
counting prompt reprocessing), and the workflow needed many turns just to orient itself before doing
task-relevant work.

## ATTEMPT 1 RESULT: the live session crashed on context-window exhaustion, misreported as "denied"

The orchestrate process exited (code 1) at `01:00:49Z`, ~4:55 after the first tool call. Full
console output:

```
wf_0001: intake denied — view: path outside the repository: C:\Users\<user>\Desktop\Alteryx-to-Snowflake\workflows\wf_0001\manifest.json; view: path outside the repository: C:\Users\<user>\Desktop\Alteryx-to-Snowflake\mappings\global.yaml; powershell: shell metacharacter in command: Get-ChildItem . -Recurse -Depth 2 | Select-Object FullName, Length | Format-List; powershell: shell metacharacter in command: Get-ChildItem -Path . -Recurse -Depth 2 | Format-List FullName,Length; powershell: intake may not run this command: Get-Location; view: path outside the repository: C:\Users\<user>\AppData\Local\Temp\claude\...\scratchpad\t16live; powershell: listing argument not allowed: .; powershell: git rev-parse is not a listing command: status, diff, log only; powershell: flag not allowed for git status: --no-pager; view: path outside the repository: C:\Users\<user>\.copilot\session-state\83254e16-9fe1-4038-928c-b1648ad1557d
wf_0001: intake → NEEDS_HUMAN (denied)
wf_0001  parse=PARSED intake=NEEDS_HUMAN
wf_0001: needs a human — {"intake":"denied"}
[exited with code 1]
```

**But the real cause was NOT a permission denial — it was the model's own context window filling
up**, found by cross-referencing `llama-server`'s own log (background task `blr8lrjp2`), which is
ground truth for what actually happened on the wire:

```
7.11.064.513 I slot launch_slot_: id  3 | task 7960 | processing task, is_child = 0
7.15.943.233 I slot print_timing: id  3 | task 7960 | prompt eval time = 2234.67 ms / 1270 tokens
7.15.944.077 I slot      release: id  3 | task 7960 | stop processing: n_tokens = 31061, truncated = 0
7.16.046.031 I slot get_availabl: id  3 | task -1 | selected slot by LCP similarity, f_sim_best = 0.875
7.16.046.725 I slot launch_slot_: id  3 | task 8051 | processing task, is_child = 0
7.16.046.818 E srv    send_error: task id = 8051, error: request (35518 tokens) exceeds the available context size (32768 tokens), try increasing it
7.16.046.823 I slot      release: id  3 | task 8051 | stop processing: n_tokens = 31061, truncated = 0
7.16.046.861 W srv          stop: cancel task, id_task = 8051
```

The session's own accumulated context (system prompt + custom-agent prompt + tool schemas + the
whole growing tool-call/result history) hit **35,518 requested tokens against the server's
32,768-token `-c` window** on the 22nd inference turn of a single agent session, and llama-server
hard-rejected the request. That surfaced inside the SDK as an `onErrorOccurred` hook call with
`errorContext: "model_call"` — but **`hooks.ts`'s `onErrorOccurred` logs `String(input.error ?? "")`,
and here `input.error` was some non-string/non-`Error` object, so the audit line recorded only
`"error":"[object Object]"`** — the SDK-side detail of *why* the model call failed was silently lost
by our own logging code, and I only recovered the real cause by reading llama-server's independent
log. **`orchestrator/runner.ts`'s `CopilotRunner.run` catch block checks `state.denied` before
checking the thrown error's timeout/rate-limit signature** (`orchestrator/runner.ts:300-305`) — so
because six *earlier, already-recovered-from* permission denials had set `state.denied = true`
minutes earlier in the same session, this later, unrelated context-overflow crash got classified as
`"denied"` instead of `"error"`, and `manifest.json.reasons.intake` now reads `"denied"` even though
no denial caused the failure. **This is a real diagnostic-accuracy bug, found from live evidence,**
but it lives in `runner.ts`/`hooks.ts`, not `policy.ts` — outside this task's declared code-change
scope (policy.ts + tests). Recorded here prominently per the addendum's instruction to report
critical findings; not fixed as part of this task. (Flagged separately — see the end of this report.)

Confirmed from disk: `workflows/wf_0001/intake/mappings.yaml` (64 bytes) and `open_questions.md`
(1052 bytes) are the **pre-existing skeleton files `intake_prompt.py --no-interactive` writes before
the agent ever runs** (real Python, not the model) — the model itself never got far enough to write
`plan.md`, `mappings.yaml`'s resolved content, or anything else. `manifest.json.metrics.intake =
{"lastMs": 293830, "toolCalls": 37}` — **293.8 seconds (4:54) of wall time and 37 tool calls for one
intake attempt that produced no usable output.**

Total real model-serving wall time for this attempt, from `llama-server`'s own log (first task
launch to the final rejected task): **2:22 → 7:16 = 4 min 54 s**, across 22 separate inference turns
(`task 0` through `task 7960`, then the rejected `task 8051`).

## ATTEMPT 2 launched (per addendum: "if intake has not produced plan.md after two attempts, stop")

Reset with `--from-stage intake` and re-ran the identical command (started ~01:03:27Z, ~9.5 minutes
of wall time / ~4:54 of model time used by attempt 1, well inside the ~25-minute model-time budget):
```
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root <scratch> --only wf_0001 --runner copilot --profile local --no-interactive --from-stage intake --stop-after intake
```

## CRITICAL FINDING (positive): sub-agent (`task`) tool calls DO pass through the same `onPreToolUse` hook

This directly answers one of the addendum's explicit "what to record" questions. Real evidence, raw:

```
{"t":"2026-09-20T01:04:08.243Z","role":"intake","ev":"pre","tool":"task","args":"{\"agent_type\":\"task\",\"description\":\"Inspect wf_0001 project structure\",\"name\":\"inspect-wf0001\",\"prompt\":\"You are helping an Alteryx→Snowflake intake agent. I need a quick structural overview. Do NOT edit anything.\\n\\nRun these commands and show outputs...\"}","decision":"allow"}
{"t":"2026-09-20T01:04:32.578Z","role":"intake","ev":"pre","tool":"powershell","args":"{\"command\":\"git --no-pager log --oneline -5; echo \\\"---STATUS---\\\"; git --no-pager status --short | Select-Object -First 50\",\"description\":\"Show git log and status\"}","decision":"deny"}
{"t":"2026-09-20T01:04:32.579Z","role":"intake","ev":"pre","tool":"powershell","args":"{\"command\":\"Get-ChildItem -Path . -Recurse -File | Select-Object -First 120 FullName\",\"description\":\"List files recursively\"}","decision":"deny"}
{"t":"2026-09-20T01:04:32.580Z","role":"intake","ev":"pre","tool":"powershell","args":"{\"command\":\"Get-ChildItem -Path . -Recurse -Directory | Select-Object FullName\",\"description\":\"List directories recursively\"}","decision":"deny"}
{"t":"2026-09-20T01:04:32.581Z","role":"intake","ev":"pre","tool":"powershell","args":"{\"command\":\"Get-ChildItem -Path . -Name | Sort-Object\",\"description\":\"List top-level entries\"}","decision":"deny"}
```

The model called the `task` tool (`agent_type: "task"`) to spin up a sub-agent with its own prompt
asking it to run `git log`/`git status`/`Get-ChildItem` chains. `task` itself is in the provisional
`READ_TOOLS` allow-list, so the delegation call itself was allowed (correctly — spawning a sub-agent
is not itself a filesystem/shell action). **The sub-agent's own four PowerShell commands were then
each intercepted by `onPreToolUse` under the SAME `role: "intake"` audit context and correctly
denied** — every one used `;` or `|` to chain commands, which `SHELL_METACHARACTERS` catches
regardless of which agent (top-level or sub-agent) issued the call. This is exactly the coverage the
policy is supposed to have, and it held: **no gap, no bypass — a sub-agent cannot escape the
permission policy the top-level agent is bound by.** This answers the addendum's question
definitively: yes, `task` calls pass through the same hook, for both the delegation itself and
everything the sub-agent subsequently does in the same session.

Still watching to see whether the sub-agent recovers with corrected (unchained) commands, or whether
this attempt also runs into context exhaustion.

## ATTEMPT 2 RESULT: same failure mode, confirmed repeatable

The sub-agent adapted (`git --no-pager log` → `git log --oneline -5`, which then succeeded; the
piped `Get-ChildItem` calls stayed denied). The model then read real files successfully using its
correct absolute path (`workflows/wf_0001/manifest.json` 486 bytes, `mappings/global.yaml` 9665
bytes read via the exact real absolute scratch-root path this time — `glob`/`view` succeeded),
generated one very long response (2000+ tokens in a single turn, later turns 1000+ tokens each), and
then crashed identically:

```
{"t":"2026-09-20T01:08:09.095Z","role":"intake","ev":"error","error":"[object Object]","context":"model_call","recoverable":true,"class":"error"}
{"t":"2026-09-20T01:08:09.099Z","role":"intake","ev":"tool-fail","tool":"task","error":"400 request (34965 tokens) exceeds the available context size (32768 tokens), try increasing it"}
```

This time the `task` tool's own `onPostToolUseFailure` hook captured a clean, real string error
message (`"400 request (34965 tokens) exceeds..."`) — unlike `onErrorOccurred`'s `"[object
Object]"` for the identical underlying failure. This proves the real error text IS obtainable from
the SDK in *some* code paths; `onErrorOccurred`'s handling in `hooks.ts` is what loses it,
confirming Critical Finding 2 below is a logging bug, not an SDK limitation. `orchestrate.ts`'s
final console output for attempt 2:

```
wf_0001: intake denied — powershell: shell metacharacter in command: git --no-pager log --oneline -5; echo "---STATUS---"; git --no-pager status --sh; powershell: shell metacharacter in command: Get-ChildItem -Path . -Recurse -File | Select-Object -First 120 FullName; powershell: shell metacharacter in command: Get-ChildItem -Path . -Recurse -Directory | Select-Object FullName; powershell: shell metacharacter in command: Get-ChildItem -Path . -Name | Sort-Object; powershell: git --no-pager is not a listing command: status, diff, log only; powershell: git --no-pager is not a listing command: status, diff, log only; powershell: shell metacharacter in command: Get-ChildItem -Path . -Recurse -File | Select-Object -First 120 FullName; powershell: shell metacharacter in command: Get-ChildItem -Path . -Recurse -Directory | Select-Object FullName; powershell: interpreter flag -File is never allowed; powershell: listing argument not allowed: .; view: path outside the repository: <scratch root>; glob: path outside the repository: <scratch root>
wf_0001: intake → NEEDS_HUMAN (denied)
wf_0001  parse=PARSED intake=NEEDS_HUMAN
wf_0001: needs a human — {"intake":"denied"}
[exited with code 1]
```

Same misreported `"denied"` reason (Critical Finding 1, same mechanism). `manifest.json` after
attempt 2: `status.intake = "NEEDS_HUMAN"`, `reasons.intake = "denied"`, and — **`metrics.intake`
still reads `{"lastMs": 293830, "toolCalls": 37}`, byte-for-byte identical to attempt 1's numbers**,
even though attempt 2 ran independently for a different duration. This is not a coincidence: I
counted `"ev":"pre"` lines in `audit.jsonl` directly — 66 total in the file, 29 of them after
attempt 1's `session-end` line, meaning attempt 1 itself made exactly 37 pre-tool-use calls
(66 − 29 = 37, matching its own reported `toolCalls: 37`) and **attempt 2 made 29**, a different
number that never made it into the manifest. There is also **no second `"ev":"session-end"` line in
`audit.jsonl` at all** — only attempt 1's. **Confirmed: `onSessionEnd` never fired for attempt 2.**
Since `hooks.ts`'s `onSessionEnd` handler is the ONLY place `wf.metrics[role]` is written, attempt
2's real tool-call count and duration were silently never recorded — the field just kept showing
attempt 1's stale numbers. (`manifest.json.status`/`reasons` DID update correctly for attempt 2,
because those are written by `stages.ts`'s own `saveManifest` calls in `migrateWorkflow`'s loop and
`finally` block, a separate code path from the hooks.) This is a fourth diagnostic-accuracy finding,
in the same family as Critical Findings 1 and 2 below: **a session that ends via this kind of hard
model-call crash skips `onSessionEnd` entirely**, so any per-session bookkeeping that only happens
there (currently just `metrics`) goes stale and silently misattributes an old run's numbers to a
new one, with nothing in the manifest to indicate the numbers are wrong. `workflows/wf_0001/intake/`
still contains only the three pre-existing Python-written skeleton files — the model never wrote
`plan.md` in either attempt.

**Total real model-serving wall time, attempt 2** (`llama-server` log, task launch `10.03` → the
`task` tool's own failed sub-call `~14.13`, relative to server start): **~4 minutes 10 seconds**,
comparable to attempt 1's 4:54. Combined model time for both attempts: **~9 minutes**, well inside
the addendum's ~25-minute budget. Per the addendum ("if intake has not produced plan.md after two
attempts, stop and report"), **I stopped here** rather than trying a third time — the failure mode
was identical and deterministic in shape both times (context growth from a mix of necessary reads
and self-inflicted orientation overhead), so a third attempt would be very likely to reproduce it
again without adding new information. **Step 4 (`--stop-after translate`) was not attempted**, per
the addendum's own rule that it applies only if intake succeeded.

`grep -c unrecognized-tool workflows/wf_0001/audit.jsonl` → **0**, across both attempts combined.
Distinct real `toolName` values seen across both attempts: `view`, `glob`, `grep`, `powershell`,
`task` — all five already correctly classified by the existing policy before this test ever ran.

## Cleanup: llama-server stopped, port verified free

`taskkill /IM llama-server.exe /F` → `SUCCESS: the process "llama-server.exe" with PID 40420 has
been terminated.` `tasklist /FI "IMAGENAME eq llama-server.exe"` → `INFO: No tasks are running
which match the specified criteria.` `netstat -ano | grep :8080` → one harmless `TIME_WAIT` leftover
socket entry only (`127.0.0.1:52023 → 127.0.0.1:8080 TIME_WAIT`), **no `LISTENING` entry** — nothing
is bound to port 8080 anymore; the `TIME_WAIT` entry is normal OS-level TCP teardown residue from
the closed connection and clears on its own. **llama-server is stopped and port 8080 is free.**

## Policy changes: NONE to `orchestrator/policy.ts` itself — evidence did not call for any

Every real `toolName` observed (`view`, `glob`, `grep`, `powershell`, `task`) was already correctly
classified by the existing provisional `READ_TOOLS`/`SHELL_TOOL` regexes before this test — zero
`unrecognized-tool` audit events in either attempt. No `SQL_TOOL`- or `WRITE_TOOL`-classified call
was ever attempted (the run never left intake's read-only orientation phase), so those two regexes
remain **unverified by live evidence** — this is stated plainly rather than glossed over.

**No security hole was found.** Every denial observed was correct: a hallucinated absolute path, a
piped/chained shell command, a disallowed git subcommand/flag, a disallowed listing argument, and a
literal repository-root reference were all denied exactly as the policy specifies. The sub-agent
(`task`) pass-through, specifically named as a risk in the addendum, was verified to have **no
bypass**.

Added **7 regression tests** to `orchestrator/test/policy.test.ts` (43 policy tests total, up from
36; 92 node tests total, up from 85) that pin the exact real command/argument shapes observed live
as byte-for-byte assertions — see the commit `b39a779` message and the tests themselves for the
full list. One of those tests documents a genuine internal inconsistency found live (`get-childitem`
declares `-file` as one of its own allowed flags, but `INTERPRETER_FLAGS`'s blanket `-file` check
runs first and unconditionally denies it for every role, making the declared allowance unreachable);
this is not a security issue (denying is still safe) and was recorded, not fixed, per the addendum's
rule against loosening any deny.

`fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts` →
**92/92 passing**. `fnm exec --using=22 node.exe ".../node_modules/typescript/bin/tsc" --noEmit -p
.` → clean.

## Critical findings — blocking anything, and what the user must do

**Nothing blocked on login.** The live CopilotRunner path is NOT blocked on GitHub authentication
for the local BYOK provider — `CopilotClient.start()`/`createSession()` worked with no login prompt
in the dry run and both live attempts. This is the opposite of the "blocked on login" scenario the
brief and addendum anticipated as a possible outcome; it did not happen.

**What DID block progress: the model's own context window.** Both live attempts crashed identically
— llama-server rejected a request once the session's own accumulated context (system prompt + tool
schemas + `.github/agents/intake.agent.md` + the growing tool-call/result history) exceeded the
32,768-token `-c` window (35,518 tokens requested in attempt 1; 34,965 in attempt 2). Neither attempt
produced `plan.md` or any other model-authored file. See `docs/live-smoke-test.md` for the full
verdict, tokens/sec figures, and the honest per-role assessment (only `intake` has live evidence;
every other role is untested).

**Three diagnostic-accuracy bugs found live, in `runner.ts`/`hooks.ts` (NOT `policy.ts` — out of
this task's declared code-change scope, reported not fixed):**
1. `orchestrator/runner.ts:300-305` (`CopilotRunner.run`'s catch block) checks `state.denied` before
   checking the thrown error's timeout/rate-limit signature, so a context-window crash that happens
   minutes after earlier, already-recovered-from permission denials gets permanently misreported as
   `"denied"` in `manifest.json.reasons`. Confirmed in both attempts.
2. `orchestrator/hooks.ts`'s `onErrorOccurred` handler does `String(input.error ?? "")`; here
   `input.error` was a non-string object at runtime (despite the SDK's own `ErrorOccurredHookInput.error:
   string` type declaration), stringifying to `"[object Object]"` and losing the real diagnostic
   text. Confirmed reproducible in both attempts; the real text was only recovered by
   cross-referencing `llama-server`'s independent log. Notably, the SAME underlying failure's error
   text WAS captured cleanly by `onPostToolUseFailure` in attempt 2 (`"400 request (34965 tokens)
   exceeds..."`), proving the text is obtainable and this is a `hooks.ts` logging bug, not an SDK
   limitation.
3. **`onSessionEnd` never fired for attempt 2** (no second `"ev":"session-end"` line in
   `audit.jsonl` at all, despite the session clearly having terminated) — proven by tool-call
   counting, not assumption: 66 total `"ev":"pre"` lines in the file, 29 of them after attempt 1's
   `session-end` line (so attempt 1 made 37, matching its own reported count exactly; attempt 2 made
   29). Since `wf.metrics[role]` is written ONLY inside `onSessionEnd`, attempt 2's real duration and
   tool-call count were never recorded — `manifest.json.metrics.intake` still shows attempt 1's
   stale `{"lastMs": 293830, "toolCalls": 37}` after attempt 2 finished, with nothing in the file to
   flag that these numbers belong to a different run. `status`/`reasons` still updated correctly for
   attempt 2, because those are written by `stages.ts`'s own `saveManifest` calls, a separate code
   path from the hooks — only the hook-only bookkeeping (`metrics`) goes silently stale.

All three are flagged as follow-up work (see `spawn_task` note in the final message to the
coordinator); none were fixed here, since this task's code-change scope is `policy.ts` + tests only,
and fixing them is not "tightening a policy from evidence."

## What the user must do themselves to go further

See `docs/live-smoke-test.md`'s closing section. In short: nothing required login here and none was
attempted; raising `-Context` well beyond 32768 (GPU memory permitting) and re-running the same
two-command sequence is the most direct next experiment; the three `runner.ts`/`hooks.ts` diagnostic
bugs are real and reproducible but out of this task's scope to fix (flagged as a follow-up task,
`task_3875a16d`).

---

# Status: DONE

## Commits

- `22a81f6` wip: serve_model.ps1 for the local BYOK llama-server
- `b39a779` wip: regression tests locking in live-verified policy behavior
- `5d6417e` docs: live BYOK smoke test results (intake, wf_0001, two attempts)

**Brief correction:** the brief's suggested final commit message is "docs: live BYOK smoke test
results and tightened tool policy". The evidence from this live run did not call for any tightening
of `orchestrator/policy.ts` itself — every real tool name and argument shape observed was already
correctly classified, and no hole was found (see "Policy changes" above). Per the addendum's own
explicit allowance ("or NO policy change if the evidence does not call for one (say so)") and the
project's honesty rule (never imply something happened that didn't), the actual final commit message
(`5d6417e`) describes what really happened instead: docs from the live run, with the regression
tests that pin down verified-correct behavior already committed separately in `b39a779`.

## Verification commands run (final)

```
fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts
# → # tests 92 / # pass 92 / # fail 0

fnm exec --using=22 node.exe "C:\Users\<user>\Desktop\Alteryx to Snowflake\node_modules\typescript\bin\tsc" --noEmit -p .
# → (clean, no output)

git status --short   # (in .worktrees/task-16)
# → only intended files touched; no workflows/ ever appeared in this worktree's git tree
```

## Files changed

- `scripts/dev/serve_model.ps1` (new) — loopback-only llama-server launcher, brief's parameters and
  defaults, fails clearly if either path is missing.
- `orchestrator/test/policy.test.ts` — 7 new regression tests locking in live-verified behavior
  (no changes to `orchestrator/policy.ts` itself — evidence did not call for any).
- `docs/live-smoke-test.md` (new) — full write-up: versions, exact commands, what worked/did not,
  tokens/sec and wall time (all sourced from logs), per-role verdict, confirmation nothing touched
  Snowflake or Alteryx.
- This report.

## Self-review

- Every figure in `docs/live-smoke-test.md` and this report is copied from `audit.jsonl`,
  `orchestrate.ts`'s console output, `llama-server`'s own log, or a command I ran and can point to —
  none invented. Where I could not confirm something from evidence (`onPermissionRequest`,
  `ask_user`), I said so plainly instead of guessing.
- I did not tune the policy, the agent prompts, or anything else to make the model "pass" — both
  attempts were run with the identical, unmodified configuration, and the second attempt existed
  only because the addendum explicitly calls for two attempts before giving up.
- I did not fix the three `runner.ts`/`hooks.ts` bugs I found, even though I could see exactly what
  line to change in each case — they are outside this task's declared policy.ts-only code-change
  scope, and the task brief was explicit that a failing live path is a reportable result, not a
  standing invitation to patch whatever code stands between the model and success. Flagged instead
  as a follow-up task (`task_3875a16d`).
- The one internal inconsistency I found in `policy.ts` itself (`get-childitem`'s unreachable
  `-file` allowance) was recorded with a regression test that pins the CURRENT (denying) behavior,
  not changed — consistent with "never loosen a deny to help the model."
- llama-server's PID was recorded in this report within the same turn it was started, per the
  coordinator's session-kill-safety instruction, before any live model call was made.
