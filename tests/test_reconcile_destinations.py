"""`reconcile.destinations()` — the lanes the sweep's computed writes can reach
(DRE-5286, epic DRE-5268).

`ready_lane_writers.writes()` reads a lane write at its call site, and six of
`reconcile.py`'s sites hand the write layer a lane it cannot read there: a
retiring lane's `replaced_by`, `REVIEW_LANE` three times, `_fleet_outage_state`'s
`lane` parameter, and the `move` lambda handed to `limit_recovery.recover`.
The ready-work writer check lets them through only because `reconcile.py` is
permitted in every ready-work lane; the Green Light writer check (DRE-5282)
has no such escape and would report every one. The module publishes
`ready_lane_writers.DESTINATIONS_HOOK` instead — the one general answer for a
writer whose destination is computed.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reconcile_destinations.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import limit_recovery  # noqa: E402
import ready_lane_writers  # noqa: E402
import reconcile  # noqa: E402

#: The six sites `writes()` could not read at 9edf221, by the expression each
#: hands the write layer — line numbers move, the expressions name the sites.
UNREAD_AT_9EDF221 = (
    "drain_retiring_lanes: to (a retiring lane's replaced_by)",
    "REVIEW_LANE (three sites)",
    "_fleet_outage_state: lane",
    "recover_limit_deaths: lane (the move handed to limit_recovery.recover)",
)


def _destinations():
    return reconcile.destinations()


def test_it_is_the_hook_the_writer_check_reads():
    assert ready_lane_writers.DESTINATIONS_HOOK == "destinations"
    assert callable(getattr(reconcile, ready_lane_writers.DESTINATIONS_HOOK))


def test_it_returns_a_tuple():
    assert isinstance(_destinations(), tuple)


def test_it_holds_every_lane_a_computed_write_reaches():
    lanes = set(_destinations())
    for lane in (
        reconcile.REVIEW_LANE,
        limit_recovery.PLANNING_LANE,
        limit_recovery.BOUNCE_LANE,
        limit_recovery.BUILD_LANE,
        "Canceled",
        "Done",
    ):
        assert lane in lanes, lane


def test_it_holds_every_retiring_lanes_replacement():
    """Nothing is retiring today, so a retiring lane is injected — the drain's
    destination is read off the contract, and so is this."""
    real = lane_contract.lanes

    def with_one_retiring(status="live", contract=None):
        if status == "retiring":
            return ({"name": "Old Lane", "status": "retiring",
                     "replaced_by": "Backlog"},)
        return real(status, contract)

    with patch.object(lane_contract, "lanes", side_effect=with_one_retiring):
        assert "Backlog" in reconcile.destinations()


def test_green_light_is_not_in_it():
    """Nothing in this module writes the CEO's decision queue any more."""
    assert "Green Light" not in _destinations()
    assert reconcile.ESCALATED_STATE not in _destinations()


def test_it_makes_no_linear_call():
    def no_call(*a, **kw):
        raise AssertionError("destinations() called Linear")

    with patch.object(reconcile.linear_ops, "gql", side_effect=no_call), \
            patch.object(reconcile.linear_ops, "gql_paged", side_effect=no_call), \
            patch.object(reconcile.linear_ops, "get_issue", side_effect=no_call), \
            patch.object(reconcile, "active_cards", side_effect=no_call):
        assert _destinations()


def test_every_lane_in_it_is_live():
    """`_published_destinations` keeps only live lanes, so a name that is not
    one would silently read as no destination at all."""
    live = set(lane_contract.lane_names(status="live"))
    assert set(_destinations()) <= live


def test_no_write_in_reconcile_is_unread():
    unread = [
        w for w in ready_lane_writers.writes()
        if w.lane is None and w.where.startswith("scripts/reconcile.py:")
    ]
    assert unread == [], (
        "reconcile.py hands the write layer a destination nothing can read at "
        f"{[(w.where, w.expression) for w in unread]}. At 9edf221 these were "
        f"the six sites {UNREAD_AT_9EDF221}; a new computed write must name its "
        "lane at the call site or add it to reconcile.destinations()"
    )
