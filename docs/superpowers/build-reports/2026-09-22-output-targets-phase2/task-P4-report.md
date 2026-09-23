# Task P4 report — `docs/handoff-production.md` and `docs/production-backlog.md`

Worktree `.worktrees/p2-P4`, branch `wt/p2-P4`, dispatched from `e1a5bdf`. C4V landed on the
integration branch during the task (`5e3d8a9`), so, per the ledger's P4 ruling, the integration head
was merged in (`964dcd4`) before finishing; the final commit is squashed onto `5e3d8a9` (see Commits).

## What I implemented

- **`docs/handoff-production.md`** (new, ~970 lines): the production hand-off written FOR AN AGENT —
  imperative, exact commands, a **Verify.** checklist after every step of parts 0–3, **Pass.** and
  **Record.** for every rung of the ladder, and a closing `## Never do`. Sections, in the order the
  brief's test pins: First week (links `docs/production-backlog.md` as the **first-week checklist**);
  0. who runs what (agent / human / the pipeline's role agents and what `orchestrator/policy.ts` denies
  them), prerequisites, and the **run root** recipe; 1. Copilot Enterprise models (human login →
  `list_models.ts` → `set_models.py` → `--check-models` → settling `WRITE_TOOL`/`SQL_TOOL` on the first
  hosted run); 2. Snowflake access (grants from `snowflake-backend.md` §5, the human's
  `connections.toml` entry — key pair or SSO, never a password — the sandbox allow-list, dbt-snowflake
  and its environment, the real column catalog export, the Snowflake-path commands, `deploy.py` dry run
  → `--execute` with the overwrite warning and the `GET_DDL` backup); 3. real Alteryx workflows
  (`survey_corpus.py` → `plugin_map.py`/cookbook/simulator → one workflow into a run root → parse,
  intake, analyze (human answers `open_questions.md` or the interactive prompt) → golden capture with
  `inject_outputs.py` + `AlteryxEngineCmd.exe` (human) → translate/validate/document → human review);
  4. the verification ladder (rung 1 offline mock reproduces the committed `workflows/` with a
  normalise-and-diff; rung 2 hosted models on the samples; rung 3 real Snowflake sandbox, whose FIRST
  step is `deploy.py wf_0001 … --execute` into the sandbox then `validate_segment.py wf_0001 seg_01
  --backend snowflake` — the first test of the `LET`/`IDENTIFIER(:var)`/`$$` procedure form, with the
  controller's documentation citations; rung 4 one real workflow; rung 5 a batch), with a verdict-table
  and a status-table helper; 5. everything never proven (spec §9 incl. the two Snowpark mock limits,
  dbt-duckdb vs Snowflake, DuckDB vs Snowflake, the simulator vs Alteryx, every `snowflake-backend.md`
  §7 item, hosted models, SDK tool names, compaction live, Alteryx capture, `gh`, the character-to-token
  estimate, prompt injection, one known defect with its workaround); Never do.
- **`docs/production-backlog.md`** (new): an opening paragraph saying everything is written down and
  **not built**, and that every number was **copied from a command** run at write time (the command is
  named beside each). Fourteen `## ` items, each with the four bold-led fields: the ledger's eleven
  (Alteryx tool coverage; Sources and sinks outside Snowflake; Golden-data governance; Parallel run and
  reconciliation before switching off Alteryx; Scheduling; Environment promotion and rollback;
  Continuous integration; Reproducible installs — quoting `pip freeze` lines; Cost and status
  visibility; Model evaluation; Prompt-injection tests), the dispatch's two (Segmenter scale; The
  dbt-snowflake adapter), and one I found (Golden capture for database targets — see Findings 2–3).
- **`tests/test_handoff_production.py`** (new, 30 tests): the brief's six tests verbatim, plus twelve
  for what the brief asks in prose: the two dispatch-added backlog items' four fields; the backlog's
  "not built" / "copied from a command" opening; the reproducible-installs item quotes `pip freeze`
  lines for nine packages; top-level files and `snowflake/`/`catalog/` paths named exist; every Node
  entry point named on a `--experimental-strip-types` line exists; **every `--option` on every
  documented `.venv/Scripts/python.exe scripts/…` command (continuation lines followed) appears in that
  script's own `--help`**; **every option on every documented `orchestrate.ts` line is a `case` in
  `orchestrator/cli.ts`** (the brief's `_FLAG` only sees the first per line); every step of parts 1–3
  has a **Verify.**; the ladder has exactly rungs 1–5, each with **Pass.** and **Record.**; rung 3
  deploys `wf_0001` before validating `wf_0001 seg_01` on `--backend snowflake`; the Never-do list names
  every forbidden action the brief lists; the Copilot hand-off opens with the pointer; the README points
  at the guide from §1's targets section, §7 and §8, and at the backlog from §7.
- **Pointers.** `docs/handoff-copilot-models.md` opens with "Production hand-off in full:
  `docs/handoff-production.md` …". README §1 "Three output targets" (a closing sentence), §7 (a lead
  paragraph pointing at the guide's §3 and the backlog) and §8 (a lead paragraph pointing at the
  guide's §1–2). Not the Mermaid diagrams.

### Edits beyond "pointers" (disclosed; each corrects a statement that is now false and that an agent following the guide would trip on)

1. `docs/handoff-copilot-models.md` §0: "The dbt target is phase 2 and is not built." → built
   (`docs/reference/output-targets.md` §3.3).
2. `docs/handoff-copilot-models.md` §2: the `set_models.py` command gains the five `--role` flags,
   with two sentences why (Finding 1).
3. `docs/handoff-copilot-models.md` §5 step 4: "Build a scratch root exactly as README §6 shows" →
   the guide's §0.3 run root (README §6's minimal recipe copies no `.github/agents/`, so a Copilot
   session gets NO custom agents, and its minimal config's hosted profile falls back to the placeholder
   ids — Finding 1's mechanism).
4. README §7 step 3: `--root .` (runs the pipeline in the repo root, which every other doc forbids)
   → `--root <run root>` plus one sentence on what a run root is.
5. README §8: "install `snowflake-connector-python` into `.venv`, pass `kind="snowflake"` plus real
   `connect_args` to `get_backend`" (both false since P2: the connector is installed — `pip freeze`
   shows `snowflake-connector-python==4.7.5` — and `SnowflakeBackend` refuses every argument but a
   connection name) → the named-connection sentence; "the one code change needed for per-role context
   tiers" (done by P1) → `set_models.py` and `--check-models`.
6. `docs/reference/snowflake-backend.md` (P2's file, no owner this wave): the intro sentence and the §7
   "Agents" bullet said the policy does NOT deny `--backend`/`--connection`/`--sandbox-database`;
   W2's follow-up made it deny them (`script-backend`, `orchestrator/policy.ts` line ~1287, test
   "script-backend: --backend, --connection and --sandbox-database are denied in every spelling"). Both
   sentences corrected. The guide tells the agent to read this file first, so the contradiction had to
   go. If the controller prefers this file untouched, revert this one file; nothing else depends on it.

## Findings (real defects found while verifying the surface; none fixed in code — P4 is docs)

1. **`set_models.py --default X` as documented leaves five roles on the placeholder `gpt-6-astra`.**
   `set_models.py` (`_set_or_clear`) deletes `profiles.hosted.roleModels` when no `--role` is given;
   `loadConfig` in `orchestrator/cli.ts` merges `profiles.hosted` as `{...DEFAULT_CONFIG.profiles.hosted,
   ...onDisk.hosted}`, so `DEFAULT_CONFIG`'s `roleModels` (astra for intake, analyzer, translator, fixer,
   parser-recovery) comes back. Reproduced in a scratch root: after the documented command, the merged
   config routes those five roles to `gpt-6-astra`; `configuredModels` lists them under
   "orchestrator.config.json profiles.hosted.roleModels.*" although the file no longer contains the key.
   If the catalog has no `gpt-6-astra`, `--check-models` exits 1 with a confusing location; if it HAS an
   enabled one, the preflight passes and five roles run on the pricier model — against owner policy 1.
   Same mechanism for `roleContextTiers`/`roleReasoningEffort` if a call omits their flags. Workaround
   (verified in the same probe: every role then resolves to the default id, `configuredModels` lists no
   stray id): pass `--role <role>=<id>` for the five roles — now in the guide §1.3 and the Copilot
   hand-off §2. **Suggested code fix (controller's ruling):** `set_models.py` writes the three maps
   explicitly (an empty `{}` survives the merge and means "no override"), or `loadConfig` stops
   inheriting per-role maps from `DEFAULT_CONFIG` when the on-disk hosted profile exists.
2. **`inject_outputs.py --import-set` does not record `manifest.golden_sets`.** `stageGolden` checks
   only `m.golden_sets`, so with `golden.producer: "alteryx"` a workflow parks at `BLOCKED` again after
   a successful import; `docs/reference/real-workflows.md` §5 ("only continues once a person has
   actually run it") is therefore not what happens. Workaround in the guide §3.5 (a JSON snippet,
   `normal` first); backlog item "Golden capture for database targets" names the fix.
3. **Database targets with append/merge or pre/post SQL cannot be captured by `inject_outputs.py`.**
   The capture taps the stream feeding the Output tool (`_output_points`), but the validators compare
   the target's state AFTER the write and load `golden/targets_before/<set>/<LOGICAL>.csv` first
   (`load_golden.py`; `sim_output` in `alteryx_sim.py` returns "the table's state afterwards" for such a
   db output). The guide §3.5 says to stop and escalate; the backlog item says what to build.
4. **`gh` publishes when present.** `stageIntake` runs `gh issue create --body-file
   intake/open_questions.md` on every WAITING_FOR_ANSWERS and `stagePr` runs `gh pr create --fill`. In
   the company's setting (where `gh` may be installed and signed in) that is publishing. The guide keeps
   run roots outside every git work tree with `GH_REPO` unset (verified by a Verify step) and passes
   `--stop-after document` on every orchestrate command.
5. **`scripts/parse.py --help` and `scripts/load_golden.py --help` crash** with `UnicodeEncodeError`
   ('→' in the help text) when stdout is a cp1252 pipe (Git Bash, or `subprocess` capture) — exit 1.
   Pre-existing, outside P4's files. The guide therefore names no `.venv/Scripts/python.exe
   scripts/parse.py …` command (the brief's `--help` test would fail on it); parsing happens through the
   orchestrator's parse stage.
6. **The segmenter is superlinear.** Measured here (scratch script, synthetic linear chains of
   `wf_0002`'s Formula node): 0.8 s / 11.5 s / 124.9 s at 300 / 1,000 / 3,000 tools; `cProfile` at
   1,000 tools: 1,879,000 `json.dumps` calls via `find_split` → `size_chars` → `prompt_chars`. Recorded
   in the backlog with the P3 review's ~29 s figure; memoising `prompt_chars` is the first step named.

## Facts stated as the controller verified them

- The procedure form (C4V): `LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>';`
  then `IDENTIFIER(:<LOGICAL>_SRC)`, `$$` bodies including `procs/master.sql`, with the two
  docs.snowflake.com citations; never run on an account; rung 3a is its first test. The old form is not
  written anywhere in the new text (C4V's `tests/test_documented_identifier_form.py` passes on the
  merged tree).
- External-location SQL denied (`external-location`), agents denied `--root`/`--backend`/
  `--connection`/`--sandbox-database`; the human runs every Snowflake-path command; sandbox databases
  must be in `policy.sandboxDatabases`.
- `wf_0007` is referred to by `samples/wf_0007` (no path into the not-yet-committed tree).
- Nothing about the local-model live test (Task H).
- Nothing claims a real Snowflake, Alteryx or hosted-model run; no machine path or login name (the
  hand-off scans pass).

## TDD evidence

RED (the new test file committed alone as `bf04eb2`, before any doc existed):

```
$ .venv/Scripts/python.exe -m pytest tests/test_handoff_production.py -rfs
FAILED tests/test_handoff_production.py::test_the_guide_sections_are_present_in_order
FAILED ...::test_the_guide_links_the_backlog_as_the_first_week_checklist
FAILED ...::test_every_backlog_item_has_its_four_fields
FAILED ...::test_every_named_path_exists
FAILED ...::test_every_orchestrate_flag_is_parsed
FAILED ...::test_the_two_added_backlog_items_have_their_four_fields
FAILED ...::test_the_backlog_says_it_is_written_down_not_built_and_where_its_numbers_come_from
FAILED ...::test_the_reproducible_installs_item_quotes_pip_freeze_lines_for_the_verified_stack
FAILED ...::test_every_other_named_repo_file_exists
FAILED ...::test_every_node_entry_point_named_exists
FAILED ...::test_every_option_on_a_documented_python_command_is_one_its_script_accepts
FAILED ...::test_every_option_on_a_documented_orchestrate_command_is_parsed
FAILED ...::test_each_step_of_the_first_three_parts_ends_in_a_verify_checklist
FAILED ...::test_each_rung_of_the_ladder_has_pass_criteria_and_what_to_record
FAILED ...::test_the_first_real_account_rung_deploys_wf_0001_into_the_sandbox_and_validates_it_there
FAILED ...::test_the_never_do_list_names_every_forbidden_action
FAILED ...::test_the_copilot_models_hand_off_opens_with_the_pointer
FAILED ...::test_the_readme_points_at_the_guide_from_the_targets_section_7_and_8_and_at_the_backlog_from_7
SKIPPED [1] tests\test_handoff_production.py:61: got empty parameter set for (script)
18 failed, 1 skipped in 0.31s
```

Every failure is the expected one (the two documents do not exist → `FileNotFoundError`; the pointer
and README tests fail on the missing sentence). The one skip is the brief's parametrised `--help` test,
whose parameters are read from the documents at collection time — no documents, no parameters. It is
not a skip in the GREEN run.

Along the way the path test caught two real mistakes of mine (a backticked `docs/migration.md` that is
a per-workflow path, and a pytest node id read as a path) — fixed in the text.

GREEN: see Verification.

## Verification

On the merged tree (integration head `5e3d8a9` + this task):

```
$ .venv/Scripts/python.exe -m pytest tests/test_handoff_production.py
30 passed in 3.17s
$ .venv/Scripts/python.exe -m pytest tests/test_handoff_production.py tests/test_committed_workflows.py tests/test_documented_identifier_form.py
80 passed in 4.02s          (the hand-off scans, and C4V's scan that no tracked file shows the old IDENTIFIER form)
$ .venv/Scripts/python.exe -m pytest -rs
1839 passed in 410.02s (0:06:50)          (0 skipped, 0 failed, no warnings)
```

The last run preceded one doc-only edit (the backlog's CI item now quotes that very line instead of a
placeholder I had written first); the 80 doc/scan tests above were re-run after it. The parametrised
`--help` test covers 12 scripts: deploy, answer_samples, build_samples, set_models, gen_source_views,
inject_outputs, intake_prompt, survey_corpus, validate_dbt, validate_segment, validate_snowpark,
validate_workflow. The option check covers 33 (script, option) pairs; the orchestrate check covers
`--check-models --from-stage --no-interactive --only --profile --root --runner --stop-after --tier`.
No TypeScript changed, so the node suite and tsc were not re-run.

## Commits

- `f54e7d3` docs: production hand-off guide for an agent, and the backlog only the company's setting
  can close — ONE commit on top of the integration head `5e3d8a9`; `git diff 5e3d8a9 f54e7d3` touches
  exactly the six files above. It squashes (with `git reset --soft 5e3d8a9`) the task's history:
  `bf04eb2` wip RED tests, `9c7fffe` wip docs, `964dcd4` merge of the integration head (C4V landed),
  `b49c4ef` wip follow-ups, plus the final backlog number. `bf04eb2` is kept in this report as the RED
  evidence object.

## Files changed

- new: `docs/handoff-production.md`, `docs/production-backlog.md`, `tests/test_handoff_production.py`
- modified: `docs/handoff-copilot-models.md`, `README.md`, `docs/reference/snowflake-backend.md` (see
  "Edits beyond pointers" item 6)

## Self-review

- Between my first write of the guide and my next edit, its prose on disk was restyled into shorter
  sentences and lists (apparently by a formatting hook in this environment; the session reported it as
  a deliberate on-disk change). I re-read all of it afterwards and re-checked every technical statement
  against the code; the commands, paths and facts were unchanged.
- The mechanics of the guide's snippets were run in a scratch root: the rung-1 normalise-and-diff
  (volatile-only differences vanish, `diff exit 0`; a changed verdict still shows, exit 1), the verdict
  and status tables over the committed `workflows/`, and the `set_models.py` workaround through the real
  `loadConfig`/`configuredModels`.
- Commands a human runs against Snowflake, Alteryx or the Copilot catalog were NOT run (no network, no
  authentication); their flags are pinned against each script's `--help` / `cli.ts`.

## Concerns

- Findings 1–3 are code defects outside P4's files, documented with workarounds; they want a
  controller ruling (Finding 1 especially: the documented `set_models.py` command silently violates the
  owner's model policy without the workaround).
- Item 6 of "Edits beyond pointers" touches P2's doc; revertable on its own.
- `README.md` §7's short form still says `--profile local`; the guide is the production procedure.
- Rung 1's pass rule assumes G has refreshed the committed `workflows/` (G is in progress); until then
  the diff will not be empty.

## Fix round 1

Requirements: `task-P4-fix1.md` (B1–B4). Worktree `.worktrees/p2-P4`, on top of `f54e7d3`.

### B1 — `set_models.py --default <id>` routes every role to `<id>`

- `orchestrator/cli.ts`: `DEFAULT_CONFIG.profiles.hosted` no longer has a `roleModels` map. That
  follows the owner's policy of one default model for every role. `loadConfig` builds each profile with
  `profileOver(base, file)`:
  - Scalars fall back to the default one at a time.
  - `roleModels`, `roleContextTiers` and `roleReasoningEffort` come from the file whole or not at all.
    A map the file leaves out is deleted, never inherited from the default. A map the file has replaces
    the default's, never merged key by key.
  - A file that does not define the profile keeps the default.
- `--check-models` now prints `effective <role>: <id>` for each of the eight roles before the catalog
  check. The id is computed the same way `runner.ts` picks a session's model.
- The committed `orchestrator.config.json` stays consistent. It defines all three maps, so its loaded
  value does not change, and the existing "real committed config resolves the owner's policy" test
  still passes. I ran `set_models.py --dry-run` with the flags that describe the committed split
  (`--default gpt-5.6-luna`, five `--role …=gpt-6-astra`, the five long-context roles,
  `documenter=low`, `reviewer=low`). It lists NO change to `orchestrator.config.json`. It would change
  only the program-spec placeholders in two other files:
  - `config.json`: `modelPolicy` removed from analyzer, translator, fixer, parser-recovery and
    general-purpose; cookbook-curator and rubber-duck move from `gpt-6-astra` to the default; research
    `contextTier` moves from `long_context` to `default`.
  - `.github/agents/cookbook-curator.agent.md` gains a `model:` line.

  These are exactly what the hand-off's one command is meant to set. I did not apply them.
- Tests:
  - `orchestrator/test/cli.test.ts` gains three tests:
    - "DEFAULT_CONFIG's hosted profile routes every role to its one model: no roleModels"
    - "a file's hosted role maps are whole maps…"
    - "--check-models lists the effective model of every role"
  - The four existing `--check-models` tests now build their per-role split explicitly (`SPLIT`)
    instead of relying on the default's.
  - `orchestrator/test/integration.test.ts` runs the real `set_models.py --default X` on copies of the
    committed `orchestrator.config.json`, `config.json` and `.github/agents/`. It checks three things:
    - `loadConfig` resolves all eight roles to X.
    - All nine agent files, cookbook-curator included, carry X.
    - `main --check-models`, given a catalog holding ONLY X, exits 0 and prints `effective <role>: X`
      for every role.

### B2 — `--import-set` records the golden set

- `scripts/inject_outputs.py` has a new `record_golden_set(repo, wf, set)`. It appends the set to
  `manifest.golden_sets` once, keeping order.
  - `--import-set` calls it only after `import_captures` has written every CSV.
  - A failed import records nothing.
  - If the CSVs are written but the manifest cannot be updated, the CLI exits 2 and says the set was
    imported but NOT recorded.
- The golden stage's printed instructions now say three things: the import records the set, import
  `normal` first, and resume with `--from-stage golden --only <wf>`. They used to say "then re-run",
  but a plain re-run never retries a parked stage.
- Tests:
  - `tests/test_inject_outputs.py`:
    - "an import records its set in manifest golden_sets once and in order": importing normal gives
      [normal]; then edge gives [normal, edge]; importing normal again leaves it unchanged.
    - "a failed import records no golden set".
    - "an import whose set cannot be recorded exits 2 and says so". I added this with the new exit-2
      path, so it was not written RED-first.
  - `orchestrator/test/integration.test.ts` uses the FAKE env with `golden.producer: "alteryx"`:
    1. The golden stage parks at BLOCKED.
    2. A capture map and one real `.yxdb` capture are written.
    3. The REAL `inject_outputs.py --import-set normal` runs.
    4. `--from-stage golden` reaches DONE with `golden_sets == ["normal"]`.
- The before and after state of append, merge and PreSQL database targets is NOT fixed. The backlog
  item "Golden capture for database targets" is rewritten to cover that one gap.

### B3 — GitHub is opt-in

- Config and flag:
  - `orchestrator/types.ts` gains `OrchestratorConfig.github?: { enabled?: boolean }`, `RunOptions.gh`
    and `Env.ghEnabled`.
  - `DEFAULT_CONFIG.github` is `{ enabled: false }`, and the committed `orchestrator.config.json`
    states `"github": { "enabled": false }`.
  - `--gh` turns it on for one run.
- `githubAccess(config, opts, probe)` is exported from `cli.ts`, and `main` uses it:
  - Off, it returns `{enabled: false, hasGh: false}` and never calls the probe (`gh --version`).
  - On, the probe decides `hasGh`.
  - The banner prints `gh=disabled|yes|no`.
- `stages.ts` has `ghUnavailable(env)`, which returns `gh: disabled` when GitHub is off and
  `gh not installed` when it is on but missing.
  - Intake logs `waiting for answers — gh: disabled, answer the boxes in …/open_questions.md`.
  - The pr stage logs `gh: disabled; skipping PR`.
  - Neither calls `env.sh("gh", …)`. With GitHub on, behaviour is unchanged.
- Tests:
  - `cli.test.ts` gains two tests:
    - "--gh parses, and GitHub integration is off unless the config or the flag turns it on", which
      also checks the committed config.
    - "with GitHub off, gh is never invoked -- not even to see whether it is installed", which checks
      the probe is called 0 times.
  - `stages.test.ts` gains two tests:
    - "with GitHub integration off, an installed gh is never called and the stages log gh: disabled":
      at WAITING_FOR_ANSWERS and on a full run to document, there are zero `sh` calls.
    - "the fake env, like the orchestrator, has GitHub integration off by default".
  - The two existing gh tests now set `ghEnabled: true`, so they still test the unchanged behaviour
    with GitHub on.
- Docs:
  - README §3: the `gh` paragraph.
  - README §5: issues are opened only with `--gh`.
  - README §7: the run touches no GitHub remote.
  - README §9 item 10: "GitHub is opt-in".
  - README §6 and the sample table are untouched; they belong to Task G.

### B4 — every script's `--help` and errors work in a cp1252 console

- One mechanism covers every script. `scripts/lib/console.py`'s `utf8_console()` switches
  `sys.stdout` and `sys.stderr` to UTF-8 with `errors="replace"`.
  - Every script's `__main__` block calls it before `main()`: 26 scripts, 22 under `scripts/` and 4
    under `scripts/dev/`.
  - Nothing calls it on import, so tests that run `main()` in-process keep their captured streams.
  - UTF-8 is also what the orchestrator uses to decode a child's output.
- Tests, in `tests/test_script_console.py`:
  - Every module is either a script or one of the two known libraries (`scripts/dev/__init__.py`,
    `scripts/dev/formula.py`).
  - `--help` under `PYTHONIOENCODING=cp1252` exits 0 and prints `usage:` (26 parametrised cases).
  - Every `__main__` block calls `utf8_console()` (26 cases).
  - An error naming a non-ASCII path reaches stderr intact: `survey_corpus.py <tmp>/no_such_→` exits
    2 and prints the path in UTF-8, not as `→`.
- The guide names `parse.py` again:
  - §3.3's Verify runs `.venv/Scripts/python.exe scripts/parse.py wf_1001 --check --root "$RUN"`
    before any model is called.
  - The backlog's sources-and-sinks step is now exact commands (`parse.py`, `intake_touchpoints.py`).
  - The `--help` check in `tests/test_handoff_production.py` now covers 14 scripts, with parse.py and
    intake_touchpoints.py added.

### Workarounds removed from the docs

- Guide §1.3:
  - The five `--role` flags, the paragraph explaining them and the `roleModels` Verify are gone.
  - In their place: the plain command, a sentence on whole maps, and a Verify that
    `grep -c '"roleModels"'` prints 0.
  - §1.4 gains a Verify for the `effective <role>` lines.
  - §5's "known defect" paragraph is removed.
- Guide §3.5:
  - The hand-written `golden_sets` snippet is gone. One paragraph now says the import records the set
    and that `normal` goes first.
  - The only gap left is the database-target one.
- Guide §0.3:
  - "Keep run roots outside any git work tree, with GH_REPO unset" was a requirement. Now GitHub is
    off by default and keeping run roots outside git is just a good habit.
  - Verify checks `"enabled": false` and `gh=disabled`.
  - Never-do: never pass `--gh` or set `github.enabled` without the human's approval.
- `docs/handoff-copilot-models.md`:
  - §2: the five `--role` flags and their paragraph are replaced by the plain command plus the
    whole-maps sentence.
  - §4 item 4 mentions the `effective <role>` lines.

### TDD evidence

RED: the tests alone, committed as `7e68103` before any implementation.

```
$ .venv/Scripts/python.exe -m pytest tests/test_script_console.py tests/test_inject_outputs.py
FAILED tests/test_script_console.py::test_help_works_in_a_cp1252_console[scripts/load_golden.py]
FAILED tests/test_script_console.py::test_help_works_in_a_cp1252_console[scripts/parse.py]
FAILED tests/test_script_console.py::test_an_error_naming_a_non_ascii_path_reaches_stderr_intact
FAILED tests/test_inject_outputs.py::test_an_import_records_its_set_in_manifest_golden_sets_once_and_in_order
FAILED tests/test_script_console.py::test_every_script_enters_through_the_one_console_helper[...]   (x26)
30 failed, 47 passed
$ node --experimental-strip-types --test orchestrator/test/cli.test.ts orchestrator/test/stages.test.ts orchestrator/test/integration.test.ts
# SyntaxError: The requested module '../cli.ts' does not provide an export named 'githubAccess'
not ok 1 - orchestrator\test\cli.test.ts            (the whole file: githubAccess does not exist yet)
not ok - set_models.py --default X on a copy of the committed config: ...   expected 'luna-max-under-test', actual 'gpt-6-astra'
not ok - importing real Alteryx captures records the golden set, ...       expected 'DONE', actual 'BLOCKED'
not ok - with GitHub integration off, an installed gh is never called and the stages log gh: disabled
not ok - the fake env, like the orchestrator, has GitHub integration off by default
# tests 128  # pass 123  # fail 5
```

The two integration failures reproduce B1 and B2 exactly. The guard test "a failed import records no
golden set" passed at RED, as a guard should.

### Verification (fix round 1)

These results are on the tree committed as `093aff4`:

```
$ node --experimental-strip-types --test orchestrator/test/*.test.ts
# tests 320  # pass 320  # fail 0  # skipped 0      (the real-Python integration tests ran; none skipped)
$ node node_modules/typescript/bin/tsc --noEmit -p .
(clean, exit 0)
$ .venv/Scripts/python.exe -m pytest -rs
1898 passed in 415.32s (0:06:55)                    (0 skipped, 0 failed, no warnings)
$ .venv/Scripts/python.exe -m pytest tests/test_handoff_production.py tests/test_committed_workflows.py tests/test_documented_identifier_form.py
82 passed                                           (re-run after the backlog's CI item took the new suite line)
```

Before this round the suite was 1839. This round adds 59 tests:
- 54 in `tests/test_script_console.py`;
- 3 in `tests/test_inject_outputs.py`;
- 2 from the handoff test's `--help` parametrisation, which now also covers `parse.py` and
  `intake_touchpoints.py`.

### Files changed (fix round 1)

`orchestrator/{cli,stages,types}.ts`, `orchestrator/test/{cli,stages,integration}.test.ts`,
`orchestrator/test/fakes.ts`, `orchestrator.config.json`, `scripts/lib/console.py` (new), the
`__main__` block of all 26 scripts, `scripts/inject_outputs.py`, `tests/test_inject_outputs.py`,
`tests/test_script_console.py` (new), `README.md` (§3, §5, §7, §9), `docs/handoff-production.md`,
`docs/production-backlog.md`, `docs/handoff-copilot-models.md`.

None of Task G's files were touched: `workflows/**`, `tests/test_committed_workflows.py`,
`tests/test_documented_identifier_form.py`, `tests/test_deploy.py`, README §6 and the sample table.

### Commit (fix round 1)

`093aff4` fix: set_models routes every role; imported captures record their golden set; GitHub is
opt-in; --help works in cp1252. This is one commit on top of `f54e7d3`. It squashes, with
`git reset --soft f54e7d3`, three wip commits:
- `7e68103`, RED tests only (the RED evidence object);
- `fc49cb5`, GREEN code;
- `8aa7af5`, docs.

It also includes the refreshed suite line.

### Concerns (fix round 1)

- Every script now writes UTF-8 to stdout and stderr when run as a program. A consumer that decodes
  a script's piped output as cp1252 would see mojibake for non-ASCII characters instead of a crash. The
  orchestrator already decodes UTF-8, and the full suite (which includes subprocess tests using
  `text=True`) passes unchanged.
- `set_models.py`'s dry run against the committed files would still change `config.json` and
  `cookbook-curator.agent.md`, but not `orchestrator.config.json`. Those two files hold the program
  spec's placeholders, and replacing them is the purpose of the one hand-off command. I did not apply
  the change.
- `docs/reference/real-workflows.md` §5 (Task P3's doc) still says only that the workflow "continues
  once a person has actually run it". The golden stage's own message now names
  `--from-stage golden`, and the guide's §3.6 uses it. I left the P3 doc unchanged.

## Fix round 2

The rulings came from the controller's summary of the opus review. Worktree `.worktrees/p2-P4`, on top
of `093aff4`.

### I1 — an explicit execution order

- The opening no longer says "follow it literally, top to bottom". It now says to follow the guide
  literally in the **execution order**, not in reading order.
- A new `## Execution order` section sits between "First week" and §0. It lists the steps in order:
  1. §0
  2. Rung 1
  3. §1.1–1.4
  4. Rung 2, with §1.5
  5. §2.1–2.5
  6. Rung 3, with §2.6
  7. §3.1–3.3
  8. Rung 4, through §3.4–3.7
  9. Rung 5, with §3.3–3.6 for each workflow, then §3.7

  The backlog runs alongside.
- Each step that belongs to a rung now opens with a marker:
  - §1.5: "**Run this only at rung 2**".
  - §2.6: "**Run this only at rung 3**" (and at rung 4 for the real workflow).
  - §3.4, §3.5, §3.6 and §3.7: "**Run this only at rung 4**" (and at rung 5 for each workflow of the
    batch).
- Tests in `tests/test_handoff_production.py`:
  - "the guide opens with an explicit execution order naming every part and rung" checks three
    things. "top to bottom" is gone. The section comes before `## 0.`. The twelve markers
    (§0 … Rung 5) appear in order.
  - "the steps that belong to a rung say when to run them".

### Minors

- **M1.** New test "every option named in prose is one a script or orchestrate accepts".
  - It collects every `--option`, in any case, from inline code outside fenced blocks.
  - Each option must appear in some script's `--help` or be a `case` in `orchestrator/cli.ts`.
  - A short allow-list covers other tools' options: node's `--experimental-strip-types` and `--test`;
    `tsc --noEmit`; git's `--porcelain`, `--git-dir` and `--stat`; `gh pr create --fill`; fnm's
    `--using`; `--version`.
  - I checked it by mutation: a prose `` `deploy.py --exceute` `` is caught.
- **M2.** §2.4's Verify is now `pip show dbt-snowflake` alone. It adds a warning not to probe the
  package by running `validate_dbt.py --backend snowflake`, because that clears the workflow's reports
  first.
- **M3.** Rung 5 now says that every workflow goes through §3.3–3.6 as the pilot did, and when to run
  §3.6's per-workflow `--from-stage golden`. Its Pass rule treats two parks as to-do items rather than
  failures, because both carry no reason by design:
  - intake at `WAITING_FOR_ANSWERS`;
  - golden at `BLOCKED` with the alteryx producer.
- **M4.** §0.3's bullet now reads: a hand-written config without a hosted profile routes every role to
  the placeholder `gpt-5.6-luna`, not to the ids §1.3 writes.
- **M5.** Rung 3a says phase 2 replaced the earlier form, an expression written inside `IDENTIFIER`,
  which is not in the documented grammar. `compile_check.py` refuses it as `c4:identifier_expression`
  and the SQL judge denies it. The old form is not written anywhere; C4V's scan passes.
- **M6.** Rung 3a says `--execute` also creates `WF0001_MASTER`, and that no rung CALLs a master. §5
  lists "a CALL of any master procedure" as never proven.
- **M7.** Rung 3a says the `--execute` runs through the validation connection on purpose: the sandbox
  is the validation role's database, and the deploying role has no grant there.
- **M8.**
  - §1.5: tightening `WRITE_TOOL` / `SQL_TOOL` from observed evidence is the one sanctioned
    `policy.ts` change, and the human reviews the diff before any re-run. Never-do says the same.
  - §2.3: the agent adds a sandbox database only after the human has confirmed the name in writing.
    Never-do: never add an unconfirmed database.
- **M9.** One rule, stated in §0.1: a step that needs a login or a credential is the human's. That
  covers the Copilot sign-in (so `list_models.ts`, `--check-models` and every
  `--runner copilot --profile hosted` run), a Snowflake connection, and an Alteryx licence. Everything
  offline is the agent's. To match it:
  - §3.4, §3.6, rung 2 and rung 5 now say **Who: human** for their orchestrate commands.
  - Never-do restates the rule.
  - `docs/handoff-copilot-models.md` §5 step 5 now says "The human runs …".
- **M10.**
  - The Part 3 intro now says "one gap".
  - `docs/reference/real-workflows.md` §5 now says four things. The stage is BLOCKED while
    `manifest.json` records no golden set. Each `--import-set` records its set. A parked stage is not
    retried by a plain re-run. Resume with `--from-stage golden --only <wf>`.
- **M11.** "the four suites" → "the three suites".
- **M12.** §3.5's Verify names `"$RUN/workflows/wf_1001/golden/intermediates/<seg>/normal/"`, one
  `<stream>.csv` per boundary stream.
- **M13.** §2.6 now explains the source views and how deploy.py's CALL finds them:
  - `gen_source_views.py` runs nothing. It prints the DDL for `<target_database>.MIG_SRC_<WF>`, where
    `<target_database>` is the run root's `mappings/global.yaml` `program.target_database`.
  - The human runs that DDL.
  - Deploy with that same database as `--database`: `deploy.py`'s example `CALL` passes `--database`
    as both the source and the target database, so `--src-schema MIG_SRC_<WF>` names the views.
    Checked in `scripts/deploy.py` `procedure_script`.
- **M14.** `scripts/dev/segment_scale.py` is committed.
  - It builds a linear chain of Formula tools whose node shapes are copied from the parsed `wf_0002`
    sample and written into the script, so it depends on no committed tree.
  - `--tools N` is repeatable. Output: `tools=N segments=S seconds=T`. Exit 2 when `--tools` is missing
    or below 3.
  - Its `__main__` calls `utf8_console()`.
  - Tests: `tests/test_segment_scale.py` (4 tests).
  - The backlog now quotes the command and its output from 2026-09-23:
    `tools=300 segments=16 seconds=0.8`, `tools=1000 segments=64 seconds=12.7`,
    `tools=3000 segments=256 seconds=142.7`. The segment counts are the same as the scratch run's; the
    times are its own.
- **M15.** `orchestrator/cli.ts`'s `runProcess` now decodes each pipe with `setEncoding("utf8")`, a
  stream decoder, instead of `String(chunk)`. Node test in `cli.test.ts`: "a multi-byte character split
  across two pipe chunks reaches the orchestrator intact". A child writes `→`'s first byte to stdout
  and stderr, waits 200 ms, then writes the other two bytes, and the test runs it through
  `makeEnv(...).sh`.

### Controller facts added

- §1.5 now gives the SDK's real tool names as the third live test observed them against the local BYOK
  model (`docs/live-smoke-test.md`, "Third live test"):
  - `view` {path, view_range}
  - `create` {path, file_text}: the write tool, which `WRITE_TOOL` already recognises
  - `powershell` {command, description}
  - `grep` {pattern, paths, output_mode, -n/-A/-C}
  - `glob` {pattern, paths}
  - `ask_user` {question, choices}

  Rung 2 confirms these names on the hosted models rather than discovering them. §5's hosted item now
  reads "the tool names on the hosted models".
- The opening no longer says "Neither got past intake". It says the live tests drove the SDK against a
  local model and no hosted model has run. I did not describe the third test's outcome.
- §0.2 recommends a short run-root path (`C:\mig\runs\<name>`). In the third live test the model
  mangled a path of about 150 characters and the policy correctly denied those reads.

### TDD evidence

RED, from the tests alone, committed as `49b2791`:

```
$ node --experimental-strip-types --test orchestrator/test/cli.test.ts
not ok 28 - a multi-byte character split across two pipe chunks reaches the orchestrator intact
  expected: '→'   actual: '���'
# tests 28  # pass 27  # fail 1
$ .venv/Scripts/python.exe -m pytest tests/test_handoff_production.py tests/test_segment_scale.py
FAILED tests/test_handoff_production.py::test_the_guide_opens_with_an_explicit_execution_order_naming_every_part_and_rung
FAILED tests/test_handoff_production.py::test_the_steps_that_belong_to_a_rung_say_when_to_run_them
FAILED tests/test_segment_scale.py::test_one_line_per_size_in_the_documented_shape
FAILED tests/test_segment_scale.py::test_the_chain_is_deterministic
FAILED tests/test_segment_scale.py::test_a_chain_needs_at_least_three_tools
```

Three tests passed at RED:
- The M1 prose-option pin passed at RED because the docs already satisfied it. It is a guard, and the
  mutation check above shows it discriminates.
- "no size is a usage error" passed at RED because a missing script also exits 2.
- My first draft of the M1 test failed at RED on `--noEmit`. `_OPTION` is lower-case only, so it read
  `--no`. I made the new test's option regex case-aware. That was a test fix, not a doc change.

### Verification (fix round 2)

These results are on the tree committed as the round's commit:

```
$ node --experimental-strip-types --test orchestrator/test/*.test.ts
# tests 321  # pass 321  # fail 0  # skipped 0
$ node node_modules/typescript/bin/tsc --noEmit -p .
(clean, exit 0)
$ .venv/Scripts/python.exe -m pytest tests/test_handoff_production.py tests/test_segment_scale.py tests/test_script_console.py tests/test_committed_workflows.py tests/test_documented_identifier_form.py
146 passed
```
$ .venv/Scripts/python.exe -m pytest -rs
1908 passed in 419.28s (0:06:59)                    (0 skipped, 0 failed, no warnings)
```

After the full run, only the backlog's CI line changed: it now quotes that line. The 86 doc and scan tests were re-run after that edit and passed.

### Commit (fix round 2)

`7dd21fc` docs: explicit execution order; prose flags pinned; review minors; stream decoding. It is one commit on top of `093aff4` (9 files, +382/−69). It squashes, with `git reset --soft 093aff4`, the round's wip commits: `49b2791` (RED tests only, the RED evidence object) and the GREEN wip.
