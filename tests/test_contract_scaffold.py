"""contract_scaffold.py: every mechanical field of every contract, derived by code (Task L3, R1/R3).

A live analyzer run (the fourth live test: a local model through the real SDK) got mechanical
contract fields wrong -- an invented stream name, no input `tool_id`, no `write_mode`, a non-null
`table` for a target, a fixed-width string without its size, no `workflow` -- every one of them
derivable from the parsed DAG, the segment sub-DAGs, `segments/order.json`, `segments/targets.json`
and `intake/mappings.yaml`. The scaffold derives them; the analyzer keeps only judgment. (Its unsized
`VARCHAR` for a variable-length string is accepted: the first documented difference below.)

The acceptance test is the committed reference: for every segment of every sample, the scaffold's
mechanical fields equal the canned contract's -- with exactly two documented differences, each
pinned below with its reason (never a canned contract edited to fit):

* `wf_0007` spells a `V_String` column as unsized `VARCHAR` (its analysis.md: types from
  `types_map.alteryx_to_snowflake`, which leaves V_String unsized by a recorded ruling), where the
  other six samples -- and the scaffold -- write `VARCHAR(<DAG size>)`. Both spellings are
  accepted for a variable-length string; any other type is not.
* `wf_0003/seg_02`'s `update_insert` target lists `LOADED_FLAG` after the stream's columns: a column
  the EXISTING target table already has (`golden_inputs/targets_before`), which no DAG shows. A
  target that keeps existing rows (append, update_insert) may carry such target-only columns after
  the stream's own; an overwrite target may not.

Nothing here has run on Snowflake or Alteryx: every file is built locally from `samples/`.
"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

import contract_scaffold as cs
import target_check
from lib.io import read_json, write_json, write_yaml
from lib.paths import Repo
from tests.helpers import prepare_workflow

ROOT = Path(__file__).parents[1]
SAMPLES = ROOT / "samples"
TRANSLATED = sorted(p.parents[1].name for p in SAMPLES.glob("wf_*/canned/segments"))

#: The mechanical keys of each entry kind, exactly as R1 lists them (plus `kind`, which names the kind).
MECHANICAL_INPUT_MAPPED = ("logical", "tool_id", "columns")
MECHANICAL_INPUT_STREAM = ("from", "stream", "table", "columns")
MECHANICAL_TARGET = ("stream", "kind", "tool_id", "logical", "table", "write_mode", "columns")
MECHANICAL_WORK = ("stream", "kind", "table", "logical", "columns")

#: Documented reason 1 (module docstring): the one workflow whose canned contracts spell a
#: V_String column unsized.
UNSIZED_VSTRING_WORKFLOWS = {"wf_0007"}
#: Documented reason 2: (workflow, segment, output index) -> the target-only columns its canned
#: contract lists after the stream's columns.
TARGET_ONLY_COLUMNS = {("wf_0003", "seg_02", 0): ["LOADED_FLAG"]}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """`built(wf_id)` -> a Repo holding that sample parsed, segmented, intake READY from its own
    recorded answers, `targets.json` proposed and the canned contracts copied into place."""
    repos: dict[str, Repo] = {}

    def build(wf_id: str) -> Repo:
        if wf_id not in repos:
            root = tmp_path_factory.mktemp(wf_id)
            repo = prepare_workflow(root, wf_id)
            target_check.target_check(repo, wf_id, "procedures")
            repos[wf_id] = repo
        return repos[wf_id]

    return build


def _canned(wf_id: str, seg: str) -> dict:
    return json.loads((SAMPLES / wf_id / "canned" / "segments" / seg / "contract.json").read_text(encoding="utf-8"))


def _order(repo: Repo, wf_id: str) -> list[str]:
    return [seg for wave in read_json(repo.wf(wf_id, "segments", "order.json")) for seg in wave]


def _project(entry: dict, keys: tuple[str, ...]) -> dict:
    """An entry's mechanical keys only, each column reduced to its name and type."""
    out = {key: entry[key] for key in keys if key in entry}
    if "columns" in out:
        out["columns"] = [{"name": c["name"], "type": c["type"]} for c in out["columns"]]
    return out


def _mechanical(contract: dict) -> dict:
    def input_keys(entry):
        return MECHANICAL_INPUT_STREAM if entry.get("stream") else MECHANICAL_INPUT_MAPPED

    def output_keys(entry):
        return MECHANICAL_TARGET if entry.get("kind") == "target" else MECHANICAL_WORK

    return {
        "workflow": contract.get("workflow"),
        "segment": contract.get("segment"),
        "target": contract.get("target"),
        "inputs": [_project(entry, input_keys(entry)) for entry in contract.get("inputs") or []],
        "outputs": [_project(entry, output_keys(entry)) for entry in contract.get("outputs") or []],
        "output": _project(contract["output"], output_keys(contract["output"])) if contract.get("output") else None,
    }


def _documented(wf_id: str, seg: str, canned: dict, scaffolded: dict) -> dict:
    """The canned contract's mechanical projection with the two documented differences mapped onto
    the scaffold's spelling -- and nothing else touched."""
    expected = _mechanical(canned)
    if wf_id in UNSIZED_VSTRING_WORKFLOWS:
        for section in ("inputs", "outputs"):
            for mine, theirs in zip(expected[section], scaffolded[section]):
                for column, derived in zip(mine["columns"], theirs["columns"]):
                    if column["type"] == "VARCHAR" and derived["type"].startswith("VARCHAR("):
                        column["type"] = derived["type"]
        expected["output"] = expected["outputs"][0]
    for (wf, segment, index), names in TARGET_ONLY_COLUMNS.items():
        if (wf, segment) == (wf_id, seg):
            columns = expected["outputs"][index]["columns"]
            assert [c["name"] for c in columns[-len(names):]] == names
            del columns[-len(names):]
            if index == 0:
                expected["output"] = expected["outputs"][0]
    return expected


@pytest.mark.parametrize("wf_id", TRANSLATED)
def test_the_scaffold_reproduces_every_canned_contracts_mechanical_fields(wf_id, built):
    repo = built(wf_id)
    derived = cs.derive(repo, wf_id)
    segments = _order(repo, wf_id)
    assert sorted(derived.segments) == sorted(segments)
    for seg in segments:
        scaffolded = derived.segments[seg].contract
        assert _mechanical(scaffolded) == _documented(wf_id, seg, _canned(wf_id, seg), _mechanical(scaffolded)), (
            f"{wf_id}/{seg}: the scaffold's mechanical fields differ from the canned contract's")


@pytest.mark.parametrize("wf_id", TRANSLATED)
def test_re_applying_the_scaffold_changes_no_canned_contract(wf_id, built):
    """The orchestrator re-applies the scaffold over every contract after the analyzer: over the
    committed reference that must be a no-op, or the offline run would rewrite what it replays."""
    repo = built(wf_id)
    derived = cs.derive(repo, wf_id)
    for seg in _order(repo, wf_id):
        canned = _canned(wf_id, seg)
        assert cs.apply(canned, derived.segments[seg]) == canned, f"{wf_id}/{seg}"


@pytest.mark.parametrize("wf_id", TRANSLATED)
def test_the_scaffold_leaves_every_judgment_field_to_the_analyzer(wf_id, built):
    repo = built(wf_id)
    derived = cs.derive(repo, wf_id)
    for seg, scaffold in derived.segments.items():
        contract = scaffold.contract
        for field in ("row_relation", "ordering", "tolerances", "parity_risks"):
            assert field not in contract, f"{wf_id}/{seg}: the scaffold invented {field}"
        assert contract["normalizations"] == []
        for entry in contract["inputs"] + contract["outputs"]:
            assert entry["keys"] == []
            assert "expected_rows" not in entry and "large" not in entry
            assert all(column["nullable"] is True for column in entry["columns"])


def test_the_t3_sample_is_scaffolded_too(tmp_path):
    """wf_0005 has no canned contracts (tier T3): the scaffold still derives its one segment, and
    `prune_unjudged` removes the pre-filled contract no analyzer judged."""
    repo = Repo(tmp_path)
    for rel in ("parsed/dag.json", "intake/mappings.yaml", "segments/order.json", "segments/targets.json",
                "segments/seg_01/dag.json"):
        destination = repo.wf("wf_0005", *rel.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "workflows" / "wf_0005" / rel, destination)
    derived = cs.derive(repo, "wf_0005")
    contract = derived.segments["seg_01"].contract
    assert [(e["logical"], e["tool_id"]) for e in contract["inputs"]] == [("ACCOUNTS", "1")]
    assert [(o["stream"], o["kind"], o["tool_id"], o["write_mode"]) for o in contract["outputs"]] == [
        ("3_Output", "target", "4", "overwrite")]
    assert cs.prefill(repo, "wf_0005") == ["seg_01"]
    assert repo.seg("wf_0005", "seg_01", "contract.json").is_file()
    assert cs.prune_unjudged(repo, "wf_0005") == ["seg_01"]
    assert not repo.seg("wf_0005", "seg_01", "contract.json").exists()


# --- a synthetic workflow: every shape the samples do not show -----------------------------------

WF = "wf_0100"


def _field(name, alteryx_type, size=None, scale=None):
    return {"name": name, "type": alteryx_type, "size": size, "scale": scale}


ID = _field("ID", "Int64", 8)
NAME = _field("Name", "V_String", 30)
CODE = _field("CODE", "String", 4)
AMOUNT = _field("AMOUNT", "FixedDecimal", 19, 2)
SHAPE = _field("SHAPE", "SpatialObj", 100)


def _node(tool_id, node_type, meta, config=None):
    return {"tool_id": tool_id, "type": node_type, "config": config or {}, "meta": meta}


def _edge(src, anchor, dst, dst_anchor="Input"):
    return {"src": src, "src_anchor": anchor, "dst": dst, "dst_anchor": dst_anchor}


def synthetic(tmp_path) -> Repo:
    """seg_01: source 1 -> filter 2; 2.T feeds seg_02 AND Output 3 (append); 2.F feeds seg_02 too.
    seg_02: source 4 (mapped) and both of seg_01's streams -> join 5 -> Output 6 (merge, fed from
    seg_02's own join) and a work stream 5.J into seg_03. seg_03: formula 7 -> Output 8 (overwrite).
    Tool 4's SHAPE column is a SpatialObj, a type the type map does not cover."""
    repo = Repo(tmp_path)
    nodes = [
        _node("1", "input", {"Output": [ID, NAME, CODE, AMOUNT]}),
        _node("2", "filter", {"T": [ID, NAME, CODE, AMOUNT], "F": [ID, NAME, CODE, AMOUNT]}),
        _node("3", "output", {"Output": [ID, NAME, CODE, AMOUNT]}, {"write_mode": "append"}),
        _node("4", "input", {"Output": [ID, SHAPE]}),
        _node("5", "join", {"J": [ID, NAME, SHAPE], "L": [ID], "R": [ID]}),
        _node("6", "output", {"Output": [ID, NAME, SHAPE]}, {"write_mode": "update_insert"}),
        _node("7", "formula", {"Output": [ID, NAME]}),
        _node("8", "output", {"Output": [ID, NAME]}, {"write_mode": "overwrite"}),
    ]
    edges = [_edge("1", "Output", "2"), _edge("2", "T", "3"), _edge("2", "T", "5", "Left"),
             _edge("2", "F", "5", "Right"), _edge("4", "Output", "5", "Right"), _edge("5", "J", "6"),
             _edge("5", "J", "7"), _edge("7", "Output", "8")]
    write_json(repo.wf(WF, "parsed", "dag.json"), {"workflow": WF, "nodes": nodes, "edges": edges})
    members = {"seg_01": ["1", "2", "3"], "seg_02": ["4", "5", "6"], "seg_03": ["7", "8"]}
    where = {tool: seg for seg, tools in members.items() for tool in tools}
    for seg, tools in members.items():
        write_json(repo.seg(WF, seg, "dag.json"), {
            "workflow": WF, "segment": seg,
            "nodes": [n for n in nodes if n["tool_id"] in tools],
            "edges": [e for e in edges if e["src"] in tools and e["dst"] in tools],
            "inbound": [{**e, "from_segment": where[e["src"]]} for e in edges
                        if e["dst"] in tools and e["src"] not in tools],
            "outbound": [{**e, "to_segment": where[e["dst"]]} for e in edges
                         if e["src"] in tools and e["dst"] not in tools],
        })
    write_json(repo.wf(WF, "segments", "order.json"), [["seg_01"], ["seg_02"], ["seg_03"]])
    write_json(repo.wf(WF, "segments", "targets.json"), {
        "output_kind": "procedures", "segments": {"seg_01": "sql", "seg_02": "snowpark", "seg_03": "sql"}})
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), {
        "sources": {"a.yxdb": {"snowflake": "DB.RAW.A", "logical": "A", "tool_ids": ["1"]},
                    "b.yxdb": {"snowflake": "DB.RAW.B", "logical": "B", "tool_ids": ["4"]}},
        "outputs": {"o3.yxdb": {"snowflake": "DB.OUT.O3", "logical": "O3", "mode": "append", "keys": [], "tool_ids": ["3"]},
                    "o6.yxdb": {"snowflake": "DB.OUT.O6", "logical": "O6", "mode": "merge", "keys": ["ID"], "tool_ids": ["6"]},
                    "o8.yxdb": {"snowflake": "DB.OUT.O8", "logical": "O8", "mode": "overwrite", "keys": [], "tool_ids": ["8"]}},
    })
    return repo


def test_work_streams_come_before_targets_and_only_the_first_is_the_primary_table(tmp_path):
    derived = cs.derive(synthetic(tmp_path), WF)
    seg_01 = derived.segments["seg_01"].contract
    assert [(o["stream"], o["kind"], o.get("table")) for o in seg_01["outputs"]] == [
        ("2_T", "work", "MIG_WORK.WF0100_SEG_01_OUT"),
        ("2_F", "work", "MIG_WORK.WF0100_SEG_01_OUT_2_F"),
        ("2_T", "target", None),
    ]
    assert seg_01["output"] == seg_01["outputs"][0]
    target = seg_01["outputs"][2]
    assert (target["tool_id"], target["logical"], target["write_mode"]) == ("3", "O3", "append")
    assert all("tool_id" not in o and "write_mode" not in o and o["logical"] is None for o in seg_01["outputs"][:2])


def test_mapped_sources_come_before_upstream_streams_whose_table_is_the_producers(tmp_path):
    derived = cs.derive(synthetic(tmp_path), WF)
    inputs = derived.segments["seg_02"].contract["inputs"]
    assert [(e.get("logical"), e.get("tool_id"), e.get("from"), e.get("stream"), e.get("table")) for e in inputs] == [
        ("B", "4", None, None, None),
        (None, None, "seg_01", "2_T", "MIG_WORK.WF0100_SEG_01_OUT"),
        (None, None, "seg_01", "2_F", "MIG_WORK.WF0100_SEG_01_OUT_2_F"),
    ]
    assert "table" not in inputs[0] and "from" not in inputs[0] and "stream" not in inputs[0]
    assert "logical" not in inputs[1] and "tool_id" not in inputs[1]


def test_types_follow_the_type_map_with_variable_strings_sized_and_names_upper_cased(tmp_path):
    derived = cs.derive(synthetic(tmp_path), WF)
    columns = derived.segments["seg_01"].contract["inputs"][0]["columns"]
    assert columns == [
        {"name": "ID", "type": "NUMBER(38,0)", "nullable": True},
        {"name": "NAME", "type": "VARCHAR(30)", "nullable": True},
        {"name": "CODE", "type": "VARCHAR(4)", "nullable": True},
        {"name": "AMOUNT", "type": "NUMBER(19,2)", "nullable": True},
    ]


def test_merge_is_written_as_update_insert_and_the_target_reads_the_producing_anchor(tmp_path):
    derived = cs.derive(synthetic(tmp_path), WF)
    seg_02 = derived.segments["seg_02"].contract
    assert [(o["stream"], o["kind"]) for o in seg_02["outputs"]] == [("5_J", "work"), ("5_J", "target")]
    assert seg_02["outputs"][1]["write_mode"] == "update_insert"
    assert seg_02["target"] == "snowpark", "target is copied from targets.json"


def test_a_type_the_map_does_not_cover_is_left_to_the_analyzer_and_reported(tmp_path):
    derived = cs.derive(synthetic(tmp_path), WF)
    source_b = derived.segments["seg_02"].contract["inputs"][0]
    assert source_b["columns"][1] == {"name": "SHAPE", "nullable": True}, "no type is guessed"
    assert any("SHAPE" in note and "SpatialObj" in note for note in derived.notes), derived.notes


def test_prefill_writes_only_the_contracts_that_do_not_exist_yet(tmp_path):
    repo = synthetic(tmp_path)
    kept = repo.seg(WF, "seg_02", "contract.json")
    kept.write_text('{"segment": "seg_02", "row_relation": "filter"}\n', encoding="utf-8")
    assert cs.prefill(repo, WF) == ["seg_01", "seg_03"]
    assert kept.read_text(encoding="utf-8") == '{"segment": "seg_02", "row_relation": "filter"}\n'
    written = read_json(repo.seg(WF, "seg_01", "contract.json"))
    assert written == cs.derive(repo, WF).segments["seg_01"].contract
    assert cs.prefill(repo, WF) == [], "a second run writes nothing"


# --- re-applying: the scaffold is authoritative, judgment is kept -----------------------------------

def _judged(contract: dict) -> dict:
    return {**contract, "row_relation": "filter",
            "ordering": {"keys": [], "alteryx_deterministic": True, "order_dependent_columns": []},
            "tolerances": {}, "parity_risks": [{"tool_id": "2", "class": "LOGIC", "note": "n"}]}


def test_apply_restores_every_mechanical_field_the_live_analyzer_got_wrong(built):
    """The six mistakes of the fourth live run, made on the committed wf_0001
    contract: re-applying the scaffold puts back exactly the canned contract."""
    repo = built("wf_0001")
    canned = _canned("wf_0001", "seg_01")
    wrong = copy.deepcopy(canned)
    del wrong["workflow"]
    del wrong["inputs"][0]["tool_id"]
    wrong["inputs"][0]["table"] = "SALES.RAW.ORDERS"
    wrong["outputs"][0]["stream"] = "sales_summary"
    del wrong["outputs"][0]["write_mode"]
    wrong["outputs"][1]["table"] = "ANALYTICS.CURATED.EXCLUDED_ORDERS"
    wrong["inputs"][0]["columns"][2]["type"] = "VARCHAR(200)"
    wrong["outputs"][0]["columns"][2]["type"] = "NUMBER(38,2)"
    wrong["output"] = {"stream": "sales_summary"}
    assert cs.apply(wrong, cs.derive(repo, "wf_0001").segments["seg_01"]) == canned


def test_apply_keeps_judgment_nullability_keys_row_estimates_and_a_lowered_or_raised_target(tmp_path):
    repo = synthetic(tmp_path)
    scaffold = cs.derive(repo, WF).segments["seg_01"]
    mine = _judged(copy.deepcopy(scaffold.contract))
    mine["target"] = "manual"
    mine["inputs"][0]["columns"][0]["nullable"] = False
    mine["inputs"][0]["keys"] = ["ID"]
    mine["inputs"][0]["expected_rows"] = {"min": 1, "max": 10}
    mine["inputs"][0]["large"] = True
    mine["outputs"][0]["keys"] = ["ID"]
    mine["output"] = copy.deepcopy(mine["outputs"][0])
    assert cs.apply(mine, scaffold) == mine
    raised = {**mine, "target": "sql"}
    assert cs.apply(raised, scaffold)["target"] == "sql", "a raised target is checkTargets' to report, never fixed"
    missing = {key: value for key, value in mine.items() if key != "target"}
    assert "target" not in cs.apply(missing, scaffold), "a missing target stays missing (target-missing)"


def test_apply_drops_invented_entries_and_adds_the_missing_ones(tmp_path):
    repo = synthetic(tmp_path)
    scaffold = cs.derive(repo, WF).segments["seg_01"]
    mine = _judged(copy.deepcopy(scaffold.contract))
    mine["outputs"] = [mine["outputs"][2], {"stream": "invented", "kind": "work", "table": "MIG_WORK.X", "columns": []}]
    applied = cs.apply(mine, scaffold)
    assert [(o["stream"], o["kind"]) for o in applied["outputs"]] == [("2_T", "work"), ("2_F", "work"), ("2_T", "target")]
    assert applied["output"] == applied["outputs"][0]


def test_apply_keeps_the_type_maps_unsized_spelling_but_not_a_wrong_length(tmp_path):
    repo = synthetic(tmp_path)
    scaffold = cs.derive(repo, WF).segments["seg_01"]
    mine = _judged(copy.deepcopy(scaffold.contract))
    mine["inputs"][0]["columns"][1]["type"] = "VARCHAR"       # V_String(30): the type map's spelling
    mine["inputs"][0]["columns"][2]["type"] = "VARCHAR"       # String(4): fixed width, never unsized
    applied = cs.apply(mine, scaffold)
    assert [c["type"] for c in applied["inputs"][0]["columns"][1:3]] == ["VARCHAR", "VARCHAR(4)"]
    mine["inputs"][0]["columns"][1]["type"] = "VARCHAR(10)"
    assert cs.apply(mine, scaffold)["inputs"][0]["columns"][1]["type"] == "VARCHAR(30)"


def test_apply_keeps_target_only_columns_only_where_the_target_keeps_its_rows(tmp_path):
    repo = synthetic(tmp_path)
    derived = cs.derive(repo, WF)
    extra = {"name": "LOADED_AT", "type": "TIMESTAMP_NTZ", "nullable": True}
    appended = _judged(copy.deepcopy(derived.segments["seg_01"].contract))
    appended["outputs"][2]["columns"].append(extra)            # tool 3: append
    assert cs.apply(appended, derived.segments["seg_01"])["outputs"][2]["columns"][-1] == extra
    overwritten = _judged(copy.deepcopy(derived.segments["seg_03"].contract))
    overwritten["outputs"][0]["columns"].append(extra)         # tool 8: overwrite
    assert extra not in cs.apply(overwritten, derived.segments["seg_03"])["outputs"][0]["columns"]


def test_apply_all_rewrites_only_contracts_that_changed(tmp_path):
    repo = synthetic(tmp_path)
    cs.prefill(repo, WF)
    untouched = repo.seg(WF, "seg_02", "contract.json")
    compact = json.dumps(read_json(untouched)) + "\n"          # a different byte layout, same content
    untouched.write_text(compact, encoding="utf-8")
    broken = read_json(repo.seg(WF, "seg_01", "contract.json"))
    broken["outputs"][0]["stream"] = "invented"
    write_json(repo.seg(WF, "seg_01", "contract.json"), broken)
    assert cs.apply_all(repo, WF) == ["seg_01"]
    assert untouched.read_text(encoding="utf-8") == compact
    assert read_json(repo.seg(WF, "seg_01", "contract.json"))["outputs"][0]["stream"] == "2_T"


def test_apply_all_refuses_a_contract_that_is_not_a_json_object(tmp_path):
    repo = synthetic(tmp_path)
    cs.prefill(repo, WF)
    repo.seg(WF, "seg_02", "contract.json").write_text("[1, 2]\n", encoding="utf-8")
    with pytest.raises(cs.ContractUnreadable, match="seg_02"):
        cs.apply_all(repo, WF, ["seg_02"])


def test_prune_removes_only_contracts_nobody_judged(tmp_path):
    repo = synthetic(tmp_path)
    cs.prefill(repo, WF)
    judged = _judged(read_json(repo.seg(WF, "seg_02", "contract.json")))
    write_json(repo.seg(WF, "seg_02", "contract.json"), judged)
    assert cs.prune_unjudged(repo, WF) == ["seg_01", "seg_03"]
    assert repo.seg(WF, "seg_02", "contract.json").is_file()


# --- the CLI ---------------------------------------------------------------------------------------

def test_cli_prints_the_scaffold_and_writes_nothing_by_default(tmp_path, capsys):
    repo = synthetic(tmp_path)
    assert cs.main([WF, "--root", str(tmp_path)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert sorted(printed) == ["seg_01", "seg_02", "seg_03"]
    assert not repo.seg(WF, "seg_01", "contract.json").exists()


def test_cli_modes_prefill_apply_and_prune(tmp_path, capsys):
    repo = synthetic(tmp_path)
    assert cs.main([WF, "--prefill", "--segments", "seg_02", "--root", str(tmp_path)]) == 0
    assert [p.parent.name for p in sorted(repo.wf(WF, "segments").glob("*/contract.json"))] == ["seg_02"]
    assert cs.main([WF, "--apply", "--root", str(tmp_path)]) == 0
    assert cs.main([WF, "--prune-unjudged", "--root", str(tmp_path)]) == 0
    assert not repo.seg(WF, "seg_02", "contract.json").exists()
    err = capsys.readouterr().err
    assert "SpatialObj" in err, "a derivation gap is reported on stderr"


def test_cli_apply_exits_1_naming_a_contract_that_is_not_json(tmp_path, capsys):
    repo = synthetic(tmp_path)
    cs.prefill(repo, WF)
    repo.seg(WF, "seg_03", "contract.json").write_text("{not json", encoding="utf-8")
    assert cs.main([WF, "--apply", "--root", str(tmp_path)]) == 1
    first = capsys.readouterr().err.splitlines()[0]
    assert first.startswith("seg_03: contract.json is not a JSON object"), first


@pytest.mark.parametrize("argv, message", [
    (["wf_0100", "--apply", "--prefill"], "not allowed with"),
    (["wf_0100", "--segments", "seg_09"], "seg_09"),
])
def test_cli_usage_errors_exit_2(tmp_path, capsys, argv, message):
    synthetic(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cs.main([*argv, "--root", str(tmp_path)])
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_cli_exits_2_before_segmentation(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cs.main([WF, "--root", str(tmp_path)])
    assert exc.value.code == 2


# --- fix round 1 ------------------------------------------------------------------------------------

def _snowpark_with_two_streams(tmp_path) -> Repo:
    """wf_0200. seg_01 (snowpark): source 1 -> Python 2 (anchor `1`) and Union 4 (anchor `Output`); both
    leave for seg_02, `2_1` first, so `4_Output` is the SECOND work stream, whose anchor is lower case.
    seg_02: Join 5 -> Output 6."""
    repo = Repo(tmp_path)
    wf = "wf_0200"
    nodes = [_node("1", "input", {"Output": [ID, NAME]}), _node("2", "python", {"1": [ID, NAME]}),
             _node("4", "union", {"Output": [ID, NAME]}), _node("5", "join", {"J": [ID, NAME]}),
             _node("6", "output", {"Output": [ID, NAME]}, {"write_mode": "overwrite"})]
    edges = [_edge("1", "Output", "2"), _edge("1", "Output", "4"), _edge("2", "1", "5", "Left"),
             _edge("4", "Output", "5", "Right"), _edge("5", "J", "6")]
    write_json(repo.wf(wf, "parsed", "dag.json"), {"workflow": wf, "nodes": nodes, "edges": edges})
    members = {"seg_01": ["1", "2", "4"], "seg_02": ["5", "6"]}
    where = {tool: seg for seg, tools in members.items() for tool in tools}
    for seg, tools in members.items():
        write_json(repo.seg(wf, seg, "dag.json"), {
            "workflow": wf, "segment": seg, "nodes": [n for n in nodes if n["tool_id"] in tools],
            "edges": [e for e in edges if e["src"] in tools and e["dst"] in tools],
            "inbound": [{**e, "from_segment": where[e["src"]]} for e in edges if e["dst"] in tools and e["src"] not in tools],
            "outbound": [{**e, "to_segment": where[e["dst"]]} for e in edges if e["src"] in tools and e["dst"] not in tools]})
    write_json(repo.wf(wf, "segments", "order.json"), [["seg_01"], ["seg_02"]])
    write_json(repo.wf(wf, "segments", "targets.json"), {"segments": {"seg_01": "snowpark", "seg_02": "sql"}})
    write_yaml(repo.wf(wf, "intake", "mappings.yaml"), {
        "sources": {"a.yxdb": {"snowflake": "DB.RAW.A", "logical": "A", "tool_ids": ["1"]}},
        "outputs": {"o.yxdb": {"snowflake": "DB.OUT.O", "logical": "O", "mode": "overwrite", "keys": [], "tool_ids": ["6"]}}})
    return repo


def test_a_snowpark_segments_second_work_stream_is_one_table_to_the_scaffold_the_rules_and_its_consumer(tmp_path):
    """I1: `MIG_WORK.WF0200_SEG_01_OUT_4_OUTPUT` -- the scaffold's spelling, the one `lib.backend.qualified`
    accepts on every validator's path -- is the table the producer writes (the Snowpark rules accept it)
    and the table the consumer reads."""
    from lib.snowpark_rules import check_proc_py
    derived = cs.derive(_snowpark_with_two_streams(tmp_path), "wf_0200")
    producer, consumer = derived.segments["seg_01"].contract, derived.segments["seg_02"].contract
    assert [(o["stream"], o["table"]) for o in producer["outputs"]] == [
        ("2_1", "MIG_WORK.WF0200_SEG_01_OUT"), ("4_Output", "MIG_WORK.WF0200_SEG_01_OUT_4_OUTPUT")]
    assert [(i["stream"], i["table"]) for i in consumer["inputs"]] == [
        ("2_1", "MIG_WORK.WF0200_SEG_01_OUT"), ("4_Output", "MIG_WORK.WF0200_SEG_01_OUT_4_OUTPUT")]
    proc = ('# tool 1: Input Data\n# tool 2: Python tool\n# tool 4: Union\n\n\n'
            'def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):\n'
            '    a = session.table(f"{src_db}.{src_schema}.A")\n'
            '    a.write.mode("overwrite").save_as_table("MIG_WORK.WF0200_SEG_01_OUT")\n'
            '    a.write.mode("overwrite").save_as_table("MIG_WORK.WF0200_SEG_01_OUT_4_OUTPUT")\n'
            '    return "OK"\n')
    assert check_proc_py(proc, "wf_0200", "seg_01", producer) == []


def _gap_free(contract: dict) -> dict:
    """The synthetic seg_02 contract, judged, with the one type the scaffold cannot derive (tool 4's
    SpatialObj `SHAPE`) declared -- a contract the checker passes."""
    for entry in contract["inputs"] + contract["outputs"]:
        for column in entry["columns"]:
            column.setdefault("type", "GEOGRAPHY")
    contract["output"] = copy.deepcopy(contract["outputs"][0])
    return {**contract, "row_relation": "expand",
            "ordering": {"keys": [], "alteryx_deterministic": True, "order_dependent_columns": []},
            "tolerances": {}, "parity_risks": [{"tool_id": "5", "class": "LOGIC", "note": "n"}]}


def _put(path: str, value):
    """A mutation setting `a.b[0].c` to `value`."""
    def mutate(contract):
        keys: list = []
        for part in path.replace("]", "").split("."):
            name, *indexes = part.split("[")
            if name:
                keys.append(name)
            keys.extend(int(i) for i in indexes)
        node = contract
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return mutate


#: (label, mutation, what the checker then says -- None when the re-apply repairs it, being mechanical)
JUNK = [
    ("a list type on a derivable variable-length string", _put("inputs[1].columns[1].type", ["VARCHAR"]), None),
    ("an object type on a derivable variable-length string", _put("inputs[1].columns[1].type", {"name": "VARCHAR"}), None),
    ("a number type on a derivable column", _put("inputs[1].columns[0].type", 38), None),
    ("a list type where the DAG says nothing", _put("inputs[0].columns[1].type", ["GEOGRAPHY"]),
     'inputs[0] (source tool 4).columns[1] (SHAPE).type is ["GEOGRAPHY"]'),
    ("a number type where the DAG says nothing", _put("inputs[0].columns[1].type", 7),
     "inputs[0] (source tool 4).columns[1] (SHAPE).type is 7"),
    ("an injection where the DAG says nothing", _put("inputs[0].columns[1].type", "GEOGRAPHY); DROP TABLE X; --"),
     'inputs[0] (source tool 4).columns[1] (SHAPE).type is "GEOGRAPHY); DROP TABLE X; --"'),
    ("inputs as an object", _put("inputs", {"a": 1}), "inputs[0] (source tool 4).columns[1] (SHAPE).type is missing"),
    ("an output that is a string", _put("outputs[0]", "junk"), "outputs[0] (work stream 5_J).columns[2] (SHAPE).type is missing"),
    ("columns as an object", _put("inputs[0].columns", {"a": 1}), "inputs[0] (source tool 4).columns[1] (SHAPE).type is missing"),
    ("a column that is a string", _put("inputs[1].columns[0]", "ID"), None),
    ("a column name that is a list", _put("inputs[1].columns[0].name", ["ID"]), None),
    ("keys as a string", _put("inputs[0].keys", "ID"), 'inputs[0] (source tool 4).keys is "ID"'),
    ("nullable as a string", _put("inputs[0].columns[0].nullable", "no"), 'columns[0] (ID).nullable is "no"'),
    ("expected_rows as a string", _put("inputs[0].expected_rows", "many"), 'expected_rows is "many"'),
    ("row_relation as a list", _put("row_relation", ["expand"]), 'row_relation is ["expand"]'),
    ("ordering as a string", _put("ordering", "by ID"), 'ordering is "by ID"'),
    ("a tolerance too large for a float", _put("tolerances", {"ID": {"float_abs": 10 ** 400}}), 'tolerances.ID is {"float_abs": 1000'),
    ("an output kind that is a list", _put("outputs[1].kind", ["target"]), None),
    ("a target tool_id that is an object", _put("outputs[1].tool_id", {"id": "6"}), None),
    ("a parity risk tool_id that is a list", _put("parity_risks", [{"tool_id": ["5"], "class": "LOGIC", "note": "n"}]),
     'parity_risks[0].tool_id is ["5"]'),
]


@pytest.mark.parametrize("label, mutate, reported", JUNK, ids=[j[0] for j in JUNK])
def test_apply_never_crashes_on_model_junk_and_the_checker_names_what_is_left(tmp_path, capsys, label, mutate, reported):
    """I3: `--apply` over a malformed contract repairs what is mechanical and leaves the rest for
    contract_check.py to name (exit 1, a message) -- never a traceback and exit 2, which the orchestrator
    would record as a tooling crash and never feed back to the model."""
    import contract_check
    repo = synthetic(tmp_path)
    cs.prefill(repo, WF)
    path = repo.seg(WF, "seg_02", "contract.json")
    contract = _gap_free(read_json(path))
    write_json(path, contract)
    assert contract_check.check(repo, WF, ["seg_02"]) == [], "the base contract passes"
    mutate(contract)
    write_json(path, contract)
    assert cs.main([WF, "--apply", "--segments", "seg_02", "--root", str(tmp_path)]) == 0, capsys.readouterr().err
    problems = contract_check.check(repo, WF, ["seg_02"])
    if reported is None:
        assert problems == [], problems
    else:
        assert any(reported in line for line in problems), problems
        assert contract_check.main([WF, "--segments", "seg_02", "--root", str(tmp_path)]) == 1


def test_a_target_without_its_kind_keeps_its_keys_and_nullability(built):
    """M1: an entry is recognised by its tool id whatever its own `kind` says, so the analyzer's
    judgment on it survives the re-apply."""
    repo = built("wf_0001")
    canned = _canned("wf_0001", "seg_01")
    wrong = copy.deepcopy(canned)
    del wrong["outputs"][0]["kind"]
    applied = cs.apply(wrong, cs.derive(repo, "wf_0001").segments["seg_01"])
    assert applied == canned
    assert applied["outputs"][0]["keys"] == ["REGION", "SIZE_BAND"]


def test_a_mapped_source_that_also_carries_a_stream_keeps_its_row_estimate(built):
    repo = built("wf_0001")
    canned = _canned("wf_0001", "seg_01")
    wrong = copy.deepcopy(canned)
    wrong["inputs"][0]["stream"] = "1_Output"
    assert cs.apply(wrong, cs.derive(repo, "wf_0001").segments["seg_01"]) == canned


def test_an_empty_segments_list_is_a_usage_error(tmp_path, capsys):
    """N4: `--segments ""` names nothing; it is refused, never taken as "check nothing and pass"."""
    synthetic(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cs.main([WF, "--segments", "", "--root", str(tmp_path)])
    assert exc.value.code == 2
    assert "names no segment" in capsys.readouterr().err
