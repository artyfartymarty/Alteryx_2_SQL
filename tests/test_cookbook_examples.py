"""Cookbook v1 regression harness (plan Task 12b).

Every `cookbook/<tool>.md` page's "Snowflake pattern" is checked against the parity **oracle**
(`scripts/dev/alteryx_sim.py`), never against what the SQL happens to do: for each
`tests/cookbook_examples/<tool>/case.json`, the harness

1. loads every input CSV as `MIG_COOKBOOK.IN_<id>` on a DuckDB backend (`scripts/lib/backend.py`);
2. builds the one-tool `dag.json` the case describes (the input tool ids as source nodes, plus the
   one tool under test) and runs it through `simulate()` for the **expected** rows;
3. runs each `compare[].sql` file — the same SQL the page shows under "Snowflake pattern" — through
   the backend for the **actual** rows;
4. asserts `scripts/compare.py`'s `compare()` reports `verdict == "PASS"`.

So a pattern only stays in the cookbook if it reproduces the oracle's NULL, ordering and truncation
behaviour on data that carries at least one NULL, one duplicate row and one boundary value. Nothing
here has run against a real Alteryx engine or a real Snowflake account (see `cookbook/index.md`'s
"Local verification" note): "expected" is a documented set of assumptions
(`docs/reference/simulator-semantics.md`), and "actual" is Snowflake-dialect SQL translated by
sqlglot and executed on DuckDB.

**Stream naming.** For an ordinary tool, `compare[].stream` is a real simulator stream key,
`"<tool_id>_<anchor>"` (`SimResult.streams`). An **Output** tool has no out-anchor of its own — it
writes into `SimResult.outputs[tool_id]` instead — so by this harness's own convention (not the
dag contract's) an Output-tool example's stream is written `"<tool_id>_Output"` and is read from
`SimResult.outputs` rather than `.streams`.

**Keys.** Duplicate golden rows are byte-identical, so most streams here compare keyless
(`"keys": []`, program spec §9's row-multiset path — see `scripts/compare.py`'s module docstring).
The three tools whose data-node semantics genuinely guarantee a unique key are keyed: Summarize (its
own group-by columns), Unique's `U` anchor (its own dedup fields) and Record ID (the field it
generates, which is monotonic and therefore unique on both sides).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dev.alteryx_sim import simulate
from lib.backend import DuckDBBackend
from lib.io import read_json
from lib.types_map import alteryx_to_snowflake
from lib.typed_csv import read_table

import compare as cmp

ROOT = Path(__file__).resolve().parents[1]
COOKBOOK_DIR = ROOT / "cookbook"
EXAMPLES_DIR = ROOT / "tests" / "cookbook_examples"

#: Every tool the brief names for `cookbook/<tool>.md` (program spec §8.2's list, Task 12b's scope).
COOKBOOK_TOOLS = (
    "input", "output", "select", "filter", "formula", "join", "union", "summarize", "sort",
    "unique", "sample", "record_id", "multi_row_formula", "cross_tab", "transpose", "regex",
    "datetime", "data_cleansing",
)

#: Tools on that list the simulator cannot run, and why — checked against
#: `dev.alteryx_sim.SIMULATORS` below so this can never silently drift out of date. Every one of
#: `COOKBOOK_TOOLS` is in fact in `SIMULATORS`, so this is empty; it stays as a real mapping (not
#: just a set) so a future tool added to the cookbook without simulator support fails an assertion
#: here instead of silently losing its executable example.
NOT_EXECUTABLE_LOCALLY: dict[str, str] = {}

#: The two output-target pages (`cookbook/snowpark.md`, `cookbook/dbt.md`, Task E) follow their
#: own template and their own regression harnesses (`tests/test_cookbook_snowpark.py`,
#: `tests/test_cookbook_dbt.py`) -- they are not per-Alteryx-tool pages, so `COOKBOOK_TOOLS`'
#: bijection tests must not expect one of them for a "tool" named "snowpark" or "dbt".
TARGET_PAGES = ("snowpark", "dbt")

TOLERANCES = {"float_abs": 1e-6, "float_rel": 1e-9, "timestamp_precision": "milliseconds",
              "rounding": {"abs": 0.01}}

_HEADINGS = (
    "## What Alteryx does",
    "## Snowflake pattern",
    "## Parity risks",
    "## Config fields that change the pattern",
    "## Do not",
)

_FENCE_RE = re.compile(r"```sql\n(.*?)```", re.DOTALL)
_HEADING_RE = re.compile(r"^##\s+(.*)$", re.MULTILINE)
_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _cases() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("*/case.json")) if EXAMPLES_DIR.is_dir() else []


# --- tool <-> simulator support: proves NOT_EXECUTABLE_LOCALLY isn't guessing -------------------

def test_every_cookbook_tool_is_either_simulated_or_documented_as_not_executable():
    from dev.alteryx_sim import SIMULATORS
    for tool in COOKBOOK_TOOLS:
        if tool in NOT_EXECUTABLE_LOCALLY:
            assert tool not in SIMULATORS, (
                f"{tool} is in NOT_EXECUTABLE_LOCALLY but scripts/dev/alteryx_sim.py now simulates "
                "it: remove the exemption and add a runnable example")
        else:
            assert tool in SIMULATORS, (
                f"{tool} has no simulator rule (scripts/dev/alteryx_sim.py SIMULATORS) and is not "
                "in NOT_EXECUTABLE_LOCALLY: add it there with a reason, do not fake an example")


# --- page <-> example bijection ------------------------------------------------------------------

def test_every_cookbook_tool_has_a_page():
    pages = {path.stem for path in COOKBOOK_DIR.glob("*.md") if path.stem != "index"} - set(TARGET_PAGES)
    assert pages == set(COOKBOOK_TOOLS)


def test_executable_tools_have_an_example_directory_and_vice_versa():
    executable = [tool for tool in COOKBOOK_TOOLS if tool not in NOT_EXECUTABLE_LOCALLY]
    example_dirs = {path.parent.name for path in _cases()}
    assert example_dirs == set(executable)


def test_not_executable_pages_say_so():
    for tool, reason in NOT_EXECUTABLE_LOCALLY.items():
        text = (COOKBOOK_DIR / f"{tool}.md").read_text(encoding="utf-8")
        assert "not executable locally" in text, (
            f"cookbook/{tool}.md must say its pattern is not executable locally: {reason}")


# --- page template shape --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", COOKBOOK_TOOLS)
def test_page_has_the_template_headings(tool):
    text = (COOKBOOK_DIR / f"{tool}.md").read_text(encoding="utf-8")
    assert text.startswith("# "), f"cookbook/{tool}.md must open with a level-1 title"
    for heading in _HEADINGS:
        assert heading in text, f"cookbook/{tool}.md is missing the {heading!r} heading"
    # Headings appear in template order, so a reordered page still fails loudly.
    positions = [text.index(heading) for heading in _HEADINGS]
    assert positions == sorted(positions), f"cookbook/{tool}.md headings are out of template order"


def _section(text: str, heading: str) -> str:
    """The body of one `## heading` section, up to the next `##` heading or end of file."""
    start = text.index(heading) + len(heading)
    match = _HEADING_RE.search(text, start)
    return text[start: match.start() if match else len(text)]


def _fenced_sql_blocks(section: str) -> list[str]:
    return [block for block in _FENCE_RE.findall(section)]


@pytest.mark.parametrize("tool", [t for t in COOKBOOK_TOOLS if t not in NOT_EXECUTABLE_LOCALLY])
def test_snowflake_pattern_matches_its_query_files_exactly(tool):
    page = (COOKBOOK_DIR / f"{tool}.md").read_text(encoding="utf-8")
    case = read_json(EXAMPLES_DIR / tool / "case.json")
    blocks = _fenced_sql_blocks(_section(page, "## Snowflake pattern"))
    streams = case["compare"]
    assert len(blocks) == len(streams), (
        f"cookbook/{tool}.md's Snowflake pattern has {len(blocks)} fenced SQL block(s), "
        f"case.json names {len(streams)} compare stream(s) — one block per stream, in order")
    for block, entry in zip(blocks, streams):
        expected_sql = (EXAMPLES_DIR / tool / entry["sql"]).read_text(encoding="utf-8")
        assert block.rstrip("\n") == expected_sql.rstrip("\n"), (
            f"cookbook/{tool}.md's fenced SQL for stream {entry['stream']!r} does not match "
            f"tests/cookbook_examples/{tool}/{entry['sql']} exactly")


# --- index.md ----------------------------------------------------------------------------------

def test_index_has_the_tool_type_and_function_maps_and_the_verification_note():
    text = (COOKBOOK_DIR / "index.md").read_text(encoding="utf-8")
    # Tool -> Snowflake map (§8.2): every cookbook tool's row is present.
    for tool_label in ("Input Data", "Output Data", "Select", "Filter", "Formula", "Join", "Union",
                       "Summarize", "Sort", "Unique", "Sample", "Record ID", "Multi-Row Formula",
                       "Cross Tab", "Transpose", "RegEx", "DateTime", "Data Cleansing"):
        assert tool_label in text, f"index.md tool map is missing {tool_label!r}"
    # Type map (§8.3).
    for alteryx_type in ("Bool", "FixedDecimal(p,s)", "Float / Double", "String(n)", "Date / Time / DateTime"):
        assert alteryx_type in text, f"index.md type map is missing {alteryx_type!r}"
    # Function map (§8.4).
    for fn in ("IIF(c,a,b)", "Round(x, m)", "DateTimeAdd", "Substring(s, start, len)"):
        assert fn in text, f"index.md function map is missing {fn!r}"
    assert "Local verification" in text
    for phrase in ("not yet verified on Snowflake", "String(n)", "ALTER SESSION",
                   "owner's-rights", "multiplication scale rule"):
        assert phrase in text, f"index.md Local verification note is missing {phrase!r}"


# --- the executable examples themselves -----------------------------------------------------------

def _edges(raw: list[list]) -> list[dict]:
    edges = []
    for item in raw:
        src, src_anchor, dst, dst_anchor = item[:4]
        order = item[4] if len(item) > 4 else 1
        edges.append({"src": src, "src_anchor": src_anchor, "dst": dst, "dst_anchor": dst_anchor,
                      "dst_order": order})
    return edges


def _dag(case: dict) -> dict:
    node = dict(case["node"])
    node.setdefault("config", {})
    node.setdefault("meta", {})
    node.setdefault("container_id", None)
    # An "input" tool example's own node IS one of case["inputs"]' tool ids (the tool under test
    # reads its own seed data); every other input id gets a plain auto-generated input node.
    input_nodes = [{"tool_id": tool_id, "type": "input", "config": {}, "meta": {}, "container_id": None}
                   for tool_id in case["inputs"] if tool_id != node["tool_id"]]
    return {"workflow": "cookbook", "segment": "cookbook", "nodes": input_nodes + [node],
            "edges": _edges(case.get("edges", [])), "constants": case.get("constants", {})}


def _expected_table(sim_result, node: dict, stream: str) -> dict:
    """The oracle's table for one `compare[].stream` — see the module docstring's stream-naming note."""
    if node["type"] == "output":
        table = sim_result.outputs.get(node["tool_id"])
        assert table is not None, f"output tool {node['tool_id']} wrote nothing"
        assert stream == f"{node['tool_id']}_Output", (
            f"this harness names an Output tool's stream '<tool_id>_Output', got {stream!r}")
        return table
    table = sim_result.streams.get(stream)
    assert table is not None, (
        f"stream {stream!r} not produced; the simulator produced {sorted(sim_result.streams)}")
    return table


def _contract(table: dict, keys: list[str]) -> dict:
    columns = [{"name": field["name"], "type": alteryx_to_snowflake(field), "nullable": True}
               for field in table["fields"]]
    output = {"stream": "cookbook", "table": None, "kind": "work", "logical": None,
              "columns": columns, "keys": keys}
    return {"segment": "cookbook", "output": output,
            "ordering": {"order_dependent_columns": []}, "tolerances": {}}


@pytest.mark.parametrize("case_path", _cases(), ids=[p.parent.name for p in _cases()])
def test_example_inputs_carry_a_null_and_a_duplicate_row(case_path):
    """The brief's guarantee, enforced: the NULL and duplicate-row paths are where translations go
    wrong, so every example has to exercise them. (A boundary row is tool-specific and is argued
    on each page rather than checked here.) `SEQ` is the examples' row-order column -- SQL has no
    input order of its own, so order-dependent tools need one -- and is ignored when looking for
    the duplicate, since it is unique by construction."""
    case = read_json(case_path)
    tables = [read_table(case_path.parent / filename) for filename in case["inputs"].values()]

    def business_rows(table: dict) -> list[tuple]:
        keep = [i for i, field in enumerate(table["fields"]) if field["name"] != "SEQ"]
        return [tuple(row[i] for i in keep) for row in table["rows"]]

    assert any(value is None for table in tables for row in business_rows(table) for value in row), (
        "no input row holds a NULL")
    assert any(len(business_rows(table)) != len(set(business_rows(table))) for table in tables), (
        "no input holds a duplicate row")


@pytest.mark.parametrize("case_path", _cases(), ids=[p.parent.name for p in _cases()])
def test_cookbook_example_matches_the_simulator(case_path):
    directory = case_path.parent
    case = read_json(case_path)
    node = case["node"]

    seed = {tool_id: read_table(directory / filename) for tool_id, filename in case["inputs"].items()}
    targets_before = {logical: read_table(directory / filename)
                      for logical, filename in (case.get("targets_before") or {}).items()}
    sim_result = simulate(_dag(case), seed, targets_before=targets_before,
                          logical_by_tool=case.get("logical_by_tool") or {})

    backend = DuckDBBackend()
    try:
        for tool_id, filename in case["inputs"].items():
            backend.load_table(f"MIG_COOKBOOK.IN_{tool_id}", read_table(directory / filename))

        for entry in case["compare"]:
            stream = entry["stream"]
            expected_table = _expected_table(sim_result, node, stream)
            # Two empty tables compare as PASS, so an example whose oracle output is empty would
            # pass whatever its SQL says. Every checked stream has to carry rows.
            assert expected_table["rows"], (
                f"{case_path.parent.name}/{stream}: the simulator produced no rows, so this "
                f"example cannot tell a right pattern from a wrong one")
            expected_fqn = f"MIG_COMPARE.EXPECTED_{_SAFE.sub('_', stream)}"
            backend.load_table(expected_fqn, expected_table)

            sql = (directory / entry["sql"]).read_text(encoding="utf-8")
            actual_fqn = entry.get("table")
            if actual_fqn:
                backend.execute(sql)          # the SQL is itself a full CREATE ... AS statement
            else:
                actual_fqn = f"MIG_COOKBOOK.ACTUAL_{_SAFE.sub('_', stream)}"
                backend.execute(f"CREATE OR REPLACE TABLE {actual_fqn} AS\n{sql}")

            contract = _contract(expected_table, entry.get("keys") or [])
            report = cmp.compare(backend, expected_fqn, actual_fqn, contract, TOLERANCES)
            assert report["verdict"] == "PASS", (
                f"{case_path.parent.name}/{stream}: {json.dumps(report, indent=2, default=str)}")
    finally:
        backend.close()
