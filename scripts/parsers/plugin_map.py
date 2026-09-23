"""Plugin → canonical tool type and the anchor names per type (dag-contract §2).

The table is the contract's, verbatim and in its order. A plugin that is not in it is `unknown`:
the parser never guesses a tool's semantics, it keeps the node and lets an extension or the
analyzer explain it.
"""
from __future__ import annotations

# GuiSettings Plugin → the type every later stage keys on.
PLUGIN_TYPES: dict[str, str] = {
    "AlteryxBasePluginsGui.DbFileInput.DbFileInput": "input",
    "AlteryxBasePluginsGui.DbFileOutput.DbFileOutput": "output",
    "AlteryxBasePluginsGui.AlteryxSelect.AlteryxSelect": "select",
    "AlteryxBasePluginsGui.Filter.Filter": "filter",
    "AlteryxBasePluginsGui.Formula.Formula": "formula",
    "AlteryxBasePluginsGui.Join.Join": "join",
    "AlteryxBasePluginsGui.Union.Union": "union",
    "AlteryxSpatialPluginsGui.Summarize.Summarize": "summarize",
    "AlteryxBasePluginsGui.Sort.Sort": "sort",
    "AlteryxBasePluginsGui.Unique.Unique": "unique",
    "AlteryxBasePluginsGui.Sample.Sample": "sample",
    "AlteryxBasePluginsGui.RecordID.RecordID": "record_id",
    "AlteryxBasePluginsGui.MultiRowFormula.MultiRowFormula": "multi_row_formula",
    "AlteryxBasePluginsGui.CrossTab.CrossTab": "cross_tab",
    "AlteryxBasePluginsGui.Transpose.Transpose": "transpose",
    "AlteryxBasePluginsGui.RegEx.RegEx": "regex",
    "AlteryxBasePluginsGui.DateTime.DateTime": "datetime",
    "AlteryxBasePluginsGui.AppendFields.AppendFields": "append_fields",
    "AlteryxBasePluginsGui.BlockUntilDone.BlockUntilDone": "block_until_done",
    "AlteryxBasePluginsGui.BrowseV2.BrowseV2": "browse",
    "AlteryxBasePluginsGui.RunCommand.RunCommand": "run_command",
    # verify against your Alteryx version: the Python tool's plugin id is taken from this repo's samples
    "AlteryxBasePluginsGui.PythonTool.PythonTool": "python",
    "AlteryxBasePluginsGui.MacroInput.MacroInput": "macro_input",
    "AlteryxBasePluginsGui.MacroOutput.MacroOutput": "macro_output",
    "AlteryxBasePluginsGui.Action.Action": "action",
    "AlteryxGuiToolkit.ToolContainer.ToolContainer": "container",
    "AlteryxGuiToolkit.TextBox.TextBox": "comment",
}

# Every question tool (TextBox, DropDown, NumericUpDown, …) is one `interface` type.
QUESTION_PREFIX = "AlteryxGuiToolkit.Questions."

# Macros Alteryx ships that are really tools. Keys are lower-cased file names.
BUILTIN_MACROS: dict[str, str] = {"cleanse.yxmc": "data_cleansing"}

# type -> {"in": [canonical in anchors], "out": {XML anchor name: canonical name}}.
# `macro` and `unknown` are filled in by the parser: a macro's anchors are named after its own
# macro_input/macro_output tools, and an unknown tool's are whatever its connections use.
ANCHORS: dict[str, dict] = {
    "input": {"in": [], "out": {"Output": "Output"}},
    "output": {"in": ["Input"], "out": {}},
    "select": {"in": ["Input"], "out": {"Output": "Output"}},
    "filter": {"in": ["Input"], "out": {"True": "T", "False": "F"}},
    "formula": {"in": ["Input"], "out": {"Output": "Output"}},
    "join": {"in": ["Left", "Right"], "out": {"Left": "L", "Join": "J", "Right": "R"}},
    "union": {"in": ["Input"], "out": {"Output": "Output"}},
    "summarize": {"in": ["Input"], "out": {"Output": "Output"}},
    "sort": {"in": ["Input"], "out": {"Output": "Output"}},
    "unique": {"in": ["Input"], "out": {"Unique": "U", "Duplicates": "D"}},
    "sample": {"in": ["Input"], "out": {"Output": "Output"}},
    "record_id": {"in": ["Input"], "out": {"Output": "Output"}},
    "multi_row_formula": {"in": ["Input"], "out": {"Output": "Output"}},
    "cross_tab": {"in": ["Input"], "out": {"Output": "Output"}},
    "transpose": {"in": ["Input"], "out": {"Output": "Output"}},
    "regex": {"in": ["Input"], "out": {"Output": "Output"}},
    "datetime": {"in": ["Input"], "out": {"Output": "Output"}},
    "append_fields": {"in": ["Targets", "Source"], "out": {"Output": "Output"}},
    "block_until_done": {"in": ["Input"],
                         "out": {"Output1": "Output1", "Output2": "Output2", "Output3": "Output3"}},
    "browse": {"in": ["Input"], "out": {}},
    "run_command": {"in": ["Input"], "out": {"Output": "Output"}},
    "python": {"in": ["Input"],
              "out": {"Output1": "1", "Output2": "2", "Output3": "3", "Output4": "4", "Output5": "5"}},
    "macro_input": {"in": [], "out": {"Output": "Output"}},
    "macro_output": {"in": ["Input"], "out": {}},
    "data_cleansing": {"in": ["Input"], "out": {"Output": "Output"}},
    "action": {"in": [], "out": {}},
    "interface": {"in": [], "out": {}},
    "container": {"in": [], "out": {}},
    "comment": {"in": [], "out": {}},
    "macro": {"in": [], "out": {}},
    "unknown": {"in": [], "out": {}},
}


def classify(plugin: str | None, macro: str | None) -> str:
    """The canonical type for a node's `GuiSettings Plugin` and `EngineSettings Macro`.

    Data Cleansing and macro nodes carry no Plugin attribute at all, so they are classified by the
    macro they run: `Cleanse.yxmc` (any case, any directory) is the Data Cleansing tool, and every
    other macro is a `macro` node the parser resolves and parses recursively.
    """
    if plugin:
        if plugin in PLUGIN_TYPES:
            return PLUGIN_TYPES[plugin]
        if plugin.startswith(QUESTION_PREFIX):
            return "interface"
        return "unknown"
    if macro:
        base = macro.replace("\\", "/").rsplit("/", 1)[-1].lower()
        return BUILTIN_MACROS.get(base, "macro")
    return "unknown"


def anchors_of(tool_type: str) -> dict:
    """ANCHORS[tool_type], or the empty pair for a type the map does not know."""
    return ANCHORS.get(tool_type, {"in": [], "out": {}})


def canonical_out_anchor(tool_type: str, xml_name: str) -> str:
    """`Join` on a join → `J`. An anchor the type does not declare is kept as written, so
    invariant 2 can report it instead of the parser silently inventing a name."""
    return anchors_of(tool_type)["out"].get(xml_name, xml_name)


def out_anchor_names(tool_type: str) -> list[str]:
    """The canonical out anchors of a type, in the contract's order."""
    return list(anchors_of(tool_type)["out"].values())


# Which output target a tool type needs (cookbook §8.2, made machine-readable). Anything not
# listed and known is `sql`; a type the map does not know stays `unknown`.
TARGET_CLASS: dict[str, str] = {
    "python": "snowpark",
    "r": "manual", "run_command": "manual", "download": "manual", "email": "manual",
    "render": "manual", "spatial": "manual",
}


def node_class(tool_type: str) -> str:
    if tool_type in TARGET_CLASS:
        return TARGET_CLASS[tool_type]
    if tool_type in ANCHORS and tool_type != "unknown":
        return "sql"
    return "unknown"
