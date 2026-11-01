"""A hold that waits on other cards lifts itself once they are Done (DRE-8593).

A `🔬 proof-waiting` hold whose needs half opens with card ids — `— needs
DRE-<n>…` — was an ordinary event hold to the proof dispatcher: nothing read
the named card, so the hold stood after that card closed, and a time hold
beside it could never lift either. DRE-5357 (agent-bureau, Hand-work) is that
case, read on 2026-11-01: its hold of 2026-10-17 waits on DRE-5407's record,
DRE-5407 has been Done since 2026-10-27, and its time hold of 2026-11-01
(`not-before=2026-11-01T20:21+00:00`) cannot lift while the first stands.

Pinned here, replayed off DRE-5357's own hold lines and times:

  * the reader: the card ids a card hold's needs half opens with, and none
    for every hold the dispatcher already reads another way;
  * the lift: both holds met, one `🔬 proof-observed` lifts them, naming the
    time and `DRE-5407 Done`, and the card goes on to its dispatch;
  * the four holds that still hold — before the time, and DRE-5407 In
    Progress, Canceled, Duplicate or unread — and a hold naming more cards
    than `REARM_FIX_CARDS`;
  * the lane read: a card hold whose cards are all Done is left to the thread
    read, every card hold in the lane costs one request together, and a
    failed request names nothing held on that account;
  * a card hold is no event hold, so it is never chased;
  * a card-hold lift re-arms nothing, and the dry run writes nothing;
  * every hold kind that stays with a person still holds when a card it
    mentions is Done.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_record  # noqa: E402
import proof_run_state  # noqa: E402
from test_proof_dispatch import (  # noqa: E402
    GATE_FILED,
    MET_ROW,
    NOT_MET_ROW,
    PERSON,
    REPO,
    SPENT,
    VIEWER,
    Board,
    Harness,
    _about,
    _lines,
    _rearm_pass,
    _sent_back,
    fake_voices,
    lane_card,
    observed_note,
    promoted,
    record_md,
)

CARD = "DRE-5357"
WAITED_ON = "DRE-5407"
#: DRE-5357's hold of 2026-10-17 13:48 PT, verbatim.
CARD_HOLD = ("🔬 proof-waiting: observation 8 (the first weekly report is one card in "
             "Green Light, with its measured cost recorded) — needs DRE-5407's record "
             "to merge, which waits on the report of Sunday 2026-10-18 (about 05:30 PT) "
             "and the operator's reading on draft PR #3173; then this record cites it "
             "on draft PR #3876.")
CLAUSE = "not-before=2026-11-01T20:21+00:00"
#: DRE-5357's time-only hold of 2026-11-01 01:21 PT, verbatim (its first line).
TIME_HOLD = ("🔬 proof-waiting: All eight observations are recorded by hand, by the "
             f"operator, in agen… — needs {CLAUSE}")
#: The note posted one second before the hold, and the dashed note after it
#: that discharged nothing, with the dispatcher's own reply — first lines.
OBSERVED_BEFORE = ("🔬 proof-observed: second pass read by hand against production on "
                   "2026-10-17, 13:30–13:45 PT, and written into "
                   "architecture/proofs/agent-working-logs.md on draft PR #3876. The "
                   "first pass is left as written.")
DASHED = ("🔬 proof-observed (operator session, 10-17 3:25 PM PT): `foundry` now has "
          "its agent-log upload role. The CEO said go at about 3:20 PM PT.")
UNREAD_REPLY = ("🔬 proof-note-unread: the note of 2026-10-17 15:24 PT “🔬 proof-observed "
                "(operator session, 10-17 3:25 PM PT): `foundry` now has its agent-log "
                "upload role. The CEO said go at about 3:20 PM PT.” discharged nothing — "
                "a note is read only when it opens “🔬 proof-observed:” with the colon, "
                "and a dash or anything else in its place is not read as one. Post it "
                "again opening “🔬 proof-observed:” to discharge the hold.")
#: 12:30 PT on 2026-11-01, nine minutes after the time hold's time; and 11:30.
AFTER = datetime(2026, 11, 1, 20, 30, tzinfo=timezone.utc)
BEFORE = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)


def at(body: str, stamp: str, user: str | None = VIEWER) -> dict:
    """A comment posted at an exact UTC time."""
    return {"body": body, "createdAt": stamp, "user": {"id": user} if user else None}


def dre_5357_thread(*extra) -> list:
    """DRE-5357's proof thread from the hold of 2026-10-17 on."""
    return [promoted_at("2026-10-12T16:00:00.000Z"),
            at(OBSERVED_BEFORE, "2026-10-17T20:48:22.328Z", PERSON),
            at(CARD_HOLD, "2026-10-17T20:48:23.105Z", PERSON),
            at(DASHED, "2026-10-17T22:24:24.284Z", PERSON),
            at(UNREAD_REPLY, "2026-10-23T08:55:47.091Z"),
            at(TIME_HOLD, "2026-11-01T08:21:32.319Z"),
            *extra]


def promoted_at(stamp: str) -> dict:
    return {**promoted(0), "createdAt": stamp}


class CardBoard(Board):
    """The fixture board, with the named cards' states read in one request
    each time, counted and failing on demand."""

    def __init__(self, *args, card_states=None, fail=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.card_states = dict(card_states or {})
        self.fail = fail

    def states(self, identifiers):
        self.reads.append(("states", tuple(identifiers)))
        if self.fail is not None:
            raise self.fail
        return {i: self.card_states[i] for i in identifiers if i in self.card_states}


def _pass(monkeypatch, *, waited_on="Done", now=AFTER, live=True, shown=True,
          thread=None, fail=None, extra_states=None):
    thread = dre_5357_thread() if thread is None else thread
    card = lane_card(CARD, window=thread if shown else [thread[0]])
    states = {WAITED_ON: waited_on, **(extra_states or {})}
    board = CardBoard(hand=[card], threads={CARD: thread}, card_states=states, fail=fail)
    h = Harness(monkeypatch, board)
    tally = h.sweep(live=live, now=now)
    return h, tally


def _lifts(h) -> list:
    return [body for _, body in h.posted if body.startswith(proof_record.OBSERVED_MARK)]


# --------------------------------------------------------------------------- #
# the reader                                                                    #
# --------------------------------------------------------------------------- #


def _line(needs: str, observed: str = "the record merges") -> str:
    return linear_ops.proof_waiting_line(observed, needs)


def test_the_reader_returns_the_card_dre_5357s_hold_waits_on():
    assert proof_dispatch._card_hold(CARD_HOLD) == [WAITED_ON]


def test_the_reader_returns_every_card_the_needs_half_opens_with():
    assert proof_dispatch._card_hold(_line("DRE-1, DRE-2 and DRE-3 to close")) == [
        "DRE-1", "DRE-2", "DRE-3"]


NOT_A_CARD_HOLD = {
    "fix-shipped": _line("DRE-9 shipped"),
    "time-only": TIME_HOLD,
    "ceo-press": _line("DRE-9 to close and the CEO's press on Approve"),
    "cause": _line("the operator's decision on DRE-9's rows: accept, reword or drop"),
    "record": _line("DRE-9 shipped",
                    observed=f"{proof_dispatch.proof_record_pr.RECORD_HOLD_OPENING} "
                             "“the tag moves” waits on DRE-9 shipped"),
    "own-needs": _line(proof_dispatch.EXHAUSTED_NEEDS,
                       observed=proof_dispatch.EXHAUSTED_OBSERVED),
    "id-not-first": _line("the release DRE-4 cuts"),
    "no-needs": "🔬 proof-waiting: DRE-4 is the record",
    "not-a-hold": "DRE-4 — needs DRE-5 to close",
}


@pytest.mark.parametrize("case", sorted(NOT_A_CARD_HOLD))
def test_the_reader_returns_nothing_for_a_hold_read_another_way(case):
    assert proof_dispatch._card_hold(NOT_A_CARD_HOLD[case]) == []


def test_the_record_hold_fixture_is_the_records_own_hold():
    assert proof_dispatch.proof_record_pr.is_record_hold(NOT_A_CARD_HOLD["record"])
    assert proof_dispatch.proof_cause_park.is_cause_hold(NOT_A_CARD_HOLD["cause"])


# --------------------------------------------------------------------------- #
# the lift                                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("shown", [True, False], ids=["window-shows-it", "beyond-the-window"])
def test_dre_5357_both_holds_met_lift_with_one_note_and_the_card_goes_on(
        monkeypatch, capsys, shown):
    h, tally = _pass(monkeypatch, shown=shown)
    lines = _about(_lines(capsys), CARD)
    (note,) = _lifts(h)
    assert proof_dispatch.LIFTED_BY in note
    assert f"{CLAUSE} has passed" in note
    assert f"{WAITED_ON} Done" in note
    assert "read at 2026-11-01 12:30 PT" in note
    # The card goes on to the next condition, and is dispatched.
    assert h.fired == [(CARD, REPO, "first proof run", "proof-execute")], lines
    assert tally.dispatched == 1 and tally.held == 0
    assert [b for _, b in h.posted if not b.startswith(proof_record.OBSERVED_MARK)
            and proof_run_state.receipt(b) is None] == [], h.posted
    # The note discharges both holds.
    after = dre_5357_thread(at(note, "2026-11-01T20:30:01.000Z"))
    assert proof_record.open_holds(fake_voices(after, VIEWER, card=CARD)) == []


def test_the_dry_run_says_it_would_lift_and_posts_nothing(monkeypatch, capsys):
    h, _ = _pass(monkeypatch, live=False)
    lines = _lines(capsys)
    assert h.posted == [] and h.fired == [] and h.holds == []
    assert any(line.startswith(f"would: lift {CARD} — ") and f"{WAITED_ON} Done" in line
               and f"{CLAUSE} has passed" in line for line in lines), lines


def test_a_card_hold_alone_whose_cards_are_done_lifts_by_itself(monkeypatch, capsys):
    thread = [promoted_at("2026-10-12T16:00:00.000Z"),
              at(_line("DRE-1, DRE-2 and DRE-3 to close"), "2026-11-01T18:00:00.000Z")]
    h, _ = _pass(monkeypatch, thread=thread,
                 extra_states={"DRE-1": "Done", "DRE-2": "Done", "DRE-3": "Done"})
    (note,) = _lifts(h)
    assert "DRE-1 Done, DRE-2 Done, DRE-3 Done" in note
    assert "not-before=" not in note
    assert [f[0] for f in h.fired] == [CARD]


# --------------------------------------------------------------------------- #
# what still holds                                                              #
# --------------------------------------------------------------------------- #


def _held_line(lines) -> str:
    named = [line for line in lines if "condition 4 (hold): held by " in line]
    assert len(named) == 1, lines
    return named[0]


@pytest.mark.parametrize("shown", [True, False], ids=["early-check", "full-check"])
def test_before_its_time_the_time_hold_is_named_with_when_it_lifts(
        monkeypatch, capsys, shown):
    h, tally = _pass(monkeypatch, now=BEFORE, shown=shown)
    line = _held_line(_about(_lines(capsys), CARD))
    assert h.posted == [] and h.fired == []
    assert tally.held == 1
    assert TIME_HOLD in line and line.endswith("— lifts at 2026-11-01 12:21 PT"), line


@pytest.mark.parametrize("waited_on", ["In Progress", "Canceled", "Duplicate"])
def test_a_card_not_done_holds_and_the_line_names_it_and_its_state(
        monkeypatch, capsys, waited_on):
    h, tally = _pass(monkeypatch, waited_on=waited_on)
    line = _held_line(_about(_lines(capsys), CARD))
    assert h.posted == [] and h.fired == []
    assert tally.held == 1
    assert CARD_HOLD in line and f"{WAITED_ON} is {waited_on}" in line, line


def test_a_card_read_that_raises_holds_and_says_the_card_could_not_be_read(
        monkeypatch, capsys):
    h, tally = _pass(monkeypatch, fail=linear_ops.LinearError("HTTP 500 from Linear"))
    line = _held_line(_about(_lines(capsys), CARD))
    assert h.posted == [] and h.fired == []
    assert tally.held == 1
    assert CARD_HOLD in line and f"{WAITED_ON} could not be read" in line, line
    assert "HTTP 500" in line


def test_a_card_linear_does_not_answer_for_is_not_read_as_done(monkeypatch, capsys):
    thread = [promoted_at("2026-10-12T16:00:00.000Z"),
              at(_line("DRE-77 to close"), "2026-11-01T18:00:00.000Z")]
    h, _ = _pass(monkeypatch, thread=thread)
    line = _held_line(_about(_lines(capsys), CARD))
    assert h.posted == [] and h.fired == []
    assert "DRE-77 could not be read" in line, line


@pytest.mark.parametrize("shown", [True, False], ids=["early-check", "full-check"])
def test_a_card_hold_naming_more_cards_than_the_cap_is_not_read(monkeypatch, capsys, shown):
    many = proof_dispatch.REARM_FIX_CARDS + 1
    ids = [f"DRE-{i}" for i in range(1, many + 1)]
    needs = f"{', '.join(ids[:-1])} and {ids[-1]} to close"
    thread = [promoted_at("2026-10-12T16:00:00.000Z"),
              at(_line(needs), "2026-11-01T18:00:00.000Z")]
    h, tally = _pass(monkeypatch, thread=thread, shown=shown,
                     extra_states={i: "Done" for i in ids})
    line = _held_line(_about(_lines(capsys), CARD))
    assert h.posted == [] and h.fired == []
    assert tally.held == 1
    assert f"names {many} cards" in line, line
    assert not any(read[0] == "states" for read in h.board.reads)


#: The holds that stay with a person, each mentioning DRE-5407, Done.
A_PERSONS = {
    "free-text": ("the nightly on main", f"the nightly after {WAITED_ON} merges"),
    "ceo-press": ("the CEO signs in to the console",
                  f"{WAITED_ON} to close and the CEO's press: sign in once"),
    "cause": ("“the nightly names v3” reads “Not observed. cause: …”",
              f"the operator's decision on {WAITED_ON}'s rows: accept, reword or drop"),
    "budget": (proof_dispatch.EXHAUSTED_OBSERVED, proof_dispatch.EXHAUSTED_NEEDS),
}


@pytest.mark.parametrize("kind", sorted(A_PERSONS))
def test_a_hold_that_stays_with_a_person_still_holds_when_a_card_it_names_is_done(
        monkeypatch, capsys, kind):
    observed, needs = A_PERSONS[kind]
    body = linear_ops.proof_waiting_line(observed, needs)
    if kind == "budget":
        body = body.replace(" — needs ", f" ({WAITED_ON} Done) — needs ")
    thread = [promoted_at("2026-10-12T16:00:00.000Z"),
              at(body, "2026-11-01T18:00:00.000Z")]
    h, tally = _pass(monkeypatch, thread=thread)
    lines = _about(_lines(capsys), CARD)
    assert h.posted == [] and h.fired == [], lines
    assert tally.held == 1
    assert body.split("\n", 1)[0] in _held_line(lines)


# --------------------------------------------------------------------------- #
# the lane read                                                                 #
# --------------------------------------------------------------------------- #


def _card_hold_card(ident: str, waits_on: str) -> dict:
    thread = [promoted_at("2026-10-12T16:00:00.000Z"),
              at(_line(f"{waits_on} to close"), "2026-11-01T18:00:00.000Z")]
    return lane_card(ident, window=thread)


def test_two_card_holds_in_one_lane_cost_one_request_and_neither_is_named_held(
        monkeypatch, capsys):
    cards = [_card_hold_card("DRE-6101", "DRE-7001"), _card_hold_card("DRE-6102", "DRE-7002")]
    board = CardBoard(hand=cards, card_states={"DRE-7001": "Done", "DRE-7002": "Done"},
                      threads={c["identifier"]: list(reversed(c["comments"]["nodes"]))
                               for c in cards})
    h = Harness(monkeypatch, board)
    h.sweep(now=AFTER)
    lines = _lines(capsys)
    first_card_read = next(i for i, r in enumerate(board.reads) if r[0] == "card")
    lane_reads = [r for r in board.reads[:first_card_read] if r[0] == "states"]
    assert len(lane_reads) == 1, board.reads
    assert set(lane_reads[0][1]) == {"DRE-7001", "DRE-7002"}
    # Neither is named held off the lane read: the thread read lifts it.
    assert not any("condition 4 (hold)" in line for line in lines), lines
    assert len(h.fired) == 1


def test_the_lane_read_names_a_card_hold_not_met_held_at_no_thread_read(
        monkeypatch, capsys):
    cards = [_card_hold_card("DRE-6101", "DRE-7001")]
    board = CardBoard(hand=cards, card_states={"DRE-7001": "In Progress"})
    h = Harness(monkeypatch, board)
    tally = h.sweep(now=AFTER)
    line = _held_line(_about(_lines(capsys), "DRE-6101"))
    assert "DRE-7001 is In Progress" in line, line
    assert tally.held == 1 and h.fired == []
    assert ("thread", "DRE-6101") not in board.reads
    assert [r for r in board.reads if r[0] == "states"] == [("states", ("DRE-7001",))]


def test_a_lane_with_no_card_hold_makes_no_request(monkeypatch, capsys):
    board = CardBoard(hand=[lane_card("DRE-6101")])
    h = Harness(monkeypatch, board)
    h.sweep(now=AFTER)
    assert not any(r[0] == "states" for r in board.reads)


def test_a_failed_lane_request_names_nothing_held_on_that_account(monkeypatch, capsys):
    cards = [_card_hold_card("DRE-6101", "DRE-7001")]
    board = CardBoard(hand=cards, threads={"DRE-6101": list(reversed(
        cards[0]["comments"]["nodes"]))}, fail=linear_ops.LinearError("HTTP 502"))
    h = Harness(monkeypatch, board)
    h.sweep(now=AFTER)
    lines = _about(_lines(capsys), "DRE-6101")
    # The lane read leaves it to the thread read, which holds it as unread.
    assert ("thread", "DRE-6101") in board.reads
    line = _held_line(lines)
    assert "DRE-7001 could not be read" in line, line
    assert h.posted == [] and h.fired == []


def test_the_production_read_asks_for_every_card_in_one_request(monkeypatch):
    calls = []

    def gql(query, variables=None):
        calls.append((query, variables))
        return {"issues": {"nodes": [
            {"identifier": "DRE-5407", "state": {"name": "Done"}},
            {"identifier": "DRE-7002", "state": {"name": "In Progress"}}]}}

    monkeypatch.setattr(linear_ops, "gql", gql)
    got = proof_dispatch.LinearReads().states(["DRE-5407", "DRE-7002", "DRE-9"])
    assert got == {"DRE-5407": "Done", "DRE-7002": "In Progress"}
    assert len(calls) == 1
    query, variables = calls[0]
    assert query == proof_dispatch.CARD_STATES_QUERY
    assert variables == {"numbers": [5407, 7002, 9]}


# --------------------------------------------------------------------------- #
# no chase, no re-arm                                                           #
# --------------------------------------------------------------------------- #


def test_a_card_hold_is_not_an_event_hold():
    assert proof_dispatch._is_event_hold(CARD_HOLD) is False
    assert proof_dispatch._is_event_hold(_line("the release DRE-4 cuts")) is True


def test_a_card_hold_long_past_its_chase_is_never_chased(monkeypatch, capsys):
    thread = [promoted_at("2026-10-12T16:00:00.000Z"),
              at(CARD_HOLD, "2026-10-17T20:48:23.105Z", PERSON)]
    h, tally = _pass(monkeypatch, thread=thread, waited_on="In Progress")
    lines = _about(_lines(capsys), CARD)
    assert h.fired == [] and h.posted == [], lines
    assert not any("chase:" in line for line in lines), lines
    assert tally.held == 1


def test_a_card_hold_lift_re_arms_nothing(monkeypatch, capsys):
    # A lift of the card hold alone: a lift quoting a time is read as owing a
    # timed re-run (`_lifted_at`, DRE-7316), which is the time lift's rule.
    thread = [promoted_at("2026-10-12T16:00:00.000Z"),
              at(CARD_HOLD, "2026-10-17T20:48:23.105Z", PERSON)]
    h, _ = _pass(monkeypatch, thread=thread)
    (note,) = _lifts(h)
    assert f"{WAITED_ON} Done" in note and "not-before=" not in note
    text = note[len(proof_record.OBSERVED_MARK):].strip()
    capsys.readouterr()

    lifted = observed_note(300, text, user=VIEWER)
    h, _ = _rearm_pass(monkeypatch, thread=_sent_back(*SPENT, lifted),
                       text=record_md(MET_ROW, NOT_MET_ROW))
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.posted == []
    assert h.holds == [("DRE-5930", *GATE_FILED)]
    assert any(proof_dispatch.NEVER_ANOTHER in line for line in lines), lines

    # The same words without the lift's mark are a person's note: one re-arm.
    plain = observed_note(300, text.replace(proof_dispatch.LIFTED_BY, "read by hand"),
                          user=VIEWER)
    h, _ = _rearm_pass(monkeypatch, thread=_sent_back(*SPENT, plain),
                       text=record_md(MET_ROW, NOT_MET_ROW))
    assert len(h.fired) == 1 and h.fired[0][2].startswith(
        "re-run after the 🔬 proof-observed of ")


# --------------------------------------------------------------------------- #
# the documents                                                                 #
# --------------------------------------------------------------------------- #


def test_the_docstring_and_the_hold_deadlines_page_say_a_card_hold_lifts_itself():
    doc = " ".join((proof_dispatch.__doc__ or "").split()).lower()
    assert "card hold" in doc and "every card it names is done" in doc
    assert "only the ids the needs half opens with are read" in doc
    page = " ".join((ROOT / "docs" / "hold-deadlines.md").read_text().split()).lower()
    assert "card hold" in page and "every card it names is done" in page
    assert "only the ids the needs half opens with are read" in page
