"""Task 12a: the Snowflake operations DDL under snowflake/ (plan Task 12, phase 04 brief).

Files reproduce program spec 00-README.md §10.2-10.4 (ops tables, shadow table, reconciliation
task, alert) and §11.1 (the three governance roles). Nothing here has run on a real Snowflake
account (every file says so in its header); these tests only check the SQL is well-formed and that
the sandbox/production boundary the spec describes is respected in the text.
"""
import re
from pathlib import Path

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parents[1]
SNOWFLAKE_DIR = ROOT / "snowflake"

EXPECTED_FILES = [
    "01_ops_tables.sql",
    "02_shadow_table_template.sql",
    "03_reconciliation_task_template.sql",
    "04_alerts.sql",
    "05_roles.sql",
]
TEMPLATE_FILES = {"02_shadow_table_template.sql", "03_reconciliation_task_template.sql"}

# Schemas/databases this program's DDL is allowed to name. MIG_WORK/MIG_GOLDEN are the sandbox
# (contract C3); OPS holds run/reconciliation bookkeeping and ANALYTICS.CURATED holds the curated
# production outputs the shadow tables sit beside (program spec §10.2) -- the brief calls these two
# out by name as the allowed "production-side" mentions. Fix round 1 removed this program's one
# account-wide grant (IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE), so SNOWFLAKE/INFORMATION_SCHEMA
# are deliberately NOT allowed here any more -- both now appear only inside 05_roles.sql's comments.
ALLOWED_SCHEMAS = {"MIG_WORK", "MIG_GOLDEN", "OPS", "ANALYTICS", "CURATED"}

# Keywords that introduce a schema/database-qualified object reference, e.g. "FROM OPS.RUN_LOG",
# "SCHEMA MIG_WORK". TABLE/VIEW/PROCEDURE/TASK/ALERT only introduce an object name when the
# *statement itself* opens with CREATE ("CREATE TABLE x"); bare, in a GRANT, they are privilege
# nouns ("GRANT CREATE TABLE ON SCHEMA x"), where the very next word is "ON", not an object name.
# Deciding this per statement (rather than by a `CREATE ... KEYWORD` substring match anywhere in
# the file) is what avoids that ambiguity for "GRANT ... CREATE VIEW ON SCHEMA x": the GRANT
# statement never starts with CREATE, so those keywords are simply not offered to it.
_BASE_REF_KEYWORDS = ("FROM", "JOIN", "INTO", "SCHEMA", "DATABASE", "LIKE")
_CREATE_ONLY_REF_KEYWORDS = ("TABLE", "TASK", "ALERT", "VIEW", "PROCEDURE")


def _schema_refs(stmt: str) -> list[str]:
    keywords = _BASE_REF_KEYWORDS
    if re.match(r"(?i)^CREATE\b", stmt):
        keywords += _CREATE_ONLY_REF_KEYWORDS
    pattern = re.compile(
        # the reference may start with a placeholder itself (e.g. "DATABASE <DB>"), not only carry
        # one as a later dotted segment (e.g. "ANALYTICS.CURATED.<TARGET>") -- allow '<' to open it.
        r"\b(?:" + "|".join(keywords) + r")\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?([A-Za-z_<][A-Za-z0-9_.<>\"]*)"
    )
    return pattern.findall(stmt)


def _strip_line_comments(text: str) -> str:
    """Blanks out `-- ...` line comments (outside single-quoted strings).

    A naive `text.split(';')` is fooled by a semicolon inside a comment (several header comments
    below use "... Snowflake; review and adapt ..."), splitting one statement into two. Comments
    are stripped first so the later `;`-split only ever cuts real statement boundaries.
    """
    out = []
    for line in text.splitlines():
        in_string = False
        cut = len(line)
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "'":
                in_string = not in_string
            elif not in_string and line[i:i + 2] == "--":
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def _statements(text: str) -> list[str]:
    """Raw, comment-stripped `;`-terminated statements, in source order."""
    return [s.strip() for s in _strip_line_comments(text).split(";") if s.strip()]


def _create_table_statements(text: str) -> list[str]:
    return [s for s in _statements(text) if re.match(r"(?is)^CREATE\s+(OR\s+REPLACE\s+)?TABLE\b", s)]


def _substitute_placeholders(stmt: str) -> str:
    """Fills the two template placeholders with dummy valid identifiers so a template's SQL can be
    parsed like any other statement; the committed files keep the human-readable `<WF_ID>`/
    `<TARGET>` form (plan Task 12 brief), which is not itself valid SQL syntax.
    """
    return stmt.replace("<WF_ID>", "WF_ID_PLACEHOLDER").replace("<TARGET>", "TARGET_PLACEHOLDER")


# --- file inventory -----------------------------------------------------------------------

def test_expected_files_exist_and_nothing_else():
    found = {p.name for p in SNOWFLAKE_DIR.glob("*.sql")}
    assert found == set(EXPECTED_FILES)


def test_every_file_is_non_empty():
    for name in EXPECTED_FILES:
        text = (SNOWFLAKE_DIR / name).read_text(encoding="utf-8")
        assert text.strip(), name


def test_every_file_has_a_not_executed_review_header():
    for name in EXPECTED_FILES:
        text = (SNOWFLAKE_DIR / name).read_text(encoding="utf-8")
        assert "NOT executed against any Snowflake account" in text, name
        assert "review" in text.lower(), name


# --- CREATE TABLE statements parse with sqlglot's Snowflake dialect ------------------------

def test_create_table_statements_parse_with_sqlglot_snowflake_dialect():
    seen = 0
    for name in EXPECTED_FILES:
        text = (SNOWFLAKE_DIR / name).read_text(encoding="utf-8")
        for stmt in _create_table_statements(text):
            seen += 1
            tree = sqlglot.parse_one(_substitute_placeholders(stmt), read="snowflake")
            assert tree is not None, f"{name}: empty parse for {stmt!r}"
            assert isinstance(tree, exp.Create), f"{name}: not a CREATE statement: {stmt!r}"
            assert not list(tree.find_all(exp.Command)), (
                f"{name}: sqlglot did not understand this statement and fell back to a raw "
                f"Command: {stmt!r}"
            )
    assert seen == 3, "expected 2 tables in 01_ops_tables.sql plus 1 in the shadow-table template"


def test_ops_tables_declares_run_log_and_recon_results():
    text = (SNOWFLAKE_DIR / "01_ops_tables.sql").read_text(encoding="utf-8")
    tables = {}
    for stmt in _create_table_statements(text):
        tree = sqlglot.parse_one(stmt, read="snowflake")
        table_name = tree.find(exp.Table).name.upper()
        tables[table_name] = {c.name.upper() for c in tree.find_all(exp.ColumnDef)}
    assert set(tables) == {"RUN_LOG", "RECON_RESULTS"}
    assert {"RUN_ID", "WF_ID", "STATUS", "CREDITS", "WAREHOUSE"} <= tables["RUN_LOG"]
    assert {"RUN_ID", "WF_ID", "TARGET_TABLE", "VERDICT", "ROW_DELTA"} <= tables["RECON_RESULTS"]


# --- tasks / alerts / grants are checked as text only, never parsed with sqlglot -----------

def test_tasks_alerts_and_grants_are_not_parsed_by_this_suite():
    """03/04/05 contain CREATE TASK, CREATE ALERT and GRANT statements, which the brief says may
    not parse in sqlglot; confirm none of them are CREATE TABLE statements this suite would try to
    parse, and check their shape by substring instead."""
    for name in ("03_reconciliation_task_template.sql", "04_alerts.sql", "05_roles.sql"):
        text = (SNOWFLAKE_DIR / name).read_text(encoding="utf-8")
        assert _create_table_statements(text) == [], name

    task_text = (SNOWFLAKE_DIR / "03_reconciliation_task_template.sql").read_text(encoding="utf-8")
    assert "CREATE OR REPLACE TASK" in task_text
    assert "INSERT INTO OPS.RECON_RESULTS" in task_text

    alert_text = (SNOWFLAKE_DIR / "04_alerts.sql").read_text(encoding="utf-8")
    assert "CREATE OR REPLACE ALERT" in alert_text
    assert "RECON_RESULTS" in alert_text and "'FAIL'" in alert_text

    roles_text = (SNOWFLAKE_DIR / "05_roles.sql").read_text(encoding="utf-8")
    assert roles_text.count("GRANT ") > 0


# --- placeholders only in the two templates -------------------------------------------------

def test_placeholders_only_in_the_two_template_files():
    for name in EXPECTED_FILES:
        text = (SNOWFLAKE_DIR / name).read_text(encoding="utf-8")
        has_placeholder = "<WF_ID>" in text or "<TARGET>" in text
        assert has_placeholder == (name in TEMPLATE_FILES), name


def test_templates_do_not_hardcode_the_spec_example_values():
    """The program spec's own worked example uses wf_0042 / GL_SUMMARY; if either leaked into a
    template literally, the file would not actually be reusable per-workflow."""
    for name in TEMPLATE_FILES:
        text = (SNOWFLAKE_DIR / name).read_text(encoding="utf-8")
        assert "wf_0042" not in text
        assert "GL_SUMMARY" not in text


# --- no production schema other than OPS/ANALYTICS -------------------------------------------

def test_no_production_schema_mentioned_other_than_ops_and_analytics():
    for name in EXPECTED_FILES:
        text = (SNOWFLAKE_DIR / name).read_text(encoding="utf-8")
        for stmt in _statements(text):
            for ref in _schema_refs(stmt):
                parts = ref.split(".")
                # the last segment of a multi-part reference is the object (table/task/alert)
                # name, not a schema; a single bare part (e.g. "SCHEMA MIG_WORK") IS the schema.
                schema_parts = parts if len(parts) == 1 else parts[:-1]
                for part in schema_parts:
                    token = part.strip('"')
                    if token.startswith("<") and token.endswith(">"):
                        continue  # <WF_ID> / <TARGET> placeholder, filled in per workflow
                    assert token.upper() in ALLOWED_SCHEMAS, (
                        f"{name}: disallowed schema {token!r} in {ref!r} (statement: {stmt[:60]!r})"
                    )


def test_scanner_does_not_also_reject_the_allowed_sandbox_schemas():
    """Sanity check for the scanner above: it must not be so strict that it also rejects the
    sandbox schemas the roles file is specifically granting access to."""
    roles_text = (SNOWFLAKE_DIR / "05_roles.sql").read_text(encoding="utf-8")
    assert "MIG_WORK" in roles_text
    assert "MIG_GOLDEN" in roles_text


# --- roles: the three roles and their grant boundaries (program spec §11.1) -----------------

def test_three_roles_are_declared():
    text = (SNOWFLAKE_DIR / "05_roles.sql").read_text(encoding="utf-8")
    for role in ("MIGRATION_AGENT", "MIGRATION_CI", "MIGRATION_RUN"):
        assert re.search(rf"CREATE ROLE (IF NOT EXISTS )?{role}\b", text), role


def test_migration_agent_gets_usage_on_sandbox_and_nothing_named_production():
    text = (SNOWFLAKE_DIR / "05_roles.sql").read_text(encoding="utf-8")
    # every statement that actually grants to (or declares) MIGRATION_AGENT names it on its own
    # line, so filtering by line -- rather than slicing the file at the first mention of another
    # role's name, which the up-front `CREATE ROLE` block would cut far too early -- isolates them.
    agent_lines = "\n".join(line for line in text.splitlines() if "MIGRATION_AGENT" in line)
    assert "GRANT USAGE ON SCHEMA MIG_WORK TO ROLE MIGRATION_AGENT" in agent_lines
    assert "GRANT USAGE ON SCHEMA MIG_GOLDEN TO ROLE MIGRATION_AGENT" in agent_lines
    assert "INFORMATION_SCHEMA" in agent_lines or "IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE" in agent_lines
    assert "ANALYTICS" not in agent_lines  # nothing on the production/curated schema
    assert "MIGRATION_CI" not in agent_lines
    assert "MIGRATION_RUN" not in agent_lines


def test_migration_ci_deploys_and_migration_run_executes_in_production():
    text = (SNOWFLAKE_DIR / "05_roles.sql").read_text(encoding="utf-8")
    assert "MIGRATION_CI" in text and "deploys" in text.lower()
    assert "MIGRATION_RUN" in text and "production" in text.lower()
    assert "GRANT USAGE ON SCHEMA ANALYTICS TO ROLE MIGRATION_RUN" in text


# --- fix round 1, IMPORTANT 2: MIGRATION_AGENT must not over-grant relative to least privilege ---

def test_imported_privileges_and_account_usage_do_not_appear_uncommented():
    """An earlier draft granted MIGRATION_AGENT `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE` for
    intake's INFORMATION_SCHEMA lookups; that also exposes all of SNOWFLAKE.ACCOUNT_USAGE
    account-wide (every role's query text, login history, object metadata), which is not least
    privilege (program spec §11.1). The fix documents a sandbox-catalog-table alternative instead,
    as a commented block -- so these phrases may still appear in a comment, just never in a
    statement that would actually execute."""
    for name in EXPECTED_FILES:
        text = (SNOWFLAKE_DIR / name).read_text(encoding="utf-8")
        for stmt in _statements(text):
            upper = stmt.upper()
            assert "IMPORTED PRIVILEGES" not in upper, f"{name}: {stmt!r}"
            assert "ACCOUNT_USAGE" not in upper, f"{name}: {stmt!r}"


def test_migration_agent_grants_name_only_sandbox_databases_and_schemas():
    """Every uncommented GRANT ... TO ROLE MIGRATION_AGENT must only ever name the sandbox
    database/schemas (or the <DB> placeholder standing in for that one sandbox database) -- never
    a production database, whether by a real name or a generic stand-in like <SOURCE_DB> (that
    placeholder is reserved for the commented catalog-table template, which documents a job that
    does NOT run as MIGRATION_AGENT)."""
    text = (SNOWFLAKE_DIR / "05_roles.sql").read_text(encoding="utf-8")
    seen_any_grant = False
    for stmt in _statements(text):
        if not stmt.upper().startswith("GRANT") or "MIGRATION_AGENT" not in stmt:
            continue
        seen_any_grant = True
        for ref in _schema_refs(stmt):
            for part in ref.split("."):
                token = part.strip('"')
                if token == "<DB>":
                    continue  # the one sandbox database, filled in consistently everywhere
                assert token.upper() in ALLOWED_SCHEMAS, f"{stmt!r}: disallowed reference {token!r}"
        assert "<SOURCE_DB>" not in stmt
    assert seen_any_grant
