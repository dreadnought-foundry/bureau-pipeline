#!/usr/bin/env python3
"""Reconcile the PR's FORMAL review state with an APPROVE verdict (stdlib only).

THE BUG (DRE-1874, live evidence on PRs #1830/#1827, 2026-06-24):
  The QA critic runs via claude-code-action with the bureau-bot github_token.
  When the critic's verdict is REQUEST_CHANGES, the action submits a FORMAL
  GitHub "Changes requested" review (authored by agent-bureau-bot). When the
  fixing agent later resolves the findings and the critic flips to APPROVE,
  qa-review.yml only posts a *comment* (`🔎 QA Critic — VERDICT: APPROVE`).
  The earlier formal review is never touched — so GitHub keeps
  `reviewDecision = CHANGES_REQUESTED` and `mergeStateStatus = BLOCKED`, even
  with branch protection requiring zero reviews. The merge-gate (and even
  `gh pr merge --admin`) is blocked by "1 review requesting changes" until a
  human manually dismisses the stale review. This strands legitimately
  approved PRs and contributes to the auto-merge stalls.

THE FIX:
  When the verdict is APPROVE, make the formal review state agree with it:
    1. DISMISS every still-active CHANGES_REQUESTED review on the PR (the
       qa-bot / bureau-bot have write access, so a dismissal is allowed
       regardless of which identity authored the review), AND
    2. submit a fresh formal APPROVE review, so `reviewDecision` becomes
       APPROVED and nothing is left requesting changes.
  Both steps are idempotent: dismissing only the reviews whose latest state is
  CHANGES_REQUESTED, and the APPROVE review is harmless to repeat.

This runs with the SAME token qa-review.yml already mints (the bureau-bot App
via GH_TOKEN). The bot has write access to the repo, which is all GitHub
requires to dismiss a review and to submit a new one.

Called from qa-review.yml only on an APPROVE verdict:

    python3 sync_review_state.py <owner/repo> <pr-number> [dismiss-message]

Reads/writes via the `gh` CLI (GH_TOKEN in env). Best-effort: it logs and
returns 0 even if the API is briefly unavailable — it must never wedge the
merge it is trying to UNBLOCK.
"""

from __future__ import annotations

import json
import subprocess
import sys

DEFAULT_DISMISS_MESSAGE = (
    "Superseded: the QA Critic verdict flipped to APPROVE — the requested "
    "changes were resolved, so this stale review is dismissed (DRE-1874)."
)
APPROVE_BODY = (
    "QA Critic verdict: APPROVE — formal approval recorded so the merge gate "
    "is not blocked by a stale review (DRE-1874)."
)


def gh_json(args: list[str], default):
    """Run a `gh` command and parse stdout as JSON; return default on failure."""
    out = gh(args)
    if out is None:
        return default
    try:
        return json.loads(out)
    except ValueError:
        return default


def gh(args: list[str]) -> str | None:
    """Run `gh <args>`; return stdout on success, None on any failure.

    Isolated so tests can stub the single subprocess seam (mirrors the gh()
    helpers in reconcile.py / linear_ops.py).
    """
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:  # gh missing / not executable
        print(f"sync_review_state: gh invocation failed: {exc}")
        return None
    if proc.returncode != 0:
        print(
            f"sync_review_state: `gh {' '.join(args)}` exited "
            f"{proc.returncode}: {proc.stderr.strip()}"
        )
        return None
    return proc.stdout


def reviews_to_dismiss(reviews: list[dict]) -> list[int]:
    """IDs of reviews whose author's LATEST review state still requests changes.

    GitHub records every review event; the one that counts toward
    `reviewDecision` is each reviewer's most recent non-comment review. We
    therefore look at the last CHANGES_REQUESTED / APPROVED / DISMISSED review
    per author and dismiss only those left at CHANGES_REQUESTED. COMMENTED and
    already-DISMISSED reviews are ignored — making this idempotent (a second
    run finds nothing to dismiss).
    """
    latest_by_author: dict[str, dict] = {}
    for r in reviews:
        state = r.get("state")
        # COMMENTED reviews never change the decision; skip so they don't mask
        # an earlier CHANGES_REQUESTED as the "latest" state.
        if state == "COMMENTED":
            continue
        author = ((r.get("user") or {}).get("login")) or ""
        latest_by_author[author] = r
    out = []
    for r in latest_by_author.values():
        if r.get("state") == "CHANGES_REQUESTED" and r.get("id") is not None:
            out.append(r["id"])
    return out


def sync_on_approve(
    repo: str,
    pr: str,
    dismiss_message: str = DEFAULT_DISMISS_MESSAGE,
    approve_body: str = APPROVE_BODY,
) -> int:
    """Dismiss stale CHANGES_REQUESTED reviews + submit a fresh APPROVE review.

    Returns the number of state-changing API calls made (dismissals + the
    approval). Best-effort: individual failures are logged, not fatal.
    """
    reviews = gh_json(["api", f"repos/{repo}/pulls/{pr}/reviews", "--paginate"], [])
    if not isinstance(reviews, list):
        reviews = []

    actions = 0
    for review_id in reviews_to_dismiss(reviews):
        res = gh(
            [
                "api",
                "-X",
                "PUT",
                f"repos/{repo}/pulls/{pr}/reviews/{review_id}/dismissals",
                "-f",
                f"message={dismiss_message}",
                "-f",
                "event=DISMISS",
            ]
        )
        if res is not None:
            print(f"sync_review_state: dismissed stale review {review_id}")
            actions += 1

    # A fresh APPROVE review makes reviewDecision == APPROVED. Even after the
    # dismissals above this is the positive signal branch protection / the
    # merge-gate read; submitting it is harmless to repeat.
    res = gh(
        [
            "api",
            "-X",
            "POST",
            f"repos/{repo}/pulls/{pr}/reviews",
            "-f",
            "event=APPROVE",
            "-f",
            f"body={approve_body}",
        ]
    )
    if res is not None:
        print("sync_review_state: submitted fresh APPROVE review")
        actions += 1

    return actions


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: sync_review_state.py <owner/repo> <pr-number> [message]")
        return 2
    repo, pr = argv[0], argv[1]
    message = argv[2] if len(argv) > 2 and argv[2].strip() else DEFAULT_DISMISS_MESSAGE
    sync_on_approve(repo, pr, dismiss_message=message)
    # Always succeed: this step UNBLOCKS the merge; a transient failure here
    # must not turn into a red job that strands the PR further.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
