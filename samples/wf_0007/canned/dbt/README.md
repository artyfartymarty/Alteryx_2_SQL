# wf_0007 — regional targets and attainment, as a dbt project

The whole workflow migrated as one dbt project (`output_kind: dbt`): three models, one per
contract output, run in dbt's own dependency order. There is no `master.sql` and no per-segment
procedure. **Nothing in this project has run on a real Snowflake account or a real Alteryx
engine**; it has been run only against the local DuckDB double (`dbt-duckdb`), by
`scripts/validate_dbt.py`.

| Model | Contract output | Materialisation |
|---|---|---|
| `models/wf0007_seg_01_out.sql` | `seg_01` work stream `3_J` (`MIG_WORK.WF0007_SEG_01_OUT`) | `table` |
| `models/region_attainment.sql` | `seg_02` target `REGION_ATTAINMENT` (tool 6, overwrite) | `table`, `alias='REGION_ATTAINMENT'` |
| `models/attainment_history.sql` | `seg_02` target `ATTAINMENT_HISTORY` (tool 7, Update; Insert if new) | `incremental`, `merge` on `REGION, PERIOD`, `alias='ATTAINMENT_HISTORY'` |

## Running it

Against Snowflake (the `snowflake` output of `profiles.yml`; **never run from this repository** —
`dbt-snowflake` is not installed here and is not part of `requirements.txt`):

```
dbt run --project-dir workflows/wf_0007/dbt --profiles-dir workflows/wf_0007/dbt --target snowflake --vars '{"src_schema": "<SRC>", "tgt_schema": "<TGT>"}'
```

Every connection value comes from the environment when dbt runs — `SNOWFLAKE_ACCOUNT`,
`SNOWFLAKE_USER`, `SNOWFLAKE_AUTHENTICATOR` (default `externalbrowser`),
`SNOWFLAKE_PRIVATE_KEY_PATH`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE` — and
`profiles.yml` itself holds no credential (it is `scripts/lib/dbt_project.py`'s
`PROFILES_TEMPLATE`, byte for byte).

Locally (the `local` output, DuckDB), the way `scripts/validate_dbt.py` runs it — always through
`scripts/lib/dbt_project.run_dbt`, never by hand from an agent session:

```
.venv/Scripts/python.exe scripts/validate_dbt.py wf_0007
```

That builds a fresh sandbox `workflows/wf_0007/dbt_sandbox_<set>.duckdb` per golden set, points
the profile at it through `MIG_DBT_DUCKDB_PATH`, and passes the flattened local schema names as
`--vars` (`{"src_schema": "MIGDB__MIG_GOLDEN_WF0007_<SET>", "tgt_schema": "MIGDB__MIG_WORK"}`).
Without `MIG_DBT_DUCKDB_PATH` the `local` output falls back to `dbt_sandbox.duckdb` in the working
directory.

## What `--vars` mean

- `src_schema` — the schema that holds the two mapped sources under their logical names,
  `TARGETS` and `ACTUALS` (`models/sources.yml`). In production that is the schema exposing
  `PLANNING.RAW.TARGETS` and `SALES.RAW.ACTUALS` under those names; in validation it is the golden
  view schema `MIG_GOLDEN_WF0007_<SET>`.
- `tgt_schema` — where every model is written: the work model `WF0007_SEG_01_OUT` and the two
  targets `REGION_ATTAINMENT` and `ATTAINMENT_HISTORY`. `ATTAINMENT_HISTORY` must already exist
  there with its history: the model merges into it rather than rebuilding it.

Pass both on every run: `dbt_project.yml` declares them as `null`, and `profiles.yml` reads
`tgt_schema` only from the command line's `--vars`.
