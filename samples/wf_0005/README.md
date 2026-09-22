# wf_0005 — vendor dedupe (deliberately not migratable)

`source/vendor_dedupe.yxmd`, E1 engine, 4 nodes: input (1) → a third-party plugin (2) →
Run Command (3) → yxdb output (4).

**This file was written by hand to `docs/reference/dag-contract.md`. It has never been produced
or opened by Alteryx.** `AcmeAnalytics.Dedupe.DedupeTool` and `AcmeDedupe.dll` are inventions;
no such tool exists.

## What this sample is here to exercise

| Tool | Behaviour under test |
|------|----------------------|
| 2 | an **unknown** plugin: the parser must keep it with `type: "unknown"` and its `raw_config`, never drop it. With one unknown among four data nodes it is 25% of them, so the "unknowns without a `behavior` are at most 10%" invariant fails until a `scripts/parsers/ext/` extension explains the tool — that failing-then-passing pair is what the parser tests use it for |
| 3 | `run_command` is tier T3, which is why `expected_terminal` is `MANUAL`: the workflow must park rather than pretend to migrate |

Because the workflow can never validate, the simulator refuses it (`UnsupportedTool`) and no golden
outputs are ever produced. Only the `normal` and `empty` input sets exist; `period_end` and `edge`
would have nothing to prove.

## `normal` (6 rows)

| `ACCT` | Why it is there |
|---|---|
| `A-100` twice | the same account updated twice — the case the dedupe tool claims to resolve by keeping the later `UPDATED` |
| `B-200` | a single row with a negative balance |
| `C-300` twice | a second duplicate pair, out of date order, so "keep max date" is not the same as "keep last row" |
| `D-400` | `UPDATED` is NULL, so there is no date to compare |

## `empty`

Header row, zero data rows.
