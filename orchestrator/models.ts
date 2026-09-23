// Pure functions behind `orchestrate.ts --check-models` (docs/handoff-copilot-models.md §2):
// what model id this repo currently configures for a profile, and whether every one of them is
// present and enabled in a live (or injected) catalog. Nothing here talks to the network -- the
// catalog is always handed in, either by the CLI's real `client.listModels()` call or, in tests,
// by an injected fake (see orchestrator/cli.ts's `--check-models` branch and
// scripts/dev/list_models.ts, which prints the same shape for a human to read).
import type { OrchestratorConfig, Profile, Role } from "./types.ts";
import { ROLES } from "./types.ts";

/** One place a model id is configured, and the id configured there -- printed by `--check-models`
 * so a human can fix it with one `scripts/dev/set_models.py` call. */
export interface ConfiguredModel {
  where: string;
  id: string;
}

/** The subset of the SDK's `ModelInfo` (node_modules/@github/copilot-sdk/dist/types.d.ts:2962)
 * this preflight needs. */
export interface CatalogModel {
  id: string;
  policy?: { state?: string };
}

export interface ModelCheck {
  ok: boolean;
  missing: ConfiguredModel[];
  disabled: ConfiguredModel[];
}

/** The shape of `config.json`'s (the Copilot CLI config) `subagents.agents` map that this
 * preflight reads -- see docs/handoff-copilot-models.md §3. */
export interface CliSubagentsConfig {
  subagents?: { agents?: Record<string, { model?: string }> };
}

/**
 * Every model id configured for `profile`: `orchestrator.config.json`'s `profiles.<profile>`
 * (`model` and every `roleModels.<role>`), every agent's frontmatter `model:` (`agentModels`,
 * keyed by agent name -- `orchestrator/agents.ts`'s `loadAgents` already resolves these from
 * `.github/agents/*.agent.md`), and every `config.json` sub-agent `model` (`cliConfig`).
 * De-duplicated by (where, id): the same location is never reported twice for the same id.
 */
export function configuredModels(
  config: OrchestratorConfig,
  profile: Profile,
  agentModels: Record<string, string>,
  cliConfig: CliSubagentsConfig,
): ConfiguredModel[] {
  const seen = new Set<string>();
  const out: ConfiguredModel[] = [];
  const add = (where: string, id: string | undefined): void => {
    if (!id) return;
    const key = `${where}\u0000${id}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ where, id });
  };

  const profileConfig = config.profiles[profile];
  add(`orchestrator.config.json profiles.${profile}.model`, profileConfig?.model);
  for (const role of ROLES as Role[]) {
    add(`orchestrator.config.json profiles.${profile}.roleModels.${role}`, profileConfig?.roleModels?.[role]);
  }
  for (const [name, id] of Object.entries(agentModels)) {
    add(`.github/agents/${name}.agent.md model`, id);
  }
  for (const [name, agent] of Object.entries(cliConfig.subagents?.agents ?? {})) {
    add(`config.json subagents.agents.${name}.model`, agent.model);
  }
  return out;
}

/**
 * Every configured id checked against `catalog`: `missing` when no catalog entry has that id at
 * all, `disabled` when the id exists but its `policy.state` is not exactly `"enabled"` (covers
 * `"disabled"`, `"unconfigured"` and a model reported with no `policy` at all).
 */
export function checkModels(configured: ConfiguredModel[], catalog: CatalogModel[]): ModelCheck {
  const byId = new Map(catalog.map((model) => [model.id, model]));
  const missing: ConfiguredModel[] = [];
  const disabled: ConfiguredModel[] = [];
  for (const entry of configured) {
    const found = byId.get(entry.id);
    if (!found) {
      missing.push(entry);
    } else if (found.policy?.state !== "enabled") {
      disabled.push(entry);
    }
  }
  return { ok: missing.length === 0 && disabled.length === 0, missing, disabled };
}
