"""The proof dispatcher re-arms a record on its branch (DRE-8451).

DRE-8440 taught the dispatcher to read a waiting record off its branch when
no record pull request is open, but only a record whose blocking rows all
wait; any other was refused with `BRANCH_NOT_YET` on every pass. Once the
proof run stops opening a pull request for a record that cannot merge
(DRE-8441), a record waiting on a fix, an operator's note or a re-run lives
only on its branch, so it would never run again.

And the record carries its own hold. For such a record the run's
`proof_record_pr.decide` posts a `🔬 proof-waiting` whose observed half
opens `RECORD_HOLD_OPENING`, naming each row and what it waits on. Before
this card that hold blocked forever: `rerunning` read past only time, cause
and budget holds, the Hand-work lane read held a card on its newest open
hold, and the chase read it as an event hold.

Pinned here, through the dispatcher's real sweep, one card across passes
whose thread grows with every write. Every card's thread is seeded with the
hold `proof_record_pr.decide` really posts for its record, as its newest
open `🔬 proof-waiting`, with nothing after it:

  * a record naming `fix: DRE-9001`, in In Review and in Hand-work, is not
    dispatched while DRE-9001 is open, gets one re-armed run once it is
    Done, merged and live, and is not sent again for the same fix;
  * the real sequence: `decide` holds, then a pass dispatches the re-arm;
  * a new `🔬 proof-observed` after the newest receipt re-arms one run;
  * a record of plain `Not met.` rows re-run twice, then one fix card filed
    and held on, and no second one;
  * a waiting row beside a fix row held on its time, then one timed re-run;
  * a record hold is never an event hold, and no chase is spent on it;
  * a record that cannot be read is refused unread;
  * the pull-request path reads past the record hold once its record is read.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_dispatch_branch_rearm.py -v
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_record  # noqa: E402
import proof_record_pr  # noqa: E402
import proof_release  # noqa: E402
import reconcile  # noqa: E402
import spoken_thread  # noqa: E402
import test_proof_dispatch as base  # noqa: E402
from test_proof_dispatch import PERSON, REPO, SLUG, VIEWER, fake_voices, state  # noqa: E402
from test_proof_dispatch_branch_record import (  # noqa: E402
    AFTER, BEFORE, BRANCH, CARD, DESCRIPTION, FINISHED, FIXED, HEAD, LANES, PLAIN,
    STAMPED, UNSTAMPED, Card, _closed_pull, _iso, _Linear, _lines, pt, record_md,
)

FIX = "DRE-9001"
#: The defect fix card the fake filer answers with (DRE-7953).
FILED = "DRE-9100"
BRANCH_RERUN = f"re-run of the record on its branch at {HEAD[:7]}"
REARMED = f"re-run after {FIX} shipped"


def fix_card(state_name: str) -> dict:
    return {"identifier": FIX, "title": "bureau-pipeline: render the badge",
            "state": {"name": state_name},
            "labels": {"nodes": [{"name": f"repo:{SLUG}"}, {"name": "agent:engineer"}]}}


class Rearm(Card):
    """`Card` of DRE-8440, with the reads a re-arm makes: the fix card, its
    pull request and its release, and the defect filer of DRE-7953."""

    def __init__(self, monkeypatch, lane: str, text, **kw):
        self.fixes = {FIX: fix_card("In Progress")}
        self.merged: dict = {}
        self.live_fix = "ready"
        self.filed: list = []
        if lane == "In Review":
            kw.setdefault("pulled", _closed_pull())
        super().__init__(monkeypatch, lane, text, **kw)

    def ship(self, *, live: str = "ready") -> None:
        self.fixes[FIX] = fix_card("Done")
        self.merged[FIX] = {"number": 977, "state": "MERGED",
                            "headRefName": f"agent/{FIX}-badge",
                            "mergeCommit": {"oid": "f00d" + "0" * 36},
                            "mergedAt": _iso(pt(20, 18, 0))}
        self.live_fix = live

    def find_pr(self, ident, repo=None):
        return self.merged.get(ident)

    def release(self, repo, merges, *, read):
        return proof_release.Reading(self.live_fix, [f"{self.live_fix} — fixture"])

    def file_fix(self, proof_card, epic, record_pr_url, gaps, *, repo_slug, kind):
        self.filed.append((proof_card, epic, record_pr_url, list(gaps), repo_slug, kind))
        return {"identifier": FILED, "url": f"https://linear.app/x/issue/{FILED}/y"}

    def sweep(self, when, *, live=True, heads=None):
        self.now = when
        return proof_dispatch.sweep(
            REPO, SLUG, live=live, linear=_Fixes(self), read=self.github.api,
            find_pr=self.find_pr,
            run_state=lambda *a, **k: state(self.run, dispatches=1),
            release=self.release, fire=self.fire, voices=fake_voices, now=when,
            find_record=self.find_record,
            read_record=lambda pr, head: proof_record.Record(None, None, "no pull request"),
            open_heads=lambda: dict(heads or {}), file_fix=self.file_fix)


class _Fixes(_Linear):
    def fix_card(self, ident):
        self.of.reads.append(("fix_card", ident))
        return self.of.fixes[ident]


def _record_hold(card: Card, text: str) -> str:
    """The card's newest open hold, proved to be the one `decide` composes
    for `text` and posts, with nothing after it."""
    blocking = proof_record_pr.can_merge(text).blocking
    line = linear_ops.proof_waiting_line(*proof_record_pr.hold_line(blocking, FINISHED))
    voices = fake_voices(card.thread, VIEWER, card=CARD)
    assert proof_record.open_holds(voices)[-1] == line
    assert card.bodies()[-1] == line
    assert proof_record_pr.is_record_hold(line), line
    return line


def _held_by(lines: list, line: str) -> bool:
    return any(f"held by {line}" in said for said in lines)


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


def test_branch_not_yet_is_gone_and_the_branch_re_run_is_named():
    assert not hasattr(proof_dispatch, "BRANCH_NOT_YET")
    source = (ROOT / "scripts" / "proof_dispatch.py").read_text(encoding="utf-8")
    assert "BRANCH_NOT_YET" not in source
    assert proof_dispatch.BRANCH_RERUN_REASON == "re-run of the record on its branch at {sha7}"


def test_past_record_hold_drops_every_record_hold_and_keeps_the_rest():
    held = proof_record_pr.hold_line(proof_record_pr.can_merge(record_md(FIXED)).blocking,
                                     FINISHED)
    record = linear_ops.proof_waiting_line(*held)
    timed = linear_ops.proof_waiting_line("the report", "not-before=2026-10-21T06:30-07:00")
    budget = linear_ops.proof_waiting_line(proof_dispatch.GATE_EXHAUSTED_OBSERVED,
                                           f"{FILED} shipped")
    assert proof_dispatch._past_record_hold([record, timed, budget, record]) == [timed, budget]
    assert proof_dispatch._past_record_hold([timed]) == [timed]


# --------------------------------------------------------------------------- #
# a fix named on the branch: waits for it, then one re-armed run               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", LANES)
def test_a_branch_record_naming_a_fix_waits_for_it_then_gets_one_re_armed_run(
        monkeypatch, capsys, lane):
    card = Rearm(monkeypatch, lane, record_md(FIXED))
    held = _record_hold(card, record_md(FIXED))
    assert held.endswith(f"— needs {FIX} shipped")
    if lane == "In Review":
        assert card.decided == proof_record_pr.CLOSED_AND_HELD
        assert card.github.pulls[0]["state"] == "CLOSED"
    else:
        assert card.decided == proof_record_pr.HELD
    count = len(card.thread)

    # DRE-9001 open: read, never held on the record hold, nothing dispatched
    # and nothing written.
    for when in (BEFORE, AFTER):
        tally = card.sweep(when)
        lines = _lines(capsys)
        assert card.fired == [] and len(card.thread) == count, card.bodies()[count:]
        assert tally.held == 1, lines
        assert not _held_by(lines, held), lines
        assert not any("condition 4" in line for line in lines), lines
        assert any(f"the fix {FIX} is In Progress, not Done" in line for line in lines), lines
        assert ("fix_card", FIX) in card.reads

    # Done, merged, but its release not live: still nothing.
    card.ship(live="waiting")
    card.sweep(AFTER + timedelta(minutes=15))
    lines = _lines(capsys)
    assert card.fired == [], lines
    assert any("is waiting" in line for line in lines), lines

    # Done, merged and live: one re-armed run.
    card.ship()
    tally = card.sweep(AFTER + timedelta(minutes=30))
    lines = _lines(capsys)
    assert card.fired == [(CARD, REARMED)], lines
    newest = card.receipts()[-1]
    assert (newest.reason, newest.count) == (REARMED, "re-armed")
    assert f"{REARMED} (re-armed)" in card.bodies()[-1]
    assert tally.dispatched == 1

    # The run finishes and the record still names the fix: never again.
    for minutes in (45, 60, 75):
        card.sweep(AFTER + timedelta(minutes=minutes))
        lines = _lines(capsys)
        assert card.fired == [(CARD, REARMED)], lines
        assert any("already re-armed" in line for line in lines), lines


def test_the_hand_work_lane_read_leaves_a_record_hold_to_the_thread_read(monkeypatch):
    card = Rearm(monkeypatch, "Hand-work", record_md(FIXED))
    _record_hold(card, record_md(FIXED))
    lane = card.lane_read("Hand-work")[0]
    one = proof_dispatch._Pass(
        REPO, SLUG, live=True, linear=_Fixes(card), read=card.github.api,
        find_pr=card.find_pr, run_state=lambda *a, **k: state("finished"),
        release=card.release, fire=card.fire, voices=fake_voices, now=AFTER,
        find_record=card.find_record, read_record=lambda pr, head: None)
    assert one.held_on_the_lane(lane) is None


def test_the_real_sequence_decide_holds_and_a_pass_dispatches_the_re_arm(
        monkeypatch, capsys):
    # The run ends with no hold of its own; `decide` is called here, as the
    # proof run calls it, over the same fake GitHub and the same thread.
    card = Rearm(monkeypatch, "Hand-work", record_md(FIXED), hold=False)
    assert card.holds() == []
    word = proof_record_pr.decide(
        REPO, CARD, f"https://linear.app/x/issue/{CARD}", "PROOF: the Sunday report",
        gh=card.github.cli, comment=card._hold, now=FINISHED,
        voices=lambda ident: fake_voices(card.thread, VIEWER, card=ident),
        description=DESCRIPTION)
    assert word == proof_record_pr.HELD
    held = _record_hold(card, record_md(FIXED))
    assert card.holds() == [held]

    card.sweep(BEFORE)
    assert card.fired == []
    card.ship()
    tally = card.sweep(AFTER)
    lines = _lines(capsys)
    assert card.fired == [(CARD, REARMED)], lines
    assert tally.dispatched == 1


# --------------------------------------------------------------------------- #
# a new 🔬 proof-observed: one re-armed run                                     #
# --------------------------------------------------------------------------- #


def test_a_new_proof_observed_after_the_newest_receipt_re_arms_one_run(
        monkeypatch, capsys):
    card = Rearm(monkeypatch, "In Review", record_md(FIXED))
    _record_hold(card, record_md(FIXED))
    card.sweep(BEFORE)
    assert card.fired == []

    noted = pt(21, 6, 0)
    card.thread.append({"body": "🔬 proof-observed: the badge renders on the staging card",
                        "createdAt": _iso(noted), "user": {"id": PERSON}})
    tally = card.sweep(AFTER)
    lines = _lines(capsys)
    reason = proof_dispatch.REARM_NOTE_REASON.format(
        at=spoken_thread.pacific_label(_iso(noted)))
    assert card.fired == [(CARD, reason)], lines
    assert card.receipts()[-1].count == "re-armed"
    assert tally.dispatched == 1

    # The note is older than the run it re-armed: no second one.
    card.sweep(AFTER + timedelta(minutes=30))
    lines = _lines(capsys)
    assert card.fired == [(CARD, reason)], lines


# --------------------------------------------------------------------------- #
# plain Not met. rows: two re-runs, then one fix card                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", LANES)
def test_plain_rows_on_the_branch_are_re_run_twice_then_one_fix_card_is_filed(
        monkeypatch, capsys, lane):
    card = Rearm(monkeypatch, lane, record_md(PLAIN))
    held = _record_hold(card, record_md(PLAIN))
    assert held.endswith("— needs a re-run")

    for n in (1, 2):
        tally = card.sweep(AFTER + timedelta(hours=n - 1))
        lines = _lines(capsys)
        assert card.fired[-1:] == [(CARD, BRANCH_RERUN)], lines
        assert len(card.fired) == n
        newest = card.receipts()[-1]
        assert (newest.reason, newest.count) == (BRANCH_RERUN, f"re-run {n} of 2")
        assert tally.dispatched == 1

    # The budget spent: one fix card, kind defect, and the hold on it.
    tally = card.sweep(AFTER + timedelta(hours=2))
    lines = _lines(capsys)
    assert len(card.fired) == 2, lines
    assert len(card.filed) == 1, lines
    proof, _, url, rows, slug, kind = card.filed[0]
    assert (proof, slug, kind) == (CARD, SLUG, "defect")
    assert rows == [("The receipt is posted once", "defect")]
    assert BRANCH in url
    assert card.holds()[-1] == linear_ops.proof_waiting_line(
        proof_dispatch.GATE_EXHAUSTED_OBSERVED, f"{FILED} shipped")
    assert card.holds()[-1].endswith(f"— needs {FILED} shipped")
    assert tally.held == 1
    assert any(f"filed {FILED}" in line for line in lines), lines

    # Later passes file nothing more and dispatch nothing.
    count = len(card.thread)
    for hours in (3, 4):
        card.sweep(AFTER + timedelta(hours=hours))
        lines = _lines(capsys)
        assert len(card.filed) == 1 and len(card.fired) == 2, lines
        assert len(card.thread) == count, card.bodies()[count:]


# --------------------------------------------------------------------------- #
# a waiting row beside a fix row: held on its time, then one timed re-run      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", LANES)
def test_a_waiting_row_beside_a_fix_row_is_held_on_its_time_never_on_the_record_hold(
        monkeypatch, capsys, lane):
    text = record_md(STAMPED, FIXED)
    card = Rearm(monkeypatch, lane, text)
    held = _record_hold(card, text)
    count = len(card.thread)

    tally = card.sweep(BEFORE)
    lines = _lines(capsys)
    assert card.fired == [], lines
    assert tally.held == 1, lines
    assert not _held_by(lines, held), lines
    (timed,) = card.bodies()[count:]
    assert proof_record.time_only(timed) == pt(21, 6, 30)
    assert any("lifts at 2026-10-21 06:30 PT" in line for line in lines), lines

    tally = card.sweep(BEFORE + timedelta(minutes=30))
    lines = _lines(capsys)
    assert card.fired == [] and len(card.thread) == count + 1, lines
    assert tally.held == 1
    assert _held_by(lines, timed) and not _held_by(lines, held), lines

    tally = card.sweep(AFTER)
    lines = _lines(capsys)
    reason = f"timed re-run after not-before 2026-10-21 06:30 PT at {HEAD[:7]}"
    assert card.fired == [(CARD, reason)], lines
    (lift,) = card.lifts()
    assert proof_dispatch.LIFTED_BY in lift
    assert card.receipts()[-1].count == "timed re-run 1 of 2"
    assert tally.dispatched == 1


# --------------------------------------------------------------------------- #
# no chase on a record hold                                                    #
# --------------------------------------------------------------------------- #


def test_a_record_hold_is_never_an_event_hold():
    for text in (record_md(PLAIN), record_md(FIXED), record_md(UNSTAMPED, PLAIN),
                 record_md(STAMPED, FIXED)):
        blocking = proof_record_pr.can_merge(text).blocking
        line = linear_ops.proof_waiting_line(*proof_record_pr.hold_line(blocking, FINISHED))
        assert proof_record_pr.is_record_hold(line), line
        assert not proof_dispatch._is_event_hold(line), line


def test_no_chase_is_dispatched_on_a_record_hold_however_long_it_stands(
        monkeypatch, capsys):
    card = Rearm(monkeypatch, "Hand-work", record_md(PLAIN))
    _record_hold(card, record_md(PLAIN))
    # The run is no longer finished, so the record is not read: ten days on,
    # the record hold still stands and nothing chases it.
    card.run = "dead"
    for day in (22, 25, 30):
        card.sweep(pt(day, 9, 0))
        lines = _lines(capsys)
        assert card.fired == [], lines
        assert not any("chase" in line for line in lines), lines


# --------------------------------------------------------------------------- #
# a record not read: refused unread                                            #
# --------------------------------------------------------------------------- #


def test_a_record_that_cannot_be_read_is_refused_unread_in_either_lane(
        monkeypatch, capsys):
    for lane in LANES:
        card = Rearm(monkeypatch, lane, record_md(FIXED))
        held = _record_hold(card, record_md(FIXED))
        card.ship()
        card.github.fail = reconcile.ReconcileReadError("gh api failed rc=1: HTTP 502")
        count = len(card.thread)
        tally = card.sweep(AFTER)
        lines = _lines(capsys)
        assert card.fired == [] and tally.dispatched == 0, (lane, lines)
        assert len(card.thread) == count, (lane, card.bodies()[count:])
        assert card.github.reads, lane
        assert any(BRANCH in line and "could not be read" in line for line in lines), (
            lane, lines)
        assert not _held_by(lines, held), (lane, lines)


# --------------------------------------------------------------------------- #
# the pull-request path reads past it too                                      #
# --------------------------------------------------------------------------- #


def test_the_pull_request_path_reads_past_the_record_hold_once_the_record_is_read(
        monkeypatch, capsys):
    text = base.record_md(base.MET_ROW, base.NOT_MET_ROW)
    blocking = proof_record_pr.can_merge(text).blocking
    held = base.comment(linear_ops.proof_waiting_line(
        *proof_record_pr.hold_line(blocking, base.NOW - timedelta(minutes=80))), 80)
    assert proof_record_pr.is_record_hold(held["body"])
    h, tally, _ = base._gate_pass(monkeypatch, thread=base._sent_back(held),
                                  pr=base._declined(), text=text)
    lines = _lines(capsys)
    assert [f[2] for f in h.fired] == [base.GATE_RERUN], lines
    assert tally.dispatched == 1
