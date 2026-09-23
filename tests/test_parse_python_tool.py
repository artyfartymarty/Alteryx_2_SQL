"""A Python tool parses to a `python` node whose config carries the script; it is its own segment."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from parsers import plugin_map, tool_config
import segment as segmenter

XML = """<Configuration><Script>import pandas as pd
df = Alteryx.read("#1")
Alteryx.write(df, 1)</Script></Configuration>"""


def test_plugin_maps_to_python_with_one_input_and_five_outputs():
    assert plugin_map.classify("AlteryxBasePluginsGui.PythonTool.PythonTool", None) == "python"
    anchors = plugin_map.anchors_of("python")
    assert anchors["in"] == ["Input"]
    assert anchors["out"] == {"Output1": "1", "Output2": "2", "Output3": "3", "Output4": "4", "Output5": "5"}


def test_config_carries_the_script_verbatim():
    config = tool_config.parse_config("python", ET.fromstring(XML))
    assert config == {"script": 'import pandas as pd\ndf = Alteryx.read("#1")\nAlteryx.write(df, 1)'}


def test_a_python_node_is_always_its_own_segment():
    dag = {"nodes": [
        {"tool_id": "1", "type": "input", "container_id": None},
        {"tool_id": "2", "type": "filter", "container_id": None},
        {"tool_id": "3", "type": "python", "container_id": None},
        {"tool_id": "4", "type": "summarize", "container_id": None},
        {"tool_id": "5", "type": "output", "container_id": None},
    ], "edges": [
        {"src": "1", "src_anchor": "Output", "dst": "2", "dst_anchor": "Input"},
        {"src": "2", "src_anchor": "T", "dst": "3", "dst_anchor": "Input", "dst_order": 1},
        {"src": "3", "src_anchor": "1", "dst": "4", "dst_anchor": "Input"},
        {"src": "4", "src_anchor": "Output", "dst": "5", "dst_anchor": "Input"},
    ]}
    result = segmenter.segment(dag, min_tools=1)
    # segment()'s real return shape: result["segments"] maps seg id -> member tool ids (see
    # scripts/segment.py's docstring and tests/test_segment.py's test_macro_is_its_own_segment).
    assert ["3"] in result["segments"].values()
