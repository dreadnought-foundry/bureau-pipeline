"""An epic the epic wait held is carried on once nothing holds it (DRE-6671).

`scripts/epic_wait.py hold` parks an epic in Backlog while a blocker epic is
open, and its receipt tells the CEO nobody needs to move it. The auto-advance
that was to carry it on, `reconcile.advance_unblocked_epics`, is an EDGE: it
fires only when the sweep itself closes a blocker epic Done, for a dependent in
Backlog at that instant. A blocker Canceled or made a Duplicate, one a person
closed, one closed between the gate's read and its move, or one whose lane the
gate could not read would each strand the held epic in Backlog — and the lane
contract promotes no epic out of Backlog, so nothing would say so.

`reconcile.release_waiting_epics` is the LEVEL: every full sweep reads every
held epic again with the hold's own rule. Pinned here as scenarios — the gate
parks the card on a fake board, the blocker then ends, and the sweep is asked —
each test failing if the behavior it names is removed:

  1. a blocker that ends Done, Canceled or Duplicate releases the card to the
     auto-advance's own lane and note;
  2. a blocker still open, a blocker whose lane cannot be read, and a read
     that fails keep it waiting — and a lane unread at the hold that a later
     read finds closed releases it;
  3. the release happens once, reads only held cards, and touches only this
     repo's;
  4. the full sweep asks it, off the Backlog read promotion already makes.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_wait_release.py -v
"""

from __future__ import annotations

import contextlib
import inspect
import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import epic_wait  # noqa: E402
import reconcile  # noqa: E402
from test_epic_wait import _Board, _dre6585, _hold  # noqa: E402

HELD = "DRE-9103"
BLOCKER = "DRE-9101"


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")


def _wire(monkeypatch, board: _Board) -> None:
    """Point the sweep's `linear_ops` at the fake board."""
    monkeypatch.setattr(reconcile.linear_ops, "gql", board.gql)
    monkeypatch.setattr(reconcile.linear_ops, "comment_bodies", board.comment_bodies)
    monkeypatch.setattr(reconcile.linear_ops, "cmd_comment", board.cmd_comment)
    monkeypatch.setattr(reconcile.linear_ops, "cmd_advance", board.cmd_advance)


def _candidate(board: _Board, identifier: str, repo: str = "bureau-pipeline") -> dict:
    """The card as `backlog_children` hands it to promotion: its labels and its
    comment window inline, newest first as the API returns it."""
    card = board.cards[identifier]
    return {
        "identifier": identifier,
        "title": card["title"],
        "description": "",
        "state": {"name": card["state"]},
        "parent": {"identifier": card["parent"]} if card["parent"] else None,
        "children": {"nodes": [{"id": "child"}] if card["children"] else []},
        "labels": {"nodes": [{"name": f"repo:{repo}"}]},
        "comments": {"nodes": [{"body": b} for b in reversed(card["comments"])]},
        "inverseRelations": {"nodes": []},
    }


def _parked(blocker_lane: str | None = "In Progress") -> _Board:
    board = _Board(_dre6585(first_lane=blocker_lane))
    _hold(board, HELD)
    assert board.cards[HELD]["state"] == "Backlog", "the gate parked it"
    return board


def _release(board: _Board, *identifiers: str) -> tuple:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        released = reconcile.release_waiting_epics(
            [_candidate(board, i) for i in (identifiers or (HELD,))])
    return released, buf.getvalue()


def _notes(board: _Board, card: str) -> list:
    return [b for b in board.cards[card]["comments"] if b.startswith(epic_wait.RELEASED)]


# =========================================================================== #
# 1. a blocker that ends releases the card                                    #
# =========================================================================== #


class TestABlockerThatEndsReleasesTheCard:
    @pytest.mark.parametrize("ended", ["Done", "Canceled", "Duplicate"])
    def test_the_held_epic_is_carried_on(self, ended, monkeypatch):
        board = _parked()
        board.cards[BLOCKER]["state"] = ended
        _wire(monkeypatch, board)
        released, out = _release(board)
        assert released == {HELD}
        assert board.cards[HELD]["state"] == "Triage"
        assert board.advances[-1] == (HELD, "Triage", "Backlog")
        assert len(_notes(board, HELD)) == 1
        assert HELD in out

    def test_it_takes_the_auto_advance_s_own_lane_and_note(self, monkeypatch):
        """Whichever release found it, a carried-on epic reads the same."""
        board = _parked()
        board.cards[BLOCKER]["state"] = "Canceled"
        _wire(monkeypatch, board)
        _release(board)
        note = _notes(board, HELD)[0]
        assert "Canceled" in note
        assert "a human still approves the plan" in note

    def test_the_auto_advance_s_note_opens_with_the_released_mark(self, monkeypatch):
        """`epic_wait.RELEASED` ends the wait on the record; the edge-triggered
        auto-advance must write it too, or a card it carried on would read as
        still held."""
        posted = []
        monkeypatch.setattr(reconcile.linear_ops, "gql", lambda q, v: {"issue": {
            "relations": {"nodes": [{"type": "blocks", "issue": {"identifier": HELD}}]}}})
        monkeypatch.setattr(reconcile, "card_state", lambda i: "Backlog")
        monkeypatch.setattr(reconcile, "epic_blockers_unmet", lambda *a, **k: False)
        monkeypatch.setattr(reconcile.linear_ops, "cmd_advance", lambda *a, **k: None)
        monkeypatch.setattr(reconcile.linear_ops, "cmd_comment",
                            lambda ident, body, *f: posted.append(body))
        reconcile.advance_unblocked_epics(BLOCKER)
        assert len(posted) == 1 and posted[0].startswith(epic_wait.RELEASED)
        assert epic_wait.waiting([epic_wait.OPENING + " held", posted[0]]) is False


# =========================================================================== #
# 2. what keeps it waiting                                                    #
# =========================================================================== #


class TestWhatKeepsItWaiting:
    def test_a_blocker_still_open_keeps_it_in_backlog(self, monkeypatch):
        board = _parked()
        board.cards[BLOCKER]["state"] = "In Review"
        _wire(monkeypatch, board)
        released, out = _release(board)
        assert released == set()
        assert board.cards[HELD]["state"] == "Backlog"
        assert _notes(board, HELD) == []
        assert "still waits" in out and BLOCKER in out

    def test_a_blocker_whose_lane_cannot_be_read_keeps_it_waiting(self, monkeypatch):
        board = _parked()
        board.cards[BLOCKER]["state"] = None
        _wire(monkeypatch, board)
        released, _ = _release(board)
        assert released == set()
        assert board.cards[HELD]["state"] == "Backlog"

    def test_a_lane_unread_at_the_hold_is_read_again_and_releases(self, monkeypatch):
        """The receipt promises "until the next pass reads it" — this is that
        read. Under the edge alone, a blocker already closed when the gate
        could not read it never closes again, and the card never moves."""
        board = _parked(blocker_lane=None)
        board.cards[BLOCKER]["state"] = "Done"
        _wire(monkeypatch, board)
        released, _ = _release(board)
        assert released == {HELD}
        assert board.cards[HELD]["state"] == "Triage"

    def test_a_read_that_fails_keeps_it_waiting_and_says_so(self, monkeypatch):
        board = _parked()
        board.cards[BLOCKER]["state"] = "Done"
        _wire(monkeypatch, board)

        def boom(query, variables):
            raise reconcile.linear_ops.LinearError("linear is down")

        monkeypatch.setattr(reconcile.linear_ops, "gql", boom)
        released, out = _release(board)
        assert released == set()
        assert board.cards[HELD]["state"] == "Backlog"
        assert "could not be read" in out

    def test_a_full_relation_page_is_not_released_on_half_a_page(self, monkeypatch):
        board = _parked()
        board.cards[BLOCKER]["state"] = "Done"
        _wire(monkeypatch, board)
        read = board.gql

        def full_page(query, variables):
            data = read(query, variables)
            data["issue"]["inverseRelations"]["pageInfo"]["hasNextPage"] = True
            return data

        monkeypatch.setattr(reconcile.linear_ops, "gql", full_page)
        released, _ = _release(board)
        assert released == set()
        assert board.cards[HELD]["state"] == "Backlog"


# =========================================================================== #
# 3. once, held cards only, this repo only                                    #
# =========================================================================== #


class TestOnceAndOnlyHeldCards:
    def test_a_card_carried_on_is_not_carried_on_again(self, monkeypatch):
        """A person who puts the released epic back in Backlog parked it; the
        spent receipt is not a reason to move it a second time."""
        board = _parked()
        board.cards[BLOCKER]["state"] = "Done"
        _wire(monkeypatch, board)
        _release(board)
        board.cards[HELD]["state"] = "Backlog"
        reads = len(board.reads)
        released, _ = _release(board)
        assert released == set()
        assert board.cards[HELD]["state"] == "Backlog"
        assert len(_notes(board, HELD)) == 1
        assert len(board.reads) == reads, "a spent wait costs no read"

    def test_a_backlog_card_with_no_receipt_costs_no_read(self, monkeypatch):
        board = _Board(_dre6585())
        board.cards["DRE-9102"]["state"] = "Backlog"
        _wire(monkeypatch, board)
        released, _ = _release(board, "DRE-9102")
        assert released == set()
        assert board.reads == []

    def test_another_repo_s_held_card_is_left_to_its_own_sweep(self, monkeypatch):
        board = _parked()
        board.cards[BLOCKER]["state"] = "Done"
        _wire(monkeypatch, board)
        reads = len(board.reads)
        with contextlib.redirect_stdout(io.StringIO()):
            released = reconcile.release_waiting_epics(
                [_candidate(board, HELD, repo="portico")])
        assert released == set()
        assert board.cards[HELD]["state"] == "Backlog"
        assert len(board.reads) == reads

    def test_a_card_held_again_after_a_release_gets_a_fresh_receipt(self, monkeypatch):
        """The receipt dedupe reads only the current wait: a card carried on
        and then held again on the same blocker says so again."""
        board = _parked()
        board.cards[BLOCKER]["state"] = "Done"
        _wire(monkeypatch, board)
        _release(board)
        board.cards[BLOCKER]["state"] = "In Progress"  # reopened
        board.cards[HELD]["state"] = "Planning"
        _hold(board, HELD)
        receipts = [b for b in board.cards[HELD]["comments"]
                    if b.startswith(epic_wait.OPENING)]
        assert len(receipts) == 2
        assert epic_wait.waiting(board.cards[HELD]["comments"]) is True


# =========================================================================== #
# 4. the full sweep asks it                                                   #
# =========================================================================== #


class TestTheSweepAsksIt:
    def test_promotion_releases_a_held_epic_off_its_candidates(self, monkeypatch):
        board = _parked()
        board.cards[BLOCKER]["state"] = "Canceled"
        _wire(monkeypatch, board)
        monkeypatch.setattr(reconcile, "epic_records", lambda epics: {})
        monkeypatch.setattr(reconcile, "_door_epic_threads", lambda epics: None)
        with contextlib.redirect_stdout(io.StringIO()):
            reconcile.promote_ready(
                active_count=0, candidates=[_candidate(board, HELD)],
                release_waits=True)
        assert board.cards[HELD]["state"] == "Triage"

    def test_promotion_without_the_flag_leaves_it(self, monkeypatch):
        """The gate path (`--promote-only`) keeps to its own work."""
        board = _parked()
        board.cards[BLOCKER]["state"] = "Canceled"
        _wire(monkeypatch, board)
        monkeypatch.setattr(reconcile, "epic_records", lambda epics: {})
        monkeypatch.setattr(reconcile, "_door_epic_threads", lambda epics: None)
        with contextlib.redirect_stdout(io.StringIO()):
            reconcile.promote_ready(active_count=0, candidates=[_candidate(board, HELD)])
        assert board.cards[HELD]["state"] == "Backlog"

    def test_the_full_sweep_passes_the_flag(self):
        source = inspect.getsource(reconcile.main)
        assert "release_waits=True" in source
