"""Agent Task, the Medic and Agent Fix read their card through the door (DRE-8900).

Every job of the three that reads Linear carries the door's four variables and
a `BUREAU_READ_CARD_MAX_AGE`: 60 on Agent Task, 300 on the Medic and Agent
Fix. A card read in those jobs then asks the read door first, through
`linear_ops.card_via_door` (DRE-8898), and takes its answer only when the
door's `as_of` is at or after the card's floor:

* in a Medic job, the failed run's end (`BUREAU_READ_CARD_SEEN_AFTER` is
  `github.event.workflow_run.updated_at`), so the retry gate sees the park the
  failed run posted seconds before it ended;
* in Agent Task's `execute` and Agent Fix's `fix`, the job's own start, which
  the job's first step writes to `$GITHUB_ENV`, so a guard sees every receipt
  an earlier run posted — and every write this job made, off the own-write
  record.

The scenario tests here render each job's `env:` out of the workflow YAML and
run the step's own `linear_ops.py` command against it, so a job that lost its
variables reads Linear and the test goes red. Linear is `LinearCard`, a
urlopen stand-in for one card kept in a JSON file (two processes share it);
the door and its token issuer are `tests/bureau_read_fakes.py` on 127.0.0.1.
`tests/test_medic_card_facts_door.py` and `tests/test_hold_read_door.py`
import the harness from here.
"""

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
import linear_ops  # noqa: E402
import medic_retry  # noqa: E402
import stream_watchdog  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, door_env  # noqa: E402
from bureau_read_fakes import card as door_shape  # noqa: E402

CARD = "DRE-7"
#: Who Linear says this process's key is.
VIEWER = "linear-viewer"

#: The door's four variables, exactly as reconcile.yml sets them.
DOOR_VARIABLES = {
    "BUREAU_READ": "${{ vars.BUREAU_READ || 'off' }}",
    "BUREAU_READ_URL": "${{ vars.BUREAU_READ_URL }}",
    "BUREAU_READ_AUDIENCE": "${{ vars.BUREAU_READ_AUDIENCE }}",
    "BUREAU_PIPELINE_REF": "${{ inputs.pipeline_ref }}",
}
#: The failed run's end, the floor of every Medic job.
WORKFLOW_RUN_END = "${{ github.event.workflow_run.updated_at }}"
#: The jobs that read Linear, and the max-age each carries.
MEDIC_JOBS = ("classify", "retry_declined", "stall_record", "backoff",
              "environment_hold", "diagnose")
JOBS = {("agent-task.yml", "execute"): "60", ("agent-fix.yml", "fix"): "300",
        **{("medic.yml", job): "300" for job in MEDIC_JOBS}}
#: The jobs whose floor is their own start, and the step that writes it.
START_STEP = "Note the job's start for the read door"
START_JOBS = (("agent-task.yml", "execute"), ("agent-fix.yml", "fix"))
_SECOND = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


# ── time ────────────────────────────────────────────────────────────────────


def iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def at(base: str, seconds: float) -> str:
    """`base` moved by `seconds`, ISO."""
    return iso(instant(base) + timedelta(seconds=seconds))


# ── the workflows ───────────────────────────────────────────────────────────


def workflow(name: str) -> dict:
    with open(WORKFLOWS / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def job(name: str, job_id: str) -> dict:
    return workflow(name)["jobs"][job_id]


def step(name: str, job_id: str, step_name: str) -> dict:
    (found,) = [s for s in job(name, job_id)["steps"] if s.get("name") == step_name]
    return found


def rendered_job_env(name: str, job_id: str, *, door: FakeDoor, issuer: FakeIssuer,
                     run_end: str = "") -> dict:
    """The door's part of a job's `env:`, as GitHub renders it for a caller
    whose `BUREAU_READ` is `on`, plus what the runner itself provides (the
    OIDC request endpoint, the repository). A variable the job does not
    carry is not here, and the job's reads then go where that leaves them."""
    values = {
        DOOR_VARIABLES["BUREAU_READ"]: "on",
        DOOR_VARIABLES["BUREAU_READ_URL"]: door.url,
        DOOR_VARIABLES["BUREAU_READ_AUDIENCE"]: "https://app.agent-bureau.com/pipeline-read",
        DOOR_VARIABLES["BUREAU_PIPELINE_REF"]: "stable",
        WORKFLOW_RUN_END: run_end,
    }
    runner = door_env(door_url=door.url, issuer=issuer)
    env = {k: v for k, v in runner.items()
           if k.startswith(("ACTIONS_", "GITHUB_"))}
    for key, value in (job(name, job_id).get("env") or {}).items():
        if not key.startswith(("BUREAU_READ", "BUREAU_PIPELINE_REF")):
            continue
        value = str(value)
        env[key] = values.get(value, "" if "${{" in value else value)
    return env


def run_start_step(name: str, job_id: str, tmp_path: Path) -> dict:
    """Run the job's first step as the runner would and read what it wrote to
    `$GITHUB_ENV`."""
    first = job(name, job_id)["steps"][0]
    github_env = tmp_path / f"github-env-{job_id}"
    github_env.write_text("")
    proc = subprocess.run(["bash", "-e", "-c", first["run"]], capture_output=True,
                          text=True, env={**os.environ, "GITHUB_ENV": str(github_env)},
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in github_env.read_text().splitlines() if line)


# ── Linear: one card, in a file ─────────────────────────────────────────────


def comment(body: str, created_at: str, user: str | None = VIEWER) -> dict:
    return {"body": body, "createdAt": created_at, "user": {"id": user} if user else None}


def new_card(path: Path, comments=(), *, state: str = "In Progress", labels=(),
             title: str = "a card", description: str = "what it does") -> Path:
    """A card on Linear: its comments OLDEST first."""
    path.write_text(json.dumps({
        "identifier": CARD, "state": state, "labels": list(labels), "title": title,
        "description": description, "comments": list(comments)}))
    return path


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


class LinearCard:
    """api.linear.app for one card, read from and written to `path` on every
    request, so a comment one process posts is on the card the next process
    reads. Every other URL (the runner's OIDC endpoint, the door) goes to the
    real urlopen. `sent` is every query this process sent."""

    def __init__(self, path):
        self.path = Path(path)
        self.sent: list[str] = []
        self.real = urllib.request.urlopen

    def __call__(self, req, *a, **k):
        if not req.full_url.startswith(linear_ops.API):
            return self.real(req, *a, **k)
        payload = json.loads(req.data)
        self.sent.append(payload["query"])
        return _Resp(self.answer(payload["query"], payload.get("variables") or {}))

    def thread_reads(self) -> list[str]:
        return [q for q in self.sent if "comments(" in q]

    def answer(self, query: str, variables: dict) -> dict:
        card = json.loads(self.path.read_text())
        field = linear_ops._mutation_field(query)
        if field:
            if field == "commentCreate":
                card["comments"].append(comment(variables["input"]["body"],
                                                iso(datetime.now(UTC))))
                self.path.write_text(json.dumps(card))
            return {"data": {field: {"success": True}}}
        if query.strip() == "query { viewer { id } }":
            return {"data": {"viewer": {"id": VIEWER}}}
        if "$after" in query:
            return {"data": {"issue": {"comments": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}
        newest = list(reversed(card["comments"]))
        issue = {
            "id": f"uuid-{CARD}", "identifier": CARD, "title": card["title"],
            "description": card["description"], "team": {"id": "team-1"},
            "state": {"id": "state-1", "name": card["state"], "type": "started"},
            "labels": {"nodes": [{"id": f"label-{n}", "name": n} for n in card["labels"]]},
            "children": {"nodes": []}, "project": None,
            "comments": {"pageInfo": {"hasNextPage": len(newest) > linear_ops.COMMENT_WINDOW,
                                      "endCursor": None},
                         "nodes": newest[:linear_ops.COMMENT_WINDOW]},
        }
        return {"data": {"viewer": {"id": VIEWER}, "issue": issue}}


def door_node(path: Path, as_of: str) -> dict:
    """The door's node for the card in `path` as it stood at `as_of`: every
    comment posted by then, newest first, as the door hands them."""
    card = json.loads(path.read_text())
    node = door_shape(CARD, card["state"], labels=card["labels"], title=card["title"],
                      description=card["description"])
    node["comments"]["nodes"] = [c for c in reversed(card["comments"])
                                 if instant(c["createdAt"]) <= instant(as_of)]
    return node


@contextmanager
def door_at(path: Path, as_of: str):
    """A door answering FRESH, `as_of` that instant, with the card as it stood."""
    with FakeIssuer() as issuer, FakeDoor({CARD: door_node(path, as_of)}, as_of=as_of) as door:
        yield door, issuer


def fallback_lines(err: str) -> list[str]:
    return [ln for ln in err.splitlines() if ln.startswith(bureau_read.FALLBACK_TAG)]


# ── one process, as a step runs it ──────────────────────────────────────────

_CLI = r'''
import json, os, runpy, sys, urllib.request
tests, scripts = sys.argv[1], sys.argv[2]
sys.path[:0] = [scripts, tests]
from test_agent_runs_read_door import LinearCard
stub = LinearCard(os.environ["STUB_CARD"])
urllib.request.urlopen = stub
import bureau_read
bureau_read._wait = lambda _seconds: None  # a second ask never sleeps here (DRE-9250)
import atexit
def _save():
    with open(os.environ["STUB_SENT"], "w") as fh:
        json.dump(stub.sent, fh)
atexit.register(_save)
sys.argv = [scripts + "/" + sys.argv[3], *sys.argv[4:]]
runpy.run_path(sys.argv[0], run_name="__main__")
'''

#: What a test process must not inherit from the runner it runs on.
AMBIENT = ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE", "BUREAU_PIPELINE_REF",
           "BUREAU_READ_CARD_MAX_AGE", "BUREAU_READ_CARD_SEEN_AFTER",
           "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
           "GITHUB_EVENT_NAME", "GITHUB_HEAD_REF", "LINEAR_API_KEY_FALLBACK",
           "LINEAR_KEY_HOME", "BUREAU_LINEAR_READ_ONLY")


def linear_ops_cli(card_file: Path, env_over: dict, *args: str):
    """`linear_ops.py <args…>` as a step runs it, with Linear the card in
    `card_file`. Returns the process and the Linear queries it sent."""
    return script_cli(card_file, env_over, "linear_ops.py", *args)


def script_cli(card_file: Path, env_over: dict, script: str, *args: str):
    """`<script> <args…>`, any pipeline script, as a step runs it."""
    sent = card_file.parent / f"sent-{len(list(card_file.parent.glob('sent-*')))}.json"
    env = {k: v for k, v in os.environ.items() if k not in AMBIENT}
    env.update(LINEAR_API_KEY="test-key", LINEAR_IDENTITY="fleet", STUB_CARD=str(card_file),
               STUB_SENT=str(sent))
    env.update(env_over)
    proc = subprocess.run([sys.executable, "-c", _CLI, str(TESTS), str(ROOT / "scripts"), script,
                           *args],
                          capture_output=True, text=True, env=env, timeout=120)
    return proc, json.loads(sent.read_text()) if sent.exists() else None


def thread_queries(sent) -> list[str]:
    return [q for q in sent or [] if "comments(" in q]


# ── 1. the door's mode on every job that reads Linear ───────────────────────


@pytest.mark.parametrize("name,job_id", sorted(JOBS))
def test_every_job_that_reads_linear_carries_the_doors_variables(name, job_id):
    env = job(name, job_id).get("env") or {}
    for key, value in DOOR_VARIABLES.items():
        assert env.get(key) == value, (name, job_id, key)


@pytest.mark.parametrize("name,job_id", sorted(JOBS))
def test_every_job_carries_the_freshness_its_reader_needs(name, job_id):
    env = job(name, job_id).get("env") or {}
    assert str(env.get(linear_ops.CARD_MAX_AGE_ENV)) == JOBS[(name, job_id)], (name, job_id)


@pytest.mark.parametrize("job_id", MEDIC_JOBS)
def test_every_medic_job_reads_the_card_as_it_stood_when_the_run_ended(job_id):
    env = job("medic.yml", job_id).get("env") or {}
    assert env.get(linear_ops.CARD_SEEN_AFTER_ENV) == WORKFLOW_RUN_END, job_id


@pytest.mark.parametrize("name,job_id", START_JOBS)
def test_the_job_opens_by_noting_its_start(name, job_id, tmp_path):
    first = job(name, job_id)["steps"][0]
    assert first.get("name") == START_STEP
    before = datetime.now(UTC).replace(microsecond=0)
    written = run_start_step(name, job_id, tmp_path)
    after = datetime.now(UTC)
    assert list(written) == [linear_ops.CARD_SEEN_AFTER_ENV]
    noted = written[linear_ops.CARD_SEEN_AFTER_ENV]
    assert _SECOND.match(noted), noted
    assert before <= instant(noted) <= after


@pytest.mark.parametrize("name,job_id", START_JOBS)
def test_no_env_block_in_the_job_names_the_start(name, job_id):
    """A job- or step-level `env:` entry would override what the first step
    wrote to `$GITHUB_ENV` for that step, and its reads would lose the floor."""
    body = job(name, job_id)
    blocks = [body.get("env") or {}] + [s.get("env") or {} for s in body["steps"]]
    assert all(linear_ops.CARD_SEEN_AFTER_ENV not in block for block in blocks)


def test_the_steps_that_already_ask_the_door_keep_their_variables():
    for step_name in ("Card snapshot (one read for every pre-agent step)", "Card → In Progress"):
        env = step("agent-task.yml", "execute", step_name)["env"]
        for key, value in DOOR_VARIABLES.items():
            assert env.get(key) == value, (step_name, key)


# ── 2. the hand-back step uses the snapshot ─────────────────────────────────


def test_the_hand_back_step_reads_the_snapshot():
    env = step("agent-task.yml", "execute",
               "Hand an answered design question back to the critic")["env"]
    snapshot = step("agent-task.yml", "execute",
                    "Card snapshot (one read for every pre-agent step)")["env"]
    assert env.get("BUREAU_CARD_SNAPSHOT") == snapshot["BUREAU_CARD_SNAPSHOT"]


#: The design question as the fix loop posts it — its mark line is what
#: `design_answer` looks for, on a comment the pipeline posted.
QUESTION = "🎨 design-question: `Board` at `console/src/Board.tsx`\nShould it be one file?"


def test_the_hand_back_sees_the_pipelines_question_on_a_door_snapshot(tmp_path):
    """The door's viewer is the console's key, never this process's ("The
    viewer is never the door's"). A snapshot the door served must not make the
    pipeline's own question read as a person's, or `handback` finds nothing
    asked and the CEO's answer never reaches the critic."""
    now = datetime.now(UTC)
    card_file = new_card(tmp_path / "card.json", [comment(QUESTION, iso(now - timedelta(minutes=10)))])
    snapshot = tmp_path / "card-snapshot.json"
    with door_at(card_file, iso(now)) as (door, issuer):
        env = rendered_job_env("agent-task.yml", "execute", door=door, issuer=issuer)
        took, _ = script_cli(card_file, env, "agent_task_snapshot.py", "take", CARD,
                             "--out", str(snapshot))
        assert took.returncode == 0, took.stderr
        assert json.loads(snapshot.read_text())["source"] == "door"
        assert json.loads(snapshot.read_text())["viewer"] != VIEWER
        proc, sent = script_cli(card_file, {**env, "BUREAU_CARD_SNAPSHOT": str(snapshot)},
                                "design_answer.py", "state", CARD,
                                "--part", "Board", "--path", "console/src/Board.tsx")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "asked", proc.stderr
    assert thread_queries(sent) == []  # the thread came off the snapshot


def test_a_door_snapshot_with_no_viewer_of_our_own_reads_as_unknown(tmp_path, monkeypatch):
    """When Linear does not say who this key is, the door's viewer is not
    taken in its place: the pipeline's comments cannot be told from a
    person's, and the thread reads UNKNOWN."""
    import agent_task_snapshot
    import spoken_thread

    snapshot = tmp_path / "card-snapshot.json"
    snapshot.write_text(json.dumps({
        "schema": agent_task_snapshot.SCHEMA, "source": "door", "identifier": CARD,
        "issue": {"identifier": CARD}, "viewer": "fleet-user",
        "comments": [comment(QUESTION, "2026-11-01T00:00:00Z", user="fleet-user")]}))
    monkeypatch.setenv(agent_task_snapshot.ENV, str(snapshot))
    monkeypatch.setattr(linear_ops, "viewer_id", lambda: None)
    assert [v.kind for v in spoken_thread.read(CARD)] == [spoken_thread.UNKNOWN]


# ── Agent Task's report sees the earlier run's receipt ──────────────────────


def _requeue_card(tmp_path: Path, posted: str) -> Path:
    return new_card(tmp_path / "card.json", [
        comment("⏳ 1/5 plan", at(posted, -600)),
        comment("dead-run-budget-reset: the operator reset the cap", at(posted, -300)),
        comment("🔁 dead-run-requeue: run 1 died with no PR", posted),
    ])


def test_the_report_reads_linear_for_a_receipt_the_door_is_older_than(tmp_path):
    start = run_start_step("agent-task.yml", "execute", tmp_path)
    t = start[linear_ops.CARD_SEEN_AFTER_ENV]
    card_file = _requeue_card(tmp_path, at(t, -30))
    with door_at(card_file, at(t, -40)) as (door, issuer):
        env = {**rendered_job_env("agent-task.yml", "execute", door=door, issuer=issuer),
               **start, "RUNNER_TEMP": str(tmp_path)}
        proc, sent = linear_ops_cli(card_file, env, "count-comments", CARD, "dead-run-requeue",
                                    "--since", "dead-run-budget-reset")
        assert door.asked("/cards"), "the job's read never asked the door"
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "1"
    assert len(thread_queries(sent)) == 1
    (line,) = fallback_lines(proc.stderr)
    assert "reason=older-than-needed" in line


def test_the_report_takes_a_door_that_holds_the_receipt(tmp_path):
    start = run_start_step("agent-task.yml", "execute", tmp_path)
    t = start[linear_ops.CARD_SEEN_AFTER_ENV]
    card_file = _requeue_card(tmp_path, at(t, -30))
    with door_at(card_file, at(t, 60)) as (door, issuer):
        env = {**rendered_job_env("agent-task.yml", "execute", door=door, issuer=issuer),
               **start, "RUNNER_TEMP": str(tmp_path)}
        proc, sent = linear_ops_cli(card_file, env, "count-comments", CARD, "dead-run-requeue",
                                    "--since", "dead-run-budget-reset")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "1"
    assert thread_queries(sent) == []
    assert fallback_lines(proc.stderr) == []


# ── Agent Task's report sees the agent's own receipt ────────────────────────


def test_the_report_sees_the_receipt_the_agent_posted_in_this_job(tmp_path):
    """The agent posts `⏳ 3/5` five minutes into the job; the report's
    `dump-comments` then meets a door whose `as_of` is four minutes in. The
    job's own-write record, in the one `RUNNER_TEMP` both steps share, puts
    the floor at the post, so the read goes to Linear and lists it."""
    t = iso(datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5))
    card_file = new_card(tmp_path / "card.json", [comment("⏳ 1/5 plan", at(t, 30))])
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    with door_at(card_file, at(t, 240)) as (door, issuer):
        env = {**rendered_job_env("agent-task.yml", "execute", door=door, issuer=issuer),
               linear_ops.CARD_SEEN_AFTER_ENV: t, "RUNNER_TEMP": str(runner_temp)}
        posted, _sent = linear_ops_cli(card_file, env, "comment", CARD, "⏳ 3/5 green")
        assert posted.returncode == 0, posted.stderr
        proc, sent = linear_ops_cli(card_file, env, "dump-comments", CARD)
        assert [r["query"]["ids"] for r in door.asked("/cards")], "nothing asked the door"
    assert proc.returncode == 0, proc.stderr
    assert "⏳ 3/5 green" in json.loads(proc.stdout)
    assert len(thread_queries(sent)) == 1
    (line,) = fallback_lines(proc.stderr)
    assert "reason=older-than-needed" in line


# ── Agent Fix's hold count sees an earlier fix run's note ───────────────────

HOLD_KEY = "unfixable-check-hold @0123abcd"


def test_the_fix_jobs_hold_count_sees_an_earlier_runs_note(tmp_path):
    start = run_start_step("agent-fix.yml", "fix", tmp_path)
    t = start[linear_ops.CARD_SEEN_AFTER_ENV]
    card_file = new_card(tmp_path / "card.json", [
        comment("🔧 fix attempt 1", at(t, -120)),
        comment(f"The checks cannot be fixed here.\n\n{HOLD_KEY}", at(t, -20)),
    ])
    with door_at(card_file, at(t, -30)) as (door, issuer):
        env = {**rendered_job_env("agent-fix.yml", "fix", door=door, issuer=issuer),
               **start, "RUNNER_TEMP": str(tmp_path)}
        proc, sent = linear_ops_cli(card_file, env, "count-comments", CARD, HOLD_KEY)
        assert door.asked("/cards"), "the job's read never asked the door"
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "1"
    assert len(thread_queries(sent)) == 1
    (line,) = fallback_lines(proc.stderr)
    assert "reason=older-than-needed" in line


# ── the Medic: `dump-comments` off the door ─────────────────────────────────

#: The failed run's end.
RUN_END = "2026-11-03T18:00:00Z"


def test_a_medic_dump_comments_sends_no_linear_query(tmp_path):
    card_file = new_card(tmp_path / "card.json", [comment("⏳ 1/5 plan", at(RUN_END, -900))])
    with door_at(card_file, at(RUN_END, 2)) as (door, issuer):
        env = {**rendered_job_env("medic.yml", "classify", door=door, issuer=issuer,
                                  run_end=RUN_END), "RUNNER_TEMP": str(tmp_path)}
        proc, sent = linear_ops_cli(card_file, env, "dump-comments", CARD)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == ["⏳ 1/5 plan"]
    assert sent == []


#: This run, and the attempt the Medic is asked about.
RUN_ID, ATTEMPT = "38170959772", "2"
STALL_LOG = TESTS / "fixtures" / "stalled-plan-38170959772.log"


def _stall_card(tmp_path: Path) -> Path:
    stall = stream_watchdog.stall_from_log(STALL_LOG.read_text())
    assert stall is not None
    record = stream_watchdog.stall_record(stall, run_id=RUN_ID, attempt="1")
    return new_card(tmp_path / "card.json", [
        comment("⏳ 1/5 plan", at(RUN_END, -900)),
        comment(record, at(RUN_END, -5)),
    ])


@pytest.mark.parametrize("job_id", ["stall_record", "diagnose"])
@pytest.mark.parametrize("offset,from_linear", [(-10, True), (0, False), (2, False)])
def test_a_medic_thread_read_sees_the_stall_record_the_run_left(job_id, offset, from_linear,
                                                                tmp_path):
    """The failed run's attempt 1 left its stall record at T minus 5 seconds.
    A door older than T cannot vouch for it and the read goes to Linear; a
    door at or after T can, and Linear is not asked. Either way the record is
    what `repeat_stall` reads."""
    card_file = _stall_card(tmp_path)
    with door_at(card_file, at(RUN_END, offset)) as (door, issuer):
        env = {**rendered_job_env("medic.yml", job_id, door=door, issuer=issuer,
                                  run_end=RUN_END), "RUNNER_TEMP": str(tmp_path)}
        proc, sent = linear_ops_cli(card_file, env, "dump-comments", CARD)
        assert door.asked("/cards"), "the job's read never asked the door"
    assert proc.returncode == 0, proc.stderr
    bodies = json.loads(proc.stdout)
    stall = stream_watchdog.stall_from_log(STALL_LOG.read_text())
    assert medic_retry.repeat_stall(stall, bodies, run_id=RUN_ID, attempt=ATTEMPT)
    if from_linear:
        assert len(thread_queries(sent)) == 1
        (line,) = fallback_lines(proc.stderr)
        assert "reason=older-than-needed" in line
    else:
        assert sent == []
        assert fallback_lines(proc.stderr) == []
