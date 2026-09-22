// The state machine: docs/spec/01-copilot-setup.md Part B §3-§5.
// The first nine tests are the task brief's contract, verbatim.
import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { mkdir, stat, writeFile } from "node:fs/promises";
import { makeEnv, readManifest } from "./fakes.ts";
import { migrateWorkflow, masterSql, plannedStages } from "../stages.ts";
import { writeJson } from "../manifest.ts";

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
  const withGh = await makeEnv({ wf: "wf_0001", intake: "WAITING_FOR_ANSWERS", hasGh: true });
  await migrateWorkflow(withGh.env, "wf_0001", {});
  const issue = withGh.calls.sh.find((c) => c.cmd === "gh");
  assert.ok(issue, "gh issue create is attempted when gh is installed");
  assert.deepEqual(issue!.args.slice(0, 2), ["issue", "create"]);

  const withoutGh = await makeEnv({ wf: "wf_0001", intake: "WAITING_FOR_ANSWERS" });
  await migrateWorkflow(withoutGh.env, "wf_0001", {});
  assert.equal(withoutGh.calls.sh.length, 0);
  assert.ok(withoutGh.calls.logs.some((line) => line.includes("open_questions.md")));
});
test("a PR is opened only when gh is installed", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", hasGh: true });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.pr, "OPEN");
  assert.deepEqual(calls.sh[0].args.slice(0, 2), ["pr", "create"]);

  const offline = await makeEnv({ wf: "wf_0001" });
  const n = await migrateWorkflow(offline.env, "wf_0001", {});
  assert.equal(n.status.pr, undefined);
  assert.ok(offline.calls.logs.some((line) => /gh not installed/.test(line)));
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
