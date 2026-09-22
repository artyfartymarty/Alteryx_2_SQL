// One real end-to-end run of wf_0001 (plan Task 15 Step 5): the REAL Python scripts
// (parse, intake_touchpoints, intake_prompt, segment, alteryx_sim, compile_check,
// validate_segment) driven by the orchestrator's actual state machine, with agents replayed
// from samples/wf_0001/canned/** by the real MockRunner — nothing here is faked except the
// agent. Everything is offline: DuckDB + the Alteryx simulator + mock agents, never a real
// Snowflake account or Alteryx Designer.
//
// seed -> run 1 parks at WAITING_FOR_ANSWERS (intake is non-interactive and unanswered) ->
// scripts/dev/answer_samples.py copies samples/wf_0001/sample.json's answers into
// manifest.answers -> run 2 (scenario fix-loop:seg_01, so the translator's first attempt is the
// canned broken SQL) ends VALIDATED with the fixer having run once -> run 3 is a no-op.
//
// This test spawns the real venv Python and is skipped — never failed — when that interpreter
// cannot be found at either of its expected paths.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { cp, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { main } from "../cli.ts";
import { tempRoot } from "./fakes.ts";

const execFileAsync = promisify(execFile);

const HERE = path.dirname(fileURLToPath(import.meta.url));
// scripts/, samples/, mappings/, catalog/, cookbook/, .github/agents/ live in THIS tree (which
// may be a git worktree under <checkout>/.worktrees/<name>), but the Python virtualenv is never
// per-worktree here: every worktree shares the primary checkout's .venv, two directories up.
// PIPELINE_PYTHON overrides both when the venv lives somewhere else.
const REPO_ROOT = path.resolve(HERE, "..", "..");
const SHARED_ROOT = path.resolve(REPO_ROOT, "..", "..");
const VENV_PYTHONS = (root: string) => [
  path.join(root, ".venv", "Scripts", "python.exe"),
  path.join(root, ".venv", "bin", "python"),
];
const PYTHON_CANDIDATES = [process.env.PIPELINE_PYTHON, ...VENV_PYTHONS(REPO_ROOT), ...VENV_PYTHONS(SHARED_ROOT)]
  .filter((candidate): candidate is string => Boolean(candidate));

interface PyResult {
  code: number;
  stdout: string;
  stderr: string;
}

async function exists(file: string): Promise<boolean> {
  try {
    await stat(file);
    return true;
  } catch {
    return false;
  }
}

/** PIPELINE_PYTHON, else a per-worktree venv if this tree has one, else the shared checkout's. */
async function resolvePython(): Promise<string | undefined> {
  for (const candidate of PYTHON_CANDIDATES) {
    if (await exists(candidate)) return candidate;
  }
  return undefined;
}

function runPython(python: string, args: string[], cwd: string): Promise<PyResult> {
  return execFileAsync(python, args, { cwd }).then(
    ({ stdout, stderr }) => ({ code: 0, stdout, stderr }),
    (error) => {
      const e = error as { code?: number; stdout?: string; stderr?: string };
      return { code: typeof e.code === "number" ? e.code : 1, stdout: e.stdout ?? "", stderr: e.stderr ?? "" };
    },
  );
}

async function readManifest(root: string, id: string): Promise<any> {
  return JSON.parse(await readFile(path.join(root, "workflows", id, "manifest.json"), "utf8"));
}

/**
 * A scratch root with everything the orchestrator resolves relative to --root: scripts/,
 * samples/, mappings/, catalog/, cookbook/, .github/agents/ and its own orchestrator.config.json
 * pointing at the real venv interpreter by absolute path. workflows/wf_0001 is seeded (source +
 * golden inputs + a bare manifest.json) but not yet parsed — the orchestrator's own parse stage
 * does that on run 1.
 */
async function seedScratchRoot(python: string): Promise<string> {
  const root = await tempRoot("orch-int-");
  await Promise.all(
    ["scripts", "samples", "mappings", "catalog", "cookbook"].map((dir) =>
      cp(path.join(REPO_ROOT, dir), path.join(root, dir), { recursive: true }),
    ),
  );
  await mkdir(path.join(root, ".github", "agents"), { recursive: true });
  await cp(path.join(REPO_ROOT, ".github", "agents"), path.join(root, ".github", "agents"), { recursive: true });
  await writeFile(path.join(root, "orchestrator.config.json"), `${JSON.stringify({ python }, null, 2)}\n`, "utf8");

  const seeded = await runPython(python, ["scripts/dev/build_samples.py", "seed", "--only", "wf_0001"], root);
  assert.equal(seeded.code, 0, `seeding wf_0001 failed:\n${seeded.stdout}\n${seeded.stderr}`);
  return root;
}

test(
  "wf_0001 end to end: real python scripts, mock agents, a fix-loop iteration, then a no-op re-run",
  { timeout: 120_000 },
  async (t) => {
    const python = await resolvePython();
    if (!python) {
      t.skip(`venv python not found at any of ${PYTHON_CANDIDATES.join(", ")} (set PIPELINE_PYTHON) — skipping the real-Python integration run`);
      return;
    }

    const root = await seedScratchRoot(python);

    // --- run 1: non-interactive, unanswered — parks at WAITING_FOR_ANSWERS -------------------
    const code1 = await main(["--root", root, "--runner", "mock", "--no-interactive"]);
    assert.equal(code1, 0, "a parked workflow (WAITING_FOR_ANSWERS) is not a NEEDS_HUMAN-class exit");

    const afterRun1 = await readManifest(root, "wf_0001");
    assert.equal(afterRun1.status.parse, "PARSED");
    assert.equal(afterRun1.status.intake, "WAITING_FOR_ANSWERS");
    assert.equal(afterRun1.status.analyze, undefined, "analyze must not run before intake is READY");
    assert.equal(await exists(path.join(root, "workflows", "wf_0001", "intake", "touchpoints.json")), true);
    assert.equal(await exists(path.join(root, "workflows", "wf_0001", "intake", "open_questions.md")), true);

    // --- answer_samples.py: feed samples/wf_0001/sample.json's answers into manifest.answers --
    const answered = await runPython(python, ["scripts/dev/answer_samples.py", "--only", "wf_0001"], root);
    assert.equal(answered.code, 0, `answer_samples.py failed:\n${answered.stdout}\n${answered.stderr}`);
    const afterAnswers = await readManifest(root, "wf_0001");
    assert.deepEqual(afterAnswers.answers, {
      Q1: "SALES.RAW.ORDERS",
      Q2: "ANALYTICS.CURATED.SALES_SUMMARY",
      Q3: "ANALYTICS.CURATED.EXCLUDED_ORDERS",
    });

    // --- run 2: scenario fix-loop:seg_01 — the translator's first attempt is the canned broken
    // SQL, so the validator must fail it (needs_human: false) before the fixer's canned proc.sql
    // passes on the next iteration. -----------------------------------------------------------
    const code2 = await main(["--root", root, "--runner", "mock", "--no-interactive", "--scenario", "fix-loop:seg_01"]);
    assert.equal(code2, 0);

    const afterRun2 = await readManifest(root, "wf_0001");
    assert.equal(afterRun2.status.intake, "READY");
    assert.equal(afterRun2.status.analyze, "DONE");
    assert.equal(afterRun2.status.golden, "DONE");
    assert.equal(afterRun2.status.translate, "VALIDATED");
    assert.equal(afterRun2.status.document, "DONE");
    assert.equal(afterRun2.segment_status?.seg_01, "PASS");
    assert.equal(
      await exists(path.join(root, "workflows", "wf_0001", "segments", "seg_01", "fix_log.md")),
      true,
      "the fixer must have run once for the fix loop to have engaged at all",
    );
    const fixLog = await readFile(path.join(root, "workflows", "wf_0001", "segments", "seg_01", "fix_log.md"), "utf8");
    assert.match(fixLog, /FIXED/);

    // validation.json belongs to the LAST validate_segment.py run (that script deletes and
    // rewrites it on every invocation) — it must be the real script's own PASS, with numbers
    // only a real comparison could produce, not anything MockRunner fabricated.
    const validation = JSON.parse(
      await readFile(path.join(root, "workflows", "wf_0001", "segments", "seg_01", "validation.json"), "utf8"),
    );
    assert.equal(validation.verdict, "PASS");
    assert.equal(typeof validation.idempotent, "boolean");

    const masterSql = await readFile(path.join(root, "workflows", "wf_0001", "procs", "master.sql"), "utf8");
    assert.match(masterSql, /CALL MIG_WORK\.WF0001_SEG_01\(/);

    // --- run 3: nothing changed — every stage is already terminal-good, so nothing re-executes.
    const procMtimeBefore = (await stat(path.join(root, "workflows", "wf_0001", "segments", "seg_01", "proc.sql"))).mtimeMs;
    const validationMtimeBefore = (await stat(path.join(root, "workflows", "wf_0001", "segments", "seg_01", "validation.json")))
      .mtimeMs;

    const code3 = await main(["--root", root, "--runner", "mock", "--no-interactive"]);
    assert.equal(code3, 0);

    const procMtimeAfter = (await stat(path.join(root, "workflows", "wf_0001", "segments", "seg_01", "proc.sql"))).mtimeMs;
    const validationMtimeAfter = (await stat(path.join(root, "workflows", "wf_0001", "segments", "seg_01", "validation.json")))
      .mtimeMs;
    assert.equal(procMtimeAfter, procMtimeBefore, "the translate stage must not re-run: proc.sql was not rewritten");
    assert.equal(
      validationMtimeAfter,
      validationMtimeBefore,
      "the translate stage must not re-run: validate_segment.py was not called again",
    );

    const afterRun3 = await readManifest(root, "wf_0001");
    const dropUpdatedAt = ({ updated_at, ...rest }: Record<string, unknown>) => rest;
    assert.deepEqual(dropUpdatedAt(afterRun3), dropUpdatedAt(afterRun2), "only updated_at may differ on a no-op re-run");
  },
);
