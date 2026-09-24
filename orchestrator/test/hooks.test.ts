// The hooks table from docs/spec/01-copilot-setup.md Part B §5: permissions, audit,
// secret flagging, error classification and per-role metrics.
import { test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtemp, mkdir, readFile, rm, utimes, writeFile } from "node:fs/promises";
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
  spillFilesNamedIn,
  recordableSpillFiles,
  SPILL_CLOCK_TOLERANCE_MS,
  outputFolders,
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
  assert.equal(state.denials.length, 0);

  const refused = await hooks.onPreToolUse!(
    { ...base(root), toolName: "edit", toolArgs: { path: "workflows/wf_0001/golden/inputs/normal/1.csv", token: "sk-live-1" } },
    invocation,
  );
  assert.equal((refused as any).permissionDecision, "deny");
  assert.equal(state.denials.length, 1);
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
  assert.equal(state.denials.length, 2);

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
    denials: [],
    readDenials: [],
    actDenials: [],
    severeDenials: [],
    spillFiles: new Set(),
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

// --- live hardening, Task L1: the SDK's spill files (R2) and graded denials (R3) -----------------
// Live evidence (docs/live-smoke-test.md "Third live test"): a `Get-ChildItem -Recurse` result came
// back 920 bytes long, cut off with the SDK's own "[Truncated — full output (…) temporarily saved to
// <temp>\<ms>-copilot-tool-output-<pid>-<uuid>.txt]", and the model's `view` of that file was
// denied as outside the repository. The hook now records such a path -- only in the SDK's two
// sentences, only inside the OS temp directory, only with the SDK's spill name -- and the policy
// lets THIS session read exactly that file with view or grep.

const SPILL_NAME = "1790163412714-copilot-tool-output-21368-34355146-2e9d-48e8-b348-30d1813a265c.txt";
const spillIn = (dir: string, name = SPILL_NAME) => path.join(dir, name);
/** A real spill-named file in the OS temp directory, written now and removed after the test (fix
 * round 1, M5: a spill path is recorded only if the file exists and is no older than the session). */
async function freshSpill(t: any): Promise<string> {
  const file = path.join(os.tmpdir(), `${Date.now()}-copilot-tool-output-${process.pid}-${randomUUID()}.txt`);
  await writeFile(file, "the full output\n", "utf8");
  t.after(() => rm(file, { force: true }));
  return file;
}
const truncated = (file: string) =>
  `Mode LastWriteTime Length Name\n---- ------------- ------ ----\nd---- workflows\n[Truncated \u2014 full output (48213 characters) temporarily saved to ${file}]`;
const tooLarge = (file: string) => `Output too large to read at once (48213 characters). Saved to: ${file}\nRead it in parts with view_range.`;
const post = (root: string, text: string, toolName = "powershell") => ({
  ...base(root),
  toolName,
  toolArgs: { command: "Get-ChildItem -Recurse" },
  toolResult: { resultType: "success" as const, textResultForLlm: text },
});

test("L1 R2: spillFilesNamedIn reads the SDK's two sentences, inside the temp directory, with the SDK's name", () => {
  const temp = os.tmpdir();
  const file = spillIn(temp);
  assert.deepEqual(spillFilesNamedIn(truncated(file), temp), [path.resolve(file)]);
  assert.deepEqual(spillFilesNamedIn(tooLarge(file), temp), [path.resolve(file)]);
  assert.deepEqual(spillFilesNamedIn(tooLarge(`${file}.`), temp), [path.resolve(file)], "a full stop after the path is not part of it");
  assert.deepEqual(spillFilesNamedIn(truncated(spillIn(path.join(temp, "nested"))), temp), [path.resolve(spillIn(path.join(temp, "nested")))]);
  // a forged path outside the temp directory
  const outside = spillIn(path.join(path.parse(temp).root, "not-temp"));
  assert.deepEqual(spillFilesNamedIn(truncated(outside), temp), []);
  assert.deepEqual(spillFilesNamedIn(truncated(spillIn(path.join(temp, "..", "sibling"))), temp), []);
  // a temp file with a name that is not the SDK's spill name
  assert.deepEqual(spillFilesNamedIn(truncated(path.join(temp, "notes.txt")), temp), []);
  assert.deepEqual(spillFilesNamedIn(truncated(path.join(temp, `${SPILL_NAME}.exe`)), temp), []);
  // a relative path, even with the spill name
  assert.deepEqual(spillFilesNamedIn(truncated(SPILL_NAME), temp), []);
  // the right path in any other sentence is not recorded
  assert.deepEqual(spillFilesNamedIn(`The previous output was saved to ${file} earlier.`, temp), []);
  assert.deepEqual(spillFilesNamedIn(`see ${file}`, temp), []);
  assert.deepEqual(spillFilesNamedIn("", temp), []);
});

test("L1 R2: a spill file this session's result named becomes readable by view and grep -- nothing else", async (t) => {
  const { root, wf, env } = await fixture(t);
  const { hooks, state } = hooksFor("intake", wf, env);
  const file = await freshSpill(t);
  const pre = (toolName: string, toolArgs: unknown) => hooks.onPreToolUse!({ ...base(root), toolName, toolArgs }, invocation);

  // before the result names it, the file is outside the repository like any other
  assert.equal(((await pre("view", { path: file })) as any).permissionDecision, "deny");

  await hooks.onPostToolUse!(post(root, truncated(file)), invocation);
  assert.equal(state.spillFiles.size, 1);
  assert.deepEqual(await pre("view", { path: file }), { permissionDecision: "allow" });
  assert.deepEqual(await pre("view", { path: file, view_range: [1, 200] }), { permissionDecision: "allow" });
  assert.deepEqual(await pre("grep", { pattern: "wf_0001", paths: [file] }), { permissionDecision: "allow" });

  // every write, and the shell, on the recorded file stays denied; a write there -- through a write tool
  // (Task L6, R2) or a shell command (L6 fix round 2, I5) -- is outside the repository, which is severe
  const actsBefore = state.actDenials.length;
  const severeBefore = state.severeDenials.length;
  for (const [toolName, toolArgs] of [
    ["create", { path: file, file_text: "x" }],
    ["edit", { path: file, old_str: "a", new_str: "b" }],
    ["powershell", { command: `Remove-Item ${file}` }],
  ] as [string, unknown][]) {
    assert.equal(((await pre(toolName, toolArgs)) as any).permissionDecision, "deny", toolName);
  }
  assert.equal(state.actDenials.length, actsBefore);
  assert.equal(state.severeDenials.length, severeBefore + 3);

  // grep with one recorded and one unrecorded path is denied
  const unrecorded = spillIn(os.tmpdir(), "1790163499999-copilot-tool-output-4242-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.txt");
  assert.equal(((await pre("grep", { pattern: "x", paths: [file, unrecorded] })) as any).permissionDecision, "deny");
});

test("L1 R2: another session's spill file is not this session's to read", async (t) => {
  const { root, wf, env } = await fixture(t);
  const file = await freshSpill(t);
  const first = hooksFor("intake", wf, env);
  await first.hooks.onPostToolUse!(post(root, tooLarge(file)), invocation);
  assert.deepEqual(
    await first.hooks.onPreToolUse!({ ...base(root), toolName: "view", toolArgs: { path: file } }, invocation),
    { permissionDecision: "allow" },
  );
  const second = hooksFor("intake", wf, env);
  const refused = (await second.hooks.onPreToolUse!({ ...base(root), toolName: "view", toolArgs: { path: file } }, invocation)) as any;
  assert.equal(refused.permissionDecision, "deny");
  assert.match(refused.permissionDecisionReason, /outside the repository/);
  assert.deepEqual(second.state.readDenials.length, 1);
});

test("L1 R2: a forged path in the SDK's sentence is never recorded, and stays unreadable", async (t) => {
  const { root, wf, env } = await fixture(t);
  const { hooks, state } = hooksFor("intake", wf, env);
  const outside = spillIn(path.join(path.parse(os.tmpdir()).root, "not-temp"));
  const notSpill = path.join(os.tmpdir(), "notes.txt");
  await hooks.onPostToolUse!(post(root, truncated(outside)), invocation);
  await hooks.onPostToolUse!(post(root, truncated(notSpill)), invocation);
  assert.equal(state.spillFiles.size, 0);
  for (const target of [outside, notSpill]) {
    const refused = (await hooks.onPreToolUse!({ ...base(root), toolName: "view", toolArgs: { path: target } }, invocation)) as any;
    assert.equal(refused.permissionDecision, "deny", target);
  }
});

test("L1 R3: onPreToolUse records each denial with its class and hands the SDK only its own two fields", async (t) => {
  const { root, wf, env } = await fixture(t);
  const { hooks, state } = hooksFor("intake", wf, env);
  const pre = (toolName: string, toolArgs: unknown) => hooks.onPreToolUse!({ ...base(root), toolName, toolArgs }, invocation);

  const severe = (await pre("view", { path: "workflows/wf_0002/manifest.json" })) as any;
  assert.deepEqual(Object.keys(severe).sort(), ["permissionDecision", "permissionDecisionReason"]);
  await pre("powershell", { command: "Get-ChildItem -Recurse -File | Select-Object FullName" });
  await pre("powershell", { command: "New-Item -ItemType Directory -Path workflows\\wf_0001\\notes" });
  await pre("create", { path: "cookbook/x.md", file_text: "x" });
  await pre("view", { path: "workflows/wf_0001/manifest.json" }); // allowed: no denial

  // Task L6 (R2): another workflow, named, and a write outside the own workflow are severe
  assert.equal(state.readDenials.length, 1);
  assert.equal(state.actDenials.length, 1);
  assert.equal(state.severeDenials.length, 2);
  assert.equal(state.denials.length, 4, "every denial is still listed, in order");
  assert.match(state.severeDenials[0], /^view: no access to other workflows/);
  assert.match(state.severeDenials[1], /^create: cookbook\/ is read-only/);
  assert.match(state.readDenials[0], /^powershell: /);
  assert.match(state.actDenials[0], /^powershell: /);
  // nothing about the audit changes: every call, allowed or denied, has its pre line, and a denied
  // call's line names its class
  const lines = await auditLines(root);
  assert.deepEqual(lines.map((line) => line.decision), ["deny", "deny", "deny", "deny", "allow"]);
  assert.deepEqual(lines.map((line) => line.class), ["severe", "read", "act", "severe", undefined]);
});

test("L1 R3: recordMetrics sums read, act and severe denials over sessions, like toolCalls", async (t) => {
  const { root, wf, env } = await fixture(t);
  for (const session of [1, 2]) {
    const { hooks } = hooksFor("intake", wf, env);
    await hooks.onPreToolUse!({ ...base(root), toolName: "view", toolArgs: { path: "C:/elsewhere/x.md" } }, invocation);
    if (session === 2) {
      // an act (inside the own workflow, outside intake's lane) and, Task L6, a severe one (outside it)
      await hooks.onPreToolUse!({ ...base(root), toolName: "create", toolArgs: { path: "workflows/wf_0001/segments/seg_01/contract.json" } }, invocation);
      await hooks.onPreToolUse!({ ...base(root), toolName: "create", toolArgs: { path: "cookbook/x.md" } }, invocation);
    }
    await hooks.onSessionEnd!({ ...base(root), reason: "complete" }, invocation);
  }
  assert.equal(wf.metrics.intake.readDenials, 2);
  assert.equal(wf.metrics.intake.actDenials, 1);
  assert.equal(wf.metrics.intake.severeDenials, 1);
  assert.equal(wf.metrics.intake.toolCalls, 4);
  const saved = await loadManifest(root, "wf_0001");
  assert.equal(saved.metrics.intake.readDenials, 2);
  assert.equal(saved.metrics.intake.actDenials, 1);
  assert.equal(saved.metrics.intake.severeDenials, 1);
});

// ---------- Task L1, fix round 1 ----------

test("L1 fix 1 (M1): a spill path is recorded only in its canonical spelling, whitespace untrimmed", () => {
  const temp = os.tmpdir();
  const file = spillIn(temp);
  // a path that only resolves to the spill file is not the path the SDK names
  assert.deepEqual(spillFilesNamedIn(truncated(path.join(temp, "sub") + path.sep + ".." + path.sep + SPILL_NAME), temp), []);
  // Unicode whitespace after the name is part of the name, which is then not a spill name
  assert.deepEqual(spillFilesNamedIn(truncated(`${file}\u00a0`), temp), []);
  assert.deepEqual(spillFilesNamedIn(tooLarge(`${file}\u2028`), temp), []);
  // blanks around the path inside the SDK's own sentence are still dropped
  assert.deepEqual(spillFilesNamedIn(`[Truncated \u2014 full output (1 KB) temporarily saved to  ${file} ]`, temp), [path.resolve(file)]);
});

test("L1 fix 1 (M5): a spill path is recorded only if the file exists and is no older than the session", async (t) => {
  const started = Date.now();
  const fresh = await freshSpill(t);
  assert.deepEqual(await recordableSpillFiles(truncated(fresh), started), [path.resolve(fresh)]);
  // a path quoted from an older file: an earlier session's spill, copied into a notes file
  const older = await freshSpill(t);
  const anHourAgo = new Date(started - 3_600_000);
  await utimes(older, anHourAgo, anHourAgo);
  assert.deepEqual(await recordableSpillFiles(truncated(older), started), []);
  // just inside the clock tolerance is still this session's
  const skewed = await freshSpill(t);
  const withinTolerance = new Date(started - SPILL_CLOCK_TOLERANCE_MS + 500);
  await utimes(skewed, withinTolerance, withinTolerance);
  assert.deepEqual(await recordableSpillFiles(tooLarge(skewed), started), [path.resolve(skewed)]);
  // a spill-named path that does not exist
  assert.deepEqual(await recordableSpillFiles(truncated(spillIn(os.tmpdir(), `1-copilot-tool-output-0-${randomUUID()}.txt`)), started), []);
});

test("L1 fix 1 (M5): through the hooks, a result quoting an older spill file makes nothing readable", async (t) => {
  const { root, wf, env } = await fixture(t);
  const older = await freshSpill(t);
  const anHourAgo = new Date(Date.now() - 3_600_000);
  await utimes(older, anHourAgo, anHourAgo);
  const { hooks, state } = hooksFor("intake", wf, env);
  // e.g. a `view` of the agent's own notes file, into which it copied an earlier session's sentence
  await hooks.onPostToolUse!(post(root, `# notes
${truncated(older)}`, "view"), invocation);
  assert.equal(state.spillFiles.size, 0);
  const refused = (await hooks.onPreToolUse!({ ...base(root), toolName: "view", toolArgs: { path: older } }, invocation)) as any;
  assert.equal(refused.permissionDecision, "deny");
});

test("L1 fix 1 (M9): a denied call's pre line carries its reason, redacted and bounded, and its class", async (t) => {
  const { root, wf, env } = await fixture(t);
  const { hooks } = hooksFor("intake", wf, env);
  const pre = (toolName: string, toolArgs: unknown) => hooks.onPreToolUse!({ ...base(root), toolName, toolArgs }, invocation);
  await pre("view", { path: "workflows/wf_0001/manifest.json" });
  await pre("view", { path: "workflows/wf_0002/manifest.json" });
  await pre("powershell", { command: `python scripts/segment.py wf_0001 --token=sk-live-9 ${"x".repeat(900)}` });
  await pre("view", { path: "C:/elsewhere/x.md" });
  const [allowed, severe, act, read] = await auditLines(root);
  assert.equal(allowed.decision, "allow");
  assert.equal("reason" in allowed || "class" in allowed, false, "an allowed call's line is unchanged");
  assert.equal(severe.decision, "deny");
  assert.equal(severe.class, "severe", "Task L6 (R2): a call whose reason is another workflow is severe");
  assert.match(severe.reason, /no access to other workflows \(wf_0002\)/);
  assert.equal(read.class, "read");
  assert.match(read.reason, /outside the repository/);
  assert.equal(act.class, "act");
  assert.doesNotMatch(act.reason, /sk-live-9/, "the reason is redacted like the arguments");
  assert.ok(act.reason.length <= AUDIT_ARG_LIMIT, `bounded: ${act.reason.length}`);
});

// ---------- live hardening, Task L9 fix round 2 (L9-m4): outputFolders holds ids to ID_PATTERN ----------

test("L9-m4: outputFolders skips a segment id that fails ID_PATTERN, and names it in skipped", () => {
  const bad = outputFolders("translator", { segment: "../../etc" });
  assert.deepEqual(bad.folders, [], "no folder built from the bad id");
  assert.deepEqual(bad.skipped, ["../../etc"]);

  const good = outputFolders("translator", { segment: "seg_01" });
  assert.deepEqual(good.folders, ["segments/seg_01"]);
  assert.deepEqual(good.skipped, []);
});

test("L9-m4: outputFolders skips only the bad ids in a batch, keeping the rest", () => {
  const result = outputFolders("analyzer", { batch: { id: "batch_01", segments: ["seg_01", "../evil", "seg_02"] } });
  assert.deepEqual(result.folders, ["notes", "analysis", "segments/seg_01", "segments/seg_02"]);
  assert.deepEqual(result.skipped, ["../evil"]);
});
