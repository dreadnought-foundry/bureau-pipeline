"""The fleet budget reader's table (DRE-3202), driven off fake log text — no
`gh` call anywhere in here. The reader adds up the `linear-budget:` lines
linear_ops prints; these tests pin how it reads them and what it prints."""

import ast
import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
)

import check_linear_budget as clb  # noqa: E402
import linear_ops  # noqa: E402

# What `gh run view --log` prints: job, step, timestamp, then the line.
_PREFIX = "reconcile\tSweep\t2026-09-05T21:08:29.1234567Z "


def _log(*lines: str) -> str:
    return "\n".join(_PREFIX + line for line in lines) + "\n"


def test_spent_is_read_off_the_line_linear_ops_prints():
    """Producer and consumer agree on the format: compose the line with the
    seam's own function, then read it back."""
    linear_ops._reset_budget_state()
    linear_ops._note_response_headers(
        {"x-ratelimit-requests-remaining": "2400", "x-ratelimit-requests-reset": "1788645600000"}
    )
    linear_ops._note_response_headers({"x-ratelimit-requests-remaining": "2380"})
    assert clb.spent_from_log(_log("some other line", linear_ops.budget_line())) == [20]


def test_rolled_and_unknown_lines_count_as_seen_but_unknowable():
    log = _log(
        "linear-budget: 5 → 2499 (window rolled; window resets 16:00 PT)",
        "linear-budget: unknown (no rate-limit headers seen)",
        "linear-budget: 100 → 97 (spent 3 this run; window resets 16:00 PT; refused after 4 calls)",
    )
    assert clb.spent_from_log(log) == [None, None, 3]


def test_aggregate_sums_per_repo_and_workflow_sorted_by_total_desc():
    rows = clb.aggregate(
        [
            ("atlas", "Reconcile", _log("linear-budget: 900 → 880 (spent 20 this run; window resets 16:00 PT)")),
            ("atlas", "Reconcile", _log("linear-budget: 880 → 830 (spent 50 this run; window resets 16:00 PT)")),
            ("portico", "Medic", _log("linear-budget: 830 → 829 (spent 1 this run; window resets 16:00 PT)")),
            ("agent-bureau", "Reconcile", _log("linear-budget: 829 → 700 (spent 129 this run; window resets 16:00 PT)")),
            ("portico", "Reconcile", _log("no linear call in this run")),
        ]
    )
    assert [(r["repo"], r["workflow"], r["runs"], r["total"], r["max"]) for r in rows] == [
        ("agent-bureau", "Reconcile", 1, 129, 129),
        ("atlas", "Reconcile", 2, 70, 50),
        ("portico", "Medic", 1, 1, 1),
        ("portico", "Reconcile", 1, 0, 0),
    ]


def test_render_table_prints_unknown_for_an_unreadable_repo_and_a_grand_total():
    rows = clb.aggregate(
        [
            ("atlas", "Reconcile", _log("linear-budget: 900 → 880 (spent 20 this run; window resets 16:00 PT)")),
            ("portico", "Medic", _log("linear-budget: 880 → 879 (spent 1 this run; window resets 16:00 PT)")),
        ]
    )
    text = clb.render_table(rows, ["deltasolv"], hours=2, skipped=3)
    lines = text.splitlines()
    assert lines[0] == "linear budget, last 2h"
    assert "deltasolv" in text and "UNKNOWN" in text
    assert "0" not in [tok for tok in next(line for line in lines if "deltasolv" in line).split()[1:]]
    total_line = lines[-1]
    assert total_line.startswith("TOTAL") and " 21" in total_line
    assert "1 repo(s) UNKNOWN" in total_line
    assert "3 run(s) skipped" in total_line
    # sorted: atlas (20) above portico (1)
    assert text.index("atlas") < text.index("portico")


def test_the_reader_only_lists_and_views_runs():
    """READ-ONLY, pinned: the only `gh` verbs in the script are `run list` and
    `run view`. A write verb added later fails here before it fails live."""
    with open(clb.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    verbs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_gh":
            args = [a.value for a in node.args if isinstance(a, ast.Constant)]
            verbs.add(tuple(args[:2]))
    assert verbs == {("run", "list"), ("run", "view")}
