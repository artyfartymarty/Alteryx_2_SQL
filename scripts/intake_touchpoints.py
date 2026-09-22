"""Enumerate every external touchpoint of a parsed workflow and propose Snowflake candidates for
each one (program spec §5, plan contracts C6/C8).

    python scripts/intake_touchpoints.py <wf_id> [--yxdb-dir DIR]... [--root .]

This is pure enumeration and scoring: it never asks a question and never writes a mapping.
`scripts/intake_prompt.py` is what asks the workflow owner which Snowflake table each touchpoint
is, using the candidates this script proposes; this script only reads `parsed/dag.json` (and, for
`.yxdb` inputs it can find on disk, the yxdb header) and writes `intake/touchpoints.json`.

A *touchpoint* is one external thing the workflow reads or writes, or one thing whose value a
translator would otherwise have to guess: an Input/Output Data tool, a macro, a Run Command or
unrecognised ("unknown") tool, a workflow constant, or an App/workflow parameter (an `interface`
tool at the top level of the dag -- not one nested inside a macro's own `sub_dag`, which is that
macro's private business). Only `input` and `output` touchpoints are `blocking`: everything else is
listed for `intake/plan.md` (a later task's concern) but never holds up `intake_prompt.py`'s status.

**DB touchpoint keys (contract C8).** `normalize_key` always returns the bare `alias:<alias>` for
any DB-backed config, per C8's own definition. But a single DB alias/connection can serve many
tables, so a DB *output* touchpoint's `key` -- the thing `intake/mappings.yaml["outputs"]` and
`mappings/global.yaml["outputs"]` are keyed by -- is `normalize_key(config) + "/" + table.lower()`,
e.g. `alias:prod_fin/dbo.gl_summary`; a DB *input*'s key stays the plain `alias:<alias>` (mirroring
contract C8's own worked example, which only shows the bare alias). That extra suffix is applied
here in `_output_touchpoint`, not inside `normalize_key` itself, which stays a faithful, direct
implementation of C8 and nothing more.

Nothing here has run against a real Snowflake account or Alteryx Designer: `load_catalog`'s default
path reads `catalog/columns.csv`, a hand-written stand-in for `INFORMATION_SCHEMA.COLUMNS` (see
`catalog/README.md`); the `backend`-argument path reads the same shape from `MIG_WORK.CATALOG_COLUMNS`,
a table a separate, human-run job is expected to publish, because the `MIGRATION_AGENT` role is
deliberately not granted `INFORMATION_SCHEMA` access on production databases.

**An output must never quietly default onto a source (coordinator ruling, post-review).** Column
overlap alone cannot tell an input's table from an output's, because an output's schema is very
often a reshaping of some input's -- offering `SALES.RAW.ORDERS` as a candidate for an *output* that
happens to share most of its columns would let a careless Enter overwrite the workflow's own input.
`propose_candidates` therefore drops, from an output's column-backed ranking only, any table in
`program.raw_schema` (default `RAW`) or named in `exclude`; `run` computes `exclude` from
`known_input_sources` (every input's resolved/top-candidate table) before scoring any output. The
naming-convention candidate is never filtered this way -- it's a proposal to confirm, not a lookup.
`intake_prompt.py` layers two more defences on top: Enter only ever auto-accepts an output onto a
*full* column match, and typing or choosing a self-referencing table anyway requires an explicit
confirmation, recorded as `self_reference: true`.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import traceback
from pathlib import Path
from typing import Sequence

from lib import io as lib_io
from lib import yxdb
from lib.paths import Repo, add_root_arg

# `<File FileFormat="…">` codes already resolve to "yxdb"/"csv"/"xlsx"/"db" in dag-contract's
# `config["format"]`; nothing here re-reads the raw code.
_DRIVE_RE = re.compile(r"^[a-z]:/(.*)$")
_FROM_RE = re.compile(r"(?i)\bFROM\s+([A-Za-z_][A-Za-z0-9_.\"$]*)")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

_TOUCHPOINT_KEYS = (
    "id", "kind", "tool_id", "annotation", "format", "source", "key", "fields", "field_source",
    "record_count", "query", "table", "write_mode", "keys", "pre_sql", "post_sql", "blocking",
    "resolved", "candidates",
)


def _tp(**kwargs) -> dict:
    """One touchpoint dict with every key the brief's shape defines, defaulted, then overridden."""
    tp = {
        "id": None, "kind": None, "tool_id": None, "annotation": None, "format": None,
        "source": None, "key": None, "fields": [], "field_source": None, "record_count": None,
        "query": None, "table": None, "write_mode": None, "keys": [], "pre_sql": None,
        "post_sql": None, "blocking": False, "resolved": None, "candidates": [],
    }
    tp.update(kwargs)
    return tp


# --- contract C8: normalized source key ---------------------------------------------------------

def normalize_key(config: dict) -> str:
    """Contract C8. DB configs (`format: "db"`) -> `alias:<alias>`. File configs -> lower-case,
    backslashes to `/`, a drive letter or leading `//` stripped, then the last two path
    components: `C:\\data\\sales\\orders.yxdb` -> `sales/orders.yxdb`."""
    fmt = config.get("format")
    if fmt == "db":
        alias = (config.get("alias") or "").strip()
        return f"alias:{alias}"
    return _file_key((config.get("source") or "").strip())


def _file_key(source: str) -> str:
    s = source.strip().lower().replace("\\", "/")
    m = _DRIVE_RE.match(s)
    if m:
        s = m.group(1)
    elif s.startswith("//"):
        s = s.lstrip("/")
    parts = [p for p in s.split("/") if p]
    return "/".join(parts[-2:])


# --- catalog --------------------------------------------------------------------------------------

def load_catalog(repo: Repo, backend=None) -> list[dict]:
    """Rows of `{database, schema, table, column, data_type, row_count}`.

    `backend` given: read the same shape from `MIG_WORK.CATALOG_COLUMNS` (a sanitized catalog a
    separate job publishes -- the agent role has no `INFORMATION_SCHEMA` access on its own).
    `backend` omitted: read `catalog/columns.csv`, or `[]` if it doesn't exist.
    """
    if backend is not None:
        columns, db_rows = backend.query(
            "SELECT database, schema, table, column, data_type, row_count "
            "FROM MIG_WORK.CATALOG_COLUMNS")
        lower_cols = [c.lower() for c in columns]
        return [dict(zip(lower_cols, row)) for row in db_rows]

    path = repo.root / "catalog" / "columns.csv"
    if not path.is_file():
        return []
    rows = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            raw_count = (row.get("row_count") or "").strip()
            rows.append({
                "database": row["database"], "schema": row["schema"], "table": row["table"],
                "column": row["column"], "data_type": row["data_type"],
                "row_count": int(raw_count) if raw_count else None,
            })
    return rows


# --- locating a yxdb file on disk ------------------------------------------------------------------

def _safe_basename(source: str) -> str | None:
    """The file-name component of a Windows path, refusing anything that isn't a plain name (dag-
    derived text is never trusted into a path unsanitised): empty, `.`/`..`, or still containing a
    separator after splitting on both `\\` and `/`."""
    if not source:
        return None
    name = re.split(r"[\\/]+", source.strip())[-1]
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return None
    return name


def find_yxdb(repo: Repo, wf_id: str, source: str, extra_dirs: Sequence[Path] = ()) -> Path | None:
    """The literal `source` path if it exists here, else `workflows/<wf_id>/source/data/<basename>`
    (what `dev.build_samples` and a real capture both populate), else `<dir>/<basename>` for each of
    `extra_dirs` in order. Never raises: a source that can't be found, or whose basename is unsafe
    to join to a path, is reported as "not found", never an error and never a guess.
    """
    if source:
        try:
            literal = Path(source)
            if literal.is_file():
                return literal
        except OSError:
            pass
    basename = _safe_basename(source)
    if basename is None:
        return None
    candidates = [repo.wf(wf_id, "source", "data", basename)]
    candidates += [Path(d) / basename for d in extra_dirs]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


# --- field lists ------------------------------------------------------------------------------------

def _meta_fields(node: dict, anchor: str) -> list[str] | None:
    fields = (node.get("meta") or {}).get(anchor)
    if not fields:
        return None
    names = [f.get("name") for f in fields if f.get("name")]
    return names or None


def _upstream_meta_fields(dag: dict, tool_id: str) -> list[str] | None:
    """The field list of whatever feeds `tool_id`'s `Input` anchor, read from *that* node's own
    meta for the anchor the edge actually leaves from -- used when a sink tool (an Output whose
    target doesn't exist yet) carries no `MetaInfo` of its own."""
    node_by_id = {n["tool_id"]: n for n in dag.get("nodes", [])}
    for edge in dag.get("edges", []):
        if edge["dst"] != tool_id:
            continue
        src = node_by_id.get(edge["src"])
        if src is None:
            continue
        fields = _meta_fields(src, edge["src_anchor"])
        if fields:
            return fields
    return None


_BRACKET_RE = re.compile(r"\[([^\]]+)\]")


def _downstream_fields(dag: dict, tool_id: str) -> list[str]:
    """Best-effort fallback when a touchpoint has no yxdb header and no meta at all: the field
    names referenced by the nearest downstream select/join/formula tool. Walks forward from
    `tool_id` breadth-first and stops at the first such tool; returns `[]` if none is reached.
    """
    node_by_id = {n["tool_id"]: n for n in dag.get("nodes", [])}
    edges = dag.get("edges", [])
    queue = [tool_id]
    seen = {tool_id}
    while queue:
        current = queue.pop(0)
        for edge in edges:
            if edge["src"] != current or edge["dst"] in seen:
                continue
            seen.add(edge["dst"])
            node = node_by_id.get(edge["dst"])
            if node is None:
                continue
            config = node.get("config") or {}
            if node["type"] == "select":
                names = [f["name"] for f in (config.get("fields") or []) if f.get("name")]
                if names:
                    return names
            elif node["type"] == "formula":
                names: list[str] = []
                for formula in config.get("formulas") or []:
                    for ref in _BRACKET_RE.findall(formula.get("expression") or ""):
                        if ref not in names:
                            names.append(ref)
                if names:
                    return names
            elif node["type"] == "join":
                names = [k["left"] for k in (config.get("keys") or []) if k.get("left")]
                if names:
                    return names
            queue.append(edge["dst"])
    return []


def _first_from_table(query: str | None) -> str | None:
    match = _FROM_RE.search(query or "")
    return match.group(1) if match else None


# --- per-kind touchpoint builders --------------------------------------------------------------------

def _input_touchpoint(repo: Repo, wf_id: str, dag: dict, node: dict, qid: str,
                      extra_dirs: Sequence[Path]) -> dict:
    config = node.get("config") or {}
    fmt = config.get("format")
    source = config.get("source")
    query = config.get("query")
    table = None
    fields: list[str] = []
    field_source = None
    record_count = None

    if fmt == "db":
        field_source = "meta"
        fields = _meta_fields(node, "Output") or []
        table = _first_from_table(query)
    elif fmt == "yxdb":
        path = find_yxdb(repo, wf_id, source, extra_dirs)
        if path is not None:
            try:
                header = yxdb.read_header(path)
            except yxdb.YxdbError:
                path = None
            else:
                fields = [f["name"] for f in header.fields]
                field_source = "yxdb_header"
                record_count = header.num_records
        if path is None:
            own = _meta_fields(node, "Output")
            if own:
                fields, field_source = own, "meta"
            else:
                fields, field_source = _downstream_fields(dag, node["tool_id"]), "downstream"
    else:  # csv, xlsx, or anything else the parser recorded a format for
        own = _meta_fields(node, "Output")
        if own:
            fields, field_source = own, "meta"
        else:
            fields, field_source = _downstream_fields(dag, node["tool_id"]), "downstream"

    return _tp(id=qid, kind="input", tool_id=node["tool_id"], annotation=node.get("annotation"),
              format=fmt, source=source, key=normalize_key(config), fields=fields,
              field_source=field_source, record_count=record_count, query=query, table=table,
              blocking=True)


def _output_touchpoint(dag: dict, node: dict, qid: str) -> dict:
    config = node.get("config") or {}
    fmt = config.get("format")
    source = config.get("source")
    table = config.get("table")
    key = normalize_key(config)
    if fmt == "db" and table:
        key = f"{key}/{table.lower()}"

    own = _meta_fields(node, "Output")
    if own:
        fields, field_source = own, "meta"
    else:
        upstream = _upstream_meta_fields(dag, node["tool_id"])
        fields, field_source = (upstream, "meta") if upstream else ([], None)

    return _tp(id=qid, kind="output", tool_id=node["tool_id"], annotation=node.get("annotation"),
              format=fmt, source=source, key=key, fields=fields, field_source=field_source,
              table=table, write_mode=config.get("write_mode"), keys=config.get("keys") or [],
              pre_sql=config.get("pre_sql"), post_sql=config.get("post_sql"), blocking=True)


def _macro_touchpoint(node: dict, qid: str) -> dict:
    return _tp(id=qid, kind="macro", tool_id=node["tool_id"], annotation=node.get("annotation"),
              source=node.get("macro_path"), key=node.get("macro_path"), blocking=False)


def _parameter_touchpoint(node: dict, qid: str) -> dict:
    config = node.get("config") or {}
    # `format` is unused by a parameter touchpoint's own shape, so the question's Designer type
    # (NumericUpDown, DropDown, …) rides there instead of adding a field the shape doesn't have.
    return _tp(id=qid, kind="parameter", tool_id=node["tool_id"], annotation=node.get("annotation"),
              format=config.get("type"), source=config.get("default"), key=config.get("name"),
              blocking=False)


def _manual_touchpoint(node: dict, qid: str) -> dict:
    config = node.get("config") or {}
    source = config.get("command") if node["type"] == "run_command" else node.get("plugin")
    return _tp(id=qid, kind="manual", tool_id=node["tool_id"], annotation=node.get("annotation"),
              source=source, blocking=False)


def _constant_touchpoint(name: str, value: str, qid: str) -> dict:
    return _tp(id=qid, kind="constant", source=value, key=name, blocking=False)


# --- resolving against mappings/global.yaml -----------------------------------------------------

def _resolve_from_global(repo: Repo, touchpoints: list[dict]) -> None:
    """Fills `resolved` for any input/output touchpoint whose key is already answered in
    `mappings/global.yaml`; such touchpoints are never asked again (`intake_prompt.py`)."""
    global_map: dict = {}
    if repo.global_mappings.is_file():
        global_map = lib_io.read_yaml(repo.global_mappings) or {}
    sources = global_map.get("sources") or {}
    outputs = global_map.get("outputs") or {}
    for tp in touchpoints:
        if tp["kind"] == "input":
            entry = sources.get(tp["key"])
        elif tp["kind"] == "output":
            entry = outputs.get(tp["key"])
        else:
            entry = None
        if entry:
            tp["resolved"] = {"snowflake": entry.get("snowflake"), "logical": entry.get("logical"),
                              "from": "global"}


# --- enumeration ------------------------------------------------------------------------------------

def _tool_num(tool_id: str) -> int:
    try:
        return int(tool_id)
    except (TypeError, ValueError):
        return 1 << 30  # a non-numeric id (shouldn't happen; parse.py always writes a string of
                        # digits) sorts after every real tool rather than crashing enumeration


_KIND_RANK = {"input": 0, "output": 1, "macro": 2, "parameter": 2, "manual": 2}


def _enumerate(repo: Repo, wf_id: str, dag: dict, extra_dirs: Sequence[Path]) -> list[dict]:
    entries = []
    for node in dag.get("nodes", []):
        node_type = node["type"]
        if node_type in ("input", "output", "macro", "interface"):
            kind = "parameter" if node_type == "interface" else node_type
        elif node_type == "run_command" or node_type == "unknown":
            kind = "manual"
        else:
            continue
        entries.append((_tool_num(node["tool_id"]), _KIND_RANK[kind], kind, node))
    entries.sort(key=lambda e: (e[0], e[1]))

    touchpoints: list[dict] = []
    for _, _, kind, node in entries:
        qid = f"Q{len(touchpoints) + 1}"
        if kind == "input":
            touchpoints.append(_input_touchpoint(repo, wf_id, dag, node, qid, extra_dirs))
        elif kind == "output":
            touchpoints.append(_output_touchpoint(dag, node, qid))
        elif kind == "macro":
            touchpoints.append(_macro_touchpoint(node, qid))
        elif kind == "parameter":
            touchpoints.append(_parameter_touchpoint(node, qid))
        else:
            touchpoints.append(_manual_touchpoint(node, qid))

    for name, value in (dag.get("constants") or {}).items():
        qid = f"Q{len(touchpoints) + 1}"
        touchpoints.append(_constant_touchpoint(name, value, qid))

    _resolve_from_global(repo, touchpoints)
    return touchpoints


def enumerate_touchpoints(repo: Repo, wf_id: str, dag: dict) -> list[dict]:
    """Every touchpoint in `dag`, ordered by numeric tool id (inputs before outputs for the same
    id), then constants. Candidates are not proposed here (`candidates: []` on every entry) --
    that's `propose_candidates`/`run`'s job, since it needs the catalog and program config this
    function doesn't take. `.yxdb` inputs are looked up with no `extra_dirs`; `run` re-does that
    lookup with the CLI's `--yxdb-dir` directories.
    """
    return _enumerate(repo, wf_id, dag, ())


# --- candidate scoring -------------------------------------------------------------------------------

def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if not t.isdigit()}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _basename_for_scoring(tp: dict) -> str:
    """The file/table base name a touchpoint is "about", for both the naming-convention candidate
    and the name-similarity score: a DB table's own name (`dbo.GL_LEDGER` -> `GL_LEDGER`), else the
    file basename with its extension stripped."""
    if tp.get("table"):
        return tp["table"].split(".")[-1]
    text = tp.get("source") or tp.get("key") or ""
    name = re.split(r"[\\/]+", text)[-1]
    return re.sub(r"\.[A-Za-z0-9]+$", "", name)


def _sanitize_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()


def _naming_candidate(tp: dict, program: dict) -> dict | None:
    sanitized = _sanitize_name(_basename_for_scoring(tp))
    if not sanitized:
        return None
    database = program.get("target_database")
    schema = program.get("raw_schema") if tp["kind"] == "input" else program.get("target_schema")
    if not database or not schema:
        return None
    return {"snowflake": f"{database}.{schema}.{sanitized}", "basis": "naming"}


def known_input_sources(touchpoints: list[dict], answers: Sequence[dict] = (),
                        existing_sources: dict | None = None) -> dict[str, str | None]:
    """`{snowflake.upper(): tool_id | None}` for every table this workflow already reads --
    coordinator ruling: an output must never default onto one of the workflow's own sources,
    because column overlap alone can't tell an input from an output that happens to share its
    columns (an output usually *is* a reshaping of some input). A table counts as "a source of
    this workflow" from any of FOUR places (fix round 1: the first review only covered the first
    and third):

    1. an `input` touchpoint's `resolved` answer from `mappings/global.yaml`;
    2. what an `input` touchpoint was already mapped to earlier in this same prompt session
       (`answers`, `{"id", "action": "map", "snowflake"}`, e.g. from `prompt_touchpoints`);
    3. an `input` touchpoint's own top *column-backed* candidate, even before anyone has confirmed
       it -- a strong-enough guess that offering it right back as an output candidate would be
       self-defeating;
    4. a source declared through the opening "other yxdb" loop, this session (an `answers` entry
       whose `id` matches no touchpoint at all, e.g. `"U1"`) or an earlier one (`existing_sources`,
       `intake/mappings.yaml["sources"]` as already on disk, `note: user-declared` entries
       included) -- a table the workflow reads is a table the workflow reads, whether or not a
       `dag.json` node happens to name it.

    Called with no `answers`/`existing_sources` (the defaults) at `intake_touchpoints.run` time,
    before any prompting has happened, and again during interactive/non-interactive resolution
    with the answers gathered so far and whatever `intake/mappings.yaml` already has on disk.
    """
    by_id = {t["id"]: t for t in touchpoints if t.get("id")}
    answered_by_id = {a["id"]: a for a in answers if a.get("action") == "map" and a.get("id")}
    sources: dict[str, str | None] = {}

    for t in touchpoints:
        if t["kind"] != "input":
            continue
        tool_id = t.get("tool_id")
        resolved = t.get("resolved")
        if resolved and resolved.get("snowflake"):
            sources.setdefault(resolved["snowflake"].upper(), tool_id)
        answer = answered_by_id.get(t.get("id"))
        if answer and answer.get("snowflake"):
            sources.setdefault(answer["snowflake"].upper(), tool_id)
        candidates = t.get("candidates") or []
        if candidates and candidates[0].get("basis") == "columns":
            sources.setdefault(candidates[0]["snowflake"].upper(), tool_id)

    for qid, answer in answered_by_id.items():
        if qid in by_id:
            # A real touchpoint's own answer: an input's is already folded in above; an output's
            # is deliberately never treated as a source (mapping an output doesn't make its
            # target a table the workflow *reads*).
            continue
        snowflake = answer.get("snowflake")
        if snowflake:
            sources.setdefault(snowflake.upper(), None)

    for entry in (existing_sources or {}).values():
        snowflake = (entry or {}).get("snowflake")
        if snowflake:
            tool_ids = entry.get("tool_ids") or []
            sources.setdefault(snowflake.upper(), tool_ids[0] if tool_ids else None)

    return sources


def propose_candidates(tp: dict, catalog: list[dict], program: dict, *,
                       exclude: Sequence[str] = ()) -> list[dict]:
    """Up to 3 catalog tables with `>=0.5` field overlap (`score = 0.8*overlap + 0.2*name`, name
    similarity by Jaccard over non-numeric tokens), best first, plus a naming-convention candidate
    unless it duplicates one of those 3. A naming candidate is only ever a proposal to confirm --
    nothing here writes a mapping.

    For an `output` touchpoint, a catalog table is dropped from the *column-backed* ranking (never
    from the naming-convention candidate, which is a proposal to confirm, not a lookup) when its
    schema is `program.raw_schema` (default `RAW`) or its FQN (case-insensitively) is in `exclude`
    -- coordinator ruling: an output must never quietly default onto the raw landing schema or
    onto one of the workflow's own inputs (`known_input_sources`), because "this output shares 7/7
    columns with a table" is exactly what's true of the workflow's own input.
    """
    fields = [f.upper() for f in (tp.get("fields") or [])]
    result: list[dict] = []
    excluded_upper = {e.upper() for e in exclude}
    raw_schema = ((program.get("raw_schema") or "RAW").upper()) if tp["kind"] == "output" else None
    if fields:
        tables: dict[str, dict] = {}
        for row in catalog:
            fqn = f"{row['database']}.{row['schema']}.{row['table']}"
            info = tables.setdefault(fqn, {"row_count": row.get("row_count"), "columns": set()})
            info["columns"].add((row.get("column") or "").upper())

        own_tokens = _tokens(_basename_for_scoring(tp))
        scored = []
        for fqn, info in tables.items():
            if fqn.upper() in excluded_upper:
                continue
            if raw_schema is not None and fqn.split(".")[1].upper() == raw_schema:
                continue
            matched = sum(1 for f in fields if f in info["columns"])
            overlap = matched / len(fields)
            if overlap < 0.5:
                continue
            name_score = _jaccard(own_tokens, _tokens(fqn.split(".")[-1]))
            score = 0.8 * overlap + 0.2 * name_score
            missing = [f for f in fields if f not in info["columns"]]
            scored.append({"snowflake": fqn, "matched": matched, "of": len(fields),
                           "missing": missing, "score": round(score, 4),
                           "row_count": info["row_count"], "basis": "columns"})
        scored.sort(key=lambda c: (-c["score"], c["snowflake"]))
        result = scored[:3]

    naming = _naming_candidate(tp, program)
    if naming and not any(c["snowflake"] == naming["snowflake"] for c in result):
        result.append(naming)
    return result


# --- run --------------------------------------------------------------------------------------------

def run(repo: Repo, wf_id: str, extra_dirs: Sequence[Path] = ()) -> list[dict]:
    """Enumerate, propose candidates for every unresolved blocking touchpoint, and write
    `intake/touchpoints.json`. Raises `FileNotFoundError` when the workflow hasn't been parsed yet
    (a usage error for the CLI, not a domain failure): there is nothing to enumerate.
    """
    dag_path = repo.wf(wf_id, "parsed", "dag.json")
    if not dag_path.is_file():
        raise FileNotFoundError(
            f"no parsed/dag.json for {wf_id}; run `python scripts/parse.py {wf_id}` first")
    dag = lib_io.read_json(dag_path)

    touchpoints = _enumerate(repo, wf_id, dag, extra_dirs)

    catalog = load_catalog(repo)
    program: dict = {}
    if repo.global_mappings.is_file():
        program = (lib_io.read_yaml(repo.global_mappings) or {}).get("program") or {}

    # Inputs first, so each output's exclusion set (known_input_sources) can see every input's
    # resolved answer or top column-backed candidate before any output is scored. Also fold in
    # intake/mappings.yaml's own `sources` if this workflow has been through intake before (fix
    # round 1: re-running this script mid-session must not un-protect an already-known source).
    existing_mappings_path = repo.wf(wf_id, "intake", "mappings.yaml")
    existing_sources = None
    if existing_mappings_path.is_file():
        existing_sources = (lib_io.read_yaml(existing_mappings_path) or {}).get("sources")
    for tp in touchpoints:
        if tp["kind"] == "input" and tp["resolved"] is None:
            tp["candidates"] = propose_candidates(tp, catalog, program)
    exclude = known_input_sources(touchpoints, existing_sources=existing_sources).keys()
    for tp in touchpoints:
        if tp["kind"] == "output" and tp["resolved"] is None:
            tp["candidates"] = propose_candidates(tp, catalog, program, exclude=exclude)

    lib_io.write_json(repo.wf(wf_id, "intake", "touchpoints.json"), touchpoints)
    return touchpoints


# --- CLI --------------------------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0001")
    parser.add_argument("--yxdb-dir", dest="yxdb_dirs", action="append", default=[],
                        help="extra directory to search for a yxdb input's local copy (repeatable)")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    repo = Repo(args.root)
    try:
        touchpoints = run(repo, args.wf_id, extra_dirs=[Path(d) for d in args.yxdb_dirs])
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))  # exit 2: bad workflow id, or nothing parsed to enumerate
    except Exception:
        traceback.print_exc()
        return 2

    blocking = sum(1 for t in touchpoints if t["blocking"])
    unresolved = sum(1 for t in touchpoints if t["blocking"] and t["resolved"] is None)
    print(f"{args.wf_id}: {len(touchpoints)} touchpoints, {blocking} blocking "
          f"({unresolved} not yet resolved from mappings/global.yaml)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
