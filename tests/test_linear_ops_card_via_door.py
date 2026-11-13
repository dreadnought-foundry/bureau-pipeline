"""`linear_ops`' own card reads ask the read door first (DRE-8898).

A step opts in by naming its max-age in `BUREAU_READ_CARD_MAX_AGE`; every
other step reads Linear exactly as before. A door answer is taken only when
its `as_of` is at or after the card's floor: `BUREAU_READ_CARD_SEEN_AFTER`
(or the `seen_after` argument) and the newest write this job made to the card
(`tests/test_linear_ops_own_writes.py`). The viewer is never the door's.

No test reaches Linear or the console: Linear is `LinearStub`, a urlopen
stand-in that answers api.linear.app by what each request asks, and the door
and its token issuer are `tests/bureau_read_fakes.py` on 127.0.0.1.
"""

import json
import os
import subprocess
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
import linear_ops  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

#: Who Linear says this process's key is.
LINEAR_VIEWER = "linear-viewer"
#: Who the door says its key is (the console's): `FakeDoor.envelope`'s viewer.
DOOR_VIEWER = "fleet-user"
#: The comment pre-read's own query: the card's `id` alone.
TARGET_QUERY = "query($id: String!) { issue(id: $id) { id } }"


def thread(ident: str, n: int = 5) -> list[dict]:
    """A card's comments, OLDEST first: by this key, by the door's key, and by
    an integration with no user, in turn."""
    users = [{"id": LINEAR_VIEWER}, {"id": DOOR_VIEWER}, None]
    return [{"body": f"{ident} comment {i} receipt" if i % 2 else f"{ident} comment {i}",
             "createdAt": f"2026-11-01T10:00:{i:02d}.000Z",
             "user": users[i % 3]}
            for i in range(n)]


THREADS = {ident: thread(ident) for ident in ("DRE-1", "DRE-2", "DRE-7", "DRE-8")}


def door_card(ident: str) -> dict:
    """The door's node for `ident`: its comments NEWEST first, as the door
    (and Linear) hands them."""
    node = card(ident, "In Progress", description=f"the description of {ident}")
    node["comments"]["nodes"] = list(reversed(THREADS[ident]))
    return node


WORLD = {ident: door_card(ident) for ident in THREADS}


def records(ident: str) -> list[dict]:
    """`comment_records` for `ident`, worked out by hand, oldest→newest."""
    return [{"body": c["body"],
             "authored_by_pipeline": (c["user"] or {}).get("id") == LINEAR_VIEWER,
             "created_at": c["createdAt"]}
            for c in THREADS[ident]]


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
    """api.linear.app, answered by what each request asks. Every other URL —
    the runner's OIDC endpoint the door's client mints its token at — goes to
    the real urlopen.

    `fail` names mutation fields Linear answers with an error, `unsuccessful`
    those it answers `success: false`; `labels` is each card's labels."""

    def __init__(self, *, viewer: str = LINEAR_VIEWER):
        self.viewer = viewer
        self.fail: set[str] = set()
        self.unsuccessful: set[str] = set()
        self.labels: dict[str, list[dict]] = {}
        self.sent: list[tuple[str, dict]] = []
        self.real = urllib.request.urlopen

    def __call__(self, req, *a, **k):
        if not req.full_url.startswith(linear_ops.API):
            return self.real(req, *a, **k)
        payload = json.loads(req.data)
        query, variables = payload["query"], payload.get("variables") or {}
        self.sent.append((query, variables))
        return _Resp(self.answer(query, variables))

    def queries(self) -> list[str]:
        return [q for q, _v in self.sent]

    def thread_reads(self) -> list[str]:
        return [q for q in self.queries() if "comments(" in q]

    def answer(self, query: str, variables: dict) -> dict:
        field = linear_ops._mutation_field(query)
        if field:
            if field in self.fail:
                return {"errors": [{"message": f"{field} refused by the stub"}]}
            if field == "issueLabelCreate":
                return {"data": {field: {"issueLabel": {"id": "label-new"}}}}
            return {"data": {field: {"success": field not in self.unsuccessful}}}
        ident = variables.get("id")
        if query.strip() == "query { viewer { id } }":
            return {"data": {"viewer": {"id": self.viewer}}}
        if "comments(" in query:
            if "$after" in query:
                return {"data": {"issue": {"comments": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}
            return {"data": {"viewer": {"id": self.viewer}, "issue": {"comments": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": list(reversed(THREADS.get(ident, [])))}}}}
        if "history" in query:
            return {"data": {"issue": {"history": {"nodes": []}}}}
        if "issues(" in query:
            return {"data": {"issues": {"nodes": []}}}
        if "team(id:" in query:
            return {"data": {"team": {"labels": {"nodes": [
                {"id": "label-bug", "name": "bug"}]}}}}
        if "children {" in query:
            return {"data": {"issue": {"children": {"nodes": []}}}}
        if "{ description }" in query:
            return {"data": {"issue": {"description": f"linear's description of {ident}"}}}
        if "labels { nodes { id name } }" in query:
            return {"data": {"issue": {"id": f"uuid-{ident}", "team": {"id": "team-1"},
                                       "labels": {"nodes": self.labels.get(ident, [])}}}}
        return {"data": {"issue": {
            "id": f"uuid-{ident}", "identifier": ident, "title": "t",
            "team": {"id": "team-1"},
            "state": {"id": "state-1", "name": "In Progress", "type": "started"},
            "labels": {"nodes": []}, "children": {"nodes": []}, "project": None}}}


DOOR_ENVS = ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE", "BUREAU_PIPELINE_REF",
             "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
             "GITHUB_REPOSITORY", "GITHUB_WORKFLOW", "GITHUB_JOB",
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
def door_up(monkeypatch, *, token: bool = True, max_age: str | None = "60", mode: str = "on"):
    """A door answering FRESH from WORLD, pointed at by the step's environment."""
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        env = door_env(door_url=door.url, issuer=issuer if token else None, mode=mode)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        if max_age is None:
            monkeypatch.delenv(linear_ops.CARD_MAX_AGE_ENV, raising=False)
        else:
            monkeypatch.setenv(linear_ops.CARD_MAX_AGE_ENV, max_age)
        yield door


@pytest.fixture
def door(linear, monkeypatch):
    with door_up(monkeypatch) as d:
        yield d


def card_reads(door: FakeDoor) -> list[dict]:
    return door.asked("/cards")


def fallback_lines(err: str) -> list[str]:
    return [ln for ln in err.splitlines() if ln.startswith(bureau_read.FALLBACK_TAG)]


def read_lines() -> list[dict]:
    out = []
    for line in linear_ops.read_lines():
        out.append(dict(part.split("=", 1) for part in line.split(" ")
                        if "=" in part and not part.startswith('reader=')))
    return out


# ── the contract's names ────────────────────────────────────────────────────


def test_the_contracts_names():
    assert linear_ops.CARD_MAX_AGE_ENV == "BUREAU_READ_CARD_MAX_AGE"
    assert linear_ops.CARD_SEEN_AFTER_ENV == "BUREAU_READ_CARD_SEEN_AFTER"
    assert linear_ops.OWN_WRITES_FILE == "linear-own-writes.jsonl"


def test_card_via_door_hands_back_the_node_and_its_read(door, linear):
    node, read = linear_ops.card_via_door("DRE-1")
    assert node["identifier"] == "DRE-1"
    assert node["id"] == "uuid-DRE-1"
    assert isinstance(read, bureau_read.DoorRead)
    assert read.as_of == door.as_of
    (asked,) = card_reads(door)
    assert asked["query"] == {"ids": "DRE-1", "comments": "50", "relations": "0"}
    assert linear.sent == []


def test_relations_ask_the_relations_max_age(door):
    node, _read = linear_ops.card_via_door("DRE-1", relations=True)
    assert "inverseRelations" in node
    (asked,) = card_reads(door)
    assert asked["query"]["relations"] == "1"
    assert asked["headers"]["x-bureau-relations-max-age"] == str(bureau_read.RELATIONS_MAX_AGE)


def test_a_linear_hold_answers_none(door, capsys):
    door.routes["/cards"] = door.unknown(bureau_read.LINEAR_HOLD)
    assert linear_ops.card_via_door("DRE-1") is None


# ── 1. the thread reads, off the door ───────────────────────────────────────


def test_comment_records_whole_thread_off_the_door(door, linear):
    got = linear_ops.comment_records("DRE-1", whole_thread=True)
    assert got == records("DRE-1")
    assert linear.thread_reads() == []
    (asked,) = card_reads(door)
    assert asked["query"]["comments"] == "all"


def test_comment_bodies_off_the_door(door, linear):
    assert linear_ops.comment_bodies("DRE-1") == [c["body"] for c in THREADS["DRE-1"]]
    assert linear.sent == []
    (asked,) = card_reads(door)
    assert asked["query"]["comments"] == str(linear_ops.COMMENT_WINDOW)


def test_count_comments_off_the_door(door, linear):
    assert linear_ops.count_comments("DRE-1", "receipt") == 2
    assert linear_ops.count_comments("DRE-1", "receipt", since="comment 2") == 1
    assert linear.sent == []


def test_thread_and_viewer_off_the_door(door, linear):
    nodes, me = linear_ops._thread_and_viewer("DRE-1", "body", "user", "createdAt")
    assert nodes == THREADS["DRE-1"]
    assert me == LINEAR_VIEWER
    assert linear.thread_reads() == []


def test_the_door_answers_what_a_linear_read_answers(linear, monkeypatch):
    linear_reads = {
        "records": linear_ops.comment_records("DRE-1", whole_thread=True),
        "bodies": linear_ops.comment_bodies("DRE-1"),
        "count": linear_ops.count_comments("DRE-1", "receipt"),
        "thread": linear_ops._thread_and_viewer("DRE-1", "body", "user", "createdAt"),
        "timeline": linear_ops.comment_timeline("DRE-1"),
    }
    assert linear.thread_reads(), "the baseline is a Linear read"
    with door_up(monkeypatch):
        linear.sent.clear()
        door_reads = {
            "records": linear_ops.comment_records("DRE-1", whole_thread=True),
            "bodies": linear_ops.comment_bodies("DRE-1"),
            "count": linear_ops.count_comments("DRE-1", "receipt"),
            "thread": linear_ops._thread_and_viewer("DRE-1", "body", "user", "createdAt"),
            "timeline": linear_ops.comment_timeline("DRE-1"),
        }
    assert door_reads == linear_reads
    assert linear.thread_reads() == []


def test_dump_comments_with_authors_off_the_door(door, linear, capsys):
    linear_ops.cmd_dump_comments("DRE-1", "--with-authors")
    assert json.loads(capsys.readouterr().out) == records("DRE-1")
    assert linear.thread_reads() == []


# ── 2. the viewer is never the door's ───────────────────────────────────────


def test_authorship_is_judged_against_linears_viewer(door, linear):
    rows = linear_ops.comment_records("DRE-1", whole_thread=True)
    by_door_key = [r for r, c in zip(rows, THREADS["DRE-1"])
                   if (c["user"] or {}).get("id") == DOOR_VIEWER]
    assert by_door_key and not any(r["authored_by_pipeline"] for r in by_door_key)
    assert [r["authored_by_pipeline"] for r in rows] == [
        (c["user"] or {}).get("id") == LINEAR_VIEWER for c in THREADS["DRE-1"]]


def test_two_whole_threads_off_the_door_send_one_viewer_read(door, linear):
    linear_ops.comment_records("DRE-1", whole_thread=True)
    linear_ops.comment_records("DRE-2", whole_thread=True)
    assert linear.queries() == ["query { viewer { id } }"]
    (line,) = read_lines()
    assert (line["caller"], line["count"]) == ("linear_ops.viewer_id", "1")


def test_the_viewer_is_asked_again_on_another_bucket(door, linear, monkeypatch):
    linear_ops.comment_records("DRE-1", whole_thread=True)
    monkeypatch.setattr(linear_ops, "spending_bucket", lambda: "another-bucket")
    linear_ops.comment_records("DRE-2", whole_thread=True)
    assert linear.queries() == ["query { viewer { id } }"] * 2


def test_comment_bodies_off_the_door_asks_no_viewer(door, linear):
    linear_ops.comment_bodies("DRE-1", whole_thread=True)
    linear_ops.comment_bodies("DRE-2")
    linear_ops.comment_timeline("DRE-1", whole_thread=True)
    linear_ops.count_comments("DRE-2", "receipt")
    assert linear.sent == []


# ── 3. a step opts in by naming its max-age ─────────────────────────────────

READS = {
    "comment_records": lambda: linear_ops.comment_records("DRE-1", whole_thread=True),
    "comment_bodies": lambda: linear_ops.comment_bodies("DRE-1"),
    "count_comments": lambda: linear_ops.count_comments("DRE-1", "receipt"),
    "thread_and_viewer": lambda: linear_ops._thread_and_viewer("DRE-1", "body", "user"),
    "comment_timeline": lambda: linear_ops.comment_timeline("DRE-1", whole_thread=True),
    "dump_comments": lambda: linear_ops.cmd_dump_comments("DRE-1", "--with-authors"),
    "description": lambda: linear_ops.cmd_description("DRE-1"),
    "comment": lambda: linear_ops.cmd_comment("DRE-1", "x"),
}

#: The Linear requests each read sends with the door off.
LINEAR_REQUESTS = {
    "comment_records": [linear_ops._THREAD_QUERY],
    "comment_bodies": [linear_ops._THREAD_QUERY],
    "count_comments": [linear_ops._THREAD_QUERY],
    "thread_and_viewer": [linear_ops._THREAD_QUERY],
    "comment_timeline": [linear_ops._THREAD_QUERY],
    "dump_comments": [linear_ops._THREAD_QUERY],
    "description": ["""query($id: String!) { issue(id: $id) { description } }"""],
    "comment": [TARGET_QUERY, "commentCreate"],
}


def _sent_like(sent: list[str], expected: list[str]) -> bool:
    return len(sent) == len(expected) and all(
        s == e or (e == "commentCreate" and "commentCreate(" in s)
        for s, e in zip(sent, expected))


@pytest.mark.parametrize("name", sorted(READS))
def test_with_the_door_off_every_read_sends_todays_requests(name, linear, monkeypatch,
                                                            capsys):
    with door_up(monkeypatch, mode="off") as door:
        READS[name]()
    assert _sent_like(linear.queries(), LINEAR_REQUESTS[name]), linear.queries()
    assert door.requests == []
    assert fallback_lines(capsys.readouterr().err) == []


@pytest.mark.parametrize("name", sorted(READS))
def test_no_max_age_reads_linear_and_asks_no_door(name, linear, monkeypatch, capsys):
    with door_up(monkeypatch, max_age=None) as door:
        READS[name]()
    assert _sent_like(linear.queries(), LINEAR_REQUESTS[name]), linear.queries()
    assert door.requests == []
    assert fallback_lines(capsys.readouterr().err) == []


@pytest.mark.parametrize("value", ["abc", "5", "3601", "19", "60s", "-60"])
@pytest.mark.parametrize("name", ["comment_records", "comment_bodies", "comment"])
def test_a_max_age_that_is_not_one_reads_linear_and_names_it_once(name, value, linear,
                                                                 monkeypatch, capsys):
    with door_up(monkeypatch, max_age=value) as door:
        READS[name]()
        READS[name]()
    assert _sent_like(linear.queries(), LINEAR_REQUESTS[name] * 2), linear.queries()
    assert door.requests == []
    err = capsys.readouterr().err
    assert fallback_lines(err) == []
    named = [ln for ln in err.splitlines() if linear_ops.CARD_MAX_AGE_ENV in ln]
    assert len(named) == 1 and repr(value) in named[0], err


@pytest.mark.parametrize("value", ["20", "300", "3600"])
def test_the_step_max_age_is_the_requests(value, linear, monkeypatch):
    with door_up(monkeypatch, max_age=value) as door:
        linear_ops.comment_bodies("DRE-1")
    (asked,) = card_reads(door)
    assert asked["headers"]["x-bureau-max-age"] == value
    assert linear.sent == []


def test_a_pass_with_no_max_age_reads_linear_on_a_miss(linear, monkeypatch):
    with door_up(monkeypatch, max_age=None) as door:
        linear_ops.open_pass()
        rows = linear_ops.comment_records("DRE-1")
    assert rows == records("DRE-1")
    assert linear.thread_reads() == [linear_ops._THREAD_QUERY]
    assert door.requests == []


def test_a_pass_with_a_max_age_asks_the_door_for_the_whole_thread(door, linear):
    linear_ops.open_pass()
    assert linear_ops.comment_records("DRE-1") == records("DRE-1")
    (asked,) = card_reads(door)
    assert asked["query"]["comments"] == "all"
    assert linear.thread_reads() == []


# ── fallbacks ───────────────────────────────────────────────────────────────


def test_a_thread_the_door_cannot_prove_whole_reads_linear(door, linear, capsys):
    door.incomplete_threads.add("DRE-1")
    assert linear_ops.comment_records("DRE-1", whole_thread=True) == records("DRE-1")
    assert linear.thread_reads() == [linear_ops._THREAD_QUERY]
    (line,) = fallback_lines(capsys.readouterr().err)
    assert "read=cards reason=thread-incomplete" in line


def test_no_token_falls_back_once_per_read(linear, monkeypatch, capsys):
    with door_up(monkeypatch, token=False) as door:
        linear_ops.comment_bodies("DRE-1")
        linear_ops.comment_records("DRE-2", whole_thread=True)
    assert len(linear.thread_reads()) == 2
    assert door.requests == []
    lines = fallback_lines(capsys.readouterr().err)
    assert len(lines) == 2
    assert all("read=cards reason=no-token" in ln for ln in lines)


def test_a_pull_request_run_asks_the_door_for_its_own_card_only(linear, monkeypatch,
                                                                capsys):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "agent/DRE-7-x")
    with door_up(monkeypatch) as door:
        assert linear_ops.comment_records("DRE-7", whole_thread=True) == records("DRE-7")
        assert linear.thread_reads() == []
        assert [r["query"]["ids"] for r in card_reads(door)] == ["DRE-7"]
        assert linear_ops.comment_records("DRE-8") == records("DRE-8")
        assert [r["query"]["ids"] for r in card_reads(door)] == ["DRE-7"]
    assert linear.thread_reads() == [linear_ops._THREAD_QUERY]
    (line,) = fallback_lines(capsys.readouterr().err)
    assert "read=cards reason=pull-request" in line


# ── the floor ───────────────────────────────────────────────────────────────


def test_an_answer_at_or_after_seen_after_is_used(door, linear, monkeypatch):
    monkeypatch.setenv(linear_ops.CARD_SEEN_AFTER_ENV, "2026-11-02T18:00:00Z")
    door.as_of = "2026-11-02T18:00:01Z"
    assert linear_ops.comment_bodies("DRE-1") == [c["body"] for c in THREADS["DRE-1"]]
    assert linear.sent == []


@pytest.mark.parametrize("seen_after,as_of", [
    ("2026-11-02T18:00:00Z", "2026-11-02T17:59:59Z"),
    ("soon", "2026-11-02T18:00:01Z"),
    ("2026-11-02T18:00:00Z", "yesterday"),
])
def test_an_answer_older_than_the_floor_reads_linear(seen_after, as_of, door, linear,
                                                     monkeypatch, capsys):
    monkeypatch.setenv(linear_ops.CARD_SEEN_AFTER_ENV, seen_after)
    door.as_of = as_of
    assert linear_ops.comment_bodies("DRE-1") == [c["body"] for c in THREADS["DRE-1"]]
    assert linear.thread_reads() == [linear_ops._THREAD_QUERY]
    (line,) = fallback_lines(capsys.readouterr().err)
    assert "read=cards reason=older-than-needed" in line


def test_an_empty_seen_after_is_no_floor(door, linear, monkeypatch):
    monkeypatch.setenv(linear_ops.CARD_SEEN_AFTER_ENV, "")
    assert linear_ops.card_via_door("DRE-1") is not None


def test_the_seen_after_argument_wins_over_the_variable(door, linear, monkeypatch):
    door.as_of = "2026-11-02T17:59:59Z"
    monkeypatch.setenv(linear_ops.CARD_SEEN_AFTER_ENV, "2026-11-02T18:00:00Z")
    assert linear_ops.card_via_door("DRE-1", seen_after="2026-11-02T17:00:00Z") is not None
    monkeypatch.setenv(linear_ops.CARD_SEEN_AFTER_ENV, "2026-11-02T17:00:00Z")
    assert linear_ops.card_via_door("DRE-1", seen_after="2026-11-02T18:00:00Z") is None


def test_floor_false_skips_the_floor(door, monkeypatch):
    monkeypatch.setenv(linear_ops.CARD_SEEN_AFTER_ENV, "2999-01-01T00:00:00Z")
    assert linear_ops.card_via_door("DRE-1") is None
    assert linear_ops.card_via_door("DRE-1", floor=False) is not None


# ── the comment's pre-read: `_comment_target` ───────────────────────────────


def test_comment_with_the_door_answering_sends_only_the_comment(door, linear):
    linear_ops.cmd_comment("DRE-1", "x")
    (only,) = linear.sent
    assert "commentCreate(" in only[0]
    assert only[1]["input"]["issueId"] == "uuid-DRE-1"
    (asked,) = card_reads(door)
    assert asked["query"]["comments"] == "1"


def test_comment_with_the_door_unknown_reads_the_id_alone(door, linear):
    door.routes["/cards"] = door.unknown("stale")
    linear_ops.cmd_comment("DRE-1", "x")
    assert _sent_like(linear.queries(), [TARGET_QUERY, "commentCreate"])
    (line,) = read_lines()
    assert line["caller"] == "linear_ops._comment_target"


def test_comment_inside_a_card_read_once_sends_no_pre_read(door, linear):
    with linear_ops._card_read_once("DRE-1"):
        linear.sent.clear()
        door.requests.clear()
        linear_ops.cmd_comment("DRE-1", "x")
    (only,) = linear.queries()
    assert "commentCreate(" in only
    assert door.requests == []


def test_comment_takes_the_id_whatever_seen_after_says(door, linear, monkeypatch):
    an_hour_on = linear_ops.datetime.now(linear_ops.UTC) + linear_ops.timedelta(hours=1)
    monkeypatch.setenv(linear_ops.CARD_SEEN_AFTER_ENV,
                       an_hour_on.isoformat().replace("+00:00", "Z"))
    assert linear_ops._comment_target("DRE-1") == "uuid-DRE-1"
    assert linear.sent == []


_CLI = r'''
import json, os, runpy, sys, urllib.request
tests, scripts = sys.argv[1], sys.argv[2]
sys.path[:0] = [scripts, tests]
from test_linear_ops_card_via_door import LinearStub
stub = LinearStub()
urllib.request.urlopen = stub
import bureau_read
bureau_read._wait = lambda _seconds: None  # a second ask never sleeps here (DRE-9250)
import atexit
def _save():
    with open(os.environ["STUB_SENT"], "w") as fh:
        json.dump(stub.queries(), fh)
atexit.register(_save)
if sys.argv[3] == "cli":
    sys.argv = [scripts + "/linear_ops.py", *sys.argv[4:]]
    runpy.run_path(scripts + "/linear_ops.py", run_name="__main__")
else:
    import linear_ops
    exec(sys.argv[4])
'''


def run(tmp_path, env_over: dict, kind: str, *args: str):
    """One process: `cli <command> <args…>` runs `linear_ops.py` as a step
    would, `py <code>` runs `code` with `linear_ops` imported. Linear is
    `LinearStub` in that process. Returns the process and its Linear queries."""
    sent = tmp_path / f"sent-{len(list(tmp_path.glob('sent-*')))}.json"
    env = {k: v for k, v in os.environ.items() if k not in DOOR_ENVS}
    env.update(LINEAR_API_KEY="test-key", LINEAR_IDENTITY="fleet", STUB_SENT=str(sent))
    for name in ("LINEAR_API_KEY_FALLBACK", "LINEAR_KEY_HOME", "GITHUB_EVENT_NAME",
                 "GITHUB_HEAD_REF"):
        env.pop(name, None)
    env.update(env_over)
    proc = subprocess.run(
        [sys.executable, "-c", _CLI, str(TESTS), str(ROOT / "scripts"), kind, *args],
        capture_output=True, text=True, env=env, timeout=120)
    return proc, json.loads(sent.read_text()) if sent.exists() else None


def door_env_for(door: FakeDoor, issuer: FakeIssuer, **over) -> dict:
    env = door_env(door_url=door.url, issuer=issuer)
    env[linear_ops.CARD_MAX_AGE_ENV] = "60"
    env.update(over)
    return env


def test_the_comment_command_off_the_door_sends_one_request(tmp_path):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        proc, sent = run(tmp_path, door_env_for(door, issuer), "cli", "comment", "DRE-1", "x")
    assert proc.returncode == 0, proc.stderr
    assert "commented on DRE-1" in proc.stdout
    assert len(sent) == 1 and "commentCreate(" in sent[0], sent
    assert any(ln.startswith("linear-calls: 1 request(s)") for ln in proc.stderr.splitlines())


def test_the_comment_command_names_its_pre_read_when_the_door_cannot_answer(tmp_path):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/cards"] = door.unknown("stale")
        proc, sent = run(tmp_path, door_env_for(door, issuer), "cli", "comment", "DRE-1", "x")
    assert proc.returncode == 0, proc.stderr
    assert _sent_like(sent, [TARGET_QUERY, "commentCreate"]), sent
    (line,) = [ln for ln in proc.stderr.splitlines() if ln.startswith("linear-read:")]
    assert "site=linear_ops.comment " in line
    assert line.endswith("caller=linear_ops._comment_target")


# ── description ─────────────────────────────────────────────────────────────


def test_description_off_the_door_sends_no_linear_request(door, linear, capsys):
    linear_ops.cmd_description("DRE-1")
    assert capsys.readouterr().out == "the description of DRE-1"
    assert linear.sent == []


def test_the_description_command_off_the_door(tmp_path):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        proc, sent = run(tmp_path, door_env_for(door, issuer), "cli", "description", "DRE-1")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "the description of DRE-1"
    assert sent == []


# ── what stays on Linear ────────────────────────────────────────────────────

STAYS = {
    "get_issue_fresh": lambda: linear_ops.get_issue("DRE-1", fresh=True),
    "history_read_back": lambda: linear_ops._clobbered_terminal_state(
        "DRE-1", "state-1", linear_ops.datetime.now(linear_ops.UTC)),
    "children_json": lambda: linear_ops.cmd_children_json("DRE-1"),
    "children": lambda: linear_ops.cmd_children("DRE-1"),
    "find_open": lambda: linear_ops.find_open("a title"),
}


@pytest.mark.parametrize("name", sorted(STAYS))
def test_the_reads_that_stay_on_linear_send_todays_requests(name, linear, monkeypatch):
    with door_up(monkeypatch, mode="off"):
        STAYS[name]()
    today = linear.queries()
    linear.sent.clear()
    with door_up(monkeypatch) as door:
        STAYS[name]()
    assert today and linear.queries() == today
    assert door.requests == []


def test_a_retried_comments_look_reads_linear(door, linear):
    look = linear_ops._comment_look("DRE-1", "never posted", linear_ops.datetime.now(
        linear_ops.UTC))
    assert look() is None
    assert linear.thread_reads() == [linear_ops._THREAD_QUERY]
    assert card_reads(door) == []
