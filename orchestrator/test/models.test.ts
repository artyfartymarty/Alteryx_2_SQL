// Pure functions behind `orchestrate.ts --check-models`: what the repo currently configures for
// the hosted profile (orchestrator.config.json, every agent's frontmatter, config.json's
// sub-agents) and whether each configured id is present and enabled in a (possibly injected)
// catalog. See docs/handoff-copilot-models.md §2.
import { test } from "node:test";
import assert from "node:assert/strict";
import { checkModels, configuredModels } from "../models.ts";
import type { CatalogModel, ConfiguredModel } from "../models.ts";
import { DEFAULT_CONFIG } from "../cli.ts";
import type { OrchestratorConfig } from "../types.ts";

function hostedConfig(overrides: Partial<OrchestratorConfig["profiles"]["hosted"]> = {}): OrchestratorConfig {
  return {
    ...DEFAULT_CONFIG,
    profiles: {
      ...DEFAULT_CONFIG.profiles,
      hosted: { ...DEFAULT_CONFIG.profiles.hosted, ...overrides },
    },
  };
}

test("every configured id is in the enabled catalog -> ok", () => {
  const config = hostedConfig({ model: "luna", roleModels: { intake: "astra" } });
  const configured = configuredModels(config, "hosted", {}, {});
  const catalog: CatalogModel[] = [
    { id: "luna", policy: { state: "enabled" } },
    { id: "astra", policy: { state: "enabled" } },
  ];
  const result = checkModels(configured, catalog);
  assert.deepEqual(result, { ok: true, missing: [], disabled: [] });
});

test("a missing id and a disabled id are each reported with where they live", () => {
  const config = hostedConfig({ model: "luna", roleModels: { intake: "astra", analyzer: "vanished" } });
  const configured = configuredModels(config, "hosted", {}, {});
  const catalog: CatalogModel[] = [
    { id: "luna", policy: { state: "disabled" } },
    { id: "astra", policy: { state: "enabled" } },
    // "vanished" is not in the catalog at all
  ];
  const result = checkModels(configured, catalog);
  assert.equal(result.ok, false);

  assert.equal(result.disabled.length, 1);
  assert.equal(result.disabled[0].id, "luna");
  assert.match(result.disabled[0].where, /profiles\.hosted\.model/);

  assert.equal(result.missing.length, 1);
  assert.equal(result.missing[0].id, "vanished");
  assert.match(result.missing[0].where, /roleModels\.analyzer/);
});

test("an id with no policy at all (unconfigured) counts as not-ok, same as disabled", () => {
  const config = hostedConfig({ model: "luna" });
  const configured = configuredModels(config, "hosted", {}, {});
  const result = checkModels(configured, [{ id: "luna" }]);
  assert.equal(result.ok, false);
  assert.equal(result.disabled.length, 1);
  assert.equal(result.disabled[0].id, "luna");
});

test(
  "configuredModels collects profile.model, roleModels, every agent frontmatter model and " +
    "every config.json sub-agent model, de-duplicated by (where, id)",
  () => {
    const config = hostedConfig({ model: "luna", roleModels: { intake: "astra" } });
    const agentModels = { intake: "astra", reviewer: "luna" };
    const cliConfig = {
      subagents: {
        agents: {
          intake: { model: "astra" },
          task: { model: "luna" },
        },
      },
    };
    const configured = configuredModels(config, "hosted", agentModels, cliConfig);

    // Every source is represented, even where the id repeats across two different locations.
    const pairs = configured.map((c) => `${c.where}\u0000${c.id}`);
    assert.equal(new Set(pairs).size, pairs.length, "no (where, id) pair repeats");
    assert.equal(configured.length, 6, JSON.stringify(configured));

    assert.ok(configured.some((c) => /profiles\.hosted\.model$/.test(c.where) && c.id === "luna"));
    assert.ok(configured.some((c) => /roleModels\.intake$/.test(c.where) && c.id === "astra"));
    assert.ok(configured.some((c) => c.where.includes("intake.agent.md") && c.id === "astra"));
    assert.ok(configured.some((c) => c.where.includes("reviewer.agent.md") && c.id === "luna"));
    assert.ok(configured.some((c) => c.where.includes("subagents.agents.intake") && c.id === "astra"));
    assert.ok(configured.some((c) => c.where.includes("subagents.agents.task") && c.id === "luna"));

    // Calling it again with the exact same inputs produces the exact same (where, id) set --
    // configuredModels is a pure function of its inputs.
    const again = configuredModels(config, "hosted", agentModels, cliConfig);
    assert.deepEqual(
      new Set(again.map((c) => `${c.where}\u0000${c.id}`)),
      new Set(pairs),
    );
  },
);

test("configuredModels ignores roles the hosted profile does not override", () => {
  const config = hostedConfig({ model: "luna", roleModels: {} });
  const configured: ConfiguredModel[] = configuredModels(config, "hosted", {}, {});
  assert.deepEqual(configured, [{ where: "orchestrator.config.json profiles.hosted.model", id: "luna" }]);
});
