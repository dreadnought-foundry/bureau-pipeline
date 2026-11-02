"""Six Sweep reads that went straight to Linear now ask the read door first (DRE-8907).

The door already answers each in the shape it uses, so in mode `on` each asks
it with a row of its own in `reconcile.DOOR_MAX_AGES`, prints one
`read-door: read` line per door request, and on an UNKNOWN makes exactly the
Linear read it made before — every fail-safe stays where it was:

1. `card_parked_for_human` — the last read before a fix agent is dispatched;
2. `_blocker_card` — a non-epic blocker's title, state and parent;
3. `_release_card` — the hold reason, read before the label comes off;
4. `advance_unblocked_epics` — the closed epic's dependents;
5. `clock_planning_epics` — an aged Planning epic, taken off the pass's own
   Planning/Intake board read when it is there, with no request at all;
6. busy threads — every work-lane card whose comment window is partial has its
   whole thread asked of the door in one read and cached for the pass.

`off` and `shadow` make exactly the Linear reads they made, and print no line.
The door and the issuer are `tests/bureau_read_fakes.py`, on 127.0.0.1; Linear
is a stub that answers from the same cards and records every request.
"""
from __future__ import annotations

import contextlib
import copy
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

LINE = re.compile(
    r"^read-door: read decision=(?P<decision>\S+) verdict=(?P<verdict>FRESH|UNKNOWN) "
    r"age=(?P<age>\d+|-) max-age=(?P<max_age>\d+) reason=(?P<reason>\S+)$")
REPO_LABEL = "repo:portico"
NOW = "2026-11-02T12:00:00Z"
#: A receipt far older than the stall clock's gate.
AGED = "2026-11-01T00:00:00Z"


def _busy(ident: str, lane: str = "In Progress") -> dict:
    """A card past the fifty-comment window."""
    return card(ident, lane, labels=(REPO_LABEL,),
                comments=tuple(f"{ident} comment {i}" for i in range(60)))


WORLD = (
    card("DRE-7", "In Review", labels=(REPO_LABEL,)),                     # free
    card("DRE-17", "In Review", labels=(REPO_LABEL, reconcile.HOLD_LABEL)),  # held
    card("DRE-27", "Green Light", labels=(REPO_LABEL,)),                  # parked lane
    card("DRE-8", "In Progress", labels=(REPO_LABEL,), title="Proof: the thing works",
         parent="DRE-9", parent_lane="Done"),
    card("DRE-9", "Done", labels=(REPO_LABEL,), children=True),
    card("DRE-10", "Done", labels=(REPO_LABEL,), children=True),
    card("DRE-11", "Queued", labels=(REPO_LABEL,), children=True, blockers=(("DRE-10", "Done"),)),
    card("DRE-12", "Queued", labels=(REPO_LABEL,), children=True, blockers=(("DRE-10", "Done"),)),
    card("DRE-30", "Planning", labels=(REPO_LABEL, reconcile.HOLD_LABEL), children=True),
)


class Linear:
    """Linear as the same cards: every single-issue read answers the whole
    node, with its forward `relations`; every paged read answers nothing."""

    def __init__(self, *cards, fail: bool = False):
        self.cards = {c["identifier"]: copy.deepcopy(c) for c in cards}
        self.fail = fail
        self.reads: list[tuple[str, dict]] = []
        self.got: list[str] = []

    def gql(self, query, variables=None):
        q = " ".join(query.split())
        self.reads.append((q, dict(variables or {})))
        if self.fail:
            raise linear_ops.LinearError("Linear is down")
        if "issues(" in q:
            return {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False,
                                                         "endCursor": None}}}
        ident = (variables or {}).get("id")
        node = copy.deepcopy(self.cards.get(ident))
        if node is not None:
            node["relations"] = {"nodes": [
                {"type": "blocks", "issue": {"identifier": other["identifier"]}}
                for other in self.cards.values()
                for rel in other["inverseRelations"]["nodes"]
                if rel["type"] == "blocks" and rel["issue"]["identifier"] == ident]}
        return {"issue": node, "viewer": {"id": "fleet-user"}}

    def get_issue(self, ident, *, fresh=False):
        self.got.append(ident)
        c = self.cards[ident]
        return {"identifier": ident, "state": dict(c["state"]),
                "labels": copy.deepcopy(c["labels"])}

    def asked(self, ident: str) -> int:
        return sum(1 for _, v in self.reads if v.get("id") == ident)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY",
                 "GITHUB_EVENT_NAME", "MERGED_CARD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    monkeypatch.setattr(reconcile, "MAX_WIP", 5)
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()
    linear_ops.open_pass()
    yield
    linear_ops.reset_pass_cache()
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()


def _wire(monkeypatch, linear: Linear) -> Linear:
    monkeypatch.setattr(linear_ops, "gql", linear.gql)
    monkeypatch.setattr(linear_ops, "get_issue", linear.get_issue)
    return linear


@pytest.fixture
def linear(monkeypatch):
    return _wire(monkeypatch, Linear(*WORLD))


@contextlib.contextmanager
def door_at(monkeypatch, *cards, mode="on", **door_kw):
    world = {c["identifier"]: c for c in cards}
    with FakeIssuer() as issuer, FakeDoor(world, **door_kw) as door:
        for key, value in door_env(door_url=door.url, issuer=issuer, mode=mode).items():
            monkeypatch.setenv(key, value)
        yield door


def _lines(out: str) -> list[dict]:
    found = []
    for raw in out.splitlines():
        if raw.startswith("read-door: read "):
            match = LINE.match(raw)
            assert match, f"not the contract's shape: {raw!r}"
            found.append(match.groupdict())
    return found


def _stale(door: FakeDoor) -> None:
    for endpoint in ("/cards", "dependents"):
        door.routes[endpoint] = door.unknown("stale")


def _max_ages(door: FakeDoor) -> list[str]:
    return [r["headers"]["x-bureau-max-age"] for r in door.requests]


# ── 1. card_parked_for_human ────────────────────────────────────────────────


@pytest.mark.parametrize("ident, parked", [("DRE-7", False), ("DRE-17", True),
                                           ("DRE-27", True)])
def test_the_parked_check_is_read_off_the_door(monkeypatch, linear, capsys, ident, parked):
    with door_at(monkeypatch, *WORLD) as door:
        assert reconcile.card_parked_for_human(ident) is parked
    assert linear.reads == []
    assert _max_ages(door) == ["60"]
    assert door.requests[0]["query"]["comments"] == "50"
    assert [(x["decision"], x["verdict"]) for x in _lines(capsys.readouterr().out)] == [
        ("parked-check", "FRESH")]


@pytest.mark.parametrize("ident, parked", [("DRE-7", False), ("DRE-17", True)])
def test_a_stale_parked_check_reads_linear_as_today(monkeypatch, linear, capsys, ident,
                                                    parked):
    with door_at(monkeypatch, *WORLD) as door:
        _stale(door)
        assert reconcile.card_parked_for_human(ident) is parked
    assert linear.asked(ident) == 1
    assert [(x["decision"], x["verdict"], x["reason"])
            for x in _lines(capsys.readouterr().out)] == [("parked-check", "UNKNOWN", "stale")]


def test_a_parked_check_neither_can_answer_is_still_parked(monkeypatch, capsys):
    linear = _wire(monkeypatch, Linear(*WORLD, fail=True))
    with door_at(monkeypatch, *WORLD) as door:
        _stale(door)
        assert reconcile.card_parked_for_human("DRE-7") is True
    assert linear.asked("DRE-7") == 1


# ── 2. _blocker_card ────────────────────────────────────────────────────────


def test_a_blocker_card_is_read_off_the_door(monkeypatch, linear, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        got = reconcile._blocker_card("DRE-8")
        assert reconcile._blocker_card("DRE-8") is got  # once per pass
    assert linear.reads == []
    assert _max_ages(door) == ["120"]
    assert got["identifier"] == "DRE-8"
    assert got["title"] == "Proof: the thing works"
    assert got["state"] == {"name": "In Progress"}
    assert got["parent"] == {"identifier": "DRE-9", "state": {"name": "Done"}}
    # The decision it feeds is the one Linear's read gave.
    assert reconcile._proof_blocker("DRE-8") == ("DRE-9", None)
    assert [(x["decision"], x["verdict"]) for x in _lines(capsys.readouterr().out)] == [
        ("blocker-card", "FRESH")]


def test_a_stale_blocker_card_reads_linear_as_today(monkeypatch, linear, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        _stale(door)
        assert reconcile._proof_blocker("DRE-8") == ("DRE-9", None)
    assert linear.asked("DRE-8") == 1
    assert [q for q, _ in linear.reads] == [" ".join(reconcile._BLOCKER_CARD_QUERY.split())]
    assert [(x["decision"], x["verdict"], x["reason"])
            for x in _lines(capsys.readouterr().out)] == [("blocker-card", "UNKNOWN", "stale")]


# ── 3. _release_card ────────────────────────────────────────────────────────


@pytest.fixture
def writes(monkeypatch):
    done: list[tuple] = []
    monkeypatch.setattr(linear_ops, "remove_label", lambda *a: done.append(("label",) + a))
    monkeypatch.setattr(linear_ops, "cmd_advance", lambda *a: done.append(("advance",) + a))
    monkeypatch.setattr(linear_ops, "cmd_comment", lambda *a: done.append(("comment",) + a))
    return done


PR = {"headRefName": "agent/DRE-17-the-fix", "number": 3}


def test_the_release_read_is_off_the_door(monkeypatch, linear, writes, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        reconcile._release_card(PR, "released")
    assert linear.reads == []
    assert _max_ages(door) == ["60"]
    assert door.requests[0]["query"]["comments"] == "50"
    assert writes[0] == ("label", "DRE-17", reconcile.HOLD_LABEL)
    assert [w[0] for w in writes] == ["label", "advance", "comment"]
    assert [(x["decision"], x["verdict"]) for x in _lines(capsys.readouterr().out)] == [
        ("release-card", "FRESH")]


def test_a_stale_release_read_reads_linear_as_today(monkeypatch, linear, writes, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        _stale(door)
        reconcile._release_card(PR, "released")
    assert linear.asked("DRE-17") == 1
    assert [w[0] for w in writes] == ["label", "advance", "comment"]
    assert [(x["decision"], x["verdict"], x["reason"])
            for x in _lines(capsys.readouterr().out)] == [("release-card", "UNKNOWN", "stale")]


# ── 4. advance_unblocked_epics ──────────────────────────────────────────────


@pytest.fixture
def returned(monkeypatch):
    moved: list[str] = []
    monkeypatch.setattr(reconcile, "card_state", lambda ident: "Queued")
    monkeypatch.setattr(reconcile, "epic_blockers_unmet",
                        lambda ident, release_at_build_done=True: False)
    monkeypatch.setattr(reconcile, "_return_epic_to_planning",
                        lambda dep, because: moved.append(dep))
    return moved


def test_the_dependents_of_a_closed_epic_are_read_off_the_door(monkeypatch, linear, returned,
                                                               capsys):
    with door_at(monkeypatch, *WORLD) as door:
        reconcile.advance_unblocked_epics("DRE-10")
    assert linear.reads == []
    assert [r["path"].endswith("/cards/DRE-10/dependents") for r in door.requests] == [True]
    assert _max_ages(door) == ["120"]
    assert returned == ["DRE-11", "DRE-12"]
    assert [(x["decision"], x["verdict"]) for x in _lines(capsys.readouterr().out)] == [
        ("epic-dependents", "FRESH")]


def test_stale_dependents_read_linear_as_today(monkeypatch, linear, returned, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        _stale(door)
        reconcile.advance_unblocked_epics("DRE-10")
    assert linear.asked("DRE-10") == 1
    assert "relations(first: 20)" in linear.reads[0][0]
    assert returned == ["DRE-11", "DRE-12"]
    assert [(x["decision"], x["verdict"], x["reason"])
            for x in _lines(capsys.readouterr().out)] == [("epic-dependents", "UNKNOWN", "stale")]


# ── 5. clock_planning_epics ─────────────────────────────────────────────────


CANDIDATES = [{"identifier": "DRE-40", "parent": {"identifier": "DRE-30",
                                                  "state": {"name": "Planning"}}}]


@pytest.fixture
def aged(monkeypatch):
    monkeypatch.setattr(linear_ops, "first_comment_at", lambda ident, key: AGED)


def test_an_epic_on_the_pass_planning_read_costs_no_request(monkeypatch, linear, aged,
                                                            capsys):
    with door_at(monkeypatch, *WORLD) as door:
        reconcile._linear_lanes()  # the pass's Planning/Intake read
        asked = len(door.requests)
        reconcile.clock_planning_epics(CANDIDATES, NOW)
    assert len(door.requests) == asked
    assert linear.got == [] and linear.reads == []
    # Its labels came off the board read: the hold is seen, so it is not clocked.
    assert "epic DRE-30 carries needs-human" in capsys.readouterr().out


def test_an_epic_absent_from_the_planning_read_is_read_as_today(monkeypatch, linear, aged,
                                                                capsys):
    others = [c for c in WORLD if c["identifier"] != "DRE-30"]
    with door_at(monkeypatch, *others):
        reconcile._linear_lanes()
        reconcile.clock_planning_epics(CANDIDATES, NOW)
    assert linear.got == ["DRE-30"]
    assert "epic DRE-30 carries needs-human" in capsys.readouterr().out


# ── 6. busy threads ─────────────────────────────────────────────────────────


def test_busy_work_lane_threads_come_whole_from_one_door_read(monkeypatch, linear, capsys):
    with door_at(monkeypatch, _busy("DRE-1"), _busy("DRE-2"), *WORLD) as door:
        reconcile._door_work_cards()
    threads = door.asked("/cards")
    assert [(r["query"]["ids"], r["query"]["comments"], r["query"]["relations"])
            for r in threads] == [("DRE-1,DRE-2", "all", "0")]
    assert threads[0]["headers"]["x-bureau-max-age"] == "120"
    assert len(linear_ops.comment_bodies("DRE-1")) == 60
    assert len(linear_ops.comment_bodies("DRE-2")) == 60
    assert linear.reads == []
    assert [(x["decision"], x["verdict"]) for x in _lines(capsys.readouterr().out)] == [
        ("work-lanes", "FRESH"), ("busy-threads", "FRESH")]


def test_a_thread_the_door_cannot_prove_whole_is_left_to_linear(monkeypatch, linear, capsys):
    with door_at(monkeypatch, _busy("DRE-1"), _busy("DRE-2"), *WORLD) as door:
        door.incomplete_threads = {"DRE-1"}
        reconcile._door_work_cards()
    assert [r["query"]["ids"] for r in door.asked("/cards")] == ["DRE-1,DRE-2", "DRE-2"]
    linear_ops.comment_bodies("DRE-2")
    assert linear.reads == []
    linear_ops.comment_bodies("DRE-1")
    assert [v.get("id") for _, v in linear.reads] == ["DRE-1"]
    assert [(x["decision"], x["verdict"], x["reason"])
            for x in _lines(capsys.readouterr().out)] == [
        ("work-lanes", "FRESH", "-"), ("busy-threads", "UNKNOWN", "thread-incomplete"),
        ("busy-threads", "FRESH", "-")]


def test_no_busy_card_asks_for_no_thread(monkeypatch, linear):
    with door_at(monkeypatch, *WORLD) as door:
        reconcile._door_work_cards()
    assert door.asked("/cards") == []


# ── off and shadow ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_and_shadow_read_the_six_from_linear_as_before(monkeypatch, linear, writes,
                                                           returned, aged, capsys, mode):
    with door_at(monkeypatch, _busy("DRE-1"), *WORLD, mode=mode) as door:
        reconcile.card_parked_for_human("DRE-7")
        reconcile._blocker_card("DRE-8")
        reconcile._release_card(PR, "released")
        reconcile.advance_unblocked_epics("DRE-10")
        reconcile.active_cards(reconcile.SWEEP_STATES)
        reconcile.clock_planning_epics(CANDIDATES, NOW)
    assert _lines(capsys.readouterr().out) == []
    assert [v.get("id") for _, v in linear.reads if "id" in v] == [
        "DRE-7", "DRE-8", "DRE-17", "DRE-10"]
    assert linear.got == ["DRE-30"]
    # Shadow compares the board; neither mode asks the door for anything else.
    assert [r["path"].rsplit("/", 1)[-1] for r in door.requests] == (
        ["board"] if mode == "shadow" else [])
