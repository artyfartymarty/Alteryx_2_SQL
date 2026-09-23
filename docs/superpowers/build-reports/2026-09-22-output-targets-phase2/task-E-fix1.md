# Task E — fix round 1 (rulings on the task review of 6b6fdbf)

Both provisional conditions were MET (explicit-NULL filter form is portable; the 1.005 half-boundary is named as unprovable
locally). The dbt half is solid. Four rulings; apply all, RED-first, then the cookbook tests and the whole suite.

## C1 — every Snowpark snippet on the page must pass the rules (`rule:tool_comments`)
`cookbook/snowpark.md:36,47,76,79,85` and `tests/cookbook_examples/snowpark/{filter,formula}/example.py` write
`# tool 2 (anchor T): …` / `# tool 2 (AMOUNT): …`; `snowpark_rules._TOOL_COMMENT` needs the colon right after the id.
RULING: write `# tool 2: Filter … True branch — …` (the annotation goes after the colon). Add a test,
`test_every_snippet_on_the_snowpark_page_passes_the_rules`, that extracts every fenced `python` block from
`cookbook/snowpark.md`, wraps each fragment that is not already a full procedure in a synthetic top-level
`run(session, src_db, src_schema, tgt_db, tgt_schema, run_id)` (the same wrapper shape a translator would use) and asserts
`snowpark_rules.check_proc_py(...) == []` — so the page can never drift from the gate again. RED first against the
current page.

## C2 — the carry-over golden data must exercise the cancellation reset
`tests/cookbook_examples/snowpark/python_carry_over/input.csv`: the only `CANCELLED=true` row is reached with `deferred`
already 0, so dropping `if bool(row["CANCELLED"]): deferred = 0.0` still PASSes (the reviewer ran that mutant: PASS).
RULING: add a customer whose period before a cancelled period is capped (deferred > 0 carried in), so the reset changes
`RECOGNIZED`/`DEFERRED`; regenerate the expected output through the simulator exactly as the harness does (never by hand).
Add a committed mutation test, `test_the_carry_over_example_catches_a_dropped_reset`: run the harness on a copy of
`example.py` with the reset line removed and assert the verdict is FAIL (with a LOGIC cluster on RECOGNIZED or DEFERRED).

## I3 — the dbt merge example must catch a narrowed `unique_key` at run time too
`tests/cookbook_examples/dbt/merge`: REGION is 1:1 with ID in the fixture, so narrowing `unique_key` to `['REGION']`
still PASSes the runtime compare (only `compile_check`'s static `dbt:model_config` catches it). RULING: change the
fixture data so a region holds at least two IDs (in `history_before` and in the input), regenerate expectations the way
the harness does, and add `test_the_merge_example_catches_a_narrowed_unique_key` (a scratch copy of the project with
`unique_key=['REGION']` → FAIL). Add one sentence to `cookbook/dbt.md` naming both lines of defence: the static
`dbt:model_config` check (keys must equal the contract's) and the runtime compare.

## Spec §9 — name the two mock limits
Add to our spec `docs/superpowers/specs/2026-09-22-output-targets-design.md` §9's Snowpark bullet (never `docs/spec/**`):
the Local Testing Framework does not propagate NULL through `==`/`!=` and does not implement `round()` (decimal narrowing
goes through binary-float rounding), so a NULL comparison and a half-boundary rounding case are not proven locally.

## Not a finding
The dbt page's hook snippet is illustrative (no executable hook example); leave it.

## Report
Append "## Fix round 1" to `task-E-report.md`. Commit as `wip: fix round 1 (C1, C2, I3, §9) — <what>`. Same worktree
and rules as before.
