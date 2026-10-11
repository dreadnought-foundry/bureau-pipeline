#!/usr/bin/env python3
"""The merge gate's fork-refresh decision (DRE-5066, stdlib only).

Origin (live, 2026-09-25, DRE-4912). Two agent-bureau pull requests each added
a `0062_*` alembic migration on `0061_poll_sample`. Each passed the repo's own
migration-head gate (agent-bureau's `scripts/check_migration_head.py`,
DRE-4305), because each was checked against `main` as it stood when its own CI
ran. Nothing re-checked when `main` moved, the merge gate merged the second at
13:06 PT, and `main` forked. The same shape hit DRE-4240/4241 earlier in
September and DRE-4533 on 2026-09-22, with a near miss on 2026-09-26 (#2795).

The line this does NOT cross is DRE-2416's: currency is still not a gate. A
head that is behind `main` merges exactly like a current one, and that stays.
Only a same-prefix addition on both sides refreshes — the pull request adds a
file under a path the repo declares order-sensitive, AND `main` has added a
file under that same path since the branch's merge base. Then the branch is
updated from `main` so CI runs again on a fresh merge ref, where the repo's
own gate refuses the second head before it can land. Nothing else pays a CI
run.

A second rule (DRE-6871) refreshes for a different clash: `main` gained a test
since the pull request's last test run began, and that test names a source
file the pull request changes (or the mirror — the pull request adds a test
naming a source file `main` changed). Each side passed its own CI and the pair
had never run together: agent-bureau #3604 (`RiverRow.fold.test.ts` naming
`greenLightGrid`) and bureau-pipeline #871
(`tests/test_planning_escalation_prior_answer.py` naming
`planning_escalation`) are two of the three recorded clashes, and this rule
would have caught both. Measured over five days it fires on 12% of
agent-bureau merges and 15% of bureau-pipeline merges, so a repo switches it
on for itself. It never holds a pull request with no test run, and currency
alone is still not a gate.

"The pull request's last test run" is the anchor run: the newest run of the
head by `created_at` whose event is `pull_request` and whose path is not in
`merge_gate.DEFAULT_REVIEW_WORKFLOWS`. Never a `push` run, never a review run.
That is condition 1's filter for "CI of the commit", so a review run created
later on `edited` or `ready_for_review` never moves it: in bureau-pipeline it
is `tests.yml`'s `pull_request` run and in agent-bureau `ci.yml`'s. The wiring
reads the listing; this module only decides over the payload it built.

This module is the decision and nothing else: pure functions over GitHub
payloads, no I/O in `decide()`, and a CLI for the workflow, in the shape of
`stale_merge_ref.py` (DRE-3138). The merge-gate wiring and the declaration a
product repo writes are their own cards.

The declaration is `.github/bureau/merge-recheck.json` in the product checkout:

    {"$schema_note": "…", "order_sensitive_paths": ["console/backend/alembic/versions/"],
     "recheck_new_tests": true}

each `order_sensitive_paths` entry a repo-relative directory prefix ending in
`/`, matched with `filename.startswith(prefix)`. `"recheck_new_tests": true`
switches rule 6 on, and then `order_sensitive_paths` may be absent (read as
`[]`). The key absent or `false`: rule 6 is off and rules 1 to 5 decide
exactly as before it existed.

The rule, in order:

  1. No declaration file → `proceed` (the repo opted out). A declaration that
     does not parse, whose `order_sensitive_paths` is not a list of
     `/`-terminated strings, or whose `recheck_new_tests` is anything but a
     JSON boolean → `wait`, naming the file (fail closed).
  2. `behind_by` 0 or missing → `proceed`; the base-advance payload is not
     consulted. The merge gate writes `{}` when its compare read blips, and
     that has always merged.
  3. A receipt `Merge gate: refreshed onto <tip>` for the CURRENT `main` tip
     → `wait`: a refresh was already requested at this tip and has not landed
     (the branch is still behind), so never a second PUT and never `proceed`.
     At most one refresh per `main` commit, the `stale_merge_ref` discipline.
     It sits after rule 2 on purpose — once the refresh lands the branch is
     current, and a receipt for that same tip must not hold it until `main`
     moves again.
  4. Behind, and the base-advance payload is not an object with `files[]` →
     `wait`. A blip is not "safe to merge".
  5. A file counts on either side when its `status` is `added` or `renamed` (a
     renumbered migration arrives as a rename). `refresh` when some declared
     prefix — the first, in declaration order — has a counting file in the
     pull request's `files[]` AND in the base-advance `files[]`. Otherwise
     `proceed` — or, with the switch on, rule 6. This rule is evaluated first
     and wins when it fires.
  6. New tests (DRE-6871), only with `"recheck_new_tests": true`, in order:
     a. A receipt `Merge gate: refreshed for new tests onto <tip>` for ANY
        tip → `proceed`, whatever the since-run payload holds: one refresh
        per pull request, for its whole life. The migrations rule's receipt
        and its one-per-tip rule are untouched.
     b. The since-run payload is the compare `<main's tip when the anchor run
        was created>...<main>`, and it has exactly three shapes. An object
        with `files[]` is evaluated. `{"no_test_run": true}` → `proceed`: no
        test run of this head exists, so there is nothing a refresh would
        re-test against. Anything else, `{}` included, is a read that failed
        → `wait`, as rule 4 answers a blipped base advance. It is read only
        here: never with the switch off, never when the branch is not behind.
     c. A test file has a directory segment `tests`, `test` or `__tests__`,
        or a basename matching `test_*.py`, `*_test.py`, `*.test.<ext>` or
        `*.spec.<ext>`; every other file is a source file. A test is new
        when its `status` is `added` or `renamed`; a source file is changed
        at any status.
     d. A source file's name is its basename up to the first `.`, and names
        shorter than six characters are ignored. A test names it when the
        test's `patch` (for an added file, the whole file) holds the name as
        a whole word, case-sensitive. A new test whose entry carries no
        `patch` — GitHub omits it for large files — names nothing, and the
        reason says which file could not be read.
     e. `refresh` when a new test in the since-run payload names a source
        file the pull request changes, or a new test in the pull request
        names a source file the since-run payload changed. Otherwise
        `proceed`.

Known limit, stated rather than hidden: GitHub's compare API lists at most 300
files per side. A `main` that gained more than that since the merge base can
hide a same-prefix addition; the repo's own head gate on `main` is still the
backstop there. The same limit applies to the since-run payload.

CLI:

    python3 order_sensitive_refresh.py decide --compare-file <compare json> \\
        --base-advance-file <compare json> --declaration-file <path> \\
        [--receipts-file <json list of comment bodies>] \\
        [--since-run-file <compare json>]

stdout line 1 is `decision=refresh|proceed|wait`, line 2 `reason=<one line>`.
On `refresh`, line 3 `prefix=<matched prefix>` (order-sensitive) or `prefix=`
with an empty value (new-tests), line 4 `rule=order-sensitive` or
`rule=new-tests`. Two lines on `proceed` and `wait`. A reader takes `rule=` by
its key, never by its line number. With the switch off the output is the three
lines it was before rule 6 existed, so a refresh with no `rule=` line is an
order-sensitive one. An absent `--since-run-file` reads as `{}`. Exit 0 on
every decision; exit 2 ONLY when `--compare-file` cannot be read as an object
— the head is the subject, and a silent answer there would steer the gate on a
guess.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from dataclasses import dataclass

REFRESH = "refresh"
PROCEED = "proceed"
WAIT = "wait"

DECLARATION_PATH = ".github/bureau/merge-recheck.json"
DECLARATION_KEY = "order_sensitive_paths"
# Rule 6's switch, a JSON boolean beside `order_sensitive_paths` (DRE-6871).
RECHECK_KEY = "recheck_new_tests"

# Which rule a `refresh` came from; the wiring picks its receipt by it.
RULE_ORDER_SENSITIVE = "order-sensitive"
RULE_NEW_TESTS = "new-tests"

# The statuses GitHub's compare API gives a file that is new at its path. A
# renumbered migration is a rename, and it lands a new head just as surely.
COUNTING_STATUSES = frozenset({"added", "renamed"})

# The receipt the merge-gate wiring writes when it asks for a refresh. The
# wording is shared with that card; `receipt_marker()` is the one place it is
# spelled.
_RECEIPT_PREFIX = "Merge gate: refreshed onto"


def receipt_marker(tip_sha: str) -> str:
    """Binds a refresh request to the `main` tip it was requested at. The full
    sha, never an abbreviation: this is an idempotency key read with `in`."""
    return f"{_RECEIPT_PREFIX} {tip_sha}"


# Rule 6's receipt. It carries the tip for the reader, but rule 6 reads only
# the prefix: one refresh per pull request, whatever `main` did since.
_NEW_TESTS_RECEIPT_PREFIX = "Merge gate: refreshed for new tests onto"


def new_tests_receipt_marker(tip_sha: str) -> str:
    """The receipt the wiring writes when rule 6 asked for a refresh."""
    return f"{_NEW_TESTS_RECEIPT_PREFIX} {tip_sha}"


# Rule 6's test files: a directory segment, or a basename shape.
_TEST_DIRS = frozenset({"tests", "test", "__tests__"})
_TEST_BASENAME = re.compile(
    r"test_.*\.py|.*_test\.py|.+\.(?:test|spec)\.[^.]+")
# Shorter names match too much ordinary text to mean the file.
MIN_NAME_LENGTH = 6

NO_TEST_RUN_REASON = ("no test run of this head exists, so there is nothing "
                      "a refresh would re-test against — merging as today")


class _Sentinel:
    def __init__(self, name):
        self._name = name

    def __repr__(self):
        return self._name


# The declaration file does not exist: the repo has not opted in.
NO_DECLARATION = _Sentinel("NO_DECLARATION")
# An input that exists and would not parse. Nothing accepts it, so every
# reader in decide() treats it as unreadable — one rule, written once.
UNREADABLE = _Sentinel("UNREADABLE")


@dataclass(frozen=True)
class Decision:
    """One decision, the one-line reason, and on `refresh` the prefix both
    sides added under (empty for rule 6) and the rule that fired."""

    decision: str
    reason: str
    prefix: str = ""
    rule: str = ""


def _one_line(text: str) -> str:
    """Filenames and paths are caller data; a newline in one must not become
    a second `key=value` line in the workflow's output."""
    return " ".join(str(text).split())


def _read_switch(declaration):
    """Rule 6's switch: True or False, or None when the key holds anything but
    a JSON boolean. A declaration that is not an object is rule 1's to
    refuse, and reads here as off."""
    if not isinstance(declaration, dict):
        return False
    switch = declaration.get(RECHECK_KEY, False)
    return switch if isinstance(switch, bool) else None


def _read_declaration(declaration, switch=False):
    """The declared prefixes, or None if the declaration is malformed. With
    the switch on, absent prefixes are an empty list."""
    if not isinstance(declaration, dict):
        return None
    if switch and DECLARATION_KEY not in declaration:
        return []
    paths = declaration.get(DECLARATION_KEY)
    if not isinstance(paths, list):
        return None
    for path in paths:
        if (not isinstance(path, str) or not path.endswith("/")
                or path != _one_line(path) or path.strip("/") == ""):
            return None
    return list(paths)


def _behind_by(compare) -> int:
    """`behind_by`, with missing or unreadable read as 0 (rule 2)."""
    if not isinstance(compare, dict):
        return 0
    behind = compare.get("behind_by")
    if isinstance(behind, bool) or not isinstance(behind, int) or behind < 0:
        return 0
    return behind


def _sha(compare, key) -> str:
    commit = compare.get(key) if isinstance(compare, dict) else None
    sha = commit.get("sha") if isinstance(commit, dict) else None
    return sha if isinstance(sha, str) else ""


def _read_files(payload):
    """`files[]` from a compare payload, or None if it is not there."""
    if not isinstance(payload, dict):
        return None
    files = payload.get("files")
    return files if isinstance(files, list) else None


def _counting(files) -> list:
    """The filenames that are new at their path, in payload order."""
    names = []
    for entry in files or ():
        if not isinstance(entry, dict):
            continue
        name = entry.get("filename")
        if isinstance(name, str) and entry.get("status") in COUNTING_STATUSES:
            names.append(name)
    return names


def _read_receipts(receipts):
    """The receipt BODIES, or None if they cannot be read. `None` in means no
    receipts were given. A list of `gh` comment objects is accepted too."""
    if receipts is None:
        return []
    if not isinstance(receipts, (list, tuple)):
        return None
    bodies = []
    for item in receipts:
        if isinstance(item, str):
            bodies.append(item)
        elif isinstance(item, dict):
            body = item.get("body")
            bodies.append(body if isinstance(body, str) else "")
        else:
            return None
    return bodies


def is_test_file(path: str) -> bool:
    """Rule 6's test file: a `tests`, `test` or `__tests__` directory segment,
    or a test-shaped basename. Every other file is a source file."""
    *dirs, base = path.split("/")
    return bool(_TEST_DIRS.intersection(dirs)) or bool(
        _TEST_BASENAME.fullmatch(base))


def _entries(files):
    """`(filename, entry)` for each readable entry, in payload order."""
    for entry in files:
        if isinstance(entry, dict) and isinstance(entry.get("filename"), str):
            yield entry["filename"], entry


def _new_tests(files) -> list:
    return [(name, entry) for name, entry in _entries(files)
            if entry.get("status") in COUNTING_STATUSES and is_test_file(name)]


def _named_sources(files) -> list:
    """`(source file, name)` for each changed source file whose name is long
    enough to mean it."""
    sources = []
    for name, _entry in _entries(files):
        if is_test_file(name):
            continue
        stem = posixpath.basename(name).split(".", 1)[0]
        if len(stem) >= MIN_NAME_LENGTH:
            sources.append((name, stem))
    return sources


def _first_naming(tests, sources, unread):
    """The first `(test, source)` where the test's text names the source, in
    payload order. A test with no `patch` names nothing and is recorded in
    `unread`."""
    for test, entry in tests:
        patch = entry.get("patch")
        if not isinstance(patch, str):
            if test not in unread:
                unread.append(test)
            continue
        for source, stem in sources:
            if re.search(rf"\b{re.escape(stem)}\b", patch):
                return test, source
    return None


def _new_tests_rule(*, compare, since_run, bodies, behind_by,
                    no_fork) -> Decision:
    """Rule 6, reached only with the switch on, the branch behind, and the
    migrations rule quiet."""
    # 6a — one refresh per pull request, whatever the tip.
    if any(_NEW_TESTS_RECEIPT_PREFIX in body for body in bodies):
        return Decision(PROCEED, f"{no_fork}, and this pull request was "
                                 f"already refreshed once for new tests — "
                                 f"never a second time")

    # 6b — the since-run payload's three shapes.
    theirs = _read_files(since_run)
    if theirs is None:
        if isinstance(since_run, dict) and since_run.get("no_test_run") is True:
            return Decision(PROCEED, NO_TEST_RUN_REASON)
        return Decision(WAIT, f"behind `main` by {behind_by} and the "
                              f"since-run payload (what `main` gained since "
                              f"this head's last test run began) could not be "
                              f"read")

    # 6c to 6e — a new test on one side naming a file the other side changed.
    ours = _read_files(compare) or []
    unread = []
    hit = _first_naming(_new_tests(theirs), _named_sources(ours), unread)
    if hit:
        return Decision(REFRESH, _one_line(
            f"main added {hit[0]}, which names {hit[1]}, changed by this pull "
            f"request"), rule=RULE_NEW_TESTS)
    hit = _first_naming(_new_tests(ours), _named_sources(theirs), unread)
    if hit:
        return Decision(REFRESH, _one_line(
            f"this pull request adds {hit[0]}, which names {hit[1]}, changed "
            f"on main"), rule=RULE_NEW_TESTS)
    reason = f"{no_fork}, and no new test names a file the other side changed"
    if unread:
        reason += (f" — could not read {', '.join(unread)}: the compare "
                   f"entry carries no patch")
    return Decision(PROCEED, _one_line(reason))


def decide(*, compare, base_advance, declaration, receipts=None,
           since_run={}, declaration_path: str = DECLARATION_PATH
           ) -> Decision:
    """Refresh, proceed or wait — over payloads the caller has already read.

    `declaration` is the parsed declaration, `NO_DECLARATION` when the file
    does not exist, or `UNREADABLE` when it would not parse. `base_advance`,
    `receipts` and `since_run` may be `UNREADABLE` too; `since_run` is read
    only by rule 6, and never mutated, so its `{}` default is safe. Nothing
    here reads a file or the network.
    """
    # Rule 1 — the repo's own opt-in.
    if declaration is NO_DECLARATION:
        return Decision(PROCEED, f"no {_one_line(declaration_path)} — this "
                                 f"repo declares no order-sensitive paths")
    switch = _read_switch(declaration)
    if switch is None:
        return Decision(WAIT, f"{_one_line(declaration_path)} sets "
                              f"`{RECHECK_KEY}` to something other than "
                              f"`true` or `false` — failing closed")
    prefixes = _read_declaration(declaration, switch)
    if prefixes is None:
        return Decision(WAIT, f"{_one_line(declaration_path)} does not parse "
                              f"as a list of `/`-terminated "
                              f"`{DECLARATION_KEY}` — failing closed")

    # Rule 2 — currency is not a gate (DRE-2416).
    behind_by = _behind_by(compare)
    if behind_by == 0:
        return Decision(PROCEED, "not behind `main` — nothing a refresh "
                                 "could change")

    # Rule 3 — at most one refresh per `main` commit.
    tip = _sha(compare, "base_commit")
    bodies = _read_receipts(receipts)
    if bodies is None:
        return Decision(WAIT, "the receipts could not be read — cannot tell "
                              "whether a refresh was already requested")
    if tip and any(receipt_marker(tip) in body for body in bodies):
        return Decision(WAIT, f"a refresh was already requested onto `main` "
                              f"{tip[:8]} and has not landed — waiting for "
                              f"its CI, never a second one")
    if not tip and any(_RECEIPT_PREFIX in body for body in bodies):
        return Decision(WAIT, "the compare payload names no `main` tip, and "
                              "a refresh receipt exists — cannot tell whether "
                              "it was for this tip")

    # Rule 4 — a blip is not "safe to merge".
    theirs_files = _read_files(base_advance)
    if theirs_files is None:
        return Decision(WAIT, f"behind `main` by {behind_by} and what `main` "
                              f"gained since the merge base could not be "
                              f"read")

    # Rule 5 — the same declared prefix, added to on both sides.
    ours = _counting(_read_files(compare) or [])
    theirs = _counting(theirs_files)
    for prefix in prefixes:
        ours_here = [name for name in ours if name.startswith(prefix)]
        theirs_here = [name for name in theirs if name.startswith(prefix)]
        if ours_here and theirs_here:
            base = _sha(compare, "merge_base_commit")
            since = f" since merge base {base[:8]}" if base else ""
            return Decision(
                REFRESH,
                _one_line(
                    f"this pull request adds {ours_here[0]} and `main` "
                    f"added {theirs_here[0]} under {prefix}{since} — "
                    f"refresh so CI re-checks the order on a fresh merge "
                    f"ref"),
                prefix,
                RULE_ORDER_SENSITIVE,
            )
    no_fork = (f"behind `main` by {behind_by}, but no declared prefix was "
               f"added to on both sides")
    if not switch:
        return Decision(PROCEED, no_fork)

    # Rule 6 — a new test naming a file the other side changed (DRE-6871).
    return _new_tests_rule(compare=compare,
                           since_run=since_run,
                           bodies=bodies, behind_by=behind_by,
                           no_fork=no_fork)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _or_unreadable(path):
    try:
        return _load(path)
    except (OSError, ValueError):
        return UNREADABLE


def _declaration(path):
    try:
        return _load(path)
    except FileNotFoundError:
        return NO_DECLARATION
    except (OSError, ValueError):
        return UNREADABLE


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Decide whether the merge gate refreshes a branch before "
                    "merging, because both it and `main` added a file under "
                    "an order-sensitive path, or because one side added a "
                    "test naming a file the other changed"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--compare-file", required=True,
                   help="JSON from `gh api repos/{repo}/compare/{base}...{head}`")
    d.add_argument("--base-advance-file", required=True,
                   help="JSON from `gh api repos/{repo}/compare/"
                        "{merge_base}...{base}` (`{}` when the read blipped)")
    d.add_argument("--declaration-file", required=True,
                   help=f"the product checkout's {DECLARATION_PATH}")
    d.add_argument("--receipts-file",
                   help="JSON list of the comment bodies already on the pull "
                        "request")
    d.add_argument("--since-run-file",
                   help="JSON from `gh api repos/{repo}/compare/{main's tip "
                        "when the anchor run was created}...{base}`, or "
                        "`{\"no_test_run\": true}`; absent reads as `{}`")
    args = parser.parse_args(argv)

    try:
        compare = _load(args.compare_file)
    except (OSError, ValueError) as exc:
        compare, problem = None, str(exc)
    else:
        problem = f"a JSON {type(compare).__name__}, not an object"
    if not isinstance(compare, dict):
        # Loud: the head is the subject, and a silent answer here would steer
        # the gate on a guess.
        print(f"order_sensitive_refresh: cannot read the compare payload: "
              f"{_one_line(problem)}", file=sys.stderr)
        return 2

    declaration = _declaration(args.declaration_file)
    decision = decide(
        compare=compare,
        base_advance=_or_unreadable(args.base_advance_file),
        declaration=declaration,
        receipts=(_or_unreadable(args.receipts_file)
                  if args.receipts_file else None),
        since_run=(_or_unreadable(args.since_run_file)
                   if args.since_run_file else {}),
        declaration_path=args.declaration_file,
    )
    print(f"decision={decision.decision}")
    print(f"reason={decision.reason}")
    if decision.decision == REFRESH:
        print(f"prefix={decision.prefix}")
        # With the switch off the output stays the three lines it always
        # was: a refresh with no `rule=` line can only be order-sensitive.
        if _read_switch(declaration):
            print(f"rule={decision.rule}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
