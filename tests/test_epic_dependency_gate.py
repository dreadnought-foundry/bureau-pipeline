"""TDD for EPIC-LEVEL dependency enforcement in the reconcile sweep (DRE-1772).

The card-level gate (promote_ready) already holds a Backlog child until its
OWN blockers are Done. But nothing enforced dependencies between EPICS: an epic
B that is `blocked-by` epic A could be moved In Progress (plan approved) and its
children would promote and start building even though A — the prerequisite
epic — had not shipped. And when A finally finished, nothing pulled B into the
pipeline; an operator had to notice and hand-promote it.

This module TDDs two additive behaviors:

  1. EPIC-LEVEL GATE — before promoting an epic's children, check that EPIC's
     own `blocked-by` relations; if ANY blocker epic is not Done, promote NONE
     of that epic's children this sweep, regardless of the epic's own state.
     Composes with (does not replace) the card-level gate, MAX_WIP, and the
     DRE-1585 unresolved-agent-blocker guard. Fails SAFE: missing/ambiguous
     relation data → treat as blocked (do not promote).

  2. AUTO-ADVANCE — when a blocker epic reaches Done, for each epic blocked-by
     it: if ALL its blocker epics are now Done AND it is still in Backlog, move
     it to Triage (which triggers the planner). NEVER to In Progress — the Plan
     Review approval gate is preserved. Idempotent: never re-advance an epic
     already past Backlog; never thrash an operator-parked epic. Fails SAFE.

Reconcile governs promotion for EVERY product repo, so a bug here breaks
everyone — hence test-first.

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


def _child(identifier="DRE-900", parent="DRE-800", parent_state="In Progress"):
    """A Backlog child eligible on every existing ground (active parent epic,
    no formal card blockers, no agent-blocker) — so ONLY the new epic-level
    gate can hold it back."""
    return {
        "identifier": identifier,
        "description": "**Repo:** agent-bureau\nwork",
        "parent": {"identifier": parent, "state": {"name": parent_state}},
        "labels": {"nodes": [{"name": "size:M"}]},
        "comments": {"nodes": []},
        "inverseRelations": {"nodes": []},
    }


# ---------------------------------------------------------------------------
# Behavior 1: epic-level gate — epic_blockers_unmet(epic_identifier)
# ---------------------------------------------------------------------------
def test_epic_with_unmet_blocker_epic_reports_unmet():
    """Epic B blocked-by epic A (A is In Progress, not Done) → B's blockers
    are unmet. (Relation read via the epic's inverseRelations, like cards.)"""
    epic_b = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\nepic B",
        "inverseRelations": {
            "nodes": [{"type": "blocks", "issue": {"identifier": "DRE-700", "state": {"name": "In Progress"}}}]
        },
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic_b):
        assert reconcile.epic_blockers_unmet("DRE-800") is True


def test_epic_with_all_blocker_epics_done_reports_clear():
    """Epic B blocked-by epic A, A Done → B's blockers are met (False)."""
    epic_b = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\nepic B",
        "inverseRelations": {
            "nodes": [{"type": "blocks", "issue": {"identifier": "DRE-700", "state": {"name": "Done"}}}]
        },
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic_b):
        assert reconcile.epic_blockers_unmet("DRE-800") is False


def test_epic_with_no_blockers_reports_clear():
    """An epic with no blocker relations and no blocked-by lines is clear."""
    epic_b = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\nepic B",
        "inverseRelations": {"nodes": []},
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic_b):
        assert reconcile.epic_blockers_unmet("DRE-800") is False


def test_epic_blocked_by_description_line_reports_unmet():
    """A 'Blocked by: DRE-700' line on the EPIC counts (A not Done)."""
    epic_b = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\nBlocked by: DRE-700\nepic B",
        "inverseRelations": {"nodes": []},
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic_b), patch.object(
        reconcile, "card_state", return_value="In Progress"
    ):
        assert reconcile.epic_blockers_unmet("DRE-800") is True


def test_epic_relations_unreadable_fails_safe_unmet():
    """Fail SAFE: if the epic's relation data can't be read, treat as blocked."""
    with patch.object(reconcile, "_fetch_epic_relations", return_value=None):
        assert reconcile.epic_blockers_unmet("DRE-800") is True


# ---------------------------------------------------------------------------
# Behavior 1 wired into promote_ready: B blocked-by A, A not Done →
# B's Backlog cards NOT promoted even though B is In Progress.
# ---------------------------------------------------------------------------
def test_promote_ready_skips_children_of_blocked_epic():
    reconcile._write_failures.clear()
    child = _child(identifier="DRE-900", parent="DRE-800", parent_state="In Progress")
    with patch.object(reconcile, "backlog_children", return_value=[child]), patch.object(
        reconcile, "epic_blockers_unmet", return_value=True
    ), patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    advance.assert_not_called()


def test_promote_ready_promotes_children_of_unblocked_epic():
    """Control: epic's own blockers are all Done → children promote as before."""
    reconcile._write_failures.clear()
    child = _child(identifier="DRE-900", parent="DRE-800", parent_state="In Progress")
    with patch.object(reconcile, "backlog_children", return_value=[child]), patch.object(
        reconcile, "epic_blockers_unmet", return_value=False
    ), patch.object(reconcile, "card_state", return_value="Done"), patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as advance, patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    advance.assert_called_once_with("DRE-900", "Todo", "Backlog")


def test_promote_ready_evaluates_each_epic_once_per_sweep():
    """Two children of the SAME blocked epic → the epic gate is consulted but
    neither child promotes (and the gate is not spammed per-child)."""
    reconcile._write_failures.clear()
    kids = [
        _child(identifier="DRE-900", parent="DRE-800"),
        _child(identifier="DRE-901", parent="DRE-800"),
    ]
    with patch.object(reconcile, "backlog_children", return_value=kids), patch.object(
        reconcile, "epic_blockers_unmet", return_value=True
    ) as gate, patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    advance.assert_not_called()
    assert gate.call_count == 1  # consulted once per epic, not once per child


# ---------------------------------------------------------------------------
# Behavior 2: auto-advance — advance_unblocked_epics(done_epic)
# ---------------------------------------------------------------------------
def _forward_blocks(done_epic, *blocked_epics):
    """gql shape for the epic that just went Done: its forward `relations`
    naming the epics it blocks."""
    return {
        "issue": {
            "relations": {
                "nodes": [
                    {"type": "blocks", "issue": {"identifier": b}} for b in blocked_epics
                ]
            }
        }
    }


def test_done_blocker_epic_advances_dependent_backlog_to_triage():
    """A→Done, B blocked-by only A, B in Backlog → B moves Backlog→Triage."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_forward_blocks("DRE-700", "DRE-800")), \
        patch.object(reconcile, "card_state", return_value="Backlog"), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile.advance_unblocked_epics("DRE-700")
    advance.assert_called_once_with("DRE-800", "Triage", "Backlog")
    comment.assert_called_once()


def test_advance_never_moves_epic_to_in_progress():
    """The Plan Review approval gate is preserved: target is Triage, NOT
    In Progress — assert the literal transition target."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_forward_blocks("DRE-700", "DRE-800")), \
        patch.object(reconcile, "card_state", return_value="Backlog"), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.advance_unblocked_epics("DRE-700")
    _, args, _ = advance.mock_calls[0]
    assert args[1] == "Triage"
    assert "In Progress" not in args


def test_advance_holds_epic_with_another_unmet_blocker():
    """B blocked-by A AND C; A Done but C not Done → B stays in Backlog."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_forward_blocks("DRE-700", "DRE-800")), \
        patch.object(reconcile, "card_state", return_value="Backlog"), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=True), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.advance_unblocked_epics("DRE-700")
    advance.assert_not_called()


def test_advance_is_idempotent_for_already_advanced_epic():
    """B already past Backlog (In Progress / Triage) → never re-advanced; the
    operator-parked / already-running epic is never thrashed."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_forward_blocks("DRE-700", "DRE-800")), \
        patch.object(reconcile, "card_state", return_value="In Progress"), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.advance_unblocked_epics("DRE-700")
    advance.assert_not_called()


def test_advance_does_not_revive_canceled_epic():
    """A dropped (Canceled) dependent epic is never pulled back into the flow."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_forward_blocks("DRE-700", "DRE-800")), \
        patch.object(reconcile, "card_state", return_value="Canceled"), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance:
        reconcile.advance_unblocked_epics("DRE-700")
    advance.assert_not_called()


def test_advance_fails_safe_when_relations_unreadable():
    """If the just-Done epic's forward relations can't be read, advance nothing."""
    with patch.object(reconcile.linear_ops, "gql", return_value={"issue": None}), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance:
        reconcile.advance_unblocked_epics("DRE-700")
    advance.assert_not_called()


# ---------------------------------------------------------------------------
# Behavior 2 wiring: close_finished_epics auto-advances the chain on merge,
# and the full sweep is the backstop.
# ---------------------------------------------------------------------------
def test_close_finished_epics_advances_chain_when_an_epic_closes():
    """When close_finished_epics closes an epic, it advances the epics that
    epic was blocking (merge-time hook)."""
    kids = {"issue": {"children": {"nodes": [{"state": {"name": "Done"}}, {"state": {"name": "Done"}}]}}}
    with patch.object(reconcile.linear_ops, "gql", return_value=kids), \
        patch.object(reconcile.linear_ops, "cmd_state"), \
        patch.object(reconcile.linear_ops, "cmd_comment"), \
        patch.object(reconcile, "advance_unblocked_epics") as advance_chain:
        reconcile.close_finished_epics({"DRE-700"})
    advance_chain.assert_called_once_with("DRE-700")


def test_close_finished_epics_no_advance_when_epic_stays_open():
    """No close → no chain advance."""
    kids = {"issue": {"children": {"nodes": [{"state": {"name": "Done"}}, {"state": {"name": "In Progress"}}]}}}
    with patch.object(reconcile.linear_ops, "gql", return_value=kids), \
        patch.object(reconcile.linear_ops, "cmd_state"), \
        patch.object(reconcile.linear_ops, "cmd_comment"), \
        patch.object(reconcile, "advance_unblocked_epics") as advance_chain:
        reconcile.close_finished_epics({"DRE-700"})
    advance_chain.assert_not_called()
