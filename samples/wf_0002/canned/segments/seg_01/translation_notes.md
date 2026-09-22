# wf_0002 / seg_01 — translation notes

One assumption per line. Nothing below has been checked against a real Alteryx engine or a real
Snowflake account; every claim is against `docs/reference/simulator-semantics.md` (the parity
oracle), `docs/reference/dag-contract.md` §4 and program spec §8.

- The segment is Tool Container 100 ("Prep customers"), tools 1 and 2; the container itself carries no data and gets no CTE.
- Its one outbound stream, `2_Output`, feeds the Join's Left anchor in seg_03, so it is materialised as the primary work table `MIG_WORK.WF0002_SEG_01_OUT` (contract C3) with `CREATE OR REPLACE TRANSIENT TABLE`: the table is rebuilt from scratch on every run and needs no Time Travel.
- Tool 1 reads `CUSTOMERS`, the logical name `intake/mappings.yaml` records for `crm/customers.yxdb`; the procedure never names the mapped Snowflake table.
- Tool 2 has no `Plugin` attribute and is classified `data_cleansing` from `<EngineSettings Macro="Cleanse.yxmc">`; its options are read from the `<Value name="…">` pairs, not guessed.
- The options applied are exactly the three the configuration turns on — replace NULL strings with blank, trim whitespace, modify case to upper — and only on the two fields the List Box names, `NAME` and `CITY`; `CUST_ID` and `TIER` pass through untouched.
- The nesting order `UPPER(TRIM(COALESCE(x, '')))` is the macro's own order (replace nulls, then trim, then modify case), not an arbitrary one: trimming a NULL would leave it NULL, and the tool promises a blank.
- Because "replace NULL strings with blank" runs first, `NAME` and `CITY` are never NULL on this stream, which is why the contract declares both `NOT NULL`; the broken variant `02_cleansing_keeps_null_names.sql` trips that check.
- `TRIM` is Snowflake's, which removes spaces; the oracle's trim removes whitespace generally, so a tab or a newline around a value would survive here and not there — no golden set contains one, so the divergence is recorded rather than guarded against.
- `UPPER` uses Snowflake's own Unicode case rules; the `edge` golden set carries Latin Extended and CJK text and agrees with the oracle on this runtime, but the two are not guaranteed to agree for every script.
- `contract.output.keys` is `[]`: the `edge` golden set holds `CUST_ID` 12 twice as byte-identical rows, so no column set identifies a row and the comparison falls back to a row multiset, which is exact about whether the rows match and attributes a difference to a column only by nearest-match pairing, a heuristic.
- `RUN_ID` is unused: contract C4 fixes the parameter list, and this segment has no query tag, no audit row and no pre/post SQL to put it in.
