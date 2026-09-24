"""Check every segment's contract.json before any later stage reads it (Task L3, docs/reference/contracts.md).

    contract_check.py <wf_id> [--segments seg_01,seg_02] [--root .]

Three kinds of rule, each problem printed on its own line to stderr as `<seg>: <field> ...;
expected ...` -- the exact text the orchestrator records (`contract: <first problem>`) and shows
the analyzer on its retry, and what the analyzer sees when it runs this script itself:

1. **Mechanical agreement** with `contract_scaffold.py`: `workflow`, `segment`, every input's
   `logical`/`tool_id` (a mapped source) or `from`/`stream`/`table` (an upstream stream), every
   output's `stream`, `kind`, `tool_id`, `logical`, `table`, `write_mode`, every column's name and
   type, the entries' order, and `output` == `outputs[0]`. Keys of another entry kind (a `table` on a
   mapped source) are refused. A variable-length string may be spelled `VARCHAR(<size>)` or the
   type map's `VARCHAR`; a target that keeps its rows (append, update_insert) may list target-only
   columns after the stream's own. A field the scaffold could not derive is only shape-checked.
2. **Shape of what downstream scripts read**: every column's `nullable` is a boolean, every entry's
   `keys` a list of its own column names, `expected_rows` (optional) `{"min", "max"}` integers with
   0 <= min <= max, `large` (optional) a boolean; `target` is `sql`, `snowpark` or `manual` and never
   above `segments/targets.json`'s proposal.
3. **Judgment well-formed**: `row_relation` one of `1:1`, `filter`, `aggregate`, `expand`;
   `ordering` exactly `{keys, alteryx_deterministic, order_dependent_columns}` -- `keys` columns the
   segment carries anywhere (inputs, its tools' anchors, outputs: an ordering key may be dropped
   before the output, as wf_0003/seg_02's is), `alteryx_deterministic` a boolean,
   `order_dependent_columns` output columns; `tolerances` keyed by output column, each
   `{"float_abs", "float_rel"}` non-negative numbers (compare.py's per-column override);
   `normalizations` `"trim:<COL>"`/`"upper:<COL>"` on output columns (compare.py's directives);
   `parity_risks` exactly `{tool_id, class, note}` with a tool of this segment (or one inside a macro
   of it, `<macro>/<tool>`, as its sub-DAG names it) and a class of `lib.vocab.DIFF_CLASSES`.
   A column type the scaffold could not derive, or a target-only column's, is a Snowflake type
   name with an optional `(precision[, scale])` -- what compile_check.py can build a table from.

Exit codes: 0 every contract passes; 1 any problem (a missing or unreadable contract.json included);
2 usage (no `segments/order.json`, `parsed/dag.json` or `intake/mappings.yaml`, an unknown
`--segments` id) or a crash. Writes nothing.

Nothing here has run on Snowflake or Alteryx: it compares JSON the analyzer wrote with JSON the
pipeline derived.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import traceback
from typing import Sequence

import contract_scaffold as cs
from lib.io import read_json
from lib.paths import Repo, add_root_arg
from lib.vocab import DIFF_CLASSES

ROW_RELATIONS = ("1:1", "filter", "aggregate", "expand")
TARGETS = ("sql", "snowpark", "manual")
TARGET_RANK = {"manual": 0, "snowpark": 1, "sql": 2}
ORDERING_FIELDS = ("keys", "alteryx_deterministic", "order_dependent_columns")
TOLERANCE_KEYS = frozenset({"float_abs", "float_rel"})
NORMALIZATION_OPS = ("trim", "upper")
LIST_LIMIT = 20

ORDERING_SHAPE = ('{"keys": [<column>...], "alteryx_deterministic": true|false, '
                  '"order_dependent_columns": [<output column>...]}')
TOLERANCE_SHAPE = '{"float_abs": <number >= 0>, "float_rel": <number >= 0>} (either or both)'
ROWS_SHAPE = '{"min": <integer >= 0>, "max": <integer >= min>}'
NORMALIZATIONS_SHAPE = 'a list of "trim:<COLUMN>" / "upper:<COLUMN>" directives (may be empty)'
TYPE_SHAPE = 'a Snowflake column type such as "VARCHAR(10)" or "NUMBER(38,0)"'
RISK_FIELDS = ("tool_id", "class", "note")

#: A column type as `compile_check.py` splices it into the DDL of a contract-shaped table: one or more
#: words (`GEOGRAPHY`, `TIMESTAMP_NTZ`, `DOUBLE PRECISION`) and an optional `(precision[, scale])`.
#: Anything else -- empty, not a string, a quote, a `;` -- is refused (review of L3, I2).
TYPE_GRAMMAR = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?: [A-Za-z][A-Za-z0-9_]*)*(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?$")


def _shown(value) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 120 else text[:117] + "..."


def _names(names) -> str:
    names = list(names)
    head = ", ".join(str(n) for n in names[:LIST_LIMIT])
    return head + (f", ... ({len(names) - LIST_LIMIT} more)" if len(names) > LIST_LIMIT else "")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    """A JSON number `compare.py` can read with `float()`: never a bool, never infinite, never an
    integer too large for a float (which overflows -- review of L3, M5)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _label(entry: dict, section: str) -> str:
    kind = cs.entry_kind(entry, section)
    if kind == "source":
        return f"source tool {entry.get('tool_id')}" if entry.get("tool_id") else f"source {entry.get('logical')}"
    if kind == "stream":
        return f"stream {entry.get('stream')} from {entry.get('from')}"
    if kind == "target":
        return f"target tool {entry.get('tool_id')}" if entry.get("tool_id") else f"target {entry.get('logical')}"
    return f"work stream {entry.get('stream')}"


def _expected_entry(entry: dict, section: str) -> str:
    kind = cs.entry_kind(entry, section)
    parts = []
    if kind in ("stream", "target") and entry.get("stream"):
        parts.append(f"stream {entry['stream']}")
    if kind in ("source", "target") and entry.get("logical"):
        parts.append(f"logical {entry['logical']}")
    if kind in ("stream", "work") and entry.get("table"):
        parts.append(f"table {entry['table']}")
    return f"expected one ({', '.join(parts)})" if parts else "expected one"


class _Problems(list):
    def __init__(self, seg: str):
        super().__init__()
        self.seg = seg

    def missing(self, path: str, expected: str) -> None:
        self.append(f"{self.seg}: {path} is missing; expected {expected}")

    def wrong(self, path: str, value, expected: str) -> None:
        self.append(f"{self.seg}: {path} is {_shown(value)}; expected {expected}")

    def line(self, text: str) -> None:
        self.append(f"{self.seg}: {text}")

    def bad(self, owner: dict, key: str, path: str, expected: str) -> None:
        """`missing` when `owner` has no `key`, else `wrong` with the value it has."""
        if key in owner:
            self.wrong(path, owner[key], expected)
        else:
            self.missing(path, expected)


def _check_bool(p: _Problems, owner: dict, key: str, path: str, required: bool = True) -> None:
    if (key in owner or required) and not isinstance(owner.get(key), bool):
        p.bad(owner, key, path, "true or false")


def _check_columns(p: _Problems, where: str, mine: list[dict] | None, theirs, accepted: dict[int, frozenset[str]],
                   extras: bool) -> list[str]:
    """Columns against the scaffold (or, when it has none, their shape only); returns their names."""
    expected_shape = "a list of {name, type, nullable} objects"
    if not isinstance(theirs, list):
        p.missing(f"{where}.columns", expected_shape) if theirs is None else p.wrong(f"{where}.columns", theirs, expected_shape)
        return []
    for index, column in enumerate(theirs):
        if not isinstance(column, dict):
            p.wrong(f"{where}.columns[{index}]", column, "a {name, type, nullable} object")
    names = [str(c.get("name")) for c in theirs if isinstance(c, dict)]
    derived = {c["name"]: (i, c) for i, c in enumerate(mine or [])}
    if mine is not None:
        wanted = [c["name"] for c in mine]
        head = names[:len(wanted)] if extras else names
        if head != wanted:
            tail = " followed by any column only the existing target table has" if extras else ""
            p.line(f"{where}.columns are named [{', '.join(names)}]; expected [{', '.join(wanted)}]{tail}")
    for index, column in enumerate(theirs):
        if not isinstance(column, dict):
            continue
        name = str(column.get("name"))
        path = f"{where}.columns[{index}] ({name})"
        if not isinstance(column.get("name"), str) or not column["name"].strip():
            p.wrong(f"{where}.columns[{index}].name", column.get("name"), "a column name")
        at = derived.get(name)
        if at is not None and "type" in at[1]:
            want = at[1]["type"]
            also = sorted(accepted.get(at[0], ()))
            if column.get("type") != want and column.get("type") not in also:
                p.bad(column, "type", f"{path}.type", _shown(want) + "".join(f" (or {_shown(a)})" for a in also))
        elif not isinstance(column.get("type"), str) or not TYPE_GRAMMAR.match(column["type"]):
            # No derived type to compare with (a derivation gap, a target-only column, an entry the
            # scaffold has no columns for): the shape compile_check.py can build a table from.
            p.bad(column, "type", f"{path}.type", TYPE_SHAPE)
        _check_bool(p, column, "nullable", f"{path}.nullable")
    return names


def _check_entry(p: _Problems, section: str, index: int, position: int, entry: dict, mine: dict,
                 scaffold: cs.SegmentScaffold) -> None:
    """One written entry (at `position` of the contract's list) against the scaffold's entry `index`."""
    kind = cs.entry_kind(mine, section)
    where = f"{section}[{position}] ({_label(mine, section)})"
    for key in cs.MECHANICAL_KEYS[kind]:
        if key == "columns":
            continue
        if key in mine:
            if key not in entry:
                p.missing(f"{where}.{key}", _shown(mine[key]))
            elif entry[key] != mine[key]:
                p.wrong(f"{where}.{key}", entry[key], _shown(mine[key]))
        elif not isinstance(entry.get(key), str) or not entry[key].strip():
            # The scaffold could not derive it (a derivation gap it reported): the analyzer declares it.
            p.bad(entry, key, f"{where}.{key}",
                  "a non-empty string the analyzer declares (the pipeline could not derive it)")
    for key in sorted(cs.FORBIDDEN_KEYS[kind] & set(entry)):
        owner = {"source": "a mapped source, which is read by its logical name",
                 "stream": "an upstream stream, which is read from its table",
                 "work": "a work stream", "target": "a target"}[kind]
        p.wrong(f"{where}.{key}", entry[key], f"no {key} on {owner}")
    accepted = {c: spellings for (s, e, c), spellings in scaffold.also_accepted.items() if (s, e) == (section, index)}
    extras = section == "outputs" and index in scaffold.extras_allowed
    names = _check_columns(p, where, mine.get("columns"), entry.get("columns"), accepted, extras)
    keys = entry.get("keys")
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        p.bad(entry, "keys", f"{where}.keys", "a list of its column names (may be empty)")
    else:
        upper = {n.upper() for n in names}
        for key in keys:
            if key.upper() not in upper:
                p.line(f"{where}.keys names {key}, which is not one of its columns; expected a subset of {_names(names)}")
    if section == "inputs":
        if "expected_rows" in entry:
            rows = entry["expected_rows"]
            ok = (isinstance(rows, dict) and set(rows) == {"min", "max"} and _is_int(rows["min"])
                  and _is_int(rows["max"]) and 0 <= rows["min"] <= rows["max"])
            if not ok:
                p.wrong(f"{where}.expected_rows", rows, ROWS_SHAPE)
        _check_bool(p, entry, "large", f"{where}.large", required=False)


def _check_section(p: _Problems, section: str, contract: dict, scaffold: cs.SegmentScaffold) -> None:
    derived = scaffold.contract[section]
    written = contract.get(section)
    if not isinstance(written, list):
        expected = f"a list of {len(derived)} entries: {_names(_label(e, section) for e in derived)}"
        p.bad(contract, section, section, expected)
        return
    if not written and derived:
        p.line(f"{section} is empty; expected {len(derived)} entries: {_names(_label(e, section) for e in derived)}")
        return
    matched = cs.match_entries(derived, written, section)
    positions = [next(i for i, e in enumerate(written) if e is m) if m is not None else None for m in matched]
    for mine, entry in zip(derived, matched):
        if entry is None:
            p.line(f"{section} has no entry for {_label(mine, section)}; {_expected_entry(mine, section)}")
    used = {i for i in positions if i is not None}
    for index, entry in enumerate(written):
        if index not in used:
            label = _label(entry, section) if isinstance(entry, dict) else _shown(entry)
            p.line(f"{section}[{index}] ({label}) is not an {'input' if section == 'inputs' else 'output'} the DAG "
                   f"shows; expected only: {_names(_label(e, section) for e in derived)}")
    present = [i for i in positions if i is not None]
    if present != sorted(present):
        order = [(_label(written[i], section)) for i in sorted(present)]
        p.line(f"{section} are in the order {', '.join(order)}; expected "
               f"{', '.join(_label(m, section) for m, i in zip(derived, positions) if i is not None)}")
    for index, (mine, position) in enumerate(zip(derived, positions)):
        if position is not None:
            _check_entry(p, section, index, position, written[position], mine, scaffold)


def _output_columns(contract: dict) -> list[str]:
    seen: list[str] = []
    for output in contract.get("outputs") if isinstance(contract.get("outputs"), list) else []:
        for column in (output.get("columns") if isinstance(output, dict) and isinstance(output.get("columns"), list) else []):
            name = str(column.get("name")).upper() if isinstance(column, dict) else None
            if name and name not in seen:
                seen.append(name)
    return seen


def _data_columns(contract: dict, scaffold: cs.SegmentScaffold) -> set[str]:
    seen = set(scaffold.data_columns) | set(_output_columns(contract))
    for entry in contract.get("inputs") if isinstance(contract.get("inputs"), list) else []:
        for column in (entry.get("columns") if isinstance(entry, dict) and isinstance(entry.get("columns"), list) else []):
            if isinstance(column, dict):
                seen.add(str(column.get("name")).upper())
    return seen


def _check_judgment(p: _Problems, seg: str, contract: dict, scaffold: cs.SegmentScaffold) -> None:
    outputs = _output_columns(contract)
    relation = contract.get("row_relation")
    expected = "one of " + ", ".join(f'"{r}"' for r in ROW_RELATIONS)
    if "row_relation" not in contract:
        p.missing("row_relation", expected)
    elif relation not in ROW_RELATIONS:
        p.wrong("row_relation", relation, expected)

    ordering = contract.get("ordering")
    if not isinstance(ordering, dict):
        p.bad(contract, "ordering", "ordering", ORDERING_SHAPE)
    else:
        keys = ordering.get("keys")
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            p.bad(ordering, "keys", "ordering.keys", "a list of column names (may be empty)")
        else:
            data = _data_columns(contract, scaffold)
            for key in keys:
                if key.upper() not in data:
                    p.line(f"ordering.keys names {key}, which is not a column of {seg}; expected a column of its "
                           f"inputs, its tools' anchors or its outputs")
        _check_bool(p, ordering, "alteryx_deterministic", "ordering.alteryx_deterministic")
        dependent = ordering.get("order_dependent_columns")
        if not isinstance(dependent, list) or not all(isinstance(k, str) for k in dependent):
            p.bad(ordering, "order_dependent_columns", "ordering.order_dependent_columns",
                  "a list of output column names (may be empty)")
        else:
            for name in dependent:
                if name.upper() not in outputs:
                    p.line(f"ordering.order_dependent_columns names {name}, which is not an output column; "
                           f"expected one of {_names(outputs)}")
        for key in ordering:
            if key not in ORDERING_FIELDS:
                p.line(f"ordering.{key} is not an ordering field; expected only keys, alteryx_deterministic and "
                       f"order_dependent_columns")

    tolerances = contract.get("tolerances")
    if not isinstance(tolerances, dict):
        p.bad(contract, "tolerances", "tolerances", "an object keyed by output column (may be empty: {})")
    else:
        for name, directive in tolerances.items():
            if str(name).upper() not in outputs:
                p.line(f"tolerances.{name} names no output column; expected one of {_names(outputs)}")
                continue
            ok = (isinstance(directive, dict) and set(directive) <= TOLERANCE_KEYS
                  and all(_is_number(v) and v >= 0 for v in directive.values()))
            if not ok:
                p.wrong(f"tolerances.{name}", directive, TOLERANCE_SHAPE)

    normalizations = contract.get("normalizations")
    if not isinstance(normalizations, list):
        p.bad(contract, "normalizations", "normalizations", NORMALIZATIONS_SHAPE)
    else:
        for index, directive in enumerate(normalizations):
            operation, _, name = str(directive).partition(":") if isinstance(directive, str) else ("", "", "")
            if operation.strip().lower() not in NORMALIZATION_OPS or not name.strip():
                p.wrong(f"normalizations[{index}]", directive, '"trim:<COLUMN>" or "upper:<COLUMN>"')
            elif name.strip().upper() not in outputs:
                p.line(f"normalizations[{index}] names {name.strip()}, which is not an output column; expected one of "
                       f"{_names(outputs)}")

    risks = contract.get("parity_risks")
    shape = f'{{"tool_id": "<a tool of {seg}>", "class": "<diff class>", "note": "<why>"}}'
    tools = sorted(scaffold.tool_ids, key=cs.nested_tool_key)
    if not isinstance(risks, list):
        p.bad(contract, "parity_risks", "parity_risks", f"a list of {shape} (may be empty)")
    else:
        for index, risk in enumerate(risks):
            if not isinstance(risk, dict):
                p.wrong(f"parity_risks[{index}]", risk, shape)
                continue
            tool = risk.get("tool_id")
            if not isinstance(tool, str) or tool not in scaffold.tool_ids:
                p.bad(risk, "tool_id", f"parity_risks[{index}].tool_id", f"a tool of {seg}: {_names(tools)}")
            if risk.get("class") not in DIFF_CLASSES:
                p.bad(risk, "class", f"parity_risks[{index}].class", f"one of {', '.join(DIFF_CLASSES)}")
            note = risk.get("note")
            if not isinstance(note, str) or not note.strip():
                p.bad(risk, "note", f"parity_risks[{index}].note", "a non-empty sentence")
            for key in risk:
                if key not in RISK_FIELDS:
                    p.line(f"parity_risks[{index}].{key} is not a parity-risk field; expected only tool_id, "
                           f"class and note")


def check_contract(wf_id: str, seg: str, contract: dict, scaffold: cs.SegmentScaffold,
                   proposal: str | None) -> list[str]:
    """Every problem of one contract, in a fixed order: identity, target, inputs, outputs, output,
    then the judgment fields."""
    p = _Problems(seg)
    derived = scaffold.contract
    for key in ("workflow", "segment"):
        if key not in contract:
            p.missing(key, _shown(derived[key]))
        elif contract[key] != derived[key]:
            p.wrong(key, contract[key], _shown(derived[key]))

    target = contract.get("target")
    if "target" not in contract:
        p.missing("target", f'"{proposal}" (segments/targets.json) or a lower target' if proposal
                  else "one of " + ", ".join(f'"{t}"' for t in TARGETS))
    elif target not in TARGETS:
        p.wrong("target", target, "one of " + ", ".join(f'"{t}"' for t in TARGETS))
    elif proposal in TARGET_RANK and TARGET_RANK[target] > TARGET_RANK[proposal]:
        p.line(f'target is "{target}", above the proposal "{proposal}" in segments/targets.json; expected '
               f'"{proposal}" or lower (a target may only be lowered: sql -> snowpark -> manual)')

    _check_section(p, "inputs", contract, scaffold)
    _check_section(p, "outputs", contract, scaffold)
    outputs = contract.get("outputs")
    if isinstance(outputs, list) and outputs:
        if "output" not in contract:
            p.missing("output", "an exact copy of outputs[0]")
        elif contract["output"] != outputs[0]:
            p.line("output differs from outputs[0]; expected an exact copy of outputs[0]")

    _check_judgment(p, seg, contract, scaffold)
    return list(p)


def check(repo: Repo, wf_id: str, segments: Sequence[str] | None = None) -> list[str]:
    """Every problem of every contract in scope, segment by segment in order.json order. Raises
    FileNotFoundError without `segments/order.json` / `parsed/dag.json` / `intake/mappings.yaml`,
    and contract_scaffold.UnknownSegment for a `segments` id order.json does not list."""
    return _check(repo, wf_id, segments)[0]


def _check(repo: Repo, wf_id: str, segments: Sequence[str] | None) -> tuple[list[str], int]:
    derivation = cs.derive(repo, wf_id)
    scope = cs.in_scope(derivation, segments)
    targets_file = repo.wf(wf_id, "segments", "targets.json")
    proposals = (read_json(targets_file).get("segments") or {}) if targets_file.is_file() else {}
    problems: list[str] = []
    for seg in scope:
        if not repo.seg(wf_id, seg, "contract.json").is_file():
            problems.append(f"{seg}: contract.json is missing; expected the analyzer's contract for {seg}")
            continue
        try:
            contract = cs.read_contract(repo, wf_id, seg)
        except cs.ContractUnreadable as exc:
            problems.append(f"{exc}; expected a JSON object")
            continue
        proposal = proposals.get(seg)
        problems += check_contract(wf_id, seg, contract, derivation.segments[seg],
                                   proposal if isinstance(proposal, str) else None)
    return problems, len(scope)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id")
    parser.add_argument("--segments", help="comma-separated segments to check (default: every segment of order.json)")
    add_root_arg(parser)
    args = parser.parse_args(argv)
    segments = [s.strip() for s in args.segments.split(",") if s.strip()] if args.segments is not None else None
    if segments == []:
        parser.error("--segments names no segment")                         # exit 2: never "check nothing"

    try:
        problems, count = _check(Repo(args.root), args.wf_id, segments)
    except FileNotFoundError as exc:
        parser.error(f"{args.wf_id} cannot be checked yet: {exc}")         # exit 2
    except cs.UnknownSegment as exc:
        parser.error(str(exc))                                             # exit 2
    except Exception:                                                      # exit 2: a crash, never a verdict
        traceback.print_exc()
        return 2

    if not problems:
        print(f"{args.wf_id}: {count} contract(s) checked, every one passes")
        return 0
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{args.wf_id}: {len(problems)} problems in {count} contract(s) checked")
    return 1


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
