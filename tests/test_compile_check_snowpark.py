"""Dry-checking a Snowpark segment: snowpark_rules on proc.py, plus proc.sql staying in sync."""
import json
import subprocess
import sys
from pathlib import Path

from lib.io import read_json, write_yaml
from lib.paths import Repo
import compile_check as cc
import render_snowpark as rs
from test_snowpark_rules import CONTRACT as RULES_CONTRACT, GOOD

WF, SEG = "wf_0006", "seg_02"
CONTRACT = {**RULES_CONTRACT, "target": "snowpark"}


def build(tmp_path, proc_py=GOOD, contract=None, render=True):
    repo = Repo(tmp_path)
    write_yaml(tmp_path / "mappings" / "global.yaml", {"program": {"snowpark_runtime": "3.11"}})
    seg_dir = repo.seg(WF, SEG)
    seg_dir.mkdir(parents=True, exist_ok=True)
    (seg_dir / "contract.json").write_text(
        json.dumps(contract if contract is not None else CONTRACT, indent=2) + "\n", encoding="utf-8", newline="\n")
    (seg_dir / "proc.py").write_text(proc_py, encoding="utf-8", newline="\n")
    if render:
        assert rs.main([WF, SEG, "--root", str(tmp_path)]) == 0
    return repo


def test_a_matching_procedure_compiles(tmp_path):
    repo = build(tmp_path)
    report = cc.compile_check(repo, WF, SEG)
    assert report == {"status": "OK", "target": "snowpark", "errors": [], "statements": 0}
    assert read_json(repo.seg(WF, SEG, "compile_check.json")) == report


def test_a_rule_violation_is_an_error(tmp_path):
    bad = GOOD.replace("import pandas as pd", "import pandas as pd\nimport os")
    repo = build(tmp_path, proc_py=bad)
    report = cc.compile_check(repo, WF, SEG)
    assert report["status"] != "OK"
    assert any(e.startswith("rule:imports") for e in report["errors"])


def test_a_hand_edited_proc_sql_is_a_render_mismatch(tmp_path):
    repo = build(tmp_path)
    proc_sql = repo.seg(WF, SEG, "proc.sql")
    proc_sql.write_text(proc_sql.read_text(encoding="utf-8") + "\n-- hand edited\n", encoding="utf-8", newline="\n")
    report = cc.compile_check(repo, WF, SEG)
    assert report["status"] != "OK"
    assert any(e.startswith("rule:render_mismatch") for e in report["errors"])


def test_a_proc_py_that_cannot_be_rendered_is_a_render_mismatch(tmp_path):
    """proc.py mutated after render() so it now contains `$$` (render_snowpark.render() itself
    refuses this -- test_render_snowpark.py covers that); compile_check must not let the
    ValueError escape, it is one more render_mismatch."""
    repo = build(tmp_path)
    proc_py = repo.seg(WF, SEG, "proc.py")
    proc_py.write_text(GOOD.replace('return "OK"', 'return "$$"'), encoding="utf-8", newline="\n")
    report = cc.compile_check(repo, WF, SEG)
    assert report["status"] != "OK"
    assert any(e.startswith("rule:render_mismatch") for e in report["errors"])


def test_target_sql_on_a_python_wrapper_is_an_error(tmp_path):
    repo = build(tmp_path)
    report = cc.compile_check(repo, WF, SEG, target="sql")
    assert report["status"] != "OK"


def run_cli(tmp_path, *extra):
    return subprocess.run([sys.executable, str(Path(cc.__file__)), WF, SEG, "--root", str(tmp_path), *extra],
                          capture_output=True, text=True)


def test_cli_exits_zero_when_the_procedure_compiles(tmp_path):
    build(tmp_path)
    done = run_cli(tmp_path)
    assert done.returncode == 0 and "OK" in done.stdout


def test_cli_exits_one_on_a_rule_violation(tmp_path):
    bad = GOOD.replace("import pandas as pd", "import pandas as pd\nimport os")
    build(tmp_path, proc_py=bad)
    done = run_cli(tmp_path)
    assert done.returncode == 1


def test_cli_exits_two_when_proc_py_is_missing(tmp_path):
    repo = Repo(tmp_path)
    write_yaml(tmp_path / "mappings" / "global.yaml", {"program": {"snowpark_runtime": "3.11"}})
    seg_dir = repo.seg(WF, SEG)
    seg_dir.mkdir(parents=True, exist_ok=True)
    (seg_dir / "contract.json").write_text(json.dumps(CONTRACT, indent=2) + "\n", encoding="utf-8", newline="\n")
    done = run_cli(tmp_path)
    assert done.returncode == 2
