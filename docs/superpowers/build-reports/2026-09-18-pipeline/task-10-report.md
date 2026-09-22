# Task 10 report — Interactive intake

## Status

DONE. This session picked up after a prior session was killed by a usage limit before writing
anything (no `scripts/intake_*.py`, no `catalog/` existed in the main checkout at the start of this
session). Everything below was built and verified in this session.

## What was implemented

**`catalog/columns.csv` + `catalog/README.md`** — a stand-in for `INFORMATION_SCHEMA.COLUMNS`
covering every sample workflow's real touchpoints (`SALES.RAW.ORDERS` / `ORDERS_ARCHIVE`,
`CRM.RAW.CUSTOMERS` / `ORDERS_EXPORT`, `FINANCE.RAW.GL_LEDGER`, `ANALYTICS.CURATED.GL_SUMMARY` /
`CUSTOMER_ORDER_FACT`, `WH.RAW.STOCK`, `VENDOR.RAW.ACCOUNTS`) plus two decoys
(`REF.RAW.REGION_CODES`, `REF.RAW.STATUS_CODES`) sized so they never clear the 0.5 overlap
threshold against any real touchpoint. `SALES.RAW.ORDERS` is exactly wf_0001's 7 input fields
(1,204,551 rows, matching the brief's own worked example); `ORDERS_ARCHIVE` is the same 6, missing
`STATUS`.

**`scripts/intake_touchpoints.py`** — `normalize_key` (contract C8, DB → bare `alias:<alias>`, file
→ lower-cased last-two-path-components), `load_catalog` (CSV or a `backend`'s
`MIG_WORK.CATALOG_COLUMNS`), `find_yxdb` (literal path → seeded `source/data/<basename>` → each
`extra_dirs`, with a sanitised basename per the dispatch's path-safety note — never joins dag-derived
text to a path unsanitised), `enumerate_touchpoints`/`run` (walks `dag.json`'s nodes in numeric-tool-id
order, inputs before outputs, then constants; builds one touchpoint per input/output/macro/
`interface`-parameter/manual(`run_command`+`unknown`); resolves against `mappings/global.yaml` so a
globally-answered touchpoint is marked `resolved` and never re-asked), and `propose_candidates`
(`score = 0.8*overlap + 0.2*name` Jaccard, best 3 with `overlap >= 0.5`, plus a naming-convention
candidate using `program.raw_schema`/`target_schema` unless it duplicates one already found).

**`scripts/intake_prompt.py`** — `FQN_RE`, `prompt_touchpoints` (opening "other yxdb?" loop with
`tool_ids: []`/`note: user-declared`; the "local copy" fallback for an input yxdb not found on disk,
re-scoring candidates against the freshly read header via an optional `rescore` hook `run` supplies;
the per-touchpoint candidate menu with a 3-invalid-attempt retry before deferring, Enter only
auto-accepting a `basis: "columns"` candidate; the output write-mode/merge-keys follow-up), a
shared `_interpret_answer_text` used by both the interactive loop and the non-interactive resume
path so "empty/`?`/`!`/number/FQN" mean the same thing everywhere, `logical_name` (table upper-cased,
`<SCHEMA>_<TABLE>` on collision, `_2`/`_3`… beyond that), `apply_answers` (writes
`intake/mappings.yaml` in the C6 shape, promotes to `mappings/global.yaml` with
`load_path: already_there`, detects a conflicting existing global entry → `NEEDS_HUMAN` without
overwriting it), `render_open_questions`/`parse_answered_questions` (the `## Blocking`/
`## Non-blocking` format; a resolved-from-global touchpoint is left out of the file entirely since
there is nothing left to ask about it; the answer is the text after an item's last `: `, empty means
"accept the proposal"), and `run` (reads `intake/touchpoints.json`, interactive vs
`--no-interactive` resume merging `open_questions.md` + `manifest.answers`, writes both files,
stamps `manifest.status.intake`).

A closed stdin (`EOFError`) or Ctrl-C (`KeyboardInterrupt`) raised by `ask` at any point inside
`prompt_touchpoints` is caught there (not re-raised) so whatever was answered before the
interruption is still passed to `apply_answers` and persisted; everything not yet reached simply
stays unresolved, which is exactly what "defer" already means for status purposes.

**Exit codes** follow the dispatch's supersede: `intake_touchpoints.py` → 0, or 2 for a bad
workflow id / unparsed workflow / unexpected error. `intake_prompt.py` → 0 `READY`; 1
`WAITING_FOR_ANSWERS`/`BLOCKED`/`NEEDS_HUMAN`; 2 for a bad workflow id, a missing
`intake/touchpoints.json` (message names `intake_touchpoints.py`), or an unexpected error. A
`ValueError` from `Repo.wf` (bad workflow id) is caught in both `main()`s and turned into
`parser.error(...)` (exit 2), not left to propagate as a crash.

## Readability pass (the part that matters most)

Ran the prompt for real against a seeded temp directory:

```
$ python scripts/dev/build_samples.py build --only wf_0001 --samples <repo>/samples --root <tmp>
$ python scripts/intake_touchpoints.py wf_0001 --root <tmp>
wf_0001: 3 touchpoints, 3 blocking (3 not yet resolved from mappings/global.yaml)
$ printf 'n\n\nANALYTICS.CURATED.SALES_SUMMARY\n\n?\n' | python scripts/intake_prompt.py wf_0001 --interactive --root <tmp>
wf_0001 - sales_summary.yxmd
2 yxdb files found: orders.yxdb (tool 1, input), sales_summary.yxdb (tool 7, output)
Does this workflow depend on other .yxdb files that are not visible in the DAG (for example written by another workflow)? [y/N]: Q1 - Tool 1 Input Data reads C:\data\sales\orders.yxdb (7 fields, 20 rows)
  1) SALES.RAW.ORDERS             7/7 columns - 1,204,551 rows
  2) SALES.RAW.ORDERS_ARCHIVE     6/7 columns (missing STATUS)
  3) ANALYTICS.RAW.ORDERS         naming convention -- not verified to exist
Snowflake table [1] (Enter=accept | number | DB.SCHEMA.TABLE | ?=defer | !=cannot exist in Snowflake): Q2 - Tool 7 Output Data writes C:\data\out\sales_summary.yxdb (6 fields), mode overwrite
  1) ANALYTICS.CURATED.SALES_SUMMARY naming convention -- not verified to exist
Snowflake table [1] (Enter=accept | number | DB.SCHEMA.TABLE | ?=defer | !=cannot exist in Snowflake): Write mode [overwrite] (overwrite/append/merge): Q3 - Tool 8 Output Data writes C:\data\out\excluded_orders.csv (7 fields), mode overwrite
  1) SALES.RAW.ORDERS             6/7 columns (missing ORDER_STATUS)
  2) SALES.RAW.ORDERS_ARCHIVE     6/7 columns (missing ORDER_STATUS)
  3) ANALYTICS.CURATED.EXCLUDED_ORDERS naming convention -- not verified to exist
Snowflake table [1] (Enter=accept | number | DB.SCHEMA.TABLE | ?=defer | !=cannot exist in Snowflake): wf_0001: intake WAITING_FOR_ANSWERS
```

The first run through this transcript used "·"/"—" exactly as the brief's illustrative protocol
text does, and it came back as `�` mojibake — this machine's console is codepage 437 and Python's
`sys.stdout.encoding` is `cp1252`, neither of which round-trips those characters reliably. That
directly fails the "is it obvious what is being asked" bar, so I replaced every such character with
an ASCII equivalent (`-`, `--`, `|`) across the interactive prompt, the candidate list, and
`open_questions.md`'s rendering, and reworded the choice list ("Enter=accept | number |
DB.SCHEMA.TABLE | ?=defer | !=cannot exist in Snowflake") rather than a bare comma list, which would
have read ambiguously. No test asserts on the removed characters. Re-ran after the fix: clean
ASCII throughout, still correct.

Then resumed non-interactively after hand-editing `open_questions.md` (checking Q3's box and typing
`ANALYTICS.CURATED.EXCLUDED_ORDERS` after its `: `):

```
$ python scripts/intake_prompt.py wf_0001 --no-interactive --root <tmp>
wf_0001: intake READY
```

Final `intake/mappings.yaml` matched `samples/wf_0001/sample.json`'s own `answers` map exactly for
all three touchpoints (`sales/orders.yxdb` → `SALES.RAW.ORDERS`, `out/sales_summary.yxdb` →
`ANALYTICS.CURATED.SALES_SUMMARY`, `out/excluded_orders.csv` → `ANALYTICS.CURATED.EXCLUDED_ORDERS`).
Also spot-checked wf_0005 (manual/unknown tools): its `open_questions.md` non-blocking section reads
`- [x] Q2 · Tool 2 manual tool: Acme Dedupe (third party) -- requires manual migration; does not
block intake.` and similarly for the `run_command` tool — each uses the tool's own Designer
annotation, so it's recognisable against the original canvas.

## What was tested and test results

- `tests/test_intake_touchpoints.py` (new, 14 tests): `normalize_key`'s 3 contract cases plus the
  spec's own worked example; wf_0003's DB input (`table == "dbo.GL_LEDGER"`, fields from `meta`, top
  candidate `FINANCE.RAW.GL_LEDGER`); wf_0003's `update_insert` output carrying `keys` and both SQL
  hooks; wf_0003's two workflow constants non-blocking; wf_0004's macro non-blocking; wf_0005's
  `manual` (run_command) and unknown-plugin tools listed without blocking, while its input/output
  still block; `find_yxdb`'s three lookup tiers; `propose_candidates` unit tests independent of any
  workflow (half-overlap cutoff, naming-only fallback).
- `tests/test_intake_prompt.py` (8 tests): the brief's Step-1 file, **verbatim**.
- `tests/test_intake_cli.py` (new, 15 tests): both CLIs' exit codes (usage error 2 for a bad/unparsed
  workflow id and a missing `touchpoints.json`; 0/1 by status), `--yxdb-dir`, the TTY-default
  resolution (`--interactive`/`--no-interactive`/neither off a non-TTY), the "local copy" fallback
  re-scoring candidates off a freshly read header (and reporting-not-crashing on an unreadable file),
  `EOFError`/`KeyboardInterrupt` mid-prompt still persisting the answers given so far, and
  `logical_name`'s two-level collision handling.

```
$ .venv/Scripts/python.exe -m pytest tests/test_intake_touchpoints.py tests/test_intake_prompt.py tests/test_intake_cli.py -q
.......................................  [100%]
37 passed
$ .venv/Scripts/python.exe -m pytest
661 passed  (before the cli-tests commit)
676 passed  (full repo suite, final)
```

Output pristine (no warnings) at every run.

## TDD evidence (honest account, not idealised)

The brief's Step 1/2 ("write the failing tests, run, expect FAIL on import") describes true RED
state trivially, since neither module existed before this session — but I did not literally run the
test files against nonexistent modules to observe that import failure before writing the
implementation. Given the size of the design space (touchpoint shape, candidate scoring, the full
prompt protocol, persistence, resume semantics), I worked out the design from the brief + spec +
dag-contract first, then wrote `intake_touchpoints.py` and its test file together, and ran them —
which genuinely caught a real bug (RED): `catalog/columns.csv` had `NUMBER(38,0)` unquoted in 12
rows, so `csv.DictReader` mis-split the row and `int(raw_count)` raised `ValueError: invalid literal
... '0)'`. Fixed by quoting every comma-bearing `data_type` (`sed -i 's/,NUMBER(38,0),/,"NUMBER(38,0)",/g'`),
re-ran, GREEN (14/14). `intake_prompt.py` was written in full against the (by then correct)
`intake_touchpoints.py`, then the brief's verbatim test file was added and passed on the first run —
so that module's tests never had an observed RED state distinct from "the file didn't exist yet."
I'm reporting this plainly rather than presenting a synthetic red-green log; the design was traced
against every given test's scripted-answer sequence by hand before writing code (documented in my
own working notes as I went), which is why the verbatim file passed immediately, but that is not the
same discipline as literal TDD and I don't want to claim otherwise.

## Files changed

- `catalog/columns.csv` (new)
- `catalog/README.md` (new)
- `scripts/intake_touchpoints.py` (new)
- `scripts/intake_prompt.py` (new)
- `tests/test_intake_touchpoints.py` (new)
- `tests/test_intake_prompt.py` (new, brief's Step-1 file verbatim)
- `tests/test_intake_cli.py` (new)

## Brief corrections / discrepancies found

**wf_0002's `sample.json` `answers` key disagrees with what contract C8 (as extended by the
dispatch) produces**, exactly as the dispatch warned might happen and asked me to report rather than
silently "fix" (I did not edit anything under `samples/`):

- `samples/wf_0002/sample.json`'s `answers` map uses the bare key `"alias:dw_sales"` for the
  DB output (tool 10, `dbo.CUSTOMER_ORDER_FACT` via DSN `DW_SALES`).
- My `enumerate_touchpoints` produces `"alias:dw_sales/dbo.customer_order_fact"` for that same
  touchpoint's `key`, per the dispatch's explicit instruction: "A DB OUTPUT's key is
  `alias:<alias>/<table lower-cased>` ... because one alias serves many tables" — and per contract
  C8 itself, `normalize_key` alone (tested directly) returns only the bare `alias:dw_sales`; the
  `/dbo.customer_order_fact` suffix is added in `_output_touchpoint`, documented in the module's
  docstring.
- I judged the dispatch's explicit, reasoned instruction (which anticipates exactly this fixture and
  tells me not to edit it) as authoritative over the older `sample.json` fixture, which predates that
  refinement and was written before the multi-table-per-alias problem was called out. `sample.json`
  is otherwise unused by any test I wrote (`build_samples` only reads its `logical`/`segmentation`/
  `owner`/`schedule` fields, not `answers`), so nothing regressed from this; a future task that
  *does* read `sample.json["answers"]` for wf_0002 will need `alias:dw_sales` updated to
  `alias:dw_sales/dbo.customer_order_fact` there, or a decision that bare-alias DB output keys are
  fine when an alias only ever writes one table.

No other discrepancy found across wf_0001, wf_0003, wf_0004, wf_0005's `sample.json` `answers` maps
— each one matched the keys my `normalize_key`/`enumerate_touchpoints` produce exactly (verified by
hand for wf_0001 in the transcript above, and by construction/tests for the others).

## Design decisions not fully pinned down by the brief (documented here for the record)

- **`parameter` touchpoints** (top-level `interface`/Question tools, e.g. an Analytic App's
  parameters) are implemented per dag-contract/plugin_map's `interface` type, but none of the 5
  samples exercise this path at the top level (wf_0004's only `interface` tool lives inside its
  macro's own `sub_dag`, which is that macro's private business, not a workflow-level touchpoint).
  Untested against a real fixture; reviewed by inspection only.
- **Non-blocking item checkbox state** for constants/macros/manual tools has no test coverage in the
  brief beyond "listed non-blocking" (which I do test). I made macro/manual/parameter items always
  render checked (`- [x]`, nothing to decide) and constants render unchecked with an empty
  `Override:` until a human explicitly checks the box and/or supplies override text in
  `open_questions.md` (mirroring the program spec §5.4 DateTimeNow() example precisely, which shows
  an *unchecked* non-blocking item on first render).
- **A `resolved`-from-global touchpoint is omitted from `open_questions.md` entirely** (not rendered
  as either `[x]` or `[ ]`), since there is genuinely nothing left to ask about it and rendering it
  either way risked misleading a human skimming the file. Not directly tested by name, but consistent
  with `test_globally_resolved_sources_are_never_asked_again`'s spirit (never asked again — including
  in the written record of open questions).

## Self-review findings

- Re-read every function against the brief's exact interface list; all present with matching
  signatures except `prompt_touchpoints`, which gained one optional keyword-only `rescore=None`
  parameter beyond the documented 3 — needed so the "local copy" fallback (which the brief's prose
  requires) can re-run `propose_candidates` against the catalog/program config, neither of which
  `prompt_touchpoints`'s documented signature receives. Documented in its docstring; backward
  compatible with the documented 3-argument call (verified: `apply_answers`'s conflict test and every
  other given test call `prompt_touchpoints` only indirectly through `ip.run`, which is where the
  `rescore` closure is built).
- Confirmed no test relies on `catalog/columns.csv`'s exact row counts beyond wf_0001's
  `1,204,551` (used in the transcript check); the rest were chosen for plausibility only.
- Confirmed `_safe_basename`/`find_yxdb` never join unsanitised dag-derived or user-typed text to a
  path (per the coordinator's mid-task heads-up about the incoming `paths.py` tightening): a yxdb
  basename is taken via `re.split(r"[\\/]+", source)[-1]` and refused if empty, `.`/`..`, or still
  containing a separator, before ever reaching `repo.wf(...)`.

## Concerns

None that block completion. The two design-decision notes above (parameter touchpoints untested
against a real fixture; non-blocking checkbox semantics) are the only places I made a judgment call
the brief didn't fully pin down; both are documented in code comments/docstrings as well as here.

---

## Follow-up 1 — dangerous default: an output could default onto its own input

The coordinator read the real transcript in this report (not just the tests) and caught something
the tests didn't: wf_0001's Q3 (`excluded_orders.csv`, an *output*) offered `SALES.RAW.ORDERS` as
its top candidate (6/7 columns, `basis: "columns"`) — the workflow's own *input*. Under the
original brief's rule ("Enter accepts candidate 1 only if its basis is columns"), pressing Enter on
that output would have mapped it, write mode `overwrite`, onto the table the workflow reads. Column
overlap is the right signal for an input's table; for an output it just as naturally surfaces the
workflow's own inputs, since an output is very often a reshaping of one. This report's own
transcript exhibited the exact bug it was meant to be a readability check for — a good reminder that
"does the transcript read clearly" and "is the transcript's *behavior* correct" are different
questions, and I'd only fully checked the first.

### What changed

**`scripts/intake_touchpoints.py`**
- `propose_candidates(tp, catalog, program, *, exclude=())` — new keyword-only `exclude` (default
  empty, so every existing 3-positional-arg call, including the brief's, is unaffected). For an
  `output` touchpoint only, a catalog table is dropped from the column-backed ranking (never from
  the naming-convention candidate — that stays a proposal to confirm, not a lookup) when its FQN is
  in `exclude` or its schema equals `program.raw_schema` (default `RAW`).
- `known_input_sources(touchpoints, answers=())` — new public function: `{fqn.upper(): tool_id}`
  for every table this workflow already reads through one of its own `input` touchpoints, per the
  ruling's three sources — resolved from `mappings/global.yaml`, already answered earlier in the
  same prompt session, or an input's own top column-backed candidate (even unconfirmed: a strong
  enough guess that offering it back as an output candidate would defeat the whole point).
- `run` now scores every input's candidates first, computes `known_input_sources` from the result,
  then scores every output's candidates with that as `exclude` — so an output's exclusion list is
  correct even on the very first `intake_touchpoints.py` run, before any interactive session exists.

**`scripts/intake_prompt.py`**
- `_enter_accepts(t, candidate)` — Enter may only auto-accept an output candidate on a **full**
  column match (`matched == of`); a partial match, however high its score, is exactly as likely to
  be some other table and is never a safe default. An input keeps the brief's original rule.
- The prompt line now shows what Enter will actually do: `[1] (Enter=accept | …)` when it would
  map, `[defer] (Enter=defer | …)` when it would not — never a bracket that lies about the keypress.
- `_SelfReferenceRisk` — raised by `_interpret_answer_text` (only ever for an output) when a
  **chosen** table, by number or typed FQN, collides with `known_input_sources` or the raw schema.
  Since candidates are already filtered, this only fires when a human deliberately picks or types
  the table anyway (including the rare case where the *naming* candidate itself happens to collide).
  - Interactive (`_ask_touchpoint`): prints `WARNING: <table> is also what tool <N> READS. Mode
    <mode> would replace this workflow's own input.` (or the raw-schema wording), asks
    `Type 'yes' to confirm, or anything else to pick a different table:`, and only the literal `yes`
    proceeds — anything else loops back to the same "Snowflake table [...]" prompt, consuming one of
    the 3 attempts.
  - Non-interactive (`run`'s resume path): the answer text must end with the literal token
    `confirm-self-reference` (`FQN_RE` allows no hyphens, so this can never collide with a real
    identifier); without it the touchpoint simply stays unresolved.
  - Either way, a confirmed self-reference is recorded as `self_reference: true` on that output in
    `intake/mappings.yaml`, and `render_open_questions` adds a **non-blocking** item citing program
    spec §8.5's run-ordering risk, naming the tool and mode. Every still-open (unchecked) blocking
    output item also gets a short proactive hint about the `confirm-self-reference` token, so a
    human sees the rule *before* typing a colliding table, not only after a silently dropped
    attempt (there's no clean stateless way to say "you tried and were refused" across separate
    `open_questions.md` renders, so I chose to always explain the rule instead).
- `prompt_touchpoints` re-scores each output's candidates immediately before asking about it (via an
  extended `rescore(t, exclude)` — `run`'s closure now takes an optional `exclude` kwarg, and the
  existing local-copy caller, which only ever rescored an *input*, still calls it with just `t`),
  using `known_input_sources(touchpoints, answers)` recomputed fresh each time — so a source
  answered earlier in *this same session* (not just what was already known when
  `intake_touchpoints.py` last ran) is excluded too.

### Tests (RED verified, not assumed)

New `tests/test_intake_self_reference.py`, 10 tests, covering exactly the brief's list: wf_0001's Q3
never offers `SALES.RAW.ORDERS`/`SALES.RAW.ORDERS_ARCHIVE`; Enter on Q3 defers; typing
`SALES.RAW.ORDERS` for Q3 warns, `no` re-asks within the same 3-attempt budget, `yes` applies with
`self_reference: true` and a WARNING naming tool 1; the non-interactive path refuses the same answer
without the token and applies it with `... confirm-self-reference`; wf_0003's `dbo.GL_SUMMARY` →
`ANALYTICS.CURATED.GL_SUMMARY` (a full 6/6 match in `CURATED`, not `RAW`, and not a source) *is*
accepted by Enter, no catalog change needed (it was already an exact 6-column match); a
unit-level check that the raw-schema exclusion fires even for a table that isn't a known source at
all; and a re-run of the brief's own `test_enter_accepts_…` scenario to confirm it is byte-for-byte
unaffected (Q2/Q3 there were an explicit FQN and `?`, neither of which touches a candidate list or
Enter's default).

To get genuine RED rather than an assumed one: `git stash push --keep-index -m ruling1-wip --
scripts/intake_touchpoints.py scripts/intake_prompt.py` (keeping the new test file), re-ran —
**8 of 10 failed** against the pre-fix code, exactly the ones exercising the new exclusion/warning/
token behavior; the other 2 (the wf_0003 full-match acceptance, which was never wrong, and the
brief's own unaffected scenario) passed both before and after, as they should. `git stash pop`
restored the fix; all 10 green again.

All of the brief's verbatim `tests/test_intake_prompt.py` tests still pass unchanged — none of them
exercised the code path this ruling changes (Q2/Q3 in `test_enter_accepts_…` were an explicit FQN
and a `?` defer; every other given test's outputs are deferred or use explicit FQNs too), so there
was nothing to reconcile.

### Real transcripts (re-run per the coordinator's instruction)

**wf_0001** — `printf 'n\n\nSALES.RAW.ORDERS\nno\nANALYTICS.CURATED.SALES_SUMMARY\n\n?\n' | python scripts/intake_prompt.py wf_0001 --interactive --root <tmp>` (deliberately typing the workflow's own input for Q2 first, to exercise the new warning):

```
wf_0001 - sales_summary.yxmd
2 yxdb files found: orders.yxdb (tool 1, input), sales_summary.yxdb (tool 7, output)
Does this workflow depend on other .yxdb files that are not visible in the DAG (for example written by another workflow)? [y/N]: Q1 - Tool 1 Input Data reads C:\data\sales\orders.yxdb (7 fields, 20 rows)
  1) SALES.RAW.ORDERS             7/7 columns - 1,204,551 rows
  2) SALES.RAW.ORDERS_ARCHIVE     6/7 columns (missing STATUS)
  3) ANALYTICS.RAW.ORDERS         naming convention -- not verified to exist
Snowflake table [1] (Enter=accept | number | DB.SCHEMA.TABLE | ?=defer | !=cannot exist in Snowflake): Q2 - Tool 7 Output Data writes C:\data\out\sales_summary.yxdb (6 fields), mode overwrite
  1) ANALYTICS.CURATED.SALES_SUMMARY naming convention -- not verified to exist
Snowflake table [defer] (Enter=defer | number | DB.SCHEMA.TABLE | ?=defer | !=cannot exist in Snowflake): WARNING: SALES.RAW.ORDERS is also what tool 1 READS. Mode overwrite would replace this workflow's own input.
Type 'yes' to confirm, or anything else to pick a different table: Snowflake table [defer] (Enter=defer | number | DB.SCHEMA.TABLE | ?=defer | !=cannot exist in Snowflake): Write mode [overwrite] (overwrite/append/merge): Q3 - Tool 8 Output Data writes C:\data\out\excluded_orders.csv (7 fields), mode overwrite
  1) ANALYTICS.CURATED.EXCLUDED_ORDERS naming convention -- not verified to exist
Snowflake table [defer] (Enter=defer | number | DB.SCHEMA.TABLE | ?=defer | !=cannot exist in Snowflake): wf_0001: intake WAITING_FOR_ANSWERS
```

Note `SALES.RAW.ORDERS`/`SALES.RAW.ORDERS_ARCHIVE` no longer appear in Q3's candidate list at all
(compare the original report's transcript above, where they did), and Q2/Q3 both correctly show
`[defer]` as their Enter default, since neither has a full column match. Resulting
`intake/open_questions.md`'s still-open Q3 item now reads (line-wrapped here for the report only):

```
- [ ] Q3 - Tool 8 Output Data writes C:\data\out\excluded_orders.csv (7 fields), mode overwrite.
      Proposed: ANALYTICS.CURATED.EXCLUDED_ORDERS (naming convention; not verified to exist).
      (a table this workflow already reads, or in its raw landing schema, needs
      'confirm-self-reference' appended to the answer to confirm it anyway.)
      Confirm or supply another:
```

**wf_0003** — `printf 'n\n\n\n\n\n' | python scripts/intake_prompt.py wf_0003 --interactive --root <tmp>` (all Enter, to show the full-match output candidate *is* still accepted, plus the merge-keys follow-up):

```
wf_0003 - gl_period_close.yxmd
0 yxdb files found: none
Does this workflow depend on other .yxdb files that are not visible in the DAG (for example written by another workflow)? [y/N]: Q1 - Tool 1 Input Data reads dbo.GL_LEDGER via alias:prod_fin (6 fields)
  1) FINANCE.RAW.GL_LEDGER        6/6 columns - 15,208,763 rows
  2) ANALYTICS.RAW.GL_LEDGER      naming convention -- not verified to exist
Snowflake table [1] (Enter=accept | number | DB.SCHEMA.TABLE | ?=defer | !=cannot exist in Snowflake): Q2 - Tool 10 Output Data writes dbo.GL_SUMMARY via alias:prod_fin/dbo.gl_summary (6 fields), mode update_insert
  1) ANALYTICS.CURATED.GL_SUMMARY 6/6 columns - 41,876 rows
Snowflake table [1] (Enter=accept | number | DB.SCHEMA.TABLE | ?=defer | !=cannot exist in Snowflake): Write mode [merge] (overwrite/append/merge): Merge keys [ACCT, PERIOD]: wf_0003: intake READY
```

Q2 correctly shows `[1] (Enter=accept …)` — `ANALYTICS.CURATED.GL_SUMMARY` is a full 6/6 match in
`CURATED`, not `RAW`, and isn't one of this workflow's own inputs — and `intake/mappings.yaml` came
out exactly matching `samples/wf_0003/sample.json`'s own intended answers:

```yaml
sources:
  alias:prod_fin: {snowflake: FINANCE.RAW.GL_LEDGER, logical: GL_LEDGER, tool_ids: ['1'], confirmed_by: <user>}
outputs:
  alias:prod_fin/dbo.gl_summary:
    snowflake: ANALYTICS.CURATED.GL_SUMMARY, logical: GL_SUMMARY, tool_ids: ['10'], confirmed_by: <user>
    mode: merge, keys: [ACCT, PERIOD]
```

## Follow-up 1 — RULING 2: wf_0002's sample answer key

Aligned `samples/_tools/make_golden_inputs.py`'s wf_0002 `sample.json["answers"]` entry with
wf_0003's own established precedent: the DB output's key is no longer the bare `alias:dw_sales`
(which collides with contract C8's own bare-alias form and doesn't carry the `/<table>` suffix a
real DB-output touchpoint key needs — exactly the discrepancy I flagged in the original report), but
the tool id `"10"`. Regenerated with `.venv/Scripts/python.exe samples/_tools/make_golden_inputs.py`
and confirmed via `git diff --stat samples/` that **only** `samples/wf_0002/sample.json` changed (one
key, `alias:dw_sales` → `10`) — all 34 golden CSVs and every other `sample.json` came back
byte-identical. Updated the adjacent comments on both wf_0002 and wf_0003 to explain each one's own
reason (one alias serving many tables vs. an input and output sharing the same bare alias).

Added `test_every_sample_answer_key_resolves_to_a_blocking_touchpoint` (parametrized over all 5
samples) to `tests/test_intake_touchpoints.py`: for every `sample.json["answers"]` key, resolves it
against the touchpoints `enumerate_touchpoints` actually produces, accepting either the normalized
`key` (contract C8) or the bare `tool_id` — so this alignment is now enforced, not just asserted in
a report. `tests/test_samples_wellformed.py` doesn't resolve answer keys at all (only checks
`len(answers)` and that values are valid FQNs), so it needed no changes and still passes unchanged.

## Follow-up 1 — `parameter` touchpoints

Confirmed as accepted: none of the five samples has a workflow-level (non-macro) Question tool, so
this path had no fixture-level coverage. Added
`test_parameter_touchpoint_from_a_top_level_interface_tool` to `tests/test_intake_touchpoints.py` — a
small synthetic dag (one `type: "interface"` node, `plugin: "AlteryxGuiToolkit.Questions.NumericUpDown"`)
passed directly to `enumerate_touchpoints`, asserting `kind: "parameter"`, `blocking: False`,
`key`/`source`/`format` pulled from the question's own `name`/`default`/`type`.

## Follow-up 1 — verification

```
$ .venv/Scripts/python.exe -m pytest tests/test_intake_touchpoints.py tests/test_intake_prompt.py \
    tests/test_intake_cli.py tests/test_intake_self_reference.py tests/test_samples_wellformed.py -q
120 passed
$ .venv/Scripts/python.exe -m pytest
692 passed
```

Output pristine at every run. Files changed this round: `scripts/intake_touchpoints.py`,
`scripts/intake_prompt.py`, `tests/test_intake_touchpoints.py` (additions),
`tests/test_intake_self_reference.py` (new), `samples/_tools/make_golden_inputs.py`,
`samples/wf_0002/sample.json`. Commit: `556c0e6` fix: an output must never default onto a source
(dangerous-default review).

No new concerns. The two Follow-up-1 rulings are fully addressed; nothing further is pending.

---

## Fix round 1 — one CRITICAL, two IMPORTANT (found by live probing)

Worked in the worktree `.worktrees/task-10-fix` (branch `wt/task-10-fix`), per the coordinator's
instruction, because another agent was writing in the main checkout. Commit `d53d8ca` on that
branch.

The reviewer's static checks (interfaces, contract C8, candidate scoring, the prompt protocol, the
dangerous-default fix, sample-key alignment) all passed, then the reviewer **ran the multi-session
and self-reference scenarios live** and found three real bugs none of my own tests had exercised.

### CRITICAL — `intake/mappings.yaml` regressed across a multi-session run

`apply_answers` rebuilt `intake/mappings.yaml` from a **blank dict** every call, populated only
from that call's own `answers`. Both prompting paths skip any touchpoint whose `resolved` is set
(the "never asked again" rule). So the sequence the reviewer ran live — map Q1+Q2 interactively
(`WAITING_FOR_ANSWERS`, Q3 open) → re-run `intake_touchpoints.py` (Q1/Q2 now `resolved` from the
just-promoted `mappings/global.yaml`) → answer Q3 non-interactively (`READY`) — left
`intake/mappings.yaml["sources"]` empty and `["outputs"]` missing the Q2 entry, even though the
workflow reported `READY`. This is load-bearing: `scripts/load_golden.py::load_set` reads **only**
`workflows/<wf>/intake/mappings.yaml` for logical names (never `mappings/global.yaml`), so that
`READY` workflow would fail at validation with a missing-logical-name `ValueError`.

**Fix (ruling 1):** `apply_answers` now:
1. Reads the **existing** `intake/mappings.yaml` (if any) and starts from it — `sources`/`outputs`
  seeded from the file, not `{}` — then layers this call's answers on top. A later answer for a
  key replaces the earlier entry; everything else already on file survives untouched.
2. Seeds `logical_name`'s collision set (`taken_logicals`) from every logical name already in the
  file, not just this call's own.
3. Backfills a full entry (`snowflake`, `logical`, `tool_ids`, and for outputs `mode`/`keys`) for
  every blocking touchpoint that is `resolved` (from `mappings/global.yaml`) and not already
  covered — `confirmed_by` taken from the **global** entry's own `confirmed_by` (looked up by key
  in the freshly-read `mappings/global.yaml`, not invented), plus `from: "global"`. `t["resolved"]`
  itself only ever carries `{"snowflake","logical","from"}` (the brief's verbatim
  `test_globally_resolved_sources_are_never_asked_again` asserts that exact dict), so
  `confirmed_by` could not come from `resolved` itself — it has to be looked up.
4. `constants`/`parameters`/`macros` stay refreshed straight from the dag every call (unchanged
  behavior — a removed constant or macro should not linger from an old file).
5. `run()` adds an **independent guard**: after `apply_answers` returns, `_missing_blocking_entries`
  is recomputed against the touchpoints and the mappings dict (not trusting `result["missing"]`);
  if anything is missing and the status claims `READY`, the status is force-downgraded to
  `NEEDS_HUMAN`, the missing touchpoint ids are named in a `GUARD: …` message via `out(...)`, and
  that is what gets persisted to `manifest.status.intake`. This is deliberately redundant with
  `apply_answers`'s own (now-correct) computation — the point is that a *future* bug in that
  computation can never again surface as a silent, wrong `READY`.

### IMPORTANT A — self-reference protection missed user-declared sources

`known_input_sources` only ever scanned DAG `input` touchpoints. A source declared through the
opening "other yxdb" loop (`_ask_extra_yxdb`) gets a synthetic id like `"U1"` that matches no
touchpoint, so it never reached the exclusion set — verified live by declaring
`\\share\fx\rates.yxdb` → `ANALYTICS.CURATED.FX_RATES` and then typing that exact FQN as an
output's answer: no warning, no confirmation prompt, no `self_reference` flag. My own test had used
`FINANCE.RAW.FX_RATES`, which the *raw-schema* check happened to catch anyway, masking the gap.

**Fix (ruling 2):** `known_input_sources` gained an `existing_sources` parameter and now folds in
sources from two more places: (a) this session's own opening-loop answers (an `answers` entry whose
`id` matches no real touchpoint), and (b) `intake/mappings.yaml["sources"]` as already on disk —
covering a source declared in an *earlier* session, `note: user-declared` entries included.
`intake_touchpoints.run` reads any existing `intake/mappings.yaml` before scoring candidates;
`intake_prompt.run` reads it once and threads it through both `prompt_touchpoints` (a new optional
`existing_sources` kwarg) and the non-interactive `known_input_sources` call. Comparison stays
case-insensitive (already was, via `.upper()` throughout).

### IMPORTANT B — the self-reference note corrupted a later parse

`_render_self_reference_note` rendered `- [x] Q<n> (self-reference) - Tool … target {snowflake}
…` — a **second line matching the same `Q<n>` checkbox pattern** the real item uses, with its own
`": "` in the prose ("target SALES.RAW.ORDERS (mode overwrite), which is..."). The old
line-based `_ITEM_RE`/`parse_answered_questions` matched on the bare `Q<n>` prefix and let the
*last* matching line win, so the note's own prose was read as `Q<n>`'s answer on a later
non-interactive resume. `FQN_RE` rejected it (it's prose, not a table name), so nothing was
*invented*, but the real confirmed answer was silently lost — verified live: after an interactively
confirmed self-reference, one more `ip.run(interactive=False)` dropped that output from
`intake/mappings.yaml`.

**Fix (ruling 3):** Rewrote `open_questions.md` rendering and parsing to be block-based and
label-anchored:
- An **item** is every line from its `- [ ]`/`- [x] Q<n>` checkbox up to (not including) the next
  checkbox or heading line.
- Every item's **first line** is now just the checkbox plus a short, colon-free description (`Tool
  N Input/Output Data reads/writes <source> (…)`.) — never the proposal, a conflict note, or the
  answer.
- Every other fact — the `Proposed: …` line, a `CONFLICT: …` line, the self-reference hint, and
  finally the answer — is an **indented continuation line**, and the answer is read only from the
  fixed-label line the renderer always emits **last**: `Confirm or supply another:` for
  input/output items, `Override:` for constants — both defined once as `_LABEL_IO`/
  `_LABEL_CONSTANT` (`_ANSWER_LABELS`), shared by renderer and parser so they can never drift apart.
- `_render_self_reference_note` now emits a `*` bullet (not `- [ ]`/`- [x]`), so it can **never**
  match the checkbox pattern and can never be mistaken for an item of its own.
- `parse_answered_questions`/`_item_answer` read an item's answer by precedence, stated in the
  docstring: (1) the designated answer line's own text; (2) if that's empty or missing, the text
  after the **last** `': '` in the item's **first line only**; (3) if the first line has no `': '`
  at all, a trailing token on the first line matching `FQN_RE` (optionally followed by
  ` confirm-self-reference`). Precedence 2/3 exist *specifically* to keep the brief's own verbatim
  `test_answers_written_into_open_questions_resume_the_workflow` working unchanged — it appends the
  answer straight onto the checkbox's first line (`- [x] Q3 … ANALYTICS.CURATED.EXCLUDED_ORDERS`)
  rather than onto a labelled line. Traced by hand against that test's exact string manipulation:
  the new first-line format has zero `': '` occurrences before the test's edit, so precedence 2
  finds nothing and precedence 3 (the trailing-FQN token, which the appended text always is)
  correctly recovers the answer — verified both by hand-tracing and by the test itself, unmodified,
  still passing.

### Tests (RED verified honestly)

New `tests/test_intake_fix_round_1.py`, 12 tests, covering the reviewer's exact list:
- **(a)** the reviewer's CRITICAL sequence end to end — interactive Q1+Q2, re-run
  `intake_touchpoints.py`, non-interactive Q3, `READY`, all three entries present with `logical`
  and `tool_ids` — **and then calls `load_golden.load_set` against the result**, the real consumer,
  and asserts it succeeds and loads real tables. Not a mock: an actual `DuckDBBackend`.
- **(b)** a workflow every touchpoint of which is resolved from `mappings/global.yaml` before the
  first prompt: only the opening yxdb question is ever asked (`len(said) == 1`), status `READY`,
  and `intake/mappings.yaml` is fully populated with `confirmed_by`/`from: global` taken from the
  global entries.
- **(c)** the guard: `monkeypatch.setattr(ip, "apply_answers", fake_apply_answers)` returning a
  rigged `{"status": "READY", "mappings": {…all empty…}, …}`; `run()` downgrades to `NEEDS_HUMAN`
  and the `GUARD:` message names `Q1`, `Q2` and `Q3`.
- **(d)** four tests: typing `ANALYTICS.CURATED.FX_RATES` (a user-declared source, not in the raw
  schema) for an output triggers the warning; typing the differently-cased
  `analytics.curated.fx_rates` does too; the non-interactive path refuses the same answer without
  `confirm-self-reference`; a source declared in an *earlier* session (nothing new declared this
  session) is protected the same way.
- **(e)** an interactively confirmed self-reference survives **two more** non-interactive
  `ip.run()` calls unchanged (`self_reference: true` still present both times).
- **(f)** three tests: prose containing `': '` inside an item's body is never read as the answer;
  a `*` self-reference note line never parses as its own item and never clobbers the real item's
  answer; an empty checked answer on an output whose proposal is *not* Enter-acceptable (naming or
  partial match) stays unresolved with an explanation, paired with the positive case (a full-match
  output *is* accepted by an empty checked answer) to directly test the dangerous-default ruling's
  interaction with "accept the proposal" non-interactively.
- **(g)**: the brief's own eight verbatim tests re-run unchanged as part of the full suite below.

RED evidence, honestly obtained per the updated rule (git stash — and `git checkout -- <file>`,
which the rule explicitly names too — are both banned in this repo because the stash stack is
shared across every worktree while other agents work concurrently in them): copied
`scripts/`, `tests/`, `mappings/`, `catalog/`, `samples/` and `pyproject.toml` into a scratch
directory **outside** the repo, then `git show 556c0e6:scripts/intake_prompt.py` and
`git show 556c0e6:scripts/intake_touchpoints.py` (run from inside the worktree, which shares the
same object database — this reads a historical blob, it does not touch any working tree) to
overwrite just those two files in the scratch copy with their pre-fix content, confirmed by grep
that the fix-round markers (`existing_sources`, `_missing_blocking_entries`, `_item_blocks`) were
genuinely absent, and ran the new test file there:

```
$ cd <scratch copy with pre-fix scripts/>
$ .venv/Scripts/python.exe -m pytest tests/test_intake_fix_round_1.py -v
======================== 11 failed, 1 passed in 1.26s =========================
```

11 of 12 failed against the pre-fix code. The 1 that passed
(`test_f_empty_answer_on_a_checked_output_item_with_no_enter_acceptable_proposal_stays_unresolved`)
exercises `_interpret_answer_text`'s already-correct "empty text + non-columns-basis top candidate
→ defer" rule, which predates this fix round entirely (it's the same rule the earlier
dangerous-default fix added) — correctly unaffected, included alongside its positive-case pair for
completeness of ruling 3's own test requirement. Deleted the scratch copy immediately after. Then,
back in the worktree (never touched by the above): all 12 green.

```
$ .venv/Scripts/python.exe -m pytest tests/test_intake_fix_round_1.py -v
======================== 12 passed in 1.88s ========================
$ .venv/Scripts/python.exe -m pytest tests/test_intake_touchpoints.py tests/test_intake_prompt.py \
    tests/test_intake_cli.py tests/test_intake_self_reference.py tests/test_intake_fix_round_1.py \
    tests/test_load_golden.py tests/test_samples_wellformed.py -q
183 passed
$ .venv/Scripts/python.exe -m pytest
704 passed in 29.09s
```

Output pristine at every run. No brief assertion conflicted with any ruling — the brief's verbatim
`test_answers_written_into_open_questions_resume_the_workflow` needed the fallback-precedence design
described above to keep passing unchanged, which it does.

### Self-review

- Re-read the full diff of both files. One naming nit caught and fixed before committing: a code
  comment in `known_input_sources` said "a real *input* touchpoint's own answer" for a `continue`
  that actually also (correctly) skips a real *output* touchpoint's own answer — reworded to state
  both cases accurately (an output's own confirmed target is deliberately never treated as a
  "source": mapping an output doesn't make its target a table the workflow *reads*).
- Confirmed `known_input_sources`'s new return type (`dict[str, str | None]`, since a user-declared
  source has no `tool_id`) is handled safely everywhere it's read: `_self_reference_warning` already
  falls back to "one of this workflow's own inputs" when `tool_id` is `None`.
- Confirmed the case-insensitive comparison test (d) exercises the *typed-FQN* path specifically
  (case only matters for what a human types; the offered candidates are always the catalog's own
  canonical case).

### Files changed this round

- `scripts/intake_prompt.py`
- `scripts/intake_touchpoints.py`
- `tests/test_intake_fix_round_1.py` (new, 12 tests)

Commit: `d53d8ca` on branch `wt/task-10-fix` (worktree `.worktrees/task-10-fix`) — "fix:
mappings.yaml stays cumulative; self-reference covers every source; open_questions.md parsing is
block-based (fix round 1)".

No new concerns. All three findings (1 CRITICAL, 2 IMPORTANT) are fixed and covered by tests that
were verified RED against the actual pre-fix code before being verified GREEN against the fix.

---

## Fix round 2 — the new guard validated presence, not completeness

Worked in a fresh worktree, `.worktrees/task-10-fix2` (branch `wt/task-10-fix2`), per the
coordinator's instruction, because another agent was writing in the main checkout. Commit
`bae7052` on that branch.

Re-review of fix round 1: **all three prior findings (1 CRITICAL, 2 IMPORTANT) confirmed fixed** —
the re-reviewer reproduced every probe (an answer that changes an earlier mapping replaces it
without duplicating; an output's `mode`/`keys` survive a later session; a cross-session
logical-name collision resolves to `QUX_ORDERS`; the self-reference gate covers user-declared,
differently-cased and earlier-session sources; block-based parsing keeps the brief's verbatim tests
byte-identical). Then found **one Important gap in the guard round 1 itself introduced**.

### FINDING — the guard checked entry PRESENCE only, not COMPLETENESS

`_missing_blocking_entries` (round 1's guard) only checked `t["key"] not in
(mappings.get(section) or {})` — it never checked that an entry which *was* present actually had
`logical` (or `tool_ids`, a valid `mode`, …). Live reproduction: seed `intake/mappings.yaml` with
entries for all three of wf_0001's blocking touchpoints but omit `logical` from the source entry (a
hand-edit, or a future regression in `apply_answers`) — the guard saw nothing missing,
`ip.run(interactive=False, ...)` returned `READY`, and `load_golden.load_set` then raised
`ValueError: source 'sales/orders.yxdb' (tool 1) has no 'logical:' name in intake/mappings.yaml` —
the exact failure the guard exists to make impossible.

### Fix

Split the round-1 guard into two functions with different consequences:

- **`_missing_blocking_entries`** (kept, unchanged): a blocking touchpoint with **no entry at all**
  yet. This is the normal, silent mid-session state — nothing has gone wrong, the workflow just
  isn't fully answered — and still contributes to `WAITING_FOR_ANSWERS`, never `NEEDS_HUMAN`. This
  distinction is exactly what keeps every deferred-touchpoint test (the brief's own and round 1's
  alike — e.g. `test_enter_accepts_a_column_backed_candidate_and_outputs_are_asked_for_mode`, which
  expects `WAITING_FOR_ANSWERS` when Q3 has no entry) passing unchanged: if "no entry" were folded
  into the same "violation" bucket as an invalid entry, that verbatim test would have started
  reporting `NEEDS_HUMAN` instead.
- **`_mapping_problems`** (new): every reason an entry that *does* exist is incomplete or invalid —
  a genuine violation. For every entry actually in `mappings["sources"]`/`["outputs"]`
  (touchpoint-tied or `note: "user-declared"` alike):
  - `snowflake` is a string matching `FQN_RE`;
  - `logical` is a string matching `^[A-Z_][A-Z0-9_$]*$` (procedures use it inside
    `IDENTIFIER(...)`), and no two entries in the whole file share one (they'd collide in the
    sandbox view schema);
  - `tool_ids` is a non-empty list containing the tied touchpoint's own `tool_id` — skipped
    entirely for a `note: "user-declared"` entry, which has no touchpoint and legitimately carries
    `tool_ids: []` (exempt from *this* rule only, not the others);
  - for an output: `mode` is one of `overwrite`/`append`/`merge`, `keys` is a list, non-empty when
    `mode == "merge"`.

  Returns human-readable strings naming the entry and the field, e.g. `sources["sales/orders.yxdb"]:
  logical is missing`. Reports only — never repairs, invents, or mutates anything.

`apply_answers`'s own status computation now checks `problems` (→ `NEEDS_HUMAN`) before `missing`
(→ `WAITING_FOR_ANSWERS`), and returns both lists. `run()`'s guard recomputes **both** independently
against the file it actually wrote (not trusting `apply_answers`'s own bookkeeping — the point is a
check that survives a future bug in that computation, or a hand-edited file): any genuine
`_mapping_problems` violation is **always** reported (message via `out()`, and a `>` note — never a
`- [ ]`/`- [x]` line, so it can't be mistaken for an item — at the very top of `open_questions.md`,
per the ruling's "message in the returned/printed status line and as a note at the top of
open_questions.md"), whether `apply_answers` already caught it on its own or not; a missing-only
condition escalates only when it contradicts a claimed `READY` (round 1's narrower, still-useful
anomaly check).

### Tests (RED verified honestly)

New `tests/test_intake_fix_round_2.py`, 15 tests: the reviewer's exact reproduction (missing
`logical`) plus `load_golden.load_set` as the real consumer, confirming it is never reached with a
`READY` status built on the bad file (and that calling it anyway fails exactly as the live
reproduction showed); one test per other rule (bad `snowflake` shape, lower-case `logical`, spaced
`logical`, empty `tool_ids`, `tool_ids` not containing the touchpoint's id, unknown output `mode`,
`merge` with empty `keys`, duplicate `logical` across a source and an output); a user-declared
source's empty `tool_ids` does *not* trip the tool-id rule but a bad `logical` on that same entry
still does; a fully valid hand-built file is `READY` with `load_golden.load_set` succeeding (the
positive control); CLI exit `1`/`0`. Updated one round-1 test
(`test_c_guard_downgrades_a_falsely_ready_status_to_needs_human`) whose assertion assumed the old
guard's one-combined-line message; the new, fuller guard reports one line per problem, so the
assertion now checks across all guard-related lines instead of just the first.

RED evidence, honestly obtained (the rule added after round 1 — no `git stash`, and `git checkout
-- <file>` is banned too — applied here as well): copied `scripts/`, `tests/`, `mappings/`,
`catalog/`, `samples/`, `pyproject.toml` into a scratch directory outside the repo, then `git show
d53d8ca:scripts/intake_prompt.py` (round 1's commit; run from inside the worktree, which shares the
same object database — reads a historical blob, touches no working tree) to overwrite that one file
in the scratch copy, confirmed by grep that `_mapping_problems`/`_LOGICAL_RE` were genuinely absent,
and ran the new test file there:

```
$ cd <scratch copy with pre-round-2 intake_prompt.py>
$ .venv/Scripts/python.exe -m pytest tests/test_intake_fix_round_2.py -v
======================== 12 failed, 3 passed in 1.65s =========================
```

12 of 15 failed against the pre-round-2 code. The 3 that passed are correct **positive controls**:
the user-declared-empty-`tool_ids` exemption (never broken — the old code never checked `tool_ids`
at all) and the fully-valid-file `READY` case in both its direct and CLI forms (never broken — a
complete, valid file was always `READY` under presence-only checking too). Deleted the scratch copy
immediately after. Back in the worktree (never touched by the above): all 15 green.

```
$ .venv/Scripts/python.exe -m pytest tests/test_intake_fix_round_2.py -v
======================== 15 passed in 1.53s ========================
$ .venv/Scripts/python.exe -m pytest tests/test_intake_touchpoints.py tests/test_intake_prompt.py \
    tests/test_intake_cli.py tests/test_intake_self_reference.py tests/test_intake_fix_round_1.py \
    tests/test_intake_fix_round_2.py tests/test_load_golden.py tests/test_samples_wellformed.py -q
198 passed
$ .venv/Scripts/python.exe -m pytest
736 passed, 5 skipped in 33.16s
```

Output pristine for every command touching my own files (the 5 skips and one deprecation warning
in the full-suite run are pre-existing, from `tests/test_e2e_parity.py`, unrelated to this task —
this worktree's base already includes other agents' later work). No brief or round-1 assertion
conflicted with this ruling.

### Self-review

- Confirmed `logical_name()`'s own output always satisfies `_LOGICAL_RE` by construction
  (`.upper()` of characters already restricted to `FQN_RE`'s charset), so `apply_answers`'s own
  writes never trip the new checks — the only way to reach a `_mapping_problems` violation is a
  hand-edited file or a mocked/broken `apply_answers`, exactly the guard's intended scope.
- Verified the "problems always reported regardless of which layer caught it" design against both
  paths: when `apply_answers` catches an invalid entry on its own (the common case), and when a
  monkeypatched `apply_answers` claims `READY` despite one (round 1's `test_c`, still passing) —
  both print the message and note it in `open_questions.md`.
- Checked the `guard_violation` boolean can never fire for a normal, benign `WAITING_FOR_ANSWERS`
  (some touchpoints simply not yet answered): `_mapping_problems` only ever inspects entries that
  *exist*, so a deferred touchpoint with no entry contributes nothing to it.

### Files changed this round

- `scripts/intake_prompt.py`
- `tests/test_intake_fix_round_1.py` (one assertion updated for the fuller guard message)
- `tests/test_intake_fix_round_2.py` (new, 15 tests)

Commit: `bae7052` on branch `wt/task-10-fix2` (worktree `.worktrees/task-10-fix2`) — "fix: READY
guard validates entry completeness, not just presence (fix round 2)".

No new concerns. The Important finding is fixed and covered by tests verified RED against the
actual pre-round-2 code before being verified GREEN against the fix.

---

## Fix round 3 — CRITICAL: logical names flip-flop on every no-op resume

Worked in a fresh worktree, `.worktrees/task-10-fix3` (branch `wt/task-10-fix3`), per the
coordinator's instruction, because another agent was writing in the main checkout. Commit
`28ddf68` on that branch.

This one came from the **controller**, not the reviewer: reproducing round 2 through the real CLI
surfaced a CRITICAL regression dating all the way back to round 1, which neither the round-1
re-review nor round 2's own review had caught.

### THE FINDING

Reproduction: seed wf_0001, run `intake_touchpoints.py`, an interactive session answering Q1 with
Enter, Q2 `ANALYTICS.CURATED.SALES_SUMMARY` + Enter for mode, Q3 `ANALYTICS.CURATED.EXCLUDED_ORDERS`
+ Enter — logical names `ORDERS` / `SALES_SUMMARY` / `EXCLUDED_ORDERS`. Then
`intake_prompt.py wf_0001 --no-interactive`, with **nothing changed**, three times in a row:

```
after interactive session                                     : ORDERS / SALES_SUMMARY / EXCLUDED_ORDERS
--no-interactive (nothing changed)                     READY   : RAW_ORDERS / CURATED_SALES_SUMMARY / CURATED_EXCLUDED_ORDERS
--no-interactive (nothing changed), again              READY   : ORDERS / SALES_SUMMARY / EXCLUDED_ORDERS
--no-interactive (nothing changed), again              READY   : RAW_ORDERS / CURATED_SALES_SUMMARY / CURATED_EXCLUDED_ORDERS
```

### CAUSE, confirmed in the code

Round 1's `apply_answers` seeds `taken_logicals` (the collision set `logical_name` checks a new
name against) from every logical name already in `intake/mappings.yaml` — necessary so a genuinely
new entry doesn't collide with an established one. But the non-interactive resume path re-parses
`open_questions.md` and re-applies the **same checked answers** on every single call — nothing
marks an item "already settled". So re-answering a key with its own **unchanged** FQN handed
`logical_name` a `taken` set that already contained that key's **own** current logical name (seeded
from the very entry about to be recomputed) — a guaranteed self-collision — which fell back to
`<SCHEMA>_<TABLE>` (`RAW_ORDERS`). The *following* run then saw `RAW_ORDERS` on file instead of
`ORDERS`, so the plain name was "free" again, and the two states alternated forever. Load-bearing:
translated procedures read `IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS')`,
`load_golden.load_set` builds the sandbox view schema from these exact names, and the orchestrator
runs intake more than once per workflow — a harmless re-run would silently break a validated
workflow's procedure/view wiring.

### Fix

In `apply_answers`'s answers-layering loop (both the touchpoint-tied branch and the user-declared
branch), before doing anything with a `"map"` answer:

1. **`same_fqn`** — if the existing entry's `snowflake` already matches the answer's (compared
  case-insensitively), `continue` immediately. The entry — `logical`, `mode`, `keys`,
  `confirmed_by`, `self_reference`, `note`, `from`, anything else on it — is kept exactly as it is;
  nothing is recomputed, and nothing is re-promoted to `mappings/global.yaml` (which is what makes
  that file stable too, and is what makes a hand-edited `logical`/`mode`/`keys` survive a resume:
  nothing here ever touches an entry whose FQN didn't change).
2. **`free_up`** — only reached for a genuinely new-or-changed entry: removes that same key's own
  *previous* `logical` (if any) from `taken_logicals` before calling `logical_name`, so replacing
  an entry's mapping never collides with the name it is itself in the process of replacing.

Both fixes are five lines each, applied at the two call sites that build a source/output entry.

### Other stability concerns checked (ruling point 5)

Went looking for the same self-collision or non-idempotence pattern elsewhere, as asked:

- **`manifest.answers` growing** — no: `answers_by_qid` is recomputed fresh each call from *this*
  call's own `answers` list and merged with `.update()`, which overwrites a key's value in place
  rather than duplicating or reordering it. Idempotent by construction; confirmed with a test.
- **Notes appended to `open_questions.md` on every run** — no: `render_open_questions` rebuilds the
  **entire file from scratch** every call (from `touchpoints` + the just-written `mappings`, never
  from the previous file's own text), so there is no append-only accumulation path for it to have.
  Confirmed by the byte-identical check across three resumes.
- **`tool_ids` gaining duplicates** — no: every place that sets `entry["tool_ids"]` assigns a fresh
  single-element list (`[t["tool_id"]]`), replacing rather than appending; and round round 3's own
  `same_fqn` skip means an unchanged entry's `tool_ids` isn't even touched a second time. Confirmed
  with a test across three resumes.
- **User-declared sources re-added with a new `U<n>` id each run** — the id itself is genuinely
  transient (used only to correlate an answer with its target key within one `apply_answers` call,
  never persisted anywhere — `answers_by_qid` explicitly filters to `a["id"] in by_id`, which a
  synthetic `"U1"` never is), so a new id each session was never itself a problem. What *would* have
  been a problem is the same self-collision bug hitting the *entry* on re-declaration, which
  `same_fqn`/`free_up` (the same general fix) now also covers — confirmed with a test that runs two
  full interactive sessions re-declaring the identical source and checks the resulting entry is
  byte-for-byte identical, not duplicated under a second key.

### Tests (RED verified honestly, and one claim double-checked rather than assumed)

New `tests/test_intake_fix_round_3.py`, 9 tests:

- the reproduction itself: three no-op `--no-interactive` resumes leave `intake/mappings.yaml`,
  `open_questions.md` **and** `mappings/global.yaml` byte-identical (not just the logical names —
  the whole file, since a change anywhere would mean *something* was recomputed that shouldn't
  have been);
- the orchestrator's own interactive → `--no-interactive` handover, with `load_golden.load_set`
  (the real consumer) actually run against the result and asserted to find the views under the
  **original** logical names;
- hand-edited `logical`/`mode`/`keys` survive a resume, and survive a *further* resume too;
- no duplicate `tool_ids` across three resumes;
- a user-declared source is not duplicated across two full interactive sessions that redeclare it;
- backfilled (`from: global`) entries are stable across resumes (byte-identical file, both times);
- `manifest.answers` is stable across resumes.

For the FQN-round-trip requirement (ruling 3), I did **not** just implement the coordinator's
literal worked example and assume it was red — I checked it by hand first. Running `SALES.RAW.ORDERS
-> SALES.RAW.ORDERS_ARCHIVE -> SALES.RAW.ORDERS` through `apply_answers` directly against the
**pre-round-3** code already returned to `ORDERS`, not `RAW_ORDERS`: at the moment each step is
computed, the seeded `taken_logicals` set never happens to contain the table name being computed,
because `ORDERS` and `ORDERS_ARCHIVE` never coincide. That specific example does not exercise
ruling 3's distinct "collisions never count an entry against itself" mechanism — it happens to be
adequately covered by ruling 2's unchanged-FQN skip alone. I kept it in the suite anyway (it's a
real, if weaker, regression test, and it's literally what was asked for), but to get a test that is
actually RED for ruling 3 specifically, I looked for a sequence where the *new* FQN's table name
coincides with the *same key's own current* logical name while the full FQN still differs (so
ruling 2's same-FQN skip does not fire): changing only the **database** while keeping schema and
table name (`SALES.RAW.ORDERS` -> `ANALYTICS.RAW.ORDERS`, both `ORDERS`). Verified by hand this
*does* fail under the pre-round-3 code (`RAW_ORDERS`) and passes under the fix (`ORDERS`), and added
it as `test_changing_only_the_database_keeps_the_same_table_name_from_colliding_with_itself`.

RED evidence, honestly obtained (`git show c186382:scripts/intake_prompt.py` — round 2's merge
commit, i.e. the exact pre-round-3 state — into a scratch directory outside the repo; confirmed by
grep that `same_fqn`/`free_up` were absent):

```
$ cd <scratch copy with pre-round-3 intake_prompt.py>
$ .venv/Scripts/python.exe -m pytest tests/test_intake_fix_round_3.py -v
tests\test_intake_fix_round_3.py FFF.F..F.                               [100%]
========================= 5 failed, 4 passed in 0.92s =========================
```

5 of 9 failed against the pre-round-3 code, reproducing the exact `ORDERS` -> `RAW_ORDERS` /
`SALES_SUMMARY` -> `CURATED_SALES_SUMMARY` flip from the live reproduction. The 4 that passed both
before and after are legitimate positive controls: the coordinator's own `ORDERS_ARCHIVE` round trip
(explained above), backfilled-from-global stability (nothing ever gets re-answered for an
already-fully-resolved workflow, so there was nothing for the old bug to touch), `tool_ids`
stability, and `manifest.answers` stability (none of these three were ever broken by the original
bug — they're independent, correctly-designed mechanisms, and I verified that by testing them, not
by assuming it). Deleted the scratch copy immediately after. Back in the worktree (never touched by
the above): all 9 green.

```
$ .venv/Scripts/python.exe -m pytest tests/test_intake_fix_round_3.py -v
======================== 9 passed in 0.99s ========================
$ .venv/Scripts/python.exe -m pytest tests/test_intake_touchpoints.py tests/test_intake_prompt.py \
    tests/test_intake_cli.py tests/test_intake_self_reference.py tests/test_intake_fix_round_1.py \
    tests/test_intake_fix_round_2.py tests/test_intake_fix_round_3.py tests/test_load_golden.py \
    tests/test_validate_segment.py tests/test_samples_wellformed.py -q
164 passed
$ .venv/Scripts/python.exe -m pytest
745 passed, 5 skipped, 1 warning in 33.73s
```

Output pristine for every command touching my own files. The 5 skips and the deprecation warning
(`tests/test_e2e_parity.py`) are pre-existing and unrelated — this worktree's base already includes
other agents' later work. No brief, round-1 or round-2 assertion conflicted with this ruling; none
of my own earlier tests encoded the flip (they were all written and verified after round 1's
introduction of the bug, but none happened to exercise a *second* no-op resume, which is exactly
why this regression went unnoticed until the controller's own live reproduction).

### Self-review

- Re-traced the exact bug mechanism against the code (not just against the symptom) before writing
  a single line of fix, per the ruling's "please confirm in the code" — the trace is in the CAUSE
  section above and matches what the tests then demonstrated.
- Caught my own mistake before it became false RED evidence: my first draft of the FQN-round-trip
  test used the coordinator's literal example, asserted `status == "READY"` at each step, and
  failed — but for an unrelated, *correct* reason (changing an already-globally-promoted entry's
  FQN legitimately conflicts with the old value still in `mappings/global.yaml`, which is a
  separate, working safety feature, not this bug). Rather than papering over that with a looser
  assertion, I switched to calling `apply_answers` directly (bypassing the global-promotion
  entanglement) and checked hand-first whether the literal example was even RED at all under the
  old code — it wasn't — which is what led me to add the genuinely-RED "change only the database"
  test instead of stopping at a green-under-both-versions test that only looked like coverage.
- Confirmed the fix is symmetric between the touchpoint-tied branch and the user-declared-source
  branch of `apply_answers`'s answers loop (both call the same `same_fqn`/`free_up` helpers).

### Files changed this round

- `scripts/intake_prompt.py`
- `tests/test_intake_fix_round_3.py` (new, 9 tests)

Commit: `28ddf68` on branch `wt/task-10-fix3` (worktree `.worktrees/task-10-fix3`) — "fix: intake
resume is idempotent -- logical names no longer flip-flop (fix round 3)".

No new concerns. The CRITICAL finding is fixed, its cause confirmed in the code before the fix was
written, and covered by tests verified RED against the actual pre-round-3 code (one test corrected
mid-flight when hand-verification showed the coordinator's literal example wasn't itself RED)
before being verified GREEN against the fix.
