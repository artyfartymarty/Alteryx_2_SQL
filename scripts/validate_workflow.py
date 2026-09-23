"""The chain test: a whole workflow, every segment on its upstream segments' ACTUAL output.

    python scripts/validate_workflow.py <wf_id> [--set NAME]... [--root .]
        [--backend duckdb|snowflake] [--connection NAME] [--sandbox-database DB]

`validate_segment.py` / `validate_snowpark.py` validate each segment in isolation: a downstream
segment is fed the GOLDEN intermediate Alteryx produced at its input boundary
(`load_golden.load_intermediate`), never its upstream segment's own output. That makes a failing
segment easy to localise, and it makes every error that only appears once segments are stitched
together invisible -- rounding that accumulates across boundaries, a type that widens at a seam, a
row-order assumption a golden intermediate happens to satisfy. This script closes that gap. For
each golden set it builds ONE fresh backend holding only the raw golden inputs (+ `targets_before`,
exactly as `load_golden.load_set` loads them), runs every segment in `segments/order.json` wave
order on whatever its upstream segments actually wrote, and judges every output with the unchanged
`compare.py` against its golden file: every work stream (a *boundary*) and every target (a
*final*), so the first divergence is localised to one segment and stream.

**Mixed SQL/Snowpark chains.** A SQL segment runs in the chain's `DuckDBBackend`
(`lib.proc_runner.run_proc`). A Snowpark segment runs in a fresh Local Testing Framework session:
its raw inputs and `targets_before` from golden data (`validate_snowpark.load_set_snowpark`), each
upstream stream it declares and each of its targets' current chain state handed in from the
backend, and every output it writes handed back -- all through `lib/handoff.py`, which reads the
REAL schema on both sides and never coerces to a contract (phase-1 ruling 16), so a Snowpark
segment that writes the wrong type is a `TYPE` difference here too.

**Report (ruling R-W1).** `workflows/<wf>/validation_workflow.<set>.json` per set and
`validation_workflow.json` for the workflow, in the shared report shape plus `workflow`,
`boundaries`, `finals`, `divergence_kind` (`null` | `boundary` | `chain_drift`) and
`first_divergence` (`{segment, stream, output, set}`): the first failing boundary in chain order is
a `boundary` divergence; a segment that raises while chained is a `boundary` divergence with
`stream: null` and an `error` naming it; if every boundary is within tolerance but a final is not,
it is `chain_drift` -- accumulated tolerance, a question for a human, never for the fixer
(`lib.validation.chain_report`). Idempotency: for the first golden set the whole chain runs a
second time from a fresh backend with every segment's inputs presented in REVERSED order (the
judged run hands rows on in physical order, never sorted), and every relation it wrote is compared
as a row multiset; any difference FAILs the chain -- which is how a procedure that depends on its
input's row order (none is promised by Snowflake) shows up as a `boundary` at that segment.

**A dbt workflow** (`manifest.output_kind == "dbt"`) needs no chain of its own: `validate_dbt.py`
already runs the whole project, where every model reads its upstream model's actual table through
`ref()`, and it writes `validation_workflow.json` from that same run. This script delegates to it
and returns that report.

**Exit codes.** 0 when the chain PASSes (`PASS` or `PASS_WITH_ACCEPTED_DIFF`), 1 on FAIL, 2 for a
usage error (a missing `order.json`, contract, procedure, mappings or golden file, or no golden
sets; nothing is written, and any stale chain report is deleted first) or any other crash.

**`--backend snowflake` (plan Task P2).** Selected only by that flag, run by a human: every pass runs
in ONE fresh sandbox in a sandbox database the policy lists, through a NAMED connection. A SQL
segment's procedure is created and CALLed there; a Snowpark segment runs in a session built from the
same connection, in the same database, where its upstream tables already are -- one engine, so the
typed hand-off is not used. The idempotency re-run INSERTs the raw golden inputs and `targets_before`
in reversed order (a real account has no physical order to reverse, and nothing is re-presented
between segments): a weaker perturbation than the local one.

Nothing here has run against a real Snowflake account or Alteryx: SQL runs on the DuckDB double,
Snowpark in the Local Testing Framework, dbt on dbt-duckdb.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path
from typing import Sequence

import compare
import validate_dbt
import validate_snowpark as vsp
from lib import handoff, validation as v
from lib.backend import SANDBOX_DB, BackendError, DuckDBBackend
from lib.dbt_project import DbtUnavailable
from lib.io import load_manifest, read_json, read_yaml
from lib.paths import Repo, add_root_arg
from lib.proc_runner import ProcError, run_proc
from lib.typed_csv import read_table
from lib.validation import ChainError
from load_golden import GOLDEN_SCHEMA, check_names, golden_view_schema, load_set

BACKENDS = v.BACKENDS

__all__ = ["ChainError", "validate_workflow", "main"]


# --- prerequisites ----------------------------------------------------------------------------------


def _prerequisites(repo: Repo, wf_id: str, golden_sets: Sequence[str] | None, database: str = SANDBOX_DB) -> dict:
    """Everything the chain needs, checked before anything runs: `FileNotFoundError`/`ValueError`
    (a usage error) for a missing `segments/order.json`, an empty order, a segment with no
    `contract.json` / no `outputs[]` / a `manual` target / no procedure for its target, no
    `intake/mappings.yaml`, or no golden sets."""
    order_path = repo.wf(wf_id, "segments", "order.json")
    if not order_path.is_file():
        raise FileNotFoundError(f"{wf_id} has no segments/order.json to chain: {order_path}; "
                                f"segment this workflow first")
    order = read_json(order_path)
    segments = [seg for wave in order for seg in wave]
    if not segments:
        raise ValueError(f"{wf_id}: segments/order.json lists no segments to chain")

    contracts: dict[str, dict] = {}
    procs: dict[str, Path] = {}
    dags: dict[str, dict | None] = {}
    for seg in segments:
        contract_path = repo.seg(wf_id, seg, "contract.json")
        if not contract_path.is_file():
            raise FileNotFoundError(f"{wf_id}/{seg} has no contract.json to validate against: "
                                    f"{contract_path}; translate this segment first")
        contract = read_json(contract_path)
        if not contract.get("outputs"):
            raise ValueError(f"{wf_id}/{seg} contract.json has no outputs[] to validate (plan "
                             f"contract C5 requires at least one); translate this segment first")
        target = contract.get("target") or "sql"
        if target not in ("sql", "snowpark"):
            raise ValueError(f"{wf_id}/{seg} has target {target!r}: only sql and snowpark segments "
                             f"can run in a chain")
        proc_file = repo.seg(wf_id, seg, "proc.py" if target == "snowpark" else "proc.sql")
        if not proc_file.is_file():
            raise FileNotFoundError(f"{wf_id}/{seg} has no procedure to chain: {proc_file}; "
                                    f"translate this segment first")
        dag_path = repo.seg(wf_id, seg, "dag.json")
        contracts[seg], procs[seg] = contract, proc_file
        dags[seg] = read_json(dag_path) if dag_path.is_file() else None

    mappings_path = repo.wf(wf_id, "intake", "mappings.yaml")
    if not mappings_path.is_file():
        raise FileNotFoundError(f"{wf_id} has no intake/mappings.yaml to load golden data against: "
                                f"{mappings_path}; run intake first: python scripts/intake_prompt.py {wf_id}")

    manifest = load_manifest(repo, wf_id)
    sets = list(golden_sets) if golden_sets is not None else list(manifest.get("golden_sets") or [])
    if not sets:
        raise ValueError(f"{wf_id} has no golden sets to chain (manifest.json's golden_sets is empty "
                         f"and none were given with --set); run dev/build_samples.py or "
                         f"inject_outputs.py first")

    # Every name spliced into SQL (or used as a Snowpark table name) from the contracts or the
    # mappings, checked before any backend exists (fix round 1, C2).
    for seg in segments:
        v.check_contract_names(wf_id, seg, contracts[seg], database)
    check_names(repo, wf_id, sets, database=database)

    raw = (read_yaml(repo.global_mappings) if repo.global_mappings.is_file() else {}) or {}
    mappings = read_yaml(mappings_path) or {}
    return {
        "order": order, "contracts": contracts, "procs": procs, "dags": dags, "sets": sets,
        "sources": [source["logical"] for source in (mappings.get("sources") or {}).values()
                    if source.get("logical")],
        "tolerances": raw.get("tolerances") or compare.DEFAULT_TOLERANCES,
        "accepted": raw.get("accepted_diff_classes") or (),
        "approvals": manifest.get("accepted_diffs") or [],
    }


# --- row order: physical on the judged run, reversed on the idempotency re-run ------------------------
#
# A Snowflake table has no row order, so a procedure that depends on the order its input arrives in
# is non-deterministic in production even when every local run agrees. The first chain run leaves
# every table in the order the local engine wrote it and hands rows across a Snowpark seam in that
# same physical order (never sorted: a sort would present one convenient order and hide the
# dependence). The idempotency re-run then presents every segment's every input in an order
# different from the one that segment saw on the first run -- the raw golden inputs reversed, and
# each upstream stream or target reversed UNLESS it already arrives as the exact reverse of the
# first run's order -- so an order-dependent segment computes something else and the two runs
# differ (Task W1 fix rounds 1 and 3, rulings I2 and R1). Physical order and `rowid` are the DuckDB
# double's; they prove nothing about a real account's order.

#: The exact reverse of what one segment saw of one input on the first run: `(segment, relation) -> digest`.
OrderSeen = dict[tuple[str, str], str]


def _digest(rows: list) -> str:
    """A digest of a row list IN ORDER -- equal only for the same rows in the same order."""
    return hashlib.sha256(repr([tuple(row) for row in rows]).encode("utf-8")).hexdigest()


#: Rewrites a table with its rows in reversed physical order, types kept (shared with validate_dbt.py).
_reverse = v.reverse_physical_order


def _present(seg: str, fqn: str, rows: list, seen: OrderSeen, rerun: bool) -> bool:
    """Records (first run) or checks (re-run) the order `seg` is about to see `fqn` in. The first
    run records the digest of the exact REVERSE of what it saw. On the re-run, True -- reverse it --
    unless the rows already arrive as that exact reverse (an order-preserving upstream passed its own
    reversed input on, and reversing again would restore the first run's order). Anything else --
    the first run's order, or only a PARTLY different one (an upstream sort on a non-unique key, a
    Filter's branches unioned back) -- is reversed, so whatever it arrives as, the segment is shown
    an order other than the first run's (fix round 3, R1: "reverse only if unchanged" let a partial
    reorder through to a first-N consumer)."""
    if not rerun:
        seen[(seg, fqn)] = _digest(list(reversed(rows)))
        return False
    return seen.get((seg, fqn)) != _digest(rows)


def _order_sensitive_inputs(backend, wf_id: str, seg: str, contract: dict) -> list[str]:
    """What a segment reads besides the raw golden inputs: every upstream stream it declares, and
    the current state of every target it writes into (an append or merge target)."""
    tables = [entry["table"] for entry in contract.get("inputs") or [] if entry.get("stream") and entry.get("table")]
    tables += [v.actual_table(wf_id, seg, output) for output in contract.get("outputs") or []
               if output.get("kind") == "target"]
    return [fqn for fqn in dict.fromkeys(tables) if backend.table_exists(fqn)]


# --- one chain run ---------------------------------------------------------------------------------


def _hand_in(session, backend, seg: str, fqn: str, seen: OrderSeen, rerun: bool) -> None:
    table = handoff.table_from_backend(backend, fqn)
    if _present(seg, fqn, table["rows"], seen, rerun):
        table["rows"].reverse()
    handoff.load_into_snowpark(session, fqn, table)


def _run_snowpark_segment(backend, repo: Repo, wf_id: str, seg: str, golden_set: str, contract: dict,
                          proc_path: Path, args: dict, plan: dict, seen: OrderSeen, rerun: bool) -> None:
    """A Snowpark segment inside the chain: a fresh local session with the raw golden inputs and
    `targets_before` (a missing golden file is a usage error and propagates as one), then -- the
    part a `ChainError` covers -- its declared upstream streams and its targets' current chain
    state handed in from `backend`, `run()`, and every declared output handed back with the schema
    Snowpark really wrote. An output Snowpark no longer has is dropped from `backend` too, so it is
    judged as missing, never as the copy `backend` held before the segment ran (fix round 1, I3).
    On the re-run every input arrives reversed (see `_present`)."""
    session = vsp.new_local_session()
    try:
        vsp.load_set_snowpark(session, repo, wf_id, golden_set)
        try:
            if rerun:                                                # raw golden inputs, reversed
                for logical in plan["sources"]:
                    fqn = f"{args['SRC_DB']}.{args['SRC_SCHEMA']}.{logical}"
                    table = handoff.table_from_snowpark(session, fqn)
                    if table is not None:
                        table["rows"].reverse()
                        handoff.load_into_snowpark(session, fqn, table)
            for fqn in _order_sensitive_inputs(backend, wf_id, seg, contract):   # ACTUAL chain state
                _hand_in(session, backend, seg, fqn, seen, rerun)
            module = vsp.load_module(proc_path)
            module.run(session, args["SRC_DB"], args["SRC_SCHEMA"], args["TGT_DB"], args["TGT_SCHEMA"],
                       args["RUN_ID"])
            for output in contract.get("outputs") or []:
                fqn = v.actual_table(wf_id, seg, output)
                table = handoff.table_from_snowpark(session, fqn)   # the REAL written schema
                if table is None:
                    backend.execute(f"DROP TABLE IF EXISTS {fqn}")
                else:
                    handoff.load_into_backend(backend, fqn, table)
        except Exception as exc:  # noqa: BLE001 -- anything run() or the seam raises is a domain FAIL
            raise ChainError(seg, exc) from exc
    finally:
        session.close()


def _run_sql_segment(backend, wf_id: str, seg: str, contract: dict, proc_path: Path, args: dict,
                     seen: OrderSeen, rerun: bool) -> None:
    try:
        # Presenting the inputs is part of this segment's step: a relation that cannot be read or
        # reversed is reported at this segment, never an uncaught crash (fix round 3, R2).
        for fqn in _order_sensitive_inputs(backend, wf_id, seg, contract):
            if _present(seg, fqn, backend.query(f"SELECT * FROM {fqn}")[1], seen, rerun):
                _reverse(backend, fqn)
    except Exception as exc:  # noqa: BLE001 -- reported as this segment's divergence, with the error
        raise ChainError(seg, exc) from exc
    try:
        run_proc(backend, proc_path.read_text(encoding="utf-8"), args)
    except (ProcError, BackendError) as exc:
        raise ChainError(seg, exc) from exc


def _judge(backend, repo: Repo, wf_id: str, seg: str, golden_set: str, output: dict, index: int,
           plan: dict, database: str = SANDBOX_DB) -> dict:
    """One output of one segment against its golden file, exactly as `validate_segment` judges it
    (the same `compare.compare` arguments), in the chain's own backend."""
    expected = f"{v.EXPECTED_SCHEMA}.CHAIN_EXPECTED_{index}"
    backend.load_table(expected, read_table(v.golden_path(repo, wf_id, seg, golden_set, output)))
    actual = v.actual_table(wf_id, seg, output, database)
    if backend.table_exists(actual):
        report = compare.compare(
            backend, expected, actual, plan["contracts"][seg], plan["tolerances"], output=output,
            accepted_classes=plan["accepted"],
            approvals=[a for a in plan["approvals"] if a.get("segment") == seg],
            segment_dag=plan["dags"][seg], golden_set=golden_set)
    else:
        report = v.missing_table_report(actual)
    target = output.get("kind") == "target"
    return {"segment": seg, "stream": output.get("stream"), "kind": "target" if target else "work",
            "output": output.get("logical") if target else output.get("table"),
            "relation": actual, "report": report}


def _run_chain(repo: Repo, wf_id: str, golden_set: str, plan: dict, seen: OrderSeen, *,
               rerun: bool = False) -> tuple[list[dict], DuckDBBackend]:
    """One pass from the raw golden inputs: every segment in wave order on the actual output of its
    upstream segments, every output judged right after its segment ran. The first run records the
    order each segment saw its inputs in (`seen`); the re-run (`rerun=True`) presents them reversed.
    Returns `(entries, backend)`; the caller closes the backend. A segment that raises becomes a
    `ChainError` carrying the entries judged before it (`.entries`), with the backend closed."""
    backend = DuckDBBackend()
    entries: list[dict] = []
    try:
        info = load_set(backend, repo, wf_id, golden_set)
        if rerun:                     # the base tables behind the raw-input views, reversed once
            first_segment = plan["order"][0][0]
            for fqn in info["loaded"]:
                if fqn.startswith(f"{GOLDEN_SCHEMA}."):
                    try:
                        _reverse(backend, fqn)
                    except Exception as exc:  # noqa: BLE001 -- the re-run stopped before its first segment
                        raise ChainError(first_segment, exc) from exc
        args = {**info["args"], "RUN_ID": f"validate_workflow_{wf_id}_{golden_set}"}
        for wave in plan["order"]:
            for seg in wave:
                contract = plan["contracts"][seg]
                if contract.get("target") == "snowpark":
                    _run_snowpark_segment(backend, repo, wf_id, seg, golden_set, contract, plan["procs"][seg],
                                          args, plan, seen, rerun)
                else:
                    _run_sql_segment(backend, wf_id, seg, contract, plan["procs"][seg], args, seen, rerun)
                for output in contract.get("outputs") or []:
                    entries.append(_judge(backend, repo, wf_id, seg, golden_set, output, len(entries), plan))
    except ChainError as exc:
        exc.entries = entries
        backend.close()
        raise
    except BaseException:
        backend.close()
        raise
    return entries, backend


def _relations(entries: list[dict]) -> list[str]:
    return list(dict.fromkeys(entry["relation"] for entry in entries))


# --- the chain on a real account (plan Task P2) --------------------------------------------------------
#
# One engine: every segment runs in the SAME sandbox database -- a SQL segment's procedure is created
# and CALLed there, a Snowpark segment runs in a session built from the same named connection, where
# its upstream tables already are -- so nothing crosses a seam and `lib.handoff` is never used. A real
# account has no physical row order to reverse, so the idempotency re-run perturbs the only order it
# controls: the raw golden inputs and `targets_before` are INSERTed in reversed order (ruling 2). That
# is weaker than the local reversal -- Snowflake promises no scan order either way, and nothing is
# re-presented between two segments -- and documented as such.


def _run_snowpark_segment_on_snowflake(sandbox, seg: str, proc_path: Path, args: dict) -> None:
    from lib import snowflake_conn  # noqa: PLC0415  (lazy: never on the local path)
    session = snowflake_conn.snowpark_session(sandbox.connection_name)
    try:
        try:
            session.sql(f"USE DATABASE {sandbox.database}").collect()
        except Exception as exc:  # noqa: BLE001 -- the session, not the segment: redacted, a crash
            raise snowflake_conn.scrubbed(exc) from None
        try:
            module = vsp.load_module(proc_path)
            module.run(session, args["SRC_DB"], args["SRC_SCHEMA"], args["TGT_DB"], args["TGT_SCHEMA"],
                       args["RUN_ID"])
        except Exception as exc:  # noqa: BLE001 -- anything run() raises is this segment's domain FAIL
            raise ChainError(seg, snowflake_conn.scrubbed(exc)) from None
    finally:
        session.close()


def _run_chain_on_snowflake(repo: Repo, wf_id: str, golden_set: str, plan: dict, sandbox, *,
                            rerun: bool = False) -> tuple[list[dict], object]:
    """`_run_chain` on a real account: ONE fresh sandbox per pass, every segment in wave order in it,
    every output judged right after its segment ran. The re-run loads the raw inputs in reversed
    INSERT order. Returns `(entries, backend)`; the caller closes the backend."""
    backend = sandbox.fresh([golden_view_schema(wf_id, golden_set)])
    entries: list[dict] = []
    try:
        info = load_set(backend, repo, wf_id, golden_set, database=sandbox.database, reverse=rerun)
        args = {**info["args"], "RUN_ID": f"validate_workflow_{wf_id}_{golden_set}"}
        for wave in plan["order"]:
            for seg in wave:
                contract = plan["contracts"][seg]
                if contract.get("target") == "snowpark":
                    _run_snowpark_segment_on_snowflake(sandbox, seg, plan["procs"][seg], args)
                else:
                    try:
                        backend.call_procedure(plan["procs"][seg].read_text(encoding="utf-8"), args)
                    except (ProcError, BackendError) as exc:
                        raise ChainError(seg, exc) from exc
                for output in contract.get("outputs") or []:
                    entries.append(_judge(backend, repo, wf_id, seg, golden_set, output, len(entries), plan,
                                          sandbox.database))
    except ChainError as exc:
        exc.entries = entries
        backend.close()
        raise
    except BaseException:
        backend.close()
        raise
    return entries, backend


def _idempotency_on_snowflake(repo: Repo, wf_id: str, golden_set: str, plan: dict, entries: list[dict],
                              backend, sandbox) -> tuple[bool, list[str], ChainError | None]:
    """`_idempotency` on a real account: the first pass's relations are snapshotted BEFORE the re-run's
    fresh sandbox replaces them (both passes share one sandbox database)."""
    relations = _relations(entries)
    first = v.snapshot(backend, relations)
    try:
        _, other = _run_chain_on_snowflake(repo, wf_id, golden_set, plan, sandbox, rerun=True)
    except ChainError as exc:
        return False, relations, exc
    try:
        diverging = v.diverging_tables(first, v.snapshot(other, relations), relations)
    finally:
        other.close()
    return not diverging, diverging, None


def _idempotency(repo: Repo, wf_id: str, golden_set: str, plan: dict, entries: list[dict],
                 backend: DuckDBBackend, seen: OrderSeen) -> tuple[bool, list[str], ChainError | None]:
    """A second, independent chain run from a fresh backend with every segment's inputs presented
    in reversed order, every relation the first run judged compared as a row multiset. A relation
    missing on either side counts as diverging; a second run that raises returns that `ChainError`
    (the raising segment is where the chain stopped being reproducible -- fix round 1, M2) with
    every relation diverging: idempotency is never `True` without both runs actually compared."""
    relations = _relations(entries)
    try:
        _, other = _run_chain(repo, wf_id, golden_set, plan, seen, rerun=True)
    except ChainError as exc:
        return False, relations, exc
    try:
        diverging = [relation for relation in relations
                     if not (backend.table_exists(relation) and other.table_exists(relation))
                     or v.ordered_rows(backend, relation) != v.ordered_rows(other, relation)]
    finally:
        other.close()
    return not diverging, diverging, None


def _validate_dbt_workflow(repo: Repo, wf_id: str, golden_sets: Sequence[str] | None, **engine) -> dict:
    validate_dbt.validate_dbt(repo, wf_id, golden_sets, **engine)
    return read_json(repo.wf(wf_id, "validation_workflow.json"))


def validate_workflow(repo: Repo, wf_id: str, golden_sets: Sequence[str] | None = None, *,
                      backend: str = "duckdb", connection: str | None = None,
                      sandbox_database: str | None = None) -> dict:
    """Runs the chain for every named golden set (default `manifest.golden_sets`), writes
    `validation_workflow.<set>.json` and `validation_workflow.json` at the workflow root, and
    returns the top-level report. Every stale chain report is deleted before anything else; a
    usage error raises `FileNotFoundError`/`ValueError` and leaves none behind.

    `backend="snowflake"` (plan Task P2) runs every pass in one fresh sandbox on a real account,
    through the `connection` named and in the `sandbox_database` the policy lists."""
    v.clear_stale_workflow_reports(repo, wf_id)
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}: expected one of {', '.join(BACKENDS)}")
    sandbox = v.snowflake_target(repo, backend, connection, sandbox_database)    # nothing connects yet
    if load_manifest(repo, wf_id).get("output_kind") == "dbt":
        return _validate_dbt_workflow(repo, wf_id, golden_sets, backend=backend, connection=connection,
                                      sandbox_database=sandbox_database)

    plan = _prerequisites(repo, wf_id, golden_sets, SANDBOX_DB if sandbox is None else sandbox.database)
    reports: dict[str, dict] = {}
    idempotent: tuple[bool | None, list[str]] = (None, [])
    for index, golden_set in enumerate(plan["sets"]):
        started = time.perf_counter()
        seen: OrderSeen = {}
        try:
            entries, chain = (_run_chain(repo, wf_id, golden_set, plan, seen) if sandbox is None
                              else _run_chain_on_snowflake(repo, wf_id, golden_set, plan, sandbox))
        except ChainError as exc:
            report = v.chain_report(wf_id, golden_set, exc.entries, exc)
        else:
            try:
                diverging: list[str] = []
                rerun_error: ChainError | None = None
                if index == 0:
                    is_idempotent, diverging, rerun_error = (
                        _idempotency(repo, wf_id, golden_set, plan, entries, chain, seen) if sandbox is None
                        else _idempotency_on_snowflake(repo, wf_id, golden_set, plan, entries, chain, sandbox))
                    idempotent = (is_idempotent, diverging)
                report = v.chain_report(wf_id, golden_set, entries, diverging=diverging, rerun_error=rerun_error)
            finally:
                chain.close()
        report["runtime_ms"] = int(round((time.perf_counter() - started) * 1000))
        reports[golden_set] = report
    return v.write_workflow_reports(repo, wf_id, plan["sets"], reports, idempotent)


# --- CLI --------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0003")
    parser.add_argument("--set", dest="sets", action="append", default=None,
                        help="golden set to chain (repeatable); default: manifest.golden_sets")
    v.add_backend_args(parser)
    add_root_arg(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo = Repo(args.root)
    try:
        report = validate_workflow(repo, args.wf_id, args.sets, backend=args.backend, connection=args.connection,
                                   sandbox_database=args.sandbox_database)
    except (FileNotFoundError, ValueError, DbtUnavailable) as exc:
        parser.error(v.error_text(args.backend, str(exc)))  # exit 2: a missing prerequisite; nothing was written
    except Exception:            # noqa: BLE001 -- exit 2 is "anything unexpected"; never a bare traceback exit 1
        v.print_crash(args.backend)
        return 2

    where = report.get("first_divergence")
    divergence = (f"; {report['divergence_kind']} at {where['segment']}/{where['stream']} "
                  f"({where['output']}) on {where['set']}") if where else ""
    print(f"{args.wf_id}: chain {report['verdict']} {report['sets']} "
          f"(idempotent={report['idempotent']}){divergence}")
    return 0 if report["verdict"].startswith("PASS") else 1


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
