"""Red-main repair raises console alerts, and closes them itself (DRE-7454).

Red-main repair used to file two Triage cards — "Red main needs a human —
repair budget exhausted" and "Red main needs a decision" — each created in
Planning and moved to Triage in a second write, the lane DRE-2723 reserved for
broken cards. Both are notices that a person must look at a red default
branch, so both now go through the notice door's client
(`scripts/pipeline_notice.py`) as CRITICAL console alerts:

* the budget-exhausted step raises `red_main:<repo>:budget_exhausted`;
* the Report step's escalation raises `red_main:<repo>:needs_decision`;
* each hands the client today's card — its title, lane `Triage` and label
  `repo:<slug>` — as the fallback for a door it cannot reach, and neither
  runs `linear_ops.py create`, `find-open` or `state … Triage` any more;
* a new `clear-notices` job resolves both keys, with the all-clear copy, when
  a watched workflow's run on the default branch concludes `success` and no
  other run on that commit failed, timed out or is still running. That
  decision and the recovery body live in `scripts/red_main_clear.py`.

The steps are read from the workflow file and executed as GitHub executes
them (`bash -e -o pipefail`), with the client and `gh` stubbed, so the flags
proved here are the flags the step really passes.

Run: cd bureau-pipeline && python3 -m pytest tests/test_red_main_repair_notice.py -v
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import red_main_clear  # noqa: E402
import step_shell  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "red-main-repair.yml"

RUN_URL = "https://github.com/EveryBite/Atlas/actions/runs/38003951327"
GREEN_URL = "https://github.com/EveryBite/Atlas/actions/runs/38100000001"
HEAD_SHA = "abc1234def5678abc1234def5678abc1234def56"
REPO = "EveryBite/Atlas"
SLUG = "atlas"


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _steps(job: str) -> list[dict]:
    return _doc()["jobs"][job]["steps"]


def _budget_step() -> dict:
    found = [s for s in _steps("repair")
             if s.get("if") == "steps.decide.outputs.escalate == 'true'"]
    assert len(found) == 1, "exactly one budget-exhausted step"
    return found[0]


def _report_step() -> dict:
    found = [s for s in _steps("repair") if s.get("name") == "Report"]
    assert len(found) == 1, "exactly one Report step"
    return found[0]


def _escalation_branch(run: str) -> str:
    """The Report step's escalation branch: from the escalation file's test to
    the next branch of the chain."""
    start = run.index("/tmp/repair-escalation.txt")
    return run[start:run.index("repair_finish.py finish", start)]


def _clear_job() -> dict:
    jobs = _doc()["jobs"]
    assert "clear-notices" in jobs, "no clear-notices job"
    return jobs["clear-notices"]


def _clear_step(fragment: str) -> dict:
    found = [s for s in _clear_job()["steps"] if fragment in (s.get("run") or "")]
    assert len(found) == 1, f"exactly one clear-notices step runs {fragment!r}"
    return found[0]


# ── the stubs ─────────────────────────────────────────────────────────────────

# The client, recorded: one JSON line per call, with the text of every file it
# was handed so the body can be read after the step's temp files are gone.
_CLIENT_STUB = r'''
import json, os, sys
args = sys.argv[1:]
files = {}
for flag in ("--body-file", "--recovery-body-file"):
    if flag in args:
        path = args[args.index(flag) + 1]
        files[flag] = open(path, encoding="utf-8").read()
with open(os.environ["CALLS"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"tool": "pipeline_notice", "args": args, "files": files}) + "\n")
notice = args[args.index("--notice") + 1]
print(f"pipeline-notice: {os.environ.get('FAKE_OUTCOME', 'raised')} kind=pipeline_notice:{notice}")
sys.exit(int(os.environ.get("FAKE_CLIENT_RC", "0")))
'''

# Any other pipeline script the step reaches: recorded, silent, green.
_RECORDER_STUB = r'''
import json, os, sys
with open(os.environ["CALLS"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"tool": os.path.basename(sys.argv[0]), "args": sys.argv[1:]}) + "\n")
'''

# `gh`, answering `gh run list` with FAKE_RUNS (or failing on FAKE_GH_RC).
_GH_STUB = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["CALLS"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"tool": "gh", "args": sys.argv[1:]}) + "\n")
rc = int(os.environ.get("FAKE_GH_RC", "0"))
if rc:
    print("HTTP 403: Resource not accessible by integration", file=sys.stderr)
    sys.exit(rc)
print(os.environ.get("FAKE_RUNS", "[]"))
'''


def _run_step(tmp: Path, step: dict, env: dict, *, real=()) -> tuple:
    """Execute `step`'s shell in `tmp` with every pipeline script stubbed
    except `real`, and `/tmp/` moved under `tmp` so nothing is shared."""
    scripts = tmp / ".bureau-pipeline" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("linear_ops.py", "card_pr.py", "repair_finish.py",
                 "repair_history.py", "red_main_repair.py"):
        (scripts / name).write_text(_RECORDER_STUB)
    (scripts / "pipeline_notice.py").write_text(_CLIENT_STUB)
    for name in real:
        shutil.copy(ROOT / "scripts" / name, scripts / name)
    bindir = tmp / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text(_GH_STUB)
    gh.chmod(0o755)
    scratch = tmp / "scratch"
    scratch.mkdir(exist_ok=True)
    calls = tmp / "calls.jsonl"
    calls.write_text("")
    output = tmp / "github-output"
    output.write_text("")
    summary = tmp / "summary.md"
    summary.write_text("")
    body = step_shell.step_shell(step).replace("/tmp/", f"{scratch}/")
    full_env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "CALLS": str(calls),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_SERVER_URL": "https://github.com",
        **env,
    }
    done = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", body],
                          cwd=tmp, env=full_env, capture_output=True,
                          text=True, check=False)
    recorded = [json.loads(ln) for ln in calls.read_text().splitlines() if ln]
    return done, recorded, scratch, output.read_text()


def _flag(args: list[str], name: str) -> str:
    assert name in args, f"{name} not passed: {args}"
    return args[args.index(name) + 1]


def _flags(args: list[str], name: str) -> list[str]:
    return [args[i + 1] for i in range(len(args) - 1) if args[i] == name]


# ── the budget-exhausted step ────────────────────────────────────────────────


BUDGET_ENV = {
    "RUN_URL": RUN_URL,
    "WF_NAME": "Pipeline Tests",
    "HEAD_SHA": HEAD_SHA,
    "TIMEOUT_JOB": "",
    "TIMEOUT_STEP": "",
    "TIMEOUT_LIMIT": "",
    "TIMEOUT_COMMITS": "",
}


def test_the_budget_step_raises_the_budget_exhausted_alert(tmp_path):
    done, calls, _, _ = _run_step(tmp_path, _budget_step(), BUDGET_ENV)
    assert done.returncode == 0, done.stderr + done.stdout
    raises = [c for c in calls if c["tool"] == "pipeline_notice"]
    assert len(raises) == 1, calls
    args = raises[0]["args"]
    assert args[0] == "raise"
    assert _flag(args, "--notice") == f"red_main:{SLUG}:budget_exhausted"
    assert _flag(args, "--severity") == "CRITICAL"
    assert _flag(args, "--action-url") == RUN_URL


def test_the_budget_alert_carries_the_body_it_writes_today(tmp_path):
    _, calls, _, _ = _run_step(tmp_path, _budget_step(), BUDGET_ENV)
    body = calls[0]["files"]["--body-file"]
    assert "both of its attempts failed" in body
    assert f"What failed: Pipeline Tests. Latest failed run: {RUN_URL}" in body
    assert "A human needs to look at this one." in body


def test_the_budget_alert_names_the_step_and_the_clock(tmp_path):
    env = {**BUDGET_ENV, "TIMEOUT_JOB": "unit", "TIMEOUT_STEP": "Test",
           "TIMEOUT_LIMIT": "12m", "TIMEOUT_COMMITS": "abc1234, def5678"}
    _, calls, _, _ = _run_step(tmp_path, _budget_step(), env)
    body = calls[0]["files"]["--body-file"]
    assert 'the step "Test" of job "unit" ran past its limit (12m)' in body
    assert "abc1234, def5678" in body


def test_the_budget_fallback_is_todays_card(tmp_path):
    _, calls, _, _ = _run_step(tmp_path, _budget_step(), BUDGET_ENV)
    args = calls[0]["args"]
    assert _flag(args, "--fallback-title") == (
        f"Red main needs a human — repair budget exhausted ({HEAD_SHA[:7]})")
    assert _flag(args, "--fallback-lane") == "Triage"
    assert _flags(args, "--fallback-label") == [f"repo:{SLUG}"]


def test_the_budget_step_files_and_searches_nothing_itself(tmp_path):
    _, calls, _, _ = _run_step(tmp_path, _budget_step(), BUDGET_ENV)
    assert [c["tool"] for c in calls] == ["pipeline_notice"]
    run = _budget_step()["run"]
    assert not re.search(r"linear_ops\.py\S*\s+create\b", run)
    assert "find-open" not in run
    assert not re.search(r"linear_ops\.py\S*\s+state\b", run)


def test_a_lost_budget_alert_fails_the_step(tmp_path):
    done, _, _, _ = _run_step(tmp_path, _budget_step(),
                              {**BUDGET_ENV, "FAKE_OUTCOME": "lost no-url",
                               "FAKE_CLIENT_RC": "1"})
    assert done.returncode != 0


def test_the_budget_step_reaches_the_door():
    env = _budget_step().get("env") or {}
    assert env.get("BUREAU_READ_URL") == "${{ vars.BUREAU_READ_URL }}"
    # The fallback card still needs Linear.
    assert env.get("LINEAR_API_KEY") == "${{ secrets.LINEAR_API_KEY }}"


# ── the Report step's escalation ─────────────────────────────────────────────


REPORT_ENV = {
    "RUN_URL": RUN_URL,
    "WF_NAME": "Pipeline Tests",
    "BRANCH": "repair/DRE-1-abc1234def56",
    "CARD_URL": "",
    "DEFAULT_BRANCH": "main",
    "HEAD_SHA": HEAD_SHA,
}

ESCALATION = "Should the nightly keep the slow suite, or move it to a weekly run?"


def _run_escalation(tmp_path, **extra):
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "repair-escalation.txt").write_text(ESCALATION + "\n")
    return _run_step(tmp_path, _report_step(), {**REPORT_ENV, **extra})


def test_the_escalation_raises_the_needs_decision_alert(tmp_path):
    done, calls, _, _ = _run_escalation(tmp_path)
    assert done.returncode == 0, done.stderr + done.stdout
    raises = [c for c in calls if c["tool"] == "pipeline_notice"]
    assert len(raises) == 1, calls
    args = raises[0]["args"]
    assert args[0] == "raise"
    assert _flag(args, "--notice") == f"red_main:{SLUG}:needs_decision"
    assert _flag(args, "--severity") == "CRITICAL"


def test_the_escalation_text_is_the_alerts_body(tmp_path):
    _, calls, _, _ = _run_escalation(tmp_path)
    raise_ = [c for c in calls if c["tool"] == "pipeline_notice"][0]
    assert ESCALATION in raise_["files"]["--body-file"]


def test_the_escalation_fallback_is_todays_card(tmp_path):
    _, calls, _, _ = _run_escalation(tmp_path)
    args = [c for c in calls if c["tool"] == "pipeline_notice"][0]["args"]
    assert _flag(args, "--fallback-title") == (
        "Red main needs a decision — Pipeline Tests failing on the default branch")
    assert _flag(args, "--fallback-lane") == "Triage"
    assert _flags(args, "--fallback-label") == [f"repo:{SLUG}"]


def test_the_escalation_files_and_searches_nothing_itself(tmp_path):
    _, calls, _, _ = _run_escalation(tmp_path)
    assert not [c for c in calls if c["tool"] == "linear_ops.py"], calls
    branch = _escalation_branch(_report_step()["run"])
    assert "pipeline_notice.py raise" in branch
    assert not re.search(r"linear_ops\.py\S*\s+create\b", branch)
    assert "find-open" not in branch
    assert not re.search(r"linear_ops\.py\S*\s+state\b", branch)


def test_a_lost_escalation_fails_the_step(tmp_path):
    done, _, _, _ = _run_escalation(tmp_path, FAKE_OUTCOME="lost no-url",
                                    FAKE_CLIENT_RC="1")
    assert done.returncode != 0
    assert "::error::" in done.stdout


def test_the_report_step_reaches_the_door():
    env = _report_step().get("env") or {}
    assert env.get("BUREAU_READ_URL") == "${{ vars.BUREAU_READ_URL }}"


def test_the_workflow_mints_no_card_of_its_own():
    """Neither alert is a card any more: no step in the file creates one,
    searches for one or moves one to Triage."""
    for job_name, job in _doc()["jobs"].items():
        for step in job.get("steps", []):
            run = step.get("run") or ""
            name = f"{job_name}/{step.get('name', '<unnamed>')}"
            assert not re.search(r"linear_ops\.py\S*\s+create\b", run), name
            assert "find-open" not in run, name
            assert not re.search(r'linear_ops\.py\S*\s+state\b[^\n]*Triage', run), name


# ── id-token: no raising job is denied it ────────────────────────────────────


def _keeps_id_token(job: dict) -> bool:
    perms = job.get("permissions")
    if perms is None:
        return True  # inherits the stub's grant
    return isinstance(perms, dict) and perms.get("id-token") == "write"


def test_no_raising_or_resolving_job_is_denied_id_token():
    jobs = _doc()["jobs"]
    for name in ("repair", "clear-notices"):
        assert name in jobs, name
        assert _keeps_id_token(jobs[name]), name
    assert _keeps_id_token(_doc())


# ── the clear-notices job, from the workflow file ────────────────────────────


def test_clear_notices_runs_only_on_a_default_branch_success():
    cond = " ".join(str(_clear_job().get("if")).split())
    assert cond == (
        "github.event.workflow_run.conclusion == 'success' && "
        "github.event.workflow_run.head_branch == "
        "github.event.repository.default_branch")


def test_clear_notices_grants_exactly_three_permissions():
    assert _clear_job().get("permissions") == {
        "id-token": "write", "contents": "read", "actions": "read"}


def test_clear_notices_holds_no_linear_and_files_no_card():
    text = yaml.safe_dump(_clear_job())
    assert "linear_ops.py" not in text
    assert "LINEAR_API_KEY" not in text
    assert "--fallback" not in text


def test_clear_notices_reads_every_run_on_the_commit():
    step = _clear_step("red_main_clear.py decide")
    run = step["run"]
    assert "gh run list" in run
    assert '--commit "$HEAD_SHA"' in run
    env = step.get("env") or {}
    assert env.get("GH_TOKEN") == "${{ github.token }}"
    assert env.get("HEAD_SHA") == "${{ github.event.workflow_run.head_sha }}"


def test_clear_notices_resolves_both_keys_with_the_all_clear_copy():
    step = _clear_step("pipeline_notice.py resolve")
    run = step["run"]
    assert step.get("if") == "steps.green.outputs.go == 'true'"
    for key in ("budget_exhausted", "needs_decision"):
        assert f'--notice "red_main:$REPO_SLUG:{key}"' in run
    assert run.count("pipeline_notice.py resolve") == 2
    assert run.count('--recovery-title "Main is green again in $REPO_SLUG"') == 2
    assert run.count("--recovery-body-file") == 2


def test_clear_notices_reaches_the_door():
    step = _clear_step("pipeline_notice.py resolve")
    env = {**(_clear_job().get("env") or {}), **(step.get("env") or {})}
    assert env.get("BUREAU_READ_URL") == "${{ vars.BUREAU_READ_URL }}"


# ── the clear-notices job, executed ──────────────────────────────────────────


def _runs(*rows) -> str:
    return json.dumps([
        {"databaseId": i + 1, "workflowName": wf, "status": status,
         "conclusion": conclusion, "event": event}
        for i, (wf, status, conclusion, event) in enumerate(rows)])


DECIDE_ENV = {"HEAD_SHA": HEAD_SHA, "GITHUB_RUN_ID": "999"}


def _decide(tmp_path, runs: str, **extra):
    return _run_step(tmp_path, _clear_step("red_main_clear.py decide"),
                     {**DECIDE_ENV, "FAKE_RUNS": runs, **extra},
                     real=("red_main_clear.py",))


def test_the_decide_step_goes_when_every_run_is_green(tmp_path):
    done, calls, _, output = _decide(tmp_path, _runs(
        ("Pipeline Tests", "completed", "success", "push"),
        ("Integration Harness", "completed", "skipped", "push")))
    assert done.returncode == 0, done.stderr
    assert "go=true" in output.splitlines()
    gh = [c for c in calls if c["tool"] == "gh"][0]["args"]
    assert gh[:2] == ["run", "list"]
    assert _flag(gh, "--commit") == HEAD_SHA
    assert _flag(gh, "--repo") == REPO


def test_the_decide_step_stops_beside_a_red_run(tmp_path):
    done, _, _, output = _decide(tmp_path, _runs(
        ("Pipeline Tests", "completed", "success", "push"),
        ("Integration Harness", "completed", "failure", "push")))
    assert done.returncode == 0, done.stderr
    assert "go=false" in output.splitlines()


def test_the_decide_step_stops_when_the_runs_cannot_be_read(tmp_path):
    done, _, _, output = _decide(tmp_path, "[]", FAKE_GH_RC="1")
    assert done.returncode == 0, done.stderr
    assert "go=false" in output.splitlines()


def test_the_resolve_step_closes_both_keys_with_the_recovery_copy(tmp_path):
    env = {"WF_NAME": "Pipeline Tests", "RUN_URL": GREEN_URL,
           "RUN_UPDATED_AT": "2026-10-16T21:05:00Z",
           "FAKE_OUTCOME": "not-open"}
    done, calls, _, _ = _run_step(
        tmp_path, _clear_step("pipeline_notice.py resolve"), env,
        real=("red_main_clear.py",))
    assert done.returncode == 0, done.stderr + done.stdout
    resolves = [c for c in calls if c["tool"] == "pipeline_notice"]
    assert [_flag(c["args"], "--notice") for c in resolves] == [
        f"red_main:{SLUG}:budget_exhausted", f"red_main:{SLUG}:needs_decision"]
    expected = (f"Pipeline Tests passed on the default branch of {SLUG} at "
                f"14:05 PT, so the red main this alert reported has cleared. "
                f"Run: {GREEN_URL}")
    for c in resolves:
        assert c["args"][0] == "resolve"
        assert _flag(c["args"], "--recovery-title") == f"Main is green again in {SLUG}"
        assert c["files"]["--recovery-body-file"].strip() == expected


# ── scripts/red_main_clear.py ────────────────────────────────────────────────


def _run(conclusion, status="completed", *, event="push", run_id=1,
         workflow="Pipeline Tests") -> dict:
    return {"databaseId": run_id, "workflowName": workflow, "status": status,
            "conclusion": conclusion, "event": event}


def test_go_when_every_run_concluded_success_skipped_or_neutral():
    verdict = red_main_clear.judge([
        _run("success", run_id=1), _run("skipped", run_id=2),
        _run("neutral", run_id=3)])
    assert verdict.go is True
    assert verdict.reason == "all-green"


def test_stop_when_a_run_failed():
    verdict = red_main_clear.judge([_run("success", run_id=1),
                                    _run("failure", run_id=2)])
    assert verdict.go is False
    assert verdict.reason == "red"


def test_stop_when_a_run_timed_out():
    verdict = red_main_clear.judge([_run("success", run_id=1),
                                    _run("timed_out", run_id=2)])
    assert verdict.go is False
    assert verdict.reason == "red"


@pytest.mark.parametrize("status", ["in_progress", "queued", "waiting", "pending"])
def test_stop_when_a_run_has_not_completed(status):
    verdict = red_main_clear.judge([_run("success", run_id=1),
                                    _run("", status, run_id=2)])
    assert verdict.go is False
    assert verdict.reason == "unfinished"


def test_this_run_itself_is_not_waited_on():
    verdict = red_main_clear.judge(
        [_run("success", run_id=1), _run("", "in_progress", run_id=999)],
        self_run_id="999")
    assert verdict.go is True


def test_a_run_started_by_another_runs_completion_does_not_judge_the_commit():
    """The repair rail, the medic and every other `workflow_run` listener run
    on the default branch's head sha whenever any watched run completes — the
    other watched workflow's Red-Main Repair run among them. They do not
    validate the commit, and waiting on them would never send an all-clear."""
    verdict = red_main_clear.judge([
        _run("success", run_id=1),
        _run("", "in_progress", event="workflow_run", run_id=2,
             workflow="Red-Main Repair"),
        _run("failure", event="workflow_run", run_id=3, workflow="Medic")])
    assert verdict.go is True


def test_stop_when_there_is_nothing_to_judge():
    assert red_main_clear.judge([]).go is False
    assert red_main_clear.judge([]).reason == "no-runs"


def test_stop_when_the_listing_is_not_a_list_of_runs():
    for raw in ("", "unreadable", "{}", "[1, 2]"):
        assert red_main_clear.judge_text(raw).go is False, raw
        assert red_main_clear.judge_text(raw).reason == "unreadable", raw


def test_red_outranks_unfinished():
    verdict = red_main_clear.judge([_run("failure", run_id=1),
                                    _run("", "in_progress", run_id=2)])
    assert verdict.reason == "red"


def test_the_recovery_body_names_the_workflow_the_time_in_pt_and_the_run():
    at = datetime(2026, 10, 16, 21, 5, tzinfo=timezone.utc)
    assert red_main_clear.recovery_body(
        workflow="Pipeline Tests", repo=SLUG, run_url=GREEN_URL, at=at) == (
        f"Pipeline Tests passed on the default branch of {SLUG} at 14:05 PT, "
        f"so the red main this alert reported has cleared. Run: {GREEN_URL}")


def test_the_recovery_body_reads_winter_time_too():
    at = datetime(2026, 12, 1, 18, 30, tzinfo=timezone.utc)
    body = red_main_clear.recovery_body(
        workflow="Pipeline Tests", repo=SLUG, run_url=GREEN_URL, at=at)
    assert " at 10:30 PT," in body


def test_the_cli_writes_go_and_reason_lines(tmp_path):
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps([_run("success")]))
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "red_main_clear.py"), "decide",
         "--runs-file", str(runs)],
        capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[:2] == ["go=true", "reason=all-green"]


# ── the writers report ───────────────────────────────────────────────────────


def test_the_writers_report_reads_both_red_main_writers_as_repointed():
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "notice_writers.py"), "report"],
        capture_output=True, text=True, check=False)
    assert done.returncode == 0
    lines = done.stdout.splitlines()
    assert "notice-writers: red-main-budget repointed" in lines
    assert "notice-writers: red-main-decision repointed" in lines
