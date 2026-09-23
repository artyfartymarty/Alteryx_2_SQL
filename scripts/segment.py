"""Deterministic DAG segmentation: `workflows/<id>/parsed/dag.json` -> `segments/` (program spec §7.2).

    python scripts/segment.py <wf_id> [--min-tools N] [--max-tools N] [--max-prompt-chars N] [--root .]

A segment becomes one Snowflake stored procedure, so the cuts below trade off translator context
size against segment count: Tool Containers, macro boundaries and Python tool boundaries are the
author's own grouping (a Python tool becomes a Snowpark procedure and is always cut alone) and are
respected first; everything else is merged or split to land each segment around
`min_tools`..`max_tools` tools, without ever separating an ordering-dependent chain (Sort -> ... ->
Unique/Multi-Row Formula/Record ID/Sample/Summarize First-Last) or a Join from its own two inputs.

Tool count alone under-weights a handful of tools with huge configs (a Formula with hundreds of
expressions), so every group is also kept under a prompt-size budget: `prompt_chars()` is a
compact-JSON character estimate of one node's `type`/`config`/`meta` (tokens are roughly a quarter
of that), and `size_chars` sums it across a group. A group under `max_tools` is still split when
its `size_chars` exceeds `max_prompt_chars`; a merge that would push the combined estimate over the
budget is skipped even when both sides are undersized by tool count; a single tool whose own
estimate alone exceeds the budget stands alone with a warning, since no split can help it. The
budget resolves `--max-prompt-chars` -> `manifest.json`'s `segmentation.max_prompt_chars` ->
`mappings/global.yaml`'s `segmentation.max_prompt_chars` -> `DEFAULT_MAX_PROMPT_CHARS` (see
`run()`); characters are an estimate, not a token count, and the default is conservative until
calibrated against real usage (docs/reference/large-workflows.md).

Exit codes follow the plan's Global Constraints: 0 whenever a segmentation is produced, even with
warnings -- an unsplittable oversized group or an ordering dependency crossing a macro or Python
tool is still a valid (if imperfect) segmentation, and the analyzer agent reviews warnings, so they
don't block the pipeline. 1 is a domain failure: no valid segmentation could be produced at all,
i.e. the group graph itself has a cycle and step 7 cannot find a topological order for it. 2 is for
usage errors: bad arguments, or a workflow that hasn't been parsed yet, so there is nothing to
segment.

Nothing here has run against real Snowflake or Alteryx; segmentation is purely structural.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback
from typing import Sequence

from lib.io import load_manifest, read_json, read_yaml, save_manifest, write_json
from lib.paths import Repo, add_root_arg
from lib.vocab import NON_DATA_TYPES, ORDER_DEPENDENT_TYPES

DEFAULT_MAX_PROMPT_CHARS = 60000


def prompt_chars(node: dict) -> int:
    """Compact-JSON character estimate of one node's prompt weight: `type`, `config` and `meta`
    only (tokens are roughly a quarter of this count). `annotation` and `raw_config` are excluded
    -- display-only, or already reflected in `config`."""
    payload = {"type": node.get("type"), "config": node.get("config"), "meta": node.get("meta")}
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":")))


class _DSU:
    """Union-find over tool ids, path compression + union by rank."""

    def __init__(self, ids: Sequence[str]):
        self.parent = {i: i for i in ids}
        self.rank = {i: 0 for i in ids}

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


# --- the algorithm ---

def segment(dag: dict, *, min_tools: int = 15, max_tools: int = 40, formula_heavy_cap: int = 20,
            max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS) -> dict:
    """Cut `dag` into segments. Returns
    `{"segments": {"seg_01": ["1","2"], ...}, "order": [[...], ...], "warnings": [...], "params": {...}}`.

    `segments` maps seg id to member tool ids (numeric-string order); `order` is the list of
    topological waves of seg ids (parallel-safe within a wave); ties everywhere break on the
    lowest tool id, so the result is fully deterministic. `max_prompt_chars` bounds each group's
    `size_chars` (module docstring) exactly as `max_tools` bounds its tool count.
    """
    nodes_by_id = {n["tool_id"]: n for n in dag["nodes"]}
    data_ids = sorted((t for t, n in nodes_by_id.items() if n["type"] not in NON_DATA_TYPES), key=int)
    data_id_set = set(data_ids)
    # dag-contract: container/comment/interface/action nodes never appear in edges; the filter
    # below is defensive so a malformed dag can't corrupt grouping.
    data_edges = [e for e in dag["edges"] if e["src"] in data_id_set and e["dst"] in data_id_set]
    warnings: list[str] = []

    outermost_cache: dict[str, str | None] = {}

    def outermost(tool_id: str) -> str | None:
        """The topmost Tool Container id enclosing `tool_id`, or None if it isn't in one."""
        if tool_id in outermost_cache:
            return outermost_cache[tool_id]
        cid = nodes_by_id[tool_id]["container_id"]
        seen: set[str] = set()
        while cid is not None and cid not in seen:
            seen.add(cid)
            parent = nodes_by_id.get(cid)
            if parent is None or parent.get("container_id") is None:
                break
            cid = parent["container_id"]
        outermost_cache[tool_id] = cid
        return cid

    def is_hard(e: dict) -> bool:
        """A hard cut: the edge touches a macro or Python tool node. Macros and Python tools are
        always their own segment (a Python tool becomes a Snowpark procedure)."""
        return nodes_by_id[e["src"]]["type"] in ("macro", "python") or nodes_by_id[e["dst"]]["type"] in ("macro", "python")

    def is_soft(e: dict) -> bool:
        """A soft cut: the edge crosses a Tool Container boundary (None is its own value)."""
        return outermost(e["src"]) != outermost(e["dst"])

    dsu = _DSU(data_ids)

    # Step 2: union everything that isn't a hard or soft cut.
    for e in sorted(data_edges, key=lambda e: (int(e["src"]), int(e["dst"]))):
        if is_hard(e) or is_soft(e):
            continue
        dsu.union(e["src"], e["dst"])

    # Browse rides with its upstream node's group (never counts toward size) unless that link is
    # itself a hard cut -- macros stay their own segment even when a Browse hangs off them.
    for tid in data_ids:
        if nodes_by_id[tid]["type"] != "browse":
            continue
        for e in data_edges:
            if e["dst"] == tid and not is_hard(e):
                dsu.union(tid, e["src"])

    # Step 3: ordering protection. From every order-dependent node (and every Summarize using
    # First/Last), walk upstream through single-input nodes until a Sort or a source, unioning the
    # whole path and overriding any soft cut it crosses. A hard cut stops the walk with a warning.
    protected_edges: set[frozenset[str]] = set()

    def is_first_or_last_summarize(node: dict) -> bool:
        if node["type"] != "summarize":
            return False
        fields = (node.get("config") or {}).get("fields") or []
        return any(f.get("action") in ("First", "Last") for f in fields)

    ordering_starts = [t for t in data_ids
                        if nodes_by_id[t]["type"] in ORDER_DEPENDENT_TYPES or is_first_or_last_summarize(nodes_by_id[t])]
    for start in ordering_starts:
        current = start
        while True:
            inbound = [e for e in data_edges if e["dst"] == current]
            if len(inbound) != 1:
                break
            pred = inbound[0]["src"]
            pred_node = nodes_by_id[pred]
            if pred_node["type"] in ("macro", "python"):
                warnings.append(f"ordering dependency of tool {start} crosses "
                                 f"{pred_node['type']} {pred}")
                break
            dsu.union(current, pred)
            protected_edges.add(frozenset((current, pred)))
            if pred_node["type"] == "sort":
                break
            current = pred

    # Step 4: join protection. A join's input edges may never be a split point. This step unions
    # nothing -- container cuts (already decided above) still win.
    for e in data_edges:
        if nodes_by_id[e["dst"]]["type"] == "join":
            protected_edges.add(frozenset((e["src"], e["dst"])))

    def groups_snapshot() -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for t in data_ids:
            groups.setdefault(dsu.find(t), []).append(t)
        return groups

    def size_of(members) -> int:
        """Data-node count toward min/max_tools: everything except Browse."""
        return sum(1 for t in members if nodes_by_id[t]["type"] != "browse")

    def size_chars(members) -> int:
        """Estimated prompt size toward `max_prompt_chars`: every member counts, Browse included
        (a Browse still carries its own `meta`/`config` into the translator's prompt)."""
        return sum(prompt_chars(nodes_by_id[t]) for t in members)

    def acyclic_after_merge(rep_a: str, rep_b: str) -> bool:
        """Would merging rep_a's and rep_b's groups keep the group-level graph a DAG?"""
        ra, rb = dsu.find(rep_a), dsu.find(rep_b)

        def super_root(t: str) -> str:
            r = dsu.find(t)
            return ra if r == rb else r

        adj: dict[str, set[str]] = {}
        for e in data_edges:
            sr, dr = super_root(e["src"]), super_root(e["dst"])
            if sr != dr:
                adj.setdefault(sr, set()).add(dr)
        color: dict[str, int] = {}

        def dfs(u: str) -> bool:
            color[u] = 1
            for v in adj.get(u, ()):
                c = color.get(v, 0)
                if c == 1 or (c == 0 and not dfs(v)):
                    return False
            color[u] = 2
            return True

        return all(dfs(u) for u in list(adj) if color.get(u, 0) == 0)

    # Step 5: merge undersized groups into the smallest eligible neighbour, one merge at a time
    # (state changes each time), until no more merges are possible.
    def merge_pass() -> bool:
        groups = groups_snapshot()
        for root in sorted(groups, key=lambda r: int(groups[r][0])):
            members = groups[root]
            if size_of(members) >= min_tools:
                continue
            member_set = set(members)
            neighbours: set[str] = set()
            for e in data_edges:
                if is_hard(e):
                    continue
                s, d = e["src"], e["dst"]
                other = d if s in member_set and d not in member_set else \
                    (s if d in member_set and s not in member_set else None)
                if other is not None:
                    neighbours.add(dsu.find(other))
            best_key = None
            best_root = None
            for nb_root in neighbours:
                nb_members = groups[nb_root]
                if size_of(members) + size_of(nb_members) > max_tools:
                    continue
                if size_chars(members) + size_chars(nb_members) > max_prompt_chars:
                    continue
                if not acyclic_after_merge(members[0], nb_members[0]):
                    continue
                key = (size_of(nb_members), int(nb_members[0]))
                if best_key is None or key < best_key:
                    best_key, best_root = key, nb_root
            if best_root is not None:
                dsu.union(members[0], groups[best_root][0])
                return True
        return False

    while merge_pass():
        pass

    # Step 6: split oversized groups at the bridge edge that best balances the halves, skipping
    # edges protected by steps 3-4, repeating until every group fits (or has no valid bridge left).
    def formula_heavy(members) -> bool:
        countable = size_of(members)
        heavy = sum(1 for t in members if nodes_by_id[t]["type"] in ("formula", "multi_row_formula"))
        return countable > 0 and heavy * 2 > countable

    def needs_split(members) -> bool:
        size = size_of(members)
        return (size > max_tools or (size > formula_heavy_cap and formula_heavy(members))
                or size_chars(members) > max_prompt_chars)

    def find_split(members):
        """The best non-protected bridge in `members`' induced subgraph, or None. Balances the two
        halves by estimated characters when `members` is over the prompt budget, by tool count
        otherwise -- a group can be oversized on tools without being anywhere near the character
        budget (and vice versa for a handful of huge-config tools), so each reason for splitting
        balances on its own measure."""
        by_chars = size_chars(members) > max_prompt_chars
        member_set = set(members)
        internal = [e for e in data_edges if e["src"] in member_set and e["dst"] in member_set]
        best = None
        for idx, e in enumerate(internal):
            if frozenset((e["src"], e["dst"])) in protected_edges:
                continue
            adj: dict[str, set[str]] = {}
            for j, e2 in enumerate(internal):
                if j == idx:
                    continue
                adj.setdefault(e2["src"], set()).add(e2["dst"])
                adj.setdefault(e2["dst"], set()).add(e2["src"])
            reached = {e["src"]}
            stack = [e["src"]]
            while stack:
                u = stack.pop()
                for v in adj.get(u, ()):
                    if v not in reached:
                        reached.add(v)
                        stack.append(v)
            if e["dst"] in reached:
                continue  # not a bridge: the group stays connected without this edge
            comp_a = reached
            comp_b = member_set - comp_a
            diff = (abs(size_chars(comp_a) - size_chars(comp_b)) if by_chars
                    else abs(size_of(comp_a) - size_of(comp_b)))
            key = (diff, int(e["src"]), int(e["dst"]))
            if best is None or key < best[0]:
                best = (key, comp_a, comp_b)
        return None if best is None else (best[1], best[2])

    groups_list = list(groups_snapshot().values())
    # An unsplittable group stays as it is through every later pass of the loop below, so its
    # warning is emitted the first time only (final fix wave M3), not once per pass.
    warned_over_cap: set[frozenset[str]] = set()
    changed = True
    while changed:
        changed = False
        next_groups = []
        for members in groups_list:
            if not needs_split(members):
                next_groups.append(members)
                continue
            split = find_split(members)
            if split is None:
                over_tools = size_of(members) > max_tools
                over_formula = not over_tools and formula_heavy(members) and size_of(members) > formula_heavy_cap
                over_chars = size_chars(members) > max_prompt_chars
                if over_tools or over_formula:
                    cap = max_tools if over_tools else formula_heavy_cap
                    detail = f"{size_of(members)} > {cap}"
                    if over_chars:
                        detail += f" and {size_chars(members)} characters > max_prompt_chars {max_prompt_chars}"
                else:
                    detail = f"{size_chars(members)} characters > max_prompt_chars {max_prompt_chars}"
                if frozenset(members) not in warned_over_cap:
                    warned_over_cap.add(frozenset(members))
                    warnings.append(f"segment {sorted(members, key=int)} stays above its size cap "
                                     f"({detail}): no splittable bridge remains")
                next_groups.append(members)
                continue
            comp_a, comp_b = split
            next_groups.append(sorted(comp_a, key=int))
            next_groups.append(sorted(comp_b, key=int))
            changed = True
        groups_list = next_groups

    # A tool whose own estimate alone is already over budget can never be fixed by splitting (it
    # stays alone, or ends up alone once its group is split down as far as step 6 can take it), so
    # it gets its own warning independent of how its group ended up -- named per tool, not per
    # segment, so the translator agent sees exactly which tool to expect trouble from.
    for tid in data_ids:
        tid_chars = prompt_chars(nodes_by_id[tid])
        if tid_chars > max_prompt_chars:
            warnings.append(f"tool {tid} alone is estimated at {tid_chars} characters, "
                             f"over segmentation.max_prompt_chars {max_prompt_chars}")

    # Step 7: number groups in topological order, ties by lowest tool id.
    root_of = {t: gi for gi, members in enumerate(groups_list) for t in members}
    successors: dict[int, set[int]] = {gi: set() for gi in range(len(groups_list))}
    indeg = {gi: 0 for gi in range(len(groups_list))}
    for e in data_edges:
        sg, dg = root_of[e["src"]], root_of[e["dst"]]
        if sg != dg and dg not in successors[sg]:
            successors[sg].add(dg)
            indeg[dg] += 1

    remaining = dict(indeg)
    assigned: set[int] = set()
    levels: list[list[int]] = []
    while len(assigned) < len(groups_list):
        frontier = sorted((gi for gi in range(len(groups_list)) if gi not in assigned and remaining[gi] == 0),
                          key=lambda gi: int(groups_list[gi][0]))
        if not frontier:
            leftover = sorted((gi for gi in range(len(groups_list)) if gi not in assigned),
                              key=lambda gi: int(groups_list[gi][0]))
            warnings.append(f"segment graph has a cycle among groups starting at tools "
                             f"{[groups_list[gi][0] for gi in leftover]}")
            frontier = leftover
        levels.append(frontier)
        assigned.update(frontier)
        for gi in frontier:
            for dg in successors[gi]:
                remaining[dg] -= 1

    seg_id_of: dict[int, str] = {}
    n = 1
    for level in levels:
        for gi in level:
            seg_id_of[gi] = f"seg_{n:02d}"
            n += 1

    segments: dict[str, list[str]] = {}
    for level in levels:
        for gi in level:
            segments[seg_id_of[gi]] = sorted(groups_list[gi], key=int)
    order = [[seg_id_of[gi] for gi in level] for level in levels]

    return {"segments": segments, "order": order, "warnings": warnings,
            "params": {"min_tools": min_tools, "max_tools": max_tools,
                       "formula_heavy_cap": formula_heavy_cap, "max_prompt_chars": max_prompt_chars}}


def build_segment_dag(dag: dict, seg: str, members: list[str], owner: dict[str, str]) -> dict:
    """One segment's sub-DAG (contract C7): its own nodes/edges plus inbound/outbound crossings."""
    member_set = set(members)
    nodes_by_id = {n["tool_id"]: n for n in dag["nodes"]}
    seg_nodes = [nodes_by_id[t] for t in sorted(member_set, key=int) if t in nodes_by_id]

    internal, inbound, outbound = [], [], []
    for e in sorted(dag["edges"], key=lambda e: (int(e["src"]), int(e["dst"]), e.get("dst_order", 1))):
        src_in, dst_in = e["src"] in member_set, e["dst"] in member_set
        if src_in and dst_in:
            internal.append(dict(e))
        elif dst_in:
            inbound.append({**e, "from_segment": owner.get(e["src"])})
        elif src_in:
            outbound.append({**e, "to_segment": owner.get(e["dst"])})

    return {"workflow": dag.get("workflow"), "segment": seg, "nodes": seg_nodes,
            "edges": internal, "inbound": inbound, "outbound": outbound}


# --- one workflow under a repo root ---

def run(repo: Repo, wf_id: str, **overrides) -> dict:
    """Segment `workflows/<wf_id>/parsed/dag.json` and write `segments/` (program spec §7.2).

    Each segmentation parameter (`min_tools`, `max_tools`, `formula_heavy_cap`, `max_prompt_chars`)
    resolves `overrides` (CLI) -> `manifest.segmentation` -> `mappings/global.yaml`'s
    `segmentation` block -> `segment()`'s own default, the first of those that names the key
    winning; `min_tools`/`max_tools` have never had anything in `global.yaml` to read, so for them
    this is unchanged from before. Writes `segments/seg_NN/dag.json` (contract C7),
    `segments/order.json`, `segments/segmentation.json`, and sets `manifest.segments`. Re-running
    with the same inputs reproduces the same files byte for byte. A `seg_NN` directory from an
    earlier run that the new result no longer uses is removed if it holds nothing but `dag.json`;
    if it holds anything else it is left alone and a warning is recorded instead.
    """
    dag = read_json(repo.wf(wf_id, "parsed", "dag.json"))
    manifest = load_manifest(repo, wf_id)
    global_map = read_yaml(repo.global_mappings) if repo.global_mappings.is_file() else {}
    params = dict((global_map or {}).get("segmentation") or {})
    params.update(manifest.get("segmentation") or {})
    params.update({k: v for k, v in overrides.items() if v is not None})
    result = segment(dag, **params)

    owner = {t: s for s, members in result["segments"].items() for t in members}
    seg_root = repo.wf(wf_id, "segments")
    existing = {p.name for p in seg_root.glob("seg_*") if p.is_dir()} if seg_root.exists() else set()
    for stale in sorted(existing - set(result["segments"])):
        stale_dir = seg_root / stale
        extra = [p for p in stale_dir.iterdir() if p.name != "dag.json"]
        if extra:
            result["warnings"].append(f"left {stale} in place: it holds more than dag.json ({stale_dir})")
        else:
            shutil.rmtree(stale_dir)

    for seg_id, members in result["segments"].items():
        write_json(repo.seg(wf_id, seg_id, "dag.json"), build_segment_dag(dag, seg_id, members, owner))
    write_json(repo.wf(wf_id, "segments", "order.json"), result["order"])
    write_json(repo.wf(wf_id, "segments", "segmentation.json"), result)

    manifest["segments"] = list(result["segments"])
    save_manifest(repo, manifest)
    return result


# --- CLI ---

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0003")
    parser.add_argument("--min-tools", type=int, default=None, dest="min_tools")
    parser.add_argument("--max-tools", type=int, default=None, dest="max_tools")
    parser.add_argument("--max-prompt-chars", type=int, default=None, dest="max_prompt_chars")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    try:
        result = run(Repo(args.root), args.wf_id, min_tools=args.min_tools, max_tools=args.max_tools,
                      max_prompt_chars=args.max_prompt_chars)
    except FileNotFoundError as exc:
        parser.error(str(exc))  # exit 2: nothing parsed yet, so there is nothing to segment
    except Exception:  # exit 2: a crash outside segmentation itself, which leaves nothing written
        traceback.print_exc()
        return 2
    print(f"{len(result['segments'])} segments, {len(result['order'])} waves, "
          f"{len(result['warnings'])} warnings")
    for warning in result["warnings"]:
        print(f"  {warning}", file=sys.stderr)
    # A warning alone isn't a failure (see the exit-code note above); only a group graph that
    # step 7 couldn't order at all -- a cycle -- means no valid segmentation was produced.
    return 1 if any("cycle" in w for w in result["warnings"]) else 0


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
