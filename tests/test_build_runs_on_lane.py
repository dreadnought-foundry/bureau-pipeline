"""The build runner lane: a suite-running job runs where the repo's CI runs
(DRE-4846).

On 2026-09-24 a build or fix agent that runs a product's own test suite was
SIGKILLed — exit 137, the kernel OOM killer — on a `bureau-mini-light-*` runner
(1 CPU, 2 GB, no swap) FOUR times at 17-26 minutes:

* portico DRE-4514, run 35950845844, attempts 1 AND 2, then fix run
  35954619710. The first attempt had reached `⏳ 3/5 implementation green`, and
  that work was lost; the pull request arrived carrying only its RED tests.
* agent-bureau DRE-4810, run 36093192182, attempt 1, 21:34 → 22:00 PT.

A fleet read of every QA Review run from 09-23 10:00 PT to 09-25 10:05 PT — 129
of them — put the critic in the same lane on 2026-09-25. It was killed the same
way THREE times, on both attempts each time, and all three pull requests touch
Portico `infra/`: portico #687 (run 35932479611), #699 (run 36089623608) and
#717 (run 36161433688, killed after eighteen minutes of real review). The
critic runs the product's suite itself — the DRE-3005 evidence rule makes it
paste the command and the output for any claim about a run — so it is exactly
the suite-running job this lane is for. Critics in bureau-pipeline, Atlas and
DeltaSolv, which set no runner variable and run on 7 GB hosted runners, were
never killed.

Both repos read `BUREAU_RUNS_ON` on these jobs, and in both it is the bare
`bureau-mini` set, so a suite-running agent landed on a light runner about half
the time. agent-bureau's `architecture/decisions/adr-owned-runner-fleet.md`
anticipated exactly this: "a light job over 1.5 GiB" is listed under "What
would change this record", and "The heavy class is where suites run".

The lane this file pins:

* `agent-task.yml`'s `execute`, `agent-fix.yml`'s `fix` and `qa-review.yml`'s
  `review` read `BUREAU_CI_RUNS_ON || BUREAU_RUNS_ON || '["ubuntu-latest"]'`.
  That is the long expression with ONE variable in front of it, and the
  variable is not a new one: it is each product repo's own CI variable, which
  agent-bureau's `ci.yml` already reads on every job. Read with `gh variable
  list` on 2026-09-24 at about 22:40 PT it was set on portico and agent-bureau
  (both naming `bureau-heavy`) and on agent-bureau-demo (the bare `bureau-mini`
  set, so the demo can still land on light); atlas and deltasolv set neither
  and stay on `ubuntu-latest`. Nobody has to set anything for this to take
  effect in the two repos that lost work.
* A fourth variable (`BUREAU_BUILD_RUNS_ON`) was deliberately NOT introduced.
  The console's pool fail-back (`console/backend/monitors/runner_pool.py` in
  agent-bureau) deletes exactly three variables when no runner is online —
  `BUREAU_RUNS_ON`, `BUREAU_CI_RUNS_ON`, `BUREAU_SHORT_RUNS_ON` — so a fourth
  would keep builds pinned to a dead pool. Reading the chain the fleet's CI
  already reads, byte for byte, is what makes the fail-back cover this lane.
* A repo that sets neither variable renders exactly what it renders today, and
  that is computed below rather than asserted.

The trade, stated: in agent-bureau and portico the build, fix and review jobs
(up to 120 minutes each) now share the three heavy runners with CI, so a review
can queue behind CI — Portico ran 63 reviews and agent-bureau 40 in the window
above. The ADR's own "heavy queueing" line is the trigger to revisit it, and
the lighter path for the critic is a separate card: have it cite CI's own run
instead of re-running the suite.

Verify, plan, the sweeps, the merge gate and the medic's `diagnose` keep their
chains unchanged — `tests/test_runs_on_switchable.py` and
`tests/test_short_runs_on_lane.py` hold them there.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_runs_on_switchable import (  # noqa: E402
    BUILD_JOBS,
    BUILD_SWITCHABLE,
    SWITCHABLE,
    _load,
    _runner_jobs,
    expected_runs_on,
)
from test_short_runs_on_lane import SHORT_VAR, render_runs_on  # noqa: E402

BUILD_VAR = "BUREAU_CI_RUNS_ON"

# The shape the two repos that lost work carry: `BUREAU_CI_RUNS_ON` naming the
# heavy class, `BUREAU_RUNS_ON` the bare mini set that includes the 2 GB light
# runners the four builds died on.
_BOTH_SET = {
    "BUREAU_CI_RUNS_ON": '["self-hosted", "linux", "arm64", "bureau-heavy"]',
    "BUREAU_RUNS_ON": '["self-hosted", "linux", "arm64", "bureau-mini"]',
}
_LONG_ONLY = {"BUREAU_RUNS_ON": '["self-hosted", "linux", "arm64", "bureau-mini"]'}


def _jobs(name: str) -> dict[str, str]:
    return dict(_runner_jobs(_load(WORKFLOWS / name)))


# --------------------------------------------------------------------------
# the lane is a named list of jobs
# --------------------------------------------------------------------------
def test_the_build_lane_is_exactly_three_jobs() -> None:
    """The scope is the card's, named job by job: the two jobs that build
    (DRE-4846's headline) and the critic the 2026-09-25 amendment added.
    Widening it again is a decision, not a refactor."""
    assert BUILD_JOBS == {
        "agent-task.yml": {"execute"},
        "agent-fix.yml": {"fix"},
        "qa-review.yml": {"review"},
    }
    mentions = {
        path.name
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if BUILD_VAR in path.read_text()
    }
    assert mentions == set(BUILD_JOBS), (
        f"only {sorted(BUILD_JOBS)} may name {BUILD_VAR}; found {sorted(mentions)}"
    )


@pytest.mark.parametrize("name", sorted(BUILD_JOBS))
def test_every_build_job_reads_the_ci_variable_first(name: str) -> None:
    wrong = {
        job_id: runs_on
        for job_id, runs_on in _jobs(name).items()
        if job_id in BUILD_JOBS[name] and runs_on != BUILD_SWITCHABLE
    }
    assert not wrong, (
        f"{name}: every build-lane job must read {BUILD_SWITCHABLE} so a job "
        f"running a product suite lands where that repo's CI runs. "
        f"Wrong: {wrong}"
    )


@pytest.mark.parametrize("name", sorted(BUILD_JOBS))
def test_no_build_lane_file_reads_the_short_variable(name: str) -> None:
    """The build lane widens the chain in the other direction from the short
    lane; a suite-running job pointed at the sub-minute pool would be the
    DRE-3875 incident with the lanes swapped."""
    assert SHORT_VAR not in (WORKFLOWS / name).read_text(), (
        f"{name} runs a product test suite; it must never read {SHORT_VAR}"
    )


# --------------------------------------------------------------------------
# what each of the three variable states renders — computed, not asserted
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(BUILD_JOBS))
def test_with_no_variables_set_the_build_jobs_stay_hosted(name: str) -> None:
    """The card's first rendering criterion: a repo that sets neither variable
    — atlas and deltasolv today — renders exactly what it renders today."""
    for job_id, runs_on in _jobs(name).items():
        assert render_runs_on(runs_on, {}) == ["ubuntu-latest"], (
            f"{name}:{job_id} does not default to the hosted runner"
        )


@pytest.mark.parametrize("name", sorted(BUILD_JOBS))
def test_with_only_the_long_variable_set_nothing_moves(name: str) -> None:
    """The second: with `BUREAU_CI_RUNS_ON` unset, every job in the file — on
    the build lane or not — renders what the long expression renders, so the
    change is inert until a repo has the CI variable set."""
    today = render_runs_on(SWITCHABLE, _LONG_ONLY)
    for job_id, runs_on in _jobs(name).items():
        assert render_runs_on(runs_on, _LONG_ONLY) == today, (
            f"{name}:{job_id} changed where it runs for a repo that has not "
            f"set {BUILD_VAR}"
        )


@pytest.mark.parametrize("name", sorted(BUILD_JOBS))
def test_the_ci_variable_wins_when_both_are_set(name: str) -> None:
    """The third, and the point of the card: with both set — portico and
    agent-bureau today — the suite-running job lands on the heavy class
    instead of the mini set that includes the 2 GB light runners."""
    build = render_runs_on(BUILD_SWITCHABLE, _BOTH_SET)
    long = render_runs_on(SWITCHABLE, _BOTH_SET)
    assert build == ["self-hosted", "linux", "arm64", "bureau-heavy"]
    assert long == ["self-hosted", "linux", "arm64", "bureau-mini"]
    for job_id, runs_on in _jobs(name).items():
        want = build if job_id in BUILD_JOBS[name] else long
        assert render_runs_on(runs_on, _BOTH_SET) == want, (
            f"{name}:{job_id} rendered {render_runs_on(runs_on, _BOTH_SET)}, "
            f"expected {want} (expression {expected_runs_on(name, job_id)})"
        )
