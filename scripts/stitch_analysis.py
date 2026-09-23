"""Stitch a batched analyzer's fragments into `analysis.md` and `unsupported.json` (Task W2).

    stitch_analysis.py <wf_id> [--root .]

Above its character budget the analyzer runs once per batch (`segments/batches.json`, written by
`plan_batches.py`), and each call writes only its own fragments: `analysis/<batch>.md` and
`analysis/<batch>.unsupported.json`. This script -- never an agent -- joins them:

- `analysis.md`: `# <wf> analysis (stitched from N batches by scripts/stitch_analysis.py)`, then per
  batch, in segment order, `## <batch>: segments seg_01, seg_02` and that fragment verbatim.
- `unsupported.json`: the highest `tier` of any fragment (T1 < T2 < T3), and every list the
  fragments carry (`unsupported`, `unknown`) concatenated in batch order with each tool once (the
  first entry for a `tool_id` wins; an entry without one is kept once by value).

It refuses -- exit 1, writing nothing -- anything but every segment of `segments/order.json` exactly
once, in order, across the batches, or a fragment that is missing, empty, not JSON, or whose tier is
not one of T1, T2, T3. Exit 2 is a usage error (no `segments/order.json` or `segments/batches.json`)
or a crash, with nothing written either.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Sequence

from lib.io import read_json, write_json
from lib.paths import Repo, add_root_arg

TIERS = ("T1", "T2", "T3")


class StitchRefused(Exception):
    """A domain failure: the fragments do not cover every segment exactly once (exit 1)."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _coverage_problems(order: list[list[str]], batches: list[dict]) -> list[str]:
    every = [seg for wave in order for seg in wave]
    where: dict[str, list[str]] = {}
    for batch in batches:
        for seg in batch.get("segments") or []:
            where.setdefault(seg, []).append(str(batch.get("id")))
    problems = [f"{seg} is in {', '.join(ids)}" for seg, ids in where.items() if len(ids) > 1]
    problems += [f"{seg} is in no batch" for seg in every if seg not in where]
    problems += [f"{seg} ({', '.join(ids)}) is not a segment of segments/order.json"
                 for seg, ids in where.items() if seg not in every]
    listed = [seg for batch in batches for seg in batch.get("segments") or []]
    if not problems and listed != every:
        problems.append(f"the batches list {listed}, not segments/order.json's order {every}")
    return problems


def _merge_lists(parts: list[dict]) -> dict[str, list]:
    """Every list-valued key of the fragments, concatenated in batch order, each tool once."""
    merged: dict[str, list] = {}
    for part in parts:
        for key, value in part.items():
            if key == "tier" or not isinstance(value, list):
                continue
            into = merged.setdefault(key, [])
            for item in value:
                tool = item.get("tool_id") if isinstance(item, dict) else None
                if tool is not None:
                    if any(isinstance(have, dict) and have.get("tool_id") == tool for have in into):
                        continue
                elif item in into:
                    continue
                into.append(item)
    return merged


def stitch(repo: Repo, wf_id: str) -> dict:
    """Write `analysis.md` and `unsupported.json` from the fragments; returns the batch ids, the
    segments in order and the stitched tier. Raises FileNotFoundError without `order.json` /
    `batches.json` and StitchRefused for anything but every segment exactly once -- both before
    anything is written."""
    order = read_json(repo.wf(wf_id, "segments", "order.json"))
    batches = read_json(repo.wf(wf_id, "segments", "batches.json")).get("batches") or []
    problems = _coverage_problems(order, batches)

    texts: list[str] = []
    parts: list[dict] = []
    for batch in batches:
        batch_id = str(batch.get("id"))
        fragment = repo.wf(wf_id, "analysis", f"{batch_id}.md")
        unsupported = repo.wf(wf_id, "analysis", f"{batch_id}.unsupported.json")
        text = fragment.read_text(encoding="utf-8") if fragment.is_file() else None
        if text is None:
            problems.append(f"analysis/{batch_id}.md is missing")
        elif not text.strip():
            problems.append(f"analysis/{batch_id}.md is empty")
        texts.append(text or "")
        if not unsupported.is_file():
            problems.append(f"analysis/{batch_id}.unsupported.json is missing")
            continue
        try:
            part = read_json(unsupported)
        except json.JSONDecodeError as exc:
            problems.append(f"analysis/{batch_id}.unsupported.json is not JSON ({exc.msg}, line {exc.lineno})")
            continue
        if not isinstance(part, dict) or part.get("tier") not in TIERS:
            tier = part.get("tier") if isinstance(part, dict) else None
            problems.append(f"analysis/{batch_id}.unsupported.json has tier {json.dumps(tier)}, "
                            f"not one of {', '.join(TIERS)}")
            continue
        parts.append(part)
    if problems:
        raise StitchRefused(problems)

    lines = [f"# {wf_id} analysis (stitched from {len(batches)} batches by scripts/stitch_analysis.py)\n"]
    for batch, text in zip(batches, texts):
        lines.append(f"\n## {batch['id']}: segments {', '.join(batch.get('segments') or [])}\n\n")
        lines.append(text if text.endswith("\n") else f"{text}\n")
    tier = max((part["tier"] for part in parts), key=TIERS.index)
    analysis = repo.wf(wf_id, "analysis.md")
    with open(analysis, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(lines))
    write_json(repo.wf(wf_id, "unsupported.json"), {"tier": tier, **_merge_lists(parts)})
    return {"batches": [str(b.get("id")) for b in batches], "segments": [s for wave in order for s in wave], "tier": tier}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    try:
        result = stitch(Repo(args.root), args.wf_id)
    except FileNotFoundError as exc:
        parser.error(f"{args.wf_id} has no batch plan to stitch: {exc}")   # exit 2, nothing written
    except StitchRefused as exc:
        print(f"stitch: {exc.problems[0]}", file=sys.stderr)
        for problem in exc.problems[1:]:
            print(f"  {problem}", file=sys.stderr)
        return 1
    except Exception:                                                     # exit 2: a crash
        traceback.print_exc()
        return 2

    print(f"{args.wf_id}: stitched {len(result['batches'])} batches, {len(result['segments'])} segments, "
          f"tier {result['tier']}")
    return 0


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
