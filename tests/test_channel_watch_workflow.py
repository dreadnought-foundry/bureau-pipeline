"""The Channel Watch run (DRE-8655): `.github/workflows/channel-watch.yml`.

`scripts/hung_job.py` (DRE-8652) cancels a `Pipeline Tests` run on main that
holds a hung job and re-runs it once; `scripts/channel_hold.py` (DRE-8653)
diagnoses a held `stable` channel and raises or resolves its console alert.
This file holds the workflow that runs the two every 10 minutes to the
modules' contracts. Every test reads the LIVE workflow file. The shell that
decides whether the hung-job pass applies is EXECUTED under bash with a stub
`python3` on the PATH, over the value its expression yields for each setting
of `HUNG_JOB_WATCH`, so the answer is what the runner would do.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import channel_hold  # noqa: E402
import check_workflow_watchers  # noqa: E402
import hung_job  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "channel-watch.yml"
MEDIC_STUB = WORKFLOWS / "self-medic.yml"
NIGHTLY_WATCH = WORKFLOWS / "nightly-watch.yml"
PROOF_REACH = ROOT / "config" / "proof-reach.json"
SELF_HOSTING = ROOT / "docs" / "self-hosting.md"

NAME = "Channel Watch"
CRON = "4-59/10 * * * *"
APP_ACTION = "actions/create-github-app-token"


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on(doc):
    return check_workflow_watchers.on_block(doc)


def _job(name: str) -> dict:
    return _doc()["jobs"][name]


def _steps_running(job: str, script: str) -> list:
    return [s for s in _job(job)["steps"] if script in str(s.get("run") or "")]


def _one_step_running(job: str, script: str) -> dict:
    found = _steps_running(job, script)
    assert len(found) == 1, f"{script} is not run by exactly one step of {job}"
    return found[0]


def _bash(script: str, env: dict, cwd: Path):
    """Run one step's `run:` the way the runner does (`bash -e`), with a
    `python3` on the PATH that records its argv, NUL-separated, one file per
    call."""
    stub_dir = cwd / ".stub"
    stub_dir.mkdir(exist_ok=True)
    calls = cwd / ".calls"
    calls.mkdir(exist_ok=True)
    for old in calls.iterdir():
        old.unlink()
    stub = stub_dir / "python3"
    stub.write_text("#!/bin/sh\n"
                    f'n=$(ls "{calls}" | wc -l)\n'
                    f'printf "%s\\0" "$@" > "{calls}/$n"\n'
                    "exit 0\n")
    stub.chmod(0o755)
    full_env = {"PATH": f"{stub_dir}:{os.environ['PATH']}", **env}
    result = subprocess.run(["bash", "-e", "-c", script], cwd=cwd, env=full_env,
                            capture_output=True, text=True)
    argvs = [(calls / str(i)).read_bytes().decode().split("\0")[:-1]
             for i in range(len(list(calls.iterdir())))]
    return result, argvs


def _evaluate(expr: str, values: dict):
    """A GitHub expression over the names in `values`, each put in as its
    literal and the operators read as Python's. A name left over fails to
    evaluate, so an expression reading something this test does not know is a
    failure, never a pass."""
    body = expr.strip()
    assert body.startswith("${{") and body.endswith("}}"), expr
    body = body[3:-2]
    for name, value in sorted(values.items(), key=lambda kv: -len(kv[0])):
        body = body.replace(name, repr(value))
    body = body.replace("&&", " and ").replace("||", " or ")
    body = re.sub(r"!(?!=)", " not ", body)
    return eval(body, {"__builtins__": {}}, {})  # noqa: S307 — test-only, literals


# ---------------------------------------------------------------- triggers


def test_the_workflow_is_named_channel_watch():
    assert _doc()["name"] == NAME


def test_it_runs_every_ten_minutes_off_the_hour():
    on = _on(_doc())
    assert on["schedule"] == [{"cron": CRON}]


def test_the_cron_minutes_are_the_modules_pass_minutes():
    """`hung_job.replay` names the pass that would have canceled a job from
    `PASS_MINUTES`; a cron that drifts from them makes every replay wrong."""
    minute = _on(_doc())["schedule"][0]["cron"].split()[0]
    start_end, step = minute.split("/")
    start, end = (int(x) for x in start_end.split("-"))
    assert tuple(range(start, end + 1, int(step))) == hung_job.PASS_MINUTES


def test_the_interval_handed_to_the_hold_check_is_the_cron_step():
    minute = _on(_doc())["schedule"][0]["cron"].split()[0]
    step = int(minute.split("/")[1])
    run = str(_one_step_running("hold", "scripts/channel_hold.py")["run"])
    assert f"--interval-minutes {step}" in run


def test_it_can_be_dispatched_by_hand_with_no_inputs():
    on = _on(_doc())
    assert "workflow_dispatch" in on
    assert not (on["workflow_dispatch"] or {}).get("inputs")


def test_two_passes_never_overlap():
    assert _doc()["concurrency"] == {"group": "channel-watch",
                                     "cancel-in-progress": False}


def test_the_workflow_grants_only_contents_read():
    assert _doc()["permissions"] == {"contents": "read"}


# ---------------------------------------------------------------- hung-jobs


def test_hung_jobs_has_room_for_a_cancel_to_land():
    assert _job("hung-jobs")["timeout-minutes"] == 15


def _app_step() -> dict:
    found = [s for s in _job("hung-jobs")["steps"]
             if str(s.get("uses") or "").startswith(APP_ACTION + "@")]
    assert len(found) == 1, "hung-jobs does not mint exactly one App token"
    return found[0]


def test_hung_jobs_mints_the_bureau_app_token():
    step = _app_step()
    assert step["with"]["app-id"] == "${{ secrets.BUREAU_APP_ID }}"
    assert step["with"]["private-key"] == "${{ secrets.BUREAU_APP_PRIVATE_KEY }}"
    assert step.get("id"), "the mint has no id, so its token cannot be read"


def test_the_checkout_comes_before_the_watch():
    steps = _job("hung-jobs")["steps"]
    checkout = next(i for i, s in enumerate(steps)
                    if str(s.get("uses") or "").startswith("actions/checkout@"))
    watch = steps.index(_one_step_running("hung-jobs", "scripts/hung_job.py"))
    mint = steps.index(_app_step())
    assert checkout < watch and mint < watch


def test_hung_job_gets_the_app_token_as_gh_token():
    """A re-run asked for with GITHUB_TOKEN starts no workflow when it
    finishes, so Promote Channel would never wake (vendor boundary Q1)."""
    step = _one_step_running("hung-jobs", "scripts/hung_job.py")
    app_id = _app_step()["id"]
    assert step["env"]["GH_TOKEN"] == f"${{{{ steps.{app_id}.outputs.token }}}}"


def test_no_step_of_hung_jobs_hands_hung_job_the_workflow_token():
    for step in _steps_running("hung-jobs", "scripts/hung_job.py"):
        text = yaml.safe_dump(step)
        assert "github.token" not in text
        assert "secrets.GITHUB_TOKEN" not in text


def test_hung_job_watches_this_repository():
    run = str(_one_step_running("hung-jobs", "scripts/hung_job.py")["run"])
    assert 'scripts/hung_job.py watch --repo "$GITHUB_REPOSITORY"' in run


def _hung_job_argv(setting: str, tmp_path: Path) -> list:
    step = _one_step_running("hung-jobs", "scripts/hung_job.py")
    env = {}
    for key, expr in (step.get("env") or {}).items():
        if "vars.HUNG_JOB_WATCH" in str(expr):
            env[key] = str(_evaluate(str(expr), {"vars.HUNG_JOB_WATCH": setting})).lower()
        else:
            env[key] = "token"
    env["GITHUB_REPOSITORY"] = "dreadnought-foundry/bureau-pipeline"
    result, argvs = _bash(str(step["run"]), env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert len(argvs) == 1, argvs
    assert argvs[0][:4] == ["scripts/hung_job.py", "watch", "--repo",
                            "dreadnought-foundry/bureau-pipeline"]
    return argvs[0]


def test_the_off_switch_is_read_from_the_repository_variable():
    step = _one_step_running("hung-jobs", "scripts/hung_job.py")
    assert any("vars.HUNG_JOB_WATCH != 'off'" in str(v)
               for v in (step.get("env") or {}).values())


@pytest.mark.parametrize("setting", ["", "on", "true", "anything"])
def test_unset_or_anything_but_off_applies(setting, tmp_path):
    assert "--apply" in _hung_job_argv(setting, tmp_path)


def test_off_leaves_the_watch_reporting_without_apply(tmp_path):
    assert "--apply" not in _hung_job_argv("off", tmp_path)


# --------------------------------------------------------------------- hold


def test_hold_is_short():
    assert _job("hold")["timeout-minutes"] == 5


def test_hold_holds_the_notice_doors_token_and_reads_only():
    assert _job("hold")["permissions"] == {"id-token": "write",
                                           "actions": "read",
                                           "contents": "read"}


def test_hold_runs_the_watch_live_at_the_cron_interval():
    run = " ".join(str(_one_step_running("hold", "scripts/channel_hold.py")["run"]).split())
    assert ('python3 scripts/channel_hold.py watch --repo "$GITHUB_REPOSITORY" '
            "--interval-minutes 10 --apply") in run


def test_hold_passes_the_hold_and_the_notice_doors_settings():
    env = _one_step_running("hold", "scripts/channel_hold.py")["env"]
    assert env["GH_TOKEN"] == "${{ github.token }}"
    assert env["CHANNEL_HOLD"] == "${{ vars.CHANNEL_HOLD }}"


def test_hold_passes_the_door_and_fallback_exactly_as_nightly_watch_does():
    nightly = yaml.safe_load(NIGHTLY_WATCH.read_text(encoding="utf-8"))
    notice = next(s for s in nightly["jobs"]["watch"]["steps"]
                  if s.get("name") == "Raise the alarm on the console")
    env = _one_step_running("hold", "scripts/channel_hold.py")["env"]
    for key in ("BUREAU_READ_URL", "LINEAR_API_KEY"):
        assert env[key] == notice["env"][key]


def test_the_hold_check_never_gets_the_app_token():
    assert not [s for s in _job("hold")["steps"]
                if str(s.get("uses") or "").startswith(APP_ACTION + "@")]


# ------------------------------------------------------------------ records


def test_the_medic_watches_channel_watch():
    stub = yaml.safe_load(MEDIC_STUB.read_text(encoding="utf-8"))
    assert NAME in _on(stub)["workflow_run"]["workflows"]


def test_the_watcher_check_passes():
    assert check_workflow_watchers.main([]) == 0


@pytest.mark.parametrize("module,script,job", [
    (hung_job, "scripts/hung_job.py", "hung-jobs"),
    (channel_hold, "scripts/channel_hold.py", "hold"),
])
def test_proof_reach_keeps_both_log_tags(module, script, job):
    lines = json.loads(PROOF_REACH.read_text(encoding="utf-8"))["log_lines"]
    found = [e for e in lines if e["printed_by"] == script]
    assert len(found) == 1, f"{script} has no single log_lines entry"
    entry = found[0]
    assert entry["tag"] == module.TAG
    assert entry["constant"] == "TAG"
    assert NAME in entry["when"] and f"{job} job" in entry["when"]


def test_self_hosting_says_what_the_watch_does():
    text = " ".join(SELF_HOSTING.read_text(encoding="utf-8").split())
    quiet = text.index("A quiet channel says so")
    after = text[quiet:]
    para = after[:after.index("Every move is a Linear release")]
    for needle in ("Channel Watch", "HUNG_JOB_WATCH",
                   "pipeline_notice:channel_hold:stable",
                   "2 hours", "06:00", "06:29 PT", "re-run once"):
        assert needle in para, needle


def test_hung_job_is_handed_the_ci_column_as_github_evaluates_it_at_re_run_time():
    """DRE-8817: the watch never re-runs a hung job while the `ci` column
    still names the service it hung on, and reads that column here."""
    env = _one_step_running("hung-jobs", "scripts/hung_job.py")["env"]
    assert env["CI_RUNS_ON"] == (
        "${{ vars.BUREAU_CI_RUNS_ON || vars.BUREAU_RUNS_ON || '[\"ubuntu-latest\"]' }}")
    assert "SHORT_RUNS_ON" not in env and "LONG_RUNS_ON" not in env
