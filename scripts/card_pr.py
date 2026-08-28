#!/usr/bin/env python3
"""ONE predicate for "did this card produce a PR?" (DRE-2316, stdlib only).

THE BUG THIS EXISTS TO KILL. agent-task's Report step asked

    gh pr list --head "$BRANCH" --json url

and `gh pr list` defaults to `--state open`. On 2026-08-08 PR #137 for
DRE-2316 merged at 22:22:19; the Report step ran ten seconds later, could not
see the merged PR, posted "🪦 dead-run-requeue: agent died with no PR and no
blocker note", and requeued the card. A second agent was dispatched onto work
that had already shipped. The reconcile sweep had already learned this lesson
(`pr_for` passes `--state all`); the run's own report step never did.

Two call sites disagreeing is the whole failure. Per this repo's history a
guard that enumerates its call sites by hand always misses the one added
later, so the determination lives HERE and every caller asks it:

    has_work_pr(pr)   -> a PR counts as work when it is OPEN or MERGED
    find(card, ...)   -> the card's newest counting PR, or None

Properties every caller inherits:

  * `--state all`, always. A merged PR is a PR (that is the DRE-2316 bug).
  * A CLOSED-unmerged PR does NOT count. An abandoned attempt leaves the card
    requeueable, which is what the reconcile review-lane cap is built on
    (DRE-2034).
  * Attribution is confirmed with a \\b-anchored match on the card identifier
    in the head ref, so neither DRE-1343 (an empty --head filter returns the
    repo's newest open PR, which the step then claimed) nor DRE-2025 (DRE-142
    matching agent/DRE-1428-*) can come back through this seam.
  * The newest attempt wins (highest PR number): an older merged PR must never
    shadow a newer open one.
  * A read failure is NOT "no PR" — it raises PrLookupError, and the CLI exits
    3 so a caller can tell "definitely none" from "could not tell" (DRE-2034:
    a 403 parsed as emptiness yanked healthy cards around twice on
    2026-06-28).

CLI (what the workflows call):

    card_pr.py find <CARD> [--branch <B>] [--repo <owner/name>]
    card_pr.py find --branch <B> [--repo <owner/name>]      # card-less refs

Prints "<STATE>\\t<URL>" when a counting PR exists, nothing when there is
definitively none. Exit 0 either way; exit 3 when the answer is unreadable.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 — fixed-arg gh calls, shell=False
import sys

OPEN = "OPEN"
MERGED = "MERGED"
# The states that mean "this card produced work". CLOSED (unmerged) is
# deliberately absent — see the module docstring.
WORK_STATES = (OPEN, MERGED)

# Minimal field set every caller needs. reconcile asks for more (comments,
# headRefOid) and passes its own; the shared matching does not depend on them.
PR_FIELDS = "number,url,headRefName,state"

_LIST_LIMIT = "30"


class PrLookupError(RuntimeError):
    """The PR list could not be read (403, rate limit, network, bad JSON).

    NEVER conflate with "no PR": acting on a fabricated emptiness is the
    DRE-2034 class of bug this type exists to keep separable.
    """


def pr_state(pr: dict | None) -> str:
    return ((pr or {}).get("state") or "").upper()


def has_work_pr(pr: dict | None) -> bool:
    """THE predicate. True when this card/branch has a PR that counts as work.

    A merged PR counts. This one line is the whole DRE-2316 fix: every
    "the agent died with no PR" decision must be `not has_work_pr(...)`.
    """
    return pr is not None and pr_state(pr) in WORK_STATES


def matches_card(head_ref: str | None, identifier: str) -> bool:
    """True iff `head_ref` carries THIS card id, word-anchored (DRE-2025)."""
    if not head_ref or not identifier:
        return False
    return re.search(rf"\b{re.escape(identifier)}\b", head_ref, re.IGNORECASE) is not None


def newest(prs: list[dict]) -> dict | None:
    """The newest (highest-numbered) PR of a candidate list, or None."""
    return max(prs, key=lambda pr: pr.get("number") or 0) if prs else None


def _gh_json(args: list[str]) -> str:
    """Run a read-path `gh` command LOUDLY: rc != 0 raises PrLookupError."""
    p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", *args], capture_output=True, text=True, check=False
    )
    if p.returncode != 0:
        raise PrLookupError(
            f"gh {' '.join(args)} failed rc={p.returncode}: {p.stderr.strip()[:400]}"
        )
    return p.stdout


def _list(args: list[str], *, repo: str | None, fields: str, run) -> list[dict]:
    cmd = ["pr", "list"]
    if repo:
        cmd += ["--repo", repo]
    # --state all: the DRE-2316 fix. Never rely on gh's default (open only).
    cmd += ["--state", "all", "--limit", _LIST_LIMIT, "--json", fields, *args]
    try:
        return json.loads(run(cmd) or "[]")
    except ValueError as e:  # unparseable payload is unreadable, not "none"
        raise PrLookupError(f"gh {' '.join(cmd)}: bad JSON: {e}") from e


def find(
    identifier: str | None = None,
    *,
    branch: str | None = None,
    repo: str | None = None,
    fields: str = PR_FIELDS,
    run=None,
) -> dict | None:
    """The newest COUNTING PR for a card (and/or an exact head branch).

    Lookup order — head branch first (cheap and exact), then the card search
    `head:agent/DRE-N` as a fallback for when the branch ref is gone (a merge
    deletes it) or never resolved locally. Candidates from either query must
    survive the same attribution confirm.

    Raises PrLookupError if any query could not be read.
    """
    run = run or _gh_json
    candidates: list[dict] = []
    if branch:
        candidates += _list(["--head", branch], repo=repo, fields=fields, run=run)
    if identifier and not _matching(candidates, identifier, branch):
        candidates += _list(
            ["--search", f"head:agent/{identifier}"], repo=repo, fields=fields, run=run
        )
    return newest(_matching(candidates, identifier, branch))


def _matching(prs: list[dict], identifier: str | None, branch: str | None) -> list[dict]:
    """Candidates that both BELONG to this card/branch and count as work.

    With an identifier the head ref must carry it (anchored). Without one —
    red-main-repair's `repair/*` refs have no card — the head ref must equal
    the requested branch exactly; a loose match there would let any PR through.
    """
    out = []
    for pr in prs:
        head = pr.get("headRefName") or ""
        owned = matches_card(head, identifier) if identifier else head == (branch or "")
        if owned and has_work_pr(pr):
            out.append(pr)
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="card_pr.py", add_help=True)
    sub = parser.add_subparsers(dest="cmd")
    find_p = sub.add_parser("find", help="print '<STATE>\\t<URL>' for the card's PR")
    find_p.add_argument("card", nargs="?", default="")
    find_p.add_argument("--branch", default="")
    find_p.add_argument("--repo", default="")
    if not argv:
        parser.print_usage()
        return 2
    ns = parser.parse_args(argv)
    if ns.cmd != "find" or not (ns.card or ns.branch):
        parser.print_usage()
        return 2
    try:
        pr = find(ns.card or None, branch=ns.branch or None, repo=ns.repo or None)
    except PrLookupError as e:
        # Exit 3, printing NOTHING on stdout: "could not tell" must never be
        # read by a caller as "definitely no PR" (DRE-2034).
        print(f"card_pr: {e}", file=sys.stderr)
        return 3
    if pr:
        print(f"{pr_state(pr)}\t{pr.get('url') or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
