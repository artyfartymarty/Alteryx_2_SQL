// Loads .github/agents/*.agent.md and turns them into SDK customAgents.
// The SDK does not document auto-loading that folder, so the orchestrator passes the
// agents explicitly and selects one with `agent: <role>` (design doc §4 deviation 2).
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import type { CustomAgentConfig } from "@github/copilot-sdk";
import type { Profile } from "./types.ts";

export interface AgentFile {
  name: string;
  description: string;
  model?: string;
  prompt: string;
}

const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/;

/** Splits YAML-ish frontmatter (only the keys we use) from the prompt body. */
export function parseAgentFile(text: string): AgentFile {
  const match = FRONTMATTER.exec(text);
  const body = (match ? text.slice(match[0].length) : text).trim();
  const fields: Record<string, string> = {};
  if (match) {
    for (const line of match[1].split(/\r?\n/)) {
      const kv = /^([A-Za-z_][\w-]*)\s*:\s*(.*)$/.exec(line.trim());
      if (!kv || line.trimStart().startsWith("#")) continue;
      fields[kv[1].toLowerCase()] = kv[2].trim().replace(/^["']|["']$/g, "");
    }
  }
  return {
    name: fields.name ?? "",
    description: fields.description ?? "",
    model: fields.model || undefined,
    prompt: body,
  };
}

/**
 * Every agent definition in the repo, as `customAgents` for createSession.
 * The local BYOK profile serves one model for every role, so per-agent models are dropped:
 * keeping them would make the runtime ask the llama.cpp server for a model it does not have.
 */
export async function loadAgents(root: string, profile: Profile): Promise<CustomAgentConfig[]> {
  const dir = path.join(root, ".github", "agents");
  let names: string[];
  try {
    names = (await readdir(dir)).filter((f) => f.endsWith(".agent.md")).sort();
  } catch {
    return [];
  }
  const agents: CustomAgentConfig[] = [];
  for (const file of names) {
    const parsed = parseAgentFile(await readFile(path.join(dir, file), "utf8"));
    const name = parsed.name || path.basename(file, ".agent.md");
    agents.push({
      name,
      description: parsed.description || `The ${name} agent.`,
      prompt: parsed.prompt,
      ...(profile === "hosted" && parsed.model ? { model: parsed.model } : {}),
    });
  }
  return agents;
}
