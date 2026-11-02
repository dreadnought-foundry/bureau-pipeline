"""The Dispatch proof runs step reads through the read door at five minutes (DRE-8910).

`scripts/proof_dispatch.py`'s pass used to read Hand-work and In Review
through the sweep's own board read (the door at 120 seconds, then Linear on
`stale`), and every candidate's card, whole thread and named cards' states
straight from Linear. In `BUREAU_READ=on` the pass's `DoorReads` now asks the
door first, with `X-Bureau-Max-Age: 300`, and prints one
`reconcile.door_read_line` per door request:

* `lane` — ONE `scope=repo` board read of `reconcile.DOOR_WORK_LANES` per
  process (`proof-dispatch-lane`), filtered to the lane asked; Green Light
  is still `LinearReads.lane`, with no door request;
* `card` — `/cards` with relations (`proof-dispatch-card`), and the epic's
  children from Linear in one small query (`DoorReads._epic_children`);
* `thread` — `/cards?comments=all` (`proof-dispatch-thread`), the viewer
  from `linear_ops.viewer_id()`;
* `states` — `/cards` without relations (`proof-dispatch-states`).

On an UNKNOWN each falls back to the `LinearReads` method it overrides, which
these tests stub and assert was called — never the Linear request that method
makes underneath, which is `linear_ops`'s business (DRE-8898). `off` and
`shadow` call exactly today's `LinearReads` methods and ask the door nothing.

The door and the issuer are `tests/bureau_read_fakes.py`, on 127.0.0.1; Linear
is a stub that records every query.
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
import proof_dispatch  # noqa: E402
import reconcile  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, door_env  # noqa: E402
from bureau_read_fakes import card as door_card  # noqa: E402
from test_proof_dispatch import (  # noqa: E402
    EPIC,
    NOW,
    REPO,
    SLUG,
    VIEWER,
    Board,
    Harness,
    comment,
    detail,
    fake_voices,
    lane_card,
)

LINE = re.compile(
    r"^read-door: read decision=(?P<decision>\S+) verdict=(?P<verdict>FRESH|UNKNOWN) "
    r"age=(?P<age>\d+|-) max-age=(?P<max_age>\d+) reason=(?P<reason>\S+)$")
CANDIDATE = "DRE-5930"
GREEN = "DRE-5940"
REVIEWED = "DRE-5950"
WORKING = "DRE-5960"
WORK_LANES = "In Progress,In Review,Hand-work"
#: The siblings `detail()` gives the epic: the build cards, both Done.
SIBLINGS = detail()["parent"]["children"]


def _door_node(ident: str, lane: str, *, title: str | None = None,
               window=None) -> dict:
    """A card in the door's full shape, carrying the comment window and the
    labels `lane_card` gives the same card on today's board."""
    today = lane_card(ident, title=title, state=lane, window=window)
    node = door_card(ident, lane, parent=EPIC, title=today["title"],
                     description=today["description"],
                     blockers=(("DRE-5921", "Done"), ("DRE-5922", "Done")))
    node["labels"] = today["labels"]
    node["comments"] = {"pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": today["comments"]["nodes"]}
    return node


def _green() -> dict:
    return lane_card(GREEN, title="bureau-pipeline: a build waiting on the CEO",
                     state="Green Light")


WORLD = (
    _door_node(CANDIDATE, "Hand-work"),
    _door_node(REVIEWED, "In Review", title="bureau-pipeline: a build in review"),
    _door_node(WORKING, "In Progress", title="bureau-pipeline: a build in flight"),
    door_card("DRE-5921", "Done"),
    door_card("DRE-5922", "Done"),
)


class Door(FakeDoor):
    """The fake door, answering UNKNOWN `reason` for one kind of read:
    `lane` (/board), `card` (/cards with relations), `thread`
    (/cards?comments=all) or `states` (/cards, neither)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unknown_for: dict[str, str] = {}

    @staticmethod
    def kind(endpoint: str, query: dict) -> str:
        if endpoint == "/board":
            return "lane"
        if query.get("comments") == "all":
            return "thread"
        return "card" if query.get("relations") == "1" else "states"

    def answer(self, endpoint, query):
        reason = self.unknown_for.get(self.kind(endpoint, query))
        if reason:
            return self.unknown(reason)
        return super().answer(endpoint, query)


class Linear:
    """`linear_ops.gql`, answering the reads a door pass still makes and
    recording every query: the Green Light lane, the epic's children, the
    viewer, and a marked card's relation times."""

    def __init__(self, green=(), relations=None):
        self.green = list(green)
        self.relations = relations or {}
        #: What a top-up read of a card's relations answers.
        self.topup = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}
        self.reads: list[tuple[str, dict]] = []

    def __call__(self, query, variables=None):
        variables = dict(variables or {})
        self.reads.append((query, variables))
        if "viewer" in query:
            return {"viewer": {"id": VIEWER}}
        if "states" in variables:
            nodes = [c for c in self.green if c["state"]["name"] in variables["states"]]
            return {"issues": {"nodes": nodes,
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if query == proof_dispatch.EPIC_CHILDREN_QUERY:
            return {"issue": {"parent": {"children": SIBLINGS}}}
        if query == proof_dispatch.LANE_RELATIONS_QUERY:
            return {"issues": {"nodes": [
                {"identifier": i, "inverseRelations": page}
                for i, page in self.relations.items()]}}
        if "inverseRelations(first: 100" in query:
            return {"issue": {"inverseRelations": self.topup}}
        return {}

    def kinds(self) -> list[str]:
        """Each recorded query, named."""
        out = []
        for query, variables in self.reads:
            if "viewer" in query:
                out.append("viewer")
            elif "states" in variables:
                out.append(f"lane:{','.join(variables['states'])}")
            elif query == proof_dispatch.EPIC_CHILDREN_QUERY:
                out.append(f"epic-children:{variables['id']}")
            elif query == proof_dispatch.LANE_RELATIONS_QUERY:
                out.append("lane-relations")
            elif "inverseRelations(first: 100" in query:
                out.append(f"relations-top-up:{variables['id']}")
            else:
                out.append(" ".join(query.split())[:60])
        return out


class Today:
    """Today's `LinearReads` methods, stubbed on the class and recorded — the
    methods a door fallback, `off` and `shadow` call."""

    def __init__(self, monkeypatch, board: Board):
        self.board = board
        self.calls: list[tuple] = []
        for name in ("lane", "card", "thread", "states"):
            monkeypatch.setattr(proof_dispatch.LinearReads, name, self._stub(name))

    def _stub(self, name):
        def called(_reads, arg):
            self.calls.append((name, arg if isinstance(arg, str) else tuple(arg)))
            if name == "states":
                return {i: "Done" for i in arg}
            got = getattr(self.board, name)(arg)
            if name == "card":
                got = {**got, "identifier": arg}
            return got
        return called


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY",
                 "GITHUB_EVENT_NAME", "GITHUB_HEAD_REF", "PROOF_SHADOW_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bureau_read, "TOTAL_TIMEOUT", 0.5)
    monkeypatch.setattr(reconcile, "_inverse_topup_refused", [])
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()
    yield
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()


@pytest.fixture
def linear(monkeypatch):
    stub = Linear(green=[_green()])
    monkeypatch.setattr(linear_ops, "gql", stub)
    return stub


@contextlib.contextmanager
def door_at(monkeypatch, *cards, mode="on"):
    world = {c["identifier"]: c for c in (cards or WORLD)}
    with FakeIssuer() as issuer, Door(world) as door:
        for key, value in door_env(door_url=door.url, issuer=issuer, mode=mode).items():
            monkeypatch.setenv(key, value)
        yield door


def _today_board() -> Board:
    """The same world on today's injected board."""
    return Board(hand=[lane_card(CANDIDATE)], green=[_green()],
                 review=[lane_card(REVIEWED, title="bureau-pipeline: a build in review",
                                   state="In Review")])


def _pass(h: Harness, *, linear=None):
    """One pass with the production reads (`linear` omitted, as `main` runs
    it) and everything else the harness's."""
    return proof_dispatch.sweep(
        REPO, SLUG, live=True, linear=linear, read=lambda path: None,
        find_pr=h.find_pr, run_state=h.run_state, release=h.release, fire=h.fire,
        voices=fake_voices, now=NOW, find_record=h.find_record,
        read_record=h.read_record, open_heads=h.open_heads, file_fix=h.file_fix)


def _lines(out: str) -> list[dict]:
    """Every `read-door: read` line, each held to the contract's shape."""
    found = []
    for raw in out.splitlines():
        if raw.startswith("read-door: read "):
            match = LINE.match(raw)
            assert match, f"not the contract's shape: {raw!r}"
            found.append(match.groupdict())
    return found


FIRST_RUN = [(CANDIDATE, REPO, "first proof run", "proof-execute")]


def _today(monkeypatch) -> Harness:
    """Today's pass over the same world: what the door pass must match."""
    h = Harness(monkeypatch, _today_board())
    h.sweep()
    assert h.fired == FIRST_RUN
    return h


# ── a whole pass, every door answer FRESH ──────────────────────────────────


def test_a_fresh_door_pass_reads_linear_only_for_the_viewer_green_light_and_the_epic(
        monkeypatch, capsys, linear):
    today = _today(monkeypatch)
    capsys.readouterr()
    with door_at(monkeypatch) as door:
        h = Harness(monkeypatch, None)
        tally = _pass(h)
    assert sorted(linear.kinds()) == sorted(
        ["viewer", "lane:Green Light", f"epic-children:{CANDIDATE}"])
    # The same decision and the same receipt as today.
    assert h.fired == FIRST_RUN and tally.dispatched == 1
    assert h.posted == today.posted
    assert h.order == [("fire", CANDIDATE), ("comment", CANDIDATE)]
    boards = door.asked("/board")
    assert [(r["query"]["lanes"], r["query"]["scope"]) for r in boards] == [
        (WORK_LANES, "repo")]
    cards = door.asked("/cards")
    assert [(r["query"]["ids"], r["query"]["comments"], r["query"]["relations"])
            for r in cards] == [(CANDIDATE, "50", "1"), (CANDIDATE, "all", "0")]


def test_every_door_request_in_the_pass_sends_300_and_prints_its_line(
        monkeypatch, capsys, linear):
    with door_at(monkeypatch) as door:
        _pass(Harness(monkeypatch, None))
    out = capsys.readouterr().out
    assert door.requests
    assert {r["headers"]["x-bureau-max-age"] for r in door.requests} == {"300"}
    lines = _lines(out)
    assert [(x["decision"], x["verdict"], x["max_age"]) for x in lines] == [
        ("proof-dispatch-lane", "FRESH", "300"),
        ("proof-dispatch-card", "FRESH", "300"),
        ("proof-dispatch-thread", "FRESH", "300"),
    ]
    assert len(lines) == len(door.requests)
    assert proof_dispatch.DISPATCH_MAX_AGE == 300


# ── lane ────────────────────────────────────────────────────────────────────


def test_two_work_lanes_are_one_door_request_and_green_light_is_none(
        monkeypatch, capsys, linear):
    today = Today(monkeypatch, _today_board())
    with door_at(monkeypatch) as door:
        reads = proof_dispatch.DoorReads()
        hand = reads.lane("Hand-work")
        review = reads.lane("In Review")
        assert len(door.requests) == 1
        green = reads.lane("Green Light")
    assert [c["identifier"] for c in hand] == [CANDIDATE]
    assert [c["identifier"] for c in review] == [REVIEWED]
    (asked,) = door.requests
    assert asked["path"].endswith("/board")
    assert (asked["query"]["lanes"], asked["query"]["scope"]) == (WORK_LANES, "repo")
    assert asked["headers"]["x-bureau-max-age"] == "300"
    # Green Light: no door request, today's `LinearReads.lane`.
    assert len(door.requests) == 1
    assert today.calls == [("lane", "Green Light")]
    assert [c["identifier"] for c in green] == [GREEN]
    assert [x["decision"] for x in _lines(capsys.readouterr().out)] == [
        "proof-dispatch-lane"]


def test_a_door_lane_card_showing_the_marked_hold_still_reads_its_relation_times(
        monkeypatch, linear):
    marked = comment(proof_dispatch.BUDGET_HOLDS[0]
                     + proof_dispatch.SPENT_AT.format(sha7="abcdef1"), 30)
    page = {"pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"type": "blocks", "createdAt": "2026-10-06T17:00:00Z",
                       "issue": {"identifier": "DRE-8001"}}]}
    linear.relations = {REVIEWED: page}
    node = _door_node(REVIEWED, "In Review", window=[marked])
    with door_at(monkeypatch, node, _door_node(CANDIDATE, "Hand-work")):
        reads = proof_dispatch.DoorReads()
        (got,) = reads.lane("In Review")
        (other,) = reads.lane("Hand-work")
    assert linear.kinds() == ["lane-relations"]
    assert linear.reads[0][1] == {"numbers": [5950]}
    assert got["inverseRelations"] == page
    assert "inverseRelations" not in other


def test_a_stale_work_lane_read_falls_back_to_today_and_decides_the_same(
        monkeypatch, capsys, linear):
    today = Today(monkeypatch, _today_board())
    with door_at(monkeypatch) as door:
        door.unknown_for["lane"] = "stale"
        h = Harness(monkeypatch, None)
        _pass(h)
    assert ("lane", "Hand-work") in today.calls
    assert ("lane", "In Review") in today.calls
    # The UNKNOWN is remembered: the second work lane asks the door nothing.
    assert len(door.asked("/board")) == 1
    assert h.fired == FIRST_RUN
    (lane,) = [x for x in _lines(capsys.readouterr().out)
               if x["decision"] == "proof-dispatch-lane"]
    assert (lane["verdict"], lane["reason"], lane["max_age"]) == ("UNKNOWN", "stale", "300")


def test_a_held_work_lane_read_holds_the_board_and_reads_no_linear_lane(
        monkeypatch, linear):
    today = Today(monkeypatch, _today_board())
    with door_at(monkeypatch) as door:
        door.unknown_for["lane"] = bureau_read.LINEAR_HOLD
        h = Harness(monkeypatch, None)
        with pytest.raises(reconcile.BoardHeld):
            _pass(h)
    assert today.calls == [("lane", "Green Light")]
    assert h.fired == []


# ── card ────────────────────────────────────────────────────────────────────


def test_the_card_read_is_the_door_node_with_its_epic_children_from_linear(
        monkeypatch, capsys, linear):
    with door_at(monkeypatch) as door:
        issue = proof_dispatch.DoorReads().card(CANDIDATE)
    assert linear.kinds() == [f"epic-children:{CANDIDATE}"]
    assert issue["identifier"] == CANDIDATE
    assert issue["description"] == WORLD[0]["description"]
    assert issue["parent"]["identifier"] == EPIC
    assert issue["parent"]["state"] == {"name": "In Progress"}
    assert issue["parent"]["children"] == SIBLINGS
    assert [r["issue"]["identifier"] for r in issue["inverseRelations"]["nodes"]] == [
        "DRE-5921", "DRE-5922"]
    (asked,) = door.asked("/cards")
    assert asked["query"]["relations"] == "1"
    assert asked["headers"]["x-bureau-max-age"] == "300"
    assert [x["decision"] for x in _lines(capsys.readouterr().out)] == [
        "proof-dispatch-card"]


def test_the_epic_children_query_selects_exactly_what_card_query_selects():
    flat = " ".join(proof_dispatch.EPIC_CHILDREN_QUERY.split())
    children = ("children(first: 100) { nodes { identifier title state { name } "
                "labels { nodes { name } } } }")
    assert children in flat
    assert children in " ".join(proof_dispatch.CARD_QUERY.split())
    # Only the children: the door's node carries the rest.
    for field in ("description", "inverseRelations", "identifier state"):
        assert field not in flat.replace("identifier title state", "")


def test_a_whole_door_relation_page_is_not_topped_up_and_a_partial_one_is(
        monkeypatch, linear):
    with door_at(monkeypatch):
        proof_dispatch.DoorReads().card(CANDIDATE)
    assert linear.kinds() == [f"epic-children:{CANDIDATE}"]
    linear.reads.clear()
    bureau_read.reset_for_tests()
    partial = _door_node(CANDIDATE, "Hand-work")
    partial["inverseRelations"]["pageInfo"]["hasNextPage"] = True
    rest = [{"type": "blocks", "issue": {"identifier": f"DRE-59{n}", "state": {"name": "Done"}}}
            for n in (21, 22, 23)]
    linear.topup = {"pageInfo": {"hasNextPage": False, "endCursor": "c"}, "nodes": rest}
    with door_at(monkeypatch, partial):
        issue = proof_dispatch.DoorReads().card(CANDIDATE)
    assert sorted(linear.kinds()) == sorted(
        [f"epic-children:{CANDIDATE}", f"relations-top-up:{CANDIDATE}"])
    # The door gave no cursor, so the set is read from its start, to the end.
    assert issue["inverseRelations"] == {
        "pageInfo": {"hasNextPage": False, "endCursor": "c"}, "nodes": rest}


def test_a_stale_card_read_falls_back_to_today_and_decides_the_same(
        monkeypatch, capsys, linear):
    today = Today(monkeypatch, _today_board())
    with door_at(monkeypatch) as door:
        door.unknown_for["card"] = "stale"
        h = Harness(monkeypatch, None)
        _pass(h)
    assert ("card", CANDIDATE) in today.calls
    assert f"epic-children:{CANDIDATE}" not in linear.kinds()
    assert h.fired == FIRST_RUN
    (card,) = [x for x in _lines(capsys.readouterr().out)
               if x["decision"] == "proof-dispatch-card"]
    assert (card["verdict"], card["reason"]) == ("UNKNOWN", "stale")
    assert len(door.asked("/cards")) == 2  # the card, then the thread


def test_a_held_card_read_refuses_the_candidate_and_reads_no_linear_card(
        monkeypatch, capsys, linear):
    today = Today(monkeypatch, _today_board())
    with door_at(monkeypatch) as door:
        door.unknown_for["card"] = bureau_read.LINEAR_HOLD
        h = Harness(monkeypatch, None)
        _pass(h)
    assert ("card", CANDIDATE) not in today.calls
    assert h.fired == []
    assert any("condition 2" in line and CANDIDATE in line
               for line in capsys.readouterr().out.splitlines())


def test_a_door_card_with_no_parent_reads_no_epic_and_is_refused_at_condition_2(
        monkeypatch, capsys, linear):
    orphan = _door_node(CANDIDATE, "Hand-work")
    orphan["parent"] = None
    with door_at(monkeypatch, orphan):
        issue = proof_dispatch.DoorReads().card(CANDIDATE)
    assert not issue["parent"]
    assert linear.kinds() == []
    bureau_read.reset_for_tests()
    linear.reads.clear()
    with door_at(monkeypatch, orphan, *WORLD[1:]):
        h = Harness(monkeypatch, None)
        _pass(h)
    assert h.fired == []
    assert f"epic-children:{CANDIDATE}" not in linear.kinds()
    assert any("condition 2" in line and CANDIDATE in line
               and "the card has no parent epic" in line
               for line in capsys.readouterr().out.splitlines())


def test_linear_answering_no_epic_leaves_the_card_unread_and_refused_at_condition_2(
        monkeypatch, capsys, linear):
    def no_epic(query, variables=None):
        if query == proof_dispatch.EPIC_CHILDREN_QUERY:
            linear.reads.append((query, dict(variables or {})))
            return {"issue": {"parent": None}}
        return linear(query, variables)

    monkeypatch.setattr(linear_ops, "gql", no_epic)
    with door_at(monkeypatch):
        with pytest.raises(LookupError):
            proof_dispatch.DoorReads().card(CANDIDATE)
        h = Harness(monkeypatch, None)
        _pass(h)
    assert h.fired == []
    assert any("condition 2" in line and CANDIDATE in line
               and "could not be read" in line
               for line in capsys.readouterr().out.splitlines())


# ── thread ──────────────────────────────────────────────────────────────────


def test_the_thread_read_is_the_whole_door_thread_oldest_first_and_the_viewer(
        monkeypatch, capsys, linear):
    window = [comment(f"note {i}", 60 - i) for i in range(3)]
    with door_at(monkeypatch, _door_node(CANDIDATE, "Hand-work", window=window)) as door:
        nodes, viewer = proof_dispatch.DoorReads().thread(CANDIDATE)
    assert [n["body"] for n in nodes] == ["note 0", "note 1", "note 2"]
    assert nodes == window
    assert viewer == VIEWER
    assert linear.kinds() == ["viewer"]
    (asked,) = door.asked("/cards")
    assert (asked["query"]["comments"], asked["query"]["relations"]) == ("all", "0")
    assert [x["decision"] for x in _lines(capsys.readouterr().out)] == [
        "proof-dispatch-thread"]


def test_an_incomplete_door_thread_falls_back_to_today_and_decides_the_same(
        monkeypatch, capsys, linear):
    today = Today(monkeypatch, _today_board())
    with door_at(monkeypatch) as door:
        door.incomplete_threads.add(CANDIDATE)
        h = Harness(monkeypatch, None)
        _pass(h)
    assert ("thread", CANDIDATE) in today.calls
    assert "viewer" not in linear.kinds()
    assert h.fired == FIRST_RUN
    (thread,) = [x for x in _lines(capsys.readouterr().out)
                 if x["decision"] == "proof-dispatch-thread"]
    assert (thread["verdict"], thread["reason"]) == ("UNKNOWN", "thread-incomplete")


def test_a_held_thread_read_refuses_the_candidate_and_reads_no_linear_thread(
        monkeypatch, capsys, linear):
    today = Today(monkeypatch, _today_board())
    with door_at(monkeypatch) as door:
        door.unknown_for["thread"] = bureau_read.LINEAR_HOLD
        h = Harness(monkeypatch, None)
        _pass(h)
    assert ("thread", CANDIDATE) not in today.calls
    assert "viewer" not in linear.kinds()
    assert h.fired == []
    assert any("condition 4" in line and CANDIDATE in line
               for line in capsys.readouterr().out.splitlines())


# ── states ──────────────────────────────────────────────────────────────────


def test_the_states_read_is_one_door_request_without_relations(
        monkeypatch, capsys, linear):
    with door_at(monkeypatch) as door:
        got = proof_dispatch.DoorReads().states(["DRE-5921", "DRE-5960"])
    assert got == {"DRE-5921": "Done", "DRE-5960": "In Progress"}
    assert linear.reads == []
    (asked,) = door.asked("/cards")
    assert (asked["query"]["ids"], asked["query"]["relations"]) == (
        "DRE-5921,DRE-5960", "0")
    assert asked["headers"]["x-bureau-max-age"] == "300"
    (line,) = _lines(capsys.readouterr().out)
    assert (line["decision"], line["verdict"], line["max_age"]) == (
        "proof-dispatch-states", "FRESH", "300")


def test_a_states_read_the_door_cannot_answer_falls_back_to_today(monkeypatch, linear):
    today = Today(monkeypatch, _today_board())
    with door_at(monkeypatch):
        # A card outside the tenant: the door answers 404, UNKNOWN `not-found`.
        got = proof_dispatch.DoorReads().states(["DRE-5921", "DRE-9999"])
    assert today.calls == [("states", ("DRE-5921", "DRE-9999"))]
    assert got == {"DRE-5921": "Done", "DRE-9999": "Done"}


def test_a_held_states_read_raises_and_reads_no_linear_states(monkeypatch, linear):
    today = Today(monkeypatch, _today_board())
    with door_at(monkeypatch) as door:
        door.unknown_for["states"] = bureau_read.LINEAR_HOLD
        with pytest.raises(bureau_read.ReadUnknown):
            proof_dispatch.DoorReads().states(["DRE-5921", "DRE-5960"])
    assert today.calls == []
    assert linear.reads == []


def test_a_states_read_asks_the_door_for_at_most_50_cards(monkeypatch, linear):
    Today(monkeypatch, _today_board())
    named = [f"DRE-{6000 + n}" for n in range(proof_dispatch.LANE_RELATIONS_CARDS + 1)]
    with door_at(monkeypatch) as door:
        proof_dispatch.DoorReads().states(named)
    (asked,) = door.asked("/cards")
    assert asked["query"]["ids"].split(",") == named[:50]
    assert proof_dispatch.LANE_RELATIONS_CARDS == 50


def test_a_states_read_of_no_cards_asks_the_door_nothing(monkeypatch, linear):
    today = Today(monkeypatch, _today_board())
    with door_at(monkeypatch) as door:
        assert proof_dispatch.DoorReads().states([]) == {}
    assert door.requests == []
    assert today.calls == [("states", ())]


# ── what DoorReads is ───────────────────────────────────────────────────────


def test_door_reads_overrides_only_the_four_reads():
    def methods(cls):
        return {name for name, value in vars(cls).items()
                if callable(value) and not name.startswith("__")}

    assert issubclass(proof_dispatch.DoorReads, proof_dispatch.LinearReads)
    assert methods(proof_dispatch.DoorReads) & methods(proof_dispatch.LinearReads) == {
        "lane", "card", "thread", "states"}
    assert methods(proof_dispatch.DoorReads) - methods(proof_dispatch.LinearReads) == {
        "_epic_children"}
    for name in ("blocking", "fix_card"):
        assert getattr(proof_dispatch.DoorReads, name) is getattr(
            proof_dispatch.LinearReads, name)


class _Built(Exception):
    """Raised by the stub pass once it has seen its reads."""


def test_the_pass_reads_through_door_reads_by_default(monkeypatch):
    seen = []

    def built(*args, **kwargs):
        seen.append(kwargs["linear"])
        raise _Built

    monkeypatch.setattr(proof_dispatch, "_Pass", built)
    with pytest.raises(_Built):
        proof_dispatch.sweep(REPO, SLUG, live=False)
    assert type(seen[0]) is proof_dispatch.DoorReads


# ── off and shadow ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_and_shadow_call_exactly_todays_reads_and_ask_the_door_nothing(
        monkeypatch, capsys, linear, mode):
    calls = {}
    for name, linear_reads in (("door", None), ("today", proof_dispatch.LinearReads())):
        bureau_read.reset_for_tests()
        today = Today(monkeypatch, _today_board())
        with door_at(monkeypatch, mode=mode) as door:
            h = Harness(monkeypatch, None)
            _pass(h, linear=linear_reads)
        assert door.requests == []
        assert h.fired == FIRST_RUN
        calls[name] = today.calls
    assert calls["door"] == calls["today"] == [
        ("lane", "Green Light"), ("lane", "Hand-work"), ("lane", "In Review"),
        ("card", CANDIDATE), ("thread", CANDIDATE)]
    assert linear.reads == []
    assert _lines(capsys.readouterr().out) == []
