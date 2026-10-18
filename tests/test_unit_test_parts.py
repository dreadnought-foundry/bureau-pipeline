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
import re
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
    # Less the walks, which run in their own job (DRE-6909).
    assert set(found) | set(parts.walks(ROOT)) >= {
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


def _where(split: list[list[str]]) -> dict[str, int]:
    return {f: i for i, part in enumerate(split) for f in part}


def test_a_new_test_file_moves_no_other_file_between_parts():
    # Test modules here leak state through the process (a module-level
    # `os.environ.setdefault` read once by `reconcile`), so a file's
    # neighbours in its part are part of what it was tested against. A pull
    # request that adds one test file must not reshuffle every part — that
    # would expose some unrelated test's luck in a PR that never touched it.
    measured = {f: float(i % 5 + 1) for i, f in enumerate(FILES)}
    before = _where(parts.assign(FILES, 4, measured))
    after = _where(parts.assign(FILES + ["tests/test_aa_new.py"], 4, measured))
    assert {f: after[f] for f in FILES} == before


def test_a_deleted_test_file_moves_no_other_file_between_parts():
    measured = {f: float(i % 5 + 1) for i, f in enumerate(FILES)}
    before = _where(parts.assign(FILES, 4, measured))
    kept = [f for f in FILES if f != "tests/test_03.py"]
    after = _where(parts.assign(kept, 4, measured))
    assert {f: after[f] for f in kept} == {f: before[f] for f in kept}
    assert "tests/test_03.py" not in after


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
    # It waits on the parts and, since DRE-6909, on the walks' job too
    # (`tests/test_tests_workflow_parts.py`).
    _, job = _aggregate()
    needs = job.get("needs")
    assert PARTS_JOB in ([needs] if isinstance(needs, str) else needs)


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


def _coverage_problems(record: dict, files: list[str]) -> list[str]:
    """What is wrong with a durations record against the test files on a
    tree: no shared `method` entry, or fewer than 90% of the files with
    recorded seconds. Empty when the record covers the tree."""
    found = []
    if not str(record.get("method") or "").strip():
        found.append("the record's `method` entry is empty or missing — say "
                     "when and how the durations were measured")
    seconds = record.get("seconds") or {}
    measured = [f for f in files if f in seconds]
    if len(measured) < 0.9 * len(files):
        unmeasured = [f for f in files if f not in seconds]
        found.append(
            f"{len(measured)} of {len(files)} test files carry recorded "
            f"seconds, under 90%. For each new test file, add its seconds to "
            f"`seconds` and one line to `provenance` in "
            f"config/unit-test-durations.json, both keyed by the file's path, "
            f"the `provenance` line saying where those seconds came from (a CI "
            f"run and job, or a local pytest run) — or re-measure the whole "
            f"suite (python3 scripts/unit_test_parts.py measure "
            f"<junit.xml or CI log>... --note \"<how>\"). Unmeasured: "
            f"{unmeasured[:10]}")
    return found


def test_the_durations_record_covers_the_live_tree():
    record = json.loads(parts.DURATIONS.read_text(encoding="utf-8"))
    found = _coverage_problems(record, parts.discover(ROOT))
    assert found == [], "\n".join(found)


def test_the_coverage_check_fails_under_ninety_percent():
    record = json.loads(parts.DURATIONS.read_text(encoding="utf-8"))
    files = parts.discover(ROOT)
    assert _coverage_problems(record, files) == []
    keep = int(0.85 * len(files))
    thin = dict(record, seconds={f: 1.0 for f in files[:keep]})
    found = _coverage_problems(thin, files)
    assert len(found) == 1 and "under 90%" in found[0], found
    # The message names the keyed entry, never a clause on a shared note.
    assert "`provenance`" in found[0] and "keyed by the file's path" in found[0]
    assert "clause" not in found[0] and "measured note" not in found[0]


@pytest.mark.parametrize("method", [None, "", "   "])
def test_the_coverage_check_fails_without_the_shared_method(method):
    record = json.loads(parts.DURATIONS.read_text(encoding="utf-8"))
    files = parts.discover(ROOT)
    planted = dict(record)
    if method is None:
        planted.pop("method", None)
    else:
        planted["method"] = method
    found = _coverage_problems(planted, files)
    assert len(found) == 1 and "`method`" in found[0], found


# --- One line per file: the provenance record (DRE-7508) ------------------
#
# The record's note on where each measurement came from was one string on one
# line, 6,888 characters by 2bd0361, and every pull request that added a test
# file added a clause to it. So any two open pull requests that each added a
# test conflicted on that one line (#1134, twice). The `seconds` map beside it
# merged cleanly, because each file is its own line; the provenance is now
# stored the same way, keyed by the file's path.

#: The note as it stood on `main` at 2bd0361, frozen, so the carry-over can be
#: checked clause by clause after the note itself is gone.
OLD_NOTE = ROOT / "tests" / "fixtures" / "unit-test-durations-note-2bd0361.txt"

#: How the old note joined its clauses.
OLD_CLAUSE_JOIN = r"; (?:and|plus) "

#: The every-default walk's clause carried the walks multiplier; that sentence
#: moved to the shared `method` entry and the rest stayed with the walk.
WALKS_SENTENCE = "; every walk (tests/*_keeps_the_suite_green.py)"

#: The longest line the record may hold. The old note's line was 6,888.
LONGEST_LINE = 1000


def _old_clauses() -> list[str]:
    return re.split(OLD_CLAUSE_JOIN, OLD_NOTE.read_text(encoding="utf-8").rstrip("\n"))


def _named_files(clause: str) -> list[str]:
    return [f for f in re.findall(r"tests/[\w*]+\.py", clause) if "*" not in f]


def test_no_line_of_the_durations_record_is_longer_than_a_thousand_characters():
    lines = parts.DURATIONS.read_text(encoding="utf-8").splitlines()
    long = [(n, len(line)) for n, line in enumerate(lines, 1)
            if len(line) > LONGEST_LINE]
    assert long == [], f"lines over {LONGEST_LINE} characters (line, length): {long}"


def test_the_record_keeps_provenance_one_entry_per_file():
    record = json.loads(parts.DURATIONS.read_text(encoding="utf-8"))
    assert "measured" not in record, (
        "the one-string `measured` note is retired — the shared part is "
        "`method`, and each file's provenance is its own `provenance` entry")
    assert isinstance(record.get("provenance"), dict)
    assert list(record["provenance"]) == sorted(record["provenance"]), (
        "keep `provenance` in path order, so a new file's line has neighbours "
        "of its own")
    lines = {line.rstrip(",") for line in
             parts.DURATIONS.read_text(encoding="utf-8").splitlines()}
    for path, entry in record["provenance"].items():
        assert isinstance(entry, str) and entry.strip(), path
        assert f"    {json.dumps(path)}: {json.dumps(entry)}" in lines, (
            f"{path}'s provenance is not one line of its own")


def test_every_clause_of_the_old_note_is_carried_over_word_for_word():
    record = json.loads(parts.DURATIONS.read_text(encoding="utf-8"))
    provenance, method = record["provenance"], record["method"]
    clauses = _old_clauses()
    assert len(clauses) == 31, len(clauses)
    general, *per_file = clauses
    assert _named_files(general) == []
    named = sorted({f for c in per_file for f in _named_files(c)})
    assert len(named) == 33, named
    missing = [f for f in named if f not in provenance]
    assert missing == [], f"not keyed in `provenance`: {missing}"
    split = [c for c in per_file if WALKS_SENTENCE in c]
    assert len(split) == 1, split
    walk_rest, walks_sentence = split[0].split(WALKS_SENTENCE)
    walks_sentence = WALKS_SENTENCE[2:] + walks_sentence
    assert "4.28" in walks_sentence and "(DRE-7016)" in walks_sentence
    # The general clause and the walks multiplier make the shared entry.
    assert general in method and walks_sentence in method, method
    # Every other clause is its files' whole entry; one naming two files is
    # the entry of both.
    for clause in per_file:
        text = walk_rest if clause is split[0] else clause
        assert _named_files(text), clause
        for f in _named_files(text):
            assert provenance[f] == text, (f, provenance[f], text)


def test_every_provenance_key_has_recorded_seconds():
    record = json.loads(parts.DURATIONS.read_text(encoding="utf-8"))
    orphans = sorted(set(record["provenance"]) - set(record["seconds"]))
    assert orphans == [], (
        f"`provenance` names files `seconds` does not: {orphans}")


def _insert_after(mapping: dict, after: str, key: str, value) -> dict:
    out = {}
    for k, v in mapping.items():
        out[k] = v
        if k == after:
            out[key] = value
    assert key in out, after
    return out


def _with_new_file(record: dict, path: str, seconds: float, how: str,
                   seconds_after: str) -> str:
    """The record as a pull request adding the test file `path` writes it:
    its seconds after `seconds_after`, its provenance in path order."""
    out = dict(record)
    out["seconds"] = _insert_after(record["seconds"], seconds_after, path, seconds)
    out["provenance"] = dict(sorted({**record["provenance"], path: how}.items()))
    return json.dumps(out, indent=2) + "\n"


def _merge_file(tmp_path: Path, base: str, ours: str, theirs: str
                ) -> tuple[int, str]:
    for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
        (tmp_path / f"{name}.json").write_text(text, encoding="utf-8")
    proc = subprocess.run(
        ["git", "merge-file", "-p", str(tmp_path / "ours.json"),
         str(tmp_path / "base.json"), str(tmp_path / "theirs.json")],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout


#: Two test files two open pull requests might each add; their paths sort to
#: different places in `provenance`.
NEW_A = "tests/test_dre7508_alpha_probe.py"
NEW_Z = "tests/test_tdd_dre7508_zeta_probe.py"


def test_two_pull_requests_each_adding_a_test_file_merge_cleanly(tmp_path):
    base = parts.DURATIONS.read_text(encoding="utf-8")
    assert json.dumps(json.loads(base), indent=2) + "\n" == base, (
        "the record is not in the shape `measure` writes, so a hand edit and "
        "this test disagree on where its lines are")
    record = json.loads(base)
    keys = list(record["seconds"])
    third, two_thirds = keys[len(keys) // 3], keys[2 * len(keys) // 3]
    prov = sorted(record["provenance"])
    # The two new files land between different neighbours, not both last.
    where = [sorted(prov + [f]).index(f) for f in (NEW_A, NEW_Z)]
    assert where[1] - where[0] >= 2 and where[1] < len(prov), where
    ours = _with_new_file(record, NEW_A, 1.5, "measured for the alpha probe",
                          third)
    theirs = _with_new_file(record, NEW_Z, 2.5, "measured for the zeta probe",
                            two_thirds)
    code, merged = _merge_file(tmp_path, base, ours, theirs)
    assert code == 0, merged
    assert "<<<<<<<" not in merged and ">>>>>>>" not in merged
    out = json.loads(merged)
    for f, s in ((NEW_A, 1.5), (NEW_Z, 2.5)):
        assert out["seconds"][f] == s
        assert f in out["provenance"]
    assert out["method"] == record["method"]

    # The same two pull requests against the old shape — one `measured` note
    # each appends a clause to — conflict on that one line, which is #1134.
    old = {"measured": OLD_NOTE.read_text(encoding="utf-8").rstrip("\n"),
           "seconds": record["seconds"]}
    old_base = json.dumps(old, indent=2) + "\n"

    def old_shape(path: str, seconds: float, after: str) -> str:
        return json.dumps({
            "measured": old["measured"] + f"; and {path}, new, at {seconds} s",
            "seconds": _insert_after(old["seconds"], after, path, seconds),
        }, indent=2) + "\n"

    code, merged = _merge_file(tmp_path, old_base,
                               old_shape(NEW_A, 1.5, third),
                               old_shape(NEW_Z, 2.5, two_thirds))
    assert code != 0 and "<<<<<<<" in merged, (
        "the old one-line note merged cleanly — this test no longer shows "
        "what the provenance map is for")


def test_measure_writes_the_shared_method_and_no_measured_string(tmp_path,
                                                                 monkeypatch):
    log = tmp_path / "part-1.log"
    log.write_text(
        "u\tt\t2026-10-04T22:14:26.0000000Z "
        "tests/test_conftest_marker.py::test_a PASSED [  0%]\n"
        "u\tt\t2026-10-04T22:14:28.5000000Z "
        "tests/test_unit_test_parts.py::test_b PASSED [  1%]\n")
    out = tmp_path / "unit-test-durations.json"
    monkeypatch.setattr(parts, "DURATIONS", out)
    assert parts.main(["measure", str(log), "--note", "CI run 1, four parts"]) == 0
    text = out.read_text(encoding="utf-8")
    record = json.loads(text)
    assert "measured" not in record, record
    assert record["method"] == "CI run 1, four parts"
    assert record["provenance"] == {}
    assert record["seconds"] == {"tests/test_unit_test_parts.py": 2.5}
    assert parts.load_weights(out) == {"tests/test_unit_test_parts.py": 2.5}


def test_the_measure_instructions_name_the_keyed_provenance_entry():
    doc = parts.__doc__
    assert "`provenance`" in doc and "keyed by" in doc, doc
    assert "clause" not in doc
    help_text = subprocess.run(
        [sys.executable, str(SCRIPT), "measure", "--help"],
        capture_output=True, text=True, check=True).stdout
    flat = " ".join(help_text.split())
    assert "method" in flat and "provenance" in flat, flat
    assert "clause" not in flat


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


def _module_defaults(path: Path) -> dict[str, str]:
    """The `os.environ.setdefault(KEY, VALUE)` calls a test module makes at
    import, read with `ast` (never executed)."""
    import ast

    found = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        call = node.value if isinstance(node, ast.Expr) else None
        if (isinstance(call, ast.Call)
                and ast.unparse(call.func) == "os.environ.setdefault"
                and len(call.args) == 2
                and all(isinstance(a, ast.Constant) for a in call.args)):
            found.setdefault(call.args[0].value, call.args[1].value)
    return found


def test_every_part_starts_from_the_environment_the_whole_suite_had():
    """Test modules set environment defaults AT IMPORT, and modules such as
    `reconcile` read them once, at their own first import. In one runner the
    alphabetically first module that sets a key decides it for the whole
    process; a part that does not collect that module would let some other
    module decide, and tests written against the whole-suite value go red —
    which is exactly what the first two runs of this PR did (DRE-5838:
    `test_close_epics`, `test_promote_only`, then
    `test_check_agent_result_failed_delivery` once the split moved).

    So every key a test module defaults must be set in the parts' env, to the
    value the whole suite would have had: the job's own value where the old
    job already set one, otherwise the first module's in collection order.
    """
    env = next(s for s in _jobs()[PARTS_JOB]["steps"]
               if s.get("name") == "Unit tests").get("env", {})
    decided: dict[str, str] = {}
    for rel in parts.discover(ROOT):
        for key, value in _module_defaults(ROOT / rel).items():
            decided.setdefault(key, value)
    assert decided, "no test module sets an environment default?"
    for key, value in decided.items():
        assert key in env, (
            f"{key} is defaulted by test modules at import but not set for "
            f"the parts — whole-suite value {value!r}"
        )
    assert env["REPO_SLUG"] == decided["REPO_SLUG"]
