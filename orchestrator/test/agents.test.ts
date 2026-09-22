// .github/agents/*.agent.md is the single source for both the Copilot CLI and the SDK.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { parseAgentFile, loadAgents } from "../agents.ts";

const ROSTER = [
  "intake",
  "analyzer",
  "translator",
  "reviewer",
  "validator",
  "fixer",
  "parser-recovery",
  "documenter",
  "cookbook-curator",
];

const agentFile = (name: string, model: string) =>
  `---\nname: ${name}\ndescription: What ${name} does, in one line.\nmodel: ${model}\n# tools: left unrestricted; permissions are enforced by onPreToolUse.\n---\nYou are the ${name} agent.\n\n## Rules\n- Stay in your lane.\n`;

async function agentsRoot(): Promise<string> {
  const root = await mkdtemp(path.join(os.tmpdir(), "orch-agents-"));
  await mkdir(path.join(root, ".github", "agents"), { recursive: true });
  for (const name of ROSTER) {
    const model = name === "reviewer" || name === "validator" || name === "documenter" ? "gpt-5.6-luna" : "gpt-6-astra";
    await writeFile(path.join(root, ".github", "agents", `${name}.agent.md`), agentFile(name, model), "utf8");
  }
  return root;
}

test("parseAgentFile splits frontmatter from the prompt", () => {
  const parsed = parseAgentFile(agentFile("translator", "gpt-6-astra"));
  assert.equal(parsed.name, "translator");
  assert.equal(parsed.description, "What translator does, in one line.");
  assert.equal(parsed.model, "gpt-6-astra");
  assert.equal(parsed.prompt.startsWith("You are the translator agent."), true);
  assert.equal(parsed.prompt.includes("## Rules"), true);
  assert.equal(parsed.prompt.includes("name: translator"), false);
});

test("parseAgentFile keeps a --- inside the body and tolerates a file with no frontmatter", () => {
  const withRule = parseAgentFile("---\nname: fixer\ndescription: d\n---\nfirst\n\n---\n\nsecond\n");
  assert.equal(withRule.prompt, "first\n\n---\n\nsecond");
  assert.equal(withRule.model, undefined);

  const bare = parseAgentFile("just a prompt\n");
  assert.equal(bare.name, "");
  assert.equal(bare.prompt, "just a prompt");
});

test("loadAgents reads the roster; local drops the per-agent model, hosted keeps it", async (t) => {
  const root = await agentsRoot();
  t.after(() => rm(root, { recursive: true, force: true }));

  const hosted = await loadAgents(root, "hosted");
  assert.equal(hosted.length, 9);
  assert.deepEqual(hosted.map((a) => a.name).sort(), [...ROSTER].sort());
  assert.equal(hosted.find((a) => a.name === "translator")?.model, "gpt-6-astra");
  assert.equal(hosted.find((a) => a.name === "reviewer")?.model, "gpt-5.6-luna");
  assert.equal(hosted.every((a) => a.prompt.length > 0 && (a.description ?? "").length > 0), true);

  const local = await loadAgents(root, "local");
  assert.equal(local.length, 9);
  assert.equal(local.every((a) => a.model === undefined), true, "the BYOK profile serves one model for every role");
});

test("loadAgents falls back to the file name and returns nothing when the folder is absent", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "orch-agents-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  assert.deepEqual(await loadAgents(root, "hosted"), []);

  await mkdir(path.join(root, ".github", "agents"), { recursive: true });
  await writeFile(path.join(root, ".github", "agents", "documenter.agent.md"), "no frontmatter here\n", "utf8");
  await writeFile(path.join(root, ".github", "agents", "notes.md"), "not an agent\n", "utf8");
  const agents = await loadAgents(root, "hosted");
  assert.deepEqual(agents.map((a) => a.name), ["documenter"]);
  assert.equal(agents[0].prompt, "no frontmatter here");
});
