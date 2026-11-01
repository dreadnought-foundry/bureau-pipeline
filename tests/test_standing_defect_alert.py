"""A card defect that keeps the sweep red raises a console alert (DRE-8632).

From 10-30 10:46 AM PDT to about 5:36 PM PDT on 10-31, 120 scheduled
agent-bureau Reconcile runs went red on one prose blocker on DRE-7305, and the
only signal was a `::notice::` on a run nobody opens. The full sweep now hands
its `_stale_defects` ledger to `standing_defect_alert.settle`, which raises one
WARN console alert per card the ledger names and resolves the ones whose card
has left it. What these pin, each against the failure it exists to stop:

* the card is read off each appender's real ledger shape, and an entry with
  no card (the idle board) raises nothing and is counted;
* one raise per card, at WARN, with no fallback card, under the contract's
  key, title and body;
* a pass resolves only this repo's standing-defect alerts whose card is not
  on its ledger — and resolves nothing when the open alerts cannot be read;
* the full sweep calls it once, with the ledger as it stands; the
  `--promote-only` gate never does; and nothing it raises moves the exit.

No test here reaches the console, GitHub or Linear: the notice client and the
open-alerts read are replaced at the module boundary, and the sweep runs over
a fake board.
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import bureau_read  # noqa: E402
import limit_recovery  # noqa: E402
import pipeline_notice  # noqa: E402
import promotion_stall  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
import standing_defect_alert  # noqa: E402

REPO = "bureau-pipeline"
#: The card's own entry, as `_report_epic_prose_defect` writes it.
DRE_7305 = ("DRE-7305: a prose blocker declaring DRE-7300 with no blockedBy "
            "relation has stood for 3.0h")
IDLE = promotion_stall.LEDGER_IDLE_OPENER + (
    "5: 2 cards in Queued stand refused or held with nothing dispatched; the "
    "oldest receipt, on DRE-10, is 2.5h old — a person must act")
REPAIR_SENTENCE = ("The sweep turns green when this defect is fixed. If it is still "
                   "open after 2 hours, the Alarm Repair makes one attempt on it.")


class Door:
    """The notice client and the open-alerts read, as `settle` sees them."""

    def __init__(self, *, open_alerts=(), outcome="raised", unreadable=None):
        self.raised: list[dict] = []
        self.resolved: list[str] = []
        self.outcome = outcome
        self.alerts = [dict(a) for a in open_alerts]
        self.unreadable = unreadable

    def raise_notice(self, notice, **kw):
        self.raised.append({"notice": notice, **kw})
        return self.outcome

    def resolve_notice(self, notice, **kw):
        self.resolved.append(notice)
        return "resolved"

    def open_alerts(self):
        if self.unreadable is not None:
            raise self.unreadable
        return {"schema": "bureau-read/1", "read_at": "2026-11-01T16:00:00Z",
                "alerts": self.alerts}


@pytest.fixture
def door(monkeypatch):
    def make(**kw):
        fake = Door(**kw)
        monkeypatch.setattr(pipeline_notice, "raise_notice", fake.raise_notice)
        monkeypatch.setattr(pipeline_notice, "resolve_notice", fake.resolve_notice)
        monkeypatch.setattr(bureau_read, "open_alerts", fake.open_alerts)
        return fake
    return make


def alert(kind: str) -> dict:
    card = kind.rsplit(":", 1)[-1]
    return {"kind": kind, "severity": "WARN", "title": f"{card} is open",
            "raised_at": "2026-10-30T17:46:00Z",
            "card": card if card.startswith("DRE-") else None}


# ── which card an entry names ───────────────────────────────────────────────


def test_each_card_bearing_appender_names_its_card():
    """The three appenders that name a card, in the shapes they write today."""
    stalled = promotion_stall.ledger_line(
        promotion_stall.Refused("DRE-41", "no-verdict", "2026-10-31T00:00:00Z"), 180)
    recovery = (f"ERROR: {limit_recovery.RECOVERY_TAG} DRE-42: "
                f"{limit_recovery.STOOD_PHRASE} 3.0 hours (session limit, build "
                "stage) — handed to a person")
    assert standing_defect_alert.card_of(DRE_7305) == "DRE-7305"
    assert standing_defect_alert.card_of(stalled) == "DRE-41"
    assert standing_defect_alert.card_of(recovery) == "DRE-42"


@pytest.mark.parametrize("entry", [
    IDLE,
    "",
    "a prose blocker on DRE-7305 has stood",  # a card named, but not opening it
    "DRE-: no number",
    "promotion-stalled the board: no card",
])
def test_an_entry_that_opens_with_no_card_names_none(entry):
    assert standing_defect_alert.card_of(entry) is None


# ── the raise ───────────────────────────────────────────────────────────────


def test_the_cards_entry_raises_its_alert_at_warn_with_no_fallback(door, capsys):
    fake = door()
    lines = standing_defect_alert.settle([DRE_7305], REPO)
    assert len(fake.raised) == 1
    raised = fake.raised[0]
    assert raised["notice"] == "standing-defect:bureau-pipeline:DRE-7305"
    assert raised["severity"] == "WARN"
    assert "fallback" in raised and raised["fallback"] is None
    assert raised["title"] == "DRE-7305 is keeping the bureau-pipeline sweep red"
    assert DRE_7305 in raised["body"]
    assert raised["body"].index(DRE_7305) < raised["body"].index(REPAIR_SENTENCE)
    assert "standing-defect-alert: raised card=DRE-7305" in lines
    assert "standing-defect-alert: raised card=DRE-7305" in capsys.readouterr().out.splitlines()


def test_the_notice_key_is_one_the_door_admits():
    assert pipeline_notice.KEY_PATTERN.match("standing-defect:bureau-pipeline:DRE-7305")


def test_two_entries_naming_one_card_raise_once(door):
    fake = door()
    other = DRE_7305.replace("DRE-7300", "DRE-7301")
    lines = standing_defect_alert.settle([DRE_7305, other], REPO)
    assert [r["notice"] for r in fake.raised] == ["standing-defect:bureau-pipeline:DRE-7305"]
    assert lines.count("standing-defect-alert: raised card=DRE-7305") == 1


def test_two_cards_raise_two_alerts(door):
    fake = door()
    stalled = promotion_stall.ledger_line(
        promotion_stall.Refused("DRE-41", "no-verdict", "2026-10-31T00:00:00Z"), 180)
    standing_defect_alert.settle([DRE_7305, stalled], REPO)
    assert [r["notice"] for r in fake.raised] == [
        "standing-defect:bureau-pipeline:DRE-7305",
        "standing-defect:bureau-pipeline:DRE-41",
    ]


def test_an_entry_with_no_card_raises_nothing_and_is_counted(door):
    fake = door()
    lines = standing_defect_alert.settle([IDLE], REPO)
    assert fake.raised == []
    assert "standing-defect-alert: 1 entry(ies) name no card" in lines
    assert "standing-defect-alert: none standing" in lines


@pytest.mark.parametrize("outcome", ["repeat", "lost"])
def test_a_repeat_and_a_lost_raise_are_each_said(door, outcome):
    door(outcome=outcome)
    lines = standing_defect_alert.settle([DRE_7305], REPO)
    assert f"standing-defect-alert: {outcome} card=DRE-7305" in lines


def test_every_line_opens_with_the_tag(door):
    door(open_alerts=[alert("pipeline_notice:standing-defect:bureau-pipeline:DRE-9")])
    lines = standing_defect_alert.settle([DRE_7305, IDLE], REPO)
    assert lines
    assert all(line.startswith("standing-defect-alert: ") for line in lines)


# ── the resolve ─────────────────────────────────────────────────────────────


OPEN_TWO = [alert("pipeline_notice:standing-defect:bureau-pipeline:DRE-7305"),
            alert("pipeline_notice:standing-defect:portico:DRE-1")]


def test_a_green_pass_resolves_only_this_repos_alert(door):
    fake = door(open_alerts=OPEN_TWO)
    lines = standing_defect_alert.settle([], REPO)
    assert fake.resolved == ["standing-defect:bureau-pipeline:DRE-7305"]
    assert fake.raised == []
    assert "standing-defect-alert: resolved card=DRE-7305" in lines
    assert "standing-defect-alert: none standing" in lines


def test_a_card_still_on_the_ledger_is_not_resolved(door):
    fake = door(open_alerts=OPEN_TWO + [
        alert("pipeline_notice:standing-defect:bureau-pipeline:DRE-41")])
    standing_defect_alert.settle([DRE_7305], REPO)
    assert fake.resolved == ["standing-defect:bureau-pipeline:DRE-41"]


@pytest.mark.parametrize("kind", [
    "pipeline_stuck:DRE-7305",
    "pipeline_notice:hold-late:bureau-pipeline:hand-work-overdue",
    "pipeline_notice:standing-defect:bureau-pipeline-two:DRE-7305",
    "pipeline_notice:standing-defect:bureau-pipeline:DRE-7305:extra",
])
def test_an_alert_that_is_not_this_repos_standing_defect_is_left_alone(door, kind):
    fake = door(open_alerts=[alert(kind)])
    standing_defect_alert.settle([], REPO)
    assert fake.resolved == []


def test_a_ledger_not_evaluated_resolves_nothing_and_says_so(door, monkeypatch):
    """An empty ledger from a pass that never ran its gates is absence, not
    green: the raise half still runs, the resolve half does not, and the
    open alerts are not even read."""
    fake = door(open_alerts=OPEN_TWO)
    reads: list = []
    monkeypatch.setattr(bureau_read, "open_alerts",
                        lambda: reads.append(1) or fake.open_alerts())
    lines = standing_defect_alert.settle(
        [], REPO, unread=["promote_ready (linear-hold)"])
    assert fake.resolved == []
    assert reads == []
    said = [line for line in lines if "not evaluated" in line]
    assert said == ["standing-defect-alert: ledger not evaluated — "
                    "promote_ready (linear-hold); nothing resolved this pass"]
    assert "standing-defect-alert: none standing" in lines


def test_a_ledger_not_evaluated_still_raises_what_it_did_find(door):
    fake = door(open_alerts=OPEN_TWO + [
        alert("pipeline_notice:standing-defect:bureau-pipeline:DRE-41")])
    standing_defect_alert.settle([DRE_7305], REPO, unread=["recover_limit_deaths"])
    assert [r["notice"] for r in fake.raised] == ["standing-defect:bureau-pipeline:DRE-7305"]
    assert fake.resolved == []


def test_unreadable_open_alerts_resolve_nothing_and_say_so(door):
    fake = door(open_alerts=OPEN_TWO,
                unreadable=bureau_read.ReadUnknown("closed", "HTTP 503 db down"))
    lines = standing_defect_alert.settle([DRE_7305], REPO)
    assert fake.resolved == []
    assert [r["notice"] for r in fake.raised] == ["standing-defect:bureau-pipeline:DRE-7305"]
    said = [line for line in lines if "open alerts unreadable" in line]
    assert len(said) == 1
    assert said[0].startswith("standing-defect-alert: open alerts unreadable — ")
    assert "closed" in said[0]


# ── the sweep: one call, at the end of the full sweep, never on the gate ────


FLEET_VERDICT = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")
EPIC = "DRE-7305"


def _hours_ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def _child() -> dict:
    """A Queued child of the epic, eligible on every ground but its epic."""
    return {
        "identifier": "DRE-7310",
        "title": "a child of the epic",
        "description": "**Repo:** bureau-pipeline\nwork",
        "createdAt": _hours_ago(24),
        "state": {"name": "Queued"},
        "parent": {"identifier": EPIC, "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
        "comments": {"nodes": [{"body": FLEET_VERDICT, "createdAt": _hours_ago(20)}]},
        "inverseRelations": {"nodes": []},
    }


def _epic() -> dict:
    """The epic: a prose blocker on DRE-7300 and no relation behind it."""
    return {"identifier": EPIC, "description": "**Blocked by:** DRE-7300",
            "inverseRelations": {"nodes": []}}


#: Every backstop the full sweep runs that is not the gate under test.
_STOOD_DOWN = (
    "drain_retiring_lanes", "unstick_conflicts", "refresh_stale_merge_refs",
    "retrigger_dead_heads", "flag_no_checks_prs", "flag_unowned_prs",
    "fix_approved_but_red", "retry_dead_fix_runs", "redispatch_standing_verdicts",
    "recover_limit_deaths", "restart_answered_blockers", "card_dependabot_prs",
    "review_dependabot_prs", "recover_crashed_reviews",
    "report_fleet_reviewer_outage", "check_dependabot_capacity",
    "report_intake_depth", "repair_frozen_planning_holds", "close_finished_epics",
    "report_break_glass", "report_fix_concurrency", "report_evicted_fix_runs",
    "rerun_runner_lost_ci", "resync_desynced_heads", "flag_unlanded_work",
)


@pytest.fixture
def board(monkeypatch):
    """The fake board: one In Progress epic whose prose defect was first
    said 3 hours ago, and its Queued child. Linear's writes are recorded,
    GitHub answers empty, and the ledgers start clean."""
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-pipeline")
    monkeypatch.setattr(reconcile, "REPO_SLUG", REPO)
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects, reconcile._card_skips, reconcile._degraded):
        ledger.clear()
    stubs = {name: mock.MagicMock() for name in _STOOD_DOWN}
    stubs.update({
        "flag_stranded": mock.MagicMock(return_value=set()),
        "report_epic_growth": mock.MagicMock(return_value=[]),
        "active_cards": mock.MagicMock(return_value=[]),
        "backlog_children": mock.MagicMock(return_value=[_child()]),
        "_fetch_epic_relations": mock.MagicMock(return_value=_epic()),
        "merged_card_scope": mock.MagicMock(return_value=None),
    })
    for name, stub in stubs.items():
        monkeypatch.setattr(reconcile, name, stub)
    lops = reconcile.linear_ops
    monkeypatch.setattr(lops, "open_pass", mock.MagicMock())
    monkeypatch.setattr(lops, "cmd_comment", mock.MagicMock())
    monkeypatch.setattr(lops, "cmd_advance", mock.MagicMock())
    monkeypatch.setattr(lops, "cmd_state", mock.MagicMock())
    monkeypatch.setattr(lops, "comment_bodies", mock.MagicMock(return_value=[]))
    monkeypatch.setattr(lops, "count_comments", mock.MagicMock(return_value=1))
    monkeypatch.setattr(lops, "first_comment_at", mock.MagicMock(return_value=_hours_ago(3)))
    # Every other Linear read is unanswered, so nothing leaves the process.
    offline = mock.MagicMock(side_effect=lops.LinearError("offline in this test"))
    monkeypatch.setattr(lops, "gql", offline)
    monkeypatch.setattr(lops, "gql_paged", offline)
    monkeypatch.setattr(reconcile.subprocess, "run", mock.MagicMock(
        side_effect=lambda argv, **kw: mock.Mock(
            returncode=0, stdout="[]" if argv[:3] == ["gh", "pr", "list"] else "",
            stderr="")))
    monkeypatch.setattr(reconcile.time, "sleep", lambda s: None)
    yield
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects, reconcile._card_skips, reconcile._degraded):
        ledger.clear()


@pytest.fixture
def notice_door(monkeypatch):
    """The notice door's wire and the open-alerts read: every raise and resolve
    the sweep makes, through the real client, is recorded here."""
    sent: list[dict] = []

    def _send(payload):
        sent.append(payload)
        return "raised" if payload["action"] == "raise" else "resolved"

    monkeypatch.setattr(pipeline_notice, "_send", _send)
    monkeypatch.setattr(bureau_read, "open_alerts", lambda: {
        "schema": "bureau-read/1", "read_at": "2026-11-01T16:00:00Z", "alerts": []})
    return sent


def _exit_of(**kw) -> str | None:
    try:
        reconcile.main(**kw)
    except SystemExit as e:
        return str(e.code)
    return None


def test_the_full_sweep_raises_the_epics_alert_and_exits_red_as_before(
        board, notice_door, capsys):
    exit_text = _exit_of()
    assert exit_text == ("reconcile: 0 write / 0 read failure(s), 1 unfixed card "
                         "defect(s) — see ERROR lines above")
    raises = [p for p in notice_door
              if p["action"] == "raise" and p["notice"].startswith("standing-defect:")]
    assert [p["notice"] for p in raises] == [f"standing-defect:{REPO}:{EPIC}"]
    assert raises[0]["severity"] == "WARN"
    assert raises[0]["title"] == f"{EPIC} is keeping the {REPO} sweep red"
    assert "has stood for 3.0h" in raises[0]["body"]
    out = capsys.readouterr().out.splitlines()
    assert f"standing-defect-alert: raised card={EPIC}" in out
    assert f"pipeline-notice: raised kind=pipeline_notice:standing-defect:{REPO}:{EPIC}" in out


def test_the_full_sweep_hands_settle_the_ledger_once(board, notice_door, monkeypatch):
    seen: list = []
    monkeypatch.setattr(standing_defect_alert, "settle",
                        lambda entries, repo, **kw: seen.append((list(entries), repo, kw)) or [])
    _exit_of()
    assert len(seen) == 1
    entries, repo, kw = seen[0]
    assert repo == REPO
    assert entries == list(reconcile._stale_defects)
    assert len(entries) == 1 and entries[0].startswith(f"{EPIC}: a prose blocker")
    assert list(kw["unread"]) == []


def test_a_promote_only_run_makes_no_notice_call(board, notice_door, monkeypatch):
    settle = mock.MagicMock(return_value=[])
    monkeypatch.setattr(standing_defect_alert, "settle", settle)
    exit_text = _exit_of(promote_only=True)
    assert exit_text == ("reconcile: 0 write failure(s), 1 unfixed card defect(s) — "
                         "see ERROR lines above")
    assert notice_door == []
    settle.assert_not_called()


def test_a_settle_that_raises_changes_neither_exit(board, notice_door, monkeypatch, capsys):
    full_before = _exit_of()
    reconcile._stale_defects.clear()
    gate_before = _exit_of(promote_only=True)
    reconcile._stale_defects.clear()
    capsys.readouterr()

    monkeypatch.setattr(standing_defect_alert, "settle",
                        mock.MagicMock(side_effect=RuntimeError("the door exploded")))
    assert _exit_of() == full_before
    out = capsys.readouterr().out.splitlines()
    said = [line for line in out if line.startswith("standing-defect-alert: ")]
    assert len(said) == 1 and "the door exploded" in said[0]
    reconcile._stale_defects.clear()
    assert _exit_of(promote_only=True) == gate_before


def test_a_green_full_sweep_resolves_the_alert_it_raised(board, notice_door, monkeypatch):
    """The epic's sentence is fixed: the ledger is empty, the run is green,
    and the alert still open from the red pass is resolved."""
    monkeypatch.setattr(reconcile, "_fetch_epic_relations", mock.MagicMock(
        return_value={"identifier": EPIC, "description": "fixed",
                      "inverseRelations": {"nodes": []}}))
    monkeypatch.setattr(bureau_read, "open_alerts", lambda: {
        "schema": "bureau-read/1", "read_at": "2026-11-01T16:00:00Z",
        "alerts": [alert(f"pipeline_notice:standing-defect:{REPO}:{EPIC}")]})
    assert _exit_of() is None
    assert [(p["action"], p["notice"]) for p in notice_door
            if p["notice"].startswith("standing-defect:")] == [
        ("resolve", f"standing-defect:{REPO}:{EPIC}")]


# ── a pass that never evaluated the ledger resolves nothing ─────────────────


OPEN_EPIC = {"schema": "bureau-read/1", "read_at": "2026-11-01T16:00:00Z",
             "alerts": [alert(f"pipeline_notice:standing-defect:{REPO}:{EPIC}")]}


def _standing_sends(notice_door) -> list[tuple[str, str]]:
    return [(p["action"], p["notice"]) for p in notice_door
            if p["notice"].startswith("standing-defect:")]


def test_a_held_board_resolves_no_open_alert(board, notice_door, monkeypatch, capsys):
    """The read door says Linear is held: the promotion phase is skipped, the
    ledger is empty because nothing was asked, and the alert still open for
    the epic — whose defect still stands — is left open."""
    monkeypatch.setattr(reconcile, "backlog_children", mock.MagicMock(
        side_effect=reconcile.BoardHeld("linear-hold")))
    monkeypatch.setattr(bureau_read, "open_alerts", lambda: OPEN_EPIC)
    _exit_of()
    assert reconcile._stale_defects == []
    assert _standing_sends(notice_door) == []
    out = capsys.readouterr().out.splitlines()
    assert ("standing-defect-alert: ledger not evaluated — promote_ready "
            "(linear-hold); nothing resolved this pass") in out


def test_a_held_board_read_resolves_no_open_alert(board, notice_door, monkeypatch, capsys):
    """The hold found before promotion (`_door_hold`), not inside it."""
    def held(*a, **kw):
        reconcile._door_hold.append("linear-hold")
        raise reconcile.BoardHeld("linear-hold")
    monkeypatch.setattr(reconcile, "active_cards", mock.MagicMock(side_effect=held))
    monkeypatch.setattr(bureau_read, "open_alerts", lambda: OPEN_EPIC)
    _exit_of()
    assert _standing_sends(notice_door) == []
    out = capsys.readouterr().out
    assert "standing-defect-alert: ledger not evaluated — promote_ready" in out


def test_an_unreadable_defect_age_resolves_no_open_alert(board, notice_door, monkeypatch,
                                                         capsys):
    """The epic's prose defect is found but how long it has stood cannot be
    read: unknown is not fixed, so its alert stays open."""
    monkeypatch.setattr(reconcile.linear_ops, "first_comment_at", mock.MagicMock(
        side_effect=reconcile.linear_ops.LinearError("HTTP 502")))
    monkeypatch.setattr(bureau_read, "open_alerts", lambda: OPEN_EPIC)
    _exit_of()
    assert reconcile._stale_defects == []
    assert _standing_sends(notice_door) == []
    out = capsys.readouterr().out
    assert f"standing-defect-alert: ledger not evaluated — {EPIC}'s prose defect age" in out


def test_an_idle_pass_resolves_the_open_alert(board, notice_door, monkeypatch):
    """The decision: an idle pass skips promotion because nothing of this
    repo's is in motion or waiting to be promoted, so no gate of its can be
    standing — the empty ledger is the true one, and the alert resolves."""
    monkeypatch.setattr(reconcile, "sweep_idle", mock.MagicMock(
        return_value="no card of this repo is in motion or waiting to be promoted"))
    monkeypatch.setattr(bureau_read, "open_alerts", lambda: OPEN_EPIC)
    _exit_of()
    assert _standing_sends(notice_door) == [("resolve", f"standing-defect:{REPO}:{EPIC}")]


def test_a_green_full_sweep_hands_settle_no_unread_phase(board, notice_door, monkeypatch):
    monkeypatch.setattr(reconcile, "_fetch_epic_relations", mock.MagicMock(
        return_value={"identifier": EPIC, "description": "fixed",
                      "inverseRelations": {"nodes": []}}))
    seen: list = []
    monkeypatch.setattr(standing_defect_alert, "settle",
                        lambda entries, repo, **kw: seen.append(list(kw["unread"])) or [])
    _exit_of()
    assert seen == [[]]


@pytest.fixture
def limit_pass(monkeypatch):
    reconcile._ledger_unread.clear()
    reconcile._stale_defects.clear()
    reconcile._write_failures.clear()
    yield
    reconcile._ledger_unread.clear()
    reconcile._stale_defects.clear()
    reconcile._write_failures.clear()


def test_limit_recovery_held_is_recorded_unread(limit_pass, monkeypatch):
    monkeypatch.setattr(reconcile, "active_cards", mock.MagicMock(
        side_effect=reconcile.BoardHeld("linear-hold")))
    with pytest.raises(reconcile.BoardHeld):
        reconcile.recover_limit_deaths()
    assert reconcile._ledger_unread == ["recover_limit_deaths (linear-hold)"]


def test_limit_recovery_that_fails_partway_is_recorded_unread(limit_pass, monkeypatch):
    monkeypatch.setattr(reconcile, "active_cards", mock.MagicMock(return_value=[]))
    monkeypatch.setattr(reconcile.limit_recovery, "recover", mock.MagicMock(
        side_effect=RuntimeError("boom")))
    reconcile.recover_limit_deaths()
    assert reconcile._ledger_unread == ["recover_limit_deaths (RuntimeError)"]


def test_limit_recovery_that_runs_to_the_end_records_nothing(limit_pass, monkeypatch):
    monkeypatch.setattr(reconcile, "active_cards", mock.MagicMock(return_value=[]))
    monkeypatch.setattr(reconcile.limit_recovery, "recover", mock.MagicMock(return_value=[]))
    reconcile.recover_limit_deaths()
    assert reconcile._ledger_unread == []
