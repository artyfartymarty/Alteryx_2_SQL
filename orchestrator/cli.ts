// Command line, process plumbing and the workflow pool.
// Everything that talks to the operating system lives here; stages.ts only sees `Env`.
import { spawn } from "node:child_process";
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { loadAgents } from "./agents.ts";
import { loadManifest } from "./manifest.ts";
import { CopilotRunner, MockRunner } from "./runner.ts";
import { migrateWorkflow } from "./stages.ts";
import { STAGES } from "./types.ts";
import type { AgentRunner, Env, Manifest, OrchestratorConfig, Profile, RunOptions, ShResult, Stage, Tier } from "./types.ts";

export const CONFIG_FILE = "orchestrator.config.json";

/** Used when orchestrator.config.json is absent (a scratch root, or a test). */
export const DEFAULT_CONFIG: OrchestratorConfig = {
  python: ".venv/Scripts/python.exe",
  samplesDir: "samples",
  parallelism: 3,
  maxFixIterations: 3,
  maxParseRecovery: 2,
  sessionTimeoutMs: 1_200_000,
  golden: { producer: "simulator" },
  budgets: { maxToolCallsPerWorkflow: 400 },
  policy: { sandboxDatabases: ["MIGDB"] },
  profiles: {
    local: {
      provider: { type: "openai", baseUrl: "http://127.0.0.1:8080/v1", apiKey: "local" },
      model: "ternary-bonsai-2-27b",
      reasoningEffort: "medium",
    },
    hosted: {
      model: "gpt-5.6-luna",
      roleModels: {
        intake: "gpt-6-astra",
        analyzer: "gpt-6-astra",
        translator: "gpt-6-astra",
        fixer: "gpt-6-astra",
        "parser-recovery": "gpt-6-astra",
      },
    },
  },
};

/** A bad invocation: exit 2, having done nothing. */
export class UsageError extends Error {}

const STATUS_NEEDS_A_HUMAN = ["NEEDS_HUMAN", "QUARANTINED", "BLOCKED"];

function oneOf<T extends string>(value: string, allowed: readonly T[], flag: string): T {
  if (!(allowed as readonly string[]).includes(value)) {
    throw new UsageError(`${flag} must be one of ${allowed.join(", ")} (got ${value})`);
  }
  return value as T;
}

export function parseArgs(argv: string[]): RunOptions {
  const opts: RunOptions = {};
  for (let i = 0; i < argv.length; i++) {
    const raw = argv[i];
    const eq = raw.indexOf("=");
    const flag = raw.startsWith("--") && eq > 0 ? raw.slice(0, eq) : raw;
    const inline = raw.startsWith("--") && eq > 0 ? raw.slice(eq + 1) : undefined;
    const value = (): string => {
      const next = inline ?? argv[++i];
      if (next === undefined || next.startsWith("--")) throw new UsageError(`${flag} needs a value`);
      return next;
    };

    switch (flag) {
      case "--only":
        opts.only = value();
        break;
      case "--from-stage":
        opts.fromStage = oneOf(value(), STAGES, flag) as Stage;
        break;
      case "--stop-after":
        opts.stopAfter = oneOf(value(), STAGES, flag) as Stage;
        break;
      case "--tier":
        opts.tier = oneOf(value(), ["T1", "T2", "T3"] as const, flag) as Tier;
        break;
      case "--dry-run":
        opts.dryRun = true;
        break;
      case "--runner":
        opts.runner = oneOf(value(), ["mock", "copilot"] as const, flag);
        break;
      case "--profile":
        opts.profile = oneOf(value(), ["local", "hosted"] as const, flag);
        break;
      case "--interactive":
        opts.interactive = true;
        break;
      case "--no-interactive":
        opts.interactive = false;
        break;
      case "--root":
        opts.root = value();
        break;
      case "--scenario":
        opts.scenario = value();
        break;
      default:
        throw new UsageError(`unknown option ${flag}`);
    }
  }
  return opts;
}

/**
 * `orchestrator.config.json`, read from `root`. Absent is fine (every field falls back to
 * `DEFAULT_CONFIG`) — a scratch root or a test need not carry one at all. PRESENT but unreadable
 * as a JSON object (a syntax error, or a top-level value that parses but isn't an object — an
 * array would otherwise spread its indices as bogus config keys) is a usage error, not a silent
 * default: a hand-edited config that got corrupted should say so loudly, by name, rather than
 * quietly falling back to a `.venv/Scripts/python.exe` that resolves nowhere near where the
 * caller thinks it does.
 */
async function readConfigFile(root: string): Promise<Partial<OrchestratorConfig>> {
  const configPath = path.join(root, CONFIG_FILE);
  let text: string;
  try {
    text = await readFile(configPath, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
    throw new UsageError(`cannot read ${configPath}: ${error instanceof Error ? error.message : String(error)}`);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new UsageError(`${configPath} is not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new UsageError(`${configPath} must contain a JSON object, got ${Array.isArray(parsed) ? "an array" : typeof parsed}`);
  }
  return parsed as Partial<OrchestratorConfig>;
}

/**
 * Every stage shells out to `config.python`. When that path does not exist each script call fails
 * with ENOENT, which the stages can only read as "the script produced no output" — so a config
 * mistake would park every workflow at NEEDS_HUMAN with nothing in the trail naming the
 * interpreter. (A POSIX-style `/c/Users/…` path written from Git Bash is the usual way to get
 * here on Windows.) Refuse to start instead.
 */
export async function assertPythonExists(root: string, config: OrchestratorConfig): Promise<void> {
  const resolved = path.resolve(root, config.python);
  let isFile = false;
  try {
    isFile = (await stat(resolved)).isFile();
  } catch {
    isFile = false;
  }
  if (!isFile) {
    throw new UsageError(
      `the configured python interpreter does not exist: ${resolved} ` +
        `("python": ${JSON.stringify(config.python)} in orchestrator.config.json, resolved against --root). ` +
        `On Windows use a drive-letter path such as C:/…/.venv/Scripts/python.exe, not /c/….`,
    );
  }
}

export async function loadConfig(root: string): Promise<OrchestratorConfig> {
  const onDisk = await readConfigFile(root);
  return {
    ...DEFAULT_CONFIG,
    ...onDisk,
    golden: { ...DEFAULT_CONFIG.golden, ...(onDisk.golden ?? {}) },
    budgets: { ...DEFAULT_CONFIG.budgets, ...(onDisk.budgets ?? {}) },
    policy: { ...DEFAULT_CONFIG.policy, ...(onDisk.policy ?? {}) },
    profiles: {
      local: { ...DEFAULT_CONFIG.profiles.local, ...(onDisk.profiles?.local ?? {}) },
      hosted: { ...DEFAULT_CONFIG.profiles.hosted, ...(onDisk.profiles?.hosted ?? {}) },
    },
  };
}

function runProcess(
  command: string,
  args: string[],
  opts: { cwd: string; inheritStdio?: boolean },
): Promise<ShResult> {
  return new Promise((resolve) => {
    const child = spawn(command, args, {
      cwd: opts.cwd,
      stdio: opts.inheritStdio ? "inherit" : ["ignore", "pipe", "pipe"],
    });
    let out = "";
    let err = "";
    child.stdout?.on("data", (chunk) => {
      out += String(chunk);
    });
    child.stderr?.on("data", (chunk) => {
      err += String(chunk);
    });
    child.on("error", (error) => resolve({ ok: false, code: 127, out, err: String(error) }));
    child.on("close", (code) => resolve({ ok: code === 0, code: code ?? -1, out, err }));
  });
}

export function makeEnv(args: {
  root: string;
  config: OrchestratorConfig;
  runner: AgentRunner;
  interactive: boolean;
  hasGh: boolean;
}): Env {
  const { root, config } = args;
  return {
    root,
    config,
    runner: args.runner,
    interactive: args.interactive,
    hasGh: args.hasGh,
    py: (script, scriptArgs, opts) =>
      runProcess(path.resolve(root, config.python), [script, ...scriptArgs, "--root", root], {
        cwd: root,
        inheritStdio: opts?.inheritStdio,
      }),
    sh: (command, commandArgs) => runProcess(command, commandArgs, { cwd: root }),
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    log: (line) => console.log(line),
  };
}

async function workflowIds(root: string, opts: RunOptions): Promise<string[]> {
  let entries;
  try {
    entries = await readdir(path.join(root, "workflows"), { withFileTypes: true });
  } catch {
    throw new UsageError(`no workflows directory under ${root}`);
  }
  const all = entries
    .filter((entry) => entry.isDirectory() && !entry.name.startsWith("."))
    .map((entry) => entry.name)
    .sort();
  if (opts.only) {
    if (!all.includes(opts.only)) throw new UsageError(`unknown workflow ${opts.only} under ${path.join(root, "workflows")}`);
    return [opts.only];
  }
  return all;
}

async function pool<T>(items: T[], size: number, worker: (item: T) => Promise<void>): Promise<void> {
  const queue = [...items];
  const lanes = Array.from({ length: Math.max(1, Math.min(size, queue.length)) }, async () => {
    for (let next = queue.shift(); next !== undefined; next = queue.shift()) await worker(next);
  });
  await Promise.all(lanes);
}

function summarize(m: Manifest): string {
  const cells = STAGES.filter((stage) => m.status[stage] !== undefined).map((stage) => `${stage}=${m.status[stage]}`);
  return `${m.id}  ${cells.join(" ") || "not started"}`;
}

/**
 * Exit codes: 0 every selected workflow reached a terminal or parked state, 1 a workflow
 * ended NEEDS_HUMAN / QUARANTINED / BLOCKED, 2 a usage error or an unexpected failure.
 */
export async function main(argv: string[], deps: { env?: Env } = {}): Promise<number> {
  let opts: RunOptions;
  try {
    opts = parseArgs(argv);
  } catch (error) {
    console.error(`orchestrate: ${error instanceof Error ? error.message : String(error)}`);
    return 2;
  }

  let client: { stop(): Promise<unknown> } | undefined;
  try {
    const root = deps.env?.root ?? path.resolve(opts.root ?? process.cwd());
    const config = deps.env?.config ?? (await loadConfig(root));
    if (!deps.env) await assertPythonExists(root, config);
    const profile: Profile = opts.profile ?? "local";
    const interactive = deps.env?.interactive ?? opts.interactive ?? Boolean(process.stdin.isTTY);

    let ids = await workflowIds(root, opts);
    if (opts.tier) {
      // A corrupt manifest is not this filter's problem to report: leave that workflow selected
      // (undefined tier never excludes it) so its own migrateWorkflow call hits loadManifest
      // again below and reports the real CorruptManifestError against ITS id, instead of one
      // corrupt file aborting tier selection for every other workflow (F12).
      const tiers = await Promise.all(
        ids.map(async (id) => {
          try {
            return (await loadManifest(root, id)).tier;
          } catch {
            return undefined;
          }
        }),
      );
      ids = ids.filter((_, i) => tiers[i] === undefined || tiers[i] === opts.tier);
    }
    if (ids.length === 0) {
      console.log("orchestrate: no workflows selected");
      return 0;
    }

    let env = deps.env;
    if (!env) {
      const kind = opts.runner ?? "mock";
      let runner: MockRunner | CopilotRunner;
      if (kind === "mock") {
        runner = new MockRunner(root, path.resolve(root, config.samplesDir), opts.scenario);
      } else {
        const { CopilotClient } = await import("@github/copilot-sdk");
        const copilot = new CopilotClient();
        await copilot.start();
        client = copilot;
        runner = new CopilotRunner({ client: copilot, root, config, profile, agents: await loadAgents(root, profile) });
      }
      const hasGh = (await runProcess("gh", ["--version"], { cwd: root })).ok;
      env = makeEnv({ root, config, runner, interactive, hasGh });
      runner.attach(env);
      console.log(
        `orchestrate: root=${root} runner=${kind} profile=${profile} gh=${hasGh ? "yes" : "no"} ` +
          `interactive=${interactive ? "yes" : "no"} workflows=${ids.length}`,
      );
    }

    // A human can only answer one prompt at a time, so interactive runs are serial.
    const parallelism = interactive ? 1 : config.parallelism;
    const finished: Manifest[] = [];
    // A workflow whose migrateWorkflow call throws (a corrupt manifest.json above all — F12, but
    // also any other unexpected failure) is caught HERE, per workflow, so it cannot take the rest
    // of the pool down with it: the other lanes keep running, and this one is reported and folded
    // into the exit code below instead of unwinding the whole run.
    const failed: { id: string; message: string }[] = [];
    await pool(ids, parallelism, async (id) => {
      try {
        finished.push(await migrateWorkflow(env!, id, opts));
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        failed.push({ id, message });
        console.error(`orchestrate: ${id}: ${message}`);
      }
    });

    finished.sort((a, b) => a.id.localeCompare(b.id));
    for (const m of finished) console.log(summarize(m));
    const stuck = finished.filter((m) => Object.values(m.status).some((s) => STATUS_NEEDS_A_HUMAN.includes(s)));
    for (const m of stuck) console.log(`${m.id}: needs a human — ${JSON.stringify(m.reasons ?? {})}`);
    if (failed.length > 0) return 2;
    return stuck.length > 0 ? 1 : 0;
  } catch (error) {
    console.error(`orchestrate: ${error instanceof Error ? error.message : String(error)}`);
    return 2;
  } finally {
    await client?.stop().catch(() => undefined);
  }
}
