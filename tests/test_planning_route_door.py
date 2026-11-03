"""The planner's route lookups read through the read door (DRE-8904).

`planning_route._read_returned_child` and `_read_epic_wait` are single-card
reads the door's `/cards` answers. With the door on and FRESH they ask it
first — the card, and its parent when it has one; the epic with its relations
— and answer exactly what today's `_FAMILY_QUERY` and `_WAIT_QUERY` answer
over the same board. Any `None` from the door, and a relations page the door
says it did not serve whole, reads Linear as today.

No test reaches Linear or the console: Linear is `LinearStub`, a urlopen
stand-in answering api.linear.app from the same `WORLD` the fake door
(`tests/bureau_read_fakes.py`, on 127.0.0.1) serves.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(TESTS))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
import planning_route  # noqa: E402
import planning_shape  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

MODEL = "claude-fable-5-1"
CHILD = "DRE-101"
PARENT = "DRE-100"
EPIC = "DRE-200"
DONE_BLOCKER = "DRE-201"
OPEN_BLOCKER = "DRE-202"
PAGED_EPIC = "DRE-300"


def _stamp(shape: str, why: str = "the classifier's read") -> str:
    return planning_shape.shape_comment(shape, why, by=planning_shape.BY_PLANNER, model=MODEL)


def _receipt() -> str:
    import planner_score

    return (f"{planner_score.HANDBACK_RECEIPT_PREFIX} this card was dispatched as "
            "one piece of work and is an epic's worth.\n\n"
            "1. the first piece — a.py — does not depend on piece 2\n\n"
            "Run: https://github.com/o/r/actions/runs/1")


#: The child's own thread: a one-off, handed back, and read afresh as an epic.
RETURNED = [_stamp("one-off"), _receipt(), _stamp("epic", "the split, read after the hand-back")]

WORLD = {
    CHILD: card(CHILD, "Planning", parent=PARENT, comments=RETURNED),
    PARENT: card(PARENT, "In Progress", title="[EPIC] bureau-pipeline: the work",
                 children=True, comments=[_stamp("epic")]),
    EPIC: card(EPIC, "Planning", title="[EPIC] bureau-pipeline: next",
               blockers=[(DONE_BLOCKER, "Done"), (OPEN_BLOCKER, "Green Light")]),
    DONE_BLOCKER: card(DONE_BLOCKER, "Done"),
    OPEN_BLOCKER: card(OPEN_BLOCKER, "Green Light"),
    PAGED_EPIC: card(PAGED_EPIC, "Planning", title="[EPIC] bureau-pipeline: wide",
                     blockers=[(OPEN_BLOCKER, "Green Light")], relations_partial=True),
}


class _Resp:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode()
        self.headers = {"x-ratelimit-requests-remaining": "100"}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _children(node: dict) -> dict:
    return {"nodes": [{"id": k["id"]} for k in (node["children"]["nodes"])[:1]]}


class LinearStub:
    """api.linear.app, answered from WORLD by what each request asks. Every
    other URL — the runner's OIDC endpoint — goes to the real urlopen."""

    def __init__(self):
        self.sent: list[str] = []
        self.real = urllib.request.urlopen

    def __call__(self, req, *a, **k):
        if not req.full_url.startswith(linear_ops.API):
            return self.real(req, *a, **k)
        payload = json.loads(req.data)
        query, variables = payload["query"], payload.get("variables") or {}
        self.sent.append(query)
        return _Resp({"data": self.answer(query, variables.get("id"))})

    def answer(self, query: str, ident: str) -> dict:
        node = WORLD[ident]
        if query == planning_route._FAMILY_QUERY:
            parent = None
            if node["parent"]:
                up = WORLD[node["parent"]["identifier"]]
                parent = {"identifier": up["identifier"], "title": up["title"],
                          "children": _children(up)}
            return {"issue": {"children": _children(node), "parent": parent}}
        if query == planning_route._WAIT_QUERY:
            return {"issue": {"identifier": ident, "children": _children(node),
                              "inverseRelations": node["inverseRelations"]}}
        if "history(" in query:
            return {"issue": {"history": {"nodes": []}}}
        if "comments(" in query:
            return {"viewer": {"id": "linear-viewer"}, "issue": {"comments": node["comments"]}}
        if "parent { identifier state { name } }" in query:  # the sweep's blocker read
            return {"issue": {key: node[key] for key in ("identifier", "title", "state",
                                                         "parent")}}
        raise AssertionError(f"an unstubbed Linear read: {' '.join(query.split())[:80]!r}")

    def card_reads(self) -> list[str]:
        """The two reads this card moves to the door."""
        return [q for q in self.sent
                if q in (planning_route._FAMILY_QUERY, planning_route._WAIT_QUERY)]


DOOR_ENVS = ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE", "BUREAU_PIPELINE_REF",
             "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
             "GITHUB_WORKFLOW", "GITHUB_JOB",
             "BUREAU_READ_CARD_MAX_AGE", "BUREAU_READ_CARD_SEEN_AFTER",
             "BUREAU_LINEAR_READ_ONLY")


@pytest.fixture
def linear(monkeypatch):
    for name in DOOR_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.delenv(linear_ops.FALLBACK_ENV, raising=False)
    monkeypatch.delenv(linear_ops.HOME_ENV, raising=False)
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, "fleet")
    linear_ops.reset_pass_cache()
    stub = LinearStub()
    monkeypatch.setattr(urllib.request, "urlopen", stub)
    yield stub
    linear_ops.reset_pass_cache()


@contextmanager
def door_up(monkeypatch):
    """A door answering FRESH from WORLD, pointed at by the step's environment."""
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        for key, value in door_env(door_url=door.url, issuer=issuer).items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv(linear_ops.CARD_MAX_AGE_ENV, "60")
        yield door


@pytest.fixture
def door(linear, monkeypatch):
    with door_up(monkeypatch) as d:
        yield d


def _fresh_pass() -> None:
    """A new process: no kept thread, no kept blocker card."""
    import reconcile

    linear_ops.reset_pass_cache()
    reconcile.reset_sweep_cards()


def _asked_cards(door: FakeDoor) -> list[dict]:
    return [r["query"] for r in door.asked("/cards")]


def fallback_lines(err: str) -> list[str]:
    return [ln for ln in err.splitlines() if ln.startswith(bureau_read.FALLBACK_TAG)]


# ── 1. the returned child's family ──────────────────────────────────────────


def test_returned_child_off_the_door_answers_what_linear_answers(linear, monkeypatch):
    baseline = planning_route._read_returned_child(linear_ops, CHILD, RETURNED)
    assert planning_route._FAMILY_QUERY in linear.sent, "the baseline is today's read"
    assert baseline.returned is True and baseline.parent == PARENT
    _fresh_pass()
    with door_up(monkeypatch) as door:
        linear.sent.clear()
        served = planning_route._read_returned_child(linear_ops, CHILD, RETURNED)
    assert served == baseline
    assert linear.sent == []
    assert {"ids": CHILD, "comments": "1", "relations": "0"} in _asked_cards(door)
    assert {"ids": PARENT, "comments": "1", "relations": "0"} in _asked_cards(door)


def test_a_card_with_no_parent_asks_the_door_once(door, linear):
    WORLD["DRE-102"] = card("DRE-102", "Planning", comments=RETURNED)
    try:
        got = planning_route._read_returned_child(linear_ops, "DRE-102", RETURNED)
    finally:
        del WORLD["DRE-102"]
    assert got.returned is False
    assert "no parent" in got.reason
    assert _asked_cards(door) == [{"ids": "DRE-102", "comments": "1", "relations": "0"}]
    assert linear.sent == []


def test_a_parent_the_door_cannot_answer_reads_the_family_query(door, linear):
    door.routes["/cards"] = lambda endpoint, query, _h: (
        door.unknown("stale") if query["ids"] == PARENT
        else (200, door.envelope([door._shaped(WORLD[query["ids"]], query)])))
    got = planning_route._read_returned_child(linear_ops, CHILD, RETURNED)
    assert got.returned is True and got.parent == PARENT
    assert linear.card_reads() == [planning_route._FAMILY_QUERY]


def test_a_card_the_door_cannot_answer_reads_the_family_query(door, linear):
    door.routes["/cards"] = door.unknown("stale")
    got = planning_route._read_returned_child(linear_ops, CHILD, RETURNED)
    assert got.returned is True
    assert linear.card_reads() == [planning_route._FAMILY_QUERY]


# ── 2. the epic's wait ──────────────────────────────────────────────────────


def test_epic_wait_off_the_door_answers_what_linear_answers(linear, monkeypatch):
    """Two blockers in different lanes: the Done one releases nothing and the
    Green Light one holds. The epic's own read goes to the door; what still
    reaches Linear is its lane history, which the door does not carry."""
    baseline = planning_route._read_epic_wait(linear_ops, EPIC)
    assert planning_route._WAIT_QUERY in linear.sent, "the baseline is today's read"
    assert baseline.wait is True
    assert baseline.waits_on == f"{OPEN_BLOCKER} (Green Light)"
    _fresh_pass()
    with door_up(monkeypatch) as door:
        linear.sent.clear()
        served = planning_route._read_epic_wait(linear_ops, EPIC)
    assert served == baseline
    assert linear.card_reads() == []
    assert [q for q in linear.sent if "history(" not in q] == []
    assert {"ids": EPIC, "comments": "1", "relations": "1"} in _asked_cards(door)


def test_a_relations_page_the_door_did_not_serve_whole_reads_the_wait_query(door, linear):
    got = planning_route._read_epic_wait(linear_ops, PAGED_EPIC)
    assert linear.card_reads() == [planning_route._WAIT_QUERY]
    assert got.wait is False and "not read to the end" in got.reason


def test_an_epic_the_door_cannot_answer_reads_the_wait_query(door, linear):
    door.routes["/cards"] = door.unknown("relations-stale")
    got = planning_route._read_epic_wait(linear_ops, EPIC)
    assert linear.card_reads() == [planning_route._WAIT_QUERY]
    assert got.wait is True


def test_the_epic_read_asks_cards_with_relations_and_their_bound(door, linear, monkeypatch):
    calls: list[tuple[tuple, dict]] = []
    real = bureau_read.cards

    def recording(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(bureau_read, "cards", recording)
    planning_route._read_epic_wait(linear_ops, EPIC)
    epic_calls = [kw for args, kw in calls if list(args[0]) == [EPIC]]
    assert epic_calls, calls
    (kwargs,) = epic_calls
    assert kwargs["relations"] is True
    assert kwargs["relations_max_age"] == bureau_read.RELATIONS_MAX_AGE
    assert bureau_read.RELATIONS_MAX_AGE == 1200
    (asked,) = [r for r in door.asked("/cards") if r["query"]["ids"] == EPIC]
    assert asked["headers"]["x-bureau-relations-max-age"] == "1200"


def test_with_the_door_off_nothing_is_asked_of_it(linear, monkeypatch):
    """The step that names no max-age reads Linear exactly as today."""
    with door_up(monkeypatch) as door:
        monkeypatch.delenv(linear_ops.CARD_MAX_AGE_ENV)
        planning_route._read_epic_wait(linear_ops, EPIC)
        planning_route._read_returned_child(linear_ops, CHILD, RETURNED)
    assert not [q for q in _asked_cards(door) if q["ids"] in (EPIC, CHILD, PARENT)]
    assert linear.card_reads() == [planning_route._WAIT_QUERY, planning_route._FAMILY_QUERY]
