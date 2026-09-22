# Task 17 — Offline full run committed to `workflows/`, README, final verification

**Status: DONE.**

Worked in the MAIN tree (branch `feat/pipeline-m0-m2`), per the addendum's override — not a
worktree. `.worktrees/task-15-fix` was never touched.

## Commits

```
77d46bd wip: offline full run committed to workflows/, with the confirmed_by leak fixed
63e07e4 wip: README.md — the front door for someone who has never seen this repo
9113fe0 feat: worked examples from the offline run, README and final verification
```

The third commit is an empty marker (no file changes of its own) carrying the final verification
results, the same pattern `task-15-int-report.md` used for its own closing commit.

## What the run produced

Command sequence (`task-15-int-report.md`'s "Command sequence for Task 17"), run twice end to end
in the repo root — once before the `confirmed_by` fix was found (see below), reverted, and once
after, which is what is committed:

```
.venv/Scripts/python.exe scripts/dev/build_samples.py seed --root .
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root . --runner mock --no-interactive   # pass 1: parks
.venv/Scripts/python.exe scripts/dev/answer_samples.py --root .
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root . --runner mock --no-interactive   # pass 2: terminal
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root . --runner mock --no-interactive   # pass 3: no-op, exit 0
```

Terminal-state table, copied from the five committed `workflows/*/manifest.json` files:

| workflow | tier | parse | intake | analyze | golden | translate | segments |
|---|---|---|---|---|---|---|---|
| wf_0001 | T1 | PARSED (attempts: 1) | READY | DONE | DONE | **VALIDATED** | seg_01: PASS |
| wf_0002 | T1 | PARSED (attempts: 1) | READY | DONE | DONE | **VALIDATED** | seg_01/02/03: PASS |
| wf_0003 | T1 | PARSED (attempts: 1) | READY | DONE | DONE | **VALIDATED** | seg_01/02: PASS |
| wf_0004 | T1 | PARSED (attempts: 1) | READY | DONE | DONE | **VALIDATED** | seg_01/02/03: PASS |
| wf_0005 | T3 | PARSED (attempts: 2) | READY | DONE | *(never runs — T3)* | **MANUAL** | none (T3 has no per-segment contracts) |

Matches the addendum's expectation exactly. wf_0005's `manifest.json.reasons.translate` is
`"tier-T3"`; its `parse.attempts: 2` is the parser-recovery round (real `parse.py --check` failure
→ MockRunner replays `samples/wf_0005/canned/parser-recovery/**` → real re-parse succeeds,
confirmed by `workflows/wf_0005/parsed/parse_diagnosis.md` being present). Pass 3 exited 0 with no
status changes anywhere (`wf_0001`-`wf_0004`'s `pr` stage still logs "gh not installed; skipping
PR" every pass, since a missing `gh` never sets `status.pr`, so `shouldRun` keeps attempting it
forever — a harmless no-op, not a state change). 322 files committed under `workflows/`.

Every `validation*.json` under `workflows/*/segments/*/` carries verdict `PASS` (checked by the
new `tests/test_committed_workflows.py::test_every_committed_validation_json_carries_a_passing_verdict`,
which reads every one of them, not a sample).

## Reproducibility

Re-ran the *entire* sequence a second time in an independent scratch copy (fresh
`build_samples.py seed` → `answer_samples.py` → two `orchestrate.ts` passes), with a **pristine**
copy of `mappings/global.yaml` and **without** `scripts/parsers/ext/acme_dedupe.py` pre-present
(see "wf_0005 parser-recovery files" below for why that second condition matters — getting it
wrong the first time silently skipped wf_0005's whole recovery path and gave a false pass). Diffed
every file against the committed tree (`diff -rq workflows <scratch>/workflows`, then a per-file
`diff` on every difference `diff -rq` reported):

**Result: every single difference across all 322 files was confined to exactly two fields:**

- `manifest.json["updated_at"]` — a timestamp, restamped by `saveManifest`'s `finally` block on
  every save regardless of whether a stage actually ran.
- `validation*.json["runtime_ms"]` — `validate_segment.py`'s own measured wall-clock time for that
  run.

No other field, no file set, no file content anywhere else differed — including the tricky bits:
wf_0005's `parse.attempts: 2` and `parsed/parse_diagnosis.md` reproduced identically once the
scratch copy correctly excluded the extension beforehand; wf_0002's Join/Union segment ordering,
wf_0003's `MERGE` output, wf_0004's macro/CrossTab segments, and every golden-set validation report
were all byte-identical modulo the two fields above.

**Volatile fields** (belongs in the README's own words, and does): `manifest.json["updated_at"]`
and `validation*.json["runtime_ms"]`. Nothing else is expected to differ on a re-run from the same
starting state (design deviation 7: idempotency means determinism).

## Absolute-path / user-name audit

Grepped the committed `workflows/` tree (case-insensitive) for `C:\Users`, `C:/Users`, and
`<user>`, per the addendum.

**Hit found and fixed at the source:** `intake/mappings.yaml["confirmed_by"]` read `<user>` (the
real OS login name, exact case) on every one of the five workflows, in the first run. Root cause:
`scripts/intake_prompt.py`'s CLI defaulted an unset `--user` to `getpass.getuser()` unconditionally
— including for a **non-interactive** resume, which is exactly what `orchestrator/stages.ts`'s
`stageIntake` calls (`intake_prompt.py <id> --no-interactive`, no `--user`) on every automated pass,
and exactly what this offline run's own two orchestrator passes did. Nobody is present during a
non-interactive resume — the mapping was already confirmed earlier, via `open_questions.md` or
`manifest.answers` — so stamping the resuming process's own OS account onto `confirmed_by` was both
a leak (machine-specific, would differ on every contributor's machine) and inaccurate (that account
never confirmed anything).

**Fix** (`scripts/intake_prompt.py`, `main()`): `--user`'s default now depends on whether the call
is interactive. Interactive keeps the old behavior (`getpass.getuser()` — a human really is
present). Non-interactive now defaults to the literal string `"automation"`. An explicit `--user`
always wins either way. New tests in `tests/test_intake_cli.py`
(`test_prompt_cli_defaults_user_to_automation_when_resuming_non_interactively`,
`test_prompt_cli_user_flag_overrides_the_non_interactive_default`,
`test_prompt_cli_defaults_user_to_the_os_login_name_when_interactive`) lock in all three cases via
a spy on `intake_prompt.run` (so they test `main`'s own default-selection logic in isolation from
`run`'s already-well-covered handling of a given `user` value). **RED**, confirmed against the
pre-fix code by temporarily overwriting `scripts/intake_prompt.py` with `git show
HEAD:scripts/intake_prompt.py`'s content (never `git checkout --`/`git stash`), then restoring the
fix: the non-interactive-default test failed with `user: "unknown"` instead of `"automation"`
against the old code (the `getpass.getuser()` call was mocked to raise in the test, which the old
code's `except Exception: user = args.user or "unknown"` swallowed — a different failure mode than
the real leak, but conclusive that the old code takes a different branch). **GREEN** after
restoring the fix, and confirmed again in the real second offline run:
`workflows/*/intake/mappings.yaml["confirmed_by"]` now reads `"automation"` everywhere the answer
came from this run's own non-interactive resume.

**Not a leak — reported, not changed:** every `samples/wf_000N/sample.json`'s pre-existing
`"owner": "wf_owner"` (lower-case) fixture value, which flows verbatim into
`manifest.json["source"]["owner"]`, `open_questions.md`'s header, and the generated docs for all
five workflows. This is stable, hand-authored fixture content that long predates this task —
`tests/test_samples_wellformed.py::test_sample_json_has_the_planned_keys` already asserts
`meta["owner"] == "wf_owner"` by name, and dozens of other pre-existing tests across
`tests/test_intake_*.py` pass `user="wf_owner"` explicitly as their own test fixture value. It is
identical on every machine (read from a checked-in file, never from the environment), so it does
not compromise reproducibility, and renaming it now would be a large, high-risk, out-of-scope
change touching test files this task was not asked to rewrite. `tests/test_committed_workflows.py`
encodes this exact distinction: it asserts the exact-case string `<user>` never appears anywhere
under `workflows/`, while leaving the lower-case `wf_owner` alone.

**No absolute path of this PC** was found anywhere in the committed `workflows/` tree, in either
run.

## wf_0005 parser-recovery replay files — decided to exclude, not commit

`scripts/parsers/ext/acme_dedupe.py` and `tests/parser_corpus/acme_dedupe/**` are wf_0005's
parser-recovery agent's real output (MockRunner replays them verbatim from
`samples/wf_0005/canned/parser-recovery/**`), landing at the repo root because `orchestrate.ts`
was run with `--root .` (per the addendum's own note that this is where they land when run in the
repo root).

**Decision: excluded from this commit, with a new `.gitignore` entry explaining why** (next to the
two new lines in `.gitignore`). Reasoning, established by actually trying the alternative first
(accepting them into the corpus), not by inspection alone:

1. `parse.py`'s `DEFAULT_EXT_DIR` (`scripts/parsers/ext/`) is **always** loaded by `parse_file`,
   regardless of any `ext_dirs` a caller passes (`scripts/parse.py:72`). Committing
   `acme_dedupe.py` there permanently would make every future `parse.py wf_0005 --check` — from a
   fresh checkout, with no recovery agent ever having run — succeed on the **first** attempt,
   because the extension is already loaded by default. wf_0005 exists specifically to exercise the
   parser-recovery path (`samples/wf_0005/README.md`); permanently fixing its own trigger would
   retire that sample's purpose for good, silently, the next time anyone reruns Step 1's sequence.
2. Confirmed this is not hypothetical by actually trying to accept the fixture into the permanent
   corpus (add `"acme_dedupe"` to `tests/parser_corpus/test_corpus.py`'s `FIXTURES` set, per that
   fixture's own `README.md`, which explicitly invites this). Doing so broke:
   - the fixture's own `test_the_fragment_fails_invariant_8_without_the_extension` (its "without
     the extension" premise stopped being true, because the extension was now sitting in
     `DEFAULT_EXT_DIR` for the whole test session);
   - four *other*, pre-existing tests that assume wf_0005's own real sample
     (`samples/wf_0005/source/vendor_dedupe.yxmd`) trips `INVARIANT_VIOLATION` with no extension
     present: `tests/test_build_samples.py::test_build_wf_0005_reports_no_golden_sets_without_raising`,
     `::test_build_wf_0005_leaves_no_stale_intermediates_or_outputs_from_an_earlier_state`,
     `tests/test_canned_artifacts.py::test_the_recovery_extension_registers_and_explains_the_unknown_tool[wf_0005]`,
     `::test_the_recovery_corpus_test_passes_from_a_replayed_tree[wf_0005]`, and
     `tests/test_parse.py`'s five tests parametrized on an "unknown tool" scenario built the same
     way.
   I also tried fixing just the fixture's own test with a `monkeypatch` on `DEFAULT_EXT_DIR`, which
   fixed that one test but then broke `test_canned_artifacts.py`'s own harness, which calls every
   corpus test function directly with zero arguments (`getattr(module, case)()`, bypassing pytest's
   fixture injection) — incompatible with a function that now requires `tmp_path`/`monkeypatch`.
   Reverted both attempts back to the original, unmodified content (confirmed byte-identical to
   `git show HEAD:...` for the tracked canned-source copy) rather than leave a partial fix that
   only worked for one caller.
3. Given (1) and (2), committing these files would trade a one-time cosmetic tidiness for a
   permanent, silent loss of wf_0005's own designed purpose and four unrelated tests' correctness.
   Excluded instead, with the `.gitignore` comment naming exactly this reasoning and pointing at
   `samples/wf_0005/canned/parser-recovery/tests/parser_corpus/acme_dedupe/README.md`.

`workflows/wf_0005/parsed/parse_diagnosis.md` (the recovery agent's own diagnosis, a workflow-scoped
output under `workflows/`, not a repo-wide parser extension) **is** committed — it is legitimate
evidence of what happened in this run and carries no reproducibility risk of its own.

## `mappings/global.yaml` — reverted, not committed

Running the pipeline in the repo root (per the addendum's override) has a real side effect Task
15/16 never hit, because they ran in scratch roots: `intake_prompt.py`'s `apply_answers` promotes
every newly-confirmed mapping into the shared `mappings/global.yaml`. Running the real sequence
mutated it — five sources and six outputs got promoted, `confirmed_by` included. Reverted to its
exact committed content (via `git show HEAD:mappings/global.yaml`, never `git checkout --`) before
the final commit, because a large share of the existing intake test suite (`tests/test_intake_*.py`)
copies this file wholesale as a **pristine** fixture (`sources: {}`, `outputs: {}`); leaving it
populated would have pre-resolved wf_0001-0004's touchpoints from the wrong place in every one of
those tests and silently skipped the `WAITING_FOR_ANSWERS` state they exist to exercise (confirmed
by literally observing six pre-existing tests fail this way in `tests/test_intake_cli.py` alone
before reverting). `git diff mappings/global.yaml` is empty in the final commit.

## Verification (clean working tree, exact output)

```
$ .venv/Scripts/python.exe -m pytest
........................................................................ [  7%]
........................................................................ [ 14%]
........................................................................ [ 22%]
........................................................................ [ 29%]
........................................................................ [ 36%]
........................................................................ [ 44%]
........................................................................ [ 51%]
........................................................................ [ 58%]
........................................................................ [ 66%]
........................................................................ [ 73%]
........................................................................ [ 80%]
........................................................................ [ 88%]
........................................................................ [ 95%]
..........................................                               [100%]
978 passed in 95.20s (0:01:35)
```

```
$ .venv/Scripts/python.exe -m pytest -m e2e
.....................                                                    [100%]
21 passed, 957 deselected in 26.62s
```

(`-m e2e` is a subset of the plain run above — `978 - 21 = 957`, matching "957 deselected"
exactly.)

```
$ fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts
1..92
# tests 92
# suites 0
# pass 92
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 4684.8203
```

```
$ fnm exec --using=22 node.exe node_modules/typescript/bin/tsc --noEmit -p .
(clean, exit 0)
```

**All four green, zero skips. Nothing red.**

Baseline before this task's own changes (for context, not a claim about what "should" be true):
`.venv/Scripts/python.exe -m pytest` alone (no `workflows/` yet, no new test file, before the
`intake_prompt.py` fix) reported 960 passed. The +18 to reach 978 is exactly
`tests/test_committed_workflows.py`'s own 18 tests; no other test file's count changed net (3 tests
added to `tests/test_intake_cli.py`, 0 net change elsewhere).

## Decisions made where the addendum left room

1. **`confirmed_by`'s new default is the literal string `"automation"`**, not e.g. the actual
   `USERNAME`/`USER` environment variable or a config-driven value. Chosen because the alternative
   still leaks a machine-specific string into a committed file for anyone who runs this offline
   sequence for real; a fixed, obviously-synthetic marker says clearly "an automated resume wrote
   this, not a person" without inventing a name.
2. **`scripts/parsers/ext/acme_dedupe.py`/`tests/parser_corpus/acme_dedupe/**` excluded**, with a
   `.gitignore` entry rather than silence — see above. This is the one place I actually reversed an
   earlier decision (I first tried committing it and adopting it into the permanent corpus) after
   the evidence came in.
3. **`mappings/global.yaml` reverted to its committed content**, not committed with its
   promotions — see above.
4. **The re-running/parking ruling** (escalated stages `NEEDS_HUMAN`/`QUARANTINED`/`BLOCKED` are
   parked by a plain re-run; only `--from-stage` reopens them; `WAITING_FOR_ANSWERS` re-runs its own
   scripts every pass) is described in the README exactly as given in my brief, as the intended
   behavior of a fix landing separately (`.worktrees/task-15-fix`, never touched by this task).
   None of the five committed workflows ever reached an escalated state, so this task's own actual
   run never exercised the current (pre-fix) code path that would contradict it — the README
   describes the design ruling, not something this task itself re-verified against the current
   `orchestrator/stages.ts`.
5. **The fixer-loop demonstration in the README** uses a separate, uncommitted scratch run
   (`--scenario fix-loop:seg_01 --only wf_0001`) rather than forcing one of the five committed
   workflows through a real fix iteration, since none of the canned segments actually needed one
   and manufacturing a fake failure in the committed tree would misrepresent what the real run
   produced.

## README sections I am least sure are accurate

- **§11's "Verify against your CLI/SDK build" checklist**, specifically the `effortLevel`/
  `contextTier`/`subagents.agents` items: I read `orchestrator/runner.ts` and `orchestrator/agents.ts`
  closely and cross-referenced `docs/live-smoke-test.md`, but I did not myself run a new live
  session against `config.json`'s `subagents.agents` routing — my "still open" / "partially
  verified" splits there are my own reading of what Task 16's live evidence does and does not cover,
  not a fresh live test of my own.
- **§8's Snowflake-backend guidance** (what a real `SnowflakeBackend` deployment needs) is written
  from reading `scripts/lib/backend.py`'s own docstring and the DDL files' headers; nothing in this
  task exercised `SnowflakeBackend` itself (it remains explicitly untested, as the file's own
  docstring says).
- **The `--only wf_0001 --interactive --runner copilot --profile local` command in §7** ("adding a
  real workflow") is constructed from `orchestrator/cli.ts`'s flag parsing and has the right shape,
  but I did not run it end-to-end against a live model in this task (Task 16 already did the live
  BYOK test, on `wf_0001`, and it did not complete intake — see `docs/live-smoke-test.md`, which
  the README points to).

## Files changed

- `workflows/wf_0001..wf_0005/**` (new) — the committed offline run, 322 files.
- `scripts/intake_prompt.py` — the `--user` default fix (see above).
- `tests/test_intake_cli.py` — 3 new tests locking in the fix.
- `tests/test_committed_workflows.py` (new) — 18 tests against the real, git-tracked `workflows/`
  tree: terminal states, segment/validation verdicts, tracked-file hygiene, and the
  absolute-path/user-name audit.
- `.gitignore` — two new lines excluding wf_0005's parser-recovery replay output, with the
  reasoning inline.
- `README.md` (new).
- This report.

## Self-review

- Every number in this report and in the README is either copied from a command I ran in this
  session (shown above) or points at the file that has it (`task-17-report.md` itself, never a
  count baked into `README.md`, per the addendum).
- The two places I found something wrong (`confirmed_by`'s leak, and the acme_dedupe/permanent-corpus
  conflict) were both found by actually running the sequence for real and then verifying
  reproducibility in an independent scratch copy — not by inspection alone. The acme_dedupe finding
  in particular only surfaced because I ran the *full* test suite after regenerating `workflows/`,
  rather than just the one new test file I had just written.
- I did not touch `orchestrator/stages.ts`, `orchestrator/policy.ts`, or anything else under
  `.worktrees/task-15-fix`'s ownership, per the coordinator's boundary.
