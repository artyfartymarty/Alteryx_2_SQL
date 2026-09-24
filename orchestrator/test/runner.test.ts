// CopilotRunner.run's classification of a crashed session's thrown error (d1), and the metrics
// it records for every session whether or not the SDK's onSessionEnd hook fires (d3).
//
// Task 16 diagnostics (task-16-report.md, ATTEMPT 1/2, a live BYOK run against a local
// llama.cpp server): both live attempts crashed on the model's own context window filling up
// ("400 request (34965 tokens) exceeds the available context size (32768 tokens), try
// increasing it"), but the crash was misreported as "denied" (runner.ts's catch block checked
// state.denied before the thrown error's own signature) and the crashed session's tool-call
// spend was silently lost (onSessionEnd, the only place metrics were written, never fired on
// this crash path).
//
// These tests drive a fake CopilotClient/session that calls the exact SessionHooks
// CopilotRunner wires up (via hooksFor), the same way the real SDK would, so the real
// policy.ts and hooks.ts code runs -- only the SDK's session transport is faked.
import { test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { CopilotClient, SessionConfig, SessionHooks } from "@github/copilot-sdk";
import { DEFAULT_CONFIG } from "../cli.ts";
import { notesPath, notesReminder, NOTES_ROLES } from "../hooks.ts";
import { CopilotRunner, MockRunner } from "../runner.ts";
import { toolCallsUsed } from "../stages.ts";
import { BROKEN_DBT, CANNED_DBT } from "./fakes.ts";
import type { Env, Manifest, OrchestratorConfig, ProfileConfig, Role, ShResult } from "../types.ts";

const invocation = { sessionId: "session-1" };

// Real text from task-16-report.md ATTEMPT 2's onPostToolUseFailure -- the same underlying
// context-window failure both live attempts' sessions crashed on.
const CONTEXT_OVERFLOW_TEXT =
  "400 request (34965 tokens) exceeds the available context size (32768 tokens), try increasing it";

// A hallucinated absolute path, byte-for-byte the shape from ATTEMPT 1's first real audit line
// (task-16-report.md): denied by policy.ts's normalizeToolPath as outside the repository,
// regardless of what the fixture's real temp root is.
const HALLUCINATED_PATH = {
  toolName: "view",
  toolArgs: { path: "C:\\Users\\someone\\Desktop\\Alteryx-to-Snowflake\\workflows\\wf_0001\\manifest.json" },
};

interface ToolCall {
  toolName: string;
  toolArgs: unknown;
  /** Task L1: when the call is allowed, its result text goes through onPostToolUse, as the SDK's would. */
  result?: string;
}

interface Scenario {
  /** Tool calls the fake session runs through onPreToolUse before completing or crashing. */
  toolCalls?: ToolCall[];
  /** If set, sendAndWait throws this after the tool calls above. */
  throws?: unknown;
  /** Fire onErrorOccurred with this error before throwing/returning -- the SDK's own
   * error-reporting hook, independent of what sendAndWait itself throws. */
  reportError?: { error: unknown; errorContext?: "model_call" | "tool_execution" | "system" | "user_input" };
  /** Fire onSessionEnd before returning/throwing. Most real sessions do; the live crash in
   * ATTEMPT 2 did not (task-16-report.md: no second "ev":"session-end" line in audit.jsonl). */
  fireSessionEnd?: boolean;
  /** Task W4: fired in order during sendAndWait, each through every `session.compaction_complete`
   * handler `CopilotRunner.run` registered via `session.on` (spike fact S5). */
  compactions?: { success: boolean }[];
  /** Task W4: fired in order during sendAndWait, each through every `assistant.usage` handler. */
  usage?: number[];
  /** Task W4 fix round 1: makes the fake session's `send(...)` reject, so a test can prove the
   * fire-and-forget notes reminder logs its failure instead of swallowing it silently. */
  sendFails?: boolean;
  /** Task L7 fix round 1 (R-a): makes the fake session's `abort()` reject with this, so a test can
   * prove a failed abort is logged, not thrown, and the session is still disconnected. */
  abortFails?: unknown;
  /** Task L7 fix round 1 (R-a): makes `abort()` resolve only after this many ms, so a test can prove
   * the bounded wait gives up and moves on instead of hanging `run` forever. */
  abortDelayMs?: number;
  /** Task L7 fix round 2 (L7-m1): makes `abort()` NOT fire `session.idle` afterward, so a test can
   * prove the bounded wait also covers "acknowledged but never actually idle" (a shell command or a
   * sub-agent task still running), not just a hung `abort()` call itself. */
  skipIdleAfterAbort?: boolean;
}

/** What a test can inspect after a run through a `fakeClient` session: every `send(...)` call
 * across every session this client created, each session's own handler bookkeeping (Task W4 --
 * whether `session.on`'s unsubscribe was actually called), and the order `abort`/`disconnect` were
 * actually called in (Task L7 fix round 1, R-a: at most one of each per session, `order` is shared
 * across every session `fakeClient` creates for one test). */
interface FakeSessionCalls {
  sends: { prompt: string; mode?: string }[];
  sessions: { handlerCount(type: string): number }[];
  order: string[];
}

/** A fake CopilotClient whose createSession returns a session that drives the real hooks
 * CopilotRunner built via hooksFor, the same way the SDK would -- everything downstream
 * (policy.ts's decide, hooks.ts's audit/classification) is the real code under test.
 *
 * Task W4: the session also implements `on`/`send`, the two SDK members `CopilotRunner.run`
 * exercises for the compaction memory aid (spike fact S5: `session.d.ts:190`, `types.d.ts:2791`).
 * `on` stores handlers per event type in a `Set` and returns an unsubscribe that deletes just that
 * one handler, so a test can prove unsubscription by checking the set is empty afterwards.
 */
function fakeClient(root: string, scenario: Scenario, calls: FakeSessionCalls = { sends: [], sessions: [], order: [] }): CopilotClient {
  return {
    async createSession(config: { hooks?: SessionHooks }) {
      const hooks = config.hooks!;
      const base = { sessionId: "session-1", timestamp: new Date(), workingDirectory: root };
      const handlers = new Map<string, Set<(event: any) => void>>();
      const session = {
        on(type: string, handler: (event: any) => void) {
          const set = handlers.get(type) ?? new Set();
          set.add(handler);
          handlers.set(type, set);
          return () => {
            handlers.get(type)?.delete(handler);
          };
        },
        handlerCount(type: string): number {
          return handlers.get(type)?.size ?? 0;
        },
        async send(options: { prompt: string; mode?: string }) {
          if (scenario.sendFails) throw new Error("simulated send failure");
          calls.sends.push(options);
          return "";
        },
        async sendAndWait() {
          for (const call of scenario.toolCalls ?? []) {
            const decision = await hooks.onPreToolUse!({ ...base, toolName: call.toolName, toolArgs: call.toolArgs }, invocation);
            if (call.result !== undefined && (decision as { permissionDecision?: string } | undefined)?.permissionDecision === "allow") {
              await hooks.onPostToolUse!(
                { ...base, toolName: call.toolName, toolArgs: call.toolArgs, toolResult: { resultType: "success", textResultForLlm: call.result } },
                invocation,
              );
            }
          }
          for (const compaction of scenario.compactions ?? []) {
            for (const handler of [...(handlers.get("session.compaction_complete") ?? [])]) {
              handler({ ...base, id: "evt", parentId: null, ephemeral: true, type: "session.compaction_complete", data: { success: compaction.success } });
            }
          }
          for (const inputTokens of scenario.usage ?? []) {
            for (const handler of [...(handlers.get("assistant.usage") ?? [])]) {
              handler({ ...base, id: "evt", parentId: null, ephemeral: true, type: "assistant.usage", data: { inputTokens, model: "m" } });
            }
          }
          if (scenario.reportError) {
            await hooks.onErrorOccurred!(
              {
                ...base,
                error: scenario.reportError.error as string,
                errorContext: scenario.reportError.errorContext ?? "model_call",
                recoverable: true,
              },
              invocation,
            );
          }
          if (scenario.fireSessionEnd) {
            await hooks.onSessionEnd!({ ...base, reason: scenario.throws ? "error" : "complete" }, invocation);
          }
          if (scenario.throws !== undefined) throw scenario.throws;
          return undefined;
        },
        async abort() {
          calls.order.push("abort");
          if (scenario.abortDelayMs !== undefined) await new Promise((resolve) => setTimeout(resolve, scenario.abortDelayMs));
          if (scenario.abortFails !== undefined) throw scenario.abortFails;
          // Task L7 fix round 2 (L7-m1): a real session eventually goes idle once its turn actually
          // stops, not merely once abort() is acknowledged -- this fake fires that confirmation right
          // away, exactly as `abort()` itself resolving would in the common case, unless a scenario
          // asks it not to (`skipIdleAfterAbort`, for the "idle never arrives" bounded-wait test).
          if (!scenario.skipIdleAfterAbort) {
            for (const handler of [...(handlers.get("session.idle") ?? [])]) {
              handler({ ...base, id: "evt", parentId: null, ephemeral: true, type: "session.idle", data: { aborted: true } });
            }
          }
        },
        async disconnect() {
          calls.order.push("disconnect");
          return undefined;
        },
      };
      calls.sessions.push(session);
      return session;
    },
  } as unknown as CopilotClient;
}

async function fixture(t: any): Promise<{ root: string; wf: Manifest; env: Env; config: OrchestratorConfig; logs: string[] }> {
  const root = await mkdtemp(path.join(os.tmpdir(), "orch-runner-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, "workflows", "wf_0001"), { recursive: true });
  await writeFile(path.join(root, "workflows", "wf_0001", "manifest.json"), '{"id":"wf_0001","status":{},"metrics":{}}\n', "utf8");
  const wf: Manifest = { id: "wf_0001", tier: "T1", status: {}, metrics: {} };
  const config: OrchestratorConfig = { ...DEFAULT_CONFIG, sessionTimeoutMs: 5_000 };
  const logs: string[] = [];
  const env = { root, config, log: (line: string) => logs.push(line), interactive: false } as unknown as Env;
  return { root, wf, env, config, logs };
}

function runnerFor(client: CopilotClient, root: string, config: OrchestratorConfig, env: Env): CopilotRunner {
  const r = new CopilotRunner({ client, root, config, profile: "local", agents: [] });
  r.attach(env);
  return r;
}

const run = (
  client: CopilotClient,
  root: string,
  config: OrchestratorConfig,
  env: Env,
  wf: Manifest,
  role: Role = "intake",
  ctx?: Parameters<CopilotRunner["run"]>[3],
) => runnerFor(client, root, config, env).run(role, wf, "do the thing", ctx);

// ---------- per-role context tier and reasoning effort (docs/handoff-copilot-models.md §4) ------
//
// A fake client that only records the SessionConfig createSession received -- no tool calls, no
// hooks exercised; these tests care about what reaches the SDK call, not the policy machinery.

function recordingClient(sink: SessionConfig[]): CopilotClient {
  return {
    async createSession(config: SessionConfig) {
      sink.push(config);
      return {
        // CopilotRunner.run calls session.on(...) unconditionally right after createSession
        // (Task W4) -- a session with no tool calls or events still needs a working one.
        on() {
          return () => undefined;
        },
        async send() {
          return "";
        },
        async sendAndWait() {
          return undefined;
        },
        async disconnect() {
          return undefined;
        },
      };
    },
  } as unknown as CopilotClient;
}

test("per-role context tier and effort reach createSession", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const hostedProfile: ProfileConfig = {
    model: "m",
    reasoningEffort: "medium",
    roleContextTiers: { analyzer: "long_context" },
    roleReasoningEffort: { reviewer: "low" },
  };
  const hostedConfig: OrchestratorConfig = { ...config, profiles: { ...config.profiles, hosted: hostedProfile } };
  const sink: SessionConfig[] = [];
  const client = recordingClient(sink);
  const runner = new CopilotRunner({ client, root, config: hostedConfig, profile: "hosted", agents: [] });
  runner.attach(env);

  await runner.run("analyzer", wf, "task");
  assert.equal(sink[0].contextTier, "long_context");
  assert.equal(sink[0].reasoningEffort, "medium", "analyzer has no per-role effort override, so it falls back to the profile's");

  await runner.run("reviewer", wf, "task");
  assert.equal("contextTier" in sink[1], false, "reviewer has no per-role tier override -- the key is absent, not undefined");
  assert.equal(sink[1].reasoningEffort, "low");
});

// Live hardening, Task L7 (R1): the local BYOK profile's provider.maxPromptTokens/maxOutputTokens
// reach the SDK's createSession unchanged, inside the same `provider` object as baseUrl/apiKey --
// cli.test.ts covers the config-file validation that keeps a bad value from ever reaching here.
test("L7 R1: profiles.local.provider.maxPromptTokens/maxOutputTokens reach createSession inside provider", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const localProfile: ProfileConfig = {
    provider: { type: "openai", baseUrl: "http://127.0.0.1:8080/v1", apiKey: "local", maxPromptTokens: 120000, maxOutputTokens: 8000 },
    model: "ternary-bonsai-2-27b",
    reasoningEffort: "medium",
  };
  const localConfig: OrchestratorConfig = { ...config, profiles: { ...config.profiles, local: localProfile } };
  const sink: SessionConfig[] = [];
  const client = recordingClient(sink);
  const runner = new CopilotRunner({ client, root, config: localConfig, profile: "local", agents: [] });
  runner.attach(env);

  await runner.run("translator", wf, "task");
  assert.equal(sink[0].provider?.baseUrl, "http://127.0.0.1:8080/v1", "the rest of the provider object is unchanged");
  assert.equal(sink[0].provider?.maxPromptTokens, 120000);
  assert.equal(sink[0].provider?.maxOutputTokens, 8000);
});

test("L7 R1: the hosted profile (no provider) sends no maxPromptTokens/maxOutputTokens at all", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const hostedProfile: ProfileConfig = { model: "gpt-5.6-luna", reasoningEffort: "medium" };
  const hostedConfig: OrchestratorConfig = { ...config, profiles: { ...config.profiles, hosted: hostedProfile } };
  const sink: SessionConfig[] = [];
  const client = recordingClient(sink);
  const runner = new CopilotRunner({ client, root, config: hostedConfig, profile: "hosted", agents: [] });
  runner.attach(env);

  await runner.run("translator", wf, "task");
  assert.equal(sink[0].provider, undefined);
});

test("the local profile is unchanged: no contextTier key reaches createSession, and effort is unchanged", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const sink: SessionConfig[] = [];
  const client = recordingClient(sink);
  const runner = new CopilotRunner({ client, root, config, profile: "local", agents: [] });
  runner.attach(env);

  await runner.run("analyzer", wf, "task");
  assert.equal("contextTier" in sink[0], false, "the local profile sets no roleContextTiers, so the key must be absent entirely");
  assert.equal(sink[0].reasoningEffort, config.profiles.local.reasoningEffort);
  assert.equal(sink[0].model, config.profiles.local.model);
});

// ---------- live hardening, Task L9 (R1): the SDK never even offers a tool the policy always refuses ----------
// Live evidence (task-L9-brief.md): the documenter's session parked on the SDK's own built-in
// `web_fetch`. `excludedTools` (node_modules/@github/copilot-sdk/dist/types.d.ts ~2015-2021) "always
// takes precedence", so CopilotRunner passes the fixed list (policy.ts's ALWAYS_EXCLUDED_BUILTIN_TOOLS,
// prefixed `builtin:`) on every session, extendable from orchestrator.config.json's session.excludedTools.

test("L9 R1: createSession always receives the fixed excludedTools list, for every role", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const sink: SessionConfig[] = [];
  const client = recordingClient(sink);
  const runner = new CopilotRunner({ client, root, config, profile: "local", agents: [] });
  runner.attach(env);

  await runner.run("documenter", wf, "task");
  assert.deepEqual(sink[0].excludedTools, ["builtin:web_fetch", "builtin:web_search", "builtin:sql", "builtin:write_agent"]);
});

test("L9 R1: orchestrator.config.json's session.excludedTools extends the fixed list, verbatim, after it", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const extended: OrchestratorConfig = { ...config, session: { excludedTools: ["mcp:extra_tool", "custom:noop"] } };
  const sink: SessionConfig[] = [];
  const client = recordingClient(sink);
  const runner = new CopilotRunner({ client, root, config: extended, profile: "local", agents: [] });
  runner.attach(env);

  await runner.run("intake", wf, "task");
  assert.deepEqual(sink[0].excludedTools, [
    "builtin:web_fetch", "builtin:web_search", "builtin:sql", "builtin:write_agent", "mcp:extra_tool", "custom:noop",
  ]);
});

// ---------- d1: classification order ----------

test("d1: a crash after earlier, already-recovered-from denials is classified from the thrown error, not state.denied", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, {
    toolCalls: [HALLUCINATED_PATH, HALLUCINATED_PATH], // two denials the model already moved past
    throws: new Error(CONTEXT_OVERFLOW_TEXT),
  });
  const result = await run(client, root, config, env, wf);
  assert.equal(result.ok, false);
  assert.equal(result.error, "context-overflow", "not denied, even though two earlier denials happened in this session");
  assert.match(result.detail ?? "", /exceeds the available context size/);
});

// An attempted action that parks at once: the live shape of an intake agent writing a small Python
// helper outside its lane to make the notes directory -- here into the pipeline's own scripts/. Since Task
// L6 (R2) such a write is SEVERE, and one severe denial parks the session; other attempted actions are
// budgeted (`budgets.maxActDenialsPerSession`), and so are blocked reads (HALLUCINATED_PATH). (L6 fix round
// 2, I6: the same helper as a NEW file at the run root is an ordinary act -- a scratch file, not the
// pipeline's.)
const SEVERE_CALL = { toolName: "create", toolArgs: { path: "scripts/make_notes_dir.py", file_text: "import os\n" } };

test("d1: a session that ends because of an attempted action, with no other error signature, still reports denied", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, {
    toolCalls: [SEVERE_CALL],
    throws: new Error("session aborted: no further tool calls permitted"),
  });
  const result = await run(client, root, config, env, wf);
  assert.equal(result.error, "denied");
  assert.match(result.detail ?? "", /^severe-denials: 1 \(parks at once\); create: scripts\/ is read-only for every role/);
});

test("d1: a session with an attempted action that completes without throwing still reports denied (unchanged behaviour)", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, { toolCalls: [SEVERE_CALL] }); // no throw: sendAndWait resolves
  const result = await run(client, root, config, env, wf);
  assert.equal(result.error, "denied");
});

test("d1: a real timeout signature still classifies as timeout despite an earlier denial", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, {
    toolCalls: [HALLUCINATED_PATH],
    throws: new Error("session timed out after 1200000ms"),
  });
  const result = await run(client, root, config, env, wf);
  assert.equal(result.error, "timeout");
});

// ---------- live hardening, Task L7 fix round 1 (R-a): a timeout aborts the turn first ----------
// The SDK's own docs (session.d.ts ~154) say sendAndWait's timeout "does not abort in-flight agent
// work" -- so before Task L7's `runAgent` keep path (stages.ts) can trust what `verify` finds on
// disk, the turn that might still be writing to it has to actually be asked to stop.

test("R-a: a timeout calls session.abort() before session.disconnect(), in that order", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const client = fakeClient(root, { throws: new Error("Timeout after 2700000ms waiting for session.idle") }, calls);
  const runner = new CopilotRunner({ client, root, config, profile: "local", agents: [] });
  runner.attach(env);

  const result = await runner.run("translator", wf, "task");
  assert.equal(result.error, "timeout");
  assert.deepEqual(calls.order, ["abort", "disconnect"]);
});

test("R-a: a session with no timeout never calls abort()", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const client = fakeClient(root, {}, calls);
  const runner = new CopilotRunner({ client, root, config, profile: "local", agents: [] });
  runner.attach(env);

  await runner.run("translator", wf, "task");
  assert.deepEqual(calls.order, ["disconnect"]);
});

test("R-a: a failed abort() is logged, not thrown, and the session is still disconnected and reported as a timeout", async (t) => {
  const { root, wf, env, config, logs } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const client = fakeClient(
    root,
    { throws: new Error("Timeout after 2700000ms waiting for session.idle"), abortFails: new Error("abort refused: no active turn") },
    calls,
  );
  const runner = new CopilotRunner({ client, root, config, profile: "local", agents: [] });
  runner.attach(env);

  const result = await runner.run("translator", wf, "task");
  assert.equal(result.error, "timeout", "the failed abort does not change the session's own classification");
  assert.deepEqual(calls.order, ["abort", "disconnect"], "disconnect still runs even though abort failed");
  assert.ok(logs.some((line) => line.includes("session.abort() after timeout failed") && line.includes("abort refused")), logs.join("\n"));
});

test("R-a: an abort() that never settles is bounded, not awaited forever", async (t) => {
  const { root, wf, env, config, logs } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  // Longer than the runner's own (test-configured) bound, so the real abort() call is still
  // in flight when the race gives up -- exactly the "hung abort()" shape this test exists for.
  const client = fakeClient(
    root,
    { throws: new Error("Timeout after 2700000ms waiting for session.idle"), abortDelayMs: 10_000 },
    calls,
  );
  const runner = new CopilotRunner({ client, root, config, profile: "local", agents: [], abortBoundMs: 20 });
  runner.attach(env);

  const started = Date.now();
  const result = await runner.run("translator", wf, "task");
  assert.ok(Date.now() - started < 2000, "run() did not wait for the full 10s abort() delay");
  assert.equal(result.error, "timeout");
  assert.equal(calls.order[calls.order.length - 1], "disconnect", "disconnect still ran, after the bounded wait gave up");
  assert.ok(logs.some((line) => line.includes("session.abort() after timeout failed or did not finish in time")), logs.join("\n"));
});

test("L7-m1: an acknowledged abort() that never reaches session.idle is bounded too, not awaited forever", async (t) => {
  const { root, wf, env, config, logs } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  // abort() itself resolves quickly (acknowledged), but no session.idle ever follows -- a shell
  // command or an attached sub-agent still running, the exact gap L7-m1 exists to cover.
  const client = fakeClient(
    root,
    { throws: new Error("Timeout after 2700000ms waiting for session.idle"), skipIdleAfterAbort: true },
    calls,
  );
  const runner = new CopilotRunner({ client, root, config, profile: "local", agents: [], abortBoundMs: 20 });
  runner.attach(env);

  const started = Date.now();
  const result = await runner.run("translator", wf, "task");
  assert.ok(Date.now() - started < 2000, "run() did not wait forever for session.idle");
  assert.equal(result.error, "timeout");
  assert.deepEqual(calls.order, ["abort", "disconnect"], "abort() itself was still called and acknowledged");
  assert.ok(logs.some((line) => line.includes("session.abort() after timeout failed or did not finish in time")), logs.join("\n"));
});

test("L7-m1: abort() acknowledged and session.idle both arrive -- the ordinary case is unaffected", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const client = fakeClient(root, { throws: new Error("Timeout after 2700000ms waiting for session.idle") }, calls);
  const runner = new CopilotRunner({ client, root, config, profile: "local", agents: [] });
  runner.attach(env);

  const result = await runner.run("translator", wf, "task");
  assert.equal(result.error, "timeout");
  assert.deepEqual(calls.order, ["abort", "disconnect"]);
});

test("R-a: the effective provider limits are logged once per session start", async (t) => {
  const { root, wf, env, config, logs } = await fixture(t);
  const localConfig: OrchestratorConfig = {
    ...config,
    profiles: {
      ...config.profiles,
      local: { ...config.profiles.local, provider: { type: "openai", baseUrl: "http://127.0.0.1:8080/v1", maxPromptTokens: 100000, maxOutputTokens: 8000 } },
    },
  };
  const client = fakeClient(root, {});
  const runner = new CopilotRunner({ client, root, config: localConfig, profile: "local", agents: [] });
  runner.attach(env);

  await runner.run("translator", wf, "task");
  const starts = logs.filter((line) => line.includes("session starting:"));
  assert.equal(starts.length, 1, "once per session start");
  assert.match(starts[0], /maxPromptTokens=100000/);
  assert.match(starts[0], /maxOutputTokens=8000/);
});

// ---------- live hardening, Task L7 fix round 2 (IMP-1, L7-m2): every timeout aborts the turn ----------
// Review (review-L7fix-L9-L8fix.md, IMP-1): L6 fix round 2's severe-denial check returned `denied`
// BEFORE fix round 1's abort block ever ran, so the one turn most worth stopping -- one that already
// made a severe attempt -- was exactly the one left running. L7-m2 is the same shape for a non-severe,
// over-budget act/read denial count.

test("IMP-1: a severe call followed by a timeout still aborts the turn, in order, before being classified denied", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const client = fakeClient(
    root,
    { toolCalls: [SEVERE_CALL], throws: new Error("Timeout after 2700000ms waiting for session.idle") },
    calls,
  );
  const runner = new CopilotRunner({ client, root, config, profile: "local", agents: [] });
  runner.attach(env);

  const result = await runner.run("translator", wf, "task");
  assert.equal(result.error, "denied", "the severe denial still decides the classification");
  assert.deepEqual(calls.order, ["abort", "disconnect"], "the turn is aborted before the session is judged, same as a plain timeout");
});

test("IMP-1: a plain severe call with no timeout is unaffected -- no abort, still denied", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const client = fakeClient(root, { toolCalls: [SEVERE_CALL] }, calls);
  const runner = new CopilotRunner({ client, root, config, profile: "local", agents: [] });
  runner.attach(env);

  const result = await runner.run("translator", wf, "task");
  assert.equal(result.error, "denied");
  assert.deepEqual(calls.order, ["disconnect"], "no timeout, so no abort call");
});

test("L7-m2: over-budget act denials followed by a timeout are classified denied, not timeout", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const actBudget = config.budgets.maxActDenialsPerSession ?? 20;
  const client = fakeClient(
    root,
    { toolCalls: acts(actBudget + 1), throws: new Error("Timeout after 2700000ms waiting for session.idle") },
    calls,
  );
  const runner = new CopilotRunner({ client, root, config, profile: "local", agents: [] });
  runner.attach(env);

  const result = await runner.run("translator", wf, "task");
  assert.equal(result.error, "denied", "the over-budget act count wins over the timeout classification");
  assert.match(result.detail ?? "", /act-denials/);
  assert.deepEqual(calls.order, ["abort", "disconnect"], "still aborted first: this is a timeout too");
});

test("L7-m2: act denials WITHIN the budget followed by a timeout are unaffected -- still timeout", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const client = fakeClient(
    root,
    { toolCalls: acts(1), throws: new Error("Timeout after 2700000ms waiting for session.idle") },
    calls,
  );
  const runner = new CopilotRunner({ client, root, config, profile: "local", agents: [] });
  runner.attach(env);

  const result = await runner.run("translator", wf, "task");
  assert.equal(result.error, "timeout", "one act denial is within budget, so gradedDenials() is undefined");
  assert.deepEqual(calls.order, ["abort", "disconnect"]);
});

test("d1: a real rate-limit signature still classifies as rate-limit despite an earlier denial", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, {
    toolCalls: [HALLUCINATED_PATH],
    throws: new Error("HTTP 429 rate limit exceeded"),
  });
  const result = await run(client, root, config, env, wf);
  assert.equal(result.error, "rate-limit");
});

test("d1: onErrorOccurred marking state.rateLimited earlier still wins even if the final thrown text does not itself match", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, {
    reportError: { error: "HTTP 429 rate limit exceeded" },
    throws: new Error("connection reset"),
  });
  const result = await run(client, root, config, env, wf);
  assert.equal(result.error, "rate-limit");
});

test("d1: onErrorOccurred marking state.contextOverflow earlier still wins even if the final thrown text does not itself match", async (t) => {
  // Mirrors the rate-limit case above: not verified live which shape the real SDK uses (see
  // task-16-diag-report.md "what I could not determine without a live run"), so both the
  // thrown-error text AND this state-flag fallback are covered.
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, {
    reportError: { error: { message: CONTEXT_OVERFLOW_TEXT } },
    throws: new Error("session ended unexpectedly"),
  });
  const result = await run(client, root, config, env, wf);
  assert.equal(result.error, "context-overflow");
});

// ---------- F7 (final review): context-overflow wins over rate-limit when both are present ------

test("F7: a thrown error carrying both an anchored '429' and the context-overflow phrase classifies as context-overflow", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, {
    throws: new Error("429: the request exceeds the available context size (32768 tokens)"),
  });
  const result = await run(client, root, config, env, wf);
  assert.equal(result.error, "context-overflow", "context-overflow must win over the standalone 429 in the same text");
});

test("F7: a context-overflow token count ('(42901 tokens)') is never misread as rate-limit-429", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, {
    throws: new Error("400 request (42901 tokens) exceeds the available context size (32768 tokens), try increasing it"),
  });
  const result = await run(client, root, config, env, wf);
  assert.equal(result.error, "context-overflow");
  assert.notEqual(result.error, "rate-limit");
});

// ---------- d3: metrics for every session, crashed or not ----------

test("d3: a session that crashes without onSessionEnd firing still records this session's duration and tool calls", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, {
    toolCalls: [HALLUCINATED_PATH, HALLUCINATED_PATH, HALLUCINATED_PATH],
    throws: new Error(CONTEXT_OVERFLOW_TEXT),
    fireSessionEnd: false,
  });
  await run(client, root, config, env, wf);
  assert.equal(wf.metrics.intake?.toolCalls, 3, "the crashed session's own 3 tool calls are not lost");
  assert.equal(typeof wf.metrics.intake?.lastMs, "number");
});

test("d3: a session where onSessionEnd fires is not double-counted by the runner's own finally block", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, {
    toolCalls: [
      { toolName: "view", toolArgs: { path: "workflows/wf_0001/manifest.json" } },
      { toolName: "view", toolArgs: { path: "mappings/global.yaml" } },
    ],
    fireSessionEnd: true,
  });
  await run(client, root, config, env, wf);
  assert.equal(wf.metrics.intake?.toolCalls, 2, "not 4 -- onSessionEnd already recorded it, the finally block must not add it again");
});

test("d3: a crashed first attempt's tool calls add to a second attempt's, and the budget check (toolCallsUsed) sees the total", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const crashed = fakeClient(root, {
    toolCalls: [HALLUCINATED_PATH, HALLUCINATED_PATH, HALLUCINATED_PATH, HALLUCINATED_PATH, HALLUCINATED_PATH],
    throws: new Error(CONTEXT_OVERFLOW_TEXT),
    fireSessionEnd: false, // exactly the live-observed shape: onSessionEnd never fired on this crash path
  });
  const first = await run(crashed, root, config, env, wf);
  assert.equal(first.error, "context-overflow");
  assert.equal(wf.metrics.intake?.toolCalls, 5);

  // A second, independent attempt at the same role (e.g. a fresh `--from-stage intake` run
  // reloading this same manifest) must ADD to the workflow's spend, not replace it.
  const succeeded = fakeClient(root, {
    toolCalls: [
      { toolName: "view", toolArgs: { path: "workflows/wf_0001/manifest.json" } },
      { toolName: "view", toolArgs: { path: "mappings/global.yaml" } },
      { toolName: "view", toolArgs: { path: "workflows/wf_0001/parsed/dag.json" } },
      { toolName: "view", toolArgs: { path: "workflows/wf_0001/intake/touchpoints.json" } },
    ],
    fireSessionEnd: true,
  });
  const second = await run(succeeded, root, config, env, wf);
  assert.equal(second.ok, true);
  assert.equal(wf.metrics.intake?.toolCalls, 9, "5 (crashed) + 4 (succeeded) -- the crashed attempt's spend is not lost");
  assert.equal(toolCallsUsed(wf), 9, "the same total orchestrator/stages.ts's runAgent budget check reads");
});

// ---------- MockRunner: replaying a Snowpark segment (output targets, phase 1) ----------
//
// The canned tree here is `samples/wf_0006`-SHAPED, built in the test rather than read from the
// repo: Task 5 owns the real sample, and this file must not depend on it. The shape that matters
// is the one spec §4.2 and §6 fix: a Snowpark segment's canned artefact is `proc.py` (never a
// canned `proc.sql`, which only `render_snowpark.py` writes), and a broken variant may be a `.py`
// file, so the "first variant in name order" rule cannot filter on `.sql` any more.

interface MockFixture {
  root: string;
  runner: MockRunner;
  py: { script: string; args: string[] }[];
  wf: Manifest;
}

/** `samples/<wf>/canned/segments/<seg>/…` plus `workflows/<wf>/segments/<seg>/contract.json`,
 * which is what `replayValidator` reads the target from. */
async function mockFixture(
  t: any,
  options: { target?: string; canned: Record<string, string>; broken?: Record<string, string>; scenario?: string },
): Promise<MockFixture> {
  const root = await mkdtemp(path.join(os.tmpdir(), "orch-mock-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const samples = path.join(root, "samples");
  const write = async (file: string, text: string) => {
    await mkdir(path.dirname(file), { recursive: true });
    await writeFile(file, text, "utf8");
  };
  for (const [name, text] of Object.entries(options.canned)) {
    await write(path.join(samples, "wf_0006", "canned", "segments", "seg_02", name), text);
  }
  for (const [name, text] of Object.entries(options.broken ?? {})) {
    await write(path.join(samples, "wf_0006", "broken_sql", "seg_02", name), text);
  }
  await write(
    path.join(root, "workflows", "wf_0006", "segments", "seg_02", "contract.json"),
    `${JSON.stringify({ segment: "seg_02", ...(options.target ? { target: options.target } : {}) }, null, 2)}\n`,
  );

  const py: { script: string; args: string[] }[] = [];
  const runner = new MockRunner(root, samples, options.scenario);
  runner.attach({
    root,
    async py(script: string, args: string[]): Promise<ShResult> {
      py.push({ script, args });
      return { ok: true, code: 0, out: "", err: "" };
    },
  } as unknown as Env);
  return { root, runner, py, wf: { id: "wf_0006", status: {}, metrics: {} } };
}

const segFile = (root: string, name: string) =>
  readFile(path.join(root, "workflows", "wf_0006", "segments", "seg_02", name), "utf8").then((t) => t, () => undefined);

test("MockRunner replays canned proc.py for a Snowpark segment and never fabricates a proc.sql", async (t) => {
  const { root, runner, wf } = await mockFixture(t, {
    target: "snowpark",
    canned: { "proc.py": "def run(session, a, b, c, d, e):\n    return 'OK'\n", "translation_notes.md": "- python tool\n" },
  });
  const result = await runner.run("translator", wf, "translate seg_02", { segment: "seg_02", iteration: 0 });
  assert.equal(result.ok, true);
  assert.match((await segFile(root, "proc.py")) ?? "", /def run\(session/);
  assert.equal(await segFile(root, "proc.sql"), undefined, "proc.sql is render_snowpark.py's output, not the mock's");
  assert.match((await segFile(root, "translation_notes.md")) ?? "", /python tool/);
});

test("MockRunner still replays proc.sql for a segment whose canned tree has no proc.py", async (t) => {
  const { root, runner, wf } = await mockFixture(t, {
    target: "sql",
    canned: { "proc.sql": "-- tool 1\nCREATE OR REPLACE PROCEDURE X() RETURNS STRING LANGUAGE SQL AS BEGIN RETURN 'OK'; END;\n" },
  });
  assert.equal((await runner.run("translator", wf, "t", { segment: "seg_02", iteration: 0 })).ok, true);
  assert.match((await segFile(root, "proc.sql")) ?? "", /CREATE OR REPLACE PROCEDURE/);
  assert.equal(await segFile(root, "proc.py"), undefined);
});

test("MockRunner serves the first broken variant in name order whatever its extension", async (t) => {
  const { root, runner, wf } = await mockFixture(t, {
    target: "snowpark",
    scenario: "fix-loop:seg_02",
    canned: { "proc.py": "def run(session, a, b, c, d, e):\n    return 'OK'\n" },
    broken: { "01_drops_nulls.py": "def run(session, a, b, c, d, e):\n    return 'BROKEN'\n", "02_other.sql": "-- never chosen\n" },
  });
  assert.equal((await runner.run("translator", wf, "t", { segment: "seg_02", iteration: 0 })).ok, true);
  assert.match((await segFile(root, "proc.py")) ?? "", /BROKEN/, "the .py variant is the bad first attempt");
  assert.equal(await segFile(root, "proc.sql"), undefined, "the .sql variant sorted second and was not served");

  // The fixer's turn serves the good canned artefact, exactly as it does for a SQL segment.
  assert.equal((await runner.run("fixer", wf, "t", { segment: "seg_02", iteration: 1 })).ok, true);
  assert.match((await segFile(root, "proc.py")) ?? "", /return 'OK'/);
});

test("MockRunner's validator spawns validate_snowpark.py for a snowpark contract and validate_segment.py otherwise", async (t) => {
  const snowpark = await mockFixture(t, { target: "snowpark", canned: { "proc.py": "def run(session, a, b, c, d, e):\n    return 'OK'\n" } });
  assert.equal((await snowpark.runner.run("validator", snowpark.wf, "v", { segment: "seg_02" })).ok, true);
  assert.deepEqual(snowpark.py, [{ script: "scripts/validate_snowpark.py", args: ["wf_0006", "seg_02"] }]);

  const sql = await mockFixture(t, { target: "sql", canned: { "proc.sql": "-- x\n" } });
  assert.equal((await sql.runner.run("validator", sql.wf, "v", { segment: "seg_02" })).ok, true);
  assert.deepEqual(sql.py, [{ script: "scripts/validate_segment.py", args: ["wf_0006", "seg_02"] }]);

  const noTarget = await mockFixture(t, { canned: { "proc.sql": "-- x\n" } });
  assert.equal((await noTarget.runner.run("validator", noTarget.wf, "v", { segment: "seg_02" })).ok, true);
  assert.deepEqual(noTarget.py, [{ script: "scripts/validate_segment.py", args: ["wf_0006", "seg_02"] }]);
});

// ---------- MockRunner and CopilotRunner in dbt scope (output targets, phase 2) ----------
//
// A dbt workflow's translate-stage roles run ONCE for the whole workflow with `ctx.dbt` set and no
// segment: the translator/fixer replay `samples/<wf>/canned/dbt/**` into `workflows/<wf>/dbt/`, the
// reviewer replays `canned/review.json` to `dbt/review.json`, and the validator runs the real
// `scripts/validate_dbt.py <wf>`. The canned tree is built here (CANNED_DBT, the same fixture the
// stage tests replay), not read from the repo: Task C owns the real wf_0007 sample.

interface DbtFixture {
  root: string;
  runner: MockRunner;
  py: { script: string; args: string[] }[];
  wf: Manifest;
  file(...rest: string[]): Promise<string | undefined>;
}

async function dbtFixture(
  t: any,
  options: { scenario?: string; exitCode?: number; broken?: Record<string, string> } = {},
): Promise<DbtFixture> {
  const root = await mkdtemp(path.join(os.tmpdir(), "orch-dbt-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const samples = path.join(root, "samples");
  const write = async (file: string, text: string) => {
    await mkdir(path.dirname(file), { recursive: true });
    await writeFile(file, text, "utf8");
  };
  for (const [rel, text] of Object.entries(CANNED_DBT)) await write(path.join(samples, "wf_0007", "canned", "dbt", ...rel.split("/")), text);
  await write(path.join(samples, "wf_0007", "canned", "review.json"), '{"verdict": "PASS", "findings": []}\n');
  for (const [rel, text] of Object.entries(options.broken ?? { "models/orders_out.sql": BROKEN_DBT })) {
    await write(path.join(samples, "wf_0007", "broken_sql", "dbt", ...rel.split("/")), text);
  }
  const py: { script: string; args: string[] }[] = [];
  const runner = new MockRunner(root, samples, options.scenario);
  runner.attach({
    root,
    async py(script: string, args: string[]): Promise<ShResult> {
      py.push({ script, args });
      const code = options.exitCode ?? 0;
      return { ok: code === 0, code, out: "", err: code === 2 ? "validate_dbt.py: error: no golden sets" : "" };
    },
  } as unknown as Env);
  const file = (...rest: string[]) =>
    readFile(path.join(root, "workflows", "wf_0007", ...rest), "utf8").then((text) => text, () => undefined);
  return { root, runner, py, wf: { id: "wf_0007", status: {}, metrics: {} }, file };
}

test("MockRunner replays canned/dbt/** for the translator in dbt scope", async (t) => {
  const { runner, wf, file } = await dbtFixture(t);
  const result = await runner.run("translator", wf, "translate the dbt project", { iteration: 0, dbt: true });
  assert.equal(result.ok, true);
  for (const [rel, text] of Object.entries(CANNED_DBT)) {
    assert.equal(await file("dbt", ...rel.split("/")), text, `dbt/${rel} replayed at the same relative path`);
  }
  assert.equal(await file("segments", "seg_01", "proc.sql"), undefined, "a dbt translator writes no procedure");
  assert.equal(await file("dbt", "fix_log.md"), undefined, "only the fixer logs a fix");
});

test("the first broken dbt variant is served on the fix-loop:dbt scenario", async (t) => {
  const { runner, wf, file } = await dbtFixture(t, { scenario: "fix-loop:dbt" });
  assert.equal((await runner.run("translator", wf, "t", { iteration: 0, dbt: true })).ok, true);
  assert.equal(await file("dbt", "models", "orders_out.sql"), BROKEN_DBT);
  assert.equal(await file("dbt", "models", "wf0001_seg_01_out.sql"), CANNED_DBT["models/wf0001_seg_01_out.sql"], "only the broken model is replaced");

  assert.equal((await runner.run("fixer", wf, "f", { iteration: 1, dbt: true })).ok, true);
  assert.equal(await file("dbt", "models", "orders_out.sql"), CANNED_DBT["models/orders_out.sql"]);
  assert.match((await file("dbt", "fix_log.md")) ?? "", /## iteration 1 — dbt project[\s\S]*status: FIXED/);

  assert.equal((await runner.run("fixer", wf, "f", { iteration: 2, dbt: true })).ok, true);
  assert.equal(((await file("dbt", "fix_log.md")) ?? "").match(/## iteration/g)?.length, 2, "the fix log is appended to, never replaced");
});

test("never-fixed:dbt serves the broken model to every fixer turn, and a variant is chosen in name order", async (t) => {
  const { runner, wf, file } = await dbtFixture(t, {
    scenario: "never-fixed:dbt",
    broken: { "models/b_second.sql": "-- never chosen\n", "models/a_first.sql": "-- BROKEN first\n" },
  });
  await runner.run("translator", wf, "t", { iteration: 0, dbt: true });
  assert.equal(await file("dbt", "models", "a_first.sql"), "-- BROKEN first\n");
  assert.equal(await file("dbt", "models", "b_second.sql"), undefined);
  await runner.run("fixer", wf, "f", { iteration: 1, dbt: true });
  assert.equal(await file("dbt", "models", "a_first.sql"), "-- BROKEN first\n");
  assert.match((await file("dbt", "fix_log.md")) ?? "", /status: UNFIXED/);
});

test("the reviewer in dbt scope replays canned/review.json to dbt/review.json", async (t) => {
  const { runner, wf, file } = await dbtFixture(t);
  assert.equal((await runner.run("reviewer", wf, "r", { iteration: 0, dbt: true })).ok, true);
  assert.deepEqual(JSON.parse((await file("dbt", "review.json")) ?? "{}"), { verdict: "PASS", findings: [] });
  assert.equal(await file("segments", "seg_01", "review.json"), undefined);
});

test("the validator in dbt scope runs scripts/validate_dbt.py <wf>", async (t) => {
  const ok = await dbtFixture(t);
  assert.deepEqual(await ok.runner.run("validator", ok.wf, "v", { iteration: 0, dbt: true }).then((r) => [r.ok, r.error]), [true, undefined]);
  assert.deepEqual(ok.py, [{ script: "scripts/validate_dbt.py", args: ["wf_0007"] }]);

  // exit 1 is a domain FAIL: the reports are written and the orchestrator reads the verdicts
  const failed = await dbtFixture(t, { exitCode: 1 });
  assert.equal((await failed.runner.run("validator", failed.wf, "v", { dbt: true })).ok, true);

  const crashed = await dbtFixture(t, { exitCode: 2 });
  const result = await crashed.runner.run("validator", crashed.wf, "v", { dbt: true });
  assert.deepEqual([result.ok, result.error], [false, "error"]);
  assert.match(result.detail ?? "", /validate_dbt\.py exit 2/);
});

test("MockRunner in dbt scope reports a missing canned project instead of inventing one", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "orch-dbt-empty-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const runner = new MockRunner(root, path.join(root, "samples"));
  const result = await runner.run("translator", { id: "wf_0007", status: {}, metrics: {} }, "t", { iteration: 0, dbt: true });
  assert.deepEqual([result.ok, result.error], [false, "missing-output"]);
  const review = await runner.run("reviewer", { id: "wf_0007", status: {}, metrics: {} }, "r", { dbt: true });
  assert.deepEqual([review.ok, review.error], [false, "missing-output"]);
});

test("CopilotRunner passes the dbt scope to the policy", async (t) => {
  const createModel = { toolName: "create", toolArgs: { path: "workflows/wf_0001/dbt/models/x.sql" } };
  const { root, wf, env, config } = await fixture(t);
  const scoped = await runnerFor(fakeClient(root, { toolCalls: [createModel] }), root, config, env)
    .run("translator", wf, "write the dbt project", { iteration: 0, dbt: true });
  assert.equal(scoped.ok, true, JSON.stringify(scoped));
  assert.equal(scoped.error, undefined);

  const audit = (await readFile(path.join(root, "workflows", "wf_0001", "audit.jsonl"), "utf8"))
    .split("\n").filter(Boolean).map((line) => JSON.parse(line));
  assert.equal(audit[0].dbt, true, "the audit line records the dbt scope");
  assert.equal(audit[0].decision, "allow");

  const unscoped = await runnerFor(fakeClient(root, { toolCalls: [createModel] }), root, config, env)
    .run("translator", wf, "write the dbt project", {});
  // Task L6 (R2): refused, inside the own workflow -- one attempted action, within the budget of 20
  assert.equal(unscoped.ok, true, JSON.stringify(unscoped));
  assert.equal(wf.metrics.translator?.actDenials, 1);
  const lines = (await readFile(path.join(root, "workflows", "wf_0001", "audit.jsonl"), "utf8")).split("\n").filter(Boolean);
  const last = JSON.parse(lines[lines.length - 1]);
  assert.deepEqual([last.decision, last.class], ["deny", "act"]);
  assert.match(last.reason, /no segment is in context/);
  assert.equal("dbt" in last, false, "an unscoped session's audit line carries no dbt key");
});

test("an error that carries a secret is redacted and bounded before it reaches AgentResult.detail", async (t) => {
  // errorText keeps a whole error object as JSON so classification sees everything; the same text
  // is returned as `detail`, which stages.ts logs to the console and records in the manifest.
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, {
    toolCalls: [],
    throws: { message: `upstream refused the request: api_key=sk-live-123456 ${"x".repeat(2000)}` },
  });
  const result = await run(client, root, config, env, wf);
  assert.equal(result.ok, false);
  assert.doesNotMatch(result.detail ?? "", /sk-live-123456/, "the secret must not survive into detail");
  assert.match(result.detail ?? "", /<redacted>/);
  assert.ok((result.detail ?? "").length <= 500, `detail is bounded like an audit line, got ${(result.detail ?? "").length}`);
});

// ---------- output targets phase 2, Task W2: a batched analyzer ----------
// Above the analyzer's character budget the orchestrator runs one analyzer call per batch of
// waves (`ctx.batch`). MockRunner replays deterministically: only that batch's canned contracts,
// plus the batch's two fragment files from the canned analysis.md / unsupported.json -- never the
// stitched analysis.md or unsupported.json, which scripts/stitch_analysis.py writes.

async function analyzerFixture(t: any): Promise<{ root: string; runner: MockRunner; wf: Manifest; file(...rest: string[]): Promise<string | undefined> }> {
  const root = await mkdtemp(path.join(os.tmpdir(), "orch-batch-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const canned = (...rest: string[]) => path.join(root, "samples", "wf_0006", "canned", ...rest);
  const write = async (file: string, text: string) => {
    await mkdir(path.dirname(file), { recursive: true });
    await writeFile(file, text, "utf8");
  };
  await write(canned("analysis.md"), "# wf_0006 analysis\n\nEvery tool classified.\n");
  await write(canned("unsupported.json"), '{"tier": "T2", "unsupported": [], "unknown": []}\n');
  for (const seg of ["seg_01", "seg_02", "seg_03"]) {
    await write(canned("segments", seg, "contract.json"), `{"segment": "${seg}", "target": "sql"}\n`);
  }
  const runner = new MockRunner(root, path.join(root, "samples"));
  const file = (...rest: string[]) =>
    readFile(path.join(root, "workflows", "wf_0006", ...rest), "utf8").then((text) => text, () => undefined);
  return { root, runner, wf: { id: "wf_0006", status: {}, metrics: {} }, file };
}

test("MockRunner's analyzer in a batch replays only that batch", async (t) => {
  const { runner, wf, file } = await analyzerFixture(t);
  const result = await runner.run("analyzer", wf, "analyze batch_02", { batch: { id: "batch_02", segments: ["seg_02", "seg_03"] } });
  assert.equal(result.ok, true, JSON.stringify(result));

  assert.equal(await file("segments", "seg_01", "contract.json"), undefined, "batch_01's contract is not this batch's");
  assert.match((await file("segments", "seg_02", "contract.json")) ?? "", /"seg_02"/);
  assert.match((await file("segments", "seg_03", "contract.json")) ?? "", /"seg_03"/);
  assert.equal(await file("analysis", "batch_02.md"), "# wf_0006 analysis\n\nEvery tool classified.\n");
  assert.equal(await file("analysis", "batch_02.unsupported.json"), '{"tier": "T2", "unsupported": [], "unknown": []}\n');
  assert.equal(await file("analysis.md"), undefined, "the stitched analysis.md is the script's, not the mock's");
  assert.equal(await file("unsupported.json"), undefined);
  assert.equal(wf.tier, undefined, "the tier comes from the stitched unsupported.json, not from one batch");

  // without a batch the replay is exactly today's
  const whole = await runner.run("analyzer", wf, "analyze", {});
  assert.equal(whole.ok, true);
  assert.match((await file("segments", "seg_01", "contract.json")) ?? "", /"seg_01"/);
  assert.equal(await file("analysis.md"), "# wf_0006 analysis\n\nEvery tool classified.\n");
  assert.equal(wf.tier, "T2");
});

test("CopilotRunner passes the analyzer batch to the policy and the audit trail", async (t) => {
  const fragment = { toolName: "create", toolArgs: { path: "workflows/wf_0001/analysis/batch_01.md" } };
  const { root, wf, env, config } = await fixture(t);
  const batched = await runnerFor(fakeClient(root, { toolCalls: [fragment] }), root, config, env)
    .run("analyzer", wf, "analyze batch_01", { batch: { id: "batch_01", segments: ["seg_01"] } });
  assert.equal(batched.ok, true, JSON.stringify(batched));

  const audit = (await readFile(path.join(root, "workflows", "wf_0001", "audit.jsonl"), "utf8"))
    .split("\n").filter(Boolean).map((line) => JSON.parse(line));
  assert.equal(audit[0].batch, "batch_01", "the audit line records the batch");
  assert.equal(audit[0].decision, "allow");

  const unbatched = await runnerFor(fakeClient(root, { toolCalls: [fragment] }), root, config, env).run("analyzer", wf, "analyze", {});
  // Task L6 (R2): refused, inside the own workflow -- one attempted action, within the budget of 20
  assert.equal(unbatched.ok, true, JSON.stringify(unbatched));
  const lines = (await readFile(path.join(root, "workflows", "wf_0001", "audit.jsonl"), "utf8")).split("\n").filter(Boolean);
  const last = JSON.parse(lines[lines.length - 1]);
  assert.deepEqual([last.decision, last.class], ["deny", "act"]);
  assert.match(last.reason, /analyzer may not write/);
  assert.equal("batch" in last, false, "an unbatched session's audit line carries no batch key");
});

// ---------- output targets phase 2, Task W4: a compaction memory aid ----------
// After every successful `session.compaction_complete`, CopilotRunner.run sends one short,
// `mode: "immediate"` follow-up telling a notes-keeping role (intake, analyzer, fixer) to re-read
// its notes file; every compaction (whatever the role) is counted in
// `manifest.metrics.<role>.compactions`, and the peak `assistant.usage` input tokens are recorded
// as `peakInputTokens`. A failed compaction is logged, not counted, and sends nothing. Handlers are
// unsubscribed on every exit path.

test("notesPath and notesReminder name only the fixed path; NOTES_ROLES is exactly intake, analyzer, fixer", () => {
  assert.equal(notesPath("wf_0001", "analyzer"), "workflows/wf_0001/notes/analyzer.md");
  assert.equal(notesPath("wf_0002", "fixer"), "workflows/wf_0002/notes/fixer.md");
  assert.deepEqual(NOTES_ROLES, ["intake", "analyzer", "fixer"]);
  const reminder = notesReminder("analyzer", "wf_0001");
  assert.match(reminder, /workflows\/wf_0001\/notes\/analyzer\.md/);
  assert.match(reminder, /compacted/i);
});

test("a compaction sends one notes reminder and is counted in metrics", async (t) => {
  const { root, wf, env, config, logs } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const client = fakeClient(root, { compactions: [{ success: true }, { success: true }] }, calls);
  const result = await run(client, root, config, env, wf, "analyzer");
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(calls.sends.length, 2, "one send per successful compaction");
  for (const sent of calls.sends) {
    assert.equal(sent.mode, "immediate");
    assert.match(sent.prompt, /workflows\/wf_0001\/notes\/analyzer\.md/);
  }
  assert.equal(wf.metrics.analyzer?.compactions, 2);
  const compactionLogs = logs.filter((line) => line.includes("compacted"));
  assert.equal(compactionLogs.length, 2, `one log line per compaction; logs were ${JSON.stringify(logs)}`);
});

test("a failed compaction is logged, not counted, and sends nothing", async (t) => {
  const { root, wf, env, config, logs } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const client = fakeClient(root, { compactions: [{ success: false }] }, calls);
  const result = await run(client, root, config, env, wf, "analyzer");
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(calls.sends.length, 0, "a failed compaction sends no reminder");
  assert.equal(wf.metrics.analyzer?.compactions ?? 0, 0, "a failed compaction is not counted");
  assert.ok(logs.some((line) => line.includes("compaction failed")), JSON.stringify(logs));
});

test("Task W4 fix round 1: a fire-and-forget reminder that fails to send is logged, not swallowed", async (t) => {
  const { root, wf, env, config, logs } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const client = fakeClient(root, { compactions: [{ success: true }], sendFails: true }, calls);
  const result = await run(client, root, config, env, wf, "analyzer");
  assert.equal(result.ok, true, JSON.stringify(result));
  // The compaction itself still counts; the send failure is a separate, logged event.
  assert.equal(wf.metrics.analyzer?.compactions, 1);
  assert.equal(calls.sends.length, 0, "the fake session never actually recorded a send when it fails");
  // Let the fire-and-forget promise's rejection handler run before checking logs.
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(
    logs.some((line) => line.includes("notes reminder failed to send")),
    `expected a log line for the failed send; logs were ${JSON.stringify(logs)}`,
  );
});

test("a role without notes is counted but not reminded", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const client = fakeClient(root, { compactions: [{ success: true }] }, calls);
  await run(client, root, config, env, wf, "reviewer");
  assert.equal(wf.metrics.reviewer?.compactions, 1, "every role's compactions are counted, notes-keeping or not");
  assert.equal(calls.sends.length, 0, "reviewer is not a notes-keeping role: nothing is sent");
});

test("assistant.usage input tokens are recorded as peakInputTokens; the max is kept across sessions", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const first = fakeClient(root, { usage: [1200, 9000, 4000] });
  await run(first, root, config, env, wf, "validator");
  assert.equal(wf.metrics.validator?.peakInputTokens, 9000);

  const second = fakeClient(root, { usage: [500, 3000] });
  await run(second, root, config, env, wf, "validator");
  assert.equal(wf.metrics.validator?.peakInputTokens, 9000, "a later, smaller session must not lower the peak");
});

test("handlers are unsubscribed when the session ends, including on a crash path", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const calls: FakeSessionCalls = { sends: [], sessions: [], order: [] };
  const client = fakeClient(root, { throws: new Error("session aborted: no further tool calls permitted") }, calls);
  const result = await run(client, root, config, env, wf, "analyzer");
  assert.equal(result.ok, false, "sanity: this session did crash");
  assert.equal(calls.sessions.length, 1);
  assert.equal(calls.sessions[0].handlerCount("session.compaction_complete"), 0, "unsubscribed even on the crash path");
  assert.equal(calls.sessions[0].handlerCount("assistant.usage"), 0);
});

test("MockRunner writes no notes and records no compactions", async (t) => {
  const { root, runner, wf } = await mockFixture(t, { target: "sql", canned: { "proc.sql": "-- x\n" } });
  assert.equal((await runner.run("translator", wf, "t", { segment: "seg_02", iteration: 0 })).ok, true);
  assert.equal((await runner.run("fixer", wf, "t", { segment: "seg_02", iteration: 1 })).ok, true);
  await assert.rejects(() => readFile(path.join(root, "workflows", "wf_0006", "notes", "translator.md"), "utf8"));
  await assert.rejects(() => readFile(path.join(root, "workflows", "wf_0006", "notes", "fixer.md"), "utf8"));
  assert.equal(wf.metrics.translator, undefined, "MockRunner never calls recordMetrics -- compactions are CopilotRunner's own concern");
  assert.equal(wf.metrics.fixer, undefined);
});

// ---------- output targets phase 2, Task N1: the notes directory exists before the session ----
// Live evidence (docs/live-smoke-test.md "Third live test"): every intake session tried to CREATE
// workflows/<wf>/notes/ itself (PowerShell New-Item/md, a python -c makedirs, once a `create` of a
// script that would make it) -- denied every time, at a cost of tool calls. The fix is on the
// orchestrator side: CopilotRunner.run creates the directory before createSession, for a
// notes-keeping role only, and never throws out of run if that fails.

for (const role of ["intake", "analyzer", "fixer"] as const) {
  test(`Task N1: the notes directory exists before createSession is called, for ${role}`, async (t) => {
    const { root, wf, env, config } = await fixture(t);
    const notesDir = path.join(root, "workflows", "wf_0001", "notes");
    const client: CopilotClient = {
      async createSession() {
        // The check itself has to happen here, at the moment createSession is called -- not
        // after run() returns, which could pass even if the directory were created too late
        // (e.g. only in the `finally` block, after the session already ran).
        assert.equal(existsSync(notesDir), true, `${role}: the notes directory must exist before createSession`);
        return {
          on() {
            return () => undefined;
          },
          async send() {
            return "";
          },
          async sendAndWait() {
            return undefined;
          },
          async disconnect() {
            return undefined;
          },
        };
      },
    } as unknown as CopilotClient;
    const result = await run(client, root, config, env, wf, role);
    assert.equal(result.ok, true, JSON.stringify(result));
  });
}

for (const role of ["translator", "reviewer", "validator", "documenter"] as const) {
  test(`Task N1: ${role} gets no notes directory`, async (t) => {
    const { root, wf, env, config } = await fixture(t);
    const sink: SessionConfig[] = [];
    const client = recordingClient(sink);
    const result = await run(client, root, config, env, wf, role);
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(
      existsSync(path.join(root, "workflows", "wf_0001", "notes")),
      false,
      `${role} is not a notes-keeping role; the orchestrator must create nothing`,
    );
  });
}

test("Task N1: running a notes role's session twice is idempotent", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const sink: SessionConfig[] = [];
  const client = recordingClient(sink);
  const first = await run(client, root, config, env, wf, "intake");
  const second = await run(client, root, config, env, wf, "intake");
  assert.equal(first.ok, true, JSON.stringify(first));
  assert.equal(second.ok, true, JSON.stringify(second));
  assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "notes")), true);
});

test("Task N1: a notes directory create failure is logged (naming the workflow and role), and the session still runs", async (t) => {
  const { root, wf, env, config, logs } = await fixture(t);
  // A FILE sitting where the notes directory should go makes mkdir(..., { recursive: true }) fail.
  await writeFile(path.join(root, "workflows", "wf_0001", "notes"), "not a directory\n", "utf8");
  const sink: SessionConfig[] = [];
  const client = recordingClient(sink);
  const result = await run(client, root, config, env, wf, "analyzer");
  assert.equal(result.ok, true, "a failed mkdir must never stop the session from running");
  assert.ok(
    logs.some((line) => line.includes("wf_0001") && line.includes("analyzer") && /notes/i.test(line)),
    `expected a log line naming the workflow and role; logs were ${JSON.stringify(logs)}`,
  );
});

// ---------- live hardening, Task L9 (R2): every role's output folders exist before its session ----
// Live evidence (task-L9-brief.md): a documenter session, on a workflow whose translation had already
// reached VALIDATED, parked trying `mkdir workflows/<wf>/docs` and `New-Item -Path
// workflows/<wf>/docs -ItemType Directory` itself -- denied, since no role's policy lane allows
// creating a directory. hooks.ts's outputFolders generalises Task N1's one special case (the notes
// folder, above) into every role's own output folders, built only from the role and the fixed ids
// ctx carries.

test("L9 R2: intake gets its intake/ folder created before createSession", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const intakeDir = path.join(root, "workflows", "wf_0001", "intake");
  const client: CopilotClient = {
    async createSession() {
      assert.equal(existsSync(intakeDir), true, "the intake directory must exist before createSession");
      return {
        on() { return () => undefined; },
        async send() { return ""; },
        async sendAndWait() { return undefined; },
        async disconnect() { return undefined; },
      };
    },
  } as unknown as CopilotClient;
  const result = await run(client, root, config, env, wf, "intake");
  assert.equal(result.ok, true, JSON.stringify(result));
});

test("L9 R2: a whole-workflow analyzer call (no batch) creates no segments/ or analysis/ folder", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const sink: SessionConfig[] = [];
  const client = recordingClient(sink);
  const result = await run(client, root, config, env, wf, "analyzer");
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "segments")), false);
  assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "analysis")), false);
  assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "notes")), true, "analyzer is still a notes-keeping role");
});

test("L9 R2: a batched analyzer call creates its own segments' folders and analysis/, and no other segment's", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const sink: SessionConfig[] = [];
  const client = recordingClient(sink);
  const result = await run(client, root, config, env, wf, "analyzer", { batch: { id: "batch_02", segments: ["seg_02", "seg_03"] } });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "segments", "seg_02")), true);
  assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "segments", "seg_03")), true);
  assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "segments", "seg_01")), false, "not this batch's segment");
  assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "analysis")), true);
});

test("L9 R2: translator/fixer with a segment get segments/<seg>/ created before createSession", async (t) => {
  for (const role of ["translator", "fixer"] as const) {
    const { root, wf, env, config } = await fixture(t);
    const sink: SessionConfig[] = [];
    const client = recordingClient(sink);
    const result = await run(client, root, config, env, wf, role, { segment: "seg_07" });
    assert.equal(result.ok, true, `${role}: ${JSON.stringify(result)}`);
    assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "segments", "seg_07")), true, role);
  }
});

test("L9 R2: translator/fixer in dbt scope get dbt/models/ (and so dbt/) created before createSession", async (t) => {
  for (const role of ["translator", "fixer"] as const) {
    const { root, wf, env, config } = await fixture(t);
    const sink: SessionConfig[] = [];
    const client = recordingClient(sink);
    const result = await run(client, root, config, env, wf, role, { dbt: true });
    assert.equal(result.ok, true, `${role}: ${JSON.stringify(result)}`);
    assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "dbt", "models")), true, role);
    assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "dbt")), true, role);
  }
});

test("L9 R2: documenter gets docs/ created before createSession, and never procs/ (the documenter has no write lane there)", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const docsDir = path.join(root, "workflows", "wf_0001", "docs");
  const client: CopilotClient = {
    async createSession() {
      assert.equal(existsSync(docsDir), true, "the docs directory must exist before createSession");
      return {
        on() { return () => undefined; },
        async send() { return ""; },
        async sendAndWait() { return undefined; },
        async disconnect() { return undefined; },
      };
    },
  } as unknown as CopilotClient;
  const result = await run(client, root, config, env, wf, "documenter");
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "procs")), false);
});

test("L9 R2: reviewer and validator get no output folder of their own", async (t) => {
  for (const role of ["reviewer", "validator"] as const) {
    const { root, wf, env, config } = await fixture(t);
    const sink: SessionConfig[] = [];
    const client = recordingClient(sink);
    const result = await run(client, root, config, env, wf, role, { segment: "seg_01" });
    assert.equal(result.ok, true, `${role}: ${JSON.stringify(result)}`);
    // Nothing under workflows/wf_0001/ is created for these two roles by this mechanism -- their
    // segments/<seg>/ (or dbt/) already exists by the time they run, from an earlier role's session.
    assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "segments")), false, role);
  }
});

test("L9 R2: one folder's create failure is logged by name and never stops another folder of the same session", async (t) => {
  const { root, wf, env, config, logs } = await fixture(t);
  // A FILE sitting where "analysis" should go makes that one mkdir fail; "segments/seg_02" and
  // "segments/seg_03" are unobstructed siblings in the very same batched call.
  await mkdir(path.join(root, "workflows", "wf_0001"), { recursive: true });
  await writeFile(path.join(root, "workflows", "wf_0001", "analysis"), "not a directory\n", "utf8");
  const sink: SessionConfig[] = [];
  const client = recordingClient(sink);
  const result = await run(client, root, config, env, wf, "analyzer", { batch: { id: "batch_02", segments: ["seg_02", "seg_03"] } });
  assert.equal(result.ok, true, "one folder failing to create must never stop the session from running");
  assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "segments", "seg_02")), true, "the sibling folder still gets created");
  assert.equal(existsSync(path.join(root, "workflows", "wf_0001", "segments", "seg_03")), true);
  assert.ok(
    logs.some((line) => line.includes("wf_0001") && line.includes("analyzer") && /analysis/i.test(line)),
    `expected a log line naming the workflow, the role and the failed folder; logs were ${JSON.stringify(logs)}`,
  );
});

// ---------- live hardening, Task L1 (R3): denials are graded ----------
// Live evidence (docs/live-smoke-test.md "Third live test"): all three intake sessions wrote
// intake/plan.md and mappings.yaml and were then parked `denied`. 33 of their 54 denials were refused
// reads -- a mangled absolute path, a sibling workflow's file, a PowerShell listing -- and the 21
// attempted actions were the notes-directory attempts Task N1 removes: this grading and N1 together
// are what would have let them finish. A blocked READ is recorded and budgeted
// (`budgets.maxReadDenialsPerSession`, default 20), and within the budget the session's outcome is
// decided exactly as if nothing had been denied. (Task L6 grades attempted actions too; see below.)

const READ_CALLS = {
  mangled: HALLUCINATED_PATH,
  // (Task L6, R2: a view that NAMES another workflow is severe; a broad read that could reach one is a read)
  sibling: { toolName: "glob", toolArgs: { pattern: "workflows/*/intake/plan.md" } },
  listing: { toolName: "powershell", toolArgs: { command: "Get-ChildItem -Recurse -File | Select-Object FullName", description: "list" } },
};
// Task L6 (R2): attempted actions that are not severe -- budgeted by `budgets.maxActDenialsPerSession` (default 20
// since L6 fix round 1; it was 3)
const ACT_CALLS: ToolCall[] = [
  // the e2e analyzer's interpreter one-liner (live; abbreviated)
  { toolName: "powershell", toolArgs: { command: "python -c \"import json; json.load(open('workflows/wf_0001/contract.json'))\" 2>&1; echo done", description: "Validate JSON files", initial_wait: 30, mode: "sync" } },
  // the wf_0006 probe's placeholder sub-agent message (live)
  { toolName: "write_agent", toolArgs: { message: "placeholder", agent_id: "noop" } },
  // a write inside the own workflow, outside intake's lane
  { toolName: "edit", toolArgs: { path: "workflows/wf_0001/segments/seg_01/contract.json", old_str: "a", new_str: "b" } },
  // an unknown tool (stop_powershell was one; L6 fix round 1 allows it)
  { toolName: "launch_rocket", toolArgs: {} },
];
const acts = (count: number): ToolCall[] => Array.from({ length: count }, (_, i) => ACT_CALLS[i % ACT_CALLS.length]);
const reads = (count: number): ToolCall[] => Array.from({ length: count }, (_, i) => Object.values(READ_CALLS)[i % 3]);

test("L1 R3: a session whose only denials are blocked reads, within the budget, is ok", async (t) => {
  const { root, wf, env, config, logs } = await fixture(t);
  const result = await run(fakeClient(root, { toolCalls: reads(3), fireSessionEnd: true }), root, config, env, wf);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.error, undefined);
  assert.equal(wf.metrics.intake?.readDenials, 3);
  assert.equal(wf.metrics.intake?.actDenials, 0);
  assert.equal(wf.metrics.intake?.severeDenials, 0);
  assert.ok(
    logs.includes("wf_0001: intake session denials: 3 read (budget 20), 0 act (budget 20), 0 severe — see workflows/wf_0001/audit.jsonl"),
    JSON.stringify(logs),
  );
  // every denial is still in the audit trail, first
  const audit = (await readFile(path.join(root, "workflows", "wf_0001", "audit.jsonl"), "utf8")).split("\n").filter(Boolean).map((line) => JSON.parse(line));
  assert.equal(audit.filter((line) => line.ev === "pre" && line.decision === "deny").length, 3);
});

test("L1 R3: exactly the budget of read denials is ok; one more parks the session as denied, with the reason", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const atBudget = await run(fakeClient(root, { toolCalls: reads(20) }), root, config, env, wf);
  assert.equal(atBudget.ok, true, JSON.stringify(atBudget));

  const over = await run(fakeClient(root, { toolCalls: reads(21) }), root, config, env, wf);
  assert.equal(over.ok, false);
  assert.equal(over.error, "denied");
  assert.match(over.detail ?? "", /^read-denials: 21 over the budget of 20; view: path outside the repository/);

  // the budget is the configured one
  const tight: OrchestratorConfig = { ...config, budgets: { ...config.budgets, maxReadDenialsPerSession: 2 } };
  const overTight = await run(fakeClient(root, { toolCalls: reads(3) }), root, tight, env, wf);
  assert.equal(overTight.error, "denied");
  assert.match(overTight.detail ?? "", /^read-denials: 3 over the budget of 2; /);
  assert.equal(wf.metrics.intake?.readDenials, 44, "20 + 21 + 3 read denials, summed over sessions like toolCalls");
});

test("L1 R3, amended by L6: a severe attempt among blocked reads parks the session as denied, named first", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, { toolCalls: [READ_CALLS.mangled, SEVERE_CALL, READ_CALLS.sibling] });
  const result = await run(client, root, config, env, wf);
  assert.equal(result.error, "denied");
  assert.match(result.detail ?? "", /^severe-denials: 1 \(parks at once\); create: scripts\/ is read-only for every role[^;]*; view: /);
  assert.equal(wf.metrics.intake?.severeDenials, 1);
  assert.equal(wf.metrics.intake?.actDenials, 0);
  assert.equal(wf.metrics.intake?.readDenials, 2);
});

test("L1 R3: the crash path grades denials the same way", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const aborted = new Error("session aborted: no further tool calls permitted");
  // reads within the budget do not explain a crash: the session ends as the error it threw
  const readsOnly = await run(fakeClient(root, { toolCalls: reads(2), throws: aborted }), root, config, env, wf);
  assert.equal(readsOnly.error, "error");
  assert.match(readsOnly.detail ?? "", /session aborted/);
  // a severe attempt does (Task L6)
  const withSevere = await run(fakeClient(root, { toolCalls: [READ_CALLS.listing, SEVERE_CALL], throws: aborted }), root, config, env, wf);
  assert.equal(withSevere.error, "denied");
  assert.match(withSevere.detail ?? "", /^severe-denials: 1 /);
  // attempted actions within their budget do not; over it, they do
  const withActs = await run(fakeClient(root, { toolCalls: acts(20), throws: aborted }), root, config, env, wf);
  assert.equal(withActs.error, "error");
  const overActs = await run(fakeClient(root, { toolCalls: acts(21), throws: aborted }), root, config, env, wf);
  assert.equal(overActs.error, "denied");
  assert.match(overActs.detail ?? "", /^act-denials: 21 over the budget of 20; /);
  // and so do reads over the budget
  const tight: OrchestratorConfig = { ...config, budgets: { ...config.budgets, maxReadDenialsPerSession: 1 } };
  const overBudget = await run(fakeClient(root, { toolCalls: reads(2), throws: aborted }), root, tight, env, wf);
  assert.equal(overBudget.error, "denied");
  assert.match(overBudget.detail ?? "", /^read-denials: 2 over the budget of 1; /);
  // a real error signature still wins over denials within their budgets...
  const overflow = await run(fakeClient(root, { toolCalls: [ACT_CALLS[0]], throws: new Error(CONTEXT_OVERFLOW_TEXT) }), root, config, env, wf);
  assert.equal(overflow.error, "context-overflow");
  // ...but never over a severe one (L6 fix round 2, I3)
  const severeOverflow = await run(fakeClient(root, { toolCalls: [SEVERE_CALL], throws: new Error(CONTEXT_OVERFLOW_TEXT) }), root, config, env, wf);
  assert.equal(severeOverflow.error, "denied");
});

// L6 fix round 2 (I3): R2 says a severe denial parks the session at once. The catch path used to judge
// the thrown error first, so a severe attempt followed by a timeout or a rate limit came back as
// `timeout` / `rate-limit` -- retried, or (L7) kept as a success when the output passed its check.
test("L6 fix 2 (I3): a severe denial parks the session on every exit path", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  for (const [how, throws] of [
    ["completes normally", undefined],
    ["then times out", new Error("Timeout after 2700000ms waiting for session.idle")],
    ["then hits a rate limit", new Error("HTTP 429 rate limit exceeded")],
    ["then overflows the context", new Error(CONTEXT_OVERFLOW_TEXT)],
    ["then crashes otherwise", new Error("connection reset")],
  ] as [string, Error | undefined][]) {
    const result = await run(fakeClient(root, { toolCalls: [READ_CALLS.mangled, SEVERE_CALL], throws }), root, config, env, wf, "translator");
    assert.equal(result.ok, false, how);
    assert.equal(result.error, "denied", how);
    assert.match(result.detail ?? "", /^severe-denials: 1 \(parks at once\); /, how);
    assert.equal(result.severeDenials, 1, `${how}: the result says how many severe denials the session had`);
  }
  // the order for everything else is unchanged: a timeout after blocked reads is a timeout
  const readsThenTimeout = await run(fakeClient(root, { toolCalls: [READ_CALLS.mangled], throws: new Error("Timeout after 2700000ms waiting for session.idle") }), root, config, env, wf);
  assert.equal(readsThenTimeout.error, "timeout");
  assert.equal(readsThenTimeout.severeDenials, 0);
});

test("L1 R3: the default budget is 20, in DEFAULT_CONFIG and the committed orchestrator.config.json", async () => {
  assert.equal(DEFAULT_CONFIG.budgets.maxReadDenialsPerSession, 20);
  const committed = JSON.parse(await readFile(path.join(import.meta.dirname, "..", "..", "orchestrator.config.json"), "utf8"));
  assert.equal(committed.budgets.maxReadDenialsPerSession, 20);
});

// ---------- live hardening, Task L6 (R2): three classes -- read and act are budgeted, severe parks ----------
// Live evidence (the end-to-end run of wf_0001): the analyzer's contract passed contract_check.py and
// check_seams.py, and the session still parked `denied` on one misclassified read and two benign attempted
// actions (an interpreter one-liner, an edit of intake's file inside its own workflow). Every refused call is
// still refused, audited and counted; a SEVERE one -- outside the workflow or the sandbox, or damage -- parks
// the session at once; an attempted action parks it only beyond `budgets.maxActDenialsPerSession` (default 20
// since L6 fix round 1, the same as reads: the budgets catch thrashing, the stage's checks judge the output).

test("L6 R2: twenty attempted actions are within the budget; a twenty-first parks the session as denied, with the reason", async (t) => {
  // (L6 fix round 1: the default act budget is 20, the same as reads; it was 3)
  const { root, wf, env, config, logs } = await fixture(t);
  const atBudget = await run(fakeClient(root, { toolCalls: [...acts(20), ...reads(2)] }), root, config, env, wf);
  assert.equal(atBudget.ok, true, JSON.stringify(atBudget));
  assert.equal(atBudget.error, undefined);
  assert.ok(
    logs.includes("wf_0001: intake session denials: 2 read (budget 20), 20 act (budget 20), 0 severe — see workflows/wf_0001/audit.jsonl"),
    JSON.stringify(logs),
  );

  const over = await run(fakeClient(root, { toolCalls: [...reads(1), ...acts(21)] }), root, config, env, wf);
  assert.equal(over.ok, false);
  assert.equal(over.error, "denied");
  assert.match(over.detail ?? "", /^act-denials: 21 over the budget of 20; powershell: shell metacharacter in command: python -c/);
  assert.equal(wf.metrics.intake?.actDenials, 41, "20 + 21 attempted actions, summed over sessions like toolCalls");
  assert.equal(wf.metrics.intake?.readDenials, 3);
  // the reads are listed after the actions (a shorter session shows it inside the bounded detail)
  const small: OrchestratorConfig = { ...config, budgets: { ...config.budgets, maxActDenialsPerSession: 1 } };
  const ordered = await run(fakeClient(root, { toolCalls: [...reads(1), ...acts(2)] }), root, small, env, wf);
  assert.match(ordered.detail ?? "", /^act-denials: 2 over the budget of 1; powershell: .*; write_agent: .*; view: path outside the repository/);

  // every refused call is still audited, with its class
  const audit = (await readFile(path.join(root, "workflows", "wf_0001", "audit.jsonl"), "utf8")).split("\n").filter(Boolean).map((line) => JSON.parse(line));
  const denied = audit.filter((line) => line.ev === "pre" && line.decision === "deny");
  assert.equal(denied.length, 22 + 22 + 3);
  assert.deepEqual([...new Set(denied.map((line) => line.class))].sort(), ["act", "read"]);
});

test("L6 R2: the act budget is the configured one, and 0 restores the spec's rule (any attempted action parks)", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const one: OrchestratorConfig = { ...config, budgets: { ...config.budgets, maxActDenialsPerSession: 1 } };
  assert.equal((await run(fakeClient(root, { toolCalls: acts(1) }), root, one, env, wf)).ok, true);
  const overOne = await run(fakeClient(root, { toolCalls: acts(2) }), root, one, env, wf);
  assert.equal(overOne.error, "denied");
  assert.match(overOne.detail ?? "", /^act-denials: 2 over the budget of 1; /);

  const zero: OrchestratorConfig = { ...config, budgets: { ...config.budgets, maxActDenialsPerSession: 0 } };
  const none = await run(fakeClient(root, { toolCalls: acts(1) }), root, zero, env, wf);
  assert.equal(none.error, "denied");
  assert.match(none.detail ?? "", /^act-denials: 1 over the budget of 0; /);
});

test("L6 R2: one severe denial parks the session at once, whatever the budgets", async (t) => {
  const { root, wf, env, config, logs } = await fixture(t);
  const generous: OrchestratorConfig = { ...config, budgets: { ...config.budgets, maxActDenialsPerSession: 50, maxReadDenialsPerSession: 50 } };
  for (const severe of [
    SEVERE_CALL,
    { toolName: "view", toolArgs: { path: "workflows/wf_0002/intake/plan.md" } },
    { toolName: "snowflake_query", toolArgs: { sql: "SELECT 1 FROM MIG_WORK.T" } },
    { toolName: "powershell", toolArgs: { command: "python scripts/intake_touchpoints.py wf_0001 --root ." } },
    { toolName: "powershell", toolArgs: { command: "iwr https://example.com -OutFile x" } },
    { toolName: "powershell", toolArgs: { command: "echo $env:SNOWFLAKE_PASSWORD" } },
    { toolName: "powershell", toolArgs: { command: "rm -rf workflows; echo done" } },
  ]) {
    const result = await run(fakeClient(root, { toolCalls: [READ_CALLS.mangled, ACT_CALLS[1], severe] }), root, generous, env, wf);
    assert.equal(result.error, "denied", JSON.stringify(severe));
    assert.match(result.detail ?? "", new RegExp(`^severe-denials: 1 \\(parks at once\\); ${severe.toolName}: `), JSON.stringify(severe));
    assert.match(result.detail ?? "", /; write_agent: .*; view: path outside the repository/, "then the acts, then the reads");
  }
  assert.equal(wf.metrics.intake?.severeDenials, 7);
  assert.ok(
    logs.includes("wf_0001: intake session denials: 1 read (budget 50), 1 act (budget 50), 1 severe — see workflows/wf_0001/audit.jsonl"),
    JSON.stringify(logs),
  );
  const audit = (await readFile(path.join(root, "workflows", "wf_0001", "audit.jsonl"), "utf8")).split("\n").filter(Boolean).map((line) => JSON.parse(line));
  assert.equal(audit.filter((line) => line.ev === "pre" && line.class === "severe").length, 7, "the audit's pre line records the class");
});

test("L6: the e2e analyzer session, replayed with its own denials, ends ok -- its outputs decide", async (t) => {
  const { root, wf, env, config, logs } = await fixture(t);
  const live: ToolCall[] = [
    { toolName: "glob", toolArgs: { pattern: "*" } },
    { toolName: "grep", toolArgs: { pattern: "status\\.analyze|\"status\"", output_mode: "content", "-n": true } },
    { toolName: "powershell", toolArgs: { command: "Get-ChildItem -Path . -Directory -Recurse -Name | Select-Object -First 60", description: "List top-level directories", initial_wait: 30, mode: "sync" } },
    { toolName: "view", toolArgs: { path: "." } },
    { toolName: "glob", toolArgs: { pattern: "*/" } },
    { toolName: "powershell", toolArgs: { command: 'Get-Content scripts/lib/io.py -Raw | Select-String -Pattern "manifest|read_manifest|manifest_path|write_manifest|def "', description: "Find manifest functions in io.py", initial_wait: 30, mode: "sync" } },
    { toolName: "glob", toolArgs: { pattern: "**/manifest*.md" } },
    { toolName: "glob", toolArgs: { pattern: "**/canned/**" } },
    ACT_CALLS[0],
    { toolName: "edit", toolArgs: { path: "workflows/wf_0001/intake/open_questions.md", old_str: "(none)", new_str: "- [ ] Q4" } },
  ];
  const result = await run(fakeClient(root, { toolCalls: live, fireSessionEnd: true }), root, config, env, wf, "analyzer");
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.deepEqual(
    [wf.metrics.analyzer?.readDenials, wf.metrics.analyzer?.actDenials, wf.metrics.analyzer?.severeDenials],
    [8, 2, 0],
  );
  assert.ok(
    logs.includes("wf_0001: analyzer session denials: 8 read (budget 20), 2 act (budget 20), 0 severe — see workflows/wf_0001/audit.jsonl"),
    JSON.stringify(logs),
  );
});

test("L6 fix 1: the default act budget is 20, in DEFAULT_CONFIG and the committed orchestrator.config.json", async () => {
  assert.equal(DEFAULT_CONFIG.budgets.maxActDenialsPerSession, 20);
  const committed = JSON.parse(await readFile(path.join(import.meta.dirname, "..", "..", "orchestrator.config.json"), "utf8"));
  assert.equal(committed.budgets.maxActDenialsPerSession, 20);
});

// ---------- live hardening, Task L1 (R2): the SDK's spill file, end to end ----------

test("L1 R2: a session reads the spill file its own listing produced, and ends ok with no denial", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  // fix round 1 (M5): the spill file must exist and be no older than the session, as the SDK's would be
  const spill = path.join(os.tmpdir(), `${Date.now()}-copilot-tool-output-${process.pid}-${randomUUID()}.txt`);
  await writeFile(spill, "the full listing\n", "utf8");
  t.after(() => rm(spill, { force: true }));
  const client = fakeClient(root, {
    toolCalls: [
      {
        toolName: "powershell",
        // (live, the listing was `Get-ChildItem -Recurse` at the root; since fix round 2, S2, a
        // recursive listing must stay inside the own workflow)
        toolArgs: { command: "Get-ChildItem -Recurse workflows/wf_0001", description: "List all files and directories recursively" },
        result: `Directory: workflows\n[Truncated — full output (48213 characters) temporarily saved to ${spill}]`,
      },
      { toolName: "view", toolArgs: { path: spill } },
      { toolName: "grep", toolArgs: { pattern: "plan.md", paths: [spill], output_mode: "content" } },
    ],
  });
  const result = await run(client, root, config, env, wf);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(wf.metrics.intake?.readDenials, 0);
  assert.equal(wf.metrics.intake?.actDenials, 0);

  // a second session never saw that result: the same file is a blocked read there
  const other = await run(fakeClient(root, { toolCalls: [{ toolName: "view", toolArgs: { path: spill } }] }), root, config, env, wf);
  assert.equal(other.ok, true, "a single blocked read is within the budget");
  assert.equal(wf.metrics.intake?.readDenials, 1);
});
