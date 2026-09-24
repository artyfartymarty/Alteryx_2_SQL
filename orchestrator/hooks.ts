// Session hooks: the permission layer, the audit trail and the per-role metrics.
// Every hook is audit-first: a line lands in workflows/<id>/audit.jsonl before any
// decision is returned, so a denied or crashed session still leaves a trail.
import { appendFile, mkdir, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { inspect } from "node:util";
import type { SessionHooks } from "@github/copilot-sdk";
import { decide, ID_PATTERN, SANDBOX_SCHEMAS, SPILL_FILE_NAME, spillFileKey, UNRECOGNIZED_TOOL } from "./policy.ts";
import { saveManifest, wfDir } from "./manifest.ts";
import type { AgentCtx, AnalyzerBatch, Env, Manifest, Role } from "./types.ts";

/** Audit lines are truncated and scrubbed: they are read by humans and kept in the repo. */
export const AUDIT_ARG_LIMIT = 500;
export const LARGE_RESULT_BYTES = 200_000;
// Tool arguments arrive as JSON, so the key is usually quoted (`"token":"…"`): the optional
// quotes here are what make these patterns fire on real audit input and not just on `token=…`.
export const SECRET_ASSIGNMENT = /(password|pwd|token|secret|apikey|api_key)"?\s*[=:]\s*"?[^\s",}\]]+"?/gi;
export const SECRET_MENTION = /(password|pwd|token|secret)"?\s*[=:]/i;
// `429` must be a standalone number, never a substring of a token count such as the
// context-overflow messages just below: "request (42901 tokens) exceeds the available context
// size", "(14290 tokens)" and `"prompt_tokens":42900` all contain the digits "429" but must not
// be misclassified as a rate limit (F7, final review). The lookaround anchors are digit-only, so
// a real "429 Too Many Requests" / "status: 429" still matches.
export const RATE_LIMIT = /(?<!\d)429(?!\d)|rate.?limit|quota/i;
// The real text observed live (task-16-report.md, ATTEMPT 1/2, the local llama.cpp BYOK
// server): "400 request (34965 tokens) exceeds the available context size (32768 tokens), try
// increasing it" / "request (35518 tokens) exceeds the available context size (32768 tokens),
// try increasing it". The second and third alternatives are defensive coverage for other
// providers' phrasing (untested live, unlike the first).
export const CONTEXT_OVERFLOW =
  /exceeds?\s+the\s+available\s+context\s+(size|length|window)|context\s+(length|window)\s+exceeded|maximum\s+context\s+length/i;

/** Task L1 (R3): how many READ denials one session may collect before it parks as `denied`
 * (`budgets.maxReadDenialsPerSession`, this value by default). */
export const MAX_READ_DENIALS_PER_SESSION = 20;

/** Task L6 (R2): how many attempted actions that are not severe (`act` denials) one session may collect
 * before it parks as `denied` (`budgets.maxActDenialsPerSession`, this value by default). A SEVERE
 * denial parks the session at once. L6 fix round 1: 20, the same as reads (it was 3) -- the budgets catch
 * a session that thrashes; whether its output is good is for the stage's deterministic checks to say. A
 * live translator whose SQL passed all four golden sets made 33 blocked attempts. */
export const MAX_ACT_DENIALS_PER_SESSION = 20;

/**
 * Task L1 (R2): the two sentences the SDK runtime (`src/runtime/src/tools/large_output.rs` in
 * `@github/copilot-sdk-win32-x64`'s `runtime.node`) writes into a tool result when it spills the
 * full output to a temporary file: `[Truncated — full output (…) temporarily saved to <path>]` and
 * `… too large to read at once (…). Saved to: <path>`. Only a path in one of these two sentences is
 * ever recorded; a path the result mentions in any other sentence is not.
 */
export const SPILL_MESSAGES: RegExp[] = [
  /\[Truncated\s*[—–-]{1,2}\s*full output \([^()\r\n]*\) temporarily saved to ([^\]\r\n]+)\]/g,
  /too large to read at once \([^()\r\n]*\)\. Saved to: ([^\r\n]+)/g,
];

/**
 * The spill files a tool result names (R2): a path in one of `SPILL_MESSAGES`' two sentences that
 * is absolute, resolves inside the OS temp directory (`tmpdir`, `os.tmpdir()` by default) and whose
 * base name is the SDK's spill name (`SPILL_FILE_NAME`). Anything else -- a path outside the temp
 * directory, a temp file with another name, a relative path -- is dropped. Task L1 fix round 1
 * (M1): the path must also already be canonical (resolving it changes nothing but separators), and
 * only blanks and tabs around it are dropped, never other Unicode whitespace, so the recorded key
 * is exactly what the result named. Returned resolved.
 */
export function spillFilesNamedIn(text: string, tmpdir: string = os.tmpdir()): string[] {
  const found: string[] = [];
  const temp = path.resolve(tmpdir);
  for (const pattern of SPILL_MESSAGES) {
    for (const match of String(text ?? "").matchAll(pattern)) {
      const named = match[1].replace(/^[ \t]+|[ \t]+$/g, "").replace(/\.$/, "");
      if (!path.isAbsolute(named)) continue;
      const resolved = path.resolve(named);
      if (resolved.replace(/\\/g, "/") !== named.replace(/\\/g, "/")) continue;
      const relative = path.relative(temp, resolved);
      if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) continue;
      if (!SPILL_FILE_NAME.test(path.basename(resolved))) continue;
      found.push(resolved);
    }
  }
  return found;
}

/** How far a spill file's modification time may lie before its session's start (clock skew). */
export const SPILL_CLOCK_TOLERANCE_MS = 2_000;

/**
 * The spill files a result names that THIS session can have produced (Task L1 fix round 1, M5):
 * `spillFilesNamedIn`'s candidates that exist as files and were last modified no earlier than the
 * session's start (less `SPILL_CLOCK_TOLERANCE_MS`). A result that merely quotes the name of an older
 * file -- an earlier session's spill, copied into a notes file -- records nothing.
 */
export async function recordableSpillFiles(text: string, sessionStartMs: number, tmpdir: string = os.tmpdir()): Promise<string[]> {
  const recordable: string[] = [];
  for (const candidate of spillFilesNamedIn(text, tmpdir)) {
    const info = await stat(candidate).catch(() => undefined);
    if (info?.isFile() && info.mtimeMs >= sessionStartMs - SPILL_CLOCK_TOLERANCE_MS) recordable.push(candidate);
  }
  return recordable;
}

/** What the runner needs to know about a finished session that the result text cannot say. */
export interface HookState {
  toolCalls: number;
  /** Every denial, in order, as `<tool>: <reason>`. */
  denials: string[];
  /** Task L1 (R3): the denials whose call could only have read (`denialClass` `read`). */
  readDenials: string[];
  /** Task L1 (R3): the attempted actions (`denialClass` `act`). Since Task L6 (R2) they are budgeted. */
  actDenials: string[];
  /** Task L6 (R2): the attempted actions that reached outside the workflow or the sandbox, or tried to do
   * damage (`denialClass` `severe`). Any one of them parks the session. */
  severeDenials: string[];
  /** Task L1 (R2): `spillFileKey` keys of the spill files THIS session's tool results named. */
  spillFiles: Set<string>;
  rateLimited: boolean;
  /** Set from `onErrorOccurred`, mirroring `rateLimited`: the SDK's own error-reporting hook may
   * see the context-overflow signature (e.g. in an `onPostToolUseFailure`-style message) even
   * when the final error `CopilotRunner.run`'s catch block receives from `sendAndWait` itself
   * does not carry the same text. Not verified live either way — see task-16-diag-report.md. */
  contextOverflow: boolean;
  errors: string[];
  /** Guards `recordMetrics` so a session whose `onSessionEnd` fires AND is also finalized by
   * `CopilotRunner.run`'s own `finally` block (belt-and-braces for the crash path, where
   * `onSessionEnd` was observed live not to fire at all) is never counted twice. */
  metricsRecorded: boolean;
  /** Task W4: how many `session.compaction_complete` events this session saw with `data.success
   * === true`. A failed compaction is logged (`CopilotRunner.run`) but never counted here. */
  compactions: number;
  /** Task W4: the highest `assistant.usage` `data.inputTokens` this session saw, 0 if none
   * arrived. `recordMetrics` keeps the maximum across every session of the same role. */
  peakInputTokens: number;
}

/** The three roles that keep a running notes file (Task W4): long stretches of tool calls can
 * outlast what a compacted context still remembers, so intake, analyzer and fixer each write
 * their own `workflows/<wf>/notes/<role>.md` as they go. The notes are a re-read aid, never the
 * durable record -- that stays in the contract and the files each role writes. */
export const NOTES_ROLES: Role[] = ["intake", "analyzer", "fixer"];

/** Where a notes-keeping role's own notes file lives -- the one extra lane `policy.ts` opens for
 * it, and the path both the task text (`orchestrator/stages.ts`) and the post-compaction reminder
 * below name. Task N1: `CopilotRunner.run` creates this file's parent directory (recursive,
 * idempotent) before the session starts, since no role's policy lane allows creating a directory
 * itself -- so the task text can truthfully say the directory already exists. */
export const notesPath = (wfId: string, role: Role): string => `workflows/${wfId}/notes/${role}.md`;

/** The one short follow-up `CopilotRunner.run` sends (`mode: "immediate"`, never `"enqueue"`)
 * right after every successful compaction of a notes-keeping role's session. Fixed text naming
 * only the notes path -- it never includes anything the workflow itself wrote, so a compacted
 * session cannot be steered by data instead of the orchestrator. */
export const notesReminder = (role: Role, wfId: string): string =>
  `Your context was just compacted. Re-read ${notesPath(wfId, role)} (your decisions and open items) ` +
  `and the files you have already written before continuing; they are the record, not your memory.`;

/**
 * Live hardening, Task L9 (R2), generalising the notes folder above (Task N1) into every role's
 * output folders: the workflow-relative folders THIS session's task will write into, built only
 * from the role and the fixed ids `ctx` carries (a segment, a batch's segments, the dbt scope) --
 * never from task text or a directory listing. `CopilotRunner.run` creates each of them (`mkdir`,
 * recursive) before `createSession`, since no role's policy lane allows creating a directory
 * itself. Live evidence (task-L9-brief.md): a documenter session parked trying `mkdir
 * workflows/<wf>/docs` and `New-Item -Path workflows/<wf>/docs -ItemType Directory` itself, because
 * `docs/` did not exist yet -- exactly the problem Task N1 solved for the three notes-keeping roles
 * alone.
 *
 * - `intake`: `intake/`.
 * - `analyzer`: nothing for a whole-workflow call (`scripts/segment.py` already wrote every
 *   `segments/<seg>/` before any analyzer session runs); for one call of a BATCHED analyzer
 *   (`ctx.batch`, Task W2) its own segments' `segments/<seg>/`, plus `analysis/` for its two
 *   fragments.
 * - `translator` / `fixer`: `segments/<seg>/` for a procedures segment (`ctx.segment`), or
 *   `dbt/models/` for a dbt workflow's whole-project scope (`ctx.dbt`) -- `dbt/models/` also creates
 *   its parent `dbt/`, where the rest of the dbt lane's files live.
 * - `documenter`: `docs/`. Never `procs/`: `policy.ts`'s `writeLanes` gives the documenter no lane
 *   there at all (it only ever READS `procs/master.sql` / `procs/README.md`, written by
 *   `stages.ts` itself, never by the documenter's own session), so pre-creating it would offer a
 *   folder the policy can never let this role write into.
 * - every notes-keeping role (`NOTES_ROLES`): `notes/`, exactly as Task N1 already did -- folded
 *   into this one list instead of its own separate `mkdir` call.
 *
 * `reviewer`, `validator` and `parser-recovery` get nothing here: their write lanes are single
 * files inside folders another role's session (or an earlier script) already created.
 *
 * Live hardening, Task L9 fix round 2 (L9-m4): `ctx.segment` and `ctx.batch.segments` are held to
 * `ID_PATTERN` -- the same check `policy.ts`'s `writeLanes`/`analyzerBatchLanes` already apply to
 * the same ids -- before either is used to build a path. Today these ids only ever come from
 * `scripts/segment.py`/`plan_batches.py`, which no agent lane can write, so an agent cannot reach
 * this; it is defence in depth, matching the brief's "built only from … fixed segments and ids". An
 * id that fails the check is left out of `folders` (so `CopilotRunner.run` never `mkdir`s it) and
 * named in `skipped`, so the caller can log it instead of silently doing nothing.
 */
export function outputFolders(
  role: Role,
  ctx: Pick<AgentCtx, "segment" | "dbt" | "batch"> = {},
): { folders: string[]; skipped: string[] } {
  const folders: string[] = [];
  const skipped: string[] = [];
  const segmentFolder = (segment: string): void => {
    if (ID_PATTERN.test(segment)) folders.push(`segments/${segment}`);
    else skipped.push(segment);
  };
  if (NOTES_ROLES.includes(role)) folders.push("notes");
  switch (role) {
    case "intake":
      folders.push("intake");
      break;
    case "analyzer":
      if (ctx.batch) {
        folders.push("analysis");
        for (const segment of ctx.batch.segments) segmentFolder(segment);
      }
      break;
    case "translator":
    case "fixer":
      if (ctx.dbt) folders.push("dbt/models");
      else if (ctx.segment) segmentFolder(ctx.segment);
      break;
    case "documenter":
      folders.push("docs");
      break;
    default:
      break;
  }
  return { folders, skipped };
}

export function redact(text: string): string {
  return text.replace(SECRET_ASSIGNMENT, "<redacted>");
}

/** Redact first, then truncate, so a cut-off secret cannot survive the truncation. */
export function auditArgs(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value ?? {});
  return redact(text ?? "").slice(0, AUDIT_ARG_LIMIT);
}

/**
 * A session error from the SDK can arrive as a string, an `Error`, or an arbitrary object: the
 * SDK's own `ErrorOccurredHookInput.error` (and `PostToolUseFailureHookInput.error`) are typed
 * `string`, but live evidence (task-16-report.md, ATTEMPT 1) showed `onErrorOccurred`'s
 * `input.error` was a plain, non-`Error` object at runtime — `String(value)` on that collapsed to
 * the useless `"[object Object]"`, and the real diagnostic text had to be recovered from
 * `llama-server`'s own independent log instead. This extracts real text robustly: an object's
 * own `.message` first (covers both a real `Error` instance and a plain `{message, ...}` shape
 * that crossed an RPC boundary and lost its prototype), else a `JSON.stringify` of the whole
 * value, else — for a value `JSON.stringify` itself cannot handle, such as a cyclic object —
 * `util.inspect`. Whitespace (including any embedded newlines) is collapsed to single spaces so
 * the caller's audit line stays exactly one JSON value per line; the caller still applies
 * `redact` and the `AUDIT_ARG_LIMIT` truncation, same as any other audited text.
 */
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

/**
 * Record this session's duration and tool-call count into `wf.metrics[role]`. `toolCalls` is
 * additive on whatever was already there, so a crashed or retried session's spend is never lost
 * from (or overwritten out of) the workflow's cumulative tool-call budget —
 * `orchestrator/stages.ts`'s `toolCallsUsed` sums every role's `toolCalls` to enforce
 * `budgets.maxToolCallsPerWorkflow`, and a second attempt (whether a retry within one run or a
 * fresh `--from-stage` invocation reloading a stale on-disk manifest) must add to that total, not
 * replace it. `lastMs` is deliberately NOT additive — it names the most recent session's own
 * duration. Idempotent per session via `state.metricsRecorded`: both `onSessionEnd` and
 * `CopilotRunner.run`'s own `finally` block call this, and whichever runs first wins — the other
 * is a no-op, so a session is never double-counted.
 *
 * Task W4 adds two more fields, on the same two rules: `compactions` accumulates like
 * `toolCalls` (every session's count adds to the workflow's running total), and
 * `peakInputTokens` keeps the MAXIMUM across every session of this role, like a high-water mark
 * rather than a sum -- a later, smaller session must never lower it.
 *
 * Task L1 (R3) adds `readDenials` and `actDenials`, summed like `toolCalls`: every session's
 * blocked reads and attempted actions add to the role's running totals (and `reloadManifest`
 * keeps the higher of disk and memory for both, as it does for `toolCalls`). Task L6 (R2) adds
 * `severeDenials` on the same rules.
 */
export function recordMetrics(wf: Manifest, role: Role, state: HookState, ms: number): void {
  if (state.metricsRecorded) return;
  state.metricsRecorded = true;
  const previous = wf.metrics[role] as Record<string, unknown> | undefined;
  const prior = (field: string): number => (typeof previous?.[field] === "number" ? (previous[field] as number) : 0);
  wf.metrics[role] = {
    ...(previous ?? {}),
    lastMs: ms,
    toolCalls: prior("toolCalls") + state.toolCalls,
    compactions: prior("compactions") + state.compactions,
    peakInputTokens: Math.max(prior("peakInputTokens"), state.peakInputTokens),
    readDenials: prior("readDenials") + state.readDenials.length,
    actDenials: prior("actDenials") + state.actDenials.length,
    severeDenials: prior("severeDenials") + state.severeDenials.length,
  };
}

/** `dbt` is a dbt workflow's whole-project scope (output-targets design §6): the policy judges the
 * session's writes against the dbt lanes, and every audit line records `"dbt": true`. `batch` is
 * one call of a batched analyzer (Task W2): the policy narrows the analyzer's lanes to that batch,
 * and every audit line records `"batch": "<id>"`. */
export function hooksFor(
  role: Role,
  wf: Manifest,
  env: Env,
  segment?: string,
  dbt?: boolean,
  batch?: AnalyzerBatch,
): { hooks: SessionHooks; state: HookState } {
  const auditFile = wfDir(env.root, wf.id, "audit.jsonl");
  const state: HookState = {
    toolCalls: 0,
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
  const started = Date.now();

  const audit = async (record: Record<string, unknown>): Promise<void> => {
    const line = JSON.stringify({
      t: new Date().toISOString(),
      role,
      segment,
      ...(dbt ? { dbt: true } : {}),
      ...(batch ? { batch: batch.id } : {}),
      ...record,
    });
    try {
      await mkdir(path.dirname(auditFile), { recursive: true });
      await appendFile(auditFile, `${line}\n`, "utf8");
    } catch (error) {
      env.log(`audit write failed for ${wf.id}: ${String(error)}`);
    }
  };

  const hooks: SessionHooks = {
    onSessionStart: async () => ({
      additionalContext: [
        `Workflow ${wf.id}. Manifest: workflows/${wf.id}/manifest.json. Tier: ${wf.tier ?? "unknown"}.`,
        `Program answers: mappings/global.yaml. Cookbook index: cookbook/index.md.`,
        `Sandbox schemas: ${SANDBOX_SCHEMAS.join(", ")}. Nothing is deployed from this session; output is files + a PR.`,
        ...(batch ? [`Batch ${batch.id}: segments ${batch.segments.join(", ")} only.`] : []),
      ].join("\n"),
    }),

    onPreToolUse: async (input) => {
      state.toolCalls += 1;
      const decision = decide(role, wf.id, input.toolName, input.toolArgs, segment, env.root, {
        sandboxDatabases: env.config?.policy?.sandboxDatabases,
        dbtProject: dbt,
        analyzerBatch: batch,
        readableSpillFiles: state.spillFiles,
      });
      // Task L1 fix round 1 (M9): a denied call's pre line also carries its reason (redacted and
      // bounded like the arguments) and its class (`read`, `act` or, since Task L6, `severe`), so the
      // per-session log line's pointer here leads to both. Still audit-first: the line is written
      // before any state changes or the return.
      await audit({
        ev: "pre",
        tool: input.toolName,
        args: auditArgs(input.toolArgs),
        decision: decision.permissionDecision,
        ...(decision.permissionDecision === "deny"
          ? { reason: redact(decision.permissionDecisionReason).slice(0, AUDIT_ARG_LIMIT), class: decision.denialClass }
          : {}),
      });
      if (decision.permissionDecision === "deny") {
        const denial = `${input.toolName}: ${decision.permissionDecisionReason}`;
        state.denials.push(denial);
        // Task L1 (R3), Task L6 (R2): graded, never softened -- the call is refused either way; the
        // class only decides whether the session may go on (CopilotRunner.run reads these three lists).
        const graded = { read: state.readDenials, act: state.actDenials, severe: state.severeDenials };
        graded[decision.denialClass].push(denial);
        // Default denies are how Task 16 learns this build's real tool names.
        if (decision.permissionDecisionReason.startsWith(UNRECOGNIZED_TOOL)) {
          await audit({ ev: "unrecognized-tool", tool: input.toolName });
        }
        // The SDK gets exactly its own two fields; the class is the orchestrator's.
        return { permissionDecision: "deny", permissionDecisionReason: decision.permissionDecisionReason };
      }
      return decision;
    },

    onPostToolUse: async (input) => {
      const text = input.toolResult?.textResultForLlm ?? "";
      await audit({ ev: "post", tool: input.toolName, bytes: text.length, result: input.toolResult?.resultType });
      if (SECRET_MENTION.test(text)) await audit({ ev: "secret-in-result", tool: input.toolName });
      if (text.length > LARGE_RESULT_BYTES) await audit({ ev: "large-result", tool: input.toolName, bytes: text.length });
      // Task L1 (R2): the SDK spilled this result's full output to a temp file and told the model
      // to open it. That file -- and only a file THIS session's results named, which exists and is
      // no older than this session (fix round 1, M5) -- becomes readable.
      for (const spill of await recordableSpillFiles(text, started)) state.spillFiles.add(spillFileKey(spill));
      return undefined;
    },

    onPostToolUseFailure: async (input) => {
      await audit({ ev: "tool-fail", tool: input.toolName, error: redact(errorText(input.error)).slice(0, AUDIT_ARG_LIMIT) });
      return undefined;
    },

    onErrorOccurred: async (input) => {
      const text = errorText(input.error);
      // Context-overflow is judged FIRST and wins when both signatures are present (F7, final
      // review): an anchored "429" can still legitimately co-occur with an "exceeds the available
      // context size" message from the same provider, and the retry policy for the two is
      // different (RETRY_ONCE never retries context-overflow, but rate-limit backs off and retries
      // up to MAX_RATE_LIMIT_RETRIES times) -- misreading a context overflow as a rate limit would
      // burn three paid retries that can never succeed.
      const contextOverflow = CONTEXT_OVERFLOW.test(text);
      const rateLimited = !contextOverflow && RATE_LIMIT.test(text);
      if (rateLimited) state.rateLimited = true;
      if (contextOverflow) state.contextOverflow = true;
      state.errors.push(text);
      await audit({
        ev: "error",
        error: redact(text).slice(0, AUDIT_ARG_LIMIT),
        context: input.errorContext,
        recoverable: input.recoverable,
        class: rateLimited ? "rate-limit" : contextOverflow ? "context-overflow" : "error",
      });
      return undefined;
    },

    onSessionEnd: async (input) => {
      recordMetrics(wf, role, state, Date.now() - started);
      await audit({ ev: "session-end", reason: input.reason, toolCalls: state.toolCalls });
      await saveManifest(env.root, wf);
      return undefined;
    },
  };

  return { hooks, state };
}
