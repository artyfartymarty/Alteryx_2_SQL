# Large workflows

A workflow above a handful of tools is migrated in chunks, not as one giant unit, so that no single
piece of work overflows a translator or analyzer agent's context. `scripts/segment.py` cuts the
workflow into segments sized for one context each; other pieces of this design land here as later
tasks build them. This page collects those mechanisms as they arrive; today it covers segment
sizing, the seam check, batched analyzer and stitched analysis that keep the analyzer within one
context, the compaction memory aid for a session whose context fills up anyway, and the chain test
(a whole-workflow validator across segment boundaries).

## Segment size

Tool count alone under-weights a handful of tools with enormous configs — a Formula tool with
hundreds of expressions counts the same as a lone Select. `scripts/segment.py` also keeps every
segment under a **prompt-size budget**, measured in characters, alongside its existing
`min_tools`/`max_tools` tool-count budget.

**The estimate.** `segment.prompt_chars(node)` is the length of the compact JSON of that node's
`type`, `config` and `meta` (`sort_keys=True`, no whitespace) — the fields that actually reach a
translator's prompt for that tool. `annotation` and `raw_config` are excluded: the first is
display-only, the second is redundant with `config`. A group's estimate, `size_chars`, is the sum
of `prompt_chars` over every tool in it. **Characters are an estimate, not a token count** —
`tokens ≈ chars / 4` is the rule of thumb this repository uses elsewhere, but it is a rough
conversion, not a measured one; nothing here has run against a real hosted model. The default budget
is deliberately conservative and is meant to be calibrated later against real `assistant.usage`
token counts from a live run.

**Behaviour.** The budget changes three of the existing steps:

- **Splitting.** A group can need splitting even when it is comfortably under `max_tools`: if its
  `size_chars` exceeds `max_prompt_chars`, it is still cut at the best bridge, balanced by estimated
  characters rather than by tool count (a group split for being tool-heavy keeps balancing by tool
  count instead — the two reasons for splitting balance on their own measure).
- **Merging.** Two undersized neighbours are never merged if doing so would push their combined
  `size_chars` over the budget, even when both are well under `min_tools` and the merge would
  otherwise be legal on tool count alone.
- **The unsplittable case.** A group that stays over budget because every remaining bridge is
  protected (an ordering dependency, a join's own inputs) gets the same "stays above its size cap"
  warning `scripts/segment.py` already produces for a tool-count overflow, naming the character
  estimate as well. A single tool whose own `prompt_chars` alone is already over the budget can never
  be fixed by splitting, so it gets its own warning regardless of how its group ends up:
  `tool <id> alone is estimated at <n> characters, over segmentation.max_prompt_chars <budget>`.

**The knob.** `max_prompt_chars` defaults to `segment.DEFAULT_MAX_PROMPT_CHARS` (60 000 characters,
≈15 000 tokens) and resolves the same way `min_tools`/`max_tools` already do against a workflow's
manifest, with one more fallback layer beneath it: a CLI `--max-prompt-chars N` beats
`manifest.json`'s `segmentation.max_prompt_chars` beats `mappings/global.yaml`'s
`segmentation.max_prompt_chars` (a new top-level `segmentation:` block there) beats the default.

**The pin.** Every committed sample workflow segments exactly as it did before this budget existed:
their whole-DAG character estimates measure roughly 1,250–7,200 characters and their largest single
node roughly 1,200 characters, all far under the 60,000-character default, so none of them trigger a
character-driven split, a blocked merge, or an over-budget warning. `tests/test_segment_prompt_size.py`
pins this for every workflow under `workflows/` alongside the budget's own behaviour on synthetic,
deliberately oversized DAGs.

## Batched analysis and seams

Segments sized for one context each do not by themselves keep the **analyzer** within one context:
it classifies every tool of the workflow and writes every segment's contract, so for a large
workflow one analyzer call can still overflow. Above a character budget the analyzer therefore runs
batch by batch, and the one thing batching could break — two contracts written in different calls
disagreeing about the stream between them — is checked by code, never by a model.

**Seams.** A seam is one work stream crossing a segment cut: the producer contract's `outputs[]`
entry (`kind: "work"`) and the consumer contract's `inputs[]` entry that carries its `stream`.
`scripts/check_seams.py <wf> [--segments seg_01,seg_02]` holds every seam to this:

- same `table` and `stream`, and exactly one segment writes that table — the one the consumer's
  `from` names, in an earlier wave than the consumer;
- the same columns in the same order, the same type **family** per column (`types_map.type_family`,
  the function `compare.py` judges a schema with: `NUMBER(19,2)` against `NUMBER(38,0)` agrees,
  `FLOAT` against `NUMBER(38,0)` does not) and the same nullability;
- the same keys;
- a stream the segment sub-DAGs (`segments/<seg>/dag.json`) show crossing into a segment is declared
  in that segment's `inputs[]` at all;
- across the workflow, no table is written by two work outputs.

It writes `segments/seams.json` (`ok`, every seam with its `status` and every one of its
`problems`, and the duplicates) and exits 0 when every seam agrees, 1 on any mismatch — the first
stderr line is `seam-mismatch: <producer>-><consumer> <stream>` — and 2 for a usage error or a
crash. `--segments` restricts the check to seams whose consumer is listed; their producers must
still exist. Every seam of every committed sample agrees
(`tests/test_check_seams.py::test_every_committed_sample_has_clean_seams`).

**Where the orchestrator runs it.** In the analyzer's verify callback, after the target check: a
mismatch makes the analyzer retry once, and a second one parks analyze `NEEDS_HUMAN` with
`reasons.analyze = "seam-mismatch: <producer>-><consumer> <stream>"` (exit 2, or an exit 1 that
left no mismatch in the report, is `script-error`). The report is deleted before every call, so a
reason always comes from the call just made.

**When it batches.** After `target_check.py`, the orchestrator runs
`scripts/plan_batches.py <wf> --budget-chars <analyzerBudgetChars>` (`orchestrator.config.json`,
default 60 000 characters, ≈15 000 tokens). Each segment's estimate is
`prompt_context.segment_detail_chars` — the characters of its tools' DAG-summary lines and its
touchpoint lines, exactly the detail a batch render carries for it — and every batch also pays for
`prompt_context.render_global`: the workflow map (one line per segment: its tools, the streams it
reads and from whom, the streams it hands on and its Output tools) and the target proposal.
Consecutive waves join a batch while `render_global` plus the batch's detail stays within the
budget; a batch closes at a wave boundary, never inside a wave, so every producer a batch reads from
is in an earlier batch or in the batch itself. A single wave over the budget on its own still gets a
batch, with a warning in `segments/batches.json` (logged by the orchestrator): no cut between waves
can make it smaller. The plan is a pure function of the workflow's files — the same inputs give the
same `batches.json` byte for byte (`tests/test_plan_batches.py::test_batches_are_deterministic`).
**Characters are an estimate, not a token count** (tokens ≈ characters / 4); the estimate leaves out
a batch render's header, its three batch-only sections' headings, data sentences and fences, the
producer contracts, the task text itself and whatever the model reads with its own tools, so the
budget is deliberately conservative and meant to be calibrated against a real model's usage. The
rendered context itself never exceeds the budget: `prompt_context.py` truncates deterministically,
detail first.

**One batch is today's single call.** A workflow that fits is ONE batch, and ONE batch runs exactly
as before: one analyzer call with the whole-workflow context, `check_seams.py <wf>` added to its
verify callback and nothing else. Every committed sample is one batch under the default budget (their
estimates measure roughly 1,100–2,700 characters;
`tests/test_plan_batches.py::test_every_committed_sample_is_one_batch_under_the_default_budget`), so
an extra session is only ever paid for above the budget; the tool-call budget and the retry policy
apply to every batch unchanged.

**What each batch's call gets.** `prompt_context.py <wf> --role analyzer --batch batch_NN
--budget-chars <analyzerBudgetChars>` renders, under the same fence, escaping and hard budget as
every other inline context: the workflow map and the target proposal (the whole workflow, in every
batch), the **producer contracts at this batch's input seams** — for every stream the batch reads
from a segment in an earlier batch, that producer's `outputs[]` entry exactly as the earlier call
wrote it, as compact JSON — and then full detail (DAG-summary and touchpoint lines) for this batch's
own segments only. The task names the batch and its segments.

**What each batch may write.** The analyzer's policy lane narrows to the batch (`ctx.batch` →
`PolicyOptions.analyzerBatch`): the `contract.json` of each of its segments, `analysis/<batch>.md`
and `analysis/<batch>.unsupported.json` — never another batch's contracts, never `analysis.md`,
`unsupported.json` or `manifest.json`, never another workflow's files. The lane is enforced by the
policy, not by the prompt (`orchestrator/test/policy.test.ts`, "the analyzer in a batch writes
only its batch's contracts and fragments"). Each batch's verify callback requires its two fragments
(the `unsupported.json` one with a T1, T2 or T3 `tier`) and, unless a fragment has made the workflow T3, every contract of the batch, no raised target
(`target-mismatch: …` / `target-missing: …`, as for one call) and every seam INTO its segments
(`check_seams.py <wf> --segments <its segments>`); since live-hardening Task L3 the batch's contracts
are also re-applied from the scaffold first and checked by `contract_check.py <wf> --segments <its
segments>` last (`docs/reference/contracts.md`); one retry per batch, then the same park reasons as
one call. `analysis/` is emptied when a batched analyze starts, so a fragment from an earlier plan is
never stitched.

**Stitching.** When every batch has passed, `scripts/stitch_analysis.py <wf>` — never an agent —
writes `analysis.md`: `# <wf> analysis (stitched from N batches by scripts/stitch_analysis.py)`, then
per batch, in segment order, `## batch_NN: segments …` and that batch's fragment verbatim; and
`unsupported.json`: the highest tier of any fragment (T1 < T2 < T3) and every `unsupported` /
`unknown` entry once per tool. It refuses — exit 1, nothing written, `reasons.analyze = "stitch"` —
anything but every segment of `order.json` exactly once and in order across the batches, and a
fragment that is missing, empty, or carries no valid tier. The orchestrator then reads the tier
from the stitched `unsupported.json` (both paths read it from there), runs the target check over
every segment to decide `output_kind`, and finishes analyze exactly as the single call does.

**What this does not prove.** The seam check compares two contracts; it says nothing about whether
either matches what the translated procedures really write — that is the chain test's job (below).
The character estimate has not been calibrated against a real model: nothing here has run against a
hosted model, and a batch that still overflows a real context is not detected by these scripts — the
compaction events below (Task W4) are the first thing that would observe it, once a session actually
runs against a real model.

## Compaction

Segment sizing, the batch budget and the inline-context budget (`PROMPT_CONTEXT_CHARS`) all bound
what an agent is TOLD to read up front; none of them bounds what it reads afterwards with its own
tools, or how long a session runs before the SDK itself has to compact the conversation to keep it
under the model's context window. When that happens mid-session, whatever the agent was tracking in
its own head — which tool it last read, what it had already decided — can be lost even though
nothing on disk changed. `CopilotRunner.run` (`orchestrator/runner.ts`) turns that event into one
short nudge back to the files that are still there.

**The SDK events (spike fact S5).**
`node_modules/@github/copilot-sdk/dist/generated/session-events.d.ts` defines
`"session.compaction_start"`, `"session.compaction_complete"` (`data.success: boolean`, plus
`data.preCompactionTokens` and `data.tokensRemoved` when the SDK reports them) and `"assistant.usage"`
(`data.inputTokens`); a session hands out `on<K extends SessionEventType>(eventType: K, handler) => ()
=> void` (`session.d.ts:190`) and a fire-and-forget follow-up through
`send({ prompt, mode: "enqueue" | "immediate" })` (`types.d.ts:2791`). Right after `createSession`,
`CopilotRunner.run` subscribes to both event types for the session it just opened, and unsubscribes
both in the same `finally` block that already disconnects the session and records its metrics —
covering the normal, timeout and crash exit paths alike.

**The notes file.** Three roles keep a running notes file, re-read on the orchestrator's say-so, not
their own: intake (`workflows/<wf>/notes/intake.md`), analyzer (`workflows/<wf>/notes/analyzer.md`,
the same one lane whether the analyzer runs once or once per batch) and fixer
(`workflows/<wf>/notes/fixer.md`, whichever form of its task is running — one segment, a Snowpark
segment, or the whole dbt project). `CopilotRunner.run` creates `workflows/<wf>/notes/` itself
(recursive, idempotent) before that role's session starts, since no role's policy lane allows
creating a directory (Task N1: a live intake session otherwise tries PowerShell `New-Item`/`md` or a
`python -c` makedirs call and is correctly denied, at the cost of tool calls); a failure to create it
is logged and the session still runs. Each role's own task text (`orchestrator/stages.ts`) ends with a
fixed sentence naming its file, saying plainly that the notes are not the record, and saying the
directory already exists: *"Keep your decisions and open items in workflows/<id>/notes/<role>.md as
you go; the durable record stays in the contract and the files you write. The directory already
exists; write the file with your file-writing tool; do not create directories."*
`orchestrator/policy.ts` opens exactly that one file as one more write lane for that role, on top of
(never instead of) its ordinary lanes — including a batched analyzer's narrowed lane and a dbt-scope
fixer's narrowed lane, both of which still admit `notes/analyzer.md` / `notes/fixer.md`; directory
creation by an agent stays denied for every role, whatever the tool.

**The reminder.** On every `session.compaction_complete` with `data.success === true`, whatever the
role, the compaction is counted (below); if that role is one of the three above, `CopilotRunner.run`
also sends one short, fixed follow-up — `notesReminder(role, wfId)` — with `mode: "immediate"` (never
queued behind whatever the agent does next), asking it to re-read its notes file and the files it has
already written before continuing. The reminder never contains anything the workflow itself wrote:
only the fixed sentence and the notes path, so a compacted session cannot be steered by data instead
of the orchestrator. A **failed** compaction (`data.success === false`) is logged and nothing else —
not counted, and no reminder is sent, since re-reading a notes file could not help with a compaction
that did not happen.

**Metrics.** Every session records into `manifest.metrics.<role>`, alongside the existing `toolCalls`
and `lastMs`: `compactions`, the count of successful compactions this role has seen, cumulative
across every session of that role exactly as `toolCalls` already is (a crashed or retried session's
count is never lost); and `peakInputTokens`, the highest `assistant.usage.data.inputTokens` this role
has seen, kept as a maximum across sessions rather than summed — a later, smaller session never lowers
it. `peakInputTokens` is what the batch and prompt-context character budgets above are meant to be
calibrated against; the live test (Task H) records the ratio between the character estimates and
this number from a real run.

**What is and is not guaranteed.** The subscription, the reminder and the metrics are real code,
exercised by `orchestrator/test/runner.test.ts` against a fake session that fires the same event
shapes `session-events.d.ts` declares — not against a real Copilot session. `MockRunner`, the offline
replay path, never creates a session at all: it writes no notes file and fires no compaction event,
so a canned run's `manifest.metrics.<role>` carries no `compactions` or `peakInputTokens`. Unverified
until a live run (`docs/live-smoke-test.md`, Task H): whether `session.compaction_complete` and
`assistant.usage` actually arrive on the hosted profile and on BYOK, in what order and how often;
whether the model receiving `notesReminder` actually re-reads the file rather than just acknowledging
it — that is model behaviour, not something this code can force; and whether the character-based
budgets above, once compared against real `peakInputTokens` numbers, turn out to be conservative
enough. The notes file itself is always an aid: if a compacted session never re-reads it, or writes
something the contract and the other files disagree with, the contract and the files are what every
later stage checks — the notes are never read by anything but the agent that wrote them.

## The chain test

**Why per-segment validation cannot see composition.** `scripts/validate_segment.py` and
`scripts/validate_snowpark.py` validate one segment at a time, and a downstream segment is fed the
*golden* intermediate Alteryx produced at its input boundary (`load_golden.load_intermediate`),
never its upstream segment's own output. That localises a broken segment precisely, and it hides
every error that only appears once the segments are stitched together: a rounding difference a
human accepted at one boundary that grows past tolerance two segments later, a type that widens at a
seam, a row-order assumption the golden intermediate happens to satisfy. Nothing before this test
ever executed the segments in sequence — not even `procs/master.sql`, which only lists the calls.

**What `scripts/validate_workflow.py` runs.**

```
.venv/Scripts/python.exe scripts/validate_workflow.py <wf_id> [--set NAME]... [--backend duckdb]
```

For every golden set it builds ONE fresh backend holding only the raw golden inputs and
`targets_before` (exactly what `load_golden.load_set` loads), then runs every segment in
`segments/order.json` wave order — waves in order, the segments of a wave in their listed order — on
whatever its upstream segments actually wrote. Right after a segment runs, every output its contract
declares is judged against its golden file with the unchanged `compare.py` (the same arguments
`validate_segment` passes): each work stream is a **boundary**, each target a **final**. For the
first golden set the whole chain then runs a second time from a fresh backend — with every segment's
inputs presented in REVERSED order (next section) — and every relation it wrote is compared as a row
multiset; any difference (or a second run that raises) makes the chain non-idempotent and FAILs it.
`--backend` names the engine: `duckdb` (the local double, the default) or `snowflake` (a real
account, run by a human; `docs/reference/snowflake-backend.md`).

**Row order.** A Snowflake table has no row order, so a migrated procedure that depends on the order
its input arrives in (the first N records, a running value without an explicit `ORDER BY`, a
first-wins dedupe with ties) is non-deterministic in production even when every local run agrees. The
chain exposes that: its judged (first) run leaves every table in the order the local engine wrote it
and hands rows across a Snowpark seam in that same physical order — never sorted, since a sort would
present one convenient order and hide the dependence — while the idempotency re-run shows every segment
each of its inputs in an order DIFFERENT from the one that segment saw on the first run. The raw golden
inputs are reversed. Every upstream stream and every append/merge target the segment writes into is
reversed too — rewritten before a SQL segment runs (`CREATE OR REPLACE TABLE … ORDER BY rowid DESC` on the
DuckDB double; a stream written as a view is first materialised into a table), handed reversed to a
Snowpark segment — UNLESS it already arrives as the exact reverse of the first run's order (an
order-preserving upstream passed its own reversed input on; reversing again would restore the first
run's order). Anything else is reversed, including an input that arrives only partly reordered (an
upstream sort on a non-unique key, a Filter's branches unioned back), so whatever order it arrives in,
the segment never sees the first run's order again — unless that order reads the same backwards (a
single row, or rows that are all identical). An order-dependent segment then computes something else on
the re-run, the chain is non-idempotent, and the divergence is a `boundary` at that segment
(`tests/test_validate_workflow.py::test_a_segment_that_relies_on_its_input_order_is_a_non_idempotent_boundary`
and `…::test_a_partial_reorder_upstream_does_not_hide_a_first_n_consumer`, SQL and Snowpark). What it
still cannot see: an order dependence that yields the same result under both presentations (e.g. a
"first N" that happens to pick the same rows from both orders, or an input whose order reads the same
backwards), and any order dependence that shows only on a golden set after the first, which runs once.

**Row order on `--backend snowflake`** (chain and dbt re-runs alike). A real account has no physical
row order to reverse, so the re-run perturbs the only order it controls: its fresh sandbox INSERTs the
raw golden inputs and every `targets_before` table in REVERSED order, and nothing is re-presented
between two segments. This is weaker than the local reversal — Snowflake promises no scan order either
way and may hand back the rows in the same order whatever order they went in — so an order dependence
the local chain exposes may pass on the account. The local run is the order check; the Snowflake run
is the engine check
(`tests/test_snowflake_validators.py::test_the_snowflake_rerun_loads_raw_inputs_in_reversed_insert_order`).

**Exit codes.** Exit 0 when the chain PASSes (`PASS` or `PASS_WITH_ACCEPTED_DIFF`), 1 on FAIL, 2 for a usage error or
a crash — a missing `order.json`, contract, procedure, mappings file or golden file, or no golden
sets; any stale chain report is deleted before anything else runs, so a usage error leaves none.

**The report.** `workflows/<wf>/validation_workflow.<set>.json` per set and
`workflows/<wf>/validation_workflow.json` for the workflow, in the same shape as a segment's
`validation.json` (`verdict`, `checks`, `diff_clusters`, `idempotent`, `idempotency_diff`,
`needs_human`, `sets`, …; `segment` is `null`) plus:

| Field | Meaning |
|---|---|
| `workflow` | the workflow id |
| `boundaries` | every work stream judged, in chain order: `{segment, stream, verdict}` |
| `finals` | every target judged, in chain order: `{segment, stream, output, verdict}` |
| `divergence_kind` | `null`, `"boundary"` or `"chain_drift"` (below) |
| `first_divergence` | `null` or `{segment, stream, output, set}` — where the chain first went wrong |

Every diff cluster carries the `segment` and `stream` it came from, and `checks` are keyed
`<segment>:<stream>:<kind>:<output>`. The workflow-level body comes from the worst golden set, except
that a `boundary` divergence in any set outranks a `chain_drift` in an earlier one.

**Classification (ruling R-W1).** In chain order:

- The **first failing boundary** is a `boundary` divergence at that segment and stream. Example
  (`tests/chain_fixtures.py::build_composition`): `seg_01` rounds three prices to cents — 1.005,
  2.115, 0.125 become 1.01, 2.12, 0.13 — and a human accepted that as a `ROUNDING` difference, so
  `seg_01`'s boundary is `PASS_WITH_ACCEPTED_DIFF`. Alone, `seg_02` (`PRICE * 1000`) reads the golden,
  unrounded prices and PASSes; chained, it reads the rounded ones and every value is 5 off, a `LOGIC`
  difference. The chain reports `boundary` at `seg_02` / `3_Output`, not at the final `seg_03` that
  merely copies it.
- A segment that **raises** while chained (a compile or runtime error on its real input, a table its
  upstream never wrote, a value that cannot cross a Snowpark seam) is a `boundary` divergence with
  `stream: null`, and the report's `error` names the segment; every output judged before it is still
  reported.
- A relation that differs between the two runs (**non-determinism**) is a `boundary` divergence at the
  first output in chain order that wrote it, unless a failing boundary or a raise came first.
- Otherwise, if every boundary is within tolerance but a **final** is not, it is `chain_drift` at the
  first failing final. Example (`build_drift`): the same accepted rounding, then `seg_02` sums the
  prices into `TOTALS`: every boundary passes, yet the total is 3.26 against the golden 3.245 — each
  price within tolerance, their sum not. That is accumulated tolerance: whether it is acceptable is an
  approval decision, not a code fix.

**Engines crossing at a Snowpark seam.** A SQL segment runs in the chain's DuckDB backend. A Snowpark
segment runs in a fresh Local Testing Framework session holding its raw inputs and `targets_before`
from golden data; every upstream stream its contract declares and each of its targets' current state
are handed in from the backend, and every output it writes is handed back — all through
`scripts/lib/handoff.py`, the one typed hand-off. It reads the **real** schema on both sides (DuckDB's
catalog through `types_map.duckdb_to_alteryx` going in, Snowpark's `StructType` through
`types_map.snowpark_to_alteryx` coming back) and never takes a contract, so a Snowpark segment that
writes `RECOGNIZED` as a string where its contract says `FLOAT` is a `TYPE` difference at that
segment's boundary, not something the hand-off quietly coerced away. A column type with no Alteryx
counterpart (`BLOB`, `INTERVAL`, lists, structs, …) cannot cross and raises, which the chain reports
as that segment raising. Integer columns come back from Snowpark as `NUMBER(38,0)` — DuckDB's
`DECIMAL(38,0)` — which is Snowflake's own integer type. Rows cross in physical order (see Row order).
An output the Snowpark segment no longer has after it ran — never written, or dropped — is dropped from
the chain's backend too, so it is judged as a missing table, never as the copy (a `targets_before`
state, say) the backend held before the segment ran.

**A dbt workflow** needs no chain of its own: `validate_dbt.py` already runs the whole project, and
every model reads its upstream model's actual table through `ref()`. The same run writes
`validation_workflow.json` from the same per-output compare reports (a failed `dbt run` is a
`boundary` divergence with `stream: null` at the segment owning the first failed model), and
`validate_workflow.py` on a workflow whose manifest says `output_kind: dbt` delegates to it. Its
idempotency re-run (DV7: a second FRESH sandbox, compared as row multisets) loads every raw golden input
and every `targets_before` table in REVERSED row order, the first sandbox keeping file order: dbt runs
every model itself, so the order can only be changed at the inputs, and a model that depends on row
order (a `LIMIT` without `ORDER BY`, a window without a total order) becomes non-idempotent and FAILs as
a `boundary` at its segment
(`tests/test_validate_dbt.py::test_a_model_that_depends_on_row_order_is_not_idempotent`). A dependence
between two models that an order-preserving upstream model hands on unchanged is still exposed, since the
reversal reaches it through that upstream; one that an upstream model re-sorts (fully, or partly — ties,
a union of branches) may not be: dbt runs every model itself, so the order cannot be changed between two
models the way the procedures chain changes it between two segments.

**What the orchestrator does with it.** After every segment of a procedures workflow PASSed on its
own, `stageTranslate` runs `validate_workflow.py <wf>` once, before writing `procs/master.sql`;
translate is `VALIDATED` only when the chain PASSes. The per-segment script calls are unchanged. Any
`validation_workflow.json` left on disk is deleted before every chain call, so the orchestrator only
ever acts on the report of the call it just made.

| Chain result | Route | `reasons.translate` when parked |
|---|---|---|
| PASS | `master.sql`, `VALIDATED` | — |
| `boundary` | ONE fixer round on `first_divergence.segment`, then that segment's own compile check, review and validation, then the chain again. The segment's recorded PASS is withdrawn (and saved) before the round, so a crash inside it leaves the segment to be translated and gated again on resume. The fixer's task points at `first_divergence` in `validation_workflow.json` and says the segment's own `validation.json` PASS is expected | `chain: <seg> <stream> after 1 fixer round on <fixed seg>` if it fails again; `chain: <seg>: <reason>` if the round itself does not PASS the segment |
| `chain_drift` | parks at once — never a fixer task | `chain-drift: <output>` |
| `needs_human` in the report (whatever the verdict) | parks at once | `chain: needs_human` |
| exit 2, or no report written | parks at once | `chain: script-error` |

A dbt workflow runs no extra script: `translateDbt` requires the `validation_workflow.json` that
`validate_dbt.py` wrote to be `PASS*`, and parks `dbt: chain FAIL` otherwise (a missing report
included). A resumed run whose segments had all PASSed runs the chain again (procedures) or re-reads
the report (dbt) before `VALIDATED`.

**What this does not prove.** Nothing here has run on Snowflake or Alteryx: SQL runs on the DuckDB
double, Snowpark in the Local Testing Framework, dbt on dbt-duckdb, and "PASS" means those doubles
agree with the golden data the simulator produced. The chain is only as long as `order.json` says;
idempotency — and with it the row-order check — runs on the first golden set only, as for a segment.
Physical order and `rowid` are the DuckDB double's: reversing them shows a procedure that depends on
order, it says nothing about the order a real account would deliver.
