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
limit. In `tests.yml` each walk has a job of its own (DRE-6935): `walks --json`
hands the job list to the matrix, and `run-walks --only FILE` runs that one,
on a clock `tests/test_walks_time_budget.py` holds to each walk's recorded
seconds (DRE-7016).

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

Run: python3 scripts/unit_test_parts.py list --part 1 --of 4
     python3 scripts/unit_test_parts.py check <dir of part-*.txt manifests>
     python3 scripts/unit_test_parts.py run-walks
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
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


def _took(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m{secs:02d}s"


def run_walks(root: Path, only: str | None = None) -> int:
    """Every walk, or only the walk `only` names, one pytest run each, timed.
    1 when a walk failed or there is no walk at all — a pattern that matches
    nothing is a silent green — and 2 when `only` names no walk."""
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
    rows = []
    for rel in found:
        print(f"running walk {rel}", flush=True)
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
            out.write("### Console-change walks\n\n| walk | result | took |\n"
                      "|---|---|---|\n")
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
                         help="one JSON list on one line, for a job matrix; "
                              "no walk is an error")

    p_run = sub.add_parser("run-walks", help="run every walk, timed")
    p_run.add_argument("--root", type=Path, default=ROOT)
    p_run.add_argument("--only", help="run only this walk, repo-relative")

    p_measure = sub.add_parser("measure", help="rebuild the durations record")
    p_measure.add_argument("reports", type=Path, nargs="+")
    p_measure.add_argument("--note", required=True,
                           help="when and how the reports were produced; "
                                "written as the record's `method` entry, and "
                                "the `provenance` map starts empty")

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
            print(json.dumps(found))
            return 0
        print("\n".join(found))
        return 0

    if args.cmd == "run-walks":
        return run_walks(args.root, args.only)

    seconds = measure(args.reports)
    DURATIONS.write_text(json.dumps(
        {"method": args.note, "provenance": {}, "seconds": seconds},
        indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(seconds)} file durations to {DURATIONS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
