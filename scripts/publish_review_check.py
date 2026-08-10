#!/usr/bin/env python3
"""Publish the critic's verdict as a check run BOUND TO THE REVIEWED HEAD.

DRE-2291. qa-review runs on two triggers, and only one of them leaves a
record on the pull request.

A `pull_request` run is attributed to the PR's head, so GitHub creates its
`call / review` check run on the head commit. A `workflow_dispatch` run —
the documented manual re-run, and the remedy the reconcile sweep itself
dispatches (DRE-2047 dependabot reviews, DRE-2282 crashed-review recovery)
— is attributed to the DEFAULT BRANCH. Its check run therefore lands on
main's tip and NOTHING lands on the PR. Verified live on 2026-08-07:
agent-bureau PR #2007 (head 14934d98) had its only branch-attributed review
run CANCELLED — killed by `concurrency: cancel-in-progress` when a manual
dispatch was issued over it — and three subsequent workflow_dispatch runs
wrote their `call / review` onto commit 87eb0999 (main). The PR head kept
the dead check while a genuine `VERDICT: APPROVE @14934d98…` sat in the
comments. A manual re-review looks successful and changes nothing anyone
reads off the commit.

Everything that judges a head is misled by that: the console's merge view,
a human deciding whether to merge by hand, `gh pr checks`,
mergeStateStatus, and reconcile.recover_crashed_reviews, which reads ONLY
the checks at the head — so a dispatched review's crash is invisible to the
very backstop meant to recover it.

This script closes the gap. It writes ONE check run per head, named so the
existing readers already classify it as a review check, created if absent
and UPDATED IN PLACE if present. A workflow_dispatch re-review therefore
overwrites exactly what a pull_request run would have written, and no head
can accumulate two contradictory review records.

The conclusion reports the VERDICT, not the job's liveness. Today's
`call / review` goes GREEN on a REQUEST_CHANGES verdict, because all it
means is "the critic job finished" — the false green that got two
REQUEST_CHANGES PRs hand-merged and shipped a data-integrity regression
(DRE-1825 → DRE-1895). Here: APPROVE → success, REQUEST_CHANGES → failure,
crashed critic → failure with a summary that says plainly it is not a code
rejection (DRE-1916).

This check is NOT a merge credential and must never become one. The merge
gate's review condition stays the qa-bot-authored `VERDICT: APPROVE` comment
bound to the current head (merge_gate.py condition 2, DRE-1990). A check
the pipeline writes about itself can only ever BLOCK a merge here, never
grant one — merge_gate.py excludes review-origin checks by verified suite
origin (DRE-1994) and knows nothing about this name.

TOKEN: the qa-bot App carries checks:READ only (`GET /apps/
agent-bureau-qa-bot`, verified 2026-08-07), so the critic's own token
cannot create a check run. The caller passes a token minted from the
dispatch App, which carries checks:write and whose secrets already reach
every consuming repo. One API write per review does not reopen the DRE-1921
quota split, which was about the critic's whole review workload sharing the
relay's bucket.

Called from qa-review.yml after the verdict comment is posted:

    python3 publish_review_check.py \
      --repo owner/name --sha <reviewed head> \
      --verdict-file /tmp/qa-verdict.md --real true|false \
      --run-url <this run> --event <github.event_name>

Exit 0 when the head carries the check. Non-zero when it could not be
written after retries — the review run then goes red rather than leaving
the head unmarked a second time.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 — fixed-arg calls to the gh CLI only
import sys
import time
import urllib.parse

#: The head-bound review record. MUST end with "review": both
#: reconcile._review_checks_at_head and reconcile.fix_approved_but_red
#: classify a check as review-owned by that suffix, so this name joins the
#: class they already understand — without it, approved-but-red would
#: dispatch a fix agent over the critic's own red check.
CHECK_NAME = "QA critic review"

#: Load-bearing like the verdict comment: retry through a transient GitHub
#: blip rather than leaving the head unmarked, then fail loudly.
ATTEMPTS = 3


def _verdict_word(verdict: str) -> str:
    """APPROVE / REQUEST_CHANGES from the verdict body's VERDICT: line, or
    "" when there is no readable one. Anchored to the line, matching the
    critic's contract (`check_critic_result.py`) and merge_gate's parsing."""
    for line in verdict.splitlines():
        stripped = line.strip()
        if not stripped.startswith("VERDICT:"):
            continue
        rest = stripped[len("VERDICT:"):].strip().upper()
        if rest.startswith("APPROVE"):
            return "APPROVE"
        if rest.startswith("REQUEST_CHANGES"):
            return "REQUEST_CHANGES"
        return ""
    return ""


def decide(real: bool, verdict: str) -> tuple[str, str, str]:
    """(conclusion, title, summary) for the head-bound check.

    `real` is check_critic_result.py's answer to "did a genuine review
    run". A crash, or a verdict body with no readable VERDICT: line, fails
    CLOSED — the check goes red and says why in plain English rather than
    guessing at an outcome nobody produced.
    """
    if not real:
        return (
            "failure",
            "Review crashed — no verdict",
            "The adversarial reviewer could not run (an infrastructure "
            "failure — it produced no findings). This is NOT a code "
            "rejection. Merge is held until a critic actually reviews this "
            "commit.",
        )
    word = _verdict_word(verdict)
    if word == "APPROVE":
        return (
            "success",
            "VERDICT: APPROVE",
            "The adversarial reviewer approved this commit.",
        )
    if word == "REQUEST_CHANGES":
        return (
            "failure",
            "VERDICT: REQUEST_CHANGES",
            "The adversarial reviewer is asking for changes to this commit. "
            "Read its verdict comment on the pull request for the findings.",
        )
    return (
        "failure",
        "Review produced no readable verdict — treated as a crash",
        "A review ran but wrote no `VERDICT:` line, so its outcome cannot "
        "be read. This is NOT a code rejection. Treated as a crash and the "
        "merge is held, fail-closed.",
    )


def _gh(args: list[str], payload: str | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
        input=payload,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _existing_check_id(repo: str, sha: str) -> int | None:
    """The id of this head's existing head-bound check, or None.

    Scoped to THIS name at THIS head — scanning every check on the commit
    would invite a collision with a PR-authored job. An unreadable read
    returns None (create, the safe direction): DRE-2034 discipline says
    never act on fabricated data, and inventing an id to PATCH is the one
    action that could destroy a record.
    """
    name = urllib.parse.quote(CHECK_NAME)
    rc, out, _ = _gh([
        "api",
        f"repos/{repo}/commits/{sha}/check-runs?check_name={name}&filter=latest",
        "--jq", "[.check_runs[] | {id}]",
    ])
    if rc != 0 or not out.strip():
        return None
    try:
        runs = json.loads(out)
    except ValueError:
        return None
    return runs[0]["id"] if runs else None


def detail_body(*, sha: str, summary: str, run_url: str, event: str,
                carried_from: str = "") -> str:
    """The check's summary body.

    `carried_from` (DRE-2340) names the commit a CARRIED verdict was
    actually earned on. The critic did not re-run on this head: the head
    moved only because the gate merged the base branch in, and the PR's own
    contribution is byte-identical to the diff that verdict was earned
    against. The check still has to say something honest about this
    commit — a head showing NO review status reads as "unreviewed" to every
    consumer (`gh pr checks`, the console, a human), which is exactly what
    it is not."""
    if carried_from:
        return (
            f"{summary}\n\n"
            f"Head commit: `{sha}`\n"
            f"Reviewed commit: `{carried_from}`\n"
            f"Triggering event: `{event}`\n"
            f"Review run: {run_url}\n\n"
            "The reviewer did NOT re-read this commit. Its verdict was "
            "carried from the reviewed commit above because this pull "
            "request's own changes are unchanged — the head moved only by "
            "merging the base branch in, which touched nothing this pull "
            "request changes (DRE-2340). Any change to the pull request's "
            "own diff kills that verdict and forces a fresh review."
        )
    return (
        f"{summary}\n\n"
        f"Reviewed commit: `{sha}`\n"
        f"Triggering event: `{event}`\n"
        f"Review run: {run_url}\n\n"
        "This check is bound to the commit the critic actually read "
        "(DRE-2291), so a manual re-review updates it exactly as an "
        "automatic one does. A run cancelled by a newer review of the same "
        "pull request is superseded here, not left speaking for the head."
    )


def publish(*, repo: str, sha: str, real: bool, verdict: str,
            run_url: str, event: str, carried_from: str = "") -> int:
    """Create or update the head-bound check. 0 on success."""
    conclusion, title, summary = decide(real, verdict)
    detail = detail_body(sha=sha, summary=summary, run_url=run_url,
                         event=event, carried_from=carried_from)
    payload = json.dumps({
        "name": CHECK_NAME,
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {"title": f"{title} @{sha[:8]}", "summary": detail},
    })
    check_id = _existing_check_id(repo, sha)
    if check_id is None:
        args = ["api", "-X", "POST", f"repos/{repo}/check-runs", "--input", "-"]
    else:
        args = ["api", "-X", "PATCH", f"repos/{repo}/check-runs/{check_id}",
                "--input", "-"]
    last = ""
    for attempt in range(1, ATTEMPTS + 1):
        rc, _, err = _gh(args, payload)
        if rc == 0:
            verb = "created" if check_id is None else "updated"
            print(
                f"review check: {verb} {CHECK_NAME!r} = {conclusion} on "
                f"{sha[:8]} (event {event})"
            )
            return 0
        last = err.strip()[:400]
        print(
            f"review check: write attempt {attempt}/{ATTEMPTS} failed: {last}",
            file=sys.stderr,
        )
        if attempt < ATTEMPTS:
            time.sleep(attempt * 5)
    print(
        f"::error::could not publish the review check on {sha} — the pull "
        f"request carries no head-bound record of this review: {last}",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sha", required=True,
                    help="the head commit the critic reviewed (DRE-1990)")
    ap.add_argument("--verdict-file", required=True)
    ap.add_argument("--real", required=True,
                    help="check_critic_result.py's verdict: true|false")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--event", default="")
    ap.add_argument("--carried-from", default="",
                    help="the commit a CARRIED verdict was earned on "
                         "(DRE-2340) — set only on the review-skip path, "
                         "where the reviewer did not re-read this head")
    args = ap.parse_args(argv)
    try:
        with open(args.verdict_file) as f:
            verdict = f.read()
    except OSError:
        verdict = ""
    return publish(
        repo=args.repo,
        sha=args.sha,
        real=args.real.strip().lower() == "true",
        verdict=verdict,
        run_url=args.run_url,
        event=args.event,
        carried_from=args.carried_from,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
