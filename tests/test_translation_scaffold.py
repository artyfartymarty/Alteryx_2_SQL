"""translation_scaffold.py: the orchestrator writes every mechanical line of a translation (Task L8, R1).

Live runs with a real model spent most of each translator session on lines nothing but the contract,
the segment DAG and `intake/mappings.yaml` decide -- a procedure's header, its `LET` lines, its write
statements, its work-table names -- and a dbt translator overflowed its context without writing one
file. The scaffold writes all of that; the translator only replaces each `TODO(scaffold)` body.

The acceptance test is the committed reference: for every canned translation of every sample, the
skeleton's mechanical parts equal the canned file's -- a SQL procedure's header, session line, `LET`
lines, every statement with its CTE bodies collapsed (the write head, the CTE names in order, the final
`SELECT … FROM`, a MERGE's ON/WHEN clauses, the C3 work-table names) and its `RETURN`; a Snowpark
module's signature, reads, writes and tool comments; a dbt project's two fixed files byte for byte, its
model files, `config(...)` lines, `sources.yml`, and `schema.yml` without its free-text descriptions.
Transplanting the canned CTE bodies into the skeleton compiles (`compile_check.py` OK) for every canned
SQL segment and for the dbt project. One documented difference, pinned below with its reason (never a
canned file edited to fit):

* `wf_0004/seg_03`'s second statement appends `ORDER BY …` to the final `SELECT … FROM t5_transpose`:
  the Transpose's configured row order, which the translator may add after the scaffold's final FROM
  (the scaffold cannot know whether an output's order matters; the contract's `ordering` is judgment).

Nothing here has run on Snowflake or Alteryx: every file is built locally from `samples/`.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

import compile_check
import render_snowpark
import translation_scaffold as ts
from lib import snowpark_rules
from lib.dbt_project import PROFILES_TEMPLATE
from lib.io import read_json, write_json, write_yaml
from lib.paths import Repo
from lib.proc_runner import _split_code, masked_code, parse_proc
from lib.scaffold import TODO_MARKER, todo_errors
from tests.helpers import copy_pristine_mappings_and_catalog, prepare_workflow

ROOT = Path(__file__).parents[1]
SAMPLES = ROOT / "samples"

#: Every canned SQL procedure: a `proc.sql` with no `proc.py` beside it (a Snowpark segment's proc.sql
#: is the rendered wrapper, not a translation).
SQL_SEGMENTS = sorted((p.parents[3].name, p.parent.name) for p in SAMPLES.glob("wf_*/canned/segments/*/proc.sql")
                      if not (p.parent / "proc.py").is_file())
SNOWPARK_SEGMENTS = sorted((p.parents[3].name, p.parent.name) for p in SAMPLES.glob("wf_*/canned/segments/*/proc.py"))
DBT_WORKFLOWS = sorted(p.parents[1].name for p in SAMPLES.glob("wf_*/canned/dbt"))

#: The one documented difference (module docstring): (workflow, segment, statement index) -> the clause
#: the canned statement carries after the scaffold's final `SELECT … FROM <cte>`.
TRAILING_CLAUSES = {("wf_0004", "seg_03", 1): "ORDER BY"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """`built(wf_id)` -> a Repo holding that sample parsed, segmented, intake READY from its own recorded
    answers and the canned contracts (and translations) copied into place."""
    repos: dict[str, Repo] = {}

    def build(wf_id: str) -> Repo:
        if wf_id not in repos:
            repos[wf_id] = prepare_workflow(tmp_path_factory.mktemp(wf_id), wf_id)
        return repos[wf_id]

    return build


def _canned(wf_id: str, *parts: str) -> str:
    return (SAMPLES / wf_id / "canned").joinpath(*parts).read_text(encoding="utf-8")


# --- the SQL procedure's mechanical parts ------------------------------------------------------------

_CTE_RE = re.compile(r"\b(t\d[A-Za-z0-9_]*)\s+AS\s*\(", re.IGNORECASE)


def _matching(scan: str, open_at: int) -> int:
    depth = 0
    for index in range(open_at, len(scan)):
        if scan[index] == "(":
            depth += 1
        elif scan[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError(f"unbalanced parenthesis at {open_at}")


def _cte_spans(statement: str) -> list[tuple[str, int, int]]:
    """(name, body start, body end) of every tool CTE of `statement`, outermost only, in order."""
    scan = masked_code(statement)                      # comments and strings blanked, same offsets
    spans, pos = [], 0
    while (match := _CTE_RE.search(scan, pos)) is not None:
        close = _matching(scan, match.end() - 1)
        spans.append((match.group(1), match.end(), close))
        pos = close + 1
    return spans


def collapse(statement: str) -> str:
    """`statement` with its comments removed, every tool CTE's body replaced by `…`, whitespace
    squeezed and no space around parentheses, commas and semicolons -- what a skeleton and a canned
    translation must agree on."""
    code = masked_code(statement, strings=False)
    pieces, pos = [], 0
    for _, start, end in _cte_spans(statement):
        pieces += [code[pos:start], "…"]
        pos = end
    pieces.append(code[pos:])
    return re.sub(r"\s*([(),;])\s*", r"\1", " ".join("".join(pieces).split()))


def _header(text: str) -> str:
    start = re.search(r"CREATE\s+OR\s+REPLACE\s+PROCEDURE", text).start()
    return " ".join(text[start:text.index("$$")].split())


def sql_mechanics(text: str) -> dict:
    proc = parse_proc(text)
    return {
        "header": _header(text),
        "session": proc.session,
        "lets": [" ".join(let.text.split()) for let in proc.lets],
        "statements": [collapse(statement) for statement in proc.statements],
        "returns": [ret.text for ret in proc.returns],
    }


def _work_tables(text: str, own: str) -> set[str]:
    return set(re.findall(r"MIG_WORK\.[A-Z0-9_]+", text)) - {own}


@pytest.mark.parametrize(("wf_id", "seg"), SQL_SEGMENTS)
def test_the_sql_skeleton_reproduces_every_canned_procedure_mechanically(wf_id, seg, built):
    repo = built(wf_id)
    skeleton = ts.render_sql(repo, wf_id, seg)
    canned = _canned(wf_id, "segments", seg, "proc.sql")
    mine, theirs = sql_mechanics(skeleton), sql_mechanics(canned)
    for key in ("header", "session", "lets", "returns"):
        assert mine[key] == theirs[key], f"{wf_id}/{seg}: {key} differs"
    assert len(mine["statements"]) == len(theirs["statements"]), (wf_id, seg, mine["statements"], theirs["statements"])
    for index, (statement, reference) in enumerate(zip(mine["statements"], theirs["statements"])):
        if statement == TODO_MARKER:
            # a PreSQL/PostSQL slot: the canned statement there touches the same target
            neighbour = next(s for s in mine["statements"] if s != TODO_MARKER)
            target = re.search(r"IDENTIFIER\(:([A-Z0-9_]+_TGT)\)", neighbour).group(1)
            assert f"IDENTIFIER(:{target})" in reference, (wf_id, seg, index, reference)
            continue
        trailing = TRAILING_CLAUSES.get((wf_id, seg, index))
        if trailing:
            assert reference.startswith(statement + " "), (wf_id, seg, index, statement, reference)
            assert reference[len(statement):].strip().startswith(trailing), (wf_id, seg, index, reference)
            continue
        assert statement == reference, f"{wf_id}/{seg} statement {index}:\n{statement}\n!=\n{reference}"
    own = f"MIG_WORK.{wf_id.upper().replace('_', '')}_{seg.upper()}"
    canned_code = masked_code(canned, strings=False)
    assert _work_tables(skeleton, own) == _work_tables(canned_code, own), f"{wf_id}/{seg}: C3 names differ"


def _transplant(skeleton: str, canned: str) -> str:
    """The skeleton with every TODO(scaffold) body replaced by the canned translation's: a CTE stub
    by the canned CTE of the same name in the same statement, a statement slot by the canned
    statement at that position."""
    theirs = parse_proc(canned).statements
    chunks = _split_code(skeleton, ";")
    statement = -1
    out = []
    for chunk in chunks:
        code = " ".join(masked_code(chunk, strings=False).split())
        if re.match(r"(CREATE OR REPLACE (TRANSIENT )?TABLE|INSERT INTO|MERGE INTO|TRUNCATE|DELETE|UPDATE|TODO\()",
                    code):
            statement += 1
            reference = theirs[statement]
            if code == TODO_MARKER:
                out.append("\n  " + reference)
                continue
            bodies = {name: reference[start:end] for name, start, end in _cte_spans(reference)}
            for name, start, end in reversed(_cte_spans(chunk)):
                assert chunk[start:end].strip() == TODO_MARKER, (name, chunk[start:end])
                chunk = chunk[:start] + bodies[name] + chunk[end:]
        out.append(chunk)
    assert statement == len(theirs) - 1
    return ";".join(out)


@pytest.mark.parametrize(("wf_id", "seg"), SQL_SEGMENTS)
def test_the_canned_bodies_transplanted_into_the_skeleton_compile(wf_id, seg, built):
    repo = built(wf_id)
    filled = _transplant(ts.render_sql(repo, wf_id, seg), _canned(wf_id, "segments", seg, "proc.sql"))
    repo.seg(wf_id, seg, "proc.sql").write_text(filled, encoding="utf-8")
    report = compile_check.compile_check(repo, wf_id, seg)
    assert report["status"] == "OK", (wf_id, seg, report["errors"], filled)


@pytest.mark.parametrize(("wf_id", "seg"), SQL_SEGMENTS)
def test_every_sql_stub_body_is_exactly_the_marker_and_the_skeleton_is_refused_by_name(wf_id, seg, built):
    repo = built(wf_id)
    skeleton = ts.render_sql(repo, wf_id, seg)
    for statement in parse_proc(skeleton).statements:
        if " ".join(masked_code(statement, strings=False).split()) == TODO_MARKER:
            continue
        for name, start, end in _cte_spans(statement):
            assert statement[start:end].strip() == TODO_MARKER, (name, statement[start:end])
    report = compile_check._check(skeleton, read_json(repo.seg(wf_id, seg, "contract.json")), wf_id, seg)
    assert report["status"] == "ERROR"
    assert report["errors"] and all(error.startswith("scaffold:todo: proc.sql line ") for error in report["errors"])
    assert len(report["errors"]) == skeleton.count(TODO_MARKER)


# --- the Snowpark module's mechanical parts ----------------------------------------------------------


def _snowpark_mechanics(source: str) -> dict:
    tree = ast.parse(source)
    reads, writes = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            literal = snowpark_rules._literal(node.args[0]) if len(node.args) == 1 else None
            if node.func.attr == "table" and literal is not None:
                reads.add(literal)
            if node.func.attr == "save_as_table" and literal is not None:
                writes.add((snowpark_rules._chained_mode(node.func.value), literal))
    runs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"]
    returns = [ast.unparse(n) for n in ast.walk(runs[0]) if isinstance(n, ast.Return)]
    return {"signature": [a.arg for a in runs[0].args.args], "reads": reads, "writes": writes,
            "returns": returns, "tools": snowpark_rules._tool_comments(source)}


@pytest.mark.parametrize(("wf_id", "seg"), SNOWPARK_SEGMENTS)
def test_the_snowpark_skeleton_reproduces_the_canned_module_mechanically(wf_id, seg, built):
    repo = built(wf_id)
    skeleton = ts.render_snowpark(repo, wf_id, seg)
    assert _snowpark_mechanics(skeleton) == _snowpark_mechanics(_canned(wf_id, "segments", seg, "proc.py"))
    # the wrapper render_snowpark.py builds around it is the canned proc.sql's, up to the body
    rendered = render_snowpark.render(skeleton, wf_id, seg, "3.11")
    canned_sql = _canned(wf_id, "segments", seg, "proc.sql")
    assert rendered.split("$$")[0] == canned_sql.split("$$")[0]


@pytest.mark.parametrize(("wf_id", "seg"), SNOWPARK_SEGMENTS)
def test_a_filled_snowpark_skeleton_passes_every_snowpark_rule_and_an_unfilled_one_is_refused(wf_id, seg, built):
    repo = built(wf_id)
    skeleton = ts.render_snowpark(repo, wf_id, seg)
    contract = read_json(repo.seg(wf_id, seg, "contract.json"))
    filled = _fill_snowpark(skeleton)
    assert snowpark_rules.segment_rule_errors(repo, wf_id, seg, contract, filled) == []
    repo.seg(wf_id, seg, "proc.py").write_text(skeleton, encoding="utf-8")
    repo.seg(wf_id, seg, "proc.sql").write_text(render_snowpark.render(skeleton, wf_id, seg, "3.11"), encoding="utf-8")
    report = compile_check.compile_check(repo, wf_id, seg)
    assert report["status"] == "ERROR" and report["target"] == "snowpark"
    assert report["errors"] and all(error.startswith("scaffold:todo: proc.py line ") for error in report["errors"])


def _fill_snowpark(skeleton: str) -> str:
    """Every TODO(scaffold) line replaced by the smallest code the stub's own comment asks for: each
    `out_<stream>` it names bound to the first DataFrame it reads (a PreSQL/PostSQL slot: `pass`)."""
    lines = skeleton.splitlines()
    out = []
    for index, line in enumerate(lines):
        if TODO_MARKER not in line:
            out.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip())]
        comment = "\n".join(lines[max(0, index - 3):index])
        reads = re.findall(r"reads ([a-z_][a-z0-9_]*)", comment)
        assigns = re.findall(r"assign (out_[a-z0-9_]+)", comment)
        if assigns:
            source = reads[0] if reads else "None"
            out.extend(f"{indent}{name} = {source}" for name in assigns)
        else:
            out.append(f"{indent}pass")
    return "\n".join(out) + "\n"


# --- the dbt project ---------------------------------------------------------------------------------


def _without_descriptions(value):
    if isinstance(value, dict):
        return {k: _without_descriptions(v) for k, v in value.items() if k != "description"}
    if isinstance(value, list):
        return [_without_descriptions(v) for v in value]
    return value


def _tool_ids(sql: str) -> set[str]:
    return set(re.findall(r"--\s*tool\s+([0-9A-Za-z/]+)", sql))


def _reads(sql: str) -> set[str]:
    return set(re.findall(r"\{\{\s*((?:source|ref)\([^)]*\))\s*\}\}", sql))


def _final_select(sql: str) -> str:
    code = collapse(sql)
    return code[code.rindex("SELECT"):]


@pytest.mark.parametrize("wf_id", DBT_WORKFLOWS)
def test_the_dbt_skeleton_reproduces_the_canned_project_mechanically(wf_id, built):
    repo = built(wf_id)
    files = ts.render_dbt(repo, wf_id)
    canned = SAMPLES / wf_id / "canned" / "dbt"
    assert files["dbt_project.yml"] == (canned / "dbt_project.yml").read_text(encoding="utf-8")
    assert files["profiles.yml"] == PROFILES_TEMPLATE == (canned / "profiles.yml").read_text(encoding="utf-8")
    assert files["models/sources.yml"] == (canned / "models" / "sources.yml").read_text(encoding="utf-8")
    assert _without_descriptions(yaml.safe_load(files["models/schema.yml"])) == _without_descriptions(
        yaml.safe_load((canned / "models" / "schema.yml").read_text(encoding="utf-8")))
    models = sorted(rel for rel in files if rel.startswith("models/") and rel.endswith(".sql"))
    assert models == sorted(f"models/{p.name}" for p in (canned / "models").glob("*.sql"))
    every_tool = set()
    for rel in models:
        mine, theirs = files[rel], (canned / rel).read_text(encoding="utf-8")
        assert mine.splitlines()[0] == theirs.splitlines()[0], f"{rel}: the config line differs"
        assert [n for n, _, _ in _cte_spans(mine)] == [n for n, _, _ in _cte_spans(theirs)], f"{rel}: CTE names differ"
        assert _final_select(mine) == _final_select(theirs), rel
        assert _reads(mine) == _reads(theirs), rel
        assert _tool_ids(mine) == _tool_ids(theirs), rel
        every_tool |= _tool_ids(mine)
        for name, start, end in _cte_spans(mine):
            assert mine[start:end].strip() == TODO_MARKER, (rel, name)


@pytest.mark.parametrize("wf_id", DBT_WORKFLOWS)
def test_the_canned_dbt_bodies_transplanted_into_the_skeleton_compile_and_the_skeleton_itself_is_refused(
        wf_id, built, tmp_path):
    repo = built(wf_id)
    files = ts.render_dbt(repo, wf_id)
    canned = SAMPLES / wf_id / "canned" / "dbt"
    project = repo.wf(wf_id, "dbt")
    shutil.rmtree(project)
    for rel, text in files.items():
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text(text, encoding="utf-8", newline="\n")
    refused = compile_check.compile_check(repo, wf_id, None, target="dbt")
    assert refused["status"] == "ERROR"
    assert refused["errors"] and all(e.startswith("scaffold:todo: models/") for e in refused["errors"]), refused
    for rel, text in files.items():
        if not (rel.startswith("models/") and rel.endswith(".sql")):
            continue
        theirs = (canned / rel).read_text(encoding="utf-8")
        bodies = {name: theirs[start:end] for name, start, end in _cte_spans(theirs)}
        for name, start, end in reversed(_cte_spans(text)):
            text = text[:start] + bodies[name] + text[end:]
        (project / rel).write_text(text, encoding="utf-8", newline="\n")
    report = compile_check.compile_check(repo, wf_id, None, target="dbt")
    assert report["status"] == "OK", report["errors"]


# --- writing: never over an existing file; the CLI ---------------------------------------------------


def _manifest(repo: Repo, wf_id: str, **fields) -> None:
    write_json(repo.wf(wf_id, "manifest.json"), {"id": wf_id, "status": {}, "metrics": {}, **fields})


def test_the_scaffold_never_overwrites_an_existing_translation(built, capsys):
    """The MockRunner path and a resumed run both reach the scaffold with a translation already on
    disk: every canned file stays byte for byte what it was."""
    for wf_id in sorted({wf for wf, _ in SQL_SEGMENTS + SNOWPARK_SEGMENTS}):
        repo = built(wf_id)
        order = [seg for wave in read_json(repo.wf(wf_id, "segments", "order.json")) for seg in wave]
        for seg in order:
            for name in ("proc.sql", "proc.py"):
                source = SAMPLES / wf_id / "canned" / "segments" / seg / name
                if source.is_file():
                    shutil.copy(source, repo.seg(wf_id, seg, name))
            before = {p.name: p.read_bytes() for p in repo.seg(wf_id, seg).glob("proc.*")}
            assert ts.main([wf_id, "--segment", seg, "--root", str(repo.root)]) == 0
            after = {p.name: p.read_bytes() for p in repo.seg(wf_id, seg).glob("proc.*")}
            assert after == before, f"{wf_id}/{seg}: the scaffold overwrote a translation"
        assert "kept" in capsys.readouterr().out


def test_the_scaffold_never_overwrites_an_existing_dbt_file(built, capsys):
    wf_id = DBT_WORKFLOWS[0]
    repo = built(wf_id)
    _manifest(repo, wf_id, output_kind="dbt")
    project = repo.wf(wf_id, "dbt")
    before = {p.relative_to(project).as_posix(): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert ts.main([wf_id, "--root", str(repo.root)]) == 0
    after = {p.relative_to(project).as_posix(): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert after == before
    # one file missing: only that one is written
    (project / "models" / "schema.yml").unlink()
    assert ts.main([wf_id, "--root", str(repo.root)]) == 0
    assert (project / "models" / "schema.yml").read_text(encoding="utf-8") == ts.render_dbt(repo, wf_id)["models/schema.yml"]
    assert {rel: (project / rel).read_bytes() for rel in before if rel != "models/schema.yml"} == {
        rel: data for rel, data in before.items() if rel != "models/schema.yml"}
    out = capsys.readouterr().out
    assert "wrote" in out and "models/schema.yml" in out


def test_the_cli_writes_each_target_where_the_orchestrator_expects_it(built, tmp_path):
    for wf_id, seg in SQL_SEGMENTS[:1] + SNOWPARK_SEGMENTS:
        repo = built(wf_id)
        name = "proc.py" if (wf_id, seg) in SNOWPARK_SEGMENTS else "proc.sql"
        for leftover in repo.seg(wf_id, seg).glob("proc.*"):
            leftover.unlink()
        assert ts.main([wf_id, "--segment", seg, "--root", str(repo.root)]) == 0
        written = repo.seg(wf_id, seg, name).read_text(encoding="utf-8")
        expected = ts.render_snowpark(repo, wf_id, seg) if name == "proc.py" else ts.render_sql(repo, wf_id, seg)
        assert written == expected
        assert TODO_MARKER in written
        assert sorted(p.name for p in repo.seg(wf_id, seg).glob("proc.*")) == [name], "one file, the source of truth"
    wf_id = DBT_WORKFLOWS[0]
    repo = built(wf_id)
    _manifest(repo, wf_id, output_kind="dbt")
    shutil.rmtree(repo.wf(wf_id, "dbt"))
    assert ts.main([wf_id, "--root", str(repo.root)]) == 0
    project = repo.wf(wf_id, "dbt")
    assert {p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file()} == set(ts.render_dbt(repo, wf_id))


def test_usage_errors_exit_2_and_write_nothing(built, tmp_path, capsys):
    wf_id, seg = SQL_SEGMENTS[0]
    repo = built(wf_id)
    assert ts.main([wf_id, "--segment", "seg_99", "--root", str(repo.root)]) == 2
    assert ts.main(["wf_9999", "--root", str(repo.root)]) == 2                       # no such workflow
    empty = Repo(tmp_path / "empty")
    write_json(empty.wf(wf_id, "segments", "order.json"), [[seg]])
    assert ts.main([wf_id, "--segment", seg, "--root", str(empty.root)]) == 2        # no contract, no DAG
    assert not empty.seg(wf_id, seg, "proc.sql").exists()
    dbt_wf = DBT_WORKFLOWS[0]
    dbt_repo = built(dbt_wf)
    _manifest(dbt_repo, dbt_wf, output_kind="dbt")
    assert ts.main([dbt_wf, "--segment", "seg_01", "--root", str(dbt_repo.root)]) == 2  # a dbt project is one unit
    err = capsys.readouterr().err
    assert "seg_99" in err and "dbt" in err


def test_a_manual_segment_gets_no_skeleton(built, capsys):
    wf_id, seg = SQL_SEGMENTS[0]
    repo = built(wf_id)
    contract = read_json(repo.seg(wf_id, seg, "contract.json"))
    for leftover in repo.seg(wf_id, seg).glob("proc.*"):
        leftover.unlink()
    write_json(repo.seg(wf_id, seg, "contract.json"), {**contract, "target": "manual"})
    try:
        assert ts.main([wf_id, "--segment", seg, "--root", str(repo.root)]) == 0
        assert not list(repo.seg(wf_id, seg).glob("proc.*"))
        assert "manual" in capsys.readouterr().err
    finally:
        write_json(repo.seg(wf_id, seg, "contract.json"), contract)


# --- synthetic shapes the samples do not cover -------------------------------------------------------

WF = "wf_0200"


def _field(name, alteryx_type="V_String", size=20):
    return {"name": name, "type": alteryx_type, "size": size, "scale": None}


def _node(tool_id, node_type, meta, config=None, annotation=None, out_anchors=None, **extra):
    return {"tool_id": tool_id, "type": node_type, "config": config or {}, "meta": meta, "annotation": annotation,
            "in_anchors": ["Input"], "out_anchors": out_anchors or list(meta), **extra}


def _edge(src, anchor, dst, dst_anchor="Input", order=1):
    return {"src": src, "src_anchor": anchor, "dst": dst, "dst_anchor": dst_anchor, "dst_order": order}


ID, NAME, AMOUNT = _field("ID", "Int64", 8), _field("NAME"), _field("AMOUNT", "Double", 8)


def synthetic(root: Path, *, target: str = "sql") -> Repo:
    """One segment: source 1 -> macro 2 (inner: macro_input 1 -> filter 3 (T) -> macro_output 5, plus an
    interface 9) -> union 4 (#1 the macro's output, #2 source 1 again) -> four Output tools, one per
    write mode: 5 overwrite, 6 append, 7 truncate_append (via the tool's own mode), 8 merge on ID with a
    PreSQL and a PostSQL. Annotations carry `$$`, Jinja and a newline."""
    repo = Repo(root)
    copy_pristine_mappings_and_catalog(root)
    inner = {"nodes": [
        _node("1", "macro_input", {"Output": [ID, NAME, AMOUNT]}, annotation="rows in"),
        _node("3", "filter", {"T": [ID, NAME, AMOUNT], "F": [ID, NAME, AMOUNT]}, annotation="keep {{ env_var('X') }}"),
        _node("5", "macro_output", {"Output": [ID, NAME, AMOUNT]}),
        _node("9", "interface", {}),
    ], "edges": [_edge("1", "Output", "3"), _edge("3", "T", "5")]}
    nodes = [
        _node("1", "input", {"Output": [ID, NAME, AMOUNT]}, annotation="the $$ source\nsecond line"),
        _node("2", "macro", {"Output5": [ID, NAME, AMOUNT]}, annotation="a macro", sub_dag=inner,
              out_anchors=["Output5"]),
        _node("4", "union", {"Output": [ID, NAME, AMOUNT]}),
        _node("5", "output", {"Output": [ID, NAME, AMOUNT]}, {"write_mode": "overwrite"}),
        _node("6", "output", {"Output": [ID, NAME, AMOUNT]}, {"write_mode": "append"}),
        _node("7", "output", {"Output": [ID, NAME, AMOUNT]}, {"write_mode": "truncate_append"}),
        _node("8", "output", {"Output": [ID, NAME, AMOUNT]},
              {"write_mode": "update_insert", "pre_sql": "DELETE FROM dbo.O8 WHERE 1 = 0 $$",
               "post_sql": "UPDATE dbo.O8 SET NAME = NAME"}),
    ]
    edges = [_edge("1", "Output", "2", "Input1"), _edge("2", "Output5", "4", "Input", 1),
             _edge("1", "Output", "4", "Input", 2), _edge("4", "Output", "5"), _edge("4", "Output", "6"),
             _edge("4", "Output", "7"), _edge("4", "Output", "8")]
    write_json(repo.wf(WF, "parsed", "dag.json"), {"workflow": WF, "nodes": nodes, "edges": edges})
    write_json(repo.seg(WF, "seg_01", "dag.json"), {"workflow": WF, "segment": "seg_01", "nodes": nodes,
                                                     "edges": edges, "inbound": [], "outbound": []})
    write_json(repo.wf(WF, "segments", "order.json"), [["seg_01"]])
    write_json(repo.wf(WF, "segments", "targets.json"), {"output_kind": "procedures", "segments": {"seg_01": target}})
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), {
        "sources": {"a.yxdb": {"snowflake": "DB.RAW.A", "logical": "A", "tool_ids": ["1"]}},
        "outputs": {f"o{t}": {"snowflake": f"DB.OUT.O{t}", "logical": f"O{t}", "mode": mode, "keys": keys,
                              "tool_ids": [t]}
                    for t, mode, keys in (("5", "overwrite", []), ("6", "append", []),
                                          ("7", None, []), ("8", "merge", ["ID"]))},
    })
    mappings = yaml.safe_load(repo.wf(WF, "intake", "mappings.yaml").read_text(encoding="utf-8"))
    del mappings["outputs"]["o7"]["mode"]           # tool 7's mode comes from the Output tool itself
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), mappings)
    import contract_scaffold
    contract = contract_scaffold.derive(repo, WF).segments["seg_01"].contract
    contract.update({"target": target, "row_relation": "1:1", "ordering": {"keys": [], "alteryx_deterministic": True,
                                                                           "order_dependent_columns": []},
                     "tolerances": {}, "parity_risks": []})
    for output in contract["outputs"]:
        if output.get("logical") == "O8":
            output["keys"] = ["ID"]
    contract["output"] = contract["outputs"][0]
    write_json(repo.seg(WF, "seg_01", "contract.json"), contract)
    return repo


def test_the_synthetic_sql_skeleton_has_every_write_form_and_a_filled_one_passes_every_c4_rule(tmp_path):
    repo = synthetic(tmp_path)
    skeleton = ts.render_sql(repo, WF, "seg_01")
    statements = [collapse(s) for s in parse_proc(skeleton).statements]
    heads = [s.split(" WITH")[0].split("(…)")[0] for s in statements]
    assert heads[0].startswith("CREATE OR REPLACE TABLE IDENTIFIER(:O5_TGT)AS")
    assert heads[1].startswith("INSERT INTO IDENTIFIER(:O6_TGT)(ID,NAME,AMOUNT)")
    assert statements[2] == "TRUNCATE TABLE IDENTIFIER(:O7_TGT)"
    assert heads[3].startswith("INSERT INTO IDENTIFIER(:O7_TGT)(ID,NAME,AMOUNT)")
    assert statements[4] == TODO_MARKER                                   # tool 8's PreSQL
    assert statements[5].startswith("MERGE INTO IDENTIFIER(:O8_TGT)AS T USING(WITH")
    assert statements[5].endswith("ON T.ID = S.ID WHEN MATCHED THEN UPDATE SET NAME = S.NAME,AMOUNT = S.AMOUNT "
                                  "WHEN NOT MATCHED THEN INSERT(ID,NAME,AMOUNT)VALUES(S.ID,S.NAME,S.AMOUNT)")
    assert statements[6] == TODO_MARKER                                   # tool 8's PostSQL
    # the macro is inlined one CTE per inner data tool; its Output anchor resolves to the inner filter's T
    assert [n for n, _, _ in _cte_spans(parse_proc(skeleton).statements[0])] == [
        "t1_input", "t2_macro_m1_macro_input", "t2_macro_m3_filter_t", "t4_union"]
    assert "reads t2_macro_m3_filter_t (Input #1), t1_input (Input #2)" in skeleton
    # nothing a workflow wrote can end the $$ body, open Jinja or break a comment line
    body = skeleton.split("$$")[1]
    assert "{{" not in skeleton and "second line" in skeleton and "\nsecond line" not in skeleton
    assert len(skeleton.split("$$")) == 3, body
    # every mechanical rule passes once the stubs are filled
    filled = _fill_sql(skeleton)
    proc = parse_proc(filled)
    contract = read_json(repo.seg(WF, "seg_01", "contract.json"))
    targets = compile_check.target_writes(contract, compile_check._mappings(repo, WF), compile_check._dag_nodes(repo, WF, "seg_01"))
    assert compile_check._signature_errors(proc, filled, WF, "seg_01") == []
    assert compile_check.table_reference_errors(proc) == []
    assert compile_check.identifier_role_errors(proc) == []
    assert compile_check.return_form_errors(proc) == []
    assert compile_check.write_mode_errors(proc, targets) == []
    repo.seg(WF, "seg_01", "proc.sql").write_text(filled, encoding="utf-8")
    assert compile_check.compile_check(repo, WF, "seg_01")["status"] == "OK"


def _fill_sql(skeleton: str) -> str:
    """Each CTE stub filled with `SELECT <its yield columns> FROM <its first read>`, each slot with a
    harmless statement on the adjacent target."""
    out = []
    lines = skeleton.splitlines()
    for index, line in enumerate(lines):
        if TODO_MARKER not in line:
            out.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip())]
        comment = "\n".join(lines[max(0, index - 4):index])
        if line.strip() == f"{TODO_MARKER};":
            target = re.findall(r"IDENTIFIER\(:([A-Z0-9_]+_TGT)\)", comment)[-1]
            out.append(f"{indent}UPDATE IDENTIFIER(:{target}) SET ID = ID;")
            continue
        read = re.findall(r"reads ([^;(,\s]+(?:\(:[A-Z0-9_]+\))?)", comment)[-1]
        columns = re.findall(r"yields ([A-Z0-9_, ]+)", comment)[-1]
        out.append(f"{indent}SELECT {columns} FROM {read}")
    return "\n".join(out) + "\n"


def test_the_synthetic_snowpark_skeleton_passes_the_rules_once_filled(tmp_path):
    repo = synthetic(tmp_path, target="snowpark")
    skeleton = ts.render_snowpark(repo, WF, "seg_01")
    contract = read_json(repo.seg(WF, "seg_01", "contract.json"))
    assert 'src_a = session.table(f"{src_db}.{src_schema}.A")' in skeleton
    assert 'out_4_output.write.mode("overwrite").save_as_table(f"{tgt_db}.{tgt_schema}.O5")' in skeleton
    assert 'out_4_output.write.mode("append").save_as_table(f"{tgt_db}.{tgt_schema}.O6")' in skeleton
    assert 'out_4_output.write.mode("truncate").save_as_table(f"{tgt_db}.{tgt_schema}.O7")' in skeleton
    assert 'tgt_o8 = session.table(f"{tgt_db}.{tgt_schema}.O8")' in skeleton
    errors = snowpark_rules.segment_rule_errors(repo, WF, "seg_01", contract, _fill_snowpark(skeleton))
    assert errors == []
    assert "$$" not in skeleton and "{{" not in skeleton
    render_snowpark.render(skeleton, WF, "seg_01", "3.11")                  # renderable as it stands


def test_the_synthetic_dbt_skeleton_carries_the_hooks_and_every_config(tmp_path):
    repo = synthetic(tmp_path)
    files = ts.render_dbt(repo, WF)
    configs = {rel: text.splitlines()[0] for rel, text in files.items() if rel.endswith(".sql")}
    assert configs == {
        "models/o5.sql": "{{ config(materialized='table', alias='O5') }}",
        "models/o6.sql": "{{ config(materialized='incremental', incremental_strategy='append', alias='O6') }}",
        "models/o7.sql": "{{ config(" + TODO_MARKER + ") }}",
        "models/o8.sql": "{{ config(materialized='incremental', incremental_strategy='merge', unique_key=['ID'], "
                         f'alias=\'O8\', pre_hook="{TODO_MARKER}", post_hook="{TODO_MARKER}") }}}}',  # N2: double quotes
    }
    sources = yaml.safe_load(files["models/sources.yml"])
    assert sources["sources"][0]["tables"] == [{"name": "A", "columns": [{"name": "ID"}, {"name": "NAME"}, {"name": "AMOUNT"}]}]
    assert "src_a AS (" in files["models/o5.sql"] and "{{ source('src', 'A') }}" in files["models/o5.sql"]
    for rel, text in files.items():
        if rel.startswith("models/"):                                    # no workflow text reaches Jinja
            assert not [span for span in re.findall(r"\{[{%#](.*?)[}%#]\}", text, re.S) if "env_var" in span], rel
        assert TODO_MARKER not in text or rel.endswith(".sql"), rel


def test_the_marker_is_the_one_the_orchestrator_knows():
    stages = (ROOT / "orchestrator" / "stages.ts").read_text(encoding="utf-8")
    assert f'SCAFFOLD_TODO = "{TODO_MARKER}"' in stages


def test_todo_errors_name_the_line_and_the_tool():
    text = "-- tool 3: filter\nt3 AS (\n  TODO(scaffold)\n)\n# tool 2/4: x\nTODO(scaffold)\n"
    assert todo_errors(text, "proc.sql") == [
        "scaffold:todo: proc.sql line 3 (tool 3) still holds the orchestrator's TODO(scaffold) marker; replace it "
        "with the transformation and keep every mechanical line around it as it is",
        "scaffold:todo: proc.sql line 6 (tool 2/4) still holds the orchestrator's TODO(scaffold) marker; replace "
        "it with the transformation and keep every mechanical line around it as it is",
    ]


def test_the_translator_and_the_fixer_are_told_about_the_skeleton_and_the_golden_data():
    """R2/R3: the agent files say the orchestrator wrote the skeleton, what to replace, what never to
    change, that compile_check refuses a remaining marker -- and not to read the golden data in bulk."""
    translator = " ".join((ROOT / ".github" / "agents" / "translator.agent.md").read_text(encoding="utf-8").split())
    for phrase in ("The orchestrator writes the skeleton first", "scripts/translation_scaffold.py", TODO_MARKER,
                   "not the header, the `LET` lines, the write statements or the file layout", "scaffold:todo",
                   "The orchestrator writes the project's skeleton first",
                   "not the file layout, the YAML files or the config lines",
                   "Keep the signature, the reads, the writes and the return as they are",
                   "read at most one golden set's inputs", "the validator compares the rest"):
        assert phrase in translator, phrase
    fixer = " ".join((ROOT / ".github" / "agents" / "fixer.agent.md").read_text(encoding="utf-8").split())
    for phrase in ("scripts/translation_scaffold.py", "Keep them as the skeleton wrote them",
                   "restore the form the error names", "scaffold:todo", TODO_MARKER,
                   "do not read the golden data in bulk"):
        assert phrase in fixer, phrase


def test_a_target_whose_write_mode_is_unknown_costs_only_its_own_statement(tmp_path, capsys):
    """Neither the contract nor the mappings name tool 6's write mode: its statement is a TODO with the
    reason in its comment (and on stderr), every other target keeps its mechanical write."""
    repo = synthetic(tmp_path)
    contract = read_json(repo.seg(WF, "seg_01", "contract.json"))
    for output in contract["outputs"]:
        if output.get("logical") == "O6":
            del output["write_mode"]
    write_json(repo.seg(WF, "seg_01", "contract.json"), contract)
    mappings = yaml.safe_load(repo.wf(WF, "intake", "mappings.yaml").read_text(encoding="utf-8"))
    del mappings["outputs"]["o6"]["mode"]
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), mappings)
    skeleton = ts.render_sql(repo, WF, "seg_01")
    assert "LET O6_TGT" not in skeleton
    assert "-- tool 6: output -- its write cannot be derived: c4:write_mode: target O6 has no write_mode" in skeleton
    assert "CREATE OR REPLACE TABLE IDENTIFIER(:O5_TGT) AS" in skeleton and "MERGE INTO IDENTIFIER(:O8_TGT) AS T" in skeleton
    assert ts.main([WF, "--segment", "seg_01", "--root", str(repo.root)]) == 0
    assert "note: seg_01: c4:write_mode: target O6 has no write_mode" in capsys.readouterr().err


def _pass_through(root: Path) -> Repo:
    """seg_01: source 1 -> seg_02; seg_02: only Output tool 2 (overwrite), fed straight by seg_01's stream."""
    repo = Repo(root)
    copy_pristine_mappings_and_catalog(root)
    wf = "wf_0300"
    nodes = [_node("1", "input", {"Output": [ID, NAME]}), _node("2", "output", {"Output": [ID, NAME]}, {"write_mode": "overwrite"})]
    edge = {**_edge("1", "Output", "2"), "wireless": False}
    write_json(repo.wf(wf, "parsed", "dag.json"), {"workflow": wf, "nodes": nodes, "edges": [edge]})
    write_json(repo.seg(wf, "seg_01", "dag.json"), {"workflow": wf, "segment": "seg_01", "nodes": nodes[:1], "edges": [],
                                                     "inbound": [], "outbound": [{**edge, "to_segment": "seg_02"}]})
    write_json(repo.seg(wf, "seg_02", "dag.json"), {"workflow": wf, "segment": "seg_02", "nodes": nodes[1:], "edges": [],
                                                     "inbound": [{**edge, "from_segment": "seg_01"}], "outbound": []})
    write_json(repo.wf(wf, "segments", "order.json"), [["seg_01"], ["seg_02"]])
    write_yaml(repo.wf(wf, "intake", "mappings.yaml"), {
        "sources": {"a": {"snowflake": "DB.RAW.A", "logical": "A", "tool_ids": ["1"]}},
        "outputs": {"o": {"snowflake": "DB.OUT.O2", "logical": "O2", "mode": "overwrite", "keys": [], "tool_ids": ["2"]}}})
    import contract_scaffold
    for seg, scaffold in contract_scaffold.derive(repo, wf).segments.items():
        write_json(repo.seg(wf, seg, "contract.json"), {**scaffold.contract, "target": "sql", "row_relation": "1:1",
                                                        "ordering": {"keys": [], "alteryx_deterministic": True,
                                                                     "order_dependent_columns": []},
                                                        "tolerances": {}, "parity_risks": []})
    return repo


def test_a_segment_with_nothing_to_translate_compiles_as_scaffolded(tmp_path):
    repo = _pass_through(tmp_path)
    skeleton = ts.render_sql(repo, "wf_0300", "seg_02")
    assert TODO_MARKER not in skeleton
    assert "CREATE OR REPLACE TABLE IDENTIFIER(:O2_TGT) AS\n  SELECT\n      ID,\n      NAME\n  FROM MIG_WORK.WF0300_SEG_01_OUT;" in skeleton
    repo.seg("wf_0300", "seg_02", "proc.sql").write_text(skeleton, encoding="utf-8")
    assert compile_check.compile_check(repo, "wf_0300", "seg_02")["status"] == "OK"
    module = ts.render_snowpark(repo, "wf_0300", "seg_02")
    assert "    out_1_output = in_1_output\n" in module and TODO_MARKER not in module
    contract = read_json(repo.seg("wf_0300", "seg_02", "contract.json"))
    assert snowpark_rules.segment_rule_errors(repo, "wf_0300", "seg_02", {**contract, "target": "snowpark"}, module) == []
    model = ts.render_dbt(repo, "wf_0300")["models/o2.sql"]
    assert "FROM {{ ref('wf0300_seg_01_out') }}" in model and model.rstrip().endswith("FROM ref_wf0300_seg_01_out")


# --- fix round 1 (review-L8-report.md) -----------------------------------------------------------------


def _build(root: Path, wf: str, nodes: list, edges: list, segs: dict, mappings: dict, targets: dict,
           keys: dict | None = None, output_kind: str = "procedures") -> Repo:
    """A synthetic workflow: `segs` maps a segment to (its tool ids, inbound edges, outbound edges); the
    internal edges and the contracts (mechanical fields from contract_scaffold, neutral judgment) follow."""
    import contract_scaffold
    repo = Repo(root)
    copy_pristine_mappings_and_catalog(root)
    by_id = {n["tool_id"]: n for n in nodes}
    write_json(repo.wf(wf, "parsed", "dag.json"), {"workflow": wf, "nodes": nodes, "edges": edges})
    for seg, (ids, inbound, outbound) in segs.items():
        internal = [e for e in edges if e["src"] in ids and e["dst"] in ids]
        write_json(repo.seg(wf, seg, "dag.json"), {"workflow": wf, "segment": seg, "nodes": [by_id[i] for i in ids],
                                                    "edges": internal, "inbound": inbound, "outbound": outbound})
    write_json(repo.wf(wf, "segments", "order.json"), [[seg] for seg in segs])
    write_json(repo.wf(wf, "segments", "targets.json"), {"output_kind": output_kind, "segments": targets})
    write_yaml(repo.wf(wf, "intake", "mappings.yaml"), mappings)
    write_json(repo.wf(wf, "manifest.json"), {"id": wf, "status": {}, "metrics": {}, "output_kind": output_kind})
    for seg, scaffold in contract_scaffold.derive(repo, wf).segments.items():
        contract = {**scaffold.contract, "target": targets[seg], "row_relation": "1:1",
                    "ordering": {"keys": [], "alteryx_deterministic": True, "order_dependent_columns": []},
                    "tolerances": {}, "parity_risks": []}
        for output in contract["outputs"]:
            if keys and output.get("logical") in keys:
                output["keys"] = keys[output["logical"]]
        contract["output"] = contract["outputs"][0]
        write_json(repo.seg(wf, seg, "contract.json"), contract)
    return repo


_READS = re.compile(r"reads (IDENTIFIER\(:[A-Z0-9_]+\)|[A-Za-z0-9_.]+)")


def _fill_star(text: str, slot: str = "DELETE FROM IDENTIFIER(:{t}) WHERE 1 = 0;") -> str:
    """Every stub body `SELECT * FROM <what it reads>`, every PreSQL/PostSQL slot a harmless statement
    on its target, every hook a harmless statement on `{{ this }}`: the mechanical lines untouched."""
    text = text.replace(f'pre_hook="{TODO_MARKER}"', 'pre_hook="DELETE FROM {{ this }} WHERE 1 = 0"')
    text = text.replace(f'post_hook="{TODO_MARKER}"', 'post_hook="DELETE FROM {{ this }} WHERE 1 = 0"')
    out, lines = [], text.splitlines()
    for index, line in enumerate(lines):
        if TODO_MARKER not in line:
            out.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip())]
        comment = "\n".join(lines[max(0, index - 4):index])
        if line.strip() == f"{TODO_MARKER};":
            out.append(indent + slot.format(t=re.findall(r"IDENTIFIER\(:([A-Z0-9_]+_TGT)\)", comment)[-1]))
        else:
            out.append(f"{indent}SELECT * FROM {_READS.findall(comment)[-1]}")
    return "\n".join(out) + "\n"


RESERVED = [_field("ID", "Int64", 8), _field("Order"), _field("Group"), _field("On"), _field("No")]


def _reserved(root: Path, target: str = "sql", output_kind: str = "procedures") -> Repo:
    """Source 1 -> formula 2 -> Output 3 (append) and Output 4 (merge on ORDER, GROUP, with a PreSQL and a
    PostSQL): every column but ID is a reserved word in Snowflake or DuckDB (ORDER, GROUP, ON) or a YAML
    boolean (ON, NO)."""
    nodes = [_node("1", "input", {"Output": RESERVED}), _node("2", "formula", {"Output": RESERVED}),
             _node("3", "output", {"Output": RESERVED}, {"write_mode": "append"}),
             _node("4", "output", {"Output": RESERVED}, {"write_mode": "update_insert",
                                                         "pre_sql": "DELETE FROM o4 WHERE 1 = 0",
                                                         "post_sql": "UPDATE o4 SET ID = ID"})]
    edges = [_edge("1", "Output", "2"), _edge("2", "Output", "3"), _edge("2", "Output", "4")]
    mappings = {"sources": {"a": {"snowflake": "D.S.A", "logical": "A", "tool_ids": ["1"]}},
                "outputs": {"o3": {"snowflake": "D.S.O3", "logical": "O3", "mode": "append", "keys": [], "tool_ids": ["3"]},
                            "o4": {"snowflake": "D.S.O4", "logical": "O4", "mode": "merge", "keys": ["ORDER", "GROUP"],
                                   "tool_ids": ["4"]}}}
    return _build(root, "wf_0400", nodes, edges, {"seg_01": (["1", "2", "3", "4"], [], [])}, mappings,
                  {"seg_01": target}, keys={"O4": ["ORDER", "GROUP"]}, output_kind=output_kind)


def test_i2_reserved_word_columns_are_quoted_in_every_mechanical_sql_line_and_the_filled_procedure_compiles(tmp_path):
    repo = _reserved(tmp_path)
    skeleton = ts.render_sql(repo, "wf_0400", "seg_01")
    assert '(ID, "ORDER", "GROUP", "ON", NO)' in skeleton                     # the INSERT and MERGE column lists
    assert 'ON T."ORDER" = S."ORDER" AND T."GROUP" = S."GROUP"' in skeleton
    assert '      "ON" = S."ON",' in skeleton and '      "ORDER",' in skeleton
    filled = _fill_star(skeleton)
    repo.seg("wf_0400", "seg_01", "proc.sql").write_text(filled, encoding="utf-8")
    report = compile_check.compile_check(repo, "wf_0400", "seg_01")
    assert report["status"] == "OK", report["errors"]


def test_i2_reserved_word_columns_pass_the_snowpark_rules(tmp_path):
    """Snowpark quotes every column name it is given (`quote_name`), so a reserved word is safe as a plain
    name; the literal is JSON-escaped, and the merge's join is on the contract's keys."""
    repo = _reserved(tmp_path, target="snowpark")
    skeleton = ts.render_snowpark(repo, "wf_0400", "seg_01")
    assert '(tgt_o4["ORDER"] == out_2_output["ORDER"]) & (tgt_o4["GROUP"] == out_2_output["GROUP"])' in skeleton
    contract = read_json(repo.seg("wf_0400", "seg_01", "contract.json"))
    assert snowpark_rules.segment_rule_errors(repo, "wf_0400", "seg_01", contract, _fill_snowpark(skeleton)) == []


def test_i2_reserved_words_and_yaml_booleans_are_quoted_in_the_dbt_project_and_it_compiles(tmp_path):
    repo = _reserved(tmp_path, output_kind="dbt")
    files = ts.render_dbt(repo, "wf_0400")
    schema = yaml.safe_load(files["models/schema.yml"])
    assert [c["name"] for c in schema["models"][0]["columns"]] == ["ID", "ORDER", "GROUP", "ON", "NO"]
    sources = yaml.safe_load(files["models/sources.yml"])
    assert [c["name"] for c in sources["sources"][0]["tables"][0]["columns"]] == ["ID", "ORDER", "GROUP", "ON", "NO"]
    assert "unique_key=['\"ORDER\"', '\"GROUP\"']" in files["models/o4.sql"].splitlines()[0]
    project = repo.wf("wf_0400", "dbt")
    for rel, text in files.items():
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text(_fill_star(text) if rel.endswith(".sql") else text, encoding="utf-8", newline="\n")
    report = compile_check.compile_check(repo, "wf_0400", None, target="dbt")
    assert report["status"] == "OK", report["errors"]


@pytest.mark.parametrize("name", ["NO", "ON", "YES", "OFF", "TRUE", "NULL", "1E3", "0X1F", "~", "Y"])
def test_i2_a_yaml_name_is_quoted_whenever_yaml_would_read_it_as_something_else(name):
    assert yaml.safe_load(f"- name: {ts._yaml_name(name)}") == [{"name": name}]


def test_i1_a_segment_with_nothing_to_translate_writes_a_marker_free_skeleton(tmp_path):
    """The orchestrator side (orchestrator/test/scaffolds.test.ts) keeps such a skeleton as the
    translation; here: it has no marker at all, in either procedure target."""
    repo = _pass_through(tmp_path)
    assert TODO_MARKER not in ts.render_sql(repo, "wf_0300", "seg_02")
    assert TODO_MARKER not in ts.render_snowpark(repo, "wf_0300", "seg_02")


@pytest.mark.parametrize("spelling", ["todo(scaffold)", "TODO (scaffold)", "Todo( Scaffold )"])
def test_m2_a_respelled_marker_is_still_refused(spelling):
    assert todo_errors(f"-- tool 3: x\n  {spelling}\n", "proc.sql") != []


def test_m3_a_presql_or_postsql_slot_deleted_outright_no_longer_compiles(tmp_path):
    repo = _reserved(tmp_path)
    skeleton = ts.render_sql(repo, "wf_0400", "seg_01")
    no_slots = "\n".join(line for line in skeleton.splitlines() if line.strip() != f"{TODO_MARKER};") + "\n"
    repo.seg("wf_0400", "seg_01", "proc.sql").write_text(_fill_star(no_slots), encoding="utf-8")
    errors = [e for e in compile_check.compile_check(repo, "wf_0400", "seg_01")["errors"] if e.startswith("c4:write_mode")]
    assert len(errors) == 1, errors
    assert "tool 4 has a PreSQL" in errors[0] and "tool 4 has a PostSQL" in errors[0], errors


def test_m4_the_dbt_project_file_is_written_last(tmp_path, capsys):
    repo = _reserved(tmp_path, output_kind="dbt")
    assert list(ts.render_dbt(repo, "wf_0400"))[-1] == "dbt_project.yml"
    assert ts.main(["wf_0400", "--dbt", "--root", str(repo.root)]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "wrote workflows/wf_0400/dbt/dbt_project.yml"


def test_m5_workflow_text_reaches_code_only_where_it_is_safe(tmp_path, capsys):
    """A column name that holds `$$` cannot be written inside a `$$` body, one that holds Jinja braces not in
    a dbt model: that name is a TODO (with a note), never the raw text. A `"` or a backslash in a name is
    escaped in a Python literal."""
    cols = [_field("ID", "Int64", 8), _field("A$$B"), _field("C{{D"), _field('E"F\\G')]
    nodes = [_node("1", "input", {"Output": cols}), _node("2", "output", {"Output": cols}, {"write_mode": "update_insert"})]
    mappings = {"sources": {"a": {"snowflake": "D.S.A", "logical": "A", "tool_ids": ["1"]}},
                "outputs": {"o2": {"snowflake": "D.S.O2", "logical": "O2", "mode": "merge", "keys": ['E"F\\G'],
                                   "tool_ids": ["2"]}}}
    repo = _build(tmp_path, "wf_0401", nodes, [_edge("1", "Output", "2")], {"seg_01": (["1", "2"], [], [])},
                  mappings, {"seg_01": "sql"}, keys={"O2": ['E"F\\G']})
    skeleton = ts.render_sql(repo, "wf_0401", "seg_01")
    assert skeleton.count("$$") == 2 and "A$$B" not in skeleton
    module = ts.render_snowpark(repo, "wf_0401", "seg_01")
    ast.parse(module)
    assert "A$$B" not in module and '"E\\"F\\\\G"' in module
    model = ts.render_dbt(repo, "wf_0401")["models/o2.sql"]
    assert "C{{D" not in model and "{{" not in model.replace("{{ config(", "").replace("{{ source(", "")
    assert ts.main(["wf_0401", "--segment", "seg_01", "--root", str(repo.root)]) == 0
    assert "A$$B" not in capsys.readouterr().err                                # the note never quotes it raw


def test_m6_every_stub_of_a_multi_statement_procedure_is_addressable(built):
    """The same tool's stub in two statements differs by its comment (the output it is written for), so an
    edit that quotes the comment and the stub is unique in the file."""
    repo = built("wf_0001")
    skeleton = ts.render_sql(repo, "wf_0001", "seg_01")
    tool_lines = [line.strip() for line in skeleton.splitlines() if line.strip().startswith("-- tool ")]
    assert len(tool_lines) == len(set(tool_lines)), tool_lines
    assert "-- tool 1: input Orders extract -- for SALES_SUMMARY" in skeleton


def test_n1_n2_one_write_mode_spelling_and_double_quoted_hook_placeholders(tmp_path):
    repo = _reserved(tmp_path, output_kind="dbt")
    model = ts.render_dbt(repo, "wf_0400")["models/o4.sql"]
    assert f'pre_hook="{TODO_MARKER}", post_hook="{TODO_MARKER}"' in model.splitlines()[0]
    assert "(tool 4, update_insert)" in model and "write mode update_insert" in model
    assert "write mode merge" not in model


def test_n3_the_project_and_one_segment_are_asked_for_explicitly(tmp_path, capsys):
    repo = _reserved(tmp_path)                                               # manifest says procedures
    assert ts.main(["wf_0400", "--dbt", "--root", str(repo.root)]) == 0       # --dbt: the project, whatever it says
    assert repo.wf("wf_0400", "dbt", "dbt_project.yml").is_file()
    assert ts.main(["wf_0400", "--dbt", "--segment", "seg_01", "--root", str(repo.root)]) == 2
    assert "one or the other" in capsys.readouterr().err


@pytest.mark.parametrize("keys, accepted", [
    (['"ORDER"', "ID"], True), (['"CUSTOMER ID"'], True), (["ORDER"], True),
    (['"A"; DROP TABLE X; --"'], False), (['"A{{ env_var(1) }}"'], False), (['"A""B"'], False), (['"'], False),
])
def test_i2_the_closed_surface_takes_a_quoted_unique_key_and_nothing_that_could_break_out(keys, accepted):
    """A reserved-word key is written double-quoted (dbt splices it into the merge as written; a local
    dbt-duckdb run merged on '"ORDER"' cleanly); a quoted key can hold only letters, digits, _, $ and
    spaces, so it can neither end its quotes nor open Jinja."""
    from lib import dbt_surface
    assert (dbt_surface._config_value_problem("unique_key", keys) is None) is accepted

