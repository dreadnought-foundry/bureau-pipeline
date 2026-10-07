#!/usr/bin/env python3
"""A pull request whose head GitHub never moved to its branch (DRE-6217).

GitHub can record a push and never synchronize the pull request: the branch
ref moves, `pulls/<n>` keeps the old `head.sha`, and every check, the critic,
the fix loop and the merge gate keep reading a commit the branch has already
left. bureau-pipeline #780 sat six hours that way on 2026-10-07 —
`git/ref/heads/agent/DRE-6124-…` read `d1754c31` while the pull request read
`79764952` — until the operator closed and reopened it, and GitHub moved the
head within a minute. `update-branch`, over GraphQL and over REST, refused
with `head sha didn't match the current head ref`; close and reopen is the
remedy that worked.

`reconcile.resync_desynced_heads` makes the reads and the writes. This module
owns the words: the tag, the two markers the sweep reads back, the log line
and the two notices. One definition each, because the notice IS the
idempotency key — a second copy of the wording is how a sweep comes to miss
its own receipt and close a pull request twice.

Two markers, and neither contains the other:

* `head-desync @<branch sha>` — the sweep closed and reopened this pull
  request once for that branch commit. Never again for the same commit.
* `head-desync-unresolved @<branch sha>` — the pull request is still not on
  that commit and the sweep will not act on it; a person has been told, once.
"""
from __future__ import annotations

#: The tag every line and notice carries.
TAG = "head-desync"
#: The tag of the one notice that hands a still-desynced pull request to a
#: person. Not a substring problem: `head-desync @` never occurs inside
#: `head-desync-unresolved @`, so each marker matches only its own notice.
UNRESOLVED_TAG = f"{TAG}-unresolved"

#: How old the branch commit must be before the sweep reads a mismatch as a
#: desync rather than GitHub's normal synchronize lag — the same 15 minutes
#: `retrigger_dead_heads` gives a fresh push to spin up its checks.
MIN_AGE_MINUTES = 15


def marker(branch_sha: str) -> str:
    """The resync receipt's key: one close-and-reopen per branch commit."""
    return f"{TAG} @{branch_sha}"


def unresolved_marker(branch_sha: str) -> str:
    """The hand-to-a-person notice's key: said once per branch commit."""
    return f"{UNRESOLVED_TAG} @{branch_sha}"


def resynced(receipts: list[str], branch_sha: str) -> bool:
    """Whether the sweep already closed and reopened for this branch commit."""
    key = marker(branch_sha)
    return any(key in body for body in receipts)


def told(receipts: list[str], branch_sha: str) -> bool:
    """Whether a person was already told this branch commit is stuck."""
    key = unresolved_marker(branch_sha)
    return any(key in body for body in receipts)


def log_line(number: int, pr_head: str, branch_sha: str) -> str:
    """The one line the sweep prints for a desynced pull request."""
    return f"{TAG}: PR #{number} {pr_head[:8]} → {branch_sha[:8]}"


def resync_notice(number: int, pr_head: str, branch_sha: str,
                  age_minutes: float) -> str:
    """The receipt posted after the close and reopen."""
    return (
        f"🔄 `{marker(branch_sha)}` — the reconcile sweep closed and reopened "
        f"this pull request once (DRE-6217).\n\n"
        f"GitHub kept this pull request on `{pr_head}` while its branch has "
        f"been on `{branch_sha}` for {int(age_minutes)} minutes. Until the "
        f"pull request follows its branch, every check, the review and the "
        f"merge gate read a commit the branch has already left. Closing and "
        f"reopening is what makes GitHub move it.\n\n"
        f"Nothing is needed from you. CI and the review start on the new "
        f"commit within a minute or two. The sweep does this once per branch "
        f"commit; if the pull request is still behind its branch on the next "
        f"sweep, it says so here and stops."
    )


def unresolved_notice(number: int, pr_head: str, branch_sha: str,
                      why: str) -> str:
    """The one notice that hands a still-desynced pull request to a person.

    `why` is `after` (the one resync did not move the head) or `diverged`
    (the branch is not a descendant of the head, so the sweep never closed
    it). Must never contain `marker(branch_sha)`: that would read as a resync
    that never happened.
    """
    if why == "diverged":
        what = (
            f"The branch is not a descendant of `{pr_head[:8]}` — it was "
            f"rewritten, not extended — and GitHub can refuse to reopen a pull "
            f"request whose head was rewritten, so the sweep did not close "
            f"this one."
        )
    else:
        what = (
            "The sweep already closed and reopened it once for this commit, "
            "and GitHub still has not moved it. It will not try again."
        )
    return (
        f"⚠️ `{unresolved_marker(branch_sha)}` — this pull request needs a "
        f"person (DRE-6217).\n\n"
        f"GitHub has this pull request on `{pr_head}`, but its branch is on "
        f"`{branch_sha}`. {what}\n\n"
        f"Every check, the review and the merge gate are still reading the "
        f"older commit. Closing and reopening it by hand, or pushing a new "
        f"commit to the branch, is the next step.\n\n"
        f"_Said once per branch commit._"
    )
