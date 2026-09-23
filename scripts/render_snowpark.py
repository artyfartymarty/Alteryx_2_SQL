"""Render segments/<seg>/proc.sql from proc.py: the LANGUAGE PYTHON wrapper with the C4 signature.

    render_snowpark.py <wf_id> <seg> [--root .]
"""
from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Sequence

from lib.io import read_yaml
from lib.paths import Repo, add_root_arg

TEMPLATE = """CREATE OR REPLACE PROCEDURE MIG_WORK.{wf}_{seg}(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '{runtime}'
PACKAGES = ('snowflake-snowpark-python', 'pandas')
HANDLER = 'run'
EXECUTE AS CALLER
AS
$$
{body}
$$;
"""


def render(proc_py: str, wf_id: str, seg: str, runtime: str) -> str:
    if "$$" in proc_py:
        raise ValueError("proc.py must not contain `$$` (it would end the procedure body)")
    return TEMPLATE.format(wf=wf_id.upper().replace("_", ""), seg=seg.upper(), runtime=runtime,
                           body=proc_py.rstrip("\n"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id")
    parser.add_argument("seg")
    add_root_arg(parser)
    args = parser.parse_args(argv)
    repo = Repo(args.root)
    try:
        proc_py = repo.seg(args.wf_id, args.seg, "proc.py").read_text(encoding="utf-8")
        program = (read_yaml(repo.global_mappings) or {}).get("program") or {}
        runtime = str(program.get("snowpark_runtime") or "3.11")
    except (FileNotFoundError, ValueError) as exc:  # a missing prerequisite: usage error, exit 2
        print(f"render_snowpark: {args.wf_id}/{args.seg}: {exc}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 2

    # proc.py itself is unrenderable (e.g. it contains `$$`): a domain failure, exit 1 -- not a
    # missing prerequisite (Task 3 fix round 1, coordinator ruling). Anything else render() raises
    # is a bug, not a domain failure or a usage error: exit 2, like every other unexpected
    # exception (Task 3 fix round 2, coordinator ruling) -- it must never escape uncaught.
    try:
        text = render(proc_py, args.wf_id, args.seg, runtime)
    except ValueError as exc:
        print(f"render_snowpark: {args.wf_id}/{args.seg}: {exc}", file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc()
        return 2

    try:
        target = repo.seg(args.wf_id, args.seg, "proc.sql")
        target.write_bytes(text.encode("utf-8"))
    except Exception:
        traceback.print_exc()
        return 2
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
