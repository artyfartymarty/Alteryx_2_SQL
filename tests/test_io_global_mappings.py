"""`lib.io.write_global_mappings` (F1d, coordinator ruling): writing `mappings/global.yaml`
preserves everything ABOVE the top-level `sources:` key verbatim -- comments included -- and
regenerates only `sources`/`outputs`. When an existing file's shape doesn't allow that (no
top-level `sources:` line, or some other top-level key follows it), it falls back to a full dump
and says so on stderr, so the promotion logic in `intake_prompt.py` never silently drops content
it doesn't understand.
"""
from __future__ import annotations

from pathlib import Path

from lib import io as lib_io

ROOT = Path(__file__).parents[1]
REAL_GLOBAL_YAML = ROOT / "mappings" / "global.yaml"


def test_preserves_every_comment_line_above_sources_and_still_parses_the_same_values(tmp_path, capsys):
    path = tmp_path / "global.yaml"
    original = REAL_GLOBAL_YAML.read_text(encoding="utf-8")
    path.write_text(original, encoding="utf-8")
    comment_lines = [line for line in original.splitlines() if line.strip().startswith("#")]
    assert comment_lines, "sanity: the repo's real global.yaml has comments to preserve"

    doc = lib_io.read_yaml(path)
    doc["sources"] = {"sales/orders.yxdb": {"snowflake": "SALES.RAW.ORDERS", "logical": "ORDERS"}}
    doc["outputs"] = {"out/x.csv": {"snowflake": "ANALYTICS.CURATED.X", "logical": "X", "mode": "overwrite"}}

    lib_io.write_global_mappings(path, doc)

    new_lines = path.read_text(encoding="utf-8").splitlines()
    for comment_line in comment_lines:
        assert comment_line in new_lines, f"comment line lost: {comment_line!r}"

    reparsed = lib_io.read_yaml(path)
    assert reparsed["program"] == doc["program"]
    assert reparsed["session"] == doc["session"]
    assert reparsed["tolerances"] == doc["tolerances"]
    assert reparsed["accepted_diff_classes"] == doc["accepted_diff_classes"]
    assert reparsed["sources"] == doc["sources"]
    assert reparsed["outputs"] == doc["outputs"]
    assert capsys.readouterr().err == ""  # the expected shape: no fallback warning


def test_a_no_op_write_leaves_the_file_byte_identical(tmp_path):
    path = tmp_path / "global.yaml"
    original = REAL_GLOBAL_YAML.read_text(encoding="utf-8")
    path.write_text(original, encoding="utf-8")
    doc = lib_io.read_yaml(path)

    lib_io.write_global_mappings(path, doc)

    assert path.read_bytes() == original.encode("utf-8")


def test_falls_back_to_a_full_dump_and_warns_when_there_is_no_top_level_sources_line(tmp_path, capsys):
    path = tmp_path / "global.yaml"
    path.write_text("program:\n  target_database: ANALYTICS\n", encoding="utf-8")

    obj = {"program": {"target_database": "ANALYTICS"}, "sources": {"a": 1}, "outputs": {}}
    lib_io.write_global_mappings(path, obj)

    assert lib_io.read_yaml(path) == obj
    err = capsys.readouterr().err
    assert str(path) in err
    assert "sources" in err.lower()


def test_falls_back_when_a_top_level_key_follows_outputs(tmp_path, capsys):
    path = tmp_path / "global.yaml"
    path.write_text("program:\n  a: 1\nsources: {}\noutputs: {}\nextra:\n  b: 2\n", encoding="utf-8")

    obj = {"program": {"a": 1}, "sources": {"x": 1}, "outputs": {}, "extra": {"b": 2}}
    lib_io.write_global_mappings(path, obj)

    err = capsys.readouterr().err
    assert str(path) in err
    assert lib_io.read_yaml(path) == obj


def test_falls_back_when_outputs_appears_before_sources(tmp_path, capsys):
    path = tmp_path / "global.yaml"
    path.write_text("program:\n  a: 1\noutputs: {}\nsources: {}\n", encoding="utf-8")

    obj = {"program": {"a": 1}, "outputs": {"y": 1}, "sources": {}}
    lib_io.write_global_mappings(path, obj)

    err = capsys.readouterr().err
    assert str(path) in err
    assert lib_io.read_yaml(path) == obj


def test_a_brand_new_file_is_a_plain_dump_without_a_warning(tmp_path, capsys):
    path = tmp_path / "global.yaml"

    lib_io.write_global_mappings(path, {"program": {"a": 1}, "sources": {}, "outputs": {}})

    assert lib_io.read_yaml(path) == {"program": {"a": 1}, "sources": {}, "outputs": {}}
    assert capsys.readouterr().err == ""


def test_creates_parent_directories_when_missing(tmp_path):
    path = tmp_path / "nested" / "mappings" / "global.yaml"

    lib_io.write_global_mappings(path, {"sources": {}, "outputs": {}})

    assert path.is_file()
