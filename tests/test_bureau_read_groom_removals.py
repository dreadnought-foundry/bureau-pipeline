"""The read door's client reads every stored groom decision (DRE-7821).

agent-bureau DRE-6660's `GET /api/v1/pipeline/groom-removals` serves the
console's record of the groom decisions the CEO made on a proposal: every
card he took off a batch, and why. `bureau_read.groom_removals()` reads it the
way `split_history` reads the split ledger. What these pin, each against the
failure it exists to stop:

* the door's body unchanged, or `ReadUnknown` — never part of one (a wrong
  schema, a missing list, a reason or decision outside its shape, and a
  decision carrying `actor_login`, which the door must never serve);
* it is asked whatever `BUREAU_READ` says: there is no Linear copy behind it;
* one failure and the door is not asked again this run;
* a missing address or token is `ReadUnknown` before any request, saying
  which, so the groomer's memory can print why it fell back;
* a `since` that is not an instant with an offset is the caller's bug, a
  `ValueError`, before any request.

No test here reaches the console, GitHub or Linear: the door and the issuer
are `tests/bureau_read_fakes.py`, on 127.0.0.1.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bureau_read  # noqa: E402
from bureau_read_fakes import SCHEMA, FakeDoor, FakeIssuer, door_env  # noqa: E402

#: DRE-6660's decision row, exactly: the eleven fields the console serves.
FIELDS = ("id", "recorded_at", "standing_card", "proposal_id", "card", "decision",
          "reason", "hold_until_card", "hold_until_date", "actor_name", "comment")

SINCE = "2026-09-23T00:00:00Z"


@pytest.fixture(autouse=True)
def _fresh_client(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY",
                 "GITHUB_EVENT_NAME"):
        monkeypatch.delenv(name, raising=False)
    bureau_read.reset_for_tests()
    yield
    bureau_read.reset_for_tests()


def _point(monkeypatch, door, issuer, **kw):
    for key, value in door_env(door_url=door.url, issuer=issuer, **kw).items():
        monkeypatch.setenv(key, value)


def decision(card: str, **over) -> dict:
    row = {
        "id": f"gr-{card}", "recorded_at": "2026-10-20T13:05:00Z",
        "standing_card": "DRE-2683", "proposal_id": "p-20261020",
        "card": card, "decision": "hold", "reason": "not-ready",
        "hold_until_card": "DRE-7000", "hold_until_date": None,
        "actor_name": "The CEO", "comment": "after the door ships",
    }
    row.update(over)
    return row


def body(decisions=(), *, since=None) -> dict:
    return {
        "schema": SCHEMA,
        "read_at": "2026-10-23T13:00:00Z",
        "since": since,
        "reasons": [{"key": "not-ready", "label": "Not ready: waits on other work"},
                    {"key": "duplicate", "label": "Duplicate of another card"}],
        "decisions": [dict(d) for d in decisions],
    }


ANSWER = body([decision("DRE-7101"),
               decision("DRE-7102", decision="not-doing", reason="duplicate",
                        hold_until_card=None, comment=None),
               decision("DRE-7103", decision="put-back", reason=None,
                        hold_until_card=None, hold_until_date="2026-11-01")])


def _serve(door: FakeDoor, answer: dict) -> None:
    """The door's `GET /groom-removals`, echoing `since` the way DRE-6660 does."""
    def route(endpoint, query, headers):
        return 200, {**answer, "since": query.get("since")}
    door.routes["/groom-removals"] = route


# ── the contract the groomer-memory card reads ──────────────────────────────


def test_the_contract_constants():
    assert bureau_read.GROOM_REMOVALS_PATH == "/groom-removals"
    assert bureau_read.GROOM_REMOVALS_MAX_AGE == 3600
    assert bureau_read.GROOM_REMOVAL_FIELDS == FIELDS


# ── a well-formed answer ────────────────────────────────────────────────────


def test_a_well_formed_answer_is_returned_unchanged_and_sends_no_since(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _serve(door, ANSWER)
        _point(monkeypatch, door, issuer)
        got = bureau_read.groom_removals()
    sent = door.asked("/groom-removals")
    assert len(sent) == 1
    assert sent[0]["path"] == "/api/v1/pipeline/groom-removals"
    assert sent[0]["query"] == {}
    assert sent[0]["headers"]["authorization"].startswith("Bearer ")
    assert sent[0]["headers"]["x-bureau-max-age"] == str(bureau_read.GROOM_REMOVALS_MAX_AGE)
    assert got == ANSWER
    assert [d["decision"] for d in got["decisions"]] == ["hold", "not-doing", "put-back"]
    assert "served 1 " in bureau_read.exit_line()


def test_a_since_is_sent_once(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _serve(door, ANSWER)
        _point(monkeypatch, door, issuer)
        got = bureau_read.groom_removals(since=SINCE)
    sent = door.asked("/groom-removals")
    assert len(sent) == 1
    assert sent[0]["query"] == {"since": SINCE}
    assert got == {**ANSWER, "since": SINCE}


@pytest.mark.parametrize("since", ["2026-09-23T00:00:00+00:00",
                                   "2026-09-22T17:00:00-07:00",
                                   "2026-09-23T00:00:00.250Z"])
def test_a_since_with_an_offset_is_accepted(monkeypatch, since):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _serve(door, ANSWER)
        _point(monkeypatch, door, issuer)
        bureau_read.groom_removals(since=since)
    assert door.asked("/groom-removals")[0]["query"] == {"since": since}


@pytest.mark.parametrize("mode", [None, "off"])
def test_it_is_asked_whatever_the_mode_says(monkeypatch, mode):
    """The mode is the board reads' rollout switch against Linear. The groom
    decisions have no Linear copy behind them, so an unset or `off` mode does
    not stop the read."""
    with FakeIssuer() as issuer, FakeDoor() as door:
        _serve(door, ANSWER)
        _point(monkeypatch, door, issuer, mode=mode or "off")
        if mode is None:
            monkeypatch.delenv("BUREAU_READ")
        got = bureau_read.groom_removals()
    assert got == ANSWER
    assert len(door.asked("/groom-removals")) == 1


def test_an_empty_record_is_a_whole_answer(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _serve(door, body())
        _point(monkeypatch, door, issuer)
        got = bureau_read.groom_removals()
    assert got["decisions"] == []


# ── an answer outside the contract ──────────────────────────────────────────


@pytest.mark.parametrize("mutilate", [
    pytest.param(lambda b: b.update(schema="bureau-read/0"), id="wrong-schema"),
    pytest.param(lambda b: b.pop("schema"), id="no-schema"),
    pytest.param(lambda b: b.pop("reasons"), id="no-reasons"),
    pytest.param(lambda b: b.update(reasons={"not-ready": "Not ready"}), id="reasons-not-list"),
    pytest.param(lambda b: b.pop("decisions"), id="no-decisions"),
    pytest.param(lambda b: b.update(decisions=None), id="decisions-null"),
    pytest.param(lambda b: b["reasons"].append("not-ready"), id="reason-not-dict"),
    pytest.param(lambda b: b["reasons"][0].pop("label"), id="reason-no-label"),
    pytest.param(lambda b: b["reasons"][1].pop("key"), id="reason-no-key"),
    pytest.param(lambda b: b["reasons"][0].update(label=None), id="reason-label-null"),
    pytest.param(lambda b: b["reasons"][0].update(key=3), id="reason-key-not-str"),
    pytest.param(lambda b: b["decisions"].append("DRE-7104"), id="decision-not-dict"),
    pytest.param(lambda b: b["decisions"][1].update(actor_login="ceo-gh"),
                 id="decision-carries-actor-login"),
    *[pytest.param(lambda b, f=f: b["decisions"][2].pop(f), id=f"decision-lacks-{f}")
      for f in FIELDS],
])
def test_an_answer_outside_its_contract_is_malformed_never_partial(monkeypatch, mutilate):
    answer = body([decision("DRE-7101"), decision("DRE-7102"), decision("DRE-7103")])
    mutilate(answer)
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/groom-removals"] = (200, answer)
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.groom_removals()
    assert caught.value.reason == "malformed"
    assert "served 0 " in bureau_read.exit_line()


def test_a_bad_decision_is_named_by_its_card(monkeypatch):
    answer = body([decision("DRE-7101"), decision("DRE-7102", actor_login="ceo-gh")])
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/groom-removals"] = (200, answer)
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.groom_removals()
    assert "DRE-7102" in caught.value.detail
    assert "actor_login" in caught.value.detail


def test_a_decision_missing_a_field_is_named_by_its_card(monkeypatch):
    lacking = decision("DRE-7103")
    lacking.pop("hold_until_date")
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/groom-removals"] = (200, body([decision("DRE-7101"), lacking]))
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.groom_removals()
    assert "DRE-7103" in caught.value.detail
    assert "hold_until_date" in caught.value.detail


def test_an_answer_that_is_not_json_is_malformed(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/groom-removals"] = (200, b"<html>")
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.groom_removals()
    assert caught.value.reason == "malformed"


# ── a door that fails ───────────────────────────────────────────────────────


@pytest.mark.parametrize("status,reason", [(401, "refused"), (503, "closed")])
def test_a_refusal_is_unknown_and_stops_the_door(monkeypatch, status, reason):
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/groom-removals"] = (status, {"error": {"code": "X"}})
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.groom_removals()
        assert caught.value.reason == reason and caught.value.status == status
        _serve(door, ANSWER)
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.groom_removals()
        assert len(door.requests) == 1  # never asked again this run


def test_a_timeout_is_unknown_and_stops_the_door(monkeypatch):
    monkeypatch.setattr(bureau_read, "TOTAL_TIMEOUT", 0.4)
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/groom-removals"] = (200, ANSWER, 1.0)
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.groom_removals()
        assert caught.value.reason == "timeout"
        _serve(door, ANSWER)
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.groom_removals()
        assert len(door.requests) == 1  # never asked again this run


def test_a_malformed_answer_stops_the_door(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/groom-removals"] = (200, body([decision("DRE-7101",
                                                              actor_login="ceo-gh")]))
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.groom_removals()
        _serve(door, ANSWER)
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.groom_removals()
        assert len(door.requests) == 1


# ── a door setting that is missing ──────────────────────────────────────────


def test_with_no_address_it_says_which_setting_and_asks_nothing(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _serve(door, ANSWER)
        _point(monkeypatch, door, issuer)
        monkeypatch.delenv("BUREAU_READ_URL")
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.groom_removals()
        assert door.requests == [] and issuer.requests == []
    assert caught.value.reason == "unavailable"
    assert "BUREAU_READ_URL" in caught.value.detail


def test_with_no_token_it_says_so_and_asks_nothing(monkeypatch):
    with FakeDoor() as door:
        _serve(door, ANSWER)
        _point(monkeypatch, door, None)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.groom_removals()
        assert door.requests == []
    assert caught.value.reason == "no-token"
    assert "id-token: write" in caught.value.detail


def test_a_pull_request_run_never_asks(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _serve(door, ANSWER)
        _point(monkeypatch, door, issuer)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.groom_removals()
        assert door.requests == [] and issuer.requests == []
    assert caught.value.reason == "event-refused"


# ── a since that is not an instant ──────────────────────────────────────────


@pytest.mark.parametrize("since", [
    "2026-09-23T00:00:00",       # no offset
    "2026-09-23",                # a date, not an instant
    "",
    "   ",
    "yesterday",
    "2026-13-01T00:00:00Z",
    1727049600,
])
def test_a_since_that_is_not_an_instant_with_an_offset_is_a_value_error(monkeypatch, since):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _serve(door, ANSWER)
        _point(monkeypatch, door, issuer)
        with pytest.raises(ValueError):
            bureau_read.groom_removals(since=since)
        assert door.requests == [] and issuer.requests == []
