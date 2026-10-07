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

DRE-2340 adds the one skip that is not a guess: when the latest qa-bot
critic verdict is an APPROVE whose CONTENT id equals the current head's,
the critic has already read exactly this diff and re-reading it would
produce the same verdict. That is not an inference from a branch name — it
is the gate's own binding, decided by the gate's own carry predicate
(`merge_gate.carries_content`, see scripts/verdict_content.py) so the skip
can never be wider than the carry. Six of PR #205's seven reviews were that
case.

Called from qa-review.yml's "Decide review" step:

    python3 should_review_pr.py "<head-branch>" \
      [--comments-file <issues/{pr}/comments>] \
      [--compare-file <compare/{base}...{head}>] \
      [--pr-commits-file <pulls/{pr}/commits>] [--qa-login <login>]

DRE-5801 adds the other skip that is not a guess: GitHub's own `isDraft`
for the PR (`--is-draft`). A draft is a person's work in progress, and the
rest of the pipeline already reads it that way — the merge gate never merges
one (DRE-3467), the reconcile sweeps skip them. On 2026-10-04 the critic was
the one that did not: five hand-built drafts drew REQUEST_CHANGES, the fix
agent disputed each finding, and each card was parked in Triage for a person
to move back to Hand-work. Marking the PR ready for review fires the review
(`ready_for_review` in the stub), so the skip only waits. It applies to a
dispatched re-review too (`--requested`), the second way a critic reaches a
draft. An unreadable flag reviews — this step's fail-soft direction.

DRE-6192 adds the one event that reviews only by exception: `edited`. On
agent-bureau #3323 (2026-10-07) the critic sent a proof record back for a
missing `What's new:` line, a person fixed the description by hand, and the
pull request sat until someone re-ran the review — nobody pushed, so nothing
fired. An `edited` event (`--event-action edited`) reviews when all three
hold: the body changed (`--body-changed`, the payload's `changes.body`), the
newest qa-bot verdict is REQUEST_CHANGES bound to the current head
(`--head-sha`), and the editor is not a Bot (`--sender-is-bot`). Every other
edit skips, and a standing APPROVE still prints its carry. Only a person's
edit counts because the critic never edits a body and the fix agent always
pushes after its own (DRE-5632), so the path cannot loop.

Exit 0 → review (run the critic). Exit 1 → skip. Prints `review=true|false`
on stdout for the workflow to capture as a step output, plus `carried_sha=`
and `content_id=` on a carry skip so the workflow can re-publish the
head-bound review check against the commit the verdict was earned on, or
`draft=true` on a draft skip, which publishes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# ONE implementation of the verdict read (DRE-2340, trap 4): the gate's.
# A second copy here would drift the way reconcile's did (DRE-1998), and
# this one decides whether a paid review happens at all.
import merge_gate
from verdict_content import content_id

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


def carried_approve(
    comments,
    qa_login: str,
    head_content_id: str | None,
    pr_commit_shas=frozenset(),
) -> str | None:
    """The reviewed SHA of a standing APPROVE that still binds this head's
    CONTENT, or None (DRE-2340).

    Read entirely through merge_gate: same authorship filter (DRE-1987 — a
    forged comment is invisible, so it can never suppress a review), same
    anchored verdict parsing, and — critically — the gate's OWN carry
    predicate, `carries_content`, not a hand-rolled restatement of it.

    Why the call and not a copy (the review finding on this PR): the skip
    must be a strict SUBSET of the gate's carry. A hand-rolled chain here
    checked three of the gate's four conditions — it omitted condition 4,
    that the reviewed commit is still in the PR's own commit record — and
    the two predicates then disagreed on a content-preserving head rewrite
    (`git commit --amend`, a rebase onto an unmoved base, `@dependabot
    rebase`, or simply a commit record longer than the fetched window).
    On that state the skip said "no review needed" while the gate said
    "stale, waiting for a fresh review", and reconcile's review-lane sweep — also
    missing condition 4 — nudged the GATE rather than the review. Nothing
    ordered the review the gate was waiting for, so the PR stalled until a
    human dispatched one by hand: the DRE-2071 failure mode. Sharing the
    predicate makes the subset relation hold by construction.

    `pr_commit_shas` is the condition-4 record (merge_gate.commit_shas of
    `GET pulls/{pr}/commits`). Its default — empty, the `[]` blip
    substitute — carries nothing, so an unreadable record means "no proof of
    a carry" and the review runs, which is this step's fail-soft direction.

    A verdict already bound to the head by SHA is NOT a carry — that head
    has been reviewed and nothing is about to re-review it anyway.
    """
    if not head_content_id or not qa_login or not comments:
        return None
    line = merge_gate.first_line(
        merge_gate.latest_verdict_comment(
            comments, qa_login, merge_gate.CRITIC_MARKER
        )
    )
    if not line:
        return None
    if merge_gate.verdict_token(line, merge_gate.CRITIC_MARKER) != "APPROVE":
        return None
    sha = merge_gate.verdict_sha(line)
    if not sha:
        return None
    if not merge_gate.carries_content(
        line, sha, head_content_id, pr_commit_shas
    ):
        return None
    return sha


def edit_rereviews(
    comments,
    qa_login: str,
    head_sha: str | None,
    body_changed: bool,
    sender_is_bot: bool,
) -> bool:
    """True when a pull request's `edited` event deserves a review
    (DRE-6192): a person changed the body while the critic's REQUEST_CHANGES
    stands on the unchanged head.

    The verdict is read through merge_gate exactly as carried_approve reads
    it — same authorship filter, same anchored parse — so a forged or quoted
    REQUEST_CHANGES is invisible and can never start a review. The NEWEST
    verdict decides: one already answered by an APPROVE starts nothing.

    A verdict on an older sha is a skip, not a review: the push that moved
    the head already started the review of it. No condition reads WHAT the
    findings were about — no machine-readable signal for "a body finding"
    exists — so a person's edit after a code finding costs one review that
    repeats the finding, bounded by needing a person's edit each time.
    """
    if not body_changed or sender_is_bot or not head_sha or not qa_login:
        return False
    line = merge_gate.first_line(
        merge_gate.latest_verdict_comment(
            comments or [], qa_login, merge_gate.CRITIC_MARKER
        )
    )
    if not line:
        return False
    if (merge_gate.verdict_token(line, merge_gate.CRITIC_MARKER)
            != "REQUEST_CHANGES"):
        return False
    return merge_gate.verdict_sha(line) == head_sha


def should_review(
    branch: str | None,
    comments=None,
    qa_login: str = "",
    head_content_id: str | None = None,
    pr_commit_shas=frozenset(),
    is_draft: bool = False,
) -> bool:
    """True — the critic reviews every pull request (DRE-2250), unless a
    standing APPROVE already binds this head's content (DRE-2340).

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

    THE ONE SKIP (DRE-2340). The branch name is still never consulted. The
    skip fires only on the gate's own binding, evaluated by the gate's own
    predicate: the latest qa-bot critic verdict is an APPROVE, the CONTENT
    id it carries equals the id the caller computed for the current head,
    and the reviewed commit is still in the PR's own commit record — i.e.
    the PR's own contribution is byte-identical to the diff that verdict
    was earned on, and only a base merge (or an empty commit) moved the
    head. Re-reviewing would spend a full critic run to re-read an
    unchanged diff and re-issue the same verdict; on portico PR #205 that
    happened six times in 47 minutes. This is not a guess about triviality
    — it is a proof about content, and every unprovable case returns True.

    The skip is a strict SUBSET of the gate's carry, by construction (see
    carried_approve): skipping a review the gate will not honour deadlocks
    the PR, because nothing else ever orders that review.

    A DRAFT IS NOT REVIEWED (DRE-5801). `is_draft` is GitHub's own flag, the
    visible signal a person sets, so it is not a guess either. Nothing waits
    on a verdict for a draft — the gate never merges one — and marking it
    ready fires the review that skipping here deferred.
    """
    if is_draft:
        return False
    return carried_approve(
        comments, qa_login, head_content_id, pr_commit_shas
    ) is None


def _load(path: str | None, fallback):
    """A payload file, or the fallback on anything unreadable. An
    unreadable record must mean "no proof of a carry" (→ review), never a
    crashed review job."""
    if not path:
        return fallback
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        print(f"should_review_pr: cannot read {path}: {e}", file=sys.stderr)
        return fallback


def _flatten(comments):
    """The comment record as one flat list, through the gate's own flattener
    (DRE-4139). A payload that is not a comment record at all carries no
    proof of a carry, so it degrades to `[]` — review — rather than raising
    inside a step whose every read is fail-soft."""
    try:
        return merge_gate.flatten_pages(comments)
    except ValueError as e:
        print(f"should_review_pr: unreadable comment record: {e}",
              file=sys.stderr)
        return []


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("branch", nargs="?", default="",
                    help="the PR's head branch ref")
    ap.add_argument("--comments-file", default=None,
                    help="raw REST payload of GET issues/{pr}/comments — the "
                         "standing verdict record (DRE-2340)")
    ap.add_argument("--compare-file", default=None,
                    help="raw REST payload of GET compare/{base}...{head} — "
                         "the current head's content record (DRE-2340)")
    ap.add_argument("--pr-commits-file", default=None,
                    help="raw REST payload of GET pulls/{pr}/commits — the "
                         "carry's condition 4, the same record merge-gate "
                         "reads; omitted/unreadable = no carry (review)")
    ap.add_argument("--qa-login", default="",
                    help="trusted verdict author, e.g. "
                         "agent-bureau-qa-bot[bot]; omitted = no carry")
    ap.add_argument("--is-draft", default="",
                    help="GitHub's own isDraft for the PR, `true`/`false` "
                         "(DRE-5801); a draft is skipped, anything else "
                         "unreadable reviews")
    ap.add_argument("--requested", action="store_true",
                    help="a dispatched re-review: reviewed on request unless "
                         "the PR is a draft — no carry is consulted")
    ap.add_argument("--event-action", default="",
                    help="the pull_request event's `action`; only `edited` "
                         "changes the decision (DRE-6192)")
    ap.add_argument("--body-changed", default="",
                    help="`true` when an `edited` payload carries "
                         "changes.body; anything else is a title-only edit")
    ap.add_argument("--sender-is-bot", default="",
                    help="`false` when the editor is a person; anything "
                         "else, unreadable included, is not a person's edit")
    ap.add_argument("--head-sha", default="",
                    help="the PR's current head, which the REQUEST_CHANGES "
                         "must be bound to for an edit to review")
    return ap


def _draft(raw: str) -> bool:
    """True only for GitHub's literal `true`. Empty, `null` or anything else
    is an unreadable flag, and an unreadable flag reviews: skipping on a
    guess is the expensive mistake here, not reviewing."""
    return (raw or "").strip().lower() == "true"


def _decide_edit(args, comments, carried, head_content_id) -> int:
    """Print the `edited` event's decision (DRE-6192). Its fail direction is
    the opposite of every other path's: an edit added a review that did not
    exist before, so an unreadable fact SKIPS — only a literal `true` body
    change and a literal `false` bot flag admit the edit."""
    branch = args.branch
    if edit_rereviews(
        comments, args.qa_login, args.head_sha,
        body_changed=_draft(args.body_changed),
        sender_is_bot=(args.sender_is_bot or "").strip().lower() != "false",
    ):
        print("review=true")
        print(
            f"will re-review {branch!r} — a person edited the description "
            f"while the critic's REQUEST_CHANGES stands on {args.head_sha} "
            "(DRE-6192)"
        )
        return 0
    print("review=false")
    if carried:
        # A standing APPROVE takes today's carry path: the workflow
        # re-publishes the head-bound check exactly as on a push.
        print(f"carried_sha={carried}")
        print(f"content_id={head_content_id}")
    print(
        f"skipping {branch!r} — an edit reviews only when a person changes "
        "the description while the critic's REQUEST_CHANGES stands on the "
        "unchanged head (DRE-6192)"
    )
    return 1


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    branch = args.branch
    # DRE-5801: a draft first, on every event — before any carry is read,
    # because a draft skip re-publishes nothing.
    if _draft(args.is_draft):
        print("review=false")
        print("draft=true")
        print(
            f"skipping {branch or 'this pull request'!r} — it is a draft, a "
            "person's work in progress; marking it ready for review starts "
            "the review (DRE-5801)"
        )
        return 1
    if args.requested:
        print("review=true")
        print("workflow_dispatch — reviewing on request")
        return 0
    # DRE-4139: qa-review.yml fetches this record with the same call shape
    # merge-gate.yml uses, so the skip and the gate can never disagree about
    # the standing verdict — which now means every page of it, arriving as
    # the array of PAGES `--slurp` emits. Unflattenable reads as no record at
    # all, this step's fail-soft direction (→ review).
    comments = _flatten(_load(args.comments_file, []))
    head_content_id = content_id(_load(args.compare_file, {}))
    pr_commit_shas = merge_gate.commit_shas(_load(args.pr_commits_file, []))
    carried = carried_approve(
        comments, args.qa_login, head_content_id, pr_commit_shas
    )
    if args.event_action == "edited":
        return _decide_edit(args, comments, carried, head_content_id)
    review = should_review(
        branch, comments=comments, qa_login=args.qa_login,
        head_content_id=head_content_id, pr_commit_shas=pr_commit_shas,
    )
    print(f"review={'true' if review else 'false'}")
    if review:
        card = card_in_branch(branch)
        print(
            f"will review {branch!r}"
            + (f" (linked card {card})" if card else "")
        )
        return 0
    # DRE-2340: the only skip there is. Emit what the workflow needs to
    # re-publish the head-bound review check against the reviewed commit —
    # a skip is silent on the PR (the standing verdict stays the latest
    # comment), so the CHECK is the only place the head can show an honest
    # review status.
    print(f"carried_sha={carried}")
    print(f"content_id={head_content_id}")
    print(
        f"skipping {branch!r} — the standing APPROVE for {carried} still "
        "binds this head's content; re-reviewing would re-read an unchanged "
        "diff (DRE-2340)"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
