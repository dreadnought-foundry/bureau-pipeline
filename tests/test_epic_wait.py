"""An epic arriving in Planning while a blocker epic is open waits in Backlog
(DRE-6671).

The second epic of an observation-gated seam is "planned in detail only when
the first is Done" (`standards/card-quality.md`). DRE-6591 kept a roll-up's
blocked child in Backlog at activation; the Planning entrance itself stayed
open, so on 2026-10-10 the planner on DRE-6589 (blocked by DRE-6587, In
Progress) could only escalate "wait for the reading", and the answer press put
it straight back in Planning for a second run that asked the same question.

`scripts/epic_wait.py hold` is the gate `plan.yml` runs before the planner slot
is claimed. Pinned here against a fake Linear, each test failing if the
behavior it names is removed:

  1. the DRE-6585 shape — three child epics, the third blocked by the first,
     which is In Progress: the third is moved Planning → Backlog with ONE
     `⏸️ epic-waits:` receipt naming the first and its lane, and the gate
     answers `held=true`;
  2. what holds nothing — a terminal blocker, a blocker that is a card and not
     an epic, a card that is not an epic — and what holds anyway: a blocker
     whose state could not be read;
  3. idempotency — a second run on the held card posts nothing and moves
     nothing;
  4. the receipt is a declared act, and the Backlog entrance permits the move.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_wait.py -v
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import epic_wait  # noqa: E402
import pipeline_act  # noqa: E402

PARENT = "DRE-9100"
RECEIPT_OPENING = "⏸️ epic-waits:"


def _card(identifier: str, *, state: str = "Planning", title: str | None = None,
          children: bool = False, blocked_by=(), parent: str | None = None,
          comments=()) -> dict:
    return {
        "identifier": identifier,
        "title": title if title is not None else f"[EPIC] bureau-pipeline: slice {identifier}",
        "state": state,
        "children": children,
        "blocked_by": list(blocked_by),
        "parent": parent,
        "comments": list(comments),
    }


class _Board:
    """A fake `linear_ops`: the four functions the gate calls, over a dict of
    cards. `gql` answers the gate's one card read by the card's id; a card
    whose `state` is None answers with no state, the unreadable lane."""

    def __init__(self, cards: list):
        self.cards = {c["identifier"]: c for c in cards}
        self.moves: list = []
        self.advances: list = []
        self.posted: list = []
        self.reads: list = []

    def _node(self, identifier: str) -> dict:
        card = self.cards[identifier]
        state = card["state"]
        return {
            "identifier": identifier,
            "title": card["title"],
            "state": {"name": state} if state is not None else None,
            "children": {"nodes": [{"id": "child"}] if card["children"] else []},
        }

    def gql(self, query: str, variables: dict) -> dict:
        identifier = variables["id"]
        self.reads.append(identifier)
        card = self.cards.get(identifier)
        if card is None:
            return {"issue": None}
        issue = self._node(identifier)
        issue["parent"] = {"identifier": card["parent"]} if card["parent"] else None
        issue["inverseRelations"] = {
            "nodes": [
                {"type": "blocks",
                 "issue": self._node(b) if b in self.cards else None}
                for b in card["blocked_by"]
            ],
            "pageInfo": {"hasNextPage": False},
        }
        return {"issue": issue}

    def comment_bodies(self, identifier: str, *, whole_thread: bool = False) -> list:
        return list(self.cards[identifier]["comments"])

    def cmd_comment(self, identifier: str, body: str, *flags: str) -> None:
        self.posted.append((identifier, body))
        self.cards[identifier]["comments"].append(body)

    def cmd_advance(self, identifier: str, to_state: str, from_states_csv: str,
                    *flags: str) -> None:
        self.advances.append((identifier, to_state, from_states_csv))
        allowed = [s.strip().lower() for s in from_states_csv.split(",")]
        if (self.cards[identifier]["state"] or "").lower() in allowed:
            self.cards[identifier]["state"] = to_state
            self.moves.append((identifier, to_state))


def _hold(board: _Board, card: str, tmp_path: Path | None = None) -> tuple:
    """Run the gate; return (exit code, `held` as written, stdout)."""
    out_file = None
    if tmp_path is not None:
        out_file = tmp_path / "github_output"
        out_file.write_text("", encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = epic_wait.hold(board, card, github_output=str(out_file) if out_file else None)
    held = None
    if out_file is not None:
        for line in out_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("held="):
                held = line.split("=", 1)[1]
    return code, held, buf.getvalue()


def _receipts(board: _Board, card: str) -> list:
    return [b for b in board.cards[card]["comments"] if b.startswith(RECEIPT_OPENING)]


def _dre6585(first_lane: str = "In Progress") -> list:
    """The DRE-6585 shape: a roll-up with three child epics, the third blocked
    by the first, the second free. The third has just arrived in Planning."""
    return [
        _card(PARENT, state="In Progress", children=True,
              title="[EPIC] bureau-pipeline: the roll-up"),
        _card("DRE-9101", state=first_lane, parent=PARENT),
        _card("DRE-9102", state="Planning", parent=PARENT),
        _card("DRE-9103", state="Planning", parent=PARENT, blocked_by=["DRE-9101"]),
    ]


# =========================================================================== #
# 1. the DRE-6585 shape                                                       #
# =========================================================================== #


class TestAnEpicBlockedByAnOpenEpicWaits:
    def test_it_is_moved_to_backlog_with_one_receipt_naming_the_blocker(self, tmp_path):
        board = _Board(_dre6585())
        code, held, out = _hold(board, "DRE-9103", tmp_path)
        assert code == 0
        assert held == "true"
        assert board.moves == [("DRE-9103", "Backlog")]
        assert board.advances == [("DRE-9103", "Backlog", "Planning")]
        receipts = _receipts(board, "DRE-9103")
        assert len(receipts) == 1
        first = receipts[0].splitlines()[0]
        assert "DRE-9101" in first and "In Progress" in first
        assert "DRE-9103" in "\n".join(out.splitlines())

    def test_the_receipt_says_where_it_waits_and_what_brings_it_back(self):
        board = _Board(_dre6585())
        _hold(board, "DRE-9103")
        body = _receipts(board, "DRE-9103")[0]
        assert "Backlog" in body
        assert "auto-advance" in body
        assert "Done" in body

    def test_the_receipt_is_composed_as_the_declared_act(self):
        board = _Board(_dre6585())
        _hold(board, "DRE-9103")
        trailer = pipeline_act.read_trailer(_receipts(board, "DRE-9103")[0])
        assert trailer is not None
        assert trailer["act"] == epic_wait.ACT
        assert trailer["tag"] == epic_wait.TAG == "epic-waits"

    def test_the_receipt_is_posted_before_the_move(self):
        """The receipt says why the card moved; a move with no receipt beside it
        is a card that left Planning for no stated reason."""
        order = []
        board = _Board(_dre6585())
        post, advance = board.cmd_comment, board.cmd_advance
        board.cmd_comment = lambda *a, **k: (order.append("receipt"), post(*a, **k))[1]
        board.cmd_advance = lambda *a, **k: (order.append("move"), advance(*a, **k))[1]
        _hold(board, "DRE-9103")
        assert order == ["receipt", "move"]

    def test_the_free_sibling_is_not_held(self, tmp_path):
        board = _Board(_dre6585())
        code, held, _ = _hold(board, "DRE-9102", tmp_path)
        assert (code, held) == (0, "false")
        assert board.moves == [] and board.posted == []

    def test_every_open_blocker_epic_is_named_with_its_lane(self):
        cards = _dre6585() + [_card("DRE-9104", state="Green Light")]
        cards[3]["blocked_by"] = ["DRE-9101", "DRE-9104"]
        board = _Board(cards)
        _hold(board, "DRE-9103")
        first = _receipts(board, "DRE-9103")[0].splitlines()[0]
        assert "DRE-9101 (In Progress)" in first
        assert "DRE-9104 (Green Light)" in first

    def test_a_blocker_epic_known_only_by_its_children_holds(self):
        cards = _dre6585()
        cards[1]["title"] = "bureau-pipeline: an untitled epic"
        cards[1]["children"] = True
        board = _Board(cards)
        _hold(board, "DRE-9103")
        assert board.moves == [("DRE-9103", "Backlog")]

    def test_a_blocker_epic_known_only_by_its_shape_stamp_holds(self):
        cards = _dre6585()
        cards[1]["title"] = "bureau-pipeline: a stamped epic"
        cards[1]["comments"] = ["🧩 planning-shape: epic"]
        board = _Board(cards)
        _hold(board, "DRE-9103")
        assert board.moves == [("DRE-9103", "Backlog")]


# =========================================================================== #
# 2. what holds nothing, and what holds anyway                                #
# =========================================================================== #


class TestWhatHoldsNothing:
    @pytest.mark.parametrize("finished", ["Done", "Canceled", "Duplicate"])
    def test_a_terminal_blocker_holds_nothing(self, finished, tmp_path):
        board = _Board(_dre6585(first_lane=finished))
        code, held, out = _hold(board, "DRE-9103", tmp_path)
        assert (code, held) == (0, "false")
        assert board.moves == [] and board.posted == []
        assert out.strip(), "a pass says why"

    def test_a_blocker_that_is_a_card_not_an_epic_holds_nothing(self, tmp_path):
        cards = _dre6585()
        cards[1]["title"] = "bureau-pipeline: a one-off build card"
        board = _Board(cards)
        code, held, out = _hold(board, "DRE-9103", tmp_path)
        assert (code, held) == (0, "false")
        assert board.moves == [] and board.posted == []
        assert out.strip()

    def test_a_card_that_is_not_an_epic_is_never_held(self, tmp_path):
        cards = _dre6585()
        cards[3]["title"] = "bureau-pipeline: a one-off blocked by an open epic"
        board = _Board(cards)
        code, held, out = _hold(board, "DRE-9103", tmp_path)
        assert (code, held) == (0, "false")
        assert board.moves == [] and board.posted == []
        assert "not an epic" in out

    def test_a_card_with_no_blockers_is_not_held(self, tmp_path):
        board = _Board([_card("DRE-9200")])
        assert _hold(board, "DRE-9200", tmp_path)[:2] == (0, "false")
        assert board.moves == [] and board.posted == []

    def test_the_parent_epic_named_as_a_blocker_holds_nothing(self, tmp_path):
        """Never name the parent epic as a blocker — it stays In Progress until
        its children finish, so holding on it would hold the child forever."""
        cards = _dre6585()
        cards[3]["blocked_by"] = [PARENT]
        board = _Board(cards)
        assert _hold(board, "DRE-9103", tmp_path)[:2] == (0, "false")
        assert board.moves == []

    def test_a_card_outside_planning_and_backlog_is_not_held(self, tmp_path):
        """An approval (In Progress) or a review is not the Planning entrance."""
        cards = _dre6585()
        cards[3]["state"] = "In Progress"
        board = _Board(cards)
        assert _hold(board, "DRE-9103", tmp_path)[:2] == (0, "false")
        assert board.moves == [] and board.posted == []


class TestAnUnreadableBlockerHolds:
    def test_a_blocker_whose_state_cannot_be_read_holds_the_card(self, tmp_path):
        board = _Board(_dre6585(first_lane=None))
        code, held, out = _hold(board, "DRE-9103", tmp_path)
        assert (code, held) == (0, "true")
        assert board.moves == [("DRE-9103", "Backlog")]
        first = _receipts(board, "DRE-9103")[0].splitlines()[0]
        assert "DRE-9101" in first
        assert "could not be read" in first
        assert "could not be read" in out

    def test_a_relation_the_board_could_not_name_holds_the_card(self, tmp_path):
        cards = _dre6585()
        cards[3]["blocked_by"] = ["DRE-9999"]  # not on the board: issue null
        board = _Board(cards)
        assert _hold(board, "DRE-9103", tmp_path)[:2] == (0, "true")
        assert board.moves == [("DRE-9103", "Backlog")]


class TestAnUnreadableCard:
    def test_a_card_the_read_failed_on_passes_loudly(self, tmp_path):
        """Nothing is known about its blockers — not even that it has any — so
        the gate holds nothing and says so with a warning."""
        board = _Board(_dre6585())

        def boom(query, variables):
            raise RuntimeError("linear is down")

        board.gql = boom
        code, held, out = _hold(board, "DRE-9103", tmp_path)
        assert (code, held) == (0, "false")
        assert "::warning::" in out
        assert board.moves == [] and board.posted == []


# =========================================================================== #
# 3. idempotency                                                              #
# =========================================================================== #


class TestTwiceIsOnce:
    def test_a_second_run_posts_nothing_and_moves_nothing(self, tmp_path):
        board = _Board(_dre6585())
        _hold(board, "DRE-9103", tmp_path)
        assert len(board.posted) == 1 and len(board.moves) == 1
        code, held, out = _hold(board, "DRE-9103", tmp_path)
        assert (code, held) == (0, "true"), "still held: nothing may plan it"
        assert len(board.posted) == 1, "the receipt is posted once"
        assert len(board.moves) == 1, "the second run moves nothing"
        assert len(board.advances) == 1, "and asks for no move from Backlog"
        assert len(_receipts(board, "DRE-9103")) == 1

    def test_a_receipt_for_the_same_blockers_in_planning_is_not_reposted(self):
        """A run that posted and then died before the move: the next posts no
        second receipt, and still makes the move."""
        board = _Board(_dre6585())
        _hold(board, "DRE-9103")
        board.cards["DRE-9103"]["state"] = "Planning"
        board.cards["DRE-9101"]["state"] = "In Review"  # a lane change is not a new blocker
        _hold(board, "DRE-9103")
        assert len(_receipts(board, "DRE-9103")) == 1
        assert board.moves == [("DRE-9103", "Backlog"), ("DRE-9103", "Backlog")]

    def test_a_different_set_of_blockers_is_a_new_receipt(self):
        cards = _dre6585() + [_card("DRE-9104", state="In Progress")]
        board = _Board(cards)
        _hold(board, "DRE-9103")
        board.cards["DRE-9103"]["state"] = "Planning"
        board.cards["DRE-9103"]["blocked_by"] = ["DRE-9101", "DRE-9104"]
        _hold(board, "DRE-9103")
        assert len(_receipts(board, "DRE-9103")) == 2


# =========================================================================== #
# 4. the CLI, the registry and the contract                                   #
# =========================================================================== #


class TestTheCli:
    def test_hold_reads_the_card_and_writes_the_output(self, tmp_path, monkeypatch):
        board = _Board(_dre6585())
        monkeypatch.setattr(epic_wait, "_linear_ops", lambda: board)
        out_file = tmp_path / "out"
        out_file.write_text("", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            code = epic_wait.main(["hold", "DRE-9103", "--github-output", str(out_file)])
        assert code == 0
        assert "held=true" in out_file.read_text(encoding="utf-8").splitlines()
        assert board.moves == [("DRE-9103", "Backlog")]


class TestTheRegistryAndTheContract:
    def test_the_act_is_declared_as_a_hold_for_the_sweep(self):
        row = pipeline_act.record(epic_wait.ACT)
        assert row["tag"] == "epic-waits"
        assert row["kind"] == "hold"
        assert row["next_actor"] == "reconcile.py"
        assert row["discharges"] is None
        assert row["subscriber"] == "plan.yml"
        assert row["emits"]["file"] == "scripts/epic_wait.py"

    def test_the_registry_checks_clean(self):
        assert pipeline_act.problems() == []

    def test_the_docs_carry_the_row(self):
        text = (ROOT / "docs" / "pipeline-acts.md").read_text(encoding="utf-8")
        assert "`⏸️ epic-waits`" in text
        assert "`epic-waits-on-blocker`" in text

    def test_backlog_entrance_names_the_held_epic(self):
        entrance = _backlog_entrance()
        assert "DRE-6671" in entrance
        assert "blocker epic" in entrance
        assert "DRE-6591" in entrance

    def test_backlog_writers_permit_the_planner(self):
        writers = _backlog_clause("writers")["who"]
        assert "plan.yml" in writers

    def test_the_rendered_contract_is_current(self):
        rendered = (ROOT / "docs" / "lane-contract.md").read_text(encoding="utf-8")
        assert "DRE-6671" in rendered


def _backlog_clause(name: str) -> dict:
    doc = json.loads((ROOT / "config" / "lane-contract.json").read_text(encoding="utf-8"))
    lane = next(l for l in doc["lanes"] if l["name"] == "Backlog")
    return lane["clauses"][name]


def _backlog_entrance() -> str:
    return _backlog_clause("entrance")["text"]
