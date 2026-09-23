# Session telemetry, spend budgets and the cost report — design

Sub-project 1 of 4 (telemetry → compaction and hand-off checkpoints → token cost estimation →
prompt assembly). Approved 2026-09-22. The orchestrator today records only tool-call counts per
role; this adds real token and billing figures from the Copilot SDK's event stream, two spend caps
with a kill switch, a cost report, and the context-window override a BYOK model needs.

Nothing here has run against a hosted Copilot model. Every SDK fact below was read from
`@github/copilot-sdk` 1.0.14's type definitions; §8 lists what only a live run can confirm.

## 1. Goals and non-goals

Goals
- Record, per workflow and role, what every agent session cost: model calls, input/output/cache
  tokens, Copilot AI units, duration, compactions and truncations — including sessions that crash.
- Park a workflow when its spend crosses a per-workflow cap in AI units or input tokens, the way
  the tool-call cap already does, and stop a runaway session mid-flight.
- Produce a cost report per workflow, role and model that finance can read.
- Let the runtime know a BYOK model's context window so its own compaction can fire.
- Leave the repo in a state a corporate team can take over: no machine paths, every field
  documented with its default, unit-tested against the fake SDK session, and an explicit list of
  what is unverified live.

Non-goals (later sub-projects)
- Tuning or steering compaction, hand-off checkpoints (sub-project 2).
- Estimating tokens before a run (sub-project 3). Assembling prompts (sub-project 4).
- Pricing: AI units are what Copilot bills; the report carries no currency.

## 2. SDK surface used (1.0.14)

| Need | SDK element | Notes |
|---|---|---|
| Event stream | `session.on(handler): () => void` | `SessionEvent` union; unsubscribe function returned |
| Per model call | `assistant.usage` — `model`, `inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`, `cost` (billing multiplier), `duration`, `apiCallId`, `copilotUsage.totalNanoAiu` | every field optional except `model` |
| Compaction | `session.compaction_start` (`currentTokens`, `conversationTokens`), `session.compaction_complete` (`trigger`, `messagesRemoved`, `checkpointPath`, `checkpointNumber`, `compactionTokensUsed`, `error`) | `CompactionTrigger`: threshold, context_limit_retry, manual, memory_pressure, model_switch |
| Truncation | `session.truncation` — messages/tokens before and after | |
| Context size per turn | `session.usage_info` — `currentTokens`, `messagesLength`, `systemTokens` | |
| Failures | `model.call_failure` | |
| Sub-agents | `subagent.started`, `subagent.completed`, `subagent.failed` — `agentId` | |
| Session totals | `session.shutdown` — `modelMetrics` per model (requests, usage, `totalNanoAiu`), `currentTokens`, `totalApiDurationMs` | may not arrive on a crash path |
| Kill switch | `session.abort(): Promise<void>` | |
| BYOK window | `createSession({ modelCapabilities: { limits: { max_context_window_tokens } } })` | `ModelCapabilitiesOverride = DeepPartial<ModelCapabilities>` |

Not used: the runtime's `preCompact` hook (not exposed in the SDK's typed `SessionHooks`), the
CLI's on-disk session event log (undocumented format).

## 3. Data model

### 3.1 `manifest.metrics[role]` — lifetime totals, never reset

```json
"metrics": {
  "intake": {
    "lastMs": 293830,            // existing: duration of the most recent session
    "toolCalls": 37,             // existing: cumulative tool calls across sessions
    "sessions": 2,               // sessions started for this role, crashed ones included
    "calls": 41,                 // model API calls seen (assistant.usage events)
    "inputTokens": 512340,
    "outputTokens": 8120,
    "cacheReadTokens": 402000,
    "cacheWriteTokens": 0,
    "aiu": 12.4,                 // Copilot AI units = sum(totalNanoAiu) / 1e9; null when never reported
    "compactions": 1,
    "truncations": 0,
    "models": {
      "<model id>": { "calls": 41, "inputTokens": 512340, "outputTokens": 8120, "aiu": 12.4 }
    },
    "reported": { "usage": true, "shutdown": false }
  }
}
```

Rules
- Every counter is cumulative across sessions of the role and is never reset; `lastMs` is the
  most recent session only (unchanged).
- `aiu` is `null` until the first event that carries `totalNanoAiu`; a BYOK provider reports
  none, and the report must show "not reported", never `0`.
- `reported.usage` is true once any `assistant.usage` event arrived in any session of the role;
  `reported.shutdown` once any `session.shutdown` did. They tell a reader whether zeros are real.
- `models` keys are the ids the runtime reports in `assistant.usage.model`, verbatim.
- Existing readers (`toolCallsUsed`, the cost of the F8 budget reset, `test_committed_workflows`)
  keep working: the two old fields keep their names and meaning.

### 3.2 `manifest.budget` — the window the caps are checked against

```json
"budget": { "since": "2026-09-22T15:04:05Z", "toolCalls": 37, "aiu": 12.4, "inputTokens": 512340 }
```

- Incremented by exactly the same amounts as the corresponding `metrics` counters, at the same
  moment (`recordMetrics`); `aiu` is `0` here when nothing was reported (the cap simply cannot
  trigger), because the window is arithmetic, not a report.
- `--from-stage` resets the window (`since` = now, counters 0) and logs one line
  `<id>: --from-stage <stage> reset the spend window (tool calls <n>, aiu <x>, input tokens <t>)`.
  It never touches `metrics`. **Behaviour change:** `metrics.toolCalls` is no longer reset by
  `--from-stage` (F8 fix wave); the reset now applies to `budget.toolCalls`. `toolCallsUsed`
  reads `budget.toolCalls`; if `budget` is absent (manifests written before this change) it falls
  back to the sum of `metrics.*.toolCalls`, as today, and creates the window on first save.

### 3.3 `audit.jsonl` events (all single-line, redacted, bounded by `AUDIT_ARG_LIMIT`)

| `ev` | fields | source |
|---|---|---|
| `usage` | `role, model, inputTokens, outputTokens, cacheReadTokens, cacheWriteTokens, cost, aiu, durationMs, apiCallId, agentId?` | `assistant.usage` |
| `compaction-start` | `role, trigger?, currentTokens, conversationTokens` | `session.compaction_start` |
| `compaction-complete` | `role, trigger, messagesRemoved, currentTokens, conversationTokens, checkpointPath, checkpointNumber, tokensUsed, error?` | `session.compaction_complete` |
| `truncation` | `role, performedBy, messagesBefore, messagesAfter, tokensBefore, tokensAfter` | `session.truncation` |
| `usage-info` | `role, currentTokens, messagesLength, systemTokens` | `session.usage_info` |
| `model-fail` | `role, model?, error` (redacted) | `model.call_failure` |
| `subagent-start` / `subagent-complete` / `subagent-fail` | `role, agentId, name?` | `subagent.*` |
| `shutdown` | `role, currentTokens, totalApiDurationMs, totalNanoAiu, models: {id: {requests, inputTokens, outputTokens, aiu}}` | `session.shutdown` |
| `budget-abort` | `role, cap: "inputTokens", used, limit` | the kill switch |

No prompt, message, summary or file content is ever written. `checkpointPath` is a path string
only. Field names that the runtime does not send are omitted from the line (not written as null),
so a reader can tell "absent" from "zero".

## 4. Budgets and the kill switch

### 4.1 Configuration (`orchestrator.config.json` → `budgets`)

| Field | Default | Meaning |
|---|---|---|
| `maxToolCallsPerWorkflow` | 400 (existing) | tool calls in the current window |
| `maxAiuPerWorkflow` | unlimited (absent) | Copilot AI units in the current window |
| `maxInputTokensPerWorkflow` | unlimited (absent) | input tokens in the current window; also arms the kill switch |

A missing or `null` field means unlimited and the README says so; `0` is an error at config load
(a cap of zero would park every workflow before its first call) — `UsageError`, exit 2.

### 4.2 Between calls (in `runAgent`, before every agent invocation)

The check that exists today for tool calls runs for all three counters of `manifest.budget`.
They are checked in the order tool calls, AI units, input tokens; the first one exceeded wins. The workflow parks exactly as it does now — `AgentResult`
`{ ok: false, error: "error", detail: "budget" }` so the existing `escalate` path and tests keep
working — and `reasons.<stage>` records the specific cap and numbers, e.g.
`budget: aiu 12.4 > 10` or `budget: inputTokens 1203400 > 1000000`. The log line names the same.

### 4.3 Mid-session (the kill switch)

- Armed only when `maxInputTokensPerWorkflow` is set. The telemetry handler keeps
  `budget.inputTokens` (at session start) + this session's `inputTokens` so far; on the
  `assistant.usage` event that crosses the cap it calls `session.abort()`, records
  `budget-abort`, and sets `state.budgetAbort`.
- The runner classifies that session as `{ ok: false, error: "error", detail: "budget" }`
  (checked before the timeout / overflow / rate-limit signatures, since the abort is ours), so
  `runAgent` does not retry it and the workflow parks with `reasons.<stage>` =
  `budget: inputTokens <used> > <limit> (session aborted)`.
- Only the token cap arms it: BYOK reports no AI units, and an AI-unit kill switch that silently
  never fires on one profile would be a false safety. The AI-unit cap stays a between-calls
  check; README and the hand-off doc say this.
- `abort()` is awaited with a 5-second timeout; if it rejects or times out, the session is still disconnected in
  `finally` and the classification stands.

## 5. Cost report — `scripts/cost_report.py`

```
.venv/Scripts/python.exe scripts/cost_report.py [--root .] [--out reports/cost] [--format csv|md|both]
```

- Reads every `workflows/*/manifest.json` under `--root`; a manifest without `metrics` yields a
  row with "no sessions"; a role whose `reported.usage` is false yields "not reported by runtime"
  in the coverage column and blanks (never zeros) in the token and AIU columns.
- Rows: `workflow, tier, terminal_status, role, model, sessions, calls, input_tokens,
  output_tokens, cache_read_tokens, aiu, tool_calls, last_ms, coverage`; then per-workflow totals
  and per-tier totals (sums over rows that reported).
- Writes `<out>.csv` and/or `<out>.md` (Markdown table); ordering is by workflow id, role, model,
  so two runs over the same manifests produce byte-identical files.
- Exit 0 on success, 2 on a usage error or an unreadable manifest (named). No numbers are computed
  from anything but the manifests; no prices.

## 6. BYOK context window

- `profiles.local.contextWindowTokens` (optional integer). When present, `CopilotRunner` passes
  `modelCapabilities: { limits: { max_context_window_tokens } }` on `createSession` so the
  runtime's default compaction thresholds (80 % background, 95 % blocking) refer to the real
  window of the local server. `serve_model.ps1` prints
  `set profiles.local.contextWindowTokens to <Context> in orchestrator.config.json` at start-up.
- Compaction thresholds themselves are not configured here (sub-project 2).

## 7. Components and mechanics

| File | Change |
|---|---|
| `orchestrator/types.ts` | `UsageTotals`, `RoleMetrics`, `BudgetWindow`, `Manifest.budget?`, `budgets.maxAiuPerWorkflow?`, `budgets.maxInputTokensPerWorkflow?`, `ProfileConfig.contextWindowTokens?` |
| `orchestrator/telemetry.ts` (new) | `attachTelemetry(session, state, audit, guard): () => void` — subscribes with `session.on`, one `case` per event type in §3.3 with optional-field access, unknown events ignored, returns the unsubscribe; `guard` = `{ limit?: number, alreadyUsed: number, abort(): Promise<void> }` for the kill switch |
| `orchestrator/hooks.ts` | `HookState` gains `usage: UsageTotals`, `budgetAbort: boolean`; `recordMetrics` folds `usage` into `metrics[role]` (lifetime) and `manifest.budget` (window); still idempotent, still called from `onSessionEnd` and the runner's `finally` |
| `orchestrator/runner.ts` | subscribe right after `createSession`, unsubscribe in `finally` before `disconnect`; `modelCapabilities` for the local profile; `budgetAbort` classification first |
| `orchestrator/stages.ts` | `toolCallsUsed` → `spendUsed(m): {toolCalls, aiu, inputTokens}` with the fallback of §3.2; the three-cap check; `clearFromStage` resets the window instead of `metrics.toolCalls` |
| `orchestrator/cli.ts` | config validation for the two new caps (`0` is a usage error) |
| `scripts/cost_report.py` (new), `tests/test_cost_report.py` (new) | §5 |
| `scripts/dev/serve_model.ps1` | the one printed line of §6 |
| `docs/reference/manifest-telemetry.md` (new) | the fields of §3 with meanings and defaults |
| `README.md`, `docs/handoff-copilot-models.md`, `docs/live-smoke-test.md` | §9 |

Data flow: `createSession` → `attachTelemetry` → events accumulate in `HookState.usage` and are
audited as they arrive → (kill switch may `abort()`) → `sendAndWait` resolves or throws →
`finally`: unsubscribe, `recordMetrics` (metrics + budget), `disconnect` → `AgentResult` → `runAgent`
routes → next call's cap check reads `manifest.budget`.

Error handling: a handler exception never propagates into the session (each `case` is wrapped;
a failure to write an audit line is logged once per session); `abort()` failures are logged and
do not change the classification; a `session.on` that throws at subscription time is logged as
`telemetry-unavailable` in the audit and the session runs without telemetry (`reported.usage`
stays false).

## 8. Testing

Node (`orchestrator/test/`), against the fake SDK session:
- the fake session gains `events: SessionEvent[]` emitted during `sendAndWait`, interleaved with
  its tool calls, and records `abort()` calls;
- per-model accumulation from several `assistant.usage` events, AIU conversion (nano → units),
  `reported.usage`;
- one test per audit event shape (fields present, absent fields omitted, redaction applied to
  `model-fail`);
- no events at all → `reported.usage=false`, `aiu=null`, token counters 0, report row says
  "not reported";
- crash path (session throws, no `session.shutdown`, no `onSessionEnd`) still records tokens seen
  so far;
- each cap parks with `detail: "budget"` and the right `reasons.<stage>` text; first exceeded wins;
- kill switch: aborts on the crossing event, not before; classification `budget`; no retry;
  not armed when the cap is absent;
- `--from-stage` resets `budget` and not `metrics`; a manifest without `budget` falls back to the
  metrics sum;
- `contextWindowTokens` reaches `createSession` for the local profile only;
- config validation: `0` caps are usage errors.

Python (`tests/test_cost_report.py`): synthetic manifests (reported / not reported / no sessions /
mixed models) → golden CSV and Markdown; byte-identical on a second run; exit 2 on a corrupt
manifest, named.

Existing tests that must keep passing unchanged: everything in `orchestrator/test/*.test.ts`
except the F8 tests that assert `metrics.toolCalls` is reset by `--from-stage` — those are
re-pointed at `budget.toolCalls` (listed in the implementation report).

## 9. Documentation and the live-verification checklist

- README §6/§8: the three budget fields with defaults, the cost report command, the
  `contextWindowTokens` field, and the sentence that AIU caps are checked between calls only.
- `docs/handoff-copilot-models.md` §6: replace "tool-call budget" wording with the three caps.
- `docs/live-smoke-test.md`, new list "Telemetry — to verify on a live run": whether
  `assistant.usage` arrives on the hosted profile and on BYOK; whether `copilotUsage.totalNanoAiu`
  is populated; whether `session.shutdown` arrives on a normal end and on a crash; whether
  `session.abort()` ends the session promptly and what `sendAndWait` throws then; whether the
  capabilities override makes the runtime compact a local model at the thresholds.
- `docs/superpowers/build-reports/.../ledger.md` records the implementation as before.

## 10. Rulings carried into this design

- Spend is never erased: `metrics` are lifetime, `budget` is the window; `--from-stage` resets
  only the window.
- Absent runtime data is reported as absent (`null`, blank, "not reported"), never as zero.
- The kill switch acts only on a number both profiles can report (input tokens).
- No dependency on undocumented CLI behaviour; anything the SDK's types do not promise is on the
  live checklist, not assumed.
- `docs/spec/**` stays verbatim; manifest additions are documented in `docs/reference/`.
