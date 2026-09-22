"""Parser tests: the five sample workflows parsed to `dag.json` (plan task 4).

The samples are hand-written to `docs/reference/dag-contract.md`; **no file here was produced or
opened by Alteryx**, so these tests pin the contract's shapes, not agreement with Designer.
"""
from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import parse, invariants
from parsers import registry, tool_config

SAMPLES = Path(__file__).parents[1] / "samples"


def dag_of(wf, name):
    dag, _ = parse.parse_file(SAMPLES / wf / "source" / name)
    return dag, {n["tool_id"]: n for n in dag["nodes"]}


# --- the tests the plan's Step 1 names ---

def test_filter_anchors_and_formula_order():
    dag, n = dag_of("wf_0001", "sales_summary.yxmd")
    assert dag["engine"] == "AMP" and dag["yxmd_version"] == "2023.1"
    assert n["3"]["type"] == "filter" and n["3"]["out_anchors"] == ["T", "F"]
    assert {"src": "3", "src_anchor": "F", "dst": "8", "dst_anchor": "Input"}.items() <= next(e for e in dag["edges"] if e["dst"] == "8").items()
    assert [f["field"] for f in n["4"]["config"]["formulas"]] == ["AMOUNT", "NET", "SIZE_BAND"]
    assert n["2"]["config"]["fields"][0] == {"name": "CUSTOMER", "selected": True, "rename": None, "type": "String", "size": 10}
    assert n["1"]["meta"]["Output"][0] == {"name": "ORDER_ID", "type": "Int32", "size": 4, "scale": None}


def test_nested_containers_and_cleanse_macro():
    dag, n = dag_of("wf_0002", "customer_orders.yxmd")
    assert n["3"]["container_id"] == "210" and n["210"]["container_id"] == "200" and n["1"]["container_id"] == "100"
    assert n["2"]["type"] == "data_cleansing" and n["2"]["config"]["modify_case"] == "upper"
    assert n["300"]["type"] == "comment"
    assert sorted(e["src_anchor"] for e in dag["edges"] if e["src"] == "5") == ["J", "L", "R"]
    assert [e["src"] for e in sorted((e for e in dag["edges"] if e["dst"] == "9"), key=lambda e: e["dst_order"])] == ["6", "7", "8"]


def test_credentials_are_scrubbed_everywhere():
    dag, n = dag_of("wf_0003", "gl_period_close.yxmd")
    _, aliases = parse.parse_file(SAMPLES / "wf_0003" / "source" / "gl_period_close.yxmd")
    blob = str(dag)
    assert "svc_alx" not in blob and "__EncPwd" not in blob and aliases == ["prod_fin"]
    assert n["1"]["config"]["source"] == "<scrubbed:prod_fin>" and n["1"]["config"]["alias"] == "prod_fin"
    assert n["1"]["config"]["query"].startswith("SELECT ACCT")
    assert dag["constants"] == {"User.Region": "EMEA", "User.PeriodEnd": "2026-08-31"}
    assert n["10"]["config"]["write_mode"] == "update_insert" and n["10"]["config"]["keys"] == ["ACCT", "PERIOD"]
    assert n["10"]["config"]["pre_sql"].startswith("DELETE") and n["10"]["config"]["table"] == "dbo.GL_SUMMARY"


def test_macro_is_parsed_recursively():
    _, n = dag_of("wf_0004", "inventory.yxmd")
    m = n["2"]
    assert m["type"] == "macro" and m["macro_path"] == "Supporting_Macros/clean_codes.yxmc"
    assert m["interface"] == [{"name": "MinQty", "type": "NumericUpDown", "default": "0"}]
    assert m["config"]["values"] == {"MinQty": "1"} and len(m["sub_dag"]["nodes"]) == 6


def test_missing_macro_is_flagged_not_dropped(tmp_path):
    shutil.copy(SAMPLES / "wf_0004" / "source" / "inventory.yxmd", tmp_path / "inventory.yxmd")
    dag, _ = parse.parse_file(tmp_path / "inventory.yxmd")
    m = next(x for x in dag["nodes"] if x["tool_id"] == "2")
    assert m["unresolved"] is True and m["sub_dag"] is None


def test_unknown_plugin_trips_the_share_invariant_until_an_extension_explains_it(tmp_path):
    src = SAMPLES / "wf_0005" / "source" / "vendor_dedupe.yxmd"
    dag, _ = parse.parse_file(src)
    errs = invariants.check(src.read_text(encoding="utf-8"), dag)
    assert any("unknown" in e for e in errs)
    ext = tmp_path / "ext"; ext.mkdir()
    (ext / "acme.py").write_text(
        "from parsers.registry import register_plugin\n"
        "register_plugin('AcmeAnalytics.Dedupe.DedupeTool', lambda x, n: {'type': 'unknown', 'in_anchors': ['Input'],"
        " 'out_anchors': ['Output'], 'behavior': 'appears to dedupe on ACCT keeping max UPDATED', 'confidence': 0.6})\n")
    try:
        dag2, _ = parse.parse_file(src, ext_dirs=[ext])
        assert invariants.check(src.read_text(encoding="utf-8"), dag2) == []
    finally:
        registry.reset()


def test_run_writes_report_and_scrubs_source(tmp_path):
    from lib.paths import Repo
    repo = Repo(tmp_path)
    dest = repo.wf("wf_0003", "source"); dest.mkdir(parents=True)
    shutil.copy(SAMPLES / "wf_0003" / "source" / "gl_period_close.yxmd", dest)
    report = parse.run(repo, "wf_0003", check=True)
    assert report["status"] == "PARSED" and report["scrubbed_aliases"] == ["prod_fin"] and report["node_count"] == 12
    assert "__EncPwd" not in (dest / "gl_period_close.yxmd").read_text(encoding="utf-8")


# --- behaviour the plan describes in prose but does not test ---

def test_decode_xml_reads_bom_declared_encoding_and_falls_back_to_cp1252():
    assert parse.decode_xml('<?xml version="1.0"?><a>x</a>'.encode("utf-8-sig")).startswith("<?xml")
    assert "\ufeff" not in parse.decode_xml('<a>x</a>'.encode("utf-8-sig"))
    assert parse.decode_xml('<?xml version="1.0" encoding="windows-1252"?><a>caf\u00e9</a>'.encode("cp1252")).endswith("caf\u00e9</a>")
    # no declaration and not valid UTF-8 -> cp1252 (dag-contract: encoding is a parser fallback class)
    assert parse.decode_xml(b"<a>caf\xe9</a>") == "<a>caf\u00e9</a>"
    assert parse.decode_xml(b"<a>caf\xc3\xa9</a>") == "<a>caf\u00e9</a>"


def test_document_level_fields_come_from_the_document():
    dag, _ = dag_of("wf_0002", "customer_orders.yxmd")
    assert dag["engine"] == "E1"  # <RunE2 value="False"/>
    assert dag["file_kind"] == "yxmd" and dag["source_file"] == "customer_orders.yxmd"
    assert dag["workflow"] == "wf_0002" and dag["constants"] == {}


def test_container_caption_becomes_the_annotation_and_containers_carry_no_data():
    dag, n = dag_of("wf_0002", "customer_orders.yxmd")
    assert n["100"]["type"] == "container" and n["100"]["annotation"] == "Prep customers"
    assert n["210"]["annotation"] == "Type the CSV export"
    assert n["1"]["annotation"] == "CRM customers" and n["1"]["container_id"] == "100"
    assert n["100"]["container_id"] is None
    wired = {e["src"] for e in dag["edges"]} | {e["dst"] for e in dag["edges"]}
    assert wired.isdisjoint({"100", "200", "210", "300"})


def test_every_edge_carries_wireless_and_a_dst_order():
    dag, _ = dag_of("wf_0001", "sales_summary.yxmd")
    assert all(e["wireless"] is False and e["dst_order"] == 1 for e in dag["edges"])
    assert len(dag["edges"]) == 7


def test_input_config_covers_files_csv_and_db_sources():
    _, n1 = dag_of("wf_0001", "sales_summary.yxmd")
    assert n1["1"]["config"] == {"source": r"C:\data\sales\orders.yxdb", "format": "yxdb", "query": None,
                                 "alias": None, "csv": None, "record_limit": None}
    _, n2 = dag_of("wf_0002", "customer_orders.yxmd")
    assert n2["3"]["config"]["format"] == "csv"
    assert n2["3"]["config"]["csv"] == {"delimiter": ",", "header": True, "codepage": "28591"}
    _, n3 = dag_of("wf_0003", "gl_period_close.yxmd")
    assert n3["1"]["config"]["format"] == "db" and n3["1"]["config"]["csv"] is None


def test_output_config_covers_write_modes_tables_and_sql():
    _, n1 = dag_of("wf_0001", "sales_summary.yxmd")
    assert n1["8"]["config"] == {"source": r"C:\data\out\excluded_orders.csv", "format": "csv", "alias": None,
                                 "table": None, "write_mode": "overwrite", "keys": [],
                                 "pre_sql": None, "post_sql": None}
    _, n2 = dag_of("wf_0002", "customer_orders.yxmd")
    assert n2["10"]["config"] == {"source": "<scrubbed:dw_sales>", "format": "db", "alias": "dw_sales",
                                  "table": "dbo.CUSTOMER_ORDER_FACT", "write_mode": "append", "keys": [],
                                  "pre_sql": None, "post_sql": None}
    _, n3 = dag_of("wf_0003", "gl_period_close.yxmd")
    assert n3["10"]["config"]["post_sql"].startswith("UPDATE dbo.GL_SUMMARY")


def test_sink_tools_keep_the_metainfo_of_the_records_they_receive():
    _, n = dag_of("wf_0002", "customer_orders.yxmd")
    assert n["11"]["type"] == "browse" and n["11"]["out_anchors"] == []
    assert [f["name"] for f in n["11"]["meta"]["Output"]][:2] == ["CUST_ID", "NAME"]
    assert [f["name"] for f in n["10"]["meta"]["Output"]][:2] == ["CUST_ID", "NAME"]


def test_meta_keys_are_canonical_anchor_names():
    _, n1 = dag_of("wf_0001", "sales_summary.yxmd")
    assert set(n1["3"]["meta"]) == {"T", "F"}
    _, n2 = dag_of("wf_0002", "customer_orders.yxmd")
    assert set(n2["5"]["meta"]) == {"L", "J", "R"}
    _, n3 = dag_of("wf_0003", "gl_period_close.yxmd")
    assert set(n3["5"]["meta"]) == {"U", "D"}
    assert n3["1"]["meta"]["Output"][3] == {"name": "AMOUNT", "type": "FixedDecimal", "size": 19, "scale": 2}


def test_raw_config_is_kept_and_scrubbed():
    _, n = dag_of("wf_0003", "gl_period_close.yxmd")
    raw = n["1"]["raw_config"]
    assert raw.startswith("<Configuration>") and "FROM dbo.GL_LEDGER" in raw
    assert "PWD=" not in raw and "UID=" not in raw and "scrubbed:prod_fin" in raw
    assert n["2"]["raw_config"].count("<Expression>") == 1


def test_select_filter_formula_and_join_configs():
    _, n1 = dag_of("wf_0001", "sales_summary.yxmd")
    assert n1["2"]["config"]["unknown_selected"] is True
    assert n1["2"]["config"]["fields"][1] == {"name": "STATUS", "selected": True, "rename": "ORDER_STATUS",
                                              "type": None, "size": None}
    assert n1["3"]["config"] == {"expression": '[REGION] != "WEST"'}
    assert n1["4"]["config"]["formulas"][1] == {"field": "NET", "expression": "Round([AMOUNT] * IIF([QTY] >= 10, 0.9, 1), 0.01)",
                                               "type": "Double", "size": 8}
    _, n2 = dag_of("wf_0002", "customer_orders.yxmd")
    assert n2["5"]["config"]["keys"] == [{"left": "CUST_ID", "right": "CUST_ID"}]
    assert n2["5"]["config"]["select"] == {"fields": [{"name": "Right_CUST_ID", "selected": False, "rename": None,
                                                       "type": None, "size": None}], "unknown_selected": True}
    assert n2["9"]["config"] == {"mode": "name"}


def test_order_dependent_tool_configs():
    _, n = dag_of("wf_0003", "gl_period_close.yxmd")
    assert n["4"]["config"]["fields"] == [{"field": "ACCT", "order": "asc"}, {"field": "POSTED_DT", "order": "asc"},
                                          {"field": "ENTRY_ID", "order": "desc"}]
    assert n["5"]["config"] == {"fields": ["ACCT", "POSTED_DT"]}
    assert n["6"]["config"] == {"field": "RUN_BAL", "update_existing": False, "type": "Double", "size": 8,
                                "num_rows": 1, "expression": "[Row-1:RUN_BAL] + [AMOUNT]", "group_by": ["ACCT"],
                                "unknown_rows": "zero"}
    assert n["7"]["config"] == {"field": "RecordID", "start": 1, "type": "Int32", "position": "first"}
    assert n["3"]["config"] == {"direction": "to_datetime", "field": "POSTED", "format": "%d/%m/%Y",
                                "out_field": "POSTED_DT"}
    assert n["9"]["config"]["fields"][0] == {"field": "ACCT", "action": "GroupBy", "rename": "ACCT", "separator": None}
    assert n["9"]["config"]["fields"][3]["action"] == "Last"


def test_regex_cross_tab_and_transpose_configs():
    _, n = dag_of("wf_0004", "inventory.yxmd")
    assert n["3"]["config"] == {"field": "SKU", "expression": r"^([A-Z]+)-(\d+)$", "case_insensitive": False,
                                "method": "parse", "replace": None, "copy_unmatched": False, "match_field": None,
                                "output_fields": [{"name": "FAMILY", "type": "V_String", "size": 20},
                                                  {"name": "ITEM_NO", "type": "V_String", "size": 10}]}
    assert n["4"]["config"] == {"group_by": ["SKU", "FAMILY"], "header_field": "WAREHOUSE", "data_field": "QTY",
                                "methods": ["Sum"]}
    assert n["5"]["config"] == {"key_fields": ["SKU"], "data_fields": ["EAST", "NORTH", "WEST"]}
    sub = {x["tool_id"]: x for x in n["2"]["sub_dag"]["nodes"]}
    assert sub["2"]["config"]["method"] == "replace" and sub["2"]["config"]["replace"] == "$1-$2"
    assert sub["2"]["config"]["copy_unmatched"] is True and sub["2"]["config"]["case_insensitive"] is True


def test_data_cleansing_maps_every_known_value_name():
    _, n = dag_of("wf_0002", "customer_orders.yxmd")
    assert n["2"]["config"] == {"fields": ["NAME", "CITY"], "replace_null_strings_blank": True,
                                "replace_null_numeric_zero": False, "trim_whitespace": True,
                                "remove_tabs_linebreaks_dupspaces": False, "remove_all_whitespace": False,
                                "remove_letters": False, "remove_numbers": False, "remove_punctuation": False,
                                "modify_case": "upper"}


def test_macro_anchors_are_named_after_the_macros_own_tools():
    _, n = dag_of("wf_0004", "inventory.yxmd")
    assert n["2"]["in_anchors"] == ["Input1"] and n["2"]["out_anchors"] == ["Output5"]
    assert n["2"]["unresolved"] is False
    assert n["2"]["sub_dag"]["file_kind"] == "yxmc"
    sub = {x["tool_id"]: x for x in n["2"]["sub_dag"]["nodes"]}
    assert sub["10"]["type"] == "interface" and sub["1"]["type"] == "macro_input"


def test_unresolved_macro_takes_its_anchors_from_the_connections(tmp_path):
    shutil.copy(SAMPLES / "wf_0004" / "source" / "inventory.yxmd", tmp_path / "inventory.yxmd")
    dag, _ = parse.parse_file(tmp_path / "inventory.yxmd")
    m = next(x for x in dag["nodes"] if x["tool_id"] == "2")
    assert m["macro_path"] == "Supporting_Macros/clean_codes.yxmc" and m["interface"] == []
    assert m["in_anchors"] == ["Input1"] and m["out_anchors"] == ["Output5"]
    assert invariants.check((tmp_path / "inventory.yxmd").read_text(encoding="utf-8"), dag) == []


def test_unknown_tools_keep_their_raw_config_and_get_anchors_from_connections():
    _, n = dag_of("wf_0005", "vendor_dedupe.yxmd")
    assert n["2"]["type"] == "unknown" and n["2"]["plugin"] == "AcmeAnalytics.Dedupe.DedupeTool"
    assert n["2"]["config"] == {} and "<KeyField>ACCT</KeyField>" in n["2"]["raw_config"]
    assert n["2"]["in_anchors"] == ["Input"] and n["2"]["out_anchors"] == ["Output"]
    assert n["3"]["type"] == "run_command" and n["3"]["config"] == {"command": r"C:\scripts\notify.bat", "args": None}


@pytest.mark.parametrize("wf,name", [("wf_0001", "sales_summary.yxmd"), ("wf_0002", "customer_orders.yxmd"),
                                     ("wf_0003", "gl_period_close.yxmd"), ("wf_0004", "inventory.yxmd")])
def test_the_migratable_samples_satisfy_every_invariant(wf, name):
    src = SAMPLES / wf / "source" / name
    dag, _ = parse.parse_file(src)
    assert invariants.check(src.read_text(encoding="utf-8"), dag) == []


def test_an_element_extension_can_add_document_level_fields():
    calls = []

    def handler(el, dag):
        calls.append(el.tag)
        dag["layout"] = (el.text or "").strip()

    registry.register_element("LayoutType", handler)
    try:
        dag, _ = dag_of("wf_0001", "sales_summary.yxmd")
        assert dag["layout"] == "Horizontal" and calls == ["LayoutType"]
    finally:
        registry.reset()


def config_of(tool_type, xml):
    return tool_config.parse_config(tool_type, ET.fromstring(xml))


def test_tool_shapes_the_samples_do_not_reach():
    """dag-contract §4 cases with no sample workflow behind them yet."""
    assert config_of("sample", "<Configuration><Mode>First</Mode><N>1</N>"
                               "<GroupFields><Field name='ACCT'/></GroupFields></Configuration>") == \
        {"mode": "first", "n": 1, "group_by": ["ACCT"]}
    # an `aka:` alias instead of a DSN, and the query in CDATA
    assert config_of("input", "<Configuration><File FileFormat='23'>aka:PROD_ORACLE|||"
                              "<![CDATA[SELECT 1]]></File></Configuration>") == \
        {"source": "<scrubbed:prod_oracle>", "format": "db", "query": "SELECT 1",
         "alias": "prod_oracle", "csv": None, "record_limit": None}
    assert config_of("record_id", "<Configuration><FieldName>ID</FieldName><StartValue>0</StartValue>"
                                  "<FieldType>Int64</FieldType><Position>1</Position></Configuration>") == \
        {"field": "ID", "start": 0, "type": "Int64", "position": "last"}
    assert config_of("regex", "<Configuration><Field>SKU</Field><RegExExpression value='A'/>"
                              "<Method>Match</Method><Match><Field>SKU_Matched</Field></Match>"
                              "</Configuration>")["match_field"] == "SKU_Matched"
    updated = config_of("multi_row_formula",
                        "<Configuration><UpdateField value='True'/><UpdateField_Name>BAL</UpdateField_Name>"
                        "<NumRows value='2'/><OtherRows>NULL</OtherRows>"
                        "<Expression>[Row-1:BAL]</Expression></Configuration>")
    assert updated["field"] == "BAL" and updated["update_existing"] is True
    assert updated["num_rows"] == 2 and updated["unknown_rows"] == "null"
    assert config_of("transpose", "<Configuration><KeyFields><Field field='A'/></KeyFields>"
                                  "<DataFields><Field field='B' selected='True'/>"
                                  "<Field field='C' selected='False'/></DataFields></Configuration>") == \
        {"key_fields": ["A"], "data_fields": ["B"]}
    assert config_of("union", "<Configuration><Mode>ByPos</Mode></Configuration>") == {"mode": "position"}
    for option, mode in (("Overwrite", "overwrite"), ("Create New Table", "overwrite"),
                         ("Overwrite Table (Drop)", "overwrite"), ("Append Existing", "append"),
                         ("Update; Insert if new", "update_insert"),
                         ("Delete Data &amp; Append", "truncate_append")):
        xml = f"<Configuration><File FileFormat='19'>o.yxdb</File><OutputOption>{option}</OutputOption></Configuration>"
        assert config_of("output", xml)["write_mode"] == mode, option
    # a write mode nobody has taught the parser stays null rather than becoming an overwrite
    assert config_of("output", "<Configuration><File FileFormat='19'>o.yxdb</File>"
                               "<OutputOption>Elope With The Data</OutputOption></Configuration>")["write_mode"] is None
    for tool_type in ("container", "comment", "action", "browse", "unknown", "append_fields"):
        assert config_of(tool_type, "<Configuration><Whatever/></Configuration>") == {}


MINIMAL = """<?xml version="1.0"?>
<AlteryxDocument yxmdVer="2023.1">
  <Nodes>
    <Node ToolID="1"><GuiSettings Plugin="AlteryxBasePluginsGui.DbFileInput.DbFileInput" />
      <Properties><Configuration><File FileFormat="19">C:\\data\\a.yxdb</File></Configuration></Properties>
    </Node>
    <Node ToolID="2"><GuiSettings Plugin="AlteryxBasePluginsGui.BlockUntilDone.BlockUntilDone" /></Node>
  </Nodes>
  <Connections>
    <Connection Wireless="True">
      <Origin ToolID="1" Connection="Output" />
      <Destination ToolID="2" Connection="Input" />
    </Connection>
  </Connections>
</AlteryxDocument>
"""


VENDOR = """<?xml version="1.0"?>
<AlteryxDocument yxmdVer="2023.1">
  <Nodes>
    <Node ToolID="1"><GuiSettings Plugin="AlteryxBasePluginsGui.DbFileInput.DbFileInput" />
      <Properties><Configuration><File FileFormat="19">C:\\data\\v.yxdb</File></Configuration></Properties>
    </Node>
    <Node ToolID="2"><GuiSettings Plugin="AcmeAnalytics.Dedupe.DedupeTool" />
      <Properties>
        <Configuration><UniqueFields><Field field="ACCT" /></UniqueFields></Configuration>
        <Annotation DisplayMode="0"><DefaultAnnotationText>Acme dedupe</DefaultAnnotationText></Annotation>
        <MetaInfo connection="Unique"><RecordInfo><Field name="ACCT" type="V_String" size="20" /></RecordInfo></MetaInfo>
        <MetaInfo connection="Duplicates"><RecordInfo><Field name="ACCT" type="V_String" size="20" /></RecordInfo></MetaInfo>
      </Properties>
    </Node>
    <Node ToolID="3"><GuiSettings Plugin="AlteryxBasePluginsGui.DbFileOutput.DbFileOutput" />
      <Properties><Configuration><File FileFormat="19">C:\\data\\out.yxdb</File></Configuration></Properties>
    </Node>
  </Nodes>
  <Connections>
    <Connection><Origin ToolID="1" Connection="Output" /><Destination ToolID="2" Connection="Input" /></Connection>
    <Connection><Origin ToolID="2" Connection="Unique" /><Destination ToolID="3" Connection="Input" /></Connection>
  </Connections>
</AlteryxDocument>
"""


def test_a_wireless_connection_is_marked_as_one(tmp_path):
    path = tmp_path / "wireless.yxmd"
    path.write_text(MINIMAL, encoding="utf-8")
    dag, _ = parse.parse_file(path)
    assert dag["edges"] == [{"src": "1", "src_anchor": "Output", "dst": "2", "dst_anchor": "Input",
                             "dst_order": 1, "wireless": True}]
    assert dag["engine"] == "E1"  # no <Properties> at all
    assert next(n for n in dag["nodes"] if n["tool_id"] == "2")["out_anchors"] == \
        ["Output1", "Output2", "Output3"]


def vendor_dag(tmp_path, handler):
    """Parse VENDOR with `handler` registered for the Acme plugin, then clear the registry."""
    path = tmp_path / "vendor.yxmd"
    path.write_text(VENDOR, encoding="utf-8")
    registry.register_plugin("AcmeAnalytics.Dedupe.DedupeTool", handler)
    try:
        dag, _ = parse.parse_file(path)
    finally:
        registry.reset()
    return dag, {n["tool_id"]: n for n in dag["nodes"]}


def test_an_extension_may_remap_a_vendor_plugin_to_a_builtin_type(tmp_path):
    """The handler runs before classification, so everything the type governs follows its choice."""
    dag, n = vendor_dag(tmp_path, lambda node_xml, node: {"type": "unique"})
    assert n["2"]["type"] == "unique"
    assert n["2"]["config"] == {"fields": ["ACCT"]}  # parsed for `unique`, not left {} for `unknown`
    assert n["2"]["in_anchors"] == ["Input"] and n["2"]["out_anchors"] == ["U", "D"]
    assert set(n["2"]["meta"]) == {"U", "D"}  # MetaInfo re-keyed to the final type's anchors
    assert next(e for e in dag["edges"] if e["src"] == "2")["src_anchor"] == "U"
    assert invariants.check(VENDOR, dag) == []


def test_an_extension_that_supplies_a_config_keeps_it_verbatim(tmp_path):
    def handler(node_xml, node):
        # the handler is called with the node the parser has built so far
        assert node["tool_id"] == "2" and node["plugin"] == "AcmeAnalytics.Dedupe.DedupeTool"
        assert node["annotation"] == "Acme dedupe" and "UniqueFields" in node["raw_config"]
        assert node_xml.get("ToolID") == "2"
        return {"config": {"vendor": "acme"}, "behavior": "keeps the newest row per ACCT",
                "confidence": 0.5, "nonsense": "ignored"}

    dag, n = vendor_dag(tmp_path, handler)
    assert n["2"]["config"] == {"vendor": "acme"}
    assert n["2"]["type"] == "unknown"  # no type returned, so classify's answer stands
    assert n["2"]["behavior"].startswith("keeps the newest") and n["2"]["confidence"] == 0.5
    assert "nonsense" not in n["2"]
    assert n["2"]["out_anchors"] == ["Unique"]  # unknown: the anchors its connections use


def test_an_extension_calling_a_plain_tool_a_macro_is_reported_not_crashed(tmp_path):
    """There is no macro file to resolve, so invariant 4 reports it instead of the parser raising."""
    dag, n = vendor_dag(tmp_path, lambda node_xml, node: {"type": "macro"})
    assert n["2"]["type"] == "macro" and "macro_path" not in n["2"]
    assert any("macro tool 2 has no path" in e for e in invariants.check(VENDOR, dag))


def test_an_extension_under_root_is_loaded_by_run(tmp_path):
    ext = tmp_path / "scripts" / "parsers" / "ext"
    ext.mkdir(parents=True)
    (ext / "acme.py").write_text(
        "from parsers.registry import register_plugin\n"
        "register_plugin('AcmeAnalytics.Dedupe.DedupeTool', lambda x, n: {'type': 'unknown',"
        " 'in_anchors': ['Input'], 'out_anchors': ['Output'],"
        " 'behavior': 'appears to dedupe on ACCT keeping max UPDATED', 'confidence': 0.6})\n")
    repo, _ = _repo_with(tmp_path, "wf_0005", "vendor_dedupe.yxmd")
    try:
        report = parse.run(repo, "wf_0005", check=True)
    finally:
        registry.reset()
    assert report["status"] == "PARSED" and report["errors"] == []
    assert report["unknown_share"] == 0.25  # still unknown, but now explained


def test_load_extensions_reports_what_it_loaded_and_reset_forgets_it(tmp_path):
    ext = tmp_path / "ext"; ext.mkdir()
    (ext / "_private.py").write_text("raise AssertionError('modules starting with _ are skipped')\n")
    (ext / "noop.py").write_text("from parsers.registry import register_plugin\n"
                                 "register_plugin('X.Y.Z', lambda x, n: {'type': 'unknown'})\n")
    try:
        assert registry.load_extensions([ext]) == ["noop"]
        assert registry.plugin_handler("X.Y.Z") is not None
    finally:
        registry.reset()
    assert registry.plugin_handler("X.Y.Z") is None


# --- run() and the CLI ---

def _repo_with(tmp_path, wf, name):
    from lib.paths import Repo
    repo = Repo(tmp_path)
    dest = repo.wf(wf, "source"); dest.mkdir(parents=True)
    shutil.copy(SAMPLES / wf / "source" / name, dest)
    return repo, dest


def test_run_writes_a_dag_and_a_report_with_the_program_schema_keys(tmp_path):
    from lib.io import read_json
    repo, _ = _repo_with(tmp_path, "wf_0001", "sales_summary.yxmd")
    report = parse.run(repo, "wf_0001", check=True)
    dag = read_json(repo.wf("wf_0001", "parsed", "dag.json"))
    assert dag["workflow"] == "wf_0001" and len(dag["nodes"]) == 8
    assert read_json(repo.wf("wf_0001", "parsed", "parse_report.json")) == report
    assert set(report) == {"status", "errors", "node_count", "unknown_share", "scrubbed_aliases",
                           "attempt", "extension", "reason", "diagnosis"}
    assert report == {"status": "PARSED", "errors": [], "node_count": 8, "unknown_share": 0.0,
                      "scrubbed_aliases": [], "attempt": 1, "extension": None, "reason": None, "diagnosis": None}


def test_run_reports_an_invariant_violation_without_dropping_the_dag(tmp_path):
    repo, _ = _repo_with(tmp_path, "wf_0005", "vendor_dedupe.yxmd")
    report = parse.run(repo, "wf_0005", check=True)
    assert report["status"] == "INVARIANT_VIOLATION" and any("unknown" in e for e in report["errors"])
    assert report["unknown_share"] == 0.25 and repo.wf("wf_0005", "parsed", "dag.json").exists()
    assert parse.main(["wf_0005", "--check", "--root", str(tmp_path)]) == 1


def test_run_without_check_skips_the_invariants(tmp_path):
    repo, _ = _repo_with(tmp_path, "wf_0005", "vendor_dedupe.yxmd")
    report = parse.run(repo, "wf_0005", check=False)
    assert report["status"] == "PARSED" and report["errors"] == []


def test_run_keeps_a_recovered_status_and_its_attempt(tmp_path):
    from lib.io import write_json
    repo, _ = _repo_with(tmp_path, "wf_0001", "sales_summary.yxmd")
    write_json(repo.wf("wf_0001", "parsed", "parse_report.json"),
               {"status": "RECOVERED", "attempt": 2, "extension": "acme.py", "diagnosis": "parsed/parse_diagnosis.md"})
    report = parse.run(repo, "wf_0001", check=True)
    assert report["status"] == "RECOVERED" and report["attempt"] == 2 and report["extension"] == "acme.py"
    assert report["diagnosis"] == "parsed/parse_diagnosis.md"
    assert parse.main(["wf_0001", "--check", "--root", str(tmp_path)]) == 0


def test_run_reports_failed_with_the_message_when_the_xml_is_broken(tmp_path):
    from lib.paths import Repo
    repo = Repo(tmp_path)
    dest = repo.wf("wf_9999", "source"); dest.mkdir(parents=True)
    (dest / "broken.yxmd").write_text("<AlteryxDocument><Nodes>\n", encoding="utf-8")
    report = parse.run(repo, "wf_9999", check=True)
    assert report["status"] == "FAILED" and report["errors"] and "no element found" in report["errors"][0]
    # FAILED is a domain failure: spec §6.4 sends it to parser-recovery exactly like
    # INVARIANT_VIOLATION, so it must not look like a usage error to the orchestrator.
    assert parse.main(["wf_9999", "--root", str(tmp_path)]) == 1


def test_a_usage_error_exits_2_and_writes_nothing(tmp_path):
    """Exit 2 is for arguments the parser cannot act on at all, not for a workflow it could not parse."""
    with pytest.raises(SystemExit) as missing:
        parse.main(["wf_absent", "--root", str(tmp_path)])
    assert missing.value.code == 2
    assert not (tmp_path / "workflows" / "wf_absent").exists()  # no report directory conjured up
    with pytest.raises(SystemExit) as no_args:
        parse.main([])
    assert no_args.value.code == 2


def test_a_crash_outside_the_parse_exits_2(tmp_path, monkeypatch):
    """A parse that fails writes a FAILED report and exits 1; a crash that leaves no report is a 2."""
    def boom(*args, **kwargs):
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(parse, "run", boom)
    assert parse.main(["wf_0001", "--root", str(tmp_path)]) == 2


def test_run_scrubs_every_source_file_and_stays_reparseable(tmp_path):
    repo, dest = _repo_with(tmp_path, "wf_0002", "customer_orders.yxmd")
    first = parse.run(repo, "wf_0002", check=True)
    assert first["status"] == "PARSED" and first["scrubbed_aliases"] == ["dw_sales"]
    text = (dest / "customer_orders.yxmd").read_text(encoding="utf-8")
    assert "etl_user" not in text and "__EncPwd1__" not in text
    # the scrubbed file is still well-formed XML and parses to the same dag
    again = parse.run(repo, "wf_0002", check=True)
    assert again["status"] == "PARSED" and again["node_count"] == first["node_count"]
    _, n = dag_of("wf_0002", "customer_orders.yxmd")
    reparsed = {x["tool_id"]: x for x in parse.parse_file(dest / "customer_orders.yxmd")[0]["nodes"]}
    assert reparsed["10"]["config"] == n["10"]["config"]
