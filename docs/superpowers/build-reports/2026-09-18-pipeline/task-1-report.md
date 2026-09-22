# Task 1: Foundations — report

## What I implemented

All files listed in the brief's "Files" section:

- `pyproject.toml`, `requirements.txt`, `.gitattributes`, `tsconfig.json` — copied verbatim from the brief's exact blocks.
- `scripts/lib/__init__.py` (empty package marker).
- `scripts/lib/paths.py` — `Repo` (`.root` resolved via `Path(root).resolve()`; `.global_mappings` set in `__init__`), `.wf()`, `.seg()`, `add_root_arg()`, `wf_token()`, `seg_token()`.
- `scripts/lib/vocab.py` — `STATUSES`, `VERDICTS`, `DIFF_CLASSES`, `GOLDEN_SETS`, `NON_DATA_TYPES`, `ORDER_DEPENDENT_TYPES`, `T3_TYPES`, values taken verbatim from 00-index.md's Global Constraints and plan §7/§8.
- `scripts/lib/io.py` — `read_json`/`write_json` (mkdir parents, `indent=2`, trailing `\n`, `newline="\n"` to force LF on Windows), `read_yaml`/`write_yaml` (`sort_keys=False`, `allow_unicode=True`, same LF handling), `load_manifest`/`save_manifest` (default shape `{"id", "status": {}, "metrics": {}}`; `save_manifest` stamps `updated_at` as UTC ISO-8601 ending in `"Z"` and writes to `repo.wf(manifest["id"], "manifest.json")`).
- `scripts/lib/typed_csv.py` (contract C1) — `read_table`/`write_table` (CSV + `<name>.schema.json` sidecar, `newline=""`, `lineterminator="\n"`, RFC 4180 quoting via the stdlib `csv` module's default `QUOTE_MINIMAL`), `parse_value`/`format_value` (Int*/Byte → `int`, Float/Double → `float`, FixedDecimal → `Decimal`, Bool → `"true"`/`"false"`, everything else → `str`; `\N` ↔ `None`).
- `.github/copilot-instructions.md` — the six bullets copied verbatim from `docs/spec/01-copilot-setup.md` §3's fenced block, plus the seventh line the brief specifies (`- Procedures follow docs/reference/dag-contract.md and plan contract C4: ...`).
- `mappings/global.yaml` — the template from `docs/spec/00-README.md` §6, comments kept; `sources:` example entries replaced with `sources: {}`; `outputs: {}` kept as-is; added `program.raw_schema: RAW` and `tolerances.rounding: {abs: 0.01}`.
- `tests/test_foundations.py` — the brief's five tests verbatim, plus 11 additional tests (see below).

## What I tested and results

Ran `.venv/Scripts/python.exe -m pytest` from the repo root (uses `pyproject.toml`'s `testpaths = ["tests"]`, `addopts = "-q"`).

Final run: **16 passed**, output pristine (no warnings, no stray output):

```
$ .venv/Scripts/python.exe -m pytest
................
16 passed in 0.08s
```

### TDD Evidence

**RED** — `tests/test_foundations.py` written first (the brief's five tests verbatim), against an empty `scripts/lib/` directory (no module files yet):

```
$ .venv/Scripts/python.exe -m pytest tests/test_foundations.py
ImportError while importing test module 'tests\test_foundations.py'.
Traceback:
tests\test_foundations.py:3: in <module>
    from lib import io, typed_csv, vocab
E   ImportError: cannot import name 'io' from 'lib' (unknown location)
```

Note on the exact error: the brief anticipates `ModuleNotFoundError: lib`. I got `ImportError: cannot import name 'io' from 'lib'` instead, because I had already created the empty `scripts/lib/` directory (as the destination for the five modules) before running the test — an empty directory on `sys.path` is picked up by Python as an implicit PEP 420 namespace package, so `lib` itself resolves (with no `__file__`, hence "unknown location") but has no `io` attribute. This is a cosmetic difference in the failure message, not a different root cause: the five modules genuinely did not exist yet, and the test failed for that reason. I did not treat this as a brief correction since no test content was wrong — it's a byproduct of directory-creation order that has no effect once the modules are implemented.

**GREEN** — after implementing the five modules, the original five tests pass:

```
$ .venv/Scripts/python.exe -m pytest tests/test_foundations.py -v
tests\test_foundations.py .....                                          [100%]
5 passed in 0.13s
```

### Additional tests (behaviour described in prose but not exercised by the brief's five tests)

Per the rules ("Add further tests for behaviour the brief describes in prose but does not test"), I added 11 more tests to `tests/test_foundations.py`, covering:

- `vocab.STATUSES`/`VERDICTS` match the Global Constraints list exactly (not just "contains one value each").
- `vocab.GOLDEN_SETS`, `NON_DATA_TYPES`, `ORDER_DEPENDENT_TYPES`, `T3_TYPES` (untested by the brief's `test_vocab_is_closed`).
- `Repo.global_mappings` resolves to `<root>/mappings/global.yaml`.
- `Repo(root)` resolves a non-normalized root (trailing `.` segment) to the same absolute path as a plain `tmp_path`.
- `add_root_arg` adds `--root` defaulting to `"."`, and accepts an override.
- `save_manifest` actually lands on disk at `repo.wf(id, "manifest.json")` with the expected content, not just that a re-read succeeds.
- `read_yaml`/`write_yaml` preserve insertion order (`sort_keys=False`) and write literal non-ASCII (`allow_unicode=True`), and use LF endings.
- `write_table` applies RFC 4180 quoting only where needed (plain field unquoted, comma-containing and quote-containing fields quoted/escaped) — the brief's round-trip test alone doesn't pin down the on-disk quoting shape.
- `write_table` uses LF line endings (no `\r`).
- `parse_value`/`format_value` exercised directly per Alteryx type family, including `Date`/`DateTime` passing through as plain strings.
- `mappings/global.yaml` on disk has the task's required additions (`program.raw_schema`, `tolerances.rounding`, empty `sources`/`outputs`) and parses via `io.read_yaml`.

All 16 tests pass together; final command and output shown above.

## Files changed

- `pyproject.toml` (new)
- `requirements.txt` (new)
- `.gitattributes` (new)
- `tsconfig.json` (new)
- `scripts/lib/__init__.py` (new)
- `scripts/lib/paths.py` (new)
- `scripts/lib/vocab.py` (new)
- `scripts/lib/io.py` (new)
- `scripts/lib/typed_csv.py` (new)
- `.github/copilot-instructions.md` (new)
- `mappings/global.yaml` (new)
- `tests/test_foundations.py` (new)

## Self-review findings

- Confirmed every interface in the brief's "Produces" block exists with the exact signature/name listed (`Repo.__init__`, `.wf`, `.seg`, `.global_mappings`, `add_root_arg`, `wf_token`, `seg_token`; `vocab`'s seven names; `io`'s six functions; `typed_csv`'s four functions).
- Confirmed `requirements.txt` constraints are already satisfied by the `.venv` (duckdb 1.5.5, sqlglot 30.18.0, PyYAML 6.0.3, pytest 9.1.1) — no installs performed.
- Confirmed `tsconfig.json` is valid JSON; did not run `tsc` (no TypeScript source exists yet, per the task context).
- Confirmed `.gitignore`/`node_modules` untouched; only staged the files this task owns (no `git add -A`).
- Verified the committed diff contains exactly the 12 files above (checked `git status --porcelain` before and after `git add`); `__pycache__` directories created by the test run were not staged (already covered by `.gitignore`).
- Re-read `mappings/global.yaml` against the program spec block side-by-side: comments preserved verbatim, only the `sources:` body changed (examples → `{}`), `outputs: {}` unchanged, and the two brief-specified additions present in the right sections (`program.raw_schema` alongside the other schema keys; `tolerances.rounding` nested under `tolerances`).
- Re-read `.github/copilot-instructions.md` line-by-line against `docs/spec/01-copilot-setup.md` §3's fenced block: all six original bullets copied verbatim, plus the exact seventh line the brief specifies, in the order given.

## Brief corrections

None. No test in the brief was found to be wrong; the one discrepancy (RED-step error message, see TDD Evidence above) is an artifact of my own directory-creation order, not an error in the brief.

## Issues or concerns

None. All 16 tests pass with pristine output; every deliverable in the brief's file list exists with the specified content/interface.

## Fix round 1

**Finding (Important):** `format_value` rendered `Float`/`Double` via bare `str(value)`, which
switches to scientific notation outside roughly `[1e-4, 1e16)` (e.g. `format_value(1e17, "Double")
-> "1e+17"`). Contract C1 requires "numbers in plain decimal notation." Values still round-tripped
through `float()` on read, but the on-disk golden CSV violated the stated wire format.

### What I changed

- `scripts/lib/typed_csv.py`:
  - Added a `_format_float(value: float) -> str` helper. It rejects non-finite values
    (`inf`, `-inf`, `nan`) with `ValueError(f"cannot write non-finite float in plain decimal
    notation: {value!r}")`, since they have no plain-decimal form. For finite values it routes
    through `format(Decimal(repr(value)), "f")` — `repr(value)` is Python's shortest decimal
    string that round-trips back to `value`, so parsing it into a `Decimal` and reformatting with
    the `'f'` (fixed-point) presentation type re-renders the *same digits* without an exponent:
    no scientific notation, no float noise padding, and the sign of `-0.0` is preserved (`Decimal`
    keeps a signed zero). If the fixed-point text has no `.` (happens when the value's decimal
    exponent is ≥ 0, e.g. `1e17 -> Decimal("1E+17") -> "100000000000000000"`), a trailing `.0` is
    appended so the text still reads as a float, not an integer.
  - `format_value` now dispatches `Float`/`Double` types to `_format_float` instead of falling
    through to the generic `str(value)` branch (which remains for `Int*`/`FixedDecimal`/strings —
    `Decimal.__str__` already produces plain notation for the scales golden data uses, and ints
    never have an exponent).
  - `parse_value` is unchanged — `float(text)` already parses plain-decimal text correctly, so the
    round-trip fix only needed to change the write side.

### Covering tests

Added to `tests/test_foundations.py` (section "fix round 1: Float/Double must be written in plain
decimal notation (contract C1)"):

- `test_format_float_never_uses_scientific_notation` — no `e`/`E` in the output for
  `1e17, 1e-7, 0.1, 250.005, 1/3, -0.0, 0.0, 1.7976931348623157e308, 5e-324, -1e-7`.
- `test_format_float_round_trips_exactly_including_negative_zero` — for the same values,
  `parse_value(format_value(x, "Double"), "Double") == x` **and** `math.copysign(1.0, back) ==
  math.copysign(1.0, value)` (catches a fix that round-trips numerically but loses `-0.0`'s sign).
- `test_format_float_keeps_shortest_digits_no_float_noise` — `0.1 -> "0.1"`,
  `1/3 -> "0.3333333333333333"` (not padded with binary-float noise digits).
- `test_format_float_integral_values_keep_fractional_part` — `100.0 -> "100.0"`,
  `1e17 -> "100000000000000000.0"`.
- `test_format_float_rejects_non_finite_values` — `inf`/`-inf`/`nan` raise `ValueError` naming the
  value.
- `test_typed_csv_float_column_round_trip_plain_decimal` — a full `write_table`/`read_table` round
  trip on a `Double` column containing `1e17, 1e-7, 0.1, -0.0, None`; asserts no `e`/`E` anywhere
  in the on-disk CSV text and that the table reads back equal.

### RED (before the fix, on the pre-fix `typed_csv.py`)

Command:

```
.venv/Scripts/python.exe -m pytest tests/test_foundations.py -k "format_float or float_column" -v
```

Relevant output (4 of the 6 new tests failed; the other 2 passed incidentally because `str(0.1)`
and a same-sign-only round-trip happened to already satisfy those specific assertions):

```
AssertionError: assert ('e' not in '1e+17' ...)          # test_format_float_never_uses_scientific_notation
AssertionError: assert '1e+17' == '100000000000000000.0' # test_format_float_integral_values_keep_fractional_part
AssertionError: expected ValueError for inf               # test_format_float_rejects_non_finite_values
AssertionError: assert "e" not in text.lower()             # test_typed_csv_float_column_round_trip_plain_decimal
4 failed, 2 passed, 16 deselected in 0.13s
```

### GREEN (after the fix)

Command:

```
.venv/Scripts/python.exe -m pytest tests/test_foundations.py -v
```

Output:

```
collected 22 items
tests\test_foundations.py ......................                         [100%]
22 passed in 0.11s
```

Also ran the full repo suite (includes Task 2's `tests/test_yxdb.py`, untouched by this fix) to
confirm no regressions:

```
.venv/Scripts/python.exe -m pytest
....................................
36 passed in 0.18s
```

### Files changed (this round)

- `scripts/lib/typed_csv.py` (modified — `_format_float` helper, `format_value` dispatch)
- `tests/test_foundations.py` (modified — 6 new tests, `math` import)

Committed as `5d55cd7` — "fix: write floats in plain decimal notation in typed CSV". Staged only
these two files (`docs/superpowers/plans/2026-09-18-pipeline/01-foundations-parser.md` was already
modified in the working tree by Task 2 and was left untouched, per the coordinator's instruction).
