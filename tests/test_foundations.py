import argparse
import json
import math
import os
from decimal import Decimal
from pathlib import Path

import pytest

from lib import io, typed_csv, vocab
from lib.paths import Repo, add_root_arg, wf_token, seg_token

def test_repo_paths(tmp_path):
    repo = Repo(tmp_path)
    assert repo.wf("wf_0001", "parsed", "dag.json") == tmp_path.resolve() / "workflows/wf_0001/parsed/dag.json"
    assert repo.seg("wf_0001", "seg_02", "proc.sql").name == "proc.sql"
    assert wf_token("wf_0001") == "WF0001" and seg_token("seg_02") == "SEG_02"

def test_json_is_lf_with_trailing_newline(tmp_path):
    p = tmp_path / "a" / "b.json"
    io.write_json(p, {"k": [1, 2]})
    raw = p.read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw and json.loads(raw) == {"k": [1, 2]}

def test_manifest_default_and_timestamp(tmp_path):
    repo = Repo(tmp_path)
    m = io.load_manifest(repo, "wf_0009")
    assert m == {"id": "wf_0009", "status": {}, "metrics": {}}
    io.save_manifest(repo, m)
    assert io.load_manifest(repo, "wf_0009")["updated_at"].endswith("Z")

def test_typed_csv_round_trip_distinguishes_null_from_empty(tmp_path):
    table = {"fields": [{"name": "S", "type": "V_String", "size": 20, "scale": None},
                        {"name": "N", "type": "Int32", "size": 4, "scale": None},
                        {"name": "D", "type": "FixedDecimal", "size": 19, "scale": 2},
                        {"name": "B", "type": "Bool", "size": 1, "scale": None}],
             "rows": [["a,b", 1, Decimal("10.50"), True], ["", None, None, False], [None, -3, Decimal("0.00"), None]]}
    path = tmp_path / "t.csv"
    typed_csv.write_table(path, table)
    assert (tmp_path / "t.schema.json").exists()
    assert "\\N" in path.read_text(encoding="utf-8")
    assert typed_csv.read_table(path) == table

def test_vocab_is_closed():
    assert "WAITING_FOR_ANSWERS" in vocab.STATUSES and "PASS_WITH_ACCEPTED_DIFF" in vocab.VERDICTS
    assert vocab.DIFF_CLASSES == ("ROUNDING", "ORDERING", "NULL_SEMANTICS", "TRUNCATION", "TYPE", "LOGIC", "GOLDEN_DATA", "UNKNOWN")


# --- additional tests: behaviour the brief describes in prose but does not test ---

def test_statuses_and_verdicts_match_global_constraints_exactly():
    # plan 00-index.md Global Constraints: the closed vocabulary, verbatim.
    assert vocab.STATUSES == frozenset({
        "PENDING", "DONE", "PARSED", "RECOVERED", "QUARANTINED", "READY",
        "WAITING_FOR_ANSWERS", "BLOCKED", "NEEDS_HUMAN", "MANUAL", "VALIDATED", "OPEN",
    })
    assert vocab.VERDICTS == frozenset({"PASS", "PASS_WITH_ACCEPTED_DIFF", "FAIL", "BLOCK"})


def test_vocab_other_closed_sets():
    assert vocab.GOLDEN_SETS == ("normal", "period_end", "empty", "edge")
    assert vocab.NON_DATA_TYPES == frozenset({"container", "comment", "interface", "action"})
    assert vocab.ORDER_DEPENDENT_TYPES == frozenset({"sample", "record_id", "unique", "multi_row_formula"})
    assert vocab.T3_TYPES == frozenset({"run_command"})


def test_repo_global_mappings_path(tmp_path):
    repo = Repo(tmp_path)
    assert repo.global_mappings == tmp_path.resolve() / "mappings" / "global.yaml"


def test_repo_root_is_resolved_regardless_of_trailing_dot(tmp_path):
    # Repo(root) resolves the path (absolute, no "." components) so downstream comparisons are stable.
    repo = Repo(str(tmp_path) + os.sep + ".")
    assert repo.root == tmp_path.resolve()


def test_add_root_arg_defaults_to_current_directory():
    parser = argparse.ArgumentParser()
    add_root_arg(parser)
    args = parser.parse_args([])
    assert args.root == "."
    args = parser.parse_args(["--root", "/some/path"])
    assert args.root == "/some/path"


def test_save_manifest_writes_to_repo_wf_manifest_json(tmp_path):
    repo = Repo(tmp_path)
    io.save_manifest(repo, {"id": "wf_0042", "status": {"parse": "PARSED"}, "metrics": {}})
    on_disk = io.read_json(repo.wf("wf_0042", "manifest.json"))
    assert on_disk["id"] == "wf_0042"
    assert on_disk["status"] == {"parse": "PARSED"}
    assert on_disk["updated_at"].endswith("Z")


def test_yaml_round_trip_preserves_key_order_and_unicode(tmp_path):
    p = tmp_path / "m.yaml"
    obj = {"zeta": 1, "alpha": "café", "middle": {"b": 2, "a": 1}}
    io.write_yaml(p, obj)
    text = p.read_text(encoding="utf-8")
    # sort_keys=False: insertion order preserved, not alphabetized.
    assert text.index("zeta") < text.index("alpha")
    # allow_unicode=True: non-ASCII written literally, not \uXXXX-escaped.
    assert "café" in text
    assert b"\r" not in p.read_bytes()
    assert io.read_yaml(p) == obj


def test_typed_csv_rfc4180_quotes_only_fields_that_need_it(tmp_path):
    table = {"fields": [{"name": "S", "type": "V_String", "size": 20, "scale": None}],
             "rows": [["plain"], ["has,comma"], ['has"quote']]}
    path = tmp_path / "q.csv"
    typed_csv.write_table(path, table)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "plain"
    assert lines[2] == '"has,comma"'
    assert lines[3] == '"has""quote"'
    assert typed_csv.read_table(path) == table


def test_typed_csv_uses_lf_line_endings(tmp_path):
    table = {"fields": [{"name": "N", "type": "Int32", "size": 4, "scale": None}], "rows": [[1], [2]]}
    path = tmp_path / "lf.csv"
    typed_csv.write_table(path, table)
    raw = path.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")


def test_parse_and_format_value_by_alteryx_type():
    assert typed_csv.parse_value("\\N", "V_String") is None
    assert typed_csv.parse_value("42", "Int64") == 42
    assert typed_csv.parse_value("3.5", "Double") == 3.5
    assert typed_csv.parse_value("10.50", "FixedDecimal") == Decimal("10.50")
    assert typed_csv.parse_value("true", "Bool") is True
    assert typed_csv.parse_value("false", "Bool") is False
    assert typed_csv.parse_value("2024-01-31", "Date") == "2024-01-31"
    assert typed_csv.parse_value("2024-01-31 12:00:00", "DateTime") == "2024-01-31 12:00:00"

    assert typed_csv.format_value(None, "Int32") == "\\N"
    assert typed_csv.format_value(True, "Bool") == "true"
    assert typed_csv.format_value(False, "Bool") == "false"
    assert typed_csv.format_value(Decimal("0.00"), "FixedDecimal") == "0.00"
    assert typed_csv.format_value("2024-01-31", "Date") == "2024-01-31"


def test_global_yaml_matches_program_spec_plus_task_additions():
    root = Path(__file__).resolve().parents[1]
    obj = io.read_yaml(root / "mappings" / "global.yaml")
    assert obj["program"]["raw_schema"] == "RAW"
    assert obj["tolerances"]["rounding"] == {"abs": 0.01}
    assert obj["segmentation"] == {"max_prompt_chars": 60000}
    assert obj["sources"] == {}
    assert obj["outputs"] == {}


# --- fix round 1: Float/Double must be written in plain decimal notation (contract C1) ---

def test_format_float_never_uses_scientific_notation():
    for value in (1e17, 1e-7, 0.1, 250.005, 1 / 3, -0.0, 0.0,
                  1.7976931348623157e308, 5e-324, -1e-7):
        text = typed_csv.format_value(value, "Double")
        assert "e" not in text and "E" not in text, f"{value!r} -> {text!r} used scientific notation"


def test_format_float_round_trips_exactly_including_negative_zero():
    for value in (1e17, 1e-7, 0.1, 250.005, 1 / 3, -0.0, 0.0,
                  1.7976931348623157e308, 5e-324, -1e-7, 100.0):
        text = typed_csv.format_value(value, "Double")
        back = typed_csv.parse_value(text, "Double")
        assert back == value
        assert math.copysign(1.0, back) == math.copysign(1.0, value), (
            f"sign lost: {value!r} -> {text!r} -> {back!r}"
        )


def test_format_float_keeps_shortest_digits_no_float_noise():
    assert typed_csv.format_value(0.1, "Double") == "0.1"
    assert typed_csv.format_value(1 / 3, "Float") == "0.3333333333333333"


def test_format_float_integral_values_keep_fractional_part():
    assert typed_csv.format_value(100.0, "Double") == "100.0"
    assert typed_csv.format_value(1e17, "Double") == "100000000000000000.0"


def test_format_float_rejects_non_finite_values():
    for bad in (float("inf"), float("-inf"), float("nan")):
        try:
            typed_csv.format_value(bad, "Double")
        except ValueError as e:
            assert repr(bad) in str(e) or str(bad) in str(e)
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


def test_typed_csv_float_column_round_trip_plain_decimal(tmp_path):
    table = {"fields": [{"name": "F", "type": "Double", "size": 8, "scale": None}],
             "rows": [[1e17], [1e-7], [0.1], [-0.0], [None]]}
    path = tmp_path / "f.csv"
    typed_csv.write_table(path, table)
    text = path.read_text(encoding="utf-8")
    assert "e" not in text.lower()
    assert typed_csv.read_table(path) == table


# --- fix round 2 (task 9 re-review): Repo.wf/Repo.seg validate wf_id/seg outright -------------
#
# A traversal id like ".." would collapse `Repo.wf("..")` back to the repo root itself, defeating
# any containment check built on top of it (dev/build_samples.py's `_reset_dir` guard). These ids
# are rejected at the source, for every script that builds a path through Repo -- not only
# build_samples.py's.

BAD_IDS = ["..", ".", "", "a/b", "a\\b", "C:\\x", "/abs", "wf_0001/../wf_0002",
          "wf_0001.", "wf_0001 ", " wf_0001", "wf 0001"]
GOOD_IDS = ["wf_0001", "seg_01", "WF-12_a"]


@pytest.mark.parametrize("bad_id", BAD_IDS)
def test_repo_wf_rejects_invalid_workflow_ids(tmp_path, bad_id):
    repo = Repo(tmp_path)
    with pytest.raises(ValueError):
        repo.wf(bad_id, "parsed", "dag.json")


@pytest.mark.parametrize("good_id", GOOD_IDS)
def test_repo_wf_accepts_valid_workflow_ids(tmp_path, good_id):
    repo = Repo(tmp_path)
    path = repo.wf(good_id, "parsed", "dag.json")
    assert path == tmp_path.resolve() / "workflows" / good_id / "parsed" / "dag.json"


@pytest.mark.parametrize("bad_id", BAD_IDS)
def test_repo_seg_rejects_invalid_workflow_or_segment_ids(tmp_path, bad_id):
    repo = Repo(tmp_path)
    with pytest.raises(ValueError):
        repo.seg(bad_id, "seg_01", "dag.json")
    with pytest.raises(ValueError):
        repo.seg("wf_0001", bad_id, "dag.json")


@pytest.mark.parametrize("good_id", GOOD_IDS)
def test_repo_seg_accepts_valid_ids(tmp_path, good_id):
    repo = Repo(tmp_path)
    path = repo.seg(good_id, good_id, "dag.json")
    assert path == tmp_path.resolve() / "workflows" / good_id / "segments" / good_id / "dag.json"


@pytest.mark.parametrize("bad_id", BAD_IDS)
def test_wf_token_and_seg_token_reject_the_same_bad_ids(bad_id):
    with pytest.raises(ValueError):
        wf_token(bad_id)
    with pytest.raises(ValueError):
        seg_token(bad_id)


def test_repo_wf_and_seg_accept_ordinary_relative_parts_but_reject_absolute_ones(tmp_path):
    repo = Repo(tmp_path)
    # Script-authored literals like these are not external input and stay legal.
    assert repo.wf("wf_0001", "golden", "inputs", ".sandbox.duckdb").name == ".sandbox.duckdb"
    assert repo.seg("wf_0001", "seg_01", "proc.sql").name == "proc.sql"

    for bad_part in ("/abs", "\\abs", "C:\\evil", "C:/evil"):
        with pytest.raises(ValueError):
            repo.wf("wf_0001", bad_part)
        with pytest.raises(ValueError):
            repo.seg("wf_0001", "seg_01", bad_part)


# --- fix round 3 (task 9 re-review): Repo.wf/Repo.seg reject a dot component in *parts --------
#
# Round 2's _validate_part only rejected an ABSOLUTE part, so a *relative* traversal like
# "..", "..", "intake" still reached dev/build_samples.py's `_reset_dir` -- which judged
# ownership on the literal first part ("golden"/"inputs" are legal), not on where the whole
# thing actually resolved to. `chr(92)` builds the backslash cases so the literal string in the
# test source can't be misread as an escape sequence.

BAD_PARTS = ["..", ".", "", "a/../b", "a" + chr(92) + ".." + chr(92) + "b", "golden/./inputs"]
GOOD_PARTS = ["golden", "golden/inputs", "seg_01", "3_Output.csv", "validation.normal.json"]


@pytest.mark.parametrize("bad_part", BAD_PARTS)
def test_repo_wf_rejects_a_dot_component_in_parts(tmp_path, bad_part):
    repo = Repo(tmp_path)
    with pytest.raises(ValueError):
        repo.wf("wf_0001", bad_part)


@pytest.mark.parametrize("bad_part", BAD_PARTS)
def test_repo_seg_rejects_a_dot_component_in_parts(tmp_path, bad_part):
    repo = Repo(tmp_path)
    with pytest.raises(ValueError):
        repo.seg("wf_0001", "seg_01", bad_part)


@pytest.mark.parametrize("good_part", GOOD_PARTS)
def test_repo_wf_and_seg_accept_relative_parts_with_no_dot_component(tmp_path, good_part):
    repo = Repo(tmp_path)
    # A dot *inside* a component (a filename, or a multi-component literal) is not a traversal.
    repo.wf("wf_0001", good_part)
    repo.seg("wf_0001", "seg_01", good_part)


def test_vocab_owns_the_data_less_node_types_that_target_check_and_compile_check_share():
    """Final fix wave M2: three copies of "node types that carry no data" had drifted into one
    shared set plus two hand-maintained supersets. `DATA_LESS_TYPES` is the superset, derived
    from `NON_DATA_TYPES` rather than retyped, so a type added to one reaches both call sites."""
    import compile_check
    import target_check

    assert vocab.DATA_LESS_TYPES == vocab.NON_DATA_TYPES | {"browse"}
    assert target_check.DATA_LESS_TYPES is vocab.DATA_LESS_TYPES
    assert compile_check.DATA_LESS_TYPES is vocab.DATA_LESS_TYPES
