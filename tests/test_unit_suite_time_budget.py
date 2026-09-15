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

IT BIT AGAIN AT 10 MINUTES (DRE-3991 review round 1, measured 2026-09-15), and
the growth this docstring predicted is the whole of the reason: 6,277 tests
then, 9,594 now. Run 34993126917 (job 104462553529) printed

    2026-09-15T16:19:04Z  5 failed, 9594 passed, 2 skipped,
                          896 subtests passed in 593.37s (0:09:53)
    2026-09-15T16:19:05Z  ##[error]The operation was canceled.
    ANNOTATION: The job has exceeded the maximum execution time of 10m0s

and the distribution is again the argument. The `scripts unit tests` job on
the fifteen `main` runs before it, longest and shortest:

    9m08s  run 34871402677  2026-09-14T16:53:41Z -> 17:02:49Z   cap: 10m0s
    6m04s  run 34931087544  2026-09-15T05:01:35Z -> 05:07:39Z

52 seconds of headroom against 184 seconds of run-to-run spread — the 16-vs-93
shape above, one cap later. THIS BRANCH IS NOT THE CAUSE either: its three new
test files run in 10.87s together, timed locally. The constants below are
re-measured to that window, so these assertions are stricter now than they
were, not looser.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"

WORKFLOW = "tests.yml"
JOB = "unit"

#: Longest real `scripts unit tests` job on main in the sampled window
#: (run 34871402677, 2026-09-15). It succeeded with 52 seconds to spare.
OBSERVED_MAX_SECONDS = 548

#: Shortest in the same window (run 34931087544). The gap between the two is
#: the run-to-run spread the cap has to absorb, and it is the whole argument:
#: a budget narrower than its own variance is a coin flip, not a limit.
OBSERVED_MIN_SECONDS = 364
OBSERVED_SPREAD_SECONDS = OBSERVED_MAX_SECONDS - OBSERVED_MIN_SECONDS

#: Where run 34993126917 was cancelled, 13 seconds after its 9,594-test suite
#: had finished and printed its result (10m6s against a 10m0s cap).
CANCELLED_AT_SECONDS = 606


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


def test_the_cap_clears_the_point_where_a_suite_was_killed_mid_sentence():
    """THE defect, twice: the suite finished, said so, and the check went red.

    A required check that kills a run whose answer is already printed is worse
    than a slow one: nobody gets the exit code, so it spends a full critic
    round and a fix-loop attempt on a question the suite already answered.
    """
    wall = _timeout_minutes(WORKFLOW, JOB) * 60
    assert wall >= CANCELLED_AT_SECONDS + OBSERVED_SPREAD_SECONDS, (
        f"{WORKFLOW} job {JOB!r} dies at {wall}s, but run 34993126917 reached "
        f"{CANCELLED_AT_SECONDS}s with its 9,594-test result already printed "
        f"and was cancelled. Clearing that point alone is not enough — the "
        f"same suite varies by {OBSERVED_SPREAD_SECONDS}s run to run, so the "
        f"cap must clear it by at least that spread."
    )


def test_the_cap_has_real_margin_over_the_longest_real_run():
    """Not just above the maximum — above it with room.

    The 5m cap sat 16 seconds above the longest observed run while that run's
    own spread was 93 seconds; the 10m cap that replaced it sat 52 seconds
    above, against a spread of 184. By DRE-2422's standard ("a run that
    SUCCEEDS with exactly zero margin left is not a budget"), each was already
    spent before the branch that tripped it existed. Doubling the observed
    maximum is the same deliberately generous shape the turn-budget siblings
    use, and it leaves room for a suite that has grown by ~3,300 tests since
    the last time this was measured.
    """
    wall = _timeout_minutes(WORKFLOW, JOB) * 60
    assert wall >= OBSERVED_MAX_SECONDS * 2, (
        f"{WORKFLOW} job {JOB!r} runs the whole suite in {wall}s against a "
        f"longest real run of {OBSERVED_MAX_SECONDS}s. The suite grows every "
        f"week; a cap without 2x headroom over measured work turns the next "
        f"few dozen green tests into a red required check."
    )
