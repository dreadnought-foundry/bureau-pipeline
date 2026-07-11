#!/usr/bin/env python3
"""The merge gate's DECISION — the fleet's single highest-privilege call.

Extracted from inline shell in .github/workflows/merge-gate.yml (DRE-1992);
the pre-extraction shell is frozen at tests/fixtures/merge-gate.ba4305d.yml
and tests/test_merge_gate_decision_table.py proves this module reproduces
its decisions case-for-case. The workflow is now a thin caller: it gathers
the inputs from GitHub's own records and acts on this module's verdict —
no agent claims trusted, no human in the loop.

The conditions (all must pass, evaluated in this order):

0. BRANCH CURRENCY (DRE-1924) — the PR's head must contain its base
   branch's tip, per GitHub's own compare record (GET
   compare/{base}...{head_sha}, status ahead/identical). A stale branch
   (behind/diverged) earned its green against an older base — the exact
   "green alone, red together" class that turned main red on 2026-07-11:
   the Asana connector PR (registered `asana`) and a test PR (asserted
   `asana` is unknown) were each green on their own branch, red once both
   landed. Decision `update`: the workflow updates the branch and exits;
   CI re-runs on the merged result and the gate re-evaluates. An unknown
   status (compare API blip → the workflow substitutes `{}`) is `wait`,
   fail-closed — never merge past an unverifiable base, and never fire
   the update mutation on unverifiable data either. This replaces the
   untested shell `mergeStateStatus == BEHIND` fast-path, which GitHub
   only reports when branch protection's "require branches to be up to
   date" toggle is already on — the compare record works regardless.
   Evaluated FIRST (the old fast-path's position): green checks and a
   bound APPROVE on a stale branch prove nothing about the merged result.

1. CI — every check run on the PR's head SHA has completed green
   (conclusion success/skipped/neutral). The REVIEW workflow's own check
   runs are EXCLUDED: the critic's verdict COMMENT is the review's source
   of truth (condition 2), and a review run killed by an API blip must not
   deadlock the merge. Exclusion is by VERIFIED ORIGIN, never by name
   (DRE-1994): the old `endswith("review")` name test was attacker-nameable
   — check names come from PR-authored workflow files, so a failing job
   named `sneaky-review` was invisible to the all-green rule. Now a check
   run is excluded only if its check suite belongs to a workflow run that
   GitHub's own workflow-runs record attributes to an allowlisted review
   workflow FILE (path). GitHub gives every workflow run its own check
   suite, so a PR-authored workflow — whatever its jobs are named — can
   never place a check run inside the review workflow's suite. Residual
   (documented in tests/test_merge_gate_check_origin.py): a PR modifying
   the review stub at its own path still gets excluded — exactly the run
   class the exclusion targets, and exclusion grants no approval power
   (condition 2 still gates). No counted runs at all → wait (checks
   haven't reported yet).

2. QA Critic — the latest critic verdict comment is APPROVE, bound to the
   PR's current head:
   - AUTHORSHIP (DRE-1987 / #57): only comments authored by the qa-bot App
     count. GitHub reserves the "[bot]" suffix, so no user account can
     impersonate it; the workflow derives the login from the same App key
     it merges with (app-slug of the minted token).
   - SHA BINDING (DRE-1990 / #60): qa-review.yml embeds the reviewed
     commit on the verdict line (`VERDICT: <X> @<full-sha>`). A verdict
     whose SHA is MISSING (pre-DRE-1990, or the neutral could-not-run
     status) or STALE (≠ the current head) is NO verdict — fail-closed,
     the gate waits for a fresh review. Code pushed after a genuine
     APPROVE must not ride that approval into main (PRs #13/#25 did).
   - The SHA check runs BEFORE the APPROVE check, so a stale
     REQUEST_CHANGES reads as "no verdict — wait", not "hold".

3. QA Verifier — scope-gated stage; it may simply never have run:
   - ABSENT verdict → not a gate (falls through).
   - PRESENT verdict proves the PR is in Verifier scope, so a MISSING or
     STALE SHA must HOLD for a fresh verify (DRE-1990 asymmetry — treating
     it as absent would fail OPEN and merge code the Verifier never ran).
   - Bound to the current head: PASS proceeds; SKIP is advisory and
     proceeds too (DRE-1991 / #61 — the Verifier brief promises a SKIP
     never blocks); anything else (FAIL, neutral) holds.
   - Same authorship rule as the critic: a forged FAIL could stall merges,
     a forged PASS could mask a real FAIL.

STRUCTURED / ANCHORED verdict parsing (DRE-1992 scope note, 2026-07-09):
a comment merely QUOTING a verdict marker must not count as one. A comment
is a verdict comment only if its FIRST LINE starts with the marker
(optionally preceded by the producer's emoji — never by a quote prefix like
"> "), and the verdict token only counts in the structured position the
producers emit: `<marker> — VERDICT: <TOKEN> … @<sha>`. The old shell's
contains()/glob matching could be satisfied by quotation or prose; the
four sanctioned differences are documented as delta rows in
tests/test_merge_gate_decision_table.py.

Contract with merge-gate.yml:
  stdin/argv: --head-sha, --qa-login, --check-runs-file (the raw REST
    payload of GET /repos/{repo}/commits/{sha}/check-runs), --comments-file
    (the raw REST payload of GET /repos/{repo}/issues/{pr}/comments),
    --workflow-runs-file (the raw REST payload of
    GET /repos/{repo}/actions/runs?head_sha={sha} — the verified-origin
    record for the review-run exclusion), --compare-file (the status of
    GET /repos/{repo}/compare/{base}...{head_sha} — the branch-currency
    record, DRE-1924), --review-workflows (optional comma-separated
    allowlist of review workflow paths).
  stdout: zero or more `note=` lines, then exactly one `decision=` line
    (merge | update | wait | hold) and one `reason=` line (plain English).
  exit 0 = decided; exit 2 = malformed input (the job fails loudly and
    nothing merges — never fail open).

wait vs hold vs update: `wait` means the gate expects a future event to
change the answer (CI finishing, a fresh review of the current head);
`hold` means an explicit negative verdict is standing (REQUEST_CHANGES,
Verifier FAIL) and only a new verdict lifts it; `update` means the branch
is stale and the workflow should update it from its base (CI then re-runs
on the merged result). None of the three merges.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

CRITIC_MARKER = "QA Critic"
VERIFIER_MARKER = "QA Verifier"

# Workflow FILES whose runs are the review stage — their check runs are
# excluded from condition 1 by verified origin (DRE-1994). Paths as GitHub
# records them on the workflow RUN, not names a PR can choose.
DEFAULT_REVIEW_WORKFLOWS = (
    ".github/workflows/qa-review.yml",  # the product-repo critic stub
    ".github/workflows/pr-review.yml",  # bureau-pipeline's own critic
)

# Green = completed with a conclusion GitHub treats as non-blocking.
GREEN_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})

# GitHub compare/{base}...{head} status values (DRE-1924): the head is
# current when it contains the base's tip, stale when the base has commits
# the head lacks. Anything else is unverifiable → wait, fail-closed.
CURRENT_STATUSES = frozenset({"ahead", "identical"})
STALE_STATUSES = frozenset({"behind", "diverged"})

# A full 40-hex SHA anywhere on the verdict line (`@<sha>`), as the
# producers append it. Abbreviated SHAs deliberately do not bind.
_SHA_RE = re.compile(r"@([0-9a-f]{40})")

# Anchor: the marker must OPEN the first line, allowing only the producer's
# short emoji/badge prefix ("🔎 ", "🧪 ") — never a markdown quote (">") or
# leading prose. This is what makes quoting a verdict inert.
_ANCHOR = r"^\s*(?:[^\w\s>]{1,4}\s+)?"
_HEAD_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _marker_re(marker: str) -> re.Pattern:
    return re.compile(_ANCHOR + re.escape(marker) + r"\b")


def _verdict_re(marker: str) -> re.Pattern:
    # The structured position the producers emit:
    #   { echo "🔎 QA Critic — $(head -1 verdict.md) @${REVIEWED_SHA}"; … }
    # i.e. `<marker> — VERDICT: <TOKEN>` (em-dash), optional trailing prose.
    return re.compile(_ANCHOR + re.escape(marker) + r"\s+—\s+VERDICT:\s*([A-Z_]+)")


@dataclass
class Decision:
    action: str  # merge | wait | hold
    reason: str
    notes: list = field(default_factory=list)


def first_line(body: Optional[str]) -> str:
    body = body or ""
    return body.splitlines()[0] if body else ""


def latest_verdict_comment(comments, qa_login: str, marker: str) -> Optional[str]:
    """Body of the LATEST comment that (a) is authored by the qa-bot App and
    (b) opens with the marker on its first line. None if no such comment —
    forged, human, deleted-account, and quoting/prose comments are invisible,
    not merely non-approving."""
    rx = _marker_re(marker)
    latest = None
    for c in comments:
        user = c.get("user") or {}
        if user.get("login") != qa_login:
            continue
        if not rx.match(first_line(c.get("body"))):
            continue
        latest = c.get("body") or ""
    return latest


def verdict_sha(line: str) -> Optional[str]:
    m = _SHA_RE.search(line)
    return m.group(1) if m else None


def verdict_token(line: str, marker: str) -> Optional[str]:
    """The structured verdict token (APPROVE / REQUEST_CHANGES / PASS /
    FAIL / SKIP), or None when the line carries no structured verdict
    (neutral could-not-run status, prose)."""
    m = _verdict_re(marker).match(line)
    return m.group(1) if m else None


def review_suite_ids(workflow_runs, review_workflows) -> frozenset:
    """Check-suite ids of the runs produced by the review workflow files —
    the verified-origin record (DRE-1994). GitHub attributes every workflow
    run to its workflow FILE (`path`) and gives it its own check suite
    (`check_suite_id`), so a check run ties back to its producing file by
    suite membership; the names a PR chooses for its jobs never enter into
    it. Runs without a suite id never match (a suite-less check run must
    not be excludable via a None match)."""
    return frozenset(
        r["check_suite_id"]
        for r in workflow_runs
        if r.get("path") in review_workflows
        and r.get("check_suite_id") is not None
    )


def evaluate_currency(compare_status) -> Optional[Decision]:
    """Condition 0 (DRE-1924). None = head contains the base's tip,
    proceed. Stale → `update` (the workflow updates the branch; CI re-runs
    on the merged result). Unknown → `wait` — never merge past an
    unverifiable base, and never mutate the branch on unverifiable data."""
    if compare_status in CURRENT_STATUSES:
        return None
    if compare_status in STALE_STATUSES:
        return Decision(
            "update",
            f"branch is {compare_status} relative to its base — its green "
            "was earned against an older base; update the branch and "
            "re-run CI on the merged result",
        )
    return Decision(
        "wait",
        f"branch currency unknown (compare status {compare_status!r}) — "
        "wait; never merge past an unverifiable base",
    )


def evaluate_checks(check_runs, review_suites=frozenset()) -> Optional[Decision]:
    """Condition 1. None = green, proceed. Only check runs sitting in a
    verified review workflow's check suite are excluded — an empty origin
    record (listing API blip) excludes nothing and the gate waits,
    fail-closed."""
    counted = [
        r
        for r in check_runs
        if (r.get("check_suite") or {}).get("id") not in review_suites
    ]
    total = len(counted)
    if total == 0:
        return Decision("wait", "no checks reported yet — wait")
    not_green = [
        r
        for r in counted
        if r.get("status") != "completed"
        or (r.get("conclusion") or "") not in GREEN_CONCLUSIONS
    ]
    if not_green:
        return Decision(
            "wait", f"{len(not_green)} of {total} check runs not green — wait"
        )
    return None


def evaluate_critic(line: str, head_sha: str) -> Optional[Decision]:
    """Condition 2, given the first line of the latest counted critic
    comment ('' if none). None = APPROVE bound to head, proceed."""
    if not line:
        return Decision("wait", "no critic verdict yet — wait")
    sha = verdict_sha(line)
    if sha is None:
        return Decision(
            "wait",
            "critic verdict names no reviewed commit (pre-DRE-1990 format or "
            f"neutral status) — treated as NO verdict; waiting for a fresh "
            f"review of {head_sha}",
        )
    if sha != head_sha:
        return Decision(
            "wait",
            f"critic verdict is for {sha} but head is now {head_sha} — stale; "
            "treated as NO verdict, waiting for a fresh review",
        )
    if verdict_token(line, CRITIC_MARKER) != "APPROVE":
        return Decision("hold", "latest verdict is not APPROVE — holding")
    return None


def evaluate_verifier(line: str, head_sha: str) -> tuple[Optional[Decision], str]:
    """Condition 3, given the first line of the latest counted verifier
    comment ('' if none). Returns (decision-or-None, advisory note);
    None = not a gate / satisfied, proceed."""
    if not line:
        return None, "no verifier verdict (verify out of scope / not run) — not a gate"
    sha = verdict_sha(line)
    if sha is None:
        return (
            Decision(
                "hold",
                "verifier verdict names no verified commit (pre-DRE-1990 "
                "format or neutral status) — holding for a fresh verify",
            ),
            "",
        )
    if sha != head_sha:
        return (
            Decision(
                "hold",
                f"verifier verdict is for {sha} but head is now {head_sha} — "
                "stale; holding for a fresh verify",
            ),
            "",
        )
    token = verdict_token(line, VERIFIER_MARKER)
    if token == "PASS":
        return None, ""
    if token == "SKIP":
        return None, "verifier verdict is SKIP for the current head — advisory, not a gate"
    return Decision("hold", "latest verifier verdict is not PASS — holding"), ""


def decide(
    head_sha: str,
    qa_login: str,
    check_runs,
    comments,
    review_suites=frozenset(),
    compare_status=None,
) -> Decision:
    """The whole gate: conditions 0 → 1 → 2 → 3, first blocker wins.
    `review_suites` is the verified-origin record from review_suite_ids();
    the default (empty — nothing excluded) is the fail-closed direction.
    `compare_status` is the branch-currency record (DRE-1924); the default
    (None — unverifiable → wait) is likewise fail-closed."""
    blocked = evaluate_currency(compare_status)
    if blocked:
        return blocked

    blocked = evaluate_checks(check_runs, review_suites)
    if blocked:
        return blocked

    critic_body = latest_verdict_comment(comments, qa_login, CRITIC_MARKER)
    blocked = evaluate_critic(first_line(critic_body), head_sha)
    if blocked:
        return blocked

    verifier_body = latest_verdict_comment(comments, qa_login, VERIFIER_MARKER)
    blocked, note = evaluate_verifier(first_line(verifier_body), head_sha)
    if blocked:
        return blocked

    decision = Decision(
        "merge", f"CI green + critic APPROVE bound to {head_sha} — merge as qa-bot"
    )
    if note:
        decision.notes.append(note)
    return decision


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--head-sha", required=True,
                        help="the PR's current headRefOid (full 40-hex)")
    parser.add_argument("--qa-login", required=True,
                        help="trusted verdict author, e.g. agent-bureau-qa-bot[bot]")
    parser.add_argument("--check-runs-file", required=True,
                        help="raw REST payload of GET commits/{sha}/check-runs")
    parser.add_argument("--comments-file", required=True,
                        help="raw REST payload of GET issues/{pr}/comments")
    parser.add_argument("--workflow-runs-file", required=True,
                        help="raw REST payload of GET actions/runs?head_sha=<sha> "
                             "— the verified-origin record for the review-run "
                             "exclusion (DRE-1994)")
    parser.add_argument("--compare-file", required=True,
                        help="REST payload of GET compare/{base}...{head_sha} "
                             "(the status field suffices) — the branch-"
                             "currency record (DRE-1924)")
    parser.add_argument("--review-workflows",
                        default=",".join(DEFAULT_REVIEW_WORKFLOWS),
                        help="comma-separated paths of the review workflow "
                             "files whose check runs are excluded from the "
                             "all-green rule")
    return parser


def _die(msg: str) -> "NoReturn":  # noqa: F821
    print(f"merge_gate: {msg}", file=sys.stderr)
    sys.exit(2)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if not _HEAD_SHA_RE.match(args.head_sha or ""):
        _die(f"--head-sha must be a full 40-hex SHA, got {args.head_sha!r}")
    # The [bot] suffix is GitHub-reserved; an empty or non-App login here
    # means the token minting step broke — fail loud, never fail open.
    if not args.qa_login.endswith("[bot]") or len(args.qa_login) <= len("[bot]"):
        _die(f"--qa-login must be a GitHub App login (…[bot]), got {args.qa_login!r}")

    try:
        with open(args.check_runs_file) as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _die(f"cannot read check runs: {e}")
    check_runs = payload.get("check_runs") if isinstance(payload, dict) else payload
    if not isinstance(check_runs, list):
        _die("check-runs payload has no check_runs list")

    try:
        with open(args.comments_file) as f:
            comments = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _die(f"cannot read comments: {e}")
    if not isinstance(comments, list):
        _die("comments payload is not a list")

    try:
        with open(args.workflow_runs_file) as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _die(f"cannot read workflow runs: {e}")
    workflow_runs = (
        payload.get("workflow_runs") if isinstance(payload, dict) else payload
    )
    if not isinstance(workflow_runs, list):
        _die("workflow-runs payload has no workflow_runs list")
    review_paths = frozenset(
        p.strip() for p in args.review_workflows.split(",") if p.strip()
    )
    review_suites = review_suite_ids(workflow_runs, review_paths)

    try:
        with open(args.compare_file) as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _die(f"cannot read compare record: {e}")
    if not isinstance(payload, dict):
        _die("compare payload is not an object")
    # `{}` (the workflow's blip substitute) yields None → wait, fail-closed.
    compare_status = payload.get("status")

    decision = decide(
        args.head_sha, args.qa_login, check_runs, comments, review_suites,
        compare_status,
    )
    for note in decision.notes:
        print(f"note={note}")
    print(f"decision={decision.action}")
    print(f"reason={decision.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
