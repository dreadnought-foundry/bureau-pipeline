"""TDD for event-driven promotion (--promote-only) — the 80-minute-gap fix.

PROBLEM: the stubs declare cron "*/15" but GitHub delivers sweeps 78-100
minutes apart (scheduled workflows are best-effort and heavily throttled).
Eligibility changes at two precise EVENTS — an epic activating (plan.yml)
and a blocker going Done (linear-sync.yml) — yet promotion only happened on
the drifting cron. Live incident 2026-06-12: DRE-1260 activated at 14:11:59,
nine seconds AFTER the 14:11:50 sweep checked; its six eligible children sat
in Backlog facing an ~80-minute wait.

FIX UNDER TEST: reconcile.py main(promote_only=True) — runs ONLY the
promotion gate (WIP count + promote_ready + loud-failure exit), skipping the
PR backstops and stale-card nudge loop, so plan.yml and linear-sync.yml can
invoke it at those exact moments. The cron sweep stays as backstop.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import reconcile  # noqa: E402


def _phase_mocks():
    """Patch every sweep phase with recorders; promotion path returns cleanly."""
    return {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "active_cards": MagicMock(return_value=[]),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=2),
    }


def test_promote_only_runs_promotion_and_skips_backstops():
    """--promote-only must run promote_ready and NOTHING that needs GitHub.

    The event hooks (plan.yml activate, linear-sync Done) carry only
    LINEAR_API_KEY — promotion is pure Linear (the Todo transition rides the
    relay webhook for dispatch). Backstops/nudges need gh and stay cron-only.
    """
    mocks = _phase_mocks()
    with patch.multiple(reconcile, **mocks):
        reconcile.main(promote_only=True)

    mocks["promote_ready"].assert_called_once_with(active_count=0)
    mocks["unstick_conflicts"].assert_not_called()
    mocks["retrigger_dead_heads"].assert_not_called()
    mocks["fix_approved_but_red"].assert_not_called()
    mocks["close_finished_epics"].assert_not_called()


def test_full_sweep_still_runs_everything():
    """Default main() keeps the full sweep: backstops AND promotion."""
    mocks = _phase_mocks()
    with patch.multiple(reconcile, **mocks):
        reconcile.main()

    mocks["promote_ready"].assert_called_once()
    mocks["unstick_conflicts"].assert_called_once()
    mocks["retrigger_dead_heads"].assert_called_once()
    mocks["fix_approved_but_red"].assert_called_once()


def test_promote_only_counts_active_cards_for_wip():
    """The WIP cap must respect cards already in flight for THIS repo.

    Only the CONTAINER is exempt, and epic-ness is the shape (DRE-3044): the
    promoted one-off below wears `agent:planner` — every card out of Planning
    does — and it is real work, so it counts.
    """
    mocks = _phase_mocks()
    mocks["active_cards"] = MagicMock(
        return_value=[
            {  # this repo — counts toward WIP
                "identifier": "DRE-1",
                "title": "work",
                "description": "**Repo:** agent-bureau\nwork",
                "state": {"name": "In Progress"},
                "children": {"nodes": []},
                "labels": {"nodes": []},
                "updatedAt": "2026-06-12T00:00:00Z",
            },
            {  # other repo — excluded
                "identifier": "DRE-2",
                "title": "work",
                "description": "**Repo:** atlas\nwork",
                "state": {"name": "In Progress"},
                "children": {"nodes": []},
                "labels": {"nodes": []},
                "updatedAt": "2026-06-12T00:00:00Z",
            },
            {  # this repo, a real epic (it has children) — excluded from WIP
                "identifier": "DRE-3",
                "title": "the front door",
                "description": "**Repo:** agent-bureau\nepic",
                "state": {"name": "In Progress"},
                "children": {"nodes": [{"id": "kid-1"}]},
                "labels": {"nodes": [{"name": "agent:planner"}]},
                "updatedAt": "2026-06-12T00:00:00Z",
            },
            {  # this repo, a promoted one-off wearing the planner label — counts
                "identifier": "DRE-4",
                "title": "trim the trailing slash",
                "description": "**Repo:** agent-bureau\nwork",
                "state": {"name": "In Progress"},
                "children": {"nodes": []},
                "labels": {"nodes": [{"name": "agent:planner"}]},
                "updatedAt": "2026-06-12T00:00:00Z",
            },
        ]
    )
    with patch.multiple(reconcile, **mocks):
        reconcile.main(promote_only=True)
    mocks["promote_ready"].assert_called_once_with(active_count=2)


def test_promote_only_write_failures_exit_nonzero():
    """A failed promotion write must turn the hook run red (DRE-1254 rule)."""
    mocks = _phase_mocks()

    def _failing_promote(active_count):
        reconcile._write_failures.append("simulated linear write failure")
        return 0

    mocks["promote_ready"] = MagicMock(side_effect=_failing_promote)
    reconcile._write_failures.clear()
    try:
        with patch.multiple(reconcile, **mocks):
            with pytest.raises(SystemExit):
                reconcile.main(promote_only=True)
    finally:
        reconcile._write_failures.clear()


def _backlog_card(identifier, *, epic):
    return {
        "identifier": identifier,
        "title": f"[EPIC] {identifier}" if epic else "work",
        "description": "**Repo:** agent-bureau\nwork",
        "children": {"nodes": [{"id": "kid-1"}] if epic else []},
        "labels": {"nodes": []},
        "parent": None,
    }


def test_full_sweep_asks_promotion_to_close_backlog_epics(monkeypatch):
    """DRE-6410: the full sweep's promotion closes this repo's finished
    Backlog epics; the event-driven gate's does not."""
    mocks = _phase_mocks()
    with patch.multiple(reconcile, **mocks):
        reconcile.main()
    assert mocks["promote_ready"].call_args.kwargs.get("close_epics") is True


def test_promote_only_closes_no_backlog_epic():
    """The event-driven gate is promotion alone: it closes nothing, Backlog
    epics included — the full sweep and `--close-only` are the closers."""
    mocks = _phase_mocks()
    with patch.multiple(reconcile, **mocks):
        reconcile.main(promote_only=True)
    assert not mocks["promote_ready"].call_args.kwargs.get("close_epics")
    mocks["close_finished_epics"].assert_not_called()


def test_promotion_closes_backlog_epics_off_its_one_backlog_read(monkeypatch, capsys):
    """Backlog is read ONCE: the finished epics are closed off that list and
    the same list, less what closed, is what the gate walks."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    epic, one_off = _backlog_card("DRE-5", epic=True), _backlog_card("DRE-6", epic=False)
    theirs = dict(_backlog_card("DRE-7", epic=True), description="**Repo:** atlas\nwork")
    read = MagicMock(return_value=[epic, one_off, theirs])
    close = MagicMock(side_effect=lambda epics: set(epics) & {"DRE-5"})
    with patch.object(reconcile, "backlog_children", read), \
            patch.object(reconcile, "close_finished_epics", close), \
            patch.object(reconcile.linear_ops, "gql", return_value={}), \
            patch.object(reconcile.linear_ops, "cmd_advance"), \
            patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.promote_ready(0, close_epics=True)
    read.assert_called_once_with()
    close.assert_called_once_with({"DRE-5"})
    out = capsys.readouterr().out
    assert "DRE-5 is an epic" not in out, "a closed epic is no longer a candidate"


def test_promotion_without_the_flag_closes_nothing(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    read = MagicMock(return_value=[_backlog_card("DRE-5", epic=True)])
    close = MagicMock(return_value=set())
    with patch.object(reconcile, "backlog_children", read), \
            patch.object(reconcile, "close_finished_epics", close), \
            patch.object(reconcile.linear_ops, "gql", return_value={}):
        reconcile.promote_ready(0)
    close.assert_not_called()
