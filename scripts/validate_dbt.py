"""Validate a whole dbt-target workflow's project against every golden set (design spec §5.3).

    python scripts/validate_dbt.py <wf_id> [--set NAME]... [--project DIR] [--root .]
        [--backend duckdb|snowflake] [--connection NAME] [--sandbox-database DB]

A dbt project is checked as ONE unit (DV6): unlike `validate_segment.py`/`validate_snowpark.py`,
which drive one segment's own procedure, this module runs the whole project's `dbt run` once per
golden set and then judges every contract output of every segment against that one run's actual
tables -- a downstream segment's model reads its upstream's model through `ref()`, the real chain,
never a golden intermediate reloaded in isolation. For each golden set this builds a *fresh*
on-disk DuckDB sandbox loaded exactly as `load_golden.load_set` loads one for the SQL path (DV3:
the `--vars` a local run passes are the flattened schema names `lib.backend.local_name` produces,
`lib.dbt_project.local_vars`), runs `dbt run` through the one place this repo ever invokes dbt
(`lib.dbt_project.run_dbt`), and compares every `contract.outputs[]` table of every segment against
its golden CSV with the unchanged `compare.py`, through the same report-shaping machinery
`lib.validation` shares with the other two validators (`combine`, `missing_table_report`,
`fail_report`, `clear_stale_reports`, `write_reports`).

**Idempotency (DV7).** The design's "same starting state" is two FRESH sandboxes built the same
way, compared as row multisets -- a shared sandbox would double an Append output by design (spike
S3), which would look like non-determinism when it is not. So, for the *first* golden set only,
after the primary run this builds a second, `_rerun`-suffixed sandbox from scratch -- with every raw
golden input and every `targets_before` table loaded in REVERSED row order (the first sandbox keeps
file order; Task W1 fix round 2), so a model that depends on the order its input arrives in (a
`LIMIT` without `ORDER BY`, a window without a total order; Snowflake promises no row order) computes
something else -- runs the whole project again, and calls a segment idempotent when every one of its
declared output tables holds the same rows both times. The `_rerun` sandbox (and its `.wal`) is deleted once compared; the
per-set sandboxes (`dbt_sandbox_<set>.duckdb`, DV1's dot-free naming -- a dot-prefixed file name
breaks dbt-duckdb's own catalog naming, spike S2) are kept for inspection and are git-ignored.

**A `dbt run` failure is a domain FAIL, not a crash (`DbtRunFailed`).** Because the whole project
runs as one unit, a single broken model fails every segment's report for that golden set -- each
gets `verdict: "FAIL"` and an `error` naming the failed model(s) from dbt's own `run_results.json`,
never a Python traceback. The per-set dbt log is kept at `dbt/logs/validate_<set>.log` (and
`validate_<first set>_rerun.log`), git-ignored, for whoever needs the full output.

**The workflow's chain report comes from the same run (Task W1).** A dbt project's run already IS
the chain `validate_workflow.py` builds for a procedures workflow -- every model reads its upstream
model's actual table through `ref()` -- so no second run is needed: the same per-output compare
reports, in chain order, become `workflows/<wf>/validation_workflow.json` and
`validation_workflow.<set>.json` (`lib.validation.chain_report`: `boundaries`, `finals`,
`divergence_kind`, `first_divergence`; a failed `dbt run` is a `boundary` divergence with
`stream: null` at the segment owning the first failed model). `validate_workflow.py` delegates a
dbt workflow here and returns that report; the orchestrator requires it to be `PASS*` before
translate is `VALIDATED`.

**The closed surface first (final fix wave C1).** Before any sandbox exists, the project is judged
by `lib.dbt_project.check_surface` (a Python model, a project or model hook that is not one plain
statement against `{{ this }}`, a macro, a `packages.yml`, Jinja in YAML, a model reading anything but
`source()`/`ref()`/`{{ this }}`, ...). A project outside it never runs: no dbt process starts, every
segment FAILs on every set with an `error` naming `dbt:surface`, the chain report FAILs at the first
segment, and the CLI exits 1 -- a domain failure, like a failed `dbt run`.

**Usage vs domain, and report hygiene.** Exactly like the other two validators: a missing
prerequisite (`segments/order.json`, missing or listing no segment; a segment's `contract.json` with no
`outputs[]`; `dbt/dbt_project.yml`, `intake/mappings.yaml`, no golden sets to validate, no `dbt` console script
beside this interpreter) is a *usage* error (`FileNotFoundError`/`ValueError`/`DbtUnavailable`) and
propagates uncaught; every segment's stale `validation*.json` is deleted before any prerequisite
check runs, so a usage error -- or any other crash -- leaves no report behind, never a stale one. A
golden set that is no longer requested loses its own leftover `validation.<set>.json` too.

**`--backend snowflake` (plan Task P2).** Selected only by that flag, run by a human: each sandbox is
a fresh set of schemas in a sandbox database the policy lists, loaded through a NAMED connection
(`lib.snowflake_sandbox`); dbt runs the profile's `snowflake` output with `SNOWFLAKE_DATABASE` set to
that database and `--vars` naming `MIG_GOLDEN_<WF>_<SET>` / `MIG_WORK` unflattened; the tables are
judged through `SnowflakeBackend`, and the DV7 re-run loads its inputs in reversed INSERT order (a
real account has no physical order to reverse afterwards -- a weaker perturbation). dbt's output is
redacted before it reaches a log or a report.

Nothing here has ever run against a real Snowflake account: dbt-duckdb's types, case folding and
MERGE semantics are DuckDB's own (design §9), not Snowflake's, and `dbt-snowflake` is not installed
in this environment.
"""
from __future__ import annotations

import argparse
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import compare
from lib import dbt_project, validation as v
from lib.backend import DuckDBBackend
from lib.dbt_project import DbtResult, DbtUnavailable
from lib.io import load_manifest, read_json, read_yaml
from lib.paths import Repo, add_root_arg
from lib.typed_csv import read_table
from load_golden import GOLDEN_SCHEMA, SANDBOX_DB, WORK_SCHEMA, check_names, golden_view_schema, load_set


class DbtRunFailed(Exception):
    """`dbt run` exited non-zero: a domain FAIL for every segment of the project (it runs as one
    unit, so one broken model fails all of them for this golden set)."""


# --- one golden set: a fresh sandbox, one dbt run --------------------------------------------------


@contextmanager
def _reading(handle):
    """A backend to read one run's tables from: a DuckDB sandbox FILE is opened (and closed after);
    a live `SnowflakeBackend` (plan Task P2) is used as it is -- its caller closes it."""
    if isinstance(handle, Path):
        backend = DuckDBBackend(str(handle))
        try:
            yield backend
        finally:
            backend.close()
    else:
        yield handle


def _run_project_on_snowflake(sandbox, repo: Repo, wf_id: str, golden_set: str, project: Path,
                              log_name: str, *, reverse: bool = False) -> tuple[DbtResult, object]:
    """One `dbt run` of the whole project against a real account (plan Task P2): a fresh sandbox in
    the sandbox database with the golden set loaded as `load_golden.load_set` loads it -- in reversed
    INSERT order for DV7's re-run (`reverse`; no physical order exists there to reverse afterwards) --
    then `run_dbt` on the profile's `snowflake` output with `SNOWFLAKE_DATABASE` set to the sandbox
    database. Returns the result (every text redacted; the log is written from that) and the live
    backend its tables are read through, which the caller closes."""
    from lib import snowflake_conn  # noqa: PLC0415  (lazy: never on the local path)
    view_schema = golden_view_schema(wf_id, golden_set)
    backend = sandbox.fresh([view_schema])
    try:
        load_set(backend, repo, wf_id, golden_set, database=sandbox.database, reverse=reverse)
        result = dbt_project.run_dbt("run", project, vars={"src_schema": view_schema, "tgt_schema": WORK_SCHEMA},
                                     duckdb_path=None, target="snowflake",
                                     extra_env={"SNOWFLAKE_DATABASE": sandbox.database})
    except BaseException:
        backend.close()
        raise
    result = replace(result, output=snowflake_conn.redact(result.output),
                     results=[{**r, "message": snowflake_conn.redact(r["message"])} for r in result.results])
    log = project / "logs" / f"{log_name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(result.output, encoding="utf-8", newline="\n")
    return result, backend


def _load_sandbox(repo: Repo, wf_id: str, golden_set: str, path: Path, *, reverse: bool = False) -> None:
    """A fresh on-disk sandbox at `path`, loaded exactly as `load_golden.load_set` loads one for
    the SQL validators: any stale file (and its `.wal`) from a previous run is removed first. With
    `reverse` (DV7's re-run sandbox, Task W1 fix round 2) every raw golden input table and every
    `targets_before` table is then rewritten in reversed row order -- the source views read the
    reversed base tables -- so a model that depends on row order computes something else."""
    for stale in (path, path.with_name(path.name + ".wal")):
        if stale.exists():
            stale.unlink()
    backend = DuckDBBackend(str(path))
    try:
        info = load_set(backend, repo, wf_id, golden_set)
        if reverse:
            for fqn in info["loaded"]:
                if fqn.startswith((f"{GOLDEN_SCHEMA}.", f"{SANDBOX_DB}.{WORK_SCHEMA}.")):
                    v.reverse_physical_order(backend, fqn)
    finally:
        backend.close()


def _run_project(repo: Repo, wf_id: str, golden_set: str, project: Path, sandbox: Path,
                 log_name: str, *, reverse: bool = False) -> DbtResult:
    _load_sandbox(repo, wf_id, golden_set, sandbox, reverse=reverse)
    return dbt_project.run_dbt("run", project, vars=dbt_project.local_vars(golden_view_schema(wf_id, golden_set)),
                               duckdb_path=sandbox, log_file=project / "logs" / f"{log_name}.log")


def _run_error(result: DbtResult, golden_set: str) -> str:
    failed = ", ".join(result.failed_models) or "none named by dbt"
    skipped = f"; skipped: {', '.join(result.skipped_models)}" if result.skipped_models else ""
    detail = next((r["message"] for r in result.results
                   if r["status"] in dbt_project.FAILED_STATUSES and r["message"]), None) or dbt_project.tail(result.output)
    return (f"dbt run exited {result.code} on golden set {golden_set}: failed models: {failed}{skipped}. "
            f"{dbt_project.bounded(detail)}")


def _snapshot(sandbox, relations: dict[str, list[tuple[dict, str]]]) -> dict[str, list[tuple] | None]:
    with _reading(sandbox) as backend:
        return v.snapshot(backend, [fqn for pairs in relations.values() for _, fqn in pairs])


def _compare_set(repo: Repo, wf_id: str, golden_set: str, sandbox, segments: list[str],
                 contracts: dict[str, dict], relations: dict[str, list[tuple[dict, str]]],
                 settings: dict, manifest: dict) -> tuple[dict[str, dict], list[dict]]:
    """Every segment's report for this set, plus the chain entries (`lib.validation.chain_report`)
    the same per-output compare reports make, in chain order: segments in wave order, outputs in
    contract order. `sandbox` is the run's DuckDB file, or the live Snowflake backend (Task P2)."""
    reports: dict[str, dict] = {}
    entries: list[dict] = []
    with _reading(sandbox) as backend:
        index = 0
        for seg in segments:
            dag_path = repo.seg(wf_id, seg, "dag.json")
            segment_dag = read_json(dag_path) if dag_path.is_file() else None
            approvals = [a for a in (manifest.get("accepted_diffs") or []) if a.get("segment") == seg]
            output_reports = []
            for output, fqn in relations[seg]:
                expected = v.expected_fqn(index)
                index += 1
                backend.load_table(expected, read_table(v.golden_path(repo, wf_id, seg, golden_set, output)))
                report = (compare.compare(backend, expected, fqn, contracts[seg], settings["tolerances"],
                                          output=output, accepted_classes=settings["accepted"],
                                          approvals=approvals, segment_dag=segment_dag, golden_set=golden_set)
                          if backend.table_exists(fqn) else v.missing_table_report(fqn))
                output_reports.append((output, report))
                target = output.get("kind") == "target"
                entries.append({"segment": seg, "stream": output.get("stream"),
                                "kind": "target" if target else "work",
                                "output": output.get("logical") if target else output.get("table"),
                                "relation": fqn, "report": report})
            reports[seg] = v.combine(contracts[seg], golden_set, output_reports)
    return reports, entries


def _failed_segment(result: DbtResult, segments: list[str], contracts: dict[str, dict]) -> str:
    """The segment whose contract output is the first model dbt names as failed (else skipped),
    in chain order -- where the chain report says the run stopped. Falls back to the first
    segment when dbt named no model at all (an error before any model ran)."""
    named = set(result.failed_models) or set(result.skipped_models)
    for seg in segments:
        if any(dbt_project.model_name(output) in named for output in contracts[seg]["outputs"]):
            return seg
    return segments[0]


def _refuse(repo: Repo, wf_id: str, sets: list[str], segments: list[str], contracts: dict[str, dict],
            error: dbt_project.DbtUnsafe) -> dict[str, dict]:
    """The project is outside the closed dbt surface (final fix wave C1): no sandbox is built and no
    dbt process starts. Every segment FAILs on every set with an `error` naming `dbt:surface` (and
    each finer check), never idempotent; the chain report FAILs at the first segment, with
    `stream: null` like any run that raised. A domain failure: the CLI exits 1."""
    per_set = {golden_set: {seg: v.fail_report(contracts[seg], golden_set, error) for seg in segments}
               for golden_set in sets}
    results = {seg: v.write_reports(repo, wf_id, seg, sets, {s: per_set[s][seg] for s in sets}, (None, []),
                                    extra={"target": "dbt"})
               for seg in segments}
    chain_reports = {}
    for golden_set in sets:
        chain_reports[golden_set] = v.chain_report(wf_id, golden_set, [], v.ChainError(segments[0], error))
        chain_reports[golden_set]["runtime_ms"] = 0
    v.write_workflow_reports(repo, wf_id, sets, chain_reports, (None, []), extra={"target": "dbt"})
    return results


def _workflow_idempotency(segments: list[str],
                          idempotent: dict[str, tuple[bool | None, list[str]]]) -> tuple[bool | None, list[str]]:
    """The chain's idempotency from every segment's own: `None` if any segment's was never
    established (the first set's run failed), else whether every one held, with every diverging
    relation in chain order."""
    if any(idempotent[seg][0] is None for seg in segments):
        return None, []
    diverging = list(dict.fromkeys(fqn for seg in segments for fqn in idempotent[seg][1]))
    return not diverging, diverging


# --- prerequisites, mirroring validate_segment.validate_segment's own checks --------------------


def _segments(repo: Repo, wf_id: str) -> list[str]:
    order_path = repo.wf(wf_id, "segments", "order.json")
    if not order_path.is_file():
        raise FileNotFoundError(f"{wf_id} has no segments/order.json to validate against: "
                                f"{order_path}; segment this workflow first")
    order = read_json(order_path)
    segments = [seg for wave in order for seg in wave]
    if not segments:
        raise ValueError(f"{wf_id}: segments/order.json lists no segments to validate: {order_path}; "
                         f"segment this workflow first")
    return segments


def _prerequisites(repo: Repo, wf_id: str, project: Path, segments: list[str],
                   golden_sets: Sequence[str] | None,
                   database: str = SANDBOX_DB) -> tuple[dict[str, dict], list[str], dict, dict]:
    project_yml = project / "dbt_project.yml"
    if not project_yml.is_file():
        raise FileNotFoundError(f"{wf_id} has no dbt project to validate: {project_yml}; "
                                f"translate this workflow first (or pass --project)")

    contracts: dict[str, dict] = {}
    for seg in segments:
        contract_path = repo.seg(wf_id, seg, "contract.json")
        if not contract_path.is_file():
            raise FileNotFoundError(f"{wf_id}/{seg} has no contract.json to validate against: "
                                    f"{contract_path}; translate this segment first")
        contract = read_json(contract_path)
        if not contract.get("outputs"):
            raise ValueError(f"{wf_id}/{seg} contract.json has no outputs[] to validate (plan "
                             f"contract C5 requires at least one); translate this segment first")
        contracts[seg] = contract

    mappings_path = repo.wf(wf_id, "intake", "mappings.yaml")
    if not mappings_path.is_file():
        raise FileNotFoundError(f"{wf_id} has no intake/mappings.yaml to load golden data against: "
                                f"{mappings_path}; run intake first: python scripts/intake_prompt.py {wf_id}")

    manifest = load_manifest(repo, wf_id)
    sets = list(golden_sets) if golden_sets is not None else list(manifest.get("golden_sets") or [])
    if not sets:
        raise ValueError(f"{wf_id} has no golden sets to validate against (manifest.json's "
                         f"golden_sets is empty and none were given with --set); run "
                         f"dev/build_samples.py or inject_outputs.py first")

    raw_settings = read_yaml(repo.global_mappings) if repo.global_mappings.is_file() else {}
    raw_settings = raw_settings or {}
    settings = {"tolerances": raw_settings.get("tolerances") or compare.DEFAULT_TOLERANCES,
               "accepted": raw_settings.get("accepted_diff_classes") or ()}
    # Every name spliced into SQL (or into dbt's --vars) from the contracts or the mappings, checked
    # before any sandbox exists or dbt runs (fix round 1, C2).
    for seg in segments:
        v.check_contract_names(wf_id, seg, contracts[seg], database)
    check_names(repo, wf_id, sets, database=database)
    return contracts, sets, settings, manifest


# --- the whole workflow -----------------------------------------------------------------------------


def validate_dbt(repo: Repo, wf_id: str, golden_sets: Sequence[str] | None = None, *,
                 project_dir: Path | None = None, backend: str = "duckdb", connection: str | None = None,
                 sandbox_database: str | None = None) -> dict[str, dict]:
    """Validates a dbt-target workflow's whole project against every named golden set (default:
    `manifest.golden_sets`): one `dbt run` per golden set, judged against every segment's contract
    outputs, plus a DV7 idempotency check from the first golden set's two fresh sandboxes. Writes
    `segments/<seg>/validation.json` and `validation.<set>.json` for every segment (each carrying
    `"target": "dbt"` at the top level only) and returns `{segment: top-level report}`.

    `backend="snowflake"` (plan Task P2): each sandbox is a fresh `SnowflakeSandbox` in the sandbox
    database, dbt runs on the profile's `snowflake` output (`SNOWFLAKE_DATABASE` = that database,
    `--vars` the unflattened `MIG_GOLDEN_<WF>_<SET>` / `MIG_WORK`), and the tables are read back
    through `SnowflakeBackend`. dbt-snowflake must be installed (`DbtUnavailable` otherwise).
    """
    v.clear_stale_workflow_reports(repo, wf_id)              # before anything else (Task W1)
    segments = _segments(repo, wf_id)                        # order.json, wave order; FileNotFoundError
    for seg in segments:
        v.clear_stale_reports(repo, wf_id, seg)              # before any prerequisite check
    sandbox = v.snowflake_target(repo, backend, connection, sandbox_database)   # nothing connects yet
    database = SANDBOX_DB if sandbox is None else sandbox.database
    project = Path(project_dir) if project_dir is not None else dbt_project.project_dir(repo, wf_id)
    contracts, sets, settings, manifest = _prerequisites(repo, wf_id, project, segments, golden_sets, database)
    dbt_project.dbt_executable()                             # DbtUnavailable before anything runs
    if sandbox is not None:
        from lib import snowflake_conn  # noqa: PLC0415  (lazy: never on the local path)
        snowflake_conn.require_dbt_snowflake()               # DbtUnavailable naming dbt-snowflake
    refused = dbt_project.check_surface(project)             # final fix wave C1: before any process
    if refused:
        return _refuse(repo, wf_id, sets, segments, contracts, dbt_project.DbtUnsafe(refused))
    for stale in repo.wf(wf_id).glob("dbt_sandbox_*.duckdb*"):
        stale.unlink()
    relations = {seg: [(o, dbt_project.model_relation(wf_id, seg, o, database)) for o in contracts[seg]["outputs"]]
                for seg in segments}
    idempotent: dict[str, tuple[bool | None, list[str]]] = {seg: (None, []) for seg in segments}
    per_set: dict[str, dict[str, dict]] = {}
    chains: dict[str, tuple[list[dict], v.ChainError | None]] = {}
    for index, golden_set in enumerate(sets):
        started = time.perf_counter()
        live = []                                            # Snowflake backends to close after this set
        try:
            if sandbox is None:
                handle = dbt_project.sandbox_path(repo, wf_id, golden_set)
                result = _run_project(repo, wf_id, golden_set, project, handle, f"validate_{golden_set}")
            else:
                result, handle = _run_project_on_snowflake(sandbox, repo, wf_id, golden_set, project,
                                                           f"validate_{golden_set}")
                live.append(handle)
            if result.code != 0:
                error = DbtRunFailed(_run_error(result, golden_set))
                reports = {seg: v.fail_report(contracts[seg], golden_set, error) for seg in segments}
                chains[golden_set] = ([], v.ChainError(_failed_segment(result, segments, contracts), error))
            else:
                reports, entries = _compare_set(repo, wf_id, golden_set, handle, segments, contracts,
                                                relations, settings, manifest)
                chains[golden_set] = (entries, None)
                if index == 0:                               # DV7: a second run from a FRESH sandbox
                    first = _snapshot(handle, relations)     # before a Snowflake re-run replaces it
                    if sandbox is None:
                        rerun = dbt_project.sandbox_path(repo, wf_id, golden_set, "_rerun")
                        again = _run_project(repo, wf_id, golden_set, project, rerun,
                                             f"validate_{golden_set}_rerun", reverse=True)
                    else:
                        again, rerun = _run_project_on_snowflake(sandbox, repo, wf_id, golden_set, project,
                                                                 f"validate_{golden_set}_rerun", reverse=True)
                        live.append(rerun)
                    second = _snapshot(rerun, relations) if again.code == 0 else None
                    if sandbox is None:
                        for path in (rerun, rerun.with_name(rerun.name + ".wal")):
                            if path.exists():
                                path.unlink()
                    for seg in segments:
                        fqns = [fqn for _, fqn in relations[seg]]
                        diverging = fqns if second is None else v.diverging_tables(first, second, fqns)
                        idempotent[seg] = (not diverging, diverging)
                        if diverging:
                            reports[seg]["verdict"] = v.worst_verdict([reports[seg]["verdict"], "FAIL"])
        finally:
            for backend_ in live:
                backend_.close()
        elapsed = int(round((time.perf_counter() - started) * 1000))
        for seg in segments:
            reports[seg]["runtime_ms"] = elapsed             # the whole project ran for this set
        per_set[golden_set] = reports
    results = {seg: v.write_reports(repo, wf_id, seg, sets, {s: per_set[s][seg] for s in sets},
                                    idempotent[seg], extra={"target": "dbt"})
               for seg in segments}
    # The workflow's chain report comes from the same runs (Task W1): the whole project IS the
    # chain -- every model read its upstream model's actual table through ref().
    chain_idempotency = _workflow_idempotency(segments, idempotent)
    chain_reports = {}
    for index, golden_set in enumerate(sets):
        entries, error = chains[golden_set]
        chain_reports[golden_set] = v.chain_report(wf_id, golden_set, entries, error,
                                                   diverging=chain_idempotency[1] if index == 0 else ())
        chain_reports[golden_set]["runtime_ms"] = per_set[golden_set][segments[0]]["runtime_ms"]
    v.write_workflow_reports(repo, wf_id, sets, chain_reports, chain_idempotency, extra={"target": "dbt"})
    return results


# --- CLI --------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0009")
    parser.add_argument("--set", dest="sets", action="append", default=None,
                        help="golden set to validate (repeatable); default: manifest.golden_sets")
    parser.add_argument("--project", type=Path, default=None,
                        help="dbt project directory to validate (default: workflows/<wf_id>/dbt)")
    v.add_backend_args(parser)
    add_root_arg(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo = Repo(args.root)
    try:
        reports = validate_dbt(repo, args.wf_id, args.sets, project_dir=args.project, backend=args.backend,
                               connection=args.connection, sandbox_database=args.sandbox_database)
    except (FileNotFoundError, ValueError, DbtUnavailable) as exc:
        parser.error(v.error_text(args.backend, str(exc)))  # exit 2: a missing prerequisite; nothing was written
    except Exception:            # noqa: BLE001 -- exit 2 is "anything unexpected"; never a bare traceback exit 1
        v.print_crash(args.backend)
        return 2

    for seg, report in reports.items():
        print(f"{args.wf_id}/{seg}: {report['verdict']} {report['sets']} "
             f"(idempotent={report['idempotent']})")
    return 0 if all(report["verdict"].startswith("PASS") for report in reports.values()) else 1


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
