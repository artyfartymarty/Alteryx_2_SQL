// Shared types for the orchestrator. No behaviour lives here.
import type { MCPServerConfig } from "@github/copilot-sdk";

export type Role =
  | "intake"
  | "analyzer"
  | "translator"
  | "reviewer"
  | "validator"
  | "fixer"
  | "parser-recovery"
  | "documenter";

export type Stage = "parse" | "intake" | "analyze" | "golden" | "translate" | "document" | "pr";

export const STAGES: Stage[] = ["parse", "intake", "analyze", "golden", "translate", "document", "pr"];

export const ROLES: Role[] = [
  "intake",
  "analyzer",
  "translator",
  "reviewer",
  "validator",
  "fixer",
  "parser-recovery",
  "documenter",
];

export type Tier = "T1" | "T2" | "T3";

export interface Manifest {
  id: string;
  tier?: Tier;
  segments?: string[];
  golden_sets?: string[];
  status: Record<string, string>;
  segment_status?: Record<string, string>;
  metrics: Record<string, any>;
  parse?: { attempts: number; extensions: string[] };
  /** Why a stage stopped, when the status alone does not say: budget, script-error, … */
  reasons?: Record<string, string>;
  [k: string]: unknown;
}

export interface ShResult {
  ok: boolean;
  code: number;
  out: string;
  err: string;
}

/** "context-overflow": the model's own context window filled up (a real BYOK-server failure
 * mode, live-verified — see task-16-report.md). It is a reason, not a status: an escalated
 * workflow still lands on the existing NEEDS_HUMAN status, same as any other "error"; it is never
 * retried (orchestrator/stages.ts's RETRY_ONCE), since an identical retry cannot succeed. */
export type AgentError = "missing-output" | "denied" | "timeout" | "rate-limit" | "error" | "context-overflow";

export interface AgentResult {
  ok: boolean;
  error?: AgentError;
  detail?: string;
  toolCalls: number;
  ms: number;
}

export interface AgentRunner {
  run(
    role: Role,
    wf: Manifest,
    task: string,
    ctx?: { segment?: string; iteration?: number },
  ): Promise<AgentResult>;
}

/** Everything a stage is allowed to touch outside pure computation. */
export interface Env {
  root: string;
  config: OrchestratorConfig;
  runner: AgentRunner;
  interactive: boolean;
  hasGh: boolean;
  py(script: string, args: string[], opts?: { inheritStdio?: boolean }): Promise<ShResult>;
  sh(cmd: string, args: string[]): Promise<ShResult>;
  sleep(ms: number): Promise<void>;
  log(line: string): void;
}

export type Profile = "local" | "hosted";

export type ReasoningEffort = "low" | "medium" | "high" | "xhigh" | "max";

export interface ProfileConfig {
  /** BYOK provider; omitted for the hosted profile, which uses Copilot's own models. */
  provider?: { type?: "openai" | "azure" | "anthropic"; baseUrl: string; apiKey?: string };
  model?: string;
  reasoningEffort?: ReasoningEffort;
  /** Per-role model override; falls back to `model`. */
  roleModels?: Partial<Record<Role, string>>;
}

export interface OrchestratorConfig {
  /** Python interpreter, resolved relative to the run root. */
  python: string;
  /** Canned agent artifacts for the mock runner, resolved relative to the run root. */
  samplesDir: string;
  parallelism: number;
  maxFixIterations: number;
  maxParseRecovery: number;
  sessionTimeoutMs: number;
  golden: { producer: "simulator" | "alteryx" };
  budgets: { maxToolCallsPerWorkflow: number };
  profiles: Record<Profile, ProfileConfig>;
  /** Passed straight to createSession; the Snowflake MCP server goes here. */
  mcpServers?: Record<string, MCPServerConfig>;
  /** Permission-policy configuration; see orchestrator/POLICY.md. */
  policy?: { sandboxDatabases?: string[] };
}

export interface RunOptions {
  only?: string;
  fromStage?: Stage;
  stopAfter?: Stage;
  tier?: Tier;
  dryRun?: boolean;
  runner?: "mock" | "copilot";
  profile?: Profile;
  /** Undefined means "decide from stdin being a TTY". */
  interactive?: boolean;
  root?: string;
  scenario?: string;
}
