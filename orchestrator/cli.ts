// Command line, process plumbing and the workflow pool.
// Everything that talks to the operating system lives here; stages.ts only sees `Env`.
import { spawn } from "node:child_process";
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { loadAgents } from "./agents.ts";
import { MAX_ACT_DENIALS_PER_SESSION, MAX_READ_DENIALS_PER_SESSION } from "./hooks.ts";
import { loadManifest } from "./manifest.ts";
import { checkModels, configuredModels } from "./models.ts";
import type { CatalogModel, CliSubagentsConfig } from "./models.ts";
import { CopilotRunner, MockRunner } from "./runner.ts";
import { ExternalRunner } from "./external_runner.ts";
import { migrateWorkflow } from "./stages.ts";
import { ROLES, STAGES } from "./types.ts";
import type {
  AgentRunner, Env, Manifest, OrchestratorConfig, Profile, ProfileConfig, RunOptions, ShResult, Stage, Tier,
} from "./types.ts";

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
  budgets: {
    maxToolCallsPerWorkflow: 400,
    maxReadDenialsPerSession: MAX_READ_DENIALS_PER_SESSION,
    maxActDenialsPerSession: MAX_ACT_DENIALS_PER_SESSION,
  },
  policy: { sandboxDatabases: ["MIGDB"] },
  // GitHub is opt-in (Task P4 fix round 1, B3): with it off the orchestrator never invokes `gh` --
  // no issue for open questions, no pull request -- whether or not `gh` is installed. `--gh` turns
  // it on for one run.
  github: { enabled: false },
  analyzerBudgetChars: 60000,
  profiles: {
    local: {
      provider: { type: "openai", baseUrl: "http://127.0.0.1:8080/v1", apiKey: "local" },
      model: "ternary-bonsai-2-27b",
      reasoningEffort: "medium",
    },
    // One model for every role, the owner's policy (docs/handoff-copilot-models.md §1): no
    // roleModels here, so a config file that drops its own roleModels routes every role to `model`
    // (Task P4 fix round 1, B1). The id itself is a placeholder until scripts/dev/set_models.py.
    hosted: {
      model: "gpt-5.6-luna",
      reasoningEffort: "medium",
      roleContextTiers: {
        intake: "long_context",
        analyzer: "long_context",
        fixer: "long_context",
        "parser-recovery": "long_context",
        validator: "long_context",
      },
      roleReasoningEffort: {
        documenter: "low",
        reviewer: "low",
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
        opts.runner = oneOf(value(), ["mock", "copilot", "external"] as const, flag);
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
      case "--check-models":
        opts.checkModels = true;
        break;
      case "--gh":
        opts.gh = true;
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

/**
 * Task L1 (R1): the environment every agent session's runtime process runs with -- `base` (the
 * orchestrator's own environment by default) with the directory of the configured interpreter
 * (`config.python`, resolved against `root` exactly as `makeEnv`'s `py` and `assertPythonExists`
 * resolve it) put FIRST on PATH. A run root is a copy without `.venv` whose config names the
 * interpreter by absolute path, and `python` on the machine's own PATH is the system interpreter
 * without the project's packages; with this, `python scripts/<name>.py …` in an agent's shell runs
 * the project's interpreter, which is the form every agent file shows.
 *
 * Windows environment keys are case-insensitive (`Path` vs `PATH`): the existing key is replaced,
 * never a second one added. When `base` carries more than one case variant (a parent can pass such a
 * block), their entries are MERGED into the first key, in key order, and the others dropped (Task L1
 * fix round 1, M7), so exactly one remains and no directory is lost. On POSIX only `PATH` itself is
 * PATH. The interpreter's directory comes first, then every other entry in its original order, each
 * once: a repeat (in any case and with any trailing separator, on Windows) is dropped, so an entry
 * already naming the interpreter's directory is moved, not repeated, and applying this twice gives
 * the same result as once. Empty entries are dropped. `base` is never modified. `platform` exists so
 * both path conventions can be tested on either host.
 */
export function sessionEnvironment(
  config: Pick<OrchestratorConfig, "python">,
  root: string,
  base: Record<string, string | undefined> = process.env,
  platform: NodeJS.Platform = process.platform,
): Record<string, string | undefined> {
  const windows = platform === "win32";
  const paths = windows ? path.win32 : path.posix;
  const interpreterDir = paths.dirname(paths.resolve(root, config.python));
  const comparable = (entry: string): string => {
    const trimmed = entry.replace(windows ? /[\\/]+$/ : /\/+$/, "") || entry;
    return windows ? trimmed.replace(/\//g, "\\").toLowerCase() : trimmed;
  };

  const env: Record<string, string | undefined> = { ...base };
  const pathKeys = Object.keys(env).filter((key) => (windows ? key.toUpperCase() === "PATH" : key === "PATH"));
  const key = pathKeys[0] ?? "PATH";
  const merged = pathKeys.flatMap((name) => (env[name] ?? "").split(paths.delimiter));
  for (const duplicate of pathKeys.slice(1)) delete env[duplicate];
  const seen = new Set([comparable(interpreterDir)]);
  const entries: string[] = [];
  for (const entry of merged) {
    if (entry === "" || seen.has(comparable(entry))) continue;
    seen.add(comparable(entry));
    entries.push(entry);
  }
  env[key] = [interpreterDir, ...entries].join(paths.delimiter);
  return env;
}

/** The options every agent-session `CopilotClient` is constructed with (Task L1, R1): the SDK's
 * `CopilotClientOptions.env` -- "Environment variables to pass to the runtime process" -- set to
 * `sessionEnvironment`, so the runtime and every shell it starts find the project's interpreter as
 * `python`. (The SDK's in-process transport refuses `env`; the default stdio transport takes it.) */
export function copilotClientOptions(config: OrchestratorConfig, root: string): { env: Record<string, string | undefined> } {
  return { env: sessionEnvironment(config, root) };
}

/**
 * `config.json` (the Copilot CLI config copied into `COPILOT_HOME`; `--check-models` only reads
 * its `subagents.agents.<name>.model` map). Absent is fine — a scratch root need not carry one —
 * but present and unparsable is a usage error, the same treatment `readConfigFile` gives
 * `orchestrator.config.json`.
 */
async function readCliConfigFile(root: string): Promise<CliSubagentsConfig> {
  const configPath = path.join(root, "config.json");
  let text: string;
  try {
    text = await readFile(configPath, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
    throw new UsageError(`cannot read ${configPath}: ${error instanceof Error ? error.message : String(error)}`);
  }
  try {
    return JSON.parse(text) as CliSubagentsConfig;
  } catch (error) {
    throw new UsageError(`${configPath} is not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
}

/** The real catalog call `--check-models` makes when no `deps.listModels` is injected: a human's
 * own preflight, never run by an agent (docs/handoff-copilot-models.md §2 — the human signs in
 * with `copilot`, then `/login`, first). Mirrors scripts/dev/list_models.ts. */
async function liveListModels(): Promise<CatalogModel[]> {
  const { CopilotClient } = await import("@github/copilot-sdk");
  const client = new CopilotClient();
  await client.start();
  try {
    return await client.listModels();
  } finally {
    await client.stop();
  }
}

/**
 * `--check-models`: lists the hosted-profile catalog (via `deps.listModels`, or a live SDK call
 * when none is injected) and checks it against every model id this repo currently configures for
 * the hosted profile. Exits before any workflow is selected — this never touches `workflows/`.
 */
async function runCheckModels(
  root: string,
  config: OrchestratorConfig,
  profile: Profile,
  listModels: (() => Promise<CatalogModel[]>) | undefined,
): Promise<number> {
  if (profile !== "hosted") {
    throw new UsageError("--check-models requires --profile hosted (the local BYOK profile has no catalog to check)");
  }
  const cliConfig = await readCliConfigFile(root);
  const agents = await loadAgents(root, "hosted");
  const agentModels = Object.fromEntries(
    agents.filter((agent): agent is typeof agent & { model: string } => Boolean(agent.model)).map((agent) => [agent.name, agent.model]),
  );
  const configured = configuredModels(config, profile, agentModels, cliConfig);

  // What each role's session will actually ask for -- exactly runner.ts's createSession resolution.
  const profileConfig = config.profiles[profile];
  for (const role of ROLES) {
    console.log(`effective ${role}: ${profileConfig.roleModels?.[role] ?? profileConfig.model ?? "(none)"}`);
  }

  let catalog: CatalogModel[];
  try {
    catalog = await (listModels ?? liveListModels)();
  } catch (error) {
    throw new UsageError(
      `cannot list the model catalog (sign in first: copilot, then /login — docs/handoff-copilot-models.md §2): ` +
        `${error instanceof Error ? error.message : String(error)}`,
    );
  }

  const result = checkModels(configured, catalog);
  for (const entry of result.missing) console.log(`${entry.where}: ${entry.id} — missing`);
  for (const entry of result.disabled) console.log(`${entry.where}: ${entry.id} — disabled`);
  console.log(
    result.ok
      ? `orchestrate: every configured model id is enabled (${configured.length} checked)`
      : `orchestrate: ${result.missing.length} missing, ${result.disabled.length} disabled, of ${configured.length} configured`,
  );
  return result.ok ? 0 : 1;
}

/** The per-role maps of a profile. Each is one decision made as a whole (scripts/dev/set_models.py
 * writes all three from one command), so a file that defines the profile owns them outright. */
const ROLE_MAPS = ["roleModels", "roleContextTiers", "roleReasoningEffort"] as const;

/** A profile from the config file over its default. Scalars (`model`, `reasoningEffort`, `provider`)
 * fall back to the default one by one; the per-role maps are taken from the file whole or not at
 * all -- never merged key by key, and never inherited from the default when the file omits them --
 * so removing a map, or a role from a map, in the file removes it (Task P4 fix round 1, B1). A file
 * that does not define the profile gets the default unchanged. */
function profileOver(base: ProfileConfig, file: ProfileConfig | undefined): ProfileConfig {
  if (!file) return base;
  const merged: ProfileConfig = { ...base, ...file };
  for (const key of ROLE_MAPS) {
    if (file[key] === undefined) delete merged[key];
  }
  return merged;
}

export async function loadConfig(root: string): Promise<OrchestratorConfig> {
  const onDisk = await readConfigFile(root);
  const config: OrchestratorConfig = {
    ...DEFAULT_CONFIG,
    ...onDisk,
    golden: { ...DEFAULT_CONFIG.golden, ...(onDisk.golden ?? {}) },
    budgets: { ...DEFAULT_CONFIG.budgets, ...(onDisk.budgets ?? {}) },
    policy: { ...DEFAULT_CONFIG.policy, ...(onDisk.policy ?? {}) },
    github: { ...DEFAULT_CONFIG.github, ...(onDisk.github ?? {}) },
    profiles: {
      local: profileOver(DEFAULT_CONFIG.profiles.local, onDisk.profiles?.local),
      hosted: profileOver(DEFAULT_CONFIG.profiles.hosted, onDisk.profiles?.hosted),
    },
  };
  // Task L1 fix round 1 (M6): the read-denial budget decides whether a session parks, and a value
  // that is not a non-negative integer ("twenty", -1, 2.5, null) would make every comparison with it
  // false -- read denials would never park. A configuration error, stopped here (exit 2).
  // Task L6 (R2): the same for the attempted-action budget -- failing open there would mean attempted
  // actions never park a session.
  for (const key of ["maxReadDenialsPerSession", "maxActDenialsPerSession"] as const) {
    const budget: unknown = config.budgets[key];
    if (!Number.isInteger(budget) || (budget as number) < 0) {
      throw new UsageError(
        `${path.join(root, CONFIG_FILE)}: budgets.${key} must be a non-negative integer ` +
          `(got ${JSON.stringify(budget) ?? String(budget)})`,
      );
    }
  }
  // Fix round 2 (S5): the same for the workflow's tool-call budget, which must be a positive integer
  // -- `used > "400"` or `used > null` would never stop a workflow, and 0 would stop every one.
  const toolBudget: unknown = config.budgets.maxToolCallsPerWorkflow;
  if (!Number.isInteger(toolBudget) || (toolBudget as number) <= 0) {
    throw new UsageError(
      `${path.join(root, CONFIG_FILE)}: budgets.maxToolCallsPerWorkflow must be a positive integer ` +
        `(got ${JSON.stringify(toolBudget) ?? String(toolBudget)})`,
    );
  }
  // Live hardening, Task L7 (R1): `profiles.<name>.provider.maxPromptTokens` / `maxOutputTokens` reach
  // the SDK's `createSession` unchanged (runner.ts), so a value that is not a positive integer would
  // silently reach the SDK as garbage (a string, a negative number) instead of failing here, by name,
  // where a hand-edited config can actually be fixed. Either profile may set them (`checkModels`'s own
  // hosted-only guard is a separate thing), and absent is fine -- most profiles set neither.
  for (const name of ["local", "hosted"] as const) {
    const provider = config.profiles[name].provider;
    if (!provider) continue;
    for (const key of ["maxPromptTokens", "maxOutputTokens"] as const) {
      const value: unknown = provider[key];
      if (value !== undefined && (!Number.isInteger(value) || (value as number) <= 0)) {
        throw new UsageError(
          `${path.join(root, CONFIG_FILE)}: profiles.${name}.provider.${key} must be a positive integer ` +
            `(got ${JSON.stringify(value) ?? String(value)})`,
        );
      }
    }
  }
  // Live hardening, Task L9 (R1): `session.excludedTools` reaches the SDK's `createSession` unchanged
  // (runner.ts's sessionExcludedTools, appended after the fixed ALWAYS_EXCLUDED_BUILTIN_TOOLS list), so
  // a value that is not a list of non-empty strings would silently reach the SDK as garbage instead of
  // failing here, by name. Absent is fine -- the fixed list alone still reaches every session.
  if (config.session?.excludedTools !== undefined) {
    const list: unknown = config.session.excludedTools;
    if (!Array.isArray(list) || list.some((entry) => typeof entry !== "string" || entry.length === 0)) {
      throw new UsageError(
        `${path.join(root, CONFIG_FILE)}: session.excludedTools must be a list of non-empty strings ` +
          `(got ${JSON.stringify(list)})`,
      );
    }
    // Live hardening, Task L9 fix round 2 (L9 minor): a bare "*" is refused by the SDK's own
    // `createSession` (unlike an unknown plain tool name, which it silently ignores -- confirmed live
    // against the bundled runtime). `excludedTools`'s own doc comment
    // (node_modules/@github/copilot-sdk/dist/types.d.ts ~2004) names the only patterns it accepts:
    // source-qualified (`builtin:*`, `builtin:<name>`, `mcp:*`, `mcp:<name>`, `custom:*`, `custom:<name>`)
    // or a bare exact tool name -- a bare "*" is neither, and the ToolSet helper only ever produces the
    // qualified form (`addBuiltIn("*")` yields `"builtin:*"`, never a bare `"*"`). Refused here, by name,
    // rather than reaching `createSession` as a crash with no orchestrator-side diagnosis.
    if (list.includes("*")) {
      throw new UsageError(
        `${path.join(root, CONFIG_FILE)}: session.excludedTools may not contain a bare "*" (the SDK's ` +
          `createSession refuses it) -- use a source-qualified form instead, such as "builtin:*", "mcp:*" ` +
          `or "custom:*"`,
      );
    }
  }
  return config;
}

/** Whether this run may use GitHub, and whether `gh` is there to use. Off unless the config's
 * `github.enabled` or `--gh` turns it on; when off, `probe` (which runs `gh --version`) is never
 * called, so the orchestrator never invokes `gh` at all (Task P4 fix round 1, B3). */
export async function githubAccess(
  config: OrchestratorConfig,
  opts: Pick<RunOptions, "gh">,
  probe: () => Promise<boolean>,
): Promise<{ enabled: boolean; hasGh: boolean }> {
  const enabled = opts.gh === true || config.github?.enabled === true;
  return { enabled, hasGh: enabled ? await probe() : false };
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
    // A stream decoder, not String(chunk): a UTF-8 character split across two pipe chunks would
    // otherwise become two replacement characters (Task P4 fix round 2, M15). Every script writes
    // UTF-8 (scripts/lib/console.py).
    child.stdout?.setEncoding("utf8");
    child.stderr?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => {
      out += chunk;
    });
    child.stderr?.on("data", (chunk: string) => {
      err += chunk;
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
  ghEnabled: boolean;
}): Env {
  const { root, config } = args;
  return {
    root,
    config,
    runner: args.runner,
    interactive: args.interactive,
    hasGh: args.hasGh,
    ghEnabled: args.ghEnabled,
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
export async function main(
  argv: string[],
  deps: { env?: Env; listModels?: () => Promise<CatalogModel[]> } = {},
): Promise<number> {
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
    const profile: Profile = opts.profile ?? "local";
    // Checked before assertPythonExists: --check-models runs no script and needs no interpreter.
    if (opts.checkModels) return await runCheckModels(root, config, profile, deps.listModels);
    if (!deps.env) await assertPythonExists(root, config);
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
      let runner: MockRunner | CopilotRunner | ExternalRunner;
      if (kind === "mock") {
        runner = new MockRunner(root, path.resolve(root, config.samplesDir), opts.scenario);
      } else if (kind === "external") {
        // Every agent call is handed out through <root>/.agent-requests/ (orchestrator/external_runner.ts).
        runner = new ExternalRunner(root, config);
      } else {
        const { CopilotClient } = await import("@github/copilot-sdk");
        // Task L1 (R1): the configured interpreter is `python` inside every agent session.
        const copilot = new CopilotClient(copilotClientOptions(config, root));
        await copilot.start();
        client = copilot;
        runner = new CopilotRunner({ client: copilot, root, config, profile, agents: await loadAgents(root, profile) });
      }
      const gh = await githubAccess(config, opts, async () => (await runProcess("gh", ["--version"], { cwd: root })).ok);
      env = makeEnv({ root, config, runner, interactive, hasGh: gh.hasGh, ghEnabled: gh.enabled });
      runner.attach(env);
      console.log(
        `orchestrate: root=${root} runner=${kind} profile=${profile} gh=${gh.enabled ? (gh.hasGh ? "yes" : "no") : "disabled"} ` +
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
