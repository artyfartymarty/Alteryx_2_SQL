// Live hardening, Task L8: the orchestrator writes every mechanical line of a translation.
//
// Live evidence (task-L8-brief.md): a SQL translator spent its session on the procedure's header, LET lines
// and write statements -- all mechanical -- and a dbt translator overflowed its context without writing a
// single project file. Now `scripts/translation_scaffold.py` writes the skeleton before the translator's
// first session (R2): once, only where no translation exists yet, never before a fixer; the translator
// replaces every TODO(scaffold) body and `compile_check.py` refuses a remaining one. A translator whose
// skeleton is still untouched when its session ends has not produced anything: that is `missing-output`
// (retried once, told why), and a timeout that left it untouched is never kept.
import { test } from "node:test";
import assert from "node:assert/strict";
import { writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { makeEnv, SKELETON } from "./fakes.ts";
import { migrateWorkflow, SCAFFOLD_TODO, SESSION_RULE_LINES } from "../stages.ts";
import { RETRY_SENTENCE } from "../feedback.ts";
import type { AgentResult } from "../types.ts";

const DBT = { wf: "wf_0001", twoWaves: true, manifest: { output_target: "dbt" as const } };

/** `py:<script> <args…>` and `agent:<role>` interleaved in the order they happened. */
function sequence(calls: Awaited<ReturnType<typeof makeEnv>>["calls"]): string[] {
  let next = 0;
  return calls.order.map((entry) => (entry.startsWith("py:") ? `${entry} ${calls.py[next++].args.join(" ")}`.trim() : entry));
}

const scaffoldCalls = (calls: Awaited<ReturnType<typeof makeEnv>>["calls"]) =>
  calls.py.filter((c) => c.script === "scripts/translation_scaffold.py");

test("L8: the fake scaffold's skeleton carries the one marker the Python scripts refuse", () => {
  assert.equal(SCAFFOLD_TODO, "TODO(scaffold)");
  assert.ok(SKELETON("proc.sql").includes(SCAFFOLD_TODO) && SKELETON("proc.py").includes(SCAFFOLD_TODO));
});

test("L8 R2: the scaffold writes a SQL segment's skeleton once, right before the translator's first session", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  const seq = sequence(calls);
  const at = seq.indexOf("py:scripts/translation_scaffold.py wf_0001 --segment seg_01");
  assert.ok(at > 0, seq.join("\n"));
  assert.equal(seq[at + 1], "agent:translator", "nothing between the scaffold and the translator");
  assert.ok(at > seq.indexOf("py:scripts/dev/alteryx_sim.py wf_0001 --set all"), "after golden, inside translate");
  assert.equal(scaffoldCalls(calls).length, 1);
  assert.ok(calls.logs.some((line) => line.includes("translation_scaffold: note: seg_01: a note the scaffold prints")),
    "the scaffold's notes reach the log");
  assert.ok(calls.logs.includes("wf_0001: translation_scaffold: wrote workflows/wf_0001/segments/seg_01/proc.sql"),
    "and what it wrote");
});

test("L8 R2: the scaffold runs once per segment and never before a fixer -- the translator's work is never re-scaffolded", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "fix-loop:seg_01" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.ok(calls.roles.filter((r) => r === "fixer").length >= 1, "the fix loop ran");
  assert.equal(scaffoldCalls(calls).length, 1);
  const seq = sequence(calls);
  assert.ok(seq.indexOf("py:scripts/translation_scaffold.py wf_0001 --segment seg_01") < seq.indexOf("agent:translator"));
});

test("L8 R2: a Snowpark segment's skeleton is its proc.py, and each segment of a workflow gets its own", async () => {
  const { env, calls, files } = await makeEnv({ wf: "wf_0001", twoWaves: true, scenario: "snowpark:seg_02" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.deepEqual(scaffoldCalls(calls).map((c) => c.args), [["wf_0001", "--segment", "seg_01"], ["wf_0001", "--segment", "seg_02"]]);
  const translator = calls.tasks.find((t) => t.role === "translator" && t.segment === "seg_02")!;
  assert.match(translator.task, /workflows\/wf_0001\/segments\/seg_02\/proc\.py/);
  assert.ok(await files.exists("workflows", "wf_0001", "segments", "seg_02", "proc.py"));
});

test("L8 R2: a resumed run keeps the translator's file: no scaffold, and the translator starts from its own work", async () => {
  const { env, calls, files } = await makeEnv({ wf: "wf_0001" });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const own = files.path("workflows", "wf_0001", "segments", "seg_01", "proc.sql");
  await mkdir(path.dirname(own), { recursive: true });
  await writeFile(own, "-- the translator's own work from an earlier run\n", "utf8");
  const recording = env.runner;
  let seen: string | undefined;
  env.runner = {
    async run(role, wf, task, ctx) {
      if (role === "translator") seen = await files.read("workflows", "wf_0001", "segments", "seg_01", "proc.sql");
      return recording.run(role, wf, task, ctx);
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.equal(scaffoldCalls(calls).length, 0, "an existing translation is never scaffolded over");
  assert.equal(seen, "-- the translator's own work from an earlier run\n");
  const translator = calls.tasks.find((t) => t.role === "translator")!;
  assert.doesNotMatch(translator.task, /has written the skeleton/, "no skeleton sentence for a file that is not one");
});

test("L8 R2: the translator is told the skeleton is written, what to replace and what never to change", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  await migrateWorkflow(env, "wf_0001", {});
  const task = calls.tasks.find((t) => t.role === "translator")!.task.replace(/\s+/g, " ");
  for (const phrase of [
    "The orchestrator has written the skeleton, workflows/wf_0001/segments/seg_01/proc.sql",
    `Replace every ${SCAFFOLD_TODO} body with the transformation`,
    "not the header, the LET lines, the write statements or the file layout",
    `scripts/compile_check.py refuses a remaining ${SCAFFOLD_TODO} (scaffold:todo)`,
  ]) {
    assert.ok(task.includes(phrase), `${phrase}\n${task}`);
  }
});

test("L8 R2: a fixer facing a skeleton the translator left half-filled is told which lines are still its to write", async () => {
  const { env, calls, files } = await makeEnv({ wf: "wf_0001", scenario: "compile-fails:seg_01" });
  const recording = env.runner;
  env.runner = {
    async run(role, wf, task, ctx) {
      if (role === "translator") {
        // touched, but one TODO body still unwritten
        const own = files.path("workflows", "wf_0001", "segments", "seg_01", "proc.sql");
        await writeFile(own, `${SKELETON("proc.sql")}-- the first stub is done\n`, "utf8");
        calls.roles.push(role);
        calls.tasks.push({ role, segment: ctx?.segment, task });
        calls.order.push(`agent:${role}`);
        return { ok: true, toolCalls: 0, ms: 0 };
      }
      return recording.run(role, wf, task, ctx);
    },
  };
  await migrateWorkflow(env, "wf_0001", {});
  const fixer = calls.tasks.find((t) => t.role === "fixer")!;
  assert.ok(fixer, "the compile failure sent it to the fixer");
  assert.match(fixer.task, /TODO\(scaffold\) bod(y|ies) .*still unwritten/);
  assert.match(fixer.task, /keep the mechanical lines around them as they are/);
});

test("L8 R2: a translator that leaves the skeleton untouched wrote nothing: retried once, told why, then parked", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  // intake, analyze and golden run as usual; from translate on, every session ends without touching a file
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  env.runner = {
    async run(role, _wf, task, ctx): Promise<AgentResult> {
      calls.roles.push(role);
      calls.tasks.push({ role, segment: ctx?.segment, task });
      return { ok: true, toolCalls: 0, ms: 0 };
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(m.reasons?.translate ?? "", /seg_01: translator missing-output/);
  const translators = calls.tasks.filter((t) => t.role === "translator");
  assert.equal(translators.length, 2, "one retry, as for any missing output");
  assert.ok(!translators[0].task.includes(RETRY_SENTENCE));
  assert.ok(translators[1].task.includes(RETRY_SENTENCE), translators[1].task);
  assert.match(translators[1].task, /still the orchestrator's skeleton/);
});

test("L8 R2: a translator that timed out without touching the skeleton is not kept (the skeleton is not its output)", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", agentErrors: { translator: ["timeout", "timeout"] } });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(m.reasons?.translate ?? "", /seg_01: translator timeout/);
  assert.equal(m.metrics.translator?.timeouts, undefined, "never kept, so never counted");
  assert.equal(calls.tasks.filter((t) => t.role === "translator").length, 2);
});

test("L8 R2: a translator that filled the skeleton and then timed out IS kept (L7 R2 unchanged)", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001" });
  const recording = env.runner;
  let timedOut = false;
  env.runner = {
    async run(role, wf, task, ctx) {
      const result = await recording.run(role, wf, task, ctx);      // the mock writes the canned proc.sql
      if (role === "translator" && !timedOut) {
        timedOut = true;
        return { ok: false, error: "timeout", detail: "Timeout after 2700000ms waiting for session.idle", toolCalls: 0, ms: 0 };
      }
      return result;
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.equal(calls.tasks.filter((t) => t.role === "translator").length, 1);
  assert.equal(m.metrics.translator?.timeouts, 1);
});

test("L8 R2: the scaffold exiting 2 is a script error for the segment: no translator session starts", async () => {
  const { env, calls } = await makeEnv({ wf: "wf_0001", scenario: "translation-scaffold-crashes" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(m.reasons?.translate ?? "", /seg_01: script-error/);
  assert.ok(!calls.roles.includes("translator"));
  assert.ok(calls.logs.some((line) => line.includes("translation_scaffold.py exited 2")), calls.logs.join("\n"));
});

test("L8 R2: a dbt workflow's skeleton is written once, for the whole project, before the translator", async () => {
  const { env, calls } = await makeEnv({ ...DBT, scenario: "dbt" });
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.deepEqual(scaffoldCalls(calls).map((c) => c.args), [["wf_0001", "--dbt"]], "N3: the project is asked for explicitly");
  const seq = sequence(calls);
  assert.equal(seq[seq.indexOf("py:scripts/translation_scaffold.py wf_0001 --dbt") + 1], "agent:translator");
  const task = calls.tasks.find((t) => t.role === "translator" && t.dbt)!.task.replace(/\s+/g, " ");
  for (const phrase of [
    "The orchestrator has written the project's skeleton under workflows/wf_0001/dbt/",
    `Replace every ${SCAFFOLD_TODO}`,
    "not the file layout, the YAML files or the config lines",
    `scripts/compile_check.py wf_0001 --target dbt refuses a remaining ${SCAFFOLD_TODO} (scaffold:todo)`,
  ]) {
    assert.ok(task.includes(phrase), `${phrase}\n${task}`);
  }
});

test("L8 R2: a dbt fix loop scaffolds once; a project that already exists is never scaffolded", async () => {
  const loop = await makeEnv({ ...DBT, scenario: "fix-loop:dbt" });
  const m = await migrateWorkflow(loop.env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.equal(scaffoldCalls(loop.calls).length, 1);

  const resumed = await makeEnv({ ...DBT, scenario: "dbt" });
  await migrateWorkflow(resumed.env, "wf_0001", { stopAfter: "golden" });
  const project = resumed.files.path("workflows", "wf_0001", "dbt", "dbt_project.yml");
  await mkdir(path.dirname(project), { recursive: true });
  await writeFile(project, "name: wf_0001\n", "utf8");
  await migrateWorkflow(resumed.env, "wf_0001", {});
  assert.equal(scaffoldCalls(resumed.calls).length, 0);
});

test("L8 R2: a dbt translator that leaves the project's skeleton untouched is retried once, then parks", async () => {
  const { env } = await makeEnv({ ...DBT, scenario: "dbt" });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const tasks: string[] = [];
  env.runner = {
    async run(role, _wf, task): Promise<AgentResult> {
      if (role === "translator") tasks.push(task);
      return { ok: true, toolCalls: 0, ms: 0 };
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(m.reasons?.translate ?? "", /dbt: translator missing-output/);
  assert.equal(tasks.length, 2);
  assert.match(tasks[1], /still the orchestrator's skeleton/);
});

test("L8 R3: the sixth session rule: the golden data is not read in bulk", () => {
  const rule = SESSION_RULE_LINES[5].replace(/\s+/g, " ");
  for (const phrase of ["Never read the golden data in bulk", "the contract describes every column",
    "at most one golden set's inputs", "the validator compares the rest"]) {
    assert.ok(rule.includes(phrase), phrase);
  }
});

// ---------- fix round 1 (review-L8-report.md): I1, I3, M1 ----------

const COMPLETE = "-- the orchestrator's skeleton (fake): complete, nothing to translate\nSELECT 1;\n";

test("L8 fix 1 (I1): a skeleton with nothing to fill, left as written, is the translation -- not missing-output", async () => {
  const { env, calls, files } = await makeEnv({ wf: "wf_0001" });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const py = env.py;
  env.py = async (script, args, opts) => {
    if (script !== "scripts/translation_scaffold.py") return py(script, args, opts);
    calls.py.push({ script, args, inheritStdio: false });
    const own = files.path("workflows", "wf_0001", "segments", "seg_01", "proc.sql");
    await mkdir(path.dirname(own), { recursive: true });
    await writeFile(own, COMPLETE, "utf8");          // what the real script writes for a pass-through segment
    return { ok: true, code: 0, out: "wrote workflows/wf_0001/segments/seg_01/proc.sql", err: "" };
  };
  const recording = env.runner;
  const translator: string[] = [];
  env.runner = {
    async run(role, wf, task, ctx) {
      if (role !== "translator") return recording.run(role, wf, task, ctx);
      translator.push(task);                          // writes its notes, leaves the complete procedure alone
      await writeFile(files.path("workflows", "wf_0001", "segments", "seg_01", "translation_notes.md"), "- nothing to translate\n", "utf8");
      return { ok: true, toolCalls: 3, ms: 0 };
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED", JSON.stringify(m.reasons));
  assert.equal(translator.length, 1, "no retry");
  assert.equal(await files.read("workflows", "wf_0001", "segments", "seg_01", "proc.sql"), COMPLETE);
  const task = translator[0].replace(/\s+/g, " ");
  assert.ok(task.includes("is already complete"), task);
  assert.doesNotMatch(task, /Replace every TODO\(scaffold\)/, "no TODO sentence when there is none");
});

test("L8 fix 1 (M1): an untouched skeleton an EARLIER run left is judged like a fresh one", async () => {
  const { env, calls, files } = await makeEnv({ wf: "wf_0001" });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const own = files.path("workflows", "wf_0001", "segments", "seg_01", "proc.sql");
  await mkdir(path.dirname(own), { recursive: true });
  await writeFile(own, SKELETON("proc.sql"), "utf8");     // a crashed run's skeleton, still unfilled
  const tasks: string[] = [];
  env.runner = {
    async run(role, _wf, task): Promise<AgentResult> {
      if (role === "translator") tasks.push(task);
      return { ok: true, toolCalls: 0, ms: 0 };
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(scaffoldCalls(calls).length, 0, "the file exists: never scaffolded over");
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(m.reasons?.translate ?? "", /seg_01: translator missing-output/);
  assert.equal(tasks.length, 2);
  assert.match(tasks[0], /has written the skeleton/, "the file still holds TODO bodies: the translator is told so");
  assert.match(tasks[1], /still the orchestrator's skeleton/);
});

test("L8 fix 1 (I3): a dbt translator that wrote only translation_notes.md has not touched the skeleton", async () => {
  const { env, files } = await makeEnv({ ...DBT, scenario: "dbt" });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const recording = env.runner;
  const tasks: string[] = [];
  env.runner = {
    async run(role, wf, task, ctx) {
      if (role !== "translator") return recording.run(role, wf, task, ctx);
      tasks.push(task);
      await writeFile(files.path("workflows", "wf_0001", "dbt", "translation_notes.md"), "I read the contracts\n", "utf8");
      return { ok: true, toolCalls: 3, ms: 0 };
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "NEEDS_HUMAN");
  assert.match(m.reasons?.translate ?? "", /dbt: translator missing-output/);
  assert.equal(tasks.length, 2);
  assert.match(tasks[1], /still the orchestrator's skeleton/);
});

test("L8 fix 1 (I3): a dbt translator that timed out having written only compile_check.json is not kept", async () => {
  const { env, calls, files } = await makeEnv({ ...DBT, scenario: "dbt" });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const recording = env.runner;
  let attempts = 0;
  env.runner = {
    async run(role, wf, task, ctx) {
      if (role === "translator" && ++attempts === 1) {
        calls.tasks.push({ role, dbt: ctx?.dbt, task });
        await writeFile(files.path("workflows", "wf_0001", "dbt", "compile_check.json"), "{\"status\": \"ERROR\"}\n", "utf8");
        return { ok: false, error: "timeout", detail: "Timeout after 2700000ms waiting for session.idle", toolCalls: 3, ms: 0 };
      }
      return recording.run(role, wf, task, ctx);        // the retry: the mock writes the canned project
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.equal(attempts, 2, "retried, not kept");
  assert.equal(m.metrics.translator?.timeouts, undefined, "never kept, so never counted");
});

test("L8 fix 1 (I3): a dbt translator that wrote a model and then timed out IS kept", async () => {
  const { env, files } = await makeEnv({ ...DBT, scenario: "dbt" });
  await migrateWorkflow(env, "wf_0001", { stopAfter: "golden" });
  const recording = env.runner;
  let attempts = 0;
  env.runner = {
    async run(role, wf, task, ctx) {
      const result = await recording.run(role, wf, task, ctx);     // the mock writes the canned project
      if (role === "translator" && ++attempts === 1) {
        return { ok: false, error: "timeout", detail: "Timeout after 2700000ms waiting for session.idle", toolCalls: 3, ms: 0 };
      }
      return result;
    },
  };
  const m = await migrateWorkflow(env, "wf_0001", {});
  assert.equal(m.status.translate, "VALIDATED");
  assert.equal(attempts, 1);
  assert.equal(m.metrics.translator?.timeouts, 1);
  assert.ok(await files.exists("workflows", "wf_0001", "dbt", "models", "orders_out.sql"));
});
