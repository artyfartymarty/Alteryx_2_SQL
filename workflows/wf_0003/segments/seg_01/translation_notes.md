# wf_0003 / seg_01 — translation notes

One assumption per line. Nothing below has been checked against a real Alteryx engine or a real
Snowflake account; every claim is against `docs/reference/simulator-semantics.md` (the parity
oracle) and program spec §8.

- The segment holds tools 1–3, the "Extract" Tool Container, and terminates in an outbound stream rather than an Output tool, so its only output is the work table `MIG_WORK.WF0003_SEG_01_OUT` that `seg_02` reads.
- Tool 1 reads `GL_LEDGER`, the logical name `intake/mappings.yaml` records for the ODBC alias `prod_fin`; the procedure never names the mapped Snowflake table, and the scrubbed credentials in the source never reach any artifact.
- The Input tool's own query is part of its configuration, not something the procedure runs: its six columns become the projection of `t1_input` and its `WHERE AMOUNT <> 0` is re-applied there, because the procedure reads the whole mapped table.
- Assumption about the golden data, stated in `samples/wf_0003/README.md` and relied on here: no golden set contains a row whose `AMOUNT` is 0 or NULL, so re-applying that predicate removes nothing the simulator kept. A set that did contain one would make the two sides disagree for a reason that is not a translation bug.
- `AMOUNT` is declared `NOT NULL` on the outbound stream because that predicate removes every NULL amount; `REGION` is declared `NOT NULL` because the Filter keeps only `EMEA`. Both were checked against all four golden sets.
- Tool 2's expression compares against the workflow constant `[User.Region]`, whose value is the text `EMEA`; it is resolved at translation time into a literal, so a run for another region needs a new procedure rather than a new argument.
- Tool 2 has only its True anchor wired, so only `t2_filter_t` is built: `WHERE REGION = 'EMEA'` already drops the rows Alteryx would send to `F` (both the false ones and the NULL-region ones), because a three-valued `WHERE` keeps only what is really true.
- `t2_filter_t` carries the anchor letter as a suffix even though one branch is wired, so the CTE name says which anchor it is, as it does wherever a Filter appears.
- Tool 3's `%d/%m/%Y` becomes `TRY_TO_TIMESTAMP_NTZ(POSTED, 'DD/MM/YYYY')`: the `TRY_` form is the DateTime tool's warn-and-null behaviour, and `31/02/2026` in the `normal` set is the row that proves a date that does not exist has to become NULL rather than raise.
- Local-runtime fact, verified: `TRY_TO_TIMESTAMP_NTZ(x, 'DD/MM/YYYY')` keeps its format argument through sqlglot (it becomes `TRY_STRPTIME(x, '%d/%m/%Y')`), unlike the one-argument `TRY_TO_DATE`, whose format 13a found is dropped. A day-first format could not be translated correctly without it.
- `POSTED_DT` is an Alteryx `DateTime`, so a parsed date carries midnight and the column is `TIMESTAMP_NTZ`; `DATE` would be a type difference on a column the next segment sorts by.
- `ALTER SESSION SET TIMEZONE`/`WEEK_START` is set for the same reason every procedure in this project sets it, and contract C4 requires `EXECUTE AS CALLER` so that it is allowed to.
- Needs verification on Snowflake: `CREATE OR REPLACE TRANSIENT TABLE … AS` takes each column's type from the expression that produced it and does not enforce the `VARCHAR(n)`/`NUMBER(p,s)` widths `contract.outputs[0].columns` declares. The declared widths are the Alteryx field widths and are what the next segment is written against; nothing here checks them.
- `contract.outputs[0].keys` is `[]`: the `edge` golden set holds two byte-identical `DUP` rows on purpose, so no column set identifies a row and the comparison falls back to a row multiset, which is exact about whether the rows match and attributes a difference to a column only by nearest-match pairing, a heuristic.
- `ordering.order_dependent_columns` is empty for this segment: nothing here numbers rows, takes a first or last row, or runs a window. Row order still matters to `seg_02`, whose Sort is stable — but its three sort keys separate every distinguishable row in all four golden sets, so the order this table is written in cannot change `seg_02`'s result.
- `RUN_ID` is unused: contract C4 fixes the parameter list, and this segment has no query tag, no audit row and no pre/post SQL to put it in.
