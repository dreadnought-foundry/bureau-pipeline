"""The console-change walks' WALL CLOCK, one shard at a time (DRE-7907,
measured 2026-10-24, all times Pacific).

The sibling of `test_unit_suite_time_budget.py`: the same defect, a single
clock spent by work that grew under it, one job over.

THE HISTORY (DRE-7016, DRE-7278). Each walk ran in a job of its own, and the
walks job's clock was a walk's desk seconds — measured alone on four cores —
times `CI_OVER_DESK`, the slowest CI walk over its desk record: 2.13 on
2026-10-11, 4.28 on 2026-10-14, doubled, which put the limit at 100 minutes.
By 2026-10-24 the every-default walk took 60m07s against its 583 desk
seconds, 6.19 times: a ratio read off a desk is stale the day a walk grows,
and one walk held `unit-result`, and the channel promotion behind it, 45 to
60 minutes past the unit parts.

WHAT WAS MEASURED (DRE-7907). The walk jobs' wall times on four `main` push
runs of `Pipeline Tests` on 2026-10-24, each on `ubuntu-latest` (2 cores).
The slowest of each walk is its record in `config/unit-test-durations.json`,
its run and job in that walk's `provenance` line:

    walk            slowest   run           job
    agent-move      46m09s    40133214810   120989216557   2,769 s
    every-default   60m07s    40133214810   120989216560   3,607 s
    project-model   50m56s    40119507138   120947794113   3,056 s
    change          0m29s     38174492643   114573848564      29 s (DRE-7016's)

THE FIXED COST of a job is 19 seconds: the change walk's job 120989216692
(run 40133214810) took 0m19s with no case in it — set up, checkout, the
cached install, pytest starting and the walk's case-free tests. The other
walks' jobs spent 8 to 14 seconds outside the walk step on those runs.

THE PROJECTION. A shard's time is the fixed cost plus the walk's record less
the fixed cost, scaled by the shard's case count over the walk's, both counts
rounded up to the next multiple of 2 because a walk runs its cases two at a
time on these runners (`scripts/unit_test_parts.py`'s
`projected_shard_seconds`). Each walk gets the smallest shard count whose
largest shard projects at or under the target.

THE TARGET is 450 seconds, 7.5 minutes: `tests.yml` documents run-to-run
spread of up to 2x on these runners, so a shard projected at 7.5 minutes
lands at or under 15 minutes on a slow runner. THE CLOCK is the walks job's
`timeout-minutes`, at least twice the slowest shard's projection rounded up
to the next ten minutes — the unit parts' rule — and never more than twice
the target rounded the same way, 20 minutes.

A walk added with no record, a record that grew, or a clock that shrank fails
here by the walk's name, with the shard count that would fit.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import unit_test_parts  # noqa: E402

TESTS_WF = REPO / ".github" / "workflows" / "tests.yml"

#: The margin on top of the slowest shard's projection, as with the parts.
MARGIN = 2

#: Each walk's slowest CI walk job on 2026-10-24, its run and its job — the
#: records this card wrote. A newer, slower main run may replace one, with
#: its own ids; `test_every_walk_record_names_its_run_and_job` holds the ids
#: to whatever the record says.
MEASURED = {
    "tests/test_console_agent_move_keeps_the_suite_green.py":
        (2769.0, "40133214810", "120989216557"),
    "tests/test_console_every_default_keeps_the_suite_green.py":
        (3607.0, "40133214810", "120989216560"),
    "tests/test_console_project_model_keeps_the_suite_green.py":
        (3056.0, "40119507138", "120947794113"),
    "tests/test_console_change_keeps_the_suite_green.py":
        (29.0, "38174492643", "114573848564"),
}

#: The walk with no case run in the walks job, and its seconds: the longest
#: the walks job has taken for it, 0m29s in job 114573848564 (run
#: 38174492643).
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


def shard_counts() -> dict[str, int]:
    """Each walk's shard count, as `walks --json` hands it to the matrix."""
    return {e["walk"]: e["shards"] for e in unit_test_parts.walk_shards(REPO)}


def case_counts() -> dict[str, int]:
    return {rel: len(unit_test_parts.walk_cases(REPO, rel) or [])
            for rel in unit_test_parts.walks(REPO)}


def fits(seconds: float, cases: int, ceiling: float) -> int | None:
    """The smallest shard count whose largest shard projects at or under
    `ceiling`, or None when not even one case a shard does."""
    for n in range(1, max(cases, 1) + 1):
        if unit_test_parts.projected_shard_seconds(seconds, cases, n) <= ceiling:
            return n
    return None


def _would_fit(seconds: float, cases: int, ceiling: float) -> str:
    n = fits(seconds, cases, ceiling)
    if n is None:
        return (f"no shard count fits: two cases alone project past "
                f"{ceiling:.0f} s — re-measure the walk, or split its cases")
    return f"{n} shard(s) would fit"


def problems(shards: dict[str, int], records: dict[str, float],
             cases: dict[str, int], limit: int) -> list[str]:
    """Every walk that does not fit: one with no recorded seconds, one whose
    largest shard projects over the shard target, and — once for the run —
    the slowest shard's projection, doubled, over `limit` seconds. Each names
    the walk and the shard count that would fit. Empty when every walk
    fits."""
    target = unit_test_parts.WALK_SHARD_TARGET_SECONDS
    found, projected = [], {}
    for walk, n in shards.items():
        seconds = records.get(walk)
        if not seconds:
            found.append(
                f"{walk} has no recorded seconds in "
                f"config/unit-test-durations.json, so nothing says its shards "
                f"fit the {target} s target — measure it, and record its "
                f"seconds under `seconds` and where they came from under "
                f"`provenance`, both keyed by its path")
            continue
        projected[walk] = unit_test_parts.projected_shard_seconds(
            seconds, cases[walk], n)
        if projected[walk] > target:
            found.append(
                f"{walk} is recorded at {seconds:g} s for {cases[walk]} "
                f"case(s); in {n} shard(s) its largest shard projects at "
                f"{projected[walk]:.0f} s, over the {target} s shard target — "
                f"{_would_fit(seconds, cases[walk], target)}")
    if projected:
        slowest = max(projected, key=projected.get)
        if MARGIN * projected[slowest] > limit:
            seconds = records[slowest]
            found.append(
                f"{slowest}'s largest shard projects at "
                f"{projected[slowest]:.0f} s in {shards[slowest]} shard(s); "
                f"{MARGIN} x that is more than the walks job's {limit} s "
                f"({limit // 60} minutes) — "
                f"{_would_fit(seconds, cases[slowest], limit / MARGIN)}, or "
                f"raise timeout-minutes in .github/workflows/tests.yml")
    return found


def test_the_walks_job_declares_a_clock():
    assert limit_seconds() > 0


def test_every_shard_of_every_walk_fits_the_target_and_the_clock():
    shards = shard_counts()
    assert sorted(shards) == unit_test_parts.walks(REPO), shards
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    found = problems(shards, records, case_counts(), limit_seconds())
    assert found == [], "\n".join(found)


def test_each_walk_gets_the_smallest_shard_count_that_fits():
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    target = unit_test_parts.WALK_SHARD_TARGET_SECONDS
    cases = case_counts()
    for walk, n in shard_counts().items():
        assert n == fits(records[walk], cases[walk], target), (walk, n)


def test_the_change_walk_fits_one_shard():
    assert shard_counts()[CHANGE_WALK] == 1


def test_the_target_lands_a_slow_runner_inside_fifteen_minutes():
    """The 2x spread `tests.yml` documents, on the target: 15 minutes."""
    assert 2 * unit_test_parts.WALK_SHARD_TARGET_SECONDS <= 15 * 60
    assert unit_test_parts.WALK_SHARD_TARGET_SECONDS == 450
    assert unit_test_parts.WALK_JOB_FIXED_SECONDS == 19


def test_the_clock_is_twice_the_slowest_shard_rounded_up_to_ten_minutes():
    """At least 2x the slowest projection, rounded up to the next ten
    minutes; never more than 2x the target rounded the same way, because a
    shard count that grows with the cases keeps every shard under it."""
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    cases = case_counts()
    slowest = max(unit_test_parts.projected_shard_seconds(records[w], cases[w], n)
                  for w, n in shard_counts().items())
    assert limit_seconds() >= math.ceil(MARGIN * slowest / 600) * 600
    ceiling = math.ceil(MARGIN * unit_test_parts.WALK_SHARD_TARGET_SECONDS / 600) * 600
    assert limit_seconds() <= ceiling, (limit_seconds(), ceiling)
    assert limit_seconds() == 20 * 60


def test_the_projection_rounds_both_counts_up_to_pairs():
    """19 + (3,607 - 19) x 4 / 36 for the every-default walk in 9 shards:
    35 cases round to 36, its largest shard's 4 stay 4."""
    project = unit_test_parts.projected_shard_seconds
    assert round(project(3607.0, 35, 9), 1) == round(19 + 3588 * 4 / 36, 1)
    # Three cases in a shard run as two pairs.
    assert project(3607.0, 35, 12) == project(3607.0, 35, 9)
    assert project(29.0, 19, 1) == 29.0


def test_every_walk_record_is_a_ci_measurement_naming_its_run_and_job():
    record = json.loads(unit_test_parts.DURATIONS.read_text(encoding="utf-8"))
    for walk in unit_test_parts.walks(REPO):
        seconds, run, job = MEASURED[walk]
        assert record["seconds"][walk] == seconds, walk
        entry = record["provenance"][walk]
        assert walk in entry and run in entry and job in entry, entry
        assert "locally" not in entry and "desk" not in entry.replace(
            "no desk measurement", ""), entry


def test_the_change_walk_has_its_measured_record():
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    assert records.get(CHANGE_WALK) == CHANGE_WALK_SECONDS, records.get(CHANGE_WALK)


def test_the_change_walk_record_names_its_measurement():
    # The change walk's own provenance entry (DRE-7508), not a shared note.
    record = json.loads(unit_test_parts.DURATIONS.read_text(encoding="utf-8"))
    entry = record["provenance"][CHANGE_WALK]
    assert CHANGE_WALK in entry and "114573848564" in entry, entry


def test_the_measure_it_message_names_the_keyed_provenance_entry():
    new = "tests/test_console_moved_twice_keeps_the_suite_green.py"
    (found,) = problems({new: 1}, {}, {new: 3}, limit_seconds())
    assert "measure it" in found, found
    assert "`seconds`" in found and "`provenance`" in found, found
    assert "keyed by its path" in found, found
    assert "clause" not in found and "note" not in found, found


def test_a_slower_record_fails_naming_the_walk_and_the_count_that_fits():
    shards, cases = shard_counts(), case_counts()
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    slow = "tests/test_console_every_default_keeps_the_suite_green.py"
    planted = dict(records, **{slow: 2 * records[slow]})
    found = problems(shards, planted, cases, limit_seconds())
    over = [p for p in found if p.startswith(slow) and "shard target" in p]
    assert len(over) == 1, found
    want = fits(planted[slow], cases[slow], unit_test_parts.WALK_SHARD_TARGET_SECONDS)
    assert want > shards[slow]
    assert f"{want} shard(s) would fit" in over[0], over[0]
    # The real record of the same walk fits, so the doubling is what failed.
    assert problems({slow: shards[slow]}, records, cases, limit_seconds()) == []


def test_a_clock_too_short_for_the_slowest_shard_fails_naming_it():
    shards, cases = shard_counts(), case_counts()
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    found = problems(shards, records, cases, 10 * 60)
    assert len(found) == 1 and "10 minutes" in found[0], found
    slowest = max(shards, key=lambda w: unit_test_parts.projected_shard_seconds(
        records[w], cases[w], shards[w]))
    assert found[0].startswith(slowest), found[0]
    want = fits(records[slowest], cases[slowest], 10 * 60 / MARGIN)
    assert f"{want} shard(s) would fit" in found[0], found[0]


def test_a_walk_with_no_record_fails_by_name():
    shards, cases = shard_counts(), case_counts()
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    missing = unit_test_parts.walks(REPO)[-1]
    planted = {k: v for k, v in records.items() if k != missing}
    found = problems(shards, planted, cases, limit_seconds())
    assert len(found) == 1 and missing in found[0], found


def test_a_new_walk_on_the_tree_with_no_record_fails_by_name():
    records = unit_test_parts.load_weights(unit_test_parts.DURATIONS)
    new = "tests/test_console_moved_twice_keeps_the_suite_green.py"
    shards = dict(shard_counts(), **{new: 1})
    cases = dict(case_counts(), **{new: 3})
    found = problems(shards, records, cases, limit_seconds())
    assert len(found) == 1 and new in found[0], found
