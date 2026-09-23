"""`--backend snowflake` on all four validators, against the DuckDB-backed fake (plan Task P2).

`tests/fake_snowflake.py` is patched in as `snowflake.connector` (and, for Snowpark, as the session
`lib.snowflake_conn.snowpark_session` builds); nothing here connects to a real account or reads a
real `connections.toml`. What these tests prove: the Snowflake path goes through a NAMED connection
only, prepares a per-run sandbox in a database the policy lists, sends the statements the brief
names in the order it names, judges with the same `compare.py` into the same report shape, and leaks
no credential. What a real account does with those statements is not proven.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import validate_dbt as vd
import validate_segment as vs
import validate_snowpark as vsp
import validate_workflow as vw
from lib import dbt_project, handoff, snowflake_conn, validation as v
from lib.backend import DuckDBBackend
from lib.dbt_project import DbtResult
from lib.io import read_json, read_yaml, write_json, write_yaml
from lib.typed_csv import read_table
from tests import chain_fixtures as cf
from tests.fake_snowflake import FakeConnectorModule, FakeSession
from tests.helpers import prepare_workflow

DB = "MIGDB_SANDBOX"
SB = ["--backend", "snowflake", "--connection", "sandbox", "--sandbox-database", DB]
SECRET = "hunter2-secret"
SCHEMAS = ["MIG_GOLDEN", "MIG_WORK", "MIG_COMPARE"]


@pytest.fixture
def fake(monkeypatch):
    module = FakeConnectorModule()
    monkeypatch.setitem(sys.modules, "snowflake.connector", module)
    monkeypatch.delenv(snowflake_conn.CONNECTION_ENV, raising=False)
    monkeypatch.delenv(snowflake_conn.SANDBOX_DB_ENV, raising=False)
    return module


def _allow(root: Path, databases=(DB,)) -> None:
    write_json(Path(root) / "orchestrator.config.json", {"policy": {"sandboxDatabases": list(databases)}})


def _fresh(view_schema: str) -> list[str]:
    return [f"USE DATABASE {DB}", *(f"CREATE OR REPLACE SCHEMA {DB}.{s}" for s in [*SCHEMAS, view_schema])]


def _without_runtime(report: dict) -> dict:
    return {key: value for key, value in report.items() if key != "runtime_ms"}


def _sessions(monkeypatch, account=None) -> list[FakeSession]:
    built: list[FakeSession] = []

    def build(name):
        built.append(FakeSession(name, account=account))
        return built[-1]
    monkeypatch.setattr(snowflake_conn, "snowpark_session", build)
    return built


def _passes(statements: list[str], view_schema: str) -> list[list[str]]:
    """The recorded statements split at every sandbox reset (`USE DATABASE`)."""
    starts = [i for i, sql in enumerate(statements) if sql == f"USE DATABASE {DB}"]
    return [statements[start:end] for start, end in zip(starts, [*starts[1:], len(statements)])]


# --- the brief's six ----------------------------------------------------------------------------------

def test_validate_segment_on_snowflake_deploys_calls_and_judges(tmp_path, fake):
    repo = prepare_workflow(tmp_path / "sf", "wf_0001")
    _allow(repo.root)

    rc = vs.main(["wf_0001", "seg_01", *SB, "--set", "normal", "--root", str(repo.root)])

    assert rc == 0
    sql = fake.sql()
    assert sql[:5] == _fresh("MIG_GOLDEN_WF0001_NORMAL")
    assert repo.seg("wf_0001", "seg_01", "proc.sql").read_text(encoding="utf-8") in sql
    calls = [(s, p) for s, p in fake.statements if s.startswith("CALL")]
    # one CALL per fresh sandbox: the judged run, then the idempotency re-run
    assert calls == [("CALL MIG_WORK.WF0001_SEG_01(%s, %s, %s, %s, %s)",
                      [DB, "MIG_GOLDEN_WF0001_NORMAL", DB, "MIG_WORK", "validate_wf_0001_seg_01_normal"])] * 2
    assert [p[:5] for p in _passes(sql, "MIG_GOLDEN_WF0001_NORMAL")] == [_fresh("MIG_GOLDEN_WF0001_NORMAL")] * 2
    assert fake.connect_calls == [{"connection_name": "sandbox"}] * 2
    assert all(connection.closed for connection in fake.connections)

    local = prepare_workflow(tmp_path / "local", "wf_0001")
    assert vs.main(["wf_0001", "seg_01", "--set", "normal", "--root", str(local.root)]) == 0
    for name in ("validation.json", "validation.normal.json"):
        on_snowflake = read_json(repo.seg("wf_0001", "seg_01", name))
        assert on_snowflake["verdict"] == "PASS" and on_snowflake["idempotent"] is True
        assert _without_runtime(on_snowflake) == _without_runtime(read_json(local.seg("wf_0001", "seg_01", name)))


def test_validate_snowpark_on_snowflake_builds_its_session_from_the_connection(tmp_path, fake, monkeypatch):
    repo = prepare_workflow(tmp_path / "sf", "wf_0006")
    _allow(repo.root)
    sessions = _sessions(monkeypatch)

    rc = vsp.main(["wf_0006", "seg_02", *SB, "--set", "normal", "--root", str(repo.root)])

    assert rc == 0
    report = read_json(repo.seg("wf_0006", "seg_02", "validation.json"))
    assert report["verdict"] == "PASS" and report["idempotent"] is True and report["target"] == "snowpark"
    # one session per run -- the judged run and the idempotency re-run -- each built from the name only
    assert [session.connection_name for session in sessions] == ["sandbox", "sandbox"]
    for session in sessions:
        assert session.statements == _fresh("MIG_GOLDEN_WF0006_NORMAL")
        assert session.closed
    assert fake.connect_calls == []

    local = prepare_workflow(tmp_path / "local", "wf_0006")
    assert vsp.main(["wf_0006", "seg_02", "--set", "normal", "--root", str(local.root)]) == 0
    assert _without_runtime(report) == _without_runtime(read_json(local.seg("wf_0006", "seg_02", "validation.json")))


def _recording_run_dbt(fake, repo, wf, recorded):
    """`dbt_project.run_dbt` for the Snowflake target: records its arguments, then plays the part of
    dbt-snowflake by writing every model's table into the fake account from its golden file."""
    order = read_json(repo.wf(wf, "segments", "order.json"))

    def run_dbt(command, project, *, vars, duckdb_path, target="local", log_file=None, extra_env=None,
                timeout=None):
        recorded.append({"command": command, "project": Path(project), "vars": dict(vars),
                         "duckdb_path": duckdb_path, "target": target, "extra_env": dict(extra_env or {})})
        golden_set = vars["src_schema"].removeprefix(f"MIG_GOLDEN_{wf.upper().replace('_', '')}_").lower()
        for seg in [seg for wave in order for seg in wave]:
            for output in read_json(repo.seg(wf, seg, "contract.json"))["outputs"]:
                fake.load(dbt_project.model_relation(wf, seg, output, database=DB),
                          read_table(v.golden_path(repo, wf, seg, golden_set, output)))
        # an adapter that echoes a secret: the log the validator keeps must not
        return DbtResult(0, f"Completed successfully (connect args: password={SECRET})", [], None)
    return run_dbt


def test_validate_dbt_on_snowflake_uses_the_snowflake_target(tmp_path, fake, monkeypatch):
    repo = prepare_workflow(tmp_path, "wf_0007")
    _allow(repo.root)
    recorded: list[dict] = []
    monkeypatch.setattr(dbt_project, "run_dbt", _recording_run_dbt(fake, repo, "wf_0007", recorded))
    monkeypatch.setattr(snowflake_conn, "require_dbt_snowflake", lambda: None)

    rc = vd.main(["wf_0007", *SB, "--set", "normal", "--root", str(repo.root)])

    assert rc == 0
    assert len(recorded) == 2                                          # the run, then DV7's re-run
    for call in recorded:
        assert call["command"] == "run" and call["target"] == "snowflake"
        assert call["vars"] == {"src_schema": "MIG_GOLDEN_WF0007_NORMAL", "tgt_schema": "MIG_WORK"}
        assert call["duckdb_path"] is None
        assert call["extra_env"] == {"SNOWFLAKE_DATABASE": DB}
        assert call["project"] == repo.wf("wf_0007", "dbt")
    for seg in ("seg_01", "seg_02"):
        report = read_json(repo.seg("wf_0007", seg, "validation.json"))
        assert report["verdict"] == "PASS" and report["idempotent"] is True and report["target"] == "dbt"
    chain = read_json(repo.wf("wf_0007", "validation_workflow.json"))
    assert chain["verdict"] == "PASS" and chain["target"] == "dbt"
    passes = _passes(fake.sql(), "MIG_GOLDEN_WF0007_NORMAL")
    assert [p[:5] for p in passes] == [_fresh("MIG_GOLDEN_WF0007_NORMAL")] * 2
    assert not list(repo.wf("wf_0007").glob("dbt_sandbox_*.duckdb"))  # no DuckDB sandbox on this path
    log = repo.wf("wf_0007", "dbt", "logs", "validate_normal.log").read_text(encoding="utf-8")
    assert "Completed successfully" in log and SECRET not in log

    # ruling (2): the re-run inserts every raw input and every targets_before table in reversed order
    for table in ("MIG_GOLDEN.WF0007_NORMAL_IN_1", "MIG_GOLDEN.WF0007_NORMAL_IN_2", f"{DB}.MIG_WORK.ATTAINMENT_HISTORY"):
        inserts = [params for sql, params in fake.statements if sql.startswith(f"INSERT INTO {table} ")]
        assert len(inserts) == 2 and len(inserts[0]) > 1, table
        assert inserts[1] == list(reversed(inserts[0])), table


def test_validate_workflow_on_snowflake_runs_the_chain_in_one_sandbox(tmp_path, fake, monkeypatch):
    repo = prepare_workflow(tmp_path, "wf_0006")
    _allow(repo.root)
    sessions = _sessions(monkeypatch, account=fake)
    crossed: list[str] = []
    for name in ("table_from_backend", "load_into_backend", "table_from_snowpark", "load_into_snowpark"):
        monkeypatch.setattr(handoff, name, lambda *args, _name=name, **kwargs: crossed.append(_name))

    rc = vw.main(["wf_0006", *SB, "--set", "normal", "--root", str(repo.root)])

    report = read_json(repo.wf("wf_0006", "validation_workflow.json"))
    assert rc == 0, report
    assert report["verdict"] == "PASS" and report["idempotent"] is True and report["first_divergence"] is None
    assert [b["verdict"] for b in report["boundaries"]] == ["PASS", "PASS"]
    assert [f["verdict"] for f in report["finals"]] == ["PASS"]
    sql = fake.sql()
    calls = [s for s in sql if s.startswith("CALL")]
    assert calls == ["CALL MIG_WORK.WF0006_SEG_01(%s, %s, %s, %s, %s)",
                     "CALL MIG_WORK.WF0006_SEG_03(%s, %s, %s, %s, %s)"] * 2  # wave order, two passes
    passes = _passes(sql, "MIG_GOLDEN_WF0006_NORMAL")
    assert len(passes) == 2
    for chain_pass in passes:
        assert chain_pass[:5] == _fresh("MIG_GOLDEN_WF0006_NORMAL")
        assert not any(s.startswith("CREATE OR REPLACE SCHEMA") for s in chain_pass[5:])
    # the Snowpark segment ran in a session built from the name, in the same sandbox database
    assert [(s.connection_name, s.statements, s.closed) for s in sessions] == [
        ("sandbox", [f"USE DATABASE {DB}"], True)] * 2
    assert crossed == []                                               # one engine: no typed hand-off
    assert fake.connect_calls == [{"connection_name": "sandbox"}] * 2


def test_no_credential_reaches_argv_logs_or_reports(tmp_path, fake, monkeypatch, capsys):
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", SECRET)
    repo = prepare_workflow(tmp_path, "wf_0001")
    _allow(repo.root)

    assert vs.main(["wf_0001", "seg_01", *SB, "--set", "normal", "--root", str(repo.root)]) == 0

    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    written = [path for path in repo.wf("wf_0001").rglob("*") if path.is_file() and path.suffix in (".json", ".log")]
    assert any(path.name == "validation.json" for path in written)
    for path in written:
        assert SECRET not in path.read_text(encoding="utf-8"), path
    assert SECRET not in repr(fake.statements) and SECRET not in repr(fake.connect_calls)
    assert fake.connect_calls == [{"connection_name": "sandbox"}] * 2

    for module, positional in ((vs, ["wf_0001", "seg_01"]), (vsp, ["wf_0006", "seg_02"]),
                               (vd, ["wf_0007"]), (vw, ["wf_0001"])):
        for flag in ("--password", "--token", "--private-key-file"):
            with pytest.raises(SystemExit) as refused:
                module.build_parser().parse_args([*positional, *SB, flag, "x"])
            assert refused.value.code == 2
            assert f"unrecognized arguments: {flag}" in capsys.readouterr().err


def test_the_duckdb_default_is_unchanged(tmp_path, fake):
    repo = prepare_workflow(tmp_path, "wf_0001")
    assert vs.main(["wf_0001", "seg_01", "--set", "normal", "--root", str(repo.root)]) == 0
    composition = cf.build_composition(tmp_path / "chain", rounding=False)
    assert vw.validate_workflow(composition, cf.WF_COMPOSITION, ["normal"])["verdict"] == "PASS"
    assert fake.connect_calls == [] and fake.statements == []

    # and nothing Snowflake is even imported on the default path (the bare `snowflake` namespace
    # package is already in sys.modules when the interpreter starts: a .pth file of the venv)
    probe = ("import sys; sys.path[:0] = ['scripts', '.']; import validate_segment as vs; "
             f"rc = vs.main(['wf_0001', 'seg_01', '--set', 'normal', '--root', {str(repo.root)!r}]); "
             "print(rc, sorted(m for m in sys.modules if m.startswith(('snowflake.', 'lib.snowflake'))))")
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          cwd=Path(__file__).parents[1])
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "0 []"


# --- further coverage: the sandbox policy, usage errors, redaction, the reversed re-run ----------------

@pytest.mark.parametrize("module, argv", [
    (vs, ["wf_0001", "seg_01"]), (vsp, ["wf_0006", "seg_02"]), (vd, ["wf_0007"]), (vw, ["wf_0001"])],
    ids=["segment", "snowpark", "dbt", "workflow"])
def test_a_sandbox_database_outside_the_policy_is_refused_before_anything_connects(tmp_path, fake, monkeypatch,
                                                                                 capsys, module, argv):
    sessions = _sessions(monkeypatch)
    _allow(tmp_path, ["MIGDB"])
    if module is vd:            # validate_dbt reads order.json to clear every segment's stale report first
        write_json(tmp_path / "workflows" / "wf_0007" / "segments" / "order.json", [["seg_01"]])
    stale = tmp_path / "workflows" / argv[0] / "validation_workflow.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    write_json(stale, {"verdict": "PASS"})
    for database in ("ANALYTICS", "migdb"):
        with pytest.raises(SystemExit) as refused:
            module.main([*argv, "--backend", "snowflake", "--connection", "sandbox",
                         "--sandbox-database", database, "--root", str(tmp_path)])
        assert refused.value.code == 2
        err = capsys.readouterr().err
        assert database in err and ("sandboxDatabases" in err or "identifier" in err), err
    assert fake.connect_calls == [] and sessions == []
    assert not list(tmp_path.rglob("validation.json"))                 # nothing was written
    if module in (vd, vw):
        assert not stale.exists()                                      # and a stale chain report is gone


def test_snowflake_usage_errors_exit_2_and_connect_to_nothing(tmp_path, fake, capsys):
    _allow(tmp_path)
    # a workflow that was never prepared: the prerequisites are checked before any connection
    with pytest.raises(SystemExit) as unprepared:
        vs.main(["wf_0001", "seg_01", *SB, "--root", str(tmp_path)])
    assert unprepared.value.code == 2
    # no connection name anywhere
    with pytest.raises(SystemExit) as nameless:
        vw.main(["wf_0001", "--backend", "snowflake", "--sandbox-database", DB, "--root", str(tmp_path)])
    assert nameless.value.code == 2 and "MIG_SNOWFLAKE_CONNECTION" in capsys.readouterr().err
    # Snowflake options without --backend snowflake
    with pytest.raises(SystemExit) as stray:
        vs.main(["wf_0001", "seg_01", "--connection", "sandbox", "--root", str(tmp_path)])
    assert stray.value.code == 2 and "--backend snowflake" in capsys.readouterr().err
    assert fake.connect_calls == []


def test_a_connector_error_is_redacted_before_it_reaches_a_report(tmp_path, fake, capsys):
    repo = prepare_workflow(tmp_path, "wf_0001")
    _allow(repo.root)
    fake.account.fail["CALL MIG_WORK.WF0001_SEG_01"] = f"Uncaught exception: connect(password={SECRET})"

    rc = vs.main(["wf_0001", "seg_01", *SB, "--set", "normal", "--root", str(repo.root)])

    assert rc == 1                                                     # a domain FAIL, as locally
    report = read_json(repo.seg("wf_0001", "seg_01", "validation.json"))
    assert report["verdict"] == "FAIL" and "Uncaught exception" in report["error"]
    assert SECRET not in report["error"] and "password=" in report["error"]
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_a_snowflake_crash_prints_a_redacted_traceback_and_exits_2(tmp_path, fake, monkeypatch, capsys):
    repo = prepare_workflow(tmp_path, "wf_0001")
    _allow(repo.root)

    def boom(*args, **kwargs):
        raise RuntimeError(f"connection reset (token={SECRET})")
    monkeypatch.setattr(vs, "validate_segment", boom)
    assert vs.main(["wf_0001", "seg_01", *SB, "--root", str(repo.root)]) == 2
    err = capsys.readouterr().err
    assert "RuntimeError" in err and SECRET not in err


def test_a_snowpark_segment_that_raises_in_a_snowflake_chain_is_a_redacted_boundary(tmp_path, fake, monkeypatch):
    repo = prepare_workflow(tmp_path, "wf_0006")
    _allow(repo.root)
    sessions = _sessions(monkeypatch, account=fake)
    proc_py = repo.seg("wf_0006", "seg_02", "proc.py")
    text = proc_py.read_text(encoding="utf-8")
    broken = text.replace("    out = []\n", f"    raise RuntimeError('warehouse refused: token={SECRET}')\n", 1)
    assert broken != text
    proc_py.write_text(broken, encoding="utf-8", newline="\n")

    report = vw.validate_workflow(repo, "wf_0006", ["normal"], backend="snowflake", connection="sandbox",
                                  sandbox_database=DB)

    assert report["verdict"] == "FAIL" and report["divergence_kind"] == "boundary"
    assert report["first_divergence"] == {"segment": "seg_02", "stream": None, "output": None, "set": "normal"}
    assert "warehouse refused" in report["error"] and SECRET not in report["error"]
    assert [b["segment"] for b in report["boundaries"]] == ["seg_01"]     # judged before seg_02 raised
    assert SECRET not in repo.wf("wf_0006", "validation_workflow.normal.json").read_text(encoding="utf-8")
    assert all(session.closed for session in sessions)
    assert all(connection.closed for connection in fake.connections)


# --- fix round 1, C2: every name derived from workflow data is an identifier, checked up front -------

#: The reviewer's shape: a `logical` that turns the golden view's DDL into a different view.
CRAFTED = "ORDERS AS SELECT ID AS X FROM MIG_WORK.LEFTOVER --"


def _mutate_yaml(path: Path, change) -> None:
    doc = read_yaml(path)
    change(doc)
    write_yaml(path, doc)


def _first(mapping: dict) -> dict:
    return next(iter(mapping.values()))


def _set_logical(section: str, value):
    return lambda doc: _first(doc[section]).__setitem__("logical", value)


def _set_tool_ids(value):
    return lambda doc: _first(doc["sources"]).__setitem__("tool_ids", value)


@pytest.fixture
def statements_on_duckdb(monkeypatch):
    """Every statement any `DuckDBBackend` is asked to run or load, recorded (and still run)."""
    seen: list[str] = []
    for name in ("execute", "query", "load_table", "create_view"):
        real = getattr(DuckDBBackend, name)

        def spy(self, first, *args, _real=real, **kwargs):
            seen.append(str(first))
            return _real(self, first, *args, **kwargs)
        monkeypatch.setattr(DuckDBBackend, name, spy)
    return seen


@pytest.mark.parametrize("backend", ["duckdb", "snowflake"])
def test_names_from_workflow_data_are_refused_before_anything_runs(tmp_path, fake, statements_on_duckdb, capsys,
                                                                   backend):
    repo = prepare_workflow(tmp_path, "wf_0001")
    _allow(repo.root)
    mappings = repo.wf("wf_0001", "intake", "mappings.yaml")
    contract = repo.seg("wf_0001", "seg_01", "contract.json")
    pristine = {mappings: mappings.read_text(encoding="utf-8"), contract: contract.read_text(encoding="utf-8")}
    cases = [(mappings, _set_logical("sources", CRAFTED), CRAFTED),
             (mappings, _set_logical("sources", "orders"), "orders"),            # lower case
             (mappings, _set_logical("sources", "RAW.ORDERS"), "RAW.ORDERS"),    # dotted
             (mappings, _set_logical("sources", 'OR"DERS'), 'OR"DERS'),          # a quote
             (mappings, _set_tool_ids(["1 --"]), "1 --"),                        # a tool id suffix
             (mappings, _set_logical("outputs", "SALES_SUMMARY; DROP TABLE X"), "SALES_SUMMARY; DROP TABLE X"),
             (contract, None, "sales_summary")]                                  # a contract target
    options = SB if backend == "snowflake" else []
    statements_on_duckdb.clear()                           # building the sample may use the double itself
    for path, change, value in cases:
        for original, text in pristine.items():
            original.write_text(text, encoding="utf-8", newline="\n")
        if change is not None:
            _mutate_yaml(path, change)
        else:
            doc = read_json(contract)
            doc["outputs"][0]["logical"] = value
            write_json(contract, doc)
        with pytest.raises(SystemExit) as refused:
            vs.main(["wf_0001", "seg_01", *options, "--set", "normal", "--root", str(repo.root)])
        assert refused.value.code == 2, value
        err = capsys.readouterr().err
        assert repr(value)[1:-1] in err, (value, err)
        assert not repo.seg("wf_0001", "seg_01", "validation.json").exists()
    assert statements_on_duckdb == [] and fake.statements == [] and fake.connect_calls == []


def test_every_validator_checks_names_before_it_connects(tmp_path, fake, statements_on_duckdb, monkeypatch, capsys):
    sessions = _sessions(monkeypatch)

    def no_dbt(*args, **kwargs):
        raise AssertionError("dbt must not run for a workflow whose names are refused")
    monkeypatch.setattr(dbt_project, "run_dbt", no_dbt)
    monkeypatch.setattr(snowflake_conn, "require_dbt_snowflake", lambda: None)

    six = prepare_workflow(tmp_path / "six", "wf_0006")
    seven = prepare_workflow(tmp_path / "seven", "wf_0007")
    for root in (six.root, seven.root):
        _allow(root)
    _mutate_yaml(six.wf("wf_0006", "intake", "mappings.yaml"), _set_logical("sources", CRAFTED))
    _mutate_yaml(seven.wf("wf_0007", "intake", "mappings.yaml"), _set_logical("sources", CRAFTED))
    runs = [(vsp, ["wf_0006", "seg_02", *SB], six), (vw, ["wf_0006", *SB], six), (vw, ["wf_0006"], six),
            (vd, ["wf_0007", *SB], seven), (vd, ["wf_0007"], seven)]
    statements_on_duckdb.clear()                           # building the samples may use the double itself
    for module, argv, repo in runs:
        with pytest.raises(SystemExit) as refused:
            module.main([*argv, "--set", "normal", "--root", str(repo.root)])
        assert refused.value.code == 2, argv
        assert CRAFTED in capsys.readouterr().err, argv
    assert statements_on_duckdb == []

    # a contract's work table reaches SQL as written: it is checked too, on both backends
    six_again = prepare_workflow(tmp_path / "six_again", "wf_0006")
    _allow(six_again.root)
    contract_path = six_again.seg("wf_0006", "seg_01", "contract.json")
    contract = read_json(contract_path)
    bad_table = "MIG_WORK.WF0006_SEG_01_OUT; DROP TABLE X"
    contract["outputs"][0]["table"] = bad_table
    write_json(contract_path, contract)
    statements_on_duckdb.clear()
    for module, argv in ((vs, ["wf_0006", "seg_01"]), (vw, ["wf_0006"])):
        for options in ([], SB):
            with pytest.raises(SystemExit) as refused:
                module.main([*argv, *options, "--set", "normal", "--root", str(six_again.root)])
            assert refused.value.code == 2 and bad_table in capsys.readouterr().err
    assert statements_on_duckdb == [] and fake.statements == [] and fake.connect_calls == [] and sessions == []


def test_the_chain_report_on_snowflake_equals_the_local_one(tmp_path, fake, monkeypatch):
    """Fix round 1: as for `validate_segment` and `validate_snowpark`, the same chain on the fake account
    writes the same `validation_workflow*.json` as the local chain, `runtime_ms` aside."""
    on_snowflake = prepare_workflow(tmp_path / "sf", "wf_0006")
    _allow(on_snowflake.root)
    _sessions(monkeypatch, account=fake)
    assert vw.main(["wf_0006", *SB, "--set", "normal", "--root", str(on_snowflake.root)]) == 0
    local = prepare_workflow(tmp_path / "local", "wf_0006")
    assert vw.main(["wf_0006", "--set", "normal", "--root", str(local.root)]) == 0
    for name in ("validation_workflow.json", "validation_workflow.normal.json"):
        report = read_json(on_snowflake.wf("wf_0006", name))
        assert report["verdict"] == "PASS" and report["idempotent"] is True
        assert _without_runtime(report) == _without_runtime(read_json(local.wf("wf_0006", name))), name


def test_the_snowflake_rerun_loads_raw_inputs_in_reversed_insert_order(tmp_path, fake):
    """Ruling (2): a real account has no physical row order to reverse, so the chain's idempotency
    re-run perturbs the only order it controls -- the order the raw golden inputs are INSERTed in."""
    repo = cf.build_composition(tmp_path, rounding=False)
    _allow(repo.root)

    report = vw.validate_workflow(repo, cf.WF_COMPOSITION, ["normal"], backend="snowflake",
                                  connection="sandbox", sandbox_database=DB)

    assert report["verdict"] == "PASS" and report["idempotent"] is True
    inserts = [(sql, params) for sql, params in fake.statements
               if sql.startswith("INSERT INTO MIG_GOLDEN.")]
    first, second = inserts[: len(inserts) // 2], inserts[len(inserts) // 2:]
    assert first and [sql for sql, _ in first] == [sql for sql, _ in second]
    for (_, rows_first), (_, rows_second) in zip(first, second):
        assert len(rows_first) > 1 and rows_second == list(reversed(rows_first))
