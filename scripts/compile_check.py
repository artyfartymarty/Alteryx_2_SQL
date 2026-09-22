"""Does a segment's procedure compile and run against tables shaped like its contract?

    python scripts/compile_check.py <wf_id> <seg> [--root .]

This is the cheap gate before parity: it catches a procedure that is not valid Snowflake SQL, that
reads a table nobody mapped, or that produces different columns from the ones `contract.json`
promises — without any golden data. The run happens on an empty in-memory sandbox, so it says
nothing about values; `compare.py` does that.

Six checks, in order:

0. the signature is contract C4's: `MIG_WORK.<WF>_<SEG>` for this workflow and segment, exactly
   `(SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA, RUN_ID)` in that order, and `EXECUTE AS CALLER` —
   a procedure that runs with owner's rights cannot set the session parameters these procedures
   rely on. Nothing else is checked when the signature is wrong: the call would not bind.
1. the text is the supported procedure shape (plan contract C4);
2. every statement parses with sqlglot's Snowflake parser and is not a `Command` fallback, which
   is what sqlglot produces when it does not actually understand a statement;
3. empty tables are created for each `contract.inputs[]` — mapped sources under
   `MIGDB.MIG_COMPILE`, upstream segment tables at the literal name the contract gives — and for
   each `outputs[]` of kind `target` under `MIGDB.MIG_WORK`;
4. the procedure runs against them;
5. each `outputs[]` of kind `work` exists afterwards with the contract's column names, compared
   case-insensitively and in order.

Writes `segments/<seg>/compile_check.json`. Exit 0 on OK, 1 on a compile error, 2 when there is
nothing to check. Nothing here has run on a Snowflake account: passing means DuckDB accepted the
translated SQL.
"""
from __future__ import annotations

import argparse
import re
import sys
import traceback

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from lib.backend import SANDBOX_DB, BackendError, DuckDBBackend
from lib.io import read_json, write_json
from lib.paths import Repo, add_root_arg, seg_token, wf_token
from lib.proc_runner import ProcError, ProcInfo, bind, parse_proc, run_proc

COMPILE_SCHEMA = "MIG_COMPILE"
WORK_SCHEMA = "MIG_WORK"
ARGS = {"SRC_DB": SANDBOX_DB, "SRC_SCHEMA": COMPILE_SCHEMA,
        "TGT_DB": SANDBOX_DB, "TGT_SCHEMA": WORK_SCHEMA, "RUN_ID": "compile_check"}

# Contract C4's procedure signature.
C4_PARAMS = ("SRC_DB", "SRC_SCHEMA", "TGT_DB", "TGT_SCHEMA", "RUN_ID")
C4_EXECUTE_AS = "CALLER"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# Presence only — proc_runner reads the value. This tells "EXECUTE AS OWNER was written" from
# "the clause is missing", which both reach us as ProcInfo.execute_as == "OWNER".
_EXECUTE_AS_CLAUSE_RE = re.compile(r"\bEXECUTE\s+AS\b", re.IGNORECASE)


def compile_check(repo: Repo, wf_id: str, seg: str) -> dict:
    """Returns {"status": "OK"|"ERROR", "errors": [str], "statements": int} and writes it."""
    proc_path = repo.seg(wf_id, seg, "proc.sql")
    contract_path = repo.seg(wf_id, seg, "contract.json")
    for path, what in ((proc_path, "procedure"), (contract_path, "contract")):
        if not path.exists():
            raise FileNotFoundError(f"{wf_id}/{seg} has no {what} to check: {path}")
    report = _check(proc_path.read_text(encoding="utf-8"), read_json(contract_path), wf_id, seg)
    write_json(repo.seg(wf_id, seg, "compile_check.json"), report)
    return report


def _check(sql_text: str, contract: dict, wf_id: str, seg: str) -> dict:
    errors: list[str] = []
    try:
        proc = parse_proc(sql_text)
    except ProcError as exc:
        return {"status": "ERROR", "errors": [_clean(str(exc))], "statements": 0}

    signature = _signature_errors(proc, sql_text, wf_id, seg)
    if signature:
        # A wrong signature is not something the later checks can work around: the call would not
        # bind, so anything they reported would be a consequence, not a second problem.
        return {"status": "ERROR", "errors": signature, "statements": len(proc.statements)}

    bound = []
    for statement in proc.statements:
        try:
            bound.append(bind(statement, ARGS))
        except ProcError as exc:
            errors.append(_clean(str(exc)))
    if len(bound) == len(proc.statements):
        errors.extend(_parse_errors(bound))

    if not errors:
        errors.extend(_run_errors(sql_text, contract))
    return {"status": "ERROR" if errors else "OK", "errors": errors,
            "statements": len(proc.statements)}


def _signature_errors(proc: ProcInfo, sql_text: str, wf_id: str, seg: str) -> list[str]:
    """Check 0: the procedure is the one contract C4 says this segment must expose."""
    errors = []
    expected_name = f"{WORK_SCHEMA}.{wf_token(wf_id)}_{seg_token(seg)}"
    if proc.name.replace('"', "").upper() != expected_name:
        errors.append(f"procedure {proc.name} found; contract C4 requires {expected_name} "
                      f"for {wf_id}/{seg}")
    if tuple(param.upper() for param in proc.params) != C4_PARAMS:
        errors.append(f"parameters ({', '.join(proc.params)}) found; contract C4 requires "
                      f"({', '.join(C4_PARAMS)}) in that order")
    if proc.execute_as != C4_EXECUTE_AS:
        header = sql_text.split("$$", 1)[0]
        found = (f"EXECUTE AS {proc.execute_as} found" if _EXECUTE_AS_CLAUSE_RE.search(header)
                 else "no EXECUTE AS clause found (Snowflake defaults to owner's rights)")
        errors.append(f"{found}; contract C4 requires EXECUTE AS {C4_EXECUTE_AS}, because a "
                      f"procedure running with owner's rights cannot ALTER SESSION")
    return errors


def _parse_errors(statements: list[str]) -> list[str]:
    errors = []
    for statement in statements:
        try:
            tree = sqlglot.parse_one(statement, read="snowflake")
        except SqlglotError as exc:
            errors.append(_clean(f"not valid Snowflake SQL: {exc}\n{statement.strip()}"))
            continue
        if tree is None:
            errors.append(_clean(f"empty statement: {statement.strip()}"))
            continue
        command = next(tree.find_all(exp.Command), None)
        if command is not None:
            errors.append(_clean("the Snowflake parser does not understand this statement and fell "
                                 f"back to a raw command: {command.sql(dialect='snowflake')}"))
    return errors


def _run_errors(sql_text: str, contract: dict) -> list[str]:
    errors: list[str] = []
    outputs = contract.get("outputs") or ([contract["output"]] if contract.get("output") else [])
    backend = DuckDBBackend()
    try:
        for fqn, columns in _fixtures(contract, outputs, errors):
            try:
                _create_empty(backend, fqn, columns)
            except (BackendError, ValueError) as exc:
                errors.append(_clean(f"cannot build a contract-shaped table {fqn}: {exc}"))
        if errors:
            return errors
        try:
            run_proc(backend, sql_text, ARGS)
        except (BackendError, ProcError) as exc:
            return [_clean(str(exc))]
        errors.extend(_output_errors(backend, outputs))
    finally:
        backend.close()
    return errors


def _fixtures(contract: dict, outputs: list[dict], errors: list[str]):
    """(fqn, columns) for every table the procedure expects to find already there."""
    for source in contract.get("inputs") or []:
        logical = source.get("logical")
        fqn = f"{SANDBOX_DB}.{COMPILE_SCHEMA}.{logical}" if logical else source.get("table")
        if not fqn:
            errors.append(f"contract input {source!r} has neither a `logical` name nor a `table`")
            continue
        yield fqn, source.get("columns") or []
    for output in outputs:
        if output.get("kind") != "target":
            continue
        logical = output.get("logical")
        if not logical:
            errors.append(f"contract output {output.get('stream')!r} is a target with no `logical` name")
            continue
        yield f"{SANDBOX_DB}.{WORK_SCHEMA}.{logical}", output.get("columns") or []


def _create_empty(backend: DuckDBBackend, fqn: str, columns: list[dict]) -> None:
    if not columns:
        raise ValueError("the contract lists no columns for it")
    declarations = ", ".join(f'"{column["name"]}" {column["type"]}' for column in columns)
    backend.execute(f"CREATE OR REPLACE TABLE {fqn} ({declarations})")


def _output_errors(backend: DuckDBBackend, outputs: list[dict]) -> list[str]:
    errors = []
    for output in outputs:
        if output.get("kind") != "work":
            continue
        table = output.get("table")
        if not table:
            errors.append(f"contract output {output.get('stream')!r} is a work table with no `table` name")
            continue
        if not backend.table_exists(table):
            errors.append(f"{table}: the procedure did not create this output table")
            continue
        actual = [column["name"].upper() for column in backend.table_columns(table)]
        expected = [column["name"].upper() for column in output.get("columns") or []]
        if actual != expected:
            errors.append(f"{table}: produces columns {actual} but the contract promises {expected}")
    return errors


def _clean(text: str) -> str:
    """sqlglot underlines the offending token with ANSI codes; they do not belong in JSON."""
    return _ANSI_RE.sub("", text)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0003")
    parser.add_argument("seg", help="segment id, e.g. seg_01")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    try:
        report = compile_check(Repo(args.root), args.wf_id, args.seg)
    except (FileNotFoundError, KeyError) as exc:
        print(f"cannot compile-check {args.wf_id}/{args.seg}: {exc}", file=sys.stderr)
        return 2
    except Exception:  # exit 2: a crash outside the checks above, which leaves no report either
        traceback.print_exc()
        return 2
    print(f"{report['status']}: {report['statements']} statements, {len(report['errors'])} errors")
    for error in report["errors"]:
        print(f"  {error}", file=sys.stderr)
    return 0 if report["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
