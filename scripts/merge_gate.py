#!/usr/bin/env python3
"""The merge gate's DECISION — the fleet's single highest-privilege call.

Extracted from inline shell in .github/workflows/merge-gate.yml (DRE-1992);
the pre-extraction shell is frozen at tests/fixtures/merge-gate.ba4305d.yml
and tests/test_merge_gate_decision_table.py proves this module reproduces
its decisions case-for-case. The workflow is now a thin caller: it gathers
the inputs from GitHub's own records and acts on this module's verdict —
no agent claims trusted, no human in the loop.

The three conditions (all must pass, evaluated in this order):

1. CI — every check run on the PR's head SHA has completed green
   (conclusion success/skipped/neutral). Check runs whose name ends with
   "review" are EXCLUDED: the critic's verdict COMMENT is the review's
   source of truth (condition 2), and a review run killed by an API blip
   must not deadlock the merge. No runs at all → wait (checks haven't
   reported yet).

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
    (the raw REST payload of GET /repos/{repo}/issues/{pr}/comments).
  stdout: zero or more `note=` lines, then exactly one `decision=` line
    (merge | wait | hold) and one `reason=` line (plain English).
  exit 0 = decided; exit 2 = malformed input (the job fails loudly and
    nothing merges — never fail open).

wait vs hold: `wait` means the gate expects a future event to change the
answer (CI finishing, a fresh review of the current head); `hold` means an
explicit negative verdict is standing (REQUEST_CHANGES, Verifier FAIL) and
only a new verdict lifts it. The workflow treats both as "do not merge".
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

# Green = completed with a conclusion GitHub treats as non-blocking.
GREEN_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})

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


def evaluate_checks(check_runs) -> Optional[Decision]:
    """Condition 1. None = green, proceed."""
    counted = [
        r for r in check_runs if not str(r.get("name") or "").endswith("review")
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


def decide(head_sha: str, qa_login: str, check_runs, comments) -> Decision:
    """The whole gate: conditions 1 → 2 → 3, first blocker wins."""
    blocked = evaluate_checks(check_runs)
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

    decision = decide(args.head_sha, args.qa_login, check_runs, comments)
    for note in decision.notes:
        print(f"note={note}")
    print(f"decision={decision.action}")
    print(f"reason={decision.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
