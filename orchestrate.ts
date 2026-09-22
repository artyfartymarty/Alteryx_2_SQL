// Alteryx -> Snowflake migration orchestrator, built on the GitHub Copilot SDK.
// The deterministic outer loop lives in orchestrator/: stages.ts is the state machine,
// policy.ts decides permissions, hooks.ts audits, runner.ts is the agent seam
// (MockRunner replays canned artifacts; CopilotRunner opens real sessions).
//
//   fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --runner mock --no-interactive
//
// Nothing here has run against real Snowflake or real Alteryx.
import { main } from "./orchestrator/cli.ts";

process.exit(await main(process.argv.slice(2)));
