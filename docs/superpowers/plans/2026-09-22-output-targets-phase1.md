# Output Targets, Phase 1 — Target Decision and the Snowpark Python Target — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the pipeline decide per segment whether a migration output is a SQL procedure or a Snowpark Python procedure, produce and validate Snowpark procedures offline exactly as SQL ones are, and prove it end to end with a new sample workflow whose Python tool the simulator can run.

**Architecture:** A deterministic `target_check.py` proposes targets from the parsed DAG; the analyzer may only lower them; `contract.json.target` drives the orchestrator's per-segment dispatch (static check → validator). Snowpark procedures have `proc.py` as source of truth, `proc.sql` rendered from it, an AST rule set enforced by `compile_check.py --target snowpark`, and `validate_snowpark.py` runs them in the Snowpark Local Testing Framework, then judges with the unchanged `compare.py` through a validation library shared with `validate_segment.py`. The simulator gains a sandboxed `python` tool so golden data exists for the new sample.

**Tech Stack:** Python 3.14 (`.venv`), `snowflake-snowpark-python[pandas]` 1.55 (local testing), DuckDB via `sqlglot`, `pytest`; TypeScript on Node 22 (`node --experimental-strip-types`, `node:test`).

**Spec:** [docs/superpowers/specs/2026-09-22-output-targets-design.md](../specs/2026-09-22-output-targets-design.md) — sections 1–7 (SQL + Snowpark parts), 9, 10, 11. Phase 2 (dbt, `wf_0007`, cookbook pages, `prompt_context.py`, live runs) is a separate plan.

## Global Constraints

- Python: always `.venv/Scripts/python.exe` from the repo root; tests `.venv/Scripts/python.exe -m pytest <paths>` (`addopts=-q` is set; never add another `-q`). Node: `"C:\Users\<you>\AppData\Local\Microsoft\WinGet\Links\fnm.exe" exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts` and `… node.exe node_modules/typescript/bin/tsc --noEmit -p .` (in a worktree, `tsc` comes from the main checkout's `node_modules`).
- Exit codes `0` success / `1` domain failure / `2` usage or crash; `main(argv=None) -> int` mirrors `scripts/parse.py`; every script takes `--root` and writes only under it.
- Files UTF-8, LF; JSON `indent=2` + trailing newline (`lib.io.write_json`). No machine paths or user names in committed files. `docs/spec/**` is never edited.
- Numbers about data come only from scripts; agents and canned artefacts never invent a count.
- Statuses, verdicts and diff classes keep their vocabularies (`00-index.md` Global Constraints).
- `compare.py` is not modified by this plan.
- TDD; `wip:` commits early; never `git stash`, `git checkout --` or `git reset --hard`; conventional commits ending `Co-Authored-By: <model-accurate trailer>`; no push to any remote.
- Suites at the start: pytest 1005 passed / 0 skipped, node 150/150, tsc clean. Zero skips stay zero.

## File structure

| File | Responsibility |
|---|---|
| `scripts/parsers/plugin_map.py` (modify) | `python` plugin/type/anchors; `TARGET_CLASS` + `node_class()` |
| `scripts/parsers/tool_config.py` (modify) | `_python()` config extractor |
| `scripts/segment.py` (modify) | a `python` node is a hard cut (its own segment) |
| `scripts/dev/alteryx_sim.py` (modify) | `sim_python`: sandboxed script with the `Alteryx` pandas shim |
| `scripts/target_check.py` (new) | targets.json proposal |
| `scripts/dev/build_samples.py` (modify) | `output_target` into the manifest |
| `mappings/global.yaml` (modify) | `program.output_target`, `program.snowpark_runtime` |
| `scripts/lib/snowpark_rules.py` (new) | AST rules for `proc.py` (shared by compile_check and tests) |
| `scripts/render_snowpark.py` (new) | `proc.py` → `proc.sql` wrapper |
| `scripts/lib/proc_runner.py` (modify) | `parse_proc` accepts `LANGUAGE PYTHON` (records `language`) |
| `scripts/compile_check.py` (modify) | `--target auto\|sql\|snowpark` |
| `scripts/lib/validation.py` (new) | report shaping shared by the validators (moved out of `validate_segment.py`) |
| `scripts/lib/types_map.py` (modify) | `alteryx_to_snowpark()` |
| `scripts/validate_snowpark.py` (new) | Snowpark local-testing validator |
| `samples/wf_0006/**`, `samples/_tools/make_golden_inputs.py` (modify) | the new sample, goldens, canned artefacts, broken variants |
| `tests/helpers.py`, `tests/test_e2e_parity.py`, `tests/test_canned_artifacts.py` (modify) | copy `proc.py`; run `wf_0006`; check Snowpark canned rules |
| `orchestrator/stages.ts`, `runner.ts`, `policy.ts`, `types.ts`, `test/**` (modify) | analyze runs target_check; dispatch by target; mock replay of `.py`; lanes |
| `.github/agents/{analyzer,translator,reviewer,validator,fixer}.agent.md` (modify) | per-target instructions |
| `docs/reference/output-targets.md` (new), `docs/reference/dag-contract.md`, `docs/reference/simulator-semantics.md`, `README.md` (modify) | documentation |

---

### Task 1: The `python` tool in the parser, the segmenter and the simulator

**Files:**
- Modify: `scripts/parsers/plugin_map.py` (`PLUGIN_TYPES`, `ANCHORS`), `scripts/parsers/tool_config.py` (`_python`, `PARSERS`), `scripts/segment.py` (`is_hard`), `scripts/dev/alteryx_sim.py` (`sim_python`, `SIMULATORS`)
- Modify: `docs/reference/dag-contract.md` (§2 table row, §4 config), `docs/reference/simulator-semantics.md` (new §"Python tool")
- Test: `tests/test_alteryx_sim_python.py` (new), `tests/test_parse_python_tool.py` (new), `tests/test_segment.py` (add one test)

**Interfaces:**
- Consumes: `_table`, `_row_dicts`, `_field`, `_Context`, `SIMULATORS` (alteryx_sim.py); `PARSERS`, `_find` (tool_config.py); `PLUGIN_TYPES`, `ANCHORS` (plugin_map.py).
- Produces: tool type `"python"`; anchors in `["Input"]`, out `{"Output1": "1", …, "Output5": "5"}`; node config `{"script": str}`; `sim_python(node, inputs_by_anchor, ctx) -> {"1": Table, …}` for the anchors the script wrote; `PYTHON_TOOL_ALLOWED_MODULES` (frozenset) and `run_python_tool(script: str, inputs: list[dict]) -> dict[int, dict]` exported from `alteryx_sim.py` for tests.

- [ ] **Step 1: Failing tests**

`tests/test_alteryx_sim_python.py`:

```python
"""The simulator's python tool: a sandboxed script with the Alteryx.read/write pandas shim."""
from __future__ import annotations

import pytest

from dev import alteryx_sim as sim

FIELDS = [{"name": "CUSTOMER", "type": "V_String", "size": 20, "scale": None},
          {"name": "PERIOD", "type": "V_String", "size": 7, "scale": None},
          {"name": "BILLED", "type": "Double", "size": 8, "scale": None},
          {"name": "CAP", "type": "Double", "size": 8, "scale": None},
          {"name": "CANCELLED", "type": "Bool", "size": 1, "scale": None}]
ROWS = [["A", "2026-01", 100.0, 80.0, False], ["A", "2026-02", 50.0, 80.0, False],
        ["A", "2026-03", 10.0, 80.0, True], ["B", "2026-01", None, 80.0, False]]
TABLE = {"fields": FIELDS, "rows": ROWS}

SCRIPT = """
import pandas as pd
df = Alteryx.read("#1")
df = df.sort_values(["CUSTOMER", "PERIOD"]).reset_index(drop=True)
out = []
for customer, group in df.groupby("CUSTOMER", sort=False):
    deferred = 0.0
    for _, row in group.iterrows():
        if bool(row["CANCELLED"]):
            deferred = 0.0
        billed = 0.0 if pd.isna(row["BILLED"]) else float(row["BILLED"])
        recognized = min(billed + deferred, float(row["CAP"]))
        deferred = billed + deferred - recognized
        out.append({"CUSTOMER": customer, "PERIOD": row["PERIOD"],
                    "RECOGNIZED": round(recognized, 2), "DEFERRED": round(deferred, 2)})
Alteryx.write(pd.DataFrame(out), 1)
"""


def _node(script=SCRIPT):
    return {"tool_id": "3", "type": "python", "config": {"script": script}}


def test_shim_runs_the_script_and_types_the_output():
    result = sim.run_python_tool(SCRIPT, [TABLE])
    assert set(result) == {1}
    out = result[1]
    assert [f["name"] for f in out["fields"]] == ["CUSTOMER", "PERIOD", "RECOGNIZED", "DEFERRED"]
    assert [f["type"] for f in out["fields"]] == ["V_WString", "V_WString", "Double", "Double"]
    assert out["rows"] == [["A", "2026-01", 80.0, 20.0], ["A", "2026-02", 70.0, 0.0],
                           ["A", "2026-03", 10.0, 0.0], ["B", "2026-01", 0.0, 0.0]]


def test_nan_becomes_null_and_ints_bools_dates_map_to_alteryx_types():
    script = """
import pandas as pd
df = Alteryx.read("#1")
out = pd.DataFrame({"N": [1, 2], "F": [1.5, float("nan")], "B": [True, False],
                    "S": ["x", None], "D": pd.to_datetime(["2026-01-02", None])})
Alteryx.write(out, 2)
"""
    out = sim.run_python_tool(script, [TABLE])[2]
    assert [f["type"] for f in out["fields"]] == ["Int64", "Double", "Bool", "V_WString", "DateTime"]
    assert out["rows"] == [[1, 1.5, True, "x", "2026-01-02 00:00:00"], [2, None, False, None, None]]


@pytest.mark.parametrize("bad", [
    "import os\nAlteryx.write(Alteryx.read('#1'), 1)",
    "open('x.txt', 'w')",
    "import subprocess",
    "__import__('socket')",
    "import pandas as pd\npd.read_csv('x.csv')",
])
def test_sandbox_refuses_filesystem_network_and_disallowed_imports(bad):
    with pytest.raises(sim.Unsupported):
        sim.run_python_tool(bad, [TABLE])


def test_sim_python_reads_connections_in_order_and_returns_only_written_anchors():
    second = {"fields": FIELDS[:1], "rows": [["Z"]]}
    node = _node("Alteryx.write(Alteryx.read('#2'), 3)")
    result = sim.sim_python(node, {"Input": [TABLE, second]}, sim._Context({}, {}, {}, [], {}))
    assert list(result) == ["3"]
    assert result["3"]["rows"] == [["Z"]]


def test_a_script_error_is_reported_as_unsupported_with_the_tool_id():
    with pytest.raises(sim.Unsupported, match="tool 3"):
        sim.sim_python(_node("raise ValueError('boom')"), {"Input": [TABLE]}, sim._Context({}, {}, {}, [], {}))
```

`tests/test_parse_python_tool.py`:

```python
"""A Python tool parses to a `python` node whose config carries the script; it is its own segment."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from parsers import plugin_map, tool_config
import segment as segmenter

XML = """<Configuration><Script>import pandas as pd
df = Alteryx.read("#1")
Alteryx.write(df, 1)</Script></Configuration>"""


def test_plugin_maps_to_python_with_one_input_and_five_outputs():
    assert plugin_map.classify("AlteryxBasePluginsGui.PythonTool.PythonTool", None) == "python"
    anchors = plugin_map.anchors_of("python")
    assert anchors["in"] == ["Input"]
    assert anchors["out"] == {"Output1": "1", "Output2": "2", "Output3": "3", "Output4": "4", "Output5": "5"}


def test_config_carries_the_script_verbatim():
    config = tool_config.parse_config("python", ET.fromstring(XML))
    assert config == {"script": 'import pandas as pd\ndf = Alteryx.read("#1")\nAlteryx.write(df, 1)'}


def test_a_python_node_is_always_its_own_segment():
    dag = {"nodes": [
        {"tool_id": "1", "type": "input", "container_id": None},
        {"tool_id": "2", "type": "filter", "container_id": None},
        {"tool_id": "3", "type": "python", "container_id": None},
        {"tool_id": "4", "type": "summarize", "container_id": None},
        {"tool_id": "5", "type": "output", "container_id": None},
    ], "edges": [
        {"src": "1", "src_anchor": "Output", "dst": "2", "dst_anchor": "Input"},
        {"src": "2", "src_anchor": "T", "dst": "3", "dst_anchor": "Input", "dst_order": 1},
        {"src": "3", "src_anchor": "1", "dst": "4", "dst_anchor": "Input"},
        {"src": "4", "src_anchor": "Output", "dst": "5", "dst_anchor": "Input"},
    ]}
    result = segmenter.segment(dag, min_tools=1)
    groups = [sorted(g) for g in result["segments"].values()] if isinstance(result.get("segments"), dict) else result["segments"]
    assert ["3"] in [sorted(g) for g in groups]
```

(Adjust the last assertion to `segment()`'s real return shape — read `scripts/segment.py`'s docstring — without weakening it: the python node must be alone in its segment.)

Run: `.venv/Scripts/python.exe -m pytest tests/test_alteryx_sim_python.py tests/test_parse_python_tool.py`
Expected: FAIL (`run_python_tool` missing; plugin unknown).

- [ ] **Step 2: Parser and segmenter**

`plugin_map.py`: add `"AlteryxBasePluginsGui.PythonTool.PythonTool": "python",` to `PLUGIN_TYPES` (after `RunCommand`; comment: `# verify against your Alteryx version: the Python tool's plugin id is taken from this repo's samples`) and to `ANCHORS`: `"python": {"in": ["Input"], "out": {"Output1": "1", "Output2": "2", "Output3": "3", "Output4": "4", "Output5": "5"}},`.

`tool_config.py`: add

```python
def _python(config: ET.Element) -> dict:
    """This repo's Python tool keeps its code in <Script>; real Alteryx stores a notebook JSON
    (dag-contract §4 says how to extend this when migrating such workflows)."""
    return {"script": (_find(config, "Script") or "").strip()}
```

and `"python": _python,` in `PARSERS`.

`segment.py`, in `is_hard`: `return nodes_by_id[e["src"]]["type"] in ("macro", "python") or nodes_by_id[e["dst"]]["type"] in ("macro", "python")` and update the docstring: "Macros and Python tools are always their own segment (a Python tool becomes a Snowpark procedure)". Also update the ordering-protection warning text if it names macros only.

Check the parser's connection handling gives a `python` node's `Input` connections a `dst_order` from `name="#N"` exactly as `union` gets it (grep `dst_order` in `scripts/parse.py`); if the rule is keyed on type `union`, extend it to `python`.

- [ ] **Step 3: Simulator**

In `alteryx_sim.py` add (near `sim_run_command`/`UNSUPPORTED_TYPES`):

```python
PYTHON_TOOL_ALLOWED_MODULES = frozenset({"pandas", "numpy", "re", "math", "datetime", "decimal"})
_PYTHON_TOOL_BUILTINS = {name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
                         for name in ("abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "isinstance",
                                      "len", "list", "max", "min", "range", "round", "set", "sorted", "str", "sum",
                                      "tuple", "zip", "reversed", "ValueError", "KeyError", "TypeError", "Exception")}


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".")[0]
    if level != 0 or root not in PYTHON_TOOL_ALLOWED_MODULES:
        raise Unsupported(f"python tool: import of {name!r} is not allowed (allowed: "
                          f"{', '.join(sorted(PYTHON_TOOL_ALLOWED_MODULES))})")
    return importlib.import_module(name)


def _pandas_to_table(pdf) -> dict:
    """DataFrame -> Table: int64->Int64, float64->Double, bool->Bool, datetime64->DateTime,
    everything else V_WString; NaN/NaT -> NULL. Column order is the frame's."""
    import pandas as pd
    fields, columns = [], []
    for name in pdf.columns:
        series = pdf[name]
        kind = str(series.dtype)
        if kind.startswith("int"):
            fields.append(_field(str(name), "Int64", 8)); columns.append([None if pd.isna(v) else int(v) for v in series])
        elif kind.startswith("float"):
            fields.append(_field(str(name), "Double", 8)); columns.append([None if pd.isna(v) else float(v) for v in series])
        elif kind == "bool":
            fields.append(_field(str(name), "Bool", 1)); columns.append([bool(v) for v in series])
        elif kind.startswith("datetime64"):
            fields.append(_field(str(name), "DateTime", 19))
            columns.append([None if pd.isna(v) else v.strftime("%Y-%m-%d %H:%M:%S") for v in series])
        else:
            fields.append(_field(str(name), "V_WString", 254))
            columns.append([None if v is None or (isinstance(v, float) and math.isnan(v)) else str(v) for v in series])
    return _table(fields, [list(row) for row in zip(*columns)] if columns else [])


def _table_to_pandas(table: dict):
    import pandas as pd
    names = [f["name"] for f in table["fields"]]
    pdf = pd.DataFrame([list(r) for r in table["rows"]], columns=names)
    for f in table["fields"]:
        if f["type"] in formula.FLOAT_TYPES:
            pdf[f["name"]] = pd.to_numeric(pdf[f["name"]], errors="coerce").astype("float64")
        elif f["type"] == "Bool":
            pdf[f["name"]] = pdf[f["name"]].astype("boolean")
    return pdf


def run_python_tool(script: str, inputs: list[dict]) -> dict[int, dict]:
    """Run one Python tool script in the sandbox. `inputs` are the tables on the tool's input
    connections in `#1`, `#2`, … order. Returns the tables written to anchors 1..5."""
    written: dict[int, dict] = {}

    class Alteryx:  # the shim the real tool exposes
        @staticmethod
        def read(name: str):
            index = int(str(name).lstrip("#")) - 1
            if not 0 <= index < len(inputs):
                raise Unsupported(f"python tool: Alteryx.read({name!r}) but only {len(inputs)} input(s) are connected")
            return _table_to_pandas(inputs[index])

        @staticmethod
        def write(pdf, anchor: int) -> None:
            if not 1 <= int(anchor) <= 5:
                raise Unsupported(f"python tool: Alteryx.write(..., {anchor}) is not an output anchor 1..5")
            written[int(anchor)] = _pandas_to_table(pdf)

    namespace = {"__builtins__": {**_PYTHON_TOOL_BUILTINS, "__import__": _guarded_import}, "Alteryx": Alteryx}
    try:
        exec(compile(script, "<python tool>", "exec"), namespace)  # noqa: S102 -- the sandbox above
    except Unsupported:
        raise
    except Exception as exc:  # the script's own failure is a simulator refusal, not a crash
        raise Unsupported(f"python tool script failed: {type(exc).__name__}: {exc}") from exc
    return written


def sim_python(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """The Python tool: the embedded script runs in a sandbox with the Alteryx.read/write shim."""
    inputs = list(inputs_by_anchor.get("Input") or [])
    try:
        written = run_python_tool(node["config"].get("script") or "", inputs)
    except Unsupported as exc:
        raise Unsupported(f"tool {node['tool_id']}: {exc}") from exc
    return {str(anchor): table for anchor, table in sorted(written.items())}
```

Add `import importlib` and `import math` at the top if absent; register `"python": sim_python` in `SIMULATORS`. `open`/`exec`/`eval` are absent from the sandbox builtins, so `open('x.txt', 'w')` raises `NameError`, which the wrapper turns into `Unsupported`; `pd.read_csv('x.csv')` fails inside pandas with a `FileNotFoundError` → `Unsupported`. `Unsupported` is the simulator's existing refusal class (line ~40).

How `inputs_by_anchor["Input"]` is ordered: `_execute` collects a node's inputs per anchor; confirm it keeps `dst_order` (as `sim_union` needs) and reuse that ordering.

- [ ] **Step 4: Docs**

`dag-contract.md` §2 table: `| AlteryxBasePluginsGui.PythonTool.PythonTool | python | Input (connections #1..#n, ordered) | 1..5 (Output1..Output5) | verify the plugin id against your Alteryx version |`. §4: `python: {"script": "<the tool's code>"} — this repo's samples keep the code in <Configuration><Script>; a real Alteryx workflow stores a Jupyter notebook JSON, to be extracted into the same "script" key.` `simulator-semantics.md`: a §"Python tool" stating the sandbox, the shim, the dtype mapping, NaN → NULL, and that the shim is a model of the tool (assumption — verify).

Run: both new test files + `tests/test_segment.py tests/test_parse.py tests/test_alteryx_sim.py` (whatever exists) — all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/parsers/plugin_map.py scripts/parsers/tool_config.py scripts/segment.py scripts/dev/alteryx_sim.py docs/reference/dag-contract.md docs/reference/simulator-semantics.md tests/test_alteryx_sim_python.py tests/test_parse_python_tool.py
git commit -m "feat: the Python tool — parsed, segmented alone, simulated in a sandbox with the Alteryx.read/write shim"
```

---

### Task 2: `scripts/target_check.py`, `TARGET_CLASS`, preference plumbing

**Files:**
- Modify: `scripts/parsers/plugin_map.py` (`TARGET_CLASS`, `node_class`), `scripts/dev/build_samples.py` (`output_target` into the manifest), `mappings/global.yaml`
- Create: `scripts/target_check.py`
- Test: `tests/test_target_check.py` (new), `tests/test_build_samples.py` (one test; find the existing seed test)

**Interfaces:**
- Consumes: `Repo`, `read_json`, `write_json`, `read_yaml` (`lib`), `plugin_map.node_class`.
- Produces: `plugin_map.TARGET_CLASS: dict[str, str]`, `plugin_map.node_class(tool_type) -> "sql"|"snowpark"|"manual"|"unknown"`; `target_check.target_check(repo, wf_id, prefer) -> dict` (the targets.json content, also written); `target_check.dbt_blockers(...)`; CLI `target_check.py <wf> [--prefer procedures|dbt] [--root .]` exit 0 / 1 (unknown nodes) / 2.

- [ ] **Step 1: Failing tests** — `tests/test_target_check.py`:

```python
"""target_check.py: deterministic target proposal per segment and workflow output kind."""
from __future__ import annotations

from lib.io import read_json, write_json, write_yaml
from lib.paths import Repo
from parsers import plugin_map
import target_check as tc

WF = "wf_0009"


def _dag(nodes):
    return {"workflow": WF, "nodes": [{"tool_id": i, "type": t, "config": c} for i, t, c in nodes], "edges": []}


def _seg(repo, seg, nodes):
    write_json(repo.seg(WF, seg, "dag.json"), {"workflow": WF, "segment": seg,
               "nodes": [{"tool_id": i, "type": t, "config": c} for i, t, c in nodes], "edges": [], "inbound": [], "outbound": []})


def build(tmp_path, *, nodes, segments, outputs):
    repo = Repo(tmp_path)
    write_json(repo.wf(WF, "parsed", "dag.json"), _dag(nodes))
    write_json(repo.wf(WF, "segments", "order.json"), [[s] for s in segments])
    for seg in segments:
        _seg(repo, seg, [n for n in nodes if n[0] in segments[seg]])
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), {"sources": {}, "outputs": outputs})
    return repo


PLAIN = [("1", "input", {}), ("2", "filter", {}), ("5", "output", {"pre_sql": None, "post_sql": None})]
OUT_OVERWRITE = {"out/a.yxdb": {"snowflake": "A.B.C", "logical": "C", "mode": "overwrite", "keys": [], "tool_ids": ["5"]}}


def test_node_class_table():
    assert plugin_map.node_class("python") == "snowpark"
    assert plugin_map.node_class("run_command") == "manual"
    assert plugin_map.node_class("filter") == "sql"
    assert plugin_map.node_class("unknown") == "unknown"
    assert plugin_map.node_class("wat") == "unknown"


def test_all_sql_procedures_preference(tmp_path):
    repo = build(tmp_path, nodes=PLAIN, segments={"seg_01": {"1", "2", "5"}}, outputs=OUT_OVERWRITE)
    result = tc.target_check(repo, WF, "procedures")
    assert result["segments"] == {"seg_01": "sql"}
    assert result["output_kind"] == "procedures"
    assert result["reason"] == "preference procedures"
    assert result["dbt_blockers"] == []
    assert read_json(repo.wf(WF, "segments", "targets.json")) == result


def test_python_node_makes_its_segment_snowpark_and_blocks_dbt(tmp_path):
    nodes = PLAIN + [("3", "python", {"script": "pass"})]
    repo = build(tmp_path, nodes=nodes, segments={"seg_01": {"1", "2"}, "seg_02": {"3"}, "seg_03": {"5"}}, outputs=OUT_OVERWRITE)
    result = tc.target_check(repo, WF, "dbt")
    assert result["segments"] == {"seg_01": "sql", "seg_02": "snowpark", "seg_03": "sql"}
    assert result["nodes"] == {"3": "snowpark"}
    assert result["output_kind"] == "procedures"
    assert result["dbt_blockers"] == [{"segment": "seg_02", "kind": "snowpark_segment"}]
    assert result["reason"].startswith("dbt refused: ")


def test_dbt_granted_when_preferred_and_feasible(tmp_path):
    outputs = {**OUT_OVERWRITE, "out/b.yxdb": {"snowflake": "A.B.D", "logical": "D", "mode": "merge", "keys": ["ID"], "tool_ids": ["5"]}}
    repo = build(tmp_path, nodes=PLAIN, segments={"seg_01": {"1", "2", "5"}}, outputs=outputs)
    result = tc.target_check(repo, WF, "dbt")
    assert result["output_kind"] == "dbt"
    assert result["reason"] == "preference dbt; every segment is sql and every output is dbt-expressible"


def test_every_dbt_blocker(tmp_path):
    cases = [
        ({"out/a.yxdb": {"snowflake": "A.B.C", "logical": "C", "mode": "merge", "keys": [], "tool_ids": ["5"]}}, PLAIN, "merge_without_keys"),
        ({"out/a.yxdb": {"snowflake": "A.B.C", "logical": "C", "mode": "update_only", "keys": [], "tool_ids": ["5"]}}, PLAIN, "write_mode_unsupported"),
        (OUT_OVERWRITE, [("1", "input", {}), ("5", "output", {"pre_sql": "DELETE FROM X; DELETE FROM Y", "post_sql": None})], "presql_not_plain"),
        (OUT_OVERWRITE, [("1", "input", {}), ("5", "output", {"pre_sql": None, "post_sql": "CREATE TABLE Z AS SELECT 1"})], "postsql_not_plain"),
        (OUT_OVERWRITE, PLAIN + [("9", "run_command", {})], "manual_node"),
        (OUT_OVERWRITE, PLAIN + [("9", "unknown", {})], "unknown_node"),
        ({}, PLAIN, "no_outputs"),
    ]
    for outputs, nodes, kind in cases:
        repo = build(tmp_path / kind, nodes=nodes, segments={"seg_01": {n[0] for n in nodes}}, outputs=outputs)
        result = tc.target_check(repo, WF, "dbt")
        assert result["output_kind"] == "procedures", kind
        assert kind in [b["kind"] for b in result["dbt_blockers"]], kind


def test_cli_exit_codes(tmp_path, capsys):
    repo = build(tmp_path, nodes=PLAIN + [("9", "unknown", {})], segments={"seg_01": {"1", "2", "5", "9"}}, outputs=OUT_OVERWRITE)
    assert tc.main([WF, "--root", str(tmp_path)]) == 1, "unknown nodes: written, exit 1"
    assert read_json(repo.wf(WF, "segments", "targets.json"))["nodes"]["9"] == "unknown"
    assert tc.main(["wf_0404", "--root", str(tmp_path)]) == 2
    assert "wf_0404" in capsys.readouterr().err
```

Run: FAIL (`node_class` missing, module missing).

- [ ] **Step 2: Implement**

`plugin_map.py`:

```python
# Which output target a tool type needs (cookbook §8.2, made machine-readable). Anything not
# listed and known is `sql`; a type the map does not know stays `unknown`.
TARGET_CLASS: dict[str, str] = {
    "python": "snowpark",
    "r": "manual", "run_command": "manual", "download": "manual", "email": "manual",
    "render": "manual", "spatial": "manual",
}


def node_class(tool_type: str) -> str:
    if tool_type in TARGET_CLASS:
        return TARGET_CLASS[tool_type]
    if tool_type in ANCHORS and tool_type != "unknown":
        return "sql"
    return "unknown"
```

`scripts/target_check.py` (full):

```python
"""Propose the output target of every segment and the workflow's output kind (spec §3.1).

    target_check.py <wf_id> [--prefer procedures|dbt] [--root .]

Writes segments/targets.json. Exit 0 written; 1 written but the workflow has unknown nodes; 2 usage.
"""
from __future__ import annotations

import argparse
import re
import sys
import traceback
from collections.abc import Sequence

from lib.io import read_json, read_yaml, write_json
from lib.paths import Repo, add_root_arg
from parsers.plugin_map import node_class

DBT_MODES = {"overwrite", "append", "merge"}
PLAIN_STATEMENT = re.compile(r"^\s*(DELETE|UPDATE|INSERT|TRUNCATE|CALL)\b", re.IGNORECASE)
DATA_LESS = {"container", "comment", "interface", "action", "browse"}


def _plain(sql: str | None) -> bool:
    if not sql or not sql.strip():
        return True
    statements = [s for s in sql.split(";") if s.strip()]
    return len(statements) == 1 and bool(PLAIN_STATEMENT.match(statements[0]))


def dbt_blockers(segment_targets: dict[str, str], nodes: dict[str, str], seg_nodes: dict[str, list[dict]],
                 outputs: dict) -> list[dict]:
    blockers: list[dict] = []
    for seg, target in segment_targets.items():
        if target == "snowpark":
            blockers.append({"segment": seg, "kind": "snowpark_segment"})
        for node in seg_nodes[seg]:
            cls = nodes.get(node["tool_id"])
            if cls == "manual":
                blockers.append({"segment": seg, "kind": "manual_node", "tool_id": node["tool_id"]})
            elif cls == "unknown":
                blockers.append({"segment": seg, "kind": "unknown_node", "tool_id": node["tool_id"]})
            if node["type"] == "output":
                cfg = node.get("config") or {}
                if not _plain(cfg.get("pre_sql")):
                    blockers.append({"segment": seg, "kind": "presql_not_plain", "tool_id": node["tool_id"]})
                if not _plain(cfg.get("post_sql")):
                    blockers.append({"segment": seg, "kind": "postsql_not_plain", "tool_id": node["tool_id"]})
    if not outputs:
        blockers.append({"segment": None, "kind": "no_outputs"})
    for key, entry in sorted(outputs.items()):
        mode = (entry or {}).get("mode")
        seg = next((s for s, ns in seg_nodes.items() if any(n["tool_id"] in (entry.get("tool_ids") or []) for n in ns)), None)
        if mode not in DBT_MODES:
            blockers.append({"segment": seg, "kind": "write_mode_unsupported", "output": key, "mode": mode})
        elif mode == "merge" and not entry.get("keys"):
            blockers.append({"segment": seg, "kind": "merge_without_keys", "output": key})
    return blockers


def target_check(repo: Repo, wf_id: str, prefer: str) -> dict:
    dag = read_json(repo.wf(wf_id, "parsed", "dag.json"))
    order = read_json(repo.wf(wf_id, "segments", "order.json"))
    segments = [seg for wave in order for seg in wave]
    seg_nodes = {seg: [n for n in read_json(repo.seg(wf_id, seg, "dag.json"))["nodes"]
                       if n.get("type") not in DATA_LESS] for seg in segments}
    nodes = {n["tool_id"]: node_class(n.get("type") or "unknown") for n in dag["nodes"]
             if n.get("type") not in DATA_LESS}
    segment_targets = {seg: ("snowpark" if any(nodes.get(n["tool_id"]) == "snowpark" for n in ns) else "sql")
                       for seg, ns in seg_nodes.items()}
    mappings_path = repo.wf(wf_id, "intake", "mappings.yaml")
    outputs = (read_yaml(mappings_path) or {}).get("outputs") or {} if mappings_path.is_file() else {}
    blockers = dbt_blockers(segment_targets, nodes, seg_nodes, outputs)
    if prefer == "dbt" and not blockers:
        kind, reason = "dbt", "preference dbt; every segment is sql and every output is dbt-expressible"
    elif prefer == "dbt":
        kind = "procedures"
        reason = "dbt refused: " + "; ".join(sorted({f"{b['segment'] or 'workflow'} {b['kind']}" for b in blockers}))
    else:
        kind, reason = "procedures", "preference procedures"
    result = {"preference": prefer, "output_kind": kind, "reason": reason, "dbt_blockers": blockers,
              "segments": segment_targets,
              "nodes": {tool_id: cls for tool_id, cls in sorted(nodes.items()) if cls != "sql"}}
    write_json(repo.wf(wf_id, "segments", "targets.json"), result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id")
    parser.add_argument("--prefer", choices=["procedures", "dbt"], default="procedures")
    add_root_arg(parser)
    args = parser.parse_args(argv)
    try:
        result = target_check(Repo(args.root), args.wf_id, args.prefer)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"target_check: {args.wf_id}: {exc}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 2
    print(f"{args.wf_id}: output_kind={result['output_kind']} segments={result['segments']}")
    return 1 if "unknown" in result["nodes"].values() else 0


if __name__ == "__main__":
    sys.exit(main())
```

(`add_root_arg` lives wherever `parse.py` imports it from — reuse the same import.) If the output tool config keys are not `pre_sql`/`post_sql`, use the names `tool_config._output()` really produces and update the test fixtures to match — the names in the code must match the parser's, never a guess.

`build_samples.py` seed: after `manifest["segmentation"] = …` add `if sample.get("output_target"): manifest["output_target"] = sample["output_target"]`. `mappings/global.yaml` `program`: add `output_target: procedures   # procedures | dbt — what the organisation wants; target_check.py honours it only when the workflow is dbt-expressible` and `snowpark_runtime: "3.11"   # RUNTIME_VERSION for LANGUAGE PYTHON procedures. Verify the versions your Snowflake account offers.` `tests/test_foundations.py` (or wherever global.yaml keys are asserted) is updated if it pins the key list. `test_build_samples.py`: a seed test asserting `manifest["output_target"] == "dbt"` for a sample.json carrying it and the key absent otherwise.

Run the new tests + `tests/test_build_samples*.py tests/test_foundations.py tests/test_intake*.py` (the global.yaml fixture helper blanks only sources/outputs, so the new program keys flow through) — green.

- [ ] **Step 3: Commit**

```bash
git add scripts/parsers/plugin_map.py scripts/target_check.py scripts/dev/build_samples.py mappings/global.yaml tests/test_target_check.py tests/test_build_samples.py
git commit -m "feat: target_check.py proposes sql/snowpark per segment and procedures/dbt per workflow from the DAG and the write modes"
```

---

### Task 3: Snowpark procedure rules, renderer and `compile_check.py --target`

> **Superseded during execution (rulings, see the phase's rulings file):** the rule code below is the
> plan's first argument, not what was merged. The merged `scripts/lib/snowpark_rules.py` is structural and
> conservative: `session` may appear only as the receiver of `.table(...)` / `.create_dataframe(...)` inside
> the single top-level `run` (binding, passing, aliasing, `getattr`, nested/class-level or lambda use are
> `rule:session_scope`); `.table()` / `.save_as_table()` take exactly one positional literal or C4-parameter
> f-string and no keywords (`rule:table_names`); `sql_expr`, `call_function`, `call_builtin`, `function`,
> `call_udf`, `call_table_function`, `table_function` are refused as names, attributes or from-imports
> (`rule:no_raw_sql`); `getattr`/`setattr`/`delattr`/`vars` and ANY dunder access are refused (`rule:no_io`);
> exactly one `def run` may exist in the whole module; `# tool N:` comments are found through `tokenize`,
> not a regex over the raw text. `alteryx_to_snowpark` mirrors `alteryx_to_snowflake`'s string policy
> (`String(n)` → `StringType(n)`, `V_String` → unsized `StringType()`). `render_snowpark.py` exits 1 for a
> `$$` in the source (domain) and 2 for usage or any other failure.


**Files:**
- Create: `scripts/lib/snowpark_rules.py`, `scripts/render_snowpark.py`
- Modify: `scripts/lib/proc_runner.py` (`parse_proc`: `LANGUAGE PYTHON`), `scripts/compile_check.py` (`--target`), `scripts/lib/types_map.py` (`alteryx_to_snowpark`)
- Test: `tests/test_snowpark_rules.py`, `tests/test_render_snowpark.py`, `tests/test_compile_check_snowpark.py` (new)

**Interfaces:**
- Produces: `snowpark_rules.ALLOWED_MODULES`, `snowpark_rules.check_proc_py(source: str, wf_id: str, seg: str, contract: dict) -> list[str]` (empty = OK; each string is one named violation `rule:<name>: <detail>`); `render_snowpark.render(proc_py: str, wf_id: str, seg: str, runtime: str) -> str`, CLI `render_snowpark.py <wf> <seg> [--root .]`; `proc_runner.ProcInfo.language: "SQL"|"PYTHON"`; `compile_check.compile_check(repo, wf_id, seg, target="auto")`; `types_map.alteryx_to_snowpark(field) -> snowflake.snowpark.types.DataType`.

- [ ] **Step 1: Failing tests**

`tests/test_snowpark_rules.py`:

```python
from __future__ import annotations

import pytest

from lib import snowpark_rules as rules

CONTRACT = {"segment": "seg_02", "inputs": [{"from": "seg_01", "stream": "2_T", "table": "MIG_WORK.WF0006_SEG_01_OUT", "columns": []}],
            "outputs": [{"stream": "3_1", "table": "MIG_WORK.WF0006_SEG_02_OUT", "kind": "work", "logical": None, "columns": [], "keys": []}]}
GOOD = '''# tool 3: Python tool -- revenue schedule (pandas: sequential per customer)
import pandas as pd
from snowflake.snowpark.types import StructType, StructField, StringType, DoubleType


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()
    out = session.create_dataframe(pdf, schema=StructType([StructField("CUSTOMER", StringType(20))]))
    out.write.mode("overwrite").save_as_table("MIG_WORK.WF0006_SEG_02_OUT")
    return "OK"
'''


def test_the_good_procedure_passes():
    assert rules.check_proc_py(GOOD, "wf_0006", "seg_02", CONTRACT) == []


@pytest.mark.parametrize("mutation,rule", [
    (("import pandas as pd", "import pandas as pd\nimport os"), "rule:imports"),
    (("session.table(", "session.sql('select 1'); session.table("), "rule:no_session_sql"),
    (("return \"OK\"", "open('x'); return \"OK\""), "rule:no_io"),
    (("def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id)", "def run(session, a, b)"), "rule:signature"),
    (("# tool 3:", "# note:"), "rule:tool_comments"),
    (("MIG_WORK.WF0006_SEG_01_OUT", "SALES.RAW.ORDERS"), "rule:table_names"),
    (('save_as_table("MIG_WORK.WF0006_SEG_02_OUT")', 'save_as_table("MIG_WORK.WF0006_SEG_09_OUT")'), "rule:table_names"),
    (("def run(", "def helper(session):\n    return session\n\n\ndef run("), "rule:session_scope"),
])
def test_each_rule_names_its_violation(mutation, rule):
    old, new = mutation
    assert old in GOOD
    errors = rules.check_proc_py(GOOD.replace(old, new), "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith(rule) for e in errors), errors


def test_a_syntax_error_is_one_violation():
    errors = rules.check_proc_py("def run(:\n", "wf_0006", "seg_02", CONTRACT)
    assert len(errors) == 1 and errors[0].startswith("rule:syntax")
```

`tests/test_render_snowpark.py`:

```python
from __future__ import annotations

from lib.io import write_json, write_yaml
from lib.paths import Repo
from lib.proc_runner import parse_proc
import render_snowpark as rs

PY = 'def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):\n    return "OK"\n'


def test_render_is_deterministic_and_carries_the_c4_signature():
    a = rs.render(PY, "wf_0006", "seg_02", "3.11")
    b = rs.render(PY, "wf_0006", "seg_02", "3.11")
    assert a == b
    assert a.startswith("CREATE OR REPLACE PROCEDURE MIG_WORK.WF0006_SEG_02(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)")
    assert "LANGUAGE PYTHON" in a and "RUNTIME_VERSION = '3.11'" in a and "HANDLER = 'run'" in a and "EXECUTE AS CALLER" in a
    assert a.rstrip().endswith("$$;")
    assert PY in a
    info = parse_proc(a)
    assert info.language == "PYTHON"
    assert info.name.upper() == "MIG_WORK.WF0006_SEG_02"


def test_a_proc_py_containing_dollar_dollar_is_refused():
    try:
        rs.render(PY.replace('"OK"', '"$$"'), "wf_0006", "seg_02", "3.11")
    except ValueError as exc:
        assert "$$" in str(exc)
    else:
        raise AssertionError("$$ inside the body would end the wrapper early")


def test_cli_writes_proc_sql_next_to_proc_py(tmp_path, capsys):
    repo = Repo(tmp_path)
    write_yaml(tmp_path / "mappings" / "global.yaml", {"program": {"snowpark_runtime": "3.11"}})
    path = repo.seg("wf_0006", "seg_02", "proc.py")
    path.parent.mkdir(parents=True)
    path.write_text(PY, encoding="utf-8")
    assert rs.main(["wf_0006", "seg_02", "--root", str(tmp_path)]) == 0
    assert repo.seg("wf_0006", "seg_02", "proc.sql").read_text(encoding="utf-8") == rs.render(PY, "wf_0006", "seg_02", "3.11")
    assert rs.main(["wf_0006", "seg_09", "--root", str(tmp_path)]) == 2
```

`tests/test_compile_check_snowpark.py`: build a segment dir with the `GOOD` proc.py from the rules test (import it), a contract with `"target": "snowpark"`, run `render_snowpark.main`, then `compile_check.compile_check(repo, wf, seg)` → `status == "OK"`, `report["target"] == "snowpark"`; with `import os` added → status not OK and an error starting `rule:imports`; with `proc.sql` edited by hand → error `rule:render_mismatch`; `--target sql` on a python wrapper → the SQL path's own signature/parse errors (assert non-OK); `main([wf, seg, "--root", …])` exit codes 0/1/2 (2 when `proc.py` is missing).

Run: FAIL (modules missing).

- [ ] **Step 2: Implement**

`scripts/lib/snowpark_rules.py`:

```python
"""The rules a Snowpark procedure module must satisfy (spec §4.2), as one AST walk."""
from __future__ import annotations

import ast
import re

ALLOWED_MODULES = frozenset({"snowflake.snowpark", "snowflake.snowpark.functions", "snowflake.snowpark.types",
                             "pandas", "numpy", "re", "math", "datetime", "decimal"})
FORBIDDEN_NAMES = frozenset({"exec", "eval", "open", "__import__", "compile", "globals", "locals"})
FORBIDDEN_MODULES = frozenset({"os", "sys", "subprocess", "socket", "urllib", "requests", "http", "shutil", "pathlib"})
FORBIDDEN_SESSION = frozenset({"sql", "call"})
SIGNATURE = ["session", "src_db", "src_schema", "tgt_db", "tgt_schema", "run_id"]
_TOOL_COMMENT = re.compile(r"^\s*#\s*tool\s+(\S+)\s*:", re.MULTILINE)


def _module_allowed(name: str) -> bool:
    return name in ALLOWED_MODULES or any(name.startswith(a + ".") for a in ALLOWED_MODULES if a.count(".") == 0)


def _literal(node: ast.AST) -> str | None:
    """A string literal or an f-string whose only placeholders are the C4 parameters."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.value.id in SIGNATURE:
                parts.append("{" + v.value.id + "}")
            else:
                return None
        return "".join(parts)
    return None


def check_proc_py(source: str, wf_id: str, seg: str, contract: dict) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"rule:syntax: {exc.msg} (line {exc.lineno})"]
    wf_u, seg_u = wf_id.upper().replace("_", ""), seg.upper()
    own = f"MIG_WORK.{wf_u}_{seg_u}_OUT"
    allowed_tables = {own} | {f"{own}_{o['stream']}" for o in contract.get("outputs", []) if o.get("kind") == "work"}
    allowed_tables |= {i["table"] for i in contract.get("inputs", []) if i.get("table")}
    src_form = re.compile(r"^\{src_db\}\.\{src_schema\}\.[A-Z_][A-Z0-9_$]*$")
    tgt_form = re.compile(r"^\{tgt_db\}\.\{tgt_schema\}\.[A-Z_][A-Z0-9_$]*$")

    runs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"]
    if len(runs) != 1 or [a.arg for a in runs[0].args.args] != SIGNATURE:
        errors.append(f"rule:signature: expected exactly one `def run({', '.join(SIGNATURE)})`")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                if name.split(".")[0] in FORBIDDEN_MODULES or not _module_allowed(name):
                    errors.append(f"rule:imports: `{name}` is not on the allow-list")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errors.append(f"rule:no_io: `{node.id}` is not allowed")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "session" and node.attr in FORBIDDEN_SESSION:
            errors.append(f"rule:no_session_sql: `session.{node.attr}` is not allowed; use the DataFrame API")
        if isinstance(node, ast.FunctionDef) and node.name != "run" and any(a.arg == "session" for a in node.args.args):
            errors.append(f"rule:session_scope: only `run` may take `session` (found `{node.name}`)")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("table", "save_as_table") and node.args:
            text = _literal(node.args[0])
            if text is None:
                errors.append(f"rule:table_names: `{node.func.attr}` needs a literal or a C4-parameter f-string")
            elif not (text in allowed_tables or src_form.match(text) or tgt_form.match(text)):
                errors.append(f"rule:table_names: `{text}` is not this segment's input, output or a C4 source/target form")
    tools = {str(n["tool_id"]) for n in contract.get("nodes", [])} if contract.get("nodes") else set()
    commented = set(_TOOL_COMMENT.findall(source))
    for tool_id in sorted(tools - commented):
        errors.append(f"rule:tool_comments: no `# tool {tool_id}:` comment")
    if not tools and not commented:
        errors.append("rule:tool_comments: no `# tool <id>:` comment at all")
    return sorted(set(errors))
```

(`contract["nodes"]` is not a contract field; `compile_check` passes the segment's data nodes in — see below — and the rules test passes a contract without `nodes`, which triggers the "at all" check.)

`scripts/render_snowpark.py`:

```python
"""Render segments/<seg>/proc.sql from proc.py: the LANGUAGE PYTHON wrapper with the C4 signature.

    render_snowpark.py <wf_id> <seg> [--root .]
"""
from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Sequence

from lib.io import read_yaml
from lib.paths import Repo, add_root_arg

TEMPLATE = """CREATE OR REPLACE PROCEDURE MIG_WORK.{wf}_{seg}(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '{runtime}'
PACKAGES = ('snowflake-snowpark-python', 'pandas')
HANDLER = 'run'
EXECUTE AS CALLER
AS
$$
{body}
$$;
"""


def render(proc_py: str, wf_id: str, seg: str, runtime: str) -> str:
    if "$$" in proc_py:
        raise ValueError("proc.py must not contain `$$` (it would end the procedure body)")
    return TEMPLATE.format(wf=wf_id.upper().replace("_", ""), seg=seg.upper(), runtime=runtime,
                           body=proc_py.rstrip("\n"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id")
    parser.add_argument("seg")
    add_root_arg(parser)
    args = parser.parse_args(argv)
    repo = Repo(args.root)
    try:
        proc_py = repo.seg(args.wf_id, args.seg, "proc.py").read_text(encoding="utf-8")
        program = (read_yaml(repo.global_mappings) or {}).get("program") or {}
        runtime = str(program.get("snowpark_runtime") or "3.11")
        text = render(proc_py, args.wf_id, args.seg, runtime)
        target = repo.seg(args.wf_id, args.seg, "proc.sql")
        target.write_bytes(text.encode("utf-8"))
    except (FileNotFoundError, ValueError) as exc:
        print(f"render_snowpark: {args.wf_id}/{args.seg}: {exc}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 2
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`proc_runner.parse_proc`: recognise `LANGUAGE PYTHON`, set `ProcInfo.language` (`"SQL"` default), and for PYTHON keep `body` = the `$$…$$` text without splitting statements (the C4 signature parsing is shared). Keep every existing test green.

`compile_check.py`: `compile_check(repo, wf_id, seg, target="auto")`: resolve `auto` from `contract.get("target", "sql")`; for `snowpark`: read `proc.py` (missing → `FileNotFoundError` → exit 2), errors = `check_proc_py(source, wf_id, seg, {**contract, "nodes": data_nodes})` where `data_nodes` are the segment's `dag.json` nodes minus the data-less types (`container, comment, interface, action, browse`), plus `rule:render_mismatch` if `proc.sql` is absent or differs from `render(...)`, plus the signature check on `proc.sql` through `parse_proc`; report `{"status": "OK"|"ERROR", "target": "snowpark", "errors": [...], "statements": 0}` written to `compile_check.json` as today. CLI: `--target {auto,sql,snowpark}`.

`types_map.alteryx_to_snowpark(field)`: Int types → `LongType()`; `Float`/`Double` → `DoubleType()`; `FixedDecimal` → `DecimalType(size, scale)`; `Bool` → `BooleanType()`; `Date` → `DateType()`; `DateTime` → `TimestampType()`; `Time` → `TimeType()`; strings → `StringType(size)` (or `StringType()` when size is None); import lazily inside the function so `types_map` stays importable without Snowpark.

Run: the three new test files + `tests/test_proc_runner*.py tests/test_compile_check*.py` — green.

- [ ] **Step 3: Commit**

```bash
git add scripts/lib/snowpark_rules.py scripts/render_snowpark.py scripts/lib/proc_runner.py scripts/compile_check.py scripts/lib/types_map.py tests/test_snowpark_rules.py tests/test_render_snowpark.py tests/test_compile_check_snowpark.py
git commit -m "feat: Snowpark procedure rules, proc.py -> proc.sql renderer, compile_check --target snowpark"
```

---

### Task 4: Shared validation library and `validate_snowpark.py`

> **Superseded during execution (rulings):** `run_handler` takes the contract as well —
> `run_handler(repo, wf_id, seg, golden_set, contract, proc_path, run_id)`. The `_alteryx_for` contract-side
> mapping described below was removed: `_read_back` derives the actual table's fields from the REAL Snowpark
> schema (`session.table(fqn).schema`) through `types_map.snowpark_to_alteryx`, the exact inverse of
> `alteryx_to_snowpark`, so `compare.py`'s own schema check sees wrong types, extra and missing columns; a
> cell that still does not convert, or an unsupported Snowpark type, is a domain FAIL report (exit 1) through
> `validation.fail_report`, never a usage error; `FixedDecimal` carries its real precision and scale.
> `_load_module` sets `sys.dont_write_bytecode` around the import so no `__pycache__` appears beside a procedure.


**Files:**
- Create: `scripts/lib/validation.py`, `scripts/validate_snowpark.py`
- Modify: `scripts/validate_segment.py` (import from the library; behaviour unchanged)
- Test: `tests/test_validate_snowpark.py` (new); every existing `tests/test_validate_segment*.py` unchanged and green

**Interfaces:**
- `scripts/lib/validation.py` exports, moved verbatim from `validate_segment.py` and made public: `VERDICT_SEVERITY`, `worst_verdict`, `missing_table_report(actual_fqn)`, `combine(contract, golden_set, output_reports)`, `fail_report(contract, golden_set, error)`, `aggregate_sets(sets, reports)`, `clear_stale_reports(repo, wf_id, seg)`, `actual_table(wf_id, seg, output)`, `golden_path(repo, wf_id, seg, golden_set, output)`, `write_reports(repo, wf_id, seg, sets, reports, idempotent)` (the tail of `validate_segment()` that writes `validation*.json` and returns the aggregate), `expected_fqn(index)`; `validate_segment.py` keeps `_load_and_run`, `_run_one_set`, `_outputs_equal`, `_read_ordered_rows`, `validate_segment`, `main`, and re-exports the moved names under their old underscore names (`_combine = combine` …) so `tests/test_validate_segment*.py` monkeypatches keep working.
- `validate_snowpark.validate_snowpark(repo, wf_id, seg, golden_sets=None, *, proc_path=None) -> dict` (same report shape); `validate_snowpark.run_handler(repo, wf_id, seg, golden_set, proc_path, run_id) -> Session`; CLI `validate_snowpark.py <wf> <seg> [--set …] [--proc FILE] [--root .]`, exit 0 PASS* / 1 FAIL / 2 usage.

- [ ] **Step 1: Failing tests** — `tests/test_validate_snowpark.py`, mirroring `tests/test_validate_segment.py`'s `wf_0009` fixture (`build()` writes the same manifest/mappings/goldens/dag but `contract["target"] = "snowpark"`, `segments/seg_01/proc.py` instead of `proc.sql`, plus the rendered `proc.sql`):

```python
PROC_PY = '''# tool 2: Filter -- keep NOTE = 'keep'
from snowflake.snowpark.functions import col


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep")
    kept.write.mode("overwrite").save_as_table("MIG_WORK.WF0009_SEG_01_OUT")
    kept.write.mode("overwrite").save_as_table(f"{tgt_db}.{tgt_schema}.ITEMS_OUT")
    return "OK"
'''
PROC_PY_WRONG = PROC_PY.replace('== "keep"', '== "drop"')
PROC_PY_RAISES = PROC_PY.replace("    kept.write", "    raise RuntimeError('boom')\n    kept.write")
PROC_PY_RANDOM = PROC_PY.replace('kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep")',
                                 'import random\n    kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep").with_column("NOTE", col("NOTE"))\n    kept = session.create_dataframe([[r["ID"], r["NOTE"] + str(random.random())] for r in kept.collect()], schema=["ID", "NOTE"])')
PROC_PY_MISSING_TARGET = PROC_PY.replace('    kept.write.mode("overwrite").save_as_table(f"{tgt_db}.{tgt_schema}.ITEMS_OUT")\n', "")
```

Tests (same names as the SQL suite, `vsp.validate_snowpark` in place of `vs.validate_segment`): correct → `sets == {"normal": "PASS"}`, `idempotent is True`, both `validation.json` and `validation.normal.json` written, `report["target"] == "snowpark"`; wrong filter → `FAIL` with every cluster carrying `stream == "2_T"`; a raising handler → `verdict == "FAIL"`, `error` set, `diff_clusters == []`, report written, CLI exit 1; the random one → `idempotent is False` and `FAIL`; the missing target table → `FAIL` with `error` naming `MIGDB.MIG_WORK.ITEMS_OUT`; unknown segment / empty golden set list → `FileNotFoundError` / `ValueError` and nothing written; `--proc FILE`; CLI 0/1/2 with no traceback at exit 1; a second `validate_snowpark` call leaves no stale `validation.*.json` for a dropped set. (`random` is not on the allow-list — that is the point of the compile check, not of the validator; the validator runs what it is given.)

Run: FAIL (module missing).

- [ ] **Step 2: Extract the library**

Move the functions listed under Interfaces from `validate_segment.py` into `scripts/lib/validation.py` (public names), import them back into `validate_segment.py` and keep the old underscore aliases. Run `tests/test_validate_segment.py tests/test_validate_segment_fix_round_1.py` — 31 pass, unchanged.

- [ ] **Step 3: Implement `validate_snowpark.py`**

```python
"""Validate a Snowpark Python segment in the Snowpark Local Testing Framework (spec §5.2).

    validate_snowpark.py <wf_id> <seg> [--set NAME]... [--proc FILE] [--root .]
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import traceback
from collections.abc import Sequence
from pathlib import Path

import compare
from lib import validation as v
from lib.backend import DuckDBBackend
from lib.io import read_json, read_yaml
from lib.paths import Repo, add_root_arg
from lib.typed_csv import read_table
from lib.types_map import alteryx_to_snowpark, type_family
from load_golden import golden_view_schema, GOLDEN_SCHEMA, SANDBOX_DB

WORK_SCHEMA = "MIG_WORK"


def _session():
    from snowflake.snowpark import Session
    return Session.builder.configs({"local_testing": True}).create()


def _save(session, fqn: str, table: dict) -> None:
    from snowflake.snowpark.types import StructField, StructType
    schema = StructType([StructField(f["name"], alteryx_to_snowpark(f), True) for f in table["fields"]])
    rows = [list(_to_snowpark(v, f) for v, f in zip(row, table["fields"])) for row in table["rows"]]
    session.create_dataframe(rows, schema=schema).write.mode("overwrite").save_as_table(fqn)


def _to_snowpark(value, field):
    """typed_csv values -> what create_dataframe accepts: ISO strings become date/datetime objects."""
    import datetime as dt
    if value is None:
        return None
    if field["type"] == "Date":
        return dt.date.fromisoformat(value)
    if field["type"] == "DateTime":
        return dt.datetime.fromisoformat(value)
    return value


def load_set_snowpark(session, repo: Repo, wf_id: str, golden_set: str) -> dict:
    """Mirror of load_golden.load_set for a local Snowpark session: inputs under the golden view
    schema by logical name, targets_before under MIG_WORK. Returns the run args."""
    mappings = read_yaml(repo.wf(wf_id, "intake", "mappings.yaml")) or {}
    view_schema = golden_view_schema(wf_id, golden_set)
    for key, source in (mappings.get("sources") or {}).items():
        logical = source.get("logical")
        tool_ids = [str(t) for t in source.get("tool_ids") or []]
        if not logical or not tool_ids:
            raise ValueError(f"{wf_id}: source {key!r} has no logical name or tool ids")
        path = repo.wf(wf_id, "golden", "inputs", golden_set, f"{tool_ids[0]}.csv")
        _save(session, f"{SANDBOX_DB}.{view_schema}.{logical}", read_table(path))
    for key, output in (mappings.get("outputs") or {}).items():
        if output.get("mode") in ("append", "merge"):
            path = repo.wf(wf_id, "golden", "targets_before", golden_set, f"{output['logical']}.csv")
            _save(session, f"{SANDBOX_DB}.{WORK_SCHEMA}.{output['logical']}", read_table(path))
    return {"SRC_DB": SANDBOX_DB, "SRC_SCHEMA": view_schema, "TGT_DB": SANDBOX_DB, "TGT_SCHEMA": WORK_SCHEMA}


def _load_module(proc_path: Path):
    spec = importlib.util.spec_from_file_location(f"proc_{abs(hash(str(proc_path)))}", proc_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    if not callable(getattr(module, "run", None)):
        raise ValueError(f"{proc_path} has no run()")
    return module


def run_handler(repo: Repo, wf_id: str, seg: str, golden_set: str, contract: dict, proc_path: Path, run_id: str):
    session = _session()
    args = load_set_snowpark(session, repo, wf_id, golden_set)
    for entry in contract.get("inputs", []):
        if entry.get("stream"):
            path = repo.wf(wf_id, "golden", "intermediates", entry["from"], golden_set, f"{entry['stream']}.csv")
            _save(session, entry["table"], read_table(path))
    module = _load_module(proc_path)
    module.run(session, args["SRC_DB"], args["SRC_SCHEMA"], args["TGT_DB"], args["TGT_SCHEMA"], run_id)
    return session


def _read_back(session, fqn: str, columns: list[dict]) -> dict | None:
    """The actual table as a typed Table in the contract's declared types, or None if absent."""
    try:
        pdf = session.table(fqn).to_pandas()
    except Exception:
        return None
    fields = [{"name": c["name"], "type": _alteryx_for(c["type"]), "size": None, "scale": None} for c in columns]
    names = [c["name"] for c in columns]
    rows = []
    for record in pdf.to_dict("records"):
        rows.append([_coerce(record.get(n), c["type"]) for n, c in zip(names, columns)])
    return {"fields": fields, "rows": rows}
```

with `_alteryx_for(sql_type)` mapping the contract's Snowflake type family back to an Alteryx type for `backend.load_table` (`number` with scale 0 → `Int64`, other `number` → `FixedDecimal`, `float` → `Double`, `varchar` → `V_WString`, `boolean` → `Bool`, `date` → `Date`, `timestamp` → `DateTime`), `_coerce` turning pandas values into `typed_csv` values (NaN → None, `Timestamp` → ISO string, `numpy` scalars → Python), a `_run_one_set` that follows `validate_segment._run_one_set` step for step (fresh session → `run_handler` → for every `contract.outputs[]`: `_read_back(session, v.actual_table(wf_id, seg, output), output["columns"])`, `None` → `v.missing_table_report(fqn)`, else load expected (`read_table(v.golden_path(...))`) and actual into a fresh `DuckDBBackend` as `MIG_COMPARE.EXPECTED_<n>` / `MIG_COMPARE.ACTUAL_<n>` and `compare.compare(...)` with the same `tolerances`/`accepted_classes`/`approvals`/`segment_dag` arguments `validate_segment` passes; a Python exception from `run` → `v.fail_report` with `error`; idempotency: run twice in two sessions, compare every output's rows read back and fully sorted → `idempotent`, `idempotency_diff`), and `validate_snowpark(...)` doing the prerequisite checks (`contract.json`, `proc.py`, mappings, non-empty sets, `outputs[]` non-empty), `v.clear_stale_reports` first, the per-set loop, `v.aggregate_sets` + `v.write_reports`, adding `"target": "snowpark"` to the top-level report. `main` mirrors `validate_segment.main` exactly (0 PASS*, 1 FAIL, 2 usage/unexpected).

Run: `tests/test_validate_snowpark.py` green; `tests/test_validate_segment*.py` unchanged.

- [ ] **Step 4: Commit**

```bash
git add scripts/lib/validation.py scripts/validate_segment.py scripts/validate_snowpark.py tests/test_validate_snowpark.py
git commit -m "feat: validate_snowpark.py runs a Snowpark procedure in the local testing framework and judges it with compare.py; validation report shaping shared"
```

---

### Task 5: Sample `wf_0006` — sources, goldens, canned artefacts, broken variants, tests

**Files:**
- Create: `samples/wf_0006/{source/subscription_revenue.yxmd, sample.json, README.md}`, `samples/wf_0006/canned/{intake/plan.md, analysis.md, unsupported.json, docs/migration.md, segments/seg_01/{contract.json,proc.sql,translation_notes.md,review.json}, segments/seg_02/{contract.json,proc.py,proc.sql,translation_notes.md,review.json}, segments/seg_03/{…proc.sql…}}`, `samples/wf_0006/broken_sql/{broken.json, seg_02/01_cancellation_reset_ignored.py, seg_03/01_summarize_drops_period.sql}`
- Modify: `samples/_tools/make_golden_inputs.py` (wf_0006 block), `tests/helpers.py` (copy `proc.py` too), `tests/test_e2e_parity.py` (dispatch by target; add `wf_0006`), `tests/test_canned_artifacts.py` (Snowpark rules for `proc.py`, `proc.sql` ↔ render, target keys)

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: the sample the orchestrator task (6) and the offline run use.

- [ ] **Step 1: The workflow** — `subscription_revenue.yxmd`, written in the same XML shape as `samples/wf_0001/source/sales_summary.yxmd` (MetaInfo per tool, connections with names, `yxmdVer`):

| Tool | Plugin | Config | MetaInfo out |
|---|---|---|---|
| 1 | DbFileInput `C:\data\billing\subscriptions.yxdb` | | `CUSTOMER V_String 20, PERIOD V_String 7, BILLED Double 8, CAP Double 8, CANCELLED Bool 1` |
| 2 | Filter `[BILLED] > 0` | | same |
| 3 | `AlteryxBasePluginsGui.PythonTool.PythonTool` `<Script>` = the `SCRIPT` from `tests/test_alteryx_sim_python.py` verbatim | connection from 2's True named `#1` | `CUSTOMER V_WString 254, PERIOD V_WString 254, RECOGNIZED Double 8, DEFERRED Double 8` (what the shim types) |
| 4 | Summarize group `PERIOD`, `Sum RECOGNIZED → TOTAL_RECOGNIZED`, `Sum DEFERRED → TOTAL_DEFERRED`, `Count → CUSTOMERS` | | |
| 5 | DbFileOutput `C:\data\out\revenue_by_period.yxdb`, overwrite | | |

`sample.json`: id, title "Subscription revenue recognition", owner `wf_owner`, schedule, `segmentation: {"min_tools": 1, "max_tools": 40}`, `expected_terminal: "VALIDATED"`, `answers` for the input file key and the output key (`ANALYTICS.CURATED.REVENUE_BY_PERIOD`), `logical: {"1": "SUBSCRIPTIONS", "5": "REVENUE_BY_PERIOD"}`. `README.md` per the other samples (what each golden row exercises; the tier-T2 note; "the Python tool's plugin id is this repo's — verify").

`make_golden_inputs.py`: `SUBSCRIPTIONS` fields + `normal` (three customers, one cancellation mid-stream, one BILLED NULL, one BILLED 0 that the filter drops), `period_end` (a customer whose deferred balance is exactly the cap), `empty`, `edge` (a byte-identical duplicate row; a negative BILLED; a customer with a single cancelled row). Run the generator; commit its outputs.

`build_samples.py seed --only wf_0006` + `build` must succeed (the simulator runs the python tool). Verify segmentation is `seg_01 {1,2}`, `seg_02 {3}`, `seg_03 {4,5}`.

- [ ] **Step 2: Canned artefacts**

`seg_01/proc.sql`, `seg_03/proc.sql`: SQL procedures per C4 and the 13a patterns (NUMBER arithmetic for the sums; `# tool` comments). `seg_02/proc.py`:

```python
# tool 3: Python tool -- revenue schedule with carry-over. Sequential per customer (the deferred
# balance feeds the next period's cap check and resets on cancellation), so the rows are
# processed in pandas exactly as the Alteryx script does; the frame is small by construction.
import pandas as pd
from snowflake.snowpark.types import DoubleType, StringType, StructField, StructType


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()
    pdf = pdf.sort_values(["CUSTOMER", "PERIOD"]).reset_index(drop=True)
    out = []
    for customer, group in pdf.groupby("CUSTOMER", sort=False):
        deferred = 0.0
        for _, row in group.iterrows():
            if bool(row["CANCELLED"]):
                deferred = 0.0
            billed = 0.0 if pd.isna(row["BILLED"]) else float(row["BILLED"])
            recognized = min(billed + deferred, float(row["CAP"]))
            deferred = billed + deferred - recognized
            out.append([customer, row["PERIOD"], round(recognized, 2), round(deferred, 2)])
    schema = StructType([StructField("CUSTOMER", StringType(254)), StructField("PERIOD", StringType(254)),
                         StructField("RECOGNIZED", DoubleType()), StructField("DEFERRED", DoubleType())])
    session.create_dataframe(out, schema=schema).write.mode("overwrite").save_as_table("MIG_WORK.WF0006_SEG_02_OUT")
    return "OK"
```

`seg_02/proc.sql` = `render_snowpark.py wf_0006 seg_02` output. Contracts carry `"target": "sql"|"snowpark"`, `inputs[].from/stream/table` for seg_02 (from `seg_01`, stream `2_T`, table `MIG_WORK.WF0006_SEG_01_OUT`) and seg_03 (from `seg_02`, stream `3_1`, table `MIG_WORK.WF0006_SEG_02_OUT`), `outputs[]` with the work streams and the final target (`tool_id: "5"`), keys where unique (`CUSTOMER, PERIOD` for seg_02's stream; `PERIOD` for the target). `unsupported.json`: `{"tier": "T2", "unsupported": [], "unknown": []}`; `analysis.md` lists the target per segment and why; `plan.md`, `docs/migration.md` with a "Deployment" section that shows the Python procedure's DDL is `proc.sql` and names the runtime; `review.json` PASS; `translation_notes.md` for seg_02 says which tool needed pandas, that `to_pandas()` is bounded by the segment's row count, and the `- tool 3 has no CTE: it is a Python procedure` line is NOT used (the CTE rule does not apply to `proc.py`; the canned test below exempts `.py` segments from the CTE check and applies the Snowpark rules instead).

Every segment must pass every golden set through the right validator (`validate_segment` for seg_01/seg_03, `validate_snowpark` for seg_02) with `idempotent: true`, and `compile_check.py` (auto target) must exit 0 for all three.

- [ ] **Step 3: Broken variants and `broken.json`**

`broken_sql/seg_02/01_cancellation_reset_ignored.py`: the canned `proc.py` with the `if bool(row["CANCELLED"]): deferred = 0.0` lines removed and a `# BROKEN ON PURPOSE` header in Python comment form; `broken_sql/seg_03/01_summarize_drops_period.sql`: the seg_03 procedure grouping by nothing. Run each through its validator and record the OBSERVED `expect` (class, columns, stream) in `broken.json` (rows gain `"target": "snowpark"|"sql"`); both must FAIL with `needs_human: false`.

- [ ] **Step 4: Tests**

`tests/helpers.py::prepare_workflow`: copy `proc.py` when present (`for name in ("contract.json", "proc.sql", "proc.py")`). `tests/test_e2e_parity.py`: a helper `validate(repo, wf, seg, sets=None, proc_path=None)` that reads the segment's contract target and calls `validate_snowpark` or `validate_segment`; add `"wf_0006"` to the parametrization; `test_broken_migration_fails_with_the_right_class` uses `case["target"]`. `tests/test_canned_artifacts.py`: for a segment whose contract target is `snowpark`, require `proc.py`, assert `snowpark_rules.check_proc_py(...) == []`, assert `proc.sql == render(...)`, and skip the CTE check for it; assert every contract has `target` ∈ {`sql`, `snowpark`}; every `.py` broken variant still satisfies the rules (mirrors "every broken variant is still a C4 procedure"); `test_every_canned_procedure_has_the_c4_signature` runs `parse_proc` on the rendered wrapper too.

Run `tests/test_e2e_parity.py tests/test_canned_artifacts.py tests/test_samples_wellformed.py` — green, no skips.

- [ ] **Step 5: Commit**

```bash
git add samples/wf_0006 samples/_tools/make_golden_inputs.py tests/helpers.py tests/test_e2e_parity.py tests/test_canned_artifacts.py
git commit -m "feat: sample wf_0006 — a Python tool segment migrated as a Snowpark procedure, with goldens, canned artefacts and broken variants"
```

---

### Task 6: Orchestrator dispatch, mock replay, policy, agents, docs, offline run

> **Superseded during execution (rulings):** the rank sentence below is inverted — per spec §3.2 `sql` is
> the HIGHEST target and a raise is `snowpark` → `sql` (or `manual` → anything); the merged
> `TARGET_RANK` is `{manual: 0, snowpark: 1, sql: 2}`. `target_check.py` already implements `--prefer auto`,
> so no `programOutputTarget` helper exists; its exit 1 (written, unknown nodes) continues to the analyzer and
> only exit 2 is `script-error`. A contract whose target is `manual` never reaches the translator
> (`migrateSegment` parks it as `manual-segment`); a segment with no usable proposal parks as
> `target-missing: <seg>` (only the two spec reason formats exist); after a render exit 1 or a compile failure
> the fixer's task text quotes the failing script's redacted, 500-character-bounded diagnosis. The task was
> executed as 6A (Steps 1–2 and the docs) and 6B (Step 3, re-running ALL six samples because every manifest
> gained `output_kind` and every canned contract gained `"target": "sql"`).


**Files:**
- Modify: `orchestrator/types.ts` (`Manifest.output_kind?`, `output_target?`), `orchestrator/stages.ts` (`stageAnalyze`, `migrateSegment`, `agentFailureReason` unchanged), `orchestrator/runner.ts` (`MockRunner`: replay `.py`, target-aware validator), `orchestrator/policy.ts` (`ROLE_SCRIPTS`), `orchestrator/test/fakes.ts`, `orchestrator/test/stages.test.ts`, `orchestrator/test/policy.test.ts`
- Modify: `.github/agents/{analyzer,translator,reviewer,validator,fixer}.agent.md`, `.github/copilot-instructions.md`
- Create: `docs/reference/output-targets.md`; modify `README.md` (targets section, sample table, deployment), `docs/handoff-copilot-models.md` (pointer)
- Modify: `workflows/wf_0006/**` (committed product of the offline run), `tests/test_committed_workflows.py` (expects `wf_0006`)

**Interfaces:**
- Consumes: `scripts/target_check.py`, `render_snowpark.py`, `compile_check.py --target`, `validate_snowpark.py`, sample `wf_0006`.

- [ ] **Step 1: Failing node tests** (in `stages.test.ts`, using `fakes.ts` — extend the fake `py` dispatcher to know `scripts/target_check.py` (writes `segments/targets.json` from a scenario map), `scripts/render_snowpark.py` (writes `proc.sql` next to `proc.py`), `scripts/validate_snowpark.py` (writes a PASS `validation.json` like the SQL fake), and `compile_check.py` with `--target`; extend `seedWorkflow`'s canned tree so a scenario `snowpark:seg_02` gives seg_02 a contract with `target: "snowpark"` and a canned `proc.py`):

1. analyze runs `target_check.py` before the analyzer and passes `--prefer` from `manifest.output_target` (`dbt`) else `procedures`; `manifest.output_kind` is set from `targets.json`.
2. a contract that RAISES a target (`targets.json` says snowpark, contract says sql) parks analyze `NEEDS_HUMAN` with reason `target-mismatch: seg_02 raised snowpark to sql`; a LOWERED one (`sql` → `snowpark`) is accepted and logged.
3. a snowpark segment: after the translator, `render_snowpark.py` then `compile_check.py … --target snowpark` run; the validator role's task text names `validate_snowpark.py`; the fake validator writes `validation.json`; segment PASS. `render_snowpark.py` exit 2 → `script-error`; exit 1 → treated like a compile failure (next iteration).
4. a sql segment's calls are byte-identical to before (no `--target` argument, no render call).
5. `MockRunner` (real one, in `runner.test.ts`/`integration.test.ts` style): with `samples/wf_0006`-shaped canned files in a temp samples dir, `replaySql` copies `proc.py` for a snowpark segment and the `.py` broken variant on the fix-loop scenario; `replayValidator` spawns `validate_snowpark.py` for a snowpark contract.
6. policy: `analyzer` may run `scripts/target_check.py`; `translator`/`fixer` may run `render_snowpark.py`; `validator` may run `validate_snowpark.py`; `reviewer` may not run any of them; the translator may write `segments/seg_02/proc.py` (already allowed) and NOT `segments/seg_02/proc.sql` for a snowpark segment? — keep `proc.sql` writable (the renderer is a script the translator may run, and the lane already lists both); add the test that the renderer's output path is inside the lane.

Run: FAIL.

- [ ] **Step 2: Implement**

`stages.ts` `stageAnalyze`: after `segment.py` succeeds:

```ts
  const prefer = (m.output_target as string | undefined) ?? (await programOutputTarget(env)) ?? "procedures";
  const targets = await env.py("scripts/target_check.py", [m.id, "--prefer", prefer]);
  if (targets.code === 2) return scriptError(env, m, "analyze", "scripts/target_check.py", targets);
```

(`programOutputTarget` reads `mappings/global.yaml`'s `program.output_target` with a tiny YAML line scan or `env.py("scripts/…")`? — simplest and honest: pass nothing when the manifest has no value and let `target_check.py` read `global.yaml` itself: give the script `--prefer auto` meaning "manifest value if the orchestrator passed one, else `global.yaml`"; implement `auto` in `target_check.py` reading `repo.global_mappings` — add that branch and a test in Task 6.) The analyzer task text gains "read workflows/<id>/segments/targets.json and copy each segment's target into its contract.json (you may only lower a target)". The verify callback, after the existing checks: read `targets.json`; for every segment read `contract.json.target` (missing → return false); rank `sql < snowpark < manual`; a contract with a higher rank than the proposal → set `m.reasons.analyze = "target-mismatch: <seg> raised <proposal> to <contract>"` and return false (the escalate path then names it — check `escalate`'s reason precedence and make `reasons.analyze` win); set `m.output_kind = targets.output_kind === "dbt" && every contract target is sql ? "dbt" : "procedures"`.

`migrateSegment`: read the contract once per iteration (`readJsonOr<{target?: string}>`); `const target = contract.target === "snowpark" ? "snowpark" : "sql"`; for snowpark, after the agent wrote and before compile: `const rendered = await env.py("scripts/render_snowpark.py", [m.id, segment]); if (rendered.code === 2) return { verdict: "NEEDS_HUMAN", reason: "script-error" }; if (!rendered.ok) { lastReason = …; continue; }`; compile call gets `["--target", target]` only when snowpark; validator task text: `Validate segment … with scripts/validate_snowpark.py …` for snowpark; the fixer task mentions `proc.py`.

`runner.ts` `MockRunner.replaySql`: the artefact name is `proc.py` when the canned segment has one (`canned/segments/<seg>/proc.py` exists) — copy it (and the broken `.py` variant from `broken_sql/<seg>/*.py` on the broken scenarios, first in name order regardless of extension); `replayValidator`: read the workflow's `contract.json.target` and spawn `scripts/validate_snowpark.py` for snowpark. `policy.ts` `ROLE_SCRIPTS`: `analyzer: [..., "scripts/target_check.py"]`, `translator: [..., "scripts/render_snowpark.py"]`, `fixer` likewise, `validator: [..., "scripts/validate_snowpark.py"]`.

Agents: analyzer — the `target_check.py` step (after `segment.py`'s cuts), the lower-only rule, `contract.json.target`; translator — a "Snowpark segments" section with the §4.2 rules verbatim, `render_snowpark.py` then `compile_check.py … --target snowpark` as the done criterion; reviewer — blocking checks for `.py` (rules, `proc.sql` ↔ `proc.py`, sequential logic justified in notes); validator — `validate_snowpark.py` for `target: snowpark`; fixer — repair `proc.py`, never `proc.sql` directly. `.github/copilot-instructions.md`: one paragraph. `tests/test_agents_config.py` stays green (it checks frontmatter keys only).

`docs/reference/output-targets.md`: vocabulary, decision rules, the Snowpark artefact rules, file layout, deployment (`proc.sql` is the DDL; runtime/packages; `master.sql` unchanged), what the local double does not prove (spec §9), "dbt: phase 2". README: "Three output targets" section (SQL now, Snowpark now, dbt phase 2), `wf_0006` in the sample table, deployment steps for a Python procedure. Hand-off doc: pointer.

- [ ] **Step 3: Offline run for `wf_0006` and the committed product**

In a scratch root per README §6 (copy `scripts mappings catalog`, absolute python path), seed → run → `answer_samples.py` → run → no-op run for `wf_0006` only (`--only wf_0006`); confirm `translate=VALIDATED`, `seg_02` served by `validate_snowpark.py` (its `validation.json` has `"target": "snowpark"`), `master.sql` calls all three; then copy `workflows/wf_0006/` from the scratch root into the repo (the same way `workflows/wf_0001…5` were produced — see `docs/superpowers/build-reports/2026-09-18-pipeline/task-17-report.md`; no `.duckdb`, no `audit.jsonl`, no absolute paths, `confirmed_by: automation`), and extend `tests/test_committed_workflows.py`'s expected terminal states with `wf_0006: VALIDATED`. Re-run the README reproducibility check (`git archive`-style scratch copy → diff) for `wf_0006`.

Run all four suites: pytest (expect ≈1005 + new tests, 0 skipped), node (expect 150 + new), tsc clean, plus `tests/test_committed_workflows.py`.

- [ ] **Step 4: Commit**

```bash
git add orchestrator .github docs/reference/output-targets.md README.md docs/handoff-copilot-models.md workflows/wf_0006 tests/test_committed_workflows.py
git commit -m "feat: per-segment target dispatch — target_check in analyze, Snowpark render/check/validate in translate, mock replay of proc.py; wf_0006 offline run committed"
```

---

## Self-review (done while writing)

- Spec coverage, phase 1: §3.1 → Task 2; §3.2/§3.3 → Tasks 2 and 6; §4.2 → Task 3; §5.1/§5.2 → Tasks 3–4; §6 (procedures side) → Task 6; §7.1/§7.2 → Tasks 1 and 5; §8 agents/docs (Snowpark parts) → Task 6; §9 statements → Tasks 4 and 6 docs; §10 tests → each task. Phase 2 owns §4.3, §5.3, §7.3, the cookbook pages, `prompt_context.py` and the live tests.
- Names used consistently: `node_class`, `TARGET_CLASS`, `target_check`, `dbt_blockers`, `check_proc_py`, `ALLOWED_MODULES`, `render`, `ProcInfo.language`, `compile_check(repo, wf, seg, target="auto")`, `alteryx_to_snowpark`, `validation.{worst_verdict, missing_table_report, combine, fail_report, aggregate_sets, clear_stale_reports, actual_table, golden_path, write_reports}`, `validate_snowpark`, `run_handler`, `load_set_snowpark`, `run_python_tool`, `sim_python`, `PYTHON_TOOL_ALLOWED_MODULES`, `contract.json.target`, `segments/targets.json`, `manifest.output_kind`, `manifest.output_target`.
- Two things the implementers must read rather than assume, both called out in their tasks: the output tool's config key names in `tool_config._output()`, and `segment()`'s return shape.
