"""The Sweep's door reads state their max-age per decision and log each answer (DRE-8907).

Every board and cards read `scripts/reconcile.py` sends the read door used to
carry `bureau_read.BOARD_MAX_AGE`, whether its answer fed a dispatch or only an
alarm, and nothing in the log said what any one read was answered. Now:

* `reconcile.DOOR_MAX_AGES` holds one max-age per decision, and each of the
  seven Sweep reads sends its own row as `X-Bureau-Max-Age`;
* each door request prints one line, composed by `reconcile.door_read_line`:

      read-door: read decision=<d> verdict=<FRESH|UNKNOWN> age=<s|-> max-age=<s> reason=<r|->

* a read never sent to the door — mode `off` or `shadow`, or a door already
  stopped for the run — prints no such line, and its Linear read is today's.

The door and the issuer are `tests/bureau_read_fakes.py`, on 127.0.0.1; Linear
is a stub that records every request and answers with nothing.
"""
from __future__ import annotations

import ast
import contextlib
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

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

LINE = re.compile(
    r"^read-door: read decision=(?P<decision>\S+) verdict=(?P<verdict>FRESH|UNKNOWN) "
    r"age=(?P<age>\d+|-) max-age=(?P<max_age>\d+) reason=(?P<reason>\S+)$")

#: The twelve decisions and their seconds, exactly as the card states them.
TABLE = {
    "work-lanes": 120,
    "queue": 120,
    "idle-check": 120,
    "planning-intake": 300,
    "epic-threads": 300,
    "retiring-lanes": 900,
    "unlanded-lanes": 900,
    "parked-check": 60,
    "release-card": 60,
    "blocker-card": 120,
    "epic-dependents": 120,
    "busy-threads": 120,
}

WORLD = (
    card("DRE-1", "In Progress", labels=("repo:portico",)),
    card("DRE-2", "Queued", labels=("repo:portico",)),
    card("DRE-3", "Planning", labels=("repo:portico",)),
    card("DRE-4", "Backlog", labels=("repo:portico",)),
)


class Linear:
    """Linear, answering every read with nothing and recording each one."""

    def __init__(self):
        self.reads: list[tuple[str, dict]] = []

    def gql(self, query, variables=None):
        self.reads.append((" ".join(query.split()), dict(variables or {})))
        return {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False,
                                                     "endCursor": None}},
                "issue": None}

    def lanes(self) -> list[tuple]:
        """The lanes of every paged board read, in order."""
        return [tuple(v["states"]) for _, v in self.reads if "states" in v]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY",
                 "GITHUB_EVENT_NAME", "MERGED_CARD"):
        monkeypatch.delenv(name, raising=False)
    # Long enough to outlast the fakes: 0.1s did not, on a loaded runner, while
    # the issuer signed its RS256 token (DRE-9220), and every read fell back to
    # Linear. The one test that waits for the timeout sleeps past this budget.
    monkeypatch.setattr(bureau_read, "TOTAL_TIMEOUT", 2.0)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    monkeypatch.setattr(reconcile, "MAX_WIP", 5)
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()
    linear_ops.open_pass()
    yield
    linear_ops.reset_pass_cache()
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()


@pytest.fixture
def linear(monkeypatch):
    stub = Linear()
    monkeypatch.setattr(linear_ops, "gql", stub.gql)
    return stub


@contextlib.contextmanager
def door_at(monkeypatch, *cards, mode="on", **door_kw):
    world = {c["identifier"]: c for c in cards}
    with FakeIssuer() as issuer, FakeDoor(world, **door_kw) as door:
        for key, value in door_env(door_url=door.url, issuer=issuer, mode=mode).items():
            monkeypatch.setenv(key, value)
        yield door


def _lines(out: str) -> list[dict]:
    """Every `read-door: read` line, each held to the contract's shape."""
    found = []
    for raw in out.splitlines():
        if raw.startswith("read-door: read "):
            match = LINE.match(raw)
            assert match, f"not the contract's shape: {raw!r}"
            found.append(match.groupdict())
    return found


def _max_ages(door: FakeDoor, endpoint: str) -> list[str]:
    return [r["headers"]["x-bureau-max-age"] for r in door.asked(endpoint)]


# ── the table ───────────────────────────────────────────────────────────────


def test_the_table_holds_exactly_the_twelve_decisions():
    assert reconcile.DOOR_MAX_AGES == TABLE


def test_no_call_site_but_the_shadow_board_still_sends_the_board_max_age():
    tree = ast.parse((ROOT / "scripts" / "reconcile.py").read_text())
    sites = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef):
            continue
        for node in ast.walk(func):
            if (isinstance(node, ast.Attribute) and node.attr == "BOARD_MAX_AGE"
                    and isinstance(node.value, ast.Name) and node.value.id == "bureau_read"):
                sites.append(func.name)
    assert sites == ["_shadow_board"]


# ── the seven Sweep reads send their own row ───────────────────────────────


def test_the_work_lanes_read_sends_its_row(monkeypatch, linear, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        reconcile._door_work_cards()
    assert _max_ages(door, "/board") == ["120"]
    assert [x["decision"] for x in _lines(capsys.readouterr().out)] == ["work-lanes"]


def test_the_queue_board_read_sends_its_row(monkeypatch, linear, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        reconcile.backlog_children(None)
    assert _max_ages(door, "/board") == ["120"]
    assert [x["decision"] for x in _lines(capsys.readouterr().out)] == ["queue"]


def test_the_queue_cards_read_sends_its_row(monkeypatch, linear, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        reconcile.backlog_children(["DRE-2"])
    assert _max_ages(door, "/cards") == ["120"]
    assert [x["decision"] for x in _lines(capsys.readouterr().out)] == ["queue"]


def test_the_idle_check_sends_its_row(monkeypatch, linear, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        assert reconcile.sweep_idle() is None
    assert _max_ages(door, "/board") == ["120"]
    assert [x["decision"] for x in _lines(capsys.readouterr().out)] == ["idle-check"]


def test_the_planning_intake_read_sends_its_row(monkeypatch, linear, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        reconcile._fleet_or_linear(reconcile.DOOR_LINEAR_LANES, "Intake/Planning")
    assert _max_ages(door, "/board") == ["300"]
    assert [x["decision"] for x in _lines(capsys.readouterr().out)] == ["planning-intake"]


def test_the_epic_threads_read_sends_its_row(monkeypatch, linear, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        reconcile._door_epic_threads(["DRE-1"])
    assert _max_ages(door, "/cards") == ["300"]
    assert [x["decision"] for x in _lines(capsys.readouterr().out)] == ["epic-threads"]


def test_the_retiring_lanes_read_sends_its_row(monkeypatch, linear, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        reconcile._door_retiring_cards()
    assert _max_ages(door, "/board") == ["900"]
    assert [x["decision"] for x in _lines(capsys.readouterr().out)] == ["retiring-lanes"]


def test_the_unlanded_lanes_read_sends_its_row(monkeypatch, linear, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        assert reconcile._unlanded_door_lanes(["DRE-1"]) == {"DRE-1": "In Progress"}
    assert _max_ages(door, "/cards") == ["900"]
    assert [x["decision"] for x in _lines(capsys.readouterr().out)] == ["unlanded-lanes"]


# ── the line ────────────────────────────────────────────────────────────────


def test_a_fresh_work_lanes_answer_prints_its_age_rounded(monkeypatch, linear, capsys):
    with door_at(monkeypatch, *WORLD, age=42.4):
        reconcile._door_work_cards()
    out = capsys.readouterr().out.splitlines()
    assert ("read-door: read decision=work-lanes verdict=FRESH age=42 max-age=120 "
            "reason=-") in out


def test_the_helper_returns_and_prints_the_line(capsys):
    fresh = bureau_read.DoorRead(nodes=[], as_of="2026-11-02T00:00:00Z", age_seconds=7.6)
    line = reconcile.door_read_line("queue", 120, read=fresh)
    assert line == "read-door: read decision=queue verdict=FRESH age=8 max-age=120 reason=-"
    unknown = reconcile.door_read_line(
        "parked-check", 60, error=bureau_read.ReadUnknown("lane-not-held", "x"))
    assert unknown == ("read-door: read decision=parked-check verdict=UNKNOWN age=- "
                       "max-age=60 reason=lane-not-held")
    assert capsys.readouterr().out.splitlines() == [line, unknown]


def test_a_fresh_answer_with_no_age_prints_a_dash(capsys):
    line = reconcile.door_read_line("busy-threads", 120, read=bureau_read.DoorRead(nodes=[]))
    assert line == ("read-door: read decision=busy-threads verdict=FRESH age=- "
                    "max-age=120 reason=-")


def test_a_stale_retiring_answer_says_so_and_reads_linear_as_today(monkeypatch, linear,
                                                                    capsys):
    with door_at(monkeypatch, *WORLD) as door:
        door.routes["/board"] = door.unknown("stale")
        reconcile._door_retiring_cards()
    lines = _lines(capsys.readouterr().out)
    # Today's flow: the fleet scope answers UNKNOWN, the repo scope is asked,
    # and when it too answers UNKNOWN the lanes are read from Linear once.
    assert len(door.asked("/board")) == 2
    assert lines == [{"decision": "retiring-lanes", "verdict": "UNKNOWN", "age": "-",
                      "max_age": "900", "reason": "stale"}] * 2
    assert linear.lanes() == [reconcile.RETIRING_LANES]


# ── no line for a read never sent ──────────────────────────────────────────


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_and_shadow_print_no_read_line_and_read_linear_as_before(monkeypatch, linear,
                                                                      capsys, mode):
    with door_at(monkeypatch, *WORLD, mode=mode) as door:
        reconcile.active_cards(reconcile.DOOR_WORK_LANES)
        reconcile.backlog_children(None)
        reconcile._fleet_or_linear(reconcile.DOOR_LINEAR_LANES, "Intake/Planning")
        reconcile._door_epic_threads(["DRE-1"])
        reconcile._unlanded_door_lanes(["DRE-1"])
    assert _lines(capsys.readouterr().out) == []
    # The Linear board read the sweep always made, and nothing new.
    assert linear.lanes() == [reconcile.BOARD_READ_LANES]
    if mode == "shadow":  # the door IS asked in shadow — and still no line
        assert door.asked("/board")
    else:
        assert door.requests == []


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_the_idle_check_off_and_shadow_prints_no_read_line(monkeypatch, linear, capsys,
                                                           mode):
    with door_at(monkeypatch, *WORLD, mode=mode):
        reconcile.sweep_idle()
    assert _lines(capsys.readouterr().out) == []
    assert [q for q, _ in linear.reads] == [" ".join(reconcile.IDLE_QUERY.split())]


def test_a_door_stopped_for_the_run_prints_no_read_line(monkeypatch, linear, capsys):
    with door_at(monkeypatch, *WORLD) as door:
        # An answer that would be read, sent only after the budget is spent:
        # nothing but the timeout can stop the door.
        door.routes["/board"] = (200, door.envelope([], lanes=["In Progress"]),
                                 bureau_read.TOTAL_TIMEOUT + 0.5)
        with pytest.raises(bureau_read.ReadUnknown) as exc:
            bureau_read.board(["In Progress"], max_age=60)
        assert exc.value.reason == "timeout"
        assert not bureau_read.enabled()
        capsys.readouterr()
        reconcile._door_work_cards()
        reconcile._door_retiring_cards()
        reconcile._unlanded_door_lanes(["DRE-1"])
        reconcile._door_epic_threads(["DRE-1"])
    assert _lines(capsys.readouterr().out) == []
    assert linear.lanes() == [reconcile.BOARD_READ_LANES, reconcile.RETIRING_LANES]
