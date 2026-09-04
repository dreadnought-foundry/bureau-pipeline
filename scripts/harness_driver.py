#!/usr/bin/env python3
"""Which `scripts/harness/` drives this harness run? (DRE-3101)

The harness drives a SHARED sandbox, and since DRE-3075 two runs are in it
at once: main's proving run — the one `stable` advances on — and whichever
pull request is being gated. That is only safe because the driver's sweep
is scoped to the run's own namespace, and the scoping lives in
`scripts/harness/`, which a `pull_request` run reads FROM THE PR'S OWN HEAD
(`harness.yml`: `ref: github.event.pull_request.head.sha`).

So every branch cut before the namespacing merged still ran the old,
un-namespaced sweep: it closed every harness PR and deleted every harness
branch in the sandbox, main's included. On 2026-09-04 that ran for a day —
from 10:00 PT no main proving run finished (cancelled, cancelled,
cancelled, hung for over an hour, cancelled, pending) while pull-request
runs kept passing, because main has one slot and the pull requests have
many. `stable` sat at `a7bfa52` from 08:23 PT through ten merges. Seen
directly at 10:39 PT: main's probe PR #939 in the sandbox was closed by the
bot two minutes after DRE-3098's run started its scenarios.

The fix is the one the reusable workflows already use for their own code —
re-checkout at a known ref rather than trusting whatever the caller's head
happens to carry. A pull request drives the sandbox with **main's**
`scripts/harness/`, so a branch's age can no longer decide what the
cleanup does.

The one exception is the case where the PR's copy is the point: when the
pull request itself changes `scripts/harness/`, the harness is what is
under test and its own copy has to run — otherwise the gate proves main's
driver twice and the change lands unproven. That case says so in the
receipt, so a reader of the `integration-harness` stamp never has to guess
which driver ran.

This module is the decision only — no network, no git; the workflow
gathers GitHub's records and acts on the verdict (the `promote_channel.py`
/ `release_gate.py` shape).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Iterable, NamedTuple

#: The driver directory, with its trailing slash — the guard is the
#: DIRECTORY and never the word. `scripts/harness_driver.py` (this file) and
#: `tests/test_harness_*.py` both open with the same letters, and a prefix
#: match on `scripts/harness` alone would route every one of them down the
#: PR's-own-copy path, which is the bug this module exists to close.
DRIVER_DIR = "scripts/harness/"

#: The branch the pull-request checkout is replaced from. Deliberately the
#: branch NAME, not a sha: what is wanted here is the newest cleanup rules,
#: which is the opposite of the stamp's requirement (harness.yml pins the
#: TESTED sha to an immutable commit, DRE-2551).
TRUNK = "main"

#: `main` — a pull request that leaves the harness alone drives the sandbox
#: with the trunk's cleanup and probe rules.
SOURCE_MAIN = "main"
#: `pr` — the pull request changes `scripts/harness/`, so the harness is
#: what is under test and its own copy runs.
SOURCE_PR = "pr"
#: `head` — a push to main or a hand dispatch: the ref that was checked out
#: IS the ref under test, and re-checking main over it would either be a
#: no-op or would silently test something other than `pipeline_ref`.
SOURCE_HEAD = "head"

#: What the `integration-harness` status description says about each. Short
#: on purpose: GitHub clamps a status description at 140 characters and the
#: run's own state goes in front of this (`sandbox_health.RECEIPT_LIMIT`).
RECEIPTS = {
    SOURCE_MAIN: "driver: scripts/harness/ from main",
    SOURCE_PR: "driver: this PR's own scripts/harness/ — the harness is under test",
    SOURCE_HEAD: "driver: the ref under test",
}

#: The only event whose checkout is somebody else's branch.
PULL_REQUEST = "pull_request"


class Choice(NamedTuple):
    """`(source, reason, receipt)` — a token for the workflow, prose for the
    run log, a phrase for the stamp."""

    source: str
    reason: str
    receipt: str


def touches_driver(paths: Iterable[str]) -> bool:
    """Does this change list edit the harness driver itself?"""
    return any((p or "").strip().startswith(DRIVER_DIR) for p in paths)


def choose(event_name: str, changed_paths: Iterable[str]) -> Choice:
    """Which `scripts/harness/` this run drives the sandbox with.

    `changed_paths` is the pull request's own file list and is only read for
    a `pull_request` event. An EMPTY list — including the empty list a
    failed API call leaves behind — reads as "no harness change", which
    routes to main. That is the safe direction: the worst it can cost is a
    harness edit proved against main's driver, whereas the other default
    lets a pre-namespacing sweep loose on a sandbox main is live in.
    """
    if event_name != PULL_REQUEST:
        return Choice(
            SOURCE_HEAD,
            f"{event_name!r} run: the checked-out ref is the ref under test.",
            RECEIPTS[SOURCE_HEAD],
        )
    if touches_driver(changed_paths):
        return Choice(
            SOURCE_PR,
            f"this pull request changes {DRIVER_DIR} — the harness is what is "
            f"under test, so its own copy drives the sandbox.",
            RECEIPTS[SOURCE_PR],
        )
    return Choice(
        SOURCE_MAIN,
        f"this pull request leaves {DRIVER_DIR} alone, so the sandbox cleanup "
        f"and probe rules come from {TRUNK} — a branch's age must not decide "
        f"what the sweep deletes (DRE-3101).",
        RECEIPTS[SOURCE_MAIN],
    )


def main(argv: list[str] | None = None, stdin=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", required=True)
    args = parser.parse_args(argv)

    changed = [line.strip() for line in (stdin or sys.stdin).read().splitlines()]
    choice = choose(args.event_name, [c for c in changed if c])

    print(f"{choice.source}: {choice.reason}")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            # One line per key — `$GITHUB_OUTPUT` is a key=value file, and
            # both values are composed here rather than taken from input.
            fh.write(f"source={choice.source}\n")
            fh.write(f"receipt={choice.receipt}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(f"🧭 {choice.receipt}\n\n{choice.reason}\n\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
