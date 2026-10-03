"""A state write decided from door data is from-lane-conditional (Stage 2 item 33, review C3).

`guarded_state_write` re-reads the card right before it writes — the DRE-2316
race fix — but refused only a FINISHED card. It never compared the lane or the
labels the decision was made on. That was tolerable while the decision's read
was the sweep's own Linear read, seconds old; on the read door the gap is the
door's max-age as well, so a requeue decided on "In Progress, not held" could
land on a card a person just labelled `needs-human`, or that just moved on.

`cmd_state(..., expect=(lanes...), labels_absent=(labels...))` is the
`cmd_state_if` the review asked for. It reuses the reads `cmd_state` already
pays for — its first read and the guarded pre-write re-read — so it costs ZERO
extra requests, and it refuses (returns False, writes nothing) when either read
shows the card outside the expected lanes or carrying a label the decision read
as absent. It is a keyword on `cmd_state` rather than a new function so every
call site keeps its literal destination lane, which is what
`ready_lane_writers` reads (a new wrapper name would be a writer that check
could not see).

Without the keywords `cmd_state` is byte-for-byte the call it always was.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import linear_ops  # noqa: E402
import ready_lane_writers  # noqa: E402

STATES = {
    "Backlog": ("st-backlog", "backlog"),
    "Todo": ("st-todo", "unstarted"),
    "In Progress": ("st-progress", "started"),
    "In Review": ("st-review", "started"),
    "Done": ("st-done", "completed"),
    "Canceled": ("st-canceled", "canceled"),
}


class FakeCard:
    """One card behind `linear_ops.gql`. `between` runs after the FIRST card
    read and before the second — the race window the guard exists for."""

    def __init__(self, lane: str, labels=(), between=None):
        self.lane = lane
        self.labels = list(labels)
        self.between = between
        self.reads = 0
        self.requests = 0
        self.writes: list[str] = []

    def gql(self, query, variables=None):
        self.requests += 1
        q = " ".join(query.split())
        if "issueUpdate" in q:
            sid = variables["input"]["stateId"]
            name = next(n for n, (i, _t) in STATES.items() if i == sid)
            self.writes.append(name)
            self.lane = name
            return {"issueUpdate": {"success": True}}
        if "workflowStates" in q:
            return {"workflowStates": {"nodes": [
                {"id": i, "name": n, "type": t} for n, (i, t) in STATES.items()]}}
        if "history(" in q:
            return {"issue": {"history": {"nodes": []}}}
        if "comments" in q:
            return {"viewer": {"id": "me"}, "issue": {"comments": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}
        if "issue(id:" in q:
            self.reads += 1
            if self.reads == 2 and self.between:
                self.between(self)
            sid, stype = STATES[self.lane]
            return {"issue": {
                "id": "uuid-1", "identifier": "DRE-1", "title": "A card",
                "team": {"id": "team"},
                "state": {"id": sid, "name": self.lane, "type": stype},
                "labels": {"nodes": [{"name": n} for n in self.labels]},
                "children": {"nodes": []},
            }}
        raise AssertionError(f"unexpected query: {q[:120]}")


@pytest.fixture
def fake(monkeypatch):
    def make(lane, labels=(), between=None):
        card = FakeCard(lane, labels, between)
        monkeypatch.setattr(linear_ops, "gql", card.gql)
        return card
    return make


def test_the_expected_lane_writes(fake):
    card = fake("In Progress")
    assert linear_ops.cmd_state("DRE-1", "Todo", expect=("In Progress",)) is True
    assert card.writes == ["Todo"]


def test_a_card_that_already_left_the_lane_is_not_written(fake, capsys):
    card = fake("In Review")
    assert linear_ops.cmd_state("DRE-1", "Todo", expect=("In Progress",)) is False
    assert card.writes == []
    assert "not 'In Progress'" in capsys.readouterr().out


def test_a_move_inside_the_race_window_is_caught_by_the_prewrite_read(fake):
    def moved(card):
        card.lane = "In Review"
    card = fake("In Progress", between=moved)
    assert linear_ops.cmd_state("DRE-1", "Todo", expect=("In Progress",)) is False
    assert card.writes == []


def test_a_hold_label_the_decision_read_as_absent_refuses(fake):
    card = fake("In Progress", labels=("needs-human",))
    assert linear_ops.cmd_state(
        "DRE-1", "Todo", expect=("In Progress",), labels_absent=("needs-human",)) is False
    assert card.writes == []


def test_a_hold_label_added_inside_the_race_window_refuses(fake):
    def held(card):
        card.labels.append("Needs-Human")
    card = fake("In Progress", between=held)
    assert linear_ops.cmd_state(
        "DRE-1", "Todo", expect=("In Progress",), labels_absent=("needs-human",)) is False
    assert card.writes == []


def test_the_expectation_costs_no_extra_request(fake):
    plain = fake("In Progress")
    linear_ops.cmd_state("DRE-1", "Todo")
    guarded = fake("In Progress")
    linear_ops.cmd_state("DRE-1", "Todo", expect=("In Progress",),
                         labels_absent=("needs-human",))
    assert guarded.requests == plain.requests
    assert guarded.writes == plain.writes == ["Todo"]


def test_a_terminal_target_is_conditional_on_the_first_read(fake):
    card = fake("Todo")
    assert linear_ops.cmd_state("DRE-1", "Canceled", expect=("In Progress",)) is False
    assert card.writes == []
    card = fake("In Progress")
    assert linear_ops.cmd_state("DRE-1", "Canceled", expect=("In Progress",)) is True
    assert card.writes == ["Canceled"]


def test_a_deliberate_park_keeps_its_flag(fake):
    card = fake("In Progress", labels=("needs-human",))
    assert linear_ops.cmd_state("DRE-1", "Backlog", "--park", expect=("In Progress",)) is True
    assert card.writes == ["Backlog"]


def test_the_building_card_reroute_is_conditional_too(fake):
    def moved(card):
        card.lane = "In Review"
    card = fake("In Progress", between=moved)
    assert linear_ops.cmd_state("DRE-1", "Backlog", expect=("In Progress",)) is False
    assert card.writes == []


def test_without_the_keywords_cmd_state_is_unchanged(fake):
    card = fake("In Review")
    assert linear_ops.cmd_state("DRE-1", "Todo") is True
    assert card.writes == ["Todo"]


def test_a_refused_finished_card_reads_false(fake):
    card = fake("Done")
    assert linear_ops.cmd_state("DRE-1", "Todo") is False
    assert card.writes == []


def test_the_writer_check_still_reads_cmd_states_destination():
    params = ready_lane_writers.lane_parameters()
    positions, names = params["cmd_state"]
    assert positions == (1,) and "state_name" in names
    assert "cmd_state" in ready_lane_writers.seam_functions()
