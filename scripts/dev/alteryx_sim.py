"""Run a parsed Alteryx workflow over golden data, without Alteryx.

    python scripts/dev/alteryx_sim.py <wf_id> [--set normal|period_end|empty|edge|all] [--root .]

This is the parity **oracle**: what it produces is what every translated Snowflake procedure is
compared against, so its semantics come from `docs/reference/dag-contract.md` §4 and the program
spec §8 — never from whatever the SQL happens to do. **No Alteryx engine was available while it
was written**; every rule is an assumption, listed one per line in
`docs/reference/simulator-semantics.md`.

Test tooling only; nothing in the production path imports it. Tools it cannot reproduce
(`unknown`, `run_command`, an unresolved macro) raise `UnsupportedTool` rather than being guessed
at, which is how a workflow like `samples/wf_0005` ends up with no golden data at all.
"""
from __future__ import annotations

import argparse
import ast
import copy
import datetime as _dt
import importlib
import re
import string
import sys
from dataclasses import dataclass, field as _dataclass_field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Sequence

if __package__ in (None, ""):  # `python scripts/dev/alteryx_sim.py` puts scripts/dev on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dev import formula
from dev.formula import RowWindow, coerce, to_string
from lib.io import load_manifest, read_json, read_yaml, save_manifest
from lib.paths import Repo, add_root_arg
from lib.typed_csv import read_table, write_table
from lib.vocab import GOLDEN_SETS, NON_DATA_TYPES


class UnsupportedTool(Exception):
    """A tool the simulator refuses to reproduce: `unknown`, `run_command`, unresolved macro."""


@dataclass
class SimResult:
    """`streams` is keyed `"<tool_id>_<anchor>"`; `outputs` by the Output tool's id. Both hold Tables."""

    streams: dict[str, dict] = _dataclass_field(default_factory=dict)
    outputs: dict[str, dict] = _dataclass_field(default_factory=dict)
    warnings: list[str] = _dataclass_field(default_factory=list)


UNSUPPORTED_TYPES = frozenset({"unknown", "run_command"})
_QUESTION_RE = re.compile(r"(?i)\[%Question\.([^%\]]+)%\]")

# Sizes for a Select that changes a field's type without saying how wide the result is.
_DEFAULT_SIZES = {"Bool": 1, "Byte": 1, "Int16": 2, "Int32": 4, "Int64": 8, "Float": 4,
                  "Double": 8, "Date": 10, "Time": 8, "DateTime": 19, "FixedDecimal": 19}


class _Context:
    """What every `sim_*` function may read: the workflow's constants, the golden data it was
    given, and somewhere to leave sink results and warnings."""

    __slots__ = ("constants", "targets_before", "logical_by_tool", "warnings", "seed",
                 "outputs", "sinks")

    def __init__(self, constants: dict, targets_before: dict, logical_by_tool: dict,
                 warnings: list, seed: dict):
        self.constants = constants
        self.targets_before = targets_before
        self.logical_by_tool = logical_by_tool
        self.warnings = warnings
        self.seed = seed            # tool_id -> Table, for `input` and `macro_input`
        self.outputs = {}           # Output tool_id -> Table
        self.sinks = {}             # macro_output tool_id -> Table

    def child(self, seed: dict) -> "_Context":
        """A context for a macro's sub-DAG: same constants and warnings, its own streams."""
        return _Context(self.constants, self.targets_before, self.logical_by_tool,
                        self.warnings, seed)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


# --- table helpers -------------------------------------------------------------------------

def _table(fields: Sequence[dict], rows: list[list]) -> dict:
    return {"fields": [dict(f) for f in fields], "rows": rows}


def _empty_table() -> dict:
    return {"fields": [], "rows": []}


def _copy_table(table: dict) -> dict:
    return {"fields": [dict(f) for f in table["fields"]], "rows": [list(r) for r in table["rows"]]}


def _field(name: str, alteryx_type: str | None, size: int | None = None,
           scale: int | None = None) -> dict:
    return {"name": name, "type": alteryx_type, "size": size, "scale": scale}


def _index_of(fields: Sequence[dict], name: str) -> int | None:
    """Alteryx field names are case-insensitive; exact hits come first."""
    for index, item in enumerate(fields):
        if item["name"] == name:
            return index
    lowered = (name or "").lower()
    for index, item in enumerate(fields):
        if (item["name"] or "").lower() == lowered:
            return index
    return None


def _row_dicts(table: dict) -> list[dict]:
    names = [f["name"] for f in table["fields"]]
    return [dict(zip(names, row)) for row in table["rows"]]


def _rows_from_dicts(dicts: Sequence[dict], fields: Sequence[dict]) -> list[list]:
    return [[row.get(f["name"]) for f in fields] for row in dicts]


def _sort_key(value: Any) -> tuple:
    """NULL first, then numbers, then strings by code point. One column, so ranks stay uniform."""
    if value is None:
        return (0, 0, "")
    if isinstance(value, bool):
        return (1, int(value), "")
    if isinstance(value, (int, float, Decimal)):
        return (1, value, "")
    return (2, 0, str(value))


def _stable_sort(rows: list[list], fields: Sequence[dict], specs: Sequence[dict]) -> list[list]:
    """Multi-key stable sort: last key first, so earlier keys win (dag-contract §4 sort)."""
    ordered = list(rows)
    for spec in reversed(list(specs)):
        index = _index_of(fields, spec.get("field") or "")
        if index is None:
            continue
        descending = (spec.get("order") or "asc").lower().startswith("desc")
        ordered.sort(key=lambda row, i=index: _sort_key(row[i]), reverse=descending)
    return ordered


def _group_rows(table: dict, group_by: Sequence[str]) -> list[tuple[tuple, list[int]]]:
    """(group key, row indexes) in first-seen order; no group fields means one group of everything."""
    indexes = [_index_of(table["fields"], name) for name in group_by or []]
    groups: dict[tuple, list[int]] = {}
    for position, row in enumerate(table["rows"]):
        key = tuple(row[i] if i is not None else None for i in indexes)
        groups.setdefault(key, []).append(position)
    return list(groups.items())


# --- tool simulators -----------------------------------------------------------------------

def sim_input(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """The golden input for this tool, described by its `meta.Output` when the parser saw one."""
    table = ctx.seed.get(node["tool_id"])
    meta = (node.get("meta") or {}).get("Output") or []
    if table is None:
        ctx.warn(f"tool {node['tool_id']}: no golden input, emitting an empty table")
        return {"Output": _table(meta, [])}
    if not meta:
        return {"Output": _copy_table(table)}
    if [f["name"] for f in meta] != [f["name"] for f in table["fields"]]:
        ctx.warn(f"tool {node['tool_id']}: golden input columns differ from the tool's MetaInfo; "
                 "keeping the golden file's own schema")
        return {"Output": _copy_table(table)}
    fields = [dict(f) for f in meta]
    rows = [[coerce(value, f["type"], f["size"], f["scale"]) for value, f in zip(row, fields)]
            for row in table["rows"]]
    return {"Output": {"fields": fields, "rows": rows}}


def sim_browse(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """A Browse shows data and passes none on."""
    return {}


def sim_block_until_done(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """Ordering only: the same records leave on every anchor."""
    table = _primary(inputs_by_anchor)
    return {anchor: _copy_table(table) for anchor in ("Output1", "Output2", "Output3")}


def sim_select(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    return {"Output": _apply_select(_primary(inputs_by_anchor), node["config"], ctx, node["tool_id"])}


def _apply_select(table: dict, config: dict, ctx: _Context, tool_id: str) -> dict:
    """Reorder, drop, rename and retype; `unknown_selected` appends the rest in incoming order."""
    source_fields = table["fields"]
    chosen: list[tuple[int, dict]] = []
    listed: set[int] = set()
    for spec in config.get("fields") or []:
        index = _index_of(source_fields, spec.get("name") or "")
        if index is None:
            ctx.warn(f"tool {tool_id}: Select lists {spec.get('name')!r}, which is not on the "
                     "incoming stream")
            continue
        listed.add(index)
        if not spec.get("selected"):
            continue
        source = source_fields[index]
        new_type = spec.get("type") or source["type"]
        retyped = new_type != source["type"]
        size = spec.get("size")
        if size is None:
            size = _DEFAULT_SIZES.get(new_type) if retyped else source.get("size")
        chosen.append((index, _field(spec.get("rename") or source["name"], new_type, size,
                                     None if retyped else source.get("scale"))))
    if config.get("unknown_selected"):
        chosen.extend((index, dict(source)) for index, source in enumerate(source_fields)
                      if index not in listed)
    fields = [item for _, item in chosen]
    rows = [[coerce(row[index], item["type"], item["size"], item["scale"]) for index, item in chosen]
            for row in table["rows"]]
    return {"fields": fields, "rows": rows}


def sim_filter(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """True leaves on `T`; false **and NULL** leave on `F` (program spec §8.5)."""
    table = _primary(inputs_by_anchor)
    expression = node["config"].get("expression") or ""
    kept, dropped = [], []
    for row, values in zip(table["rows"], _row_dicts(table)):
        verdict = formula.evaluate(expression, values, constants=ctx.constants)
        (kept if formula.truth(verdict) is True else dropped).append(list(row))
    return {"T": _table(table["fields"], kept), "F": _table(table["fields"], dropped)}


def sim_formula(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """Expressions run in order: a later one sees what an earlier one just wrote."""
    table = _primary(inputs_by_anchor)
    fields = [dict(f) for f in table["fields"]]
    rows = _row_dicts(table)
    for spec in node["config"].get("formulas") or []:
        index = _index_of(fields, spec.get("field") or "")
        existing = fields[index] if index is not None else None
        new_type = spec.get("type") or (existing["type"] if existing else None)
        size = spec.get("size")
        if size is None:
            size = existing.get("size") if existing and new_type == existing["type"] \
                else _DEFAULT_SIZES.get(new_type)
        scale = existing.get("scale") if existing and new_type == existing["type"] else None
        target = _field(existing["name"] if existing else spec.get("field"), new_type, size, scale)
        if existing is None:
            fields.append(target)
        else:
            fields[index] = target
        compiled = formula.compile_expr(spec.get("expression") or "")
        for row in rows:
            row[target["name"]] = coerce(formula.evaluate(compiled, row, constants=ctx.constants),
                                         new_type, size, scale)
    return {"Output": {"fields": fields, "rows": _rows_from_dicts(rows, fields)}}


def sim_join(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """Inner equi-join on `J`, the unmatched rows on `L` and `R`. NULL keys never match."""
    left = _first(inputs_by_anchor.get("Left"))
    right = _first(inputs_by_anchor.get("Right"))
    config = node["config"]
    left_keys, right_keys = [], []
    for pair in config.get("keys") or []:
        left_index = _index_of(left["fields"], pair.get("left") or "")
        right_index = _index_of(right["fields"], pair.get("right") or "")
        if left_index is None or right_index is None:
            ctx.warn(f"tool {node['tool_id']}: join key {pair} is not on both streams")
            left_keys, right_keys = [], []
            break
        left_keys.append(left_index)
        right_keys.append(right_index)

    by_key: dict[tuple, list[int]] = {}
    if left_keys:
        for position, row in enumerate(right["rows"]):
            key = tuple(row[i] for i in right_keys)
            if None not in key:
                by_key.setdefault(key, []).append(position)

    joined, unmatched_left, matched_right = [], [], set()
    for row in left["rows"]:
        key = tuple(row[i] for i in left_keys) if left_keys else None
        partners = by_key.get(key, []) if key is not None and None not in key else []
        if not partners:
            unmatched_left.append(list(row))
            continue
        for position in partners:
            matched_right.add(position)
            joined.append(list(row) + list(right["rows"][position]))
    unmatched_right = [list(row) for position, row in enumerate(right["rows"])
                       if position not in matched_right]

    taken = {(f["name"] or "").lower() for f in left["fields"]}
    join_fields = [dict(f) for f in left["fields"]]
    for item in right["fields"]:
        name = item["name"]
        join_fields.append(dict(item, name=f"Right_{name}" if (name or "").lower() in taken else name))
    join_table = _table(join_fields, joined)
    return {"J": _apply_select(join_table, config.get("select") or {}, ctx, node["tool_id"]),
            "L": _table(left["fields"], unmatched_left),
            "R": _table(right["fields"], unmatched_right)}


def sim_union(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """Inputs stack in `dst_order`; by name, a field an input lacks arrives NULL."""
    tables = [table for anchor in sorted(inputs_by_anchor) for table in inputs_by_anchor[anchor]]
    if not tables:
        return {"Output": _empty_table()}
    if (node["config"].get("mode") or "name") == "position":
        fields = [dict(f) for f in tables[0]["fields"]]
        rows = [[row[i] if i < len(row) else None for i in range(len(fields))]
                for table in tables for row in table["rows"]]
        return {"Output": {"fields": fields, "rows": rows}}
    fields: list[dict] = []
    seen: set[str] = set()
    for table in tables:
        for item in table["fields"]:
            if (item["name"] or "").lower() not in seen:
                seen.add((item["name"] or "").lower())
                fields.append(dict(item))
    rows = []
    for table in tables:
        positions = [_index_of(table["fields"], item["name"]) for item in fields]
        rows.extend([row[i] if i is not None else None for i in positions] for row in table["rows"])
    return {"Output": {"fields": fields, "rows": rows}}


_COUNT_ACTIONS = {"count", "countnonnull", "countdistinct"}


def sim_summarize(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """One row per group, groups ascending with NULL first (dag-contract §4 summarize)."""
    table = _primary(inputs_by_anchor)
    specs = [spec for spec in node["config"].get("fields") or [] if spec.get("action")]
    group_by = [spec["field"] for spec in specs if spec["action"].lower() == "groupby"]
    fields = [_summary_field(spec, table, ctx, node["tool_id"]) for spec in specs]

    groups = _group_rows(table, group_by) if table["rows"] else []
    key_positions = [index for index, spec in enumerate(specs) if spec["action"].lower() == "groupby"]
    groups.sort(key=lambda item: tuple(_sort_key(value) for value in item[0]))

    rows = []
    for key, positions in groups:
        members = [table["rows"][position] for position in positions]
        row = []
        for index, (spec, target) in enumerate(zip(specs, fields)):
            if index in key_positions:
                value = key[key_positions.index(index)]
            else:
                source = _index_of(table["fields"], spec["field"] or "")
                values = [member[source] for member in members] if source is not None else []
                value = _aggregate(spec, values)
            row.append(coerce(value, target["type"], target["size"], target["scale"]))
        rows.append(row)
    return {"Output": {"fields": fields, "rows": rows}}


def _summary_field(spec: dict, table: dict, ctx: _Context, tool_id: str) -> dict:
    action = spec["action"].lower()
    name = spec.get("rename") or spec.get("field")
    index = _index_of(table["fields"], spec.get("field") or "")
    source = table["fields"][index] if index is not None else _field(spec.get("field"), None)
    if index is None:
        ctx.warn(f"tool {tool_id}: Summarize reads {spec.get('field')!r}, which is not on the "
                 "incoming stream")
    if action in _COUNT_ACTIONS:
        return _field(name, "Int64", 8)
    if action == "avg":
        return _field(name, "Double", 8)
    if action == "sum":
        return dict(source, name=name) if source["type"] == "FixedDecimal" else _field(name, "Double", 8)
    if action == "concat":
        return _field(name, "V_String", source.get("size"))
    return dict(source, name=name)


def _aggregate(spec: dict, values: list) -> Any:
    action = spec["action"].lower()
    present = [value for value in values if value is not None]
    if action == "count":
        return len(values)
    if action == "countnonnull":
        return len(present)
    if action == "countdistinct":
        return len(set(present))
    if action in ("sum", "avg"):
        numbers = [number for number in (formula.as_number(value) for value in present)
                   if number is not None]
        if not numbers:
            return None
        total = sum(numbers, Decimal(0))
        return total if action == "sum" else total / Decimal(len(numbers))
    if action == "min":
        return formula.extreme(values, False)
    if action == "max":
        return formula.extreme(values, True)
    if action == "first":
        return values[0] if values else None
    if action == "last":
        return values[-1] if values else None
    if action == "concat":
        separator = spec.get("separator")
        return (separator if separator is not None else ",").join(to_string(v) for v in present)
    raise UnsupportedTool(f"Summarize action {spec['action']!r} is not implemented")


def sim_sort(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    table = _primary(inputs_by_anchor)
    rows = _stable_sort(table["rows"], table["fields"], node["config"].get("fields") or [])
    return {"Output": _table(table["fields"], [list(row) for row in rows])}


def sim_unique(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """The first row per key combination in incoming order goes to `U`, the rest to `D`."""
    table = _primary(inputs_by_anchor)
    indexes = [_index_of(table["fields"], name) for name in node["config"].get("fields") or []]
    seen: set[tuple] = set()
    unique, duplicates = [], []
    for row in table["rows"]:
        key = tuple(row[i] if i is not None else None for i in indexes)
        (duplicates if key in seen else unique).append(list(row))
        seen.add(key)
    return {"U": _table(table["fields"], unique), "D": _table(table["fields"], duplicates)}


def sim_sample(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """First / last / skip / 1-in-N per group, emitted in incoming order."""
    table = _primary(inputs_by_anchor)
    config = node["config"]
    mode = (config.get("mode") or "first").lower()
    count = config.get("n")
    count = 1 if count is None else int(count)
    keep: set[int] = set()
    for _, positions in _group_rows(table, config.get("group_by") or []):
        if mode == "first":
            keep.update(positions[:count])
        elif mode == "last":
            keep.update(positions[-count:] if count else [])
        elif mode == "skip":
            keep.update(positions[count:])
        elif mode == "one_in_n":
            keep.update(positions[index] for index in range(0, len(positions), max(count, 1)))
        else:
            raise UnsupportedTool(f"tool {node['tool_id']}: Sample mode {config.get('mode')!r}")
    rows = [list(row) for position, row in enumerate(table["rows"]) if position in keep]
    return {"Output": _table(table["fields"], rows)}


def sim_record_id(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    table = _primary(inputs_by_anchor)
    config = node["config"]
    alteryx_type = config.get("type") or "Int32"
    target = _field(config.get("field") or "RecordID", alteryx_type,
                    _DEFAULT_SIZES.get(alteryx_type, 4))
    start = 1 if config.get("start") is None else int(config["start"])
    at_front = (config.get("position") or "first") == "first"
    carried = [dict(f) for f in table["fields"]]
    fields = ([target] + carried) if at_front else (carried + [target])
    rows = []
    for offset, row in enumerate(table["rows"]):
        value = coerce(start + offset, target["type"], target["size"], target["scale"])
        rows.append(([value] + list(row)) if at_front else (list(row) + [value]))
    return {"Output": {"fields": fields, "rows": rows}}


def sim_multi_row_formula(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """Rows are processed in incoming order within each group, so `[Row-1:F]` sees what was
    just computed. Rows leave in the order they arrived."""
    table = _primary(inputs_by_anchor)
    config = node["config"]
    fields = [dict(f) for f in table["fields"]]
    index = _index_of(fields, config.get("field") or "") if config.get("update_existing") else None
    alteryx_type = config.get("type") or (fields[index]["type"] if index is not None else "Double")
    size = config.get("size") or (fields[index].get("size") if index is not None else None)
    scale = fields[index].get("scale") if index is not None else None
    name = fields[index]["name"] if index is not None else (config.get("field") or "Value")
    target = _field(name, alteryx_type, size, scale)
    if index is None:
        fields.append(target)
    else:
        fields[index] = target

    rows = _row_dicts(table)
    for row in rows:
        row.setdefault(name, None)  # a new field reads as NULL until its own row is computed
    types = {item["name"]: item["type"] for item in fields}
    compiled = formula.compile_expr(config.get("expression") or "")
    unknown = config.get("unknown_rows") or "null"
    for _, positions in _group_rows(table, config.get("group_by") or []):
        members = [rows[position] for position in positions]
        for offset, member in enumerate(members):
            window = RowWindow(members, offset, unknown, types)
            member[name] = coerce(formula.evaluate(compiled, member, constants=ctx.constants,
                                                   rows=window), alteryx_type, size, scale)
    return {"Output": {"fields": fields, "rows": _rows_from_dicts(rows, fields)}}


def _sanitize_header(value: Any) -> str:
    """Cross Tab column names: everything outside `[A-Za-z0-9_]` becomes `_` (dag-contract §4)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", to_string(value) or "")


def sim_cross_tab(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """Groups ascending; header columns frozen by `meta.Output` when the parser saw one."""
    table = _primary(inputs_by_anchor)
    config = node["config"]
    group_by = []
    for name in config.get("group_by") or []:
        if _index_of(table["fields"], name) is None:
            ctx.warn(f"tool {node['tool_id']}: Cross Tab groups by {name!r}, which is not on the "
                     "incoming stream")
        else:
            group_by.append(name)
    header_index = _index_of(table["fields"], config.get("header_field") or "")
    data_index = _index_of(table["fields"], config.get("data_field") or "")
    methods = config.get("methods") or ["Sum"]
    method = methods[0]
    if len(methods) > 1:
        ctx.warn(f"tool {node['tool_id']}: Cross Tab has several methods; only {method} is applied")

    key_fields = [dict(table["fields"][_index_of(table["fields"], name)]) for name in group_by]
    meta = (node.get("meta") or {}).get("Output") or []
    if meta:
        value_fields = [dict(f) for f in meta[len(key_fields):]]
    else:
        headers = sorted({_sanitize_header(row[header_index]) for row in table["rows"]}) \
            if header_index is not None else []
        value_fields = [_field(header, *_cross_tab_type(method, table, data_index)) for header in headers]
    columns = {item["name"]: position for position, item in enumerate(value_fields)}

    groups = _group_rows(table, group_by)
    groups.sort(key=lambda item: tuple(_sort_key(value) for value in item[0]))
    rows = []
    for key, positions in groups:
        buckets: dict[str, list] = {name: [] for name in columns}
        for position in positions:
            row = table["rows"][position]
            header = _sanitize_header(row[header_index]) if header_index is not None else ""
            if header not in buckets:
                ctx.warn(f"tool {node['tool_id']}: header value {header!r} has no column; it is dropped")
                continue
            buckets[header].append(row[data_index] if data_index is not None else None)
        pivoted = list(key)
        for item in value_fields:
            value = _aggregate({"action": method, "separator": None}, buckets[item["name"]]) \
                if buckets[item["name"]] else None  # a combination with no rows is NULL, not zero
            pivoted.append(coerce(value, item["type"], item["size"], item["scale"]))
        rows.append(pivoted)
    return {"Output": {"fields": key_fields + value_fields, "rows": rows}}


def _cross_tab_type(method: str, table: dict, data_index: int | None) -> tuple:
    source = table["fields"][data_index] if data_index is not None else _field("", None)
    lowered = method.lower()
    if lowered in _COUNT_ACTIONS:
        return ("Int64", 8, None)
    if lowered in ("sum", "avg"):
        return ("Double", 8, None)
    if lowered == "concat":
        return ("V_String", source.get("size"), None)
    return (source["type"], source.get("size"), source.get("scale"))


def sim_transpose(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """Keys, then one `Name`/`Value` row per data field, in configured order. NULLs are kept."""
    table = _primary(inputs_by_anchor)
    config = node["config"]
    key_indexes = [(name, _index_of(table["fields"], name)) for name in config.get("key_fields") or []]
    data_indexes = [(name, _index_of(table["fields"], name))
                    for name in config.get("data_fields") or []]
    types = {table["fields"][i]["type"] for _, i in data_indexes if i is not None}
    value_type = types.pop() if len(types) == 1 else "V_String"
    value_size = next((table["fields"][i].get("size") for _, i in data_indexes if i is not None), None)

    fields = [dict(table["fields"][i]) for _, i in key_indexes if i is not None]
    fields.append(_field("Name", "V_String", 255))
    fields.append(_field("Value", value_type, value_size))
    rows = []
    for row in table["rows"]:
        keys = [row[i] for _, i in key_indexes if i is not None]
        for name, index in data_indexes:
            value = row[index] if index is not None else None
            rows.append(keys + [name, coerce(value, value_type, value_size)])
    return {"Output": {"fields": fields, "rows": rows}}


def sim_regex(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """`parse` appends the group columns, `replace` rewrites the field, `match` adds a flag."""
    table = _primary(inputs_by_anchor)
    config = node["config"]
    index = _index_of(table["fields"], config.get("field") or "")
    if index is None:
        ctx.warn(f"tool {node['tool_id']}: RegEx reads {config.get('field')!r}, which is not on "
                 "the incoming stream")
        return {"Output": _copy_table(table)}
    pattern = formula.compiled_pattern(config.get("expression") or "",
                                       bool(config.get("case_insensitive")))
    method = (config.get("method") or "parse").lower()
    fields = [dict(f) for f in table["fields"]]
    rows = [list(row) for row in table["rows"]]

    if method == "parse":
        targets = [_field(item.get("name"), item.get("type") or "V_String", item.get("size"))
                   for item in config.get("output_fields") or []]
        fields.extend(targets)
        for row in rows:
            found = pattern.search(to_string(row[index]) or "") if row[index] is not None else None
            groups = list(found.groups()) if found else []
            row.extend(coerce(groups[position] if position < len(groups) else None,
                              item["type"], item["size"]) for position, item in enumerate(targets))
        return {"Output": {"fields": fields, "rows": rows}}

    if method == "match":
        target = _field(config.get("match_field") or "Matched", "Bool", 1)
        fields.append(target)
        for row in rows:
            text = to_string(row[index])
            row.append(None if text is None else pattern.search(text) is not None)
        return {"Output": {"fields": fields, "rows": rows}}

    if method == "replace":
        replacement = formula.regex_replacement(config.get("replace") or "")
        copy_unmatched = bool(config.get("copy_unmatched"))
        for row in rows:
            text = to_string(row[index])
            if text is None:
                continue
            if pattern.search(text) is None:
                row[index] = text if copy_unmatched else None
            else:
                row[index] = coerce(pattern.sub(replacement, text), fields[index]["type"],
                                    fields[index]["size"])
        return {"Output": {"fields": fields, "rows": rows}}

    raise UnsupportedTool(f"tool {node['tool_id']}: RegEx method {config.get('method')!r}")


def sim_datetime(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """Text to DateTime or back; anything the format cannot read becomes NULL."""
    table = _primary(inputs_by_anchor)
    config = node["config"]
    index = _index_of(table["fields"], config.get("field") or "")
    to_text = (config.get("direction") or "to_datetime") == "to_string"
    pattern = config.get("format") or "%Y-%m-%d"
    target = _field(config.get("out_field") or "DateTime_Out",
                    "V_String" if to_text else "DateTime", 255 if to_text else 19)
    fields = [dict(f) for f in table["fields"]] + [target]
    rows = []
    for row in table["rows"]:
        source = row[index] if index is not None else None
        convert = formula.date_format if to_text else formula.date_parse
        rows.append(list(row) + [coerce(convert(source, pattern), target["type"], target["size"])])
    if index is None:
        ctx.warn(f"tool {node['tool_id']}: DateTime reads {config.get('field')!r}, which is not "
                 "on the incoming stream")
    return {"Output": {"fields": fields, "rows": rows}}


def sim_data_cleansing(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """The `Cleanse.yxmc` options, applied in the order the macro applies them."""
    table = _primary(inputs_by_anchor)
    config = node["config"]
    fields = [dict(f) for f in table["fields"]]
    rows = [list(row) for row in table["rows"]]
    for name in config.get("fields") or []:
        index = _index_of(fields, name)
        if index is None:
            ctx.warn(f"tool {node['tool_id']}: Data Cleansing lists {name!r}, which is not on the "
                     "incoming stream")
            continue
        item = fields[index]
        numeric = item["type"] in formula.NUMERIC_TYPES
        for row in rows:
            row[index] = _cleanse(row[index], config, numeric, item)
    return {"Output": {"fields": fields, "rows": rows}}


def _cleanse(value: Any, config: dict, numeric: bool, item: dict) -> Any:
    if value is None:
        if numeric and config.get("replace_null_numeric_zero"):
            return coerce(0, item["type"], item["size"], item["scale"])
        if not numeric and config.get("replace_null_strings_blank"):
            value = ""
        else:
            return None
    if numeric:
        return value
    text = to_string(value)
    if config.get("remove_tabs_linebreaks_dupspaces"):
        text = re.sub(r"[\t\r\n]", " ", text)
        text = re.sub(r" {2,}", " ", text)
    if config.get("remove_all_whitespace"):
        text = "".join(character for character in text if not character.isspace())
    if config.get("trim_whitespace"):
        text = text.strip()
    if config.get("remove_letters"):
        text = "".join(character for character in text if not character.isalpha())
    if config.get("remove_numbers"):
        text = "".join(character for character in text if not character.isdigit())
    if config.get("remove_punctuation"):
        text = "".join(character for character in text if character not in string.punctuation)
    case = (config.get("modify_case") or "").lower()
    if case == "upper":
        text = text.upper()
    elif case == "lower":
        text = text.lower()
    elif case == "title":
        text = re.sub(r"\S+", lambda word: word.group()[0].upper() + word.group()[1:].lower(), text)
    return coerce(text, item["type"], item["size"], item["scale"])


def sim_macro_input(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    return {"Output": _copy_table(ctx.seed.get(node["tool_id"]) or _empty_table())}


def sim_macro_output(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    ctx.sinks[node["tool_id"]] = _primary(inputs_by_anchor)
    return {}


def sim_macro(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """Substitute `[%Question.X%]`, run the sub-DAG, return its `Output<id>` streams."""
    sub_dag = _substitute_questions(node)
    seed = {}
    for sub_node in sub_dag["nodes"]:
        if sub_node["type"] == "macro_input":
            tables = inputs_by_anchor.get(f"Input{sub_node['tool_id']}") or []
            seed[sub_node["tool_id"]] = tables[0] if tables else _empty_table()
    inner = ctx.child(seed)
    _execute(sub_dag, inner)
    for tool_id in inner.outputs:
        ctx.warn(f"tool {node['tool_id']}: Output tool {tool_id} inside the macro writes no golden file")
    return {f"Output{tool_id}": table for tool_id, table in inner.sinks.items()}


def _substitute_questions(node: dict) -> dict:
    """Macro question values replace `[%Question.<name>%]` textually, as the dag contract says."""
    values = {name: value for name, value in ((node.get("config") or {}).get("values") or {}).items()}
    for item in node.get("interface") or []:
        values.setdefault(item.get("name"), item.get("default"))
    lookup = {str(name).strip().lower(): ("" if value is None else str(value))
              for name, value in values.items()}

    def replace(match: re.Match) -> str:
        name = match.group(1).strip().lower()
        if name not in lookup:
            raise UnsupportedTool(f"tool {node['tool_id']}: macro question "
                                  f"{match.group(1)!r} has neither a value nor a default")
        return lookup[name]

    sub_dag = copy.deepcopy(node["sub_dag"])
    for sub_node in sub_dag["nodes"]:
        sub_node["config"] = _replace_in(sub_node.get("config"), replace)
        if sub_node.get("sub_dag"):
            sub_node["sub_dag"] = _replace_in(sub_node["sub_dag"], replace)
    return sub_dag


def _replace_in(value: Any, replace: Callable[[re.Match], str]) -> Any:
    if isinstance(value, str):
        return _QUESTION_RE.sub(replace, value)
    if isinstance(value, dict):
        return {key: _replace_in(item, replace) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_in(item, replace) for item in value]
    return value


PYTHON_TOOL_ALLOWED_MODULES = frozenset({"pandas", "numpy", "re", "math", "datetime", "decimal"})
_PYTHON_TOOL_BUILTINS = {name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
                         for name in ("abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "isinstance",
                                      "len", "list", "max", "min", "range", "round", "set", "sorted", "str", "sum",
                                      "tuple", "zip", "reversed", "ValueError", "KeyError", "TypeError", "Exception")}


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    """`__import__`'s contract, restricted to PYTHON_TOOL_ALLOWED_MODULES. A plain dotted
    `import a.b` binds `a`, not `a.b` -- only the `from a.b import c` form (which passes a
    `fromlist`) evaluates to the submodule. Returning the submodule either way would leave a
    script that wrote `import pandas.io` with the name `pandas` bound to `pandas.io`
    (final fix wave M7)."""
    root = name.split(".")[0]
    if level != 0 or root not in PYTHON_TOOL_ALLOWED_MODULES:
        raise UnsupportedTool(f"python tool: import of {name!r} is not allowed (allowed: "
                              f"{', '.join(sorted(PYTHON_TOOL_ALLOWED_MODULES))})")
    module = importlib.import_module(name)
    return module if fromlist else importlib.import_module(root)


def _check_python_tool_script(script: str) -> ast.Module:
    """Static pre-check, run before a single statement of the script executes. This is an
    accident guard, not a security boundary (docs/reference/simulator-semantics.md §7.1): it
    refuses any dunder name or attribute access -- the gadgets that recover the real, unrestricted
    `__import__`/`open` through an imported module's own `__builtins__` dict (`pd.__builtins__`),
    or reach the whole class graph (`().__class__.__base__.__subclasses__()`) -- and refuses any
    `import`/`from … import` outside `PYTHON_TOOL_ALLOWED_MODULES` at parse time, on top of the
    guarded `__import__` that already refuses it at run time.
    """
    try:
        tree = ast.parse(script, "<python tool>", "exec")
    except SyntaxError as exc:
        raise UnsupportedTool(f"python tool script failed: SyntaxError: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise UnsupportedTool(f"python tool: attribute access {node.attr!r} is not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise UnsupportedTool(f"python tool: name {node.id!r} is not allowed")
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in PYTHON_TOOL_ALLOWED_MODULES:
                    raise UnsupportedTool(f"python tool: import of {alias.name!r} is not allowed "
                                          f"(allowed: {', '.join(sorted(PYTHON_TOOL_ALLOWED_MODULES))})")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level != 0 or root not in PYTHON_TOOL_ALLOWED_MODULES:
                raise UnsupportedTool(f"python tool: import of {node.module!r} is not allowed "
                                      f"(allowed: {', '.join(sorted(PYTHON_TOOL_ALLOWED_MODULES))})")
    return tree


def _pandas_to_table(pdf) -> dict:
    """DataFrame -> Table, by pandas' own type predicates rather than a str(dtype) prefix match
    (a str(dtype) match misses pandas' nullable dtypes, e.g. "Int64"/"boolean", entirely):
    bool (incl. nullable "boolean") -> Bool, int (incl. nullable "Int64") -> Int64,
    float -> Double, datetime64 -> DateTime, everything else -> V_WString. `pd.isna(v)` decides
    NULL uniformly in every branch, so a NULL of any kind (NaN, NaT, None, `pd.NA`) becomes NULL
    rather than surfacing as the literal "<NA>" string. Column order is the frame's."""
    import pandas as pd
    from pandas.api.types import is_bool_dtype, is_datetime64_any_dtype, is_float_dtype, is_integer_dtype
    fields, columns = [], []
    for name in pdf.columns:
        series = pdf[name]
        if is_bool_dtype(series):  # checked before is_integer_dtype: bool is not an int type,
            fields.append(_field(str(name), "Bool", 1))  # but be explicit rather than rely on that
            columns.append([None if pd.isna(v) else bool(v) for v in series])
        elif is_integer_dtype(series):
            fields.append(_field(str(name), "Int64", 8))
            columns.append([None if pd.isna(v) else int(v) for v in series])
        elif is_float_dtype(series):
            fields.append(_field(str(name), "Double", 8))
            columns.append([None if pd.isna(v) else float(v) for v in series])
        elif is_datetime64_any_dtype(series):
            fields.append(_field(str(name), "DateTime", 19))
            columns.append([None if pd.isna(v) else v.strftime("%Y-%m-%d %H:%M:%S") for v in series])
        else:
            fields.append(_field(str(name), "V_WString", 254))
            columns.append([None if pd.isna(v) else str(v) for v in series])
    return _table(fields, [list(row) for row in zip(*columns)] if columns else [])


def _table_to_pandas(table: dict):
    import pandas as pd
    names = [f["name"] for f in table["fields"]]
    pdf = pd.DataFrame([list(r) for r in table["rows"]], columns=names)
    for f in table["fields"]:
        if f["type"] in formula.FLOAT_TYPES:
            pdf[f["name"]] = pd.to_numeric(pdf[f["name"]], errors="coerce").astype("float64")
        elif f["type"] == "Bool":
            pdf[f["name"]] = pdf[f["name"]].astype("boolean")
    return pdf


def run_python_tool(script: str, inputs: list[dict]) -> dict[int, dict]:
    """Run one Python tool script in the sandbox. `inputs` are the tables on the tool's input
    connections in `#1`, `#2`, … order. Returns the tables written to anchors 1..5.

    The sandbox (guarded builtins/import plus `_check_python_tool_script`'s AST pre-check) is an
    accident guard, not a security boundary: an allowed library can still reach the filesystem and
    load native code (for example `DataFrame.to_csv`, or a submodule the allow-list admits by its
    top-level package alone, such as `numpy.ctypeslib` or `pandas.io.common`); the simulator runs
    only this repository's own committed sample scripts and must never be pointed at an untrusted
    workflow's Python tool.
    """
    written: dict[int, dict] = {}
    tree = _check_python_tool_script(script)  # refuse before a single statement runs

    class Alteryx:  # the shim the real tool exposes
        @staticmethod
        def read(name: str):
            index = int(str(name).lstrip("#")) - 1
            if not 0 <= index < len(inputs):
                raise UnsupportedTool(f"python tool: Alteryx.read({name!r}) but only {len(inputs)} input(s) are connected")
            return _table_to_pandas(inputs[index])

        @staticmethod
        def write(pdf, anchor: int) -> None:
            """A later write to the same anchor replaces an earlier one; only the anchors the
            script actually wrote end up in the result."""
            if not 1 <= int(anchor) <= 5:
                raise UnsupportedTool(f"python tool: Alteryx.write(..., {anchor}) is not an output anchor 1..5")
            written[int(anchor)] = _pandas_to_table(pdf)

    namespace = {"__builtins__": {**_PYTHON_TOOL_BUILTINS, "__import__": _guarded_import}, "Alteryx": Alteryx}
    try:
        exec(compile(tree, "<python tool>", "exec"), namespace)  # noqa: S102 -- the sandbox above
    except UnsupportedTool:
        raise
    except Exception as exc:  # the script's own failure is a simulator refusal, not a crash
        raise UnsupportedTool(f"python tool script failed: {type(exc).__name__}: {exc}") from exc
    return written


def sim_python(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """The Python tool: the embedded script runs in a sandbox with the Alteryx.read/write shim."""
    inputs = list(inputs_by_anchor.get("Input") or [])
    try:
        written = run_python_tool(node["config"].get("script") or "", inputs)
    except UnsupportedTool as exc:
        raise UnsupportedTool(f"tool {node['tool_id']}: {exc}") from exc
    return {str(anchor): table for anchor, table in sorted(written.items())}


def sim_output(node: dict, inputs_by_anchor: dict, ctx: _Context) -> dict:
    """A file target is the incoming table; a database target is the table's state afterwards."""
    table = _primary(inputs_by_anchor)
    config = node["config"]
    logical = ctx.logical_by_tool.get(node["tool_id"])
    before = ctx.targets_before.get(logical) if logical else None
    if config.get("format") == "db" and (before is not None or config.get("pre_sql")
                                         or config.get("post_sql")):
        ctx.outputs[node["tool_id"]] = _write_to_database(node, table, before, ctx)
    else:
        ctx.outputs[node["tool_id"]] = _copy_table(table)
    return {}


SIMULATORS: dict[str, Callable[[dict, dict, _Context], dict]] = {
    "input": sim_input, "output": sim_output, "browse": sim_browse,
    "block_until_done": sim_block_until_done, "select": sim_select, "filter": sim_filter,
    "formula": sim_formula, "join": sim_join, "union": sim_union, "summarize": sim_summarize,
    "sort": sim_sort, "unique": sim_unique, "sample": sim_sample, "record_id": sim_record_id,
    "multi_row_formula": sim_multi_row_formula, "cross_tab": sim_cross_tab,
    "transpose": sim_transpose, "regex": sim_regex, "datetime": sim_datetime,
    "data_cleansing": sim_data_cleansing, "macro": sim_macro, "macro_input": sim_macro_input,
    "macro_output": sim_macro_output, "python": sim_python,
}


# --- the database a db Output writes into --------------------------------------------------

def _duckdb_type(item: dict) -> str:
    alteryx_type = item["type"]
    if alteryx_type in formula.INT_TYPES:
        return "BIGINT"
    if alteryx_type == "FixedDecimal":
        return f"DECIMAL({item.get('size') or 19},{item.get('scale') or 0})"
    if alteryx_type in formula.FLOAT_TYPES:
        return "DOUBLE"
    if alteryx_type == "Bool":
        return "BOOLEAN"
    if alteryx_type == "Date":
        return "DATE"
    if alteryx_type == "Time":
        return "TIME"
    if alteryx_type == "DateTime":
        return "TIMESTAMP"
    return "VARCHAR"


def _to_duckdb(value: Any, item: dict) -> Any:
    value = coerce(value, item["type"], item["size"], item["scale"])
    if value is None or not isinstance(value, str):
        return value
    if item["type"] == "Date":
        return _dt.date.fromisoformat(value)
    if item["type"] == "DateTime":
        return _dt.datetime.fromisoformat(value)
    if item["type"] == "Time":
        return _dt.time.fromisoformat(value)
    return value


def _quote(name: str) -> str:
    return ".".join(f'"{part}"' for part in name.split("."))


def _write_to_database(node: dict, incoming: dict, before: dict | None, ctx: _Context) -> dict:
    """Load the target's prior state, run PreSQL, apply the write mode by column name, run PostSQL.

    The result is the table's final state, ordered by its update keys or by every column, which is
    what `golden/outputs/<tool_id>.csv` records.
    """
    import duckdb  # a test-only dependency, imported where it is used

    config = node["config"]
    mode = config.get("write_mode") or "overwrite"
    table_name = config.get("table") or "TARGET"
    keys = list(config.get("keys") or [])
    fields = [dict(f) for f in (incoming["fields"] if mode == "overwrite" or before is None
                                else before["fields"])]
    prior = [] if mode == "overwrite" or before is None else before["rows"]

    connection = duckdb.connect()
    try:
        schema = table_name.rpartition(".")[0]
        if schema:
            connection.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote(schema)}")
        columns = ", ".join(f"{_quote(f['name'])} {_duckdb_type(f)}" for f in fields)
        connection.execute(f"CREATE TABLE {_quote(table_name)} ({columns})")
        _insert(connection, table_name, fields, prior)
        _run_sql(connection, config.get("pre_sql"), ctx, node)

        incoming_fields = [dict(f) for f in incoming["fields"]]
        staged = ", ".join(f"{_quote(f['name'])} {_duckdb_type(f)}" for f in incoming_fields)
        connection.execute(f"CREATE TEMP TABLE incoming ({staged})")
        _insert(connection, "incoming", incoming_fields, incoming["rows"])

        shared = [f["name"] for f in fields
                  if _index_of(incoming_fields, f["name"]) is not None]
        for item in incoming_fields:
            if _index_of(fields, item["name"]) is None:
                ctx.warn(f"tool {node['tool_id']}: column {item['name']!r} is not on the target "
                         f"table {table_name} and is not written")
        _apply_write_mode(connection, table_name, mode, shared, keys, ctx, node)
        _run_sql(connection, config.get("post_sql"), ctx, node)

        selected = ", ".join(_quote(f["name"]) for f in fields)
        rows = connection.execute(f"SELECT {selected} FROM {_quote(table_name)}").fetchall()
    finally:
        connection.close()
    # Ordered here rather than in SQL: DuckDB's ORDER BY canonicalises -0.0 to 0.0, and this way
    # a target's rows land in the same NULL-first order the Sort tool produces.
    order = [{"field": name, "order": "asc"} for name in (keys or [f["name"] for f in fields])]
    final = [[coerce(value, f["type"], f["size"], f["scale"]) for value, f in zip(row, fields)]
             for row in rows]
    return {"fields": fields, "rows": _stable_sort(final, fields, order)}


def _insert(connection, table_name: str, fields: Sequence[dict], rows: Sequence[Sequence]) -> None:
    if not rows:
        return
    placeholders = ", ".join("?" for _ in fields)
    connection.executemany(
        f"INSERT INTO {_quote(table_name)} VALUES ({placeholders})",
        [[_to_duckdb(value, item) for value, item in zip(row, fields)] for row in rows])


def _apply_write_mode(connection, table_name: str, mode: str, shared: Sequence[str],
                      keys: Sequence[str], ctx: _Context, node: dict) -> None:
    target, columns = _quote(table_name), ", ".join(_quote(name) for name in shared)
    if not shared:
        ctx.warn(f"tool {node['tool_id']}: no incoming column matches {table_name} by name, "
                 "so the target is left as it was")
        return
    if mode == "truncate_append":
        connection.execute(f"DELETE FROM {target}")
    if mode in ("overwrite", "append", "truncate_append"):
        connection.execute(f"INSERT INTO {target} ({columns}) SELECT {columns} FROM incoming")
        return
    if mode != "update_insert":
        raise UnsupportedTool(f"tool {node['tool_id']}: write mode {mode!r} is not implemented")
    if not keys:
        ctx.warn(f"tool {node['tool_id']}: update_insert without keys behaves as append")
        connection.execute(f"INSERT INTO {target} ({columns}) SELECT {columns} FROM incoming")
        return
    # NULL keys never match, exactly as the `=` a translated MERGE would use.
    match = " AND ".join(f"{target}.{_quote(key)} = incoming.{_quote(key)}" for key in keys)
    updates = ", ".join(f"{_quote(name)} = incoming.{_quote(name)}"
                        for name in shared if name not in keys)
    if updates:
        connection.execute(f"UPDATE {target} SET {updates} FROM incoming WHERE {match}")
    connection.execute(
        f"INSERT INTO {target} ({columns}) SELECT {columns} FROM incoming "
        f"WHERE NOT EXISTS (SELECT 1 FROM {target} WHERE {match})")


def _run_sql(connection, sql: str | None, ctx: _Context, node: dict) -> None:
    """PreSQL/PostSQL is written for the source database; sqlglot moves it to DuckDB."""
    if not sql or not sql.strip():
        return
    import sqlglot  # a test-only dependency, imported where it is used

    try:
        statements = sqlglot.transpile(sql, read="tsql", write="duckdb")
    except Exception as error:  # any sqlglot failure is the same kind of problem: untranslatable
        raise UnsupportedTool(
            f"tool {node['tool_id']}: cannot translate SQL {sql!r}: {error}") from error
    for statement in statements:
        connection.execute(statement)


# --- execution -----------------------------------------------------------------------------

def _first(tables: Sequence[dict] | None) -> dict:
    return tables[0] if tables else _empty_table()


def _primary(inputs_by_anchor: dict) -> dict:
    """The table on `Input`, or on whatever single anchor the tool actually has."""
    if "Input" in inputs_by_anchor:
        return _first(inputs_by_anchor["Input"])
    for anchor in sorted(inputs_by_anchor):
        return _first(inputs_by_anchor[anchor])
    return _empty_table()


def _refuse_unsupported(dag: dict, inside: str = "") -> None:
    for node in dag.get("nodes") or []:
        tool_id, tool_type = node["tool_id"], node["type"]
        where = f"tool {tool_id}{inside}"
        if tool_type in UNSUPPORTED_TYPES:
            raise UnsupportedTool(f"{where} has type {tool_type!r}: the simulator will not guess "
                                  "what it does")
        if tool_type == "macro":
            if node.get("unresolved") or not node.get("sub_dag"):
                raise UnsupportedTool(f"{where} is an unresolved macro "
                                      f"({node.get('macro_path')}): its sub-workflow was not found")
            _refuse_unsupported(node["sub_dag"], f" inside macro {tool_id}")
        elif tool_type not in NON_DATA_TYPES and tool_type not in SIMULATORS:
            raise UnsupportedTool(f"{where}: the simulator has no rule for a {tool_type} tool")


def _order(nodes: Sequence[dict], edges: Sequence[dict]) -> list[dict]:
    """Topological order over the data nodes; a cycle is a corrupt dag, not a workflow."""
    by_id = {node["tool_id"]: node for node in nodes}
    waiting = {tool_id: 0 for tool_id in by_id}
    downstream: dict[str, list[str]] = {tool_id: [] for tool_id in by_id}
    for edge in edges:
        source, destination = edge.get("src"), edge.get("dst")
        if source in by_id and destination in by_id:
            downstream[source].append(destination)
            waiting[destination] += 1
    ready = [tool_id for tool_id in by_id if waiting[tool_id] == 0]
    ordered = []
    while ready:
        tool_id = ready.pop(0)
        ordered.append(by_id[tool_id])
        for next_id in downstream[tool_id]:
            waiting[next_id] -= 1
            if waiting[next_id] == 0:
                ready.append(next_id)
    if len(ordered) != len(by_id):
        raise ValueError("the dag has a cycle: " +
                         ", ".join(sorted(tool_id for tool_id in by_id if waiting[tool_id] > 0)))
    return ordered


def _gather(node: dict, edges: Sequence[dict], streams: dict) -> dict[str, list[dict]]:
    """Incoming tables per destination anchor, ordered by `dst_order` (Union's `#1`, `#2`, …)."""
    grouped: dict[str, list[tuple]] = {}
    for position, edge in enumerate(edges):
        if edge.get("dst") != node["tool_id"]:
            continue
        table = streams.get(f"{edge.get('src')}_{edge.get('src_anchor')}")
        if table is None:
            continue
        grouped.setdefault(edge.get("dst_anchor") or "Input", []).append(
            (edge.get("dst_order") or 1, position, table))
    return {anchor: [table for _, _, table in sorted(items, key=lambda item: item[:2])]
            for anchor, items in grouped.items()}


def _execute(dag: dict, ctx: _Context) -> dict[str, dict]:
    nodes = [node for node in dag.get("nodes") or [] if node["type"] not in NON_DATA_TYPES]
    edges = dag.get("edges") or []
    streams: dict[str, dict] = {}
    for node in _order(nodes, edges):
        produced = SIMULATORS[node["type"]](node, _gather(node, edges, streams), ctx)
        for anchor, table in produced.items():
            streams[f"{node['tool_id']}_{anchor}"] = table
    return streams


def simulate(dag: dict, inputs: dict[str, dict], *, targets_before: dict[str, dict] | None = None,
             logical_by_tool: dict[str, str] | None = None) -> SimResult:
    """Run every tool of `dag` over `inputs` (keyed by Input tool id).

    `targets_before` is keyed by logical table name and gives a database Output tool the state its
    target was in before the run; `logical_by_tool` says which Output tool writes which of them.
    """
    _refuse_unsupported(dag)
    ctx = _Context(dag.get("constants") or {}, targets_before or {}, logical_by_tool or {},
                   [], dict(inputs))
    streams = _execute(dag, ctx)
    return SimResult(streams=streams, outputs=ctx.outputs, warnings=ctx.warnings)


# --- golden data for one workflow ----------------------------------------------------------

def _segments(repo: Repo, wf_id: str) -> list[tuple[str, dict]]:
    root = repo.wf(wf_id, "segments")
    if not root.is_dir():
        return []
    return [(path.name, read_json(path / "dag.json")) for path in sorted(root.iterdir())
            if (path / "dag.json").is_file()]


def _outbound_streams(segment_dag: dict) -> list[str]:
    """Every stream that leaves a segment, except one an Output tool inside it already captures."""
    kinds = {node["tool_id"]: node["type"] for node in segment_dag.get("nodes") or []}
    streams: list[str] = []
    for edge in segment_dag.get("outbound") or []:
        if kinds.get(edge.get("dst")) == "output":
            continue  # golden/outputs/<tool_id>.csv holds this one
        stream = f"{edge.get('src')}_{edge.get('src_anchor')}"
        if stream not in streams:
            streams.append(stream)
    return streams


def _read_set(directory: Path) -> dict[str, dict]:
    if not directory.is_dir():
        return {}
    return {path.stem: read_table(path) for path in sorted(directory.glob("*.csv"))}


def run(repo: Repo, wf_id: str, golden_sets: Sequence[str],
        logical_by_tool: dict[str, str]) -> list[str]:
    """Simulate every golden set of one workflow and write contract C2's golden files.

    Returns the sets it produced, which is also what it records in `manifest.golden_sets`. A
    workflow with a tool the simulator refuses produces nothing at all and returns `[]`.
    """
    dag = read_json(repo.wf(wf_id, "parsed", "dag.json"))
    segments = _segments(repo, wf_id)
    results: list[tuple[str, SimResult]] = []
    try:
        for name in golden_sets:
            inputs = _read_set(repo.wf(wf_id, "golden", "inputs", name))
            if not inputs:
                continue
            results.append((name, simulate(
                dag, inputs,
                targets_before=_read_set(repo.wf(wf_id, "golden", "targets_before", name)),
                logical_by_tool=logical_by_tool)))
    except UnsupportedTool as error:
        print(f"{wf_id}: {error}")
        results = []

    for name, result in results:
        for segment, segment_dag in segments:
            for stream in _outbound_streams(segment_dag):
                table = result.streams.get(stream)
                if table is None:
                    print(f"{wf_id}/{segment}: no stream {stream} to write")
                    continue
                write_table(repo.wf(wf_id, "golden", "intermediates", segment, name,
                                    f"{stream}.csv"), table)
        for tool_id, table in result.outputs.items():
            write_table(repo.wf(wf_id, "golden", "outputs", name, f"{tool_id}.csv"), table)
        for warning in result.warnings:
            print(f"{wf_id}/{name}: {warning}")

    produced = [name for name, _ in results]
    manifest = load_manifest(repo, wf_id)
    manifest["golden_sets"] = produced
    save_manifest(repo, manifest)
    return produced


def read_logical_by_tool(repo: Repo, wf_id: str) -> dict[str, str]:
    """Which logical table each Input/Output tool touches: `intake/mappings.yaml` (C6) if intake
    has run, else the sample's own `sample.json`."""
    mappings_path = repo.wf(wf_id, "intake", "mappings.yaml")
    if mappings_path.is_file():
        mappings = read_yaml(mappings_path) or {}
        found = {}
        for section in ("sources", "outputs"):
            for entry in (mappings.get(section) or {}).values():
                name = (entry or {}).get("logical")
                for tool_id in (entry or {}).get("tool_ids") or []:
                    if name:
                        found[str(tool_id)] = name
        return found
    sample = repo.root / "samples" / wf_id / "sample.json"
    if sample.is_file():
        return {str(tool_id): name
                for tool_id, name in (read_json(sample).get("logical") or {}).items()}
    return {}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0003")
    parser.add_argument("--set", dest="golden_set", default="all", choices=[*GOLDEN_SETS, "all"],
                        help="which golden set to simulate (default: all)")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    repo = Repo(args.root)
    wanted = list(GOLDEN_SETS) if args.golden_set == "all" else [args.golden_set]
    produced = run(repo, args.wf_id, wanted, read_logical_by_tool(repo, args.wf_id))
    if not produced:
        print(f"{args.wf_id}: no golden sets produced")
        return 1
    print(f"{args.wf_id}: golden sets {', '.join(produced)}")
    return 0


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
