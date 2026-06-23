"""TDD for the sticky human-hold that breaks the dead/hung-run requeue loop.

Origin: DRE-1331 (2026-06-13) bundled 4 mobile screens, hung the engineer
agent repeatedly, produced 0 PRs, and looped for ~2h without alerting anyone.
The agent-task 3-strike cap only counts *completed* dead runs, so HUNG /
timed-out runs (which never reach the agent-task report step — only reconcile
sees them) never hit the cap. Reconcile then requeued the In-Progress-no-PR
card every 60 min forever, and the "hold for a human" state was not sticky
(reconcile re-requeued / re-promoted it). Medic never fired because the run
never *completed as a failure* within reconcile's window.

FIX UNDER TEST:
  - linear_ops.add_label(identifier, name): create-if-missing then attach,
    idempotent — stamps the 'needs-human' hold label.
  - reconcile: one shared dead-run cap (counts the 'dead-run-requeue' tag
    across BOTH agent-task and reconcile). After the cap the In-Progress-no-PR
    path HOLDS (label + Backlog + 🚨 alert) instead of requeueing — so hung
    runs are caught too.
  - reconcile.held()/promote_ready/sweep: a card carrying 'needs-human' is
    never requeued, nudged, or auto-promoted until a human removes the label.

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

import linear_ops  # noqa: E402
import reconcile  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; in the full suite an earlier
    test file can fix it to the 'atlas' default before we set the env. Pin it
    so the real promote_ready/sweep recognise this test's agent-bureau cards
    regardless of collection order (test-isolation hazard)."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


# --------------------------------------------------------------------------
# linear_ops.add_label — create-if-missing, attach, idempotent
# --------------------------------------------------------------------------
def _issue_label_payload(labels):
    return {
        "issue": {
            "id": "uuid-1",
            "team": {"id": "team-1"},
            "labels": {"nodes": labels},
        }
    }


def test_add_label_idempotent_when_present():
    """Already-labelled issue: read only, never mutate."""
    payload = _issue_label_payload([{"id": "L1", "name": "needs-human"}])
    with patch.object(linear_ops, "gql", return_value=payload) as g:
        linear_ops.add_label("DRE-1", "needs-human")
    assert g.call_count == 1  # the read; no create, no issueUpdate


def test_add_label_reuses_existing_team_label():
    """Existing team label is attached without creating a new one."""
    seq = [
        _issue_label_payload([]),  # issue read
        {"team": {"labels": {"nodes": [{"id": "L9", "name": "needs-human"}]}}},
        {"issueUpdate": {"success": True}},  # attach
    ]
    with patch.object(linear_ops, "gql", side_effect=seq) as g:
        linear_ops.add_label("DRE-1", "needs-human")
    assert g.call_count == 3
    joined = " ".join(str(c.args[0]) for c in g.call_args_list)
    assert "issueLabelCreate" not in joined
    assert g.call_args_list[2].args[1]["input"]["labelIds"] == ["L9"]


def test_add_label_creates_when_missing():
    """No matching team label: create it, then attach alongside existing ids."""
    seq = [
        _issue_label_payload([{"id": "L0", "name": "other"}]),  # issue read
        {"team": {"labels": {"nodes": [{"id": "Lx", "name": "other"}]}}},  # no match
        {"issueLabelCreate": {"issueLabel": {"id": "Lnew"}}},  # create
        {"issueUpdate": {"success": True}},  # attach
    ]
    with patch.object(linear_ops, "gql", side_effect=seq) as g:
        linear_ops.add_label("DRE-1", "needs-human")
    assert g.call_count == 4
    assert g.call_args_list[3].args[1]["input"]["labelIds"] == ["L0", "Lnew"]


# --------------------------------------------------------------------------
# linear_ops.remove_label — the generic detach helper (idempotent inverse of
# add_label). It once cleared the retired `proposed` propose-gate marker
# (DRE-1660); that machinery is gone (escalate-by-exception, DRE-1655/1662) but
# the helper stays, so these pin its generic behavior on an example label.
# --------------------------------------------------------------------------
def test_remove_label_noop_when_absent():
    """Card without the label: read only, never mutate (idempotent no-op)."""
    payload = {"issue": {"id": "uuid-1", "labels": {"nodes": []}}}
    with patch.object(linear_ops, "gql", return_value=payload) as g:
        linear_ops.remove_label("DRE-1", "stale")
    assert g.call_count == 1  # the read; no issueUpdate


def test_remove_label_detaches_when_present():
    """Present label is removed; other labels are preserved."""
    seq = [
        {
            "issue": {
                "id": "uuid-1",
                "labels": {
                    "nodes": [
                        {"id": "Lp", "name": "stale"},
                        {"id": "Lk", "name": "size:M"},
                    ]
                },
            }
        },
        {"issueUpdate": {"success": True}},
    ]
    with patch.object(linear_ops, "gql", side_effect=seq) as g:
        linear_ops.remove_label("DRE-1", "stale")
    assert g.call_count == 2
    # Only the surviving (non-removed) label id is written back.
    assert g.call_args_list[1].args[1]["input"]["labelIds"] == ["Lk"]


def test_remove_label_case_insensitive():
    """Matches the label name case-insensitively, like add_label."""
    seq = [
        {"issue": {"id": "uuid-1", "labels": {"nodes": [{"id": "Lp", "name": "Stale"}]}}},
        {"issueUpdate": {"success": True}},
    ]
    with patch.object(linear_ops, "gql", side_effect=seq) as g:
        linear_ops.remove_label("DRE-1", "stale")
    assert g.call_count == 2
    assert g.call_args_list[1].args[1]["input"]["labelIds"] == []


# --------------------------------------------------------------------------
# reconcile.held
# --------------------------------------------------------------------------
def test_held_true_with_label():
    assert reconcile.held({"labels": {"nodes": [{"name": "needs-human"}]}}) is True


def test_held_false_without_label():
    assert reconcile.held({"labels": {"nodes": [{"name": "size:M"}]}}) is False


def test_held_handles_missing_labels():
    assert reconcile.held({}) is False


# --------------------------------------------------------------------------
# reconcile sweep: shared cap → sticky hold
# --------------------------------------------------------------------------
def _full_sweep_mocks(extra=None):
    m = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "age_minutes": MagicMock(return_value=999),  # always stale
        "pr_for": MagicMock(return_value=None),  # no PR
    }
    if extra:
        m.update(extra)
    return m


def _inprogress_card(labels=None):
    return {
        "identifier": "DRE-1331",
        "description": "**Repo:** agent-bureau\nwork",
        "state": {"name": "In Progress"},
        "labels": {"nodes": labels or []},
        "updatedAt": "2026-06-12T00:00:00Z",
    }


def test_in_progress_holds_after_cap():
    """≥ cap dead-run-requeue tags → label + Backlog, NOT another Todo requeue."""
    reconcile._write_failures.clear()
    mocks = _full_sweep_mocks({"active_cards": MagicMock(return_value=[_inprogress_card()])})
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "count_comments", return_value=2
    ), patch.object(reconcile.linear_ops, "add_label") as add_label, patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.main()
    add_label.assert_called_once_with("DRE-1331", reconcile.HOLD_LABEL)
    cmd_state.assert_called_once_with("DRE-1331", "Backlog")
    assert ("DRE-1331", "Todo") not in [c.args for c in cmd_state.call_args_list]


def test_in_progress_requeues_below_cap():
    """Below the cap, the dead run still requeues to Todo (existing behaviour)."""
    reconcile._write_failures.clear()
    mocks = _full_sweep_mocks({"active_cards": MagicMock(return_value=[_inprogress_card()])})
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "count_comments", return_value=1
    ), patch.object(reconcile.linear_ops, "add_label") as add_label, patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.main()
    cmd_state.assert_called_once_with("DRE-1331", "Todo")
    add_label.assert_not_called()


def test_sweep_skips_held_card():
    """A card already carrying the hold label is left completely alone."""
    reconcile._write_failures.clear()
    held = _inprogress_card(labels=[{"name": "needs-human"}])
    mocks = _full_sweep_mocks({"active_cards": MagicMock(return_value=[held])})
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment, patch.object(reconcile.linear_ops, "add_label") as add_label:
        reconcile.main()
    cmd_state.assert_not_called()
    cmd_comment.assert_not_called()
    add_label.assert_not_called()


# --------------------------------------------------------------------------
# reconcile.promote_ready: never auto-promote a held card
# --------------------------------------------------------------------------
def _backlog_candidate(labels):
    return {
        "identifier": "DRE-1331",
        "description": "**Repo:** agent-bureau\nwork",
        "parent": {"identifier": "DRE-1268", "state": {"name": "In Progress"}},
        "labels": {"nodes": labels},
        "inverseRelations": {"nodes": []},
    }


def test_promote_ready_skips_held_card():
    reconcile._write_failures.clear()
    with patch.object(
        reconcile, "backlog_children", return_value=[_backlog_candidate([{"name": "needs-human"}])]
    ), patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    advance.assert_not_called()


def test_promote_ready_promotes_unheld_card():
    """Control: an identical card WITHOUT the hold label is promoted."""
    reconcile._write_failures.clear()
    with patch.object(
        reconcile, "backlog_children", return_value=[_backlog_candidate([{"name": "size:M"}])]
    ), patch.object(reconcile, "epic_blockers_unmet", return_value=False), patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as advance, patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    advance.assert_called_once_with("DRE-1331", "Todo", "Backlog")
