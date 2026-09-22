# Task 5 report: `scripts/segment.py`

## What I implemented

`scripts/segment.py` with the four interfaces the brief specifies:

- `segment(dag, *, min_tools=15, max_tools=40, formula_heavy_cap=20) -> dict` — the deterministic
  7-step algorithm. Returns `{"segments", "order", "warnings", "params"}`.
- `build_segment_dag(dag, seg, members, owner) -> dict` — contract C7 shape
  (`workflow, segment, nodes, edges, inbound, outbound`).
- `run(repo, wf_id, **overrides) -> dict` — reads `parsed/dag.json` and `manifest.segmentation`,
  writes `segments/seg_NN/dag.json`, `segments/order.json`, `segments/segmentation.json`, sets
  `manifest.segments`, cleans up stale `seg_NN` dirs.
- CLI: `python scripts/segment.py <wf_id> [--min-tools N] [--max-tools N] [--root .]`.

### Algorithm implementation notes

- **Data nodes / size**: nodes whose type isn't in `NON_DATA_TYPES`. Browse is a data node (gets a
  group) but is excluded from every size count (`size_of`), and its inbound edge is forced into
  union with its upstream node — unless that edge is itself a hard cut (browse hanging directly off
  a macro), in which case it stays its own segment rather than merging into an unmergeable macro.
- **Hard cuts** (edge touches a `macro` node) are absolute everywhere: never unioned in step 2,
  stop the ordering walk (with a warning) in step 3, never eligible as a merge channel in step 5.
  They are *not* excluded from the group-level dependency graph used for the acyclic check (step 5)
  or the topological ordering (step 7) — a macro's segment still has real upstream/downstream data
  dependencies that must be respected even though it can never merge.
- **Soft cuts**: edges whose endpoints' outermost Tool Container differ (`None` is its own value,
  computed by walking `container_id` to the root). Everything else unions in step 2.
- **Ordering protection** (step 3): from each `ORDER_DEPENDENT_TYPES` node and each `summarize`
  using `First`/`Last`, walks upstream one predecessor at a time as long as the current node has
  exactly one inbound edge, unioning and protecting each edge crossed, stopping at (and including) a
  `sort`, at a source (0 inbound edges), or at a fan-in node (>1 inbound edges, e.g. a `join`/`union`
  that happens to sit in the chain — included in the union, not traversed past). A macro predecessor
  stops the walk with `"ordering dependency of tool <id> crosses macro <id>"` and does not union.
- **Join protection** (step 4): every edge whose destination is a `join` node is marked protected
  (never a step-6 split point). Unions nothing, as specified.
- **Merge** (step 5): repeatedly finds the lowest-tool-id group below `min_tools`, gathers neighbour
  groups reachable via a non-hard cross edge, filters to `merged size <= max_tools` and "stays
  acyclic" (a DFS cycle check on the group-level quotient graph after the hypothetical merge, built
  from *all* inter-group edges, hard or soft), and merges with the smallest eligible neighbour
  (lowest tool id in that neighbour breaks ties — this is a true tiebreaker only in the sense of the
  spec's wording, since two distinct groups can never share a lowest tool id, so the comparison is
  actually always decisive and order-independent). Repeats to a fixpoint.
- **Split** (step 6): for each group needing a split (`size > max_tools`, or `size > formula_heavy_cap`
  with more than half its (non-browse) members `formula`/`multi_row_formula`), finds every internal
  edge that is a real graph bridge (removing it disconnects the group's induced subgraph — checked
  directly by BFS/DFS per candidate, not Tarjan's algorithm, since these graphs are tiny) and is not
  in the protected set, and picks the one that minimizes `|size(half A) - size(half B)|`, tie-broken
  by the edge's own `(src, dst)`. If none exists, keeps the group and warns. Repeats to a fixpoint
  (a split half can itself still be oversized and gets split again on the next pass).
- **Numbering** (step 7): Kahn's algorithm over the final groups' quotient graph, batched into
  topological levels (`order`), each level's groups sorted by lowest tool id before numbering
  `seg_01, seg_02, …` in level order.

### A deliberate scope decision (not in the brief's literal text)

The 7 steps run **once each, in sequence** — merge to a fixpoint, then split to a fixpoint, with no
second merge pass afterward. This means a split can leave a fragment permanently below `min_tools`
if nothing eligible remains to merge it with (this actually happens in
`test_ordering_chain_is_never_split`: tool `10` ends up alone once `9`'s ordering-protected chain
pulls `9`—and transitively `10`, already unioned with it via the container—away from the `9→10`
edge, the only remaining unprotected bridge in that group). I considered adding a cleanup merge pass
after splitting, but rejected it: it isn't in the brief, and doing it safely requires *also*
re-checking the formula-heavy-cap condition on the hypothetical re-merge (otherwise it could silently
undo a formula-heavy split), which is exactly the kind of scope creep the project rules ask me to
avoid. None of the six brief tests depend on this either way (verified by hand-trace and by the
passing test). I flagged it in the test file's docstring rather than hiding it.

### Brief corrections

None. I initially misread wf_0003's README narrative ("the lone tool 10 … merges across the soft
cut") as describing the exact mechanism for the brief's actual test parameters
(`min_tools=2, max_tools=4`), but that README describes a *different* invocation
(`sample.json`'s `min_tools=3, max_tools=40`, where no split ever happens). The brief's six tests
never exercise that specific parameter combination for tool 10's final home, so there's nothing to
correct — just a note in case a future task hand-traces the same README passage against the wrong
test.

## What I tested

`tests/test_segment.py`: the brief's six tests verbatim, plus seven more for behaviour the brief
describes in prose but the six tests never exercise:

- `test_undersized_containers_merge_across_soft_cuts` — wf_0002 at `min_tools=3` (its own README's
  worked example) collapses to one segment. This is the **only** coverage of step 5's actual merge
  path succeeding; none of the six brief tests ever produces a successful merge (I checked each by
  hand: every undersized group in them either already meets the floor or has no eligible neighbour).
- `test_join_inputs_are_never_a_split_point` — a synthetic join where its two input edges tie for
  the best possible split balance with a valid, unprotected edge elsewhere; proves step 4 actually
  excludes candidates rather than just never being exercised (I verified by hand that without the
  protection check, the tie-break would pick the join input edge first, splitting it away).
- `test_unsplittable_oversized_group_is_kept_and_warned` — an all-ordering-protected chain that
  can't legally be split; must stay intact with a warning instead of violating the cap or crashing.
- `test_run_writes_segment_files_and_manifest` — C7 shape, `order.json`, `segmentation.json`,
  `manifest.segments`.
- `test_run_is_idempotent_byte_identical` — reruns produce identical bytes for both
  `segmentation.json` and a segment's `dag.json`.
- `test_run_reads_manifest_segmentation_and_cli_overrides_win` — manifest values apply, explicit
  `run()` kwargs override them.
- `test_run_removes_empty_stale_dirs_but_warns_for_dirs_with_extra_files` — a stale `seg_NN` holding
  only `dag.json` is removed; one holding an extra file is left alone and warned about.

I also ran a manual end-to-end CLI smoke test outside pytest (`parse.py` then `segment.py --root
<tmp>` on `wf_0002`), confirmed the 2-wave/3-segment output matches the hand-traced expectation, and
confirmed a second CLI run reproduces byte-identical `segmentation.json` and `seg_01/dag.json`
(via `sha256sum -c`). Scratch files were under `/tmp` and are cleaned up; nothing was written to the
repo's own `workflows/`.

### TDD evidence

- RED: `tests/test_segment.py` written first, importing `segment` (which didn't exist yet). Ran
  `.venv/Scripts/python.exe -m pytest tests/test_segment.py -q` before creating `scripts/segment.py`
  — collection failed with `ModuleNotFoundError: No module named 'segment'`, the expected failure
  since the module under test didn't exist.
- GREEN: after implementing `scripts/segment.py`:
  ```
  .venv/Scripts/python.exe -m pytest tests/test_segment.py -v
  ============================= test session starts =============================
  collected 13 items
  tests\test_segment.py .............                                      [100%]
  ============================= 13 passed in 0.11s ==============================
  ```
- Full suite, run once before committing:
  ```
  .venv/Scripts/python.exe -m pytest
  179 passed in 0.49s
  ```
  Output pristine both times — no warnings, no skips.

## Files changed

- `scripts/segment.py` (new, 409 lines)
- `tests/test_segment.py` (new, 13 tests)

## Self-review findings

- Initial draft of the merge step built a `dict[str, str]` of "neighbour root -> touching tool id"
  that was computed but never read (I use each neighbour group's stored lowest tool id from the
  groups snapshot instead). Simplified to a plain `set[str]` of neighbour roots before finishing.
- Confirmed the `acyclic_after_merge` cycle check uses the *full* group-level dependency graph
  (including hard-cut edges), not just mergeable edges — an earlier draft excluded hard edges here,
  which would have let the acyclic check pass on a merge that actually creates a cycle in the real
  segment execution order (hard cuts still represent real data dependencies for `order.json`, they
  just can never be merged across).
- Verified the tie-break keys used in both `merge_pass` (`(size, lowest tool id)`) and `find_split`
  (`(balance diff, src, dst)`) are always unique per candidate, so `set`/dict iteration order (which
  Python doesn't guarantee across processes for `str` keys) can't introduce nondeterminism into the
  result — the comparison always has a strict, well-defined minimum.
- Read `test_containers_cut_and_waves_are_topological`, `wf_0002/README.md` and the raw
  `customer_orders.yxmd` XML directly to confirm the 9→10/9→11 edges (browse's actual upstream)
  before trusting my own derivation of "browse rides with its upstream."

## Concerns

- `run()`'s exit-code mapping was flagged by review and fixed in round 1 (see below); resolved.
- `acyclic_after_merge`'s cycle check is a plain recursive DFS; fine at this task's scale (tests
  top out at 30 tools) but would want an iterative rewrite before this ever runs against a
  300-tool production workflow, to avoid Python's recursion limit.

## Status

DONE

## Fix round 1

**Where:** dedicated worktree `.worktrees/task-5-fix` (branch `wt/task-5-fix`), per the
coordinator's instruction — the main checkout was in use by another agent. Commit `9c3c231` on top
of the worktree's existing history (which already contained `0de3e96`).

### Finding addressed (Important)

`scripts/segment.py:391-404` — `main()` had no exception handling. A workflow that hadn't been
parsed yet raised an uncaught `FileNotFoundError` from `read_json(.../parsed/dag.json)` inside
`run()`, producing a raw traceback and Python's default exit code 1 — colliding with the "1 =
domain failure" contract and leaving exit 2 ("usage or unexpected error") unreachable for the most
common failure mode. Separately, the old exit-code logic (`1 if result["warnings"] else 0`) treated
*any* warning as a failure, which the reviewer's ruling says is wrong: a warning is advisory (the
analyzer reviews it), not a failed segmentation.

### What changed

`scripts/segment.py`:
- Added an "Exit codes" paragraph to the module docstring (mirroring `parse.py`'s), stating: 0 for
  any segmentation produced (warnings included), 1 only when no valid segmentation could be produced
  at all (the group graph has a cycle and step 7 can't order it), 2 for usage errors (bad arguments,
  or a workflow with no `parsed/dag.json` yet).
- `main()` now wraps the `run(...)` call: `FileNotFoundError` → `parser.error(str(exc))` (argparse's
  own exit-2 path, printed as a usage error, nothing written); any other `Exception` →
  `traceback.print_exc()` then `return 2`. This mirrors `scripts/parse.py:410-416` exactly (I
  re-read `parse.py` first, as instructed, and confirmed it had a `traceback.print_exc()` /
  `return 2` branch I hadn't accounted for — my original `except Exception: return 2` draft here
  omitted the traceback print, which I then added to match).
- The success-path return changed from `1 if result["warnings"] else 0` to
  `1 if any("cycle" in w for w in result["warnings"]) else 0` — warnings alone no longer force a
  non-zero exit; only the specific "group graph has a cycle" warning (the one case where step 7
  falls back because no valid topological order exists) does.
- Added `import traceback`.

`tests/test_segment.py`: six new tests, five matching the reviewer's list verbatim in spirit plus
one I added to actually exercise the exit-1 path (the ruling names it but none of the five listed
tests reach it):
- `test_main_on_never_parsed_workflow_exits_2_and_writes_nothing` — `pytest.raises(SystemExit)`,
  code `2`, and no `workflows/wf_absent/` directory is created.
- `test_main_with_no_arguments_exits_2` — argparse's own required-argument handling, unaffected by
  this fix but worth pinning.
- `test_main_on_parsed_sample_exits_0_and_writes_artifacts` — a normal run with default params
  writes `seg_01/dag.json`, `order.json`, `segmentation.json` and returns `0`.
- `test_main_exits_2_on_unexpected_exception` — `monkeypatch.setattr(segment, "run", boom)` where
  `boom` raises `RuntimeError`; asserts a plain `== 2` return (not `SystemExit`), matching
  `parse.py`'s equivalent test (`test_a_crash_outside_the_parse_exits_2`).
- `test_main_exits_0_when_only_warnings_are_produced` — reuses the all-ordering-protected chain from
  `test_unsplittable_oversized_group_is_kept_and_warned` (a real, non-cycle warning); asserts the
  warning is present in `segmentation.json` *and* exit code is `0`.
- `test_main_exits_1_when_the_group_graph_has_a_cycle` (added beyond the reviewer's list) — a new
  fixture `CYCLIC_GROUP_DAG`: two Tool Containers whose internal wiring (`1->3`, `2->4`) keeps each
  a single group, but `1->2` and `4->3` cross between them in opposite directions, producing a
  2-cycle at the *group* level even though the tool-level graph stays acyclic (`3` and `4` are sinks,
  so there's no real path back to `1` or `2` — `invariants.py`'s own acyclicity check would pass this
  dag). This is the only currently-reachable case that trips step 7's cycle fallback, so it's the
  only way to actually verify the new exit-1 branch rather than trust it by inspection.

### TDD evidence

RED — reverted just the `main()`/docstring edit via `git stash push -- scripts/segment.py` (keeping
the new tests in place) and ran the new tests against the pre-fix code:

```
$ "/c/Users/<user>/Desktop/Alteryx to Snowflake/.venv/Scripts/python.exe" -m pytest tests/test_segment.py -k "test_main_" -v
...
FAILED tests/test_segment.py::test_main_on_never_parsed_workflow_exits_2_and_writes_nothing
FAILED tests/test_segment.py::test_main_exits_2_on_unexpected_exception - RuntimeError: the disk went away
FAILED tests/test_segment.py::test_main_exits_0_when_only_warnings_are_produced
    assert (["segment ['1', '2', '3', '4', '5', '6', '7'] stays above its size cap (7 > 3): no splittable bridge remains"] and 1 == 0)
================= 3 failed, 3 passed, 13 deselected in 0.26s ==================
```

The three failures are exactly the three behaviours the fix changes (uncaught `FileNotFoundError`,
uncaught `RuntimeError`, and warnings forcing exit 1). The three that already passed against the old
code are unaffected by this fix (argparse's own no-args handling; the clean-sample case, which
happens to produce zero warnings either way; and the cycle case, which the *old* buggy
"any warning → 1" logic also happened to return 1 for, coincidentally).

GREEN — restored the fix (`git stash pop`), fixed a docstring typo caught on re-read ("find it a
topological order" → "find a topological order for it"), reran:

```
$ "/c/Users/<user>/Desktop/Alteryx to Snowflake/.venv/Scripts/python.exe" -m pytest tests/test_segment.py -v
collected 19 items
tests\test_segment.py ...................                                [100%]
============================= 19 passed in 0.16s ==============================
```

Full suite, run once before committing:

```
$ "/c/Users/<user>/Desktop/Alteryx to Snowflake/.venv/Scripts/python.exe" -m pytest
267 passed in 2.39s
```

Output pristine both times (no warnings, no skips). Also re-ran after committing, same result.

### Manual smoke test

```
$ "/c/Users/<user>/Desktop/Alteryx to Snowflake/.venv/Scripts/python.exe" scripts/segment.py wf_never_parsed --root /tmp/seg_fix_smoke
usage: segment.py [-h] [--min-tools MIN_TOOLS] [--max-tools MAX_TOOLS] [--root ROOT] wf_id
segment.py: error: [Errno 2] No such file or directory: '...\seg_fix_smoke\workflows\wf_never_parsed\parsed\dag.json'
$ echo EXIT:$?
EXIT:2
```

Confirms the CLI now reports a clean usage error instead of a raw traceback. Scratch directory was
under `/tmp` and has been removed.

### Files changed (this round)

- `scripts/segment.py` (+20/-2 lines: docstring exit-codes paragraph, `import traceback`, `main()`
  exception handling and revised exit-1 condition)
- `tests/test_segment.py` (+85 lines: six new tests, one new fixture)

### Status

DONE
