// The deterministic outer loop: stage order, loops, budgets and error routing.
// docs/spec/01-copilot-setup.md Part B §3 (state machine), §4 (loops and budgets) and the
// error-routing table in §5. Every stage is idempotent: a stage whose status is already
// terminal-good is skipped, so a re-run after a crash or a human answer resumes in place.
// An ESCALATED status (NEEDS_HUMAN, QUARANTINED, golden's BLOCKED) is neither terminal-good nor
// "not started" -- it is PARKED (fix round 1, task-15-int): a plain re-run does not retry that
// stage's scripts or agents, and does not let any later stage of the same workflow run either.
// Only an explicit `--from-stage <stage>` (the existing `clearFromStage`) reopens it. This does
// not apply to WAITING_FOR_ANSWERS, whose whole point is to be re-run every pass so a newly
// merged answer can move intake forward, or to a T3 workflow's MANUAL, which is a normal (if
// permanent) resting state, not an escalation.
import { mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { retryFeedback } from "./feedback.ts";
import { auditArgs, notesPath } from "./hooks.ts";
import { fileExists, loadManifest, readJsonOr, reloadManifest, saveManifest, wfDir } from "./manifest.ts";
import { STAGES } from "./types.ts";
import type {
  AgentCtx,
  AgentError,
  AgentResult,
  AnalyzerBatch,
  Env,
  Manifest,
  OutputKind,
  Role,
  RunOptions,
  SegmentTarget,
  ShResult,
  Stage,
  Tier,
} from "./types.ts";

/** A stage whose status is one of these has nothing left to do. NEEDS_HUMAN / QUARANTINED /
 * BLOCKED are not listed here even though they also stop a plain re-run from retrying the stage
 * -- they are not success, they are PARKED (see `ESCALATED` / `isParked` below). */
export const TERMINAL_GOOD: Record<Stage, string[]> = {
  parse: ["PARSED", "RECOVERED"],
  intake: ["READY"],
  analyze: ["DONE"],
  golden: ["DONE"],
  translate: ["VALIDATED", "MANUAL"],
  document: ["DONE"],
  pr: ["OPEN"],
};

/** Live hardening, Task L8: the one fixed string every stub body of `scripts/translation_scaffold.py`'s
 * skeleton is (`scripts/lib/scaffold.py` `TODO_MARKER`; `tests/test_translation_scaffold.py` pins the two
 * equal). `compile_check.py` refuses a file that still holds it (`scaffold:todo`). */
export const SCAFFOLD_TODO = "TODO(scaffold)";

/** Errors worth one identical retry before escalating (spec §5 routing table). */
const RETRY_ONCE: AgentError[] = ["missing-output", "timeout"];
const MAX_RATE_LIMIT_RETRIES = 3;

type Step = "continue" | "stop";

function reasons(m: Manifest): Record<string, string> {
  m.reasons ??= {};
  return m.reasons;
}

/** Exported so tests can verify the budget sees a role's cumulative spend (every attempt,
 * including a crashed one) exactly as `runAgent` below does, without reimplementing the sum. */
export function toolCallsUsed(m: Manifest): number {
  let total = 0;
  for (const entry of Object.values(m.metrics ?? {})) {
    const calls = (entry as { toolCalls?: unknown } | null)?.toolCalls;
    if (typeof calls === "number") total += calls;
  }
  return total;
}

function escalate(env: Env, m: Manifest, stage: Stage, result: AgentResult): Step {
  // A reason a verify callback already recorded for THIS stage wins over the agent-level one
  // (output-targets design §3.2): a contract that raised a target reaches escalate as a generic
  // "missing-output" (that is how a failed verify is reported to runAgent), and "missing-output"
  // would tell a human nothing about the contradiction that actually stopped the workflow.
  const why = m.reasons?.[stage] ?? (result.detail === "budget" ? "budget" : (result.error ?? "error"));
  m.status[stage] = "NEEDS_HUMAN";
  reasons(m)[stage] = why;
  env.log(`${m.id}: ${stage} → NEEDS_HUMAN (${why})`);
  return "stop";
}

/** Exit 2 from a script is never a domain verdict: it is a broken invocation or a crash. A few
 * callers route a non-2 exit here too, for a script whose failure is never a domain verdict at all
 * (target_check.py, a classifier) -- so the log names the code it actually saw. */
function scriptError(env: Env, m: Manifest, stage: Stage, script: string, result: ShResult): Step {
  m.status[stage] = "NEEDS_HUMAN";
  reasons(m)[stage] = "script-error";
  const tail = `${result.out}\n${result.err}`.trim().split("\n").slice(-3).join(" | ");
  env.log(`${m.id}: ${script} exited ${result.code} — ${tail}`);
  return "stop";
}

function domainFailure(env: Env, m: Manifest, stage: Stage, why: string, result: ShResult): Step {
  m.status[stage] = "NEEDS_HUMAN";
  reasons(m)[stage] = why;
  env.log(`${m.id}: ${stage} → NEEDS_HUMAN (${why}) — ${result.err.trim() || result.out.trim()}`);
  return "stop";
}

/**
 * Task L1 (R4): the fixed session rules every agent task carries, for every role and every form of
 * its task. Live evidence (docs/live-smoke-test.md "Third live test"): nothing told the model how
 * to navigate, so it typed absolute paths (mangling a long run root), listed files with PowerShell
 * and opened a sibling workflow's folder -- each refused, each a tool call spent. A constant: it
 * carries no workflow-authored text (not even the workflow's id; the task names that).
 * `.github/copilot-instructions.md` mirrors the same seven rules.
 *
 * Task L7 (R3) adds the fifth. Live evidence (task-L7-brief.md): a translator whose `validate_segment.py`
 * run failed spent the rest of its session reading 54 files, many of them the pipeline's own `scripts/`
 * source, trying to debug the diff by reading the tool's own implementation -- until its session timed
 * out. `translator.agent.md` / `fixer.agent.md` bound self-validation to one run per session for the
 * same reason (R3's other half); this rule is the general instruction every role gets, since none of
 * them may read this pipeline's own source to debug a difference either.
 *
 * Task L8 (R3) adds the sixth. Live evidence (task-L8-brief.md): a dbt translator read every golden CSV
 * of every set before writing anything and overflowed its context. Reading stays allowed -- this is
 * guidance, and the budgets bound the rest.
 *
 * Task L9 (R3) adds the seventh. Live evidence (task-L9-brief.md): the SDK offered a documenter session
 * its own built-in `web_fetch`, and the model called it -- refused as a severe network attempt, parking
 * the session at once, on a workflow whose translation had already reached VALIDATED. `policy.ts`'s
 * `ALWAYS_EXCLUDED_BUILTIN_TOOLS` (R1) stops the SDK offering it in the first place; this rule is the
 * same fact stated to the model, for a build where the exclusion is not honoured or the tool is offered
 * under another name this codebase has not seen yet.
 */
export const SESSION_RULE_LINES: readonly string[] = [
  "In every tool call use a path relative to the repository root (workflows/<id>/…, where <id> is the workflow " +
    "your task names); never type an absolute path. The one exception is a temporary file that a tool result " +
    "itself says holds that result's full output: read it with view or grep, exactly as named.",
  "List files with glob, read them with view and search them with grep; the shell only runs the commands your " +
    "agent file names, its scripts as `python scripts/<name>.py …`, one command per shell call: `;`, `&&`, `|`, " +
    "redirection and `$` are refused, so read a script's report file with view afterwards. `python` is already the " +
    "project's interpreter: never check it.",
  "Among the workflows, only workflows/<id>/ is yours; never open another workflow's folder.",
  "A refused tool call is final: do not retry it in another form or through another tool. Continue with what is " +
    "allowed, and if you cannot finish without it, say so in your notes (or, if your role keeps none, in the file " +
    "you were asked to write) and stop.",
  "Do not read this pipeline's own scripts/ or orchestrator/ source to debug a difference: the validation report, " +
    "the contract, the cookbook and docs/reference/ are the evidence.",
  "Never read the golden data in bulk: the contract describes every column. When an example helps, read at most one " +
    "golden set's inputs (e.g. normal); the validator compares the rest.",
  "There is no network in these sessions: every page you need is in the repository (`cookbook/`, `docs/reference/`).",
];

export const SESSION_RULES =
  `Session rules, the same for every agent: ${SESSION_RULE_LINES.map((rule, i) => `(${i + 1}) ${rule}`).join(" ")}`;

/** A task in its two parts: the orchestrator's own instructions, and the fenced inline context
 * (`scripts/prompt_context.py`, Task F) that follows them -- data, never instructions. */
interface AgentTask {
  instructions: string;
  context: string;
}

/** The text an agent is sent (Task L1, R4): the instructions, then the session rules, then any
 * inline context -- so the rules are always part of the instructions and never inside the fence. */
export function taskText(task: string | AgentTask): string {
  const { instructions, context } = typeof task === "string" ? { instructions: task, context: "" } : task;
  return `${instructions}\n\n${SESSION_RULES}${context}`;
}

/** What a stage's verify callback may hand `runAgent` besides its verdict: the full report of the
 * check that failed (Task L3, R4), which the retry quotes after the recorded reason. */
type VerifyNote = (report: string) => void;

/** Live hardening, Task L7 (R2): additive per role, on the same rules as `hooks.ts`'s `recordMetrics`
 * (a retried or later attempt's count is never lost) -- how many of this role's sessions ended
 * `timeout` but were kept because the stage's own verify passed anyway. */
function recordKeptTimeout(m: Manifest, role: Role): void {
  const previous = m.metrics[role] as Record<string, unknown> | undefined;
  const prior = typeof previous?.timeouts === "number" ? (previous.timeouts as number) : 0;
  m.metrics[role] = { ...(previous ?? {}), timeouts: prior + 1 };
}

/**
 * Live hardening, Task L7 fix round 2 (IMP-2): whether `before` (a snapshot taken at an attempt's
 * own start, via `snapshot`) still matches `after` byte for byte, file for file -- the same
 * comparison `untouched` (Task L8) makes, extracted so a `runAgent` `freshSince` closure can compare
 * two snapshots it took itself directly, without `untouched`'s own `tree`/`wanted` recompute.
 */
function sameContent(before: Skeleton, after: Skeleton): boolean {
  if (before.size !== after.size) return false;
  for (const [file, text] of before) if (after.get(file) !== text) return false;
  return true;
}

/** Live hardening, Task L7 fix round 2 (IMP-2): the dbt project's OWN work files -- every model
 * under `models/` (`MODEL_FILE`, Task L8) plus the two model YAMLs, `models/sources.yml` and
 * `models/schema.yml` -- exactly the translator's/fixer's lane (`translator.agent.md`, "Your lane"),
 * never `dbt_project.yml`, `profiles.yml`, `README.md`, `translation_notes.md`, `fix_log.md`,
 * `compile_check.json`, `review.json`, `validation*.json` or `logs/`. Review IMP-2: the orchestrator
 * writes `compile_check.json` milliseconds before a fixer that follows a compile failure runs, and a
 * dbt fixer is told to log every iteration to `fix_log.md` -- either one alone used to be enough to
 * make a timed-out session with no real repair look "written this session" under the old, clock-based
 * check (any mtime within a 2 s tolerance of the attempt's start counted, whatever file it was).
 */
const DBT_WORK_FILE = (file: string): boolean => MODEL_FILE(file) || /[\\/]models[\\/](sources|schema)\.yml$/i.test(file);

/**
 * One agent call with the spec's retry policy: back off on a rate limit, retry a missing
 * output or a timeout once, escalate a denied tool at once, and never start an agent once
 * the workflow is over its tool-call budget. (Since Tasks L1 and L6 a session is `denied` only for a
 * severe attempt, or for attempted actions or blocked reads over `budgets.maxActDenialsPerSession` /
 * `budgets.maxReadDenialsPerSession` -- see `CopilotRunner.run`.) Every task is sent through
 * `taskText`, so every role gets the session rules.
 *
 * Task L3 (R4): the one retry after a failed verify (`missing-output`) is told why -- the task gains
 * `feedback.ts`'s fixed sentence and, in a data fence, the reason the attempt recorded (or the
 * agent-level error) and the report the verify callback noted. A first attempt, and a retry after
 * a timeout (no check failed), carry no such block.
 *
 * Task L7 (R2), live evidence (task-L7-brief.md): a translator wrote a valid, compiling `proc.sql`
 * and then spent the rest of its session reading source files instead of stopping, until the SDK's
 * own session timeout -- and the orchestrator threw the compiled procedure away and retried from
 * scratch. A session that ends `timeout` is no longer retried unseen: when the stage gave a `verify`
 * callback, it is run once against what the session actually left on disk; if it passes, the result
 * is accepted as a success (logged, and counted in `recordKeptTimeout`) instead of being retried. If
 * `verify` fails, or the stage gave none, the existing `RETRY_ONCE` path below applies unchanged.
 * A session with a severe denial is never kept: it parks `denied` (L6 fix round 2, I3).
 *
 * Task L7 fix round 1 (R-b, review I2): `verify` alone is existence, and existence can already be
 * true before this attempt ever ran -- a fixer's `verify` is the same `fileExists(proc.sql)` the
 * translator's iteration 0 already satisfied, so a fixer that timed out having changed nothing was
 * still "kept". `freshSince`, when the caller gives one, is an extra gate on the SAME timeout-keep
 * path: it must also say the checked output actually CHANGED during this attempt. Roles with no
 * `freshSince` (every one but the translator/fixer calls in `migrateSegment`/`migrateDbt`) are
 * unchanged -- existence is still their whole check, per the ruling's own scope (fix round 1 review,
 * M4).
 *
 * Task L7 fix round 2 (IMP-2): "changed" is judged by CONTENT, not the clock -- fix round 1's
 * `freshSince(attemptStartedAt)` compared a file's mtime against the attempt's start (with a 2 s
 * tolerance), and housekeeping the orchestrator or the agent itself writes near the same instant
 * (`compile_check.json`, `fix_log.md`) could satisfy it without a single model file changing. Now
 * `freshSince` is called ONCE, before the session runs, to snapshot the stage's own work files; it
 * returns a check function called AFTER, true only if that snapshot no longer matches -- no clock
 * involved at all, so nothing written before or after the attempt (however close in time) can be
 * mistaken for something written during it.
 */
async function runAgent(
  env: Env,
  m: Manifest,
  role: Role,
  stage: Stage,
  task: string | AgentTask,
  ctx: AgentCtx = {},
  verify?: (note: VerifyNote) => Promise<boolean>,
  freshSince?: () => Promise<() => Promise<boolean>>,
): Promise<AgentResult> {
  let text = taskText(task);
  const budget = env.config.budgets.maxToolCallsPerWorkflow;
  let rateLimits = 0;
  let retried = false;
  for (;;) {
    // A park reason describes the LAST attempt (round 2, R2). A failed `verify` is reported to
    // this loop as a generic "missing-output", which `RETRY_ONCE` retries — so the reason that
    // verify recorded belongs to the attempt that has just been abandoned, and leaving it would
    // make `escalate` prefer it over whatever stops attempt 2 (denied, context-overflow, budget).
    // Cleared here, before every attempt, so it can only ever describe the one that ended the
    // stage; the stage-level clears stay, for the reason loaded from disk at the start of a run.
    if (m.reasons) delete m.reasons[stage];
    const used = toolCallsUsed(m);
    if (used > budget) {
      env.log(`${m.id}: tool-call budget exceeded (${used} > ${budget}); not starting ${role}`);
      return { ok: false, error: "error", detail: "budget", toolCalls: 0, ms: 0 };
    }

    // Task L7 fix round 2 (IMP-2): the "before" snapshot is taken before the session runs, so
    // `changed` (below) judges THIS attempt's own edits -- content an earlier iteration or a
    // resumed run already left in place is part of "before" too, so it is never mistaken for work
    // done now.
    const changed = freshSince ? await freshSince() : undefined;
    let report: string | undefined;
    let result = await env.runner.run(role, m, text, ctx);
    // L6 fix round 2 (I3): a session with a severe denial parks `denied` at once, whatever ended it --
    // never verified, kept or retried. (CopilotRunner already reports it so; this holds for any runner.)
    if ((result.severeDenials ?? 0) > 0 && result.error !== "denied") {
      result = { ...result, ok: false, error: "denied", detail: `severe-denials: ${result.severeDenials} (parks at once); ${result.detail ?? ""}` };
    }
    if (result.ok && verify && !(await verify((noted) => { report = noted; }))) {
      result = { ...result, ok: false, error: "missing-output", detail: "the agent wrote no output file" };
    }
    // Task L7 (R2), extended by fix round 1/2 (R-b, IMP-2): a timeout with a `verify` that now
    // passes, AND (when the caller cares) an output that actually CHANGED during this attempt, is
    // kept, not retried -- see the doc comment above. `result.ok` is already false here (the block
    // above only ever turns a success into `missing-output`, never the reverse), so this and the
    // `if (result.ok)` below are mutually exclusive: `verify` runs at most once per attempt either way.
    if (
      !result.ok &&
      result.error === "timeout" &&
      !result.severeDenials &&
      verify &&
      (await verify((noted) => { report = noted; })) &&
      (!changed || (await changed()))
    ) {
      recordKeptTimeout(m, role);
      env.log(`${m.id}: ${role} timed out after writing an output that passes its check — kept`);
      return { ...result, ok: true, error: undefined, detail: undefined };
    }
    if (result.ok) return result;
    env.log(`${m.id}: ${role} ${result.error ?? "error"}${result.detail ? ` — ${result.detail}` : ""}`);

    if (result.error === "rate-limit" && rateLimits < MAX_RATE_LIMIT_RETRIES) {
      await env.sleep(2 ** rateLimits * 1000);
      rateLimits += 1;
      continue;
    }
    if (result.error && RETRY_ONCE.includes(result.error) && !retried) {
      retried = true;
      if (result.error === "missing-output") {
        const why = m.reasons?.[stage] ?? `${result.error}${result.detail ? `: ${result.detail}` : ""}`;
        text = `${taskText(task)}\n\n${retryFeedback(why, report)}`;
      }
      continue;
    }
    return result;
  }
}

async function readOrder(env: Env, id: string): Promise<string[][]> {
  const order = await readJsonOr<string[][]>(wfDir(env.root, id, "segments", "order.json"), []);
  return Array.isArray(order) ? order : [];
}

// ---------- stages ----------

async function stageParse(env: Env, m: Manifest): Promise<Step> {
  const max = env.config.maxParseRecovery;
  const extensions = m.parse?.extensions ?? [];
  let attempts = m.parse?.attempts ?? 0;

  for (let attempt = 0; attempt <= max; attempt++) {
    const parsed = await env.py("scripts/parse.py", [m.id, "--check"]);
    attempts += 1;
    await reloadManifest(env.root, m);
    const report = await readJsonOr<{ extension?: string | null }>(
      wfDir(env.root, m.id, "parsed", "parse_report.json"),
      {},
    );
    if (report.extension && !extensions.includes(report.extension)) extensions.push(report.extension);
    m.parse = { attempts, extensions };

    if (parsed.code === 2) return scriptError(env, m, "parse", "scripts/parse.py", parsed);
    if (parsed.ok) {
      m.status.parse = "PARSED";
      return "continue";
    }
    if (attempt === max) break;

    const recovery = await runAgent(
      env,
      m,
      "parser-recovery",
      "parse",
      `scripts/parse.py failed or violated invariants for ${m.id} (attempt ${attempt + 1} of ${max}). ` +
        `Read workflows/${m.id}/source/ and workflows/${m.id}/parsed/parse_report.json, diagnose the XML variant, ` +
        `add the smallest extension under scripts/parsers/ext/ with a fixture in tests/parser_corpus/, and re-parse.`,
    );
    if (!recovery.ok) return escalate(env, m, "parse", recovery);
  }

  m.status.parse = "QUARANTINED";
  m.tier = "T3";
  env.log(`${m.id}: still unparsable after ${max} recovery attempts → QUARANTINED, tier T3`);
  return "stop";
}

/** Characters of inline context the intake and analyzer tasks carry (≈ 4 000 tokens). Task F
 * (output-targets design §8): two earlier live tests overflowed the local model's context window
 * during intake because the agent had to find `parsed/dag.json` and `intake/touchpoints.json`
 * tool call by tool call -- `scripts/prompt_context.py` renders a compact, budgeted summary of
 * them once, up front, instead. */
export const PROMPT_CONTEXT_CHARS = 16000;

/** Task W4: appended to every form of the intake, analyzer and fixer tasks (batched included),
 * always as part of the INSTRUCTIONS, before any fenced inline context that follows (Task F: the
 * data fence never carries an instruction) -- so the notes path reaches the agent whether or not
 * that call's own context block rendered. */
function notesInstruction(role: Role, wfId: string): string {
  return ` Keep your decisions and open items in ${notesPath(wfId, role)} as you go; the durable ` +
    `record stays in the contract and the files you write. The directory already exists; write ` +
    `the file with your file-writing tool; do not create directories.`;
}

/** Runs `scripts/prompt_context.py --role <role>` and returns the block to append to that role's
 * task text -- `""` on any failure, so a broken renderer degrades the task (no inline context,
 * same as before this existed) rather than stopping the stage. The rendered text is DATA the
 * workflow itself wrote (annotations, tool names, touchpoint keys): it is appended after the
 * task's own instructions, never interpolated into them, so nothing in it can rewrite what the
 * agent is told to do. */
async function inlineContext(env: Env, m: Manifest, role: "intake" | "analyzer"): Promise<string> {
  const rendered = await env.py("scripts/prompt_context.py",
    [m.id, "--role", role, "--budget-chars", String(PROMPT_CONTEXT_CHARS)]);
  if (!rendered.ok) {
    env.log(`${m.id}: prompt_context.py --role ${role} exited ${rendered.code}; the ${role} task goes without inline context`);
    return "";
  }
  const text = rendered.out.trim();
  return text ? `\n\n${text}` : "";
}

async function stageIntake(env: Env, m: Manifest): Promise<Step> {
  const touchpoints = await env.py("scripts/intake_touchpoints.py", [m.id]);
  await reloadManifest(env.root, m);
  if (touchpoints.code === 2) return scriptError(env, m, "intake", "scripts/intake_touchpoints.py", touchpoints);

  // The prompt owns stdin when a human is here; otherwise it records what it cannot ask.
  const prompt = env.interactive
    ? await env.py("scripts/intake_prompt.py", [m.id], { inheritStdio: true })
    : await env.py("scripts/intake_prompt.py", [m.id, "--no-interactive"]);
  await reloadManifest(env.root, m);
  if (prompt.code === 2) return scriptError(env, m, "intake", "scripts/intake_prompt.py", prompt);

  if (!(await fileExists(wfDir(env.root, m.id, "intake", "plan.md")))) {
    const context = await inlineContext(env, m, "intake");
    const result = await runAgent(
      env,
      m,
      "intake",
      "intake",
      {
        instructions:
          `Run intake for ${m.id}. Read workflows/${m.id}/parsed/dag.json, workflows/${m.id}/intake/touchpoints.json ` +
          `and mappings/global.yaml, then write workflows/${m.id}/intake/mappings.yaml, open_questions.md and plan.md.` +
          notesInstruction("intake", m.id),
        context,
      },
      {},
      () => fileExists(wfDir(env.root, m.id, "intake", "plan.md")),
    );
    if (!result.ok) return escalate(env, m, "intake", result);
    await reloadManifest(env.root, m);
  }

  const status = m.status.intake ?? "BLOCKED";
  if (status === "READY") return "continue";

  const questions = wfDir(env.root, m.id, "intake", "open_questions.md");
  if (status === "WAITING_FOR_ANSWERS") {
    const noGh = ghUnavailable(env);
    if (!noGh) {
      const issue = await env.sh("gh", [
        "issue",
        "create",
        "--title",
        `[migration] ${m.id}: answers needed`,
        "--body-file",
        questions,
      ]);
      if (!issue.ok) env.log(`${m.id}: gh issue create failed: ${issue.err.trim()}`);
    } else {
      env.log(`${m.id}: waiting for answers — ${noGh}, answer the boxes in ${questions}`);
    }
    return "stop";
  }

  env.log(`${m.id}: intake ${status} — see ${questions}`);
  return "stop";
}

/** `sql` is the HIGHEST target and `manual` the lowest. The analyzer may only ever move a segment
 * DOWN this ladder (output-targets design §3.2: "It may **lower** a target (`sql` → `snowpark`, or
 * either → `manual`) … it may never raise one"). A contract that moves back towards `sql` claims
 * the deterministic classifier was wrong about, say, a Python tool — a contradiction only a human
 * can settle, so the workflow parks instead of translating against it. */
const TARGET_RANK: Record<string, number> = { manual: 0, snowpark: 1, sql: 2 };

interface TargetVerdict {
  ok: boolean;
  /** Set when `ok` is false: the exact string recorded as `reasons.analyze`. */
  reason?: string;
  /** `targets.json`'s own proposal, kept so the caller can say why a dbt preference was dropped. */
  proposedKind?: string;
  outputKind?: OutputKind;
  /** One line per segment the analyzer legitimately lowered, to log once the stage succeeds. */
  lowered: string[];
}

async function readTargets(
  env: Env,
  m: Manifest,
): Promise<{ output_kind?: string; segments?: Record<string, string> }> {
  return readJsonOr(wfDir(env.root, m.id, "segments", "targets.json"), {});
}

/**
 * The mirror design §3.3 asks for, for a workflow with no contracts to check: `targets.json`'s own
 * `output_kind` verbatim. `target_check.py` writes `targets.json` for a tier-T3 workflow like any
 * other, so the decision it made is on disk — it is only §3.2's lower-only verification (which
 * reads contracts, and can drop a `dbt` proposal when one was lowered off `sql`) that has nothing
 * to run against. An unwritable or out-of-vocabulary kind falls back to `procedures`, the same
 * default `target_check.py` itself resolves to.
 */
async function mirroredKind(env: Env, m: Manifest): Promise<OutputKind> {
  return (await readTargets(env, m)).output_kind === "dbt" ? "dbt" : "procedures";
}

/**
 * The §3.2 check the orchestrator owes the pipeline: every contract carries a `target`, none of
 * them ranks above `targets.json`'s proposal, and the workflow's `output_kind` is `dbt` only if
 * the script proposed dbt AND every contract is still plain `sql` (an analyzer that lowers one
 * segment to Snowpark legitimately makes the whole workflow non-dbt).
 */
async function checkTargets(env: Env, m: Manifest, segments: string[]): Promise<TargetVerdict> {
  const targets = await readTargets(env, m);
  const proposals = targets.segments ?? {};
  const lowered: string[] = [];
  let everyTargetIsSql = true;

  for (const segment of segments) {
    const contract = await readJsonOr<{ target?: string }>(
      wfDir(env.root, m.id, "segments", segment, "contract.json"),
      {},
    );
    const target = contract.target;
    if (typeof target !== "string" || !(target in TARGET_RANK)) {
      return { ok: false, reason: `target-missing: ${segment}`, lowered };
    }
    const proposal = proposals[segment];
    if (typeof proposal !== "string" || !(proposal in TARGET_RANK)) {
      // Nothing was verified, which is the same failure as a contract with no target of its own —
      // and design §3.2 has exactly two reason formats, so the detail goes to the log, not a third.
      env.log(`${m.id}: ${segment} has no target proposal in segments/targets.json — nothing to verify against`);
      return { ok: false, reason: `target-missing: ${segment}`, lowered };
    }
    if (TARGET_RANK[target] > TARGET_RANK[proposal]) {
      return { ok: false, reason: `target-mismatch: ${segment} raised ${proposal} to ${target}`, lowered };
    }
    if (target !== proposal) lowered.push(`${m.id}: ${segment} lowered ${proposal} to ${target} — see analysis.md`);
    if (target !== "sql") everyTargetIsSql = false;
  }

  return {
    ok: true,
    proposedKind: targets.output_kind,
    outputKind: targets.output_kind === "dbt" && everyTargetIsSql ? "dbt" : "procedures",
    lowered,
  };
}

/** A reason recorded by an EARLIER attempt at analyze must not outlive that attempt (final fix
 * wave M5). `escalate` prefers `m.reasons.analyze` over the agent-level error, because a failed
 * verify reaches it as a generic "missing-output" — so a reason left standing would make an
 * attempt that fails BEFORE verify (denied, timeout, budget) park with the previous attempt's
 * `target-mismatch: …` instead of whatever actually stopped it. `reloadManifest` copies the whole
 * on-disk manifest over `m`, so this has to run after each reload, not once before them. */
function clearAnalyzeReason(m: Manifest): void {
  if (m.reasons) delete m.reasons.analyze;
}

/** The analyzer's character budget (Task W2): above it, `scripts/plan_batches.py` plans more than one
 * batch and the workflow is analysed batch by batch. An estimate, not tokens (≈ characters / 4). */
const ANALYZER_BUDGET_CHARS = 60000;

function analyzerBudgetChars(env: Env): number {
  return env.config.analyzerBudgetChars ?? ANALYZER_BUDGET_CHARS;
}

const TIERS: Tier[] = ["T1", "T2", "T3"];

function asTier(value: unknown): Tier | undefined {
  return TIERS.includes(value as Tier) ? (value as Tier) : undefined;
}

/** `segments/seams.json` as `scripts/check_seams.py` writes it. */
interface SeamReport {
  seams?: { producer?: string | null; consumer?: string; stream?: string; status?: string }[];
  duplicates?: { table?: string; producers?: string[] }[];
}

/**
 * Runs `scripts/check_seams.py <wf> [--segments …]` (Task W2) and returns the reason to park with
 * if a seam disagrees, or undefined when every one agrees. The report is deleted first, so the
 * reason can only ever come from this call's own `segments/seams.json`: `seam-mismatch:
 * <producer>-><consumer> <stream>` for the first mismatching seam (a duplicate that no seam reads
 * names its producers and table instead), and `script-error` for exit 2 or an exit 1 that left no
 * mismatch to name.
 */
async function seamReason(env: Env, m: Manifest, segments?: string[], note?: VerifyNote): Promise<string | undefined> {
  const file = wfDir(env.root, m.id, "segments", "seams.json");
  await rm(file, { force: true });
  const checked = await env.py("scripts/check_seams.py", segments ? [m.id, "--segments", segments.join(",")] : [m.id]);
  if (checked.ok) return undefined;
  if (checked.code !== 1) {
    env.log(`${m.id}: scripts/check_seams.py exited ${checked.code} — ${checked.err.trim().split("\n").slice(-1)[0] ?? ""}`);
    return "script-error";
  }
  note?.(checked.err);
  const report = await readJsonOr<SeamReport>(file, {});
  const seam = (report.seams ?? []).find((s) => s.status === "mismatch");
  const duplicate = (report.duplicates ?? [])[0];
  const why = seam
    ? `seam-mismatch: ${seam.producer ?? "?"}->${seam.consumer} ${seam.stream}`
    : duplicate
      ? `seam-mismatch: ${(duplicate.producers ?? []).join("+")}->? ${duplicate.table}`
      : "script-error";
  env.log(`${m.id}: ${why} — ${checked.err.trim().split("\n").slice(0, 3).join(" | ")}`);
  return why;
}

/** `--segments a,b` for a batch's call of a script, nothing for the whole workflow's. */
function scoped(segments?: string[]): string[] {
  return segments ? ["--segments", segments.join(",")] : [];
}

/** The reason a contract step failed with: `contract: <the script's first stderr line>` (redacted and
 * bounded, as every recorded reason is), or `script-error` for anything but exit 1. The script's
 * whole stderr is noted for the retry (R4). */
function contractFailure(env: Env, m: Manifest, script: string, result: ShResult, note?: VerifyNote): string {
  const lines = result.err.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (result.code !== 1) {
    env.log(`${m.id}: ${script} exited ${result.code} — ${lines.slice(-1)[0] ?? ""}`);
    return "script-error";
  }
  note?.(lines.join("\n"));
  env.log(`${m.id}: ${script} — ${lines.slice(0, 3).join(" | ")}`);
  return `contract: ${auditArgs(lines[0] ?? `${script} exited 1 and named no problem`)}`;
}

/**
 * Task L3 (R3), the first half of the analyze gate's contract step: `contract_scaffold.py --apply`
 * re-applies every mechanical field over the analyzer's contracts (authoritative: the analyzer's
 * judgment, nullability, keys and `target` are kept as written -- raising or dropping a target is
 * still `checkTargets`' to report). Undefined when it ran; else the reason to park with.
 */
async function reapplyScaffold(env: Env, m: Manifest, segments?: string[], note?: VerifyNote): Promise<string | undefined> {
  const applied = await env.py("scripts/contract_scaffold.py", [m.id, "--apply", ...scoped(segments)]);
  return applied.ok ? undefined : contractFailure(env, m, "scripts/contract_scaffold.py", applied, note);
}

/** The second half, after the target and seam checks: `contract_check.py` (Task L3, R2). */
async function contractReason(env: Env, m: Manifest, segments?: string[], note?: VerifyNote): Promise<string | undefined> {
  const checked = await env.py("scripts/contract_check.py", [m.id, ...scoped(segments)]);
  return checked.ok ? undefined : contractFailure(env, m, "scripts/contract_check.py", checked, note);
}

/** The analyzer's own share of a contract (Task L3): the orchestrator writes every mechanical field. */
const JUDGMENT_SENTENCE =
  `Every mechanical field of each contract -- workflow, segment, target, inputs, outputs, output, and every column's ` +
  `name and type -- is pre-filled by the orchestrator from the parsed DAG, the segment cuts, segments/targets.json ` +
  `and intake/mappings.yaml, and is re-applied after you: do not change it. Your job is the judgment: ` +
  `row_relation, ordering, tolerances, normalizations and parity_risks, each column's nullable, each entry's keys ` +
  `(and an input's expected_rows and large); and you may only lower a target (sql → snowpark → manual, never back ` +
  `towards sql).`;

async function stageAnalyze(env: Env, m: Manifest): Promise<Step> {
  const segmented = await env.py("scripts/segment.py", [m.id]);
  await reloadManifest(env.root, m);
  clearAnalyzeReason(m);
  if (segmented.code === 2) return scriptError(env, m, "analyze", "scripts/segment.py", segmented);
  if (!segmented.ok) return domainFailure(env, m, "analyze", "segmentation", segmented);

  // `--prefer auto` is always what the orchestrator passes: target_check.py itself resolves the
  // preference from manifest.output_target, then mappings/global.yaml's program.output_target,
  // then "procedures" (output-targets design §3.3), so the orchestrator never re-implements it.
  // Exit 1 is NOT a failure here (design §3.1): targets.json IS written, and it is written
  // precisely so the analyzer can see the `unknown` nodes and record them in analysis.md /
  // unsupported.json — parking the stage would make the one case that exit code exists for
  // unreachable. Only exit 2, a broken invocation, stops the stage.
  const targets = await env.py("scripts/target_check.py", [m.id, "--prefer", "auto"]);
  if (targets.code === 2) return scriptError(env, m, "analyze", "scripts/target_check.py", targets);
  if (!targets.ok) env.log(`${m.id}: target_check: unknown nodes in ${m.id}; the analyzer decides`);

  // Task W2: one analyzer call must fit its context. plan_batches.py groups consecutive waves under
  // the character budget; ONE batch (every committed sample) is today's single call, unchanged.
  // It has no domain failure: anything but exit 0 is a broken invocation.
  const planned = await env.py("scripts/plan_batches.py", [m.id, "--budget-chars", String(analyzerBudgetChars(env))]);
  if (!planned.ok) return scriptError(env, m, "analyze", "scripts/plan_batches.py", planned);
  const plan = await readJsonOr<{ batches?: unknown; warnings?: unknown }>(wfDir(env.root, m.id, "segments", "batches.json"), {});
  const entries = Array.isArray(plan.batches) ? plan.batches : [];
  if (!entries.every(isBatch)) return scriptError(env, m, "analyze", "scripts/plan_batches.py", planned);
  const batches = entries.map((batch) => ({ id: batch.id, segments: [...batch.segments] }));
  for (const warning of Array.isArray(plan.warnings) ? plan.warnings : []) env.log(`${m.id}: plan_batches: ${String(warning)}`);

  // Task L3 (R3): the mechanical fields are code's, written before the analyzer (single or batched)
  // ever runs -- and only where no contract exists yet, so a resumed run keeps the analyzer's
  // judgment. A field the scaffold could not derive is a note on stderr, logged, and left to the analyzer.
  const prefilled = await env.py("scripts/contract_scaffold.py", [m.id, "--prefill"]);
  if (!prefilled.ok) return scriptError(env, m, "analyze", "scripts/contract_scaffold.py", prefilled);
  for (const line of prefilled.err.split(/\r?\n/).filter((l) => l.trim())) env.log(`${m.id}: contract_scaffold: ${line.trim()}`);

  const order = await readOrder(env, m.id);
  const segments = order.flat();
  if (batches.length > 1) return await analyzeInBatches(env, m, batches, segments);

  const context = await inlineContext(env, m, "analyzer");
  let verdict: TargetVerdict | undefined;
  const result = await runAgent(
    env,
    m,
    "analyzer",
    "analyze",
    {
      instructions:
        `Analyze ${m.id}: classify every tool in workflows/${m.id}/parsed/dag.json, confirm the cuts in ` +
        `workflows/${m.id}/segments/order.json, and complete the contract.json of each segment in ` +
        `workflows/${m.id}/segments/, plus analysis.md, unsupported.json and the tier in manifest.json. ` +
        `${JUDGMENT_SENTENCE} The proposal is workflows/${m.id}/segments/targets.json; say in analysis.md why for ` +
        `each target you lowered. Before you finish, run python scripts/contract_check.py ${m.id} and ` +
        `python scripts/check_seams.py ${m.id} and fix what they report.` +
        notesInstruction("analyzer", m.id),
      context,
    },
    {},
    async (note) => {
      // The tier decision comes first (docs/spec/01-copilot-setup.md §3's state diagram:
      // `analyze --> MANUAL: tier T3` bypasses `analyze --> golden: contracts written` entirely,
      // and the spec's own migrateWorkflow skeleton sets status.analyze = "DONE" unconditionally
      // right after the analyzer runs, checking tier only afterwards). A T3 workflow is never
      // assigned a segment to translate, so it must never be held to the "every segment has a
      // contract.json" bar that only the T1/T2 -> golden path needs.
      const unsupported = await readJsonOr<{ tier?: string }>(wfDir(env.root, m.id, "unsupported.json"), {});
      if (unsupported.tier === "T3") {
        // No contracts exist, so §3.2's lower-only check has nothing to verify — but §3.3's
        // mirror is not conditional on that: `targets.json` was written for this workflow too and
        // its `output_kind` is the decision, so the manifest records it rather than staying silent.
        verdict = { ok: true, outputKind: await mirroredKind(env, m), lowered: [] };
        return true;
      }
      for (const segment of segments) {
        if (!(await fileExists(wfDir(env.root, m.id, "segments", segment, "contract.json")))) return false;
      }
      if (segments.length === 0) return false;
      // Task L3 (R3): the mechanical fields are re-applied BEFORE anything reads the contracts...
      const applied = await reapplyScaffold(env, m, undefined, note);
      if (applied) {
        reasons(m).analyze = applied;
        return false;
      }
      const checked = await checkTargets(env, m, segments);
      if (!checked.ok) {
        reasons(m).analyze = checked.reason!;
        return false;
      }
      // Task W2: every seam is checked by code, never by a model.
      const seams = await seamReason(env, m, undefined, note);
      if (seams) {
        reasons(m).analyze = seams;
        return false;
      }
      // ...and the checker runs last, so the target and seam checks keep their order and reasons.
      const contract = await contractReason(env, m, undefined, note);
      if (contract) {
        reasons(m).analyze = contract;
        return false;
      }
      verdict = checked;
      return true;
    },
  );
  if (!result.ok) return escalate(env, m, "analyze", result);
  await reloadManifest(env.root, m);
  clearAnalyzeReason(m);   // the stage succeeded: a DONE analyze carries no reason at all
  return await finishAnalyze(env, m, verdict);
}

function isBatch(value: unknown): value is AnalyzerBatch {
  const batch = value as AnalyzerBatch | null;
  return (
    typeof batch?.id === "string" &&
    Array.isArray(batch.segments) &&
    batch.segments.length > 0 &&
    batch.segments.every((segment) => typeof segment === "string")
  );
}

/**
 * The analyzer, batch by batch (Task W2, docs/reference/large-workflows.md). Each call is told its
 * batch's segments and gets `prompt_context.py --batch`'s context (the workflow map, the target
 * proposal, the producer contracts earlier batches wrote, its own segments' detail), and its policy
 * lane narrows to its batch (`ctx.batch`): its segments' contract.json files and its two fragments.
 * Its verify callback holds it to that: the fragments exist and, unless the workflow is already T3,
 * every contract exists, no target is raised, and every seam INTO its segments agrees. One retry per
 * batch, as for the single call. Then `stitch_analysis.py` — never an agent — writes analysis.md and
 * unsupported.json, and the tier is read from the stitched file.
 */
async function analyzeInBatches(env: Env, m: Manifest, batches: AnalyzerBatch[], segments: string[]): Promise<Step> {
  // Fragments of an earlier plan (more batches, other ids) must never be stitched or verified.
  await rm(wfDir(env.root, m.id, "analysis"), { recursive: true, force: true });
  let tierSoFar: Tier | undefined;
  for (const [index, batch] of batches.entries()) {
    const rendered = await env.py("scripts/prompt_context.py",
      [m.id, "--role", "analyzer", "--batch", batch.id, "--budget-chars", String(analyzerBudgetChars(env))]);
    if (!rendered.ok) {
      env.log(`${m.id}: prompt_context.py --batch ${batch.id} exited ${rendered.code}; the batch goes without inline context`);
    }
    const context = rendered.ok && rendered.out.trim() ? `\n\n${rendered.out.trim()}` : "";
    const fragment = (suffix: string) => wfDir(env.root, m.id, "analysis", `${batch.id}${suffix}`);
    const instructions =
      `Analyze ${m.id}, batch ${index + 1} of ${batches.length} (${batch.id}): segments ${batch.segments.join(", ")}. ` +
      `The workflow is too large for one analyzer call, so it is analysed batch by batch in wave order; the ` +
      `workflow map, the target proposal and the contracts earlier batches wrote at this batch's input seams are ` +
      `below. Classify every tool of these segments (workflows/${m.id}/parsed/dag.json; each segment's own tools ` +
      `are in workflows/${m.id}/segments/<segment>/dag.json) and write ONLY: a contract.json for each of these ` +
      `segments, workflows/${m.id}/analysis/${batch.id}.md (this batch's part of analysis.md) and ` +
      `workflows/${m.id}/analysis/${batch.id}.unsupported.json (this batch's tier and unsupported tools, in ` +
      `unsupported.json's shape). ${JUDGMENT_SENTENCE} The proposal is workflows/${m.id}/segments/targets.json; say ` +
      `in the fragment why for each target you lowered. For every input that comes from a segment of an earlier ` +
      `batch, declare the same nullability and keys as that producer's outputs[] entry below — ` +
      `scripts/check_seams.py checks every seam after you. Before you finish, run ` +
      `python scripts/contract_check.py ${m.id} --segments <segment> and ` +
      `python scripts/check_seams.py ${m.id} --segments <segment> for each of ${batch.segments.join(", ")}, and fix ` +
      `what they report. The orchestrator stitches analysis.md and unsupported.json and records the tier; do not ` +
      `write them or manifest.json.` +
      notesInstruction("analyzer", m.id);
    const result = await runAgent(env, m, "analyzer", "analyze", { instructions, context }, { batch }, async (note) => {
      if (!(await fileExists(fragment(".md")))) return false;
      // The fragment's unsupported.json must carry a tier the stitch can rank: a missing or unreadable
      // one is this batch's missing output, retried once here, never left for the stitch to refuse.
      const tier = asTier((await readJsonOr<{ tier?: unknown }>(fragment(".unsupported.json"), {})).tier);
      if (!tier) return false;
      // A T3 workflow is never translated, so — exactly as for the single call — it is never held to
      // the contract bar, in this batch or any later one (a later batch's seams would read contracts
      // a T3 batch never had to write).
      if (tier === "T3" || tierSoFar === "T3") return true;
      for (const segment of batch.segments) {
        if (!(await fileExists(wfDir(env.root, m.id, "segments", segment, "contract.json")))) return false;
      }
      // Task L3 (R3): as for one call -- re-apply, then targets, then seams, then the checker, each for
      // this batch's own segments only.
      const applied = await reapplyScaffold(env, m, batch.segments, note);
      if (applied) {
        reasons(m).analyze = applied;
        return false;
      }
      const checked = await checkTargets(env, m, batch.segments);
      if (!checked.ok) {
        reasons(m).analyze = checked.reason!;
        return false;
      }
      const seams = await seamReason(env, m, batch.segments, note);
      if (seams) {
        reasons(m).analyze = seams;
        return false;
      }
      const contract = await contractReason(env, m, batch.segments, note);
      if (contract) {
        reasons(m).analyze = contract;
        return false;
      }
      return true;
    });
    if (!result.ok) return escalate(env, m, "analyze", result);
    await reloadManifest(env.root, m);
    clearAnalyzeReason(m);
    const tier = asTier((await readJsonOr<{ tier?: unknown }>(fragment(".unsupported.json"), {})).tier);
    if (tier === "T3") tierSoFar = "T3";
  }

  const stitched = await env.py("scripts/stitch_analysis.py", [m.id]);
  if (stitched.code === 2) return scriptError(env, m, "analyze", "scripts/stitch_analysis.py", stitched);
  if (!stitched.ok) return domainFailure(env, m, "analyze", "stitch", stitched);
  return await finishAnalyze(env, m, undefined, segments);
}

/**
 * The tail both analyze paths share: the tier from `unsupported.json` (the analyzer's single call
 * writes it; `stitch_analysis.py` writes it for a batched one), then — for the batched path, whose
 * verify callbacks each saw one batch — §3.2's lower-only check over EVERY segment, which is what
 * decides `output_kind` (a T3 workflow mirrors `targets.json` instead); then the verdict is logged
 * and recorded, analyze is DONE, and a T3 workflow's translate goes MANUAL.
 */
async function finishAnalyze(env: Env, m: Manifest, verdict?: TargetVerdict, segments?: string[]): Promise<Step> {
  const tier = asTier((await readJsonOr<{ tier?: unknown }>(wfDir(env.root, m.id, "unsupported.json"), {})).tier);
  if (tier) m.tier = tier;
  if (m.tier === "T3") {
    // Task L3: a T3 workflow has no contracts (it is never translated), so whatever the pre-fill wrote
    // and no analyzer judged goes; a contract an analyzer did judge stays.
    const pruned = await env.py("scripts/contract_scaffold.py", [m.id, "--prune-unjudged"]);
    if (!pruned.ok) return scriptError(env, m, "analyze", "scripts/contract_scaffold.py", pruned);
  }
  if (!verdict && segments) {
    if (m.tier === "T3") {
      verdict = { ok: true, outputKind: await mirroredKind(env, m), lowered: [] };
    } else {
      const checked = await checkTargets(env, m, segments);
      if (!checked.ok) {
        m.status.analyze = "NEEDS_HUMAN";
        reasons(m).analyze = checked.reason!;
        env.log(`${m.id}: analyze → NEEDS_HUMAN (${checked.reason})`);
        return "stop";
      }
      verdict = checked;
    }
  }

  if (verdict) {
    for (const line of verdict.lowered) env.log(line);
    if (verdict.proposedKind === "dbt" && verdict.outputKind !== "dbt") {
      env.log(`${m.id}: targets.json proposed output_kind dbt, but a contract lowered a segment off sql — procedures`);
    }
    m.output_kind = verdict.outputKind;
  }

  m.status.analyze = "DONE";
  if (m.tier === "T3") {
    m.status.translate = "MANUAL";
    reasons(m).translate = "tier-T3";
    env.log(`${m.id}: tier T3 — translation stays manual (see unsupported.json)`);
    return "stop";
  }
  return "continue";
}

async function stageGolden(env: Env, m: Manifest): Promise<Step> {
  if (m.golden_sets?.length) {
    m.status.golden = "DONE";
    return "continue";
  }
  if (env.config.golden.producer === "alteryx") {
    // F10: scripts/inject_outputs.py's argparse requires --capture-dir on every invocation, and
    // only WRITES golden CSVs on the second, --import-set pass — captures never land under
    // workflows/<id>/golden/ on their own. The first form instruments the workflow and PRINTS the
    // exact AlteryxEngineCmd command line for a person to run; only after that real run has
    // produced .yxdb files does the second form convert them into a named golden set.
    env.log(
      `${m.id}: golden data must come from Alteryx, in two steps (scripts/inject_outputs.py --help for the ` +
        `full contract):\n` +
        `  1) python scripts/inject_outputs.py ${m.id} --capture-dir <capture-dir>\n` +
        `     writes source/*.instrumented.yxmd + golden/capture_map.json, and prints the AlteryxEngineCmd ` +
        `command to run that instrumented copy — run it, so <capture-dir> fills with .yxdb captures\n` +
        `  2) python scripts/inject_outputs.py ${m.id} --capture-dir <capture-dir> --import-set normal\n` +
        `     imports those captures as golden set "normal" under workflows/${m.id}/golden/ and records it in\n` +
        `     manifest.json golden_sets (import "normal" first; repeat both steps per golden set)\n` +
        `then resume with --from-stage golden --only ${m.id}`,
    );
    m.status.golden = "BLOCKED";
    return "stop";
  }

  const simulated = await env.py("scripts/dev/alteryx_sim.py", [m.id, "--set", "all"]);
  await reloadManifest(env.root, m);
  if (simulated.code === 2) return scriptError(env, m, "golden", "scripts/dev/alteryx_sim.py", simulated);
  if (!m.golden_sets?.length) {
    m.status.golden = "BLOCKED";
    reasons(m).golden = "no-golden-sets";
    env.log(`${m.id}: the simulator produced no golden sets — ${simulated.err.trim() || "unsupported workflow"}`);
    return "stop";
  }
  m.status.golden = "DONE";
  return "continue";
}

interface SegmentOutcome {
  verdict: string;
  /** Why this segment ended NEEDS_HUMAN, carried out to reasons.translate (F13). Undefined for
   * a PASS-shaped verdict, which needs no explanation. */
  reason?: string;
}

/** The reason string for an agent (translator/fixer/reviewer/validator) that did not complete —
 * "budget" on its own when the tool-call budget stopped it (F13 example: "seg_03: budget"),
 * otherwise "<role> <error>" (e.g. "translator missing-output"). */
function agentFailureReason(role: Role, result: AgentResult): string {
  return result.detail === "budget" ? "budget" : `${role} ${result.error ?? "error"}`;
}

/** A failing script's own words, made safe to put in an agent prompt: `auditArgs` is the same
 * redact-then-bound helper `CopilotRunner` already applies to `AgentResult.detail` (redaction
 * first, so a truncated secret cannot survive the cut), reused rather than reimplemented. */
function diagnosis(result: ShResult): string {
  return auditArgs(`${result.err}\n${result.out}`.trim()) || "(no output)";
}

/** `validation.json` and `validation.<set>.json` -- the shapes `lib.validation.clear_stale_reports`
 * deletes -- and the chain report's `validation_workflow[.<set>].json` (`clear_stale_workflow_reports`). */
const SEGMENT_REPORT = /^validation(\.[a-z0-9_-]+)?\.json$/i;
const CHAIN_REPORT = /^validation_workflow(\.[a-z0-9_-]+)?\.json$/i;

/**
 * Task L4 (R2): the translator and the fixer may run the validator scripts on their own work, which
 * writes the very reports a validator session is judged by ("the report exists", then its verdict).
 * Before the validator is dispatched those reports are deleted -- each named segment's, and for a
 * dbt project the chain report too -- so only the validator's own run can satisfy that check, and a
 * verdict read afterwards is never one a translator or fixer produced.
 */
async function clearValidationReports(env: Env, wfId: string, segments: string[], chain: boolean): Promise<void> {
  const clear = async (dir: string, shape: RegExp) => {
    let names: string[] = [];
    try {
      names = await readdir(dir);
    } catch {
      return; // no directory, nothing to clear
    }
    for (const name of names) if (shape.test(name)) await rm(path.join(dir, name), { force: true });
  };
  for (const segment of segments) await clear(wfDir(env.root, wfId, "segments", segment), SEGMENT_REPORT);
  if (chain) await clear(wfDir(env.root, wfId), CHAIN_REPORT);
}

// ---------- live hardening, Task L8: the orchestrator writes the translation's skeleton ----------

/** The translation files of a skeleton, with the text each held when the translator's session began --
 * so a session that ends with every one of them exactly as it was is known to have written nothing. */
type Skeleton = Map<string, string>;

/** The marker as `scripts/lib/scaffold.py`'s `TODO_PATTERN` finds it: any case, any spacing (fix round 1, M2). */
const TODO_RE = /todo\s*\(\s*scaffold\s*\)/i;

/** The files a translation lives in (fix round 1, I3): a segment's procedure file itself, or a dbt project's
 * models -- never its notes, `compile_check.json`, logs or anything else a session may also leave there. */
const MODEL_FILE = (file: string): boolean => /[\\/]models[\\/].*\.sql$/i.test(file);

/** The text of `target` (a file), or of every file under it (a directory, recursively) -- only the
 * files `wanted` accepts, when it is given. */
async function snapshot(target: string, wanted?: (file: string) => boolean): Promise<Skeleton> {
  const files: Skeleton = new Map();
  const visit = async (at: string): Promise<void> => {
    let info;
    try {
      info = await stat(at);
    } catch {
      return;
    }
    if (info.isDirectory()) {
      for (const entry of await readdir(at)) await visit(path.join(at, entry));
    } else if (!wanted || wanted(at)) {
      files.set(at, await readFile(at, "utf8"));
    }
  };
  await visit(target);
  return files;
}

/**
 * Task L8, fix round 1 (I1, I3, M1): the skeleton a translator's session is judged against -- the
 * translation files (`tree`, filtered by `wanted`) as they are when the session begins, whoever wrote them
 * (this run's scaffold or an earlier run's), but only while they still hold a TODO body. A skeleton with
 * nothing to fill (a segment that only passes a stream through) is already a complete translation:
 * undefined, so leaving it exactly as written is no failure.
 */
async function pendingSkeleton(tree: string, wanted?: (file: string) => boolean): Promise<Skeleton | undefined> {
  const files = await snapshot(tree, wanted);
  return [...files.values()].some((text) => TODO_RE.test(text)) ? files : undefined;
}

/** Whether the translation files still hold exactly the pending skeleton: the same files, each with the same
 * text (a new model counts as work; a new note, report or log does not -- they are not translation files). */
async function untouched(skeleton: Skeleton | undefined, tree: string, wanted?: (file: string) => boolean): Promise<boolean> {
  if (!skeleton) return false;
  const now = await snapshot(tree, wanted);
  if (now.size !== skeleton.size) return false;
  for (const [file, text] of skeleton) if (now.get(file) !== text) return false;
  return true;
}

/** Whether a translation file under `target` (a model, a YAML file, a procedure) still holds a TODO body. */
async function holdsTodo(target: string): Promise<boolean> {
  for (const text of (await snapshot(target, (file) => /\.(sql|py|yml)$/.test(file))).values()) {
    if (TODO_RE.test(text)) return true;
  }
  return false;
}

/**
 * Task L8 (R2): before a translator's first session the orchestrator writes the translation's skeleton
 * (`scripts/translation_scaffold.py <wf> --segment <seg> | --dbt`: every mechanical line, a TODO body per tool) --
 * only when `own`, the file the translation lives in, does not exist yet, so a resumed run keeps the
 * translator's work (and the script itself never writes over a file either). "written" when it ran,
 * "exists" when there was nothing to do, "error" when the script failed -- a script error, like
 * compile_check.py's exit 2.
 */
async function writeSkeleton(env: Env, m: Manifest, args: string[], own: string): Promise<"written" | "exists" | "error"> {
  if (await fileExists(own)) return "exists";
  const ran = await env.py("scripts/translation_scaffold.py", args);
  if (!ran.ok) {
    env.log(`${m.id}${args.includes("--segment") ? ` ${args[args.length - 1]}` : ""}: translation_scaffold.py exited ${ran.code} — ${ran.err.trim()}`);
    return "error";
  }
  // What it wrote (stdout: `wrote <file>`) and anything it could not derive (stderr: `note: …`).
  for (const line of `${ran.out}\n${ran.err}`.split(/\r?\n/).filter((l) => l.trim())) {
    env.log(`${m.id}: translation_scaffold: ${line.trim()}`);
  }
  return "written";
}

/** What the translator is told when the skeleton the orchestrator just wrote has no TODO in it (I1). */
function completeSentence(file: string): string {
  return ` The orchestrator has written ${file} and it is already complete: every line of this translation is ` +
    `mechanical (it only passes its input through). Review it, keep it as it is, and write translation_notes.md.`;
}

/** What the translator is told when its file is (still) the orchestrator's skeleton. */
function skeletonSentence(file: string, target: "sql" | "snowpark"): string {
  const [lines, keep] = target === "snowpark"
    ? ["the signature, the reads, the writes and the return", "the signature, the reads, the writes or the file layout"]
    : ["the header, the LET lines, the write statements, the work-table names and the RETURN",
       "the header, the LET lines, the write statements or the file layout"];
  return ` The orchestrator has written the skeleton, ${file}: every mechanical line (${lines}) is already there. ` +
    `Replace every ${SCAFFOLD_TODO} body with the transformation and change nothing else: not ${keep}. ` +
    `scripts/compile_check.py refuses a remaining ${SCAFFOLD_TODO} (scaffold:todo).`;
}

function dbtSkeletonSentence(wfId: string): string {
  return ` The orchestrator has written the project's skeleton under workflows/${wfId}/dbt/: dbt_project.yml, ` +
    `profiles.yml, README.md, models/sources.yml, models/schema.yml and one model per contract output with its config ` +
    `line, its source()/ref() reads, its CTE names and its final SELECT. Replace every ${SCAFFOLD_TODO} (a CTE body, or ` +
    `a hook in a config line) with the transformation and change nothing else: not the file layout, the YAML files ` +
    `or the config lines. scripts/compile_check.py ${wfId} --target dbt refuses a remaining ${SCAFFOLD_TODO} (scaffold:todo).`;
}

/** What a fixer is told when the file it repairs still holds a TODO body. */
const UNWRITTEN_SENTENCE =
  ` Some ${SCAFFOLD_TODO} bodies of the orchestrator's skeleton are still unwritten (compile_check.py names each as ` +
  `scaffold:todo): write them, and keep the mechanical lines around them as they are.`;

/** How far one call of `migrateSegment` runs. The defaults are the ordinary loop (translator on
 * iteration 0, then fixers, up to `maxFixIterations`); the chain check (Task W1) asks for exactly
 * one fixer round — `{ firstIteration: 1, iterations: 2, note }` — on the segment where the stitched
 * workflow first diverged. */
interface SegmentOptions {
  /** The first iteration to run; 0 is the translator's turn, anything later a fixer's. */
  firstIteration?: number;
  /** One past the last iteration (default `maxFixIterations`). */
  iterations?: number;
  /** Replaces the fixer's standing "read validation.json and review.json first and change only what
   * their diagnosis points at" sentence — for the chain check's round, where the segment's own
   * validation.json PASSes by construction and `validation_workflow.json` holds the diagnosis. */
  repairTask?: string;
}

/** translate → review → validate for one segment, bounded by maxFixIterations. */
async function migrateSegment(env: Env, m: Manifest, segment: string, opts: SegmentOptions = {}): Promise<SegmentOutcome> {
  const first = opts.firstIteration ?? 0;
  const iterations = opts.iterations ?? env.config.maxFixIterations;
  // How many iterations this call can spend — what every exhausted-loop reason reports.
  const spent = iterations - first;
  const contractFile = wfDir(env.root, m.id, "segments", segment, "contract.json");
  const procSql = wfDir(env.root, m.id, "segments", segment, "proc.sql");
  const procPy = wfDir(env.root, m.id, "segments", segment, "proc.py");
  // The reason reported if every iteration is spent without a PASS or an early escalation —
  // updated as the loop learns more, so the final NEEDS_HUMAN names the LAST thing that actually
  // happened (F13: "seg_02: validation FAIL after 3 iterations", "seg_01: reviewer BLOCK …").
  let lastReason = `validation FAIL after ${spent} iterations`;
  // Set when an iteration ends BEFORE review, so the next fixer is not sent to read a
  // validation.json / review.json that this segment does not have yet (fix round 1, I2).
  let failedBeforeReview: string | undefined;

  for (let iteration = first; iteration < iterations; iteration++) {
    // Re-read per iteration: the analyzer's contract is the one authority on what this segment is
    // written in, and a --from-stage analyze between two runs can legitimately have changed it.
    const contract = await readJsonOr<{ target?: SegmentTarget }>(contractFile, {});
    // `manual` is the bottom of the ladder: a segment the analyzer judged no generator should
    // attempt (design §3.2). A T3 workflow normally never gets here at all, but nothing forces
    // unsupported.json's tier and this contract to agree, so the guard lives at the point of harm
    // — no agent is dispatched and nothing is written (fix round 1, I1).
    if (contract.target === "manual") {
      env.log(`${m.id} ${segment}: contract target is manual — no generator may attempt it; segment needs a human`);
      return { verdict: "NEEDS_HUMAN", reason: "manual-segment" };
    }
    const target: "sql" | "snowpark" = contract.target === "snowpark" ? "snowpark" : "sql";
    const role: Role = iteration === 0 ? "translator" : "fixer";
    // Task L8 (R2): the file the translation lives in, and the skeleton the orchestrator writes there
    // before the translator's first session (only where none exists: a resumed run keeps its work).
    const own = target === "snowpark" ? procPy : procSql;
    const ownRel = `workflows/${m.id}/segments/${segment}/${path.basename(own)}`;
    let skeleton: Skeleton | undefined;
    let complete = false;
    if (iteration === 0) {
      const scaffolded = await writeSkeleton(env, m, [m.id, "--segment", segment], own);
      if (scaffolded === "error") return { verdict: "NEEDS_HUMAN", reason: "script-error" };
      // fix round 1 (I1, M1): judged against the file as this session finds it, while it holds a TODO
      skeleton = await pendingSkeleton(own);
      complete = scaffolded === "written" && !skeleton && (await fileExists(own));
    }
    const hasTodo = await holdsTodo(own);
    const pythonNote =
      ` This is a Snowpark Python segment: its source of truth is proc.py, and proc.sql is rendered from it by ` +
      `scripts/render_snowpark.py — never edit proc.sql by hand.`;
    const task =
      iteration === 0
        ? `Translate segment ${segment} of ${m.id} per workflows/${m.id}/segments/${segment}/contract.json and dag.json.` +
          (hasTodo ? skeletonSentence(ownRel, target) : complete ? completeSentence(ownRel) : "") +
          (target === "snowpark" ? pythonNote : "")
        : (opts.repairTask ??
            `Repair segment ${segment} of ${m.id}: read workflows/${m.id}/segments/${segment}/validation.json and ` +
              `review.json first and change only what their diagnosis points at.`) +
          (failedBeforeReview ? ` ${failedBeforeReview}` : "") +
          (hasTodo ? UNWRITTEN_SENTENCE : "") +
          (target === "snowpark" ? pythonNote : "") +
          notesInstruction("fixer", m.id);
    const written = await runAgent(
      env,
      m,
      role,
      "translate",
      task,
      { segment, iteration },
      async (note) => {
        const exists = target === "snowpark" ? await fileExists(procPy) : (await fileExists(procSql)) || (await fileExists(procPy));
        // Task L8: the skeleton is the orchestrator's, not the translator's output -- a session that
        // left it exactly as written has written nothing (retried once, told why; a timeout not kept).
        if (exists && (await untouched(skeleton, own))) {
          note(`${ownRel} is still the orchestrator's skeleton, exactly as written: every ${SCAFFOLD_TODO} body is still to be written`);
          return false;
        }
        return exists;
      },
      // Task L7 fix round 1 (R-b), by content since fix round 2 (IMP-2): the same file `verify`
      // checks for existence (`own` -- L8's own canonical translation file for this target), now
      // also for a content change during THIS attempt -- a fixer (or a resumed translator) that
      // timed out without actually editing it is not kept just because an EARLIER attempt already
      // wrote one, or because something else (compile_check.json, fix_log.md) changed near the
      // same instant.
      async () => {
        const before = await snapshot(own);
        return async () => !sameContent(before, await snapshot(own));
      },
    );
    if (!written.ok) {
      env.log(`${m.id} ${segment}: ${role} ${written.error ?? "error"} — segment needs a human`);
      return { verdict: "NEEDS_HUMAN", reason: agentFailureReason(role, written) };
    }

    if (target === "snowpark") {
      // proc.sql is a rendered artefact, never hand-written: it has to exist and match proc.py
      // before compile_check.py can check the pair (output-targets design §4.2/§5.1).
      const rendered = await env.py("scripts/render_snowpark.py", [m.id, segment]);
      if (rendered.code === 2) {
        env.log(`${m.id} ${segment}: render_snowpark.py exited 2 — ${rendered.err.trim()}`);
        return { verdict: "NEEDS_HUMAN", reason: "script-error" };
      }
      if (!rendered.ok) {
        // Exit 1 is the renderer's one domain failure (`$$` in proc.py): the agent's own output is
        // unrenderable, which is the same kind of problem a compile error is — let it try again.
        env.log(`${m.id} ${segment}: render failed on iteration ${iteration} — ${rendered.err.trim()}`);
        lastReason = `render_snowpark failed after ${spent} iterations`;
        failedBeforeReview =
          `The previous attempt failed before review: scripts/render_snowpark.py said: ${diagnosis(rendered)}`;
        continue;
      }
    }

    const compiled = await env.py(
      "scripts/compile_check.py",
      target === "snowpark" ? [m.id, segment, "--target", target] : [m.id, segment],
    );
    if (compiled.code === 2) {
      env.log(`${m.id} ${segment}: compile_check.py exited 2 — ${compiled.err.trim()}`);
      return { verdict: "NEEDS_HUMAN", reason: "script-error" };
    }
    if (!compiled.ok) {
      env.log(`${m.id} ${segment}: compile check failed on iteration ${iteration} — ${compiled.err.trim()}`);
      lastReason = `compile check failed after ${spent} iterations`;
      failedBeforeReview =
        `The previous attempt failed before review: scripts/compile_check.py failed; read compile_check.json. ` +
        `It said: ${diagnosis(compiled)}`;
      continue;
    }
    // Past compile: review.json and validation.json are about to exist, so the standing fixer
    // task is accurate again and last iteration's pre-review diagnosis must not be repeated.
    failedBeforeReview = undefined;

    const reviewed = await runAgent(
      env,
      m,
      "reviewer",
      "translate",
      `Review segment ${segment} of ${m.id} against its contract and write review.json.`,
      { segment, iteration },
      () => fileExists(wfDir(env.root, m.id, "segments", segment, "review.json")),
    );
    if (!reviewed.ok) return { verdict: "NEEDS_HUMAN", reason: agentFailureReason("reviewer", reviewed) };
    const review = await readJsonOr<{ verdict?: string }>(
      wfDir(env.root, m.id, "segments", segment, "review.json"),
      {},
    );
    if (review.verdict === "BLOCK") {
      env.log(`${m.id} ${segment}: review BLOCK on iteration ${iteration}`);
      lastReason = `reviewer BLOCK after ${spent} iterations`;
      continue;
    }

    await clearValidationReports(env, m.id, [segment], false); // Task L4: never a translator's own report
    const validated = await runAgent(
      env,
      m,
      "validator",
      "translate",
      target === "snowpark"
        ? `Validate segment ${segment} of ${m.id} against every golden set with scripts/validate_snowpark.py and ` +
          `write validation.json.`
        : `Validate segment ${segment} of ${m.id} against every golden set and write validation.json.`,
      { segment, iteration },
      () => fileExists(wfDir(env.root, m.id, "segments", segment, "validation.json")),
    );
    if (!validated.ok) return { verdict: "NEEDS_HUMAN", reason: agentFailureReason("validator", validated) };
    const validation = await readJsonOr<{ verdict?: string; needs_human?: boolean }>(
      wfDir(env.root, m.id, "segments", segment, "validation.json"),
      {},
    );
    // Coordinator ruling (task-15-int brief): needs_human wins regardless of verdict — a
    // PASS_WITH_ACCEPTED_DIFF report that also says needs_human: true is not a green light just
    // because its verdict starts with "PASS".
    const verdict = String(validation.verdict ?? "");
    if (validation.needs_human) {
      env.log(`${m.id} ${segment}: validation says the diff is not in the SQL — needs a human`);
      return { verdict: "NEEDS_HUMAN", reason: "needs_human" };
    }
    if (verdict.startsWith("PASS")) return { verdict };
    lastReason = `validation FAIL after ${spent} iterations`;
  }
  return { verdict: "NEEDS_HUMAN", reason: lastReason };
}

// ---------- a dbt workflow: ONE project, one translate loop (output-targets design §4.3, §6) ----------

/** design §4.3 / §6: a dbt workflow deploys with one command; this file replaces master.sql. */
export function dbtReadme(wfId: string): string {
  return [
    `# ${wfId} — deploy as a dbt project`,
    ``,
    `Generated by orchestrate.ts. This workflow's output kind is \`dbt\`: there is no master.sql and no per-segment`,
    `procedure. Nothing in this repository has run it against Snowflake.`,
    ``,
    "```",
    `dbt run --project-dir workflows/${wfId}/dbt --profiles-dir workflows/${wfId}/dbt --target snowflake --vars '{"src_schema": "<SRC>", "tgt_schema": "<TGT>"}'`,
    "```",
    ``,
    `<SRC> is the schema that holds the mapped source tables under their logical names; <TGT> is where the models`,
    `are written. Every connection value comes from the SNOWFLAKE_* variables named in workflows/${wfId}/dbt/profiles.yml.`,
    `dbt-snowflake must be installed first; it is not part of requirements.txt.`,
    ``,
  ].join("\n");
}

/** The model a contract output becomes — the mirror of `scripts/lib/dbt_project.model_name`: a
 * final target's model is its logical name lower-cased, a work stream's its MIG_WORK table name
 * lower-cased (design §4.3). Anything that is not `kind: "target"` is a work stream there too. An
 * output without the key its kind is named by throws, as the Python raises KeyError — never the
 * model name "undefined". */
export function dbtModelName(output: { kind?: string; logical?: string | null; table?: string | null }): string {
  const key = output.kind === "target" ? "logical" : "table";
  const value = output[key];
  if (value === undefined || value === null) {
    throw new Error(`dbtModelName: a ${output.kind ?? "work"} output is named by its "${key}", and this one has none: ${JSON.stringify(output)}`);
  }
  return key === "logical" ? String(value).toLowerCase() : String(value).split(".").pop()!.toLowerCase();
}

/** `<model> (<segment>)` for every contract output of the given segments — what the fixer is told
 * to look at after a validation FAIL. */
async function failingModels(env: Env, m: Manifest, segments: string[]): Promise<string> {
  const names: string[] = [];
  for (const segment of segments) {
    const contract = await readJsonOr<{ outputs?: { kind?: string; logical?: string; table?: string }[] }>(
      wfDir(env.root, m.id, "segments", segment, "contract.json"),
      {},
    );
    for (const output of contract.outputs ?? []) names.push(`${dbtModelName(output)} (${segment})`);
  }
  return names.join(", ");
}

interface DbtOutcome {
  /** Per segment, the verdict of the LAST validation of the project as it now stands. */
  verdicts: Record<string, string>;
  /** Why the project needs a human; undefined when every segment PASSed. */
  reason?: string;
}

/** translate → compile check → review → validate for the whole dbt project, bounded by
 * maxFixIterations — `migrateSegment`'s loop, run once per workflow instead of once per segment. */
async function migrateDbt(env: Env, m: Manifest, segments: string[]): Promise<DbtOutcome> {
  const iterations = env.config.maxFixIterations;
  const dbtFile = (...rest: string[]) => wfDir(env.root, m.id, "dbt", ...rest);
  const reportOf = (segment: string) => wfDir(env.root, m.id, "segments", segment, "validation.json");
  let lastReason = `validation FAIL after ${iterations} iterations`;
  let failedBeforeReview: string | undefined;
  let failing = "";
  let verdicts: Record<string, string> = {};

  for (let iteration = 0; iteration < iterations; iteration++) {
    // Every agent turn may change the project, so a verdict from an earlier validation no longer
    // describes it: a park must never report a PASS for a project nothing validated since.
    verdicts = {};
    const role: Role = iteration === 0 ? "translator" : "fixer";
    // Task L8 (R2): the project's skeleton, written before the translator's first session when the
    // project does not exist yet (a resumed run keeps the translator's project).
    let skeleton: Skeleton | undefined;
    if (iteration === 0) {
      // N3: the project is asked for by name, never inferred from the manifest on disk
      const scaffolded = await writeSkeleton(env, m, [m.id, "--dbt"], dbtFile("dbt_project.yml"));
      if (scaffolded === "error") return { verdicts, reason: "script-error" };
      // fix round 1 (I3, M1): judged on the project's models alone, while they hold a TODO
      skeleton = await pendingSkeleton(dbtFile(), MODEL_FILE);
    }
    const hasTodo = await holdsTodo(dbtFile());
    const task =
      iteration === 0
        ? `Translate ${m.id} into ONE dbt project under workflows/${m.id}/dbt/ (its output kind is dbt): read every ` +
          `segment's contract.json and dag.json and follow docs/reference/output-targets.md §3.3 and cookbook/dbt.md.` +
          (hasTodo ? dbtSkeletonSentence(m.id) : "")
        : `Repair the dbt project of ${m.id}: read workflows/${m.id}/dbt/review.json and every segment's ` +
          `validation.json first and change only what their diagnosis points at.` +
          (failing ? ` Failing models: ${failing}.` : "") +
          (failedBeforeReview ? ` ${failedBeforeReview}` : "") +
          (hasTodo ? UNWRITTEN_SENTENCE : "") +
          notesInstruction("fixer", m.id);
    const written = await runAgent(
      env,
      m,
      role,
      "translate",
      task,
      { iteration, dbt: true },
      async (note) => {
        if (!(await fileExists(dbtFile("dbt_project.yml")))) return false;
        // Task L8: a project that is still exactly the orchestrator's skeleton is not the translator's output.
        if (await untouched(skeleton, dbtFile(), MODEL_FILE)) {
          note(`workflows/${m.id}/dbt/ is still the orchestrator's skeleton, exactly as written: every ${SCAFFOLD_TODO} is still to be written`);
          return false;
        }
        return true;
      },
      // Task L7 fix round 1 (R-b), "the dbt fixer likewise", by content since fix round 2 (IMP-2): a
      // dbt fixer usually edits one or two model files, never `dbt_project.yml` itself, so freshness
      // is snapshotted over `DBT_WORK_FILE` -- every model and the two model YAMLs, recursively under
      // `dbt/` -- not just the one file `verify` checks, and never `compile_check.json`/`fix_log.md`/
      // other housekeeping a fixer is told to write regardless of whether it repaired anything.
      async () => {
        const before = await snapshot(dbtFile(), DBT_WORK_FILE);
        return async () => !sameContent(before, await snapshot(dbtFile(), DBT_WORK_FILE));
      },
    );
    if (!written.ok) {
      env.log(`${m.id}: ${role} ${written.error ?? "error"} on the dbt project — it needs a human`);
      return { verdicts, reason: agentFailureReason(role, written) };
    }

    // DV6: a dbt project is one unit, so the check takes no segment.
    const compiled = await env.py("scripts/compile_check.py", [m.id, "--target", "dbt"]);
    if (compiled.code === 2) {
      env.log(`${m.id}: compile_check.py --target dbt exited 2 — ${compiled.err.trim()}`);
      return { verdicts, reason: "script-error" };
    }
    if (!compiled.ok) {
      env.log(`${m.id}: dbt compile check failed on iteration ${iteration} — ${compiled.err.trim()}`);
      lastReason = `compile check failed after ${iterations} iterations`;
      failing = "";   // M1: the failing models are an older project's; the check's words replace them
      failedBeforeReview =
        `The previous attempt failed before review: scripts/compile_check.py ${m.id} --target dbt failed; read ` +
        `workflows/${m.id}/dbt/compile_check.json. It said: ${diagnosis(compiled)}`;
      continue;
    }
    failedBeforeReview = undefined;

    const reviewed = await runAgent(
      env,
      m,
      "reviewer",
      "translate",
      `Review the dbt project of ${m.id} against every segment's contract and write workflows/${m.id}/dbt/review.json.`,
      { iteration, dbt: true },
      () => fileExists(dbtFile("review.json")),
    );
    if (!reviewed.ok) return { verdicts, reason: agentFailureReason("reviewer", reviewed) };
    if ((await readJsonOr<{ verdict?: string }>(dbtFile("review.json"), {})).verdict === "BLOCK") {
      env.log(`${m.id}: review BLOCK on the dbt project, iteration ${iteration}`);
      lastReason = `reviewer BLOCK after ${iterations} iterations`;
      failing = "";   // M1: dbt/review.json is newer than the last validation's failing models
      continue;
    }

    await clearValidationReports(env, m.id, segments, true); // Task L4: never a translator's own reports
    const validated = await runAgent(
      env,
      m,
      "validator",
      "translate",
      `Validate the dbt project of ${m.id} against every golden set with scripts/validate_dbt.py ${m.id}; it writes ` +
        `every segment's validation.json.`,
      { iteration, dbt: true },
      async () => {
        for (const segment of segments) if (!(await fileExists(reportOf(segment)))) return false;
        return true;
      },
    );
    if (!validated.ok) return { verdicts, reason: agentFailureReason("validator", validated) };
    const reports = await Promise.all(
      segments.map((segment) => readJsonOr<{ verdict?: string; needs_human?: boolean }>(reportOf(segment), {})),
    );
    // needs_human wins over any verdict, as it does per segment (task-15-int ruling 4): a
    // PASS_WITH_ACCEPTED_DIFF that also says needs_human is not a green light for that segment.
    verdicts = Object.fromEntries(
      segments.map((segment, i) => [segment, reports[i].needs_human ? "NEEDS_HUMAN" : String(reports[i].verdict ?? "")]),
    );
    if (reports.some((report) => report.needs_human)) {
      env.log(`${m.id}: validate_dbt says a diff is not in the project — it needs a human`);
      return { verdicts, reason: "needs_human" };
    }
    if (segments.every((segment) => verdicts[segment].startsWith("PASS"))) return { verdicts };
    failing = await failingModels(env, m, segments.filter((segment) => !verdicts[segment].startsWith("PASS")));
    lastReason = `validation FAIL after ${iterations} iterations`;
  }
  return { verdicts, reason: lastReason };
}

/** stageTranslate for `output_kind: "dbt"`: one loop for the whole project, then every segment's
 * status from its own report, and `procs/README.md` (never master.sql) once every segment PASSes. */
async function translateDbt(env: Env, m: Manifest, order: string[][]): Promise<Step> {
  const segments = order.flat();
  m.segment_status ??= {};
  // F11's crash-window rule, per workflow: a recorded NEEDS_HUMAN segment parks the project.
  if (segments.some((segment) => m.segment_status![segment] === "NEEDS_HUMAN")) {
    m.status.translate = "NEEDS_HUMAN";
    reasons(m).translate = "dbt: needs_human (recorded)";
    env.log(`${m.id}: the dbt project has a recorded NEEDS_HUMAN segment — stopping the workflow`);
    return "stop";
  }
  // …and a project whose every segment already PASSed (saved, then interrupted before VALIDATED)
  // is not translated again: the project is one unit, so it is all or nothing.
  if (!segments.every((segment) => String(m.segment_status![segment] ?? "").startsWith("PASS"))) {
    const outcome = await migrateDbt(env, m, segments);
    for (const segment of segments) {
      const verdict = outcome.verdicts[segment] ?? "";
      m.segment_status[segment] = outcome.reason && !verdict.startsWith("PASS") ? "NEEDS_HUMAN" : verdict;
    }
    if (outcome.reason) {
      // Set BEFORE the save that records segment_status (F11's ordering).
      m.status.translate = "NEEDS_HUMAN";
      reasons(m).translate = `dbt: ${outcome.reason}`;
      await saveManifest(env.root, m);
      env.log(`${m.id}: the dbt project needs a human (${outcome.reason}) — stopping the workflow`);
      return "stop";
    }
    await saveManifest(env.root, m);
  }

  // Task W1: a dbt project's run IS the chain — validate_dbt.py wrote the workflow's chain report
  // from the same run that PASSed every segment, so no extra run: translate needs that report to
  // say PASS* (absent or anything else parks; a chain FAIL is never a fixer task for dbt here).
  const chain = await readJsonOr<ChainReport>(wfDir(env.root, m.id, "validation_workflow.json"), {});
  if (!String(chain.verdict ?? "").startsWith("PASS")) {
    m.status.translate = "NEEDS_HUMAN";
    reasons(m).translate = "dbt: chain FAIL";
    env.log(`${m.id}: translate → NEEDS_HUMAN (dbt: chain FAIL) — validation_workflow.json says ${chain.verdict ?? "nothing"}`);
    return "stop";
  }
  // Final fix wave N-dbt: needs_human wins over a passing chain verdict, the M6 rule chainCheck follows.
  if (chain.needs_human) {
    m.status.translate = "NEEDS_HUMAN";
    reasons(m).translate = "dbt: chain needs_human";
    env.log(`${m.id}: translate → NEEDS_HUMAN (dbt: chain needs_human) — validation_workflow.json says ${chain.verdict} and needs_human`);
    return "stop";
  }

  const procs = wfDir(env.root, m.id, "procs");
  await mkdir(procs, { recursive: true });
  await rm(path.join(procs, "master.sql"), { force: true });   // a dbt workflow has none (design §4.3)
  await writeFile(path.join(procs, "README.md"), dbtReadme(m.id), "utf8");
  m.status.translate = "VALIDATED";
  return "continue";
}

// ---------- the chain test: the stitched workflow, not just its segments (Task W1, ruling R-W1) ----------

/** What the orchestrator reads from `workflows/<wf>/validation_workflow.json`. */
interface ChainReport {
  verdict?: string;
  needs_human?: boolean;
  divergence_kind?: "boundary" | "chain_drift" | null;
  first_divergence?: { segment?: string; stream?: string | null; output?: string | null; set?: string } | null;
}

/**
 * After every segment PASSed on its own (fed golden intermediates), run the chain once
 * (`validate_workflow.py <wf>`: every segment on its upstream segments' actual output). A PASS lets
 * translate reach VALIDATED. Ruling R-W1 routes a FAIL: a `boundary` divergence gets ONE fixer round
 * on the segment where the chain first diverged — then that segment's own compile / review /
 * validate, then the chain again; a `chain_drift` (every boundary within tolerance, a final output
 * not) is accumulated tolerance, an approval question for a human, never a fixer task.
 */
async function chainCheck(env: Env, m: Manifest): Promise<Step> {
  const park = (why: string): Step => {
    m.status.translate = "NEEDS_HUMAN";
    reasons(m).translate = why;
    env.log(`${m.id}: translate → NEEDS_HUMAN (${why})`);
    return "stop";
  };
  const reportFile = wfDir(env.root, m.id, "validation_workflow.json");
  let fixedSegment: string | undefined;
  for (let round = 0; round < 2; round++) {
    // M1 (fix round 1): a report on disk must belong to THIS call; an older one is never acted on.
    await rm(reportFile, { force: true });
    const ran = await env.py("scripts/validate_workflow.py", [m.id]);
    if (ran.code === 2) {
      env.log(`${m.id}: validate_workflow.py exited 2 — ${ran.err.trim()}`);
      return park("chain: script-error");
    }
    if (!(await fileExists(reportFile))) {
      env.log(`${m.id}: validate_workflow.py exited ${ran.code} and wrote no validation_workflow.json — ${ran.err.trim()}`);
      return park("chain: script-error");
    }
    const report = await readJsonOr<ChainReport>(reportFile, {});
    // M6: needs_human wins over a passing verdict too, as it does per segment (task-15-int ruling 4).
    if (ran.ok) return report.needs_human ? park("chain: needs_human") : "continue";
    const at = report.first_divergence ?? {};
    if (report.divergence_kind === "chain_drift") return park(`chain-drift: ${at.output ?? at.stream ?? "unknown output"}`);
    if (report.needs_human) return park("chain: needs_human");
    if (round === 1) {
      // M4: where the chain still diverges, and which segment the one round repaired.
      return park(`chain: ${at.segment ?? "?"} ${at.stream ?? "(raised)"} after 1 fixer round on ${fixedSegment}`);
    }
    if (!at.segment) return park("chain: FAIL with no first_divergence");
    const segment = at.segment;
    // I1 (fix round 1): the fixer is about to rewrite a segment the manifest says PASSed. Its PASS is
    // withdrawn and saved first, so a crash anywhere in the round leaves that segment to be translated
    // and gated again on resume — never skipped as PASSed while its procedure is unchecked.
    delete m.segment_status![segment];
    await saveManifest(env.root, m);
    // M3: the segment's own validation.json PASSes by construction here (it was fed golden
    // intermediates), so the task points at the chain report instead of that report's diagnosis.
    const repairTask =
      `Repair segment ${segment} of ${m.id}: the stitched workflow (scripts/validate_workflow.py) diverges first at ` +
      `${segment}/${at.stream ?? "(it raised)"} on golden set ${at.set} — read first_divergence and this segment's diff ` +
      `clusters in workflows/${m.id}/validation_workflow.json before anything else. This segment's own validation.json ` +
      `PASSes, as expected: it was fed golden intermediates, while the chain ran it on its upstream segments' actual ` +
      `output. Fix what the chain report points at and keep that per-segment PASS.`;
    const fixed = await migrateSegment(env, m, segment, { firstIteration: 1, iterations: 2, repairTask });
    fixedSegment = segment;
    m.segment_status![segment] = fixed.verdict;
    if (!String(fixed.verdict).startsWith("PASS")) return park(`chain: ${segment}: ${fixed.reason ?? "needs_human"}`);
    await saveManifest(env.root, m);
  }
  return park("chain: FAIL");
}

async function stageTranslate(env: Env, m: Manifest): Promise<Step> {
  const order = await readOrder(env, m.id);
  if (order.flat().length === 0) {
    m.status.translate = "NEEDS_HUMAN";
    reasons(m).translate = "no-segments";
    env.log(`${m.id}: segments/order.json lists no segments`);
    return "stop";
  }
  if (m.output_kind === "dbt") return await translateDbt(env, m, order);

  m.segment_status ??= {};

  // F11 (crash window): a segment can be on disk as NEEDS_HUMAN while status.translate itself
  // never got set — either from a manifest saved by an older build (segment_status was written
  // before status.translate in the same wave), or a process that died in the gap between the two
  // writes. Such a segment is exactly as parked as status.translate = NEEDS_HUMAN would be: it
  // must not be silently retried by a plain run, only reopened deliberately (--from-stage, which
  // clears segment_status below).
  const recordedStuck = order.flat().filter((segment) => m.segment_status![segment] === "NEEDS_HUMAN");
  if (recordedStuck.length > 0) {
    m.status.translate = "NEEDS_HUMAN";
    reasons(m).translate = recordedStuck.map((segment) => `${segment}: needs_human (recorded)`).join("; ");
    env.log(`${m.id}: translate has a recorded NEEDS_HUMAN segment (${recordedStuck.join(", ")}) — stopping the workflow`);
    return "stop";
  }

  for (const [index, wave] of order.entries()) {
    const results = await Promise.all(
      wave.map((segment): Promise<SegmentOutcome> => {
        const recorded = m.segment_status![segment];
        // F11: a segment already PASSed (in an earlier wave-by-wave pass of THIS SAME stage,
        // interrupted before the stage as a whole finished) is never re-translated — no agent
        // call, no script, proc.sql untouched, its verdict kept exactly as recorded.
        if (typeof recorded === "string" && recorded.startsWith("PASS")) {
          return Promise.resolve({ verdict: recorded });
        }
        return migrateSegment(env, m, segment);
      }),
    );
    wave.forEach((segment, i) => {
      m.segment_status![segment] = results[i].verdict;
    });

    const failed = wave.map((segment, i) => ({ segment, result: results[i] })).filter(({ result }) => result.verdict === "NEEDS_HUMAN");
    if (failed.length > 0) {
      // F11: status.translate (and its reason) is set BEFORE the save just below, which is the
      // one that records this wave's segment_status — a crash between the two can no longer leave
      // segment_status on disk without the stage itself being marked NEEDS_HUMAN to match.
      m.status.translate = "NEEDS_HUMAN";
      reasons(m).translate = failed.map(({ segment, result }) => `${segment}: ${result.reason ?? "needs_human"}`).join("; ");
      await saveManifest(env.root, m);
      env.log(`${m.id}: wave ${index + 1} needs a human — stopping the workflow`);
      return "stop";
    }
    await saveManifest(env.root, m);
  }

  // Task W1: every segment PASSed alone; the stitched whole must PASS too before master.sql.
  if ((await chainCheck(env, m)) === "stop") return "stop";

  const master = wfDir(env.root, m.id, "procs", "master.sql");
  await mkdir(path.dirname(master), { recursive: true });
  // G3 (fix round 1): a procedures workflow has no dbt deployment — a README.md left by an earlier
  // dbt run of the same workflow would tell a human to `dbt run` something that no longer applies.
  await rm(path.join(path.dirname(master), "README.md"), { force: true });
  await writeFile(master, masterSql(m.id, order), "utf8");
  m.status.translate = "VALIDATED";
  return "continue";
}

async function stageDocument(env: Env, m: Manifest): Promise<Step> {
  // Ruling R-C1: the record carries a Deployment section per output kind, pointed at the one
  // artefact that says how that kind deploys — never deployed from the agent's session.
  const task =
    m.output_kind === "dbt"
      ? `Document ${m.id}: write workflows/${m.id}/docs/migration.md from the parsed dag, the contracts, ` +
        `workflows/${m.id}/dbt/translation_notes.md and the validation reports. Its Deployment section is the dbt run ` +
        `command in workflows/${m.id}/procs/README.md. Restate only what those artifacts say.`
      : `Document ${m.id}: write workflows/${m.id}/docs/migration.md from the parsed dag, the contracts, the ` +
        `translation notes and the validation reports. Its Deployment section names procs/master.sql and every ` +
        `segments/<seg>/proc.sql. Restate only what those artifacts say.`;
  const result = await runAgent(env, m, "documenter", "document", task, {}, () =>
    fileExists(wfDir(env.root, m.id, "docs", "migration.md")),
  );
  if (!result.ok) return escalate(env, m, "document", result);
  m.status.document = "DONE";
  return "continue";
}

/** Why this run does not use GitHub, or undefined when it may: `gh: disabled` when GitHub integration
 * is off (the default; `github.enabled` or `--gh` turns it on), `gh not installed` when it is on but
 * `gh` did not answer. Either way the stage carries on exactly as before (Task P4 fix round 1, B3). */
function ghUnavailable(env: Env): string | undefined {
  if (!env.ghEnabled) return "gh: disabled";
  if (!env.hasGh) return "gh not installed";
  return undefined;
}

async function stagePr(env: Env, m: Manifest): Promise<Step> {
  const noGh = ghUnavailable(env);
  if (noGh) {
    env.log(`${m.id}: ${noGh}; skipping PR`);
    return "continue";
  }
  const created = await env.sh("gh", [
    "pr",
    "create",
    "--fill",
    "--title",
    `migrate(${m.id}): ${m.tier ?? "tier unknown"}, ${m.segments?.length ?? 0} segments`,
  ]);
  if (!created.ok) {
    env.log(`${m.id}: gh pr create failed: ${created.err.trim()}`);
    return "continue";
  }
  m.status.pr = "OPEN";
  const url = created.out.trim().split("\n").pop();
  if (url?.startsWith("http")) m.pr = url;
  return "continue";
}

const RUNNERS: Record<Stage, (env: Env, m: Manifest) => Promise<Step>> = {
  parse: stageParse,
  intake: stageIntake,
  analyze: stageAnalyze,
  golden: stageGolden,
  translate: stageTranslate,
  document: stageDocument,
  pr: stagePr,
};

// ---------- the loop over stages ----------

/** Stages a tier-T3 workflow never reaches: docs/spec/01-copilot-setup.md §3's state diagram
 * draws `analyze --> MANUAL: tier T3` as a dead end with no outgoing edge at all, unlike
 * `analyze --> golden: contracts written`. stageAnalyze's own "stop" keeps the FIRST pass from
 * ever reaching golden's shouldRun check, but a LATER migrateWorkflow call restarts the stage
 * loop from the top; since golden has no status of its own to be terminal-good about, it would
 * otherwise look exactly like a stage that simply hasn't run yet and get attempted anyway. */
const NOT_APPLICABLE_FOR_T3: Stage[] = ["golden", "document", "pr"];

/** A stage whose status is one of these was escalated on an earlier run and is PARKED: a human
 * has to look at it (or a fresh `--from-stage` has to say the situation changed) before the
 * workflow moves again. It is deliberately status-only, not per-stage like `TERMINAL_GOOD` --
 * NEEDS_HUMAN can land on intake/analyze/translate/document, QUARANTINED only on parse and
 * BLOCKED on intake or golden, and all of them mean the same thing wherever they land. */
const ESCALATED = ["NEEDS_HUMAN", "QUARANTINED", "BLOCKED"];

function isParked(status: string | undefined): boolean {
  return status !== undefined && ESCALATED.includes(status);
}

function shouldRun(m: Manifest, stage: Stage): boolean {
  if (m.tier === "T3" && NOT_APPLICABLE_FOR_T3.includes(stage)) return false;
  const status = m.status[stage];
  if (isParked(status)) return false;
  return !TERMINAL_GOOD[stage].includes(status ?? "");
}

/** `--from-stage S` reopens S and everything after it, AND grants a fresh tool-call budget
 * (coordinator ruling, F8): every role's cumulative `toolCalls` is reset to 0 so an over-budget
 * workflow that could otherwise never be revived (the budget in `toolCallsUsed` never expires on
 * its own, and a plain re-run would just re-park at the same NEEDS_HUMAN) can be deliberately
 * reopened by an operator. `lastMs` is kept -- it names a session's own duration, not spend, and
 * has nothing to do with the budget. */
export function clearFromStage(env: Env, m: Manifest, from?: Stage): void {
  if (!from) return;
  const reopened = STAGES.slice(STAGES.indexOf(from));
  for (const stage of reopened) {
    delete m.status[stage];
    if (m.reasons) delete m.reasons[stage];
  }
  // F11: --from-stage translate (or any earlier stage) also clears segment_status, so a segment
  // this run redoes deliberately is not skipped as "already PASSed" by stageTranslate's
  // crash-resume logic, and a segment recorded NEEDS_HUMAN does not still park the reopened stage.
  if (reopened.includes("translate") && m.segment_status) delete m.segment_status;
  const before = toolCallsUsed(m);
  if (before > 0) {
    for (const entry of Object.values(m.metrics ?? {})) {
      const role = entry as { toolCalls?: unknown } | null;
      if (role && typeof role.toolCalls === "number") role.toolCalls = 0;
    }
    env.log(`${m.id}: --from-stage ${from} reset the tool-call budget (was ${before}, now 0)`);
  }
}

/** The one line a parked workflow logs on a plain run: which stage, what it is parked at, why
 * (when a reason was recorded) and the exact command that reopens it. */
function logParked(env: Env, m: Manifest, stage: Stage): void {
  const status = m.status[stage];
  const reason = m.reasons?.[stage];
  env.log(
    `${m.id}: ${stage} is parked at ${status}${reason ? ` (${reason})` : ""} — ` +
      `resume with --from-stage ${stage} --only ${m.id}`,
  );
}

export function plannedStages(m: Manifest, opts: RunOptions): Stage[] {
  const planned: Stage[] = [];
  for (const stage of STAGES) {
    if (isParked(m.status[stage])) break;
    if (shouldRun(m, stage)) planned.push(stage);
    if (opts.stopAfter === stage) break;
  }
  return planned;
}

/**
 * Drive one workflow as far as its state allows. Returns the manifest, which is also saved
 * after every stage and once more in `finally`, so a crash cannot lose the progress made.
 */
export async function migrateWorkflow(env: Env, id: string, opts: RunOptions): Promise<Manifest> {
  const m = await loadManifest(env.root, id);
  clearFromStage(env, m, opts.fromStage);

  if (opts.dryRun) {
    const planned = plannedStages(m, opts);
    env.log(`${id}: would run ${planned.length ? planned.join(" → ") : "nothing"}`);
    return m;
  }

  // Clear on disk too: a stage that re-reads the manifest after a script would otherwise
  // merge the very statuses --from-stage just removed straight back in.
  if (opts.fromStage) await saveManifest(env.root, m);

  try {
    for (const stage of STAGES) {
      // A stage already parked at an escalated status stops the workflow here, exactly as it did
      // on the run that escalated it -- no script or agent runs for it or for anything after it.
      if (isParked(m.status[stage])) {
        logParked(env, m, stage);
        break;
      }
      if (shouldRun(m, stage)) {
        const step = await RUNNERS[stage](env, m);
        await saveManifest(env.root, m);
        if (step === "stop") break;
      }
      if (opts.stopAfter === stage) break;
    }
  } finally {
    await saveManifest(env.root, m);
  }
  return m;
}

/** One procedure that calls every segment in `order.json` wave order (contract C4 shape). */
export function masterSql(wfId: string, order: string[][]): string {
  const wf = wfId.replace(/_/g, "").toUpperCase();
  const params = "SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING";
  const body = order
    .map((wave, index) => {
      const calls = wave.map((segment) => `  CALL MIG_WORK.${wf}_${segment.toUpperCase()}(:SRC_DB, :SRC_SCHEMA, :TGT_DB, :TGT_SCHEMA, :RUN_ID);`);
      return [`  -- wave ${index + 1}`, ...calls].join("\n");
    })
    .join("\n");
  return [
    `-- Generated by orchestrate.ts from segments/order.json. Never run against Snowflake by this repo.`,
    `CREATE OR REPLACE PROCEDURE MIG_WORK.${wf}_MASTER(${params})`,
    `RETURNS STRING LANGUAGE SQL`,
    `EXECUTE AS CALLER`,
    `AS`,
    // Snowflake CLI, SnowSQL and the Python connector's execute_stream/execute_string do not parse
    // a Snowflake Scripting block unless it is delimited: the body travels in `$$ … $$`, exactly as
    // every segment's proc.sql does.
    `$$`,
    `BEGIN`,
    body,
    `  RETURN 'OK';`,
    `END;`,
    `$$;`,
    ``,
  ].join("\n");
}
