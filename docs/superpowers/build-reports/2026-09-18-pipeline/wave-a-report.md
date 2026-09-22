# Fix-wave Agent A report (Python + docs)

Worktree: `.worktrees\wave-a`, branch `wt/wave-a`, based on `1228ed3`. Never touched
`orchestrator/**`, `orchestrate.ts`, the main tree, or `.claude\worktrees\`.

Final state: **1005 passed, 0 skipped, 0 warnings** (baseline was 980; net +25 tests: +7 F1d,
+2 F1b, +4 F1c, +6 F1e, +3 F4, -1 F15, +1 F9, +3 F5/F6). `git diff main -- docs/spec` is empty.
Worktree clean. Six commits, one per logical group plus a first WIP for Group 1's four
interlocking items:

- `e77a381` wip: pristine test fixtures (F1b), gated promotion (F1c), comment-preserving global.yaml writes (F1d)
- `2b2e268` fix: build_samples seed/build refuse to overwrite an already-seeded workflow without --force (F1e)
- `fc5bd13` fix: GOLDEN_DATA/UNKNOWN never approvable (F4), drop a tautological test (F15), compile_check catch-all (F9)
- `2c58948` docs: analyzer contract keys (F5), unsupported.json tier shape (F6), venv interpreter everywhere (F16-docs)
- `d506e97` fix: restore docs/spec verbatim, move parked-escalation rule into the design doc (F3)
- `e3591f9` docs: README accuracy pass — scratch-root offline walkthrough, promotion rule, budgets, agent count (F1a/F2/F14)

---

## GROUP 1 — the repo-root side effect

### F1b — pristine test fixtures

**Files:** `tests/helpers.py` (new `copy_pristine_mappings_and_catalog`), `tests/test_helpers.py` (new,
RED/GREEN via a scratch source tree), every site that used to
`shutil.copytree(ROOT / "mappings"/"catalog", ...)`: `tests/test_answer_samples.py`,
`tests/test_canned_artifacts.py`, `tests/test_intake_cli.py`, `tests/test_intake_fix_round_{1,2,3,4}.py`,
`tests/test_intake_prompt.py`, `tests/test_intake_self_reference.py`, `tests/test_intake_touchpoints.py`.

The helper copies `mappings/`+`catalog/`, then reads the copied `global.yaml`, sets `sources`/`outputs`
to `{}` (every other key untouched) and writes it back through `lib.io.write_global_mappings` (F1d,
below — this reuses the same comment-preserving writer instead of a second implementation).

RED/GREEN: `tests/test_helpers.py::test_a_polluted_source_global_yaml_is_copied_pristine` was written
against a scratch source tree with a hand-polluted `global.yaml` and passed on first run because the
implementation and test were written together (documented as a self-review gap below); I then verified
by hand (a throwaway script, not committed) that *without* the blanking step the copy would retain the
pollution, confirming the test is not tautological. `test_the_source_tree_itself_is_left_untouched`
pins that the helper never writes back into its source.

**Demonstration (report only, as asked):** I edited the real `mappings/global.yaml` to add a bogus
`sources.deliberately/polluted.csv` entry, ran `pytest -k "intake or foundations or committed_workflows
or helpers"` (206 tests), and got **205 passed, 1 failed** — the one failure was
`test_foundations.py::test_global_yaml_matches_program_spec_plus_task_additions`, which is the test that
correctly pins the LIVE file's own content and is supposed to fail when that file is polluted. Every
fixture-consuming test passed regardless. I then restored `mappings/global.yaml` to its committed
content with the Edit tool (never `git checkout --`); `git diff` on that file is empty.

Every existing `copytree(ROOT / "mappings", ...)` site is now gone — confirmed by
`grep -rn 'copytree(ROOT / "mappings"' tests/*.py`, which only matches inside the helper itself.

### F1c — no promotion under the default automation identity

**Files:** `scripts/intake_prompt.py` (`apply_answers` gained `promote_to_global: bool = True`;
`run` gained `promote: bool = True`, forwarded to `apply_answers`; `main` computes
`promote = interactive or bool(args.user)` and passes it through), `tests/test_intake_cli.py` (four new
tests), `tests/test_intake_fix_round_1.py` (one monkeypatched fake's signature widened).

Design: the `promote()` closure inside `apply_answers` still ALWAYS runs the conflict check against
`mappings/global.yaml` (an existing entry with a different FQN is still recorded as a conflict); only
the *mutation* of `global_map` for a brand-new key is skipped when `promote_to_global` is false. The
final `write_global_mappings` call always runs — when nothing was promoted, it reproduces the same
bytes (F1d's byte-identical guarantee), which is exactly what the new "automation default leaves
global.yaml byte-identical" test checks.

`run`/`apply_answers` default `promote=True` for *direct* callers (tests, `tests/helpers.py`), because
every one of them already passes an explicit, non-default `user` string — which is the same signal
`--user <name>` is at the CLI. Only `main`'s own default-identity path (`--no-interactive`, no `--user`)
computes `promote=False`.

**Tests whose setup changed and why:** I audited every call site of `apply_answers`/`run`/`main` across
the whole suite (`grep -rn "intake_prompt\.\(run\|apply_answers\|main\)"`, 84 hits) for one that relies
on the *default* automation identity promoting something. **None do** — every direct call to
`run`/`apply_answers` already passes an explicit `user="wf_owner"` (or similar), and every `main()` call
that resumes non-interactively either passes `--user` explicitly or never reaches a point where
promotion would be observable (it asserts on exit codes / intake status, not on `global.yaml`
content). So the RULING's "check every existing intake test... fix the TEST SETUP" turned up **zero**
tests needing a setup change for F1c itself — only the one monkeypatched fake in
`test_intake_fix_round_1.py::test_c_guard_downgrades_a_falsely_ready_status_to_needs_human`
(`fake_apply_answers`) needed `**_kwargs` added to its signature so the new `promote_to_global=`
keyword `apply_answers` is now called with doesn't raise `TypeError`. Same for
`test_intake_cli.py`'s existing `fake_run` (already had `**kwargs`, so it needed no change).

New tests (`tests/test_intake_cli.py`): `test_prompt_cli_automation_default_never_promotes_leaves_global_yaml_byte_identical`,
`test_prompt_cli_explicit_user_flag_promotes_to_global_yaml`,
`test_prompt_cli_interactive_promotes_to_global_yaml` (had to feed `sys.stdin` a `StringIO`, not
monkeypatch `builtins.input`, because `run`'s `ask` parameter defaults to `input` bound once at import
time — noted inline), `test_prompt_cli_automation_default_still_detects_conflicts` (proves the
consistency check survives `promote=False`).

RED confirmed by running the four new tests before the `main`/`run`/`apply_answers` edits (all failed:
either promotion happened when it shouldn't, or `TypeError` from the missing kwarg); GREEN after.

### F1d — comment-preserving `mappings/global.yaml` writes

**Files:** `scripts/lib/io.py` (new `write_global_mappings` + `_preserved_global_mappings_preamble`),
`tests/test_io_global_mappings.py` (new, 7 tests, TDD RED-then-GREEN).

Preserves every line above the top-level `sources:` key verbatim (found by a `^key:`-at-column-0 regex
scan); regenerates only `sources`/`outputs` below it. Falls back to a full `yaml.safe_dump` — with a
stderr warning naming the path — when an existing file doesn't have that exact shape (no `sources:`
line, `outputs:` appearing before `sources:`, or any other top-level key trailing `outputs:`). A
brand-new file (no prior content to preserve) is written in full, quietly — I read this as a bootstrap
case distinct from "an existing file whose shape doesn't allow it", noted under Brief corrections below.

RED: ran the 7 new tests against `lib.io` before adding the function — `AttributeError: module
'lib.io' has no attribute 'write_global_mappings'` on all 7. GREEN after implementing.

`apply_answers`'s final write switched from `lib_io.write_yaml` to `lib_io.write_global_mappings`.

### F1e — `build_samples.py` seed/build refuse to overwrite an already-seeded workflow

**Files:** `scripts/dev/build_samples.py` (`seed`/`build` gained `force: bool = False`; new
`_refuse_already_seeded` check runs before anything else; CLI gained `--force`),
`tests/test_build_samples.py` (6 new tests + 11 existing calls updated).

RED: 6 new tests (`test_seed_refuses_a_workflow_that_already_has_a_manifest_without_force`,
`test_seed_force_overwrites_an_already_seeded_workflow`, the `build` equivalents, and two CLI-level
tests) all failed before the change — confirmed via targeted run before editing
`build_samples.py`. GREEN after.

**Tests whose setup changed and why** (every one deliberately re-seeds/re-builds the *same*
`repo`+`wf_id` a second time — the whole point of the "Fix round 1-4" reseed suite — so each needed
`force=True` added to its second call to keep exercising reseed mechanics rather than hitting the new
refusal):
- `test_build_twice_is_byte_identical`
- `test_seed_never_overwrites_existing_status_metrics_answers_or_accepted_diffs`
- `test_reseed_removes_a_deleted_golden_input_csv`
- `test_reseed_removes_a_dropped_golden_set_folder`
- `test_reseed_removes_a_renamed_source_file`
- `test_reseed_removes_a_yxdb_whose_input_basename_changed`
- `test_rebuild_removes_a_stale_segment_after_a_segmentation_change`
- `test_build_wf_0005_leaves_no_stale_intermediates_or_outputs_from_an_earlier_state`
- `test_reseed_leaves_files_it_does_not_own_untouched`
- `test_seed_refuses_a_linked_owned_dir_before_deleting_or_writing_anything`

Two more (`test_reseed_leaves_prior_state_intact_when_sample_json_is_malformed`,
`test_reseed_leaves_prior_state_intact_when_a_yxdb_input_csv_is_missing`) would have kept *passing*
without `force=True` (their bare `pytest.raises(BuildError)` doesn't check the message, and the new
refusal also raises `BuildError`), but that would have silently stopped testing what they say they
test. I added `force=True` plus a `match=` to each so they keep pinning the malformed-JSON / missing-CSV
failure specifically, not the new gate.

Checked every other caller of `build_samples.seed`/`build` across the whole suite (`tests/helpers.py`,
`test_answer_samples.py`, `test_canned_artifacts.py`, `test_intake_cli.py`,
`test_intake_fix_round_{1,2,3,4}.py`, `test_intake_prompt.py`, `test_intake_self_reference.py`,
`test_intake_touchpoints.py`) and the orchestrator's own `orchestrator/test/integration.test.ts`
(read-only) — every one of them seeds a given `wf_id` on a given `repo` exactly once, so none needed a
change.

---

## GROUP 2 — compare / scripts

### F4 — `GOLDEN_DATA`/`UNKNOWN` never approvable

**Files:** `scripts/compare.py` (`_approved` checks `cluster["class"] in _NEVER_APPROVABLE_CLASSES`
first, before the scope check; module + function docstrings updated), `tests/test_compare.py` (3 new
tests).

The real live bug: `_nullability_clusters` writes a `GOLDEN_DATA` cluster with `scope="columns"` for a
NOT NULL violation *in the golden data itself* — under the old code this was approvable like any other
column cluster. `UNKNOWN` gets the same class-level refusal as defense in depth, even though every
current `UNKNOWN` cluster is `scope="synthetic"` (already unapprovable on that basis alone — no code
path in `compare.py` classifies a column diff as `UNKNOWN`).

RED: `test_unknown_is_never_approvable_even_at_column_scope_with_a_matching_approval` (a direct
`cmp._approved()` call) failed as expected (`True` instead of `False`) before the fix.
`test_golden_data_is_never_approvable_even_with_a_matching_column_approval` needed a rewrite mid-flight:
my first version only approved `GOLDEN_DATA` and it passed even against the OLD code, because the same
fixture also raises an unrelated, unaccepted `NULL_SEMANTICS` cluster on the `actual` side (same NULL
on both sides trips *both* of `_nullability_clusters`'s per-side checks) — that second cluster alone
already forced `FAIL`, masking the thing under test. Fixed by accepting+approving *both* classes, so
the assertion actually depends on whether `GOLDEN_DATA` itself is approvable. Documented as a "Brief
corrections"-style catch below since I built and then had to correct my own test, not one given to me.

Also added `test_ordering_can_still_be_accepted_with_an_approval` (ROUNDING already had a
passing-approval regression; ORDERING didn't) to prove the carve-out didn't widen into blocking every
class.

### F15 — dropped the tautological test

**File:** `tests/test_compare_keyless.py`.

Deleted `test_an_accepted_pair_always_has_a_column_in_common` (`assert size // 2 < size` for
`size in range(2, 9)` — pure arithmetic, never touched `compare.py`). Left a comment in its place
naming the two tests directly above it that already pin the same property behaviourally against the
real `_pair_surplus` pairing code: `test_two_of_four_columns_rewritten_still_pairs` (2-of-4 differing
IS paired, and the resulting cluster names only the two differing columns, proving the other two are
the "column in common") and `test_more_than_half_the_columns_rewritten_does_not_pair` (3-of-4 differing
is refused). Not a new test — the brief allows deleting when an existing test already pins the
property, and named it.

### F9 — `compile_check.py` catch-all

**Files:** `scripts/compile_check.py` (`main` gained the standard `except Exception: traceback.print_exc();
return 2`, mirroring `scripts/parse.py`), `tests/test_compile_check.py` (1 new test).

RED: `test_cli_unexpected_exception_exits_2` (monkeypatches `cc.compile_check` to raise `RuntimeError`)
failed with the raw `RuntimeError` propagating out of `main()` before the fix. GREEN after.

---

## GROUP 3 — agent/instruction contracts

### F5 — analyzer contract keys

**File:** `.github/agents/analyzer.agent.md` (step 3 of the Procedure), `tests/test_agents_config.py`
(2 new tests).

Added `inputs[].from` (upstream segment id) and `inputs[].table` (the literal
`MIG_WORK.<WF>_<SEG>_OUT[_<stream>]` table — confirmed the exact naming against
`docs/superpowers/plans/2026-09-18-pipeline/00-index.md`'s contract C3, not guessed) next to the
existing `inputs[].stream`; `validate_segment.py` (`_load_and_run`, ~L151-161) raises
`ValueError` when a stream-carrying input is missing either, confirmed by reading the code. Also named
the top-level `"segment"` key (`compare.py`'s `_verdict` matches an approval's `segment` field against
`contract.get("segment")` — an absent `segment` means no real approval will ever equal `None`, so
`PASS_WITH_ACCEPTED_DIFF` silently becomes unreachable for that contract) and the optional
`"normalizations"` key (`compare.py` line ~1259 reads it directly).

RED evidence: `git show HEAD~1:.github/agents/analyzer.agent.md` (the pre-edit content) confirmed via
grep that none of `inputs[].from`, `inputs[].table`, `MIG_WORK.<WF>_<SEG>_OUT`, `"segment"`,
`PASS_WITH_ACCEPTED_DIFF`, `"normalizations"` were present; the new tests
(`test_analyzer_documents_inputs_from_and_table_for_segment_chained_inputs`,
`test_analyzer_documents_segment_and_normalizations_contract_keys`) would have failed against it.

### F6 — `unsupported.json` tier shape

**File:** `.github/agents/analyzer.agent.md` (Outputs section), `tests/test_agents_config.py`
(1 new test).

Documented the file's shape (`"tier"`, `"unsupported"[]`, `"unknown"[]`, per
`samples/wf_0005/canned/unsupported.json`) and stated plainly that the orchestrator reads `tier` from
THIS file, not `manifest.json` — confirmed by reading (read-only) `orchestrator/runner.ts`'s
`replayAnalyzer` (sets `wf.tier` from `unsupported.json`'s own field) and `orchestrator/stages.ts`'s
analyze-stage completion check (same file, same field). New test
`test_analyzer_documents_unsupported_json_tier_shape` pins `"tier"`, `T1`/`T2`/`T3`, `runner.ts` and
`stages.ts` all appearing in the body.

### F16-docs — venv interpreter everywhere

**Files:** `.github/copilot-instructions.md` (one new rule), `.github/agents/intake.agent.md`,
`parser-recovery.agent.md`, `translator.agent.md`, `validator.agent.md` (every bare
`python scripts/...` / `python -m pytest` became `.venv/Scripts/python.exe scripts/...` /
`.venv/Scripts/python.exe -m pytest`).

Touched all four agents whose bodies are otherwise kept verbatim from the spec (`fixer` and
`documenter`/`cookbook-curator` had no `python` mentions to begin with) — did NOT add the
`<!-- amended: plan Task 12 -->` marker to any of them, since `test_amended_paragraphs_are_marked`
explicitly requires that marker be ABSENT from those four bodies and this ruling isn't a plan-Task-12
amendment anyway.

Checked `tests/test_agents_config.py` for any assertion pinning the old `python scripts/...` spelling
(`grep -n python tests/test_agents_config.py`) — **none exist**, so nothing needed adjusting there.

---

## GROUP 4 — docs

### F3 — restore `docs/spec/` verbatim; move the parked-escalation rule

**Files:** `docs/spec/01-copilot-setup.md` (reverted the one edited sentence to `main`'s exact text via
Edit, not `git checkout --`), `docs/superpowers/specs/2026-09-18-alteryx-snowflake-pipeline-design.md`
(§4 grew from 7 to 11 items).

`git diff main -- docs/spec` is empty (verified after the edit, and again just now at wrap-up).

Added 4 items to the design doc's §4: `EXECUTE AS CALLER`, "row-scope and schema-scope differences are
never approvable" (updated for F4's `GOLDEN_DATA`/`UNKNOWN` carve-out), keyless nearest-match pairing,
and the parked-escalation/`--from-stage`/tool-call-budget-reset rule itself (the content removed from
`docs/spec/01-copilot-setup.md`, rewritten as a deviation from the spec's own simpler "skips any stage
already DONE/PASSED" sentence). Cross-checked every README §9 item against the design doc's new §4 to
confirm all 10 the reviewer counted in the README are now represented (some 1:1, some the README split
into two that the design doc still states as one, e.g. logical names + multi-output contracts).

### F2 + F1a + F14 — README.md

**File:** `README.md` (82 insertions / 22 deletions).

(i) §6's offline sequence now runs in a SCRATCH ROOT. This required real investigation, not just
substituting `--root .` → `--root "$SCRATCH"`: I traced `orchestrator/cli.ts`'s `env.py` and found it
spawns every Python call with `cwd: root` (the `--root` value), and the `script` argument
(`"scripts/parse.py"` etc.) is a bare relative string — so `scripts/` has to physically exist under
whatever root you pass, not just under the repo. I also found `config.python`/`config.samplesDir` are
resolved via `path.resolve(root, ...)`, so a scratch `orchestrator.config.json` needs them as absolute
paths pointing back at the real `.venv` and `samples/` (confirmed this exact pattern was already used,
independently, in `docs/live-smoke-test.md`'s own recipe). The new §6 recipe copies `scripts/`
(+ `mappings`/`catalog`, as §5 already does) into `$SCRATCH` and writes a two-key
`orchestrator.config.json` with absolute `python`/`samplesDir`, with inline comments explaining why
each piece is needed. Stated plainly that the committed `workflows/` tree is the product of running
this once (the *original* run used `--root .`, which is exactly why its `mappings/global.yaml`
promotion needed a manual revert — see `77d46bd`'s commit message), that a fresh clone already has the
terminal states with nothing to run, and that a run leaves the wf_0005 parser-recovery replay files
under whatever root it used.

(ii) Added notes in both §5 (right after the transcript's `mappings.yaml`, since that run's `--user
wf_owner` does promote under F1c) and §6 (right after the scratch sequence, since that run's
`--no-interactive` with no `--user` does not) stating the real promotion rule plainly.

(iii) New paragraph after "Re-running" describing `budgets.maxToolCallsPerWorkflow`: accumulates
across every session a workflow has ever run (traced `toolCallsUsed` in `orchestrator/stages.ts`,
which sums `manifest.metrics[role].toolCalls` — persisted on disk, hence "across sessions"), parks
`NEEDS_HUMAN` with reason `budget` when crossed (confirmed the literal string in `stages.ts`), and
`--from-stage` reopens it with a fresh budget (described as the rule; Agent B implements the reset in
`orchestrator/**`, which I did not touch).

(iv) §1 now says the orchestrator drives eight roles (named from `orchestrator/types.ts`'s `Role`
type, confirmed there is no `"cookbook-curator"` in it) and that the ninth, `cookbook-curator`, is
CLI-only; §6's own "nine Copilot agents were replayed" corrected to eight, since no orchestrate.ts run
ever touches cookbook-curator. Left "nine" everywhere the README means the literal file count (repo
map, §11).

(v) The repo map's "docs/spec (never edited by this build)" claim needed no repair — it's true again
after F3's restore. Grepped the whole README for any other stale claim about the docs/spec edit (there
was none).

(vi) §9 item 8 now states `GOLDEN_DATA`/`UNKNOWN` are never approvable at any scope, mirroring the
design doc's §4.

(vii) Already used `.venv/Scripts/python.exe` consistently; nothing to change (the one bare `python -m
venv .venv` bootstrap line is correct as-is — there is no venv interpreter yet to invoke it with).

No test covers README.md's prose (confirmed via grep — the only README.md hits in the test suite are
for `docs/spec/00-README.md` and `samples/wf_*/README.md`), so this whole item is untested by
construction; I re-read the finished section for internal consistency (e.g. fixed a cross-reference in
the fixer-loop paragraph that would have pointed at "§5's reproduction" for a config-with-absolute-paths
pattern that actually only exists in §6 after this edit).

---

## Self-review findings / brief corrections

- **F1d's bootstrap-vs-fallback split is my own interpretation**, not stated verbatim in the ruling:
  the ruling says fall back-with-a-warning when the file's shape doesn't allow verbatim preservation,
  listing "no top-level `sources:` line" as one such shape. I read a *nonexistent* file as a different,
  unremarkable case (nothing to preserve yet, no warning needed) rather than a malformed one, so
  `write_global_mappings` on a brand-new path writes quietly. This only fires the first time
  `mappings/global.yaml` is ever created, which never happens in this repo (the committed one always
  exists), so it doesn't affect any current test, but I flag it here in case that reading is wrong.
- **F4's first test attempt was wrong and I had to catch it myself**: my first version of
  `test_golden_data_is_never_approvable_even_with_a_matching_column_approval` passed even against the
  unfixed code, because a second, unrelated `NULL_SEMANTICS` cluster in the same fixture was already
  forcing `FAIL` on its own. Caught this by inspecting the actual `diff_clusters` list with a throwaway
  script before trusting the green result, and rewrote the test to approve both classes so it actually
  depends on `GOLDEN_DATA`'s own approvability. Documented under F4 above.
- **F1b/F1e's implementation-and-test-together gap**: for `copy_pristine_mappings_and_catalog` I wrote
  the helper and its test in the same pass rather than strict RED-then-GREEN, given the function's
  small size; I compensated with a manual "would this fail without the fix" check (shown under F1b)
  rather than skipping verification entirely.
- **Nothing was left undone.** Every numbered item in the brief (F1a–F1e, F2, F3, F4, F5, F6, F9, F14,
  F15, F16-docs) has a corresponding commit and, where the brief asked for tests, RED/GREEN evidence
  above.
- **One thing noticed but explicitly out of scope**: `.github/agents/intake.agent.md` step 7 still says
  the intake agent should "promote anything reusable... to mappings/global.yaml" without qualifying it
  by identity/interactivity (F1c's new rule). Not part of F5/F6/F16-docs, and changing agent-facing
  policy prose beyond what was asked felt like scope creep for a fix-wave — left it, noting it here
  instead of silently expanding my mandate.

## Verification commands run

```
.venv/Scripts/python.exe -m pytest --no-header -rN   # 1005 passed in ~93s, every time, across every group
git diff main -- docs/spec                            # empty
git status --short                                     # clean
```
