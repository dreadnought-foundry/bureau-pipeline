"""What one lane move costs in Linear requests (Stage 2 fix #9, package BP-3).

A lane move through `linear_ops` used to cost four reads and one write:

    issue          the first read: the card's id, team and lane
    workflowStates the team's lanes, to turn a NAME into Linear's state id
    issue          the pre-write re-read (DRE-2316): refuse a card that went
                   terminal since the decision
    issueUpdate    the write
    history        the read-back (DRE-1877/DRE-2316): put back a terminal
                   state that landed inside the residual window anyway

Two of those are waste when the caller already has what they read:

  * The FIRST read, when the caller HOLDS the card — it decided on the card's
    lane, from the read door or its own earlier read (`held=True`, `--held`, or
    a conditional write: `expect=` is the lane the caller read). Every decision
    that first read made — the finished-card refusal, the building-card reroute
    to Todo, the conditional write's lane and labels, `advance`'s from-lane and
    `--not-epic` — is made on the pre-write re-read instead, which is the
    fresher of the two reads anyway.
  * The WORKFLOW STATES after the first lookup in a process, and every lookup
    when the read door serves them (`BUREAU_READ=on`).

The DRE-2316 re-read and the read-back are never dropped: every test below
that writes a non-terminal lane asserts both are still there, in order.

These tests run against a fake Linear at the TRANSPORT (`urlopen`), so the
count is the real one `linear_ops` prints as `linear-calls:` (#683).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, door_env  # noqa: E402

TEAM = "team-uuid-dre"
STATES = [
    {"id": "st-backlog", "name": "Backlog", "type": "backlog"},
    {"id": "st-todo", "name": "Todo", "type": "unstarted"},
    {"id": "st-progress", "name": "In Progress", "type": "started"},
    {"id": "st-review", "name": "In Review", "type": "started"},
    {"id": "st-planning", "name": "Planning", "type": "unstarted"},
    {"id": "st-done", "name": "Done", "type": "completed"},
    {"id": "st-canceled", "name": "Canceled", "type": "canceled"},
]
BY_NAME = {s["name"]: s for s in STATES}
BY_ID = {s["id"]: s for s in STATES}


class _Response:
    def __init__(self, body: dict):
        self._data = json.dumps(body).encode()
        self.headers = {}

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeLinear:
    """Linear as it is NOW, at the transport. Records each request's KIND:
    `issue`, `states` (by team id), `states:key` (by team key), `comments`,
    `write`, `history`."""

    def __init__(self, **cards):
        # ident -> {"lane": name, "labels": [...], "title": str, "children": bool}
        self.cards = {ident: dict(spec) for ident, spec in cards.items()}
        self.kinds: list[str] = []
        self.writes: list[tuple[str, str]] = []
        self.team_keys: list[str] = []

    def card(self, ident):
        return self.cards[ident]

    def urlopen(self, req, timeout=None):
        payload = json.loads(req.data)
        q = " ".join(payload["query"].split())
        v = payload.get("variables") or {}
        return _Response({"data": self._answer(q, v)})

    def _answer(self, q: str, v: dict) -> dict:
        if "issueUpdate" in q:
            self.kinds.append("write")
            ident = self._ident(v["id"])
            lane = BY_ID[v["input"]["stateId"]]["name"]
            self.writes.append((ident, lane))
            self.cards[ident]["lane"] = lane
            return {"issueUpdate": {"success": True}}
        if "history(" in q:
            self.kinds.append("history")
            return {"issue": {"history": {"nodes": []}}}
        if "workflowStates" in q:
            if "key:" in q:
                self.kinds.append("states:key")
                self.team_keys.append(v.get("teamKey"))
            else:
                self.kinds.append("states")
                assert v.get("teamId") == TEAM
            return {"workflowStates": {"nodes": [dict(s) for s in STATES]}}
        if "comments(" in q:
            self.kinds.append("comments")
            return {"viewer": {"id": "me"},
                    "issue": {"comments": {"nodes": [],
                                           "pageInfo": {"hasNextPage": False,
                                                        "endCursor": None}}}}
        if "issue(id" in q:
            self.kinds.append("issue")
            ident = v["id"]
            c = self.cards[ident]
            s = BY_NAME[c["lane"]]
            return {"issue": {
                "id": f"uuid-{ident}", "identifier": ident,
                "title": c.get("title") or f"Card {ident}",
                "team": {"id": TEAM},
                "state": dict(s),
                "labels": {"nodes": [{"name": n} for n in c.get("labels", ())]},
                "children": {"nodes": [{"id": "kid"}] if c.get("children") else []},
            }}
        raise AssertionError(f"unexpected Linear query: {q[:160]}")

    def _ident(self, issue_id: str) -> str:
        return issue_id[len("uuid-"):] if issue_id.startswith("uuid-") else issue_id


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_PIPELINE_REF",
                 "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    reset = getattr(linear_ops, "reset_workflow_states", None)
    if reset is not None:
        reset()
    linear_ops._card_memo.clear()
    yield
    if reset is not None:
        reset()


@contextlib.contextmanager
def linear(monkeypatch, **cards):
    fake = FakeLinear(**cards)
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
    yield fake


def _quiet(fn, *a, **kw):
    out = io.StringIO()
    with redirect_stdout(out):
        result = fn(*a, **kw)
    return result, out.getvalue()


@contextlib.contextmanager
def door(monkeypatch, nodes=None, *, route=None, mode="on"):
    """The real client against a real HTTP fake door that serves `nodes` as
    the workflow states (or `route`, an `(status, body)` answer)."""
    with FakeIssuer() as issuer, FakeDoor({}) as d:
        for key, value in door_env(door_url=d.url, issuer=issuer, mode=mode).items():
            monkeypatch.setenv(key, value)
        d.routes["/workflow-states"] = route or (
            200, d.envelope(nodes if nodes is not None else [dict(s) for s in STATES],
                            nodes_key="workflowStates"))
        yield d


# ── BUREAU_READ off, no held card: today's requests, minus the repeated states ──


def test_off_first_unheld_move_is_exactly_todays_requests(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin:
        moved, _ = _quiet(linear_ops.cmd_state, "DRE-1", "In Review")
    assert moved is True
    assert lin.kinds == ["issue", "states", "issue", "write", "history"]
    assert lin.writes == [("DRE-1", "In Review")]
    assert linear_ops.requests_made() == 5


def test_off_a_second_move_in_one_process_reads_the_states_once(monkeypatch):
    cards = {"DRE-1": {"lane": "In Progress"}, "DRE-2": {"lane": "In Progress"}}
    with linear(monkeypatch, **cards) as lin:
        _quiet(linear_ops.cmd_state, "DRE-1", "In Review")
        first = list(lin.kinds)
        lin.kinds.clear()
        _quiet(linear_ops.cmd_state, "DRE-2", "In Review")
    assert first == ["issue", "states", "issue", "write", "history"]
    # The DRE-2316 re-read and the read-back are both still there.
    assert lin.kinds == ["issue", "issue", "write", "history"]
    assert lin.writes == [("DRE-1", "In Review"), ("DRE-2", "In Review")]


def test_off_the_building_card_reroute_looks_the_states_up_once(monkeypatch):
    """In Progress → Backlog without `--park` is re-routed to Todo (DRE-1885).
    The reroute used to look Todo up with a second `workflowStates` request."""
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin:
        moved, out = _quiet(linear_ops.cmd_state, "DRE-1", "Backlog")
    assert moved is True
    assert lin.writes == [("DRE-1", "Todo")]
    assert "re-queued to 'Todo'" in out
    assert lin.kinds == ["issue", "states", "issue", "comments", "write", "history"]


def test_off_never_asks_the_door(monkeypatch):
    def refuse(**kw):
        raise AssertionError("the door was asked with BUREAU_READ off")

    monkeypatch.setattr(bureau_read, "workflow_states", refuse)
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}):
        moved, _ = _quiet(linear_ops.cmd_state, "DRE-1", "In Review", held=True)
    assert moved is True


def test_a_lane_missing_from_the_cached_states_is_read_again_once(monkeypatch):
    """A cache must not turn a lane added mid-run into "no state named": a
    name the cached list lacks is asked of Linear once more before failing."""
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"},
                                "DRE-2": {"lane": "In Progress"}}) as lin:
        _quiet(linear_ops.cmd_state, "DRE-1", "In Review")
        lin.kinds.clear()
        with pytest.raises(linear_ops.LinearError, match="no state named"):
            _quiet(linear_ops.cmd_state, "DRE-2", "Nowhere")
    assert lin.kinds == ["issue", "states"]
    assert lin.writes == [("DRE-1", "In Review")]


# ── A held card: no first read; every decision on the pre-write re-read ──────


def test_a_held_move_sends_no_first_read(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin:
        moved, out = _quiet(linear_ops.cmd_state, "DRE-1", "In Review", held=True)
    assert moved is True
    assert "DRE-1 → In Review" in out
    # states (by the team key: a held card carries no team id), the DRE-2316
    # re-read, the write, the read-back.
    assert lin.kinds == ["states:key", "issue", "write", "history"]
    assert lin.team_keys == ["DRE"]
    assert lin.writes == [("DRE-1", "In Review")]


def test_a_held_move_with_the_states_in_hand_costs_two_reads_and_the_write(monkeypatch):
    cards = {"DRE-1": {"lane": "In Progress"}, "DRE-2": {"lane": "In Progress"}}
    with linear(monkeypatch, **cards) as lin:
        _quiet(linear_ops.cmd_state, "DRE-1", "In Review", held=True)
        before = linear_ops.requests_made()
        lin.kinds.clear()
        _quiet(linear_ops.cmd_state, "DRE-2", "In Review", held=True)
    assert lin.kinds == ["issue", "write", "history"]
    assert linear_ops.requests_made() - before == 3


def test_the_cli_held_flag_is_a_held_card(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin:
        moved, _ = _quiet(linear_ops.cmd_state, "DRE-1", "In Review", "--held")
    assert moved is True
    assert lin.kinds == ["states:key", "issue", "write", "history"]


def test_a_conditional_write_is_a_held_card(monkeypatch):
    """`expect=` is the lane the caller READ (Stage 2 item 33): it holds the
    card, so the first read is waste. The condition is checked on the
    pre-write re-read."""
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin:
        moved, _ = _quiet(linear_ops.cmd_state, "DRE-1", "Todo",
                          expect=("In Progress",), labels_absent=("needs-human",))
    assert moved is True
    assert lin.kinds == ["states:key", "issue", "comments", "write", "history"]
    assert lin.writes == [("DRE-1", "Todo")]


def test_a_conditional_write_is_refused_on_the_live_lane(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Review"}}) as lin:
        moved, out = _quiet(linear_ops.cmd_state, "DRE-1", "Todo",
                            expect=("In Progress",))
    assert moved is False
    assert lin.writes == []
    assert "not writing" in out
    assert lin.kinds == ["states:key", "issue"]


def test_a_conditional_write_is_refused_on_a_live_label(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress",
                                          "labels": ["needs-human"]}}) as lin:
        moved, _ = _quiet(linear_ops.cmd_state, "DRE-1", "Todo",
                          expect=("In Progress",), labels_absent=("needs-human",))
    assert moved is False
    assert lin.writes == []


def test_a_held_card_that_finished_since_is_never_reopened(monkeypatch):
    """DRE-1877/DRE-2316: the held card said In Progress; it is Done now."""
    with linear(monkeypatch, **{"DRE-1": {"lane": "Done"}}) as lin:
        moved, out = _quiet(linear_ops.cmd_state, "DRE-1", "Todo", held=True)
    assert moved is False
    assert lin.writes == []
    assert "terminal" in out
    assert lin.kinds == ["states:key", "issue"]


def test_a_held_building_card_is_rerouted_to_todo_on_the_live_read(monkeypatch):
    """DRE-1885 decided on the re-read: the caller's card was in Todo when it
    decided on Backlog, the live card is building — it goes to Todo."""
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin:
        moved, out = _quiet(linear_ops.cmd_state, "DRE-1", "Backlog", held=True)
    assert moved is True
    assert lin.writes == [("DRE-1", "Todo")]
    assert "re-queued to 'Todo'" in out
    assert lin.kinds == ["states:key", "issue", "comments", "write", "history"]


@pytest.mark.parametrize("lane,labels,flags", [
    ("Todo", [], ()),                       # not building: an ordinary park
    ("In Progress", [], ("--park",)),       # a deliberate park
    ("In Progress", ["needs-human"], ()),   # a human owns it
])
def test_a_held_park_lands_in_backlog_when_the_live_card_allows_it(
        monkeypatch, lane, labels, flags):
    with linear(monkeypatch, **{"DRE-1": {"lane": lane, "labels": labels}}) as lin:
        moved, _ = _quiet(linear_ops.cmd_state, "DRE-1", "Backlog", *flags, held=True)
    assert moved is True
    assert lin.writes == [("DRE-1", "Backlog")]
    assert lin.kinds == ["states:key", "issue", "write", "history"]


def test_a_held_move_to_the_lane_it_is_in_writes_nothing(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Review"}}) as lin:
        moved, out = _quiet(linear_ops.cmd_state, "DRE-1", "In Review", held=True)
    assert moved is True
    assert lin.writes == []
    assert "already in" in out


def test_a_held_card_moved_to_a_terminal_lane_is_still_read_first(monkeypatch):
    """A terminal target skips the pre-write re-read, so a held card is read
    once as before: the no-op check never runs on a snapshot."""
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Review"}}) as lin:
        moved, _ = _quiet(linear_ops.cmd_state, "DRE-1", "Done", held=True)
    assert moved is True
    assert lin.writes == [("DRE-1", "Done")]
    assert lin.kinds == ["states:key", "issue", "write"]


def test_a_held_terminal_move_on_a_card_already_there_writes_nothing(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "Done"}}) as lin:
        moved, _ = _quiet(linear_ops.cmd_state, "DRE-1", "Done", held=True)
    assert moved is True
    assert lin.writes == []


# ── advance: the from-lane check on the live read ───────────────────────────


def test_a_held_advance_checks_its_from_lane_on_the_live_read(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Review"}}) as lin:
        _, out = _quiet(linear_ops.cmd_advance, "DRE-1", "In Review", "In Progress,Todo",
                        held=True)
    assert lin.writes == []
    assert "not advancing" in out
    assert lin.kinds == ["states:key", "issue"]


def test_a_held_advance_writes_with_both_guards(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin:
        _quiet(linear_ops.cmd_advance, "DRE-1", "In Review", "In Progress,Todo", "--held")
    assert lin.writes == [("DRE-1", "In Review")]
    assert lin.kinds == ["states:key", "issue", "write", "history"]


def test_a_held_advance_refuses_an_epic_on_the_live_read(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress", "children": True}}) as lin:
        _, out = _quiet(linear_ops.cmd_advance, "DRE-1", "In Review", "In Progress",
                        "--not-epic", "--held")
    assert lin.writes == []
    assert "EPIC" in out


def test_an_unheld_advance_is_unchanged(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin:
        _quiet(linear_ops.cmd_advance, "DRE-1", "In Review", "In Progress,Todo")
    assert lin.kinds == ["issue", "states", "issue", "write", "history"]


# ── BUREAU_READ=on: the states from the read door ───────────────────────────


def test_on_the_states_come_from_the_door_once_per_process(monkeypatch):
    cards = {"DRE-1": {"lane": "In Progress"}, "DRE-2": {"lane": "In Progress"}}
    with linear(monkeypatch, **cards) as lin, door(monkeypatch) as d:
        _quiet(linear_ops.cmd_state, "DRE-1", "In Review", held=True)
        _quiet(linear_ops.cmd_state, "DRE-2", "In Review", held=True)
    assert lin.kinds == ["issue", "write", "history"] * 2
    assert lin.writes == [("DRE-1", "In Review"), ("DRE-2", "In Review")]
    assert len(d.asked("/workflow-states")) == 1


def test_on_an_unheld_move_takes_its_states_from_the_door(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin, door(monkeypatch):
        _quiet(linear_ops.cmd_state, "DRE-1", "In Review")
    assert lin.kinds == ["issue", "issue", "write", "history"]


def test_on_a_lane_the_door_does_not_name_is_read_from_linear(monkeypatch):
    """The door's states are the ones it has SEEN (`linear_workflow_state` is
    filled from deliveries): a missing name is a miss, never "no such lane"."""
    seen = [dict(BY_NAME["Todo"]), dict(BY_NAME["In Progress"])]
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin, \
            door(monkeypatch, seen):
        _quiet(linear_ops.cmd_state, "DRE-1", "In Review", held=True)
    assert lin.kinds == ["states:key", "issue", "write", "history"]
    assert lin.writes == [("DRE-1", "In Review")]


def test_on_a_lane_the_door_names_twice_is_read_from_linear(monkeypatch):
    twice = [dict(s) for s in STATES] + [{"id": "other-team-review", "name": "In Review",
                                           "type": "started"}]
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin, \
            door(monkeypatch, twice):
        _quiet(linear_ops.cmd_state, "DRE-1", "In Review", held=True)
    assert lin.kinds == ["states:key", "issue", "write", "history"]
    assert lin.writes == [("DRE-1", "In Review")]


@pytest.mark.parametrize("reason", ["stale", "missing-field"])
def test_on_a_door_that_cannot_answer_falls_back_to_linear(monkeypatch, reason):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin:
        with FakeIssuer() as issuer, FakeDoor({}) as d:
            for key, value in door_env(door_url=d.url, issuer=issuer).items():
                monkeypatch.setenv(key, value)
            d.routes["/workflow-states"] = d.unknown(reason)
            _quiet(linear_ops.cmd_state, "DRE-1", "In Review", held=True)
    assert lin.kinds == ["states:key", "issue", "write", "history"]
    assert lin.writes == [("DRE-1", "In Review")]


def test_on_a_closed_door_falls_back_to_linear(monkeypatch):
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin, \
            door(monkeypatch, route=(503, {"error": {"code": "DOOR_CLOSED"}})):
        _quiet(linear_ops.cmd_state, "DRE-1", "In Review", held=True)
    assert lin.writes == [("DRE-1", "In Review")]
    assert lin.kinds == ["states:key", "issue", "write", "history"]


def test_on_linear_hold_the_lane_move_still_looks_its_lane_up_and_says_so(monkeypatch):
    """`linear-hold` makes a READER skip its phase rather than spend the held
    bucket (item 34). A lane move is a write that goes to Linear regardless,
    so skipping its lookup would only drop the move: it reads the lane from
    Linear and says why, in one line."""
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin:
        with FakeIssuer() as issuer, FakeDoor({}) as d:
            for key, value in door_env(door_url=d.url, issuer=issuer).items():
                monkeypatch.setenv(key, value)
            d.routes["/workflow-states"] = d.unknown("linear-hold")
            moved, out = _quiet(linear_ops.cmd_state, "DRE-1", "In Review", held=True)
    assert moved is True
    assert lin.writes == [("DRE-1", "In Review")]
    assert "linear-hold" in out
    assert lin.kinds == ["states:key", "issue", "write", "history"]


def test_shadow_reads_the_door_and_moves_on_linears_answer(monkeypatch):
    wrong = [dict(s) for s in STATES if s["name"] != "In Review"] + [
        {"id": "st-review-door", "name": "In Review", "type": "started"}]
    with linear(monkeypatch, **{"DRE-1": {"lane": "In Progress"}}) as lin, \
            door(monkeypatch, wrong, mode="shadow") as d:
        _, out = _quiet(linear_ops.cmd_state, "DRE-1", "In Review", held=True)
    assert len(d.asked("/workflow-states")) == 1
    assert lin.writes == [("DRE-1", "In Review")]  # Linear's id, not the door's
    assert lin.kinds == ["states:key", "issue", "write", "history"]
    assert "read-door-diff:" in out and "In Review" in out


def test_the_test_session_resets_the_states_between_tests():
    """conftest drops the cached states with the rest of the process state:
    one test's fake lanes must never be the next test's."""
    import conftest

    assert "reset_workflow_states" in Path(conftest.__file__).read_text()
    assert linear_ops._workflow_states == {}
