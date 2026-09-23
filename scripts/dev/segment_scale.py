"""Time `segment.segment()` on a synthetic chain of Formula tools (production backlog, "Segmenter scale").

    python scripts/dev/segment_scale.py --tools N [--tools M ...]

For each N it builds one linear chain -- an Input tool, N-2 Formula tools, an Output tool, each wired
to the next -- and prints `tools=N segments=S seconds=T`: the number of segments and the wall time of
`segment.segment()` alone, to one decimal. The three node shapes (type, config, field metadata) are
copied from the parsed `wf_0002` sample's first Input, first Formula and its Output tool and written
out below, so the measurement depends on no committed workflow tree. Default segmentation parameters.

A development measurement, not part of the pipeline: it reads nothing and writes nothing (so it takes
no `--root`). Exit codes: 0 measured; 2 a usage error (no `--tools`, or a size below 3) or a crash.
"""
from __future__ import annotations

import argparse
import copy
import sys
import time
import traceback
from pathlib import Path
from typing import Sequence

if __package__ in (None, ""):  # `python scripts/dev/segment_scale.py` puts scripts/ on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import segment

_FIELDS = [
    {"name": "CUST_ID", "type": "Int32", "size": 4, "scale": None},
    {"name": "NAME", "type": "V_WString", "size": 60, "scale": None},
    {"name": "CITY", "type": "V_String", "size": 40, "scale": None},
    {"name": "TIER", "type": "V_String", "size": 10, "scale": None},
    {"name": "ORDER_ID", "type": "Int32", "size": 4, "scale": None},
    {"name": "AMOUNT", "type": "Double", "size": 8, "scale": None},
    {"name": "ORDER_DATE", "type": "Date", "size": 10, "scale": None},
    {"name": "MATCH_FLAG", "type": "V_String", "size": 12, "scale": None},
]
_INPUT = {
    "type": "input", "plugin": "AlteryxBasePluginsGui.DbFileInput.DbFileInput", "container_id": None,
    "config": {"source": "customers.yxdb", "format": "yxdb", "query": None, "alias": None, "csv": None,
               "record_limit": None},
    "annotation": "", "in_anchors": [], "out_anchors": ["Output"], "meta": {"Output": _FIELDS[:4]},
}
_FORMULA = {
    "type": "formula", "plugin": "AlteryxBasePluginsGui.Formula.Formula", "container_id": None,
    "config": {"formulas": [{"field": "MATCH_FLAG", "expression": "\"MATCHED\"", "type": "V_String",
                             "size": 12}]},
    "annotation": "", "in_anchors": ["Input"], "out_anchors": ["Output"], "meta": {"Output": _FIELDS},
}
_OUTPUT = {
    "type": "output", "plugin": "AlteryxBasePluginsGui.DbFileOutput.DbFileOutput", "container_id": None,
    "config": {"source": "<scrubbed:dw_sales>", "format": "db", "alias": "dw_sales",
               "table": "dbo.CUSTOMER_ORDER_FACT", "write_mode": "append", "keys": [], "pre_sql": None,
               "post_sql": None},
    "annotation": "", "in_anchors": ["Input"], "out_anchors": [], "meta": {"Output": _FIELDS},
}


def chain(tools: int) -> dict:
    """A dag.json-shaped linear chain of `tools` tools: Input, `tools - 2` Formulas, Output."""
    nodes, edges = [], []
    for number in range(1, tools + 1):
        shape = _INPUT if number == 1 else _OUTPUT if number == tools else _FORMULA
        nodes.append({"tool_id": str(number), **copy.deepcopy(shape)})
        if number > 1:
            edges.append({"src": str(number - 1), "src_anchor": "Output", "dst": str(number),
                          "dst_anchor": "Input", "dst_order": 1, "wireless": False})
    return {"workflow": f"synthetic_chain_{tools}", "nodes": nodes, "edges": edges}


def measure(tools: int) -> tuple[int, float]:
    """(segment count, seconds spent in `segment.segment()`) for a chain of `tools` tools."""
    dag = chain(tools)
    started = time.perf_counter()
    result = segment.segment(dag)
    return len(result["segments"]), time.perf_counter() - started


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tools", type=int, action="append", required=True, metavar="N",
                        help="chain length to time (repeatable); at least 3")
    args = parser.parse_args(argv)
    too_small = [size for size in args.tools if size < 3]
    if too_small:
        parser.error(f"--tools must be at least 3 (an Input, a Formula, an Output), got {too_small[0]}")
    try:
        for size in args.tools:
            segments, seconds = measure(size)
            print(f"tools={size} segments={segments} seconds={seconds:.1f}", flush=True)
    except Exception:  # exit 2: nothing here is a domain failure
        traceback.print_exc()
        return 2
    return 0


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
