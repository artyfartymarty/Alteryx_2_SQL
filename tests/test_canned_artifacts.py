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
procedure per segment, `RECOVERY_WORKFLOWS` for the ones whose replay is a parser extension
because the workflow never gets far enough to be translated.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

import pytest

from dev import build_samples
from lib import io
from lib.paths import Repo, seg_token, wf_token
from lib.proc_runner import ProcError, parse_proc
from lib.vocab import DIFF_CLASSES
from tests.helpers import copy_pristine_mappings_and_catalog
from tests.test_e2e_parity import broken_cases

ROOT = Path(__file__).parents[1]
SAMPLES = ROOT / "samples"
CATALOG = ROOT / "catalog" / "columns.csv"

#: Contract C4's procedure signature.
C4_PARAMS = ["SRC_DB", "SRC_SCHEMA", "TGT_DB", "TGT_SCHEMA", "RUN_ID"]

#: Node types that carry no data through the segment, so no CTE can correspond to them: the three
#: layout/annotation kinds, Action tools, and Browse (the simulator says a Browse emits nothing).
NO_CTE_TYPES = frozenset({"container", "comment", "interface", "action", "browse"})

#: Top-level keys every contract.json carries (program schema plus plan contract C5).
CONTRACT_KEYS = ("segment", "inputs", "output", "outputs", "row_relation", "ordering",
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
#: context cannot mistake it for a translation anybody should copy.
_BROKEN_HEADER = ("-- BROKEN ON PURPOSE -- do not fix: this is a fixture for "
                  "tests/test_e2e_parity.py.")

CANNED_WORKFLOWS = sorted(path.parent.name for path in SAMPLES.glob("wf_*/canned"))

#: The canned workflows that carry a translation: a procedure, a contract and broken variants per
#: segment. Everything a *migrated* workflow's artifacts have to satisfy is parametrized over
#: these, not over `CANNED_WORKFLOWS`, because a workflow that parks at MANUAL never gets a
#: segment at all (`orchestrator/stages.ts` stops a T3 workflow after the analyzer) and would
#: otherwise fail every one of them for the right reason.
TRANSLATED_WORKFLOWS = sorted(path.parents[1].name for path in SAMPLES.glob("wf_*/canned/segments"))

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


# --- the procedures ----------------------------------------------------------------------------


@pytest.mark.parametrize("wf_id", TRANSLATED_WORKFLOWS)
def test_every_canned_procedure_has_the_c4_signature(wf_id):
    """Plan contract C4, and exactly what `scripts/compile_check.py` rejects a procedure over
    before it looks at anything else."""
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


@pytest.mark.parametrize("wf_id", TRANSLATED_WORKFLOWS)
def test_every_data_node_has_a_cte_or_a_documented_exemption(wf_id, build_workflow):
    """The reviewer agent's first blocking check: every node of the segment's DAG has a CTE, or a
    documented merge in translation_notes.md says why it has none.

    Nodes that carry no data (containers, comments, interface and action tools, and a Browse,
    which the simulator says emits nothing) need neither.
    """
    repo = build_workflow(wf_id)
    for seg, path in _procs(wf_id):
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
    """Contract C4: a mapped source or target is reached only through `IDENTIFIER(...)` on its
    logical name, so the same body runs against the golden view schema and against production.
    The broken variants are checked too -- a fixture is allowed one wrong translation, not a
    hard-coded table name."""
    forbidden = _catalog_names()
    files = [path for _, path in _procs(wf_id)]
    files += sorted((SAMPLES / wf_id / "broken_sql").glob("*/*.sql"))
    for path in files:
        text = path.read_text(encoding="utf-8").upper()
        named = sorted(name for name in forbidden if name in text)
        assert not named, (
            f"{path.relative_to(SAMPLES)} names the mapped table(s) {named}; contract C4 requires "
            f"IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.<LOGICAL>') instead")


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
    stream and `MIG_WORK.<WF>_<SEG>_OUT_<stream>` for the others, and the procedure creates it."""
    repo = build_workflow(wf_id)
    for seg in _segments(repo, wf_id):
        contract = json.loads(
            (_canned(wf_id) / "segments" / seg / "contract.json").read_text(encoding="utf-8"))
        sql_text = (_canned(wf_id) / "segments" / seg / "proc.sql").read_text(encoding="utf-8")
        primary = f"MIG_WORK.{wf_token(wf_id)}_{seg_token(seg)}_OUT"
        for output in contract["outputs"]:
            if output["kind"] != "work":
                continue
            assert output["table"] == primary or output["table"].startswith(primary + "_"), (
                f"{wf_id}/{seg}: work table {output['table']!r} is not {primary} or "
                f"{primary}_<stream> (plan contract C3)")
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


@pytest.mark.parametrize("wf_id", TRANSLATED_WORKFLOWS)
def test_the_canned_artifact_set_is_complete(wf_id, build_workflow):
    """What the mock runner replays for a T1 workflow, one file per agent stage."""
    repo = build_workflow(wf_id)
    canned = _canned(wf_id)
    for relative in ("intake/plan.md", "analysis.md", "unsupported.json", "docs/migration.md"):
        assert (canned / relative).is_file(), f"{wf_id}: canned/{relative} is missing"
    for seg in _segments(repo, wf_id):
        for name in ("contract.json", "proc.sql", "translation_notes.md", "review.json"):
            assert (canned / "segments" / seg / name).is_file(), (
                f"{wf_id}/{seg}: canned/segments/{seg}/{name} is missing")
        review = json.loads(
            (canned / "segments" / seg / "review.json").read_text(encoding="utf-8"))
        assert review == {"verdict": "PASS", "findings": []}, (
            f"{wf_id}/{seg}: review.json is {review}, expected a clean PASS")


@pytest.mark.parametrize("wf_id", TRANSLATED_WORKFLOWS)
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
        for name in ("contract.json", "proc.sql"):
            assert (canned / seg / name).is_file(), (
                f"{wf_id}: prepare_workflow would raise -- canned/segments/{seg}/{name} is missing")
    cases = [param.values for param in broken_cases() if param.values[0] == wf_id]
    assert cases, (
        f"{wf_id}: test_broken_migration_fails_with_the_right_class would collect no case for "
        f"this workflow")


@pytest.mark.parametrize("wf_id", TRANSLATED_WORKFLOWS)
def test_the_documenter_sections_are_all_present(wf_id):
    """The sections `.github/agents/documenter.agent.md` requires of `docs/migration.md`."""
    text = (_canned(wf_id) / "docs" / "migration.md").read_text(encoding="utf-8")
    headings = {line.lstrip("# ").strip().lower() for line in text.splitlines()
                if line.startswith("## ")}
    required = ["overview", "source mappings", "tool → cte map", "assumptions",
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
        sql_path = path.parent / case["segment"] / case["file"]
        assert sql_path.is_file(), f"{where}: no such file {sql_path.relative_to(SAMPLES)}"
        listed.add(sql_path)
        assert case["golden_set"] in golden_sets, (
            f"{where}: golden_set {case['golden_set']!r} is not one of {golden_sets}")
        expect = case["expect"]
        assert expect["class"] in DIFF_CLASSES, (
            f"{where}: diff class {expect['class']!r} is outside the vocabulary")
        assert isinstance(expect["columns"], list) and expect.get("stream")

    on_disk = set(path.parent.glob("*/*.sql"))
    assert on_disk == listed, (
        f"{wf_id}: these broken_sql files have no broken.json row: "
        f"{sorted(str(p.relative_to(SAMPLES)) for p in on_disk - listed)}")


@pytest.mark.parametrize("wf_id", TRANSLATED_WORKFLOWS)
def test_every_broken_variant_is_the_canned_procedure_with_one_documented_mistake(wf_id):
    """A fixture is the *correct* procedure with one realistic translator mistake in it, and it
    says at the top which mistake that is.

    Without this check a variant could silently drift away from the procedure it was cut from --
    most easily by the canned `proc.sql` being edited afterwards and the variant not being
    regenerated -- and it would go on failing validation for a reason that has nothing to do with
    the `broken.json` row explaining it. The parity test would still pass, which is the worst kind
    of green.

    A variant may legitimately *drop* a CTE (forgetting a tool is a realistic mistake); it may not
    invent one, because then it is a different translation rather than a wrong one.
    """
    for sql_path in sorted((SAMPLES / wf_id / "broken_sql").glob("*/*.sql")):
        where = sql_path.relative_to(SAMPLES)
        broken = sql_path.read_text(encoding="utf-8")
        assert broken.startswith(_BROKEN_HEADER), (
            f"{where} does not open with `{_BROKEN_HEADER}`, so a reader who opens it out of "
            f"context cannot tell it is wrong on purpose")

        canned = _canned(wf_id) / "segments" / sql_path.parent.name / "proc.sql"
        assert canned.is_file(), f"{where}: there is no canned proc.sql it could be a variant of"
        correct = canned.read_text(encoding="utf-8")

        marker = "CREATE OR REPLACE PROCEDURE"
        assert marker in broken, f"{where}: no {marker} statement"
        header, _, body = broken.partition(marker)
        assert "The mistake:" in header, (
            f"{where}: the header comment does not say what the mistake is (`The mistake: …`)")
        assert body != correct.partition(marker)[2], (
            f"{where} is byte-identical to {canned.relative_to(SAMPLES)} below the header: a "
            f"fixture that is not broken proves nothing")

        invented = _cte_names(body) - _cte_names(correct)
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
