"""The short-job runner lane (DRE-3887, the sweeps DRE-4276, the train DRE-4606).

On 2026-09-13 twenty-two agent-bureau Merge Gate runs sat queued from 16:40 PT,
about an hour. All three light runners were busy with long Claude jobs — Agent
Task execute, Verify, QA Review — while the three heavy runners sat idle. The
cause was one expression: every job in `merge-gate.yml` and `linear-sync.yml`
read `vars.BUREAU_RUNS_ON`, the SAME variable that routes the 20-minute agent
and critic jobs, so a 30-second gate queued behind them for a slot.

On 2026-09-18 at 13:47 PT it happened again one lane over (DRE-4276): the
mini's six runners were all busy, 38 jobs were queued, the oldest reviews had
waited about 100 minutes, and fifteen of the queued jobs were agent-bureau
`Pipeline Medic` runs plus three `Reconcile` sweeps — sub-minute scripts that
read and write GitHub and Linear, taking a turn ahead of every review. They
still read `vars.BUREAU_RUNS_ON` alone, so a repo could not move them without
moving its reviews too.

On 2026-09-22 between 13:00 and 13:17 PT it happened to the release train
(DRE-4606). Portico's `plan` job — sixteen SECONDS of `release_train.py` —
waited 26 minutes for a `bureau-light` slot while all three heavy runners sat
idle and ineligible; over 2026-09-18 22:00 PT → 2026-09-22 13:00 PT that job's
queue wait was p90 44 minutes and max 81 minutes across 109 jobs, and Portico
was 17 commits behind its last portal release. `wait` is the worse of the two:
it is a SLEEP, and it can hold a slot for seventy minutes doing nothing at all.

The lane this file pins:

* Every SHORT job of the five reusables below reads
  `BUREAU_SHORT_RUNS_ON || BUREAU_RUNS_ON || '["ubuntu-latest"]'`. That is
  the long expression with ONE variable in front of it, so a repo that has not
  set the new variable renders byte-identically to what it rendered before —
  the change is inert until a repo opts in, and `test_an_unset_short_variable_
  renders_todays_runner` below is the proof, computed rather than asserted.
* No long Claude job reads the short variable. `agent-task`, `qa-review`,
  `verify` and `plan` are whole files of them; inside `medic.yml` the one such
  job is `diagnose`, which runs a Claude agent for up to twenty minutes and is
  pinned BY NAME to the long chain. Routing any of them to the short pool
  would recreate the incident with the lanes swapped.
* Nor does the one job that does real work on a runner: `release-train.yml`'s
  `release` assumes the caller's OIDC role and cuts the release, sixty minutes
  of it, and is pinned BY NAME to the long chain the same way (DRE-4606).

The scope is exactly these five files, and inside them every job is named:
short ones in `SHORT_JOBS`, long ones in `LONG_JOBS`. A job added to any of
them without a runner decision fails here rather than inheriting one, and
widening the lane to another file is a card, not a tidy-up.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_runs_on_switchable import (  # noqa: E402
    LONG_JOBS,
    SHORT_LANE,
    SHORT_SWITCHABLE,
    SWITCHABLE,
    _load,
    _runner_jobs,
    expected_runs_on,
)

SHORT_VAR = "BUREAU_SHORT_RUNS_ON"

# Every short job of the five files, named. DRE-3887's criterion was "a test
# pins that list"; DRE-4276 adds the two sweeps to it and DRE-4606 the release
# train's two decision jobs. The medic's eight script jobs — the classifier,
# the one retry, and the six that write a note or a receipt and end — are each
# `timeout-minutes: 5` and call nothing but `gh` and `python3`; reconcile's
# `sweep` is `reconcile.py` and nothing else; the train's `wait` and `plan`
# are `release_train.py` and nothing else, over the caller's own `github.token`
# and no cloud role. `wait` is the one short-lane job that is not quick — it
# SLEEPS, up to seventy minutes — and that is the argument for moving it, not
# against: the criterion is no Claude call, no build and no test suite, never
# the clock.
SHORT_JOBS = {
    "merge-gate.yml": {"resolve", "evaluate"},
    "linear-sync.yml": {"card-done", "conflict-sweep"},
    "reconcile.yml": {"sweep"},
    "medic.yml": {
        "classify",
        "retry",
        "retry_declined",
        "stall_record",
        "backoff",
        "upstream_outage",
        "linear_rate_limited",
        "environment_hold",
    },
    "release-train.yml": {"wait", "plan"},
}

# The whole-file long Claude jobs the DRE-3887 card named — none of them may
# so much as mention the short variable. `medic.yml` left this list in
# DRE-4276: its one Claude job is pinned by name in `LONG_JOBS` instead.
LONG_CLAUDE = ["agent-task.yml", "qa-review.yml", "verify.yml", "plan.yml"]

_EXPR = re.compile(r"^\$\{\{ fromJSON\((?P<chain>.+?)\) \}\}$")


def render_runs_on(expr: str, variables: dict[str, str]) -> list[str]:
    """Evaluate a `${{ fromJSON(a || b || '<json>') }}` runs-on the way GitHub
    does: `||` yields the first operand that is not falsy, an unset repository
    variable is the empty string (falsy), and `fromJSON` parses the winner.

    Deliberately not a general expression engine — it understands exactly the
    one shape these workflows use, so an expression of any other shape fails
    the parse rather than being silently approximated.
    """
    match = _EXPR.match(expr)
    assert match, f"not a fromJSON runs-on expression: {expr!r}"
    for operand in (o.strip() for o in match.group("chain").split("||")):
        if operand.startswith("vars."):
            value = variables.get(operand[len("vars.") :], "")
        else:
            assert operand.startswith("'") and operand.endswith("'"), operand
            value = operand[1:-1]
        if value:
            return json.loads(value)
    raise AssertionError(f"every operand of {expr!r} was falsy")


def _jobs(name: str) -> dict[str, str]:
    return dict(_runner_jobs(_load(WORKFLOWS / name)))


# --------------------------------------------------------------------------
# the lane is a named list of jobs
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(SHORT_JOBS))
def test_every_job_of_a_short_lane_file_has_a_named_lane(name: str) -> None:
    """Every runner job in the file is either a short job or a named long
    job. A job added without a runner decision fails here rather than
    inheriting one."""
    named = SHORT_JOBS[name] | LONG_JOBS.get(name, set())
    assert set(_jobs(name)) == named, (
        f"{name}'s runner jobs moved; the lane is pinned by job name so a new "
        f"job cannot join or leave it unnoticed"
    )
    assert not SHORT_JOBS[name] & LONG_JOBS.get(name, set()), (
        f"{name}: a job cannot be on both lanes"
    )


@pytest.mark.parametrize("name", sorted(SHORT_JOBS))
def test_every_short_job_reads_the_short_variable_first(name: str) -> None:
    wrong = {
        job_id: runs_on
        for job_id, runs_on in _jobs(name).items()
        if job_id in SHORT_JOBS[name] and runs_on != SHORT_SWITCHABLE
    }
    assert not wrong, (
        f"{name}: every short job must read {SHORT_SWITCHABLE} so a repo can "
        f"route its sub-minute jobs off the long-job runners. Wrong: {wrong}"
    )


def test_the_medics_diagnosis_agent_stays_in_the_agent_lane() -> None:
    """The one medic job that spends a Claude run — `diagnose`, twenty minutes
    and sixty turns of it — keeps reading the long chain (DRE-4276). It is the
    job the short lane is being cleared FOR, not one that belongs on it."""
    assert LONG_JOBS["medic.yml"] == {"diagnose"}
    jobs = _jobs("medic.yml")
    assert jobs["diagnose"] == SWITCHABLE, (
        f"medic.yml:diagnose runs a Claude agent for minutes; it must keep "
        f"reading {SWITCHABLE}, got {jobs['diagnose']!r}"
    )


def test_the_trains_release_job_stays_in_the_long_lane() -> None:
    """The one release-train job that does real work on the runner — `release`
    assumes the caller's OIDC role and cuts the release, `timeout-minutes: 60`
    — keeps reading the long chain (DRE-4606). `wait` and `plan` only DECIDE;
    this is the job the short lane clears the way for."""
    assert LONG_JOBS["release-train.yml"] == {"release"}
    jobs = _jobs("release-train.yml")
    assert jobs["release"] == SWITCHABLE, (
        f"release-train.yml:release does the deploy; it must keep reading "
        f"{SWITCHABLE}, got {jobs['release']!r}"
    )


def test_the_short_lane_is_exactly_five_files() -> None:
    """The scope the CEO signed on 2026-09-14 (merge-gate and linear-sync), the
    two sweeps DRE-4276 added on 2026-09-18, and the release train DRE-4606
    added on 2026-09-22 — and nothing else. Widening the lane again is a
    decision, not a refactor."""
    assert SHORT_LANE == set(SHORT_JOBS)
    assert SHORT_LANE == {
        "merge-gate.yml",
        "linear-sync.yml",
        "reconcile.yml",
        "medic.yml",
        "release-train.yml",
    }
    mentions = {
        path.name
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if SHORT_VAR in path.read_text()
    }
    assert mentions == SHORT_LANE, (
        f"only {sorted(SHORT_LANE)} may name {SHORT_VAR}; found {sorted(mentions)}"
    )


# --------------------------------------------------------------------------
# no long Claude job reads it
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", LONG_CLAUDE)
def test_long_claude_jobs_never_read_the_short_variable(name: str) -> None:
    path = WORKFLOWS / name
    assert SHORT_VAR not in path.read_text(), (
        f"{name} runs Claude for minutes at a time; routing it to the short "
        f"pool recreates DRE-3875 with the lanes swapped"
    )
    # Per JOB, off `expected_runs_on` rather than off `SWITCHABLE` flat
    # (DRE-4846): `agent-task.yml`'s `execute` and `qa-review.yml`'s `review`
    # read the build lane, which is the long chain with the product repo's own
    # CI variable in front, while `verify.yml`, `plan.yml` and every other job
    # of these files still read `SWITCHABLE` exactly. The short variable is
    # still barred from all four files by the assertion above.
    wrong = {
        job_id: runs_on
        for job_id, runs_on in _jobs(name).items()
        if runs_on != expected_runs_on(name, job_id)
    }
    assert not wrong, (
        f"{name} must keep reading the long chain, build-lane jobs with the "
        f"caller's CI variable in front. Wrong: {wrong}"
    )


# --------------------------------------------------------------------------
# the fallback is exactly today's behaviour
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(SHORT_JOBS))
def test_an_unset_short_variable_renders_todays_runner(name: str) -> None:
    """Computed, not asserted: with `BUREAU_SHORT_RUNS_ON` unset, every job in
    the file — short or long — renders what the long expression renders, for a
    repo that sets nothing AND for a repo already on the pool."""
    for variables in (
        {},
        {"BUREAU_RUNS_ON": '["self-hosted", "macos", "bureau"]'},
    ):
        today = render_runs_on(SWITCHABLE, variables)
        for job_id, runs_on in _jobs(name).items():
            assert render_runs_on(runs_on, variables) == today, (
                f"{name}:{job_id} changed where it runs for a repo that has "
                f"not set {SHORT_VAR} (vars={variables})"
            )


# The card's second criterion, over the RENDERED expressions of the real jobs:
# with both variables set, the sweep jobs land in the short lane and the agent
# job lands in the long one.
_BOTH_SET = {
    "BUREAU_SHORT_RUNS_ON": '["ubuntu-latest"]',
    "BUREAU_RUNS_ON": '["self-hosted", "linux", "arm64", "bureau-mini"]',
}


@pytest.mark.parametrize("name", sorted(SHORT_JOBS))
def test_the_short_variable_wins_on_every_short_job(name: str) -> None:
    """…and the lane is not decoration: set it and the short jobs move, while
    the long jobs stay exactly where they were — per job, off the expression
    the workflow file actually carries."""
    short = render_runs_on(SHORT_SWITCHABLE, _BOTH_SET)
    long = render_runs_on(SWITCHABLE, _BOTH_SET)
    assert short == ["ubuntu-latest"]
    assert long == ["self-hosted", "linux", "arm64", "bureau-mini"]
    for job_id, runs_on in _jobs(name).items():
        want = short if job_id in SHORT_JOBS[name] else long
        assert render_runs_on(runs_on, _BOTH_SET) == want, (
            f"{name}:{job_id} rendered {render_runs_on(runs_on, _BOTH_SET)}, "
            f"expected {want} (expression {expected_runs_on(name, job_id)})"
        )


@pytest.mark.parametrize("name", sorted(SHORT_JOBS))
def test_the_default_is_still_the_hosted_runner(name: str) -> None:
    """The card's third criterion: with neither variable set, every job in
    the file still resolves to `ubuntu-latest`."""
    assert render_runs_on(SHORT_SWITCHABLE, {}) == ["ubuntu-latest"]
    for job_id, runs_on in _jobs(name).items():
        assert render_runs_on(runs_on, {}) == ["ubuntu-latest"], (
            f"{name}:{job_id} does not default to the hosted runner"
        )
