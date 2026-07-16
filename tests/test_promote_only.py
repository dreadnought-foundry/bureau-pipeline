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
    """The WIP cap must respect cards already in flight for THIS repo."""
    mocks = _phase_mocks()
    mocks["active_cards"] = MagicMock(
        return_value=[
            {  # this repo — counts toward WIP
                "identifier": "DRE-1",
                "description": "**Repo:** agent-bureau\nwork",
                "state": {"name": "In Progress"},
                "labels": {"nodes": []},
                "updatedAt": "2026-06-12T00:00:00Z",
            },
            {  # other repo — excluded
                "identifier": "DRE-2",
                "description": "**Repo:** atlas\nwork",
                "state": {"name": "In Progress"},
                "labels": {"nodes": []},
                "updatedAt": "2026-06-12T00:00:00Z",
            },
            {  # this repo but an epic (agent:planner) — excluded from WIP
                "identifier": "DRE-3",
                "description": "**Repo:** agent-bureau\nepic",
                "state": {"name": "In Progress"},
                "labels": {"nodes": [{"name": "agent:planner"}]},
                "updatedAt": "2026-06-12T00:00:00Z",
            },
        ]
    )
    with patch.multiple(reconcile, **mocks):
        reconcile.main(promote_only=True)
    mocks["promote_ready"].assert_called_once_with(active_count=1)


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
