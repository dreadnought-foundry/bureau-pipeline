#!/usr/bin/env python3
"""Can this run's changes reach a console-change walk? (DRE-7908)

`tests.yml`'s `walks` job runs every `tests/*_keeps_the_suite_green.py` walk
across shards, 45 to 60 minutes of jobs a run on 2026-10-24. On that day PR
#1119 merged one proof record, its run on main (40133214810) held the walks
open to 3:13 PM PT although nothing in it could turn one red, and the channel
promotion refused `unit-suite-not-finished`. So `walk-list` asks this script
once, and every walk step is gated on the answer (the CEO's decision of
2026-10-24, "Split + run only when relevant"; `standards/engineering.md`,
narrow per change, whole every night).

A SKIP LIST, NOT A REACH LIST. A walk copies the tree and runs most of
`tests/` that reads the model, console and session code, and those import
most of `scripts/` and read most of `config/`. So the rule names the little
that is safe:

  * a changed path CAN SKIP only when it is under `docs/` or `architecture/`
    and no walk, no `tests/console_change_walk.py` and no file in any walk's
    inner run names it — its repo path or its file name anywhere in that
    file's text. The inner run is read off each walk's own
    `suite_files(tree)`, so a new walk's or a new inner test's reads join
    with no edit here. The check is on the changed path's name, so it holds
    the same for a path that exists at the tree, is new, or was deleted;
  * EVERY OTHER PATH RUNS the walks — any `.py`, deleted or renamed ones
    included, anything under `config/`, `.github/`, `standards/`, `briefs/`
    or `tests/fixtures/`, and every root file.

`NOT_SCANNED` holds the one file left out of the scan, the rule's own test.
It must spell out the model files as cases, which puts it in the change
walk's inner run, and the headline docs-only merge as a case too: scanned, it
would turn that document to `run`. It only names documents as inputs to the
rule and never reads them, so leaving its text out opens no gap, and a change
to it is a `.py` and runs every walk.

KNOWN GAP: an inner test that finds a document by glob, or builds its path at
run time, is not seen by the name check. The nightly run is whole, and is the
net for that gap.

THE BASE ON A PUSH TO MAIN is never `github.event.before`. Push A changes code
and its walks go red; push B, right after, changes one document. Against
`event.before` B skips, goes green, and the promotion, which reads only the
candidate's own run, moves `stable` past A's red walks. So `push_base` diffs
from the newest earlier main commit whose `Pipeline Tests` run concluded
`success`, push or schedule, and only when it is an ancestor of the head.
By induction a success run either ran its walks green or diffed only
skip-safe files against an earlier success.

FAILS OPEN. A changed-files list that is missing, empty or holds a line that
is not a path answers `run`; so does a walk that cannot be imported or whose
`suite_files()` raises (the reason names the walk), a scanned file that
cannot be read, and a tree with no walk. A run list that cannot be read, no
earlier success, or a base that is not an ancestor answers no base, and the
workflow step answers `run`.

Usage:
    python3 scripts/walk_reach.py decide --changed <file> --tree <root>
    python3 scripts/walk_reach.py base --runs <runs.json> --head <sha>

`decide` prints `run` or `skip` on its first line and why on its second — the
first path that runs, or what could not be read. `base` prints the base sha or
`none` on its first line and why on its second. Both exit 0.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import unit_test_parts  # noqa: E402
from suite_green_reach import Unreadable, changed_paths  # noqa: E402

#: The only places a changed path may skip from.
SKIPPABLE_UNDER = ("docs/", "architecture/")

#: The walks' one helper, scanned beside them.
HELPER = "tests/console_change_walk.py"

#: Left out of the scan: the rule's own test, which names documents only as
#: the rule's inputs (the module docstring says why).
NOT_SCANNED = ("tests/test_walk_reach.py",)

#: A run whose commit may be the base on a push: green, on main.
BASE_EVENTS = ("push", "schedule")
SHA = re.compile(r"[0-9a-f]{40}")


def scanned_files(tree: Path) -> list[str]:
    """Every walk, the helper and every file in any walk's inner run, less
    `NOT_SCANNED`; repo-relative, sorted. Raises `Unreadable` naming the walk
    that cannot be imported or whose `suite_files()` raises."""
    tree = Path(tree)
    walks = unit_test_parts.walks(tree)
    if not walks:
        raise Unreadable(f"no walk ({unit_test_parts.WALK}) under {tree / 'tests'}")
    files = set(walks) | {HELPER}
    for walk in walks:
        try:
            inner = unit_test_parts._load_walk(tree, walk).suite_files(tree)
            files |= {str(rel) for rel in inner}
        except Exception as exc:  # noqa: BLE001 — any failure here is a walk it cannot read
            raise Unreadable(f"the walk {walk}: {type(exc).__name__}: {exc}") from exc
    return sorted(files - set(NOT_SCANNED))


def scanned_texts(tree: Path) -> dict[str, str]:
    """`scanned_files`, each mapped to its text."""
    texts = {}
    for rel in scanned_files(tree):
        try:
            texts[rel] = (Path(tree) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise Unreadable(f"{rel}: {exc}") from exc
    return texts


def naming(path: str, texts: dict[str, str]) -> str | None:
    """The first scanned file whose text holds `path` or its file name."""
    name = Path(path).name
    return next((rel for rel, text in texts.items()
                 if path in text or name in text), None)


def decide(changed: Path, tree: Path) -> tuple[str, str]:
    """`("run", <the first path that runs>)`, `("skip", <why>)`, or
    `("run", <what could not be read>)`."""
    texts = None
    try:
        paths = changed_paths(changed)
        for path in paths:
            if not path.startswith(SKIPPABLE_UNDER):
                return "run", f"{path} is not under docs/ or architecture/"
            if texts is None:
                texts = scanned_texts(tree)
            by = naming(path, texts)
            if by:
                return "run", f"{path} is named by {by}"
    except Unreadable as exc:
        return "run", f"cannot read: {exc}"
    return "skip", (f"each of the {len(paths)} changed file(s) is under docs/ or "
                    f"architecture/ and named by no walk, its helper or its inner run")


def push_base(runs, head: str, is_ancestor) -> tuple[str | None, str]:
    """The compare base for a push of `head` to main: the newest earlier main
    commit whose run in `runs` (the `workflow_runs` of `GET .../actions/
    workflows/tests.yml/runs`) concluded `success`, when `is_ancestor(sha)`
    holds. `(None, <why>)` when there is none, it cannot be read, or it is
    not an ancestor."""
    try:
        green = [r for r in runs
                 if r["head_branch"] == "main" and r["event"] in BASE_EVENTS
                 and r["conclusion"] == "success" and r["head_sha"] != head]
        green.sort(key=lambda r: str(r["created_at"]), reverse=True)
    except (TypeError, KeyError, AttributeError) as exc:
        return None, f"cannot read the earlier Pipeline Tests runs: {type(exc).__name__}: {exc}"
    if not green:
        return None, "no earlier commit on main has a Pipeline Tests run that concluded success"
    sha = green[0]["head_sha"]
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        return None, f"the newest green run's commit is not a sha: {sha!r}"
    try:
        ancestor = is_ancestor(sha)
    except Exception as exc:  # noqa: BLE001 — a check that cannot answer is not an ancestor
        return None, f"cannot tell whether {sha} is an ancestor of {head}: {exc}"
    if not ancestor:
        return None, f"the last green commit on main, {sha}, is not an ancestor of {head}"
    return sha, f"{sha} is the newest earlier commit on main whose Pipeline Tests run concluded success"


def git_is_ancestor(head: str):
    """`git merge-base --is-ancestor <sha> <head>`: exit 0 holds, 1 does not,
    anything else raises."""
    def check(sha: str) -> bool:
        proc = subprocess.run(["git", "merge-base", "--is-ancestor", sha, head],
                              capture_output=True, text=True)
        if proc.returncode not in (0, 1):
            raise OSError(f"git merge-base exited {proc.returncode}: {proc.stderr.strip()}")
        return proc.returncode == 0
    return check


def base(runs_file: Path, head: str) -> tuple[str | None, str]:
    try:
        body = json.loads(Path(runs_file).read_text(encoding="utf-8"))
        runs = body["workflow_runs"]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return None, f"cannot read the earlier Pipeline Tests runs {runs_file}: {exc}"
    return push_base(runs, head, git_is_ancestor(head))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("decide", help="print run or skip for a changed-files list")
    cmd.add_argument("--changed", required=True, type=Path,
                     help="the changed paths, one per line")
    cmd.add_argument("--tree", required=True, type=Path,
                     help="the checked-out tree the walks are read from")
    cmd = sub.add_parser("base", help="print the compare base for a push to main")
    cmd.add_argument("--runs", required=True, type=Path,
                     help="the body of GET .../actions/workflows/tests.yml/runs")
    cmd.add_argument("--head", required=True, help="the pushed commit")
    args = parser.parse_args(argv)
    if args.command == "decide":
        answer, why = decide(args.changed, args.tree)
    else:
        sha, why = base(args.runs, args.head)
        answer = sha or "none"
    print(answer)
    print(why)
    return 0


if __name__ == "__main__":
    sys.exit(main())
