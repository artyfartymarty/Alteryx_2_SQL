from __future__ import annotations

import pytest

from lib import snowpark_rules as rules

CONTRACT = {"segment": "seg_02", "inputs": [{"from": "seg_01", "stream": "2_T", "table": "MIG_WORK.WF0006_SEG_01_OUT", "columns": []}],
            "outputs": [{"stream": "3_1", "table": "MIG_WORK.WF0006_SEG_02_OUT", "kind": "work", "logical": None, "columns": [], "keys": []}]}
GOOD = '''# tool 3: Python tool -- revenue schedule (pandas: sequential per customer)
import pandas as pd
from snowflake.snowpark.types import StructType, StructField, StringType, DoubleType


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()
    out = session.create_dataframe(pdf, schema=StructType([StructField("CUSTOMER", StringType(20))]))
    out.write.mode("overwrite").save_as_table("MIG_WORK.WF0006_SEG_02_OUT")
    return "OK"
'''

# A second canned-style procedure: create_dataframe from literal rows (not a table read) and
# .write.mode("append") instead of "overwrite" -- both still legitimate C4 shapes.
GOOD2 = '''# tool 5: Python tool -- static onboarding rows
from snowflake.snowpark.types import StructType, StructField, StringType, LongType


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    rows = [("A", 1), ("B", 2)]
    schema = StructType([StructField("CODE", StringType(5)), StructField("N", LongType())])
    out = session.create_dataframe(rows, schema=schema)
    out.write.mode("append").save_as_table("MIG_WORK.WF0006_SEG_02_OUT")
    return "OK"
'''


def test_the_good_procedure_passes():
    assert rules.check_proc_py(GOOD, "wf_0006", "seg_02", CONTRACT) == []


def test_a_procedure_using_create_dataframe_from_rows_and_append_also_passes():
    assert rules.check_proc_py(GOOD2, "wf_0006", "seg_02", CONTRACT) == []


@pytest.mark.parametrize("mutation,rule", [
    (("import pandas as pd", "import pandas as pd\nimport os"), "rule:imports"),
    # `session.sql(...)` is no longer its own "no_session_sql" rule (Task 3 fix round 1, coordinator
    # ruling): it is one more way `session` appears somewhere other than `.table(...)`/
    # `.create_dataframe(...)` in `run`, so the structural rule:session_scope catches it now.
    (("session.table(", "session.sql('select 1'); session.table("), "rule:session_scope"),
    (("return \"OK\"", "open('x'); return \"OK\""), "rule:no_io"),
    (("def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id)", "def run(session, a, b)"), "rule:signature"),
    (("# tool 3:", "# note:"), "rule:tool_comments"),
    (("MIG_WORK.WF0006_SEG_01_OUT", "SALES.RAW.ORDERS"), "rule:table_names"),
    (('save_as_table("MIG_WORK.WF0006_SEG_02_OUT")', 'save_as_table("MIG_WORK.WF0006_SEG_09_OUT")'), "rule:table_names"),
    (("def run(", "def helper(session):\n    return session\n\n\ndef run("), "rule:session_scope"),
])
def test_each_rule_names_its_violation(mutation, rule):
    old, new = mutation
    assert old in GOOD
    errors = rules.check_proc_py(GOOD.replace(old, new), "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith(rule) for e in errors), errors


def test_a_syntax_error_is_one_violation():
    errors = rules.check_proc_py("def run(:\n", "wf_0006", "seg_02", CONTRACT)
    assert len(errors) == 1 and errors[0].startswith("rule:syntax")


# --- Task 3 fix round 1: the reviewer's evasion probes (coordinator ruling, binding) ------------
# Each reproduces one of the task-3 review's probes exactly; between them they found the
# rules as originally written evadable by ordinary Python. The rules are now conservative and
# structural: `session` may appear ONLY as the receiver of `.table(...)`/`.create_dataframe(...)`
# written directly in the one top-level `run`; a fixed list of raw-SQL escape-hatch names is
# refused everywhere; `.table`/`.save_as_table` take exactly one positional literal/f-string
# argument and no keywords; tool comments are found through real COMMENT tokens, not source text.

def test_session_aliased_then_used_for_sql_is_a_violation():
    """probe_rules.py #1 (alias_then_sql): `s = session; s.sql(...)` dodges an attribute-only check."""
    bad = GOOD.replace(
        'pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
        's = session\n    s.sql("DROP TABLE MIG_WORK.WF0006_SEG_01_OUT")\n'
        '    pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
    )
    errors = rules.check_proc_py(bad, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:session_scope") for e in errors), errors


def test_getattr_session_sql_is_a_violation():
    """probe_rules.py #2 (getattr_session_sql): `getattr(session, "sql")(...)` dodges an
    Attribute-node-shaped check; also now caught directly since `getattr` joined FORBIDDEN_NAMES."""
    bad = GOOD.replace(
        'pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
        'getattr(session, "sql")("DROP TABLE MIG_WORK.WF0006_SEG_01_OUT")\n'
        '    pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
    )
    errors = rules.check_proc_py(bad, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:session_scope") for e in errors), errors
    assert any(e.startswith("rule:no_io") and "getattr" in e for e in errors), errors


def test_save_as_table_with_a_keyword_argument_is_a_violation():
    """probe_rules2.py (save_as_table_kwarg): `table_name=` dodges the old `node.args and ...` check."""
    bad = GOOD.replace(
        'out.write.mode("overwrite").save_as_table("MIG_WORK.WF0006_SEG_02_OUT")',
        'out.write.mode("overwrite").save_as_table(table_name="SALES.RAW.SECRET")',
    )
    errors = rules.check_proc_py(bad, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:table_names") for e in errors), errors


def test_session_table_with_a_keyword_argument_is_a_violation():
    """probe_rules2.py (session_table_kwarg): `name=` dodges the same way."""
    bad = GOOD.replace(
        'pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
        'pdf = session.table(name="SALES.RAW.SECRET").to_pandas()',
    )
    errors = rules.check_proc_py(bad, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:table_names") for e in errors), errors


def test_functions_sql_expr_attribute_is_a_violation():
    """probe_rules.py #3 (functions_sql_expr): raw SQL smuggled through the DataFrame API via
    `F.sql_expr(...)` -- an Attribute access, not a bare Name, so FORBIDDEN_NAMES never saw it."""
    bad = GOOD.replace(
        "import pandas as pd", "import pandas as pd\nfrom snowflake.snowpark import functions as F",
    ).replace(
        'pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
        'pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").select(F.sql_expr("(SELECT 1)")).to_pandas()',
    )
    errors = rules.check_proc_py(bad, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:no_raw_sql") for e in errors), errors


def test_from_import_of_call_function_is_a_violation():
    """A `from ... import call_function` alone (never even called) must be refused at the import."""
    bad = GOOD.replace(
        "import pandas as pd",
        "import pandas as pd\nfrom snowflake.snowpark.functions import call_function",
    )
    errors = rules.check_proc_py(bad, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:no_raw_sql") for e in errors), errors


def test_a_run_nested_inside_a_class_is_a_violation():
    """probe_rules.py #5 (nested_run_class_alias_call): a second `def run` living inside a class,
    taking and aliasing `session` -- invisible to a `tree.body`-only scan for `run`."""
    bad = GOOD + '''

class _Helper:
    def run(session, x):
        s = session
        s.call("SYSTEM$WAIT", 1)
'''
    errors = rules.check_proc_py(bad, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:session_scope") for e in errors), errors


def test_a_fabricated_tool_comment_inside_a_string_literal_does_not_count():
    """probe_rules.py #4 (fake_tool_comment_in_string): the real `# tool 3:` comment is removed and
    the same text is planted inside a triple-quoted string instead -- a regex over raw source text
    cannot tell the difference; tokenize's COMMENT tokens can."""
    bad = GOOD.replace(
        "# tool 3: Python tool -- revenue schedule (pandas: sequential per customer)\n", "",
    ).replace(
        "def run(session,",
        '_DOC = """\n# tool 3: fake comment inside a string, not a real code comment\n"""\n\n\ndef run(session,',
    )
    errors = rules.check_proc_py(bad, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:tool_comments") for e in errors), errors


# --- Task 3 fix round 2: the scoped re-review's two residual findings (coordinator ruling) -------
# Residual 1: dunder access (`__dict__`, `__getattribute__`, `__class__`, `__base__`,
# `__subclasses__`, ...) reaches Python internals -- and, chained together, an object graph walk
# that can reach anything importable -- without ever being a plain Name/Attribute.attr/import alias
# match against FORBIDDEN_NAMES or RAW_SQL_NAMES. Refused under the EXISTING rule:no_io (the spec's
# rule-name list is fixed) for any `ast.Attribute` whose `attr` matches `^__.*__$` and any
# `ast.Name` whose `id` matches `^__.*__$`, except `__import__` (already forbidden by name, keeps
# its own message).

GOOD3 = '''# tool 7: Python tool -- per-customer running total (pandas groupby)
import pandas as pd
from snowflake.snowpark.types import StructType, StructField, StringType, DoubleType


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()
    rows = []
    for customer, group in pdf.groupby("CUSTOMER"):
        rows.append((customer, float(group["AMOUNT"].sum())))
    schema = StructType([StructField("CUSTOMER", StringType(20)), StructField("TOTAL", DoubleType())])
    out = session.create_dataframe(rows, schema=schema)
    out.write.mode("overwrite").save_as_table("MIG_WORK.WF0006_SEG_02_OUT")
    return "OK"
'''


def test_a_procedure_with_a_groupby_loop_and_create_dataframe_with_schema_passes():
    """Positive control for fix round 2: session.table(...).to_pandas(), a pandas groupby loop,
    StructType([...]), session.create_dataframe(rows, schema=...) and .write.mode("overwrite")
    .save_as_table(...) -- none of it dunder access -- must still have zero violations."""
    assert rules.check_proc_py(GOOD3, "wf_0006", "seg_02", CONTRACT) == []


def test_dict_dunder_via_the_functions_module_is_a_violation():
    """F.__dict__["sql_expr"] reaches the raw-SQL helper without ever writing the name `sql_expr`
    as a Name, an Attribute.attr, or an import alias -- `__dict__` itself is the evasion."""
    bad = GOOD.replace(
        "import pandas as pd", "import pandas as pd\nimport snowflake.snowpark.functions as F",
    ).replace(
        'pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
        'F.__dict__["sql_expr"]\n    pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
    )
    errors = rules.check_proc_py(bad, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:no_io") and "__dict__" in e for e in errors), errors


def test_getattribute_dunder_via_the_functions_module_is_a_violation():
    """F.__getattribute__("sql_expr") is the same evasion through a different dunder."""
    bad = GOOD.replace(
        "import pandas as pd", "import pandas as pd\nimport snowflake.snowpark.functions as F",
    ).replace(
        'pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
        'F.__getattribute__("sql_expr")\n    pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
    )
    errors = rules.check_proc_py(bad, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:no_io") and "__getattribute__" in e for e in errors), errors


def test_session_dunder_class_is_a_violation():
    """session.__class__ is not `session.table(...)`/`session.create_dataframe(...)` -- it is
    already a rule:session_scope violation, but the dunder access itself is separately refused."""
    bad = GOOD.replace(
        'pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
        'session.__class__\n    pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
    )
    errors = rules.check_proc_py(bad, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:no_io") and "__class__" in e for e in errors), errors


def test_object_graph_walk_via_chained_dunders_is_a_violation():
    """().__class__.__base__.__subclasses__() -- the classic sandbox-escape chain -- must be
    caught at the first dunder attribute, not only if it is ever actually called."""
    bad = GOOD.replace(
        'pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
        '().__class__.__base__.__subclasses__()\n'
        '    pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()',
    )
    errors = rules.check_proc_py(bad, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:no_io") and "__class__" in e for e in errors), errors
    assert any(e.startswith("rule:no_io") and "__base__" in e for e in errors), errors
    assert any(e.startswith("rule:no_io") and "__subclasses__" in e for e in errors), errors


# --- final fix wave F2 (review I1 + I4): the write surface is closed --------------------------
# A C4 procedure has exactly ONE sink: `.write.mode(<literal>).save_as_table(<one positional
# literal>)`. The review verified against snowflake-snowpark-python 1.55.0 that `DataFrameWriter`
# also exposes `saveAsTable`, `insert_into`/`insertInto`, `copy_into_location`, `csv`, `json`,
# `parquet`, `orc` and `save`, and that a REBOUND bound method (`sink = w.save_as_table;
# sink(...)`) is an Attribute that is never a call's `func`. All three evaded the rules and all
# three were confirmed to really write the table in a live local session.

#: The same segment as CONTRACT, but with the mapped names its contract declares: a source
#: logical on the input and a target logical on the output. The `{src_db}`/`{tgt_db}` f-string
#: forms are legal ONLY for a name the contract declares (I4) -- shape alone is not enough.
CONTRACT_MAPPED = {
    "segment": "seg_02",
    "inputs": [{**CONTRACT["inputs"][0], "logical": "SUBSCRIPTIONS"}],
    "outputs": [CONTRACT["outputs"][0],
                {"stream": "4_Output", "kind": "target", "tool_id": "5", "table": None,
                 "logical": "REVENUE_BY_PERIOD", "write_mode": "overwrite", "columns": [], "keys": []}],
}


def _errors(source: str, contract: dict = CONTRACT) -> list[str]:
    return rules.check_proc_py(source, "wf_0006", "seg_02", contract)


def test_the_good_procedure_also_passes_against_a_contract_that_declares_logicals():
    """Positive control for I4's new check: adding declared logical names to the contract must not
    make the literal-table procedure stop passing."""
    assert _errors(GOOD, CONTRACT_MAPPED) == []


def test_save_as_table_camel_case_alias_is_checked_like_save_as_table():
    """Probe E2: `saveAsTable` is a REAL snowpark alias, not a typo, and it wrote the table."""
    bad = GOOD.replace('save_as_table("MIG_WORK.WF0006_SEG_02_OUT")',
                       'saveAsTable("ANALYTICS.CURATED.ANYTHING_AT_ALL")')
    errors = _errors(bad)
    assert any(e.startswith("rule:table_names") for e in errors), errors


def test_save_as_table_camel_case_alias_is_accepted_when_it_names_the_right_table():
    """The alias is *checked*, not banned outright: it is the same rule as `save_as_table`."""
    assert _errors(GOOD.replace("save_as_table(", "saveAsTable(")) == []


@pytest.mark.parametrize("sink", ["insert_into", "insertInto", "copy_into_location",
                                  "copyIntoLocation", "csv", "json", "parquet", "orc", "save"])
def test_every_other_dataframewriter_sink_is_refused(sink):
    """Not a table write at all (a stage, a file, an INSERT into something already there), so
    there is no name to check -- the method itself is refused wherever it appears."""
    bad = GOOD.replace('save_as_table("MIG_WORK.WF0006_SEG_02_OUT")',
                       sink + '("MIG_WORK.WF0006_SEG_02_OUT")')
    errors = _errors(bad)
    assert any(e.startswith("rule:no_io") and sink in e for e in errors), errors


@pytest.mark.parametrize("sink", ["insert_into", "csv", "save"])
def test_a_forbidden_sink_is_refused_as_a_bare_name_too(sink):
    """`from ... import insert_into`, or a local called `csv`, is the same evasion the raw-SQL
    names already have a bare-Name check for."""
    bad = GOOD.replace('    return "OK"', "    " + sink + '\n    return "OK"')
    errors = _errors(bad)
    assert any(e.startswith("rule:no_io") and sink in e for e in errors), errors


@pytest.mark.parametrize("method", ["save_as_table", "saveAsTable", "table", "create_dataframe"])
def test_a_write_method_that_is_referenced_instead_of_called_is_refused(method):
    """Probe E3: `sink = writer.save_as_table; sink("ANY.TABLE")`. The bound method is an
    `ast.Attribute` that is not the `func` of a `Call`, so no argument is ever checked. The same
    structural move ruling 51 already made for `session`."""
    bad = GOOD.replace('    return "OK"',
                       "    sink = out.write." + method + "\n"
                       '    sink("ANALYTICS.CURATED.ANYTHING")\n'
                       '    return "OK"')
    errors = _errors(bad)
    assert any(e.startswith("rule:table_names") and "not referenced" in e for e in errors), errors


def test_a_write_to_an_undeclared_logical_is_refused():
    """Probe B5: the canned procedure plus one extra
    `save_as_table(f"{tgt_db}.{tgt_schema}.SHADOW_COPY")` was rules-clean AND validated PASS on
    all four golden sets -- an undeclared second table nothing in the pipeline noticed."""
    bad = GOOD.replace('    return "OK"',
                       '    out.write.mode("overwrite").save_as_table(f"{tgt_db}.{tgt_schema}.SHADOW_COPY")\n'
                       '    return "OK"')
    for contract in (CONTRACT, CONTRACT_MAPPED):
        errors = _errors(bad, contract)
        assert any(e.startswith("rule:table_names") and "SHADOW_COPY" in e for e in errors), errors


def test_a_write_to_the_contracts_own_target_logical_passes():
    good = GOOD.replace('save_as_table("MIG_WORK.WF0006_SEG_02_OUT")',
                        'save_as_table(f"{tgt_db}.{tgt_schema}.REVENUE_BY_PERIOD")')
    assert _errors(good, CONTRACT_MAPPED) == []
    # ... and the very same write is refused by the contract that declares no target logical.
    assert any(e.startswith("rule:table_names") for e in _errors(good, CONTRACT))


def test_a_read_of_an_undeclared_logical_is_refused():
    bad = GOOD.replace('session.table("MIG_WORK.WF0006_SEG_01_OUT")',
                       'session.table(f"{src_db}.{src_schema}.SOMEBODY_ELSES_TABLE")')
    errors = _errors(bad, CONTRACT_MAPPED)
    assert any(e.startswith("rule:table_names") and "SOMEBODY_ELSES_TABLE" in e for e in errors), errors


def test_a_read_of_the_contracts_own_source_logical_passes():
    good = GOOD.replace('session.table("MIG_WORK.WF0006_SEG_01_OUT")',
                        'session.table(f"{src_db}.{src_schema}.SUBSCRIPTIONS")')
    assert _errors(good, CONTRACT_MAPPED) == []
    assert any(e.startswith("rule:table_names") for e in _errors(good, CONTRACT))


# --- final fix wave F3 (review I2): the session rules do not depend on the receiver's name -----
# `from snowflake.snowpark import Session; live = Session.builder.getOrCreate()` hands the module
# the very session `run()` was given, under a name no rule was watching. `Session` is therefore
# refused outright, and every session method is refused as an attribute of ANY receiver.

def test_the_name_session_class_is_refused_outright():
    """Probe E1, reproduced: the import is on the allow-list and `live` is not called `session`,
    so nothing fired. A C4 procedure is HANDED a session; it never builds or fetches one."""
    bad = GOOD.replace(
        "import pandas as pd", "import pandas as pd\nfrom snowflake.snowpark import Session",
    ).replace(
        '    return "OK"',
        "    live = Session.builder.getOrCreate()\n"
        '    live.table("MIG_WORK.WF0006_SEG_01_OUT")\n'
        '    return "OK"',
    )
    errors = _errors(bad)
    assert any(e.startswith("rule:session_scope") and "Session" in e for e in errors), errors


def test_the_session_class_is_refused_at_the_import_even_if_never_used():
    bad = GOOD.replace("import pandas as pd",
                       "import pandas as pd\nfrom snowflake.snowpark import Session")
    errors = _errors(bad)
    assert any(e.startswith("rule:session_scope") and "Session" in e for e in errors), errors


@pytest.mark.parametrize("method", ["sql", "call"])
def test_raw_sql_session_methods_are_refused_on_any_receiver(method):
    bad = GOOD.replace('    return "OK"', "    live." + method + '("DROP TABLE X")\n    return "OK"')
    errors = _errors(bad)
    assert any(e.startswith("rule:no_session_sql") and method in e for e in errors), errors


@pytest.mark.parametrize("method", ["add_packages", "add_import", "add_requirements", "udf",
                                    "sproc", "udtf", "udaf", "file", "query_history",
                                    "use_database", "use_schema", "use_role", "use_warehouse",
                                    "close"])
def test_every_other_session_method_is_refused_on_any_receiver(method):
    """None of these has a local-testing equivalent that would fail, so the parity gate would
    never see them: the rules are the only thing standing between them and a real account."""
    bad = GOOD.replace('    return "OK"', "    live." + method + '("x")\n    return "OK"')
    errors = _errors(bad)
    assert any(e.startswith("rule:no_io") and method in e for e in errors), errors


def test_the_positive_controls_survive_every_new_refusal():
    """Nothing added by F2/F3 may cost the three good procedures their clean bill of health."""
    for source in (GOOD, GOOD2, GOOD3):
        assert _errors(source) == [], source


# --- final fix wave M4: the dunder pre-check is the simulator's, exactly ----------------------
# The module docstring claims this check mirrors `alteryx_sim._check_python_tool_script`'s. That
# one is `startswith("__")`, which also refuses `__private` and name-mangled attributes; this one
# was `^__.*__$`, which did not. Both err toward refusal, so nothing was unsafe -- the claim was
# just inexact. It is now the same rule.

@pytest.mark.parametrize("name", ["__private", "__mangled", "__dict__", "__class__"])
def test_any_name_mangled_or_dunder_attribute_is_refused(name):
    bad = GOOD.replace('    return "OK"', f"    out.{name}\n" + '    return "OK"')
    errors = _errors(bad)
    assert any(e.startswith("rule:no_io") and name in e for e in errors), errors


@pytest.mark.parametrize("name", ["__private", "__dict__"])
def test_any_name_mangled_or_dunder_bare_name_is_refused(name):
    bad = GOOD.replace('    return "OK"', f"    {name}\n" + '    return "OK"')
    errors = _errors(bad)
    assert any(e.startswith("rule:no_io") and name in e for e in errors), errors


# --- round 2, R1 (scoped re-review): three more write routes, none of them a dunder -----------
# Confirmed against snowflake-snowpark-python 1.55 by the re-reviewer: `DataFrame` can create a
# view or a dynamic table (and the view ones SUCCEED in the Local Testing Framework, so an
# undeclared object is created and the segment still PASSes -- I4's class under another name);
# `DataFrame.session` hands back the live session, and the session rule only knew the bare NAME
# `session`; and `to_pandas()` returns a real pandas frame whose own writers reach the filesystem.

#: A procedure that uses the DataFrame API broadly and legitimately. Nothing R1 refuses may cost
#: it its clean bill of health: 28 distinct DataFrame/Column methods, no view, no session
#: attribute, no pandas writer, one sink.
GOOD4 = '''# tool 9: Python tool -- the DataFrame API surface a translator actually reaches for
from snowflake.snowpark.functions import coalesce, col, count, lit, sum as sum_
from snowflake.snowpark.types import DoubleType, StringType, StructField, StructType


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    rows = session.table("MIG_WORK.WF0006_SEG_01_OUT")
    kept = (rows
            .filter(col("BILLED") > lit(0))
            .where(col("CAP").is_not_null())
            .select(col("CUSTOMER"), col("PERIOD"), col("BILLED"), col("CAP"))
            .with_column("NET", coalesce(col("BILLED"), lit(0.0)))
            .with_columns(["SPARE"], [lit(0.0)])
            .with_column_renamed(col("SPARE"), "UNUSED")
            .drop(col("UNUSED"))
            .dropna(subset=["CUSTOMER"])
            .fillna(0.0, subset=["NET"])
            .distinct()
            .sort(col("CUSTOMER").asc(), col("PERIOD").desc())
            .limit(1000))
    totals = kept.group_by(col("CUSTOMER")).agg(
        sum_(col("NET")).alias("TOTAL"), count(col("PERIOD")).alias("PERIODS"))
    both = (kept.join(totals, ["CUSTOMER"])
                .cross_join(totals.limit(1))
                .union(kept.join(totals, ["CUSTOMER"]).cross_join(totals.limit(1)))
                .union_all(kept.join(totals, ["CUSTOMER"]).cross_join(totals.limit(1)))
                .union_by_name(kept.join(totals, ["CUSTOMER"]).cross_join(totals.limit(1)))
                .except_(totals.cross_join(kept.limit(0)))
                .intersect(totals.cross_join(kept.limit(0)))
                .rename(col("TOTAL"), "TOTAL_NET"))
    how_many = both.count()
    shape = both.describe()
    head = both.first()
    every = both.collect()
    pdf = both.sample(n=0.5).to_pandas()
    schema = StructType([StructField("CUSTOMER", StringType(254)), StructField("TOTAL_NET", DoubleType())])
    out = session.create_dataframe(pdf[["CUSTOMER", "TOTAL_NET"]], schema=schema)
    out.write.mode("overwrite").save_as_table("MIG_WORK.WF0006_SEG_02_OUT")
    return "OK" if how_many and shape and head and every else "OK"
'''


def test_the_broad_dataframe_api_positive_control_is_clean():
    """28 distinct DataFrame/Column methods, none of them refused. This is the control that says
    R1's deny-lists ban write routes, not the DataFrame API."""
    assert _errors(GOOD4) == [], _errors(GOOD4)


@pytest.mark.parametrize("method", ["create_or_replace_view", "create_or_replace_temp_view",
                                    "create_or_replace_dynamic_table"])
def test_creating_a_view_or_dynamic_table_is_refused(method):
    """Probe (a): rules-clean AND valid in the Local Testing Framework, so the undeclared object
    really gets created and the segment still PASSes -- exactly I4's failure under another name."""
    bad = GOOD.replace('    return "OK"',
                       f'    out.{method}("ANALYTICS.CURATED.SHADOW")\n'
                       '    return "OK"')
    errors = _errors(bad)
    assert any(e.startswith("rule:no_io") and method in e for e in errors), errors


@pytest.mark.parametrize("method", ["write_pandas", "copy_into_table", "cache_result"])
def test_the_remaining_snowpark_write_routes_are_refused(method):
    bad = GOOD.replace('    return "OK"', f'    out.{method}("X")\n    return "OK"')
    errors = _errors(bad)
    assert any(e.startswith("rule:no_io") and method in e for e in errors), errors


def test_write_pandas_through_the_dataframes_own_session_is_refused():
    """Probe (b): `DataFrame.session` IS the live session, and `session` was refused only as a
    bare Name. Both halves are now refused -- the attribute and the method."""
    bad = GOOD.replace('    return "OK"',
                       '    out.session.write_pandas(pd.DataFrame(), "ANALYTICS.CURATED.SHADOW")\n'
                       '    return "OK"')
    errors = _errors(bad)
    assert any(e.startswith("rule:session_scope") and "session" in e for e in errors), errors
    assert any(e.startswith("rule:no_io") and "write_pandas" in e for e in errors), errors


def test_the_session_attribute_is_refused_on_any_receiver():
    """The attribute alone, never called: reading the live session off anything is the evasion."""
    bad = GOOD.replace('    return "OK"', '    live = out.session\n    return "OK"')
    errors = _errors(bad)
    assert any(e.startswith("rule:session_scope") for e in errors), errors


def test_session_dot_table_is_still_the_one_permitted_shape():
    """The bare-Name rule is unchanged: `session.table(...)` in `run` stays clean, and the new
    attribute rule must not fire on the ATTRIBUTE of that call (`table`), only on `.session`."""
    assert _errors(GOOD) == []


@pytest.mark.parametrize("writer", ["to_csv", "to_parquet", "to_json", "to_excel", "to_pickle",
                                    "to_sql", "to_feather", "to_hdf", "to_clipboard", "to_html",
                                    "to_latex", "to_markdown", "to_xml", "to_stata", "to_gbq"])
def test_every_pandas_writer_is_refused(writer):
    """Probe (c): `to_pandas()` hands back a real pandas frame, and its writers are the filesystem
    reach the accident-guard caveat names -- here, in a procedure that would run on an account."""
    bad = GOOD.replace('    return "OK"',
                       f'    out.to_pandas().{writer}("C:/tmp/leak")\n'
                       '    return "OK"')
    errors = _errors(bad)
    assert any(e.startswith("rule:no_io") and writer in e for e in errors), errors


def test_to_pandas_itself_is_not_a_writer():
    """The control for the list above: reading a frame into pandas is how row-sequential logic is
    expressed at all (spec §4.2), so `to_pandas` must stay clean."""
    assert _errors(GOOD) == [] and "to_pandas" in GOOD


# --- output targets, phase 2, Task F: the third phase-1 leftover -------------------------------
# `to_pandas()`'s frame is real pandas, but `.to_numpy()`/`.values` hands back a real `numpy`
# array just as readily, and `numpy`'s OWN file writers (`savetxt`, `savez`, `savez_compressed`,
# `tofile`, a pandas/numpy array's `.dump()`) reach the filesystem exactly as the pandas writers
# do. `numpy` is already on `ALLOWED_MODULES` (row-sequential math needs it), so only the write
# routes themselves are refused -- the same accident-guard register as `PANDAS_WRITERS` (ledger:
# filesystem-write routes through allowed modules; this is not a security boundary).

@pytest.mark.parametrize("line", [
    "pdf.to_numpy().tofile('x.bin')", "np.savetxt('x.txt', pdf.to_numpy())", "np.savez('x', a=1)",
    "np.savez_compressed('x', a=1)", "pdf.values.dump('x.pkl')", "savetxt('x', 1)",
])
def test_numpy_file_writers_are_refused(line):
    source = GOOD.replace("    return \"OK\"", f"    {line}\n    return \"OK\"").replace(
        "import pandas as pd", "import pandas as pd\nimport numpy as np")
    errors = rules.check_proc_py(source, "wf_0006", "seg_02", CONTRACT)
    assert any(e.startswith("rule:no_io") for e in errors), errors


def test_to_numpy_itself_stays_legal():
    source = GOOD.replace("    return \"OK\"", "    arr = pdf.to_numpy()\n    return \"OK\"")
    assert rules.check_proc_py(source, "wf_0006", "seg_02", CONTRACT) == []
