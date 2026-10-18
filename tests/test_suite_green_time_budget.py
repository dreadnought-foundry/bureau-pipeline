"""RED-first tests for the suite-green job's WALL CLOCK (DRE-7511, measured
2026-10-18, times UTC).

The sibling of `test_walks_time_budget.py`: the same defect, a single clock
spent by work that grew under it, one job over.

WHAT WAS MEASURED. The `console change keeps the suite green (part N)` job
(`tests.yml`'s `suite-green`) on the nine `Pipeline Tests` runs on `main` from
39093286093 to 39121478021 (2026-10-18 03:20 to 11:00 UTC), read on
2026-10-18, each green, with 13 cases in the harness's `CASES` split over two
parts under the job's 90-minute limit — seven cases on part 1, six on part 2:

    run 39121478021  part 1 69m08s   part 2 60m25s
    run 39120437791  part 1 68m24s   part 2 61m26s
    run 39116693569  part 1 68m07s   part 2 61m05s
    run 39110211873  part 1 41m33s   part 2 37m39s
    run 39107179820  part 1 68m14s   part 2 61m46s
    run 39106828821  part 1 68m22s   part 2 59m19s
    run 39101508381  part 1 68m36s   part 2 58m57s
    run 39097743049  part 1 60m06s   part 2 57m35s
    run 39093286093  part 1 63m44s   part 2 55m35s

The slowest is part 1 of run 39121478021 (job 117657804407): pytest's own
count, `16 passed in 4139.85s (1:08:59)`, for its seven cases, 591.41 seconds
a case, rounded up to `CASE_SECONDS`. The cases run side by side through the
harness's thread pool, one per core, so a part's clock grows with the cases
it holds: the four cases that ran together took 2,257 to 2,271 seconds each,
the three after them 1,785 to 1,844.

PR #1134 (DRE-7284) added a fourth model group and with it more cases: its
parts ran 74 and 73 minutes and passed, then both were canceled at the
90-minute limit (part 2 in run 39120233659, job 117675506459). Nothing had
said a part was near its clock.

THE RULE. A part holds the cases `selected` gives it — index modulo the part
count — so its projected time is the number it holds times `CASE_SECONDS`.
Every part's projection must be at or under `SHARE` (75%) of the job's own
`timeout-minutes`, read from `.github/workflows/tests.yml`, and the job's
part count must be the smallest that does that. With 13 cases, 592 seconds a
case and 90 minutes, two parts project 7 x 592 = 4,144 s, over the 4,050 s
line, and three project 5 x 592 = 2,960 s: three parts. A change that adds
cases fails here, on its own pull request, naming the case count, the seconds
a case and the part count that would fit — before a part is canceled at its
limit on somebody's approved pull request.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

import test_console_change_keeps_the_suite_green as harness  # noqa: E402

TESTS_WF = REPO / ".github" / "workflows" / "tests.yml"
JOB_NAME = "console change keeps the suite green"

#: The wall-clock seconds one case adds to its part on a GitHub-hosted machine:
#: 4,139.85 s by pytest's count for part 1's seven cases, 591.41 a case,
#: rounded up — the slowest part on main, run 39121478021, job 117657804407
#: (2026-10-18, head 39d9038, 13 cases over two parts).
CASE_SECONDS = 592

#: The share of the job's clock a part may be projected to use. The rest is
#: the margin for a slow machine: the same seven cases took 41m33s in run
#: 39110211873 and 69m08s in run 39121478021.
SHARE = 0.75


def suite_green_job() -> tuple[str, dict]:
    """The one job in tests.yml that runs the harness's cases."""
    jobs = yaml.safe_load(TESTS_WF.read_text(encoding="utf-8"))["jobs"]
    found = [(key, job) for key, job in jobs.items()
             if str(job.get("name", "")).startswith(JOB_NAME)]
    assert len(found) == 1, [k for k, _ in found]
    return found[0]


def limit_seconds() -> int:
    _, job = suite_green_job()
    minutes = job.get("timeout-minutes")
    assert minutes is not None, (
        "the suite-green job declares no timeout-minutes — GitHub's default is "
        "six hours, which is not a budget anyone chose")
    return int(minutes) * 60


def job_parts() -> list:
    return suite_green_job()[1]["strategy"]["matrix"]["part"]


def shares(case_count: int, parts: int) -> list[int]:
    """How many cases each part holds, by the harness's own split."""
    planted = [(f"case-{i}", {}) for i in range(case_count)]
    return [len(harness.selected(planted, {harness.PART_ENV: str(n),
                                           harness.OF_ENV: str(parts)}))
            for n in range(1, parts + 1)]


def parts_needed(case_count: int, case_seconds: float, limit: int) -> int | None:
    """The smallest part count whose every part is projected at or under
    `SHARE` of `limit` seconds, or None when one case alone is over it."""
    per_part = math.floor(SHARE * limit / case_seconds)
    if per_part < 1:
        return None
    return max(1, math.ceil(case_count / per_part))


def problems(case_count: int, case_seconds: float, parts: int,
             limit: int) -> list[str]:
    """Every part whose projected time is over `SHARE` of `limit` seconds, each
    naming the case count, the seconds a case and the part count that would
    fit. Empty when every part fits."""
    line = SHARE * limit
    need = parts_needed(case_count, case_seconds, limit)
    if need is None:
        fix = (f"no part count fits: one case at {case_seconds:g} s is over "
               f"{line:.0f} s — raise the job's timeout-minutes in "
               f".github/workflows/tests.yml from a new measurement")
    else:
        fix = (f"{case_count} cases at {case_seconds:g} s a case need {need} "
               f"parts — set the suite-green job's matrix.part to 1..{need} and "
               f"BUREAU_SUITE_GREEN_OF to \"{need}\" in .github/workflows/tests.yml")
    found = []
    for n, held in enumerate(shares(case_count, parts), start=1):
        projected = held * case_seconds
        if projected > line:
            found.append(
                f"part {n} of {parts} holds {held} of the {case_count} cases, "
                f"{held} x {case_seconds:g} s = {projected:.0f} s, over "
                f"{SHARE:.0%} of the job's {limit} s ({limit // 60}-minute) "
                f"limit, {line:.0f} s — {fix}")
    return found


def test_the_job_declares_a_clock():
    assert limit_seconds() > 0


def test_every_part_on_the_tree_fits_inside_three_quarters_of_its_clock():
    found = problems(len(harness.CASES), CASE_SECONDS, len(job_parts()),
                     limit_seconds())
    assert found == [], "\n".join(found)


def test_the_job_runs_the_smallest_part_count_that_fits():
    need = parts_needed(len(harness.CASES), CASE_SECONDS, limit_seconds())
    assert need is not None
    assert job_parts() == list(range(1, need + 1)), (job_parts(), need)
    # One part fewer would not fit, so the count is the smallest.
    if need > 1:
        assert problems(len(harness.CASES), CASE_SECONDS, need - 1,
                        limit_seconds())


def test_todays_tree_needs_at_least_three_parts():
    """Read from the tree's cases, not copied: 13 on 2026-10-18 at 592 s a
    case under 90 minutes is three parts."""
    assert parts_needed(len(harness.CASES), CASE_SECONDS, limit_seconds()) >= 3


def test_a_larger_case_count_fails_naming_its_three_numbers():
    real = len(harness.CASES)
    parts = len(job_parts())
    planted = real * 2
    found = problems(planted, CASE_SECONDS, parts, limit_seconds())
    assert found, "twice the cases on the same parts still fit"
    need = parts_needed(planted, CASE_SECONDS, limit_seconds())
    assert need > parts
    for message in found:
        assert f"{planted} cases" in message, message
        assert f"{CASE_SECONDS:g} s a case" in message, message
        assert f"need {need} parts" in message, message
    # The real count on the same parts fits, so the planted count is what failed.
    assert problems(real, CASE_SECONDS, parts, limit_seconds()) == []


def test_a_case_over_the_line_alone_says_no_part_count_fits():
    limit = limit_seconds()
    slow = SHARE * limit + 1
    assert parts_needed(1, slow, limit) is None
    found = problems(1, slow, 1, limit)
    assert len(found) == 1 and "no part count fits" in found[0], found


def test_the_case_seconds_name_their_measurement():
    """The comment above `CASE_SECONDS` names the run and the job it was read
    from, as the durations record does for the walks."""
    lines = Path(__file__).read_text(encoding="utf-8").splitlines()
    at = next(i for i, line in enumerate(lines)
              if line.startswith("CASE_SECONDS = "))
    comment = []
    for line in reversed(lines[:at]):
        if not line.startswith("#:"):
            break
        comment.append(line)
    note = " ".join(comment)
    assert "run 39121478021" in note and "job 117657804407" in note, note
