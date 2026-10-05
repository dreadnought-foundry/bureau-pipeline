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

AND AT 20 MINUTES (DRE-5179 review round 2, measured 2026-09-30) — this time on
`main` first. Three of main's own runs that morning were cancelled at the cap
with their result printed or about to be:

    run 36675466692  13077 passed ... in 1178.10s (0:19:38)   cancelled 20m0s
    run 36678331201  13136 passed ... in 1181.81s (0:19:41)   cancelled 20m5s
    run 36683794636  (still at 88%)                           cancelled 20m17s

and this PR's run 36692848704 (job 109813822031) followed the same line:

    2026-09-30T09:16:24Z  13222 passed, 2 skipped, 1808 subtests passed
                          in 1195.01s (0:19:55)
    2026-09-30T09:16:24Z  ##[error]The operation was canceled.
    ANNOTATION: The job has exceeded the maximum execution time of 20m0s

The fifteen green `main` runs before them, longest and shortest:

    19m32s  run 36668478468  2026-09-30T04:21:42Z -> 04:41:14Z   cap: 20m0s
    13m15s  run 36661280696  2026-09-30T02:45:17Z -> 02:58:32Z

28 seconds of headroom against 377 seconds of spread — the same shape a third
time. 9,594 tests then, 13,222 now. A branch whose main is already cancelled
at the cap cannot be what crossed it; the constants below are re-measured to
this window.

AND AT 40 MINUTES (DRE-5839, measured 2026-10-04) — this time without a single
result printed. PR #721 (DRE-5807) ran the job twice and lost it twice, with no
test failed in either log:

    job 111533604660  2026-10-04T21:17:20Z -> 21:57:35Z  canceled 40m15s at 73%
    job 111542354301  2026-10-04T22:03:19Z -> 22:43:35Z  canceled 40m16s at 76%
                      (the rerun, run 37235481401 attempt 2)

At 76% after 40 minutes, that suite needed roughly 53 minutes to finish. The
34 most recent finished runs of this job on any branch took 23.2 to 38.0
minutes (1392s to 2283s). The forty most recent `main` runs, longest and
shortest:

    39m21s  run 37155389278  2026-10-03T21:31:16Z -> 22:10:37Z   cap: 40m0s
    19m55s  run 37097815047  2026-10-03T04:49:32Z -> 05:09:27Z

39 seconds of headroom against 1,166 seconds of spread — the same shape a
fourth time. 80 is ~2x that longest run, the same rule, and it clears 2x by
only 78 seconds. DRE-5838 (split the suite into parallel parts) is the real
fix: this cap has now doubled four times in a month (5 to 10 to 20 to 40 to
80), and a clock that has to double every week is measuring the suite, not
limiting it. The constants below are re-measured to this window.

AND THEN SPLIT (DRE-5838, 2026-10-04). The job no longer runs the whole suite in
one runner: `unit` is a matrix of parts, each handed a share of the test files
by `scripts/unit_test_parts.py`, and a separate job keeps the required check
name `scripts unit tests` (`tests/test_unit_test_parts.py`). So the clock below
is a PART's clock, and the arithmetic is the same rule applied to a part: the
heaviest part's predicted share of the longest whole-suite run, doubled. The
whole-suite measurements above stay — they are what a part's share is a share
of.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import unit_test_parts  # noqa: E402
WORKFLOWS = REPO / ".github" / "workflows"

WORKFLOW = "tests.yml"
JOB = "unit"

#: Longest real `scripts unit tests` job in the sampled window, the forty most
#: recent `main` runs (run 37155389278, 2026-10-03). It succeeded with 39
#: seconds to spare under the 40m cap.
OBSERVED_MAX_SECONDS = 2361

#: Shortest in the same window (run 37097815047). The gap between the two is
#: the run-to-run spread the cap has to absorb, and it is the whole argument:
#: a budget narrower than its own variance is a coin flip, not a limit.
OBSERVED_MIN_SECONDS = 1195
OBSERVED_SPREAD_SECONDS = OBSERVED_MAX_SECONDS - OBSERVED_MIN_SECONDS

#: Where job 111542354301 (run 37235481401, PR #721's rerun) was canceled, at
#: 76% with no test failed (40m16s against a 40m0s cap).
CANCELLED_AT_SECONDS = 2416


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


def _part_count() -> int:
    doc = yaml.safe_load((WORKFLOWS / WORKFLOW).read_text())
    return len(doc["jobs"][JOB]["strategy"]["matrix"]["part"])


def _heaviest_part_seconds() -> float:
    """The heaviest part's predicted share of the longest whole-suite run."""
    files = unit_test_parts.discover(REPO)
    weights = unit_test_parts.weigh(
        files, unit_test_parts.load_weights(unit_test_parts.DURATIONS))
    split = unit_test_parts.assign(files, _part_count(), weights)
    heaviest = max(sum(weights[f] for f in part) for part in split)
    return OBSERVED_MAX_SECONDS * heaviest / sum(weights.values())


def test_the_cap_clears_the_heaviest_part_with_the_spread_on_top():
    """THE defect, four times: the suite was still running, and the check went
    red. A part's clock has to clear the part's own predicted work plus the
    run-to-run spread, scaled to its share — the 16-vs-93 shape must not
    reappear one level down.
    """
    wall = _timeout_minutes(WORKFLOW, JOB) * 60
    share = _heaviest_part_seconds()
    spread = OBSERVED_SPREAD_SECONDS * share / OBSERVED_MAX_SECONDS
    assert wall >= share + spread, (
        f"{WORKFLOW} job {JOB!r} gives each part {wall}s, but the heaviest "
        f"part is predicted at {share:.0f}s with {spread:.0f}s of spread."
    )


def test_the_cap_has_real_margin_over_the_heaviest_part():
    """Not just above the heaviest part — above it with room.

    The 5m cap sat 16 seconds above the longest observed run while that run's
    own spread was 93 seconds; the 10m cap that replaced it sat 52 seconds
    above, against a spread of 184; the 20m cap after that sat 28 seconds
    above, against a spread of 377; the 40m cap after that sat 39 seconds
    above, against a spread of 1,166. By DRE-2422's standard ("a run that
    SUCCEEDS with exactly zero margin left is not a budget"), each was already
    spent before the branch that tripped it existed. Doubling the measured
    work is the same deliberately generous shape the turn-budget siblings use;
    since DRE-5838 the measured work is a part's, not the whole suite's.
    """
    wall = _timeout_minutes(WORKFLOW, JOB) * 60
    share = _heaviest_part_seconds()
    assert wall >= share * 2, (
        f"{WORKFLOW} job {JOB!r} gives each part {wall}s against a heaviest "
        f"part predicted at {share:.0f}s. The suite grows every week; a cap "
        f"without 2x headroom over measured work turns the next few dozen "
        f"green tests into a red required check."
    )


def test_the_aggregate_job_declares_a_wall_clock_too():
    """The job carrying the required name waits on the parts, then runs the
    static checks. It needs its own cap, or it inherits GitHub's six hours."""
    doc = yaml.safe_load((WORKFLOWS / WORKFLOW).read_text())
    named = [k for k, j in doc["jobs"].items()
             if j.get("name") == "scripts unit tests"]
    assert len(named) == 1, named
    assert _timeout_minutes(WORKFLOW, named[0]) > 0
