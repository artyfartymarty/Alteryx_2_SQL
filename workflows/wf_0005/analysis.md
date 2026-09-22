# wf_0005 — analysis

Reads `parsed/dag.json` (after parse recovery), `intake/mappings.yaml` and
`parsed/parse_diagnosis.md`. Restates them; adds no claim about behaviour that no artifact
supports, and no number about data — every count, diff and tolerance in this project comes from
`scripts/`.

## Tool classification

**Tier T3 — manual.** Two nodes block the migration and either one would be enough on its own.

| Tool | Type | Class | Rule applied |
|------|------|-------|--------------|
| 1 | `input` | `sql` | Input Data → table read; the source is mapped in `mappings.yaml` |
| 2 | `unknown` | `unknown` | `AcmeAnalytics.Dedupe.DedupeTool` is in no plugin map. `scripts/parsers/ext/acme_dedupe.py` supplies a `behavior` and a `confidence` and deliberately leaves the type `unknown`; there is no cookbook pattern to cite |
| 3 | `run_command` | `manual` | Program spec: `run_command` is T3 by name. A Snowflake stored procedure cannot start a process on a host |
| 4 | `output` | `sql` | Output Data, write mode `overwrite` → `CREATE OR REPLACE TABLE … AS` |

Two of the four data nodes are not `sql`, so `unsupported.json` lists both with
`blocks_migration: true`, the tier is T3 and the status is `NEEDS_HUMAN`. The workflow's terminal
state is `MANUAL`.

### What the unknown tool's `behavior` says, and what it does not

`parsed/dag.json` carries, for tool 2: `type: "unknown"`, the untouched `raw_config`, a `config`
restating its three configuration elements (`key_field` `ACCT`, `keep` `MaxDate`, `date_field`
`UPDATED`), a plain-language `behavior` and `confidence` 0.5.

That reads like a Sort followed by a Unique — one row per `ACCT`, the one with the largest
`UPDATED`. The analyzer does **not** adopt that reading, for the three reasons
`parsed/parse_diagnosis.md` records: the XML says nothing about a NULL date, nothing about a tie,
and nothing about whether the comparison is by date or by full timestamp. Each of those would show
up as a data difference rather than an error, which is exactly the kind of mistake that survives a
review. `samples/wf_0005/README.md` says the `normal` input carries an account with a NULL
`UPDATED` and two duplicate pairs, one of them out of date order, so all three questions are
answerable from the data the moment somebody knows the tool.

## Segmentation

Not run. `scripts/segment.py` proposes cuts for a workflow that is going to be translated; this
one is not, so `segments/` stays empty and no `contract.json` is written. There is no procedure,
no `broken_sql` and no validation for this workflow.

If both blocking tools were ever resolved, four tools against a `min_tools` of 3 would be a single
segment.

## Golden data

`scripts/dev/alteryx_sim.py` refuses this workflow outright: it raises `UnsupportedTool` for an
`unknown` tool and for a `run_command`, and a workflow with either produces no golden data at all
— not even for the tools upstream of it. `manifest.golden_sets` is therefore empty.

Only the `normal` and `empty` golden **inputs** exist under `samples/wf_0005/golden_inputs/`;
`period_end` and `edge` would have nothing to prove. Those inputs are still worth keeping: they
are the data a human will use to answer the three questions above.

## Parity risks

None to record, because nothing is translated and nothing is compared. The risks this workflow
carries are decisions, not differences:

| Tool | Decision a human has to make |
|------|------------------------------|
| 2 | What the Acme Dedupe tool does with a NULL `UPDATED`, with a tie on `UPDATED`, and whether it compares the date or a full timestamp |
| 3 | What replaces `notify.bat` — a Snowflake task, an external function, a step in whatever orchestrates the migrated procedures, or nothing |

## Parse recovery

This workflow reached the analyzer only because the `parser-recovery` agent ran first. The core
parse did not fail — every node was read and every connection resolved — but invariant 8 caps
unexplained `unknown` tools at 10% of a workflow's data nodes, and one of four is 25%, so
`parse_report.json` came back `INVARIANT_VIOLATION`.

`scripts/parsers/ext/acme_dedupe.py` supplied the missing explanation, the re-parse came back
clean, and `parse_report.json` is now `RECOVERED`. The extension ships with a regression fixture,
`tests/parser_corpus/acme_dedupe/`, which a human reviews before the corpus accepts it
permanently. The full diagnosis is in `parsed/parse_diagnosis.md`.
