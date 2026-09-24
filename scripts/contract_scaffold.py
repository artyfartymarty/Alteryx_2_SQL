"""Derive every mechanical field of every segment's contract.json (Task L3, docs/reference/contracts.md).

    contract_scaffold.py <wf_id> [--segments seg_01,seg_02] [--prefill | --apply | --prune-unjudged] [--root .]

A contract has two kinds of fields. MECHANICAL ones follow from files the pipeline already wrote --
`parsed/dag.json`, `segments/order.json`, `segments/<seg>/dag.json`, `segments/targets.json` and
`intake/mappings.yaml` -- and this script derives them; JUDGMENT ones (`row_relation`, `ordering`,
`tolerances`, `parity_risks`, `normalizations`), plus nullability, `keys`, `expected_rows` and
`large`, are the analyzer's. The mechanical fields, per segment:

* `workflow`, `segment`, and `target` (copied from `targets.json`; the analyzer may only lower it).
* `inputs[]`: one entry per mapped source tool in the segment -- `logical` (mappings.yaml `sources`),
  `tool_id`, `columns` -- in tool-id order, then one per distinct stream crossing into the segment
  (`segments/<seg>/dag.json` `inbound`, in edge order) -- `from` (the producing segment), `stream`
  (`<src tool>_<anchor>`), `table` (contract C3: `MIG_WORK.<WF>_<FROM>_OUT` for the producer's first
  work stream, `MIG_WORK.<WF>_<FROM>_OUT_<STREAM>` for any other), `columns`.
* `outputs[]`: one `work` entry per distinct stream leaving the segment (`outbound`, in edge order)
  -- `stream`, `kind`, `table` (C3, as above), `logical: null`, `columns` -- then one `target` entry
  per Output tool in the segment, in tool-id order -- `stream` (the edge into it), `kind`,
  `tool_id`, `logical` (mappings.yaml `outputs`), `table: null`, `write_mode` (mappings.yaml `mode`:
  `overwrite`, `append`, and `merge` written as `update_insert`, the Output tool's own vocabulary),
  `columns`. `output` is `outputs[0]`.
* `columns`: the producing anchor's DAG meta, each name upper-cased (every consumer compares names
  upper-cased) and typed by `lib.types_map.alteryx_to_snowflake` -- except that a variable-length
  string (`V_String`, `V_WString`) keeps its DAG size, `VARCHAR(<size>)`, as six of the seven
  committed samples declare it; the type map's own unsized `VARCHAR` is accepted as the same type.

Neutral defaults the reference uses are pre-filled (`nullable: true`, `keys: []`,
`normalizations: []`); nothing else of the analyzer's is invented. A field that cannot be derived
(a type the type map does not cover, a source with no mapping, an anchor with no meta) is left out and
reported as a note on stderr: it is the analyzer's to declare, and the checker only checks its shape.

Modes (at most one; the default prints the scaffold as JSON and writes nothing):
  --prefill          write each segment's contract.json where none exists yet (the orchestrator, before
                     the analyzer; a resumed run keeps the analyzer's own contract)
  --apply            re-apply the mechanical fields over every existing contract (the orchestrator,
                     after the analyzer): authoritative, keeping every judgment field, nullability,
                     keys, row estimates, `target` exactly as written (lowering and raising are
                     `checkTargets`' to judge), and target-only columns after the stream's own on a
                     target that keeps existing rows (append, update_insert). A contract is rewritten
                     only when something changed.
  --prune-unjudged   delete every contract that carries none of `row_relation`, `ordering`,
                     `tolerances`, `parity_risks` (the orchestrator, for a tier-T3 workflow, which has
                     no contracts: what is left is only what --prefill wrote)

Exit codes: 0 done (a malformed judgment or type field is left in place for `contract_check.py` to
name); 1 (--apply) a contract.json that is not a JSON object, or of a shape the re-apply cannot read
-- the first stderr line names it, and nothing is written; 2 usage (no `segments/order.json`,
`parsed/dag.json` or `intake/mappings.yaml`, an unknown or empty `--segments`, two modes) or a crash,
with nothing written.

Nothing here has run on Snowflake or Alteryx: it reads JSON and YAML the pipeline wrote.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import traceback
from dataclasses import dataclass, field
from typing import Sequence

from lib.io import read_json, read_yaml, write_json
from lib.paths import Repo, add_root_arg, seg_token, wf_token
from lib.types_map import alteryx_to_snowflake

#: The judgment fields are `row_relation`, `ordering`, `tolerances`, `parity_risks` and
#: `normalizations`: the analyzer decides every one of them, and the scaffold writes none but
#: `normalizations`' neutral `[]`. A contract carrying none of these four was never judged by an
#: analyzer (`--prune-unjudged`).
JUDGED_MARKERS = ("row_relation", "ordering", "tolerances", "parity_risks")

#: Every mechanical key per entry kind, in the order the committed samples write them.
MECHANICAL_KEYS = {
    "source": ("logical", "tool_id", "columns"),
    "stream": ("from", "stream", "table", "columns"),
    "work": ("stream", "kind", "table", "logical", "columns"),
    "target": ("stream", "kind", "tool_id", "logical", "table", "write_mode", "columns"),
}

#: Keys an entry of one kind must not carry because they belong to another kind (a mapped source
#: with a `table`, a work stream with a `write_mode`, ...): `--apply` removes them.
FORBIDDEN_KEYS = {
    kind: frozenset(key for keys in MECHANICAL_KEYS.values() for key in keys) - set(keys)
    for kind, keys in MECHANICAL_KEYS.items()
}

#: mappings.yaml `mode` -> the contract's `write_mode`, in the Output tool's own vocabulary
#: (`parsers/tool_config.WRITE_MODES`; intake_prompt maps `update_insert` to `merge` the other way).
WRITE_MODES = {"overwrite": "overwrite", "append": "append", "merge": "update_insert"}

#: Write modes that keep the target's existing rows, so its table can carry columns the stream lacks.
KEEPS_ROWS = frozenset({"append", "update_insert"})

_VARIABLE_STRINGS = frozenset({"V_String", "V_WString"})


class ContractUnreadable(ValueError):
    """A contract.json `--apply` cannot re-apply over: not JSON, or not a JSON object (exit 1)."""


@dataclass
class SegmentScaffold:
    """One segment's derivation. `contract` is exactly what --prefill writes (JSON-ready; a field
    that could not be derived is absent). The rest is what re-applying and checking need to know
    about it and cannot be put in the contract itself."""
    contract: dict
    #: (section, entry index, column index) -> other spellings accepted for that column's type.
    also_accepted: dict[tuple[str, int, int], frozenset[str]] = field(default_factory=dict)
    #: indexes of `outputs[]` entries that may carry target-only columns after the stream's own.
    extras_allowed: set[int] = field(default_factory=set)
    #: every tool id in the segment, and every `<macro>/<tool>` id inside one of its macros (a parity
    #: risk names one of them).
    tool_ids: frozenset[str] = frozenset()
    #: every column name (upper-cased) the segment's data carries anywhere: its inputs, every anchor
    #: of its own tools, its outputs (`ordering.keys` may name any of them).
    data_columns: frozenset[str] = frozenset()


@dataclass
class Derivation:
    segments: dict[str, SegmentScaffold]
    order: list[str]
    #: derivation gaps, one line each: what the analyzer has to declare itself.
    notes: list[str]


def tool_key(tool_id: str) -> tuple[int, int, str]:
    """Numeric tool ids numerically, anything else after (compare.py's `_tool_sort_key` order)."""
    text = str(tool_id)
    return (0, int(text), "") if text.isdigit() else (1, 0, text)


def nested_tool_key(tool_id: str) -> tuple:
    """`tool_key` per `/`-separated part, so `2` < `2/1` < `2/2` < `2/10` < `3`."""
    return tuple(tool_key(part) for part in str(tool_id).split("/"))


def _nested_ids(node: dict, prefix: str) -> list[str]:
    """`<prefix><id>` for every node of a resolved macro's `sub_dag`, recursively (target_check.py's
    naming of a tool inside a macro)."""
    ids: list[str] = []
    for inner in ((node.get("sub_dag") or {}).get("nodes") or []):
        inner_id = f"{prefix}{inner.get('tool_id')}"
        ids.append(inner_id)
        ids.extend(_nested_ids(inner, f"{inner_id}/"))
    return ids


def _column_type(field: dict) -> str:
    """A DAG meta field -> its contract type. Raises ValueError/KeyError/TypeError when the type map
    cannot say (the caller leaves the type to the analyzer)."""
    if field.get("type") in _VARIABLE_STRINGS:
        size = field.get("size")
        if isinstance(size, int) and not isinstance(size, bool) and size > 0:
            return f"VARCHAR({size})"
        return "VARCHAR"
    return alteryx_to_snowflake(field)


def _columns(fields: list | None, where: str, notes: list[str]) -> tuple[list[dict], dict[int, frozenset[str]]]:
    columns: list[dict] = []
    accepted: dict[int, frozenset[str]] = {}
    for index, meta in enumerate(fields or []):
        name = str(meta.get("name")).upper()
        column: dict = {"name": name}
        try:
            column["type"] = _column_type(meta)
        except (KeyError, ValueError, TypeError):
            notes.append(f"{where}: column {name} has Alteryx type {meta.get('type')!r} (size "
                         f"{meta.get('size')!r}), which lib/types_map.py does not map; the analyzer declares its type")
        column["nullable"] = True
        if meta.get("type") in _VARIABLE_STRINGS and column.get("type", "").startswith("VARCHAR("):
            accepted[index] = frozenset({"VARCHAR"})
        columns.append(column)
    return columns, accepted


def _anchor_fields(node: dict | None, anchor: str | None) -> list | None:
    """The meta of one anchor of a node; for `anchor=None`, the node's `Output` anchor or its only one."""
    meta = (node or {}).get("meta") or {}
    if anchor is not None:
        return meta.get(anchor)
    if "Output" in meta:
        return meta["Output"]
    return next(iter(meta.values())) if len(meta) == 1 else None


def _distinct_streams(edges: list | None, segment_key: str) -> list[tuple[str, str, str | None]]:
    """(src, src_anchor, other segment) for every distinct (src, anchor), in edge order."""
    seen: list[tuple[str, str, str | None]] = []
    keys: set[tuple[str, str]] = set()
    for edge in edges or []:
        key = (str(edge.get("src")), str(edge.get("src_anchor")))
        if key not in keys:
            keys.add(key)
            seen.append((*key, edge.get(segment_key)))
    return seen


def _stream_token(stream: str) -> str:
    """A stream as the tail of a C3 table name: upper-cased, anything but A-Z/0-9/_ as `_`."""
    return re.sub(r"[^A-Z0-9_]", "_", stream.upper())


def _by_tool(section: dict | None) -> dict[str, dict]:
    """mappings.yaml `sources`/`outputs` -> {tool id: entry} (the first entry naming a tool wins)."""
    out: dict[str, dict] = {}
    for entry in (section or {}).values():
        if not isinstance(entry, dict):
            continue
        for tool_id in entry.get("tool_ids") or []:
            out.setdefault(str(tool_id), entry)
    return out


def derive(repo: Repo, wf_id: str) -> Derivation:
    """Every segment's scaffold. Raises FileNotFoundError when `segments/order.json`,
    `parsed/dag.json`, `intake/mappings.yaml` or a segment's `dag.json` does not exist."""
    order = [seg for wave in read_json(repo.wf(wf_id, "segments", "order.json")) for seg in wave]
    dag = read_json(repo.wf(wf_id, "parsed", "dag.json"))
    mappings = read_yaml(repo.wf(wf_id, "intake", "mappings.yaml")) or {}
    targets_file = repo.wf(wf_id, "segments", "targets.json")
    proposals = (read_json(targets_file).get("segments") or {}) if targets_file.is_file() else {}
    sub_dags = {seg: read_json(repo.seg(wf_id, seg, "dag.json")) for seg in order}

    nodes = {str(node["tool_id"]): node for node in dag.get("nodes") or []}
    sources = _by_tool(mappings.get("sources"))
    outputs_map = _by_tool(mappings.get("outputs"))
    edges_into: dict[str, dict] = {}
    for edge in dag.get("edges") or []:
        edges_into.setdefault(str(edge.get("dst")), edge)

    def work_streams(seg: str) -> list[str]:
        return [f"{src}_{anchor}" for src, anchor, _ in _distinct_streams(sub_dags[seg].get("outbound"), "to_segment")]

    def table_of(seg: str, stream: str) -> str | None:
        if seg not in sub_dags:
            return None
        base = f"MIG_WORK.{wf_token(wf_id)}_{seg_token(seg)}_OUT"
        streams = work_streams(seg)
        return base if streams and streams[0] == stream else f"{base}_{_stream_token(stream)}"

    notes: list[str] = []
    segments: dict[str, SegmentScaffold] = {}
    for seg in order:
        sub = sub_dags[seg]
        member_ids = [str(node["tool_id"]) for node in sub.get("nodes") or []]
        members = [nodes.get(tool_id) or node for tool_id, node in zip(member_ids, sub.get("nodes") or [])]
        nested = [inner for tool_id, node in zip(member_ids, members)
                  for inner in _nested_ids(node or {}, f"{tool_id}/")]
        scaffold = SegmentScaffold(contract={}, tool_ids=frozenset(member_ids + nested))
        inputs: list[dict] = []
        outputs: list[dict] = []

        def columns_for(section: str, entries: list[dict], fields: list | None, where: str) -> dict:
            if fields is None:
                notes.append(f"{where}: the DAG has no field list for it; the analyzer declares its columns")
                return {}
            columns, accepted = _columns(fields, where, notes)
            for column_index, spellings in accepted.items():
                scaffold.also_accepted[(section, len(entries), column_index)] = spellings
            return {"columns": columns}

        # inputs: mapped sources first, in tool-id order ...
        source_ids = sorted((t for t, node in zip(member_ids, members) if node.get("type") == "input" or t in sources),
                            key=tool_key)
        for tool_id in source_ids:
            where = f"{seg} inputs[{len(inputs)}] (source tool {tool_id})"
            entry: dict = {}
            logical = (sources.get(tool_id) or {}).get("logical")
            if logical:
                entry["logical"] = str(logical)
            else:
                notes.append(f"{where}: intake/mappings.yaml maps no logical name to tool {tool_id}")
            entry["tool_id"] = tool_id
            entry.update(columns_for("inputs", inputs, _anchor_fields(nodes.get(tool_id), None), where))
            entry["keys"] = []
            inputs.append(entry)
        # ... then every stream crossing in from another segment, in edge order.
        for src, anchor, producer in _distinct_streams(sub.get("inbound"), "from_segment"):
            stream = f"{src}_{anchor}"
            where = f"{seg} inputs[{len(inputs)}] (stream {stream})"
            entry = {}
            if producer:
                entry["from"] = str(producer)
            else:
                notes.append(f"{where}: segments/{seg}/dag.json names no from_segment for it")
            entry["stream"] = stream
            table = table_of(str(producer), stream) if producer else None
            if table:
                entry["table"] = table
            elif producer:
                notes.append(f"{where}: {producer} is not a segment of segments/order.json")
            entry.update(columns_for("inputs", inputs, _anchor_fields(nodes.get(src), anchor), where))
            entry["keys"] = []
            inputs.append(entry)

        # outputs: every work stream leaving the segment, in edge order ...
        for src, anchor, _ in _distinct_streams(sub.get("outbound"), "to_segment"):
            stream = f"{src}_{anchor}"
            where = f"{seg} outputs[{len(outputs)}] (work stream {stream})"
            entry = {"stream": stream, "kind": "work", "table": table_of(seg, stream), "logical": None}
            entry.update(columns_for("outputs", outputs, _anchor_fields(nodes.get(src), anchor), where))
            entry["keys"] = []
            outputs.append(entry)
        # ... then every Output tool, in tool-id order.
        output_ids = sorted((t for t, node in zip(member_ids, members) if node.get("type") == "output"), key=tool_key)
        for tool_id in output_ids:
            where = f"{seg} outputs[{len(outputs)}] (target tool {tool_id})"
            feed = edges_into.get(tool_id)
            entry = {}
            if feed:
                entry["stream"] = f"{feed.get('src')}_{feed.get('src_anchor')}"
            else:
                notes.append(f"{where}: no edge feeds Output tool {tool_id}")
            entry["kind"] = "target"
            entry["tool_id"] = tool_id
            mapping = outputs_map.get(tool_id) or {}
            if mapping.get("logical"):
                entry["logical"] = str(mapping["logical"])
            else:
                notes.append(f"{where}: intake/mappings.yaml maps no logical name to tool {tool_id}")
            entry["table"] = None
            mode = WRITE_MODES.get(str(mapping.get("mode"))) or ((nodes.get(tool_id) or {}).get("config") or {}).get("write_mode")
            if mode:
                entry["write_mode"] = str(mode)
                if mode in KEEPS_ROWS:
                    scaffold.extras_allowed.add(len(outputs))
            else:
                notes.append(f"{where}: neither intake/mappings.yaml nor the Output tool names a write mode")
            fields = _anchor_fields(nodes.get(str(feed.get("src"))), str(feed.get("src_anchor"))) if feed else None
            entry.update(columns_for("outputs", outputs, fields, where))
            entry["keys"] = []
            outputs.append(entry)
        if not outputs:
            notes.append(f"{seg}: no stream leaves the segment and it has no Output tool; it has no outputs[]")

        contract: dict = {"workflow": wf_id, "segment": seg}
        if isinstance(proposals.get(seg), str):
            contract["target"] = proposals[seg]
        contract["inputs"] = inputs
        contract["outputs"] = outputs
        if outputs:
            contract["output"] = copy.deepcopy(outputs[0])
        contract["normalizations"] = []
        scaffold.contract = contract

        seen: set[str] = set()
        for node in members:
            for fields in ((node or {}).get("meta") or {}).values():
                seen.update(str(f.get("name")).upper() for f in fields or [])
        for entry in inputs + outputs:
            seen.update(str(c.get("name")).upper() for c in entry.get("columns") or [])
        scaffold.data_columns = frozenset(seen)
        segments[seg] = scaffold
    return Derivation(segments=segments, order=order, notes=notes)


# --- re-applying --------------------------------------------------------------------------------


def entry_kind(entry: dict, section: str) -> str:
    """`source` / `stream` for an input, `work` / `target` for an output (as the entry itself says)."""
    if section == "inputs":
        return "stream" if entry.get("stream") or entry.get("from") else "source"
    return "target" if entry.get("kind") == "target" else "work"


def _fallback(entry: dict, kind: str) -> tuple | None:
    """A second way to recognise an entry whose identity key is wrong or missing (the live analyzer
    left out `tool_id` and invented a stream name): its logical name, or a work stream's table."""
    if kind in ("source", "target") and entry.get("logical"):
        return ("logical", str(entry["logical"]).upper())
    if kind in ("stream", "work") and entry.get("table"):
        return ("table", str(entry["table"]).upper())
    return None


def match_entries(scaffolded: list[dict], written: list, section: str) -> list[dict | None]:
    """For each scaffold entry, the written entry it corresponds to (or None). Each written entry is
    used at most once, in three passes:

    1. a source or a target by its tool id, whatever the written entry's own classification says --
       a target that left out `kind`, or a source that also carries a `stream`, keeps its judgment
       (review of L3, M1);
    2. a stream or a work entry by its stream, among written entries of its own kind;
    3. anything still unmatched by its logical name (a source or target, whatever the written entry's
       classification) or its table (a stream or work entry of its own kind).
    """
    pool = [(i, e) for i, e in enumerate(written) if isinstance(e, dict)]
    used: set[int] = set()
    found: list[int | None] = [None] * len(scaffolded)
    kinds = [entry_kind(mine, section) for mine in scaffolded]

    def take(index: int, predicate) -> None:
        match = next((i for i, e in pool if i not in used and predicate(e)), None)
        if match is not None:
            used.add(match)
            found[index] = match

    for index, (mine, kind) in enumerate(zip(scaffolded, kinds)):
        if kind in ("source", "target"):
            take(index, lambda e, t=str(mine.get("tool_id")): e.get("tool_id") is not None and str(e["tool_id"]) == t)
    for index, (mine, kind) in enumerate(zip(scaffolded, kinds)):
        if found[index] is None and kind in ("stream", "work"):
            take(index, lambda e, k=kind, st=str(mine.get("stream")):
                 entry_kind(e, section) == k and str(e.get("stream")) == st)
    for index, (mine, kind) in enumerate(zip(scaffolded, kinds)):
        key = _fallback(mine, kind)
        if found[index] is None and key is not None:
            same = ("source", "target", "stream", "work") if kind in ("source", "target") else (kind,)
            take(index, lambda e, k=kind, key=key, same=same:
                 entry_kind(e, section) in same and _fallback(e, k) == key)
    return [written[i] if i is not None else None for i in found]


def _apply_columns(derived: list[dict], written, accepted: dict[int, frozenset[str]], extras: bool) -> list[dict]:
    theirs = [c for c in written or [] if isinstance(c, dict)] if isinstance(written, list) else []
    by_name = {}
    for column in theirs:
        by_name.setdefault(str(column.get("name")).upper(), column)
    out: list[dict] = []
    for index, mine in enumerate(derived):
        other = by_name.pop(mine["name"], None) or {}
        column = {"name": mine["name"]}
        if "type" in mine:
            spelled = other.get("type")
            # Only a string can be an accepted spelling; anything else (a list, an object) is replaced
            # by the derived type rather than looked up in a set (review of L3, I3).
            keep = isinstance(spelled, str) and spelled in accepted.get(index, ())
            column["type"] = spelled if keep else mine["type"]
        elif "type" in other:
            column["type"] = other["type"]
        column["nullable"] = other["nullable"] if "nullable" in other else True
        for key, value in other.items():
            column.setdefault(key, value)
        out.append(column)
    if extras:
        derived_names = {c["name"] for c in derived}
        out.extend(c for c in theirs if str(c.get("name")).upper() not in derived_names)
    return out


def _apply_entry(mine: dict, theirs: dict | None, section: str, index: int, scaffold: SegmentScaffold) -> dict:
    theirs = theirs or {}
    kind = entry_kind(mine, section)
    out: dict = {}
    for key in MECHANICAL_KEYS[kind]:
        if key == "columns":
            if "columns" in mine:
                accepted = {c: spellings for (s, e, c), spellings in scaffold.also_accepted.items()
                            if (s, e) == (section, index)}
                extras = section == "outputs" and index in scaffold.extras_allowed
                out["columns"] = _apply_columns(mine["columns"], theirs.get("columns"), accepted, extras)
            elif "columns" in theirs:
                out["columns"] = theirs["columns"]
        elif key in mine:
            out[key] = mine[key]
        elif key in theirs:                     # not derivable: the analyzer's to declare
            out[key] = theirs[key]
    out["keys"] = theirs["keys"] if "keys" in theirs else []
    for key, value in theirs.items():
        if key not in out and key not in FORBIDDEN_KEYS[kind]:
            out[key] = value
    return out


def apply(contract: dict, scaffold: SegmentScaffold) -> dict:
    """`contract` with every mechanical field re-applied from `scaffold` (pure). Entries the DAG does
    not have are dropped and missing ones added; every judgment field, nullability, `keys`,
    `expected_rows`, `large`, any other key the analyzer wrote, and `target` exactly as written are
    kept. Target-only columns survive after the stream's own only on a target that keeps rows."""
    derived = scaffold.contract
    new: dict = {"workflow": derived["workflow"], "segment": derived["segment"]}
    if "target" in contract:
        new["target"] = contract["target"]
    for section in ("inputs", "outputs"):
        written = contract.get(section) if isinstance(contract.get(section), list) else []
        matched = match_entries(derived[section], written, section)
        new[section] = [_apply_entry(mine, theirs, section, index, scaffold)
                        for index, (mine, theirs) in enumerate(zip(derived[section], matched))]
    if new["outputs"]:
        new["output"] = copy.deepcopy(new["outputs"][0])
    for key, value in contract.items():
        if key not in new and key not in ("workflow", "segment", "target", "inputs", "outputs", "output"):
            new[key] = value
    return new


def read_contract(repo: Repo, wf_id: str, seg: str) -> dict:
    """A segment's contract.json as a dict; ContractUnreadable when it is not a JSON object."""
    path = repo.seg(wf_id, seg, "contract.json")
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ContractUnreadable(f"{seg}: contract.json is not a JSON object ({exc})") from exc
    if not isinstance(contract, dict):
        raise ContractUnreadable(f"{seg}: contract.json is not a JSON object (it holds a {type(contract).__name__})")
    return contract


def in_scope(derivation: Derivation, segments: Sequence[str] | None) -> list[str]:
    if segments is None:
        return list(derivation.order)
    unknown = [seg for seg in segments if seg not in derivation.segments]
    if unknown:
        raise UnknownSegment(f"{', '.join(unknown)} not in segments/order.json")
    return [seg for seg in derivation.order if seg in set(segments)]


class UnknownSegment(ValueError):
    """A `--segments` id that `segments/order.json` does not list: a usage error, exit 2."""


def prefill(repo: Repo, wf_id: str, segments: Sequence[str] | None = None,
            derivation: Derivation | None = None) -> list[str]:
    """Writes the scaffold as contract.json for every segment in scope that has none yet; returns
    the segments written."""
    derivation = derivation or derive(repo, wf_id)
    written = []
    for seg in in_scope(derivation, segments):
        path = repo.seg(wf_id, seg, "contract.json")
        if not path.exists():
            write_json(path, derivation.segments[seg].contract)
            written.append(seg)
    return written


def apply_all(repo: Repo, wf_id: str, segments: Sequence[str] | None = None,
              derivation: Derivation | None = None) -> list[str]:
    """Re-applies the scaffold over every existing contract in scope, rewriting only the ones that
    changed; returns those. Raises ContractUnreadable -- before writing anything -- for a contract
    that is not a JSON object."""
    derivation = derivation or derive(repo, wf_id)
    scope = [seg for seg in in_scope(derivation, segments) if repo.seg(wf_id, seg, "contract.json").is_file()]
    contracts = {seg: read_contract(repo, wf_id, seg) for seg in scope}
    applied: dict[str, dict] = {}
    for seg, contract in contracts.items():
        try:
            applied[seg] = apply(contract, derivation.segments[seg])
        except (TypeError, AttributeError, KeyError, ValueError) as exc:
            # A shape `apply` did not foresee is still the model's contract, never a crash of this
            # script: exit 1 with the segment named, and contract_check.py names the fields.
            raise ContractUnreadable(f"{seg}: contract.json has a shape the re-apply cannot read "
                                     f"({type(exc).__name__}: {exc}); expected a contract "
                                     f"scripts/contract_check.py can check") from exc
    changed = [seg for seg, new in applied.items() if new != contracts[seg]]
    for seg in changed:
        write_json(repo.seg(wf_id, seg, "contract.json"), applied[seg])
    return changed


def unjudged(contract: dict) -> bool:
    return not any(field in contract for field in JUDGED_MARKERS)


def prune_unjudged(repo: Repo, wf_id: str, segments: Sequence[str] | None = None) -> list[str]:
    """Deletes every contract in scope that no analyzer judged (see `unjudged`); returns those. A
    contract that is not a JSON object is left alone: it is not the scaffold's."""
    order = [seg for wave in read_json(repo.wf(wf_id, "segments", "order.json")) for seg in wave]
    removed = []
    for seg in order:
        if segments is not None and seg not in segments:
            continue
        path = repo.seg(wf_id, seg, "contract.json")
        if not path.is_file():
            continue
        try:
            contract = read_contract(repo, wf_id, seg)
        except ContractUnreadable:
            continue
        if unjudged(contract):
            path.unlink()
            removed.append(seg)
    return removed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id")
    parser.add_argument("--segments", help="comma-separated segments (default: every segment of order.json)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prefill", action="store_true", help="write contract.json where none exists")
    mode.add_argument("--apply", action="store_true", help="re-apply the mechanical fields over every contract")
    mode.add_argument("--prune-unjudged", action="store_true", help="delete contracts no analyzer judged")
    add_root_arg(parser)
    args = parser.parse_args(argv)
    segments = [s.strip() for s in args.segments.split(",") if s.strip()] if args.segments is not None else None
    if segments == []:
        parser.error("--segments names no segment")                         # exit 2: never "scaffold nothing"
    repo = Repo(args.root)

    try:
        derivation = derive(repo, args.wf_id)
        scope = in_scope(derivation, segments)
    except FileNotFoundError as exc:
        parser.error(f"{args.wf_id} cannot be scaffolded yet: {exc}")      # exit 2, nothing written
    except UnknownSegment as exc:
        parser.error(str(exc))                                             # exit 2, nothing written
    except Exception:                                                      # exit 2: a crash, never a verdict
        traceback.print_exc()
        return 2

    try:
        if args.apply:
            changed = apply_all(repo, args.wf_id, scope, derivation)
            print(f"{args.wf_id}: re-applied the mechanical fields; rewrote {', '.join(changed) or 'none'}")
            return 0
        if args.prune_unjudged:
            removed = prune_unjudged(repo, args.wf_id, scope)
            print(f"{args.wf_id}: removed the unjudged contracts of {', '.join(removed) or 'none'}")
            return 0
        notes = [note for note in derivation.notes if note.split(" ", 1)[0].rstrip(":") in scope]
        for note in notes:
            print(f"note: {note}", file=sys.stderr)
        if args.prefill:
            written = prefill(repo, args.wf_id, scope, derivation)
            print(f"{args.wf_id}: pre-filled {', '.join(written) or 'none'}; every other contract already existed")
            return 0
        print(json.dumps({seg: derivation.segments[seg].contract for seg in scope}, indent=2))
        return 0
    except ContractUnreadable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
