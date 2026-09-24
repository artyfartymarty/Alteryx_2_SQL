"""Write the skeleton of a translation: every mechanical line, with a TODO body per tool (Task L8).

    translation_scaffold.py <wf_id> [--segment seg_01 | --dbt] [--root .]

A translation has two kinds of lines. MECHANICAL ones follow from files the pipeline already wrote --
the segment's `contract.json`, its `dag.json`, `segments/order.json`, `intake/mappings.yaml` and
`mappings/global.yaml` -- and this script writes them; the TRANSFORMATION (what each Alteryx tool does
to its rows) is the translator's. Every place the transformation goes is a stub whose body is exactly
`lib.scaffold.TODO_MARKER`, and `compile_check.py` refuses a file that still holds one
(`scaffold:todo`), so an unfilled skeleton can never compile. Per target:

* **sql** (`segments/<seg>/proc.sql`): contract C4's header, the session line (`mappings/global.yaml`
  `session`), one documented `LET` per mapped source and per target (C4V form), then one statement
  per contract output in the contract's order -- a work stream as `CREATE OR REPLACE TRANSIENT TABLE
  <its C3 table> AS`, a target in the form its write mode needs (`c4:write_mode`: overwrite
  `CREATE OR REPLACE TABLE IDENTIFIER(:<L>_TGT) AS`, append `INSERT INTO … (<columns>)`,
  truncate_append a `TRUNCATE TABLE` then that `INSERT`, update_insert a `MERGE … ON` the contract's
  keys with both `WHEN` clauses), with a TODO statement before/after it for the Output tool's
  PreSQL/PostSQL. Each statement's `WITH` holds one CTE stub per data node it needs, in DAG order,
  named as the canned procedures name them (`t<id>_<type>`, `_<anchor>` for one anchor of a tool with
  several, `t<macro>_macro_m<inner>_<type>` for a macro's inner tools), each after a
  `-- tool <id>: <type> <annotation>` comment and a line saying what it reads and which columns it
  yields; the statement ends `SELECT <the stream's columns> FROM <the CTE that yields it>`. Then
  `RETURN 'OK'`.
* **snowpark** (`segments/<seg>/proc.py`): `def run(session, src_db, src_schema, tgt_db, tgt_schema,
  run_id)`, every input read (`session.table(f"{src_db}.{src_schema}.<L>")` for a mapped source, the
  literal C3 table for an upstream stream) into a named DataFrame, one stub per data node with its
  `# tool <id>:` comment, every output written from a named `out_<stream>` DataFrame in the form the
  Snowpark rules' `rule:write_mode` needs, and `return "OK"`.
* **dbt** (`dbt/`, the whole workflow): `dbt_project.yml` (`lib.dbt_project.PROJECT_YML_TEMPLATE`),
  `profiles.yml` (`PROFILES_TEMPLATE`, byte for byte), `README.md`, `models/sources.yml` (every source
  `intake/mappings.yaml` maps, with the contract's input columns), `models/schema.yml` (every model's
  contract columns, `not_null` for a `nullable: false` column, `unique` for a single-column key), and
  one model per contract output named by `lib.dbt_project.model_name`, its exact `config(...)` line
  (`expected_model_config` for its write mode, with a TODO `pre_hook`/`post_hook` where the Output tool
  has a PreSQL/PostSQL), its `source()`/`ref()` reads pre-written as the first CTEs, a CTE stub per
  data node and the final `SELECT`.

Nothing is ever written over an existing file: a resumed run keeps the translator's work, and the
offline MockRunner's canned translation (written after this) is never touched; a dbt project's
`dbt_project.yml` is written last. Text a workflow authored reaches a skeleton in two forms only
(fix round 1, M5): prose (an annotation, a PreSQL) inside a comment, on one line, without `$$`, braces
or the TODO marker; and column names in code -- double-quoted wherever a name is not plain or is a
reserved word of Snowflake or DuckDB (`needs_quotes`), YAML-quoted wherever YAML would read it as
something else (`_yaml_name`), JSON-escaped in a Python literal. A name that holds `$$` (a SQL or
Snowpark procedure) or a brace (dbt) cannot be written there at all: each place it would be named is
the TODO marker, with a note.

`--segment <seg>` writes that segment's procedure, `--dbt` the workflow's dbt project (the
orchestrator always says which); with neither, a workflow whose `manifest.json` says `output_kind: dbt`
gets its project and any other every segment's procedure (a `manual` segment gets none). Exit codes:
0 done (what was written and what was kept on stdout; a part that could not be derived is a TODO, with
a note on stderr); 2 usage -- no `segments/order.json`, contract, DAG or mappings, an unknown segment,
`--segment` with `--dbt` or for a dbt workflow -- or a crash, with nothing written.

Nothing here has run on Snowflake or Alteryx: it reads the JSON and YAML the pipeline wrote.
"""
from __future__ import annotations

import argparse
import heapq
import json
import re
import sys
import traceback
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Sequence

import yaml

import contract_scaffold as cs
from lib import dbt_project
from lib.io import load_manifest, read_json, read_yaml
from lib.paths import Repo, add_root_arg, seg_token, wf_token
from lib.scaffold import TODO_MARKER, TODO_PATTERN
from lib.vocab import DATA_LESS_TYPES, TARGET_WRITE_MODES
from lib.write_modes import ContractError, TargetWrite, target_writes

_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SESSION_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Unscaffoldable(ValueError):
    """A usage error: the workflow or segment cannot be scaffolded as asked (exit 2)."""


# --- text a workflow authored, made safe for a comment ------------------------------------------------


def _text(value, limit: int = 160) -> str:
    """One comment line's worth of workflow-authored text: whitespace (newlines included) squeezed,
    no control or format character, no `$$` (it would end a procedure body), no brace (Jinja in a dbt
    model is rendered even inside a SQL comment), never the TODO marker, bounded."""
    text = " ".join(str(value if value is not None else "").split())
    text = "".join(ch for ch in text if ch.isprintable())
    text = TODO_PATTERN.sub("TODO", text.replace("{", "(").replace("}", ")"))
    while "$$" in text:
        text = text.replace("$$", "$ $")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- identifiers a workflow authored, made safe for code (fix round 1, I2 and M5) ------------------------

#: Snowflake's reserved keywords, transcribed from its documentation ("Reserved & limited keywords"):
#: a column spelled like one must be double-quoted. Not checked against an account (nothing here has
#: run on Snowflake); quoting an upper-case name more often than needed is harmless on both engines,
#: because an unquoted identifier folds to upper case.
SNOWFLAKE_RESERVED = frozenset("""
    ACCOUNT ALL ALTER AND ANY AS BETWEEN BY CASE CAST CHECK COLUMN CONNECT CONNECTION CONSTRAINT CREATE CROSS
    CURRENT CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP CURRENT_USER DATABASE DELETE DISTINCT DROP ELSE EXISTS
    FALSE FOLLOWING FOR FROM FULL GRANT GROUP GSCLUSTER HAVING ILIKE IN INCREMENT INNER INSERT INTERSECT INTO IS
    ISSUE JOIN LATERAL LEFT LIKE LOCALTIME LOCALTIMESTAMP MINUS NATURAL NOT NULL OF ON OR ORDER ORGANIZATION
    QUALIFY REGEXP REVOKE RIGHT RLIKE ROW ROWS SAMPLE SCHEMA SELECT SET SOME START TABLE TABLESAMPLE THEN TO
    TRIGGER TRUE TRY_CAST UNION UNIQUE UPDATE USING VALUES VIEW WHEN WHENEVER WHERE WINDOW WITH""".split())


@lru_cache(maxsize=1)
def duckdb_reserved() -> frozenset[str]:
    """The keywords the local double's parser refuses as a bare column name: DuckDB's own
    `duckdb_keywords()`, categories `reserved` and `type_function` (a `column_name` or `unreserved`
    keyword is a valid bare column name)."""
    import duckdb
    with duckdb.connect() as con:
        rows = con.execute("SELECT keyword_name FROM duckdb_keywords() "
                           "WHERE keyword_category IN ('reserved', 'type_function')").fetchall()
    return frozenset(str(row[0]).upper() for row in rows)


def needs_quotes(name: str) -> bool:
    """Whether a column must be double-quoted to be read as that column on Snowflake and on DuckDB."""
    return not _PLAIN.match(name) or name.upper() in SNOWFLAKE_RESERVED or name.upper() in duckdb_reserved()


#: What a name may not hold to be written in each target's code at all: `$$` ends a procedure body (a SQL
#: procedure, and a Snowpark module inside its rendered wrapper); a brace can open Jinja in a dbt model or
#: YAML file. Such a name is written as the TODO marker instead, with a note (`unwritable`).
_UNWRITABLE = {"sql": ("$$",), "snowpark": ("$$",), "dbt": ("{", "}")}


def unwritable(name: str, target: str) -> bool:
    return any(piece in name for piece in _UNWRITABLE[target])


def _ident(name: str, target: str = "sql") -> str:
    """A column as SQL (a procedure, or a dbt model) names it: a plain name that is no reserved word as
    is, anything else double-quoted -- and a name the target cannot hold, the TODO marker."""
    if unwritable(name, target):
        return TODO_MARKER
    return '"' + name.replace('"', '""') + '"' if needs_quotes(name) else name


def _dbt_ident(name: str) -> str:
    return _ident(name, "dbt")


def _py_name(name: str) -> str:
    """A column as a Python string literal (Snowpark quotes every name it is given, so a reserved word
    needs nothing more); a name that holds `$$` is the TODO marker."""
    return TODO_MARKER if unwritable(name, "snowpark") else json.dumps(name)


def _var(text: str) -> str:
    """A Python identifier made from a stream or logical name."""
    return re.sub(r"[^a-z0-9_]", "_", text.lower())


# --- the data flow of one segment, one CTE per (tool, anchor) -----------------------------------------


@dataclass(frozen=True)
class Read:
    """What a stub reads: another stub (`unit`, by key), a mapped source (`source`, by logical) or an
    upstream segment's stream (`stream`, by stream name). `label` names the input anchor when the tool
    has several inputs."""
    kind: str
    name: object
    label: str | None = None


@dataclass
class Unit:
    """One CTE: a data tool's output on one anchor (`anchor` is None for a tool with only one)."""
    path: str
    anchor: str | None
    node: dict
    reads: list[Read] = field(default_factory=list)
    columns: list[str] | None = None

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.path, self.anchor)

    @property
    def cte(self) -> str:
        parts = self.path.split("/")
        name = f"t{parts[0]}" + "".join(f"_macro_m{part}" for part in parts[1:])
        name += "_" + _var(str(self.node.get("type") or "tool"))
        return name + (f"_{_var(self.anchor)}" if self.anchor else "")


def _multi_anchor(node: dict) -> bool:
    return len(node.get("out_anchors") or list((node.get("meta") or {}).keys())) > 1


def _columns(fields) -> list[str] | None:
    return None if fields is None else [str(f.get("name")).upper() for f in fields]


class SegmentFlow:
    """The segment DAG as stubs: which (tool, anchor) reads what, flattened through macros."""

    def __init__(self, dag: dict, sources: dict[str, dict]):
        self.nodes = {str(n.get("tool_id")): n for n in dag.get("nodes") or []}
        self.edges = list(dag.get("edges") or []) + list(dag.get("inbound") or [])
        self.leaving = list(dag.get("edges") or []) + list(dag.get("outbound") or [])
        self.sources = sources
        self._units: dict[tuple[str, str | None], Unit] = {}

    # nodes and edges at any depth ("2" is a segment tool, "2/4" tool 4 inside macro 2)

    def node_at(self, path: str) -> dict | None:
        parts = path.split("/")
        node = self.nodes.get(parts[0])
        for part in parts[1:]:
            inner = {str(n.get("tool_id")): n for n in ((node or {}).get("sub_dag") or {}).get("nodes") or []}
            node = inner.get(part)
        return node

    def _edges_in(self, path: str) -> list[dict]:
        """Edges into `path`, `src` qualified to the same depth."""
        if "/" not in path:
            return [e for e in self.edges if str(e.get("dst")) == path]
        parent, inner = path.rsplit("/", 1)
        sub = (self.node_at(parent) or {}).get("sub_dag") or {}
        return [{**e, "src": f"{parent}/{e.get('src')}"} for e in sub.get("edges") or [] if str(e.get("dst")) == inner]

    def _edges_out(self, path: str) -> list[dict]:
        if "/" not in path:
            return [e for e in self.leaving if str(e.get("src")) == path]
        parent, inner = path.rsplit("/", 1)
        sub = (self.node_at(parent) or {}).get("sub_dag") or {}
        return [e for e in sub.get("edges") or [] if str(e.get("src")) == inner]

    def feeds(self, path: str) -> list[dict]:
        """The edges feeding a stub, in input-anchor order (a macro's inner Macro Input is fed by
        whatever feeds the macro's own `Input<id>` anchor)."""
        node = self.node_at(path) or {}
        if node.get("type") == "macro_input" and "/" in path:
            parent, inner = path.rsplit("/", 1)
            return [e for e in self._edges_in(parent) if str(e.get("dst_anchor")) == f"Input{inner}"]
        anchors = [str(a) for a in node.get("in_anchors") or []]
        edges = self._edges_in(path)
        rank = {id(e): i for i, e in enumerate(edges)}
        return sorted(edges, key=lambda e: (anchors.index(str(e.get("dst_anchor"))) if str(e.get("dst_anchor")) in anchors
                                            else len(anchors), e.get("dst_order") or 0, rank[id(e)]))

    def resolve(self, src: str, anchor: str | None) -> Read:
        """What reading `src`'s `anchor` means: an upstream stream, a macro's inner stub, or a stub."""
        node = self.node_at(src)
        if node is None:
            return Read("stream", f"{src}_{anchor}")
        sub = node.get("sub_dag") or {}
        if node.get("type") == "macro" and sub.get("nodes"):
            inner = str(anchor or "").removeprefix("Output")
            feed = next((e for e in sub.get("edges") or [] if str(e.get("dst")) == inner), None)
            if feed is None:
                return Read("unit", self.unit(src, None).key)
            return self.resolve(f"{src}/{feed.get('src')}", str(feed.get("src_anchor")))
        return Read("unit", self.unit(src, anchor if _multi_anchor(node) else None).key)

    def unit(self, path: str, anchor: str | None) -> Unit:
        key = (path, anchor)
        if key in self._units:
            return self._units[key]
        node = self.node_at(path) or {}
        unit = Unit(path=path, anchor=anchor, node=node)
        self._units[key] = unit
        meta = node.get("meta") or {}
        unit.columns = _columns(meta.get(anchor) if anchor else cs._anchor_fields(node, None))
        if "/" not in path and path in self.sources:
            unit.reads = [Read("source", self.sources[path])]
            return unit
        feeds = self.feeds(path)
        per_anchor: dict[str, int] = {}
        for edge in feeds:
            per_anchor[str(edge.get("dst_anchor"))] = per_anchor.get(str(edge.get("dst_anchor")), 0) + 1
        for edge in feeds:
            label = None
            if len(feeds) > 1:
                label = str(edge.get("dst_anchor"))
                if per_anchor[label] > 1:
                    label += f" #{edge.get('dst_order')}"
            read = self.resolve(str(edge.get("src")), str(edge.get("src_anchor")))
            unit.reads.append(Read(read.kind, read.name, label))
        return unit

    def _rank(self, unit: Unit) -> tuple:
        """DAG order among ready stubs: the tool id, then (for one tool's several anchors) the order
        of the first edge leaving each anchor."""
        leaving = self._edges_out(unit.path)
        first = next((i for i, e in enumerate(leaving) if str(e.get("src_anchor")) == unit.anchor), None)
        if first is None:
            anchors = [str(a) for a in unit.node.get("out_anchors") or []]
            first = 10_000 + (anchors.index(unit.anchor) if unit.anchor in anchors else 0)
        return (cs.nested_tool_key(unit.path), first)

    def chain(self, read: Read) -> list[Unit]:
        """Every stub `read` needs, in DAG order (a stable topological order, smallest tool first)."""
        needed: dict[tuple, Unit] = {}
        pending = [read]
        while pending:
            current = pending.pop()
            if current.kind != "unit" or current.name in needed:
                continue
            unit = self._units[current.name]
            needed[unit.key] = unit
            pending.extend(unit.reads)
        waiting = {key: {r.name for r in unit.reads if r.kind == "unit"} for key, unit in needed.items()}
        ready = [(self._rank(u), key) for key, u in needed.items() if not waiting[key]]
        heapq.heapify(ready)
        ordered: list[Unit] = []
        while ready:
            _, key = heapq.heappop(ready)
            ordered.append(needed[key])
            for other, deps in waiting.items():
                if key in deps:
                    deps.discard(key)
                    if not deps:                                 # its last input just became available
                        heapq.heappush(ready, (self._rank(needed[other]), other))
        placed = {unit.key for unit in ordered}                  # a cycle (never in a parsed DAG) keeps its stubs
        return ordered + sorted((u for k, u in needed.items() if k not in placed), key=self._rank)

    def data_paths(self) -> list[str]:
        """Every segment-level data tool (not an Output tool), in tool-id order."""
        return sorted((p for p, n in self.nodes.items()
                       if n.get("type") not in DATA_LESS_TYPES and n.get("type") != "output"), key=cs.tool_key)


# --- one segment, everything the three renderers need -------------------------------------------------


@dataclass
class Output:
    """One contract output: the stream it writes and where."""
    entry: dict                      # the derived (mechanical) contract entry
    written: dict                    # the contract file's own entry (nullability, keys), or {}
    feed: Read | None
    columns: list[str]               # the stream's own columns, in order
    write: TargetWrite | None = None
    problem: str | None = None       # why the write could not be derived (the statement is a TODO)


@dataclass
class Segment:
    wf_id: str
    seg: str
    contract: dict
    derived: dict
    flow: SegmentFlow
    outputs: list[Output]
    streams: dict[str, str]          # upstream stream -> its C3 table
    notes: list[str]

    @property
    def proc(self) -> str:
        return f"MIG_WORK.{wf_token(self.wf_id)}_{seg_token(self.seg)}"


def _load(repo: Repo, wf_id: str, derivation: cs.Derivation | None = None) -> tuple[cs.Derivation, dict]:
    derivation = derivation or cs.derive(repo, wf_id)
    mappings = read_yaml(repo.wf(wf_id, "intake", "mappings.yaml")) or {}
    return derivation, mappings


def segment(repo: Repo, wf_id: str, seg: str, derivation: cs.Derivation | None = None) -> Segment:
    """Everything a skeleton of `seg` needs. FileNotFoundError when its contract or DAG is missing;
    Unscaffoldable when `seg` is not in `segments/order.json`."""
    derivation, mappings = _load(repo, wf_id, derivation)
    if seg not in derivation.segments:
        raise Unscaffoldable(f"{seg} is not a segment of {wf_id}'s segments/order.json")
    contract = read_json(repo.seg(wf_id, seg, "contract.json"))
    if not isinstance(contract, dict):
        raise Unscaffoldable(f"{wf_id}/{seg}: contract.json is not a JSON object")
    dag = read_json(repo.seg(wf_id, seg, "dag.json"))
    derived = derivation.segments[seg].contract
    sources = {tool: str(entry.get("logical")) for tool, entry in cs._by_tool(mappings.get("sources")).items()
               if entry.get("logical")}
    flow = SegmentFlow(dag, sources)
    notes = [note for note in derivation.notes if note.split(" ", 1)[0].rstrip(":") == seg]
    streams = {str(e["stream"]): str(e["table"]) for e in derived.get("inputs") or [] if e.get("stream") and e.get("table")}

    # Each target's write mode as compile_check.py's `c4:write_mode` resolves it, one target at a time,
    # so a target the contract and the mappings say too little about only costs its own statement.
    writes: dict[str, TargetWrite] = {}
    problems: dict[str, str] = {}
    for entry in contract.get("outputs") or []:
        if not isinstance(entry, dict) or entry.get("kind") != "target" or not entry.get("logical"):
            continue
        try:
            writes.update({w.logical: w for w in target_writes({"outputs": [entry]}, mappings, list(dag.get("nodes") or []))})
        except ContractError as exc:
            problems[str(entry["logical"])] = str(exc)
    written_by_key = {}
    for entry in contract.get("outputs") or []:
        if isinstance(entry, dict):
            written_by_key.setdefault((entry.get("kind"), entry.get("tool_id") or entry.get("stream")), entry)

    outbound = {f"{e.get('src')}_{e.get('src_anchor')}": e for e in dag.get("outbound") or []}
    into = {str(e.get("dst")): e for e in (dag.get("edges") or []) + (dag.get("inbound") or [])}
    outputs = []
    for entry in derived.get("outputs") or []:
        kind = entry.get("kind")
        if kind == "work":
            edge = outbound.get(str(entry.get("stream")))
        else:
            edge = into.get(str(entry.get("tool_id")))
        feed = flow.resolve(str(edge.get("src")), str(edge.get("src_anchor"))) if edge else None
        if feed is not None and feed.kind == "unit":
            flow.chain(feed)                                    # builds every stub it needs
        output = Output(entry=entry, feed=feed, columns=[c["name"] for c in entry.get("columns") or []],
                        written=written_by_key.get((kind, entry.get("tool_id") if kind == "target" else entry.get("stream")), {}))
        if kind == "target":
            output.write = writes.get(str(entry.get("logical")))
            if output.write is None:
                output.problem = problems.get(str(entry.get("logical"))) or (
                    f"target tool {entry.get('tool_id')} has no logical name or write mode in contract.json / "
                    f"intake/mappings.yaml")
                notes.append(f"{seg}: {output.problem}; its write statement is a TODO")
        if feed is None:
            notes.append(f"{seg}: no edge feeds output {entry.get('stream')}; its final SELECT is a TODO")
        outputs.append(output)
    return Segment(wf_id=wf_id, seg=seg, contract=contract, derived=derived, flow=flow, outputs=outputs,
                   streams=streams, notes=notes)


def _session_line(repo: Repo) -> str | None:
    session = ((read_yaml(repo.global_mappings) or {}) if repo.global_mappings.is_file() else {}).get("session") or {}
    parts = []
    for key, value in session.items():
        if not _SESSION_KEY.match(str(key)):
            continue
        if isinstance(value, bool):
            shown = "TRUE" if value else "FALSE"
        elif isinstance(value, (int, float)):
            shown = str(value)
        else:
            shown = "'" + str(value).replace("'", "''") + "'"
        parts.append(f"{key} = {shown}")
    return f"ALTER SESSION SET {', '.join(parts)};" if parts else None


def _stub_comment(unit: Unit, reads: list[str], prefix: str, label: str | None = None) -> list[str]:
    anchor = f" (anchor {_text(unit.anchor, 40)})" if unit.anchor else ""
    annotation = _text(unit.node.get("annotation"))
    head = f"{prefix} tool {unit.path}: {_text(unit.node.get('type'), 40)}{anchor}" + (f" {annotation}" if annotation else "")
    if label:                    # the same stub in another statement differs here (fix round 1, M6)
        head += f" -- for {label}"
    detail = []
    if reads:
        detail.append("reads " + ", ".join(reads))
    if unit.columns is not None:
        detail.append("yields " + ", ".join(_text(c, 80) for c in unit.columns))
    return [head] + ([f"{prefix}   " + "; ".join(detail)] if detail else [])


def _unused_tools(seg: Segment, used: set[str], prefix: str) -> list[str]:
    """A comment per data tool no output of the segment reads (it needs a `tool <id>` comment all the
    same: dbt:tool_comments, and a reader lining the file up with the workflow)."""
    lines = []
    for path in seg.flow.data_paths():
        if path not in used and not any(u.startswith(f"{path}/") for u in used):
            node = seg.flow.node_at(path) or {}
            annotation = _text(node.get("annotation"))
            lines.append(f"{prefix} tool {path}: {_text(node.get('type'), 40)}" + (f" {annotation}" if annotation else "")
                         + " -- feeds no output of this segment, so it has no stub")
    return lines


# --- sql ----------------------------------------------------------------------------------------------


def _sql_read(seg: Segment, read: Read, code: bool = True) -> str:
    """How SQL names what a stub reads. An upstream stream with no C3 table (a derivation gap, noted) is
    the TODO marker where it is code, and a plain description where it is a comment."""
    if read.kind == "unit":
        return seg.flow._units[read.name].cte
    if read.kind == "source":
        return f"IDENTIFIER(:{read.name}_SRC)"
    return seg.streams.get(str(read.name)) or (TODO_MARKER if code else f"(stream {_text(read.name, 60)}: no C3 table)")


def _with_block(seg: Segment, units: list[Unit], indent: str, read_name, prefix: str = "--",
                label: str | None = None) -> list[str]:
    """`WITH` and one stub per unit. `label` (a SQL procedure's statements) names the output the chain is
    written for in every stub comment, so a stub repeated in two statements is still unique in the file."""
    lines = [f"{indent}WITH"]
    macros_seen: set[str] = set()
    for index, unit in enumerate(units):
        if "/" in unit.path:
            macro = unit.path.split("/")[0]
            if macro not in macros_seen:
                macros_seen.add(macro)
                node = seg.flow.node_at(macro) or {}
                annotation = _text(node.get("annotation"))
                lines.append(f"{indent}{prefix} tool {macro}: macro" + (f" {annotation}" if annotation else "")
                             + " -- inlined, one stub per inner tool" + (f" -- for {label}" if label else ""))
        reads = [read_name(r) + (f" ({_text(r.label, 40)})" if r.label else "") for r in unit.reads]
        lines += [indent + line for line in _stub_comment(unit, reads, prefix, label)]
        lines.append(f"{indent}{unit.cte} AS (")
        lines.append(f"{indent}    {TODO_MARKER}")
        lines.append(f"{indent})" + ("," if index < len(units) - 1 else ""))
    return lines


def _select(columns: list[str], source: str, indent: str, ident=_ident) -> list[str]:
    shown = [ident(c) for c in columns] or [TODO_MARKER]
    return ([f"{indent}SELECT"] + [f"{indent}    {c}" + ("," if i < len(shown) - 1 else "") for i, c in enumerate(shown)]
            + [f"{indent}FROM {source}"])


def _slot(tool: str, phase: str, sql_text: str, target: str, prefix: str, indent: str, how: str) -> list[str]:
    return [f"{indent}{prefix} tool {tool}: {phase} ({'before' if phase == 'PreSQL' else 'after'} the write), "
            f"from dag.json: {_text(sql_text, 200)}",
            f"{indent}{prefix}   {how} {target}",
            f"{indent}{TODO_MARKER}" + (";" if prefix == "--" else "")]


def _sql_statement(seg: Segment, output: Output) -> list[str]:
    entry = output.entry
    chain = seg.flow.chain(output.feed) if output.feed is not None and output.feed.kind == "unit" else []
    label = _text(entry.get("logical") if entry.get("kind") == "target" else entry.get("table"), 80)
    source = _sql_read(seg, output.feed) if output.feed is not None else TODO_MARKER
    lines = ["  -- " + "=" * 91]
    if entry.get("kind") == "work":
        lines.append(f"  -- the work stream {_text(entry.get('stream'), 60)}, which another segment reads "
                     f"(contract C3: {entry.get('table')})")
        lines.append("  -- " + "=" * 91)
        lines.append(f"  CREATE OR REPLACE TRANSIENT TABLE {entry.get('table')} AS")
        body = (_with_block(seg, chain, "  ", lambda r: _sql_read(seg, r, False), label=label) if chain else [])
        return lines + body + [line for line in _select(output.columns, source, "  ")[:-1]] + [f"  FROM {source};"]
    tool = str(entry.get("tool_id"))
    node = seg.flow.node_at(tool) or {}
    annotation = _text(node.get("annotation"))
    write = output.write
    if write is None:
        lines.append(f"  -- tool {tool}: output" + (f" {annotation}" if annotation else "")
                     + f" -- its write cannot be derived: {_text(output.problem, 200)}")
        lines.append("  -- " + "=" * 91)
        return lines + [f"  {TODO_MARKER};"]
    target = f"IDENTIFIER(:{write.logical}_TGT)"
    mode = write.spelling + (f" ({write.mode})" if write.spelling != write.mode else "")
    lines.append(f"  -- tool {tool}: output" + (f" {annotation}" if annotation else "")
                 + f" -- target {write.logical}, write mode {mode}")
    lines.append("  -- " + "=" * 91)
    config = node.get("config") or {}
    how = "rewrite it as its own statement against"
    if write.pre_sql:
        lines += _slot(tool, "PreSQL", config.get("pre_sql"), target, "--", "  ", how) + [""]
    columns = ", ".join(_ident(c) for c in output.columns) or TODO_MARKER
    if write.mode == "overwrite":
        lines.append(f"  CREATE OR REPLACE TABLE {target} AS")
    elif write.mode in ("append", "truncate_append"):
        if write.mode == "truncate_append":
            lines += [f"  TRUNCATE TABLE {target};", ""]
        lines += [f"  INSERT INTO {target}", f"      ({columns})"]
    if write.mode != "update_insert":
        body = _with_block(seg, chain, "  ", lambda r: _sql_read(seg, r, False), label=label) if chain else []
        lines += body + _select(output.columns, source, "  ")[:-1] + [f"  FROM {source};"]
    else:
        keys = [k.upper() for k in write.keys]
        rest = [c for c in output.columns if c.upper() not in keys] or list(output.columns[:1] or keys[:1])
        lines += [f"  MERGE INTO {target} AS T", "  USING ("]
        lines += (_with_block(seg, chain, "    ", lambda r: _sql_read(seg, r, False), label=label) if chain else [])
        lines += _select(output.columns, source, "    ")
        lines += ["  ) AS S",
                  "  ON " + " AND ".join(f"T.{_ident(k)} = S.{_ident(k)}" for k in keys),
                  "  WHEN MATCHED THEN UPDATE SET"]
        lines += [f"      {_ident(c)} = S.{_ident(c)}" + ("," if i < len(rest) - 1 else "") for i, c in enumerate(rest)]
        lines += [f"  WHEN NOT MATCHED THEN INSERT ({columns})",
                  "      VALUES (" + ", ".join(f"S.{_ident(c)}" for c in output.columns) + ");"]
    if write.post_sql:
        lines += [""] + _slot(tool, "PostSQL", config.get("post_sql"), target, "--", "  ", how)
    return lines


_SKELETON_NOTE = ("the orchestrator's skeleton (scripts/translation_scaffold.py). Every line outside a TODO body is "
                  "mechanical: {what}. Replace each TODO body -- the one fixed marker compile_check.py refuses "
                  "(scaffold:todo) -- with that tool's transformation, and change nothing else. Nothing in this "
                  "file has ever run on Snowflake or on Alteryx.")


def _wrap(prefix: str, text: str, width: int = 104) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    return [f"{prefix} {line}" for line in lines + ([line] if line else [])]


def render_sql(repo: Repo, wf_id: str, seg_id: str, derivation: cs.Derivation | None = None) -> str:
    """The `proc.sql` skeleton of one segment."""
    seg = segment(repo, wf_id, seg_id, derivation)
    what = ("the header, the session line, the LET lines, each write statement (its head, its CTE names, its "
            "final SELECT, a MERGE's clauses) and the RETURN; a TODO statement is the Output tool's PreSQL or "
            "PostSQL, rewritten against the IDENTIFIER its comment names. An ORDER BY may follow a final "
            "SELECT's FROM when the output's order matters")
    lines = _wrap("--", f"{wf_id} / {seg_id} -- " + _SKELETON_NOTE.format(what=what))
    lines += [f"CREATE OR REPLACE PROCEDURE {seg.proc}(",
              "    SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)",
              "RETURNS STRING", "LANGUAGE SQL", "EXECUTE AS CALLER", "AS", "$$", "BEGIN", ""]
    session = _session_line(repo)
    if session:
        lines += [f"  {session}", ""]
    sources = list(dict.fromkeys(str(e["logical"]) for e in seg.derived.get("inputs") or [] if e.get("logical")))
    targets = list(dict.fromkeys(o.write.logical for o in seg.outputs if o.write is not None))
    if sources or targets:
        lines.append("  -- contract C4: each mapped table's name is built once, then referenced as IDENTIFIER(:<name>)")
        lines += [f"  LET {name}_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.{name}';" for name in sources]
        lines += [f"  LET {name}_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.{name}';" for name in targets]
        lines.append("")
    used = {u.path for o in seg.outputs if o.feed is not None and o.feed.kind == "unit" for u in seg.flow.chain(o.feed)}
    unused = _unused_tools(seg, used, "  --")
    if unused:
        lines += unused + [""]
    for output in seg.outputs:
        lines += _sql_statement(seg, output) + [""]
    lines += ["  RETURN 'OK';", "END;", "$$;"]
    return "\n".join(lines) + "\n"


# --- snowpark -----------------------------------------------------------------------------------------


def render_snowpark(repo: Repo, wf_id: str, seg_id: str, derivation: cs.Derivation | None = None) -> str:
    """The `proc.py` skeleton of one Snowpark segment."""
    seg = segment(repo, wf_id, seg_id, derivation)
    flow = seg.flow
    what = ("the signature, every read (session.table), every write (save_as_table, merge) and the return. Assign "
            "each out_<stream> DataFrame a stub names; the columns of a DataFrame you create are the contract's, in "
            "the contract's order")
    lines = _wrap("#", f"{wf_id} / {seg_id} -- " + _SKELETON_NOTE.format(what=what))
    merges = any(o.write is not None and o.write.mode == "update_insert" for o in seg.outputs)
    if merges:
        lines.append("from snowflake.snowpark.functions import when_matched, when_not_matched")
    lines += ["", "", "def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):"]
    for entry in seg.derived.get("inputs") or []:
        if entry.get("logical"):
            lines.append(f"    # the mapped source {entry['logical']} (intake/mappings.yaml)")
            lines.append(f'    src_{_var(entry["logical"])} = session.table(f"{{src_db}}.{{src_schema}}.{entry["logical"]}")')
        elif entry.get("stream") and entry.get("table"):
            lines.append(f"    # {_text(entry.get('from'), 40)}'s work stream {_text(entry['stream'], 60)}")
            lines.append(f'    in_{_var(entry["stream"])} = session.table("{entry["table"]}")')

    assigns: dict[str, list[str]] = {}
    for output in seg.outputs:
        if output.feed is None:
            continue
        stream = str(output.entry.get("stream"))
        owner = flow._units[output.feed.name].path.split("/")[0] if output.feed.kind == "unit" else None
        if owner is not None:
            names = assigns.setdefault(owner, [])
            text = f"out_{_var(stream)} ({', '.join(_text(c, 80) for c in output.columns)})"
            if text not in names:
                names.append(text)

    # one stub per segment data tool, in DAG order
    order = _top_level_order(flow)
    for path in order:
        node = flow.node_at(path) or {}
        annotation = _text(node.get("annotation"))
        lines += ["", f"    # tool {path}: {_text(node.get('type'), 40)}" + (f" {annotation}" if annotation else "")]
        if path in flow.sources:
            reads = [f"src_{_var(flow.sources[path])}"]
        else:
            reads = []
            feeds = flow.feeds(path)
            for edge in feeds:
                src, anchor = str(edge.get("src")), str(edge.get("src_anchor"))
                if flow.node_at(src) is None:
                    shown = "in_" + _var(f"{src}_{anchor}")
                else:
                    multi = _multi_anchor(flow.node_at(src) or {})
                    shown = f"tool {src}" + (f" (anchor {_text(anchor, 40)})" if multi else "")
                if len(feeds) > 1:
                    shown += f" as {_text(edge.get('dst_anchor'), 40)} #{edge.get('dst_order')}"
                reads.append(shown)
        if reads:
            lines.append("    #   reads " + ", ".join(reads))
        for text in assigns.get(path, []):
            lines.append(f"    #   assign {text}")
        lines.append(f"    {TODO_MARKER}")

    # an output no stub yields: fed straight from an upstream stream (mechanical), or by nothing (a TODO)
    frames: set[str] = set()
    for output in seg.outputs:
        frame = f"out_{_var(str(output.entry.get('stream')))}"
        if frame in frames or (output.feed is not None and output.feed.kind == "unit"):
            continue
        frames.add(frame)
        if output.feed is not None and output.feed.kind == "stream" and seg.streams.get(str(output.feed.name)):
            lines += ["", f"    {frame} = in_{_var(str(output.feed.name))}"]
        else:
            lines += ["", f"    # nothing in this segment yields {_text(output.entry.get('stream'), 60)}: assign {frame}",
                      f"    {TODO_MARKER}"]

    for output in seg.outputs:
        lines += [""] + _snowpark_write(seg, output)
    lines.append('    return "OK"')
    return "\n".join(lines) + "\n"


def _top_level_order(flow: SegmentFlow) -> list[str]:
    paths = flow.data_paths()
    deps = {p: {str(e.get("src")) for e in flow.feeds(p) if str(e.get("src")) in paths} for p in paths}
    ready = [(cs.tool_key(p), p) for p in paths if not deps[p]]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        _, path = heapq.heappop(ready)
        ordered.append(path)
        for other in paths:
            if path in deps[other]:
                deps[other].discard(path)
                if not deps[other] and other not in ordered:
                    heapq.heappush(ready, (cs.tool_key(other), other))
    return ordered + [p for p in paths if p not in ordered]          # a cycle keeps its tools, unordered


def _snowpark_write(seg: Segment, output: Output) -> list[str]:
    entry = output.entry
    frame = f"out_{_var(str(entry.get('stream')))}"
    if entry.get("kind") == "work":
        return [f"    # the work stream {_text(entry.get('stream'), 60)}, which another segment reads (contract C3)",
                f'    {frame}.write.mode("overwrite").save_as_table("{entry.get("table")}")']
    tool = str(entry.get("tool_id"))
    node = seg.flow.node_at(tool) or {}
    annotation = _text(node.get("annotation"))
    write = output.write
    head = f"    # tool {tool}: output" + (f" {annotation}" if annotation else "")
    if write is None:
        return [head + f" -- its write cannot be derived: {_text(output.problem, 200)}", f"    {TODO_MARKER}"]
    mode = write.spelling + (f" ({write.mode})" if write.spelling != write.mode else "")
    lines = [head + f" -- target {write.logical}, write mode {mode}"]
    name = f'f"{{tgt_db}}.{{tgt_schema}}.{write.logical}"'
    table = f"tgt_{_var(write.logical)}"
    config = node.get("config") or {}
    if write.pre_sql or write.post_sql or write.mode == "update_insert":
        lines.append(f"    {table} = session.table({name})")
    how = f"rewrite it as a {table}.update(...) or {table}.delete(...) of"
    if write.pre_sql:
        lines += _slot(tool, "PreSQL", config.get("pre_sql"), write.logical, "#", "    ", how)
    if write.mode == "update_insert":
        keys = [k.upper() for k in write.keys]
        rest = [c for c in output.columns if c.upper() not in keys] or list(output.columns[:1] or keys[:1])
        join = " & ".join(f"({table}[{_py_name(k)}] == {frame}[{_py_name(k)}])" for k in keys)
        update = ", ".join(f"{_py_name(c)}: {frame}[{_py_name(c)}]" for c in rest)
        insert = ", ".join(f"{_py_name(c)}: {frame}[{_py_name(c)}]" for c in output.columns)
        lines += [f"    {table}.merge(",
                  f"        {frame},",
                  f"        {join},",
                  f"        [when_matched().update({{{update}}}),",
                  f"         when_not_matched().insert({{{insert}}})],",
                  "    )"]
    else:
        save_mode = {"overwrite": "overwrite", "append": "append", "truncate_append": "truncate"}[write.mode]
        lines.append(f'    {frame}.write.mode("{save_mode}").save_as_table({name})')
    if write.post_sql:
        lines += _slot(tool, "PostSQL", config.get("post_sql"), write.logical, "#", "    ", how)
    return lines


# --- dbt ----------------------------------------------------------------------------------------------


def _yaml_name(name: str) -> str:
    """A name as a YAML string: bare only when YAML reads it back as exactly that string (fix round 1,
    I2: `NO`, `ON`, `YES`, `OFF`, `TRUE`, `NULL` are booleans or null to YAML 1.1), else JSON-quoted -- a
    JSON string is a YAML double-quoted scalar. A name holding a brace (Jinja) is the TODO marker."""
    if unwritable(name, "dbt"):
        return TODO_MARKER
    try:
        same = _PLAIN.match(name) is not None and yaml.safe_load(name) == name
    except yaml.YAMLError:
        same = False
    return name if same else json.dumps(name)


def _dbt_key(name: str) -> str:
    """A `unique_key` entry: dbt splices it into SQL as written, so a name that needs quotes carries them."""
    return TODO_MARKER if unwritable(name, "dbt") else ('"' + name + '"' if needs_quotes(name) else name)


def _mode_text(output: Output) -> str:
    """One spelling of a target's write mode in every comment (fix round 1, N1): the contract's, with the
    form it resolves to when that differs."""
    if output.write is not None:
        return output.write.spelling + (f" ({output.write.mode})" if output.write.spelling != output.write.mode else "")
    return str(output.entry.get("write_mode"))


def _materialization(output: Output, mapping: dict | None) -> list[str] | None:
    """A model's `config(...)` arguments for its write mode (`dbt_project.expected_model_config`, the
    mapping's own key order as the committed sample writes it); None for a mode dbt cannot express."""
    entry = output.entry
    if entry.get("kind") != "target":
        return ["materialized='table'"]
    try:
        expected = dbt_project.expected_model_config(str((mapping or {}).get("mode")), (mapping or {}).get("keys") or [],
                                                     str(entry.get("logical")))
    except ValueError:
        return None
    if "unique_key" in expected:
        expected["unique_key"] = [_dbt_key(str(k).upper()) for k in (mapping or {}).get("keys") or []]
    return [f"{key}={value!r}" for key, value in expected.items()]


def _config(output: Output, mapping: dict | None) -> str:
    """The model's exact config line: its materialization, and a TODO `pre_hook`/`post_hook` where the
    Output tool has a PreSQL/PostSQL. A write mode dbt cannot express is a TODO config."""
    parts = _materialization(output, mapping)
    if parts is None:
        return "{{ config(" + TODO_MARKER + ") }}"
    # double-quoted, so a PreSQL's own string literals need no change of quote style (fix round 1, N2)
    if output.write is not None and output.write.pre_sql:
        parts.append(f'pre_hook="{TODO_MARKER}"')
    if output.write is not None and output.write.post_sql:
        parts.append(f'post_hook="{TODO_MARKER}"')
    return "{{ config(" + ", ".join(parts) + ") }}"


def _dbt_read(seg: Segment, read: Read, code: bool = True) -> str:
    """How a model names what a stub reads (see `_sql_read` for a stream with no C3 table)."""
    if read.kind == "unit":
        return seg.flow._units[read.name].cte
    if read.kind == "source":
        return f"src_{_var(str(read.name))}"
    table = seg.streams.get(str(read.name))
    if table:
        return f"ref_{dbt_project.model_name({'kind': 'work', 'table': table})}"
    return TODO_MARKER if code else f"(stream {_text(read.name, 60)}: no C3 table)"


def _dbt_model(seg: Segment, output: Output, name: str, mapping: dict | None) -> str:
    entry = output.entry
    chain = seg.flow.chain(output.feed) if output.feed is not None and output.feed.kind == "unit" else []
    if entry.get("kind") == "work":
        what = f"{seg.seg}'s work stream {_text(entry.get('stream'), 60)}, which lands in {entry.get('table')}"
    else:
        what = f"{seg.seg}'s target {entry.get('logical')} (tool {entry.get('tool_id')}, {_mode_text(output)})"
    note = "the config line, the source()/ref() reads, the CTE names and the final SELECT"
    config_line = _config(output, mapping)
    if "_hook=" in config_line:
        note += ("; a TODO hook in the config line is the Output tool's PreSQL/PostSQL, one statement against this "
                 "model's own table (the comment below quotes it)")
    elif TODO_MARKER in config_line:
        note += "; the TODO config is a write mode dbt cannot express (see translator.agent.md)"
    lines = [config_line]
    lines += _wrap("--", f"models/{name}.sql -- {seg.wf_id} / {what}: " + _SKELETON_NOTE.format(what=note))
    node = (seg.flow.node_at(str(entry.get("tool_id"))) or {}) if entry.get("kind") == "target" else {}
    config = node.get("config") or {}
    if output.write is not None and output.write.pre_sql:
        lines.append(f"-- tool {entry.get('tool_id')}: PreSQL, the pre_hook: {_text(config.get('pre_sql'), 200)}")
    if output.write is not None and output.write.post_sql:
        lines.append(f"-- tool {entry.get('tool_id')}: PostSQL, the post_hook: {_text(config.get('post_sql'), 200)}")

    reads = []
    for unit in chain:
        reads += [r for r in unit.reads if r.kind in ("source", "stream")]
    if output.feed is not None and output.feed.kind == "stream":
        reads.append(output.feed)
    wanted = {(r.kind, r.name) for r in reads}
    read_ctes = []
    for input_entry in seg.derived.get("inputs") or []:
        columns = [c["name"] for c in input_entry.get("columns") or []]
        if input_entry.get("logical") and ("source", input_entry["logical"]) in wanted:
            logical = input_entry["logical"]
            read_ctes.append((f"-- the mapped source {logical} (intake/mappings.yaml)", f"src_{_var(logical)}", columns,
                              f"{{{{ source('src', '{logical}') }}}}"))
            wanted.discard(("source", logical))
        elif input_entry.get("stream") and ("stream", input_entry["stream"]) in wanted:
            model = dbt_project.model_name({"kind": "work", "table": input_entry["table"]})
            read_ctes.append((f"-- {_text(input_entry.get('from'), 40)}'s work stream {_text(input_entry['stream'], 60)}, "
                              f"the model {model}", f"ref_{model}", columns, f"{{{{ ref('{model}') }}}}"))
            wanted.discard(("stream", input_entry["stream"]))
    body = ["WITH"] if (read_ctes or chain) else []
    for index, (comment, cte, columns, relation) in enumerate(read_ctes):
        body += [comment, f"{cte} AS ("] + _select(columns, relation, "    ", _dbt_ident) + [")" + ("," if index < len(read_ctes) - 1 or chain else "")]
    if chain:
        body += _with_block(seg, chain, "", lambda r: _dbt_read(seg, r, False))[1:]
    lines += body
    if entry.get("kind") == "target":
        annotation = _text(node.get("annotation"))
        lines.append(f"-- tool {entry.get('tool_id')}: output" + (f" {annotation}" if annotation else "")
                     + f" -- target {entry.get('logical')}, write mode {_mode_text(output)}")
    source = _dbt_read(seg, output.feed) if output.feed is not None else TODO_MARKER
    lines += _select(output.columns, source, "", _dbt_ident)
    return "\n".join(lines) + "\n"


def _yaml_columns(columns: list[dict], keys: list[str], indent: str) -> list[str]:
    lines = [f"{indent}columns:"]
    single_key = [k.upper() for k in keys] if len(keys) == 1 else []
    for column in columns:
        name = str(column.get("name")).upper()
        lines.append(f"{indent}  - name: {_yaml_name(name)}")
        tests = (["not_null"] if column.get("nullable") is False else []) + (["unique"] if name in single_key else [])
        if tests:
            lines.append(f"{indent}    data_tests:")
            lines += [f"{indent}      - {test}" for test in tests]
    return lines


def render_dbt(repo: Repo, wf_id: str) -> dict[str, str]:
    """The dbt project skeleton of the whole workflow: {path under dbt/: text}."""
    derivation, mappings = _load(repo, wf_id)
    outputs_map = {str(entry.get("logical")): entry for entry in (mappings.get("outputs") or {}).values()
                   if isinstance(entry, dict) and entry.get("logical")}
    files = {"profiles.yml": dbt_project.PROFILES_TEMPLATE}
    schema = ["version: 2", "models:"]
    table_rows = []
    source_columns: dict[str, list[str]] = {}
    for seg_id in derivation.order:
        seg = segment(repo, wf_id, seg_id, derivation)
        written_inputs = [e for e in seg.contract.get("inputs") or [] if isinstance(e, dict)]
        for entry in seg.derived.get("inputs") or []:
            if entry.get("logical") and entry["logical"] not in source_columns:
                theirs = next((w for w in written_inputs if w.get("logical") == entry["logical"]), {})
                source_columns[entry["logical"]] = [str(c.get("name")).upper() for c in
                                                    (theirs.get("columns") or entry.get("columns") or [])]
        used: set[str] = set()
        first_model = None
        for output in seg.outputs:
            entry = output.entry
            name = dbt_project.model_name(entry)
            mapping = outputs_map.get(str(entry.get("logical"))) if entry.get("kind") == "target" else None
            files[f"models/{name}.sql"] = _dbt_model(seg, output, name, mapping)
            first_model = first_model or f"models/{name}.sql"
            if output.feed is not None and output.feed.kind == "unit":
                used |= {u.path for u in seg.flow.chain(output.feed)}
            if entry.get("kind") == "work":
                description = f"{seg_id}'s work stream {_text(entry.get('stream'), 60)} ({entry.get('table')})."
                table_rows.append(f"| `models/{name}.sql` | `{seg_id}` work stream `{_text(entry.get('stream'), 60)}` | `table` |")
            else:
                mode = _mode_text(output)
                description = f"Tool {entry.get('tool_id')}, {mode}: logical {entry.get('logical')}."
                parts = _materialization(output, mapping)
                shown = f"`{', '.join(parts)}`" if parts else "none: dbt cannot express this write mode"
                table_rows.append(f"| `models/{name}.sql` | `{seg_id}` target `{entry.get('logical')}` "
                                  f"(tool {entry.get('tool_id')}, {mode}) | {shown} |")
            columns = output.written.get("columns") if isinstance(output.written.get("columns"), list) else None
            columns = columns or [{"name": c, "nullable": True} for c in output.columns]
            schema += [f"  - name: {_yaml_name(name)}", f"    description: {json.dumps(_text(description, 300))}"]
            schema += _yaml_columns([c for c in columns if isinstance(c, dict)],
                                    [str(k) for k in output.written.get("keys") or []], "    ")
        unused = _unused_tools(seg, used, "--")
        if unused and first_model:
            first, rest = files[first_model].split("\n", 1)
            files[first_model] = "\n".join([first] + unused + [rest])
    files["models/schema.yml"] = "\n".join(schema) + "\n"
    sources = ["version: 2", "sources:", "  - name: src", "    schema: \"{{ var('src_schema') }}\"", "    tables:"]
    logicals = list(dict.fromkeys(str(e.get("logical")) for e in (mappings.get("sources") or {}).values()
                                  if isinstance(e, dict) and e.get("logical")))
    for logical in logicals:
        sources.append(f"      - name: {_yaml_name(logical)}")
        if source_columns.get(logical):
            sources.append("        columns:")
            sources += [f"          - name: {_yaml_name(c)}" for c in source_columns[logical]]
    files["models/sources.yml"] = "\n".join(sources) + "\n"
    files["README.md"] = _dbt_readme(wf_id, logicals, table_rows)
    # Last (fix round 1, M4): the orchestrator scaffolds only while dbt_project.yml is missing, so a run
    # killed half-way leaves no project file and the next run completes the skeleton.
    files["dbt_project.yml"] = dbt_project.project_yml(wf_id)
    return files


def _dbt_readme(wf_id: str, sources: list[str], rows: list[str]) -> str:
    return "\n".join([
        f"# {wf_id} -- a dbt project",
        "",
        "The whole workflow migrated as one dbt project (`output_kind: dbt`): one model per contract output, run in",
        "dbt's own dependency order. There is no master.sql and no per-segment procedure. Nothing in this project has",
        "run on a real Snowflake account or a real Alteryx engine; the local checks run it on DuckDB (dbt-duckdb).",
        "",
        "| Model | Contract output | Materialisation |",
        "|---|---|---|",
        *rows,
        "",
        "## Running it",
        "",
        "Against Snowflake (the `snowflake` output of `profiles.yml`; dbt-snowflake is not installed in this repository):",
        "",
        "```",
        f"dbt run --project-dir workflows/{wf_id}/dbt --profiles-dir workflows/{wf_id}/dbt --target snowflake "
        "--vars '{\"src_schema\": \"<SRC>\", \"tgt_schema\": \"<TGT>\"}'",
        "```",
        "",
        "Every connection value comes from a `SNOWFLAKE_*` environment variable when dbt runs; `profiles.yml` holds no",
        "credential. Locally (the `local` output, DuckDB), the way the pipeline runs it:",
        "",
        "```",
        f"python scripts/validate_dbt.py {wf_id}",
        "```",
        "",
        "## What `--vars` mean",
        "",
        "- `src_schema` -- the schema that holds the mapped sources under their logical names"
        + (f" ({', '.join(f'`{s}`' for s in sources)})." if sources else "."),
        "- `tgt_schema` -- where every model is written.",
        "",
    ])


# --- writing ------------------------------------------------------------------------------------------


def _write_new(repo: Repo, path, text: str, report: list[str]) -> None:
    rel = path.relative_to(repo.root).as_posix()
    if path.exists():
        report.append(f"kept {rel} (it exists; the scaffold never writes over a file)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    report.append(f"wrote {rel}")


def _unwritable_notes(info: Segment, target: str) -> list[str]:
    """A note per column name the target's code cannot hold (`unwritable`): every place it would be
    named is the TODO marker, for a human or the translator to resolve (the name is shown sanitised)."""
    names = [c["name"] for section in ("inputs", "outputs") for entry in info.derived.get(section) or []
             for c in entry.get("columns") or []]
    what = "a brace, which dbt reads as Jinja" if target == "dbt" else "`$$`, which ends the procedure body"
    return [f"{info.seg}: column {_text(name, 80)} holds {what}; every mechanical line naming it is a TODO"
            for name in dict.fromkeys(names) if unwritable(name, target)]


def scaffold(repo: Repo, wf_id: str, seg_id: str | None = None, dbt: bool | None = None) -> tuple[list[str], list[str]]:
    """Writes the skeleton(s) `main` would, never over an existing file. Returns (report lines, notes).
    `dbt` True writes the workflow's dbt project, False procedures; None decides from `manifest.json`."""
    derivation = cs.derive(repo, wf_id)
    if dbt and seg_id is not None:
        raise Unscaffoldable("--dbt (the whole project) and --segment (one procedure): give one or the other")
    if dbt is None:
        dbt = seg_id is None and load_manifest(repo, wf_id).get("output_kind") == "dbt"
        if seg_id is not None and load_manifest(repo, wf_id).get("output_kind") == "dbt":
            raise Unscaffoldable(f"{wf_id} is a dbt workflow: its project is one unit, scaffolded without --segment")
    report: list[str] = []
    notes: list[str] = []
    if dbt:
        for seg in derivation.order:                       # every contract must exist before anything is written
            info = segment(repo, wf_id, seg, derivation)
            notes += info.notes + _unwritable_notes(info, "dbt")
        files = render_dbt(repo, wf_id)
        for rel, text in files.items():
            _write_new(repo, repo.wf(wf_id, "dbt", *rel.split("/")), text, report)
        return report, notes
    scope = derivation.order if seg_id is None else [seg_id]
    rendered = []
    for seg in scope:
        info = segment(repo, wf_id, seg, derivation)      # raises before anything is written
        target = info.contract.get("target") or "sql"
        if target == "manual":
            notes.append(f"{seg}: contract target is manual -- no generator may attempt it, so it gets no skeleton")
            continue
        notes += info.notes + _unwritable_notes(info, "snowpark" if target == "snowpark" else "sql")
        if target == "snowpark":
            rendered.append((repo.seg(wf_id, seg, "proc.py"), render_snowpark(repo, wf_id, seg, derivation)))
        else:
            rendered.append((repo.seg(wf_id, seg, "proc.sql"), render_sql(repo, wf_id, seg, derivation)))
    for path, text in rendered:
        _write_new(repo, path, text, report)
    return report, notes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id")
    parser.add_argument("--segment", help="one segment's procedure")
    parser.add_argument("--dbt", action="store_true", help="the workflow's dbt project (one unit)")
    add_root_arg(parser)
    args = parser.parse_args(argv)
    try:
        repo = Repo(args.root)
        report, notes = scaffold(repo, args.wf_id, args.segment, True if args.dbt else None)
    except FileNotFoundError as exc:
        print(f"translation_scaffold: {args.wf_id} cannot be scaffolded yet: {exc}", file=sys.stderr)
        return 2
    except (Unscaffoldable, ValueError) as exc:
        print(f"translation_scaffold: {exc}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 2
    for line in report:
        print(line)
    for note in dict.fromkeys(notes):
        print(f"note: {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
