### Task C4V: The documented `IDENTIFIER` form for every SQL procedure (controller-added)

**Tier:** most capable · **Depends on:** W2 merged (`orchestrator/policy.ts`) · **Runs before:** W4, G

**Why (verified 2026-09-23 against Snowflake's documentation):** every SQL procedure this pipeline writes — the canned
answer keys, the broken variants, the cookbook, the translator's instructions, the policy's SQL judge and the 2026-09-18
design's contract C4 — references its source and target tables as

    IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS')

`docs.snowflake.com/en/sql-reference/identifier-literal` documents `IDENTIFIER( { string_literal | session_variable |
bind_variable | snowflake_scripting_variable } )` — a single value, not an expression. The concatenation form is
therefore undocumented and likely rejected when a real account compiles the procedure. The documented form builds the
name first:

    LET ORDERS_SRC VARCHAR := :SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS';
    ... FROM IDENTIFIER(:ORDERS_SRC) ...

Nothing here can be proven locally (no Snowflake account); the change moves every procedure from an undocumented form to
the documented one, and the local double must support the new form exactly.

**The new contract C4 table-reference rule (binding):**
- Inside the procedure's `BEGIN … END` block, before the first statement that uses it, one `LET <VAR> VARCHAR := <expr>;`
  per distinct source or target table, where `<expr>` is exactly `:SRC_DB || '.' || :SRC_SCHEMA || '.<LOGICAL>'` or
  `:TGT_DB || '.' || :TGT_SCHEMA || '.<LOGICAL>'` (the same three-part concatenation the old form used, now on the
  right-hand side of a `LET`).
- `<VAR>` naming: `<LOGICAL>_SRC` for a source and `<LOGICAL>_TGT` for a target (upper case; a logical that is both a
  source and a target gets both variables).
- Every reference is `IDENTIFIER(:<VAR>)`; an expression inside `IDENTIFIER(...)` is refused by `compile_check.py` with a
  named check `c4:identifier_expression` and by the policy's SQL judge.
- Work tables in `MIG_WORK` keep their literal names (unchanged).

**Files:**
- `scripts/lib/proc_runner.py` — `bind` evaluates `LET <VAR> VARCHAR := <expr>;` where `<expr>` is a concatenation of
  bound parameters and string literals (after parameter substitution), records `<VAR>`, substitutes `:<VAR>` in later
  statements, and folds `IDENTIFIER('<a.b.c>')` as today; the LET statement itself is not executed on the backend. A LET
  whose expression is anything else → `ProcError` naming it. Keep accepting the old form in the local runner ONLY behind
  an explicit test that proves `compile_check` refuses it (so no committed artefact can use it).
- `scripts/compile_check.py` — `c4:identifier_expression` (any `IDENTIFIER(` whose argument is not a single `:<VAR>`,
  string literal, or session variable), `c4:let_form` (a LET that does not match the rule), and the existing checks keep
  working on the bound text.
- `orchestrator/policy.ts` — `CONTRACT_IDENTIFIER` recognises `IDENTIFIER(:<VAR>)` whose `<VAR>` is declared by a
  matching `LET` in the same body; the old concatenation form inside `IDENTIFIER` is judged like any other dynamic name
  (denied); `orchestrator/POLICY.md`; policy tests.
- Every SQL procedure under `samples/**/canned/**` and `samples/**/broken_sql/**` (25 files incl. wf_0001–0004 and
  wf_0006's SQL segments), plus their `translation_notes.md`/`docs/migration.md` mentions; the broken variants keep
  their one documented mistake and their recorded failure class (re-observe; `broken.json` unchanged or updated with the
  observed values).
- `.github/agents/translator.agent.md` (the C4 rule; keep any verbatim-pinned spec bullet in sync),
  `cookbook/input.md`, `cookbook/output.md` (+ any cookbook example that contains a full procedure), `docs/reference/*`
  that show the pattern, the 2026-09-18 design spec's C4 text (ours; `docs/spec/**` stays frozen — if the program spec
  shows the old form, say so in the design spec as a documented deviation).
- Tests: `tests/test_proc_runner*.py` (LET evaluation, substitution, refusal), `tests/test_compile_check*.py` (both new
  checks), `tests/test_canned_artifacts.py` (every canned and broken procedure uses only the documented form — a scan),
  `orchestrator/test/policy.test.ts`.

**Acceptance:** every canned procedure passes `compile_check.py`, `validate_segment.py` (all golden sets, idempotent)
and `validate_workflow.py` (all samples, chain PASS, idempotent); every broken variant still FAILs with its recorded
class; a repo-wide scan finds no `IDENTIFIER(` with an expression argument in any tracked `.sql`, `.md`, `.py` or `.ts`
file except the tests that prove the refusal and the design-spec sentence that records the deviation; the whole suite
green, 0 skipped; node and tsc green. Nothing claims the new form ran on Snowflake: every doc that mentions it says it is
the DOCUMENTED form, chosen because the old one is not in Snowflake's grammar, and that the first real-account run
(the hand-off's verification ladder) confirms it.

**Coupling found by the P2 review (binding):** `scripts/lib/backend.py`'s `SnowflakeBackend.call_procedure` (Task P2, in
flight, not on your base) calls `proc_runner.parse_proc` to read the C4 parameter order; `parse_proc`'s statement scanner
(`_SCRIPTING_KEYWORDS` includes `LET`) refuses the whole procedure today. Your change to `parse_proc` must accept the
documented `LET` form everywhere `parse_proc` is used, not only in `run_proc`'s DuckDB path, and a test must show
`parse_proc` returns the same name/parameters for a procedure in the new form.
