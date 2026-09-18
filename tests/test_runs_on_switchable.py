"""Every reusable job's `runs-on` is switchable per caller (DRE-3350).

The org burned 52,584 GitHub-hosted Linux minutes between 2026-09-01 and
2026-09-08 — about $900 a month at that rate — and self-hosted runners bill
nothing per minute. The private repos move to a Mac mini runner pool, and the
place a job asks where to run is HERE, in the reusables, not in the stubs.

The rule this file pins:

* Every job in a `workflow_call` reusable reads the CALLER's repository
  variable `BUREAU_RUNS_ON`, with `ubuntu-latest` as the default when it is
  unset. A repo moves to the pool by setting one variable and moves back by
  deleting it. No new `workflow_call` input — a required input is the
  DRE-2689 startup-failure shape, and an optional one would still need every
  stub touched.
* …except the SHORT-job lane (DRE-3887): the two bookkeeping reusables put
  `vars.BUREAU_SHORT_RUNS_ON` in front of that same chain, so a repo whose
  light runners are busy with 20-minute Claude jobs can route its 30-second
  gates somewhere else. The rest of the rule is unchanged, and a repo that
  sets nothing renders exactly what it renders today —
  `tests/test_short_runs_on_lane.py` is where that lane is pinned.
  DRE-4276 widened it to the two SWEEPS — reconcile's `sweep` and every medic
  job but `diagnose`, which runs a Claude agent for up to twenty minutes and
  stays on the long chain. So the lane is now a set of JOBS, not of files:
  `SHORT_LANE` names the files that read the short variable and `LONG_JOBS`
  names the jobs inside them that do not.
* Every job in a workflow that runs only in THIS repo stays on the literal
  `ubuntu-latest`. bureau-pipeline is public, its minutes bill at $0, and the
  org's Default runner group refuses public repos — a public-repo job pointed
  at the pool would queue forever, so the literal is the second guard.

The expression is one exact string so a grep finds every site and a future
edit cannot drift one file from the others.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

SWITCHABLE = "${{ fromJSON(vars.BUREAU_RUNS_ON || '[\"ubuntu-latest\"]') }}"
# The short lane (DRE-3887): the SAME chain with one more variable in front,
# so an unset `BUREAU_SHORT_RUNS_ON` renders whatever `SWITCHABLE` renders.
SHORT_SWITCHABLE = (
    "${{ fromJSON(vars.BUREAU_SHORT_RUNS_ON || vars.BUREAU_RUNS_ON"
    " || '[\"ubuntu-latest\"]') }}"
)
# The reusables that carry sub-minute bookkeeping jobs. Exactly these — the
# scope is the cards' (DRE-3887 the gates, DRE-4276 the sweeps), and widening
# it is a decision, not a tidy-up.
SHORT_LANE = {"merge-gate.yml", "linear-sync.yml", "reconcile.yml", "medic.yml"}
# The jobs INSIDE a short-lane file that stay on the long chain, by name: a
# job that spends a Claude run for minutes belongs with the agent, critic and
# verifier jobs, and routing it to the short pool would recreate DRE-3875 with
# the lanes swapped (DRE-4276).
LONG_JOBS = {"medic.yml": {"diagnose"}}
SHORT_VAR_HINT = "vars.BUREAU_SHORT_RUNS_ON"
HOSTED = "ubuntu-latest"


def expected_runs_on(name: str, job_id: str) -> str:
    """The one expression a reusable job's `runs-on` must be, by file and job."""
    if name in SHORT_LANE and job_id not in LONG_JOBS.get(name, set()):
        return SHORT_SWITCHABLE
    return SWITCHABLE


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _triggers(doc: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True.
    on = doc.get("on", doc.get(True))
    if isinstance(on, list):
        return {k: None for k in on}
    if isinstance(on, str):
        return {on: None}
    return on or {}


def _is_reusable(doc: dict) -> bool:
    return "workflow_call" in _triggers(doc)


def _runner_jobs(doc: dict):
    """(job_id, runs-on) for every job that runs on a runner — a job that
    `uses:` another workflow has no runner of its own and is skipped."""
    for job_id, job in (doc.get("jobs") or {}).items():
        if "uses" in job:
            continue
        yield job_id, job.get("runs-on")


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_every_runner_job_declares_where_it_runs(path: Path) -> None:
    doc = _load(path)
    for job_id, runs_on in _runner_jobs(doc):
        assert runs_on is not None, f"{path.name}:{job_id} has no runs-on"


@pytest.mark.parametrize(
    "path",
    [p for p in _workflow_files() if _is_reusable(_load(p))],
    ids=lambda p: p.name,
)
def test_reusable_jobs_read_the_callers_runner_variable(path: Path) -> None:
    doc = _load(path)
    wrong = [
        f"{job_id}: {runs_on!r} (expected {expected_runs_on(path.name, job_id)})"
        for job_id, runs_on in _runner_jobs(doc)
        if runs_on != expected_runs_on(path.name, job_id)
    ]
    assert not wrong, (
        f"{path.name} is a workflow_call reusable; every job must read the "
        f"caller's runner variable, short-lane jobs with {SHORT_VAR_HINT} in "
        f"front. Not switchable: {wrong}"
    )


@pytest.mark.parametrize(
    "path",
    [p for p in _workflow_files() if not _is_reusable(_load(p))],
    ids=lambda p: p.name,
)
def test_this_repos_own_jobs_stay_hosted(path: Path) -> None:
    doc = _load(path)
    wrong = [
        f"{job_id}: {runs_on!r}"
        for job_id, runs_on in _runner_jobs(doc)
        if runs_on != HOSTED
    ]
    assert not wrong, (
        f"{path.name} runs only in bureau-pipeline, which is public and bills "
        f"$0; its jobs stay on {HOSTED!r}. Wrong: {wrong}"
    )


def test_the_reusable_set_is_the_one_the_card_names() -> None:
    """The card names the reusables it switches; a new reusable added later
    is caught by the parametrized test above, but this pins that the set
    did not silently shrink — a reusable that stopped being `workflow_call`
    would fall out of the switchable rule without anyone noticing."""
    reusable = {p.name for p in _workflow_files() if _is_reusable(_load(p))}
    expected = {
        "agent-fix.yml", "agent-task.yml", "groomer.yml", "linear-sync.yml",
        "medic.yml", "merge-gate.yml", "plan.yml", "planner-replay.yml",
        "qa-review.yml", "reconcile.yml",
        "red-main-repair.yml", "release-train.yml", "verify.yml",
    }
    assert expected <= reusable, sorted(expected - reusable)
