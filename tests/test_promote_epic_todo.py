"""An epic in Todo activates nothing; the sweep carries it out (DRE-5347).

DRE-1893 once let an epic activate at Todo as well as In Progress. This epic
retires that convention: approval is the move to **In Progress** and only that
(DRE-5316 says why a Green Light to Todo drag is re-planned rather than read as
approval). An epic found in Todo is carried out of it on every full sweep
(`reconcile.carry_epics_out_of_todo`, tested in test_reconcile_epic_carry.py)
— to In Progress when it was approved, to Planning when it was not.

The case that makes the activation set matter is the window before the carry.
An epic dragged from Green Light to Todo is unapproved, and with Todo in the set
the promoter would release its verdict-carrying children in that window. With
In Progress alone, an approved epic dragged to Todo pauses its children for at
most one sweep, and an unapproved one releases nothing.

FIX UNDER TEST: reconcile.EPIC_ACTIVE_STATES = ("In Progress",) and the
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
import routing_verdict  # noqa: E402

# Since DRE-3385 a Backlog card carrying NO routing verdict is refused
# promotion outright, so every candidate below carries one — a fixture held
# back for the missing verdict would say nothing about the gate under test.
FLEET_VERDICT = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")



@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so promote_ready
    recognises this test's agent-bureau cards regardless of collection order."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


def _child(parent_state: str, *, blocked_by: str = "", identifier: str = "DRE-2",
           blocker_state: str = "In Progress"):
    """A Backlog child of an epic in `parent_state`, repo by label, no agent
    blocker. `blocked_by` gives it a real `blockedBy` relation — which is what
    the gate reads (DRE-2676) — plus the `**Blocked by:**` line that documents
    it for humans; empty = no blocker at all."""
    desc = "Wire the thing."
    relations = []
    if blocked_by:
        desc += f"\n\n**Blocked by:** {blocked_by}"
        relations.append({
            "type": "blocks",
            "issue": {"identifier": blocked_by, "state": {"name": blocker_state}},
        })
    return {
        "identifier": identifier,
        "description": desc,
        "parent": {"identifier": "DRE-1", "state": {"name": parent_state}},
        "labels": {"nodes": [{"name": "agent:engineer"}, {"name": "repo:agent-bureau"}]},
        "comments": {"nodes": [{"body": FLEET_VERDICT}]},
        "inverseRelations": {"nodes": relations},
    }


# --------------------------------------------------------------------------
# EPIC_ACTIVE_STATES — the activation set
# --------------------------------------------------------------------------
def test_only_in_progress_activates_an_epic():
    assert reconcile.EPIC_ACTIVE_STATES == ("In Progress",)
    assert "Todo" not in reconcile.EPIC_ACTIVE_STATES


# --------------------------------------------------------------------------
# promote_ready — a Todo parent releases nothing
# --------------------------------------------------------------------------
def _promote(card):
    reconcile._write_failures.clear()
    with patch.object(reconcile, "backlog_children", return_value=[card]), patch.object(
        reconcile, "epic_blockers_unmet", return_value=False
    ), patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        promoted = reconcile.promote_ready(active_count=0)
    return promoted, advance


def test_a_fleet_child_of_an_epic_in_todo_is_not_released(capsys):
    """The window before the carry: an epic dragged into Todo — from Green
    Light, unapproved — must not release a child carrying a FLEET verdict.
    The sweep says why, in the parent-not-active words."""
    promoted, advance = _promote(_child("Todo"))
    assert promoted == 0
    advance.assert_not_called()
    assert "is not active (Todo)" in capsys.readouterr().out


def test_a_fleet_child_of_an_epic_in_progress_is_still_released():
    """Regression: approval is the move to In Progress, and it still releases
    the unblocked children."""
    promoted, advance = _promote(_child("In Progress"))
    assert promoted == 1
    advance.assert_called_once_with("DRE-2", "Todo", "Backlog")


def test_in_progress_epic_with_unfinished_blocker_does_not_promote():
    """An active epic's child whose own blocker (DRE-9) is NOT yet Done stays
    parked — activation does not bypass the blocker checks (unchanged)."""
    reconcile._write_failures.clear()
    card = _child("In Progress", blocked_by="DRE-9")
    with patch.object(reconcile, "backlog_children", return_value=[card]), patch.object(
        reconcile, "epic_blockers_unmet", return_value=False
    ), patch.object(reconcile, "card_state", return_value="In Progress"), patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as advance, patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    advance.assert_not_called()


def test_no_lane_but_in_progress_activates_children():
    """Scope guard: an epic anywhere but In Progress — Todo included — never
    promotes its children."""
    for inactive in ("Todo", "Backlog", "Planning", "Green Light", "Done"):
        promoted, advance = _promote(_child(inactive))
        assert promoted == 0, f"epic in {inactive} must not activate children"
        advance.assert_not_called()
