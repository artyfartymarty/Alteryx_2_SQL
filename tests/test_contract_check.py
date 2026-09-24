"""contract_check.py: every contract checked by code before any later stage reads it (Task L3, R2).

The analyze gate used to check only that each contract existed, that no target was raised and that
the seams agreed: a contract with an invented stream name, an ordering in an invented shape or no
parity_risks reached golden, translate and validate unnoticed (the fourth live test: a local model
through the real SDK). The checker compares every mechanical field with the scaffold
(`contract_scaffold.py`), checks the JSON shape and type of every field a downstream script reads,
and checks that every judgment field is well-formed. Each problem is one line naming the segment,
the field and what is expected -- the text a model is shown on its retry.

Every committed sample's canned contract passes (the reference); each rule below is then broken on
one of them. Nothing here has run on Snowflake or Alteryx.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import contract_check as cc
import contract_scaffold as cs
import target_check
from lib.io import read_json, read_yaml, write_json, write_yaml
from tests.helpers import prepare_workflow

ROOT = Path(__file__).parents[1]
SAMPLES = ROOT / "samples"
TRANSLATED = sorted(p.parents[1].name for p in SAMPLES.glob("wf_*/canned/segments"))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    repos = {}

    def build(wf_id):
        if wf_id not in repos:
            repo = prepare_workflow(tmp_path_factory.mktemp(wf_id), wf_id)
            target_check.target_check(repo, wf_id, "procedures")
            repos[wf_id] = repo
        return repos[wf_id]

    return build


def _canned(wf_id: str, seg: str) -> dict:
    return json.loads((SAMPLES / wf_id / "canned" / "segments" / seg / "contract.json").read_text(encoding="utf-8"))


def problems(built, wf_id: str, seg: str, mutate) -> list[str]:
    """The checker's problems for the canned contract of `wf_id/seg` after `mutate(contract)`."""
    repo = built(wf_id)
    contract = copy.deepcopy(_canned(wf_id, seg))
    mutate(contract)
    proposal = read_json(repo.wf(wf_id, "segments", "targets.json"))["segments"].get(seg)
    return cc.check_contract(wf_id, seg, contract, cs.derive(repo, wf_id).segments[seg], proposal)


def _set(path: str, value):
    """A mutation setting `a.b[0].c` (or deleting it, for `value is DELETE`)."""
    def mutate(contract):
        *parents, last = _parse(path)
        node = contract
        for key in parents:
            node = node[key]
        if value is DELETE:
            del node[last]
        else:
            node[last] = value
    return mutate


DELETE = object()


def _parse(path: str) -> list:
    keys: list = []
    for part in path.replace("]", "").split("."):
        name, *indexes = part.split("[")
        if name:
            keys.append(name)
        keys.extend(int(i) for i in indexes)
    return keys


# --- the reference passes ------------------------------------------------------------------------

@pytest.mark.parametrize("wf_id", TRANSLATED)
def test_every_canned_contract_passes(wf_id, built):
    assert cc.check(built(wf_id), wf_id) == []


@pytest.mark.parametrize("wf_id", TRANSLATED)
def test_the_cli_exits_0_on_every_canned_workflow(wf_id, built, capsys):
    repo = built(wf_id)
    assert cc.main([wf_id, "--root", str(repo.root)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "every one passes" in captured.out


# --- the live evidence ----------------------------------------------------------------------------

def test_the_live_analyzers_contract_and_what_is_left_after_the_orchestrator_re_applies_the_scaffold(built):
    """`tests/contract_fixtures/live_wf_0001_seg_01.json` is the contract a real model wrote for
    wf_0001/seg_01 in the fourth live test (a local model through the real SDK; the analyzer parked on
    trying to check it itself). Every mechanical mistake is named; once the orchestrator has
    re-applied the scaffold, only the judgment the model still owes is left."""
    repo = built("wf_0001")
    live = read_json(ROOT / "tests" / "contract_fixtures" / "live_wf_0001_seg_01.json")
    scaffold = cs.derive(repo, "wf_0001").segments["seg_01"]
    judgment = [
        "seg_01: ordering.keys is missing; expected a list of column names (may be empty)",
        "seg_01: ordering.alteryx_deterministic is missing; expected true or false",
        "seg_01: ordering.sort is not an ordering field; expected only keys, alteryx_deterministic and "
        "order_dependent_columns",
        'seg_01: normalizations is missing; expected a list of "trim:<COLUMN>" / "upper:<COLUMN>" directives '
        '(may be empty)',
        'seg_01: parity_risks is missing; expected a list of {"tool_id": "<a tool of seg_01>", "class": '
        '"<diff class>", "note": "<why>"} (may be empty)',
    ]
    assert cc.check_contract("wf_0001", "seg_01", live, scaffold, "sql") == [
        'seg_01: workflow is missing; expected "wf_0001"',
        'seg_01: inputs[0] (source tool 1).tool_id is missing; expected "1"',
        'seg_01: inputs[0] (source tool 1).table is "SALES.RAW.ORDERS"; expected no table on a mapped source, '
        'which is read by its logical name',
        'seg_01: outputs[0] (target tool 7).stream is "sales_summary"; expected "6_Output"',
        "seg_01: outputs[0] (target tool 7).table is missing; expected null",
        'seg_01: outputs[0] (target tool 7).write_mode is missing; expected "overwrite"',
        'seg_01: outputs[1] (target tool 8).stream is "excluded_orders"; expected "3_F"',
        "seg_01: outputs[1] (target tool 8).table is missing; expected null",
        'seg_01: outputs[1] (target tool 8).write_mode is missing; expected "overwrite"',
        'seg_01: outputs[1] (target tool 8).columns[0] (CUSTOMER).type is "VARCHAR"; expected "VARCHAR(10)"',
        *judgment,
    ]
    assert cc.check_contract("wf_0001", "seg_01", cs.apply(live, scaffold), scaffold, "sql") == judgment


# --- mechanical agreement with the scaffold --------------------------------------------------------

@pytest.mark.parametrize("path, value, expected", [
    ("workflow", DELETE, 'seg_01: workflow is missing; expected "wf_0001"'),
    ("workflow", "wf_0002", 'seg_01: workflow is "wf_0002"; expected "wf_0001"'),
    ("segment", "seg_1", 'seg_01: segment is "seg_1"; expected "seg_01"'),
    ("inputs[0].tool_id", DELETE, 'seg_01: inputs[0] (source tool 1).tool_id is missing; expected "1"'),
    ("inputs[0].logical", "ORDERS_RAW", 'seg_01: inputs[0] (source tool 1).logical is "ORDERS_RAW"; expected "ORDERS"'),
    ("inputs[0].columns[2].type", "VARCHAR(10)",
     'seg_01: inputs[0] (source tool 1).columns[2] (REGION).type is "VARCHAR(10)"; expected "VARCHAR(20)" (or "VARCHAR")'),
    ("inputs[0].columns[0].type", "FLOAT",
     'seg_01: inputs[0] (source tool 1).columns[0] (ORDER_ID).type is "FLOAT"; expected "NUMBER(38,0)"'),
    ("outputs[0].stream", "sales_summary",
     'seg_01: outputs[0] (target tool 7).stream is "sales_summary"; expected "6_Output"'),
    ("outputs[1].table", "ANALYTICS.CURATED.EXCLUDED_ORDERS",
     'seg_01: outputs[1] (target tool 8).table is "ANALYTICS.CURATED.EXCLUDED_ORDERS"; expected null'),
    ("outputs[1].write_mode", DELETE, 'seg_01: outputs[1] (target tool 8).write_mode is missing; expected "overwrite"'),
    ("outputs[1].write_mode", "append", 'seg_01: outputs[1] (target tool 8).write_mode is "append"; expected "overwrite"'),
    ("outputs[1].logical", "EXCLUDED", 'seg_01: outputs[1] (target tool 8).logical is "EXCLUDED"; expected "EXCLUDED_ORDERS"'),
])
def test_each_mechanical_field_must_agree_with_the_scaffold(built, path, value, expected):
    found = problems(built, "wf_0001", "seg_01", _set(path, value))
    assert expected in found, found


def test_the_type_maps_unsized_varchar_is_accepted_for_a_variable_string(built):
    assert problems(built, "wf_0001", "seg_01", _set("inputs[0].columns[2].type", "VARCHAR")) == []


def test_a_fixed_width_string_must_keep_its_size(built):
    found = problems(built, "wf_0001", "seg_01", _set("outputs[1].columns[0].type", "VARCHAR"))
    assert 'seg_01: outputs[1] (target tool 8).columns[0] (CUSTOMER).type is "VARCHAR"; expected "VARCHAR(10)"' in found


def test_a_mapped_source_carries_no_table(built):
    found = problems(built, "wf_0001", "seg_01", _set("inputs[0].table", "SALES.RAW.ORDERS"))
    assert found == ['seg_01: inputs[0] (source tool 1).table is "SALES.RAW.ORDERS"; expected no table on a '
                     'mapped source, which is read by its logical name']


def test_column_names_must_be_the_dags_in_order(built):
    def swap(contract):
        columns = contract["inputs"][0]["columns"]
        columns[0], columns[1] = columns[1], columns[0]
    found = problems(built, "wf_0001", "seg_01", swap)
    assert found[0] == ("seg_01: inputs[0] (source tool 1).columns are named [CUSTOMER, ORDER_ID, REGION, AMOUNT_TXT, "
                        "QTY, ORDER_DATE, STATUS]; expected [ORDER_ID, CUSTOMER, REGION, AMOUNT_TXT, QTY, ORDER_DATE, "
                        "STATUS]"), found


def test_an_invented_output_and_a_missing_one_are_both_named(built):
    def replace(contract):
        contract["outputs"][1] = {"stream": "3_Z", "kind": "work", "table": "MIG_WORK.X", "logical": None,
                                  "columns": [], "keys": []}
    found = problems(built, "wf_0001", "seg_01", replace)
    assert "seg_01: outputs has no entry for target tool 8; expected one (stream 3_F, logical EXCLUDED_ORDERS)" in found
    assert ("seg_01: outputs[1] (work stream 3_Z) is not an output the DAG shows; expected only: target tool 7, "
            "target tool 8") in found


def test_entries_must_be_in_the_scaffolds_order(built):
    def reverse(contract):
        contract["outputs"].reverse()
        contract["output"] = copy.deepcopy(contract["outputs"][0])
    found = problems(built, "wf_0001", "seg_01", reverse)
    assert found == ["seg_01: outputs are in the order target tool 8, target tool 7; expected target tool 7, target tool 8"]


def test_output_must_be_a_copy_of_outputs_0(built):
    found = problems(built, "wf_0001", "seg_01", _set("output", {"stream": "sales_summary"}))
    assert found == ["seg_01: output differs from outputs[0]; expected an exact copy of outputs[0]"]


def test_an_upstream_inputs_from_stream_and_table_are_mechanical(built):
    found = problems(built, "wf_0002", "seg_03", _set("inputs[1].table", "MIG_WORK.WF0002_SEG_02_OUT_4_OUTPUT"))
    assert found == ['seg_03: inputs[1] (stream 4_Output from seg_02).table is "MIG_WORK.WF0002_SEG_02_OUT_4_OUTPUT"; '
                     'expected "MIG_WORK.WF0002_SEG_02_OUT"']
    found = problems(built, "wf_0002", "seg_03", _set("inputs[0].from", "seg_02"))
    assert 'seg_03: inputs[0] (stream 2_Output from seg_01).from is "seg_02"; expected "seg_01"' in found


def test_a_work_outputs_table_is_mechanical(built):
    def rename(contract):
        contract["outputs"][0]["table"] = "MIG_WORK.CUSTOMERS_CLEAN"
        contract["output"] = copy.deepcopy(contract["outputs"][0])
    found = problems(built, "wf_0002", "seg_01", rename)
    assert found == ['seg_01: outputs[0] (work stream 2_Output).table is "MIG_WORK.CUSTOMERS_CLEAN"; '
                     'expected "MIG_WORK.WF0002_SEG_01_OUT"']


def test_target_only_columns_are_accepted_only_where_the_target_keeps_its_rows(built):
    assert problems(built, "wf_0003", "seg_02", lambda c: None) == [], "the canned LOADED_FLAG is accepted"

    def add(contract):
        for entry in (contract["outputs"][1], ):
            entry["columns"].append({"name": "LOADED_AT", "type": "TIMESTAMP_NTZ", "nullable": True})
    found = problems(built, "wf_0001", "seg_01", add)
    assert found[0].startswith("seg_01: outputs[1] (target tool 8).columns are named [CUSTOMER, "), found
    assert found[0].endswith("ORDER_DATE]"), found

    def untyped(contract):
        contract["outputs"][0]["columns"][-1] = {"name": "LOADED_FLAG", "nullable": True}
        contract["output"] = copy.deepcopy(contract["outputs"][0])
    found = problems(built, "wf_0003", "seg_02", untyped)
    assert found == ['seg_02: outputs[0] (target tool 10).columns[6] (LOADED_FLAG).type is missing; expected a '
                     'Snowflake column type such as "VARCHAR(10)" or "NUMBER(38,0)"']


# --- the shape of the analyzer-owned fields a script reads ----------------------------------------------

@pytest.mark.parametrize("path, value, expected", [
    ("inputs[0].columns[1].nullable", "yes",
     'seg_01: inputs[0] (source tool 1).columns[1] (CUSTOMER).nullable is "yes"; expected true or false'),
    ("inputs[0].columns[1].nullable", DELETE,
     "seg_01: inputs[0] (source tool 1).columns[1] (CUSTOMER).nullable is missing; expected true or false"),
    ("outputs[0].keys", "REGION",
     'seg_01: outputs[0] (target tool 7).keys is "REGION"; expected a list of its column names (may be empty)'),
    ("inputs[0].keys", DELETE,
     "seg_01: inputs[0] (source tool 1).keys is missing; expected a list of its column names (may be empty)"),
    ("inputs[0].expected_rows", {"min": 5, "max": 1},
     'seg_01: inputs[0] (source tool 1).expected_rows is {"min": 5, "max": 1}; expected {"min": <integer >= 0>, '
     '"max": <integer >= min>}'),
    ("inputs[0].large", "no", 'seg_01: inputs[0] (source tool 1).large is "no"; expected true or false'),
])
def test_each_analyzer_owned_field_has_the_shape_downstream_scripts_read(built, path, value, expected):
    found = problems(built, "wf_0001", "seg_01", _set(path, value))
    assert expected in found, found


def test_keys_must_be_columns_of_their_own_entry(built):
    def keys(contract):
        contract["outputs"][0]["keys"] = ["REGION", "NOPE"]
        contract["output"] = copy.deepcopy(contract["outputs"][0])
    found = problems(built, "wf_0001", "seg_01", keys)
    assert found == ["seg_01: outputs[0] (target tool 7).keys names NOPE, which is not one of its columns; expected a "
                     "subset of REGION, SIZE_BAND, TOTAL_NET, ORDERS, PRICED_ORDERS, LAST_ORDER"]


def test_expected_rows_and_large_are_optional(built):
    def drop(contract):
        del contract["inputs"][0]["expected_rows"]
        del contract["inputs"][0]["large"]
    assert problems(built, "wf_0001", "seg_01", drop) == []


# --- target ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (DELETE, 'seg_01: target is missing; expected "sql" (segments/targets.json) or a lower target'),
    ("dbt", 'seg_01: target is "dbt"; expected one of "sql", "snowpark", "manual"'),
])
def test_the_target_must_be_present_and_in_the_vocabulary(built, value, expected):
    assert problems(built, "wf_0001", "seg_01", _set("target", value)) == [expected]


def test_a_target_may_only_be_lowered(built):
    assert problems(built, "wf_0006", "seg_01", _set("target", "manual")) == []
    found = problems(built, "wf_0006", "seg_02", _set("target", "sql"))
    assert found == ['seg_02: target is "sql", above the proposal "snowpark" in segments/targets.json; expected '
                     '"snowpark" or lower (a target may only be lowered: sql -> snowpark -> manual)']


# --- the judgment fields are well-formed -----------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (DELETE, 'seg_01: row_relation is missing; expected one of "1:1", "filter", "aggregate", "expand"'),
    ("n:1", 'seg_01: row_relation is "n:1"; expected one of "1:1", "filter", "aggregate", "expand"'),
])
def test_row_relation(built, value, expected):
    assert problems(built, "wf_0001", "seg_01", _set("row_relation", value)) == [expected]


ORDERING_SHAPE = ('{"keys": [<column>...], "alteryx_deterministic": true|false, '
                  '"order_dependent_columns": [<output column>...]}')


def test_the_live_analyzers_invented_ordering_shape_is_refused(built):
    live = {"order_dependent_columns": [], "sort": [{"column": "TOTAL_NET", "direction": "desc"}]}
    found = problems(built, "wf_0001", "seg_01", _set("ordering", live))
    assert found == [
        "seg_01: ordering.keys is missing; expected a list of column names (may be empty)",
        "seg_01: ordering.alteryx_deterministic is missing; expected true or false",
        "seg_01: ordering.sort is not an ordering field; expected only keys, alteryx_deterministic and "
        "order_dependent_columns",
    ]


@pytest.mark.parametrize("path, value, expected", [
    ("ordering", DELETE, f"seg_01: ordering is missing; expected {ORDERING_SHAPE}"),
    ("ordering", [], f"seg_01: ordering is []; expected {ORDERING_SHAPE}"),
    ("ordering.keys", ["TOTAL_NET", "BOGUS"],
     "seg_01: ordering.keys names BOGUS, which is not a column of seg_01; expected a column of its inputs, its "
     "tools' anchors or its outputs"),
    ("ordering.alteryx_deterministic", "yes", 'seg_01: ordering.alteryx_deterministic is "yes"; expected true or false'),
    ("ordering.order_dependent_columns", ["STATUS"],
     "seg_01: ordering.order_dependent_columns names STATUS, which is not an output column; expected one of "
     "REGION, SIZE_BAND, TOTAL_NET, ORDERS, PRICED_ORDERS, LAST_ORDER, CUSTOMER, ORDER_STATUS, ORDER_ID, "
     "AMOUNT_TXT, QTY, ORDER_DATE"),
])
def test_ordering(built, path, value, expected):
    found = problems(built, "wf_0001", "seg_01", _set(path, value))
    assert expected in found, found


def test_ordering_keys_may_name_an_input_column_the_output_drops(built):
    """wf_0003/seg_02 orders by POSTED_DT and ENTRY_ID, which its Summarize drops: the canned contract
    is right, so `ordering.keys` is checked against every column the segment carries."""
    assert problems(built, "wf_0003", "seg_02", lambda c: None) == []
    assert _canned("wf_0003", "seg_02")["ordering"]["keys"] == ["ACCT", "POSTED_DT", "ENTRY_ID"]


TOLERANCE_SHAPE = '{"float_abs": <number >= 0>, "float_rel": <number >= 0>} (either or both)'


@pytest.mark.parametrize("value, expected", [
    ([], 'seg_01: tolerances is []; expected an object keyed by output column (may be empty: {})'),
    ({"BOGUS": {"float_abs": 0.01}},
     "seg_01: tolerances.BOGUS names no output column; expected one of REGION, SIZE_BAND, TOTAL_NET, ORDERS, "
     "PRICED_ORDERS, LAST_ORDER, CUSTOMER, ORDER_STATUS, ORDER_ID, AMOUNT_TXT, QTY, ORDER_DATE"),
    ({"TOTAL_NET": 0.01}, f"seg_01: tolerances.TOTAL_NET is 0.01; expected {TOLERANCE_SHAPE}"),
    ({"TOTAL_NET": {"abs": 0.01}}, f'seg_01: tolerances.TOTAL_NET is {{"abs": 0.01}}; expected {TOLERANCE_SHAPE}'),
    ({"TOTAL_NET": {"float_abs": -1}}, f'seg_01: tolerances.TOTAL_NET is {{"float_abs": -1}}; expected {TOLERANCE_SHAPE}'),
    ({"TOTAL_NET": {"float_rel": True}}, f'seg_01: tolerances.TOTAL_NET is {{"float_rel": true}}; expected {TOLERANCE_SHAPE}'),
])
def test_tolerances(built, value, expected):
    assert problems(built, "wf_0001", "seg_01", _set("tolerances", value)) == [expected]


def test_a_well_formed_tolerance_passes(built):
    assert problems(built, "wf_0001", "seg_01", _set("tolerances", {"total_net": {"float_abs": 0.01, "float_rel": 0}})) == []


@pytest.mark.parametrize("value, expected", [
    (DELETE, 'seg_01: normalizations is missing; expected a list of "trim:<COLUMN>" / "upper:<COLUMN>" directives '
             '(may be empty)'),
    ("trim:REGION", 'seg_01: normalizations is "trim:REGION"; expected a list of "trim:<COLUMN>" / "upper:<COLUMN>" '
                    'directives (may be empty)'),
    (["strip:REGION"], 'seg_01: normalizations[0] is "strip:REGION"; expected "trim:<COLUMN>" or "upper:<COLUMN>"'),
    (["trim:"], 'seg_01: normalizations[0] is "trim:"; expected "trim:<COLUMN>" or "upper:<COLUMN>"'),
    (["trim:BOGUS"], "seg_01: normalizations[0] names BOGUS, which is not an output column; expected one of REGION, "
                     "SIZE_BAND, TOTAL_NET, ORDERS, PRICED_ORDERS, LAST_ORDER, CUSTOMER, ORDER_STATUS, ORDER_ID, "
                     "AMOUNT_TXT, QTY, ORDER_DATE"),
])
def test_normalizations(built, value, expected):
    assert problems(built, "wf_0001", "seg_01", _set("normalizations", value)) == [expected]


def test_well_formed_normalizations_pass(built):
    assert problems(built, "wf_0001", "seg_01", _set("normalizations", ["trim:region", "UPPER:CUSTOMER"])) == []


RISK_SHAPE = '{"tool_id": "<a tool of seg_01>", "class": "<diff class>", "note": "<why>"}'


@pytest.mark.parametrize("value, expected", [
    (DELETE, f"seg_01: parity_risks is missing; expected a list of {RISK_SHAPE} (may be empty)"),
    ({}, f"seg_01: parity_risks is {{}}; expected a list of {RISK_SHAPE} (may be empty)"),
    (["x"], f'seg_01: parity_risks[0] is "x"; expected {RISK_SHAPE}'),
    ([{"tool_id": "99", "class": "LOGIC", "note": "n"}],
     'seg_01: parity_risks[0].tool_id is "99"; expected a tool of seg_01: 1, 2, 3, 4, 5, 6, 7, 8'),
    ([{"tool_id": 3, "class": "LOGIC", "note": "n"}],
     "seg_01: parity_risks[0].tool_id is 3; expected a tool of seg_01: 1, 2, 3, 4, 5, 6, 7, 8"),
    ([{"tool_id": "3", "class": "ORDER", "note": "n"}],
     'seg_01: parity_risks[0].class is "ORDER"; expected one of ROUNDING, ORDERING, NULL_SEMANTICS, TRUNCATION, '
     'TYPE, LOGIC, GOLDEN_DATA, UNKNOWN'),
    ([{"tool_id": "3", "class": "LOGIC"}],
     "seg_01: parity_risks[0].note is missing; expected a non-empty sentence"),
    ([{"tool_id": "3", "class": "LOGIC", "note": "  "}],
     'seg_01: parity_risks[0].note is "  "; expected a non-empty sentence'),
])
def test_parity_risks(built, value, expected):
    assert problems(built, "wf_0001", "seg_01", _set("parity_risks", value)) == [expected]


def test_a_risk_may_name_a_tool_inside_a_macro_of_the_segment(built):
    risk = [{"tool_id": "2/3", "class": "LOGIC", "note": "inside the macro"}]
    assert problems(built, "wf_0004", "seg_02", _set("parity_risks", risk)) == []


def test_every_message_names_the_segment_and_what_is_expected(built):
    def wreck(contract):
        contract.pop("workflow")
        contract["outputs"][0]["stream"] = "x"
        contract["ordering"] = {"sort": []}
        contract["tolerances"] = {"X": 1}
        contract["parity_risks"] = [{}]
        contract["normalizations"] = ["x"]
        contract["row_relation"] = "?"
    found = problems(built, "wf_0001", "seg_01", wreck)
    assert len(found) >= 8
    for line in found:
        assert line.startswith("seg_01: ") and "; expected " in line and "\n" not in line, line


# --- the CLI ----------------------------------------------------------------------------------------------

def _copy(built, tmp_path, wf_id):
    import shutil
    root = tmp_path / "root"
    shutil.copytree(built(wf_id).root, root)
    return root


def test_cli_prints_one_line_per_problem_and_exits_1(built, tmp_path, capsys):
    root = _copy(built, tmp_path, "wf_0001")
    path = root / "workflows" / "wf_0001" / "segments" / "seg_01" / "contract.json"
    contract = read_json(path)
    contract["row_relation"] = "?"
    del contract["workflow"]
    write_json(path, contract)
    assert cc.main(["wf_0001", "--root", str(root)]) == 1
    captured = capsys.readouterr()
    assert captured.err.splitlines() == [
        'seg_01: workflow is missing; expected "wf_0001"',
        'seg_01: row_relation is "?"; expected one of "1:1", "filter", "aggregate", "expand"',
    ]
    assert "2 problems" in captured.out


def test_cli_names_a_missing_or_unreadable_contract(built, tmp_path, capsys):
    root = _copy(built, tmp_path, "wf_0002")
    (root / "workflows" / "wf_0002" / "segments" / "seg_01" / "contract.json").unlink()
    (root / "workflows" / "wf_0002" / "segments" / "seg_02" / "contract.json").write_text("{", encoding="utf-8")
    assert cc.main(["wf_0002", "--root", str(root)]) == 1
    err = capsys.readouterr().err.splitlines()
    assert err[0] == "seg_01: contract.json is missing; expected the analyzer's contract for seg_01"
    assert err[1].startswith("seg_02: contract.json is not a JSON object"), err


def test_cli_segments_restricts_the_check(built, tmp_path, capsys):
    root = _copy(built, tmp_path, "wf_0002")
    (root / "workflows" / "wf_0002" / "segments" / "seg_01" / "contract.json").unlink()
    assert cc.main(["wf_0002", "--segments", "seg_02,seg_03", "--root", str(root)]) == 0


@pytest.mark.parametrize("argv", [["wf_0001", "--segments", "seg_09"], ["wf_0404"]])
def test_cli_usage_errors_exit_2(built, argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cc.main([*argv, "--root", str(built("wf_0001").root)])
    assert exc.value.code == 2


# --- fix round 1: the derivation gaps, every column type, and the remaining rules -------------------

from tests.test_contract_scaffold import WF as SYNTHETIC, synthetic  # noqa: E402  (a shared fixture builder)

TYPE_EXPECTED = 'a Snowflake column type such as "VARCHAR(10)" or "NUMBER(38,0)"'


def _synthetic_problems(tmp_path, seg: str, mutate=lambda c: None, repo_edit=lambda repo: None) -> list[str]:
    """The checker's problems for the synthetic workflow's pre-filled `seg` contract, judged, with the
    SpatialObj column it cannot derive declared `GEOGRAPHY` -- after `mutate(contract)`."""
    repo = synthetic(tmp_path)
    repo_edit(repo)
    derivation = cs.derive(repo, SYNTHETIC)
    contract = copy.deepcopy(derivation.segments[seg].contract)
    for entry in contract["inputs"] + contract["outputs"]:
        for column in entry.get("columns") or []:
            column.setdefault("type", "GEOGRAPHY")
    contract["output"] = copy.deepcopy(contract["outputs"][0])
    contract.update(row_relation="expand", tolerances={}, parity_risks=[],
                    ordering={"keys": [], "alteryx_deterministic": True, "order_dependent_columns": []})
    mutate(contract)
    proposal = read_json(repo.wf(SYNTHETIC, "segments", "targets.json"))["segments"].get(seg)
    return cc.check_contract(SYNTHETIC, seg, contract, derivation.segments[seg], proposal)


def test_the_synthetic_contract_with_its_gap_declared_passes(tmp_path):
    assert _synthetic_problems(tmp_path, "seg_02") == []


@pytest.mark.parametrize("value", ["GEOGRAPHY", "NUMBER(38, 0)", "TIMESTAMP_NTZ(9)", "DOUBLE PRECISION", "varchar(4)"])
def test_a_type_the_scaffold_could_not_derive_may_be_any_well_formed_type(tmp_path, value):
    assert _synthetic_problems(tmp_path, "seg_02", _set("inputs[0].columns[1].type", value)) == []


@pytest.mark.parametrize("value, shown", [
    (DELETE, None),
    ("", '""'),
    ("   ", '"   "'),
    ("GEOGRAPHY); DROP TABLE X; --", '"GEOGRAPHY); DROP TABLE X; --"'),
    (["GEOGRAPHY"], '["GEOGRAPHY"]'),
    ({"name": "GEOGRAPHY"}, '{"name": "GEOGRAPHY"}'),
    (7, "7"),
    ("NUMBER(38,", '"NUMBER(38,"'),
])
def test_a_type_the_scaffold_could_not_derive_is_still_checked(tmp_path, value, shown):
    """I2: a column whose type the type map cannot derive (tool 4's SpatialObj) is shape-checked, never
    passed unseen: compile_check.py splices it into DDL."""
    found = _synthetic_problems(tmp_path, "seg_02", _set("inputs[0].columns[1].type", value))
    what = "is missing" if shown is None else f"is {shown}"
    assert found == [f"seg_02: inputs[0] (source tool 4).columns[1] (SHAPE).type {what}; expected {TYPE_EXPECTED}"]


def test_a_logical_name_intake_did_not_map_is_the_analyzers_to_declare(tmp_path):
    def unmap(repo):
        mappings = read_yaml(repo.wf(SYNTHETIC, "intake", "mappings.yaml"))
        del mappings["sources"]["b.yxdb"]
        write_yaml(repo.wf(SYNTHETIC, "intake", "mappings.yaml"), mappings)
    assert _synthetic_problems(tmp_path, "seg_02", repo_edit=unmap) == [
        "seg_02: inputs[0] (source tool 4).logical is missing; expected a non-empty string the analyzer declares "
        "(the pipeline could not derive it)"]
    assert _synthetic_problems(tmp_path, "seg_02", _set("inputs[0].logical", "B"), repo_edit=unmap) == []


def test_columns_of_an_anchor_with_no_meta_are_the_analyzers_to_declare(tmp_path):
    def no_meta(repo):
        dag = read_json(repo.wf(SYNTHETIC, "parsed", "dag.json"))
        next(n for n in dag["nodes"] if n["tool_id"] == "4")["meta"] = {}
        write_json(repo.wf(SYNTHETIC, "parsed", "dag.json"), dag)
    assert _synthetic_problems(tmp_path, "seg_02", repo_edit=no_meta) == [
        "seg_02: inputs[0] (source tool 4).columns is missing; expected a list of {name, type, nullable} objects"]
    declared = [{"name": "ID", "type": "NUMBER(38,0)", "nullable": True}, {"name": "SHAPE", "type": [], "nullable": True}]
    assert _synthetic_problems(tmp_path, "seg_02", _set("inputs[0].columns", declared), repo_edit=no_meta) == [
        f"seg_02: inputs[0] (source tool 4).columns[1] (SHAPE).type is []; expected {TYPE_EXPECTED}"]


def test_an_empty_outputs_list_is_one_problem(built):
    """N3: one line, not one per missing entry plus one for the empty list."""
    def empty(contract):
        contract["outputs"] = []
        del contract["output"]
    assert problems(built, "wf_0001", "seg_01", empty) == [
        "seg_01: outputs is empty; expected 2 entries: target tool 7, target tool 8"]


def test_a_tolerance_too_large_for_a_float_is_refused_not_a_crash(built):
    """M5: compare.py reads a tolerance with float(), which overflows on a 400-digit integer."""
    found = problems(built, "wf_0001", "seg_01", _set("tolerances", {"TOTAL_NET": {"float_abs": 10 ** 400}}))
    assert len(found) == 1 and found[0].startswith("seg_01: tolerances.TOTAL_NET is {\"float_abs\": 1000"), found
    assert found[0].endswith(f"expected {TOLERANCE_SHAPE}")


@pytest.mark.parametrize("risk, expected", [
    ({"tool_id": "2/3", "class": "LOGIC", "note": "n"}, None),
    ({"tool_id": "2/99", "class": "LOGIC", "note": "n"},
     'seg_02: parity_risks[0].tool_id is "2/99"; expected a tool of seg_02: 2, 2/1, 2/2, 2/3, 2/4, 2/5, 2/10'),
    ({"tool_id": "2/", "class": "LOGIC", "note": "n"},
     'seg_02: parity_risks[0].tool_id is "2/"; expected a tool of seg_02: 2, 2/1, 2/2, 2/3, 2/4, 2/5, 2/10'),
    ({"tool_id": "2", "class": "LOGIC", "note": "n", "severity": "high"},
     "seg_02: parity_risks[0].severity is not a parity-risk field; expected only tool_id, class and note"),
])
def test_a_risk_names_a_tool_of_the_segment_or_of_one_of_its_macros_and_nothing_else(built, risk, expected):
    """M6: `<macro>/<tool>` only for a tool the macro's own sub-DAG has."""
    found = problems(built, "wf_0004", "seg_02", _set("parity_risks", [risk]))
    assert found == ([] if expected is None else [expected])


def test_a_macro_path_under_a_tool_that_is_no_macro_is_refused(built):
    found = problems(built, "wf_0001", "seg_01", _set("parity_risks", [{"tool_id": "3/1", "class": "LOGIC", "note": "n"}]))
    assert found == ['seg_01: parity_risks[0].tool_id is "3/1"; expected a tool of seg_01: 1, 2, 3, 4, 5, 6, 7, 8']


def test_cli_an_empty_segments_list_is_a_usage_error(built, capsys):
    """N4."""
    with pytest.raises(SystemExit) as exc:
        cc.main(["wf_0001", "--segments", "", "--root", str(built("wf_0001").root)])
    assert exc.value.code == 2
    assert "names no segment" in capsys.readouterr().err
