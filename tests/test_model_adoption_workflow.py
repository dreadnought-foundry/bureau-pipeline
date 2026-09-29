"""RED-first tests for DRE-3898 — the model adoption workflow.

THE PROBLEM. The CEO gave three rules on 2026-09-14 for a model the catalog
offers that the pipeline has never configured, and two sibling cards built
them as code: `scripts/model_adoption.py` (DRE-3895) sorts every candidate into
`adopt` / `ignore` / `ask`, and `scripts/model_adoption_actions.py` (DRE-3903)
applies, renders and files. `model-trial.yml` (DRE-3897) runs a candidate for
real. Nothing ran any of it. `.github/workflows/model-adoption.yml` is the
schedule that makes the three rules happen without him:

  * `ignore` — a line in the summary table and nothing else;
  * `ask`    — ONE question card per candidate, for the CEO's queue;
  * `adopt`  — the FIRST candidate only, one in flight at a time: a trial,
               then (passed) a record card, the edit, a branch and an ordinary
               pull request, or (failed) ONE `Model trial failed:` card;
  * a `degraded` trial, or a pinned Claude Code that cannot run the candidate
    at full strength, opens DRE-5121's pin-raise pull request instead.

WHY SOME OF THIS FILE EXECUTES THE WORKFLOW'S SCRIPTS. "A day with no `adopt`
candidate runs no trial" and "an adoption already open holds the rail" are
claims about behaviour. The `run:` blocks are extracted from the YAML and run
under bash against synthetic Decision records and a fake `gh`, the discipline
`tests/test_model_trial_workflow.py` applies to the trial's own scoring script.
The rest parses the YAML, in the style of `tests/test_model_catalog.py`'s
workflow tests.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_adoption_workflow.py -v
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "model-adoption.yml"
MEDIC_STUB = WORKFLOWS / "self-medic.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import check_workflow_watchers as watchers  # noqa: E402
import claude_code_pin  # noqa: E402

NAME = "Model adoption"
CRON = "17 7 * * *"
TRIAL_USES = "./.github/workflows/model-trial.yml"

# Every write the workflow may make, and the only ones. `ignore` has none.
CARD_WRITES = ("open-question-card", "open-record-card", "linear_ops.py create")
GITHUB_WRITES = ("git push", "gh pr create")

# The adoption, in the order the card fixes (after the trial job).
ADOPT_ORDER = (
    "model_adoption_actions.py open-record-card",
    "model_adoption_actions.py apply",
    "python3 scripts/sync_model_config.py\n",
    "python3 scripts/sync_model_config.py --check",
    "git push",
    "gh pr create",
)


# --------------------------------------------------------------------------- #
# Reading the workflow                                                         #
# --------------------------------------------------------------------------- #

def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _text() -> str:
    return WORKFLOW.read_text()


def _uncommented() -> str:
    return "\n".join(line for line in _text().splitlines()
                     if not line.lstrip().startswith("#"))


def _on(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _jobs(doc: dict | None = None) -> dict:
    return (doc or _doc()).get("jobs") or {}


def _job(name: str) -> dict:
    job = _jobs().get(name)
    assert isinstance(job, dict), f"{WORKFLOW.name} has no `{name}` job"
    return job


def _steps(job: dict) -> list[dict]:
    return [s for s in job.get("steps") or [] if isinstance(s, dict)]


def _all_steps() -> list[dict]:
    return [s for job in _jobs().values() for s in _steps(job or {})]


def _run_steps(job: dict | None = None) -> str:
    steps = _steps(job) if job is not None else _all_steps()
    return "\n".join(s.get("run", "") for s in steps)


def _step_by_id(job: dict, step_id: str) -> dict:
    for step in _steps(job):
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with id {step_id!r}")


def _steps_running(needle: str) -> list[dict]:
    return [s for s in _all_steps() if needle in (s.get("run") or "")]


def _job_if(name: str) -> str:
    return str(_job(name).get("if") or "")


def _trial_jobs() -> dict:
    return {name: job for name, job in _jobs().items()
            if (job or {}).get("uses") == TRIAL_USES}


# --------------------------------------------------------------------------- #
# 1. Schedule, dispatch, concurrency, timeout                                  #
# --------------------------------------------------------------------------- #

def test_the_workflow_is_named_model_adoption():
    assert _doc().get("name") == NAME


def test_it_runs_daily_at_07_17_utc():
    schedule = _on(_doc()).get("schedule")
    assert schedule == [{"cron": CRON}], (
        "the CEO on 2026-09-28: 'it needs to look at it almost every day' — "
        f"daily at 07:17 UTC, `{CRON}`")


def test_it_is_dispatchable_with_a_boolean_dry_run_defaulting_false():
    dispatch = _on(_doc()).get("workflow_dispatch") or {}
    dry_run = (dispatch.get("inputs") or {}).get("dry_run")
    assert dry_run, "workflow_dispatch must take a `dry_run` input"
    assert dry_run.get("type") == "boolean"
    assert dry_run.get("default") is False


def test_one_run_at_a_time_in_the_model_adoption_group():
    concurrency = _doc().get("concurrency")
    assert isinstance(concurrency, dict)
    assert concurrency.get("group") == "model-adoption"
    assert concurrency.get("cancel-in-progress") is False, (
        "a queued run must not cancel one between its push and its pull request")


def test_every_job_that_runs_steps_is_capped_at_30_minutes():
    for name, job in _jobs().items():
        if "uses" in job:
            continue  # a reusable-workflow call takes no timeout of its own
        assert job.get("timeout-minutes") == 30, name


# --------------------------------------------------------------------------- #
# 2. One live catalog read, classified — never the committed snapshot          #
# --------------------------------------------------------------------------- #

def test_it_snapshots_the_live_catalog_into_runner_temp_then_classifies_it():
    runs = _run_steps(_job("classify"))
    snapshot = 'python3 scripts/model_catalog.py snapshot "$RUNNER_TEMP/catalog.json"'
    classify = ('python3 scripts/model_adoption.py classify --snapshot '
                '"$RUNNER_TEMP/catalog.json"')
    assert snapshot in runs
    assert classify in runs
    assert runs.index(snapshot) < runs.index(classify)


def test_the_catalog_is_fetched_exactly_once():
    assert _run_steps().count("model_catalog.py snapshot") == 1


def test_it_never_reads_the_committed_models_json():
    """The committed snapshot stopped updating on 2026-08-10 (DRE-3879)."""
    assert "models.json" not in _uncommented()


def test_apply_edits_against_the_same_catalog_the_rule_classified():
    for step in _steps_running("model_adoption_actions.py apply"):
        assert '--snapshot "$RUNNER_TEMP/catalog.json"' in step["run"]


# --------------------------------------------------------------------------- #
# 3. A quiet day: no trial, no model call                                      #
# --------------------------------------------------------------------------- #

def test_the_only_model_call_is_the_trial_and_it_is_gated_on_an_adopt_route():
    """No step in this file runs a model itself; the two trial jobs do, and
    each runs only when the classify job handed on a candidate to act on."""
    assert "claude-code-action" not in _uncommented()
    trials = _trial_jobs()
    assert set(trials) == {"trial", "pin-trial"}
    assert _job_if("trial") == "needs.classify.outputs.route == 'trial'"
    assert "needs.pin-raise.outputs.branch != ''" in _job_if("pin-trial")


def test_the_pin_raise_runs_only_for_a_degraded_trial_or_a_pin_route():
    cond = _job_if("pin-raise")
    assert "needs.classify.outputs.route == 'pin'" in cond
    assert "needs.trial.outputs.outcome == 'degraded'" in cond


def test_the_trial_is_model_trial_yml_with_the_candidate_and_inherited_secrets():
    trial = _job("trial")
    assert trial["uses"] == TRIAL_USES
    assert trial["with"]["model"] == "${{ needs.classify.outputs.candidate }}"
    assert trial.get("secrets") == "inherit"
    assert trial["with"].get("pipeline_ref"), "model-trial.yml requires pipeline_ref"


def test_the_pin_trial_runs_on_the_raised_pin_branch():
    trial = _job("pin-trial")
    assert trial["with"]["model"] == "${{ needs.classify.outputs.candidate }}"
    assert trial["with"]["pipeline_ref"] == "${{ needs.pin-raise.outputs.branch }}"
    assert trial.get("secrets") == "inherit"


def _write_fake_gh(tmp_path: Path, prs: list[dict]) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text("#!/usr/bin/env bash\ncat \"$FAKE_GH_PRS\"\n")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "prs.json").write_text(json.dumps(prs))
    return bin_dir


def _run_step(tmp_path: Path, step: dict, env_extra: dict | None = None,
              prs: list[dict] | None = None):
    """Run a step's real `run:` block from the repo root, with RUNNER_TEMP,
    GITHUB_OUTPUT and GITHUB_STEP_SUMMARY in `tmp_path`. Returns (proc,
    outputs, summary)."""
    script = tmp_path / "step.sh"
    script.write_text("set -eo pipefail\n" + step["run"])
    out = tmp_path / "github_output"
    out.write_text("")
    summary = tmp_path / "step_summary"
    summary.write_text("")
    env = {k: v for k, v in os.environ.items()
           if k not in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY")}
    env.update({
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
        "FAKE_GH_PRS": str(tmp_path / "prs.json"),
    })
    if prs is not None:
        env["PATH"] = f"{_write_fake_gh(tmp_path, prs)}{os.pathsep}{env['PATH']}"
    env.update(env_extra or {})
    proc = subprocess.run(["bash", str(script)], cwd=ROOT, env=env,
                          capture_output=True, text=True)
    outputs = dict(line.split("=", 1)
                   for line in out.read_text().splitlines() if "=" in line)
    return proc, outputs, summary.read_text()


def _decision(candidate: str, rule: str, reason: str = "evidence") -> dict:
    return {
        "candidate": candidate, "display_name": candidate.title(),
        "created_at": "2026-11-01T00:00:00Z", "family": "sonnet", "rule": rule,
        "replaces": ([{"ladder": "workhorse", "model": "old-sonnet",
                       "created_at": "2026-06-29T00:00:00Z",
                       "price": {"input": 2.0, "output": 10.0}}]
                     if rule == "adopt" else []),
        "price": {"input": 2.0, "output": 10.0},
        "reason": reason,
    }


def _pick(tmp_path: Path, decisions: list[dict]):
    (tmp_path / "decisions.json").write_text(json.dumps(decisions))
    return _run_step(tmp_path, _step_by_id(_job("classify"), "pick"))


def _route(tmp_path: Path, **env):
    step = _step_by_id(_job("classify"), "route")
    full = {"CANDIDATE": "", "IN_FLIGHT": "", "SUPPORTS": "", "SUPPORTS_WHY": ""}
    full.update(env)
    return _run_step(tmp_path, step, full)


def test_a_day_with_no_candidate_hands_nothing_on(tmp_path):
    proc, out, summary = _pick(tmp_path, [])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["candidate"] == ""
    assert out["asks"] == "0"
    assert "No candidate today" in summary
    _, route, _ = _route(tmp_path, CANDIDATE=out["candidate"])
    assert route["route"] == "", "no candidate must route to no trial"


def test_an_ignore_only_day_is_a_summary_line_and_nothing_else(tmp_path):
    proc, out, summary = _pick(tmp_path, [
        _decision("old-haiku-3", "ignore", "superseded by what we run")])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out == {"candidate": "", "asks": "0"}
    assert "| `old-haiku-3` | ignore | superseded by what we run |" in summary
    assert not list(tmp_path.glob("ask-*.json"))
    assert not (tmp_path / "decision.json").exists()


def test_every_candidate_is_a_row_with_its_id_rule_and_reason(tmp_path):
    decisions = [_decision("new-sonnet-9", "adopt", "newer at the same price"),
                 _decision("new-family-1", "ask", "new model family nobody runs"),
                 _decision("old-haiku-3", "ignore", "older | superseded")]
    _, _, summary = _pick(tmp_path, decisions)
    assert "| Candidate | Rule | Reason |" in summary
    for d in decisions:
        assert f"| `{d['candidate']}` | {d['rule']} |" in summary
    assert "older \\| superseded" in summary, "a pipe in a reason must not split the row"


def test_each_ask_gets_its_own_decision_file(tmp_path):
    proc, out, _ = _pick(tmp_path, [
        _decision("new-family-1", "ask", "new model family"),
        _decision("new-family-2", "ask", "no declared price")])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["asks"] == "2"
    assert out["candidate"] == "", "an ask is a question, never a trial"
    files = sorted(tmp_path.glob("ask-*.json"))
    assert [json.loads(f.read_text())["candidate"] for f in files] == [
        "new-family-1", "new-family-2"]


def test_only_the_first_adopt_decision_is_acted_on(tmp_path):
    proc, out, summary = _pick(tmp_path, [
        _decision("new-sonnet-9", "adopt"), _decision("new-opus-9", "adopt")])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["candidate"] == "new-sonnet-9"
    assert json.loads((tmp_path / "decision.json").read_text())["candidate"] == "new-sonnet-9"
    assert "new-opus-9" in summary, "the waiting candidate is named, not dropped"


def test_an_unreadable_classification_fails_the_run(tmp_path):
    (tmp_path / "decisions.json").write_text("not json")
    proc, _, _ = _run_step(tmp_path, _step_by_id(_job("classify"), "pick"))
    assert proc.returncode != 0


def test_the_route_trials_a_supported_or_unknown_candidate(tmp_path):
    for supports in ("yes", "unknown", ""):
        _, out, _ = _route(tmp_path, CANDIDATE="new-sonnet-9", SUPPORTS=supports)
        assert out["route"] == "trial", supports


def test_the_route_raises_the_pin_when_the_pinned_claude_code_says_no(tmp_path):
    _, out, summary = _route(tmp_path, CANDIDATE="new-sonnet-9", SUPPORTS="no",
                             SUPPORTS_WHY="not in its model table")
    assert out["route"] == "pin"
    assert "not in its model table" in summary


def test_an_adoption_in_flight_routes_to_nothing(tmp_path):
    _, out, _ = _route(tmp_path, CANDIDATE="new-sonnet-9", IN_FLIGHT="true",
                       SUPPORTS="yes")
    assert out["route"] == ""


def test_the_pinned_support_is_asked_of_the_candidate_before_any_trial():
    step = _step_by_id(_job("classify"), "pin")
    assert 'claude_code_pin.py supports "$CANDIDATE"' in step["run"]
    assert "steps.inflight.outputs.in_flight != 'true'" in step["if"]


# --------------------------------------------------------------------------- #
# 4. One adoption in flight at a time                                          #
# --------------------------------------------------------------------------- #

def _inflight(tmp_path: Path, prs: list[dict]):
    (tmp_path / "decision.json").write_text(json.dumps(_decision("new-sonnet-9", "adopt")))
    return _run_step(tmp_path, _step_by_id(_job("classify"), "inflight"), prs=prs)


def test_no_open_adoption_leaves_the_rail_free(tmp_path):
    proc, out, _ = _inflight(tmp_path, [
        {"title": "fix(DRE-1): something else", "headRefName": "agent/DRE-1-else"}])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["in_flight"] == "false"


def test_an_open_pr_with_the_rendered_title_holds_the_rail(tmp_path):
    title = "Adopt new-sonnet-9 on the workhorse ladder (replaces old-sonnet)"
    proc, out, _ = _inflight(tmp_path, [{"title": title, "headRefName": "some/branch"}])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["in_flight"] == "true"


def test_any_open_adoption_head_holds_the_rail(tmp_path):
    proc, out, summary = _inflight(tmp_path, [
        {"title": "Adopt another", "headRefName": "agent/DRE-77-adopt-new-opus-9"}])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["in_flight"] == "true"
    assert "skipped" in summary


def test_the_in_flight_title_is_the_rendered_pr_title():
    run = _step_by_id(_job("classify"), "inflight")["run"]
    assert "model_adoption_actions.py render pr-title" in run
    assert "agent/*-adopt-*" in run


# --------------------------------------------------------------------------- #
# 5. ask — ONE question card per candidate; ignore — no write at all           #
# --------------------------------------------------------------------------- #

def test_ask_opens_one_question_card_per_candidate():
    steps = _steps_running("model_adoption_actions.py open-question-card")
    assert len(steps) == 1
    step = steps[0]
    assert 'for DECISION in "$RUNNER_TEMP"/ask-*.json' in step["run"]
    assert "steps.pick.outputs.asks != '0'" in step["if"]
    assert "LINEAR_API_KEY" in (step.get("env") or {})


def test_the_workflow_writes_only_the_cards_the_three_rules_name():
    """`ignore` has no card write of any kind: the only Linear writes in the
    file are the question card (ask), the record card (adopt, passed) and the
    trial-failure card (adopt, failed)."""
    runs = _run_steps()
    writes = re.findall(r"open-question-card|open-record-card|linear_ops\.py \w[\w-]*",
                        runs)
    assert sorted(set(writes)) == sorted({
        "open-question-card", "open-record-card",
        "linear_ops.py find-open", "linear_ops.py create"})
    assert "ignore" not in " ".join(str(s.get("if") or "") for s in _all_steps())
    assert "linear_ops.py comment" not in runs


def test_every_card_write_push_and_pr_is_guarded_by_dry_run():
    for needle in CARD_WRITES + GITHUB_WRITES:
        steps = _steps_running(needle)
        assert steps, f"nothing runs {needle}"
        for step in steps:
            assert "env.DRY_RUN != 'true'" in str(step.get("if") or ""), (
                f"`{step.get('name')}` runs {needle} without the dry-run guard")


def test_dry_run_is_false_on_a_schedule():
    assert _doc()["env"]["DRY_RUN"] == "${{ inputs.dry_run && 'true' || 'false' }}"


def test_a_dry_run_still_prints_the_rendered_texts():
    runs = _run_steps()
    assert "render question-title" in runs and "render question-body" in runs
    assert "render pr-title" in runs and "render pr-body" in runs
    assert "apply \"$CANDIDATE\" --snapshot \"$RUNNER_TEMP/catalog.json\" --check" in runs
    for needle in ("render question-title", "--check"):
        for step in _steps_running(needle):
            if "sync_model_config" in step["run"]:
                continue
            assert "env.DRY_RUN == 'true'" in str(step.get("if")), step.get("name")


# --------------------------------------------------------------------------- #
# 6. adopt — passed: record card, edit, branch, PR, in that order             #
# --------------------------------------------------------------------------- #

def test_the_adopt_job_runs_only_on_a_passed_trial():
    adopt = _job("adopt")
    assert "trial" in adopt["needs"]
    assert "needs.trial.outputs.outcome == 'passed'" in _job_if("adopt")


def test_the_adoption_runs_in_the_card_order():
    runs = _run_steps(_job("adopt"))
    positions = []
    for needle in ADOPT_ORDER:
        assert needle in runs, needle
        positions.append(runs.index(needle))
    assert positions == sorted(positions), dict(zip(ADOPT_ORDER, positions))


def test_the_trial_result_is_written_to_runner_temp_trial_json():
    runs = _run_steps(_job("adopt"))
    assert '"trial.json"' in runs
    for key in ("model", "outcome", "run_url", "summary"):
        assert f'"{key}"' in runs
    assert '--trial "$RUNNER_TEMP/trial.json"' in runs


def test_the_record_card_names_this_run_and_its_outputs_name_the_branch():
    step = _step_by_id(_job("adopt"), "record")
    assert '--run-url "$RUN_URL"' in step["run"]
    assert '>> "$GITHUB_OUTPUT"' in step["run"]
    assert "actions/runs/${{ github.run_id }}" in step["env"]["RUN_URL"]
    branch = _step_by_id(_job("adopt"), "branch")
    assert branch["env"]["BRANCH"] == "${{ steps.record.outputs.branch }}"


def test_the_branch_is_cut_from_origin_main_or_reused_after_a_crash():
    run = _step_by_id(_job("adopt"), "branch")["run"]
    assert 'git checkout -B "$BRANCH" origin/main' in run
    assert 'git ls-remote --exit-code --heads origin "$BRANCH"' in run
    assert "reuse=true" in run
    for step in _steps(_job("adopt")):
        run = step.get("run") or ""
        if ("model_adoption_actions.py apply" in run and "--check" not in run) \
                or "sync_model_config.py" in run or "git push" in run:
            assert "steps.branch.outputs.reuse != 'true'" in step["if"], step["name"]


def test_the_model_tests_are_informational():
    step = _step_by_id(_job("adopt"), "tests")
    run = step["run"]
    assert "python3 -m pytest tests/test_model_policy.py tests/test_model_config.py -q" in run
    assert "set +e" in run, "a red model test must not stop the pull request"
    assert "result=" in run
    pr = [s for s in _steps(_job("adopt")) if "gh pr create" in (s.get("run") or "")][0]
    assert pr["env"]["TESTS"] == "${{ steps.tests.outputs.result }}"


def test_the_commit_touches_only_the_model_config():
    run = [s for s in _steps(_job("adopt")) if "git push" in (s.get("run") or "")][0]["run"]
    assert "git add -- config/models.yaml agents.yaml scripts/model_fallback.py" in run
    assert "git add ." not in run and "git add -A" not in run
    assert "config/models.yaml|agents.yaml|scripts/model_fallback.py" in run
    assert 'user.name="agent-bureau-bot[bot]"' in run


def test_nothing_pushes_to_main():
    text = _uncommented()
    assert "HEAD:main" not in text
    assert not re.search(r"git\s+push[^\n]*\bmain\b", text)
    for step in _steps_running("git push"):
        assert 'git push origin "HEAD:refs/heads/$BRANCH"' in step["run"]


def test_the_pr_is_made_with_the_app_token():
    for job_name in ("adopt", "pin-raise", "pin-pr"):
        steps = _steps(_job(job_name))
        app = steps[0]
        assert app.get("id") == "app", f"{job_name}: the App token is minted first"
        assert app["uses"].startswith("actions/create-github-app-token@")
        assert app["with"]["app-id"] == "${{ secrets.BUREAU_APP_ID }}"
        assert app["with"]["private-key"] == "${{ secrets.BUREAU_APP_PRIVATE_KEY }}"
        checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@"))
        assert checkout["with"]["token"] == "${{ steps.app.outputs.token }}"
    for step in _steps_running("gh pr create"):
        assert step["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"


def test_the_pr_title_and_body_come_from_render():
    step = [s for s in _steps(_job("adopt")) if "gh pr create" in (s.get("run") or "")][0]
    run = step["run"]
    assert ('--title "$(python3 scripts/model_adoption_actions.py render pr-title '
            '--decision "$RUNNER_TEMP/decision.json")"') in run
    assert '--body-file "$RUNNER_TEMP/pr-body.md"' in run
    render = _run_steps(_job("adopt"))
    assert ('model_adoption_actions.py render pr-body --decision "$RUNNER_TEMP/decision.json"'
            in render)
    assert '> "$RUNNER_TEMP/pr-body.md"' in render


def test_an_open_pr_on_the_branch_is_not_duplicated():
    for step in _steps_running("gh pr create"):
        run = step["run"]
        assert 'gh pr list --repo "$GITHUB_REPOSITORY" --head "$BRANCH" --state open' in run
        assert run.index("gh pr list") < run.index("gh pr create")


# --------------------------------------------------------------------------- #
# 7. adopt — failed: ONE trial-failure card and nothing else                   #
# --------------------------------------------------------------------------- #

def test_a_failed_trial_files_one_card_and_does_nothing_else():
    job = _job("trial-failed")
    assert "needs.trial.outputs.outcome == 'failed'" in _job_if("trial-failed")
    runs = _run_steps(job)
    assert 'TITLE="Model trial failed: $CANDIDATE"' in runs
    assert runs.index("linear_ops.py find-open") < runs.index("linear_ops.py create")
    assert "--repo bureau-pipeline --label agent:devops" in runs
    for other in ("open-record-card", "open-question-card", "git push",
                  "gh pr create", "model_adoption_actions.py apply"):
        assert other not in runs, other


def _failure_body(tmp_path: Path, **env) -> str:
    step = next(s for s in _steps(_job("trial-failed"))
                if "trial-failed.md" in (s.get("run") or "")
                and "linear_ops" not in s["run"])
    proc, _, _ = _run_step(tmp_path, step, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return (tmp_path / "trial-failed.md").read_text()


def test_the_failure_card_names_the_run_and_the_death_class(tmp_path):
    body = _failure_body(
        tmp_path, CANDIDATE="new-sonnet-9",
        SUMMARY="new-sonnet-9: failed — api_death, 1 turns, 3s",
        TRIAL_RUN_URL="https://github.com/o/r/actions/runs/42", ON_RAISED_PIN="false")
    assert "https://github.com/o/r/actions/runs/42" in body
    assert "- Death class: api_death" in body
    assert "on the pinned Claude Code" in body
    assert "## Acceptance criteria" in body


def test_a_failed_trial_on_the_raised_pin_says_so(tmp_path):
    body = _failure_body(
        tmp_path, CANDIDATE="new-sonnet-9",
        SUMMARY="new-sonnet-9: degraded — ran at 200,000 of 1,000,000, 5 turns, 9s",
        TRIAL_RUN_URL="https://github.com/o/r/actions/runs/43", ON_RAISED_PIN="true")
    assert "raised Claude Code pin" in body


def test_a_pin_raise_whose_trial_did_not_pass_reaches_the_failure_card():
    cond = _job_if("trial-failed")
    assert "!cancelled()" in cond
    assert "needs.pin-trial.outputs.outcome != 'passed'" in cond


# --------------------------------------------------------------------------- #
# 8. degraded / unsupported — DRE-5121's pin-raise PR, one in flight           #
# --------------------------------------------------------------------------- #

def test_the_pin_raise_finds_applies_and_dedupes_on_the_modules_own_prefix():
    runs = _run_steps(_job("pin-raise"))
    assert 'claude_code_pin.py latest-supporting "$CANDIDATE"' in runs
    assert 'claude_code_pin.py apply --pin "$RUNNER_TEMP/pin.json"' in runs
    assert "from claude_code_pin import PR_TITLE_PREFIX" in runs
    assert claude_code_pin.PR_TITLE_PREFIX not in runs, "read the prefix, never restate it"
    assert runs.index("PR_TITLE_PREFIX") < runs.index("latest-supporting")


def test_the_pin_raise_branch_is_an_agent_branch_named_for_its_target():
    runs = _run_steps(_job("pin-raise"))
    assert "agent/claude-code-pin-{pin['to']['claude_code']}" in runs


def test_the_pin_raise_pr_needs_a_passed_trial_on_the_raised_pin():
    assert "needs.pin-trial.outputs.outcome == 'passed'" in _job_if("pin-pr")
    runs = _run_steps(_job("pin-pr"))
    assert ('claude_code_pin.py render pr-body --pin "$RUNNER_TEMP/pin.json" \\\n'
            '            --trial "$RUNNER_TEMP/trial.json"') in _text()
    assert ('--title "$(python3 scripts/claude_code_pin.py render pr-title '
            '--pin "$RUNNER_TEMP/pin.json")"') in runs


def _pin_inflight(tmp_path: Path, prs: list[dict]):
    return _run_step(tmp_path, _step_by_id(_job("pin-raise"), "inflight"), prs=prs)


def test_one_pin_raise_in_flight_at_a_time(tmp_path):
    title = f"{claude_code_pin.PR_TITLE_PREFIX} to 9.9.9 (claude-code-action v9.9.9)"
    proc, out, _ = _pin_inflight(tmp_path, [{"title": title, "headRefName": "x"}])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["in_flight"] == "true"
    proc, out, _ = _pin_inflight(tmp_path, [{"title": "Adopt x", "headRefName": "y"}])
    assert out["in_flight"] == "false"


# --------------------------------------------------------------------------- #
# 9. Hygiene the fleet already enforces elsewhere                              #
# --------------------------------------------------------------------------- #

def test_no_literal_model_id_follows_model_anywhere():
    assert not re.search(r"--model\s+claude-", _text())


def test_no_expression_inside_a_run_block():
    """DRE-3484: a `run:` with an interpolation compiles to one `format()`
    with a 21,000-character ceiling. Values arrive through `env:`."""
    for step in _all_steps():
        assert "${{" not in (step.get("run") or ""), step.get("name")


def test_the_medic_watches_the_workflow():
    watched = _on(yaml.safe_load(MEDIC_STUB.read_text()))["workflow_run"]["workflows"]
    assert NAME in watched


def test_the_watcher_check_passes():
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_workflow_watchers.py")],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert watchers  # the checker the medic list is read against


@pytest.mark.parametrize("job", ["classify", "adopt", "pin-raise", "pin-pr"])
def test_python_tooling_is_installed_through_the_shared_cached_action(job):
    names = [s.get("name") for s in _steps(_job(job))
             if s.get("uses") == "./.github/actions/setup-python-cached"]
    assert names and all(names), f"{job}: a named setup-python-cached step"
