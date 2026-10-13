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
               then (passed) ONE write of the model home under the adoption's
               own name and a record card (DRE-6943 — no branch, no pull
               request), or (failed) ONE `Model trial failed:` card;
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

# The adoption, in the order DRE-6943 fixes (after the trial job): the home is
# written, then the record card says what the write did.
ADOPT_ORDER = (
    "model_adoption_actions.py apply",
    "model_adoption_actions.py open-record-card",
)

# The three scripts whose every step holds the model-home read token — one
# rule for the file (DRE-6943), named with or without `.py`, so a heredoc
# that imports one is held to it too.
HOME_SCRIPTS = re.compile(r"\b(model_adoption_actions|model_adoption|claude_code_pin)\b")
HOME_TOKEN = "${{ steps.model_home.outputs.token }}"
WRITE_TOKEN = "${{ steps.model_home_write.outputs.token }}"
# The steps that built the adoption's pull request, gone with it.
PR_STEPS = (
    "Render the adoption pull request",
    "Create the adoption branch off origin/main, or reuse it",
    "Regenerate the derived model regions",
    "Run the model tests (informational)",
    "Commit and push the adoption branch",
    "Open the adoption pull request",
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


HOME_READ = {"state": "read", "written_at": "2026-10-11T18:03:12Z",
             "line": "model-home: read — written 2026-10-11T18:03:12Z by console"}


def _pick(tmp_path: Path, decisions: list[dict], home: dict | None = None):
    (tmp_path / "decisions.json").write_text(json.dumps(decisions))
    (tmp_path / "home-read.json").write_text(json.dumps(home or HOME_READ))
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
    assert out["written_at"] == HOME_READ["written_at"]
    assert "No candidate today" in summary
    _, route, _ = _route(tmp_path, CANDIDATE=out["candidate"])
    assert route["route"] == "", "no candidate must route to no trial"


def test_an_ignore_only_day_is_a_summary_line_and_nothing_else(tmp_path):
    proc, out, summary = _pick(tmp_path, [
        _decision("old-haiku-3", "ignore", "superseded by what we run")])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out == {"candidate": "", "asks": "0", "written_at": HOME_READ["written_at"]}
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
    (tmp_path / "home-read.json").write_text(json.dumps(HOME_READ))
    proc, _, _ = _run_step(tmp_path, _step_by_id(_job("classify"), "pick"))
    assert proc.returncode != 0


@pytest.mark.parametrize("state, line", [
    ("unreadable", "model-home: unreadable — the GitHub API answered HTTP 403 for "
                   "BUREAU_MODEL_CHOICES; running on the defaults in the code"),
    ("not set", "model-home: not set — running on the defaults in the code"),
])
def test_a_home_that_was_not_read_stops_green_with_its_line(tmp_path, state, line):
    """An adoption is never decided against the defaults in the code: the run
    classifies nothing, says the home's line in its summary, and stays green."""
    proc, out, summary = _pick(tmp_path, [], {"state": state, "written_at": None,
                                              "line": line})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out == {"candidate": "", "asks": "0", "written_at": ""}
    assert line in summary
    assert "No candidate today" not in summary
    _, route, _ = _route(tmp_path, CANDIDATE=out["candidate"])
    assert route["route"] == ""


def test_the_classify_step_records_the_home_it_read():
    step = next(s for s in _steps(_job("classify"))
                if "model_adoption.py classify" in (s.get("run") or ""))
    assert '--home-out "$RUNNER_TEMP/home-read.json"' in step["run"]
    assert _job("classify")["outputs"]["written_at"] == "${{ steps.pick.outputs.written_at }}"


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


def test_the_card_owed_fallback_head_holds_the_rail(tmp_path):
    """Linear down when an adoption opened: its PR sits on the fallback head,
    `agent/model-adoption-<id>`, and still holds the rail."""
    proc, out, summary = _inflight(tmp_path, [
        {"title": "Adopt another", "headRefName": "agent/model-adoption-new-opus-9"}])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["in_flight"] == "true"
    assert "skipped" in summary


def test_the_in_flight_title_is_the_rendered_pr_title():
    run = _step_by_id(_job("classify"), "inflight")["run"]
    assert "model_adoption_actions.py render pr-title" in run
    assert "from model_adoption_actions import fallback_branch, record_branch" in run


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
    # The home write too: every `apply` but the `--check` preview.
    writes = [s for s in _steps_running("model_adoption_actions.py apply")
              if "--check" not in s["run"]]
    assert writes
    for step in writes:
        assert "env.DRY_RUN != 'true'" in str(step.get("if") or ""), step.get("name")


def test_dry_run_is_false_on_a_schedule():
    assert _doc()["env"]["DRY_RUN"] == "${{ inputs.dry_run && 'true' || 'false' }}"


def test_a_dry_run_still_prints_the_rendered_texts():
    runs = _run_steps()
    assert "render question-title" in runs and "render question-body" in runs
    assert "render pr-title" in runs
    assert ('apply "$CANDIDATE" --snapshot "$RUNNER_TEMP/catalog.json" '
            '--expect-written-at "$EXPECT_WRITTEN_AT" --check') in runs
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
    record = _step_by_id(_job("adopt"), "record")["run"]
    assert '--trial "$RUNNER_TEMP/trial.json"' in record


def test_the_record_card_names_this_run_and_says_what_apply_did():
    step = _step_by_id(_job("adopt"), "record")
    assert '--run-url "$RUN_URL"' in step["run"]
    assert '--applied "$RUNNER_TEMP/applied.json"' in step["run"]
    assert '>> "$GITHUB_OUTPUT"' in step["run"]
    assert "actions/runs/${{ github.run_id }}" in step["env"]["RUN_URL"]
    assert "env.DRY_RUN != 'true'" in step["if"]


def _apply_step() -> dict:
    return _step_by_id(_job("adopt"), "apply")


def test_apply_writes_the_home_with_the_written_at_classify_recorded():
    step = _apply_step()
    assert step["name"] == "Apply the adoption to the home"
    assert "env.DRY_RUN != 'true'" in step["if"]
    env = step["env"]
    assert env["EXPECT_WRITTEN_AT"] == "${{ needs.classify.outputs.written_at }}"
    assert env["BUREAU_MODEL_HOME_WRITE_TOKEN"] == WRITE_TOKEN
    run = step["run"]
    assert 'apply "$CANDIDATE" --snapshot "$RUNNER_TEMP/catalog.json"' in run
    assert '--expect-written-at "$EXPECT_WRITTEN_AT"' in run
    assert '--result "$RUNNER_TEMP/applied.json"' in run
    preview = [s for s in _steps(_job("adopt")) if "--check" in (s.get("run") or "")]
    assert len(preview) == 1
    assert '--expect-written-at "$EXPECT_WRITTEN_AT"' in preview[0]["run"]
    assert preview[0]["env"]["EXPECT_WRITTEN_AT"] == env["EXPECT_WRITTEN_AT"]


def test_the_adopt_job_mints_the_write_token_for_the_home():
    steps = _steps(_job("adopt"))
    mint = _step_by_id(_job("adopt"), "model_home_write")
    assert mint["uses"].split("@")[0] == "actions/create-github-app-token"
    assert mint["with"]["owner"] == "dreadnought-foundry"
    assert mint["with"]["repositories"] == "bureau-pipeline"
    # DRE-7230: no permission subset — v3.2.0 declares no variables input, so
    # the token carries the installation's own permissions, variables: write
    # among them. No continue-on-error: a refused write is red.
    assert not [k for k in mint["with"] if k.startswith("permission-")], mint["with"]
    assert "continue-on-error" not in mint
    assert "env.DRY_RUN != 'true'" in mint["if"]
    assert steps.index(mint) < steps.index(_apply_step())
    # The write token reaches the one step that writes, and no other.
    holders = [s.get("name") for s in _all_steps()
               if "BUREAU_MODEL_HOME_WRITE_TOKEN" in (s.get("env") or {})]
    assert holders == ["Apply the adoption to the home"]


def test_the_adopt_job_opens_no_pull_request():
    names = [s.get("name") for s in _steps(_job("adopt"))]
    for gone in PR_STEPS:
        assert gone not in names, gone
    assert "Apply the adoption to config/models.yaml" not in names
    runs = _run_steps(_job("adopt"))
    for needle in ("git checkout -B", "sync_model_config", "pytest", "git commit",
                   "git push", "gh pr create", "render pr-body"):
        assert needle not in runs, needle


def _home_doc(tmp_path: Path, written_at: str) -> Path:
    sys.path.insert(0, str(ROOT / "tests"))
    import model_fixture

    doc = model_fixture.fixture_home()
    doc["written_at"] = written_at
    path = tmp_path / "home.json"
    path.write_text(json.dumps(doc))
    return path


def test_the_apply_step_as_written_writes_nothing_on_a_home_changed_mid_trial(tmp_path):
    """The step's real `run:`, against a home a console save moved on: it
    ends green, says the one sentence in the summary and writes the result
    the record card reads — and with no write made, no network is touched."""
    home = _home_doc(tmp_path, "2026-10-12T09:30:00Z")
    proc, _, summary = _run_step(tmp_path, _apply_step(), {
        "CANDIDATE": "claude-opus-5-6",
        "EXPECT_WRITTEN_AT": "2026-10-11T00:00:00Z",
        "BUREAU_MODEL_HOME_FILE": str(home),
        "BUREAU_MODEL_HOME_WRITE_TOKEN": "",
    })
    assert proc.returncode == 0, proc.stdout + proc.stderr
    sentence = ("the home changed at 2026-10-12T09:30:00Z while the trial ran; "
                "nothing written; tomorrow's run sorts again")
    assert sentence in proc.stdout
    assert sentence in summary
    applied = json.loads((tmp_path / "applied.json").read_text())
    assert applied["outcome"] == "home-changed"


def test_every_step_that_runs_an_adoption_script_holds_the_read_token():
    """One rule for the file: a step that runs or imports model_adoption.py,
    model_adoption_actions.py or claude_code_pin.py holds
    BUREAU_MODEL_HOME_TOKEN from its job's mint, whatever it runs."""
    held = {}
    for job_name, job in _jobs().items():
        for step in _steps(job or {}):
            run = "\n".join(line for line in (step.get("run") or "").splitlines()
                            if not line.lstrip().startswith("#"))
            if HOME_SCRIPTS.search(run):
                env = {**(job.get("env") or {}), **(step.get("env") or {})}
                held.setdefault(job_name, []).append(step.get("name"))
                assert env.get("BUREAU_MODEL_HOME_TOKEN") == HOME_TOKEN, (
                    f"{job_name}: {step.get('name')}")
    assert set(held) == {"classify", "adopt", "pin-raise", "pin-pr"}
    # The steps the card names, by what they run.
    for name in ("Classify every candidate", "Is an adoption already in flight",
                 "Does the pinned Claude Code run it at full strength",
                 "Ask the CEO — one question card per candidate",
                 "Dry run — print the questions"):
        assert name in held["classify"], name
    for name in ("Apply the adoption to the home", "Open the record card",
                 "Dry run — preview the home write"):
        assert name in held["adopt"], name
    for name in ("Find the oldest release that runs it at full strength",
                 "Render the pin-raise pull request",
                 "Raise the pin on its branch and push it"):
        assert name in held["pin-raise"], name
    assert "Open the pin-raise pull request" in held["pin-pr"]


@pytest.mark.parametrize("job_name", ["classify", "adopt", "pin-raise", "pin-pr"])
def test_each_job_that_runs_one_mints_the_read_token_before_it(job_name):
    steps = _steps(_job(job_name))
    mint = _step_by_id(_job(job_name), "model_home")
    assert mint["name"] == "Mint the model-home read token"
    assert mint["uses"].split("@")[0] == "actions/create-github-app-token"
    assert mint["continue-on-error"] is True
    assert mint["with"]["owner"] == "dreadnought-foundry"
    assert mint["with"]["repositories"] == "bureau-pipeline"
    assert not [k for k in mint["with"] if k.startswith("permission-")], mint["with"]
    first = next(i for i, s in enumerate(steps) if HOME_SCRIPTS.search(s.get("run") or ""))
    assert steps.index(mint) < first


def test_the_model_home_scope_check_passes():
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_model_home_scope.py")],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_action_pin_check_passes():
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_action_pins.py")],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


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
    assert '--repo "$REPO_SLUG" --label agent:devops' in runs
    assert "basename \"$GITHUB_REPOSITORY\"" in runs
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


def test_a_written_at_that_is_not_one_plain_line_never_reaches_the_output(tmp_path):
    proc, out, _ = _pick(tmp_path, [], dict(HOME_READ, written_at="x\nroute=pin"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["written_at"] == "unusable"
    assert "route" not in out
