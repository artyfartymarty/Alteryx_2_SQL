"""Contract C4's documented table-reference form in the local runner (Task C4V).

Snowflake documents `IDENTIFIER( { string_literal | session_variable | bind_variable |
snowflake_scripting_variable } )` -- one value, not an expression -- so every procedure builds each
mapped table's name first, `LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA ||
'.<LOGICAL>';`, and then reads it as `IDENTIFIER(:<LOGICAL>_SRC)`. Inside the LET the procedure's
arguments are named WITHOUT a colon -- Snowflake's Scripting documentation says the colon is for
binding a variable inside a SQL statement, not for an expression -- and the colon comes back in
`IDENTIFIER(:<LOGICAL>_SRC)`, which is inside a SQL statement (fix round 1). The DuckDB double has
to run exactly that: evaluate the LET from the call arguments, never send it to the backend,
substitute `:<VAR>` afterwards the same way it substitutes `:<PARAM>`, and refuse any other LET --
a colon-prefixed argument inside one included -- by name. Nothing here has run on a real Snowflake
account; the first real-account run confirms the form.
"""
import pytest

from lib.backend import DuckDBBackend
from lib.proc_runner import LetError, ProcError, bind, let_values, parse_proc, run_proc

HEADER = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0009_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;
"""
LETS = """  -- contract C4: each mapped table's name is built once, then read through IDENTIFIER(:<var>)
  LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS';
  LET ITEMS_OUT_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.ITEMS_OUT';
"""
STATEMENTS = """  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0009_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data
  t1_input AS (SELECT ID, NOTE FROM IDENTIFIER(:ITEMS_SRC)),
  -- tool 2: Filter, True branch
  t2_filter AS (SELECT * FROM t1_input WHERE NOTE <> ':ITEMS_SRC')
  SELECT ID, NOTE FROM t2_filter;
  INSERT INTO IDENTIFIER(:ITEMS_OUT_TGT) SELECT ID, NOTE FROM MIG_WORK.WF0009_SEG_01_OUT;
"""
FOOTER = """  RETURN 'OK';
END;
$$;"""
PROC = HEADER + LETS + STATEMENTS + FOOTER

ARGS = {"SRC_DB": "MIGDB", "SRC_SCHEMA": "MIG_GOLDEN_WF0009_NORMAL", "TGT_DB": "MIGDB",
        "TGT_SCHEMA": "MIG_WORK", "RUN_ID": "r1"}
F = [{"name": "ID", "type": "Int32", "size": 4, "scale": None},
     {"name": "NOTE", "type": "V_String", "size": 20, "scale": None}]


def _with_let(let_statement: str) -> str:
    return HEADER + LETS + f"  {let_statement};\n" + STATEMENTS + FOOTER


class _Recording:
    def __init__(self):
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)


# --- parse_proc: the P2 coupling (SnowflakeBackend.call_procedure reads the C4 order here) ------

def test_parse_proc_reads_name_parameters_and_statements_of_a_procedure_in_the_documented_form():
    proc = parse_proc(PROC)
    assert proc.name == "MIG_WORK.WF0009_SEG_01"
    assert proc.params == ["SRC_DB", "SRC_SCHEMA", "TGT_DB", "TGT_SCHEMA", "RUN_ID"]
    assert proc.execute_as == "CALLER" and proc.language == "SQL"
    assert proc.session == {"TIMEZONE": "America/New_York", "WEEK_START": "1"}
    # The LETs are not executable statements: they are evaluated, never sent to a backend.
    assert len(proc.statements) == 2
    assert not any(statement.lstrip().upper().startswith("LET") for statement in proc.statements)
    assert [let.name for let in proc.lets] == ["ITEMS_SRC", "ITEMS_OUT_TGT"]
    assert proc.lets[0].expression == "SRC_DB || '.' || SRC_SCHEMA || '.ITEMS'"
    # The comment in front of the first LET belongs to the body, not to the statement a message quotes.
    assert proc.lets[0].text == "LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS'"


# --- evaluation and substitution ----------------------------------------------------------------

def test_a_let_is_evaluated_from_the_bound_parameters():
    assert let_values(parse_proc(PROC), ARGS) == {
        "ITEMS_SRC": "MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS",
        "ITEMS_OUT_TGT": "MIGDB.MIG_WORK.ITEMS_OUT",
    }


def test_an_argument_inside_a_let_is_named_case_insensitively_like_any_unquoted_name():
    proc = PROC.replace("LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA",
                        "LET ITEMS_SRC VARCHAR := src_db || '.' || Src_Schema")
    assert let_values(parse_proc(proc), ARGS)["ITEMS_SRC"] == "MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS"


def test_identifier_of_a_let_variable_folds_to_the_table_name():
    values = {**ARGS, **let_values(parse_proc(PROC), ARGS)}
    out = bind("SELECT ID FROM IDENTIFIER(:ITEMS_SRC) WHERE NOTE <> ':ITEMS_SRC'", values)
    assert out == "SELECT ID FROM MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS WHERE NOTE <> ':ITEMS_SRC'"


def test_a_let_variable_outside_identifier_binds_as_a_string_like_a_parameter():
    values = {**ARGS, **let_values(parse_proc(PROC), ARGS)}
    assert bind("SELECT :items_src AS n, :RUN_ID AS r -- :ITEMS_SRC", values) == (
        "SELECT 'MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS' AS n, 'r1' AS r -- :ITEMS_SRC")


def test_run_proc_end_to_end_in_the_documented_form():
    backend = DuckDBBackend()
    backend.load_table("MIG_GOLDEN.WF0009_NORMAL_IN_1", {"fields": F, "rows": [[1, "keep"], [2, None]]})
    backend.create_view("MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS", "MIG_GOLDEN.WF0009_NORMAL_IN_1")
    backend.load_table("MIGDB.MIG_WORK.ITEMS_OUT", {"fields": F, "rows": []})
    run_proc(backend, PROC, ARGS)
    assert backend.query("SELECT ID FROM MIGDB.MIG_WORK.ITEMS_OUT")[1] == [(1,)]


def test_the_let_statement_is_never_sent_to_the_backend():
    backend = _Recording()
    run_proc(backend, PROC, ARGS)
    assert len(backend.executed) == 2
    assert not any("LET " in sql or "IDENTIFIER" in sql for sql in backend.executed)
    assert "FROM MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS" in backend.executed[0]
    assert backend.executed[1].startswith("INSERT INTO MIGDB.MIG_WORK.ITEMS_OUT ")


def test_a_let_variable_is_case_insensitive_like_a_parameter():
    proc = PROC.replace("IDENTIFIER(:ITEMS_SRC)", "IDENTIFIER(:items_src)")
    backend = _Recording()
    run_proc(backend, proc, ARGS)
    assert "FROM MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS" in backend.executed[0]


def test_a_let_after_the_alter_session_or_before_it_is_accepted():
    proc = HEADER.replace("  ALTER SESSION", LETS + "  ALTER SESSION") + STATEMENTS + FOOTER
    info = parse_proc(proc)
    assert [let.name for let in info.lets] == ["ITEMS_SRC", "ITEMS_OUT_TGT"]
    assert info.session["TIMEZONE"] == "America/New_York"


# --- refusals: any other LET is a ProcError that names it ------------------------------------------

@pytest.mark.parametrize("let_statement", [
    "LET X := 1",                                           # no type
    "LET X NUMBER := 1",                                    # not VARCHAR
    "LET X VARCHAR DEFAULT 'MIGDB.MIG_WORK.T'",             # DEFAULT, not :=
    "LET X VARCHAR := UPPER(SRC_DB)",                      # a function call
    "LET X VARCHAR := SRC_DB || NOTE_COLUMN",              # a name that is not an argument
    "LET X VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.' || (SELECT 'T')",  # a subquery
    "LET X VARCHAR := NOT_A_PARAMETER || '.T'",            # not a parameter of this procedure
    "LET X VARCHAR",                                        # no value at all
])
def test_a_let_outside_the_documented_shape_is_refused_by_name(let_statement):
    with pytest.raises(LetError, match="supported procedure subset") as caught:
        parse_proc(_with_let(let_statement))
    assert let_statement in str(caught.value)
    assert isinstance(caught.value, ProcError)


@pytest.mark.parametrize("let_statement", [
    "LET OTHER_SRC VARCHAR := :SRC_DB || '.' || :SRC_SCHEMA || '.OTHER'",
    "LET OTHER_SRC VARCHAR := SRC_DB || '.' || :SRC_SCHEMA || '.OTHER'",
])
def test_a_colon_prefixed_argument_inside_a_let_is_refused_with_a_clear_message(let_statement):
    """Fix round 1: Snowflake's documented expression syntax names a variable or argument without
    a colon; the colon binds a variable inside a SQL statement. A colon inside a LET is refused."""
    with pytest.raises(LetError, match=r"without a colon") as caught:
        parse_proc(_with_let(let_statement))
    assert let_statement in str(caught.value) and ":SRC_SCHEMA" in str(caught.value)


def test_a_let_that_shadows_a_parameter_is_refused():
    with pytest.raises(LetError, match="SRC_DB"):
        parse_proc(_with_let("LET SRC_DB VARCHAR := 'MIGDB'"))


def test_a_variable_declared_twice_is_refused():
    with pytest.raises(LetError, match="ITEMS_SRC.*twice"):
        parse_proc(_with_let("LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.OTHER'"))


# --- fix round 2: a comment inside a LET, and what parse_proc now records for compile_check ------

@pytest.mark.parametrize("let_statement", [
    "LET OTHER_SRC /* c */ VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.OTHER'",
    "LET OTHER_SRC VARCHAR := SRC_DB || '.' || /* c */ SRC_SCHEMA || '.OTHER'",
    "LET OTHER_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.OTHER' -- c\n",
])
def test_a_comment_inside_a_let_is_refused(let_statement):
    """A comment in FRONT of a LET is the body's; one inside it is refused, as the policy refuses it."""
    with pytest.raises(LetError, match="comment inside a LET"):
        parse_proc(_with_let(let_statement))


def test_parse_proc_records_every_return_and_how_many_statements_follow_it():
    """`RETURN` is not executed by the double, so it is recorded for `compile_check`'s
    `c4:return_form` instead of being dropped."""
    assert [(ret.text, ret.followed_by) for ret in parse_proc(PROC).returns] == [("RETURN 'OK'", 0)]
    early = PROC.replace("  RETURN 'OK';\n", "").replace(STATEMENTS, "  RETURN 'early';\n" + STATEMENTS)
    assert [(ret.text, ret.followed_by) for ret in parse_proc(early).returns] == [("RETURN 'early'", 2)]


def test_parse_proc_keeps_each_parameter_declaration():
    assert parse_proc(PROC).param_declarations == [
        "SRC_DB STRING", "SRC_SCHEMA STRING", "TGT_DB STRING", "TGT_SCHEMA STRING", "RUN_ID STRING"]
    header = PROC.replace("SRC_DB STRING,", "SRC_DB  STRING DEFAULT 'FINANCE',")
    assert parse_proc(header).param_declarations[0] == "SRC_DB STRING DEFAULT 'FINANCE'"


@pytest.mark.parametrize("statement", [
    "ITEMS_SRC := 'FINANCE.RAW.ITEMS'", "ITEMS_SRC /* c */ := 'FINANCE.RAW.ITEMS'",
    "\"ITEMS_SRC\" := 'FINANCE.RAW.ITEMS'", "SRC_DB := 'FINANCE'",
    "REPEAT ITEMS_SRC := 'X'", "LOOP NULL", "CASE WHEN TRUE THEN NULL", "ELSEIF (TRUE) THEN NULL",
    "ELSE NULL", "BREAK", "CONTINUE", "EXCEPTION WHEN OTHER THEN NULL", "OPEN c1", "FETCH c1 INTO X",
    "CLOSE c1", "RAISE my_error", "AWAIT ALL", "CANCEL x", "NULL",
])
def test_the_runner_refuses_every_scripting_form_the_policy_denies(statement):
    """Fix round 2: contract C4 bodies are flat, in the double as in the policy's SQL judge -- an
    assignment anywhere, and a statement opened by any Snowflake Scripting control keyword, is
    outside the subset (the double would otherwise send it to DuckDB or skip it)."""
    with pytest.raises(ProcError, match="supported procedure subset"):
        parse_proc(HEADER + LETS + f"  {statement};\n" + STATEMENTS + FOOTER)


def test_a_variable_used_before_its_let_is_refused():
    proc = HEADER + STATEMENTS + LETS + FOOTER
    with pytest.raises(LetError, match=r":ITEMS_SRC.*before"):
        parse_proc(proc)


def test_run_proc_refuses_a_bad_let_before_anything_reaches_the_backend():
    backend = _Recording()
    with pytest.raises(LetError):
        run_proc(backend, _with_let("LET X VARCHAR := UPPER(SRC_DB)"), ARGS)
    assert backend.executed == []


def test_an_undeclared_variable_inside_identifier_is_still_refused_by_bind():
    with pytest.raises(ProcError, match="IDENTIFIER"):
        bind("SELECT * FROM IDENTIFIER(:NEVER_DECLARED)", ARGS)
