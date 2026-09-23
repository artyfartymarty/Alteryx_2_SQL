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

/** What the migration of a whole workflow produces (output-targets design §2). A workflow is
 * either a set of stored procedures (some of which may be Snowpark Python) or a dbt project —
 * never a mix. A `dbt` workflow is translated as ONE project for the whole workflow. */
export type OutputKind = "procedures" | "dbt";

/** What one segment's procedure is written in (output-targets design §2): `sql` today's stored
 * procedure, `snowpark` a Python one (`proc.py` + a rendered `proc.sql` wrapper). `manual` is the
 * analyzer's escape hatch — a segment no generator should attempt. */
export type SegmentTarget = "sql" | "snowpark" | "manual";

export interface Manifest {
  id: string;
  tier?: Tier;
  segments?: string[];
  golden_sets?: string[];
  /** What the organisation ASKED for, from sample.json or an intake answer; `target_check.py
   * --prefer auto` reads it, falling back to mappings/global.yaml's program.output_target. */
  output_target?: OutputKind;
  /** What the workflow is actually getting, mirrored from segments/targets.json after the
   * analyzer's contracts have been verified (output-targets design §3.2). */
  output_kind?: OutputKind;
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

/** What one agent call acts on. A procedures workflow's translate-stage roles act on one
 * `segment`; a dbt workflow's act on the whole project (`dbt: true`, no segment — output-targets
 * design §6), which is also what widens their policy lanes to `workflows/<wf>/dbt/…`. */
export interface AgentCtx {
  segment?: string;
  iteration?: number;
  dbt?: boolean;
  /** One call of a batched analyzer (Task W2, docs/reference/large-workflows.md): the batch's id
   * and its segments. It narrows the analyzer's write lane to those segments' contract.json files
   * and the batch's two fragments, `analysis/<id>.md` and `analysis/<id>.unsupported.json`. */
  batch?: AnalyzerBatch;
}

/** One batch of consecutive waves `scripts/plan_batches.py` planned in `segments/batches.json`: its id
 * (`batch_NN`) and its segments, in wave order. */
export interface AnalyzerBatch {
  id: string;
  segments: string[];
}

export interface AgentRunner {
  run(role: Role, wf: Manifest, task: string, ctx?: AgentCtx): Promise<AgentResult>;
}

/** Everything a stage is allowed to touch outside pure computation. */
export interface Env {
  root: string;
  config: OrchestratorConfig;
  runner: AgentRunner;
  interactive: boolean;
  /** `gh` answered `--version`; only ever probed when `ghEnabled`. */
  hasGh: boolean;
  /** GitHub integration is on for this run (`github.enabled` or `--gh`); off, no stage calls `gh`. */
  ghEnabled: boolean;
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
  /** Per-role context-window tier (docs/handoff-copilot-models.md §4 item 4): `long_context` pins
   * the session to the long-context tier of the SAME model, never a jump to a pricier one (owner's
   * model policy, §1 item 2). Absent for a role means the SDK's own default. The local BYOK
   * profile ignores this by construction (one model, one window). */
  roleContextTiers?: Partial<Record<Role, "default" | "long_context">>;
  /** Per-role reasoning effort override; falls back to `reasoningEffort`. */
  roleReasoningEffort?: Partial<Record<Role, ReasoningEffort>>;
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
  /** GitHub integration (an issue for open intake questions, a pull request after document). Off
   * unless `enabled` is true or the run passes `--gh`; off, the orchestrator never invokes `gh`. */
  github?: { enabled?: boolean };
  /** Characters of rendered context one analyzer call may carry before the workflow is analysed
   * batch by batch (`scripts/plan_batches.py --budget-chars`, Task W2). An estimate, not tokens
   * (roughly characters / 4). Absent means `DEFAULT_CONFIG`'s 60 000. */
  analyzerBudgetChars?: number;
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
  /** `--check-models`: list the catalog and check every configured hosted-profile id, instead of
   * running any workflow (docs/handoff-copilot-models.md §2). */
  checkModels?: boolean;
  /** `--gh`: GitHub integration on for this run, whatever `github.enabled` says. */
  gh?: boolean;
}
