// The AgentRunner seam: everything except a live model call is exercised by MockRunner.
//
// MockRunner replays the canned artifacts a human wrote under samples/<wf>/canned/ (Task 13),
// so the state machine, the file contracts and the fix loop can be tested offline. It never
// invents a number or a verdict: the validator role runs the real scripts/validate_segment.py.
//
// CopilotRunner creates one disposable session per call, selects the custom agent by role and
// lets the hooks in hooks.ts enforce the permission policy.
import { appendFile, copyFile, mkdir, readdir, stat } from "node:fs/promises";
import path from "node:path";
import type { CopilotClient, CustomAgentConfig, SessionConfig } from "@github/copilot-sdk";
import { AUDIT_ARG_LIMIT, CONTEXT_OVERFLOW, errorText, hooksFor, RATE_LIMIT, recordMetrics, redact } from "./hooks.ts";
import { readJsonOr, wfDir, writeJson } from "./manifest.ts";
import type {
  AgentError,
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

  async run(
    role: Role,
    wf: Manifest,
    _task: string,
    ctx?: { segment?: string; iteration?: number },
  ): Promise<AgentResult> {
    const started = Date.now();
    const outcome = await this.replay(role, wf, ctx ?? {});
    return { ...outcome, toolCalls: 0, ms: Date.now() - started };
  }

  private canned(wf: Manifest, ...rest: string[]): string {
    return path.join(this.samplesDir, wf.id, "canned", ...rest);
  }

  /** The first broken variant a human wrote for this segment, in name order. */
  private async brokenSql(wf: Manifest, segment: string): Promise<string | undefined> {
    const dir = path.join(this.samplesDir, wf.id, "broken_sql", segment);
    const files = (await filesUnder(dir)).filter((f) => f.endsWith(".sql"));
    return files[0];
  }

  private async replay(role: Role, wf: Manifest, ctx: { segment?: string; iteration?: number }): Promise<Outcome> {
    const segment = ctx.segment ?? "";
    switch (role) {
      case "intake":
        return (await copyInto(this.canned(wf, "intake", "plan.md"), wfDir(this.root, wf.id, "intake", "plan.md")))
          ? OK
          : missing("intake/plan.md");

      case "analyzer":
        return await this.replayAnalyzer(wf);

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

  private async replaySql(role: Role, wf: Manifest, segment: string, iteration: number): Promise<Outcome> {
    const target = wfDir(this.root, wf.id, "segments", segment, "proc.sql");
    const fixLoop = scenarioFor(this.scenario, "fix-loop");
    const neverFixed = scenarioFor(this.scenario, "never-fixed");
    const serveBroken =
      (role === "translator" && iteration === 0 && (fixLoop.segment === segment || neverFixed.segment === segment)) ||
      (role === "fixer" && neverFixed.segment === segment);

    if (serveBroken) {
      const broken = await this.brokenSql(wf, segment);
      if (!broken) return missing(`broken_sql/${segment}/*.sql`);
      await mkdir(path.dirname(target), { recursive: true });
      await copyFile(broken, target);
    } else if (!(await copyInto(this.canned(wf, "segments", segment, "proc.sql"), target))) {
      return missing(`segments/${segment}/proc.sql`);
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

  /** The only role that runs a real script: verdicts and numbers never come from a mock. */
  private async replayValidator(wf: Manifest, segment: string): Promise<Outcome> {
    if (!this.env) return { ok: false, error: "error", detail: "MockRunner has no env; call attach(env)" };
    const result = await this.env.py("scripts/validate_segment.py", [wf.id, segment]);
    if (result.code === 2) {
      return { ok: false, error: "error", detail: `validate_segment.py exit 2: ${result.err.trim().slice(0, 200)}` };
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

  async run(
    role: Role,
    wf: Manifest,
    task: string,
    ctx?: { segment?: string; iteration?: number },
  ): Promise<AgentResult> {
    const env = this.env;
    if (!env) throw new Error("CopilotRunner has no env; call attach(env) before running agents");
    const profile = this.config.profiles[this.profile];
    const { hooks, state } = hooksFor(role, wf, env, ctx?.segment);
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

    let session;
    try {
      session = await this.client.createSession({
        workingDirectory: this.root,
        model: profile.roleModels?.[role] ?? profile.model,
        reasoningEffort: profile.reasoningEffort,
        provider: profile.provider,
        mcpServers: this.config.mcpServers,
        customAgents: this.agents,
        agent: role,
        hooks,
        // The policy already decided in onPreToolUse, so the session never blocks on a prompt.
        onPermissionRequest: () => ({ kind: "approve-once" }),
        onUserInputRequest: (request) => this.answer(request, env),
      });
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
