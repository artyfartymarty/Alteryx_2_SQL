// The AgentRunner seam: everything except a live model call is exercised by MockRunner.
//
// MockRunner replays the canned artifacts a human wrote under samples/<wf>/canned/ (Task 13),
// so the state machine, the file contracts and the fix loop can be tested offline. It never
// invents a number or a verdict: the validator role runs the real validator script
// (validate_segment.py, validate_snowpark.py, or validate_dbt.py for a dbt workflow's project).
//
// CopilotRunner creates one disposable session per call, selects the custom agent by role and
// lets the hooks in hooks.ts enforce the permission policy.
import { appendFile, copyFile, mkdir, readdir, stat } from "node:fs/promises";
import path from "node:path";
import type { CopilotClient, CustomAgentConfig, SessionConfig } from "@github/copilot-sdk";
import {
  AUDIT_ARG_LIMIT,
  CONTEXT_OVERFLOW,
  errorText,
  hooksFor,
  NOTES_ROLES,
  notesReminder,
  RATE_LIMIT,
  recordMetrics,
  redact,
} from "./hooks.ts";
import { readJsonOr, wfDir, writeJson } from "./manifest.ts";
import type {
  AgentCtx,
  AgentError,
  AnalyzerBatch,
  AgentResult,
  AgentRunner,
  Env,
  Manifest,
  OrchestratorConfig,
  Profile,
  Role,
} from "./types.ts";

type UserInputHandler = NonNullable<SessionConfig["onUserInputRequest"]>;
type UserInputRequest = Parameters<UserInputHandler>[0];
type UserInputResponse = Awaited<ReturnType<UserInputHandler>>;

export const USER_UNAVAILABLE =
  "The user is not available. Record this as an unchecked item in open_questions.md and continue.";

interface Outcome {
  ok: boolean;
  error?: AgentError;
  detail?: string;
}

const OK: Outcome = { ok: true };
const missing = (what: string): Outcome => ({ ok: false, error: "missing-output", detail: `no canned ${what}` });

async function exists(file: string): Promise<boolean> {
  try {
    await stat(file);
    return true;
  } catch {
    return false;
  }
}

async function copyInto(from: string, to: string): Promise<boolean> {
  if (!(await exists(from))) return false;
  await mkdir(path.dirname(to), { recursive: true });
  await copyFile(from, to);
  return true;
}

async function filesUnder(dir: string): Promise<string[]> {
  const out: string[] = [];
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await filesUnder(full)));
    else out.push(full);
  }
  return out.sort();
}

/** `fix-loop:seg_01` / `never-fixed:seg_01` / `recovery-fails`. */
function scenarioFor(scenario: string | undefined, kind: string): { on: boolean; segment?: string } {
  if (!scenario) return { on: false };
  const [name, segment] = scenario.split(":");
  return name === kind ? { on: true, segment } : { on: false };
}

export class MockRunner implements AgentRunner {
  readonly root: string;
  readonly samplesDir: string;
  readonly scenario?: string;
  private env?: Env;

  constructor(root: string, samplesDir: string, scenario?: string) {
    this.root = root;
    this.samplesDir = samplesDir;
    this.scenario = scenario;
  }

  /** The runner is built before the env that owns it, so the seam is closed here. */
  attach(env: Env): void {
    this.env = env;
  }

  async run(role: Role, wf: Manifest, _task: string, ctx?: AgentCtx): Promise<AgentResult> {
    const started = Date.now();
    const outcome = await this.replay(role, wf, ctx ?? {});
    return { ...outcome, toolCalls: 0, ms: Date.now() - started };
  }

  private canned(wf: Manifest, ...rest: string[]): string {
    return path.join(this.samplesDir, wf.id, "canned", ...rest);
  }

  /** The first broken variant a human wrote for this segment, in name order — whatever its
   * extension: a Snowpark segment's deliberately-wrong first attempt is a `.py` file, and
   * `broken_sql/` (the directory name predates Snowpark) holds both kinds. */
  private async brokenVariant(wf: Manifest, segment: string): Promise<string | undefined> {
    const dir = path.join(this.samplesDir, wf.id, "broken_sql", segment);
    return (await filesUnder(dir))[0];
  }

  private async replay(role: Role, wf: Manifest, ctx: AgentCtx): Promise<Outcome> {
    // A dbt workflow's translate-stage roles act on the whole project, never on one segment
    // (output-targets design §6); every other role replays exactly as it does for procedures.
    if (ctx.dbt) {
      switch (role) {
        case "translator":
        case "fixer":
          return await this.replayDbt(role, wf, ctx.iteration ?? 0);
        case "reviewer":
          return (await copyInto(this.canned(wf, "review.json"), wfDir(this.root, wf.id, "dbt", "review.json")))
            ? OK
            : missing("review.json");
        case "validator":
          return await this.runValidator(wf, "scripts/validate_dbt.py", [wf.id]);
        default:
          break;
      }
    }
    const segment = ctx.segment ?? "";
    switch (role) {
      case "intake":
        return (await copyInto(this.canned(wf, "intake", "plan.md"), wfDir(this.root, wf.id, "intake", "plan.md")))
          ? OK
          : missing("intake/plan.md");

      case "analyzer":
        return ctx.batch ? await this.replayAnalyzerBatch(wf, ctx.batch) : await this.replayAnalyzer(wf);

      case "translator":
      case "fixer":
        return await this.replaySql(role, wf, segment, ctx.iteration ?? 0);

      case "reviewer":
        return (await copyInto(
          this.canned(wf, "segments", segment, "review.json"),
          wfDir(this.root, wf.id, "segments", segment, "review.json"),
        ))
          ? OK
          : missing(`segments/${segment}/review.json`);

      case "validator":
        return await this.replayValidator(wf, segment);

      case "documenter":
        return (await copyInto(this.canned(wf, "docs", "migration.md"), wfDir(this.root, wf.id, "docs", "migration.md")))
          ? OK
          : missing("docs/migration.md");

      case "parser-recovery":
        return await this.replayRecovery(wf);

      default:
        return { ok: false, error: "error", detail: `no canned artifacts for role ${role}` };
    }
  }

  private async replayAnalyzer(wf: Manifest): Promise<Outcome> {
    const unsupported = this.canned(wf, "unsupported.json");
    if (!(await exists(unsupported))) return missing("unsupported.json");
    await copyInto(this.canned(wf, "analysis.md"), wfDir(this.root, wf.id, "analysis.md"));
    await copyInto(unsupported, wfDir(this.root, wf.id, "unsupported.json"));
    const tier = (await readJsonOr<{ tier?: string }>(unsupported, {})).tier;
    if (tier === "T1" || tier === "T2" || tier === "T3") wf.tier = tier;
    for (const contract of await filesUnder(this.canned(wf, "segments"))) {
      if (path.basename(contract) !== "contract.json") continue;
      const segment = path.basename(path.dirname(contract));
      await copyInto(contract, wfDir(this.root, wf.id, "segments", segment, "contract.json"));
    }
    return OK;
  }

  /**
   * One call of a batched analyzer (Task W2): only the batch's own canned contracts, and its two
   * fragments — `analysis/<id>.md` and `analysis/<id>.unsupported.json` — copied from the canned
   * whole-workflow `analysis.md` / `unsupported.json`. Never the stitched files, and never the tier:
   * `scripts/stitch_analysis.py` writes those, and the orchestrator reads the tier from its output.
   */
  private async replayAnalyzerBatch(wf: Manifest, batch: AnalyzerBatch): Promise<Outcome> {
    const unsupported = this.canned(wf, "unsupported.json");
    if (!(await exists(unsupported))) return missing("unsupported.json");
    const analysis = this.canned(wf, "analysis.md");
    if (!(await exists(analysis))) return missing("analysis.md");
    await copyInto(analysis, wfDir(this.root, wf.id, "analysis", `${batch.id}.md`));
    await copyInto(unsupported, wfDir(this.root, wf.id, "analysis", `${batch.id}.unsupported.json`));
    for (const segment of batch.segments) {
      await copyInto(
        this.canned(wf, "segments", segment, "contract.json"),
        wfDir(this.root, wf.id, "segments", segment, "contract.json"),
      );
    }
    return OK;
  }

  /**
   * Replay the translator/fixer for one segment. The artefact is `proc.py` when the canned tree
   * has one (a Snowpark segment — its `proc.sql` is rendered by `scripts/render_snowpark.py`, so
   * the mock must never write one), else `proc.sql` as before. A broken variant is served under
   * its OWN extension, so a `.py` variant lands on `proc.py`.
   */
  private async replaySql(role: Role, wf: Manifest, segment: string, iteration: number): Promise<Outcome> {
    const into = (name: string) => wfDir(this.root, wf.id, "segments", segment, name);
    const fixLoop = scenarioFor(this.scenario, "fix-loop");
    const neverFixed = scenarioFor(this.scenario, "never-fixed");
    const serveBroken =
      (role === "translator" && iteration === 0 && (fixLoop.segment === segment || neverFixed.segment === segment)) ||
      (role === "fixer" && neverFixed.segment === segment);

    if (serveBroken) {
      const broken = await this.brokenVariant(wf, segment);
      if (!broken) return missing(`broken_sql/${segment}/*`);
      const target = into(broken.endsWith(".py") ? "proc.py" : "proc.sql");
      await mkdir(path.dirname(target), { recursive: true });
      await copyFile(broken, target);
    } else if (await exists(this.canned(wf, "segments", segment, "proc.py"))) {
      await copyInto(this.canned(wf, "segments", segment, "proc.py"), into("proc.py"));
    } else if (!(await copyInto(this.canned(wf, "segments", segment, "proc.sql"), into("proc.sql")))) {
      return missing(`segments/${segment}/proc.sql or proc.py`);
    }

    if (role === "translator") {
      await copyInto(
        this.canned(wf, "segments", segment, "translation_notes.md"),
        wfDir(this.root, wf.id, "segments", segment, "translation_notes.md"),
      );
      return OK;
    }

    const fixLog = wfDir(this.root, wf.id, "segments", segment, "fix_log.md");
    await mkdir(path.dirname(fixLog), { recursive: true });
    await appendFile(
      fixLog,
      `## iteration ${iteration} — ${segment}\n- symptom: see validation.json\n- fix: replayed from samples/${wf.id}/canned\n- status: ${serveBroken ? "UNFIXED" : "FIXED"}\n\n`,
      "utf8",
    );
    return OK;
  }

  /**
   * Replay the translator/fixer for a dbt workflow's whole project: every file under
   * `canned/dbt/` lands under `workflows/<wf>/dbt/` at the same relative path. On the dbt
   * fix-loop/never-fixed scenarios the first broken model in name order under `broken_sql/dbt/`
   * (design §6) is laid over it, and a fixer turn appends to `dbt/fix_log.md`.
   */
  private async replayDbt(role: Role, wf: Manifest, iteration: number): Promise<Outcome> {
    const from = this.canned(wf, "dbt");
    const files = await filesUnder(from);
    if (files.length === 0) return missing("dbt/**");
    const into = (rel: string) => wfDir(this.root, wf.id, "dbt", ...rel.split("/"));
    const relative = (dir: string, file: string) => path.relative(dir, file).split(path.sep).join("/");
    for (const file of files) await copyInto(file, into(relative(from, file)));

    const fixLoop = scenarioFor(this.scenario, "fix-loop");
    const neverFixed = scenarioFor(this.scenario, "never-fixed");
    const serveBroken =
      (role === "translator" && iteration === 0 && (fixLoop.segment === "dbt" || neverFixed.segment === "dbt")) ||
      (role === "fixer" && neverFixed.segment === "dbt");
    if (serveBroken) {
      const dir = path.join(this.samplesDir, wf.id, "broken_sql", "dbt");
      const broken = (await filesUnder(dir))[0];
      if (!broken) return missing("broken_sql/dbt/**");
      await copyInto(broken, into(relative(dir, broken)));
    }

    if (role === "fixer") {
      const fixLog = into("fix_log.md");
      await mkdir(path.dirname(fixLog), { recursive: true });
      await appendFile(
        fixLog,
        `## iteration ${iteration} — dbt project\n- symptom: see review.json and every segment's validation.json\n` +
          `- fix: replayed from samples/${wf.id}/canned/dbt\n- status: ${serveBroken ? "UNFIXED" : "FIXED"}\n\n`,
        "utf8",
      );
    }
    return OK;
  }

  /** The only role that runs a real script: verdicts and numbers never come from a mock. Which
   * script is the segment's own `contract.json.target` (output-targets design §6) — the SQL and
   * Snowpark validators take the same arguments and write the same `validation.json` shape. */
  private async replayValidator(wf: Manifest, segment: string): Promise<Outcome> {
    const contract = await readJsonOr<{ target?: string }>(
      wfDir(this.root, wf.id, "segments", segment, "contract.json"),
      {},
    );
    const script = contract.target === "snowpark" ? "scripts/validate_snowpark.py" : "scripts/validate_segment.py";
    return await this.runValidator(wf, script, [wf.id, segment]);
  }

  /** Spawn a validator script and classify its exit: 2 is a broken invocation (the agent could
   * not do its job), while 0 and 1 both mean the reports were written — the verdict inside them is
   * the orchestrator's to read, never the mock's. */
  private async runValidator(wf: Manifest, script: string, args: string[]): Promise<Outcome> {
    if (!this.env) return { ok: false, error: "error", detail: "MockRunner has no env; call attach(env)" };
    const result = await this.env.py(script, args);
    if (result.code === 2) {
      return { ok: false, error: "error", detail: `${path.basename(script)} exit 2: ${result.err.trim().slice(0, 200)}` };
    }
    return OK;
  }

  private async replayRecovery(wf: Manifest): Promise<Outcome> {
    if (scenarioFor(this.scenario, "recovery-fails").on) return OK; // diagnosed nothing; the re-parse will fail again
    const from = this.canned(wf, "parser-recovery");
    const files = await filesUnder(from);
    if (files.length === 0) return missing("parser-recovery/");
    for (const file of files) {
      const rel = path.relative(from, file).split(path.sep).join("/");
      const to = rel.startsWith("parsed/")
        ? wfDir(this.root, wf.id, ...rel.split("/"))
        : path.join(this.root, ...rel.split("/"));
      await copyInto(file, to);
    }
    const report = wfDir(this.root, wf.id, "parsed", "parse_report.json");
    const current = await readJsonOr<Record<string, unknown>>(report, {});
    await writeJson(report, { ...current, status: "RECOVERED" });
    return OK;
  }
}

export interface CopilotRunnerOptions {
  client: CopilotClient;
  root: string;
  config: OrchestratorConfig;
  profile: Profile;
  agents: CustomAgentConfig[];
}

export class CopilotRunner implements AgentRunner {
  readonly root: string;
  readonly config: OrchestratorConfig;
  readonly profile: Profile;
  readonly agents: CustomAgentConfig[];
  private readonly client: CopilotClient;
  private env?: Env;

  constructor(options: CopilotRunnerOptions) {
    this.client = options.client;
    this.root = options.root;
    this.config = options.config;
    this.profile = options.profile;
    this.agents = options.agents;
  }

  attach(env: Env): void {
    this.env = env;
  }

  async run(role: Role, wf: Manifest, task: string, ctx?: AgentCtx): Promise<AgentResult> {
    const env = this.env;
    if (!env) throw new Error("CopilotRunner has no env; call attach(env) before running agents");
    const profile = this.config.profiles[this.profile];
    const { hooks, state } = hooksFor(role, wf, env, ctx?.segment, ctx?.dbt, ctx?.batch);
    const started = Date.now();
    // `detail` is logged to the console and recorded in the manifest by stages.ts. errorText keeps
    // a whole error object as JSON (so classification sees everything), which means a key carried
    // by an upstream error could reach those places: redact, then bound, exactly like an audit line.
    const done = (error?: AgentError, detail?: string): AgentResult => ({
      ok: error === undefined,
      error,
      detail: detail === undefined ? undefined : redact(detail).slice(0, AUDIT_ARG_LIMIT),
      toolCalls: state.toolCalls,
      ms: Date.now() - started,
    });

    // Task N1 (live evidence, docs/live-smoke-test.md "Third live test"): a notes-keeping role's
    // session is told to write workflows/<wf>/notes/<role>.md, but its policy lane has no
    // directory creation -- every intake session tried to make the directory itself and was
    // denied. The orchestrator creates it instead, before the session starts. The path is built
    // only from this.root, the fixed "workflows" segment, wf.id and the fixed "notes" segment --
    // never from task text. A failure is logged (naming the workflow and role) and the session
    // still runs; it never throws out of run.
    if (NOTES_ROLES.includes(role)) {
      try {
        await mkdir(wfDir(this.root, wf.id, "notes"), { recursive: true });
      } catch (error) {
        env.log(
          `${wf.id}: ${role} could not create the notes directory: ${redact(errorText(error)).slice(0, AUDIT_ARG_LIMIT)}`,
        );
      }
    }

    let session;
    // Task W4: unsubscribed in `finally` below, on every exit path (a normal return, a thrown
    // error, a timeout) -- never left attached to a session this method is done with.
    const unsubscribe: (() => void)[] = [];
    try {
      session = await this.client.createSession({
        workingDirectory: this.root,
        model: profile.roleModels?.[role] ?? profile.model,
        reasoningEffort: profile.roleReasoningEffort?.[role] ?? profile.reasoningEffort,
        ...(profile.roleContextTiers?.[role] ? { contextTier: profile.roleContextTiers[role] } : {}),
        provider: profile.provider,
        mcpServers: this.config.mcpServers,
        customAgents: this.agents,
        agent: role,
        hooks,
        // The policy already decided in onPreToolUse, so the session never blocks on a prompt.
        onPermissionRequest: () => ({ kind: "approve-once" }),
        onUserInputRequest: (request) => this.answer(request, env),
      });
      // `session` is reassigned in this `let`, so a plain `const` alias is what lets these two
      // closures (invoked later, asynchronously, by the SDK) keep a definitely-assigned reference
      // instead of the widened "maybe still undefined" type `session` itself carries outside a
      // straight-line read (spike fact S5: session.d.ts:190's `on`, types.d.ts:2791's `send`).
      const activeSession = session;
      unsubscribe.push(
        activeSession.on("session.compaction_complete", (event) => {
          if (!event.data.success) {
            env.log(`${wf.id}: ${role} context compaction failed`);
            return;
          }
          state.compactions += 1;
          env.log(`${wf.id}: ${role} context compacted (${state.compactions} so far)`);
          // The reminder names only the fixed notes path -- never workflow-authored text -- and is
          // sent `mode: "immediate"` so it lands before whatever the agent does next, not queued
          // behind it. Fire-and-forget: a failed send is logged (fix round 1 minor), not thrown --
          // this handler runs inside the SDK's own event dispatch, not CopilotRunner.run's try/catch.
          if (NOTES_ROLES.includes(role)) {
            void activeSession
              .send({ prompt: notesReminder(role, wf.id), mode: "immediate" })
              .catch((error) => env.log(`${wf.id}: ${role} notes reminder failed to send: ${errorText(error)}`));
          }
        }),
      );
      unsubscribe.push(
        activeSession.on("assistant.usage", (event) => {
          const input = event.data.inputTokens;
          if (typeof input === "number" && input > state.peakInputTokens) state.peakInputTokens = input;
        }),
      );
      await session.sendAndWait({ prompt: task }, this.config.sessionTimeoutMs);
    } catch (error) {
      // The thrown error is judged FIRST, before state.denied: a crash that happens minutes
      // after earlier, already-recovered-from permission denials must not be misreported as
      // "denied" just because state.denied was set at some point earlier in the same session
      // (live evidence, task-16-report.md ATTEMPT 1/2 — a context-window overflow was
      // misclassified as "denied" this way). "denied" is the fallback classification, used only
      // when no other error signature explains why the session actually ended.
      const text = errorText(error);
      if (/timed?\s?out|timeout/i.test(text)) return done("timeout", text);
      // context-overflow is judged BEFORE rate-limit and wins when both signatures are present
      // (F7, final review), mirroring hooks.ts's onErrorOccurred: an anchored "429" can still
      // legitimately co-occur with an "exceeds the available context size" message, and the two
      // errors have different retry policies (RETRY_ONCE never retries context-overflow, but
      // rate-limit backs off and retries up to MAX_RATE_LIMIT_RETRIES times) -- misclassifying a
      // context overflow as a rate limit burns three paid retries that can never succeed.
      // state.contextOverflow (set from onErrorOccurred) is an OR-fallback for CONTEXT_OVERFLOW
      // on the thrown error's own text: the SDK's error-reporting hook may see the real signature
      // even when what sendAndWait itself throws does not.
      if (state.contextOverflow || CONTEXT_OVERFLOW.test(text)) return done("context-overflow", text);
      if (state.rateLimited || RATE_LIMIT.test(text)) return done("rate-limit", text);
      if (state.denied) return done("denied", state.denials.join("; "));
      return done("error", text);
    } finally {
      // Task W4: unsubscribed before disconnect, on every exit path -- including the timeout and
      // crash paths that reach this block via the catch above.
      for (const off of unsubscribe) off();
      await session?.disconnect().catch(() => undefined);
      // Every session's spend is recorded here, whether or not onSessionEnd fired for it (it was
      // observed live not to fire on this crash path at all — task-16-report.md ATTEMPT 2).
      // recordMetrics is idempotent per session, so a session where onSessionEnd DID already run
      // is not double-counted.
      recordMetrics(wf, role, state, Date.now() - started);
    }

    // A completed session that tried to leave its lane is still a prompt bug, not a success.
    if (state.denied) return done("denied", state.denials.join("; "));
    return done();
  }

  private async answer(request: UserInputRequest, env: Env): Promise<UserInputResponse> {
    if (!env.interactive) return { answer: USER_UNAVAILABLE, wasFreeform: true };
    const { createInterface } = await import("node:readline/promises");
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    try {
      const choices = request.choices?.length
        ? `\n${request.choices.map((choice, i) => `  ${i + 1}) ${choice}`).join("\n")}\n`
        : " ";
      const typed = (await rl.question(`\n${request.question}${choices}> `)).trim();
      const chosen = request.choices?.[Number(typed) - 1];
      return { answer: chosen ?? typed, wasFreeform: chosen === undefined };
    } finally {
      rl.close();
    }
  }
}
