# Task 6B — the offline run for all six samples and the refreshed committed `workflows/`

**Status: DONE_WITH_CONCERNS.** Everything the brief asks for is done and green. The one place
reality contradicts the brief is `manifest.output_kind` on the tier-T3 workflow (`wf_0005`), which
does **not** get one — see "Brief corrections" below. That is merged Task 6A behaviour, not a
regression, and it is now pinned by a test and stated in the README.

Worked entirely in `C:\Users\<user>\Desktop\Alteryx to Snowflake\.worktrees\ot-task-6b`
(branch `wt/ot-task-6b`, base `71c3bf5`). No `git stash` / `checkout --` / `reset --hard`; no
subagents; no other worktree or the main tree touched. Scratch roots under
`…\scratchpad\ot-6b\` (`run1` = the run that was committed, `run2` = the independent
reproducibility export, `base` = a `git archive 71c3bf5` export used only to count the baseline
test collection, `before-pass3` / `norm` = diff snapshots).

## Commits

```
91e3a78 wip: offline run refreshed for all six samples — workflows/ and the invariants that pin it
7368568 feat: offline run refreshed for all six samples — wf_0006's Snowpark segment committed; targets.json and output_kind in every workflow
```

---

## Step 1 — the run, in a scratch root

`scripts`, `mappings`, `catalog` were copied **from the worktree** (the code under test);
`samplesDir` points at the **worktree's** `samples/`; `python` is the main checkout's venv.

```bash
WT="C:/Users/<user>/Desktop/Alteryx to Snowflake/.worktrees/ot-task-6b"
SCRATCH=".../scratchpad/ot-6b/run1"
REPO="C:/Users/<user>/Desktop/Alteryx to Snowflake"
FNM="C:/Users/<user>/AppData/Local/Microsoft/WinGet/Links/fnm.exe"

mkdir -p "$SCRATCH"
cd "$WT"
cp -r scripts mappings catalog "$SCRATCH/"
printf '{"python": "%s/.venv/Scripts/python.exe", "samplesDir": "%s/samples"}' "$REPO" "$WT" \
  > "$SCRATCH/orchestrator.config.json"

# 1. seed
"$REPO/.venv/Scripts/python.exe" scripts/dev/build_samples.py seed --root "$SCRATCH" --samples "$(pwd -W)/samples"
# 2. pass 1 — everything parks at WAITING_FOR_ANSWERS
"$FNM" exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --runner mock --no-interactive
# 3. answers
"$REPO/.venv/Scripts/python.exe" scripts/dev/answer_samples.py --root "$SCRATCH" --samples "$(pwd -W)/samples"
# 4. pass 2 — terminal states
"$FNM" exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --runner mock --no-interactive
# 5. pass 3 — no-op
"$FNM" exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --runner mock --no-interactive
```

Seed output: `wf_0001: seeded (AMP)` … `wf_0006: seeded (AMP)` (all six).
Answer output: `wf_0001: 3 answered, 0 unanswered` … `wf_0006: 2 answered, 0 unanswered`.

Pass 1 (all six park):

```
wf_0001  parse=PARSED intake=WAITING_FOR_ANSWERS
…
wf_0006  parse=PARSED intake=WAITING_FOR_ANSWERS
```

Pass 2 (exit 0):

```
wf_0005: target_check: unknown nodes in wf_0005; the analyzer decides
wf_0005: tier T3 — translation stays manual (see unsupported.json)
wf_0001  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0002  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0003  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0004  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0005  parse=PARSED intake=READY analyze=DONE translate=MANUAL
wf_0006  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
```

(The `target_check: unknown nodes` line is the merged fix-round-1 C1 behaviour: exit 1 writes
`targets.json` and the analyzer still runs.)

### Terminal states actually observed (read back out of the six `manifest.json` files)

| workflow | tier | parse | intake | analyze | golden | translate | output_kind | segment_status |
|---|---|---|---|---|---|---|---|---|
| wf_0001 | T1 | PARSED (attempts 1) | READY | DONE | DONE | **VALIDATED** | procedures | seg_01: PASS |
| wf_0002 | T1 | PARSED (attempts 1) | READY | DONE | DONE | **VALIDATED** | procedures | seg_01/02/03: PASS |
| wf_0003 | T1 | PARSED (attempts 1) | READY | DONE | DONE | **VALIDATED** | procedures | seg_01/02: PASS |
| wf_0004 | T1 | PARSED (attempts 1) | READY | DONE | DONE | **VALIDATED** | procedures | seg_01/02/03: PASS |
| wf_0005 | T3 | PARSED (attempts 2) | READY | DONE | *(never runs)* | **MANUAL** | **absent** | none |
| wf_0006 | T2 | PARSED (attempts 1) | READY | DONE | DONE | **VALIDATED** | procedures | seg_01/02/03: PASS |

`segments/targets.json` exists for **all six**. Its `segments` map:

```
wf_0001 {'seg_01': 'sql'}
wf_0002 {'seg_01': 'sql', 'seg_02': 'sql', 'seg_03': 'sql'}
wf_0003 {'seg_01': 'sql', 'seg_02': 'sql'}
wf_0004 {'seg_01': 'sql', 'seg_02': 'sql', 'seg_03': 'sql'}
wf_0005 {'seg_01': 'sql'}                       (+ nodes {"2": "unknown", "3": "manual"})
wf_0006 {'seg_01': 'sql', 'seg_02': 'snowpark', 'seg_03': 'sql'}
```

### wf_0006 / seg_02 — the Snowpark evidence

- `contract.json`: `"target": "snowpark"` (seg_01 and seg_03: `"target": "sql"`).
- `proc.py` present; `proc.sql` present and **byte-equal to `render_snowpark.render(proc.py,
  "wf_0006", "seg_02", "3.11")`** — checked in-process:
  `byte-equal to render(): True` (2127 bytes each; runtime resolved from
  `mappings/global.yaml`'s `program.snowpark_runtime`).
- `compile_check.json`: `{"status": "OK", "target": "snowpark", "errors": [], "statements": 0}`.
- `validation.json`: `verdict=PASS target=snowpark idempotent=True golden_set=normal` — the
  `"target": "snowpark"` key is written **only** by `validate_snowpark.py`
  (`validate_snowpark.validate_snowpark`'s `extra={"target": "snowpark"}`), so seg_02 was served by
  it. The four per-set reports (`validation.{normal,edge,empty,period_end}.json`) are all PASS and
  carry **no** `target` key — by design: `lib.validation.write_reports` applies `extra=` to the
  top-level report only.
- seg_01 / seg_03 `validation.json` have no `target` key at all → `validate_segment.py` served
  them (its call passes no `extra=`). All five of each segment's reports are PASS.
- `procs/master.sql` calls all three in wave order:
  `WF0006_SEG_01` (wave 1) → `WF0006_SEG_02` (wave 2) → `WF0006_SEG_03` (wave 3), all with the C4
  five-argument signature.

### Pass 3 was a no-op

`workflows/` was snapshotted before pass 3 and diffed after:

```
$ diff -rq -x audit.jsonl -x '*.duckdb' -x '*.duckdb.wal' <before-pass3>/workflows <run1>/workflows
Files …/wf_0001/manifest.json and …/wf_0001/manifest.json differ
… (one line per workflow, six in total)
```

Every one of those six diffs is a single line:

```
24c24
<   "updated_at": "2026-09-22T20:39:36.872Z",
---
>   "updated_at": "2026-09-22T20:39:56.045Z",
```

Nothing else in the tree changed. (`audit.jsonl` never appears at all under `--runner mock`;
there is no such file anywhere in the run root. The README already documents pass 3 as "changes
nothing but each manifest's `updated_at`", which is exactly what happened.)

---

## Step 2 — the product copied into the repo

`rm -rf workflows/wf_000N` then `cp -r <run1>/workflows/wf_000N workflows/` for all six.
Result: 409 files under `workflows/` (was 322 tracked across five workflows;
327 for the refreshed five + 82 for wf_0006).

Hygiene, all checked against the scratch tree before copying:

- no `*.duckdb`, `*.duckdb.wal`, `audit.jsonl` or `__pycache__` anywhere under `workflows/` (find: 0 hits);
- `confirmed_by: automation` in every one of the six `intake/mappings.yaml` (15 occurrences, no other value);
- a full scan of all 409 files (404 text, 5 binary `.yxdb` skipped) for `<user>`, `C:\Users`,
  `C:/Users`, `/c/Users`, `scratchpad`, `temp\claude`, `temp/claude` and the regex
  `[a-zA-Z]:[\\/]Users[\\/]` → **0 hits**;
- `git check-ignore` over every wf_0006 file → nothing ignored;
- `git status` shows 59 modified + 6 untracked (the five new `targets.json` and `wf_0006/`) and
  **zero deletions**, i.e. no file the old tree had went missing.

### OLD committed `wf_0001..wf_0005` vs the NEW run — every kind of difference

Diffed file-by-file and classified by JSON key path (a script over both trees):

| kind of difference | files | detail |
|---|---|---|
| **(c)** new file `segments/targets.json` | 5 | one per workflow; nothing else is new and nothing was removed |
| **(a)** key added `output_kind = "procedures"` in `manifest.json` | 4 | wf_0001–wf_0004. **wf_0005 does not get one** (see Brief corrections) |
| **(b)** key added `"target": "sql"` in `contract.json` | 9 | every contract of wf_0001–wf_0004 (wf_0005 is T3 and has none) |
| **(d)** value changed `runtime_ms` in `validation*.json` | 45 | e.g. `212 -> 161`, `504 -> 647` — `validate_segment.py`'s own measured wall-clock |
| **(d)** value changed `updated_at` in `manifest.json` | 5 | e.g. `2026-09-20T01:32:53.901Z -> 2026-09-22T20:39:56.045Z` |

**Nothing else.** No `proc.sql` changed, no `dag.json`, no `analysis.md`, no golden CSV, no
`review.json`, no validation verdict, no manifest status, no `intake/`, no `docs/migration.md`,
no file removed. `runtime_ms` and `updated_at` are exactly the two volatile fields
`task-17-report.md` identified when it did the same check for the original run.

---

## Step 3 — tests (`tests/test_committed_workflows.py`)

Written **before** the new tree was copied in, and run against the old committed tree:

```
$ .venv/Scripts/python.exe -m pytest tests/test_committed_workflows.py -p no:randomly
23 failed, 18 passed in 0.44s
FAILED …::test_manifest_reaches_the_expected_terminal_state[wf_0006]
FAILED …::test_a_validated_workflow_also_finished_golden_and_document[wf_0006]
FAILED …::test_a_validated_workflow_has_every_segment_passing[wf_0006]
FAILED …::test_every_committed_workflow_has_a_targets_json[wf_0001 … wf_0006]        (6)
FAILED …::test_a_translated_workflow_records_the_decided_output_kind[wf_0001 … wf_0006] (5)
FAILED …::test_the_manual_t3_workflow_records_no_output_kind
FAILED …::test_no_committed_contract_raises_its_proposed_target[wf_0001 … wf_0006]   (6)
FAILED …::test_a_committed_snowpark_segment_carries_proc_py_its_rendered_proc_sql_and_a_snowpark_report
FAILED …::test_a_committed_sql_segment_s_validation_report_carries_no_target_key
```

Every failure is the expected one: the old tree has no `wf_0006`, no `targets.json`, no
`output_kind` and no `target` on any contract (`assert checked, "no committed segment has target
sql"` — zero contracts carried one).

**GREEN** after the copy: `41 passed in 0.14s` (18 → 41, i.e. **+23** tests; 0 skipped).

What was added:

- `EXPECTED_TERMINAL["wf_0006"] = "VALIDATED"` — which re-parametrises the three existing terminal
  tests over the sixth workflow too.
- `test_every_committed_workflow_has_a_targets_json` (× 6): `targets.json` exists, its
  `output_kind` and `preference` are in `{"procedures", "dbt"}`, it proposes for exactly the
  segments in `segments/order.json`, and every proposal is in the `sql/snowpark/manual` vocabulary.
- `test_a_translated_workflow_records_the_decided_output_kind` (× 5): `manifest.output_kind` in
  `{"procedures", "dbt"}` for every workflow that reached translate.
- `test_the_manual_t3_workflow_records_no_output_kind`: wf_0005 has `targets.json` but no
  `output_kind`, with the reason (the mirror lives in the verify callback, which returns early
  for T3) in the docstring.
- `test_no_committed_contract_raises_its_proposed_target` (× 6): the lower-only rule, with
  `TARGET_RANK = {"manual": 0, "snowpark": 1, "sql": 2}` mirroring `orchestrator/stages.ts`, plus
  a count check so a workflow cannot pass by having no contracts at all.
- `test_a_committed_snowpark_segment_carries_proc_py_its_rendered_proc_sql_and_a_snowpark_report`:
  for every `target: "snowpark"` contract — `proc.py` exists, `proc.sql` is byte-equal to a fresh
  `render_snowpark.render(...)` (catches a `proc.py` edited without re-rendering),
  `compile_check.json` and `validation.json` both carry `"target": "snowpark"`; asserts at least
  one such segment exists so it can never pass vacuously.
- `test_a_committed_sql_segment_s_validation_report_carries_no_target_key`: for every
  `target: "sql"` contract — no `proc.py` beside it and **no `target` key** in `validation.json`.
  (I read `validate_segment.py`/`lib/validation.py` for this, as the brief asked: it calls
  `write_reports` with no `extra=`, so the key is absent rather than `"sql"`.)

The machine-path / OS-login-name scan and the "no `.duckdb`/`audit.jsonl` tracked" checks are
untouched and now cover all six trees (they walk `git ls-files workflows`). The login-name test
has a `pytest.skip` guard for a too-generic login; the login here is `<user>`, so it runs — the
suite reports **0 skipped**.

Docstrings that said "five workflows" / pointed at the retired `task-17-addendum.md` sequence now
say six and point at README §6.

---

## Step 4 — the reproducibility check

A second, independent scratch root exported from the committed worktree tree:

```bash
S2=".../scratchpad/ot-6b/run2"
cd "$WT" && git archive HEAD | tar -x -C "$S2"
rm -rf "$S2/workflows"                     # re-run from nothing, not from the committed product
printf '{"python": "%s/.venv/Scripts/python.exe", "samplesDir": "%s/samples"}' "$REPO" "$S2" \
  > "$S2/orchestrator.config.json"
"$REPO/.venv/Scripts/python.exe" "$S2/scripts/dev/build_samples.py" seed --root "$S2" --samples "$S2/samples"
"$FNM" exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$S2" --runner mock --no-interactive
"$REPO/.venv/Scripts/python.exe" "$S2/scripts/dev/answer_samples.py" --root "$S2" --samples "$S2/samples"
"$FNM" exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$S2" --runner mock --no-interactive
```

This export uses its own `samples/`, `scripts/`, `mappings/`, `catalog/` — nothing from `run1`.
`scripts/parsers/ext/acme_dedupe.py` is **not** present in the export (it is gitignored), which is
the condition `task-17-report.md` flagged as easy to get wrong: without it, wf_0005's
parser-recovery path really runs, and it did (`parse.attempts: 2` reproduced identically).

Same terminal states as run 1, then:

```
$ diff -rq -x audit.jsonl -x '*.duckdb' -x '*.duckdb.wal' workflows "$S2/workflows"
```

60 `… differ` lines (54 `validation*.json`, 6 `manifest.json`), **no** "Only in …" line in either
direction. Classified key-by-key over all 409 files:

```
--- value differs: runtime_ms  (54 files)
--- value differs: updated_at  (6 files)
(files compared: 409; committed-only 0; rerun-only 0)
```

With those two volatile fields normalised in copies of both trees, the diff is literally empty:

```
$ diff -rq -x audit.jsonl -x '*.duckdb' -x '*.duckdb.wal' <norm>/a/workflows <norm>/b/workflows
DIFF_EXIT=0   (no output)
```

So all 409 committed files reproduce byte-for-byte from a clean export except
`manifest.json["updated_at"]` and `validation*.json["runtime_ms"]` — the same two fields the
README and `task-17-report.md` already name as volatile.

---

## Step 5 — README

- §6 opening: "The **five** sample workflows under `workflows/`" → "**six**".
- §6 terminal-states table: new row
  `| wf_0006 | T2 | PARSED | READY | DONE | DONE | **VALIDATED** | seg_01/02/03: PASS (seg_02 is the Snowpark one) |`.
- §6: one new paragraph, "**The worked Snowpark example is `workflows/wf_0006/segments/seg_02/`**"
  — `proc.py` is the source of truth, `proc.sql` the rendered deployable `LANGUAGE PYTHON` DDL,
  `compile_check.json` the `--target snowpark` verdict, `validation.json` carrying
  `"target": "snowpark"` because `validate_snowpark.py` ran it; seg_01/seg_03 stayed `sql` under
  `validate_segment.py`; `procs/master.sql` calls all three; and the wf_0005 `output_kind`
  exception stated plainly.
- §6 fixer-loop paragraph: "None of the **five** committed workflows" → "**six**".
- §10 repo map: "fixtures for the **5** sample workflows" → "**6**".
- §1 "Three output targets" table, two cells (see Brief corrections 2).

Nothing else in the README moved. **There is no second "sample table" in the README** — the only
table with per-workflow rows is §6's terminal-states table. The other per-sample statements are
the two prose counts and the repo-map line, all updated above.

---

## Step 6 — suites

| suite | command | result |
|---|---|---|
| pytest (whole) | `.venv/Scripts/python.exe -m pytest` (run from the worktree) | **1215 passed in 82.09s**, exit 0, **0 skipped** (re-run at the final commit; 83.49s on the first run) |
| node | `fnm exec --using=22 npm.cmd test` | **`# tests 176 / # pass 176 / # fail 0 / # skipped 0`** |
| tsc | `fnm exec --using=22 node.exe <main>/node_modules/typescript/bin/tsc --noEmit -p .` | clean, exit 0 |

Baseline at the dispatch base `71c3bf5`, measured by exporting it to a scratch root and running
`pytest --collect-only`: **1192 tests collected**. 1192 + 23 = 1215, so this task's only test-count
change is its own 23 new `tests/test_committed_workflows.py` cases; no other file's count moved.

`git status` after the full pytest run showed only the intended changes — the suite left no stray
`scripts/parsers/ext/acme_dedupe.py` or other artefact in the worktree.

---

## Brief corrections

1. **"every `manifest.json` has `output_kind: "procedures"`" is not true for `wf_0005`, and
   should not be.** Brief Step 1's bullet and Step 3's "every committed `manifest.json` has
   `output_kind`" both assume all six. In the merged Task 6A code, `manifest.output_kind` is set
   from `checkTargets`'s verdict inside `stageAnalyze`'s verify callback — and that callback
   returns `true` **before** calling `checkTargets` when `unsupported.json.tier === "T3"`, because
   a T3 workflow has no `contract.json` anywhere to check a target against
   (`orchestrator/stages.ts`, `stageAnalyze`). So `verdict` stays undefined and no `output_kind` is
   written. The design agrees: §3.2 says "the verify callback applies §3.2 and sets
   `manifest.output_kind`", and §3.2's verification is contract-by-contract.

   I did not change the orchestrator (that is not this task, and stamping `procedures` onto a
   workflow that produces nothing would be a claim the pipeline never made). Instead the tests
   pin the real behaviour on both sides: `test_a_translated_workflow_records_the_decided_output_kind`
   over the five translated workflows, and `test_the_manual_t3_workflow_records_no_output_kind`
   asserting the absence *and* that `targets.json` is still there. The README paragraph says it in
   one clause. **If the controller wants `output_kind` on T3 workflows too, that is a one-line
   change in `stageAnalyze` plus a node test, and it belongs to 6A's file, not 6B's.**

2. **Two cells of README §1's output-target table had to change**, which brief Step 5 ("No other
   README edits") did not anticipate. The SQL row read "built, and what every committed sample
   uses today" — false the moment `wf_0006/seg_02` is committed. It now reads "…what every
   committed segment except `wf_0006/seg_02` uses", and the Snowpark row gained
   "`workflows/wf_0006/segments/seg_02/` is the committed worked example (§6)". This is the
   honesty rule, not a scope expansion; nothing else in §1 moved.

3. **Brief Step 1's "the third (no-op) pass changed nothing (`diff -rq` … empty apart from
   `audit.jsonl`)" and Step 4's "must be empty"** are only literally true once `updated_at` /
   `runtime_ms` are excluded — `saveManifest` restamps `updated_at` on every save and
   `validate_segment.py`/`validate_snowpark.py` measure their own wall clock. README §6 and
   `task-17-report.md` both already say so. I report the raw `diff -rq` output, the key-level
   classification, and a normalised diff that is empty, rather than quietly claiming an empty one.

---

## Concerns

1. **The wf_0005 `output_kind` gap** (Brief corrections 1). It is behaviour, tested and documented,
   but it is the one thing a reviewer reading the brief will expect to see and will not.

2. **A scratch root's `mappings/global.yaml` is rewritten, losing its comments.** README §6 says
   "`mappings/global.yaml` is untouched by this whole sequence". After the run, the *scratch copy*
   parses to an **identical** value (`yaml.safe_load(a) == yaml.safe_load(b)` → `True`, and
   `sources`/`outputs` are still empty, so nothing was promoted) — but the file has been
   re-serialised: comments stripped, `'3.11'` re-quoted, `accepted_diff_classes` re-flowed to a
   block list. The repo's own `mappings/global.yaml` is untouched (it is only *copied out*), so
   nothing committed here is affected and no test is affected. It is pre-existing behaviour, not
   something 6A or 6B introduced, but README §6's word "untouched" is slightly stronger than what
   a scratch root actually shows. I did not change the README for it — it is out of this task's
   scope and would need a look at `intake_prompt.py`'s write path to describe correctly.

3. **`runtime_ms` is a wall-clock number inside 132 committed files.** It makes `diff -rq` on a
   re-run noisy for anyone doing the reproducibility check by eye (60 "differ" lines that are all
   benign). Nothing in this task's scope changes that — `validate_segment.py` has written it since
   Task 17 — but a future task that wants a literally-empty `diff -rq` would have to drop the
   field or normalise it.

4. **The committed `workflows/` tree is now 409 files.** Eighty-seven more than before. That is
   what committing a sixth worked example costs and the brief asked for it, but it is a big share
   of the repo's diff surface for anything that touches a script under `scripts/`.

5. **Nothing here ran on real Snowflake or real Alteryx.** `wf_0006/seg_02`'s `proc.sql` has never
   been executed by Snowflake; `validate_snowpark.py` ran `proc.py` in the Snowpark **Local
   Testing Framework**, and every SQL segment ran on DuckDB via `sqlglot`. The README and the new
   test docstrings say so; no wording I added implies otherwise.

## Files changed

- `workflows/wf_0001 … wf_0006/**` — the refreshed offline product (409 files: 59 modified, 6 new
  paths, 0 deleted).
- `tests/test_committed_workflows.py` — +23 tests (18 → 41) and the six-workflow docstring fixes.
- `README.md` — §1 two table cells, §6 count + wf_0006 row + the Snowpark paragraph + the fixer-loop
  count, §10 repo-map count.
- This report.

## Self-review

- Every number and every quoted line above came out of a command run in this session; nothing is
  recalled or estimated. The baseline 1192 was measured, not taken from an earlier report.
- The old-vs-new classification was done **before** anything was overwritten, key by key over both
  trees rather than by reading a `diff -rq` file list, which is how the "nothing else changed"
  claim is actually supported.
- Tests were RED against the real old tree (not a simulated one), and the RED list was saved before
  the tree was replaced.
- The reproducibility export deliberately deletes its own `workflows/` and uses its own `samples/`,
  so it re-derives the product rather than confirming a copy of it.

---

# Fix round 1

Review **APPROVED** the task with no findings. This round applies the controller's single ruling
on my own concern 1: spec §3.3 says `targets.json.output_kind` is **mirrored** into
`manifest.output_kind`, and `target_check.py` writes `targets.json` for a tier-T3 workflow too, so
`stageAnalyze`'s T3 early return must not skip the mirror. **The T3 `output_kind` exception
described in my first report no longer exists.**

Commit: **`459f5bc`** — *wip: fix round 1 — a T3 workflow's manifest mirrors output_kind too;
wf_0005 refreshed*.

## Counts

| suite | command | result |
|---|---|---|
| node | `fnm exec --using=22 npm.cmd test` | **`# tests 177 / # pass 177 / # fail 0 / # skipped 0`** (was 176) |
| typecheck | `… tsc --noEmit -p .` | clean, exit 0 |
| pytest (whole) | `.venv/Scripts/python.exe -m pytest` | **1215 passed in 84.18s**, exit 0, **0 skipped** (unchanged count: one test deleted, one widened from 5 to 6 parametrisations) |

## 1. `orchestrator/stages.ts` — the mirror runs for T3 too

**RED first.** New node test `a T3 workflow's manifest still mirrors targets.json's output_kind`
(`orchestrator/test/stages.test.ts`, next to the existing T3 tests) against the unchanged code:

```
not ok 121 - a T3 workflow's manifest still mirrors targets.json's output_kind
  error: |-
    Expected values to be strictly equal:
    + actual - expected
    + undefined
    - 'procedures'
# tests 177 / # pass 176 / # fail 1 / # skipped 0
```

It asserts `m.tier === "T3"`, `m.status.translate === "MANUAL"`,
`m.output_kind === "procedures"`, **and** that the manifest's kind equals the one in that
workflow's own `segments/targets.json` on disk — so it pins a *mirror*, not a hard-coded
constant. No existing test was touched.

**GREEN:** `# tests 177 / # pass 177 / # fail 0 / # skipped 0`.

The change, chosen so the non-T3 path stays byte-identical:

- `checkTargets`'s `targets.json` read is factored into a two-line `readTargets(env, m)` helper
  (same call, same fallback `{}`), plus a new `mirroredKind(env, m)` that returns
  `targets.json.output_kind === "dbt" ? "dbt" : "procedures"` — the same `procedures` default
  `target_check.py` itself resolves to when nothing says otherwise.
- In `stageAnalyze`'s verify callback, the T3 early return now sets the verdict before returning:
  `verdict = { ok: true, outputKind: await mirroredKind(env, m), lowered: [] }`. The existing
  `if (verdict) { … m.output_kind = verdict.outputKind; }` block after `runAgent` then does the
  write, so there is exactly one place that assigns `output_kind`.
- `checkTargets` and every non-T3 code path are otherwise unchanged: `proposedKind` stays
  `undefined` for T3, so the "proposed dbt but a contract lowered a segment off sql" log line
  cannot fire for a workflow that has no contracts, and `lowered` is empty so nothing is logged.
  §3.2's lower-only verification still does not run for T3 — there is nothing to verify.

Docs updated:

- `docs/reference/output-targets.md` §2 step 4: a new paragraph after the `output_kind` sentence —
  a T3 workflow has no contracts so none of the verification applies to it, but `targets.json` was
  written all the same and §3.3's mirror is not conditional, so `manifest.json.output_kind` is
  `targets.json.output_kind` verbatim; every workflow the analyzer finishes carries the decided
  kind whether or not anything was built from it.
- `README.md` §6: the sentence I wrote about the wf_0005 exception now reads "… are in all six
  trees, wf_0005 included: it is tier T3 and has no contracts for the lower-only check to run
  against, but the kind `target_check.py` decided is still mirrored into its manifest."

## 2. `workflows/` refreshed by re-running, not by hand

The whole README §6 sequence again, in a fresh scratch root
(`…/scratchpad/ot-6b/fix1-run1`), identical commands to Step 1 of the first report — seed, pass 1,
`answer_samples.py`, pass 2, pass 3 — with `scripts mappings catalog` copied from the worktree and
`samplesDir` pointing at the worktree's `samples/`. Terminal states unchanged (wf_0001–4 and
wf_0006 `VALIDATED`, wf_0005 `MANUAL`); pass 3 again changed nothing but each manifest's
`updated_at` (six one-line diffs, nothing else in the tree).

Hygiene re-checked before copying: no `*.duckdb`, `audit.jsonl` or `__pycache__` anywhere;
`confirmed_by: automation` the only value in all six `intake/mappings.yaml`; a scan of all 409
files (404 text, 5 binary `.yxdb`) for `<user>`, `C:\Users`, `C:/Users`, `/c/Users`,
`scratchpad`, `temp\claude`, `temp/claude` and `[a-zA-Z]:[\/]Users[\/]` → `scanned=404
binary_skipped=5 hits=0`.

**Difference from the previous commit (`7368568`), classified key by key over both trees:**

```
--- key added: output_kind = 'procedures'  (1 files)   wf_0005\manifest.json
--- value changed: runtime_ms  (52 files)
--- value changed: updated_at  (6 files)
```

Nothing else: no file added or removed, no other key added or removed, no value changed anywhere
else. `git status` after the copy showed 58 modified files and **zero** untracked or deleted.
`git diff workflows/wf_0005/manifest.json` is exactly the two lines expected:

```
-  "updated_at": "2026-09-22T20:39:56.048Z",
+  "updated_at": "2026-09-22T21:05:56.314Z",
   "tier": "T3",
+  "output_kind": "procedures",
```

## 3. `tests/test_committed_workflows.py`

- `test_the_manual_t3_workflow_records_no_output_kind` — **deleted** (the behaviour it pinned is
  the one that changed).
- `test_a_translated_workflow_records_the_decided_output_kind` → renamed
  **`test_every_committed_workflow_records_the_decided_output_kind`** and parametrised over
  `WORKFLOW_IDS` (all six) instead of `VALIDATED_WORKFLOW_IDS`. It no longer just checks the
  vocabulary: it reads `segments/targets.json` and asserts the manifest against it — a verbatim
  match for T3, and for every other workflow the §3.2 rule (`dbt` survives only while every
  contract is still plain `sql`, else `procedures`), with the contracts listed in the failure
  message.
- `test_no_committed_contract_raises_its_proposed_target` keeps its T3 special case (a workflow
  with no contracts is skipped segment-by-segment, and the "one contract per segment" count check
  still only applies to non-T3).

**RED first** against the old committed `wf_0005/manifest.json`:

```
$ .venv/Scripts/python.exe -m pytest tests/test_committed_workflows.py -p no:randomly
>       assert manifest["output_kind"] in OUTPUT_KINDS
E       KeyError: 'output_kind'
FAILED …::test_every_committed_workflow_records_the_decided_output_kind[wf_0005]
1 failed, 40 passed in 0.20s
```

**GREEN** after the refreshed tree was copied in: `41 passed in 0.15s` (0 skipped; the file's total
is unchanged at 41 — one test removed, one parametrisation added).

## 4. Reproducibility, re-done

Second independent export, this time from the fix commit:
`git archive HEAD | tar -x -C <fix1-run2>`, its own `workflows/` deleted first, its own
`samples/`/`scripts/`/`mappings/`/`catalog/`, and `scripts/parsers/ext/` again containing only
`README.md` and `__init__.py` (so wf_0005's parser-recovery path really ran). Same terminal states.

```
$ diff -rq -x audit.jsonl -x '*.duckdb' -x '*.duckdb.wal' workflows <fix1-run2>/workflows
58 lines, 0 of them "Only in …"
```

Classified over all 409 files:

```
--- value differs: runtime_ms  (52 files)
--- value differs: updated_at  (6 files)
(files compared: 409; committed-only 0; rerun-only 0)
```

With those two volatile fields normalised in copies of both trees:

```
$ diff -rq -x audit.jsonl -x '*.duckdb' -x '*.duckdb.wal' <norm>/a/workflows <norm>/b/workflows
DIFF_EXIT=0   (no output)
```

wf_0005's `output_kind: "procedures"` reproduces from a clean export, so the new mirror is
deterministic like everything else in the tree.

## Concerns after this round

1. **Concern 1 of the first report is closed** -- a T3 workflow now records the decided kind, the
   README and `docs/reference/output-targets.md` say so, and both the node suite and the
   committed-tree suite pin it. The remaining concerns from the first report (the scratch
   `global.yaml` re-serialisation, `runtime_ms` noise in a re-run diff, the 409-file tree, and the
   standing honesty note that nothing has run on real Snowflake or Alteryx) are unchanged.
2. **`mirroredKind` treats anything that is not the literal `"dbt"` as `procedures`**, including a
   missing or malformed `targets.json`. That matches `target_check.py`'s own default and keeps the
   type honest (`OutputKind` has exactly two members), but it means a corrupted `targets.json` on a
   T3 workflow would silently record `procedures` rather than parking. A T3 workflow builds nothing
   either way, and parking on it would be new behaviour the ruling did not ask for, so I did not
   invent it -- worth a sentence if a reviewer disagrees.
