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
    """Only THIS repo's epics are handed to the closer.

    Epic-ness is the SHAPE, not `agent:planner` (DRE-3044) — the containers
    below carry children, the way a real epic does, and the one-off wears the
    planner label the relay puts on every card out of Planning without that
    making it an epic.
    """
    mocks = _phase_mocks()
    mocks["active_cards"] = MagicMock(
        return_value=[
            {  # this repo, epic — included
                "identifier": "DRE-1496",
                "title": "[EPIC] the front door",
                "description": "**Repo:** agent-bureau\nepic",
                "children": {"nodes": [{"id": "kid-1"}]},
                "labels": {"nodes": [{"name": "agent:planner"}]},
            },
            {  # this repo, NOT an epic — excluded
                "identifier": "DRE-1508",
                "title": "trim the trailing slash",
                "description": "**Repo:** agent-bureau\nwork",
                "children": {"nodes": []},
                "labels": {"nodes": [{"name": "agent:planner"}]},
            },
            {  # other repo epic — excluded
                "identifier": "DRE-200",
                "title": "[EPIC] someone else's decomposition",
                "description": "**Repo:** atlas\nepic",
                "children": {"nodes": [{"id": "kid-2"}]},
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


# ---------------------------------------------------------------------------
# DRE-3148: one Linear timeout on one epic must not kill the sweep. Every other
# step already shrugs off a failed Linear call; this was the one bare call.
# Live symptom (2026-09-06, portico run 33970765609): two sweeps in a row dead
# on `TimeoutError: The read operation timed out` inside close_finished_epics.
# ---------------------------------------------------------------------------
def _gql_timing_out_on(bad_epic: str, *, then=("Done", "Done")):
    """A fake `gql` keyed on the epic id: `bad_epic` times out the way a socket
    read does, every other epic answers with all-Done children."""

    def fake(query, variables=None):
        if variables and variables.get("id") == bad_epic:
            raise TimeoutError("The read operation timed out")
        return _kids(*then)

    return fake


def test_one_epic_timing_out_does_not_stop_the_others():
    """DRE-100's read times out; DRE-200 is still closed and the call returns."""
    with patch.object(reconcile.linear_ops, "gql", side_effect=_gql_timing_out_on("DRE-100")), \
        patch.object(reconcile.linear_ops, "cmd_state") as state, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment, \
        patch.object(reconcile, "advance_unblocked_epics") as advance_chain:
        reconcile.close_finished_epics({"DRE-100", "DRE-200"})  # sorted: DRE-100 first
    state.assert_called_once_with("DRE-200", "Done")
    comment.assert_called_once()
    advance_chain.assert_called_once_with("DRE-200")


def test_timed_out_epic_is_named_in_the_log_with_the_error(capsys):
    """The skip is loud: stderr names the epic, the error, and that it was
    skipped this sweep — the next sweep recomputes the input, so nothing is
    lost, but a reader of the run log must be able to see it happened."""
    with patch.object(reconcile.linear_ops, "gql", side_effect=_gql_timing_out_on("DRE-100")), \
        patch.object(reconcile.linear_ops, "cmd_state"), \
        patch.object(reconcile.linear_ops, "cmd_comment"), \
        patch.object(reconcile, "advance_unblocked_epics"):
        reconcile.close_finished_epics({"DRE-100", "DRE-200"})
    err = capsys.readouterr().err
    assert "epic-close: could not close DRE-100" in err
    assert "The read operation timed out" in err
    assert "skipped this sweep" in err
    assert "DRE-200" not in err  # the epic that closed is not reported as a failure


def test_close_only_sweep_survives_a_linear_timeout():
    """The failing shape from the notice cannot recur: a timeout inside the
    epic-close pass leaves `main(close_only=True)` returning normally (exit 0),
    not dying with the traceback that killed two sweeps in a row."""
    mocks = _phase_mocks()
    del mocks["close_finished_epics"]  # the real one, under a timing-out Linear
    mocks["active_cards"] = MagicMock(
        return_value=[
            {
                "identifier": "DRE-100",
                "title": "[EPIC] the one Linear hangs on",
                "description": "**Repo:** agent-bureau\nepic",
                "children": {"nodes": [{"id": "kid-1"}]},
                "labels": {"nodes": [{"name": "agent:planner"}]},
            },
        ]
    )
    with patch.multiple(reconcile, **mocks), \
        patch.object(reconcile.linear_ops, "gql", side_effect=_gql_timing_out_on("DRE-100")), \
        patch.object(reconcile.linear_ops, "cmd_state") as state:
        reconcile.main(close_only=True)  # must not raise
    state.assert_not_called()
