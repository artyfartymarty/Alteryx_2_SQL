"""The simulator's python tool: a sandboxed script with the Alteryx.read/write pandas shim."""
from __future__ import annotations

import pytest

from dev import alteryx_sim as sim

FIELDS = [{"name": "CUSTOMER", "type": "V_String", "size": 20, "scale": None},
          {"name": "PERIOD", "type": "V_String", "size": 7, "scale": None},
          {"name": "BILLED", "type": "Double", "size": 8, "scale": None},
          {"name": "CAP", "type": "Double", "size": 8, "scale": None},
          {"name": "CANCELLED", "type": "Bool", "size": 1, "scale": None}]
ROWS = [["A", "2026-01", 100.0, 80.0, False], ["A", "2026-02", 50.0, 80.0, False],
        ["A", "2026-03", 10.0, 80.0, True], ["B", "2026-01", None, 80.0, False]]
TABLE = {"fields": FIELDS, "rows": ROWS}

SCRIPT = """
import pandas as pd
df = Alteryx.read("#1")
df = df.sort_values(["CUSTOMER", "PERIOD"]).reset_index(drop=True)
out = []
for customer, group in df.groupby("CUSTOMER", sort=False):
    deferred = 0.0
    for _, row in group.iterrows():
        if bool(row["CANCELLED"]):
            deferred = 0.0
        billed = 0.0 if pd.isna(row["BILLED"]) else float(row["BILLED"])
        recognized = min(billed + deferred, float(row["CAP"]))
        deferred = billed + deferred - recognized
        out.append({"CUSTOMER": customer, "PERIOD": row["PERIOD"],
                    "RECOGNIZED": round(recognized, 2), "DEFERRED": round(deferred, 2)})
Alteryx.write(pd.DataFrame(out), 1)
"""


def _node(script=SCRIPT):
    return {"tool_id": "3", "type": "python", "config": {"script": script}}


def test_shim_runs_the_script_and_types_the_output():
    result = sim.run_python_tool(SCRIPT, [TABLE])
    assert set(result) == {1}
    out = result[1]
    assert [f["name"] for f in out["fields"]] == ["CUSTOMER", "PERIOD", "RECOGNIZED", "DEFERRED"]
    assert [f["type"] for f in out["fields"]] == ["V_WString", "V_WString", "Double", "Double"]
    assert out["rows"] == [["A", "2026-01", 80.0, 20.0], ["A", "2026-02", 70.0, 0.0],
                           ["A", "2026-03", 10.0, 0.0], ["B", "2026-01", 0.0, 0.0]]


def test_nan_becomes_null_and_ints_bools_dates_map_to_alteryx_types():
    script = """
import pandas as pd
df = Alteryx.read("#1")
out = pd.DataFrame({"N": [1, 2], "F": [1.5, float("nan")], "B": [True, False],
                    "S": ["x", None], "D": pd.to_datetime(["2026-01-02", None])})
Alteryx.write(out, 2)
"""
    out = sim.run_python_tool(script, [TABLE])[2]
    assert [f["type"] for f in out["fields"]] == ["Int64", "Double", "Bool", "V_WString", "DateTime"]
    assert out["rows"] == [[1, 1.5, True, "x", "2026-01-02 00:00:00"], [2, None, False, None, None]]


@pytest.mark.parametrize("bad", [
    "import os\nAlteryx.write(Alteryx.read('#1'), 1)",
    "open('x.txt', 'w')",
    "import subprocess",
    "__import__('socket')",
    "import pandas as pd\npd.read_csv('x.csv')",
])
def test_sandbox_refuses_filesystem_network_and_disallowed_imports(bad):
    with pytest.raises(sim.UnsupportedTool):
        sim.run_python_tool(bad, [TABLE])


def test_sim_python_reads_connections_in_order_and_returns_only_written_anchors():
    second = {"fields": FIELDS[:1], "rows": [["Z"]]}
    node = _node("Alteryx.write(Alteryx.read('#2'), 3)")
    result = sim.sim_python(node, {"Input": [TABLE, second]}, sim._Context({}, {}, {}, [], {}))
    assert list(result) == ["3"]
    assert result["3"]["rows"] == [["Z"]]


def test_a_script_error_is_reported_as_unsupported_with_the_tool_id():
    with pytest.raises(sim.UnsupportedTool, match="tool 3"):
        sim.sim_python(_node("raise ValueError('boom')"), {"Input": [TABLE]}, sim._Context({}, {}, {}, [], {}))


# --- fix round 1 / C1: the sandbox is an accident guard, not a security boundary. An AST
# pre-check refuses dunder name/attribute access and non-allow-listed imports before exec() ever
# runs, closing the gadgets a reviewer's probe scripts used to recover the real __import__/open
# through an imported module's own __builtins__ dict, or to reach the class graph via
# ().__class__.__base__.__subclasses__(). ---

@pytest.mark.parametrize("bad", [
    "import pandas as pd\nreal_import = pd.__builtins__['__import__']",
    "import pandas as pd\npd.__builtins__",
    "x = ().__class__.__base__.__subclasses__()",
    "def f():\n    pass\nreal_builtins = f.__globals__['__builtins__']",
    "from os import path",
    "from . import os",
])
def test_ast_precheck_refuses_dunder_access_and_bad_imports(bad):
    with pytest.raises(sim.UnsupportedTool):
        sim.run_python_tool(bad, [TABLE])


def test_recovering_real_open_via_module_builtins_never_runs(tmp_path):
    """Mirrors the reviewer's probe: recover the real, unrestricted `open` through
    pandas.__builtins__ and use it to write a marker file. If the AST pre-check runs before any
    statement of the script executes (rather than merely reacting to whatever line happens to
    raise first), the marker is never created."""
    marker = tmp_path / "escaped.txt"
    script = f"""
import pandas as pd
real_open = pd.__builtins__['open']
with real_open({str(marker)!r}, "w") as fh:
    fh.write("escaped")
Alteryx.write(pd.DataFrame({{'ok': ['yes']}}), 1)
"""
    with pytest.raises(sim.UnsupportedTool):
        sim.run_python_tool(script, [TABLE])
    assert not marker.exists()


def test_ast_precheck_does_not_disturb_a_normal_script():
    """The precheck must not false-positive on ordinary, allowed code: plain names, plain
    attribute access (no leading `__`), and an allow-listed `from … import`."""
    script = "from pandas import DataFrame\nAlteryx.write(DataFrame({'x': [1, 2]}), 1)"
    result = sim.run_python_tool(script, [TABLE])
    assert result[1]["rows"] == [[1], [2]]


# --- fix round 1 / C2: dtype detection must use pandas' own type predicates, not a str(dtype)
# prefix match, so pandas' nullable Int64/boolean dtypes (and a NULL of any kind) are typed and
# NULLed correctly instead of silently stringified to the literal "<NA>". ---

def test_nullable_int64_with_a_null_stays_int64_with_none():
    script = """
import pandas as pd
out = pd.DataFrame({"N": pd.array([1, None], dtype="Int64")})
Alteryx.write(out, 1)
"""
    out = sim.run_python_tool(script, [TABLE])[1]
    assert out["fields"][0]["type"] == "Int64"
    assert out["rows"] == [[1], [None]]


def test_nullable_boolean_with_a_null_stays_bool_with_none():
    script = """
import pandas as pd
out = pd.DataFrame({"B": pd.array([True, None], dtype="boolean")})
Alteryx.write(out, 1)
"""
    out = sim.run_python_tool(script, [TABLE])[1]
    assert out["fields"][0]["type"] == "Bool"
    assert out["rows"] == [[True], [None]]


def test_object_column_with_pd_na_becomes_v_wstring_with_none():
    script = """
import pandas as pd
out = pd.DataFrame({"S": pd.Series(["x", pd.NA], dtype="object")})
Alteryx.write(out, 1)
"""
    out = sim.run_python_tool(script, [TABLE])[1]
    assert out["fields"][0]["type"] == "V_WString"
    assert out["rows"] == [["x"], [None]]


def test_bool_round_trips_through_read_and_write_unchanged():
    """A Bool column read straight through and written back must keep its type and values,
    including a NULL — this is the exact corruption C2 found: `_table_to_pandas` types an
    incoming Bool field as pandas' nullable "boolean" dtype, which the old str(dtype)-prefix
    check in `_pandas_to_table` did not recognize as Bool at all."""
    fields = FIELDS  # includes CANCELLED: Bool
    rows = [["A", "2026-01", 100.0, 80.0, False], ["A", "2026-02", 50.0, 80.0, None]]
    table_with_null_bool = {"fields": fields, "rows": rows}
    script = 'df = Alteryx.read("#1")\nAlteryx.write(df[["CUSTOMER", "CANCELLED"]], 1)'
    out = sim.run_python_tool(script, [table_with_null_bool])[1]
    assert [f["type"] for f in out["fields"]] == ["V_WString", "Bool"]
    assert out["rows"] == [["A", False], ["A", None]]


# --- final fix wave M7: `import pkg.sub` binds the PACKAGE, as CPython does -------------------

def test_a_dotted_plain_import_binds_the_top_level_package():
    """CPython's `__import__` contract: `import a.b` binds `a`, not `a.b` (only the `fromlist`
    form returns the submodule). The guard returned the submodule either way, so a script writing
    `import pandas.io` would have ended up with the name `pandas` bound to `pandas.io`. No
    committed sample script does this, which is why nothing caught it."""
    import pandas

    assert sim._guarded_import("pandas.io") is pandas
    assert sim._guarded_import("pandas") is pandas
    assert sim._guarded_import("pandas.io", fromlist=("common",)) is pandas.io


def test_a_dotted_import_of_a_module_outside_the_allow_list_is_still_refused():
    with pytest.raises(sim.UnsupportedTool):
        sim._guarded_import("os.path")
