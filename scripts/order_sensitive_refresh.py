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

This module is the decision and nothing else: pure functions over GitHub
payloads, no I/O in `decide()`, and a CLI for the workflow, in the shape of
`stale_merge_ref.py` (DRE-3138). The merge-gate wiring and the declaration a
product repo writes are their own cards.

The declaration is `.github/bureau/merge-recheck.json` in the product checkout:

    {"$schema_note": "…", "order_sensitive_paths": ["console/backend/alembic/versions/"]}

each entry a repo-relative directory prefix ending in `/`, matched with
`filename.startswith(prefix)`.

The rule, in order:

  1. No declaration file → `proceed` (the repo opted out). A declaration that
     does not parse, or whose `order_sensitive_paths` is not a list of
     `/`-terminated strings → `wait`, naming the file (fail closed).
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
     `proceed`.

Known limit, stated rather than hidden: GitHub's compare API lists at most 300
files per side. A `main` that gained more than that since the merge base can
hide a same-prefix addition; the repo's own head gate on `main` is still the
backstop there.

CLI:

    python3 order_sensitive_refresh.py decide --compare-file <compare json> \\
        --base-advance-file <compare json> --declaration-file <path> \\
        [--receipts-file <json list of comment bodies>]

stdout line 1 is `decision=refresh|proceed|wait`, line 2 `reason=<one line>`,
and on `refresh` line 3 `prefix=<the matched prefix>`. Exit 0 on every
decision; exit 2 ONLY when `--compare-file` cannot be read as an object — the
head is the subject, and a silent answer there would steer the gate on a
guess.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

REFRESH = "refresh"
PROCEED = "proceed"
WAIT = "wait"

DECLARATION_PATH = ".github/bureau/merge-recheck.json"
DECLARATION_KEY = "order_sensitive_paths"

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
    sides added under."""

    decision: str
    reason: str
    prefix: str = ""


def _one_line(text: str) -> str:
    """Filenames and paths are caller data; a newline in one must not become
    a second `key=value` line in the workflow's output."""
    return " ".join(str(text).split())


def _read_declaration(declaration):
    """The declared prefixes, or None if the declaration is malformed."""
    if not isinstance(declaration, dict):
        return None
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


def decide(*, compare, base_advance, declaration, receipts=None,
           declaration_path: str = DECLARATION_PATH) -> Decision:
    """Refresh, proceed or wait — over payloads the caller has already read.

    `declaration` is the parsed declaration, `NO_DECLARATION` when the file
    does not exist, or `UNREADABLE` when it would not parse. `base_advance`
    and `receipts` may be `UNREADABLE` too. Nothing here reads a file or the
    network.
    """
    # Rule 1 — the repo's own opt-in.
    if declaration is NO_DECLARATION:
        return Decision(PROCEED, f"no {_one_line(declaration_path)} — this "
                                 f"repo declares no order-sensitive paths")
    prefixes = _read_declaration(declaration)
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
            )
    return Decision(PROCEED, f"behind `main` by {behind_by}, but no declared "
                             f"prefix was added to on both sides")


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
                    "an order-sensitive path"
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

    decision = decide(
        compare=compare,
        base_advance=_or_unreadable(args.base_advance_file),
        declaration=_declaration(args.declaration_file),
        receipts=(_or_unreadable(args.receipts_file)
                  if args.receipts_file else None),
        declaration_path=args.declaration_file,
    )
    print(f"decision={decision.decision}")
    print(f"reason={decision.reason}")
    if decision.decision == REFRESH:
        print(f"prefix={decision.prefix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
