"""Instrument a workflow's XML with extra Output Data tools so a real Alteryx run captures golden
data, and import the resulting `.yxdb` captures into the project's typed-CSV golden format
(program spec §7.5, dag-contract §4 "output").

    python scripts/inject_outputs.py <wf_id> --capture-dir C:\\mig\\capture\\wf_0001 [--root .]
    python scripts/inject_outputs.py <wf_id> --capture-dir C:\\mig\\capture\\wf_0001 --import-set normal [--root .]

Nothing here runs Alteryx. The first form clones `workflows/<wf_id>/source/*.yxm*`, wires in one
Output Data tool (`FileFormat="19"`, i.e. `.yxdb`) per input tool's out stream, per segment
boundary stream and per stream feeding a final Output tool, writes the clone as
`source/<name>.instrumented.yxmd` plus `golden/capture_map.json`, and prints the
`AlteryxEngineCmd.exe` command line a person would run against the clone. The second form is run
after that person has actually done so: it reads the `.yxdb` files the run produced out of
`--capture-dir`, writes them as typed CSV under `golden/` (contract C1/C2) and, once every CSV is
written, records the set in `manifest.json`'s `golden_sets` (appended once, order kept) -- the list
the orchestrator's golden stage reads. A failed import records nothing.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path, PureWindowsPath
from typing import Sequence
from xml.sax.saxutils import escape

import parse
from lib import typed_csv, yxdb
from lib.io import load_manifest, read_json, save_manifest, write_json
from lib.paths import Repo, add_root_arg
from parsers import plugin_map

ALTERYX_ENGINE_CMD = r"C:\Program Files\Alteryx\bin\AlteryxEngineCmd.exe"

# New ToolIDs start at max(existing tool id) + this (program spec §7.5 / task brief).
NEW_ID_OFFSET = 1001

_NODE_TEMPLATE = (
    '<Node ToolID="{tool_id}">'
    '<GuiSettings Plugin="AlteryxBasePluginsGui.DbFileOutput.DbFileOutput">'
    '<Position x="{x}" y="{y}"/></GuiSettings>'
    '<Properties><Configuration>'
    '<File MaxRecords="" FileFormat="19">{file}</File>'
    '<Passwords/>'
    '<FormatSpecificOptions><OutputOption>Overwrite</OutputOption></FormatSpecificOptions>'
    '</Configuration>'
    '<Annotation DisplayMode="0"><Name/><DefaultAnnotationText>{annotation}</DefaultAnnotationText></Annotation>'
    '</Properties>'
    '<EngineSettings EngineDll="AlteryxBasePluginsEngine.dll" EngineDllEntryPoint="AlteryxDbFileOutput"/>'
    '</Node>'
)

_CONNECTION_TEMPLATE = (
    '<Connection>'
    '<Origin ToolID="{src}" Connection="{src_anchor}"/>'
    '<Destination ToolID="{dst}" Connection="Input"/>'
    '</Connection>'
)


# --- anchor names: canonical (dag.json) -> XML (dag-contract §2) ---

def _xml_out_anchor(tool_type: str, canonical_anchor: str) -> str:
    """The XML `Connection` name for a tool's canonical out anchor, e.g. join's `L` -> `Left`.

    `plugin_map.ANCHORS[type]["out"]` already holds this mapping the other way round (XML -> canonical,
    used to build dag.json); this inverts it. A type absent from the table, or an anchor a type
    doesn't declare (macro anchors, which dag-contract §4 says are already canonical) is returned
    unchanged.
    """
    for xml_name, canonical in plugin_map.anchors_of(tool_type)["out"].items():
        if canonical == canonical_anchor:
            return xml_name
    return canonical_anchor


# --- capture points: where a new Output Data tool taps an existing stream ---

def _input_points(dag: dict) -> list[dict]:
    """One capture point per input tool's out anchor (there is exactly one: `Output`)."""
    points = []
    for node in sorted((n for n in dag["nodes"] if n["type"] == "input"), key=lambda n: int(n["tool_id"])):
        for anchor in node["out_anchors"]:
            points.append({
                "kind": "input", "of_tool": node["tool_id"], "segment": None,
                "src_tool_id": node["tool_id"], "src_anchor": anchor,
                "src_xml_anchor": _xml_out_anchor(node["type"], anchor),
                "stream": f"{node['tool_id']}_{anchor}",
                "filename": f"in_{node['tool_id']}.yxdb",
            })
    return points


def _intermediate_points(dag: dict, segment_dags: list[dict]) -> list[dict]:
    """One capture point per distinct stream a segment's `outbound` list carries (contract C7)."""
    nodes_by_id = {n["tool_id"]: n for n in dag["nodes"]}
    points = []
    for seg_dag in sorted(segment_dags, key=lambda s: s["segment"]):
        seen: set[tuple[str, str]] = set()
        for edge in seg_dag.get("outbound", []):
            key = (edge["src"], edge["src_anchor"])
            if key in seen:
                continue
            seen.add(key)
            src_type = nodes_by_id[edge["src"]]["type"]
            stream = f"{edge['src']}_{edge['src_anchor']}"
            points.append({
                "kind": "intermediate", "of_tool": edge["src"], "segment": seg_dag["segment"],
                "src_tool_id": edge["src"], "src_anchor": edge["src_anchor"],
                "src_xml_anchor": _xml_out_anchor(src_type, edge["src_anchor"]),
                "stream": stream,
                "filename": f"mid_{seg_dag['segment']}_{stream}.yxdb",
            })
    return points


def _output_points(dag: dict) -> list[dict]:
    """One capture point per final Output tool, tapping the stream that feeds it."""
    nodes_by_id = {n["tool_id"]: n for n in dag["nodes"]}
    points = []
    out_nodes = sorted((n for n in dag["nodes"] if n["type"] == "output"), key=lambda n: int(n["tool_id"]))
    for node in out_nodes:
        inbound = sorted((e for e in dag["edges"] if e["dst"] == node["tool_id"]),
                         key=lambda e: (int(e["src"]), e["src_anchor"]))
        if not inbound:
            continue  # a disconnected Output tool has nothing to capture
        edge = inbound[0]
        src_type = nodes_by_id[edge["src"]]["type"]
        points.append({
            "kind": "output", "of_tool": node["tool_id"], "segment": None,
            "src_tool_id": edge["src"], "src_anchor": edge["src_anchor"],
            "src_xml_anchor": _xml_out_anchor(src_type, edge["src_anchor"]),
            "stream": f"{edge['src']}_{edge['src_anchor']}",
            "filename": f"out_{node['tool_id']}.yxdb",
        })
    return points


def _original_workflow_file(source_dir: Path) -> Path:
    """The workflow `parse.py` parsed, ignoring any earlier `*.instrumented.yxmd` this script
    itself wrote there: `parse.workflow_file` sorts alphabetically, and "instrumented" sorts
    before the plain name, so a second run would otherwise re-instrument its own output."""
    candidates = [p for p in source_dir.glob("*")
                 if p.suffix.lower() in parse.WORKFLOW_SUFFIXES and ".instrumented." not in p.name.lower()]
    for suffixes in ((".yxmd", ".yxwz"), (".yxmc",)):
        for path in sorted(candidates):
            if path.suffix.lower() in suffixes:
                return path
    raise FileNotFoundError(f"no workflow file in {source_dir}")


def _insert_before(text: str, marker: str, insertion: str) -> str:
    """Splices `insertion` immediately before the first `marker`, leaving every other byte as is."""
    if not insertion:
        return text
    index = text.index(marker)
    return text[:index] + insertion + text[index:]


# --- public API (task brief interface) ---

def inject(xml_text: str, dag: dict, segment_dags: list[dict], capture_dir: str) -> tuple[str, list[dict]]:
    """Clone `xml_text` with one Output Data tool per golden capture point.

    Capture points are, in order: (a) every input tool's out stream, (b) every distinct stream a
    segment's `outbound` edges carry (contract C7), (c) the stream feeding every final Output tool
    (program spec §7.5). New nodes and connections are inserted as text just before `</Nodes>` and
    `</Connections>` respectively, so every original byte of `xml_text` is preserved untouched.
    Returns the new XML text and the capture map rows (written by the CLI to
    `golden/capture_map.json`).
    """
    points = _input_points(dag) + _intermediate_points(dag, segment_dags) + _output_points(dag)

    existing_ids = [int(n["tool_id"]) for n in dag["nodes"]]
    next_id = (max(existing_ids) if existing_ids else 0) + NEW_ID_OFFSET

    nodes_xml: list[str] = []
    connections_xml: list[str] = []
    capture_map: list[dict] = []
    for i, point in enumerate(points):
        tool_id = str(next_id + i)
        file_path = str(PureWindowsPath(capture_dir) / point["filename"])
        annotation = f"golden capture: {point['kind']} {point['stream']}"
        nodes_xml.append(_NODE_TEMPLATE.format(
            tool_id=tool_id, x=50 + i * 120, y=900,
            file=escape(file_path), annotation=escape(annotation)))
        connections_xml.append(_CONNECTION_TEMPLATE.format(
            src=point["src_tool_id"], src_anchor=escape(point["src_xml_anchor"]), dst=tool_id))
        capture_map.append({
            "tool_id": tool_id, "kind": point["kind"], "of_tool": point["of_tool"],
            "stream": point["stream"], "segment": point["segment"], "file": file_path,
        })

    new_xml = _insert_before(xml_text, "</Nodes>", "".join(nodes_xml))
    new_xml = _insert_before(new_xml, "</Connections>", "".join(connections_xml))
    return new_xml, capture_map


def _golden_csv_path(repo: Repo, wf_id: str, golden_set: str, row: dict) -> Path:
    if row["kind"] == "input":
        return repo.wf(wf_id, "golden", "inputs", golden_set, f"{row['of_tool']}.csv")
    if row["kind"] == "output":
        return repo.wf(wf_id, "golden", "outputs", golden_set, f"{row['of_tool']}.csv")
    if row["kind"] == "intermediate":
        return repo.wf(wf_id, "golden", "intermediates", row["segment"], golden_set, f"{row['stream']}.csv")
    raise ValueError(f"unknown capture kind {row['kind']!r}")  # pragma: no cover - inject() never emits one


def import_captures(repo: Repo, wf_id: str, golden_set: str, capture_dir: Path) -> list[Path]:
    """`golden/capture_map.json` + the `.yxdb` files a real Alteryx run wrote to `capture_dir` ->
    typed CSV under `golden/` (contract C1/C2). Returns the CSV paths written, one per capture
    row. A row's file is looked up by name under `capture_dir` (not by the absolute path recorded
    at injection time, which may belong to a different machine).

    Every listed capture is resolved and read (`yxdb.read_header`/`read_records`) before anything
    is written, so a row that fails never leaves a partially imported golden set on disk. A row
    whose file is missing from `capture_dir` raises `FileNotFoundError` naming it; a row whose
    file exists but isn't a readable `.yxdb` raises `yxdb.YxdbError` naming it. Both are domain
    failures the CLI reports as exit 1, never a silent skip.
    """
    capture_dir = Path(capture_dir)
    capture_map_path = repo.wf(wf_id, "golden", "capture_map.json")
    if not capture_map_path.is_file():
        raise FileNotFoundError(
            f"{capture_map_path} not found; run scripts/inject_outputs.py {wf_id} "
            f"--capture-dir <dir> first")
    capture_map = read_json(capture_map_path)

    # Pass 1: resolve and read every listed capture before writing anything.
    tables: list[tuple[Path, dict]] = []
    for row in capture_map:
        filename = PureWindowsPath(row["file"]).name
        source_path = capture_dir / filename
        if not source_path.is_file():
            raise FileNotFoundError(f"capture file missing: {filename} (expected at {source_path})")
        try:
            header = yxdb.read_header(source_path)
            table = {"fields": header.fields, "rows": list(yxdb.read_records(source_path))}
        except yxdb.YxdbError as exc:
            raise yxdb.YxdbError(f"{filename}: {exc}") from exc
        tables.append((_golden_csv_path(repo, wf_id, golden_set, row), table))

    # Pass 2: every capture validated -- now write.
    written: list[Path] = []
    for csv_path, table in tables:
        typed_csv.write_table(csv_path, table)
        written.append(csv_path)
    return written


def record_golden_set(repo: Repo, wf_id: str, golden_set: str) -> list[str]:
    """Appends `golden_set` to `manifest.json`'s `golden_sets` unless it is already there (order kept)
    and returns the list. The orchestrator's golden stage (`stageGolden`, orchestrator/stages.ts)
    moves on only when that list is non-empty; its idempotency re-runs use the FIRST set, so import
    `normal` first (Task P4 fix round 1, B2)."""
    manifest = load_manifest(repo, wf_id)
    sets = list(manifest.get("golden_sets") or [])
    if golden_set not in sets:
        sets.append(golden_set)
        manifest["golden_sets"] = sets
        save_manifest(repo, manifest)
    return sets


def _run_instrument(repo: Repo, wf_id: str, capture_dir: str) -> tuple[Path, list[dict]]:
    """The instrument path's core logic: every prerequisite is checked (and named in the
    exception if absent) before `inject()` runs, and nothing is written unless all of them are
    present. Raises `FileNotFoundError` for a missing prerequisite -- a usage error the CLI turns
    into exit 2.
    """
    source_dir = repo.wf(wf_id, "source")
    source_path = _original_workflow_file(source_dir)  # names source_dir if nothing is there

    dag_path = repo.wf(wf_id, "parsed", "dag.json")
    if not dag_path.is_file():
        raise FileNotFoundError(f"{dag_path} not found; run scripts/parse.py {wf_id} first")
    dag = read_json(dag_path)

    seg_root = repo.wf(wf_id, "segments")
    seg_dag_paths = sorted(seg_root.glob("seg_*/dag.json")) if seg_root.is_dir() else []
    if not seg_dag_paths:
        raise FileNotFoundError(f"no segments under {seg_root}; run scripts/segment.py {wf_id} first")
    segment_dags = [read_json(p) for p in seg_dag_paths]

    xml_text = parse.decode_xml(source_path.read_bytes())
    new_xml, capture_map = inject(xml_text, dag, segment_dags, capture_dir)

    out_path = source_dir / f"{source_path.stem}.instrumented.yxmd"
    with open(out_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(new_xml)
    write_json(repo.wf(wf_id, "golden", "capture_map.json"), capture_map)
    return out_path, capture_map


# --- CLI ---

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0003")
    parser.add_argument("--capture-dir", required=True,
                        help="where a real Alteryx run will write (or has written) the .yxdb captures")
    parser.add_argument("--import-set", default=None, metavar="SET",
                        help="import already-captured .yxdb files as this golden set, instead of instrumenting")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    repo = Repo(args.root)

    if args.import_set:
        capture_dir = Path(args.capture_dir)
        if not capture_dir.is_dir():
            parser.error(f"--capture-dir is not a directory: {capture_dir}")
        capture_map_path = repo.wf(args.wf_id, "golden", "capture_map.json")
        if not capture_map_path.is_file():
            parser.error(f"{capture_map_path} not found; run scripts/inject_outputs.py "
                        f"{args.wf_id} --capture-dir <dir> first")
        try:
            written = import_captures(repo, args.wf_id, args.import_set, capture_dir)
        except (FileNotFoundError, yxdb.YxdbError, ValueError) as exc:
            # a listed capture is missing or unreadable, or (ValueError) a capture_map.json row's
            # own "of_tool"/"segment"/"stream" is malformed -- lib.paths.Repo.wf raises for one
            # that isn't a plain relative path segment (e.g. containing ".."), which is exactly
            # the same "this row is bad" domain failure as the other two, not a crash.
            print(str(exc), file=sys.stderr)
            return 1
        except Exception:  # exit 2: a crash outside the checks above, which leaves nothing written
            traceback.print_exc()
            return 2
        try:
            record_golden_set(repo, args.wf_id, args.import_set)   # only after every CSV is written
        except Exception:  # exit 2: the CSVs are written, but the set is not recorded -- say so
            traceback.print_exc()
            print(f"golden set {args.import_set!r} was imported but NOT recorded in manifest.json's "
                  f"golden_sets", file=sys.stderr)
            return 2
        for path in written:
            print(path)
        return 0

    try:
        out_path, capture_map = _run_instrument(repo, args.wf_id, args.capture_dir)
    except FileNotFoundError as exc:
        parser.error(str(exc))  # exit 2: a prerequisite is missing, nothing was written
    except Exception:  # exit 2: a crash outside the checks above, which leaves nothing written
        traceback.print_exc()
        return 2

    print(f"instrumented {len(capture_map)} capture points -> {out_path}")
    print(f'"{ALTERYX_ENGINE_CMD}" "{out_path.resolve()}"')
    return 0


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
