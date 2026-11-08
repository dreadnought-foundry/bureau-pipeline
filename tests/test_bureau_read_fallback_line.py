"""The read door's client names every read that falls back to Linear (DRE-8794).

Before this card a caller that caught `ReadUnknown` printed its own free-form
`read-door:` sentence, on stdout or stderr, and nothing could count who fell
back or why. The client now prints ONE fixed line per fallback, itself, and
keeps them in order for the sweep's alert to read:

    read-fallback: reader="<workflow> / <job> / <script>" read=<read> reason=<reason>

What these pin, each against the failure it exists to stop:

* each of the five reads with a Linear copy behind it prints exactly one line
  per fallback, in mode `on` only;
* a skip (`linear-hold`, a 429 while the key is held) is not a fallback;
* the four reads with no Linear copy never print it;
* a `pull_request` run says `pull-request`, a read after the door stopped says
  `unavailable`;
* `fallbacks()` holds one tuple per line, and the reader names the run.

No test here reaches the console, GitHub or Linear: the door and the issuer
are `tests/bureau_read_fakes.py`, on 127.0.0.1.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bureau_read  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

LINE = re.compile(
    r'^read-fallback: reader="(?P<reader>[^"]+)" read=(?P<read>\S+) reason=(?P<reason>\S+)$')
SCRIPT = Path(sys.argv[0]).name

WORLD = {
    "DRE-1": card("DRE-1", "Todo"),
    "DRE-2": card("DRE-2", "In Progress", labels=("repo:portico",)),
}


@pytest.fixture(autouse=True)
def _fresh_client(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY",
                 "GITHUB_EVENT_NAME", "GITHUB_WORKFLOW", "GITHUB_JOB"):
        monkeypatch.delenv(name, raising=False)
    bureau_read.reset_for_tests()
    yield
    bureau_read.reset_for_tests()


def _point(monkeypatch, door, issuer, **kw):
    for key, value in door_env(door_url=door.url, issuer=issuer, **kw).items():
        monkeypatch.setenv(key, value)


def _lines(err: str) -> list[dict]:
    """Every `read-fallback:` line on stderr, each held to the contract's shape."""
    out = []
    for raw in err.splitlines():
        if raw.startswith(bureau_read.FALLBACK_TAG):
            match = LINE.match(raw)
            assert match, f"not the contract's shape: {raw!r}"
            out.append(match.groupdict())
    return out


def _unknown(call):
    with pytest.raises(bureau_read.ReadUnknown) as caught:
        call()
    return caught.value


# ── the five reads with a Linear copy behind them ───────────────────────────
# Each scenario: (read name, how the door fails, the call, the reason).


def _board_stale(door):
    door.routes["/board"] = door.unknown("stale")
    return lambda: bureau_read.board(["Todo"], max_age=120)


def _cards_not_found(door):
    return lambda: bureau_read.cards(["DRE-404"], max_age=60)


def _dependents_relations_stale(door):
    door.routes["dependents"] = door.unknown("relations-stale")
    return lambda: bureau_read.dependents("DRE-1", max_age=120)


def _workflow_states_closed(door):
    door.routes["/workflow-states"] = (503, {"error": {"code": "CLOSED"}})
    return lambda: bureau_read.workflow_states(max_age=3600)


def _search_refused(door):
    door.routes["/search"] = (403, {"error": {"code": "FORBIDDEN"}})
    return lambda: bureau_read.search("handover", scope="fleet")


FIVE = [
    ("board", _board_stale, "stale"),
    ("cards", _cards_not_found, "not-found"),
    ("dependents", _dependents_relations_stale, "relations-stale"),
    ("workflow-states", _workflow_states_closed, "closed"),
    ("search", _search_refused, "refused"),
]


@pytest.mark.parametrize("read,arrange,reason", FIVE, ids=[f[0] for f in FIVE])
def test_each_read_prints_exactly_one_line_per_fallback(monkeypatch, capsys, read,
                                                        arrange, reason):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer)
        call = arrange(door)
        caught = _unknown(call)
    assert caught.reason == reason and not caught.skip
    lines = _lines(capsys.readouterr().err)
    assert lines == [{"reader": f"local / - / {SCRIPT}", "read": read, "reason": reason}]
    assert bureau_read.fallbacks() == [(f"local / - / {SCRIPT}", read, reason)]


def test_the_line_is_on_stderr_never_stdout(monkeypatch, capsys):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer)
        _unknown(_board_stale(door))
    out = capsys.readouterr()
    assert bureau_read.FALLBACK_TAG not in out.out
    assert len(_lines(out.err)) == 1


def test_the_tag_is_the_contracts():
    assert bureau_read.FALLBACK_TAG == "read-fallback:"


def test_a_served_read_prints_no_line(monkeypatch, capsys):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer)
        bureau_read.board(["Todo"], max_age=120)
        bureau_read.cards(["DRE-1"], max_age=60)
        bureau_read.workflow_states(max_age=3600)
    assert _lines(capsys.readouterr().err) == []
    assert bureau_read.fallbacks() == []


# ── a skip is not a fallback ────────────────────────────────────────────────


def test_linear_hold_prints_no_line(monkeypatch, capsys):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = door.unknown(bureau_read.LINEAR_HOLD)
        _point(monkeypatch, door, issuer)
        caught = _unknown(lambda: bureau_read.board(["Todo"], max_age=120))
    assert caught.skip
    assert _lines(capsys.readouterr().err) == []
    assert bureau_read.fallbacks() == []


def test_a_429_while_the_key_is_held_prints_no_line(monkeypatch, capsys):
    import linear_ops
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = (429, {"error": {"code": "THROTTLED"}})
        _point(monkeypatch, door, issuer)
        monkeypatch.setitem(linear_ops._budget, "refused_after", 3)
        caught = _unknown(lambda: bureau_read.board(["Todo"], max_age=120))
    assert caught.status == 429 and caught.skip
    assert _lines(capsys.readouterr().err) == []
    assert bureau_read.fallbacks() == []


def test_a_429_with_the_key_free_is_a_fallback(monkeypatch, capsys):
    import linear_ops
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = (429, {"error": {"code": "THROTTLED"}})
        _point(monkeypatch, door, issuer)
        monkeypatch.setitem(linear_ops._budget, "refused_after", None)
        caught = _unknown(lambda: bureau_read.board(["Todo"], max_age=120))
    assert not caught.skip
    assert [ln["reason"] for ln in _lines(capsys.readouterr().err)] == ["throttled"]


# ── off and shadow are never a fallback ─────────────────────────────────────


@pytest.mark.parametrize("mode", ["off", "shadow"])
@pytest.mark.parametrize("read,arrange,reason", FIVE, ids=[f[0] for f in FIVE])
def test_off_and_shadow_print_no_line(monkeypatch, capsys, mode, read, arrange, reason):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer, mode=mode)
        _unknown(arrange(door))
    assert _lines(capsys.readouterr().err) == []
    assert bureau_read.fallbacks() == []


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_note_fallback_itself_is_silent_off_and_in_shadow(monkeypatch, capsys, mode):
    monkeypatch.setenv("BUREAU_READ", mode)
    bureau_read.note_fallback("board", "stale")
    assert _lines(capsys.readouterr().err) == []
    assert bureau_read.fallbacks() == []


# ── the two reasons the line names itself ───────────────────────────────────


def test_a_pull_request_run_says_pull_request(monkeypatch, capsys):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        first = _unknown(lambda: bureau_read.board(["Todo"], max_age=120))
        second = _unknown(lambda: bureau_read.cards(["DRE-1"], max_age=60))
        assert door.requests == []
    assert first.reason == "event-refused"
    lines = _lines(capsys.readouterr().err)
    # Every read of a pull_request run is refused for the same reason: the
    # event. The second one finds the door stopped by it, and says so.
    assert [(ln["read"], ln["reason"]) for ln in lines] == [
        ("board", "pull-request"), ("cards", "pull-request")]
    assert second.reason == "unavailable"


def test_note_fallback_spells_event_refused_as_pull_request(monkeypatch, capsys):
    monkeypatch.setenv("BUREAU_READ", "on")
    bureau_read.note_fallback("search", "event-refused")
    assert [ln["reason"] for ln in _lines(capsys.readouterr().err)] == ["pull-request"]
    assert bureau_read.fallbacks()[0][2] == "pull-request"


def test_a_read_after_the_door_stopped_says_unavailable(monkeypatch, capsys):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = (503, {"error": {"code": "CLOSED"}})
        _point(monkeypatch, door, issuer)
        _unknown(lambda: bureau_read.board(["Todo"], max_age=120))
        later = _unknown(lambda: bureau_read.cards(["DRE-1"], max_age=60))
    assert later.reason == "unavailable"
    lines = _lines(capsys.readouterr().err)
    assert [(ln["read"], ln["reason"]) for ln in lines] == [
        ("board", "closed"), ("cards", "unavailable")]


def test_no_token_is_a_fallback_named_no_token(monkeypatch, capsys):
    with FakeDoor(WORLD) as door:
        _point(monkeypatch, door, None)
        _unknown(lambda: bureau_read.search("handover"))
    assert [(ln["read"], ln["reason"]) for ln in _lines(capsys.readouterr().err)] == [
        ("search", "no-token")]


# ── the reads with no Linear copy never print it ────────────────────────────


def test_the_reads_with_no_linear_copy_never_print_the_line(monkeypatch, capsys):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        for endpoint in ("/planning-order", "/split-history", "/groom-removals", "/alerts"):
            door.routes[endpoint] = (500, {"error": {"code": "BROKEN"}})
        _point(monkeypatch, door, issuer)
        _unknown(bureau_read.planning_order)
        _unknown(lambda: bureau_read.split_history("2026-10-01T00:00:00Z"))
        # The door stopped on the split history's 500: these two fail too.
        _unknown(bureau_read.groom_removals)
        _unknown(bureau_read.open_alerts)
    assert _lines(capsys.readouterr().err) == []
    assert bureau_read.fallbacks() == []


@pytest.mark.parametrize("name", ["planning_order", "split_history", "groom_removals",
                                  "open_alerts"])
def test_each_read_with_no_linear_copy_is_silent_on_its_own_failure(monkeypatch, capsys,
                                                                    name):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/" + {"planning_order": "planning-order",
                           "split_history": "split-history",
                           "groom_removals": "groom-removals",
                           "open_alerts": "alerts"}[name]] = (503, {"error": {}})
        _point(monkeypatch, door, issuer)
        args = ("2026-10-01T00:00:00Z",) if name == "split_history" else ()
        _unknown(lambda: getattr(bureau_read, name)(*args))
    assert _lines(capsys.readouterr().err) == []
    assert bureau_read.fallbacks() == []


# ── fallbacks() and the reader ──────────────────────────────────────────────


def test_fallbacks_holds_one_tuple_per_line_in_order(monkeypatch, capsys):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = door.unknown("stale")
        door.routes["/search"] = (404, {"error": {"code": "NOT_FOUND"}})
        _point(monkeypatch, door, issuer)
        _unknown(lambda: bureau_read.board(["Todo"], max_age=120))
        _unknown(lambda: bureau_read.cards(["DRE-404"], max_age=60))
        _unknown(lambda: bureau_read.search("handover", scope="fleet"))
        _unknown(lambda: bureau_read.board(["Todo"], max_age=120))
    lines = _lines(capsys.readouterr().err)
    noted = bureau_read.fallbacks()
    assert noted == [(ln["reader"], ln["read"], ln["reason"]) for ln in lines]
    assert [(r, why) for _, r, why in noted] == [
        ("board", "stale"), ("cards", "not-found"), ("search", "not-found"),
        ("board", "stale")]


def test_fallbacks_is_a_copy(monkeypatch):
    monkeypatch.setenv("BUREAU_READ", "on")
    bureau_read.note_fallback("board", "stale")
    bureau_read.fallbacks().clear()
    assert len(bureau_read.fallbacks()) == 1


def test_reset_for_tests_clears_the_fallbacks(monkeypatch):
    monkeypatch.setenv("BUREAU_READ", "on")
    bureau_read.note_fallback("board", "stale")
    bureau_read.reset_for_tests()
    assert bureau_read.fallbacks() == []


def test_the_reader_names_the_workflow_job_and_script(monkeypatch, capsys):
    monkeypatch.setenv("BUREAU_READ", "on")
    monkeypatch.setenv("GITHUB_WORKFLOW", "Reconcile Sweep")
    monkeypatch.setenv("GITHUB_JOB", "sweep")
    monkeypatch.setattr(sys, "argv", ["/home/runner/work/x/scripts/reconcile.py", "--full"])
    bureau_read.note_fallback("board", "timeout")
    err = capsys.readouterr().err
    assert ('read-fallback: reader="Reconcile Sweep / sweep / reconcile.py" '
            'read=board reason=timeout') in err.splitlines()
    assert bureau_read.fallbacks() == [
        ("Reconcile Sweep / sweep / reconcile.py", "board", "timeout")]


def test_the_reader_without_github_is_local_and_dash(monkeypatch, capsys):
    monkeypatch.setenv("BUREAU_READ", "on")
    monkeypatch.setattr(sys, "argv", ["scripts/groomer.py"])
    bureau_read.note_fallback("search", "unavailable")
    assert ('read-fallback: reader="local / - / groomer.py" read=search '
            'reason=unavailable') in capsys.readouterr().err.splitlines()


def test_a_real_process_prints_the_line_to_stderr(tmp_path):
    """The whole thing in a child process: what a workflow step's log shows."""
    probe = tmp_path / "sweep_probe.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
        "import bureau_read\n"
        "bureau_read.note_fallback('workflow-states', 'malformed')\n")
    env = {"PATH": "/usr/bin:/bin", "BUREAU_READ": "on",
           "GITHUB_WORKFLOW": "Bureau Reconcile", "GITHUB_JOB": "reconcile"}
    done = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True,
                          env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    assert bureau_read.FALLBACK_TAG not in done.stdout
    assert ('read-fallback: reader="Bureau Reconcile / reconcile / sweep_probe.py" '
            'read=workflow-states reason=malformed') in done.stderr.splitlines()


# ── the docstring and the proof's log lines ─────────────────────────────────


def test_the_docstring_names_the_line_where_the_door_is_not_used():
    doc = bureau_read.__doc__
    section = doc.split("## When the door is not used", 1)[1].split("\n## ", 1)[0]
    assert "read-fallback:" in section
    assert "linear-hold" in section or "skip" in section


def test_proof_reach_declares_the_three_lines():
    doc = json.loads((ROOT / "config" / "proof-reach.json").read_text())
    tags = {e["tag"]: e for e in doc["log_lines"]}
    assert (tags["read-fallback:"]["printed_by"], tags["read-fallback:"]["constant"]) == (
        "scripts/bureau_read.py", "FALLBACK_TAG")
    assert tags["read-door:"]["printed_by"] == "scripts/bureau_read.py"
    assert tags["linear-calls:"]["printed_by"] == "scripts/linear_ops.py"


def test_proof_reach_check_passes():
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "proof_reach.py"), "check"],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
