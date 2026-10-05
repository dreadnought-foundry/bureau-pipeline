"""RED-first tests for DRE-5838 — the `scripts unit tests` job runs in parts.

The job ran the whole suite in one runner, and the suite outgrew every clock it
was given: 5, 10, 20, 40 and 80 minutes in a month (the history is in
`tests/test_unit_suite_time_budget.py`). On 2026-10-04 PR #721 (DRE-5807) lost
the job twice at the 40-minute cap with no test failed, and the 34 most recent
runs took 23 to 38 minutes. A clock that has to double every week is measuring
the suite, not limiting it.

So the suite now runs as parallel parts, and three things have to stay true
that were true for free while it ran in one piece:

  * EVERY TEST STILL RUNS EXACTLY ONCE. `scripts/unit_test_parts.py` splits the
    test files across the parts, and each part records the files it was handed.
    The aggregate job reads those records back and fails when a test file is in
    no part or in two — the card's check, run against what the parts actually
    ran rather than against a recomputation of what they should have.
  * THE MERGE GATE STILL SEES ONE RESULT. Branch protection on `main` requires
    a check named exactly `scripts unit tests` (read from the live branch on
    2026-10-04: `scripts unit tests` and `TDD commit discipline`). The parts
    report under their own names, so one job keeps the old name and fails
    unless every part passed — including when a part failed, which is why it
    runs `always()`: a required check that is SKIPPED counts as passing.
  * NO PART TAKES MORE THAN 20 MINUTES on the suite as measured.

Run: cd bureau-pipeline && python3 -m pytest tests/test_unit_test_parts.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import unit_test_parts as parts  # noqa: E402

SCRIPT = ROOT / "scripts" / "unit_test_parts.py"
TESTS_WF = ROOT / ".github" / "workflows" / "tests.yml"

#: The name branch protection requires on `main` (read 2026-10-04 from
#: `GET repos/dreadnought-foundry/bureau-pipeline/branches/main`).
REQUIRED_CHECK = "scripts unit tests"

#: The job that runs the suite, one matrix entry per part.
PARTS_JOB = "unit"

#: The card's ceiling on any one part, in seconds.
PART_CEILING_SECONDS = 20 * 60

#: Longest whole-suite `scripts unit tests` run in the window DRE-5839
#: measured — the forty most recent `main` runs, run 37155389278 — the same
#: figure `test_unit_suite_time_budget.py` carries.
OBSERVED_WHOLE_SUITE_SECONDS = 2361

#: What a part pays before its first test: a full-history checkout (DRE-5179
#: needs it) and the cached tooling install. Generous on purpose.
PART_SETUP_SECONDS = 120


def _tree(tmp_path: Path, names: list[str]) -> Path:
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_x():\n    pass\n")
    return tmp_path


def _doc() -> dict:
    return yaml.safe_load(TESTS_WF.read_text(encoding="utf-8"))


def _jobs() -> dict:
    return _doc()["jobs"]


def _aggregate() -> tuple[str, dict]:
    named = [(k, j) for k, j in _jobs().items() if j.get("name") == REQUIRED_CHECK]
    assert len(named) == 1, (
        f"exactly one job in tests.yml must be named {REQUIRED_CHECK!r} — "
        f"branch protection requires that check name; found {named}"
    )
    return named[0]


# ---------------------------------------------------------------------------
# 1. Discovery — the files `pytest tests` would collect.
# ---------------------------------------------------------------------------


def test_discovery_finds_the_files_pytest_collects(tmp_path):
    root = _tree(tmp_path, [
        "tests/test_a.py",
        "tests/b_test.py",
        "tests/sub/test_c.py",
        "tests/conftest.py",
        "tests/helpers.py",
        "tests/fixtures/data.json",
        "tests/__pycache__/test_a.cpython-312.py",
        "tests/.hidden/test_d.py",
    ])
    assert parts.discover(root) == [
        "tests/b_test.py",
        "tests/sub/test_c.py",
        "tests/test_a.py",
    ]


def test_discovery_on_the_live_tree_matches_a_plain_glob():
    found = parts.discover(ROOT)
    assert found, "the live tree has no test files?"
    assert set(found) >= {
        p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py")
    }
    assert "tests/test_unit_test_parts.py" in found


# ---------------------------------------------------------------------------
# 2. The partition — every file in exactly one part, balanced by weight.
# ---------------------------------------------------------------------------

FILES = [f"tests/test_{i:02d}.py" for i in range(23)]


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 8])
def test_every_file_lands_in_exactly_one_part(n):
    split = parts.assign(FILES, n, {})
    assert len(split) == n
    flat = [f for part in split for f in part]
    assert sorted(flat) == sorted(FILES)
    assert len(flat) == len(set(flat))
    assert all(split), "no part may be empty while there are files to give it"


def test_the_partition_is_deterministic_whatever_the_input_order():
    weights = {f: float(i % 7 + 1) for i, f in enumerate(FILES)}
    a = parts.assign(FILES, 4, weights)
    b = parts.assign(list(reversed(FILES)), 4, weights)
    assert a == b


def test_the_heavy_files_are_spread_not_stacked():
    # One file as heavy as everything else together must sit alone; a
    # round-robin or alphabetical split would stack it with others.
    weights = {f: 1.0 for f in FILES}
    weights["tests/test_00.py"] = 22.0
    split = parts.assign(FILES, 2, weights)
    heavy = next(p for p in split if "tests/test_00.py" in p)
    assert heavy == ["tests/test_00.py"]
    loads = sorted(sum(weights[f] for f in p) for p in split)
    assert loads == [22.0, 22.0]


def test_an_unmeasured_file_is_weighed_as_a_typical_one():
    # A new test file has no measurement yet. It must still land somewhere,
    # weighed as the median measured file rather than as zero (zero would
    # pile every new file onto one part).
    weights = {"tests/test_00.py": 1.0, "tests/test_01.py": 3.0,
               "tests/test_02.py": 5.0}
    assert parts.weigh(["tests/test_new.py"], weights) == {"tests/test_new.py": 3.0}
    assert parts.weigh(["tests/test_new.py"], {}) == {"tests/test_new.py": 1.0}


def test_more_parts_than_files_is_refused():
    with pytest.raises(ValueError):
        parts.assign(FILES[:3], 4, {})


def test_a_part_outside_the_range_is_refused(tmp_path):
    root = _tree(tmp_path, ["tests/test_a.py", "tests/test_b.py"])
    for bad in (0, 3):
        with pytest.raises(ValueError):
            parts.part_files(root, bad, 2, {})


# ---------------------------------------------------------------------------
# 3. The check — what the parts actually ran, read back.
# ---------------------------------------------------------------------------


def _write_manifests(directory: Path, split: list[list[str]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for i, files in enumerate(split, 1):
        (directory / f"part-{i}.txt").write_text(
            parts.manifest(i, len(split), files))


def test_the_check_passes_when_every_file_ran_once(tmp_path):
    root = _tree(tmp_path / "repo", FILES)
    ran = tmp_path / "ran"
    _write_manifests(ran, parts.assign(parts.discover(root), 3, {}))
    assert parts.check(root, ran) == []


def test_the_check_fails_on_a_file_in_no_part(tmp_path):
    root = _tree(tmp_path / "repo", FILES)
    split = parts.assign(parts.discover(root), 3, {})
    dropped = split[1].pop()
    ran = tmp_path / "ran"
    _write_manifests(ran, split)
    problems = parts.check(root, ran)
    assert any(dropped in p and "no part" in p for p in problems), problems


def test_the_check_fails_on_a_file_in_two_parts(tmp_path):
    root = _tree(tmp_path / "repo", FILES)
    split = parts.assign(parts.discover(root), 3, {})
    twice = split[0][0]
    split[2].append(twice)
    ran = tmp_path / "ran"
    _write_manifests(ran, split)
    problems = parts.check(root, ran)
    assert any(twice in p and "parts 1 and 3" in p for p in problems), problems


def test_the_check_fails_on_a_part_that_never_reported(tmp_path):
    # The matrix lost an entry, or a part died before it recorded anything:
    # its files are in no part, and the missing part is named too.
    root = _tree(tmp_path / "repo", FILES)
    ran = tmp_path / "ran"
    _write_manifests(ran, parts.assign(parts.discover(root), 3, {}))
    (ran / "part-2.txt").unlink()
    problems = parts.check(root, ran)
    assert any("part 2 of 3" in p for p in problems), problems
    assert any("no part" in p for p in problems), problems


def test_the_check_fails_when_parts_disagree_on_the_count(tmp_path):
    root = _tree(tmp_path / "repo", FILES)
    ran = tmp_path / "ran"
    _write_manifests(ran, parts.assign(parts.discover(root), 3, {}))
    (ran / "part-9.txt").write_text(parts.manifest(1, 4, []))
    problems = parts.check(root, ran)
    assert any("disagree" in p for p in problems), problems


def test_the_check_fails_on_a_file_this_tree_does_not_have(tmp_path):
    root = _tree(tmp_path / "repo", FILES)
    split = parts.assign(parts.discover(root), 2, {})
    split[0].append("tests/test_gone.py")
    ran = tmp_path / "ran"
    _write_manifests(ran, split)
    problems = parts.check(root, ran)
    assert any("tests/test_gone.py" in p for p in problems), problems


def test_the_check_fails_when_nothing_reported(tmp_path):
    root = _tree(tmp_path / "repo", FILES)
    (tmp_path / "ran").mkdir()
    assert parts.check(root, tmp_path / "ran")


def test_a_rerun_of_the_same_part_is_not_two_parts(tmp_path):
    # "Re-run failed jobs" runs a part again; its record is the same files
    # under the same part number, and that is one part, not two.
    root = _tree(tmp_path / "repo", FILES)
    ran = tmp_path / "ran"
    split = parts.assign(parts.discover(root), 2, {})
    _write_manifests(ran, split)
    (ran / "part-1-again.txt").write_text(parts.manifest(1, 2, split[0]))
    assert parts.check(root, ran) == []


def test_the_cli_lists_a_part_and_the_check_reads_it_back(tmp_path):
    root = _tree(tmp_path / "repo", FILES)
    ran = tmp_path / "ran"
    listed = []
    for i in (1, 2, 3):
        out = subprocess.run(
            [sys.executable, str(SCRIPT), "list", "--root", str(root),
             "--part", str(i), "--of", "3",
             "--manifest", str(ran / f"part-{i}.txt")],
            capture_output=True, text=True, check=True).stdout.split()
        listed += out
    assert sorted(listed) == sorted(parts.discover(root))
    ok = subprocess.run(
        [sys.executable, str(SCRIPT), "check", "--root", str(root), str(ran)],
        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    (ran / "part-3.txt").unlink()
    red = subprocess.run(
        [sys.executable, str(SCRIPT), "check", "--root", str(root), str(ran)],
        capture_output=True, text=True)
    assert red.returncode == 1
    assert "no part" in red.stdout + red.stderr


# ---------------------------------------------------------------------------
# 4. The workflow — parts in parallel, one result under the required name.
# ---------------------------------------------------------------------------


def _part_count() -> int:
    matrix = _jobs()[PARTS_JOB]["strategy"]["matrix"]["part"]
    assert matrix == list(range(1, len(matrix) + 1)), (
        f"the parts are numbered 1..N with no gap: {matrix}"
    )
    return len(matrix)


def test_the_suite_runs_as_a_matrix_of_parts():
    job = _jobs()[PARTS_JOB]
    assert _part_count() >= 2
    assert job["strategy"].get("fail-fast") is False, (
        "one red part must not cancel the others — a canceled part hides "
        "its own failures behind somebody else's"
    )
    assert job["name"] != REQUIRED_CHECK, (
        "the parts report under their own names; the required name belongs "
        "to the one job that reports all of them"
    )
    run = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "unit_test_parts.py list" in run
    text = yaml.safe_dump(job["steps"])
    assert "matrix.part" in text and "strategy.job-total" in text
    assert "pytest tests" not in run, "a part must not run the whole suite"


def test_each_part_uploads_what_it_ran():
    steps = _jobs()[PARTS_JOB]["steps"]
    uploads = [s for s in steps
               if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert len(uploads) == 1
    assert "matrix.part" in uploads[0]["with"]["name"]
    assert "always()" in str(uploads[0].get("if", "")), (
        "a part whose tests fail still ran them — record it either way"
    )


def test_one_job_carries_the_required_check_name():
    _, job = _aggregate()
    assert job.get("needs") in (PARTS_JOB, [PARTS_JOB])


def test_the_required_check_runs_even_when_a_part_failed():
    # Without always() GitHub SKIPS a job whose needs failed, and branch
    # protection counts a skipped required check as passing.
    _, job = _aggregate()
    assert "always()" in str(job.get("if", ""))


def test_the_required_check_fails_unless_every_part_succeeded():
    _, job = _aggregate()
    text = yaml.safe_dump(job["steps"])
    assert f"needs.{PARTS_JOB}.result" in text
    assert "success" in text


def test_the_required_check_runs_the_once_and_only_once_check():
    _, job = _aggregate()
    downloads = [s for s in job["steps"]
                 if str(s.get("uses", "")).startswith("actions/download-artifact@")]
    assert len(downloads) == 1
    run = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "unit_test_parts.py check" in run


def test_the_static_checks_still_run_under_the_required_name():
    # They ran as steps of the old single job; they stay under the name the
    # merge gate requires rather than moving to an unrequired one.
    _, job = _aggregate()
    run = "\n".join(str(s.get("run", "")) for s in job["steps"])
    for script in ("check_pipeline_ref.py", "check_action_pins.py",
                   "check_wip_cap.py", "check_reconcile_env.py",
                   "check_workflow_watchers.py", "check_death_receipts.py",
                   "check_workflow_prompts.py", "pipeline_act.py check",
                   "check_act_receipts.py"):
        assert script in run, f"{script} no longer runs under {REQUIRED_CHECK!r}"


def test_no_part_takes_more_than_twenty_minutes_on_the_measured_suite():
    """The card's ceiling, predicted from what was measured.

    `config/unit-test-durations.json` holds each file's measured share of the
    suite; the whole suite took OBSERVED_WHOLE_SUITE_SECONDS in CI at its
    longest. The heaviest part's share of that, plus setup, must stay under
    twenty minutes — with room, because the suite grows every week.
    """
    files = parts.discover(ROOT)
    weights = parts.weigh(files, parts.load_weights(parts.DURATIONS))
    split = parts.assign(files, _part_count(), weights)
    total = sum(weights.values())
    heaviest = max(sum(weights[f] for f in p) for p in split)
    predicted = OBSERVED_WHOLE_SUITE_SECONDS * heaviest / total + PART_SETUP_SECONDS
    assert predicted <= PART_CEILING_SECONDS * 0.75, (
        f"the heaviest of {_part_count()} parts is predicted at "
        f"{predicted:.0f}s against a {PART_CEILING_SECONDS}s ceiling — add a part"
    )


def test_the_durations_record_covers_the_live_tree():
    record = json.loads(parts.DURATIONS.read_text(encoding="utf-8"))
    assert record.get("measured"), "say when and how the durations were measured"
    seconds = record["seconds"]
    files = parts.discover(ROOT)
    measured = [f for f in files if f in seconds]
    assert len(measured) >= 0.9 * len(files), (
        "most test files carry no measurement — re-measure "
        "(python3 scripts/unit_test_parts.py measure <junit.xml>...)"
    )


def test_measure_reads_seconds_per_file_from_a_ci_log(tmp_path):
    # A green run's own `pytest -v` log is a measurement: the runner stamps
    # every result line, and each line's gap from the one before it is that
    # test's time.
    log = tmp_path / "part-1.log"
    log.write_text(
        "scripts unit tests\tUnit tests\t2026-10-04T22:14:26.0000000Z "
        "tests/test_conftest_marker.py::test_a PASSED [  0%]\n"
        "scripts unit tests\tUnit tests\t2026-10-04T22:14:28.5000000Z "
        "tests/test_unit_test_parts.py::test_b PASSED [  1%]\n"
        "scripts unit tests\tUnit tests\t2026-10-04T22:14:29.0000000Z "
        "some other line\n"
        "scripts unit tests\tUnit tests\t2026-10-04T22:14:31.5000000Z "
        "tests/test_unit_test_parts.py::test_c FAILED [  2%]\n")
    assert parts.measure([log]) == {"tests/test_unit_test_parts.py": 5.5}


def test_measure_reads_seconds_per_file_from_junit_xml(tmp_path):
    report = tmp_path / "part-1.xml"
    report.write_text(
        '<testsuites><testsuite>'
        '<testcase classname="tests.test_unit_test_parts" name="a" time="1.25"/>'
        '<testcase classname="tests.test_unit_test_parts.TestX" name="b" time="2"/>'
        '</testsuite></testsuites>')
    assert parts.measure([report]) == {"tests/test_unit_test_parts.py": 3.25}
