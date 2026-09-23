"""The canned agent artifacts under `samples/<wf>/canned/` are what the orchestrator's mock runner
replays instead of calling a real agent, and `samples/<wf>/broken_sql/` is what proves `compare.py`
catches a bad translation (plan task 13). This module checks their shape, so a hand-written fixture
cannot drift away from the contracts the real pipeline enforces.

Every check here is static or structural. Whether a procedure actually *produces* the golden data
is `tests/test_e2e_parity.py`'s job, and every number about data comes from `scripts/` — nothing in
this file computes one. Nothing in the repository has run against a real Snowflake account or a
real Alteryx engine; the segment DAGs these tests read are built by the local parser and segmenter
into `tmp_path`.

The tests are parametrized over whichever `samples/wf_*/canned/` directories exist, split by what
each workflow's canned set actually replays: `TRANSLATED_WORKFLOWS` for the ones that carry a
translation -- split into `PROCEDURE_WORKFLOWS` (a procedure per segment) and `DBT_WORKFLOWS` (one
dbt project for the whole workflow) -- and `RECOVERY_WORKFLOWS` for the ones whose replay is a
parser extension because the workflow never gets far enough to be translated.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

import pytest

import compile_check
import render_snowpark
from dev import build_samples
from lib import dbt_project, io
from lib.paths import Repo, seg_token, wf_token
from lib.proc_runner import ProcError, identifier_arguments, parse_proc
from lib.snowpark_rules import check_proc_py
from lib.vocab import DIFF_CLASSES
from tests.helpers import copy_pristine_mappings_and_catalog, prepare_workflow
from tests.test_e2e_parity import broken_cases

ROOT = Path(__file__).parents[1]
SAMPLES = ROOT / "samples"
CATALOG = ROOT / "catalog" / "columns.csv"

#: Contract C4's procedure signature.
C4_PARAMS = ["SRC_DB", "SRC_SCHEMA", "TGT_DB", "TGT_SCHEMA", "RUN_ID"]

#: `contract.json`'s own output target (plan contract C5): which validator and which static gate a
#: segment gets. `manual` and `unknown` are node classifications (`plugin_map.TARGET_CLASS`), not
#: segment targets -- a segment carrying one never reaches a translator at all. A dbt workflow's
#: segments are still `sql` here: dbt is the workflow's output KIND, not a segment target.
CONTRACT_TARGETS = frozenset({"sql", "snowpark"})

#: What a `broken.json` row's `target` may say -- which validator `tests/test_e2e_parity.py` feeds
#: the variant to. `dbt` names a model laid over the workflow's whole dbt project and judged by
#: `validate_dbt.py`, rather than a segment's own procedure.
TARGETS = frozenset({"sql", "snowpark", "dbt"})

#: Node types that carry no data through the segment, so no CTE can correspond to them: the three
#: layout/annotation kinds, Action tools, and Browse (the simulator says a Browse emits nothing).
NO_CTE_TYPES = frozenset({"container", "comment", "interface", "action", "browse"})

#: Top-level keys every contract.json carries (program schema plus plan contract C5).
CONTRACT_KEYS = ("segment", "target", "inputs", "output", "outputs", "row_relation", "ordering",
                 "tolerances", "parity_risks")

#: `row_relation`'s vocabulary, exactly as `docs/spec/00-README.md` (Contracts, `row_relation`)
#: spells it -- relative to the segment's primary input.
ROW_RELATIONS = frozenset({"1:1", "filter", "aggregate", "expand"})

#: Keys every `contract.outputs[]` entry carries (plan contract C5).
OUTPUT_KEYS = ("stream", "table", "kind", "logical", "columns", "keys")

TIERS = frozenset({"T1", "T2", "T3"})

#: `<name> AS (` at the start of a line: how every CTE in these procedures is written. A derived
#: table (`) f4`) and a cast (`CAST(x AS NUMBER(38,10))`) do not match, because the name has to be
#: followed by whitespace and then `AS (`.
_CTE_RE = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]+AS[ \t]*\(", re.MULTILINE)

def _tool_comment_re(tool_id: str) -> re.Pattern[str]:
    """The `-- tool <id>: …` comment the repo rules require in front of each tool's CTE. The id may
    be followed by an anchor note, as in `-- tool 3 (anchor F): …`."""
    return re.compile(rf"--\s*tool\s+{re.escape(tool_id)}\b")

#: The one way a node may be exempted from having a CTE: an explicit line in translation_notes.md,
#: naming the tool and giving a reason. Narrow on purpose -- a note that merely mentions the tool
#: does not excuse it.
_EXEMPTION_RE = re.compile(r"^-\s*tool\s+(\S+)\s+has no CTE:\s*\S", re.MULTILINE)

#: The first line of every file under `samples/<wf>/broken_sql/`, so a reader who opens one out of
#: context cannot mistake it for a translation anybody should copy -- in the comment syntax of the
#: language the variant is written in (a Snowpark segment's variant is a `proc.py` module).
_BROKEN_HEADER = ("-- BROKEN ON PURPOSE -- do not fix: this is a fixture for "
                  "tests/test_e2e_parity.py.")
_BROKEN_HEADER_PY = ("# BROKEN ON PURPOSE -- do not fix: this is a fixture for "
                     "tests/test_e2e_parity.py.")

#: Per suffix: the header a broken variant opens with, the canned file it is a variant of, and the
#: marker that separates that header from the body the two are compared below.
_BROKEN_KINDS = {".sql": (_BROKEN_HEADER, "proc.sql", "CREATE OR REPLACE PROCEDURE"),
                 ".py": (_BROKEN_HEADER_PY, "proc.py", "def run(")}

CANNED_WORKFLOWS = sorted(path.parent.name for path in SAMPLES.glob("wf_*/canned"))

#: The canned workflows that carry a translation: a procedure, a contract and broken variants per
#: segment. Everything a *migrated* workflow's artifacts have to satisfy is parametrized over
#: these, not over `CANNED_WORKFLOWS`, because a workflow that parks at MANUAL never gets a
#: segment at all (`orchestrator/stages.ts` stops a T3 workflow after the analyzer) and would
#: otherwise fail every one of them for the right reason.
TRANSLATED_WORKFLOWS = sorted(path.parents[1].name for path in SAMPLES.glob("wf_*/canned/segments"))

#: The translated workflows migrated as ONE dbt project (`output_kind: dbt`, design §4.3): their
#: canned tree carries `canned/dbt/**` and a contract per segment, but no procedure per segment.
DBT_WORKFLOWS = sorted(p.parents[2].name for p in SAMPLES.glob("wf_*/canned/dbt/dbt_project.yml"))

#: The translated workflows whose segments each carry a procedure (`output_kind: procedures`):
#: everything procedure-shaped below -- the C4 signature, the CTE rule, the Snowpark renderer, the
#: `.sql`/`.py` broken variants -- is parametrized over these.
PROCEDURE_WORKFLOWS = [wf for wf in TRANSLATED_WORKFLOWS if wf not in DBT_WORKFLOWS]

#: The only files a translator writes into `dbt/` (DV5's lane minus `fix_log.md`, which the fixer
#: appends to): the MockRunner replays EVERY file under `canned/dbt/`, so nothing else may be there
#: -- not `review.json` (the reviewer's), `compile_check.json` (the script's), `logs/` or `target/`.
_DBT_TRANSLATOR_FILES = frozenset({"dbt_project.yml", "profiles.yml", "README.md",
                                   "translation_notes.md"})

#: The canned workflows whose replay is a parser extension rather than SQL -- the files a
#: `parser-recovery` agent writes when `scripts/parse.py` cannot get a workflow past its
#: invariants.
RECOVERY_WORKFLOWS = sorted(path.parents[1].name
                            for path in SAMPLES.glob("wf_*/canned/parser-recovery"))

#: Where `orchestrator/runner.ts`'s `replayRecovery` puts each canned recovery file: a path under
#: `parsed/` goes into the workflow's own directory, and everything else into the repository root.
#: Only these two roots are legitimate, and an extension may write nowhere else (program spec
#: §6.4, `orchestrator/policy.ts`).
_RECOVERY_ROOTS = ("parsed/", "scripts/parsers/ext/", "tests/parser_corpus/")


def test_at_least_one_workflow_has_canned_artifacts():
    """Guards the parametrizations below: an empty glob would turn every test in this module into
    an empty parameter set that quietly passes."""
    assert CANNED_WORKFLOWS, f"no samples/wf_*/canned/ directory exists under {SAMPLES}"
    assert TRANSLATED_WORKFLOWS, f"no samples/wf_*/canned/segments/ directory exists under {SAMPLES}"
    assert RECOVERY_WORKFLOWS, (
        f"no samples/wf_*/canned/parser-recovery/ directory exists under {SAMPLES}")
    assert DBT_WORKFLOWS, f"no samples/wf_*/canned/dbt/dbt_project.yml exists under {SAMPLES}"
    assert set(DBT_WORKFLOWS) <= set(TRANSLATED_WORKFLOWS), (
        f"a canned dbt project needs a canned contract per segment too: "
        f"{sorted(set(DBT_WORKFLOWS) - set(TRANSLATED_WORKFLOWS))}")
    assert set(TRANSLATED_WORKFLOWS) | set(RECOVERY_WORKFLOWS) == set(CANNED_WORKFLOWS), (
        f"these canned workflows replay neither a translation nor a parser recovery: "
        f"{sorted(set(CANNED_WORKFLOWS) - set(TRANSLATED_WORKFLOWS) - set(RECOVERY_WORKFLOWS))}")


@pytest.fixture(scope="module")
def build_workflow(tmp_path_factory):
    """`build_workflow(wf_id)` -> a `Repo` holding that sample parsed, segmented and simulated.

    Built once per workflow per module run: every test below needs the same segment DAGs, and
    `build_samples.build` chains the parser, the segmenter and the Alteryx simulator, all local
    doubles.
    """
    built: dict[str, Repo] = {}

    def build(wf_id: str) -> Repo:
        if wf_id not in built:
            root = tmp_path_factory.mktemp(wf_id)
            copy_pristine_mappings_and_catalog(root)
            repo = Repo(root)
            build_samples.build(repo, SAMPLES, wf_id)
            built[wf_id] = repo
        return built[wf_id]

    return build


# --- helpers -----------------------------------------------------------------------------------


def _canned(wf_id: str) -> Path:
    return SAMPLES / wf_id / "canned"


def _segments(repo: Repo, wf_id: str) -> list[str]:
    return [seg for wave in io.read_json(repo.wf(wf_id, "segments", "order.json")) for seg in wave]


def _catalog_names() -> set[str]:
    """Every real table in `catalog/columns.csv`, as the qualified names a procedure must never
    write: `DB.SCHEMA.TABLE` and `SCHEMA.TABLE`.

    The bare table name is deliberately not included: a logical name may legitimately equal it
    (`ORDERS` is both `SALES.RAW.ORDERS`'s table name and tool 1's logical name), and the logical
    name is exactly what contract C4 tells the procedure to use.
    """
    names: set[str] = set()
    with CATALOG.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            database, schema, table = row["database"], row["schema"], row["table"]
            names.add(f"{database}.{schema}.{table}".upper())
            names.add(f"{schema}.{table}".upper())
    return names


def _cte_names(sql_text: str) -> set[str]:
    return {match.group(1) for match in _CTE_RE.finditer(sql_text)}


def _has_cte_for(node: dict, cte_names: set[str]) -> bool:
    """A node's CTE is `t<id>_<type>`, optionally with an anchor suffix (`t3_filter_f`) when the
    tool has several output anchors that feed different branches."""
    stem = f"t{node['tool_id']}_{node['type']}"
    return any(name == stem or name.startswith(stem + "_") for name in cte_names)


def _exempted_tools(notes_path: Path) -> set[str]:
    if not notes_path.is_file():
        return set()
    return set(_EXEMPTION_RE.findall(notes_path.read_text(encoding="utf-8")))


def _procs(wf_id: str) -> list[tuple[str, Path]]:
    canned = _canned(wf_id) / "segments"
    return [(path.parent.name, path) for path in sorted(canned.glob("*/proc.sql"))]


def _contract(wf_id: str, seg: str) -> dict:
    return json.loads(
        (_canned(wf_id) / "segments" / seg / "contract.json").read_text(encoding="utf-8"))


def _target(wf_id: str, seg: str) -> str:
    """The segment's output target, read from its own canned contract -- absent means `sql`, the
    same default `compile_check.py --target auto` applies."""
    path = _canned(wf_id) / "segments" / seg / "contract.json"
    return (_contract(wf_id, seg).get("target") or "sql") if path.is_file() else "sql"


def _data_nodes(repo: Repo, wf_id: str, seg: str) -> list[dict]:
    """The segment's DAG nodes that carry data -- what `snowpark_rules.check_proc_py`'s
    tool-comment rule walks, and the same filter `compile_check.py` applies before calling it."""
    dag = io.read_json(repo.seg(wf_id, seg, "dag.json"))
    return [node for node in dag["nodes"] if node["type"] not in NO_CTE_TYPES]


def _snowpark_runtime(repo: Repo) -> str:
    """`program.snowpark_runtime` as `render_snowpark.py` itself resolves it, read from the
    fixture's own pristine copy of `mappings/global.yaml` rather than the live tree."""
    program = (io.read_yaml(repo.global_mappings) or {}).get("program") or {}
    return str(program.get("snowpark_runtime") or "3.11")


# --- the procedures ----------------------------------------------------------------------------


@pytest.mark.parametrize("wf_id", PROCEDURE_WORKFLOWS)
def test_every_canned_procedure_has_the_c4_signature(wf_id):
    """Plan contract C4, and exactly what `scripts/compile_check.py` rejects a procedure over
    before it looks at anything else.

    A Snowpark segment's `proc.sql` is `render_snowpark.py`'s wrapper rather than something a
    translator wrote by hand, and it goes through `parse_proc` here like any other: the wrapper
    carries the same name, the same five parameters and the same `EXECUTE AS CALLER`, and differs
    only in declaring `LANGUAGE PYTHON`.
    """
    procs = _procs(wf_id)
    assert procs, f"samples/{wf_id}/canned/segments/ holds no proc.sql"
    for seg, path in procs:
        try:
            proc = parse_proc(path.read_text(encoding="utf-8"))
        except ProcError as exc:
            pytest.fail(f"{wf_id}/{seg}: proc.sql is outside the supported procedure shape: {exc}")
        expected_name = f"MIG_WORK.{wf_token(wf_id)}_{seg_token(seg)}"
        assert proc.name.replace('"', "").upper() == expected_name, (
            f"{wf_id}/{seg}: procedure is named {proc.name}, contract C4 requires {expected_name}")
        assert [param.upper() for param in proc.params] == C4_PARAMS, (
            f"{wf_id}/{seg}: parameters are {proc.params}, contract C4 requires {C4_PARAMS}")
        assert proc.execute_as == "CALLER", (
            f"{wf_id}/{seg}: EXECUTE AS {proc.execute_as}; contract C4 requires CALLER, because a "
            f"procedure running with owner's rights cannot ALTER SESSION")
        assert proc.statements, f"{wf_id}/{seg}: the procedure body runs no statement"
        expected_language = "PYTHON" if _target(wf_id, seg) == "snowpark" else "SQL"
        assert proc.language == expected_language, (
            f"{wf_id}/{seg}: proc.sql declares LANGUAGE {proc.language}, but contract.json's "
            f"target is {_target(wf_id, seg)!r}")


@pytest.mark.parametrize("wf_id", PROCEDURE_WORKFLOWS)
def test_every_data_node_has_a_cte_or_a_documented_exemption(wf_id, build_workflow):
    """The reviewer agent's first blocking check: every node of the segment's DAG has a CTE, or a
    documented merge in translation_notes.md says why it has none.

    Nodes that carry no data (containers, comments, interface and action tools, and a Browse,
    which the simulator says emits nothing) need neither.

    A `snowpark` segment is exempt: its `proc.sql` is a rendered Python wrapper with no CTEs at
    all, and the per-tool rule that replaces this one for it -- a real `# tool <id>:` comment per
    data node, found through the tokenizer -- is `snowpark_rules.check_proc_py`'s
    `rule:tool_comments`, asserted in
    `test_every_snowpark_segment_satisfies_the_ast_rules_and_the_renderer` below.
    """
    repo = build_workflow(wf_id)
    for seg, path in _procs(wf_id):
        if _target(wf_id, seg) == "snowpark":
            continue
        dag = io.read_json(repo.seg(wf_id, seg, "dag.json"))
        sql_text = path.read_text(encoding="utf-8")
        cte_names = _cte_names(sql_text)
        exempted = _exempted_tools(path.parent / "translation_notes.md")
        for node in dag["nodes"]:
            if node["type"] in NO_CTE_TYPES:
                continue
            tool_id = str(node["tool_id"])
            if _has_cte_for(node, cte_names):
                assert _tool_comment_re(tool_id).search(sql_text), (
                    f"{wf_id}/{seg}: the CTE for tool {tool_id} has no `-- tool {tool_id}: …` "
                    f"comment in front of it")
                continue
            assert tool_id in exempted, (
                f"{wf_id}/{seg}: tool {tool_id} ({node['type']}) has no t{tool_id}_{node['type']} "
                f"CTE in proc.sql and no `- tool {tool_id} has no CTE: <reason>` line in "
                f"translation_notes.md; CTEs found: {sorted(cte_names)}")


@pytest.mark.parametrize("wf_id", CANNED_WORKFLOWS)
def test_no_procedure_names_a_real_catalog_table(wf_id):
    """Contract C4: a mapped source or target is reached only through `IDENTIFIER(:<VAR>)` on a
    name built from its logical name, so the same body runs against the golden view schema and
    against production.
    The broken variants are checked too -- a fixture is allowed one wrong translation, not a
    hard-coded table name."""
    forbidden = _catalog_names()
    files = [path for _, path in _procs(wf_id)]
    files += sorted((_canned(wf_id) / "segments").glob("*/proc.py"))
    files += sorted(path for path in (SAMPLES / wf_id / "broken_sql").glob("*/*")
                    if path.suffix in _BROKEN_KINDS)
    # A dbt model reaches a mapped table only through `{{ source('src', '<LOGICAL>') }}` -- the
    # same "no hand-written FQN" rule, applied to the project and its broken models.
    files += sorted((_canned(wf_id) / "dbt").glob("models/**/*.sql"))
    files += sorted((SAMPLES / wf_id / "broken_sql" / "dbt").glob("**/*.sql"))
    for path in files:
        text = path.read_text(encoding="utf-8").upper()
        named = sorted(name for name in forbidden if name in text)
        assert not named, (
            f"{path.relative_to(SAMPLES)} names the mapped table(s) {named}; contract C4 requires "
            f"LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>'; and "
            f"IDENTIFIER(:<LOGICAL>_SRC) instead")


#: Contract C4's table reference (Task C4V): the argument of every `IDENTIFIER(…)` in a SQL
#: procedure is one variable named `<LOGICAL>_SRC` or `<LOGICAL>_TGT`, built by a `LET`.
_C4_IDENTIFIER_ARGUMENT_RE = re.compile(r":(?P<logical>[A-Z_][A-Z0-9_$]*)_(?P<side>SRC|TGT)")


def _sql_procedures(wf_id: str) -> list[Path]:
    """Every hand-written SQL procedure of the workflow: the canned `proc.sql` of each `sql`
    segment (a `snowpark` segment's is the rendered Python wrapper) and every `.sql` variant."""
    files = [path for seg, path in _procs(wf_id) if _target(wf_id, seg) == "sql"]
    return files + sorted((SAMPLES / wf_id / "broken_sql").glob("*/*.sql"))


@pytest.mark.parametrize("wf_id", PROCEDURE_WORKFLOWS)
def test_every_sql_procedure_uses_only_the_documented_identifier_form(wf_id):
    """Snowflake documents `IDENTIFIER( { string_literal | session_variable | bind_variable |
    snowflake_scripting_variable } )` -- one value, not an expression -- so every canned and broken
    procedure builds each mapped table's name with `LET <LOGICAL>_SRC|_TGT VARCHAR := …` and reads
    or writes it as `IDENTIFIER(:<LOGICAL>_SRC|_TGT)`: `compile_check.py`'s two named C4 checks
    have nothing to say, every `IDENTIFIER(…)` takes such a variable, and every `LET` is used.
    Nothing here has run on Snowflake; the first real-account run confirms the documented form."""
    files = _sql_procedures(wf_id)
    assert files, f"samples/{wf_id} holds no SQL procedure"
    for path in files:
        where = path.relative_to(SAMPLES)
        proc = parse_proc(path.read_text(encoding="utf-8"))
        assert compile_check.table_reference_errors(proc) + compile_check.identifier_role_errors(proc) == [], where
        arguments = [argument for statement in proc.statements
                     for argument in identifier_arguments(statement)]
        for argument in arguments:
            assert _C4_IDENTIFIER_ARGUMENT_RE.fullmatch(argument), f"{where}: IDENTIFIER({argument})"
        unused = {let.name for let in proc.lets} - {argument[1:] for argument in arguments}
        assert not unused, f"{where}: LET {sorted(unused)} never used in an IDENTIFIER(…)"


@pytest.mark.parametrize("wf_id", PROCEDURE_WORKFLOWS)
def test_every_canned_procedure_builds_one_name_per_mapped_source_and_target(wf_id):
    """One `LET` per distinct source (`<LOGICAL>_SRC`, a contract input with a logical name) and
    per final target (`<LOGICAL>_TGT`, a contract output of kind `target`), and no other."""
    for seg, path in _procs(wf_id):
        if _target(wf_id, seg) != "sql":
            continue
        contract = _contract(wf_id, seg)
        expected = ({f"{source['logical']}_SRC" for source in contract.get("inputs") or []
                     if source.get("logical")}
                    | {f"{output['logical']}_TGT" for output in contract.get("outputs") or []
                       if output.get("kind") == "target"})
        declared = [let.name for let in parse_proc(path.read_text(encoding="utf-8")).lets]
        assert sorted(declared) == sorted(expected), f"{wf_id}/{seg}"


@pytest.mark.parametrize("wf_id", PROCEDURE_WORKFLOWS)
def test_every_canned_procedure_passes_compile_check(wf_id, tmp_path):
    """The canned procedures are what a correct translator writes, so `compile_check.py` -- the
    target each segment's contract names, the two C4 table-reference checks included -- must have
    nothing to say about any of them."""
    repo = prepare_workflow(tmp_path, wf_id)
    for seg in _segments(repo, wf_id):
        report = compile_check.compile_check(repo, wf_id, seg)
        assert report["status"] == "OK", f"{wf_id}/{seg}: " + "\n".join(report["errors"])


@pytest.mark.parametrize("wf_id", CANNED_WORKFLOWS)
def test_every_broken_variant_is_still_a_c4_procedure(wf_id):
    """A broken variant is fed to `validate_segment.py` in place of the real procedure, so it has
    to be the same procedure with one mistake in it -- not a fragment and not a different shape."""
    for path in sorted((SAMPLES / wf_id / "broken_sql").glob("*/*.sql")):
        seg = path.parent.name
        try:
            proc = parse_proc(path.read_text(encoding="utf-8"))
        except ProcError as exc:
            pytest.fail(f"{path.relative_to(SAMPLES)} is not a runnable procedure: {exc}")
        assert proc.name.replace('"', "").upper() == f"MIG_WORK.{wf_token(wf_id)}_{seg_token(seg)}"
        assert [param.upper() for param in proc.params] == C4_PARAMS
        assert proc.execute_as == "CALLER"


@pytest.mark.parametrize("wf_id", PROCEDURE_WORKFLOWS)
def test_every_snowpark_segment_satisfies_the_ast_rules_and_the_renderer(wf_id, build_workflow):
    """A `snowpark` segment's source of truth is `proc.py`, and `proc.sql` is only the wrapper
    `render_snowpark.render` produces from it. This is the canned twin of what
    `compile_check.py --target snowpark` runs on a real translation: the AST rules must have
    nothing to say about the module, and the committed wrapper must be byte-identical to a fresh
    render -- an edited `proc.py` whose `proc.sql` was never re-rendered would otherwise deploy
    the stale body while every other check here passed.
    """
    repo = build_workflow(wf_id)
    runtime = _snowpark_runtime(repo)
    for seg in _segments(repo, wf_id):
        if _target(wf_id, seg) != "snowpark":
            continue
        seg_dir = _canned(wf_id) / "segments" / seg
        proc_py = seg_dir / "proc.py"
        assert proc_py.is_file(), (
            f"{wf_id}/{seg}: contract.json declares target snowpark, so canned/segments/{seg}/"
            f"proc.py is the source of truth and must exist")
        source = proc_py.read_text(encoding="utf-8")
        contract = {**_contract(wf_id, seg), "nodes": _data_nodes(repo, wf_id, seg)}
        assert check_proc_py(source, wf_id, seg, contract) == [], (
            f"{wf_id}/{seg}: proc.py breaks the Snowpark rules (spec §4.2)")
        assert (seg_dir / "proc.sql").read_text(encoding="utf-8") == \
            render_snowpark.render(source, wf_id, seg, runtime), (
            f"{wf_id}/{seg}: proc.sql is not render_snowpark.render(proc.py, …) for runtime "
            f"{runtime}; re-run `python scripts/render_snowpark.py {wf_id} {seg}`")


# --- the contracts -----------------------------------------------------------------------------


@pytest.mark.parametrize("wf_id", TRANSLATED_WORKFLOWS)
def test_every_contract_has_the_c5_keys(wf_id, build_workflow):
    repo = build_workflow(wf_id)
    segments = _segments(repo, wf_id)
    for seg in segments:
        path = _canned(wf_id) / "segments" / seg / "contract.json"
        assert path.is_file(), f"{wf_id}/{seg}: no canned contract.json"
        contract = json.loads(path.read_text(encoding="utf-8"))
        missing = [key for key in CONTRACT_KEYS if key not in contract]
        assert not missing, f"{wf_id}/{seg}: contract.json is missing {missing} (plan contract C5)"
        assert contract["segment"] == seg
        assert contract["target"] in CONTRACT_TARGETS, (
            f"{wf_id}/{seg}: contract.json target is {contract['target']!r}, the vocabulary "
            f"(plan contract C5) is {sorted(CONTRACT_TARGETS)}")
        assert contract["row_relation"] in ROW_RELATIONS, (
            f"{wf_id}/{seg}: row_relation is {contract['row_relation']!r}, the spec vocabulary "
            f"(docs/spec/00-README.md) is {sorted(ROW_RELATIONS)}")
        assert "order_dependent_columns" in contract["ordering"], (
            f"{wf_id}/{seg}: contract.ordering has no `order_dependent_columns` (plan contract C5)")

        outputs = contract["outputs"]
        assert outputs, f"{wf_id}/{seg}: contract.outputs is empty"
        assert contract["output"] == outputs[0], (
            f"{wf_id}/{seg}: contract.output must equal contract.outputs[0] (plan contract C5)")
        for output in outputs:
            where = f"{wf_id}/{seg} output {output.get('stream')!r}"
            missing = [key for key in OUTPUT_KEYS if key not in output]
            assert not missing, f"{where}: missing {missing} (plan contract C5)"
            assert output["kind"] in ("work", "target"), f"{where}: kind is {output['kind']!r}"
            assert output["columns"], f"{where}: lists no columns"
            assert isinstance(output["keys"], list), f"{where}: keys must be a list"
            if output["kind"] == "target":
                assert output.get("tool_id"), f"{where}: a target needs the Output tool's tool_id"
                assert output["logical"], f"{where}: a target needs a logical name"
            else:
                assert output["table"], f"{where}: a work output needs its literal MIG_WORK table"

        for entry in contract["inputs"]:
            where = f"{wf_id}/{seg} input {entry.get('logical') or entry.get('stream')!r}"
            assert entry.get("columns"), f"{where}: lists no columns"
            if entry.get("stream"):
                assert entry.get("from") in segments, (
                    f"{where}: `from` must name an upstream segment (plan contract C5)")
                assert entry.get("table"), f"{where}: an upstream input needs its literal table"
            else:
                assert entry.get("logical"), (
                    f"{where}: a mapped source needs a `logical` name (plan contract C5)")


@pytest.mark.parametrize("wf_id", TRANSLATED_WORKFLOWS)
def test_work_outputs_use_the_contract_c3_table_names(wf_id, build_workflow):
    """A segment's outbound stream is materialised at `MIG_WORK.<WF>_<SEG>_OUT` for the primary
    stream and `MIG_WORK.<WF>_<SEG>_OUT_<stream>` for the others, and the procedure creates it --
    or, for a dbt workflow, the project has the `table` model named after it (design §4.3)."""
    repo = build_workflow(wf_id)
    for seg in _segments(repo, wf_id):
        contract = json.loads(
            (_canned(wf_id) / "segments" / seg / "contract.json").read_text(encoding="utf-8"))
        primary = f"MIG_WORK.{wf_token(wf_id)}_{seg_token(seg)}_OUT"
        for output in contract["outputs"]:
            if output["kind"] != "work":
                continue
            assert output["table"] == primary or output["table"].startswith(primary + "_"), (
                f"{wf_id}/{seg}: work table {output['table']!r} is not {primary} or "
                f"{primary}_<stream> (plan contract C3)")
            if wf_id in DBT_WORKFLOWS:
                model = _canned(wf_id) / "dbt" / "models" / f"{dbt_project.model_name(output)}.sql"
                assert model.is_file(), (
                    f"{wf_id}/{seg}: no model {model.relative_to(SAMPLES)} writes {output['table']}")
                continue
            sql_text = (_canned(wf_id) / "segments" / seg / "proc.sql").read_text(encoding="utf-8")
            assert output["table"] in sql_text, (
                f"{wf_id}/{seg}: the procedure never writes {output['table']}")


# --- the remaining canned artifacts --------------------------------------------------------------


@pytest.mark.parametrize("wf_id", CANNED_WORKFLOWS)
def test_unsupported_json_declares_a_valid_tier(wf_id):
    path = _canned(wf_id) / "unsupported.json"
    assert path.is_file(), f"{wf_id}: no canned unsupported.json"
    unsupported = json.loads(path.read_text(encoding="utf-8"))
    assert unsupported.get("tier") in TIERS, (
        f"{wf_id}: unsupported.json tier is {unsupported.get('tier')!r}, expected one of "
        f"{sorted(TIERS)}")
    assert isinstance(unsupported.get("unsupported"), list)
    assert isinstance(unsupported.get("unknown"), list)


@pytest.mark.parametrize("wf_id", PROCEDURE_WORKFLOWS)
def test_the_canned_artifact_set_is_complete(wf_id, build_workflow):
    """What the mock runner replays for a T1 workflow, one file per agent stage."""
    repo = build_workflow(wf_id)
    canned = _canned(wf_id)
    for relative in ("intake/plan.md", "analysis.md", "unsupported.json", "docs/migration.md"):
        assert (canned / relative).is_file(), f"{wf_id}: canned/{relative} is missing"
    for seg in _segments(repo, wf_id):
        names = ["contract.json", "proc.sql", "translation_notes.md", "review.json"]
        if _target(wf_id, seg) == "snowpark":
            names.append("proc.py")  # the source of truth; proc.sql is rendered from it
        for name in names:
            assert (canned / "segments" / seg / name).is_file(), (
                f"{wf_id}/{seg}: canned/segments/{seg}/{name} is missing")
        review = json.loads(
            (canned / "segments" / seg / "review.json").read_text(encoding="utf-8"))
        assert review == {"verdict": "PASS", "findings": []}, (
            f"{wf_id}/{seg}: review.json is {review}, expected a clean PASS")


@pytest.mark.parametrize("wf_id", PROCEDURE_WORKFLOWS)
def test_the_end_to_end_parity_tests_do_not_skip_this_workflow(wf_id, build_workflow):
    """`tests/helpers.prepare_workflow` skips while `samples/<wf>/canned/segments/` is absent and
    raises if a segment's `contract.json` or `proc.sql` is missing. Once a workflow has canned
    artifacts, neither may happen: a silently skipped parity test proves nothing.

    Checked as preconditions rather than by running the e2e suite from here, so a parity failure
    is reported by `tests/test_e2e_parity.py` and not duplicated as a failure of this module.
    """
    canned = _canned(wf_id) / "segments"
    assert canned.is_dir(), (
        f"{wf_id}: samples/{wf_id}/canned/segments/ is missing, so prepare_workflow would skip "
        f"every e2e parity test for it")
    repo = build_workflow(wf_id)
    for seg in _segments(repo, wf_id):
        names = ["contract.json", "proc.sql"]
        if _target(wf_id, seg) == "snowpark":
            names.append("proc.py")  # what validate_snowpark.py actually runs
        for name in names:
            assert (canned / seg / name).is_file(), (
                f"{wf_id}: prepare_workflow would raise -- canned/segments/{seg}/{name} is missing")
    cases = [param.values for param in broken_cases() if param.values[0] == wf_id]
    assert cases, (
        f"{wf_id}: test_broken_migration_fails_with_the_right_class would collect no case for "
        f"this workflow")


@pytest.mark.parametrize("wf_id", TRANSLATED_WORKFLOWS)
def test_the_documenter_sections_are_all_present(wf_id):
    """The sections `.github/agents/documenter.agent.md` requires of `docs/migration.md` --
    including `## Deployment` for every output kind (ruling R-C1): what gets deployed, by whom,
    and that nothing here has run on Snowflake."""
    text = (_canned(wf_id) / "docs" / "migration.md").read_text(encoding="utf-8")
    headings = {line.lstrip("# ").strip().lower() for line in text.splitlines()
                if line.startswith("## ")}
    required = ["overview", "source mappings", "tool → cte map", "deployment", "assumptions",
                "accepted differences", "unsupported / manual items", "validation summary",
                "runbook", "open items"]
    missing = [heading for heading in required if heading not in headings]
    assert not missing, f"{wf_id}: docs/migration.md has no section for {missing}"


# --- the broken variants --------------------------------------------------------------------------


@pytest.mark.parametrize("wf_id", TRANSLATED_WORKFLOWS)
def test_every_broken_json_row_points_at_a_real_file_and_segment(wf_id, build_workflow):
    repo = build_workflow(wf_id)
    segments = _segments(repo, wf_id)
    golden_sets = io.load_manifest(repo, wf_id).get("golden_sets") or []
    path = SAMPLES / wf_id / "broken_sql" / "broken.json"
    assert path.is_file(), f"{wf_id}: no broken_sql/broken.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert cases, f"{wf_id}: broken.json lists no cases"

    listed: set[Path] = set()
    for case in cases:
        where = f"{wf_id} broken.json {case.get('file')!r}"
        assert case["segment"] in segments, f"{where}: segment {case['segment']!r} is not a segment"
        assert case["target"] in TARGETS, (
            f"{where}: target {case.get('target')!r} is outside {sorted(TARGETS)}; "
            f"tests/test_e2e_parity.py reads it to pick the validator")
        if case["target"] == "dbt":
            # A dbt variant is a model laid over the whole project, so its path is relative to
            # broken_sql/ itself (the project-relative path under dbt/), not to a segment folder.
            assert wf_id in DBT_WORKFLOWS, (
                f"{where}: a dbt row in a workflow with no canned dbt project")
            assert case["file"].startswith("dbt/models/"), (
                f"{where}: a dbt variant is a model under dbt/models/, not {case['file']!r}")
            sql_path = path.parent / case["file"]
        else:
            sql_path = path.parent / case["segment"] / case["file"]
            assert case["target"] == _target(wf_id, case["segment"]), (
                f"{where}: target {case['target']!r} disagrees with the segment's own contract")
        assert sql_path.is_file(), f"{where}: no such file {sql_path.relative_to(SAMPLES)}"
        listed.add(sql_path)
        assert case["golden_set"] in golden_sets, (
            f"{where}: golden_set {case['golden_set']!r} is not one of {golden_sets}")
        expect = case["expect"]
        assert expect["class"] in DIFF_CLASSES, (
            f"{where}: diff class {expect['class']!r} is outside the vocabulary")
        assert isinstance(expect["columns"], list) and expect.get("stream")

    on_disk = {found for found in path.parent.glob("*/*") if found.suffix in _BROKEN_KINDS}
    on_disk |= set((path.parent / "dbt").glob("**/*.sql"))
    assert on_disk == listed, (
        f"{wf_id}: these broken_sql files have no broken.json row: "
        f"{sorted(str(p.relative_to(SAMPLES)) for p in on_disk - listed)}")


@pytest.mark.parametrize("wf_id", PROCEDURE_WORKFLOWS)
def test_every_broken_variant_is_the_canned_procedure_with_one_documented_mistake(wf_id):
    """A fixture is the *correct* procedure with one realistic translator mistake in it, and it
    says at the top which mistake that is.

    Without this check a variant could silently drift away from the procedure it was cut from --
    most easily by the canned `proc.sql` being edited afterwards and the variant not being
    regenerated -- and it would go on failing validation for a reason that has nothing to do with
    the `broken.json` row explaining it. The parity test would still pass, which is the worst kind
    of green.

    A variant may legitimately *drop* a CTE (forgetting a tool is a realistic mistake); it may not
    invent one, because then it is a different translation rather than a wrong one. A Snowpark
    segment's variant is a `proc.py` module cut from the canned `proc.py`, so it is held to the
    same three rules in Python's own comment syntax; the CTE rule has nothing to say about it.
    """
    for proc_path in sorted(path for path in (SAMPLES / wf_id / "broken_sql").glob("*/*")
                            if path.suffix in _BROKEN_KINDS):
        where = proc_path.relative_to(SAMPLES)
        expected_header, canned_name, marker = _BROKEN_KINDS[proc_path.suffix]
        broken = proc_path.read_text(encoding="utf-8")
        assert broken.startswith(expected_header), (
            f"{where} does not open with `{expected_header}`, so a reader who opens it out of "
            f"context cannot tell it is wrong on purpose")

        canned = _canned(wf_id) / "segments" / proc_path.parent.name / canned_name
        assert canned.is_file(), f"{where}: there is no canned {canned_name} it could be a variant of"
        correct = canned.read_text(encoding="utf-8")

        assert marker in broken, f"{where}: no `{marker}` in the file"
        header, _, body = broken.partition(marker)
        assert "The mistake:" in header, (
            f"{where}: the header comment does not say what the mistake is (`The mistake: …`)")
        assert body != correct.partition(marker)[2], (
            f"{where} is byte-identical to {canned.relative_to(SAMPLES)} below the header: a "
            f"fixture that is not broken proves nothing")

        if proc_path.suffix != ".sql":
            continue
        invented = _cte_names(body) - _cte_names(correct)
        assert not invented, (
            f"{where} introduces CTE(s) {sorted(invented)} that {canned.relative_to(SAMPLES)} "
            f"does not have; a variant is one wrong translation, not a different one")


@pytest.mark.parametrize("wf_id", PROCEDURE_WORKFLOWS)
def test_every_broken_python_variant_still_satisfies_the_snowpark_rules(wf_id, build_workflow):
    """The Snowpark twin of `test_every_broken_variant_is_still_a_c4_procedure`: a `.py` variant is
    fed to `validate_snowpark.py` in place of the real module, so it has to be the same procedure
    with one *logic* mistake in it -- still the C4 `run` signature, still inside the AST rules.
    A variant that broke a rule instead would fail for a reason its `broken.json` row does not
    explain, and `compare.py` would never get to say anything at all."""
    repo = build_workflow(wf_id)
    for proc_path in sorted((SAMPLES / wf_id / "broken_sql").glob("*/*.py")):
        seg = proc_path.parent.name
        contract = {**_contract(wf_id, seg), "nodes": _data_nodes(repo, wf_id, seg)}
        errors = check_proc_py(proc_path.read_text(encoding="utf-8"), wf_id, seg, contract)
        assert errors == [], (
            f"{proc_path.relative_to(SAMPLES)} breaks the Snowpark rules; a broken variant is one "
            f"wrong translation, not an unrunnable one")


# --- the dbt project (output_kind dbt, design §4.3) ---------------------------------------------
#
# A dbt workflow replays ONE project for the whole workflow (`canned/dbt/**`, MockRunner's
# `replayDbt`), a per-workflow `canned/review.json`, and a contract per segment -- never a
# procedure. Its broken variants are models laid over that project (`broken_sql/dbt/models/*.sql`).


def _dbt_model_variants(wf_id: str) -> list[Path]:
    return sorted((SAMPLES / wf_id / "broken_sql" / "dbt").glob("**/*.sql"))


@pytest.mark.parametrize("wf_id", DBT_WORKFLOWS)
def test_every_dbt_sample_passes_compile_check_dbt(wf_id, tmp_path):
    """The canned project is what a correct translator writes, so the dbt target's own static gate
    (`compile_check.py <wf> --target dbt`, all eleven `dbt:<check>`s, `dbt parse` included) must
    have nothing to say about it. Built through `prepare_workflow` rather than the module's shared
    `build_workflow`: the gate reads `intake/mappings.yaml` (sources, write modes, merge keys),
    which only a resolved intake writes."""
    repo = prepare_workflow(tmp_path, wf_id)
    report = compile_check.compile_check_dbt(repo, wf_id)
    assert report["status"] == "OK", "\n".join(report["errors"])


@pytest.mark.parametrize("wf_id", DBT_WORKFLOWS)
def test_the_dbt_artifact_set_is_complete(wf_id, build_workflow):
    """What the mock runner replays for a dbt workflow: the per-workflow agent files, a contract per
    segment (the analyzer's), the project (the translator's) and one reviewer verdict for the whole
    project -- and nothing procedure-shaped, which would mean a second, competing translation."""
    repo = build_workflow(wf_id)
    canned = _canned(wf_id)
    for relative in ("intake/plan.md", "analysis.md", "unsupported.json", "docs/migration.md",
                     "review.json"):
        assert (canned / relative).is_file(), f"{wf_id}: canned/{relative} is missing"
    for relative in (*dbt_project.PROJECT_FILES, "translation_notes.md"):
        assert (canned / "dbt" / relative).is_file(), f"{wf_id}: canned/dbt/{relative} is missing"

    project_files = sorted(path.relative_to(canned / "dbt").as_posix()
                           for path in (canned / "dbt").rglob("*") if path.is_file())
    outside = [rel for rel in project_files
               if rel not in _DBT_TRANSLATOR_FILES and not rel.startswith("models/")]
    assert not outside, (
        f"{wf_id}: canned/dbt/ holds {outside}, outside the translator's lane; the MockRunner "
        f"replays every file there as the translator's own output")

    review = json.loads((canned / "review.json").read_text(encoding="utf-8"))
    assert review == {"verdict": "PASS", "findings": []}, (
        f"{wf_id}: canned/review.json is {review}, expected a clean PASS")

    for seg in _segments(repo, wf_id):
        seg_dir = canned / "segments" / seg
        assert (seg_dir / "contract.json").is_file(), f"{wf_id}/{seg}: no canned contract.json"
        for name in ("proc.sql", "proc.py", "review.json"):
            assert not (seg_dir / name).exists(), (
                f"{wf_id}/{seg}: canned/segments/{seg}/{name} exists, but a dbt workflow has no "
                f"per-segment procedure or review -- the project is the translation")
        contract = _contract(wf_id, seg)
        assert contract["target"] == "sql", (
            f"{wf_id}/{seg}: a dbt workflow's segments are all `sql` (design §3.1: any other "
            f"target is a dbt blocker), not {contract['target']!r}")
        for output in contract["outputs"]:
            model = canned / "dbt" / "models" / f"{dbt_project.model_name(output)}.sql"
            assert model.is_file(), (
                f"{wf_id}/{seg}: no model {model.relative_to(SAMPLES)} for output "
                f"{output.get('logical') or output.get('table')!r}")

    cases = [param.values[1] for param in broken_cases() if param.values[0] == wf_id]
    assert cases and all(case["target"] == "dbt" for case in cases), (
        f"{wf_id}: test_broken_migration_fails_with_the_right_class needs at least one dbt row "
        f"for this workflow, and every row must be one: {cases}")


@pytest.mark.parametrize("wf_id", DBT_WORKFLOWS)
def test_every_dbt_broken_variant_is_the_canned_model_with_one_documented_mistake(wf_id):
    """The dbt twin of the procedure rule above: a broken model is the canned model of the same
    path with one realistic mistake, opening with the SQL broken header and saying what the mistake
    is. It may drop a CTE (forgetting a tool), never invent one."""
    variants = _dbt_model_variants(wf_id)
    assert variants, f"{wf_id}: no broken model under broken_sql/dbt/"
    marker = "{{ config("
    for variant in variants:
        where = variant.relative_to(SAMPLES)
        broken = variant.read_text(encoding="utf-8")
        assert broken.startswith(_BROKEN_HEADER), (
            f"{where} does not open with `{_BROKEN_HEADER}`, so a reader who opens it out of "
            f"context cannot tell it is wrong on purpose")

        relative = variant.relative_to(SAMPLES / wf_id / "broken_sql")
        canned = _canned(wf_id) / relative
        assert canned.is_file(), f"{where}: there is no canned/{relative.as_posix()} it could vary"
        correct = canned.read_text(encoding="utf-8")

        assert marker in broken, f"{where}: no `{marker}` line in the file"
        header, _, body = broken.partition(marker)
        assert "The mistake:" in header, (
            f"{where}: the header comment does not say what the mistake is (`The mistake: …`)")
        assert body != correct.partition(marker)[2], (
            f"{where} is byte-identical to {canned.relative_to(SAMPLES)} below the header: a "
            f"fixture that is not broken proves nothing")

        canned_ctes = _cte_names(correct)
        assert canned_ctes, f"{canned.relative_to(SAMPLES)} has no `<name> AS (` CTE to compare with"
        invented = _cte_names(body) - canned_ctes
        assert not invented, (
            f"{where} introduces CTE(s) {sorted(invented)} that {canned.relative_to(SAMPLES)} "
            f"does not have; a variant is one wrong translation, not a different one")


# --- the parser-recovery artifacts ----------------------------------------------------------------
#
# A workflow the parser cannot get past its invariants replays a parser EXTENSION instead of SQL
# (`orchestrator/runner.ts`'s `replayRecovery`). These files are code, not prose, so the checks
# below actually run them: the extension has to register through `scripts/parsers/registry.py`'s
# real mechanism, and the corpus test that ships with it has to pass from a tree that holds
# nothing but the replayed files.


def _recovery_files(wf_id: str) -> dict[str, Path]:
    """Every canned recovery file, keyed by the forward-slash path `replayRecovery` derives."""
    base = _canned(wf_id) / "parser-recovery"
    return {path.relative_to(base).as_posix(): path for path in sorted(base.rglob("*"))
            if path.is_file()}


def _stage_recovery(wf_id: str, root: Path) -> Path:
    """Copy the canned recovery files into `root` exactly as the mock runner would, minus the
    `parsed/` ones, which go to the workflow directory and are prose. Returns `root`."""
    for relative, path in _recovery_files(wf_id).items():
        if relative.startswith("parsed/"):
            continue
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, destination)
    return root


@pytest.mark.parametrize("wf_id", RECOVERY_WORKFLOWS)
def test_the_recovery_artifact_set_is_complete(wf_id):
    """What the mock runner replays for a workflow that parks at MANUAL: the intake plan, the
    analysis and `unsupported.json`, plus the three things `.github/agents/parser-recovery.agent.md`
    requires -- a diagnosis, an extension, and a corpus fixture with a test.

    There is deliberately no `docs/migration.md` and no `canned/segments/`: `orchestrator/stages.ts`
    stops a T3 workflow after the analyzer, so neither the translator nor the documenter ever runs.
    """
    canned = _canned(wf_id)
    for relative in ("intake/plan.md", "analysis.md", "unsupported.json"):
        assert (canned / relative).is_file(), f"{wf_id}: canned/{relative} is missing"
    assert not (canned / "segments").exists(), (
        f"{wf_id}: canned/segments/ exists, but this workflow replays a parser recovery and has "
        f"no procedure to validate")
    assert not (canned / "docs" / "migration.md").exists(), (
        f"{wf_id}: canned/docs/migration.md exists, but the documenter never runs for a workflow "
        f"that parks at MANUAL")

    files = _recovery_files(wf_id)
    assert "parsed/parse_diagnosis.md" in files, f"{wf_id}: no parser-recovery/parsed/parse_diagnosis.md"
    assert files["parsed/parse_diagnosis.md"].read_text(encoding="utf-8").strip(), (
        f"{wf_id}: parse_diagnosis.md is empty")

    extensions = [name for name in files if name.startswith("scripts/parsers/ext/")
                  and name.endswith(".py")]
    assert extensions, f"{wf_id}: no parser-recovery/scripts/parsers/ext/*.py"
    fixtures = {name.split("/")[2] for name in files if name.startswith("tests/parser_corpus/")}
    assert fixtures, f"{wf_id}: no parser-recovery/tests/parser_corpus/<name>/ fixture directory"
    for fixture in sorted(fixtures):
        present = {name.split("/", 3)[3] for name in files
                   if name.startswith(f"tests/parser_corpus/{fixture}/")}
        assert any(name.startswith("test_") and name.endswith(".py") for name in present), (
            f"{wf_id}: corpus fixture {fixture} has no test_*.py; an extension without a test is "
            f"not finished (scripts/parsers/ext/README.md)")
        assert any(name.endswith((".yxmd", ".yxwz", ".yxmc", ".xml")) for name in present), (
            f"{wf_id}: corpus fixture {fixture} has no workflow fragment")
        assert "README.md" in present, (
            f"{wf_id}: corpus fixture {fixture} has no README.md saying what it guards")

    outside = [name for name in files if not name.startswith(_RECOVERY_ROOTS)]
    assert not outside, (
        f"{wf_id}: these canned recovery files are outside the roots `replayRecovery` and "
        f"`orchestrator/policy.ts` allow ({list(_RECOVERY_ROOTS)}): {outside}")


@pytest.mark.parametrize("wf_id", RECOVERY_WORKFLOWS)
def test_a_recovered_workflow_declares_tier_t3_and_names_what_blocks_it(wf_id, build_workflow):
    """A workflow that needs parser recovery is not automatically T3 -- the recovery is what lets
    the pipeline *reach* a verdict. This one is T3 for its own reasons, and `unsupported.json` has
    to say which tools they are, by an id the parsed DAG really has."""
    unsupported = json.loads((_canned(wf_id) / "unsupported.json").read_text(encoding="utf-8"))
    assert unsupported["tier"] == "T3", (
        f"{wf_id}: replays a parser recovery and never produces a procedure, so its tier must be "
        f"T3, not {unsupported['tier']!r}")
    assert unsupported["unsupported"], (
        f"{wf_id}: tier T3 with an empty `unsupported` list says nothing about what blocks it")

    repo = build_workflow(wf_id)
    dag = io.read_json(repo.wf(wf_id, "parsed", "dag.json"))
    tool_ids = {str(node["tool_id"]) for node in dag["nodes"]}
    for entry in unsupported["unsupported"] + unsupported["unknown"]:
        assert str(entry["tool_id"]) in tool_ids, (
            f"{wf_id}: unsupported.json names tool {entry['tool_id']!r}, which is not in the "
            f"parsed DAG")
        assert str(entry.get("reason") or entry.get("behavior") or "").strip(), (
            f"{wf_id}: unsupported.json entry for tool {entry['tool_id']} gives no reason")


@pytest.mark.parametrize("wf_id", RECOVERY_WORKFLOWS)
def test_the_recovery_extension_registers_and_explains_the_unknown_tool(wf_id, tmp_path):
    """The extension really does register through `parsers.registry` and really does turn the
    workflow's own `INVARIANT_VIOLATION` into a clean parse -- run against the sample's own source,
    from a tree holding nothing but the replayed files.

    `registry` state is process-global, so it is reset in a `finally`, as every other test that
    loads an extension does.
    """
    import invariants
    import parse
    from parsers import registry

    source = parse.workflow_file(SAMPLES / wf_id / "source")
    xml_text = parse.decode_xml(source.read_bytes())
    try:
        before, _ = parse.parse_file(source)
        assert any("unknown tools without a behavior" in error
                   for error in invariants.check(xml_text, before)), (
            f"{wf_id} parses cleanly without the extension, so the recovery artifacts guard "
            f"nothing")

        ext_dir = _stage_recovery(wf_id, tmp_path) / "scripts" / "parsers" / "ext"
        after, _ = parse.parse_file(source, ext_dirs=[ext_dir])
        assert invariants.check(xml_text, after) == [], (
            f"{wf_id}: the canned extension does not get the workflow past its invariants")
    finally:
        registry.reset()

    explained = [node for node in after["nodes"] if node.get("type") == "unknown"]
    assert explained, f"{wf_id}: no unknown tool left for the extension to explain"
    for node in explained:
        assert str(node.get("behavior") or "").strip(), (
            f"{wf_id}: tool {node['tool_id']} is still unknown with no behavior")
        assert 0.0 <= float(node["confidence"]) < 1.0, (
            f"{wf_id}: tool {node['tool_id']} claims confidence {node.get('confidence')!r}; an "
            f"extension that has not settled the tool's semantics may not claim certainty")
        assert node["raw_config"], (
            f"{wf_id}: tool {node['tool_id']} lost its raw_config, which is the only record of "
            f"what the vendor tool was configured to do")


@pytest.mark.parametrize("wf_id", RECOVERY_WORKFLOWS)
def test_the_recovery_corpus_test_passes_from_a_replayed_tree(wf_id, tmp_path):
    """The test that ships with the extension is the permanent regression gate, so it has to pass
    where the mock runner puts it -- `<root>/tests/parser_corpus/<name>/` beside
    `<root>/scripts/parsers/ext/`, with no conftest and no `pythonpath` to help it.

    Run in this process rather than as a subprocess: a replayed tree holds the extension and the
    fixture but not the parser, so the module under test has to find `parse` on the session's own
    path, which is exactly the fallback it is written for.
    """
    import importlib.util

    from parsers import registry

    root = _stage_recovery(wf_id, tmp_path)
    modules = sorted((root / "tests" / "parser_corpus").glob("*/test_*.py"))
    assert modules, f"{wf_id}: the replayed tree holds no corpus test to run"
    try:
        for index, path in enumerate(modules):
            spec = importlib.util.spec_from_file_location(
                f"replayed_corpus_{wf_id}_{index}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cases = sorted(name for name in dir(module) if name.startswith("test_"))
            assert cases, f"{wf_id}: {path.name} defines no test"
            for case in cases:
                getattr(module, case)()
    finally:
        registry.reset()
