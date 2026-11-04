"""TDD for "the prose-blocker refusal asks a fresh read first" (DRE-8851).

On 2026-11-02 at 12:24 AM PT the sweep posted `prose-blocker-no-relation` on
DRE-8635 and moved it to Triage, saying the board held no `blockedBy`
relation for DRE-8848. An operator had linked DRE-8848 as a blocker at about
12:16 AM PT, eight minutes earlier, and Linear's API listed it under DRE-8635's
blockers when it was checked. The refusal decided on the pass's board read —
the read door's cached board in `on`, up to `bureau_read.BOARD_MAX_AGE` old
and only as current as the door's own copy of the card's relations — and
never asked Linear again before it posted and moved.

What is TDD'd here: the refusal fires only when a FRESH read of the card's own
relations, made in the same pass, still lacks the identifier its prose names.

  1. THE REPLAY — DRE-8635's shape: a `Blocked by: DRE-8848` line, a board
     read that predates the relation, and Linear holding it. No comment, no
     move, and the card waits on DRE-8848 the way the relation says it should.
  2. THE CONTROL — the same line with no relation in either read still parks
     the card in Triage, as it did before this card.
  3. BETWEEN PASSES — a relation added after one pass is seen on the next,
     even when that pass is served the same stale board.

Run: cd bureau-pipeline && python3 -m pytest tests/test_prose_blocker_fresh_read.py -v
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import linear_ops  # noqa: E402
import prose_blockers  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

FLEET_VERDICT = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")

# The two forms of DRE-8635's declaration: the plain line the card names, and
# the link-wrapped mention Linear serialises a pasted issue reference as.
PLAIN = "Blocked by: DRE-8848"
LINKED = (
    "**Blocked by:** [DRE-8848](<https://linear.app/dreadnoughtfoundry/issue/"
    "DRE-8848/agent-bureau-the-console-knows-the-failure-tallys-receipt-failure>)"
)


@pytest.fixture(autouse=True)
def _pin_and_reset(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    reconcile._card_skips.clear()


def _relation(identifier: str, state: str = "In Progress") -> dict:
    return {"type": "blocks", "issue": {"identifier": identifier, "state": {"name": state}}}


def _card(description: str, relations=()) -> dict:
    """DRE-8635 as the sweep reads it: a child of an active epic, carrying a
    FLEET verdict, so only the dependency gate and the prose refusal can hold
    it back. Its two older blockers are already Done."""
    return {
        "identifier": "DRE-8635",
        "description": "**Repo:** bureau-pipeline\n" + description,
        "parent": {"identifier": "DRE-8600", "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": "size:M"}]},
        "comments": {"nodes": [{"body": FLEET_VERDICT}]},
        "inverseRelations": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [_relation("DRE-8633", "Done"), _relation("DRE-8632", "Done"),
                      *relations],
        },
    }


class _Board:
    """Linear and the pass's board read, kept apart.

    `live` is what Linear holds now. `snapshot` is what the pass's board read
    serves — the read door's cached board — and it changes only when a test
    says so, which is the whole of the stale read this card is about. A read
    with `from_linear=True` is the sweep asking Linear itself, and is answered
    from `live`, scoped to `only` and to the cards still in the queue."""

    def __init__(self, snapshot: dict, live: dict | None = None):
        self.snapshot = [snapshot]
        self.live = {snapshot["identifier"]: live if live is not None else snapshot}
        self.lane = {snapshot["identifier"]: "Queued"}
        self.comments: dict = {snapshot["identifier"]: []}
        self.moves: list = []
        self.live_reads: list = []

    def backlog_children(self, only=None, *, from_linear=False, stamped=False):
        if not from_linear:
            return [copy.deepcopy(c) for c in self.snapshot
                    if self.lane[c["identifier"]] == "Queued"]
        self.live_reads.append(list(only) if only is not None else None)
        return [copy.deepcopy(card) for ident, card in self.live.items()
                if (only is None or ident in only) and self.lane[ident] == "Queued"]

    def cmd_advance(self, identifier, to_state, from_states_csv):
        if self.lane.get(identifier) not in from_states_csv.split(","):
            return
        self.lane[identifier] = to_state
        self.moves.append((identifier, to_state))

    def cmd_comment(self, identifier, body, *flags):
        self.comments.setdefault(identifier, []).append(body)

    def count_comments(self, identifier, needle, since=None):
        return sum(1 for body in self.comments.get(identifier, []) if needle in body)

    def refusals(self) -> list:
        return [b for b in self.comments["DRE-8635"] if prose_blockers.CARD_TAG in b]

    def sweep(self, active_count: int = 0) -> int:
        with patch.object(reconcile, "backlog_children", self.backlog_children), \
            patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
            patch.object(reconcile.linear_ops, "count_comments", self.count_comments), \
            patch.object(reconcile.linear_ops, "cmd_advance", self.cmd_advance), \
            patch.object(reconcile.linear_ops, "cmd_comment", self.cmd_comment):
            return reconcile.promote_ready(active_count=active_count)


# ---------------------------------------------------------------------------
# 1. The replay: the relation is on the board, the pass's read predates it
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("line", [PLAIN, LINKED], ids=["plain", "link-wrapped"])
def test_replay_dre_8635_a_relation_linear_holds_is_not_refused(line, capsys):
    board = _Board(_card(line), live=_card(line, [_relation("DRE-8848", "Queued")]))
    board.sweep()
    assert board.refusals() == []
    assert ("DRE-8635", "Triage") not in board.moves
    out = capsys.readouterr().out
    assert f"{prose_blockers.CARD_TAG}:" not in out


def test_replay_the_card_waits_on_the_relation_the_fresh_read_found(capsys):
    """Not refused, and not promoted either: the fresh read's DRE-8848 is an
    open blocker, so the card waits on it — the gate reads the relation the
    fresh read returned, never the stale page that lacked it."""
    board = _Board(_card(PLAIN), live=_card(PLAIN, [_relation("DRE-8848", "Queued")]))
    promoted = board.sweep()
    assert promoted == 0
    assert board.moves == []
    out = capsys.readouterr().out
    assert "DRE-8848" in out and "formal blockedBy relation" in out


def test_replay_a_shipped_blocker_the_fresh_read_found_lets_the_card_go():
    board = _Board(_card(PLAIN), live=_card(PLAIN, [_relation("DRE-8848", "Done")]))
    promoted = board.sweep()
    assert promoted == 1
    assert board.moves == [("DRE-8635", "In Progress")]


def test_the_fresh_read_is_the_cards_own_and_made_in_the_same_pass():
    board = _Board(_card(PLAIN), live=_card(PLAIN, [_relation("DRE-8848", "Queued")]))
    board.sweep()
    assert ["DRE-8635"] in board.live_reads


def test_a_card_whose_board_read_agrees_with_its_prose_costs_no_fresh_read():
    """The fresh read is spent only on a card the board read would refuse —
    a corroborated declaration, every one of the board's today, costs nothing."""
    card = _card(PLAIN, [_relation("DRE-8848", "Queued")])
    board = _Board(card)
    board.sweep()
    assert ["DRE-8635"] not in board.live_reads


# ---------------------------------------------------------------------------
# 2. The control: no relation in either read still parks the card
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("line", [PLAIN, LINKED], ids=["plain", "link-wrapped"])
def test_the_same_line_with_no_relation_still_parks_the_card_in_triage(line):
    board = _Board(_card(line))
    board.sweep()
    assert board.moves == [("DRE-8635", "Triage")]
    assert len(board.refusals()) == 1
    assert "DRE-8848" in board.refusals()[0]


def test_a_card_that_left_the_queue_since_the_board_read_is_left_alone():
    """The fresh read is scoped to the queue: a card a person moved mid-pass
    is not refused on a read of a lane it is no longer in."""
    board = _Board(_card(PLAIN))
    board.live = {}  # Linear's queue no longer holds it; the board read still does
    board.sweep()
    assert board.refusals() == []
    assert board.moves == []


def test_a_fresh_read_that_fails_neither_refuses_nor_promotes(capsys):
    """No fresh answer, no proof: the claim is not refused on the stale read,
    and the card is not promoted past a blocker nobody could read either."""
    board = _Board(_card(PLAIN))

    def _read(only=None, *, from_linear=False, stamped=False):
        if from_linear:
            raise linear_ops.LinearError("linear error: 502")
        return [copy.deepcopy(board.snapshot[0])]

    with patch.object(board, "backlog_children", side_effect=_read):
        promoted = board.sweep()
    assert promoted == 0
    assert board.refusals() == []
    assert board.moves == []
    out = capsys.readouterr().out
    assert "DRE-8635" in out and "502" in out


def test_a_fresh_read_whose_relations_are_unknown_neither_refuses_nor_promotes():
    live = _card(PLAIN)
    live["inverseRelations"]["pageInfo"]["hasNextPage"] = True
    board = _Board(_card(PLAIN), live=live)
    with patch.object(reconcile, "complete_inverse_relations"):
        promoted = board.sweep()
    assert promoted == 0
    assert board.refusals() == []
    assert board.moves == []


# ---------------------------------------------------------------------------
# 3. A relation added between two passes is seen on the next pass
# ---------------------------------------------------------------------------
def test_a_relation_added_between_two_passes_is_seen_on_the_next_pass():
    """Pass one is at the WIP cap, so the card is held before any gate reads
    it. Between the passes an operator links DRE-8848 — on Linear, not on the
    door's cached board, which still serves the copy from before. Pass two
    must see the relation, not refuse the card for lacking it."""
    board = _Board(_card(PLAIN))
    board.sweep(active_count=reconcile.MAX_WIP)
    assert board.moves == [] and board.refusals() == []

    board.live["DRE-8635"] = _card(PLAIN, [_relation("DRE-8848", "Queued")])

    board.sweep()
    assert board.refusals() == []
    assert board.moves == []

    board.live["DRE-8635"] = _card(PLAIN, [_relation("DRE-8848", "Done")])
    board.sweep()
    assert board.moves == [("DRE-8635", "In Progress")]
