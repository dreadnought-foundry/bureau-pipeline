"""RED-first tests: the In QA→Todo requeue honours the shared hold cap (DRE-2034).

THE BUG: main()'s In-QA-no-PR branch requeues to Todo UNCAPPED — no DEAD_TAG
receipt, no REQUEUE_CAP check. A card whose PR read persistently misses (the
class the loud-read fix closes; or a genuinely vanished PR) loops In QA →
Todo → In Progress → In QA forever, burning an agent run per lap, while the
sibling In Progress path has been capped since DRE-1403.

FIX UNDER TEST: the In QA requeue counts the same shared DEAD_TAG as the In
Progress path — below the cap it requeues with a 🪦 DEAD_TAG receipt (so the
next lap sees a higher count), at the cap it parks the card in Backlog with
the needs-human label, exactly like the In Progress hold.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_failure_state(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    # getattr: _read_failures is the API under test — on the unfixed code it
    # does not exist yet, and the fixture must not mask the behavioral RED.
    reconcile._write_failures.clear()
    getattr(reconcile, "_read_failures", []).clear()
    yield
    reconcile._write_failures.clear()
    getattr(reconcile, "_read_failures", []).clear()


def _inqa_card():
    return {
        "identifier": "DRE-2034",
        "description": "**Repo:** agent-bureau\nwork",
        "state": {"name": "In QA"},
        "labels": {"nodes": []},
        "updatedAt": "2026-06-28T00:00:00Z",
    }


def _sweep_mocks(extra=None):
    m = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "age_minutes": MagicMock(return_value=999),  # always stale
        "pr_for": MagicMock(return_value=None),  # In QA, no PR
        "redispatch": MagicMock(return_value=True),
        "active_cards": MagicMock(return_value=[_inqa_card()]),
        # DRE-1993: the stranded-card watchdog runs on every full sweep and
        # would make real Linear calls on this mocked card; stub it out —
        # this test exercises the In-QA requeue cap, not the watchdog.
        "flag_stranded": MagicMock(return_value=set()),
    }
    if extra:
        m.update(extra)
    return m


def test_inqa_requeue_below_cap_counts_the_shared_dead_tag():
    """Below the cap the requeue still happens, but its receipt must carry
    DEAD_TAG so the next lap counts it. On the unfixed code this FAILS —
    the receipt is a plain 🧹 comment the counter never sees."""
    mocks = _sweep_mocks()
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "count_comments", return_value=1
    ), patch.object(reconcile.linear_ops, "add_label") as add_label, patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, patch.object(reconcile.linear_ops, "cmd_comment") as cmd_comment:
        reconcile.main()
    cmd_state.assert_called_once_with("DRE-2034", "Todo")
    add_label.assert_not_called()
    bodies = [c.args[1] for c in cmd_comment.call_args_list]
    assert any(reconcile.DEAD_TAG in b for b in bodies), (
        "the In QA requeue must count toward the shared dead-run cap"
    )


def test_inqa_requeue_at_cap_holds_instead_of_looping():
    """ACCEPTANCE: at the cap the card parks needs-human in Backlog — no
    third lap. On the unfixed code this FAILS: it requeues to Todo again."""
    mocks = _sweep_mocks()
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "count_comments", return_value=reconcile.REQUEUE_CAP
    ), patch.object(reconcile.linear_ops, "add_label") as add_label, patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.main()
    add_label.assert_called_once_with("DRE-2034", reconcile.HOLD_LABEL)
    # --park: deliberate HOLD-cap park, same DRE-1885 opt-out as In Progress.
    cmd_state.assert_called_once_with("DRE-2034", "Backlog", "--park")
    assert ("DRE-2034", "Todo") not in [c.args for c in cmd_state.call_args_list]


def test_inqa_with_open_pr_is_untouched_by_the_cap():
    """Control: the cap only guards the no-PR branch — an In QA card with an
    open PR and no verdict still just gets the qa-review re-nudge."""
    pr = {
        "number": 9,
        "headRefName": "agent/DRE-2034-loud-gh-reads",
        "state": "OPEN",
        "comments": [],
        "headRefOid": "a" * 40,
    }
    mocks = _sweep_mocks({
        "pr_for": MagicMock(return_value=pr),
        "_nudge": MagicMock(return_value=True),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "count_comments"
    ) as count_comments, patch.object(
        reconcile.linear_ops, "add_label"
    ) as add_label, patch.object(reconcile.linear_ops, "cmd_state") as cmd_state, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        reconcile.main()
    count_comments.assert_not_called()
    add_label.assert_not_called()
    cmd_state.assert_not_called()
    mocks["_nudge"].assert_called_once_with("qa-review.yml", 9)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
