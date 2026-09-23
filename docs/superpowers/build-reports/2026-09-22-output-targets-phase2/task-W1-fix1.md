# Task W1 — fix round 1 (rulings on the task review of ff25202)

The review (opus) ran 23 adversarial chains: 20 behave correctly, including composition errors per-segment validation
misses, real-schema type checks both ways, tolerance parity between boundaries and finals, and set precedence. Your brief
corrections and all five design additions are ACCEPTED. Three Important findings produce a wrong PASS/VALIDATED of the
stitched whole — the outcome the user most wants to avoid — plus seven Minors. Apply every ruling RED-first; the
reviewer's probes are in the scratch folder `scratchpad\p2-rev-W1\` (`harness.py`, `case_*.py`, `crash_window.test.ts`,
`stale_report.test.ts`) — turn the relevant ones into committed tests.

## I1 — a crash inside the chain's fixer round must not let unchecked code reach VALIDATED (stages.ts ~905-906, ~946)
The manifest still says the segment PASSed while the fixer rewrites it; a resume skips that segment's compile_check,
review, per-segment validation (and `render_snowpark.py` for Snowpark) and runs only the chain. RULING: before the fixer
round starts, remove the segment's PASS (`delete m.segment_status[seg]`, or a non-PASS marker the resume path treats as
"re-run this segment") and SAVE the manifest; the resume then re-runs that segment's full gate sequence. Test: the
reviewer's crash-window probe (crash at compile_check, reviewer and validator of the fixed segment) — after resume the
segment's full sequence runs again and the workflow is VALIDATED only after it.

## I2 — the chain must expose order dependence (handoff.py:81; docs large-workflows.md :65, :136-137; validate_workflow.py:10)
Snowflake tables are unordered; a migrated procedure that relies on input row order is non-deterministic in production.
The sorted hand-off hides it at Snowpark seams (reviewer cases d2b, d4: false PASS). RULING (goes beyond the plan's
Step 3 — the plan's sort was the defect):
- First chain run: hand rows off in the backend's physical order (no sort).
- Idempotency re-run: present every segment's inputs in REVERSED physical order — rows handed to a Snowpark segment
  reversed, and SQL-visible input/work tables rewritten in reversed order before the segment runs (e.g.
  `CREATE OR REPLACE TABLE … AS SELECT * FROM … ORDER BY rowid DESC` on DuckDB, or the equivalent your backend supports) —
  so any order-dependent segment produces a different result and the chain is non-idempotent → `boundary` at that
  segment (your design addition 1). The multiset comparison of the two runs is unchanged.
- Correct the docs: what the chain now catches (order dependence at any seam, SQL or Snowpark), and what it still cannot
  (an order dependence that happens to give identical results under both orders).
- Run the chain on EVERY committed sample afterwards. If a canned procedure now fails because it relied on physical
  order, that is a real defect in the canned answer key: fix the canned artefact minimally (an explicit ORDER BY / sort,
  as the cookbook teaches), say so in the report, and re-run its per-segment validation and the e2e tests. (This widens
  your file ownership to `samples/**/canned/**` for that purpose only.)
Tests: d2b and d4 become FAIL (non-idempotent, boundary at the order-dependent segment); d1a/d2a too; the committed
samples PASS.

## I3 — a vanished Snowpark output is a missing table, never a stale copy (validate_workflow.py:165-167)
When `table_from_snowpark` returns None, drop the backend relation (`DROP TABLE IF EXISTS`) so the judge reports a
missing table. Test: the reviewer's case k (Snowpark drops its merge target whose targets_before equals the golden) →
FAIL, missing table.

## Minors
- M1 `stages.ts:896-905`: delete `validation_workflow.json` before every chain call; exit 1 with no report →
  `chain: script-error`. Test with the stale-report probe.
- M2 `validate_workflow.py:239-240`: when the idempotency re-run raises, report the raise (`error` names the segment) and
  blame the raising segment (`boundary`, `stream: null`), not the first relation.
- M3 `stages.ts:558,561`: a chain-triggered fixer round's task text points at `validation_workflow.json`'s
  `first_divergence`, says the segment's own `validation.json` PASS is expected, and drops the contradictory "change only
  what their diagnosis points at" sentence for this case.
- M4 `stages.ts:901`: the second-failure reason names both: `chain: <seg> <stream> after 1 fixer round on <fixed seg>`.
- M5 `tests/chain_fixtures.py`: add a KEYED drift fixture (the reviewer's h3/h4 pair) so a tolerance mismatch between
  boundaries and finals would be caught.
- M6 `stages.ts:896`: on exit 0 with `needs_human: true`, park like `migrateSegment` does (defensive).
- M7 `validate_workflow.py:218`: give `ChainError` a typed `entries` attribute.

## Report
Append "## Fix round 1" to `task-W1-report.md` (including the per-sample chain verdicts after I2, and any canned fix).
Commit as `wip: fix round 1 (I1, I2, I3, M1-M7) — <what>`. Same worktree and rules as before.
