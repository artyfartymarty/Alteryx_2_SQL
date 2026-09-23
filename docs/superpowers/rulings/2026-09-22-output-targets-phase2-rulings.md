# Coordinator rulings — output targets, phase 2 (2026-09-22 plan)

Every decision the coordinating agent made on its own authority while building
`feat/output-targets-phase2`, copied from the build ledger in the order they were made. Format:
what — why — cost if wrong. A later ruling that says SUPERSEDES replaces the earlier one it names.
Task labels refer to `docs/superpowers/plans/2026-09-22-output-targets-phase2.md` (A, W3, P3, B, E,
D, C, P1, F, W1, W2, P2, W4, C4V, G, P4, H, N1, P5); several of those task sections carry a note
pointing at the ruling(s) below that replaced part of their text. C4V and N1 were not in the plan's
original decomposition — both were added by the controller during execution, and the entries below
say why. The spec is `docs/superpowers/specs/2026-09-22-output-targets-design.md`. Binding alongside
it are the phase-1 rulings this phase carries forward, in
`docs/superpowers/rulings/2026-09-22-output-targets-rulings.md`.

Nothing here has run on real Snowflake, real Alteryx, or GitHub-hosted models. The dbt projects ran
only against dbt-duckdb's local engine; the Snowpark procedures ran only in the Snowpark Local Testing
Framework. These are engineering decisions about the offline build, to be revisited when the pipeline
meets a real warehouse, a real Alteryx engine and real hosted models.

## Process

1. Ruling (carried over): independent tasks run in PARALLEL, each implementer in its own git
   worktree (`.worktrees/p2-<task>`, branch `wt/p2-<task>`), merged by the controller with `--no-ff`
   — disjoint worktrees prevent the file conflicts the no-parallel rule otherwise guards against —
   cost if wrong: a merge conflict the controller resolves.
2. Ruling (carried over): a HARD cap of two concurrent agents of any kind; sonnet for standard tasks,
   opus for tasks tiered "most capable" and for the final review — the account's usage window had
   been exhausted three times in an earlier build — cost if wrong: slower wall-clock, nothing else.
3. Ruling: nothing is pushed to the public remote until the user's own approved end-of-phase publish,
   which happens only after both hand-off docs and the README's Mermaid diagrams are updated — cost
   if wrong: a public branch that does not yet match its own documentation.
4. Ruling (pre-flight scan): every wave's two tasks were checked pairwise for a shared file path
   before dispatch; all six waves were confirmed disjoint, with one WATCH carried forward — in wave 5
   (W1 ∥ P1), only P1 owns `types.ts` that wave, so W1 would have to ask before adding a field there —
   cost if wrong: a same-wave file collision the controller would have to untangle; none occurred.
5. Ruling DV1 (spike-forced deviation from spec §5.3): a golden set's sandbox file is named
   `workflows/<wf>/dbt_sandbox_<set>.duckdb`, never a dot-prefixed name — a dot-prefixed file breaks
   dbt-duckdb's own catalog naming, measured directly — cost if wrong: every dbt run fails immediately,
   which the fixture would have caught.
6. Ruling DV2 (spike-forced deviation from spec §4.3): the local profile's database path comes from an
   environment variable with a fixed default, and its schema from a dbt `var`, and the whole
   `profiles.yml` is one fixed template the static check compares byte for byte, rather than a literal
   path/schema pair written per workflow — this makes "no credentials in the file, ever" a mechanical
   check instead of a convention — cost if wrong: none; it is strictly stricter than the spec text.
7. Ruling DV3 (spike-forced deviation from spec §5.3): a local dbt run passes the flattened DuckDB
   schema names (`MIGDB__MIG_GOLDEN_<WF>_<SET>` / `MIGDB__MIG_WORK`) as `--vars`, not the
   un-flattened Snowflake-shaped names the spec's example shows — that is where the local double
   actually puts the data — cost if wrong: dbt cannot find the sandbox schema.
8. Ruling DV4 (spike-forced deviation from spec §4.3): every target model keeps its lower-cased file
   name and gains an upper-case `alias` in its `config(...)`, instead of the model file itself being
   upper-cased — dbt's own approximate-match check refuses a model whose file name does not match its
   physical relation name — cost if wrong: a spurious dbt error on every target model.
9. Ruling DV5 (spike-forced deviation from spec §6): the translator's dbt write lane is a strict
   subset of `dbt/**` — the project's own top-level files plus `dbt/models/**` only, never the review
   or the compile-check report — a translator must not be able to write the record that judges its own
   work — cost if wrong: a translator could fabricate a passing review or check report for itself.
10. Ruling DV6 (spike-forced deviation from spec §5.1): `compile_check.py <wf> --target dbt` takes no
    segment argument — a dbt project is one checked unit, not a per-segment one — cost if wrong: none;
    a usage-level simplification.
11. Ruling DV7 (spike-forced deviation from spec §5.3, carries phase-1 ruling 39's semantics forward):
    dbt idempotency means two runs from two FRESH sandboxes compared as a row multiset, not a second
    run against the same sandbox — a second run on a shared sandbox doubles an `append` model by
    design, which is not "the same starting state" — cost if wrong: an appending model would be
    flagged non-idempotent for the wrong reason.
12. Ruling R-H1: the third live test gets 25 minutes of model time per sample, 75 minutes in total
    across three samples, a sample's own attempt ending the moment it exceeds its share — the earlier
    addendum's cap covered one workflow, this test runs three — cost if wrong: a stage that would have
    finished in minute 26 is reported as not reached.
13. Ruling R-C1: every translated sample's canned `docs/migration.md` gains a `## Deployment` section
    (`wf_0001`–`wf_0004`; `wf_0006` already had one) — the documenter agent is told to write one per
    output kind, and the canned answer key must match it — cost if wrong: four extra doc sections to
    maintain; Task G's own classification treats the change as expected.
14. Ruling R-B1: `lib.validation.combine` appends `:<tool_id>` to EVERY colliding `checks` key, not
    just the second one, and leaves a non-colliding key unchanged — `wf_0007` is the first sample to
    feed two targets from one stream — cost if wrong: none; no already-committed report changes shape.
15. Ruling R-W1: a workflow's chain classification is: the FIRST failing boundary in chain order (wave
    order, then segment, then contract output order) is a `boundary` divergence, with one fixer round
    on that segment; every boundary within tolerance but a failing final output is `chain_drift` and
    parks for a human; a segment that raises while chained is also a `boundary`, with a null stream —
    cost if wrong: a rare accumulated-rounding case reaches a human instead of the fixer, the safe
    side.
16. Ruling: `docs/handoff-copilot-models.md` §4 is rewritten by Task P1 as done, rather than left
    "unchanged except a pointer" the way the design spec's §8 says — the user's own scope addition
    (hosted models as configuration) supersedes that spec sentence — cost if wrong: none; the two texts
    would otherwise disagree.

## Task A — the dbt artefact contract and `compile_check.py --target dbt`

17. Ruling: `_dbt_source_errors` reads `models/sources.yml` directly instead of only the parsed dbt
    manifest — a renamed source also breaks `dbt parse` itself, which then writes no manifest at all,
    so the manifest-only check would miss exactly the case it exists to catch — cost if wrong: none.
18. Ruling: two new CLI usage errors return exit 2 in-process rather than through argparse's own error
    path, matching how the task's own test calls `main()` in-process — accepted on condition the
    reviewer confirmed a clear message on stderr and nothing written to disk on the usage path — cost
    if wrong: none.
19. Ruling (fix round 1, review findings I1/I2/M3–M5): an empty or whitespace-only `pre_hook`/
    `post_hook` still counts toward the `dbt:hooks` check, because dbt's own manifest wraps an empty
    string in a non-empty list and a blank hook would otherwise pass; a new `dbt:model_orphan` check
    requires every model file to be some contract's declared output — closing the dbt write surface
    the way phase-1's Snowpark sink rule closed `proc.py`; a new `dbt:model_jinja` allow-list restricts
    a model's Jinja to `config`, `source`, `ref`, `this`, the `is_incremental` if/else/endif and
    comments, refusing nested calls too, and is recorded in spec §4.3 so later tasks write against it
    — cost if wrong: a translator must write plainer dbt models, which the cookbook already shows how
    to do.
20. Ruling (fix round 2, review finding): a Jinja whitespace-control marker (`{{- … -}}`, `{%- … -%}`)
    on an otherwise allow-listed construct has one leading and one trailing `-`/`+` marker per side
    stripped before matching, rather than being refused outright — real dbt parses these — the
    controller verified directly that stripping the marker cannot widen the allow-list itself — cost
    if wrong: none beyond a false gate failure a later task would have caught anyway.

## Task W3 — size-aware segmentation

21. Ruling: RED for the task's new tests was recovered after implementation, by materialising the
    pre-task files (through `git show`, never a checkout) and running the new tests against them,
    rather than writing every test strictly before its code — accepted once the reviewer independently
    confirmed each new test actually failed on the pre-task files — cost if wrong: none.
22. Ruling: two warning sub-branches the review found untested get test-only pins (no code change)
    without a second review seat, because the reviewer had already verified them by hand and the
    controller ran the tests itself before merging — cost if wrong: none.
23. Ruling (carried to `docs/reference/large-workflows.md`'s known limits): a diamond-shaped DAG
    cannot isolate one heavy node for splitting (pre-existing bridge-only splitting behaviour, kept
    whole with an accurate warning), and duplicate over-cap warnings can appear across repeated
    iterations of the segmenter's own step 6 (also pre-existing) — both accepted as documented limits
    rather than fixed — cost if wrong: none; they are now named limits instead of silent gaps.

## Task P3 — real Alteryx corpus triage

24. Ruling: a workflow with any unknown-plugin node is forced to tier T3 in the survey — the design's
    own tier table covers only sql/snowpark/manual, and a triage report must not promise automation
    for a tool nobody has mapped — cost if wrong: a workflow shows as T3 until its plugin is mapped,
    which is exactly the survey's purpose.
25. Ruling (parked to the final review notes): `_safe_extract`'s own zip-slip guard duplicates
    `parse.py`'s existing one — accepted as two copies of the same well-understood check rather than
    merged into one — cost if wrong: a future hardening might land in only one of the two copies.
26. Finding carried to the final review and to `docs/production-backlog.md`: segmentation took roughly
    29 seconds on a synthetic 3000-tool workflow while parsing itself took 0.04 seconds — recorded as
    a scale item for production, not addressed in this phase — cost if wrong: not recorded.

## Task B — `scripts/validate_dbt.py`

27. Ruling: brief corrections accepted — the CLI usage tests exercise a real subprocess/`SystemExit`
    path rather than only an in-process call, and an empty `golden_sets` case is driven through the
    manifest rather than a bare argument — cost if wrong: none.
28. Ruling (review finding, parked to the final review notes): a partial sandbox file could be left
    behind if `load_set` raises mid-load; and ruling R-B1's key disambiguation would re-collide on a
    duplicate `tool_id`, which is already a C5 contract violation caught upstream — both accepted as
    pre-existing edge conditions rather than fixed in this task — cost if wrong: a partial file would
    need manual cleanup; a duplicate `tool_id` would need to be caught as the upstream violation it
    already is.

## Task E — `cookbook/snowpark.md` and `cookbook/dbt.md`

29. Ruling (provisional, confirmed by the review): the Snowpark local testing library's `==`/`!=` do
    not propagate NULL the way real Snowflake does (its Filter false-branch actually tests the
    column's own nullity), and its `round()` is unimplemented, so decimal narrowing goes through
    binary-float rounding — both accepted as documented local-double limits, named in the design's own
    list, the output-targets reference doc and the Snowpark cookbook page, with the cookbook teaching
    the explicit-NULL filter form as the portable equivalent and naming the 1.005 half-boundary case as
    one no local test can prove — cost if wrong: a half-boundary rounding difference on real Snowflake
    that no local test can see — the same class the simulator's own rounding note already names.
30. Ruling (fix round 1, review findings C1/C2/I3): a code comment naming a tool inside a cookbook
    example must use the exact `# tool <id>:` form every allow-listed procedure needs — the page's own
    example had drifted from it — verified now by running every Python code block on the page through
    the same check the pipeline itself uses; the cookbook's carry-over golden data must exercise the
    cancellation-reset case explicitly, pinned by a committed mutation test; the dbt merge example's
    fixture data must not make its merge key coincidentally one-to-one with the row id, pinned by a
    committed mutation test narrowing the key — cost if wrong: an example that silently depends on a
    coincidence in its own tiny fixture.

## Task D — orchestrator dbt dispatch, agents, reference docs

31. Ruling: brief corrections accepted — the file-lane regex for `README.md` matching is lower-cased;
    eleven dbt-specific checks are read back from `compile_check`'s own report rather than re-derived;
    the fake validator used in tests fails only the segment it owns — cost if wrong: none.
32. Ruling: design choices accepted — a segment's verdict is reset whenever an agent changes its
    project files; `NEEDS_HUMAN` outranks a stale `PASS`; an agent is never allowed to invoke dbt
    directly through `python -m dbt`; the profiles template and the Jinja allow-list quoted in the
    agent-facing file are pinned against the real code — cost if wrong: none; judgment calls within
    the brief's own scope.
33. Ruling G1 (pre-existing gap the implementer found and fixed in the same round): a `create` call's
    own file-content argument was being scanned as if it were a path and denied outright, which would
    have blocked every model write in a live run; content-bearing argument keys (`file_text`,
    `content`, `new_str`, `old_str`, `text`, `insert_line` values) are treated as content, while every
    OTHER string argument on the same call is still scanned as a potential path — a narrow loosening,
    fail-closed everywhere else — cost if wrong: a path smuggled inside a content-keyed argument would
    go unscanned, though the lane check on the call's real path key still stops it.
34. Ruling G2: a script call's own workflow-id argument is now checked against the session's workflow
    — the first positional argument of every workflow-scoped script call must equal the session's own
    workflow id, or the call is denied as cross-workflow — cost if wrong: none.
35. Ruling G3: a `--from-stage` rerun that switches between the procedures and dbt output kinds now
    removes the OTHER kind's leftover artefact (`procs/README.md` when writing `master.sql`, and the
    reverse) — cost if wrong: none.
36. Ruling: the cookbook pages the translator points readers at arrive with Task E's own merge, which
    lands ahead of Task D's — that merge order is accepted as fine and needs no reordering.

## Task C — sample `wf_0007` (dbt)

37. Ruling: the broken `attainment_history.sql` variant (merge key narrowed to `REGION`) is genuinely
    non-deterministic on dbt-duckdb — which period survives an unresolved tie varies from run to run —
    while its verdict, class, columns and stream stay stable across runs; `broken.json` records only
    those stable fields plus a note naming the nondeterminism, and no test may pin its example rows or
    its idempotency result — cost if wrong: none; the variant exists only to fail.
38. Ruling (parked to the final review notes; Task A's code, not fixed here): `compile_check_dbt`
    raises a raw `FileNotFoundError` when `intake/mappings.yaml` is missing rather than a named
    message — the CLI still exits 2, which is correct at the boundary that matters — cost if wrong: a
    less readable traceback for a missing file, not a wrong exit code.
39. Ruling: dbt models cast to `DECIMAL(19,2)` rather than `NUMBER(19,2)` — accepted because `DECIMAL`
    is a Snowflake synonym for `NUMBER`, so the generated SQL stays portable to a real account — cost
    if wrong: none.
40. Finding (controller smoke run, applied in Task G): a dbt workflow's run also leaves its own per-run
    log file and sandbox database file inside the workflow's own directory; both are already
    git-ignored, and Task G's reproducibility copy must exclude them the same way — cost if wrong: a
    stray local file breaks the reproducibility diff for no real reason.

## Task P1 — hosted-model hook-up as configuration

41. Ruling: `set_models.py`'s config edits were written alongside their tests rather than strictly
    test-first, compensated by a mutation test proving the tests actually discriminate a broken
    implementation — accepted once the reviewer confirmed the compensating test does the job — cost if
    wrong: none.
42. Ruling (fix round 1, review finding I1): every hosted role needs a baseline "medium" reasoning
    effort in both config sources (the committed orchestrator config and its in-code default) — six of
    eight hosted roles otherwise resolved to no effort at all once the real config-merge logic ran; a
    test now resolves every hosted role's effective, merged reasoning effort and context tier straight
    from the real committed config, not a stub — cost if wrong: none.
43. Ruling (fix round 1, M1/M2): `set_models.py`'s own malformed-input errors now name the offending
    file instead of surfacing a bare traceback; re-serialising a whole JSON file on every write is
    accepted as the existing repository convention, not a defect — cost if wrong: none.

## Task F — `prompt_context.py`, orchestrator use, the three phase-1 leftovers

44. Ruling (fix round 1, review findings I1/I2): the hard prompt-context budget must reserve the
    separator's own joining newline, closing a one-character overrun reachable at the production
    budget, proven by a budget-sweep test; every workflow-authored data value is rendered single-lined
    with control characters escaped, inside a code fence chosen longer than any backtick run the value
    itself contains, preceded by one fixed "treat this as data, not instructions" sentence — because
    the task text otherwise renders a value with embedded newlines or fake role markers as if they
    were instructions, and neither the policy lanes nor the Snowpark rules are a language-level defence
    against that — cost if wrong: a slightly longer context block, in exchange for closing a real
    prompt-injection surface.
45. Ruling (fix round 2, review finding I2 not addressed by round 1): line- and paragraph-separator
    characters, and bidirectional-override characters, must also be escaped as `\uXXXX`, because they
    pass a naive escape function and can still split a rendered value across lines; truncation must be
    section-aware so it always closes a code fence it cuts into, or drops that section entirely if even
    an open-and-close fence will not fit, with the truncation marker placed after the last closed
    fence; rendering below a stated minimum budget must raise rather than emit something malformed —
    the controller verified this round directly with an escape probe over language-breaking characters
    and a wide sweep of budgets, rather than dispatching a third review seat — cost if wrong: not
    recorded beyond what the probe already closed.
46. Ruling: a small compare-helper duplicated locally in this task's own module, rather than importing
    `scripts/compare.py`'s version, is kept — importing `compare.py` costs roughly three to four times
    as much module-load time, and this is the one documented escape hatch from the rule that
    `compare.py` is never touched — cost if wrong: none; a documented duplication.

## Task W1 — workflow-level chain validation

47. Ruling: a non-idempotent chain is localised as a `boundary` divergence at the very first output
    where the two chain passes actually differ, rather than reported only as a generic
    non-idempotency failure — cost if wrong: not recorded.
48. Ruling: a `boundary` divergence found in ANY golden set outranks a `chain_drift` divergence found
    in another, when the workflow's overall verdict is chosen — cost if wrong: not recorded.
49. Ruling: only a `ProcError` or a `BackendError` counts as "the segment raised" for a SQL segment
    inside the chain (any other exception is treated as a real bug, not a domain outcome); a new
    `ChainError` is added to the shared validation library for this — cost if wrong: not recorded.
50. Ruling: the dbt chain-report gate (translate needs a PASS-shaped `validation_workflow.json` before
    it may proceed) applies again on a resumed run, not only on a fresh one — cost if wrong: not
    recorded.
51. Ruling: the pinned end-to-end call-list test deliberately gains the workflow-level validator's one
    new call, and two rows are added to `docs/reference/output-targets.md` — accepted as the expected
    shape of the change — cost if wrong: none.
52. Ruling (fix round 1, review findings I1/I2/I3 — SUPERSEDES the hand-off's original row order): a
    crash inside the chain's own fixer round could let a resumed run skip straight past the fixed
    segment's compile-check, review and validation and reach `VALIDATED`; the segment's stale `PASS`
    verdict is now cleared and saved before the fixer round runs. The design as first built handed
    every chain segment its upstream rows sorted by every column — deterministic, but it hid a Snowpark
    procedure's reliance on input order and gave a false `PASS` on two adversarial cases; the hand-off
    is changed to physical arrival order on the chain's first pass, with every segment's inputs
    presented in REVERSED order on the idempotency re-run, so real order-dependence shows up as a
    non-idempotent boundary instead of staying hidden; a vanished Snowpark output is now judged from
    the fact that it is missing, not from the backend's stale leftover copy (a third false-`PASS` case)
    — cost if wrong: a correct but order-sensitive procedure needs an explicit `ORDER BY`, which the
    cookbook already shows how to write.
53. Ruling (fix round 1, on the reviewer's own concerns): the dbt idempotency re-run also reverses its
    raw-input and pre-existing-target load order to match, since the reversal fix is about load order,
    not backend-specific behaviour — the spec's own DV7 amendment text says so; and on a real Snowflake
    backend (Task P2), the same re-run loads raw inputs in reversed insert order as a documented
    best-effort perturbation, acknowledged as weaker than the local reversal because a real account has
    no physical row order to begin with — cost if wrong: none; a real account's own weaker perturbation
    is already documented as weaker.
54. Ruling: fix round 2 and the scoped re-review of rounds 1 and 2 together confirmed every committed
    sample's chain still `PASS`es with the reversed re-run and stays idempotent — no committed
    procedure turned out to rely on physical order.
55. Ruling (re-review residuals R1/R2, fix round 3 — SUPERSEDES round 1's plain reversal): round 1's
    reversal still missed a PARTIAL reorder (rows already tie-sorted, or a Filter-then-UNION upstream
    feeding a consumer that only reads the first N rows); the rule becomes "reverse UNLESS the input
    already arrives as the exact reverse of run 1's order", verified against a sixteen-case attack
    table; a VIEW-backed input is materialised before it is reversed (reversing a view's own row ids
    was crashing the re-run; it is now a safe-side boundary instead); the fixer agent's file list
    gains `validation_workflow.json` as an input it should read — cost if wrong: the final
    whole-branch review is the next net that would catch it.
56. Ruling: round 3 was verified by the controller directly, re-running the reviewer's own sixteen-case
    attack suite against the fixed code rather than dispatching a third review seat — every case gave
    the expected verdict, including the two partial-reorder cases correctly failing as non-idempotent
    boundaries and two genuinely correct procedures correctly passing.

## Task W2 — seam check, batched analyzer, stitch

57. Ruling: the pinned "batched analyze" call sequence gains exactly `plan_batches.py` and
    `check_seams.py` on every analyze pass, not only when batching itself triggers — the brief requires
    both calls on every analyze pass, and "byte-identical" in the brief meant "otherwise unchanged", not
    that these two calls are conditional — cost if wrong: none; accepted by the review.
58. Ruling (beyond-brief additions, accepted by the review): every stream that crosses a DAG cut must
    be DECLARED by its consumer segment, not merely observed; a stream's producer segment must run in
    an earlier wave than its consumer; the analyzer's per-segment detail-character budget counts
    touchpoint lines; a segment's tier is read from `unsupported.json` on both the batched and
    unbatched paths; the analyzer budget itself is not empirically calibrated in this phase and is
    documented as such rather than tuned — cost if wrong: not recorded.
59. Ruling (minor, accepted): a batched analyze pass's crash-and-resume path is untested — accepted
    because the safe fallback is simply a full re-run, never a silent partial one — cost if wrong: not
    recorded.

## Task P2 — a real Snowflake backend and `deploy.py`

60. Ruling: agents are explicitly denied the new Snowflake-related flags (`--backend`, `--connection`,
    `--sandbox-database`) in every spelling, including argparse prefix abbreviations — an agent must
    never choose or touch a real backend — cost if wrong: none; assigned to Task W2's follow-up since
    W2 owns `policy.ts` that wave.
61. Ruling (verified against Snowflake's own documentation): `procs/master.sql`'s Scripting body needs
    a `$$` delimiter — Snowflake's CLI, SnowSQL and the Python connector's streaming execution do not
    reliably parse an undelimited Scripting block — the generated master body is wrapped in `$$`, like
    every segment procedure already was.
62. Ruling (verified against Snowflake's own documentation — the seed for Task C4V): the table-reference
    form every SQL procedure has used since the pipeline's first build,
    `IDENTIFIER(:DB || '.' || :SCHEMA || '.NAME')`, is not the documented `IDENTIFIER` grammar, which
    accepts only a single string literal, session variable, bind variable or Scripting variable, never
    an expression. A new controller-added task, C4V, switches the whole pipeline to the documented form
    across every layer that emits, checks or judges that SQL — cost if wrong: the documented form is
    strictly safer, so at worst a no-op on an account that also happens to accept the old form.
63. Ruling: a sandbox database is accepted for a real Snowflake run only if it is named in the
    orchestrator's own policy configuration (`CREATE OR REPLACE SCHEMA` is destructive) — anything else
    is a usage error raised before any connection is even opened.
64. Ruling: on a real Snowflake backend, the idempotency re-run loads raw inputs and pre-existing
    targets in reversed insert order, as a documented best-effort perturbation weaker than the local
    double's reversal, since a real account has no physical row order.
65. Ruling: the local DuckDB backend stays the byte-identical default path and needs no Snowflake
    connection at all.
66. Ruling: `deploy.py` also refuses to run against a workflow whose chain validation report is missing
    or not passing — the chain result is the real `VALIDATED` gate, not only the per-segment one.
67. Ruling: every connector or Session error is redacted before it reaches a log or report, and no flag
    ever accepts a password, token or private key directly — only a named connection.
68. Ruling: nothing about the real Snowflake path is added to any agent's allow-list — a human runs it,
    never an agent.
69. Ruling (fix round 1, review findings C1/C2/I1/I2): redaction must blank an unquoted secret value
    all the way to the end of its line, not just up to the first delimiter — a single point every
    redaction path shares; every identifier assembled from a logical name or a tool id must be
    validated as an identifier on BOTH backends before anything runs, because a crafted logical name
    could otherwise redefine an existing object; `deploy.py`'s own documentation must say plainly that
    `--execute` silently overwrites an existing procedure, and what grants it needs; a chain-level
    equality test compares the local and Snowflake-backed results for the procedures sample (the
    dbt sample's equivalent test is skipped by ruling — the fake account's own dbt run writes the
    golden tables itself, so it would prove nothing) — cost if wrong: not recorded beyond what the
    fixes already closed.
70. Ruling (residual from re-review, fixed before merge): a password embedded in a connection URL's
    user-info segment was not being redacted — fixed as a one-line follow-up, probed on four different
    secret shapes directly by the controller rather than a further review seat.

## Task C4V — the documented `IDENTIFIER` form for every SQL procedure (controller-added)

Added by the controller from Task P2's review (ruling 62): the concatenation-inside-`IDENTIFIER` form
every SQL procedure used was verified undocumented and likely rejected on a real account, and closing
it touches enough files (the local runner, the static check, the SQL policy judge, every canned and
broken procedure, the cookbook, the translator agent and our own spec) to be its own task.

71. Ruling (the task's founding decision): every SQL procedure moves from the undocumented
    "`IDENTIFIER` of a concatenation expression" form to the documented one — one `LET` binding per
    distinct source or target table (`<LOGICAL>_SRC` / `<LOGICAL>_TGT`), built from the same
    three-part concatenation, then `IDENTIFIER(:<VAR>)` everywhere the table is referenced; an
    expression written directly inside `IDENTIFIER(...)` is refused by both the static check and the
    SQL policy judge.
72. Ruling accepted alongside it: `IDENTIFIER(:SRC_DB)` used without a preceding `LET` is refused;
    `DECLARE`, a bare `var := …` assignment and `INTO :var` inside a procedure body are all denied; a
    dated historical record is excluded from the machine-path scan; the C4 contract's index entry
    gains a short amendment note; one reviewer-facing instruction line is updated.
73. Ruling (fix round 1, verified against Snowflake's own documentation — SUPERSEDES the founding
    decision's colon usage): a variable or argument is written WITHOUT a leading colon on the
    right-hand side of an expression (a `LET`'s own right-hand side, or a `RETURN`) — the colon only
    binds a variable INSIDE a SQL statement. The documented `LET` form drops the colon from its
    right-hand side, and a colon appearing inside a `LET`'s own expression is now refused.
74. Ruling (full task review, CRITICAL finding — the same failure class as an earlier residual under a
    different name): the SQL policy judge, once it trusts `IDENTIFIER(:VAR)` following a `LET`, could
    still be defeated by re-pointing that variable at a different table afterward — through a comment
    placed before `:=`, a quoted name, an assignment inside an `IF`/`BEGIN`/loop/`CASE`/`EXCEPTION`
    block, or a shadowing nested `LET` — all allowed by the base rule even though it denied the plain
    forms. In the same family: a `RETURN` clause was unchecked, a body `CALL` was never judged, and the
    header's parameter order was unchecked.
75. Ruling (fix round 2, closing the CRITICAL finding): a colon-style assignment or a `LET` statement
    anywhere outside the one recognised `LET`-binding position is denied, along with every Scripting
    control-flow keyword — a C4 procedure body must stay flat, which the contract always required; a
    `RETURN` clause may only be a literal; a body may not `CALL` anything; the procedure header must
    match the exact C4 form; a comment placed inside a `LET` statement is refused by the static check
    too, so the policy and the static check agree.
76. Ruling (scoped re-review of round 2 passed; round 2 also merged the integration branch again and
    closed every one of twenty-six distinct ways the base rule had allowed a bypass, one committed test
    per case): tokenizers now read escaped quotes and quoted identifiers the way Snowflake itself does;
    the local runner refuses the same denied keywords and any colon-style assignment outside a `LET`; a
    `RETURN` clause must be both a literal AND the procedure's last statement; a body's statements are
    allow-listed to the ordinary DML/DDL verbs a C4 procedure needs (`SELECT`, `WITH`, `INSERT`,
    `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `ALTER`, `TRUNCATE`, `DROP`, `COPY`); `master.sql` itself is
    denied through the same SQL-writing tool.
77. Ruling (re-review, PRE-EXISTING gap found outside this task's original scope, fixed as its round 3
    because this task already owns the policy file): an external `COPY INTO` target, an external or
    credentialed `STAGE`, a storage/API/external-access integration, an external function, or a
    `GET`/`PUT`/`REMOVE`/`LIST` against an external location were none of them schema-checked — a
    sandbox-to-external exfiltration path existed for any validator's SQL call. All are now denied by
    name; an internal sandbox stage keeps working unchanged. A companion check refuses a source-role
    variable used as a write target, or a target-role variable used as a read source.
78. Ruling: every one of the thirty canned and broken SQL procedures binds to byte-identical generated
    SQL before and after this whole task, across all its rounds; every sample's chain still passes
    twice (fresh run plus idempotency re-run) at every round.

## Task W4 — compaction memory aid

79. Ruling (fix round 1, review finding): the compaction-recovery metrics for accumulated compaction
    count and peak input tokens must take the MAX of the value already on disk and the value in memory
    when a manifest is reloaded, not simply keep whichever the disk copy says — the reviewer reproduced
    a batched crash-resume (or parser-recovery retry) that was silently LOWERING both numbers on
    reload, falsifying the design's own "these numbers are never lost, never lowered" claim and risking
    a miscalibrated live-test budget — cost if wrong: the live test's own time/token budget tracking
    would be wrong in the optimistic direction.
80. Ruling (accepted alongside the fix): a failed attempt to send the post-compaction reminder is
    logged rather than silently dropped; the validator and documenter roles are explicitly denied a
    notes-file path — they were never meant to have one.

## Task G — the offline run for all seven samples

81. Ruling: Step 3 stopped deliberately, as instructed, so the controller could inspect every changed
    file's classification before continuing — every previously committed workflow only gained a
    segmentation parameter (from Task W3), with segmentation otherwise byte-identical — accepted as an
    expected, documented kind of change; Step 4 resumed with a fresh run.
82. Ruling: the `wf_0006` deploy fixture's own docstring, which had become factually wrong, is
    corrected to also read the committed chain validation report.

## Task P4 — `docs/handoff-production.md` and `docs/production-backlog.md`

Ruling: this task starts before Task G, ahead of its place in the wave order — every command, flag
and path it documents already exists, and it points at `wf_0007` by its sample until Task G commits
the tree, merging the integration head again before it finishes if newer work has landed — cost if
wrong: a pointer to a path Task G later commits needs a follow-up edit, which happened once and was
folded into the merge.

83. Ruling B1 (a production-path bug the implementer found while writing the guide, fixed before the
    guide describes it): `set_models.py --default` left five of nine roles on their placeholder model,
    because the orchestrator's own config loader deep-merges the default config's per-role model map
    back over a file profile's role map — the default config's hosted profile is given NO per-role
    model map at all, so a file profile's role map is always taken whole; a test now resolves the
    effective, merged model id for all nine roles.
84. Ruling B2: `--import-set` never actually recorded the imported golden sets, so any real golden
    capture stayed permanently `BLOCKED` — a successful import now appends to the recorded golden sets
    (a database-target before/after capture stays a backlog item, not fixed here).
85. Ruling B3: with the GitHub command-line tool installed, the pipeline was creating GitHub issues and
    pull requests automatically — GitHub integration becomes OPT-IN (a configuration key defaulting to
    off, and a per-run flag that turns it on for that run), matching the owner's own stated
    no-publish-without-approval stance — cost if wrong: none; matches an existing house rule.
86. Ruling B4: every script's `--help` crashed under the Windows console encoding the guide assumes —
    one shared console-encoding fix applied to all twenty-six scripts, with a parametrised test.
87. Ruling: accepted alongside the fixes — script output is UTF-8 (the orchestrator already reads
    UTF-8); `set_models.py --dry-run` is treated as a genuine no-op against the live config file.
88. Ruling (fix round 2, review finding, IMPORTANT): the guide's own prose said to "follow top to
    bottom" while its actual verification ladder numbers five rungs that are not meant to run strictly
    in file order — a literal reading could run the first hosted session on a real workflow before
    completing the first three rungs; the execution order is now pinned explicitly, with a test.
89. Ruling: fifteen further minor corrections landed in the same round — unpinned command flags in
    prose, a verify step that silently deletes reports, a rung that should park rather than continue,
    several stale sentences, an undocumented change to a form used elsewhere, `master.sql`'s `CALL`
    never actually being exercised, a missing connection detail on one rung, a change to the policy
    file that needed a sentence saying it is sanctioned, a missing human-in-the-loop rule, a wrong
    count of a named gap, a suite count quoted as four when it is three, an intermediates path, a
    data-definition detail for a helper script, an uncommitted helper script, and a CLI decoding a
    stream per chunk in a way that could split a multi-byte character — plus the live-test facts from
    Task H (the SDK's real tool names; a recommendation to keep run roots short) folded in.
90. Ruling (resolved at integration, scoped re-review): the guide's own citation of the third live test
    resolved once the controller merged this task over that record and rewrote the README's note on
    the write and SQL tools — the write tool is confirmed live (it matches the `create`-family pattern
    observed); the SQL tool stays unverified (no SQL-named tool exists in a local session; the real
    Snowflake tool name is confirmed only at a later rung) — cost if wrong: one stale sentence.
91. Ruling (parked to the final review, out of this task's own scope): the guide's rung 3 reuses rung
    1's run root even though an earlier section says every earlier root is stale — carried forward
    rather than fixed here (closed by the final fix wave's hand-off-guide ruling, below).

## Task H — the bounded live test (controller task)

92. Ruling R-H1: see Process, ruling 12.
93. Finding, the direct trigger for Task N1: in all three live intake sessions the model tried several
    different ways to create the workflow's own notes directory itself — every attempt was correctly
    denied (the intake role's allow-list has no directory creation and no arbitrary interpreter call)
    — this cost tool calls in every run.
94. Finding, settling a previously unknown fact about the live SDK: the actual tool names and shapes
    were observed directly for the first time — a view tool, a create tool (the real write tool), a
    shell tool, a grep tool, a glob tool, and a subagent-spawning tool — this is what made Task D's
    earlier content-key fix (ruling 33) actually matter in practice.
95. Ruling: per the live test's own hard rules, a parked workflow gets no `--from-stage` retry in this
    bounded test; a run ends at whatever stage it reached.
96. Finding carried to the production backlog rather than fixed here: the character-to-token ratio
    actually observed across the three live intakes was roughly 136 to 200 rendered characters per
    estimated token — recorded as a calibration data point, since the inline-context character budgets
    only ever bounded the rendered text, never the model's real token cost.

## Task N1 — the orchestrator creates the notes directory (controller-added)

Added by the controller from Task H's own live-test finding (ruling 93): all three live intake
sessions burned tool calls on denied attempts to create the notes directory themselves.

97. Ruling (the task's founding decision): the orchestrator now creates a workflow's own `notes/`
    directory itself, before starting any session for a role that keeps notes, instead of leaving
    directory creation denied and undocumented. The policy itself is NOT widened — directory creation
    by an agent is still denied — cost if wrong: one redundant, still-denied directory-creation attempt
    per session.
98. Ruling: a failed directory-creation attempt is logged and the session still runs; the fixed
    task-text sentence telling a notes-keeping role the directory already exists is placed before any
    inline data context, consistent with the rule that the data fence never carries an instruction.
99. Ruling: one pre-existing test's regular expression needed updating for the new fixed sentence —
    accepted as a mechanical consequence, not a design change.

## Task P5 — README architecture diagrams, redrawn last

100. Ruling (task review, two Important findings): the per-segment diagram loop omitted the
     Snowpark-only render step and the compile-check step (with its own compile-failure edge) that the
     new dbt subgraph already showed, so both are added for parity; the "Stages" diagram was far wider
     than the other two, so it is redrawn as a top-to-bottom flowchart with left-to-right subgraphs
     inside it, targeting roughly a third of its original width.
101. Ruling (accepted alongside it): the dbt outcome edges are drawn from the subgraph's own id rather
     than a synthetic node; `compare.py`'s name is shown explicitly wherever a diagram reaches that
     step; Task N1's notes-directory creation is shown if there is room.
102. Ruling (side finding from the same review, verified by the controller, fixed in the final fix wave
     rather than reopening this task): the orchestrator's own dbt translate path checked only the
     chain report's PASS/FAIL verdict and ignored its separate `needs_human` flag, while the equivalent
     procedures-path check already parks on `needs_human` even alongside a `PASS` — a dbt workflow
     could reach `VALIDATED` with an unresolved `needs_human` flag still set. Closed as ruling N-dbt,
     below.
103. Ruling (controller commit after this task's merge): the components diagram is redrawn again to
     stack its script nodes, shrinking it from roughly 3579 to 2170 pixels wide, and the tool names
     shown are corrected to the ones the live test actually confirmed (`create`, `ask_user`).

## Final whole-branch review — the one fix wave

104. Ruling (the review's own verdict): ready after fixes, with one Critical and three Minor findings
     across the whole branch. Every seam the review specifically checked came back clean — the
     dbt-and-chain gate, the Snowflake backend's redaction and identifier checks, Task C4V's rules
     against the deployment path, the seam-check/batching/compaction/notes lanes together, hosted-model
     routing, GitHub's opt-in default, and every previously known false-`PASS` path.
105. Ruling C1 (Critical, the trigger for the whole fix wave): a dbt model written in Python, an
     `on-run-start`/`on-run-end` project hook, or a `pre_hook`/`post_hook` carrying arbitrary SQL all
     passed the static check and actually EXECUTED during dbt validation, although the design's own
     closed-surface promise (and Task A's own `dbt:model_jinja` allow-list) said none of that was
     possible; the same class of gap let a model's own `SELECT` read a raw file or an undeclared table
     with no static check ever inspecting its table references — cost if wrong: a dbt migration project
     was not the closed surface it claimed to be.

The following close ruling 105, each with its own committed test:

106. Ruling C1.1: one choke point enforces the dbt closed-surface check — `dbt_project.py`'s
     `check_surface` runs FIRST inside `run_dbt`, before any subprocess starts; any violation raises
     before a process is ever spawned, so every caller (the static check and the validator alike) is
     gated the same way, whoever calls it.
107. Ruling C1.2: the dbt project directory may contain only a fixed, named set of files — the five
     project-level files, model SQL files, exactly the two project YAML files, and the two git-ignored
     output directories dbt itself writes into (never read back as input) — anything else, including
     any Python file anywhere in the project, is refused by its relative path.
108. Ruling C1.3: `dbt_project.yml` itself is checked as a template with an exact, closed set of
     top-level keys and NO Jinja delimiter anywhere in the file — closing off `on-run-start`/
     `on-run-end` hooks, model-level hook blocks, dispatch overrides and every other project-level
     escape hatch in one rule.
109. Ruling C1.4: both project YAML files are closed to a fixed key shape, with no Jinja anywhere
     except one named, exact variable reference in the sources file — because dbt renders Jinja inside
     YAML at parse time, so "no Jinja" has to be the rule, not a list of which calls are dangerous.
110. Ruling C1.5: a model's `config(...)` call is closed to a fixed set of keyword arguments, each a
     string literal or a list of string literals, parsed properly rather than pattern-matched — closing
     off a raw `sql_header`, a schema/database override, a grants block and similar escapes.
111. Ruling C1.6: a `pre_hook`/`post_hook`'s SQL is now judged, not just required to exist — it must be
     exactly one `DELETE`, `UPDATE`, `INSERT` or `TRUNCATE` statement whose only Jinja is the model's
     own table reference, and whose every table reference, including inside any subquery, is that same
     table — parsed and refused fail-closed, with a deny-list of file/environment/network-reading
     function names and refusal of any function the parser does not recognise. A hook that genuinely
     needs to touch a different table has no way to express that on the dbt target, and the docs say
     so.
112. Ruling C1.7: a model's own `SELECT` is judged the same way — every table reference must resolve
     to a declared source, a `ref`, the model's own table, or a same-statement CTE name, with the same
     function deny-list and the same fail-closed refusal, and exactly one top-level `SELECT`.
113. Ruling C1.8: as defence in depth, the translator's and fixer's own write permissions for the dbt
     project are narrowed from the whole `models/**` tree down to exactly the SQL files plus the two
     named YAML files — a write to a Python file, a macro, a packages file or any other YAML under the
     project is denied at the policy layer too, independent of the static check.
114. Ruling C1.9: the review's own failing scenarios are committed as tests against real scratch
     copies of the sample dbt project — a Python model, each hook variant, a raw-file read, an
     un-sourced table read — every one is refused by the static check with its own named reason, and,
     for every case that used to actually execute, the validator is proven to start no dbt process and
     write no passing report at all; the committed sample and every cookbook dbt example still pass
     cleanly under the new gate.
115. Ruling N-dbt (from the Task P5 review's side finding, ruling 102): the dbt translate path now
     parks with a named reason whenever the chain report says `needs_human`, even alongside a `PASS`
     verdict, matching the rule the procedures path already followed.
116. Ruling M1: an `order.json` that flattens to no segment at all is now a usage error for both the
     dbt and the workflow-level validators, exactly like a missing one.
117. Ruling M2: a missing `intake/mappings.yaml` is now named explicitly by the dbt compile check, the
     same way it already was for contracts.
118. Ruling M3: the segmenter's own over-cap size warning is now emitted once per group, not once per
     outer iteration of its own algorithm.
119. Ruling (hand-off guide, closes ruling 91's carry-over): the guide's rung 3 and its own earlier
     "every previous run root is stale" statement are reconciled to agree with each other, rather than
     left in tension.
120. Ruling (parked, recorded not fixed — the final review's own remaining carry-overs): none of the
     review's other carry-over findings were judged load-bearing enough to act on in this phase; one is
     already documented in the design's own list, and the duplicate zip-slip guard (ruling 25) is
     already parked at Task P3's review.

121. Ruling (fix-wave outcome): the implementer's two corrections to ruling C1.7 are accepted. First,
     the `is_incremental` if/else is judged by rendering the model twice, once with each branch taken,
     and parsing each rendering; joining both branches' text, as the ruling said, does not parse when
     each branch holds a whole `SELECT`, and would have refused a committed model. Nested blocks stay
     refused, so every line of branch text is still judged. Second, "exactly one top-level `SELECT`"
     means one query statement, so a top-level `UNION ALL` is accepted, with every table it reads still
     judged. The stricter checks the implementer added beyond the rulings are kept: `model-paths` and
     `profiles.yml` are checked before any run, `alias` and `unique_key` must be plain identifiers
     because dbt pastes them into SQL unquoted, every source must use the `var('src_schema')` schema,
     test arguments may not contain `(`, and model SQL is cross-checked with DuckDB's own parser — cost
     if wrong: some safe dbt is refused (ruling 123).
122. Ruling (fix-wave outcome): an adversarial re-review of the fix tried 241 attacks against the gate.
     They covered Jinja, YAML, the project's file set and model and hook SQL, and included a sweep
     across 117 DuckDB functions. No attack achieved code execution, a read or write outside the
     project, or a passing report for an unsafe project, and nothing regressed. Every path that can
     start dbt goes through the one gated `run_dbt`, and the dbt process itself is started in exactly
     one place. The fix wave is merged — cost if wrong: an attack class nobody tried; the attack corpus
     stays in `tests/test_dbt_surface.py`.
123. Ruling (fix-wave residuals): the gate's over-strict refusals and its two disclosed limits go to
     `docs/production-backlog.md` as "The dbt surface gate's strictness" rather than being loosened now.
     The over-strict refusals are `var( 'src_schema' )` with inner spaces, a dash in a model file name,
     and a function sqlglot's DuckDB dialect does not know, such as `iff`. The disclosed limits are a
     race with a concurrent writer between the check and dbt's start, and NTFS alternate data streams
     and hard links, which are inert here. `deploy.py` exiting 2 rather than 1 on a refused project is
     accepted, because nothing runs either way. `target_check.py` still proposing dbt for a
     PreSQL/PostSQL that touches another table is already its own backlog item — cost if wrong: a pilot
     translation parks on safe dbt and a human allows the construct with a test.
124. Ruling (found while preparing the publish; SUPERSEDES phase 1's final-review M10 list): the
     login-name scan in `tests/test_committed_workflows.py` no longer keeps a committed list of build
     machines' login names, because that list spelled out the very name the scan protects, and the
     publish would have shipped it. Every machine now guards its own login, read at run time from
     `getpass`, the home directory's name and `USERNAME`/`USER`. The environment variable
     `MIGRATION_BANNED_LOGIN_NAMES` can add other machines' names from outside the repository, and
     the scan now covers its own test file too — cost if wrong: a machine that never runs the suite
     is not guarded unless its name is set in that variable.
