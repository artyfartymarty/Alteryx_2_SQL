# Phase 04 — Intake, capture instrumentation, agents, cookbook, DDL

Read [00-index.md](00-index.md) first. Contracts C6 and C8 matter most here.

---

### Task 10: Interactive intake

This is the owner's explicit requirement: **the pipeline asks the user which Snowflake table each yxdb (and every other input and output) corresponds to.** Enumeration and prompting are code; the intake agent adds judgment afterwards.

**Files:** Create `scripts/intake_touchpoints.py`, `scripts/intake_prompt.py`, `catalog/columns.csv`, `catalog/README.md`; Tests `tests/test_intake_touchpoints.py`, `tests/test_intake_prompt.py`.

**Interfaces — Consumes:** `parsed/dag.json`, `lib.yxdb.read_header`, `mappings/global.yaml`, `lib.io`. **Produces:**
```python
# scripts/intake_touchpoints.py
def normalize_key(config: dict) -> str                                   # contract C8
def load_catalog(repo: Repo, backend=None) -> list[dict]                 # rows: database, schema, table, column, data_type, row_count
def find_yxdb(repo: Repo, wf_id: str, source: str, extra_dirs: Sequence[Path] = ()) -> Path | None
def enumerate_touchpoints(repo: Repo, wf_id: str, dag: dict) -> list[dict]
def propose_candidates(tp: dict, catalog: list[dict], program: dict) -> list[dict]
def run(repo: Repo, wf_id: str, extra_dirs: Sequence[Path] = ()) -> list[dict]          # writes intake/touchpoints.json

# scripts/intake_prompt.py
FQN_RE = r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){2}$"
def prompt_touchpoints(touchpoints: list[dict], *, ask: Callable[[str], str], out: Callable[[str], None]) -> list[dict]
def logical_name(fqn: str, taken: set[str]) -> str                       # table part; on collision "<SCHEMA>_<TABLE>"
def apply_answers(repo: Repo, wf_id: str, touchpoints: list[dict], answers: list[dict], user: str) -> dict   # writes mappings.yaml; promotes to global.yaml
def render_open_questions(wf_id: str, owner: str, touchpoints: list[dict], mappings: dict) -> str
def parse_answered_questions(md_text: str) -> dict[str, str]             # {"Q1": "FINANCE.RAW.GL_LEDGER", "Q3": ""}
def run(repo: Repo, wf_id: str, *, interactive: bool, ask=input, out=print, user: str | None = None) -> str   # returns the intake status
```
CLIs: `python scripts/intake_touchpoints.py <wf> [--yxdb-dir DIR]… [--root .]`; `python scripts/intake_prompt.py <wf> [--no-interactive] [--user NAME] [--root .]` (interactive by default only when stdin is a TTY). `intake_prompt` exit codes: 0 `READY`, 1 `WAITING_FOR_ANSWERS` or `BLOCKED`.

**Touchpoint** (one per external thing, ordered by numeric tool id, inputs before outputs for the same id):
```json
{ "id": "Q1", "kind": "input", "tool_id": "1", "annotation": "Orders extract", "format": "yxdb",
  "source": "C:\\data\\sales\\orders.yxdb", "key": "sales/orders.yxdb",
  "fields": ["ORDER_ID", "CUSTOMER"], "field_source": "yxdb_header", "record_count": 20, "query": null,
  "table": null, "write_mode": null, "keys": [], "pre_sql": null, "post_sql": null,
  "blocking": true, "resolved": null, "candidates": [] }
```
`kind`: `input`, `output`, `macro`, `constant`, `parameter`, `manual`. Only `input` and `output` are blocking. `field_source` is `yxdb_header` when `find_yxdb` finds the file (literal path, then `workflows/<wf>/source/data/<basename>`, then each `extra_dirs`), else `meta` (the tool's `meta`), else `downstream` (field names referenced by the nearest downstream select/join/formula). DB inputs take fields from `meta`; `table` is the first table named in `query`'s `FROM`. `resolved` is filled from `mappings/global.yaml` (`sources[key]` or `outputs[key]`) as `{"snowflake", "logical", "from": "global"}` and such touchpoints are **never asked again**. `manual` touchpoints (type in `T3_TYPES`) and `unknown` tools are listed for `plan.md` but do not block intake.

**Candidates.** For each unresolved input or output, score every catalog table: `overlap = matched / len(fields)` on upper-cased names; `name = ` Jaccard similarity between the tokens of the file/table base name and the catalog table name (split on non-alphanumerics, drop pure digits); `score = 0.8 * overlap + 0.2 * name`. Keep tables with `overlap >= 0.5`, best three, each `{"snowflake", "matched", "of", "missing": […], "score", "row_count", "basis": "columns"}`. Always append the naming-convention candidate `{"snowflake": "<target_database>.<raw_schema|target_schema>.<SANITIZED_BASENAME>", "basis": "naming"}` (inputs use `program.raw_schema`, outputs `program.target_schema`) unless it duplicates one above. A naming candidate is a *proposal to confirm*, never applied silently.

**Prompt protocol.** Opening block, once per workflow:
```
wf_0001 · sales_summary.yxmd · 2 yxdb files found: orders.yxdb (tool 1, input), sales_summary.yxdb (tool 7, output)
Does this workflow depend on other .yxdb files that are not visible in the DAG (for example written by another workflow)? [y/N]:
```
`y` loops: `Path of the yxdb:` then `Snowflake table (DB.SCHEMA.TABLE):`, recording a source with `tool_ids: []` and `note: user-declared`; empty path ends the loop. For an **input** yxdb whose file was not found (never for outputs, which do not exist yet), ask once `Local copy of <basename> to read its field list (Enter to skip):` and re-run candidate scoring if given. Then per unresolved touchpoint:
```
Q1 · Tool 1 · Input Data reads C:\data\sales\orders.yxdb (7 fields, 20 rows)
  1) SALES.RAW.ORDERS            7/7 columns · 1,204,551 rows
  2) SALES.RAW.ORDERS_ARCHIVE    6/7 columns (missing STATUS)
  3) ANALYTICS.RAW.ORDERS        naming convention — not verified to exist
Snowflake table [1]  (Enter = accept · number · DB.SCHEMA.TABLE · ? = defer · ! = cannot exist in Snowflake):
```
Enter accepts candidate 1 **only if its basis is `columns`**; otherwise Enter defers. A value that is neither a listed number nor matches `FQN_RE` is asked again; after three invalid attempts in total the touchpoint is deferred. Outputs then ask `Write mode [merge] (overwrite/append/merge):` defaulting from `write_mode` (`update_insert` → `merge`, `truncate_append` → `overwrite`) and, for merge, `Merge keys [ACCT, PERIOD]:`. Each answer is `{"id", "action": "map"|"defer"|"impossible", "snowflake", "write_mode", "keys"}`.

**Persistence.** `apply_answers` writes `intake/mappings.yaml` in the program schema's shape plus `logical` (C6): `sources`, `outputs` (with `mode` and `keys`), `constants` (from `dag.constants`), `parameters` and `macros` (each macro path → `{inline: true}`). Every mapped entry gets `confirmed_by: <user>`. Mapped sources and outputs are promoted to `mappings/global.yaml` with `load_path: already_there` unless the key already exists there with a different table, in which case nothing is overwritten and the status becomes `NEEDS_HUMAN` with both values named in `open_questions.md`. `render_open_questions` follows program spec §5.4: `## Blocking` and `## Non-blocking`, one `- [ ]`/`- [x]` item per touchpoint with its `Q<n>` id, what the tool does, the proposal with evidence, and the consequence. Constants are non-blocking items whose default is the current value. `parse_answered_questions` reads checked items; the answer is the text after the item's last `: `, and an empty answer on a checked item means "accept the proposal".

**Status.** `BLOCKED` if any answer is `impossible`; else `WAITING_FOR_ANSWERS` if any blocking touchpoint is unresolved; else `READY`. `run` writes `manifest.status.intake` and, non-interactively, first merges answers found in `open_questions.md` and `manifest.answers`.

`catalog/columns.csv` (header `database,schema,table,column,data_type,row_count`) describes a plausible warehouse so candidates exist for the samples: `SALES.RAW.ORDERS` (all 7 order columns), `SALES.RAW.ORDERS_ARCHIVE` (6 of them), `CRM.RAW.CUSTOMERS`, `CRM.RAW.ORDERS_EXPORT`, `FINANCE.RAW.GL_LEDGER`, `ANALYTICS.CURATED.GL_SUMMARY`, `ANALYTICS.CURATED.CUSTOMER_ORDER_FACT`, `WH.RAW.STOCK`, `VENDOR.RAW.ACCOUNTS`, and two decoys sharing a few column names. `catalog/README.md` says the file is a stand-in for `INFORMATION_SCHEMA.COLUMNS` and gives the query that exports the real one.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_intake_prompt.py
import shutil
from pathlib import Path
import pytest
from lib import io
from lib.paths import Repo
import intake_touchpoints as tpx, intake_prompt as ip
from dev import build_samples

ROOT = Path(__file__).parents[1]
@pytest.fixture
def repo(tmp_path):
    shutil.copytree(ROOT / "mappings", tmp_path / "mappings"); shutil.copytree(ROOT / "catalog", tmp_path / "catalog")
    r = Repo(tmp_path); build_samples.build(r, ROOT / "samples", "wf_0001"); return r
def scripted(answers):
    it = iter(answers); said = []
    return (lambda prompt: (said.append(prompt), next(it))[1]), said

def test_touchpoints_read_the_yxdb_header_and_rank_candidates(repo):
    tps = tpx.run(repo, "wf_0001")
    t = tps[0]
    assert (t["id"], t["kind"], t["format"], t["key"]) == ("Q1", "input", "yxdb", "sales/orders.yxdb")
    assert t["field_source"] == "yxdb_header" and len(t["fields"]) == 7 and t["record_count"] > 0
    assert t["candidates"][0]["snowflake"] == "SALES.RAW.ORDERS" and t["candidates"][0]["matched"] == 7
    assert t["candidates"][1]["missing"] == ["STATUS"] and t["candidates"][-1]["basis"] == "naming"
    assert [x["kind"] for x in tps].count("output") == 2

def test_enter_accepts_a_column_backed_candidate_and_outputs_are_asked_for_mode(repo):
    tps = tpx.run(repo, "wf_0001")
    ask, said = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    out = []
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=out.append, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    assert "not visible in the DAG" in said[0] and "7/7 columns" in "\n".join(out)
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert m["sources"]["sales/orders.yxdb"] == {"snowflake": "SALES.RAW.ORDERS", "logical": "ORDERS", "tool_ids": ["1"], "confirmed_by": "wf_owner"}
    assert m["outputs"]["out/sales_summary.yxdb"]["snowflake"] == "ANALYTICS.CURATED.SALES_SUMMARY" and m["outputs"]["out/sales_summary.yxdb"]["mode"] == "overwrite"
    md = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert "- [x] Q1" in md and "- [ ] Q3" in md and "## Blocking" in md
    assert io.read_yaml(repo.global_mappings)["sources"]["sales/orders.yxdb"]["snowflake"] == "SALES.RAW.ORDERS"

def test_answers_written_into_open_questions_resume_the_workflow(repo):
    tpx.run(repo, "wf_0001"); ask, _ = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    q = repo.wf("wf_0001", "intake", "open_questions.md")
    lines = [l.replace("- [ ] Q3", "- [x] Q3").rstrip() + (" ANALYTICS.CURATED.EXCLUDED_ORDERS" if l.startswith("- [ ] Q3") else "") for l in q.read_text(encoding="utf-8").splitlines()]
    q.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert ip.run(repo, "wf_0001", interactive=False, user="wf_owner") == "READY"
    assert io.load_manifest(repo, "wf_0001")["status"]["intake"] == "READY"

def test_globally_resolved_sources_are_never_asked_again(repo):
    g = io.read_yaml(repo.global_mappings); g["sources"]["sales/orders.yxdb"] = {"snowflake": "SALES.RAW.ORDERS", "logical": "ORDERS", "confirmed_by": "jdoe"}
    io.write_yaml(repo.global_mappings, g)
    tps = tpx.run(repo, "wf_0001")
    assert tps[0]["resolved"] == {"snowflake": "SALES.RAW.ORDERS", "logical": "ORDERS", "from": "global"}
    ask, said = scripted(["n", "?", "?"]); ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert not any("Q1" in s for s in said)

def test_bad_fqn_is_reasked_then_deferred_and_never_invented(repo):
    tpx.run(repo, "wf_0001"); ask, said = scripted(["n", "orders", "SALES.ORDERS", "nope nope", "?", "?"])
    assert ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner") == "WAITING_FOR_ANSWERS"
    assert "sales/orders.yxdb" not in (io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml")).get("sources") or {})

def test_impossible_touchpoint_blocks(repo):
    tpx.run(repo, "wf_0001"); ask, _ = scripted(["n", "!", "?", "?"])
    assert ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner") == "BLOCKED"

def test_user_declared_yxdb(repo):
    tpx.run(repo, "wf_0001"); ask, _ = scripted(["y", r"\\share\fx\rates.yxdb", "FINANCE.RAW.FX_RATES", "", "?", "?", "?"])
    ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    src = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))["sources"]["fx/rates.yxdb"]
    assert src["snowflake"] == "FINANCE.RAW.FX_RATES" and src["tool_ids"] == [] and src["note"] == "user-declared"

def test_conflict_with_global_needs_a_human(repo):
    g = io.read_yaml(repo.global_mappings); g["outputs"]["out/sales_summary.yxdb"] = {"snowflake": "ANALYTICS.CURATED.OTHER", "logical": "OTHER", "confirmed_by": "jdoe"}
    io.write_yaml(repo.global_mappings, g); tps = tpx.run(repo, "wf_0001")
    answers = [{"id": t["id"], "action": "map", "snowflake": "ANALYTICS.CURATED.SALES_SUMMARY", "write_mode": "overwrite", "keys": []} for t in tps if t["key"] == "out/sales_summary.yxdb"]
    for t in tps: t["resolved"] = None
    assert ip.apply_answers(repo, "wf_0001", tps, answers, "wf_owner")["status"] == "NEEDS_HUMAN"
    assert io.read_yaml(repo.global_mappings)["outputs"]["out/sales_summary.yxdb"]["snowflake"] == "ANALYTICS.CURATED.OTHER"
```
`tests/test_intake_touchpoints.py` covers `normalize_key` (`C:\data\sales\orders.yxdb` → `sales/orders.yxdb`; `\\fileserver\crm\customers.yxdb` → `crm/customers.yxdb`; DB alias → `alias:prod_fin`), the DB input of `wf_0003` (`table == "dbo.GL_LEDGER"`, fields from `meta`, top candidate `FINANCE.RAW.GL_LEDGER`), the `update_insert` output carrying `keys` and both SQL hooks, a macro and two constants listed as non-blocking, and `wf_0005`'s `manual` and unknown tools listed without blocking.

- [ ] **Step 2:** Run both files. Expected: FAIL on import.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: all pass. Then try it for real: `.venv/Scripts/python.exe scripts/intake_prompt.py wf_0001 --root <a seeded temp dir>` and read the transcript for clarity.
- [ ] **Step 5:** Commit `feat: interactive intake that asks the owner to map yxdb and other touchpoints`.

---

### Task 11: `scripts/inject_outputs.py`

The real-Alteryx golden path. Captures are written as `.yxdb` because Alteryx writes it natively with exact types and Task 2 can read it.

**Files:** Create `scripts/inject_outputs.py`; Test `tests/test_inject_outputs.py`.

**Interfaces — Produces:**
```python
def inject(xml_text: str, dag: dict, segment_dags: list[dict], capture_dir: str) -> tuple[str, list[dict]]
    # capture map rows: {"tool_id": "1012", "kind": "input"|"intermediate"|"output", "of_tool": "1", "stream": "1_Output", "segment": null|"seg_02", "file": "<capture_dir>\\in_1.yxdb"}
def import_captures(repo: Repo, wf_id: str, golden_set: str, capture_dir: Path) -> list[Path]   # yxdb → typed CSV under golden/ (C2)
```
CLI: `python scripts/inject_outputs.py <wf> --capture-dir C:\mig\capture\wf_0001 [--import-set normal] [--root .]`. New ToolIDs start at `max(existing) + 1001`. One Output Data node (`FileFormat="19"`) per input tool out stream, per segment outbound stream, and per stream feeding a final output tool; canonical anchors are mapped back to XML names (`T` → `True`, `J` → `Join`, …). Writes `source/<name>.instrumented.yxmd` and `golden/capture_map.json`, and prints the `AlteryxEngineCmd.exe` command line to run. It never runs Alteryx.

- [ ] **Step 1:** Tests: instrumenting `wf_0003` yields XML that `parse.parse_file` accepts with `invariants.check == []`; the capture map has 1 input, 1 intermediate (`seg_01`'s stream `3_Output`) and 1 output entry; original nodes' XML is byte-identical; `import_captures` on a directory of yxdb files written with `write_yxdb` produces `golden/inputs/normal/1.csv` equal to the source rows.
- [ ] **Step 2:** Run. Expected: FAIL on import.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat: workflow instrumentation for golden capture and yxdb capture import`.

---

### Task 12: Agents, config, cookbook, Snowflake DDL

**Files:** Create `.github/agents/{intake,analyzer,translator,reviewer,validator,fixer,parser-recovery,documenter,cookbook-curator}.agent.md`, `config.json`, `cookbook/index.md`, `cookbook/{input,output,select,filter,formula,join,union,summarize,sort,unique,sample,record_id,multi_row_formula,cross_tab,transpose,regex,datetime,data_cleansing}.md`, `cookbook/proposals/.gitkeep`, `tests/cookbook_examples/<tool>/{case.json,input*.csv,query.sql}`, `snowflake/{01_ops_tables,02_shadow_table_template,03_reconciliation_task_template,04_alerts,05_roles}.sql`; Tests `tests/test_agents_config.py`, `tests/test_cookbook_examples.py`, `tests/test_snowflake_ddl.py`.

**Agents.** Copy each definition from program spec 01 §A.4 verbatim, then apply these amendments so they match what was built:
- *intake*: step 1 becomes "Run `python scripts/intake_touchpoints.py <id>` and read `intake/touchpoints.json`; do not re-enumerate by hand." Add: "When a human is present, ask through `ask_user`, one touchpoint per question, with the top candidate as the default. Otherwise run `python scripts/intake_prompt.py <id> --no-interactive`." Add: "Every source and output needs a `logical` name (plan contract C6)."
- *analyzer*: contracts include `outputs[]`, `inputs[].logical` and `ordering.order_dependent_columns` (C5).
- *translator*: replace the `SRC_DB`/`SRC_SCHEMA` rule with contract C4 in full, and "Done when `python scripts/compile_check.py <id> seg_NN` exits 0".
- *reviewer*: add blocking checks "procedure matches the C4 signature and is a linear statement list" and "no literal reference to a mapped Snowflake table; sources and targets use `IDENTIFIER` with logical names".
- *validator*: procedure becomes "Run `python scripts/validate_segment.py <id> seg_NN`; it deploys, runs every golden set, runs the first twice, and calls `compare.py`. Then read `validation.json` and add your interpretation under `interpretation`. Never edit numbers."
- All: frontmatter keys are `name`, `description`, `model` only.

`config.json` is program spec 01 §A.1 verbatim.

**Cookbook.** Each page uses the program spec §8.1 template. `index.md` has the §8.2 tool map, §8.3 type map and §8.4 function map, plus a "Local verification" note: patterns are verified on DuckDB through sqlglot and are **not yet verified on Snowflake**; `String(n)` overflow errors and `ALTER SESSION` inside owner's-rights procedures cannot be reproduced locally. Every page's "Snowflake pattern" is exactly the SQL in that tool's `query.sql`.

**Runnable examples.** `tests/cookbook_examples/<tool>/case.json`:
```json
{ "tool": "filter", "inputs": {"1": "input.csv"}, "node": { "tool_id": "2", "type": "filter", "config": {"expression": "[REGION] != \"WEST\""} },
  "edges": [["1", "Output", "2", "Input"]], "compare": [ { "stream": "2_T", "sql": "query_true.sql", "keys": ["ID"] },
                                                         { "stream": "2_F", "sql": "query_false.sql", "keys": ["ID"] } ] }
```
The harness loads each input as `MIG_COOKBOOK.IN_<id>`, runs the **simulator** on the one-tool DAG for the expected rows, runs the page's SQL on DuckDB for the actual rows, and asserts `compare(...)["verdict"] == "PASS"`. So every cookbook pattern is checked against the modelled Alteryx behaviour, including its NULL, ordering and truncation edge rows. Each input has at least one NULL, one duplicate and one boundary row.

`snowflake/*.sql`: the DDL from program spec §10.2–10.4 with `<WF_ID>`/`<TARGET>` placeholders in the templates, `CREATE ALERT` on `RECON_RESULTS.VERDICT = 'FAIL'`, and the three roles from §11.1 with `MIG_*`-only grants for `MIGRATION_AGENT`.

- [ ] **Step 1:** Write the tests. `test_agents_config.py`: nine agent files exist; each has frontmatter with exactly `name`, `description`, `model` and `name` equals the file stem; translator mentions `TGT_SCHEMA` and `compile_check.py`; validator mentions `validate_segment.py`; `config.json` parses, `maxConcurrency == 4`, `maxDepth == 2`, and lists all nine agents plus the five built-ins. `test_cookbook_examples.py`: parametrized over `tests/cookbook_examples/*/case.json` as described; also assert every cookbook page has the five template headings and that its fenced SQL equals the example's query files. `test_snowflake_ddl.py`: every file is non-empty, mentions no production schema other than `OPS`/`ANALYTICS` placeholders, and each `CREATE TABLE` statement parses with sqlglot's Snowflake dialect.
- [ ] **Step 2:** Run. Expected: FAIL (files missing).
- [ ] **Step 3:** Write the content. Where a pattern fails against the simulator, decide which is wrong from the documented Alteryx behaviour in program spec §8, fix that side, and note it in the page's "Parity risks".
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat: agent definitions, Copilot config, cookbook v1 with executable examples, ops DDL`.
