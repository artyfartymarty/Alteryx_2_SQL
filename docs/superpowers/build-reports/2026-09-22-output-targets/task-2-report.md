# Task 2 report: `scripts/target_check.py`, `TARGET_CLASS`, preference plumbing

## What I implemented

1. **`scripts/parsers/plugin_map.py`** — appended `TARGET_CLASS` and `node_class()` at the very
   end of the file (nothing else touched, so the merge with ot-task-1's `PLUGIN_TYPES`/`ANCHORS`
   edits stays clean), verbatim per the brief.

2. **`scripts/target_check.py`** (new) — `target_check()`, `dbt_blockers()`, `_resolve_preference()`
   and the `main(argv=None) -> int` CLI, per the brief plus coordinator ruling (a):
   - `--prefer` choices are now `auto | procedures | dbt`, default `auto`.
   - `_resolve_preference(repo, wf_id, prefer)`: when `prefer != "auto"` it's returned unchanged
     (explicit `--prefer procedures|dbt` overrides). When `prefer == "auto"`: `manifest.json`'s own
     `output_target` wins if the file exists and the key is truthy; else `mappings/global.yaml`'s
     `program.output_target` if the file exists and the key is truthy; else `"procedures"`.
   - `target_check()` calls `_resolve_preference` first and reassigns `prefer` to the resolved
     value, so `result["preference"]` (written to `targets.json`) always holds the *resolved*
     value, never the literal `"auto"`.

3. **`scripts/dev/build_samples.py`** — in `seed()`, right after
   `manifest["segmentation"] = sample.get("segmentation") or {}`, added:
   ```python
   if sample.get("output_target"):
       manifest["output_target"] = sample["output_target"]
   ```

4. **`mappings/global.yaml`** — added two keys under `program:` (after `column_name_policy`):
   `output_target: procedures` and `snowpark_runtime: "3.11"`, both with the brief's exact comments.

5. **Tests**: `tests/test_target_check.py` (new, 11 tests) and one new test in
   `tests/test_build_samples.py`.

## The real config key names (brief's placeholder check)

The brief said the `pre_sql`/`post_sql` names in its code/fixtures might be placeholders. I read
`scripts/parsers/tool_config.py::_output()` and found its keys already ARE `pre_sql` and
`post_sql` literally (lines 192-193: `"pre_sql": scrub(_find(config, ".//PreSQL") or "")[0] or
None`, `"post_sql": ...`). **No change was needed** — the brief's code and fixtures were already
correct against the real parser.

For write-mode vocabulary (coordinator ruling (b)): `scripts/intake_prompt.py` defines
`_VALID_MODES = {"overwrite", "append", "merge"}` (line 39), which is exactly the brief's
`DBT_MODES = {"overwrite", "append", "merge"}`. The `intake/mappings.yaml` `outputs` entries use
key `"mode"` (not `"write_mode"`) — confirmed against `workflows/wf_0001/intake/mappings.yaml` on
disk and against `intake_prompt.py` line ~639 (`entry["mode"] = a.get("write_mode")`), which also
matches the brief's `dbt_blockers()` (`entry.get("mode")`) and the `OUT_OVERWRITE` fixture
(`"mode": "overwrite"`). **No change was needed here either** — both of the brief's literal-code
concerns turned out to already match the real parser/intake vocabulary.

## `tests/test_foundations.py` / global.yaml key pinning

`test_global_yaml_matches_program_spec_plus_task_additions` only asserts specific keys
(`program.raw_schema`, `tolerances.rounding`, `sources == {}`, `outputs == {}`) — it does not pin
an exhaustive key list for `program`, so adding `output_target`/`snowpark_runtime` needed no
change there. I grepped the whole `tests/` tree for any exhaustive `program` key-set assertion
(`.keys()`, literal dict equality against `obj["program"]`) and found none; the only tests that
construct their own minimal `program: {a: 1}` YAML text are `tests/test_io_global_mappings.py`'s
own synthetic fixtures, unaffected by the real file's content.

`tests/helpers.py::copy_pristine_mappings_and_catalog` copies the real `mappings/` tree and blanks
only `sources`/`outputs`, so `program.output_target`/`program.snowpark_runtime` now flow into
every fixture that uses it, as the brief anticipated.

## Coordinator ruling (a): `--prefer auto` — tests added

Beyond the brief's 6 tests, I added 5 more to `tests/test_target_check.py` for the ruling:

- `test_prefer_auto_resolves_manifest_over_global` — manifest.json has `output_target: dbt`,
  global.yaml's `program.output_target: procedures`; asserts manifest wins (`preference == "dbt"`).
- `test_prefer_auto_resolves_global_when_manifest_has_no_output_target` — manifest.json exists but
  has no `output_target` key; global.yaml has `output_target: dbt`; asserts global wins.
- `test_prefer_auto_defaults_to_procedures_when_absent_everywhere` — no manifest.json, no
  mappings/global.yaml under the tmp_path root at all; asserts `"procedures"`.
- `test_cli_prefer_defaults_to_auto` — CLI invoked with no `--prefer` flag; manifest.json carries
  `output_target: dbt`; asserts the CLI's default is genuinely `auto` and resolves it end to end
  (`targets.json["preference"] == "dbt"`).
- `test_cli_explicit_prefer_overrides_auto_resolution` — same manifest (`dbt`), but CLI called
  with `--prefer procedures`; asserts the explicit flag wins over the manifest.

All fixtures reuse `PLAIN` + `OUT_OVERWRITE` (zero `dbt_blockers`), so whichever way `auto`
resolves, `output_kind` tracks the resolved preference directly and the test is only exercising
preference-resolution, not blocker logic (already covered by the brief's own tests).

## `tests/test_build_samples.py` — new seed test

`test_seed_copies_output_target_into_manifest_only_when_the_sample_carries_it`: uses the existing
`_copy_sample` helper to make a private mutable copy of `wf_0001`'s sample fixture, adds
`"output_target": "dbt"` to its `sample.json`, seeds it, and asserts
`manifest["output_target"] == "dbt"`; then seeds the real (unmodified) `wf_0001` sample into a
second repo and asserts the key is absent from that manifest.

## TDD evidence

RED (before `scripts/target_check.py` existed):
```
$ .venv/Scripts/python.exe -m pytest tests/test_target_check.py
ImportError while importing test module '...\tests\test_target_check.py'.
tests\test_target_check.py:7: in <module>
    import target_check as tc
E   ModuleNotFoundError: No module named 'target_check'
1 error in 0.15s
```

GREEN (after implementation):
```
$ .venv/Scripts/python.exe -m pytest tests/test_target_check.py -v
collected 11 items
tests\test_target_check.py ...........                                   [100%]
11 passed in 2.23s
```

Focused run (target_check + build_samples + foundations + every intake test, per the brief's
"run the new tests + ..." instruction):
```
$ .venv/Scripts/python.exe -m pytest tests/test_target_check.py tests/test_build_samples.py \
    tests/test_foundations.py tests/test_intake_cli.py tests/test_intake_fix_round_1.py \
    tests/test_intake_fix_round_2.py tests/test_intake_fix_round_3.py \
    tests/test_intake_fix_round_4.py tests/test_intake_prompt.py \
    tests/test_intake_self_reference.py tests/test_intake_touchpoints.py -v
collected 249 items
249 passed in 12.46s
```

## Full suite

Before any of my changes (baseline, from this worktree at e3052fe):
```
$ .venv/Scripts/python.exe -m pytest
1 failed, 1004 passed in 66.07s
FAILED tests/test_backend.py::test_snowflake_backend_fails_clearly_without_connector
```
This failure is pre-existing and unrelated to Task 2: `snowflake-connector-python` is actually
installed in this venv, so `get_backend("snowflake")` reaches real connector code (which then
fails on this machine's absent Snowflake connection config) instead of hitting the
"not-installed" `BackendError` the test expects. I did not touch `scripts/lib/backend.py` or
`tests/test_backend.py`; this is an environment property of the venv, not something Task 2
introduced or is in scope to fix.

After my changes:
```
$ .venv/Scripts/python.exe -m pytest
1 failed, 1016 passed in 60.32s
FAILED tests/test_backend.py::test_snowflake_backend_fails_clearly_without_connector
```
1016 - 1004 = 12 new tests (11 in `test_target_check.py` + 1 in `test_build_samples.py`), all
passing. The same single pre-existing, unrelated failure is the only one present both before and
after. No new warnings appeared in either run (`grep -i warning` on the full `-m pytest` output
returns nothing).

## Files changed

- Modified: `scripts/parsers/plugin_map.py` (append-only, `TARGET_CLASS` + `node_class`)
- Modified: `scripts/dev/build_samples.py` (2 lines, `seed()`)
- Modified: `mappings/global.yaml` (2 lines, `program:` section)
- Modified: `tests/test_build_samples.py` (1 new test)
- Created: `scripts/target_check.py`
- Created: `tests/test_target_check.py` (11 tests: the brief's 6 + 5 for ruling (a))

## Deviations from the brief

- `--prefer` gains a third choice `"auto"` (default), and `target_check()` resolves it via a new
  private helper `_resolve_preference()` before doing anything else — required by coordinator
  ruling (a), not in the brief's original code block.
- No other deviations. The two placeholder concerns the brief flagged (output-node PreSQL/PostSQL
  key names, write-mode vocabulary) both turned out to already match the real parser/intake code
  verbatim, so the brief's literal code and fixtures needed no edits for those.

## Self-review concerns

- `_resolve_preference` reads `manifest.json` via plain `read_json` inside `target_check()`,
  without validating that `output_target` (if present) is one of `"procedures"`/`"dbt"`. An
  invalid value (e.g. a typo) would flow straight into `result["preference"]` and then into the
  `if prefer == "dbt" ... else: "procedures"` branch, silently taking the `else` (procedures)
  path rather than erroring. This matches the brief's existing looseness (the CLI's own
  `--prefer` argparse `choices` already guards the explicit-flag path; `auto`-resolved values from
  data files were never validated in the brief either), so I left it as is rather than inventing
  new validation the brief didn't ask for. Flagging it here in case the coordinator wants a
  ValueError on an unrecognized resolved value.
- `manifest.json` or `mappings/global.yaml` failing to parse (malformed JSON/YAML) during
  `--prefer auto` resolution will raise inside `target_check()`, caught by the CLI's
  `except (FileNotFoundError, KeyError, ValueError)` → exit 2. This seemed like reasonable,
  consistent behavior (a broken manifest/global.yaml is a usage-level problem) but isn't
  explicitly tested since it wasn't asked for.
- Worktree is clean after this report is written (only the git-tracked files listed above are
  modified/added; nothing under `workflows/` or `samples/` was touched by any test, all of which
  use `tmp_path`).

## Fix round 1 (coordinator ruling, binding)

**Finding (Important):** an invalid resolved `output_target` (e.g. `"DBT"`, `"dbt "`, a typo)
silently fell through to `"procedures"` in `_resolve_preference`, so `targets.json` could record
`preference: "DBT"` next to `reason: "preference procedures"` — an internally inconsistent
record (spec §1: the decision is "always recorded with its reason"). This is exactly the concern
I flagged under "Self-review concerns" in the original report above. Reviewer reproduced it with
`manifest.json = {"output_target": "DBT"}`.

**Ruling:** a resolved value outside `{"procedures", "dbt"}` is a USAGE error —
`_resolve_preference` raises `ValueError` naming the value and its source (`manifest.json
output_target` or `mappings/global.yaml program.output_target`); `main()`'s existing
`except (FileNotFoundError, KeyError, ValueError)` already routes it to exit 2 with the message on
stderr, and nothing is written (raising happens before `target_check()` reaches `write_json`).
Explicit `--prefer` values are unaffected — argparse's own `choices=["auto", "procedures", "dbt"]`
already constrains them.

### What changed

- `scripts/target_check.py`:
  - Added `PREFERENCES = {"procedures", "dbt"}` (line 27, next to the other module-level
    constants).
  - `_resolve_preference` (was lines 66-81, now ~67-96): after reading a truthy
    `manifest.json["output_target"]`, it's checked against `PREFERENCES`; if not a member, raises
    `ValueError(f"manifest.json output_target {manifest_target!r} is not one of {sorted(PREFERENCES)}")`
    before returning it. Same check, same message shape, for
    `mappings/global.yaml`'s `program.output_target` (`ValueError(f"mappings/global.yaml
    program.output_target {global_target!r} is not one of {sorted(PREFERENCES)}")`). Docstring
    updated to describe both branches.
  - `main()` and `target_check()`'s own branch logic (~line 99 onward, previously flagged as
    "~93-99" in the finding) needed no change: the invalid value now never reaches that branch at
    all, since `_resolve_preference` raises before `target_check` does anything else.

- `tests/test_target_check.py`: added `import pytest` and three new tests (now 14 total, up from
  11):
  - `test_prefer_auto_manifest_invalid_value_raises_naming_manifest_and_value` — manifest.json
    `output_target: "DBT"`; asserts `ValueError` matching `"manifest.json"`, message contains
    `"DBT"`, and `targets.json` is not written.
  - `test_prefer_auto_global_invalid_value_raises_naming_global_and_value` — global.yaml
    `program.output_target: "Dbt"`; asserts `ValueError` matching `"global.yaml"`, message
    contains `"Dbt"`, and `targets.json` is not written.
  - `test_cli_prefer_auto_invalid_manifest_value_exits_2_and_writes_nothing` — CLI (`main()`, no
    explicit `--prefer`) against a manifest with `output_target: "DBT"`; asserts exit code 2,
    `"DBT"` on stderr, and `targets.json` not written.

### RED (before the fix, reproducing the reviewer's finding)

```
$ .venv/Scripts/python.exe -m pytest tests/test_target_check.py -k "invalid" -v
...
FAILED tests/test_target_check.py::test_prefer_auto_manifest_invalid_value_raises_naming_manifest_and_value
  >       with pytest.raises(ValueError, match="manifest.json") as exc:
  E       Failed: DID NOT RAISE ValueError
FAILED tests/test_target_check.py::test_prefer_auto_global_invalid_value_raises_naming_global_and_value
  >       with pytest.raises(ValueError, match="global.yaml") as exc:
  E       Failed: DID NOT RAISE ValueError
FAILED tests/test_target_check.py::test_cli_prefer_auto_invalid_manifest_value_exits_2_and_writes_nothing
  >       assert rc == 2
  E       assert 0 == 2
  ---------------------------- Captured stdout call -----------------------------
  wf_0009: output_kind=procedures segments={'seg_01': 'sql'}
3 failed, 11 deselected in 0.14s
```
(The captured stdout on the third failure is the exact bug: the CLI happily proceeded and printed
`output_kind=procedures` for a manifest whose `output_target` was `"DBT"`.)

### GREEN (after the fix)

```
$ .venv/Scripts/python.exe -m pytest tests/test_target_check.py -v
collected 14 items
tests\test_target_check.py ..............                                [100%]
14 passed in 0.19s
```

### Tests run per the ruling's instruction

```
$ .venv/Scripts/python.exe -m pytest tests/test_target_check.py tests/test_build_samples.py -v
collected 69 items
69 passed in 3.09s
```

### Full suite after the fix

```
$ .venv/Scripts/python.exe -m pytest
1 failed, 1019 passed in 59.24s
FAILED tests/test_backend.py::test_snowflake_backend_fails_clearly_without_connector
```
1019 = 1016 (post-task-2, pre-fix) + 3 new tests, all passing. Same single pre-existing, unrelated
failure as before (see "Full suite" section above) — nothing else regressed. No new warnings.

Commit: `4513bab` — "fix: target_check.py rejects an invalid --prefer auto resolved value instead
of silently defaulting to procedures". Worktree clean after commit.
