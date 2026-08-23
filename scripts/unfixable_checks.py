#!/usr/bin/env python3
"""Required checks that only a history rewrite can satisfy (DRE-2694).

Origin (live, 2026-08-23): bureau-pipeline PR #176 (DRE-2672) sat blocked for
over three hours. The critic confirmed the feature work, the tests, the README
update and the scope were all sound, and raised exactly ONE blocking finding —
the required `TDD commit discipline` check was red, because the branch's
commits were in the wrong ORDER (config, then code, then the test seven hours
later).

The fix agent can ADD commits. It cannot REORDER them: order changes only by
rewriting history, which needs a force-push it does not have and should not
have. It diagnosed that correctly and refused — on attempt TWO, after a full
CI round and a full critic round — and the PR then stopped permanently, with
nothing anywhere saying "this class of failure has no automated path to green".

Every other red check the fix loop can attempt. This one it can only report.
So this module is the registry of that class, plus the decision the loop takes
from a PR's failed check runs:

  * ESCALATE — at least one failed check has no add-a-commit path. Say so on
    the PR, park the card in the CEO's "needs you" queue, and do NOT spend a
    fix attempt that could only end here. On the FIRST attempt, not the second.
  * FIX — ordinary work: the loop's normal business.

The registry is deliberately tiny and deliberately not a label. Membership is
a structural fact about a check ("its finding is the ORDER of the commits, and
order is not a thing a new commit can change"), not a property a build agent
could award itself. Adding a row means claiming that no commit ANYONE could
push would turn the check green.

Matching is on the CHECK RUN name, which GitHub takes from the job's `name:`.
A repo that runs the check through a stub or a reusable workflow publishes it
nested — "Pipeline Tests / TDD commit discipline" — so matching is a
case-folded substring, not equality. Erring loose is the safe direction here:
the failure mode of a false match is a human being asked to look at a PR, and
the failure mode of a miss is the three hours this card is about.

Read as a library by check_tdd_commits.py (so the red check states its own
remedy, at CI time, from ONE source) and as a CLI by agent-fix.yml:

    python3 unfixable_checks.py decide --checks-file <gh api check-runs json> \\
        [--attempt N] [--pr N] [--comment-out FILE] [--card-note-out FILE]

stdout line 1 is the decision. Exit 0 = decided; exit 2 = could not read the
payload (loud — a check-runs read that silently answered "fix" would put the
loop straight back where it started, and one that answered "escalate" would
park healthy cards).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import NamedTuple

ESCALATE = "escalate"
FIX = "fix"

# The line that was missing on PR #176, in one place. It appears in the red
# check's own output AND in the fix loop's hold comment, so a writer meets the
# same sentence whichever end they arrive from.
NO_ADD_A_COMMIT_LINE = (
    "This check cannot be satisfied by adding another commit — there is no "
    "automated path to green."
)

# Binds the hold comment to a head sha so a re-fired critic does not re-post
# it (the fix-convergence-halt pattern, DRE-2024).
HOLD_MARKER = "unfixable-check-hold"

# The heading of the delegation rule in standards/engineering.md, named here
# so tests/test_unfixable_check_escalation.py pins the two together.
DELEGATION_RULE_HEADING = "## Delegating work that will commit"

# GitHub check-run conclusions that mean "this check reported a failure".
# `cancelled` is deliberately absent: a cancelled run never reported anything,
# so it is not evidence of a structural violation and must not park a card.
# Same for `neutral`, `skipped` and a null conclusion (still running).
FAILED_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "action_required", "startup_failure"}
)


class UnfixableCheck(NamedTuple):
    """One required check that no pushed commit can turn green."""

    check_name: str
    what: str            # what the check found, in one line
    why_unfixable: str   # why adding a commit cannot clear it
    remedy: str          # what a human has to do instead


TDD_CHECK_NAME = "TDD commit discipline"

UNFIXABLE_CHECKS: dict[str, UnfixableCheck] = {
    TDD_CHECK_NAME: UnfixableCheck(
        check_name=TDD_CHECK_NAME,
        what=(
            "the branch's commits are in the wrong order — no commit touching "
            "`tests/` comes before the first commit that changes non-test code"
        ),
        why_unfixable=(
            "the finding is the ORDER of commits that already exist, and order "
            "changes only by rewriting the branch's history. The fix loop can "
            "add commits; it cannot move one that is already behind another, "
            "and it holds no force-push rights on this branch (by design)"
        ),
        remedy=(
            "someone with permission to rewrite this branch reorders the "
            "existing commits so a test commit comes first (an interactive "
            "rebase, or cherry-picking them onto a fresh branch in test-first "
            "order) and force-pushes. The CONTENT does not change: the "
            "resulting tree must be identical to the one it replaces, which is "
            "what makes the rewrite safe to check by machine"
        ),
    ),
}


def _normalize(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def match(check_name: str) -> UnfixableCheck | None:
    """The registry entry a check-run name refers to, or None.

    Case-folded substring so a nested "<caller> / <job>" check-run name — the
    shape every repo that runs this through a stub publishes — still matches.
    """
    haystack = _normalize(check_name)
    if not haystack:
        return None
    for key, entry in UNFIXABLE_CHECKS.items():
        if _normalize(key) in haystack:
            return entry
    return None


def _check_runs(payload):
    """Every check-run record in any shape `gh api` emits for
    `repos/{repo}/commits/{sha}/check-runs`: the bare object, the
    array-of-pages `--paginate --slurp` produces, or a flat list of runs.

    Raises ValueError on anything else — a payload we cannot read is not an
    all-green board.
    """
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("check-runs payload must be an object or an array")
    runs = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("check-runs payload must contain objects")
        if "check_runs" in item:
            page = item["check_runs"]
            if not isinstance(page, list):
                raise ValueError("check_runs must be an array")
            runs.extend(page)
        else:
            runs.append(item)
    if not all(isinstance(r, dict) for r in runs):
        raise ValueError("check-runs payload must contain check-run objects")
    return runs


def failed_check_names(payload) -> list[str]:
    """The names of the check runs that FAILED, in payload order."""
    return [
        r.get("name") or ""
        for r in _check_runs(payload)
        if (r.get("conclusion") or "") in FAILED_CONCLUSIONS
    ]


def unfixable_failures(failed_names) -> list[UnfixableCheck]:
    """The registry entries the failed names hit, deduped, order preserved."""
    seen: set[str] = set()
    entries = []
    for name in failed_names:
        entry = match(name)
        if entry and entry.check_name not in seen:
            seen.add(entry.check_name)
            entries.append(entry)
    return entries


def decide(failed_names) -> str:
    """ESCALATE iff any failed check has no add-a-commit path, else FIX."""
    return ESCALATE if unfixable_failures(failed_names) else FIX


def remedy_block(entry: UnfixableCheck) -> str:
    """The two sentences the RED check prints under its own failure message,
    so the writer learns the remedy at CI time rather than after a critic
    round (DRE-2694 option 2 — fail earlier, where it is cheap)."""
    return f"{NO_ADD_A_COMMIT_LINE} What clears it: {entry.remedy}."


def pr_comment(entries, attempt=None) -> str:
    """The hold comment on the pull request. Names the check, states plainly
    that nothing the loop could push will clear it, and says what does.

    Emits no verdict-shaped text (standards/untrusted-content.md): verdict
    markers are an approval credential and only the critic writes one.
    """
    head = (
        f"🛑 {HOLD_MARKER}: this pull request has "
        f"{len(entries)} required check(s) the fix loop structurally cannot "
        f"address, so it is escalating instead of attempting."
    )
    parts = [head, ""]
    for entry in entries:
        parts += [
            f"**{entry.check_name}** — {entry.what}.",
            "",
            f"{NO_ADD_A_COMMIT_LINE} Why: {entry.why_unfixable}.",
            "",
            f"What clears it: {entry.remedy}.",
            "",
        ]
    if attempt is not None:
        parts.append(
            f"Escalated on attempt {attempt} rather than spending fix rounds "
            f"that could only end here. Once the branch head moves, CI and the "
            f"review re-run on their own — no dispatch needed."
        )
    else:
        parts.append(
            "Once the branch head moves, CI and the review re-run on their "
            "own — no dispatch needed."
        )
    return "\n".join(parts).rstrip() + "\n"


def card_note(entries, pr=None) -> str:
    """The Linear comment the CEO reads. Plain English, outcome and risk, one
    ask — standards/comms.md. No file paths, no commands, no code."""
    what = "; ".join(
        "the work was committed in an order a required rule does not allow"
        if e.check_name == TDD_CHECK_NAME
        else e.check_name
        for e in entries
    )
    where = f" Details on PR #{pr}." if pr is not None else ""
    return (
        "🙋 This pull request is held by a rule the automation cannot satisfy "
        f"on its own: {what}.\n\n"
        "The work itself is not in question. Nothing needs rewriting — the "
        "same commits need putting in a different order, and only a person "
        "with permission to rewrite the branch can do that here.\n\n"
        "The pipeline stopped on the first attempt instead of burning review "
        f"rounds it could not win.{where} Once someone reorders it, the checks "
        "and the review run again by themselves."
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Decide whether the fix loop "
                                                 "can address a PR's red checks")
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--checks-file", required=True,
                   help="JSON from `gh api repos/{repo}/commits/{sha}/check-runs`")
    d.add_argument("--attempt", type=int)
    d.add_argument("--pr", type=int)
    d.add_argument("--comment-out")
    d.add_argument("--card-note-out")
    args = parser.parse_args(argv)

    try:
        with open(args.checks_file, encoding="utf-8") as fh:
            failed = failed_check_names(json.load(fh))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # Loud. Neither answer is safe to guess: "fix" reproduces the stall,
        # "escalate" parks healthy cards.
        print(f"unfixable_checks: cannot read check runs: {exc}", file=sys.stderr)
        return 2

    entries = unfixable_failures(failed)
    decision = ESCALATE if entries else FIX
    print(decision)
    print(
        f"{len(failed)} failed check(s); "
        f"{len(entries)} with no add-a-commit path"
        + (f": {', '.join(e.check_name for e in entries)}" if entries else ""),
        file=sys.stderr,
    )
    if entries:
        if args.comment_out:
            with open(args.comment_out, "w", encoding="utf-8") as fh:
                fh.write(pr_comment(entries, args.attempt))
        if args.card_note_out:
            with open(args.card_note_out, "w", encoding="utf-8") as fh:
                fh.write(card_note(entries, args.pr))
    return 0


if __name__ == "__main__":
    sys.exit(main())
