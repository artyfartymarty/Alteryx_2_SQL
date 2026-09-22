# Task 16 diagnostics (d1–d3) — DONE

Worktree: `.worktrees/task-16-diag`, branch `wt/task-16-diag`, based on `b57c585`.

Scope: unit-tested code only, in this worktree. No model server started, no live Copilot path
run. Fixes `orchestrator/runner.ts`, `orchestrator/hooks.ts`, `orchestrator/types.ts` for the
three diagnostic-accuracy bugs found live during Task 16's BYOK smoke test
(`task-16-report.md`, ATTEMPT 1/2), with TDD (RED against the pre-fix code, then GREEN).

## Baseline

`fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts`
before any change: **100/100 passing** (93 pre-Task-16 + 7 policy regression tests from Task 16,
matching the brief's stated baseline). `tsc --noEmit` clean. Worktree clean at the start.

## After

**115/115 passing** (100 baseline + 15 new: 10 in the new `orchestrator/test/runner.test.ts`, 5
added to `orchestrator/test/hooks.test.ts`). `tsc --noEmit` clean. Worktree clean after the final
commit (verified below).

## d1 — classification looks at the thrown error first, not `state.denied`

**File:line:** `orchestrator/runner.ts`, `CopilotRunner.run`'s catch block, originally lines
300–305 (now ~300–312 with the added `context-overflow` branch and comment).

**Before:**
```ts
const text = error instanceof Error ? error.message : String(error);
if (state.denied) return done("denied", state.denials.join("; "));
if (/timed?\s?out|timeout/i.test(text)) return done("timeout", text);
if (state.rateLimited || RATE_LIMIT.test(text)) return done("rate-limit", text);
return done("error", text);
```

**After:**
```ts
const text = errorText(error);
if (/timed?\s?out|timeout/i.test(text)) return done("timeout", text);
if (state.rateLimited || RATE_LIMIT.test(text)) return done("rate-limit", text);
if (CONTEXT_OVERFLOW.test(text)) return done("context-overflow", text);
if (state.denied) return done("denied", state.denials.join("; "));
return done("error", text);
```

`state.denied` is now the fallback, checked only after every more-specific signature has been
ruled out — exactly the binding ruling. Added `AgentError = "... | "context-overflow"` in
`orchestrator/types.ts` (a reason, not a new status: an escalated workflow still lands on
`NEEDS_HUMAN`, same as any other `"error"`). It is not added to `orchestrator/stages.ts`'s
`RETRY_ONCE` (`["missing-output", "timeout"]`), so it falls through `runAgent`'s loop exactly like
today's plain `"error"` — a single attempt, then straight to `escalate()`, which already uses
`result.error` as the reason (`m.reasons[stage] = "context-overflow"`) with no change needed in
`stages.ts` beyond exporting `toolCallsUsed` for tests (see d3).

`CONTEXT_OVERFLOW` regex (`orchestrator/hooks.ts:25-26`, exported so `runner.ts` can use the same
pattern hooks.ts uses for its own audit `class` field):
```ts
export const CONTEXT_OVERFLOW =
  /exceeds?\s+the\s+available\s+context\s+(size|length|window)|context\s+(length|window)\s+exceeded|maximum\s+context\s+length/i;
```
The first alternative matches the two REAL quotes in task-16-report.md verbatim: `"400 request
(34965 tokens) exceeds the available context size (32768 tokens), try increasing it"` (attempt
2's `onPostToolUseFailure`) and `"request (35518 tokens) exceeds the available context size
(32768 tokens), try increasing it"` (llama-server's own log, attempt 1). The other two
alternatives are defensive coverage for other providers' phrasing (OpenAI-style "maximum context
length" / "context length exceeded") — **not verified live**, called out honestly. The existing
timeout regex (`/timed?\s?out|timeout/i`) and `RATE_LIMIT` (`/429|rate.?limit|quota/i`) were left
unchanged — no real timeout text exists in the live evidence to check them against (both live
attempts hit context-overflow, not a timeout), and they already matched the rate-limit-shaped
test in `hooks.test.ts`.

**Hardening added beyond the minimum:** mirroring the existing `state.rateLimited` pattern
(`RATE_LIMIT` is checked both against the thrown error's text AND a state flag set earlier from
`onErrorOccurred`), I added a matching `state.contextOverflow` flag, set in `onErrorOccurred` when
`CONTEXT_OVERFLOW` matches, and OR'd into the runner's check
(`state.contextOverflow || CONTEXT_OVERFLOW.test(text)`). The live report never showed what
`sendAndWait` itself throws in this failure mode (the pre-fix `state.denied` check masked it every
time), so it is not certain the thrown error's own text carries the context-overflow signature —
`onErrorOccurred`'s separate `input.error` payload might be the only place it appears. This flag
closes that gap the same way it is already closed for rate-limit, at negligible cost. New test:
`d1: onErrorOccurred marking state.contextOverflow earlier still wins even if the final thrown
text does not itself match`.

### RED / GREEN

RED (against the pre-fix `runner.ts`/`hooks.ts`/`types.ts`, swapped in from `git show HEAD:...`,
new `orchestrator/test/runner.test.ts` run alone with only `stages.ts`'s `toolCallsUsed` given an
`export` keyword so the test module could load — no other behavior touched):
```
not ok 1 - d1: a crash after earlier, already-recovered-from denials is classified from the thrown error, not state.denied
  expected: 'context-overflow'
  actual: 'denied'
not ok 4 - d1: a real timeout signature still classifies as timeout despite an earlier denial
  expected: 'timeout'
  actual: 'denied'
not ok 5 - d1: a real rate-limit signature still classifies as rate-limit despite an earlier denial
  expected: 'rate-limit'
  actual: 'denied'
# tests 9, pass 4, fail 5
```
(Tests 2, 3, 6 passed even on the old code — they exercise scenarios that never depended on the
ordering bug, e.g. "denied with no other signature" or "no denial at all"; kept as regression
locks for behavior that must keep holding after the fix, and confirmed still passing below. This
RED run was captured with 9 tests, before the `state.contextOverflow`-flag hardening test below
was added; that 10th test only exists against the fixed code, since `HookState.contextOverflow`
did not exist pre-fix.)

GREEN (same file, against the fixed code): all 10 `d1`/`d3` tests in `runner.test.ts` pass (see
full suite run below).

## d2 — readable, bounded error text from any shape, in one shared helper

**File:line:** `orchestrator/hooks.ts:52-72` (new `errorText` function), used at
`hooks.ts:169` (`onPostToolUseFailure`), `hooks.ts:174` (`onErrorOccurred`), and
`orchestrator/runner.ts:307` (the catch block, replacing `error instanceof Error ? error.message
: String(error)`).

```ts
export function errorText(value: unknown): string {
  if (value === undefined || value === null) return "";
  let text: string;
  if (typeof value === "string") {
    text = value;
  } else if (typeof value === "object") {
    const message = (value as { message?: unknown }).message;
    if (typeof message === "string" && message.length > 0) {
      text = message;
    } else {
      try {
        text = JSON.stringify(value) ?? String(value);
      } catch {
        text = inspect(value, { depth: 3, breakLength: Infinity });
      }
    }
  } else {
    text = String(value);
  }
  return text.replace(/\s+/g, " ").trim();
}
```

Precedence: an object's own `.message` first (covers both a real `Error` instance and a plain
`{message, ...}` shape that crossed an RPC boundary and lost its prototype — exactly the live
shape: `ErrorOccurredHookInput.error` is typed `string` in the SDK's `.d.ts` but was a plain
object at runtime per task-16-report.md ATTEMPT 1), then a bounded `JSON.stringify` for any other
object, then `util.inspect` as a safe fallback for a value `JSON.stringify` cannot handle (a
cyclic object throws `TypeError: Converting circular structure to JSON`; `util.inspect` handles
cycles natively). **Bound chosen: the existing `AUDIT_ARG_LIMIT` (500 chars)** — callers still
apply `redact(...).slice(0, AUDIT_ARG_LIMIT)` on the result, same as every other audited field, so
`errorText` itself does not duplicate that bound; it only guarantees the text is a string at all
and collapses embedded whitespace/newlines (`.replace(/\s+/g, " ")`) so the final audit line stays
exactly one JSON value per line regardless of what the SDK handed back.

Also updated `onErrorOccurred`'s existing `class` audit field to say `"context-overflow"` (not
just `"rate-limit"` / `"error"`) when `CONTEXT_OVERFLOW` matches, using the same shared regex —
this means a live audit.jsonl would now show the true cause at the moment it happens, instead of
requiring the cross-referencing-with-llama-server's-log recovery the live report describes.

### RED / GREEN

RED, reproduced directly against the **unmodified pre-fix `hooks.ts`** (not a reimplementation):
```
$ node --experimental-strip-types -e '... hooksFor("intake", wf, env) ...
    onErrorOccurred({ error: { message: "400 request (34965 tokens) exceeds the available
    context size (32768 tokens), try increasing it" }, errorContext: "model_call", ... })'

RED evidence (old hooks.ts onErrorOccurred with object error):
{"t":"...","role":"intake","ev":"error","error":"[object Object]","context":"model_call","recoverable":true,"class":"error"}
```
This is the exact bug from task-16-report.md ATTEMPT 1, reproduced live from the actual pre-fix
code. (`errorText`/`recordMetrics` did not exist at all pre-fix, so the new `hooks.test.ts` tests
that reference them by name fail to even load against the old module — `SyntaxError: The
requested module '../hooks.ts' does not provide an export named 'errorText'` — which is itself
valid RED evidence that the capability did not exist before.)

GREEN: `hooks.test.ts`'s new tests (`errorText extracts readable text from every shape...`,
`errorText collapses embedded newlines...`, `onErrorOccurred with a non-Error object error
records readable text, not [object Object]...`, `onPostToolUseFailure with a non-string error
also extracts readable text...`) all pass against the fixed code (see full suite run below);
the `onErrorOccurred` test asserts on the exact real quote and confirms `class: "context-overflow"`.

## d3 — metrics recorded for every session, additive, never double-counted

**File:line:** `orchestrator/hooks.ts:104-110` (new `recordMetrics` function; the
`metricsRecorded` field added to `HookState`, `hooks.ts:43`), called from `hooks.ts:196`
(`onSessionEnd`, replacing its inline `wf.metrics[role] = {...}` write) and from
`orchestrator/runner.ts`'s `CopilotRunner.run` `finally` block, after `session?.disconnect()`.

**The accounting I found:** `orchestrator/stages.ts`'s `runAgent` reads a **cumulative-across-roles**
total via `toolCallsUsed(m)` (sums `m.metrics[role].toolCalls` for every role in the manifest) and
compares it to `budgets.maxToolCallsPerWorkflow` before starting each new agent call. But
*within* a single role, the pre-fix `onSessionEnd` **overwrote** `wf.metrics[role].toolCalls`
with only the just-finished session's own count (`toolCalls: state.toolCalls`, no accumulation)
— so a role's metric only ever reflected its *most recent* session, not its total spend across
every attempt (retries within one `runAgent` loop, or a fresh `--from-stage` invocation reloading
a stale on-disk manifest). Combined with `onSessionEnd` not firing at all on the live crash path,
this meant a crashed session's spend was not just under-counted, it was **completely invisible**
to the budget check — exactly what task-16-report.md's byte-count reconciliation proved (66 total
`"ev":"pre"` lines across both attempts; `manifest.json.metrics.intake` never moved past
attempt 1's stale `{"lastMs": 293830, "toolCalls": 37}`).

**Fix:** `recordMetrics(wf, role, state, ms)` is now the single place that writes
`wf.metrics[role]`, called from two places (`onSessionEnd` and `CopilotRunner.run`'s `finally`
block), guarded by a new `state.metricsRecorded` flag so whichever runs first "wins" and the
other is a no-op — a session is never double-counted even if both paths somehow ran.
`toolCalls` is now **additive** on whatever was already recorded for that role
(`priorCalls + state.toolCalls`); `lastMs` stays non-additive (it names only the most recent
session's own duration, by design — the field name says "last", not "total"). Because
`CopilotRunner.run`'s `finally` block always runs (crash or not), a session that crashes with no
`onSessionEnd` at all — the exact live-observed shape — is now still counted, and the finally
block's `recordMetrics` call is what makes that possible.

I did not add a `saveManifest` call to `CopilotRunner.run`'s `finally` block: `wf` is a shared,
mutable object, and `orchestrator/stages.ts`'s `migrateWorkflow` already calls `saveManifest`
unconditionally in its own loop/finally (`stages.ts:377,513,525,531`) after every `runAgent` call
returns — exactly the mechanism task-16-report.md itself credits for why `manifest.status`/
`reasons` updated correctly for attempt 2 even though `metrics` did not. Mutating `wf.metrics`
in-memory inside `CopilotRunner.run` is therefore sufficient; `stages.ts` is the only real caller
of `CopilotRunner.run` and always persists afterward.

### RED / GREEN

RED (pre-fix code, `runner.test.ts`):
```
not ok 7 - d3: a session that crashes without onSessionEnd firing still records this session's duration and tool calls
  the crashed session's own 3 tool calls are not lost
  undefined !== 3
not ok 9 - d3: a crashed first attempt's tool calls add to a second attempt's, and the budget check (toolCallsUsed) sees the total
  expected: 'context-overflow'
  actual: 'denied'   (fails at the first assertion, chained from the d1 bug)
```
Also a direct unit-level RED lock added to `hooks.test.ts` for the exact live numbers
(`recordMetrics adds to (not replaces) a role's prior toolCalls...` — `37 + 29 = 66`, the real
combined spend from both live attempts) — this test targets the new `recordMetrics` function
directly and could not run at all pre-fix (function did not exist).

GREEN: all `d3` tests pass against the fixed code, including
`toolCallsUsed(wf) === 9` after a simulated crashed-then-succeeded pair of attempts (5 + 4),
proving the budget check itself (not a reimplementation of it — the actual exported
`orchestrator/stages.ts` function is imported and called) sees the crashed session's spend.

## Full test run (final, fixed code)

```
fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts
# tests 115
# pass 115
# fail 0

fnm exec --using=22 node.exe ".../node_modules/typescript/bin/tsc" --noEmit -p .
# (clean, no output)
```

## Files changed

- `orchestrator/hooks.ts` — `CONTEXT_OVERFLOW` regex, `errorText`, `recordMetrics`,
  `HookState.metricsRecorded`; `onPostToolUseFailure`/`onErrorOccurred` use `errorText`;
  `onErrorOccurred`'s audit `class` field also reports `"context-overflow"`; `onSessionEnd`
  delegates its metrics write to `recordMetrics`.
- `orchestrator/runner.ts` — `CopilotRunner.run`'s catch block reorders classification (thrown
  error first, `state.denied` last), adds the `context-overflow` branch, uses `errorText`; the
  `finally` block calls `recordMetrics` for every session.
- `orchestrator/types.ts` — `AgentError` gains `"context-overflow"`.
- `orchestrator/stages.ts` — `toolCallsUsed` exported (visibility only, no behavior change) so
  tests can verify the budget check directly instead of reimplementing its sum.
- `orchestrator/test/runner.test.ts` (new) — 10 tests: d1 classification order (context-overflow,
  denied-with-no-other-signature, denied-without-throwing, timeout, rate-limit, rate-limit via
  `state.rateLimited`, context-overflow via `state.contextOverflow`), d3 metrics
  (crash-without-session-end, session-end-not-double-counted, crashed-then-succeeded cumulative
  total incl. `toolCallsUsed`). Drives a fake `CopilotClient`/session that calls the real
  `SessionHooks` `CopilotRunner` builds via `hooksFor`, so `policy.ts`'s real `decide()` and
  `hooks.ts`'s real audit/classification code run — only the SDK's session transport is faked.
- `orchestrator/test/hooks.test.ts` — 5 new tests: `errorText` shape coverage (string, `Error`,
  `{message}` object, `undefined`/`null`, cyclic object, embedded newlines), `onErrorOccurred`/
  `onPostToolUseFailure` with a non-string error, `recordMetrics` additive/idempotent behavior
  pinned to the live `37 + 29 = 66` numbers.
- `docs/live-smoke-test.md` — appended a dated "Follow-up, 2026-09-19" section stating d1–d3 are
  fixed in code and unit-tested, explicitly **not** re-verified live.
- `orchestrator/POLICY.md` — **not changed**: it documents `orchestrator/policy.ts`'s permission
  decisions (`decide()`), which this task did not touch at all. The session-outcome
  classification this task fixes (`AgentResult.error` / `manifest.reasons`) is a different layer
  (`runner.ts`/`hooks.ts`), out of POLICY.md's stated scope.
- Not changed: `docs/spec/01-copilot-setup.md`. Its hook table (line 519: `onSessionEnd | Write
  duration and tool-call count per role into manifest.metrics`) and skeleton pseudo-code (lines
  638/640) still describe the old, simpler shape and are now slightly stale (metrics are also
  recorded from `CopilotRunner.run`'s `finally` block). This is the overall program spec, outside
  the file set this task was scoped to touch (`orchestrator/POLICY.md`/comments,
  `docs/live-smoke-test.md`); flagging it here rather than editing it unilaterally.

## What I could not determine without a live run

- Whether a real Copilot SDK session's thrown error (from `session.sendAndWait()`) actually
  carries the context-overflow text as its `.message`, or whether the real thrown error is a more
  generic wrapper (e.g. "session ended unexpectedly") with the real detail only reachable via
  `onErrorOccurred`'s separate `input.error` payload. Both live attempts' console output never
  showed the raw thrown-error text (the pre-fix `state.denied` check masked it every time), so
  there is no live example of what `sendAndWait` itself throws in this exact failure mode — only
  what `onErrorOccurred` and `onPostToolUseFailure` received. **Mitigated, not eliminated:** I
  added `state.contextOverflow`, set from `onErrorOccurred`, OR'd into the runner's check
  (`state.contextOverflow || CONTEXT_OVERFLOW.test(text)`) — exactly parallel to how
  `state.rateLimited` already covers the same ambiguity for rate limits. This means the fix works
  whichever of the two shapes the real SDK turns out to use, but which shape it actually is (or
  whether `onErrorOccurred` reliably fires before the throw at all) is still unconfirmed without a
  live run.
- Whether `onSessionEnd`'s real SDK timing relative to `session.disconnect()` matches what these
  tests assume (finally calls `disconnect()` then `recordMetrics`, on the theory that a real
  `onSessionEnd` firing during teardown would run before `recordMetrics`'s guard check). Since
  `recordMetrics` is idempotent regardless of ordering, this only matters if the real SDK could
  invoke `onSessionEnd` *after* `disconnect()` resolves (asynchronously, post-teardown) — not
  observed live either way.
- Whether `CONTEXT_OVERFLOW`'s two defensive (non-llama.cpp) alternatives match any other real
  provider's actual wording — untested by any live evidence, called out in the regex's own
  comment.

## Self-review

- Followed TDD: wrote `orchestrator/test/runner.test.ts` and the `hooks.test.ts` additions,
  confirmed RED against the real pre-fix code (via `git show HEAD:...` copies swapped
  temporarily into the worktree, then restored — no `git stash`/`checkout --`/`reset --hard`
  used anywhere), then implemented, then confirmed GREEN.
- Did not touch `orchestrator/policy.ts` at all — this task's scope is `runner.ts`/`hooks.ts`,
  not the permission policy.
- Kept `AgentError`'s status vocabulary otherwise unchanged, per the ruling: `context-overflow`
  is a reason, `manifest.status[stage]` still only ever becomes `NEEDS_HUMAN`.
- The `toolCallsUsed` export from `stages.ts` is a pure visibility change (added the `export`
  keyword to an existing function, zero logic change) — verified by diff.
- Honesty: `docs/live-smoke-test.md`'s follow-up note says plainly that nothing here was
  re-verified live.

## Status: DONE

### Commits
- (to be created next) `fix: live-run diagnostics — error classified from the thrown error, readable error text, metrics recorded for crashed sessions`

### Verification commands (final)
```
fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts
# tests 115 / pass 115 / fail 0

fnm exec --using=22 node.exe ".../node_modules/typescript/bin/tsc" --noEmit -p .
# (clean)

git status --short   # clean after the commit below
```
