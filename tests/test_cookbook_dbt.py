"""cookbook/dbt.md's one executable pattern -- a merge model on DuckDB -- checked against the
simulator (spec §8) exactly like every other cookbook pattern: the oracle is
`scripts/dev/alteryx_sim.py`, the actual is the committed example project's `models/history.sql`
run for real through `lib.dbt_project.run_dbt` on a DuckDB sandbox, the judge is `compare.py`.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import compare as cmp
from dev.alteryx_sim import simulate
from lib import dbt_project as dp
from lib.backend import DuckDBBackend
from lib.io import read_json
from lib.typed_csv import read_table
from tests.test_cookbook_examples import TOLERANCES, _contract, _dag, _expected_table

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "tests" / "cookbook_examples" / "dbt" / "merge"
PROJECT = EXAMPLE / "project"
_SQL_FENCE = re.compile(r"```sql\n(.*?)```", re.DOTALL)
_HEADINGS = ("## Materialisations", "## Hooks", "## Sources and refs", "## Naming", "## Tests",
            "## What dbt-duckdb does not prove")


def _case() -> dict:
    return read_json(EXAMPLE / "case.json")


def _project_listing() -> list[str]:
    return sorted(p.relative_to(PROJECT).as_posix() for p in PROJECT.rglob("*"))


def _run(tmp_path):
    """Seeds a fresh DuckDB sandbox exactly as `case.json` describes and runs the committed
    example project against it for real. Returns (entry, expected_table, sandbox_path, result)."""
    case = _case()
    seed = {tool_id: read_table(EXAMPLE / name) for tool_id, name in case["inputs"].items()}
    targets_before = {logical: read_table(EXAMPLE / name)
                      for logical, name in case["targets_before"].items()}
    sim = simulate(_dag(case), seed, targets_before=targets_before,
                   logical_by_tool=case["logical_by_tool"])
    entry = case["compare"][0]
    expected = _expected_table(sim, case["node"], entry["stream"])

    sandbox = tmp_path / "dbt_sandbox_cookbook.duckdb"
    backend = DuckDBBackend(str(sandbox))
    try:
        backend.load_table("MIGDB.MIG_COOKBOOK.IN_1", seed["1"])
        backend.load_table("MIGDB.MIG_WORK.HISTORY", targets_before["HISTORY"])
    finally:
        backend.close()

    result = dp.run_dbt("run", PROJECT, vars=dp.local_vars("MIG_COOKBOOK"), duckdb_path=sandbox)
    return entry, expected, sandbox, result


def test_the_dbt_page_shows_the_executable_merge_model():
    page = (ROOT / "cookbook" / "dbt.md").read_text(encoding="utf-8")
    for heading in _HEADINGS:
        assert heading in page, f"cookbook/dbt.md is missing the {heading!r} heading"
    blocks = [b.rstrip("\n") for b in _SQL_FENCE.findall(page)]
    assert len(blocks) == 1, (
        f"cookbook/dbt.md's executable example must show exactly one SQL block, found {len(blocks)}")
    model = (PROJECT / "models" / "history.sql").read_text(encoding="utf-8")
    assert blocks[0] == model.rstrip("\n")


def test_the_example_inputs_carry_a_null_and_a_duplicate_row():
    """As `test_cookbook_examples.test_example_inputs_carry_a_null_and_a_duplicate_row` asserts
    for the SQL pages: the incoming rows carry a NULL (REGION on ID 2) and a duplicate row (ID 1
    twice, byte-identical) -- the two paths a merge translation most often gets wrong."""
    case = _case()
    table = read_table(EXAMPLE / case["inputs"]["1"])
    keep = [i for i, field in enumerate(table["fields"]) if field["name"] != "SEQ"]
    rows = [tuple(row[i] for i in keep) for row in table["rows"]]
    assert any(value is None for row in rows for value in row), "no input row holds a NULL"
    assert len(rows) != len(set(rows)), "no input holds a duplicate row"


def test_the_merge_example_matches_the_simulator(tmp_path):
    entry, expected, sandbox, result = _run(tmp_path)
    assert expected["rows"], "the oracle produced no rows"
    assert result.ok, json.dumps(result.results, indent=2, default=str)

    backend = DuckDBBackend(str(sandbox))
    try:
        backend.load_table("MIG_COMPARE.EXPECTED_0", expected)
        contract = _contract(expected, entry["keys"])
        report = cmp.compare(backend, "MIG_COMPARE.EXPECTED_0", "MIGDB.MIG_WORK.HISTORY", contract,
                             TOLERANCES)
        assert report["verdict"] == "PASS", json.dumps(report, indent=2, default=str)
    finally:
        backend.close()


def test_running_the_example_writes_nothing_into_the_committed_project(tmp_path):
    before = _project_listing()
    _, _, _, result = _run(tmp_path)
    assert result.ok, json.dumps(result.results, indent=2, default=str)
    assert _project_listing() == before


def test_the_merge_example_catches_a_narrowed_unique_key(tmp_path):
    """I3: REGION used to be 1:1 with ID in this fixture (EAST only ever named ID 1), so a
    translation that narrowed `unique_key` to `['REGION']` still ran to a table that happened to
    match the correct one -- only `compile_check.py`'s static `dbt:model_config` check (which
    compares a model's `unique_key` against the mapping's own declared keys) caught the mistake,
    never a validation run. `history_before.csv` now gives EAST two rows (ID 1 and ID 3), so the
    two keys are no longer interchangeable at runtime either: this builds a scratch copy of the
    committed project with `unique_key=['REGION']` in place of `['ID']`, runs it for real, and
    asserts the runtime compare against the correct (`keys=['ID']`) oracle output now FAILs too --
    confirmed directly, before writing this test, against both the oracle's own `update_insert`
    write mode and a real `dbt run` (REGION's single incoming EAST row matches both of the
    target's EAST rows, so both get overwritten with the incoming values and ID 3's own row is
    gone from the result). Static and runtime checking are two independent lines of defence here,
    not one restating the other -- see cookbook/dbt.md's "Materialisations" section."""
    case = _case()
    seed = {tool_id: read_table(EXAMPLE / name) for tool_id, name in case["inputs"].items()}
    targets_before = {logical: read_table(EXAMPLE / name)
                      for logical, name in case["targets_before"].items()}
    sim = simulate(_dag(case), seed, targets_before=targets_before,
                   logical_by_tool=case["logical_by_tool"])
    entry = case["compare"][0]
    expected = _expected_table(sim, case["node"], entry["stream"])

    scratch_project = tmp_path / "project"
    shutil.copytree(PROJECT, scratch_project)
    model_path = scratch_project / "models" / "history.sql"
    original = model_path.read_text(encoding="utf-8")
    narrowed = original.replace("unique_key=['ID']", "unique_key=['REGION']")
    assert narrowed != original, "history.sql no longer has unique_key=['ID'] to narrow"
    model_path.write_text(narrowed, encoding="utf-8", newline="\n")

    sandbox = tmp_path / "dbt_sandbox_narrow.duckdb"
    backend = DuckDBBackend(str(sandbox))
    try:
        backend.load_table("MIGDB.MIG_COOKBOOK.IN_1", seed["1"])
        backend.load_table("MIGDB.MIG_WORK.HISTORY", targets_before["HISTORY"])
    finally:
        backend.close()

    result = dp.run_dbt("run", scratch_project, vars=dp.local_vars("MIG_COOKBOOK"), duckdb_path=sandbox)
    assert result.ok, json.dumps(result.results, indent=2, default=str)

    backend = DuckDBBackend(str(sandbox))
    try:
        backend.load_table("MIG_COMPARE.EXPECTED_0", expected)
        contract = _contract(expected, entry["keys"])
        report = cmp.compare(backend, "MIG_COMPARE.EXPECTED_0", "MIGDB.MIG_WORK.HISTORY", contract,
                             TOLERANCES)
        assert report["verdict"] == "FAIL", json.dumps(report, indent=2, default=str)
    finally:
        backend.close()
