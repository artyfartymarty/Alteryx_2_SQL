"""Feed each sample workflow's recorded answers into `manifest.json["answers"]`, so a
`workflows/<wf_id>/` parked at `WAITING_FOR_ANSWERS` by a non-interactive `intake_prompt.py` run
can be resumed by a second one with nobody at a keyboard (plan Task 15 Step 5 -- the orchestrator's
offline end-to-end run).

    python scripts/dev/answer_samples.py [--only wf_id] [--samples samples] [--root .]

For every `workflows/<wf_id>/` that has already been through `scripts/intake_touchpoints.py` (an
`intake/touchpoints.json` on disk) and whose `samples/<wf_id>/sample.json` records `"answers"`,
this looks up each blocking, unresolved touchpoint's answer with `lib.sample_answers.answer_for`
-- the same key-then-tool-id lookup `tests/helpers.py::prepare_workflow` uses to build its own
pytest fixtures, not forked here -- and writes it into `manifest.json["answers"]`.
`intake_prompt.py --no-interactive` merges that dict with whatever is already checked off in
`intake/open_questions.md` on its very next run (`intake_prompt._merged_answers`). Write mode and
merge keys are never written here: `intake_prompt.py` always derives those from the touchpoint's
own recorded Alteryx write mode (`intake_prompt._default_write_mode`) -- the same "proposal
default" a human accepting Enter would get, whether or not this script ever ran.

This never invents an answer: a blocking touchpoint `sample.json["answers"]` has nothing for stays
out of `manifest.json["answers"]` entirely and is reported by its question id, same as if a human
had left it blank. A workflow that has not been through `intake_touchpoints.py` yet is a usage
error when named explicitly with `--only`; found while scanning every `workflows/*` directory (no
`--only` given), it simply is not this script's business yet and is skipped without affecting the
exit code -- same for a workflow with no matching `samples/<wf_id>/sample.json`, or one whose
sample records no `"answers"` at all.

Nothing here has run against a real Snowflake account or Alteryx: `sample.json["answers"]` is a
fixture's own hand-recorded stand-in for what a human would have typed at the real prompt.

A `sample.json["answers"]` key that matches no touchpoint at all (a stale or mistyped key --
fix round 1) is never silently dropped: it is named on stderr as a WARNING, once per processed
workflow, and never affects the exit code -- it means the fixture and the workflow have drifted,
which is worth a human's attention but is not itself a reason to fail the run.

Exit codes follow the plan's Global Constraints: 0 when every blocking touchpoint of every
processed workflow now has an answer on file (already resolved, already answered earlier, or
newly written here); 1 when at least one is left with nothing to answer it (named on stderr); 2
for a usage error (an unknown `--only` workflow, a missing `samples/` directory, a `sample.json`
that fails to parse) or anything unexpected.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Sequence

if __package__ in (None, ""):  # `python scripts/dev/answer_samples.py` puts scripts/ on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import io as lib_io
from lib.paths import Repo, add_root_arg
from lib.sample_answers import answer_for


def _samples_dir(repo: Repo, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else repo.root / path


def _raw_answers(samples_dir: Path, wf_id: str) -> dict | None:
    """`sample.json["answers"]`, or `None` when there is no sample for `wf_id` or it records none
    -- the caller's cue to skip this workflow rather than treat it as a target."""
    sample_path = samples_dir / wf_id / "sample.json"
    if not sample_path.is_file():
        return None
    sample = lib_io.read_json(sample_path)
    return sample.get("answers") or None


def _orphaned_answer_keys(touchpoints: list[dict], raw_answers: dict) -> list[str]:
    """`sample.json["answers"]` keys that match no touchpoint at all -- neither by `key` nor by
    `tool_id` (the same two lookups `answer_for` tries), on ANY touchpoint, blocking or not,
    resolved or not. Almost certainly a stale or mistyped key: a key that does match a touchpoint
    but that touchpoint happens to be non-blocking or already resolved is still a legitimate
    match, not an orphan, so it is never reported. Returned sorted for a deterministic message.
    """
    known: set[str] = set()
    for t in touchpoints:
        key = t.get("key")
        if key is not None:
            known.add(key)
        tool_id = t.get("tool_id")
        if tool_id is not None:
            known.add(str(tool_id))
    return sorted(k for k in raw_answers if k not in known)


def answer_workflow(repo: Repo, samples_dir: Path, wf_id: str) -> dict:
    """Writes `sample.json`'s answers into `manifest.json["answers"]` for `wf_id`. Returns
    `{"answered": [qid, ...], "unanswered": [qid, ...], "orphaned": [key, ...]}` -- every
    blocking, unresolved touchpoint `answer_for` could or could not find an answer for, plus any
    `sample.json["answers"]` key that matches no touchpoint at all (see `_orphaned_answer_keys`).
    Raises `FileNotFoundError` when `intake/touchpoints.json` doesn't exist yet (a usage error for
    the CLI, not a domain outcome).
    """
    tp_path = repo.wf(wf_id, "intake", "touchpoints.json")
    if not tp_path.is_file():
        raise FileNotFoundError(
            f"no intake/touchpoints.json for {wf_id}; "
            f"run `python scripts/intake_touchpoints.py {wf_id}` first")
    touchpoints = lib_io.read_json(tp_path)
    raw_answers = _raw_answers(samples_dir, wf_id) or {}

    manifest = lib_io.load_manifest(repo, wf_id)
    answers = dict(manifest.get("answers") or {})
    answered: list[str] = []
    unanswered: list[str] = []
    for t in touchpoints:
        if not t["blocking"] or t["resolved"] is not None:
            continue
        snowflake = answer_for(t, raw_answers)
        if snowflake is None:
            unanswered.append(t["id"])
            continue
        answers[t["id"]] = snowflake
        answered.append(t["id"])

    manifest["answers"] = answers
    lib_io.save_manifest(repo, manifest)
    orphaned = _orphaned_answer_keys(touchpoints, raw_answers)
    return {"answered": answered, "unanswered": unanswered, "orphaned": orphaned}


def _workflow_ids(repo: Repo) -> list[str]:
    workflows_dir = repo.root / "workflows"
    if not workflows_dir.is_dir():
        return []
    return sorted(p.name for p in workflows_dir.iterdir() if p.is_dir() and not p.name.startswith("."))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default=None, help="one workflow id, e.g. wf_0001")
    parser.add_argument("--samples", default="samples", help="samples directory (default: samples)")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    try:
        repo = Repo(args.root)
        samples_dir = _samples_dir(repo, args.samples)
        if not samples_dir.is_dir():
            raise ValueError(f"samples directory not found: {samples_dir}")

        if args.only is not None:
            if not repo.wf(args.only).is_dir():
                raise ValueError(f"unknown workflow {args.only!r}: not found under {repo.root / 'workflows'}")
            wf_ids = [args.only]
        else:
            wf_ids = _workflow_ids(repo)

        any_unanswered = False
        for wf_id in wf_ids:
            raw_answers = _raw_answers(samples_dir, wf_id)
            if raw_answers is None:
                if args.only is not None:
                    raise ValueError(f"{wf_id}: no answers recorded in {samples_dir / wf_id / 'sample.json'}")
                print(f"{wf_id}: no sample answers recorded; skipped")
                continue
            try:
                result = answer_workflow(repo, samples_dir, wf_id)
            except FileNotFoundError as exc:
                if args.only is not None:
                    raise
                print(f"{wf_id}: skipped ({exc})")
                continue
            print(f"{wf_id}: {len(result['answered'])} answered, {len(result['unanswered'])} unanswered")
            for qid in result["unanswered"]:
                print(f"  {wf_id} {qid}: no recorded answer in sample.json", file=sys.stderr)
            if result["unanswered"]:
                any_unanswered = True
            for key in result["orphaned"]:
                # A stale or mistyped sample.json["answers"] key: it matches no touchpoint at
                # all, so nothing ever reads it. Reported, never fatal -- the exit code is
                # unaffected, same as any_unanswered is untouched here.
                print(f"  {wf_id}: WARNING sample.json[\"answers\"][{key!r}] matches no touchpoint "
                      f"(stale or mistyped key)", file=sys.stderr)
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))  # exit 2: bad --only, missing samples dir, unanswerable --only target
    except Exception:
        traceback.print_exc()
        return 2

    return 1 if any_unanswered else 0


if __name__ == "__main__":
    sys.exit(main())
