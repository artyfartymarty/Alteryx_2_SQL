# Task P4 — fix round 1 (controller rulings on the production bugs the hand-off work found)

Writing the guide surfaced four real bugs on the production path. Each is fixed HERE (you found them, and the guide should
describe the fixed behaviour, not a workaround). RED-first for each; then remove the corresponding workaround from
`docs/handoff-production.md` / `docs/handoff-copilot-models.md` and keep `tests/test_handoff_production.py` green.

## B1 — `set_models.py --default <id>` must route EVERY role to `<id>` (orchestrator/cli.ts `loadConfig`, `DEFAULT_CONFIG`)
`loadConfig` deep-merges the file's hosted profile over `DEFAULT_CONFIG`, so the default's `roleModels` (the placeholder
`gpt-6-astra` for five roles) comes back after `set_models.py` removed it. RULING: (1) `DEFAULT_CONFIG.profiles.hosted`
carries NO `roleModels` (the owner's policy is one default model for every role); (2) when the file defines a profile,
`roleModels`, `roleContextTiers` and `roleReasoningEffort` are taken from the file as whole maps — never merged key by key
with the defaults — so removing a key in the file removes it; (3) a node test: after `set_models.py --default X` on a copy
of the committed config, `loadConfig` resolves every one of the nine roles' model to `X`; and `--check-models` lists the
effective ids. The committed `orchestrator.config.json` stays consistent (run `set_models.py --dry-run` to prove it is a
no-op on the committed file, or say what it would change).

## B2 — importing real Alteryx captures records the golden set (`scripts/inject_outputs.py --import-set`)
`--import-set <name>` writes the typed CSVs but never adds `<name>` to `manifest.golden_sets`, so `stageGolden` stays
BLOCKED after a real capture. RULING: `--import-set` appends the set name to `manifest.golden_sets` (deduplicated, order
kept) after every CSV is written successfully, and not at all on failure. Test: an import makes `stageGolden` (with the
fake env) move to DONE. The before/after state of append/merge/PreSQL database targets is NOT fixed here — keep it as a
backlog item with the exact gap stated.

## B3 — the pipeline never touches GitHub unless explicitly enabled (orchestrator `gh` use)
With `gh` installed, intake runs `gh issue create` and the pr stage runs `gh pr create` — an outward-facing side effect the
owner did not ask for. RULING: GitHub integration is OPT-IN: a config key `github: { enabled: false }` (default false) and
a CLI flag `--gh` that enables it for one run; with it off, the orchestrator never invokes `gh` (the stages record
`gh: disabled` and continue exactly as when `gh` is not installed). Tests: gh installed + disabled → no `gh` call; enabled
→ the existing behaviour. Update README §5/§7 and the guide (the "keep run roots outside any git repo" workaround can
become a recommendation, not a requirement).

## B4 — every script's `--help` works in a Windows console (cp1252)
`parse.py --help` and `load_golden.py --help` exit 1 with UnicodeEncodeError on a cp1252 pipe. RULING: every script under
`scripts/` (and `scripts/dev/`) prints help and errors correctly under `PYTHONIOENCODING=cp1252`: reconfigure stdout/stderr
to UTF-8 with `errors="replace"` at entry (a shared helper in `scripts/lib/`), or keep help text ASCII — your call, one
mechanism for all. Test: a parametrised test runs `<script> --help` for every script with `PYTHONIOENCODING=cp1252` and
asserts exit 0. Then the guide may name `parse.py` commands again where useful.

## Accepted as is
Your edits beyond pointers (README §7/§8 corrections, `docs/handoff-copilot-models.md` §0/§5 corrections, the two stale
lines in `docs/reference/snowflake-backend.md`).

## Coordination
Task G is running in parallel (it owns `workflows/**`, `tests/test_committed_workflows.py`,
`tests/test_documented_identifier_form.py`, `tests/test_deploy.py`, README §6 and the sample table). Do not touch those.

## Report
Append "## Fix round 1" to `task-P4-report.md` (each bug: the fix, the RED evidence, the workaround removed). Commit as
`fix: set_models routes every role; imported captures record their golden set; GitHub is opt-in; --help works in cp1252`.
Run node, tsc, `tests/test_handoff_production.py`, and the whole pytest suite.
