"""README §1's diagrams draw what the code does; this pins their content and a structural lint.
Rendering is checked by the controller in a browser with Mermaid 11 (Task P5 Step 4)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
_TYPES = ("flowchart ", "sequenceDiagram", "stateDiagram-v2")
MUST_SHOW = ("validate_segment.py", "validate_snowpark.py", "validate_dbt.py", "validate_workflow.py",
             "check_seams.py", "plan_batches.py", "stitch_analysis.py", "--backend snowflake",
             "compile_check.py", "dbt/", "procs/README.md", "chain-drift")


def _blocks() -> list[str]:
    return _BLOCK.findall((ROOT / "README.md").read_text(encoding="utf-8"))


def test_every_block_is_a_known_diagram_type_and_structurally_balanced():
    blocks = _blocks()
    assert len(blocks) >= 3
    for block in blocks:
        assert block.lstrip().startswith(_TYPES), block[:40]
        assert "\t" not in block
        for opening, closing in ("[]", "()", "{}"):
            assert block.count(opening) == block.count(closing), (opening, block[:60])
        assert block.count('"') % 2 == 0
        subgraphs = len(re.findall(r"^\s*subgraph\b", block, re.MULTILINE))
        ends = len(re.findall(r"^\s*end\s*$", block, re.MULTILINE))
        loops = len(re.findall(r"^\s*(loop|alt|opt|par|critical|rect)\b", block, re.MULTILINE))
        assert ends == subgraphs + loops, block[:60]


def test_the_diagrams_show_the_targets_the_chain_the_batches_and_the_backend_switch():
    text = "\n".join(_blocks())
    missing = [item for item in MUST_SHOW if item not in text]
    assert not missing, missing
