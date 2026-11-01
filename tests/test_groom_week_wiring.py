"""RED-first: the Sunday job (DRE-7975).

`.github/workflows/groom-week.yml` runs once a week, before the 06:30 PT
Sunday briefing, and does three things in order: it replays the replay set on
`main` (`replay-main`, a call of `groomer.yml` with `replay` set), posts the
week's groomer scorecard on the standing groom card
(`groom_scorecard.py score`, DRE-7970), records where each case ended on
`main` (`groom_replay_gate.py record-main`, DRE-7972) and adds the week's
recorded removals to the cases (`groom_replay_cases.py build`, DRE-7971).
What changed under `tests/fixtures/groom-replay-cases/` reaches `main` by one
pull request, never by a push.

What this file holds shut:

  * the clock: `11 10 * * 0`, on a minute no other workflow file fires on at
    any hour — every other cron's minute field is expanded, and a copy of the
    workflows with a cron on minute 11 fails, naming its file;
  * `replay-main`: its condition on `cases`, its call of the local
    `groomer.yml` with the replay set at `main`, and the secrets and variables
    `self-groomer.yml`'s `call` job passes;
  * `week`: `if: always()`, the three commands in order with their arguments,
    the read door's settings in their environment, and the top-level
    permissions exactly `contents: read` and `id-token: write`;
  * the `week` step's shell, driven with stub scripts: a skipped or failed
    replay still scores, never records, and a failed one says so;
  * the pull request step, driven against a throwaway origin and a stub `gh`:
    only the cases' paths are staged, the branch is
    `agent/groom-week-<week_end>`, a branch left by a crash is reused, and an
    open pull request is updated, never duplicated;
  * that branch is one the pipeline owns: `reconcile.pipeline_owns` and
    `fix_eligible` say yes, and `evaluate_and_merge.sh`'s branch case matches
    it, so the merge gate and the fix loop act on the week's pull request.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_week_wiring.py -q
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_replay_gate  # noqa: E402
import reconcile  # noqa: E402

WEEK = "groom-week.yml"
CRON = "11 10 * * 0"
MINUTE = 11
LOCAL_GROOMER = "./.github/workflows/groomer.yml"
CASES_DIR = "tests/fixtures/groom-replay-cases"
REPLAY_SET = f"{CASES_DIR}/replay-set.json"
SHORT_RUNS_ON = ("${{ fromJSON(vars.BUREAU_SHORT_RUNS_ON || vars.BUREAU_RUNS_ON "
                 "|| '[\"ubuntu-latest\"]') }}")

WHICH_WEEK_STEP = "Which week"
DOWNLOAD_STEP = "Download the main replay's verified proposal"
WEEK_STEP = "Score the week, record main, build the cases"
PR_STEP = "Open or update the week's pull request"

#: The three commands of the `week` step, in the order they run, each with
#: the arguments the card's contract gives it.
SCORE = ('python3 scripts/groom_scorecard.py score '
         '--card "${{ vars.GROOM_PROPOSAL_CARD }}" --post')
RECORD = ('python3 scripts/groom_replay_gate.py record-main '
          '--proposal replay/verified/proposal-verified.json --at "$REPLAY_AT"')
BUILD = "python3 scripts/groom_replay_cases.py build"

#: The read door's settings, as DRE-7821 handed them to `propose`.
DOOR_ENV = {
    "BUREAU_READ_URL": "${{ vars.BUREAU_READ_URL }}",
    "BUREAU_READ_AUDIENCE": "${{ vars.BUREAU_READ_AUDIENCE }}",
    "BUREAU_PIPELINE_REF": "main",
}

DID_NOT_FINISH = ("groom-replay-gate: main replay did not finish — results "
                  "not recorded")


# --------------------------------------------------------------------------
# reading the YAML
# --------------------------------------------------------------------------
def _load(name: str, workflows: Path = WORKFLOWS) -> dict:
    path = workflows / name
    assert path.is_file(), f"missing workflow {name}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    # PyYAML (YAML 1.1) reads a bare `on:` key as the boolean True.
    on = doc.get("on", doc.get(True))
    if isinstance(on, dict):
        return on
    if isinstance(on, list):
        return {event: None for event in on}
    if isinstance(on, str):
        return {on: None}
    return {}


def _job(name: str) -> dict:
    return _load(WEEK)["jobs"][name]


def _step(job: dict, name: str) -> dict:
    for step in job.get("steps") or []:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


def _expression(value) -> str:
    text = " ".join(str(value or "").split())
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    return text.strip()


def _render(run: str, values: dict) -> str:
    """The step's shell with each `${{ … }}` replaced as GitHub would, from
    `values`; an expression the test did not name is a test error."""
    def one(match):
        expr = " ".join(match.group(1).split())
        if expr not in values:
            raise AssertionError(f"the shell interpolates {expr!r}")
        return values[expr]
    return re.sub(r"\$\{\{(.*?)\}\}", one, run)


# --------------------------------------------------------------------------
# the clock
# --------------------------------------------------------------------------
def cron_minutes(field: str) -> set[int]:
    """The minutes of the hour a cron's minute field fires on: one number, a
    list (`4,19,34,49`), a range (`1-15`), a step (`*/15`, `5/20`, `0-30/10`)
    or `*`."""
    minutes: set[int] = set()
    for part in field.split(","):
        base, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if base == "*":
            lo, hi = 0, 59
        elif "-" in base:
            lo_text, hi_text = base.split("-", 1)
            lo, hi = int(lo_text), int(hi_text)
        else:
            lo = int(base)
            hi = 59 if step_text else lo
        minutes.update(range(lo, hi + 1, step))
    return minutes


def files_firing_on(minute: int, workflows: Path = WORKFLOWS,
                    own: str = WEEK) -> list[str]:
    """Every workflow file but `own` with a cron that fires on `minute` at
    any hour."""
    hits = []
    for path in sorted(workflows.glob("*.y*ml")):
        if path.name == own:
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        for entry in _on(doc).get("schedule") or []:
            fields = str((entry or {}).get("cron") or "").split()
            if fields and minute in cron_minutes(fields[0]):
                hits.append(path.name)
                break
    return hits


def check_minute_is_clear(workflows: Path = WORKFLOWS) -> None:
    hits = files_firing_on(MINUTE, workflows)
    assert not hits, (f"minute {MINUTE} is also fired on by {', '.join(hits)} — "
                      "the Sunday job's cron must sit on a minute of its own "
                      "(standards/engineering.md, CI rule 2)")


@pytest.mark.parametrize("field, minutes", [
    ("11", {11}),
    ("*/15", {0, 15, 30, 45}),
    ("4,19,34,49", {4, 19, 34, 49}),
    ("1-3", {1, 2, 3}),
    ("5/20", {5, 25, 45}),
    ("0-30/10", {0, 10, 20, 30}),
    ("*", set(range(60))),
])
def test_the_minute_field_expands_to_the_minutes_it_fires_on(field, minutes):
    assert cron_minutes(field) == minutes


def test_the_trigger_is_the_sunday_cron_and_a_dispatch_with_week_ending():
    on = _on(_load(WEEK))
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert [e.get("cron") for e in on["schedule"]] == [CRON]
    inputs = (on["workflow_dispatch"] or {}).get("inputs") or {}
    assert set(inputs) == {"week_ending"}
    assert inputs["week_ending"].get("required") in (None, False)
    assert inputs["week_ending"].get("default", "") == ""


def test_no_other_workflow_fires_on_the_sunday_job_s_minute():
    check_minute_is_clear()


@pytest.mark.parametrize("cron", ["11 3 * * *", "4,11,40 * * * *",
                                  "*/11 * * * *", "1-15 6 * * 1"])
def test_a_copy_with_another_cron_on_minute_11_fails_naming_that_file(
        tmp_path, cron):
    copy = tmp_path / "workflows"
    shutil.copytree(WORKFLOWS, copy)
    doc = yaml.safe_load((copy / "hygiene.yml").read_text(encoding="utf-8"))
    on = _on(doc)
    on["schedule"] = [*(on.get("schedule") or []), {"cron": cron}]
    doc.pop(True, None)
    doc["on"] = on
    (copy / "hygiene.yml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(AssertionError, match=r"hygiene\.yml"):
        check_minute_is_clear(copy)


def test_the_cron_is_more_than_an_hour_clear_of_the_train_stubs():
    minute, hour = (int(f) for f in CRON.split()[:2])
    at = hour * 60 + minute
    for train in (12 * 60, 13 * 60):
        assert abs(at - train) > 60


# --------------------------------------------------------------------------
# the shape
# --------------------------------------------------------------------------
def test_the_permissions_are_exactly_contents_read_and_id_token_write():
    doc = _load(WEEK)
    assert doc.get("permissions") == {"contents": "read", "id-token": "write"}
    for name, job in doc["jobs"].items():
        assert "permissions" not in job, f"job {name!r} widens the permissions"


def test_the_three_jobs_and_their_order():
    jobs = _load(WEEK)["jobs"]
    assert list(jobs) == ["count", "replay-main", "week"]
    assert jobs["replay-main"]["needs"] in ("count", ["count"])
    assert jobs["week"]["needs"] == ["count", "replay-main"]


def test_count_outputs_the_number_of_cases_and_the_replay_s_start():
    job = _job("count")
    assert set(job["outputs"]) == {"cases", "started_at"}
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout.get("with", {}).get("ref") == "main"


def test_count_s_step_counts_the_cases_file(tmp_path):
    job = _job("count")
    step = next(s for s in job["steps"] if s.get("id") == "count")
    (tmp_path / CASES_DIR).mkdir(parents=True)
    shutil.copy(ROOT / CASES_DIR / "cases.json", tmp_path / CASES_DIR)
    out = tmp_path / "out"
    subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, check=True,
                   env={**os.environ, "GITHUB_OUTPUT": str(out)})
    lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
    cases = json.loads((ROOT / CASES_DIR / "cases.json").read_text())["cases"]
    assert lines["cases"] == str(len(cases)) != "0"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", lines["started_at"])


def test_replay_main_keys_on_cases_and_calls_the_local_groomer_on_main():
    job = _job("replay-main")
    assert _expression(job["if"]) == "needs.count.outputs.cases != '0'"
    assert job["uses"] == LOCAL_GROOMER
    assert job["with"] == {
        "pipeline_ref": "main",
        "mode": "propose",
        "judgement": "off",
        "capacity": "20",
        "replay": REPLAY_SET,
        "intake_hold": "${{ vars.INTAKE_HOLD }}",
    }
    assert "card" not in job["with"]


def test_replay_main_passes_every_secret_and_variable_the_call_job_passes():
    call = _load("self-groomer.yml")["jobs"]["call"]
    job = _job("replay-main")
    assert job.get("secrets") == call.get("secrets") == "inherit"
    for key, value in call["with"].items():
        if "vars." in str(value):
            assert job["with"].get(key) == value, key


def test_the_replay_queues_in_groomer_yml_s_replay_group():
    """DRE-7974's replay group: a run with `replay` set never queues behind
    the morning run's `groomer` / `groomer-post` groups."""
    assert _job("replay-main")["with"]["replay"]
    groomer = _load("groomer.yml")["jobs"]
    for job, group in (("groom", "groomer-replay-{0}"),
                       ("post", "groomer-post-replay-{0}")):
        expr = _expression(groomer[job]["concurrency"]["group"])
        assert expr.startswith("inputs.replay != '' && "), job
        assert group in expr and "github.run_id" in expr, job


def test_the_replay_set_is_on_main_and_never_a_verify_step_path():
    """The pull request touches only the cases, so the replay gate's `reach`
    is false for it."""
    assert (ROOT / REPLAY_SET).is_file()
    assert not any(p.startswith(CASES_DIR) for p in groom_replay_gate.VERIFY_STEP_PATHS)


def test_week_runs_always_after_both():
    job = _job("week")
    assert _expression(job["if"]) == "always()"
    assert job["runs-on"] == SHORT_RUNS_ON
    assert isinstance(job.get("timeout-minutes"), int)


def test_the_app_token_is_minted_before_checkout_and_checkout_is_main():
    steps = _job("week")["steps"]
    assert steps[0].get("id") == "app"
    assert steps[0]["uses"].startswith("actions/create-github-app-token@")
    assert steps[0]["with"] == {
        "app-id": "${{ secrets.BUREAU_APP_ID }}",
        "private-key": "${{ secrets.BUREAU_APP_PRIVATE_KEY }}",
    }
    assert steps[1]["uses"].startswith("actions/checkout@")
    assert steps[1]["with"]["ref"] == "main"
    assert steps[1]["with"]["token"] == "${{ steps.app.outputs.token }}"


def test_the_verified_proposal_is_downloaded_only_after_a_replay_that_succeeded():
    step = _step(_job("week"), DOWNLOAD_STEP)
    assert _expression(step["if"]) == "needs.replay-main.result == 'success'"
    assert step["uses"].startswith("actions/download-artifact@")
    assert step["with"] == {"name": "groom-proposal-verified",
                            "path": "replay/verified"}


def test_the_week_step_runs_the_three_commands_in_order():
    run = _step(_job("week"), WEEK_STEP)["run"]
    flat = " ".join(run.split())
    at = [flat.index(command) for command in (SCORE, RECORD, BUILD)]
    assert at == sorted(at), "score, then record-main, then build"
    assert flat.count("groom_scorecard.py") == 1
    assert flat.count("groom_replay_gate.py") == 1
    assert flat.count("groom_replay_cases.py") == 1


def test_the_week_step_holds_the_door_settings_and_the_linear_key():
    env = _step(_job("week"), WEEK_STEP)["env"]
    for name, value in DOOR_ENV.items():
        assert env.get(name) == value, name
    assert env["LINEAR_API_KEY"] == "${{ secrets.LINEAR_API_KEY }}"
    assert env["GROOM_PROPOSAL_CARD"] == "${{ vars.GROOM_PROPOSAL_CARD }}"
    assert env["REPLAY_RESULT"] == "${{ needs.replay-main.result }}"
    assert env["REPLAY_AT"] == "${{ needs.count.outputs.started_at }}"
    for secret in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        assert secret not in env


# --------------------------------------------------------------------------
# the week, driven with stub scripts
# --------------------------------------------------------------------------
STUB = """\
import json, os, sys
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("STUB_EXIT_" + os.path.basename(sys.argv[0]).split(".")[0].upper(), "0")))
"""


def _drive_week(tmp_path: Path, result: str, *, week_ending: str = "",
                exits: dict | None = None):
    (tmp_path / "scripts").mkdir()
    for name in ("groom_scorecard.py", "groom_replay_gate.py",
                 "groom_replay_cases.py"):
        (tmp_path / "scripts" / name).write_text(STUB)
    log = tmp_path / "log.jsonl"
    run = _render(_step(_job("week"), WEEK_STEP)["run"],
                  {"vars.GROOM_PROPOSAL_CARD": "DRE-4541"})
    env = {**os.environ, "STUB_LOG": str(log), "REPLAY_RESULT": result,
           "REPLAY_AT": "2026-10-25T10:11:40Z", "WEEK_ENDING": week_ending,
           "WEEK_END": week_ending or "2026-10-24"}
    for name, code in (exits or {}).items():
        env[f"STUB_EXIT_{name.upper()}"] = str(code)
    done = subprocess.run(["bash", "-c", run], cwd=tmp_path, env=env,
                          capture_output=True, text=True)
    calls = ([json.loads(line) for line in log.read_text().splitlines()]
             if log.exists() else [])
    return done, calls


def test_a_replay_that_succeeded_scores_records_and_builds(tmp_path):
    done, calls = _drive_week(tmp_path, "success")
    assert done.returncode == 0, done.stderr
    assert calls == [
        ["groom_scorecard.py", "score", "--card", "DRE-4541", "--post"],
        ["groom_replay_gate.py", "record-main", "--proposal",
         "replay/verified/proposal-verified.json", "--at", "2026-10-25T10:11:40Z"],
        ["groom_replay_cases.py", "build"],
    ]
    assert DID_NOT_FINISH not in done.stdout


@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_a_replay_that_did_not_finish_scores_and_builds_and_records_nothing(
        tmp_path, result):
    done, calls = _drive_week(tmp_path, result)
    assert done.returncode == 0, done.stderr
    assert [c[0] for c in calls] == ["groom_scorecard.py", "groom_replay_cases.py"]
    assert DID_NOT_FINISH in done.stdout


def test_a_skipped_replay_scores_and_builds_and_records_nothing(tmp_path):
    done, calls = _drive_week(tmp_path, "skipped")
    assert done.returncode == 0, done.stderr
    assert [c[0] for c in calls] == ["groom_scorecard.py", "groom_replay_cases.py"]
    assert DID_NOT_FINISH not in done.stdout
    assert "groom-replay-gate: main replay was skipped" in done.stdout


def test_a_week_ending_from_the_dispatch_reaches_the_score(tmp_path):
    done, calls = _drive_week(tmp_path, "success", week_ending="2026-10-17")
    assert done.returncode == 0, done.stderr
    assert calls[0] == ["groom_scorecard.py", "score", "--card", "DRE-4541",
                        "--post", "--week-ending", "2026-10-17"]


def test_a_score_that_failed_writes_nothing_else(tmp_path):
    done, calls = _drive_week(tmp_path, "success",
                              exits={"groom_scorecard": 1})
    assert done.returncode != 0
    assert [c[0] for c in calls] == ["groom_scorecard.py"]


# --------------------------------------------------------------------------
# which week
# --------------------------------------------------------------------------
def _which_week(tmp_path: Path, given: str):
    step = _step(_job("week"), WHICH_WEEK_STEP)
    assert step["env"]["WEEK_ENDING"] == "${{ inputs.week_ending }}"
    out = tmp_path / "out"
    done = subprocess.run(["bash", "-c", step["run"]], cwd=ROOT,
                          capture_output=True, text=True,
                          env={**os.environ, "GITHUB_OUTPUT": str(out),
                               "WEEK_ENDING": given})
    return done, (out.read_text() if out.exists() else "")


def test_which_week_is_the_given_saturday(tmp_path):
    done, out = _which_week(tmp_path, "2026-10-17")
    assert done.returncode == 0, done.stderr
    assert out == "end=2026-10-17\n"


def test_which_week_defaults_to_the_saturday_before_today(tmp_path):
    import groom_scorecard
    done, out = _which_week(tmp_path, "")
    assert done.returncode == 0, done.stderr
    assert out == f"end={groom_scorecard.default_week_ending()}\n"


@pytest.mark.parametrize("given", ["2026-10-18", "next week", "2026-10-17; rm -rf /"])
def test_which_week_refuses_anything_but_a_saturday(tmp_path, given):
    done, out = _which_week(tmp_path, given)
    assert done.returncode != 0
    assert out == ""


# --------------------------------------------------------------------------
# the pull request
# --------------------------------------------------------------------------
def test_the_pull_request_step_stages_only_the_cases_and_looks_before_it_creates():
    step = _step(_job("week"), PR_STEP)
    assert step["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"
    assert step["env"]["WEEK_END"] == "${{ steps.week.outputs.end }}"
    run = step["run"]
    adds = re.findall(r"git add\b[^\n]*", run)
    assert adds == [f"git add -- {CASES_DIR}/"]
    assert 'BRANCH="agent/groom-week-$WEEK_END"' in run
    listed = run.index('gh pr list --repo "$GITHUB_REPOSITORY" --head "$BRANCH" --state open')
    assert listed < run.index("gh pr create")
    assert listed < run.index("gh pr edit")
    assert "--base main" in run
    assert "git push origin HEAD:main" not in run
    assert re.search(r"git push\b[^\n]*refs/heads/\$BRANCH", run)



def _week_branch() -> str:
    """The branch the pull request step creates, for the week ending 2026-10-24."""
    run = _step(_job("week"), PR_STEP)["run"]
    found = re.search(r'^\s*BRANCH="([^"$]*)\$WEEK_END"\s*$', run, re.M)
    assert found, "the pull request step assigns no BRANCH from $WEEK_END"
    return found.group(1) + "2026-10-24"


def test_the_week_s_branch_is_one_the_pipeline_owns():
    # A branch outside the pipeline's set is skipped by the merge gate and
    # ignored by the fix loop, so the week's pull request would never merge.
    branch = _week_branch()
    assert reconcile.pipeline_owns(branch), branch
    assert reconcile.fix_eligible(branch), branch
    script = (ROOT / "scripts" / "evaluate_and_merge.sh").read_text()
    case = re.search(r'^case "\$BRANCH" in ([^\n]*?)\) ;; \*\) echo "not an agent branch',
                     script, re.M)
    assert case, "evaluate_and_merge.sh's branch case moved"
    done = subprocess.run(
        ["bash", "-c", f'case "$1" in {case.group(1)}) echo owned;; *) echo skip;; esac',
         "_", branch], capture_output=True, text=True)
    assert done.stdout.strip() == "owned", branch


GH_STUB = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["pr", "list"]:
    print(os.environ.get("GH_OPEN_PR", ""))
"""


def _case(case: str, card: str, *, expect="off-planning", reason="duplicate",
          main=None) -> dict:
    return {"case": case, "card": card, "expect": expect,
            "reason_label": reason, "main": main or
            {"ended": None, "at": None, "passes": None}}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    """A throwaway origin with `main` holding one case, and a clone of it."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "--quiet", str(origin), str(work))
    _git(work, "checkout", "--quiet", "-b", "main")
    (work / CASES_DIR).mkdir(parents=True)
    (work / "README").write_text("x\n")
    old = {"schema": "groom-replay-cases/1", "cases": [
        _case("seed-DRE-7239", "DRE-7239",
              main={"ended": None, "at": None, "passes": None})]}
    (work / CASES_DIR / "cases.json").write_text(json.dumps(old, indent=2))
    (work / CASES_DIR / "replay-set.json").write_text("{}\n")
    _git(work, "add", ".")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit",
         "--quiet", "-m", "main")
    _git(work, "push", "--quiet", "origin", "main")
    return origin, work


def _this_week(work: Path) -> None:
    """What the week step leaves: the seed recorded on main, one new case."""
    new = {"schema": "groom-replay-cases/1", "cases": [
        _case("seed-DRE-7239", "DRE-7239",
              main={"ended": "Cancel", "at": "2026-10-25T10:11:40Z",
                    "passes": True}),
        _case("abc123def456-DRE-9001", "DRE-9001", expect="off-cancel",
              reason="still wanted"),
    ]}
    (work / CASES_DIR / "cases.json").write_text(json.dumps(new, indent=2))
    (work / CASES_DIR / "replay-set.json").write_text('{"n": 2}\n')


def _drive_pr(tmp_path: Path, work: Path, *, open_pr: str = ""):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(GH_STUB)
    (bin_dir / "gh").chmod(0o755)
    temp = tmp_path / "runner-temp"
    temp.mkdir(exist_ok=True)
    log = tmp_path / "gh.jsonl"
    log.unlink(missing_ok=True)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "GH_LOG": str(log), "GH_OPEN_PR": open_pr, "GH_TOKEN": "t",
           "WEEK_END": "2026-10-24", "RUNNER_TEMP": str(temp),
           "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
           "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "42",
           "GIT_CONFIG_GLOBAL": "/dev/null"}
    done = subprocess.run(["bash", "-c", _step(_job("week"), PR_STEP)["run"]],
                          cwd=work, env=env, capture_output=True, text=True)
    calls = ([json.loads(line) for line in log.read_text().splitlines()]
             if log.exists() else [])
    return done, calls


def _arg(call: list, flag: str) -> str:
    return call[call.index(flag) + 1]


TITLE = ("groom-week: replay cases for the week ending 2026-10-24 — "
         "1 new, 1 recorded on main")


def test_a_week_with_changes_opens_one_pull_request_from_its_branch(tmp_path):
    origin, work = _repo(tmp_path)
    _this_week(work)
    done, calls = _drive_pr(tmp_path, work)
    assert done.returncode == 0, done.stderr
    assert [c[:2] for c in calls] == [["pr", "list"], ["pr", "create"]]
    create = calls[1]
    assert _arg(create, "--head") == "agent/groom-week-2026-10-24"
    assert _arg(create, "--base") == "main"
    assert _arg(create, "--title") == TITLE
    body = Path(_arg(create, "--body-file")).read_text()
    assert body.splitlines()[0] == "What's new: none"
    assert "| abc123def456-DRE-9001 | DRE-9001 | off-cancel | still wanted |" in body
    assert "seed-DRE-7239" in body and "Cancel" in body
    # Pushed to the branch, never to main, and only the cases' paths.
    changed = _git(origin, "diff", "--name-only", "main",
                   "agent/groom-week-2026-10-24").split()
    assert changed and all(p.startswith(f"{CASES_DIR}/") for p in changed)
    assert _git(origin, "log", "--format=%s", "-1", "main").strip() == "main"


def test_an_open_pull_request_is_updated_never_duplicated(tmp_path):
    origin, work = _repo(tmp_path)
    _this_week(work)
    done, calls = _drive_pr(tmp_path, work, open_pr="77")
    assert done.returncode == 0, done.stderr
    assert [c[:2] for c in calls] == [["pr", "list"], ["pr", "edit"]]
    assert calls[1][2] == "77"
    assert _arg(calls[1], "--title") == TITLE


def test_a_branch_a_crash_left_with_no_pull_request_is_reused(tmp_path):
    origin, work = _repo(tmp_path)
    # A crashed run pushed the branch and opened nothing.
    _git(work, "checkout", "--quiet", "-b", "agent/groom-week-2026-10-24")
    (work / CASES_DIR / "gate-set.json").write_text("{}\n")
    _git(work, "add", ".")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit",
         "--quiet", "-m", "crashed run")
    _git(work, "push", "--quiet", "origin", "agent/groom-week-2026-10-24")
    crashed = _git(work, "rev-parse", "HEAD").strip()
    _git(work, "checkout", "--quiet", "main")
    _git(work, "clean", "-fdq")
    _this_week(work)
    done, calls = _drive_pr(tmp_path, work)
    assert done.returncode == 0, done.stderr
    assert "reusing it" in done.stdout
    assert [c[:2] for c in calls] == [["pr", "list"], ["pr", "create"]]
    # Built on the crashed run's commit, never forced over it.
    _git(origin, "merge-base", "--is-ancestor", crashed, "agent/groom-week-2026-10-24")
    cases = json.loads(_git(origin, "show",
                            f"agent/groom-week-2026-10-24:{CASES_DIR}/cases.json"))
    assert [c["card"] for c in cases["cases"]] == ["DRE-7239", "DRE-9001"]


def test_a_week_with_no_changes_opens_nothing(tmp_path):
    origin, work = _repo(tmp_path)
    done, calls = _drive_pr(tmp_path, work)
    assert done.returncode == 0, done.stderr
    assert calls == []
    assert "no pull request" in done.stdout
    heads = _git(origin, "for-each-ref", "--format=%(refname)", "refs/heads")
    assert heads.split() == ["refs/heads/main"]


# --------------------------------------------------------------------------
# the rest of the repo's rules
# --------------------------------------------------------------------------
def test_the_medic_watches_it():
    assert _load(WEEK).get("name") == "Groom Week"
    watched = (_on(_load("self-medic.yml")).get("workflow_run") or {}).get(
        "workflows") or []
    assert "Groom Week" in watched


def test_two_runs_of_the_job_never_overlap():
    concurrency = _load(WEEK).get("concurrency") or {}
    assert concurrency.get("group") == "groom-week"
    assert concurrency.get("cancel-in-progress") is False


def test_the_doc_names_the_clock_the_outputs_the_rerun_and_the_widening():
    doc = (ROOT / "docs" / "groom-week.md").read_text(encoding="utf-8")
    for token in ("03:11 PT", "02:11 PT", CRON, "scorecard", "record-main",
                  "build", "pull request", "week_ending", "workflow_dispatch",
                  "D5", "DRE-6648", "DRE-7975"):
        assert token in doc, token
