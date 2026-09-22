"""Alteryx workflow XML → `workflows/<id>/parsed/dag.json` (program spec §6.2, dag-contract).

    python scripts/parse.py <wf_id> [--check] [--root .]

Everything downstream — segmenter, intake, simulator, agents — reads the dag this writes, so the
parser keeps what it found and marks what it did not understand: an unrecognised plugin stays a
node with `type: "unknown"` and its `raw_config`, an unresolvable macro keeps its path with
`unresolved: true`. Nothing is dropped and nothing is guessed.

Exit codes follow the plan's Global Constraints: 0 for a clean parse (`PARSED`, or the preserved
`RECOVERED`) and 1 for a domain failure — `FAILED`, `INVARIANT_VIOLATION` and `QUARANTINED` alike,
because a workflow that failed to parse is exactly what the parser-recovery agent exists for
(spec §6.4). 2 is reserved for usage errors: bad arguments, or no workflow to parse at all.
"""
from __future__ import annotations

import argparse
import re
import sys
import traceback
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Sequence

import invariants
from lib.io import read_json, write_json
from lib.paths import Repo, add_root_arg
from lib.vocab import NON_DATA_TYPES
from parsers import plugin_map, registry, tool_config

# Extensions that ship with the parser. `run` adds the repo's own copy when --root points
# somewhere else (program spec §6.2).
DEFAULT_EXT_DIR = Path(__file__).resolve().parent / "parsers" / "ext"

WORKFLOW_SUFFIXES = (".yxmd", ".yxwz", ".yxmc")
_XML_DECL_RE = re.compile(r"^<\?xml[^>]*\?>")
_ENCODING_RE = re.compile(r"(?i)encoding\s*=\s*[\"']([^\"']+)[\"']")


class LockedDocument(Exception):
    """A workflow whose body is locked or encrypted: there is nothing to parse (spec §6.1)."""


# --- decoding ---

def decode_xml(raw: bytes) -> str:
    """Bytes → text: BOM first, then the declared encoding, then UTF-8, then cp1252.

    Alteryx writes UTF-8 with a BOM on Windows, but older files and hand-edited ones turn up as
    cp1252 with no declaration at all, and expat cannot decode cp1252 itself.
    """
    for bom, encoding in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            return raw.decode(encoding)
    head = raw[:200].decode("ascii", errors="replace")
    declared = _ENCODING_RE.search(head) if head.lstrip().startswith("<?xml") else None
    for encoding in ([declared.group(1)] if declared else []) + ["utf-8", "cp1252"]:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


# --- one document ---

def parse_file(path: str | Path, ext_dirs: Sequence[Path] = ()) -> tuple[dict, list[str]]:
    """Parse one workflow, macro or app file. Returns (dag, aliases scrubbed out of it)."""
    path = Path(path)
    ext_dirs = tuple(Path(d) for d in ext_dirs)
    registry.load_extensions([DEFAULT_EXT_DIR, *ext_dirs])
    aliases: list[str] = []
    dag = _parse_document(path, ext_dirs, aliases, ())
    return dag, aliases


def _parse_document(path: Path, ext_dirs: Sequence[Path], aliases: list[str],
                    macro_stack: tuple[Path, ...]) -> dict:
    root = ET.fromstring(decode_xml(path.read_bytes()))
    nodes_element = root.find("Nodes")
    if nodes_element is None and (root.find(".//Locked") is not None
                                  or root.find(".//EncryptedNodes") is not None):
        raise LockedDocument(f"{path.name} has no <Nodes>: the workflow is locked or encrypted")

    properties = root.find("Properties")
    dag = {
        "workflow": _workflow_id(path),
        "yxmd_version": root.get("yxmdVer"),
        "engine": _engine(root, properties),
        "constants": _constants(properties),
        "file_kind": path.suffix.lower().lstrip("."),
        "source_file": path.name,
        "nodes": [],
        "edges": [],
    }
    if nodes_element is not None:
        _walk_nodes(nodes_element, None, dag, path, ext_dirs, aliases, macro_stack)
    dag["edges"] = _edges(root, {n["tool_id"]: n["type"] for n in dag["nodes"]})
    _anchors_from_connections(dag)
    for tag, handler in registry.element_handlers().items():
        for element in root.iter(tag):
            handler(element, dag)
    return dag


def _workflow_id(path: Path) -> str:
    """`workflows/wf_0003/source/gl.yxmd` → `wf_0003`; anywhere else, the file's own stem."""
    return path.parent.parent.name if path.parent.name == "source" else path.stem


def _engine(root: ET.Element, properties: ET.Element | None) -> str:
    """AMP when the document's Properties carry `<RunE2 value="True"/>`, else the E1 engine."""
    if (root.get("RunE2") or "").upper() in ("T", "TRUE"):
        return "AMP"
    for flag in (properties.iterfind(".//RunE2") if properties is not None else []):
        if (flag.get("value") or "").lower() == "true":
            return "AMP"
    return "E1"


def _constants(properties: ET.Element | None) -> dict[str, str]:
    constants = {}
    for constant in (properties.iterfind("Constants/Constant") if properties is not None else []):
        name = constant.findtext("Name")
        if name:
            constants[name.strip()] = (constant.findtext("Value") or "").strip()
    return constants


def _walk_nodes(parent: ET.Element, container_id: str | None, dag: dict, path: Path,
                ext_dirs: Sequence[Path], aliases: list[str], macro_stack: tuple[Path, ...]) -> None:
    """Every `<Node>`, recursing into `<ChildNodes>` carrying the container's ToolID."""
    for node_element in parent.findall("Node"):
        node = _build_node(node_element, container_id, path, ext_dirs, aliases, macro_stack)
        dag["nodes"].append(node)
        children = node_element.find("ChildNodes")
        if children is not None:
            _walk_nodes(children, node["tool_id"], dag, path, ext_dirs, aliases, macro_stack)


def _build_node(node_element: ET.Element, container_id: str | None, path: Path,
                ext_dirs: Sequence[Path], aliases: list[str], macro_stack: tuple[Path, ...]) -> dict:
    """One `<Node>` → a dag node.

    A registered extension handler runs *before* classification decides anything: it sees the part
    of the node that does not depend on the tool's type, and what it returns settles the type. Only
    then are `config` and the anchors read for the type that won, so an extension that remaps a
    vendor plugin to a built-in type gets that type's config, not the one `unknown` would have had.
    """
    gui = node_element.find("GuiSettings")
    engine = node_element.find("EngineSettings")
    plugin = gui.get("Plugin") if gui is not None else None
    macro = engine.get("Macro") if engine is not None else None

    properties = node_element.find("Properties")
    configuration = properties.find("Configuration") if properties is not None else None
    raw_config = None
    if configuration is not None:
        raw_config, found = tool_config.scrub_xml(ET.tostring(configuration, encoding="unicode").strip())
        for alias in found:
            if alias not in aliases:
                aliases.append(alias)

    fallback_type = plugin_map.classify(plugin, macro)
    node = {
        "tool_id": str(node_element.get("ToolID")),
        "type": fallback_type,
        "plugin": plugin,
        "container_id": container_id,
        "config": {},
        "raw_config": raw_config,
        "annotation": _annotation(properties, fallback_type, configuration),
        "in_anchors": [],
        "out_anchors": [],
        "meta": _meta(properties, fallback_type),
    }

    handler = registry.plugin_handler(plugin)
    handled = registry.mergeable(handler(node_element, node)) if handler is not None else {}

    tool_type = node["type"] = handled.get("type", fallback_type)
    if tool_type != fallback_type:  # the handler renamed the tool: re-read what the type governs
        node["annotation"] = _annotation(properties, tool_type, configuration)
        node["meta"] = _meta(properties, tool_type)
    node["config"] = handled["config"] if "config" in handled \
        else tool_config.parse_config(tool_type, configuration)
    node["in_anchors"] = list(plugin_map.anchors_of(tool_type)["in"])
    node["out_anchors"] = plugin_map.out_anchor_names(tool_type)
    if tool_type == "macro" and macro is not None:
        # A handler that calls a node a macro without an `<EngineSettings Macro=…>` to resolve
        # leaves it without a `macro_path`, which is invariant 4's to report.
        _resolve_macro(node, macro, path, ext_dirs, aliases, macro_stack)
    # anchors the handler named win over both the type's defaults and a macro's own tools
    node.update({key: handled[key] for key in ("in_anchors", "out_anchors", "behavior", "confidence")
                 if key in handled})
    return node


def _annotation(properties: ET.Element | None, tool_type: str,
                configuration: ET.Element | None) -> str | None:
    """The text Designer shows under a tool; for a Tool Container, its caption."""
    annotation = properties.find("Annotation") if properties is not None else None
    for tag in ("AnnotationText", "DefaultAnnotationText"):
        text = (annotation.findtext(tag) or "").strip() if annotation is not None else ""
        if text:
            return text
    if tool_type == "container" and configuration is not None:
        return (configuration.findtext("Caption") or "").strip() or None
    return None


def _meta(properties: ET.Element | None, tool_type: str) -> dict[str, list[dict]]:
    """`<MetaInfo connection="…"><RecordInfo>` per anchor, keyed by canonical anchor name.

    Sink tools keep theirs: intake needs a field list for an output whose target does not exist
    yet. A field's `source` attribute is Designer's provenance note and is ignored.
    """
    meta: dict[str, list[dict]] = {}
    for block in (properties.findall("MetaInfo") if properties is not None else []):
        anchor = block.get("connection") or "Output"
        meta[plugin_map.canonical_out_anchor(tool_type, anchor)] = [
            {"name": f.get("name"), "type": f.get("type"),
             "size": _int(f.get("size")), "scale": _int(f.get("scale"))}
            for f in block.findall("RecordInfo/Field")]
    return meta


def _int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _resolve_macro(node: dict, macro: str, path: Path, ext_dirs: Sequence[Path],
                   aliases: list[str], macro_stack: tuple[Path, ...]) -> None:
    """Resolve, parse and describe a macro node: `macro_path`, `sub_dag`, `interface`, anchors."""
    macro_path = macro.replace("\\", "/")
    candidates = [path.parent / macro_path] + [Path(d).parent / macro_path for d in ext_dirs]
    resolved = next((c for c in candidates if c.is_file()), None)
    sub_dag = None
    if resolved is not None and resolved.resolve() not in macro_stack:
        sub_dag = _parse_document(resolved, ext_dirs, aliases, macro_stack + (resolved.resolve(),))

    node["macro_path"] = macro_path
    node["sub_dag"] = sub_dag
    node["unresolved"] = sub_dag is None
    node["interface"] = [n["config"] for n in (sub_dag or {}).get("nodes", []) if n["type"] == "interface"]
    if sub_dag is not None:
        # dag-contract §4: a macro's anchors are named after its own macro_input/macro_output tools.
        node["in_anchors"] = [f"Input{n['tool_id']}" for n in sub_dag["nodes"] if n["type"] == "macro_input"]
        node["out_anchors"] = [f"Output{n['tool_id']}" for n in sub_dag["nodes"] if n["type"] == "macro_output"]


def _edges(root: ET.Element, types: dict[str, str]) -> list[dict]:
    edges = []
    for connection in root.findall("Connections/Connection"):
        origin, destination = connection.find("Origin"), connection.find("Destination")
        if origin is None or destination is None:
            continue
        src = str(origin.get("ToolID"))
        label = destination.get("name") or connection.get("name") or ""
        order = label.lstrip("#")
        edges.append({
            "src": src,
            "src_anchor": plugin_map.canonical_out_anchor(types.get(src, "unknown"),
                                                          origin.get("Connection")),
            "dst": str(destination.get("ToolID")),
            "dst_anchor": destination.get("Connection"),
            # Union inputs arrive as `Input` with Alteryx's `#1`, `#2` label; everything else is 1.
            "dst_order": int(order) if order.isdigit() else 1,
            "wireless": (connection.get("Wireless") or "").lower() == "true",
        })
    return edges


def _anchors_from_connections(dag: dict) -> None:
    """Give a tool the parser cannot describe the anchors its connections actually use."""
    for node in dag["nodes"]:
        if node["type"] not in ("unknown", "macro"):
            continue
        if not node["out_anchors"]:
            node["out_anchors"] = list(dict.fromkeys(
                e["src_anchor"] for e in dag["edges"] if e["src"] == node["tool_id"] and e["src_anchor"]))
        if not node["in_anchors"]:
            node["in_anchors"] = list(dict.fromkeys(
                e["dst_anchor"] for e in dag["edges"] if e["dst"] == node["tool_id"] and e["dst_anchor"]))


# --- one workflow under a repo root ---

def run(repo: Repo, wf_id: str, check: bool) -> dict:
    """Parse `workflows/<wf_id>/source/`, write `parsed/dag.json` and `parsed/parse_report.json`.

    Returns the report. On a clean parse every source file is rewritten without its credentials.
    Raises `FileNotFoundError` when there is no workflow to parse at all: that is a usage error,
    and writing a report for it would conjure up a workflow directory for a mistyped id.
    """
    source_dir = repo.wf(wf_id, "source")
    previous = _previous_report(repo, wf_id)
    report = {"status": "FAILED", "errors": [], "node_count": 0, "unknown_share": 0.0,
              "scrubbed_aliases": [], "attempt": 1, "extension": None,
              "reason": None, "diagnosis": None}
    try:
        _unzip_packages(source_dir)
        path = workflow_file(source_dir)
        dag, aliases = parse_file(path, ext_dirs=_ext_dirs(repo))
    except FileNotFoundError:
        raise  # there is no workflow here: a usage error for the caller, not a parse failure
    except LockedDocument as exc:
        report.update(status="QUARANTINED", reason="locked", errors=[str(exc)])
    except Exception as exc:  # the parser-recovery agent's other trigger (spec §6.4)
        report.update(status="FAILED", errors=[f"{type(exc).__name__}: {exc}"])
    else:
        errors = invariants.check(decode_xml(path.read_bytes()), dag) if check else []
        report.update(status="INVARIANT_VIOLATION" if errors else "PARSED", errors=errors,
                      node_count=len(dag["nodes"]), unknown_share=_unknown_share(dag),
                      scrubbed_aliases=aliases)
        if not errors and previous.get("status") == "RECOVERED":
            # An earlier extension fixed this workflow and still holds: keep the credit.
            report.update(status="RECOVERED", attempt=previous.get("attempt", 1),
                          extension=previous.get("extension"), diagnosis=previous.get("diagnosis"))
        write_json(repo.wf(wf_id, "parsed", "dag.json"), dag)
        if report["status"] in ("PARSED", "RECOVERED"):
            _scrub_sources(source_dir)
    write_json(repo.wf(wf_id, "parsed", "parse_report.json"), report)
    return report


def _previous_report(repo: Repo, wf_id: str) -> dict:
    path = repo.wf(wf_id, "parsed", "parse_report.json")
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except ValueError:
        return {}


def _ext_dirs(repo: Repo) -> list[Path]:
    """The parser's own `ext/`, plus the one under --root when that is a different tree."""
    dirs = [DEFAULT_EXT_DIR]
    root_ext = repo.root / "scripts" / "parsers" / "ext"
    if root_ext.resolve() != DEFAULT_EXT_DIR.resolve():
        dirs.append(root_ext)
    return dirs


def _unzip_packages(source_dir: Path) -> None:
    """A `.yxzp` is a zip of a workflow and its dependencies: unpack it where it lies."""
    for package in sorted(source_dir.glob("*.yxzp")):
        with zipfile.ZipFile(package) as archive:
            for member in archive.namelist():
                target = (source_dir / member).resolve()
                if not str(target).startswith(str(source_dir.resolve())):
                    raise ValueError(f"{package.name} contains a path outside source/: {member}")
            archive.extractall(source_dir)


def workflow_file(source_dir: Path) -> Path:
    """The workflow in `source/`: a `.yxmd`/`.yxwz` if there is one, else a macro."""
    files = sorted(p for p in source_dir.glob("*") if p.suffix.lower() in WORKFLOW_SUFFIXES)
    for suffixes in ((".yxmd", ".yxwz"), (".yxmc",)):
        for path in files:
            if path.suffix.lower() in suffixes:
                return path
    raise FileNotFoundError(f"no workflow file in {source_dir}")


def _unknown_share(dag: dict) -> float:
    """Share of the data nodes the parser could not classify (spec §6.4's fallback trigger)."""
    data = [n for n in dag["nodes"] if n["type"] not in NON_DATA_TYPES]
    if not data:
        return 0.0
    return round(sum(1 for n in data if n["type"] == "unknown") / len(data), 4)


def _scrub_sources(source_dir: Path) -> None:
    """Rewrite every workflow file under `source/` without its credentials (dag-contract §6)."""
    for path in sorted(source_dir.rglob("*")):
        if path.suffix.lower() not in WORKFLOW_SUFFIXES:
            continue
        text, _ = tool_config.scrub_xml(decode_xml(path.read_bytes()))
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_utf8_declaration(text))


def _utf8_declaration(text: str) -> str:
    """We write UTF-8, so a declaration that claims another encoding has to say so."""
    declaration = _XML_DECL_RE.match(text)
    if declaration is None:
        return text
    found = _ENCODING_RE.search(declaration.group(0))
    if found is None or found.group(1).lower() in ("utf-8", "utf8"):
        return text
    fixed = declaration.group(0).replace(found.group(0), 'encoding="utf-8"')
    return fixed + text[declaration.end():]


# --- CLI ---

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0003")
    parser.add_argument("--check", action="store_true", help="run invariants.py and fail on any violation")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    try:
        report = run(Repo(args.root), args.wf_id, check=args.check)
    except FileNotFoundError as exc:
        parser.error(str(exc))  # exit 2: nothing to parse, so there is no report to write
    except Exception:  # exit 2: a crash outside the parse itself, which leaves no report either
        traceback.print_exc()
        return 2
    print(f"{report['status']}: {report['node_count']} nodes, "
          f"{len(report['scrubbed_aliases'])} aliases scrubbed")
    for error in report["errors"]:
        print(f"  {error}", file=sys.stderr)
    # Every status but a clean parse is a domain failure: FAILED and INVARIANT_VIOLATION both send
    # the workflow to parser-recovery (spec §6.4), and QUARANTINED sends it to its owner.
    return 0 if report["status"] in ("PARSED", "RECOVERED") else 1


if __name__ == "__main__":
    sys.exit(main())
