"""RED-first tests: the Planning stall watchdog leaves a limit-dead card to the
limit recovery until its reset has passed (DRE-5841).

THE BUG: a classify- or plan-stage limit death leaves its card in Planning,
and the recovery (`limit_recovery.recover`) brings it back from there once
the marker's reset passes — five hours after the run when the run named none
(DRE-5455). The watchdog measures a Planning card by `updatedAt` against
`PLANNING_MINUTES`, and unlike the nudge loop it had no limit skip: two hours
into the wait it parked the card in Triage under `🧹 planning-stall-park`.
`🧹` is a receipt glyph, so that note superseded the marker and the card never
came back on its own.

FIX UNDER TEST: after the age gate and the under-review skip, a card whose
newest receipt is a limit marker is skipped until the wait's end
(`reconcile.limit_wait_ends`: the reset plus PLANNING_MINUTES, or the
marker's own `createdAt` plus PLANNING_MINUTES when it names no reset). Past
it, the card goes through the stall exit as it stands, with a reason of its
own (`reconcile.limit_wait_overran_reason`).

Run: cd bureau-pipeline && python3 -m pytest tests/test_planning_stall_leaves_limit_deaths.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import dead_run  # noqa: E402
import dedupe_dispatch  # noqa: E402
import limit_recovery  # noqa: E402
import reconcile  # noqa: E402
import validate_card  # noqa: E402

IDENT = "DRE-5841"
RUN = "33912345678"
WINDOW = reconcile.PLANNING_MINUTES


@pytest.fixture(autouse=True)
def _pin_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/agent-bureau")
    monkeypatch.setattr(
        validate_card, "VALID_SLUGS", {"agent-bureau", "atlas", "bureau-pipeline"}
    )
    reconcile._write_failures.clear()
    reconcile.reset_sweep_cards()


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(minutes: float) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


def claude_marker(reset: datetime) -> str:
    """An assumed-clock Claude plan death, as dead_run's own writer puts it
    on the card — never hand-typed, so a change to the shape reaches both
    halves or neither."""
    return dead_run.LimitDeath(kind="claude", stage="plan", reset=reset,
                               run_id=RUN, reset_assumed=True).marker()


def linear_marker() -> str:
    """A Linear classify death that named no reset: `reset=unknown`."""
    marker = dead_run.LimitDeath(kind="linear", stage="classify", reset=None,
                                 run_id=RUN).marker()
    assert "reset=unknown" in marker.split("\n", 1)[0]
    return marker


def card(*comments, ident=IDENT, minutes_stale=WINDOW + 1, labels=(), children=0):
    """A Planning card as the board read returns it. `comments` are
    `(body, createdAt-or-None)` pairs, oldest→newest as the card reads; the
    window is stored NEWEST FIRST, the order Linear answers in (DRE-3250)."""
    nodes = []
    for body, at in comments:
        node = {"body": body}
        if at is not None:
            node["createdAt"] = _iso(at)
        nodes.append(node)
    return {
        "id": f"id-{ident}",
        "identifier": ident,
        "title": f"{ident} title",
        "description": "work",
        "updatedAt": _iso(_ago(minutes_stale)),
        "state": {"name": "Planning"},
        "labels": {"nodes": [{"name": name} for name in labels]},
        "children": {"nodes": [{"id": f"child-{i}"} for i in range(children)]},
        "comments": {"nodes": list(reversed(nodes))},
    }


def _no_per_card_fetch(identifier, *a, **kw):
    raise AssertionError(
        f"the watchdog fetched {identifier}'s comments per card — the bodies "
        "come inline with active_cards() since DRE-2929"
    )


def _run_watchdog(cards):
    """The whole watchdog, the way tests/test_planning_lane_strand.py drives
    it: a stubbed `active_cards` honoring the lane filter and carrying each
    card's comment window inline, `cmd_comment` and `cmd_state` recorded, and
    no per-card fetch."""
    def by_lane(states=reconcile.SWEEP_STATES):
        return [c for c in cards if c["state"]["name"] in states]

    with patch.object(reconcile, "active_cards", side_effect=by_lane), \
         patch.object(reconcile.linear_ops, "comment_bodies",
                      side_effect=_no_per_card_fetch), \
         patch.object(reconcile.linear_ops, "cmd_comment") as comment, \
         patch.object(reconcile.linear_ops, "cmd_state") as state, \
         patch.object(reconcile.linear_ops, "add_label"):
        flagged = reconcile.flag_stranded()
    return flagged, comment, state


def _assert_parked(flagged, comment, state, ident=IDENT) -> str:
    assert ident in flagged
    state.assert_called_once_with(ident, reconcile.PARKED_STATE)
    comment.assert_called_once()
    note = comment.call_args.args[1]
    assert note.startswith(f"🧹 {dedupe_dispatch.STALL_PARK_TAG}: {ident}"), note
    return note


def _assert_skipped(flagged, comment, state, ident=IDENT):
    assert ident not in flagged
    comment.assert_not_called()
    state.assert_not_called()


# --------------------------------------------------------------------------
# a Claude death with a reset: skipped until reset + PLANNING_MINUTES
# --------------------------------------------------------------------------
def test_a_card_waiting_on_an_assumed_reset_is_left_to_the_recovery(capsys):
    """THE CARD THIS FIX EXISTS FOR. Stale past the window, its newest
    comment the assumed-clock marker with the reset still two hours off.
    RED on `main`: parked in Triage, and the park note closed the marker."""
    reset = datetime.now(UTC) + timedelta(minutes=119)
    flagged, comment, state = _run_watchdog(
        [card((claude_marker(reset), _ago(WINDOW + 1)))])
    _assert_skipped(flagged, comment, state)
    out = capsys.readouterr().out
    assert (f"watchdog: {IDENT} is waiting on a claude limit death (plan stage) "
            f"until {dead_run.pacific(reset)}") in out, out
    assert "the limit recovery owns it, not a strand" in out, out


def test_a_reset_passed_inside_the_window_is_still_skipped(capsys):
    """The recovery re-enters a reset card on its next pass with room — the
    window after the reset is its time to do so, not the watchdog's."""
    reset = _ago(WINDOW - 1)
    flagged, comment, state = _run_watchdog(
        [card((claude_marker(reset), _ago(WINDOW + 400)))])
    _assert_skipped(flagged, comment, state)
    assert "limit recovery owns it" in capsys.readouterr().out


def test_a_reset_a_window_past_is_parked_with_a_reason_of_its_own():
    """The recovery has had a whole window since the reset and has not
    brought the card back: no WIP room, or every re-entry failed. That is a
    strand, and the note says when the wait should have ended."""
    reset = _ago(WINDOW)
    flagged, comment, state = _run_watchdog(
        [card((claude_marker(reset), _ago(WINDOW + 400)))])
    note = _assert_parked(flagged, comment, state)
    assert dead_run.pacific(reset) in note, note
    assert "has not brought it back" in note, note
    assert reconcile.stalled_planning_reason() not in note


# --------------------------------------------------------------------------
# a marker with no reset: skipped until its own createdAt + PLANNING_MINUTES
# --------------------------------------------------------------------------
def test_a_linear_marker_younger_than_the_window_is_skipped(capsys):
    flagged, comment, state = _run_watchdog(
        [card((linear_marker(), _ago(WINDOW - 1)))])
    _assert_skipped(flagged, comment, state)
    out = capsys.readouterr().out
    assert f"watchdog: {IDENT} is waiting on a linear limit death (classify stage)" in out, out


def test_a_linear_marker_a_window_old_is_parked_naming_its_time_plus_the_window():
    posted = _ago(WINDOW)
    flagged, comment, state = _run_watchdog([card((linear_marker(), posted))])
    note = _assert_parked(flagged, comment, state)
    assert dead_run.pacific(posted) in note, note
    assert dead_run.pacific(posted + timedelta(minutes=WINDOW)) in note, note
    assert "has not brought it back" in note, note


def test_a_marker_with_no_reset_and_no_created_at_is_skipped_as_of_unknown_age(capsys):
    flagged, comment, state = _run_watchdog([card((linear_marker(), None))])
    _assert_skipped(flagged, comment, state)
    out = capsys.readouterr().out
    assert f"watchdog: {IDENT} is waiting on a linear limit death (classify stage)" in out
    assert "age is unknown" in out, out


# --------------------------------------------------------------------------
# the wait's end and the reason, as functions
# --------------------------------------------------------------------------
def test_limit_wait_ends_reads_the_reset_first_then_the_marker_time():
    reset = datetime(2026, 10, 6, 20, 0, tzinfo=UTC)
    posted = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
    stated = dead_run.parse_limit_marker(claude_marker(reset))
    unknown = dead_run.parse_limit_marker(linear_marker())
    window = timedelta(minutes=WINDOW)
    assert reconcile.limit_wait_ends(stated, posted) == reset + window
    assert reconcile.limit_wait_ends(stated, None) == reset + window
    assert reconcile.limit_wait_ends(unknown, posted) == posted + window
    assert reconcile.limit_wait_ends(unknown, None) is None


def test_the_overran_reason_names_the_assumed_reset_and_the_sweep():
    reset = datetime(2026, 10, 6, 20, 0, tzinfo=UTC)
    reason = reconcile.limit_wait_overran_reason(
        dead_run.parse_limit_marker(claude_marker(reset)), None)
    assert dead_run.pacific(reset) in reason
    assert dead_run.pacific(reset + timedelta(minutes=WINDOW)) in reason
    assert "assumed" in reason
    assert "has not brought it back" in reason


# --------------------------------------------------------------------------
# the rules around it are unchanged
# --------------------------------------------------------------------------
def test_a_marker_the_recovery_answered_is_measured_by_the_stall_clock():
    """After the recovery's own `🔁` receipt the marker no longer stands
    (`limit_recovery.waiting`), so the card is an ordinary stall again."""
    receipt = (f"{limit_recovery.RECOVERY_MARK} re-entered plan — window reset at "
               "2026-10-06 06:00 PT.")
    reset = datetime.now(UTC) + timedelta(minutes=119)
    flagged, comment, state = _run_watchdog([card(
        (claude_marker(reset), _ago(WINDOW + 30)), (receipt, _ago(WINDOW + 5)))])
    note = _assert_parked(flagged, comment, state)
    assert reconcile.stalled_planning_reason() in note


def test_an_epic_under_review_is_the_review_skips_never_this_ones(capsys):
    """The under-review skip (DRE-5286) runs first and claims the epic."""
    reset = datetime.now(UTC) + timedelta(minutes=119)
    epic = card((claude_marker(reset), _ago(WINDOW + 1)),
                labels=("repo:agent-bureau",), children=2)
    with patch.object(reconcile.linear_ops, "comment_records", return_value=[]), \
         patch.object(reconcile.rereview_watch, "under_review", return_value=True):
        flagged, comment, state = _run_watchdog([epic])
    _assert_skipped(flagged, comment, state)
    out = capsys.readouterr().out
    assert f"watchdog: {IDENT} is with the critics" in out, out
    assert "limit death" not in out, out


# --------------------------------------------------------------------------
# a full sweep: the recovery re-enters the card before the watchdog looks
# --------------------------------------------------------------------------
def _full_sweep_mocks(cards):
    """tests/test_limit_recovery.py's full-sweep stubs, with the watchdog
    left real: it is half of what this sweep is about."""
    return {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "pr_for": MagicMock(return_value=None),
        "agent_run_alive": MagicMock(return_value=False),
        "active_cards": MagicMock(return_value=cards),
    }


def test_a_full_sweep_reenters_the_reset_card_and_posts_no_stall_park():
    """A Planning card a window old whose assumed reset passed a minute ago.
    The recovery bounces it Intake → Planning under its `window reset at`
    receipt, and the watchdog — reading the same board later in the same
    pass — posts no stall-park note. MUTATION CHECK: run `flag_stranded`
    before `recover_limit_deaths` in `main()` and the watchdog looks first."""
    reset = _ago(1)
    dead = card((claude_marker(reset), _ago(WINDOW + 1)), labels=("repo:agent-bureau",))
    events: list[str] = []
    real_watchdog = reconcile.flag_stalled_planning

    def watchdog():
        events.append("watchdog")
        return real_watchdog()

    def comment(ident, body):
        events.append(body)

    with patch.multiple(reconcile, **_full_sweep_mocks([dead])), \
         patch.object(reconcile, "flag_stalled_planning", side_effect=watchdog), \
         patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
         patch.object(reconcile.linear_ops, "cmd_state") as cmd_state, \
         patch.object(reconcile.linear_ops, "cmd_comment", side_effect=comment), \
         patch.object(reconcile.linear_ops, "add_label"):
        reconcile.main()
    moves = [tuple(c.args) for c in cmd_state.call_args_list]
    assert moves == [(IDENT, "Intake"), (IDENT, "Planning")], moves
    receipts = [e for e in events if e.startswith(limit_recovery.RECOVERY_MARK)]
    assert len(receipts) == 1 and "window reset at" in receipts[0], events
    assert not any(dedupe_dispatch.STALL_PARK_TAG in e for e in events), events
    assert "watchdog" in events, "the sweep never ran the Planning watchdog"
    assert events.index(receipts[0]) < events.index("watchdog"), (
        "the watchdog looked before the recovery re-entered the card")
    assert reconcile._write_failures == []
