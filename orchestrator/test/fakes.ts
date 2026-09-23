// Self-contained fixtures for the stage and CLI tests.
//
// Two things are faked and nothing else:
//   * `py` performs each Python script's FILE EFFECTS itself, so these tests need no
//     interpreter, no DuckDB and no samples committed by another task. Exit codes follow
//     the scripts' documented contract (0 success, 1 domain failure, 2 usage/unexpected).
//   * the agent runner records which role ran and can be told to fail with a given
//     AgentError; everything else is the real MockRunner copying real files.
//
// The fixture tree is a minimal stand-in for samples/<wf>/: a one-segment wf_0001 that
// validates, and a wf_0005 whose canned analyzer says tier T3 and whose canned
// parser-recovery writes a parser extension.
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { DEFAULT_CONFIG } from "../cli.ts";
import { loadManifest, readJsonOr, wfDir, writeJson } from "../manifest.ts";
import { MockRunner } from "../runner.ts";
import type {
  AgentError,
  AgentResult,
  AgentRunner,
  Env,
  Manifest,
  OrchestratorConfig,
  Role,
  ShResult,
} from "../types.ts";

export { loadManifest as readManifest };

const GOOD_SQL = (wf: string, seg: string) => `-- tool 1: Input Data
CREATE OR REPLACE PROCEDURE MIG_WORK.${wf.toUpperCase().replace("_", "")}_${seg.toUpperCase()}(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
BEGIN
  LET ORDERS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ORDERS';
  CREATE OR REPLACE TABLE MIG_WORK.${wf.toUpperCase().replace("_", "")}_${seg.toUpperCase()}_OUT AS
  WITH t1_input AS (SELECT ACCT, AMOUNT FROM IDENTIFIER(:ORDERS_SRC))
  -- tool 2: Filter — NULL evaluations go to the False branch
  SELECT ACCT, AMOUNT FROM t1_input WHERE NOT (AMOUNT = 0) OR AMOUNT IS NULL;
  RETURN 'OK';
END;
`;

const BROKEN_SQL = `-- tool 1: Input Data
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0001_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
BEGIN
  LET ORDERS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ORDERS';
  CREATE OR REPLACE TABLE MIG_WORK.WF0001_SEG_01_OUT AS
  WITH t1_input AS (SELECT ACCT, AMOUNT FROM IDENTIFIER(:ORDERS_SRC))
  -- tool 2: Filter — wrong: NULL AMOUNT rows are dropped
  SELECT ACCT, AMOUNT FROM t1_input WHERE AMOUNT <> 0;
  RETURN 'OK';
END;
`;

/** A Snowpark segment's source of truth (spec §4.2): one public `run`, DataFrame API only.
 * `proc.sql` is NOT canned beside it — `render_snowpark.py` is the only thing that writes it. */
const GOOD_PY = (seg: string) => `"""Segment ${seg} — Snowpark Python procedure."""
from snowflake.snowpark.functions import col


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id) -> str:
    # tool 1: Input Data
    orders = session.table(f"{src_db}.{src_schema}.ORDERS")
    # tool 2: Filter — NULL evaluations go to the False branch
    kept = orders.filter((col("AMOUNT") != 0) | col("AMOUNT").is_null())
    kept.write.mode("overwrite").save_as_table("MIG_WORK.WF0001_${seg.toUpperCase()}_OUT")
    return "OK"
`;

/** What the fake `render_snowpark.py` writes, matching the real script's template shape. */
const RENDERED_SQL = (wf: string, seg: string, body: string) =>
  `CREATE OR REPLACE PROCEDURE MIG_WORK.${wf.toUpperCase().replace("_", "")}_${seg.toUpperCase()}(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)\n` +
  `RETURNS STRING\nLANGUAGE PYTHON\nRUNTIME_VERSION = '3.11'\nPACKAGES = ('snowflake-snowpark-python', 'pandas')\n` +
  `HANDLER = 'run'\nEXECUTE AS CALLER\nAS\n$$\n${body.replace(/\n+$/, "")}\n$$;\n`;

const CONTRACT = (seg: string, target: string | null = "sql", outputs: unknown[] = []) =>
  JSON.stringify(
    {
      segment: seg,
      // `target` is omitted entirely when null — the "analyzer forgot it" shape the verify
      // callback must refuse with `target-missing`.
      ...(target === null ? {} : { target }),
      inputs: [{ logical: "ORDERS", table: "MIG_GOLDEN_WF0001_NORMAL.ORDERS", columns: [], keys: ["ACCT"] }],
      output: { table: `MIG_WORK.WF0001_${seg.toUpperCase()}_OUT`, columns: ["ACCT", "AMOUNT"], keys: ["ACCT"] },
      outputs,
      row_relation: "filter",
      ordering: { keys: ["ACCT"], alteryx_deterministic: true },
      tolerances: {},
      parity_risks: [],
    },
    null,
    2,
  );

// ---------- the dbt project a dbt scenario replays (output targets phase 2, design §4.3) ----------
//
// `dbt`, `fix-loop:dbt`, `never-fixed:dbt`, `compile-fails:dbt`, `compile-crashes:dbt` and
// `needs-human:dbt` are dbt scenarios: the canned tree gains `canned/dbt/**` (what a translator
// writes for the whole workflow), `canned/review.json` (the reviewer's per-workflow verdict) and one
// broken model under `broken_sql/dbt/`. The contracts gain the outputs those models stand for:
// seg_01's work stream and seg_02's one final target, so exactly one segment owns the broken model.

/** A dbt workflow's scenario names `dbt` itself, or `dbt` in its segment position. */
export function isDbtScenario(scenario?: string): boolean {
  const [name, segment] = (scenario ?? "").split(":");
  return name === "dbt" || segment === "dbt";
}

/** What `samples/<wf>/broken_sql/dbt/models/orders_out.sql` holds — the fake validate_dbt.py FAILs
 * the segment that owns `orders_out` whenever the project's model is byte-for-byte this. */
export const BROKEN_DBT = "BROKEN_DBT";

const DBT_OUTPUTS: Record<string, unknown[]> = {
  seg_01: [{ kind: "work", table: "MIG_WORK.WF0001_SEG_01_OUT", stream: "2_T", tool_id: "2", columns: [] }],
  seg_02: [{ kind: "target", logical: "ORDERS_OUT", stream: "2_T", tool_id: "5" }],
};

/** `canned/dbt/<relative path>` → content; the runner tests reuse it to check the replay. */
export const CANNED_DBT: Record<string, string> = {
  "dbt_project.yml": "name: wf_0001\nprofile: alteryx_migration\nmodel-paths: [models]\nvars:\n  src_schema: null\n  tgt_schema: null\n",
  "profiles.yml": "# stands in for scripts/lib/dbt_project.py PROFILES_TEMPLATE\n",
  "README.md": "# wf_0001 dbt project\n",
  "translation_notes.md": "- Filter: NULL rows kept per Alteryx semantics\n",
  "models/sources.yml": "version: 2\nsources:\n  - name: src\n    schema: \"{{ var('src_schema') }}\"\n    tables:\n      - name: ORDERS\n",
  "models/schema.yml": "version: 2\nmodels:\n  - name: wf0001_seg_01_out\n  - name: orders_out\n",
  "models/wf0001_seg_01_out.sql":
    "{{ config(materialized='table') }}\n-- tool 1: Input Data\nwith t1_input as (select ACCT, AMOUNT from {{ source('src', 'ORDERS') }})\n" +
    "-- tool 2: Filter — NULL evaluations go to the False branch\nselect ACCT, AMOUNT from t1_input where not (AMOUNT = 0) or AMOUNT is null\n",
  "models/orders_out.sql":
    "{{ config(materialized='table', alias='ORDERS_OUT') }}\n-- tool 5: Output Data (overwrite, logical ORDERS_OUT)\n" +
    "select ACCT, AMOUNT from {{ ref('wf0001_seg_01_out') }}\n",
};

const temporaries: string[] = [];
let cleanupRegistered = false;

/** Temp roots are removed when the test process exits, whatever the outcome. */
function registerCleanup(): void {
  if (cleanupRegistered) return;
  cleanupRegistered = true;
  process.on("exit", () => {
    for (const dir of temporaries) {
      try {
        rmSync(dir, { recursive: true, force: true });
      } catch {
        /* a leftover temp dir is not worth failing a test run */
      }
    }
  });
}

export async function tempRoot(prefix = "orch-"): Promise<string> {
  registerCleanup();
  const dir = await mkdtemp(path.join(os.tmpdir(), prefix));
  temporaries.push(dir);
  return dir;
}

async function write(file: string, text: string): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, text, "utf8");
}

async function exists(file: string): Promise<boolean> {
  try {
    await stat(file);
    return true;
  } catch {
    return false;
  }
}

export interface FakeCalls {
  py: { script: string; args: string[]; inheritStdio: boolean }[];
  sh: { cmd: string; args: string[] }[];
  roles: Role[];
  /** Every task prompt an agent was given, so a test can assert which script a role was told to
   * run (the validator's is target-dependent: validate_segment.py vs validate_snowpark.py). */
  tasks: { role: Role; segment?: string; dbt?: boolean; batch?: { id: string; segments: string[] }; task: string }[];
  /** `py:<script>` and `agent:<role>` interleaved in the order they actually happened, so a test
   * can assert a script ran BEFORE an agent (target_check.py before the analyzer) rather than
   * only that both happened. */
  order: string[];
  sleeps: number[];
  logs: string[];
}

/** Which segments `target_check.py` proposes as `snowpark`, and what target the canned analyzer
 * writes into each contract — the two halves the lower-only verify rule compares. A scenario
 * names one segment; everything else stays `sql`. `null` omits `target` from the contract. */
interface TargetShape {
  proposed: Set<string>;
  contract: Record<string, string | null>;
  /** A segment `target_check.py` leaves out of `targets.json.segments` altogether. */
  omitted?: string;
  /** `target_check.py` exits 1 — `targets.json` IS written, the workflow just has unknown nodes. */
  unknownNodes: boolean;
}

function targetShape(scenario?: string): TargetShape {
  const [name, segment] = (scenario ?? "").split(":");
  const none: TargetShape = { proposed: new Set(), contract: {}, unknownNodes: name === "unknown-nodes" };
  if (!segment) return none;
  switch (name) {
    // The script proposes snowpark and the analyzer agrees: the ordinary Snowpark path.
    // render-fails / render-crashes are the same shape with a failing renderer.
    case "snowpark":
    case "render-fails":
    case "render-crashes":
      return { ...none, proposed: new Set([segment]), contract: { [segment]: "snowpark" } };
    // The script proposed snowpark; the analyzer wrote sql back — a RAISE (spec §3.2).
    case "target-raise":
      return { ...none, proposed: new Set([segment]), contract: { [segment]: "sql" } };
    // The script proposed sql; the analyzer lowered it to snowpark — allowed.
    case "target-lower":
      return { ...none, contract: { [segment]: "snowpark" } };
    // …all the way down to manual, which no generator may attempt.
    case "manual":
      return { ...none, contract: { [segment]: "manual" } };
    case "target-missing":
      return { ...none, contract: { [segment]: null } };
    // targets.json exists but says nothing about this segment: nothing to verify against.
    case "no-proposal":
      return { ...none, omitted: segment };
    default:
      return none;
  }
}

export interface FakeOptions {
  wf: string;
  scenario?: string;
  /** What the fake intake_prompt.py records in manifest.status.intake. */
  intake?: string;
  /** Errors to inject, one per call of that role, in order. `null` means "let this call run for
   * real" -- what a test needs to make attempt 1 fail VERIFY and attempt 2 fail before it. */
  agentErrors?: Partial<Record<Role, (AgentError | null)[]>>;
  interactive?: boolean;
  hasGh?: boolean;
  /** GitHub integration on for this run; off by default, as in the orchestrator (fix round 1, B3). */
  ghEnabled?: boolean;
  /** Merged into the seeded manifest.json on disk. */
  manifest?: Partial<Manifest>;
  config?: Partial<OrchestratorConfig>;
  /** "empty" makes the simulator produce no golden sets and exit 1. */
  golden?: "ok" | "empty";
  /** The fake segment.py writes a single wave [["seg_01"]] by default; true writes two waves,
   * [["seg_01"], ["seg_02"]], for tests of cross-wave translate behaviour (F11). */
  twoWaves?: boolean;
  /** One segment per wave, seg_01…seg_0N (at most three are canned); overrides `twoWaves`. */
  waves?: number;
}

export interface Fake {
  root: string;
  env: Env;
  calls: FakeCalls;
  files: {
    path(...rest: string[]): string;
    read(...rest: string[]): Promise<string | undefined>;
    exists(...rest: string[]): Promise<boolean>;
  };
  /** Makes the next intake_prompt.py run report READY, as an owner answering the questions. */
  answerIntake(): void;
  cleanup(): Promise<void>;
}

/** The canned artifacts a human would have written under samples/<wf>/. */
async function seedSamples(root: string, wf: string, shape: TargetShape, dbt = false): Promise<void> {
  const canned = (...rest: string[]) => path.join(root, "samples", wf, "canned", ...rest);
  const t3 = wf === "wf_0005";
  await write(canned("intake", "plan.md"), `# ${wf} plan\n\nTier: ${t3 ? "T3" : "T1"}. Segments: seg_01.\n`);
  await write(canned("analysis.md"), `# ${wf} analysis\n\nEvery tool classified.\n`);
  await write(
    canned("unsupported.json"),
    `${JSON.stringify({ tier: t3 ? "T3" : "T1", tools: t3 ? [{ tool_id: "77", type: "unknown" }] : [] }, null, 2)}\n`,
  );
  if (!t3) {
    // A T3 workflow is never assigned segments to translate at all (program spec: analyze
    // completes on the tier alone and translate goes straight to MANUAL) -- the real
    // samples/wf_0005/canned/ has no segments/ or broken_sql/ tree, and this fixture must not
    // invent one either, or it would mask exactly the defect this shape exists to catch.
    for (const seg of ["seg_01", "seg_02", "seg_03"]) {
      // seg_02 / seg_03 are only ever assigned (via order.json) when a test opts into `twoWaves` /
      // `waves: 3`; seeding their canned artifacts unconditionally is harmless for every other test,
      // which never reads them.
      const target = seg in shape.contract ? shape.contract[seg] : "sql";
      await write(canned("segments", seg, "contract.json"), `${CONTRACT(seg, target, dbt ? DBT_OUTPUTS[seg] : [])}\n`);
      // A Snowpark segment's canned artefact is proc.py; its proc.sql is rendered, never canned.
      if (target === "snowpark") await write(canned("segments", seg, "proc.py"), GOOD_PY(seg));
      else await write(canned("segments", seg, "proc.sql"), GOOD_SQL(wf, seg));
      await write(canned("segments", seg, "translation_notes.md"), "- Filter: NULL rows kept per Alteryx semantics\n");
      await write(canned("segments", seg, "review.json"), `${JSON.stringify({ verdict: "PASS", findings: [] }, null, 2)}\n`);
      await write(path.join(root, "samples", wf, "broken_sql", seg, "broken.sql"), BROKEN_SQL.replace(/SEG_01/g, seg.toUpperCase()));
    }
    // document is NOT_APPLICABLE_FOR_T3 (a T3 workflow never reaches it), and the real
    // samples/wf_0005/canned/ has no docs/ tree at all -- this fixture must not invent one
    // either, for the same reason it invents no segments/ tree above.
    await write(canned("docs", "migration.md"), `# ${wf} migration\n\nOverview, mappings, runbook.\n`);
    if (dbt) {
      for (const [rel, text] of Object.entries(CANNED_DBT)) await write(canned("dbt", ...rel.split("/")), text);
      await write(canned("review.json"), `${JSON.stringify({ verdict: "PASS", findings: [] }, null, 2)}\n`);
      await write(path.join(root, "samples", wf, "broken_sql", "dbt", "models", "orders_out.sql"), BROKEN_DBT);
    }
  }
  if (t3) {
    await write(
      canned("parser-recovery", "scripts", "parsers", "ext", "vendor_tool.py"),
      '"""Extension registering the Vendor.CustomTool plugin."""\n',
    );
    await write(canned("parser-recovery", "tests", "parser_corpus", "vendor_tool", "fragment.xml"), "<Node/>\n");
    await write(canned("parser-recovery", "parsed", "parse_diagnosis.md"), "Unknown plugin Vendor.CustomTool.\n");
  }
}

/** Seeds one more workflow (source + samples + a manifest.json) into a root a Fake already
 * owns — for tests that need more than one workflow directory, e.g. proving that a corrupt
 * manifest for one workflow does not stop another workflow in the same run (F12). */
export async function seedWorkflow(
  root: string,
  wf: string,
  manifest?: Partial<Manifest>,
  scenario?: string,
): Promise<void> {
  await seedSamples(root, wf, targetShape(scenario), isDbtScenario(scenario));
  await write(path.join(root, "workflows", wf, "source", `${wf}.yxmd`), "<AlteryxDocument yxmdVer=\"2023.1\"/>\n");
  await writeJson(path.join(root, "workflows", wf, "manifest.json"), { id: wf, status: {}, metrics: {}, ...(manifest ?? {}) });
}

export async function makeEnv(options: FakeOptions): Promise<Fake> {
  const wf = options.wf;
  const root = await tempRoot();
  const calls: FakeCalls = { py: [], sh: [], roles: [], tasks: [], order: [], sleeps: [], logs: [] };
  const state = { intake: options.intake ?? "READY" };
  const shape = targetShape(options.scenario);

  await seedWorkflow(root, wf, options.manifest, options.scenario);

  const config: OrchestratorConfig = { ...DEFAULT_CONFIG, ...(options.config ?? {}) };
  const runner = new MockRunner(root, path.join(root, "samples"), options.scenario);
  const scenarioSegment = (kind: string): string | undefined => {
    const [name, segment] = (options.scenario ?? "").split(":");
    return name === kind ? segment : undefined;
  };

  const result = (code: number, out = "", err = ""): ShResult => ({ ok: code === 0, code, out, err });

  /** Both validators write the same report shape; only `target` and the artefact they read
   * differ (`validate_segment.py` drives proc.sql, `validate_snowpark.py` drives proc.py). */
  const fakeValidate = async (id: string, seg: string, target: "sql" | "snowpark"): Promise<ShResult> => {
    const artefact = target === "snowpark" ? "proc.py" : "proc.sql";
    const current = await readFile(wfDir(root, id, "segments", seg, artefact), "utf8").catch(() => "");
    const brokenDir = path.join(root, "samples", id, "broken_sql", seg);
    const broken = await readFile(path.join(brokenDir, target === "snowpark" ? "broken.py" : "broken.sql"), "utf8")
      .catch(() => " ");
    const needsHuman = scenarioSegment("needs-human") === seg;
    // A report that PASSES (with an accepted diff) but still says needs_human: true -- the
    // shape ruling 4 (task-15-int brief) exists for: the fixer loop must not treat this as a
    // green light just because the verdict starts with PASS.
    const needsHumanPass = scenarioSegment("needs-human-pass") === seg;
    const fail = needsHuman || current === broken;
    const verdict = needsHumanPass ? "PASS_WITH_ACCEPTED_DIFF" : fail ? "FAIL" : "PASS";
    await writeJson(wfDir(root, id, "segments", seg, "validation.json"), {
      segment: seg,
      target,
      golden_set: "normal",
      verdict,
      checks: { schema: "PASS" },
      diff_clusters: (fail || needsHumanPass) ? [{ class: "NULL_SEMANTICS", columns: ["AMOUNT"], count: 2, suspect_cte: "t2_filter" }] : [],
      idempotent: true,
      needs_human: needsHuman || needsHumanPass,
    });
    return result(needsHumanPass ? 0 : (fail ? 1 : 0));
  };

  /** `compile_check.py <wf> --target dbt` (no segment, DV6): exit 2 with no report when there is
   * no project or on `compile-crashes:dbt` (the real script writes nothing on a crash), else
   * `dbt/compile_check.json` written — `ERROR` and exit 1 on `compile-fails:dbt`, `OK` and 0. */
  const fakeCompileDbt = async (id: string): Promise<ShResult> => {
    if (!(await exists(wfDir(root, id, "dbt", "dbt_project.yml")))) {
      return result(2, "", `cannot compile-check ${id}: ${id} has no dbt project to check`);
    }
    if (scenarioSegment("compile-crashes") === "dbt") return result(2, "", "Traceback (most recent call last): dbt parse crashed");
    const fails = scenarioSegment("compile-fails") === "dbt";
    const errors = fails
      ? ["dbt:model_config: models/orders_out.sql has materialized='view'; the overwrite write mode needs materialized='table'"]
      : [];
    await writeJson(wfDir(root, id, "dbt", "compile_check.json"), {
      status: fails ? "ERROR" : "OK",
      target: "dbt",
      errors,
      statements: 0,
      models: 2,
    });
    return fails ? result(1, "ERROR: 0 statements, 1 errors", `  ${errors[0]}`) : result(0, "OK: 0 statements, 0 errors");
  };

  /** `validate_dbt.py <wf>`: one report per segment of order.json, `"target": "dbt"`. The segment
   * whose contract owns `ORDERS_OUT` FAILs while `models/orders_out.sql` is the broken variant (or,
   * on `needs-human:dbt`, FAILs with `needs_human: true`); every other segment PASSes, as it does
   * when a real project's last model alone is wrong. Exit 1 on any FAIL, 2 with no project. */
  const fakeValidateDbt = async (id: string): Promise<ShResult> => {
    if (!(await exists(wfDir(root, id, "dbt", "dbt_project.yml")))) {
      return result(2, "", `validate_dbt.py: error: ${id} has no dbt project`);
    }
    const model = await readFile(wfDir(root, id, "dbt", "models", "orders_out.sql"), "utf8").catch(() => "");
    const needsHuman = scenarioSegment("needs-human") === "dbt";
    // A PASS (with an accepted diff) that still says needs_human: true — ruling 4's shape, per project.
    const needsHumanPass = scenarioSegment("needs-human-pass") === "dbt";
    const order = await readJsonOr<string[][]>(wfDir(root, id, "segments", "order.json"), []);
    let failed = false;
    for (const seg of order.flat()) {
      const contract = await readJsonOr<{ outputs?: { logical?: string }[] }>(
        wfDir(root, id, "segments", seg, "contract.json"),
        {},
      );
      const owns = (contract.outputs ?? []).some((output) => output.logical === "ORDERS_OUT");
      const fail = owns && (needsHuman || model === BROKEN_DBT);
      const accepted = owns && needsHumanPass;
      failed ||= fail;
      await writeJson(wfDir(root, id, "segments", seg, "validation.json"), {
        segment: seg,
        target: "dbt",
        verdict: fail ? "FAIL" : accepted ? "PASS_WITH_ACCEPTED_DIFF" : "PASS",
        needs_human: (fail && needsHuman) || accepted,
        diff_clusters: fail || accepted ? [{ class: "LOGIC", columns: ["AMOUNT"], count: 2, stream: "2_T" }] : [],
        idempotent: true,
      });
    }
    // Task W1: the same run writes the workflow's chain report (the chain IS the full dbt run).
    // `chain-fail:dbt` makes it FAIL while every segment's own report PASSes; `chain-needs-human:dbt`
    // (final fix wave N-dbt) makes it a PASS that still says needs_human.
    const chainFails = failed || scenarioSegment("chain-fail") === "dbt";
    await writeJson(wfDir(root, id, "validation_workflow.json"), {
      workflow: id,
      target: "dbt",
      verdict: chainFails ? "FAIL" : "PASS",
      needs_human: scenarioSegment("chain-needs-human") === "dbt",
      divergence_kind: chainFails ? "chain_drift" : null,
      first_divergence: chainFails ? { segment: "seg_02", stream: "2_T", output: "ORDERS_OUT", set: "normal" } : null,
    });
    return result(failed ? 1 : 0, "", failed ? "FAIL" : "");
  };

  /** `validate_workflow.py <wf>` (Task W1): the chain report at the workflow root, PASS by default.
   * `chain-boundary:<seg>` FAILs with a boundary divergence at `<seg>/2_T` on the FIRST call and
   * PASSes after (the one fixer round repaired it); `chain-boundary-stuck:<seg>` FAILs every call;
   * `chain-drift` FAILs with `chain_drift` at the final `ORDERS_OUT`; `chain-crash` exits 2;
   * `chain-needs-human` exits 0 with `needs_human: true`. */
  let chainCalls = 0;
  const fakeValidateWorkflow = async (id: string): Promise<ShResult> => {
    chainCalls += 1;
    const [name, segment] = (options.scenario ?? "").split(":");
    if (name === "chain-crash") return result(2, "", "Traceback (most recent call last): validate_workflow.py crashed");
    const boundary = (name === "chain-boundary" && chainCalls === 1) || name === "chain-boundary-stuck";
    const drift = name === "chain-drift";
    // `chain-needs-human`: exit 0 on an accepted difference whose report still says needs_human.
    const needsHuman = name === "chain-needs-human";
    const verdict = boundary || drift ? "FAIL" : needsHuman ? "PASS_WITH_ACCEPTED_DIFF" : "PASS";
    await writeJson(wfDir(root, id, "validation_workflow.json"), {
      workflow: id,
      verdict,
      needs_human: needsHuman,
      boundaries: [],
      finals: [],
      divergence_kind: boundary ? "boundary" : drift ? "chain_drift" : null,
      first_divergence: boundary
        ? { segment, stream: "2_T", output: `MIG_WORK.WF0001_${String(segment).toUpperCase()}_OUT`, set: "normal" }
        : drift
          ? { segment: "seg_02", stream: "2_T", output: "ORDERS_OUT", set: "normal" }
          : null,
    });
    return result(verdict.startsWith("PASS") ? 0 : 1, `${id}: ${verdict}`);
  };

  /** `plan_batches.py <wf> --budget-chars N` (Task W2): ONE batch holding every wave, unless the
   * scenario is `batched` or `stitch-fails`, which plan one batch per wave (two with `twoWaves`). */
  const fakePlanBatches = async (id: string, args: string[]): Promise<ShResult> => {
    const budgetAt = args.indexOf("--budget-chars");
    const order = await readJsonOr<string[][]>(wfDir(root, id, "segments", "order.json"), []);
    const perWave = options.scenario === "batched" || options.scenario === "stitch-fails";
    const groups = perWave ? order.map((_, w) => [w]) : order.length ? [order.map((_, w) => w)] : [];
    const batches = groups.map((waves, n) => ({
      id: `batch_${String(n + 1).padStart(2, "0")}`,
      waves,
      segments: waves.flatMap((w) => order[w]),
      estimate_chars: 0,
    }));
    await writeJson(wfDir(root, id, "segments", "batches.json"), {
      budget_chars: Number(args[budgetAt + 1]),
      estimate_note: "characters of the rendered context; tokens are roughly characters / 4",
      batches,
      warnings: [],
    });
    return result(0, `${id}: ${batches.length} batch(es)`);
  };

  /** `check_seams.py <wf> [--segments a,b]` (Task W2): `segments/seams.json` written every call;
   * `seam-mismatch:<seg>` makes the seam into `<seg>` a mismatch (exit 1) whenever `<seg>` is in
   * scope -- every segment without `--segments`, only the listed ones with it. */
  const fakeCheckSeams = async (id: string, args: string[]): Promise<ShResult> => {
    const segmentsAt = args.indexOf("--segments");
    const scope = segmentsAt >= 0 ? args[segmentsAt + 1].split(",") : undefined;
    const consumer = scenarioSegment("seam-mismatch");
    const failing = consumer !== undefined && (scope === undefined || scope.includes(consumer));
    const problem = "AMOUNT: FLOAT (float) produced, NUMBER(38,0) (number) consumed";
    const seams = failing
      ? [{ producer: "seg_01", consumer, stream: "2_T", table: "MIG_WORK.WF0001_SEG_01_OUT", status: "mismatch", problems: [problem] }]
      : [];
    await writeJson(wfDir(root, id, "segments", "seams.json"), { ok: !failing, seams, duplicates: [] });
    return failing
      ? result(1, "", `seam-mismatch: seg_01->${consumer} 2_T\n  ${problem}`)
      : result(0, `${id}: every seam agrees`);
  };

  /** `stitch_analysis.py <wf>` (Task W2): `analysis.md` from every batch's fragment in order and
   * `unsupported.json` with the highest fragment tier; exit 1 on `stitch-fails` or a missing
   * fragment, writing nothing. */
  const fakeStitch = async (id: string): Promise<ShResult> => {
    if (options.scenario === "stitch-fails") return result(1, "", "stitch: seg_02 is in batch_01 and batch_02");
    const plan = await readJsonOr<{ batches?: { id: string; segments: string[] }[] }>(
      wfDir(root, id, "segments", "batches.json"),
      {},
    );
    const batches = plan.batches ?? [];
    const rank = ["T1", "T2", "T3"];
    let tier = "T1";
    let text = `# ${id} analysis (stitched from ${batches.length} batches by scripts/stitch_analysis.py)\n`;
    for (const batch of batches) {
      const fragment = await readFile(wfDir(root, id, "analysis", `${batch.id}.md`), "utf8").catch(() => undefined);
      if (fragment === undefined) return result(1, "", `stitch: analysis/${batch.id}.md is missing`);
      text += `\n## ${batch.id}: segments ${batch.segments.join(", ")}\n\n${fragment}`;
      const part = await readJsonOr<{ tier?: string }>(wfDir(root, id, "analysis", `${batch.id}.unsupported.json`), {});
      if (rank.indexOf(part.tier ?? "") > rank.indexOf(tier)) tier = part.tier!;
    }
    await write(wfDir(root, id, "analysis.md"), text);
    await writeJson(wfDir(root, id, "unsupported.json"), { tier, unsupported: [], unknown: [] });
    return result(0, `${id}: stitched ${batches.length} batches`);
  };

  const py = async (script: string, args: string[], opts?: { inheritStdio?: boolean }): Promise<ShResult> => {
    calls.py.push({ script, args, inheritStdio: Boolean(opts?.inheritStdio) });
    calls.order.push(`py:${script}`);
    const name = script.split("/").pop() ?? script;
    const id = args[0] ?? wf;
    const manifestFile = path.join(root, "workflows", id, "manifest.json");
    const patchManifest = async (patch: (m: Manifest) => void): Promise<void> => {
      const manifest = await loadManifest(root, id);
      patch(manifest);
      await writeJson(manifestFile, manifest);
    };

    switch (name) {
      case "parse.py": {
        // wf_0005 only parses once a recovery extension exists (the vendor plugin case).
        const recovered = await exists(path.join(root, "scripts", "parsers", "ext", "vendor_tool.py"));
        const ok = id !== "wf_0005" || recovered;
        await writeJson(wfDir(root, id, "parsed", "parse_report.json"), {
          status: ok ? (recovered ? "RECOVERED" : "PARSED") : "FAILED",
          errors: ok ? [] : ["unknown Plugin name: Vendor.CustomTool"],
          node_count: 4,
          extension: recovered ? "vendor_tool" : null,
        });
        return result(ok ? 0 : 1, "", ok ? "" : "parse failed: unknown Plugin name");
      }
      case "intake_touchpoints.py":
        await writeJson(wfDir(root, id, "intake", "touchpoints.json"), { inputs: [], outputs: [] });
        return result(0);
      case "intake_prompt.py": {
        const status = state.intake;
        await patchManifest((m) => {
          m.status.intake = status;
        });
        await write(
          wfDir(root, id, "intake", "open_questions.md"),
          status === "READY" ? "- [x] Tool 1 Input → SALES.RAW.ORDERS\n" : "- [ ] Tool 1 Input → ?\n",
        );
        return result(status === "READY" ? 0 : 1, "", status === "READY" ? "" : `intake ${status}`);
      }
      case "segment.py": {
        const count = options.waves ?? (options.twoWaves ? 2 : 1);
        const segments = Array.from({ length: count }, (_, i) => `seg_${String(i + 1).padStart(2, "0")}`);
        await writeJson(wfDir(root, id, "segments", "order.json"), segments.map((seg) => [seg]));
        await patchManifest((m) => {
          m.segments = segments;
        });
        return result(0);
      }
      case "alteryx_sim.py": {
        const empty = options.golden === "empty";
        await patchManifest((m) => {
          m.golden_sets = empty ? [] : ["normal", "period_end", "empty", "edge"];
        });
        return result(empty ? 1 : 0, "", empty ? "no simulator support for this workflow" : "");
      }
      case "target_check.py": {
        // Faithful to scripts/target_check.py's own `--prefer auto` resolution (spec §3.3):
        // manifest.output_target first, then mappings/global.yaml, then "procedures".
        const preferAt = args.indexOf("--prefer");
        const prefer = preferAt >= 0 ? args[preferAt + 1] : "auto";
        const manifest = await loadManifest(root, id);
        const preference = prefer === "auto" ? ((manifest.output_target as string | undefined) ?? "procedures") : prefer;
        const order = await readJsonOr<string[][]>(wfDir(root, id, "segments", "order.json"), []);
        const segs = order.flat().filter((seg) => seg !== shape.omitted);
        const segments = Object.fromEntries(segs.map((seg) => [seg, shape.proposed.has(seg) ? "snowpark" : "sql"]));
        const blockers = segs
          .filter((seg) => segments[seg] === "snowpark")
          .map((seg) => ({ segment: seg, kind: "snowpark_segment" }));
        const outputKind = preference === "dbt" && blockers.length === 0 ? "dbt" : "procedures";
        await writeJson(wfDir(root, id, "segments", "targets.json"), {
          preference,
          output_kind: outputKind,
          reason:
            outputKind === "dbt"
              ? "preference dbt"
              : preference === "dbt"
                ? `dbt refused: ${blockers.map((b) => `${b.segment} target is snowpark`).join("; ")}`
                : "preference procedures",
          dbt_blockers: blockers,
          segments,
          // Exit 1 is NOT a refusal to write: targets.json is complete, the workflow simply has a
          // node the class table does not know, which is the analyzer's problem to record.
          nodes: shape.unknownNodes ? { "7": "unknown" } : {},
        });
        return shape.unknownNodes
          ? result(1, `${id}: output_kind=${outputKind}`, `${id}: 1 unknown node`)
          : result(0, `${id}: output_kind=${outputKind}`);
      }
      case "render_snowpark.py": {
        const seg = args[1];
        // Exit 2 is a broken invocation; exit 1 is the script's one domain failure (`$$` in the
        // source). Both are exercised, because the orchestrator routes them differently.
        if (scenarioSegment("render-crashes") === seg) return result(2, "", "usage: render_snowpark.py <wf_id> <seg>");
        if (scenarioSegment("render-fails") === seg) {
          return result(1, "", "proc.py must not contain `$$` (it would end the procedure body)");
        }
        const body = await readFile(wfDir(root, id, "segments", seg, "proc.py"), "utf8").catch(() => undefined);
        if (body === undefined) return result(2, "", `render_snowpark: no proc.py for ${id} ${seg}`);
        await write(wfDir(root, id, "segments", seg, "proc.sql"), RENDERED_SQL(id, seg, body));
        return result(0);
      }
      case "compile_check.py": {
        const targetAt = args.indexOf("--target");
        if (targetAt >= 0 && args[targetAt + 1] === "dbt") return await fakeCompileDbt(id);
        return scenarioSegment("compile-fails") === args[1]
          ? result(1, "", "compile error near line 6")
          : result(0, "compile OK");
      }
      case "validate_dbt.py":
        return await fakeValidateDbt(id);
      case "validate_workflow.py":
        return await fakeValidateWorkflow(id);
      case "validate_segment.py":
        return await fakeValidate(id, args[1], "sql");
      case "validate_snowpark.py":
        return await fakeValidate(id, args[1], "snowpark");
      case "prompt_context.py": {
        // Task F: exit 2 on scenario `context-fails` (a broken renderer -- the stage must still
        // run its agent, just without the inline block); otherwise a fixed, recognisable stand-in
        // for the real Markdown, naming the role (and, Task W2, the batch) it was asked for.
        const role = args[1] === "--role" ? args[2] : "?";
        const batchAt = args.indexOf("--batch");
        const batch = batchAt >= 0 ? ` ${args[batchAt + 1]}` : "";
        if (options.scenario === "context-fails") {
          return result(2, "", "prompt_context: crashed");
        }
        return result(0, `## Inline context for ${role}${batch} (fake)\n- tool 1 input`);
      }
      case "plan_batches.py":
        return await fakePlanBatches(id, args);
      case "check_seams.py":
        return await fakeCheckSeams(id, args);
      case "stitch_analysis.py":
        return await fakeStitch(id);
      default:
        return result(2, "", `fake py has no script ${script}`);
    }
  };

  const errors = options.agentErrors ?? {};
  const recording: AgentRunner = {
    async run(role, manifest, task, ctx): Promise<AgentResult> {
      calls.roles.push(role);
      calls.tasks.push({ role, segment: ctx?.segment, dbt: ctx?.dbt, batch: ctx?.batch, task });
      calls.order.push(`agent:${role}`);
      const queued = errors[role]?.shift();
      if (queued) return { ok: false, error: queued, detail: `injected ${queued}`, toolCalls: 0, ms: 0 };
      return runner.run(role, manifest, task, ctx);
    },
  };

  const env: Env = {
    root,
    config,
    runner: recording,
    interactive: options.interactive ?? false,
    hasGh: options.hasGh ?? false,
    ghEnabled: options.ghEnabled ?? false,
    py,
    sh: async (cmd, args) => {
      calls.sh.push({ cmd, args });
      return env.hasGh ? result(0, "https://example.invalid/pr/1") : result(127, "", `${cmd}: not found`);
    },
    sleep: async (ms) => {
      calls.sleeps.push(ms);
    },
    log: (line) => calls.logs.push(line),
  };

  runner.attach(env);

  return {
    root,
    env,
    calls,
    files: {
      path: (...rest) => path.join(root, ...rest),
      read: (...rest) => readFile(path.join(root, ...rest), "utf8").then((t) => t, () => undefined),
      exists: (...rest) => exists(path.join(root, ...rest)),
    },
    answerIntake: () => {
      state.intake = "READY";
    },
    cleanup: () => rm(root, { recursive: true, force: true }),
  };
}
