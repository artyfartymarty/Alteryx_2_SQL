"""Segment parity validation: loads golden data, runs a translated procedure, compares every
output, checks determinism, and writes `validation.json` (program spec §9, plan task 14).

    python scripts/validate_segment.py <wf_id> <seg> [--set NAME]... [--proc FILE] [--root .]
        [--backend duckdb|snowflake] [--connection NAME] [--sandbox-database DB]

This is the deterministic core the `validator` agent (and the orchestrator's mock) run exactly as
written -- every parity verdict in the project flows through this module, and it never invents a
number or a verdict: every count, diff and classification comes from `compare.py`. For each golden
set this script loads a *fresh in-memory* `DuckDBBackend` (contract C3's local stand-in for
Snowflake), loads the golden set (`load_golden.load_set`), loads any upstream-segment intermediate
this segment's contract declares (`load_golden.load_intermediate`), runs the segment's procedure
(`lib.proc_runner.run_proc`), and compares every `contract.outputs[]` entry against its golden file
with `compare.compare`. Nothing here has ever run against a real Snowflake account or Alteryx.

**Aggregating outputs into one report per golden set.** A segment can have several outputs (a
`work` stream feeding the next segment, one or more `target` tables from Output Data tools that
terminate in this segment). Each is compared independently -- `compare.py`'s own verdict rule is
never re-derived or overridden -- and their reports are combined into one report for that golden
set: the verdict is the worst of the per-output verdicts (`FAIL` if any is `FAIL`, else
`PASS_WITH_ACCEPTED_DIFF` if any is that, else `PASS`), `needs_human` is true if any output's report
says so, and every diff cluster is carried over with a `"stream"` key added so a reader can tell
which output it came from.

**Idempotency (design ruling, not "run twice without resetting").** An Append output legitimately
doubles if the same procedure runs twice against the same starting state without a reset in
between, so that can never be the test. Instead, for the *first* golden set only, this module runs
the procedure twice, each time from scratch in its own fresh in-memory backend loaded from the same
starting state, and calls it idempotent when every output table holds the same rows -- as a
multiset, order does not matter -- both times. That is a real test of determinism: it catches a
non-deterministic function (`RANDOM()`, `UNIFORM(...)`) without being fooled by legitimate
Append-mode accumulation. The result applies to the whole segment, not just that one golden set, so
every set's report (and the top-level `validation.json`) carries the same `idempotent` value.

**Errors.** A `ProcError`/`BackendError` while running the procedure (a compile error, e.g. a SQL
syntax mistake, or a runtime error) is a *domain* failure: it means the SQL is wrong, which is
exactly what feeds the fixer loop, so that golden set's report becomes
`{"verdict": "FAIL", "error": "<message>", "diff_clusters": [], ...}` and processing continues (exit
1, and the report is still written). Likewise, a declared `contract.outputs[]` table the procedure
never created is an *actual*-side domain failure, not compare.py's own usage-error `ValueError` --
this module checks for the table itself before calling `compare.compare()`, and turns a missing one
into that output's own FAIL report naming the table. A missing `contract.json`, procedure file,
`intake/mappings.yaml`, an *empty* `contract.outputs[]` (nothing to validate at all), a missing
golden CSV/schema sidecar, or an unknown workflow/segment/golden-set name is a *usage* error: the
exception (`FileNotFoundError`/`ValueError`) propagates so the CLI can report it with
`parser.error(...)` (exit 2). Every prerequisite is checked, and every golden set is fully
processed in memory, before anything is written to disk.

**A report on disk always belongs to the most recent invocation.** Before anything else runs --
before even the prerequisite checks -- `validate_segment()` deletes this segment's
`validation.json` and every `validation.<set>.json` already on disk. So a usage error, an unknown
segment, or any other crash leaves *no* report at all (the orchestrator's stage gate reads
`validation.json`'s `verdict`/`needs_human` only if the file exists at all -- see
`orchestrator/stages.ts` -- so "no file" cleanly means NEEDS_HUMAN rather than a stale verdict from
an earlier run), and a golden set that is no longer requested loses its own leftover
`validation.<set>.json` too.

**Idempotency failure fails validation.** When the two runs of the first golden set disagree on any
output table's contents -- or the second run cannot even complete -- that first set's own report is
forced to `FAIL` (whatever `compare()` said) and gets an `idempotency_diff`: the output table names
that could not be shown to be idempotent (names only, never invented counts; a table missing on
either run counts as diverging, since idempotency can never be verified, let alone true, without
both runs actually being compared). `idempotent` stays a bool (`None` only when the *first* run
itself failed to complete, so no second run was even attempted) and `idempotency_diff` is `[]`
exactly when `idempotent` is `True`; both are carried onto every golden set's report, same as
`idempotent` always was.

**`--backend snowflake` (plan Task P2).** Selected only by that flag, run by a human: each run gets a
fresh sandbox in a sandbox database the policy lists (`lib.snowflake_sandbox`), reached through a
NAMED connection (`lib.snowflake_conn`); the golden set is loaded there, the procedure is created
and CALLed there, and every output is judged there with the same `compare.py` into the same report.
The two runs share that database, so the first run's outputs are snapshotted before the second
run's fresh sandbox replaces them. Never exercised against a real account
(`docs/reference/snowflake-backend.md`).

**The top-level `validation.json`.** The verdict is the *worst* across every processed golden set
(`FAIL` > `PASS_WITH_ACCEPTED_DIFF` > `PASS`), not merely "the first non-passing set" -- a later
set can still be worse than an earlier one that already wasn't a plain PASS. The body (every other
key) is copied from the first golden set, in processing order, whose own verdict equals that worst
verdict. `needs_human` is true if *any* processed set's own report says so, regardless of which
set's report supplied the body. `sets` always lists every processed set's own verdict. The CLI's
exit code follows this same top-level verdict.
"""
from __future__ import annotations

import argparse
import sys
import time
from functools import partial
from pathlib import Path
from typing import Callable, Sequence

import compare
from lib import typed_csv
from lib.backend import SANDBOX_DB, BackendError, DuckDBBackend, SnowflakeBackend
from lib.io import load_manifest, read_json, read_yaml
from lib.paths import Repo, add_root_arg
from lib.proc_runner import ProcError, run_proc
from lib.validation import (
    VERDICT_SEVERITY, add_backend_args, aggregate_sets, actual_table, check_contract_names, clear_stale_reports,
    combine, diverging_tables, error_text, expected_fqn, fail_report, golden_path, missing_table_report,
    ordered_rows, print_crash, snapshot, snowflake_target, worst_verdict, write_reports,
)
from load_golden import check_names, golden_view_schema, load_intermediate, load_set

# The report-shaping helpers below moved to `lib/validation.py` (shared with `validate_snowpark.py`)
# and are re-exported here under their old private names so this module's own tests -- which
# monkeypatch a couple of them directly -- keep working unchanged.
_VERDICT_SEVERITY = VERDICT_SEVERITY
_worst_verdict = worst_verdict
_expected_fqn = expected_fqn
_missing_table_report = missing_table_report
_combine = combine
_fail_report = fail_report
_aggregate_sets = aggregate_sets
_clear_stale_reports = clear_stale_reports
_actual_table = actual_table
_golden_path = golden_path
_read_ordered_rows = ordered_rows


# --- one full run: load golden data, load upstream intermediates, run the procedure -------------


def _load_and_run(backend, repo: Repo, wf_id: str, seg: str, golden_set: str,
                  contract: dict, proc_sql: str, run_id: str, database: str = SANDBOX_DB) -> None:
    """One complete run against a fresh backend: golden set, any upstream-segment intermediate the
    contract declares, then the procedure. Raises `FileNotFoundError`/`ValueError` for a missing or
    malformed prerequisite (usage error) and lets `ProcError`/`BackendError` from `run_proc`
    propagate for the caller to turn into a domain FAIL. On a real account (`SnowflakeBackend`, plan
    Task P2) the procedure is created and CALLed there (`call_procedure`) instead of being run
    statement by statement, with the golden set loaded into the sandbox `database`.
    """
    info = load_set(backend, repo, wf_id, golden_set, database=database)
    for entry in contract.get("inputs") or []:
        stream = entry.get("stream")
        if not stream:
            continue          # a mapped source (has `logical`), already loaded by load_set above
        upstream = entry.get("from")
        table = entry.get("table")
        if not upstream or not table:
            raise ValueError(f"{wf_id}/{seg} contract input with stream {stream!r} needs both "
                             f"`from` (the upstream segment) and `table` to load its golden "
                             f"intermediate (plan contract C5)")
        load_intermediate(backend, repo, wf_id, upstream, golden_set, stream, table)
    args = dict(info["args"])
    args["RUN_ID"] = run_id
    if isinstance(backend, SnowflakeBackend):
        backend.call_procedure(proc_sql, args)
    else:
        run_proc(backend, proc_sql, args)


def _outputs_equal(first: dict, second: dict, tables: list[str]) -> tuple[bool, list[str]]:
    """Row-multiset equality of every declared output table between two runs' snapshots
    (`lib.validation.snapshot`). Returns `(idempotent, diverging_table_names)`. A table missing on
    either side is never treated as equal -- idempotency cannot be verified for it, so it counts as
    diverging like any other mismatch (findings K2/C4(a): "never true" without a genuine comparison
    of both runs)."""
    diverging = diverging_tables(first, second, tables)
    return not diverging, diverging


# --- one golden set -------------------------------------------------------------------------------


def _run_one_set(repo: Repo, wf_id: str, seg: str, golden_set: str, contract: dict, proc_sql: str,
                 tolerances: dict, accepted_classes: Sequence[str], approvals: Sequence[dict],
                 segment_dag: dict | None, *, check_idempotency: bool,
                 backend_factory: Callable[[], object] = DuckDBBackend, database: str = SANDBOX_DB) -> dict:
    """One golden set. `backend_factory()` gives each run its own fresh backend -- a new in-memory
    `DuckDBBackend` locally, `SnowflakeSandbox.fresh` on a real account (plan Task P2), where both
    runs share one sandbox database: so the first run's outputs are snapshotted before the second
    run's fresh sandbox replaces them."""
    started = time.perf_counter()
    run_id = f"validate_{wf_id}_{seg}_{golden_set}"
    outputs = contract.get("outputs") or []
    tables = [_actual_table(wf_id, seg, output, database) for output in outputs]
    backend = backend_factory()
    try:
        try:
            _load_and_run(backend, repo, wf_id, seg, golden_set, contract, proc_sql, run_id, database)
        except (ProcError, BackendError) as exc:
            report = _fail_report(contract, golden_set, exc)
            report["runtime_ms"] = int(round((time.perf_counter() - started) * 1000))
            return report

        output_reports: list[tuple[dict, dict]] = []
        for index, output in enumerate(outputs):
            expected_fqn = _expected_fqn(index)
            golden_path = _golden_path(repo, wf_id, seg, golden_set, output)
            backend.load_table(expected_fqn, typed_csv.read_table(golden_path))
            actual_fqn = tables[index]
            if backend.table_exists(actual_fqn):
                output_report = compare.compare(
                    backend, expected_fqn, actual_fqn, contract, tolerances, output=output,
                    accepted_classes=accepted_classes, approvals=approvals,
                    segment_dag=segment_dag, golden_set=golden_set)
            else:
                # Finding C4(a): the procedure never created this declared output table. An
                # *actual*-side problem is a domain FAIL, not compare.py's usage-error
                # ValueError -- checked here, before compare() ever gets a chance to raise it.
                output_report = _missing_table_report(actual_fqn)
            output_reports.append((output, output_report))

        report = _combine(contract, golden_set, output_reports)

        if check_idempotency:
            first = snapshot(backend, tables)        # before a second fresh sandbox can replace it
            other = backend_factory()
            try:
                try:
                    _load_and_run(other, repo, wf_id, seg, golden_set, contract, proc_sql, run_id, database)
                except (ProcError, BackendError):
                    # The second run itself could not complete: idempotency can never be verified
                    # as True without both runs actually being compared (finding K2's ruling), and
                    # here it did not even run -- every declared output counts as diverging.
                    idempotent, diverging = False, list(tables)
                else:
                    idempotent, diverging = _outputs_equal(first, snapshot(other, tables), tables)
            finally:
                other.close()
            report["idempotent"] = idempotent
            report["idempotency_diff"] = diverging
            if not idempotent:
                # Finding K2: a failed idempotency check FAILS validation, whatever compare()
                # said about this run's own output.
                report["verdict"] = _worst_verdict([report["verdict"], "FAIL"])

        report["runtime_ms"] = int(round((time.perf_counter() - started) * 1000))
        return report
    finally:
        backend.close()


# --- the whole segment -----------------------------------------------------------------------------


def validate_segment(repo: Repo, wf_id: str, seg: str, golden_sets: Sequence[str] | None = None, *,
                     proc_path: Path | None = None, backend: str = "duckdb", connection: str | None = None,
                     sandbox_database: str | None = None) -> dict:
    """Validates one segment's procedure against every named golden set (default:
    `manifest.golden_sets`), writes `segments/<seg>/validation.<set>.json` for each and
    `segments/<seg>/validation.json` for the segment as a whole, and returns that top-level report.

    Before anything else -- before even the prerequisite checks -- this deletes every
    `validation*.json` already on disk for this segment (findings C4(b)/I5), so a report on disk
    always belongs to the most recent invocation: a missing prerequisite raises
    `FileNotFoundError`/`ValueError` (a usage error for the CLI) and leaves *no* report behind, not
    a stale one from an earlier run. Every prerequisite -- `contract.json`, the procedure file,
    `intake/mappings.yaml`, a non-empty `contract.outputs[]`, a non-empty set of golden sets -- is
    checked, and every golden set is fully processed in memory, before anything is written.

    `backend="snowflake"` (plan Task P2) runs each run in a fresh sandbox on a real account --
    `connection` names the `connections.toml` entry, `sandbox_database` a database the policy lists
    -- with the same judging and the same report; both are checked before anything connects.
    """
    _clear_stale_reports(repo, wf_id, seg)
    sandbox = snowflake_target(repo, backend, connection, sandbox_database)

    contract_path = repo.seg(wf_id, seg, "contract.json")
    if not contract_path.is_file():
        raise FileNotFoundError(f"{wf_id}/{seg} has no contract.json to validate against: "
                                f"{contract_path}; translate this segment first")
    contract = read_json(contract_path)
    if not contract.get("outputs"):
        raise ValueError(f"{wf_id}/{seg} contract.json has no outputs[] to validate (plan "
                         f"contract C5 requires at least one); translate this segment first")

    proc_file = Path(proc_path) if proc_path is not None else repo.seg(wf_id, seg, "proc.sql")
    if not proc_file.is_file():
        raise FileNotFoundError(f"{wf_id}/{seg} has no procedure to validate: {proc_file}; "
                                f"translate this segment first (or pass --proc)")
    proc_sql = proc_file.read_text(encoding="utf-8")

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

    # Every name spliced into SQL from the contract or the mappings, checked before any backend exists
    # (fix round 1, C2): a bad one is a usage error and nothing is executed on either backend.
    database = SANDBOX_DB if sandbox is None else sandbox.database
    check_contract_names(wf_id, seg, contract, database)
    check_names(repo, wf_id, sets, database=database)

    reports: dict[str, dict] = {}
    idempotent: bool | None = None
    idempotency_diff: list[str] = []
    for index, golden_set in enumerate(sets):
        check = index == 0
        engine = {} if sandbox is None else {
            "backend_factory": partial(sandbox.fresh, [golden_view_schema(wf_id, golden_set)]),
            "database": sandbox.database}
        report = _run_one_set(repo, wf_id, seg, golden_set, contract, proc_sql, tolerances,
                              accepted_classes, approvals, segment_dag, check_idempotency=check, **engine)
        if check:
            idempotent = report["idempotent"]
            idempotency_diff = report["idempotency_diff"]
        reports[golden_set] = report

    # Idempotency is a property of the procedure, established once from the first golden set, and
    # carried onto every set's report (and so onto validation.json, whichever set it is drawn
    # from); aggregating the sets and writing validation.json/validation.<set>.json is
    # `lib.validation.write_reports`'s job, shared with `validate_snowpark.py`.
    return write_reports(repo, wf_id, seg, sets, reports, (idempotent, idempotency_diff))


# --- CLI --------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0003")
    parser.add_argument("seg", help="segment id, e.g. seg_01")
    parser.add_argument("--set", dest="sets", action="append", default=None,
                        help="golden set to validate (repeatable); default: manifest.golden_sets")
    parser.add_argument("--proc", type=Path, default=None,
                        help="procedure file to validate (default: segments/<seg>/proc.sql)")
    add_backend_args(parser)
    add_root_arg(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo = Repo(args.root)
    try:
        report = validate_segment(repo, args.wf_id, args.seg, args.sets, proc_path=args.proc,
                                  backend=args.backend, connection=args.connection,
                                  sandbox_database=args.sandbox_database)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(error_text(args.backend, str(exc)))  # exit 2: a missing prerequisite; nothing was written
    except Exception:            # noqa: BLE001 -- exit 2 is "anything unexpected"; never a bare traceback exit 1
        print_crash(args.backend)
        return 2

    print(f"{args.wf_id}/{args.seg}: {report['verdict']} {report['sets']} "
         f"(idempotent={report['idempotent']})")
    return 0 if report["verdict"].startswith("PASS") else 1


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
