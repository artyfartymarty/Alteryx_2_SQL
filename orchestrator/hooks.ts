// Session hooks: the permission layer, the audit trail and the per-role metrics.
// Every hook is audit-first: a line lands in workflows/<id>/audit.jsonl before any
// decision is returned, so a denied or crashed session still leaves a trail.
import { appendFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { inspect } from "node:util";
import type { SessionHooks } from "@github/copilot-sdk";
import { decide, SANDBOX_SCHEMAS, UNRECOGNIZED_TOOL } from "./policy.ts";
import { saveManifest, wfDir } from "./manifest.ts";
import type { AnalyzerBatch, Env, Manifest, Role } from "./types.ts";

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

/** What the runner needs to know about a finished session that the result text cannot say. */
export interface HookState {
  toolCalls: number;
  denied: boolean;
  denials: string[];
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
 */
export function recordMetrics(wf: Manifest, role: Role, state: HookState, ms: number): void {
  if (state.metricsRecorded) return;
  state.metricsRecorded = true;
  const previous = wf.metrics[role] as { toolCalls?: unknown; compactions?: unknown; peakInputTokens?: unknown } | undefined;
  const priorCalls = typeof previous?.toolCalls === "number" ? previous.toolCalls : 0;
  const priorCompactions = typeof previous?.compactions === "number" ? previous.compactions : 0;
  const priorPeak = typeof previous?.peakInputTokens === "number" ? previous.peakInputTokens : 0;
  wf.metrics[role] = {
    ...(previous ?? {}),
    lastMs: ms,
    toolCalls: priorCalls + state.toolCalls,
    compactions: priorCompactions + state.compactions,
    peakInputTokens: Math.max(priorPeak, state.peakInputTokens),
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
    denied: false,
    denials: [],
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
      });
      await audit({ ev: "pre", tool: input.toolName, args: auditArgs(input.toolArgs), decision: decision.permissionDecision });
      if (decision.permissionDecision === "deny") {
        state.denied = true;
        state.denials.push(`${input.toolName}: ${decision.permissionDecisionReason}`);
        // Default denies are how Task 16 learns this build's real tool names.
        if (decision.permissionDecisionReason.startsWith(UNRECOGNIZED_TOOL)) {
          await audit({ ev: "unrecognized-tool", tool: input.toolName });
        }
      }
      return decision;
    },

    onPostToolUse: async (input) => {
      const text = input.toolResult?.textResultForLlm ?? "";
      await audit({ ev: "post", tool: input.toolName, bytes: text.length, result: input.toolResult?.resultType });
      if (SECRET_MENTION.test(text)) await audit({ ev: "secret-in-result", tool: input.toolName });
      if (text.length > LARGE_RESULT_BYTES) await audit({ ev: "large-result", tool: input.toolName, bytes: text.length });
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
