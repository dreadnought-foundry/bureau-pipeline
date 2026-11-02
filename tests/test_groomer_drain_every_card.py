"""RED-first: the groom drain moves every card the CEO approved for Planning,
all at once, with no slot count — and the groom queue is gone (DRE-8619).

Since DRE-5326 the drain read the planner slot ledger before it moved a card
and moved no more cards than there were free planner slots; every card past
them was QUEUED in Intake under a `groom-queued` label until the sweep
released it (DRE-5435). The planner cap was removed on 2026-10-31 (epic
DRE-8539, `architecture/decisions/adr-planner-cap-removed.md`), so there is
nothing left to ration against. Now:

  * an approved record of 25 Planning cards, two additions and one accepted
    rewrite moves all 28 to Planning in one call, in the record's order, each
    with its `🧺 groom-moved:` note;
  * no card gets a `groom-queued` label, and the drain writes no queue record;
  * the drain makes no planner-ledger read: the fake below fails any read of
    Planning cards' comments, which is exactly what that read was;
  * an excluded card, and a card in a repo switched off on the thread, stay in
    Intake as `held back` rows, exactly as before;
  * the `SlotsUnknown` refusal no longer exists, and the drain's other
    refusals are unchanged.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_drain_every_card.py -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import groomer  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402

from test_groomer import CYCLES, NOW, judged  # noqa: E402
from test_groomer_rewrite_drain import (  # noqa: E402
    PROPOSAL_CARD, FakeOps as _RewriteOps, _accept, _body, _cells,
    _decision, _draft, _draft_comment, _drained, _thread)
from test_groomer_two_lists import (  # noqa: E402
    LIKELY_DONE, SUPERSEDED, answer_for, lane)

#: The slot read as the module shipped it, captured at collection — the drain
#: must make no such read, so a test that finds one puts it back to prove it.
_SLOT_READ = getattr(groomer, "free_planner_slots", None)

ADDITIONS = ("DRE-950", "DRE-951")
REWRITE = "DRE-901"
HELD_REPO = "atlas"
EXCLUDED, SWITCHED_OFF = "DRE-4", "DRE-7"


class LedgerlessOps(_RewriteOps):
    """The rewrite drain's fixture, with every read the planner slot ledger
    made refused: a paged read of the board (`planner_queue.read_board`) and
    any comment read of a card in Planning. A drain that rations by slots
    cannot get past either."""

    COMMENT_WINDOW = linear_ops.COMMENT_WINDOW

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.planning_reads: list[str] = []

    def gql_paged(self, query, variables=None, **kwargs):
        self.planning_reads.append(query)
        raise linear_ops.LinearError(
            "the planner ledger was read: a paged read of Planning's comments")

    def comment_records(self, identifier, *, whole_thread=False):
        if self.get_issue(identifier)["state"]["name"] == "Planning":
            self.planning_reads.append(identifier)
            raise linear_ops.LinearError(
                f"{identifier} is in Planning and its comments were read")
        return super().comment_records(identifier, whole_thread=whole_thread)

    def get_issue(self, identifier):
        issue = super().get_issue(identifier)
        if (identifier, "Planning") in self.state_writes:
            issue["state"] = {"name": "Planning"}
        return issue

    def gql(self, query, variables=None):
        if "comments" in query and query != groomer.DESCRIPTION_QUERY:
            self.planning_reads.append(query)
            raise linear_ops.LinearError("a comment read through gql")
        return super().gql(query, variables)

    def add_label(self, identifier, label_name):
        self.log.append(("label", identifier, label_name))


def twenty_five(*, held_repo_card=None):
    """Thirty cards, five of them on the Cancel list, so the approved
    Planning list is twenty-five long — with the accepted rewrite's card on
    the Rewrite first table, as `rewrite_proposal` puts it."""
    cards = lane(30, superseded=SUPERSEDED)
    if held_repo_card:
        target = next(c for c in cards if c["identifier"] == held_repo_card)
        target["labels"] = {"nodes": [{"name": f"repo:{HELD_REPO}"},
                                      {"name": "agent:engineer"}]}
    proposal = groomer.propose(
        cards, cycles=CYCLES, capacity=25, now=NOW,
        judgement=judged(cards, answer_for(cards, likely_done=LIKELY_DONE)))
    proposal["outcomes"][groomer.REWRITE] = [
        {"identifier": REWRITE, "title": f"Rewrite {REWRITE}",
         "repo": "agent-bureau", "epic": None, "band": 2, "position": 1,
         "summary": "Part of it is already on main.", "draft": _draft(REWRITE),
         "body_digest": groomer.body_digest(_body(REWRITE)), "verify": {}}]
    return proposal


def _planning(proposal):
    return [r["identifier"] for r in
            sorted(proposal["outcomes"]["now"], key=lambda r: r["position"])]


def _ops(proposal, *decisions):
    return LedgerlessOps(_thread(proposal, *decisions),
                         threads={REWRITE: [_draft_comment(REWRITE)]},
                         bodies={REWRITE: _body(REWRITE)})


def _morning():
    proposal = twenty_five()
    assert len(_planning(proposal)) == 25, (
        "the fixture must approve twenty-five cards for this to prove anything")
    ops = _ops(proposal,
               *(_decision(groomer.ADD_TAG, proposal, card=a) for a in ADDITIONS),
               _accept(proposal, REWRITE))
    return proposal, ops


def _restore_the_slot_read(monkeypatch):
    """`tests/conftest.py` used to lift the slot read for every drain test.
    Put the module's own back, so a drain that still makes it is caught."""
    if _SLOT_READ is not None:
        monkeypatch.setattr(groomer, "free_planner_slots", _SLOT_READ)


def _notes(ops):
    return [(i, b) for i, b in ops.written
            if b.startswith(f"{groomer.MARK} {groomer.MOVED_TAG}:")]


# --------------------------------------------------------------------------
# every approved card moves, in one call, in the record's order
# --------------------------------------------------------------------------
def test_all_twenty_eight_approved_cards_move_to_planning_in_one_call(monkeypatch):
    _restore_the_slot_read(monkeypatch)
    proposal, ops = _morning()
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    expected = [*_planning(proposal), *ADDITIONS, REWRITE]
    assert len(expected) == 28
    moved = [i for i, lane_ in ops.state_writes if lane_ == "Planning"]
    assert moved == expected, "not every approved card moved, or not in order"
    assert result["moved"] == expected
    assert result["added"] == list(ADDITIONS)
    assert result["rewritten"] == [REWRITE]
    assert ops.planning_reads == [], (
        f"the drain read the planner ledger: {ops.planning_reads[:2]}")


def test_each_moved_card_carries_its_groom_moved_note_and_no_queue_label(monkeypatch):
    _restore_the_slot_read(monkeypatch)
    proposal, ops = _morning()
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    noted = [i for i, _ in _notes(ops)]
    assert noted == result["moved"], "a moved card lacks its note, or has two"
    positions = {r["identifier"]: r["position"] for r in proposal["outcomes"]["now"]}
    for identifier, body in _notes(ops):
        assert body == groomer.moved_note(
            proposal["id"], position=positions.get(identifier), of=25,
            batch_card=PROPOSAL_CARD)
    assert [e for e in ops.log if e[0] == "label"] == [], (
        "the drain labelled a card")
    assert not any("groom-queued" in b for _, b in ops.written), (
        "the drain wrote a queue record")


def test_the_drained_record_says_every_card_moved_and_none_was_queued(monkeypatch):
    _restore_the_slot_read(monkeypatch)
    proposal, ops = _morning()
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert "queued" not in result and "slots" not in result
    body = _drained(ops)
    summary = next(ln for ln in body.splitlines() if ln.startswith("moved: "))
    assert summary.startswith("moved: 25 · rewritten: 1 · held back: 0 · added: 2 ·")
    assert "planner slot" not in body and "groom-queued" not in body
    for identifier in _planning(proposal):
        assert _cells(body, identifier)[2] == "moved"
    assert _cells(body, REWRITE)[2] == "rewritten"


# --------------------------------------------------------------------------
# held back exactly as before
# --------------------------------------------------------------------------
def test_an_excluded_card_and_a_held_repos_card_stay_in_intake_as_held_back(monkeypatch):
    _restore_the_slot_read(monkeypatch)
    proposal = twenty_five(held_repo_card=SWITCHED_OFF)
    assert {EXCLUDED, SWITCHED_OFF} <= set(_planning(proposal))
    hold = {"body": f"{groomer.MARK} {groomer.REPO_HOLD_TAG}: {HELD_REPO}",
            "authored_by_pipeline": False}
    ops = _ops(proposal, _decision(groomer.EXCLUDE_TAG, proposal, card=EXCLUDED,
                                   reason="design first"), hold)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    touched = {e[1] for e in ops.log}
    for kept in (EXCLUDED, SWITCHED_OFF):
        assert kept not in touched and f"uuid-{kept}" not in touched, kept
    # The Rewrite first card is held back too: nobody accepted its draft here.
    assert result["held_back"] == [EXCLUDED, SWITCHED_OFF, REWRITE]
    body = _drained(ops)
    assert _cells(body, EXCLUDED)[2:] == [
        "held back", f"`{groomer.MARK} {groomer.EXCLUDE_TAG}` — design first"]
    assert _cells(body, SWITCHED_OFF)[2:] == ["held back",
                                              f"repo held: {HELD_REPO}"]
    others = [i for i in _planning(proposal) if i not in (EXCLUDED, SWITCHED_OFF)]
    assert [i for i, lane_ in ops.state_writes if lane_ == "Planning"] == others


# --------------------------------------------------------------------------
# the refusals
# --------------------------------------------------------------------------
def test_the_slots_unknown_refusal_is_gone():
    assert not hasattr(groomer, "SlotsUnknown")
    assert not hasattr(groomer, "free_planner_slots")


def test_a_failing_linear_read_of_planning_does_not_refuse_the_drain(monkeypatch):
    """What used to refuse as `SlotsUnknown` — the ledger read failing — is a
    read the drain no longer makes, so the batch drains."""
    _restore_the_slot_read(monkeypatch)
    proposal, ops = _morning()
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert not any(b.startswith(f"{groomer.MARK} {groomer.DRAIN_REFUSED_TAG}:")
                   for _, b in ops.written)


def test_the_other_refusals_are_unchanged(monkeypatch):
    _restore_the_slot_read(monkeypatch)
    proposal = twenty_five()
    # No approval.
    unapproved = LedgerlessOps([{"body": groomer.proposal_comment(proposal),
                                 "authored_by_pipeline": True}])
    with pytest.raises(groomer.NotApproved):
        groomer.drain(unapproved, card=PROPOSAL_CARD)
    # An approval naming a proposal the card does not carry.
    no_record = LedgerlessOps([_decision(groomer.APPROVAL_TAG, proposal)])
    with pytest.raises(groomer.NoProposalRecord):
        groomer.drain(no_record, card=PROPOSAL_CARD)
    # A batch already drained.
    proposal, ops = _morning()
    groomer.drain(ops, card=PROPOSAL_CARD)
    again = _ops(proposal, *[{"body": b, "authored_by_pipeline": True}
                             for t, b in ops.written if t == PROPOSAL_CARD])
    with pytest.raises(groomer.AlreadyDrained):
        groomer.drain(again, card=PROPOSAL_CARD)
    for refused in (unapproved, no_record, again):
        assert refused.state_writes == []
        assert len([b for _, b in refused.written if b.startswith(
            f"{groomer.MARK} {groomer.DRAIN_REFUSED_TAG}:")]) == 1


# --------------------------------------------------------------------------
# the queue is gone from the groomer and its workflow
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["scripts/groomer.py",
                                  ".github/workflows/groomer.yml"])
def test_no_trace_of_the_slot_count_or_the_queue_is_left(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    found = re.findall(r"free_planner_slots|planner_queue|groom-queued|"
                       r"queue_standing|SlotsUnknown|groom_stall|"
                       r"PLANNER_MAX_RUNNING", text)
    assert found == [], f"{path} still names {sorted(set(found))}"


@pytest.mark.parametrize("path", [
    "scripts/reconcile.py", "config/notice-writers.json",
    "config/pipeline-acts.json", "config/lane-contract.json"])
def test_the_sweep_and_the_declarations_name_no_queue(path):
    """`planner_queue` is left out on purpose: the sweep's import and its
    one-way drain of the old planner line, and the `planner-slot` act, are
    DRE-8622's to delete."""
    text = (ROOT / path).read_text(encoding="utf-8")
    found = re.findall(r"free_planner_slots|groom-queued|queue_standing|"
                       r"SlotsUnknown|groom_stall|release_groom_queue|"
                       r"waiting_max|PLANNER_MAX_RUNNING", text)
    assert found == [], f"{path} still names {sorted(set(found))}"


@pytest.mark.parametrize("path", [
    "config/pipeline-acts.json", "config/triage-reasons.json",
    "docs/triage-reasons.md"])
def test_no_declaration_still_speaks_of_the_planner_line(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "planner line" not in text.lower(), path


@pytest.mark.parametrize("name", [
    "release_groom_queue", "_report_groom_stall", "_clear_groom_stall",
    "_groom_labelled", "_groom_fresh", "_groom_label_readable",
    "_repair_groom_queue", "_groom_noted", "GROOM_RELEASE_TO",
    "GROOM_LABEL_FRESH_MINUTES", "GROOM_STALL_LANE", "GROOM_STALL_TITLE"])
def test_the_sweeps_release_and_its_stall_alert_are_gone(name):
    assert not hasattr(reconcile, name), f"reconcile.{name} is still defined"


def test_the_sweep_still_drains_the_old_planner_line():
    """What this card does not touch: DRE-8622 deletes the line's drain."""
    assert callable(getattr(reconcile, "serve_planner_line", None))
    assert hasattr(reconcile, "planner_queue")


@pytest.mark.parametrize("name", [
    "QUEUED_TAG", "RELEASED_TAG", "QUEUED_LABEL", "queued_record",
    "requeued_record", "parse_queued_record", "released_record",
    "parse_released_record", "queue_records", "queue_standing",
    "wrongly_dropped", "moved_position", "GROOM_LABEL_FRESH_MINUTES"])
def test_the_queues_writers_and_readers_are_gone(name):
    assert not hasattr(groomer, name), f"groomer.{name} is still defined"


def test_the_moved_note_has_one_writer_and_no_queue_place():
    """The drain is the note's one writer: the sweep's queue release, and the
    `place` it named, went with the queue."""
    with pytest.raises(TypeError):
        groomer.moved_note("abc123def456", position=1, of=2, place=1,
                           batch_card=PROPOSAL_CARD)


def test_the_proposal_no_longer_leaves_out_a_labelled_card():
    """`propose` left a card carrying the queue's label off the offer; with
    no queue, nothing reads the label."""
    assert not hasattr(groomer, "_queued")
