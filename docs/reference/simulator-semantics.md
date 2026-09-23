# Simulator semantics — what `scripts/dev/alteryx_sim.py` believes Alteryx does

`scripts/dev/formula.py` and `scripts/dev/alteryx_sim.py` stand in for `AlteryxEngineCmd.exe` so
golden data exists without Alteryx. They are the parity **oracle**: `scripts/compare.py` measures
every translated Snowflake procedure against what they produce, so a rule that is wrong here turns
into a translation that is wrong everywhere.

**No Alteryx engine was available when these rules were written.** They come from
[the dag contract](dag-contract.md) §4, the program spec [§8](../spec/00-README.md#8-cookbook--parity-edge-cases)
and Alteryx's public documentation — never from an observed run, and never from what the generated
SQL happens to do. Every line below therefore ends with the same marker, repeated per line so that
a rule quoted on its own into a cookbook page carries its caveat with it. When an engine becomes
available, check the lines one by one and replace the marker with the version that was checked.

The line numbers in `§` references are this file's sections, not Alteryx's.

## 1. Values

- A value is `None`, `bool`, `int`, `float`, `decimal.Decimal` or `str`; dates are ISO strings, `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` (contract C1). *(assumption — verify per Alteryx version)*
- Arithmetic is computed exactly in `Decimal`; a `float` operand enters through `Decimal(repr(x))`, the shortest text that round-trips to the same double. *(deliberate simplification — see §1.1; verify against real Alteryx)*
- The result of an arithmetic operator is as wide as its widest operand — `int` < `Decimal` < `float` — so integer arithmetic stays integral and a `Double` column stays a `Double`. *(assumption — verify per Alteryx version)*
- A quotient need not terminate, so `/` and `Pow` are carried at a fixed 40 significant digits and Summarize's `Avg` at Python's default 28 — both far beyond a `Double`'s 17, and both the same on every platform. *(assumption — verify per Alteryx version)*
- Storing a result in a `Float` or `Double` field converts that exact decimal to the nearest binary double, once, at the field boundary: the arithmetic is decimal, the storage is binary. *(assumption — verify per Alteryx version)*
- A magnitude no `Double` can hold is NULL rather than an infinity, on every path alike — contract C1 has no spelling for an infinity, and `typed_csv` refuses to write one. *(assumption — verify per Alteryx version)*
- Numeric text is a plain decimal only: optional sign, digits, optional fraction, optional exponent. `1,200.50` is not a number. *(assumption — verify per Alteryx version)*

### 1.1 Exact decimal arithmetic is a deliberate simplification, and it differs from Alteryx

**Alteryx computes `Double` values in IEEE-754 binary64. This simulator does not.** It computes
in exact decimal and converts to a double only when storing into a `Float`/`Double` field. The two
models agree on most values and disagree on a minority, and where they disagree **the simulator is
the one that is not Alteryx**. This is the single largest known parity risk in the oracle.

- Measured in this repo's venv: over 200,000 random amounts (`round(uniform(0.01, 5000), 2)`) times a factor drawn from `{0.9, 0.85, 0.95, 1.1, 0.075, 1.0}`, rounding the product to cents half away from zero, the binary product and the exact decimal product give a **different cent in 1,333 cases — 0.67%**. *(measured fact, not an assumption; rerun it to confirm)*
- `4094.9 * 0.95` is `3890.1549999999997` in binary64 and `3890.155` exactly, so rounding to cents gives **3890.15 in Alteryx and 3890.16 here**. Likewise `4640.5 * 0.85` (.42 against .43) and `575.4 * 0.075` (.15 against .16). *(measured fact, not an assumption; rerun it to confirm)*
- `Floor(4.35 * 100)` is **434 in binary64** (the product is `434.99999999999994`) and **435 here**. A `Floor` or `Ceil` on a boundary turns a sub-cent difference into a whole-unit one. *(measured fact, not an assumption; rerun it to confirm)*
- `33.35 * 0.9` is **not** such a case, despite looking like one: the binary product is `30.015` (just above the half), so binary and decimal both round it to `30.02`. *(measured fact, not an assumption; rerun it to confirm)*
- Why the simplification was chosen: no Alteryx engine was available to settle what its `Round` actually does at a binary half-boundary, and exact decimal is the only model a translated procedure can reproduce deterministically on any engine — by doing the arithmetic in `NUMBER` rather than `FLOAT`. A binary oracle would have pinned the golden data to one platform's `libm`. *(assumption — verify per Alteryx version)*
- What to do with a difference of this kind when it shows up in validation: it is diff class **`ROUNDING`**, or a one-unit **`LOGIC`** difference when a `Floor`/`Ceil` sat on the boundary — not a translation bug. *(assumption — verify per Alteryx version)*
- `tests/test_formula.py::test_exact_decimal_arithmetic_differs_from_binary64_in_these_documented_cases` asserts both sides of every example above, so this section cannot drift away from what the code does. *(measured fact, not an assumption; rerun it to confirm)*

## 2. NULL and the three-valued operators

- NULL propagates through arithmetic, through string `+`, and through every function unless that function is listed otherwise below. *(assumption — verify per Alteryx version)*
- Any comparison with NULL on either side is NULL, never true and never false. *(assumption — verify per Alteryx version)*
- `AND`, `OR` and `NOT` are three-valued: `NULL AND FALSE` is false, `NULL OR TRUE` is true, everything else touching NULL is NULL. *(assumption — verify per Alteryx version)*
- `IF`, `IIF` and the Filter tool treat a NULL condition as false, so NULL rows take the else branch and leave a Filter on `F`. *(assumption — verify per Alteryx version)*
- A condition that is neither boolean, numeric nor numeric text is a `FormulaError`, not a NULL — it is an authoring mistake and the oracle refuses to guess. *(assumption — verify per Alteryx version)*

## 3. Operators

- Precedence, low to high: `OR`, `AND`, `NOT`, comparison and `IN`, `+ -`, `* / %`, unary `-`. *(assumption — verify per Alteryx version)*
- `=` and `==` mean the same thing, as do `!=` and `<>`, `AND` and `&&`, `OR` and `||`, `NOT` and `!`. *(assumption — verify per Alteryx version)*
- Comparison of two strings is case-sensitive and by code point; `"ABC" = "abc"` is false. *(assumption — verify per Alteryx version)*
- `+` concatenates when either side is a string, using `ToString` on the other side. *(assumption — verify per Alteryx version)*
- `/` is float division and never truncates; dividing by zero gives NULL rather than an error, and so does `%` by zero. *(assumption — verify per Alteryx version)*
- `%` and `Mod` take the sign of the dividend: `Mod(-7, 3)` is `-1`. *(assumption — verify per Alteryx version)*
- `x IN (a, b)` is NULL when `x` is NULL, true on the first match, and NULL rather than false when no item matched but one comparison was NULL. *(assumption — verify per Alteryx version)*
- Identifiers, function names, keywords and field names are case-insensitive; `[qty]` and `[QTY]` are the same field. *(assumption — verify per Alteryx version)*
- A string literal is delimited by `"` or `'`, a backslash inside it is a literal backslash (regex patterns live in these), and a doubled quote is one quote. *(assumption — verify per Alteryx version)*
- A `[Name]` that is not a field of the current row is looked up in the workflow's `constants`; a name in neither is a `FormulaError`. *(assumption — verify per Alteryx version)*
- A constant's value is text, and converts to a number only when the other operand of an arithmetic or comparison operator is numeric. *(assumption — verify per Alteryx version)*
- `[%Question.<name>%]` is replaced textually, before parsing, by the macro's configured value or the interface default (dag contract §4 macro). *(assumption — verify per Alteryx version)*

## 4. Functions

- `ToNumber(s)` converts trimmed plain-decimal text and gives NULL for anything else, including `1,200.50` and the empty string — the warn-and-null of program spec §8.5. *(assumption — verify per Alteryx version)*
- `ToString(x)` prints an integral number without a fraction (`12.0` becomes `"12"`) and never uses exponent notation; a second argument fixes the number of decimals. *(assumption — verify per Alteryx version)*
- `Round(x, m)` returns the nearest multiple of `m` with halves away from zero, computed on exact decimals, so `Round(2.675, 0.01)` is `2.68` and `Round(-2.5, 1)` is `-3`. *(assumption — verify per Alteryx version)*
- `Round` returns a `Double`; `Abs` keeps its argument's type; `Ceil` and `Floor` return integers. *(assumption — verify per Alteryx version)*
- `Pow(b, e)` runs on the same exact decimals `*` does, so `Pow(1.15, 2)` and `[A] * [A]` agree on `1.3225`; it is never narrower than a `Decimal`, because a negative or fractional exponent rarely lands on an integer. *(assumption — verify per Alteryx version)*
- A **fractional** exponent is the one result in the whole module that cannot be exact — `Pow(2, 0.5)` is irrational — so it is carried at the fixed 40-digit decimal precision rather than the platform's `libm`, which makes it deterministic but not exact. *(deliberate simplification — the only unavoidable one; verify against real Alteryx)*
- `Pow` is NULL where it is undefined: zero to a negative power, and a negative base under a fractional exponent — the same treatment `/` gives division by zero. *(assumption — verify per Alteryx version)*
- `Pow(0, 0)` is `1`, the IEEE-754 convention, even though the decimal module calls it invalid. *(assumption — verify per Alteryx version)*
- `Min` and `Max` are scalar over their arguments, ignore NULL, and are NULL only when every argument is NULL. *(assumption — verify per Alteryx version)*
- `Contains`, `StartsWith` and `EndsWith` take an optional third argument and are **case-insensitive** when it is absent; `0` makes them case-sensitive. *(assumption — verify per Alteryx version)*
- `Substring(s, start, len)` counts `start` from 0 and returns the rest of the string when `len` is absent. *(assumption — verify per Alteryx version)*
- `Left`, `Right`, `PadLeft(s, n, c)`, `PadRight`, `Length`, `Uppercase`, `Lowercase`, `Replace(s, a, b)` behave as their names say; `PadLeft`/`PadRight` leave a string that is already `n` long alone. *(assumption — verify per Alteryx version)*
- `Trim`, `TrimLeft` and `TrimRight` remove whitespace only, never a caller-supplied character set. *(assumption — verify per Alteryx version)*
- `TitleCase` upper-cases the first character of each whitespace-delimited word and lower-cases the rest, so `don't` becomes `Don't`. *(assumption — verify per Alteryx version)*
- `REGEX_Match(s, p, icase)` and `REGEX_Replace(s, p, r, icase)` are case-**insensitive** unless `icase` is `0`, and `$1`, `$2` in `r` are capture groups. *(assumption — verify per Alteryx version)*
- The regular expression dialect is Python's `re`, which is close to but not identical with Alteryx's PCRE and Snowflake's dialect. *(assumption — verify per Alteryx version)*
- `IsNull(x)` is true only for NULL; `IsEmpty(x)` is true for NULL and for `""` but **not** for whitespace; `Null()` is NULL. *(assumption — verify per Alteryx version)*
- `DateTimeFormat(dt, fmt)`, `DateTimeParse(s, fmt)` and the DateTime tool understand `%Y %m %d %H %M %S %y %b %B %j`; text the format cannot read becomes NULL. *(assumption — verify per Alteryx version)*
- `DateTimeParse` returns a full `YYYY-MM-DD HH:MM:SS`, so a parsed date carries a midnight time. *(assumption — verify per Alteryx version)*
- `DateTimeAdd(dt, n, unit)` clamps the day when a month or year lands short — 31 January plus one month is 28 February — and keeps a date-only input date-only unless the unit is a time unit. *(assumption — verify per Alteryx version)*
- `DateTimeDiff(a, b, unit)` is `a − b` in whole units truncated toward zero, so two days and 23 hours is `2` days and `-2` days stays `-2`; the count is integer arithmetic on microseconds, never a binary division. *(assumption — verify per Alteryx version)*
- Units are `years months days hours minutes seconds` (and `weeks`), singular or plural; anything else is a `FormulaError`. *(assumption — verify per Alteryx version)*
- `DateTimeNow()` and `DateTimeToday()` raise `FormulaError("nondeterministic")`: an oracle that invents a clock reading cannot be compared against anything. *(assumption — verify per Alteryx version)*

## 5. `coerce` — one value into one Alteryx field type

- `String` and `WString` truncate to `size` characters silently; `V_String` and `V_WString` never truncate. *(assumption — verify per Alteryx version)*
- `Byte`, `Int16`, `Int32` and `Int64` round half away from zero, so `2.5` becomes `3` and `-2.5` becomes `-3`. *(assumption — verify per Alteryx version)*
- `FixedDecimal` quantizes to `scale` half away from zero, so `1.005` at scale 2 becomes `1.01`. *(assumption — verify per Alteryx version)*
- A `Bool` from a number is `!= 0`; from text, `true/t/y/yes` and `false/f/n/no` convert and anything else is NULL. *(assumption — verify per Alteryx version)*
- Text that is not a number, or a date that does not exist such as `2026-02-30`, becomes NULL rather than raising. *(assumption — verify per Alteryx version)*
- A field with no declared type passes its value through unchanged. *(assumption — verify per Alteryx version)*

## 6. `[Row-n:FIELD]` in a Multi-Row Formula

- Offsets are counted within the group, in incoming order, and a row inside the group reads the value that was **just computed** for it. *(assumption — verify per Alteryx version)*
- Outside the group, `OtherRows` decides: `"null"` gives NULL, `"zero"` gives `0` for a numeric field and `""` for anything else, `"nearest"` gives the group's first or last row's value. *(assumption — verify per Alteryx version)*
- A newly created field reads as NULL on rows whose own value has not been computed yet, which is what `[Row+1:NEW]` sees. *(assumption — verify per Alteryx version)*

## 7. Tools

- `input` emits its golden file, described by the field list in its `meta.Output` when the parser saw one, with every value coerced to those types. *(assumption — verify per Alteryx version)*
- `browse` emits nothing; `block_until_done` passes the same records to `Output1`, `Output2` and `Output3` and has no other effect. *(assumption — verify per Alteryx version)*
- `select` emits its listed selected fields in configuration order, then — when `*Unknown` is selected — every unlisted field in incoming order; a rename, a retype and a narrower size all apply through `coerce`. *(assumption — verify per Alteryx version)*
- A `select` that changes a field's type without giving a size uses that type's natural width and drops the source scale. *(assumption — verify per Alteryx version)*
- `filter` sends true to `T` and both false **and NULL** to `F`, whether or not the `F` anchor is wired. *(assumption — verify per Alteryx version)*
- `formula` runs its expressions in order, so a later one sees what an earlier one wrote; a new field is appended and an existing one keeps its position; every result goes through `coerce`. *(assumption — verify per Alteryx version)*
- `join` is an inner equi-join on the configured key pairs for `J`; a NULL key never matches anything, so its row leaves on `L` or `R`. *(assumption — verify per Alteryx version)*
- `J` rows come out in left order, and within one left row in right order; `L` and `R` keep their own side's order and their own side's fields untouched. *(assumption — verify per Alteryx version)*
- Join key comparison is case-sensitive and exact — no trimming, no case folding, no numeric coercion of text. *(assumption — verify per Alteryx version)*
- `J` carries the left fields then the right fields, a right field whose name collides is renamed `Right_<name>`, and only then does the join's own Select apply. *(assumption — verify per Alteryx version)*
- `union` stacks its inputs in `dst_order`; by name the output keeps the first input's field order, new names are appended as later inputs bring them, and a field an input lacks arrives NULL. *(assumption — verify per Alteryx version)*
- `union` by position takes the first input's fields and lines every other input up by ordinal. *(assumption — verify per Alteryx version)*
- `summarize` emits one row per group in ascending group-key order with NULL first. *(assumption — verify per Alteryx version)*
- `Sum` and `Avg` ignore NULL and are NULL for a group whose values are all NULL; `Count` counts rows, `CountNonNull` counts values, `CountDistinct` counts distinct non-NULL values. *(assumption — verify per Alteryx version)*
- `First` and `Last` take the first and last row of the group in incoming order, NULL included, which is why they pin a segment to the tool that ordered its rows. *(assumption — verify per Alteryx version)*
- `Concat` skips NULL, joins with the configured separator (a comma when none is configured), and gives `""` for a group with nothing to join. *(assumption — verify per Alteryx version)*
- Summarize output types: `Count*` is `Int64`, `Sum` and `Avg` are `Double` except that `Sum` of a `FixedDecimal` stays `FixedDecimal`, `Concat` is `V_String`, and everything else keeps its source type. *(assumption — verify per Alteryx version)*
- `sort` is stable and multi-key; NULL sorts first ascending and last descending, and strings sort by code point. *(assumption — verify per Alteryx version)*
- `unique` sends the first row of each key combination in incoming order to `U` and every later one to `D`, comparing keys case-sensitively. *(assumption — verify per Alteryx version)*
- `sample` keeps the first, the last, all but the first, or one in every N rows of each group, and emits what it kept in incoming order rather than grouped together. *(assumption — verify per Alteryx version)*
- `record_id` numbers rows from `start` in incoming order and puts its field first or last as configured. *(assumption — verify per Alteryx version)*
- `multi_row_formula` processes each group in incoming order and emits every row in the order it arrived, not grouped. *(assumption — verify per Alteryx version)*
- `cross_tab` emits one row per group in ascending group-key order, with the header columns frozen by `meta.Output` when the parser saw one and otherwise the sorted distinct sanitized header values. *(assumption — verify per Alteryx version)*
- A header value outside the frozen list is dropped with a warning, and a group/header combination with no rows is NULL rather than zero. *(assumption — verify per Alteryx version)*
- `cross_tab` sanitizes a header value by replacing every character outside `[A-Za-z0-9_]` with `_`, and applies only the first configured method. *(assumption — verify per Alteryx version)*
- `transpose` emits the key fields, then `Name` and `Value`, one row per input row per data field in configured order, keeping NULL values; `Value` takes the data fields' common type, or `V_String` when they differ. *(assumption — verify per Alteryx version)*
- `regex` `parse` appends the group fields, leaves them all NULL on a row that does not match, and never drops a row. *(assumption — verify per Alteryx version)*
- `regex` `replace` rewrites the field in place for every match in the value, and on a row that does not match keeps the original only when `CopyUnmatched` is set, otherwise writes NULL. *(assumption — verify per Alteryx version)*
- `regex` `match` appends a `Bool` field saying whether the pattern was found. *(assumption — verify per Alteryx version)*
- `datetime` appends its output field — `DateTime` one way, `V_String` the other — and writes NULL for text the format cannot read. *(assumption — verify per Alteryx version)*
- `data_cleansing` applies its options in this order: replace nulls, remove tabs/linebreaks/duplicate spaces, remove all whitespace, trim, remove letters, remove numbers, remove punctuation, modify case. *(assumption — verify per Alteryx version)*
- `data_cleansing` treats "replace nulls with blank" as string fields only and "replace nulls with 0" as numeric fields only, and applies the character and case options to string fields only. *(assumption — verify per Alteryx version)*
- `data_cleansing` `title` case capitalizes each whitespace-delimited word, and its punctuation set is ASCII punctuation. *(assumption — verify per Alteryx version)*
- `macro` substitutes its question values, feeds each `Input<id>` stream to the matching `macro_input`, and returns each `macro_output` as `Output<id>`; an Output tool inside a macro writes no golden file and is warned about. *(assumption — verify per Alteryx version)*

## 7.1 The Python tool

**This is an accident guard, not a security boundary: an allowed library can still reach the
filesystem and load native code (for example `DataFrame.to_csv`, or a submodule the
allow-list admits by its top-level package alone, such as `numpy.ctypeslib` or
`pandas.io.common`); the simulator runs only this repository's own committed sample
scripts and must never be pointed at an untrusted workflow's Python tool.**

- The tool's `<Script>` runs through two layers, neither of which is a real sandbox against a
  script that is actually trying to escape (see the disclaimer above — this is fix round 1's
  finding, closed for the specific gadgets below but not for every possible one): (1) a **static
  pre-check** (`_check_python_tool_script`) walks the parsed script before a single statement runs
  and refuses any dunder name or attribute access (`ast.Name`/`ast.Attribute` starting with `__`)
  and any `import`/`from … import` outside the allow-list; (2) `exec()` then runs the script with
  `__builtins__` replaced by a small allow-list (`abs all any bool dict enumerate float int
  isinstance len list max min range round set sorted str sum tuple zip reversed`, and the exception
  types `ValueError KeyError TypeError Exception`) and a guarded `__import__` that re-checks the
  allow-list at run time, so `open`, `eval` and `exec` are simply undefined names inside the
  script. *(deliberate simplification, modelling this repo's sandbox rather than Alteryx's own
  Python tool — verify against real Alteryx)*
- The static pre-check exists because dunder attribute access is not a builtin name or an import,
  so nothing in layer (2) touches it: `pd.__builtins__['__import__']` and
  `().__class__.__base__.__subclasses__()` (an imported module's own `__builtins__` is bound to the
  *real*, unrestricted interpreter builtins, not the `exec()` namespace's substituted dict) fully
  recovered the real `__import__`/`open` and let a script write arbitrary files before this fix;
  both are refused, naming the offending attribute or name, before the script runs at all.
  *(measured fact, not an assumption — `tests/test_alteryx_sim_python.py`'s dunder/gadget tests
  rerun the reviewer's exact probe scripts)*
- Only `pandas`, `numpy`, `re`, `math`, `datetime` and `decimal` (`PYTHON_TOOL_ALLOWED_MODULES`) may
  be imported. The test is on the **top-level package** (`name.split(".")[0]`), so a submodule of an
  allowed root — `numpy.ctypeslib`, `pandas.io.common` — is admitted along with it; a relative
  import is refused outright. Anything else is refused at parse time (and, redundantly, at run time
  by the guarded `__import__`), which also follows CPython's contract of binding the top-level
  package for a plain dotted `import a.b` and the submodule only for `from a.b import c`. A module that
  is allowed in but reaches the filesystem or network through its own, legitimate API — `DataFrame.
  to_csv`, `pandas.read_csv` on a path that exists, and any other real I/O an allowed library
  offers — is **not** refused: the guards restrict what the *script* can name, not what an imported
  library can do once it is running. This is exactly the disclaimer at the top of this section, not
  a gap the static check is meant to close. *(assumption — verify per Alteryx version)*
- The tool exposes one shim object, `Alteryx`, with `Alteryx.read("#N")` returning the Nth input
  connection as a `pandas.DataFrame` and `Alteryx.write(df, anchor)` recording `df` as the table for
  output anchor `anchor` (1..5); a script may write to any subset of the five anchors, and only the
  anchors it actually wrote produce a stream. *(assumption — models the real Python tool's
  `AlteryxEngine`-backed shim — verify against real Alteryx)*
- `Alteryx.read` builds its `DataFrame` from the incoming Table's already-`coerce`d Python values,
  then forces `Float`/`Double` fields to `float64` and `Bool` fields to pandas' nullable `boolean`
  dtype; every other field's dtype is whatever `pandas.DataFrame` itself infers from those values
  (e.g. a clean `Int32`/`Int64` column with no NULLs infers as `int64`; a string or mixed column
  infers as `object`) — the simulator does not force it. *(assumption — verify per Alteryx version)*
- `Alteryx.write` types the outgoing Table from the `DataFrame`'s own dtype, using pandas' own type
  predicates (`pandas.api.types.is_bool_dtype`/`is_integer_dtype`/`is_float_dtype`/
  `is_datetime64_any_dtype`, bool checked first) rather than a `str(dtype)` prefix match, so
  pandas' **nullable** dtypes are recognized alongside the plain numpy ones: `bool` or nullable
  `boolean` becomes `Bool`, `int*` or nullable `Int64`/`Int32`/… becomes `Int64`, `float*` becomes
  `Double`, `datetime64` becomes `DateTime` (`YYYY-MM-DD HH:MM:SS`), and everything else becomes
  `V_WString` — never by any Alteryx annotation the script might have intended. *(assumption —
  verify per Alteryx version; fix round 1 replaced an earlier `str(dtype)`-prefix version that
  missed the nullable dtypes entirely and stringified their NULLs to the literal `"<NA>"`)*
- `pd.isna(v)` decides NULL uniformly in **every** branch above, including the `V_WString` one: a
  `NaN`, `NaT`, `None` or `pd.NA` of any dtype becomes `NULL` on the way out, not just a float
  `NaN`. *(assumption — verify per Alteryx version)*
- A later `Alteryx.write` to the same anchor replaces an earlier one; only the anchors the script
  actually wrote appear in the result. *(assumption — verify per Alteryx version)*
- A script that raises — a syntax error, a runtime exception, or the sandbox itself refusing an
  import or a missing builtin — is reported as `UnsupportedTool` naming the tool id, exactly like
  any other tool this simulator cannot reproduce (§9); the workflow's golden data generation stops
  there, it is never guessed at. *(assumption — verify per Alteryx version)*
- This whole section is a model of what the real Alteryx Python tool (a Jupyter-notebook-backed
  `AlteryxEngine.PythonSDK` shim) does, built without ever running one. *(assumption — verify per
  Alteryx version)*

## 8. Output tools and the database

- A file target, and any target the run has no prior state for, produces exactly the incoming table. *(assumption — verify per Alteryx version)*
- A database target with a `targets_before` state, PreSQL or PostSQL is replayed in an in-memory DuckDB: load the prior state, run PreSQL, apply the write mode, run PostSQL, read the table back — and that final state is the golden output. *(assumption — verify per Alteryx version)*
- PreSQL and PostSQL are the source database's dialect, translated to DuckDB with `sqlglot` reading `tsql`; SQL that will not translate raises `UnsupportedTool` rather than being skipped. *(assumption — verify per Alteryx version)*
- Columns are matched to the target by **name**, not position; an incoming column the target does not have is not written and is warned about. *(assumption — verify per Alteryx version)*
- `append` inserts every row; `truncate_append` empties the table first; `overwrite` replaces the table with the incoming data and its schema. *(assumption — verify per Alteryx version)*
- `update_insert` updates the non-key columns of rows whose keys match and inserts the rest, leaving the target's other columns NULL on an inserted row. *(assumption — verify per Alteryx version)*
- A NULL update key matches nothing, exactly as the `=` of a translated `MERGE` would, so such a row is inserted. *(assumption — verify per Alteryx version)*
- The final state is read back ordered by the update keys, or by every column when there are none, NULL first — sorted in Python because DuckDB's `ORDER BY` turns `-0.0` into `0.0`. *(assumption — verify per Alteryx version)*

## 9. What the simulator refuses

- An `unknown` tool, a `run_command` tool, a macro whose file could not be resolved, and any tool type the simulator has no rule for raise `UnsupportedTool` naming the tool id. *(assumption — verify per Alteryx version)*
- A workflow with any such tool produces no golden data at all — not even for the tools upstream of it — and `manifest.golden_sets` becomes `[]`. *(assumption — verify per Alteryx version)*
- `append_fields`, `Summarize` percentiles, Sample's random mode and every other tool outside [the dag contract](dag-contract.md) §4 are simply not implemented, and say so rather than approximating. *(assumption — verify per Alteryx version)*
- A `python` tool is not on this list — it has a rule (§7.1) — but its script can still raise `UnsupportedTool` itself: a dunder name/attribute access or a disallowed import refused by the static pre-check, a sandbox `NameError` (`open`, `eval`, …), or any other exception the script's own code throws. *(assumption — verify per Alteryx version)* **This is an accident guard, not a security boundary: an allowed library can still reach the filesystem and load native code (for example `DataFrame.to_csv`, or a submodule the allow-list admits by its top-level package alone, such as `numpy.ctypeslib` or `pandas.io.common`); the simulator runs only this repository's own committed sample scripts and must never be pointed at an untrusted workflow's Python tool.**

## 10. Where these rules are checked

`tests/test_formula.py` and `tests/test_alteryx_sim.py` assert the values in this file against
hand computation; `tests/test_alteryx_sim_python.py` does the same for §7.1, including the fix
round 1 tests that rerun a reviewer's sandbox-escape probe scripts and assert they are refused
before any of the script runs. The five workflows under `samples/` are written to exercise them;
each `samples/wf_000N/README.md` says, row by row, which behaviour its data is there to prove.
Neither those samples nor these rules have been run past a real Alteryx engine. On §7.1's sandbox:
**this is an accident guard, not a security boundary: an allowed library can still reach the
filesystem and load native code (for example `DataFrame.to_csv`, or a submodule the
allow-list admits by its top-level package alone, such as `numpy.ctypeslib` or
`pandas.io.common`); the simulator runs only this repository's own committed sample
scripts and must never be pointed at an untrusted workflow's Python tool.**
