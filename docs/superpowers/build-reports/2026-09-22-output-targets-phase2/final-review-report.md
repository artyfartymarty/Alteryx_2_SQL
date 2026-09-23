# Phase-2 final whole-branch review — report

Range `c0c02a2..fab6d60` on `feat/output-targets-phase2`, reviewed read-only in the detached worktree
`.worktrees/p2-final` at `fab6d60`. P5 (README §1 Mermaid) is out of range and reviewed separately.

## Passes completed (all five)

1. **Seams between tasks** — traced end to end through the real code (dbt×W1, P2×W1/B, C4V×P2×deploy,
   W2×F×W4×N1×policy, P1×runner, P4-B3). See findings and "seams verified" below.
2. **False PASS** — traced `validate_dbt.py`, `validate_segment.py`, `validate_snowpark.py`,
   `validate_workflow.py`, `chainCheck`/`translateDbt` in `stages.ts`, and `deploy.py`'s gate.
3. **Security of the agent sandbox** — read `orchestrator/policy.ts` whole; drove `decide()` with a probe
   script; and drove the two dbt scripts (`compile_check.py --target dbt`, `validate_dbt.py`) against a
   scratch copy of `wf_0007`. This pass produced the one Critical.
4. **Docs vs code** — spot-checked `deploy.py`, the two Snowflake libs, `snowflake-backend.md`,
   `output-targets.md`, the dbt README command, the per-role model config vs `config.json`.
5. **Carry-over notes** — verdicts at the end.

Suites re-run at `fab6d60` in the worktree: `npm test` → **331 pass / 0 fail**; `tests/test_compile_check_dbt.py`
→ 21 pass. Consistent with the brief's baseline. I did not re-run the full 7-minute pytest suite.

---

## Critical

### C1. A dbt workflow's project runs arbitrary code during local validation — the dbt output has no content gate equivalent to C4V / snowpark_rules

**Where:** `scripts/compile_check.py` `compile_check_dbt` / `_dbt_jinja_errors` (lines 170–388, the whole
dbt gate); `orchestrator/policy.ts` `writeLanes` dbt lanes (216–234); `scripts/lib/dbt_project.py`
`run_dbt` (executes the project as a subprocess).

**What.** The design's guarantee for a dbt workflow (spec §4.3, ledger DV rulings, Task A) is that a model is
a *closed surface*: `dbt:model_jinja` allows only `config(...)`, `source(...)`, `ref(...)`, `this`, the
`is_incremental` if/else, and comments — "nothing else." That guarantee is what makes the dbt path safe to
hand to an agent, the analogue of `snowpark_rules.check_proc_py` (no `exec`/`eval`/`open`, import allow-list)
for Snowpark and C4V's flat-body SQL judge for procedures. The dbt gate does **not** deliver it:

- `_dbt_jinja_errors`, `_dbt_tool_comment_errors` only glob `models/**/*.sql`. **A dbt *Python* model
  (`models/<name>.py`) is never inspected at all.** dbt-duckdb runs it (`submit_python_job` is present in the
  installed adapter), so its `def model(dbt, session)` body is arbitrary Python.
- `dbt_project.yml`'s layout check (`_dbt_layout_errors`) verifies `name`/`profile`/`model-paths`/`vars` but
  does not forbid extra keys, so **`on-run-start` / `on-run-end` hooks** are accepted and run arbitrary SQL.
- A **`pre_hook`/`post_hook`** string is plain SQL with no Jinja token, so it sails through `_dbt_jinja_config_ok`
  (which only rejects nested `{{…}}`/`name(` inside `config(...)`), e.g. `post_hook="COPY {{ this }} TO '…'"`.

An agent reaches all three using only capabilities the policy grants the translator/fixer in dbt scope: the
write lane allows `dbt/models/**` (the `UNDER` pattern matches `models/x.py`) and `dbt/dbt_project.yml`, and
`scripts/compile_check.py` is on the translator's allowed-script list — running it **is** what invokes dbt.

**Failure scenario (verified by running).** In a scratch copy of `wf_0007`, I replaced
`models/wf0007_seg_01_out.sql` with a `models/wf0007_seg_01_out.py` whose `model()` body wrote a file outside
the workflow tree, and separately added an `on-run-start` COPY and a `post_hook` COPY.
`python scripts/compile_check.py wf_0007 --target dbt` returned `OK: 0 statements, 0 errors` in every case, and
`python scripts/validate_dbt.py wf_0007` returned every segment PASS, idempotent=True, and wrote
`validation_workflow.json` verdict `PASS` — while the Python `model()` body and the two COPY hooks had run and
left their files. So a dbt project carrying arbitrary Python/SQL passes compile-check, validates PASS, reaches
`translate: VALIDATED`, and would be deployed.

**Why it matters.** This defeats three sandbox guarantees the branch works hard to hold elsewhere: (a) the
brief's own "run an arbitrary interpreter" concern — a Python model is exactly that, and the shell policy
deliberately blocks it everywhere else (`python -c` is denied via `INTERPRETER_FLAGS`, and only allow-listed
scripts may run); (b) cross-workflow isolation — a Python model can `open('workflows/<other>/…')`, bypassing
rule 1a and the G2 cross-workflow check that the branch added for scripts; (c) per-role write lanes — it can
write anywhere the process may. The outer boundary that remains is the spec's shell container (POLICY.md:19,171:
"repo as the only writable mount," network allowlist) **if the org runs it**, which bounds file writes to the
repo mount and network to the allowlist — but arbitrary in-container code execution, cross-workflow reads and
allowlisted-endpoint exfiltration are still real, and the design's stated closed-surface guarantee for dbt is
simply false. On a real account `deploy.py --execute` submits the Python model as a Snowpark proc bounded by the
Snowflake role, so production *deployment* is role-bounded; the exposure is the local validation host, which
every dbt migration exercises.

**Fix direction (not required to verify):** `compile_check_dbt` should refuse any non-`.sql` file under
`models/`, refuse `on-run-start`/`on-run-end` and any `dbt_project.yml` key outside the fixed set, and gate
hook SQL through the same statement judge the SQL path uses (or forbid hooks not derived from a mapped
Pre/PostSQL). No test exercises a `.py` model or an ad-hoc hook today, so the gap is uncovered.

---

## Important

None. The false-PASS, credential-redaction, cross-workflow, LET/`IDENTIFIER`, external-location, model-config
and gh-opt-in seams I traced all hold (see below).

---

## Minor

- **M1 — `validate_dbt`/`validate_workflow` do not guard an *empty* `order.json`.** `_segments` raises only when
  the file is missing, not when it flattens to `[]`; `validate_dbt` would then return `{}` and `main`'s
  `all(...)` over an empty dict is `True` → exit 0, writing a PASS chain report. Not reachable through the
  orchestrator (`stageTranslate` parks `no-segments`, and `validate_workflow._prerequisites` raises on an empty
  order), so it is only a direct-CLI edge on an already-invalid workflow. Read, not run.
- **M2 — carry-over note 7 (raw `FileNotFoundError` from `compile_check_dbt`)** is real but harmless; see below.
- **M3 — carry-over note 2 (duplicate cap warning per step-6 iteration)** is cosmetic; see below.

---

## Seams verified (no finding)

- **dbt × W1 gate.** A dbt workflow's VALIDATED gate is the chain report `validate_dbt.py` writes from its own
  run (`validate_dbt.py` 374–384), which `translateDbt` requires to be `PASS*` before writing `procs/README.md`
  and setting VALIDATED (`stages.ts` 1047–1063), and which `deploy.py` `_gate` re-checks (68–77). Consistent
  across `stages.ts`, `deploy.py`, the docs and committed `wf_0007` (`validation_workflow.json` = PASS,
  `procs/README.md` present, no `master.sql`).
- **P2 × W1/B backend.** Every validator resolves the engine through the one `snowflake_target` helper; a
  Snowflake option without `--backend snowflake`, or an unlisted sandbox database, is a usage error before any
  connection (`snowflake_sandbox.sandbox_database`, `validation.snowflake_target`). Credentials never reach a
  log/report/exception: `SnowflakeBackend` refuses every credential argument and every non-name parameter,
  errors go through `snowflake_conn.redact`/`scrubbed` (masking to end-of-line, URL userinfo, PEM blocks), and
  `error_text`/`print_crash` redact on the Snowflake path. `--connection`/`--sandbox-database`/`--backend` are
  denied to agents (`isScriptBackendFlag`, verified by probe).
- **C4V × P2 × deploy.** `parse_proc` accepts the documented `LET <X>_SRC := SRC_DB || '.' || … ` +
  `IDENTIFIER(:<X>_SRC)` form; `policy.ts`'s `checkProcedure` judges the flat body (denies `:=`/`LET` outside a
  conforming LET, Scripting keywords, body `CALL`, non-literal `RETURN`, external COPY/STAGE/GET/PUT, and the
  `_SRC`-written/`_TGT`-read role errors) and requires the exact C4 header; `deploy.py` executes `proc.sql` and
  `master.sql` as text (`backend.execute`), and `SnowflakeBackend.call_procedure` binds C4's five args by
  parameter order. Master body travels in `$$…$$` (`masterSql`), matching the committed `master.sql` files.
- **W2 × F × W4 × N1 × policy.** Batched analyzer lanes narrow to a batch's contracts + two fragments
  (`analyzerBatchLanes`); notes lanes (`notes/<role>.md`) are opened for intake/analyzer/fixer only and the
  directory is pre-created by the runner (N1) with no policy widening; the inline-context block is appended
  after instructions inside a fence and the compaction reminder names only the fixed notes path (no
  workflow-authored text).
- **P1 × runner.** `createSession` receives `roleModels?.[role] ?? model`, `roleReasoningEffort?.[role] ??
  reasoningEffort` (baseline medium present in both `orchestrator.config.json` and `DEFAULT_CONFIG`), and
  `contextTier` only when `roleContextTiers` has the role. Hosted profile resolves to gpt-6-astra for
  intake/analyzer/translator/fixer/parser-recovery and gpt-5.6-luna for reviewer/validator/documenter, matching
  `config.json`'s sub-agent entries.
- **P4-B3 (GitHub opt-in).** `github.enabled` defaults false; `ghUnavailable` returns `gh: disabled` when off,
  and `probe` (`gh --version`) is only called when enabled (`cli.ts` `githubAccess`). The two `env.sh("gh", …)`
  call sites (`stageIntake`, `stagePr`) are both behind `ghUnavailable`. No path invokes `gh` when disabled.
- **False PASS.** A vanished Snowpark output is dropped from the chain backend so it is judged missing, not from
  a stale copy (W1 I3); a crash in the chain fixer round withdraws and saves the segment's PASS first (W1 I1); a
  failed `dbt run` FAILs every segment and is a boundary divergence; `needs_human` overrides a PASS verdict on
  every path; idempotency never returns True without both runs actually compared. All hold.

---

## Carry-over notes (verdicts)

1. **W3 diamond can't isolate a heavy node** — *not load-bearing.* Documented as the general "unsplittable case"
   (every remaining bridge protected) in `docs/reference/large-workflows.md` (≈37–38) with an accurate warning;
   pre-existing algorithm.
2. **W3 duplicate cap warning per step-6 iteration** — *not load-bearing.* Cosmetic log duplication; recorded as
   Minor M3.
3. **P3 `_safe_extract` duplicates the zip-slip guard** — *not load-bearing.* Both copies are the same correct
   check; a shared helper is hygiene only.
4. **Segmenter scale (~29 s / 3000 tools)** — *load-bearing as a documented backlog item, and it is documented.*
   `docs/production-backlog.md` §"Segmenter scale" (260–290) records it (re-measured 142.7 s at 3000) with a
   `segment_scale.py` reproducer. No code change needed for phase 2; a real large-corpus survey must budget for it.
5. **B partial sandbox file if `load_set` raises** — *not load-bearing.* The file is git-ignored and swept by the
   next run; the report hygiene (no stale `validation*.json`) is unaffected.
6. **R-B1 collides on two outputs with the same `tool_id`** — *not load-bearing.* Requires a contract-invariant
   (C5) violation upstream; the disambiguation is correct for well-formed contracts.
7. **A: `compile_check_dbt` raises raw `FileNotFoundError` when `intake/mappings.yaml` is missing** — *minor
   (M2).* The CLI still exits 2 with a message; only the library call's contract is undocumented. No wrong result.
8. **C: broken `wf_0007 attainment_history.sql` non-deterministic on dbt-duckdb** — *not load-bearing.* Only its
   stable fields (class/columns/stream/needs_human) are recorded; the variant exists to FAIL.
9. **handoff Rung 3 reuses Rung 1's run root though §1.3 calls earlier roots stale** — *not load-bearing.* Rung 3
   makes no hosted calls, so reusing the root is harmless; worth one clarifying sentence, no more.

---

## Strengths

- The false-PASS surface is genuinely hard to fool on the SQL/Snowpark/procedures paths: physical-order-then-
  reversed idempotency, missing-output drops, crash-window PASS withdrawal, `needs_human` overrides, and
  "never idempotent without both runs compared" are all implemented and tested.
- The Snowflake `--backend` plumbing is disciplined: named-connection-only, credential arguments refused before
  import, layered redaction, sandbox-database allow-list enforced before any connection.
- C4V's SQL judge (flat body, documented `LET`/`IDENTIFIER` form, external-location denial, `_SRC`/`_TGT` roles)
  and the policy's normalized, fail-closed path/shell/SQL checks are thorough and well-tested (331 node tests).
- Docs match code on the claims I checked, and nothing asserts anything ran on real Snowflake or Alteryx.

## Overall verdict

**Ready after fixes.** The branch is otherwise solid and its tests are green, but C1 is a real
arbitrary-code-execution / sandbox-isolation hole on the dbt validation path — the one output target this phase
added — and it contradicts the design's own closed-surface guarantee. Close C1 (and add a test for a `.py`
model and an ad-hoc hook) before handing the dbt-capable pipeline to the company; the remaining items are minor
and can follow.
