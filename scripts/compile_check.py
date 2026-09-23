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
1. the text is the supported procedure shape (plan contract C4), including the one table-reference
   form contract C4 allows (Task C4V), as two named checks: `c4:let_form` -- every `LET` is
   `LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>'` or its `TGT` twin
   (`<LOGICAL>_TGT`, `TGT_DB`, `TGT_SCHEMA`), the arguments named WITHOUT a colon (a colon there is
   refused by name: Snowflake's Scripting documentation uses the colon to bind a variable inside a
   SQL statement, not in an expression) -- and `c4:identifier_expression` -- the argument of every
   `IDENTIFIER(…)` is one `:<VAR>` a `LET` of this procedure declares (with the colon: it is inside
   a SQL statement), one string literal or one session variable, never an expression. Snowflake
   documents `IDENTIFIER( { string_literal | session_variable | bind_variable |
   snowflake_scripting_variable } )`, a single value; the concatenation inside `IDENTIFIER(…)` these
   procedures once used is not in that grammar. The documented form has not run on Snowflake
   either: the first real-account run confirms it. A comment inside a `LET` is a `c4:let_form`
   too (one in front of it is fine), and `c4:return_form` (fix round 2) holds the body to one
   `RETURN '<string literal>'`, as its last statement -- the local double never runs a RETURN, and
   would run statements Snowflake never reaches after one. `c4:identifier_role` (fix round 3): a
   `<LOGICAL>_SRC` name is only ever read (FROM/JOIN/USING) and a `<LOGICAL>_TGT` name only ever
   written (CREATE … TABLE, INSERT/MERGE INTO, UPDATE, DELETE FROM, TRUNCATE). Check 0 also requires every parameter to
   be declared exactly `<NAME> STRING` (no other type, no DEFAULT), the one header the orchestrator's
   SQL policy accepts;
2. every statement parses with sqlglot's Snowflake parser and is not a `Command` fallback, which
   is what sqlglot produces when it does not actually understand a statement;
3. empty tables are created for each `contract.inputs[]` — mapped sources under
   `MIGDB.MIG_COMPILE`, upstream segment tables at the literal name the contract gives — and for
   each `outputs[]` of kind `target` under `MIGDB.MIG_WORK`;
4. the procedure runs against them;
5. each `outputs[]` of kind `work` exists afterwards with the contract's column names, compared
   case-insensitively and in order.

That six-check sequence is the `sql` target: contract C4's Snowflake Scripting subset. A `snowpark`
target (`contract.json`'s `"target"`, or `--target`) is a different shape and gets a different,
cheaper gate instead — nothing here runs a Snowpark procedure locally (there is no local Snowpark
runtime to run it on): `proc.py` is checked against `lib.snowpark_rules.check_proc_py` (spec §4.2's
AST-walk rules: allowed imports, no session.sql/call, no exec/eval/open, the C4 signature, the
table-name allow-list, per-tool comments), and `proc.sql` is checked for staying in sync with
`render_snowpark.render(proc.py, …)` plus its own C4 signature.

The `dbt` target (`compile_check.py <wf> --target dbt`, no segment: a dbt project is one unit) is a
third, different shape again: `dbt parse` (the one dbt invocation, `lib.dbt_project.run_dbt`) builds
a manifest and nothing here runs a model -- and only once the project is inside the closed dbt surface
(`lib.dbt_project.check_surface`, final fix wave C1: `dbt:surface` the closed file set, `dbt:project_yml` the
template project file, `dbt:yaml` the two closed YAML files, `dbt:hook_sql` one plain statement against `{{ this }}`
per hook, `dbt:model_sql` one query per model reading only through `source()`/`ref()`/`{{ this }}`, plus
`dbt:profiles` and `dbt:model_jinja` below); any surface error and `dbt parse` is not run at all. Sixteen named
checks, each `dbt:<name>` in `compile_check.json`'s `errors`: those five, and `dbt:layout` (the project files `lib.dbt_project.PROJECT_FILES`
names, plus `dbt_project.yml`'s own name/profile/model-paths/vars), `dbt:profiles` (`profiles.yml`
byte-identical to `PROFILES_TEMPLATE` — no credential is ever accepted), `dbt:sources`
(`models/sources.yml` declares exactly the sources `intake/mappings.yaml` maps, with the contract's
own input columns), `dbt:model_jinja` (a model's Jinja is outside the closed allow-list of design
§4.3 — `config`/`source`/`ref`/`this`/`is_incremental()` if/else/endif/comments, nothing else, not
even nested inside one of those), `dbt:parse` (dbt parse itself failed), `dbt:model_missing` (a
contract output with no model), `dbt:model_orphan` (a model that is not any contract output's),
`dbt:model_config` (a model's `materialized`/`incremental_strategy`/`unique_key`/`alias` does not
match its write mode), `dbt:columns` (`models/schema.yml`'s columns do not match the contract's, in
order), `dbt:tool_comments` (a data node with no `-- tool <id>` comment in any model) and
`dbt:hooks` (a node with non-blank PreSQL/PostSQL whose model has no matching, non-blank
`pre-hook`/`post-hook`). Writes `dbt/compile_check.json`.

Writes `segments/<seg>/compile_check.json` (`workflows/<wf>/dbt/compile_check.json` for the `dbt`
target). Exit 0 on OK, 1 on a compile error, 2 when there is nothing to check. Nothing here has run
on a Snowflake account: passing means DuckDB accepted the translated SQL (`sql` target), the AST
rules and the renderer agreed (`snowpark` target), or dbt-duckdb accepted the project (`dbt`
target).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

import render_snowpark
from lib import dbt_project, dbt_surface
from lib.backend import SANDBOX_DB, BackendError, DuckDBBackend
from lib.io import read_json, read_yaml, write_json
from lib.paths import Repo, add_root_arg, seg_token, wf_token
from lib.proc_runner import (LetError, ProcError, ProcInfo, bind, identifier_arguments, identifier_calls,
                              let_values, parse_proc, run_proc)

# Fix round 2 (M1): the one RETURN a body may have -- a single string literal (quotes doubled or
# backslash-escaped inside), nothing else.
# Fix round 3 (c4:identifier_role): the words in front of an IDENTIFIER(…) that make it a write target
# or a source read. Modifiers between CREATE/TRUNCATE and the name are skipped (`CREATE OR REPLACE
# TRANSIENT TABLE`, `TRUNCATE TABLE IF EXISTS`).
_ROLE_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*|[(),;]")
_TABLE_MODIFIERS = frozenset({"OR", "REPLACE", "TRANSIENT", "TEMPORARY", "TEMP", "VOLATILE", "LOCAL", "GLOBAL",
                              "TABLE", "IF", "NOT", "EXISTS"})
_RETURN_LITERAL_RE = re.compile(r"^RETURN\s*'(?:[^'\\]|''|\\.)*'$", re.IGNORECASE | re.DOTALL)
from lib.snowpark_rules import check_proc_py
from lib.vocab import DATA_LESS_TYPES

COMPILE_SCHEMA = "MIG_COMPILE"
WORK_SCHEMA = "MIG_WORK"
ARGS = {"SRC_DB": SANDBOX_DB, "SRC_SCHEMA": COMPILE_SCHEMA,
        "TGT_DB": SANDBOX_DB, "TGT_SCHEMA": WORK_SCHEMA, "RUN_ID": "compile_check"}

# Contract C4's procedure signature.
C4_PARAMS = ("SRC_DB", "SRC_SCHEMA", "TGT_DB", "TGT_SCHEMA", "RUN_ID")
C4_EXECUTE_AS = "CALLER"

# Contract C4's table-reference rule (Task C4V): one LET per mapped table, then IDENTIFIER(:<VAR>).
# `<LOGICAL>` has the shape intake gives a logical name (`intake_prompt._LOGICAL_RE`).
_C4_LET_RE = re.compile(
    r"^(?P<side>SRC|TGT)_DB\s*\|\|\s*'\.'\s*\|\|\s*(?P=side)_SCHEMA\s*\|\|\s*"
    r"'\.(?P<logical>[A-Z_][A-Z0-9_$]*)'$")
_C4_LET_RULE = ("LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>' for a source, "
                "LET <LOGICAL>_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.<LOGICAL>' for a target, "
                "the arguments named without a colon")
_IDENTIFIER_VARIABLE_RE = re.compile(r"^:(?P<name>[A-Za-z_][A-Za-z_0-9$]*)$")
_SESSION_VARIABLE_RE = re.compile(r"^\$[A-Za-z_][A-Za-z_0-9$]*$")
_STRING_LITERAL_RE = re.compile(r"^'(?:[^'\\]|''|\\.)*'$", re.DOTALL)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# Presence only — proc_runner reads the value. This tells "EXECUTE AS OWNER was written" from
# "the clause is missing", which both reach us as ProcInfo.execute_as == "OWNER".
_EXECUTE_AS_CLAUSE_RE = re.compile(r"\bEXECUTE\s+AS\b", re.IGNORECASE)


def compile_check(repo: Repo, wf_id: str, seg: str | None, target: str = "auto") -> dict:
    """Returns {"status": "OK"|"ERROR", "errors": [str], "statements": int} (plus `"target":
    "snowpark"` for that target) and writes it to compile_check.json.

    `target`: "auto" (default) resolves from contract.json's `"target"` field, defaulting to
    "sql" when that field is absent -- unchanged from before `--target` existed. "sql" or
    "snowpark" forces the corresponding check regardless of what the contract says. "dbt" checks
    the whole workflow's dbt project (DV6): `seg` is ignored -- a dbt project is one unit.
    """
    if target == "dbt":
        return compile_check_dbt(repo, wf_id)
    if seg is None:
        raise ValueError(f"{wf_id}: --target {target} needs a segment (only --target dbt does not)")

    contract_path = repo.seg(wf_id, seg, "contract.json")
    if not contract_path.exists():
        raise FileNotFoundError(f"{wf_id}/{seg} has no contract to check: {contract_path}")
    contract = read_json(contract_path)
    resolved = target if target != "auto" else (contract.get("target") or "sql")

    if resolved == "snowpark":
        report = _check_snowpark(repo, wf_id, seg, contract)
    else:
        proc_path = repo.seg(wf_id, seg, "proc.sql")
        if not proc_path.exists():
            raise FileNotFoundError(f"{wf_id}/{seg} has no procedure to check: {proc_path}")
        report = _check(proc_path.read_text(encoding="utf-8"), contract, wf_id, seg)
    write_json(repo.seg(wf_id, seg, "compile_check.json"), report)
    return report


# --- the dbt target -----------------------------------------------------------------------------


def compile_check_dbt(repo: Repo, wf_id: str) -> dict:
    """The dbt target's gate (design §5.1): the project stays inside the closed dbt surface
    (`dbt_project.check_surface`, final fix wave C1), is the template layout, `dbt parse` accepts it,
    and its manifest agrees with every contract -- a model per output and no model that isn't one,
    the config its write mode needs, the contract's columns in order, every mapped source, a
    `-- tool <id>:` comment per data node, a hook per PreSQL/PostSQL. Any surface error and
    `dbt parse` is not run at all. Writes dbt/compile_check.json.

    Prerequisites (each a `FileNotFoundError` naming its path, a usage error): the project's
    `dbt/dbt_project.yml`, `segments/order.json`, every segment's `contract.json`, and
    `intake/mappings.yaml`. A segment's `dag.json` is read when present."""
    project = dbt_project.project_dir(repo, wf_id)
    if not (project / "dbt_project.yml").is_file():
        raise FileNotFoundError(f"{wf_id} has no dbt project to check: {project / 'dbt_project.yml'}")
    order_path = repo.wf(wf_id, "segments", "order.json")
    if not order_path.is_file():
        raise FileNotFoundError(f"{wf_id} has no segments/order.json to check the project against: {order_path}")
    order = read_json(order_path)
    segments = [seg for wave in order for seg in wave]
    contracts = {}
    for seg in segments:
        path = repo.seg(wf_id, seg, "contract.json")
        if not path.is_file():
            raise FileNotFoundError(f"{wf_id}/{seg} has no contract to check the project against: {path}")
        contracts[seg] = read_json(path)
    mappings_path = repo.wf(wf_id, "intake", "mappings.yaml")
    if not mappings_path.is_file():
        raise FileNotFoundError(f"{wf_id} has no intake/mappings.yaml to check the project against: "
                                f"{mappings_path}; run intake first")
    mappings = read_yaml(mappings_path) or {}
    dags = {seg: (read_json(repo.seg(wf_id, seg, "dag.json")) if repo.seg(wf_id, seg, "dag.json").is_file()
                  else {"nodes": []}) for seg in segments}

    # The closed surface (dbt:surface, dbt:profiles, dbt:project_yml, dbt:yaml, dbt:model_jinja,
    # dbt:hook_sql, dbt:model_sql), dbt:layout and dbt:sources are all checked from the project's own
    # files, so they fire even when the project cannot parse at all (e.g. a renamed source breaks
    # every model that reads it, so dbt parse fails too -- both are real problems and both are
    # reported). A surface error means dbt never runs: not even `dbt parse`.
    surface = dbt_project.check_surface(project)
    errors = surface + _dbt_layout_errors(project, wf_id) + _dbt_source_errors(project, contracts, mappings)
    models = 0
    if not surface:
        with tempfile.TemporaryDirectory(prefix="dbt-compile-") as tmp:
            result = dbt_project.run_dbt("parse", project,
                                         vars=dbt_project.local_vars(dbt_project.COMPILE_SRC_SCHEMA),
                                         duckdb_path=Path(tmp) / "dbt_sandbox_compile.duckdb")
        if result.code != 0 or result.manifest is None:
            errors.append(f"dbt:parse: dbt parse exited {result.code}: "
                          f"{dbt_project.bounded(dbt_project.tail(result.output))}")
        else:
            nodes = {n["name"]: n for n in result.manifest.get("nodes", {}).values()
                    if n.get("resource_type") == "model"}
            models = len(nodes)
            errors += _dbt_model_errors(nodes, contracts, mappings)
            errors += _dbt_orphan_errors(nodes, contracts)
            errors += _dbt_tool_comment_errors(project, dags)
            errors += _dbt_hook_errors(nodes, dags, mappings)
    report = {"status": "ERROR" if errors else "OK", "target": "dbt", "errors": sorted(set(errors)),
              "statements": 0, "models": models}
    report_path = project / "compile_check.json"
    if dbt_surface.is_link(report_path):               # dbt:surface refused it; never write through it
        try:
            report_path.unlink()
        except OSError:
            os.rmdir(report_path)                    # a junction or a directory symlink
    write_json(report_path, report)
    return report


def _dbt_layout_errors(project: Path, wf_id: str) -> list[str]:
    """Every `dbt_project.PROJECT_FILES` entry exists, and `dbt_project.yml` itself declares the
    name, profile, model-paths and vars a project of this shape must have."""
    errors = [f"dbt:layout: missing {rel}" for rel in dbt_project.PROJECT_FILES
             if not (project / rel).is_file()]
    project_yml = project / "dbt_project.yml"
    if not project_yml.is_file():
        return errors
    doc = dbt_surface.load_yaml(project_yml)
    if not isinstance(doc, dict):
        return errors                                # dbt:project_yml says why
    if doc.get("name") != wf_id:
        errors.append(f"dbt:layout: dbt_project.yml name={doc.get('name')!r}; expected {wf_id!r}")
    if doc.get("profile") != dbt_project.PROFILE:
        errors.append(f"dbt:layout: dbt_project.yml profile={doc.get('profile')!r}; "
                      f"expected {dbt_project.PROFILE!r}")
    if doc.get("model-paths") != ["models"]:
        errors.append(f"dbt:layout: dbt_project.yml model-paths={doc.get('model-paths')!r}; "
                      f"expected ['models']")
    var_keys = set(doc["vars"]) if isinstance(doc.get("vars"), dict) else set()
    if not {"src_schema", "tgt_schema"} <= var_keys:
        errors.append(f"dbt:layout: dbt_project.yml vars={doc.get('vars')!r}; "
                      f"expected a mapping with src_schema and tgt_schema")
    return errors


def _dbt_source_errors(project: Path, contracts: dict, mappings: dict) -> list[str]:
    """`models/sources.yml`'s `src` source must declare exactly the logical names
    `intake/mappings.yaml` maps, each with the columns of any contract input carrying that logical
    name (upper-cased, in order). Read from the project's own YAML rather than dbt's manifest, so
    this fires even when a renamed source also breaks `dbt parse` for every model that reads it."""
    sources_path = project / "models" / "sources.yml"
    if not sources_path.is_file():
        return []  # already reported by _dbt_layout_errors
    doc = dbt_surface.load_yaml(sources_path)
    declared: dict[str, list[str]] = {}
    sources = doc.get("sources") if isinstance(doc, dict) else None
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict) or source.get("name") != "src":
            continue
        tables = source.get("tables")
        for table in tables if isinstance(tables, list) else []:
            if not isinstance(table, dict):
                continue
            columns = table.get("columns")
            declared[str(table.get("name"))] = [str(c.get("name")).upper() for c in
                                                 (columns if isinstance(columns, list) else []) if isinstance(c, dict)]

    expected_names = {str(s["logical"]) for s in (mappings.get("sources") or {}).values() if s.get("logical")}
    actual_names = set(declared)
    errors = []
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        detail = "; ".join(part for part in (
            f"missing {missing}" if missing else "", f"undeclared extra {extra}" if extra else "") if part)
        errors.append(f"dbt:sources: models/sources.yml declares {sorted(actual_names)}; "
                      f"intake/mappings.yaml maps {sorted(expected_names)} ({detail})")

    for contract in contracts.values():
        for source in contract.get("inputs") or []:
            logical = source.get("logical")
            if not logical or logical not in declared:
                continue
            expected_cols = [c["name"].upper() for c in source.get("columns") or []]
            if declared[logical] != expected_cols:
                errors.append(f"dbt:sources: models/sources.yml lists {declared[logical]} for "
                              f"{logical}; the contract declares {expected_cols}")
    return errors


# --- M3 (fix round 1): model SQL is a closed Jinja surface ---------------------------------------
#
# Now part of the closed dbt surface (final fix wave C1, `lib.dbt_surface`), which `run_dbt` itself
# enforces. `_dbt_jinja_errors` keeps its name for the agent-file pins in tests/test_agents_config.py.


def _dbt_jinja_errors(project: Path) -> list[str]:
    """Every model file's Jinja is the closed allow-list of design §4.3, no more (M3, fix round
    1; `config(...)`'s keyword arguments closed by final fix wave C1.5): only the `dbt:model_jinja`
    errors of `dbt_surface`."""
    return dbt_surface.model_jinja_errors(project)


def _dbt_model_errors(nodes: dict, contracts: dict, mappings: dict) -> list[str]:
    """Every contract output has a model; a work output's model is a `table`; a target output's
    model config matches `dbt_project.expected_model_config` for its mapped write mode; every
    model's `schema.yml` columns equal the contract's, upper-cased, in order."""
    errors: list[str] = []
    outputs_map = mappings.get("outputs") or {}
    for seg, contract in contracts.items():
        for output in contract.get("outputs") or []:
            name = dbt_project.model_name(output)
            node = nodes.get(name)
            if node is None:
                errors.append(f"dbt:model_missing: no model models/{name}.sql for {seg} output "
                              f"{output.get('stream')!r} ({output.get('kind')})")
                continue
            config = node.get("config") or {}
            if output.get("kind") == "work":
                if config.get("materialized") != "table":
                    errors.append(f"dbt:model_config: models/{name}.sql has "
                                  f"materialized={config.get('materialized')!r}; a work model needs "
                                  f"materialized='table'")
            else:
                errors.extend(_dbt_target_config_errors(name, config, output, outputs_map))
            expected_cols = [c["name"].upper() for c in output.get("columns") or []]
            actual_cols = [str(c).upper() for c in node.get("columns") or []]
            if actual_cols != expected_cols:
                errors.append(f"dbt:columns: models/schema.yml lists {actual_cols} for {name}; "
                              f"the contract declares {expected_cols}")
    return errors


def _dbt_orphan_errors(nodes: dict, contracts: dict) -> list[str]:
    """I2 (fix round 1): every model node the manifest parsed must be some contract output's model
    -- work stream or target alike. A leftover or invented `models/*.sql` that happens to build
    cleanly would otherwise deploy an untracked table with nothing to catch it."""
    known = {dbt_project.model_name(output) for contract in contracts.values()
            for output in contract.get("outputs") or []}
    return [f"dbt:model_orphan: models/{name}.sql is not a contract output"
           for name in nodes if name not in known]


def _dbt_target_config_errors(name: str, config: dict, output: dict, outputs_map: dict) -> list[str]:
    logical = output.get("logical")
    mapping = next((m for m in outputs_map.values() if m.get("logical") == logical), None)
    if mapping is None:
        return [f"dbt:model_config: no intake/mappings.yaml output has logical {logical}"]
    mode = mapping.get("mode")
    expected = dbt_project.expected_model_config(mode, mapping.get("keys") or [], logical)
    errors = []
    for key, value in expected.items():
        actual = config.get(key)
        if key == "unique_key" and actual is not None:
            actual = sorted(str(k).upper() for k in actual) if isinstance(actual, list) \
                else [str(actual).upper()]
        if actual != value:
            errors.append(f"dbt:model_config: models/{name}.sql has {key}={actual!r}; the {mode} "
                          f"write mode needs {key}={value!r}")
    return errors


def _dbt_tool_comment_errors(project: Path, dags: dict) -> list[str]:
    """Every data node (not in `DATA_LESS_TYPES`) of every segment needs a `-- tool <id>` comment
    somewhere in the project's models -- the same rule `render_snowpark`'s procedures follow,
    applied to SQL files instead of a Python AST."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(project.glob("models/**/*.sql")))
    errors = []
    for dag in dags.values():
        for node in dag.get("nodes") or []:
            if node.get("type") in DATA_LESS_TYPES:
                continue
            tool_id = str(node.get("tool_id"))
            if not re.search(rf"--\s*tool\s+{re.escape(tool_id)}\b", text):
                errors.append(f'dbt:tool_comments: no "-- tool {tool_id}:" comment in any model')
    return errors


_HOOK_LABELS = {"pre_sql": ("pre-hook", "pre_hook", "PreSQL"), "post_sql": ("post-hook", "post_hook", "PostSQL")}


def _dbt_hook_errors(nodes: dict, dags: dict, mappings: dict) -> list[str]:
    """A node with non-empty PreSQL/PostSQL needs its mapped model to carry a matching, non-empty
    `pre-hook`/`post-hook` (dbt's manifest key spelling)."""
    errors = []
    outputs_map = mappings.get("outputs") or {}
    for dag in dags.values():
        for node in dag.get("nodes") or []:
            config = node.get("config") or {}
            tool_id = str(node.get("tool_id"))
            for phase, (hook_key, hook_label, sql_label) in _HOOK_LABELS.items():
                if not config.get(phase):
                    continue
                mapping = next((m for m in outputs_map.values()
                               if tool_id in [str(t) for t in (m.get("tool_ids") or [])]), None)
                logical = mapping.get("logical") if mapping else None
                if not logical:
                    errors.append(f"dbt:hooks: no intake/mappings.yaml output maps tool {tool_id} "
                                  f"for its {sql_label}")
                    continue
                model_node = nodes.get(str(logical).lower())
                hooks = (model_node.get("config") or {}).get(hook_key) if model_node else None
                # dbt's manifest turns even pre_hook="" into [{"sql": "", ...}] -- a non-empty
                # list `if not hooks` alone would accept as "has a hook". It only really has one
                # if some entry's sql is more than blank (I1, fix round 1).
                if not hooks or not any(str(h.get("sql", "")).strip() for h in hooks):
                    errors.append(f"dbt:hooks: models/{str(logical).lower()}.sql has no {hook_label} "
                                  f"for tool {tool_id}'s {sql_label}")
    return errors


def _check(sql_text: str, contract: dict, wf_id: str, seg: str) -> dict:
    errors: list[str] = []
    try:
        proc = parse_proc(sql_text)
    except LetError as exc:
        return {"status": "ERROR", "errors": [_clean(f"c4:let_form: {exc}")], "statements": 0}
    except ProcError as exc:
        return {"status": "ERROR", "errors": [_clean(str(exc))], "statements": 0}

    signature = _signature_errors(proc, sql_text, wf_id, seg)
    if signature:
        # A wrong signature is not something the later checks can work around: the call would not
        # bind, so anything they reported would be a consequence, not a second problem.
        return {"status": "ERROR", "errors": signature, "statements": len(proc.statements)}

    table_references = table_reference_errors(proc) + identifier_role_errors(proc) + return_form_errors(proc)
    if table_references:
        # Nor is a table reference outside contract C4's form: it is refused whatever it would do
        # on the local double (which still folds the old expression form -- see proc_runner).
        return {"status": "ERROR", "errors": table_references, "statements": len(proc.statements)}

    values = {**ARGS, **let_values(proc, ARGS)}
    bound = []
    for statement in proc.statements:
        try:
            bound.append(bind(statement, values))
        except ProcError as exc:
            errors.append(_clean(str(exc)))
    if len(bound) == len(proc.statements):
        errors.extend(_parse_errors(bound))

    if not errors:
        errors.extend(_run_errors(sql_text, contract))
    return {"status": "ERROR" if errors else "OK", "errors": errors,
            "statements": len(proc.statements)}


def _check_snowpark(repo: Repo, wf_id: str, seg: str, contract: dict) -> dict:
    """The snowpark target's gate: proc.py against the AST rules, proc.sql against the renderer
    and its own C4 signature. No procedure runs locally -- there is no local Snowpark runtime."""
    proc_py_path = repo.seg(wf_id, seg, "proc.py")
    if not proc_py_path.exists():
        raise FileNotFoundError(f"{wf_id}/{seg} has no procedure module to check: {proc_py_path}")
    source = proc_py_path.read_text(encoding="utf-8")

    data_nodes = _data_nodes(repo, wf_id, seg)
    errors = list(check_proc_py(source, wf_id, seg, {**contract, "nodes": data_nodes}))
    errors.extend(_render_errors(repo, wf_id, seg, source))
    return {"status": "ERROR" if errors else "OK", "target": "snowpark",
            "errors": sorted(set(errors)), "statements": 0}


def _data_nodes(repo: Repo, wf_id: str, seg: str) -> list[dict]:
    """The segment's dag.json nodes, minus the ones with no data-transformation semantics of
    their own -- what snowpark_rules.check_proc_py's tool_comments rule walks. Empty (not an
    error) when dag.json does not exist: check_proc_py treats "no nodes at all" as leniently as
    "no contract.nodes at all"."""
    dag_path = repo.seg(wf_id, seg, "dag.json")
    if not dag_path.exists():
        return []
    dag = read_json(dag_path)
    return [node for node in dag.get("nodes") or [] if node.get("type") not in DATA_LESS_TYPES]


def _render_errors(repo: Repo, wf_id: str, seg: str, source: str) -> list[str]:
    """rule:render_mismatch when proc.sql is missing or does not match render(proc.py, …), plus
    proc.sql's own C4 signature check through parse_proc when it exists and parses."""
    proc_sql_path = repo.seg(wf_id, seg, "proc.sql")
    program = {}
    if repo.global_mappings.is_file():
        program = (read_yaml(repo.global_mappings) or {}).get("program") or {}
    runtime = str(program.get("snowpark_runtime") or "3.11")
    try:
        expected = render_snowpark.render(source, wf_id, seg, runtime)
    except ValueError as exc:
        return [f"rule:render_mismatch: proc.py cannot be rendered: {exc}"]

    if not proc_sql_path.exists():
        return [f"rule:render_mismatch: {proc_sql_path} does not exist; run render_snowpark.py "
               f"{wf_id} {seg}"]

    errors = []
    actual = proc_sql_path.read_text(encoding="utf-8")
    if actual != expected:
        errors.append(f"rule:render_mismatch: {proc_sql_path} does not match render(proc.py); "
                      f"run render_snowpark.py {wf_id} {seg} again")
    try:
        proc = parse_proc(actual)
    except ProcError as exc:
        errors.append(_clean(str(exc)))
    else:
        errors.extend(_signature_errors(proc, actual, wf_id, seg))
    return errors


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
    # Fix round 2 (c): every parameter is declared exactly `<NAME> STRING` -- no other type, no
    # DEFAULT -- which is also the only header the orchestrator's SQL policy accepts.
    for declaration in proc.param_declarations:
        name = declaration.split()[0]
        if declaration.upper() != f"{name.upper()} STRING":
            errors.append(f"parameter {declaration} found; contract C4 declares every parameter exactly "
                          f"as <NAME> STRING (no other type, no DEFAULT)")
    if proc.execute_as != C4_EXECUTE_AS:
        header = sql_text.split("$$", 1)[0]
        found = (f"EXECUTE AS {proc.execute_as} found" if _EXECUTE_AS_CLAUSE_RE.search(header)
                 else "no EXECUTE AS clause found (Snowflake defaults to owner's rights)")
        errors.append(f"{found}; contract C4 requires EXECUTE AS {C4_EXECUTE_AS}, because a "
                      f"procedure running with owner's rights cannot ALTER SESSION")
    return errors


def table_reference_errors(proc: ProcInfo) -> list[str]:
    """`c4:let_form` and `c4:identifier_expression` (Task C4V): every LET builds one mapped
    table's name by contract C4's rule, and every IDENTIFIER(…) takes one documented value."""
    errors = []
    for let in proc.lets:
        rule = _C4_LET_RE.match(let.expression)
        expected = f"{rule.group('logical')}_{rule.group('side')}" if rule else None
        if rule is None or let.name != expected:
            named = f"; this one must be named {expected}" if expected else ""
            errors.append(_clean(f"c4:let_form: {let.text} does not build a table name by contract "
                                 f"C4's rule ({_C4_LET_RULE}){named}"))
    declared = {let.name.upper() for let in proc.lets}
    for statement in proc.statements:
        for argument in identifier_arguments(statement):
            variable = _IDENTIFIER_VARIABLE_RE.match(argument)
            if variable is not None and variable.group("name").upper() in declared:
                continue
            if _STRING_LITERAL_RE.match(argument) or _SESSION_VARIABLE_RE.match(argument):
                continue
            what = ("names no variable a LET of this procedure declares" if variable is not None
                    else "takes an expression; Snowflake documents IDENTIFIER( with a single string "
                         "literal, session variable, bind variable or Snowflake Scripting variable")
            errors.append(_clean(f"c4:identifier_expression: IDENTIFIER({argument}) {what}. Build the "
                                 f"name first ({_C4_LET_RULE}) and write IDENTIFIER(:<LOGICAL>_SRC) or "
                                 f"IDENTIFIER(:<LOGICAL>_TGT)"))
    return errors


def identifier_role(code_before: str) -> str | None:
    """"write" when the IDENTIFIER(…) after `code_before` is a write target (CREATE … TABLE, INSERT /
    MERGE / COPY INTO, UPDATE, DELETE FROM, TRUNCATE), "read" when it is read as a source (FROM,
    JOIN, USING), None otherwise."""
    words = [word.upper() for word in _ROLE_WORD_RE.findall(code_before)]
    if not words:
        return None
    last, previous = words[-1], (words[-2] if len(words) > 1 else "")
    if last in ("INTO", "UPDATE"):
        return "write"
    if last == "FROM":
        return "write" if previous == "DELETE" else "read"
    if last in ("JOIN", "USING"):
        return "read"
    position = len(words) - 1
    while position >= 0 and words[position] in _TABLE_MODIFIERS:
        position -= 1
    if position >= 0 and words[position] in ("CREATE", "TRUNCATE"):
        return "write"
    return None


def identifier_role_errors(proc: ProcInfo) -> list[str]:
    """`c4:identifier_role` (fix round 3): a `<LOGICAL>_SRC` name is only ever read and a
    `<LOGICAL>_TGT` name only ever written -- a procedure that writes its source, or reads its
    target where contract C4 reads a source, is refused."""
    declared = {let.name.upper() for let in proc.lets}
    errors = []
    for statement in proc.statements:
        for code_before, argument in identifier_calls(statement):
            variable = _IDENTIFIER_VARIABLE_RE.match(argument)
            if variable is None or variable.group("name").upper() not in declared:
                continue
            name = variable.group("name").upper()
            role = identifier_role(code_before)
            if name.endswith("_SRC") and role == "write":
                errors.append(f"c4:identifier_role: IDENTIFIER({argument}) is written to; a <LOGICAL>_SRC name "
                              f"is only ever read -- write a target through its <LOGICAL>_TGT name")
            if name.endswith("_TGT") and role == "read":
                errors.append(f"c4:identifier_role: IDENTIFIER({argument}) is read as a source; a "
                              f"<LOGICAL>_TGT name is only ever written -- read a source through its "
                              f"<LOGICAL>_SRC name")
    return errors


def return_form_errors(proc: ProcInfo) -> list[str]:
    """`c4:return_form` (fix round 2): the body has one `RETURN '<string literal>'`, as its last
    statement. The local double never executes a RETURN, and it would run statements Snowflake never
    reaches after one, so anything else is refused rather than left unchecked."""
    errors = []
    if len(proc.returns) > 1:
        errors.append(_clean(f"c4:return_form: the body has {len(proc.returns)} RETURNs; contract C4 allows "
                             f"one RETURN, a string literal, as the last statement: "
                             f"{'; '.join(ret.text for ret in proc.returns)}"))
    for ret in proc.returns:
        if not _RETURN_LITERAL_RE.match(ret.text):
            errors.append(_clean(f"c4:return_form: {ret.text} is not RETURN '<string literal>' -- nothing "
                                 f"else is accepted there (the local double never runs a RETURN)"))
        if ret.followed_by:
            errors.append(_clean(f"c4:return_form: {ret.text} is not the last statement; Snowflake stops "
                                 f"there, while the local double would run the {ret.followed_by} "
                                 f"statement(s) after it"))
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
    parser.add_argument("seg", nargs="?", help="segment id, e.g. seg_01 (omit with --target dbt)")
    parser.add_argument("--target", choices=["auto", "sql", "snowpark", "dbt"], default="auto",
                        help="procedure shape to check (default: auto, from contract.json's \"target\")")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    # Not parser.error(): main() is called directly (in-process, no subprocess) by callers that
    # expect an int back, so a usage problem here still has to return 2, not raise SystemExit.
    if args.target == "dbt" and args.seg is not None:
        print(f"--target dbt checks the whole workflow, not one segment (DV6); got {args.seg!r}",
             file=sys.stderr)
        return 2
    if args.target != "dbt" and args.seg is None:
        print("seg is required unless --target dbt (a dbt project is checked as a whole)", file=sys.stderr)
        return 2

    try:
        report = compile_check(Repo(args.root), args.wf_id, args.seg, target=args.target)
    except (FileNotFoundError, KeyError, dbt_project.DbtUnavailable) as exc:
        where = f"{args.wf_id}/{args.seg}" if args.seg else args.wf_id
        print(f"cannot compile-check {where}: {exc}", file=sys.stderr)
        return 2
    except Exception:  # exit 2: a crash outside the checks above, which leaves no report either
        traceback.print_exc()
        return 2
    print(f"{report['status']}: {report['statements']} statements, {len(report['errors'])} errors")
    for error in report["errors"]:
        print(f"  {error}", file=sys.stderr)
    return 0 if report["status"] == "OK" else 1


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
