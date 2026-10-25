"""`unpark` restarts a build in the start lane (DRE-7109).

The write layer's two restarts — `unpark` and the building-card reroute
(DRE-1885, pinned in `test_state_building_not_parked.py`) — write
`lane_contract.START_LANE` (DRE-7105) and no literal — `In Progress` since the
flip card set `build_start.lane` (it was `Todo` before), and every test here
drives the shipped contract and the flipped one through one parametrized
fixture.

After the flip a card can already be in the start lane when it is restarted,
and a same-lane write fires no webhook: the relay never sees it and nothing
starts. So the write layer fires the build dispatch itself, once, and says so —
the shape `limit_recovery`'s build branch already uses.

The epic refusal stays keyed on the retiring start lane (DRE-5316): an epic
entering `In Progress` is the CEO's approval and must stay writable. The
contract names no retiring start lane since DRE-7660, so the refusal is a
no-op there and is exercised below against a stand-in `Todo`.
"""

import copy
import io
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dead_run  # noqa: E402
import epic_todo_gate  # noqa: E402
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402

IN_PLACE = "re-dispatched in place (a same-lane write fires no webhook)"


def _flipped_contract() -> dict:
    """The shipped contract with the one word the flip card turns."""
    doc = copy.deepcopy(lane_contract.load())  # load() is cached, shared
    doc["build_start"]["lane"] = "In Progress"
    assert lane_contract.build_start_problems(doc) == []
    return doc


CONTRACTS = {
    "shipped": lambda: lane_contract.load(),
    "flipped": _flipped_contract,
}


@pytest.fixture(params=sorted(CONTRACTS))
def start_lane(request, monkeypatch):
    """START_LANE under each contract, set where the write layer reads it."""
    lane = lane_contract.start_lane(CONTRACTS[request.param]())
    monkeypatch.setattr(lane_contract, "START_LANE", lane)
    return lane


class _Board:
    """A Linear double: one card in `lane`, the board's lanes, and a record of
    every state the card was written to and every build dispatch fired."""

    STATES = {
        "Queued": ("st-queued", "unstarted"),
        "Todo": ("st-todo", "unstarted"),
        "In Progress": ("st-inprogress", "started"),
        "In Review": ("st-inreview", "started"),
        "Done": ("st-done", "completed"),
    }

    def __init__(self, lane, *, title="a parked card", labels=("repo:atlas",)):
        sid, stype = self.STATES[lane]
        self.issue = {
            "id": "card-uuid",
            "identifier": "DRE-2308",
            "title": title,
            "description": "the card",
            "team": {"id": "team-1"},
            "state": {"id": sid, "name": lane, "type": stype},
            "labels": {"nodes": [{"name": n} for n in labels]},
            "children": {"nodes": []},
        }
        self.order = []
        self.fired = []

    def gql(self, query, variables=None):
        q = " ".join(query.split())
        if "issueUpdate" in q:
            sid = (variables or {})["input"]["stateId"]
            self.order.append(("state", sid))
            name = next(n for n, (i, _) in self.STATES.items() if i == sid)
            self.issue["state"] = {"id": sid, "name": name, "type": self.STATES[name][1]}
            return {"issueUpdate": {"success": True}}
        if "workflowStates" in q:
            return {"workflowStates": {"nodes": [
                {"id": sid, "name": name, "type": stype}
                for name, (sid, stype) in self.STATES.items()]}}
        if "history" in q:
            return {"issue": {"history": {"nodes": []}}}
        if "issue(id: $id)" in q:
            return {"issue": copy.deepcopy(self.issue)}
        raise AssertionError(f"unexpected gql query: {q}")

    def fire(self, card, repo, **kwargs):
        self.order.append(("fire", card["identifier"]))
        self.fired.append((card["identifier"], repo, kwargs))
        return True, ""


def _run(board, fn, *args, fire_ok=True, **kwargs):
    def fire(card, repo, **kw):
        board.fire(card, repo, **kw)
        return (True, "") if fire_ok else (False, "dispatch 403")

    buf = io.StringIO()
    with patch.object(linear_ops, "gql", side_effect=board.gql), \
            patch.object(linear_ops.plan_run, "fire", side_effect=fire), \
            patch.object(linear_ops, "comment_bodies", return_value=[]), \
            patch.object(linear_ops, "remove_label",
                         side_effect=lambda *a: board.order.append(("label", a[1]))), \
            patch.object(linear_ops, "cmd_comment",
                         side_effect=lambda *a, **k: board.order.append(("comment", a[1]))), \
            patch.object(epic_todo_gate, "post_refusal", return_value=True), \
            redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


# --------------------------------------------------------------------------
# unpark writes START_LANE, after its marker
# --------------------------------------------------------------------------


def test_unpark_on_a_queued_card_writes_the_start_lane_after_its_marker(start_lane):
    board = _Board("Queued", labels=("repo:atlas", dead_run.HOLD_LABEL))
    _, out = _run(board, linear_ops.cmd_unpark, "DRE-2308")
    steps = [step for step, _ in board.order]
    assert steps == ["label", "comment", "state"], board.order
    assert dead_run.RESET_TAG in board.order[1][1]
    assert board.order[2][1] == _Board.STATES[start_lane][0], (
        f"unpark must write the start lane {start_lane!r}; got {board.order}"
    )
    assert board.fired == [], "a lane move fires its own webhook — no second dispatch"
    assert f"back in {start_lane}" in out


def test_unpark_on_a_card_already_in_the_start_lane_fires_the_dispatch_once(start_lane):
    board = _Board(start_lane, labels=("repo:atlas", dead_run.HOLD_LABEL))
    _, out = _run(board, linear_ops.cmd_unpark, "DRE-2308")
    steps = [step for step, _ in board.order]
    assert steps == ["label", "comment", "fire"], (
        "no state write for a card already there, the marker before the "
        f"dispatch, and exactly one dispatch; got {board.order}"
    )
    assert board.fired[0][:2] == ("DRE-2308", "EveryBite/atlas"), board.fired
    assert IN_PLACE in out


def test_unpark_says_so_when_the_in_place_dispatch_fails(start_lane):
    board = _Board(start_lane)
    _, out = _run(board, linear_ops.cmd_unpark, "DRE-2308", fire_ok=False)
    assert "dispatch 403" in out
    assert "un-parked" not in out


def test_unpark_asks_the_state_move_to_restart_the_build():
    with patch.object(linear_ops, "remove_label"), \
            patch.object(linear_ops, "cmd_comment"), \
            patch.object(linear_ops, "cmd_state", return_value=True) as cmd_state:
        linear_ops.cmd_unpark("DRE-2308")
    cmd_state.assert_called_once_with("DRE-2308", lane_contract.START_LANE, restart=True)


# --------------------------------------------------------------------------
# the epic refusal stays on Todo
# --------------------------------------------------------------------------

EPIC = "[EPIC] a roll-up"


def _write(board, lane):
    sid, stype = _Board.STATES[lane]
    issue = linear_ops.get_issue("DRE-2308")
    return linear_ops.guarded_state_write("DRE-2308", issue, sid, stype, lane)


def test_an_epic_written_to_todo_is_still_refused(monkeypatch):
    # The contract names no retiring start lane since DRE-7660, so the rule is
    # exercised against a stand-in.
    monkeypatch.setattr(epic_todo_gate, "TODO", "Todo")
    board = _Board("Queued", title=EPIC)
    wrote, out = _run(board, _write, board, "Todo")
    assert wrote is False
    assert not [s for s in board.order if s[0] == "state"], board.order
    assert epic_todo_gate.TAG in out


def test_the_shipped_contract_refuses_no_epic_write():
    """No retiring start lane (DRE-7660): the refusal has nothing to key on."""
    assert epic_todo_gate.TODO is None
    board = _Board("Queued", title=EPIC)
    wrote, out = _run(board, _write, board, "Todo")
    assert wrote is True
    assert board.order == [("state", _Board.STATES["Todo"][0])]
    assert epic_todo_gate.TAG not in out


def test_an_epic_written_to_in_progress_is_not_refused():
    board = _Board("Queued", title=EPIC)
    wrote, _ = _run(board, _write, board, "In Progress")
    assert wrote is True
    assert board.order == [("state", _Board.STATES["In Progress"][0])]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
