"""The dbt project is a CLOSED surface, enforced before dbt ever runs (final fix wave, C1).

The final review proved that a Python model, an `on-run-start`/`on-run-end` hook or a
`pre_hook`/`post_hook` carrying arbitrary SQL passed `compile_check.py --target dbt` and RAN during
`validate_dbt.py`. `lib.dbt_project.check_surface` is now the one choke point: `run_dbt` calls it
before building any argument, `compile_check_dbt` reports its errors and then skips `dbt parse`,
and `validate_dbt` FAILs every segment without starting a dbt process.

The first half of this file is the review's scenario, committed: each unsafe construct laid over a
scratch copy of the canned `wf_0007` project, refused by compile_check under its own check name and
refused by validate_dbt with no dbt process started and no PASS written -- and, where the payload
would write a marker file (always inside this test's own `tmp_path`), no marker. The second half
pins the gate's finer rules on small projects of their own, and checks every committed dbt tree
still passes it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import compile_check as cc
import validate_dbt as vd
from lib import dbt_project as dp
from lib.io import read_json
from lib.paths import Repo
from tests.helpers import overlay_dbt_project, prepare_workflow

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
WF = "wf_0007"
REGION_CONFIG = "{{ config(materialized='table', alias='REGION_ATTAINMENT') }}"
HISTORY_CONFIG = ("{{ config(materialized='incremental', incremental_strategy='merge', "
                  "unique_key=['REGION', 'PERIOD'], alias='ATTAINMENT_HISTORY') }}")


# --- a scratch copy of the canned wf_0007, and a spy on every process dbt could start ------------


@pytest.fixture(scope="module")
def pristine(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("wf0007_pristine")
    prepare_workflow(root, WF)
    return root


@pytest.fixture
def scratch(pristine, tmp_path) -> Repo:
    root = tmp_path / "root"
    shutil.copytree(pristine, root)
    return Repo(root)


@pytest.fixture
def processes(monkeypatch) -> list:
    """Every `subprocess.run` call made while the test runs (dbt is only ever a subprocess of
    `run_dbt`): recorded, then passed through, so a gate that failed to stop dbt shows up both
    here and as the payload's own marker file."""
    calls: list = []
    real = subprocess.run

    def spy(args, *rest, **kwargs):
        calls.append(args)
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(dp.subprocess, "run", spy)
    return calls


def _project(repo: Repo) -> Path:
    return dp.project_dir(repo, WF)


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{path.name} no longer holds {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8", newline="\n")


# --- the review's cases: each writes a marker (inside tmp_path) if dbt ever runs it --------------


def _python_model(project: Path, marker: Path, tmp: Path) -> None:
    (project / "models" / "wf0007_seg_01_out.sql").unlink()
    (project / "models" / "wf0007_seg_01_out.py").write_text(
        "def model(dbt, session):\n"
        "    dbt.config(materialized='table')\n"
        f"    with open({marker.as_posix()!r}, 'w') as handle:\n"
        "        handle.write('a python model ran')\n"
        "    return session.sql(\"select 'X' as REGION, '2026-01' as PERIOD, 1.0 as TARGET, 1.0 as ACTUAL\")\n",
        encoding="utf-8", newline="\n")


def _on_run_start(project: Path, marker: Path, tmp: Path) -> None:
    _append(project / "dbt_project.yml",
            f"on-run-start:\n  - \"COPY (SELECT 1 AS X) TO '{marker.as_posix()}'\"\n")


def _models_post_hook(project: Path, marker: Path, tmp: Path) -> None:
    _append(project / "dbt_project.yml",
            f"models:\n  {WF}:\n    +post-hook: \"COPY (SELECT 1 AS X) TO '{marker.as_posix()}'\"\n")


def _post_hook_copy(project: Path, marker: Path, tmp: Path) -> None:
    _edit(project / "models" / "region_attainment.sql", REGION_CONFIG,
          "{{ config(materialized='table', alias='REGION_ATTAINMENT', "
          f"post_hook=\"COPY {{{{ this }}}} TO '{marker.as_posix()}'\") }}}}")


def _sql_header(project: Path, marker: Path, tmp: Path) -> None:
    # No parenthesis anywhere, so the pre-fix config check (which refused any `name(` in a config
    # argument) had nothing to catch: the header names a table the sandbox already holds.
    _edit(project / "models" / "region_attainment.sql", REGION_CONFIG,
          "{{ config(materialized='table', alias='REGION_ATTAINMENT', "
          f"sql_header=\"COPY MIGDB__MIG_WORK.ATTAINMENT_HISTORY TO '{marker.as_posix()}';\") }}}}")


def _yaml_run_query(project: Path, marker: Path, tmp: Path) -> None:
    _edit(project / "models" / "schema.yml", 'description: "Tool 6, overwrite: logical REGION_ATTAINMENT."',
          f"description: '{{{{ run_query(\"COPY (SELECT 1 AS X) TO ''{marker.as_posix()}''\") }}}}'")


def _macro_override(project: Path, marker: Path, tmp: Path) -> None:
    (project / "macros").mkdir()
    (project / "macros" / "x.sql").write_text(
        "{% macro generate_alias_name(custom_alias_name=none, node=none) -%}\n"
        f"  {{%- do run_query(\"COPY (SELECT 1 AS X) TO '{marker.as_posix()}'\") -%}}\n"
        "  {%- if custom_alias_name -%}{{ custom_alias_name | trim }}{%- else -%}{{ node.name }}{%- endif -%}\n"
        "{%- endmacro %}\n", encoding="utf-8", newline="\n")


def _packages_yml(project: Path, marker: Path, tmp: Path) -> None:
    package = tmp / "package"
    (package / "macros").mkdir(parents=True)
    (package / "dbt_project.yml").write_text("name: evil\nversion: '1.0.0'\nconfig-version: 2\n",
                                             encoding="utf-8", newline="\n")
    (project / "packages.yml").write_text(f"packages:\n  - local: {package.as_posix()}\n",
                                          encoding="utf-8", newline="\n")


def _read_csv_model(project: Path, marker: Path, tmp: Path) -> None:
    secret = tmp / "secret.csv"
    secret.write_text("REGION,PERIOD,TARGET_TOTAL,ACTUAL_TOTAL,LINES\nSECRET,2026-01,1.00,1.00,1\n",
                      encoding="utf-8", newline="\n")
    (project / "models" / "region_attainment.sql").write_text(
        f"{REGION_CONFIG}\n-- tool 6: Output Data (overwrite, logical REGION_ATTAINMENT)\n"
        f"select * from read_csv('{secret.as_posix()}')\n", encoding="utf-8", newline="\n")


def _raw_table_model(project: Path, marker: Path, tmp: Path) -> None:
    (project / "models" / "region_attainment.sql").write_text(
        f"{REGION_CONFIG}\n-- tool 6: Output Data (overwrite, logical REGION_ATTAINMENT)\n"
        "select REGION, PERIOD, TARGET_TOTAL, ACTUAL_TOTAL, LINES from MIGDB__MIG_WORK.ATTAINMENT_HISTORY\n",
        encoding="utf-8", newline="\n")


def _hook_reads_another_table(project: Path, marker: Path, tmp: Path) -> None:
    _edit(project / "models" / "attainment_history.sql", HISTORY_CONFIG,
          HISTORY_CONFIG[:-4] + ", pre_hook=\"DELETE FROM {{ this }} WHERE REGION IN "
                                "(SELECT REGION FROM MIGDB__MIG_WORK.WF0007_SEG_01_OUT)\") }}")


#: name -> (how to lay it over the project, the check compile_check names it by)
CASES = {
    "python_model": (_python_model, "dbt:surface"),
    "on_run_start_copy": (_on_run_start, "dbt:project_yml"),
    "models_block_post_hook": (_models_post_hook, "dbt:project_yml"),
    "post_hook_copy": (_post_hook_copy, "dbt:hook_sql"),
    "sql_header": (_sql_header, "dbt:model_jinja"),
    "schema_yml_run_query": (_yaml_run_query, "dbt:yaml"),
    "macro_generate_alias_name": (_macro_override, "dbt:surface"),
    "packages_yml": (_packages_yml, "dbt:surface"),
    "model_read_csv": (_read_csv_model, "dbt:model_sql"),
    "model_raw_table": (_raw_table_model, "dbt:model_sql"),
    "hook_reads_another_table": (_hook_reads_another_table, "dbt:hook_sql"),
}


def _lay(case: str, repo: Repo, tmp_path: Path) -> Path:
    marker = tmp_path / "markers" / f"{case}.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    CASES[case][0](_project(repo), marker, tmp_path)
    return marker


@pytest.mark.parametrize("case", sorted(CASES))
def test_compile_check_refuses_each_unsafe_construct_by_name_and_never_runs_dbt(case, scratch, tmp_path, processes):
    marker = _lay(case, scratch, tmp_path)
    report = cc.compile_check_dbt(scratch, WF)
    check = CASES[case][1]
    assert report["status"] == "ERROR"
    assert any(error.startswith(f"{check}: ") for error in report["errors"]), report["errors"]
    assert processes == [], "compile_check ran dbt parse over a project that fails the surface gate"
    assert not marker.exists()
    assert read_json(_project(scratch) / "compile_check.json") == report
    assert cc.main([WF, "--target", "dbt", "--root", str(scratch.root)]) == 1


@pytest.mark.parametrize("case", sorted(CASES))
def test_validate_dbt_starts_no_dbt_process_and_writes_no_pass(case, scratch, tmp_path, processes, capsys):
    marker = _lay(case, scratch, tmp_path)
    rc = vd.main([WF, "--set", "normal", "--root", str(scratch.root)])
    assert rc == 1, capsys.readouterr()
    assert processes == [], "validate_dbt started a dbt process over a project that fails the surface gate"
    assert not marker.exists(), "the payload ran"
    for seg in ("seg_01", "seg_02"):
        report = read_json(scratch.seg(WF, seg, "validation.json"))
        assert report["verdict"] == "FAIL" and report["idempotent"] is None
        assert report["error"].startswith("dbt:surface: "), report["error"]
        assert CASES[case][1] in report["error"]
        assert read_json(scratch.seg(WF, seg, "validation.normal.json"))["verdict"] == "FAIL"
    chain = read_json(scratch.wf(WF, "validation_workflow.json"))
    assert chain["verdict"] == "FAIL" and chain["divergence_kind"] == "boundary"
    assert chain["first_divergence"]["segment"] == "seg_01"
    assert not list(scratch.wf(WF).glob("dbt_sandbox_*.duckdb"))


def test_the_pristine_scratch_copy_passes_the_gate_and_compile_check(scratch, processes):
    """The control for both matrices above: the same scratch copy, unmodified, is not refused."""
    assert dp.check_surface(_project(scratch)) == []
    report = cc.compile_check_dbt(scratch, WF)
    assert report["status"] == "OK", report["errors"]
    assert len(processes) == 1, "exactly one dbt parse"


def test_run_dbt_refuses_an_unsafe_project_before_starting_any_process(scratch, tmp_path, processes):
    marker = _lay("python_model", scratch, tmp_path)
    with pytest.raises(dp.DbtUnsafe) as refused:
        dp.run_dbt("run", _project(scratch), vars=dp.local_vars("MIG_GOLDEN_WF0007_NORMAL"),
                   duckdb_path=tmp_path / "dbt_sandbox_x.duckdb")
    assert isinstance(refused.value, ValueError)
    assert "dbt:surface" in str(refused.value) and "wf0007_seg_01_out.py" in str(refused.value)
    assert refused.value.errors and all(e.startswith("dbt:") for e in refused.value.errors)
    assert processes == [] and not marker.exists()


def test_deploy_execute_is_gated_too(scratch, tmp_path, processes, monkeypatch):
    """Every caller of run_dbt is gated, deploy.py's dbt path included."""
    import deploy
    from lib import snowflake_conn
    monkeypatch.setattr(snowflake_conn, "require_dbt_snowflake", lambda: None)
    marker = _lay("on_run_start_copy", scratch, tmp_path)
    with pytest.raises(dp.DbtUnsafe):
        deploy._execute_dbt(scratch, WF, "SANDBOX_DB", "MIG_WORK", "MIG_SRC")
    assert processes == [] and not marker.exists()


# --- the gate's finer rules, on small projects of their own ---------------------------------------


PROFILES = dp.PROFILES_TEMPLATE
PROJECT_YML = ("name: wf_0009\nversion: \"1.0.0\"\nconfig-version: 2\nprofile: alteryx_migration\n"
               "model-paths: [models]\nvars:\n  src_schema: null\n  tgt_schema: null\n")
SOURCES_YML = ("version: 2\nsources:\n  - name: src\n    schema: \"{{ var('src_schema') }}\"\n    tables:\n"
               "      - name: ITEMS\n        description: the items\n        columns:\n          - name: ID\n"
               "            description: the key\n")
SCHEMA_YML = ("version: 2\nmodels:\n  - name: items_out\n    description: out\n    columns:\n"
              "      - name: ID\n        description: key\n        data_tests:\n          - not_null\n"
              "          - unique\n          - accepted_values:\n              values: [1, 2, 'three']\n"
              "              quote: false\n          - relationships:\n              to: ref('items_in')\n"
              "              field: ID\n      - name: NOTE\n        tests:\n          - not_null\n")
MODEL = ("{{ config(materialized='table', alias='ITEMS_OUT') }}\n-- tool 4: Output Data\n"
         "with t1 as (select ID from {{ source('src', 'ITEMS') }})\nselect ID from t1\n")


def _mini(tmp_path: Path, files: dict[str, str | None] | None = None) -> Path:
    """A small valid project at tmp_path/project; `files` overrides (None deletes) a relative path."""
    project = tmp_path / "project"
    base = {"dbt_project.yml": PROJECT_YML, "profiles.yml": PROFILES, "models/sources.yml": SOURCES_YML,
            "models/schema.yml": SCHEMA_YML, "models/items_out.sql": MODEL, "README.md": "# x\n"}
    base.update(files or {})
    for rel, text in base.items():
        path = project / rel
        if text is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return project


def _refusals(project: Path, name: str) -> list[str]:
    return [e for e in dp.check_surface(project) if e.startswith(f"{name}: ")]


def test_the_small_valid_project_passes(tmp_path):
    assert dp.check_surface(_mini(tmp_path)) == []


@pytest.mark.parametrize("rel", [
    "models/x.py", "x.py", "logs/x.py", "models/extra.yml", "models/sub/schema.yml", "models/sources.yaml",
    "models/docs.md", "models/seed.csv", "macros/x.sql", "snapshots/s.sql", "seeds/s.csv", "analyses/a.sql",
    "tests/t.sql", "dbt_packages/p/dbt_project.yml", "packages.yml", "dependencies.yml", "package-lock.yml",
    "selectors.yml", ".user.yml", ".dbtignore", "models/Items.SQL", "models/a b.sql", "models/.hidden.sql",
])
def test_anything_outside_the_closed_file_set_is_refused_by_its_relative_path(tmp_path, rel):
    project = _mini(tmp_path, {rel: "select 1 as X\n"})
    refusals = _refusals(project, "dbt:surface")
    assert any(rel.split("/")[0] in e for e in refusals), dp.check_surface(project)


def test_output_directories_and_the_documented_files_are_allowed(tmp_path):
    project = _mini(tmp_path, {"logs/validate_normal.log": "x", "target/run/x.json": "{}",
                               "translation_notes.md": "x", "fix_log.md": "x", "compile_check.json": "{}",
                               "review.json": "{}", "models/sub/deeper/y.sql": "select 1 as Y\n"})
    assert dp.check_surface(project) == []


def _link(target: Path, link: Path) -> None:
    """A directory link without privileges: a junction on Windows, a symlink elsewhere."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.mark.parametrize("where", ["logs", "models/linked", "target"])
def test_a_symlink_or_junction_anywhere_is_refused(tmp_path, where):
    project = _mini(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "y.sql").write_text("select 1 as Y\n", encoding="utf-8")
    link = project / where
    link.parent.mkdir(parents=True, exist_ok=True)
    _link(outside, link)
    assert any(where in e and "link" in e for e in _refusals(project, "dbt:surface")), dp.check_surface(project)


def test_compile_check_never_writes_its_report_through_a_link(scratch, tmp_path, processes):
    outside = tmp_path / "outside"
    outside.mkdir()
    report_path = _project(scratch) / "compile_check.json"
    _link(outside, report_path)
    report = cc.compile_check_dbt(scratch, WF)
    assert any(e.startswith("dbt:surface: compile_check.json") and "link" in e for e in report["errors"])
    assert processes == []
    assert report_path.is_file() and not report_path.is_symlink()
    assert read_json(report_path) == report and list(outside.iterdir()) == []


def test_profiles_yml_must_be_the_template_for_run_dbt_too(tmp_path):
    project = _mini(tmp_path, {"profiles.yml": PROFILES + "      plugins:\n        - module: evil\n"})
    assert _refusals(project, "dbt:profiles")


@pytest.mark.parametrize("extra, why", [
    ("on-run-end: [\"select 1\"]\n", "on-run-end"),
    ("models:\n  wf_0009:\n    +sql_header: \"select 1\"\n", "models"),
    ("dispatch: []\n", "dispatch"),
    ("macro-paths: [elsewhere]\n", "macro-paths"),
    ("flags: {}\n", "flags"),
    ("query-comment: x\n", "query-comment"),
])
def test_dbt_project_yml_is_the_template(tmp_path, extra, why):
    project = _mini(tmp_path, {"dbt_project.yml": PROJECT_YML + extra})
    assert any(why in e for e in _refusals(project, "dbt:project_yml")), dp.check_surface(project)


@pytest.mark.parametrize("text", [
    PROJECT_YML.replace("model-paths: [models]", "model-paths: [\"../../wf_0001/dbt/models\"]"),
    PROJECT_YML.replace("  tgt_schema: null\n", "  tgt_schema: null\n  other: 1\n"),
    PROJECT_YML.replace("  tgt_schema: null\n", ""),
    PROJECT_YML.replace("profile: alteryx_migration", "profile: \"{{ env_var('P') }}\""),
    PROJECT_YML + "# {# a Jinja comment is still a delimiter #}\n",
    PROJECT_YML.replace("version: \"1.0.0\"\n", ""),
    "name: [unclosed\n",
    PROJECT_YML + "name: again\n",
])
def test_dbt_project_yml_keys_vars_and_delimiters(tmp_path, text):
    assert _refusals(_mini(tmp_path, {"dbt_project.yml": text}), "dbt:project_yml")


@pytest.mark.parametrize("text", [
    SOURCES_YML.replace("    tables:", "    loader: x\n    tables:"),
    SOURCES_YML.replace("    tables:", "    meta: {external_location: \"read_csv('x')\"}\n    tables:"),
    SOURCES_YML.replace("        description: the items\n", "        identifier: other\n"),
    SOURCES_YML.replace("            description: the key\n", "            data_tests: [not_null]\n"),
    SOURCES_YML.replace("\"{{ var('src_schema') }}\"", "MIGDB__MIG_WORK"),
    SOURCES_YML.replace("\"{{ var('src_schema') }}\"", "\"{{ var('tgt_schema') }}\""),
    SOURCES_YML.replace("description: the items", "description: \"{{ run_query('select 1') }}\""),
    SOURCES_YML.replace("description: the items", "description: \"\\x7b\\x7b run_query('select 1') }}\""),
    SOURCES_YML.replace("- name: ITEMS", "- name: 'IT\"EMS'"),
    SOURCES_YML + "# {{ var('x') }}\n",
    SOURCES_YML + "config: {}\n",
    SOURCES_YML.replace("version: 2", "version: 3"),
    "version: 2\nsources: &a []\nextra: *a\n",
])
def test_sources_yml_is_closed(tmp_path, text):
    assert _refusals(_mini(tmp_path, {"models/sources.yml": text}), "dbt:yaml")


def test_sources_yml_tolerates_whitespace_inside_the_var_braces(tmp_path):
    text = SOURCES_YML.replace("\"{{ var('src_schema') }}\"", "\"{{var('src_schema')}}\"")
    assert dp.check_surface(_mini(tmp_path, {"models/sources.yml": text})) == []


@pytest.mark.parametrize("old, new", [
    ("    description: out\n", "    config: {materialized: view}\n"),
    ("    description: out\n", "    docs: {show: false}\n"),
    ("        description: key\n", "        meta: {x: 1}\n"),
    ("          - unique\n", "          - dbt_utils.expression_is_true: {expression: 'x'}\n"),
    ("          - unique\n", "          - unique:\n              config: {where: '1=1'}\n"),
    ("          - unique\n", "          - not_null:\n              where: '1=1'\n"),
    ("values: [1, 2, 'three']", "values: [\"env_var('SECRET')\"]"),
    ("values: [1, 2, 'three']", "values: [{a: 1}]"),
    ("to: ref('items_in')", "to: source('src', 'ITEMS')"),
    ("to: ref('items_in')", "to: \"ref('items_in') ~ run_query('select 1')\""),
    ("description: out", "description: \"{{ doc('x') }}\""),
    ("version: 2\nmodels:", "version: 2\nsources: []\nmodels:"),
])
def test_schema_yml_is_closed(tmp_path, old, new):
    assert old in SCHEMA_YML
    assert _refusals(_mini(tmp_path, {"models/schema.yml": SCHEMA_YML.replace(old, new)}), "dbt:yaml")


@pytest.mark.parametrize("config", [
    "sql_header='select 1'", "database='x'", "schema='x'", "grants={'select': ['r']}",
    "on_schema_change='fail'", "full_refresh=true", "meta={'a': 'b'}", "persist_docs={'relation': true}",
    "materialized='external'", "materialized='seed'", "incremental_strategy='microbatch'",
    "alias='ITEMS\" ; COPY (SELECT 1) TO ''x''; --'", "unique_key=['ID) ; COPY (SELECT 1) TO ''x''; --']",
    "unique_key='ID ID'", "alias=this", "alias='A' ~ 'B'", "materialized=['table']", "alias=r'ITEMS'",
    "'table'", "**{'alias': 'X'}", "alias='A', alias='B'", "alias=('A',)", "unique_key=['ID', 1]",
])
def test_model_config_keyword_arguments_are_closed(tmp_path, config):
    model = MODEL.replace("materialized='table', alias='ITEMS_OUT'", config)
    assert _refusals(_mini(tmp_path, {"models/items_out.sql": model}), "dbt:model_jinja"), config


@pytest.mark.parametrize("config", [
    "materialized='table', alias='ITEMS_OUT'",
    "materialized='incremental', incremental_strategy='append', alias='ITEMS_OUT'",
    "materialized='incremental', incremental_strategy='merge', unique_key=['ID', 'NOTE'], alias='ITEMS_OUT'",
    "materialized='incremental', incremental_strategy='merge', unique_key='ID', alias='ITEMS_OUT'",
    "materialized='view'",
    "",
])
def test_the_configs_a_migration_needs_are_accepted(tmp_path, config):
    model = MODEL.replace("materialized='table', alias='ITEMS_OUT'", config)
    assert dp.check_surface(_mini(tmp_path, {"models/items_out.sql": model})) == []


HOOK_OK = [
    "delete from {{ this }} where 1 = 0",
    "DELETE FROM {{ this }} WHERE ACCT = 'OLD'",
    "DELETE FROM {{this}} WHERE ACCT IN ('A', 'B') AND AMOUNT > 0",
    "UPDATE {{ this }} SET NOTE = UPPER(NOTE) WHERE ID IN (SELECT ID FROM {{ this }} WHERE NOTE IS NULL)",
    "INSERT INTO {{ this }} (ID, NOTE) VALUES (1, 'x')",
    "INSERT INTO {{ this }} SELECT * FROM {{ this }} WHERE ID < 0",
    "TRUNCATE {{ this }}",
    "TRUNCATE TABLE {{ this }};",
    "WITH old AS (SELECT ID FROM {{ this }} WHERE ID < 0) DELETE FROM {{ this }} WHERE ID IN (SELECT ID FROM old)",
    "",
    "   ",
]

HOOK_REFUSED = [
    "COPY {{ this }} TO 'x.csv'",
    "COPY (SELECT 1) TO 'x.csv'",
    "ATTACH 'x.duckdb' AS x",
    "DETACH x",
    "INSTALL httpfs",
    "LOAD httpfs",
    "PRAGMA version",
    "SET memory_limit = '1GB'",
    "RESET memory_limit",
    "CALL pragma_version()",
    "EXPORT DATABASE 'x'",
    "IMPORT DATABASE 'x'",
    "CREATE TABLE x AS SELECT 1",
    "DROP TABLE {{ this }}",
    "ALTER TABLE {{ this }} ADD COLUMN y INT",
    "GRANT SELECT ON {{ this }} TO r",
    "USE other",
    "SELECT 1",
    "DELETE FROM {{ this }}; DELETE FROM {{ this }}",
    "DELETE FROM {{ this }} WHERE ID IN (SELECT ID FROM other)",
    "DELETE FROM other",
    "DELETE FROM main.x WHERE 1 = 0",
    "DELETE FROM {{ this }} USING other WHERE 1 = 0",
    "UPDATE {{ this }} SET NOTE = getenv('HOME')",
    "UPDATE {{ this }} SET NOTE = current_setting('home_directory')",
    "UPDATE {{ this }} SET NOTE = (SELECT content FROM read_text('x'))",
    "UPDATE {{ this }} SET NOTE = read_text('x')",
    "INSERT INTO {{ this }} SELECT * FROM read_csv('x.csv')",
    "INSERT INTO {{ this }} SELECT * FROM 'x.csv'",
    "INSERT INTO {{ this }} SELECT * FROM glob('*')",
    "INSERT INTO {{ this }} SELECT * FROM query('select 1')",
    "INSERT INTO {{ this }} SELECT * FROM range(3)",
    "UPDATE {{ this }} SET NOTE = no_such_function_sqlglot_knows(1)",
    "WITH c AS (SELECT * FROM other) DELETE FROM {{ this }} WHERE ID IN (SELECT ID FROM c)",
    "DELETE FROM {{ this }} WHERE 1 = 0 -- a comment",
    "DELETE FROM {{ this }} /* a comment */ WHERE 1 = 0",
    "DELETE FROM {{ this }} WHERE NOTE = '{{ var(\"x\") }}'",
    "DELETE FROM {{- this }}",
    "DELETE FROM {{ this }} WHERE",
    "DELETE FROM x.{{ this }}",
]


@pytest.mark.parametrize("hook", HOOK_OK)
def test_a_plain_statement_against_this_is_an_accepted_hook(tmp_path, hook):
    model = MODEL.replace("alias='ITEMS_OUT'", f"alias='ITEMS_OUT', post_hook={hook!r}")
    assert dp.check_surface(_mini(tmp_path, {"models/items_out.sql": model})) == [], hook


@pytest.mark.parametrize("hook", HOOK_REFUSED)
def test_hook_sql_is_judged(tmp_path, hook):
    model = MODEL.replace("alias='ITEMS_OUT'", f"alias='ITEMS_OUT', pre_hook={hook!r}")
    errors = dp.check_surface(_mini(tmp_path, {"models/items_out.sql": model}))
    assert any(e.startswith(("dbt:hook_sql: ", "dbt:model_jinja: ")) for e in errors), (hook, errors)


def test_a_hook_list_or_an_escaped_delimiter_is_refused(tmp_path):
    for config in ("pre_hook=['delete from {{ this }} where 1 = 0']",
                   "pre_hook='\\x7b\\x7b run_query(1) }}'",
                   "post_hook='delete from {{ this }} where x = \\'{%\\''"):
        model = MODEL.replace("alias='ITEMS_OUT'", f"alias='ITEMS_OUT', {config}")
        errors = dp.check_surface(_mini(tmp_path / str(len(config)), {"models/items_out.sql": model}))
        assert any(e.startswith(("dbt:hook_sql: ", "dbt:model_jinja: ")) for e in errors), (config, errors)


MODEL_SQL_OK = [
    "select ID from {{ source('src', 'ITEMS') }}",
    "select ID from {{ ref('items_in') }} union all select ID from {{ this }}",
    "with a as (select ID from {{ source('src', 'ITEMS') }}), b as (select ID from a) select * from b",
    "select ID from (select ID from {{ source('src', 'ITEMS') }}) as s where ID in (select ID from {{ ref('x') }})",
    "select ID, cast(random() * 100 as decimal(19,2)) as R, left(NOTE, 4) as L, coalesce(NOTE, '') as N "
    "from {{ source('src', 'ITEMS') }}",
    "select ID from {{ source('src', 'ITEMS') }}\n{% if is_incremental() %}\nwhere ID > (select max(ID) from {{ this }})\n{% endif %}",
    "{% if is_incremental() %}\nselect ID from {{ this }}\n{% else %}\nselect ID from {{ source('src', 'ITEMS') }}\n{% endif %}",
    "select ID from {{ source('src', 'ITEMS') }} limit 1;",
]

MODEL_SQL_REFUSED = [
    "select * from read_csv('x.csv')",
    "select * from 'x.csv'",
    "select * from \"x.csv\"",
    "select * from read_parquet('x.parquet')",
    "select * from glob('*')",
    "select * from query('select 1')",
    "select * from query_table('t')",
    "select * from range(3)",
    "select * from sniff_csv('x.csv')",
    "select * from duckdb_settings()",
    "select read_text('x') as t",
    "select read_blob('x') as t",
    "select getenv('HOME') as t",
    "select current_setting('home_directory') as t",
    "select no_such_function_sqlglot_knows(1) as t",
    "select * from MIGDB__MIG_WORK.ATTAINMENT_HISTORY",
    "select * from items",
    "select * from main.{{ source('src', 'ITEMS') }}",
    "select * from {{ source('src', 'ITEMS') }} s, unnest([1, 2]) u",
    "select 1; select 2",
    "select 1 ) ; copy (select 1) to 'x' ; select ( 1",
    "copy (select 1) to 'x.csv'",
    "create table x as select 1",
    "delete from {{ this }}",
    "with a as (select * from b), b as (select 1 as ID) select * from a",
    "select * from (with c as (select 1 as ID) select * from c) x, c",
    "with recursive r as (select 1 as ID union all select ID + 1 from r where ID < 3) select * from r",
    "{% if is_incremental() %}{% if is_incremental() %}select 1{% else %}select * from other{% endif %}{% endif %}",
    "{% if is_incremental() %}select 1",
    "{% else %}select 1",
    "select 1 as ID {% if is_incremental() %} from {{ this }} {% else %} from other {% endif %}",
    "",
]


@pytest.mark.parametrize("body", MODEL_SQL_OK)
def test_model_sql_that_reads_only_through_source_ref_and_this_is_accepted(tmp_path, body):
    model = "{{ config(materialized='table', alias='ITEMS_OUT') }}\n-- tool 4: Output Data\n" + body + "\n"
    assert dp.check_surface(_mini(tmp_path, {"models/items_out.sql": model})) == [], body


@pytest.mark.parametrize("body", MODEL_SQL_REFUSED)
def test_model_sql_is_judged(tmp_path, body):
    model = "{{ config(materialized='table', alias='ITEMS_OUT') }}\n-- tool 4: Output Data\n" + body + "\n"
    errors = dp.check_surface(_mini(tmp_path, {"models/items_out.sql": model}))
    assert any(e.startswith("dbt:model_sql: models/items_out.sql") for e in errors), (body, errors)


def test_a_refusal_names_the_construct_but_never_the_file_content(tmp_path):
    model = MODEL.replace("select ID from t1", "select ID, 'hunter2' as P from t1, read_csv('x.csv')")
    errors = dp.check_surface(_mini(tmp_path, {"models/items_out.sql": model}))
    assert errors and all("hunter2" not in e for e in errors)
    assert any("read_csv" in e for e in errors)


# --- every committed dbt tree passes the gate -----------------------------------------------------


def _committed_projects() -> list[Path]:
    projects = [ROOT / "workflows" / WF / "dbt", SAMPLES / WF / "canned" / "dbt"]
    projects += sorted(p.parent for p in (ROOT / "tests" / "cookbook_examples" / "dbt").glob("*/project/dbt_project.yml"))
    return projects


@pytest.mark.parametrize("project", _committed_projects(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_committed_dbt_project_passes_the_gate(project):
    assert dp.check_surface(project) == []


def _broken_variants() -> list[Path]:
    return sorted((SAMPLES / WF / "broken_sql" / "dbt").glob("**/*.sql"))


@pytest.mark.parametrize("variant", _broken_variants(), ids=lambda p: p.name)
def test_every_broken_wf_0007_variant_still_passes_the_gate(variant, tmp_path):
    """A broken variant is one wrong TRANSLATION, judged by the runtime compare with its recorded
    class (`broken.json`, `test_e2e_parity`): the surface gate must not refuse it first."""
    repo = Repo(tmp_path)
    shutil.copytree(SAMPLES / WF / "canned" / "dbt", repo.wf(WF, "dbt"))
    project = overlay_dbt_project(repo, WF, tmp_path / "broken_project",
                                  {f"models/{variant.name}": variant})
    assert dp.check_surface(project) == []


def test_the_cookbook_hook_example_is_an_accepted_hook(tmp_path):
    page = (ROOT / "cookbook" / "dbt.md").read_text(encoding="utf-8")
    line = next(line for line in page.splitlines() if "pre_hook=" in line and line.startswith("{{ config("))
    hook = line.split('pre_hook="', 1)[1].rsplit('")', 1)[0]
    model = MODEL.replace("alias='ITEMS_OUT'", f"alias='ITEMS_OUT', pre_hook=\"{hook}\"")
    assert dp.check_surface(_mini(tmp_path, {"models/items_out.sql": model})) == []
