// Prints the model catalog the signed-in GitHub Copilot CLI can use, straight from the SDK.
//
// Use it BEFORE editing any model id in orchestrator.config.json, config.json or
// .github/agents/*.agent.md: the ids in those files came from the program spec and have never
// been checked against a live catalog. See docs/handoff-copilot-models.md.
//
//   fnm exec --using=22 node.exe --experimental-strip-types scripts/dev/list_models.ts
//
// Needs a Copilot CLI login (`copilot` -> `/login`), which is the human's own step; this script
// never authenticates. It only reads; it starts no session and spends no premium request.
import { CopilotClient } from "@github/copilot-sdk";

async function main(): Promise<number> {
  const client = new CopilotClient();
  await client.start();
  try {
    const models = await client.listModels();
    const rows = models.map((m) => ({
      id: m.id,
      name: m.name,
      context_window: m.capabilities.limits.max_context_window_tokens,
      max_output: m.capabilities.limits.max_output_tokens ?? "",
      billing_x: m.billing?.multiplier ?? "",
      efforts: (m.supportedReasoningEfforts ?? []).join("/"),
      default_effort: m.defaultReasoningEffort ?? "",
      policy: m.policy?.state ?? "",
    }));
    rows.sort((a, b) => a.id.localeCompare(b.id));
    console.table(rows);
    console.log(`${rows.length} model(s). "policy" must be "enabled" before a model can be used; ` +
      `"billing_x" is the premium-request multiplier the catalog reports for that model.`);
    return 0;
  } finally {
    await client.stop();
  }
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error(`list_models: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(2);
  },
);
