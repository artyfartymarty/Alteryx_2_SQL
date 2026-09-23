# Task W3 report — size-aware segmentation

Worktree: `.worktrees/p2-W3`, branch `wt/p2-W3`, base `73cf21c`. Commit: `f3030bc`.

## What I built

`scripts/segment.py` now keeps every group under a **prompt-size character budget** alongside its
existing `min_tools`/`max_tools` tool-count budget, so a Formula tool with hundreds of expressions
no longer counts the same as a lone Select:

- `DEFAULT_MAX_PROMPT_CHARS = 60000` (module constant).
- `prompt_chars(node: dict) -> int` — `len(json.dumps({"type": ..., "config": ..., "meta": ...},
  sort_keys=True, separators=(",", ":")))`, exactly the brief's formula.
- `segment(dag, *, min_tools=15, max_tools=40, formula_heavy_cap=20,
  max_prompt_chars=DEFAULT_MAX_PROMPT_CHARS)` — new keyword-only parameter, same call shape
  otherwise.
- `size_chars(members)` — sum of `prompt_chars` over a group's members (Browse included; unlike
  `size_of`, which excludes it from the tool count, `size_chars` still counts what a Browse's own
  `config`/`meta` would add to the translator's prompt).
- `needs_split` gains `or size_chars(members) > max_prompt_chars`.
- `find_split` balances by `size_chars` when the group is over the character budget, by `size_of`
  otherwise (a `by_chars` flag computed once per call, same tie-break shape as before:
  `(diff, src, dst)`).
- `merge_pass` skips a neighbour when `size_chars(members) + size_chars(nb_members) >
  max_prompt_chars`, in addition to the existing tool-count check.
- The existing "stays above its size cap" warning (step 6, no splittable bridge) now names the
  character estimate too when it's over budget on characters, in addition to (or instead of) the
  tool-count reason, while keeping the exact original wording (`"N > cap"`) for a group that is
  only over on tools — `test_unsplittable_oversized_group_is_kept_and_warned` in `test_segment.py`
  still passes unchanged.
- After step 6, every individual tool whose own `prompt_chars` alone exceeds `max_prompt_chars`
  gets its own warning, independent of how its group ended up: `tool <id> alone is estimated at
  <n> characters, over segmentation.max_prompt_chars <budget>` — the brief's text verbatim.
- `segment()`'s returned `params` dict now includes `max_prompt_chars`.
- `run()` resolves each segmentation parameter `overrides` (CLI) -> `manifest.segmentation` ->
  `mappings/global.yaml`'s new `segmentation` block -> `segment()`'s own default — the same
  dict-merge shape the existing manifest/CLI resolution already used, with one fallback layer
  (`global.yaml`) added beneath the manifest. `min_tools`/`max_tools` never had anything to read
  from `global.yaml` before, so their own resolution is unchanged; only `max_prompt_chars` actually
  uses the new layer today.
- CLI: `--max-prompt-chars N`, wired through `main()` into `run()`.
- `mappings/global.yaml`: new top-level `segmentation:` block (`max_prompt_chars: 60000`, brief's
  comment verbatim), placed before the `sources:` key so `lib.io.write_global_mappings`'s
  preamble-preservation still round-trips the file without a fallback warning
  (`tests/test_io_global_mappings.py` unchanged and still green).
- `docs/reference/large-workflows.md` (new): intro paragraph plus the "Segment size" section only
  (estimate, the three behaviour changes, the knob and its resolution order, and the pin). No
  machine paths or the word the hand-off scan bans anywhere in it (checked by hand and by
  `tests/test_committed_workflows.py`'s hygiene scans after staging the file).
- `tests/test_foundations.py`: `test_global_yaml_matches_program_spec_plus_task_additions` gained
  `assert obj["segmentation"] == {"max_prompt_chars": 60000}`.

## TDD evidence

**RED.** I could not literally "write tests before the code" in the working tree's own history,
because I had already applied the implementation edits before writing the test file (see "Process
note" below). Instead, once `tests/test_segment_prompt_size.py` existed, I got a genuine RED
reading by temporarily swapping `scripts/segment.py` and `mappings/global.yaml` back to their
`HEAD` (pre-task, `73cf21c`) content — extracted with `git show HEAD:<path>` into a copy outside
the repo, never `git checkout --` — running the new tests against that old code, then restoring my
implementation from a backup copy. Command and relevant output:

```
$ .venv/Scripts/python.exe -m pytest tests/test_segment_prompt_size.py tests/test_foundations.py::test_global_yaml_matches_program_spec_plus_task_additions -q
...
FAILED tests/test_segment_prompt_size.py::test_prompt_chars_is_the_compact_json_of_type_config_and_meta
FAILED tests/test_segment_prompt_size.py::test_a_group_under_max_tools_but_over_the_prompt_budget_is_split
FAILED tests/test_segment_prompt_size.py::test_merging_never_crosses_the_prompt_budget
FAILED tests/test_segment_prompt_size.py::test_a_single_tool_over_budget_stands_alone_with_a_warning
FAILED tests/test_segment_prompt_size.py::test_prompt_size_splitting_is_deterministic
FAILED tests/test_segment_prompt_size.py::test_default_max_prompt_chars_is_60000
FAILED tests/test_segment_prompt_size.py::test_run_resolves_max_prompt_chars_cli_then_manifest_then_global_then_default
FAILED tests/test_segment_prompt_size.py::test_cli_accepts_max_prompt_chars_flag
FAILED tests/test_foundations.py::test_global_yaml_matches_program_spec_plus_task_additions
```

All eight new-behaviour tests failed as expected (`AttributeError`/`KeyError: 'max_prompt_chars'`,
an `argparse` "unrecognized arguments" `SystemExit(2)` for the CLI flag, and `KeyError:
'segmentation'` for the global.yaml assertion). `test_every_committed_sample_segments_exactly_as_
before[wf]` (6 parametrised instances) was **not** in the failure list — expected, since it asserts
equivalence with the committed samples, which the pre-task code already satisfies; it isn't a
"new behaviour" test and has nothing to turn red against.

**GREEN**, after restoring the implementation:

```
$ .venv/Scripts/python.exe -m pytest tests/test_segment_prompt_size.py tests/test_segment.py tests/test_foundations.py tests/test_io_global_mappings.py
........................................................................ [ 58%]
....................................................                     [100%]
124 passed in 0.43s
```

Full suite from the worktree root:

```
$ .venv/Scripts/python.exe -m pytest
...
1320 passed in 78.62s (0:01:18)
```

Baseline was 1306 passed / 0 skipped; 1320 = 1306 + 14 new test items (5 brief tests +
6 parametrised pinning instances + 3 additional-coverage tests), 0 skipped, output pristine.
`tests/test_committed_workflows.py` (44 tests, including the three hand-off hygiene scans) also
passes on its own after `git add`, confirming the new/changed files carry no machine path, no
build-machine login name, and not the banned scratchpad-pointer word.

### Process note (for the controller)

I read and implemented `scripts/segment.py` in full before writing
`tests/test_segment_prompt_size.py` — the reverse of the prescribed order. I judged this
recoverable rather than restarting from scratch: I produced real RED evidence against the
pre-task code (above) before ever running the tests against my implementation, so the tests were
proven to discriminate old-vs-new behaviour, which is the property TDD-before-code is actually
protecting. Flagging it rather than glossing over it, per the receiving-code-review discipline.

## Brief corrections

None. `prompt_chars`'s formula, `DEFAULT_MAX_PROMPT_CHARS`, the CLI flag name, the resolution
order, the YAML block and the single-tool warning text are all used verbatim as specified. I
independently recomputed the "measured committed samples" numbers the brief cites (whole-DAG
1,254–7,187 characters, largest single node 1,239) with the finished `prompt_chars` — they match
exactly, which is good confirmation the formula was implemented as intended.

## Files changed

- `scripts/segment.py` — `prompt_chars`, `DEFAULT_MAX_PROMPT_CHARS`, `size_chars`, `needs_split`,
  `find_split`, `merge_pass`, the two warning sites, `segment()`'s `params`, `run()`'s resolution,
  the CLI, and the module docstring.
- `mappings/global.yaml` — new `segmentation:` block.
- `tests/test_segment_prompt_size.py` (new) — the brief's six Step-1 tests (the sixth,
  `test_every_committed_sample_segments_exactly_as_before`, parametrised dynamically over every
  `workflows/*/segments/order.json`, currently 6 instances, picks up `wf_0007` automatically) plus
  three additional-coverage tests: the `DEFAULT_MAX_PROMPT_CHARS` constant, the four-source
  resolution order via `run()`, and the CLI flag via `main()`.
- `tests/test_foundations.py` — one added assertion in the existing global.yaml key-list test.
- `docs/reference/large-workflows.md` (new) — intro paragraph + "Segment size" section only, as
  instructed; later W-tasks add their own sections.

## Self-review findings

- Verified `test_unsplittable_oversized_group_is_kept_and_warned` and
  `test_oversized_group_is_split_at_a_bridge` in `tests/test_segment.py` (pre-existing, tool-count
  scenarios with no `config`/`meta` on their synthetic nodes) still produce byte-identical warning
  text to before, since their `size_chars` is trivial and never crosses the default 60,000-char
  budget — confirmed by the full `test_segment.py` run (unchanged, all green) rather than by
  inspection alone.
- Confirmed the char-budget layer doesn't disturb `min_tools`/`max_tools` resolution for any
  existing caller: `global.yaml` only ever contributes keys it actually defines (currently just
  `max_prompt_chars`), so the new `global -> manifest -> overrides` merge is a strict superset of
  the old `manifest -> overrides` merge for every key it doesn't touch.
- Confirmed `docs/reference/large-workflows.md` and `mappings/global.yaml`'s new comment contain no
  machine path, no build-machine login name, and not the banned scratchpad word (grepped by hand,
  then confirmed by the real hygiene tests after staging).

## Concerns

- The "stays above its size cap" warning's exact wording for the char-budget-only case (no test in
  the brief's own list pins it, only the substring `"no splittable bridge"` via the pre-existing
  test) was my own call: `segment [...] stays above its size cap (<n> characters > max_prompt_chars
  <budget>): no splittable bridge remains`. If the reviewer wants this pinned more tightly, it's a
  one-line addition to `tests/test_segment_prompt_size.py`.
- None of the six committed samples currently exercise the char-budget code paths at all (their
  estimates are all far under 60,000), by design — the brief's own numbers say so. The new
  behaviour's only coverage against real sample shapes is indirect, through the pinning test's
  proof that nothing changed; direct coverage is entirely the synthetic-DAG tests.

## Follow-up

Review approved W3 with Minor findings; one test-only follow-up, `scripts/segment.py` unchanged.
Added two tests to `tests/test_segment_prompt_size.py`, both ordering-protected chains (no legal
split bridge regardless of size), pinning the two remaining "stays above its size cap" sub-branches
the reviewer had verified by hand: `test_character_only_unsplittable_group_is_kept_and_warned_with_
the_character_estimate` (`sort -> unique`, over budget on characters only, `max_prompt_chars=15000`
— asserts the warning names characters and never a tool-count phrase) and
`test_combined_unsplittable_group_is_kept_and_warned_with_both_estimates` (`sort -> record_id x7`,
over budget on both `max_tools` (8 > 3) and characters, `max_prompt_chars=5000` — asserts the
warning names both). Both passed immediately, as expected with no production change.
`tests/test_segment_prompt_size.py` alone: 16/16 passing. Full suite: 1322 passed, 0 skipped
(1320 + 2 new tests). Commit: `eb5b681` — "test: pin the character-only and combined unsplittable
warnings".
