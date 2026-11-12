"""The read door's client asks once more before it falls back (DRE-9250).

Over 14 hours of bureau-pipeline's run logs, most `read-fallback:` lines were
an answer a few seconds behind (`older-than-needed`, `stale`) or one token
refused (`refused`). Each of those was one answer and then Linear. Now each is
asked once more first:

* `older-than-needed` — `linear_ops.card_via_door` waits the gap to the floor
  plus `ASK_AGAIN_SLACK_SECONDS`, capped at `ASK_AGAIN_MAX_WAIT_SECONDS`;
* `stale` — `board`, `cards`, `dependents` and `workflow_states` wait the
  overage plus the slack (the slack alone for the door's own UNKNOWN);
* `refused` — a 401 that is not a `scope=fleet` refusal is asked again at
  once, and the line carries the first answer's own words after `first=`.

At most one second ask per read and `ASK_AGAIN_LIMIT` per process; never on
`linear-hold`; never in mode `off` or `shadow`. A second answer that still
fails falls back exactly as today, with one `read-fallback:` line.

Nothing here reaches Linear or the console: the door and its token issuer are
`tests/bureau_read_fakes.py` on 127.0.0.1, Linear is the urlopen stand-in of
`tests/test_linear_ops_card_via_door.py`, and the wait is recorded, never slept.
"""
from __future__ import annotations

import sys
import urllib.request
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402
from test_linear_ops_card_via_door import (  # noqa: E402
    DOOR_ENVS, THREADS, LinearStub, door_card)

FLOOR = "2026-11-06T18:00:00Z"
LINE = "read-door: asked again "

WORLD = {
    "DRE-1": door_card("DRE-1"),
    "DRE-2": card("DRE-2", "Todo"),
    "DRE-3": card("DRE-3", "Backlog", blockers=(("DRE-1", "In Progress"),)),
}


def _iso(seconds_after_floor: float) -> str:
    at = linear_ops._instant(FLOOR) + timedelta(seconds=seconds_after_floor)
    return at.isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def waits(monkeypatch):
    """Every wait the client makes, in seconds — recorded, never slept."""
    for name in DOOR_ENVS:
        monkeypatch.delenv(name, raising=False)
    waited: list = []
    monkeypatch.setattr(bureau_read, "_wait", waited.append, raising=False)
    return waited


@pytest.fixture
def linear(monkeypatch):
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


@pytest.fixture
def issuer():
    with FakeIssuer() as running:
        yield running


def _door(monkeypatch, issuer, *, mode: str = "on", card_max_age: str | None = None):
    door = FakeDoor(WORLD)
    door.__enter__()
    for key, value in door_env(door_url=door.url, issuer=issuer, mode=mode).items():
        monkeypatch.setenv(key, value)
    if card_max_age is not None:
        monkeypatch.setenv(linear_ops.CARD_MAX_AGE_ENV, card_max_age)
    return door


@pytest.fixture
def door(monkeypatch, issuer):
    running = _door(monkeypatch, issuer)
    yield running
    running.__exit__(None, None, None)


@pytest.fixture
def card_door(monkeypatch, issuer, linear):
    """A door `linear_ops` asks: the step names a card max-age and a floor."""
    running = _door(monkeypatch, issuer, card_max_age="60")
    monkeypatch.setenv(linear_ops.CARD_SEEN_AFTER_ENV, FLOOR)
    yield running
    running.__exit__(None, None, None)


def answers(door: FakeDoor, key: str, *firsts) -> None:
    """`key` answers each of `firsts` in turn, then as the door would. A
    first that is callable is handed the door's own `(status, body)`."""
    queue = list(firsts)

    def answer(endpoint, query, _headers):
        route = door.routes.pop(key)
        try:
            normal = door.answer(endpoint, query)
        finally:
            door.routes[key] = route
        if not queue:
            return normal
        first = queue.pop(0)
        return first(*normal) if callable(first) else first

    door.routes[key] = answer


def aged(age: float):
    """A FRESH answer `age` seconds old."""
    def older(status, body):
        body["freshness"]["age_seconds"] = age
        return status, body
    return older


def as_of(at: str):
    """A FRESH answer as of `at`."""
    def stamped(status, body):
        body["freshness"]["as_of"] = at
        return status, body
    return stamped


def described(text: str):
    """A FRESH /cards answer whose card carries `text` as its description."""
    def marked(status, body):
        for node in body["issues"]["nodes"]:
            node["description"] = text
        return status, body
    return marked


def lines(err: str, tag: str) -> list[str]:
    return [ln for ln in err.splitlines() if ln.startswith(tag)]


# ── the contract's names ────────────────────────────────────────────────────


def test_the_constants_live_in_bureau_read():
    assert bureau_read.ASK_AGAIN_SLACK_SECONDS == 2
    assert bureau_read.ASK_AGAIN_MAX_WAIT_SECONDS == 10
    assert bureau_read.ASK_AGAIN_LIMIT == 3
    for name in ("ASK_AGAIN_SLACK_SECONDS", "ASK_AGAIN_MAX_WAIT_SECONDS", "ASK_AGAIN_LIMIT"):
        assert name not in vars(linear_ops), f"linear_ops restates {name}"


# ── older-than-needed: `card_via_door` ──────────────────────────────────────


def _two_card_answers(door, first_at: str, second_at: str):
    answers(door, "/cards",
            lambda s, b: as_of(first_at)(*described("first")(s, b)),
            lambda s, b: as_of(second_at)(*described("second")(s, b)))


def test_an_answer_3s_older_than_the_floor_is_asked_again_and_served(card_door, waits,
                                                                    capsys):
    _two_card_answers(card_door, _iso(-3), _iso(1))
    node, read = linear_ops.card_via_door("DRE-1")
    assert node["description"] == "second"
    assert read.as_of == _iso(1)
    assert waits == [5]
    assert len(card_door.asked("/cards")) == 2
    err = capsys.readouterr().err
    assert lines(err, LINE) == [
        "read-door: asked again read=cards reason=older-than-needed trailing 3s after 5s"
        " — served"]
    assert lines(err, bureau_read.FALLBACK_TAG) == []
    assert "served 1 " in bureau_read.exit_line()


def test_a_second_answer_still_older_falls_back_once(card_door, linear, waits, capsys):
    _two_card_answers(card_door, _iso(-3), _iso(-1))
    assert linear_ops.card_via_door("DRE-1") is None
    err = capsys.readouterr().err
    assert lines(err, LINE) == [
        "read-door: asked again read=cards reason=older-than-needed trailing 3s after 5s"
        " — fell back"]
    (fallback,) = lines(err, bureau_read.FALLBACK_TAG)
    assert fallback.endswith(" read=cards reason=older-than-needed")
    assert bureau_read.fallbacks()[0][1:] == ("cards", "older-than-needed")


def test_the_caller_then_reads_linear_as_today(card_door, linear, waits, capsys):
    _two_card_answers(card_door, _iso(-3), _iso(-1))
    assert linear_ops.comment_bodies("DRE-1") == [c["body"] for c in THREADS["DRE-1"]]
    assert linear.thread_reads() == [linear_ops._THREAD_QUERY]
    assert len(card_door.asked("/cards")) == 2
    assert len(lines(capsys.readouterr().err, bureau_read.FALLBACK_TAG)) == 1


def test_a_second_answer_that_is_unknown_falls_back_once_as_older(card_door, waits,
                                                                  capsys):
    answers(card_door, "/cards", as_of(_iso(-3)), card_door.unknown("reread-pending"))
    assert linear_ops.card_via_door("DRE-1") is None
    err = capsys.readouterr().err
    assert lines(err, LINE)[0].endswith(" — fell back")
    (fallback,) = lines(err, bureau_read.FALLBACK_TAG)
    assert fallback.endswith(" read=cards reason=older-than-needed")


def test_the_wait_is_the_gap_plus_the_slack_capped(card_door, waits, capsys):
    _two_card_answers(card_door, _iso(-30), _iso(1))
    assert linear_ops.card_via_door("DRE-1") is not None
    assert waits == [10]
    assert lines(capsys.readouterr().err, LINE) == [
        "read-door: asked again read=cards reason=older-than-needed trailing 30s after 10s"
        " — served"]


def test_an_as_of_that_cannot_be_read_is_not_asked_again(card_door, waits, capsys):
    answers(card_door, "/cards", as_of("yesterday"))
    assert linear_ops.card_via_door("DRE-1") is None
    assert waits == [] and len(card_door.asked("/cards")) == 1
    assert lines(capsys.readouterr().err, LINE) == []


# ── stale: the four reads with a Linear copy ────────────────────────────────

READS = {
    "board": ("/board", lambda: bureau_read.board(["Todo"], max_age=120)),
    "cards": ("/cards", lambda: bureau_read.cards(["DRE-2"], max_age=120)),
    "dependents": ("dependents", lambda: bureau_read.dependents("DRE-1", max_age=120)),
    "workflow-states": ("/workflow-states",
                        lambda: bureau_read.workflow_states(max_age=120)),
}
STALE = {
    # the door's own UNKNOWN carries no age: the wait is the slack alone
    "door-unknown": (lambda door: door.unknown("stale"), "trailing 0s after 2s", [2]),
    # a FRESH answer 3 s past the reader's 120 s
    "past-max-age": (lambda door: aged(123), "trailing 3s after 5s", [5]),
}


def _whole(door, read, key):
    """The read as the door answers it with nothing stale in the way."""
    route = door.routes.pop(key)
    try:
        return READS[read][1]().nodes
    finally:
        door.routes[key] = route


@pytest.mark.parametrize("stale", sorted(STALE))
@pytest.mark.parametrize("read", sorted(READS))
def test_a_stale_answer_is_asked_again_and_served_whole(read, stale, door, waits, capsys):
    key, call = READS[read]
    first, said, waited = STALE[stale]
    answers(door, key, first(door))
    got = call()
    assert got.nodes and got.nodes == _whole(door, read, key)
    assert waits == waited
    err = capsys.readouterr().err
    assert lines(err, LINE) == [
        f"read-door: asked again read={read} reason=stale {said} — served"]
    assert lines(err, bureau_read.FALLBACK_TAG) == []


@pytest.mark.parametrize("stale", sorted(STALE))
@pytest.mark.parametrize("read", sorted(READS))
def test_a_second_stale_answer_falls_back_once(read, stale, door, waits, capsys):
    key, call = READS[read]
    first, said, _waited = STALE[stale]
    answers(door, key, first(door), first(door))
    with pytest.raises(bureau_read.ReadUnknown) as caught:
        call()
    assert caught.value.reason == "stale" and not caught.value.skip
    asked = [r for r in door.requests if r["path"].endswith(key.lstrip("/"))]
    assert len(asked) == 2
    err = capsys.readouterr().err
    assert lines(err, LINE) == [
        f"read-door: asked again read={read} reason=stale {said} — fell back"]
    (fallback,) = lines(err, bureau_read.FALLBACK_TAG)
    assert fallback.endswith(f" read={read} reason=stale")
    assert bureau_read.enabled()


def test_a_stale_read_counts_once_on_the_exit_line(door, waits):
    answers(door, "/board", door.unknown("stale"), door.unknown("stale"))
    with pytest.raises(bureau_read.ReadUnknown):
        bureau_read.board(["Todo"], max_age=120)
    assert bureau_read.exit_line().startswith("read-door: served 0 unknown 1 unavailable 0 ")


def test_search_is_not_asked_again(door, waits, capsys):
    door.routes["/search"] = door.unknown("stale")
    with pytest.raises(bureau_read.ReadUnknown):
        bureau_read.search("anything")
    assert len(door.asked("/search")) == 1 and waits == []
    assert lines(capsys.readouterr().err, LINE) == []


# ── refused: a 401 that is not a fleet refusal ──────────────────────────────

#: Longer than 80 characters, with a line break, a tab and a `"` inside them.
REFUSAL = (b'{"error": "This is not a token\nthe read door accepts.",\t"why": "aud"'
           + b" and then a good deal more that the line never carries" * 2)


def _first_words() -> str:
    text = REFUSAL.decode()[:80]
    for ch in '\n\t"':
        text = text.replace(ch, " ")
    return text


def test_the_first_words_are_80_characters_on_one_line():
    words = _first_words()
    assert len(words) == 80 and len(REFUSAL) > 80
    assert "\n" not in words and '"' not in words and "\t" not in words


def test_a_401_is_asked_again_at_once_and_the_door_stays_in_use(door, issuer, waits,
                                                                capsys):
    answers(door, "/board", (401, REFUSAL))
    got = bureau_read.board(["Todo"], max_age=120)
    assert [n["identifier"] for n in got.nodes] == ["DRE-2"]
    assert waits == []
    asked = door.asked("/board")
    assert len(asked) == 2
    assert asked[0]["headers"]["authorization"] != asked[1]["headers"]["authorization"]
    assert len(issuer.requests) == 2  # a fresh token for the second ask
    err = capsys.readouterr().err
    assert lines(err, LINE) == [
        "read-door: asked again read=board reason=refused trailing 0s after 0s — served"
        f' first="HTTP 401 {_first_words()}"']
    assert lines(err, bureau_read.FALLBACK_TAG) == []
    assert bureau_read.enabled()
    assert bureau_read.cards(["DRE-1"], max_age=120).nodes  # the door is still asked
    assert "read-door: unavailable" not in err


def test_a_second_401_stops_the_door_as_a_first_one_does_today(door, waits, capsys):
    answers(door, "/cards", (401, REFUSAL), (401, b"refused again"))
    with pytest.raises(bureau_read.ReadUnknown) as caught:
        bureau_read.cards(["DRE-1"], max_age=120)
    assert caught.value.reason == "refused" and caught.value.unavailable
    assert caught.value.status == 401
    assert bureau_read.disabled_reason() == "refused"
    with pytest.raises(bureau_read.ReadUnknown):
        bureau_read.board(["Todo"], max_age=120)
    assert len(door.requests) == 2
    err = capsys.readouterr().err
    (said,) = lines(err, LINE)
    assert said == ("read-door: asked again read=cards reason=refused trailing 0s after 0s"
                    f' — fell back first="HTTP 401 {_first_words()}"')
    assert "read-door: unavailable (refused: HTTP 401 refused again)" in err
    assert [f.split(" read=")[1] for f in lines(err, bureau_read.FALLBACK_TAG)] == [
        "cards reason=refused", "board reason=unavailable"]
    assert "unavailable 1" in bureau_read.exit_line()


@pytest.mark.parametrize("read", sorted(READS))
def test_each_read_asks_a_401_again(read, door, capsys):
    key, call = READS[read]
    answers(door, key, (401, b"no"))
    assert call().nodes
    (said,) = lines(capsys.readouterr().err, LINE)
    assert said.startswith(f"read-door: asked again read={read} reason=refused ")
    assert said.endswith(' — served first="HTTP 401 no"')


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_fleet_refusal_is_as_today(status, door, capsys):
    door.decline_fleet(status=status)
    with pytest.raises(bureau_read.ReadUnknown) as caught:
        bureau_read.board(["Todo"], scope="fleet", max_age=120)
    assert caught.value.reason == ("not-found" if status == 404 else "refused")
    assert len(door.requests) == 1 and bureau_read.enabled()
    assert lines(capsys.readouterr().err, LINE) == []


def test_a_403_is_as_today(door, capsys):
    door.routes["/board"] = (403, {"error": {"code": "FORBIDDEN"}})
    with pytest.raises(bureau_read.ReadUnknown):
        bureau_read.board(["Todo"], max_age=120)
    assert len(door.requests) == 1 and not bureau_read.enabled()
    assert lines(capsys.readouterr().err, LINE) == []


def test_a_404_on_cards_is_as_today(door, capsys):
    with pytest.raises(bureau_read.ReadUnknown) as caught:
        bureau_read.cards(["DRE-999"], max_age=120)
    assert caught.value.reason == "not-found"
    assert len(door.requests) == 1
    assert lines(capsys.readouterr().err, LINE) == []


def test_a_401_on_a_read_with_no_linear_copy_is_as_today(door, capsys):
    door.routes["/split-history"] = (401, {"error": {"code": "X"}})
    with pytest.raises(bureau_read.ReadUnknown):
        bureau_read.split_history("2026-07-04T20:00:00Z")
    assert len(door.requests) == 1 and not bureau_read.enabled()
    assert lines(capsys.readouterr().err, LINE) == []


def test_only_a_refused_line_carries_first(door, waits, capsys):
    answers(door, "/board", door.unknown("stale"))
    bureau_read.board(["Todo"], max_age=120)
    (said,) = lines(capsys.readouterr().err, LINE)
    assert "first=" not in said


# ── the bounds ──────────────────────────────────────────────────────────────


def test_one_second_ask_per_read(door, waits, capsys):
    answers(door, "/board", (401, b"no"), door.unknown("stale"))
    with pytest.raises(bureau_read.ReadUnknown) as caught:
        bureau_read.board(["Todo"], max_age=120)
    assert caught.value.reason == "stale"
    assert len(door.asked("/board")) == 2 and waits == []
    err = capsys.readouterr().err
    assert len(lines(err, LINE)) == 1
    assert len(lines(err, bureau_read.FALLBACK_TAG)) == 1


def test_the_fourth_case_in_a_process_falls_back_at_once(door, waits, capsys):
    door.routes["/board"] = door.unknown("stale")
    for _ in range(4):
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.board(["Todo"], max_age=120)
    assert len(door.asked("/board")) == 3 * 2 + 1
    assert waits == [2, 2, 2]
    err = capsys.readouterr().err
    assert len(lines(err, LINE)) == 3
    assert len(lines(err, bureau_read.FALLBACK_TAG)) == 4


def test_the_limit_is_shared_by_the_three_reasons(card_door, waits, capsys):
    answers(card_door, "/board", (401, b"no"))
    bureau_read.board(["Todo"], max_age=120)
    answers(card_door, "/workflow-states", card_door.unknown("stale"))
    bureau_read.workflow_states(max_age=120)
    _two_card_answers(card_door, _iso(-3), _iso(1))
    assert linear_ops.card_via_door("DRE-1") is not None
    answers(card_door, "/cards", as_of(_iso(-3)))
    assert linear_ops.card_via_door("DRE-1") is None
    assert len(lines(capsys.readouterr().err, LINE)) == 3
    assert waits == [2, 5]


def test_a_linear_hold_is_never_asked_again(door, waits, capsys):
    door.routes["/board"] = door.unknown(bureau_read.LINEAR_HOLD)
    with pytest.raises(bureau_read.ReadUnknown) as caught:
        bureau_read.board(["Todo"], max_age=120)
    assert caught.value.skip
    assert len(door.requests) == 1 and waits == []
    assert lines(capsys.readouterr().err, LINE) == []


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_and_shadow_make_no_second_ask(mode, monkeypatch, issuer, waits, capsys):
    door = _door(monkeypatch, issuer, mode=mode)
    try:
        door.routes["/board"] = door.unknown("stale")
        door.routes["/cards"] = (401, b"no")
        for call in (lambda: bureau_read.board(["Todo"], max_age=120),
                     lambda: bureau_read.cards(["DRE-1"], max_age=120)):
            with pytest.raises(bureau_read.ReadUnknown):
                call()
        assert len(door.requests) == (0 if mode == "off" else 2)
    finally:
        door.__exit__(None, None, None)
    assert waits == []
    assert lines(capsys.readouterr().err, LINE) == []


# ── the doc ─────────────────────────────────────────────────────────────────


def test_the_doc_describes_the_second_ask():
    doc = (ROOT / "docs" / "stage2-read-door-linear-ops.md").read_text()
    for needle in ("read-door: asked again read=<read> reason=<reason> trailing <s>s "
                   "after <w>s", 'first="HTTP 401 <body>"', "ASK_AGAIN_SLACK_SECONDS",
                   "ASK_AGAIN_MAX_WAIT_SECONDS", "ASK_AGAIN_LIMIT", "older-than-needed",
                   "plain retry"):
        assert needle in doc, needle
