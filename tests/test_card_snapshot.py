"""The critic's and the verifier's card, read once per run (Stage 2 fix #14, BP-8).

`qa-review.yml` and `verify.yml` each read the card twice from Linear before
this card: the description (the **Design:** line; the verifier's labels too)
and the comment thread (the build's `model-attempt:` heartbeat, DRE-3880).
`scripts/card_snapshot.py` takes ONE snapshot per job and every step reads it.

What these pin, each against the failure it exists to stop:

* `BUREAU_READ` off: exactly ONE Linear request, and the same answers the two
  reads gave — the description verbatim, the labels, the fifty-newest window
  oldest→newest (the order `last_attempt_model` scans from the end of).
* The door is never asked from a `pull_request` / `pull_request_target` run:
  the door refuses those tokens by design (S7), so asking costs a refusal the
  door counts toward its refused-and-never-served alarm (M2) and buys nothing.
* In `on`, a `workflow_dispatch` run is served by the door with no Linear
  call; a door that cannot give the WHOLE answer (UNKNOWN, closed, a thread it
  cannot prove complete) falls back to one Linear read; `linear-hold` makes no
  Linear call at all.
* In `shadow`, both are read, the difference is logged, Linear's answer is used.
* A read that fails writes no snapshot, and removes a stale one, so a step
  degrades exactly the way it did when its own read failed.

No test here reaches the console, GitHub or Linear: the door and the issuer are
`tests/bureau_read_fakes.py` on 127.0.0.1, and Linear is a fake `gql`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bureau_read  # noqa: E402
import card_snapshot  # noqa: E402
import linear_ops  # noqa: E402
import model_fallback  # noqa: E402
import verify_scope  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

DESIGN = "Build the board.\n\n**Design:** design/board.png\n"
HEARTBEAT_OLD = f"{model_fallback.MARKER_PREFIX} claude-opus-5-5"
HEARTBEAT_NEW = f"{model_fallback.MARKER_PREFIX} claude-sonnet-5"
THREAD = ["filed", HEARTBEAT_OLD, "died", HEARTBEAT_NEW, "pushed"]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
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


def _linear_issue(ident="DRE-7", *, description=DESIGN, labels=("ux",), bodies=THREAD,
                  updated="2026-10-02T19:00:00.000Z", has_next=False):
    """Linear's answer to the one union read: comments NEWEST first, as the
    `first:` window arrives (linear_ops' DRE-3250 note)."""
    # The same stamps `bureau_read_fakes.card` gives the door's copy, so an
    # unchanged thread compares equal in the shadow tests.
    nodes = [{"body": b, "createdAt": f"2026-09-0{1 + i % 9}T00:00:{i % 60:02d}.000Z",
              "user": {"id": "fleet-user"}} for i, b in enumerate(bodies)]
    return {"issue": {
        "identifier": ident, "updatedAt": updated, "description": description,
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"pageInfo": {"hasNextPage": has_next, "endCursor": None},
                     "nodes": list(reversed(nodes))},
    }}


class FakeLinear:
    def __init__(self, answer=None, error: Exception | None = None):
        self.answer = answer if answer is not None else _linear_issue()
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def gql(self, query, variables=None):
        self.calls.append((query, variables or {}))
        if self.error is not None:
            raise self.error
        return self.answer


def _take(linear: FakeLinear, ident="DRE-7"):
    with mock.patch.object(linear_ops, "gql", side_effect=linear.gql):
        return card_snapshot.take(ident)


DOOR_WORLD = {"DRE-7": card("DRE-7", "In Review", labels=("ux",), comments=THREAD,
                            description=DESIGN)}


# ── off: one Linear read where there were two ───────────────────────────────


def test_off_reads_linear_once_and_never_the_door(monkeypatch):
    linear = FakeLinear()
    with FakeIssuer() as issuer, FakeDoor(DOOR_WORLD) as door:
        _point(monkeypatch, door, issuer, mode="off")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        snap = _take(linear)
        assert door.requests == [] and issuer.requests == []
    assert len(linear.calls) == 1
    query, variables = linear.calls[0]
    assert variables == {"id": "DRE-7"}
    # The union: what `linear_ops.py description` asked, what verify_scope
    # asked, and the SAME comment window `dump-comments` asked (one literal).
    for field in ("description", "labels { nodes { name } }", linear_ops.COMMENT_WINDOW_GQL):
        assert field in query
    assert snap["source"] == "linear"
    assert snap["description"] == DESIGN
    assert snap["labels"] == ["ux"]
    assert snap["comments"] == THREAD  # oldest → newest


def test_off_answers_what_the_two_reads_answered(tmp_path):
    """The consumers' answers are byte-for-byte the old commands' answers."""
    linear = FakeLinear()
    snap = _take(linear)
    path = tmp_path / "card.json"
    card_snapshot.write(path, snap)
    # `linear_ops.py description` printed the raw description.
    assert card_snapshot.description_of(path) == DESIGN
    # `linear_ops.py dump-comments` printed a JSON array of bodies, oldest
    # first — and build-model reads the NEWEST heartbeat out of it.
    thread = tmp_path / "thread.json"
    thread.write_text(card_snapshot.thread_json(path))
    assert json.loads(thread.read_text()) == THREAD
    assert model_fallback._build_model_from_thread(str(thread)) == "claude-sonnet-5"
    # verify_scope.read_card's shape: description + labels.
    assert verify_scope.card_from_snapshot(str(path)) == {"description": DESIGN,
                                                          "labels": ["ux"]}


def test_a_null_description_reads_as_an_empty_one(tmp_path):
    linear = FakeLinear(_linear_issue(description=None, labels=("ux",)))
    path = tmp_path / "card.json"
    card_snapshot.write(path, _take(linear))
    assert card_snapshot.description_of(path) == ""
    # read_card's rule: an empty description is no card signal, labels or not.
    assert verify_scope.card_from_snapshot(str(path)) is None


def test_a_linear_failure_writes_no_snapshot_and_removes_a_stale_one(tmp_path, capsys):
    path = tmp_path / "card.json"
    path.write_text(json.dumps({"stale": True}))  # an earlier job on this machine
    linear = FakeLinear(error=linear_ops.LinearError("boom"))
    with mock.patch.object(linear_ops, "gql", side_effect=linear.gql):
        code = card_snapshot.main(["take", "DRE-7", str(path)])
    assert code == 1
    assert not path.exists()
    assert card_snapshot.description_of(path) is None
    assert card_snapshot.thread_json(path) is None
    assert verify_scope.card_from_snapshot(str(path)) is None
    assert "boom" in capsys.readouterr().err


def test_a_card_linear_does_not_hold_writes_no_snapshot(tmp_path):
    path = tmp_path / "card.json"
    linear = FakeLinear({"issue": None})
    with mock.patch.object(linear_ops, "gql", side_effect=linear.gql):
        assert card_snapshot.main(["take", "DRE-7", str(path)]) == 1
    assert not path.exists()


def test_the_cli_prints_the_old_commands_output(tmp_path):
    path = tmp_path / "card.json"
    card_snapshot.write(path, _take(FakeLinear()))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "scripts")}
    script = str(ROOT / "scripts" / "card_snapshot.py")
    desc = subprocess.run([sys.executable, script, "description", str(path)],
                          capture_output=True, text=True, env=env, check=True)
    assert desc.stdout == DESIGN
    thread = subprocess.run([sys.executable, script, "thread", str(path)],
                            capture_output=True, text=True, env=env, check=True)
    assert json.loads(thread.stdout) == THREAD
    missing = subprocess.run([sys.executable, script, "thread", str(tmp_path / "nope.json")],
                             capture_output=True, text=True, env=env)
    assert missing.returncode == 1 and missing.stdout == ""


# ── on: the door serves a trusted event; a refused event never asks ────────


def test_on_a_dispatched_run_is_served_by_the_door(monkeypatch):
    linear = FakeLinear()
    with FakeIssuer() as issuer, FakeDoor(DOOR_WORLD) as door:
        _point(monkeypatch, door, issuer)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        snap = _take(linear)
        sent = door.asked("/cards")
    assert linear.calls == []
    assert snap["source"] == "door"
    assert snap["description"] == DESIGN and snap["labels"] == ["ux"]
    assert snap["comments"] == THREAD  # the door answers newest first, reversed once
    assert sent[0]["query"] == {"ids": "DRE-7", "comments": "all", "relations": "0"}
    assert sent[0]["headers"]["x-bureau-max-age"] == str(bureau_read.REVIEW_CARD_MAX_AGE)


@pytest.mark.parametrize("event", ["pull_request", "pull_request_target"])
def test_on_a_pull_request_run_never_asks_the_door(monkeypatch, event, capsys):
    linear = FakeLinear()
    with FakeIssuer() as issuer, FakeDoor(DOOR_WORLD) as door:
        _point(monkeypatch, door, issuer)
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
        snap = _take(linear)
        # No token minted, nothing sent: the door would only refuse it.
        assert door.requests == [] and issuer.requests == []
    assert len(linear.calls) == 1 and snap["source"] == "linear"
    assert f"{event}" in capsys.readouterr().err


@pytest.mark.parametrize("event", ["pull_request", "pull_request_target"])
def test_the_client_refuses_a_refused_event_before_it_mints(monkeypatch, event):
    """Client-wide, not just here: linear-sync runs on pull_request too."""
    with FakeIssuer() as issuer, FakeDoor(DOOR_WORLD) as door:
        _point(monkeypatch, door, issuer)
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
        with pytest.raises(bureau_read.ReadUnknown) as raised:
            bureau_read.cards(["DRE-7"], max_age=120, relations=False)
        assert raised.value.reason == "event-refused" and raised.value.unavailable
        assert not raised.value.skip
        assert door.requests == [] and issuer.requests == []
    assert bureau_read.disabled_reason() == "event-refused"


def test_on_a_stale_door_falls_back_to_one_linear_read(monkeypatch):
    linear = FakeLinear()
    with FakeIssuer() as issuer, FakeDoor(DOOR_WORLD) as door:
        _point(monkeypatch, door, issuer)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        door.routes["/cards"] = door.unknown("stale")
        snap = _take(linear)
    assert len(linear.calls) == 1 and snap["source"] == "linear"


def test_on_a_closed_door_falls_back_to_one_linear_read(monkeypatch):
    linear = FakeLinear()
    with FakeIssuer() as issuer, FakeDoor(DOOR_WORLD) as door:
        _point(monkeypatch, door, issuer)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        door.routes["/cards"] = (503, {"error": {"code": "CLOSED"}})
        snap = _take(linear)
    assert len(linear.calls) == 1 and snap["source"] == "linear"


def test_on_a_thread_the_door_cannot_prove_whole_falls_back(monkeypatch):
    """`comments=all` and `hasNextPage: true` is half a thread: a missing
    heartbeat would fail the model separation closed where Linear finds it."""
    partial = card("DRE-7", "In Review", labels=("ux",), comments=THREAD, description=DESIGN)
    partial["comments"]["pageInfo"]["hasNextPage"] = True
    linear = FakeLinear()
    with FakeIssuer() as issuer, FakeDoor({"DRE-7": partial}) as door:
        _point(monkeypatch, door, issuer)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        snap = _take(linear)
    assert len(linear.calls) == 1 and snap["source"] == "linear"
    assert "unknown 1" in bureau_read.exit_line()


def test_cards_with_every_comment_refuses_a_thread_it_cannot_prove_whole(monkeypatch):
    partial = card("DRE-7", "In Review", comments=THREAD)
    partial["comments"]["pageInfo"]["hasNextPage"] = True
    with FakeIssuer() as issuer, FakeDoor({"DRE-7": partial}) as door:
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as raised:
            bureau_read.cards(["DRE-7"], max_age=120, comments="all", relations=False)
    assert raised.value.reason == "thread-incomplete"
    # The door stays in use: one card's thread is not the door failing.
    assert bureau_read.enabled()


def test_on_a_linear_hold_makes_no_linear_call(monkeypatch, tmp_path, capsys):
    linear = FakeLinear()
    path = tmp_path / "card.json"
    with FakeIssuer() as issuer, FakeDoor(DOOR_WORLD) as door:
        _point(monkeypatch, door, issuer)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        door.routes["/cards"] = door.unknown("linear-hold")
        with mock.patch.object(linear_ops, "gql", side_effect=linear.gql):
            code = card_snapshot.main(["take", "DRE-7", str(path)])
    assert code == 1 and linear.calls == [] and not path.exists()
    assert "no Linear fallback" in capsys.readouterr().err


# ── shadow: both read, the difference logged, Linear's answer used ─────────


def test_shadow_reads_both_and_acts_on_linear(monkeypatch, capsys):
    changed = _linear_issue(description=DESIGN + "\nEdited.",
                            updated="2026-10-02T21:00:00.000Z")
    linear = FakeLinear(changed)
    with FakeIssuer() as issuer, FakeDoor(DOOR_WORLD) as door:
        _point(monkeypatch, door, issuer, mode="shadow")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        snap = _take(linear)
        assert len(door.asked("/cards")) == 1
    assert len(linear.calls) == 1
    assert snap["source"] == "linear" and snap["description"].endswith("Edited.")
    out = capsys.readouterr().out
    assert "read-door-diff: door-older card DRE-7 description" in out
    assert "read-door-diff: card compared 1 card(s): 1 explained" in out


def test_shadow_with_no_difference_says_so(monkeypatch, capsys):
    linear = FakeLinear()
    with FakeIssuer() as issuer, FakeDoor(DOOR_WORLD) as door:
        _point(monkeypatch, door, issuer, mode="shadow")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        _take(linear)
    out = capsys.readouterr().out
    assert "read-door-diff: card compared 1 card(s): 0 explained" in out
    assert "0 unexplained" in out


def test_shadow_on_a_pull_request_run_is_today_with_no_door(monkeypatch):
    linear = FakeLinear()
    with FakeIssuer() as issuer, FakeDoor(DOOR_WORLD) as door:
        _point(monkeypatch, door, issuer, mode="shadow")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        snap = _take(linear)
        assert door.requests == [] and issuer.requests == []
    assert len(linear.calls) == 1 and snap["source"] == "linear"


# ── verify_scope reads the snapshot and never Linear ────────────────────────


def test_verify_scope_with_a_snapshot_never_reads_linear(tmp_path, capsys):
    path = tmp_path / "card.json"
    card_snapshot.write(path, _take(FakeLinear()))
    changed = tmp_path / "changed.txt"
    changed.write_text("README.md\n")
    with mock.patch.object(linear_ops, "gql", side_effect=AssertionError("Linear read")):
        verify_scope.main(["--card", "DRE-7", "--changed", str(changed),
                           "--snapshot", str(path)])
    out = capsys.readouterr().out
    assert "in_scope=true" in out and "is_ui=true" in out  # the Design line


def test_verify_scope_with_a_missing_snapshot_has_no_card_signal(tmp_path, capsys):
    changed = tmp_path / "changed.txt"
    changed.write_text("web/src/App.tsx\n")
    with mock.patch.object(linear_ops, "gql", side_effect=AssertionError("Linear read")):
        verify_scope.main(["--card", "DRE-7", "--changed", str(changed),
                           "--snapshot", str(tmp_path / "missing.json")])
    out = capsys.readouterr()
    assert "in_scope=false" in out.out
    assert "card=unreadable" in out.err
