"""The duplicate-dispatch guard reads through the read door, only when the door
has seen the dispatch (DRE-8904).

`_current_lane` and `_card_labels` ask whether the card moved while the
dispatch sat in the queue. A store that has applied every change up to a
moment after the dispatch answers that as well as a live read made at that
moment, so the door's node is taken only when its `as_of` is at or after the
run's `created_at`. An older `as_of`, an unreadable `created_at`, a re-run, or
no door answer reads `get_issue(fresh=True)` exactly as today.

`_one_off_card` reads the card and its WHOLE thread off one door read
(`comments="all"`), and today's two Linear reads when the door cannot vouch
for the thread — with one `read-fallback:` line, not two.

No test reaches Linear or the console: Linear is `LinearStub`, a urlopen
stand-in, and the door and its token issuer are `tests/bureau_read_fakes.py`
on 127.0.0.1.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(TESTS))

import bureau_read  # noqa: E402
import dedupe_dispatch  # noqa: E402
import linear_ops  # noqa: E402
import planning_shape  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

CARD = "DRE-501"
#: When this run was created — the instant the dispatch was made.
DISPATCHED = "2026-10-02T20:00:00Z"
ONE_AFTER = "2026-10-02T20:00:01.000Z"
ONE_BEFORE = "2026-10-02T19:59:59.000Z"

#: The door's lane and labels, and Linear's: different, so the answer says
#: which of the two was read.
DOOR_LANE, LINEAR_LANE = "Planning", "Green Light"
DOOR_LABELS, LINEAR_LABELS = ["agent:planner"], ["agent:planner", "needs-person"]

THREAD = [
    planning_shape.shape_comment("one-off", "the classifier's read",
                                 by=planning_shape.BY_PLANNER, model="claude-fable-5-1"),
    "a person's note",
]

WORLD = {
    CARD: card(CARD, DOOR_LANE, labels=DOOR_LABELS, title="bureau-pipeline: one piece",
               description="the work", comments=THREAD),
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


class LinearStub:
    """api.linear.app: `get_issue`'s read answers LINEAR_LANE and
    LINEAR_LABELS; the hand-back's card read and the thread read answer
    WORLD's card as Linear selects it."""

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
        if "project { id }" in query:  # get_issue
            return {"issue": {
                "id": node["id"], "identifier": ident, "title": node["title"],
                "team": {"id": "team-1"},
                "state": {"id": "s", "name": LINEAR_LANE, "type": "started"},
                "labels": {"nodes": [{"name": n} for n in LINEAR_LABELS]},
                "children": {"nodes": []}, "project": None}}
        if query == dedupe_dispatch._HAND_BACK_QUERY:
            return {"issue": {
                **{key: node[key] for key in ("id", "identifier", "title", "description",
                                              "createdAt", "state", "labels", "comments")},
                "children": {"nodes": []}}}
        if "comments(" in query:
            return {"viewer": {"id": "linear-viewer"}, "issue": {"comments": node["comments"]}}
        raise AssertionError(f"an unstubbed Linear read: {' '.join(query.split())[:80]!r}")

    def lane_reads(self) -> list[str]:
        return [q for q in self.sent if "project { id }" in q]


DOOR_ENVS = ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE", "BUREAU_PIPELINE_REF",
             "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
             "GITHUB_REPOSITORY", "GITHUB_WORKFLOW", "GITHUB_JOB",
             "BUREAU_READ_CARD_MAX_AGE", "BUREAU_READ_CARD_SEEN_AFTER",
             "BUREAU_LINEAR_READ_ONLY", "GITHUB_RUN_ATTEMPT")


@pytest.fixture
def linear(monkeypatch):
    for name in DOOR_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.delenv(linear_ops.FALLBACK_ENV, raising=False)
    monkeypatch.delenv(linear_ops.HOME_ENV, raising=False)
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, "fleet")
    monkeypatch.setenv("GITHUB_RUN_ID", "4242")
    linear_ops.reset_pass_cache()
    stub = LinearStub()
    monkeypatch.setattr(urllib.request, "urlopen", stub)
    yield stub
    linear_ops.reset_pass_cache()


@pytest.fixture
def meta(monkeypatch):
    """This run's `created_at`, as `_run_meta` reads it off GitHub."""
    asked: list[str] = []
    answer = {"created_at": DISPATCHED, "workflow_id": "7"}

    def run_meta(run_id):
        asked.append(run_id)
        return dict(answer)

    monkeypatch.setattr(dedupe_dispatch, "_run_meta", run_meta)
    run_meta.asked = asked
    run_meta.answer = answer
    return run_meta


@contextmanager
def door_up(monkeypatch, *, as_of: str = ONE_AFTER):
    """A door answering FRESH from WORLD as of `as_of`."""
    with FakeIssuer() as issuer, FakeDoor(WORLD, as_of=as_of) as door:
        for key, value in door_env(door_url=door.url, issuer=issuer).items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv(linear_ops.CARD_MAX_AGE_ENV, "60")
        yield door


def fallback_lines(err: str) -> list[str]:
    return [ln for ln in err.splitlines() if ln.startswith(bureau_read.FALLBACK_TAG)]


# ── 1. the lane and the labels: the door only once it has seen the dispatch ──


def test_a_door_that_has_seen_the_dispatch_answers_the_lane(linear, meta, monkeypatch):
    with door_up(monkeypatch, as_of=ONE_AFTER) as door:
        assert dedupe_dispatch._current_lane(CARD) == DOOR_LANE
    assert linear.sent == []
    (asked,) = door.asked("/cards")
    assert asked["query"]["ids"] == CARD
    assert meta.asked == ["4242"]


def test_a_door_older_than_the_dispatch_reads_the_lane_live(linear, meta, monkeypatch, capsys):
    with door_up(monkeypatch, as_of=ONE_BEFORE):
        assert dedupe_dispatch._current_lane(CARD) == LINEAR_LANE
    assert len(linear.lane_reads()) == 1
    (line,) = fallback_lines(capsys.readouterr().err)
    assert "reason=older-than-needed" in line


def test_an_unreadable_created_at_reads_the_lane_live(linear, meta, monkeypatch):
    meta.answer["created_at"] = ""
    with door_up(monkeypatch) as door:
        assert dedupe_dispatch._current_lane(CARD) == LINEAR_LANE
    assert len(linear.lane_reads()) == 1
    assert door.asked("/cards") == []


def test_a_created_at_that_is_not_an_instant_reads_the_lane_live(linear, meta, monkeypatch):
    meta.answer["created_at"] = "yesterday"
    with door_up(monkeypatch) as door:
        assert dedupe_dispatch._current_lane(CARD) == LINEAR_LANE
    assert door.asked("/cards") == []


def test_no_run_meta_at_all_reads_the_lane_live(linear, monkeypatch):
    monkeypatch.setattr(dedupe_dispatch, "_run_meta", lambda _r: {})
    with door_up(monkeypatch) as door:
        assert dedupe_dispatch._current_lane(CARD) == LINEAR_LANE
    assert door.asked("/cards") == []


def test_a_rerun_reads_the_lane_live(linear, meta, monkeypatch):
    """A re-run carries the original dispatch's `created_at`: a door that has
    seen that moment says nothing about the card now."""
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    with door_up(monkeypatch) as door:
        assert dedupe_dispatch._current_lane(CARD) == LINEAR_LANE
    assert len(linear.lane_reads()) == 1
    assert door.asked("/cards") == []


def test_the_build_gate_on_a_rerun_reads_the_lane_live(linear, meta, monkeypatch, tmp_path):
    """Agent Task's `gate` runs on a re-run too; its lane is the live one."""
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    seen: dict = {}

    def decide(identifier, run_id, bodies, status, prs, *, card_lane):
        seen["lane"] = card_lane
        return dedupe_dispatch.Decision(skip=False, reason="proceed")

    monkeypatch.setattr(dedupe_dispatch, "decide", decide)
    monkeypatch.setattr(dedupe_dispatch, "_card_prs", lambda _i: [])
    monkeypatch.setattr(dedupe_dispatch.linear_ops, "comment_bodies", lambda *_a, **_k: [])
    with door_up(monkeypatch) as door:
        dedupe_dispatch.cmd_gate(CARD)
    assert seen["lane"] == LINEAR_LANE
    assert door.asked("/cards") == []


def test_the_labels_follow_the_same_rule(linear, meta, monkeypatch):
    with door_up(monkeypatch, as_of=ONE_AFTER):
        assert dedupe_dispatch._card_labels(CARD) == DOOR_LABELS
    assert linear.sent == []
    linear_ops.reset_pass_cache()
    with door_up(monkeypatch, as_of=ONE_BEFORE):
        assert dedupe_dispatch._card_labels(CARD) == LINEAR_LABELS
    assert len(linear.lane_reads()) == 1


def test_with_the_door_off_the_dispatch_time_is_never_read(linear, meta, monkeypatch):
    """A step that names no max-age costs nothing new: no GitHub read of the
    run, and the lane read exactly as today."""
    assert dedupe_dispatch._current_lane(CARD) == LINEAR_LANE
    assert meta.asked == []


# ── 2. the hand-back's card ─────────────────────────────────────────────────


HAND_BACK_KEYS = ("id", "identifier", "title", "description", "createdAt", "state", "labels")


def _same_card(card: dict) -> dict:
    return {**{key: card[key] for key in HAND_BACK_KEYS},
            "has_children": bool(card["children"]["nodes"]),
            "thread": [c["body"] for c in linear_ops.window_nodes(card["comments"])]}


def test_the_one_off_card_off_the_door_is_the_card_linear_gives(linear, monkeypatch):
    baseline = dedupe_dispatch._one_off_card(CARD)
    assert dedupe_dispatch._HAND_BACK_QUERY in linear.sent, "the baseline is today's read"
    assert baseline is not None
    linear_ops.reset_pass_cache()
    linear.sent.clear()
    with door_up(monkeypatch) as door:
        served = dedupe_dispatch._one_off_card(CARD)
    assert linear.sent == []
    assert served is not None
    assert _same_card(served) == _same_card(baseline)
    assert _same_card(served)["thread"] == THREAD
    (asked,) = door.asked("/cards")
    assert asked["query"]["comments"] == "all"


def test_a_thread_the_door_cannot_vouch_for_makes_todays_reads(linear, monkeypatch, capsys):
    with door_up(monkeypatch) as door:
        door.incomplete_threads.add(CARD)
        got = dedupe_dispatch._one_off_card(CARD)
    assert got is not None and got["identifier"] == CARD
    assert dedupe_dispatch._HAND_BACK_QUERY in linear.sent
    assert [q for q in linear.sent if q != dedupe_dispatch._HAND_BACK_QUERY
            and "comments(" in q], "the whole thread is read off Linear"
    (line,) = fallback_lines(capsys.readouterr().err)
    assert "read=cards" in line and "reason=thread-incomplete" in line
    assert len(door.asked("/cards")) == 1


def test_a_door_card_with_a_child_is_not_a_one_off(linear, monkeypatch):
    with door_up(monkeypatch) as door:
        door.world["DRE-502"] = card("DRE-502", "In Progress",
                                     title="bureau-pipeline: one piece",
                                     comments=THREAD, children=True)
        assert dedupe_dispatch._one_off_card("DRE-502") is None
    assert linear.sent == []
