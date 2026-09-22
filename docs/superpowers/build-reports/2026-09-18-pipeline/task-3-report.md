# Task 3 report — sample workflow sources and golden inputs

## What I implemented

Five hand-written synthetic Alteryx workflows, one supporting macro, their golden input sets and
the well-formedness test that guards them. Nothing here has been produced or opened by Alteryx;
every source file follows `docs/reference/dag-contract.md` §2–§5 and the document skeleton in
program spec §6.1.

### Sources

| Workflow | File | Engine | Nodes | Notable shape |
|---|---|---|---|---|
| wf_0001 | `sales_summary.yxmd` | AMP (`RunE2 True`) | 8 | input → select → filter → formula → summarize → sort → yxdb out; filter `False` → csv out |
| wf_0002 | `customer_orders.yxmd` | E1 | 15 | containers 100, 200, nested 210, TextBox 300; join L/J/R → three formulas → union `#1/#2/#3` → DB out + browse |
| wf_0003 | `gl_period_close.yxmd` | AMP | 12 | containers 100 "Extract" (1–3) and 200 "Publish" (9–10); constants `User.Region`, `User.PeriodEnd` |
| wf_0004 | `inventory.yxmd` + `Supporting_Macros/clean_codes.yxmc` | E1 | 7 + 6 | macro anchors `Input1`/`Output5`; cross tab with frozen headers → transpose |
| wf_0005 | `vendor_dedupe.yxmd` | E1 | 4 | unknown plugin `AcmeAnalytics.Dedupe.DedupeTool` + `run_command`, `expected_terminal: MANUAL` |

Every tool id, field name, type, size, scale, anchor name and config value follows the brief
verbatim. Every data tool carries a `MetaInfo`/`RecordInfo` for each of its out anchors, computed
through the chain: select reordering under `*Unknown`, formula appends, join `Right_` prefixing and
the `J` deselect, summarize renames and result types (`Count*` → `Int64`, `Sum` of `FixedDecimal`
stays `FixedDecimal`), regex parse fields, the cross tab's frozen `EAST, NORTH, WEST`, and the
transpose's `Name`/`Value`.

### Golden inputs

`samples/wf_000N/golden_inputs/<set>/<tool_id>.csv` + `.schema.json` for `normal`, `period_end`,
`empty`, `edge` (wf_0005: `normal`, `empty` only), plus
`golden_inputs/targets_before/<set>/{CUSTOMER_ORDER_FACT,GL_SUMMARY}.csv` for the two non-overwrite
outputs. 34 CSVs in total, all written through `lib.typed_csv.write_table`, so contract C1 holds by
construction (`\N` for NULL, plain decimal floats, LF, RFC 4180 quoting, schema sidecar). No `\N`
escape was hand-typed. Only inputs are authored; expected outputs come from task 8's simulator.

`samples/_tools/make_golden_inputs.py` holds the row data and also writes the five `sample.json`
files through `lib.io.write_json`. I kept it (the dispatch allowed `samples/_tools/`) because the
rows are a designed artifact — each one exists to exercise a named behaviour — and hand-editing 34
CSV/sidecar pairs consistently would be a silent-drift hazard. The test pins the CSV schemas to the
XML `MetaInfo`, so the two cannot diverge unnoticed.

`samples/wf_000N/README.md` documents, per set, which row exercises which behaviour (e.g. wf_0001
`ORDER_ID 5`: NULL `REGION` → filter expression NULL → must reach tool 8).

### sample.json

All five carry `id, title, owner: wf_owner, schedule: "0 6 * * 1-5",
segmentation: {min_tools: 3, max_tools: 40}, expected_terminal, answers, logical`.
`answers` uses the C8 normalized key where it is unique within the workflow
(`sales/orders.yxdb`, `crm/customers.yxdb`, `alias:dw_sales`, …). wf_0003 is the exception: its
input and output both normalize to `alias:prod_fin`, so it answers by **tool id** — which is why the
brief's shape allows "normalized key **or** tool id". Every FQN uses a table the task-12 catalog is
specified to contain (`SALES.RAW.ORDERS`, `CRM.RAW.CUSTOMERS`, `FINANCE.RAW.GL_LEDGER`,
`ANALYTICS.CURATED.GL_SUMMARY`, `WH.RAW.STOCK`, `VENDOR.RAW.ACCOUNTS`, …).

## What I tested and the results

`tests/test_samples_wellformed.py` — 67 tests (parametrized over the five workflows).

The brief's Step 1 checks: every `source/**/*.yx*` parses with `xml.etree`; root is
`AlteryxDocument` with `yxmdVer`; ToolIDs unique per file (including nested `<ChildNodes>`); every
`<Connection>` `Origin`/`Destination` names a node that exists and a non-empty anchor;
`sample.json` has exactly the planned keys; every golden input CSV loads with
`typed_csv.read_table` and its `(name, type, size, scale)` list equals its input tool's
`MetaInfo`; `empty` sets have a header and zero rows.

Added for behaviour the brief describes in prose but does not test:

- node count per workflow matches the plan (wf_0003 is 12, wf_0002 is 15, …)
- every anchor a connection leaves from has a matching `MetaInfo connection=` block
- output tools carry the schema they write (intake reads it when the file does not exist)
- `logical` covers exactly the input and output tools, names are unique, `answers` has one entry
  per touchpoint and every value is a three-part FQN
- `targets_before` exists for exactly the `append`/`update_insert` outputs and for no others
- the only credential-shaped strings are the four fake placeholders, and the two workflows that
  exist to exercise the scrubber really do carry them
- the data-cleansing and macro nodes have **no** `Plugin` attribute and the expected
  `EngineSettings Macro=` value; the referenced `.yxmc` is present
- union destinations carry `Connection="Input"` with `name="#1"/"#2"/"#3"`
- golden CSVs are UTF-8 with no CR
- every workflow has a non-empty README

### TDD evidence

**RED** — `.venv/Scripts/python.exe -m pytest tests/test_samples_wellformed.py`, before authoring:

```
FFFFFF..........FFFFFFFFFF.....FFFFF.....FFFFFFFFFF.FF.......FFFF.F      [100%]
>       assert sorted(p.name for p in SAMPLES.glob("wf_*") if p.is_dir()) == WORKFLOWS
E       AssertionError: assert [] == ['wf_0001', '...4', 'wf_0005']
...
38 failed, 29 passed in 0.86s
```

Expected: no `samples/` directory existed, so every check that touches a file failed. The 29
passes are parametrizations that are vacuous without data (e.g. "no `targets_before` directory
exists" is trivially true for wf_0001/4/5 when nothing exists at all) — which is why the suite was
re-run after authoring rather than trusted at this point.

**GREEN** — same command, after authoring:

```
...................................................................      [100%]
67 passed in 0.11s
```

**Full suite** — `.venv/Scripts/python.exe -m pytest`:

```
........................................................................ [ 69%]
...............................                                          [100%]
103 passed in 0.24s
```

Output is pristine: no warnings, no stderr.

### Checks beyond the test file

Ad-hoc verification (not committed) that the XML parses into the values the contract expects:

- the DB input's `<File>` text concatenates across the CDATA boundary into
  `odbc:DSN=PROD_FIN;UID=svc_alx;PWD=__EncPwd2__|||SELECT … WHERE AMOUNT <> 0`, so
  `split("|||")` yields the connection string and the query verbatim
- `PreSQL`, `PostSQL`, `UpdateKeys` (`ACCT`, `PERIOD`), `write_mode` source text and
  `dbo.GL_SUMMARY` all read back correctly; constants are
  `{"User.Region": "EMEA", "User.PeriodEnd": "2026-08-31"}`; `RunE2` is `True`
- formula and filter expressions round-trip unescaped, e.g.
  `Round([AMOUNT] * IIF([QTY] >= 10, 0.9, 1), 0.01)` and
  `IF [AMOUNT] >= 1000 THEN "LARGE" ELSEIF … ENDIF`; the macro's regex reads back as
  `^\s*([A-Za-z]+)[- ]?(\d+)\s*$` → `$1-$2`, matching the plan's own formula test case
- all six source files are pure ASCII, so the encoding-free `<?xml version="1.0"?>` declaration is
  unambiguous; the non-ASCII lives in the `edge` CSVs where it belongs
- the three half-cent rows were hand-checked with `Decimal(repr(x))` half-away-from-zero, the rule
  task 8 will implement: `250.005 → 250.01`, `33.35 × 0.9 = 30.015 → 30.02`,
  `2500.75 × 0.9 = 2250.675 → 2250.68`. The middle one is the plan's own worked example
  (`03-simulator-samples.md` `test_evaluate`), which is why I chose 33.35 for `ORDER_ID 3`.

I also traced wf_0003's `normal` set by hand through filter → datetime → sort → unique →
multi-row → summarize: 11 rows survive the `EMEA` filter, 9 reach `Unique` (entries 1 and 11 are
the duplicates), and the summarize produces 6 `(ACCT, PERIOD)` groups, one of which
(`4000` / `2026-08`) matches a `targets_before` row so the merge updates it.

## Files changed

Created:

- `tests/test_samples_wellformed.py`
- `samples/_tools/make_golden_inputs.py`
- `samples/wf_0001/{source/sales_summary.yxmd, sample.json, README.md}`
- `samples/wf_0002/{source/customer_orders.yxmd, sample.json, README.md}`
- `samples/wf_0003/{source/gl_period_close.yxmd, sample.json, README.md}`
- `samples/wf_0004/{source/inventory.yxmd, source/Supporting_Macros/clean_codes.yxmc, sample.json, README.md}`
- `samples/wf_0005/{source/vendor_dedupe.yxmd, sample.json, README.md}`
- 34 `golden_inputs/**/*.csv` with their `.schema.json` sidecars

Not touched: `docs/superpowers/plans/2026-09-18-pipeline/01-foundations-parser.md` carries an
uncommitted edit from task 2 (the LZF control-byte correction). It is not mine and I left it
unstaged.

## Decisions a later task may need to know

These are places where the dag contract left a choice and I picked one. None contradict the brief;
all are worth a glance from the task-4 parser author.

1. **`PreSQL`, `PostSQL`, `UpdateKeys` are direct children of `<Configuration>`**, siblings of
   `<File>` and `<FormatSpecificOptions>` — the contract lists them alongside
   `<FormatSpecificOptions><OutputOption>` rather than inside it, and `UpdateKeys` is explicitly
   "our simplification". Real Alteryx nests PreSQL/PostSQL inside `FormatSpecificOptions`, so a
   parser that searches with `.//` is robust either way; one that uses
   `configuration.find("PreSQL")` will work against these samples.
2. **`OutputOption` is inside `<FormatSpecificOptions>`** for every output including file outputs,
   where I wrote `Overwrite` explicitly rather than relying on the contract's default.
3. **Output and browse tools carry `<MetaInfo connection="Output">`** holding the schema they
   receive. They have no out anchor, but intake's `field_source: "meta"` path needs a field list
   for outputs (the target file does not exist yet), and real Alteryx writes `Output` on BrowseV2.
   A parser that only keys `meta` by declared out anchors would drop these.
4. **Summarize's `Count` is bound to a field** (`ORDER_ID` in wf_0001, `ENTRY_ID` in wf_0003), as
   Alteryx's picker requires; the action still counts rows.
5. **The DB input's `MetaInfo` `source` attribute is `ODBC: PROD_FIN`**, deliberately without
   credentials, so the fake password never appears in a field's provenance and the task-4 assertion
   `"svc_alx" not in str(dag)` cannot be defeated by `meta`.
6. **Transpose's `Name` is `V_String(255)`** and `Value` is `Double(8)` (all three data fields are
   `Double`). The contract fixes the column names, not their types.
7. **`EngineDll` for Summarize is `AlteryxSpatialPluginsEngine.dll`** (entry point
   `AlteryxSummarize`), matching its `AlteryxSpatialPluginsGui` plugin, rather than the
   `AlteryxBasePluginsEngine.dll` the brief's generic skeleton line shows.
8. **`samples/_tools/` sits beside `samples/wf_*`.** Task 9's `build_samples.py` must iterate
   `samples/wf_*`, not every subdirectory of `samples/`. The underscore prefix and
   `test_samples_are_exactly_the_five_planned_workflows` are the guards.

## Concerns

1. **wf_0003 cannot have a NULL or zero `AMOUNT` in any set.** The input tool's embedded SQL ends
   in `WHERE AMOUNT <> 0`; the simulator does not execute that query (`input` just emits its golden
   table) while the translated procedure re-applies the predicate (`05-…md`: "the embedded input
   SQL's `WHERE` is re-applied in `t1_input`"). A zero or NULL `AMOUNT` row would therefore make
   the two sides disagree for a reason that is not a bug. So the `edge` set's "NULL in every
   column" holds for five of its six columns, and `AMOUNT` is the documented exception. This is
   recorded in `samples/wf_0003/README.md` as well. If a later task wants that NULL, the fix is to
   drop the `WHERE` from the sample's query, not to add the row.
2. **wf_0004's `WAREHOUSE` never takes a value outside `{EAST, NORTH, WEST}`, and its NULL only
   appears on rows the macro's filter removes.** The cross tab's header list is frozen in its
   `MetaInfo`, and the contract does not say what a header value outside the frozen list should do.
   Rather than encode a guess into the parity oracle's input, I kept such values out. Same
   reasoning as above: if task 8 decides the semantics, a row can be added then.
3. **wf_0004 has no date column**, so its `period_end` set cannot contain period-end dates; it is a
   month-end stock count instead. The four-set layout is still uniform, which is what tasks 6–9
   iterate over.
4. **wf_0002 collapses to a single segment at the sample's `min_tools: 3`** (containers 100 and
   210 hold only two data tools each, so both merge across their soft cuts). That is the brief's
   prescribed `segmentation` value and task 5's test passes `min_tools=2` explicitly to see the
   container split, so this is consistent — but if a later task wants wf_0002 to exercise
   multi-segment orchestration end to end, `sample.json` would need `min_tools: 2`.
5. **No sample exercises a wireless connection** (`"wireless": false` is in the edge contract). The
   brief does not ask for one and adding it would change segmentation and simulation semantics in a
   way no task specifies. Flagging it as a contract surface with no sample coverage.
6. **These files have never been opened by Alteryx Designer.** Attribute spellings, engine DLL
   entry points and the exact `<Properties>` block are reconstructed from the contract and from
   public knowledge of the format. If a real `.yxmd` ever becomes available, the shapes most likely
   to differ are the ones in "Decisions" above.

## Self-review findings

- Caught and fixed a stray non-ASCII character (`ақ`) that landed in `inventory.yxmd`'s tool 4
  `</Configuration>` line during authoring. It would have broken nothing (the file still parsed as
  text) but it corrupted the element indentation and was not valid tool XML in spirit; the
  well-formedness test would not have caught it, so I re-read the file rather than trusting the
  green run.
- Initially described `ORDER_ID 19` (`888.88 × 0.9 = 799.992`) as a half-cent case in the wf_0001
  README. It is not. Recomputed all candidates and corrected the README to the three rows that
  really land on `.005`.
- Reconsidered putting NULL/zero `AMOUNT` in wf_0003 and a NULL `WAREHOUSE` in wf_0004's cross tab
  path; both are recorded as concerns above rather than silently included.

---

## Follow-up 1 — wf_0002 segments at `min_tools: 2` (coordinator ruling on concern 4)

The coordinator ruled the collapse a plan defect: wf_0002 exists to demonstrate container cuts and
a parallel segment wave, so it now carries `"segmentation": {"min_tools": 2, "max_tools": 40}`.
The other four workflows stay at 3.

Changed:

- `samples/_tools/make_golden_inputs.py` — wf_0002's `segmentation`, with a comment saying why this
  one sample differs (its two prep containers hold two data tools each, so a floor of 3 merges both
  away). `sample.json` is regenerated from here, never hand-edited.
- `samples/wf_0002/sample.json` — regenerated.
- `samples/wf_0002/README.md` — new "Segmentation this sample is shaped for" section stating the
  expected groups {1, 2}, {3, 4}, {5…11}, why 3 would collapse them, and that Browse 11 rides with
  its upstream group without counting toward a size.
- `tests/test_samples_wellformed.py` — `test_sample_json_has_the_planned_keys` now checks the
  segmentation block generically: both keys present, both real `int`s (`bool` excluded, since
  `isinstance(True, int)` is true in Python), and `0 < min_tools <= max_tools`. It never asserted a
  fixed number, so nothing had to be unpinned.

Regenerating rewrote all 34 golden CSVs and all five `sample.json` files; `git status` showed only
the four intended files as modified, which also confirms the generator is byte-stable — the
property task 9's test (e) depends on.

Hand-check of the new segmentation against task 5's algorithm: the soft cuts at 2→5 and 4→5 (the
outermost containers differ) leave groups {1, 2}, {3, 4} and {5…11}; there are no
`ORDER_DEPENDENT_TYPES` nodes and no `First`/`Last` summarize, so ordering protection unions
nothing; at `min_tools: 2` no group is under the floor, so none merges. Browse 11 joins tool 9's
group and does not count, making the third group 6 data nodes. That is exactly what
`test_containers_cut_and_waves_are_topological` in the task 5 brief asserts, including
`order == [sorted([o["1"], o["3"]]), [o["5"]]]`.

wf_0003's README already carried the post-query `AMOUNT` constraint as its own section, led by the
sentence "The input tool's own SQL ends in `WHERE AMOUNT <> 0`, and the translated procedure
re-applies that predicate, so **no set may contain a row whose `AMOUNT` is 0 or NULL**". Left as is.

`docs/reference/dag-contract.md` was not touched. Its new text (commit `1b7be6d`) matches the
samples as authored: PreSQL/PostSQL/UpdateKeys as direct `<Configuration>` children, sink-tool
`MetaInfo connection="Output"`, the Union `name="#N"` attribute, and the `RunE2` flag anywhere
under `<Properties>`.

### Commands and output

```
$ .venv/Scripts/python.exe -m pytest tests/test_samples_wellformed.py
...................................................................      [100%]
67 passed in 0.17s

$ .venv/Scripts/python.exe -m pytest
........................................................................ [ 69%]
...............................                                          [100%]
103 passed in 0.26s
```

Output pristine: no warnings, no stderr.
