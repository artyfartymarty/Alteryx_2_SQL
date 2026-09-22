// Flags, workflow selection and the exit-code contract.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { makeEnv, seedWorkflow } from "./fakes.ts";
import { parseArgs, main, loadConfig, assertPythonExists, DEFAULT_CONFIG, UsageError } from "../cli.ts";
import { writeJson } from "../manifest.ts";

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

test("--tier selects only workflows already classified as that tier", async (t) => {
  const { env, calls } = await makeEnv({ wf: "wf_0005", manifest: { tier: "T3" } });
  quiet(t);
  assert.equal(await main(["--tier", "T1"], { env }), 0);
  assert.equal(calls.py.length, 0, "the T3 workflow is not selected");
  assert.equal(await main(["--tier", "T3"], { env }), 0);
  assert.ok(calls.py.length > 0);
});
