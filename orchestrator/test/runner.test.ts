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
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { CopilotClient, SessionHooks } from "@github/copilot-sdk";
import { DEFAULT_CONFIG } from "../cli.ts";
import { CopilotRunner } from "../runner.ts";
import { toolCallsUsed } from "../stages.ts";
import type { Env, Manifest, OrchestratorConfig, Role } from "../types.ts";

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
}

/** A fake CopilotClient whose createSession returns a session that drives the real hooks
 * CopilotRunner built via hooksFor, the same way the SDK would -- everything downstream
 * (policy.ts's decide, hooks.ts's audit/classification) is the real code under test. */
function fakeClient(root: string, scenario: Scenario): CopilotClient {
  return {
    async createSession(config: { hooks?: SessionHooks }) {
      const hooks = config.hooks!;
      const base = { sessionId: "session-1", timestamp: new Date(), workingDirectory: root };
      return {
        async sendAndWait() {
          for (const call of scenario.toolCalls ?? []) {
            await hooks.onPreToolUse!({ ...base, toolName: call.toolName, toolArgs: call.toolArgs }, invocation);
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
        async disconnect() {
          return undefined;
        },
      };
    },
  } as unknown as CopilotClient;
}

async function fixture(t: any): Promise<{ root: string; wf: Manifest; env: Env; config: OrchestratorConfig }> {
  const root = await mkdtemp(path.join(os.tmpdir(), "orch-runner-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, "workflows", "wf_0001"), { recursive: true });
  await writeFile(path.join(root, "workflows", "wf_0001", "manifest.json"), '{"id":"wf_0001","status":{},"metrics":{}}\n', "utf8");
  const wf: Manifest = { id: "wf_0001", tier: "T1", status: {}, metrics: {} };
  const config: OrchestratorConfig = { ...DEFAULT_CONFIG, sessionTimeoutMs: 5_000 };
  const env = { root, config, log: () => undefined, interactive: false } as unknown as Env;
  return { root, wf, env, config };
}

function runnerFor(client: CopilotClient, root: string, config: OrchestratorConfig, env: Env): CopilotRunner {
  const r = new CopilotRunner({ client, root, config, profile: "local", agents: [] });
  r.attach(env);
  return r;
}

const run = (client: CopilotClient, root: string, config: OrchestratorConfig, env: Env, wf: Manifest, role: Role = "intake") =>
  runnerFor(client, root, config, env).run(role, wf, "do the thing");

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

test("d1: a session that ends because of a denial, with no other error signature, still reports denied", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, {
    toolCalls: [HALLUCINATED_PATH],
    throws: new Error("session aborted: no further tool calls permitted"),
  });
  const result = await run(client, root, config, env, wf);
  assert.equal(result.error, "denied");
  assert.match(result.detail ?? "", /outside the repository/);
});

test("d1: a session denied earlier that completes without throwing still reports denied (unchanged behaviour)", async (t) => {
  const { root, wf, env, config } = await fixture(t);
  const client = fakeClient(root, { toolCalls: [HALLUCINATED_PATH] }); // no throw: sendAndWait resolves
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
