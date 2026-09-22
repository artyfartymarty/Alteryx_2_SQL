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
import { loadManifest, wfDir, writeJson } from "../manifest.ts";
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
  CREATE OR REPLACE TABLE MIG_WORK.${wf.toUpperCase().replace("_", "")}_${seg.toUpperCase()}_OUT AS
  WITH t1_input AS (SELECT ACCT, AMOUNT FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS'))
  -- tool 2: Filter — NULL evaluations go to the False branch
  SELECT ACCT, AMOUNT FROM t1_input WHERE NOT (AMOUNT = 0) OR AMOUNT IS NULL;
  RETURN 'OK';
END;
`;

const BROKEN_SQL = `-- tool 1: Input Data
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0001_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
BEGIN
  CREATE OR REPLACE TABLE MIG_WORK.WF0001_SEG_01_OUT AS
  WITH t1_input AS (SELECT ACCT, AMOUNT FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS'))
  -- tool 2: Filter — wrong: NULL AMOUNT rows are dropped
  SELECT ACCT, AMOUNT FROM t1_input WHERE AMOUNT <> 0;
  RETURN 'OK';
END;
`;

const CONTRACT = (seg: string) =>
  JSON.stringify(
    {
      segment: seg,
      inputs: [{ logical: "ORDERS", table: "MIG_GOLDEN_WF0001_NORMAL.ORDERS", columns: [], keys: ["ACCT"] }],
      output: { table: `MIG_WORK.WF0001_${seg.toUpperCase()}_OUT`, columns: ["ACCT", "AMOUNT"], keys: ["ACCT"] },
      outputs: [],
      row_relation: "filter",
      ordering: { keys: ["ACCT"], alteryx_deterministic: true },
      tolerances: {},
      parity_risks: [],
    },
    null,
    2,
  );

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
  sleeps: number[];
  logs: string[];
}

export interface FakeOptions {
  wf: string;
  scenario?: string;
  /** What the fake intake_prompt.py records in manifest.status.intake. */
  intake?: string;
  agentErrors?: Partial<Record<Role, AgentError[]>>;
  interactive?: boolean;
  hasGh?: boolean;
  /** Merged into the seeded manifest.json on disk. */
  manifest?: Partial<Manifest>;
  config?: Partial<OrchestratorConfig>;
  /** "empty" makes the simulator produce no golden sets and exit 1. */
  golden?: "ok" | "empty";
  /** The fake segment.py writes a single wave [["seg_01"]] by default; true writes two waves,
   * [["seg_01"], ["seg_02"]], for tests of cross-wave translate behaviour (F11). */
  twoWaves?: boolean;
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
async function seedSamples(root: string, wf: string): Promise<void> {
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
    for (const seg of ["seg_01", "seg_02"]) {
      // seg_02 is only ever assigned (via order.json) when a test opts into `twoWaves`; seeding
      // its canned artifacts unconditionally is harmless for every single-wave test, which never
      // reads them.
      await write(canned("segments", seg, "contract.json"), `${CONTRACT(seg)}\n`);
      await write(canned("segments", seg, "proc.sql"), GOOD_SQL(wf, seg));
      await write(canned("segments", seg, "translation_notes.md"), "- Filter: NULL rows kept per Alteryx semantics\n");
      await write(canned("segments", seg, "review.json"), `${JSON.stringify({ verdict: "PASS", findings: [] }, null, 2)}\n`);
      await write(path.join(root, "samples", wf, "broken_sql", seg, "broken.sql"), BROKEN_SQL.replace(/SEG_01/g, seg.toUpperCase()));
    }
    // document is NOT_APPLICABLE_FOR_T3 (a T3 workflow never reaches it), and the real
    // samples/wf_0005/canned/ has no docs/ tree at all -- this fixture must not invent one
    // either, for the same reason it invents no segments/ tree above.
    await write(canned("docs", "migration.md"), `# ${wf} migration\n\nOverview, mappings, runbook.\n`);
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
export async function seedWorkflow(root: string, wf: string, manifest?: Partial<Manifest>): Promise<void> {
  await seedSamples(root, wf);
  await write(path.join(root, "workflows", wf, "source", `${wf}.yxmd`), "<AlteryxDocument yxmdVer=\"2023.1\"/>\n");
  await writeJson(path.join(root, "workflows", wf, "manifest.json"), { id: wf, status: {}, metrics: {}, ...(manifest ?? {}) });
}

export async function makeEnv(options: FakeOptions): Promise<Fake> {
  const wf = options.wf;
  const root = await tempRoot();
  const calls: FakeCalls = { py: [], sh: [], roles: [], sleeps: [], logs: [] };
  const state = { intake: options.intake ?? "READY" };

  await seedWorkflow(root, wf, options.manifest);

  const config: OrchestratorConfig = { ...DEFAULT_CONFIG, ...(options.config ?? {}) };
  const runner = new MockRunner(root, path.join(root, "samples"), options.scenario);
  const scenarioSegment = (kind: string): string | undefined => {
    const [name, segment] = (options.scenario ?? "").split(":");
    return name === kind ? segment : undefined;
  };

  const result = (code: number, out = "", err = ""): ShResult => ({ ok: code === 0, code, out, err });

  const py = async (script: string, args: string[], opts?: { inheritStdio?: boolean }): Promise<ShResult> => {
    calls.py.push({ script, args, inheritStdio: Boolean(opts?.inheritStdio) });
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
        const segments = options.twoWaves ? ["seg_01", "seg_02"] : ["seg_01"];
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
      case "compile_check.py":
        return scenarioSegment("compile-fails") === args[1]
          ? result(1, "", "compile error near line 6")
          : result(0, "compile OK");
      case "validate_segment.py": {
        const seg = args[1];
        const current = await readFile(wfDir(root, id, "segments", seg, "proc.sql"), "utf8").catch(() => "");
        const broken = await readFile(path.join(root, "samples", id, "broken_sql", seg, "broken.sql"), "utf8").catch(() => " ");
        const needsHuman = scenarioSegment("needs-human") === seg;
        // A report that PASSES (with an accepted diff) but still says needs_human: true -- the
        // shape ruling 4 (task-15-int brief) exists for: the fixer loop must not treat this as a
        // green light just because the verdict starts with PASS.
        const needsHumanPass = scenarioSegment("needs-human-pass") === seg;
        const fail = needsHuman || current === broken;
        const verdict = needsHumanPass ? "PASS_WITH_ACCEPTED_DIFF" : fail ? "FAIL" : "PASS";
        await writeJson(wfDir(root, id, "segments", seg, "validation.json"), {
          segment: seg,
          golden_set: "normal",
          verdict,
          checks: { schema: "PASS" },
          diff_clusters: (fail || needsHumanPass) ? [{ class: "NULL_SEMANTICS", columns: ["AMOUNT"], count: 2, suspect_cte: "t2_filter" }] : [],
          idempotent: true,
          needs_human: needsHuman || needsHumanPass,
        });
        return result(needsHumanPass ? 0 : (fail ? 1 : 0));
      }
      default:
        return result(2, "", `fake py has no script ${script}`);
    }
  };

  const errors = options.agentErrors ?? {};
  const recording: AgentRunner = {
    async run(role, manifest, task, ctx): Promise<AgentResult> {
      calls.roles.push(role);
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
