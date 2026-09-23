# wf_0006 / seg_01 — translation notes

One assumption per line. Nothing below has been checked against a real Alteryx engine or a real
Snowflake account; every claim is against `docs/reference/simulator-semantics.md` (the parity
oracle) and program spec §8.

- The segment holds tools 1 and 2 and terminates in one outbound stream, `2_T`, so its only output is the work table `MIG_WORK.WF0006_SEG_01_OUT` that `seg_02` reads.
- `seg_02` is a Snowpark Python procedure rather than a SQL one. That changes nothing in this file: contract C3 names a segment's work table the same way whatever language the next segment is written in, and this procedure would be byte-identical if tool 3 were a Formula.
- Tool 1 reads `SUBSCRIPTIONS`, the logical name `intake/mappings.yaml` records for `billing/subscriptions.yxdb` (contract C8's normalized key — the last two components of `C:\data\billing\subscriptions.yxdb`, lower-cased); the procedure never names the mapped Snowflake table.
- The tool has no record limit and no query of its own, so the projection is exactly the five fields its `MetaInfo` declares, in their declared order, and there is no predicate to re-apply.
- Tool 2's expression `[BILLED] > 0` is translated as a plain `WHERE`, because SQL's three-valued logic already agrees with the Filter tool on all three cases: true leaves on `T`, and false **and NULL** leave on `F` (program spec §8.5). The `normal` golden set carries a NULL `BILLED` and a zero `BILLED` so both exclusions are exercised.
- The comparison is strict, so a period billed exactly 0 is dropped rather than kept as a zero-revenue period. That is the tool's own semantics, not a simplification.
- The False anchor is wired to nothing, so it produces no stream, no CTE and no output. Only `t2_filter_t` exists.
- `BILLED` is declared `NOT NULL` on the outbound stream, and it is the Filter alone that guarantees it. `CUSTOMER`, `PERIOD`, `CAP` and `CANCELLED` are all declared nullable: nothing in this segment removes a NULL in any of them, and the `edge` golden set's all-NULL row is dropped for its NULL `BILLED`, not for the rest.
- `contract.outputs[0].keys` is `["CUSTOMER", "PERIOD"]`. That pair is unique on this stream in all four golden sets — the `edge` set's byte-identical duplicate pair is billed 0 and never reaches the stream — so the comparison joins on it rather than falling back to a row multiset, and `seg_02` can rely on the same pair being a total sort order.
- `row_relation` is `filter`: the segment drops rows and changes nothing else.
- Needs verification on Snowflake: `CREATE OR REPLACE TRANSIENT TABLE … AS` takes each column's type from the expression that produced it and does not enforce the `VARCHAR(n)` widths `contract.outputs[0].columns` declares. Those widths are the Alteryx field widths, including the `edge` set's 20-character `CUSTOMER`, which sits exactly on its limit.
- `ALTER SESSION SET TIMEZONE`/`WEEK_START` is set for the same reason every procedure in this project sets it, and contract C4 requires `EXECUTE AS CALLER` so that it is allowed to. This workflow has no date or timestamp column at all, so neither setting can affect its result.
- `RUN_ID` is unused: contract C4 fixes the parameter list, and this segment has no query tag, no audit row and no pre/post SQL to put it in.
