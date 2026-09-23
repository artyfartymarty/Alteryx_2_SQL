# Taking a real workflow through this pipeline

Everything else in this repository is proven against synthetic samples and two local doubles for
Alteryx and Snowflake (`docs/reference/output-targets.md` §"Honesty note", the repo-wide one in
`README.md` §2). This page is the how-to for the one thing those samples cannot exercise: a real
company's real `.yxmd`/`.yxwz`/`.yxzp` exports, and the one step in the whole pipeline that
genuinely needs a person and a real Alteryx install — capturing golden data.

**A real Alteryx engine run has never been exercised by this repository.** Every claim below about
what `AlteryxEngineCmd.exe` does is read from `scripts/inject_outputs.py`'s own contract and public
Alteryx documentation, not from an observed run. Treat the capture step (§3) as unverified until a
person has actually run it once and it produced the CSVs this pipeline expects.

## 1. Survey the corpus first

Before any single workflow is touched, point `scripts/survey_corpus.py` at the directory the
company exported (any layout — every `.yxmd`/`.yxwz`/`.yxzp` anywhere under it counts as one
workflow):

```bash
.venv/Scripts/python.exe scripts/survey_corpus.py <path to the export> \
  --out survey.md --json survey.json
```

This never touches `workflows/` and never runs Alteryx — it is `parse.parse_file` (plus
`scripts/segment.py`'s and `scripts/target_check.py`'s own deterministic rules) run straight over
whatever is on disk, so it works on a directory nobody has prepared for this pipeline at all. Read
`survey.md` for:

- **Parse status** per workflow — an `error` row names the exception, never crashes the survey.
- **Tool counts by target class** (`sql`/`snowpark`/`manual`/`unknown`) and a **proposed tier**
  (`T1`/`T2`/`T3`, the same vocabulary as `docs/reference/output-targets.md` §1, extended here to
  also send a workflow with any `unknown` node to T3 — a plugin the parser cannot name is exactly
  the case that needs a person before anything downstream can be trusted).
- **Unknown plugins, by name**, and **unresolved macros** (a `.yxmc` the survey could not find next
  to its workflow) — the two things that block a clean parse of a *specific* workflow.
- A **proposed output kind** (`procedures` or `dbt`, honoring `--prefer`) and its **dbt blockers**,
  computed with the same rules `scripts/target_check.py` uses once a workflow is actually in the
  pipeline (`target_check.expand`/`classify`/`dbt_blockers`, reused — not reimplemented — because
  the two must never disagree on what blocks dbt).
- A **segment-count estimate** from `scripts/segment.py`'s own defaults.
- A **corpus-wide plugin frequency table**, sorted by how often each plugin appears (ties broken by
  name) — this is the priority order for the next section, and for what the cookbook covers first.

## 2. Teach the parser the plugins that matter

Work down the plugin frequency table. For a plugin that is a normal Alteryx tool the parser simply
has not been taught yet, add it to `scripts/parsers/plugin_map.py`'s `PLUGIN_TYPES` (and, if its
target class is not the default `sql`, `TARGET_CLASS`) — exactly the table `plugin_map.classify`
already reads, in the contract's own order. **Mark every new entry the same way the Python tool's
already is:**

```python
# verify against your Alteryx version: the Python tool's plugin id is taken from this repo's samples
"AlteryxBasePluginsGui.PythonTool.PythonTool": "python",
```

Plugin id strings have moved between Alteryx versions before; a mapping copied from one install's
export is a guess on another's until someone checks it against the company's own Designer.

For a plugin that is not a normal built-in tool — a third-party or custom plugin `plugin_map.py`
should never claim to fully understand — write a parser-recovery extension instead
(`scripts/parsers/ext/README.md` has the two-handler contract). An extension may describe a tool in
plain language and a confidence score; it must never invent semantics `plugin_map.py`'s reviewed
table would assert as fact.

Re-run the survey after each round of changes; a workflow's `unknown_plugins` list is the
regression check.

## 3. Bring one workflow in

1. **Copy the source.** `workflow_id/source/` gets the `.yxmd` (or `.yxwz`) plus every `.yxmc`
   macro it references, at the same relative paths its `<EngineSettings Macro="...">` attributes
   use — `README.md` §7 covers the mechanics (`scripts/parse.py` scrubs connection strings and
   passwords from this XML on parse; nothing else here does, so start from an export that has not
   already leaked one into a comment or annotation).
2. **Parse and segment.** `scripts/parse.py <wf> --check`, then `scripts/segment.py <wf>` — the
   same two steps `survey_corpus.py` ran in memory, now writing `workflows/<wf>/parsed/` and
   `workflows/<wf>/segments/` for real.
3. **Interactive intake.** `scripts/intake_touchpoints.py <wf>` then
   `scripts/intake_prompt.py <wf> --interactive` — `README.md` §5 is a full worked transcript of
   this exact prompt (the yxdb-to-table question, and why a careless Enter never accepts an
   unverified output guess).
4. **`scripts/target_check.py <wf>`** writes `segments/targets.json`: the proposed target per
   segment and the workflow's output kind, the same decision the survey previewed in §1 but now
   grounded in the real `intake/mappings.yaml` (write modes and keys a person actually confirmed,
   not the survey's un-confirmed defaults).

At this point the workflow is ready for the orchestrator, exactly like any sample under
`samples/`.

## 4. Golden data — the one step that needs a real Alteryx install

There is no capture tool here that runs Alteryx; `scripts/inject_outputs.py` only prepares for and
imports the result of a human doing so:

```bash
# 1) Instrument: clones source/*.yxm* with an extra Output Data tool after every input, every
#    segment boundary and every final output, writes source/*.instrumented.yxmd plus
#    golden/capture_map.json, and prints the AlteryxEngineCmd.exe command line to run.
.venv/Scripts/python.exe scripts/inject_outputs.py <wf> --capture-dir <a capture directory>

# 2) A PERSON runs that printed command against the instrumented copy, on a machine with a real
#    Alteryx install and a real license -- this pipeline does not and cannot do this step.
#    (the printed command line names AlteryxEngineCmd.exe; no script here invokes it)

# 3) Import: reads the .yxdb captures out of --capture-dir and writes them as typed CSV under
#    golden/ (dag-contract §4 "output").
.venv/Scripts/python.exe scripts/inject_outputs.py <wf> --capture-dir <the same directory> --import-set normal
```

Credentials for whatever the workflow's Input Data tools read from are the running person's own to
supply to Alteryx at that point — nothing in this repository asks for, stores or forwards them.

Repeat the instrument/run/import cycle per golden set the workflow needs
(`normal`/`period_end`/`empty`/`edge`, `docs/spec/00-README.md`'s vocabulary) — every set is a
separate real run.

## 5. Run the orchestrator against real golden data

Set `golden.producer: "alteryx"` in `orchestrator.config.json` before running the orchestrator
against this workflow. With that set, the orchestrator's `golden` stage never calls
`scripts/dev/alteryx_sim.py` (the local double every sample above uses) — it stops with `BLOCKED`
and prints the exact two-step `inject_outputs.py` sequence from §4 while `manifest.json` records no
golden set. Each successful `--import-set` records its set in `manifest.json`'s `golden_sets` (import
`normal` first). A `BLOCKED` stage is parked, so a plain re-run does not retry it: once a person has
run the capture and imported it, resume with `--from-stage golden --only <wf>`.
Everything after that stage — translate, validate, document, PR — is the same pipeline the six
committed samples already exercise, now checked against real Alteryx output instead of the
simulator's model of it.

## 6. Review

Nothing above changes what a passing `VALIDATED` workflow proves: the generated SQL (or Snowpark
procedure, or dbt project), run on DuckDB, matches the golden data within configured tolerances.
With `golden.producer: "alteryx"`, that golden data is real Alteryx output for the first time in
this repository — the offline check is stronger, but it still proves nothing about a real Snowflake
account (`docs/reference/output-targets.md` §6 lists what that gap is). A person still reviews
every generated artefact before it ships, exactly as the pipeline's PR step already asks.
