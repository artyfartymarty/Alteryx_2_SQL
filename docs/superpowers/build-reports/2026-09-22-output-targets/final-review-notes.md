# Notes for the final whole-branch review (phase 1, output targets)

Branch: `feat/output-targets`, base `main@e3052fe`. Spec: `docs/superpowers/specs/2026-09-22-output-targets-design.md`
(phase 1 = everything except §4.3 / §5.3 / §7.3 dbt, the cookbook pages, `prompt_context.py`, live tests).
Plan: `docs/superpowers/plans/2026-09-22-output-targets-phase1.md` (annotated at Tasks 3, 4, 6 with the
rulings that superseded its text). Every task had a task review, at least one fix round and a scoped
re-review; the ledger is `progress.md` beside this file.

## Observations carried over from task reviews (not yet acted on — the reviewer decides if any is load-bearing)
1. `orchestrator/stages.ts`: the pre-existing `env.log(...)` lines print raw script stderr unredacted, while the
   fixer-prompt copy of the same diagnosis goes through `hooks.ts`'s redact + 500-char bound. Two exposure levels
   for one string (pre-existing pattern, console only).
2. `scripts/lib/snowpark_rules.py`: a dunder inside a multi-line chained expression reports the chain's START line
   (CPython `ast.Attribute.lineno` semantics). Message accuracy only.
3. `scripts/lib/types_map.py::snowpark_to_alteryx`: reads the private `StringType._is_max_size` with a public
   length fallback; the re-reviewer showed the fallback alone classifies both the constructed and the read-back
   case correctly (an unsized column reads back as length 16777216).
4. `samples/wf_0006`: the NULL-`CANCELLED` guard in `proc.py` is reasoned, not exercised (no golden set can carry
   the row — the simulator raises on it); real Alteryx's typing of a NULL Bool in the Python tool is unverified and
   stated as such in five documents. An all-NULL column comes back as object/None from the Local Testing
   Framework (recorded in seg_02's notes; cannot arise here).
5. `compare.py` flags a column-ORDER difference as a schema failure (pre-existing, same as the SQL twin); the
   Snowpark docs tell the translator to write columns in contract order.
6. `snowpark_rules`: the dunder check refuses any `__x__` bare Name (e.g. `__name__`), not only attribute access —
   conservative by design; a dunder-flagged statement often also carries `rule:session_scope`.
7. `migrateSegment`'s `failedBeforeReview` is in-memory per call (a crash between iterations loses it), like the
   existing `lastReason`.
8. The nine pre-existing canned contracts and 17 broken.json rows gained `"target": "sql"` (identical edits from
   Tasks 5 and 6A); the committed `workflows/` were refreshed in Task 6B for ALL six samples.

9. Task 6B: running the README §6 sequence in a scratch root re-serialises that root's `mappings/global.yaml`
   (comments stripped, parsed value identical) although §6 says "untouched" — pre-existing, the repo copy is
   unaffected; a doc wording item at most.
10. Task 6B: reproducibility of `workflows/` holds byte-for-byte except `updated_at` (per manifest) and
    `runtime_ms` (per stage record) — the same two volatile fields Task 17 documented.
11. Task 6B: the T3 workflow (wf_0005) originally received no `manifest.output_kind` (analyze's verify returned
    early for T3); ruled and fixed in 6B's fix round so every manifest mirrors `targets.json.output_kind`.

## Suites on the integration branch
feat/output-targets @ 204c98d (36 commits over main@e3052fe): pytest 1215 passed / 0 skipped; node 177/177; tsc clean; no `__pycache__` under `samples/` or `workflows/`.
