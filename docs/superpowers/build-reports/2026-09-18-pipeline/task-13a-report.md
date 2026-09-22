# Task 13a — hand migrations and canned artifacts for `wf_0001` and `wf_0002`

**Status: DONE_WITH_CONCERNS.** Everything the brief asks for exists and the whole suite is green,
but three of the five *required* broken variants do not produce the diff class the plan predicted,
for a reason that is structural rather than a translation mistake. That is written up under
[Brief corrections](#brief-corrections) and is the main thing a reviewer should look at.

Nothing in this work has run against a real Snowflake account or a real Alteryx engine. "Passes"
below always means: the translated procedure ran on the local DuckDB double (`lib/backend.py`)
against golden data produced by `scripts/dev/alteryx_sim.py`, and `scripts/compare.py` reported
`PASS`.

## What I implemented

### `wf_0001` — one segment, two final targets

| File | What it is |
|---|---|
| `samples/wf_0001/canned/segments/seg_01/proc.sql` | `MIG_WORK.WF0001_SEG_01`, two statements: the Summarize branch into `SALES_SUMMARY` and the Filter-False branch into `EXCLUDED_ORDERS` |
| `.../contract.json` | C5: two `kind: "target"` outputs (streams `6_Output` → tool 7, `3_F` → tool 8), `output == outputs[0]` |
| `.../translation_notes.md` | 21 assumptions, one per line, plus the two documented no-CTE exemptions |
| `.../review.json` | `{"verdict": "PASS", "findings": []}` |
| `samples/wf_0001/canned/{intake/plan.md, analysis.md, unsupported.json, docs/migration.md}` | the intake, analyzer and documenter artifacts the mock runner replays |
| `samples/wf_0001/broken_sql/seg_01/*.sql` + `broken.json` | four variants (three required, one added) |

### `wf_0002` — three segments in two waves

| File | What it is |
|---|---|
| `samples/wf_0002/canned/segments/seg_01/proc.sql` | Input + Data Cleansing → `MIG_WORK.WF0002_SEG_01_OUT` |
| `samples/wf_0002/canned/segments/seg_02/proc.sql` | Input (all text) + Select retype → `MIG_WORK.WF0002_SEG_02_OUT` |
| `samples/wf_0002/canned/segments/seg_03/proc.sql` | Join J/L/R + three Formulas + Union by name → `INSERT` into `CUSTOMER_ORDER_FACT` |
| three `contract.json`, three `translation_notes.md`, three `review.json` | as above; `seg_03`'s inputs carry `from`/`stream`/`table` |
| `samples/wf_0002/canned/{intake/plan.md, analysis.md, unsupported.json, docs/migration.md}` | as above |
| `samples/wf_0002/broken_sql/{seg_01,seg_03}/*.sql` + `broken.json` | three variants (two required, one added) |

### Tests

- `tests/test_canned_artifacts.py` — new, 23 tests, parametrized over whichever
  `samples/wf_*/canned/` directories exist, so `wf_0003`–`wf_0005` are covered the moment 13b
  lands. Checks: C4 signature and `parse_proc`; one `t<id>_<type>` CTE per data node with its
  `-- tool <id>:` comment, or a documented exemption; no catalog table name in any procedure
  (canned **or** broken); C5 contract keys and `output == outputs[0]`; C3 work-table names; a
  valid `tier`; the full canned artifact set; the documenter's nine sections; every `broken.json`
  row pointing at a real file, segment and golden set, with no unlisted `.sql` left over; and that
  the e2e parity tests cannot skip a workflow that has canned artifacts.
- `tests/test_e2e_parity.py` — the sanctioned one-line correction:
  `@pytest.mark.parametrize("wf,case", list(broken_cases()))`, which removes the
  `PytestRemovedIn10Warning` about passing a generator to `parametrize`.

## What I tested and the results

```
$ .venv/Scripts/python.exe -m pytest -q
777 passed, 2 skipped in 40.93s
```

The two skips are `test_hand_migration_passes_every_golden_set[wf_0003]` and `[wf_0004]` — Task
13b's scope. Nothing about `wf_0001` or `wf_0002` skips:

```
$ .venv/Scripts/python.exe -m pytest tests/test_e2e_parity.py tests/test_canned_artifacts.py -q -rs
..ss..............................                                       [100%]
SKIPPED [1] tests\helpers.py:57: samples/wf_0003/canned not written yet (plan Task 13)
SKIPPED [1] tests\helpers.py:57: samples/wf_0004/canned not written yet (plan Task 13)
```

Output is pristine — no warnings. The Node suite is unaffected: `fnm exec --using=22 npm.cmd test`
gives `# pass 81 # fail 0`.

Per-segment, against a scratch root outside the repo (`scripts/validate_segment.py`):

```
wf_0001/seg_01: PASS {'normal': 'PASS', 'period_end': 'PASS', 'empty': 'PASS', 'edge': 'PASS'} (idempotent=True)
wf_0002/seg_01: PASS {'normal': 'PASS', 'period_end': 'PASS', 'empty': 'PASS', 'edge': 'PASS'} (idempotent=True)
wf_0002/seg_02: PASS {'normal': 'PASS', 'period_end': 'PASS', 'empty': 'PASS', 'edge': 'PASS'} (idempotent=True)
wf_0002/seg_03: PASS {'normal': 'PASS', 'period_end': 'PASS', 'empty': 'PASS', 'edge': 'PASS'} (idempotent=True)
```

`scripts/compile_check.py` exits 0 for all four segments.

### The new tests are not vacuous

TDD was not possible in the usual order here (the artifacts already existed by the time the test
was written), so I proved the test bites by mutating one artifact at a time, running the suite, and
restoring the file from an in-memory copy in a `finally` block — no `git stash`, no
`git checkout --`, and `git status` was clean afterwards. Every mutation was caught:

| Mutation | Test that failed |
|---|---|
| CTE renamed away from `t5_summarize` | `test_every_data_node_has_a_cte_or_a_documented_exemption[wf_0001]` |
| the `-- tool 6:` comment removed | same |
| the `- tool 7 has no CTE:` exemption line removed | same |
| `SALES.RAW.ORDERS` written into the procedure | `test_no_procedure_names_a_real_catalog_table[wf_0001]` |
| `EXECUTE AS CALLER` → `EXECUTE AS OWNER` | `test_every_canned_procedure_has_the_c4_signature[wf_0001]` |
| `output` no longer equal to `outputs[0]` | `test_every_contract_has_the_c5_keys[wf_0001]` |
| an extra `broken_sql/*.sql` with no `broken.json` row | `test_every_broken_json_row_points_at_a_real_file_and_segment[wf_0001]` |

## Brief corrections

### 1. Three required broken variants cannot produce the predicted diff class, and why

The plan predicts `NULL_SEMANTICS`/`REGION` for wf_0001's Filter-False mistake, `TRUNCATION`/
`CUSTOMER` for the un-truncated Select, and `LOGIC` (with hint `case_only`) for wf_0002's two.
Every one of those classes is produced **only** by `compare.py`'s *keyed* diff — `_keyed_diff`,
`_row_presence_cluster` and `_classify`. The keyless path says so itself:

```python
# scripts/compare.py, _multiset_diff
if only_expected or only_actual:
    # With no keys there is nothing to line the rows up by, so no column can be blamed.
    diff.clusters.append(_cluster("UNKNOWN", [], …, note="no keys: the rows differ but cannot be paired"))
```

And **every stream these three variants touch has to be keyless**, because every `edge` golden set
in this project carries byte-identical duplicate rows on purpose:

| Stream | Duplicate in `edge` |
|---|---|
| wf_0001 `3_F` (tool 8) | `ORDER_ID` 103 twice, all seven columns identical |
| wf_0002 `2_Output` | `CUST_ID` 12 twice, all four columns identical |
| wf_0002 `4_Output` | `ORDER_ID` 3003 twice |
| wf_0002 `9_Output` (tool 10) | `(12, 3003)` four times |

I verified the consequence rather than assuming it. With `keys: ["ORDER_ID"]` on wf_0001's tool 8
target, the **correct** procedure fails:

```
wf_0001/seg_01 verdict=FAIL sets={'normal': 'PASS', 'edge': 'FAIL'}
  class=GOLDEN_DATA columns=['ORDER_ID'] stream=3_F note=duplicate keys in expected  (example: ORDER_ID 103, 2 rows)
  class=LOGIC       columns=['ORDER_ID'] stream=3_F note=duplicate keys in actual
```

`GOLDEN_DATA` is not in `accepted_diff_classes` and `manifest.accepted_diffs` is empty, so that is
a hard `FAIL` — which would break "all four golden sets PASS". No composite key helps: the rows are
identical in every column. So `keys: []` is forced, and the brief's own escape hatch applies ("if a
stream has no natural key, use `[]` and say so in translation_notes"). I followed the other half of
the instruction — "report what it produced instead of forcing it" — and recorded the observed class
in `broken.json`, with the plan's class kept alongside it under an `intended` key and a `note`
explaining the gap. The e2e test reads only `segment`/`file`/`golden_set`/`expect`, so the extra
keys are inert.

| Variant | Plan predicted | Observed (and recorded) |
|---|---|---|
| wf_0001 `01_filter_false_drops_null_region.sql` | `NULL_SEMANTICS` / `REGION` / `3_F` | `UNKNOWN` / `[]` / `3_F`, suspect CTE `t3_filter`, plus a synthetic `UNKNOWN` naming the columns whose aggregates moved |
| wf_0001 `02_select_customer_not_truncated.sql` | `TRUNCATION` / `CUSTOMER` | `UNKNOWN` / `['CUSTOMER']` / `3_F` — the column *is* named, by the string-length aggregates |
| wf_0002 `seg_01/01_cleansing_without_upper.sql` | `LOGIC`, hint `case_only` | `UNKNOWN` / `[]` / `2_Output` — upper-casing does not change a length, so not even the aggregates can name a column |
| wf_0002 `seg_03/01_union_drops_the_join_left_branch.sql` | `LOGIC`, rows missing | `UNKNOWN` / `['CUST_ID', 'MATCH_FLAG']` / `9_Output`, suspect CTE `t5_join` |

All four still **FAIL**, which is what the fixture is for; what is lost is the *classification*, not
the detection. Because that made three of the five required variants weak demonstrations, I added
two more that land on streams where a column *can* be named, so the intended lesson is still proved
somewhere in the repository:

- `wf_0001/broken_sql/seg_01/04_filter_true_keeps_null_region.sql` — the mirror mistake, the
  Filter's *True* branch written `WHERE REGION <> 'WEST' OR REGION IS NULL`. The NULL-region rows
  reach the Summarize and open a group whose `REGION` is NULL, in a column the contract declares
  `NOT NULL` → **`NULL_SEMANTICS` / `['REGION']` / `6_Output`**, suspect CTE `t5_summarize`.
- `wf_0002/broken_sql/seg_01/02_cleansing_keeps_null_names.sql` — Data Cleansing's
  "replace NULL strings with blank" not translated → **`NULL_SEMANTICS` / `['NAME']` /
  `2_Output`**.

The plan named these as "required variants, **at least**", so the extras are within scope. They
also sort after the required ones, which keeps the mock runner's "first `.sql` in name order is the
bad first attempt" contract pointing at variants (i) and (iv) as the brief asked.

**What a reviewer might want to decide:** this is a real gap in `compare.py`, not in the
translation. A keyless comparison could still classify a *value* difference if it paired rows by
their full column tuple minus one column at a time, or the `edge` golden sets could avoid
byte-identical duplicates — but I was told not to touch `scripts/` or the golden data, and I did
not.

### 2. "`ROUND` applied to the raw FLOAT" needed a different formulation to bite

The literal mistake — `ROUND(AMOUNT * IFF(QTY >= 10, 0.9, 1), 2)`, everything left in FLOAT —
produces **no difference at all** on these four golden sets, so it could not be a fixture (the e2e
test asserts `verdict == "FAIL"`). I checked every amount in the T branch of every set, exact
decimal against the local runtime's double `ROUND`:

```
normal  250.005 x1    exact=250.01 float=250.01
normal    33.35 x0.9  exact=30.02  float=30.02
normal  2500.75 x0.9  exact=2250.68 float=2250.68
period_end 2.675 x1   exact=2.68   float=2.68
edge       0.005 x1   exact=0.01   float=0.01
…  (25 values, none differ)
```

Two reasons. DuckDB's `ROUND(<double>, n)` multiplies by `10^n` before rounding, and that
multiplication happens to push `2.675` and `250.005` back *above* the half (`ROUND(1.005::FLOAT, 2)`
really does give `1.00`, so the drift is real — just not on these numbers). And the sample data was
chosen, per `simulator-semantics.md` §1.1, from the cases where binary and decimal *agree*
(`33.35 * 0.9` is called out there explicitly as not a disagreeing case). The cookbook's other
FLOAT form, `ROUND(x / 0.01) * 0.01`, differs only in the fourteenth decimal — well inside
`float_abs = 1e-6`.

So variant (iii) applies `ROUND` to the **raw FLOAT column `AMOUNT`** and then multiplies in FLOAT:
`ROUND(AMOUNT, 2) * IFF(QTY >= 10, 0.9, 1)`. That is one realistic slip (rounding the input instead
of the result, and leaving the product in FLOAT), it matches the brief's words read as "ROUND
applied to the raw FLOAT [column]", and it produces exactly what the plan asked for:

```
class=ROUNDING columns=['TOTAL_NET'] stream=6_Output scope=columns count=3 suspect=t5_summarize
  {'key': {'REGION': 'EAST', 'SIZE_BAND': 'LARGE'},  'expected': 4750.68, 'actual': 4750.675}
  {'key': {'REGION': 'NORTH', 'SIZE_BAND': 'SMALL'}, 'expected': 79.76,   'actual': 79.755}
  {'key': {'REGION': 'SOUTH', 'SIZE_BAND': 'MEDIUM'},'expected': 934.99,  'actual': 934.992}
```

The header comment in the file says precisely what the mistake is.

### 3. A node type the brief's CTE rule does not cover: `output`

The brief exempts `container`, `comment`, `browse` and `interface` from needing a CTE, but an
Output Data tool is a data node and is not on that list — yet it is a *write*, not a computation,
so no CTE can correspond to it. Rather than invent an `t7_output` CTE that selects from the
previous one, I used the brief's other lever: "a documented merge in `translation_notes.md` may
exempt a node — make the exemption explicit and narrow". The exemption has a fixed, machine-checked
form:

```
- tool 7 has no CTE: an Output Data tool is a write, not a computation — it is the
  `CREATE OR REPLACE TABLE … AS` statement whose `SELECT` ends at `t6_sort`.
```

The test's regex is `^-\s*tool\s+(\S+)\s+has no CTE:\s*\S`, so a note that merely *mentions* the
tool does not excuse it, and a reason is mandatory. **13b must use the same line format.**

## Patterns to reuse (for Task 13b: `wf_0003`–`wf_0005`)

### Naming and file layout

- Procedure: `MIG_WORK.<WF>_<SEG>` — `wf_0003` → `WF0003`, `seg_02` → `SEG_02`. Parameters exactly
  `SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING`,
  `RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER`, body `BEGIN` … `RETURN 'OK'; END;`.
- CTE per tool: `t<id>_<type>`, where `<type>` is the dag node's `type` verbatim. When one tool has
  several output anchors that feed different branches, add the anchor as a suffix —
  `t3_filter_t` / `t3_filter_f`, `t5_join_j` / `t5_join_l` / `t5_join_r`. The test accepts
  `t<id>_<type>` or `t<id>_<type>_<anything>`.
- Every CTE is preceded by a comment starting `-- tool <id>` (an anchor note may follow:
  `-- tool 3 (anchor F): …`). The test requires that comment for every node that has a CTE.
- A node with no CTE (an Output tool, a Browse) gets a line in `translation_notes.md` of exactly
  the form `- tool <id> has no CTE: <reason>`.
- Broken variants: `samples/<wf>/broken_sql/<seg>/NN_snake_case_name.sql`, numbered so the mock
  runner's "first in name order" is the one the brief names as the bad first attempt. Each file is
  the correct procedure with **one** edit plus a header comment that opens
  `-- BROKEN ON PURPOSE -- do not fix: this is a fixture for tests/test_e2e_parity.py.` I generated
  them with a small script that asserts the search string was found and that the result differs
  from the original — worth copying, it catches a silently-failed `str.replace`.
- Write files with LF endings. Python's `Path.write_text` on Windows produces CRLF; I had to
  normalize afterwards. Use `write_bytes` with `\n`, or fix it before committing.

### Reading sources and writing targets

```sql
FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS')                 -- mapped source
CREATE OR REPLACE TABLE IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.SALES_SUMMARY') AS …
CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0002_SEG_01_OUT AS …           -- outbound stream
FROM MIG_WORK.WF0002_SEG_01_OUT L                                           -- upstream segment
```

Logical names come from `sample.json["logical"]`. `TRANSIENT` works on the local runtime
(`backend._drop_transient` strips it) and is the right storage class for a work table. Several
`IDENTIFIER(...)` calls in one statement are fine — `proc_runner.bind` folds each one.

### The NUMBER-arithmetic idiom (the one that matters most)

The oracle computes formula arithmetic in **exact decimal** and converts to a double only when
storing into a `Float`/`Double` field. So: cast to `NUMBER(38,10)` *before* multiplying or
dividing, round the NUMBER, and cast the result back to `FLOAT` only if the Alteryx field is a
Double.

```sql
CAST(ROUND(CAST(AMOUNT AS NUMBER(38,10))
           * IFF(QTY >= 10, CAST(0.9 AS NUMBER(38,10)), CAST(1 AS NUMBER(38,10))),
           2) AS FLOAT)   AS NET
```

Both operands are cast, including the literal — otherwise DuckDB types `0.9` as `DECIMAL(2,1)` and
`1` as `INTEGER`, and a FLOAT column on the other side drags the whole product back into binary.
`DECIMAL(38,10) * DECIMAL(38,10)` yields `DECIMAL(38,20)` (verified), which is exact for money;
`ROUND(<decimal>, 2)` is half away from zero, so `2.675 → 2.68` and `-2.675 → -2.68`, matching
`Round(x, 0.01)`.

Aggregating a Double column follows the same rule — sum the exact cents, not the binary values:

```sql
CAST(SUM(CAST(NET AS NUMBER(38,10))) AS FLOAT)   AS TOTAL_NET
```

Never `ROUND(<float expr>, n)`: `ROUND(1.005::FLOAT, 2)` is `1.00` on this runtime where the exact
form gives `1.01`.

### Tool-by-tool

| Alteryx | What I wrote | Why |
|---|---|---|
| **Filter**, True | `WHERE <expr>` | SQL's three-valued `WHERE` already drops the NULL-expression rows |
| **Filter**, False | `WHERE NOT (<expr>) OR (<expr operand>) IS NULL` | Alteryx sends both false **and** NULL to `F`; the `IS NULL` half is the whole point |
| **Select**, `String(n)` retype | `LEFT(x, n) AS X` | Alteryx truncates silently; `LEFT` counts characters, so non-ASCII truncates correctly |
| **Select**, `*Unknown` selected | list the named fields first, then the unlisted ones in incoming order | that is the output order the oracle produces |
| **Select**, text → number/date | `TRY_TO_NUMBER(x, 38, 0)`, `TRY_TO_DOUBLE(x)`, `TRY_TO_DATE(x, 'YYYY-MM-DD')` | Alteryx warns and nulls |
| **Formula**, several expressions | compute the earlier field in a **nested `SELECT`**, read it in the outer one | a later expression sees the earlier one's value, and SQL cannot reference an alias in the same `SELECT`; keeps one CTE per tool |
| `ToNumber` | `TRY_TO_DOUBLE` | verified NULL for `abc`, `1,200.50` and `''` on this runtime |
| `IIF(c, a, b)` | `IFF(c, a, b)` | a NULL condition takes the else branch in both |
| `IF/ELSEIF/ELSE` | `CASE WHEN … END` | a NULL comparison is not true, so those rows reach `ELSE` |
| **Summarize** `Count` / `CountNonNull` | `COUNT(*)` / `COUNT(col)` | Alteryx's `Count` counts **rows** |
| **Sort** | `ORDER BY a DESC NULLS LAST, b ASC NULLS FIRST` | oracle: NULL first ascending, last descending |
| **Join** `J` | `FROM L JOIN R ON L.K = R.K`, projecting left fields then right fields; a colliding right field would be `Right_<name>` before the Join's own Select applies | |
| **Join** `L` / `R` | `WHERE NOT EXISTS (SELECT 1 FROM <other> O WHERE O.K = T.K)` | **never `NOT IN`** — a NULL key must leave on `L`/`R`, and `NOT IN` against a column holding a NULL drops every row |
| **Union** by name | project every branch into input #1's column list, `UNION ALL` | never `UNION`; a field a branch lacks is written `CAST(NULL AS <type>)` so the result's types come from the declaration, not from whichever branch is first |
| **Data Cleansing** | `UPPER(TRIM(COALESCE(x, '')))` | the macro's own option order: replace nulls, then trim, then case. `COALESCE` must be innermost — trimming a NULL leaves it NULL |
| **Browse** | nothing | emits nothing; document it with a `- tool <id> has no CTE:` line |
| **Output**, `Overwrite` | `CREATE OR REPLACE TABLE IDENTIFIER(…) AS <select>` | |
| **Output**, `Append Existing` | `INSERT INTO IDENTIFIER(…) (<cols>) WITH … SELECT <cols> FROM t9_union` | Alteryx maps columns to the target **by name**, so name them; `INSERT … (cols) WITH … SELECT` parses and runs on this runtime |
| **Output**, `Update; Insert if new` | `MERGE` on the confirmed keys, PreSQL and PostSQL as separate statements before and after — *not exercised in my scope; wf_0003 will need it* | |

### Contracts

- `outputs[]`: one entry per outbound stream (`kind: "work"`, `table` the literal
  `MIG_WORK.<WF>_<SEG>_OUT`, `logical: null`) and one per final Output tool (`kind: "target"`,
  `tool_id`, `logical`, `table: null`, `stream` = the stream **feeding** that Output tool).
  `output` must be a copy of `outputs[0]` — the test compares them with `==`.
- **`keys` will almost always be `[]`.** Every `edge` golden set contains byte-identical duplicate
  rows by design, so check the `edge` file before declaring a key. The one stream in my scope that
  could be keyed is wf_0001's Summarize output, where the aggregation collapses the duplicates:
  `keys: ["REGION", "SIZE_BAND"]`.
- `nullable: false` is worth declaring where it is *provably* true across all four golden sets —
  it is the only way a keyless stream can ever get a column-named diff cluster, and it is what
  makes the two extra broken variants work. I used it for wf_0001's group-by columns and for
  wf_0002's cleansed `NAME`/`CITY` and the union's `MATCH_FLAG`. Check every set first: a NULL in
  the *golden* data of a `NOT NULL` column is a `GOLDEN_DATA` cluster and fails the correct
  procedure.
- `inputs[]`: a mapped source carries `logical` (plus `columns`, `keys`); an input from an upstream
  segment carries `from`, `stream` and the literal `table`.
- `ordering.order_dependent_columns` must be present even when empty. `wf_0003` has a Record ID /
  Unique, so it will be non-empty there — and those columns then get the `ORDERING` classification
  path in `compare.py`.

### The fastest loop

1. Write `samples/<wf>/canned/segments/<seg>/{contract.json,proc.sql}`.
2. `.venv/Scripts/python.exe -m pytest tests/test_e2e_parity.py -k wf_0003 -q`.
3. For hands-on debugging, build a scratch root **outside** the repo the way
   `tests/helpers.prepare_workflow` does (I kept a 90-line `prep.py` that does exactly that), then
   `python scripts/validate_segment.py <wf> <seg> [--set NAME] [--proc FILE] --root <scratch>` and
   read `segments/<seg>/validation.<set>.json` — `diff_clusters[].suspect_cte` points straight at
   the tool.
4. `python scripts/compile_check.py <wf> <seg> --root <scratch>` first: it is much faster than
   parity and catches the signature and SQL-shape mistakes.
5. A throwaway "run this Snowflake statement through the DuckDB backend and print the translation"
   script pays for itself immediately — `backend.translate(sql)` shows you what DuckDB will
   actually execute, which is how I found that `TRY_TO_DATE(x, fmt)` loses its format and that
   `IFF(c, 0.9, 1)` types as `DECIMAL`.

### Local-runtime facts that forced a particular spelling

- `TRY_TO_DOUBLE` → `TRY_CAST(… AS DOUBLE)`: NULL for `abc`, `1,200.50`, `''`; **but it accepts
  `inf` and `nan`**, where the oracle gives NULL. No golden set contains either; I recorded it as
  an assumption instead of guarding it. If 13b's data has one, build the rule explicitly with a
  `REGEXP_LIKE` guard.
- `TRY_TO_DATE(x, 'YYYY-MM-DD')` → `TRY_CAST(… AS DATE)`: the **format argument is dropped**. Fine
  for ISO text, wrong for anything else — say so in the notes.
- `TRY_TO_NUMBER(x, 38, 0)` rounds half away from zero (`'12.7'` → `13`), which matches Alteryx's
  integer coercion.
- `ROUND(<double>, 2)` on this runtime is *not* reliably binary-faithful (`1.005` → `1.00` but
  `2.675` → `2.68`), so never reason about a FLOAT rounding difference from first principles —
  measure it.
- `CAST(<double> AS NUMBER(38,10))` reproduces the oracle's `Decimal(repr(x))` for values with up
  to ten decimal places, which covers all the sample data.
- `ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;` is recorded by `proc_runner`
  and never executed, and it is removed before sqlglot sees the statement, so it is free.
- `RUN_ID` goes unused in a segment with no query tag or audit row. That is fine — C4 fixes the
  parameter list — but say so in the notes so a reviewer does not flag it.

## Commits

| SHA | Subject |
|---|---|
| `c952d48` | `wip: hand-migrated procedures, contracts and broken variants for wf_0001 and wf_0002` |
| `660eee2` | `wip: canned agent artifacts for wf_0001/wf_0002 and the canned-artifact test (in progress)` |
| `2521b81` | `wip: tests/test_canned_artifacts.py green; documented no-CTE exemptions for Output/Browse tools` |
| `6ba8c7c` | `wip: docstring tidy in tests/test_canned_artifacts.py` |
| `f9d54d1` | `feat: hand-migrated sample procedures, contracts, broken variants and canned agent outputs` |

The session was killed by a usage limit after `c952d48`; the three `wip:` commits above it are the
recovery. They were **not** squashed: `da88fd9` (another agent's Task 10 merge) sits between
`c952d48` and them, and the worktree `wt/task-10-fix4` points at `6ba8c7c`, so rewriting that
history would have diverged a branch someone else holds.

## Files changed

New:
- `samples/wf_0001/canned/{intake/plan.md, analysis.md, unsupported.json, docs/migration.md}`
- `samples/wf_0001/canned/segments/seg_01/{contract.json, proc.sql, translation_notes.md, review.json}`
- `samples/wf_0001/broken_sql/broken.json`, `samples/wf_0001/broken_sql/seg_01/0{1,2,3,4}_*.sql`
- `samples/wf_0002/canned/{intake/plan.md, analysis.md, unsupported.json, docs/migration.md}`
- `samples/wf_0002/canned/segments/seg_0{1,2,3}/{contract.json, proc.sql, translation_notes.md, review.json}`
- `samples/wf_0002/broken_sql/broken.json`, `samples/wf_0002/broken_sql/seg_01/0{1,2}_*.sql`,
  `samples/wf_0002/broken_sql/seg_03/01_*.sql`
- `tests/test_canned_artifacts.py`

Modified:
- `tests/test_e2e_parity.py` — one line, `list(broken_cases())`.

Nothing under `scripts/`, `orchestrator/`, `catalog/`, `mappings/` or any golden data was touched.

## Self-review findings and concerns

1. **The classification gap above is the headline concern.** Three of five required variants fail
   for the right reason but under class `UNKNOWN`. Detection is intact; attribution is not.
2. **wf_0001's procedure repeats tools 1–3 in both statements.** Two Output tools terminate two
   branches of one chain and C4 allows no variable to hold a shared result. The alternative — an
   undeclared scratch table in `MIG_WORK` — seemed worse than documented duplication. It is called
   out in `translation_notes.md` and in the migration doc so a reviewer does not read it as two
   different translations.
3. **I did not write a mechanical test for "the canned prose contains no number about data and no
   claim that anything ran on Snowflake or Alteryx."** A phrase blocklist would give false
   confidence and would fire on legitimate text. I wrote the artifacts to that rule by hand — every
   verdict and count in `docs/migration.md` is a pointer to `validation*.json` rather than a value —
   and I am flagging it as something a human reviewer should read rather than a test.
4. **`nullable: false` is a deliberate risk I took on five columns.** I checked all four golden
   sets for each, but it is the one contract claim that could fail later on new data.
5. `wf_0003` will be the first segment needing `MERGE` (`Update; Insert if new`) plus PreSQL and
   PostSQL, which my scope never exercised, so the patterns above say nothing tested about it.
