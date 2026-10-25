"""RED-first: `bureau_read.search`, the groomer's client for DRE-6100's
`GET /api/v1/pipeline/search` (DRE-7236).

What these pin, against the existing fake door (`tests/bureau_read_fakes.py`),
through real HTTP on 127.0.0.1:

* the route, the query (`term`, `first`, `scope`) and the headers the client
  sends on every read — a FRESH OIDC token each time, the max-age always;
* only nodes carrying exactly `{identifier, title, createdAt, state: {name}}`
  are accepted: a missing field and an extra one are both a door answering
  outside its contract;
* a `scope=fleet` 401, 403 or 404 is UNKNOWN for that one read and the door
  stays in use (the exception the client already makes for the board,
  DRE-5848), while a 5xx stops the door for the run;
* the mode is read once, and `off` sends nothing.

Run: cd bureau-pipeline && python3 -m pytest tests/test_bureau_read_search.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bureau_read  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, door_env  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_client(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY"):
        monkeypatch.delenv(name, raising=False)
    bureau_read.reset_for_tests()
    yield
    bureau_read.reset_for_tests()


def _point(monkeypatch, door, issuer, **kw):
    for key, value in door_env(door_url=door.url, issuer=issuer, **kw).items():
        monkeypatch.setenv(key, value)


def hit(ident, *, title=None, created="2026-10-06T02:49:26.858Z", state="Done"):
    """One search node, exactly DRE-6100's four fields."""
    return {"identifier": ident, "title": title or f"{ident} title",
            "createdAt": created, "state": {"name": state}}


def serve(door, nodes):
    """Answer every `/search` with `nodes`, FRESH, whatever the term."""
    door.routes["/search"] = lambda endpoint, query, headers: (
        200, door.envelope(list(nodes)))


# ── what it sends ──────────────────────────────────────────────────────────


def test_search_sends_the_route_query_and_headers_and_returns_the_nodes(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        serve(door, [hit("DRE-5942")])
        read = bureau_read.search("stranded run")
    assert isinstance(read, bureau_read.DoorRead)
    assert read.nodes == [hit("DRE-5942")]
    [sent] = door.requests
    assert sent["path"] == "/api/v1/pipeline/search"
    assert sent["query"] == {"term": "stranded run", "first": "25", "scope": "fleet"}
    assert sent["headers"]["authorization"].startswith("Bearer ")
    assert sent["headers"]["accept"] == "application/json"
    assert sent["headers"]["x-bureau-max-age"] == str(bureau_read.SEARCH_MAX_AGE)
    assert "x-bureau-relations-max-age" not in sent["headers"]


def test_search_passes_first_and_scope_as_asked(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        serve(door, [])
        bureau_read.search("docs/evidence/DRE-5390/README.md", first=50, scope="repo")
    assert door.requests[0]["query"] == {
        "term": "docs/evidence/DRE-5390/README.md", "first": "50", "scope": "repo"}


def test_every_search_carries_a_fresh_token(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        serve(door, [])
        bureau_read.search("one")
        bureau_read.search("two")
    tokens = [r["headers"]["authorization"] for r in door.requests]
    assert len(issuer.requests) == 2
    assert len(set(tokens)) == 2


@pytest.mark.parametrize("call", [
    lambda: bureau_read.search("x", first=0),
    lambda: bureau_read.search("x", first=51),
    lambda: bureau_read.search("x", first=True),
    lambda: bureau_read.search("x", scope="team"),
    lambda: bureau_read.search("   "),
])
def test_a_search_outside_the_route_is_refused_before_it_is_sent(monkeypatch, call):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        serve(door, [])
        with pytest.raises(ValueError):
            call()
    assert door.requests == []


def test_off_sends_nothing(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer, mode="off")
        serve(door, [hit("DRE-1")])
        with pytest.raises(bureau_read.ReadUnknown) as raised:
            bureau_read.search("anything")
    assert raised.value.reason == "off"
    assert door.requests == [] and issuer.requests == []


def test_the_mode_is_read_once_for_search_too(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer, mode="off")
        serve(door, [hit("DRE-1")])
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.search("anything")
        monkeypatch.setenv("BUREAU_READ", "on")  # flipped mid-run
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.search("anything")
    assert door.requests == []


# ── exactly the four fields ────────────────────────────────────────────────


@pytest.mark.parametrize("node, lacks", [
    ({"identifier": "DRE-1", "title": "t", "createdAt": "2026-10-01T00:00:00Z"},
     "state"),
    ({"identifier": "DRE-1", "title": "t", "state": {"name": "Done"}}, "createdAt"),
    ({"identifier": "DRE-1", "title": "t", "createdAt": "2026-10-01T00:00:00Z",
      "state": {"name": "Done"}, "description": "not one of the four"},
     "description"),
    ({"identifier": "DRE-1", "title": "t", "createdAt": "2026-10-01T00:00:00Z",
      "state": {"name": "Done", "type": "completed"}}, "state.type"),
    ({"identifier": "DRE-1", "title": "t", "createdAt": "2026-10-01T00:00:00Z",
      "state": None}, "state"),
])
def test_a_node_without_exactly_the_four_fields_is_malformed(monkeypatch, node, lacks):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        serve(door, [hit("DRE-2"), node])
        with pytest.raises(bureau_read.ReadUnknown) as raised:
            bureau_read.search("anything")
    assert raised.value.reason == "malformed"
    assert lacks in str(raised.value)
    # A door answering outside its contract is not asked again this run.
    assert bureau_read.disabled_reason() == "malformed"


def test_a_fresh_answer_with_no_nodes_is_an_answer_of_none(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        serve(door, [])
        assert bureau_read.search("nothing matches").nodes == []


# ── what stops the door, and what does not ─────────────────────────────────


@pytest.mark.parametrize("status, reason", [
    (401, "refused"), (403, "refused"), (404, "not-found")])
def test_a_fleet_refusal_is_unknown_for_that_read_and_the_door_stays(
        monkeypatch, status, reason):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        door.routes["/search"] = (status, {"error": {"code": "FORBIDDEN"}})
        with pytest.raises(bureau_read.ReadUnknown) as raised:
            bureau_read.search("stranded run", scope="fleet")
        assert raised.value.reason == reason
        assert raised.value.status == status
        assert bureau_read.disabled_reason() is None
        serve(door, [hit("DRE-5942")])
        read = bureau_read.search("stranded run", scope="fleet")
    assert read.nodes == [hit("DRE-5942")]
    assert len(door.requests) == 2


def test_a_repo_scope_refusal_stops_the_door_as_any_read_does(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        door.routes["/search"] = (403, {"error": {"code": "FORBIDDEN"}})
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.search("x", scope="repo")
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.search("x", scope="fleet")
    assert bureau_read.disabled_reason() == "refused"
    assert len(door.requests) == 1


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_5xx_stops_the_door_for_the_run(monkeypatch, status):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        door.routes["/search"] = (status, {"error": {"code": "DOWN"}})
        with pytest.raises(bureau_read.ReadUnknown) as raised:
            bureau_read.search("stranded run")
        assert raised.value.unavailable is True
        assert bureau_read.disabled_reason() is not None
        serve(door, [hit("DRE-5942")])
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.search("stranded run")
    assert not bureau_read.enabled()
    assert len(door.requests) == 1


def test_an_unknown_verdict_is_unknown_and_the_door_stays(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        door.routes["/search"] = door.unknown("stale")
        with pytest.raises(bureau_read.ReadUnknown) as raised:
            bureau_read.search("x")
    assert raised.value.reason == "stale"
    assert bureau_read.disabled_reason() is None


def test_no_token_is_unknown_and_never_reaches_the_door(monkeypatch):
    with FakeDoor() as door:
        _point(monkeypatch, door, None)
        serve(door, [hit("DRE-1")])
        with pytest.raises(bureau_read.ReadUnknown) as raised:
            bureau_read.search("x")
    assert raised.value.reason == "no-token"
    assert door.requests == []


def test_a_served_search_is_counted_on_the_exit_line(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        serve(door, [hit("DRE-1")])
        bureau_read.search("x")
    assert bureau_read.exit_line().startswith("read-door: served 1 unknown 0")
