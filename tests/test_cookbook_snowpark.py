"""cookbook/snowpark.md's DataFrame idioms, each checked against the simulator (spec §8) like the
SQL patterns: the oracle is scripts/dev/alteryx_sim.py, the actual is the example run in a local
Snowpark session, the judge is compare.py on DuckDB."""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest

import compare as cmp
import validate_snowpark as vsp
from dev.alteryx_sim import simulate
from lib import snowpark_rules
from lib.backend import DuckDBBackend
from lib.io import read_json
from lib.typed_csv import read_table
from tests.test_cookbook_examples import TOLERANCES, _contract, _dag, _expected_table

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "tests" / "cookbook_examples" / "snowpark"
TOOLS = ("filter", "formula", "summarize", "sort", "python_carry_over")
_PY_FENCE = re.compile(r"```python\n(.*?)```", re.DOTALL)


def _module(path: Path):
    spec = importlib.util.spec_from_file_location(f"cookbook_{path.parent.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_listed_tool_has_an_example_and_a_section():
    page = (ROOT / "cookbook" / "snowpark.md").read_text(encoding="utf-8")
    assert sorted(p.parent.name for p in EXAMPLES.glob("*/case.json")) == sorted(TOOLS)
    blocks = [b.rstrip("\n") for b in _PY_FENCE.findall(page)]
    for tool in TOOLS:
        assert (EXAMPLES / tool / "example.py").read_text(encoding="utf-8").rstrip("\n") in blocks, tool


@pytest.mark.parametrize("tool", TOOLS)
def test_snowpark_example_matches_the_simulator(tool):
    directory = EXAMPLES / tool
    case = read_json(directory / "case.json")
    seed = {tool_id: read_table(directory / name) for tool_id, name in case["inputs"].items()}
    sim = simulate(_dag(case), seed)
    module = _module(directory / "example.py")
    session = vsp._session()
    backend = DuckDBBackend()
    try:
        for tool_id, table in seed.items():
            vsp._save(session, f"MIG_COOKBOOK.IN_{tool_id}", table)
        for index, entry in enumerate(case["compare"]):
            expected = _expected_table(sim, case["node"], entry["stream"])
            assert expected["rows"], f"{tool}/{entry['stream']}: the oracle produced no rows"
            getattr(module, entry["function"])(session).write.mode("overwrite").save_as_table(f"MIG_COOKBOOK.ACTUAL_{index}")
            actual = vsp._read_back(session, f"MIG_COOKBOOK.ACTUAL_{index}")
            backend.load_table(f"MIG_COMPARE.EXPECTED_{index}", expected)
            backend.load_table(f"MIG_COMPARE.ACTUAL_{index}", actual)
            report = cmp.compare(backend, f"MIG_COMPARE.EXPECTED_{index}", f"MIG_COMPARE.ACTUAL_{index}",
                                 _contract(expected, entry.get("keys") or []), TOLERANCES)
            assert report["verdict"] == "PASS", json.dumps(report, indent=2, default=str)
    finally:
        backend.close()
        session.close()


# --- behaviour the brief states in prose but does not test above ------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOLS)
def test_every_example_inputs_carry_a_null_and_a_duplicate_row(tool):
    """Task E's brief, mirroring `test_cookbook_examples.test_example_inputs_carry_a_null_and_a_
    duplicate_row`'s guarantee for the SQL pages: every DataFrame example is exercised on a NULL
    and a duplicate row too, filter/formula/summarize/sort by reusing the SQL examples' own input
    (already proven to carry both), python_carry_over on its own SUBSCRIPTIONS-shaped input."""
    case = read_json(EXAMPLES / tool / "case.json")
    tables = [read_table(EXAMPLES / tool / filename) for filename in case["inputs"].values()]

    def business_rows(table: dict) -> list[tuple]:
        keep = [i for i, field in enumerate(table["fields"]) if field["name"] != "SEQ"]
        return [tuple(row[i] for i in keep) for row in table["rows"]]

    assert any(value is None for table in tables for row in business_rows(table) for value in row), (
        f"{tool}: no input row holds a NULL")
    assert any(len(business_rows(table)) != len(set(business_rows(table))) for table in tables), (
        f"{tool}: no input holds a duplicate row")


def test_the_carry_over_pattern_promoted_to_a_full_procedure_passes_snowpark_rules():
    """The carry-over idiom is exactly what a translator pastes into `proc.py`'s `run()` -- every
    other example on the page is a bare DataFrame fragment `(session) -> DataFrame`, which cannot
    itself satisfy `snowpark_rules.check_proc_py` (it has no top-level `run`, and it never writes
    a sink -- both required by contract C4). The carry-over pattern promoted to a full procedure
    already exists as real, committed code: `samples/wf_0006/canned/segments/seg_02/proc.py` is
    this exact pandas carry-over body (see its own contract's parity risks), so this test proves
    the pattern this page teaches, written out as a real `run()`, obeys every C4 rule with zero
    violations -- rather than re-deriving a synthetic wrapper that duplicates that file."""
    proc_path = ROOT / "samples" / "wf_0006" / "canned" / "segments" / "seg_02" / "proc.py"
    contract_path = ROOT / "samples" / "wf_0006" / "canned" / "segments" / "seg_02" / "contract.json"
    source = proc_path.read_text(encoding="utf-8")
    contract = read_json(contract_path)
    assert snowpark_rules.check_proc_py(source, "wf_0006", "seg_02", contract) == []


# --- fix round 1 (C1): every fenced python block must itself obey the C4 rules -----------------

_RUN_SIGNATURE = tuple(snowpark_rules.SIGNATURE)  # ("session", "src_db", ..., "run_id")
# Arbitrary but fixed wf/seg used only to compute the one allowed sink name below -- check_proc_py
# needs *some* wf_id/seg to derive `own`, and any legal pair works the same way.
_WF_ID, _SEG = "wf_cookbook", "seg_01"
_OWN_TABLE = "MIG_WORK.WFCOOKBOOK_SEG_01_OUT"  # f"MIG_WORK.{_WF_ID.upper().replace('_','')}_{_SEG.upper()}_OUT"
_WRAP_CONTRACT = {"inputs": [{"table": "MIG_COOKBOOK.IN_1"}],
                  "outputs": [{"table": _OWN_TABLE, "kind": "work", "stream": "1", "logical": None}],
                  "nodes": [{"tool_id": "2"}]}


def _leading_statement_lines(tree: ast.Module, lines: list[str], kinds: tuple) -> list[str]:
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, kinds):
            out.extend(lines[node.lineno - 1: node.end_lineno])
    return out


def _wrapped_modules(source: str):
    """Yields (label, synthetic_source) for every top-level function in a page snippet, each
    promoted to a full C4 `run(session, src_db, src_schema, tgt_db, tgt_schema, run_id)` -- the
    same wrapper shape a translator actually pastes a `(session) -> DataFrame` fragment into:
    the function is renamed to `run`, its signature widened to the C4 one, and its own
    `return <expr>` becomes that `run`'s one sink (`<expr>.write.mode("overwrite").save_as_table(
    <the one table this synthetic contract allows)`) followed by `return "OK"`. Done with a line
    slice off the ORIGINAL source (never `ast.unparse`, which drops every comment) so the
    fragment's own `# tool <id>: ...` comments -- the very thing C1 is checking -- survive
    unchanged. A fragment already shaped as a full `run(...)` with the C4 signature is not a
    fragment at all and is yielded unwrapped.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    imports = _leading_statement_lines(tree, lines, (ast.Import, ast.ImportFrom))
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        arg_names = tuple(a.arg for a in node.args.args)
        if node.name == "run" and arg_names == _RUN_SIGNATURE:
            yield node.name, source
            continue
        start = node.lineno - 1
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1  # keep this function's own leading "# tool ..." comment block
        block = lines[start: node.end_lineno]
        def_index = next(i for i, line in enumerate(block) if line.lstrip().startswith(f"def {node.name}("))
        block[def_index] = f"def run({', '.join(_RUN_SIGNATURE)}):"
        return_index = next(i for i in range(len(block) - 1, -1, -1) if block[i].startswith("    return "))
        expr = block[return_index][len("    return "):]
        block[return_index: return_index + 1] = [
            f'    {expr}.write.mode("overwrite").save_as_table("{_OWN_TABLE}")',
            '    return "OK"',
        ]
        yield node.name, "\n".join(imports + ["", ""] + block) + "\n"


def test_the_carry_over_example_catches_a_dropped_reset():
    """C2: the harness must be able to tell a correct carry-over translation from one that drops
    the cancellation reset, not just run without crashing. `tests/cookbook_examples/snowpark/
    python_carry_over/broken_dropped_reset.py` is example.py's own transform() with exactly the
    `if bool(row["CANCELLED"]): deferred = 0.0` lines removed (mirrors samples/wf_0006/broken_sql/
    seg_02/01_cancellation_reset_ignored.py's own mutation). GAMMA's two periods are what make
    this catchable at all: GAMMA/2026-01 is capped (billed 100 against cap 40, so deferred carries
    60 into the next period) and GAMMA/2026-02 is the cancelled one -- with the reset, 2026-02
    recognizes only its own billed 30; without it, the carried 60 is recognized too. ACME/BETA's
    only CANCELLED=true row (BETA/2026-02) carries no deferred balance into its own cancellation,
    so it alone could not catch this (and did not, before GAMMA was added -- this is the mutation
    the review's own probe against the original data found PASSing)."""
    directory = EXAMPLES / "python_carry_over"
    case = read_json(directory / "case.json")
    seed = {tool_id: read_table(directory / name) for tool_id, name in case["inputs"].items()}
    sim = simulate(_dag(case), seed)
    module = _module(directory / "broken_dropped_reset.py")
    session = vsp._session()
    backend = DuckDBBackend()
    try:
        for tool_id, table in seed.items():
            vsp._save(session, f"MIG_COOKBOOK.IN_{tool_id}", table)
        entry = case["compare"][0]
        expected = _expected_table(sim, case["node"], entry["stream"])
        module.transform(session).write.mode("overwrite").save_as_table("MIG_COOKBOOK.ACTUAL_0")
        actual = vsp._read_back(session, "MIG_COOKBOOK.ACTUAL_0")
        backend.load_table("MIG_COMPARE.EXPECTED_0", expected)
        backend.load_table("MIG_COMPARE.ACTUAL_0", actual)
        report = cmp.compare(backend, "MIG_COMPARE.EXPECTED_0", "MIG_COMPARE.ACTUAL_0",
                             _contract(expected, entry.get("keys") or []), TOLERANCES)
        assert report["verdict"] == "FAIL", json.dumps(report, indent=2, default=str)
        clusters = report["diff_clusters"]
        assert any(c["class"] == "LOGIC" and {"RECOGNIZED", "DEFERRED"} & set(c["columns"])
                   for c in clusters), json.dumps(clusters, indent=2, default=str)
    finally:
        backend.close()
        session.close()


def test_every_snippet_on_the_snowpark_page_passes_the_rules():
    """C1: `snowpark_rules._TOOL_COMMENT` needs the colon right after the tool id (`# tool 2: …`),
    not after a parenthetical (`# tool 2 (anchor T): …`) -- a page snippet that gets this wrong
    would fail `rule:tool_comments` the moment a translator actually pastes it into `run`, silently,
    since the page itself is never run through `check_proc_py`. This test closes that gap: every
    fenced python block on the page is wrapped exactly the way a translator would use it and
    checked with zero violations, so the page can never drift from the gate again."""
    page = (ROOT / "cookbook" / "snowpark.md").read_text(encoding="utf-8")
    blocks = [b.rstrip("\n") for b in _PY_FENCE.findall(page)]
    assert blocks, "cookbook/snowpark.md has no fenced python blocks"
    for block in blocks:
        for label, synthetic in _wrapped_modules(block):
            errors = snowpark_rules.check_proc_py(synthetic, _WF_ID, _SEG, _WRAP_CONTRACT)
            assert errors == [], f"{label}:\n{synthetic}\n{errors}"
