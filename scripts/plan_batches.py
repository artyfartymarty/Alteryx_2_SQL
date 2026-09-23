"""Group a workflow's waves into analyzer batches that each fit a character budget (Task W2).

    plan_batches.py <wf_id> [--budget-chars N] [--root .]

A large workflow can overflow a model's context window when ONE analyzer call reads all of it. This
plans the calls instead: consecutive waves of `segments/order.json` go into one batch for as long
as the batch's estimated context -- `prompt_context.render_global` (the workflow map and the target
proposal, carried by every batch) plus `prompt_context.segment_detail_chars` of each of its
segments -- stays within `--budget-chars`. A batch is closed at a wave boundary, never inside a wave,
so every producer a batch reads from was written by an earlier batch (or is in the batch itself).
A single wave whose estimate alone is over the budget still gets a batch of its own, with a
warning: no cut between waves can make it smaller.

Writes `segments/batches.json`:
`{"budget_chars", "estimate_note", "batches": [{"id": "batch_01", "waves": [0, 1], "segments": [...],
"estimate_chars"}], "warnings": [...]}` -- a pure function of the files it reads, so the same inputs
give the same bytes. A workflow whose whole estimate fits is ONE batch, which the orchestrator runs
exactly as before (one analyzer call); every committed sample is one batch under the default.

Characters are an estimate, not tokens (tokens are roughly characters / 4); the default budget is
conservative until it is calibrated against a real model's usage. Nothing here has run against a
hosted model.

Exit codes: 0 planned (warnings included); 2 usage -- the workflow is not parsed or segmented, or
the budget is not a positive number -- or a crash, with nothing written.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from typing import Sequence

import prompt_context
from lib.io import read_json, write_json
from lib.paths import Repo, add_root_arg

DEFAULT_ANALYZER_BUDGET_CHARS = 60000
ESTIMATE_NOTE = "characters of the rendered context; tokens are roughly characters / 4"


def plan_batches(repo: Repo, wf_id: str, budget_chars: int) -> dict:
    """Plan and write `segments/batches.json`. Raises FileNotFoundError when the workflow has not
    been parsed or segmented (nothing is written then)."""
    order = read_json(repo.wf(wf_id, "segments", "order.json"))
    base = len(prompt_context.render_global(repo, wf_id))       # map + targets, carried by every batch
    size = prompt_context.segment_detail_sizes(repo, wf_id)
    groups: list[list[int]] = []
    current: list[int] = []
    current_chars = base
    warnings: list[str] = []
    for index, wave in enumerate(order):
        wave_chars = sum(size[seg] for seg in wave)
        if current and current_chars + wave_chars > budget_chars:
            groups.append(current)
            current, current_chars = [], base
        if base + wave_chars > budget_chars:
            warnings.append(f"wave {index + 1} ({', '.join(wave)}) alone is estimated at {base + wave_chars} "
                            f"characters, over the budget of {budget_chars}")
        current.append(index)
        current_chars += wave_chars
    if current:
        groups.append(current)
    result = {
        "budget_chars": budget_chars,
        "estimate_note": ESTIMATE_NOTE,
        "batches": [{"id": f"batch_{n:02d}", "waves": waves,
                     "segments": [seg for w in waves for seg in order[w]],
                     "estimate_chars": base + sum(size[seg] for w in waves for seg in order[w])}
                    for n, waves in enumerate(groups, 1)],
        "warnings": warnings,
    }
    write_json(repo.wf(wf_id, "segments", "batches.json"), result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id")
    parser.add_argument("--budget-chars", type=int, default=DEFAULT_ANALYZER_BUDGET_CHARS)
    add_root_arg(parser)
    args = parser.parse_args(argv)
    if args.budget_chars <= 0:
        parser.error(f"--budget-chars must be a positive number of characters, not {args.budget_chars}")

    try:
        result = plan_batches(Repo(args.root), args.wf_id, args.budget_chars)
    except FileNotFoundError as exc:
        parser.error(f"{args.wf_id} has not been parsed and segmented: {exc}")   # exit 2, nothing written
    except Exception:                                                          # exit 2: a crash
        traceback.print_exc()
        return 2

    count = len(result["batches"])
    print(f"{args.wf_id}: {count} batch{'' if count == 1 else 'es'} under a budget of "
          f"{args.budget_chars} characters")
    for warning in result["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
