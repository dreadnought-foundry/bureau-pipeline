"""The read door's client reads the console's open alerts (DRE-8632).

agent-bureau's `GET /api/v1/pipeline/alerts` serves every system alert the
Alerts tab serves as open, at WARN or CRITICAL, oldest `raised_at` first.
`bureau_read.open_alerts()` reads it the way `split_history` and
`groom_removals` read theirs. What these pin, each against the failure it
exists to stop:

* the door's body unchanged, or `ReadUnknown` naming what was wrong — never
  part of one (a 503, a wrong schema, an `alerts` that is not a list, a row
  missing any of `OPEN_ALERT_FIELDS`);
* it is asked whatever `BUREAU_READ` says: there is no Linear copy behind it;
* it sends no query parameter, which the door answers 400.

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

#: The contract's row, exactly (DRE-8606).
FIELDS = ("kind", "severity", "title", "raised_at", "card")


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


def alert(kind: str, **over) -> dict:
    row = {"kind": kind, "severity": "WARN", "title": f"{kind} is open",
           "raised_at": "2026-10-30T17:46:00Z", "card": None}
    row.update(over)
    return row


def body(alerts=()) -> dict:
    return {"schema": SCHEMA, "read_at": "2026-11-01T16:00:00Z",
            "alerts": [dict(a) for a in alerts]}


ANSWER = body([
    alert("pipeline_stuck:DRE-7909", severity="CRITICAL", card="DRE-7909",
          raised_at="2026-10-30T10:00:00Z"),
    alert("pipeline_notice:standing-defect:bureau-pipeline:DRE-7305", card="DRE-7305"),
    alert("pipeline_notice:hold-late:portico:hand-work-overdue"),
])


# ── the contract the sibling cards read ─────────────────────────────────────


def test_the_contract_constants():
    assert bureau_read.OPEN_ALERTS_PATH == "/alerts"
    assert bureau_read.OPEN_ALERTS_MAX_AGE == 300
    assert bureau_read.OPEN_ALERT_FIELDS == FIELDS


# ── a well-formed answer ────────────────────────────────────────────────────


def test_a_well_formed_answer_is_returned_whole_and_sends_no_parameter(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/alerts"] = (200, ANSWER)
        _point(monkeypatch, door, issuer)
        got = bureau_read.open_alerts()
    sent = door.asked("/alerts")
    assert len(sent) == 1
    assert sent[0]["path"] == "/api/v1/pipeline/alerts"
    assert sent[0]["query"] == {}
    assert sent[0]["headers"]["authorization"].startswith("Bearer ")
    assert sent[0]["headers"]["x-bureau-max-age"] == str(bureau_read.OPEN_ALERTS_MAX_AGE)
    assert got == ANSWER
    assert [a["card"] for a in got["alerts"]] == ["DRE-7909", "DRE-7305", None]
    assert "served 1 " in bureau_read.exit_line()


@pytest.mark.parametrize("mode", [None, "off", "shadow", "on"])
def test_it_is_asked_whatever_the_mode_says(monkeypatch, mode):
    """The mode is the board reads' rollout switch against Linear. The open
    alerts have no Linear copy behind them, so an unset or `off` mode does
    not stop the read."""
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/alerts"] = (200, ANSWER)
        _point(monkeypatch, door, issuer, mode=mode or "off")
        if mode is None:
            monkeypatch.delenv("BUREAU_READ")
        got = bureau_read.open_alerts()
    assert got == ANSWER
    assert len(door.asked("/alerts")) == 1


def test_no_open_alert_is_a_whole_answer(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/alerts"] = (200, body())
        _point(monkeypatch, door, issuer)
        assert bureau_read.open_alerts()["alerts"] == []


# ── an answer outside the contract ──────────────────────────────────────────


@pytest.mark.parametrize("mutilate,named", [
    pytest.param(lambda b: b.update(schema="bureau-read/2"), "schema", id="wrong-schema"),
    pytest.param(lambda b: b.pop("schema"), "schema", id="no-schema"),
    pytest.param(lambda b: b.pop("alerts"), "alerts", id="no-alerts"),
    pytest.param(lambda b: b.update(alerts=None), "alerts", id="alerts-null"),
    pytest.param(lambda b: b.update(alerts={"kind": "x"}), "alerts", id="alerts-not-list"),
    pytest.param(lambda b: b["alerts"].append("pipeline_stuck:DRE-1"), "alert",
                 id="row-not-dict"),
    *[pytest.param(lambda b, f=f: b["alerts"][1].pop(f), f, id=f"row-lacks-{f}")
      for f in FIELDS],
])
def test_an_answer_outside_its_contract_is_unknown_naming_the_field(
        monkeypatch, mutilate, named):
    answer = body(ANSWER["alerts"])
    mutilate(answer)
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/alerts"] = (200, answer)
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.open_alerts()
    assert caught.value.reason == "malformed"
    assert named in caught.value.detail
    assert "served 0 " in bureau_read.exit_line()


def test_a_row_missing_a_field_is_named_by_its_kind(monkeypatch):
    answer = body(ANSWER["alerts"])
    answer["alerts"][1].pop("raised_at")
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/alerts"] = (200, answer)
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.open_alerts()
    assert "pipeline_notice:standing-defect:bureau-pipeline:DRE-7305" in caught.value.detail
    assert "raised_at" in caught.value.detail


def test_an_answer_that_is_not_json_is_unknown(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/alerts"] = (200, b"<html>")
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.open_alerts()
    assert caught.value.reason == "malformed"


# ── a door that fails ───────────────────────────────────────────────────────


@pytest.mark.parametrize("status,reason", [(503, "closed"), (400, "unavailable"),
                                           (401, "refused")])
def test_a_refusal_is_unknown_never_an_empty_list(monkeypatch, status, reason):
    """The door answers 503 when its database cannot be read, never an empty
    list in its place — and the client never invents one either."""
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/alerts"] = (status, {"error": {"code": "X"}})
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.open_alerts()
    assert caught.value.reason == reason and caught.value.status == status


def test_with_no_address_it_says_which_setting_and_asks_nothing(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/alerts"] = (200, ANSWER)
        _point(monkeypatch, door, issuer)
        monkeypatch.delenv("BUREAU_READ_URL")
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.open_alerts()
        assert door.requests == [] and issuer.requests == []
    assert "BUREAU_READ_URL" in caught.value.detail


def test_a_pull_request_run_never_asks(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor() as door:
        door.routes["/alerts"] = (200, ANSWER)
        _point(monkeypatch, door, issuer)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.open_alerts()
        assert door.requests == [] and issuer.requests == []
    assert caught.value.reason == "event-refused"
