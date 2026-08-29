"""RED-first tests: an aged Intake card MOVES, and Intake is in a lane set a
live sweep actually reads (DRE-2687).

THE HOLE THIS CLOSES. `SWEEP_STATES` is derived from the work-segment lanes and
`WATCHDOG_LANES` is Todo / In Progress. Intake is in neither, so a card that
enters Intake and is never drained is examined by NO mechanism, ever — the same
shape as the Backlog this wave exists to empty, one lane earlier.

THE MECHANISM IS A MOVE, NOT A REPORT. The plan's first version answered
"Intake must not become the new Backlog" with an age report and replaced it: a
report is a record, and this wave exists because recording is not enforcing.
The counter-evidence is DRE-2670 — about 480 consecutive green reconcile runs
printed, in plain English, the exact reason five cards were frozen, and nobody
read one. So past `INTAKE_MAX_AGE_MINUTES` the card MOVES to Green Light, the
CEO's "needs you" queue, carrying whatever reason is already stated on it.

WHAT THE TESTS PIN:
  * Intake is read by the full sweep (behaviourally — the states the sweep
    actually queries — not by grepping for a constant).
  * The threshold is ONE named constant, and its value is the lane contract's
    own stall window for Intake, so the number in docs/lane-contract.md is the
    number that runs.
  * An aged card is advanced Intake → Green Light. A comment alone fails these
    tests: `cmd_advance` must be called.
  * The escalation carries the stated reason when there is one, and says there
    is none when there is not (console-honesty rule 2 — absent is not invented).
  * The per-sweep cap HOLDS cards; it never forgets one — the next sweep takes
    the next oldest.

Run: cd bureau-pipeline && python3 -m pytest tests/test_intake_escalation.py -v
"""
from __future__ import annotations

import contextlib
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import critic_score  # noqa: E402
import lane_contract  # noqa: E402
import reconcile  # noqa: E402


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _card(identifier="DRE-2687", state="Intake", labels=(), minutes_stale=None):
    if minutes_stale is None:
        minutes_stale = reconcile.INTAKE_MAX_AGE_MINUTES + 60
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a card nothing has classified",
        "description": "work",
        "updatedAt": _iso(minutes_stale),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _run(cards, bodies=(), advance=None):
    """Run escalate_aged_intake() over `cards`, with the active_cards stub
    honouring the lane filter the way Linear does. Returns
    (escalated, cmd_comment mock, cmd_advance mock)."""
    def by_lane(states=reconcile.SWEEP_STATES):
        return [c for c in cards if c["state"]["name"] in states]

    reconcile._write_failures.clear()
    with patch.object(
        reconcile, "active_cards", side_effect=by_lane
    ), patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=list(bodies)
    ), patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as comment, patch.object(
        reconcile.linear_ops, "cmd_advance", side_effect=advance
    ) as advanced:
        escalated = reconcile.escalate_aged_intake()
    return escalated, comment, advanced


# --------------------------------------------------------------------------
# 1: Intake is in a lane set a live sweep actually reads
# --------------------------------------------------------------------------
def _main_mocks():
    """Every seam a full sweep touches except the lane reads themselves."""
    return [
        mock.patch.object(reconcile, name)
        for name in (
            "drain_retiring_lanes", "unstick_conflicts", "retrigger_dead_heads",
            "flag_no_checks_prs", "flag_unowned_prs", "flag_unlanded_work",
            "fix_approved_but_red", "retry_dead_fix_runs",
            "restart_answered_blockers", "review_dependabot_prs",
            "recover_crashed_reviews", "check_dependabot_capacity",
            "promote_ready", "close_finished_epics", "report_break_glass",
            "report_fix_concurrency", "report_evicted_fix_runs",
            "report_epic_growth",
        )
    ] + [
        mock.patch.object(reconcile, "flag_stranded", return_value=set()),
        mock.patch.object(reconcile, "backlog_children", return_value=[]),
    ]


def _lanes_a_full_sweep_reads() -> set[str]:
    """The states the sweep ACTUALLY queries, collected from active_cards."""
    seen: set[str] = set()

    def recorder(states=reconcile.SWEEP_STATES):
        seen.update(states)
        return []

    reconcile._write_failures.clear()
    with contextlib.ExitStack() as stack:
        for m in _main_mocks():
            stack.enter_context(m)
        stack.enter_context(
            mock.patch.object(reconcile, "active_cards", side_effect=recorder)
        )
        reconcile.main()
    return seen


def test_intake_is_a_lane_a_live_sweep_reads():
    """THE CRITERION: Intake must be in a set a live sweep reads, or this phase
    builds the same hole with a new name. Asserted against what the sweep
    queries, not against a constant somebody might define and never wire."""
    assert "Intake" in _lanes_a_full_sweep_reads()


def test_the_swept_lane_set_names_intake():
    """Structural companion: the union of the sweep's lane sets is declared
    once, so a later edit that drops Intake fails here too."""
    assert "Intake" in reconcile.SWEPT_LANES
    assert reconcile.INTAKE_LANE == ("Intake",)


def test_the_recorder_would_notice_a_lane_the_sweep_never_reads():
    """Guard the guard: a lane nothing queries must NOT appear, or the test
    above would pass against any sweep at all."""
    assert "Duplicate" not in _lanes_a_full_sweep_reads()


def test_full_sweep_runs_the_escalation():
    reconcile._write_failures.clear()
    with contextlib.ExitStack() as stack:
        esc = stack.enter_context(
            mock.patch.object(reconcile, "escalate_aged_intake", return_value=set())
        )
        for m in _main_mocks():
            stack.enter_context(m)
        stack.enter_context(
            mock.patch.object(reconcile, "active_cards", return_value=[])
        )
        reconcile.main()
    esc.assert_called_once_with()


def test_promote_only_mode_does_not_escalate():
    """The event hooks run the dependency gate alone — an Intake sweep on every
    merge would move cards on a code path nobody asked for one."""
    reconcile._write_failures.clear()
    with contextlib.ExitStack() as stack:
        esc = stack.enter_context(
            mock.patch.object(reconcile, "escalate_aged_intake", return_value=set())
        )
        for m in _main_mocks():
            stack.enter_context(m)
        stack.enter_context(
            mock.patch.object(reconcile, "active_cards", return_value=[])
        )
        reconcile.main(promote_only=True)
    esc.assert_not_called()


# --------------------------------------------------------------------------
# 2: the age threshold is ONE named constant
# --------------------------------------------------------------------------
def test_the_threshold_is_one_named_constant_read_from_the_contract():
    """One name, and the number a reader finds in docs/lane-contract.md is the
    number that runs — the same rule PLANNING_MINUTES follows."""
    assert reconcile.INTAKE_MAX_AGE_MINUTES == lane_contract.stale_minutes()["Intake"]
    assert reconcile.STALE_MINUTES["Intake"] == reconcile.INTAKE_MAX_AGE_MINUTES


def test_intake_did_not_join_the_work_lane_nudge_loop():
    """Intake is a planning-segment lane: giving it a stall window must not
    quietly enrol it in the nudge loop, which dispatches and requeues."""
    assert "Intake" not in reconcile.SWEEP_STATES


# --------------------------------------------------------------------------
# 3: the escalation is a MOVE
# --------------------------------------------------------------------------
def test_an_aged_intake_card_is_moved_to_green_light():
    card = _card()
    escalated, comment, advanced = _run([card])
    assert escalated == {"DRE-2687"}
    advanced.assert_called_once_with("DRE-2687", "Green Light", "Intake")
    assert comment.call_args.args[0] == "DRE-2687"
    body = comment.call_args.args[1]
    assert body.lstrip().startswith(f"🚨 {reconcile.INTAKE_AGED_TAG}:")
    assert "Green Light" in body


def test_the_reason_is_posted_before_the_move():
    """Copied from critic_score.escalate: a move that fails still leaves the
    reason on the card."""
    order: list[str] = []
    card = _card()

    def by_lane(states=reconcile.SWEEP_STATES):
        return [c for c in cards if c["state"]["name"] in states]

    cards = [card]
    reconcile._write_failures.clear()
    with patch.object(reconcile, "active_cards", side_effect=by_lane), \
         patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]), \
         patch.object(reconcile.linear_ops, "cmd_comment",
                      side_effect=lambda *a: order.append("comment")), \
         patch.object(reconcile.linear_ops, "cmd_advance",
                      side_effect=lambda *a: order.append("advance")):
        reconcile.escalate_aged_intake()
    assert order == ["comment", "advance"]


def test_a_young_intake_card_is_left_alone():
    card = _card(minutes_stale=reconcile.INTAKE_MAX_AGE_MINUTES - 60)
    escalated, comment, advanced = _run([card])
    assert escalated == set()
    comment.assert_not_called()
    advanced.assert_not_called()


def test_a_card_in_another_lane_is_not_escalated():
    """This rule speaks for one lane only — Backlog is the promoter's."""
    escalated, comment, advanced = _run([_card(state="Backlog")])
    assert escalated == set()
    advanced.assert_not_called()


def test_hand_built_intake_card_is_not_moved():
    """DRE-2524's exemption: no classification is coming from the pipeline for
    work a human is doing by hand, so time in Intake is not a strand."""
    escalated, comment, advanced = _run(
        [_card(labels=(reconcile.HAND_BUILT_LABEL,))]
    )
    assert escalated == set()
    advanced.assert_not_called()


def test_a_failed_move_is_a_write_failure_not_a_silent_report():
    """The gate may hold a card; it may not forget one. A move that does not
    happen fails the run red so medic sees it — it never reads as done."""
    def boom(*_a):
        raise reconcile.linear_ops.LinearError("Linear said no")

    escalated, _comment, _advanced = _run([_card()], advance=boom)
    assert escalated == set()
    assert any("DRE-2687" in f for f in reconcile._write_failures)


# --------------------------------------------------------------------------
# 4: it carries the stated reason — and never invents one
# --------------------------------------------------------------------------
def test_the_move_carries_the_critics_stated_reason():
    stated = (
        f"🚨 {critic_score.ESCALATE_TAG}: DRE-2687 — the critic could not "
        "classify it, so it is moving to **Planning** rather than waiting "
        "where nobody would look.\n\n**Why:** it has no acceptance criteria."
    )
    _escalated, comment, _advanced = _run([_card()], bodies=[stated])
    body = comment.call_args.args[1]
    assert "it has no acceptance criteria" in body, (
        "the card arrives in the CEO's queue carrying the reason already "
        "stated on it — an escalation with no reason is a card nobody can act on"
    )


def test_the_most_recent_stated_reason_wins():
    older = f"🚨 {critic_score.ESCALATE_TAG}: DRE-2687 — **Why:** the first reason."
    newer = f"🚨 {critic_score.ESCALATE_TAG}: DRE-2687 — **Why:** the later reason."
    _escalated, comment, _advanced = _run([_card()], bodies=[older, newer])
    body = comment.call_args.args[1]
    assert "the later reason" in body
    assert "the first reason" not in body


def test_a_routing_verdict_counts_as_a_stated_reason():
    verdict = reconcile.routing_verdict.verdict_comment(
        "NEEDS WORK", "the acceptance criteria describe two different cards"
    )
    _escalated, comment, _advanced = _run([_card()], bodies=[verdict])
    body = comment.call_args.args[1]
    assert "two different cards" in body


def test_no_stated_reason_is_reported_as_absent_never_invented():
    """Console-honesty rule 2: 'the query returned nothing' and 'the thing is
    in state X' get visibly different renderings."""
    _escalated, comment, _advanced = _run([_card()], bodies=[])
    body = comment.call_args.args[1]
    assert reconcile.NO_STATED_REASON in body


# --------------------------------------------------------------------------
# 5: the cap holds cards — it never forgets one
# --------------------------------------------------------------------------
def _aged_batch(n: int) -> list[dict]:
    """n aged cards, DRE-1 the oldest through DRE-n the newest."""
    return [
        _card(
            identifier=f"DRE-{i}",
            minutes_stale=reconcile.INTAKE_MAX_AGE_MINUTES + (n - i) * 60,
        )
        for i in range(1, n + 1)
    ]


def test_the_cap_bounds_one_sweep_and_takes_the_oldest_first():
    cards = _aged_batch(reconcile.INTAKE_ESCALATION_CAP + 2)
    escalated, _comment, advanced = _run(cards)
    assert len(escalated) == reconcile.INTAKE_ESCALATION_CAP
    moved = [c.args[0] for c in advanced.call_args_list]
    assert moved == [f"DRE-{i}" for i in range(1, reconcile.INTAKE_ESCALATION_CAP + 1)]


def test_a_capped_card_is_taken_on_the_next_sweep():
    """A cap that dropped the remainder would be the forgetting this whole
    mechanism exists to stop. The held cards stay in Intake and are still the
    oldest, so the next sweep takes them."""
    cards = _aged_batch(reconcile.INTAKE_ESCALATION_CAP + 2)
    first, _c, _a = _run(cards)
    # the moved ones leave the lane, exactly as Linear would show them
    for card in cards:
        if card["identifier"] in first:
            card["state"]["name"] = "Green Light"
    second, _c2, _a2 = _run(cards)
    assert second, "the second sweep escalated nothing — the cap forgot them"
    assert not (first & second)
    assert first | second == {c["identifier"] for c in cards}


def test_the_cap_is_a_named_constant():
    assert isinstance(reconcile.INTAKE_ESCALATION_CAP, int)
    assert reconcile.INTAKE_ESCALATION_CAP >= 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
