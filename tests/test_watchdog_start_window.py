"""RED-first tests: the stranded watchdog stops stamping needs-human on builds a
Linear outage kept from starting (DRE-5743).

THE INCIDENT (2026-10-02, about 14:30-15:00 PT). Linear's hourly quota was
refusing writes, so four Todo build cards — DRE-5595, DRE-5372, DRE-5410 and
DRE-5411 — could not record that their builds had started. The watchdog read
"no run receipt" as "nothing has started" and stamped all four `needs-human`.
The worst case was DRE-5595: the sweep re-sent its build at 14:47 PT and the
watchdog stamped it at 14:48 PT, one minute later, because a re-send receipt
used to COUNT AS the whole waiting time. `needs-human` also stops the sweep
re-sending, so all four would have sat in Todo for good; the operator removed
the marks by hand on 10-03.

FIX UNDER TEST — reconcile.flag_stranded(), the no-run class:
  1. A re-send buys a full start window (START_WINDOW_MINUTES, the Todo lane's
     own stall window — the one the sweep re-sends on). The dispatch time is
     the re-send receipt's, or the newest run GitHub lists for the card when
     the receipt never reached Linear.
  2. When the "nothing has started" reading cannot be trusted — Linear refused
     this sweep, a Linear limit death or a failed re-send sits in the window,
     the GitHub run lookup failed, or GitHub shows a run for the card that left
     no receipt — the watchdog records UNKNOWN and holds nothing.
  3. The stamp names its evidence: the last dispatch time and the run lookup's
     result.
"""
from __future__ import annotations

import json
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

import dead_run  # noqa: E402
import dedupe_dispatch  # noqa: E402
import reconcile  # noqa: E402
import validate_card  # noqa: E402

CARD = "DRE-5595"
RESEND = "🧹 Reconcile: card sat in Todo with no run — re-dispatched."
FAILED_RESEND = (
    "🚨 Reconcile: re-dispatch FAILED — the dispatch call did not go through, "
    "so no run was started. The sweep run is red; medic will pick it up, and "
    "the next sweep retries."
)


@pytest.fixture(autouse=True)
def _hermetic_sweep(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/agent-bureau")
    monkeypatch.setattr(validate_card, "VALID_SLUGS", {"agent-bureau", "atlas"})
    monkeypatch.setattr(reconcile, "live_rail_slugs", lambda: frozenset({"agent-bureau"}))
    monkeypatch.setattr(reconcile, "workflow_on_default_branch", lambda wf: True)
    # Planning has its own rule and its own tests; nothing here is in it.
    monkeypatch.setattr(reconcile, "flag_stalled_planning", lambda: set())
    for ledger in (reconcile._degraded, reconcile._read_failures, reconcile._write_failures):
        ledger.clear()
    yield
    for ledger in (reconcile._degraded, reconcile._read_failures, reconcile._write_failures):
        ledger.clear()


def _iso(minutes_ago: float, now: datetime | None = None) -> str:
    at = (now or datetime.now(UTC)) - timedelta(minutes=minutes_ago)
    return at.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _card(updated: str, comments=(), identifier=CARD, state="Todo"):
    """A Todo build card the way the board read returns it. `comments` are
    (body, createdAt) pairs, oldest first, as a person reads the thread."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a build card",
        "description": "work",
        "updatedAt": updated,
        "state": {"name": state},
        "labels": {"nodes": [{"name": "repo:agent-bureau"}, {"name": "agent:engineer"}]},
        # The API answers newest first; window_nodes turns it round.
        "comments": {"nodes": [{"body": b, "createdAt": c} for b, c in reversed(comments)]},
    }


def _run(run_id, status, created, conclusion=""):
    return {"databaseId": int(run_id), "status": status,
            "conclusion": conclusion, "createdAt": created}


def _actions(runs=(), jobs=None, fail=None):
    """A double for reconcile._actions_read: the build workflow's run listing
    answers `runs` (newest first), a run's jobs answer `jobs[run_id]`, and
    `fail` makes the LISTING unreadable the way a 403 does."""
    jobs = jobs or {}

    def read(args):
        if args[0] == "run" and args[1] == "list":
            return (None, fail) if fail else (json.dumps(list(runs)), None)
        if args[0] == "api" and args[1].endswith("/jobs"):
            run_id = args[1].split("/runs/")[1].split("/")[0]
            return json.dumps({"jobs": [{"name": n} for n in jobs.get(run_id, [])]}), None
        raise AssertionError(f"unexpected Actions read: {args}")

    return read


def _job(identifier=CARD):
    return [f"{dedupe_dispatch.PLAN_JOB_MARKER} {identifier}"]


def _watch(cards, runs=(), jobs=None, fail=None, now: str | None = None):
    """Run flag_stranded over `cards`; returns (flagged, comment mock, label mock).
    `now` freezes the sweep's clock for the replay."""
    real_age = reconcile.age_minutes

    def frozen(iso, at=None):
        return real_age(iso, at or now)

    with patch.object(reconcile, "active_cards",
                      side_effect=lambda states=reconcile.SWEEP_STATES: [
                          c for c in cards if c["state"]["name"] in states]), \
         patch.object(reconcile, "_actions_read", side_effect=_actions(runs, jobs, fail)), \
         patch.object(reconcile, "age_minutes", side_effect=frozen), \
         patch.object(reconcile.linear_ops, "cmd_comment") as comment, \
         patch.object(reconcile.linear_ops, "add_label") as add_label:
        flagged = reconcile.flag_stranded()
    return flagged, comment, add_label


def _unknown_recorded(identifier=CARD) -> bool:
    return any(identifier in e and "UNKNOWN" in e for e in reconcile._degraded)


def _held_nothing(flagged, comment, add_label):
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


# --------------------------------------------------------------------------
# 1. a re-send buys a full start window
# --------------------------------------------------------------------------
def test_the_start_window_is_the_todo_lanes_own_stall_window():
    """One number, read off the lane contract: the sweep re-sends a Todo card
    on this window, so the watchdog waiting this long after a re-send can
    never fall out of step with the re-send it is judging."""
    assert reconcile.START_WINDOW_MINUTES == reconcile.STALE_MINUTES["Todo"]


def test_a_resend_one_minute_ago_is_not_stamped():
    """DRE-5595's own shape: the receipt is a minute old. It used to stand in
    for the whole thirty minutes and the card was stamped on the spot."""
    card = _card(_iso(1), [(RESEND, _iso(1))])
    _held_nothing(*_watch([card]))


def test_a_resend_just_inside_the_window_is_not_stamped():
    card = _card(_iso(reconcile.START_WINDOW_MINUTES - 1),
                 [(RESEND, _iso(reconcile.START_WINDOW_MINUTES - 1))])
    _held_nothing(*_watch([card]))


def test_a_full_window_after_the_resend_still_stamps():
    """The alarm the re-send receipt exists for (DRE-1993) still fires — a
    window later, not a minute later: dispatch fired, nothing ran."""
    sent = _iso(reconcile.START_WINDOW_MINUTES + 1)
    card = _card(sent, [(RESEND, sent)])
    flagged, comment, add_label = _watch([card])
    assert flagged == {CARD}
    assert comment.call_args_list[0].args[1].lstrip().startswith(f"🚨 {reconcile.WATCHDOG_TAG}:")
    add_label.assert_called_once_with(CARD, reconcile.HOLD_LABEL)


def test_the_newest_resend_is_the_one_that_counts():
    """Two re-sends: the old one is past the window, the new one is not. The
    build that was sent a minute ago is the one still owed its window."""
    old, new = _iso(reconcile.START_WINDOW_MINUTES + 20), _iso(1)
    card = _card(new, [(RESEND, old), (RESEND, new)])
    _held_nothing(*_watch([card]))


def test_a_run_github_lists_inside_the_window_defers_without_any_receipt():
    """The re-send's own receipt can be the write Linear refused. GitHub still
    records the run, so the dispatch time is read there: a build created two
    minutes ago is inside its window, whatever the card says."""
    card = _card(_iso(45))
    runs = [_run(901, "completed", _iso(2), "failure")]
    _held_nothing(*_watch([card], runs=runs, jobs={"901": _job()}))
    assert not _unknown_recorded()


def test_a_queued_or_running_build_is_not_stamped():
    """A build GitHub says is queued or running is on its way: no receipt yet
    is not "nothing has started"."""
    card = _card(_iso(45))
    runs = [_run(902, "queued", _iso(40))]
    _held_nothing(*_watch([card], runs=runs, jobs={"902": _job()}))


def test_another_cards_run_does_not_defer_this_one():
    card = _card(_iso(45))
    runs = [_run(903, "completed", _iso(2), "failure")]
    flagged, _, _ = _watch([card], runs=runs, jobs={"903": _job("DRE-5372")})
    assert flagged == {CARD}


# --------------------------------------------------------------------------
# 2. an untrustworthy reading is UNKNOWN, and UNKNOWN holds nothing
# --------------------------------------------------------------------------
def test_linear_refusing_this_sweep_records_unknown(monkeypatch):
    """The sweep's own Linear requests were refused for quota: the same
    refusal that keeps a sweep from writing keeps a build from recording that
    it started."""
    monkeypatch.setitem(reconcile.linear_ops._budget, "refused_after", 12)
    card = _card(_iso(45))
    _held_nothing(*_watch([card]))
    assert _unknown_recorded()


def test_a_linear_limit_death_in_the_window_records_unknown():
    reset = datetime.now(UTC) - timedelta(minutes=5)
    marker = dead_run.limit_marker("linear", "build", reset, "36100000001")
    card = _card(_iso(45), [(marker, _iso(20))])
    _held_nothing(*_watch([card]))
    assert _unknown_recorded()


def test_a_failed_resend_in_the_window_records_unknown():
    """GitHub refused the sweep's own re-send: nothing was sent, so a missing
    receipt says nothing about a build that never had a chance to start."""
    card = _card(_iso(45), [(FAILED_RESEND, _iso(20))])
    _held_nothing(*_watch([card]))
    assert _unknown_recorded()


def test_the_lookback_is_the_watchdog_window_plus_one_start_window():
    """How far back a failure or a run still speaks for the reading: the
    watchdog's own window, plus the start window of a build sent at its
    beginning. Bounded, so one dead run cannot silence the alarm for good."""
    assert reconcile.WATCHDOG_LOOKBACK_MINUTES == (
        reconcile.WATCHDOG_MINUTES + reconcile.START_WINDOW_MINUTES)


def test_a_run_older_than_the_lookback_does_not_block_the_stamp():
    card = _card(_iso(600))
    runs = [_run(907, "completed", _iso(reconcile.WATCHDOG_LOOKBACK_MINUTES + 5), "failure")]
    flagged, _, _ = _watch([card], runs=runs, jobs={"907": _job()})
    assert flagged == {CARD}


def test_the_failed_resend_words_are_the_receipts_own():
    """The watchdog reads the nudge loop's failure receipt by its words; the
    receipt is a literal (the act registry matches it by text), so the two
    are pinned together here rather than by a shared constant."""
    source = (ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8")
    assert f'"🚨 Reconcile: {reconcile._TODO_REDISPATCH_FAILED_NOTE} — ' in source
    assert FAILED_RESEND.startswith(
        f"🚨 Reconcile: {reconcile._TODO_REDISPATCH_FAILED_NOTE} — ")


def test_a_failure_older_than_the_window_does_not_block_the_stamp():
    """An outage last week is not this window's: the watchdog still alarms."""
    marker = dead_run.limit_marker("linear", "build", None, "36100000001")
    card = _card(_iso(600), [(marker, _iso(600))])
    flagged, _, _ = _watch([card])
    assert flagged == {CARD}


def test_an_unreadable_run_lookup_records_unknown():
    card = _card(_iso(45))
    _held_nothing(*_watch([card], fail="rc=1: HTTP 403: API rate limit exceeded"))
    assert _unknown_recorded()


def test_an_unreadable_jobs_read_records_unknown():
    """A run that could not be attributed might be this card's: the reading
    is incomplete, not empty."""
    card = _card(_iso(45))
    runs = [_run(904, "completed", _iso(20), "failure")]

    def read(args):
        if args[0] == "run":
            return json.dumps(runs), None
        return None, "rc=1: HTTP 502"

    with patch.object(reconcile, "active_cards",
                      side_effect=lambda states=reconcile.SWEEP_STATES: [
                          c for c in [card] if c["state"]["name"] in states]), \
         patch.object(reconcile, "_actions_read", side_effect=read), \
         patch.object(reconcile.linear_ops, "cmd_comment") as comment, \
         patch.object(reconcile.linear_ops, "add_label") as add_label:
        flagged = reconcile.flag_stranded()
    _held_nothing(flagged, comment, add_label)
    assert _unknown_recorded()


def test_a_run_that_left_no_receipt_records_unknown():
    """GitHub shows this card's build ran and finished, and the card carries
    nothing from it: the run's first write to Linear did not land, so "no
    receipt" is not "nothing has started"."""
    card = _card(_iso(45))
    runs = [_run(905, "completed", _iso(20), "failure")]
    _held_nothing(*_watch([card], runs=runs, jobs={"905": _job()}))
    assert _unknown_recorded()


# --------------------------------------------------------------------------
# 3. the stamp names its evidence
# --------------------------------------------------------------------------
def test_the_stamp_names_the_last_dispatch_and_the_run_lookup():
    sent_at = datetime.now(UTC) - timedelta(minutes=reconcile.START_WINDOW_MINUTES + 4)
    sent = sent_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    card = _card(sent, [(RESEND, sent)])
    runs = [_run(906, "completed", _iso(3), "success")]
    flagged, comment, _ = _watch([card], runs=runs, jobs={"906": _job("DRE-5372")})
    assert flagged == {CARD}
    body = comment.call_args_list[0].args[1]
    assert "last dispatch" in body.lower()
    assert dead_run.pacific(sent_at) in body, "the dispatch time, in PT"
    assert "run lookup" in body.lower()
    assert reconcile.build_workflow() in body, "which workflow was asked"


def test_the_stamp_says_when_no_dispatch_time_is_on_record():
    card = _card(_iso(45))
    flagged, comment, _ = _watch([card])
    assert flagged == {CARD}
    body = comment.call_args_list[0].args[1].lower()
    assert "last dispatch" in body and "none on record" in body


# --------------------------------------------------------------------------
# 4. the replay: 2026-10-02, a re-send at 14:47 PT inside a Linear 429 window
# --------------------------------------------------------------------------
OUTAGE_DAY = datetime(2026, 10, 2, tzinfo=UTC)


def _pt(hh: int, mm: int) -> str:
    """A 2026-10-02 Pacific (PDT, UTC-7) wall-clock time as the API's UTC."""
    return (OUTAGE_DAY + timedelta(hours=hh + 7, minutes=mm)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def _outage_card(identifier=CARD, with_marker=False):
    """DRE-5595 as the board held it: promoted and dispatched before the
    outage, never a receipt from a run, re-sent at 14:47 PT. With the marker,
    the medic's classification of the 14:47 run's death — a Linear 429 at
    Card → In Progress — has also reached the card."""
    comments = [
        ("🧹 Auto-promoted to Todo — every blocker is Done.", _pt(14, 2)),
        (RESEND, _pt(14, 47)),
    ]
    if with_marker:
        reset = OUTAGE_DAY + timedelta(hours=22)  # 15:00 PT
        comments.append((dead_run.limit_marker("linear", "build", reset, "36190000047"),
                         _pt(14, 52)))
    return _card(comments[-1][1], comments, identifier=identifier)


def _outage_runs():
    """The build stub's listing: the 14:47 re-send ran and died on Linear's
    429 within the minute, before it could write its receipt."""
    return [_run(36190000047, "completed", _pt(14, 47), "failure"),
            _run(36190000002, "completed", _pt(14, 2), "failure")]


_OUTAGE_JOBS = {"36190000047": _job(), "36190000002": _job()}


@pytest.mark.parametrize("sweep_at", [(14, 48), (14, 55), (15, 3), (15, 20)],
                         ids=["14:48", "14:55", "15:03", "15:20"])
@pytest.mark.parametrize("with_marker", [False, True], ids=["no-marker", "medic-marker"])
def test_replay_2026_10_02_no_needs_human(sweep_at, with_marker):
    """The 14:48 sweep is the incident itself: one minute after the re-send.
    The later sweeps are the rest of the outage and its aftermath — the run
    GitHub lists for the card left no receipt, and (with the marker) the
    medic said why. None of them may stamp needs-human."""
    card = _outage_card(with_marker=with_marker)
    flagged, comment, add_label = _watch(
        [card], runs=_outage_runs(), jobs=_OUTAGE_JOBS, now=_pt(*sweep_at))
    _held_nothing(flagged, comment, add_label)


def test_replay_all_four_cards_at_14_48():
    """All four cards the outage stamped, one sweep: none is held."""
    cards = [_outage_card(i) for i in ("DRE-5595", "DRE-5372", "DRE-5410", "DRE-5411")]
    flagged, comment, add_label = _watch(
        cards, runs=_outage_runs(), jobs=_OUTAGE_JOBS, now=_pt(14, 48))
    _held_nothing(flagged, comment, add_label)
