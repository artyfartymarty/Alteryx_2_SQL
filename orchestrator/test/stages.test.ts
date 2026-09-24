// The state machine: docs/spec/01-copilot-setup.md Part B §3-§5.
// The first nine tests are the task brief's contract, verbatim.
import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { makeEnv, readManifest } from "./fakes.ts";
import { dbtModelName, dbtReadme, migrateWorkflow, masterSql, plannedStages, SESSION_RULE_LINES, SESSION_RULES } from "../stages.ts";
import { readJsonOr, saveManifest, writeJson } from "../manifest.ts";
import { WORKFLOW_ID_SCRIPTS } from "../policy.ts";
import { CopilotRunner } from "../runner.ts";
import type { CopilotClient, SessionHooks } from "@github/copilot-sdk";
import type { AgentCtx, AgentRunner, Env, Manifest, Role } from "../types.ts";

test("happy path reaches VALIDATED and documents", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.deepEqual([m.status.parse, m.status.intake, m.status.analyze, m.status.translate, m.status.document], ["PARSED", "READY", "DONE", "VALIDATED", "DONE"]);
  assert.equal(m.status.pr, undefined);                                   // gh is absent in the fake env
  assert.deepEqual(calls.roles, ["intake", "analyzer", "translator", "reviewer", "validator", "documenter"]);
});
test("unanswered intake parks the workflow and a later run resumes it", async () => {
  const { env, calls, answerIntake } = await makeEnv({ wf: "wf_0001", intake: "WAITING_FOR_ANSWERS" });
  assert.equal((await migrateWorkflow(env, "wf_0001", {})).status.intake, "WAITING_FOR_ANSWERS");
  assert.ok(!calls.roles.includes("analyzer"));
  answerIntake();
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.equal(calls.py.filter((c) => c.script.endsWith("parse.py")).length, 1);      // parse was not repeated
});
test("a failing segment is fixed on the second iteration", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "fix-loop:seg_01" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.segment_status!.seg_01, "PASS");
  assert.deepEqual(calls.roles.filter((r) => r === "translator" || r === "fixer"), ["translator", "fixer"]);
});
test("three failed iterations escalate to NEEDS_HUMAN and stop the workflow", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "never-fixed:seg_01" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 2);
  assert.ok(!calls.roles.includes("documenter"));
});
test("two failed recoveries quarantine the workflow as T3", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0005", scenario: "recovery-fails" });
  const m = await migrateWorkflow(env, "wf_0005", {});
  assert.deepEqual([m.status.parse, m.tier], ["QUARANTINED", "T3"]);
  assert.equal(calls.roles.filter((r) => r === "parser-recovery").length, 2);
});
test("a recovered T3 workflow ends MANUAL without translating", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0005" });
  const m = await migrateWorkflow(env, "wf_0005", {});
  // Coordinator ruling (task-15-int, 13b's reported defect): tier T3 short-circuits analyze
  // straight to MANUAL, with no per-segment contract.json ever required or written — the fixture
  // for wf_0005 deliberately has no segments/ or broken_sql/ tree at all (see fakes.ts), matching
  // the real samples/wf_0005/canned/, which has none either.
  assert.deepEqual(
    [m.status.parse, m.tier, m.status.analyze, m.status.translate],
    ["PARSED", "T3", "DONE", "MANUAL"],
  );
  assert.equal(m.reasons?.translate, "tier-T3");
  assert.deepEqual(calls.roles, ["parser-recovery", "intake", "analyzer"]);
  assert.ok(!calls.roles.includes("translator") && !calls.roles.includes("reviewer") && !calls.roles.includes("validator"));
});
test("a T3 workflow's manifest still mirrors targets.json's output_kind", async () => {
  // output-targets design §3.3: `targets.json.output_kind` is MIRRORED into
  // `manifest.json.output_kind`, and `target_check.py` writes `targets.json` for a T3 workflow
  // like any other. T3 has no contract.json for §3.2's lower-only check to run against, but the
  // kind the script decided is still the decision, and the manifest must record it rather than
  // leaving a reader to guess.
  const { env } = await makeEnv({ wf: "wf_0005" });
  const m = await migrateWorkflow(env, "wf_0005", {});
  assert.equal(m.tier, "T3");
  assert.equal(m.status.translate, "MANUAL");
  assert.equal(m.output_kind, "procedures");
  assert.equal(
    (await readJsonOr<{ output_kind?: string }>(path.join(env.root, "workflows", "wf_0005", "segments", "targets.json"), {})).output_kind,
    m.output_kind,
    "the manifest's output_kind is targets.json's, not an independently invented one",
  );
});
test("re-running a T3 workflow already at MANUAL does nothing (golden is never applicable)", async () => {
  // docs/spec/01-copilot-setup.md's state diagram draws `analyze --> MANUAL: tier T3` as a dead
  // end with no outgoing edge at all — golden/document/pr never apply to a T3 workflow, on the
  // first pass or any later one. The first pass never even reaches golden's shouldRun check
  // (stageAnalyze's own "stop" breaks the loop first), so only a SECOND migrateWorkflow call,
  // restarting the stage loop from the top, can expose golden being attempted anyway.
  const { env, calls } = await makeEnv({ wf: "wf_0005" });
  const first = await migrateWorkflow(env, "wf_0005", {});
  assert.equal(first.status.translate, "MANUAL");
  assert.equal(first.status.golden, undefined);
  const n = calls.roles.length;
  const nPy = calls.py.length;

  const second = await migrateWorkflow(env, "wf_0005", {});
  assert.equal(second.status.golden, undefined, "golden must stay untouched, not BLOCKED");
  assert.equal(second.status.translate, "MANUAL");
  assert.equal(calls.roles.length, n, "no agent ran again");
  assert.equal(calls.py.filter((c) => c.script.includes("alteryx_sim")).length, 0, "the simulator was never invoked for a T3 workflow");
  assert.equal(calls.py.length, nPy, "no script ran again");
});
test("rate limits back off and retry; a denied tool escalates at once", async () => {
  const a = await makeEnv({ wf: "wf_0001", agentErrors: { analyzer: ["rate-limit", "rate-limit"] } });
  assert.equal((await migrateWorkflow(a.env, "wf_0001", {})).status.analyze, "DONE");
  assert.deepEqual(a.calls.sleeps, [1000, 2000]);
  const b = await makeEnv({ wf: "wf_0001", agentErrors: { analyzer: ["denied"] } });
  assert.equal((await migrateWorkflow(b.env, "wf_0001", {})).status.analyze, "NEEDS_HUMAN");
});
test("re-running a finished workflow does nothing; --from-stage reopens it", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  await migrateWorkflow(env, "wf_0001", {}); const n = calls.roles.length;
  await migrateWorkflow(env, "wf_0001", {}); assert.equal(calls.roles.length, n);
  await migrateWorkflow(env, "wf_0001", { fromStage: "translate" }); assert.ok(calls.roles.length > n);
});
test("master.sql calls segments in wave order", () => {
  const sql = masterSql("wf_0003", [["seg_01"], ["seg_02"]]);
  assert.ok(sql.indexOf("WF0003_SEG_01(") < sql.indexOf("WF0003_SEG_02(") && sql.includes("WF0003_MASTER"));
});

// --- behaviour the brief states in prose but does not test ---

test("the happy path leaves the artifacts the next stage reads", async () => {
  const { env, files } = await makeEnv({ wf: "wf_0001" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.deepEqual(m.golden_sets, ["normal", "period_end", "empty", "edge"]);
  assert.deepEqual(m.segments, ["seg_01"]);
  assert.equal(await files.exists("workflows", "wf_0001", "intake", "plan.md"), true);
  assert.equal(await files.exists("workflows", "wf_0001", "segments", "seg_01", "contract.json"), true);
  assert.equal(await files.exists("workflows", "wf_0001", "docs", "migration.md"), true);
  const master = await files.read("workflows", "wf_0001", "procs", "master.sql");
  assert.match(master ?? "", /CALL MIG_WORK\.WF0001_SEG_01\(/);
  const onDisk = await readManifest(env.root, "wf_0001");
  assert.equal(onDisk.status.translate, "VALIDATED", "the manifest is saved, not just returned");
});
test("a compile error ends the iteration before review and validation", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "compile-fails:seg_01" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.segment_status!.seg_01, "NEEDS_HUMAN");
  assert.ok(!calls.roles.includes("reviewer"));
  assert.ok(!calls.roles.includes("validator"));
  assert.equal(calls.py.filter((c) => c.script.endsWith("compile_check.py")).length, 3);
});
test("needs_human short-circuits the fix loop after one iteration", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "needs-human:seg_01" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 0);
  assert.equal(calls.roles.filter((r) => r === "validator").length, 1);
});
// task-15-int brief, coordinator ruling 4: a PASS_WITH_ACCEPTED_DIFF report that also says
// needs_human: true must not be read as validated just because its verdict starts with "PASS".
test("needs_human wins even when the verdict itself starts with PASS", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "needs-human-pass:seg_01" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.notEqual(m.segment_status!.seg_01, "PASS_WITH_ACCEPTED_DIFF");
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 0);
  assert.equal(calls.roles.filter((r) => r === "validator").length, 1);
});
test("a workflow the simulator cannot produce golden data for stops at golden", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", golden: "empty" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.golden, "BLOCKED");
  assert.equal(m.status.translate, undefined);
  assert.ok(!calls.roles.includes("translator"));
});
test("the alteryx producer stops at golden with instructions instead of guessing data", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", config: { golden: { producer: "alteryx" } } });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.golden, "BLOCKED");
  assert.ok(calls.logs.some((line) => line.includes("inject_outputs.py")));
  assert.equal(calls.py.filter((c) => c.script.includes("alteryx_sim")).length, 0);
});
test("intake BLOCKED stops the workflow where WAITING would have parked it", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", intake: "BLOCKED" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.intake, "BLOCKED");
  assert.ok(!calls.roles.includes("analyzer"));
});
test("WAITING_FOR_ANSWERS opens an issue when gh is there and logs the path when it is not", async () => {
  const withGh = await makeEnv({ wf: "wf_0001", intake: "WAITING_FOR_ANSWERS", hasGh: true, ghEnabled: true });
  await migrateWorkflow(withGh.env, "wf_0001", {});
  const issue = withGh.calls.sh.find((c) => c.cmd === "gh");
  assert.ok(issue, "gh issue create is attempted when gh is installed");
  assert.deepEqual(issue!.args.slice(0, 2), ["issue", "create"]);

  const withoutGh = await makeEnv({ wf: "wf_0001", intake: "WAITING_FOR_ANSWERS", ghEnabled: true });
  await migrateWorkflow(withoutGh.env, "wf_0001", {});
  assert.equal(withoutGh.calls.sh.length, 0);
  assert.ok(withoutGh.calls.logs.some((line) => line.includes("open_questions.md")));
});
test("a PR is opened only when gh is installed", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", hasGh: true, ghEnabled: true });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.pr, "OPEN");
  assert.deepEqual(calls.sh[0].args.slice(0, 2), ["pr", "create"]);

  const offline = await makeEnv({ wf: "wf_0001", ghEnabled: true });
  const n = await migrateWorkflow(offline.env, "wf_0001", {});
  assert.equal(n.status.pr, undefined);
  assert.ok(offline.calls.logs.some((line) => /gh not installed/.test(line)));
});
// Task P4 fix round 1, B3: GitHub is opt-in. gh installed but GitHub integration off: no issue, no
// PR, no gh call of any kind -- the stages log `gh: disabled` and carry on exactly as without gh.
test("with GitHub integration off, an installed gh is never called and the stages log gh: disabled", async () => {
  const waiting = await makeEnv({ wf: "wf_0001", intake: "WAITING_FOR_ANSWERS", hasGh: true, ghEnabled: false });
  const w = await migrateWorkflow(waiting.env, "wf_0001", {});
  assert.equal(w.status.intake, "WAITING_FOR_ANSWERS");
  assert.equal(waiting.calls.sh.length, 0, "no gh issue create");
  assert.ok(waiting.calls.logs.some((line) => line.includes("gh: disabled") && line.includes("open_questions.md")));

  const done = await makeEnv({ wf: "wf_0001", hasGh: true, ghEnabled: false });
  const m = await migrateWorkflow(done.env, "wf_0001", {});
  assert.equal(m.status.document, "DONE");
  assert.equal(m.status.pr, undefined);
  assert.equal(done.calls.sh.length, 0, "no gh pr create");
  assert.ok(done.calls.logs.some((line) => line.includes("gh: disabled")));
});
test("the fake env, like the orchestrator, has GitHub integration off by default", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", hasGh: true });
  await migrateWorkflow(env, "wf_0001", {});
  assert.equal(calls.sh.length, 0);
});
test("--stop-after returns before the next stage and --dry-run touches nothing", async () => {
  const stopped = await makeEnv({ wf: "wf_0001" });
  const m = await migrateWorkflow(stopped.env, "wf_0001", { stopAfter: "analyze" });
  assert.equal(m.status.analyze, "DONE");
  assert.equal(m.status.translate, undefined);
  assert.deepEqual(stopped.calls.roles, ["intake", "analyzer"]);

  const dry = await makeEnv({ wf: "wf_0001" });
  const d = await migrateWorkflow(dry.env, "wf_0001", { dryRun: true });
  assert.deepEqual(d.status, {});
  assert.equal(dry.calls.py.length, 0);
  assert.equal(dry.calls.roles.length, 0);
  assert.ok(dry.calls.logs.some((line) => line.includes("parse")));
});
test("--from-stage clears that stage and every later one", async () => {
  const { env } = await makeEnv({ wf: "wf_0001" });
  await migrateWorkflow(env, "wf_0001", {});
  const m = await migrateWorkflow(env, "wf_0001", { fromStage: "analyze", stopAfter: "analyze" });
  assert.equal(m.status.analyze, "DONE");
  assert.equal(m.status.parse, "PARSED", "earlier stages keep their status");
  assert.equal(m.status.translate, undefined, "later stages were cleared");
  assert.equal(m.status.document, undefined);
});
test("a missing contract makes the analyzer retry once, then NEEDS_HUMAN", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", agentErrors: { analyzer: ["missing-output"] } });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "DONE", "one retry is enough when the second attempt writes the contract");
  assert.equal(calls.roles.filter((r) => r === "analyzer").length, 2);

  const twice = await makeEnv({ wf: "wf_0001", agentErrors: { analyzer: ["missing-output", "missing-output"] } });
  const n = await migrateWorkflow(twice.env, "wf_0001", {});
  assert.equal(n.status.analyze, "NEEDS_HUMAN");
  assert.equal(twice.calls.roles.filter((r) => r === "analyzer").length, 2);
});
test("four rate limits exhaust the retries and escalate", async () => {
  const { env, calls } = await makeEnv({
    wf: "wf_0001",
    agentErrors: { analyzer: ["rate-limit", "rate-limit", "rate-limit", "rate-limit"] },
  });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.deepEqual(calls.sleeps, [1000, 2000, 4000]);
  assert.equal(calls.roles.filter((r) => r === "analyzer").length, 4);
});
test("a tool-call budget overrun stops the workflow before the next agent", async () => {
  const { env, calls } = await makeEnv({
    wf: "wf_0001",
    manifest: { metrics: { translator: { toolCalls: 401 } } },
  });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.intake, "NEEDS_HUMAN");
  assert.equal(calls.roles.length, 0);
  assert.ok(calls.logs.some((line) => line.includes("budget")));
});
test("a script that exits 2 is an orchestrator error, not a domain verdict", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  const py = env.py;
  env.py = async (script, args, opts) => (script.endsWith("segment.py") ? { ok: false, code: 2, out: "", err: "usage: segment.py" } : py(script, args, opts));
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.equal(m.reasons!.analyze, "script-error");
  assert.ok(!calls.roles.includes("analyzer"));
});
// --- escalated statuses stay parked until --from-stage reopens them (fix round 1) -------------
// Coordinator ruling (task-15-int, fix round 1): NEEDS_HUMAN / QUARANTINED / golden's BLOCKED are
// not "nothing left to do" (TERMINAL_GOOD) and they are not "not started yet" either -- they are
// PARKED. A plain re-run must not re-invoke the stage's scripts or agents, and must not let any
// later stage run, until an operator explicitly reopens the workflow with --from-stage.

test("needs_human keeps a workflow parked: a second run makes no new calls and logs the resume line", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "needs-human:seg_01" });
  const first = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(first.status.translate, "NEEDS_HUMAN");
  const rolesAfterFirst = calls.roles.length;
  const pyAfterFirst = calls.py.length;

  const second = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(second.status.translate, "NEEDS_HUMAN");
  assert.equal(calls.roles.length, rolesAfterFirst, "no new agent calls on the second run");
  assert.equal(calls.py.length, pyAfterFirst, "no new script calls on the second run");
  assert.ok(
    calls.logs.some((line) => line.includes("--from-stage translate --only wf_0001")),
    "the resume line is logged",
  );
});

test("a quarantined workflow stays parked: a second run makes no new parse/parser-recovery calls", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0005", scenario: "recovery-fails" });
  const first = await migrateWorkflow(env, "wf_0005", {});
  assert.deepEqual([first.status.parse, first.tier], ["QUARANTINED", "T3"]);
  const rolesAfterFirst = calls.roles.length;
  const parseCallsAfterFirst = calls.py.filter((c) => c.script.endsWith("parse.py")).length;

  const second = await migrateWorkflow(env, "wf_0005", {});
  assert.equal(second.status.parse, "QUARANTINED");
  assert.equal(calls.roles.length, rolesAfterFirst, "no new parser-recovery calls on the second run");
  assert.equal(
    calls.py.filter((c) => c.script.endsWith("parse.py")).length,
    parseCallsAfterFirst,
    "no new parse.py calls on the second run",
  );
  assert.ok(
    calls.logs.some((line) => line.includes("--from-stage parse --only wf_0005")),
    "the resume line is logged",
  );
});

test("a golden BLOCKED workflow stays parked on a second run", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", golden: "empty" });
  const first = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(first.status.golden, "BLOCKED");
  const simCallsAfterFirst = calls.py.filter((c) => c.script.includes("alteryx_sim")).length;

  const second = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(second.status.golden, "BLOCKED");
  assert.equal(
    calls.py.filter((c) => c.script.includes("alteryx_sim")).length,
    simCallsAfterFirst,
    "the simulator is not re-run",
  );
  assert.ok(!calls.roles.includes("translator"));
  assert.ok(calls.logs.some((line) => line.includes("--from-stage golden --only wf_0001")));
});

test("--from-stage translate reopens a NEEDS_HUMAN workflow and re-invokes the translator", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "needs-human:seg_01" });
  await migrateWorkflow(env, "wf_0001", {});
  assert.equal(calls.roles.filter((r) => r === "translator").length, 1);

  const m = await migrateWorkflow(env, "wf_0001", { fromStage: "translate" });
  assert.equal(calls.roles.filter((r) => r === "translator").length, 2, "the translator ran again");
  assert.notEqual(m.status.translate, undefined);
});

test("--from-stage on a later stage does not skip past an earlier parked stage", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", golden: "empty" });
  const first = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(first.status.golden, "BLOCKED");
  const rolesAfterFirst = calls.roles.length;
  const simCallsAfterFirst = calls.py.filter((c) => c.script.includes("alteryx_sim")).length;

  // "document" comes after "golden" in STAGES order; --from-stage document clears document/pr
  // but must not silently reopen, or step over, the still-parked golden stage.
  const second = await migrateWorkflow(env, "wf_0001", { fromStage: "document" });
  assert.equal(second.status.golden, "BLOCKED", "golden was not touched by clearFromStage");
  assert.equal(second.status.document, undefined, "document was cleared but never reached");
  assert.equal(calls.roles.length, rolesAfterFirst, "no agent ran -- the parked golden stage still blocks");
  assert.equal(
    calls.py.filter((c) => c.script.includes("alteryx_sim")).length,
    simCallsAfterFirst,
    "golden's own script did not re-run either",
  );
});

test("plannedStages for a parked workflow is empty", async () => {
  const { env } = await makeEnv({ wf: "wf_0001", scenario: "needs-human:seg_01" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.deepEqual(plannedStages(m, {}), []);
});

test("WAITING_FOR_ANSWERS is not an escalation: intake scripts still re-run every pass", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", intake: "WAITING_FOR_ANSWERS" });
  await migrateWorkflow(env, "wf_0001", {});
  const touchpointsAfterFirst = calls.py.filter((c) => c.script.endsWith("intake_touchpoints.py")).length;
  const promptAfterFirst = calls.py.filter((c) => c.script.endsWith("intake_prompt.py")).length;

  await migrateWorkflow(env, "wf_0001", {});
  assert.equal(
    calls.py.filter((c) => c.script.endsWith("intake_touchpoints.py")).length,
    touchpointsAfterFirst + 1,
    "intake_touchpoints.py ran again",
  );
  assert.equal(
    calls.py.filter((c) => c.script.endsWith("intake_prompt.py")).length,
    promptAfterFirst + 1,
    "intake_prompt.py ran again",
  );
});

test("masterSql is a contract-shaped procedure that calls every wave in order", () => {
  const sql = masterSql("wf_0003", [["seg_01", "seg_02"], ["seg_03"]]);
  assert.match(sql, /CREATE OR REPLACE PROCEDURE MIG_WORK\.WF0003_MASTER\(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING\)/);
  assert.match(sql, /RETURNS STRING LANGUAGE SQL\s+EXECUTE AS CALLER/);
  assert.match(sql, /CALL MIG_WORK\.WF0003_SEG_02\(:SRC_DB, :SRC_SCHEMA, :TGT_DB, :TGT_SCHEMA, :RUN_ID\);/);
  assert.ok(sql.indexOf("wave 1") < sql.indexOf("wave 2"));
  assert.match(sql, /RETURN 'OK';\nEND;/);
});

// Follow-up to Task W2: Snowflake CLI, SnowSQL and the Python connector's execute_stream /
// execute_string do not parse a Snowflake Scripting block unless it is delimited, so the master's
// `BEGIN … END;` travels inside `$$ … $$` exactly like every segment's proc.sql. The fixture is the
// same file tests/test_proc_runner.py parses with `lib.proc_runner`.
test("masterSql wraps its Scripting body in $$ like every segment procedure", async () => {
  const sql = masterSql("wf_0003", [["seg_01", "seg_02"], ["seg_03"]]);
  const fixture = await readFile(fileURLToPath(new URL("fixtures/master_wf_0003.sql", import.meta.url)), "utf8");
  assert.equal(sql, fixture.replace(/\r\n/g, "\n"));
  assert.match(sql, /^-- Generated by orchestrate\.ts/);
  assert.match(sql, /EXECUTE AS CALLER\nAS\n\$\$\nBEGIN\n/);
  assert.ok(sql.endsWith("  RETURN 'OK';\nEND;\n$$;\n"));
  assert.equal(sql.split("$$").length, 3, "exactly one $$ pair");
});

// --- F8 (ruling): --from-stage grants a fresh tool-call budget --------------------------------

test("F8: a plain re-run of an over-budget workflow still parks; --from-stage grants a fresh budget and lets agents run again", async () => {
  const stuck = await makeEnv({ wf: "wf_0001", manifest: { metrics: { translator: { toolCalls: 401 } } } });
  const first = await migrateWorkflow(stuck.env, "wf_0001", {});
  assert.equal(first.status.intake, "NEEDS_HUMAN");
  assert.equal(stuck.calls.roles.length, 0);

  // A plain re-run (no --from-stage) must still park -- the budget is not reset for free.
  const second = await migrateWorkflow(stuck.env, "wf_0001", {});
  assert.equal(second.status.intake, "NEEDS_HUMAN");
  assert.equal(stuck.calls.roles.length, 0);

  const reopened = await migrateWorkflow(stuck.env, "wf_0001", { fromStage: "intake" });
  assert.notEqual(reopened.status.intake, "NEEDS_HUMAN", "the reopened workflow gets past intake");
  assert.ok(stuck.calls.roles.length > 0, "agents ran again after the budget reset");
  assert.ok(
    stuck.calls.logs.some((line) => line.includes("reset the tool-call budget")),
    "the reset is logged",
  );
});

test("F8: metrics after the reopened run count only the NEW calls, not the stale spend --from-stage reset", async () => {
  const { env } = await makeEnv({ wf: "wf_0001", manifest: { metrics: { translator: { toolCalls: 401, lastMs: 999 } } } });
  await migrateWorkflow(env, "wf_0001", {}); // parks; budget untouched
  const reopened = await migrateWorkflow(env, "wf_0001", { fromStage: "intake" });
  assert.equal(reopened.metrics.translator?.toolCalls, 0, "the stale role's toolCalls was reset to 0");
  assert.equal(reopened.metrics.translator?.lastMs, 999, "lastMs (a duration, not spend) is kept, per the ruling");
  // The real workflow's own agent calls (MockRunner records 0 toolCalls per call) must not have
  // silently re-inflated the budget back past where it started.
  const total = Object.values(reopened.metrics as Record<string, { toolCalls?: number }>).reduce(
    (sum, entry) => sum + (entry.toolCalls ?? 0),
    0,
  );
  assert.ok(total < 401, "the workflow's total spend after the reset is nowhere near the old budget overrun");
});

test("F8: a --from-stage on a workflow that was never over budget logs nothing about a reset", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  await migrateWorkflow(env, "wf_0001", {});
  await migrateWorkflow(env, "wf_0001", { fromStage: "translate" });
  assert.ok(!calls.logs.some((line) => line.includes("reset the tool-call budget")));
});

// --- F10: the real-Alteryx golden path prints a command inject_outputs.py's argparse accepts ---

test("F10: the alteryx golden instructions name both required steps with flags the script's parser accepts", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", config: { golden: { producer: "alteryx" } } });
  await migrateWorkflow(env, "wf_0001", {});
  const message = calls.logs.find((line) => line.includes("inject_outputs.py"));
  assert.ok(message, "an alteryx-golden instruction line was logged");
  // scripts/inject_outputs.py's argparse: `--capture-dir` is REQUIRED on every invocation, and
  // only a SECOND pass with `--import-set <name>` turns captures into golden CSVs -- neither flag
  // was in the old one-line message.
  assert.match(message!, /--capture-dir/);
  assert.match(message!, /--import-set/);
  assert.match(message!, new RegExp(`inject_outputs\\.py wf_0001 --capture-dir \\S+`));
  assert.match(message!, new RegExp(`inject_outputs\\.py wf_0001 --capture-dir \\S+ --import-set \\S+`));
});

// --- F11 (ruling): no re-translation of already-PASSed segments; recorded NEEDS_HUMAN parks -----

test("F11: resuming mid-translate after wave 1 passed (the crash window) does not re-invoke agents for wave 1, and its proc.sql is untouched", async () => {
  // The real crash window: a process dies between wave 1's segment_status being saved and wave 2
  // ever running (or the stage's own final status being set) -- status.translate is still unset
  // (not NEEDS_HUMAN, not VALIDATED), so a plain re-run resumes stageTranslate from the top. This
  // is reproduced directly (rather than via a real interrupted process) by seeding exactly that
  // on-disk shape and starting a fresh run against it.
  const { env, calls, files, root } = await makeEnv({ wf: "wf_0001", twoWaves: true });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const onDisk = await readManifest(root, "wf_0001");
  assert.equal(onDisk.status.translate, undefined, "translate has not run yet");
  onDisk.segment_status = { seg_01: "PASS" }; // wave 1 already completed and was saved
  await writeJson(path.join(root, "workflows", "wf_0001", "manifest.json"), onDisk);

  const procPath = ["workflows", "wf_0001", "segments", "seg_01", "proc.sql"];
  // Simulate wave 1 having actually already been translated (and validated PASS) before the
  // crash: a real proc.sql on disk, distinguishable from anything migrateSegment would (re)write.
  const alreadyValidatedSql = "-- already validated PASS before the crash; must not be touched again\n";
  await mkdir(path.dirname(files.path(...procPath)), { recursive: true });
  await writeFile(files.path(...procPath), alreadyValidatedSql, "utf8");
  const contentBefore = await files.read(...procPath);
  const mtimeBefore = (await stat(files.path(...procPath))).mtimeMs;

  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED", "both segments end up passed");
  assert.equal(m.segment_status?.seg_01, "PASS", "seg_01's recorded verdict is kept, not recomputed");
  assert.equal(m.segment_status?.seg_02, "PASS", "seg_02 (never run before) is translated fresh");
  // Exactly one translator call happened (for seg_02) -- seg_01 made zero agent calls.
  assert.equal(calls.roles.filter((r) => r === "translator").length, 1);
  assert.equal(calls.roles.filter((r) => r === "reviewer").length, 1);
  assert.equal(calls.roles.filter((r) => r === "validator").length, 1);

  const contentAfter = await files.read(...procPath);
  const mtimeAfter = (await stat(files.path(...procPath))).mtimeMs;
  assert.equal(mtimeAfter, mtimeBefore, "seg_01's proc.sql was never rewritten");
  assert.equal(contentAfter, contentBefore);
});

test("F11: --from-stage translate clears segment_status so every segment is redone deliberately", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "needs-human:seg_02" });
  await migrateWorkflow(env, "wf_0001", {});
  const rolesBefore = calls.roles.length;

  const reopened = await migrateWorkflow(env, "wf_0001", { fromStage: "translate" });
  assert.ok(
    calls.roles.slice(rolesBefore).includes("translator"),
    "translator ran again for at least one segment after --from-stage translate",
  );
  // seg_01 (previously PASS) is redone too: --from-stage clears ALL of segment_status, per the
  // ruling, not just the failing segment's entry.
  assert.notEqual(reopened.segment_status?.seg_02, undefined);
});

test("F11 (crash window): a segment recorded NEEDS_HUMAN with status.translate unset parks the stage without invoking any agent", async () => {
  const { env, calls, root } = await makeEnv({ wf: "wf_0001" });
  // Get parse/intake/analyze/golden done for real, so segments/order.json exists, then stop
  // before translate ever runs.
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const onDisk = await readManifest(root, "wf_0001");
  assert.equal(onDisk.status.translate, undefined, "translate has not run yet");

  // Simulate exactly the crash-window shape the ruling describes: segment_status recorded, but
  // status.translate was never set (the pre-fix save ordering, or a process killed between the
  // two writes).
  onDisk.segment_status = { seg_01: "NEEDS_HUMAN" };
  await writeJson(path.join(root, "workflows", "wf_0001", "manifest.json"), onDisk);
  const rolesBefore = calls.roles.length;

  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN", "a recorded NEEDS_HUMAN segment parks the stage exactly like the status would");
  assert.equal(calls.roles.length, rolesBefore, "no translator/fixer/reviewer/validator (or any other) agent ran");
  assert.match(m.reasons?.translate ?? "", /seg_01/);
});

// --- F13: a translate escalation carries the failing segment + cause into reasons.translate -----

test("F13: exhausting the fix loop on validation FAIL records which segment and why", async () => {
  const { env } = await makeEnv({ wf: "wf_0001", scenario: "never-fixed:seg_01" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(m.reasons?.translate ?? "", /seg_01: validation FAIL after 3 iterations/);
});

test("F13: a needs_human validation report records 'needs_human' against its segment", async () => {
  const { env } = await makeEnv({ wf: "wf_0001", scenario: "needs-human:seg_01" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(m.reasons?.translate ?? "", /seg_01: needs_human/);
});

// --- output targets, phase 1 (Task 6A) --------------------------------------------------------
// docs/superpowers/specs/2026-09-22-output-targets-design.md §3 (decision rules and the lower-only
// verify), §4.2 (the Snowpark artefacts) and §6 (orchestrator). The deterministic script proposes
// a target per segment, the analyzer may only LOWER it (sql -> snowpark -> manual, never back
// towards sql), and the orchestrator verifies that before anything is translated.

test("analyze runs target_check.py --prefer auto after segment.py and before the analyzer, and records output_kind", async () => {
  const { env, calls, files } = await makeEnv({ wf: "wf_0001" });
  const m = await migrateWorkflow(env, "wf_0001", { stopAfter: "analyze" });

  const call = calls.py.find((c) => c.script === "scripts/target_check.py");
  assert.ok(call, "target_check.py ran");
  assert.deepEqual(call!.args, ["wf_0001", "--prefer", "auto"]);
  assert.ok(
    calls.order.indexOf("py:scripts/segment.py") < calls.order.indexOf("py:scripts/target_check.py"),
    "target_check.py runs after segment.py's cuts",
  );
  assert.ok(
    calls.order.indexOf("py:scripts/target_check.py") < calls.order.indexOf("agent:analyzer"),
    "…and before the analyzer, which reads targets.json",
  );
  const targets = JSON.parse((await files.read("workflows", "wf_0001", "segments", "targets.json")) ?? "{}");
  assert.equal(targets.output_kind, "procedures");
  assert.equal(m.output_kind, "procedures", "output_kind is mirrored from targets.json into the manifest");
  assert.match(
    calls.tasks.find((t) => t.role === "analyzer")!.task,
    /segments\/targets\.json/,
    "the analyzer is told where the proposal is",
  );
});

test("--prefer is always auto: target_check.py resolves a dbt preference itself, and output_kind follows", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", manifest: { output_target: "dbt" } });
  const m = await migrateWorkflow(env, "wf_0001", { stopAfter: "analyze" });
  assert.deepEqual(calls.py.find((c) => c.script === "scripts/target_check.py")!.args, ["wf_0001", "--prefer", "auto"]);
  assert.equal(m.output_kind, "dbt", "every contract is still sql, so the dbt preference survives");
});

test("a contract that RAISES a target parks analyze NEEDS_HUMAN with the exact target-mismatch reason", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "target-raise:seg_02" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.equal(m.reasons?.analyze, "target-mismatch: seg_02 raised snowpark to sql");
  assert.ok(!calls.roles.includes("translator"), "nothing is translated against a contradicted target");
});

test("a LOWERED target (sql -> snowpark) is accepted and logged", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "target-lower:seg_02" });
  const m = await migrateWorkflow(env, "wf_0001", { stopAfter: "analyze" });
  assert.equal(m.status.analyze, "DONE");
  assert.equal(m.reasons?.analyze, undefined);
  assert.ok(
    calls.logs.some((line) => /seg_02.*lowered.*sql.*snowpark/.test(line)),
    `a lowering is logged; logs were ${JSON.stringify(calls.logs)}`,
  );
});

// Final fix wave M5 (whole-branch review). `escalate` prefers `m.reasons[stage]` over the
// agent-level error, because a failed verify reaches it as a generic "missing-output". But the
// reason was only cleared INSIDE the verify callback, which runs only when the agent itself
// succeeded: an attempt that fails before verify (denied, timeout, budget) therefore parked with
// the PREVIOUS attempt's verify reason. Cleared at the top of the stage instead.
test("an analyze attempt that fails before verify reports its own reason, not the last attempt's", async () => {
  const { env } = await makeEnv({
    wf: "wf_0001",
    manifest: { reasons: { analyze: "target-mismatch: seg_02 raised snowpark to sql" } },
    agentErrors: { analyzer: ["denied"] },
  });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.equal(m.reasons?.analyze, "denied", "the stale target-mismatch must not outlive its attempt");
});

// Round 2, R2 (scoped re-review): the clear above fixes the CROSS-RUN case, but `runAgent` retries
// a missing-output once inside a single stage call — and a failed verify IS a missing-output. So
// attempt 1 failing verify with `target-missing: seg_01`, then attempt 2 failing before verify
// (denied / context-overflow), still parked with attempt 1's reason. A park reason describes the
// LAST attempt: runAgent clears it before each one.
test("an analyze attempt that fails verify, then one that fails before it, parks with the LAST reason", async () => {
  const { env } = await makeEnv({
    wf: "wf_0001",
    scenario: "target-missing:seg_01",   // attempt 1: checkTargets fails -> reasons.analyze set, retried
    agentErrors: { analyzer: [null, "denied"] },   // null = let attempt 1 run for real
  });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.equal(m.reasons?.analyze, "denied", "attempt 2's reason, not attempt 1's target-missing");
});

test("a contract with no target at all parks analyze with target-missing", async () => {
  const { env } = await makeEnv({ wf: "wf_0001", scenario: "target-missing:seg_01" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.equal(m.reasons?.analyze, "target-missing: seg_01");
});

test("a lowering to snowpark makes a dbt-preferring workflow fall back to procedures, and says so", async () => {
  const { env, calls } = await makeEnv({
    wf: "wf_0001",
    twoWaves: true,
    scenario: "target-lower:seg_02",
    manifest: { output_target: "dbt" },
  });
  const m = await migrateWorkflow(env, "wf_0001", { stopAfter: "analyze" });
  assert.equal(m.status.analyze, "DONE");
  assert.equal(m.output_kind, "procedures", "targets.json proposed dbt, but a snowpark contract refuses it");
  assert.ok(calls.logs.some((line) => /dbt/.test(line) && /procedures/.test(line)), "the refusal is logged");
});

test("a snowpark segment renders, compiles with --target snowpark and is validated by validate_snowpark.py", async () => {
  const { env, calls, files } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "snowpark:seg_02" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.equal(m.segment_status?.seg_02, "PASS");

  assert.equal(await files.exists("workflows", "wf_0001", "segments", "seg_02", "proc.py"), true);
  assert.match(
    (await files.read("workflows", "wf_0001", "segments", "seg_02", "proc.sql")) ?? "",
    /LANGUAGE PYTHON/,
    "proc.sql for a snowpark segment can only have come from render_snowpark.py",
  );

  const scripts = calls.py.filter((c) => c.args[1] === "seg_02").map((c) => [c.script, c.args] as const);
  assert.deepEqual(scripts, [
    ["scripts/render_snowpark.py", ["wf_0001", "seg_02"]],
    ["scripts/compile_check.py", ["wf_0001", "seg_02", "--target", "snowpark"]],
    ["scripts/validate_snowpark.py", ["wf_0001", "seg_02"]],
  ]);
  assert.match(
    calls.tasks.find((t) => t.role === "validator" && t.segment === "seg_02")!.task,
    /scripts\/validate_snowpark\.py/,
  );
  // seg_01 is still a SQL segment in the same workflow, on the untouched path.
  assert.deepEqual(
    calls.py.filter((c) => c.args[1] === "seg_01").map((c) => [c.script, c.args] as const),
    [
      ["scripts/compile_check.py", ["wf_0001", "seg_01"]],
      ["scripts/validate_segment.py", ["wf_0001", "seg_01"]],
    ],
  );
});

test("the fixer's task for a snowpark segment names proc.py and forbids editing proc.sql by hand", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "render-fails:seg_02" });
  await migrateWorkflow(env, "wf_0001", {});
  const fixer = calls.tasks.find((t) => t.role === "fixer" && t.segment === "seg_02");
  assert.ok(fixer, "the fixer ran for the snowpark segment");
  assert.match(fixer!.task, /proc\.py/);
  assert.match(fixer!.task, /never edit .*proc\.sql/i);
});

test("render_snowpark.py exiting 2 is a script error for that segment, not a domain verdict", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "render-crashes:seg_02" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(m.reasons?.translate ?? "", /seg_02: script-error/);
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 0, "exit 2 stops the loop at once");
  assert.ok(!calls.py.some((c) => c.script === "scripts/compile_check.py" && c.args[1] === "seg_02"));
});

test("render_snowpark.py exiting 1 ends the iteration like a compile failure", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "render-fails:seg_02" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(m.reasons?.translate ?? "", /seg_02: render/);
  assert.equal(calls.py.filter((c) => c.script === "scripts/render_snowpark.py").length, 3, "one per iteration");
  assert.ok(
    !calls.tasks.some((t) => t.segment === "seg_02" && (t.role === "reviewer" || t.role === "validator")),
    "review and validation never start for a segment whose procedure could not be rendered",
  );
  assert.ok(!calls.py.some((c) => c.script === "scripts/compile_check.py" && c.args[1] === "seg_02"));
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 2, "two fixer attempts, then the loop is spent");
});

// Task W1 updated this pin deliberately: the one addition is the chain check, one
// `validate_workflow.py <wf>` call after every segment PASSed; every per-segment call is unchanged.
// Task L8 adds one call: translation_scaffold.py, once per segment, right before its translator.
test("a SQL workflow's script calls are exactly what they were, plus target_check.py, the two prompt_context.py calls, plan_batches.py, check_seams.py, contract_scaffold.py (pre-fill, re-apply), contract_check.py, translation_scaffold.py and the one validate_workflow.py call", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  await migrateWorkflow(env, "wf_0001", {});
  assert.deepEqual(
    calls.py.map((c) => [c.script, c.args] as const),
    [
      ["scripts/parse.py", ["wf_0001", "--check"]],
      ["scripts/intake_touchpoints.py", ["wf_0001"]],
      ["scripts/intake_prompt.py", ["wf_0001", "--no-interactive"]],
      ["scripts/prompt_context.py", ["wf_0001", "--role", "intake", "--budget-chars", "16000"]],
      ["scripts/segment.py", ["wf_0001"]],
      ["scripts/target_check.py", ["wf_0001", "--prefer", "auto"]],
      ["scripts/plan_batches.py", ["wf_0001", "--budget-chars", "60000"]],
      ["scripts/contract_scaffold.py", ["wf_0001", "--prefill"]],
      ["scripts/prompt_context.py", ["wf_0001", "--role", "analyzer", "--budget-chars", "16000"]],
      ["scripts/contract_scaffold.py", ["wf_0001", "--apply"]],
      ["scripts/check_seams.py", ["wf_0001"]],
      ["scripts/contract_check.py", ["wf_0001"]],
      ["scripts/dev/alteryx_sim.py", ["wf_0001", "--set", "all"]],
      ["scripts/translation_scaffold.py", ["wf_0001", "--segment", "seg_01"]],
      ["scripts/compile_check.py", ["wf_0001", "seg_01"]],
      ["scripts/validate_segment.py", ["wf_0001", "seg_01"]],
      ["scripts/validate_workflow.py", ["wf_0001"]],
    ],
    "no --target argument, no renderer and no validate_snowpark.py on the SQL path",
  );
  const validator = calls.tasks.find((t) => t.role === "validator")!;
  assert.doesNotMatch(validator.task, /snowpark/i);
  assert.match(validator.task, /against every golden set and write validation\.json/);
});

// --- output targets, phase 2, Task F: inline prompt context ------------------------------------
// design §8: the intake and analyzer agents no longer have to find `parsed/dag.json` and
// `intake/touchpoints.json` tool call by tool call -- `scripts/prompt_context.py` renders a
// compact, budgeted summary once, and the orchestrator appends it to that role's task text.

test("intake and the analyzer get their inputs inline", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  await migrateWorkflow(env, "wf_0001", {});

  const intakeCall = calls.py.find((c) => c.script === "scripts/prompt_context.py" && c.args[2] === "intake");
  assert.ok(intakeCall, "prompt_context.py ran for intake");
  assert.deepEqual(intakeCall!.args, ["wf_0001", "--role", "intake", "--budget-chars", "16000"]);
  assert.ok(
    calls.order.indexOf("py:scripts/prompt_context.py") < calls.order.indexOf("agent:intake"),
    "the intake context is rendered before the intake agent runs",
  );
  const intakeTask = calls.tasks.find((t) => t.role === "intake")!.task;
  assert.ok(intakeTask.endsWith("## Inline context for intake (fake)\n- tool 1 input"));

  const analyzerCall = calls.py.find((c) => c.script === "scripts/prompt_context.py" && c.args[2] === "analyzer");
  assert.ok(analyzerCall, "prompt_context.py ran for analyzer");
  assert.deepEqual(analyzerCall!.args, ["wf_0001", "--role", "analyzer", "--budget-chars", "16000"]);
  assert.ok(
    calls.order.indexOf("py:scripts/target_check.py") < calls.order.lastIndexOf("py:scripts/prompt_context.py"),
    "the analyzer's context is rendered after target_check.py",
  );
  const analyzerTask = calls.tasks.find((t) => t.role === "analyzer")!.task;
  assert.ok(analyzerTask.endsWith("## Inline context for analyzer (fake)\n- tool 1 input"));
});

test("a prompt_context failure is logged and the agent still runs without the block", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "context-fails" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED", "a broken renderer never stops the workflow");
  assert.ok(calls.roles.includes("intake") && calls.roles.includes("analyzer"));

  const intakeTask = calls.tasks.find((t) => t.role === "intake")!.task;
  const analyzerTask = calls.tasks.find((t) => t.role === "analyzer")!.task;
  assert.doesNotMatch(intakeTask, /Inline context/);
  assert.doesNotMatch(analyzerTask, /Inline context/);

  const contextLogs = calls.logs.filter((line) => line.includes("prompt_context.py"));
  assert.equal(contextLogs.length, 2, `one log line per role; logs were ${JSON.stringify(calls.logs)}`);

  // Task W4: the notes sentence is part of the INSTRUCTIONS, not the fenced context block -- it
  // survives even when that block failed to render.
  assert.match(intakeTask, /workflows\/wf_0001\/notes\/intake\.md/);
  assert.match(analyzerTask, /workflows\/wf_0001\/notes\/analyzer\.md/);
});

// --- output targets, phase 2, Task W4: a compaction memory aid ---------------------------------
// intake, analyzer (single-call and batched) and fixer are told to keep their own notes file; the
// sentence is fixed and names only the path, so it holds whatever the workflow's own data says.

test("the intake, analyzer and fixer tasks name their notes file", async () => {
  const { calls, env } = await makeEnv({ wf: "wf_0001", scenario: "fix-loop:seg_01" });
  await migrateWorkflow(env, "wf_0001", {});
  const intakeTask = calls.tasks.find((t) => t.role === "intake")!.task;
  const analyzerTask = calls.tasks.find((t) => t.role === "analyzer")!.task;
  const fixerTask = calls.tasks.find((t) => t.role === "fixer")!.task;
  const durable = "the durable record stays in the contract and the files you write";
  // Task N1: the fixed sentence telling the agent the directory already exists and not to create
  // one -- live evidence (docs/live-smoke-test.md "Third live test") showed every intake session
  // trying to create workflows/<wf>/notes/ itself before writing its notes file.
  const exists = "The directory already exists; write the file with your file-writing tool; do not create directories.";
  assert.match(intakeTask, /workflows\/wf_0001\/notes\/intake\.md/);
  assert.match(intakeTask, new RegExp(durable));
  assert.ok(intakeTask.includes(exists), intakeTask);
  assert.match(analyzerTask, /workflows\/wf_0001\/notes\/analyzer\.md/);
  assert.match(analyzerTask, new RegExp(durable));
  assert.ok(analyzerTask.includes(exists), analyzerTask);
  assert.match(fixerTask, /workflows\/wf_0001\/notes\/fixer\.md/);
  assert.match(fixerTask, new RegExp(durable));
  assert.ok(fixerTask.includes(exists), fixerTask);
  // Part of the INSTRUCTIONS, before any fenced inline context (Task F: the data fence never
  // carries an instruction) -- intake and analyzer both get one appended after it.
  assert.ok(intakeTask.indexOf(exists) < intakeTask.indexOf("## Inline context"), intakeTask);
  assert.ok(analyzerTask.indexOf(exists) < analyzerTask.indexOf("## Inline context"), analyzerTask);
});

test("a batched analyzer names its notes file, and the directory-exists sentence, in every batch", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "batched" });
  await migrateWorkflow(env, "wf_0001", {});
  const analyzerTasks = calls.tasks.filter((t) => t.role === "analyzer").map((t) => t.task);
  assert.ok(analyzerTasks.length >= 2, "the workflow was analysed in more than one batch");
  const exists = "The directory already exists; write the file with your file-writing tool; do not create directories.";
  for (const task of analyzerTasks) {
    assert.match(task, /workflows\/wf_0001\/notes\/analyzer\.md/);
    assert.ok(task.includes(exists), task);
    assert.ok(task.indexOf(exists) < task.indexOf("## Inline context"), task);
  }
});

// M2 (fix round 1): `targets.json` says nothing about a segment, so there is no proposal to hold
// the contract to. That is the same failure as a contract with no target — nothing was verified —
// and it uses the same one of the spec's two reason formats; the detail goes to the log.
test("a segment targets.json does not propose at all parks analyze with target-missing", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "no-proposal:seg_02" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.equal(m.reasons?.analyze, "target-missing: seg_02");
  assert.ok(
    calls.logs.some((line) => line.includes("seg_02") && line.includes("targets.json")),
    `the detail is in the log, not in the reason; logs were ${JSON.stringify(calls.logs)}`,
  );
});

// I1 (fix round 1): `manual` is the bottom of the ladder — a segment no generator should attempt.
// The tier-T3 gate normally stops the workflow long before translate, but nothing forced the two
// to agree, so this is the guard at the point of harm.
test("a contract lowered to manual never reaches the translator", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "manual:seg_02" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "DONE", "lowering to manual is a legal lowering, not a mismatch");
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.segment_status?.seg_02, "NEEDS_HUMAN");
  assert.match(m.reasons?.translate ?? "", /seg_02: manual-segment/);
  assert.equal(m.segment_status?.seg_01, "PASS", "the SQL segment in wave 1 still migrated");
  assert.ok(!calls.tasks.some((t) => t.segment === "seg_02"), "no agent was dispatched for the manual segment");
  assert.ok(!calls.py.some((c) => c.args[1] === "seg_02"), "and no script ran for it either");
});

// I2 (fix round 1): a render or compile failure happens BEFORE review, so validation.json and
// review.json — the two files the fixer's standing task points at — do not exist yet. The fixer
// has to be told which script failed and what it said, or it is repairing blind.
test("after a render failure the fixer is told which script failed and what it said", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "render-fails:seg_02" });
  await migrateWorkflow(env, "wf_0001", {});
  const fixer = calls.tasks.find((t) => t.role === "fixer" && t.segment === "seg_02");
  assert.ok(fixer, "the fixer ran");
  assert.match(fixer!.task, /failed before review/);
  assert.match(fixer!.task, /scripts\/render_snowpark\.py/);
  assert.match(fixer!.task, /must not contain/, "the renderer's own stderr is quoted");
});

test("after a compile failure the fixer is pointed at compile_check.json and told what it said", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "compile-fails:seg_01" });
  await migrateWorkflow(env, "wf_0001", {});
  const fixer = calls.tasks.find((t) => t.role === "fixer" && t.segment === "seg_01");
  assert.ok(fixer, "the fixer ran");
  assert.match(fixer!.task, /failed before review/);
  assert.match(fixer!.task, /scripts\/compile_check\.py/);
  assert.match(fixer!.task, /compile_check\.json/);
  assert.match(fixer!.task, /compile error near line 6/);
});

test("a diagnosis quoted to the fixer is redacted and bounded exactly like an audit line", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  const py = env.py;
  env.py = async (script, args, opts) =>
    script.endsWith("compile_check.py")
      ? { ok: false, code: 1, out: "", err: `compile failed: api_key=sk-live-123456 ${"x".repeat(2000)}` }
      : py(script, args, opts);
  await migrateWorkflow(env, "wf_0001", {});
  const fixer = calls.tasks.find((t) => t.role === "fixer" && t.segment === "seg_01");
  assert.ok(fixer, "the fixer ran");
  assert.doesNotMatch(fixer!.task, /sk-live-123456/, "a secret in a script's stderr must not reach a prompt");
  assert.match(fixer!.task, /<redacted>/);
  // Task L1 (R4): the fixed session-rules paragraph is not part of what this bound measures.
  const bounded = fixer!.task.replace(SESSION_RULES, "");
  assert.ok(bounded.length < 1200, `the quoted diagnosis is bounded, task was ${bounded.length} chars without the session rules`);
});

test("a SQL segment that never fails before review carries no diagnosis sentence", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "fix-loop:seg_01" });
  await migrateWorkflow(env, "wf_0001", {});
  const fixer = calls.tasks.find((t) => t.role === "fixer" && t.segment === "seg_01");
  assert.ok(fixer, "the fixer ran after a validation FAIL");
  assert.doesNotMatch(fixer!.task, /failed before review/, "validation.json and review.json do exist in this case");
  assert.match(fixer!.task, /validation\.json/);
});

test("target_check.py exiting 2 parks analyze before the analyzer ever runs", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  const py = env.py;
  env.py = async (script, args, opts) =>
    script.endsWith("target_check.py")
      ? { ok: false, code: 2, out: "", err: "usage: target_check.py" }
      : py(script, args, opts);
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.equal(m.reasons!.analyze, "script-error");
  assert.ok(!calls.roles.includes("analyzer"));
  assert.ok(calls.logs.some((line) => line.includes("target_check.py exited 2")));
});

// C1 (fix round 1). Spec §3.1: exit 1 means "targets.json written, the workflow has `unknown`
// nodes" — and it is written precisely SO THAT the analyzer can see them and record them in
// analysis.md / unsupported.json. Treating it as a script error would make the one case the exit
// code exists for unreachable.
test("target_check.py exiting 1 (unknown nodes) writes targets.json and still reaches the analyzer", async () => {
  const { env, calls, files } = await makeEnv({ wf: "wf_0001", scenario: "unknown-nodes" });
  const m = await migrateWorkflow(env, "wf_0001", { stopAfter: "analyze" });
  assert.equal(m.status.analyze, "DONE");
  assert.notEqual(m.reasons?.analyze, "script-error");
  assert.ok(calls.roles.includes("analyzer"), "the analyzer is the one that decides about an unknown node");
  const targets = JSON.parse((await files.read("workflows", "wf_0001", "segments", "targets.json")) ?? "{}");
  assert.deepEqual(targets.nodes, { "7": "unknown" }, "targets.json is complete, unknowns and all");
  assert.ok(
    calls.logs.some((line) => line.includes("target_check: unknown nodes in wf_0001; the analyzer decides")),
    `the exit is logged once; logs were ${JSON.stringify(calls.logs)}`,
  );
});

test("F13: a tool-call budget hit mid-segment records 'budget' against its segment, not a role name", async () => {
  // Simplest reliable trigger: get parse/intake/analyze/golden done for real (so migrateSegment
  // is the very next thing to run), then push the translator's role over budget on disk before
  // translate itself ever executes.
  const { env, root } = await makeEnv({ wf: "wf_0001" });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const onDisk = await readManifest(root, "wf_0001");
  onDisk.metrics.translator = { toolCalls: 401 };
  await writeJson(path.join(root, "workflows", "wf_0001", "manifest.json"), onDisk);

  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(m.reasons?.translate ?? "", /seg_01: budget/);
});

// --- output targets, phase 2 (Task D): a dbt workflow is ONE translate iteration ----------------
// docs/superpowers/specs/2026-09-22-output-targets-design.md §4.3 and §6: `output_kind: dbt` → the
// translator writes the whole project under `workflows/<wf>/dbt/`; `compile_check.py <wf> --target
// dbt` (no segment, DV6); reviewer and validator once per workflow; `validate_dbt.py` writes every
// segment's report and the per-segment statuses are read back from them; no master.sql.

const DBT = { wf: "wf_0001", twoWaves: true, manifest: { output_target: "dbt" as const } };

test("a dbt workflow is translated ONCE for the whole workflow", async () => {
  const { env, calls, files } = await makeEnv({ ...DBT, scenario: "dbt" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.output_kind, "dbt");
  assert.equal(m.status.translate, "VALIDATED");
  assert.deepEqual(calls.roles, ["intake", "analyzer", "translator", "reviewer", "validator", "documenter"]);
  for (const role of ["translator", "reviewer", "validator"] as const) {
    const task = calls.tasks.find((t) => t.role === role)!;
    assert.equal(task.dbt, true, `${role} runs in dbt scope`);
    assert.equal(task.segment, undefined, `${role} is given no segment`);
  }
  const py = calls.py.map((c) => JSON.stringify([c.script, c.args]));
  assert.equal(py.filter((c) => c === JSON.stringify(["scripts/compile_check.py", ["wf_0001", "--target", "dbt"]])).length, 1);
  assert.equal(py.filter((c) => c === JSON.stringify(["scripts/validate_dbt.py", ["wf_0001"]])).length, 1);
  assert.ok(!calls.py.some((c) => c.script === "scripts/render_snowpark.py"));
  assert.ok(!calls.py.some((c) => c.script === "scripts/validate_segment.py" || c.script === "scripts/validate_snowpark.py"));
  assert.ok(!calls.py.some((c) => c.script === "scripts/compile_check.py" && /^seg_/.test(c.args[1] ?? "")), "no per-segment compile check");
  assert.deepEqual(m.segment_status, { seg_01: "PASS", seg_02: "PASS" });
  assert.equal(await files.read("workflows", "wf_0001", "procs", "README.md"), dbtReadme("wf_0001"));
  assert.equal(await files.exists("workflows", "wf_0001", "procs", "master.sql"), false);
});

test("dbtReadme carries the exact deployment command of design §4.3", () => {
  const text = dbtReadme("wf_0007");
  assert.ok(
    text.includes(
      `dbt run --project-dir workflows/wf_0007/dbt --profiles-dir workflows/wf_0007/dbt --target snowflake --vars '{"src_schema": "<SRC>", "tgt_schema": "<TGT>"}'`,
    ),
    text,
  );
  assert.match(text, /dbt-snowflake/);
});

test("a failing dbt project gets a fixer turn that names the failing models", async () => {
  const { env, calls, files } = await makeEnv({ ...DBT, scenario: "fix-loop:dbt" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  const fixers = calls.tasks.filter((t) => t.role === "fixer");
  assert.equal(fixers.length, 1);
  assert.equal(fixers[0].dbt, true);
  assert.match(fixers[0].task, /Failing models: orders_out \(seg_02\)/);
  assert.match(fixers[0].task, /dbt\/review\.json/);
  assert.equal(await files.exists("workflows", "wf_0001", "dbt", "fix_log.md"), true);
  assert.deepEqual(m.segment_status, { seg_01: "PASS", seg_02: "PASS" });
});

test("Task W4: a dbt fixer names its own notes file too", async () => {
  const { env, calls } = await makeEnv({ ...DBT, scenario: "fix-loop:dbt" });
  await migrateWorkflow(env, "wf_0001", {});
  const fixer = calls.tasks.find((t) => t.role === "fixer")!;
  assert.match(fixer.task, /workflows\/wf_0001\/notes\/fixer\.md/);
  // Task N1: the dbt-scope fixer form gets the same fixed directory-exists sentence.
  assert.ok(
    fixer.task.includes("The directory already exists; write the file with your file-writing tool; do not create directories."),
    fixer.task,
  );
});

test("a dbt project that never passes parks translate with a dbt reason", async () => {
  const { env, calls } = await makeEnv({ ...DBT, scenario: "never-fixed:dbt" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "dbt: validation FAIL after 3 iterations");
  // seg_01's own model is right in every iteration; only the segment owning orders_out fails.
  assert.deepEqual(m.segment_status, { seg_01: "PASS", seg_02: "NEEDS_HUMAN" });
  for (const [segment, status] of Object.entries(m.segment_status!)) {
    assert.ok(status === "NEEDS_HUMAN" || status.startsWith("PASS"), `${segment}: ${status}`);
  }
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 2);
  assert.ok(!calls.roles.includes("documenter"));

  const n = calls.roles.length;
  const second = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(second.status.translate, "NEEDS_HUMAN");
  assert.equal(calls.roles.length, n, "a plain re-run of a parked dbt workflow makes no agent call");
});

test("a dbt compile failure is quoted to the fixer", async () => {
  const { env, calls } = await makeEnv({ ...DBT, scenario: "compile-fails:dbt" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  const fixer = calls.tasks.find((t) => t.role === "fixer");
  assert.ok(fixer, "the fixer ran");
  // Brief test 5 read `scripts/compile_check.py --target dbt failed`; fix round 2 puts the workflow id
  // first in every task text (the policy reads the first bare token as the workflow).
  assert.ok(fixer!.task.includes("scripts/compile_check.py wf_0001 --target dbt failed"), fixer!.task);
  assert.match(fixer!.task, /dbt\/compile_check\.json/);
  assert.match(fixer!.task, /dbt:model_config/, "the check's own words are quoted");
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "dbt: compile check failed after 3 iterations");
  assert.ok(!calls.roles.includes("reviewer") && !calls.roles.includes("validator"));
  assert.deepEqual(m.segment_status, { seg_01: "NEEDS_HUMAN", seg_02: "NEEDS_HUMAN" });
});

test("compile_check --target dbt exiting 2 is a script error", async () => {
  const { env, calls } = await makeEnv({ ...DBT, scenario: "compile-crashes:dbt" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "dbt: script-error");
  assert.ok(!calls.roles.includes("reviewer"));
  assert.ok(!calls.roles.includes("fixer"), "exit 2 stops the loop at once");
  assert.ok(calls.logs.some((line) => line.includes("compile_check.py --target dbt exited 2")));
});

test("needs_human from validate_dbt stops after one iteration", async () => {
  const { env, calls } = await makeEnv({ ...DBT, scenario: "needs-human:dbt" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "dbt: needs_human");
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 0);
  assert.equal(calls.roles.filter((r) => r === "validator").length, 1);
  assert.equal(m.segment_status?.seg_02, "NEEDS_HUMAN");
});

test("a dbt workflow whose segments all PASSed is not re-translated on resume", async () => {
  const { env, calls, files, root } = await makeEnv({ ...DBT, scenario: "dbt" });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const onDisk = await readManifest(root, "wf_0001");
  assert.deepEqual([onDisk.status.analyze, onDisk.status.golden, onDisk.output_kind], ["DONE", "DONE", "dbt"]);
  assert.equal(onDisk.status.translate, undefined);
  onDisk.segment_status = { seg_01: "PASS", seg_02: "PASS" };   // the crash window: saved, not yet VALIDATED
  await writeJson(path.join(root, "workflows", "wf_0001", "manifest.json"), onDisk);
  // Task W1: the validate_dbt.py run that PASSed every segment also wrote the workflow's chain
  // report, which translate now requires; the crash window leaves it on disk beside the segments'.
  await writeJson(path.join(root, "workflows", "wf_0001", "validation_workflow.json"), { verdict: "PASS", needs_human: false });
  const before = calls.roles.length;

  const m = await migrateWorkflow(env, "wf_0001", {});
  const after = calls.roles.slice(before);
  assert.ok(!after.some((r) => ["translator", "fixer", "reviewer", "validator"].includes(r)), `ran ${after}`);
  assert.equal(m.status.translate, "VALIDATED");
  assert.equal(await files.read("workflows", "wf_0001", "procs", "README.md"), dbtReadme("wf_0001"));
  assert.ok(!calls.py.some((c) => c.script === "scripts/validate_dbt.py"), "no script ran for it either");
});

test("the documenter is told where a dbt deployment is", async () => {
  const dbt = await makeEnv({ ...DBT, scenario: "dbt" });
  await migrateWorkflow(dbt.env, "wf_0001", {});
  const documenter = dbt.calls.tasks.find((t) => t.role === "documenter")!;
  assert.match(documenter.task, /procs\/README\.md/);
  assert.match(documenter.task, /dbt\/translation_notes\.md/);
  assert.match(documenter.task, /Deployment/);
  assert.doesNotMatch(documenter.task, /master\.sql/);

  const procedures = await makeEnv({ wf: "wf_0001" });
  await migrateWorkflow(procedures.env, "wf_0001", {});
  const task = procedures.calls.tasks.find((t) => t.role === "documenter")!.task;
  assert.match(task, /procs\/master\.sql/);
  assert.match(task, /Deployment/);
  assert.doesNotMatch(task, /procs\/README\.md/);
});

test("dbtModelName mirrors dbt_project.model_name", () => {
  assert.equal(dbtModelName({ kind: "target", logical: "ITEMS_HIST" }), "items_hist");
  assert.equal(dbtModelName({ kind: "work", table: "MIG_WORK.WF0009_SEG_01_OUT" }), "wf0009_seg_01_out");
  assert.equal(dbtModelName({ table: "MIG_WORK.WF0009_SEG_01_OUT_3_J" }), "wf0009_seg_01_out_3_j", "no kind is a work stream, as in Python");
});

// --- behaviour the brief states in prose but does not test ---

test("a dbt workflow removes a stale master.sql and writes procs/README.md in its place", async () => {
  const { env, files } = await makeEnv({ ...DBT, scenario: "dbt" });
  await mkdir(files.path("workflows", "wf_0001", "procs"), { recursive: true });
  await writeFile(files.path("workflows", "wf_0001", "procs", "master.sql"), "-- left by an earlier procedures run\n", "utf8");
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.equal(await files.exists("workflows", "wf_0001", "procs", "master.sql"), false);
  assert.equal(await files.exists("workflows", "wf_0001", "procs", "README.md"), true);
});

test("a reviewer BLOCK on a dbt project ends the iteration before validation", async () => {
  const { env, calls, root } = await makeEnv({ ...DBT, scenario: "dbt" });
  await writeFile(
    path.join(root, "samples", "wf_0001", "canned", "review.json"),
    `${JSON.stringify({ verdict: "BLOCK", findings: [{ rule: "alias", severity: "block" }] })}\n`,
    "utf8",
  );
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "dbt: reviewer BLOCK after 3 iterations");
  assert.equal(calls.roles.filter((r) => r === "reviewer").length, 3);
  assert.ok(!calls.roles.includes("validator"));
});

test("an agent failure in the dbt loop parks translate with dbt: <role> <error>", async () => {
  const { env, calls } = await makeEnv({ ...DBT, scenario: "dbt", agentErrors: { translator: ["denied"] } });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "dbt: translator denied");
  assert.ok(!calls.py.some((c) => c.script === "scripts/compile_check.py"));
  assert.deepEqual(m.segment_status, { seg_01: "NEEDS_HUMAN", seg_02: "NEEDS_HUMAN" });
});

test("a segment recorded NEEDS_HUMAN parks a dbt workflow without any agent", async () => {
  const { env, calls, root } = await makeEnv({ ...DBT, scenario: "dbt" });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const onDisk = await readManifest(root, "wf_0001");
  onDisk.segment_status = { seg_01: "PASS", seg_02: "NEEDS_HUMAN" };
  await writeJson(path.join(root, "workflows", "wf_0001", "manifest.json"), onDisk);
  const before = calls.roles.length;
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "dbt: needs_human (recorded)");
  assert.equal(calls.roles.length, before);
});

test("--from-stage translate reopens a parked dbt workflow and translates the whole project again", async () => {
  const { env, calls } = await makeEnv({ ...DBT, scenario: "needs-human:dbt" });
  await migrateWorkflow(env, "wf_0001", {});
  assert.equal(calls.roles.filter((r) => r === "translator").length, 1);
  const m = await migrateWorkflow(env, "wf_0001", { fromStage: "translate" });
  assert.equal(calls.roles.filter((r) => r === "translator").length, 2);
  assert.equal(calls.tasks.filter((t) => t.role === "translator").every((t) => t.dbt === true), true);
  assert.equal(m.reasons?.translate, "dbt: needs_human");
});

test("a PASS is not kept for a segment once a later iteration changed the project and never re-validated it", async () => {
  // Iteration 0 validates seg_01 PASS / seg_02 FAIL; the fixer's two turns never compile again.
  // The project that is parked is the fixer's, which nothing validated — so no segment of it is PASS.
  const { env } = await makeEnv({ ...DBT, scenario: "never-fixed:dbt" });
  const py = env.py;
  let compiles = 0;
  env.py = async (script, args, opts) => {
    if (script === "scripts/compile_check.py" && ++compiles > 1) return { ok: false, code: 1, out: "", err: "dbt:parse: dbt parse exited 2" };
    return py(script, args, opts);
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.reasons?.translate, "dbt: compile check failed after 3 iterations");
  assert.deepEqual(m.segment_status, { seg_01: "NEEDS_HUMAN", seg_02: "NEEDS_HUMAN" });
});

test("needs_human wins over a PASS verdict for a dbt segment too", async () => {
  const { env, calls } = await makeEnv({ ...DBT, scenario: "needs-human-pass:dbt" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "dbt: needs_human");
  assert.deepEqual(m.segment_status, { seg_01: "PASS", seg_02: "NEEDS_HUMAN" });
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 0);
});

// --- Task D fix round 1 (task-D-fix1.md): G3, M1, M3 --------------------------------------------

test("G3: a procedures workflow removes a stale procs/README.md and writes master.sql in its place", async () => {
  const { env, files } = await makeEnv({ wf: "wf_0001" });
  await mkdir(files.path("workflows", "wf_0001", "procs"), { recursive: true });
  await writeFile(files.path("workflows", "wf_0001", "procs", "README.md"), dbtReadme("wf_0001"), "utf8");
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.equal(await files.exists("workflows", "wf_0001", "procs", "README.md"), false);
  assert.match((await files.read("workflows", "wf_0001", "procs", "master.sql")) ?? "", /WF0001_MASTER/);
});

test("M1: a compile failure clears the failing-model list, so the next fixer is not sent two iterations back", async () => {
  // Iteration 0: validation FAILs orders_out. Iteration 1: the fixer is told so, and its project fails
  // the compile check. Iteration 2: the fixer is told about the compile failure only.
  const { env, calls } = await makeEnv({ ...DBT, scenario: "never-fixed:dbt" });
  const py = env.py;
  let compiles = 0;
  env.py = async (script, args, opts) =>
    script === "scripts/compile_check.py" && ++compiles === 2
      ? { ok: false, code: 1, out: "", err: "dbt:columns: models/schema.yml lists [] for orders_out" }
      : py(script, args, opts);
  await migrateWorkflow(env, "wf_0001", {});
  const fixers = calls.tasks.filter((t) => t.role === "fixer");
  assert.equal(fixers.length, 2);
  assert.match(fixers[0].task, /Failing models: orders_out \(seg_02\)/);
  assert.doesNotMatch(fixers[1].task, /Failing models/);
  assert.match(fixers[1].task, /failed before review/);
});

test("M1: a reviewer BLOCK clears the failing-model list too", async () => {
  const { env, calls, root } = await makeEnv({ ...DBT, scenario: "never-fixed:dbt" });
  const recording = env.runner;
  let reviews = 0;
  env.runner = {
    async run(role, wf, task, ctx) {
      if (role === "reviewer" && ++reviews === 2) {
        await writeFile(path.join(root, "samples", "wf_0001", "canned", "review.json"), '{"verdict": "BLOCK", "findings": []}\n', "utf8");
      }
      return recording.run(role, wf, task, ctx);
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  const fixers = calls.tasks.filter((t) => t.role === "fixer");
  assert.equal(fixers.length, 2);
  assert.match(fixers[0].task, /Failing models: orders_out \(seg_02\)/);
  assert.doesNotMatch(fixers[1].task, /Failing models/, "the BLOCK in iteration 1 is newer than iteration 0's FAIL");
  assert.equal(m.reasons?.translate, "dbt: reviewer BLOCK after 3 iterations");
});

test("M3: dbtModelName refuses an output that has no name, as dbt_project.model_name raises KeyError", () => {
  assert.throws(() => dbtModelName({}), /dbtModelName/);
  assert.throws(() => dbtModelName({ kind: "work" }), /table/);
  assert.throws(() => dbtModelName({ kind: "target" }), /logical/);
  assert.throws(() => dbtModelName({ kind: "target", table: "MIG_WORK.X" }), /logical/, "a target is named by its logical, not its table");
  assert.throws(() => dbtModelName({ kind: "work", logical: "X", table: null }), /table/);
});

// --- Task D fix round 2: every task text puts the workflow id first -------------------------------
// The policy (fix round 1, G2) judges a workflow script's FIRST bare token as its workflow, so a flag
// value written before the id is refused. An agent copies the shapes it is shown; no task may show a
// workflow script with a flag before its id, or with another workflow's id.

test("every task text names a workflow script with the session's id first, never a flag", async () => {
  const scenarios: Parameters<typeof makeEnv>[0][] = [
    { wf: "wf_0001" },
    { wf: "wf_0001", twoWaves: true, scenario: "snowpark:seg_02" },
    { wf: "wf_0001", twoWaves: true, scenario: "render-fails:seg_02" },
    { wf: "wf_0001", scenario: "compile-fails:seg_01" },
    { wf: "wf_0001", scenario: "fix-loop:seg_01" },
    { wf: "wf_0005" },
    { ...DBT, scenario: "fix-loop:dbt" },
    { ...DBT, scenario: "compile-fails:dbt" },
    { ...DBT, scenario: "dbt" },
    { wf: "wf_0001", twoWaves: true, scenario: "batched" },
  ];
  const offenders: string[] = [];
  let mentions = 0;
  for (const options of scenarios) {
    const { env, calls } = await makeEnv(options);
    await migrateWorkflow(env, options.wf, {});
    for (const { role, task } of calls.tasks) {
      for (const match of task.matchAll(/(scripts\/[a-z_]+\.py)(?:\s+(\S+))?/g)) {
        mentions += 1;
        const next = (match[2] ?? "").replace(/[;,.:)]+$/, "");   // prose punctuation after the token
        if (WORKFLOW_ID_SCRIPTS.includes(match[1]) && (next.startsWith("-") || (/^wf_/i.test(next) && next.toLowerCase() !== options.wf))) {
          offenders.push(`${role}: ${task.slice(Math.max(0, match.index - 20), match.index + 60)}`);
        }
      }
    }
  }
  assert.ok(mentions > 5, `the scan saw ${mentions} script mentions`);
  assert.deepEqual(offenders, []);
});

test("a procedures workflow never gets dbt scope or a dbt script", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.output_kind, "procedures");
  assert.ok(calls.tasks.every((t) => t.dbt === undefined), "no agent is handed a dbt flag");
  assert.ok(!calls.py.some((c) => c.args.includes("dbt") || c.script === "scripts/validate_dbt.py"));
});

// --- output targets, phase 2 (Task W1): the chain test gates VALIDATED --------------------------
// docs/reference/large-workflows.md "The chain test": after every segment PASSed on its own
// (fed golden intermediates), `validate_workflow.py <wf>` runs the stitched whole once; ruling R-W1
// routes its verdict — a `boundary` divergence gets ONE fixer round on that segment, a `chain_drift`
// parks for a human, and only a PASS lets master.sql be written and translate reach VALIDATED.

const TWO = { wf: "wf_0001", twoWaves: true };

test("the chain runs once after every segment PASSed and before master.sql", async () => {
  const { env, calls, files } = await makeEnv(TWO);
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  const chain = calls.py.filter((c) => c.script === "scripts/validate_workflow.py");
  assert.deepEqual(chain.map((c) => c.args), [["wf_0001"]], "exactly one chain call, the workflow id first");
  assert.ok(
    calls.order.lastIndexOf("agent:validator") < calls.order.indexOf("py:scripts/validate_workflow.py"),
    `the chain runs after the last segment's validator: ${calls.order.join(", ")}`,
  );
  assert.ok(
    calls.order.indexOf("py:scripts/validate_workflow.py") < calls.order.indexOf("agent:documenter"),
    "and before the document stage",
  );
  assert.match((await files.read("workflows", "wf_0001", "procs", "master.sql")) ?? "", /CALL MIG_WORK\.WF0001_SEG_02\(/);
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 0);
});

test("a boundary divergence gets one fixer round on that segment", async () => {
  const { env, calls } = await makeEnv({ ...TWO, scenario: "chain-boundary:seg_02" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  const fixers = calls.tasks.filter((t) => t.role === "fixer");
  assert.equal(fixers.length, 1, "exactly one extra fixer call");
  assert.equal(fixers[0].segment, "seg_02");
  assert.ok(fixers[0].task.includes("seg_02/2_T"), fixers[0].task);
  assert.ok(fixers[0].task.includes("workflows/wf_0001/validation_workflow.json"), fixers[0].task);
  assert.equal(calls.py.filter((c) => c.script === "scripts/validate_workflow.py").length, 2, "the chain ran again");
  // seg_02 went through its own compile / review / validate again; seg_01 did not
  const compiles = calls.py.filter((c) => c.script === "scripts/compile_check.py").map((c) => c.args[1]);
  assert.deepEqual(compiles, ["seg_01", "seg_02", "seg_02"]);
  const validators = calls.tasks.filter((t) => t.role === "validator").map((t) => t.segment);
  assert.deepEqual(validators, ["seg_01", "seg_02", "seg_02"]);
  const reviewers = calls.tasks.filter((t) => t.role === "reviewer").map((t) => t.segment);
  assert.deepEqual(reviewers, ["seg_01", "seg_02", "seg_02"]);
  // the round happened after the first chain call and before the second
  const chainAt = calls.order.flatMap((entry, i) => (entry === "py:scripts/validate_workflow.py" ? [i] : []));
  const fixerAt = calls.order.indexOf("agent:fixer");
  assert.ok(chainAt[0] < fixerAt && fixerAt < chainAt[1], calls.order.join(", "));
  assert.deepEqual(m.segment_status, { seg_01: "PASS", seg_02: "PASS" });
  assert.equal(m.reasons?.translate, undefined);
});

test("a boundary that survives the round parks translate", async () => {
  const { env, calls, files } = await makeEnv({ ...TWO, scenario: "chain-boundary-stuck:seg_02" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  // fix round 1, M4: the reason names where the chain still diverges AND which segment the round fixed
  assert.equal(m.reasons?.translate, "chain: seg_02 2_T after 1 fixer round on seg_02");
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 1, "one extra fixer call only");
  assert.equal(calls.py.filter((c) => c.script === "scripts/validate_workflow.py").length, 2);
  assert.equal(await files.exists("workflows", "wf_0001", "procs", "master.sql"), false);
  assert.ok(!calls.roles.includes("documenter"));

  const n = calls.py.length;
  const again = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(again.status.translate, "NEEDS_HUMAN");
  assert.equal(calls.py.length, n, "a plain re-run of a parked chain runs nothing");
});

test("a chain drift parks translate with chain-drift and runs no fixer", async () => {
  const { env, calls, files } = await makeEnv({ ...TWO, scenario: "chain-drift" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "chain-drift: ORDERS_OUT");
  const chainAt = calls.order.indexOf("py:scripts/validate_workflow.py");
  assert.ok(chainAt >= 0);
  assert.ok(!calls.order.slice(chainAt).includes("agent:fixer"), "no fixer after the chain check");
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 0);
  assert.equal(calls.py.filter((c) => c.script === "scripts/validate_workflow.py").length, 1);
  assert.equal(await files.exists("workflows", "wf_0001", "procs", "master.sql"), false);
  // the segments themselves still PASSed; it is the stitched whole a human has to look at
  assert.deepEqual(m.segment_status, { seg_01: "PASS", seg_02: "PASS" });
});

test("a chain script error parks with chain: script-error", async () => {
  const { env, calls, files } = await makeEnv({ ...TWO, scenario: "chain-crash" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "chain: script-error");
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 0);
  assert.equal(await files.exists("workflows", "wf_0001", "procs", "master.sql"), false);
  assert.ok(calls.logs.some((line) => line.includes("validate_workflow.py exited 2")), calls.logs.join("\n"));
});

test("a resumed translate whose segments all PASSed still runs the chain before VALIDATED", async () => {
  const { env, calls, root } = await makeEnv({ ...TWO, scenario: "chain-drift" });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const onDisk = await readManifest(root, "wf_0001");
  onDisk.segment_status = { seg_01: "PASS", seg_02: "PASS" };   // saved, then interrupted before the chain
  await writeJson(path.join(root, "workflows", "wf_0001", "manifest.json"), onDisk);
  const before = calls.roles.length;

  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.ok(!calls.roles.slice(before).some((r) => ["translator", "fixer", "reviewer", "validator"].includes(r)));
  assert.equal(calls.py.filter((c) => c.script === "scripts/validate_workflow.py").length, 1);
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "chain-drift: ORDERS_OUT");
});

test("a dbt workflow does not run validate_workflow.py; translate needs its chain report", async () => {
  const passing = await makeEnv({ ...DBT, scenario: "dbt" });
  const m = await migrateWorkflow(passing.env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.ok(!passing.calls.py.some((c) => c.script === "scripts/validate_workflow.py"), "no extra run for dbt");
  const report = await readJsonOr<{ verdict?: string }>(passing.files.path("workflows", "wf_0001", "validation_workflow.json"), {});
  assert.equal(report.verdict, "PASS", "validate_dbt.py wrote the chain report from its own run");

  const failing = await makeEnv({ ...DBT, scenario: "chain-fail:dbt" });
  const parked = await migrateWorkflow(failing.env, "wf_0001", {});
  assert.equal(parked.status.translate, "NEEDS_HUMAN");
  assert.equal(parked.reasons?.translate, "dbt: chain FAIL");
  assert.ok(!failing.calls.py.some((c) => c.script === "scripts/validate_workflow.py"));
  assert.equal(failing.calls.roles.filter((r) => r === "fixer").length, 0, "a chain FAIL is not a fixer task for dbt");
  assert.equal(await failing.files.exists("workflows", "wf_0001", "procs", "README.md"), false);
  assert.deepEqual(parked.segment_status, { seg_01: "PASS", seg_02: "PASS" });
});

// Final fix wave N-dbt: the M6 rule chainCheck follows — needs_human wins over a PASS chain verdict.
test("a dbt chain report that PASSes but says needs_human parks translate", async () => {
  const { env, calls, files } = await makeEnv({ ...DBT, scenario: "chain-needs-human:dbt" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  const report = await readJsonOr<{ verdict?: string; needs_human?: boolean }>(
    files.path("workflows", "wf_0001", "validation_workflow.json"), {});
  assert.equal(report.verdict, "PASS");
  assert.equal(report.needs_human, true);
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "dbt: chain needs_human");
  assert.equal(await files.exists("workflows", "wf_0001", "procs", "README.md"), false);
  assert.ok(!calls.roles.includes("documenter"));
  assert.deepEqual(m.segment_status, { seg_01: "PASS", seg_02: "PASS" });
});

test("a resumed dbt workflow with no chain report on disk parks with dbt: chain FAIL", async () => {
  const { env, calls, root } = await makeEnv({ ...DBT, scenario: "dbt" });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const onDisk = await readManifest(root, "wf_0001");
  onDisk.segment_status = { seg_01: "PASS", seg_02: "PASS" };
  await writeJson(path.join(root, "workflows", "wf_0001", "manifest.json"), onDisk);

  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "dbt: chain FAIL");
  assert.ok(!calls.py.some((c) => c.script === "scripts/validate_dbt.py" || c.script === "scripts/validate_workflow.py"));
});

// fix round 1, M3: the segment's own validation.json PASSes by construction when the chain fails at
// its boundary, so the chain round's task must not send the fixer to "change only what" a passing
// report's diagnosis points at.
test("a chain-triggered fixer round points at validation_workflow.json's first_divergence and expects the segment's own PASS", async () => {
  const { env, calls } = await makeEnv({ ...TWO, scenario: "chain-boundary:seg_02" });
  await migrateWorkflow(env, "wf_0001", {});
  const fixer = calls.tasks.find((t) => t.role === "fixer")!;
  assert.match(fixer.task, /^Repair segment seg_02 of wf_0001: /);
  assert.match(fixer.task, /first_divergence/);
  assert.match(fixer.task, /workflows\/wf_0001\/validation_workflow\.json/);
  assert.match(fixer.task, /seg_02\/2_T on golden set normal/);
  assert.match(fixer.task, /own validation\.json PASSes/);
  assert.doesNotMatch(fixer.task, /change only what their diagnosis points at/);
  assert.doesNotMatch(fixer.task, /--root/);
  // the ordinary fixer task is untouched but for Task W4's trailing notes sentence (Task N1 adds
  // one more fixed sentence after it, and Task L1 the fixed session rules after that, so the notes
  // sentences end the instructions, not the task).
  const plain = await makeEnv({ wf: "wf_0001", scenario: "fix-loop:seg_01" });
  await migrateWorkflow(plain.env, "wf_0001", {});
  const plainFixer = plain.calls.tasks.find((t) => t.role === "fixer")!.task;
  assert.match(plainFixer, /change only what their diagnosis points at\. Keep your decisions/);
  assert.ok(
    plainFixer.endsWith(
      "workflows/wf_0001/notes/fixer.md as you go; the durable record stays in the contract and the files you write. " +
        "The directory already exists; write the file with your file-writing tool; do not create directories.\n\n" +
        SESSION_RULES,
    ),
    plainFixer,
  );
});

// fix round 1, I1: a crash inside the chain's fixer round must not let the rewritten segment reach
// VALIDATED on resume without its own gates. Before the round the segment's PASS is removed and saved.
for (const crashAt of ["compile_check", "reviewer", "validator"] as const) {
  test(`a crash at seg_02's ${crashAt} inside the chain's fixer round re-runs seg_02's full gates on resume`, async () => {
    const { env, calls, root } = await makeEnv({ ...TWO, scenario: "chain-boundary:seg_02" });
    const realPy = env.py;
    const realRunner = env.runner;
    let chainSeen = false;
    let crashed = false;
    env.py = async (script, args, opts) => {
      if (script === "scripts/validate_workflow.py") chainSeen = true;
      if (crashAt === "compile_check" && chainSeen && !crashed && script === "scripts/compile_check.py" && args[1] === "seg_02") {
        crashed = true;
        throw new Error("simulated crash after the fixer rewrote proc.sql");
      }
      return realPy(script, args, opts);
    };
    env.runner = {
      run: async (role, wf, task, ctx) => {
        if (chainSeen && !crashed && role === crashAt && ctx?.segment === "seg_02") {
          crashed = true;
          throw new Error(`simulated crash at ${crashAt}`);
        }
        return realRunner.run(role, wf, task, ctx);
      },
    };
    await assert.rejects(migrateWorkflow(env, "wf_0001", {}));
    assert.ok(crashed, "the crash happened inside the fixer round");
    const onDisk = await readManifest(root, "wf_0001");
    assert.equal(onDisk.status.translate, undefined);
    assert.ok(!String(onDisk.segment_status?.seg_02 ?? "").startsWith("PASS"), `seg_02 on disk: ${onDisk.segment_status?.seg_02}`);
    assert.equal(onDisk.segment_status?.seg_01, "PASS");

    env.py = realPy;
    env.runner = realRunner;
    const tasksBefore = calls.tasks.length;
    const pyBefore = calls.py.length;
    const m = await migrateWorkflow(env, "wf_0001", {});
    const agents = calls.tasks.slice(tasksBefore).map((t) => `${t.role}:${t.segment ?? "-"}`);
    const scripts = calls.py.slice(pyBefore).map((c) => `${c.script.split("/").pop()} ${c.args.join(" ")}`);
    // seg_02 went through its whole sequence again, seg_01 did not, and the chain ran after it
    assert.deepEqual(agents.filter((a) => a.endsWith(":seg_02")).map((a) => a.split(":")[0]), ["translator", "reviewer", "validator"]);
    assert.ok(!agents.some((a) => a.endsWith(":seg_01")), agents.join(", "));
    assert.deepEqual(scripts.filter((s) => /^(compile_check|validate_segment|validate_workflow)\.py/.test(s)), [
      "compile_check.py wf_0001 seg_02",
      "validate_segment.py wf_0001 seg_02",
      "validate_workflow.py wf_0001",
    ]);
    assert.equal(m.status.translate, "VALIDATED");
    assert.deepEqual(m.segment_status, { seg_01: "PASS", seg_02: "PASS" });
  });
}

// fix round 1, M1: a report on disk belongs to THIS chain call — never an older one.
test("an exit 1 that left no chain report parks with chain: script-error, never acting on an older report", async () => {
  const { env, calls, root } = await makeEnv(TWO);
  await writeJson(path.join(root, "workflows", "wf_0001", "validation_workflow.json"), {
    verdict: "FAIL", divergence_kind: "boundary", needs_human: false,
    first_divergence: { segment: "seg_01", stream: "OLD_STREAM", output: "MIG_WORK.OLD", set: "normal" },
  });
  const realPy = env.py;
  env.py = async (script, args, opts) =>
    script === "scripts/validate_workflow.py"
      ? { ok: false, code: 1, out: "", err: "ModuleNotFoundError: No module named 'lib.handoff'" }
      : realPy(script, args, opts);
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "chain: script-error");
  assert.equal(calls.roles.filter((r) => r === "fixer").length, 0, "no fixer is sent after a stale report");
  assert.equal(
    await stat(path.join(root, "workflows", "wf_0001", "validation_workflow.json")).then(() => true, () => false),
    false,
    "the stale report was deleted before the chain ran",
  );
});

// fix round 1, M6: needs_human wins over any verdict, as it does per segment.
test("a PASSing chain report that says needs_human parks translate", async () => {
  const { env, files } = await makeEnv({ ...TWO, scenario: "chain-needs-human" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.equal(m.reasons?.translate, "chain: needs_human");
  assert.equal(await files.exists("workflows", "wf_0001", "procs", "master.sql"), false);
});

// --- output targets, phase 2, Task W2: seams checked by code, the analyzer batched above a budget --
// Every inter-segment stream must agree between its producer's contract and its consumer's
// (`scripts/check_seams.py`, in the analyzer's verify callback). A workflow whose rendered context
// is over `analyzerBudgetChars` is analysed batch by batch (`scripts/plan_batches.py`), each call in a
// policy-narrowed lane, and the fragments are stitched by `scripts/stitch_analysis.py` -- never by an
// agent. Every committed sample stays ONE analyzer call.

/** `py:<script> <args…>` and `agent:<role>` interleaved in the order they happened. */
function sequence(calls: Awaited<ReturnType<typeof makeEnv>>["calls"]): string[] {
  let next = 0;
  return calls.order.map((entry) => (entry.startsWith("py:") ? `${entry} ${calls.py[next++].args.join(" ")}`.trim() : entry));
}

test("a small workflow keeps one analyzer call", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true });
  const m = await migrateWorkflow(env, "wf_0001", { stopAfter: "analyze" });
  assert.equal(m.status.analyze, "DONE");

  const analyzer = calls.tasks.filter((t) => t.role === "analyzer");
  assert.equal(analyzer.length, 1, "one analyzer call");
  assert.equal(analyzer[0].batch, undefined, "no batch ctx");
  const seq = sequence(calls);
  const at = (entry: string) => seq.indexOf(entry);
  assert.ok(at("py:scripts/target_check.py wf_0001 --prefer auto") < at("py:scripts/plan_batches.py wf_0001 --budget-chars 60000"), seq.join("\n"));
  assert.ok(at("py:scripts/plan_batches.py wf_0001 --budget-chars 60000") < at("agent:analyzer"), "the plan is made before the analyzer runs");
  assert.ok(at("agent:analyzer") < at("py:scripts/check_seams.py wf_0001"), "check_seams.py runs inside the verify callback");
  assert.deepEqual(calls.py.filter((c) => c.script === "scripts/check_seams.py").map((c) => c.args), [["wf_0001"]]);
  assert.ok(!calls.py.some((c) => c.script === "scripts/stitch_analysis.py"), "nothing to stitch");
  assert.ok(!calls.py.some((c) => c.args.includes("--batch")), "no batch context");
});

test("a workflow over the budget is analysed batch by batch and stitched", async () => {
  const { env, calls, files } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "batched", manifest: { tier: "T2" } });
  const m = await migrateWorkflow(env, "wf_0001", { stopAfter: "analyze" });
  assert.equal(m.status.analyze, "DONE");
  assert.equal(m.reasons?.analyze, undefined);

  const analyzer = calls.tasks.filter((t) => t.role === "analyzer");
  assert.deepEqual(analyzer.map((t) => t.batch), [
    { id: "batch_01", segments: ["seg_01"] },
    { id: "batch_02", segments: ["seg_02"] },
  ]);
  const seq = sequence(calls);
  const from = seq.indexOf("py:scripts/plan_batches.py wf_0001 --budget-chars 60000");
  assert.deepEqual(seq.slice(from, seq.indexOf("py:scripts/stitch_analysis.py wf_0001") + 1), [
    "py:scripts/plan_batches.py wf_0001 --budget-chars 60000",
    "py:scripts/contract_scaffold.py wf_0001 --prefill",
    "py:scripts/prompt_context.py wf_0001 --role analyzer --batch batch_01 --budget-chars 60000",
    "agent:analyzer",
    "py:scripts/contract_scaffold.py wf_0001 --apply --segments seg_01",
    "py:scripts/check_seams.py wf_0001 --segments seg_01",
    "py:scripts/contract_check.py wf_0001 --segments seg_01",
    "py:scripts/prompt_context.py wf_0001 --role analyzer --batch batch_02 --budget-chars 60000",
    "agent:analyzer",
    "py:scripts/contract_scaffold.py wf_0001 --apply --segments seg_02",
    "py:scripts/check_seams.py wf_0001 --segments seg_02",
    "py:scripts/contract_check.py wf_0001 --segments seg_02",
    "py:scripts/stitch_analysis.py wf_0001",
  ]);
  assert.ok(!calls.py.some((c) => c.script === "scripts/prompt_context.py" && c.args[2] === "analyzer" && !c.args.includes("--batch")),
    "no whole-workflow analyzer context is rendered for a batched workflow");

  const [first, second] = analyzer.map((t) => t.task);
  assert.match(first, /batch_01/);
  assert.match(first, /seg_01/);
  assert.match(first, /workflows\/wf_0001\/analysis\/batch_01\.md/);
  assert.match(first, /workflows\/wf_0001\/analysis\/batch_01\.unsupported\.json/);
  assert.doesNotMatch(first, /seg_02/, "batch_01's task names only its own segments");
  assert.ok(first.endsWith("## Inline context for analyzer batch_01 (fake)\n- tool 1 input"), "the batch context is appended last");
  assert.ok(second.endsWith("## Inline context for analyzer batch_02 (fake)\n- tool 1 input"));

  assert.match((await files.read("workflows", "wf_0001", "analysis.md")) ?? "", /stitched from 2 batches/);
  assert.equal(m.tier, "T1", "the tier is the stitched unsupported.json's, not whatever the manifest said before");
  assert.equal(m.output_kind, "procedures");
});

test("a seam mismatch parks analyze with seam-mismatch after one analyzer retry", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "seam-mismatch:seg_02" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(calls.roles.filter((r) => r === "analyzer").length, 2);
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.equal(m.reasons?.analyze, "seam-mismatch: seg_01->seg_02 2_T");
  assert.equal(calls.py.filter((c) => c.script === "scripts/check_seams.py").length, 2, "checked after each attempt");
  assert.ok(!calls.roles.includes("translator"), "nothing is translated across a broken seam");
});

test("a stitch failure parks analyze", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "stitch-fails" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.equal(m.reasons?.analyze, "stitch");
  assert.equal(calls.roles.filter((r) => r === "analyzer").length, 2, "both batches ran");
  assert.ok(!calls.roles.includes("translator"));
});

/** `check_seams.py` answering for a seam into `seg_02` whenever `seg_02` is in scope: the fake's
 * own `seam-mismatch:<seg>` behaviour, for a test whose scenario string is already `batched`. */
function seamIntoSeg02(env: Awaited<ReturnType<typeof makeEnv>>["env"], code: 1 | 2): void {
  const py = env.py;
  env.py = async (script, args, opts) => {
    if (!script.endsWith("check_seams.py")) return py(script, args, opts);
    const at = args.indexOf("--segments");
    if (at >= 0 && !args[at + 1].split(",").includes("seg_02")) return py(script, args, opts);
    if (code === 2) return { ok: false, code: 2, out: "", err: "Traceback (most recent call last): check_seams.py crashed" };
    await writeJson(path.join(env.root, "workflows", "wf_0001", "segments", "seams.json"), {
      ok: false,
      seams: [{ producer: "seg_01", consumer: "seg_02", stream: "2_T", table: "MIG_WORK.WF0001_SEG_01_OUT", status: "mismatch", problems: ["x"] }],
      duplicates: [],
    });
    return { ok: false, code: 1, out: "", err: "seam-mismatch: seg_01->seg_02 2_T" };
  };
}

test("in a batch, a seam mismatch parks after that batch's one retry and nothing is stitched", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "batched" });
  seamIntoSeg02(env, 1);
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.equal(m.reasons?.analyze, "seam-mismatch: seg_01->seg_02 2_T");
  assert.deepEqual(calls.tasks.filter((t) => t.role === "analyzer").map((t) => t.batch?.id), ["batch_01", "batch_02", "batch_02"]);
  assert.ok(!calls.py.some((c) => c.script === "scripts/stitch_analysis.py"));
});

test("check_seams.py exiting 2 parks analyze with script-error, in one call or in a batch", async () => {
  const single = await makeEnv({ wf: "wf_0001", twoWaves: true });
  const py = single.env.py;
  single.env.py = async (script, args, opts) =>
    script.endsWith("check_seams.py") ? { ok: false, code: 2, out: "", err: "usage: check_seams.py" } : py(script, args, opts);
  const m = await migrateWorkflow(single.env, "wf_0001", {});
  assert.deepEqual([m.status.analyze, m.reasons?.analyze], ["NEEDS_HUMAN", "script-error"]);

  const batched = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "batched" });
  seamIntoSeg02(batched.env, 2);
  const n = await migrateWorkflow(batched.env, "wf_0001", {});
  assert.deepEqual([n.status.analyze, n.reasons?.analyze], ["NEEDS_HUMAN", "script-error"]);
});

test("a batch that raises a target parks with the same target-mismatch reason as one call", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "batched" });
  // target_check proposes snowpark for seg_02; the canned contract says sql -- a raise
  const py = env.py;
  env.py = async (script, args, opts) => {
    const out = await py(script, args, opts);
    if (script.endsWith("target_check.py")) {
      const file = path.join(env.root, "workflows", "wf_0001", "segments", "targets.json");
      const targets = await readJsonOr<{ segments: Record<string, string> }>(file, { segments: {} });
      targets.segments.seg_02 = "snowpark";
      await writeJson(file, targets);
    }
    return out;
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.equal(m.reasons?.analyze, "target-mismatch: seg_02 raised snowpark to sql");
  assert.deepEqual(calls.tasks.filter((t) => t.role === "analyzer").map((t) => t.batch?.id), ["batch_01", "batch_02", "batch_02"],
    "checked in batch_02's own verify, so batch_02 gets the one retry");
});

test("a batched T3 workflow needs no contracts and ends MANUAL", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0005", twoWaves: true, scenario: "batched" });
  const m = await migrateWorkflow(env, "wf_0005", {});
  assert.deepEqual([m.tier, m.status.analyze, m.status.translate], ["T3", "DONE", "MANUAL"]);
  assert.equal(calls.tasks.filter((t) => t.role === "analyzer").length, 2);
  assert.ok(!calls.py.some((c) => c.script === "scripts/check_seams.py"), "a T3 workflow has no contracts, so no seams");
});

test("a batched analyze starts from an empty analysis/ folder", async () => {
  const { env, files } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "batched" });
  await mkdir(files.path("workflows", "wf_0001", "analysis"), { recursive: true });
  await writeFile(files.path("workflows", "wf_0001", "analysis", "batch_07.md"), "stale\n", "utf8");
  const m = await migrateWorkflow(env, "wf_0001", { stopAfter: "analyze" });
  assert.equal(m.status.analyze, "DONE");
  assert.equal(await files.exists("workflows", "wf_0001", "analysis", "batch_07.md"), false, "a fragment from an earlier plan is gone");
  assert.equal(await files.exists("workflows", "wf_0001", "analysis", "batch_02.md"), true);
});

test("plan_batches.py exiting 2 is a script error before any analyzer runs", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  const py = env.py;
  env.py = async (script, args, opts) =>
    script.endsWith("plan_batches.py") ? { ok: false, code: 2, out: "", err: "usage: plan_batches.py" } : py(script, args, opts);
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.deepEqual([m.status.analyze, m.reasons?.analyze], ["NEEDS_HUMAN", "script-error"]);
  assert.ok(!calls.roles.includes("analyzer"));
});

test("the analyzer's budget comes from the configuration", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", config: { analyzerBudgetChars: 12345 } });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "analyze" });
  assert.deepEqual(calls.py.find((c) => c.script === "scripts/plan_batches.py")!.args, ["wf_0001", "--budget-chars", "12345"]);
});

test("a batch whose fragment carries no tier the stitch can rank gets one retry, then parks", async () => {
  const { env, calls, files } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "batched" });
  await writeFile(files.path("samples", "wf_0001", "canned", "unsupported.json"), '{"tier": "T9"}\n', "utf8");
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.analyze, "NEEDS_HUMAN");
  assert.equal(m.reasons?.analyze, "missing-output");
  assert.deepEqual(calls.tasks.filter((t) => t.role === "analyzer").map((t) => t.batch?.id), ["batch_01", "batch_01"]);
  assert.ok(!calls.py.some((c) => c.script === "scripts/stitch_analysis.py"), "the stitch never sees an unusable fragment");
});

// Follow-up to Task W2 (review Minor): a crash between batches leaves some contracts and fragments
// on disk and analyze unfinished. The resume is deliberately a FULL re-run: analysis/ is emptied and
// every batch runs again from batch_01, so nothing the crashed run wrote is ever stitched or trusted.
test("a crash after batch 1 of 3 resumes with a full batched re-run from batch_01", async () => {
  const { env, calls, files, root } = await makeEnv({ wf: "wf_0001", waves: 3, scenario: "batched" });
  const realRunner = env.runner;
  let crashed = false;
  env.runner = {
    run: async (role, wf, task, ctx) => {
      if (role === "analyzer" && ctx?.batch?.id === "batch_02" && !crashed) {
        crashed = true;
        throw new Error("simulated crash between batch_01 and batch_02");
      }
      return realRunner.run(role, wf, task, ctx);
    },
  };
  await assert.rejects(migrateWorkflow(env, "wf_0001", {}));
  assert.ok(crashed);
  const onDisk = await readManifest(root, "wf_0001");
  assert.equal(onDisk.status.analyze, undefined, "analyze did not finish");
  assert.equal(await files.exists("workflows", "wf_0001", "analysis", "batch_01.md"), true, "batch_01's fragment survived the crash");
  assert.equal(await files.exists("workflows", "wf_0001", "analysis.md"), false, "nothing was stitched");

  env.runner = realRunner;
  const pyBefore = calls.py.length;
  const tasksBefore = calls.tasks.length;
  let staleAtRestart: boolean | undefined;
  env.runner = {
    run: async (role, wf, task, ctx) => {
      if (role === "analyzer" && staleAtRestart === undefined) {
        staleAtRestart = await files.exists("workflows", "wf_0001", "analysis", "batch_01.md");
      }
      return realRunner.run(role, wf, task, ctx);
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});

  assert.equal(staleAtRestart, false, "analysis/ was emptied before batch_01 ran again");
  assert.deepEqual(
    calls.tasks.slice(tasksBefore).filter((t) => t.role === "analyzer").map((t) => t.batch?.id),
    ["batch_01", "batch_02", "batch_03"],
    "every batch runs again, from the first",
  );
  const resumed = calls.py.slice(pyBefore).map((c) => `${c.script.split("/").pop()} ${c.args.join(" ")}`);
  assert.deepEqual(resumed.filter((s) => /^(segment|target_check|plan_batches|prompt_context|check_seams|stitch_analysis)\.py/.test(s)), [
    "segment.py wf_0001",
    "target_check.py wf_0001 --prefer auto",
    "plan_batches.py wf_0001 --budget-chars 60000",
    "prompt_context.py wf_0001 --role analyzer --batch batch_01 --budget-chars 60000",
    "check_seams.py wf_0001 --segments seg_01",
    "prompt_context.py wf_0001 --role analyzer --batch batch_02 --budget-chars 60000",
    "check_seams.py wf_0001 --segments seg_02",
    "prompt_context.py wf_0001 --role analyzer --batch batch_03 --budget-chars 60000",
    "check_seams.py wf_0001 --segments seg_03",
    "stitch_analysis.py wf_0001",
  ]);
  assert.equal(m.status.analyze, "DONE");
  assert.equal(m.reasons?.analyze, undefined);
  assert.equal(m.tier, "T1");
  assert.match((await files.read("workflows", "wf_0001", "analysis.md")) ?? "", /stitched from 3 batches/);
  for (const seg of ["seg_01", "seg_02", "seg_03"]) {
    assert.equal(await files.exists("workflows", "wf_0001", "segments", seg, "contract.json"), true, seg);
  }
  assert.equal(m.status.translate, "VALIDATED", "the resumed workflow runs to the end");
  assert.deepEqual(m.segment_status, { seg_01: "PASS", seg_02: "PASS", seg_03: "PASS" });
});

// Task W4 fix round 1: analyzeInBatches's own reloadManifest call, right after each batch, is
// exactly the mid-stage-reload hazard the reviewer flagged -- an earlier batch's session may
// already be saved on disk with a LOWER compactions/peakInputTokens than a later batch's session
// has recorded in memory but not yet saved. MockRunner itself never populates these fields (it
// never opens a real session), so this test plays the part of CopilotRunner's hooks.ts
// recordMetrics/onSessionEnd directly: batch_01's call saves a low snapshot, batch_02's records a
// higher one but does not save it before analyzeInBatches's reloadManifest runs.
test("Task W4 fix round 1: a mid-stage reload between batches keeps the higher compactions/peakInputTokens, not the stale disk copy", async () => {
  const { env } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "batched" });
  const realRunner = env.runner;
  env.runner = {
    run: async (role, wf, task, ctx) => {
      const result = await realRunner.run(role, wf, task, ctx);
      if (role === "analyzer" && ctx?.batch?.id === "batch_01") {
        // What a real CopilotRunner session's onSessionEnd would have saved by the time this call
        // returns (hooks.ts's recordMetrics, then saveManifest).
        wf.metrics.analyzer = { ...(wf.metrics.analyzer ?? {}), compactions: 1, peakInputTokens: 2000 };
        await saveManifest(env.root, wf);
      }
      if (role === "analyzer" && ctx?.batch?.id === "batch_02") {
        // A LATER session recorded more, but nothing has saved it yet -- analyzeInBatches's own
        // reloadManifest, right after this call returns, must not lose it to batch_01's stale save.
        wf.metrics.analyzer = { ...(wf.metrics.analyzer ?? {}), compactions: 3, peakInputTokens: 9000 };
      }
      return result;
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.metrics.analyzer?.compactions, 3, "batch_02's higher count must survive the reload right after it");
  assert.equal(m.metrics.analyzer?.peakInputTokens, 9000, "same for peakInputTokens");
});

// ---------- live hardening, Task L1 (R4): fixed session rules in every agent task ----------
// Live evidence (docs/live-smoke-test.md "Third live test"): nothing in the task text told the model
// how to navigate, so it typed absolute paths (and mangled a long run root), listed files with
// PowerShell and opened a sibling workflow's folder. Every task runAgent sends now carries one fixed
// paragraph -- a constant, no workflow-authored text -- with the instructions, BEFORE any fenced
// inline-context block (Task F: the data fence never carries an instruction). Task L7 (R3) adds a
// fifth rule, on the live evidence that a translator spent its whole session reading this pipeline's
// own scripts/ source trying to debug a validation diff instead of handing off; Task L8 (R3) a sixth,
// on a dbt translator that read every golden CSV of every set and overflowed its context; Task L9
// (R3) a seventh, on a documenter session that parked calling the SDK's own `web_fetch`.

test("L1 R4 / L7 R3 / L8 R3 / L9 R3: the session rules are a constant that says the seven things", () => {
  assert.equal(SESSION_RULE_LINES.length, 7);
  const squeezed = SESSION_RULES.replace(/\s+/g, " ");
  for (const phrase of [
    "relative to the repository root", "workflows/<id>/", "never type an absolute path",
    "glob", "view", "grep", "`python scripts/<name>.py …`",
    "only workflows/<id>/ is yours", "never open another workflow's folder",
    "A refused tool call is final", "do not retry it in another form or through another tool",
    "say so in your notes", "and stop",
    "Do not read this pipeline's own scripts/ or orchestrator/ source to debug a difference",
    "the validation report, the contract, the cookbook and docs/reference/ are the evidence",
    "Never read the golden data in bulk", "at most one golden set's inputs", "the validator compares the rest",
    "There is no network in these sessions", "every page you need is in the repository",
    "`cookbook/`", "`docs/reference/`",
  ]) {
    assert.ok(squeezed.includes(phrase), phrase);
  }
  for (const rule of SESSION_RULE_LINES) assert.ok(SESSION_RULES.includes(rule), rule);
  assert.doesNotMatch(SESSION_RULES, /wf_\d|seg_\d|batch_\d/, "no workflow's own text or id");
});

test("L1 R4: every role's task, in every form, carries the session rules once, before any inline context", async () => {
  const runs: [Awaited<ReturnType<typeof makeEnv>>, string][] = [
    [await makeEnv({ wf: "wf_0001", scenario: "fix-loop:seg_01" }), "wf_0001"],
    [await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "batched" }), "wf_0001"],
    [await makeEnv({ ...DBT, scenario: "fix-loop:dbt" }), "wf_0001"],
    [await makeEnv({ wf: "wf_0005" }), "wf_0005"],
  ];
  const forms = new Set<string>();
  let fenced = 0;
  for (const [{ env, calls }, id] of runs) {
    await migrateWorkflow(env, id, {});
    for (const { role, task, dbt, batch } of calls.tasks) {
      const form = `${role}${dbt ? " (dbt)" : ""}${batch ? " (batch)" : ""}`;
      forms.add(form);
      assert.equal(task.split(SESSION_RULES).length, 2, `${form}: the paragraph exactly once\n${task}`);
      const fence = task.indexOf("## Inline context");
      if (fence >= 0) {
        fenced += 1;
        assert.ok(task.indexOf(SESSION_RULES) < fence, `${form}: the rules come before the inline context\n${task}`);
      }
    }
  }
  for (const form of [
    "parser-recovery", "intake", "analyzer", "analyzer (batch)", "translator", "reviewer", "validator", "fixer",
    "documenter", "translator (dbt)", "reviewer (dbt)", "validator (dbt)", "fixer (dbt)",
  ]) {
    assert.ok(forms.has(form), `${form} was not exercised; saw ${[...forms].join(", ")}`);
  }
  assert.ok(fenced >= 4, `the inline-context forms were exercised (${fenced})`);
});

test("L1 R4: .github/copilot-instructions.md mirrors every session rule", async () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const text = (await readFile(path.join(here, "..", "..", ".github", "copilot-instructions.md"), "utf8")).replace(/\s+/g, " ");
  for (const rule of SESSION_RULE_LINES) assert.ok(text.includes(rule), rule);
});

// ---------- live hardening, Task L4 (R2): a translator's or fixer's own validation is never the verdict ----------
// The translator and the fixer may now run the validator scripts on their own work, which writes the same
// validation*.json the validator's session is judged by ("the report exists"). The orchestrator clears those
// reports before it dispatches the validator, so only the validator's own run can satisfy that check.

const STALE_PASS = { verdict: "PASS", needs_human: false, diff_clusters: [], idempotent: true, sets: { normal: "PASS" } };

test("L4: a translator's own validation.json never stands in for the validator's", async () => {
  const { env, root } = await makeEnv({ wf: "wf_0001" });
  const recording = env.runner;
  const seen: boolean[] = [];
  const segDir = path.join(root, "workflows", "wf_0001", "segments", "seg_01");
  env.runner = {
    async run(role, wf, task, ctx) {
      if (role === "validator") {
        // a validator session that ends "ok" without running anything
        seen.push(await exists(path.join(segDir, "validation.json")) || await exists(path.join(segDir, "validation.normal.json")));
        return { ok: true, toolCalls: 0, ms: 0 };
      }
      const result = await recording.run(role, wf, task, ctx);
      if (role === "translator") {
        // what `python scripts/validate_segment.py wf_0001 seg_01 --set normal` leaves behind
        await writeJson(path.join(segDir, "validation.json"), STALE_PASS);
        await writeJson(path.join(segDir, "validation.normal.json"), STALE_PASS);
      }
      return result;
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.deepEqual(seen, [false, false], "the validator (and its one retry) started with no report on disk");
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(String(m.reasons?.translate), /validator missing-output/);
});

test("L4: with the validator running, a translator's own report is replaced and the segment still passes", async () => {
  const { env, root } = await makeEnv({ wf: "wf_0001" });
  const recording = env.runner;
  const segDir = path.join(root, "workflows", "wf_0001", "segments", "seg_01");
  let atValidator: boolean | undefined;
  env.runner = {
    async run(role, wf, task, ctx) {
      if (role === "validator") atValidator = await exists(path.join(segDir, "validation.normal.json"));
      const result = await recording.run(role, wf, task, ctx);
      if (role === "translator") await writeJson(path.join(segDir, "validation.normal.json"), STALE_PASS);
      return result;
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(atValidator, false);
  assert.equal(m.status.translate, "VALIDATED");
});

test("L4: in dbt scope every segment's report and the chain report are cleared before the validator", async () => {
  const { env, root } = await makeEnv({ ...DBT, scenario: "dbt" });
  const recording = env.runner;
  const wfRoot = path.join(root, "workflows", "wf_0001");
  const reports = ["segments/seg_01/validation.json", "segments/seg_02/validation.json", "validation_workflow.json",
    "validation_workflow.normal.json"];
  const seen: string[][] = [];
  env.runner = {
    async run(role, wf, task, ctx) {
      if (role === "validator") {
        const present: string[] = [];
        for (const rel of reports) if (await exists(path.join(wfRoot, ...rel.split("/")))) present.push(rel);
        seen.push(present);
        return { ok: true, toolCalls: 0, ms: 0 };
      }
      const result = await recording.run(role, wf, task, ctx);
      if (role === "translator") {
        // what `python scripts/validate_dbt.py wf_0001` leaves behind
        for (const rel of reports) await writeJson(path.join(wfRoot, ...rel.split("/")), STALE_PASS);
      }
      return result;
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.deepEqual(seen, [[], []], "nothing a translator's own run wrote was on disk when the validator started");
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(String(m.reasons?.translate), /validator missing-output/);
});

// ---------- live hardening, Task L7 (R2): a timed-out session's checked output is kept ----------
// Live evidence (task-L7-brief.md): a translator wrote a valid, once-validated proc.sql and then
// spent the rest of its session reading source trying to debug a genuine semantics diff, until its
// session timed out -- and the orchestrator discarded the compiled procedure and retranslated from
// scratch (RETRY_ONCE). Since R2, a session that ends `timeout` has the stage's own `verify` run
// against it first; if it now passes, the result is accepted instead of retried.

test("L7 R2: a translator that timed out after writing a proc.sql that passes its check is kept, not retried", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  const recording = env.runner;
  let timedOutOnce = false;
  env.runner = {
    async run(role, wf, task, ctx) {
      const result = await recording.run(role, wf, task, ctx);
      // The translator's real MockRunner replay already wrote a compiling proc.sql; only its own
      // AgentResult is misreported as a timeout, exactly once -- the SAME shape a real SDK session
      // ending mid-cleanup would leave: the output on disk, the session's own outcome `timeout`.
      if (role === "translator" && !timedOutOnce) {
        timedOutOnce = true;
        return { ok: false, error: "timeout", detail: "Timeout after 2700000ms waiting for session.idle", toolCalls: result.toolCalls, ms: result.ms };
      }
      return result;
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  const translatorCalls = calls.tasks.filter((t) => t.role === "translator");
  assert.equal(translatorCalls.length, 1, "kept, not retried -- one translator session, not two");
  assert.equal(m.metrics.translator?.timeouts, 1, "the kept timeout is counted in the role's metrics");
  assert.ok(
    calls.logs.some((line) => line.includes("wf_0001: translator timed out after writing an output that passes its check — kept")),
    calls.logs.join("\n"),
  );
});

// L6 fix round 2 (I3), through the REAL CopilotRunner and its policy hooks: the live-review shape. A
// translator session writes a proc.sql that passes its check, tries three severe things -- another
// workflow's file, a network command, a write into the pipeline's scripts/ -- each refused by the real
// policy, and then ends by a timeout, a rate limit or a context overflow. Before the fix the timeout was
// kept by L7's path and the workflow reached VALIDATED; every ending now parks the stage `denied`.
const SEVERE_TRANSLATOR_CALLS = [
  { toolName: "view", toolArgs: { path: "workflows/wf_0002/manifest.json" } },
  { toolName: "powershell", toolArgs: { command: "curl.exe https://example.invalid/x" } },
  { toolName: "create", toolArgs: { path: "scripts/_diag.py", file_text: "print(1)\n" } },
];

/** A runner whose translator sessions go through a real `CopilotRunner` with a fake SDK client: the
 * session replays the canned output (the recording MockRunner), runs `SEVERE_TRANSLATOR_CALLS` through the
 * policy's own onPreToolUse hook, and then throws `ending`. Every other role runs as before. */
function severeTranslator(env: Env, recording: AgentRunner, ending: Error): AgentRunner {
  let current: { role: Role; wf: Manifest; task: string; ctx?: AgentCtx } | undefined;
  const client = {
    async createSession(config: { hooks?: SessionHooks }) {
      const hooks = config.hooks!;
      const base = { sessionId: "session-1", timestamp: new Date(), workingDirectory: env.root };
      return {
        on: () => () => undefined,
        async send() {
          return "";
        },
        async sendAndWait() {
          await recording.run(current!.role, current!.wf, current!.task, current!.ctx);
          for (const call of SEVERE_TRANSLATOR_CALLS) {
            const decision = await hooks.onPreToolUse!({ ...base, ...call }, { sessionId: "session-1" });
            assert.equal((decision as { permissionDecision?: string }).permissionDecision, "deny", call.toolName);
          }
          throw ending;
        },
        async disconnect() {
          return undefined;
        },
      };
    },
  } as unknown as CopilotClient;
  const copilot = new CopilotRunner({ client, root: env.root, config: env.config, profile: "local", agents: [] });
  copilot.attach(env);
  return {
    async run(role, wf, task, ctx) {
      if (role !== "translator") return recording.run(role, wf, task, ctx);
      current = { role, wf, task, ctx };
      return copilot.run(role, wf, task, ctx);
    },
  };
}

for (const [how, ending] of [
  ["a timeout", new Error("Timeout after 2700000ms waiting for session.idle")],
  ["a rate limit", new Error("HTTP 429 rate limit exceeded")],
  ["a context overflow", new Error("400 request (34965 tokens) exceeds the available context size (32768 tokens), try increasing it")],
] as [string, Error][]) {
  test(`L6 fix 2 (I3): a real CopilotRunner session with severe denials that ends by ${how} parks denied -- never kept`, async () => {
    const { env, calls } = await makeEnv({ wf: "wf_0001" });
    env.runner = severeTranslator(env, env.runner, ending);
    const m = await migrateWorkflow(env, "wf_0001", {});
    assert.equal(m.status.translate, "NEEDS_HUMAN", `${how}: the stage parks`);
    assert.match(String(m.reasons?.translate), /denied/, how);
    assert.equal(calls.tasks.filter((t) => t.role === "translator").length, 1, `${how}: parked at once -- not kept, not retried`);
    assert.equal(m.metrics.translator?.timeouts, undefined, `${how}: never kept`);
    assert.ok(calls.logs.some((line) => line.includes("severe-denials: 3 (parks at once)")), calls.logs.join("\n"));
    assert.ok(!calls.logs.some((line) => line.includes("— kept")), how);
  });
}

// The same guard in runAgent itself, for a runner that reports the ending but not the park: a result that
// carries severe denials is never kept, whatever `error` it names.
test("L6 fix 2 (I3): runAgent parks any runner's result that carries a severe denial -- never kept", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  const recording = env.runner;
  env.runner = {
    async run(role, wf, task, ctx) {
      const result = await recording.run(role, wf, task, ctx);
      if (role !== "translator") return result;
      return { ok: false, error: "timeout", detail: "Timeout after 2700000ms waiting for session.idle", toolCalls: result.toolCalls, ms: result.ms, severeDenials: 1 };
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(String(m.reasons?.translate), /denied/);
  assert.equal(calls.tasks.filter((t) => t.role === "translator").length, 1);
  assert.equal(m.metrics.translator?.timeouts, undefined);
});

test("L7 R2: a translator that timed out with nothing on disk still retries once, same as before R2", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", agentErrors: { translator: ["timeout"] } });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED", "the retry succeeds");
  const translatorCalls = calls.tasks.filter((t) => t.role === "translator");
  assert.equal(translatorCalls.length, 2, "the existing RETRY_ONCE path is unchanged when verify still fails");
  assert.equal(translatorCalls[1].task, translatorCalls[0].task, "a timeout's retry carries no feedback block, same as before R2");
  assert.equal(m.metrics.translator?.timeouts, undefined, "never kept, so never counted");
});

test("L7 R2: a fixer that timed out after repairing proc.sql to a passing compile is kept, not retried", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "fix-loop:seg_01" });
  const recording = env.runner;
  let timedOutOnce = false;
  env.runner = {
    async run(role, wf, task, ctx) {
      const result = await recording.run(role, wf, task, ctx);
      if (role === "fixer" && !timedOutOnce) {
        timedOutOnce = true;
        return { ok: false, error: "timeout", detail: "Timeout after 2700000ms waiting for session.idle", toolCalls: result.toolCalls, ms: result.ms };
      }
      return result;
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  const fixerCalls = calls.tasks.filter((t) => t.role === "fixer");
  assert.equal(fixerCalls.length, 1, "kept, not retried");
  assert.equal(m.metrics.fixer?.timeouts, 1);
});

// ---------- live hardening, Task L7 fix round 1/2 (R-b, IMP-2): only an output CHANGED this session is kept ----------
// Review I2 (fix round 1): the fixer's `verify` is `fileExists(proc.sql) || fileExists(proc.py)`, which
// the translator's iteration 0 already satisfied -- so a fixer that timed out having changed NOTHING was
// still logged "kept". Review IMP-2 (fix round 2): fix round 1's mtime-based `freshSince` was gameable by
// housekeeping the orchestrator or the agent itself writes near the same instant -- `dbt/compile_check.json`
// (the orchestrator, right after a compile failure, or the fixer's own permitted run of it) and
// `dbt/fix_log.md` (every dbt fixer is told to log every iteration there). The check is now by CONTENT
// (`sameContent`/`snapshot` in stages.ts): no clock, no tolerance, so nothing written before or after an
// attempt, however close in time, and no file outside the translation's own lane, can be mistaken for a
// real edit.

/** The three probe shapes the fix round 2 review verified break the mtime-based version (none of them
 * touch a model): the orchestrator's own `compile_check.json` from the wave before the fixer starts,
 * the fixer running `compile_check.py` itself (also `compile_check.json`), and the fixer logging its
 * iteration to `fix_log.md` -- exactly what `fixer.agent.md` tells it to do every time, whether or not
 * it changed a model. */
const DBT_HOUSEKEEPING_ONLY: Record<string, (dbtDir: string) => Promise<void>> = {
  "the orchestrator's own compile_check.json, written right before the fixer starts": async (dbtDir) => {
    await writeJson(path.join(dbtDir, "compile_check.json"), { status: "ERROR", target: "dbt", errors: ["stale"], statements: 0, models: 2 });
  },
  "the fixer's own permitted compile_check.py run": async (dbtDir) => {
    await writeJson(path.join(dbtDir, "compile_check.json"), { status: "OK", target: "dbt", errors: [], statements: 3, models: 2 });
  },
  "only fix_log.md, as fixer.agent.md instructs every iteration": async (dbtDir) => {
    await writeFile(path.join(dbtDir, "fix_log.md"), "## iteration 1 -- dbt project\n- symptom: see review.json\n- fix: investigating\n", "utf8");
  },
};

for (const [shape, writeHousekeeping] of Object.entries(DBT_HOUSEKEEPING_ONLY)) {
  test(`IMP-2: a dbt fixer that timed out after writing only ${shape} is not kept`, async () => {
    const { env, calls, files } = await makeEnv({ ...DBT, scenario: "fix-loop:dbt" });
    const recording = env.runner;
    const dbtDir = files.path("workflows", "wf_0001", "dbt");
    let fixerAttempts = 0;
    env.runner = {
      async run(role, wf, task, ctx) {
        if (role === "fixer" && ctx?.dbt) {
          fixerAttempts += 1;
          if (fixerAttempts === 1) {
            // This attempt never reaches the real (mock) runner -- exactly like an SDK session that
            // timed out having written only housekeeping -- so `calls.tasks`/`roles`/`order` are
            // recorded here, the same bookkeeping `recording.run` itself would have done.
            calls.tasks.push({ role, segment: ctx?.segment, dbt: ctx?.dbt, batch: ctx?.batch, task });
            calls.roles.push(role);
            calls.order.push(`agent:${role}`);
            await writeHousekeeping(dbtDir);
            return { ok: false, error: "timeout", detail: "Timeout after 2700000ms waiting for session.idle", toolCalls: 0, ms: 0 };
          }
        }
        return recording.run(role, wf, task, ctx);
      },
    };
    const m = await migrateWorkflow(env, "wf_0001", {});
    assert.equal(m.status.translate, "VALIDATED", "the real fixer attempt (the retry) still fixes the project");
    const fixerCalls = calls.tasks.filter((t) => t.role === "fixer");
    assert.equal(fixerCalls.length, 2, "not kept: RETRY_ONCE ran -- housekeeping alone is not a model change");
    assert.equal(m.metrics.fixer?.timeouts, undefined, "never kept, so never counted");
  });
}

test("L7 R-b: a fixer that timed out without writing is not kept -- it retries as before R2", async () => {
  const { env, calls, files } = await makeEnv({ wf: "wf_0001", scenario: "fix-loop:seg_01" });
  const recording = env.runner;
  let fixerAttempts = 0;
  env.runner = {
    async run(role, wf, task, ctx) {
      if (role === "fixer") {
        fixerAttempts += 1;
        if (fixerAttempts === 1) {
          // This attempt never reaches the real (mock) runner -- exactly like an SDK session that
          // timed out before writing anything -- so `calls.tasks`/`roles`/`order` are recorded here,
          // the same bookkeeping `recording.run` itself would have done. Nothing touches proc.sql,
          // so the content snapshot taken at this attempt's own start is unchanged; no clock or
          // backdating is involved at all (fix round 2, IMP-2).
          calls.tasks.push({ role, segment: ctx?.segment, dbt: ctx?.dbt, batch: ctx?.batch, task });
          calls.roles.push(role);
          calls.order.push(`agent:${role}`);
          return { ok: false, error: "timeout", detail: "Timeout after 2700000ms waiting for session.idle", toolCalls: 0, ms: 0 };
        }
      }
      return recording.run(role, wf, task, ctx);
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED", "the real fixer attempt (the retry) still fixes it");
  const fixerCalls = calls.tasks.filter((t) => t.role === "fixer");
  assert.equal(fixerCalls.length, 2, "not kept: RETRY_ONCE ran, same as before R-b");
  assert.equal(fixerCalls[1].task, fixerCalls[0].task, "a timeout's retry carries no feedback block");
  assert.equal(m.metrics.fixer?.timeouts, undefined, "never kept, so never counted");
});

test("IMP-2: a segment fixer that timed out after writing only fix_log.md (not proc.sql) is not kept", async () => {
  const { env, calls, files } = await makeEnv({ wf: "wf_0001", scenario: "fix-loop:seg_01" });
  const recording = env.runner;
  const segDir = files.path("workflows", "wf_0001", "segments", "seg_01");
  let fixerAttempts = 0;
  env.runner = {
    async run(role, wf, task, ctx) {
      if (role === "fixer") {
        fixerAttempts += 1;
        if (fixerAttempts === 1) {
          calls.tasks.push({ role, segment: ctx?.segment, dbt: ctx?.dbt, batch: ctx?.batch, task });
          calls.roles.push(role);
          calls.order.push(`agent:${role}`);
          // proc.sql is untouched; only the fixer's own log, exactly what fixer.agent.md asks for
          // every iteration, whether or not it actually repaired anything.
          await writeFile(path.join(segDir, "fix_log.md"), "## iteration 1 -- seg_01\n- symptom: see validation.json\n- fix: investigating\n", "utf8");
          return { ok: false, error: "timeout", detail: "Timeout after 2700000ms waiting for session.idle", toolCalls: 0, ms: 0 };
        }
      }
      return recording.run(role, wf, task, ctx);
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED", "the real fixer attempt (the retry) still fixes it");
  const fixerCalls = calls.tasks.filter((t) => t.role === "fixer");
  assert.equal(fixerCalls.length, 2, "not kept: fix_log.md is not proc.sql");
  assert.equal(m.metrics.fixer?.timeouts, undefined, "never kept, so never counted");
});

test("L7 R-b: a dbt fixer that timed out after actually repairing a model is kept, not retried", async () => {
  const { env, calls } = await makeEnv({ ...DBT, scenario: "fix-loop:dbt" });
  const recording = env.runner;
  let timedOutOnce = false;
  env.runner = {
    async run(role, wf, task, ctx) {
      const result = await recording.run(role, wf, task, ctx);
      if (role === "fixer" && ctx?.dbt && !timedOutOnce) {
        timedOutOnce = true;
        return { ok: false, error: "timeout", detail: "Timeout after 2700000ms waiting for session.idle", toolCalls: result.toolCalls, ms: result.ms };
      }
      return result;
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  const fixerCalls = calls.tasks.filter((t) => t.role === "fixer");
  assert.equal(fixerCalls.length, 1, "kept, not retried");
  assert.equal(m.metrics.fixer?.timeouts, 1);
});

async function exists(file: string): Promise<boolean> {
  try {
    await stat(file);
    return true;
  } catch {
    return false;
  }
}
