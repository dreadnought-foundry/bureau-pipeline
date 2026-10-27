#!/usr/bin/env python3
"""Split the pipeline's unit suite into parallel parts (DRE-5838).

`tests.yml`'s `scripts unit tests` job ran the whole suite in one runner, and
the suite outgrew every clock it was given — 5, 10, 20, 40, then 80 minutes in
a month (`tests/test_unit_suite_time_budget.py` carries the history). On
2026-10-04 the 34 most recent runs took 23 to 38 minutes against a 40-minute
cap, and PR #721 lost the job twice with no test failed. agent-bureau met the
same wall with its console suite and split it (DRE-4865); this is the same move.

## What it does

`list` hands one part its share of the test files. The files are the ones
`pytest tests` collects (`test_*.py` and `*_test.py`, recursively, skipping the
directories pytest skips), and they are split by WEIGHT — each file's measured
seconds from `config/unit-test-durations.json` — with the heaviest placed
first, each on the lightest part so far. A file with no measurement yet (every
new test file, until somebody re-measures) weighs as the median measured file,
so new files spread out instead of piling onto one part. The weights only
balance the parts; a stale record makes them uneven, never incomplete.

`list --manifest FILE` also writes down what this part was handed, and `check`
reads every part's record back and compares it with the tree: a test file in
no part, a test file in two parts, a part that never reported, or parts that
disagree on how many parts there are, each fails the build by name. It checks
what the parts RAN, not a recomputation of what they should have run — a
matrix that drops an entry, or a part run against another commit, is caught
here rather than trusted.

THE WALKS ARE NOT IN ANY PART (DRE-6909). A walk is a
`tests/*_keeps_the_suite_green.py` file: it copies the tree, applies a console
save and runs the model tests inside the copy, once per case
(`tests/console_change_walk.py`). On 2026-10-11 the agent-move walk alone put
part 1 past its 30-minute limit (run 38142539090) with no test failed, and
every pull request paid for it. So `discover` and `list` leave the walks out,
`check` names a walk a part ran, and `walks` lists them by that file name
alone — a walk added later needs no edit here or in `tests.yml`. `run-walks`
runs each walk on its own and prints how long each took (and writes it to the
job summary), so the walks' clock is watched rather than discovered at its
limit. In `tests.yml` each walk had a job of its own (DRE-6935), and now runs
across SHARDS (DRE-7907): on 2026-10-24 the every-default walk took 60m07s in
one job, and `unit-result` waited on it. `walks --json` hands the matrix one
entry per (walk, shard) — `{"walk", "shard", "shards"}` — and `run-walks
--only FILE` runs that walk with the shard `BUREAU_WALK_SHARD` of
`BUREAU_WALK_SHARDS` names, whose cases `tests/console_change_walk.py`'s
`shard` picks, and prints the shard and how many cases it selected. Each
walk's shard count is the smallest whose largest shard projects at or under
`WALK_SHARD_TARGET_SECONDS`, from the walk's CI seconds in the durations
record and its case count (`projected_shard_seconds`), so a walk that gains
cases gains shards with no edit here or in `tests.yml`;
`tests/test_walks_time_budget.py` holds every shard to the target and to the
walks job's clock.

`measure` rebuilds the durations record from pytest `--junit-xml` reports, or
from the GitHub Actions logs of the parts' `pytest -v` (each result line is
timestamped by the runner, so a green run's logs ARE a measurement):

    gh run view <run> --log --job <job> > part-1.log   # one per part
    python3 scripts/unit_test_parts.py measure part-*.log --note "<how>"

The record keeps three entries (DRE-7508): `method`, one short line saying how
the whole record was measured (the `--note`); `seconds`, each file's seconds;
and `provenance`, one line per file saying where that file's seconds came from
when they are not from `method`'s run. A pull request that adds a test file
records it without re-measuring: its seconds in `seconds` and one line in
`provenance`, both keyed by its path, the `provenance` keys in path order — a
CI run and job, or a local pytest run, and the card. Each file is a line of its
own, so two pull requests that each add a test merge without a conflict here,
as `seconds` always did. `measure` replaces all three: every second it writes
is from its own reports, so it starts `provenance` empty.

THE NIGHTLY RE-MEASURES BEFORE THE FLOOR (DRE-7577). The record must carry
seconds for 90% of the test files (`tests/test_unit_test_parts.py`), and every
pull request that adds one moves the tree toward that floor; on 2026-10-18 a
docs-only pull request failed on files others had added. `due` answers whether
the record is under `REMEASURE_MARGIN`, a margin above the floor, and
`refresh` rebuilds it from the nightly's own part logs through `measure`.
The walks run in no part, so `refresh` keeps each walk's seconds and
`provenance` line as they were — `tests/test_walks_time_budget.py` reads both
— and its `method` names the run and the jobs it measured. `tests.yml`'s
schedule-only `durations-refresh` job asks `due`, and when it answers
`refresh`, pushes the record alone to `bot/test-durations` and opens or
updates that one pull request.

A PART RED ONLY ON THE FLOOR IS STILL A MEASUREMENT (DRE-7726). The refresh
needed every part green, and under the floor the part that runs
`tests/test_unit_test_parts.py` fails on the floor itself — so once crossed,
the refresh could never run again (part 4 of run 39315663679 on 2026-10-19:
`5 failed, 6725 passed`, all five the tests below). `coverage-only` reads a
red part's log and answers whether pytest finished and every test it failed
is one of `COVERAGE_TESTS`. The job keeps a part that succeeded or failed,
still needs all four, and measures a failed one only on that answer; its
`method` then names the job as red only on the durations-coverage tests. A
red part's log measures like a green one: a `FAILED` result line is timed
like a `PASSED` one, and pytest's closing `FAILED <id>` lines do not match.

Run: python3 scripts/unit_test_parts.py list --part 1 --of 4
     python3 scripts/unit_test_parts.py check <dir of part-*.txt manifests>
     python3 scripts/unit_test_parts.py run-walks
     python3 scripts/unit_test_parts.py due
     python3 scripts/unit_test_parts.py refresh part-*.log --run <run> --job <job>...
     python3 scripts/unit_test_parts.py coverage-only part-*.log
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import os
import re
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Each test file's measured seconds. Balance only — see the module docstring.
DURATIONS = ROOT / "config" / "unit-test-durations.json"

#: pytest's default `python_files`, which this repo does not override.
PYTHON_FILES = ("test_*.py", "*_test.py")

#: pytest's default `norecursedirs`, plus `__pycache__`, which it never
#: collects from either.
NORECURSE = ("*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules",
             "venv", "{arch}", "__pycache__")

#: A walk's file name (DRE-6909) — `tests/console_change_walk.py`'s `WALK`.
WALK = "*_keeps_the_suite_green.py"

#: A walk shard job's fixed cost, in seconds (DRE-7907): set up, checkout,
#: the cached install, pytest starting and the walk's case-free tests. The
#: change walk's job 120989216692 (run 40133214810, 2026-10-24) took 0m19s
#: with no case in it; the other walks' jobs spent 8 to 14 seconds outside
#: the walk step on the same runs.
WALK_JOB_FIXED_SECONDS = 19

#: The most a walk shard may project, in seconds (DRE-7907): 15 minutes
#: halved, because `tests.yml` documents run-to-run spread of up to 2x on
#: GitHub's 2-core runners, so a shard projected here lands at or under 15
#: minutes on a slow one.
WALK_SHARD_TARGET_SECONDS = 450

#: The weight of a file when nothing is measured at all.
DEFAULT_WEIGHT = 1.0

#: First line of a part's record.
HEADER = "# unit test part {part} of {of}"


def _collect(root: Path, patterns) -> list[str]:
    """The files under `tests/` matching `patterns`, skipping the directories
    pytest skips; repo-relative, sorted."""
    tests = root / "tests"
    found = []

    def walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                if not any(fnmatch.fnmatch(entry.name, p) for p in NORECURSE):
                    walk(entry)
            elif any(fnmatch.fnmatch(entry.name, p) for p in patterns):
                found.append(entry.relative_to(root).as_posix())

    if tests.is_dir():
        walk(tests)
    return sorted(found)


def is_walk(rel: str) -> bool:
    """A console-change walk, by its file name alone."""
    return fnmatch.fnmatch(Path(rel).name, WALK)


def walks(root: Path = ROOT) -> list[str]:
    """The walks, repo-relative, sorted — the walks' job runs these."""
    return _collect(root, (WALK,))


def _helper():
    """`tests/console_change_walk.py`, the walks' one helper. This tree's
    `scripts` and `tests` go LAST on the path, so a walk under another root
    finds its own modules first and this tree's where it has none."""
    for path in (ROOT / "scripts", ROOT / "tests"):
        if str(path) not in sys.path:
            sys.path.append(str(path))
    import console_change_walk
    return console_change_walk


def _load_walk(root: Path, rel: str):
    """The walk `rel` under `root`, imported as a module of its own."""
    _helper()
    name = "walk_" + re.sub(r"\W", "_", f"{root}/{rel}")
    spec = importlib.util.spec_from_file_location(name, root / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def walk_cases(root: Path, rel: str) -> list[str] | None:
    """The walk's case names, in order, read off its `CASES` by the helper
    (`tests/console_change_walk.py`'s `case_names`) — or None when the walk
    cannot be imported or holds no `CASES`. Whatever cannot be read runs
    whole, in one shard, and fails there by its own pytest run."""
    try:
        return _helper().case_names(_load_walk(root, rel).CASES)
    except Exception:  # noqa: BLE001 — fail open: the walk's own run reports it
        return None


def _pairs(n: int) -> int:
    """`n` rounded up to the next multiple of 2: a walk runs its cases two at
    a time on GitHub's 2-core runners."""
    return n + n % 2


def projected_shard_seconds(seconds: float, cases: int, shards: int) -> float:
    """The largest shard's projected seconds when a walk recorded at
    `seconds` for its `cases` runs in `shards` shards: the fixed cost, plus
    the rest scaled by the largest shard's cases over the walk's, both
    counts rounded up to pairs."""
    fixed = WALK_JOB_FIXED_SECONDS
    if cases < 1:
        return float(seconds)
    largest = -(-cases // shards)
    return fixed + max(seconds - fixed, 0.0) * _pairs(largest) / _pairs(cases)


def shard_count(seconds: float | None, cases: int | None) -> int:
    """The smallest shard count whose largest shard projects at or under
    `WALK_SHARD_TARGET_SECONDS`. One shard for a walk with no recorded
    seconds or no readable cases, and one shard a case when nothing fits —
    `tests/test_walks_time_budget.py` names both."""
    if not seconds or not cases:
        return 1
    for n in range(1, cases + 1):
        if projected_shard_seconds(seconds, cases, n) <= WALK_SHARD_TARGET_SECONDS:
            return n
    return cases


def walk_shards(root: Path = ROOT, record: Path = DURATIONS) -> list[dict]:
    """One entry per (walk, shard), in walk order then shard order: the walk,
    its shard number and its shard count — the walks job's matrix."""
    seconds = load_weights(record)
    out = []
    for rel in walks(root):
        names = walk_cases(root, rel)
        of = shard_count(seconds.get(rel), len(names) if names else None)
        out += [{"walk": rel, "shard": n, "shards": of} for n in range(1, of + 1)]
    return out


def discover(root: Path = ROOT) -> list[str]:
    """The test files `pytest tests` collects, less the walks, repo-relative,
    sorted — the files the parts split."""
    return [f for f in _collect(root, PYTHON_FILES) if not is_walk(f)]


def load_weights(path: Path = DURATIONS) -> dict[str, float]:
    """The measured seconds per file; empty when there is no record."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {str(k): float(v) for k, v in (record.get("seconds") or {}).items()}


def weigh(files: list[str], measured: dict[str, float]) -> dict[str, float]:
    """Every file's weight: its measurement, or the median measured file."""
    known = [measured[f] for f in measured]
    typical = statistics.median(known) if known else DEFAULT_WEIGHT
    return {f: measured.get(f, typical) for f in files}


def assign(files: list[str], of: int, measured: dict[str, float]) -> list[list[str]]:
    """Split `files` into `of` parts by weight. Deterministic whatever the
    order `files` arrives in; each part's files come back sorted.

    STABLE ON PURPOSE. Every file in the durations record is placed first,
    heaviest first, each onto the lightest part so far (ties to the lower
    part) — computed from the RECORD, including files since deleted, so where
    a measured file runs depends on nothing but the record. Files the record
    does not know (new ones) are then placed in name order the same way. So a
    pull request that adds or deletes a test file moves no other file between
    parts. That matters here because test modules leak state through the
    process — `reconcile` reads `REPO_SLUG` once, at whichever module's import
    comes first — so a file's neighbours in its part are part of what it was
    tested against, and a reshuffle would expose some unrelated test's luck in
    a pull request that never touched it. Only re-measuring moves files, and
    the pull request that re-measures runs every part.
    """
    if of < 1:
        raise ValueError(f"need at least one part, got {of}")
    if of > len(files):
        raise ValueError(
            f"{of} parts but only {len(files)} test files — a part would be "
            f"empty, and pytest handed no files runs everything")
    present = set(files)
    weights = weigh(files, measured)
    loads = [0.0] * of
    split: list[list[str]] = [[] for _ in range(of)]

    def place(f: str, weight: float) -> None:
        i = min(range(of), key=lambda i: (loads[i], i))
        if f in present:
            split[i].append(f)
        loads[i] += weight

    for f in sorted(measured, key=lambda f: (-measured[f], f)):
        place(f, measured[f])
    for f in sorted(present - set(measured)):
        place(f, weights[f])
    if not all(split):
        raise ValueError(
            "a part has no test files left — most of the durations record "
            "names deleted files; re-measure it")
    return [sorted(p) for p in split]


def part_files(root: Path, part: int, of: int,
               measured: dict[str, float]) -> list[str]:
    """Part `part` (1-based) of `of`. A walk's measured seconds weigh on no
    part: it runs in none."""
    if not 1 <= part <= of:
        raise ValueError(f"part {part} is outside 1..{of}")
    measured = {f: s for f, s in measured.items() if not is_walk(f)}
    return assign(discover(root), of, measured)[part - 1]


def manifest(part: int, of: int, files: list[str]) -> str:
    """A part's record of the files it was handed."""
    return "\n".join([HEADER.format(part=part, of=of), *files]) + "\n"


def _read_manifest(path: Path) -> tuple[int, int, list[str]] | None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return None
    words = lines[0].split()
    try:
        if lines[0] != HEADER.format(part=int(words[4]), of=int(words[6])):
            return None
        part, of = int(words[4]), int(words[6])
    except (IndexError, ValueError):
        return None
    return part, of, [ln.strip() for ln in lines[1:] if ln.strip()]


def check(root: Path, ran: Path) -> list[str]:
    """Every problem with the parts' records in `ran`, compared with the tree.

    An empty list means every test file ran in exactly one part."""
    problems = []
    records: dict[int, tuple[int, list[str]]] = {}
    counts = set()
    for path in sorted(ran.rglob("*.txt")):
        read = _read_manifest(path)
        if read is None:
            problems.append(f"{path.name} is not a unit-test part record")
            continue
        part, of, files = read
        counts.add(of)
        seen = records.get(part)
        if seen is not None and seen != (of, sorted(files)):
            problems.append(
                f"part {part} reported twice with different files "
                f"({path.name}) — two runs of one part disagree")
            continue
        records[part] = (of, sorted(files))
    if not records:
        return problems + [f"no part reported what it ran (nothing in {ran})"]
    if len(counts) > 1:
        problems.append(
            f"the parts disagree on how many parts there are: {sorted(counts)}")
    of = max(counts)
    for part in range(1, of + 1):
        if part not in records:
            problems.append(f"part {part} of {of} reported nothing")
    where: dict[str, list[int]] = {}
    for part, (_, files) in sorted(records.items()):
        for f in files:
            where.setdefault(f, []).append(part)
    tree = discover(root)
    for f in tree:
        hits = where.get(f, [])
        if not hits:
            problems.append(f"{f} is in no part — it did not run")
        elif len(hits) > 1:
            listed = ", ".join(str(h) for h in hits[:-1]) + f" and {hits[-1]}"
            problems.append(f"{f} is in parts {listed} — it ran more than once")
    for f in sorted(set(where) - set(tree)):
        if is_walk(f):
            problems.append(f"{f} was run by part(s) {where[f]}, but it is a "
                            f"walk — the walks run in their own job, never in "
                            f"a part")
            continue
        problems.append(f"{f} was run by part(s) {where[f]} but is not a test "
                        f"file in this tree")
    return problems


#: One result line of a GitHub Actions log of `pytest -v`: the runner's
#: timestamp, then the test's node id.
LOG_LINE = re.compile(
    r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?Z (tests/\S+?\.py)::")


def _measure_log(log: Path, seconds: dict[str, float]) -> None:
    """Seconds per file from a CI log of `pytest -v`: each result line's
    timestamp minus the one before it, summed per file. Approximate by a test
    at each file's edges, which is plenty for balancing."""
    previous = None
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LOG_LINE.search(line)
        if not m:
            continue
        stamp = datetime.fromisoformat(m.group(1)).timestamp() + float(
            "0" + (m.group(2) or ".0")[:7])
        if previous is not None:
            f = m.group(3)
            seconds[f] = seconds.get(f, 0.0) + max(0.0, stamp - previous)
        previous = stamp


def measure(reports: list[Path]) -> dict[str, float]:
    """Seconds per test file, summed from pytest junit-xml reports or from
    GitHub Actions logs of `pytest -v` (anything not ending `.xml`)."""
    seconds: dict[str, float] = {}
    for report in reports:
        if report.suffix != ".xml":
            _measure_log(report, seconds)
            continue
        for case in ET.parse(report).getroot().iter("testcase"):
            dotted = case.get("classname") or ""
            # pytest writes `tests.test_x.SomeClass` or `tests.test_x`.
            parts = dotted.split(".")
            for n in range(len(parts), 0, -1):
                rel = "/".join(parts[:n]) + ".py"
                if (ROOT / rel).is_file():
                    seconds[rel] = seconds.get(rel, 0.0) + float(case.get("time") or 0)
                    break
    return {f: round(s, 2) for f, s in sorted(seconds.items())}


#: Re-measure once fewer than this share of the test files carry recorded
#: seconds — a margin above the 90% floor `tests/test_unit_test_parts.py`
#: holds the record to (DRE-7577). The tree gains about 9 test files a day
#: (770 on 2026-10-18, 779 on 2026-10-19), and 5% of 779 is about 39 files, so
#: the margin trips about 39 files, roughly four days, before the floor: a
#: nightly that is red or missed for a night or two still re-measures in time.
REMEASURE_MARGIN = 0.95

#: The start of the shared `method` sentence that says what a walk's seconds
#: are. A refresh keeps the walks' seconds, so it keeps that sentence too.
WALKS_SENTENCE = "every walk (tests/*_keeps_the_suite_green.py)"


#: The durations-coverage tests (DRE-7726): the three test functions that
#: fail, in every parametrization, when the record is under its 90% floor —
#: and, measured on 2026-10-19, the only ones that do. A part red on these and
#: nothing else ran every test it was handed, so its log is still a
#: measurement. `tests/test_unit_test_parts.py` holds each name to a test
#: function it defines, so a rename fails there rather than closing the
#: refresh again.
COVERAGE_TESTS = (
    "tests/test_unit_test_parts.py::test_the_durations_record_covers_the_live_tree",
    "tests/test_unit_test_parts.py::test_the_coverage_check_fails_under_ninety_percent",
    "tests/test_unit_test_parts.py::test_the_coverage_check_fails_without_the_shared_method",
)

#: The runner's timestamp at the head of each line of a job log.
_STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ?")

#: pytest's closing line: `==== 5 failed, 6725 passed in 1201.13s (0:20:01) ====`.
_SUMMARY = re.compile(r"^=+ .+ in \d+(?:\.\d+)?s\b.*=+\s*$")

#: A failed or errored node id: the short summary's `FAILED <id> - <why>`,
#: or a `pytest -v` result line `<id> FAILED [ 50%]`. A parametrized id may
#: hold spaces inside its brackets.
_NODE = r"(?P<id>[^\s\[]+(?:\[[^\]]*\])?)"
_OUTCOME_FIRST = re.compile(rf"^(?P<what>FAILED|ERROR) {_NODE}")
_OUTCOME_LAST = re.compile(rf"^{_NODE} (?P<what>FAILED|ERROR)\b")


def coverage_only(logs: list[Path]) -> str:
    """Why these red part logs are NOT red only on the durations-coverage
    tests, or "" when they are (DRE-7726).

    Each log must carry pytest's closing summary line — a part canceled at
    its clock never printed one, and did not run everything — and every test
    it names `FAILED` must be one of `COVERAGE_TESTS`, its `[...]`
    parametrization stripped. An `ERROR` (a fixture, a collection) is never
    the floor, so any one refuses. The first problem is the answer."""
    for log in logs:
        text = log.read_text(encoding="utf-8", errors="replace")
        lines = [_STAMP.sub("", line, count=1) for line in text.splitlines()]
        for line in lines:
            seen = _OUTCOME_FIRST.match(line) or _OUTCOME_LAST.match(line)
            if not seen:
                continue
            node, what = seen.group("id"), seen.group("what")
            if what == "ERROR":
                return (f"{node} is ERROR in {log.name} — an error is never "
                        f"the durations-coverage failure")
            if node.split("[", 1)[0] not in COVERAGE_TESTS:
                return (f"{node} FAILED in {log.name}, and it is not one of "
                        f"the durations-coverage tests")
        if not any(_SUMMARY.match(line) for line in lines):
            return (f"{log.name} has no pytest summary line — the part did "
                    f"not finish, so it ran less than its share")
    return ""


def coverage(record: dict, files: list[str]) -> tuple[int, int]:
    """How many of `files` carry recorded seconds in `record`, and how many
    files there are. Seconds for files the tree no longer has count for
    nothing."""
    seconds = record.get("seconds") or {}
    return sum(1 for f in files if f in seconds), len(files)


def remeasure_due(record: dict, files: list[str]) -> bool:
    """THE decision: re-measure when fewer than `REMEASURE_MARGIN` of the
    test files carry recorded seconds; at the margin or above, nothing to do.
    Exact arithmetic, so a record at exactly 95% is never due by a rounding."""
    measured, total = coverage(record, files)
    return measured < Fraction(str(REMEASURE_MARGIN)) * total


def coverage_line(record: dict, files: list[str]) -> str:
    """The coverage `due` read, for a person: measured, total, percent."""
    measured, total = coverage(record, files)
    percent = 100.0 * measured / total if total else 100.0
    where = ("under" if remeasure_due(record, files) else "at or above")
    return (f"{measured} of {total} test files carry recorded seconds "
            f"({percent:.1f}%), {where} the {REMEASURE_MARGIN:.0%} margin")


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else (
        ", ".join(names[:-1]) + " and " + names[-1])


def refresh(record: dict, logs: list[Path], run: str, jobs: list[str],
            head: str | None = None,
            coverage_failed: list[str] | None = None) -> dict:
    """`record` re-measured from one CI run's part logs, through `measure`.

    Every file the logs time takes their seconds, and its `provenance` line
    goes: its seconds come from `method`'s run now. The walks run in no part,
    so each walk keeps its `seconds` entry and its `provenance` line exactly
    as they were, and `method` keeps the sentence saying what those seconds
    are. `method` names the run and each job it read, and says every part
    was green unless `coverage_failed` names jobs red only on the
    durations-coverage tests (DRE-7726), which it then names."""
    old_seconds = record.get("seconds") or {}
    seconds = {f: s for f, s in measure(logs).items() if not is_walk(f)}
    seconds.update({f: s for f, s in old_seconds.items() if is_walk(f)})
    provenance = {f: line for f, line in (record.get("provenance") or {}).items()
                  if is_walk(f)}
    at_head = f", head {head[:7]}" if head else ""
    if coverage_failed:
        red = (f"{'job' if len(coverage_failed) == 1 else 'jobs'} "
               f"{_and(list(coverage_failed))} red only on the "
               f"durations-coverage tests (DRE-7726), every other part green")
    else:
        red = "every part green"
    method = (
        f"CI run {run} (the nightly on main{at_head}), "
        f"{'job' if len(jobs) == 1 else 'jobs'} {_and(jobs)}: the suite in "
        f"{len(jobs)} parts, {red}, re-measured by tests.yml's "
        f"durations refresh (DRE-7577); seconds per file from the runner's "
        f"timestamps on each pytest -v result line; the walks run in no part, "
        f"so each keeps its seconds and its `provenance` line from the record "
        f"before")
    old_method = str(record.get("method") or "")
    if WALKS_SENTENCE in old_method:
        method += "; " + old_method[old_method.index(WALKS_SENTENCE):]
    return {"method": method, "provenance": dict(sorted(provenance.items())),
            "seconds": dict(sorted(seconds.items()))}


def _took(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m{secs:02d}s"


def shard_line(root: Path, rel: str) -> str:
    """The one line naming the walk, the shard this job runs of it and how
    many of its cases that shard selected (DRE-7907) — read through the
    helper, by the same names the walk's own run reads."""
    helper = _helper()
    shard = os.environ.get(helper.SHARD_ENV, "1")
    shards = os.environ.get(helper.SHARDS_ENV, "1")
    head = f"shard {shard} of {shards} of walk {rel}"
    names = walk_cases(root, rel)
    if names is None:
        return f"{head}: its cases could not be read"
    try:
        picked = helper.shard(names)
    except ValueError as exc:
        return f"{head}: {exc}"
    return f"{head}: {len(picked)} case(s) selected of {len(names)}"


def run_walks(root: Path, only: str | None = None) -> int:
    """Every walk, or only the walk `only` names, one pytest run each, timed,
    each run as the shard the environment names (every case when it names
    none). 1 when a walk failed or there is no walk at all — a pattern that
    matches nothing is a silent green — and 2 when `only` names no walk."""
    found = walks(root)
    if not found:
        print(f"unit_test_parts: no walk ({WALK}) under {root / 'tests'} — "
              f"the walks' job would prove nothing", file=sys.stderr)
        return 1
    if only is not None:
        if only not in found:
            print(f"unit_test_parts: {only} is not a walk under "
                  f"{root / 'tests'} — the walks are {found}", file=sys.stderr)
            return 2
        found = [only]
    rows, shards = [], []
    for rel in found:
        print(f"running walk {rel}", flush=True)
        shards.append(shard_line(root, rel))
        print(shards[-1], flush=True)
        start = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", rel],
            cwd=root)
        took = time.monotonic() - start
        verdict = "passed" if proc.returncode == 0 else "failed"
        rows.append((rel, verdict, took))
        print(f"walk {rel} ... {verdict} ... took {_took(took)}", flush=True)
    failed = [rel for rel, verdict, _ in rows if verdict == "failed"]
    print(f"{len(rows)} walk(s): {len(rows) - len(failed)} passed, "
          f"{len(failed)} failed")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("### Console-change walks\n\n")
            for line in shards:
                out.write(f"{line}\n\n")
            out.write("| walk | result | took |\n|---|---|---|\n")
            for rel, verdict, took in rows:
                out.write(f"| `{rel}` | {verdict} | {_took(took)} |\n")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="print one part's test files")
    p_list.add_argument("--root", type=Path, default=ROOT)
    p_list.add_argument("--part", type=int, required=True, help="1-based")
    p_list.add_argument("--of", type=int, required=True)
    p_list.add_argument("--manifest", type=Path,
                        help="also write this part's record here")

    p_check = sub.add_parser("check", help="every test file ran exactly once")
    p_check.add_argument("--root", type=Path, default=ROOT)
    p_check.add_argument("ran", type=Path, help="directory of part records")

    p_walks = sub.add_parser("walks", help="print the walks")
    p_walks.add_argument("--root", type=Path, default=ROOT)
    p_walks.add_argument("--json", action="store_true",
                         help="one JSON list on one line, for a job matrix: "
                              "one object per walk shard, carrying the walk, "
                              "its shard and its shard count; no walk is an "
                              "error")
    p_walks.add_argument("--record", type=Path, default=DURATIONS,
                         help="the durations record holding each walk's CI "
                              "seconds (default: this tree's)")

    p_run = sub.add_parser("run-walks", help="run every walk, timed")
    p_run.add_argument("--root", type=Path, default=ROOT)
    p_run.add_argument("--only", help="run only this walk, repo-relative")

    p_measure = sub.add_parser("measure", help="rebuild the durations record")
    p_measure.add_argument("reports", type=Path, nargs="+")
    p_measure.add_argument("--note", required=True,
                           help="when and how the reports were produced; "
                                "written as the record's `method` entry, and "
                                "the `provenance` map starts empty")

    p_due = sub.add_parser(
        "due", help="is the durations record due a re-measure? first line "
                    "`refresh` or `hold`, second line the coverage read")
    p_due.add_argument("--root", type=Path, default=ROOT)
    p_due.add_argument("--record", type=Path,
                       help="the durations record (default: this tree's)")

    p_refresh = sub.add_parser(
        "refresh", help="re-measure the durations record from one CI run's "
                        "part logs, keeping every walk's seconds and "
                        "provenance; writes nothing that would still be due")
    p_refresh.add_argument("logs", type=Path, nargs="+")
    p_refresh.add_argument("--root", type=Path, default=ROOT)
    p_refresh.add_argument("--run", required=True, help="the CI run id")
    p_refresh.add_argument("--job", action="append", required=True,
                           help="a part's job id, once per log")
    p_refresh.add_argument("--head", help="the commit the run tested")
    p_refresh.add_argument(
        "--coverage-failed-job", action="append", default=[],
        help="a part's job id that failed only on the durations-coverage "
             "tests (`coverage-only` said so); once per such job")

    p_only = sub.add_parser(
        "coverage-only", help="exit 0 when every red part log finished and "
                              "failed only on the durations-coverage tests")
    p_only.add_argument("logs", type=Path, nargs="+")

    args = parser.parse_args(argv)

    if args.cmd == "list":
        try:
            files = part_files(args.root, args.part, args.of, load_weights())
        except ValueError as exc:
            print(f"unit_test_parts: {exc}", file=sys.stderr)
            return 2
        if args.manifest:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(manifest(args.part, args.of, files),
                                     encoding="utf-8")
        print("\n".join(files))
        return 0

    if args.cmd == "check":
        problems = check(args.root, args.ran)
        total = len(discover(args.root))
        if problems:
            print(f"unit test parts: {len(problems)} problem(s) "
                  f"across {total} test files:")
            for p in problems:
                print(f"  - {p}")
            return 1
        print(f"unit test parts: all {total} test files ran in exactly one part")
        return 0

    if args.cmd == "walks":
        found = walks(args.root)
        if args.json:
            if not found:
                print(f"unit_test_parts: no walk ({WALK}) under "
                      f"{args.root / 'tests'} — the walks' jobs would prove "
                      f"nothing", file=sys.stderr)
                return 1
            print(json.dumps(walk_shards(args.root, args.record)))
            return 0
        print("\n".join(found))
        return 0

    if args.cmd == "run-walks":
        return run_walks(args.root, args.only)

    if args.cmd == "due":
        path = args.record or DURATIONS
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            record = {}
        files = discover(args.root)
        print("refresh" if remeasure_due(record, files) else "hold")
        print(coverage_line(record, files))
        return 0

    if args.cmd == "refresh":
        if len(args.job) != len(args.logs):
            print(f"unit_test_parts: {len(args.logs)} log(s) but "
                  f"{len(args.job)} --job — name each part's job once",
                  file=sys.stderr)
            return 2
        stray = [j for j in args.coverage_failed_job if j not in args.job]
        if stray:
            print(f"unit_test_parts: --coverage-failed-job {_and(stray)} is "
                  f"not a --job this refresh reads", file=sys.stderr)
            return 2
        old = json.loads(DURATIONS.read_text(encoding="utf-8"))
        new = refresh(old, args.logs, args.run, args.job, args.head,
                      args.coverage_failed_job)
        files = discover(args.root)
        if remeasure_due(new, files):
            print(f"unit_test_parts: the re-measured record would still be "
                  f"due — {coverage_line(new, files)}; the logs measured too "
                  f"little, so the record is left as it was", file=sys.stderr)
            return 1
        DURATIONS.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {len(new['seconds'])} file durations to {DURATIONS}: "
              f"{coverage_line(new, files)}")
        return 0

    if args.cmd == "coverage-only":
        why = coverage_only(args.logs)
        if why:
            print(why)
            return 1
        print(f"{len(args.logs)} red part log(s): red only on the "
              f"durations-coverage tests")
        return 0

    seconds = measure(args.reports)
    DURATIONS.write_text(json.dumps(
        {"method": args.note, "provenance": {}, "seconds": seconds},
        indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(seconds)} file durations to {DURATIONS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
