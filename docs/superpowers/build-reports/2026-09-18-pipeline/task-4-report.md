# Task 4 — parser, extension registry, invariants, corpus

**Status: DONE_WITH_CONCERNS** (the work is complete and green; the concerns are contract gaps I
had to fill and two deliberate departures from literal text, all listed below).
Commit: `039826b feat: Alteryx XML parser with extension registry, invariants and corpus`.

## What I implemented

| File | What it does |
|---|---|
| `scripts/parsers/plugin_map.py` | `PLUGIN_TYPES` (all 26 rows of dag-contract §2, in the contract's order), the `AlteryxGuiToolkit.Questions.*` prefix rule, `BUILTIN_MACROS`, `ANCHORS` (type → in anchors + XML→canonical out anchors), `classify`, and three readers (`anchors_of`, `canonical_out_anchor`, `out_anchor_names`). |
| `scripts/parsers/tool_config.py` | `parse_config(tool_type, configuration)` for every type §4 defines, plus `scrub`/`scrub_xml`/`split_source`/`alias_of` for §6. Every parser is total: a missing element or attribute is `null`, never an exception and never a guess. |
| `scripts/parsers/registry.py` | `register_plugin`, `register_element`, `plugin_handler`, `element_handlers`, `merge`, `load_extensions`, `reset`. Plugin handler results are filtered to `MERGEABLE_KEYS` (`type, config, in_anchors, out_anchors, behavior, confidence`), so an extension can explain a tool but not rewrite a node's identity. |
| `scripts/parsers/ext/README.md` | The registry API for the parser-recovery agent: what each handler receives and returns, the "never invent semantics / never drop a node / never write files" rules, that extensions never edit `parse.py`, and that each one ships with a corpus fixture *and* a test. |
| `scripts/parse.py` | `decode_xml`, `parse_file`, `run`, `workflow_file`, `main`. Recurses `<ChildNodes>` carrying `container_id`, resolves and parses macros recursively, canonicalises anchors, builds edges with `dst_order`/`wireless`, applies registered handlers, unzips `.yxzp` in place, writes `parsed/dag.json` + `parsed/parse_report.json`, and rewrites the source files scrubbed on a clean parse. |
| `scripts/invariants.py` | `check(xml_text, dag)` — the program spec's 1–6 with `nodes` keyed by `str(tool_id)` throughout, plus (7) every `join` is fed exactly once on `Left` and once on `Right`, and (8) `unknown` nodes lacking a `behavior` key are at most 10% of the data nodes. A `--root`-aware CLI that prints violations and never writes (see "Departures"). |
| `tests/parser_corpus/{bom,cp1252,yxwz,nested_containers,yxzp,locked}/` | Six fixtures, each with a `README.md` saying what it guards and that it is synthetic. |

Decisions handed to me were honoured: union `dst_order` from the Destination's `name="#N"`
(everything else 1), AMP from `.//RunE2` under the document's `<Properties>` or `RunE2="T"` on the
root, Data Cleansing and macros classified from `<EngineSettings Macro=…>`, `PreSQL`/`PostSQL`/
`UpdateKeys`/`OutputOption` found with `.//`, sink tools keeping `meta["Output"]`, `source=`
ignored on `RecordInfo` fields, container `<Caption>` used as the annotation when there is no
annotation text, and `"wireless": false` on every edge unless `Wireless="True"`.

## TDD evidence

**RED** — `.venv/Scripts/python.exe -m pytest tests/test_parse.py tests/test_invariants.py tests/parser_corpus/test_corpus.py`

```
tests\test_invariants.py:12: in <module>
    import invariants
E   ModuleNotFoundError: No module named 'invariants'
ERROR tests/test_parse.py
ERROR tests/test_invariants.py
ERROR tests/parser_corpus/test_corpus.py
!!!!!!!!!!!!!!!!!!! Interrupted: 3 errors during collection !!!!!!!!!!!!!!!!!!!
3 errors in 0.18s
```

Expected: the brief's Step 2 says all three files fail on import, because `parse.py`,
`invariants.py` and `parsers/` did not exist yet.

**GREEN** — same command after implementing:

```
............................................................... [100%]
63 passed in 0.32s
```

Full suite, `.venv/Scripts/python.exe -m pytest`:

```
166 passed in 0.52s
```

`.venv/Scripts/python.exe -W error -m pytest` also passes (166), so the output is pristine — no
`DeprecationWarning` from ElementTree truthiness or anywhere else.

## What I tested

- **The brief's seven tests, verbatim** (`tests/test_parse.py`): filter anchors and formula order,
  nested containers + the Cleanse macro, scrubbing everywhere, recursive macro parsing, a missing
  macro flagged not dropped, the unknown-share invariant cleared by an extension, and
  `run` writing a report and scrubbing the source (`node_count == 12`).
- **Prose-only behaviour** (31 more in the same file): `decode_xml` for BOM / declared encoding /
  UTF-8 / cp1252 fallback; document-level fields including `engine: "E1"`; container captions and
  the fact that containers, comments, interface and action tools never appear in `edges`;
  `wireless` and `dst_order` defaults and a hand-built wireless connection; input/output config for
  yxdb, csv and db sources; sink `meta`; canonical `meta` keys (`T/F`, `L/J/R`, `U/D`); scrubbed
  `raw_config`; select/filter/formula/join/union; sort/unique/multi-row/record-id/datetime/
  summarize; regex/cross-tab/transpose (including inside the macro's sub-dag); the full Data
  Cleansing value map; macro anchors from the macro's own tools and from connections when it is
  unresolved; unknown tools keeping `raw_config` and getting anchors from connections; all four
  migratable samples satisfying every invariant; `register_element`; `load_extensions`/`reset`; an
  extension loaded from `<root>/scripts/parsers/ext/` by `run`; `run`'s report keys and exact
  values, `INVARIANT_VIOLATION` without dropping the dag, `check=False`, `RECOVERED` preservation,
  `FAILED` with the exception message, CLI exit codes 0/1/2, and a second `run` over the
  already-scrubbed source producing the same config (idempotence); plus a unit test for the §4
  shapes no sample reaches (`sample`, an `aka:` alias, `record_id` position `1`, regex `Match`,
  an updating multi-row formula, `ByPos`, all five `OutputOption` spellings and an unknown one).
- **Invariants** (`tests/test_invariants.py`, 13): a valid hand-built dag; ids compared as strings;
  one failing case per invariant — missing node, dangling edge, bad anchor, `type: None`, macro
  without a path, empty input *and* output source, cycle, join with one input and with two Lefts,
  unknown share (and cleared by a `behavior`); non-data nodes not diluting the share; no
  division by zero when there are no data nodes.
- **Corpus** (`tests/parser_corpus/test_corpus.py`, 12): `bom`, `cp1252`, `yxwz` and
  `nested_containers` parse with `invariants.check == []`; the BOM bytes and the cp1252 file's
  invalid-UTF-8 bytes are asserted directly; `yxzp` is zipped at test time, unzipped by `run` and
  parsed with its macro resolved; a package member escaping `source/` fails the parse; `locked`
  reports `QUARANTINED` with reason `locked` and writes no `dag.json`; every fixture directory is
  covered and documented.

Nothing under `samples/` was edited (`git status` clean there before and after), and no test
writes into the repo's `workflows/`.

## Contract gaps I had to fill

These are shapes `dag-contract.md` does not pin. I chose the option that kept the rest of the
contract consistent; each is a candidate for a follow-up line in the contract.

1. **`scrub_xml` next to `scrub`.** §6 says the connection string is replaced by `<scrubbed:alias>`
   in `config`, `raw_config` *and* the rewritten source file. Writing that token literally into XML
   makes the file ill-formed (`unbound prefix` on re-parse), so `raw_config` and the rewritten
   source get the XML-escaped token `&lt;scrubbed:alias&gt;`, which parses back to exactly the
   plain token. `scrub(text) -> (text, aliases)` keeps the brief's signature; `scrub_xml` is the
   same function with the escaped token. A test parses a scrubbed source again and gets an
   identical `config`.
2. **`interface` tools get a config** of `{"name", "type", "default"}`, and a macro node's
   `interface` list is those configs from its sub-dag. §4 fixes the list's shape but not where it
   comes from; this keeps the question in one place and makes the sub-dag self-describing.
3. **`union` `ByPos` → `"position"`.** §4 only gives `ByName → "name"`.
4. **`regex` `output_fields` entries** are `{"name", "type", "size"}` (the `meta` field shape minus
   `scale`); §4 names the source XML but not the parsed shape.
5. **An unrecognised `OutputOption` leaves `write_mode: null`** rather than defaulting to
   `overwrite` (a file output with *no* `OutputOption` does default to `overwrite`, per §4).
   Guessing here is how a migration silently turns an update into a truncate.
6. **`parse_report.json` carries a `reason`** (null except for `QUARANTINED: "locked"`), because
   the brief requires a reason and the program schema has no field for one. Report keys are
   therefore `status, errors, node_count, unknown_share, scrubbed_aliases, attempt, extension,
   reason, diagnosis`.
7. **`unknown_share` is measured over data nodes** (containers, comments, interface and action
   tools excluded), matching invariant 8 so the report and the invariant never disagree.
8. **`meta` keys for a join** use the out-anchor map, so `MetaInfo connection="Left"` lands at
   `meta["L"]`. On a join, `Left` names both an in anchor and the `L` out anchor and the two carry
   the same fields, so this loses nothing — but it is an assumption.
9. **`raw_config` is `null`** (not `""`) for a node with no `<Configuration>`; `annotation` is
   likewise `null` when empty.
10. **`dag["workflow"]`** is the workflow directory's name when the file sits in a `source/`
    directory, else the file's stem; `run` always parses under `workflows/<wf_id>/source/`, so it
    is the wf id there.

## Departures from literal text (with reasons)

1. **Exit 0 for `RECOVERED` as well as `PARSED`.** The brief says "exit 0 only for `PARSED`", but
   it also says a clean parse whose previous report was `RECOVERED` *stays* `RECOVERED`. Exiting 1
   on a clean parse would stall the orchestrator, so the preserved `RECOVERED` exits 0. Mapping:
   `PARSED`/`RECOVERED` → 0, `INVARIANT_VIOLATION`/`QUARANTINED` → 1 (domain), `FAILED` → 2
   (unexpected), which is the plan's Global Constraints table.
2. **`invariants.py`'s CLI does not write `parse_report.json`.** The spec §6.3 `__main__` writes a
   report with `"status": "OK"`, which is not in the status vocabulary and would race `parse.py`
   for the same file. The CLI now prints the errors and exits 0/1; `parse.py --check` owns the
   report. `check()` itself is unchanged apart from the brief's additions.
3. **Invariant 6 ignores edges that point at a node that does not exist.** The spec's code counts
   them into `indeg`, so a single dangling edge reports both "references unknown node" (invariant
   2) and a phantom "cycle detected". A dangling edge is invariant 2's finding; the cycle check now
   only walks known nodes. `tests/test_invariants.py::test_2_an_edge_must_resolve_to_a_known_node`
   asserts exactly one error.

## Self-review findings (fixed before committing)

- `config.find("NumRows") or ET.Element(...)` relied on `Element.__bool__`, which is deprecated and
  would have printed a `DeprecationWarning`; replaced with an explicit `is not None`.
- The connection-string regex had no `\b`, so a word ending in `odbc` would have matched; anchored
  it on a word boundary.
- `run` initially carried `attempt`/`extension` forward from *any* previous report; narrowed to the
  `RECOVERED` branch the brief describes, so a stale extension name cannot leak into a fresh report.
- A `.yxzp` member whose path escapes `source/` now fails the parse instead of writing outside the
  workflow (plan constraint: scripts touch nothing outside `--root`). Test added.
- `invariants.py`'s CLI used `next(glob(...))` (an unhandled `StopIteration`) and missed `.yxwz`;
  it now shares `parse.workflow_file` via a function-scope import (`parse` imports `invariants`, so
  module-scope would be circular) and exits 2 with a usage error.
- Macro resolution keeps a stack of resolved paths, so a macro that (directly or transitively)
  includes itself is marked `unresolved` instead of hanging the parser.

## Concerns

- **Everything here is synthetic.** The samples and the six corpus fixtures were written by hand to
  `dag-contract.md`; no file in this repo has been produced or opened by Alteryx, and no part of
  this ran against Alteryx or Snowflake. The `locked` fixture in particular is an invention — we
  have no locked workflow to copy — and its README says so. If a real locked file ever shows up and
  disagrees, that fixture is the thing to correct, not the parser's contract.
- **`tool_config.py` is 366 lines**, the largest file in the task. It is one responsibility (one
  `<Configuration>` → one `config` dict, plus the scrubbing §6 attaches to the same values) and the
  plan names it as one file, so I did not split it; the per-type parsers are independent functions
  behind a `PARSERS` table if a later task wants to.
- **`_CRED_KV_RE` strips `PWD=`/`UID=`/`Password=`/`User ID=` pairs anywhere in a node's text**,
  including inside a SQL query that legitimately contains one. That follows §6 ("any … value"), but
  it means a query mentioning a password column comes back altered. No sample does.
- **Anchor aliases are not accepted.** `ANCHORS` holds exactly the contract's spellings, so a
  `Dupes` anchor (spec §6.1 mentions the name in passing) would be reported by invariant 2 rather
  than silently mapped to `D`. That is deliberate — an unknown anchor should reach the recovery
  agent — but it is the sort of thing a real corpus would settle.

---

# Fix round 1

Both Important findings fixed. Worked in the worktree
`.worktrees/task-4-fix` (branch `wt/task-4-fix`, from `039826b`); the main checkout was not
touched. Commit: `8a41869 fix: FAILED exits 1 and extension handlers run before classification`.
Module resolution confirmed inside the worktree before starting:
`parse.__file__ = …\.worktrees\task-4-fix\scripts\parse.py`.

## Finding 1 — `FAILED` is a domain failure, so it exits 1

The reviewer is right, and the ruling is implemented as given.

- `scripts/parse.py` `main`: the status→code dict is gone. `return 0 if report["status"] in
  ("PARSED", "RECOVERED") else 1` — `FAILED`, `INVARIANT_VIOLATION` and `QUARANTINED` all exit 1.
- Exit 2 is now only reached two ways: argparse's own usage errors, and `parser.error(...)` when
  there is no workflow to parse. To make the second true, `run` no longer swallows
  `FileNotFoundError` from `workflow_file` — it re-raises it (`except FileNotFoundError: raise`
  ahead of the broad handler) and `main` turns it into a usage error. A side effect worth having:
  `parse.py wf_typo` no longer creates `workflows/wf_typo/parsed/parse_report.json` for a mistyped
  id. A zip-slip `.yxzp` and a broken document still produce a `FAILED` report and exit 1, because
  in those cases there *is* a workflow and there *is* something to report.
- The ruling's third case — "a crash outside the parse itself that leaves no report" — did not
  actually exit 2 before (an unhandled traceback exits 1), so `main` now catches it, prints the
  traceback and returns 2.
- Module docstring (parse.py:10-13) rewritten to state this classification and why (spec §6.4
  triggers recovery on `FAILED` and `INVARIANT_VIOLATION` identically).

Tests: `test_run_reports_failed_with_the_message_when_the_xml_is_broken` now asserts
`parse.main([...]) == 1`; new `test_a_usage_error_exits_2_and_writes_nothing` (a missing workflow
and no arguments at all both raise `SystemExit(2)`, and no directory is created); new
`test_a_crash_outside_the_parse_exits_2` (monkeypatches `parse.run` to raise).

End-to-end check of the CLI on a temp root, one workflow per status:

```
wf_ok   -> exit 0     (PARSED)
wf_bad  -> exit 1     (FAILED, broken XML)
wf_lock -> exit 1     (QUARANTINED, the locked corpus fixture)
wf_typo -> exit 2     (no such workflow)
no-args -> exit 2     (argparse)
$ ls $T/workflows  ->  wf_bad  wf_lock  wf_ok      # wf_typo left nothing behind
```

## Finding 2 — the handler genuinely runs before classification

`_build_node` restructured to the ruling's order:

1. Build the type-independent part (`tool_id`, `plugin`, `container_id`, `raw_config`,
   `annotation`, `meta`) and look up a handler for the plugin; `type` is pre-filled with what
   `classify` would say so a handler can look before it leaps, and `config`/anchors start empty.
2. `type` = the handler's if it returned one, else `classify(plugin, macro)`.
3. `config` = the handler's if it returned one, else `parse_config(<final type>, configuration)`.
4. anchors = the handler's if returned, else the final type's defaults (and, for `unknown`, the
   connections as before); a macro's own tools still name its anchors, and a handler's anchors
   outrank those too.

Two details fell out of the restructure:

- When the handler *changes* the type, `annotation` and `meta` are re-read for the final type, so a
  vendor tool remapped to `unique` gets its `MetaInfo connection="Unique"/"Duplicates"` re-keyed to
  `U`/`D` — otherwise the node would have carried anchors and meta keys from two different types.
- `_resolve_macro` is only called when there really is an `<EngineSettings Macro=…>`. A handler
  that declares `type: "macro"` on a plain tool used to reach `macro.replace(...)` on `None`;
  it now produces a macro node with no `macro_path`, which invariant 4 reports.

`registry.merge(node, result)` (mutating) became `registry.mergeable(result)` (filtering), since
the parser now decides where each key lands rather than merging a result over a finished node.
`scripts/parsers/ext/README.md` replaces "called after the node has been built" with the four-step
order above, including the remapping example.

Tests added (`tests/test_parse.py`), all against a small hand-built `VENDOR` workflow whose vendor
tool carries a `<UniqueFields>` configuration, `Unique`/`Duplicates` MetaInfo and a `Unique` outbound
connection:

- `test_an_extension_may_remap_a_vendor_plugin_to_a_builtin_type` — a handler returning only
  `{"type": "unique"}` yields `config == {"fields": ["ACCT"]}` (parsed for `unique`, not the empty
  `unknown` config), `in/out_anchors == ["Input"] / ["U", "D"]`, `meta` keyed `U`/`D`, the outbound
  edge's `src_anchor == "U"`, and `invariants.check(...) == []`.
- `test_an_extension_that_supplies_a_config_keeps_it_verbatim` — the handler asserts it was called
  with `tool_id`, `plugin`, `annotation`, `raw_config` and the `<Node>` element; its `config` is
  kept verbatim, `type` falls back to `classify`, `behavior`/`confidence` are merged, a key outside
  `MERGEABLE_KEYS` is dropped, and the unknown tool's anchors still come from its connections.
- `test_an_extension_calling_a_plain_tool_a_macro_is_reported_not_crashed` — invariant 4 reports it.

The brief's `test_unknown_plugin_trips_the_share_invariant_until_an_extension_explains_it` is
unchanged and still passes.

## Commands and output

RED (after writing the new expectations, before the fix):

```
$ "…/.venv/Scripts/python.exe" -m pytest tests/test_parse.py tests/test_invariants.py tests/parser_corpus/test_corpus.py
FAILED tests/test_parse.py::test_an_extension_may_remap_a_vendor_plugin_to_a_builtin_type
FAILED tests/test_parse.py::test_run_reports_failed_with_the_message_when_the_xml_is_broken - assert 2 == 1
FAILED tests/test_parse.py::test_a_usage_error_exits_2_and_writes_nothing - Failed: DID NOT RAISE SystemExit
3 failed, 63 passed in 0.41s
```

GREEN (after the fix):

```
$ "…/.venv/Scripts/python.exe" -m pytest tests/test_parse.py tests/test_invariants.py tests/parser_corpus/test_corpus.py
68 passed in 0.29s

$ "…/.venv/Scripts/python.exe" -m pytest
171 passed in 0.44s

$ "…/.venv/Scripts/python.exe" -W error -m pytest
171 passed in 0.47s
```

(`…` is `C:\Users\<user>\Desktop\Alteryx to Snowflake`; every command ran with the worktree
`…\.worktrees\task-4-fix` as cwd.)

## Files changed in this round

`scripts/parse.py`, `scripts/parsers/registry.py`, `scripts/parsers/ext/README.md`,
`tests/test_parse.py` — all inside the worktree, committed as `8a41869`. The reviewer's Minor
items were not addressed, as instructed.
