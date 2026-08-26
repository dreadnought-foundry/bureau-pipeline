"""RED-first tests: a lane's strand rule must match what that lane's
occupants owe (DRE-2736).

THE BUG, as it will bite the new front door: the stranded watchdog sweeps
Planning (`WATCHDOG_LANES`), and its NO-ROUTE class has no age gate — a card
whose repo cannot be resolved is flagged on the FIRST sweep that sees it. Under
DRE-2719 every card passes through Planning, and a card in Planning has not
been given a `repo:` label yet — assigning one is what Planning does. So every
card on the new front path would collect `needs-human` within fifteen minutes
of arriving, and `promote_ready()` skips a held card permanently: the front
door would manufacture cards that can never be promoted. DRE-2725 then upgrades
the flag from a comment to a MOVE to Triage, whose exit routes back to
Planning — a loop.

FIX UNDER TEST:
  * Planning leaves `WATCHDOG_LANES` and gets its OWN rule
    (`reconcile.flag_stalled_planning()`), keyed on what a Planning card
    actually owes — a classification, after enough time to produce one
    (`PLANNING_MINUTES`), NOT a repo label and NOT a run receipt.
  * The lane stays visible: a card genuinely stuck in Planning past its own,
    longer threshold is still surfaced — DRE-1978 sat there for seven days.
  * The NO-ROUTE class gets a grace period of its own (asserted in
    tests/test_stranded_watchdog.py).

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402
import validate_card  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


@pytest.fixture(autouse=True)
def _pin_valid_slugs(monkeypatch):
    monkeypatch.setattr(
        validate_card, "VALID_SLUGS", {"agent-bureau", "atlas", "bureau-pipeline"}
    )


@pytest.fixture(autouse=True)
def _pin_live_snapshot(monkeypatch):
    """No live gh fetch from a test run — the canonical-snapshot re-check
    (DRE-2260) answers from this pin. raising=False keeps the RED commit green
    to collect before the attribute exists."""
    monkeypatch.setattr(
        reconcile,
        "live_rail_slugs",
        lambda: frozenset({"agent-bureau", "atlas", "bureau-pipeline"}),
        raising=False,
    )


def _iso(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat().replace(
        "+00:00", "Z"
    )


def _card(
    identifier="DRE-2736",
    state="Planning",
    labels=(),
    minutes_stale=45.0,
):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a card the front door has not classified yet",
        "description": "work",
        "updatedAt": _iso(minutes_stale),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _run_watchdog(cards, bodies=()):
    """Run the WHOLE watchdog (flag_stranded, which owns both passes) over
    `cards`, with the active_cards stub honouring the lane filter it is handed
    the way Linear does. Returns (flagged, cmd_comment mock, add_label mock)."""
    def by_lane(states=reconcile.SWEEP_STATES):
        return [c for c in cards if c["state"]["name"] in states]

    with patch.object(
        reconcile, "active_cards", side_effect=by_lane
    ), patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=list(bodies)
    ), patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as comment, patch.object(
        reconcile.linear_ops, "add_label"
    ) as add_label:
        flagged = reconcile.flag_stranded()
    return flagged, comment, add_label


# --------------------------------------------------------------------------
# 1: a card resting in Planning is not judged by the other lanes' rules
# --------------------------------------------------------------------------
def test_planning_card_without_a_repo_label_is_not_flagged():
    """THE CARD THIS FIX EXISTS FOR: the new front door hands Planning a card
    with no `repo:` label — assigning one is Planning's own job. The NO-ROUTE
    class must never see it, and the age deliberately sits PAST
    WATCHDOG_MINUTES: the no-route grace period alone would not save this
    card, only taking Planning out of that class does."""
    card = _card(labels=(), minutes_stale=reconcile.WATCHDOG_MINUTES + 15)
    flagged, comment, add_label = _run_watchdog([card], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_planning_card_with_an_off_map_repo_label_is_not_flagged():
    """Same rule, one step along: a repo label that routes nowhere is a
    routing question, and a card in Planning does not owe routing yet."""
    card = _card(
        labels=("repo:ghost-product",),
        minutes_stale=reconcile.WATCHDOG_MINUTES + 15,
    )
    flagged, comment, add_label = _run_watchdog([card], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_planner_created_child_in_planning_is_not_flagged_for_a_missing_receipt():
    """The narrower instance in the same function: the no-run class exempts
    `agent:planner` cards only when the state is NOT Planning, and a
    planner-created child inherits `repo:` + a role label but never
    `agent:planner` (parent_inherited_labels). So a routable non-planner card
    resting in Planning for 30 minutes was flagged for a run receipt it does
    not owe."""
    card = _card(
        labels=("repo:agent-bureau", "agent:engineer"),
        minutes_stale=reconcile.WATCHDOG_MINUTES + 15,
    )
    flagged, comment, add_label = _run_watchdog([card], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_planning_is_not_a_watchdog_lane():
    """Structural: the two concerns must not share a lane list, or a later
    edit to either threshold silently re-arms this bug."""
    assert "Planning" not in reconcile.WATCHDOG_LANES
    assert reconcile.PLANNING_LANE == ("Planning",)


# --------------------------------------------------------------------------
# 2: the lane stays VISIBLE — this is not fixed by making Planning invisible
# --------------------------------------------------------------------------
def test_card_stuck_in_planning_past_its_own_threshold_is_surfaced():
    """A card that has sat in Planning with nothing happening to it is still
    a strand — Planning just owes a classification rather than a receipt."""
    card = _card(labels=(), minutes_stale=reconcile.PLANNING_MINUTES + 5)
    flagged, comment, add_label = _run_watchdog([card], bodies=[])
    assert flagged == {"DRE-2736"}
    body = comment.call_args.args[1]
    assert body.startswith(f"🚨 {reconcile.WATCHDOG_TAG}:")
    assert str(reconcile.PLANNING_MINUTES) in body
    assert "Planning" in body, "the notice must name the lane it observed"
    add_label.assert_called_once_with("DRE-2736", reconcile.HOLD_LABEL)


def test_the_dre_1978_shape_is_still_caught():
    """The original incident, replayed: an EPIC parked in Planning for SEVEN
    DAYS with no planner run. It must still alarm — under the new rule the
    reason is the classification that never happened, not the routing."""
    card = _card(
        identifier="DRE-1978",
        labels=("repo:ghost-product", "agent:planner"),
        minutes_stale=7 * 24 * 60,
    )
    flagged, comment, _ = _run_watchdog([card], bodies=[])
    assert flagged == {"DRE-1978"}
    assert "Planning" in comment.call_args.args[1]


def test_planning_threshold_is_its_own_and_longer():
    """The whole point of option 1 on the card: the two concerns stop sharing
    a threshold that suits neither. Planning must give a real planner run room
    to finish (plan.yml's job alone is capped at 30 minutes)."""
    assert reconcile.PLANNING_MINUTES > reconcile.WATCHDOG_MINUTES


def test_planning_card_under_its_own_threshold_is_left_alone():
    card = _card(labels=(), minutes_stale=reconcile.PLANNING_MINUTES - 5)
    flagged, comment, add_label = _run_watchdog([card], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_a_live_planner_run_keeps_the_card_fresh():
    """Proof-of-life is what bumps updatedAt: every receipt the planner posts
    resets the clock, so a run in flight can never trip the rule."""
    card = _card(labels=("repo:agent-bureau", "agent:planner"), minutes_stale=1)
    flagged, comment, _ = _run_watchdog(
        [card],
        bodies=["🧠 model-attempt: claude-opus-4-8 — planner agent starting."],
    )
    assert flagged == set()
    comment.assert_not_called()


# --------------------------------------------------------------------------
# 3: the shared watchdog manners still hold on the new rule
# --------------------------------------------------------------------------
def test_planning_flag_is_once_ever():
    """The WATCHDOG_TAG comment is the idempotency marker for BOTH rules."""
    card = _card(minutes_stale=7 * 24 * 60)
    flagged, comment, add_label = _run_watchdog(
        [card], bodies=[f"🚨 {reconcile.WATCHDOG_TAG}: planning has produced nothing …"]
    )
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_held_planning_card_is_never_spammed():
    card = _card(labels=(reconcile.HOLD_LABEL,), minutes_stale=7 * 24 * 60)
    flagged, comment, add_label = _run_watchdog([card])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_hand_built_planning_card_is_not_flagged():
    """DRE-2524's exemption covers the new rule too: nothing is being planned
    by the pipeline on work a human is building by hand."""
    card = _card(labels=(reconcile.HAND_BUILT_LABEL,), minutes_stale=7 * 24 * 60)
    flagged, comment, add_label = _run_watchdog([card])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


# --------------------------------------------------------------------------
# 4: end to end — the new happy path, with DRE-2725's move-to-Triage ENABLED
#
# The consequence being guarded is permanent: `promote_ready()` skips any card
# carrying HOLD_LABEL (reconcile.py, "held for a human — never auto-promote"),
# so a card the watchdog flags on the front path can never be promoted again.
# DRE-2725 upgrades that flag to a MOVE to Triage, whose exit is "return to
# Intake to be re-classified" — which routes to Planning, which flags it
# again. This walk asserts the loop never starts.
# --------------------------------------------------------------------------
class _Board:
    """A tiny Linear stand-in for one card, with DRE-2725's behaviour wired in:
    any watchdog flag MOVES the card to Triage instead of only commenting."""

    def __init__(self, ident="DRE-2999", labels=()):
        self.card = _card(identifier=ident, state="Intake", labels=labels)
        self.visited = ["Intake"]
        self.comments: list[str] = []

    # --- the seams flag_stranded reads and writes -------------------------
    def active_cards(self, states=reconcile.SWEEP_STATES):
        return [self.card] if self.card["state"]["name"] in states else []

    def comment_bodies(self, ident):
        return list(self.comments)

    def cmd_comment(self, ident, body):
        self.comments.append(body)
        if reconcile.WATCHDOG_TAG in body:
            self.move("Triage")  # DRE-2725: the flag is a move, not a comment

    def add_label(self, ident, label):
        self.card["labels"]["nodes"].append({"name": label})

    # --- the board's own verbs -------------------------------------------
    def move(self, state, minutes_stale=0.0):
        self.card["state"]["name"] = state
        self.card["updatedAt"] = _iso(minutes_stale)
        self.visited.append(state)

    def age(self, minutes_stale):
        self.card["updatedAt"] = _iso(minutes_stale)

    def labels(self):
        return [n["name"].lower() for n in self.card["labels"]["nodes"]]

    def sweep(self):
        with patch.object(
            reconcile, "active_cards", side_effect=self.active_cards
        ), patch.object(
            reconcile.linear_ops, "comment_bodies", side_effect=self.comment_bodies
        ), patch.object(
            reconcile.linear_ops, "cmd_comment", side_effect=self.cmd_comment
        ), patch.object(
            reconcile.linear_ops, "add_label", side_effect=self.add_label
        ):
            return reconcile.flag_stranded()


def test_intake_to_planning_reaches_backlog_without_visiting_triage():
    """The new front path, walked sweep by sweep: a card arrives with no repo
    label, Planning classifies it, and it lands in Backlog for the promoter.
    With DRE-2725 enabled, a single watchdog flag anywhere on this walk would
    show up as a Triage visit."""
    board = _Board()

    # Arrives at the front door and waits a full sweep cycle there.
    board.age(20)
    assert board.sweep() == set()

    # Intake → Planning. Two sweeps pass while the planner works — the second
    # is past WATCHDOG_MINUTES, where the old no-route branch fired.
    board.move("Planning")
    board.age(15)
    assert board.sweep() == set()
    board.age(reconcile.WATCHDOG_MINUTES + 10)
    assert board.sweep() == set()

    # Planning does its job: classification, a repo label, and the exit
    # plan.yml already takes when an epic produces no children of its own.
    board.card["labels"]["nodes"].append({"name": "repo:agent-bureau"})
    board.move("Backlog")
    assert board.sweep() == set()

    assert board.visited == ["Intake", "Planning", "Backlog"]
    assert "Triage" not in board.visited, (
        "the watchdog flagged a card on the happy path — with DRE-2725 that is "
        "a move to Triage, whose exit routes back to Planning: the loop"
    )
    assert reconcile.HOLD_LABEL not in board.labels(), (
        "needs-human on the happy path is permanent non-promotion "
        "(promote_ready skips a held card forever)"
    )
    assert board.comments == []


def test_the_walk_would_catch_a_flag():
    """Guard the guard: the same board, on a card that IS stranded, records
    the Triage visit and the needs-human label the walk above asserts against.
    A harness that can't observe the failure would pass forever."""
    board = _Board(ident="DRE-1978")
    board.move("Todo")
    board.age(999)  # off-map repo, long past the grace period
    board.card["labels"]["nodes"].append({"name": "repo:ghost-product"})
    assert board.sweep() == {"DRE-1978"}
    assert "Triage" in board.visited
    assert reconcile.HOLD_LABEL in board.labels()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
