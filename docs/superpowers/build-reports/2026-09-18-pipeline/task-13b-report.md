# Task 13b — hand migrations and canned artifacts for `wf_0003`, `wf_0004` and `wf_0005`

**Status: DONE_WITH_CONCERNS.** Everything the brief asks for exists, the full suite is green with
zero skips, and every one of the five *required* broken variants produces the diff class the Task
13 brief predicted — unlike 13a, because `wf_0003`'s and `wf_0004`'s final targets can carry real
keys. The concerns are three measurement gaps, not translation faults; the first is the one a
reviewer should spend time on.

Nothing in this work has run against a real Snowflake account or a real Alteryx engine. "Passes"
below always means: the translated procedure ran on the local DuckDB double (`lib/backend.py`)
against golden data produced by `scripts/dev/alteryx_sim.py`, and `scripts/compare.py` reported
`PASS`.

## What I implemented

### `wf_0003` — GL period close (two segments, the MERGE workflow)

| File | What it is |
|---|---|
| `samples/wf_0003/canned/segments/seg_01/proc.sql` | tools 1–3: the Input tool's own `WHERE AMOUNT <> 0` re-applied, the region Filter against the workflow constant `User.Region`, and `TRY_TO_TIMESTAMP_NTZ(POSTED, 'DD/MM/YYYY')` → `MIG_WORK.WF0003_SEG_01_OUT` |
| `samples/wf_0003/canned/segments/seg_02/proc.sql` | tools 4–10 as **three statements**: the PreSQL `DELETE`, a `MERGE` on `ACCT, PERIOD` whose `USING` subquery holds the whole tool chain, and the PostSQL `UPDATE` |
| two `contract.json` | C5; `seg_02`'s target is keyed on the Output tool's own update keys and lists all seven target columns, `LOADED_FLAG` included |
| two `translation_notes.md`, two `review.json` | one assumption per line; the tool-10 no-CTE exemption in 13a's machine-checked form |
| `canned/{intake/plan.md, analysis.md, unsupported.json, docs/migration.md}` | the intake, analyzer and documenter artifacts the mock runner replays |
| `samples/wf_0003/broken_sql/**` + `broken.json` | four variants (two required, two added) |

Sort → Unique → Multi-Row Formula → Record ID is translated as four windows over **one repeated
`ORDER BY`** (`ACCT ASC NULLS FIRST, POSTED_DT ASC NULLS FIRST, ENTRY_ID DESC NULLS LAST`), never
relying on `t4_sort`'s row order, which no dialect promises. Summarize's `Last` becomes
`MAX_BY(RUN_BAL, RECORD_ID)` — the record id is a row number, so its maximum inside a group is
that group's last row.

### `wf_0004` — inventory with a macro (three segments)

| File | What it is |
|---|---|
| `samples/wf_0004/canned/segments/seg_01/proc.sql` | tool 1 alone: the macro takes a segment of its own, so the Input tool is one too |
| `.../seg_02/proc.sql` | the macro `clean_codes.yxmc` inlined **one CTE per inner tool**, `t2_macro_m<inner id>_<inner type>`, with the question value `MinQty = 1` from the caller overriding the macro's own default of 0 |
| `.../seg_03/proc.sql` | RegEx `Parse` guarded by `REGEXP_LIKE`, the frozen-header Cross Tab as one conditional aggregate per column, the Transpose as three stacked projections, into two `Overwrite` targets |
| three `contract.json`, `translation_notes.md`, `review.json` | as above; both targets are keyed, the two work streams are not |
| `canned/{intake/plan.md, analysis.md, unsupported.json, docs/migration.md}` | as above |
| `samples/wf_0004/broken_sql/**` + `broken.json` | five variants (one required, four added) |

### `wf_0005` — vendor dedupe (tier T3, terminal `MANUAL`)

| File | What it is |
|---|---|
| `canned/parser-recovery/scripts/parsers/ext/acme_dedupe.py` | one `register_plugin` handler for `AcmeAnalytics.Dedupe.DedupeTool`: it adds `behavior`, `confidence` 0.5 and a `config` restating the tool's three configuration elements, and deliberately leaves `type: "unknown"` |
| `canned/parser-recovery/tests/parser_corpus/acme_dedupe/{fragment.yxmd, test_acme_dedupe.py, README.md}` | a sanitized three-tool fragment and five tests: it fails invariant 8 without the extension, passes every invariant with it, still refuses to guess the type, stays wired, and carries no credentials or host paths |
| `canned/parser-recovery/parsed/parse_diagnosis.md` | the diagnosis `.github/agents/parser-recovery.agent.md` requires: the exact XML fragment, the classification, the fix, and **what was deliberately not done** |
| `canned/{intake/plan.md, analysis.md, unsupported.json}` | tier T3, both blocking tools named with reasons; no `docs/migration.md` and no `segments/`, because `orchestrator/stages.ts` stops a T3 workflow after the analyzer |

`README.md` is one file more than the brief lists. `scripts/parsers/ext/README.md` requires a
fixture directory to carry one ("An extension without a fixture is not finished"), and
`tests/parser_corpus/test_corpus.py` asserts every fixture directory has a non-empty one, so an
extension shipped without it would fail the corpus the moment a human accepted it.

### Tests

`tests/test_canned_artifacts.py` — the parametrization is now split by what a workflow's canned
set actually replays, because `wf_0005` has no segment and never will:

- `TRANSLATED_WORKFLOWS` (the ones with `canned/segments/`) keep every existing check: the C4
  signature, one CTE per data node or a documented exemption, the C5 contract keys, the C3 work
  tables, the full artifact set, the documenter's nine sections, "e2e cannot skip this workflow",
  and every `broken.json` row.
- `CANNED_WORKFLOWS` keeps the three checks that are about the repository rather than about a
  translation: a valid `tier`, no catalog table name in any canned **or** broken SQL, and every
  broken variant still being a C4 procedure.
- `RECOVERY_WORKFLOWS` (the ones with `canned/parser-recovery/`) get four new checks, two of which
  actually **run the code**:
  1. `test_the_recovery_artifact_set_is_complete` — diagnosis, extension, fixture with a test, a
     fragment and a README; no `canned/segments/`; no `docs/migration.md`; and no file outside the
     two roots `orchestrator/runner.ts`'s `replayRecovery` and `orchestrator/policy.ts` allow.
  2. `test_a_recovered_workflow_declares_tier_t3_and_names_what_blocks_it` — tier T3, a non-empty
     `unsupported` list, and every tool id in it present in the parsed DAG with a reason.
  3. `test_the_recovery_extension_registers_and_explains_the_unknown_tool` — stages the canned
     tree into `tmp_path`, parses the sample's own source with and without it, and asserts the
     workflow really does go from `unknown tools without a behavior` to a clean invariants check —
     with the node still `unknown`, still carrying `raw_config`, and with `confidence < 1.0`.
  4. `test_the_recovery_corpus_test_passes_from_a_replayed_tree` — stages the same tree, imports
     the shipped `test_acme_dedupe.py` from it and runs every test function in it.

  Both run `registry.reset()` in a `finally`, as every other test in the repo that loads an
  extension does — the registry is process-global.
- One more check over `TRANSLATED_WORKFLOWS`,
  `test_every_broken_variant_is_the_canned_procedure_with_one_documented_mistake`: every file
  under `broken_sql/` opens with the `-- BROKEN ON PURPOSE` line, its header says
  `The mistake: …`, its body really differs from the canned `proc.sql` for that segment, and it
  invents no CTE the canned procedure lacks. Without it a variant could drift away from the
  procedure it was cut from — most easily by that procedure being edited afterwards and the
  variant not regenerated — and go on failing for a reason its `broken.json` row does not
  describe, with the parity suite still green. All sixteen variants in the repository (13a's seven
  and my nine) satisfy it.

The guard test now also asserts both new lists are non-empty and that every canned workflow is in
one of them, so a workflow with neither a translation nor a recovery cannot slip through silently.

`tests/test_e2e_parity.py` is unchanged. `tests/helpers.py` is unchanged.

## What I tested and the results

```
$ .venv/Scripts/python.exe -m pytest
947 passed in 67.12s (0:01:07)
```

**Zero skips** (13a left two, for `wf_0003` and `wf_0004`; both now run), no warnings, output
pristine. The Node suite is unaffected: `fnm exec --using=22 npm.cmd test` gives
`# pass 81 # fail 0 # skipped 0`.

```
$ .venv/Scripts/python.exe -m pytest tests/test_e2e_parity.py -rs
20 passed in 18.16s
```

Per-segment, against a scratch root outside the repo (`scripts/validate_segment.py`):

```
wf_0003/seg_01: PASS {'normal': 'PASS', 'period_end': 'PASS', 'empty': 'PASS', 'edge': 'PASS'} (idempotent=True)
wf_0003/seg_02: PASS {'normal': 'PASS', 'period_end': 'PASS', 'empty': 'PASS', 'edge': 'PASS'} (idempotent=True)
wf_0004/seg_01: PASS {'normal': 'PASS', 'period_end': 'PASS', 'empty': 'PASS', 'edge': 'PASS'} (idempotent=True)
wf_0004/seg_02: PASS {'normal': 'PASS', 'period_end': 'PASS', 'empty': 'PASS', 'edge': 'PASS'} (idempotent=True)
wf_0004/seg_03: PASS {'normal': 'PASS', 'period_end': 'PASS', 'empty': 'PASS', 'edge': 'PASS'} (idempotent=True)
```

`scripts/compile_check.py` exits 0 for all five segments (1, 3, 1, 1 and 2 statements).

For `wf_0005`, `scripts/parse.py wf_0005 --check --root <scratch>` goes from
`INVARIANT_VIOLATION` (`unknown tools without a behavior: 1 of 4 data nodes (max 10%): ['2']`) to
`PARSED: 4 nodes, 0 aliases scrubbed`, exit 0, once the canned extension is dropped into
`<scratch>/scripts/parsers/ext/`.

### The new tests are not vacuous

TDD was not possible in the usual order (the artifacts have to exist before a test can check
them), so I proved the tests bite by mutating one artifact at a time, running the suite, and
restoring the file from an in-memory copy in a `finally` — no `git stash`, no `git checkout --`,
and `git status` was clean afterwards each time.

| Mutation | Test that failed |
|---|---|
| the extension stops setting `behavior` | `test_the_recovery_extension_registers_and_explains_the_unknown_tool[wf_0005]` |
| the extension claims `confidence: 1.0` | same |
| the extension guesses `type: "unique"` | same |
| the corpus fragment keeps a `C:\` host path | `test_the_recovery_corpus_test_passes_from_a_replayed_tree[wf_0005]` |
| `unsupported.json` drops to tier `T1` | `test_a_recovered_workflow_declares_tier_t3_and_names_what_blocks_it[wf_0005]` |
| `unsupported.json` names a tool id the DAG lacks | same |
| a `broken.json` row records the wrong class | `test_broken_migration_fails_with_the_right_class[wf_0003-02_postsql_not_translated.sql]` |
| `wf_0004/seg_02`'s macro CTEs renamed away from `t2_macro_*` | `test_every_data_node_has_a_cte_or_a_documented_exemption[wf_0004]` |
| every `-- tool 9:` comment removed from `wf_0003/seg_02` | `test_every_data_node_has_a_cte_or_a_documented_exemption[wf_0003]` |
| `wf_0003/seg_02`'s tool-10 no-CTE exemption reworded | same, `[wf_0003]` |
| `wf_0003/seg_02`'s target keys emptied | `test_every_contract_has_the_c5_keys[wf_0003]` |
| `wf_0003/seg_01`'s DateTime format flipped to `MM/DD/YYYY` | `test_hand_migration_passes_every_golden_set[wf_0003]` |
| `wf_0004/seg_03`'s Transpose filtered to non-NULL cells | `test_hand_migration_passes_every_golden_set[wf_0004]` |
| a broken variant loses its `BROKEN ON PURPOSE` header | `test_every_broken_variant_is_the_canned_procedure_with_one_documented_mistake[wf_0003]` |
| a broken variant's header stops saying what the mistake is | same, `[wf_0004]` |
| a broken variant invents a CTE the canned procedure lacks | same, `[wf_0004]` |

Two mutations were **not** caught, and both are informative rather than test gaps:

- Setting the extension's `in_anchors` to `[]`. `parse._anchors_from_connections` refills an
  `unknown` tool's anchors from the connections that actually use them, so the node stays wired —
  which is the behaviour invariant 2 wants, not a hole.
- Replacing `MAX_BY(RUN_BAL, RECORD_ID)` with `MAX(RUN_BAL)` in `wf_0003/seg_02`. That one is a
  real gap in the *golden data* and is Concern 1 below.

## Observed vs intended, for every broken variant

Every variant FAILs and every one has `needs_human: false`. `expect` in `broken.json` is what the
validator **now** reports (coordinator note 1); where the Task 13 brief predicted the same thing,
`intended` records that agreement explicitly.

### `wf_0003` (4 variants)

| File | Brief's prediction | Observed (recorded in `broken.json`) | Same? |
|---|---|---|---|
| `seg_01/01_datetime_reads_month_first.sql` | — (added) | `LOGIC` / `['POSTED_DT']` / `3_Output`, `paired_by: nearest_match`, suspect `t3_datetime` | — |
| `seg_02/01_unique_keeps_the_lowest_entry_id.sql` | `LOGIC` / `TOTAL` | `LOGIC` / `['CLOSING_BAL', 'TOTAL']` / `9_Output`, suspect `t9_summarize` | **yes** |
| `seg_02/02_postsql_not_translated.sql` | `NULL_SEMANTICS` / `LOADED_FLAG` | `NULL_SEMANTICS` / `['LOADED_FLAG']` / `9_Output` | **yes** |
| `seg_02/03_presql_not_translated.sql` | — (added) | `LOGIC` / `[]` / `9_Output`, scope `rows` (the 2019-12 row the DELETE should have removed) | — |

The Unique variant's cluster names `CLOSING_BAL` as well as `TOTAL` because both are written by
tool 9 and fail the same way, and `compare.py` groups columns that fail identically into one
cluster. `CLOSING_BAL` alone would have been `ORDERING` (the contract calls it order-dependent);
the pair is not, so the per-column classification wins. The e2e test's `⊆` check passes either
way.

### `wf_0004` (5 variants)

| File | Brief's prediction | Observed (recorded in `broken.json`) | Same? |
|---|---|---|---|
| `seg_02/01_macro_filter_dropped.sql` | `LOGIC` | `LOGIC` / `[]` / `2_Output5`, scope `rows`; **plus** a second cluster `NULL_SEMANTICS` / `['QTY']` from the nullability check | **yes** |
| `seg_02/02_macro_uses_the_question_default.sql` | — (added) | `LOGIC` / `[]` / `2_Output5`, scope `rows` (one row, where 01 has two) | — |
| `seg_02/03_macro_regex_nulls_unmatched.sql` | — (added) | `NULL_SEMANTICS` / `['SKU']` / `2_Output5`, twice — once from nullability, once as a value difference with `paired_by: nearest_match` | — |
| `seg_03/01_cross_tab_fills_missing_with_zero.sql` | — (added) | `NULL_SEMANTICS` / `['EAST', 'NORTH', 'WEST']` / `4_Output` suspect `t4_cross_tab`, **plus** `NULL_SEMANTICS` / `['VALUE']` / `5_Output` suspect `t5_transpose` | — |
| `seg_03/02_transpose_drops_null_values.sql` | — (added) | `NULL_SEMANTICS` / `['VALUE']` / `5_Output`, scope `rows` | — |

One variant I wrote and then **deleted**: `02_macro_regex_is_case_sensitive.sql`, which dropped
the `'i'` parameter from the macro's `REGEXP_REPLACE`. It produced **no difference at all** and
so could not be a fixture (the e2e test asserts `verdict == "FAIL"`). The reason is in the pattern
itself: `[A-Za-z]+` already matches both cases, so the `CaseInsensitve` flag is redundant for
*this* regex. It is replaced by `02_macro_uses_the_question_default.sql`, which covers the
behaviour `samples/wf_0004/README.md` says tool 2 is there to exercise (a question value
overriding an interface default), and by `03_macro_regex_nulls_unmatched.sql` for `CopyUnmatched`.

## Snowflake-validity notes, and the local-runtime facts that forced a spelling

Everything below is stated in the relevant `translation_notes.md` too.

**`MERGE`.** Valid Snowflake, and it runs on the local runtime — but the local runtime rejects a
**qualified** assignment target: `WHEN MATCHED THEN UPDATE SET T.TOTAL = S.TOTAL` fails with
`Parser Error: Qualified column names in UPDATE .. SET not supported`. The unqualified form
(`SET TOTAL = S.TOTAL`) is valid Snowflake and is what both accept, so that is what the procedure
uses. `MERGE INTO IDENTIFIER(…) AS T USING (WITH … SELECT …) AS S ON …` binds, folds and
transpiles; `backend._target_schemas` already knew about `exp.Merge`.

**`TRY_TO_TIMESTAMP_NTZ(x, 'DD/MM/YYYY')`** keeps its format argument through sqlglot (it becomes
`TRY_STRPTIME(x, '%d/%m/%Y')`), unlike the one-argument `TRY_TO_DATE` whose format 13a found is
dropped. A day-first format could not have been translated correctly without it. Verified against
`31/02/2026`, which gives NULL on both sides.

**`TO_CHAR(<timestamp>, 'YYYY-MM-DD')`** keeps its format too (it becomes `STRFTIME`), where a
plain cast to text would print a full timestamp and never equal the date literal.

**`REGEXP_SUBSTR` returns an empty string, not NULL, on the local runtime** when nothing matched;
Snowflake returns NULL. Rather than write something wrong on Snowflake, `wf_0004/seg_03` tests the
match explicitly with `REGEXP_LIKE` and writes the NULL out — correct on both. (`NULLIF(…, '')`
would have worked on this data but is wrong for a pattern whose group can capture an empty
string.) Note that Snowflake's `REGEXP_LIKE` requires the pattern to match the **whole** subject
where the RegEx tool searches; both patterns here are anchored `^…$`, so the two rules coincide.

**`REGEXP_REPLACE(subject, pattern, replacement, 1, 0, 'i')`** — the six-argument Snowflake form —
transpiles to the local four-argument form with flags `ig`. Alteryx's `$1`/`$2` become `\1`/`\2`
with the backslashes doubled in the SQL literal, which is Snowflake's own rule.

**`QUALIFY` and `MAX_BY`** are documented Snowflake SQL and both run locally.

**Snowflake's multiplication scale rule** (`min(S1+S2, max(S1,S2,12))`, not the local runtime's
`S1+S2`) is **not reached by any of these five segments**: nothing in `wf_0003` or `wf_0004`
multiplies or divides. Both only add `NUMBER(19,2)` values, where the two rules agree. The rule is
still stated in `wf_0003/seg_02`'s notes so the next translator does not have to rediscover it.

**`CREATE OR REPLACE [TRANSIENT] TABLE … AS` does not enforce the contract's `VARCHAR(n)` /
`NUMBER(p,s)` widths.** Stated in every one of the five `translation_notes.md`, and it matters
most for `wf_0004`, whose `edge` set has a `SKU` and a `NOTE` sitting exactly on their limits.

**A segment ending in two or more Output tools duplicates its shared upstream CTEs per statement**
(C4 forbids variables). That is `wf_0004/seg_03` — tools 3 and 4 appear in both statements — and
it is called out in the notes and in the migration doc so a reviewer does not read it as two
different translations. `wf_0003/seg_02` has the opposite shape: one Output tool becomes three
statements.

**Output tools use the machine-checked line** `- tool <id> has no CTE: <reason>` in
`translation_notes.md`, in 13a's exact form. Three of them: `wf_0003` tool 10, `wf_0004` tools 6
and 7.

## Brief corrections

### 1. The Task 13 brief's predicted classes all held this time

13a had to record three of five required variants as `UNKNOWN`. That does not recur here, for a
structural reason worth writing down: **`wf_0003`'s and `wf_0004`'s final targets can carry real
keys.** `wf_0003`'s target is keyed on the Output tool's own update keys (`ACCT, PERIOD`), and the
`edge` set's duplicate pair (`DUP`) collapses in the Summarize before it reaches the target;
`wf_0004`'s two targets are keyed on the Cross Tab's group-by fields and on `SKU, NAME`, and the
`edge` set's `DUP-1` pair likewise collapses in the Cross Tab. So the keyed diff runs, and it can
name a column. Only the three **work streams** (`3_Output`, `1_Output`, `2_Output5`) are keyless,
and there Task 7b's nearest-match pairing still named `POSTED_DT` and `SKU` for me.

### 2. One added variant could not be written as planned, and one required variant is reported twice

Both are covered above: the case-sensitivity variant produced no difference and was replaced, and
`wf_0004/seg_02/01_macro_filter_dropped.sql` produces two clusters (the row-presence `LOGIC` the
brief predicted, plus a `NULL_SEMANTICS` on `QTY` from the nullability check, because the row the
filter should have dropped has a NULL quantity). `broken.json`'s `note` says so for both.

### 3. `row_relation` follows the spec's vocabulary, which differs from 13a's files

Program spec §8 gives `1:1 | filter | aggregate | expand`. I used `filter`, `aggregate` and `1:1`.
13a's `wf_0002` contracts use `one_to_one` and `fan_out`, which are outside that list. I did not
change 13a's files — it is their call — but a reviewer may want them aligned.

### 4. `canned/segments/` is deliberately absent for `wf_0005`, and `orchestrator/stages.ts` will
notice

The brief is explicit that `wf_0005` gets no `proc.sql`, and it asks for no `contract.json` either.
That is right for the artifact set: there is nothing to translate and nothing to validate. But
`stageAnalyze` in `orchestrator/stages.ts` runs `scripts/segment.py` **before** it checks the
tier, and its verification callback requires a `contract.json` for every segment in
`segments/order.json`. `segment.py` succeeds for `wf_0005` once the parse is recovered (one
segment, `seg_01`, holding tools 1–4), and `MockRunner.replayAnalyzer` copies contracts from
`canned/segments/*/contract.json`, which `wf_0005` does not have — so an offline full run against
the real `samples/` would escalate `wf_0005` at analyze instead of reaching `MANUAL`. The
orchestrator's own node tests do not see this: `orchestrator/test/fakes.ts` writes its own
synthetic canned tree and gives even its T3 workflow a `contract.json`. This is a Task 15/17
integration decision — either the analyzer stage should check the tier before it demands contracts,
or `wf_0005` needs a contract with no procedure — and I did not invent one, because adding a
contract without a `proc.sql` would break `test_the_canned_artifact_set_is_complete` for exactly
the right reason.

## Self-review findings and concerns

1. **No golden set distinguishes Summarize's `Last` from a plain `MAX`.** Measured, not guessed:
   replacing `MAX_BY(RUN_BAL, RECORD_ID)` with `MAX(RUN_BAL)` in `wf_0003/seg_02` still passes all
   four sets. Every `(ACCT, PERIOD)` group's running balance happens to reach its largest value on
   its last row; the only multi-row group with a mid-group dip is `(4000, 2026-08)` in `normal`,
   whose balances run 150, 100, 300 — the dip is not at the end. So the `Last` translation rests on
   `docs/reference/simulator-semantics.md` §7 rather than on a passing test, and **no broken
   variant can demonstrate it**, because a variant has to FAIL and this one does not. Recorded in
   `wf_0003/seg_02/translation_notes.md` and under Open items in its migration doc. Fixing it
   properly means changing golden data, which is Task 3/9's and not mine.
2. **`nullable: false` is a deliberate risk on eight columns.** `wf_0003/seg_01`'s `AMOUNT` and
   `REGION`; `wf_0004/seg_02`'s `SKU`, `WAREHOUSE` and `QTY`; `SKU` on both `wf_0004/seg_03`
   targets and `NAME` on the long one. Each is provably true across all four golden sets and each
   is a real check — two of the broken variants trip one — but a NULL arriving in the *golden* data
   of such a column would be a `GOLDEN_DATA` cluster failing the correct procedure.
3. **The macro's CTE naming is a compromise.** The coordinator's note 4 says "CTEs named for the
   MACRO'S inner tools with the macro tool id in the comment". A literal reading (`m2_regex`, with
   tool 2 only in the comment) would have needed a `- tool 2 has no CTE:` exemption, which would
   have weakened the reviewer's "every data node has a CTE" check for a node that has five. I used
   `t2_macro_m<inner id>_<inner type>` instead: the parent id is in the name *and* in every
   comment, the inner tool id and type are in the name, and nothing is exempted. Explained in
   `wf_0004/seg_02/translation_notes.md`; say the word if the literal form is wanted.
4. **`config` on the unknown tool.** `acme_dedupe.py` fills `config` with the three
   `<Configuration>` children (`key_field`, `keep`, `date_field`). The agent spec mentions only
   `raw_config`, `behavior` and `confidence`, so this is one step beyond it — but it is a
   restatement of the XML rather than an interpretation, `raw_config` is untouched, and the
   analyzer gets something structured instead of a string. Easy to drop if a reviewer disagrees.
5. **The `-- tool <id>` comment check is per-file, not per-CTE.** A mutation that removed one of
   `wf_0004/seg_02`'s five `-- tool 2 (…)` comments was not caught, because four others remain and
   `_tool_comment_re` searches the whole procedure text. That is 13a's test as written and I did
   not tighten it (tightening it would need the comment to be located relative to its CTE, which is
   a real change to a passing test). Removing *every* comment for a tool is caught.
6. **I did not write a mechanical test for "the canned prose contains no number about data and no
   claim that anything ran on Snowflake or Alteryx."** Same reasoning as 13a: a phrase blocklist
   gives false confidence and fires on legitimate text. I wrote the artifacts to that rule by hand
   and grepped for `<digits> rows/records/groups/differences` across all nine new prose files
   (nothing found); it is something a human should read rather than a test.
7. **`wf_0005`'s corpus fixture cannot simply be dropped into `tests/parser_corpus/`.**
   `test_corpus.py` keeps a literal `FIXTURES` set and asserts the directories on disk match it
   exactly, so whoever accepts `acme_dedupe` permanently has to add it there in the same change.
   Said in both the fixture's README and the diagnosis, so it cannot be missed.

## The three things a reviewer should check hardest

1. **`wf_0003/seg_02`'s `MERGE` and its two bracketing statements** — that the PreSQL/write/PostSQL
   order matches Alteryx's, that `LOADED_FLAG` is genuinely the only target column the MERGE must
   not touch, and that a NULL update key really should insert (the `edge` set's NULL-`ACCT` group
   is the only witness). Everything about this segment's correctness is in three statements that
   the golden data exercises once each.
2. **Concern 1 above: `Last` versus `MAX`.** The data does not separate them, so the translation is
   right only if `simulator-semantics.md` §7 is right. If a reviewer wants it proved, it needs a
   golden row, not a code change.
3. **`wf_0004/seg_02`'s macro inlining** — that `MinQty = 1` (the caller) and not `0` (the macro),
   that `CopyUnmatched` really means "keep the subject" and `REGEXP_REPLACE` really does that, and
   that the CTE naming compromise in Concern 3 is acceptable. The macro is the one place in these
   samples where a tool's meaning lives in another file.

## Commits

| SHA | Subject |
|---|---|
| `1ca7e20` | `wip: wf_0003 hand migration - both segments pass every golden set` |
| `e7d8aa8` | `wip: wf_0003 broken variants and canned agent artifacts` |
| `4d8fd04` | `wip: wf_0004 hand migration - all three segments pass every golden set` |
| `6faa3ef` | `wip: wf_0004 broken variants and canned agent artifacts` |
| `a33638c` | `wip: wf_0005 parser-recovery artifacts and the tests that run them` |
| `f34d4de` | `feat: hand-migrated procedures, contracts, broken variants and canned outputs for wf_0003-wf_0005` |

The five `wip:` commits could **not** be squashed into the final one: Task 12b's merge (`e0564ac`)
and its two commits sit between them on `feat/pipeline-m0-m2`, and rewriting that history would
have moved commits another agent's work is built on. The final commit therefore carries real
content of its own — the broken-variant integrity check — rather than being an empty marker.

Task 12b's commits (`8bd0baf`, `5e7e2f6`, `e0564ac`) landed on `feat/pipeline-m0-m2` between mine;
the two sets of changes are disjoint and the full suite is green over both.

## Files changed

New:
- `samples/wf_0003/canned/{intake/plan.md, analysis.md, unsupported.json, docs/migration.md}`
- `samples/wf_0003/canned/segments/seg_0{1,2}/{contract.json, proc.sql, translation_notes.md, review.json}`
- `samples/wf_0003/broken_sql/broken.json`, `.../seg_01/01_*.sql`, `.../seg_02/0{1,2,3}_*.sql`
- `samples/wf_0004/canned/{intake/plan.md, analysis.md, unsupported.json, docs/migration.md}`
- `samples/wf_0004/canned/segments/seg_0{1,2,3}/{contract.json, proc.sql, translation_notes.md, review.json}`
- `samples/wf_0004/broken_sql/broken.json`, `.../seg_02/0{1,2,3}_*.sql`, `.../seg_03/0{1,2}_*.sql`
- `samples/wf_0005/canned/{intake/plan.md, analysis.md, unsupported.json}`
- `samples/wf_0005/canned/parser-recovery/scripts/parsers/ext/acme_dedupe.py`
- `samples/wf_0005/canned/parser-recovery/tests/parser_corpus/acme_dedupe/{fragment.yxmd, test_acme_dedupe.py, README.md}`
- `samples/wf_0005/canned/parser-recovery/parsed/parse_diagnosis.md`

Modified:
- `tests/test_canned_artifacts.py` — the split parametrization, four new recovery tests, the
  broken-variant integrity check, and a stronger guard test.

Nothing under `scripts/`, `orchestrator/`, `catalog/`, `mappings/`, `cookbook/`,
`tests/cookbook_examples/` or any golden data was touched.

## Fix round 1

**Status: DONE.** All four reviewer findings fixed. Full suite green, 948 passed / 0 skipped (947
before this round, +1 from the new broken-variant fixture), no warnings.

### 1. GOLDEN-DATA GAP — Summarize `Last` vs `MAX` (Important)

The reviewer was right: replacing `MAX_BY(RUN_BAL, RECORD_ID)` with `MAX(RUN_BAL)` in
`samples/wf_0003/canned/segments/seg_02/proc.sql` passed all four golden sets before this fix,
because every `(ACCT, PERIOD)` group's running balance happened to peak on its own last row.

**Files changed:**
- `samples/_tools/make_golden_inputs.py` — added one row to `LEDGER_NORMAL`, `ENTRY_ID` 14:
  `("4000", "2026-08", "01/09/2026", D("-400.00"), "EMEA", 14)`, exactly as the reviewer specified,
  with a comment explaining what it proves. `ENTRY_ID` 14 was unused (the set ran 1–13).
- Ran `.venv/Scripts/python.exe samples/_tools/make_golden_inputs.py`. `git diff --stat` on the
  regeneration (before any other edit) showed exactly one generated file touched:
  `samples/wf_0003/golden_inputs/normal/1.csv` (+1 line: the new row). The `.schema.json` sidecar,
  `sample.json`, and every other workflow's golden data came back byte-identical — the field list
  didn't change, so nothing else could drift.
- `samples/wf_0003/README.md` — updated the `normal` row/group counts (13→14 rows in, nine→ten
  rows reach the summarize; still six groups) and added a table row for `ENTRY_ID` 14 explaining
  what it separates.
- `samples/wf_0003/canned/segments/seg_02/translation_notes.md` — rewrote the "Measured gap ...
  worth a reviewer's attention" line (previously admitting no golden set could tell `Last` from
  `MAX`) to describe the fix: the new row, why it separates them, and the broken variant that now
  demonstrates it. `grep -rn -i "MAX_BY|separat" samples/wf_0003` after the edit shows no remaining
  claim that the two are indistinguishable.
- `samples/wf_0003/broken_sql/seg_02/04_last_translated_as_max.sql` — new broken variant: a full
  copy of the canned `seg_02/proc.sql` with exactly `MAX_BY(RUN_BAL, RECORD_ID)` → `MAX(RUN_BAL)`
  in `t9_summarize`'s `CLOSING_BAL`, headed with the same `BROKEN ON PURPOSE` / `The mistake: ...`
  style the sibling variants use.
- `samples/wf_0003/broken_sql/broken.json` — new entry for the variant above, `golden_set: normal`,
  recording what was **observed** (not forced).

**Evidence (validator output).** Both runs used a standalone driver built from
`tests/helpers.py::prepare_workflow` (scripts/ is off-limits to edit, so this was a scratch script
under my scratchpad directory, not a repo file), against a fresh scratch `Repo` per run:

Correct `seg_02/proc.sql`, all four golden sets:
```
"sets": {"normal": "PASS", "period_end": "PASS", "empty": "PASS", "edge": "PASS"}
"idempotent": true, "idempotency_diff": []
```

Broken variant `04_last_translated_as_max.sql`, `normal` set:
```
"verdict": "FAIL",
"diff_clusters": [{
  "class": "LOGIC", "columns": ["CLOSING_BAL"], "stream": "9_Output",
  "suspect_cte": "t9_summarize", "scope": "columns",
  "example_rows": [{"key": {"ACCT": "4000", "PERIOD": "2026-08"},
                     "expected": {"CLOSING_BAL": -100.0}, "actual": {"CLOSING_BAL": 300.0}}]
}],
"needs_human": false,
"sets": {"normal": "FAIL", "period_end": "PASS", "empty": "PASS", "edge": "PASS"}
```
This matches the reviewer's reproduction exactly: class `LOGIC`, column `CLOSING_BAL`, stream
`9_Output`, expected `-100.0` vs actual `300.0`. `04_last_translated_as_max.sql` is the only new
class in `DIFF_CLASSES` terms (`LOGIC`) — not forced; it is what `compare.py` actually reported.

**Regression check for the changed golden set:**
`tests/test_samples_wellformed.py` (69 passed), `tests/test_build_samples.py` +
`tests/test_alteryx_sim.py` (76 passed together), `tests/test_e2e_parity.py` (21 passed, up from
20 — the new broken-variant case), `tests/test_canned_artifacts.py` (56 passed) all green. Nothing
else in the repo reads `LEDGER_NORMAL` besides the one `write(...)` call the generator makes for
it, so no other sample or test depended on the old 13-row shape.

### 2. FALSE SNOWFLAKE CLAIM — Transpose/UNPIVOT (Important)

`cookbook/transpose.md` documents and tests `UNPIVOT INCLUDE NULLS (...)` keeping NULLs, so "cannot
be written as UNPIVOT" was false — a bare `UNPIVOT` drops NULLs, `UNPIVOT INCLUDE NULLS` keeps
them, and this segment's `UNION ALL` (unchanged, still correct) keeps them too.

**Files changed** (found via `grep -rn -i "unpivot" samples/wf_0004`, all five files the brief
named, plus the corresponding analysis.md table row was in a different format than expected so it
took two edits):
- `samples/wf_0004/canned/segments/seg_03/translation_notes.md` (~line 20)
- `samples/wf_0004/canned/segments/seg_03/contract.json` (~line 105, the tool-5 `parity_risks` note)
- `samples/wf_0004/canned/docs/migration.md` (~line 85)
- `samples/wf_0004/canned/analysis.md` (~line 86, the tool-5 `NULL_SEMANTICS` table row)
- `samples/wf_0004/canned/intake/plan.md` (~line 73)

Each now says: a bare `UNPIVOT` drops a NULL-valued row entirely; `UNPIVOT INCLUDE NULLS` keeps it
(citing `cookbook/transpose.md`); this segment uses `UNION ALL`, which also keeps it. The
procedure's `UNION ALL` was not touched — it's a correct choice, just no longer justified by a
false claim of impossibility.

Checked the broken variant header
`samples/wf_0004/broken_sql/seg_03/02_transpose_drops_null_values.sql` for the same claim: it only
says a bare `UNPIVOT` drops NULLs "by default" (true, not an impossibility claim), so it needed no
change. `grep -rn -i "cannot be\|is the wrong construct\|is the wrong one" samples/wf_0004` returns
nothing after the fix.

**Regression check:** `tests/test_canned_artifacts.py -k wf_0004` (12 passed) — the JSON note
change didn't break contract shape checks.

### 3. MINOR — MERGE nondeterminism assumption

Added a line to `samples/wf_0003/canned/segments/seg_02/translation_notes.md`, next to the other
`MERGE`-related assumption, stating that the `USING` subquery's final `SELECT` (`t9_summarize`) is
grouped by exactly `ACCT, PERIOD` — the same pair the `MERGE ON` clause joins on — so it can never
produce more than one source row per key, and this `MERGE` can never hit Snowflake's
nondeterministic-merge error. Marked "Needs verification on Snowflake", one assumption per line as
the rest of the file does.

### 4. MINOR / RULING — `row_relation` vocabulary

`docs/spec/00-README.md` (~line 347) fixes the vocabulary at `1:1 | filter | aggregate | expand`.
`samples/wf_0002/canned/segments/{seg_01,seg_02}/contract.json` used `one_to_one` and `seg_03`
used `fan_out`.

**TDD evidence:**
- RED: added `ROW_RELATIONS = frozenset({"1:1", "filter", "aggregate", "expand"})` and an assertion
  in `tests/test_canned_artifacts.py::test_every_contract_has_the_c5_keys` (next to the existing C5
  key checks) *before* touching any contract. `.venv/Scripts/python.exe -m pytest
  tests/test_canned_artifacts.py -k test_every_contract_has_the_c5_keys -v` failed exactly on
  `wf_0002` (`seg_01`, `row_relation is 'one_to_one'`), all others passed — confirming the test
  catches the real bug and nothing else.
- GREEN: changed `seg_01`/`seg_02`'s `row_relation` to `"1:1"` and `seg_03`'s to `"expand"`; also
  updated `samples/wf_0002/canned/analysis.md`'s prose (`one_to_one`/`fan_out` → `1:1`/`expand`) so
  the docs don't contradict the contract they describe. Re-ran the same test: 56 passed.

### Full suite

```
.venv/Scripts/python.exe -m pytest
948 passed in 68.78s (0:01:08)
```
No warnings, no skips. `git status` is clean except the files this round changed (listed below),
all staged for the fix-round-1 commit.

### Files changed (fix round 1)

Modified:
- `samples/_tools/make_golden_inputs.py`
- `samples/wf_0003/golden_inputs/normal/1.csv` (generated — regenerated, never hand-edited)
- `samples/wf_0003/README.md`
- `samples/wf_0003/canned/segments/seg_02/translation_notes.md`
- `samples/wf_0003/broken_sql/broken.json`
- `samples/wf_0002/canned/segments/seg_0{1,2,3}/contract.json`
- `samples/wf_0002/canned/analysis.md`
- `samples/wf_0004/canned/segments/seg_03/{contract.json, translation_notes.md}`
- `samples/wf_0004/canned/{analysis.md, docs/migration.md, intake/plan.md}`
- `tests/test_canned_artifacts.py`

New:
- `samples/wf_0003/broken_sql/seg_02/04_last_translated_as_max.sql`

Nothing under `scripts/` was touched; the validator-run driver used to gather the evidence above
lived only in my scratchpad directory, never in the repo.
