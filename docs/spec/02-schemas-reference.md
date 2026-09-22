# Schemas reference — manifest and every pipeline artifact

← [Plan & architecture](00-README.md) · [Copilot setup](01-copilot-setup.md)

Every artifact the pipeline reads or writes. Agents and scripts must produce exactly these shapes; the orchestrator routes on the `status` / `verdict` vocabularies only.

Status vocabulary: `PENDING` · `DONE` · `PARSED` · `RECOVERED` · `QUARANTINED` · `READY` · `WAITING_FOR_ANSWERS` · `BLOCKED` · `NEEDS_HUMAN` · `MANUAL` · `VALIDATED` · `OPEN`
Verdict vocabulary: `PASS` · `PASS_WITH_ACCEPTED_DIFF` · `FAIL` · `BLOCK`

## manifest.json

```json
{
  "id": "wf_0042",
  "source": { "file": "GL_Summary.yxmd", "alteryx_version": "2023.1", "engine": "AMP", "server_schedule": "0 6 * * 1-5", "owner": "jdoe", "consumers": ["tableau:GL Dashboard"] },
  "tier": "T1",
  "status": { "parse": "PARSED", "intake": "READY", "analyze": "DONE", "translate": "VALIDATED", "document": "DONE", "pr": "OPEN" },
  "parse": { "attempts": 1, "extensions": [] },
  "segments": ["seg_01", "seg_02", "seg_03"],
  "golden_sets": ["normal", "period_end", "empty", "edge"],
  "segment_status": { "seg_01": "PASS", "seg_02": "PASS_WITH_ACCEPTED_DIFF", "seg_03": "PASS" },
  "accepted_diffs": [ { "segment": "seg_02", "class": "ROUNDING", "columns": ["AMOUNT"], "example": "…", "approver": "jdoe", "date": "2026-09-20" } ],
  "answers": { "Q1": "FINANCE.RAW.GL_LEDGER" },
  "metrics": { "translator": { "lastMs": 182000, "toolCalls": 41, "premium_requests": 6 }, "validator": { "credits": 0.4 } },
  "production": { "trigger": "event+time_travel", "shadow_since": "2026-10-01", "clean_cycles": 7, "signoff": null },
  "pr": "https://github.com/org/migration/pull/123",
  "updated_at": "2026-09-20T14:02:11Z"
}
```

## dag.json

```json
{
  "workflow": "wf_0042", "yxmd_version": "2023.1", "engine": "AMP",
  "constants": { "User.Region": "EMEA" },
  "nodes": [
    { "tool_id": "12", "type": "input", "plugin": "AlteryxBasePluginsGui.DbFileInput.DbFileInput",
      "container_id": null, "config": { "source": "<scrubbed:fin_gl>", "format": "yxdb" },
      "raw_config": "<Configuration>…</Configuration>", "annotation": "GL extract",
      "out_anchors": ["Output"], "meta": { "Output": [ { "name": "ACCT", "type": "V_String", "size": 20 } ] } },
    { "tool_id": "55", "type": "macro", "macro_path": "Supporting_Macros/clean.yxmc", "sub_dag": { "…": "…" }, "interface": [ { "name": "Threshold", "default": "0.5" } ] },
    { "tool_id": "77", "type": "unknown", "plugin": "Vendor.CustomTool", "raw_config": "…", "behavior": "appears to dedupe on ACCT keeping max DATE", "confidence": 0.6 }
  ],
  "edges": [ { "src": "12", "src_anchor": "Output", "dst": "20", "dst_anchor": "Left" } ]
}
```

## parse_report.json

```json
{ "status": "INVARIANT_VIOLATION", "errors": ["nodes in XML but not in dag: ['41','42']"], "node_count": 118,
  "unknown_share": 0.02, "scrubbed_aliases": ["fin_gl"], "attempt": 1, "extension": null, "diagnosis": "parsed/parse_diagnosis.md" }
```

## contract.json

```json
{
  "segment": "seg_03",
  "inputs": [
    { "from": "seg_02", "table": "MIG_WORK.WF0042_SEG_02_OUT",
      "columns": [ { "name": "ACCT", "type": "VARCHAR", "nullable": false }, { "name": "AMOUNT", "type": "NUMBER(19,6)", "nullable": true } ],
      "keys": ["ACCT", "PERIOD"], "expected_rows": { "min": 1, "max": 5000000 }, "large": true }
  ],
  "output": { "table": "MIG_WORK.WF0042_SEG_03_OUT", "columns": [ "…" ], "keys": ["ACCT", "PERIOD"] },
  "row_relation": "aggregate",
  "ordering": { "keys": ["ACCT", "PERIOD", "POSTED_AT"], "alteryx_deterministic": false },
  "tolerances": { "AMOUNT": { "float_abs": 0.01 } },
  "parity_risks": [ { "tool_id": "31", "class": "ORDERING", "note": "Unique keeps first row after Sort 30" } ]
}
```

## review.json

```json
{ "verdict": "BLOCK", "findings": [ { "rule": "order_dependent_needs_order_by", "severity": "block", "location": "t31_unique", "fix_hint": "add ORDER BY POSTED_AT DESC per contract.ordering" } ] }
```

## validation.json

```json
{
  "segment": "seg_03", "golden_set": "normal", "verdict": "FAIL",
  "checks": { "schema": "PASS", "counts": { "expected": 41210, "actual": 41208, "verdict": "FAIL" }, "aggregates": "PASS", "set_diff": { "only_expected": 2, "only_actual": 0 } },
  "diff_clusters": [ { "class": "NULL_SEMANTICS", "columns": ["REGION"], "count": 2, "example_rows": [ { "key": "…", "expected": "…", "actual": "…" } ], "suspect_cte": "t18_filter" } ],
  "normalizations_applied": ["trim:REGION"], "idempotent": true,
  "runtime_ms": 8400, "credits": 0.02, "needs_human": false
}
```

## order.json

```json
[ ["seg_01", "seg_02"], ["seg_03"] ]
```

## fix_log.md

```markdown
## iteration 2 — t18_filter
- symptom: 2 rows missing where REGION is NULL
- root cause: Filter sends NULL evaluations to the False branch; translation used WHERE REGION <> 'X'
- fix: WHERE NOT (REGION = 'X') OR REGION IS NULL  → routed to False CTE
- cookbook_candidate: yes (Filter)
- status: FIXED
```

## intake/mappings.yaml (workflow-level)

```yaml
sources:
  "fin/gl_2024.yxdb": { snowflake: FINANCE.RAW.GL_LEDGER, tool_ids: ["12"], confirmed_by: jdoe }
outputs:
  "dbo.gl_summary": { snowflake: ANALYTICS.CURATED.GL_SUMMARY, mode: merge, keys: [ACCT, PERIOD], tool_ids: ["88"] }
constants: { "User.Region": "EMEA" }
parameters: { Threshold: { type: NUMBER, default: 0.5 } }
macros: { "Supporting_Macros/clean.yxmc": { shared_proc: SHARED.MACROS.CLEAN_V1 } }
```

`mappings/global.yaml` is shown in [Plan §5.6](00-README.md#6-mappingsglobalyaml-template); `open_questions.md` in [Plan §5.4](00-README.md#4-open_questionsmd-format).
