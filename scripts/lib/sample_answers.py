"""Shared lookup from a sample workflow's recorded answers (`samples/<wf>/sample.json["answers"]`)
onto one of its touchpoints.

Two callers share this, and neither forks it: `tests/helpers.py::prepare_workflow` drives
`intake_prompt.apply_answers` directly to build a fully-answered workflow before
`canned/segments/**` even exists, and `scripts/dev/answer_samples.py` feeds the same answers
through the ordinary `manifest.answers` / `intake_prompt.py --no-interactive` resume path for the
orchestrator's offline end-to-end runs (plan Task 15 Step 5). A sample workflow's set of recorded
answers means the same thing wherever it is consumed.
"""
from __future__ import annotations


def answer_for(touchpoint: dict, raw_answers: dict) -> str | None:
    """The Snowflake FQN `sample.json["answers"]` gives this touchpoint, matched by its normalized
    key first, then by tool id (wf_0002's DB output and both of wf_0003's touchpoints answer by
    tool id rather than by key). `None` when the sample records nothing for it -- never guessed.
    """
    snowflake = raw_answers.get(touchpoint.get("key"))
    if snowflake is not None:
        return snowflake
    tool_id = touchpoint.get("tool_id")
    if tool_id is not None:
        return raw_answers.get(str(tool_id))
    return None
