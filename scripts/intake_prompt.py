"""Ask the workflow owner which Snowflake table each touchpoint `intake_touchpoints.py` found
corresponds to (program spec §5) -- this is the project owner's one explicit requirement beyond
the written spec: the pipeline asks a human to name the Snowflake table for every `.yxdb` file (and
every other input and output), and it never invents one. An unresolved touchpoint stays unresolved.

    python scripts/intake_prompt.py <wf_id> [--no-interactive] [--interactive] [--user NAME] [--root .]

Interactive prompting is the default only when stdin is a TTY (`sys.stdin.isatty()`); `--no-interactive`
forces it off (resume from `intake/open_questions.md` and `manifest.answers` instead) and
`--interactive` forces it on (for an orchestrator that inherits a real terminal). Exit codes: `0`
for `READY`; `1` for `WAITING_FOR_ANSWERS`, `BLOCKED` or `NEEDS_HUMAN` (all three are legitimate
outcomes, not errors); `2` for a usage error -- an unknown workflow, a workflow that hasn't been
through `intake_touchpoints.py` yet, or anything unexpected.

Everything a human types here is DATA, never an instruction and never a path: a Snowflake name is
only ever accepted when it matches `FQN_RE`, and a yxdb path typed in answer to "Local copy of …"
or "Path of the yxdb" is used only to call `lib.yxdb.read_header` on it (never written to); a path
that doesn't exist or isn't a readable yxdb is reported and skipped, never fatal.
"""
from __future__ import annotations

import argparse
import getpass
import re
import sys
import traceback
from pathlib import Path
from typing import Callable

import intake_touchpoints as tpx
from lib import io as lib_io
from lib import yxdb
from lib.paths import Repo, add_root_arg

FQN_RE = r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){2}$"

_MODE_DEFAULTS = {"update_insert": "merge", "truncate_append": "overwrite",
                  "overwrite": "overwrite", "append": "append"}
_VALID_MODES = {"overwrite", "append", "merge"}

# Coordinator ruling (post-review): an output must never quietly default onto one of this
# workflow's own sources or the raw landing schema (program spec §8.5's run-ordering risk).
# `intake_touchpoints.propose_candidates` already drops such tables from an output's column-backed
# ranking; this token is the non-interactive escape hatch for a human who explicitly wants one
# anyway (the interactive path asks a plain yes/no instead -- see `_ask_touchpoint`).
_SELF_REF_TOKEN = "confirm-self-reference"

# Fix round 1 (coordinator ruling 3): open_questions.md parsing is block-based and label-anchored.
# Each of these is the fixed-label line `render_open_questions` always emits LAST in an item's
# block, and `parse_answered_questions` reads an answer from ONLY that line -- never from any
# other `": "` in the item's prose. Shared here so renderer and parser can never drift apart.
_LABEL_IO = "Confirm or supply another:"
_LABEL_CONSTANT = "Override:"
_ANSWER_LABELS = (_LABEL_IO, _LABEL_CONSTANT)


class _Invalid(Exception):
    """Raised by `_interpret_answer_text` for text that is neither empty, `?`, `!`, a listed
    candidate number nor a valid `DB.SCHEMA.TABLE` -- the interactive loop re-asks on this; the
    non-interactive resume path just leaves that touchpoint unresolved."""


class _SelfReferenceRisk(Exception):
    """Raised by `_interpret_answer_text` when an *output* answer -- chosen by number or typed as
    an FQN -- names a table that is one of this workflow's own input sources, or sits in the raw
    landing schema. Both `_ask_touchpoint` (interactive: a yes/no confirmation) and `run`'s
    non-interactive resume path (the `confirm-self-reference` token) catch this and decide whether
    to proceed; `propose_candidates` filtering already keeps this from ever being the *default*
    Enter choice, so this only fires when the human deliberately picked or typed the table anyway.
    """

    def __init__(self, snowflake: str, reason: str, tool_id: str | None):
        super().__init__(snowflake, reason)
        self.snowflake = snowflake
        self.reason = reason  # "source" | "raw_schema"
        self.tool_id = tool_id


# --- display helpers (shared by the interactive prompt and open_questions.md) --------------------

def _basename(path: str) -> str:
    return re.split(r"[\\/]+", path)[-1] if path else ""


def _display_source(t: dict) -> str:
    if t["format"] == "db":
        return f"{t.get('table') or '(unknown table)'} via {t['key']}"
    return t.get("source") or t.get("key") or "(unknown source)"


def _describe_tool(t: dict) -> str:
    label = "Input Data" if t["kind"] == "input" else "Output Data"
    verb = "reads" if t["kind"] == "input" else "writes"
    size = f"{len(t['fields'])} fields"
    if t["kind"] == "input" and t.get("record_count") is not None:
        size += f", {t['record_count']} rows"
    mode = f", mode {t['write_mode']}" if t["kind"] == "output" and t.get("write_mode") else ""
    return f"Tool {t['tool_id']} {label} {verb} {_display_source(t)} ({size}){mode}"


def _candidate_line(c: dict) -> str:
    name = c["snowflake"]
    if c["basis"] == "naming":
        return f"{name:<28} naming convention -- not verified to exist"
    if c["missing"]:
        return f"{name:<28} {c['matched']}/{c['of']} columns (missing {', '.join(c['missing'])})"
    if c.get("row_count") is not None:
        return f"{name:<28} {c['matched']}/{c['of']} columns - {c['row_count']:,} rows"
    return f"{name:<28} {c['matched']}/{c['of']} columns"


def _proposal_text(t: dict) -> str:
    candidates = t.get("candidates") or []
    if not candidates:
        return "Proposed: (no candidate found; supply the target)."
    c = candidates[0]
    if c["basis"] == "naming":
        return f"Proposed: {c['snowflake']} (naming convention; not verified to exist)."
    evidence = f"{c['matched']}/{c['of']} columns match"
    if c["missing"]:
        evidence += f"; missing {', '.join(c['missing'])}"
    return f"Proposed: {c['snowflake']} ({evidence})."


def _touchpoint_header(t: dict) -> str:
    return f"{t['id']} - {_describe_tool(t)}"


# --- answer interpretation (shared by interactive Enter-handling and non-interactive resume) -----

def _enter_accepts(t: dict, candidate: dict) -> bool:
    """Whether pressing Enter may auto-accept `candidate` (always the first one shown). A
    naming-convention guess is never auto-accepted for either kind. For an `output`, coordinator
    ruling: only a *full* column match (`matched == of`) may be a default -- a partial match is
    exactly as likely to be some other table entirely, and the whole point of excluding sources
    from an output's candidates is defeated if a partial match can still be rubber-stamped by
    Enter. An `input` keeps the brief's original rule (any columns-basis top candidate)."""
    if candidate["basis"] != "columns":
        return False
    if t["kind"] == "output":
        return candidate["matched"] == candidate["of"]
    return True


def _self_reference_reason(snowflake: str, workflow_sources: dict[str, str],
                           raw_schema: str | None) -> tuple[str | None, str | None]:
    """`(reason, tool_id)` -- `reason` is `"source"` (with the colliding input's `tool_id`),
    `"raw_schema"`, or `(None, None)` when `snowflake` is fine for an output to target."""
    upper = snowflake.upper()
    if upper in workflow_sources:
        return "source", workflow_sources[upper]
    parts = snowflake.split(".")
    if len(parts) == 3 and parts[1].upper() == (raw_schema or "RAW").upper():
        return "raw_schema", None
    return None, None


def _map_or_raise(t: dict, snowflake: str, workflow_sources: dict[str, str] | None,
                  raw_schema: str | None) -> dict:
    if t["kind"] == "output":
        reason, tool_id = _self_reference_reason(snowflake, workflow_sources or {}, raw_schema)
        if reason:
            raise _SelfReferenceRisk(snowflake, reason, tool_id)
    return {"action": "map", "snowflake": snowflake}


def _interpret_answer_text(t: dict, text: str | None, *,
                           workflow_sources: dict[str, str] | None = None,
                           raw_schema: str | None = None) -> dict:
    """`text` -> `{"action": "map"|"defer"|"impossible", "snowflake"?}` for an input/output
    touchpoint. Raises `_Invalid` for text that names neither a listed candidate nor a valid FQN.
    Raises `_SelfReferenceRisk` (only ever for an `output`) when the chosen table -- by number or
    typed FQN -- is in `workflow_sources` (contract: `{fqn.upper(): tool_id}`, see
    `intake_touchpoints.known_input_sources`) or sits in `raw_schema`; the caller decides whether
    to proceed. Enter (empty text) accepts candidate 1 only when `_enter_accepts` allows it;
    otherwise it defers -- it never silently accepts a risky or unverified guess.
    """
    text = (text or "").strip()
    candidates = t.get("candidates") or []
    if text == "":
        if candidates and _enter_accepts(t, candidates[0]):
            return {"action": "map", "snowflake": candidates[0]["snowflake"]}
        return {"action": "defer"}
    if text == "?":
        return {"action": "defer"}
    if text == "!":
        return {"action": "impossible"}
    if text.isdigit():
        index = int(text) - 1
        if 0 <= index < len(candidates):
            return _map_or_raise(t, candidates[index]["snowflake"], workflow_sources, raw_schema)
        raise _Invalid(text)
    if re.match(FQN_RE, text):
        return _map_or_raise(t, text, workflow_sources, raw_schema)
    raise _Invalid(text)


def _default_write_mode(t: dict) -> str:
    return _MODE_DEFAULTS.get(t.get("write_mode"), "overwrite")


def _strip_self_reference_token(text: str) -> tuple[str, bool]:
    """`"SALES.RAW.ORDERS confirm-self-reference"` -> `("SALES.RAW.ORDERS", True)`; text with no
    trailing token is returned unchanged with `False`. `FQN_RE` allows no hyphens, so the token can
    never be mistaken for part of a real (unquoted) Snowflake identifier."""
    text = (text or "").strip()
    if text.endswith(_SELF_REF_TOKEN):
        return text[: -len(_SELF_REF_TOKEN)].strip(), True
    return text, False


def _self_reference_warning(t: dict, risk: _SelfReferenceRisk) -> str:
    mode = _default_write_mode(t)
    if risk.reason == "source":
        who = f"tool {risk.tool_id}" if risk.tool_id else "one of this workflow's own inputs"
        return (f"WARNING: {risk.snowflake} is also what {who} READS. "
               f"Mode {mode} would replace this workflow's own input.")
    return (f"WARNING: {risk.snowflake} sits in the raw landing schema this workflow's own "
           f"inputs are loaded from. Mode {mode} risks colliding with an upstream load.")


# --- interactive prompting ---------------------------------------------------------------------

def _ask_extra_yxdb(touchpoints: list[dict], ask: Callable[[str], str],
                    out: Callable[[str], None], answers: list[dict]) -> None:
    yxdb_tps = [t for t in touchpoints if t["format"] == "yxdb" and t["kind"] in ("input", "output")]
    if yxdb_tps:
        desc = ", ".join(f"{_basename(t['source'] or '')} (tool {t['tool_id']}, {t['kind']})"
                         for t in yxdb_tps)
    else:
        desc = "none"
    out(f"{len(yxdb_tps)} yxdb files found: {desc}")
    other = ask("Does this workflow depend on other .yxdb files that are not visible in the DAG "
               "(for example written by another workflow)? [y/N]: ")
    if not (other or "").strip().lower().startswith("y"):
        return
    count = 0
    while True:
        path_text = (ask("Path of the yxdb: ") or "").strip()
        if not path_text:
            return
        table_text = (ask("Snowflake table (DB.SCHEMA.TABLE): ") or "").strip()
        if not re.match(FQN_RE, table_text):
            out(f"'{table_text}' is not a DB.SCHEMA.TABLE name; {path_text} was not recorded.")
            continue
        count += 1
        answers.append({
            "id": f"U{count}", "action": "map",
            "key": tpx.normalize_key({"format": "yxdb", "source": path_text}),
            "snowflake": table_text, "tool_ids": [], "note": "user-declared",
        })


def _ask_local_copies(touchpoints: list[dict], ask: Callable[[str], str],
                      out: Callable[[str], None],
                      rescore: Callable[..., None] | None) -> None:
    """For an input `.yxdb` touchpoint `intake_touchpoints.py` could not find on disk (never for
    outputs, which don't exist yet), ask once for a local copy to read its field list from, and
    re-score candidates against it if one is given. A path that isn't a readable yxdb is reported
    and skipped -- it never blocks the rest of intake. `rescore(t)` (no exclusion set needed: an
    input's own candidates are never filtered) must accept being called with just `t`.
    """
    for t in touchpoints:
        if t["kind"] != "input" or t["format"] != "yxdb" or t["field_source"] == "yxdb_header":
            continue
        if t["resolved"] is not None:
            continue
        basename = _basename(t.get("source") or "") or t.get("key") or "the file"
        local = (ask(f"Local copy of {basename} to read its field list (Enter to skip): ") or "").strip()
        if not local:
            continue
        try:
            header = yxdb.read_header(local)
        except (yxdb.YxdbError, OSError) as exc:
            out(f"Could not read '{local}' as a yxdb file ({exc}); continuing without it.")
            continue
        t["fields"] = [f["name"] for f in header.fields]
        t["field_source"] = "yxdb_header"
        t["record_count"] = header.num_records
        if rescore is not None:
            rescore(t)


def _ask_touchpoint(t: dict, ask: Callable[[str], str], out: Callable[[str], None], *,
                    workflow_sources: dict[str, str] | None = None,
                    raw_schema: str | None = None) -> dict:
    out(_touchpoint_header(t))
    candidates = t.get("candidates") or []
    for i, c in enumerate(candidates, start=1):
        out(f"  {i}) {_candidate_line(c)}")
    accepts = bool(candidates) and _enter_accepts(t, candidates[0])
    default = "1" if accepts else "defer"
    enter_word = "accept" if accepts else "defer"
    prompt = (f"Snowflake table [{default}] (Enter={enter_word} | number | DB.SCHEMA.TABLE | "
             "?=defer | !=cannot exist in Snowflake): ")
    for _ in range(3):
        raw = ask(prompt)
        try:
            result = _interpret_answer_text(t, raw, workflow_sources=workflow_sources,
                                            raw_schema=raw_schema)
        except _Invalid:
            out(f"'{(raw or '').strip()}' isn't a listed number or a DB.SCHEMA.TABLE name; "
               "try again.")
            continue
        except _SelfReferenceRisk as risk:
            out(_self_reference_warning(t, risk))
            confirm = (ask("Type 'yes' to confirm, or anything else to pick a different "
                          "table: ") or "").strip()
            if confirm == "yes":
                return {"id": t["id"], "action": "map", "snowflake": risk.snowflake,
                       "self_reference": True}
            continue
        result["id"] = t["id"]
        return result
    return {"id": t["id"], "action": "defer"}


def _ask_write_mode(t: dict, ask: Callable[[str], str]) -> dict:
    default_mode = _default_write_mode(t)
    raw = (ask(f"Write mode [{default_mode}] (overwrite/append/merge): ") or "").strip().lower()
    mode = raw if raw in _VALID_MODES else default_mode
    keys: list[str] = []
    if mode == "merge":
        default_keys = t.get("keys") or []
        raw_keys = (ask(f"Merge keys [{', '.join(default_keys)}]: ") or "").strip()
        keys = [k.strip() for k in raw_keys.split(",") if k.strip()] if raw_keys else list(default_keys)
    return {"write_mode": mode, "keys": keys}


def prompt_touchpoints(touchpoints: list[dict], *, ask: Callable[[str], str],
                       out: Callable[[str], None],
                       rescore: Callable[..., None] | None = None,
                       raw_schema: str | None = None,
                       existing_sources: dict | None = None) -> list[dict]:
    """Runs the full interactive prompt protocol (program spec §5 / the brief's prompt protocol)
    and returns the answers gathered: `{"id", "action": "map"|"defer"|"impossible", "snowflake"?,
    "write_mode"?, "keys"?, "self_reference"?}`, plus one synthetic `{"id": "U<n>", ...}` entry per
    user-declared yxdb not visible in the DAG.

    A closed stdin (`EOFError`) or Ctrl-C (`KeyboardInterrupt`) raised by `ask` at any point stops
    asking further questions immediately -- it never propagates -- so whatever was answered before
    the interruption is still returned (and, by `run`, still persisted); every touchpoint not yet
    reached simply stays unresolved, exactly as if it had been deferred.

    `rescore`, `raw_schema` and `existing_sources` are optional hooks (not part of the documented
    3-argument call) that `run` supplies: `rescore(t)` re-runs `propose_candidates` after a "local
    copy" answer updates an input's fields, and `rescore(t, exclude)` does the same for an output
    right before it is asked about, using `intake_touchpoints.known_input_sources` (recomputed
    fresh each time, so a source answered earlier in *this* session is excluded too, not just what
    was already known when `intake_touchpoints.py` first ran) -- this function alone has no
    catalog or program config to score against, which is also why `raw_schema` (for the
    self-reference check) is passed in rather than read from `mappings/global.yaml` here.
    `existing_sources` is `intake/mappings.yaml["sources"]` as already on disk (fix round 1,
    coordinator ruling 2: self-reference protection covers a source declared in an *earlier*
    session too, not just this session's own DAG inputs and opening-loop answers).
    """
    answers: list[dict] = []
    try:
        _ask_extra_yxdb(touchpoints, ask, out, answers)
        _ask_local_copies(touchpoints, ask, out, rescore)
        for t in touchpoints:
            if not t["blocking"] or t["resolved"] is not None:
                continue
            workflow_sources: dict[str, str] = {}
            if t["kind"] == "output":
                workflow_sources = tpx.known_input_sources(touchpoints, answers,
                                                            existing_sources=existing_sources)
                if rescore is not None:
                    rescore(t, workflow_sources.keys())
            answer = _ask_touchpoint(t, ask, out, workflow_sources=workflow_sources,
                                     raw_schema=raw_schema)
            answers.append(answer)
            if answer["action"] == "map" and t["kind"] == "output":
                answer.update(_ask_write_mode(t, ask))
    except (EOFError, KeyboardInterrupt):
        pass
    return answers


# --- logical names ----------------------------------------------------------------------------

def logical_name(fqn: str, taken: set[str]) -> str:
    """The table part of `fqn`, upper-cased; on collision within `taken`, `<SCHEMA>_<TABLE>`; on a
    further collision, `_2`, `_3`, … `taken` is mutated to record the name handed out.
    """
    parts = fqn.split(".")
    table = parts[-1].upper()
    schema = parts[-2].upper() if len(parts) >= 2 else ""
    name = table if table not in taken else (f"{schema}_{table}" if schema else table)
    suffix = 2
    base = name
    while name in taken:
        name = f"{base}_{suffix}"
        suffix += 1
    taken.add(name)
    return name


# --- persistence: intake/mappings.yaml + promotion to mappings/global.yaml -----------------------

# `logical` names a table in a generated procedure -- `LET <LOGICAL>_SRC VARCHAR := … || '.<LOGICAL>'`,
# then `IDENTIFIER(:<LOGICAL>_SRC)` (plan contract C4, program spec §5.6) -- which is why its shape is
# stricter than an ordinary Snowflake identifier's.
_LOGICAL_RE = re.compile(r"^[A-Z_][A-Z0-9_$]*$")


def _missing_blocking_entries(touchpoints: list[dict], mappings: dict) -> list[str]:
    """Ids of blocking, unresolved touchpoints with NO entry at all yet in
    `mappings["sources"/"outputs"]` -- the normal, expected mid-session state (contributes to
    `WAITING_FOR_ANSWERS`, not `NEEDS_HUMAN`: nothing has gone *wrong*, the workflow just isn't
    fully answered yet). Deliberately separate from `_mapping_problems`, which is reserved for an
    entry that DOES exist but fails validation -- a genuine violation, not a pending question.
    """
    missing = []
    for t in touchpoints:
        if not t["blocking"] or t["resolved"] is not None:
            continue
        section = "sources" if t["kind"] == "input" else "outputs"
        if t["key"] not in (mappings.get(section) or {}):
            missing.append(t["id"])
    return missing


def _mapping_problems(touchpoints: list[dict], mappings: dict) -> list[str]:
    """Every reason an entry that DOES exist in `intake/mappings.yaml` (as it exists on disk,
    whoever wrote it) is incomplete or invalid -- human-readable, each naming the entry and the
    field, e.g. `sources["sales/orders.yxdb"]: logical is missing`. A genuine violation (status
    `NEEDS_HUMAN`, never `READY`), unlike a touchpoint with no entry at all yet
    (`_missing_blocking_entries`'s normal `WAITING_FOR_ANSWERS` case). This REPORTS only; it never
    repairs, invents or mutates anything (coordinator ruling, fix round 2).

    Fix round 1's guard checked only that an entry EXISTED for every blocking touchpoint. The
    re-review found that presence alone isn't enough: an entry that exists but is missing
    `logical` (hand-edited, or a future regression in `apply_answers`) still let intake report
    `READY`, and `load_golden.py` then failed with exactly the "no logical: name" error the guard
    was written to make impossible. This checks, for every entry actually in `mappings["sources"]`
    and `mappings["outputs"]` (touchpoint-tied or `note: "user-declared"` alike -- a user-declared
    source is exempt from the tool-id rule below, but not from any other):

    - `snowflake` is a string matching `FQN_RE`;
    - `logical` is a string matching `^[A-Z_][A-Z0-9_$]*$` (procedures build a table name from it
      and reference it through `IDENTIFIER(:<LOGICAL>_SRC)`), and no two entries in the whole file
      (source or output) share one -- they would collide in the sandbox view schema;
    - `tool_ids` is a non-empty list containing the tied touchpoint's own `tool_id` (skipped
      entirely for a `note: "user-declared"` entry, which has no touchpoint and legitimately
      carries `tool_ids: []`);
    - for an output: `mode` is one of `overwrite`/`append`/`merge`, `keys` is a list, and non-empty
      when `mode == "merge"`.
    """
    problems: list[str] = []
    by_key = {(("sources" if t["kind"] == "input" else "outputs"), t["key"]): t
             for t in touchpoints if t["kind"] in ("input", "output")}
    logical_locations: dict[str, list[str]] = {}

    for section in ("sources", "outputs"):
        for key, raw_entry in (mappings.get(section) or {}).items():
            entry = raw_entry or {}
            label = f'{section}["{key}"]'

            snowflake = entry.get("snowflake")
            if not snowflake:
                problems.append(f"{label}: snowflake is missing")
            elif not (isinstance(snowflake, str) and re.match(FQN_RE, snowflake)):
                problems.append(f"{label}: snowflake {snowflake!r} is not a DB.SCHEMA.TABLE name")

            logical = entry.get("logical")
            if not logical:
                problems.append(f"{label}: logical is missing")
            elif not (isinstance(logical, str) and _LOGICAL_RE.match(logical)):
                problems.append(f"{label}: logical {logical!r} is not a valid identifier "
                                "(must match ^[A-Z_][A-Z0-9_$]*$)")
            else:
                logical_locations.setdefault(logical, []).append(label)

            if entry.get("note") != "user-declared":
                tool_ids = entry.get("tool_ids") or []
                touchpoint = by_key.get((section, key))
                if not tool_ids:
                    problems.append(f"{label}: tool_ids is empty")
                elif touchpoint is not None and \
                        str(touchpoint["tool_id"]) not in [str(x) for x in tool_ids]:
                    problems.append(f"{label}: tool_ids does not contain tool "
                                    f"{touchpoint['tool_id']}")

            if section == "outputs":
                mode = entry.get("mode")
                if mode not in _VALID_MODES:
                    problems.append(f"{label}: mode {mode!r} is not one of "
                                    f"{sorted(_VALID_MODES)}")
                keys = entry.get("keys")
                if not isinstance(keys, list):
                    problems.append(f"{label}: keys is not a list")
                elif mode == "merge" and not keys:
                    problems.append(f"{label}: keys must be non-empty when mode is merge")

    for logical, locations in logical_locations.items():
        if len(locations) > 1:
            problems.append(f"logical {logical!r} is used by more than one entry: "
                            f"{', '.join(locations)}")

    return problems


def apply_answers(repo: Repo, wf_id: str, touchpoints: list[dict], answers: list[dict],
                  user: str, *, promote_to_global: bool = True) -> dict:
    """Writes `intake/mappings.yaml` (program schema shape + `logical`, contract C6) and, when
    `promote_to_global` is true, promotes every newly mapped source/output to
    `mappings/global.yaml` (`load_path: already_there`) unless the key is already there with a
    *different* table, in which case nothing is overwritten and the key is recorded in the
    returned `conflicts` list (status `NEEDS_HUMAN`). Returns `{"status", "mappings", "conflicts",
    "missing", "problems", "confirmed_constants", "answers_by_qid"}` -- `missing`/`problems` are
    `_missing_blocking_entries`/`_mapping_problems`'s own findings (fix round 2), the same two
    checks `run`'s guard re-runs independently against the file it actually wrote: a touchpoint
    with no entry at all is normal mid-session (`missing`, `WAITING_FOR_ANSWERS`); an entry that
    exists but fails validation is a genuine violation (`problems`, `NEEDS_HUMAN`).

    `promote_to_global=False` (coordinator ruling F1c) never turns off the CONSISTENCY check --
    an answer that disagrees with what `mappings/global.yaml` already has for that key is still a
    `conflicts` entry, still `NEEDS_HUMAN`, whether or not this call is allowed to promote -- it
    only skips writing a *new* key into `global_map`. The caller (`run`) decides this from how the
    identity behind `user` was obtained: an interactive session or an explicitly-given `--user`
    promotes; the default non-interactive `automation` identity does not (program-wide answers are
    intended, reviewed program state, not something a resume with nobody at a keyboard should
    silently add to).

    `intake/mappings.yaml` is CUMULATIVE and SELF-SUFFICIENT (coordinator ruling 1, fix round 1):
    this starts from whatever the file already has -- not a blank slate -- and layers this call's
    answers on top; a later answer for a key replaces the earlier entry, but nothing already
    recorded is ever dropped just because this call didn't happen to re-answer it. On top of that,
    every input/output touchpoint that is `resolved` (from `mappings/global.yaml`, via
    `intake_touchpoints.py`'s own resolution) and not already covered by an entry gets one
    backfilled here too, `confirmed_by` taken from the *global* entry's own `confirmed_by` plus
    `from: "global"` -- so a workflow whose sources were all confirmed in earlier sessions, and
    then simply resolved from the program-wide file on a later `intake_touchpoints.py` run, still
    ends up with a fully self-sufficient `intake/mappings.yaml`: nothing here ever needs a reader
    to fall back to `mappings/global.yaml` (nothing outside this module does; `load_golden.py`
    reads only the workflow's own file).
    """
    by_id = {t["id"]: t for t in touchpoints if t.get("id")}

    existing_path = repo.wf(wf_id, "intake", "mappings.yaml")
    existing = lib_io.read_yaml(existing_path) if existing_path.is_file() else {}
    existing = existing or {}
    mappings: dict = {
        "sources": dict(existing.get("sources") or {}),
        "outputs": dict(existing.get("outputs") or {}),
        "constants": {}, "parameters": {}, "macros": {},
    }
    taken_logicals: set[str] = {
        entry["logical"]
        for entry in list(mappings["sources"].values()) + list(mappings["outputs"].values())
        if entry.get("logical")
    }
    confirmed_constants: set[str] = set()

    global_map = lib_io.read_yaml(repo.global_mappings) if repo.global_mappings.is_file() else {}
    global_map = global_map or {}
    global_map.setdefault("sources", {})
    global_map.setdefault("outputs", {})
    conflicts: list[dict] = []
    impossible = False

    def promote(section: str, key: str, entry: dict) -> None:
        existing_global = global_map[section].get(key)
        if existing_global is not None:
            if (existing_global.get("snowflake") or "").upper() != entry["snowflake"].upper():
                conflicts.append({"section": section, "key": key,
                                  "existing": existing_global.get("snowflake"),
                                  "attempted": entry["snowflake"]})
            return
        if not promote_to_global:
            return
        promoted = dict(entry)
        promoted["load_path"] = "already_there"
        global_map[section][key] = promoted

    def same_fqn(existing_entry: dict | None, snowflake: str) -> bool:
        return bool(existing_entry) and \
            (existing_entry.get("snowflake") or "").upper() == snowflake.upper()

    def free_up(existing_entry: dict | None) -> None:
        """Before recomputing a *changed* entry, remove its own previous `logical` from the
        collision set -- otherwise a re-answer with a new FQN would needlessly collide with the
        name it is itself replacing (coordinator ruling 3, fix round 3)."""
        if existing_entry and existing_entry.get("logical"):
            taken_logicals.discard(existing_entry["logical"])

    # 1) Layer this call's answers on top of whatever the file already had. Fix round 3: intake
    # resume must be idempotent (coordinator ruling). Re-running with nothing changed re-applies
    # the SAME answers every time (open_questions.md is re-parsed fresh each call), so an answer
    # whose FQN already matches the existing entry is a no-op -- the entry (its `logical`, `mode`,
    # `keys`, `confirmed_by`, `self_reference`, `note`, everything) is kept exactly as it is and
    # NOTHING is recomputed. Recomputing unconditionally here was the actual bug: seeding
    # `taken_logicals` from the existing file (needed so a genuinely NEW entry doesn't collide with
    # an established one) meant re-answering a key with its own unchanged FQN collided with its OWN
    # prior logical name and fell back to `<SCHEMA>_<TABLE>` -- then the FOLLOWING run saw that
    # fallback name on file instead, so the plain name was "free" again, and the two states
    # flip-flopped forever. Also protects a hand-edited `logical`/`mode`/`keys`: since nothing here
    # ever touches an entry whose FQN didn't change, an owner's override on disk survives resume.
    for a in answers:
        if a.get("action") == "impossible":
            impossible = True
            continue
        if a.get("action") != "map":
            continue

        t = by_id.get(a.get("id"))
        if t is None:
            # A user-declared source from the opening yxdb loop: not tied to any touchpoint id.
            key, snowflake = a.get("key"), a.get("snowflake")
            if not key or not snowflake:
                continue
            existing_entry = mappings["sources"].get(key)
            if same_fqn(existing_entry, snowflake):
                # Fix round 4: the entry is untouched, but it is still checked against
                # mappings/global.yaml -- status is a pure function of the files, so a conflict
                # is reported on every run until a human reconciles it.
                promote("sources", key, existing_entry)
                continue
            free_up(existing_entry)
            entry = {"snowflake": snowflake, "logical": logical_name(snowflake, taken_logicals),
                     "tool_ids": a.get("tool_ids") or [], "confirmed_by": user}
            if a.get("note"):
                entry["note"] = a["note"]
            mappings["sources"][key] = entry
            promote("sources", key, entry)
            continue

        if t["kind"] in ("input", "output"):
            section = "sources" if t["kind"] == "input" else "outputs"
            snowflake = a["snowflake"]
            existing_entry = mappings[section].get(t["key"])
            if same_fqn(existing_entry, snowflake):
                promote(section, t["key"], existing_entry)   # fix round 4, as above
                continue
            free_up(existing_entry)
            entry = {"snowflake": snowflake, "logical": logical_name(snowflake, taken_logicals),
                     "tool_ids": [t["tool_id"]] if t.get("tool_id") else [], "confirmed_by": user}
            if t["kind"] == "output":
                entry["mode"] = a.get("write_mode")
                entry["keys"] = a.get("keys") or []
                if a.get("self_reference"):
                    # Confirmed despite colliding with one of this workflow's own inputs or the
                    # raw schema (coordinator ruling): recorded so render_open_questions can carry
                    # the run-ordering risk (program spec §8.5) forward as a non-blocking item.
                    entry["self_reference"] = True
            mappings[section][t["key"]] = entry
            promote(section, t["key"], entry)
        elif t["kind"] == "constant":
            mappings["constants"][t["key"]] = a.get("snowflake") or t["source"]
            confirmed_constants.add(t["id"])
        # macro/manual/parameter touchpoints never receive a "map" answer from this module.

    # 2) Backfill any blocking input/output that is `resolved` (from mappings/global.yaml) and has
    #    no entry yet (neither from the pre-existing file nor from this call's own answers, above).
    for t in touchpoints:
        if t["kind"] not in ("input", "output") or t["resolved"] is None:
            continue
        section = "sources" if t["kind"] == "input" else "outputs"
        if t["key"] in mappings[section]:
            continue
        resolved = t["resolved"]
        global_entry = (global_map.get(section) or {}).get(t["key"]) or {}
        logical = resolved.get("logical") or logical_name(resolved["snowflake"], taken_logicals)
        taken_logicals.add(logical)
        entry = {"snowflake": resolved["snowflake"], "logical": logical,
                 "tool_ids": [t["tool_id"]] if t.get("tool_id") else [],
                 "confirmed_by": global_entry.get("confirmed_by", "unknown"), "from": "global"}
        if t["kind"] == "output":
            entry["mode"] = global_entry.get("mode")
            entry["keys"] = global_entry.get("keys") or []
            if global_entry.get("self_reference"):
                entry["self_reference"] = True
        mappings[section][t["key"]] = entry

    # 3) constants/parameters/macros are always refreshed straight from the dag, never merged from
    #    the old file (a removed constant or macro should not linger).
    for t in touchpoints:
        if t["kind"] == "constant" and t["id"] not in confirmed_constants:
            mappings["constants"][t["key"]] = t["source"]
        elif t["kind"] == "macro":
            mappings["macros"][t["key"]] = {"inline": True}
        elif t["kind"] == "parameter":
            mappings["parameters"][t["key"]] = {"type": t.get("format"), "default": t["source"]}

    missing = _missing_blocking_entries(touchpoints, mappings)
    problems = _mapping_problems(touchpoints, mappings)

    if impossible:
        status = "BLOCKED"
    elif conflicts:
        status = "NEEDS_HUMAN"
    elif problems:
        # A genuine violation (an entry exists but is invalid) -- not "still waiting", something
        # is actually wrong and needs a human, even if every touchpoint otherwise has an entry.
        status = "NEEDS_HUMAN"
    elif missing:
        status = "WAITING_FOR_ANSWERS"
    else:
        status = "READY"

    lib_io.write_yaml(repo.wf(wf_id, "intake", "mappings.yaml"), mappings)
    lib_io.write_global_mappings(repo.global_mappings, global_map)

    return {
        "status": status,
        "mappings": mappings,
        "conflicts": conflicts,
        "missing": missing,
        "problems": problems,
        "confirmed_constants": confirmed_constants,
        "answers_by_qid": {a["id"]: (a.get("snowflake") or "") for a in answers
                           if a.get("action") == "map" and a.get("id") in by_id},
    }


# --- intake/open_questions.md --------------------------------------------------------------------
#
# Fix round 1 (coordinator ruling 3): every checkbox item's FIRST line is just the checkbox plus a
# short, colon-free description -- never the proposal, a CONFLICT note, or the answer itself. Every
# other fact about the item (the proposal, a conflict, the self-reference hint, the answer) is an
# INDENTED CONTINUATION line, and the answer always comes from the fixed-label line
# (`_LABEL_IO`/`_LABEL_CONSTANT`, `_ANSWER_LABELS`) emitted LAST. This keeps the first line free of
# any `": "` that `parse_answered_questions`'s fallback could misread, and keeps a note (self-
# reference, run-ordering) from ever being mistaken for a checkbox item of its own (IMPORTANT-B).

def _render_io_item(t: dict, mappings: dict, conflicts: dict) -> str:
    section = "sources" if t["kind"] == "input" else "outputs"
    entry = (mappings.get(section) or {}).get(t["key"])
    checked = entry is not None
    answer_text = entry["snowflake"] if entry else ""
    box = "x" if checked else " "
    lines = [f"- [{box}] {t['id']} - {_describe_tool(t)}.",
            f"      {_proposal_text(t)}"]
    conflict = conflicts.get((section, t["key"]))
    if conflict:
        lines.append(f"      CONFLICT: mappings/global.yaml already maps this to "
                     f"{conflict['existing']}; this run proposed {conflict['attempted']} -- "
                     "a human must pick one.")
    if t["kind"] == "output" and not checked:
        # Proactive, not reactive: this workflow's own candidates already exclude a source/raw-
        # schema table (coordinator ruling), so the human reading this file needs to know *before*
        # typing one in, not only after a rejected attempt gets silently dropped.
        lines.append(f"      (a table this workflow already reads, or in its raw landing schema, "
                     f"needs '{_SELF_REF_TOKEN}' appended to the answer to confirm it anyway.)")
    lines.append(f"      {_LABEL_IO} {answer_text}")
    return "\n".join(lines)


def _render_self_reference_note(t: dict, entry: dict) -> str:
    # A `*` bullet, deliberately not `- [ ]`/`- [x]`: this is a note about an already-answered
    # item, not an item of its own -- IMPORTANT-B: it must never match `_CHECKBOX_RE`, or a later
    # parse would read this note's own prose as Q<n>'s answer and silently lose the real one.
    return (f"* (self-reference) {t['id']} - Tool {t['tool_id']} Output Data was confirmed to "
           f"target {entry['snowflake']} (mode {entry.get('mode') or '?'}), which is also one of "
           "this workflow's own inputs or sits in the raw landing schema: a run-ordering risk "
           "(program spec §8.5) -- make sure this output only runs after that input has loaded.")


def _render_constant_item(t: dict, mappings: dict, confirmed: set[str]) -> str:
    value = (mappings.get("constants") or {}).get(t["key"], t["source"])
    checked = t["id"] in confirmed
    override_text = value if (checked and value != t["source"]) else ""
    box = "x" if checked else " "
    return "\n".join([
        f"- [{box}] {t['id']} - Workflow constant {t['key']} is currently `{t['source']}`; "
        "used as-is unless overridden.",
        f"      {_LABEL_CONSTANT} {override_text}",
    ])


def _render_info_item(t: dict, label: str, consequence: str) -> str:
    what = t.get("annotation") or t.get("source") or t.get("key") or t["kind"]
    return f"- [x] {t['id']} - Tool {t['tool_id']} {label}: {what} -- {consequence}."


def render_open_questions(wf_id: str, owner: str, touchpoints: list[dict], mappings: dict) -> str:
    """Program spec §5.4's format: `## Blocking` / `## Non-blocking`, one `- [ ]`/`- [x]` item per
    still-open touchpoint (an already-`resolved`-from-global one needs no question at all, so it is
    left out entirely) naming its `Q<n>` id, what the tool does, the proposal with evidence, and
    the consequence. `mappings` is `apply_answers`'s returned mapping dict; `run` also folds in
    `_conflicts`/`_confirmed_constants` (private render-only keys, never written to disk) so a
    `NEEDS_HUMAN` conflict and a confirmed constant override can be named here too, and, when its
    guard fires (fix round 2), `_guard_problems` (`_mapping_problems`'s findings) so the human
    reading this file sees exactly which entries are incomplete or invalid and why, as a `>` note
    at the very top -- never a `- [ ]`/`- [x]` line, so it can never be mistaken for an item.
    """
    conflicts = {(c["section"], c["key"]): c for c in (mappings.get("_conflicts") or [])}
    confirmed_constants = set(mappings.get("_confirmed_constants") or [])
    guard_problems = mappings.get("_guard_problems") or []

    blocking: list[str] = []
    nonblocking: list[str] = []
    for t in touchpoints:
        if t["resolved"] is not None:
            continue
        if t["kind"] in ("input", "output"):
            line = _render_io_item(t, mappings, conflicts)
            (blocking if t["blocking"] else nonblocking).append(line)
        elif t["kind"] == "constant":
            nonblocking.append(_render_constant_item(t, mappings, confirmed_constants))
        elif t["kind"] == "macro":
            nonblocking.append(_render_info_item(t, "macro",
                "translated inline as a shared-procedure candidate"))
        elif t["kind"] == "manual":
            nonblocking.append(_render_info_item(t, "manual tool",
                "requires manual migration; does not block intake"))
        elif t["kind"] == "parameter":
            nonblocking.append(_render_info_item(t, "parameter",
                "becomes a procedure argument with this default"))

    for t in touchpoints:
        if t["kind"] != "output":
            continue
        entry = (mappings.get("outputs") or {}).get(t["key"])
        if entry and entry.get("self_reference"):
            nonblocking.append(_render_self_reference_note(t, entry))

    lines = [f"# Open questions - {wf_id} (owner: @{owner})", ""]
    if guard_problems:
        lines.append("> GUARD: intake/mappings.yaml has entries that are missing, incomplete or "
                     "invalid; status forced to NEEDS_HUMAN.")
        lines.extend(f"> - {problem}" for problem in guard_problems)
        lines.append("")
    lines.append("## Blocking (translation waits)")
    lines += blocking or ["(none)"]
    lines += ["", "## Non-blocking (proceeds with the assumption)"]
    lines += nonblocking or ["(none)"]
    return "\n".join(lines) + "\n"


_CHECKBOX_RE = re.compile(r"^-\s\[([ xX])\]\s+(Q\d+)\b")
_HEADING_RE = re.compile(r"^#{1,6}\s")
# A trailing FQN (optionally with the self-reference token) at the very end of a line -- the last
# fallback for an item whose answer was appended straight onto its checkbox line instead of onto
# its own labelled line (see `parse_answered_questions`'s docstring, precedence 3).
_TRAILING_ANSWER_RE = re.compile(
    FQN_RE[1:-1] + r"(?:\s+" + re.escape(_SELF_REF_TOKEN) + r")?\s*$")


def _item_blocks(md_text: str) -> list[tuple[str, bool, list[str]]]:
    """`md_text` -> `[(qid, checked, lines), …]`. An item is every line from its `- [ ]`/`- [x]
    Q<n>` checkbox up to (not including) the next checkbox or heading line -- a note bullet that
    doesn't start with a checkbox (e.g. `_render_self_reference_note`'s `*` line) is just one more
    line of whatever item is currently open, never a block of its own.
    """
    blocks: list[tuple[str, bool, list[str]]] = []
    lines: list[str] | None = None
    qid = ""
    checked = False
    for raw_line in md_text.splitlines():
        line = raw_line.rstrip()
        match = _CHECKBOX_RE.match(line)
        if match:
            if lines is not None:
                blocks.append((qid, checked, lines))
            qid = match.group(2)
            checked = match.group(1).strip() != ""
            lines = [line]
            continue
        if _HEADING_RE.match(line):
            if lines is not None:
                blocks.append((qid, checked, lines))
            lines = None
            continue
        if lines is not None:
            lines.append(line)
    if lines is not None:
        blocks.append((qid, checked, lines))
    return blocks


def _item_answer(lines: list[str]) -> str:
    """The answer text for one item's block, by precedence (coordinator ruling 3, fix round 1):
    1. the designated answer line's own text -- the line (anywhere in the block, though the
       renderer always emits it last) starting with a known label (`_ANSWER_LABELS`);
    2. else the text after the LAST `': '` in the item's FIRST line only (kept for the brief's own
       `test_answers_written_into_open_questions_resume_the_workflow`, which appends the answer
       straight onto the checkbox line instead of a labelled one);
    3. else a trailing token on the first line matching `FQN_RE`, optionally followed by
       ` confirm-self-reference`.
    A block with none of the above is "no answer" (`""`), not garbage from stray prose -- a note
    line's own `': '` (e.g. a CONFLICT explanation) is never mistaken for an answer because it is
    never the item's *first* line and never starts with a known label.
    """
    for line in reversed(lines):
        stripped = line.strip()
        for label in _ANSWER_LABELS:
            if stripped.startswith(label):
                text = stripped[len(label):].strip()
                if text:
                    return text
    first = lines[0].strip()
    idx = first.rfind(": ")
    if idx != -1:
        candidate = first[idx + 2:].strip()
        if candidate:
            return candidate
    trailing = _TRAILING_ANSWER_RE.search(first)
    if trailing:
        return trailing.group(0).strip()
    return ""


def parse_answered_questions(md_text: str) -> dict[str, str]:
    """Block-based and label-anchored (coordinator ruling 3, fix round 1): every CHECKED item's
    `Q<n>` id -> its answer text (see `_item_answer` for the exact precedence), read only from that
    item's own block -- never from a later item's or a note's unrelated `': '`. An empty string
    means the item was checked with no answer found anywhere -- "accept the proposal". Unchecked
    items are not included at all.
    """
    return {qid: _item_answer(lines) for qid, checked, lines in _item_blocks(md_text) if checked}


def _merged_answers(repo: Repo, wf_id: str, manifest: dict) -> dict[str, str]:
    """Non-interactive resume: `manifest.answers`, then `open_questions.md` layered on top (the
    more recently hand-edited source wins) -- the brief's "first merges answers found in
    open_questions.md and manifest.answers"."""
    merged = dict(manifest.get("answers") or {})
    path = repo.wf(wf_id, "intake", "open_questions.md")
    if path.is_file():
        merged.update(parse_answered_questions(path.read_text(encoding="utf-8")))
    return merged


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


OUTPUT_TARGETS = ("procedures", "dbt")


def ask_output_target(ask: Callable[[str], str], out: Callable[[str], None]) -> str | None:
    """Design §3.3: one program-level question, asked only when mappings/global.yaml has no
    program.output_target. Enter takes `procedures`; an answer outside the vocabulary is asked once
    more; a second bad answer or a closed stdin is no answer at all (target_check.py --prefer auto
    then falls back to procedures)."""
    for _ in range(2):
        try:
            text = (ask("Output target for this workflow? procedures | dbt [procedures]: ") or "").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if not text:
            return "procedures"
        if text in OUTPUT_TARGETS:
            return text
        out(f"  {text!r} is not one of: {', '.join(OUTPUT_TARGETS)}")
    out("  no output target recorded; target_check.py will use the program default")
    return None


# --- run ---------------------------------------------------------------------------------------

def run(repo: Repo, wf_id: str, *, interactive: bool, ask: Callable[[str], str] = input,
       out: Callable[[str], None] = print, user: str | None = None,
       promote: bool = True) -> str:
    """Reads `intake/touchpoints.json`, asks (interactively) or resumes (from
    `open_questions.md`/`manifest.answers`) for every unresolved touchpoint, writes
    `intake/mappings.yaml` and `intake/open_questions.md`, promotes confirmed mappings to
    `mappings/global.yaml` when `promote` is true, stamps `manifest.status.intake`, and returns
    that status. `promote` defaults to true for direct callers of `run` (a caller that passes its
    own `user` is, by that fact, giving an explicit identity); `main` computes it from how `user`
    was actually obtained (coordinator ruling F1c) and passes it through explicitly.

    Raises `FileNotFoundError` when `intake_touchpoints.py` hasn't been run for this workflow yet
    -- a usage error for the CLI, not a domain outcome.
    """
    tp_path = repo.wf(wf_id, "intake", "touchpoints.json")
    if not tp_path.is_file():
        raise FileNotFoundError(
            f"no intake/touchpoints.json for {wf_id}; "
            f"run `python scripts/intake_touchpoints.py {wf_id}` first")
    touchpoints = lib_io.read_json(tp_path)

    user = user or "unknown"
    manifest = lib_io.load_manifest(repo, wf_id)
    owner = (manifest.get("source") or {}).get("owner") or user

    program = ((lib_io.read_yaml(repo.global_mappings) or {}).get("program") or {}) \
        if repo.global_mappings.is_file() else {}
    raw_schema = program.get("raw_schema")

    # `intake/mappings.yaml["sources"]` as already on disk -- fix round 1, coordinator ruling 2:
    # self-reference protection covers a source an *earlier* session already declared (including a
    # `note: user-declared` one from the opening yxdb loop), not just this session's own DAG
    # inputs and this session's own opening-loop answers.
    existing_mappings_path = repo.wf(wf_id, "intake", "mappings.yaml")
    existing_sources = None
    if existing_mappings_path.is_file():
        existing_sources = (lib_io.read_yaml(existing_mappings_path) or {}).get("sources")

    if interactive:
        source_file = (manifest.get("source") or {}).get("file")
        out(f"{wf_id} - {source_file}" if source_file else wf_id)
        catalog = tpx.load_catalog(repo)

        # Design §3.3: the one program-level question, asked before any touchpoint -- and only
        # when neither mappings/global.yaml nor an earlier run (sample.json's own override, or a
        # prior answer) already settled it. `manifest` is the same dict `save_manifest` writes at
        # the end of `run`, so recording the answer here is enough to persist it.
        if not program.get("output_target") and not manifest.get("output_target"):
            choice = ask_output_target(ask, out)
            if choice:
                manifest["output_target"] = choice

        def _rescore(t: dict, exclude=()) -> None:
            t["candidates"] = tpx.propose_candidates(t, catalog, program, exclude=exclude)

        answers = prompt_touchpoints(touchpoints, ask=ask, out=out, rescore=_rescore,
                                     raw_schema=raw_schema, existing_sources=existing_sources)
    else:
        by_id = {t["id"]: t for t in touchpoints}
        merged = _merged_answers(repo, wf_id, manifest)
        answers = []

        # Inputs (and constants) first, so `known_input_sources` below sees this run's own input
        # answers -- not just what was already known when intake_touchpoints.py last ran -- before
        # any output answer is checked against them (coordinator ruling; mirrors the interactive
        # ordering in prompt_touchpoints).
        for qid, text in merged.items():
            t = by_id.get(qid)
            if t is None or t["resolved"] is not None:
                continue
            if t["kind"] == "constant":
                answers.append({"id": qid, "action": "map", "snowflake": text or None})
            elif t["kind"] == "input":
                try:
                    result = _interpret_answer_text(t, text)
                except _Invalid:
                    continue  # a hand-typed answer this module can't make sense of: stays unresolved
                result["id"] = qid
                answers.append(result)

        workflow_sources = tpx.known_input_sources(touchpoints, answers,
                                                    existing_sources=existing_sources)
        for qid, text in merged.items():
            t = by_id.get(qid)
            if t is None or t["resolved"] is not None or t["kind"] != "output":
                continue
            base_text, confirmed = _strip_self_reference_token(text)
            try:
                result = _interpret_answer_text(t, base_text, workflow_sources=workflow_sources,
                                                raw_schema=raw_schema)
            except _Invalid:
                continue  # a hand-typed answer this module can't make sense of: stays unresolved
            except _SelfReferenceRisk as risk:
                if not confirmed:
                    # Stays unresolved; render_open_questions's per-item hint already explains
                    # the `confirm-self-reference` token, so nothing more to do here.
                    continue
                result = {"action": "map", "snowflake": risk.snowflake, "self_reference": True}
            result["id"] = qid
            if result.get("action") == "map":
                mode = _default_write_mode(t)
                result["write_mode"] = mode
                result["keys"] = list(t.get("keys") or []) if mode == "merge" else []
            answers.append(result)

    result = apply_answers(repo, wf_id, touchpoints, answers, user, promote_to_global=promote)

    # Guard (coordinator ruling, fix round 2): intake may report READY only if every blocking
    # touchpoint's entry in intake/mappings.yaml is not just PRESENT but COMPLETE and VALID
    # (`_mapping_problems`: a valid snowflake and logical, tool_ids covering the touchpoint, valid
    # output mode/keys, no logical collisions across the file). `apply_answers` already computes
    # this honestly (its own status is `NEEDS_HUMAN` whenever `problems` is non-empty), so this
    # recomputes it independently against the file it actually wrote rather than trusting that
    # field -- the whole point is a second check that survives even a future bug in the first one,
    # or a hand-edited file. Any genuine validity problem is ALWAYS reported here (message printed
    # and noted in open_questions.md), whether or not apply_answers already caught it on its own --
    # the point is a human sees exactly what's wrong regardless of which layer caught it. Missing
    # presence (`_missing_blocking_entries`, fix round 1's original, narrower check) only escalates
    # when it contradicts a claimed `READY`: a touchpoint with no entry yet is the normal, silent
    # `WAITING_FOR_ANSWERS` case otherwise.
    guard_missing = _missing_blocking_entries(touchpoints, result["mappings"])
    guard_problems = _mapping_problems(touchpoints, result["mappings"])
    guard_violation = bool(guard_problems) or (bool(guard_missing) and result["status"] == "READY")
    if guard_violation:
        out("GUARD: intake/mappings.yaml has entries that are missing, incomplete or invalid -- "
           "status forced to NEEDS_HUMAN.")
        for t_id in guard_missing:
            out(f"  - touchpoint {t_id}: no entry in intake/mappings.yaml")
        for problem in guard_problems:
            out(f"  - {problem}")
        result = dict(result)
        result["status"] = "NEEDS_HUMAN"

    render_mappings = dict(result["mappings"])
    render_mappings["_conflicts"] = result["conflicts"]
    render_mappings["_confirmed_constants"] = result["confirmed_constants"]
    if guard_violation:
        render_mappings["_guard_problems"] = (
            [f"touchpoint {t_id}: no entry in intake/mappings.yaml" for t_id in guard_missing]
            + guard_problems)
    md = render_open_questions(wf_id, owner, touchpoints, render_mappings)
    _write_text(repo.wf(wf_id, "intake", "open_questions.md"), md)

    manifest.setdefault("status", {})["intake"] = result["status"]
    manifest.setdefault("answers", {}).update(result["answers_by_qid"])
    lib_io.save_manifest(repo, manifest)

    return result["status"]


# --- CLI --------------------------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0001")
    parser.add_argument("--no-interactive", action="store_true",
                        help="never prompt; resume from open_questions.md / manifest.answers")
    parser.add_argument("--interactive", action="store_true",
                        help="force interactive prompting even when stdin is not a TTY")
    parser.add_argument("--user", default=None,
                        help="name recorded as confirmed_by (default: the OS login name when "
                             "interactive, 'automation' when resuming non-interactively)")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    if args.no_interactive and args.interactive:
        parser.error("--no-interactive and --interactive are mutually exclusive")

    interactive = args.interactive or (not args.no_interactive and sys.stdin.isatty())
    if args.user:
        user = args.user
    elif interactive:
        # A human is actually at this keyboard, so the OS login name is a reasonable default for
        # who is confirming these mappings.
        try:
            user = getpass.getuser()
        except Exception:
            user = "unknown"
    else:
        # Nobody is present: this call is resuming from open_questions.md/manifest.answers (an
        # orchestrator pass with nobody at a keyboard, or this project's own offline sample runs).
        # Attributing that resume to whatever OS account happens to run the resuming process would
        # be both inaccurate -- that account did not confirm anything, a human answered earlier --
        # and a machine-specific value that would otherwise get baked into a committed artifact
        # (`intake/mappings.yaml`'s `confirmed_by`). `--user` still overrides this explicitly.
        user = "automation"

    # Coordinator ruling F1c: promotion to mappings/global.yaml -- program-wide, committed state --
    # happens for an interactive session or an explicitly-given `--user`, never for the default
    # non-interactive `automation` identity. The consistency check against mappings/global.yaml
    # still runs either way (apply_answers's own `promote_to_global=False` path only skips writing
    # a *new* key; a conflict is still reported).
    promote = interactive or bool(args.user)

    repo = Repo(args.root)
    try:
        status = run(repo, args.wf_id, interactive=interactive, user=user, promote=promote)
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))  # exit 2: bad workflow id, or intake_touchpoints.py hasn't run
    except (EOFError, KeyboardInterrupt):
        # Defense in depth: prompt_touchpoints already swallows these and persists partial
        # answers, so `run` should return normally. This only fires if that ever changes.
        print("intake interrupted; any answers already given were saved.", file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc()
        return 2

    print(f"{args.wf_id}: intake {status}")
    return 0 if status == "READY" else 1


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
