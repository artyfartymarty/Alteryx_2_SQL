"""Structural checks on the hand-written sample workflows and their golden inputs (plan task 3).

The sample sources under `samples/` are synthetic files written by hand to
`docs/reference/dag-contract.md` §2-§5. **No file here was produced or opened by Alteryx**,
so these tests check the shape the parser, segmenter and simulator are written against,
not agreement with Alteryx Designer.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lib import typed_csv, vocab

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

# Shape the plan fixes for each sample; later tasks assert on these ids and counts.
EXPECTED = {
    "wf_0001": {"file": "sales_summary.yxmd", "nodes": 8, "sets": vocab.GOLDEN_SETS,
                "inputs": ["1"], "outputs": ["7", "8"], "targets_before": []},
    "wf_0002": {"file": "customer_orders.yxmd", "nodes": 15, "sets": vocab.GOLDEN_SETS,
                "inputs": ["1", "3"], "outputs": ["10"], "targets_before": ["CUSTOMER_ORDER_FACT"]},
    "wf_0003": {"file": "gl_period_close.yxmd", "nodes": 12, "sets": vocab.GOLDEN_SETS,
                "inputs": ["1"], "outputs": ["10"], "targets_before": ["GL_SUMMARY"]},
    "wf_0004": {"file": "inventory.yxmd", "nodes": 7, "sets": vocab.GOLDEN_SETS,
                "inputs": ["1"], "outputs": ["6", "7"], "targets_before": []},
    "wf_0005": {"file": "vendor_dedupe.yxmd", "nodes": 4, "sets": ("normal", "empty"),
                "inputs": ["1"], "outputs": ["4"], "targets_before": []},
    "wf_0006": {"file": "subscription_revenue.yxmd", "nodes": 5, "sets": vocab.GOLDEN_SETS,
                "inputs": ["1"], "outputs": ["5"], "targets_before": []},
    # 7 tools plus Tool Containers 10 and 11 (output-targets phase 2, Task C: the dbt sample).
    "wf_0007": {"file": "regional_targets.yxmd", "nodes": 9, "sets": vocab.GOLDEN_SETS,
                "inputs": ["1", "2"], "outputs": ["6", "7"], "targets_before": ["ATTAINMENT_HISTORY"]},
}
WORKFLOWS = sorted(EXPECTED)

SAMPLE_JSON_KEYS = {"id", "title", "owner", "schedule", "segmentation",
                    "expected_terminal", "answers", "logical"}

# Optional: the output kind a sample asks for (`build_samples.seed` copies it into the manifest,
# where `target_check.py --prefer auto` reads it). Only the dbt sample sets it.
OPTIONAL_SAMPLE_JSON_KEYS = {"output_target"}
OUTPUT_TARGETS = {"procedures", "dbt"}

# The only credential-shaped strings allowed to exist in the repo: obviously fake
# placeholders that give the parser's scrubber (dag-contract §6) something to remove.
PLACEHOLDER_PASSWORDS = {"__EncPwd1__", "__EncPwd2__"}
PLACEHOLDER_USERS = {"etl_user", "svc_alx"}


def source_dir(wf: str) -> Path:
    return SAMPLES / wf / "source"


def source_files(wf: str) -> list[Path]:
    return sorted(source_dir(wf).rglob("*.yx*"))


def root_of(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def nodes_of(root: ET.Element) -> list[ET.Element]:
    """Every <Node>, including ones nested in a Tool Container's <ChildNodes>."""
    return [n for n in root.iter("Node") if "ToolID" in n.attrib]


def tool_ids(root: ET.Element) -> list[str]:
    return [n.get("ToolID") for n in nodes_of(root)]


def node_by_id(root: ET.Element, tool_id: str) -> ET.Element:
    return next(n for n in nodes_of(root) if n.get("ToolID") == tool_id)


def meta_fields(node: ET.Element, anchor: str) -> list[tuple]:
    """(name, type, size, scale) per field of one out anchor's MetaInfo/RecordInfo."""
    meta = next(m for m in node.findall("./Properties/MetaInfo") if m.get("connection") == anchor)
    return [(f.get("name"), f.get("type"),
             int(f.get("size")) if f.get("size") is not None else None,
             int(f.get("scale")) if f.get("scale") is not None else None)
            for f in meta.findall("./RecordInfo/Field")]


def schema_fields(table: dict) -> list[tuple]:
    return [(f["name"], f["type"], f["size"], f["scale"]) for f in table["fields"]]


def sample_json(wf: str) -> dict:
    return json.loads((SAMPLES / wf / "sample.json").read_text(encoding="utf-8"))


def main_root(wf: str) -> ET.Element:
    return root_of(source_dir(wf) / EXPECTED[wf]["file"])


# --- the checks the plan's Step 1 names ---

def test_samples_are_exactly_the_planned_workflows():
    assert sorted(p.name for p in SAMPLES.glob("wf_*") if p.is_dir()) == WORKFLOWS


@pytest.mark.parametrize("wf", WORKFLOWS)
def test_every_source_parses_and_is_an_alteryx_document(wf):
    files = source_files(wf)
    assert files, f"{wf} has no source/*.yx* file"
    assert (source_dir(wf) / EXPECTED[wf]["file"]).exists()
    for path in files:
        root = root_of(path)
        assert root.tag == "AlteryxDocument", f"{path.name} root is {root.tag}"
        assert root.get("yxmdVer"), f"{path.name} has no yxmdVer"


@pytest.mark.parametrize("wf", WORKFLOWS)
def test_tool_ids_are_unique_within_each_file(wf):
    for path in source_files(wf):
        ids = tool_ids(root_of(path))
        assert ids, f"{path.name} has no nodes"
        assert len(ids) == len(set(ids)), f"{path.name} repeats ToolIDs"


@pytest.mark.parametrize("wf", WORKFLOWS)
def test_every_connection_endpoint_exists(wf):
    for path in source_files(wf):
        root = root_of(path)
        known = set(tool_ids(root))
        for conn in root.iter("Connection"):
            origin, dest = conn.find("Origin"), conn.find("Destination")
            assert origin is not None and dest is not None, f"{path.name} has a half connection"
            for side, end in (("Origin", origin), ("Destination", dest)):
                assert end.get("ToolID") in known, f"{path.name} {side} {end.get('ToolID')} is not a node"
                assert end.get("Connection"), f"{path.name} {side} {end.get('ToolID')} has no anchor"


@pytest.mark.parametrize("wf", WORKFLOWS)
def test_sample_json_has_the_planned_keys(wf):
    meta = sample_json(wf)
    assert SAMPLE_JSON_KEYS <= set(meta) <= SAMPLE_JSON_KEYS | OPTIONAL_SAMPLE_JSON_KEYS
    assert meta.get("output_target", "procedures") in OUTPUT_TARGETS
    assert meta["id"] == wf and meta["owner"] == "wf_owner"
    assert meta["expected_terminal"] in vocab.STATUSES
    # Presence and type only: the bounds are per-workflow (wf_0002 needs min_tools 2 for its
    # container cuts to survive, the rest use 3), so no single number belongs in this assertion.
    seg = meta["segmentation"]
    assert set(seg) == {"min_tools", "max_tools"}
    assert all(isinstance(seg[k], int) and not isinstance(seg[k], bool) for k in seg), seg
    assert 0 < seg["min_tools"] <= seg["max_tools"]
    assert isinstance(meta["answers"], dict) and isinstance(meta["logical"], dict)


@pytest.mark.parametrize("wf", WORKFLOWS)
def test_golden_inputs_match_their_input_tool_metainfo(wf):
    root = main_root(wf)
    for golden_set in EXPECTED[wf]["sets"]:
        set_dir = SAMPLES / wf / "golden_inputs" / golden_set
        csvs = sorted(set_dir.glob("*.csv"))
        assert [p.stem for p in csvs] == EXPECTED[wf]["inputs"], f"{wf}/{golden_set}"
        for path in csvs:
            assert (set_dir / f"{path.stem}.schema.json").exists(), f"{path} has no schema sidecar"
            table = typed_csv.read_table(path)
            assert schema_fields(table) == meta_fields(node_by_id(root, path.stem), "Output"), \
                f"{wf}/{golden_set}/{path.name} does not match tool {path.stem}'s MetaInfo"


@pytest.mark.parametrize("wf", WORKFLOWS)
def test_empty_sets_have_a_header_and_no_rows(wf):
    for path in sorted((SAMPLES / wf / "golden_inputs" / "empty").glob("*.csv")):
        table = typed_csv.read_table(path)
        assert table["fields"] and table["rows"] == [], f"{path} is not empty"
        assert path.read_text(encoding="utf-8").splitlines()[0].split(",") == \
            [f["name"] for f in table["fields"]]


# --- behaviour the plan describes in prose but does not test ---

@pytest.mark.parametrize("wf", WORKFLOWS)
def test_node_count_matches_the_plan(wf):
    assert len(nodes_of(main_root(wf))) == EXPECTED[wf]["nodes"]


@pytest.mark.parametrize("wf", WORKFLOWS)
def test_every_originating_anchor_has_a_metainfo_block(wf):
    """`MetaInfo connection=` must exist for every out anchor a connection leaves from:
    intake and the segment contracts read field schemas from it."""
    for path in source_files(wf):
        root = root_of(path)
        for conn in root.iter("Connection"):
            origin = conn.find("Origin")
            node = node_by_id(root, origin.get("ToolID"))
            anchors = {m.get("connection") for m in node.findall("./Properties/MetaInfo")}
            assert origin.get("Connection") in anchors, \
                f"{path.name} tool {origin.get('ToolID')} has no MetaInfo for {origin.get('Connection')}"


@pytest.mark.parametrize("wf", WORKFLOWS)
def test_output_tools_carry_the_schema_they_write(wf):
    root = main_root(wf)
    for tool_id in EXPECTED[wf]["outputs"]:
        assert meta_fields(node_by_id(root, tool_id), "Output"), \
            f"{wf} output tool {tool_id} has no MetaInfo"


@pytest.mark.parametrize("wf", WORKFLOWS)
def test_every_input_and_output_tool_has_a_logical_name(wf):
    meta = sample_json(wf)
    expected = EXPECTED[wf]["inputs"] + EXPECTED[wf]["outputs"]
    assert sorted(meta["logical"], key=int) == sorted(expected, key=int)
    assert len(set(meta["logical"].values())) == len(expected), "logical names must be unique"
    assert len(meta["answers"]) == len(expected)
    assert all(re.fullmatch(r"[A-Z_][A-Z0-9_]*(\.[A-Z_][A-Z0-9_]*){2}", fqn)
               for fqn in meta["answers"].values()), meta["answers"]


@pytest.mark.parametrize("wf", WORKFLOWS)
def test_targets_before_is_present_for_append_and_merge_outputs(wf):
    logicals = EXPECTED[wf]["targets_before"]
    root_dir = SAMPLES / wf / "golden_inputs" / "targets_before"
    if not logicals:
        assert not root_dir.exists(), f"{wf} needs no targets_before"
        return
    known = set(sample_json(wf)["logical"].values())
    for golden_set in EXPECTED[wf]["sets"]:
        found = sorted(p.stem for p in (root_dir / golden_set).glob("*.csv"))
        assert found == sorted(logicals), f"{wf}/targets_before/{golden_set}"
        for logical in found:
            assert logical in known
            assert (root_dir / golden_set / f"{logical}.schema.json").exists()
            typed_csv.read_table(root_dir / golden_set / f"{logical}.csv")


@pytest.mark.parametrize("wf", WORKFLOWS)
def test_only_obviously_fake_credentials_appear_in_the_sources(wf):
    for path in source_files(wf):
        text = path.read_text(encoding="utf-8")
        assert set(re.findall(r"PWD=([^;|<\"]*)", text)) <= PLACEHOLDER_PASSWORDS, path
        assert set(re.findall(r"UID=([^;|<\"]*)", text)) <= PLACEHOLDER_USERS, path
        assert "Password=" not in text and "User ID=" not in text


def test_the_workflows_that_need_credentials_actually_carry_them():
    """wf_0002 and wf_0003 exist to exercise the scrubber, so the placeholders must be there."""
    for wf, password in (("wf_0002", "__EncPwd1__"), ("wf_0003", "__EncPwd2__")):
        text = (source_dir(wf) / EXPECTED[wf]["file"]).read_text(encoding="utf-8")
        assert f"PWD={password}" in text


def test_the_macro_workflow_references_a_macro_that_is_present():
    root = main_root("wf_0004")
    macro = next(e.get("Macro") for e in root.iter("EngineSettings") if e.get("Macro"))
    assert macro == r"Supporting_Macros\clean_codes.yxmc"
    assert (source_dir("wf_0004") / macro.replace("\\", "/")).exists()


def test_data_cleansing_and_macro_nodes_have_no_plugin_attribute():
    """dag-contract §2: these two are classified by <EngineSettings Macro=…>, not by Plugin."""
    for wf, tool_id, macro in (("wf_0002", "2", "Cleanse.yxmc"),
                               ("wf_0004", "2", r"Supporting_Macros\clean_codes.yxmc")):
        node = node_by_id(main_root(wf), tool_id)
        assert "Plugin" not in node.find("GuiSettings").attrib
        assert node.find("EngineSettings").get("Macro") == macro


def test_union_destinations_carry_the_alteryx_input_suffix():
    root = main_root("wf_0002")
    dests = [c.find("Destination") for c in root.iter("Connection")
             if c.find("Destination").get("ToolID") == "9"]
    assert [d.get("Connection") for d in dests] == ["Input"] * 3
    assert sorted(d.get("name") for d in dests) == ["#1", "#2", "#3"]


def test_golden_input_files_are_utf8_lf():
    for wf in WORKFLOWS:
        for path in (SAMPLES / wf / "golden_inputs").rglob("*.csv"):
            raw = path.read_bytes()
            assert b"\r" not in raw, path
            raw.decode("utf-8")


def test_every_workflow_documents_its_golden_rows():
    for wf in WORKFLOWS:
        assert (SAMPLES / wf / "README.md").read_text(encoding="utf-8").strip(), wf


def test_the_dbt_sample_asks_for_the_dbt_output_kind():
    """wf_0007 exists to be migrated as a dbt project (output-targets design §7.3); every other
    sample leaves the preference to `mappings/global.yaml` (procedures)."""
    for wf in WORKFLOWS:
        expected = "dbt" if wf == "wf_0007" else None
        assert sample_json(wf).get("output_target") == expected, wf
