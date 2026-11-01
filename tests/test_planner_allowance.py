"""A planner reads the planners' Linear allowance before it starts (DRE-8616,
epic DRE-8539).

The CEO's decision of 2026-10-31: no fixed number of planners. Before a planner
starts it reads how much of the planners' Linear allowance is left. Above the
threshold it starts at once; below it, it waits to the next `wait_minutes`
boundary and reads again; a `RATELIMITED` answer counts as below; at
`hold_max_minutes` it starts whatever the reading, after its stagger.

EVERY TEST THAT INVOLVES LINEAR GOES THROUGH `linear_ops.gql`, NOT AROUND IT.
Only the network under it is replaced — `linear_ops.urllib.request.urlopen`,
driven by the scripted transport `tests/test_linear_ops_budget.py` already
uses — so Linear's own `RATELIMITED` 400 reaches `gql`'s classification, the
stop really arms, and the transport's own count says which requests were sent.
The clock and the sleep are injected.
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
import planner_allowance as pa  # noqa: E402
import proof_reach  # noqa: E402
from test_linear_ops_budget import _Resp, _Transport, _ratelimited_400  # noqa: E402

LIMIT = 5000
RESET_MS = 1793556000000  # 2026-11-01 18:00 UTC
T0 = datetime(2026, 11, 1, 17, 0, 0, tzinfo=timezone.utc)  # on a 5-minute boundary
REPO = "dreadnought-foundry/agent-bureau"
FIXTURE_REPO = "dreadnought-foundry/agent-bureau-demo"
CFG = pa.load()
W = CFG["wait_minutes"]
H = CFG["hold_max_minutes"]
STAGGER = CFG["stagger_seconds"]
THRESHOLD = CFG["threshold_remaining"]

LINE = re.compile(
    r"^planner-allowance: (start|hold) · card (DRE-\d+) · remaining (\d+|unknown) of "
    r"(\d+|unknown) · threshold (\d+) · read (answered|refused|failed) · receipt "
    r"(held|started|deferred|refused|none) · requests (\d+) · at "
    r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)$"
)


def _iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# the wire                                                                     #
# --------------------------------------------------------------------------- #


def _headers(remaining, limit=LIMIT) -> dict:
    h = {"x-ratelimit-requests-reset": str(RESET_MS)}
    if remaining is not None:
        h["x-ratelimit-requests-remaining"] = str(remaining)
    if limit is not None:
        h["x-ratelimit-requests-limit"] = str(limit)
    return h


VIEWER = json.dumps({"data": {"viewer": {"id": "u-planner"}}}).encode()
ISSUE = json.dumps({"data": {"issue": {
    "id": "issue-uuid", "identifier": "DRE-1", "title": "t", "team": {"id": "team"},
    "state": {"id": "s", "name": "Planning", "type": "started"},
    "labels": {"nodes": []}, "children": {"nodes": []}, "project": None}}}).encode()
CREATED = json.dumps({"data": {"commentCreate": {"success": True}}}).encode()


def _read(remaining, limit=LIMIT):
    """Linear answering the allowance read."""
    return _Resp(VIEWER, _headers(remaining, limit))


def _refused(remaining=0, limit=LIMIT):
    """Linear's own RATELIMITED 400, a fresh one each time: its body is a
    stream, read once. `remaining=None` sends no rate-limit header at all."""
    if remaining is None:
        return _ratelimited_400({})
    return _ratelimited_400(_headers(remaining, limit))


def _post():
    """A receipt that lands: `cmd_comment` reads the card, then creates."""
    return [_Resp(ISSUE, _headers(None, None)), _Resp(CREATED, _headers(None, None))]


def _post_refused():
    """A receipt Linear refuses: the card read answers, the create is refused."""
    return [_Resp(ISSUE, _headers(None, None)), _refused(0)]


def _held_body(card, at, *, run="9", remaining=312):
    until = at + timedelta(minutes=H + W)
    return (f"🪫 planner-hold: held · card {card} · run {run} · repo {REPO} · "
            f"remaining {remaining} of {LIMIT} · next read at {_iso(at + timedelta(minutes=W))} · "
            f"every {W} min · until {_iso(until)} · at {_iso(at)}")


def _node(card, body, created, cid=None):
    return {"id": cid or f"c-{card}-{_iso(created)}", "body": body,
            "createdAt": _iso(created), "issue": {"identifier": card}}


def _line(nodes=()):
    """Linear answering the held-line read."""
    return _Resp(json.dumps({"data": {"comments": {
        "nodes": list(nodes), "pageInfo": {"hasNextPage": False, "endCursor": None}}}}).encode(),
        _headers(None, None))


class _Wire(_Transport):
    """The budget suite's transport, also keeping each request it was sent."""

    def __init__(self, *outcomes, events=None):
        flat = []
        for o in outcomes:
            flat.extend(o if isinstance(o, list) else [o])
        super().__init__(*flat)
        self.sent = []
        self.events = events

    def __call__(self, req, *args, **kwargs):
        payload = json.loads(req.data)
        self.sent.append(payload)
        if self.events is not None:
            self.events.append(("request", _kind(payload)))
        return super().__call__(req, *args, **kwargs)

    def receipts(self) -> list:
        """Every receipt body a commentCreate carried, in order."""
        return [p["variables"]["input"]["body"] for p in self.sent
                if "commentCreate" in p["query"]]


def _kind(payload) -> str:
    q = payload["query"]
    if "viewer" in q:
        return "read"
    if "commentCreate" in q:
        return "post:" + payload["variables"]["input"]["body"].split(" · ")[0].split(": ")[1]
    if "comments(" in q:
        return "line"
    return "card"


class _Clock:
    def __init__(self, at=T0, events=None, wire=None):
        self.t = at
        self.slept = []
        self.events = events
        self.wire = wire

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        if self.events is not None:
            self.events.append(("sleep", seconds))
        self.t += timedelta(seconds=seconds)


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    for name in (linear_ops.IDENTITY_ENV, linear_ops.FALLBACK_ENV, pa.FAKE_ENV,
                 pa.CONFIG_ENV, "GITHUB_ACTIONS"):
        monkeypatch.delenv(name, raising=False)
    linear_ops._card_memo.clear()
    events: list = []

    def _door():
        raise bureau_read.ReadUnknown("unavailable", "no door in this test")

    monkeypatch.setattr(bureau_read, "planning_order", _door)
    real_lift = linear_ops.lift_rate_limit_stop

    def _lift(why):
        events.append(("lift", why))
        return real_lift(why)

    monkeypatch.setattr(linear_ops, "lift_rate_limit_stop", _lift)

    def _install(*outcomes):
        linear_ops._reset_budget_state()
        linear_ops._card_memo.clear()
        events.clear()
        t = _Wire(*outcomes, events=events)
        monkeypatch.setattr(linear_ops.urllib.request, "urlopen", t)
        return t

    _install.events = events
    return _install


def _wait(card="DRE-1", run="1001", repo=REPO, title="Some card", clock=None, events=None):
    clock = clock or _Clock(events=events)
    out = pa.wait(card, run, repo, title, now=clock.now, sleep=clock.sleep)
    return out, clock


def _lines(capsys) -> list:
    out = capsys.readouterr().out
    return [ln for ln in out.splitlines() if ln.startswith(pa.TAG)]


def _assert_grammar(lines):
    assert lines, "no planner-allowance line was printed"
    for ln in lines:
        assert LINE.match(ln), ln


def _low_until_hold_max(remaining=312):
    """Answered low reads from the first boundary after T0 to the hold-max read."""
    return [_read(remaining) for _ in range(H // W)]


# --------------------------------------------------------------------------- #
# the decision                                                                 #
# --------------------------------------------------------------------------- #


def test_a_healthy_first_read_starts_at_once_after_one_request(wire, capsys):
    t = wire(_read(4200))
    out, clock = _wait()
    assert out == {"start": "true", "because": "allowance-ok"}
    assert t.calls == 1 and linear_ops.requests_made() == 1
    assert t.receipts() == []
    assert clock.slept == []
    lines = _lines(capsys)
    _assert_grammar(lines)
    assert lines == [f"planner-allowance: start · card DRE-1 · remaining 4200 of 5000 · "
                     f"threshold {THRESHOLD} · read answered · receipt none · requests 1 · "
                     f"at {_iso(T0)}"]


def test_a_low_read_holds_posts_held_waits_to_the_boundary_then_starts(wire, capsys):
    t = wire(_read(312), _post(), _read(4200), _line(), _post())
    out, clock = _wait()
    assert out == {"start": "true", "because": "allowance-ok"}
    nxt = T0 + timedelta(minutes=W)
    assert t.receipts() == [
        _held_body("DRE-1", T0, run="1001"),
        f"🪫 planner-hold: started · card DRE-1 · run 1001 · repo {REPO} · "
        f"remaining 4200 of 5000 · because allowance-ok · at {_iso(nxt)}",
    ]
    assert "remaining 312 of 5000" in t.receipts()[0]
    assert clock.slept == [W * 60, 0]
    assert linear_ops.requests_made() == t.calls == 7
    lines = _lines(capsys)
    _assert_grammar(lines)
    assert [LINE.match(ln).group(1, 7) for ln in lines] == [("hold", "held"),
                                                           ("start", "started")]


def test_a_real_refusal_then_a_re_read_lifts_the_stop_once_after_the_sleep(wire, capsys):
    t = wire(_refused(0), _read(312), _post(), _read(4200), _line(), _post())
    events = wire.events
    out, clock = _wait(events=events)
    assert out == {"start": "true", "because": "allowance-ok"}
    kinds = [e[0] if e[0] != "request" else e[1] for e in events]
    # After the refusal nothing is sent until the sleep returns; the stop is
    # lifted once, after that sleep, and the re-read IS sent.
    assert kinds[:4] == ["read", "sleep", "lift", "read"]
    assert kinds.count("lift") == 1
    assert kinds == ["read", "sleep", "lift", "read", "card", "post:held", "sleep",
                     "read", "line", "sleep", "card", "post:started"]
    held, started = t.receipts()
    assert held == (f"🪫 planner-hold: held · card DRE-1 · run 1001 · repo {REPO} · "
                    f"remaining 0 of 5000 · next read at {_iso(T0 + timedelta(minutes=2 * W))} · "
                    f"every {W} min · until {_iso(T0 + timedelta(minutes=H + W))} · "
                    f"at {_iso(T0)}")
    assert started.startswith("🪫 planner-hold: started · card DRE-1 · run 1001")
    assert started.endswith(f"because allowance-ok · at {_iso(T0 + timedelta(minutes=2 * W))}")
    assert linear_ops.requests_made() == t.calls == 8
    lines = _lines(capsys)
    _assert_grammar(lines)
    assert "read refused · receipt deferred · requests 1 ·" in lines[0]
    assert "remaining 312 of 5000" in lines[1] and "receipt held · requests 4 ·" in lines[1]
    assert "receipt started · requests 8 ·" in lines[2]


def test_a_refused_hold_that_ends_on_a_good_read_posts_only_started(wire, capsys):
    t = wire(_refused(0), _read(4200), _line(), _post())
    out, _ = _wait()
    assert out == {"start": "true", "because": "allowance-ok"}
    receipts = t.receipts()
    assert len(receipts) == 1
    assert receipts[0].startswith("🪫 planner-hold: started")
    assert "because allowance-ok" in receipts[0]
    lines = _lines(capsys)
    _assert_grammar(lines)
    assert "read refused · receipt deferred" in lines[0]


@pytest.mark.parametrize("header,shown", [(0, "0 of 5000"), (None, "unknown of unknown")])
def test_every_read_refused_starts_at_hold_max_after_the_run_id_stagger(
        wire, capsys, monkeypatch, header, shown):
    reads = H // W + 1
    t = wire(*[_refused(header) for _ in range(reads + 5)])
    tried = []
    real = linear_ops.cmd_comment

    def _spy(identifier, body, *flags):
        tried.append(body)
        return real(identifier, body, *flags)

    monkeypatch.setattr(linear_ops, "cmd_comment", _spy)
    out, clock = _wait(run="1007", events=wire.events)
    assert out == {"start": "true", "because": "hold-max"}
    # One read per boundary, and nothing else sent: no receipt went out.
    assert t.calls == reads
    assert all(_kind(p) == "read" for p in t.sent)
    # The held line was refused too, so the place is run_id mod 30.
    assert clock.slept == [W * 60] * (reads - 1) + [(1007 % 30) * STAGGER]
    assert len(tried) == 1 and tried[0].startswith("🪫 planner-hold: started")
    assert "because hold-max" in tried[0]
    # Every lift follows a sleep, never two without a sleep between.
    seq = [e[0] for e in wire.events if e[0] in ("sleep", "lift")]
    for i, e in enumerate(seq):
        if e == "lift":
            assert i > 0 and seq[i - 1] == "sleep", seq
    assert seq.count("lift") == reads - 1
    lines = _lines(capsys)
    _assert_grammar(lines)
    assert len(lines) == reads
    assert all(f"remaining {shown}" in ln for ln in lines)
    assert all("read refused · receipt deferred" in ln for ln in lines[:-1])
    assert lines[-1].startswith("planner-allowance: start")
    assert "read refused · receipt refused" in lines[-1]


def test_main_exits_0_when_every_read_is_refused(wire, tmp_path):
    reads = H // W + 1
    wire(*[_refused(0) for _ in range(reads + 5)])
    out_file = tmp_path / "out"
    clock = _Clock()
    code = pa.main(["wait", "DRE-1", "--run-id", "1007", "--repo", REPO, "--title", "x",
                    "--github-output", str(out_file)], now=clock.now, sleep=clock.sleep)
    assert code == 0
    assert out_file.read_text().splitlines() == ["start=true", "because=hold-max"]


def test_a_refused_receipt_is_retried_at_the_next_answered_low_read_not_before(wire, capsys):
    t = wire(_read(312), _post_refused(), _read(300), _post(), _read(4200), _line(), _post())
    out, _ = _wait(events=wire.events)
    assert out == {"start": "true", "because": "allowance-ok"}
    kinds = [e[0] if e[0] != "request" else e[1] for e in wire.events]
    assert kinds == ["read", "card", "post:held", "sleep", "lift", "read", "card",
                     "post:held", "sleep", "read", "line", "sleep", "card", "post:started"]
    first, second, started = t.receipts()
    assert first == _held_body("DRE-1", T0, run="1001")
    # The retry still reports the read that began the hold; only its next
    # read has moved on.
    assert second == first.replace(f"next read at {_iso(T0 + timedelta(minutes=W))}",
                                   f"next read at {_iso(T0 + timedelta(minutes=2 * W))}")
    lines = _lines(capsys)
    _assert_grammar(lines)
    assert "read answered · receipt refused" in lines[0]
    assert "remaining 300 of 5000" in lines[1] and "receipt held" in lines[1]


@pytest.mark.parametrize("failure", ["5xx", "network", "no-key", "garbled"])
def test_a_read_that_fails_otherwise_starts_at_once_because_read_failed(
        wire, capsys, monkeypatch, failure):
    if failure == "5xx":
        t = wire(*[urllib.error.HTTPError(linear_ops.API, 500, "err", {},
                                          io.BytesIO(b"oops")) for _ in range(3)])
    elif failure == "network":
        t = wire(*[urllib.error.URLError("unknown url type") for _ in range(3)])
    elif failure == "garbled":
        t = wire(_Resp(b"not json", _headers(4000)))
    else:
        t = wire(_read(4200))
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.delenv("LINEAR_API_KEY")
    out, clock = _wait()
    assert out == {"start": "true", "because": "read-failed"}
    assert t.receipts() == []
    assert clock.slept == []
    printed = capsys.readouterr().out
    assert "::warning::" in printed


def test_a_re_read_that_fails_mid_hold_starts_because_read_failed(wire, capsys):
    t = wire(_read(312), _post(), urllib.error.URLError("unknown url type"))
    out, clock = _wait()
    assert out == {"start": "true", "because": "read-failed"}
    assert len(t.receipts()) == 1  # the held; nothing after the failed read
    assert clock.slept == [W * 60]
    assert "::warning::" in capsys.readouterr().out


def test_a_bad_config_never_fails_the_wait(wire, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv(pa.CONFIG_ENV, str(tmp_path / "missing.json"))
    t = wire(_read(10))
    out, _ = _wait()
    assert out == {"start": "true", "because": "read-failed"}
    assert t.calls == 0
    assert "::warning::" in capsys.readouterr().out


def test_low_answered_reads_start_at_hold_max(wire, capsys):
    t = wire(_read(312), _post(), *_low_until_hold_max(400), _line(), _post())
    out, clock = _wait()
    assert out == {"start": "true", "because": "hold-max"}
    assert clock.slept == [W * 60] * (H // W) + [0]
    held, started = t.receipts()
    assert held.startswith("🪫 planner-hold: held")
    assert started == (f"🪫 planner-hold: started · card DRE-1 · run 1001 · repo {REPO} · "
                       f"remaining 400 of 5000 · because hold-max · "
                       f"at {_iso(T0 + timedelta(minutes=H))}")
    lines = _lines(capsys)
    _assert_grammar(lines)
    assert len(lines) == H // W + 1


# --------------------------------------------------------------------------- #
# the stagger and the held line                                                #
# --------------------------------------------------------------------------- #


def _order(monkeypatch, cards):
    monkeypatch.setattr(bureau_read, "planning_order", lambda: bureau_read.PlanningOrder(
        order=list(cards), set_at="2026-11-01T16:00:00Z", set_by="the CEO"))


def test_three_held_cards_at_hold_max_stagger_in_the_ceos_order(wire, monkeypatch):
    cards = {"DRE-11": T0, "DRE-12": T0 + timedelta(seconds=1),
             "DRE-13": T0 + timedelta(seconds=2)}
    held_line = [_node(c, _held_body(c, at), at) for c, at in cards.items()]
    _order(monkeypatch, ["DRE-13", "DRE-11", "DRE-12"])
    slept = {}
    for card, at in cards.items():
        wire(_read(312), _post(), *_low_until_hold_max(), _line(held_line), _post())
        out, clock = _wait(card=card, clock=_Clock(at))
        assert out == {"start": "true", "because": "hold-max"}
        slept[card] = clock.slept[-1]
    assert slept == {"DRE-13": 0, "DRE-11": STAGGER, "DRE-12": 2 * STAGGER}
    assert len(set(slept.values())) == 3


def test_two_held_cards_with_a_good_read_start_in_the_ceos_order(wire, monkeypatch):
    cards = {"DRE-21": T0, "DRE-22": T0 + timedelta(minutes=1)}
    held_line = [_node(c, _held_body(c, at), at) for c, at in cards.items()]
    _order(monkeypatch, ["DRE-22", "DRE-21"])
    slept = {}
    for card, at in cards.items():
        wire(_read(312), _post(), _read(4200), _line(held_line), _post())
        out, clock = _wait(card=card, clock=_Clock(at))
        assert out == {"start": "true", "because": "allowance-ok"}
        slept[card] = clock.slept[-1]
    assert slept == {"DRE-22": 0, "DRE-21": STAGGER}


def test_with_the_door_failing_held_cards_start_oldest_held_first(wire, capsys):
    cards = {"DRE-22": T0 + timedelta(minutes=1), "DRE-21": T0}
    held_line = [_node(c, _held_body(c, at), at) for c, at in cards.items()]
    slept = {}
    for card, at in cards.items():
        wire(_read(312), _post(), _read(4200), _line(held_line), _post())
        _, clock = _wait(card=card, clock=_Clock(at))
        slept[card] = clock.slept[-1]
    assert slept == {"DRE-21": 0, "DRE-22": STAGGER}
    assert "arrival order" in capsys.readouterr().err


@pytest.mark.parametrize("path", ["allowance-ok", "hold-max"])
def test_place_40_sleeps_wait_minutes_not_400_seconds(wire, path):
    ahead = []
    for i in range(40):
        # Older than this run's hold, and still live at the hold-max read.
        at = T0 - timedelta(minutes=4) + timedelta(seconds=i)
        ahead.append(_node(f"DRE-{100 + i}", _held_body(f"DRE-{100 + i}", at), at))
    later = _read(4200) if path == "allowance-ok" else _low_until_hold_max()
    wire(_read(312), _post(), later, _line(ahead), _post())
    out, clock = _wait()
    assert out["because"] == path
    assert clock.slept[-1] == W * 60 == 300


def test_a_held_line_read_that_fails_gives_place_run_id_mod_30(wire, capsys):
    wire(_read(312), _post(), _read(4200),
         urllib.error.URLError("unknown url type"), _post())
    out, clock = _wait(run="1045")
    assert out == {"start": "true", "because": "allowance-ok"}
    assert clock.slept[-1] == (1045 % 30) * STAGGER
    assert "run_id mod 30" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# the reader the sweep and the console use                                     #
# --------------------------------------------------------------------------- #


def test_held_is_true_only_for_a_newest_held_before_its_until():
    body = _held_body("DRE-5", T0)
    until = T0 + timedelta(minutes=H + W)
    node = _node("DRE-5", body, T0)
    assert pa.held([body], T0 + timedelta(minutes=1)) is True
    assert pa.held([node], until - timedelta(seconds=1)) is True
    assert pa.held([node], until) is False
    assert pa.held([node], until + timedelta(minutes=1)) is False
    started = (f"🪫 planner-hold: started · card DRE-5 · run 9 · repo {REPO} · "
               f"remaining 4000 of 5000 · because allowance-ok · "
               f"at {_iso(T0 + timedelta(minutes=5))}")
    later = _node("DRE-5", started, T0 + timedelta(minutes=5))
    assert pa.held([node, later], T0 + timedelta(minutes=6)) is False
    assert pa.held([body, started], T0 + timedelta(minutes=6)) is False
    assert pa.held([], T0) is False
    assert pa.held(["a plain comment", {"body": "⏸️ something else"}], T0) is False


def test_a_late_held_keeps_the_until_of_the_read_that_began_the_hold():
    body = _held_body("DRE-5", T0)
    late = _node("DRE-5", body, T0 + timedelta(minutes=10))
    assert pa.held([late], T0 + timedelta(minutes=H + W) + timedelta(seconds=1)) is False


def test_parse_receipt_reads_both_states():
    r = pa.parse_receipt(_held_body("DRE-5", T0))
    assert (r.state, r.card, r.run, r.repo, r.remaining, r.limit) == (
        "held", "DRE-5", "9", REPO, "312", "5000")
    assert r.until == _iso(T0 + timedelta(minutes=H + W)) and r.at == _iso(T0)
    s = pa.parse_receipt(f"🪫 planner-hold: started · card DRE-5 · run 9 · repo {REPO} · "
                         f"remaining unknown of unknown · because hold-max · at {_iso(T0)}")
    assert (s.state, s.because, s.remaining) == ("started", "hold-max", "unknown")
    assert pa.parse_receipt("quoting 🪫 planner-hold: held · card DRE-5") is None
    assert pa.RECEIPT_MARK == "🪫 planner-hold:"
    assert pa.TAG == "planner-allowance:"


# --------------------------------------------------------------------------- #
# linear_ops: the reading, and the one lift of the stop                       #
# --------------------------------------------------------------------------- #


def test_rate_reading_returns_the_last_response_headers(wire):
    wire(_read(4321, 5000))
    linear_ops.gql(pa.READ_QUERY)
    reading = linear_ops.rate_reading()
    assert reading["remaining"] == 4321 and reading["limit"] == 5000
    assert reading["reset_ms"] == RESET_MS


def test_lift_lets_the_next_gql_send_and_keeps_the_first_refusal_on_the_line(wire, capsys):
    t = wire(_refused(0), _read(4000))
    with pytest.raises(linear_ops.LinearRateLimited):
        linear_ops.gql(pa.READ_QUERY)
    with pytest.raises(linear_ops.LinearRateLimited, match="no request sent"):
        linear_ops.gql(pa.READ_QUERY)
    assert t.calls == 1
    before = linear_ops.requests_made()
    assert linear_ops.lift_rate_limit_stop("a test") is True
    assert linear_ops.requests_made() == before == 1
    assert "linear-stop: lifted after 1 calls (a test)" in capsys.readouterr().err
    linear_ops.gql(pa.READ_QUERY)
    assert t.calls == 2 and linear_ops.requests_made() == 2
    line = linear_ops.budget_line()
    assert "refused after 1 calls; stop lifted 1×" in line


def test_a_budget_line_with_no_lift_says_nothing_of_one(wire):
    wire(_refused(0))
    with pytest.raises(linear_ops.LinearRateLimited):
        linear_ops.gql(pa.READ_QUERY)
    assert linear_ops.lift_rate_limit_stop is not None
    line = linear_ops.budget_line()
    assert "refused after 1 calls" in line and "lifted" not in line


def _callers(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if name == "lift_rate_limit_stop":
                return True
    return False


def test_planner_allowance_is_the_only_caller_of_the_lift():
    tracked = subprocess.run(["git", "ls-files", "scripts"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.split()
    py = [p for p in tracked if p.endswith(".py")]
    assert "scripts/linear_ops.py" in py
    calling = [p for p in py if _callers((ROOT / p).read_text(encoding="utf-8"))]
    # The detection is live: the one sanctioned caller is found.
    assert "scripts/planner_allowance.py" in calling
    others = [p for p in calling if p != "scripts/planner_allowance.py"]
    assert not others, f"lift_rate_limit_stop is called outside planner_allowance: {others}"


# --------------------------------------------------------------------------- #
# the two seams that fake a reading                                            #
# --------------------------------------------------------------------------- #


def test_the_fake_reading_answers_with_no_request_sent(wire, monkeypatch):
    t = wire(_read(10))
    monkeypatch.setenv(pa.FAKE_ENV, json.dumps([{"remaining": 4200, "limit": 5000}]))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("LINEAR_API_KEY")
    out, clock = _wait()
    assert out == {"start": "true", "because": "allowance-ok"}
    assert t.calls == 0 and linear_ops.requests_made() == 0
    assert clock.slept == []


def test_the_fixture_reads_through_gql_and_lowers_only_the_first_reading(wire, capsys):
    t = wire(_read(4200, 2500), _post(), _read(4100, 2500), _line(), _post())
    out, _ = _wait(repo=FIXTURE_REPO, title="DRE-1 an ALLOWANCE FIXTURE card")
    assert out == {"start": "true", "because": "allowance-ok"}
    assert t.sent[0]["query"] == pa.READ_QUERY  # the first read was really sent
    fixture = CFG["fixture_low_reading"]["remaining"]
    held = t.receipts()[0]
    assert f"remaining {fixture} of 2500" in held
    lines = _lines(capsys)
    _assert_grammar(lines)
    assert f"remaining {fixture} of 2500" in lines[0]
    assert "remaining 4100 of 2500" in lines[1]


def test_the_fixture_title_in_any_other_repo_is_read_for_real(wire, capsys):
    t = wire(_read(4200))
    out, _ = _wait(repo=REPO, title="DRE-1 an ALLOWANCE FIXTURE card")
    assert out == {"start": "true", "because": "allowance-ok"}
    assert t.calls == 1 and t.receipts() == []
    assert "remaining 4200 of 5000" in _lines(capsys)[0]


# --------------------------------------------------------------------------- #
# check                                                                        #
# --------------------------------------------------------------------------- #


def test_check_passes_the_shipped_file(monkeypatch):
    monkeypatch.delenv(pa.CONFIG_ENV, raising=False)
    assert pa.main(["check"]) == 0


def _shipped() -> dict:
    return json.loads((ROOT / "config" / "planner-allowance.json").read_text())


def test_the_shipped_file_carries_exactly_the_contract():
    assert _shipped() == {
        "threshold_remaining": 1000, "wait_minutes": 5, "hold_max_minutes": 60,
        "stagger_seconds": 10,
        "fixture_low_reading": {"repo": "dreadnought-foundry/agent-bureau-demo",
                                "title_marker": "ALLOWANCE FIXTURE", "remaining": 312}}


@pytest.mark.parametrize("edit,named", [
    (lambda d: d.pop("stagger_seconds"), "stagger_seconds"),
    (lambda d: d.update(threshold_remaining="1000"), "threshold_remaining"),
    (lambda d: d.update(hold_max_minutes=60.5), "hold_max_minutes"),
    (lambda d: d.update(wait_minutes=0), "wait_minutes"),
    (lambda d: d.update(hold_max_minutes=115), "hold_max_minutes"),
    (lambda d: d["fixture_low_reading"].pop("title_marker"), "title_marker"),
])
def test_check_fails_naming_the_key(monkeypatch, tmp_path, capsys, edit, named):
    doc = _shipped()
    edit(doc)
    path = tmp_path / "planner-allowance.json"
    path.write_text(json.dumps(doc))
    monkeypatch.setenv(pa.CONFIG_ENV, str(path))
    assert pa.main(["check"]) == 1
    assert named in capsys.readouterr().err
    with pytest.raises(pa.PlannerAllowanceError, match=named):
        pa.load()


def test_check_fails_for_a_missing_or_malformed_file(monkeypatch, tmp_path):
    monkeypatch.setenv(pa.CONFIG_ENV, str(tmp_path / "nope.json"))
    assert pa.main(["check"]) == 1
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    monkeypatch.setenv(pa.CONFIG_ENV, str(bad))
    assert pa.main(["check"]) == 1


def test_the_hold_bound_sits_below_the_planning_stall_window():
    import lane_contract
    assert H + W < lane_contract.stale_minutes()["Planning"]


# --------------------------------------------------------------------------- #
# the log line, the proof's reach, the decision note                           #
# --------------------------------------------------------------------------- #


def test_proof_reach_declares_the_tag():
    doc = json.loads((ROOT / "config" / "proof-reach.json").read_text())
    rows = [e for e in doc["log_lines"] if e["tag"] == pa.TAG]
    assert rows == [{"tag": "planner-allowance:", "printed_by": "scripts/planner_allowance.py",
                     "constant": "TAG", "when": rows[0]["when"]}]
    assert proof_reach.check(ROOT) == []


def test_the_decision_note_records_why_the_cap_went():
    adr = (ROOT / "architecture" / "decisions" / "adr-planner-cap-removed.md").read_text()
    assert "remove the max number of planners" in adr
    assert "smart enough to know when we're basically doing too much planning" in adr
    assert "No limit, slow down near empty" in adr
    assert "planner_allowance.py" in adr and "dedupe_dispatch.py plan-gate" in adr
    assert "Planning order" in adr
    assert not re.search(r"\bSid\b", adr)
    readme = (ROOT / "config" / "README.md").read_text()
    assert "**`planner-allowance.json`**" in readme
