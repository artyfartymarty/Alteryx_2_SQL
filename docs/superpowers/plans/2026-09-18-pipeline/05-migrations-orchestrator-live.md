# Phase 05 — Hand migrations, validation driver, orchestrator, live test

Read [00-index.md](00-index.md) first. Contracts C3, C4 and C5 matter most here.

---

### Task 13: Hand migrations and canned agent artifacts

These are the "sample output SQL files": the procedures a good translator would produce, plus deliberately broken ones that prove `compare.py` catches what it should. They double as the mock runner's canned outputs.

**Files (per `wf_0001`…`wf_0004`):** `samples/<wf>/canned/intake/plan.md`, `canned/analysis.md`, `canned/unsupported.json`, `canned/segments/<seg>/{contract.json,proc.sql,translation_notes.md,review.json}`, `canned/docs/migration.md`, `samples/<wf>/broken_sql/<seg>/<name>.sql`, `samples/<wf>/broken_sql/broken.json`. For `wf_0005`: `canned/intake/plan.md`, `canned/analysis.md`, `canned/unsupported.json` (tier T3), and `canned/parser-recovery/{scripts/parsers/ext/acme_dedupe.py, tests/parser_corpus/acme_dedupe/{fragment.yxmd,test_acme_dedupe.py}, parsed/parse_diagnosis.md}`. Test: `tests/test_canned_artifacts.py`.

**Rules.** Procedures obey C4 and the repo instructions: one CTE per tool `t<id>_<type>` with a `-- tool <id>: …` comment, logical names through `IDENTIFIER`, `MIG_WORK` literals for segment tables, upper-case column aliases. Every order-dependent tool has an explicit `ORDER BY` from `contract.ordering.keys`. `ToNumber` → `TRY_TO_NUMBER`/`TRY_TO_DOUBLE`; `String(n)` → `LEFT(x, n)`; Filter false branch → `WHERE NOT (…) OR (…) IS NULL`; `Round(x, 0.01)` on a FLOAT goes through `ROUND(CAST(x AS NUMBER(38,10)), 2)` so halves behave as in the model; `Update; Insert if new` → `MERGE` by name on the confirmed keys with PreSQL and PostSQL as separate statements before and after it; the embedded input SQL's `WHERE` is re-applied in `t1_input`. `contract.json` follows C5; `translation_notes.md` lists one assumption per line; `review.json` is `{"verdict": "PASS", "findings": []}`.

`broken.json` rows: `{"segment", "file", "golden_set", "expect": {"class", "columns", "stream"}}`. Required variants, at least: wf_0001 — false branch as `WHERE REGION = 'WEST'` (`NULL_SEMANTICS`, `REGION`, stream `3_F`, which feeds Output tool 8) · `CUSTOMER` without `LEFT` (`TRUNCATION`) · `ROUND` on the raw FLOAT (`ROUNDING`, `TOTAL_NET`); wf_0002 — `L` branch dropped (`LOGIC`, rows missing) · cleansing without `UPPER` (`LOGIC`, hint `case_only`); wf_0003 — Unique ordered by `ENTRY_ID ASC` (`LOGIC`, `TOTAL`) · PostSQL omitted (`NULL_SEMANTICS`, `LOADED_FLAG`); wf_0004 — macro's `MinQty` filter dropped (`LOGIC`).

- [ ] **Step 1:** Write `tests/test_canned_artifacts.py`: every `proc.sql` passes `parse_proc`, has the C4 parameter list, contains one `t<id>_` CTE per data node of its segment dag (derive the segment dags with `build_samples.build` into `tmp_path`), contains no catalog table name from `catalog/columns.csv`, and every `contract.json` has the C5 keys. Expected: FAIL.
- [ ] **Step 2:** Write the artifacts, iterating each procedure against Task 14's driver until every golden set passes. When the SQL and the simulator disagree, find out which one contradicts program spec §8 and fix that one; never bend a golden file to fit the SQL.
- [ ] **Step 3:** Run the test. Expected: PASS.
- [ ] **Step 4:** Commit `feat: hand-migrated sample procedures, contracts, broken variants and canned agent outputs`.

---

### Task 14: `validate_segment.py` and end-to-end parity

**Files:** Create `scripts/validate_segment.py`; Tests `tests/test_validate_segment.py`, `tests/test_e2e_parity.py` (marker `e2e`), `tests/helpers.py`.

**Interfaces — Produces:**
```python
def validate_segment(repo: Repo, wf_id: str, seg: str, golden_sets: Sequence[str] | None = None, *, proc_path: Path | None = None) -> dict
# tests/helpers.py
def prepare_workflow(tmp_path: Path, wf_id: str) -> Repo    # build sample → intake with sample.json answers → copy canned contracts and procs
```
CLI: `python scripts/validate_segment.py <wf> <seg> [--set NAME]… [--proc FILE] [--root .]`; exit 0 pass, 1 fail, 2 error.

Per golden set, in a fresh in-memory `DuckDBBackend`: `load_set`; for each `contract.inputs[]` with `from` a segment, `load_intermediate` of its `stream` into its literal `table`; `run_proc` with `load_set(...)["args"]` plus `RUN_ID`; for every `contract.outputs[]`, compare against `golden/intermediates/<seg>/<set>/<stream>.csv` (`work`) or `golden/outputs/<set>/<tool_id>.csv` (`target`, actual table `MIGDB.MIG_WORK.<logical>`) with `segment_dag`, `tolerances` from `global.yaml`, `accepted_classes` and `manifest.accepted_diffs`. The first set is run twice from the same starting state; `idempotent` is whether both runs' outputs are equal as row multisets. `ProcError`/`BackendError` become `{"verdict": "FAIL", "error": "<message>", "diff_clusters": []}` (program spec: a compile or runtime error counts as FAIL and feeds the fixer). Writes `segments/<seg>/validation.<set>.json` and `validation.json` = the first non-passing set's report, else the last set's, plus `"sets": {"normal": "PASS", …}`, `idempotent`, `runtime_ms`, `credits: null`. Each cluster gains `"stream"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_e2e_parity.py
import json
from pathlib import Path
import pytest
from lib import io
from validate_segment import validate_segment
from tests.helpers import prepare_workflow
SAMPLES = Path(__file__).parents[1] / "samples"
pytestmark = pytest.mark.e2e

@pytest.mark.parametrize("wf", ["wf_0001", "wf_0002", "wf_0003", "wf_0004"])
def test_hand_migration_passes_every_golden_set(tmp_path, wf):
    repo = prepare_workflow(tmp_path, wf)
    for wave in io.read_json(repo.wf(wf, "segments", "order.json")):
        for seg in wave:
            r = validate_segment(repo, wf, seg)
            assert r["sets"] == {"normal": "PASS", "period_end": "PASS", "empty": "PASS", "edge": "PASS"}, json.dumps(r, indent=1, default=str)[:3000]
            assert r["idempotent"] is True and r["diff_clusters"] == []

def broken_cases():
    for wf_dir in sorted(SAMPLES.glob("wf_*")):
        f = wf_dir / "broken_sql" / "broken.json"
        for case in (json.loads(f.read_text(encoding="utf-8")) if f.exists() else []):
            yield pytest.param(wf_dir.name, case, id=f"{wf_dir.name}-{case['file']}")

@pytest.mark.parametrize("wf,case", broken_cases())
def test_broken_migration_fails_with_the_right_class(tmp_path, wf, case):
    repo = prepare_workflow(tmp_path, wf)
    r = validate_segment(repo, wf, case["segment"], [case["golden_set"]], proc_path=SAMPLES / wf / "broken_sql" / case["segment"] / case["file"])
    assert r["verdict"] == "FAIL"
    hits = [c for c in r["diff_clusters"] if c["class"] == case["expect"]["class"] and set(case["expect"]["columns"]) <= set(c["columns"])]
    assert hits, json.dumps(r["diff_clusters"], indent=1, default=str)[:3000]
```
`tests/test_validate_segment.py`: a SQL syntax error gives `verdict == "FAIL"` with `error` set and CLI exit 1; an unknown segment exits 2; a non-deterministic procedure (`UNIFORM`/`RANDOM()` in a column) gives `idempotent is False`.

- [ ] **Step 2:** Run. Expected: FAIL on import.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `pytest -m e2e` and the full suite. Expected: PASS. This is the program spec's M1 exit criterion; do not proceed past it on a red run.
- [ ] **Step 5:** Commit `feat: segment validation driver and end-to-end parity suite`.

---

### Task 15: Orchestrator

**Files:** Create `orchestrate.ts`, `orchestrator.config.json`, `orchestrator/{types,manifest,policy,agents,hooks,runner,stages,cli}.ts`, `orchestrator/test/{policy,agents,stages,cli}.test.ts`, `orchestrator/test/fakes.ts`.

Verified against `@github/copilot-sdk` 1.0.14 (`dist/types.d.ts`): `createSession({ workingDirectory, model, reasoningEffort, provider, mcpServers, customAgents, agent, hooks, onPermissionRequest, onUserInputRequest })`; `CustomAgentConfig { name, description?, prompt, tools?, model?, reasoningEffort? }`; hook inputs extend `{ sessionId, timestamp: Date, workingDirectory }` and `onPreToolUse` receives `{ toolName, toolArgs }` and returns `{ permissionDecision: "allow" | "deny" | "ask", permissionDecisionReason? }`; `onPostToolUse` may return `{ modifiedResult }`; `onSessionEnd` input has `reason`; `onErrorOccurred` input has `error`, `errorContext`, `recoverable`; permission results include `{ kind: "approve-once" }` and `{ kind: "reject", feedback }`; `onUserInputRequest(request: { question, choices?, allowFreeform? }) → { answer, wasFreeform }`; `session.sendAndWait({ prompt }, timeoutMs)`, `session.disconnect()`, `client.start()`, `client.stop()`. `ProviderConfig { type: "openai", baseUrl, apiKey? }`. Use erasable TypeScript only (no enums, no parameter properties): files run under `node --experimental-strip-types`.

**Interfaces — Produces:**
```ts
// orchestrator/types.ts
export type Role = "intake" | "analyzer" | "translator" | "reviewer" | "validator" | "fixer" | "parser-recovery" | "documenter";
export type Stage = "parse" | "intake" | "analyze" | "golden" | "translate" | "document" | "pr";
export interface Manifest { id: string; tier?: "T1" | "T2" | "T3"; segments?: string[]; golden_sets?: string[]; status: Record<string, string>;
  segment_status?: Record<string, string>; metrics: Record<string, any>; parse?: { attempts: number; extensions: string[] }; [k: string]: unknown }
export interface ShResult { ok: boolean; code: number; out: string; err: string }
export type AgentError = "missing-output" | "denied" | "timeout" | "rate-limit" | "error";
export interface AgentResult { ok: boolean; error?: AgentError; detail?: string; toolCalls: number; ms: number }
export interface AgentRunner { run(role: Role, wf: Manifest, task: string, ctx?: { segment?: string; iteration?: number }): Promise<AgentResult> }
export interface Env { root: string; config: OrchestratorConfig; runner: AgentRunner; interactive: boolean; hasGh: boolean;
  py(script: string, args: string[], opts?: { inheritStdio?: boolean }): Promise<ShResult>; sh(cmd: string, args: string[]): Promise<ShResult>;
  sleep(ms: number): Promise<void>; log(line: string): void }
// orchestrator/policy.ts
export type Decision = { permissionDecision: "allow" } | { permissionDecision: "deny"; permissionDecisionReason: string };
export function decide(role: Role, wfId: string, toolName: string, toolArgs: unknown, segment?: string): Decision
// orchestrator/agents.ts
export function parseAgentFile(text: string): { name: string; description: string; model?: string; prompt: string }
export function loadAgents(root: string, profile: Profile): Promise<CustomAgentConfig[]>     // local profile drops per-agent model
// orchestrator/stages.ts
export function migrateWorkflow(env: Env, id: string, opts: RunOptions): Promise<Manifest>
export function masterSql(wfId: string, order: string[][]): string
// orchestrator/cli.ts
export function parseArgs(argv: string[]): RunOptions     // --only --from-stage --stop-after --tier --dry-run --runner mock|copilot --profile local|hosted --interactive --no-interactive --root --scenario
export function main(argv: string[]): Promise<number>
```

**Policy** (`decide`; paths compared with `/` separators, case-insensitively; first match wins):
1. Any tool whose args contain `workflows/<other id>/` → deny `no access to other workflows`.
2. SQL tools (`/snowflake|sql|query/i`): only `validator` (any statement) and `intake` (statements starting with `SELECT`/`SHOW`/`DESCRIBE` that mention `INFORMATION_SCHEMA`); deny `DROP|TRUNCATE|GRANT|REVOKE|ALTER\s+(ACCOUNT|USER|ROLE)`; deny validator SQL that names no `MIG_` schema.
3. Shell tools (`/^(bash|shell|powershell|pwsh|cmd)/i`): deny `rm -rf`, `del /s`, `format`, `git push`, `git reset --hard`, `curl`, `wget`, `Invoke-WebRequest`. Otherwise allow only the role's commands — intake: `scripts/intake_touchpoints.py`, `scripts/intake_prompt.py`; translator and fixer: `scripts/compile_check.py`; validator: `scripts/validate_segment.py`, `scripts/compare.py`; parser-recovery: `-m pytest tests/parser_corpus`, `scripts/parse.py`; analyzer: `scripts/segment.py`; reviewer, documenter: none. Read-only listing commands (`ls`, `dir`, `cat`, `type`, `Get-Content`, `Get-ChildItem`, `git status`, `git diff`, `git log`) are allowed for everyone.
4. Write tools (`/^(edit|create|write|str_replace|apply_patch)/i`), by target path: intake → `workflows/<id>/intake/`, `workflows/<id>/manifest.json`, `mappings/`; analyzer → `workflows/<id>/segments/*/contract.json`, `analysis.md`, `unsupported.json`, `manifest.json`; translator and fixer → `workflows/<id>/segments/<segment>/(proc.sql|proc.py|translation_notes.md|fix_log.md)`; reviewer → `…/review.json`; validator → `…/validation*.json`; documenter → `workflows/<id>/docs/`; parser-recovery → `scripts/parsers/ext/`, `tests/parser_corpus/`, `workflows/<id>/parsed/`. Everything under `golden/`, `cookbook/` (except nothing), `scripts/parse.py` and `.github/` is denied for every role.
5. Everything else → allow.

**Hooks** (`hooksFor(role, wf, env, segment)`): `onSessionStart` returns the three context lines from the program spec; `onPreToolUse` counts, audits to `workflows/<id>/audit.jsonl` (first 500 characters of args) and returns `decide(…)`, recording `denied = true` on a deny; `onPostToolUse` audits and flags `/(password|pwd|token|secret)\s*[=:]/i` and results over 200,000 characters; `onPostToolUseFailure` and `onErrorOccurred` audit, the latter classifying `/429|rate.?limit|quota/i` as `rate-limit`; `onSessionEnd` writes `metrics[role] = { lastMs, toolCalls }`.

**Runners.** `MockRunner(root, samplesDir, scenario)` copies `samples/<wf>/canned/…` into place per role: intake → `intake/plan.md`; analyzer → `analysis.md`, `unsupported.json`, every `contract.json`, and sets `manifest.tier`; translator → `proc.sql` and `translation_notes.md` (the scenario `fix-loop:<seg>` serves the first `broken_sql` file for that segment on iteration 0); fixer → the canned `proc.sql` plus an appended `fix_log.md` entry; reviewer → `review.json`; validator → runs `env.py("scripts/validate_segment.py", [wf, seg])` for real; documenter → `docs/migration.md`; parser-recovery → the canned extension, fixture and diagnosis, and sets `parse_report.json` status `RECOVERED`. Scenarios `never-fixed:<seg>` (fixer also serves broken SQL) and `recovery-fails` (parser-recovery writes nothing) exist for tests. `CopilotRunner` creates one session per call as verified above, with `agent: role`, prompt `task`, `onPermissionRequest: () => ({ kind: "approve-once" })`, and `onUserInputRequest` bound to a readline prompt when `env.interactive`, else answering `"The user is not available. Record this as an unchecked item in open_questions.md and continue."`. It maps a timeout to `timeout`, a hook-recorded deny to `denied`, and a classified 429 to `rate-limit`.

**Stages** (`migrateWorkflow`; each stage is skipped when its status is already terminal-good; the manifest is saved after every stage and in `finally`):
- *parse*: `py scripts/parse.py <id> --check`; on failure, up to `maxParseRecovery` (2) rounds of `parser-recovery` then re-parse; still failing → `status.parse = "QUARANTINED"`, `tier = "T3"`, stop.
- *intake*: `py scripts/intake_touchpoints.py`; if `env.interactive`, `py scripts/intake_prompt.py <id>` with inherited stdio, else with `--no-interactive`; run the `intake` agent when `intake/plan.md` is missing; status from `manifest.status.intake` re-read from disk. `WAITING_FOR_ANSWERS` → `gh issue create` when `hasGh`, else log the path to `open_questions.md`; stop. `BLOCKED`/`NEEDS_HUMAN` → stop.
- *analyze*: `py scripts/segment.py`; `analyzer` agent; every segment in `order.json` must have a `contract.json` or the agent result is `missing-output`. `tier === "T3"` → `status.translate = "MANUAL"`, stop.
- *golden*: when `golden_sets` is empty: producer `simulator` → `py scripts/dev/alteryx_sim.py <id> --set all`; producer `alteryx` → log the `inject_outputs.py` instructions, `status.golden = "BLOCKED"`, stop.
- *translate*: for each wave, segments concurrently; per segment up to `maxFixIterations` (3): translator (iteration 0) or fixer; `py scripts/compile_check.py` (failure ends the iteration); reviewer, `BLOCK` ends the iteration; validator; `verdict` starting with `PASS` → record in `segment_status` and finish the segment; `needs_human` → stop iterating. Exhausted → `NEEDS_HUMAN`. Any `NEEDS_HUMAN` in a wave → `status.translate = "NEEDS_HUMAN"`, stop. Otherwise `VALIDATED` and write `procs/master.sql`.
- *document*: `documenter` agent → `status.document = "DONE"`. *pr*: `gh pr create` when `hasGh` → `OPEN`; else log `gh not installed; skipping PR` and leave `status.pr` unset.
- Agent errors: `rate-limit` → `env.sleep(2^n * 1000)` and retry, 3 times; `missing-output` and `timeout` → retry once, then `NEEDS_HUMAN` for the stage; `denied` → `NEEDS_HUMAN` immediately. `metrics` tool-call total above `budgets.maxToolCallsPerWorkflow` → `NEEDS_HUMAN` with reason `budget`.
- `--from-stage S` clears the status of `S` and later stages first. `--stop-after S` returns after `S`. `--dry-run` logs the stages that would run and calls nothing. `--interactive` forces workflow parallelism 1.

`orchestrator.config.json`: `python` (`.venv/Scripts/python.exe`), `samplesDir`, `parallelism: 3`, `maxFixIterations: 3`, `maxParseRecovery: 2`, `sessionTimeoutMs: 1200000`, `golden.producer: "simulator"`, `budgets.maxToolCallsPerWorkflow: 400`, and `profiles`: `local` = `{ provider: { type: "openai", baseUrl: "http://127.0.0.1:8080/v1", apiKey: "local" }, model: "ternary-bonsai-2-27b", reasoningEffort: "medium" }`; `hosted` = `{ model: "gpt-5.6-luna", roleModels: { intake, analyzer, translator, fixer, "parser-recovery": "gpt-6-astra" } }`.

- [ ] **Step 1: Write the failing tests**

```ts
// orchestrator/test/policy.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { decide } from "../policy.ts";
const allow = (d: any) => assert.equal(d.permissionDecision, "allow", JSON.stringify(d));
const deny = (d: any, why: RegExp) => { assert.equal(d.permissionDecision, "deny"); assert.match(d.permissionDecisionReason, why); };

test("translator writes only its own segment's files", () => {
  allow(decide("translator", "wf_0001", "edit", { path: "workflows/wf_0001/segments/seg_01/proc.sql" }, "seg_01"));
  deny(decide("translator", "wf_0001", "edit", { path: "workflows/wf_0001/segments/seg_02/proc.sql" }, "seg_01"), /translator/);
  deny(decide("translator", "wf_0001", "create", { path: "cookbook/filter.md" }, "seg_01"), /cookbook|translator/);
});
test("nobody touches golden data, the core parser or another workflow", () => {
  deny(decide("fixer", "wf_0001", "edit", { path: "workflows\\wf_0001\\golden\\outputs\\normal\\7.csv" }, "seg_01"), /golden/);
  deny(decide("parser-recovery", "wf_0005", "edit", { path: "scripts/parse.py" }), /parse\.py/);
  allow(decide("parser-recovery", "wf_0005", "create", { path: "scripts/parsers/ext/acme_dedupe.py" }));
  deny(decide("reviewer", "wf_0001", "view", { path: "workflows/wf_0002/manifest.json" }), /other workflows/);
});
test("read-only roles write only their own report", () => {
  allow(decide("reviewer", "wf_0001", "create", { path: "workflows/wf_0001/segments/seg_01/review.json" }, "seg_01"));
  deny(decide("reviewer", "wf_0001", "edit", { path: "workflows/wf_0001/segments/seg_01/proc.sql" }, "seg_01"), /reviewer/);
  allow(decide("validator", "wf_0001", "create", { path: "workflows/wf_0001/segments/seg_01/validation.json" }, "seg_01"));
});
test("SQL is for the validator in MIG schemas, and intake may only read the catalog", () => {
  deny(decide("translator", "wf_0001", "snowflake_query", { sql: "SELECT 1 FROM MIG_WORK.T" }), /may not execute SQL/);
  allow(decide("validator", "wf_0001", "snowflake_query", { sql: "CALL MIG_WORK.WF0001_SEG_01('A','B','C','D','r')" }));
  deny(decide("validator", "wf_0001", "snowflake_query", { sql: "SELECT * FROM FINANCE.RAW.GL_LEDGER" }), /sandbox/);
  deny(decide("validator", "wf_0001", "snowflake_query", { sql: "DROP TABLE MIG_WORK.T" }), /destructive/);
  allow(decide("intake", "wf_0001", "snowflake_query", { sql: "select table_name from information_schema.columns" }));
  deny(decide("intake", "wf_0001", "snowflake_query", { sql: "SELECT * FROM SALES.RAW.ORDERS" }), /INFORMATION_SCHEMA/);
});
test("shell is limited to each role's scripts", () => {
  allow(decide("translator", "wf_0001", "powershell", { command: ".venv/Scripts/python.exe scripts/compile_check.py wf_0001 seg_01" }, "seg_01"));
  deny(decide("translator", "wf_0001", "bash", { command: "python scripts/validate_segment.py wf_0001 seg_01" }, "seg_01"), /translator/);
  deny(decide("validator", "wf_0001", "bash", { command: "rm -rf workflows" }), /destructive/);
  deny(decide("documenter", "wf_0001", "bash", { command: "curl http://example.com" }), /destructive|documenter/);
  allow(decide("documenter", "wf_0001", "bash", { command: "git diff --stat" }));
});
```
```ts
// orchestrator/test/stages.test.ts  (uses fakes.ts: makeEnv({ scenario, py }) → { env, calls, files } over a temp root seeded from samples)
import { test } from "node:test";
import assert from "node:assert/strict";
import { makeEnv, readManifest } from "./fakes.ts";
import { migrateWorkflow, masterSql } from "../stages.ts";

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
  assert.deepEqual([m.status.parse, m.tier, m.status.translate], ["PARSED", "T3", "MANUAL"]);
  assert.ok(!calls.roles.includes("translator"));
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
```
`agents.test.ts`: `parseAgentFile` splits frontmatter from the prompt; `loadAgents` returns nine agents, the local profile omits `model`, the hosted profile keeps the file's `model`. `cli.test.ts`: `parseArgs` for every flag; `--dry-run` calls neither `py` nor the runner. In `fakes.ts`, `py` is a stub that performs each script's file effects from the samples' canned data (so these tests need no Python) and records calls; the validator's verdict follows the scenario.

- [ ] **Step 2:** Run `fnm exec --using=22 npm.cmd test`. Expected: FAIL, modules missing.
- [ ] **Step 3:** Implement the modules, then `orchestrate.ts` (`process.exit(await main(process.argv.slice(2)))`).
- [ ] **Step 4:** Run `npm test` and `fnm exec --using=22 npx.cmd tsc --noEmit`. Expected: all pass, no type errors against the installed SDK.
- [ ] **Step 5:** Integration, real Python, mock agents, in a scratch copy: `fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root <scratch> --runner mock --no-interactive`. Expected terminal states: wf_0001–0004 `VALIDATED`, wf_0005 `MANUAL`. Because intake is non-interactive and unanswered, the first pass parks at `WAITING_FOR_ANSWERS`; answer by copying each sample's `answers` into `manifest.answers` (add `scripts/dev/answer_samples.py` for this) and run again.
- [ ] **Step 6:** Commit `feat: SDK orchestrator with permission policy, state machine and mock runner`.

---

### Task 16: Live BYOK smoke test

**Files:** Create `scripts/dev/serve_model.ps1`, `docs/live-smoke-test.md`.

`serve_model.ps1` parameters with defaults: `-Model C:\Users\<user>\models\Ternary-Bonsai-2-27B\Ternary-Bonsai-2-27B-PQ2_0.gguf`, `-Server C:\Users\<user>\tools\llama-prism\llama-server.exe`, `-Port 8080`, `-Context 32768`; it runs `llama-server -m … --host 127.0.0.1 --port … -ngl 99 -fa on -c … --jinja --alias ternary-bonsai-2-27b` and fails clearly if either path is missing. Loopback only.

- [ ] **Step 1:** Start the server; wait for `GET /health` = `{"status":"ok"}`.
- [ ] **Step 2:** In a scratch root seeded with `wf_0001`: `… orchestrate.ts --root <scratch> --only wf_0001 --runner copilot --profile local --no-interactive --stop-after intake`.
- [ ] **Step 3:** Read `workflows/wf_0001/audit.jsonl`. Record the real `toolName` values and tighten the three regexes in `policy.ts` to them, adding a policy test per new name.
- [ ] **Step 4:** If intake produced `plan.md`, continue with `--stop-after translate` and record how far the model gets.
- [ ] **Step 5:** Write `docs/live-smoke-test.md`: date, versions, command lines, what worked (session creation, agent selection, hooks firing, denials, `ask_user`, files written), what did not, tokens per second, and the honest verdict on using this model for each role. Stop the server.
- [ ] **Step 6:** Commit `docs: live BYOK smoke test results and tightened tool policy`.

---

### Task 17: Offline full run, README, verification

- [ ] **Step 1:** In the repo root: `scripts/dev/build_samples.py seed`, `scripts/dev/answer_samples.py`, then the orchestrator with `--runner mock --no-interactive` until every workflow is terminal. Commit the resulting `workflows/` (the `.gitignore` already excludes `*.duckdb` and `audit.jsonl`).
- [ ] **Step 2:** Write `README.md`: what this is, the honesty note (nothing has run on Snowflake or Alteryx), prerequisites, the three commands to run tests, how to run the pipeline interactively on a sample (showing the yxdb prompt), how to add a real workflow, how to switch to a real Snowflake backend and the hosted profile, the repo map, and the "verify against your build" list from program spec §12 with each item marked verified, corrected or still open.
- [ ] **Step 3:** Full verification from a clean state: `pytest`, `pytest -m e2e`, `npm test`, `tsc --noEmit`. Paste the summaries into the final report. Any red result is reported as red.
- [ ] **Step 4:** Commit `feat: worked examples from the offline run, README and final verification`.
