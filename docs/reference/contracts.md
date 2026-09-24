# Contracts: mechanical fields, judgment fields, and the checker

A segment's `contract.json` (schema: `docs/spec/02-schemas-reference.md`, plus plan contracts C3 and C5)
is the hand-off every later stage reads: `load_golden.py` and the validators load its inputs, `compare.py`
judges each output against it, the translator and the reviewer work from it. Since live-hardening Task L3
it has two owners.

**Why.** In the fourth live test (live-hardening round, 2026-09-23: a local model through the real SDK, wf_0001)
the analyzer wrote a contract whose output stream names were invented (`sales_summary` for `6_Output`), whose
source input had no `tool_id` and an extra `table`, whose targets had no `write_mode` and no `table: null`,
whose string types had lost their DAG sizes, and which had no `workflow`, `normalizations` or `parity_risks`
and an `ordering` in a shape nothing reads (`sort: [...]`). Every one of those fields follows from files the
pipeline had already written, and nothing checked any of them before golden, translate and validate read the
contract. The model is now asked only for what needs judgment, and code checks all of it -- with one
exception by ruling: an unsized `VARCHAR` for a variable-length string (`V_String`) is accepted as the same
type (see Columns below), so of the live contract's sizeless strings only the fixed-width `String(10)` is
refused.

## Who owns which field

| Field | Owner | Derived from |
|---|---|---|
| `workflow`, `segment` | orchestrator | the workflow and segment ids |
| `target` | orchestrator proposes, analyzer may only **lower** | `segments/targets.json` (`target_check.py`) |
| `inputs[]` of a mapped source: `logical`, `tool_id`, `columns` | orchestrator | `intake/mappings.yaml` `sources`, the source tool's DAG meta |
| `inputs[]` of an upstream stream: `from`, `stream`, `table`, `columns` | orchestrator | the sub-DAG's `inbound` edges, the producer's anchor meta, contract C3 |
| `outputs[]` work stream: `stream`, `kind`, `table`, `logical: null`, `columns` | orchestrator | the sub-DAG's `outbound` edges, the anchor's meta, C3 |
| `outputs[]` target: `stream`, `kind`, `tool_id`, `logical`, `table: null`, `write_mode`, `columns` | orchestrator | the Output tool, the edge into it, `mappings.yaml` `outputs` |
| `output` | orchestrator | a copy of `outputs[0]` |
| every column's `nullable`; every entry's `keys`; an input's `expected_rows`, `large` | analyzer | pre-filled with the neutral `true` / `[]` |
| `row_relation`, `ordering`, `tolerances`, `parity_risks` | analyzer | never pre-filled |
| `normalizations` | analyzer | pre-filled with the neutral `[]` |

The derivation (`scripts/contract_scaffold.py`):

- **Stream names** are `<source tool>_<anchor>` (`6_Output`, `3_F`, `2_Output5`) -- the name golden
  intermediates and `validate_segment.py` already use.
- **Inputs:** mapped sources first, in tool-id order; then each distinct stream crossing into the segment, in
  the sub-DAG's `inbound` edge order.
- **Outputs:** each distinct stream leaving the segment first (in `outbound` edge order), then each Output tool
  (in tool-id order). A work stream's `table` is `MIG_WORK.<WF>_<SEG>_OUT` for the segment's first work stream
  and `MIG_WORK.<WF>_<SEG>_OUT_<STREAM>` for any other (C3; the stream upper-cased, because every validator
  splices the table through `lib.backend.qualified`, which takes plain upper-case identifiers only). A Snowpark
  procedure's table-name rule (`lib/snowpark_rules.py`) accepts the contract's own table and compares names
  case-insensitively, as Snowflake folds an unquoted identifier.
- **`write_mode`** is the mapping's `mode` in the Output tool's own vocabulary: `overwrite`, `append`, and
  `merge` as `update_insert`.
- **Columns** are the producing anchor's DAG meta: names upper-cased (every consumer compares them upper-cased),
  types from `lib.types_map.alteryx_to_snowflake`, except that a variable-length string (`V_String`,
  `V_WString`) keeps its DAG size, `VARCHAR(<size>)`, as six of the seven committed samples declare it. The type
  map's own unsized `VARCHAR` -- what `wf_0007`'s contracts declare -- is accepted as the same type; any other
  type or length is not. A fixed-width `String(n)` is always `VARCHAR(n)`.
- **Target-only columns.** A target that keeps its existing rows (`append`, `update_insert`) may list, after the
  stream's own columns, columns only the existing table has (`wf_0003`'s `LOADED_FLAG`, filled by its PostSQL).
  An overwrite target may not.
- **Gaps.** A field the scaffold cannot derive -- a type the type map does not cover, a source with no mapping,
  an anchor with no meta -- is left out, reported as a `note:` on stderr (the orchestrator logs it), and is the
  analyzer's to declare; the checker then checks only its shape (a declared column type: a Snowflake type name
  with an optional `(precision[, scale])`, what `compile_check.py` can build a table from).
- **Re-applying never crashes on the model's output.** Entries are recognised by tool id (a source or target,
  whatever its own `kind` or `stream` says), then stream, then logical name or table; a malformed type on a
  derivable column is replaced, and any other malformed field is left in place for the checker to name. A
  shape the re-apply cannot read at all is exit 1 naming the segment, never a traceback.

The acceptance test is the committed reference: for every segment of all seven samples the scaffold's
mechanical fields equal the canned contract's, with exactly the two documented differences above
(`tests/test_contract_scaffold.py`).

## When the orchestrator applies it

1. **Before the analyzer** (single call or batched), `contract_scaffold.py <wf> --prefill` writes each segment's
   `contract.json` **only where none exists** -- a resumed run keeps the analyzer's own contract. The analyzer's
   task says the mechanical fields are pre-filled and its job is the judgment.
2. **After the analyzer**, in the analyze gate's verify callback, in this order:
   `contract_scaffold.py <wf> --apply` re-applies every mechanical field over each contract -- authoritative,
   keeping every judgment field, nullability, keys, row estimates, other keys the analyzer added, and `target`
   exactly as written (a raised or missing target is still `checkTargets`' to report); a contract is rewritten
   only when something changed. Then the existing target check (`target-missing:` / `target-mismatch:`) and
   seam check (`seam-mismatch:`) keep their order and reasons; then `contract_check.py <wf>` -- a refusal is the
   park reason `contract: <first problem>`. In a batched run each step takes `--segments <the batch's
   segments>`.
3. **A tier-T3 workflow** has no contracts: `contract_scaffold.py <wf> --prune-unjudged` removes every contract
   that carries none of `row_relation`, `ordering`, `tolerances`, `parity_risks`.

The analyzer may run `python scripts/contract_check.py <wf>` and `python scripts/check_seams.py <wf>` itself
(its `ROLE_SCRIPTS`; in a batch, `--segments <segment>` one segment at a time -- a comma is not a plain
token to the policy), and is told to fix what they report before it finishes.

## The checker's rules

`scripts/contract_check.py <wf> [--segments …]` prints one line per problem to stderr, each naming the segment,
the field and what is expected (`seg_01: outputs[0] (target tool 7).stream is "sales_summary"; expected
"6_Output"`), and exits 1; 0 when every contract passes, 2 on a usage error. It writes nothing.

- **Mechanical agreement** with the scaffold: `workflow`, `segment`, every mechanical key of every entry, the
  entries themselves (an invented one and a missing one are both named) and their order, column names in order
  and types (with the two accepted spellings above), `output` an exact copy of `outputs[0]`, and no key of
  another entry kind (a mapped source carrying a `table`). An empty `inputs`/`outputs` list is one problem.
- **Shape of what scripts read:** every column's `nullable` a boolean; every entry's `keys` a list of its own
  columns; an input's `expected_rows` (optional) `{"min", "max"}` integers with `0 <= min <= max` and `large`
  (optional) a boolean; `target` one of `sql`, `snowpark`, `manual` and never above the proposal.
- **Judgment well-formed:** `row_relation` one of `1:1`, `filter`, `aggregate`, `expand`; `ordering` exactly
  `{"keys", "alteryx_deterministic", "order_dependent_columns"}` -- `keys` any column the segment carries (an
  input's, a tool anchor's or an output's: `wf_0003/seg_02` orders by `POSTED_DT`, which its Summarize drops),
  `alteryx_deterministic` a boolean, `order_dependent_columns` output columns; `tolerances` keyed by output
  column, each `{"float_abs", "float_rel"}` non-negative numbers (`compare.py`'s per-column override);
  `normalizations` `"trim:<COLUMN>"` / `"upper:<COLUMN>"` on output columns (`compare.py`'s directives);
  `parity_risks` entries exactly `{tool_id, class, note}` naming a tool of the segment (or `<macro>/<tool>` for a
  tool its macro's own sub-DAG has), a class of `lib.vocab.DIFF_CLASSES` and a non-empty note; tolerance values
  `compare.py` can read with `float()`.

## An output's `keys` that repeat in the golden data

The checker cannot see the data, so it cannot tell whether an output's declared `keys` identify a row;
`compare.py` can, and does, per golden set (live hardening, Task L11). When the declared keys repeat in the
**expected** (golden) rows of a stream, that stream is compared exactly as a stream with `"keys": []` -- the
row multiset with nearest-match pairing, the same tolerances, checks and approvals -- and its report carries
the advisory `checks.<stream>:<kind>.keys_not_unique`, e.g.
`{"keys": ["ORDER_ID"], "duplicate_groups": 1, "examples": [{"key": {"ORDER_ID": 103}, "rows": 2}]}`, plus
a note in `normalizations_applied` ("keys not unique: 3_F declares keys [ORDER_ID] but 1 key value repeats in
expected; compared without keys -- revisit the contract's keys"). The advisory is never a failure and never
`needs_human` by itself: the keyless verdict stands (`PASS` when the multisets match within tolerance, `FAIL`
with the keyless path's own clusters otherwise). Keys that are unique in expected but repeat in the
translation's output are still a `LOGIC` cluster ("duplicate keys in actual"). The live case: wf_0001's
excluded-orders stream `3_F`, declared `keys: ["ORDER_ID"]`, whose `edge` golden set holds two identical rows
with `ORDER_ID` 103 -- until Task L11 a `GOLDEN_DATA` cluster that parked a correct translation. A stream
whose real output may repeat an id should declare `"keys": []`, or a key that is unique.

## The retry is told why

`runAgent`'s one retry after a failed verify (every stage, `missing-output`) now ends its task with a fixed
sentence -- "Your previous attempt did not pass the orchestrator's checks; fix exactly these problems:" --
followed, inside a data fence, by the recorded reason and, where the stage has one, the checker's report
(`contract_check.py`'s or `check_seams.py`'s lines, less one that only repeats the reason). The fence is
Task F's: a fixed data sentence outside it,
every line escaped (controls, line and paragraph separators, bidirectional overrides), a fence longer than any
backtick run inside, redacted and bounded (`orchestrator/feedback.ts`). A first attempt, and a retry after a
timeout, carry no such block.

Nothing here has run on Snowflake, Alteryx or a hosted model: the scaffold and the checker compare JSON the
pipeline derived with JSON an analyzer wrote.
