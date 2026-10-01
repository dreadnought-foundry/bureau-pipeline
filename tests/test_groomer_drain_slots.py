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
  * every card past that is QUEUED (DRE-5435): it stays in Intake, gains the
    `groom-queued` label, and is a `held back` row whose why is
    `groom-queued: place N of M` — the summary line keeps the grammar the
    console parses — and ONE `groom-queued` record follows the drained
    record, listing the queue in place order;
  * an addition takes a slot like any other move; an agreed Cancel row does
    not, because a canceled card starts no planner;
  * a ledger the drain cannot read refuses the drain before any card moves,
    written on the proposal card like every other refusal;
  * a drain the slots stop entirely is still ONE approval: the record is
    written, the batch is used up, and a second approval is `AlreadyDrained`
    (DRE-5435 — the CEO approved batch 37b77a9f795d three times on
    2026-10-01 and was refused each time);
  * a queue already standing on the thread takes the free slots first, so a
    later batch queues behind an earlier one.

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


@pytest.fixture(autouse=True)
def _label_writes(monkeypatch):
    """The drain fixture, plus the one write a queued card gets — its label,
    logged in the same order as every other write."""
    def add_label(self, identifier, label_name):
        self.log.append(("label", identifier, label_name))
    monkeypatch.setattr(FakeOps, "add_label", add_label, raising=False)


def _labelled(ops):
    return [e[1] for e in ops.log
            if e[0] == "label" and e[2] == groomer.QUEUED_LABEL]


def _why(body, identifier):
    return [c.strip() for c in _row(body, identifier).strip().strip("|")
            .split("|")][3]


def _queued(ops):
    records = [b for t, b in ops.written if t == PROPOSAL_CARD
               and b.startswith(f"{groomer.MARK} {groomer.QUEUED_TAG}:")]
    assert len(records) == 1, f"the drain wrote {len(records)} queued records"
    return groomer.parse_queued_record(records[0])


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


def test_every_card_past_the_slots_is_queued_in_intake_as_a_held_back_row(monkeypatch):
    proposal, ops = _fifteen_and_five()
    _slots(monkeypatch, 2)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    planning = [i for i in _planning(proposal) if i != KEEP_PLANNING]
    over = planning[2:]
    for i in over:
        # The label is the ONE write a queued card gets: no lane, no cycle.
        assert [e for e in ops.log if e[1] in (i, f"uuid-{i}")] == [
            ("label", i, groomer.QUEUED_LABEL)], (
            f"the drain wrote more than the queue label on {i}")
        assert i in result["held_back"]
    assert result["queued"] == over
    body = _drained(ops)
    for n, i in enumerate(over, 1):
        assert _outcome(body, i) == "held back"
        assert _why(body, i) == f"groom-queued: place {n} of {len(over)}"
    # The CEO's own exclusion keeps its own why, not the slots'.
    assert "planner slots" not in _row(body, KEEP_PLANNING)
    summary = _summary(body)
    assert DRE_4682_SUMMARY.match(summary), summary
    assert summary.startswith(f"moved: 2 · held back: {len(over) + 2} · ")


def test_no_free_slot_moves_no_card_to_planning_and_still_cancels(monkeypatch):
    """A canceled card starts no planner, so the Cancel list is not rationed —
    and the whole Planning list joins the queue, in batch order."""
    proposal, ops = _fifteen_and_five()
    _slots(monkeypatch, 0)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert _planning_moves(ops) == []
    assert result["moved"] == []
    cancel = [i for i in _cancel(proposal) if i != KEEP_CANCEL]
    assert result["cancelled"] == cancel
    assert [i for i, lane in ops.state_writes if lane == "Canceled"] == cancel
    planning = [i for i in _planning(proposal) if i != KEEP_PLANNING]
    assert result["queued"] == planning
    assert _labelled(ops) == planning
    assert [c["identifier"] for c in _queued(ops)["cards"]] == planning


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
    assert result["queued"] == [spare]
    assert _why(_drained(ops), spare) == "groom-queued: place 1 of 1"

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


# --------------------------------------------------------------------------
# a drain the slots stop entirely is still one approval (DRE-5435)
# --------------------------------------------------------------------------
def _no_cancel_list():
    from test_groomer import CYCLES, card
    proposal = groomer.propose([card(f"DRE-{n:03d}") for n in range(6)],
                               cycles=CYCLES, capacity=3, batch_cycles=1)
    assert _planning(proposal) and not _cancel(proposal), (
        "the fixture must be a Planning list with no Cancel list")
    return proposal, ()


def _every_cancel_row_excluded():
    proposal = two_lists()
    return proposal, tuple(
        _decision(groomer.EXCLUDE_TAG, proposal, card=i, reason="still wanted")
        for i in _cancel(proposal))


@pytest.mark.parametrize("morning", [_no_cancel_list, _every_cancel_row_excluded])
def test_a_drain_no_slot_lets_move_uses_up_the_batch_in_one_approval(monkeypatch, morning):
    """Every planner slot is taken, so no card moves and none is canceled —
    and the approval is used all the same: the batch goes into the line.

    It used to refuse (`NoFreeSlot`, DRE-5326) so the batch stayed
    approvable, and on 2026-10-01 the CEO approved batch 37b77a9f795d at
    06:29, 06:30 and 07:07 PT and was refused every time. Now the record is
    written once, every card is queued, and approving it a second time is
    `AlreadyDrained`, as for any drained batch."""
    proposal, decisions = morning()
    thread = _thread(proposal, *decisions)
    ops = FakeOps(comments=thread)
    _slots(monkeypatch, 0)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == [] and ops.mutations == []
    assert result["moved"] == []
    assert result["queued"] == _planning(proposal)
    written = [b for t, b in ops.written if t == PROPOSAL_CARD]
    assert not [b for b in written if
                b.startswith(f"{groomer.MARK} {groomer.DRAIN_REFUSED_TAG}:")]
    assert len([b for b in written if
                b.startswith(f"{groomer.MARK} {groomer.DRAINED_TAG}:")]) == 1

    # The CEO approves it again: the batch is used up.
    thread += [{"body": b, "authored_by_pipeline": True} for b in written]
    ops = FakeOps(comments=thread + [_decision(groomer.APPROVAL_TAG, proposal)])
    _slots(monkeypatch, 2)
    with pytest.raises(groomer.AlreadyDrained):
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == [] and _labelled(ops) == []


def test_no_free_slot_is_no_longer_a_refusal():
    assert not hasattr(groomer, "NoFreeSlot")


# --------------------------------------------------------------------------
# twenty cards into a full line (DRE-5435's acceptance)
# --------------------------------------------------------------------------
def _twenty():
    from test_groomer import CYCLES, card
    proposal = groomer.propose([card(f"DRE-{n:03d}") for n in range(20)],
                               cycles=CYCLES, capacity=20, batch_cycles=1)
    assert len(_planning(proposal)) == 20 and not _cancel(proposal)
    return proposal


def test_twenty_cards_into_a_full_line_are_twenty_queued_rows_and_one_queue(monkeypatch):
    proposal = _twenty()
    ops = FakeOps(comments=_thread(proposal))
    _slots(monkeypatch, 0)
    groomer.drain(ops, card=PROPOSAL_CARD)
    batch = _planning(proposal)
    # 20 label writes and nothing else on a card: no state write, no cycle.
    assert ops.state_writes == [] and ops.mutations == []
    assert _labelled(ops) == batch
    body = _drained(ops)
    for n, i in enumerate(batch, 1):
        assert _outcome(body, i) == "held back"
        assert _why(body, i) == f"groom-queued: place {n} of 20"
    summary = _summary(body)
    assert DRE_4682_SUMMARY.match(summary), summary
    assert summary.startswith("moved: 0 · held back: 20 · added: 0 · ")
    queued = _queued(ops)
    assert queued["id"] == proposal["id"]
    assert [(c["place"], c["identifier"]) for c in queued["cards"]] == list(
        enumerate(batch, 1))
    # The two records, in that order, and no other comment anywhere.
    assert [b.split(":")[0] for t, b in ops.written] == [
        f"{groomer.MARK} {groomer.DRAINED_TAG}",
        f"{groomer.MARK} {groomer.QUEUED_TAG}"]


def test_two_free_slots_move_the_first_two_and_queue_the_rest(monkeypatch):
    proposal = _twenty()
    ops = FakeOps(comments=_thread(proposal))
    _slots(monkeypatch, 2)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    batch = _planning(proposal)
    assert _planning_moves(ops) == batch[:2]
    assert result["queued"] == batch[2:]
    body = _drained(ops)
    for n, i in enumerate(batch[2:], 1):
        assert _why(body, i) == f"groom-queued: place {n} of 18"
    assert [c["identifier"] for c in _queued(ops)["cards"]] == batch[2:]
    assert _labelled(ops) == batch[2:]


def _older_queue(n=3):
    """A queue standing on the thread from an earlier batch: its record, as
    the drain that wrote it wrote it."""
    rows = [{"identifier": f"DRE-80{k}", "repo": "portico"} for k in range(n)]
    return {"body": groomer.queued_record("0ld0ba7c4e11", rows),
            "authored_by_pipeline": True}


def test_a_later_batch_queues_behind_a_standing_queue(monkeypatch):
    """Two free slots and an older queue of three: the slots are the older
    queue's, so this drain moves nothing and queues all twenty behind it."""
    proposal = _twenty()
    older = _older_queue(3)
    ops = FakeOps(comments=[older, *_thread(proposal)])
    _slots(monkeypatch, 2)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    batch = _planning(proposal)
    assert ops.state_writes == []
    assert result["moved"] == [] and result["queued"] == batch
    after = [older, *_thread(proposal)] + [
        {"body": b, "authored_by_pipeline": True}
        for t, b in ops.written if t == PROPOSAL_CARD]
    standing = groomer.queue_standing(after)
    assert [s["identifier"] for s in standing] == (
        ["DRE-800", "DRE-801", "DRE-802"] + batch)


def test_a_released_card_no_longer_stands_in_front(monkeypatch):
    """Queued, less released, less left — read off the thread alone."""
    proposal = _twenty()
    older = _older_queue(3)
    released = {"body": groomer.released_record([
        {"id": "0ld0ba7c4e11", "released": ["DRE-800"],
         "left": ["DRE-801", "DRE-802"], "unqueued": []}]),
        "authored_by_pipeline": True}
    ops = FakeOps(comments=[older, released, *_thread(proposal)])
    _slots(monkeypatch, 2)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == _planning(proposal)[:2]


# --------------------------------------------------------------------------
# the queue's vocabulary
# --------------------------------------------------------------------------
def test_the_queue_markers_and_label():
    assert groomer.QUEUED_TAG == "groom-queued"
    assert groomer.RELEASED_TAG == "groom-released"
    assert groomer.QUEUED_LABEL == "groom-queued"
    assert groomer.QUEUED_TAG in groomer.ALL_MARKERS
    assert groomer.RELEASED_TAG in groomer.ALL_MARKERS
    # No seventh outcome: the console mirrors this list.
    assert groomer.DRAIN_OUTCOMES == (
        "moved", "held back", "added", "cancelled", "refused", "already gone")


def test_the_queued_record_reads_back_whole():
    rows = [{"identifier": "DRE-11", "repo": "portico"},
            {"identifier": "DRE-12", "repo": "agent-bureau"}]
    body = groomer.queued_record("abc123def456", rows)
    assert body.startswith("🧺 groom-queued: abc123def456\n")
    assert "| place | card | repo |" in body
    assert groomer.parse_queued_record(body) == {
        "id": "abc123def456",
        "cards": [{"place": 1, "identifier": "DRE-11", "repo": "portico"},
                  {"place": 2, "identifier": "DRE-12", "repo": "agent-bureau"}]}
    # Anchored: a comment quoting the marker is not the record.
    assert groomer.parse_queued_record("see " + body) is None


def test_the_released_row_is_one_line_per_batch_in_its_grammar():
    body = groomer.released_record([
        {"id": "abc123def456", "released": ["DRE-1", "DRE-2"],
         "left": ["DRE-3"], "unqueued": []},
        {"id": "fed654cba321", "released": [], "left": [],
         "unqueued": ["DRE-9"]}])
    lines = body.strip().splitlines()
    assert lines[0] == ("🧺 groom-released: abc123def456 — released: DRE-1, "
                        "DRE-2 · left the lane: DRE-3")
    assert lines[1].startswith("🧺 groom-released: fed654cba321 — released: "
                               "none · left the lane: none")
    assert "DRE-9" in lines[1]
    assert groomer.parse_released_record(body) == [
        {"id": "abc123def456", "released": ["DRE-1", "DRE-2"],
         "left": ["DRE-3"], "unqueued": []},
        {"id": "fed654cba321", "released": [], "left": [],
         "unqueued": ["DRE-9"]}]
