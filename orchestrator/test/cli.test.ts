// Flags, workflow selection and the exit-code contract.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { makeEnv, seedWorkflow } from "./fakes.ts";
import {
  parseArgs, main, loadConfig, assertPythonExists, DEFAULT_CONFIG, UsageError, githubAccess,
  makeEnv as makeRealEnv, sessionEnvironment, copilotClientOptions,
} from "../cli.ts";
import { writeJson } from "../manifest.ts";
import { ROLES } from "../types.ts";

// Two directories up from orchestrator/test/ is this tree's own root (may be a git worktree),
// which carries the REAL, currently-checked-out orchestrator.config.json -- same pattern as
// integration.test.ts's REPO_ROOT.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..");

/** main() prints a summary; the tests below only care about the exit code and the fake env. */
function quiet(t: any): string[] {
  const lines: string[] = [];
  const log = console.log;
  const error = console.error;
  console.log = (...args: unknown[]) => lines.push(args.join(" "));
  console.error = (...args: unknown[]) => lines.push(args.join(" "));
  t.after(() => {
    console.log = log;
    console.error = error;
  });
  return lines;
}

test("parseArgs reads every flag", () => {
  assert.deepEqual(parseArgs([]), {});
  assert.deepEqual(
    parseArgs([
      "--only", "wf_0042",
      "--from-stage", "translate",
      "--stop-after", "document",
      "--tier", "T1",
      "--dry-run",
      "--runner", "mock",
      "--profile", "hosted",
      "--interactive",
      "--root", "C:/scratch",
      "--scenario", "fix-loop:seg_01",
    ]),
    {
      only: "wf_0042",
      fromStage: "translate",
      stopAfter: "document",
      tier: "T1",
      dryRun: true,
      runner: "mock",
      profile: "hosted",
      interactive: true,
      root: "C:/scratch",
      scenario: "fix-loop:seg_01",
    },
  );
  assert.equal(parseArgs(["--no-interactive"]).interactive, false);
  assert.equal(parseArgs(["--runner=copilot"]).runner, "copilot");
  assert.equal(parseArgs(["--profile=local"]).profile, "local");
});

test("parseArgs refuses unknown flags, unknown values and missing values", () => {
  for (const argv of [["--wat"], ["--from-stage", "translating"], ["--tier", "T9"], ["--runner", "llama"], ["--only"], ["--root", "--dry-run"]]) {
    assert.throws(() => parseArgs(argv), UsageError, argv.join(" "));
  }
});

test("loadConfig falls back to the defaults and merges what the file sets", async () => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  assert.deepEqual(await loadConfig(root), DEFAULT_CONFIG);

  await writeJson(`${root}/orchestrator.config.json`, { parallelism: 1, golden: { producer: "alteryx" } });
  const merged = await loadConfig(root);
  assert.equal(merged.parallelism, 1);
  assert.equal(merged.golden.producer, "alteryx");
  assert.equal(merged.maxFixIterations, DEFAULT_CONFIG.maxFixIterations, "unset keys keep their default");
  assert.equal(merged.profiles.local.model, "ternary-bonsai-2-27b");
});

test("loadConfig treats a PRESENT but unparsable config file as a usage error, not a silent default", async () => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  const configPath = path.join(root, "orchestrator.config.json");

  await writeFile(configPath, "{ this is not valid json", "utf8");
  await assert.rejects(loadConfig(root), (error: unknown) => {
    assert.ok(error instanceof UsageError, `expected a UsageError, got ${error}`);
    assert.match((error as Error).message, /orchestrator\.config\.json/);
    return true;
  });

  // A top-level JSON value that parses fine but isn't an object is just as unusable as a syntax
  // error — it must be refused the same way, not silently spread into (and corrupting) the config.
  await writeFile(configPath, "[1, 2, 3]", "utf8");
  await assert.rejects(loadConfig(root), UsageError);

  // An absent file is still perfectly fine and falls back to defaults, unchanged from before.
  await rm(configPath, { force: true });
  assert.deepEqual(await loadConfig(root), DEFAULT_CONFIG);
});

// Fix round 1 (I1): the owner's policy item 4 ("low for documenter and reviewer, medium for
// everything else") must hold for the REAL committed orchestrator.config.json, not just a
// hand-built fixture -- a missing baseline `profiles.hosted.reasoningEffort` would silently
// resolve to `undefined` for every role without its own roleReasoningEffort entry.
test("the real committed orchestrator.config.json resolves the owner's reasoningEffort/contextTier policy for every hosted role", async () => {
  const config = await loadConfig(REPO_ROOT);
  const profile = config.profiles.hosted;
  const LOW_EFFORT_ROLES = new Set(["documenter", "reviewer"]);
  const LONG_CONTEXT_ROLES = new Set(["intake", "analyzer", "fixer", "parser-recovery", "validator"]);

  for (const role of ROLES) {
    // Exactly what orchestrator/runner.ts's createSession call computes.
    const effort = profile.roleReasoningEffort?.[role] ?? profile.reasoningEffort;
    const contextTier = profile.roleContextTiers?.[role];

    assert.equal(
      effort,
      LOW_EFFORT_ROLES.has(role) ? "low" : "medium",
      `${role}: reasoningEffort must resolve per the owner's policy item 4, got ${effort}`,
    );
    assert.equal(
      contextTier,
      LONG_CONTEXT_ROLES.has(role) ? "long_context" : undefined,
      `${role}: contextTier must be long_context only for the five policy-2 roles, got ${contextTier}`,
    );
  }
});

test("a configured python that does not exist is a usage error, not five misleading escalations", async () => {
  // Following the README in Git Bash once produced `"python": "/c/Users/…/python.exe"`, which Node
  // on Windows cannot resolve: every script call failed with ENOENT and every workflow was parked
  // at parse=NEEDS_HUMAN "missing-output" — nothing in the trail mentioned the interpreter.
  const { root } = await makeEnv({ wf: "wf_0001" });
  const missing = { ...DEFAULT_CONFIG, python: "no/such/dir/python.exe" };
  await assert.rejects(assertPythonExists(root, missing), (error: unknown) => {
    assert.ok(error instanceof UsageError, `expected a UsageError, got ${error}`);
    assert.match((error as Error).message, /python/);
    assert.match((error as Error).message, /no[\\/]such[\\/]dir/);
    return true;
  });

  // A directory is not an interpreter either.
  await assert.rejects(assertPythonExists(root, { ...DEFAULT_CONFIG, python: "." }), UsageError);

  // An existing file passes, relative to the root or absolute.
  await writeFile(path.join(root, "fake-python.exe"), "", "utf8");
  await assertPythonExists(root, { ...DEFAULT_CONFIG, python: "fake-python.exe" });
  await assertPythonExists(root, { ...DEFAULT_CONFIG, python: path.join(root, "fake-python.exe") });
});

test("main refuses to run with a python that does not exist (exit 2, nothing escalated)", async (t) => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  await writeJson(`${root}/orchestrator.config.json`, { python: "/c/Users/nobody/python.exe" });
  const errors: string[] = [];
  t.mock.method(console, "error", (line: string) => errors.push(String(line)));
  t.mock.method(console, "log", () => {});

  assert.equal(await main(["--root", root, "--runner", "mock", "--no-interactive"]), 2);
  assert.match(errors.join("\n"), /python/);
  const manifest = JSON.parse(await readFile(path.join(root, "workflows", "wf_0001", "manifest.json"), "utf8"));
  assert.notEqual(manifest.status?.parse, "NEEDS_HUMAN", "the workflow must not be escalated for a config mistake");
});

// Task L1 fix round 1 (M6): a read-denial budget that is not a non-negative integer would make every
// comparison with it false, so read denials would never park the session -- failing open.
test("L1 fix 1 (M6): budgets.maxReadDenialsPerSession must be a non-negative integer, or the config is refused", async () => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  for (const bad of ["twenty", "20", -1, 2.5, null, true, [], {}, Number.NaN]) {
    await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxReadDenialsPerSession: bad } });
    await assert.rejects(loadConfig(root), (error: unknown) => {
      assert.ok(error instanceof UsageError, `${JSON.stringify(bad)}: expected a UsageError, got ${error}`);
      assert.match((error as Error).message, /budgets\.maxReadDenialsPerSession must be a non-negative integer/);
      return true;
    });
  }
  for (const good of [0, 1, 20, 500]) {
    await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxReadDenialsPerSession: good } });
    assert.equal((await loadConfig(root)).budgets.maxReadDenialsPerSession, good);
  }
  // unset keeps the default
  await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxToolCallsPerWorkflow: 10 } });
  assert.equal((await loadConfig(root)).budgets.maxReadDenialsPerSession, 20);
});

// Task L6 (R2): the attempted-action budget is validated the same way: a value that is not a
// non-negative integer would make every comparison with it false, so attempted actions would never park.
test("L6 R2: budgets.maxActDenialsPerSession must be a non-negative integer, or main exits 2 naming the key", async (t) => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  for (const bad of ["three", "3", -1, 2.5, null, true, [], {}, Number.NaN]) {
    await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxActDenialsPerSession: bad } });
    await assert.rejects(loadConfig(root), (error: unknown) => {
      assert.ok(error instanceof UsageError, `${JSON.stringify(bad)}: expected a UsageError, got ${error}`);
      assert.match((error as Error).message, /budgets\.maxActDenialsPerSession must be a non-negative integer/);
      return true;
    });
  }
  for (const good of [0, 1, 3, 50]) {
    await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxActDenialsPerSession: good } });
    assert.equal((await loadConfig(root)).budgets.maxActDenialsPerSession, good);
  }
  // unset keeps the default
  await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxToolCallsPerWorkflow: 10 } });
  assert.equal((await loadConfig(root)).budgets.maxActDenialsPerSession, 20, "L6 fix round 1: the default is 20");

  await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxActDenialsPerSession: "three" } });
  const errors: string[] = [];
  t.mock.method(console, "error", (line: string) => errors.push(String(line)));
  t.mock.method(console, "log", () => {});
  assert.equal(await main(["--root", root, "--runner", "mock", "--no-interactive"]), 2);
  assert.match(errors.join("\n"), /budgets\.maxActDenialsPerSession/);
  const manifest = JSON.parse(await readFile(path.join(root, "workflows", "wf_0001", "manifest.json"), "utf8"));
  assert.equal(manifest.status?.parse, undefined, "nothing ran");
});

// Task L1 fix round 2 (S5): the workflow's tool-call budget is validated the same way.
test("L1 fix 2 (S5): budgets.maxToolCallsPerWorkflow must be a positive integer, or main exits 2 naming the key", async (t) => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  for (const bad of ["400", 0, -5, 1.5, null, false, [], {}]) {
    await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxToolCallsPerWorkflow: bad } });
    await assert.rejects(loadConfig(root), (error: unknown) => {
      assert.ok(error instanceof UsageError, `${JSON.stringify(bad)}: expected a UsageError, got ${error}`);
      assert.match((error as Error).message, /budgets\.maxToolCallsPerWorkflow must be a positive integer/);
      return true;
    });
  }
  for (const good of [1, 400, 5000]) {
    await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxToolCallsPerWorkflow: good } });
    assert.equal((await loadConfig(root)).budgets.maxToolCallsPerWorkflow, good);
  }
  await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxToolCallsPerWorkflow: "many" } });
  const errors: string[] = [];
  t.mock.method(console, "error", (line: string) => errors.push(String(line)));
  t.mock.method(console, "log", () => {});
  assert.equal(await main(["--root", root, "--runner", "mock", "--no-interactive"]), 2);
  assert.match(errors.join("\n"), /budgets\.maxToolCallsPerWorkflow/);
});

// Live hardening, Task L7 (R1): `profiles.<name>.provider.maxPromptTokens`/`maxOutputTokens` reach
// the SDK's createSession unchanged (runner.test.ts covers that half); a value that is not a
// positive integer would silently reach the SDK as garbage instead of being refused here, by name.
test("L7 R1: profiles.<name>.provider.maxPromptTokens/maxOutputTokens must be positive integers, or the config is refused", async () => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  const provider = { type: "openai" as const, baseUrl: "http://127.0.0.1:8080/v1", apiKey: "local" };
  for (const profileName of ["local", "hosted"] as const) {
    for (const key of ["maxPromptTokens", "maxOutputTokens"] as const) {
      for (const bad of ["120000", 0, -1, 1.5, null, true, [], {}, Number.NaN]) {
        await writeJson(`${root}/orchestrator.config.json`, {
          profiles: { [profileName]: { ...(profileName === "hosted" ? { model: "m" } : {}), provider: { ...provider, [key]: bad } } },
        });
        await assert.rejects(loadConfig(root), (error: unknown) => {
          assert.ok(error instanceof UsageError, `${profileName}.${key}=${JSON.stringify(bad)}: expected a UsageError, got ${error}`);
          assert.match(
            (error as Error).message,
            new RegExp(`profiles\\.${profileName}\\.provider\\.${key} must be a positive integer`),
          );
          return true;
        });
      }
      for (const good of [1, 120000, 500000]) {
        await writeJson(`${root}/orchestrator.config.json`, {
          profiles: { [profileName]: { ...(profileName === "hosted" ? { model: "m" } : {}), provider: { ...provider, [key]: good } } },
        });
        const config = await loadConfig(root);
        assert.equal(config.profiles[profileName].provider?.[key], good);
      }
    }
  }
  // absent is fine -- most profiles set neither, and the hosted default carries no provider at all.
  assert.equal(DEFAULT_CONFIG.profiles.hosted.provider, undefined);
});

// Live hardening, Task L9 (R1): session.excludedTools extends the SDK's fixed excludedTools list
// (runner.test.ts covers that it reaches createSession); a value that is not a list of non-empty
// strings would silently reach the SDK as garbage instead of being refused here, by name.
test("L9 R1: session.excludedTools must be a list of non-empty strings, or the config is refused", async () => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  for (const bad of ["web_fetch", 0, null, true, {}, [1], [null], [""], ["web_fetch", ""], [123, "x"]]) {
    await writeJson(`${root}/orchestrator.config.json`, { session: { excludedTools: bad } });
    await assert.rejects(loadConfig(root), (error: unknown) => {
      assert.ok(error instanceof UsageError, `${JSON.stringify(bad)}: expected a UsageError, got ${error}`);
      assert.match((error as Error).message, /session\.excludedTools must be a list of non-empty strings/);
      return true;
    });
  }
  for (const good of [[], ["web_fetch"], ["mcp:extra_tool", "custom:noop"]]) {
    await writeJson(`${root}/orchestrator.config.json`, { session: { excludedTools: good } });
    assert.deepEqual((await loadConfig(root)).session?.excludedTools, good);
  }
  // absent is fine -- the fixed list alone reaches createSession.
  await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxToolCallsPerWorkflow: 10 } });
  assert.equal((await loadConfig(root)).session?.excludedTools, undefined);
});

// Live hardening, Task L9 fix round 2 (L9 minor): a bare "*" is refused by the SDK's own createSession
// (unlike an unknown plain tool name, which it silently ignores) -- caught here, by name, rather than
// reaching createSession as an unhandled crash.
test("L9 fix round 2: session.excludedTools may not contain a bare \"*\"", async () => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  for (const bad of [["*"], ["web_fetch", "*"], ["*", "custom:noop"]]) {
    await writeJson(`${root}/orchestrator.config.json`, { session: { excludedTools: bad } });
    await assert.rejects(loadConfig(root), (error: unknown) => {
      assert.ok(error instanceof UsageError, `${JSON.stringify(bad)}: expected a UsageError, got ${error}`);
      assert.match((error as Error).message, /session\.excludedTools may not contain a bare "\*"/);
      return true;
    });
  }
  // the source-qualified forms are fine -- only a bare "*" is refused
  for (const good of [["builtin:*"], ["mcp:*"], ["custom:*"], ["web_fetch"]]) {
    await writeJson(`${root}/orchestrator.config.json`, { session: { excludedTools: good } });
    assert.deepEqual((await loadConfig(root)).session?.excludedTools, good);
  }
});

test("L1 fix 1 (M6): main exits 2 naming the key, before any workflow runs", async (t) => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  await writeJson(`${root}/orchestrator.config.json`, { budgets: { maxReadDenialsPerSession: "twenty" } });
  const errors: string[] = [];
  t.mock.method(console, "error", (line: string) => errors.push(String(line)));
  t.mock.method(console, "log", () => {});
  assert.equal(await main(["--root", root, "--runner", "mock", "--no-interactive"]), 2);
  assert.match(errors.join("\n"), /budgets\.maxReadDenialsPerSession/);
  const manifest = JSON.parse(await readFile(path.join(root, "workflows", "wf_0001", "manifest.json"), "utf8"));
  assert.equal(manifest.status?.parse, undefined, "nothing ran");
});

test("--dry-run calls neither py nor the runner", async (t) => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  const lines = quiet(t);
  assert.equal(await main(["--only", "wf_0001", "--dry-run"], { env }), 0);
  assert.equal(calls.py.length, 0);
  assert.equal(calls.roles.length, 0);
  assert.ok(calls.logs.some((line) => line.includes("would run") && line.includes("parse")));
  assert.ok(lines.some((line) => line.includes("wf_0001")));
});

test("a workflow that finishes exits 0; one that needs a human exits 1", async (t) => {
  const good = await makeEnv({ wf: "wf_0001" });
  const lines = quiet(t);
  assert.equal(await main(["--only", "wf_0001"], { env: good.env }), 0);
  assert.ok(good.calls.roles.includes("documenter"));

  const stuck = await makeEnv({ wf: "wf_0001", scenario: "never-fixed:seg_01" });
  assert.equal(await main(["--only", "wf_0001"], { env: stuck.env }), 1);
  assert.ok(lines.some((line) => line.includes("needs a human")));
});

test("a quarantined workflow also exits 1", async (t) => {
  const { env } = await makeEnv({ wf: "wf_0005", scenario: "recovery-fails" });
  quiet(t);
  assert.equal(await main([], { env }), 1);
});

// Coordinator ruling (task-15-int, fix round 1): a run that contains a parked escalation must
// exit with the same code it would have had on the run that escalated it -- parking a workflow
// changes what gets invoked, never the exit-code contract.
test("exit code stays stable across repeated runs of a parked workflow", async (t) => {
  quiet(t);
  const stuck = await makeEnv({ wf: "wf_0001", scenario: "never-fixed:seg_01" });
  assert.equal(await main(["--only", "wf_0001"], { env: stuck.env }), 1);
  assert.equal(await main(["--only", "wf_0001"], { env: stuck.env }), 1, "second run's exit code matches the first");

  const quarantined = await makeEnv({ wf: "wf_0005", scenario: "recovery-fails" });
  assert.equal(await main([], { env: quarantined.env }), 1);
  assert.equal(await main([], { env: quarantined.env }), 1, "second run's exit code matches the first");
});

test("usage errors exit 2 and run nothing", async (t) => {
  const { env, calls, root } = await makeEnv({ wf: "wf_0001" });
  const lines = quiet(t);

  assert.equal(await main(["--wat"], { env }), 2);
  assert.equal(await main(["--only", "wf_9999"], { env }), 2);
  assert.equal(await main(["--from-stage", "nonsense"], { env }), 2);
  assert.equal(calls.py.length, 0);
  assert.equal(calls.roles.length, 0);
  assert.ok(lines.some((line) => line.includes("unknown workflow wf_9999")));

  await rm(`${root}/workflows`, { recursive: true, force: true });
  assert.equal(await main([], { env }), 2);
  assert.ok(lines.some((line) => line.includes("no workflows directory")));
});

test("an unexpected failure inside a run exits 2 rather than claiming success", async (t) => {
  const { env } = await makeEnv({ wf: "wf_0001" });
  quiet(t);
  env.py = () => Promise.reject(new Error("spawn ENOENT python"));
  assert.equal(await main(["--only", "wf_0001"], { env }), 2);
});

// --- F12: a corrupt manifest.json is a hard, per-workflow error -- exit non-zero, that workflow
// untouched, every OTHER workflow in the same run still completes. ------------------------------

test("F12: a corrupt manifest.json for one workflow exits non-zero, names the file, and does not stop a healthy sibling workflow", async (t) => {
  const { env, root, calls } = await makeEnv({ wf: "wf_0001" });
  await seedWorkflow(root, "wf_0002");
  await writeFile(path.join(root, "workflows", "wf_0002", "manifest.json"), "{ not valid json", "utf8");
  const lines = quiet(t);

  const code = await main([], { env });
  assert.equal(code, 2, "an unexpected per-workflow failure exits 2, same as any other unexpected failure");
  assert.ok(lines.some((line) => line.includes("wf_0002") && line.includes("manifest.json")), "the corrupt file is named");

  // The healthy workflow was not abandoned: it ran its scripts and shows up in the summary.
  assert.ok(calls.py.some((c) => c.args[0] === "wf_0001"), "wf_0001's scripts still ran");
  assert.ok(lines.some((line) => line.includes("wf_0001") && line.includes("translate=VALIDATED")));

  // The corrupt file itself was never touched.
  const stillBad = await readFile(path.join(root, "workflows", "wf_0002", "manifest.json"), "utf8");
  assert.equal(stillBad, "{ not valid json");
});

// ---------- --check-models (docs/handoff-copilot-models.md §2) ----------------------------------
// Never a real listModels call here (or anywhere an agent runs): deps.listModels is always
// injected. SPLIT is a hosted profile with the program spec's per-role split: "gpt-5.6-luna"
// (profiles.hosted.model) and "gpt-6-astra" (roleModels.{intake,analyzer,translator,fixer,
// parser-recovery}). DEFAULT_CONFIG itself carries no roleModels (Task P4 fix round 1, B1).
const SPLIT = {
  profiles: {
    ...DEFAULT_CONFIG.profiles,
    hosted: {
      ...DEFAULT_CONFIG.profiles.hosted,
      roleModels: {
        intake: "gpt-6-astra", analyzer: "gpt-6-astra", translator: "gpt-6-astra", fixer: "gpt-6-astra",
        "parser-recovery": "gpt-6-astra",
      },
    },
  },
};

test("--check-models lists the catalog through the injected listModels and exits 1 on a missing id", async (t) => {
  const { env } = await makeEnv({ wf: "wf_0001", config: SPLIT });
  const lines = quiet(t);
  const code = await main(["--check-models", "--profile", "hosted"], {
    env,
    listModels: async () => [{ id: "a", policy: { state: "enabled" } }],
  });
  assert.equal(code, 1);
  assert.ok(lines.some((line) => line.includes("gpt-5.6-luna") && line.includes("missing")), lines.join("\n"));
  assert.ok(lines.some((line) => line.includes("gpt-6-astra") && line.includes("missing")), lines.join("\n"));
});

test("--check-models exits 0 when every configured id is enabled", async (t) => {
  const { env } = await makeEnv({ wf: "wf_0001", config: SPLIT });
  quiet(t);
  const code = await main(["--check-models", "--profile", "hosted"], {
    env,
    listModels: async () => [
      { id: "gpt-5.6-luna", policy: { state: "enabled" } },
      { id: "gpt-6-astra", policy: { state: "enabled" } },
    ],
  });
  assert.equal(code, 0);
});

test("--check-models reports a disabled id distinctly from a missing one", async (t) => {
  const { env } = await makeEnv({ wf: "wf_0001", config: SPLIT });
  const lines = quiet(t);
  const code = await main(["--check-models", "--profile", "hosted"], {
    env,
    listModels: async () => [
      { id: "gpt-5.6-luna", policy: { state: "disabled" } },
      { id: "gpt-6-astra", policy: { state: "enabled" } },
    ],
  });
  assert.equal(code, 1);
  assert.ok(lines.some((line) => line.includes("gpt-5.6-luna") && line.includes("disabled")), lines.join("\n"));
  assert.ok(!lines.some((line) => line.includes("gpt-6-astra") && (line.includes("missing") || line.includes("disabled"))));
});

test("--check-models with --profile local is a usage error (exit 2)", async (t) => {
  const { env } = await makeEnv({ wf: "wf_0001" });
  const lines = quiet(t);
  const code = await main(["--check-models", "--profile", "local"], { env, listModels: async () => [] });
  assert.equal(code, 2);
  assert.ok(lines.some((line) => line.includes("--check-models") && line.includes("hosted")), lines.join("\n"));
});

test("--check-models with no --profile also defaults to local and is a usage error (exit 2)", async (t) => {
  const { env } = await makeEnv({ wf: "wf_0001" });
  quiet(t);
  assert.equal(await main(["--check-models"], { env, listModels: async () => [] }), 2);
});

test("a listModels failure exits 2 and names the login step", async (t) => {
  const { env } = await makeEnv({ wf: "wf_0001" });
  const lines = quiet(t);
  const code = await main(["--check-models", "--profile", "hosted"], {
    env,
    listModels: async () => {
      throw new Error("not signed in");
    },
  });
  assert.equal(code, 2);
  assert.ok(lines.some((line) => line.includes("/login")), lines.join("\n"));
  assert.ok(lines.some((line) => line.includes("not signed in")), lines.join("\n"));
});

test("--check-models never starts a workflow run", async (t) => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  quiet(t);
  await main(["--check-models", "--profile", "hosted"], {
    env,
    listModels: async () => [
      { id: "gpt-5.6-luna", policy: { state: "enabled" } },
      { id: "gpt-6-astra", policy: { state: "enabled" } },
    ],
  });
  assert.equal(calls.py.length, 0);
  assert.equal(calls.roles.length, 0);
});

test("--tier selects only workflows already classified as that tier", async (t) => {
  const { env, calls } = await makeEnv({ wf: "wf_0005", manifest: { tier: "T3" } });
  quiet(t);
  assert.equal(await main(["--tier", "T1"], { env }), 0);
  assert.equal(calls.py.length, 0, "the T3 workflow is not selected");
  assert.equal(await main(["--tier", "T3"], { env }), 0);
  assert.ok(calls.py.length > 0);
});

// ---------- Task P4 fix round 1 ------------------------------------------------------------------

// B1: the owner's policy is one default model for every role, so the built-in hosted profile has no
// per-role model map at all -- a config file that removes roleModels must not get it back.
test("DEFAULT_CONFIG's hosted profile routes every role to its one model: no roleModels", () => {
  assert.equal(DEFAULT_CONFIG.profiles.hosted.roleModels, undefined);
});

test("a file's hosted role maps are whole maps: one the file omits is not inherited from the defaults", async () => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  await writeJson(`${root}/orchestrator.config.json`, {
    profiles: { hosted: { model: "one-model", roleContextTiers: { intake: "long_context" } } },
  });
  const hosted = (await loadConfig(root)).profiles.hosted;
  assert.equal(hosted.model, "one-model");
  assert.equal(hosted.reasoningEffort, DEFAULT_CONFIG.profiles.hosted.reasoningEffort, "scalars keep their default");
  assert.equal(hosted.roleModels, undefined, "roleModels omitted by the file stays omitted");
  assert.equal(hosted.roleReasoningEffort, undefined, "roleReasoningEffort omitted by the file stays omitted");
  assert.deepEqual(hosted.roleContextTiers, { intake: "long_context" }, "the file's map, not merged key by key");
  for (const role of ROLES) assert.equal(hosted.roleModels?.[role] ?? hosted.model, "one-model", role);
});

test("--check-models lists the effective model of every role", async (t) => {
  const { env } = await makeEnv({ wf: "wf_0001", config: SPLIT });
  const lines = quiet(t);
  await main(["--check-models", "--profile", "hosted"], {
    env,
    listModels: async () => [
      { id: "gpt-5.6-luna", policy: { state: "enabled" } },
      { id: "gpt-6-astra", policy: { state: "enabled" } },
    ],
  });
  const astra = new Set(["intake", "analyzer", "translator", "fixer", "parser-recovery"]);
  for (const role of ROLES) {
    const expected = astra.has(role) ? "gpt-6-astra" : "gpt-5.6-luna";
    assert.ok(lines.includes(`effective ${role}: ${expected}`), `${role}\n${lines.join("\n")}`);
  }
});

// B3: GitHub is opt-in -- `github.enabled` (default false) or `--gh` for one run.
test("--gh parses, and GitHub integration is off unless the config or the flag turns it on", async () => {
  assert.equal(parseArgs(["--gh"]).gh, true);
  assert.equal(parseArgs([]).gh, undefined);
  assert.equal(DEFAULT_CONFIG.github?.enabled, false);

  const { root } = await makeEnv({ wf: "wf_0001" });
  assert.equal((await loadConfig(root)).github?.enabled, false);
  await writeJson(`${root}/orchestrator.config.json`, { github: { enabled: true } });
  assert.equal((await loadConfig(root)).github?.enabled, true);

  const committed = await loadConfig(REPO_ROOT);
  assert.equal(committed.github?.enabled, false, "the committed orchestrator.config.json keeps GitHub off");
});

test("with GitHub off, gh is never invoked -- not even to see whether it is installed", async () => {
  let probes = 0;
  const probe = async () => {
    probes += 1;
    return true;
  };
  assert.deepEqual(await githubAccess(DEFAULT_CONFIG, {}, probe), { enabled: false, hasGh: false });
  assert.equal(probes, 0);

  assert.deepEqual(await githubAccess(DEFAULT_CONFIG, { gh: true }, probe), { enabled: true, hasGh: true });
  assert.deepEqual(await githubAccess({ ...DEFAULT_CONFIG, github: { enabled: true } }, {}, probe),
    { enabled: true, hasGh: true });
  assert.equal(probes, 2);
  assert.deepEqual(await githubAccess(DEFAULT_CONFIG, { gh: true }, async () => false), { enabled: true, hasGh: false });
});

// ---------- Task P4 fix round 2, M15 ---------------------------------------------------------------
// A child's output arrives in chunks; a multi-byte UTF-8 character split across two chunks must not
// turn into two replacement characters. Every script now writes UTF-8 (fix round 1, B4), so a Python
// child printing a path or a message with a non-ASCII character hits this in practice.
test("a multi-byte character split across two pipe chunks reaches the orchestrator intact", async () => {
  const { root } = await makeEnv({ wf: "wf_0001" });
  const env = makeRealEnv({
    root,
    config: DEFAULT_CONFIG,
    runner: { run: async () => ({ ok: true, toolCalls: 0, ms: 0 }) } as never,
    interactive: false,
    hasGh: false,
    ghEnabled: false,
  });
  // "→" is E2 86 92: the first byte, a pause long enough for a separate chunk, then the other two.
  const script =
    "const w = (s, b) => s.write(Buffer.from(b)); w(process.stdout, [0xe2]); w(process.stderr, [0xe2]);" +
    "setTimeout(() => { w(process.stdout, [0x86, 0x92]); w(process.stderr, [0x86, 0x92]); }, 200);";
  const result = await env.sh(process.execPath, ["-e", script]);
  assert.equal(result.ok, true);
  assert.equal(result.out, "\u2192");
  assert.equal(result.err, "\u2192");
});

// ---------- live hardening, Task L1 (R1): the project's interpreter is `python` in every session ----------
// Live evidence (docs/live-smoke-test.md "Third live test"): every agent file said "run
// .venv/Scripts/python.exe scripts/\u2026", but a run root is a copy WITHOUT .venv whose
// orchestrator.config.json names the interpreter by absolute path, and `python` on the machine's
// PATH is the system interpreter without the project's packages -- so the models probed for an
// interpreter (Test-Path, Get-Command, Get-ChildItem .venv\Scripts) and were refused. The session's
// runtime now gets the configured interpreter's directory first on PATH.

const WINDOWS_RUN = { python: "C:/Users/<user>/checkout/.venv/Scripts/python.exe" };

test("L1 R1: Windows -- the interpreter's directory goes first on the existing Path key, never a second key", () => {
  const base = { Path: "C:\\Windows\\system32;C:\\Tools", SystemRoot: "C:\\Windows", COPILOT_HOME: "C:\\h" };
  const env = sessionEnvironment(WINDOWS_RUN, "C:\\mig\\runs\\r1", base, "win32");
  assert.equal(env.Path, "C:\\Users\\<user>\\checkout\\.venv\\Scripts;C:\\Windows\\system32;C:\\Tools");
  assert.equal("PATH" in env, false, "no duplicate key: Windows keys are case-insensitive");
  assert.equal(env.SystemRoot, "C:\\Windows", "every other variable is passed through");
  assert.equal(env.COPILOT_HOME, "C:\\h");
  assert.equal(base.Path, "C:\\Windows\\system32;C:\\Tools", "base is never modified");

  const upper = sessionEnvironment(WINDOWS_RUN, "C:\\mig\\runs\\r1", { PATH: "C:\\Windows" }, "win32");
  assert.deepEqual(upper, { PATH: "C:\\Users\\<user>\\checkout\\.venv\\Scripts;C:\\Windows" });

  const both = sessionEnvironment(WINDOWS_RUN, "C:\\mig\\runs\\r1", { Path: "C:\\A", PATH: "C:\\B" }, "win32");
  assert.equal(Object.keys(both).filter((key) => key.toUpperCase() === "PATH").length, 1, JSON.stringify(both));
  assert.equal(both.Path, "C:\\Users\\<user>\\checkout\\.venv\\Scripts;C:\\A;C:\\B", "fix round 1 (M7): no directory is lost");

  const none = sessionEnvironment(WINDOWS_RUN, "C:\\mig\\runs\\r1", { SystemRoot: "C:\\Windows" }, "win32");
  assert.equal(none.PATH, "C:\\Users\\<user>\\checkout\\.venv\\Scripts");
});

test("L1 fix 1 (M7): Path and PATH together are merged into the first key, interpreter first, each entry once", () => {
  const base = {
    SystemRoot: "C:\\Windows",
    Path: "C:\\Windows\\system32;C:\\Tools;C:\\Users\\<user>\\checkout\\.venv\\Scripts",
    PATH: "c:\\tools\\;C:\\Extra;;C:\\Windows\\System32",
  };
  const env = sessionEnvironment(WINDOWS_RUN, "C:\\mig", base, "win32");
  assert.deepEqual(Object.keys(env).filter((key) => key.toUpperCase() === "PATH"), ["Path"]);
  assert.equal(env.Path, "C:\\Users\\<user>\\checkout\\.venv\\Scripts;C:\\Windows\\system32;C:\\Tools;C:\\Extra");
  assert.deepEqual(sessionEnvironment(WINDOWS_RUN, "C:\\mig", env, "win32"), env, "still idempotent");
  assert.equal(base.PATH, "c:\\tools\\;C:\\Extra;;C:\\Windows\\System32", "base is never modified");
});

test("L1 R1: POSIX -- ':' separators, and only PATH itself is PATH", () => {
  const env = sessionEnvironment({ python: "/opt/checkout/.venv/bin/python" }, "/srv/run", { PATH: "/usr/bin:/bin", Path: "unrelated" }, "linux");
  assert.equal(env.PATH, "/opt/checkout/.venv/bin:/usr/bin:/bin");
  assert.equal(env.Path, "unrelated", "POSIX keys are case-sensitive: Path is another variable");
});

test("L1 R1: a relative config.python resolves against the run root, exactly as the orchestrator resolves it", () => {
  const windows = sessionEnvironment({ python: ".venv/Scripts/python.exe" }, "C:\\mig\\runs\\r1", { Path: "C:\\Windows" }, "win32");
  assert.equal(windows.Path, "C:\\mig\\runs\\r1\\.venv\\Scripts;C:\\Windows");
  const posix = sessionEnvironment({ python: ".venv/bin/python" }, "/srv/run", { PATH: "/usr/bin" }, "linux");
  assert.equal(posix.PATH, "/srv/run/.venv/bin:/usr/bin");
  // on this host, the same resolution makeEnv's py and assertPythonExists use
  const root = path.resolve("some-run-root");
  const here = sessionEnvironment({ python: path.join(".venv", "bin", "python") }, root, { PATH: "" });
  assert.equal(here.PATH, path.dirname(path.resolve(root, path.join(".venv", "bin", "python"))));
});

test("L1 R1: idempotent -- applying it twice is applying it once, and an existing entry is moved, not repeated", () => {
  const base = { Path: "C:\\Windows;c:/users/<user>/checkout/.venv/scripts/;C:\\Tools;;" };
  const once = sessionEnvironment(WINDOWS_RUN, "C:\\mig", base, "win32");
  assert.equal(once.Path, "C:\\Users\\<user>\\checkout\\.venv\\Scripts;C:\\Windows;C:\\Tools");
  assert.deepEqual(sessionEnvironment(WINDOWS_RUN, "C:\\mig", once, "win32"), once);

  const posixOnce = sessionEnvironment({ python: "/opt/v/bin/python" }, "/", { PATH: "/usr/bin:/opt/v/bin/:/bin" }, "linux");
  assert.equal(posixOnce.PATH, "/opt/v/bin:/usr/bin:/bin");
  assert.deepEqual(sessionEnvironment({ python: "/opt/v/bin/python" }, "/", posixOnce, "linux"), posixOnce);
});

test("L1 R1: the agent-session CopilotClient is built with that environment, and the SDK accepts it", async () => {
  const root = path.resolve("some-run-root");
  const options = copilotClientOptions({ ...DEFAULT_CONFIG, python: path.join(".venv", "bin", "python") }, root);
  const pathKey = Object.keys(options.env).find((key) => key.toUpperCase() === "PATH")!;
  assert.equal(options.env[pathKey]!.split(path.delimiter)[0], path.join(root, ".venv", "bin"));
  // Constructing the client starts nothing (no runtime process until start()); it must accept `env`.
  const { CopilotClient } = await import("@github/copilot-sdk");
  const client = new CopilotClient(options);
  assert.equal((client as unknown as { resolvedEnv: Record<string, string | undefined> }).resolvedEnv, options.env);
});

// The check the ruling asks for, in a real subprocess and skipping nothing: with the builder's PATH,
// `python` is the configured interpreter and imports the project's packages. The interpreter is
// found the way integration.test.ts finds it (PIPELINE_PYTHON, this tree's .venv, the shared
// checkout's .venv); a machine without one fails here, loudly, instead of skipping.
const INTERPRETER_CANDIDATES = [
  process.env.PIPELINE_PYTHON,
  path.join(REPO_ROOT, ".venv", "Scripts", "python.exe"),
  path.join(REPO_ROOT, ".venv", "bin", "python"),
  path.join(REPO_ROOT, "..", "..", ".venv", "Scripts", "python.exe"),
  path.join(REPO_ROOT, "..", "..", ".venv", "bin", "python"),
].filter((candidate): candidate is string => Boolean(candidate));

test("L1 R1: with the session environment, `python -c \"import duckdb, sqlglot, yaml\"` runs the configured interpreter", async () => {
  const interpreter = INTERPRETER_CANDIDATES.find((candidate) => existsSync(candidate));
  assert.ok(interpreter, `the project's interpreter is a prerequisite of this check; none of ${INTERPRETER_CANDIDATES.join(", ")} exists (set PIPELINE_PYTHON)`);
  // A relative config.python against a root, the way a run root's config may name it.
  const root = path.resolve(interpreter, "..", "..", "..");
  const config = { python: path.relative(root, interpreter) };
  // The base PATH is this process's own, minus any entry that already names the interpreter's
  // directory -- so only the builder can be what puts it there.
  const dir = path.dirname(path.resolve(interpreter));
  const same = (entry: string) => path.resolve(entry).toLowerCase() === dir.toLowerCase();
  const base = { ...process.env };
  const key = Object.keys(base).find((name) => (process.platform === "win32" ? name.toUpperCase() === "PATH" : name === "PATH")) ?? "PATH";
  base[key] = (base[key] ?? "").split(path.delimiter).filter((entry) => entry && !same(entry)).join(path.delimiter);

  const env = sessionEnvironment(config, root, base);
  const { stdout } = await new Promise<{ stdout: string; stderr: string }>((resolve, reject) => {
    execFile(
      "python",
      ["-c", "import sys, duckdb, sqlglot, yaml; print(sys.executable)"],
      { env, cwd: os.tmpdir(), windowsHide: true },
      (error, out, err) => (error ? reject(new Error(`${error.message}\n${err}`)) : resolve({ stdout: out, stderr: err })),
    );
  });
  const ran = path.resolve(stdout.trim());
  const expected = path.resolve(interpreter);
  assert.equal(process.platform === "win32" ? ran.toLowerCase() : ran, process.platform === "win32" ? expected.toLowerCase() : expected);
});
