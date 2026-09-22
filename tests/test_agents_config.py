"""Task 12a: the nine Copilot custom-agent definitions and config.json (plan Task 12, phase 04).

Agent bodies are copied verbatim from program spec 01-copilot-setup.md Part A §4, with the
amendments listed in the phase-04 brief applied on top (each marked `<!-- amended: plan Task 12 -->`
so a reader can diff the file against the spec). These tests check the frontmatter contract every
file must satisfy and the specific amendments the brief and the controller called out by name.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / ".github" / "agents"
SUFFIX = ".agent.md"

# The nine custom agents (plan Task 12 file list) plus the five built-ins config.json must keep
# untouched (program spec 01-copilot-setup.md Part A §1).
CUSTOM_AGENTS = [
    "intake", "analyzer", "translator", "reviewer", "validator",
    "fixer", "parser-recovery", "documenter", "cookbook-curator",
]
BUILTIN_AGENTS = ["task", "explore", "research", "rubber-duck", "general-purpose"]


def _agent_files():
    return sorted(AGENTS_DIR.glob(f"*{SUFFIX}"))


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter, body) for a `---`-fenced Markdown file. Fails loudly if the fence is malformed.

    Parsed as flat `key: value` lines (split on the first ": "), not full YAML: several spec
    descriptions contain a colon inside the value itself (documenter's does), which a strict YAML
    parser rejects as an ambiguous nested mapping. The CLI's own frontmatter is documented as three
    flat string keys (program spec 01-copilot-setup.md Part A §1), so a flat parse is the faithful
    model of what it loads, not a workaround.
    """
    assert text.startswith("---\n"), "file must open with a `---` frontmatter fence"
    end = text.index("\n---\n", 4)
    front: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(": ")
        assert sep, f"malformed frontmatter line (expected 'key: value'): {line!r}"
        front[key.strip()] = value.strip()
    body = text[end + 5:]
    return front, body


def _read(name: str) -> tuple[dict, str]:
    return _split_frontmatter((AGENTS_DIR / f"{name}{SUFFIX}").read_text(encoding="utf-8"))


# --- file inventory -----------------------------------------------------------------------

def test_nine_agent_files_exist_and_nothing_else():
    found = {p.name[: -len(SUFFIX)] for p in _agent_files()}
    assert found == set(CUSTOM_AGENTS)


# --- frontmatter contract: exactly name, description, model; name equals the file stem -----

def test_every_agent_has_exactly_name_description_model_frontmatter():
    for path in _agent_files():
        front, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
        assert set(front.keys()) == {"name", "description", "model"}, path.name
        assert isinstance(front["name"], str) and isinstance(front["description"], str)
        assert isinstance(front["model"], str)


def test_agent_name_equals_file_stem():
    for path in _agent_files():
        stem = path.name[: -len(SUFFIX)]
        front, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
        assert front["name"] == stem, path.name


def test_intake_frontmatter_dropped_the_tools_comment():
    text = (AGENTS_DIR / f"intake{SUFFIX}").read_text(encoding="utf-8")
    front_block = text.split("\n---\n", 1)[0]
    assert "tools" not in front_block.lower()


# --- amendments named in the phase-04 brief --------------------------------------------------

def test_intake_amendments():
    _, body = _read("intake")
    assert "scripts/intake_touchpoints.py" in body
    assert "intake/touchpoints.json" in body
    assert "ask_user" in body
    assert "scripts/intake_prompt.py" in body and "--no-interactive" in body
    assert "logical" in body and "C6" in body
    # step 1 no longer hand-enumerates touchpoints
    assert "Enumerate every external touchpoint in dag.json" not in body


def test_analyzer_amendments():
    """Contract C5 adds four things to contract.json: `outputs[]`, `inputs[].logical`,
    `inputs[].stream`, and `ordering.order_dependent_columns` (docs/superpowers/plans/
    2026-09-18-pipeline/00-index.md). The analyzer amendment must state all four, not a subset --
    `inputs[].stream` is load-bearing in the validation driver, which loads a segment-chained
    input by its `stream`."""
    _, body = _read("analyzer")
    assert "outputs[]" in body
    assert "inputs[].logical" in body
    assert "inputs[].stream" in body
    assert "ordering.order_dependent_columns" in body
    assert "C5" in body


def test_analyzer_documents_inputs_from_and_table_for_segment_chained_inputs():
    """`validate_segment.py` REQUIRES `inputs[].from` and `inputs[].table` for any input that
    carries a `stream` (it raises otherwise) -- the analyzer amendment must name both, and say what
    each holds: `from` is the upstream segment id, `table` is the literal
    `MIG_WORK.<WF>_<SEG>_OUT[_<stream>]` table (contract C3)."""
    _, body = _read("analyzer")
    assert "inputs[].from" in body
    assert "inputs[].table" in body
    assert "MIG_WORK.<WF>_<SEG>_OUT" in body


def test_analyzer_documents_segment_and_normalizations_contract_keys():
    """`compare.py`'s `_verdict` matches an approval's `segment` field against
    `contract.get("segment")`: an analyzer that never writes `segment` silently blocks every
    PASS_WITH_ACCEPTED_DIFF for that contract, because no real approval will ever equal `None`.
    `normalizations` is the other top-level key `compare.py` reads directly."""
    _, body = _read("analyzer")
    assert '"segment"' in body
    assert "PASS_WITH_ACCEPTED_DIFF" in body
    assert '"normalizations"' in body


def test_analyzer_documents_unsupported_json_tier_shape():
    """`unsupported.json` carries a top-level `"tier"` (T1/T2/T3) that the orchestrator reads FROM
    THAT FILE, not manifest.json, both for a canned replay (`runner.ts`) and for the live
    analyze-stage completion check (`stages.ts`)."""
    _, body = _read("analyzer")
    assert '"tier"' in body
    assert "T1" in body and "T2" in body and "T3" in body
    assert "runner.ts" in body
    assert "stages.ts" in body


def test_translator_mentions_tgt_schema_and_compile_check():
    _, body = _read("translator")
    assert "TGT_SCHEMA" in body
    assert "scripts/compile_check.py" in body


def test_translator_states_contract_c4_in_full():
    _, body = _read("translator")
    assert "SRC_DB and SRC_SCHEMA parameters" not in body  # the old, replaced sentence
    for token in (
        "MIG_WORK.<WF>_<SEG>", "SRC_DB STRING", "SRC_SCHEMA STRING", "TGT_DB STRING",
        "TGT_SCHEMA STRING", "RUN_ID STRING", "RETURNS STRING", "LANGUAGE SQL",
        "EXECUTE AS CALLER", "IDENTIFIER(:SRC_DB", "IDENTIFIER(:TGT_DB", "RETURN 'OK';",
    ):
        assert token in body, token


def test_translator_documents_compile_check_signature_rejection():
    _, body = _read("translator")
    assert "compile_check.py" in body
    assert "EXECUTE AS CALLER" in body
    assert "SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA, RUN_ID" in body


def test_translator_documents_money_arithmetic_in_number():
    _, body = _read("translator")
    lowered = body.lower()
    assert "number" in lowered and "round(" in lowered
    assert "1.005" in body  # the worked FLOAT-vs-NUMBER example the controller specified


def test_translator_documents_compile_check_exit_codes():
    _, body = _read("translator")
    assert "exits 0" in body
    assert "exit 2" in body


def test_validator_mentions_validate_segment():
    _, body = _read("validator")
    assert "scripts/validate_segment.py" in body


def test_validator_amendment_replaces_old_procedure_steps():
    _, body = _read("validator")
    assert "interpretation" in body
    assert "Never edit numbers" in body
    # the old multi-step procedure (deploy / compare.py CLI invocation) is gone
    assert "using the snowflake tool (sandbox role only)" not in body


def test_validator_documents_compare_verdict_rule():
    _, body = _read("validator")
    assert "PASS_WITH_ACCEPTED_DIFF" in body
    assert "nothing was truncated" in body or "nothing to have been truncated" in body


def test_validator_documents_exit_codes():
    _, body = _read("validator")
    assert "exit 2" in body
    assert "stop and report" in body


def test_reviewer_amendments_add_two_blocking_checks():
    _, body = _read("reviewer")
    assert "procedure matches the C4 signature and is a linear statement list" in body
    assert "IDENTIFIER with logical names" in body


def test_reviewer_documents_compile_check_signature_as_blocking():
    _, body = _read("reviewer")
    assert "SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA, RUN_ID" in body
    assert "EXECUTE AS CALLER" in body


def test_reviewer_documents_money_arithmetic_advisory_check():
    _, body = _read("reviewer")
    assert "1.005" in body
    assert "NUMBER" in body


def test_amended_paragraphs_are_marked():
    """Every agent that the brief or the controller amended carries at least one traceability
    marker; the four untouched agents (fixer, parser-recovery, documenter, cookbook-curator)
    carry none, since their spec bodies were copied verbatim with no amendment."""
    amended = {"intake", "analyzer", "translator", "reviewer", "validator"}
    marker = "<!-- amended: plan Task 12 -->"
    for name in CUSTOM_AGENTS:
        _, body = _read(name)
        if name in amended:
            assert marker in body, name
        else:
            assert marker not in body, name


# --- fixer / parser-recovery / documenter / cookbook-curator: verbatim, no amendments -------

def test_unamended_agents_keep_their_spec_bodies():
    _, fixer = _read("fixer")
    assert "no whole-procedure rewrites; never change tolerances; never touch golden/ or cookbook/." in fixer

    _, parser_recovery = _read("parser-recovery")
    assert "At most 2 attempts per workflow" in parser_recovery

    _, documenter = _read("documenter")
    assert "never add claims about behavior that no artifact supports" in documenter

    _, cookbook_curator = _read("cookbook-curator")
    assert "never edit cookbook/*.md directly; proposals only." in cookbook_curator


# --- config.json ------------------------------------------------------------------------------

def test_config_json_parses():
    obj = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    assert isinstance(obj, dict)


def test_config_json_concurrency_and_depth():
    obj = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    assert obj["subagents"]["maxConcurrency"] == 4
    assert obj["subagents"]["maxDepth"] == 2


def test_config_json_lists_all_nine_custom_agents_plus_five_builtins():
    obj = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    agents = obj["subagents"]["agents"]
    assert set(agents.keys()) == set(CUSTOM_AGENTS) | set(BUILTIN_AGENTS)


def test_config_json_agent_models_match_the_agent_frontmatter():
    obj = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    agents = obj["subagents"]["agents"]
    for name in CUSTOM_AGENTS:
        front, _ = _read(name)
        assert agents[name]["model"] == front["model"], name
