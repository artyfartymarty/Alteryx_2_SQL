# wf_0004 / seg_01 — translation notes

One assumption per line. Nothing below has been checked against a real Alteryx engine or a real
Snowflake account; every claim is against `docs/reference/simulator-semantics.md` (the parity
oracle) and program spec §8.

- The segment holds tool 1 alone and terminates in an outbound stream, so its only output is the work table `MIG_WORK.WF0004_SEG_01_OUT` that `seg_02` reads.
- One tool is under the segmenter's `min_tools` floor of 3, and that is deliberate: the analyzer's rule is that a macro gets a segment of its own, so tool 2 cannot absorb tool 1 and there is nothing else upstream to merge with.
- Tool 1 reads `STOCK`, the logical name `intake/mappings.yaml` records for `wh/stock.yxdb` (contract C8's normalized key — the last two path components of `C:\data\wh\stock.yxdb`, lower-cased); the procedure never names the mapped Snowflake table.
- The tool has no record limit and no query of its own, so the projection is exactly the four fields its `MetaInfo` declares, in their declared order, and there is no predicate to re-apply.
- Every column is declared nullable on the outbound stream: the `edge` golden set contains a row that is NULL in all four columns, and it is the macro in `seg_02`, not this segment, that removes it.
- `contract.outputs[0].keys` is `[]`: the `edge` golden set holds two byte-identical `DUP-1` rows on purpose, so no column set identifies a row and the comparison falls back to a row multiset, which is exact about whether the rows match and attributes a difference to a column only by nearest-match pairing, a heuristic.
- `row_relation` is `1:1`: nothing here filters, aggregates or expands — the segment is a read.
- Needs verification on Snowflake: `CREATE OR REPLACE TRANSIENT TABLE … AS` takes each column's type from the expression that produced it and does not enforce the `VARCHAR(n)`/`NUMBER(p,s)` widths `contract.outputs[0].columns` declares. Those widths are the Alteryx field widths, including the `edge` set's 30-character `SKU` and 100-character `NOTE`, which sit exactly on them.
- `ALTER SESSION SET TIMEZONE`/`WEEK_START` is set for the same reason every procedure in this project sets it, and contract C4 requires `EXECUTE AS CALLER` so that it is allowed to. This workflow has no date column at all, so neither setting can affect its result.
- `RUN_ID` is unused: contract C4 fixes the parameter list, and this segment has no query tag, no audit row and no pre/post SQL to put it in.
