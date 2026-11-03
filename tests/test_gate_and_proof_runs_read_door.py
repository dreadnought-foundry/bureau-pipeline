"""Merge Gate, Proof Task and Deliver Rescue read their card through the read door (DRE-8901).

Each of the three jobs — merge-gate.yml's `evaluate`, proof-task.yml's
`execute`, deliver-rescue.yml's `deliver` — carries the door's four variables
at job level, as `reconcile.yml` sets them on its steps, and opts its card
reads in with `BUREAU_READ_CARD_MAX_AGE: 300`: five minutes, the near end of
the five to fifteen the epic's survey measured for these readers. The
`linear_ops` thread reads and comment pre-reads that DRE-8898 made door-first
then ask the door.

The gate's job opens with one more step, `Note the job's start for the read
door`, which writes `BUREAU_READ_CARD_SEEN_AFTER` to `$GITHUB_ENV`. Its hold
reads (`code_owner_hold.park` and `release`, through `comment_records`) look
for the marker an EARLIER gate run posted, and the `evaluate` job's
concurrency group (`merge-gate-pr-<pr>`, never cancel-in-progress) means that
run finished before this job began. So a door answer older than the job's
start is refused, with `reason=older-than-needed`, and the thread is read from
Linear. The scenarios below show both sides: the floor sends a stale door
answer to Linear, and without it the same answer would post a second hold or
leave a parked card in Green Light.

Deliver Rescue's stub grants `id-token: write` beside `contents: read` and
`actions: read`: a `permissions:` block removes every permission it does not
name.

No test reaches Linear or the console. Linear is `GateLinear`, a urlopen
stand-in that answers api.linear.app by what each request asks, and every
request is recorded at `linear_ops._send` — the transport seam — with the
`linear-read:` caller it is counted under. The door and its token issuer are
`tests/bureau_read_fakes.py` on 127.0.0.1.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(TESTS))

import bureau_read  # noqa: E402
import code_owner_hold  # noqa: E402
import linear_ops  # noqa: E402
import spoken_thread  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402
from test_linear_ops_card_via_door import DOOR_ENVS, _Resp  # noqa: E402

#: The three jobs this card moves onto the door: `(workflow, job)`.
GATE = ("merge-gate.yml", "evaluate")
PROOF = ("proof-task.yml", "execute")
RESCUE = ("deliver-rescue.yml", "deliver")
JOBS = (GATE, PROOF, RESCUE)

#: The door's variables, exactly as reconcile.yml's door steps set them, and
#: the step variable that opts these jobs' card reads in (DRE-8898).
DOOR_VARS = {
    "BUREAU_READ": "${{ vars.BUREAU_READ || 'off' }}",
    "BUREAU_READ_URL": "${{ vars.BUREAU_READ_URL }}",
    "BUREAU_READ_AUDIENCE": "${{ vars.BUREAU_READ_AUDIENCE }}",
    "BUREAU_PIPELINE_REF": "${{ inputs.pipeline_ref }}",
}
MAX_AGE = "BUREAU_READ_CARD_MAX_AGE"
SEEN_AFTER = "BUREAU_READ_CARD_SEEN_AFTER"
START_STEP = "Note the job's start for the read door"

#: Who Linear says this process's key is: the pipeline, for authorship.
LINEAR_VIEWER = "fleet-linear-viewer"
CARD = "DRE-7"
PR = 42
HOLD = f"⏸️ merge-gate: code-owner hold · PR #{PR}\n\nPull request #{PR} is held."
VIEWER_QUERY = "query { viewer { id } }"
#: The board the lane moves resolve their lanes on, door and Linear alike.
STATES = [
    {"id": "state-review", "name": "In Review", "type": "started"},
    {"id": "state-green", "name": "Green Light", "type": "unstarted"},
    {"id": "state-progress", "name": "In Progress", "type": "started"},
]


# ── the workflow YAML ───────────────────────────────────────────────────────


def _doc(workflow: str) -> dict:
    return yaml.safe_load((WORKFLOWS / workflow).read_text())


def _job(workflow: str, job: str) -> dict:
    return _doc(workflow)["jobs"][job]


def _env_blocks(workflow: str, job: str) -> list[dict]:
    """The job's own `env:` and every step's."""
    j = _job(workflow, job)
    return [j.get("env") or {}] + [s.get("env") or {} for s in j["steps"]]


@pytest.mark.parametrize("workflow,job", JOBS)
def test_each_job_carries_the_door_variables(workflow, job):
    env = _job(workflow, job).get("env") or {}
    for name, value in DOOR_VARS.items():
        assert env.get(name) == value, f"{workflow} {job}: {name}"


@pytest.mark.parametrize("workflow,job", JOBS)
def test_each_job_opts_its_card_reads_in_at_five_minutes(workflow, job):
    env = _job(workflow, job).get("env") or {}
    assert str(env.get(MAX_AGE)) == "300"
    # And no step names any of them again: the job's value is every step's.
    for step in _job(workflow, job)["steps"]:
        named = set(step.get("env") or {}) & {*DOOR_VARS, MAX_AGE}
        assert not named, f"{workflow} {job} step {step.get('name')!r} sets {named}"


def test_the_gate_opens_with_the_start_note():
    first = _job(*GATE)["steps"][0]
    assert first.get("name") == START_STEP
    assert SEEN_AFTER in first["run"] and '"$GITHUB_ENV"' in first["run"]
    assert "${{" not in first["run"]


def test_the_start_note_writes_the_job_start_in_utc_to_the_second(tmp_path):
    run = _job(*GATE)["steps"][0]["run"]
    github_env = tmp_path / "github_env"
    github_env.write_text("")
    before = datetime.now(UTC).replace(microsecond=0)
    proc = subprocess.run(["bash", "-eo", "pipefail", "-c", run], capture_output=True,
                          text=True, env={**os.environ, "GITHUB_ENV": str(github_env)},
                          timeout=30)
    after = datetime.now(UTC)
    assert proc.returncode == 0, proc.stderr
    (line,) = github_env.read_text().splitlines()
    name, _, value = line.partition("=")
    assert name == SEEN_AFTER
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value), value
    at = linear_ops._instant(value)
    assert at is not None and before <= at <= after


def test_no_env_block_in_the_gate_names_the_floor():
    """`$GITHUB_ENV` is overridden by a job or step `env:` that names the same
    variable, so none may — the start note is the one place it is set."""
    for block in _env_blocks(*GATE):
        assert SEEN_AFTER not in block


@pytest.mark.parametrize("workflow,job", [PROOF, RESCUE])
def test_proof_and_rescue_note_no_start(workflow, job):
    """They read no receipt an earlier run posted moments before."""
    steps = _job(workflow, job)["steps"]
    assert START_STEP not in [s.get("name") for s in steps]
    assert not any(SEEN_AFTER in (s.get("run") or "") for s in steps)
    for block in _env_blocks(workflow, job):
        assert SEEN_AFTER not in block


def test_the_rescue_stub_grants_the_id_token_and_keeps_its_reads():
    stub = _doc("self-deliver-rescue.yml")
    assert stub.get("permissions") == {
        "id-token": "write", "contents": "read", "actions": "read"}


# ── the scenarios' world ────────────────────────────────────────────────────


def _iso(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


def comment(body: str, at: str, *, by: str | None = LINEAR_VIEWER) -> dict:
    return {"body": body, "createdAt": at, "user": {"id": by} if by else None}


class GateLinear:
    """api.linear.app for one card: its lane and its thread, which the
    lane moves and comments the scenarios make change. Every other URL — the
    runner's OIDC endpoint the door's client mints at — goes to the real
    urlopen."""

    def __init__(self, lane: str, thread: list[dict]):
        self.lane = lane
        self.thread = list(thread)  # oldest first
        self.real = urllib.request.urlopen
        self.sent: list[dict] = []

    def __call__(self, req, *a, **k):
        if not req.full_url.startswith(linear_ops.API):
            return self.real(req, *a, **k)
        payload = json.loads(req.data)
        return _Resp(self.answer(payload["query"], payload.get("variables") or {}))

    def answer(self, query: str, variables: dict) -> dict:
        field = linear_ops._mutation_field(query)
        if field == "commentCreate":
            self.thread.append(comment(variables["input"]["body"],
                                       _iso(datetime.now(UTC))))
            return {"data": {field: {"success": True}}}
        if field == "issueUpdate":
            state_id = (variables.get("input") or {}).get("stateId")
            self.lane = next(s["name"] for s in STATES if s["id"] == state_id)
            return {"data": {field: {"success": True}}}
        if field:
            return {"data": {field: {"success": True}}}
        if query.strip() == VIEWER_QUERY:
            return {"data": {"viewer": {"id": LINEAR_VIEWER}}}
        if "comments(" in query:
            return {"data": {"viewer": {"id": LINEAR_VIEWER}, "issue": {"comments": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": list(reversed(self.thread))}}}}
        if "history" in query:
            return {"data": {"issue": {"history": {"nodes": []}}}}
        if "workflowStates" in query:
            return {"data": {"workflowStates": {"nodes": STATES}}}
        state = next(s for s in STATES if s["name"] == self.lane)
        return {"data": {"issue": {
            "id": f"uuid-{CARD}", "identifier": CARD, "title": "a card",
            "team": {"id": "team-1"}, "state": dict(state),
            "labels": {"nodes": []}, "children": {"nodes": []}, "project": None}}}

    # What was sent, as the transport seam saw it.
    def queries(self) -> list[str]:
        return [s["query"] for s in self.sent]

    def thread_reads(self) -> list[dict]:
        return [s for s in self.sent if "comments(" in s["query"]]

    def before_first_write(self) -> list[dict]:
        out = []
        for s in self.sent:
            if linear_ops._mutation_field(s["query"]):
                break
            out.append(s)
        return out

    def hold_comments(self) -> list[dict]:
        return [s for s in self.sent if "commentCreate(" in s["query"]
                and "code-owner hold" in s["variables"]["input"]["body"]]


@pytest.fixture
def world(monkeypatch):
    """Set the card's lane and thread with `world(lane, thread)`; Linear is
    the stub, and every request is recorded at `_send` with its caller."""
    for name in DOOR_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.delenv(linear_ops.FALLBACK_ENV, raising=False)
    monkeypatch.delenv(linear_ops.HOME_ENV, raising=False)
    monkeypatch.delenv("AGENT_TASK_SNAPSHOT", raising=False)
    linear_ops.reset_pass_cache()
    made = {}

    def make(lane: str, thread: list[dict]) -> GateLinear:
        stub = GateLinear(lane, thread)
        monkeypatch.setattr(urllib.request, "urlopen", stub)
        real_send = linear_ops._send

        def spy(payload, landed=None, read=None):
            body = json.loads(payload)
            stub.sent.append({"query": body["query"],
                              "variables": body.get("variables") or {},
                              "caller": read[1] if read else None})
            return real_send(payload, landed, read)

        monkeypatch.setattr(linear_ops, "_send", spy)
        made["stub"] = stub
        return stub

    yield make
    linear_ops.reset_pass_cache()


def render_job_env(workflow: str, job: str, door_vars: dict) -> dict:
    """The job's `env:` as a run on that job would hold it: the door's
    variables from the caller (`door_vars`, `door_env`'s shape), the identity
    the fleet passes, and every other expression empty."""
    given = {
        DOOR_VARS["BUREAU_READ"]: door_vars["BUREAU_READ"],
        DOOR_VARS["BUREAU_READ_URL"]: door_vars["BUREAU_READ_URL"],
        DOOR_VARS["BUREAU_READ_AUDIENCE"]: door_vars["BUREAU_READ_AUDIENCE"],
        DOOR_VARS["BUREAU_PIPELINE_REF"]: door_vars.get("BUREAU_PIPELINE_REF", ""),
        "${{ inputs.linear_identity }}": "fleet",
    }
    out = {}
    for name, value in (_job(workflow, job).get("env") or {}).items():
        value = str(value)
        out[name] = given.get(value, "") if "${{" in value else value
    return out


@contextmanager
def job_running(monkeypatch, workflow: str, job: str, *, mode: str = "on",
                as_of: str | None = None, door_thread: list[dict] | None = None,
                lane: str = "Green Light"):
    """A door answering FRESH for the card, and this job's env pointed at it."""
    node = card(CARD, lane)
    node["comments"]["nodes"] = list(reversed(door_thread or []))
    with FakeIssuer() as issuer, FakeDoor({CARD: node}) as door:
        if as_of is not None:
            door.as_of = as_of
        door.routes["/workflow-states"] = (
            200, door.envelope(STATES, nodes_key="workflowStates"))
        caller = door_env(door_url=door.url, issuer=issuer, mode=mode)
        env = {**render_job_env(workflow, job, caller),
               **{k: v for k, v in caller.items() if k.startswith(("ACTIONS_", "GITHUB_"))}}
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        yield door


def card_reads(door: FakeDoor) -> list[dict]:
    return [r for r in door.asked("/cards") if r["query"].get("ids") == CARD]


def older_than_needed(err: str) -> list[str]:
    return [ln for ln in err.splitlines() if ln.startswith(bureau_read.FALLBACK_TAG)
            and "reason=older-than-needed" in ln]


# ── Merge Gate: release reads its thread off the door ───────────────────────


def test_release_reads_the_thread_off_the_door(world, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
    hold = [comment(HOLD, "2026-11-01T10:00:00.000Z")]
    linear = world("Green Light", hold)
    with job_running(monkeypatch, *GATE, door_thread=hold) as door:
        code_owner_hold.release(CARD, PR)
    assert linear.lane == "In Review"
    assert card_reads(door), "the thread was not asked of the door"
    assert linear.thread_reads() == []
    before = linear.before_first_write()
    assert before, "release sent no read before its first write"
    # The card reads the write path keeps on Linear, and the one viewer read
    # authorship is judged by (the door's viewer is the console's, DRE-8898).
    callers = [s["caller"] for s in before]
    assert set(callers) <= {"linear_ops.get_issue", "linear_ops.viewer_id"}, callers
    assert callers.count("linear_ops.viewer_id") == 1
    for s in before:
        assert "comments" not in s["query"]
        if s["caller"] == "linear_ops.get_issue":
            assert s["variables"] == {"id": CARD}
        else:
            assert s["query"].strip() == VIEWER_QUERY
    assert older_than_needed(capsys.readouterr().err) == []


def test_release_with_the_door_off_reads_the_thread_from_linear(world, monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
    hold = [comment(HOLD, "2026-11-01T10:00:00.000Z")]
    linear = world("Green Light", hold)
    with job_running(monkeypatch, *GATE, mode="off", door_thread=hold) as door:
        code_owner_hold.release(CARD, PR)
    assert linear.lane == "In Review"
    assert door.requests == []
    (read,) = linear.thread_reads()
    assert read["query"] == linear_ops._THREAD_QUERY
    assert read["caller"] == "linear_ops._fetch_thread"


# ── Merge Gate: the floor — the earlier gate run's marker is seen ───────────


def _start(monkeypatch) -> datetime:
    """The job's start, T, as the start note writes it."""
    t = datetime.now(UTC).replace(microsecond=0)
    monkeypatch.setenv(SEEN_AFTER, _iso(t))
    return t


def test_release_sees_the_earlier_runs_hold_past_a_door_behind_the_start(
        world, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
    t = _start(monkeypatch)
    linear = world("Green Light", [comment(HOLD, _iso(t - timedelta(seconds=10)))])
    with job_running(monkeypatch, *GATE, as_of=_iso(t - timedelta(seconds=20)),
                     door_thread=[]) as door:
        code_owner_hold.release(CARD, PR)
    assert card_reads(door)
    (read,) = linear.thread_reads()
    assert read["caller"] == "linear_ops._fetch_thread"
    assert linear.lane == "In Review"
    assert len(older_than_needed(capsys.readouterr().err)) == 1


def test_release_takes_a_door_answer_from_after_the_start(world, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
    t = _start(monkeypatch)
    hold = [comment(HOLD, _iso(t - timedelta(seconds=10)))]
    linear = world("Green Light", hold)
    with job_running(monkeypatch, *GATE, as_of=_iso(t + timedelta(seconds=30)),
                     door_thread=hold) as door:
        code_owner_hold.release(CARD, PR)
    assert card_reads(door)
    assert linear.thread_reads() == []
    assert linear.lane == "In Review"
    assert older_than_needed(capsys.readouterr().err) == []


def test_without_the_floor_a_door_behind_the_start_strands_the_card(world, monkeypatch):
    """Why the start note exists: the same stale answer, taken, reads as a
    card the gate never parked — and it stays in Green Light."""
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
    t = datetime.now(UTC).replace(microsecond=0)
    linear = world("Green Light", [comment(HOLD, _iso(t - timedelta(seconds=10)))])
    with job_running(monkeypatch, *GATE, as_of=_iso(t - timedelta(seconds=20)),
                     door_thread=[]):
        code_owner_hold.release(CARD, PR)
    assert linear.thread_reads() == []
    assert linear.lane == "Green Light"


def test_park_posts_no_second_hold_past_a_door_behind_the_start(world, monkeypatch,
                                                               capsys):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
    t = _start(monkeypatch)
    linear = world("In Review", [comment(HOLD, _iso(t - timedelta(seconds=10)))])
    with job_running(monkeypatch, *GATE, as_of=_iso(t - timedelta(seconds=20)),
                     door_thread=[], lane="In Review") as door:
        code_owner_hold.park(CARD, PR, "the sentence")
    assert card_reads(door)
    assert len(linear.thread_reads()) == 1
    assert linear.hold_comments() == []
    assert linear.lane == "Green Light"
    assert len(older_than_needed(capsys.readouterr().err)) == 1


def test_without_the_floor_park_would_post_the_hold_twice(world, monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
    t = datetime.now(UTC).replace(microsecond=0)
    linear = world("In Review", [comment(HOLD, _iso(t - timedelta(seconds=10)))])
    with job_running(monkeypatch, *GATE, as_of=_iso(t - timedelta(seconds=20)),
                     door_thread=[], lane="In Review"):
        code_owner_hold.park(CARD, PR, "the sentence")
    assert len(linear.hold_comments()) == 1


# ── Proof Task: the people on the card, off the door ────────────────────────


def test_spoken_thread_people_reads_the_thread_off_the_door(world, monkeypatch, capsys):
    thread = [comment("⏳ 1/5 plan", "2026-11-01T10:00:00.000Z"),
              comment("a person's note", "2026-11-01T10:01:00.000Z", by="someone")]
    linear = world("In Progress", thread)
    with job_running(monkeypatch, *PROOF, door_thread=thread, lane="In Progress") as door:
        assert spoken_thread.main(["people", CARD]) == 0
    out = capsys.readouterr().out
    assert "SPOKEN STATUS: read" in out
    assert card_reads(door)
    assert linear.thread_reads() == []
    # The one Linear request: who this key is, which the door never answers.
    assert [(s["query"].strip(), s["caller"]) for s in linear.sent] == [
        (VIEWER_QUERY, "linear_ops.viewer_id")]


def test_spoken_thread_people_with_the_door_off_reads_linear(world, monkeypatch):
    thread = [comment("⏳ 1/5 plan", "2026-11-01T10:00:00.000Z")]
    linear = world("In Progress", thread)
    with job_running(monkeypatch, *PROOF, mode="off", door_thread=thread,
                     lane="In Progress") as door:
        assert spoken_thread.main(["people", CARD]) == 0
    assert door.requests == []
    assert [s["query"] for s in linear.sent] == [linear_ops._THREAD_QUERY]


# ── Deliver Rescue: its comment's pre-read, off the door ────────────────────


def test_the_rescue_jobs_comment_asks_the_door_for_its_target(world, monkeypatch):
    linear = world("In Progress", [])
    with job_running(monkeypatch, *RESCUE, lane="In Progress") as door:
        linear_ops.cmd_comment(CARD, "the rescued work is up")
    assert card_reads(door)
    (only,) = linear.sent
    assert "commentCreate(" in only["query"]
    assert only["variables"]["input"]["issueId"] == f"uuid-{CARD}"
