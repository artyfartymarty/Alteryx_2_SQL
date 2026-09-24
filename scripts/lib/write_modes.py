"""A segment's final targets and the write mode each must be written in (live hardening, Task L4).

`compile_check.py`'s `c4:write_mode` (the `sql` target) and `lib.snowpark_rules`' `rule:write_mode`
(the `snowpark` target, also applied by `validate_snowpark.py` before it imports a module) read a
target's write mode here: the contract's `outputs[].write_mode`, else the `mode` `intake/mappings.yaml`
gives the same logical (`lib.vocab.TARGET_WRITE_MODES` names every spelling), and whether the Output
tool has a PreSQL/PostSQL (`segments/<seg>/dag.json`).
"""
from __future__ import annotations

from dataclasses import dataclass

from .io import read_json, read_yaml
from .paths import Repo
from .vocab import TARGET_WRITE_MODES


def mappings_of(repo: Repo, wf_id: str) -> dict:
    """`intake/mappings.yaml`, or `{}` when intake has not written it."""
    path = repo.wf(wf_id, "intake", "mappings.yaml")
    return (read_yaml(path) or {}) if path.is_file() else {}


def dag_nodes(repo: Repo, wf_id: str, seg: str) -> list[dict]:
    """The segment's `dag.json` nodes, or `[]` when it has none."""
    path = repo.seg(wf_id, seg, "dag.json")
    return list(read_json(path).get("nodes") or []) if path.is_file() else []


def _has_sql(config: dict, key: str) -> bool:
    return bool(str(config.get(key) or "").strip())


class ContractError(ValueError):
    """The contract (or intake's mappings) does not say what a check needs -- a target's write mode,
    or a merge target's keys. Nothing in the procedure can fix that, so it is a usage error (exit 2),
    not a compile error: the analyzer's contract, or intake's mappings, has to say it."""


@dataclass(frozen=True)
class TargetWrite:
    """One final target of a segment, as `c4:write_mode` (and the Snowpark `rule:write_mode`) judge
    it: its logical name, its write mode (`mode`, the `TARGET_WRITE_MODES` form; `spelling`, as the
    contract or the mappings wrote it), the MERGE keys, and whether its Output tool has a PreSQL /
    PostSQL (`dag.json` node config) -- the only statements besides the write that may touch it."""
    logical: str
    mode: str
    spelling: str
    keys: tuple[str, ...]
    tool_id: str
    pre_sql: bool
    post_sql: bool


def target_writes(contract: dict, mappings: dict | None = None, nodes: list[dict] | None = None) -> list[TargetWrite]:
    """Every `kind: "target"` output of `contract` with a logical name (one without is `_fixtures`'
    error), with its write mode: the contract's `write_mode`, else the mode `intake/mappings.yaml`
    gives the same logical. A target with neither, or with a mode outside `TARGET_WRITE_MODES`, or
    a merge target with no keys in either file, is a `ContractError`."""
    outputs_map = (mappings or {}).get("outputs") or {}
    by_tool = {str(node.get("tool_id")): node for node in nodes or []}
    targets = []
    for output in contract.get("outputs") or ([contract["output"]] if contract.get("output") else []):
        logical = output.get("logical")
        if output.get("kind") != "target" or not logical:
            continue
        mapping = next((entry for entry in outputs_map.values()
                        if isinstance(entry, dict) and entry.get("logical") == logical), None) or {}
        spelling = output.get("write_mode") or mapping.get("mode")
        if not spelling:
            raise ContractError(f"c4:write_mode: target {logical} has no write_mode in contract.json's outputs[] "
                                f"and no mode in intake/mappings.yaml, so the form its write must take is unknown")
        mode = TARGET_WRITE_MODES.get(str(spelling))
        if mode is None:
            raise ContractError(f"c4:write_mode: target {logical} has write_mode {spelling!r}; a write mode is one of "
                                f"{', '.join(sorted(TARGET_WRITE_MODES))}")
        keys = [str(key) for key in (output.get("keys") or mapping.get("keys") or [])]
        if mode == "update_insert" and not keys:
            raise ContractError(f"c4:write_mode: target {logical} is {spelling}, and neither contract.json's "
                                f"outputs[].keys nor intake/mappings.yaml's keys name the keys its MERGE matches on")
        tool_id = str(output.get("tool_id") or "")
        config = (by_tool.get(tool_id) or {}).get("config") or {}
        targets.append(TargetWrite(logical=str(logical), mode=mode, spelling=str(spelling), keys=tuple(keys),
                                   tool_id=tool_id, pre_sql=_has_sql(config, "pre_sql"),
                                   post_sql=_has_sql(config, "post_sql")))
    return targets
