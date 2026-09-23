"""Validate a Snowpark Python segment in the Snowpark Local Testing Framework (spec §5.2).

    validate_snowpark.py <wf_id> <seg> [--set NAME]... [--proc FILE] [--root .]
        [--backend duckdb|snowflake] [--connection NAME] [--sandbox-database DB]

The SQL twin of this script, `validate_segment.py`, drives a translated SQL procedure through a
`DuckDBBackend` and compares its output with `compare.py`. This one drives a Snowpark Python
`proc.py` module's `run()` handler through the Snowpark Local Testing Framework instead -- a real
(if in-memory, unaccounted) Snowpark session, not a double -- and judges it with the exact same
unchanged `compare.py`. Everything about *shaping* the result into `validation.json` (combining
per-output reports, a missing declared table, a domain error, aggregating several golden sets,
clearing a stale report before anything else runs) is identical between the two and lives in
`lib/validation.py`; this module is only the Snowpark-specific "drive a procedure, read its output
back" half. Nothing here has ever run against a real Snowflake account.

**Session-per-run.** Each call to `run_handler` creates a brand-new local-testing `Session`: the
framework keeps its tables in that Session's own in-memory catalog, invisible to any other
Session in the same process (confirmed empirically -- see task-4-report.md), so a fresh session
plays exactly the role a fresh `DuckDBBackend()` plays for the SQL path, including for the
idempotency check's two independent runs.

**Reading a table back.** `session.table(fqn).to_pandas()` is the only way to get a Snowpark
table's rows back into Python here (`session.sql` raises `NotImplementedError` locally, and the
project's rules forbid a procedure from using it anyway). Local testing's pandas round-trip has a
few quirks worth naming because they would otherwise look like data bugs: a nullable integer
column that actually contains a NULL comes back as `float64` (NaN for the null, `1.0` instead of
`1` for a real value) rather than staying an integer column; a `DecimalType` column comes back as
`decimal.Decimal` (or `None`); a `TimestampType` column comes back as `pandas.Timestamp` (or
`NaT`); a `DateType` column comes back as `datetime.date` (or `None`). `lib/handoff.py`'s
`_coerce` undoes all of that, per-column (`_read_back` and `_save` live there since Task W1, as
`handoff.table_from_snowpark` and `handoff.load_into_snowpark`: the chain test crosses the same
seam with the same code).

**`--backend snowflake` (plan Task P2).** Selected only by that flag, run by a human: each run's
session comes from `lib.snowflake_conn.snowpark_session(<connection name>)` -- a real account --
the run's sandbox schemas are replaced in the sandbox database the policy lists, and the golden
set is loaded there; the outputs are read back and judged exactly as below. The two runs share
that database, so the first run's outputs are snapshotted before the second run starts. Never
exercised against a real account (`docs/reference/snowflake-backend.md`).

**The actual table's schema comes from Snowpark, never from the contract (task-4 fix round 1,
findings C1/C2/I3).** `_read_back` reads `session.table(fqn).schema` -- a real `StructType`, in
the table's own column order, including a column the contract never declared and omitting one the
handler failed to write -- and maps each `StructField`'s Snowpark type back to an Alteryx one with
`types_map.snowpark_to_alteryx` (`alteryx_to_snowpark`'s exact inverse). That is what lets
`compare.py`'s own schema check (families, missing columns, extra columns) see what the procedure
*actually* wrote instead of only ever confirming what the contract already expected of it. A cell
(or a column's own type) that cannot be represented as a `typed_csv` value raises `ReadBackError`
-- a domain failure, since it is the procedure's own output that is broken, never a usage error
about a missing prerequisite -- which `_run_one_set` turns into that golden set's FAIL report via
`lib.validation.fail_report`.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import compare
from lib import handoff, validation as v
from lib.backend import DuckDBBackend
from lib.io import load_manifest, read_json, read_yaml
from lib.paths import Repo, add_root_arg
from lib.typed_csv import read_table
from lib.backend import ident, qualified
from load_golden import check_names, golden_view_schema, SANDBOX_DB

WORK_SCHEMA = "MIG_WORK"


#: A cell (or a whole column's own Snowpark type) that cannot be read back as a `typed_csv` value
#: (task-4 fix round 1, finding I3). The class moved to `lib/handoff.py` with `_read_back` (Task W1)
#: and is a `handoff.HandoffError` there; this name stays so every caller and test keeps working.
ReadBackError = handoff.ReadBackError


def _session():
    from snowflake.snowpark import Session  # noqa: PLC0415  (lazy: module stays importable
                                             # without Snowpark installed, same as types_map.py)
    return Session.builder.configs({"local_testing": True}).create()


#: Loading a typed table into a Snowpark session, and reading one back with its REAL schema, moved
#: verbatim to `lib/handoff.py` (Task W1) -- the one typed hand-off `validate_workflow.py` also uses
#: at every Snowpark seam of a chain. Re-exported under their old names.
_save = handoff.load_into_snowpark
_read_back = handoff.table_from_snowpark


def load_set_snowpark(session, repo: Repo, wf_id: str, golden_set: str, *, database: str = SANDBOX_DB) -> dict:
    """Mirror of load_golden.load_set for a Snowpark session: inputs under the golden view schema by
    logical name, targets_before under MIG_WORK, in `database` (`MIGDB` locally, the sandbox
    database on `--backend snowflake`). Returns the run args."""
    mappings = read_yaml(repo.wf(wf_id, "intake", "mappings.yaml")) or {}
    view_schema = golden_view_schema(wf_id, golden_set)
    for key, source in (mappings.get("sources") or {}).items():
        logical = source.get("logical")
        tool_ids = [str(t) for t in source.get("tool_ids") or []]
        if not logical or not tool_ids:
            raise ValueError(f"{wf_id}: source {key!r} has no logical name or tool ids")
        ident(logical, f"source {key!r} logical name")           # a table name (fix round 1, C2)
        path = repo.wf(wf_id, "golden", "inputs", golden_set, f"{tool_ids[0]}.csv")
        _save(session, f"{database}.{view_schema}.{logical}", read_table(path))
    for key, output in (mappings.get("outputs") or {}).items():
        if output.get("mode") in ("append", "merge"):
            ident(output.get("logical"), f"output {key!r} logical name")
            path = repo.wf(wf_id, "golden", "targets_before", golden_set, f"{output['logical']}.csv")
            _save(session, f"{database}.{WORK_SCHEMA}.{output['logical']}", read_table(path))
    return {"SRC_DB": database, "SRC_SCHEMA": view_schema, "TGT_DB": database, "TGT_SCHEMA": WORK_SCHEMA}


def _prepare_schemas(session, database: str, schemas: Sequence[str]) -> None:
    """`--backend snowflake`: the session's database, and every sandbox schema replaced -- empty -- in
    it, exactly what `SnowflakeSandbox.fresh` does through the connector (plan Task P2)."""
    session.sql(f"USE DATABASE {database}").collect()
    for schema in schemas:
        session.sql(f"CREATE OR REPLACE SCHEMA {database}.{schema}").collect()


def _load_module(proc_path: Path):
    """Import `proc_path` as a throwaway module, writing nothing next to it.

    CPython caches a compiled module in a `__pycache__/` directory beside the source by default,
    and `proc_path` is wherever the caller pointed -- for `--proc` that is a committed fixture
    tree (`samples/<wf>/broken_sql/<seg>/`), which a validator has no business writing into. The
    flag is process-global, so the previous value is restored in a `finally` rather than assumed
    to be the default (task-5 fix round 1, controller ruling on M1).
    """
    spec = importlib.util.spec_from_file_location(f"proc_{abs(hash(str(proc_path)))}", proc_path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    finally:
        sys.dont_write_bytecode = previous
    if not callable(getattr(module, "run", None)):
        raise ValueError(f"{proc_path} has no run()")
    return module


#: Public names for the chain test (`validate_workflow.py`), which drives a Snowpark segment itself.
new_local_session = _session
load_module = _load_module


def _prepare_run(repo: Repo, wf_id: str, seg: str, golden_set: str, contract: dict):
    """Phase 1 of `run_handler`: a fresh local-testing Session with the golden set -- and any
    upstream segment's golden intermediate `contract["inputs"]` declares -- loaded into it, plus
    the run args `run()` takes. Raises `FileNotFoundError`/`ValueError` for a missing or malformed
    prerequisite: a *usage* error, exactly like `validate_segment._load_and_run`'s own
    golden-loading half (`load_golden.load_set`/`load_intermediate`) -- kept separate from phase 2
    (actually calling `run()`) so `_run_one_set` can tell the two apart the same way the SQL path
    already does (only a `ProcError`/`BackendError` from *running* the procedure is a domain FAIL;
    a missing golden CSV is not)."""
    session = _session()
    args = load_set_snowpark(session, repo, wf_id, golden_set)
    _load_intermediates(session, repo, wf_id, golden_set, contract)
    return session, args


def _load_intermediates(session, repo: Repo, wf_id: str, golden_set: str, contract: dict) -> None:
    for entry in contract.get("inputs", []):
        if entry.get("stream"):
            qualified(entry.get("table"), f"{wf_id} input table for stream {entry['stream']!r}")
            path = repo.wf(wf_id, "golden", "intermediates", entry["from"], golden_set, f"{entry['stream']}.csv")
            _save(session, entry["table"], read_table(path))


def _prepare_snowflake_run(sandbox, repo: Repo, wf_id: str, golden_set: str, contract: dict):
    """`_prepare_run` on a real account (plan Task P2): a session built from the named connection,
    the sandbox's schemas replaced in its database, the golden set and intermediates loaded there.
    A missing or malformed prerequisite stays a usage error; anything the session raises is
    re-raised with its text redacted."""
    from lib import snowflake_conn, snowflake_sandbox  # noqa: PLC0415  (lazy: never on the local path)
    schemas = [snowflake_sandbox.ident(schema)
               for schema in (*snowflake_sandbox.SANDBOX_SCHEMAS, golden_view_schema(wf_id, golden_set))]
    session = snowflake_conn.snowpark_session(sandbox.connection_name)
    try:
        _prepare_schemas(session, sandbox.database, schemas)
        args = load_set_snowpark(session, repo, wf_id, golden_set, database=sandbox.database)
        _load_intermediates(session, repo, wf_id, golden_set, contract)
    except (FileNotFoundError, ValueError):
        session.close()
        raise
    except Exception as exc:  # noqa: BLE001 -- redacted, never chained
        session.close()
        raise snowflake_conn.scrubbed(exc) from None
    return session, args


def _prepare(repo: Repo, wf_id: str, seg: str, golden_set: str, contract: dict, sandbox):
    """One run's session and args: `_prepare_run` locally, `_prepare_snowflake_run` on a real account."""
    if sandbox is None:
        return _prepare_run(repo, wf_id, seg, golden_set, contract)
    return _prepare_snowflake_run(sandbox, repo, wf_id, golden_set, contract)


def _redacted(exc: Exception, sandbox) -> Exception:
    """What a report may carry of `exc`: itself locally, its redacted text on a real account."""
    if sandbox is None:
        return exc
    from lib import snowflake_conn  # noqa: PLC0415
    return snowflake_conn.scrubbed(exc)


def run_handler(repo: Repo, wf_id: str, seg: str, golden_set: str, contract: dict, proc_path: Path,
                run_id: str):
    """Loads one golden set into a fresh local-testing Session and runs `proc.py`'s `run()`
    against it end to end, returning that Session so the caller can read the outputs back.
    `contract` is needed (beyond the brief's abbreviated `(repo, wf_id, seg, golden_set, proc_path,
    run_id)` Interfaces line) for the same reason `validate_segment._load_and_run` needs it: an
    upstream segment's golden intermediate has to be loaded before the procedure runs. A standalone
    entry point for anyone who wants the whole "load and run" bundle in one call (also what a
    literal reading of `_prepare_run` + `run()` looks like) -- `_run_one_set` below calls the two
    phases separately instead, so it can classify a failure in each phase differently."""
    session, args = _prepare_run(repo, wf_id, seg, golden_set, contract)
    module = _load_module(proc_path)
    module.run(session, args["SRC_DB"], args["SRC_SCHEMA"], args["TGT_DB"], args["TGT_SCHEMA"], run_id)
    return session


def _snapshot(session, fqns: Sequence[str]) -> dict:
    """Every table's rows as a multiset (`Counter` of `_read_back`'s already-typed rows), `None` for
    one that is missing or unreadable (a `ReadBackError`). There is no SQL `ORDER BY` to lean on
    here (no `session.sql`, by design), and `Counter` hashes rather than orders, so it never trips
    over comparing `None` against a value the way `sorted()` would. Taken from the first run BEFORE
    the second run starts: on a real account both share one sandbox database, whose schemas the
    second run replaces (plan Task P2)."""
    from collections import Counter
    rows: dict = {}
    for fqn in dict.fromkeys(fqns):
        try:
            table = _read_back(session, fqn)
        except ReadBackError:
            table = None
        rows[fqn] = None if table is None else Counter(map(tuple, table["rows"]))
    return rows


def _outputs_equal(first: dict, second: dict, fqns: list[str]) -> tuple[bool, list[str]]:
    """Row-multiset equality of every declared output table between two runs' `_snapshot`s -- the
    Snowpark twin of `validate_segment._outputs_equal`. A table missing (or unreadable) on either
    side counts as diverging, same as the SQL path."""
    diverging = v.diverging_tables(first, second, fqns)
    return not diverging, diverging


def _run_one_set(repo: Repo, wf_id: str, seg: str, golden_set: str, contract: dict, proc_path: Path,
                 tolerances: dict, accepted_classes: Sequence[str], approvals: Sequence[dict],
                 segment_dag: dict | None, *, check_idempotency: bool, sandbox=None) -> dict:
    started = time.perf_counter()
    run_id = f"validate_{wf_id}_{seg}_{golden_set}"
    outputs = contract.get("outputs") or []
    database = SANDBOX_DB if sandbox is None else sandbox.database
    fqns = [v.actual_table(wf_id, seg, output, database) for output in outputs]
    # Loading the golden set (a missing/malformed prerequisite) is a usage error and propagates
    # uncaught, exactly like validate_segment._load_and_run's own golden-loading half; only a
    # failure from actually running the procedure is a domain FAIL.
    session, args = _prepare(repo, wf_id, seg, golden_set, contract, sandbox)
    try:
        try:
            module = _load_module(proc_path)
            module.run(session, args["SRC_DB"], args["SRC_SCHEMA"], args["TGT_DB"], args["TGT_SCHEMA"],
                      run_id)
        except Exception as exc:  # noqa: BLE001 -- any exception from proc.py's run() is a domain FAIL
            report = v.fail_report(contract, golden_set, _redacted(exc, sandbox))
            report["runtime_ms"] = int(round((time.perf_counter() - started) * 1000))
            return report

        output_reports: list[tuple[dict, dict]] = []
        compare_backend = DuckDBBackend()
        try:
            for index, output in enumerate(outputs):
                actual_fqn = fqns[index]
                try:
                    actual = _read_back(session, actual_fqn)
                except ReadBackError as exc:
                    # A cell (or column type) the actual table's own declared type cannot
                    # represent (finding I3): a domain FAIL naming exactly where, not a usage
                    # error -- same treatment as proc.py's run() raising, above.
                    report = v.fail_report(contract, golden_set, exc)
                    report["runtime_ms"] = int(round((time.perf_counter() - started) * 1000))
                    return report
                if actual is None:
                    # The procedure never created this declared output table (finding C4(a)): an
                    # *actual*-side problem is a domain FAIL, not compare.py's usage-error.
                    output_report = v.missing_table_report(actual_fqn)
                else:
                    expected = read_table(v.golden_path(repo, wf_id, seg, golden_set, output))
                    expected_local = v.expected_fqn(index)
                    actual_local = f"{v.EXPECTED_SCHEMA}.ACTUAL_{index}"
                    compare_backend.load_table(expected_local, expected)
                    compare_backend.load_table(actual_local, actual)
                    output_report = compare.compare(
                        compare_backend, expected_local, actual_local, contract, tolerances,
                        output=output, accepted_classes=accepted_classes, approvals=approvals,
                        segment_dag=segment_dag, golden_set=golden_set)
                output_reports.append((output, output_report))
            report = v.combine(contract, golden_set, output_reports)
        finally:
            compare_backend.close()

        if check_idempotency:
            # Same phase split as the primary run above: loading the golden set again is not
            # caught here either (a real prerequisite failure on the second run is as genuine a
            # crash as it would be on the first), only a failure from running the procedure a
            # second time is.
            first = _snapshot(session, fqns)          # before a second sandbox can replace it
            other_session, other_args = _prepare(repo, wf_id, seg, golden_set, contract, sandbox)
            try:
                try:
                    other_module = _load_module(proc_path)
                    other_module.run(other_session, other_args["SRC_DB"], other_args["SRC_SCHEMA"],
                                     other_args["TGT_DB"], other_args["TGT_SCHEMA"], run_id)
                except Exception:  # noqa: BLE001 -- the second run itself could not complete
                    # Idempotency can never be verified as True without both runs actually being
                    # compared (finding K2's ruling), and here it did not even run -- every
                    # declared output counts as diverging.
                    idempotent, diverging = False, list(fqns)
                else:
                    idempotent, diverging = _outputs_equal(first, _snapshot(other_session, fqns), fqns)
            finally:
                other_session.close()
            report["idempotent"] = idempotent
            report["idempotency_diff"] = diverging
            if not idempotent:
                # Finding K2: a failed idempotency check FAILS validation, whatever compare()
                # said about this run's own output.
                report["verdict"] = v.worst_verdict([report["verdict"], "FAIL"])

        report["runtime_ms"] = int(round((time.perf_counter() - started) * 1000))
        return report
    finally:
        session.close()


def validate_snowpark(repo: Repo, wf_id: str, seg: str, golden_sets: Sequence[str] | None = None, *,
                      proc_path: Path | None = None, backend: str = "duckdb", connection: str | None = None,
                      sandbox_database: str | None = None) -> dict:
    """Validates one segment's Snowpark procedure against every named golden set (default:
    `manifest.golden_sets`) -- the Snowpark twin of `validate_segment.validate_segment`, same
    contract: before even the prerequisite checks, every `validation*.json` already on disk for
    this segment is deleted (findings C4(b)/I5), so a usage error leaves no stale report behind;
    every prerequisite (`contract.json`, the procedure module, `intake/mappings.yaml`, a non-empty
    `contract.outputs[]`, a non-empty set of golden sets) is checked, and every golden set is
    fully processed, before anything is written. Adds `"target": "snowpark"` to the top-level
    report the way `compile_check.py --target snowpark` does for a compile report.

    `backend="snowflake"` (plan Task P2): every run's session comes from
    `snowflake_conn.snowpark_session(<connection>)`, its sandbox schemas are replaced in the
    sandbox database (`_prepare_schemas`) and the golden set is loaded there; the outputs are read
    back and judged exactly as locally.
    """
    v.clear_stale_reports(repo, wf_id, seg)
    sandbox = v.snowflake_target(repo, backend, connection, sandbox_database)

    contract_path = repo.seg(wf_id, seg, "contract.json")
    if not contract_path.is_file():
        raise FileNotFoundError(f"{wf_id}/{seg} has no contract.json to validate against: "
                                f"{contract_path}; translate this segment first")
    contract = read_json(contract_path)
    if not contract.get("outputs"):
        raise ValueError(f"{wf_id}/{seg} contract.json has no outputs[] to validate (plan "
                         f"contract C5 requires at least one); translate this segment first")

    proc_file = Path(proc_path) if proc_path is not None else repo.seg(wf_id, seg, "proc.py")
    if not proc_file.is_file():
        raise FileNotFoundError(f"{wf_id}/{seg} has no procedure to validate: {proc_file}; "
                                f"translate this segment first (or pass --proc)")

    mappings_path = repo.wf(wf_id, "intake", "mappings.yaml")
    if not mappings_path.is_file():
        raise FileNotFoundError(f"{wf_id} has no intake/mappings.yaml to load golden data "
                                f"against: {mappings_path}; run intake first: "
                                f"python scripts/intake_prompt.py {wf_id}")

    dag_path = repo.seg(wf_id, seg, "dag.json")
    segment_dag = read_json(dag_path) if dag_path.is_file() else None

    manifest = load_manifest(repo, wf_id)
    sets = list(golden_sets) if golden_sets is not None else list(manifest.get("golden_sets") or [])
    if not sets:
        raise ValueError(f"{wf_id} has no golden sets to validate {seg} against (manifest.json's "
                         f"golden_sets is empty and none were given with --set); run "
                         f"dev/build_samples.py or inject_outputs.py first")

    settings = read_yaml(repo.global_mappings) if repo.global_mappings.is_file() else {}
    settings = settings or {}
    tolerances = settings.get("tolerances") or compare.DEFAULT_TOLERANCES
    accepted_classes = settings.get("accepted_diff_classes") or ()
    approvals = [a for a in (manifest.get("accepted_diffs") or []) if a.get("segment") == seg]

    # Every name used as a Snowpark table name, checked before any session exists (fix round 1, C2).
    database = SANDBOX_DB if sandbox is None else sandbox.database
    v.check_contract_names(wf_id, seg, contract, database)
    check_names(repo, wf_id, sets, database=database)

    reports: dict[str, dict] = {}
    idempotent: bool | None = None
    idempotency_diff: list[str] = []
    for index, golden_set in enumerate(sets):
        check = index == 0
        report = _run_one_set(repo, wf_id, seg, golden_set, contract, proc_file, tolerances,
                              accepted_classes, approvals, segment_dag, check_idempotency=check, sandbox=sandbox)
        if check:
            idempotent = report["idempotent"]
            idempotency_diff = report["idempotency_diff"]
        reports[golden_set] = report

    return v.write_reports(repo, wf_id, seg, sets, reports, (idempotent, idempotency_diff),
                           extra={"target": "snowpark"})


# --- CLI --------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0009")
    parser.add_argument("seg", help="segment id, e.g. seg_01")
    parser.add_argument("--set", dest="sets", action="append", default=None,
                        help="golden set to validate (repeatable); default: manifest.golden_sets")
    parser.add_argument("--proc", type=Path, default=None,
                        help="procedure file to validate (default: segments/<seg>/proc.py)")
    v.add_backend_args(parser)
    add_root_arg(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo = Repo(args.root)
    try:
        report = validate_snowpark(repo, args.wf_id, args.seg, args.sets, proc_path=args.proc,
                                   backend=args.backend, connection=args.connection,
                                   sandbox_database=args.sandbox_database)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(v.error_text(args.backend, str(exc)))  # exit 2: a missing prerequisite; nothing was written
    except Exception:            # noqa: BLE001 -- exit 2 is "anything unexpected"; never a bare traceback exit 1
        v.print_crash(args.backend)
        return 2

    print(f"{args.wf_id}/{args.seg}: {report['verdict']} {report['sets']} "
         f"(idempotent={report['idempotent']})")
    return 0 if report["verdict"].startswith("PASS") else 1


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
