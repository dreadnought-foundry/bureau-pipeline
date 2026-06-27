"""TDD for epic activation at Todo, ADDITIVE to In Progress (DRE-1893).

CEO decision: an epic activates the moment the CEO moves it to **Todo**
(lifecycle Backlog → Planning → Todo). In Progress / In QA are card states +
system progression, not the CEO's activation action. The dependency gate
historically promoted a Backlog child only when its parent epic was **In
Progress**; this widens "active" to Todo OR In Progress, purely ADDITIVE — In
Progress keeps working exactly as before, MAX_WIP and the blocker checks are
unchanged.

FIX UNDER TEST: reconcile.EPIC_ACTIVE_STATES = ("Todo", "In Progress") and the
promote_ready parent check `parent["state"]["name"] not in EPIC_ACTIVE_STATES`.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import reconcile  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so promote_ready
    recognises this test's agent-bureau cards regardless of collection order."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


def _child(parent_state: str, *, blocked_by: str = "", identifier: str = "DRE-2"):
    """A Backlog child of an epic in `parent_state`, repo by label, no agent
    blocker. `blocked_by` adds a "Blocked by: DRE-N" description line so the
    blocker gate can hold it; empty = no formal blocker."""
    desc = "Wire the thing."
    if blocked_by:
        desc += f"\n\n**Blocked by:** {blocked_by}"
    return {
        "identifier": identifier,
        "description": desc,
        "parent": {"identifier": "DRE-1", "state": {"name": parent_state}},
        "labels": {"nodes": [{"name": "agent:engineer"}, {"name": "repo:agent-bureau"}]},
        "comments": {"nodes": []},
        "inverseRelations": {"nodes": []},
    }


# --------------------------------------------------------------------------
# EPIC_ACTIVE_STATES — the activation set
# --------------------------------------------------------------------------
def test_active_states_include_todo_and_in_progress():
    assert "Todo" in reconcile.EPIC_ACTIVE_STATES
    assert "In Progress" in reconcile.EPIC_ACTIVE_STATES


# --------------------------------------------------------------------------
# promote_ready — Todo parent is now activation (additive)
# --------------------------------------------------------------------------
def test_todo_epic_promotes_unblocked_child():
    """The new behavior: an epic in Todo (the CEO's activation) promotes its
    unblocked Backlog children to Todo."""
    reconcile._write_failures.clear()
    card = _child("Todo")
    with patch.object(reconcile, "backlog_children", return_value=[card]), patch.object(
        reconcile, "epic_blockers_unmet", return_value=False
    ), patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    advance.assert_called_once_with("DRE-2", "Todo", "Backlog")


def test_in_progress_epic_still_promotes_unblocked_child():
    """Regression: the pre-existing In Progress trigger is untouched."""
    reconcile._write_failures.clear()
    card = _child("In Progress")
    with patch.object(reconcile, "backlog_children", return_value=[card]), patch.object(
        reconcile, "epic_blockers_unmet", return_value=False
    ), patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    advance.assert_called_once_with("DRE-2", "Todo", "Backlog")


def test_todo_epic_with_unfinished_blocker_does_not_promote():
    """A Todo epic's child whose own blocker (DRE-9) is NOT yet Done stays
    parked — Todo activation does not bypass the blocker checks (unchanged)."""
    reconcile._write_failures.clear()
    card = _child("Todo", blocked_by="DRE-9")
    with patch.object(reconcile, "backlog_children", return_value=[card]), patch.object(
        reconcile, "epic_blockers_unmet", return_value=False
    ), patch.object(reconcile, "card_state", return_value="In Progress"), patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as advance, patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    advance.assert_not_called()


def test_backlog_epic_still_does_not_promote():
    """Scope guard: a not-yet-activated epic (still Backlog/Planning) never
    promotes its children — only Todo and In Progress count as active."""
    reconcile._write_failures.clear()
    for inactive in ("Backlog", "Planning", "Plan Review", "Done"):
        card = _child(inactive)
        with patch.object(reconcile, "backlog_children", return_value=[card]), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
            reconcile.linear_ops, "cmd_comment"
        ):
            promoted = reconcile.promote_ready(active_count=0)
        assert promoted == 0, f"epic in {inactive} must not activate children"
        advance.assert_not_called()
