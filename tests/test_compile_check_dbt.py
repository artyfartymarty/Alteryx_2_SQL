"""Checking a dbt project's whole shape against its contracts through `dbt parse`'s manifest.

Each test builds `tests/dbt_fixtures.build_dbt_workflow`'s `wf_0009` project, optionally with
`replace=` overriding one or more of its files, and calls `compile_check.compile_check_dbt(repo,
WF)`. The test's name is what it asserts.
"""
from __future__ import annotations

from lib.io import read_json, write_json
import compile_check as cc
from tests import dbt_fixtures
from tests.dbt_fixtures import WF, build_dbt_workflow

ITEMS_OUT_SQL = dbt_fixtures.MODEL_FILES["models/items_out.sql"]
ITEMS_HIST_SQL = dbt_fixtures.MODEL_FILES["models/items_hist.sql"]
WORK_SQL = dbt_fixtures.MODEL_FILES["models/wf0009_seg_01_out.sql"]
SOURCES_YML = dbt_fixtures.MODEL_FILES["models/sources.yml"]
SCHEMA_YML = dbt_fixtures.MODEL_FILES["models/schema.yml"]

SCHEMA_YML_NO_ITEMS_HIST = """\
version: 2
models:
  - name: wf0009_seg_01_out
    columns:
      - name: ID
      - name: NOTE
      - name: AMOUNT
  - name: items_out
    columns:
      - name: ID
      - name: NOTE
      - name: AMOUNT
"""

SCHEMA_YML_ITEMS_OUT_COLUMNS_SWAPPED = """\
version: 2
models:
  - name: wf0009_seg_01_out
    columns:
      - name: ID
      - name: NOTE
      - name: AMOUNT
  - name: items_out
    columns:
      - name: NOTE
      - name: ID
      - name: AMOUNT
  - name: items_hist
    columns:
      - name: ID
        tests:
          - not_null
          - unique
      - name: NOTE
      - name: AMOUNT
"""


def test_the_fixture_project_passes_every_check(tmp_path):
    repo = build_dbt_workflow(tmp_path)
    report = cc.compile_check_dbt(repo, WF)
    assert report == {"status": "OK", "target": "dbt", "errors": [], "statements": 0, "models": 3}
    assert read_json(repo.wf(WF, "dbt", "compile_check.json")) == report


def test_a_project_dbt_cannot_parse_is_a_dbt_parse_error(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/items_out.sql": ITEMS_OUT_SQL.replace("{{ ref('wf0009_seg_01_out') }}", "{{ ref('nope') }}")})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    assert len(report["errors"]) == 1
    error = report["errors"][0]
    assert error.startswith("dbt:parse: dbt parse exited 2")
    assert "nope" in error


def test_a_missing_model_is_named(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/items_hist.sql": None,
        "models/schema.yml": SCHEMA_YML_NO_ITEMS_HIST})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    assert any(e.startswith("dbt:model_missing") and "items_hist" in e and "seg_02" in e
              for e in report["errors"])


def test_a_merge_model_with_the_wrong_unique_key_is_refused(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/items_hist.sql": ITEMS_HIST_SQL.replace("unique_key=['ID']", "unique_key=['NOTE']")})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    assert any(e.startswith("dbt:model_config") and "items_hist" in e and "unique_key" in e
              for e in report["errors"])


def test_an_overwrite_target_materialised_incremental_is_refused(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/items_out.sql": ITEMS_OUT_SQL.replace(
            "materialized='table', alias='ITEMS_OUT'",
            "materialized='incremental', incremental_strategy='append', alias='ITEMS_OUT'")})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    assert any(e.startswith("dbt:model_config") and "materialized" in e for e in report["errors"])


def test_a_target_model_without_its_upper_case_alias_is_refused(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/items_out.sql": ITEMS_OUT_SQL.replace(", alias='ITEMS_OUT'", "")})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    assert any(e.startswith("dbt:model_config") and "alias" in e and "ITEMS_OUT" in e
              for e in report["errors"])


def test_a_work_model_must_be_a_table(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/wf0009_seg_01_out.sql": WORK_SQL.replace("materialized='table'", "materialized='view'")})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    assert any(e.startswith("dbt:model_config") for e in report["errors"])


def test_schema_yml_columns_must_equal_the_contract_columns_in_order(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={"models/schema.yml": SCHEMA_YML_ITEMS_OUT_COLUMNS_SWAPPED})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    errors = [e for e in report["errors"] if e.startswith("dbt:columns")]
    assert errors
    assert any("ID" in e and "NOTE" in e and "AMOUNT" in e for e in errors)


def test_profiles_yml_must_be_the_template(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "profiles.yml": dbt_fixtures.dp.PROFILES_TEMPLATE.replace(
            "# The dbt profile", " # The dbt profile", 1)})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    assert any(e.startswith("dbt:profiles") for e in report["errors"])


def test_a_literal_credential_in_profiles_yml_is_refused(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "profiles.yml": dbt_fixtures.dp.PROFILES_TEMPLATE + "      password: hunter2\n"})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    assert any(e.startswith("dbt:profiles") for e in report["errors"])
    assert not any("hunter2" in e for e in report["errors"])


def test_sources_yml_must_declare_every_mapped_source(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/sources.yml": SOURCES_YML.replace("name: ITEMS", "name: ITEMZ")})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    assert any(e.startswith("dbt:sources") and "ITEMS" in e for e in report["errors"])


def test_every_data_node_needs_a_tool_comment(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/wf0009_seg_01_out.sql": WORK_SQL.replace("-- tool 2 (anchor T): Filter", "-- Filter")})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    assert any(e.startswith("dbt:tool_comments") and "2" in e for e in report["errors"])


def test_a_presql_needs_a_pre_hook(tmp_path):
    bad = tmp_path / "bad"
    repo = build_dbt_workflow(bad)
    dag = read_json(repo.seg(WF, "seg_02", "dag.json"))
    dag["nodes"][0]["config"]["pre_sql"] = "DELETE FROM X"
    write_json(repo.seg(WF, "seg_02", "dag.json"), dag)
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    assert any(e.startswith("dbt:hooks") and "4" in e for e in report["errors"])

    fixed_sql = ITEMS_OUT_SQL.replace(
        "materialized='table', alias='ITEMS_OUT'",
        "materialized='table', alias='ITEMS_OUT', "
        "pre_hook=\"delete from {{ this }} where 1 = 0\"")
    good = tmp_path / "good"
    repo2 = build_dbt_workflow(good, replace={"models/items_out.sql": fixed_sql})
    dag2 = read_json(repo2.seg(WF, "seg_02", "dag.json"))
    dag2["nodes"][0]["config"]["pre_sql"] = "DELETE FROM X"
    write_json(repo2.seg(WF, "seg_02", "dag.json"), dag2)
    report2 = cc.compile_check_dbt(repo2, WF)
    assert report2["status"] == "OK"


def test_dbt_parse_leaves_the_project_as_it_was(tmp_path):
    repo = build_dbt_workflow(tmp_path)
    project = dbt_fixtures.dp.project_dir(repo, WF)
    before = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
    cc.compile_check_dbt(repo, WF)
    after = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
    assert after == sorted(before + ["compile_check.json"])


def test_cli_exit_codes(tmp_path, capsys):
    good = tmp_path / "good"
    build_dbt_workflow(good)
    assert cc.main([WF, "--target", "dbt", "--root", str(good)]) == 0

    broken = tmp_path / "broken"
    build_dbt_workflow(broken, replace={
        "models/items_out.sql": ITEMS_OUT_SQL.replace("{{ ref('wf0009_seg_01_out') }}", "{{ ref('nope') }}")})
    assert cc.main([WF, "--target", "dbt", "--root", str(broken)]) == 1

    assert cc.main([WF, "seg_01", "--target", "dbt", "--root", str(good)]) == 2
    assert cc.main([WF, "--root", str(good)]) == 2

    empty = tmp_path / "empty"
    empty.mkdir()
    assert cc.main([WF, "--target", "dbt", "--root", str(empty)]) == 2
    assert "dbt_project.yml" in capsys.readouterr().err


# --- fix round 1 ---------------------------------------------------------------------------------


def test_a_blank_hook_is_not_a_hook(tmp_path):
    """I1: dbt's manifest turns `pre_hook=""` into `[{"sql": "", ...}]` -- a non-empty list that
    `if not hooks` alone would accept. A hook only counts if at least one entry's `sql` is
    non-blank; the same rule applies to post_hook."""
    cases = [
        ("pre_sql", "pre_hook", ""),
        ("pre_sql", "pre_hook", "   "),
        ("post_sql", "post_hook", ""),
        ("post_sql", "post_hook", "   "),
    ]
    for i, (phase, hook_kwarg, blank) in enumerate(cases):
        sql = ITEMS_OUT_SQL.replace(
            "materialized='table', alias='ITEMS_OUT'",
            f"materialized='table', alias='ITEMS_OUT', {hook_kwarg}={blank!r}")
        repo = build_dbt_workflow(tmp_path / f"case_{i}", replace={"models/items_out.sql": sql})
        dag = read_json(repo.seg(WF, "seg_02", "dag.json"))
        dag["nodes"][0]["config"][phase] = "DELETE FROM X"
        write_json(repo.seg(WF, "seg_02", "dag.json"), dag)

        report = cc.compile_check_dbt(repo, WF)
        assert report["status"] == "ERROR", cases[i]
        assert any(e.startswith("dbt:hooks") and "4" in e for e in report["errors"]), (cases[i], report["errors"])


def test_an_orphan_model_is_refused(tmp_path):
    """I2: a model that builds cleanly but matches no contract output at all -- a leftover or
    invented file that would otherwise deploy an untracked table."""
    repo = build_dbt_workflow(tmp_path, replace={
        "models/extra_model.sql": "{{ config(materialized='table') }}\nselect 1 as X\n"})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    assert report["errors"] == ["dbt:model_orphan: models/extra_model.sql is not a contract output"]


_JINJA_NEGATIVE_CASES = {
    "env_var": "select {{ env_var('X') }} as ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}\n",
    "run_query": "{{ run_query('select 1') }}\nselect ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}\n",
    "statement_block": ("{% call statement('x') %}\nselect 1\n{% endcall %}\n"
                        "select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}\n"),
    "adapter_execute": ('{{ adapter.execute("select 1") }}\n'
                       "select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}\n"),
    "var_call": "select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }} where NOTE = {{ var('x') }}\n",
    "for_loop": ("{% for x in [1, 2, 3] %}\nselect {{ x }}\n{% endfor %}\n"
                "select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}\n"),
}

_JINJA_POSITIVE_CONTROL = (
    "{{ config(materialized='table', alias='ITEMS_OUT', "
    'pre_hook="delete from {{ this }} where 1 = 0") }}\n'
    "{# every allowed construct, in one model #}\n"
    "{% if is_incremental() %}\n"
    "select ID, NOTE, AMOUNT from {{ source('src', 'ITEMS') }}\n"
    "{% else %}\n"
    "select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}\n"
    "{% endif %}\n"
)


def test_model_jinja_is_a_closed_allow_list(tmp_path):
    """M3: inside models/*.sql (hook strings included), the only Jinja allowed is config(...),
    source('src', '<LOGICAL>'), ref('<model>'), this, is_incremental() if/else/endif and comments.
    Every other case here is refused; only the positive control -- one model using every allowed
    construct, including the this-inside-a-hook-string idiom -- has no dbt:model_jinja error."""
    for name, body in _JINJA_NEGATIVE_CASES.items():
        sql = "{{ config(materialized='table', alias='ITEMS_OUT') }}\n" + body
        repo = build_dbt_workflow(tmp_path / name, replace={"models/items_out.sql": sql})
        report = cc.compile_check_dbt(repo, WF)
        assert any(e.startswith("dbt:model_jinja") for e in report["errors"]), (name, report["errors"])

    # a nested, disallowed call inside an otherwise-allowed construct's own arguments
    repo = build_dbt_workflow(tmp_path / "nested_env_var", replace={
        "models/wf0009_seg_01_out.sql": WORK_SQL.replace(
            "{{ source('src', 'ITEMS') }}", "{{ source('src', env_var('X')) }}")})
    report = cc.compile_check_dbt(repo, WF)
    assert any(e.startswith("dbt:model_jinja") for e in report["errors"]), report["errors"]

    repo = build_dbt_workflow(tmp_path / "positive", replace={"models/items_out.sql": _JINJA_POSITIVE_CONTROL})
    report = cc.compile_check_dbt(repo, WF)
    assert not any(e.startswith("dbt:model_jinja") for e in report["errors"]), report["errors"]


def test_a_work_model_column_mismatch_is_refused(tmp_path):
    """M4: the columns check applies to a work-stream model too, not only to a target model."""
    schema_yml = SCHEMA_YML.replace(
        "  - name: wf0009_seg_01_out\n    columns:\n      - name: ID\n      - name: NOTE\n"
        "      - name: AMOUNT\n",
        "  - name: wf0009_seg_01_out\n    columns:\n      - name: NOTE\n      - name: ID\n"
        "      - name: AMOUNT\n")
    repo = build_dbt_workflow(tmp_path, replace={"models/schema.yml": schema_yml})
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    errors = [e for e in report["errors"] if e.startswith("dbt:columns")]
    assert errors and any("wf0009_seg_01_out" in e for e in errors)


def test_a_hook_for_an_unmapped_tool_is_named(tmp_path):
    """M5: a PreSQL/PostSQL node whose tool id is in no intake/mappings.yaml output -- the code
    (scripts/compile_check.py:_dbt_hook_errors) names the tool id and says plainly that no mapped
    output covers it, rather than crashing or naming a model that doesn't exist."""
    repo = build_dbt_workflow(tmp_path)
    dag = read_json(repo.seg(WF, "seg_02", "dag.json"))
    dag["nodes"][0]["tool_id"] = "99"
    dag["nodes"][0]["config"]["pre_sql"] = "DELETE FROM X"
    write_json(repo.seg(WF, "seg_02", "dag.json"), dag)

    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR"
    errors = [e for e in report["errors"] if e.startswith("dbt:hooks")]
    assert errors, report["errors"]
    assert any("99" in e and "no intake/mappings.yaml output maps tool" in e for e in errors)


# --- fix round 2 ---------------------------------------------------------------------------------


_JINJA_TRIMMED_POSITIVE = (
    "{{- config(materialized='table', alias='ITEMS_OUT') -}}\n"
    "{# a comment #}\n"
    "{%- if is_incremental() -%}\n"
    "select ID, NOTE, AMOUNT from {{- source('src', 'ITEMS') -}}\n"
    "{%- else -%}\n"
    "select ID, NOTE, AMOUNT from {{- ref('wf0009_seg_01_out') -}}\n"
    "{%- endif -%}\n"
    "-- one-sided and + forms, never rendered, only scanned for their Jinja shape\n"
    "-- {{- this }}\n"
    "-- {{ this -}}\n"
    "-- {{+ this +}}\n"
    "-- {%+ if is_incremental() +%}\n"
    "-- {% else +%}\n"
    "-- {%+ endif %}\n"
)


def test_model_jinja_allows_whitespace_control_markers(tmp_path):
    """Fix round 2: dbt's whitespace-control markers (a leading/trailing `-` or `+` right inside
    the delimiter, e.g. `{{- this -}}`, `{%- if is_incremental() -%}`) are accepted by real dbt on
    every allow-listed construct, so the tokenizer must strip one leading and one trailing `-`/`+`
    (independently on either side) before matching -- one-sided and both-sided, either mark. A
    disallowed construct wrapped the same way is still refused."""
    repo = build_dbt_workflow(tmp_path / "positive", replace={"models/items_out.sql": _JINJA_TRIMMED_POSITIVE})
    report = cc.compile_check_dbt(repo, WF)
    assert not any(e.startswith("dbt:model_jinja") for e in report["errors"]), report["errors"]

    repo = build_dbt_workflow(tmp_path / "env_var", replace={
        "models/items_out.sql": "{{- config(materialized='table', alias='ITEMS_OUT') -}}\n"
                                "select {{- env_var('X') -}} as ID, NOTE, AMOUNT from "
                                "{{ ref('wf0009_seg_01_out') }}\n"})
    report = cc.compile_check_dbt(repo, WF)
    assert any(e.startswith("dbt:model_jinja") for e in report["errors"]), report["errors"]

    repo = build_dbt_workflow(tmp_path / "for_loop", replace={
        "models/items_out.sql": "{{- config(materialized='table', alias='ITEMS_OUT') -}}\n"
                                "{%- for x in y -%}\nselect {{ x }}\n{%- endfor -%}\n"
                                "select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}\n"})
    report = cc.compile_check_dbt(repo, WF)
    assert any(e.startswith("dbt:model_jinja") for e in report["errors"]), report["errors"]


# --- final fix wave M2: a missing intake/mappings.yaml names its path ------------------------------


def test_a_missing_mappings_yaml_is_a_file_not_found_naming_it(tmp_path, capsys):
    repo = build_dbt_workflow(tmp_path)
    mappings = repo.wf(WF, "intake", "mappings.yaml")
    mappings.unlink()
    try:
        cc.compile_check_dbt(repo, WF)
    except FileNotFoundError as exc:
        assert str(mappings) in str(exc) and "intake/mappings.yaml" in str(exc)
    else:
        raise AssertionError("compile_check_dbt accepted a workflow with no intake/mappings.yaml")
    assert not repo.wf(WF, "dbt", "compile_check.json").exists()
    assert cc.main([WF, "--target", "dbt", "--root", str(tmp_path)]) == 2
    assert "intake" in capsys.readouterr().err
    assert "intake/mappings.yaml" in (cc.compile_check_dbt.__doc__ or "")


def test_a_surface_error_skips_dbt_parse(tmp_path, monkeypatch):
    """C1.1: any surface error and `dbt parse` is not called at all -- not even through run_dbt's
    own gate -- while the file-level checks still report everything they find."""
    repo = build_dbt_workflow(tmp_path, replace={
        "models/items_out.sql": ITEMS_OUT_SQL.replace("alias='ITEMS_OUT'", "alias='ITEMS_OUT', sql_header='select 1'"),
        "models/sources.yml": SOURCES_YML.replace("name: ITEMS", "name: ITEMZ")})

    def no_dbt(*args, **kwargs):
        raise AssertionError("dbt parse ran over a project that fails the surface gate")

    monkeypatch.setattr(cc.dbt_project, "run_dbt", no_dbt)
    report = cc.compile_check_dbt(repo, WF)
    assert report["status"] == "ERROR" and report["models"] == 0
    assert any(e.startswith("dbt:model_jinja: models/items_out.sql") and "sql_header" in e for e in report["errors"])
    assert any(e.startswith("dbt:sources") for e in report["errors"])
    assert not any(e.startswith("dbt:parse") for e in report["errors"])
