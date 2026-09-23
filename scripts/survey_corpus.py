"""Triage a directory of real Alteryx workflow files before any of them enters this pipeline.

    python scripts/survey_corpus.py <dir> [--out report.md] [--json report.json] [--prefer procedures|dbt]

Point this at a directory holding a company's real `.yxmd`/`.yxwz`/`.yxzp` exports (any layout;
every such file anywhere under `<dir>` is one workflow) and it reports, per workflow: parse
status, tool counts by target class, unknown plugins by name, unresolved macros, a proposed tier
and output kind, dbt blockers and a segment-count estimate -- plus a corpus-wide plugin frequency
table, so the company knows which Alteryx tools to teach the parser and cookbook first
(docs/reference/real-workflows.md is the how-to for taking one of these all the way through).

Nothing here runs Alteryx or writes into `workflows/`: every workflow is parsed straight off disk
with `parse.parse_file` (a `.yxzp` is unzipped into a throwaway temp directory first) and never
touches the corpus directory. A parse failure is data, not a crash -- it becomes an `"error"` row
so one bad file never stops the rest of the corpus from being surveyed.

Exit codes follow the plan's Global Constraints as narrowed by this script's own contract: 0
whenever a report is produced, even when some files failed to parse (their rows say so); 2 for a
usage error (no such directory) or a crash outside the survey itself.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path
from typing import Sequence

import parse
import segment
import target_check
from lib.io import write_json

WORKFLOW_SUFFIXES = (".yxmd", ".yxwz", ".yxzp")

# `intake_prompt._default_write_mode`'s own mapping (program spec §5.4 / plan contract C6), kept
# here rather than imported so this script does not reach into that module's private names
# (tests/helpers.py keeps the same small copy, for the same reason): an output's raw Alteryx
# write mode, before any human has confirmed one, defaults the same way intake would.
_WRITE_MODE_DEFAULTS = {"update_insert": "merge", "truncate_append": "overwrite"}

# An absolute path of the machine this ran on -- never allowed to reach the report (hand-off
# hygiene): a wrapped exception's own text is the one place a stray absolute path could leak in
# (e.g. a `.yxzp` with no workflow inside names its own temp extraction directory).
_ABS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|]*")


def _write_mode(raw: str | None) -> str:
    return _WRITE_MODE_DEFAULTS.get(raw, raw or "overwrite")


def _sanitize(message: str) -> str:
    return _ABS_PATH_RE.sub("<path>", message)


def _tier(classes: dict[str, int]) -> str:
    """T1 all `sql`; T2 any `snowpark`; T3 any `manual` -- extended here to also cover `unknown`,
    since a plugin the parser cannot name is exactly the case a human has to look at before any
    tier below T3 could be trusted (design spec §2's vocabulary table fixes T1/T2/T3 only for the
    three classes a finished parse can produce; a pre-intake survey also has to place `unknown`)."""
    if classes["manual"] or classes["unknown"]:
        return "T3"
    if classes["snowpark"]:
        return "T2"
    return "T1"


def _blocker_text(blocker: dict) -> str:
    bits = [blocker["kind"]]
    for key in ("segment", "tool_id", "output", "mode"):
        value = blocker.get(key)
        if value:
            bits.append(f"{key}={value}")
    return " ".join(bits)


def _outputs_from(expanded: list[tuple[str, dict]]) -> dict:
    """Synthesize `target_check.dbt_blockers`' `outputs` argument straight from each output
    node's own parsed config -- there is no `intake/mappings.yaml` yet, so `write_mode`/`keys` are
    read as the parser found them, defaulted the same way intake's own first offer would be."""
    outputs = {}
    for node_id, node in expanded:
        if node["type"] != "output":
            continue
        cfg = node.get("config") or {}
        label = cfg.get("source") or "output"
        outputs[f"{label} (tool {node_id})"] = {
            "mode": _write_mode(cfg.get("write_mode")),
            "keys": cfg.get("keys") or [],
            "tool_ids": [node_id],
        }
    return outputs


def _safe_extract(archive: Path, dest: Path) -> None:
    """Unzip `archive` into `dest`, refusing any member that would land outside it (the same
    zip-slip guard `parse._unzip_packages` applies, written fresh here because that function is
    tied to unzipping in place inside a workflow's own `source/`, not into a throwaway temp dir)."""
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            target = (dest / member).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise ValueError(f"{archive.name} contains a path outside the package: {member}")
        zf.extractall(dest)


def _parse_one(path: Path) -> dict:
    """One workflow's dag, however it is packaged. A `.yxzp` is extracted to a throwaway temp
    directory first (never into the corpus directory itself) and its workflow found with
    `parse.workflow_file`, exactly as `parse.run` finds one after unpacking in place."""
    if path.suffix.lower() != ".yxzp":
        dag, _aliases = parse.parse_file(path)
        return dag
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        _safe_extract(path, tmp_dir)
        workflow_path = parse.workflow_file(tmp_dir)
        dag, _aliases = parse.parse_file(workflow_path)
        return dag


def _discover(directory: Path) -> list[Path]:
    return sorted((p for p in directory.rglob("*")
                  if p.is_file() and p.suffix.lower() in WORKFLOW_SUFFIXES),
                 key=lambda p: p.relative_to(directory).as_posix())


def _build_row(rel: str, dag: dict, expanded: list[tuple[str, dict]], prefer: str) -> dict:
    classes = {c: 0 for c in ("sql", "snowpark", "manual", "unknown")}
    nodes_cls: dict[str, str] = {}
    for node_id, node in expanded:
        cls = target_check.classify(node)
        nodes_cls[node_id] = cls
        classes[cls] += 1

    unknown_plugins = sorted({node["plugin"] for _, node in expanded
                              if node["type"] == "unknown" and node.get("plugin")})
    unresolved_macros = sorted({node["macro_path"] for _, node in expanded
                                if node["type"] == "macro" and node.get("unresolved")
                                and node.get("macro_path")})

    seg_result = segment.segment(dag)
    seg_members = seg_result["segments"]
    seg_nodes = {seg_id: target_check.expand([n for n in dag["nodes"] if n["tool_id"] in members])
                for seg_id, members in seg_members.items()}
    segment_targets = {
        seg: ("snowpark" if any(nodes_cls.get(node_id) == "snowpark" for node_id, _ in ns) else "sql")
        for seg, ns in seg_nodes.items()}
    outputs = _outputs_from(expanded)
    blockers = target_check.dbt_blockers(segment_targets, nodes_cls, seg_nodes, outputs)
    output_kind = "dbt" if prefer == "dbt" and not blockers else "procedures"

    return {
        "path": rel,
        "status": "ok",
        "tools": len(expanded),
        "classes": classes,
        "unknown_plugins": unknown_plugins,
        "unresolved_macros": unresolved_macros,
        "tier": _tier(classes),
        "output_kind": output_kind,
        "dbt_blockers": [_blocker_text(b) for b in blockers],
        "segments": len(seg_members),
    }


# --- the survey ---

def survey(directory: str | Path, *, prefer: str = "procedures") -> dict:
    """Every `.yxmd`/`.yxwz`/`.yxzp` under `directory`, one report row each, plus a corpus-wide
    plugin frequency table. `prefer` picks the same output-kind preference `target_check.py`
    takes (`"procedures"` or `"dbt"`) and is applied uniformly -- there is no per-workflow
    manifest here to read a preference from.

    Deterministic: file discovery is sorted by relative path, every node traversal follows the
    dag's own (parse-order) sequence, and `segment.segment` is itself a pure function of the dag,
    so two calls over the same directory produce byte-identical JSON.
    """
    directory = Path(directory)
    workflows: list[dict] = []
    plugin_counts: dict[tuple[str, str], int] = {}

    for path in _discover(directory):
        rel = path.relative_to(directory).as_posix()
        try:
            dag = _parse_one(path)
        except Exception as exc:  # a parse failure is data for this row, not a crash of the survey
            workflows.append({"path": rel, "status": "error",
                              "error": _sanitize(f"{type(exc).__name__}: {exc}")})
            continue

        expanded = target_check.expand(dag["nodes"])
        for _, node in expanded:
            plugin = node.get("plugin")
            if plugin:
                key = (plugin, node["type"])
                plugin_counts[key] = plugin_counts.get(key, 0) + 1
        workflows.append(_build_row(rel, dag, expanded, prefer))

    plugins = [{"plugin": plugin, "type": tool_type, "count": count}
              for (plugin, tool_type), count in plugin_counts.items()]
    plugins.sort(key=lambda e: (-e["count"], e["plugin"], e["type"]))

    return {"workflows": workflows, "plugins": plugins}


def render_markdown(report: dict) -> str:
    lines = ["# Real-corpus survey", ""]
    lines.append(f"{len(report['workflows'])} workflow file(s) found.")
    lines.append("")
    lines.append("| path | status | tier | output_kind | tools | sql | snowpark | manual | "
                 "unknown | segments | unknown plugins | unresolved macros | dbt blockers |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for wf in report["workflows"]:
        if wf["status"] == "error":
            lines.append(f"| {wf['path']} | error | | | | | | | | | | | {wf['error']} |")
            continue
        c = wf["classes"]
        lines.append(
            f"| {wf['path']} | ok | {wf['tier']} | {wf['output_kind']} | {wf['tools']} | "
            f"{c['sql']} | {c['snowpark']} | {c['manual']} | {c['unknown']} | {wf['segments']} | "
            f"{', '.join(wf['unknown_plugins']) or '-'} | "
            f"{', '.join(wf['unresolved_macros']) or '-'} | "
            f"{'; '.join(wf['dbt_blockers']) or '-'} |")
    lines.append("")
    lines.append("## Plugin frequency")
    lines.append("")
    lines.append("Teach `scripts/parsers/plugin_map.py` (or write a parser-recovery extension "
                 "under `scripts/parsers/ext/`, `scripts/parsers/ext/README.md`) the plugins "
                 "with `type: unknown` here first -- they block every workflow that uses them.")
    lines.append("")
    lines.append("| plugin | type | count |")
    lines.append("|---|---|---|")
    for p in report["plugins"]:
        lines.append(f"| {p['plugin']} | {p['type']} | {p['count']} |")
    lines.append("")
    return "\n".join(lines)


# --- CLI ---

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", help="a directory of real .yxmd/.yxwz/.yxzp files")
    parser.add_argument("--out", help="write the markdown report here")
    parser.add_argument("--json", help="write the JSON report here")
    parser.add_argument("--prefer", choices=["procedures", "dbt"], default="procedures")
    args = parser.parse_args(argv)

    directory = Path(args.directory)
    if not directory.is_dir():
        parser.error(f"{args.directory} is not a directory")

    try:
        report = survey(directory, prefer=args.prefer)
    except Exception:  # a crash outside the per-file survey itself, which writes nothing
        traceback.print_exc()
        return 2

    ok = sum(1 for w in report["workflows"] if w["status"] == "ok")
    errors = len(report["workflows"]) - ok
    print(f"{len(report['workflows'])} workflow(s) surveyed: {ok} ok, {errors} error, "
         f"{len(report['plugins'])} distinct plugin(s)")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_markdown(report))
    if args.json:
        write_json(Path(args.json), report)
    return 0


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
