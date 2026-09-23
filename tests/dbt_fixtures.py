"""A hand-built two-segment dbt workflow (`wf_0009`), the way `tests/test_validate_segment.py`'s
`build()` builds its own `wf_0009` -- self-contained, never the canned hand-migrations `tests/
test_e2e_parity.py` writes. `seg_01` filters `ITEMS` and writes the work stream `2_T`; `seg_02`
consumes that stream directly (golden data, never seg_01's own project) and feeds two targets from
it: `ITEMS_OUT` (overwrite) and `ITEMS_HIST` (merge on `ID`), so one stream feeding two targets is
already exercised at Task A (`lib.validation.combine`'s duplicate-key fix is Task B's own concern,
not this fixture's).
"""
from __future__ import annotations

from lib import typed_csv
from lib.io import write_json, write_yaml
from lib.paths import Repo
from lib import dbt_project as dp

WF = "wf_0009"
SEGMENTS = ["seg_01", "seg_02"]

F = [
    {"name": "ID", "type": "Int64", "size": 8, "scale": None},
    {"name": "NOTE", "type": "V_String", "size": 10, "scale": None},
    {"name": "AMOUNT", "type": "FixedDecimal", "size": 19, "scale": 2},
]

COLUMNS = [
    {"name": "ID", "type": "NUMBER(38,0)", "nullable": False},
    {"name": "NOTE", "type": "VARCHAR(10)", "nullable": True},
    {"name": "AMOUNT", "type": "NUMBER(19,2)", "nullable": True},
]

MAPPINGS = {
    "sources": {
        "items.yxdb": {"snowflake": "SRC.RAW.ITEMS", "logical": "ITEMS", "tool_ids": ["1"]},
    },
    "outputs": {
        "out/items_out.yxdb": {"logical": "ITEMS_OUT", "tool_ids": ["4"], "mode": "overwrite", "keys": []},
        "alias:dw/dbo.items_hist": {"logical": "ITEMS_HIST", "tool_ids": ["5"], "mode": "merge", "keys": ["ID"]},
    },
}

DAG_SEG_01 = {
    "workflow": WF, "segment": "seg_01",
    "nodes": [
        {"tool_id": "1", "type": "input", "config": {}},
        {"tool_id": "2", "type": "filter", "config": {}},
    ],
    "edges": [],
}

DAG_SEG_02 = {
    "workflow": WF, "segment": "seg_02",
    "nodes": [
        {"tool_id": "4", "type": "output", "config": {"pre_sql": None, "post_sql": None, "write_mode": "overwrite"}},
        {"tool_id": "5", "type": "output", "config": {"pre_sql": None, "post_sql": None, "write_mode": "merge"}},
    ],
    "edges": [],
}

CONTRACT_SEG_01 = {
    "segment": "seg_01",
    "inputs": [{"logical": "ITEMS", "tool_id": "1", "columns": COLUMNS, "keys": []}],
    "outputs": [
        {"stream": "2_T", "kind": "work", "table": "MIG_WORK.WF0009_SEG_01_OUT", "logical": None,
         "columns": COLUMNS, "keys": []},
    ],
    "target": "sql",
    "row_relation": "filter",
    "ordering": {"keys": ["ID"], "alteryx_deterministic": True},
    "tolerances": {},
}

CONTRACT_SEG_02 = {
    "segment": "seg_02",
    "inputs": [{"from": "seg_01", "stream": "2_T", "table": "MIG_WORK.WF0009_SEG_01_OUT",
               "columns": COLUMNS, "keys": []}],
    "outputs": [
        {"stream": "2_T", "kind": "target", "tool_id": "4", "logical": "ITEMS_OUT", "table": None,
         "columns": COLUMNS, "keys": []},
        {"stream": "2_T", "kind": "target", "tool_id": "5", "logical": "ITEMS_HIST", "table": None,
         "columns": COLUMNS, "keys": ["ID"]},
    ],
    "target": "sql",
    "row_relation": "1:1",
    "ordering": {"keys": ["ID"], "alteryx_deterministic": True},
    "tolerances": {},
}

# --- golden data, keyed by golden set --------------------------------------------------------

_INPUT_ROWS = {
    "normal": [[1, "keep", "10.00"], [2, "drop", "5.00"], [3, "keep", None], [4, None, "1.00"]],
    "second": [[5, "keep", "2.50"], [6, "keep", "3.50"]],
}
_SEG01_STREAM_ROWS = {
    "normal": [[1, "keep", "10.00"], [3, "keep", None]],
    "second": [[5, "keep", "2.50"], [6, "keep", "3.50"]],
}
_ITEMS_OUT_ROWS = _SEG01_STREAM_ROWS
_ITEMS_HIST_BEFORE_ROWS = {
    "normal": [[1, "old", "0.00"], [7, "x", "7.00"]],
    "second": [[1, "old", "0.00"], [7, "x", "7.00"]],
}
_ITEMS_HIST_AFTER_ROWS = {
    "normal": [[1, "keep", "10.00"], [3, "keep", None], [7, "x", "7.00"]],
    "second": [[1, "old", "0.00"], [5, "keep", "2.50"], [6, "keep", "3.50"], [7, "x", "7.00"]],
}

# --- the dbt project's own files, relative to workflows/<wf>/dbt/ ------------------------------

MODEL_FILES: dict[str, str] = {
    "models/wf0009_seg_01_out.sql": """\
-- models/wf0009_seg_01_out.sql
-- wf_0009 / seg_01: tools 1-2, the work stream MIG_WORK.WF0009_SEG_01_OUT (stream 2_T)
{{ config(materialized='table') }}
with
-- tool 1: Input Data -- logical ITEMS
t1_input as (
    select ID, NOTE, AMOUNT from {{ source('src', 'ITEMS') }}
),
-- tool 2 (anchor T): Filter NOTE = 'keep' -- a NULL NOTE is not true, so the row is dropped
t2_filter_t as (
    select ID, NOTE, AMOUNT from t1_input where NOTE = 'keep'
)
select ID, NOTE, AMOUNT from t2_filter_t
""",
    "models/items_out.sql": """\
-- models/items_out.sql
-- tool 4: Output Data (overwrite, logical ITEMS_OUT)
{{ config(materialized='table', alias='ITEMS_OUT') }}
select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}
""",
    "models/items_hist.sql": """\
-- models/items_hist.sql
-- tool 5: Output Data (Update; Insert if new on ID -> merge, logical ITEMS_HIST)
{{ config(materialized='incremental', incremental_strategy='merge', unique_key=['ID'], alias='ITEMS_HIST') }}
select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}
""",
    "dbt_project.yml": """\
name: wf_0009
version: "1.0.0"
config-version: 2
profile: alteryx_migration
model-paths: [models]
vars:
  src_schema: null
  tgt_schema: null
""",
    "profiles.yml": dp.PROFILES_TEMPLATE,
    "models/sources.yml": """\
version: 2
sources:
  - name: src
    schema: "{{ var('src_schema') }}"
    tables:
      - name: ITEMS
        columns:
          - name: ID
          - name: NOTE
          - name: AMOUNT
""",
    "models/schema.yml": """\
version: 2
models:
  - name: wf0009_seg_01_out
    columns:
      - name: ID
      - name: NOTE
      - name: AMOUNT
  - name: items_out
    columns:
      - name: ID
      - name: NOTE
      - name: AMOUNT
  - name: items_hist
    columns:
      - name: ID
        tests:
          - not_null
          - unique
      - name: NOTE
      - name: AMOUNT
""",
    "README.md": """\
# wf_0009 (dbt target)

Run: `dbt run --project-dir workflows/wf_0009/dbt --profiles-dir workflows/wf_0009/dbt --target snowflake --vars '{"src_schema": "<SRC>", "tgt_schema": "<TGT>"}'`
""",
}


def build_dbt_workflow(tmp_path, *, replace: dict[str, str | None] | None = None,
                       sets=("normal", "second")) -> Repo:
    """A hand-made `wf_0009` dbt workflow under `tmp_path`: manifest, mappings, golden data for
    every golden set in `sets`, segments/order.json, both segments' dag.json and contract.json,
    and the dbt project itself (`MODEL_FILES`, with `replace` applied -- a `None` value deletes
    that project file, anything else overwrites its text)."""
    repo = Repo(tmp_path)
    write_json(repo.wf(WF, "manifest.json"),
              {"id": WF, "golden_sets": list(sets), "output_kind": "dbt", "status": {}, "metrics": {}})
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), MAPPINGS)

    for golden_set in sets:
        typed_csv.write_table(repo.wf(WF, "golden", "inputs", golden_set, "1.csv"),
                              {"fields": F, "rows": _INPUT_ROWS[golden_set]})
        typed_csv.write_table(repo.wf(WF, "golden", "intermediates", "seg_01", golden_set, "2_T.csv"),
                              {"fields": F, "rows": _SEG01_STREAM_ROWS[golden_set]})
        typed_csv.write_table(repo.wf(WF, "golden", "outputs", golden_set, "4.csv"),
                              {"fields": F, "rows": _ITEMS_OUT_ROWS[golden_set]})
        typed_csv.write_table(repo.wf(WF, "golden", "targets_before", golden_set, "ITEMS_HIST.csv"),
                              {"fields": F, "rows": _ITEMS_HIST_BEFORE_ROWS[golden_set]})
        typed_csv.write_table(repo.wf(WF, "golden", "outputs", golden_set, "5.csv"),
                              {"fields": F, "rows": _ITEMS_HIST_AFTER_ROWS[golden_set]})

    write_json(repo.wf(WF, "segments", "order.json"), [["seg_01"], ["seg_02"]])
    write_json(repo.seg(WF, "seg_01", "dag.json"), DAG_SEG_01)
    write_json(repo.seg(WF, "seg_02", "dag.json"), DAG_SEG_02)
    write_json(repo.seg(WF, "seg_01", "contract.json"), CONTRACT_SEG_01)
    write_json(repo.seg(WF, "seg_02", "contract.json"), CONTRACT_SEG_02)

    files = dict(MODEL_FILES)
    if replace:
        files.update(replace)
    for rel, text in files.items():
        path = repo.wf(WF, "dbt", rel)
        if text is None:
            if path.is_file():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    return repo
