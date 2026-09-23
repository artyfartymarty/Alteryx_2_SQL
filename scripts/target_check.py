"""Propose the output target of every segment and the workflow's output kind (spec §3.1).

    target_check.py <wf_id> [--prefer auto|procedures|dbt] [--root .]

Writes segments/targets.json. Exit 0 written; 1 written but the workflow has unknown nodes; 2 usage.

`--prefer auto` (the default) resolves the workflow's preference from `manifest.json`'s own
`output_target` if set, else `mappings/global.yaml`'s `program.output_target`, else
`"procedures"` (coordinator ruling); an explicit `--prefer procedures|dbt` overrides both and is
used as given.

Classification walks each node AND, recursively, the nodes of a resolved macro's `sub_dag`: a
macro is a folded-up workflow, so a Python tool, a Run Command or an unknown tool inside one
decides the segment's target and the dbt blockers exactly as it would at the top level. Such a
node appears under the id `"<macro_tool_id>/<sub_tool_id>"`, nested again as needed.
"""
from __future__ import annotations

import argparse
import re
import sys
import traceback
from collections.abc import Sequence

from lib.io import read_json, read_yaml, write_json
from lib.paths import Repo, add_root_arg
from lib.vocab import DATA_LESS_TYPES
from parsers.plugin_map import node_class

DBT_MODES = {"overwrite", "append", "merge"}
PLAIN_STATEMENT = re.compile(r"^\s*(DELETE|UPDATE|INSERT|TRUNCATE|CALL)\b", re.IGNORECASE)
PREFERENCES = {"procedures", "dbt"}


def _plain(sql: str | None) -> bool:
    if not sql or not sql.strip():
        return True
    statements = [s for s in sql.split(";") if s.strip()]
    return len(statements) == 1 and bool(PLAIN_STATEMENT.match(statements[0]))


def expand(nodes: list[dict] | None, prefix: str = "") -> list[tuple[str, dict]]:
    """Every data-carrying node, paired with the id it is known by -- and then, recursively, the
    nodes of a resolved macro's own `sub_dag` under `"<macro_tool_id>/<sub_tool_id>"` (nested
    again for a macro inside a macro).

    A macro is not a tool: it is a workflow someone folded up. `plugin_map` maps the TYPE `macro`
    to `sql` because the node itself transforms nothing, so until this descended, a Python tool, a
    Run Command or an unknown tool hidden inside one was invisible to the whole decision (final
    fix wave F4 / whole-branch review I3).
    """
    expanded: list[tuple[str, dict]] = []
    for node in nodes or []:
        if node.get("type") in DATA_LESS_TYPES:
            continue
        node_id = f"{prefix}{node['tool_id']}"
        expanded.append((node_id, node))
        sub_dag = node.get("sub_dag")
        if sub_dag:
            expanded.extend(expand(sub_dag.get("nodes"), f"{node_id}/"))
    return expanded


def classify(node: dict) -> str:
    """`plugin_map.node_class`, except that an UNRESOLVED macro (`sub_dag: null` -- the `.yxmc`
    was never found) is `unknown`: nothing at all is known about what it does, so calling it
    `sql` would be a guess, and `expand` has no sub-DAG to descend into."""
    tool_type = node.get("type") or "unknown"
    if tool_type == "macro" and not node.get("sub_dag"):
        return "unknown"
    return node_class(tool_type)


def dbt_blockers(segment_targets: dict[str, str], nodes: dict[str, str],
                 seg_nodes: dict[str, list[tuple[str, dict]]], outputs: dict) -> list[dict]:
    """`seg_nodes` holds `expand()`'s (id, node) pairs, so a blocker inside a macro names the
    nested id (`"2/6"`) -- the only id that identifies it."""
    blockers: list[dict] = []
    for seg, target in segment_targets.items():
        if target == "snowpark":
            blockers.append({"segment": seg, "kind": "snowpark_segment"})
        for node_id, node in seg_nodes[seg]:
            cls = nodes.get(node_id)
            if cls == "manual":
                blockers.append({"segment": seg, "kind": "manual_node", "tool_id": node_id})
            elif cls == "unknown":
                blockers.append({"segment": seg, "kind": "unknown_node", "tool_id": node_id})
            if node["type"] == "output":
                cfg = node.get("config") or {}
                if not _plain(cfg.get("pre_sql")):
                    blockers.append({"segment": seg, "kind": "presql_not_plain", "tool_id": node_id})
                if not _plain(cfg.get("post_sql")):
                    blockers.append({"segment": seg, "kind": "postsql_not_plain", "tool_id": node_id})
    if not outputs:
        blockers.append({"segment": None, "kind": "no_outputs"})
    for key, entry in sorted(outputs.items()):
        mode = (entry or {}).get("mode")
        # `intake/mappings.yaml` only ever names TOP-LEVEL tool ids (intake_touchpoints.py does
        # not look inside a macro), so this matches against the id as written there.
        seg = next((s for s, ns in seg_nodes.items()
                    if any(node_id in (entry.get("tool_ids") or []) for node_id, _ in ns)), None)
        if mode not in DBT_MODES:
            blockers.append({"segment": seg, "kind": "write_mode_unsupported", "output": key, "mode": mode})
        elif mode == "merge" and not entry.get("keys"):
            blockers.append({"segment": seg, "kind": "merge_without_keys", "output": key})
    return blockers


def _resolve_preference(repo: Repo, wf_id: str, prefer: str) -> str:
    """`--prefer auto` (coordinator ruling): `manifest.json["output_target"]` if present, else
    `mappings/global.yaml`'s `program.output_target`, else `"procedures"`. An explicit
    `--prefer procedures|dbt` is returned unchanged (argparse's own `choices` already constrains
    it to a valid value).

    A resolved value outside `PREFERENCES` is a usage error (fix round 1, coordinator ruling): it
    is never written into targets.json next to a reason that silently assumed "procedures" -- the
    caller's own `main()` routes this `ValueError` to exit 2, and `target_check` never gets far
    enough to call `write_json`.
    """
    if prefer != "auto":
        return prefer
    manifest_path = repo.wf(wf_id, "manifest.json")
    if manifest_path.is_file():
        manifest_target = (read_json(manifest_path) or {}).get("output_target")
        if manifest_target:
            if manifest_target not in PREFERENCES:
                raise ValueError(f"manifest.json output_target {manifest_target!r} is not one "
                                 f"of {sorted(PREFERENCES)}")
            return manifest_target
    if repo.global_mappings.is_file():
        program = (read_yaml(repo.global_mappings) or {}).get("program") or {}
        global_target = program.get("output_target")
        if global_target:
            if global_target not in PREFERENCES:
                raise ValueError(f"mappings/global.yaml program.output_target {global_target!r} "
                                 f"is not one of {sorted(PREFERENCES)}")
            return global_target
    return "procedures"


def target_check(repo: Repo, wf_id: str, prefer: str) -> dict:
    prefer = _resolve_preference(repo, wf_id, prefer)
    dag = read_json(repo.wf(wf_id, "parsed", "dag.json"))
    order = read_json(repo.wf(wf_id, "segments", "order.json"))
    segments = [seg for wave in order for seg in wave]
    seg_nodes = {seg: expand(read_json(repo.seg(wf_id, seg, "dag.json"))["nodes"]) for seg in segments}
    nodes = {node_id: classify(node) for node_id, node in expand(dag["nodes"])}
    segment_targets = {seg: ("snowpark" if any(nodes.get(node_id) == "snowpark" for node_id, _ in ns) else "sql")
                       for seg, ns in seg_nodes.items()}
    mappings_path = repo.wf(wf_id, "intake", "mappings.yaml")
    outputs = (read_yaml(mappings_path) or {}).get("outputs") or {} if mappings_path.is_file() else {}
    blockers = dbt_blockers(segment_targets, nodes, seg_nodes, outputs)
    if prefer == "dbt" and not blockers:
        kind, reason = "dbt", "preference dbt; every segment is sql and every output is dbt-expressible"
    elif prefer == "dbt":
        kind = "procedures"
        reason = "dbt refused: " + "; ".join(sorted({f"{b['segment'] or 'workflow'} {b['kind']}" for b in blockers}))
    else:
        kind, reason = "procedures", "preference procedures"
    result = {"preference": prefer, "output_kind": kind, "reason": reason, "dbt_blockers": blockers,
              "segments": segment_targets,
              "nodes": {tool_id: cls for tool_id, cls in sorted(nodes.items()) if cls != "sql"}}
    write_json(repo.wf(wf_id, "segments", "targets.json"), result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id")
    parser.add_argument("--prefer", choices=["auto", "procedures", "dbt"], default="auto")
    add_root_arg(parser)
    args = parser.parse_args(argv)
    try:
        result = target_check(Repo(args.root), args.wf_id, args.prefer)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"target_check: {args.wf_id}: {exc}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 2
    print(f"{args.wf_id}: output_kind={result['output_kind']} segments={result['segments']}")
    return 1 if "unknown" in result["nodes"].values() else 0


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
