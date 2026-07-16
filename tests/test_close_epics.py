"""TDD for event-driven epic close (--close-epics) — the cron-drift fix.

PROBLEM: close_finished_epics has existed since 2026-06-11, but it runs ONLY
on the full cron sweep, which GitHub delivers 78-100 minutes apart in practice
(scheduled workflows are best-effort). The moment an epic actually becomes
all-Done is a precise EVENT — a merge flipping its LAST child to Done
(linear-sync.yml) — yet nothing closed it there. Live symptom (2026-06-15):
DRE-1496 sat "In Progress" with 9/9 children Done, reading "still working"
while the work had shipped (DRE-1552).

FIX UNDER TEST: reconcile.py main(close_only=True) — runs ONLY the epic-close
pass (this repo's active agent:planner epics → close_finished_epics),
skipping the PR backstops, promotion gate, and stale-card nudge loop, so
linear-sync.yml can invoke it the instant a merge lands. Epic-close is pure
Linear (LINEAR_API_KEY only). The cron sweep stays as the backstop.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import reconcile  # noqa: E402


def _phase_mocks():
    """Patch every sweep phase with recorders; close path returns cleanly."""
    return {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "active_cards": MagicMock(return_value=[]),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
    }


def test_close_only_runs_epic_close_and_nothing_else():
    """--close-epics must run close_finished_epics and skip every other phase.

    The merge hook carries only LINEAR_API_KEY — epic close is pure Linear.
    Backstops/promotion/nudges need gh or the WIP gate and stay out of this
    path (promotion has its own --promote-only hook on the same merge).
    """
    mocks = _phase_mocks()
    with patch.multiple(reconcile, **mocks):
        reconcile.main(close_only=True)

    mocks["close_finished_epics"].assert_called_once()
    mocks["promote_ready"].assert_not_called()
    mocks["unstick_conflicts"].assert_not_called()
    mocks["retrigger_dead_heads"].assert_not_called()
    mocks["fix_approved_but_red"].assert_not_called()


def test_close_only_passes_just_this_repos_active_epics():
    """Only THIS repo's agent:planner cards are handed to the closer."""
    mocks = _phase_mocks()
    mocks["active_cards"] = MagicMock(
        return_value=[
            {  # this repo, epic — included
                "identifier": "DRE-1496",
                "description": "**Repo:** agent-bureau\nepic",
                "labels": {"nodes": [{"name": "agent:planner"}]},
            },
            {  # this repo, NOT an epic — excluded
                "identifier": "DRE-1508",
                "description": "**Repo:** agent-bureau\nwork",
                "labels": {"nodes": [{"name": "agent:engineer"}]},
            },
            {  # other repo epic — excluded
                "identifier": "DRE-200",
                "description": "**Repo:** atlas\nepic",
                "labels": {"nodes": [{"name": "agent:planner"}]},
            },
        ]
    )
    with patch.multiple(reconcile, **mocks):
        reconcile.main(close_only=True)
    mocks["close_finished_epics"].assert_called_once_with({"DRE-1496"})


def test_full_sweep_still_closes_epics():
    """Default main() keeps epic-close in the full sweep (unchanged backstop)."""
    mocks = _phase_mocks()
    with patch.multiple(reconcile, **mocks):
        reconcile.main()
    mocks["close_finished_epics"].assert_called_once()


def _kids(*states):
    return {"issue": {"children": {"nodes": [{"state": {"name": s}} for s in states]}}}


def test_all_children_done_closes_epic():
    """Fixture: every child Done → epic moves to Done with a logged comment."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_kids("Done", "Done", "Done")), \
        patch.object(reconcile.linear_ops, "cmd_state") as state, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile.close_finished_epics({"DRE-1496"})
    state.assert_called_once_with("DRE-1496", "Done")
    comment.assert_called_once()


def test_one_child_not_done_leaves_epic_open():
    """Fixture: any non-terminal child → epic untouched (no state write)."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_kids("Done", "In Progress", "Done")), \
        patch.object(reconcile.linear_ops, "cmd_state") as state, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile.close_finished_epics({"DRE-1496"})
    state.assert_not_called()
    comment.assert_not_called()


def test_childless_epic_left_open():
    """An epic with zero children is never inferred closed (nothing to read)."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_kids()), \
        patch.object(reconcile.linear_ops, "cmd_state") as state:
        reconcile.close_finished_epics({"DRE-9999"})
    state.assert_not_called()


def test_all_canceled_no_done_leaves_epic_open():
    """All children terminal but NONE Done → not a completion; stays open."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_kids("Canceled", "Canceled")), \
        patch.object(reconcile.linear_ops, "cmd_state") as state:
        reconcile.close_finished_epics({"DRE-9999"})
    state.assert_not_called()
