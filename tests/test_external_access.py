"""Live hardening L4, fix round 1 (P, pre-existing, ruled in): agent SQL never touches host files.

A segment procedure is agent-written SQL that `compile_check.py`, `validate_segment.py` and
`validate_workflow.py` run on the DuckDB double (`lib.proc_runner.run_proc`). DuckDB can read and write
the host's files (`COPY … TO '<path>'`, `read_text('<path>')`, `FROM '<file>.csv'`, `ATTACH`, …), so two
guards stand between that SQL and the host:

- `compile_check.py` refuses file-, network- and settings-touching constructs by name
  (`c4:external_access`), before anything runs;
- `run_proc` turns DuckDB's external access off, and locks the configuration, before the first
  statement of the procedure runs (the golden data is loaded before that, and every later load goes
  through Python, never a file function) -- so even SQL that got past the first guard cannot reach a
  file.

Nothing here has run on Snowflake; the file functions named are DuckDB's (the double's), plus
Snowflake's stage and file functions.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import compile_check as cc
import validate_segment as vs
from lib.backend import BackendError, DuckDBBackend
from lib.io import read_json
from lib.proc_runner import run_proc
from tests import test_validate_segment as tvs

WF, SEG = tvs.WF, tvs.SEG
WORK_WRITE = "  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0009_SEG_01_OUT AS"


def _with_statement(statement: str) -> str:
    """The validate_segment fixture's correct procedure with `statement` run first."""
    return tvs.PROC.replace(WORK_WRITE, f"  {statement};\n{WORK_WRITE}", 1)


def _secret(tmp_path: Path) -> Path:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-VALUE", encoding="utf-8")
    return secret


def _probes(tmp_path: Path) -> dict[str, str]:
    """The reviewer's two probes, and the other ways DuckDB reaches a file."""
    marker = (tmp_path / "marker.csv").as_posix()
    secret = _secret(tmp_path).as_posix()
    return {
        "copy_to": f"COPY (SELECT 42 AS X) TO '{marker}'",
        "read_text": f"CREATE OR REPLACE TABLE MIG_WORK.LEAK AS SELECT content AS NOTE FROM read_text('{secret}')",
        "read_csv": f"CREATE OR REPLACE TABLE MIG_WORK.LEAK AS SELECT * FROM read_csv_auto('{secret}')",
        "quoted_function": f"CREATE OR REPLACE TABLE MIG_WORK.LEAK AS SELECT \"read_text\"('{secret}') AS NOTE",
        "file_in_from": f"CREATE OR REPLACE TABLE MIG_WORK.LEAK AS SELECT * FROM '{secret}'",
        "file_in_join": (f"CREATE OR REPLACE TABLE MIG_WORK.LEAK AS SELECT t.ID FROM MIG_WORK.WF0009_SEG_01_OUT t "
                         f"JOIN '{secret}' s ON TRUE"),
        "quoted_file": 'CREATE OR REPLACE TABLE MIG_WORK.LEAK AS SELECT * FROM "secret.txt"',
        "install": "INSTALL httpfs",
        "stage": "CREATE OR REPLACE TABLE MIG_WORK.LEAK AS SELECT $1 AS NOTE FROM @MIG_WORK.S",
        "getenv": "CREATE OR REPLACE TABLE MIG_WORK.LEAK AS SELECT getenv('PATH') AS NOTE",
        "set": "SET enable_external_access = true",
        "presigned": "CREATE OR REPLACE TABLE MIG_WORK.LEAK AS SELECT GET_PRESIGNED_URL(@S, 'f') AS NOTE",
    }


#: `_probes`' names, spelled out: collecting the tests must not call `_probes`, which writes a file.
PROBES = ("copy_to", "read_text", "read_csv", "quoted_function", "file_in_from", "file_in_join", "quoted_file",
          "install", "stage", "getenv", "set", "presigned")


def test_the_probe_list_names_every_probe(tmp_path):
    assert sorted(PROBES) == sorted(_probes(tmp_path))


@pytest.mark.parametrize("probe", PROBES)
def test_compile_check_refuses_every_file_touching_construct_before_it_runs(tmp_path, probe):
    statement = _probes(tmp_path)[probe]
    repo = tvs.build(tmp_path / "root", proc=_with_statement(statement))
    report = cc.compile_check(repo, WF, SEG)
    assert report["status"] == "ERROR"
    assert any(error.startswith("c4:external_access: ") for error in report["errors"]), report["errors"]
    assert not (tmp_path / "marker.csv").exists()


def test_the_refusal_names_the_construct_and_the_rule(tmp_path):
    repo = tvs.build(tmp_path / "root", proc=_with_statement(_probes(tmp_path)["read_text"]))
    errors = [e for e in cc.compile_check(repo, WF, SEG)["errors"] if e.startswith("c4:external_access")]
    assert errors == ["c4:external_access: read_text() reads files, settings or the network; a segment procedure "
                      "reads its sources through IDENTIFIER(:<LOGICAL>_SRC) and writes tables, nothing else"]


def test_ordinary_sql_that_merely_looks_similar_is_not_refused(tmp_path):
    """`EXTRACT(… FROM '<literal>')`, a column named like a function, a string that mentions one."""
    statement = ("CREATE OR REPLACE TABLE MIG_WORK.WF0009_SEG_01_NOTE AS SELECT "
                 "EXTRACT(YEAR FROM '2020-01-01'::DATE) AS Y, 'read_text(x) COPY TO' AS load_date")
    repo = tvs.build(tmp_path / "root", proc=_with_statement(statement))
    report = cc.compile_check(repo, WF, SEG)
    assert not [e for e in report["errors"] if e.startswith("c4:external_access")], report["errors"]


# --- with the static check bypassed, the run itself cannot reach a file -----------------------------

@pytest.mark.parametrize("probe", ["copy_to", "read_text", "file_in_from", "read_csv"])
def test_with_the_check_bypassed_the_run_fails_because_external_access_is_off(tmp_path, monkeypatch, probe):
    monkeypatch.setattr(cc, "external_access_errors", lambda *args, **kwargs: [])
    repo = tvs.build(tmp_path / "root", proc=_with_statement(_probes(tmp_path)[probe]))
    report = cc.compile_check(repo, WF, SEG)
    assert report["status"] == "ERROR"
    # DuckDB refuses the file ("Permission Error ... disabled by configuration"), or -- for `FROM '<path>'`,
    # whose replacement scan is off with external access -- no longer reads it as a file at all.
    assert any(any(sign in error for sign in ("disabled by configuration", "Permission Error", "does not exist"))
               for error in report["errors"]), report["errors"]
    assert not (tmp_path / "marker.csv").exists()
    assert "TOP-SECRET-VALUE" not in repo.seg(WF, SEG, "compile_check.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("probe", ["copy_to", "read_text"])
def test_validate_segment_cannot_reach_a_file_either(tmp_path, probe):
    """validate_segment.py never runs compile_check: the run's own lock is what stops it. The secret's
    value never reaches the report, and the marker file is never written."""
    repo = tvs.build(tmp_path / "root", proc=_with_statement(_probes(tmp_path)[probe]))
    report = vs.validate_segment(repo, WF, SEG)
    assert report["verdict"] == "FAIL"
    assert not (tmp_path / "marker.csv").exists()
    assert "TOP-SECRET-VALUE" not in repo.seg(WF, SEG, "validation.json").read_text(encoding="utf-8")


def test_run_proc_locks_external_access_before_the_first_statement(tmp_path):
    backend = DuckDBBackend()
    try:
        backend.load_table("MIGDB.MIG_COMPILE.ITEMS", {"fields": tvs.F, "rows": [[1, "keep"]]})   # loads first
        with pytest.raises(BackendError, match="disabled by configuration|Permission Error"):
            run_proc(backend, _with_statement(_probes(tmp_path)["copy_to"]), cc.ARGS)
        assert not (tmp_path / "marker.csv").exists()
        # nothing on the connection can turn it back on (the backend's own translation of a Snowflake
        # `SET` is a DuckDB user variable, so the raw setting is tried as well) ...
        backend.execute("SET enable_external_access = true")
        with pytest.raises(Exception, match="Cannot change configuration option"):
            backend._con.execute("SET enable_external_access = true")
        assert backend._con.execute("SELECT current_setting('enable_external_access')").fetchall() == [(False,)]
        # ... and later loads still work: they insert rows through Python, never through a file
        backend.load_table("MIGDB.MIG_COMPILE.MORE", {"fields": tvs.F, "rows": [[2, "drop"]]})
        assert backend.query("SELECT COUNT(*) FROM MIGDB.MIG_COMPILE.MORE")[1] == [(1,)]
    finally:
        backend.close()


def test_every_committed_procedure_still_passes_both_guards(tmp_path):
    """The canned procedures neither name a file function nor need external access."""
    for contract_path in sorted((Path(__file__).resolve().parents[1] / "workflows").glob("wf_*/segments/seg_*/contract.json")):
        if (read_json(contract_path).get("target") or "sql") != "sql" or not (contract_path.parent / "proc.sql").is_file():
            continue
        proc = cc.parse_proc((contract_path.parent / "proc.sql").read_text(encoding="utf-8"))
        assert cc.external_access_errors(proc) == [], contract_path.parent
