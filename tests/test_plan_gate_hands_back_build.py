"""The planner's guard hands a one-off back as a build (DRE-7110).

THE WINDOW. Once the flip (DRE-7120) reaches `stable`, the sweep promotes a
card out of `Queued` straight into `In Progress`. Until the relay carrying
DRE-7107 is live, the relay still reads `agent:planner` alone as an epic
approval: `_is_epic_activation(state_name, labels)` in agent-bureau's
`cloud/relay/lambda_function.py` is True for ANY card wearing the label that
enters `In Progress`. So a classified one-off — every one wears
`agent:planner` — is answered with an `agent-plan` dispatch whose
`client_payload.trigger_state` is `"in progress"` and which carries no
`reason`. Today `plan.yml` plans it as an approved epic.

THE FIX UNDER TEST. `plan_decide` reads one more condition, for a dispatch
fired for `in progress` on a card still in `In Progress`: a card with no
children and a `one-off` stamp is skipped with the guard's one receipt, and
the guard fires at most one `agent-execute` for it — none when its build is
already running (a build run in flight, or a `🧠 model-attempt` start receipt
newer than its newest entry into `In Progress`) or when that cannot be read.

Every case here drives `dedupe_dispatch.py plan-gate` with the environment
`plan.yml`'s `Duplicate-dispatch guard` step builds from the dispatch payload,
read off the workflow file. Linear is faked at the HTTP layer and GitHub at
the `gh` CLI, so every request each side is sent is on the record.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from unittest import mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")

import dedupe_dispatch  # noqa: E402
import linear_ops  # noqa: E402
import planning_shape  # noqa: E402
import plan_run  # noqa: E402

CARD = "DRE-7777"
REPO = "dreadnought-foundry/portico"
OWN_RUN = "40000000500"
OWN_CREATED = "2026-10-18T17:00:30Z"
PLAN_WORKFLOW_ID = "111"
ENTERED_IN_PROGRESS = "2026-10-18T17:00:00.000Z"
PLAN_YML = ROOT / ".github" / "workflows" / "plan.yml"

HAND_BACK = (
    f"{CARD} is a one-off, not an epic — its move into In Progress was sent to "
    "the planner; handed back as a build"
)


def _stamp(shape: str) -> str:
    return planning_shape.shape_comment(shape, "classified at Planning's front door")


def _relay_payload(trigger_state: str = "in progress") -> dict:
    """The `client_payload` the relay at agent-bureau `main` sends for a
    labeled one-off entering a lane: the six base fields and `trigger_state`,
    no `reason`, no `sent_by_run`."""
    return {
        "card_id": "uuid-7777",
        "identifier": CARD,
        "title": "Fix the widget",
        "description": "Do the thing.",
        "labels": ["agent:planner", "repo:portico"],
        "url": f"https://linear.app/dreadnoughtfoundry/issue/{CARD}",
        "trigger_state": trigger_state,
    }


def _guard_env(payload: dict) -> dict:
    """The env `plan.yml`'s `Duplicate-dispatch guard` step sets, with each
    `${{ github.event.client_payload.<key> }}` filled from `payload` the way
    Actions fills it (a missing key is the empty string)."""
    doc = yaml.safe_load(PLAN_YML.read_text())
    step = next(s for job in doc["jobs"].values() for s in job.get("steps", [])
                if s.get("name") == "Duplicate-dispatch guard")
    env = {}
    for key, expr in (step.get("env") or {}).items():
        m = re.fullmatch(r"\$\{\{\s*github\.event\.client_payload\.(\w+)\s*\}\}",
                         str(expr))
        if m:
            env[key] = str(payload.get(m.group(1)) or "")
        elif key == "REPO":
            env[key] = REPO
        elif key == "GH_TOKEN":
            env[key] = "ghs_test"
        else:
            env[key] = "lin_api_test"
    assert "plan-gate" in step["run"]
    return env


# ── Linear, at the HTTP layer ───────────────────────────────────────────────


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
    """The card as it is now. `fail` names the reads that error: `card` (the
    guard's hand-back read of the card), `thread` (the comment thread),
    `history` (the lane history)."""

    def __init__(self, *, lane="In Progress", children=0, comments=(),
                 fail=()):
        self.lane = lane
        self.children = children
        self.comments = [
            c if isinstance(c, dict) else
            {"body": c, "createdAt": "2026-10-18T16:00:00.000Z", "user": {"id": "u1"}}
            for c in comments
        ]
        self.fail = set(fail)
        self.writes: list = []
        self.posted: list = []

    def urlopen(self, req, timeout=None, **kw):
        assert getattr(req, "full_url", "") == linear_ops.API, req.full_url
        payload = json.loads(req.data)
        q = " ".join(payload["query"].split())
        return _Response(self._answer(q, payload.get("variables") or {}))

    def _issue(self) -> dict:
        return {
            "id": "uuid-7777", "identifier": CARD, "title": "Fix the widget",
            "description": "Do the thing.", "createdAt": "2026-10-10T00:00:00.000Z",
            "team": {"id": "team-1", "key": "DRE"},
            "state": {"id": "st-x", "name": self.lane, "type": "started"},
            "labels": {"nodes": [{"name": "agent:planner"}, {"name": "repo:portico"}]},
            "children": {"nodes": [{"id": f"c{i}", "identifier": f"DRE-{i}"}
                                   for i in range(self.children)]},
            "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                         "nodes": list(reversed(self.comments))},
        }

    def _answer(self, q: str, v: dict) -> dict:
        if q.startswith("mutation"):
            if "commentCreate" in q:
                self.posted.append(v["input"]["body"])
                return {"data": {"commentCreate": {"success": True}}}
            self.writes.append(q[:80])
            return {"data": {"issueUpdate": {"success": True}}}
        if "history(" in q:
            if "history" in self.fail:
                return {"errors": [{"message": "history unreadable"}]}
            return {"data": {"issue": {"history": {"nodes": [
                {"createdAt": ENTERED_IN_PROGRESS, "toState": {"name": "In Progress"}},
                {"createdAt": "2026-10-18T16:30:00.000Z", "toState": {"name": "Queued"}},
            ]}}}}
        if "comments(" in q and "description" not in q:
            if "thread" in self.fail:
                return {"errors": [{"message": "thread unreadable"}]}
            return {"data": {"viewer": {"id": "u1"}, "issue": self._issue()}}
        if "description" in q and "card" in self.fail:
            return {"errors": [{"message": "card unreadable"}]}
        return {"data": {"issue": self._issue(), "viewer": {"id": "u1"}}}


# ── GitHub, at the gh CLI ───────────────────────────────────────────────────


class FakeGitHub:
    """`gh api` as the guard and `plan_run.fire` call it. `build_runs` is the
    build workflow's run listing (None: the listing errors); `jobs` maps a run
    id to its job names."""

    def __init__(self, *, build_runs=(), jobs=None):
        self.build_runs = build_runs
        self.jobs = dict(jobs or {})
        self.dispatched: list = []
        self.calls: list = []

    def __call__(self, argv, **kw):
        if not argv or argv[0] != "gh":
            raise AssertionError(f"unexpected subprocess: {argv}")
        self.calls.append(argv)
        args = argv[1:]
        if args[:1] == ["api"] and "--input" in args:
            body = json.loads(Path(args[args.index("--input") + 1]).read_text())
            self.dispatched.append(body)
            return self._ok("")
        path = args[1]
        if path == f"repos/{REPO}/actions/runs/{OWN_RUN}":
            return self._ok({"created_at": OWN_CREATED,
                             "workflow_id": PLAN_WORKFLOW_ID})
        if path.startswith(f"repos/{REPO}/actions/workflows/{PLAN_WORKFLOW_ID}/runs"):
            return self._ok({"workflow_runs": [
                {"id": int(OWN_RUN), "created_at": OWN_CREATED,
                 "updated_at": OWN_CREATED, "status": "in_progress"}]})
        if path.startswith(f"repos/{REPO}/actions/workflows/agent-task.yml/runs"):
            if self.build_runs is None:
                return self._fail("HTTP 502: Bad Gateway")
            return self._ok({"workflow_runs": list(self.build_runs)})
        m = re.fullmatch(rf"repos/{re.escape(REPO)}/actions/runs/(\d+)/jobs", path)
        if m:
            return self._ok({"jobs": [{"name": n} for n in self.jobs.get(m.group(1), [])]})
        raise AssertionError(f"unexpected gh call: {argv}")

    @staticmethod
    def _ok(body):
        out = body if isinstance(body, str) else json.dumps(body)
        return subprocess.CompletedProcess([], 0, stdout=out, stderr="")

    @staticmethod
    def _fail(err):
        return subprocess.CompletedProcess([], 1, stdout="", stderr=err)

    @property
    def events(self) -> list:
        return [d["event_type"] for d in self.dispatched]


def _plan_gate(monkeypatch, tmp_path, lin, gh, payload=None):
    """Run `dedupe_dispatch.py plan-gate CARD` as plan.yml's step does;
    answer its step outputs and everything it printed."""
    payload = payload or _relay_payload()
    out = tmp_path / "github_output"
    out.write_text("")
    for key, value in _guard_env(payload).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_RUN_ID", OWN_RUN)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.delenv("GH_DISPATCH_TOKEN", raising=False)
    linear_ops._card_memo.clear()
    monkeypatch.setattr(urllib.request, "urlopen", lin.urlopen)
    monkeypatch.setattr(subprocess, "run", gh)
    rc = dedupe_dispatch.main(["dedupe_dispatch.py", "plan-gate", CARD])
    assert rc == 0
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if line)
    return outputs


def _one_off(**kw):
    kw.setdefault("comments", [_stamp("one-off")])
    return FakeLinear(**kw)


# ── the dispatch the relay sends today, for a labeled one-off ──────────────


def test_the_env_carries_the_relays_trigger_lane_and_no_sender():
    env = _guard_env(_relay_payload())
    assert env["TRIGGER_STATE"] == "in progress"
    assert env["SENT_BY_RUN"] == ""


def test_a_stamped_one_off_is_handed_back_as_one_build(monkeypatch, tmp_path, capsys):
    lin, gh = _one_off(), FakeGitHub()
    outputs = _plan_gate(monkeypatch, tmp_path, lin, gh)
    assert outputs["skip"] == "true"
    assert outputs["reason"] == HAND_BACK
    assert gh.events == [plan_run.EXECUTE_EVENT]
    assert gh.dispatched[0]["client_payload"]["identifier"] == CARD
    assert lin.writes == []
    assert len(lin.posted) == 1
    assert lin.posted[0].startswith(f"🤖 Duplicate dispatch skipped: {HAND_BACK}. ")
    assert "This run planned nothing and moved no lane." in lin.posted[0]
    assert f"plan-gate: {CARD} handed back as agent-execute" in capsys.readouterr().out


def test_a_start_receipt_from_an_earlier_trip_does_not_count(monkeypatch, tmp_path):
    lin = _one_off(comments=[
        {"body": "🧠 model-attempt: opus — engineer agent starting. Run: "
                 f"https://github.com/{REPO}/actions/runs/39999999999",
         "createdAt": "2026-10-17T09:00:00.000Z", "user": {"id": "u1"}},
        _stamp("one-off"),
    ])
    gh = FakeGitHub()
    outputs = _plan_gate(monkeypatch, tmp_path, lin, gh)
    assert outputs["skip"] == "true"
    assert gh.events == [plan_run.EXECUTE_EVENT]


def test_a_failed_fire_is_logged_and_still_skips(monkeypatch, tmp_path, capsys):
    lin = _one_off()

    class Refused(FakeGitHub):
        def __call__(self, argv, **kw):
            if "--input" in argv:
                self.calls.append(argv)
                return self._fail("HTTP 403: Resource not accessible")
            return super().__call__(argv, **kw)

    gh = Refused()
    outputs = _plan_gate(monkeypatch, tmp_path, lin, gh)
    assert outputs["skip"] == "true"
    assert gh.dispatched == []
    assert "HTTP 403" in capsys.readouterr().err


# ── the build is already running: nothing is fired ─────────────────────────


def test_a_build_run_in_flight_fires_nothing(monkeypatch, tmp_path):
    lin = _one_off()
    gh = FakeGitHub(
        build_runs=[{"id": 40000000400, "status": "in_progress",
                     "created_at": "2026-10-18T16:59:00Z"}],
        jobs={"40000000400": [f"call / bureau-card: {CARD}"]},
    )
    outputs = _plan_gate(monkeypatch, tmp_path, lin, gh)
    assert outputs["skip"] == "true"
    assert outputs["reason"] == HAND_BACK
    assert gh.dispatched == []
    assert lin.writes == []


def test_another_cards_build_in_flight_is_not_this_ones(monkeypatch, tmp_path):
    lin = _one_off()
    gh = FakeGitHub(
        build_runs=[{"id": 40000000401, "status": "queued",
                     "created_at": "2026-10-18T16:59:00Z"}],
        jobs={"40000000401": ["call / bureau-card: DRE-77770"]},
    )
    _plan_gate(monkeypatch, tmp_path, lin, gh)
    assert gh.events == [plan_run.EXECUTE_EVENT]


def test_a_start_receipt_newer_than_the_entry_fires_nothing(monkeypatch, tmp_path):
    lin = _one_off(comments=[
        _stamp("one-off"),
        {"body": "🧠 model-attempt: opus — engineer agent starting. Run: "
                 f"https://github.com/{REPO}/actions/runs/40000000400",
         "createdAt": "2026-10-18T17:00:05.000Z", "user": {"id": "u1"}},
    ])
    gh = FakeGitHub()
    outputs = _plan_gate(monkeypatch, tmp_path, lin, gh)
    assert outputs["skip"] == "true"
    assert gh.dispatched == []


def test_an_unreadable_run_listing_fires_nothing(monkeypatch, tmp_path, capsys):
    lin, gh = _one_off(), FakeGitHub(build_runs=None)
    outputs = _plan_gate(monkeypatch, tmp_path, lin, gh)
    assert outputs["skip"] == "true"
    assert outputs["reason"] == HAND_BACK
    assert gh.dispatched == []
    assert lin.writes == []


def test_an_unreadable_job_list_fires_nothing(monkeypatch, tmp_path):
    lin = _one_off()

    class NoJobs(FakeGitHub):
        def __call__(self, argv, **kw):
            if len(argv) > 2 and argv[2].endswith("/jobs"):
                self.calls.append(argv)
                return self._fail("HTTP 502: Bad Gateway")
            return super().__call__(argv, **kw)

    gh = NoJobs(build_runs=[{"id": 40000000402, "status": "queued",
                             "created_at": "2026-10-18T16:59:00Z"}])
    outputs = _plan_gate(monkeypatch, tmp_path, lin, gh)
    assert outputs["skip"] == "true"
    assert gh.dispatched == []


def test_an_unreadable_lane_history_fires_nothing(monkeypatch, tmp_path):
    lin = _one_off(fail={"history"}, comments=[
        _stamp("one-off"),
        {"body": "🧠 model-attempt: opus — engineer agent starting. Run: "
                 f"https://github.com/{REPO}/actions/runs/40000000400",
         "createdAt": "2026-10-18T17:00:05.000Z", "user": {"id": "u1"}},
    ])
    gh = FakeGitHub()
    outputs = _plan_gate(monkeypatch, tmp_path, lin, gh)
    assert outputs["skip"] == "true"
    assert gh.dispatched == []


# ── anything else is decided exactly as today ───────────────────────────────

TODAY_PROCEEDS = (
    f"no planner run was in flight for {CARD} and it is still in "
    "'In Progress' — proceeding"
)


@pytest.mark.parametrize("card", [
    dict(children=2, comments=[_stamp("one-off")]),
    dict(children=1, comments=[_stamp("epic")]),
    dict(comments=[_stamp("epic")]),
    dict(comments=[_stamp("roll-up")]),
    dict(comments=["no stamp here"]),
    dict(comments=[_stamp("one-off")], fail={"card"}),
    dict(comments=[_stamp("one-off")], fail={"thread"}),
], ids=["children", "epic-with-children", "epic", "roll-up", "no-stamp",
        "unreadable-card", "unreadable-thread"])
def test_anything_but_a_stamped_one_off_is_planned_as_today(monkeypatch, tmp_path, card):
    lin, gh = FakeLinear(**card), FakeGitHub()
    outputs = _plan_gate(monkeypatch, tmp_path, lin, gh)
    assert outputs["skip"] == "false"
    assert outputs["reason"] == TODAY_PROCEEDS
    assert gh.dispatched == []
    assert lin.posted == [] and lin.writes == []


@pytest.mark.parametrize("trigger, lane", [
    ("planning", "Planning"), ("green light", "Green Light"), ("triage", "Triage"),
])
def test_a_dispatch_fired_for_another_lane_proceeds_as_today(monkeypatch, tmp_path,
                                                             trigger, lane):
    lin, gh = _one_off(lane=lane), FakeGitHub()
    outputs = _plan_gate(monkeypatch, tmp_path, lin, gh,
                         payload=_relay_payload(trigger))
    assert outputs["skip"] == "false"
    assert outputs["reason"] == (
        f"no planner run was in flight for {CARD} and it is still in "
        f"{lane!r} — proceeding")
    assert gh.dispatched == []
    assert lin.posted == [] and lin.writes == []


@pytest.mark.parametrize("trigger", ["planning", "green light", "triage"])
def test_a_dispatch_for_another_lane_on_a_card_now_in_progress_is_as_today(
        monkeypatch, tmp_path, trigger):
    lin, gh = _one_off(), FakeGitHub()
    outputs = _plan_gate(monkeypatch, tmp_path, lin, gh,
                         payload=_relay_payload(trigger))
    assert outputs["skip"] == "true"
    assert outputs["reason"] == (
        f"{CARD} was dispatched on entering {trigger!r} and is now in "
        "'In Progress' — that entry has already been planned")
    assert gh.dispatched == []
    assert lin.writes == []


def test_a_one_off_that_left_in_progress_is_as_today(monkeypatch, tmp_path):
    lin, gh = _one_off(lane="In Review"), FakeGitHub()
    outputs = _plan_gate(monkeypatch, tmp_path, lin, gh)
    assert outputs["skip"] == "true"
    assert "that entry has already been planned" in outputs["reason"]
    assert gh.dispatched == []
