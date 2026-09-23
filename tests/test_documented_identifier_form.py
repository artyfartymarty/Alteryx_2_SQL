"""No tracked file teaches an expression inside `IDENTIFIER(…)` (Task C4V).

Snowflake's documentation (docs.snowflake.com/en/sql-reference/identifier-literal) gives the
grammar `IDENTIFIER( { string_literal | session_variable | bind_variable |
snowflake_scripting_variable } )` -- a single value, not an expression. Every SQL procedure this
pipeline writes therefore builds each mapped table's name first,
`LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>';`, and references it
as `IDENTIFIER(:<LOGICAL>_SRC)` (contract C4). Inside the LET the procedure's arguments are named
without a colon (Snowflake's documented expression syntax -- the colon binds a variable inside a SQL
statement, which is where `IDENTIFIER(:<LOGICAL>_SRC)` sits; fix round 1). That is the DOCUMENTED
form; nothing here has run on Snowflake, and the first real-account run (the hand-off's verification
ladder) confirms it.

`compile_check.py` refuses the expression form in a procedure (`c4:identifier_expression`) and a colon
inside a LET (`c4:let_form`), and the policy's SQL judge denies both; this scan keeps every OTHER
tracked text -- the cookbook, the agent instructions, the docs, the test fixtures, the orchestrator's
fakes -- from teaching either again.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCANNED_SUFFIXES = frozenset({".sql", ".md", ".py", ".ts"})

#: The text from `IDENTIFIER(` up to the first `)` -- or the first `"` or backtick, which close the
#: string or the Markdown span the SQL sits in. It is an expression when it joins with `||` or opens
#: with a function call (`CONCAT(…`, `UPPER(…`). A heuristic over prose and code alike, not a SQL
#: parser: `compile_check.py` is the gate for procedures; this is the guard for everything else.
_IDENTIFIER_ARGUMENT_RE = re.compile(r"\bIDENTIFIER\s*\((?P<argument>[^)\"`]*)", re.IGNORECASE)
_FUNCTION_CALL_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*\($")

#: The only files that may still show the expression form, each exactly this many times: the tests
#: that prove it is refused, this scan's own self-test, and the design spec's sentence recording the
#: deviation from the form the 2026-09-18 plan first specified. A new occurrence anywhere -- in one
#: of these files too -- fails the scan.
ALLOWED = {
    "tests/test_compile_check_c4v.py": 2,
    "tests/test_documented_identifier_form.py": 3,
    "orchestrator/test/policy.test.ts": 2,
    "docs/superpowers/specs/2026-09-18-alteryx-snowflake-pipeline-design.md": 1,
}

#: A LET's right-hand side, from `:=` to the first `;`, `"` or backtick (the end of the SQL statement,
#: of the string or of the Markdown span it sits in), and a colon-prefixed name inside it.
_LET_RIGHT_HAND_SIDE_RE = re.compile(r"\bLET\s+\S+\s+VARCHAR\s*:=(?P<rhs>[^;\"`]*)", re.IGNORECASE)
_COLON_NAME_RE = re.compile(r":\s*[A-Za-z_<]")

#: Fix round 1: the only files that may show a colon inside a LET's right-hand side, each exactly this
#: many times -- the tests that prove the refusal, and this scan's own self-test.
ALLOWED_COLON_LETS = {
    "tests/test_proc_runner_let.py": 2,
    "tests/test_compile_check_c4v.py": 2,
    "tests/test_documented_identifier_form.py": 3,
    "orchestrator/test/policy.test.ts": 2,
}

#: Not scanned, each for a stated reason: `docs/superpowers/build-reports/**`,
#: `docs/superpowers/rulings/**` and the executed Task 6 plan are dated records of what was built and
#: ruled at the time, quoting the form that was in use then. Rewriting them would falsify the record;
#: the design spec and the plan's contract C4 carry the amendment instead. The committed `workflows/**`
#: trees ARE scanned (phase-2 Task G refreshed them from the canned artefacts): every procedure a
#: reader opens there shows the documented form.
EXCLUDED_PREFIXES = (
    "docs/superpowers/build-reports/",
    "docs/superpowers/rulings/",
    "docs/superpowers/plans/2026-09-18-pipeline/02-sql-runtime-compare.md",
)


def expression_arguments(text: str) -> list[str]:
    """Every `IDENTIFIER(…)` argument in `text` that is an expression rather than one value."""
    found = []
    for match in _IDENTIFIER_ARGUMENT_RE.finditer(text):
        argument = match.group("argument")
        head = argument[:argument.find("(") + 1] if "(" in argument else ""
        if "||" in argument or (head and _FUNCTION_CALL_RE.match(head)):
            found.append(argument.strip())
    return found


def colon_lets(text: str) -> list[str]:
    """Every `LET … VARCHAR := …` in `text` whose right-hand side names something with a colon."""
    return [" ".join(match.group(0).split()) for match in _LET_RIGHT_HAND_SIDE_RE.finditer(text)
            if _COLON_NAME_RE.search(match.group("rhs"))]


def _tracked() -> list[str]:
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    files = [line for line in result.stdout.splitlines() if line]
    assert len(files) > 500, f"git ls-files returned only {len(files)} files"
    return files


def _scanned() -> list[str]:
    return [rel for rel in _tracked()
            if Path(rel).suffix in SCANNED_SUFFIXES and not rel.startswith(EXCLUDED_PREFIXES)]


def test_the_scan_recognises_an_expression_and_accepts_one_value():
    # The three expression samples below are this file's own entries in ALLOWED.
    assert expression_arguments("FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS')") == [
        ":SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS'"]
    assert expression_arguments("INTO IDENTIFIER(\n  :TGT_DB || '.T')") == [":TGT_DB || '.T'"]
    assert expression_arguments("FROM IDENTIFIER(CONCAT(:SRC_DB, '.T'))") == ["CONCAT(:SRC_DB, '.T'"]
    for one_value in ("FROM IDENTIFIER(:ORDERS_SRC)", "IDENTIFIER('MIGDB.MIG_WORK.T')",
                      "IDENTIFIER($TABLE_NAME)", "IDENTIFIER(:<LOGICAL>_SRC)", "IDENTIFIER(…)",
                      "IDENTIFIER(...)", "`IDENTIFIER(`, `EXECUTE IMMEDIATE` and `TABLE(` are denied",
                      'assert "IDENTIFIER(:SRC_DB" not in body and "a || b" in body'):
        assert expression_arguments(one_value) == [], one_value


def test_no_tracked_file_shows_an_expression_inside_identifier():
    offenders = []
    counts = {}
    for rel in _scanned():
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        found = expression_arguments(text)
        if not found:
            continue
        counts[rel] = len(found)
        if rel not in ALLOWED:
            offenders += [f"{rel}: IDENTIFIER({argument})" for argument in found]
    assert not offenders, ("IDENTIFIER( takes one value in Snowflake's documented grammar; build the "
                           "name with LET <LOGICAL>_SRC VARCHAR := … first:\n" + "\n".join(offenders))
    assert counts == {rel: count for rel, count in ALLOWED.items()}, (
        "the allowed files must show the expression form exactly as often as ALLOWED says")


def test_the_scan_recognises_a_colon_inside_a_let():
    # The three colon LET texts below (two samples, one expected result) are this file's own
    # entries in ALLOWED_COLON_LETS.
    assert colon_lets("LET ORDERS_SRC VARCHAR := :SRC_DB || '.ORDERS';") == [
        "LET ORDERS_SRC VARCHAR := :SRC_DB || '.ORDERS'"]
    assert len(colon_lets("LET A_TGT VARCHAR := TGT_DB || '.' ||\n  :TGT_SCHEMA || '.A'")) == 1
    for documented in ("LET ORDERS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ORDERS';",
                       "`LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>'`, then "
                       "`IDENTIFIER(:<LOGICAL>_SRC)`", "SELECT x FROM t WHERE y = :RUN_ID"):
        assert colon_lets(documented) == [], documented


def test_no_tracked_file_shows_a_colon_inside_a_let():
    counts = {}
    for rel in _scanned():
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        found = colon_lets(text)
        if found:
            counts[rel] = found
    offenders = [f"{rel}: {let}" for rel, found in counts.items() if rel not in ALLOWED_COLON_LETS
                 for let in found]
    assert not offenders, ("a LET names the procedure's arguments without a colon (Snowflake's documented "
                           "expression syntax):\n" + "\n".join(offenders))
    assert {rel: len(found) for rel, found in counts.items()} == ALLOWED_COLON_LETS, (
        "the allowed files must show a colon inside a LET exactly as often as ALLOWED_COLON_LETS says")


#: Fix round 2 (M2): prose that names one of the four C4 arguments as a code span WITH a colon --
#: "the LETs build it from `:TGT_DB` and `:TGT_SCHEMA`" -- teaches the colon a LET must not have.
_PROSE_COLON_ARGUMENT_RE = re.compile(r"`:(?:SRC|TGT)_(?:DB|SCHEMA)`")


def test_no_markdown_names_a_c4_argument_with_a_colon():
    offenders = []
    for rel in _scanned():
        if not rel.endswith(".md"):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        offenders += [f"{rel}: {match.group(0)}" for match in _PROSE_COLON_ARGUMENT_RE.finditer(text)]
    assert not offenders, ("inside a LET the arguments are SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA -- "
                           "no colon:\n" + "\n".join(offenders))


def test_every_exclusion_still_names_something_tracked():
    tracked = _tracked()
    for prefix in EXCLUDED_PREFIXES:
        assert any(rel.startswith(prefix) for rel in tracked), prefix
