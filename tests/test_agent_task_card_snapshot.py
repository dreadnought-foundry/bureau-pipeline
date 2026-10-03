"""agent-task reads the card ONCE before the agent starts (Stage 2 fix #10, BP-4).

Before this, the pre-agent steps of `agent-task.yml` each read the same card
from Linear on their own — the card-validation gate (the card, its fields, its
comments), the duplicate-dispatch guard (its comments, its live lane), the turn
budget (its labels), the spoken-thread renderer (its comments and the viewer)
and the In Progress move (four reads and the write): eleven reads of one card
inside a minute.

Now ONE snapshot is taken at the top of the job (`agent_task_snapshot.py take`): from
the read door's `/cards?ids=…&comments=all` when `BUREAU_READ=on`, else ONE
combined Linear read that also carries the team's workflow states. Every
pre-agent step reads the snapshot through `agent_task_snapshot.load` and falls back
to its own read when there is none — a door that cannot answer, or a snapshot
that could not be written, never blocks a dispatch.

What stays live, on purpose:
  * the duplicate-dispatch guard's ONE fresh read of the card's lane — the
    whole question there is whether it moved while this dispatch sat queued;
  * the In Progress move's DRE-2316 pre-write re-read and its read-back (BP-3:
    a held card, so its first read is the one dropped).

The counts here are taken at the transport, so they are the `linear-calls:`
numbers (#683).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import bureau_read  # noqa: E402
import agent_task_snapshot  # noqa: E402
import dedupe_dispatch  # noqa: E402
import linear_ops  # noqa: E402
import spoken_thread  # noqa: E402
import step_shell  # noqa: E402
import turn_budget  # noqa: E402
import validate_card  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

CARD = "DRE-77"
FLEET = "fleet-user"
TEAM = "team-uuid-dre"
STATES = [
    {"id": "st-backlog", "name": "Backlog", "type": "backlog"},
    {"id": "st-todo", "name": "Todo", "type": "unstarted"},
    {"id": "st-planning", "name": "Planning", "type": "unstarted"},
    {"id": "st-progress", "name": "In Progress", "type": "started"},
    {"id": "st-review", "name": "In Review", "type": "started"},
    {"id": "st-done", "name": "Done", "type": "completed"},
]
BY_NAME = {s["name"]: s for s in STATES}
BY_ID = {s["id"]: s for s in STATES}
LABELS = ["repo:portico", "agent:engineer", "initiative:bureau", "size:s"]
DESCRIPTION = "Do the thing.\n\n## Acceptance criteria\n- [ ] it is done\n"
_REAL_URLOPEN = urllib.request.urlopen
WORKFLOW = ROOT / ".github" / "workflows" / "agent-task.yml"


class _Response:
    def __init__(self, body: dict):
        self._data = json.dumps(body).encode()
        self.headers = {}

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeLinear:
    """The card as it is NOW, answered at the transport. Records each request
    as a KIND: `snapshot` (the one combined read), `issue`, `card` (the gate's
    field read), `thread`, `states`, `viewer`, `write`, `comment`, `history`."""

    def __init__(self, *, lane="Todo", labels=LABELS, comments=(), title="Build it"):
        self.lane = lane
        self.labels = list(labels)
        self.comments = [{"body": b, "createdAt": f"2026-10-02T0{i}:00:00.000Z",
                          "user": {"id": FLEET}} for i, b in enumerate(comments)]
        self.title = title
        self.kinds: list[str] = []
        self.writes: list[str] = []

    def urlopen(self, req, timeout=None, **kw):
        if getattr(req, "full_url", "") != linear_ops.API:
            return _REAL_URLOPEN(req, timeout=timeout, **kw)
        payload = json.loads(req.data)
        q = " ".join(payload["query"].split())
        return _Response({"data": self._answer(q, payload.get("variables") or {})})

    def _issue(self) -> dict:
        return {
            "id": f"uuid-{CARD}", "identifier": CARD, "title": self.title,
            "description": DESCRIPTION, "team": {"id": TEAM, "key": "DRE"},
            "state": dict(BY_NAME[self.lane]),
            "labels": {"nodes": [{"name": n} for n in self.labels]},
            "children": {"nodes": []},
            "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                         "nodes": list(reversed(self.comments))},
        }

    def _answer(self, q: str, v: dict) -> dict:
        if "issueUpdate" in q:
            self.kinds.append("write")
            self.lane = BY_ID[v["input"]["stateId"]]["name"]
            self.writes.append(self.lane)
            return {"issueUpdate": {"success": True}}
        if "commentCreate" in q:
            self.kinds.append("comment")
            return {"commentCreate": {"success": True}}
        if "history(" in q:
            self.kinds.append("history")
            return {"issue": {"history": {"nodes": []}}}
        out: dict = {}
        if "issue(id" in q:
            if "workflowStates" in q:
                self.kinds.append("snapshot")
            elif "description" in q:
                self.kinds.append("card")
            elif "comments(" in q:
                self.kinds.append("thread")
            else:
                self.kinds.append("issue")
            out["issue"] = self._issue()
        elif "workflowStates" in q:
            self.kinds.append("states")
        elif "viewer" in q:
            self.kinds.append("viewer")
        else:
            raise AssertionError(f"unexpected Linear query: {q[:160]}")
        if "workflowStates" in q:
            out["workflowStates"] = {"nodes": [dict(s) for s in STATES]}
        if "viewer" in q:
            out["viewer"] = {"id": FLEET}
        return out

    @property
    def reads(self) -> list[str]:
        return [k for k in self.kinds if k not in ("write", "comment")]


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_PIPELINE_REF",
                 "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
                 agent_task_snapshot.ENV, "GITHUB_OUTPUT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    linear_ops._card_memo.clear()
    yield


@contextlib.contextmanager
def linear(monkeypatch, **kw):
    fake = FakeLinear(**kw)
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
    yield fake


def _quiet(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        result = fn(*a, **kw)
    return result, out.getvalue() + err.getvalue()


def _new_process():
    """A workflow step is its own process: no state carries between steps."""
    linear_ops._reset_budget_state()
    linear_ops.reset_workflow_states()
    linear_ops._card_memo.clear()
    bureau_read.reset_for_tests()


@contextlib.contextmanager
def door(monkeypatch, *nodes, route=None, mode="on"):
    world = {n["identifier"]: n for n in nodes}
    with FakeIssuer() as issuer, FakeDoor(world) as d:
        for key, value in door_env(door_url=d.url, issuer=issuer, mode=mode).items():
            monkeypatch.setenv(key, value)
        if route is not None:
            d.routes["/cards"] = route
        d.routes["/workflow-states"] = (200, d.envelope([dict(s) for s in STATES],
                                                        nodes_key="workflowStates"))
        yield d


def _door_card(lane="Todo", comments=("a comment",)):
    return card(CARD, lane, labels=LABELS, comments=comments, title="Build it",
                description=DESCRIPTION)


def _take(path) -> dict | None:
    _quiet(agent_task_snapshot.main, ["take", CARD, "--out", str(path)])
    return json.loads(path.read_text()) if path.exists() else None


# ── taking the snapshot ─────────────────────────────────────────────────────


def test_off_the_snapshot_is_one_linear_read(monkeypatch, tmp_path):
    path = tmp_path / "snap.json"
    with linear(monkeypatch, comments=("hello",)) as lin:
        snap = _take(path)
    assert lin.kinds == ["snapshot"]
    assert snap["source"] == "linear"
    assert snap["issue"]["identifier"] == CARD
    assert snap["issue"]["description"] == DESCRIPTION
    assert snap["viewer"] == FLEET
    assert [c["body"] for c in snap["comments"]] == ["hello"]
    assert {s["name"] for s in snap["workflowStates"]} >= {"In Progress", "Todo"}


def test_on_the_snapshot_comes_from_the_door_with_no_linear_read(monkeypatch, tmp_path):
    path = tmp_path / "snap.json"
    with linear(monkeypatch) as lin, door(monkeypatch, _door_card()) as d:
        snap = _take(path)
    assert lin.kinds == []
    assert snap["source"] == "door"
    assert [c["body"] for c in snap["comments"]] == ["a comment"]
    asked = d.asked("/cards")[0]["query"]
    assert asked["ids"] == CARD and asked["comments"] == "all"


@pytest.mark.parametrize("reason", ["stale", "reread-pending", "linear-hold"])
def test_on_a_door_that_cannot_answer_falls_back_to_one_linear_read(
        monkeypatch, tmp_path, reason):
    path = tmp_path / "snap.json"
    with linear(monkeypatch) as lin, door(monkeypatch, _door_card()) as d:
        d.routes["/cards"] = d.unknown(reason)
        snap, out = None, ""
        _, out = _quiet(agent_task_snapshot.main, ["take", CARD, "--out", str(path)])
        snap = json.loads(path.read_text())
    assert lin.kinds == ["snapshot"]
    assert snap["source"] == "linear"
    assert reason in out


def test_on_a_door_answer_the_client_chokes_on_falls_back(monkeypatch, tmp_path):
    """Linear on ANY door error: a dispatch never dies because the door did."""
    def chokes(*a, **kw):
        raise TypeError("an envelope the client did not expect")

    monkeypatch.setenv("BUREAU_READ", "on")
    monkeypatch.setattr(bureau_read, "cards", chokes)
    path = tmp_path / "snap.json"
    with linear(monkeypatch) as lin:
        snap = _take(path)
    assert lin.kinds == ["snapshot"]
    assert snap["source"] == "linear"


def test_on_a_door_missing_the_card_falls_back(monkeypatch, tmp_path):
    path = tmp_path / "snap.json"
    with linear(monkeypatch) as lin, door(monkeypatch):  # the door holds no card
        snap = _take(path)
    assert lin.kinds == ["snapshot"]
    assert snap["source"] == "linear"


def test_on_a_long_door_thread_the_snapshot_keeps_the_newest_fifty(monkeypatch, tmp_path):
    """The window every step's own Linear read took is the fifty NEWEST
    (DRE-3250). The duplicate guard looks for the newest heartbeat and the
    epic gate for the shape stamp: the oldest fifty would get both wrong."""
    node = _door_card(comments=tuple(f"c{i}" for i in range(60)))
    path = tmp_path / "snap.json"
    with linear(monkeypatch) as lin, door(monkeypatch, node):
        snap = _take(path)
    assert lin.kinds == []
    assert snap["source"] == "door"
    assert agent_task_snapshot.comment_bodies(snap) == [f"c{i}" for i in range(10, 60)]
    assert snap["comments_partial"] is True


def test_on_a_long_door_thread_it_cannot_say_is_whole_reads_linear(monkeypatch, tmp_path):
    """`comments=all` is FRESH to the client only for a provably whole thread
    (BP-8's `thread-incomplete`): never half an answer, one Linear read."""
    node = _door_card(comments=tuple(f"c{i}" for i in range(60)))
    node["comments"]["pageInfo"]["hasNextPage"] = True
    path = tmp_path / "snap.json"
    with linear(monkeypatch) as lin, door(monkeypatch, node):
        snap, out = None, _quiet(agent_task_snapshot.main, ["take", CARD, "--out", str(path)])[1]
        snap = json.loads(path.read_text())
    assert lin.kinds == ["snapshot"]
    assert snap["source"] == "linear"
    assert "thread-incomplete" in out


def test_on_a_door_thread_it_cannot_vouch_for_is_not_used(monkeypatch, tmp_path):
    """The door says it does not hold the whole thread, and holds fewer than the
    fifty newest a Linear read would give: never half an answer."""
    node = _door_card()
    node["comments"]["pageInfo"]["hasNextPage"] = True
    path = tmp_path / "snap.json"
    with linear(monkeypatch) as lin, door(monkeypatch, node):
        snap = _take(path)
    assert lin.kinds == ["snapshot"]
    assert snap["source"] == "linear"


def test_shadow_reads_the_door_and_keeps_linears_answer(monkeypatch, tmp_path):
    path = tmp_path / "snap.json"
    stale = _door_card(lane="Backlog")
    with linear(monkeypatch) as lin, door(monkeypatch, stale, mode="shadow") as d:
        _, out = _quiet(agent_task_snapshot.main, ["take", CARD, "--out", str(path)])
    snap = json.loads(path.read_text())
    assert len(d.asked("/cards")) == 1
    assert lin.kinds == ["snapshot"]
    assert snap["source"] == "linear"
    assert snap["issue"]["state"]["name"] == "Todo"
    assert "read-door-diff:" in out


def test_a_failed_snapshot_writes_nothing_and_exits_zero(monkeypatch, tmp_path):
    def broken(req, timeout=None, **kw):
        raise OSError("no route")

    monkeypatch.setattr(urllib.request, "urlopen", broken)
    monkeypatch.setattr(linear_ops, "RETRY_BACKOFF_SECONDS", 0)
    path = tmp_path / "snap.json"
    code, out = _quiet(agent_task_snapshot.main, ["take", CARD, "--out", str(path)])
    assert code == 0
    assert not path.exists()
    assert "no snapshot" in out


# ── loading it ──────────────────────────────────────────────────────────────


def test_load_without_the_variable_is_none(monkeypatch):
    assert agent_task_snapshot.load(CARD) is None


def test_load_refuses_another_cards_snapshot(monkeypatch, tmp_path):
    path = tmp_path / "snap.json"
    with linear(monkeypatch):
        _take(path)
    monkeypatch.setenv(agent_task_snapshot.ENV, str(path))
    assert agent_task_snapshot.load(CARD) is not None
    assert agent_task_snapshot.load("DRE-78") is None


@pytest.mark.parametrize("text", ["", "{not json", '{"schema": "other/9"}', "[]"])
def test_load_refuses_an_unreadable_snapshot(monkeypatch, tmp_path, text):
    path = tmp_path / "snap.json"
    path.write_text(text)
    monkeypatch.setenv(agent_task_snapshot.ENV, str(path))
    assert agent_task_snapshot.load(CARD) is None


def test_load_of_a_missing_file_is_none(monkeypatch, tmp_path):
    monkeypatch.setenv(agent_task_snapshot.ENV, str(tmp_path / "absent.json"))
    assert agent_task_snapshot.load(CARD) is None


# ── each pre-agent step, with and without a snapshot ────────────────────────


def _snapshot(monkeypatch, tmp_path, lin_kw=None):
    path = tmp_path / "snap.json"
    with linear(monkeypatch, **(lin_kw or {})):
        _take(path)
    monkeypatch.setenv(agent_task_snapshot.ENV, str(path))
    return path


def test_the_gate_decides_a_clean_card_on_the_snapshot(monkeypatch, tmp_path):
    _snapshot(monkeypatch, tmp_path)
    _new_process()
    with linear(monkeypatch) as lin:
        _, out = _quiet(validate_card.cmd_gate, CARD)
    assert lin.kinds == []
    assert "bounced=false" in out and "role=engineer" in out


def test_the_gate_without_a_snapshot_reads_as_it_always_did(monkeypatch):
    with linear(monkeypatch) as lin:
        _, out = _quiet(validate_card.cmd_gate, CARD)
    assert lin.kinds == ["issue", "card", "thread"]
    assert "bounced=false" in out


def test_the_gate_on_the_snapshot_still_bounces_a_broken_card(monkeypatch, tmp_path):
    _snapshot(monkeypatch, tmp_path, lin_kw={"labels": ["agent:engineer"]})
    _new_process()
    with linear(monkeypatch, labels=["agent:engineer"]) as lin:
        _, out = _quiet(validate_card.cmd_gate, CARD)
    assert "bounced=true" in out
    assert lin.writes == ["Planning"]
    # Decided on the snapshot: the bounce's only reads are its write's own
    # guard (the move's re-read and read-back, BP-3) and nothing before it.
    assert "card" not in lin.kinds


def test_the_turn_budget_reads_the_labels_off_the_snapshot(monkeypatch, tmp_path):
    _snapshot(monkeypatch, tmp_path)
    _new_process()
    with linear(monkeypatch) as lin:
        code, out = _quiet(turn_budget.main, ["select", CARD])
    assert code == 0
    assert lin.kinds == []
    assert "size:s" in out.lower()


def test_the_spoken_thread_reads_the_comments_off_the_snapshot(monkeypatch, tmp_path):
    _snapshot(monkeypatch, tmp_path, lin_kw={"comments": ("pipeline note",)})
    _new_process()
    with linear(monkeypatch) as lin:
        _, out = _quiet(spoken_thread.main, ["people", CARD])
    assert lin.kinds == []
    assert "1 comment(s)" in out


def test_the_spoken_thread_reads_linear_when_the_snapshot_names_no_viewer(
        monkeypatch, tmp_path):
    path = _snapshot(monkeypatch, tmp_path)
    snap = json.loads(path.read_text())
    snap["viewer"] = None
    path.write_text(json.dumps(snap))
    _new_process()
    with linear(monkeypatch) as lin:
        _quiet(spoken_thread.main, ["people", CARD])
    assert lin.kinds == ["thread"]


def test_the_duplicate_guard_keeps_its_one_fresh_read(monkeypatch, tmp_path):
    _snapshot(monkeypatch, tmp_path)
    _new_process()
    monkeypatch.setattr(dedupe_dispatch, "_card_prs", lambda ident: [])
    with linear(monkeypatch) as lin:
        _, out = _quiet(dedupe_dispatch.cmd_gate, CARD)
    assert lin.kinds == ["issue"]  # the live lane — and nothing else
    assert "skip=false" in out


def test_the_in_progress_move_takes_its_lanes_off_the_snapshot(monkeypatch, tmp_path):
    _snapshot(monkeypatch, tmp_path)
    _new_process()
    with linear(monkeypatch) as lin:
        moved, _ = _quiet(linear_ops.cmd_state, CARD, "In Progress", "--held")
    assert moved is True
    # The DRE-2316 re-read, the write, the read-back: no first read, no states.
    assert lin.kinds == ["issue", "write", "history"]


# ── the whole pre-agent stretch ─────────────────────────────────────────────


def _pre_agent(monkeypatch, *, snapshot_path=None) -> list[int]:
    """Every pre-agent Linear step of agent-task.yml, each as its own process;
    returns each step's request count."""
    monkeypatch.setattr(dedupe_dispatch, "_card_prs", lambda ident: [])
    counts = []
    steps = []
    if snapshot_path is not None:
        steps.append(lambda: agent_task_snapshot.main(["take", CARD, "--out", str(snapshot_path)]))
    steps += [
        lambda: validate_card.cmd_gate(CARD),
        lambda: dedupe_dispatch.cmd_gate(CARD),
        lambda: turn_budget.main(["select", CARD]),
        lambda: spoken_thread.main(["people", CARD]),
        lambda: linear_ops.cmd_state(CARD, "In Progress",
                                     *(("--held",) if snapshot_path else ())),
    ]
    for step in steps:
        _new_process()
        if snapshot_path is not None and snapshot_path.exists():
            monkeypatch.setenv(agent_task_snapshot.ENV, str(snapshot_path))
        _quiet(step)
        counts.append(linear_ops.requests_made())
    return counts


def test_before_the_pre_agent_steps_read_the_card_eleven_times(monkeypatch):
    with linear(monkeypatch) as lin:
        _pre_agent(monkeypatch)
    assert len(lin.reads) == 11
    assert lin.writes == ["In Progress"]


def test_off_the_pre_agent_steps_read_linear_four_times(monkeypatch, tmp_path):
    with linear(monkeypatch) as lin:
        _pre_agent(monkeypatch, snapshot_path=tmp_path / "snap.json")
    # The snapshot, the guard's fresh lane, the move's re-read and read-back.
    assert lin.reads == ["snapshot", "issue", "issue", "history"]
    assert lin.writes == ["In Progress"]


def test_on_the_pre_agent_steps_read_linear_three_times(monkeypatch, tmp_path):
    with linear(monkeypatch) as lin, door(monkeypatch, _door_card()):
        _pre_agent(monkeypatch, snapshot_path=tmp_path / "snap.json")
    assert lin.reads == ["issue", "issue", "history"]
    assert lin.writes == ["In Progress"]


# ── the workflow wiring ─────────────────────────────────────────────────────


def _steps() -> list[dict]:
    import yaml

    doc = yaml.safe_load(WORKFLOW.read_text())
    job = next(iter(doc["jobs"].values()))
    return job["steps"]


def _step(name: str) -> dict:
    return next(s for s in _steps() if s.get("name") == name)


def _index(name: str) -> int:
    return [s.get("name") for s in _steps()].index(name)


SNAPSHOT_STEP = "Card snapshot (one read for every pre-agent step)"


def test_the_snapshot_is_taken_before_the_gate_and_never_fails_the_job():
    step = _step(SNAPSHOT_STEP)
    assert _index(SNAPSHOT_STEP) < _index("Card-validation gate")
    assert step.get("continue-on-error") is True
    shell = step_shell.step_shell(step)
    assert "agent_task_snapshot.py take" in shell
    env = step.get("env") or {}
    assert env.get(agent_task_snapshot.ENV) == "${{ runner.temp }}/card-snapshot.json"
    assert "vars.BUREAU_READ" in str(env.get("BUREAU_READ"))
    assert env.get("BUREAU_PIPELINE_REF") == "${{ inputs.pipeline_ref }}"
    assert "LINEAR_API_KEY" in env


@pytest.mark.parametrize("name", ["Card-validation gate", "Select model",
                                  "Assemble agent context", "Card → In Progress"])
def test_every_pre_agent_reader_is_handed_the_snapshot(name):
    env = _step(name).get("env") or {}
    assert env.get(agent_task_snapshot.ENV) == "${{ runner.temp }}/card-snapshot.json"


def test_the_duplicate_guard_is_handed_the_snapshot_for_comments_only():
    env = _step("Duplicate-dispatch guard").get("env") or {}
    assert env.get(agent_task_snapshot.ENV) == "${{ runner.temp }}/card-snapshot.json"


def test_the_in_progress_move_is_a_held_card_with_the_door_mode():
    step = _step("Card → In Progress")
    assert '"In Progress" --held' in step["run"]
    env = step.get("env") or {}
    assert "vars.BUREAU_READ" in str(env.get("BUREAU_READ"))


def test_no_step_after_the_move_is_handed_the_snapshot():
    """The agent and everything after it read the card as it is NOW: a
    snapshot from before the build must never answer for it."""
    after = _steps()[_index("Card → In Progress") + 1:]
    for step in after:
        assert agent_task_snapshot.ENV not in (step.get("env") or {}), step.get("name")
