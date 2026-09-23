# Session Telemetry, Spend Budgets and Cost Report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record what every Copilot agent session really costs (tokens, AI units, compactions), park a workflow when a per-workflow spend cap is crossed — with a mid-session kill switch on input tokens — and produce a cost report per workflow, role and model.

**Architecture:** `CopilotRunner` subscribes to the SDK session's event stream through a new `orchestrator/telemetry.ts`; events accumulate in the per-session `HookState` and are written to `audit.jsonl`; the existing idempotent `recordMetrics` folds them into lifetime `manifest.metrics[role]` and a resettable `manifest.budget` window; `stages.ts` checks three caps against the window before every agent call; `scripts/cost_report.py` turns manifests into CSV/Markdown. Nothing depends on undocumented CLI behaviour; what only a live run can confirm goes on a checklist.

**Tech Stack:** TypeScript on Node 22 (`node --experimental-strip-types`, `node:test`, `tsc --noEmit`), `@github/copilot-sdk` 1.0.14 types, Python 3.14 (`pytest`) for the report.

**Spec:** [docs/superpowers/specs/2026-09-22-session-telemetry-design.md](../specs/2026-09-22-session-telemetry-design.md)

## Global Constraints

- Node commands: `"C:\Users\<you>\AppData\Local\Microsoft\WinGet\Links\fnm.exe" exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts` and `… node.exe node_modules/typescript/bin/tsc --noEmit -p .` from the repo root (the `.exe` suffix is required with `fnm exec`; plain `node` on the build PC is 20.18). In a git worktree there is no `node_modules`; Node resolves upward, and `tsc` is run from the main checkout's `node_modules/typescript/bin/tsc` with `-p .` in the worktree.
- Python: always `.venv/Scripts/python.exe` from the repo root; tests `.venv/Scripts/python.exe -m pytest <paths>` (`addopts=-q` is already set; do not add another `-q`).
- CLI exit codes: `0` success, `1` domain failure, `2` usage or unexpected error; `main(argv=None) -> int` mirrors `scripts/parse.py`.
- Absent runtime data is recorded as absent (`null`, omitted field, "not reported"), never as `0` (spec §3, §10).
- `metrics` are lifetime and never reset; `budget` is the window `--from-stage` resets (spec §3.2).
- No message, prompt, summary or file content in `audit.jsonl`; every line goes through `redact` and is bounded by `AUDIT_ARG_LIMIT` (500) (spec §3.3).
- `docs/spec/**` is never edited; new manifest fields are documented in `docs/reference/manifest-telemetry.md`.
- No machine paths or user names in committed files (a test already guards `workflows/**`; keep new docs to `<you>`-style placeholders).
- Commit after each task with a conventional message ending in `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (subagents use their own model-accurate trailer); `wip:` commits early; never `git stash`, `git checkout --` or `git reset --hard`.
- TDD: write the failing test, run it, implement, run it green, commit. Existing suites must stay green: node 150/150 and pytest 1005/0 skipped at the start of this plan.
- Nothing is pushed to any remote as part of this plan; local commits only.

## File structure

| File | Responsibility |
|---|---|
| `orchestrator/types.ts` (modify) | `UsageTotals`, `RoleMetrics`, `BudgetWindow`, `Spend`; `Manifest.budget?`; `AgentResult.reason?`; new `budgets` and `ProfileConfig.contextWindowTokens?` fields |
| `orchestrator/telemetry.ts` (new) | `emptyUsage()`, `attachTelemetry(...)`: SDK session events → `HookState.usage` + audit lines; the kill switch guard (Task 3) |
| `orchestrator/hooks.ts` (modify) | `HookState.usage`/`budgetAbort`; `hooksFor` also returns `audit`; `recordMetrics` folds usage into metrics + budget; `ensureBudget`, `resetBudget`, `spendUsed` |
| `orchestrator/runner.ts` (modify) | subscribe/detach telemetry around `sendAndWait`; `budgetAbort` classified first; `modelCapabilities` for the local profile |
| `orchestrator/stages.ts` (modify) | `exceededCap`; the three-cap check in `runAgent`; `escalate` uses `result.reason`; `clearFromStage` resets the window |
| `orchestrator/cli.ts` (modify) | `budgets` validation (`0` is a usage error) |
| `orchestrator/test/fake_session.ts` (new) | the fake `CopilotClient`/session shared by runner and telemetry tests, now emitting `events` and recording `abort()` |
| `orchestrator/test/telemetry.test.ts` (new), `runner.test.ts`, `stages.test.ts`, `cli.test.ts` (modify) | tests |
| `scripts/cost_report.py` (new), `tests/test_cost_report.py` (new) | the report |
| `scripts/dev/serve_model.ps1` (modify) | prints the `contextWindowTokens` hint |
| `docs/reference/manifest-telemetry.md` (new); `README.md`, `docs/handoff-copilot-models.md`, `docs/live-smoke-test.md` (modify) | documentation and the live checklist |

---

### Task 1: Telemetry module, event-emitting fake session, metrics folding

**Files:**
- Modify: `orchestrator/types.ts` (add types after `Manifest`; add `budget?` to `Manifest`)
- Create: `orchestrator/telemetry.ts`
- Modify: `orchestrator/hooks.ts` (`HookState`, `hooksFor` return value, `recordMetrics`, new `ensureBudget`)
- Modify: `orchestrator/runner.ts` (subscribe after `createSession`, detach in `finally`)
- Create: `orchestrator/test/fake_session.ts` (moved out of `runner.test.ts`, extended)
- Modify: `orchestrator/test/runner.test.ts` (import the shared fake)
- Create: `orchestrator/test/telemetry.test.ts`

**Interfaces:**
- Consumes: `HookState`, `hooksFor`, `recordMetrics` (hooks.ts); `auditArgs`, `redact`, `errorText`, `AUDIT_ARG_LIMIT` (hooks.ts); `SessionEvent` type from `@github/copilot-sdk`.
- Produces (used by Tasks 2–5):
  - `types.ts`: `UsageTotals`, `RoleMetrics`, `BudgetWindow`, `Manifest.budget?: BudgetWindow`
  - `telemetry.ts`: `emptyUsage(): UsageTotals`; `attachTelemetry(session: Subscribable, state: HookState, audit: AuditFn, log: (line: string) => void): () => Promise<void>` (returns `detach`: unsubscribe + drain pending audit writes)
  - `hooks.ts`: `HookState.usage: UsageTotals`, `HookState.budgetAbort: boolean`; `hooksFor(...) => { hooks, state, audit }`; `export type AuditFn`; `ensureBudget(wf: Manifest): BudgetWindow`; `recordMetrics` unchanged signature, new behaviour
  - `test/fake_session.ts`: `fakeClient(root, scenario, record?)`, `Scenario`, `ToolCall`, `FakeSessionRecord`, `invocation`

- [ ] **Step 1: Add the types**

In `orchestrator/types.ts`, directly after the `Manifest` interface, add:

```ts
/** Per-model slice of a session's usage; `nanoAiu` stays null until the runtime reports one. */
export interface ModelUsage {
  calls: number;
  inputTokens: number;
  outputTokens: number;
  nanoAiu: number | null;
}

/** What one session's event stream added up to (accumulated in HookState, folded by recordMetrics). */
export interface UsageTotals {
  calls: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  /** Sum of copilotUsage.totalNanoAiu; null when no event ever carried one (a BYOK provider). */
  nanoAiu: number | null;
  compactions: number;
  truncations: number;
  models: Record<string, ModelUsage>;
  reported: { usage: boolean; shutdown: boolean };
}

/** manifest.metrics[role]: lifetime totals, never reset. `aiu` is in whole AI units (nano / 1e9). */
export interface RoleMetrics {
  lastMs: number;
  toolCalls: number;
  sessions: number;
  calls: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  aiu: number | null;
  compactions: number;
  truncations: number;
  models: Record<string, { calls: number; inputTokens: number; outputTokens: number; aiu: number | null }>;
  reported: { usage: boolean; shutdown: boolean };
}

/** manifest.budget: the spend window the caps are checked against; --from-stage resets it. */
export interface BudgetWindow {
  since: string;
  toolCalls: number;
  aiu: number;
  inputTokens: number;
}
```

and add to `Manifest` (after `reasons?`):

```ts
  /** Spend window for the budget caps (spec §3.2). Absent on manifests written before telemetry. */
  budget?: BudgetWindow;
```

Run: `fnm exec --using=22 node.exe node_modules/typescript/bin/tsc --noEmit -p .`
Expected: clean (types only).

- [ ] **Step 2: Move the fake session into a shared module and teach it events**

Create `orchestrator/test/fake_session.ts`:

```ts
// The fake CopilotClient the runner and telemetry tests share. Its session drives the REAL hooks
// CopilotRunner built via hooksFor and emits SDK session events to whatever subscribed with on(),
// so policy.ts, hooks.ts and telemetry.ts are the code under test; only the SDK transport is faked.
import type { CopilotClient, SessionEvent, SessionHooks } from "@github/copilot-sdk";

export const invocation = { sessionId: "session-1" };

export interface ToolCall {
  toolName: string;
  toolArgs: Record<string, unknown>;
}

export interface Scenario {
  /** Tool calls the fake session runs through onPreToolUse before completing or crashing. */
  toolCalls?: ToolCall[];
  /** Session events emitted (in order) to on() subscribers after the tool calls. If abort() was
   * called while emitting, emission stops there and sendAndWait throws "session aborted". */
  events?: SessionEvent[];
  /** If set, sendAndWait throws this after the tool calls and events above. */
  throws?: unknown;
  /** Fire onErrorOccurred with this error before throwing/returning -- the SDK's own
   * error-reporting hook, independent of what sendAndWait itself throws. */
  reportError?: { error: unknown; errorContext?: "model_call" | "tool_execution" | "system" | "user_input" };
  /** Fire onSessionEnd before returning/throwing. Most real sessions do; the live crash in
   * ATTEMPT 2 did not (task-16-report.md: no second "ev":"session-end" line in audit.jsonl). */
  fireSessionEnd?: boolean;
}

/** What the fake session observed, for assertions. */
export interface FakeSessionRecord {
  aborted: boolean;
  abortCalls: number;
  /** Events the session actually emitted (stops early after an abort). */
  emitted: SessionEvent[];
  /** createSession's config, so tests can assert what reached the SDK. */
  sessionConfig?: Record<string, unknown>;
}

export function newRecord(): FakeSessionRecord {
  return { aborted: false, abortCalls: 0, emitted: [] };
}

/** Build one SDK-shaped event for a scenario. `data` is passed through untouched. */
export function event(type: string, data: Record<string, unknown>, extra: Record<string, unknown> = {}): SessionEvent {
  return { type, id: `evt-${Math.random().toString(36).slice(2)}`, timestamp: new Date().toISOString(), data, ...extra } as unknown as SessionEvent;
}

export function fakeClient(root: string, scenario: Scenario, record: FakeSessionRecord = newRecord()): CopilotClient {
  return {
    async createSession(config: { hooks?: SessionHooks } & Record<string, unknown>) {
      record.sessionConfig = config;
      const hooks = config.hooks!;
      const base = { sessionId: "session-1", timestamp: new Date(), workingDirectory: root };
      const handlers = new Set<(e: SessionEvent) => void>();
      return {
        on(handler: (e: SessionEvent) => void) {
          handlers.add(handler);
          return () => handlers.delete(handler);
        },
        async abort() {
          record.aborted = true;
          record.abortCalls += 1;
        },
        async sendAndWait() {
          for (const call of scenario.toolCalls ?? []) {
            await hooks.onPreToolUse!({ ...base, toolName: call.toolName, toolArgs: call.toolArgs }, invocation);
          }
          for (const e of scenario.events ?? []) {
            record.emitted.push(e);
            for (const handler of handlers) handler(e);
            if (record.aborted) throw new Error("session aborted");
          }
          if (scenario.reportError) {
            await hooks.onErrorOccurred!(
              { ...base, error: scenario.reportError.error as string, errorContext: scenario.reportError.errorContext ?? "model_call", recoverable: true },
              invocation,
            );
          }
          if (scenario.fireSessionEnd) {
            await hooks.onSessionEnd!({ ...base, reason: scenario.throws ? "error" : "complete" }, invocation);
          }
          if (scenario.throws !== undefined) throw scenario.throws;
          return undefined;
        },
        async disconnect() {
          return undefined;
        },
      };
    },
  } as unknown as CopilotClient;
}
```

In `orchestrator/test/runner.test.ts`: delete the local `invocation`, `ToolCall`, `Scenario` and `fakeClient` definitions and import them instead:

```ts
import { fakeClient, invocation } from "./fake_session.ts";
import type { Scenario, ToolCall } from "./fake_session.ts";
```

Keep `HALLUCINATED_PATH`, `fixture`, `runnerFor` and every test unchanged.

Run: `fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/runner.test.ts`
Expected: the same 13 tests pass (pure refactor).

- [ ] **Step 3: Write the failing telemetry tests**

Create `orchestrator/test/telemetry.test.ts`:

```ts
// telemetry.ts: SDK session events -> HookState.usage, audit.jsonl lines, manifest.metrics/budget.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { DEFAULT_CONFIG } from "../cli.ts";
import { CopilotRunner } from "../runner.ts";
import type { Env, Manifest, OrchestratorConfig, RoleMetrics } from "../types.ts";
import { event, fakeClient, newRecord } from "./fake_session.ts";
import type { Scenario } from "./fake_session.ts";

async function fixture(t: any) {
  const root = await mkdtemp(path.join(os.tmpdir(), "orch-telemetry-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, "workflows", "wf_0001"), { recursive: true });
  const wf: Manifest = { id: "wf_0001", status: {}, metrics: {} };
  const logs: string[] = [];
  const env = { root, config: DEFAULT_CONFIG, interactive: false, log: (line: string) => logs.push(line) } as unknown as Env;
  return { root, wf, env, logs };
}

async function run(t: any, scenario: Scenario, config: OrchestratorConfig = DEFAULT_CONFIG) {
  const { root, wf, env, logs } = await fixture(t);
  const record = newRecord();
  const runner = new CopilotRunner(fakeClient(root, scenario, record), root, config, "local", []);
  runner.attach(env);
  const result = await runner.run("intake", wf, "do the intake");
  const auditPath = path.join(root, "workflows", "wf_0001", "audit.jsonl");
  const audit = (await readFile(auditPath, "utf8").catch(() => "")).trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
  return { result, wf, audit, record, logs };
}

const usage = (model: string, inputTokens: number, outputTokens: number, nano?: number) =>
  event("assistant.usage", { model, inputTokens, outputTokens, cacheReadTokens: 7, cacheWriteTokens: 1, cost: 1, duration: 120, apiCallId: `c-${inputTokens}`, ...(nano === undefined ? {} : { copilotUsage: { totalNanoAiu: nano } }) });

test("usage events accumulate per model into metrics, with AIU converted from nano units", async (t) => {
  const { wf, audit } = await run(t, { events: [usage("luna", 1000, 50, 2_500_000_000), usage("luna", 500, 10, 500_000_000), usage("astra", 100, 5)] });
  const m = wf.metrics.intake as RoleMetrics;
  assert.equal(m.sessions, 1);
  assert.equal(m.calls, 3);
  assert.equal(m.inputTokens, 1600);
  assert.equal(m.outputTokens, 65);
  assert.equal(m.cacheReadTokens, 21);
  assert.equal(m.cacheWriteTokens, 3);
  assert.equal(m.aiu, 3, "2.5 + 0.5 AI units; the astra call reported none and adds nothing");
  assert.deepEqual(m.models.luna, { calls: 2, inputTokens: 1500, outputTokens: 60, aiu: 3 });
  assert.deepEqual(m.models.astra, { calls: 1, inputTokens: 100, outputTokens: 5, aiu: null });
  assert.deepEqual(m.reported, { usage: true, shutdown: false });
  const lines = audit.filter((l) => l.ev === "usage");
  assert.equal(lines.length, 3);
  assert.equal(lines[0].aiu, 2.5);
  assert.equal(lines[0].durationMs, 120);
  assert.equal("aiu" in lines[2], false, "a field the runtime did not send is omitted, not null");
});

test("a session with no usage events reports nothing rather than zeros", async (t) => {
  const { wf } = await run(t, { events: [] });
  const m = wf.metrics.intake as RoleMetrics;
  assert.equal(m.calls, 0);
  assert.equal(m.aiu, null);
  assert.deepEqual(m.reported, { usage: false, shutdown: false });
  assert.equal(wf.budget?.inputTokens, 0);
  assert.equal(wf.budget?.aiu, 0);
});

test("compaction, truncation, usage-info, model failure, sub-agent and shutdown events each become one audit line", async (t) => {
  const { wf, audit } = await run(t, {
    events: [
      event("session.compaction_start", { currentTokens: 90000, conversationTokens: 80000 }),
      event("session.compaction_complete", { trigger: "threshold", messagesRemoved: 40, currentTokens: 20000, conversationTokens: 12000, checkpointPath: "C:/x/checkpoints/1.md", checkpointNumber: 1, compactionTokensUsed: { inputTokens: 90000, outputTokens: 900 } }),
      event("session.truncation", { performedBy: "runtime", preTruncationMessagesLength: 50, postTruncationMessagesLength: 30, preTruncationTokensInMessages: 70000, postTruncationTokensInMessages: 40000 }),
      event("session.usage_info", { currentTokens: 41000, messagesLength: 31, systemTokens: 3000 }),
      event("model.call_failure", { model: "luna", error: "upstream refused: api_key=sk-live-9 and more" }),
      event("subagent.started", { agentId: "sub-1", name: "explore" }),
      event("subagent.completed", { agentId: "sub-1" }),
      event("session.shutdown", { currentTokens: 41000, totalApiDurationMs: 5000, totalNanoAiu: 3_000_000_000, modelMetrics: { luna: { requests: { count: 3 }, usage: { inputTokens: 1600, outputTokens: 65, cacheReadTokens: 0, cacheWriteTokens: 0 }, totalNanoAiu: 3_000_000_000 } } }),
    ],
  });
  const evs = audit.map((l) => l.ev);
  assert.deepEqual(evs, ["compaction-start", "compaction-complete", "truncation", "usage-info", "model-fail", "subagent-start", "subagent-complete", "shutdown"]);
  const done = audit.find((l) => l.ev === "compaction-complete");
  assert.equal(done.trigger, "threshold");
  assert.equal(done.messagesRemoved, 40);
  assert.equal(done.checkpointPath, "C:/x/checkpoints/1.md");
  assert.deepEqual(done.tokensUsed, { inputTokens: 90000, outputTokens: 900 });
  const trunc = audit.find((l) => l.ev === "truncation");
  assert.deepEqual([trunc.messagesBefore, trunc.messagesAfter, trunc.tokensBefore, trunc.tokensAfter], [50, 30, 70000, 40000]);
  const fail = audit.find((l) => l.ev === "model-fail");
  assert.doesNotMatch(fail.error, /sk-live-9/);
  assert.match(fail.error, /<redacted>/);
  const shutdown = audit.find((l) => l.ev === "shutdown");
  assert.equal(shutdown.totalNanoAiu, 3_000_000_000);
  assert.equal(shutdown.models.luna.requests, 3);
  const m = wf.metrics.intake as RoleMetrics;
  assert.equal(m.compactions, 1);
  assert.equal(m.truncations, 1);
  assert.equal(m.reported.shutdown, true);
  assert.equal(m.calls, 0, "shutdown totals are not double-counted as calls");
});

test("a session that crashes after some usage still records what it consumed", async (t) => {
  const { wf, result } = await run(t, { events: [usage("luna", 700, 20, 1_000_000_000)], throws: new Error("request (70000 tokens) exceeds the available context size (65536 tokens)") });
  assert.equal(result.ok, false);
  assert.equal(result.error, "context-overflow");
  const m = wf.metrics.intake as RoleMetrics;
  assert.equal(m.inputTokens, 700);
  assert.equal(m.aiu, 1);
  assert.equal(wf.budget?.inputTokens, 700);
});

test("two sessions of the same role add up; lastMs is the latest only", async (t) => {
  const { root, wf, env } = await fixture(t);
  const runner1 = new CopilotRunner(fakeClient(root, { events: [usage("luna", 100, 1, 1_000_000_000)] }), root, DEFAULT_CONFIG, "local", []);
  runner1.attach(env);
  await runner1.run("intake", wf, "first");
  const runner2 = new CopilotRunner(fakeClient(root, { events: [usage("luna", 200, 2)] }), root, DEFAULT_CONFIG, "local", []);
  runner2.attach(env);
  await runner2.run("intake", wf, "second");
  const m = wf.metrics.intake as RoleMetrics;
  assert.equal(m.sessions, 2);
  assert.equal(m.inputTokens, 300);
  assert.equal(m.aiu, 1, "the second session reported no AIU; the total keeps the first session's");
  assert.equal(wf.budget?.inputTokens, 300);
  assert.equal(wf.budget?.aiu, 1);
});

test("a manifest with old-style metrics gets a budget window seeded from its tool calls", async (t) => {
  const { root, env } = await fixture(t);
  const wf: Manifest = { id: "wf_0001", status: {}, metrics: { translator: { toolCalls: 12, lastMs: 5 } } };
  const runner = new CopilotRunner(fakeClient(root, { toolCalls: [{ toolName: "view", toolArgs: { path: "workflows/wf_0001/manifest.json" } }] }), root, DEFAULT_CONFIG, "local", []);
  runner.attach(env);
  await runner.run("intake", wf, "x");
  assert.equal(wf.budget?.toolCalls, 13, "12 historical + 1 in this session");
  assert.ok(wf.budget?.since);
});
```

Run: `fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/telemetry.test.ts`
Expected: FAIL — `wf.budget` undefined, `metrics.intake.calls` undefined, no telemetry audit lines (module does not exist yet).

- [ ] **Step 4: Implement `telemetry.ts`**

Create `orchestrator/telemetry.ts`:

```ts
// SDK session events -> the per-session usage totals in HookState and one audit line each.
// Everything here is optional-field access over event.data: the SDK types every usage field as
// optional, and which events a runtime version emits is only provable live (docs/live-smoke-test.md,
// "Telemetry -- to verify on a live run"). A field the runtime did not send is omitted from the
// audit line, never written as null or 0, so a reader can tell "absent" from "zero".
import type { SessionEvent } from "@github/copilot-sdk";
import { AUDIT_ARG_LIMIT, errorText, redact } from "./hooks.ts";
import type { AuditFn, HookState } from "./hooks.ts";
import type { UsageTotals } from "./types.ts";

export interface Subscribable {
  on(handler: (event: SessionEvent) => void): () => void;
}

export function emptyUsage(): UsageTotals {
  return {
    calls: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0,
    nanoAiu: null, compactions: 0, truncations: 0, models: {}, reported: { usage: false, shutdown: false },
  };
}

const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const str = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" ? (v as Record<string, unknown>) : {});

/** Drop undefined values so an audit line only carries what the runtime sent. */
function present(fields: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(fields).filter(([, v]) => v !== undefined));
}

/**
 * Subscribe to a session's events. Returns `detach`, which unsubscribes and waits for the audit
 * writes already queued, so the runner can call it before `disconnect` and be sure the lines are
 * on disk. Audit writes are chained so lines land in event order.
 */
export function attachTelemetry(
  session: Subscribable,
  state: HookState,
  audit: AuditFn,
  log: (line: string) => void,
): () => Promise<void> {
  let queue: Promise<void> = Promise.resolve();
  const record = (fields: Record<string, unknown>): void => {
    queue = queue.then(() => audit(present(fields))).catch(() => undefined);
  };
  const u = state.usage;

  const handler = (event: SessionEvent): void => {
    try {
      const data = obj((event as { data?: unknown }).data);
      const agentId = str((event as { agentId?: unknown }).agentId);
      switch (event.type) {
        case "assistant.usage": {
          const model = str(data.model) ?? "unknown";
          const input = num(data.inputTokens) ?? 0;
          const output = num(data.outputTokens) ?? 0;
          const nano = num(obj(data.copilotUsage).totalNanoAiu);
          u.calls += 1;
          u.inputTokens += input;
          u.outputTokens += output;
          u.cacheReadTokens += num(data.cacheReadTokens) ?? 0;
          u.cacheWriteTokens += num(data.cacheWriteTokens) ?? 0;
          if (nano !== undefined) u.nanoAiu = (u.nanoAiu ?? 0) + nano;
          u.reported.usage = true;
          const m = (u.models[model] ??= { calls: 0, inputTokens: 0, outputTokens: 0, nanoAiu: null });
          m.calls += 1;
          m.inputTokens += input;
          m.outputTokens += output;
          if (nano !== undefined) m.nanoAiu = (m.nanoAiu ?? 0) + nano;
          record({
            ev: "usage", model, inputTokens: num(data.inputTokens), outputTokens: num(data.outputTokens),
            cacheReadTokens: num(data.cacheReadTokens), cacheWriteTokens: num(data.cacheWriteTokens),
            cost: num(data.cost), aiu: nano === undefined ? undefined : nano / 1e9,
            durationMs: num(data.duration), apiCallId: str(data.apiCallId), agentId,
          });
          return;
        }
        case "session.compaction_start":
          record({ ev: "compaction-start", trigger: str(data.trigger), currentTokens: num(data.currentTokens), conversationTokens: num(data.conversationTokens), agentId });
          return;
        case "session.compaction_complete": {
          u.compactions += 1;
          const used = obj(data.compactionTokensUsed);
          record({
            ev: "compaction-complete", trigger: str(data.trigger), messagesRemoved: num(data.messagesRemoved),
            currentTokens: num(data.currentTokens), conversationTokens: num(data.conversationTokens),
            checkpointPath: str(data.checkpointPath), checkpointNumber: num(data.checkpointNumber),
            tokensUsed: Object.keys(used).length ? present({ inputTokens: num(used.inputTokens), outputTokens: num(used.outputTokens) }) : undefined,
            error: data.error === undefined ? undefined : redact(errorText(data.error)).slice(0, AUDIT_ARG_LIMIT), agentId,
          });
          return;
        }
        case "session.truncation":
          u.truncations += 1;
          record({
            ev: "truncation", performedBy: str(data.performedBy),
            messagesBefore: num(data.preTruncationMessagesLength), messagesAfter: num(data.postTruncationMessagesLength),
            tokensBefore: num(data.preTruncationTokensInMessages), tokensAfter: num(data.postTruncationTokensInMessages), agentId,
          });
          return;
        case "session.usage_info":
          record({ ev: "usage-info", currentTokens: num(data.currentTokens), messagesLength: num(data.messagesLength), systemTokens: num(data.systemTokens), agentId });
          return;
        case "model.call_failure":
          record({ ev: "model-fail", model: str(data.model), error: redact(errorText(data.error ?? data.message ?? "")).slice(0, AUDIT_ARG_LIMIT), agentId });
          return;
        case "subagent.started":
          record({ ev: "subagent-start", agentId: str(data.agentId) ?? agentId, name: str(data.name) ?? str(data.agentName) });
          return;
        case "subagent.completed":
          record({ ev: "subagent-complete", agentId: str(data.agentId) ?? agentId });
          return;
        case "subagent.failed":
          record({ ev: "subagent-fail", agentId: str(data.agentId) ?? agentId, error: data.error === undefined ? undefined : redact(errorText(data.error)).slice(0, AUDIT_ARG_LIMIT) });
          return;
        case "session.shutdown": {
          u.reported.shutdown = true;
          const models: Record<string, unknown> = {};
          for (const [id, raw] of Object.entries(obj(data.modelMetrics))) {
            const m = obj(raw);
            const usage = obj(m.usage);
            const nano = num(m.totalNanoAiu);
            models[id] = present({
              requests: num(obj(m.requests).count), inputTokens: num(usage.inputTokens), outputTokens: num(usage.outputTokens),
              aiu: nano === undefined ? undefined : nano / 1e9,
            });
          }
          record({ ev: "shutdown", currentTokens: num(data.currentTokens), totalApiDurationMs: num(data.totalApiDurationMs), totalNanoAiu: num(data.totalNanoAiu), models });
          return;
        }
        default:
          return;
      }
    } catch (error) {
      log(`telemetry handler error: ${redact(errorText(error)).slice(0, AUDIT_ARG_LIMIT)}`);
    }
  };

  let unsubscribe: () => void = () => undefined;
  try {
    unsubscribe = session.on(handler);
  } catch (error) {
    record({ ev: "telemetry-unavailable", error: redact(errorText(error)).slice(0, AUDIT_ARG_LIMIT) });
  }
  return async () => {
    unsubscribe();
    await queue;
  };
}
```

Note `??=`: allowed under `erasableSyntaxOnly` (it is runtime JS). Note `event.type` is a string union in the SDK; the `switch` narrows on it. The shutdown handler does **not** add to `calls`/tokens — the `assistant.usage` events already counted them (spec §3.1).

- [ ] **Step 5: Extend `HookState`, `hooksFor` and `recordMetrics` in `hooks.ts`**

Add to the `HookState` interface (after `metricsRecorded`):

```ts
  /** Accumulated from the session's event stream by telemetry.ts; folded by recordMetrics. */
  usage: UsageTotals;
  /** Set when the kill switch aborted the session (Task 3); classified as `budget` by the runner. */
  budgetAbort: boolean;
```

Import the types: `import type { BudgetWindow, Env, Manifest, Role, RoleMetrics, UsageTotals } from "./types.ts";` (add `BudgetWindow`, `RoleMetrics`, `UsageTotals` to the existing import) and `import { emptyUsage } from "./telemetry.ts";` (a value import; telemetry.ts imports only *types* from hooks.ts plus `redact`/`errorText`/`AUDIT_ARG_LIMIT` — those are values, so make sure hooks.ts defines `AUDIT_ARG_LIMIT`, `redact`, `errorText` **above** the `import`-time use; ES modules hoist, and neither module runs the other's functions at import time, so the cycle is safe).

Add `export type AuditFn = (record: Record<string, unknown>) => Promise<void>;` near the top, and in `hooksFor` initialise `usage: emptyUsage(), budgetAbort: false` in the `state` literal, type the local `audit` as `AuditFn`, and change the return to `return { hooks, state, audit };` (update the return type annotation to `{ hooks: SessionHooks; state: HookState; audit: AuditFn }`).

Replace `recordMetrics` with:

```ts
const n = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

/** Lifetime AI units: prior total plus this session's nano-AIU; null stays null when nothing was ever reported. */
function addAiu(prior: unknown, nano: number | null): number | null {
  const before = typeof prior === "number" ? prior : null;
  if (nano === null) return before;
  return (before ?? 0) + nano / 1e9;
}

/** The spend window; created on first use, seeded from the old cumulative tool-call metrics so a
 * manifest written before telemetry keeps the spend it had (spec §3.2). */
export function ensureBudget(wf: Manifest): BudgetWindow {
  if (!wf.budget) {
    let toolCalls = 0;
    for (const entry of Object.values(wf.metrics ?? {})) toolCalls += n((entry as { toolCalls?: unknown } | null)?.toolCalls);
    wf.budget = { since: new Date().toISOString(), toolCalls, aiu: 0, inputTokens: 0 };
  }
  return wf.budget;
}

/** Fold one session into manifest.metrics[role] (lifetime, never reset) and manifest.budget (the
 * window). Idempotent per session: onSessionEnd and the runner's finally may both call it. */
export function recordMetrics(wf: Manifest, role: Role, state: HookState, ms: number): void {
  if (state.metricsRecorded) return;
  state.metricsRecorded = true;
  const u = state.usage;
  const prev = (wf.metrics[role] ?? {}) as Partial<RoleMetrics>;
  const models: RoleMetrics["models"] = { ...(prev.models ?? {}) };
  for (const [id, m] of Object.entries(u.models)) {
    const p = models[id] ?? { calls: 0, inputTokens: 0, outputTokens: 0, aiu: null };
    models[id] = { calls: p.calls + m.calls, inputTokens: p.inputTokens + m.inputTokens, outputTokens: p.outputTokens + m.outputTokens, aiu: addAiu(p.aiu, m.nanoAiu) };
  }
  const next: RoleMetrics = {
    ...prev,
    lastMs: ms,
    toolCalls: n(prev.toolCalls) + state.toolCalls,
    sessions: n(prev.sessions) + 1,
    calls: n(prev.calls) + u.calls,
    inputTokens: n(prev.inputTokens) + u.inputTokens,
    outputTokens: n(prev.outputTokens) + u.outputTokens,
    cacheReadTokens: n(prev.cacheReadTokens) + u.cacheReadTokens,
    cacheWriteTokens: n(prev.cacheWriteTokens) + u.cacheWriteTokens,
    aiu: addAiu(prev.aiu, u.nanoAiu),
    compactions: n(prev.compactions) + u.compactions,
    truncations: n(prev.truncations) + u.truncations,
    models,
    reported: { usage: Boolean(prev.reported?.usage) || u.reported.usage, shutdown: Boolean(prev.reported?.shutdown) || u.reported.shutdown },
  };
  wf.metrics[role] = next;
  const budget = ensureBudget(wf);
  budget.toolCalls += state.toolCalls;
  budget.aiu += (u.nanoAiu ?? 0) / 1e9;
  budget.inputTokens += u.inputTokens;
}
```

Careful: `ensureBudget` must be called **before** the session's own `toolCalls` are added to the window only once — the seed sums `metrics.*.toolCalls`, which at that moment already includes this session (we assigned `wf.metrics[role] = next` first). So call `ensureBudget(wf)` **before** assigning `next`, then add. Rewrite the tail of `recordMetrics` as:

```ts
  const budget = ensureBudget(wf);   // seeded from metrics BEFORE this session is folded in
  wf.metrics[role] = next;
  budget.toolCalls += state.toolCalls;
  budget.aiu += (u.nanoAiu ?? 0) / 1e9;
  budget.inputTokens += u.inputTokens;
```

- [ ] **Step 6: Subscribe in the runner**

In `orchestrator/runner.ts`: import `attachTelemetry` from `./telemetry.ts`; change `const { hooks, state } = hooksFor(...)` to `const { hooks, state, audit } = hooksFor(...)`; declare `let detach: (() => Promise<void>) | undefined;` next to `let session;`; right after `session = await this.client.createSession({...});` add:

```ts
      detach = attachTelemetry(session, state, audit, env.log);
```

and at the top of the `finally` block, before `disconnect`:

```ts
      if (detach) await detach().catch(() => undefined);
```

Run: `fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/telemetry.test.ts orchestrator/test/runner.test.ts orchestrator/test/hooks.test.ts`
Expected: all pass (6 new + the existing ones).

- [ ] **Step 7: Full suites and commit**

Run: `fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts` — expected 156 pass (150 + 6), 0 fail.
Run: `fnm exec --using=22 node.exe node_modules/typescript/bin/tsc --noEmit -p .` — clean.
Run: `.venv/Scripts/python.exe -m pytest tests/test_committed_workflows.py` — passes (no manifest under `workflows/` changed).

```bash
git add orchestrator/types.ts orchestrator/telemetry.ts orchestrator/hooks.ts orchestrator/runner.ts orchestrator/test/fake_session.ts orchestrator/test/runner.test.ts orchestrator/test/telemetry.test.ts
git commit -m "feat: session telemetry — usage, compaction and truncation events recorded per role into audit.jsonl, manifest.metrics and manifest.budget"
```

---

### Task 2: Spend window, three caps between calls, `--from-stage` resets the window

**Files:**
- Modify: `orchestrator/types.ts` (`budgets` fields, `AgentResult.reason?`, `Spend`)
- Modify: `orchestrator/hooks.ts` (`spendUsed`, `resetBudget`)
- Modify: `orchestrator/stages.ts` (`exceededCap`, `runAgent` check, `escalate`, `clearFromStage`, `toolCallsUsed`)
- Modify: `orchestrator/cli.ts` (`loadConfig` validation)
- Test: `orchestrator/test/stages.test.ts`, `orchestrator/test/cli.test.ts`

**Interfaces:**
- Consumes: `ensureBudget` (Task 1), `Manifest.budget`.
- Produces: `spendUsed(m: Manifest): Spend` and `resetBudget(m: Manifest): Spend` (hooks.ts); `exceededCap(spend: Spend, budgets: OrchestratorConfig["budgets"]): CapHit | undefined` (stages.ts, exported); `AgentResult.reason?: string`; config fields `budgets.maxAiuPerWorkflow?: number | null`, `budgets.maxInputTokensPerWorkflow?: number | null`. Task 3 uses `spendUsed` and the `reason` field.

- [ ] **Step 1: Failing tests**

Append to `orchestrator/test/stages.test.ts` (after the F8 tests; `makeEnv` accepts `config` overrides and `manifest`):

```ts
// --- telemetry: three caps over the spend window ------------------------------------------------

test("an AI-unit cap overrun parks the workflow with the cap and numbers in the reason", async () => {
  const { env, calls } = await makeEnv({
    wf: "wf_0001",
    manifest: { budget: { since: "2026-09-22T00:00:00Z", toolCalls: 0, aiu: 12.4, inputTokens: 0 } },
    config: { budgets: { maxToolCallsPerWorkflow: 400, maxAiuPerWorkflow: 10 } },
  });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.parse, "NEEDS_HUMAN");
  assert.equal(m.reasons?.parse, "budget: aiu 12.4 > 10");
  assert.equal(calls.roles.length, 0, "no agent ran");
  assert.ok(calls.logs.some((line) => line.includes("budget: aiu 12.4 > 10")));
});

test("an input-token cap overrun parks the workflow too; the first exceeded cap wins in the order tool calls, aiu, input tokens", async () => {
  const both = await makeEnv({
    wf: "wf_0001",
    manifest: { budget: { since: "2026-09-22T00:00:00Z", toolCalls: 401, aiu: 0, inputTokens: 2_000_000 } },
    config: { budgets: { maxToolCallsPerWorkflow: 400, maxInputTokensPerWorkflow: 1_000_000 } },
  });
  const m = await migrateWorkflow(both.env, "wf_0001", {});
  assert.equal(m.reasons?.parse, "budget: toolCalls 401 > 400");
  const tokens = await makeEnv({
    wf: "wf_0001",
    manifest: { budget: { since: "2026-09-22T00:00:00Z", toolCalls: 0, aiu: 0, inputTokens: 2_000_000 } },
    config: { budgets: { maxToolCallsPerWorkflow: 400, maxInputTokensPerWorkflow: 1_000_000 } },
  });
  const m2 = await migrateWorkflow(tokens.env, "wf_0001", {});
  assert.equal(m2.reasons?.parse, "budget: inputTokens 2000000 > 1000000");
});

test("an absent cap is unlimited", async () => {
  const { env, calls } = await makeEnv({
    wf: "wf_0001",
    manifest: { budget: { since: "2026-09-22T00:00:00Z", toolCalls: 0, aiu: 999, inputTokens: 99_000_000 } },
  });
  await migrateWorkflow(env, "wf_0001", {});
  assert.ok(calls.roles.length > 0, "agents ran: only the tool-call cap is set by default");
});

test("--from-stage resets the spend window and not the lifetime metrics", async () => {
  const { env, calls } = await makeEnv({
    wf: "wf_0001",
    manifest: {
      metrics: { translator: { toolCalls: 401, lastMs: 9, inputTokens: 5000, aiu: 3 } },
      budget: { since: "2026-09-22T00:00:00Z", toolCalls: 401, aiu: 3, inputTokens: 5000 },
    },
  });
  await migrateWorkflow(env, "wf_0001", {});               // parks on the tool-call cap
  const m = await migrateWorkflow(env, "wf_0001", { fromStage: "translate" });
  assert.ok(calls.logs.some((line) => line.includes("reset the spend window (tool calls 401, aiu 3, input tokens 5000)")));
  assert.equal(m.metrics.translator.toolCalls, 401, "lifetime metrics are never reset");
  assert.equal(m.metrics.translator.inputTokens, 5000);
  assert.ok((m.budget?.toolCalls ?? 999) < 401, "the window started again from zero");
  assert.notEqual(m.budget?.since, "2026-09-22T00:00:00Z");
});

test("a manifest without a budget window falls back to the sum of metrics tool calls", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", manifest: { metrics: { translator: { toolCalls: 401 } } } });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.reasons?.parse, "budget: toolCalls 401 > 400");
  assert.equal(calls.roles.length, 0);
});
```

Re-point the three existing F8 tests: in `"F8: a plain re-run of an over-budget workflow still parks; --from-stage grants a fresh budget…"` change the log assertion from `"reset the tool-call budget"` to `"reset the spend window"`; in `"F8: metrics after the reopened run count only the NEW calls…"` replace the assertion `assert.equal(reopened.metrics.translator?.toolCalls, 0, …)` with `assert.equal(reopened.metrics.translator?.toolCalls, 401, "lifetime metrics keep the old spend")` and change the `total < 401` check to read the window: `assert.ok((reopened.budget?.toolCalls ?? 0) < 401, "the window's spend after the reset is nowhere near the old overrun")`; in `"F8: a --from-stage on a workflow that was never over budget logs nothing about a reset"` change `"reset the tool-call budget"` to `"reset the spend window"`. Also update the existing test at line 227 (`"a tool-call budget overrun stops the workflow…"`) to assert `m.reasons?.parse === "budget: toolCalls 401 > 400"` in addition to its log check.

Append to `orchestrator/test/cli.test.ts`:

```ts
test("a zero spend cap is a usage error, not a workflow that parks before its first call", async () => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  for (const budgets of [{ maxAiuPerWorkflow: 0 }, { maxInputTokensPerWorkflow: 0 }, { maxToolCallsPerWorkflow: 0 }]) {
    await writeJson(`${root}/orchestrator.config.json`, { budgets });
    await assert.rejects(loadConfig(root), (error: unknown) => {
      assert.ok(error instanceof UsageError, `expected a UsageError for ${JSON.stringify(budgets)}`);
      assert.match((error as Error).message, /budgets\./);
      return true;
    });
  }
  await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxAiuPerWorkflow: null } });
  assert.equal((await loadConfig(root)).budgets.maxAiuPerWorkflow, null, "null means unlimited and is accepted");
});
```

Run: `fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/stages.test.ts orchestrator/test/cli.test.ts`
Expected: the new tests FAIL (reasons read `budget`, no window reset line, `loadConfig` accepts 0).

- [ ] **Step 2: Types and config validation**

`orchestrator/types.ts`: change `budgets: { maxToolCallsPerWorkflow: number };` to

```ts
  budgets: {
    maxToolCallsPerWorkflow: number;
    /** Copilot AI units per workflow window; absent or null = unlimited (checked between calls only). */
    maxAiuPerWorkflow?: number | null;
    /** Input tokens per workflow window; absent or null = unlimited; also arms the mid-session kill switch. */
    maxInputTokensPerWorkflow?: number | null;
  };
```

add `export interface Spend { toolCalls: number; aiu: number; inputTokens: number }` after `BudgetWindow`, and add to `AgentResult`: `/** Human-readable escalation reason when `detail` alone is too terse (e.g. "budget: aiu 12.4 > 10"). */ reason?: string;`.

`orchestrator/cli.ts`, in `loadConfig` after the merged object is built (bind it to `const config = {...}` and return it at the end): 

```ts
  for (const [key, value] of Object.entries(config.budgets)) {
    if (value === undefined || value === null) continue;
    if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
      throw new UsageError(`budgets.${key} must be a positive number or null (unlimited), got ${JSON.stringify(value)}`);
    }
  }
  return config;
```

- [ ] **Step 3: `spendUsed`, `resetBudget`, `exceededCap`, the check, the reset**

`orchestrator/hooks.ts` (next to `ensureBudget`):

```ts
/** The spend the caps are checked against: the window, or (older manifest) the metrics tool-call sum. */
export function spendUsed(wf: Manifest): Spend {
  if (wf.budget) return { toolCalls: wf.budget.toolCalls, aiu: wf.budget.aiu, inputTokens: wf.budget.inputTokens };
  let toolCalls = 0;
  for (const entry of Object.values(wf.metrics ?? {})) toolCalls += n((entry as { toolCalls?: unknown } | null)?.toolCalls);
  return { toolCalls, aiu: 0, inputTokens: 0 };
}

/** Start the window again from zero (an explicit --from-stage); returns what it held. */
export function resetBudget(wf: Manifest): Spend {
  const before = spendUsed(wf);
  wf.budget = { since: new Date().toISOString(), toolCalls: 0, aiu: 0, inputTokens: 0 };
  return before;
}
```

(import `Spend` in hooks.ts.)

`orchestrator/stages.ts`: import `resetBudget, spendUsed` from `./hooks.ts` and `Spend` from `./types.ts`; replace `toolCallsUsed` with

```ts
export interface CapHit { cap: "toolCalls" | "aiu" | "inputTokens"; used: number; limit: number }

/** The first cap the spend exceeds, in the order tool calls, AI units, input tokens (spec §4.2). */
export function exceededCap(spend: Spend, budgets: OrchestratorConfig["budgets"]): CapHit | undefined {
  const checks: Array<[CapHit["cap"], number, number | null | undefined]> = [
    ["toolCalls", spend.toolCalls, budgets.maxToolCallsPerWorkflow],
    ["aiu", spend.aiu, budgets.maxAiuPerWorkflow],
    ["inputTokens", spend.inputTokens, budgets.maxInputTokensPerWorkflow],
  ];
  for (const [cap, used, limit] of checks) {
    if (typeof limit === "number" && used > limit) return { cap, used, limit };
  }
  return undefined;
}

/** A cap's numbers as they appear in reasons and logs: integers plain, AI units to one decimal. */
export function capReason(hit: CapHit): string {
  const fmt = (v: number) => (hit.cap === "aiu" ? String(Math.round(v * 10) / 10) : String(v));
  return `budget: ${hit.cap} ${fmt(hit.used)} > ${fmt(hit.limit)}`;
}

/** Kept for existing tests; the window's tool calls (spec §3.2 fallback for old manifests). */
export function toolCallsUsed(m: Manifest): number {
  return spendUsed(m).toolCalls;
}
```

(import `OrchestratorConfig` type in stages.ts if not already.) In `runAgent`, replace the `budget`/`used` check with:

```ts
    const hit = exceededCap(spendUsed(m), env.config.budgets);
    if (hit) {
      const why = capReason(hit);
      env.log(`${m.id}: ${why}; not starting ${role}`);
      return { ok: false, error: "error", detail: "budget", reason: why, toolCalls: 0, ms: 0 };
    }
```

(and delete the now-unused `const budget = env.config.budgets.maxToolCallsPerWorkflow;`). In `escalate`, change the first line to `const why = result.reason ?? (result.detail === "budget" ? "budget" : (result.error ?? "error"));`. Wherever `migrateSegment` builds its `${segment}: budget` reason from a result (F13), use `result.reason ?? "budget"` in place of the literal so the text becomes `seg_01: budget: aiu 12.4 > 10` (the F13 regex `/seg_01: budget/` still matches).

In `clearFromStage`, replace the block from `const before = toolCallsUsed(m);` to the end with:

```ts
  const before = spendUsed(m);
  if (before.toolCalls > 0 || before.aiu > 0 || before.inputTokens > 0) {
    resetBudget(m);
    env.log(`${m.id}: --from-stage ${from} reset the spend window (tool calls ${before.toolCalls}, aiu ${Math.round(before.aiu * 10) / 10}, input tokens ${before.inputTokens})`);
  }
```

- [ ] **Step 4: Run, then the full suites**

Run: `fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts` — expected 162 pass (156 + 5 stages + 1 cli), 0 fail. Run `tsc --noEmit -p .` — clean.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/types.ts orchestrator/hooks.ts orchestrator/stages.ts orchestrator/cli.ts orchestrator/test/stages.test.ts orchestrator/test/cli.test.ts
git commit -m "feat: spend window with tool-call, AI-unit and input-token caps; --from-stage resets the window, never the metrics"
```

---

### Task 3: Mid-session kill switch on input tokens

**Files:**
- Modify: `orchestrator/telemetry.ts` (guard parameter), `orchestrator/runner.ts` (guard + classification)
- Test: `orchestrator/test/telemetry.test.ts`

**Interfaces:**
- Consumes: `spendUsed` (Task 2), `HookState.budgetAbort` (Task 1), `AgentResult.reason` (Task 2).
- Produces: `export interface TelemetryGuard { limit?: number | null; alreadyUsed: number; abort(): Promise<void> }`; `attachTelemetry(session, state, audit, log, guard?)`.

- [ ] **Step 1: Failing tests** (append to `telemetry.test.ts`)

```ts
const withTokenCap = (limit: number | null): OrchestratorConfig => ({ ...DEFAULT_CONFIG, budgets: { ...DEFAULT_CONFIG.budgets, maxInputTokensPerWorkflow: limit } });

test("the kill switch aborts the session on the usage event that crosses the input-token cap, and no later event is processed", async (t) => {
  const { result, wf, audit, record } = await run(t, { events: [usage("luna", 400_000, 1), usage("luna", 400_000, 1), usage("luna", 400_000, 1), usage("luna", 400_000, 1)] }, withTokenCap(1_000_000));
  assert.equal(record.abortCalls, 1);
  assert.equal(record.emitted.length, 3, "emission stopped at the crossing event");
  assert.equal(result.ok, false);
  assert.equal(result.detail, "budget");
  assert.equal(result.reason, "budget: inputTokens 1200000 > 1000000 (session aborted)");
  const abortLine = audit.find((l) => l.ev === "budget-abort");
  assert.deepEqual([abortLine.cap, abortLine.used, abortLine.limit], ["inputTokens", 1_200_000, 1_000_000]);
  assert.equal((wf.metrics.intake as RoleMetrics).inputTokens, 1_200_000, "what was consumed is still recorded");
});

test("the kill switch counts spend already in the window before this session", async (t) => {
  const { root, env } = await fixture(t);
  const wf: Manifest = { id: "wf_0001", status: {}, metrics: {}, budget: { since: "2026-09-22T00:00:00Z", toolCalls: 0, aiu: 0, inputTokens: 900_000 } };
  const record = newRecord();
  const runner = new CopilotRunner(fakeClient(root, { events: [usage("luna", 50_000, 1), usage("luna", 60_000, 1)] }, record), root, withTokenCap(1_000_000), "local", []);
  runner.attach(env);
  const result = await runner.run("intake", wf, "x");
  assert.equal(record.abortCalls, 1);
  assert.equal(record.emitted.length, 2);
  assert.equal(result.reason, "budget: inputTokens 1010000 > 1000000 (session aborted)");
});

test("without an input-token cap the kill switch is not armed", async (t) => {
  const { record, result } = await run(t, { events: [usage("luna", 5_000_000, 1)] }, withTokenCap(null));
  assert.equal(record.abortCalls, 0);
  assert.equal(result.ok, true);
});

test("a budget abort is classified before any error-text signature and is not retried by runAgent", async (t) => {
  // sendAndWait throws "session aborted" after the fake stops emitting; the runner must report budget, not error.
  const { result } = await run(t, { events: [usage("luna", 2_000_000, 1)] }, withTokenCap(1_000_000));
  assert.equal(result.error, "error");
  assert.equal(result.detail, "budget");
});
```

Run: `… --test orchestrator/test/telemetry.test.ts` — expected: the four new tests FAIL (`abortCalls` 0, `detail` undefined).

- [ ] **Step 2: Implement the guard**

`orchestrator/telemetry.ts`: add

```ts
/** The kill switch: abort the session on the usage event that takes the window past the cap. */
export interface TelemetryGuard {
  limit?: number | null;
  /** Input tokens already in the window when the session started. */
  alreadyUsed: number;
  abort(): Promise<void>;
}
```

give `attachTelemetry` a fifth parameter `guard?: TelemetryGuard`, and in the `assistant.usage` case, after the `record({ ev: "usage", … })` call:

```ts
          if (guard && typeof guard.limit === "number" && !state.budgetAbort) {
            const used = guard.alreadyUsed + u.inputTokens;
            if (used > guard.limit) {
              state.budgetAbort = true;
              state.budgetAbortReason = `budget: inputTokens ${used} > ${guard.limit} (session aborted)`;
              record({ ev: "budget-abort", cap: "inputTokens", used, limit: guard.limit });
              queue = queue.then(() => guard.abort()).catch((error) => log(`session abort failed: ${redact(errorText(error)).slice(0, AUDIT_ARG_LIMIT)}`));
            }
          }
```

The fake `abort()` is synchronous in effect, so its `aborted` flag is set before the fake checks it after the handler returns — but only because the queue's `.then` runs as a microtask before the fake's `await`-free loop continues? It does not: the fake checks `record.aborted` synchronously after calling the handlers. Call `abort()` **synchronously** instead: replace the last line with

```ts
              void guard.abort().catch((error) => log(`session abort failed: ${redact(errorText(error)).slice(0, AUDIT_ARG_LIMIT)}`));
```

Add `budgetAbortReason?: string;` to `HookState` (hooks.ts) and initialise nothing (undefined).

`orchestrator/runner.ts`: import `spendUsed` from `./hooks.ts`; build the guard and pass it:

```ts
      const cap = this.config.budgets.maxInputTokensPerWorkflow;
      const guard = typeof cap === "number"
        ? { limit: cap, alreadyUsed: spendUsed(wf).inputTokens, abort: () => withTimeout(session!.abort(), 5000) }
        : undefined;
      detach = attachTelemetry(session, state, audit, env.log, guard);
```

with, at module level:

```ts
/** abort() is awaited with a bound so a hung runtime cannot hold the runner (spec §4.3). */
function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`timed out after ${ms} ms`)), ms);
    p.then((v) => { clearTimeout(timer); resolve(v); }, (e) => { clearTimeout(timer); reject(e); });
  });
}
```

In the `catch` block, **first** line before the timeout test:

```ts
      if (state.budgetAbort) return done("error", "budget", state.budgetAbortReason);
```

and after the `finally`, before the `state.denied` completion check, the same line again (a runtime that resolves `sendAndWait` after an abort must still be classified as a budget stop). Extend `done` to `(error?: AgentError, detail?: string, reason?: string): AgentResult => ({ …, reason })`.

`runAgent` already treats `detail === "budget"` as non-retryable (`RETRY_ONCE` covers only missing-output and timeout) and `escalate` now prefers `result.reason` (Task 2).

- [ ] **Step 3: Run everything**

`… --test orchestrator/test/*.test.ts` — expected 166 pass; `tsc --noEmit -p .` clean.

- [ ] **Step 4: Commit**

```bash
git add orchestrator/telemetry.ts orchestrator/hooks.ts orchestrator/runner.ts orchestrator/test/telemetry.test.ts
git commit -m "feat: input-token kill switch aborts a session that crosses the workflow cap and parks it as budget"
```

---

### Task 4: `scripts/cost_report.py`

**Files:**
- Create: `scripts/cost_report.py`
- Test: `tests/test_cost_report.py`

**Interfaces:**
- Consumes: `manifest.json` shapes from Tasks 1–2 (`metrics[role]` as `RoleMetrics`, `tier`, `status`).
- Produces: `build_rows(manifests: list[dict]) -> list[dict]`, `render_csv(rows) -> str`, `render_md(rows) -> str`, `main(argv=None) -> int`.

- [ ] **Step 1: Failing tests**

Create `tests/test_cost_report.py`:

```python
"""scripts/cost_report.py: manifests -> rows -> CSV / Markdown, deterministic, honest about coverage."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import cost_report


def _manifest(wf: str, tier: str | None, status: dict, metrics: dict) -> dict:
    return {"id": wf, "tier": tier, "status": status, "metrics": metrics}


REPORTED = {
    "lastMs": 1200, "toolCalls": 37, "sessions": 2, "calls": 41, "inputTokens": 512340, "outputTokens": 8120,
    "cacheReadTokens": 402000, "cacheWriteTokens": 0, "aiu": 12.4, "compactions": 1, "truncations": 0,
    "models": {"luna": {"calls": 40, "inputTokens": 500000, "outputTokens": 8000, "aiu": 12.4},
               "astra": {"calls": 1, "inputTokens": 12340, "outputTokens": 120, "aiu": None}},
    "reported": {"usage": True, "shutdown": True},
}
NOT_REPORTED = {"lastMs": 300, "toolCalls": 5, "sessions": 1, "calls": 0, "inputTokens": 0, "outputTokens": 0,
                "cacheReadTokens": 0, "cacheWriteTokens": 0, "aiu": None, "compactions": 0, "truncations": 0,
                "models": {}, "reported": {"usage": False, "shutdown": False}}
OLD_STYLE = {"lastMs": 10, "toolCalls": 3}


def _write(root: Path, manifests: list[dict]) -> None:
    for m in manifests:
        d = root / "workflows" / m["id"]
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")


def test_rows_one_per_workflow_role_model_with_coverage(tmp_path):
    _write(tmp_path, [
        _manifest("wf_0002", "T1", {"translate": "VALIDATED"}, {"intake": REPORTED, "translator": NOT_REPORTED}),
        _manifest("wf_0001", "T3", {"translate": "MANUAL"}, {"documenter": OLD_STYLE}),
        _manifest("wf_0003", None, {}, {}),
    ])
    rows = cost_report.build_rows(cost_report.load_manifests(tmp_path))
    keys = [(r["workflow"], r["role"], r["model"]) for r in rows if r["kind"] == "row"]
    assert keys == [("wf_0001", "documenter", ""), ("wf_0002", "intake", "astra"), ("wf_0002", "intake", "luna"),
                    ("wf_0002", "translator", ""), ("wf_0003", "", "")], "sorted by workflow, role, model"
    luna = next(r for r in rows if r["model"] == "luna")
    assert (luna["calls"], luna["input_tokens"], luna["aiu"], luna["coverage"]) == (40, 500000, 12.4, "reported")
    astra = next(r for r in rows if r["model"] == "astra")
    assert astra["aiu"] == "", "the runtime reported no AI units for that model: blank, not 0"
    translator = next(r for r in rows if r["role"] == "translator")
    assert translator["coverage"] == "not reported by runtime"
    assert translator["input_tokens"] == "" and translator["tool_calls"] == 5
    old = next(r for r in rows if r["role"] == "documenter")
    assert old["coverage"] == "not reported by runtime" and old["tool_calls"] == 3
    empty = next(r for r in rows if r["workflow"] == "wf_0003")
    assert empty["coverage"] == "no sessions" and empty["terminal_status"] == ""


def test_totals_per_workflow_and_per_tier_sum_only_reported_rows(tmp_path):
    _write(tmp_path, [
        _manifest("wf_0001", "T1", {"translate": "VALIDATED"}, {"intake": REPORTED}),
        _manifest("wf_0002", "T1", {"translate": "VALIDATED"}, {"intake": REPORTED, "fixer": NOT_REPORTED}),
    ])
    rows = cost_report.build_rows(cost_report.load_manifests(tmp_path))
    wf_totals = {r["workflow"]: r for r in rows if r["kind"] == "workflow_total"}
    assert wf_totals["wf_0002"]["input_tokens"] == 512340 and wf_totals["wf_0002"]["aiu"] == 12.4
    assert wf_totals["wf_0002"]["tool_calls"] == 42, "tool calls are counted for every role, reported or not"
    tier = next(r for r in rows if r["kind"] == "tier_total" and r["tier"] == "T1")
    assert tier["input_tokens"] == 2 * 512340 and tier["aiu"] == 24.8 and tier["workflows"] == 2


def test_csv_and_markdown_are_byte_identical_across_runs(tmp_path):
    _write(tmp_path, [_manifest("wf_0001", "T1", {"translate": "VALIDATED"}, {"intake": REPORTED})])
    out = tmp_path / "reports" / "cost"
    assert cost_report.main(["--root", str(tmp_path), "--out", str(out), "--format", "both"]) == 0
    first = (out.with_suffix(".csv").read_bytes(), out.with_suffix(".md").read_bytes())
    assert cost_report.main(["--root", str(tmp_path), "--out", str(out), "--format", "both"]) == 0
    assert (out.with_suffix(".csv").read_bytes(), out.with_suffix(".md").read_bytes()) == first
    csv_text = first[0].decode("utf-8")
    assert csv_text.splitlines()[0] == "kind,workflow,tier,terminal_status,role,model,sessions,calls,input_tokens,output_tokens,cache_read_tokens,aiu,tool_calls,last_ms,coverage,workflows"
    md_text = first[1].decode("utf-8")
    assert "| wf_0001 |" in md_text and "not reported" not in md_text and "12.4" in md_text


def test_corrupt_manifest_is_a_usage_error_naming_the_file(tmp_path, capsys):
    (tmp_path / "workflows" / "wf_0009").mkdir(parents=True)
    (tmp_path / "workflows" / "wf_0009" / "manifest.json").write_text("{ not json", encoding="utf-8")
    assert cost_report.main(["--root", str(tmp_path), "--out", str(tmp_path / "r")]) == 2
    assert "wf_0009" in capsys.readouterr().err


def test_no_workflows_directory_is_a_usage_error(tmp_path):
    assert cost_report.main(["--root", str(tmp_path), "--out", str(tmp_path / "r")]) == 2
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_cost_report.py` — expected: `ModuleNotFoundError: cost_report`.

- [ ] **Step 2: Implement**

Create `scripts/cost_report.py`:

```python
"""Cost report: every workflows/*/manifest.json -> one row per workflow, role and model, plus
per-workflow and per-tier totals, as CSV and/or Markdown.

    .venv/Scripts/python.exe scripts/cost_report.py [--root .] [--out reports/cost] [--format csv|md|both]

Numbers come only from the manifests (written by the orchestrator from the Copilot SDK's usage
events). A role whose runtime never reported usage shows blanks and "not reported by runtime",
never zeros. AI units are what Copilot bills; no currency is applied. Exit 0 on success, 2 on a
usage error or an unreadable manifest (named on stderr).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path

COLUMNS = ["kind", "workflow", "tier", "terminal_status", "role", "model", "sessions", "calls", "input_tokens",
           "output_tokens", "cache_read_tokens", "aiu", "tool_calls", "last_ms", "coverage", "workflows"]
STAGES = ["pr", "document", "translate", "golden", "analyze", "intake", "parse"]


def load_manifests(root: Path) -> list[dict]:
    """Every workflows/<id>/manifest.json under root, sorted by id. Raises FileNotFoundError when
    there is no workflows directory and ValueError (naming the file) when one does not parse."""
    base = root / "workflows"
    if not base.is_dir():
        raise FileNotFoundError(f"no workflows directory under {root}")
    manifests = []
    for path in sorted(base.glob("*/manifest.json")):
        try:
            manifests.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise ValueError(f"{path.parent.name}: cannot read manifest.json: {exc}") from exc
    return manifests


def _terminal_status(manifest: dict) -> str:
    status = manifest.get("status") or {}
    for stage in STAGES:
        if stage in status:
            return f"{stage}={status[stage]}"
    return ""


def _num(value: object) -> int | float | str:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else ""


def _blank_row(**fields: object) -> dict:
    row = {column: "" for column in COLUMNS}
    row.update(fields)
    return row


def build_rows(manifests: list[dict]) -> list[dict]:
    """Rows of kind "row" (workflow x role x model), "workflow_total" and "tier_total", in a
    deterministic order: workflow id, role, model id; totals after their group."""
    rows: list[dict] = []
    tiers: dict[str, dict] = {}
    for manifest in sorted(manifests, key=lambda m: str(m.get("id", ""))):
        wf = str(manifest.get("id", ""))
        tier = str(manifest.get("tier") or "")
        terminal = _terminal_status(manifest)
        metrics = manifest.get("metrics") or {}
        total = {"sessions": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                 "aiu": None, "tool_calls": 0}
        if not metrics:
            rows.append(_blank_row(kind="row", workflow=wf, tier=tier, terminal_status=terminal, coverage="no sessions"))
        for role in sorted(metrics):
            m = metrics[role] or {}
            reported = bool((m.get("reported") or {}).get("usage"))
            tool_calls = _num(m.get("toolCalls")) or 0
            total["tool_calls"] += tool_calls
            total["sessions"] += _num(m.get("sessions")) or 0
            if not reported:
                rows.append(_blank_row(kind="row", workflow=wf, tier=tier, terminal_status=terminal, role=role,
                                       sessions=_num(m.get("sessions")), tool_calls=tool_calls,
                                       last_ms=_num(m.get("lastMs")), coverage="not reported by runtime"))
                continue
            models = m.get("models") or {}
            for model in sorted(models):
                mm = models[model] or {}
                aiu = mm.get("aiu")
                rows.append(_blank_row(kind="row", workflow=wf, tier=tier, terminal_status=terminal, role=role, model=model,
                                       sessions=_num(m.get("sessions")), calls=_num(mm.get("calls")),
                                       input_tokens=_num(mm.get("inputTokens")), output_tokens=_num(mm.get("outputTokens")),
                                       cache_read_tokens="", aiu=_num(aiu), tool_calls=tool_calls,
                                       last_ms=_num(m.get("lastMs")), coverage="reported"))
            total["calls"] += _num(m.get("calls")) or 0
            total["input_tokens"] += _num(m.get("inputTokens")) or 0
            total["output_tokens"] += _num(m.get("outputTokens")) or 0
            total["cache_read_tokens"] += _num(m.get("cacheReadTokens")) or 0
            if isinstance(m.get("aiu"), (int, float)):
                total["aiu"] = (total["aiu"] or 0) + m["aiu"]
        rows.append(_blank_row(kind="workflow_total", workflow=wf, tier=tier, terminal_status=terminal,
                               **{k: ("" if v is None else v) for k, v in total.items()}))
        if tier:
            t = tiers.setdefault(tier, {"workflows": 0, "sessions": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0,
                                        "cache_read_tokens": 0, "aiu": None, "tool_calls": 0})
            t["workflows"] += 1
            for key in ("sessions", "calls", "input_tokens", "output_tokens", "cache_read_tokens", "tool_calls"):
                t[key] += total[key]
            if total["aiu"] is not None:
                t["aiu"] = (t["aiu"] or 0) + total["aiu"]
    for tier in sorted(tiers):
        t = tiers[tier]
        rows.append(_blank_row(kind="tier_total", tier=tier, **{k: ("" if v is None else v) for k, v in t.items()}))
    for row in rows:
        if isinstance(row["aiu"], float):
            row["aiu"] = round(row["aiu"], 3)
    return rows


def render_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def render_md(rows: list[dict]) -> str:
    lines = ["# Cost report", "", "Numbers come from `workflows/*/manifest.json` (Copilot SDK usage events). "
             "AI units (aiu) are Copilot's billing unit; blank means the runtime reported none.", "",
             "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for row in rows:
        lines.append("| " + " | ".join(str(row[c]) for c in COLUMNS) + " |")
    return "\n".join(lines) + "\n"


def write(out: Path, rows: list[dict], fmt: str) -> list[Path]:
    out.parent.mkdir(parents=True, exist_ok=True)
    written = []
    if fmt in ("csv", "both"):
        p = out.with_suffix(".csv")
        p.write_bytes(render_csv(rows).encode("utf-8"))
        written.append(p)
    if fmt in ("md", "both"):
        p = out.with_suffix(".md")
        p.write_bytes(render_md(rows).encode("utf-8"))
        written.append(p)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repo or scratch root holding workflows/ (default: .)")
    parser.add_argument("--out", default="reports/cost", help="output path without extension (default: reports/cost)")
    parser.add_argument("--format", choices=["csv", "md", "both"], default="both")
    args = parser.parse_args(argv)
    try:
        rows = build_rows(load_manifests(Path(args.root)))
        written = write(Path(args.out), rows, args.format)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))  # exit 2, nothing written
    except Exception:  # exit 2: a crash outside the report itself
        traceback.print_exc()
        return 2
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`parser.error` prints `usage: … error: wf_0009: cannot read manifest.json: …` to stderr and exits 2 — the corrupt-manifest test reads it through `capsys` (argparse's `SystemExit(2)` propagates through `main`; the test calls `main` directly, so wrap: change the `except (FileNotFoundError, ValueError)` branch to `print(f"cost_report: {exc}", file=sys.stderr); return 2` instead of `parser.error`, so `main` returns 2 without raising).

Run: `.venv/Scripts/python.exe -m pytest tests/test_cost_report.py` — expected 5 pass. Adjust `_terminal_status`/blank rules only if a test says so; never adjust a test to match the code.

- [ ] **Step 3: Full pytest and commit**

Run: `.venv/Scripts/python.exe -m pytest` — expected 1010 passed, 0 skipped.

```bash
git add scripts/cost_report.py tests/test_cost_report.py
git commit -m "feat: cost report per workflow, role and model from the manifests (CSV + Markdown)"
```

---

### Task 5: BYOK context window, documentation, live checklist

**Files:**
- Modify: `orchestrator/types.ts` (`ProfileConfig.contextWindowTokens?`), `orchestrator/runner.ts` (`modelCapabilities`), `scripts/dev/serve_model.ps1`
- Test: `orchestrator/test/telemetry.test.ts` (one test)
- Create: `docs/reference/manifest-telemetry.md`
- Modify: `README.md` (§6 re-running paragraph, §8 budgets, §10 repo map), `docs/handoff-copilot-models.md` (§6), `docs/live-smoke-test.md` (append the checklist)

**Interfaces:**
- Consumes: `FakeSessionRecord.sessionConfig` (Task 1).
- Produces: `ProfileConfig.contextWindowTokens?: number`.

- [ ] **Step 1: Failing test** (append to `telemetry.test.ts`)

```ts
test("the local profile's contextWindowTokens reaches createSession as a capabilities override; hosted sends none", async (t) => {
  const { root, wf, env } = await fixture(t);
  const local = { ...DEFAULT_CONFIG, profiles: { ...DEFAULT_CONFIG.profiles, local: { ...DEFAULT_CONFIG.profiles.local, contextWindowTokens: 65536 } } };
  const rec = newRecord();
  const runner = new CopilotRunner(fakeClient(root, {}, rec), root, local, "local", []);
  runner.attach(env);
  await runner.run("intake", wf, "x");
  assert.deepEqual(rec.sessionConfig?.modelCapabilities, { limits: { max_context_window_tokens: 65536 } });
  const rec2 = newRecord();
  const hosted = new CopilotRunner(fakeClient(root, {}, rec2), root, DEFAULT_CONFIG, "hosted", []);
  hosted.attach(env);
  await hosted.run("intake", wf, "x");
  assert.equal(rec2.sessionConfig?.modelCapabilities, undefined);
});
```

Run it — expected FAIL (`modelCapabilities` undefined).

- [ ] **Step 2: Implement**

`types.ts`, `ProfileConfig`: add `/** BYOK only: the local server's context window (the -c you start llama-server with); passed as the SDK's modelCapabilities override so its compaction thresholds refer to the real window. */ contextWindowTokens?: number;`.

`runner.ts`, in the `createSession({...})` literal add:

```ts
        ...(typeof profile.contextWindowTokens === "number"
          ? { modelCapabilities: { limits: { max_context_window_tokens: profile.contextWindowTokens } } }
          : {}),
```

`scripts/dev/serve_model.ps1`: after the existing "starting llama-server on 127.0.0.1:$Port" line add
`Write-Host "serve_model.ps1: set profiles.local.contextWindowTokens to $Context in orchestrator.config.json so the runtime knows this window"`.

Run: `… --test orchestrator/test/*.test.ts` — expected 167 pass; `tsc` clean.

- [ ] **Step 3: Documentation**

Create `docs/reference/manifest-telemetry.md` with: the `metrics[role]` field table (name, type, meaning, "lifetime, never reset"), the `budget` window fields and the `--from-stage` rule, the `reasons.<stage>` `budget: <cap> <used> > <limit>` form, every `audit.jsonl` event of spec §3.3 with its fields, the three `budgets` config fields with defaults ("absent = unlimited; 0 is rejected"), the kill-switch rule ("input tokens only; AI units are checked between calls"), the AIU conversion (nano / 1e9), and the sentence that a BYOK provider reports no AI units. Copy the field lists from the spec; do not paraphrase numbers.

`README.md`: in the §8 paragraph that begins "**The tool-call budget is one specific, recurring reason a workflow parks at `NEEDS_HUMAN`.**" rename it to the spend budgets and list the three fields, the window/lifetime distinction, the kill switch, and the command `.venv/Scripts/python.exe scripts/cost_report.py --root <scratch-or-repo> --out reports/cost`; in §6's re-running text add one sentence that `--from-stage` resets the spend window and never the lifetime metrics; in §10's repo map add `orchestrator/telemetry.ts`, `scripts/cost_report.py`, `docs/reference/manifest-telemetry.md`; in §3's BYOK paragraph add `contextWindowTokens`.

`docs/handoff-copilot-models.md` §6: replace the first bullet with the three caps and the kill-switch sentence; add "read `docs/reference/manifest-telemetry.md` and run `scripts/cost_report.py` after every hosted run".

`docs/live-smoke-test.md`: append a dated section "Telemetry — to verify on a live run" listing exactly: (1) which of `assistant.usage`, `session.usage_info`, `session.compaction_start/complete`, `session.truncation`, `session.shutdown`, `subagent.*` arrive on the hosted profile and on BYOK; (2) whether `copilotUsage.totalNanoAiu` is populated and in what magnitude; (3) whether `session.shutdown` arrives on a normal end and on a crash; (4) what `sendAndWait` does after `session.abort()` (resolves, rejects, and with what text) and how long abort takes; (5) whether the `modelCapabilities` override makes the runtime compact a local model at the 80 % / 95 % thresholds; (6) that no number in `manifest.metrics` has yet been compared against the Copilot billing page.

Check: `.venv/Scripts/python.exe -m pytest tests/test_committed_workflows.py -k shipped_file` still
passes — those three scans are what hold `docs/` (and `scripts/`, `tests/`, `orchestrator/`,
`samples/`) to "no build machine's login name, no absolute path of a real PC, no pointer into an
agent's scratch directory".

- [ ] **Step 4: Full verification and commit**

Run all four: pytest (1010 passed, 0 skipped), node (167 pass), tsc (clean), and `.venv/Scripts/python.exe -m pytest tests/test_committed_workflows.py tests/test_agents_config.py`.

```bash
git add orchestrator/types.ts orchestrator/runner.ts orchestrator/test/telemetry.test.ts scripts/dev/serve_model.ps1 docs/reference/manifest-telemetry.md README.md docs/handoff-copilot-models.md docs/live-smoke-test.md
git commit -m "feat: BYOK context-window override; telemetry, budgets and cost report documented with the live-verification checklist"
```

---

## Self-review (done while writing)

- Spec coverage: §3.1/§3.2 → Task 1 (+ Task 2 for the window reset); §3.3 → Task 1 (all rows) + Task 3 (`budget-abort`); §4.1/§4.2 → Task 2; §4.3 → Task 3; §5 → Task 4; §6 → Task 5; §7 mechanics → Tasks 1–3; §8 tests → each task's Step 1; §9 docs → Task 5; §10 rulings → embedded (lifetime vs window, absent ≠ zero, token-only kill switch, no CLI internals, spec untouched).
- Names used consistently: `UsageTotals`, `RoleMetrics`, `BudgetWindow`, `Spend`, `HookState.usage/budgetAbort/budgetAbortReason`, `hooksFor → {hooks, state, audit}`, `AuditFn`, `emptyUsage`, `attachTelemetry(session, state, audit, log, guard?)` returning `detach`, `ensureBudget`, `spendUsed`, `resetBudget`, `exceededCap`, `capReason`, `toolCallsUsed`, `AgentResult.reason`, `TelemetryGuard`, `FakeSessionRecord.sessionConfig/emitted/abortCalls`, `event()`, `newRecord()`.
- Counts assumed at each step (156 → 162 → 166 → 167 node; 1005 → 1010 pytest) are expectations, not requirements; report the observed numbers.
