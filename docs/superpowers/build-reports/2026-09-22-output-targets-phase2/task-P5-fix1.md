# Task P5 — fix round 1 (rulings on the task review of 5fda5f7)

The review approved the redraw: the test is verbatim, every node and edge you drew traces to real code, and both of
your judgment calls hold (the chain-repair round really re-enters the same loop at the fixer step, `stages.ts` ~1129;
dbt's chain gate has no boundary/drift split to draw). Four rulings, all in `README.md` §1 only:

## I1 — the procedures loop shows its compile check (and the Snowpark render step)
`migrateSegment` (`orchestrator/stages.ts` ~772-806) runs `scripts/render_snowpark.py` (Snowpark segments only) and then
`compile_check.py` after every translator or fixer attempt; a compile failure loops straight back to the fixer. The `seg`
subgraph shows only translator → reviewer, which now reads as "only dbt compile-checks" beside `DBTT`. RULING: add a
`compile_check.py` node between the translator (and fixer) and the reviewer with a `compile fail` edge to the fixer,
mirroring `DBTT`; show the render step as a node or an edge label that says it applies to Snowpark segments only. Trace
the exact order in the code before drawing.

## I2 — the stages diagram fits GitHub's width
It renders 5293 px wide. RULING: make the OUTER stages flowchart top-to-bottom (`flowchart TB`) and keep `direction LR`
inside the analyze, per-segment and dbt subgraphs, or any other restructuring that brings the rendered width down to
about 2000 px or less without dropping content. The controller re-renders and measures; aim for the smallest width that
keeps the diagram readable.

## M1 — edge sources are consistent
Source both of `DBTT`'s outcome edges from the subgraph id (`DBTT --> DOUT`, `DBTT --> NH`), the convention `seg` uses.

## M2 — the whole-workflow validator names compare.py
`P6` gains `→ compare.py`, as `P5` does (`validate_workflow.py` calls it).

## Also
N1 has merged into the integration branch since your base: the orchestrator now creates `workflows/<wf>/notes/` before an
intake, analyzer or fixer session. If the one-agent-call diagram has room, show it as the first step for a notes role
(e.g. a note or a `mkdir notes/ (intake, analyzer, fixer)` step before `createSession`); otherwise leave it out. Do not
merge the integration branch; the controller merges.

Keep `tests/test_readme_diagrams.py` green, and if you use a construct the lint does not cover, say so in the report.
Append "## Fix round 1" to `task-P5-report.md` (each item: what changed, which code lines you traced) and list any new
Mermaid construct. Commit as `docs: the procedures loop shows its compile check and Snowpark render; the stages
diagram runs top to bottom`. Run `tests/test_readme_diagrams.py` and `tests/test_handoff_production.py`; the whole suite
is not needed for a README-only change.
