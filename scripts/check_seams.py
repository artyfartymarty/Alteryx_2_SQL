"""Check that every inter-segment stream agrees on both sides of its seam (large workflows, Task W2).

    check_seams.py <wf_id> [--segments seg_01,seg_02] [--root .]

A **seam** is one work stream crossing a segment cut: the producer contract's `outputs[]` entry
(`kind: "work"`) and the consumer contract's `inputs[]` entry that carries its `stream`. They agree
when they name the same table and stream, list the same columns in the same order with the same
type FAMILY (`lib.types_map.type_family`, the function `compare.py` judges a schema with -- so a
precision or length change is not a mismatch, `FLOAT` against `NUMBER(38,0)` is) and the same
nullability, and declare the same keys. On top of that, per seam: exactly one segment produces the
table, it is the segment the consumer says the input comes `from`, and it runs in an earlier wave
than the consumer. Where a segment's sub-DAG (`segments/<seg>/dag.json`, contract C7) shows a stream
crossing into it, the consumer's contract must declare that input at all. Across the workflow, no
table is written by two work outputs.

Writes `segments/seams.json`:
`{"ok", "seams": [{"producer", "consumer", "stream", "table", "status": "ok"|"mismatch",
"problems": [...]}], "duplicates": [{"table", "producers": [...]}]}`. Every problem of every seam is
listed, never only the first. `producer` is the segment that writes the table -- or, when none or
several do, the segment the consumer declared it `from`.

`--segments` restricts the check to seams whose CONSUMER is listed (the batched analyzer checks
each batch as it is written); their producers must still exist, and a duplicate is reported when
any of its producers is listed.

Exit codes: 0 every seam agrees; 1 any mismatch or duplicate -- the first stderr line is
`seam-mismatch: <producer>-><consumer> <stream>` (the orchestrator's park reason), then every
mismatch and its problems; 2 usage (no `segments/order.json`, an unknown `--segments` id) or a
crash (a contract that is not JSON), with nothing written.

Nothing here has run on Snowflake or Alteryx: it compares two JSON files the analyzer wrote.
"""
from __future__ import annotations

import argparse
import re
import sys
import traceback
from typing import Sequence

from lib.io import read_json, write_json
from lib.paths import Repo, add_root_arg
from lib.types_map import type_family


class UnknownSegment(ValueError):
    """A `--segments` id that `segments/order.json` does not list: a usage error, exit 2."""


def _base_type(sql_type: object) -> str:
    """`DECIMAL(19,2)` → `DECIMAL`: the comparison `compare.py` falls back on for two types
    `type_family` does not know ("other" never proves two types are the same family)."""
    return re.sub(r"\(.*", "", str(sql_type)).strip().upper()


def _same_family(produced: object, consumed: object) -> bool:
    mine, theirs = type_family(str(produced)), type_family(str(consumed))
    return mine == theirs and (mine != "other" or _base_type(produced) == _base_type(consumed))


def _column_problems(produced: list[dict], consumed: list[dict]) -> list[str]:
    names_p = [str(c.get("name")).upper() for c in produced]
    names_c = [str(c.get("name")).upper() for c in consumed]
    if names_p != names_c:
        return [f"columns {names_p} produced but {names_c} consumed"]
    problems = []
    for p, c in zip(produced, consumed):
        if not _same_family(p.get("type"), c.get("type")):
            problems.append(f"{p.get('name')}: {p.get('type')} ({type_family(str(p.get('type')))}) produced, "
                            f"{c.get('type')} ({type_family(str(c.get('type')))}) consumed")
        if bool(p.get("nullable", True)) != bool(c.get("nullable", True)):
            problems.append(f"{p.get('name')}: nullable {bool(p.get('nullable', True))} produced, "
                            f"{bool(c.get('nullable', True))} consumed")
    return problems


def _keys(entry: dict) -> list[str]:
    return sorted(str(k).upper() for k in entry.get("keys") or [])


def _table(entry: dict) -> str:
    return str(entry.get("table") or "").upper()


def _seam(producer, consumer: str, stream, table, problems: list[str]) -> dict:
    return {"producer": producer, "consumer": consumer, "stream": stream, "table": table,
            "status": "mismatch" if problems else "ok", "problems": problems}


def _crossings(repo: Repo, wf_id: str, seg: str) -> list[tuple[str, str | None]]:
    """(stream, from_segment) for every distinct stream the segment's sub-DAG shows crossing into
    it, in edge order; nothing when the segmenter has not written the sub-DAG."""
    path = repo.seg(wf_id, seg, "dag.json")
    if not path.is_file():
        return []
    seen: list[tuple[str, str | None]] = []
    for edge in read_json(path).get("inbound") or []:
        crossing = (f"{edge.get('src')}_{edge.get('src_anchor')}", edge.get("from_segment"))
        if crossing not in seen:
            seen.append(crossing)
    return seen


def check_seams(repo: Repo, wf_id: str, segments: list[str] | None = None) -> dict:
    """Check every seam (or, with `segments`, every seam INTO those segments) and write
    `segments/seams.json`. Raises FileNotFoundError without `segments/order.json` and UnknownSegment
    for a `segments` id that is not in it -- both before anything is written."""
    order = read_json(repo.wf(wf_id, "segments", "order.json"))
    every = [seg for wave in order for seg in wave]
    wave_of = {seg: index for index, wave in enumerate(order) for seg in wave}
    unknown = [seg for seg in segments or [] if seg not in wave_of]
    if unknown:
        raise UnknownSegment(f"{', '.join(unknown)} not in workflows/{wf_id}/segments/order.json")
    scope = set(every if segments is None else segments)

    contracts = {seg: read_json(path) for seg in every if (path := repo.seg(wf_id, seg, "contract.json")).is_file()}
    producers: dict[str, list[tuple[str, dict]]] = {}
    for seg, contract in contracts.items():
        for output in contract.get("outputs") or []:
            if output.get("kind") == "work" and output.get("table"):
                producers.setdefault(_table(output), []).append((seg, output))

    def work_output(seg, stream) -> dict | None:
        return next((o for o in (contracts.get(seg) or {}).get("outputs") or []
                     if o.get("kind") == "work" and o.get("stream") == stream), None)

    seams: list[dict] = []
    for consumer in every:
        if consumer not in scope or consumer not in contracts:
            continue
        declared = [entry for entry in contracts[consumer].get("inputs") or [] if entry.get("stream")]
        for entry in declared:
            stream, table, declared_from = entry.get("stream"), entry.get("table"), entry.get("from")
            found = producers.get(_table(entry), [])
            problems: list[str] = []
            producer = declared_from
            output = None
            if len(found) > 1:
                problems.append(f"{table} is produced by {sorted(seg for seg, _ in found)}")
            elif found:
                producer, output = found[0]
                if producer != declared_from:
                    problems.append(f"declared from {declared_from} but produced by {producer}")
                if output.get("stream") != stream:
                    problems.append(f"stream {output.get('stream')} produced, {stream} consumed")
            elif (output := work_output(declared_from, stream)) is not None:
                problems.append(f"table {table} consumed, but {declared_from} writes {stream} to {output.get('table')}")
            else:
                problems.append(f"no segment produces {table} (stream {stream}, declared from {declared_from})")
            if output is not None:
                problems += _column_problems(output.get("columns") or [], entry.get("columns") or [])
                if _keys(output) != _keys(entry):
                    problems.append(f"keys {_keys(output)} produced, {_keys(entry)} consumed")
                if producer in wave_of and wave_of[producer] >= wave_of[consumer]:
                    problems.append(f"{producer} runs in wave {wave_of[producer] + 1}, but {consumer} reads "
                                    f"{stream} in wave {wave_of[consumer] + 1}")
            seams.append(_seam(producer, consumer, stream, table, problems))

        streams = {entry.get("stream") for entry in declared}
        for stream, from_segment in _crossings(repo, wf_id, consumer):
            if stream not in streams:
                output = work_output(from_segment, stream)
                seams.append(_seam(from_segment, consumer, stream, output.get("table") if output else None, [
                    f"the DAG carries {stream} from {from_segment} into {consumer}, but {consumer}'s "
                    f"contract declares no input for it"]))

    duplicates = [{"table": table, "producers": sorted(seg for seg, _ in found)}
                  for table, found in sorted(producers.items())
                  if len(found) > 1 and any(seg in scope for seg, _ in found)]
    result = {"ok": all(s["status"] == "ok" for s in seams) and not duplicates,
              "seams": seams, "duplicates": duplicates}
    write_json(repo.wf(wf_id, "segments", "seams.json"), result)
    return result


def reason(result: dict) -> str | None:
    """`seam-mismatch: <producer>-><consumer> <stream>` for the first mismatch (a duplicate with no
    mismatching seam names its producers and table instead); None when every seam agrees."""
    for seam in result["seams"]:
        if seam["status"] == "mismatch":
            return f"seam-mismatch: {seam['producer'] or '?'}->{seam['consumer']} {seam['stream']}"
    for duplicate in result["duplicates"]:
        return f"seam-mismatch: {'+'.join(duplicate['producers'])}->? {duplicate['table']}"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id")
    parser.add_argument("--segments", help="comma-separated consumer segments to check (default: all)")
    add_root_arg(parser)
    args = parser.parse_args(argv)
    segments = [s.strip() for s in args.segments.split(",") if s.strip()] if args.segments is not None else None

    try:
        result = check_seams(Repo(args.root), args.wf_id, segments)
    except FileNotFoundError as exc:
        parser.error(f"{args.wf_id} has not been segmented: {exc}")   # exit 2, nothing written
    except UnknownSegment as exc:
        parser.error(str(exc))                                          # exit 2, nothing written
    except Exception:                                                   # exit 2: a crash, never a verdict
        traceback.print_exc()
        return 2

    first = reason(result)
    if first is None:
        print(f"{args.wf_id}: {len(result['seams'])} seams, every one agrees")
        return 0
    print(first, file=sys.stderr)
    for seam in result["seams"]:
        if seam["status"] == "mismatch":
            print(f"  {seam['producer'] or '?'}->{seam['consumer']} {seam['stream']} ({seam['table']}):", file=sys.stderr)
            for problem in seam["problems"]:
                print(f"    {problem}", file=sys.stderr)
    for duplicate in result["duplicates"]:
        print(f"  {duplicate['table']} is written by {', '.join(duplicate['producers'])}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
