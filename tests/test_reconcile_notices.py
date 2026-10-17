"""RED-first tests for DRE-7455 — the sweep raises console alerts, not cards.

Two of the reconcile sweep's notices, the fleet reviewer outage and the groom
stall, used to be Linear cards created straight into Triage, the lane DRE-2723
keeps for broken cards. Each is now one console alert, raised and resolved
through `scripts/pipeline_notice.py` (DRE-7451):

  * `reviewer_outage:fleet` at CRITICAL — raised by every full sweep that
    finds the outage and resolved, with the all-clear copy, by a sweep that
    finds no could-not-run at all;
  * `groom_stall` at WARN — raised while the stall holds and resolved, with no
    all-clear copy, by a pass where it no longer does.

The client is faked here, and the board is too: creating a card on any of
these paths is a failure, never a cost. When the door cannot take a raise the
client files the card the writer filed before, so each writer hands it that
card exactly — title, lane and labels.

The third writer, the switch alarm, lives in `scripts/switch_reason.py` and
is held by `tests/test_switch_reason.py`; this file reads all three off
`notice_writers.py report`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reconcile_notices.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
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

import epic_cap  # noqa: E402
import groomer  # noqa: E402
import linear_ops  # noqa: E402
import medic_classify  # noqa: E402
import pipeline_notice  # noqa: E402
import planner_queue  # noqa: E402
import reconcile  # noqa: E402
import reviewer_down  # noqa: E402

SHA = "d34db33fcafe1234d34db33fcafe1234d34db33f"
#: The sweep's own moment, frozen: 22:05 UTC on 2026-10-16 is 15:05 PT.
FIXED = datetime(2026, 10, 16, 22, 5, tzinfo=UTC)
RECOVERY_TITLE = "Reviewer back — reviews are running again"
RECOVERY_BODY = ("The reconcile sweep at 15:05 PT found the fleet-wide reviewer "
                 "outage cleared.")


class _Frozen(datetime):
    """`reconcile.datetime`, with `now` pinned to FIXED."""

    @classmethod
    def now(cls, tz=None):
        return FIXED if tz is not None else FIXED.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/agent-bureau")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.setattr(reconcile, "datetime", _Frozen)
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


def _iso(minutes_ago: float) -> str:
    return (FIXED - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


class FakeNotices:
    """`pipeline_notice` behind the sweep: every raise and resolve, in order,
    answered the way the door would answer one row per key."""

    def __init__(self, outcome=None):
        self.calls: list = []
        self.open: dict = {}
        self.outcome = outcome

    def raise_notice(self, notice, *, severity, title, body, action_url=None,
                     action_label=None, fallback=None):
        self.calls.append(("raise", notice, {
            "severity": severity, "title": title, "body": body,
            "action_url": action_url, "action_label": action_label,
            "fallback": fallback}))
        if self.outcome:
            return self.outcome
        was = self.open.get(notice)
        self.open[notice] = severity
        return "repeat" if was == severity else "raised"

    def resolve_notice(self, notice, *, recovery_title=None, recovery_body=None):
        self.calls.append(("resolve", notice, {
            "recovery_title": recovery_title, "recovery_body": recovery_body}))
        return "resolved" if self.open.pop(notice, None) else "not-open"

    def of(self, action: str) -> list:
        return [(notice, kw) for kind, notice, kw in self.calls if kind == action]


def _no_card(*_a, **_k):
    raise AssertionError("a card was created — this notice is a console alert")


# --------------------------------------------------------------------------- #
# the fleet reviewer outage                                                    #
# --------------------------------------------------------------------------- #


def _neutral(pr, minutes_ago, n=1):
    return {"body": f"{medic_classify.CRITIC_NEUTRAL_MARKER} — the run died",
            "createdAt": _iso(minutes_ago),
            "url": f"https://github.com/x/y/pull/{pr}#issuecomment-{pr}{n}"}


def _verdict(minutes_ago):
    return {"body": f"🔎 QA Critic — VERDICT: APPROVE @{SHA}\n\nLooks solid.",
            "createdAt": _iso(minutes_ago),
            "url": "https://github.com/x/y/pull/141#issuecomment-999"}


def _pr(number, comments):
    return {"number": number, "headRefName": f"agent/DRE-{number}-widget",
            "headRefOid": SHA, "baseRefName": "main",
            "mergeStateStatus": "CLEAN", "isDraft": False,
            "comments": list(comments)}


#: Three could-not-run receipts in this repository inside the window: the run
#: rule's three in a row.
OUTAGE_PRS = [_pr(141, [_neutral(141, 12)]), _pr(142, [_neutral(142, 8, 2)]),
              _pr(143, [_neutral(143, 4, 3)])]
QUIET_PRS = [_pr(141, [_verdict(3)])]


def _gh(prs):
    def fake(*args):
        if args[:2] == ("pr", "list"):
            return json.dumps(prs)
        raise AssertionError(f"unexpected gh call: {args}")
    return fake


def _actions(*args):
    if args[:2] == ("run", "list"):
        return json.dumps([{
            "databaseId": 34512000001,
            "url": ("https://github.com/dreadnought-foundry/agent-bureau/"
                    "actions/runs/34512000001"),
            "conclusion": "failure", "createdAt": _iso(12)}])
    if args[:2] == ("run", "view"):
        return "agent\tqa\t2026-10-16T21:53:03Z ReferenceError: native binary not found"
    raise AssertionError(f"unexpected Actions read: {args}")


def _sweep(prs, notices, cards=()):
    """One full sweep's outage backstop over a fixed world: a fake client, a
    board that refuses every write, and Linear refusing every request."""
    with patch.object(reconcile, "pipeline_notice", notices), \
        patch.object(reconcile, "gh", side_effect=_gh(prs)), \
        patch.object(reconcile, "gh_actions_read", side_effect=_actions), \
        patch.object(reconcile, "active_cards",
                     side_effect=lambda *a, **k: list(cards)), \
        patch.object(reconcile.bureau_read, "mode", return_value="off"), \
        patch.object(reconcile.linear_ops, "create_card", side_effect=_no_card), \
        patch.object(reconcile.linear_ops, "cmd_comment", side_effect=_no_card), \
        patch.object(reconcile.linear_ops, "cmd_state", side_effect=_no_card), \
        patch.object(reconcile.linear_ops, "gql",
                     side_effect=AssertionError("no Linear request")):
        reconcile.reset_sweep_cards()
        reconcile.report_fleet_reviewer_outage()


def test_a_sweep_that_counts_the_outage_raises_it_critical_and_creates_no_card():
    notices = FakeNotices()
    _sweep(OUTAGE_PRS, notices)
    (notice, kw), = notices.of("raise")
    assert notice == "reviewer_outage:fleet"
    assert kw["severity"] == "CRITICAL"
    assert kw["title"] == reviewer_down.card_title(_iso(12), 3, 1)
    assert kw["title"].startswith("Reviewer down since ")
    assert kw["title"].endswith("— 3 runs, 1 repos")
    # The body is the one reviewer_down composes today: its run list included.
    assert "The three usual suspects" in kw["body"]
    assert kw["body"].count(reviewer_down.LEDGER_PREFIX) == 3
    assert notices.of("resolve") == []
    assert reconcile._write_failures == []


def test_a_second_sweep_finding_the_same_outage_raises_the_same_key_again():
    notices = FakeNotices()
    _sweep(OUTAGE_PRS, notices)
    _sweep(OUTAGE_PRS, notices)
    raises = notices.of("raise")
    assert [n for n, _ in raises] == ["reviewer_outage:fleet"] * 2
    assert raises[0][1]["title"] == raises[1][1]["title"]
    assert notices.of("resolve") == []


def test_the_outage_hands_the_client_todays_card_as_its_fallback():
    notices = FakeNotices()
    _sweep(OUTAGE_PRS, notices)
    (_, kw), = notices.of("raise")
    fallback = kw["fallback"]
    assert isinstance(fallback, pipeline_notice.Fallback)
    assert fallback.title == kw["title"]
    assert fallback.lane == "Triage" == reconcile.FLEET_OUTAGE_LANE
    assert tuple(fallback.labels) == ("repo:agent-bureau", linear_ops.NO_CODE_LABEL)


def test_a_sweep_finding_no_outage_resolves_it_with_the_all_clear_copy():
    notices = FakeNotices()
    _sweep(QUIET_PRS, notices)
    assert notices.of("raise") == []
    (notice, kw), = notices.of("resolve")
    assert notice == "reviewer_outage:fleet"
    assert kw["recovery_title"] == RECOVERY_TITLE
    assert kw["recovery_body"] == RECOVERY_BODY


def test_the_all_clear_names_the_sweeps_own_time_in_pt(monkeypatch):
    later = datetime(2026, 10, 17, 3, 41, tzinfo=UTC)  # 20:41 PT on the 16th

    class _Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return later

    monkeypatch.setattr(reconcile, "datetime", _Later)
    notices = FakeNotices()
    _sweep([], notices)
    (_, kw), = notices.of("resolve")
    assert kw["recovery_body"] == (
        "The reconcile sweep at 20:41 PT found the fleet-wide reviewer outage "
        "cleared.")


def test_an_outage_raised_then_cleared_is_resolved_on_the_next_quiet_sweep():
    notices = FakeNotices()
    _sweep(OUTAGE_PRS, notices)
    _sweep(QUIET_PRS, notices)
    assert [kind for kind, _, _ in notices.calls] == ["raise", "resolve"]
    assert notices.open == {}


def test_a_crash_below_the_threshold_neither_raises_nor_resolves():
    """Another repository's sweep may be raising off crashes this one cannot
    see as a whole; a resolve between two of its raises would open the alert
    again and email the CEO twice for one outage."""
    notices = FakeNotices()
    _sweep([_pr(141, [_neutral(141, 6)])], notices)
    assert notices.calls == []


def test_a_lost_raise_takes_the_sweep_red():
    """The door refused and the fallback card could not be filed either: the
    sweep says so on its fail-loudly rail, as a failed create did."""
    notices = FakeNotices(outcome="lost")
    _sweep(OUTAGE_PRS, notices)
    assert any("reviewer-outage" in f for f in reconcile._write_failures), (
        reconcile._write_failures)


def test_a_fallback_card_is_not_a_failure():
    notices = FakeNotices(outcome="fallback-card")
    _sweep(OUTAGE_PRS, notices)
    assert reconcile._write_failures == []


# --------------------------------------------------------------------------- #
# the groom stall                                                              #
# --------------------------------------------------------------------------- #

STANDING = "DRE-5400"
BATCH = "0123456789ab"


def _queued_record(cards, minutes_ago):
    return {"body": groomer.queued_record(
                BATCH, [{"identifier": i, "repo": "portico"} for i in cards]),
            "authored_by_pipeline": True, "created_at": _iso(minutes_ago)}


def _released_record(released, minutes_ago):
    return {"body": groomer.released_record(
                [{"id": BATCH, "released": released, "left": [], "unqueued": []}]),
            "authored_by_pipeline": True, "created_at": _iso(minutes_ago)}


def _intake(ident):
    return {"id": f"uuid-{ident}", "identifier": ident, "title": "a card",
            "description": "work", "state": {"name": "Intake"},
            "labels": {"nodes": [{"name": groomer.QUEUED_LABEL}]},
            "comments": {"nodes": []}}


def _groom_pass(thread, cards, free, notices, fail_state=()):
    def cmd_state(ident, lane, *flags, **kw):
        if ident in fail_state:
            raise linear_ops.LinearError(f"{ident}: Linear said no")
        return True

    with patch.dict(os.environ, {reconcile.GROOM_CARD_ENV: STANDING}), \
        patch.object(reconcile, "REPO_SLUG", epic_cap.START_OWNER_SLUG), \
        patch.object(reconcile, "pipeline_notice", notices), \
        patch.object(reconcile, "active_cards",
                     side_effect=lambda *a, **k: list(cards)), \
        patch.object(reconcile, "_door_lane_still", return_value=True), \
        patch.object(linear_ops, "comment_records",
                     side_effect=lambda *a, **k: list(thread)), \
        patch.object(linear_ops, "cmd_state", side_effect=cmd_state), \
        patch.object(linear_ops, "remove_label"), \
        patch.object(linear_ops, "cmd_comment"), \
        patch.object(linear_ops, "create_card", side_effect=_no_card), \
        patch.object(linear_ops, "find_open", side_effect=_no_card):
        return reconcile.release_groom_queue(free)


def _stalled_thread():
    bound = planner_queue.waiting_max()
    return [_queued_record(["DRE-11", "DRE-12"], bound + 60),
            _released_record(["DRE-10"], bound + 30)]


def test_a_stalled_queue_raises_groom_stall_at_warn_and_creates_no_card():
    notices = FakeNotices()
    _groom_pass(_stalled_thread(), [_intake("DRE-11"), _intake("DRE-12")], 1,
                notices, fail_state={"DRE-11", "DRE-12"})
    (notice, kw), = notices.of("raise")
    assert notice == "groom_stall"
    assert kw["severity"] == "WARN"
    assert kw["title"] == reconcile.GROOM_STALL_TITLE
    assert BATCH in kw["body"] and "2 cards" in kw["body"]
    assert notices.of("resolve") == []


def test_the_groom_stall_hands_the_client_todays_card_as_its_fallback():
    notices = FakeNotices()
    _groom_pass(_stalled_thread(), [_intake("DRE-11"), _intake("DRE-12")], 1,
                notices, fail_state={"DRE-11", "DRE-12"})
    (_, kw), = notices.of("raise")
    fallback = kw["fallback"]
    assert isinstance(fallback, pipeline_notice.Fallback)
    assert fallback.title == reconcile.GROOM_STALL_TITLE
    assert fallback.lane == "Triage" == reconcile.GROOM_STALL_LANE
    assert tuple(fallback.labels) == ("repo:bureau-pipeline", linear_ops.NO_CODE_LABEL)


def test_a_later_pass_with_the_stall_gone_resolves_it_with_no_recovery_copy():
    notices = FakeNotices()
    cards = [_intake("DRE-11"), _intake("DRE-12")]
    _groom_pass(_stalled_thread(), cards, 1, notices,
                fail_state={"DRE-11", "DRE-12"})
    _groom_pass(_stalled_thread(), cards, 1, notices)  # the moves land
    assert [kind for kind, _, _ in notices.calls] == ["raise", "resolve"]
    (notice, kw), = notices.of("resolve")
    assert notice == "groom_stall"
    assert kw == {"recovery_title": None, "recovery_body": None}


@pytest.mark.parametrize("free", [0, 1])
def test_a_queue_that_moved_inside_the_bound_resolves_and_raises_nothing(free):
    notices = FakeNotices()
    _groom_pass([_queued_record(["DRE-11"], 30)], [_intake("DRE-11")], free,
                notices, fail_state={"DRE-11"})
    assert notices.of("raise") == []
    assert [n for n, _ in notices.of("resolve")] == ["groom_stall"]


def test_an_empty_queue_resolves_the_stall():
    notices = FakeNotices()
    _groom_pass([], [], 2, notices)
    assert [n for n, _ in notices.of("resolve")] == ["groom_stall"]
    assert notices.of("raise") == []


def test_a_lost_stall_raise_takes_the_sweep_red():
    notices = FakeNotices(outcome="lost")
    _groom_pass(_stalled_thread(), [_intake("DRE-11"), _intake("DRE-12")], 1,
                notices, fail_state={"DRE-11", "DRE-12"})
    assert any("groom" in f and "stall" in f for f in reconcile._write_failures), (
        reconcile._write_failures)


# --------------------------------------------------------------------------- #
# the enumeration reads all three as repointed                                 #
# --------------------------------------------------------------------------- #


def test_notice_writers_report_reads_the_three_as_repointed():
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "notice_writers.py"), "report"],
        capture_output=True, text=True, cwd=ROOT, check=False)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    for writer in ("reviewer-outage", "groom-stall", "switch-alarm"):
        assert f"notice-writers: {writer} repointed" in lines, lines


def test_the_sweep_still_declares_every_card_writer():
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "notice_writers.py"), "check"],
        capture_output=True, text=True, cwd=ROOT, check=False)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_removed_board_machinery_is_gone():
    """The one-row-per-key alert is the dedupe now: nothing reads the board to
    keep one card, cancels a duplicate, or caps how many a sweep files."""
    for name in ("_fleet_outage_open_card", "_fleet_outage_resolve_duplicates",
                 "_fleet_outage_state", "FLEET_OUTAGE_SWEEP_CAP"):
        assert not hasattr(reconcile, name), name
