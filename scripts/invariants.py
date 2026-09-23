"""Invariants that `parse.py --check` enforces on dag.json. Any failure -> parse_report.json status
INVARIANT_VIOLATION, which is what triggers the parser-recovery agent. Keep these cheap and
structural; semantics are the analyzer's job.

    python scripts/invariants.py <wf_id> [--root .]

prints the violations of an already-parsed workflow and exits 1 if there are any. It reads
`parsed/dag.json` and never writes: `parse.py` owns `parse_report.json`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from lib.io import read_json
from lib.paths import Repo, add_root_arg
from lib.vocab import NON_DATA_TYPES

# Above this share of data nodes, an unexplained `unknown` tool means the parse "succeeded" but
# the standard path clearly does not understand this file (program spec §6.4).
MAX_UNKNOWN_SHARE = 0.10


def check(xml_text: str, dag: dict) -> list[str]:
    errs = []
    nodes = {str(n["tool_id"]): n for n in dag["nodes"]}
    # 1. every <Node ToolID="..."> in the XML (including ones nested in containers/macros) is in dag.json
    xml_ids = set(re.findall(r'<Node\s+ToolID="(\d+)"', xml_text))
    missing = xml_ids - set(nodes)
    if missing:
        errs.append(f"nodes in XML but not in dag: {sorted(missing)[:10]}")
    # 2. every connection resolves to a known node and a known anchor
    for c in dag["edges"]:
        for side in ("src", "dst"):
            if str(c[side]) not in nodes:
                errs.append(f"edge {c} references unknown node on {side}")
        src = nodes.get(str(c["src"]))
        if src and c.get("src_anchor") and c["src_anchor"] not in src.get("out_anchors", [c["src_anchor"]]):
            errs.append(f"edge {c} uses unknown anchor {c['src_anchor']} on tool {c['src']}")
    # 3. plugin recognized or explicitly marked unknown (never silently dropped)
    for n in dag["nodes"]:
        if n.get("type") is None:
            errs.append(f"tool {n['tool_id']} has no type (plugin {n.get('plugin')})")
    # 4. macros referenced are resolved to files or flagged
    for n in dag["nodes"]:
        if n.get("type") == "macro" and not (n.get("macro_path") or n.get("unresolved")):
            errs.append(f"macro tool {n['tool_id']} has no path")
    # 5. no orphan Input/Output tools lost their config (paths / connections must be non-empty)
    for n in dag["nodes"]:
        if n.get("type") in ("input", "output") and not n.get("config", {}).get("source"):
            errs.append(f"{n['type']} tool {n['tool_id']} has empty source/target")
    # 6. DAG is acyclic (Alteryx iterative macros are the only legal cycle, and they live inside a macro node)
    indeg = {k: 0 for k in nodes}
    adj: dict[str, list[str]] = {}
    for c in dag["edges"]:  # an edge to a node that does not exist is invariant 2's problem, not a cycle
        src, dst = str(c["src"]), str(c["dst"])
        if dst in indeg and src in indeg:
            indeg[dst] += 1
            adj.setdefault(src, []).append(dst)
    seen, frontier = 0, [k for k, v in indeg.items() if v == 0]
    while frontier:
        k = frontier.pop()
        seen += 1
        for d in adj.get(k, []):
            indeg[d] -= 1
            if indeg[d] == 0:
                frontier.append(d)
    if seen != len(nodes):
        errs.append("cycle detected outside a macro")
    # 7. every join is fed on both sides, exactly once
    for n in dag["nodes"]:
        if n.get("type") != "join":
            continue
        for anchor in ("Left", "Right"):
            fed = [c for c in dag["edges"]
                   if str(c["dst"]) == str(n["tool_id"]) and c.get("dst_anchor") == anchor]
            if len(fed) != 1:
                errs.append(f"join tool {n['tool_id']} has {len(fed)} inbound edges on {anchor} (expected 1)")
    # 8. unknown tools nobody has explained are a small minority of the data nodes
    data = [n for n in dag["nodes"] if n.get("type") not in NON_DATA_TYPES]
    opaque = [n for n in data if n.get("type") == "unknown" and "behavior" not in n]
    if data and len(opaque) / len(data) > MAX_UNKNOWN_SHARE:
        ids = [str(n["tool_id"]) for n in opaque][:10]
        errs.append(f"unknown tools without a behavior: {len(opaque)} of {len(data)} data nodes "
                    f"(max {MAX_UNKNOWN_SHARE:.0%}): {ids}")
    return errs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check a parsed workflow's structural invariants")
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0003")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    import parse  # here, not at module scope: parse.py imports this module

    repo = Repo(args.root)
    try:
        source = parse.workflow_file(repo.wf(args.wf_id, "source"))
    except FileNotFoundError as exc:
        parser.error(str(exc))
    dag = read_json(repo.wf(args.wf_id, "parsed", "dag.json"))
    errs = check(parse.decode_xml(source.read_bytes()), dag)
    print(json.dumps({"errors": errs, "node_count": len(dag["nodes"])}, indent=2))
    return 1 if errs else 0


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
