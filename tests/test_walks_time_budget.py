"""RED-first tests for the console-change walks' WALL CLOCK (DRE-7016,
measured 2026-10-11, all times Pacific).

The sibling of `test_unit_suite_time_budget.py`: the same defect, a single
clock spent by work that grew under it, one job over.

WHAT WAS MEASURED. The walks job (`scripts unit tests (walks)`) on the ten
most recent finished `Pipeline Tests` runs on `main`, read at 16:15 PT, each
on a GitHub-hosted `ubuntu-24.04` machine, each green, while the three walks
on `main` shared that one job and its 45-minute limit:

    run 38179551625  23m31s      run 38177187029  28m34s
    run 38178153419  32m26s      run 38174569315  20m21s
    run 38177953435  23m31s      run 38174492643  16m28s
    run 38177885940  28m22s      run 38174280180  20m43s
    run 38177348352  32m10s      run 38171950338  22m16s

The same three walks on the fastest and the slowest of those runs:

    walk                          run 38174492643     run 38178153419
                                  job 114573848564    job 114584829847
    agent-move                    8m40s               17m31s
    change                        0m29s               0m12s
    project-model                 7m11s               14m34s

The same work varied by 16 minutes from run to run, on nothing but the
machine GitHub handed the job. PR #1042 (DRE-6935) added the every-default
walk as a fourth; its walks job passed with under two minutes to spare three
times (runs 38173239765, 38173704956 and 38174444202: 42m57s, 43m27s and
43m30s) and was canceled at the limit on run 38178110152's second attempt
(job 114591044631, 45m17s), where the walks took 17m45s, 0m12s and 20m42s and
the project-model walk was 6m25s in. That 20m42s is the slowest single walk
measured anywhere: 1,241.31 seconds by pytest's own count, against 583
recorded for it at a desk.

WHAT THE RECORDS ARE. A walk's seconds in `config/unit-test-durations.json`
are its run alone at a desk, on four cores: 633.36 for the agent-move walk,
442.62 for the project-model walk, 583 for the every-default walk. A walk runs
one copy of the model tests per core (`tests/console_change_walk.py`), so it
follows the machine, and GitHub's machines have taken up to `CI_OVER_DESK`
times as long. The change walk has no desk measurement — its slow cases skip
in the walks job — so its record is the longest the job has taken for it, 29
seconds (job 114573848564).

THE RULE. Since DRE-6935 each walk runs in a job of its own, so the walks
job's `timeout-minutes` is one walk's clock. It must be at least twice the
longest a walk could need, `2 * CI_OVER_DESK * recorded seconds`, for every
walk on the tree. With these records that is 2 x 2.13 x 633.36 = 2,698
seconds, and the limit is 50 minutes, rounded up to the next ten as the
parts' limit was. A walk added with no record, or a record grown past the
limit, fails here by the walk's name — before the job is canceled at its
limit on whichever pull request drew a slow machine.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import unit_test_parts  # noqa: E402

TESTS_WF = REPO / ".github" / "workflows" / "tests.yml"

#: How much longer a walk has taken on a GitHub-hosted machine than its
#: desk record: the every-default walk's 1,241.31 seconds in job
#: 114591044631 (run 38178110152, second attempt, 2026-10-11) over the 583
#: seconds recorded for it at a desk on four cores.
CI_OVER_DESK = 2.13

#: The margin on top of the slowest a walk could need, as with the parts.
MARGIN = 2

#: The walk with no desk record, and its seconds: the longest the walks job
#: has taken for it, 0m29s in job 114573848564 (run 38174492643).
CHANGE_WALK = "tests/test_console_change_keeps_the_suite_green.py"
CHANGE_WALK_SECONDS = 29.0


def walks_job() -> tuple[str, dict]:
    """The one job in tests.yml that runs a walk."""
    jobs = yaml.safe_load(TESTS_WF.read_text(encoding="utf-8"))["jobs"]
    found = [(key, job) for key, job in jobs.items()
             if "unit_test_parts.py run-walks" in
             "\n".join(str(s.get("run", "")) for s in job.get("steps", []))]
    assert len(found) == 1, [k for k, _ in found]
    return found[0]


def limit_seconds() -> int:
    _, job = walks_job()
    minutes = job.get("timeout-minutes")
    assert minutes is not None, (
        "the walks job declares no timeout-minutes — GitHub's default is six "
        "hours, which is not a budget anyone chose")
    return int(minutes) * 60


def problems(walks: list[str], records: dict[str, float],
             limit: int) -> list[str]:
    """Every walk that does not fit its clock: one with no recorded seconds,
    and one whose recorded seconds, as long as a slow machine makes them and
    doubled, are more than `limit` seconds. Empty when every walk fits."""
    found = []
    ways_out = ("split the walk, or raise the walks job's timeout-minutes in "
                ".github/workflows/tests.yml from a new measurement")
    for walk in walks:
        seconds = records.get(walk)
        if not seconds:
            found.append(
                f"{walk} has no recorded seconds in "
                f"config/unit-test-durations.json, so nothing says it fits "
                f"the walks job's {limit // 60}-minute limit — measure it")
            continue
        need = MARGIN * CI_OVER_DESK * seconds
        if need > limit:
            found.append(
                f"{walk} is recorded at {seconds:g} s; {MARGIN} x "
                f"{CI_OVER_DESK} x that is {need:.0f} s, more than the walks "
                f"job's {limit} s ({limit // 60} minutes) — {ways_out}")
    return found


def test_the_walks_job_declares_a_clock():
    assert limit_seconds() > 0


def test_every_walk_on_the_tree_fits_twice_inside_its_clock():
    walks = unit_test_parts.walks(REPO)
    assert walks, "no walk on the tree"
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    found = problems(walks, records, limit_seconds())
    assert found == [], "\n".join(found)


def test_the_change_walk_has_its_measured_record():
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    assert records.get(CHANGE_WALK) == CHANGE_WALK_SECONDS, records.get(CHANGE_WALK)


def test_the_change_walk_record_names_its_measurement():
    note = json.loads(unit_test_parts.DURATIONS.read_text(encoding="utf-8"))["measured"]
    assert CHANGE_WALK in note and "114573848564" in note, note


def test_the_limit_is_fifty_minutes_from_the_largest_record():
    """2 x 2.13 x 633.36 = 2,698 s, rounded up to the next ten minutes."""
    assert limit_seconds() == 50 * 60


def test_a_walk_recorded_past_the_limit_fails_by_name():
    walks = unit_test_parts.walks(REPO)
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    slow = walks[0]
    planted = dict(records, **{slow: 800.0})
    found = problems(walks, planted, limit_seconds())
    assert len(found) == 1 and slow in found[0], found
    assert "split the walk" in found[0] and "raise" in found[0], found[0]
    # The real record of the same walk fits, so the 800 is what failed.
    assert problems([slow], records, limit_seconds()) == []


def test_a_walk_with_no_record_fails_by_name():
    walks = unit_test_parts.walks(REPO)
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    missing = walks[-1]
    planted = {k: v for k, v in records.items() if k != missing}
    found = problems(walks, planted, limit_seconds())
    assert len(found) == 1 and missing in found[0], found


def test_a_new_walk_on_the_tree_with_no_record_fails_by_name():
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    new = "tests/test_console_moved_twice_keeps_the_suite_green.py"
    walks = unit_test_parts.walks(REPO) + [new]
    found = problems(walks, records, limit_seconds())
    assert len(found) == 1 and new in found[0], found
