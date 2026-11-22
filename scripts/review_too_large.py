#!/usr/bin/env python3
"""A pull request the critic refused as too large is its own outcome (DRE-9328).

THE INCIDENT. DeltaSolv PR #10 (DRE-9278) was 80 files and 84,730 changed
lines, 84,526 of them one design file. On 2026-11-12 the critic declined it
for size, as `pr_size_strategy` tells it to past 200 files / 20,000 changed
lines: no review ran, the head-bound review check went red, and the critic
said so on the pull request. A red review check with no verdict is exactly
what the sweep's crashed-review pass looks for, so it re-ran the review once
(the size did not change, so the re-run refused again), and from then on
every pass said the reviewer was down and a human had to fix it, raised the
reviewer-down alert, and fired the medic. DeltaSolv's sweep failed 399 runs in
a row, from 2026-11-16 to 2026-11-22, until a person merged the pull request.

THE RULE. A size refusal is not a crash. The sweep names it `review-too-large`
with the pull request, its file and line counts and the limit; it re-runs
nothing, because a re-run cannot change the size; it raises no reviewer-down
alert; and it asks the card ONCE per head, in the Green Light format, what
moves it: a split into smaller pull requests, or a person's review. It never
puts the refusal on a ledger that turns the sweep red — the question in Green
Light is the escalation.

HOW IT IS RECOGNIZED. Two records, both required, read by `size_at_head`:

  * the head-bound review check (`publish_review_check.CHECK_NAME`), red, with
    the title `publish_review_check.TOO_LARGE_TITLE` bound to THIS head. The
    check names the commit, which the critic's comment does not, so a refusal
    on an earlier head never excuses a crash on a later one;
  * the critic's newest comment — the qa-bot's, the caller's authorship read
    (DRE-1998) — opening with `pr_size_strategy.OVERSIZE_OPENER`. A check is
    matched by name alone, so a check anybody named `QA critic review` cannot
    on its own turn a crash into a refusal.

Either record missing, and the head is read as it always was. This module
reads nothing and writes nothing: the sweep hands it the check runs and the
critic's comment bodies, and posts what it composes.
"""
from __future__ import annotations

import re

import console_escalation
import pr_size_strategy
import publish_review_check

#: The outcome's name, in the sweep's log and in the card's once-per-head key.
#: Spelled `KEY`, not `_TAG`: pipeline_act reads every `*_TAG` literal as a
#: declared act, and this question ships as an `unconverted` row of
#: config/pipeline-acts.json.
KEY = "review-too-large"

#: The reviewer's working limit, as the critic's notice states it.
LIMIT = (f"{pr_size_strategy.OVERSIZED_FILES:,} files / "
         f"{pr_size_strategy.OVERSIZED_LINES:,} changed lines")

#: What moves it, in the words the log line and the question share.
WHAT_MOVES_IT = "a split into smaller pull requests, or a person's review"

#: The measured size inside the check's summary or the critic's notice:
#: "too large to review (80 files, 84,730 changed lines)".
_SIZE = re.compile(r"too large to review \(([^)]*)\)")


def _size_in(text: str) -> str:
    found = _SIZE.search(text or "")
    return found.group(1).strip() if found else ""


def opens_refusal(body: str) -> bool:
    """Does this critic comment open the size refusal? The cheap half of the
    read: the sweep asks it of the comment it already holds before it reads
    the head's check runs for the other half."""
    return (body or "").startswith(pr_size_strategy.OVERSIZE_OPENER)


def size_at_head(check_runs: list[dict], critic_bodies: list[str],
                 sha: str) -> str | None:
    """The measured size when the critic refused THIS head for size, else None.

    `check_runs` are the head's check runs as GitHub serves them, `output`
    included; `critic_bodies` the critic's comment bodies, oldest to newest.
    The size is read off the check's summary, or the critic's notice when the
    summary carries none; "" when neither does, and the head still counts as
    refused."""
    newest = critic_bodies[-1] if critic_bodies else ""
    if not sha or not opens_refusal(newest):
        return None
    title = f"{publish_review_check.TOO_LARGE_TITLE} @{sha[:8]}"
    for run in check_runs or []:
        if not isinstance(run, dict):
            continue
        output = run.get("output") or {}
        if (run.get("name") == publish_review_check.CHECK_NAME
                and run.get("status") == "completed"
                and run.get("conclusion") == "failure"
                and run.get("head_sha", sha) == sha
                and (output.get("title") or "") == title):
            return _size_in(output.get("summary") or "") or _size_in(newest)
    return None


def marker(number: int, sha: str) -> str:
    """The once-per-head key on the card. `" @"` ends the number, so #1's
    marker never substring-matches #10's."""
    return f"{KEY} PR #{number} @{sha}:"


def _measured(size: str) -> str:
    return size or "its measured size"


def log_line(number: int, sha: str, size: str) -> str:
    """The sweep's one line for a refused head, on every pass. Stdout, never
    `ERROR:`: nothing failed and nobody is down."""
    return (
        f"{KEY}: PR #{number} head {sha[:8]} — the critic declined to review "
        f"it for size ({_measured(size)}; the limit is {LIMIT}). Not a crashed "
        "review: nothing re-dispatched, no reviewer-down alert. What moves it: "
        f"{WHAT_MOVES_IT}"
    )


def escalation(*, number: int, sha: str, size: str) -> console_escalation.Escalation:
    """The Green Light question, in the one format (DRE-3908)."""
    return console_escalation.Escalation(
        finding=(
            f"The reviewer declined pull request #{number} at head {sha[:7]} "
            f"because it is too large to review: {_measured(size)}, past its "
            f"limit of {LIMIT}. No review ran, so there is no verdict and this "
            "is not a code rejection, and the reviewer is not down. Running "
            "the review again cannot change the size, so the sweep runs "
            "nothing more on this head."
        ),
        question=(
            "Is the change split into smaller pull requests, each reviewed on "
            "its own, or does a person review this pull request and decide "
            "the merge?"
        ),
        recommendation="Split it into smaller pull requests",
        why=("every part then gets a full review, and the merge stays held "
             "until a reviewer has read the change"),
    )


def question(*, card: str, number: int, sha: str, size: str) -> str:
    """The card comment: the key, the three lines, and the way back."""
    lines = console_escalation.render(escalation(number=number, sha=sha, size=size))
    way_back = (
        f"{card} waits in Green Light, and the sweep re-runs no review on this "
        "head. A push that brings the pull request under the limit is reviewed "
        "as usual; a split into new pull requests, or a person's review and "
        "merge, ends this one."
    )
    return "\n\n".join((f"🚨 {marker(number, sha)}", lines, way_back))
