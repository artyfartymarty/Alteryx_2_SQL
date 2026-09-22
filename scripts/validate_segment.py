"""Segment parity validation: loads golden data, runs a translated procedure, compares every
output, checks determinism, and writes `validation.json` (program spec §9, plan task 14).

    python scripts/validate_segment.py <wf_id> <seg> [--set NAME]... [--proc FILE] [--root .]

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
import traceback
from pathlib import Path
from typing import Sequence

import compare
from lib import typed_csv
from lib.backend import SANDBOX_DB, BackendError, DuckDBBackend
from lib.io import load_manifest, read_json, read_yaml, write_json
from lib.paths import Repo, add_root_arg
from lib.proc_runner import ProcError, run_proc
from load_golden import WORK_SCHEMA, load_intermediate, load_set

#: Where a golden CSV is loaded before compare() runs, one table per output (compare.py's own
#: convention is a single `MIG_COMPARE.EXPECTED`; a segment can have several outputs to compare in
#: the same backend, so each gets its own numbered table).
_EXPECTED_SCHEMA = "MIG_COMPARE"

_VERDICT_SEVERITY = {"PASS": 0, "PASS_WITH_ACCEPTED_DIFF": 1, "FAIL": 2}


# --- table/path resolution for one contract output ---------------------------------------------


def _actual_table(wf_id: str, seg: str, output: dict) -> str:
    """The sandbox table a `contract.outputs[]` entry actually lands in (plan contracts C3/C4).

    A `work` output is whatever literal table name the contract gives (the procedure creates it
    unqualified, e.g. `MIG_WORK.WF0009_SEG_01_OUT`). A `target` output has no `table` of its own --
    the procedure writes it through `IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.<LOGICAL>')`,
    which resolves to `MIGDB.MIG_WORK.<logical>`, matching `load_golden`'s own `WORK_SCHEMA`.
    """
    if output.get("kind") == "target":
        logical = output.get("logical")
        if not logical:
            raise ValueError(f"{wf_id}/{seg} contract output for stream {output.get('stream')!r} "
                             f"is a target with no `logical` name (plan contract C5)")
        return f"{SANDBOX_DB}.{WORK_SCHEMA}.{logical}"
    table = output.get("table")
    if not table:
        raise ValueError(f"{wf_id}/{seg} contract output for stream {output.get('stream')!r} "
                         f"has no `table` name (plan contract C5)")
    return table


def _golden_path(repo: Repo, wf_id: str, seg: str, golden_set: str, output: dict) -> Path:
    """`golden/outputs/<set>/<tool_id>.csv` for a `target`, `golden/intermediates/<seg>/<set>/
    <stream>.csv` for a `work` output (plan contract C2). `tool_id`/`stream` come straight from
    the contract, so they go through `Repo` -- a hostile value there raises `ValueError`."""
    if output.get("kind") == "target":
        tool_id = output.get("tool_id")
        if not tool_id:
            raise ValueError(f"{wf_id}/{seg} contract output for stream {output.get('stream')!r} "
                             f"is a target with no `tool_id` (plan contract C5)")
        return repo.wf(wf_id, "golden", "outputs", golden_set, f"{tool_id}.csv")
    stream = output.get("stream")
    if not stream:
        raise ValueError(f"{wf_id}/{seg} contract output {output!r} has no `stream`")
    return repo.wf(wf_id, "golden", "intermediates", seg, golden_set, f"{stream}.csv")


# --- one full run: load golden data, load upstream intermediates, run the procedure -------------


def _load_and_run(backend: DuckDBBackend, repo: Repo, wf_id: str, seg: str, golden_set: str,
                  contract: dict, proc_sql: str, run_id: str) -> None:
    """One complete run against a fresh backend: golden set, any upstream-segment intermediate the
    contract declares, then the procedure. Raises `FileNotFoundError`/`ValueError` for a missing or
    malformed prerequisite (usage error) and lets `ProcError`/`BackendError` from `run_proc`
    propagate for the caller to turn into a domain FAIL.
    """
    info = load_set(backend, repo, wf_id, golden_set)
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
    run_proc(backend, proc_sql, args)


def _read_ordered_rows(backend: DuckDBBackend, table: str) -> list[tuple]:
    """Every row of `table`, ordered by every column so two runs' outputs can be compared for
    equality as row multisets: sorting by all columns puts duplicate rows next to each other, and
    since duplicates are indistinguishable, their relative order can never affect the comparison."""
    columns = backend.table_columns(table)
    if not columns:
        return []
    order = ", ".join(str(i) for i in range(1, len(columns) + 1))
    return backend.query(f"SELECT * FROM {table} ORDER BY {order}")[1]


def _outputs_equal(a: DuckDBBackend, b: DuckDBBackend, wf_id: str, seg: str,
                   outputs: list[dict]) -> tuple[bool, list[str]]:
    """Row-multiset equality of every declared output table between two runs. Returns
    `(idempotent, diverging_table_names)`. A table missing on either side is never treated as
    equal -- idempotency cannot be verified for it, so it counts as diverging like any other
    mismatch (findings K2/C4(a): "never true" without a genuine comparison of both runs)."""
    diverging: list[str] = []
    for output in outputs:
        table = _actual_table(wf_id, seg, output)
        if not (a.table_exists(table) and b.table_exists(table)):
            diverging.append(table)
            continue
        if _read_ordered_rows(a, table) != _read_ordered_rows(b, table):
            diverging.append(table)
    return not diverging, diverging


# --- combining the per-output compare() reports into one report per golden set ------------------


def _worst_verdict(verdicts: Sequence[str]) -> str:
    if not verdicts:
        return "PASS"
    return max(verdicts, key=lambda verdict: _VERDICT_SEVERITY[verdict])


def _missing_table_report(actual_fqn: str) -> dict:
    """The procedure never created one of its declared output tables (finding C4(a)). This is an
    *actual*-side failure -- a domain FAIL that feeds the fixer, exactly like a compile error --
    not compare.py's own `ValueError("there is no table … to compare")`, which is reserved for a
    usage error on the *expected* side. Shaped like one of `compare.compare()`'s own per-output
    reports so `_combine` can merge it the same way, plus an `"error"` naming the table."""
    return {
        "verdict": "FAIL", "error": f"output table {actual_fqn} does not exist after running "
                                    f"the procedure",
        "checks": {}, "diff_clusters": [], "normalizations_applied": [], "needs_human": False,
        "truncated": False,
    }


def _combine(contract: dict, golden_set: str, output_reports: list[tuple[dict, dict]]) -> dict:
    """Merges one `compare.compare()` report (or `_missing_table_report`) per contract output into
    one report for this segment and golden set. Nothing about a check or a cluster is re-derived:
    every count, verdict and classification is exactly what `compare.py` produced. Only `"stream"`
    is added, to each cluster, and a `"checks"` entry per output (keyed by stream and kind, since a
    work stream and the target it feeds share the same `stream` id, plan contract C5). Any
    per-output `"error"` (a missing actual table) is carried up too, joined if there is more than
    one."""
    verdict = _worst_verdict([report["verdict"] for _, report in output_reports])
    clusters: list[dict] = []
    for output, report in output_reports:
        for cluster in report["diff_clusters"]:
            tagged = dict(cluster)
            tagged["stream"] = output.get("stream")
            clusters.append(tagged)
    clusters.sort(key=lambda cluster: (compare.CLASS_ORDER.index(cluster["class"]),
                                       cluster.get("stream") or "", cluster["columns"]))
    checks = {f"{output.get('stream')}:{output.get('kind')}": report["checks"]
             for output, report in output_reports}
    normalizations = sorted({op for _, report in output_reports
                             for op in report["normalizations_applied"]})
    errors = [report["error"] for _, report in output_reports if report.get("error")]
    result = {
        "segment": contract.get("segment"),
        "golden_set": golden_set,
        "verdict": verdict,
        "checks": checks,
        "diff_clusters": clusters,
        "normalizations_applied": normalizations,
        "idempotent": None,             # filled in by the caller, once per segment
        "idempotency_diff": [],         # filled in by the caller, once per segment
        "runtime_ms": 0,                # filled in by the caller: wall time for the whole set
        "credits": None,                # a local DuckDB run burns no Snowflake credits
        "needs_human": any(report["needs_human"] for _, report in output_reports),
        "truncated": any(report.get("truncated") for _, report in output_reports),
    }
    if errors:
        result["error"] = "; ".join(errors)
    return result


def _fail_report(contract: dict, golden_set: str, error: Exception) -> dict:
    """A `ProcError`/`BackendError` while running the procedure: a domain FAIL that feeds the fixer
    (program spec §9), not a human escalation -- so `needs_human` stays false. The first run itself
    never completed, so no second run was attempted either: `idempotent` stays `None` (finding
    K2's documented exception -- it must never become `True` without both runs being compared)."""
    return {
        "segment": contract.get("segment"), "golden_set": golden_set, "verdict": "FAIL",
        "error": str(error), "checks": {}, "diff_clusters": [], "normalizations_applied": [],
        "idempotent": None, "idempotency_diff": [], "runtime_ms": 0, "credits": None,
        "needs_human": False, "truncated": False,
    }


def _aggregate_sets(sets: Sequence[str], reports: dict[str, dict]) -> dict:
    """Combines every golden set's own report into the segment-level result written to
    `validation.json` (finding K1's ruling): the verdict is the *worst* across every processed
    set; the body (every other key) is copied from the first set, in processing order, whose own
    verdict equals that worst verdict; `needs_human` is true if *any* set's own report says so,
    regardless of which set supplied the body; `sets` lists every processed set's own verdict."""
    worst = _worst_verdict([reports[golden_set]["verdict"] for golden_set in sets])
    chosen = next(reports[golden_set] for golden_set in sets
                 if reports[golden_set]["verdict"] == worst)
    result = dict(chosen)
    result["verdict"] = worst
    result["needs_human"] = any(reports[golden_set]["needs_human"] for golden_set in sets)
    result["sets"] = {golden_set: reports[golden_set]["verdict"] for golden_set in sets}
    return result


def _clear_stale_reports(repo: Repo, wf_id: str, seg: str) -> None:
    """Deletes this segment's `validation.json` and every `validation.<set>.json` before anything
    else runs (findings C4(b)/I5): a report on disk always belongs to the most recent invocation,
    so any usage error or crash below this point leaves *no* report at all, and a golden set that
    is no longer requested loses its own leftover report too. Deletes only those two exact shapes
    in this one segment directory -- nothing else there is touched."""
    seg_dir = repo.seg(wf_id, seg)
    if not seg_dir.is_dir():
        return
    exact = seg_dir / "validation.json"
    if exact.is_file():
        exact.unlink()
    for path in seg_dir.glob("validation.*.json"):
        if path.is_file():
            path.unlink()


# --- one golden set -------------------------------------------------------------------------------


def _run_one_set(repo: Repo, wf_id: str, seg: str, golden_set: str, contract: dict, proc_sql: str,
                 tolerances: dict, accepted_classes: Sequence[str], approvals: Sequence[dict],
                 segment_dag: dict | None, *, check_idempotency: bool) -> dict:
    started = time.perf_counter()
    run_id = f"validate_{wf_id}_{seg}_{golden_set}"
    outputs = contract.get("outputs") or []
    backend = DuckDBBackend()
    try:
        try:
            _load_and_run(backend, repo, wf_id, seg, golden_set, contract, proc_sql, run_id)
        except (ProcError, BackendError) as exc:
            report = _fail_report(contract, golden_set, exc)
            report["runtime_ms"] = int(round((time.perf_counter() - started) * 1000))
            return report

        output_reports: list[tuple[dict, dict]] = []
        for index, output in enumerate(outputs):
            expected_fqn = f"{_EXPECTED_SCHEMA}.EXPECTED_{index}"
            golden_path = _golden_path(repo, wf_id, seg, golden_set, output)
            backend.load_table(expected_fqn, typed_csv.read_table(golden_path))
            actual_fqn = _actual_table(wf_id, seg, output)
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
            other = DuckDBBackend()
            try:
                try:
                    _load_and_run(other, repo, wf_id, seg, golden_set, contract, proc_sql, run_id)
                except (ProcError, BackendError):
                    # The second run itself could not complete: idempotency can never be verified
                    # as True without both runs actually being compared (finding K2's ruling), and
                    # here it did not even run -- every declared output counts as diverging.
                    idempotent, diverging = False, [_actual_table(wf_id, seg, o) for o in outputs]
                else:
                    idempotent, diverging = _outputs_equal(backend, other, wf_id, seg, outputs)
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
                     proc_path: Path | None = None) -> dict:
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
    """
    _clear_stale_reports(repo, wf_id, seg)

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

    reports: dict[str, dict] = {}
    idempotent: bool | None = None
    idempotency_diff: list[str] = []
    for index, golden_set in enumerate(sets):
        check = index == 0
        report = _run_one_set(repo, wf_id, seg, golden_set, contract, proc_sql, tolerances,
                              accepted_classes, approvals, segment_dag, check_idempotency=check)
        if check:
            idempotent = report["idempotent"]
            idempotency_diff = report["idempotency_diff"]
        reports[golden_set] = report

    # Idempotency is a property of the procedure, established once from the first golden set, and
    # carried onto every set's report (and so onto validation.json, whichever set it is drawn from).
    for report in reports.values():
        report["idempotent"] = idempotent
        report["idempotency_diff"] = idempotency_diff

    result = _aggregate_sets(sets, reports)

    write_json(repo.seg(wf_id, seg, "validation.json"), result)
    for golden_set in sets:
        write_json(repo.seg(wf_id, seg, f"validation.{golden_set}.json"), reports[golden_set])

    return result


# --- CLI --------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0003")
    parser.add_argument("seg", help="segment id, e.g. seg_01")
    parser.add_argument("--set", dest="sets", action="append", default=None,
                        help="golden set to validate (repeatable); default: manifest.golden_sets")
    parser.add_argument("--proc", type=Path, default=None,
                        help="procedure file to validate (default: segments/<seg>/proc.sql)")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    repo = Repo(args.root)
    try:
        report = validate_segment(repo, args.wf_id, args.seg, args.sets, proc_path=args.proc)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))  # exit 2: a missing prerequisite; nothing was written
    except Exception:            # noqa: BLE001 -- exit 2 is "anything unexpected"; never a bare traceback exit 1
        traceback.print_exc()
        return 2

    print(f"{args.wf_id}/{args.seg}: {report['verdict']} {report['sets']} "
         f"(idempotent={report['idempotent']})")
    return 0 if report["verdict"].startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
