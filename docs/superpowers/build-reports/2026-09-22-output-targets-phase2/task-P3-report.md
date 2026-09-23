# Task P3 report — real Alteryx corpus triage

**Status:** DONE
**Branch/worktree:** `wt/p2-P3` in `.worktrees/p2-P3`, based on `83bfd67`
**Commit:** `2180b59` — feat: survey_corpus.py triages a directory of real Alteryx workflows — classes, unknown plugins, tiers, output kinds, segment estimates

## What I implemented

- **`scripts/survey_corpus.py`** — `survey(directory, *, prefer="procedures") -> dict`,
  `render_markdown(report) -> str`, CLI `survey_corpus.py <dir> [--out report.md] [--json
  report.json] [--prefer procedures|dbt]`.
  - Discovers every `.yxmd`/`.yxwz`/`.yxzp` anywhere under `<dir>` (recursive, sorted by relative
    posix path — `.yxmc` macro files are never top-level rows, matching how a real workflow
    directory is laid out, e.g. the committed `wf_0004` sample's `Supporting_Macros/`).
  - Parses each with `parse.parse_file` directly — no `workflows/<id>` tree, no `--root`, nothing
    written to the corpus. A `.yxzp` is unzipped into a throwaway `tempfile.TemporaryDirectory()`
    (a fresh zip-slip guard, `_safe_extract`, written because `parse._unzip_packages` is tied to
    unzipping in place inside a workflow's own `source/`) and its workflow located with
    `parse.workflow_file`.
  - A parse failure of any kind becomes `{"path", "status": "error", "error"}` — never a crash of
    the whole survey. The `error` text is run through `_sanitize` (a `C:\...`-style regex scrub)
    before it reaches the report, because the one realistic leak path (a `.yxzp` with no workflow
    inside raises `FileNotFoundError` naming its own absolute temp-extraction directory) would
    otherwise put a real machine path in the output. Verified this actually fires (see "Manual
    verification" below).
  - For an "ok" row: `target_check.expand(dag["nodes"])` (recurses into every resolved macro's
    `sub_dag`, exactly as `target_check.target_check` itself does) is classified node-by-node with
    `target_check.classify` (which is `plugin_map.node_class`, except an *unresolved* macro is
    `unknown`). `classes` is the tally; `unknown_plugins` is every `type: unknown` node's `plugin`
    string; `unresolved_macros` is every `type: macro` node with `unresolved: True`, by
    `macro_path` (already `/`-normalized by the parser). Both lists are deduped and sorted.
  - `segment.segment(dag)` (its own defaults) gives the segment-count estimate. Its `segments`
    mapping (top-level tool-id membership) is re-expanded per segment
    (`target_check.expand([n for n in dag["nodes"] if n["tool_id"] in members])`) to build
    `seg_nodes`/`segment_targets` the same way `target_check.target_check` wires them from
    `segments/<seg>/dag.json` files — there are none here, so this reconstructs the equivalent
    in-memory. `dbt_blockers` — the real function, not reimplemented — is called with these plus a
    synthesized `outputs` dict built straight from each output node's own parsed `config`
    (`write_mode`/`keys`), with the raw Alteryx write-mode string defaulted through a small local
    copy of `intake_prompt._default_write_mode`'s mapping (`update_insert`→`merge`,
    `truncate_append`→`overwrite`) — kept as a local copy rather than an import of that module's
    private name, exactly the precedent `tests/helpers.py` already sets and cites for the same
    reason.
  - `output_kind` is `"dbt"` only when `prefer == "dbt"` and the (always-computed) blocker list is
    empty, otherwise `"procedures"` — the same rule `target_check.py` uses, applied uniformly
    across the corpus (there is no per-workflow manifest to resolve an `auto` preference from).
  - `tier`: `T3` if any node classifies `manual` **or `unknown`**, else `T2` if any `snowpark`,
    else `T1`. See "Design decision" below — this is a considered extension of the spec's
    three-class table, not a literal reading of it.
  - `plugins`: a corpus-wide `{plugin, type, count}` table (from every `ok` row's `expand()`
    nodes, so comments/containers/interfaces/browse — already excluded by `expand`'s
    `DATA_LESS_TYPES` filter — never appear in it), sorted by count descending then plugin name
    then type, for determinism.
- **`tests/test_survey_corpus.py`** — 14 tests (listed below).
- **`tests/corpus_fixtures/unknown_plugin.yxmd`** — a synthetic, hand-written, never-opened-by-
  Alteryx workflow (Input → `ExampleVendor.SentimentScore.SentimentScore` → Output) so
  `unknown_plugins` and the plugin table have exactly one thing to name and count. Marked
  synthetic in an XML comment (no directory-README convention exists for a single fixture file, so
  I followed the honesty rule inline instead of inventing an unrequested file).
- **`docs/reference/real-workflows.md`** — the how-to: §1 run `survey_corpus.py` first; §2 teach
  `plugin_map.py` the plugins it found (the "verify against your Alteryx version" marker, copied
  verbatim from the Python tool's own comment; a pointer to the `scripts/parsers/ext/` extension
  path for genuinely third-party plugins `plugin_map.py` should never claim to understand); §3
  bring the workflow in (copy source, parse, segment, intake — pointing at `README.md` §5's real
  transcript rather than repeating it); §4 golden capture — the one step needing a real Alteryx
  install, spelled out as `inject_outputs.py --capture-dir` → **a person** runs
  `AlteryxEngineCmd.exe` (never this pipeline) → `inject_outputs.py --capture-dir --import-set`;
  §5 orchestrator with `golden.producer: "alteryx"`, quoting the exact `BLOCKED` message
  `orchestrator/stages.ts` prints; §6 review, restating what a `VALIDATED` result does and does
  not prove. Opens and closes on the same sentence: **a real Alteryx engine run has never been
  exercised by this repository.**

## Brief corrections / controller notes followed

- **wf_0007 does not exist in this worktree** (Task C, wave 3, has not run against `wt/p2-P3`,
  which branches from wave-1's `83bfd67`). Per the controller's note, `SAMPLE_IDS` is computed
  dynamically from what `samples/*/source/` actually holds (six workflows), the "every row is
  `ok`" and count assertions run over that, and the `wf_0007`/`--prefer dbt` assertion is gated
  behind `if "wf_0007" in SAMPLE_IDS:` so it activates automatically once Task C lands, without
  this file needing another edit.
- **`wf_0005` needs the parser-recovery path only through `parse.run`'s full pipeline** (which
  loads the sample's own recorded extension under a scratch root during the offline run).
  `survey_corpus.py` calls `parse.parse_file` directly with only the parser's *shipped* extensions
  (`scripts/parsers/ext/`, which does not include the sample's `acme_dedupe` extension) — verified
  by hand that this parses `wf_0005` cleanly with node 2 (`AcmeAnalytics.Dedupe.DedupeTool`)
  classified `unknown` and node 3 (`RunCommand`) classified `manual`, i.e. `status: "ok"`,
  `unknown_plugins == ["AcmeAnalytics.Dedupe.DedupeTool"]`, `tier == "T3"` — exactly the brief's
  expected row, and the test pins it.
- **Paths are relative with `/`, no absolute path anywhere in the report** — pinned by
  `test_the_report_carries_no_absolute_path` (checks the corpus dir's own path never appears, plus
  a `C:\`/`C:/`/`/c/`-style regex over the whole JSON blob) and manually verified against the one
  realistic leak path (`.yxzp` with no workflow inside) — see "Manual verification" below. Also
  checked with the repo's own hand-off scans
  (`tests/test_committed_workflows.py::test_no_shipped_file_carries_a_real_machine_path` /
  `..._build_machine_login_name` / `..._points_at_a_scratchpad` etc.) against every file this task
  added — all pass.
- **No name change needed for the reused functions** — `target_check.expand`, `target_check.classify`
  and `target_check.dbt_blockers` all exist exactly as the controller's notes named them; nothing
  in the brief's assumed interface needed correcting against the real code.

## Design decision not fully pinned by the brief: tier and `unknown`

The design spec's vocabulary table (`docs/superpowers/specs/2026-09-22-output-targets-design.md`
§2) fixes tier only in terms of the three classes a *finished* parse plus intake can produce:
"T1 all `sql`, T2 any `snowpark`, T3 any `manual`". It says nothing about `unknown`, because by the
time `target_check.py` runs for real, nothing downstream tolerates an `unknown` node reaching a
tier decision un-escalated. A pre-intake corpus survey has no such downstream gate, so I extended
the rule: **`unknown` also forces T3** — a plugin the parser cannot name is at least as much a
blocker as a known-manual tool, and treating it as silently T1-compatible would misrepresent the
corpus to the company reading the report. This is documented in the function's own docstring
(`_tier` in `scripts/survey_corpus.py`) and in `docs/reference/real-workflows.md` §1. It does not
contradict any brief-specified test: the brief's own `wf_0005` tier-T3 example has *both* an
unknown node and a manual node, so it can't distinguish the two readings, and no other test
constrains this choice. Flagging it explicitly since it's a judgment call, not a spec citation.

## TDD evidence

**RED** — before `scripts/survey_corpus.py` existed:

```
$ .venv/Scripts/python.exe -m pytest tests/test_survey_corpus.py -x
...
tests\test_survey_corpus.py:16: in <module>
    import survey_corpus as sc
E   ModuleNotFoundError: No module named 'survey_corpus'
1 error in 0.10s
```

Expected: the module the brief specifies did not exist yet.

**GREEN** — after implementation:

```
$ .venv/Scripts/python.exe -m pytest tests/test_survey_corpus.py -v
...
collected 14 items
tests\test_survey_corpus.py ..............                               [100%]
============================= 14 passed in 0.22s ==============================
```

Test names: `test_the_committed_samples_survey_as_expected`,
`test_an_unknown_plugin_is_listed_by_name_and_counted`,
`test_an_unparsable_file_is_an_error_row_not_a_crash`,
`test_the_plugin_table_is_sorted_by_count_then_name`,
`test_the_report_carries_no_absolute_path`, `test_output_is_deterministic`,
`test_cli_writes_markdown_and_json`, `test_cli_exits_2_on_a_missing_directory`,
`test_cli_exits_2_with_no_arguments_at_all`, `test_a_crash_outside_the_survey_exits_2`,
`test_cli_exits_0_with_no_output_files_requested`,
`test_render_markdown_names_every_workflow_and_the_plugin_table`,
`test_a_yxzp_package_is_extracted_to_a_temp_dir_and_surveyed`,
`test_a_merge_mode_without_keys_is_a_dbt_blocker`. The first six plus `test_cli_writes_markdown_and_json`
are exactly the brief's named tests (parametrised over what samples exist, per the controller's
note); the rest are additional coverage for CLI exit codes (implementer-rules.md's per-exit-code
requirement), `.yxzp` handling, and the dbt write-mode/keys rule, all described in prose by the
brief/controller notes but not individually named as tests.

## Full suite

```
$ .venv/Scripts/python.exe -m pytest
1336 passed in 78.94s (0:01:18)
```

Baseline was 1322 passed / 0 skipped at `83bfd67`; 1336 − 1322 = 14, exactly this task's new tests.
Zero skips, zero failures.

## Manual verification (beyond the automated tests)

- Ran `survey_corpus.py` over a tmp copy of all six committed samples' `source/` trees end to end
  (CLI, `--out`/`--json`) and read the rendered Markdown: every row's tier/classes/segments/plugin
  list matched hand computation (`wf_0001`–`wf_0004` T1/all-sql, `wf_0005` T3 with the one unknown
  plugin and a `manual_node`+`unknown_node` blocker pair, `wf_0006` T2 with a `snowpark_segment`
  blocker) and the plugin frequency table was correctly sorted.
- Built a synthetic workflow with an unresolvable macro reference
  (`<EngineSettings Macro="Missing_Macros\does_not_exist.yxmc">`, no such file) and confirmed
  `unresolved_macros == ["Missing_Macros/does_not_exist.yxmc"]`, the node classifies `unknown`
  (not merely absent), and `tier == "T3"` — this path has no dedicated automated test (the brief
  doesn't ask for one by name), so I verified it by hand instead of leaving it unchecked.
  Considered adding a pinning test but held to the brief's named-test list plus the
  controller-described behaviors already covered; flagging this as untested-but-verified rather
  than silently assuming it works.
- Built a synthetic `.yxzp` with no workflow file inside it and confirmed the resulting `error`
  row's message is `"FileNotFoundError: no workflow file in <path>"` — the absolute temp directory
  is genuinely produced by the stdlib exception and genuinely scrubbed by `_sanitize` before
  reaching the report; this is the concrete case `test_the_report_carries_no_absolute_path` guards
  against, not a hypothetical one.

## Files changed

- `scripts/survey_corpus.py` (new)
- `tests/test_survey_corpus.py` (new)
- `tests/corpus_fixtures/unknown_plugin.yxmd` (new)
- `docs/reference/real-workflows.md` (new)

## Self-review notes

- Did not touch `README.md`, `scripts/parsers/plugin_map.py`, `scripts/target_check.py` or
  `scripts/segment.py` — read-only reuse of their public functions, per the controller's "reuse,
  never duplicate" instruction and the file-ownership table (this task owns only the four files
  above).
- `--root` is deliberately absent from this script's CLI, unlike every other script in the plan's
  Global Constraints table: the brief's own CLI signature
  (`survey_corpus.py <dir> [--out] [--json] [--prefer]`) has no `--root`, and the script's whole
  point is to run over a directory that is *not* a `workflows/` tree under any repo root — there
  is nothing for `--root` to mean here. Noting this as a deliberate, brief-directed deviation from
  the general convention, not an oversight.
- No concerns beyond the tier/`unknown` judgment call documented above.

## Concerns

None blocking. The one open item is the documented tier/`unknown` design decision above — worth a
quick nod from the controller/reviewer, but it doesn't contradict any brief-specified test or
behavior.
