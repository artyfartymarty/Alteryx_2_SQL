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
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileExists, loadManifest, readJsonOr, reloadManifest, saveManifest, wfDir } from "./manifest.ts";
import { STAGES } from "./types.ts";
import type { AgentError, AgentResult, Env, Manifest, Role, RunOptions, ShResult, Stage } from "./types.ts";

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
  const why = result.detail === "budget" ? "budget" : (result.error ?? "error");
  m.status[stage] = "NEEDS_HUMAN";
  reasons(m)[stage] = why;
  env.log(`${m.id}: ${stage} → NEEDS_HUMAN (${why})`);
  return "stop";
}

/** Exit 2 from a script is never a domain verdict: it is a broken invocation or a crash. */
function scriptError(env: Env, m: Manifest, stage: Stage, script: string, result: ShResult): Step {
  m.status[stage] = "NEEDS_HUMAN";
  reasons(m)[stage] = "script-error";
  const tail = `${result.out}\n${result.err}`.trim().split("\n").slice(-3).join(" | ");
  env.log(`${m.id}: ${script} exited 2 — ${tail}`);
  return "stop";
}

function domainFailure(env: Env, m: Manifest, stage: Stage, why: string, result: ShResult): Step {
  m.status[stage] = "NEEDS_HUMAN";
  reasons(m)[stage] = why;
  env.log(`${m.id}: ${stage} → NEEDS_HUMAN (${why}) — ${result.err.trim() || result.out.trim()}`);
  return "stop";
}

/**
 * One agent call with the spec's retry policy: back off on a rate limit, retry a missing
 * output or a timeout once, escalate a denied tool at once, and never start an agent once
 * the workflow is over its tool-call budget.
 */
async function runAgent(
  env: Env,
  m: Manifest,
  role: Role,
  task: string,
  ctx: { segment?: string; iteration?: number } = {},
  verify?: () => Promise<boolean>,
): Promise<AgentResult> {
  const budget = env.config.budgets.maxToolCallsPerWorkflow;
  let rateLimits = 0;
  let retried = false;
  for (;;) {
    const used = toolCallsUsed(m);
    if (used > budget) {
      env.log(`${m.id}: tool-call budget exceeded (${used} > ${budget}); not starting ${role}`);
      return { ok: false, error: "error", detail: "budget", toolCalls: 0, ms: 0 };
    }

    let result = await env.runner.run(role, m, task, ctx);
    if (result.ok && verify && !(await verify())) {
      result = { ...result, ok: false, error: "missing-output", detail: "the agent wrote no output file" };
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
    const result = await runAgent(
      env,
      m,
      "intake",
      `Run intake for ${m.id}. Read workflows/${m.id}/parsed/dag.json, workflows/${m.id}/intake/touchpoints.json ` +
        `and mappings/global.yaml, then write workflows/${m.id}/intake/mappings.yaml, open_questions.md and plan.md.`,
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
    if (env.hasGh) {
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
      env.log(`${m.id}: waiting for answers — gh not installed, answer the boxes in ${questions}`);
    }
    return "stop";
  }

  env.log(`${m.id}: intake ${status} — see ${questions}`);
  return "stop";
}

async function stageAnalyze(env: Env, m: Manifest): Promise<Step> {
  const segmented = await env.py("scripts/segment.py", [m.id]);
  await reloadManifest(env.root, m);
  if (segmented.code === 2) return scriptError(env, m, "analyze", "scripts/segment.py", segmented);
  if (!segmented.ok) return domainFailure(env, m, "analyze", "segmentation", segmented);

  const order = await readOrder(env, m.id);
  const segments = order.flat();
  const result = await runAgent(
    env,
    m,
    "analyzer",
    `Analyze ${m.id}: classify every tool in workflows/${m.id}/parsed/dag.json, confirm the cuts in ` +
      `workflows/${m.id}/segments/order.json, and write a contract.json for each segment plus analysis.md, ` +
      `unsupported.json and the tier in manifest.json.`,
    {},
    async () => {
      // The tier decision comes first (docs/spec/01-copilot-setup.md §3's state diagram:
      // `analyze --> MANUAL: tier T3` bypasses `analyze --> golden: contracts written` entirely,
      // and the spec's own migrateWorkflow skeleton sets status.analyze = "DONE" unconditionally
      // right after the analyzer runs, checking tier only afterwards). A T3 workflow is never
      // assigned a segment to translate, so it must never be held to the "every segment has a
      // contract.json" bar that only the T1/T2 -> golden path needs.
      const unsupported = await readJsonOr<{ tier?: string }>(wfDir(env.root, m.id, "unsupported.json"), {});
      if (unsupported.tier === "T3") return true;
      for (const segment of segments) {
        if (!(await fileExists(wfDir(env.root, m.id, "segments", segment, "contract.json")))) return false;
      }
      return segments.length > 0;
    },
  );
  if (!result.ok) return escalate(env, m, "analyze", result);
  await reloadManifest(env.root, m);

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
        `     imports those captures as golden set "normal" under workflows/${m.id}/golden/\n` +
        `then re-run`,
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

/** translate → review → validate for one segment, bounded by maxFixIterations. */
async function migrateSegment(env: Env, m: Manifest, segment: string): Promise<SegmentOutcome> {
  const iterations = env.config.maxFixIterations;
  const procSql = wfDir(env.root, m.id, "segments", segment, "proc.sql");
  const procPy = wfDir(env.root, m.id, "segments", segment, "proc.py");
  // The reason reported if every iteration is spent without a PASS or an early escalation —
  // updated as the loop learns more, so the final NEEDS_HUMAN names the LAST thing that actually
  // happened (F13: "seg_02: validation FAIL after 3 iterations", "seg_01: reviewer BLOCK …").
  let lastReason = `validation FAIL after ${iterations} iterations`;

  for (let iteration = 0; iteration < iterations; iteration++) {
    const role: Role = iteration === 0 ? "translator" : "fixer";
    const task =
      iteration === 0
        ? `Translate segment ${segment} of ${m.id} per workflows/${m.id}/segments/${segment}/contract.json and dag.json.`
        : `Repair segment ${segment} of ${m.id}: read workflows/${m.id}/segments/${segment}/validation.json and ` +
          `review.json first and change only what their diagnosis points at.`;
    const written = await runAgent(env, m, role, task, { segment, iteration }, async () =>
      (await fileExists(procSql)) || (await fileExists(procPy)),
    );
    if (!written.ok) {
      env.log(`${m.id} ${segment}: ${role} ${written.error ?? "error"} — segment needs a human`);
      return { verdict: "NEEDS_HUMAN", reason: agentFailureReason(role, written) };
    }

    const compiled = await env.py("scripts/compile_check.py", [m.id, segment]);
    if (compiled.code === 2) {
      env.log(`${m.id} ${segment}: compile_check.py exited 2 — ${compiled.err.trim()}`);
      return { verdict: "NEEDS_HUMAN", reason: "script-error" };
    }
    if (!compiled.ok) {
      env.log(`${m.id} ${segment}: compile check failed on iteration ${iteration} — ${compiled.err.trim()}`);
      lastReason = `compile check failed after ${iterations} iterations`;
      continue;
    }

    const reviewed = await runAgent(
      env,
      m,
      "reviewer",
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
      lastReason = `reviewer BLOCK after ${iterations} iterations`;
      continue;
    }

    const validated = await runAgent(
      env,
      m,
      "validator",
      `Validate segment ${segment} of ${m.id} against every golden set and write validation.json.`,
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
    lastReason = `validation FAIL after ${iterations} iterations`;
  }
  return { verdict: "NEEDS_HUMAN", reason: lastReason };
}

async function stageTranslate(env: Env, m: Manifest): Promise<Step> {
  const order = await readOrder(env, m.id);
  if (order.flat().length === 0) {
    m.status.translate = "NEEDS_HUMAN";
    reasons(m).translate = "no-segments";
    env.log(`${m.id}: segments/order.json lists no segments`);
    return "stop";
  }

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

  const master = wfDir(env.root, m.id, "procs", "master.sql");
  await mkdir(path.dirname(master), { recursive: true });
  await writeFile(master, masterSql(m.id, order), "utf8");
  m.status.translate = "VALIDATED";
  return "continue";
}

async function stageDocument(env: Env, m: Manifest): Promise<Step> {
  const result = await runAgent(
    env,
    m,
    "documenter",
    `Document ${m.id}: write workflows/${m.id}/docs/migration.md from the parsed dag, the contracts, the ` +
      `translation notes and the validation reports. Restate only what those artifacts say.`,
    {},
    () => fileExists(wfDir(env.root, m.id, "docs", "migration.md")),
  );
  if (!result.ok) return escalate(env, m, "document", result);
  m.status.document = "DONE";
  return "continue";
}

async function stagePr(env: Env, m: Manifest): Promise<Step> {
  if (!env.hasGh) {
    env.log(`${m.id}: gh not installed; skipping PR`);
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
    `BEGIN`,
    body,
    `  RETURN 'OK';`,
    `END;`,
    ``,
  ].join("\n");
}
