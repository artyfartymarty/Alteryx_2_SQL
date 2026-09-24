/**
 * `--runner external`: every agent call is handed to someone outside this process — a person, or
 * another agent system — through files, instead of to a model through the Copilot SDK.
 *
 * For each call the runner writes `<root>/.agent-requests/<id>.request.json` holding the role, the
 * workflow, the call's context (segment, dbt scope, analyzer batch), the agent file to follow and the
 * exact task text a Copilot session would have received (the orchestrator's instructions, the fixed
 * session rules and any fenced inline context — `runAgent` builds it the same way for every runner).
 * It then waits for `<id>.done.json` (the work is on disk; optional `{"toolCalls": n, "note": "…"}`)
 * or `<id>.failed.json` (`{"error": "<AgentError>", "detail": "…"}`), up to `sessionTimeoutMs`.
 *
 * Everything around the call is unchanged: the stage's own checks judge what was written, retries
 * and parks follow the same rules, and the role's output folders are created first, exactly as
 * `CopilotRunner` does. What this runner does NOT provide is the Copilot SDK's hooks: the tool
 * policy (`orchestrator/policy.ts`) never sees the external worker's actions, so the worker must keep
 * to the role's lane itself. It exists for development and for driving the pipeline with a model the
 * SDK cannot reach; production agent calls go through `CopilotRunner`.
 */
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";

import { outputFolders } from "./hooks.ts";
import { wfDir } from "./manifest.ts";
import type { AgentCtx, AgentError, AgentResult, AgentRunner, Env, Manifest, OrchestratorConfig, Role } from "./types.ts";

export const REQUEST_DIR = ".agent-requests";

const AGENT_ERRORS: readonly AgentError[] = [
  "timeout", "rate-limit", "context-overflow", "missing-output", "denied", "error",
] as AgentError[];

export interface ExternalRequest {
  id: string;
  role: Role;
  workflow: string;
  context: AgentCtx;
  agentFile: string;
  task: string;
  createdAt: string;
}

async function readJson(file: string): Promise<Record<string, unknown> | undefined> {
  try {
    return JSON.parse(await readFile(file, "utf8")) as Record<string, unknown>;
  } catch {
    return undefined;
  }
}

export class ExternalRunner implements AgentRunner {
  readonly root: string;
  readonly config: OrchestratorConfig;
  readonly pollMs: number;
  private env?: Env;
  private seq = 0;

  constructor(root: string, config: OrchestratorConfig, options: { pollMs?: number } = {}) {
    this.root = root;
    this.config = config;
    this.pollMs = options.pollMs ?? 2000;
  }

  attach(env: Env): void {
    this.env = env;
  }

  requestDir(): string {
    return path.join(this.root, REQUEST_DIR);
  }

  async run(role: Role, wf: Manifest, task: string, ctx: AgentCtx = {}): Promise<AgentResult> {
    const started = Date.now();
    const { folders } = outputFolders(role, ctx);
    for (const folder of folders) {
      await mkdir(wfDir(this.root, wf.id, ...folder.split("/")), { recursive: true });
    }

    this.seq += 1;
    const scope = ctx.batch ? `-${ctx.batch.id}` : ctx.dbt ? "-dbt" : ctx.segment ? `-${ctx.segment}` : "";
    const id = `${started}-${String(this.seq).padStart(3, "0")}-${wf.id}-${role}${scope}`;
    const dir = this.requestDir();
    await mkdir(dir, { recursive: true });
    const request: ExternalRequest = {
      id,
      role,
      workflow: wf.id,
      context: ctx,
      agentFile: `.github/agents/${role}.agent.md`,
      task,
      createdAt: new Date(started).toISOString(),
    };
    await writeFile(path.join(dir, `${id}.request.json`), `${JSON.stringify(request, null, 2)}\n`, "utf8");
    this.env?.log(`${wf.id}: ${role} handed to an external worker — ${REQUEST_DIR}/${id}.request.json`);

    const done = path.join(dir, `${id}.done.json`);
    const failed = path.join(dir, `${id}.failed.json`);
    const deadline = started + this.config.sessionTimeoutMs;
    for (;;) {
      const ok = await readJson(done);
      if (ok) {
        const toolCalls = typeof ok.toolCalls === "number" && ok.toolCalls >= 0 ? ok.toolCalls : 0;
        return { ok: true, toolCalls, ms: Date.now() - started };
      }
      const bad = await readJson(failed);
      if (bad) {
        const error = AGENT_ERRORS.includes(bad.error as AgentError) ? (bad.error as AgentError) : "error";
        const detail = typeof bad.detail === "string" ? bad.detail.slice(0, 500) : "the external worker reported a failure";
        return { ok: false, error, detail, toolCalls: 0, ms: Date.now() - started };
      }
      if (Date.now() >= deadline) {
        await rm(path.join(dir, `${id}.request.json`), { force: true });
        return { ok: false, error: "timeout", detail: "no answer from the external worker", toolCalls: 0, ms: Date.now() - started };
      }
      await new Promise((resolve) => setTimeout(resolve, this.pollMs));
    }
  }
}
