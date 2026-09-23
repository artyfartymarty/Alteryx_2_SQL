// The hooks table from docs/spec/01-copilot-setup.md Part B §5: permissions, audit,
// secret flagging, error classification and per-role metrics.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  hooksFor,
  redact,
  auditArgs,
  AUDIT_ARG_LIMIT,
  errorText,
  recordMetrics,
  RATE_LIMIT,
  CONTEXT_OVERFLOW,
  type HookState,
} from "../hooks.ts";
import { loadManifest } from "../manifest.ts";
import type { Env, Manifest } from "../types.ts";

const invocation = { sessionId: "session-1" };
const base = (root: string) => ({ sessionId: "session-1", timestamp: new Date(), workingDirectory: root });

async function fixture(t: any): Promise<{ root: string; wf: Manifest; env: Env; logs: string[] }> {
  const root = await mkdtemp(path.join(os.tmpdir(), "orch-hooks-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, "workflows", "wf_0001"), { recursive: true });
  await writeFile(path.join(root, "workflows", "wf_0001", "manifest.json"), '{"id":"wf_0001","status":{},"metrics":{}}\n', "utf8");
  const wf: Manifest = { id: "wf_0001", tier: "T1", status: {}, metrics: {} };
  const logs: string[] = [];
  const env = {
    root,
    config: { policy: { sandboxDatabases: ["ALTDB"] } },
    log: (line: string) => logs.push(line),
  } as unknown as Env;
  return { root, wf, env, logs };
}

const auditLines = async (root: string): Promise<any[]> =>
  (await readFile(path.join(root, "workflows", "wf_0001", "audit.jsonl"), "utf8"))
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));

test("audit arguments are scrubbed of secrets and truncated", () => {
  assert.equal(redact("--token: abc123 --pwd=hunter2"), "--<redacted> --<redacted>");
  assert.equal(redact('{"api_key": "sk-live-4242"}'), '{"<redacted>}');
  assert.equal(redact("nothing to hide here"), "nothing to hide here");
  const long = auditArgs({ blob: "x".repeat(2000), password: "hunter2" });
  assert.equal(long.length, AUDIT_ARG_LIMIT);
  assert.equal(long.includes("hunter2"), false);
});

test("onSessionStart states the workflow, the program answers and the sandbox", async (t) => {
  const { wf, env } = await fixture(t);
  const { hooks } = hooksFor("translator", wf, env, "seg_01");
  const out = await hooks.onSessionStart!({ ...base(env.root), source: "startup" }, invocation);
  const lines = (out as { additionalContext: string }).additionalContext.split("\n");
  assert.equal(lines.length, 3);
  assert.match(lines[0], /Workflow wf_0001.*manifest\.json.*Tier: T1/);
  assert.match(lines[1], /mappings\/global\.yaml.*cookbook\/index\.md/);
  assert.match(lines[2], /MIG_WORK, MIG_GOLDEN.*Nothing is deployed/);
});

test("onPreToolUse decides, counts and audits, and records a denial", async (t) => {
  const { root, wf, env } = await fixture(t);
  const { hooks, state } = hooksFor("translator", wf, env, "seg_01");

  const allowed = await hooks.onPreToolUse!(
    { ...base(root), toolName: "edit", toolArgs: { path: "workflows/wf_0001/segments/seg_01/proc.sql" } },
    invocation,
  );
  assert.deepEqual(allowed, { permissionDecision: "allow" });
  assert.equal(state.denied, false);

  const refused = await hooks.onPreToolUse!(
    { ...base(root), toolName: "edit", toolArgs: { path: "workflows/wf_0001/golden/inputs/normal/1.csv", token: "sk-live-1" } },
    invocation,
  );
  assert.equal((refused as any).permissionDecision, "deny");
  assert.equal(state.denied, true);
  assert.equal(state.toolCalls, 2);
  assert.match(state.denials[0], /golden/);

  const lines = await auditLines(root);
  assert.deepEqual(lines.map((l) => l.ev), ["pre", "pre"]);
  assert.deepEqual(lines.map((l) => l.decision), ["allow", "deny"]);
  assert.equal(lines[1].args.includes("sk-live-1"), false, "audited args are scrubbed");
  assert.equal(lines[0].role, "translator");
  assert.equal(lines[0].segment, "seg_01");
});

test("onPreToolUse hands the policy the repo root and audits every unrecognized tool", async (t) => {
  const { root, wf, env } = await fixture(t);
  const { hooks, state } = hooksFor("intake", wf, env);

  // An absolute path is only judgeable against a root, which the hook supplies.
  const inside = await hooks.onPreToolUse!(
    { ...base(root), toolName: "edit", toolArgs: { path: path.join(root, "workflows", "wf_0001", "intake", "plan.md") } },
    invocation,
  );
  assert.deepEqual(inside, { permissionDecision: "allow" });
  const outside = await hooks.onPreToolUse!(
    { ...base(root), toolName: "edit", toolArgs: { path: "C:/Windows/System32/drivers/etc/hosts" } },
    invocation,
  );
  assert.match((outside as any).permissionDecisionReason, /outside the repository/);

  const unknown = await hooks.onPreToolUse!({ ...base(root), toolName: "browser_open", toolArgs: {} }, invocation);
  assert.match((unknown as any).permissionDecisionReason, /unrecognized tool/);
  assert.equal(state.denied, true);

  const events = (await auditLines(root)).map((line) => line.ev);
  assert.deepEqual(events, ["pre", "pre", "pre", "unrecognized-tool"]);
});

test("onPreToolUse hands the policy the configured sandbox databases", async (t) => {
  const { root, wf, env } = await fixture(t); // config declares ALTDB, not the default MIGDB
  const { hooks } = hooksFor("validator", wf, env, "seg_01");
  const ask = (sql: string) => hooks.onPreToolUse!({ ...base(root), toolName: "snowflake_query", toolArgs: { sql } }, invocation);

  assert.deepEqual(await ask("SELECT * FROM ALTDB.MIG_WORK.T"), { permissionDecision: "allow" });
  const wrongDatabase = (await ask("SELECT * FROM MIGDB.MIG_WORK.T")) as any;
  assert.equal(wrongDatabase.permissionDecision, "deny");
  assert.match(wrongDatabase.permissionDecisionReason, /sandbox database ALTDB/);
});

test("onPostToolUse flags secrets and oversized results", async (t) => {
  const { root, wf, env } = await fixture(t);
  const { hooks } = hooksFor("validator", wf, env, "seg_01");
  const result = { resultType: "success" as const, textResultForLlm: `${"y".repeat(200_001)} password: hunter2` };
  await hooks.onPostToolUse!({ ...base(root), toolName: "snowflake_query", toolArgs: {}, toolResult: result }, invocation);
  const events = (await auditLines(root)).map((l) => l.ev);
  assert.deepEqual(events, ["post", "secret-in-result", "large-result"]);
});

test("onErrorOccurred classifies a 429 as rate-limit; onPostToolUseFailure just records", async (t) => {
  const { root, wf, env } = await fixture(t);
  const { hooks, state } = hooksFor("fixer", wf, env, "seg_01");
  await hooks.onPostToolUseFailure!({ ...base(root), toolName: "bash", toolArgs: {}, error: "exit 1" }, invocation);
  await hooks.onErrorOccurred!(
    { ...base(root), error: "HTTP 429 rate limit exceeded", errorContext: "model_call", recoverable: true },
    invocation,
  );
  assert.equal(state.rateLimited, true);
  const lines = await auditLines(root);
  assert.deepEqual(lines.map((l) => l.ev), ["tool-fail", "error"]);
  assert.equal(lines[1].class, "rate-limit");

  const other = hooksFor("fixer", wf, env, "seg_01");
  await other.hooks.onErrorOccurred!(
    { ...base(root), error: "connection reset", errorContext: "system", recoverable: false },
    invocation,
  );
  assert.equal(other.state.rateLimited, false);
});

test("onSessionEnd writes the role's metrics into the manifest on disk", async (t) => {
  const { root, wf, env } = await fixture(t);
  const { hooks } = hooksFor("analyzer", wf, env);
  await hooks.onPreToolUse!({ ...base(root), toolName: "view", toolArgs: { path: "cookbook/index.md" } }, invocation);
  await hooks.onSessionEnd!({ ...base(root), reason: "complete" }, invocation);

  assert.equal(wf.metrics.analyzer.toolCalls, 1);
  assert.equal(typeof wf.metrics.analyzer.lastMs, "number");
  const saved = await loadManifest(root, "wf_0001");
  assert.equal(saved.metrics.analyzer.toolCalls, 1);
});

// --- Task 16 diagnostics (d1-d3): live BYOK evidence (task-16-report.md, ATTEMPT 1/2) -------
// A context-window crash was (1) misreported as "denied" because runner.ts's catch block
// checked state.denied before the thrown error's own signature, (2) logged as the useless
// "[object Object]" because onErrorOccurred stringified a non-Error SDK error with plain
// String(), and (3) silently dropped from manifest.metrics because onSessionEnd -- the only
// place metrics were written -- never fired on this crash path. (1) is fixed in runner.ts (see
// orchestrator/test/runner.test.ts); (2) and (3) are the shared `errorText` and `recordMetrics`
// helpers below, used by both hooks.ts and runner.ts.

test("errorText extracts readable text from every shape an SDK-provided error can take, never '[object Object]'", () => {
  assert.equal(errorText("plain string"), "plain string");
  assert.equal(errorText(new Error("boom")), "boom");
  // Real shape observed live: onErrorOccurred's input.error was a plain, non-Error object with
  // its own .message, despite the SDK's own type declaring `error: string`.
  assert.equal(
    errorText({ message: "400 request (34965 tokens) exceeds the available context size (32768 tokens), try increasing it" }),
    "400 request (34965 tokens) exceeds the available context size (32768 tokens), try increasing it",
  );
  assert.equal(errorText(undefined), "");
  assert.equal(errorText(null), "");
  assert.notEqual(errorText({ code: "E_FOO", detail: "no message field here" }), "[object Object]");
  assert.match(errorText({ code: "E_FOO", detail: "no message field here" }), /E_FOO/);

  const cyclic: Record<string, unknown> = { code: "E_CYCLE" };
  cyclic.self = cyclic;
  const text = errorText(cyclic);
  assert.notEqual(text, "[object Object]");
  assert.match(text, /E_CYCLE/);
});

test("errorText collapses embedded newlines so an audit line stays single-line", () => {
  assert.equal(errorText("line one\nline two\r\nline three"), "line one line two line three");
  assert.equal(errorText(new Error("boom\nwith a newline")), "boom with a newline");
});

test("onErrorOccurred with a non-Error object error records readable text, not [object Object] (live: ATTEMPT 1)", async (t) => {
  const { root, wf, env } = await fixture(t);
  const { hooks, state } = hooksFor("intake", wf, env);
  await hooks.onErrorOccurred!(
    {
      ...base(root),
      error: { message: "400 request (34965 tokens) exceeds the available context size (32768 tokens), try increasing it" } as unknown as string,
      errorContext: "model_call",
      recoverable: true,
    },
    invocation,
  );
  const lines = await auditLines(root);
  assert.equal(lines[0].ev, "error");
  assert.equal(lines[0].error.includes("[object Object]"), false);
  assert.match(lines[0].error, /exceeds the available context size/);
  assert.equal(lines[0].class, "context-overflow");
  assert.equal(state.rateLimited, false);
});

test("onPostToolUseFailure with a non-string error also extracts readable text, not [object Object]", async (t) => {
  const { root, wf, env } = await fixture(t);
  const { hooks } = hooksFor("intake", wf, env);
  await hooks.onPostToolUseFailure!(
    {
      ...base(root),
      toolName: "task",
      toolArgs: {},
      error: { message: "400 request (34965 tokens) exceeds the available context size (32768 tokens), try increasing it" } as unknown as string,
    },
    invocation,
  );
  const lines = await auditLines(root);
  assert.equal(lines[0].ev, "tool-fail");
  assert.equal(lines[0].error.includes("[object Object]"), false);
  assert.match(lines[0].error, /exceeds the available context size/);
});

// --- F7 (final review): RATE_LIMIT's old, unanchored /429/ matched the digits "429" INSIDE a
// token count from the very context-overflow messages CONTEXT_OVERFLOW exists to classify, and
// rate-limit was tested before context-overflow -- so a context overflow was misclassified as a
// rate limit and burned three paid retries that could never succeed (RETRY_ONCE never retries
// context-overflow, but rate-limit backs off and retries up to MAX_RATE_LIMIT_RETRIES times). ---

test("RATE_LIMIT no longer fires on '429' embedded in a token count (context-overflow's own messages)", () => {
  assert.equal(
    RATE_LIMIT.test("request (42901 tokens) exceeds the available context size (32768 tokens)"),
    false,
    "429 is the start of 42901, not a standalone number",
  );
  assert.equal(
    RATE_LIMIT.test("request (14290 tokens) exceeds the available context size (8192 tokens)"),
    false,
    "429 is the middle of 14290, not a standalone number",
  );
  assert.equal(
    RATE_LIMIT.test('{"error":{"message":"too long","prompt_tokens":42900}}'),
    false,
    "429 is the start of 42900 inside a larger digit run, not a standalone number",
  );
});

test("RATE_LIMIT still fires on every real rate-limit signature", () => {
  assert.equal(RATE_LIMIT.test("429 Too Many Requests"), true);
  assert.equal(RATE_LIMIT.test("rate limit exceeded"), true);
  assert.equal(RATE_LIMIT.test("status: 429"), true);
  assert.equal(RATE_LIMIT.test("HTTP 429 rate limit exceeded"), true);
  assert.equal(RATE_LIMIT.test("quota exceeded for this model"), true);
});

test("onErrorOccurred classifies context-overflow, not rate-limit, for the exact live token-count messages", async (t) => {
  const { root, wf, env } = await fixture(t);
  for (const text of [
    "400 request (42901 tokens) exceeds the available context size (32768 tokens), try increasing it",
    "400 request (14290 tokens) exceeds the available context size (8192 tokens), try increasing it",
  ]) {
    const { hooks, state } = hooksFor("fixer", wf, env, "seg_01");
    await hooks.onErrorOccurred!({ ...base(root), error: text, errorContext: "model_call", recoverable: true }, invocation);
    assert.equal(state.rateLimited, false, text);
    assert.equal(state.contextOverflow, true, text);
  }
  const lines = await auditLines(root);
  assert.ok(lines.every((l) => l.ev !== "error" || l.class === "context-overflow"));
});

test("onErrorOccurred: context-overflow wins over rate-limit when a message legitimately carries both signatures", async (t) => {
  const { root, wf, env } = await fixture(t);
  const { hooks, state } = hooksFor("fixer", wf, env, "seg_01");
  const both = "429: the request exceeds the available context size (32768 tokens)";
  assert.equal(RATE_LIMIT.test(both), true, "sanity: this text also matches RATE_LIMIT on its own");
  assert.equal(CONTEXT_OVERFLOW.test(both), true, "sanity: this text also matches CONTEXT_OVERFLOW on its own");
  await hooks.onErrorOccurred!({ ...base(root), error: both, errorContext: "model_call", recoverable: true }, invocation);
  assert.equal(state.contextOverflow, true);
  assert.equal(state.rateLimited, false, "context-overflow must win, not rate-limit, when both signatures are present");
  const lines = await auditLines(root);
  assert.equal(lines[0].class, "context-overflow");
});

test("recordMetrics adds to (not replaces) a role's prior toolCalls, and is idempotent per session state", () => {
  // The exact numbers from task-16-report.md: attempt 1 made 37 tool calls (recorded), attempt
  // 2 made 29 more but onSessionEnd never fired for it, so the stale 37 survived unrecorded.
  const wf: Manifest = { id: "wf_0001", status: {}, metrics: { intake: { toolCalls: 37, lastMs: 293830 } } };
  const state: HookState = {
    toolCalls: 29,
    denied: false,
    denials: [],
    rateLimited: false,
    contextOverflow: false,
    errors: [],
    metricsRecorded: false,
    compactions: 0,
    peakInputTokens: 0,
  };
  recordMetrics(wf, "intake", state, 2000);
  assert.equal(wf.metrics.intake.toolCalls, 66, "37 (already recorded) + 29 (this session) = 66, the real combined spend");
  assert.equal(wf.metrics.intake.lastMs, 2000, "lastMs names only the most recent session's own duration, not a sum");

  recordMetrics(wf, "intake", state, 9999); // a second call with the SAME state must be a no-op
  assert.equal(wf.metrics.intake.toolCalls, 66);
  assert.equal(wf.metrics.intake.lastMs, 2000);
});
