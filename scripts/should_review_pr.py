#!/usr/bin/env python3
"""Decide whether the adversarial QA critic should review a PR (stdlib only).

DRE-1888. The critic (qa-review.yml) used to run only on agent-dispatched
work — branches matching the `agent/DRE-N-*` convention. Operator-routed
cards (e.g. anything that changes bureau-pipeline / the relay, which the
pipeline's repo-scoped agent tokens cannot author) ship on operator branches
like `fix/DRE-1885-...` or `feat/DRE-1888-...`. Those PRs were SKIPPED by the
critic, so operator work merged without a real adversarial verdict — the same
gate every normal card PR has to pass.

This helper opts operator-routed CARD PRs in. The native signal is the one
linear-sync already uses to close the loop: the PR's head branch carries a
linked Linear card reference (`DRE-<n>`). A PR with a linked card is real
product work and gets the critic; a truly chrome-only PR (no linked card —
a docs tweak, a dependency bump on a `chore/...` branch) stays skippable so
the gate never blocks on things that aren't card work.

Decision (branch-name only, so it runs as cheaply as the old `if:` guard):

  • branch starts with `agent/`            → review   (unchanged; no regression)
  • branch starts with `repair/`           → review   (red-main repair PR —
    DRE-1927: it carries no card, so without this opt-in an agent-authored
    fix to a broken main would merge with NO adversarial review, the exact
    bypass adr-red-main-auto-repair's guardrail 1 forbids)
  • branch starts with `dependabot/`       → review   (dependency PR —
    DRE-2039: the merge gate auto-merges a grouped minor/patch bump ONLY on
    a SHA-bound critic APPROVE, so without this opt-in the gate would wait
    forever on a verdict that can't exist)
  • branch carries a `DRE-<n>` reference   → review   (operator-routed card PR)
  • otherwise (no linked card)             → skip     (chrome-only)

Called from qa-review.yml's "Decide review" step:

    python3 should_review_pr.py "<head-branch>"

Exit 0 → review (run the critic). Exit 1 → skip. Also prints `review=true|false`
on stdout for the workflow to capture as a step output.
"""

from __future__ import annotations

import re
import sys

# Same pattern linear-sync.yml / merge-gate.yml use to pull the card from a
# branch ref — keep these in lockstep so "has a linked card" means exactly
# what the rest of the pipeline means by it. Case-INsensitive (DRE-2003): the
# workflow-level contains() guard already was, so a lowercase `ops/dre-N-...`
# branch used to start the review job while this gate said skip — a silent
# review bypass. Extracted ids are normalized to uppercase before use
# (Linear identifiers are uppercase; `dre-123` must resolve to card DRE-123).
_CARD_RE = re.compile(r"DRE-[0-9]+", re.IGNORECASE)


def card_in_branch(branch: str | None) -> str | None:
    """The first `DRE-<n>` card reference in the branch ref (uppercased,
    matching Linear's identifier convention), or None."""
    if not branch:
        return None
    m = _CARD_RE.search(branch)
    return m.group(0).upper() if m else None


def should_review(branch: str | None) -> bool:
    """True — the critic reviews every pull request (DRE-2250).

    This function is the ONE place review policy lives. qa-review.yml's job
    gate is mechanical only; a rule about WHICH PRs deserve review goes here,
    where it can be unit-tested.

    Why there is no longer a skip path: the old rule inferred triviality from
    the branch name — review `agent/`, `repair/`, `dependabot/`, or anything
    carrying a `DRE-<n>` card ref; skip the rest as "chrome-only". The
    inference was wrong in the expensive direction. Substantive work routinely
    lands on a branch named for the change rather than the card (`fix/…`,
    `model/…`, `db/…`), and each of those merged with no adversarial verdict
    AND no signal that one was missing — the PR simply looked green. On
    2026-07-29 that described all five open PRs across the fleet; the one
    reviewed by hand carried three real defects.

    The rule had already been widened twice for this same reason (DRE-1888 for
    operator card branches, DRE-2003 for lowercase refs). Each widening kept
    the guess and moved the boundary. Removing the guess ends the class.

    The cost is real and deliberate: the critic now runs on genuinely trivial
    PRs too. That is the cheaper mistake — a needless review costs tokens; a
    missed one has already shipped a regression (DRE-1825).

    If a skip is ever wanted again, do NOT reintroduce it as a branch-name
    rule. Make it an explicit signal a human sets and can see (a PR label), so
    opting out is a visible choice rather than an accident of what someone
    happened to call their branch.
    """
    return True


def main(argv: list[str]) -> int:
    branch = argv[0] if argv else ""
    review = should_review(branch)
    print(f"review={'true' if review else 'false'}")
    if review:
        card = card_in_branch(branch)
        print(
            f"will review {branch!r}"
            + (f" (linked card {card})" if card else "")
        )
        return 0
    # Unreachable today — every PR is reviewed. Kept so the workflow's
    # exit-code contract (0 review / 1 skip) still holds if an explicit
    # opt-out is ever added, and so a skip can never be silent when it is.
    print(f"skipping {branch!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
