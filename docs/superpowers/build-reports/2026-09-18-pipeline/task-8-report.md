# Task 8 report — `dev/formula.py` and `dev/alteryx_sim.py`

**Status:** DONE · branch `wt/task-8` in `.worktrees/task-8` · commit `bf771ee`

## What I implemented

### `scripts/dev/formula.py` (982 lines)

A tokenizer, a recursive-descent parser over a tuple AST, and a three-valued evaluator, plus
`coerce`, `RowWindow` and the `Expr`/`compile_expr` pair from the brief's interface block.

The one decision that shapes everything else: **arithmetic is computed exactly in `Decimal`**, a
float operand entering through `Decimal(repr(x))`, and the result widened back to the widest
operand type (`int` < `Decimal` < `float`). That is what makes the brief's
`Round([A] * IIF([Q] >= 10, 0.9, 1), 0.01) == 30.02` true: in binary floating point
`33.35 * 0.9` is `30.014999999999997`, which rounds **down** to `30.01`, and only the exact
decimal product `30.015` rounds to `30.02`. `samples/wf_0001/README.md` states the same
intermediate ("`33.35 × 0.9 = 30.015` → half cent → 30.02"), and the same reasoning covers its
other two hand-computed cases (`250.005 × 1 → 250.01`, `2500.75 × 0.9 = 2250.675 → 2250.68`).
`Round` returns a `Double`, which is also required: a `Decimal` actual compared against
`pytest.approx(2.68)` raises `TypeError: unsupported operand type(s) for -: 'float' and
'decimal.Decimal'` (verified against the installed pytest 9.1.1).

Everything else follows the brief's semantics list: NULL propagation, three-valued
`AND`/`OR`/`NOT`, `IF`/`IIF` treating NULL as false, case-sensitive string `=`, case-insensitive
`Contains`/`StartsWith`/`EndsWith` by default, warn-and-null `ToNumber`, 0-based `Substring`, the
string/regex/date function set, and the `coerce` rules per Alteryx type.

### `scripts/dev/alteryx_sim.py` (1152 lines)

Topological execution over the data nodes, one `sim_<type>` per tool type (23 of them), a
`_Context` carrying constants, the golden seed, sinks and warnings, and:

- **the database Output path** — an in-memory `duckdb` connection loaded with `targets_before`,
  PreSQL through `sqlglot.transpile(read="tsql", write="duckdb")`, the write mode applied by
  column **name**, PostSQL, then the table read back. I read it back **unordered and sort in
  Python**, because DuckDB's `ORDER BY` on a `DOUBLE` canonicalises `-0.0` to `0.0` — which the
  `edge` set of wf_0002 exercises (order 3001's `-0.0` amount). Sorting in Python also gives a
  target's rows the same NULL-first order the Sort tool produces.
- **`run()`** — reads `parsed/dag.json`, the C7 `segments/*/dag.json`, `golden/inputs/<set>/` and
  `golden/targets_before/<set>/`; writes `golden/intermediates/<seg>/<set>/<stream>.csv` for every
  outbound stream **except one whose destination is an `output` tool inside the same segment**,
  `golden/outputs/<set>/<tool_id>.csv` per Output tool, and `manifest.golden_sets`. It simulates
  every set before writing anything, so an `UnsupportedTool` really does leave the workflow with
  no golden files at all. A set with no input directory is skipped rather than failing.
- **the CLI** — `python scripts/dev/alteryx_sim.py <wf_id> [--set …] [--root .]`, with a guarded
  `sys.path` line so the file works both as a script (where Python puts `scripts/dev` on the path,
  not `scripts`) and as the `dev.alteryx_sim` module pytest imports. `read_logical_by_tool` reads
  `intake/mappings.yaml` (C6 `tool_ids` → `logical`) when intake has run, else
  `samples/<wf>/sample.json`'s `logical` map. Exit 1 when nothing was produced.

### `docs/reference/simulator-semantics.md`

Ten sections, one rule per line, each line ending with `*(assumption — verify per Alteryx
version)*`. The marker is repeated per line deliberately, so a rule quoted on its own into a
cookbook page keeps its caveat. The file says plainly that no Alteryx engine was available.

## TDD evidence

**RED** — both test files written first (the brief's code verbatim, plus my additions), then:

```
$ .venv/Scripts/python.exe -m pytest tests/test_formula.py tests/test_alteryx_sim.py
tests\test_formula.py:2: in <module>
    from dev.formula import evaluate, coerce, RowWindow, FormulaError
E   ModuleNotFoundError: No module named 'dev'
tests\test_alteryx_sim.py:3: in <module>
    from dev.alteryx_sim import simulate, UnsupportedTool
E   ModuleNotFoundError: No module named 'dev'
2 errors in 0.14s
```

That is the failure Step 2 predicts: `scripts/dev/` did not exist yet.

**GREEN** — after implementing, and again after every cleanup:

```
$ .venv/Scripts/python.exe -m pytest tests/test_formula.py
88 passed in 0.06s

$ .venv/Scripts/python.exe -m pytest tests/test_alteryx_sim.py
26 passed in 0.27s

$ .venv/Scripts/python.exe -m pytest          # whole repo suite
280 passed in 0.69s
```

Output is pristine — no warnings, no stray prints.

## Brief corrections

**None.** Every expected value in the brief's two test files is implemented as written and passes
unchanged. Two tests *I* added were corrected while writing them; both were my own mistakes, not
the brief's:

1. `test_row_window_nearest_and_forward_offsets` asserted `[Row+1:V]` at index 0 of a three-row
   group was NULL. It is not — index 1 exists and holds `2`. I changed the assertion to check
   both: in-range gives `2`, and the same lookup from the last row gives NULL.
2. My decimal-semantics test compared a `Decimal` result against `pytest.approx(30.015)`, which
   raises `TypeError` rather than failing. Resolved by the widening rule (a float operand gives a
   float result), so the assertion now works as written.

## Tests added beyond the brief

The brief's prose describes far more than its tests cover, so I added, in the same files:

- **formula** (60 more cases): three-valued `AND`/`OR`/`NOT`, `<>`/`==`/`&&`/`||`/`!` spellings,
  `IN` with NULL, string `+` both ways, `Mod(-7, 3) == -1`, `% 0`, case-insensitive keywords and
  field names, every string function including `TitleCase("don't stop now")`, `REGEX_Match` with
  and without the case flag, `Min`/`Max` ignoring NULL, `DateTimeAdd` month clamping
  (`2026-01-31 + 1 month = 2026-02-28`), negative and month-unit `DateTimeDiff`, `ToDate`, the
  `int < Decimal < float` widening rule, `compile_expr` reuse, unknown function and unknown field
  errors, `RowWindow` `"nearest"`, and `coerce` for Bool/Date/DateTime/invalid dates/unknown types.
- **simulator** (16 more tests): `meta.Output` typing of an input, `block_until_done` on all three
  anchors, sort NULL placement ascending **and** descending with stability, all four Sample modes,
  Union by position, Record ID in last position, RegEx `replace` (with and without
  `CopyUnmatched`) and `match`, the Data Cleansing option set with title case, Summarize
  `Min/Max/Avg/CountDistinct/First/Concat` and their output types, `Sum` keeping `FixedDecimal`,
  `append` against a prior target state, a file Output emitting no stream, `run_command` and an
  unresolved macro being refused, and two `run()` tests over hand-written C7 segment dags in
  `tmp_path` (including the "no intermediate for a stream an Output tool inside the segment
  captures" rule and the write-nothing-on-`UnsupportedTool` rule).

## Smoke check against the samples (not committed)

I parsed each sample with `parse.parse_file` and fed `simulate` the golden inputs, `targets_before`
and `sample.json`'s `logical` map, for all four sets. Everything the READMEs claim holds:

| Sample | README's claim | Observed |
|---|---|---|
| wf_0001 normal | rows 4, 5, 12, 20 reach tool 8 (WEST, and NULL REGION via the NULL→False rule) | `3_F` = exactly those four |
| wf_0001 normal | `250.005×1 → 250.01`, `33.35×0.9 → 30.02`, `2500.75×0.9 → 2250.68` | all three exact |
| wf_0001 normal | 99.99 SMALL / 100.00 MEDIUM / 1000.00 LARGE; NULL QTY takes the ×1 branch; NULL AMOUNT falls to SMALL | all hold |
| wf_0001 normal | `CUSTOMER` truncated to `String(10)` | `Echo Suppl`, `Chandrasek`-style truncation |
| wf_0001 edge | `0.005 → 0.01`, `2.675 → 2.68` (not 2.67), `0.004 → 0.00`, `-0.0` kept negative | all hold, including `NET = -0.0` |
| wf_0002 normal | 8 on `J`, 1 on `L`, 1 on `R`, union 10, append leaves **12** in the target | exactly that |
| wf_0002 normal | cleansing trims + uppercases, NULL `NAME` → `""`; `abc`→NULL amount; `2026-02-30`→NULL date | all hold |
| wf_0002 edge | duplicate customer 12 × duplicate order 3003 fans out both sides | 4 identical joined rows, plus 2 for 3004 |
| wf_0003 normal | filter drops 2, unique drops 2, **9 rows → 6 groups** | 2, 2, 9, 6 |
| wf_0003 normal | PreSQL deletes `4000/2019-12`, `9999` untouched with `LOADED_FLAG` `N`, inserts get `Y` | exactly that |
| wf_0003 normal | `7000`'s second period carries its running balance over | `2026-08` closes 12.50, `2026-09` closes 112.49 |
| wf_0004 normal | macro drops 2 of 14; six cross-tab groups; `AB-12` all three warehouses, `ZZ-9` two | 12 rows, 6 groups, exactly that shape |
| wf_0004 normal | `ab 12`, `" AB-12 "`, `AB-12` collapse to one group; `bad sku!` parses to NULL columns | both hold |
| wf_0005 | simulator refuses it | `UnsupportedTool: tool 2 has type 'unknown'` |
| all | `empty` set | header-only CSVs with full schemas |

No warnings were emitted for any sample in any set. I also ran the CLI end to end against a
temporary repo root seeded from `samples/`, with hand-written C7 segment dags for wf_0003
(`seg_01` = 1–3, `seg_02` = 4–10, the segmentation its README describes):

- wrote `golden/intermediates/seg_01/<set>/3_Output.csv` and `golden/outputs/<set>/10.csv` for all
  four sets — the two paths Task 9's test (b) names — and set `manifest.golden_sets`, exit 0;
- wrote **no** intermediate for `seg_02`, whose only outbound edge feeds Output tool 10 inside it;
- for wf_0005 printed the reason, wrote nothing, set `golden_sets: []`, exit 1;
- a second run produced byte-identical files (`diff -r` clean), which Task 9's test (e) needs.

### Two things worth flagging

1. **DuckDB's `ORDER BY` destroys `-0.0`.** Reading a database target back with
   `ORDER BY … ASC NULLS FIRST` turned wf_0002's `-0.0` amount into `0.0`; a plain `SELECT`
   preserves it. I now sort in Python. Whoever writes `compare.py` should know the oracle keeps
   the sign and a Snowflake round-trip may not — that is a `ROUNDING`-class difference, not a bug
   in either side.
2. **wf_0003's `5000` "Last is not the largest balance" note.** The README says entry 7 sits
   between two `2026-08` rows so the `Last RUN_BAL` of `(5000, 2026-08)` is not simply the
   group's largest. The observed numbers (`2026-07` closes 30.0 on a 20.00 total, `2026-08` closes
   105.25 on an 85.25 total) are internally consistent and prove `Last` is order-dependent, but
   with these amounts the last balance *is* also the largest. The sample still exercises the
   behaviour it exists for; the README's parenthetical is just stronger than its data.

## Files changed

| File | |
|---|---|
| `scripts/dev/__init__.py` | new, package docstring only |
| `scripts/dev/formula.py` | new, 982 lines |
| `scripts/dev/alteryx_sim.py` | new, 1152 lines |
| `docs/reference/simulator-semantics.md` | new |
| `tests/test_formula.py` | new, 88 cases |
| `tests/test_alteryx_sim.py` | new, 26 tests |

Nothing under `samples/`, `scripts/lib/`, `scripts/parsers/` or any other existing file was
touched. `git add` named the six paths explicitly.

## Self-review findings (fixed before committing)

- `sim_cross_tab` dropped a missing group-by field from the output columns but kept it in the
  group key, which would have produced rows longer than the field list. Both now come from the
  same filtered list, with a warning.
- `_aggregate` used `as_number(v) or Decimal(0)`, which silently swallows `Decimal('0.00')`
  because it is falsy. Replaced with an explicit `is not None` filter.
- `_apply_write_mode` would have emitted `INSERT INTO t ()` when no incoming column matched the
  target by name. It now warns and leaves the target alone.
- Made `truth`, `extreme`, `compiled_pattern`, `regex_replacement`, `date_format` and `date_parse`
  public in `formula.py` rather than having the simulator reach into underscore names; the
  DateTime tool now calls `date_format`/`date_parse` directly instead of round-tripping through a
  one-line `evaluate` call.
- Renamed the reader to `read_logical_by_tool` so it does not read as the `logical_by_tool`
  argument it produces; removed an unused `_values` helper and an unused `string` import; wrapped
  the handful of over-108-character lines; de-shadowed a reused `row` variable in `sim_cross_tab`.

## Concerns

- **File size.** `alteryx_sim.py` is ~1150 lines and `formula.py` ~980. The plan fixes these two
  files and the brief enumerates 23 tool semantics plus a whole expression language into them, so
  I did not split them on my own (the rules say not to). Both are sectioned, and every tool is one
  self-contained `sim_<type>(node, inputs_by_anchor, ctx)`, so the reading unit is a function, not
  the file. Flagging it rather than acting on it.
- **`append_fields` is not implemented.** It is in dag-contract §2's plugin table but has no
  `config` shape in §4 and is not in the brief's tool list, and no sample uses it. It raises
  `UnsupportedTool` naming the tool id rather than being guessed at. The same is true of
  Summarize percentiles and Sample's random mode.
- **An `output` tool inside a macro** produces a warning and no golden file. No sample has one,
  and namespacing its tool id into `golden/outputs/` would invent a filename the contract does not
  define.
- **The regex dialect is Python's `re`**, not Alteryx's PCRE. The sample patterns are in the
  common subset, but this is a real parity risk for any workflow using PCRE-specific syntax, and
  it is recorded in the semantics doc.
- **Everything here is an assumption.** Nothing has run against a real Alteryx engine. The values
  are hand-verified against the brief and the sample READMEs, which are themselves hand-written.
  If the oracle is wrong, every downstream parity verdict inherits the error — which is why every
  line of `simulator-semantics.md` carries its marker.

---

# Fix round 1

Commits `6fab052` and `7a87d7f`, on `feat/pipeline-m0-m2` in the main checkout (my worktree was
merged and removed). Two Important findings, plus two regressions I caught reading back my own
first fix commit.

## Correction to this report (finding 1d)

**The claim above — "in binary floating point the product is `30.014999999999997`" — is false.**
I measured it in this project's venv:

```
$ .venv/Scripts/python.exe -c "print(repr(33.35*0.9))"
30.015
```

The true binary64 value is `30.0150000000000005684341886080801486968994140625`, slightly **above**
the half, so `Round(33.35 * 0.9, 0.01)` is `30.02` under binary arithmetic too. The brief's test
passes either way, and `33.35 * 0.9` was never evidence for exact-decimal arithmetic. I did not
verify the number before asserting it; the design decision it was offered to justify is real and
has been accepted on other grounds, but the justification I gave for it was not.

The two other examples in that section (`2500.75 * 0.9` and `250.005 * 1`) are also
non-diverging: I checked all three, and binary and decimal agree on every one. They do
demonstrate half-away-from-zero rounding, which is a different and still-true point, and the test
that holds them is now named for that.

Everything else in the sections above stands. The reviewer hand-recomputed the Summarize, Cross
Tab and update/insert expectations and confirmed them.

## Finding 1 — the false claim, and the missing disclosure

**Where it appeared:** `docs/reference/simulator-semantics.md:22`, `scripts/dev/formula.py:12-13`
(module docstring), `tests/test_formula.py:128` (comment). All three corrected.

**What I measured first**, so the replacement text is fact rather than another claim:

```
$ .venv/Scripts/python.exe -c "<200,000-sample comparison, seed 20260918>"
1333 of 200000 differ = 0.67%
   (4578.9, 0.95, '4349.954999999999', '4349.95', '4349.96')
   (3684.5, 0.95, '3500.2749999999996', '3500.27', '3500.28')
   (3457.7, 0.95, '3284.8149999999996', '3284.81', '3284.82')
   (4047.95, 0.9, '3643.1549999999997', '3643.15', '3643.16')

4094.9 * 0.95: binary repr 3890.1549999999997 -> 3890.15   exact 3890.155 -> 3890.16
4640.5 * 0.85: binary repr 3944.4249999999997 -> 3944.42   exact 3944.425 -> 3944.43
575.4  * 0.075: binary repr 43.154999999999994 -> 43.15    exact 43.1550 -> 43.16
math.floor(4.35*100) = 434 (repr 434.99999999999994); exact floor = 435
```

0.67% matches the controller's independent measurement (1,345 of 200,000 on its seed; 1,333 on
mine). I quote my own run in the doc and say which draw produced it.

**What the doc says now.** `simulator-semantics.md` gains a section **§1.1 "Exact decimal
arithmetic is a deliberate simplification, and it differs from Alteryx"**, which states plainly
that Alteryx computes `Double` values in IEEE-754 binary64 and this simulator does not; that where
they disagree **the simulator is the one that is not Alteryx**; the measured 0.67% with how it was
measured; the `4094.9 * 0.95` and `Floor(4.35 * 100)` examples; `33.35 * 0.9` explicitly called
out as *not* such a case; why the simplification was chosen (no engine available to settle
half-boundary behaviour, and it is the only model a `NUMBER`-based SQL translation can reproduce
deterministically on any engine); and the marker that a difference of this kind is diff class
**`ROUNDING`**, or a one-unit **`LOGIC`** difference when a `Floor`/`Ceil` sat on the boundary.
The §1 bullet that asserted exact-decimal arithmetic flatly now carries
*(deliberate simplification — see §1.1; verify against real Alteryx)* instead of the generic
marker, and the measured lines carry *(measured fact, not an assumption; rerun it to confirm)*.

**The test that keeps it honest:**
`test_exact_decimal_arithmetic_differs_from_binary64_in_these_documented_cases` asserts **both
sides** of every documented example — what plain Python binary floats give
(`repr(4094.9 * 0.95) == "3890.1549999999997"`, `math.floor(4.35 * 100) == 434`) *and* what the
evaluator gives (`Round(…, 0.01)` = 3890.16, `Floor([A] * 100)` = 435) — plus
`repr(33.35 * 0.9) == "30.015"` as the counter-example. The doc cites that test by name.

## Finding 2 — `Pow` was the one binary-arithmetic path

`_power` computed `math.pow(float(first), float(second))`. Fixed: it now evaluates
`first ** second` as `Decimal` inside `localcontext(prec=_PRECISION)` (40 digits), and widens with
the same `max(rank(base), rank(exponent))` rule `*` uses, floored at `Decimal` because a negative
or fractional exponent rarely lands on an integer. Integral exponents are exact; a fractional one
is carried at the fixed 40-digit precision rather than the platform's `libm`, so it is
deterministic though not exact — documented as the only place exactness is impossible. Undefined
results (zero to a negative power, negative base under a root) are NULL, as `/ 0` is.

**Re-scan of every other arithmetic path** (`grep -n "math\.\|float(" scripts/dev/formula.py`,
plus the whole `FUNCTIONS` table and `alteryx_sim.py`):

| Path | Verdict |
|---|---|
| `Sqrt`, `Exp`, `Log`, `Log10`, `Ln` | **Do not exist** — not in the brief's function list, not implemented |
| `_date_diff`'s `total_seconds() / unit` and `months / 12` | **Was** a binary division. Now integer arithmetic on microseconds via `_truncate_toward_zero`; a new test pins truncation toward zero on both sides of zero |
| `Mod` / `%` with a fractional divisor | Already exact: Decimal `%` under `prec=40`. `Mod(5.5, 2.5)` gives `Decimal('0.5')` |
| Summarize `Avg` (`alteryx_sim.py:399`) | Decimal division, but at Python's default 28-digit context rather than 40. Exact-decimal and identical on every platform, so **disclosed in the doc**, not changed — `alteryx_sim.py` stayed untouched |
| `_widen` / `coerce` / `_to_number` / `_round` returning `float(...)` | Not arithmetic: one conversion of an exactly-computed decimal to the nearest double at a `Double` field boundary. **Disclosed** in §1 as "the arithmetic is decimal, the storage is binary" |
| `math.isfinite` | A predicate, not arithmetic |

## Two regressions I introduced, then caught

Reading back `6fab052` before writing this section, my new `Pow` had broken two things the old one
got right. Both are fixed in `7a87d7f`, each with a failing test written first.

1. **`Pow(0, 0)` returned NULL.** `math.pow(0, 0)` is `1.0`, but `Decimal(0) ** Decimal(0)`
   signals `InvalidOperation`, which my `except ArithmeticError` swallowed into NULL. Restored to
   `1` with an explicit guard, and documented.
2. **`Pow(10.0, 400)` returned `inf`.** `math.pow` raised `OverflowError` and was caught;
   `Decimal` produces a finite `1E+400` that `float()` turns into an infinity. `typed_csv`
   refuses to write a non-finite float, so this was a latent crash in the oracle — and
   `[A] * [B]` with `1e200` had the identical hole *already*, since both go through `_widen`.
   I put the guard in `_widen`, at the one place the conversion to a double happens, so every
   arithmetic path treats a `Double` overflow the same way: NULL, since contract C1 has no
   spelling for an infinity. **This is the one change that reaches beyond the two findings**; I
   made it there rather than inside `_power` because a guard in `_power` alone would have
   recreated exactly the kind of per-function inconsistency finding 2 was about. It cannot alter
   a finite result, and the sample diff below proves it did not.

## TDD evidence

**RED (finding 2), before touching `_power`:**

```
$ .venv/Scripts/python.exe -m pytest tests/test_formula.py
>       assert evaluate("Pow(1.15, 2)", {}) == Decimal("1.3225")
E       AssertionError: assert 1.3224999999999998 == Decimal('1.3225')
>       assert evaluate("Pow(2, 0.5)", {}) == Decimal("1.414213562373095048801688724209698078570")
E       AssertionError: assert 1.4142135623730951 == Decimal('1.414213562373095048801688724209698078570')
2 failed, 91 passed in 0.11s
```

The two new divergence/DateTimeDiff tests passed on that run, and I state them as characterization
and regression guards rather than RED evidence: the divergence test documents behaviour that was
already correct, and the DateTimeDiff cases guard the change I was about to make.

**RED (the two regressions), before the `_widen` and `0 ** 0` guards:**

```
$ .venv/Scripts/python.exe -m pytest tests/test_formula.py -k "zero_to_the_zero or no_double_can_hold"
>       assert evaluate("Pow(0, 0)", {}) == 1
E       AssertionError: assert None == 1
>       assert evaluate("Pow([A], 400)", {"A": 10.0}) is None
E       AssertionError: assert inf is None
2 failed, 93 deselected in 0.07s
```

**GREEN:**

```
$ .venv/Scripts/python.exe -m pytest tests/test_formula.py tests/test_alteryx_sim.py
121 passed in 0.50s

$ .venv/Scripts/python.exe -m pytest
468 passed in 8.43s
```

Output pristine. (The suite is 468 now rather than 280 — other tasks have merged since.)

## Golden-data regression check

Requested: confirm the four sample workflows are unchanged. I dumped **every stream and every
output**, with a type-revealing repr per cell (`float:3890.155` rather than `3890.155`, so an
int/float/Decimal swap cannot hide), for wf_0001–wf_0004 over **all four sets**, from the code
before the fix and after it:

```
$ diff before.txt after2.txt && echo "NO DIFFERENCE"
NO DIFFERENCE vs pre-fix baseline (988 lines compared)
```

All 16 workflow×set combinations are byte-identical, and wf_0005 still raises
`UnsupportedTool: tool 2 has type 'unknown'` on both its sets. As expected — no sample uses `Pow`,
`DateTimeDiff` or any value near a `Double`'s range. No warnings were emitted by any sample.

## Files changed in this round

| File | |
|---|---|
| `scripts/dev/formula.py` | `_power` rewritten on Decimal; `_widen` guards a `Double` overflow; `_date_diff` counts in integer microseconds via the new `_truncate_toward_zero`; module docstring corrected and the divergence disclosed |
| `docs/reference/simulator-semantics.md` | new §1.1; §1 bullets on precision, binary storage and overflow; `Pow` lines; `DateTimeDiff` line; the false `30.014999999999997` claim removed |
| `tests/test_formula.py` | 6 new tests (28 assertions); the misleading comment on the half-rounding test replaced and the test renamed |

`scripts/dev/alteryx_sim.py` and `tests/test_alteryx_sim.py` were **not** touched — nothing in
this round needed them.

## Concerns from this round

- **The oracle is knowingly not Alteryx on 0.67% of amount×factor roundings.** That is now
  disclosed rather than hidden, and the controller has accepted it, but whoever runs the first
  real parity comparison should expect `ROUNDING` diffs at this rate on any `Double` column that
  passes through a multiply-then-round, and a rare one-unit `LOGIC` diff behind a `Floor`/`Ceil`.
  A translation that does the arithmetic in `NUMBER` rather than `FLOAT` will match the oracle;
  one that uses `FLOAT` will not.
- **`Pow` with a fractional exponent is deterministic but not exact**, and it is the only such
  place. If a workflow ever needs `Sqrt`/`Exp`/`Log`, they will have the same property and must be
  added with the same fixed precision and the same disclosure.
- **`Avg` divides at 28 digits while `/` and `Pow` use 40.** Both are exact-decimal and
  platform-independent, and both are far beyond a `Double`'s 17 significant digits, so no result
  can differ — but it is an inconsistency a later reader may trip over. I left `alteryx_sim.py`
  untouched deliberately and disclosed it instead; unifying it would be a one-line change if the
  controller prefers.
- I did not address the reviewer's Minor items in this round, as instructed.
