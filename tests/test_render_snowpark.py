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


def test_cli_exits_one_when_proc_py_cannot_be_rendered(tmp_path):
    """Task 3 fix round 1 (coordinator ruling): `$$` inside proc.py is a DOMAIN failure -- the
    renderer itself checked the content and refused it -- so the CLI exits 1, not 2. A missing
    proc.py (test_cli_writes_proc_sql_next_to_proc_py above) stays a USAGE error, exit 2."""
    repo = Repo(tmp_path)
    write_yaml(tmp_path / "mappings" / "global.yaml", {"program": {"snowpark_runtime": "3.11"}})
    path = repo.seg("wf_0006", "seg_02", "proc.py")
    path.parent.mkdir(parents=True)
    path.write_text(PY.replace('"OK"', '"$$"'), encoding="utf-8")
    assert rs.main(["wf_0006", "seg_02", "--root", str(tmp_path)]) == 1
    assert not repo.seg("wf_0006", "seg_02", "proc.sql").exists()


def test_cli_exits_two_when_render_raises_something_other_than_valueerror(tmp_path, monkeypatch, capsys):
    """Task 3 fix round 2 (coordinator ruling): render()'s try block caught only ValueError, so any
    other exception (a bug, not a domain failure) would have escaped as an uncaught traceback with
    no controlled exit code. It must exit 2 like every other "unexpected exception" path."""
    repo = Repo(tmp_path)
    write_yaml(tmp_path / "mappings" / "global.yaml", {"program": {"snowpark_runtime": "3.11"}})
    path = repo.seg("wf_0006", "seg_02", "proc.py")
    path.parent.mkdir(parents=True)
    path.write_text(PY, encoding="utf-8")

    def boom(*args, **kwargs):
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(rs, "render", boom)
    assert rs.main(["wf_0006", "seg_02", "--root", str(tmp_path)]) == 2
    assert "RuntimeError" in capsys.readouterr().err
    assert not repo.seg("wf_0006", "seg_02", "proc.sql").exists()
