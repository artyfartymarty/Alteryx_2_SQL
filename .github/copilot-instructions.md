- This repo migrates Alteryx workflows to Snowflake. Never deploy, never write outside the current workflow's folder.
- Sandbox schemas: MIG_WORK (procedures under test), MIG_GOLDEN (golden data). Never reference production schemas by name in SQL.
- Sources come only from workflows/<id>/intake/mappings.yaml or mappings/global.yaml.
- Numbers about data (counts, diffs, tolerances) come only from scripts/compare.py output. Never estimate.
- One CTE per Alteryx tool, named t<toolid>_<tooltype>, with the tool id in a comment.
- If you are unsure what a tool does, mark it unknown and stop; do not guess semantics.
- Procedures follow docs/reference/dag-contract.md and plan contract C4: linear statement list, logical source and target names only.
- Three output targets (docs/reference/output-targets.md). `scripts/target_check.py` proposes one per segment in
  segments/targets.json; the analyzer copies it into contract.json as `"target"` and may only LOWER it
  (sql → snowpark → manual, never back towards sql). `sql` is today's stored procedure. `snowpark` is a Python
  stored procedure whose source of truth is proc.py (DataFrame API only, no session.sql); its proc.sql is
  RENDERED by scripts/render_snowpark.py and is never hand-written or hand-edited. A `dbt` workflow
  (manifest.json's `output_kind`) is ONE dbt project under workflows/<id>/dbt/ for the whole workflow, checked by
  `scripts/compile_check.py <id> --target dbt` and validated by `scripts/validate_dbt.py <id>`; agents never run
  `dbt` themselves. Read manifest.json's `output_kind` and contract.json's `target` before writing anything.
- Run every Python script with the venv interpreter, never bare `python`: `.venv/Scripts/python.exe` on Windows, `.venv/bin/python` elsewhere.
