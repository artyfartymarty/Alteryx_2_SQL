"""`lib.validation.combine`'s duplicate-`checks`-key disambiguation (ruling R-B1) and the shared
`ordered_rows` helper `validate_dbt.py` uses for its idempotency snapshots.
"""
from __future__ import annotations

from lib import validation as v

OK = {"verdict": "PASS", "checks": {"row_count": "PASS"}, "diff_clusters": [],
      "normalizations_applied": [], "needs_human": False, "truncated": False}


def test_two_targets_on_one_stream_keep_both_checks():
    outputs = [({"stream": "5_Output", "kind": "target", "tool_id": "6"}, OK),
               ({"stream": "5_Output", "kind": "target", "tool_id": "7"}, OK),
               ({"stream": "3_J", "kind": "work"}, OK)]
    report = v.combine({"segment": "seg_02"}, "normal", outputs)
    assert set(report["checks"]) == {"5_Output:target:6", "5_Output:target:7", "3_J:work"}


def test_ordered_rows_sorts_by_every_column():
    from lib.backend import DuckDBBackend
    b = DuckDBBackend()
    b.execute("CREATE TABLE MIG_WORK.T AS SELECT * FROM (VALUES (2, 'b'), (1, NULL), (1, 'a')) AS x(A, B)")
    assert v.ordered_rows(b, "MIG_WORK.T")[0][0] == 1
    b.close()
