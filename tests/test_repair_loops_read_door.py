"""Triage Repair, Alarm Repair and Hygiene read cards and threads through the door (DRE-8902).

The three scheduled loops read whole comment threads from Linear every run,
and the thread is what the read door serves whole. So:

1. Their jobs carry the door's mode — `BUREAU_READ`, `BUREAU_READ_URL`,
   `BUREAU_READ_AUDIENCE`, set as `reconcile.yml` sets them — and
   `BUREAU_READ_CARD_MAX_AGE: 300`, which opts their card reads in
   (`linear_ops.card_via_door`, DRE-8898). They are standalone workflows, so
   no `BUREAU_PIPELINE_REF`, and each job can mint the OIDC token.
2. A repair reads its own claim. `apply` takes a door answer only when its
   `as_of` is at or after the claim `claim` posted (the job's own-write
   record), and `claim` only when it is at or after the job's start
   (`BUREAU_READ_CARD_SEEN_AFTER`, written by the job's first step), so a
   re-run still finds the claim an earlier attempt posted.
3. `alarm_repair.read_card` asks the door for the card first.

No test reaches Linear or the console. Linear is `LinearBoard`: a urlopen
stand-in that answers api.linear.app from a board kept in a JSON file, so two
processes — `claim`, then `apply` — share it the way two steps share Linear.
The door and its token issuer are `tests/bureau_read_fakes.py` on 127.0.0.1,
serving a snapshot of the same board as of the door's `as_of`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(TESTS))

import alarm_repair  # noqa: E402
import bureau_read  # noqa: E402
import hold  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import triage_repair  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer  # noqa: E402

_REAL_URLOPEN = urllib.request.urlopen

#: Who Linear says this process's key is: every receipt the repair posts is
#: this user's, and `comment_records` reads exactly these as the pipeline's.
PIPELINE_USER = "pipeline-user"
PERSON_USER = "a-person"
AUDIENCE = "https://app.agent-bureau.com/pipeline-read"
SHA = "a" * 40
LONG_AGO = "2026-09-01T00:00:00.000Z"
#: Where the three workflows run, and so the caller the door answers.
REPOSITORY = "dreadnought-foundry/bureau-pipeline"

#: The jobs this card moves onto the door, by workflow file.
DOOR_JOBS = {
    "triage-repair.yml": ("select", "repair", "unrepaired"),
    "alarm-repair.yml": ("select", "repair"),
    "hygiene.yml": ("read", "keep", "summary"),
}
#: The door's variables, exactly as reconcile.yml sets them.
DOOR_VARS = {
    "BUREAU_READ": "${{ vars.BUREAU_READ || 'off' }}",
    "BUREAU_READ_URL": "${{ vars.BUREAU_READ_URL }}",
    "BUREAU_READ_AUDIENCE": "${{ vars.BUREAU_READ_AUDIENCE }}",
}
#: The jobs that open with the step that notes their start.
SEEN_AFTER_JOBS = {
    "triage-repair.yml": ("repair", "unrepaired"),
    "alarm-repair.yml": ("repair",),
}
SEEN_AFTER_STEP = "Note the job's start for the read door"
#: Every permission each block named before this card.
PERMISSIONS_BEFORE = {
    ("triage-repair.yml", None): {"contents": "read"},
    ("triage-repair.yml", "unrepaired"): {"contents": "read", "id-token": "write"},
    ("alarm-repair.yml", None): {"contents": "read"},
    ("alarm-repair.yml", "select"): {"contents": "read", "id-token": "write"},
    ("alarm-repair.yml", "repair"): {"contents": "read", "id-token": "write"},
    ("hygiene.yml", None): {"contents": "read", "actions": "read"},
}


def _doc(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _job(name: str, job: str) -> dict:
    return _doc(name)["jobs"][job]


# --------------------------------------------------------------------------- #
# 1. the door's mode, and the token                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name,job", [(n, j) for n, jobs in DOOR_JOBS.items() for j in jobs])
def test_each_job_carries_the_doors_mode_and_the_card_max_age(name, job):
    env = _job(name, job).get("env") or {}
    for key, value in DOOR_VARS.items():
        assert env.get(key) == value, (name, job, key)
    assert str(env.get(linear_ops.CARD_MAX_AGE_ENV)) == "300", (name, job)


@pytest.mark.parametrize("name", sorted(DOOR_JOBS))
def test_no_env_block_names_a_pipeline_ref(name):
    """A standalone workflow checks out no `pipeline_ref`, so the client makes
    no ref check (`bureau_read.pipeline_ref_problem`)."""
    doc = _doc(name)
    assert bureau_read.PIPELINE_REF_ENV not in (doc.get("env") or {})
    for job_id, job in doc["jobs"].items():
        assert bureau_read.PIPELINE_REF_ENV not in (job.get("env") or {}), job_id
        for step in job.get("steps") or []:
            assert bureau_read.PIPELINE_REF_ENV not in (step.get("env") or {}), step.get("name")


@pytest.mark.parametrize("name,job", [(n, j) for n, jobs in DOOR_JOBS.items() for j in jobs])
def test_each_job_can_mint_the_oidc_token(name, job):
    doc = _doc(name)
    block = _job(name, job).get("permissions", doc.get("permissions"))
    assert (block or {}).get("id-token") == "write", (name, job)


@pytest.mark.parametrize("where", sorted(PERMISSIONS_BEFORE, key=str))
def test_every_permission_block_still_names_what_it_named(where):
    name, job = where
    doc = _doc(name)
    block = doc["permissions"] if job is None else doc["jobs"][job]["permissions"]
    for permission, level in PERMISSIONS_BEFORE[where].items():
        assert block.get(permission) == level, (where, permission)


def test_the_new_job_blocks_restate_the_workflows_read():
    for job in ("select", "repair"):
        assert _job("triage-repair.yml", job)["permissions"] == {
            "contents": "read", "id-token": "write"}, job
    assert _doc("hygiene.yml")["permissions"] == {
        "contents": "read", "actions": "read", "id-token": "write"}
    for job_id, job in _doc("hygiene.yml")["jobs"].items():
        assert "permissions" not in job, f"{job_id} would replace the workflow's grant"


# --------------------------------------------------------------------------- #
# 2. the job's start, for the read door                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name,job", [(n, j) for n, jobs in SEEN_AFTER_JOBS.items()
                                      for j in jobs])
def test_the_job_opens_by_noting_its_start(name, job, tmp_path):
    first = _job(name, job)["steps"][0]
    assert first.get("name") == SEEN_AFTER_STEP
    assert "${{" not in first["run"]
    github_env = tmp_path / "github-env"
    github_env.write_text("")
    before = datetime.now(UTC).replace(microsecond=0)
    proc = subprocess.run(["bash", "-e", "-c", first["run"]], capture_output=True, text=True,
                          env={**os.environ, "GITHUB_ENV": str(github_env)}, timeout=30)
    after = datetime.now(UTC)
    assert proc.returncode == 0, proc.stderr
    (line,) = github_env.read_text().splitlines()
    key, _, value = line.partition("=")
    assert key == linear_ops.CARD_SEEN_AFTER_ENV
    assert len(value) == len("2026-11-03T18:00:00Z") and value.endswith("Z"), value
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert before <= at <= after


@pytest.mark.parametrize("name", sorted(DOOR_JOBS))
def test_no_env_block_names_the_seen_after_variable(name):
    """The step writes it to `$GITHUB_ENV`; an `env:` block naming it would
    override the job's start for that step."""
    doc = _doc(name)
    assert linear_ops.CARD_SEEN_AFTER_ENV not in (doc.get("env") or {})
    for job_id, job in doc["jobs"].items():
        assert linear_ops.CARD_SEEN_AFTER_ENV not in (job.get("env") or {}), job_id
        for step in job.get("steps") or []:
            assert linear_ops.CARD_SEEN_AFTER_ENV not in (step.get("env") or {}), step.get("name")


# --------------------------------------------------------------------------- #
# the Linear board, at the transport seam                                      #
# --------------------------------------------------------------------------- #


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class _Resp:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode()
        self.headers = {"x-ratelimit-requests-remaining": "100"}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class LinearBoard:
    """api.linear.app over a board kept in one JSON file: each card's lane,
    labels, description, parent, children, blockers and comments (oldest
    first, each `{"body", "createdAt", "user"}`). A `commentCreate` appends to
    it at the real clock, as Linear does, and every query sent is appended to
    `sent`. Every other URL — the token issuer, the door — goes to the real
    urlopen."""

    def __init__(self, path):
        self.path = Path(path)

    # --- the board --------------------------------------------------------

    def load(self) -> dict:
        return json.loads(self.path.read_text())

    def save(self, state: dict) -> None:
        self.path.write_text(json.dumps(state))

    @classmethod
    def create(cls, path) -> "LinearBoard":
        board = cls(path)
        board.save({"viewer": PIPELINE_USER, "cards": {}, "sent": []})
        return board

    def add(self, ident: str, *, lane: str = "Triage", labels=(), description: str = "",
            comments=(), parent: str | None = None, children=(), blockers=(),
            relations_partial: bool = False) -> None:
        """`comments` are `(body, createdAt)` or `(body, createdAt, user)`,
        oldest first; `blockers` are `(identifier, lane)`."""
        state = self.load()
        state["cards"][ident] = {
            "lane": lane, "labels": list(labels), "description": description,
            "parent": parent, "children": list(children),
            "blockers": [list(b) for b in blockers], "relations_partial": relations_partial,
            "comments": [{"body": c[0], "createdAt": c[1],
                          "user": c[2] if len(c) > 2 else PIPELINE_USER} for c in comments],
        }
        self.save(state)

    def say(self, ident: str, body: str, at: str, user: str = PIPELINE_USER) -> None:
        state = self.load()
        state["cards"][ident]["comments"].append({"body": body, "createdAt": at, "user": user})
        self.save(state)

    def bodies(self, ident: str) -> list[str]:
        return [c["body"] for c in self.load()["cards"][ident]["comments"]]

    def sent(self) -> list[str]:
        return list(self.load()["sent"])

    def clear_sent(self) -> None:
        state = self.load()
        state["sent"] = []
        self.save(state)

    def comments_posted(self) -> list[str]:
        return [q for q in self.sent() if "commentCreate(" in q]

    def thread_reads(self) -> list[str]:
        """One card's comment reads; the lane read's window is not one."""
        return [q for q in self.sent() if "comments(" in q and "issues(" not in q]

    # --- the node shapes --------------------------------------------------

    @staticmethod
    def _parent(state: dict, ident: str | None) -> dict | None:
        if not ident:
            return None
        lane = (state["cards"].get(ident) or {}).get("lane", "In Progress")
        return {"identifier": ident, "state": {"name": lane}}

    def linear_node(self, state: dict, ident: str) -> dict:
        """The card the way Linear answers any query that selects it."""
        c = state["cards"][ident]
        return {
            "id": f"uuid-{ident}", "identifier": ident, "title": f"Card {ident}",
            "description": c["description"], "createdAt": LONG_AGO, "updatedAt": LONG_AGO,
            "priority": 0, "team": {"id": "team-1"}, "project": None,
            "state": {"id": f"state-{c['lane']}", "name": c["lane"], "type": "started"},
            "labels": {"nodes": [{"id": f"label-{n}", "name": n} for n in c["labels"]]},
            "parent": self._parent(state, c["parent"]),
            "children": {"nodes": [{"id": f"uuid-{k}", "identifier": k, "createdAt": LONG_AGO,
                                    "state": {"name": "Backlog"}} for k in c["children"]]},
            "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                         "nodes": [{"body": m["body"], "createdAt": m["createdAt"],
                                    "user": {"id": m["user"]}}
                                   for m in reversed(c["comments"])]},
            "relations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            "inverseRelations": {
                "pageInfo": {"hasNextPage": c["relations_partial"], "endCursor": None},
                "nodes": [{"type": "blocks", "issue": {"identifier": b, "state": {"name": s}}}
                          for b, s in c["blockers"]]},
            "history": {"nodes": [{"createdAt": LONG_AGO, "toState": {"name": c["lane"]}}]},
        }

    def door_world(self, upto: datetime | None = None) -> dict:
        """Every card as the door serves it (CARD_FIELDS + inverseRelations),
        holding only the comments made by `upto`: the door's record trails
        real time."""
        state = self.load()
        world = {}
        for ident in state["cards"]:
            node = self.linear_node(state, ident)
            for key in ("team", "project", "relations", "history"):
                node.pop(key)
            node["state"] = {"name": node["state"]["name"]}
            node["labels"] = {"nodes": [{"name": n["name"]} for n in node["labels"]["nodes"]]}
            node["children"] = {"nodes": [{"id": k["id"]} for k in node["children"]["nodes"]]}
            node["comments"]["nodes"] = [m for m in node["comments"]["nodes"]
                                         if upto is None or _instant(m["createdAt"]) <= upto]
            world[ident] = node
        return world

    # --- the transport ----------------------------------------------------

    def urlopen(self, req, *a, **k):
        if not req.full_url.startswith(linear_ops.API):
            return _REAL_URLOPEN(req, *a, **k)
        payload = json.loads(req.data)
        query, variables = payload["query"], payload.get("variables") or {}
        state = self.load()
        state["sent"].append(query)
        body = self.answer(state, query, variables)
        self.save(state)
        return _Resp(body)

    def answer(self, state: dict, query: str, variables: dict) -> dict:
        field = linear_ops._mutation_field(query)
        if field:
            if field == "commentCreate":
                issue_id = variables["input"]["issueId"]
                ident = issue_id.removeprefix("uuid-")
                state["cards"][ident]["comments"].append({
                    "body": variables["input"]["body"],
                    "createdAt": _iso(datetime.now(UTC)), "user": state["viewer"]})
            return {"data": {field: {"success": True}}}
        if query.strip() == "query { viewer { id } }":
            return {"data": {"viewer": {"id": state["viewer"]}}}
        if "issues(" in query:
            lanes = set(variables.get("states") or ())
            return {"data": {"issues": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [self.linear_node(state, i) for i, c in state["cards"].items()
                          if c["lane"] in lanes]}}}
        if "$after" in query and "comments(" in query:
            return {"data": {"issue": {"comments": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}
        ident = variables.get("id")
        if ident not in state["cards"]:
            return {"data": {"viewer": {"id": state["viewer"]}, "issue": None}}
        return {"data": {"viewer": {"id": state["viewer"]},
                         "issue": self.linear_node(state, ident)}}


# --------------------------------------------------------------------------- #
# a step, as a process                                                         #
# --------------------------------------------------------------------------- #

_STEP = r'''
import os, runpy, sys, urllib.request
tests, scripts, script = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path[:0] = [scripts, tests]
from test_repair_loops_read_door import LinearBoard
urllib.request.urlopen = LinearBoard(os.environ["LINEAR_BOARD"]).urlopen
sys.argv = [os.path.join(scripts, script), *sys.argv[4:]]
runpy.run_path(os.path.join(scripts, script), run_name="__main__")
'''

#: What the runner's own environment may carry that a scenario sets itself.
_SCRUBBED = ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE", "BUREAU_PIPELINE_REF",
             "BUREAU_READ_CARD_MAX_AGE", "BUREAU_READ_CARD_SEEN_AFTER",
             "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
             "GITHUB_EVENT_NAME", "GITHUB_HEAD_REF", "GITHUB_REPOSITORY", "GITHUB_WORKFLOW",
             "GITHUB_JOB", "LINEAR_API_KEY_FALLBACK", "LINEAR_KEY_HOME",
             "BUREAU_LINEAR_READ_ONLY", "RUNNER_TEMP")


def job_door_env(name: str, job: str, door: FakeDoor, issuer: FakeIssuer) -> dict:
    """The job's own `env:` door variables, the repository variables
    resolved to this door — so a scenario runs with what the YAML sets."""
    resolved = {"${{ vars.BUREAU_READ || 'off' }}": "on",
                "${{ vars.BUREAU_READ_URL }}": door.url,
                "${{ vars.BUREAU_READ_AUDIENCE }}": AUDIENCE}
    env = {}
    for key, value in (_job(name, job).get("env") or {}).items():
        if key.startswith("BUREAU_READ"):
            env[key] = resolved.get(str(value), str(value))
    assert set(env) == {*DOOR_VARS, linear_ops.CARD_MAX_AGE_ENV}, env
    env.update(ACTIONS_ID_TOKEN_REQUEST_URL=issuer.token_url,
               ACTIONS_ID_TOKEN_REQUEST_TOKEN=issuer.request_token,
               GITHUB_REPOSITORY=REPOSITORY,
               GITHUB_WORKFLOW=_doc(name)["name"], GITHUB_JOB=job)
    return env


def step(board: LinearBoard, env_over: dict, script: str, *args: str):
    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED}
    env.update(LINEAR_API_KEY="test-key", LINEAR_IDENTITY="fleet",
               LINEAR_BOARD=str(board.path))
    env.update(env_over)
    return subprocess.run([sys.executable, "-c", _STEP, str(TESTS), str(ROOT / "scripts"),
                           script, *args],
                          capture_output=True, text=True, env=env, timeout=120)


def fallback_lines(err: str) -> list[str]:
    return [ln for ln in err.splitlines() if ln.startswith(bureau_read.FALLBACK_TAG)]


def callers(err: str) -> dict[str, int]:
    """The process's Linear reads, counted by the `caller=` its
    `linear-read:` lines name."""
    out: dict[str, int] = {}
    for line in err.splitlines():
        if line.startswith(linear_ops.READ_TAG):
            fields = dict(p.split("=", 1) for p in line.split(" ") if "=" in p
                          and not p.startswith("reader="))
            out[fields["caller"]] = out.get(fields["caller"], 0) + int(fields["count"])
    return out


def own_write(runner_temp: Path, ident: str) -> datetime:
    """The instant the job's own-write record holds for `ident`'s newest write."""
    lines = [json.loads(ln) for ln in
             (runner_temp / linear_ops.OWN_WRITES_FILE).read_text().splitlines()]
    return max(_instant(ln["at"]) for ln in lines if ln["card"] == ident)


@pytest.fixture
def board(tmp_path):
    return LinearBoard.create(tmp_path / "linear-board.json")


@pytest.fixture
def runner_temp(tmp_path):
    path = tmp_path / "runner-temp"
    path.mkdir()
    return path


# --------------------------------------------------------------------------- #
# Triage Repair: the `none` path finds its own claim                           #
# --------------------------------------------------------------------------- #

UNFIXABLE = hold.stamp_line("unfixable-check", SHA, ".github/workflows/agent-fix.yml")


def _triage_card(board: LinearBoard, ident: str = "DRE-7") -> None:
    board.add(ident, lane="Triage", labels=("needs-human", "repo:agent-bureau"),
              comments=[(UNFIXABLE, "2026-09-01T00:00:01.000Z")])


def _claim_receipt_of(bodies: list[str]) -> list[str]:
    return [b for b in bodies if b.startswith(triage_repair.RECEIPT_KEY)]


def _claim_then(board, runner_temp, door, issuer, job_start: datetime):
    """`claim DRE-7` with the `repair` job's env, the door's record current
    from the job's start; the instant its claim landed."""
    env = {**job_door_env("triage-repair.yml", "repair", door, issuer),
           "RUNNER_TEMP": str(runner_temp),
           linear_ops.CARD_SEEN_AFTER_ENV: job_start.strftime("%Y-%m-%dT%H:%M:%SZ")}
    door.world = board.door_world()
    door.as_of = _iso(datetime.now(UTC))
    proc = step(board, env, "triage_repair.py", "claim", "DRE-7")
    assert proc.returncode == triage_repair.EXIT_OK, proc.stderr
    (started,) = _claim_receipt_of(board.bodies("DRE-7"))
    assert triage_repair.outcome_of(started) == triage_repair.STARTED
    return env, own_write(runner_temp, "DRE-7")


def test_triage_apply_reads_linear_when_the_door_is_older_than_the_claim(
        board, runner_temp):
    _triage_card(board)
    job_start = datetime.now(UTC) - timedelta(seconds=30)
    with FakeIssuer() as issuer, FakeDoor({}, repository=REPOSITORY) as door:
        env, at = _claim_then(board, runner_temp, door, issuer, job_start)
        door.world = board.door_world(upto=at - timedelta(seconds=20))
        door.as_of = _iso(at - timedelta(seconds=20))
        assert not _claim_receipt_of(
            [m["body"] for m in reversed(door.world["DRE-7"]["comments"]["nodes"])])
        board.clear_sent()
        proc = step(board, env, "triage_repair.py", "apply", "DRE-7", "--action", "none")
    assert proc.returncode == triage_repair.EXIT_OK, proc.stderr
    assert "no claim stands" not in proc.stderr
    assert f"→ {triage_repair.UNREPAIRABLE}" in proc.stdout
    receipts = _claim_receipt_of(board.bodies("DRE-7"))
    assert [triage_repair.outcome_of(r) for r in receipts] == [
        triage_repair.STARTED, triage_repair.UNREPAIRABLE]
    assert len(board.thread_reads()) >= 1
    assert callers(proc.stderr).get("linear_ops._fetch_thread") == 1
    (line,) = fallback_lines(proc.stderr)
    assert "read=cards reason=older-than-needed" in line


def test_triage_apply_reads_the_door_when_its_record_holds_the_claim(board, runner_temp):
    _triage_card(board)
    job_start = datetime.now(UTC) - timedelta(seconds=30)
    with FakeIssuer() as issuer, FakeDoor({}, repository=REPOSITORY) as door:
        env, at = _claim_then(board, runner_temp, door, issuer, job_start)
        door.world = board.door_world()
        door.as_of = _iso(at + timedelta(seconds=2))
        board.clear_sent()
        door.requests.clear()
        proc = step(board, env, "triage_repair.py", "apply", "DRE-7", "--action", "none")
        asked = [r["query"] for r in door.asked("/cards")]
    assert proc.returncode == triage_repair.EXIT_OK, proc.stderr
    assert f"→ {triage_repair.UNREPAIRABLE}" in proc.stdout
    assert board.thread_reads() == []
    assert "linear_ops._fetch_thread" not in callers(proc.stderr)
    assert fallback_lines(proc.stderr) == []
    assert {"ids": "DRE-7", "comments": "all", "relations": "0"} in asked


def test_a_rerun_finds_the_earlier_attempts_claim(board, runner_temp):
    """The earlier attempt's claim was posted before this job began (the
    runs are serialized), and the door's record is older still."""
    _triage_card(board)
    job_start = datetime.now(UTC).replace(microsecond=0)
    board.say("DRE-7", pipeline_act.receipt(
        "triage-repair", triage_repair.started_body("The card as it stood.")),
        _iso(job_start - timedelta(seconds=60)))
    with FakeIssuer() as issuer, FakeDoor({}, repository=REPOSITORY) as door:
        door.world = board.door_world(upto=job_start - timedelta(seconds=90))
        door.as_of = _iso(job_start - timedelta(seconds=90))
        env = {**job_door_env("triage-repair.yml", "repair", door, issuer),
               "RUNNER_TEMP": str(runner_temp),
               linear_ops.CARD_SEEN_AFTER_ENV: job_start.strftime("%Y-%m-%dT%H:%M:%SZ")}
        board.clear_sent()
        proc = step(board, env, "triage_repair.py", "claim", "DRE-7")
    assert proc.returncode == triage_repair.EXIT_SPENT, proc.stderr
    assert board.comments_posted() == []
    assert len(_claim_receipt_of(board.bodies("DRE-7"))) == 1
    assert callers(proc.stderr).get("linear_ops._fetch_thread") == 1
    (line,) = fallback_lines(proc.stderr)
    assert "read=cards reason=older-than-needed" in line


# --------------------------------------------------------------------------- #
# Triage Repair's `select`: the threads off the door, the lane off Linear      #
# --------------------------------------------------------------------------- #


def test_select_reads_each_thread_off_the_door_and_the_lane_off_linear(board, tmp_path):
    for ident in ("DRE-7", "DRE-8"):
        _triage_card(board, ident)
    out = tmp_path / "cards.json"
    with FakeIssuer() as issuer, FakeDoor({}, repository=REPOSITORY) as door:
        door.world = board.door_world()
        door.as_of = _iso(datetime.now(UTC))
        env = job_door_env("triage-repair.yml", "select", door, issuer)
        proc = step(board, env, "triage_repair.py", "select", "--out", str(out))
        asked = sorted(r["query"]["ids"] for r in door.asked("/cards")
                       if r["query"].get("comments") == "all")
    assert proc.returncode == 0, proc.stderr
    assert sorted(r["card"] for r in json.loads(out.read_text())) == ["DRE-7", "DRE-8"]
    assert asked == ["DRE-7", "DRE-8"]
    assert board.thread_reads() == []
    by_caller = callers(proc.stderr)
    assert "linear_ops._fetch_thread" not in by_caller, by_caller
    assert by_caller.get("board_snapshot.read_board") == 1, by_caller
    assert fallback_lines(proc.stderr) == []


# --------------------------------------------------------------------------- #
# Alarm Repair: claim, then apply, with the door older than the claim          #
# --------------------------------------------------------------------------- #

KIND = "prose-blocker:DRE-7"


def _alerts(raised: str) -> tuple:
    return 200, {"schema": bureau_read.SCHEMA, "read_at": _iso(datetime.now(UTC)),
                 "alerts": [{"kind": KIND, "severity": "WARN", "title": "the alert",
                             "raised_at": raised, "card": "DRE-7"}]}


def test_alarm_apply_reads_linear_when_the_door_is_older_than_the_claim(
        board, runner_temp, tmp_path):
    board.add("DRE-7", lane="Queued", labels=("repo:agent-bureau",),
              description="**Blocked by:** DRE-9\n")
    raised = _iso(datetime.now(UTC) - timedelta(hours=3))
    decision_dir = tmp_path / "decision"
    decision_dir.mkdir()
    (decision_dir / alarm_repair.DECISION_FILE).write_text(json.dumps(
        {"action": "none", "move": "operator", "why": "the author must reword it"}))
    job_start = datetime.now(UTC) - timedelta(seconds=30)
    with FakeIssuer() as issuer, FakeDoor({}, repository=REPOSITORY) as door:
        door.routes["/alerts"] = _alerts(raised)
        env = {**job_door_env("alarm-repair.yml", "repair", door, issuer),
               "RUNNER_TEMP": str(runner_temp),
               linear_ops.CARD_SEEN_AFTER_ENV: job_start.strftime("%Y-%m-%dT%H:%M:%SZ")}
        door.world = board.door_world()
        door.as_of = _iso(datetime.now(UTC))
        proc = step(board, env, "alarm_repair.py", "claim", "DRE-7",
                    "--alert", KIND, "--raised", raised)
        assert proc.returncode == alarm_repair.EXIT_OK, proc.stderr
        at = own_write(runner_temp, "DRE-7")
        door.world = board.door_world(upto=at - timedelta(seconds=20))
        door.as_of = _iso(at - timedelta(seconds=20))
        board.clear_sent()
        proc = step(board, env, "alarm_repair.py", "apply", "DRE-7", "--alert", KIND,
                    "--raised", raised, "--dir", str(decision_dir))
    assert proc.returncode == alarm_repair.EXIT_OK, proc.stderr
    assert "no claim stands" not in proc.stderr
    outcomes = [alarm_repair.parse_receipt({"body": b, "authored_by_pipeline": True})
                for b in board.bodies("DRE-7")]
    assert [o["outcome"] for o in outcomes if o] == [alarm_repair.STARTED,
                                                     alarm_repair.DIAGNOSED]
    by_caller = callers(proc.stderr)
    assert by_caller.get("linear_ops._fetch_thread") == 1, by_caller
    lines = fallback_lines(proc.stderr)
    assert lines and all("read=cards reason=older-than-needed" in ln for ln in lines), lines


# --------------------------------------------------------------------------- #
# 3. Alarm Repair's card read asks the door                                    #
# --------------------------------------------------------------------------- #

#: One alarm class per relation and parent case: `(lane, description, parent,
#: blockers, children, partial)` and the classes Linear's answer decides.
CARD_CASES = {
    "prose-blocker": ("Queued", "**Blocked by:** DRE-9\n", None, (), (), False),
    "parent-is-no-claim": ("Queued", "**Blocked by:** DRE-3\n", "DRE-3", (), (), False),
    "terminal-blockers": ("Queued", "", None, (("DRE-9", "Done"),), (), False),
    "open-blocker": ("Queued", "", None, (("DRE-9", "In Progress"),), (), False),
    "epic": ("Queued", "", None, (("DRE-9", "Done"),), ("DRE-11",), False),
    "relations-partial": ("Queued", "**Blocked by:** DRE-9\n", None,
                          (("DRE-9", "Done"),), (), True),
}


@pytest.fixture
def linear(board, monkeypatch):
    for name in _SCRUBBED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, "fleet")
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    linear_ops.reset_pass_cache()
    monkeypatch.setattr(urllib.request, "urlopen", board.urlopen)
    yield board
    linear_ops.reset_pass_cache()


@pytest.fixture
def door(linear, monkeypatch):
    with FakeIssuer() as issuer, FakeDoor({}, repository=REPOSITORY) as d:
        for key, value in job_door_env("alarm-repair.yml", "select", d, issuer).items():
            monkeypatch.setenv(key, value)
        d.as_of = _iso(datetime.now(UTC))
        yield d


def _case(board: LinearBoard, case: str, ident: str = "DRE-7") -> None:
    lane, description, parent, blockers, children, partial = CARD_CASES[case]
    if parent:
        board.add(parent, lane="In Progress")
    board.add(ident, lane=lane, labels=("repo:agent-bureau",), description=description,
              parent=parent, blockers=blockers, children=children,
              relations_partial=partial)


def _decided(issue: dict) -> dict:
    alert = {"kind": KIND, "severity": "WARN", "title": "t", "raised_at": LONG_AGO}
    row = alarm_repair._record(alert, "DRE-7", alarm_repair.REPAIR, "why", issue, [])
    return {k: row[k] for k in ("classes", "undeclared", "blockers", "repo")}


@pytest.mark.parametrize("case", sorted(CARD_CASES))
def test_read_card_off_the_door_decides_as_linears_answer_does(case, door, linear):
    _case(linear, case)
    from_linear = alarm_repair.linear_ops.gql(alarm_repair._CARD_QUERY,
                                              {"id": "DRE-7"})["issue"]
    door.world = linear.door_world()
    linear.clear_sent()
    issue = alarm_repair.read_card("DRE-7")
    assert linear.sent() == []
    (asked,) = door.asked("/cards")
    assert asked["query"] == {"ids": "DRE-7", "comments": "50", "relations": "1"}
    assert _decided(issue) == _decided(from_linear)
    assert alarm_repair._is_epic(issue) == alarm_repair._is_epic(from_linear)
    assert alarm_repair.blockers_terminal(issue) == alarm_repair.blockers_terminal(from_linear)


def test_the_cases_cover_each_class():
    seen = set()
    for case in CARD_CASES:
        lane, description, parent, blockers, children, partial = CARD_CASES[case]
        issue = {"identifier": "DRE-7", "description": description,
                 "state": {"name": lane},
                 "parent": {"identifier": parent} if parent else None,
                 "children": {"nodes": [{"id": k} for k in children]},
                 "inverseRelations": {"pageInfo": {"hasNextPage": partial}, "nodes": [
                     {"type": "blocks", "issue": {"identifier": b, "state": {"name": s}}}
                     for b, s in blockers]}}
        seen.update(alarm_repair.classes_of("x", issue))
    assert {alarm_repair.PROSE_BLOCKER, alarm_repair.QUEUED_TERMINAL_BLOCKERS,
            alarm_repair.OTHER} <= seen


def test_an_unknown_door_reads_linear_once_and_says_so(door, linear, capsys):
    _case(linear, "prose-blocker")
    door.world = linear.door_world()
    door.routes["/cards"] = door.unknown("stale")
    issue = alarm_repair.read_card("DRE-7")
    assert issue["identifier"] == "DRE-7"
    assert linear.sent() == [alarm_repair._CARD_QUERY]
    (line,) = fallback_lines(capsys.readouterr().err)
    assert "read=cards reason=stale" in line


def test_a_card_neither_source_holds_still_raises(door, linear):
    door.routes["/cards"] = door.unknown("stale")
    with pytest.raises(linear_ops.LinearError):
        alarm_repair.read_card("DRE-404")


def test_a_card_the_door_does_not_serve_reads_linear_and_the_door_stays(door, linear, capsys):
    _case(linear, "prose-blocker", "DRE-7")
    _case(linear, "terminal-blockers", "DRE-8")
    door.world = {k: v for k, v in linear.door_world().items() if k == "DRE-8"}
    linear.clear_sent()
    issue = alarm_repair.read_card("DRE-7")
    assert issue["identifier"] == "DRE-7"
    assert linear.sent() == [alarm_repair._CARD_QUERY]
    (line,) = fallback_lines(capsys.readouterr().err)
    assert "read=cards reason=not-found" in line
    linear.clear_sent()
    other = alarm_repair.read_card("DRE-8")
    assert other["identifier"] == "DRE-8"
    assert linear.sent() == []
    assert [r["query"]["ids"] for r in door.asked("/cards")] == ["DRE-7", "DRE-8"]
    assert fallback_lines(capsys.readouterr().err) == []
