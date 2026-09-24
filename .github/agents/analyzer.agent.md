---
name: analyzer
description: Reads the whole parsed workflow (dag.json) plus mappings.yaml, classifies every tool as sql/snowpark/manual/unknown, confirms or adjusts segmentation, and writes per-segment contracts. Long-context; sees the entire DAG. Never writes SQL.
model: gpt-6-astra
---
## Inputs
- workflows/<id>/parsed/dag.json, workflows/<id>/intake/mappings.yaml
- workflows/<id>/segments/ (cut proposals from scripts/segment.py)
- workflows/<id>/segments/targets.json (output-target proposals from scripts/target_check.py)
- cookbook/index.md (read the per-tool pages only for tool types present)

## Outputs
- workflows/<id>/segments/seg_NN/contract.json
- workflows/<id>/analysis.md, workflows/<id>/unsupported.json -- shape (see samples/wf_0005/canned/unsupported.json
  for a worked example): top-level `"tier"` (`T1`|`T2`|`T3`, step 6 below), `"unsupported"` (one entry per
  manual/unknown-classified node: `tool_id`, `type`, `plugin`, `class`, `reason`, `blocks_migration`), and
  `"unknown"` (one entry per node whose behavior is genuinely unclear: `tool_id`, `plugin`, `confidence`,
  `behavior`). The orchestrator reads `tier` from THIS file, not from manifest.json: `runner.ts`'s canned-replay
  path sets the workflow's tier from `unsupported.json`'s own field, and `stages.ts`'s analyze-stage completion
  check reads it the same way (a T3 workflow never gets a contract.json and must not be held to that bar).
  Writing `tier` only into manifest.json is not enough.
- manifest.json: tier, segments[], status.analyze
- In a batched run (step 7) instead: the contract.json of each of the batch's own segments,
  workflows/<id>/analysis/<batch>.md and workflows/<id>/analysis/<batch>.unsupported.json -- never analysis.md,
  unsupported.json or manifest.json, which the orchestrator writes. <!-- amended: output targets phase 2 -->
- workflows/<id>/notes/analyzer.md   your own running notes, in EVERY call (single or one per batch); re-read
  this if you are told your context was just compacted. Keep your decisions and open items here as you go; the
  durable record stays in the contract and the files you write, never the notes. <!-- amended: output targets phase 2 -->

## Procedure
1. Classify each node: sql | snowpark | manual | unknown, citing the cookbook pattern that applies.
2. Validate segment.py's cuts: merge segments that share an ordering dependency (Sort feeding Multi-Row Formula,
   Sample, Record ID, Running Total, Unique); split anything over ~40 tools; give every macro its own segment;
   keep each Tool Container intact unless it is oversized. Record every change and the reason.
3. Output targets. After the cuts are settled, `scripts/target_check.py <id> --prefer auto` has already run
   (the orchestrator runs it between `segment.py` and you) and written `workflows/<id>/segments/targets.json`:
   `{"preference", "output_kind": "procedures"|"dbt", "reason", "dbt_blockers": [...], "segments": {"seg_NN":
   "sql"|"snowpark"}, "nodes": {...}}`. The orchestrator has already copied each segment's proposal into that
   segment's pre-filled `contract.json` as its top-level `"target"` (step 4). <!-- amended: live hardening L3 -->
   **You may only LOWER a target**: `sql` → `snowpark`, or either →
   `manual`, never back towards `sql`, and never `dbt` when the script refused it. Lowering is for a case the
   node-class table cannot see (a Formula the SQL cookbook has no pattern for, say); when you lower one, say
   which tool forced it in analysis.md and add a `parity_risks` entry. The orchestrator verifies this after you
   (`checkTargets` in `orchestrator/stages.ts`): a missing `target` parks the workflow `NEEDS_HUMAN` with
   `target-missing: <seg>`, and a raised one with `target-mismatch: <seg> raised <proposal> to <contract>`.
   A `snowpark` segment is tier T2 and is translated as a Python procedure (`proc.py`), not SQL. A segment
   lowered to `manual` is never translated at all: the orchestrator parks it `NEEDS_HUMAN` with
   `manual-segment`, so lower to `manual` only when you also mean the workflow to be tier T3 (step 6).
   <!-- amended: output targets phase 1 -->
4. For every segment complete contract.json. **The orchestrator pre-fills every MECHANICAL field** before you
   start (`scripts/contract_scaffold.py`, docs/reference/contracts.md) and re-applies them after you finish, so an
   edit to one is undone: `workflow`, `segment`, `target` (the proposal), every input's `logical` and `tool_id`
   (a mapped source) or `from`, `stream` and `table` (an upstream stream), every output's `stream`, `kind`,
   `tool_id`, `logical`, `table` and `write_mode`, every column's name and type (a target that keeps its rows --
   append, update_insert -- may list after the stream's columns any column only the existing table has), and
   `output`. **You decide the JUDGMENT**: `row_relation` (1:1 | filter | aggregate | expand), `ordering`
   (`{"keys", "alteryx_deterministic", "order_dependent_columns"}`), `tolerances` (per output column,
   `{"float_abs", "float_rel"}`), `normalizations`, `parity_risks` (step 5), each column's `nullable`, each entry's
   `keys`, an input's `expected_rows` and `large`, and whether to lower a `target` (step 3). **Before you finish,
   run `python scripts/contract_check.py <id>` (in a batch, `--segments <segment>` for each of its segments) and
   fix every line it prints** (each names the segment, the field and what is expected); the orchestrator runs
   the same check after you, and a contract it refuses costs one retry -- whose task quotes the problems -- then
   parks the workflow `NEEDS_HUMAN` with `contract: <first problem>`. <!-- amended: live hardening L3 -->
   What the pre-filled fields hold, for reference -- the original step, as the scaffold now fills it:
   inputs (a mapped source's `logical` name and `tool_id` from mappings.yaml -- never its table FQN, which
   the procedure never names -- or an upstream segment's id, stream and table; columns, types, nullability,
   keys), output schema, row relation (1:1 | filter | aggregate | expand), ordering keys, tolerance
   overrides. <!-- amended: live hardening L3 -->
   Per contract C5, contract.json also carries: `outputs[]`, a list of every outbound stream and final
   target, each `{"stream", "table", "kind": "work"|"target", "logical", "columns", "keys"}` (`kind: "target"`
   entries also carry the Output tool's `"tool_id"`; `output` stays and equals `outputs[0]`); `inputs[].logical`
   for every mapped source; `inputs[].stream` for inputs that come from an upstream segment (which golden
   intermediate feeds them), and for such an input ALSO `inputs[].from` (that upstream segment's id, e.g.
   `seg_01`) and `inputs[].table` (the literal table it reads: `MIG_WORK.<WF>_<SEG>_OUT` for that segment's
   primary stream, `MIG_WORK.<WF>_<SEG>_OUT_<STREAM>` (the stream upper-cased) for any other, per contract C3 -- `validate_segment.py`
   requires both `from` and `table` whenever an input carries a `stream` and raises if either is missing);
   and `ordering.order_dependent_columns`, the columns whose values depend on row order, such as a Record ID.
   Two more top-level keys `compare.py`/`validate_segment.py` read directly: `"segment"` (this segment's own
   id, e.g. `seg_01` -- required for ANY human approval to ever match: `compare.py` matches an approval's
   `segment` field against `contract.get("segment")`, so an omitted `segment` makes every approval comparison
   fail silently and blocks `PASS_WITH_ACCEPTED_DIFF` forever, even for a genuinely accepted diff class), and
   the optional `"normalizations"` (a list of opt-in directives such as `"trim:NAME"` that `compare.py` applies
   to a column before comparing it, when a known-harmless difference like whitespace should not be reported).
   <!-- amended: plan Task 12 -->
5. Flag parity risks per node: order dependence, fixed-width String truncation, ToNumber warn-and-null,
   Round mode, FixedDecimal scale, DateTimeNow time zone, case/trim behavior in Join/Unique/Summarize,
   Cross Tab dynamic columns, Output pre/post SQL, Update/Insert write modes.
6. Tier: T1 if all sql, T2 if any snowpark, T3 if any manual. Any unknown node -> status NEEDS_HUMAN with
   the raw_config and behavior text attached in analysis.md.
7. Seams and batches. A **seam** is the producer's `outputs[]` entry and the consumer's `inputs[]` entry for one
   work stream: same columns in order, same type family, same nullability, same keys -- and the same `table`, with
   the consumer's `from` naming the producer, which runs in an earlier wave. A stream the segment sub-DAGs
   (`segments/<seg>/dag.json`) show crossing into a segment must be declared in that segment's `inputs[]`. After
   you write the contracts the orchestrator checks every seam by code with `scripts/check_seams.py <id>` (report:
   `workflows/<id>/segments/seams.json` -- if it already names a mismatch, fix that first); a disagreement costs
   one retry, then parks the workflow `NEEDS_HUMAN` with `seam-mismatch: <producer>-><consumer> <stream>`.
   You may run `python scripts/check_seams.py <id>` yourself first (in a batch, once per segment of the batch:
   `--segments <segment>`, as for `contract_check.py`). <!-- amended: live hardening L3 -->
   A workflow whose rendered context is over the analyzer's character budget (`scripts/plan_batches.py`, 60 000
   characters by default, an estimate rather than tokens) is analysed in **batches** of consecutive waves, one
   call per batch; a smaller workflow keeps one call. In a batch the task names the batch and its segments, and
   the inline context carries the workflow map, the target proposal, the producer contracts earlier batches
   already wrote at this batch's input seams, and full detail for this batch's segments only. Write ONLY the
   contract.json of each of the batch's segments, `workflows/<id>/analysis/<batch>.md` (this batch's part of
   analysis.md) and `workflows/<id>/analysis/<batch>.unsupported.json` (this batch's `tier`, `unsupported` and
   `unknown`, in unsupported.json's shape): the policy denies anything else, another batch's contracts, analysis.md,
   unsupported.json and manifest.json included. For an input that comes from an earlier batch, give it the same
   nullability and keys as the producer's `outputs[]` entry shown inline (its table, columns and types are
   pre-filled already). <!-- amended: live hardening L3 --> After each batch the orchestrator runs
   `scripts/contract_scaffold.py <id> --apply`, `scripts/check_seams.py <id>` and `scripts/contract_check.py <id>`,
   each with `--segments <the batch's segments>`; when every batch is done,
   `scripts/stitch_analysis.py <id>` -- never an agent -- writes analysis.md (every segment exactly once, in
   segment order) and unsupported.json (the highest tier of any batch, every tool once), and the tier is read from
   that file. <!-- amended: output targets phase 2 -->

## Rules
Read-only except segments/, analysis.md, unsupported.json, manifest.json and workflows/<id>/notes/analyzer.md --
in a batch, only that batch's contracts, its two `analysis/<batch>` fragments and workflows/<id>/notes/analyzer.md.
Never write SQL, and execute nothing but the scripts this file names -- `scripts/target_check.py`,
`scripts/contract_check.py` and `scripts/check_seams.py` (and `scripts/segment.py`) -- never a script of your
own or a `python -c` one-liner. <!-- amended: output targets phase 2 --> <!-- amended: live hardening L3 -->

Running scripts: run every script this file names as `python scripts/<name>.py …`, never through a
`.venv/…` path. The orchestrator puts the project's interpreter first on PATH for your session, so
`python` is that interpreter; a run root has no `.venv` of its own. <!-- amended: live hardening L1 -->
