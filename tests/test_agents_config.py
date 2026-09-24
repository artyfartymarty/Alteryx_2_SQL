"""Task 12a: the nine Copilot custom-agent definitions and config.json (plan Task 12, phase 04).

Agent bodies are copied verbatim from program spec 01-copilot-setup.md Part A §4, with the
amendments listed in the phase-04 brief applied on top (each marked `<!-- amended: plan Task 12 -->`
so a reader can diff the file against the spec). These tests check the frontmatter contract every
file must satisfy and the specific amendments the brief and the controller called out by name.
"""
import getpass
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / ".github" / "agents"
SUFFIX = ".agent.md"

#: An absolute path of *this* PC -- never legitimate in a hand-off artifact.
_ABS_PATH_RE = re.compile(r"[a-zA-Z]:[\\/]Users[\\/]", re.IGNORECASE)

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
        "EXECUTE AS CALLER", "IDENTIFIER(:<LOGICAL>_SRC)", "IDENTIFIER(:<LOGICAL>_TGT)", "RETURN 'OK';",
    ):
        assert token in body, token


def test_translator_states_the_documented_identifier_form():
    """Task C4V: Snowflake documents `IDENTIFIER(` with one value, not an expression, so the
    translator builds each name with a LET first -- and says the form has not run on Snowflake."""
    _, body = _read("translator")
    assert "IDENTIFIER(:SRC_DB" not in body and "IDENTIFIER(:TGT_DB" not in body  # the old form
    assert "LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>';" in body
    assert "LET <LOGICAL>_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.<LOGICAL>';" in body
    assert "c4:let_form" in body and "c4:identifier_expression" in body
    assert ":= :SRC_DB" not in body and ":= :TGT_DB" not in body  # fix round 1: no colon inside a LET
    assert "without a colon" in body
    assert "documented" in body and "first real-account run" in body
    assert "with no `LET`" not in body  # the old C4 sentence that forbade every LET


def test_reviewer_checks_the_documented_identifier_form():
    _, body = _read("reviewer")
    assert "IDENTIFIER(:<LOGICAL>_SRC)" in body and "never an expression inside" in body


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


# --- output targets, phase 1: the Snowpark parts of each agent (design §8) -------------------
# The orchestrator now dispatches per segment on `contract.json.target`, so every agent that
# touches a segment has to know which artefact it is looking at. These check the specific
# statements the task brief named, not the prose around them.

def test_analyzer_documents_target_check_and_the_lower_only_rule():
    _, body = _read("analyzer")
    assert "scripts/target_check.py" in body
    assert "segments/targets.json" in body
    assert '"target"' in body
    # the direction of the rule, both halves, and what the orchestrator does about a violation
    assert "only LOWER" in body or "only lower" in body
    assert "never back towards `sql`" in body
    assert "target-mismatch: <seg> raised <proposal> to <contract>" in body
    assert "target-missing: <seg>" in body


def test_translator_carries_the_snowpark_rules_of_design_4_2():
    _, body = _read("translator")
    for token in (
        "def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id) -> str",
        "snowflake.snowpark.functions", "snowflake.snowpark.types",
        "session.sql", "__import__", "subprocess",
        'session.table(f"{src_db}.{src_schema}.<LOGICAL>")',
        '.write.mode("overwrite" | "append").save_as_table',
        "# tool <id>:", "to_pandas()",
        "scripts/render_snowpark.py", "--target snowpark",
    ):
        assert token in body, token


def test_translator_snowpark_rules_bullet_is_verbatim_from_the_design():
    """Design §4.2's `- Rules (…): …` bullet is copied into the translator VERBATIM (task brief:
    "copy them verbatim into the translator agent"), so the agent and the spec can never drift.
    Compared with newlines normalized only -- the agent files are CRLF here, the spec is LF."""
    spec = (ROOT / "docs" / "superpowers" / "specs"
            / "2026-09-22-output-targets-design.md").read_text(encoding="utf-8")
    start = spec.index("- Rules (checked by `compile_check.py --target snowpark`, §5.1)")
    end = spec.index("\n- Rendered artefact:", start)
    bullet = spec[start:end].replace("\r\n", "\n")
    _, body = _read("translator")
    assert bullet in body.replace("\r\n", "\n"), (
        "the translator's Snowpark rules bullet is not byte-identical to design §4.2's:\n"
        f"--- spec ---\n{bullet}")


def test_translator_forbids_hand_writing_the_rendered_proc_sql():
    _, body = _read("translator")
    assert "RENDERED artefact" in body
    assert "Never write or edit `proc.sql`" in body


def test_reviewer_blocks_on_the_snowpark_rules_and_the_proc_sql_proc_py_pair():
    _, body = _read("reviewer")
    assert "proc.py" in body
    assert "scripts/render_snowpark.py" in body
    assert "session.sql" in body
    assert "# tool <id>:" in body
    # row-sequential pandas has to be justified in the notes, per design §4.2
    assert "to_pandas()" in body and "translation_notes.md" in body


def test_validator_picks_its_script_from_the_contract_target():
    _, body = _read("validator")
    assert "scripts/validate_snowpark.py" in body
    assert "scripts/validate_segment.py" in body
    assert '"target"' in body
    # the honesty statement design §9 requires wherever the local double is named
    assert "Local Testing Framework" in body
    assert "subset" in body


def test_fixer_repairs_proc_py_and_never_proc_sql():
    _, body = _read("fixer")
    assert "proc.py" in body
    assert "Never edit `proc.sql` by hand" in body
    assert "scripts/render_snowpark.py" in body


def test_copilot_instructions_carry_one_targets_paragraph():
    text = (ROOT / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
    assert "docs/reference/output-targets.md" in text
    assert "scripts/target_check.py" in text
    assert "scripts/render_snowpark.py" in text
    assert "lower" in text.lower()


def test_no_machine_path_or_user_name_in_any_agent_or_instruction_file():
    """Hand-off rule: no absolute path of this PC, no OS login name, anywhere in .github/."""
    login = getpass.getuser().lower()
    files = sorted(AGENTS_DIR.glob(f"*{SUFFIX}")) + [ROOT / ".github" / "copilot-instructions.md"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not _ABS_PATH_RE.search(text), f"{path.name} carries an absolute machine path"
        assert login not in text.lower(), f"{path.name} carries the OS login name"


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


# --- final fix wave F5 (review I5): column order is a documented rule, not folklore -------------
# A Snowpark procedure hand-builds its output `StructType`, so getting the column ORDER wrong is
# an easy slip -- and `compare.py` reports it as a `TYPE` difference naming every column, which
# points the fixer at types rather than at order. Nothing on the branch told the translator the
# rule; the review reproduced the FAIL (probe A3). The sentence now lives in all four places a
# translator, a reviewer or a reader would look.

COLUMN_ORDER_SENTENCE = (
    "`StructType` must list the columns in the contract's declared `outputs[].columns` order: a "
    "different order is a schema FAIL, reported as a `TYPE` difference rather than as an ordering "
    "one"
)

COLUMN_ORDER_HOMES = (
    "docs/reference/output-targets.md",
    ".github/agents/translator.agent.md",
    ".github/agents/reviewer.agent.md",
    "samples/wf_0006/canned/segments/seg_02/translation_notes.md",
)


def _squeezed(rel: str) -> str:
    return " ".join((ROOT / rel).read_text(encoding="utf-8").split())


def test_the_column_order_rule_is_stated_wherever_a_snowpark_translation_is_described():
    wanted = " ".join(COLUMN_ORDER_SENTENCE.split())
    for rel in COLUMN_ORDER_HOMES:
        assert wanted in _squeezed(rel), f"{rel} does not state the column-order rule"


# --- round 2, R4: the spec, the translator and the reference page name what the code refuses ----
# The translator's §4.2 bullet is pinned byte-identical to the spec's above. This pins the other
# direction: every name `snowpark_rules` actually refuses must be written down in the spec bullet
# AND in docs/reference/output-targets.md, so a deny-list that grows in code cannot quietly stop
# being the contract the translator was handed.

def _spec_rules_bullet() -> str:
    spec = (ROOT / "docs" / "superpowers" / "specs"
            / "2026-09-22-output-targets-design.md").read_text(encoding="utf-8")
    start = spec.index("- Rules (checked by `compile_check.py --target snowpark`, §5.1)")
    return spec[start:spec.index("\n- Rendered artefact:", start)].replace("\r\n", "\n")


def _refused_names() -> list[str]:
    from lib import snowpark_rules as rules
    return sorted(rules.FORBIDDEN_NAMES | rules.FORBIDDEN_MODULES | rules.RAW_SQL_NAMES
                  | rules.FORBIDDEN_SINKS | rules.PANDAS_WRITERS | rules.CALL_ONLY_ATTRS
                  | rules.FORBIDDEN_SESSION_NAMES | rules.NO_SESSION_SQL_ATTRS
                  | rules.FORBIDDEN_SESSION_ATTRS | rules.NUMPY_WRITERS)


def _names_missing_from(text: str) -> list[str]:
    """A name counts as written down only when it appears in code ticks -- `save`, not the `save`
    inside `save_as_table`. `sql` and `call` are written as `session.sql` / `session.call`, which
    is how a reader meets them."""
    squeezed = " ".join(text.split())
    return [name for name in _refused_names()
            if f"`{name}`" not in squeezed and f"`session.{name}`" not in squeezed]


def test_the_spec_rules_bullet_names_every_refusal_the_rules_implement():
    missing = _names_missing_from(_spec_rules_bullet())
    assert not missing, f"design §4.2's rules bullet does not mention: {missing}"


def test_the_reference_page_names_every_refusal_the_rules_implement():
    text = (ROOT / "docs" / "reference" / "output-targets.md").read_text(encoding="utf-8")
    missing = _names_missing_from(text)
    assert not missing, f"docs/reference/output-targets.md does not mention: {missing}"


def test_the_translator_carries_the_spec_bullet_and_therefore_every_refusal():
    _, body = _read("translator")
    assert _spec_rules_bullet() in body.replace("\r\n", "\n")
    assert not _names_missing_from(body)


# --- output targets, phase 2: the dbt parts of each agent (design §4.3, §6, §8; Task D) -----------
# A dbt workflow is translated ONCE as one project, checked by `compile_check.py <id> --target dbt`
# (sixteen named checks since the final fix wave's closed surface) and validated by `validate_dbt.py`. The translator must be taught the rules
# exactly as compile_check enforces them, so the pins below run compile_check's own code against
# the constructs the agent file names, the way phase 1 pinned every name `snowpark_rules` refuses.

PHASE_2_MARKER = "<!-- amended: output targets phase 2 -->"
PHASE_2_AGENTS = ("translator", "reviewer", "validator", "fixer", "documenter")

#: Jinja a migration model may use -- each must be ACCEPTED by compile_check and written down.
DBT_JINJA_ALLOWED = (
    "{{ config(materialized='table') }}",
    "{{ source('src', '<LOGICAL>') }}",
    "{{ ref('<model>') }}",
    "{{ this }}",
    "{% if is_incremental() %}",
    "{% else %}",
    "{% endif %}",
    "{# … #}",
)

#: What the agent file names as refused (in code ticks), and a construct compile_check refuses for
#: that name -- `test_model_jinja_is_a_closed_allow_list`'s cases, one per name.
DBT_JINJA_REFUSED = {
    "env_var": "{{ env_var('X') }}",
    "var": "{{ var('src_schema') }}",
    "run_query": "{{ run_query('select 1') }}",
    "statement": "{% call statement('x') %}",
    "adapter": "{{ adapter.execute('select 1') }}",
    "{% for %}": "{% for x in y %}",
    "{{ source('src', env_var('X')) }}": "{{ source('src', env_var('X')) }}",
}


def _section(name: str, heading: str) -> str:
    """The section of `name`'s agent file that starts at `heading`, up to the next `## ` heading."""
    _, body = _read(name)
    start = body.index(heading)
    end = body.find("\n## ", start + 1)
    return body[start:] if end < 0 else body[start:end]


def _bullet(text: str, start: str) -> str:
    """One top-level `- ` bullet of `text` (with its indented continuation lines)."""
    at = text.index(start)
    end = text.find("\n- ", at + 1)
    return text[at:] if end < 0 else text[at:end]


def _jinja_refusals(tmp_path: Path, construct: str) -> list[str]:
    """compile_check's own `dbt:model_jinja` check over one model holding `construct` -- the exact
    code path `compile_check_dbt` runs, without `dbt parse`."""
    import compile_check
    models = tmp_path / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "x.sql").write_text(f"{construct}\nselect 1 as ID\n", encoding="utf-8")
    return compile_check._dbt_jinja_errors(tmp_path)


def _dbt_check_names() -> list[str]:
    """Every `dbt:<check>` name `compile_check.py --target dbt` can report: its own, and the closed
    surface's (`scripts/lib/dbt_surface.py`, final fix wave C1), which it reports too."""
    source = "\n".join((ROOT / "scripts" / name).read_text(encoding="utf-8")
                       for name in ("compile_check.py", "lib/dbt_surface.py"))
    return sorted(set(re.findall(r"""['"]dbt:([a-z_]+)(?::|['"])""", source)))


def test_phase_2_amendments_are_marked():
    for name in PHASE_2_AGENTS:
        _, body = _read(name)
        assert PHASE_2_MARKER in body, name


def test_translator_carries_the_dbt_rules():
    section = _section("translator", "## dbt projects")
    for token in (
        "alias='<LOGICAL>'", "Never run `dbt` yourself", "compile_check.py <id> --target dbt",
        "one model per final target", "ONCE", "no segment", "workflows/<id>/dbt/",
        "materialized='table'", "incremental_strategy='append'", "incremental_strategy='merge'",
        "unique_key=[", "pre_hook", "post_hook", "{{ this }}", "models/sources.yml",
        "models/schema.yml", "not_null", "unique", "-- tool <id>:", "translation_notes.md",
        "README.md", "cookbook/dbt.md", "dbt/review.json", "dbt/compile_check.json",
    ):
        assert token in section, token
    assert "is_incremental()" in section


def test_translator_points_at_both_new_cookbook_pages():
    _, body = _read("translator")
    assert "cookbook/snowpark.md" in body and "cookbook/dbt.md" in body


def test_translator_carries_the_profile_template_byte_for_byte():
    """`dbt:profiles` compares profiles.yml byte for byte with `PROFILES_TEMPLATE`, so the agent
    is handed the template itself, not a description of it."""
    from lib import dbt_project
    section = _section("translator", "## dbt projects")
    assert dbt_project.PROFILES_TEMPLATE in section.replace("\r\n", "\n")
    assert "byte for byte" in section


def test_translator_names_every_dbt_check_compile_check_runs():
    names = _dbt_check_names()
    # design §5.1's eleven, plus the closed surface's five (final fix wave C1)
    assert names == ["columns", "hook_sql", "hooks", "layout", "model_config", "model_jinja", "model_missing",
                     "model_orphan", "model_sql", "parse", "profiles", "project_yml", "sources", "surface",
                     "tool_comments", "yaml"], names
    section = _section("translator", "## dbt projects")
    missing = [name for name in names if f"dbt:{name}" not in section]
    assert not missing, f"the translator's dbt section does not name: {missing}"


def test_translator_teaches_the_closed_jinja_allow_list_exactly_as_compile_check_enforces_it(tmp_path):
    jinja = _bullet(_section("translator", "## dbt projects"), "- **Model Jinja")
    squeezed = " ".join(jinja.split())
    for construct in DBT_JINJA_ALLOWED:
        assert f"`{construct}`" in squeezed, f"the allow-list bullet does not name {construct}"
        concrete = construct.replace("<LOGICAL>", "ORDERS").replace("<model>", "orders_out").replace("…", "note")
        refusals = _jinja_refusals(tmp_path / "ok" / str(DBT_JINJA_ALLOWED.index(construct)), concrete)
        assert not refusals, (construct, refusals)
    for name, construct in DBT_JINJA_REFUSED.items():
        assert f"`{name}`" in squeezed, f"the allow-list bullet does not name the refused {name}"
        refusals = _jinja_refusals(tmp_path / "refused" / str(list(DBT_JINJA_REFUSED).index(name)), construct)
        assert refusals and refusals[0].startswith("dbt:model_jinja"), (name, refusals)


def test_translator_states_the_orphan_and_alias_rules():
    squeezed = " ".join(_section("translator", "## dbt projects").split())
    # dbt:model_orphan -- a model that is no contract output's would deploy an untracked table
    assert "dbt:model_orphan" in squeezed
    assert "every `models/*.sql` is some contract output's model" in squeezed
    # the alias is not optional: dbt refuses a lower-case model over an upper-case table (S3)
    assert "upper case" in squeezed and "approximate match" in squeezed


def test_reviewer_blocks_on_the_dbt_project_shape():
    section = _section("reviewer", "## Blocking checks for a dbt project")
    squeezed = " ".join(section.split())
    for token in (
        "one model per contract output", "unique_key", "the mapping's keys", "alias='<LOGICAL>'",
        "upper case", "profiles.yml", "PROFILES_TEMPLATE", "models/schema.yml", "in order",
        "{{ source('src', '<LOGICAL>') }}", "-- tool <id>:", "workflows/<id>/dbt/review.json",
    ):
        assert token in squeezed, token


def test_validator_picks_validate_dbt_for_a_dbt_workflow():
    _, body = _read("validator")
    squeezed = " ".join(body.split())
    assert "python scripts/validate_dbt.py <id>" in squeezed
    assert "once for the whole workflow" in squeezed
    assert '"target": "dbt"' in squeezed
    assert "stop and report" in squeezed
    # the dbt-duckdb caveats of design §9, where the local double is named
    for token in ("dbt-duckdb", "case folding", "merge", "hooks run on DuckDB"):
        assert token in squeezed, token


def test_fixer_repairs_dbt_models_and_never_the_profile():
    section = _section("fixer", "## dbt projects")
    squeezed = " ".join(section.split())
    for token in ("Never edit `profiles.yml`", "Never run `dbt`", "dbt/fix_log.md", "smallest change",
                  "Failing models"):
        assert token in squeezed, token


def test_documenter_requires_a_deployment_section_per_output_kind():
    section = _section("documenter", "## Deployment")
    squeezed = " ".join(section.split())
    for token in (
        "procs/master.sql", "segments/<seg>/proc.sql", "LANGUAGE PYTHON", "RUNTIME_VERSION", "PACKAGES",
        "procs/README.md", "SNOWFLAKE_", "dbt-snowflake", "dbt/translation_notes.md",
        "never deployed from this session",
    ):
        assert token in squeezed, token


def test_copilot_instructions_describe_dbt_as_built():
    text = " ".join((ROOT / ".github" / "copilot-instructions.md").read_text(encoding="utf-8").split())
    assert "scripts/validate_dbt.py" in text
    assert "does not yet build" not in text
    assert "workflows/<id>/dbt/" in text
    assert "compile_check.py <id> --target dbt" in text
    assert "never run `dbt`" in text.lower()


def test_the_reference_page_describes_the_dbt_target_as_compile_check_enforces_it():
    from lib import dbt_project
    text = (ROOT / "docs" / "reference" / "output-targets.md").read_text(encoding="utf-8")
    assert dbt_project.PROFILES_TEMPLATE in text.replace("\r\n", "\n"), "§3.3 carries PROFILES_TEMPLATE verbatim"
    missing = [name for name in _dbt_check_names() if f"dbt:{name}" not in text]
    assert not missing, f"docs/reference/output-targets.md does not name: {missing}"
    squeezed = " ".join(text.split())
    for token in ("scripts/validate_dbt.py", "dbt_sandbox_<set>.duckdb", "MIGDB__MIG_WORK",
                  "approximate match", "dbt: <reason>", "procs/README.md", "dbt-snowflake"):
        assert token in squeezed, token
    assert "not exercised by anything in phase 1" not in squeezed


def _takes_the_workflow_id_first(script: str) -> bool:
    """Whether `scripts/<script>.py`'s argparse declares `wf_id` as its first argument -- the
    scripts `orchestrator/policy.ts`'s WORKFLOW_ID_SCRIPTS holds to the session's workflow."""
    path = ROOT / "scripts" / f"{script}.py"
    if not path.is_file():
        return False
    first = re.search(r'add_argument\(\s*"([^"]+)"', path.read_text(encoding="utf-8"))
    return bool(first) and first.group(1) == "wf_id"


def test_every_agent_command_example_puts_the_workflow_id_first():
    """Task D fix round 2: the policy judges a workflow script's FIRST bare token as its workflow
    (fix round 1, G2), so a command an agent file shows must put the `<id>` placeholder first --
    never a flag first, whose value would then be read as the workflow. Every code span naming a
    workflow script is checked; a span naming the script with no argument is only a reference."""
    files = sorted(AGENTS_DIR.glob(f"*{SUFFIX}")) + [ROOT / ".github" / "copilot-instructions.md"]
    offenders, examples = [], 0
    for path in files:
        for span in re.findall(r"`([^`\n]+)`", path.read_text(encoding="utf-8")):
            for match in re.finditer(r"scripts/([a-z_]+)\.py((?:\s+\S+)*)", span):
                if not _takes_the_workflow_id_first(match.group(1)):
                    continue
                args = match.group(2).split()
                if not args:
                    continue
                examples += 1
                if args[0] not in ("<id>", "<wf_id>", "<wf>"):
                    offenders.append(f"{path.name}: `{span}`")
    assert examples >= 10, f"only {examples} command examples found -- the scan is not seeing them"
    assert not offenders, "a command example does not put the workflow id first:\n" + "\n".join(offenders)


def test_no_agent_file_shows_a_script_call_with_root():
    """Task D fix round 2: `--root` is denied in every agent's script call, so no agent file may
    show one."""
    for path in sorted(AGENTS_DIR.glob(f"*{SUFFIX}")) + [ROOT / ".github" / "copilot-instructions.md"]:
        assert "--root" not in path.read_text(encoding="utf-8"), path.name


def test_the_readme_describes_the_dbt_target_as_built():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.index("### Three output targets")
    section = " ".join(readme[start:readme.index("\n## ", start)].split())
    assert "decided and recorded only" not in section
    assert "in phase 1 that is as far as it goes" not in section
    for token in ("workflows/wf_0007/", "compile_check.py <wf> --target dbt", "validate_dbt.py",
                  "dbt-duckdb", "dbt-snowflake"):
        assert token in section, token


def test_fixer_reads_the_chain_report_when_the_stitched_workflow_diverges():
    """Task W1 fix round 3: the chain check's one fixer round points at
    `workflows/<id>/validation_workflow.json`; the fixer's inputs name it."""
    _, body = _read("fixer")
    inputs = body[body.index("## Inputs"):body.index("## Procedure")]
    assert "workflows/<id>/validation_workflow.json" in inputs
    assert PHASE_2_MARKER in inputs


def test_analyzer_documents_batches_and_seams():
    """Task W2: above a character budget the analyzer runs batch by batch; each call writes its
    batch's contracts plus `analysis/<batch>.md` (the script stitches analysis.md), and every seam
    is checked by `scripts/check_seams.py`, parking `seam-mismatch: …` after one retry."""
    _, body = _read("analyzer")
    squeezed = " ".join(body.split())
    for token in ("analysis/<batch>.md", "analysis/<batch>.unsupported.json", "scripts/check_seams.py",
                  "scripts/stitch_analysis.py", "seam-mismatch: <producer>-><consumer> <stream>",
                  "same columns in order, same type family, same nullability, same keys"):
        assert token in squeezed, token
    assert PHASE_2_MARKER in body


def test_no_agent_file_shows_a_script_call_with_a_backend_flag():
    """Follow-up to Task W2: `--backend`, `--connection` and `--sandbox-database` are denied in
    every agent's script call (orchestrator/policy.ts), so no agent file may show one."""
    for path in sorted(AGENTS_DIR.glob(f"*{SUFFIX}")) + [ROOT / ".github" / "copilot-instructions.md"]:
        text = path.read_text(encoding="utf-8")
        for flag in ("--backend", "--connection", "--sandbox-database"):
            assert flag not in text, f"{path.name} shows {flag}"


# --- output targets, phase 2, Task W4: a compaction memory aid -------------------------------
# intake, analyzer and fixer each keep a notes file that survives a context compaction; the durable
# record still lives in the contract and the files they write, never in the notes.

def test_intake_analyzer_and_fixer_keep_notes():
    for name in ("intake", "analyzer", "fixer"):
        _, body = _read(name)
        squeezed = " ".join(body.split())
        assert f"notes/{name}.md" in squeezed, name
        assert "the durable record stays in the contract and the files" in squeezed, name
        assert PHASE_2_MARKER in body, name


# --- live hardening, Task L1 (R1): scripts run as `python scripts/…` from any run root ----------
# A run root is a copy WITHOUT `.venv` whose orchestrator.config.json names the interpreter by
# absolute path, so the old `.venv/Scripts/python.exe scripts/…` form resolved nowhere and the live
# models probed for an interpreter instead. The orchestrator now puts the configured interpreter
# first on PATH for every agent session (orchestrator/cli.ts's `sessionEnvironment`).

L1_MARKER = "<!-- amended: live hardening L1 -->"


def test_every_agent_runs_scripts_as_python_on_the_session_path():
    files = sorted(AGENTS_DIR.glob(f"*{SUFFIX}")) + [ROOT / ".github" / "copilot-instructions.md"]
    assert len(files) == 10
    for path in files:
        text = path.read_text(encoding="utf-8")
        squeezed = " ".join(text.split())
        for venv in (".venv/Scripts", ".venv/bin", ".venv\\Scripts", "python.exe"):
            assert venv not in text, f"{path.name} still names {venv}"
        assert "`python scripts/<name>.py …`" in squeezed, path.name
        assert "never through a `.venv/…` path" in squeezed, path.name
        if path.name == f"cookbook-curator{SUFFIX}":
            # Fix round 1 (M8): the curator is CLI-only (README §1), so no orchestrator puts anything
            # on its PATH -- the file must not claim one does.
            assert "orchestrator puts" not in squeezed, path.name
            assert "no orchestrator starts you" in squeezed, path.name
        else:
            assert "puts the project's interpreter first on PATH" in squeezed, path.name
        if path.suffix == ".md" and path.name.endswith(SUFFIX):
            assert L1_MARKER in text, path.name


def _role_scripts() -> dict[str, set[str]]:
    """`orchestrator/policy.ts`'s `ROLE_SCRIPTS`: the scripts each role's session may run."""
    text = (ROOT / "orchestrator" / "policy.ts").read_text(encoding="utf-8")
    body = text[text.index("export const ROLE_SCRIPTS"):]
    body = re.sub(r"//[^\n]*", "", body[body.index("{") + 1:body.index("\n};")])
    return {role.strip('"'): set(re.findall(r'"(scripts/[a-z_]+\.py)"', scripts))
            for role, scripts in re.findall(r'("?[a-z-]+"?)\s*:\s*\[([^\]]*)\]', body)}


def test_every_script_an_agent_file_shows_runs_as_bare_python():
    """Every code span that runs a script names the interpreter as `python` (the parser-recovery
    agent's `python -m pytest tests/parser_corpus -q` is the spec's own wording, restored) -- and,
    since live hardening L4 (a translator parked trying to run a validator its role could not), every
    script an agent file shows running is one its own role may run (`ROLE_SCRIPTS`; the policy's
    argument rules are checked on the same examples in `orchestrator/test/policy.test.ts`)."""
    allowed = _role_scripts()
    assert {"translator", "fixer", "validator", "intake"} <= set(allowed), allowed
    runs = 0
    for path in sorted(AGENTS_DIR.glob(f"*{SUFFIX}")):
        role = path.name[:-len(SUFFIX)]
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(\S+)\s+(scripts/[a-z_/]+\.py|-m pytest)", text):
            interpreter = match.group(1).strip("`")
            if interpreter.endswith(("python", "python.exe", "python3", "py")):
                runs += 1
                assert interpreter == "python", f"{path.name}: {match.group(0)}"
                script = match.group(2)
                if script != "-m pytest":
                    assert script in allowed.get(role, set()), (
                        f"{path.name} shows `{match.group(0)}`, and ROLE_SCRIPTS does not let {role} run {script}")
    assert runs >= 8, f"only {runs} script runs found -- the scan is not seeing them"
    for role in ("translator", "fixer"):
        for script in ("scripts/validate_segment.py", "scripts/validate_snowpark.py", "scripts/validate_dbt.py"):
            assert script in allowed[role], (role, script)
