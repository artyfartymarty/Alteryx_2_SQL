"""`scripts/deploy.py`: print (default) or execute a VALIDATED workflow's DDL (plan Task P2).

Dry-run connects to nothing. `--execute` goes through `SnowflakeBackend(connection_name=…)` -- here the
DuckDB-backed fake patched in as `snowflake.connector` -- or, for a dbt workflow, through
`dbt_project.run_dbt(target="snowflake")`, patched to record. Nothing here reaches a real account.
"""
from __future__ import annotations

import shutil
import sys

import pytest

import deploy
from lib import dbt_project, snowflake_conn
from lib.dbt_project import DbtResult, DbtUnavailable
from lib.io import read_json, write_json
from lib.paths import Repo
from tests.fake_snowflake import FakeConnectorModule
from tests.helpers import ROOT, copy_pristine_mappings_and_catalog

WF = "wf_0006"
SEGMENTS = ["seg_01", "seg_02", "seg_03"]
TARGET = ["--database", "ANALYTICS", "--schema", "CURATED"]


@pytest.fixture
def fake(monkeypatch):
    module = FakeConnectorModule()
    monkeypatch.setitem(sys.modules, "snowflake.connector", module)
    monkeypatch.delenv(snowflake_conn.CONNECTION_ENV, raising=False)
    return module


@pytest.fixture(scope="module")
def validated_wf_0006(tmp_path_factory):
    """The committed `workflows/wf_0006`, copied -- with the chain report Task W1 made the VALIDATED
    gate (`validation_workflow.json`), which the committed tree carries since phase-2 Task G."""
    root = tmp_path_factory.mktemp("committed")
    copy_pristine_mappings_and_catalog(root)
    shutil.copytree(ROOT / "workflows" / WF, root / "workflows" / WF)
    assert read_json(Repo(root).wf(WF, "validation_workflow.json"))["verdict"] == "PASS"
    return root


@pytest.fixture
def repo(validated_wf_0006, tmp_path) -> Repo:
    shutil.copytree(validated_wf_0006, tmp_path / "root")
    return Repo(tmp_path / "root")


def _code(argv) -> int:
    """`deploy.main`'s exit code, whether it returned one or argparse raised SystemExit."""
    try:
        return deploy.main(argv)
    except SystemExit as exc:
        return exc.code


def _texts(repo: Repo) -> list[str]:
    return ([repo.seg(WF, seg, "proc.sql").read_text(encoding="utf-8") for seg in SEGMENTS]
            + [repo.wf(WF, "procs", "master.sql").read_text(encoding="utf-8")])


def _labels() -> list[str]:
    return [f"workflows/{WF}/segments/{seg}/proc.sql" for seg in SEGMENTS] + [f"workflows/{WF}/procs/master.sql"]


# --- the brief's five ---------------------------------------------------------------------------------

def test_dry_run_prints_the_procedures_in_order_and_connects_to_nothing(repo, fake, capsys):
    assert deploy.main([WF, *TARGET, "--root", str(repo.root)]) == 0

    out = capsys.readouterr().out
    blocks = "".join(f"-- {label}\n{text.rstrip()}\n\n" for label, text in zip(_labels(), _texts(repo)))
    assert out == (
        f"-- deploy.py {WF}: dry run -- printed only, nothing was executed\n"
        "USE DATABASE ANALYTICS;\n"
        "CREATE SCHEMA IF NOT EXISTS MIG_WORK;\n\n"
        f"{blocks}"
        "-- Run the workflow (targets land in ANALYTICS.CURATED; SRC is the schema of source views\n"
        "-- scripts/gen_source_views.py generates):\n"
        "-- CALL MIG_WORK.WF0006_MASTER('<SRC_DB>', '<SRC_SCHEMA>', 'ANALYTICS', 'CURATED', '<run id>');\n")
    assert fake.connect_calls == [] and fake.statements == []


def test_execute_runs_exactly_those_statements(repo, fake, capsys):
    fake.run("CREATE SCHEMA ANALYTICS.MIG_WORK")
    fake.load("ANALYTICS.MIG_WORK.KEEP_ME", {"fields": [{"name": "X", "type": "Int32", "size": 4, "scale": None}],
                                             "rows": [[1]]})

    rc = deploy.main([WF, *TARGET, "--connection", "prod", "--execute", "--root", str(repo.root)])

    assert rc == 0
    assert fake.sql() == ["USE DATABASE ANALYTICS", "CREATE SCHEMA IF NOT EXISTS MIG_WORK", *_texts(repo)]
    assert fake.connect_calls == [{"connection_name": "prod"}]
    assert fake.connections[0].closed
    # the sandbox schemas are never touched: no schema is replaced, what MIG_WORK held is still there
    assert fake.exists("ANALYTICS.MIG_WORK.KEEP_ME")
    assert sorted(fake.account.procedures) == [f"ANALYTICS.MIG_WORK.WF0006_{name}"
                                               for name in ("MASTER", "SEG_01", "SEG_02", "SEG_03")]
    out = capsys.readouterr().out
    assert "executed 6 statements through the named connection 'prod'" in out


def test_a_dbt_workflow_prints_its_run_command(validated_wf_0007, fake, capsys):
    root = validated_wf_0007
    rc = deploy.main(["wf_0007", *TARGET, "--src-schema", "MIG_SRC_WF0007", "--root", str(root)])

    assert rc == 0
    out = capsys.readouterr().out
    assert out == (
        "# deploy.py wf_0007: dry run -- printed only, nothing was executed\n"
        "# workflows/wf_0007/procs/README.md, with <SRC> and <TGT> filled in; the profile reads the\n"
        "# database from SNOWFLAKE_DATABASE:\n"
        "SNOWFLAKE_DATABASE=ANALYTICS dbt run --project-dir workflows/wf_0007/dbt --profiles-dir "
        "workflows/wf_0007/dbt --target snowflake --vars "
        "'{\"src_schema\": \"MIG_SRC_WF0007\", \"tgt_schema\": \"CURATED\"}'\n")
    assert fake.connect_calls == []


def test_an_unvalidated_workflow_is_refused(repo, fake, capsys):
    manifest = read_json(repo.wf(WF, "manifest.json"))
    manifest["status"]["translate"] = "TRANSLATED"
    write_json(repo.wf(WF, "manifest.json"), manifest)

    assert deploy.main([WF, *TARGET, "--root", str(repo.root)]) == 1
    assert "VALIDATED" in capsys.readouterr().err
    assert deploy.main([WF, *TARGET, "--connection", "prod", "--execute", "--root", str(repo.root)]) == 1
    assert fake.connect_calls == []


def test_execute_needs_a_connection(repo, fake, capsys):
    assert _code([WF, *TARGET, "--execute", "--root", str(repo.root)]) == 2
    assert "MIG_SNOWFLAKE_CONNECTION" in capsys.readouterr().err
    assert fake.connect_calls == []


# --- further coverage ---------------------------------------------------------------------------------

@pytest.mark.parametrize("change", ["missing", "FAIL"])
def test_a_workflow_whose_chain_did_not_pass_is_refused(repo, fake, capsys, change):
    """Ruling (4): `validation_workflow.json` is the VALIDATED gate since Task W1; deploy never bypasses it."""
    chain = repo.wf(WF, "validation_workflow.json")
    if change == "missing":
        chain.unlink()
    else:
        write_json(chain, {**read_json(chain), "verdict": "FAIL"})

    assert deploy.main([WF, *TARGET, "--connection", "prod", "--execute", "--root", str(repo.root)]) == 1
    assert "validation_workflow.json" in capsys.readouterr().err
    assert fake.connect_calls == []


def test_usage_errors_exit_2_and_connect_to_nothing(repo, fake, tmp_path, capsys):
    assert _code(["wf_0001", *TARGET, "--root", str(tmp_path / "empty")]) == 2       # never prepared
    assert _code([WF, "--database", "ANALYTICS; DROP DATABASE X", "--schema", "CURATED",
                  "--root", str(repo.root)]) == 2
    assert _code([WF, "--database", "ANALYTICS", "--schema", "curated", "--root", str(repo.root)]) == 2
    assert _code([WF, *TARGET, "--src-schema", "A.B", "--root", str(repo.root)]) == 2
    assert _code([WF, *TARGET, "--password", "x", "--root", str(repo.root)]) == 2
    assert "unrecognized arguments: --password" in capsys.readouterr().err
    repo.seg(WF, "seg_02", "proc.sql").unlink()
    assert _code([WF, *TARGET, "--root", str(repo.root)]) == 2
    assert "seg_02" in capsys.readouterr().err
    assert fake.connect_calls == []


def test_a_statement_that_fails_on_execute_is_a_redacted_domain_failure(repo, fake, capsys):
    fake.account.fail["WF0006_SEG_02"] = "SQL access control error: password=hunter2-secret"

    assert deploy.main([WF, *TARGET, "--connection", "prod", "--execute", "--root", str(repo.root)]) == 1

    err = capsys.readouterr().err
    assert f"workflows/{WF}/segments/seg_02/proc.sql" in err and "hunter2-secret" not in err
    assert fake.sql()[-1].startswith("CREATE OR REPLACE PROCEDURE MIG_WORK.WF0006_SEG_02")   # stopped there
    assert fake.connections[0].closed


def test_a_connection_that_cannot_be_opened_is_exit_2_with_the_error_redacted(repo, fake, monkeypatch, capsys):
    def refuse(name):
        raise RuntimeError(f"250001: Could not connect: private_key_file_pwd=hunter2-secret for {name}")
    monkeypatch.setattr(snowflake_conn, "connect", refuse)

    assert deploy.main([WF, *TARGET, "--connection", "prod", "--execute", "--root", str(repo.root)]) == 2

    err = capsys.readouterr().err
    assert "Could not connect" in err and "'prod'" in err and "hunter2-secret" not in err
    assert "Traceback" not in err
    assert fake.statements == []


def test_the_example_call_uses_the_source_schema_when_given(repo, fake, capsys):
    assert deploy.main([WF, *TARGET, "--src-schema", "MIG_SRC_WF0006", "--root", str(repo.root)]) == 0
    assert ("-- CALL MIG_WORK.WF0006_MASTER('ANALYTICS', 'MIG_SRC_WF0006', 'ANALYTICS', 'CURATED', '<run id>');\n"
            in capsys.readouterr().out)


# --- dbt ----------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def validated_wf_0007(tmp_path_factory):
    """The committed `workflows/wf_0007` (phase-2 Task G), copied: a VALIDATED dbt workflow exactly as
    the orchestrator leaves one -- `output_kind: dbt`, a PASS `validation_workflow.json` from
    `validate_dbt`, and `procs/README.md` (`dbtReadme`) in place of `master.sql`."""
    root = tmp_path_factory.mktemp("wf_0007")
    copy_pristine_mappings_and_catalog(root)
    shutil.copytree(ROOT / "workflows" / "wf_0007", root / "workflows" / "wf_0007")
    repo = Repo(root)
    manifest = read_json(repo.wf("wf_0007", "manifest.json"))
    assert manifest["output_kind"] == "dbt" and manifest["status"]["translate"] == "VALIDATED"
    assert read_json(repo.wf("wf_0007", "validation_workflow.json"))["verdict"] == "PASS"
    assert repo.wf("wf_0007", "procs", "README.md").is_file()
    return root


def test_a_dbt_execute_runs_dbt_snowflake_through_run_dbt(validated_wf_0007, fake, monkeypatch, capsys):
    recorded = []

    def run_dbt(command, project, **kwargs):
        recorded.append((command, project, kwargs))
        return DbtResult(0, "Completed successfully", [], None)
    monkeypatch.setattr(dbt_project, "run_dbt", run_dbt)
    monkeypatch.setattr(snowflake_conn, "require_dbt_snowflake", lambda: None)
    argv = ["wf_0007", *TARGET, "--src-schema", "MIG_SRC_WF0007", "--execute", "--root", str(validated_wf_0007)]

    assert deploy.main(argv) == 0

    assert recorded == [("run", Repo(validated_wf_0007).wf("wf_0007", "dbt"),
                         {"vars": {"src_schema": "MIG_SRC_WF0007", "tgt_schema": "CURATED"}, "target": "snowflake",
                          "duckdb_path": None, "extra_env": {"SNOWFLAKE_DATABASE": "ANALYTICS"}})]
    assert fake.connect_calls == []

    def failing(command, project, **kwargs):
        return DbtResult(1, "Database Error: password=hunter2-secret rejected", [], None)
    monkeypatch.setattr(dbt_project, "run_dbt", failing)
    assert deploy.main(argv) == 1
    err = capsys.readouterr().err
    assert "dbt run exited 1" in err and "hunter2-secret" not in err


def test_a_dbt_execute_names_what_is_missing(validated_wf_0007, fake, monkeypatch, capsys):
    root = str(validated_wf_0007)

    def missing():
        raise DbtUnavailable("dbt-snowflake is not installed beside this interpreter")
    monkeypatch.setattr(snowflake_conn, "require_dbt_snowflake", missing)
    assert _code(["wf_0007", *TARGET, "--src-schema", "S", "--execute", "--root", root]) == 2
    assert "dbt-snowflake" in capsys.readouterr().err

    monkeypatch.setattr(snowflake_conn, "require_dbt_snowflake", lambda: None)
    assert _code(["wf_0007", *TARGET, "--execute", "--root", root]) == 2                # no --src-schema
    assert "--src-schema" in capsys.readouterr().err
