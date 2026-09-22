# wf_0004 / seg_02 — translation notes

One assumption per line. Nothing below has been checked against a real Alteryx engine or a real
Snowflake account; every claim is against `docs/reference/simulator-semantics.md` (the parity
oracle) and program spec §8.

- The segment holds tool 2 alone, the macro `Supporting_Macros/clean_codes.yxmc`, and reads `MIG_WORK.WF0004_SEG_01_OUT`, `seg_01`'s work table, written as a literal `MIG_WORK.…` name because contract C4 reserves `IDENTIFIER(…)` for mapped sources and targets.
- A macro is a workflow, not a tool: there is no cookbook pattern for `macro`, only for the tools inside it, so its `sub_dag` is inlined here one CTE per INNER tool in the macro's own topological order.
- The CTEs are named `t2_macro_m<inner tool id>_<inner type>`. The parent tool id comes first so the segment DAG's node 2 still has CTEs that a reviewer's "every data node has a CTE" check can find, and the `m<id>` part names the macro's own tool so a reader can line each CTE up with `clean_codes.yxmc`. Every one of them is preceded by a `-- tool 2 (macro clean_codes.yxmc, inner tool N: …)` comment.
- The macro's anchors are named after its own tools (`Input1` in, `Output5` out), so the outbound stream is `2_Output5` and not `2_Output`; that is the name the golden intermediate carries.
- Tool 2's `macro_input` (inner tool 1) and `macro_output` (inner tool 5) are parameters, not reads or writes: each carries the stream through unchanged, so one is the CTE that reads the upstream work table and the other is the statement's final `SELECT`.
- The question value is `MinQty` = `1`, taken from the parent's `<Value name="MinQty">1</Value>`, which overrides the macro's own `NumericUpDown` default of `0`. `[%Question.MinQty%]` is substituted textually before the expression is read, so the translated predicate is the literal `QTY >= 1`; translating the default instead would keep every zero-quantity row.
- The macro's RegEx (inner tool 2) is method `Replace`, so it rewrites `SKU` in place rather than appending a column, and the field keeps its position.
- Alteryx's `$1`/`$2` backreferences become `\1`/`\2`, and the backslashes are doubled in the SQL literal, which is Snowflake's own rule for writing a regex in a string.
- `CaseInsensitve` (Alteryx really spells it that way) is `True`, which is the `'i'` parameter; the parent's RegEx at tool 3 is case-**sensitive**, so the two flags are opposite and easy to swap by accident.
- The occurrence argument is `0` — replace every match — which is what the tool does; the pattern is anchored, so at most one match exists in practice.
- `CopyUnmatched` is `True`, meaning a SKU the pattern does not match keeps its original value. `REGEXP_REPLACE` already returns the subject unchanged when nothing matches, so no extra spelling is needed — but it is written down here rather than assumed, because `CopyUnmatched` `False` would mean NULL and would need a different translation.
- A NULL `SKU` gives NULL from `REGEXP_REPLACE` and NULL from `UPPER`, which is what the oracle does: the RegEx tool skips a NULL value and `Uppercase` propagates NULL.
- Assumption, unproven by this sample's data: Snowflake's regex dialect is not the Python `re` the oracle uses. `\d` and `\s` are ASCII-only in the local engine and Unicode-aware in the oracle, and case-insensitive `[A-Za-z]` folds differently at a few Unicode edges. No golden set contains a non-ASCII digit or a non-breaking space, so the divergence is recorded rather than guarded against; the `edge` set's `ÄB-12` matches neither engine, which is what the row is there to prove.
- Inner tool 3's `Uppercase([SKU])` is `UPPER(SKU)`; assumption: Snowflake's `UPPER` and the oracle's Python `str.upper()` agree on this data, which is ASCII apart from `Ä`, already upper case.
- Inner tool 4's Filter has only its True anchor wired to the Macro Output, so only `t2_macro_m4_filter_t` is built: `WHERE QTY >= 1` already drops the rows Alteryx would send to `F` (both the false ones and the NULL-quantity ones), because a three-valued `WHERE` keeps only what is really true.
- That Filter is what keeps the `edge` set's all-NULL row out of the rest of the workflow, which is why `SKU`, `WAREHOUSE` and `QTY` are declared `NOT NULL` on this stream. All three were checked against all four golden sets, and the broken variant `01_macro_filter_dropped.sql` is what a violation looks like.
- `NOTE` stays nullable: nothing removes a row for a missing note, and the `edge` set's all-NULL row is the only one that had one.
- `contract.outputs[0].keys` is `[]`: the `edge` golden set holds two byte-identical `DUP-1` rows on purpose, so no column set identifies a row and the comparison falls back to a row multiset.
- Needs verification on Snowflake: the six-argument `REGEXP_REPLACE(subject, pattern, replacement, position, occurrence, parameters)` is documented Snowflake SQL and transpiles to the local runtime's four-argument form with the flags `ig`; no statement in this file has been executed on an account.
- Needs verification on Snowflake: `CREATE OR REPLACE TRANSIENT TABLE … AS` does not enforce the `VARCHAR(n)`/`NUMBER(p,s)` widths the contract declares, so a `SKU` longer than 30 characters would not be caught locally.
- Nothing in this segment multiplies or divides, so Snowflake's multiplication-scale rule (`min(S1 + S2, max(S1, S2, 12))`, unlike the local runtime's `S1 + S2`) is not reached.
- `RUN_ID` is unused: contract C4 fixes the parameter list, and this segment has no query tag, no audit row and no pre/post SQL to put it in.
