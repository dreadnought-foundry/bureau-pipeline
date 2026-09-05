"""RED-first tests for the `scripts unit tests` job's WALL CLOCK (DRE-3130
review round 1, measured 2026-09-05).

The fourth sibling of `test_verify_turn_budget.py`, `test_fix_turn_budget.py`
and `test_critic_turn_budget.py`. Same defect, different resource: there the
ceiling was turns, here it is minutes.

WHAT WAS MEASURED, not inferred. This PR's own run, 33987398284, head
`7a04ad3e367f76b633841eedd7aa21d1f6ac9913`:

    2026-09-05T19:37:17Z  6277 passed, 1 skipped, 649 subtests passed
                          in 299.47s (0:04:59)
    2026-09-05T19:37:17Z  ##[error]The operation was canceled.
    ANNOTATION: The job has exceeded the maximum execution time of 5m0s

Every test passed. The job was killed four seconds after the suite finished
saying so, and the required check went red on a green suite.

THE DISTRIBUTION IS THE ARGUMENT. The `scripts unit tests` job on the fifteen
`main` runs before this one, longest and shortest:

    4m44s  run 33934938873  2026-09-05T01:02:52Z -> 01:07:36Z   cap: 5m0s
    3m11s  run 33919788755  2026-09-04T21:09:53Z -> 21:13:04Z

So the cap already sat 16 seconds above the longest real run, while run-to-run
spread on that same suite was 93 seconds — nearly six times the remaining
headroom. The job was one unlucky runner from red on any commit, and had been
for some time.

THIS BRANCH IS NOT THE CAUSE, and that is why the remedy is the clock. Timed
locally on the same machine, back to back:

    merge base 8b29fac : 6251 passed ... in 173.21s (0:02:53)
    this branch 7a04ad3: 6277 passed ... in 175.56s (0:02:55)

    tests/test_redispatch_standing_verdict.py alone: 24 passed in 0.22s

A 2.3-second addition — 1.3% — cannot be what crossed a 93-second spread. The
new suite is the last straw on a budget that ran out, not the weight.

HONEST LIMIT OF THIS EVIDENCE: fifteen runs, one repo, one week, and the suite
keeps growing (6251 -> 6277 in this PR alone). That is enough to prove the cap
sits inside the distribution; it is NOT enough to fit a precise new number. So
the assertions below demand real headroom over the observed maximum rather
than a fitted value — deliberately generous, because a wall clock costs
nothing when unused and costs a whole review round when it bites.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"

WORKFLOW = "tests.yml"
JOB = "unit"

#: Longest real `scripts unit tests` job on main in the sampled window
#: (run 33934938873). It succeeded with 16 seconds to spare.
OBSERVED_MAX_SECONDS = 284

#: Shortest in the same window (run 33919788755). The gap between the two is
#: the run-to-run spread the cap has to absorb, and it is the whole argument:
#: a budget narrower than its own variance is a coin flip, not a limit.
OBSERVED_MIN_SECONDS = 191
OBSERVED_SPREAD_SECONDS = OBSERVED_MAX_SECONDS - OBSERVED_MIN_SECONDS

#: Where run 33987398284 was cancelled, with every one of its 6277 tests
#: already green (5m10s against a 5m0s cap).
CANCELLED_AT_SECONDS = 310


def _timeout_minutes(workflow: str, job: str) -> int:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    t = doc["jobs"][job].get("timeout-minutes")
    assert t is not None, (
        f"{workflow} job {job!r} declares no timeout-minutes — GitHub then "
        f"applies its 360-minute default, which is not a budget anyone chose"
    )
    return int(t)


def test_the_unit_job_declares_a_wall_clock():
    """A cap nobody chose is not a budget. Pinned so the fix below cannot be
    'fixed' by deleting the line and inheriting GitHub's 6-hour default."""
    assert _timeout_minutes(WORKFLOW, JOB) > 0


def test_the_cap_clears_the_point_where_a_green_suite_was_killed():
    """THE defect. 6277 tests passed and the check went red anyway.

    A required check that fails on work it has already proved correct is worse
    than a slow one: it spends a full critic round and a fix-loop attempt on a
    question the suite already answered.
    """
    wall = _timeout_minutes(WORKFLOW, JOB) * 60
    assert wall >= CANCELLED_AT_SECONDS + OBSERVED_SPREAD_SECONDS, (
        f"{WORKFLOW} job {JOB!r} dies at {wall}s, but run 33987398284 reached "
        f"{CANCELLED_AT_SECONDS}s with all 6277 tests already green and was "
        f"cancelled. Clearing that point alone is not enough — the same suite "
        f"varies by {OBSERVED_SPREAD_SECONDS}s run to run, so the cap must "
        f"clear it by at least that spread."
    )


def test_the_cap_has_real_margin_over_the_longest_real_run():
    """Not just above the maximum — above it with room.

    The old cap sat 16 seconds above the longest observed run while that run's
    own spread was 93 seconds. By DRE-2422's standard ("a run that SUCCEEDS
    with exactly zero margin left is not a budget"), 5m0s was already spent
    before this branch existed. Doubling the observed maximum is the same
    deliberately generous shape the turn-budget siblings use, and it leaves
    room for a suite that grew by 26 tests in this PR alone.
    """
    wall = _timeout_minutes(WORKFLOW, JOB) * 60
    assert wall >= OBSERVED_MAX_SECONDS * 2, (
        f"{WORKFLOW} job {JOB!r} runs the whole suite in {wall}s against a "
        f"longest real run of {OBSERVED_MAX_SECONDS}s. The suite grows every "
        f"week; a cap without 2x headroom over measured work turns the next "
        f"few dozen green tests into a red required check."
    )
