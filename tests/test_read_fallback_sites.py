"""The nine readers that skip a stopped read door name their fallback (DRE-8796).

DRE-8794 made the client print one `read-fallback:` line per read that falls
back to Linear. Nine readers ask `bureau_read.enabled()` before they ask the
door, and once the door has stopped answering for the run they go straight to
Linear without calling the client at all — so the client never sees those
fallbacks. Each of the nine now says so itself, through `note_fallback`:

    read-fallback: reader="<workflow> / <job> / <script>" read=<read> reason=<reason>

What these pin, each against the failure it exists to stop:

* each gate, in mode `on` with the door stopped by a first read that timed
  out, prints exactly one line naming its read and the stop reason, and its
  read then goes to Linear;
* in `off` and `shadow` the same gate prints none;
* a gate whose own door read fails is counted once, by the client — never a
  second line from the gate;
* a skip is not a fallback: the retiring lanes under a held Linear bucket
  raise with no line.

No test here reaches the console, GitHub or Linear: the door and the issuer
are `tests/bureau_read_fakes.py`, on 127.0.0.1, and every Linear read is a
stub that records it was asked.
"""
from __future__ import annotations

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

import agent_task_snapshot  # noqa: E402
import bureau_read  # noqa: E402
import groom_lookups  # noqa: E402
import linear_ops  # noqa: E402
import merge_sweep_gate  # noqa: E402
import reconcile  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

LINE = re.compile(
    r'^read-fallback: reader="(?P<reader>[^"]+)" read=(?P<read>\S+) reason=(?P<reason>\S+)$')
SCRIPT = Path(sys.argv[0]).name

WORLD = {
    "DRE-1": card("DRE-1", "In Progress", labels=("repo:portico",)),
    "DRE-9": card("DRE-9", "In Progress", labels=("repo:portico",), children=True),
}

#: The door's answer arrives after the client has given up on it.
STALL_SECONDS = 0.3
TIMEOUT_SECONDS = 0.1
ENDPOINTS = ("/board", "/cards", "dependents", "/workflow-states", "/search")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY",
                 "GITHUB_EVENT_NAME", "GITHUB_WORKFLOW", "GITHUB_JOB",
                 "AGENT_TASK_SNAPSHOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bureau_read, "TOTAL_TIMEOUT", TIMEOUT_SECONDS)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    monkeypatch.setattr(reconcile, "MAX_WIP", 5)
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()
    linear_ops.reset_workflow_states()
    yield
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()
    linear_ops.reset_workflow_states()


def _lines(err: str) -> list[dict]:
    """Every `read-fallback:` line on stderr, each held to the contract's shape."""
    out = []
    for raw in err.splitlines():
        if raw.startswith(bureau_read.FALLBACK_TAG):
            match = LINE.match(raw)
            assert match, f"not the contract's shape: {raw!r}"
            out.append(match.groupdict())
    return out


def _stall_every_read(door: FakeDoor) -> None:
    for endpoint in ENDPOINTS:
        door.routes[endpoint] = (200, door.envelope([]), STALL_SECONDS)


def _stop_the_door() -> None:
    """A first read that times out: the client stops asking the door for the
    rest of the run. In `off` it is never asked, and stays not asked."""
    with pytest.raises(bureau_read.ReadUnknown) as caught:
        bureau_read.board(["In Progress"], max_age=bureau_read.BOARD_MAX_AGE)
    if bureau_read.mode() == "off":
        assert caught.value.reason == "off"
    else:
        assert caught.value.reason == "timeout"
        assert bureau_read.disabled_reason() == "timeout"
    assert not bureau_read.enabled()


# ── the nine gates ──────────────────────────────────────────────────────────
# Each driver runs its gate and returns True when the read then went to Linear
# (a Linear stub was asked, or the gate handed its caller the Linear answer's
# signal). Linear itself is never reached.


def _snapshot(monkeypatch, tmp_path):
    asked = []

    def from_linear(identifier):
        asked.append(identifier)
        return {"schema": agent_task_snapshot.SCHEMA, "source": "linear",
                "taken_at": "2026-11-02T00:00:00Z", "identifier": identifier,
                "issue": {"identifier": identifier, "state": {"name": "In Progress"}},
                "comments": [], "comments_partial": False, "viewer": None,
                "workflowStates": None}

    monkeypatch.setattr(agent_task_snapshot, "_from_linear", from_linear)
    agent_task_snapshot.take("DRE-1", str(tmp_path / "snapshot.json"))
    return asked == ["DRE-1"]


def _groom_search(monkeypatch, tmp_path):
    return groom_lookups._door_search(["handover"]) is None


def _workflow_states(monkeypatch, tmp_path):
    return linear_ops._door_state_list() is None


def _merge_sweep(monkeypatch, tmp_path):
    return merge_sweep_gate.door_card("DRE-1") is None


def _linear_reads(monkeypatch):
    asked = []

    def read(lanes):
        asked.append(tuple(lanes))
        return []

    monkeypatch.setattr(reconcile, "_fetch_active_cards", read)
    monkeypatch.setattr(reconcile, "active_cards", read)
    return asked


def _fleet_or_linear(monkeypatch, tmp_path):
    asked = _linear_reads(monkeypatch)
    reconcile._fleet_or_linear(reconcile.DOOR_LINEAR_LANES, "Intake/Planning")
    return asked == [tuple(reconcile.DOOR_LINEAR_LANES)]


def _retiring(monkeypatch, tmp_path):
    asked = _linear_reads(monkeypatch)
    reconcile._door_retiring_cards()
    return asked == [tuple(reconcile.RETIRING_LANES)]


def _epic_threads(monkeypatch, tmp_path):
    reconcile._door_epic_threads(["DRE-9"])
    return linear_ops._cached_thread("DRE-9", reconcile._EPIC_THREAD_FIELDS) is None


def _unlanded(monkeypatch, tmp_path):
    return reconcile._unlanded_door_lanes(["DRE-1"]) == {}


def _sweep_idle(monkeypatch, tmp_path):
    asked = []

    def gql(query, variables=None):
        assert query == reconcile.IDLE_QUERY
        asked.append(variables)
        return {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}

    monkeypatch.setattr(linear_ops, "gql", gql)
    reconcile.sweep_idle()
    return len(asked) == 1


GATES = [
    ("agent_task_snapshot.take", "cards", _snapshot),
    ("groom_lookups._door_search", "search", _groom_search),
    ("linear_ops._door_state_list", "workflow-states", _workflow_states),
    ("merge_sweep_gate.door_card", "cards", _merge_sweep),
    ("reconcile._fleet_or_linear", "board", _fleet_or_linear),
    ("reconcile._door_retiring_cards", "board", _retiring),
    ("reconcile._door_epic_threads", "cards", _epic_threads),
    ("reconcile._unlanded_door_lanes", "cards", _unlanded),
    ("reconcile.sweep_idle", "board", _sweep_idle),
]
IDS = [g[0] for g in GATES]


def _point(monkeypatch, door, issuer, mode):
    for key, value in door_env(door_url=door.url, issuer=issuer, mode=mode).items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("gate,read,drive", GATES, ids=IDS)
def test_a_gate_that_skips_a_stopped_door_names_its_fallback(monkeypatch, capsys, tmp_path,
                                                             gate, read, drive):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _stall_every_read(door)
        _point(monkeypatch, door, issuer, "on")
        _stop_the_door()
        capsys.readouterr()  # the stopping read's own line is the client's
        before = len(door.requests)
        went_to_linear = drive(monkeypatch, tmp_path)
        assert len(door.requests) == before, f"{gate} asked a stopped door"
    assert went_to_linear, f"{gate} did not read Linear after skipping the door"
    lines = _lines(capsys.readouterr().err)
    assert lines == [{"reader": f"local / - / {SCRIPT}", "read": read,
                      "reason": "timeout"}], gate
    assert bureau_read.fallbacks()[-1] == (f"local / - / {SCRIPT}", read, "timeout")


@pytest.mark.parametrize("mode", ["off", "shadow"])
@pytest.mark.parametrize("gate,read,drive", GATES, ids=IDS)
def test_off_and_shadow_print_no_fallback_line(monkeypatch, capsys, tmp_path, mode,
                                              gate, read, drive):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _stall_every_read(door)
        _point(monkeypatch, door, issuer, mode)
        _stop_the_door()
        drive(monkeypatch, tmp_path)
    assert _lines(capsys.readouterr().err) == [], gate
    assert bureau_read.fallbacks() == []


@pytest.mark.parametrize("gate,read,drive", GATES, ids=IDS)
def test_a_read_the_client_already_noted_is_counted_once(monkeypatch, capsys, tmp_path,
                                                         gate, read, drive):
    """The door is in use when the gate reaches it, and its own read times
    out: the client names that fallback, and the gate adds nothing."""
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _stall_every_read(door)
        _point(monkeypatch, door, issuer, "on")
        assert bureau_read.enabled()
        drive(monkeypatch, tmp_path)
        assert door.requests, f"{gate} never asked the door"
    lines = _lines(capsys.readouterr().err)
    assert lines == [{"reader": f"local / - / {SCRIPT}", "read": read,
                      "reason": "timeout"}], gate


def test_the_retiring_lanes_under_a_held_bucket_are_a_skip_not_a_fallback(monkeypatch,
                                                                         capsys):
    """A pass that already knows Linear is held raises BoardHeld and reads
    nothing, so it falls back to nothing and prints no line."""
    asked = _linear_reads(monkeypatch)
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _stall_every_read(door)
        _point(monkeypatch, door, issuer, "on")
        _stop_the_door()
        capsys.readouterr()
        reconcile._door_hold.append(bureau_read.LINEAR_HOLD)
        with pytest.raises(reconcile.BoardHeld):
            reconcile._door_retiring_cards()
    assert asked == []
    assert _lines(capsys.readouterr().err) == []
