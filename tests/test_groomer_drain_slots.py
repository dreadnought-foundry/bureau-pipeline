"""RED-first: a groom drain releases no more cards than there are free planner
slots, and the cap is two (DRE-5326).

On 2026-09-30 at 07:05 PT one groom drain moved nineteen Intake cards to
Planning at once. The planner cap kept four planner and critic agent steps
running, and four together spend about 185-260 Linear requests a minute
against a key that refills about 42 a minute (2,500 an hour). By 07:40 PT the
fleet's key was at zero and every Linear-touching run in the fleet was being
refused, the activation of the CEO's priority epic DRE-5034 among them. With
two planners running the spend was about 27 a minute.

So:

  * the committed cap is two;
  * the drain reads the planner slot ledger once before any card moves, and
    moves no more cards to Planning than the free slots — the cap minus the
    planners running, the dispatched slots, and the cards already waiting in
    line (a card released into a line only lengthens it);
  * every card past that is a `held back` row naming the slots, and stays in
    Intake for the next proposal — the summary line keeps the grammar the
    console parses;
  * an addition takes a slot like any other move; an agreed Cancel row does
    not, because a canceled card starts no planner;
  * a ledger the drain cannot read refuses the drain before any card moves,
    written on the proposal card like every other refusal.

`tests/conftest.py` lifts the slot read for every other drain test (their
fixtures move fifteen cards and carry no ledger); each test here puts back the
slots it means.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_drain_slots.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer  # noqa: E402
import planner_queue  # noqa: E402

from test_groomer_cancel_drain import (  # noqa: E402
    KEEP_CANCEL, KEEP_PLANNING, PROPOSAL_CARD, FakeOps, _cancel, _decision,
    _drained, _fifteen_and_five, _outcome, _planning, _row, _summary, _thread,
    DRE_4682_SUMMARY)
from test_groomer_two_lists import two_lists  # noqa: E402

#: The slot read as the module ships it, captured at collection — before the
#: conftest fixture lifts it for the test that is running.
REAL_FREE_SLOTS = getattr(groomer, "free_planner_slots", None)


def _slots(monkeypatch, free, cap=2):
    monkeypatch.setattr(groomer, "free_planner_slots", lambda lops: (free, cap))


def _planning_moves(ops):
    return [i for i, lane in ops.state_writes if lane == "Planning"]


# --------------------------------------------------------------------------
# the cap
# --------------------------------------------------------------------------
def test_the_committed_cap_is_two(monkeypatch):
    """Four planners out-spend Linear's refill four to six times over; two
    spent about 27 requests a minute on the same board."""
    monkeypatch.delenv(planner_queue.CONFIG_ENV, raising=False)
    assert planner_queue.cap() == 2


# --------------------------------------------------------------------------
# the drain moves no more than the free slots
# --------------------------------------------------------------------------
def test_the_drain_moves_only_as_many_cards_as_there_are_free_slots(monkeypatch):
    proposal, ops = _fifteen_and_five()
    _slots(monkeypatch, 2)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    planning = [i for i in _planning(proposal) if i != KEEP_PLANNING]
    # The batch keeps its order: the first two approved cards go.
    assert _planning_moves(ops) == planning[:2]
    assert result["moved"] == planning[:2]


def test_every_card_past_the_slots_stays_in_intake_as_a_held_back_row(monkeypatch):
    proposal, ops = _fifteen_and_five()
    _slots(monkeypatch, 2)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    planning = [i for i in _planning(proposal) if i != KEEP_PLANNING]
    over = planning[2:]
    for i in over:
        assert not [e for e in ops.log if e[1] in (i, f"uuid-{i}")], (
            f"the drain wrote on {i}, a card past the free slots")
        assert i in result["held_back"]
    body = _drained(ops)
    for i in over:
        assert _outcome(body, i) == "held back"
        assert "planner slots: 2 free of 2" in _row(body, i)
    # The CEO's own exclusion keeps its own why, not the slots'.
    assert "planner slots" not in _row(body, KEEP_PLANNING)
    summary = _summary(body)
    assert DRE_4682_SUMMARY.match(summary), summary
    assert summary.startswith(f"moved: 2 · held back: {len(over) + 2} · ")


def test_no_free_slot_moves_no_card_to_planning_and_still_cancels(monkeypatch):
    """A canceled card starts no planner, so the Cancel list is not rationed."""
    proposal, ops = _fifteen_and_five()
    _slots(monkeypatch, 0)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert _planning_moves(ops) == []
    assert result["moved"] == []
    cancel = [i for i in _cancel(proposal) if i != KEEP_CANCEL]
    assert result["cancelled"] == cancel
    assert [i for i, lane in ops.state_writes if lane == "Canceled"] == cancel


def test_an_addition_takes_a_slot_after_the_batch(monkeypatch):
    proposal = two_lists()
    spare = "DRE-9001"
    ops = FakeOps(comments=_thread(
        proposal, _decision(groomer.ADD_TAG, proposal, card=spare)))
    batch = _planning(proposal)
    _slots(monkeypatch, len(batch))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert _planning_moves(ops) == batch
    assert result["added"] == []
    assert _outcome(_drained(ops), spare) == "held back"

    ops = FakeOps(comments=_thread(
        proposal, _decision(groomer.ADD_TAG, proposal, card=spare)))
    _slots(monkeypatch, len(batch) + 1)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert _planning_moves(ops) == batch + [spare]
    assert result["added"] == [spare]


def test_a_card_already_gone_does_not_use_a_slot(monkeypatch):
    proposal = two_lists()
    batch = _planning(proposal)
    ops = FakeOps(comments=_thread(proposal), lanes={batch[0]: "Done"})
    _slots(monkeypatch, 2)
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert _planning_moves(ops) == batch[1:3]


# --------------------------------------------------------------------------
# the slot read itself
# --------------------------------------------------------------------------
class _Board(FakeOps):
    """The drain fixture plus the one ledger read `planner_queue` makes."""

    COMMENT_WINDOW = 50

    def __init__(self, *args, board=None, broken=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.board = list(board or [])
        self.broken = broken

    def gql_paged(self, query, variables=None):
        if self.broken:
            raise RuntimeError("linear-budget: 0 → 0 … refused")
        return list(self.board)


def _slot_card(ident, lane, state, n):
    at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    body = planner_queue.format_receipt(
        state, card=ident, run=f"run-{n}", repo="dreadnought-foundry/atlas",
        trigger="Planning", at=at, place=1, of=1)
    return {"identifier": ident, "state": {"name": lane},
            "comments": {"nodes": [{"id": f"c-{n}", "body": body,
                                    "createdAt": at}]}}


def test_free_slots_are_the_cap_minus_running_and_waiting(monkeypatch):
    assert REAL_FREE_SLOTS is not None, "groomer has no free_planner_slots"
    monkeypatch.delenv(planner_queue.CONFIG_ENV, raising=False)
    running = _slot_card("DRE-7001", "Planning", "claimed", 1)
    waiting = _slot_card("DRE-7002", "Planning", "waiting", 2)
    assert REAL_FREE_SLOTS(_Board(board=[])) == (2, 2)
    assert REAL_FREE_SLOTS(_Board(board=[running])) == (1, 2)
    # A card waiting in line is owed the next slot before anything new is.
    assert REAL_FREE_SLOTS(_Board(board=[running, waiting])) == (0, 2)


def test_the_drain_reads_the_ledger_it_is_handed(monkeypatch):
    assert REAL_FREE_SLOTS is not None, "groomer has no free_planner_slots"
    monkeypatch.delenv(planner_queue.CONFIG_ENV, raising=False)
    monkeypatch.setattr(groomer, "free_planner_slots", REAL_FREE_SLOTS)
    proposal = two_lists()
    ops = _Board(comments=_thread(proposal),
                 board=[_slot_card("DRE-7001", "Planning", "claimed", 1)])
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert _planning_moves(ops) == _planning(proposal)[:1]
    assert "planner slots: 1 free of 2" in _drained(ops)


def test_an_unreadable_ledger_refuses_before_any_card_moves(monkeypatch):
    assert REAL_FREE_SLOTS is not None, "groomer has no free_planner_slots"
    monkeypatch.delenv(planner_queue.CONFIG_ENV, raising=False)
    monkeypatch.setattr(groomer, "free_planner_slots", REAL_FREE_SLOTS)
    proposal = two_lists()
    ops = _Board(comments=_thread(proposal), broken=True)
    with pytest.raises(groomer.DrainRefused) as refused:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []
    assert ops.mutations == []
    refusals = [b for t, b in ops.written if t == PROPOSAL_CARD and
                b.startswith(f"{groomer.MARK} {groomer.DRAIN_REFUSED_TAG}:")]
    assert len(refusals) == 1
    assert "planner slot" in refusals[0]
    assert refused.value.batch == proposal["id"]
