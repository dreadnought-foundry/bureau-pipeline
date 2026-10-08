#!/usr/bin/env python3
"""The question a spent review budget asks (DRE-6189).

When the review lane's re-trigger budget is spent on a head (REVIEW_NUDGE_CAP,
DRE-5231), the sweep parks the card in Green Light (DRE-6181) and posts the
same question on the pull request as a fix-loop blocker. This module composes
both texts from the evidence the sweep already holds — the budget, the head,
what the critic and the Verifier say on it, the gate's newest hold note —
and never from a model call. It reads nothing and writes nothing: no Linear,
no GitHub, no lane.

The question is the one Green Light format (DRE-3908): one
`console_escalation.Escalation`, rendered by `console_escalation.render`, so
the Finding, Question and Recommendation lines are declared there and
restated nowhere here. No choices block, for the reason DRE-5204 gives for
the code-owner hold: the answer is a push or a comment on the pull request,
never a click on the card. Both texts render the same `Escalation`, so the
three lines are identical in each.

  * `compose(...)` — the Linear comment: the `🚨 review-nudge-cap` notice the
    sweep looks for, the three lines, the way back, and the per-head key.
  * `pr_blocker(...)` — the pull request comment: its first line opens
    `fix_context.BLOCKER_PREFIX`, so `fix_context.prior_blockers` lists it
    and `fix_context.operator_decision` reads a person's answer after it;
    then the same three lines, the way back, `fix_context.DECISION_EXAMPLE`,
    and the same key.

The question follows the evidence. A REQUEST_CHANGES or a Verifier FAIL
standing asks whether the finding is fixed; an APPROVE asks whether the red
check is fixed; no verdict asks whether a person reads the pull request and
says what to change. A verdict written about an earlier head is no verdict on
this one. Each offers dropping the change — closing the pull
request and canceling the card by hand, the one answer no mechanism makes,
and the cancel is what lifts the hold (`card-closed`). The merge gate reads
no Operator decision, so nothing here offers one as a way past a check.

Readers: the hygiene agent's Green Light lane, through
`hygiene_green_light.recommendation(reason, note=<the comment>)` (DRE-6196);
a person reading the card or the pull request; and DRE-3893's discovery
test (DRE-3915).
"""
from __future__ import annotations

import console_escalation
import fix_context

#: The words the sweep writes and reads (reconcile.py, DRE-5231): the key a
#: spent budget is said under, once per head, and the two budgets' tags.
#: Restated because this module imports nothing that reaches Linear; the
#: tests hold them to reconcile's own literals.
REVIEW_NUDGE_CAP_KEY = "review-nudge-cap"
GATE_NUDGE_KEY = "gate-nudge"
REVIEW_NUDGE_KEY = "review-nudge"

#: The lane a new head returns the card to (DRE-6273).
REVIEW_LANE = "In Review"

#: What every Recommendation's why ends with: the two ways to the new head
#: that brings the card back.
NEW_HEAD_WAYS = (
    "push a fix, or write an Operator decision comment on the pull request, "
    "which starts the fix agent once on your words, and the fix it pushes is "
    "that head."
)

_BUDGET = {GATE_NUDGE_KEY: "merge-gate re-trigger",
           REVIEW_NUDGE_KEY: "review re-trigger"}

_DROP = "closing the pull request and canceling the card by hand"

# What the person decides, by what stands on the head: (question, the
# sweep's pick, why).
_FINDING_STANDS = (
    "Does the finding standing on this head stand and get fixed, or is the "
    f"change dropped, {_DROP}?",
    "Fix the finding",
    "the finding stands on this head and reviewing the same head again "
    "would say it again",
)
_RED_CHECK = (
    "Is the red check holding the merge to be fixed, or is the change "
    f"dropped, {_DROP}?",
    "Fix the red check",
    "the review approved this head and only the red check stands between it "
    "and the merge",
)
_NO_VERDICT = (
    "Will a person read the pull request themselves and say what to change, "
    f"or is the change dropped, {_DROP}?",
    "Read the pull request and say what to change",
    "no review verdict is bound to this head, so a person's reading is what "
    "it lacks",
)


#: What `reconcile.standing_verdict` appends when the newest verdict names an
#: earlier commit than the head.
_EARLIER_HEAD = "from earlier head"


def _token(verdict: str | None) -> str:
    """The verdict word `reconcile.standing_verdict` leads with — APPROVE,
    REQUEST_CHANGES, PASS, FAIL, SKIP — or `none`. A verdict written about an
    earlier head is `none` here: it is not bound to this head, so it neither
    approves it nor stands a finding on it. The Finding still quotes it as
    the sweep gave it."""
    if _EARLIER_HEAD in (verdict or ""):
        return "none"
    words = (verdict or "").split()
    return words[0] if words else "none"


def _ask(critic: str, verifier: str) -> tuple:
    if _token(critic) == "REQUEST_CHANGES" or _token(verifier) == "FAIL":
        return _FINDING_STANDS
    if _token(critic) == "APPROVE":
        return _RED_CHECK
    return _NO_VERDICT


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _finding(*, head, tag, critic, verifier, spent, hours, gate_note_line,
             cap) -> str:
    budget = _BUDGET[tag]
    short = head[:7]
    if cap <= 0:
        rest = ("the gate has not merged it." if tag == GATE_NUDGE_KEY
                else "no critic verdict is bound to this head.")
        what = (f"There is no {budget} budget on head {short} because the cap "
                "is off: REVIEW_NUDGE_CAP is 0, so the sweep re-triggers "
                f"nothing, and {rest}")
    else:
        took = (f"the sweep spent {_count(spent, 're-trigger')} over "
                f"{hours:.1f}h")
        did = ("on the merge gate, and the gate declined to merge on those "
               "re-triggers." if tag == GATE_NUDGE_KEY else
               "on the review, and no critic verdict bound to this head came "
               "back.")
        what = f"The {budget} budget is spent on head {short}: {took} {did}"
    parts = [what, f"Standing on it: critic {critic}, verifier {verifier}."]
    if tag == GATE_NUDGE_KEY and (gate_note_line or "").strip():
        parts.append("The gate's newest hold note on the pull request reads "
                     f"\"{gate_note_line.strip()}\".")
    if cap > 0:
        parts.append("The sweep has stopped re-triggering, because "
                     f"{_count(cap, 'window')} on the same head is a stall, "
                     "not a slow round.")
    return " ".join(parts)


def escalation(*, card, pr_number, head, tag, critic, verifier, spent, hours,
               gate_note_line, cap) -> console_escalation.Escalation:
    """The one reading of the evidence both texts render."""
    if tag not in _BUDGET:
        raise ValueError(f"unknown re-trigger tag {tag!r}; expected "
                         f"{GATE_NUDGE_KEY!r} or {REVIEW_NUDGE_KEY!r}")
    question, pick, reason = _ask(critic, verifier)
    return console_escalation.Escalation(
        finding=_finding(head=head, tag=tag, critic=critic, verifier=verifier,
                         spent=spent, hours=hours,
                         gate_note_line=gate_note_line, cap=cap),
        question=question,
        recommendation=pick,
        why=f"{reason} — the card comes back only on a new head, so "
            f"{NEW_HEAD_WAYS}",
    )


def _way_back(card) -> str:
    return (
        f"{card} waits in Green Light, and the sweep re-triggers nothing more "
        f"on this head. It comes back to {REVIEW_LANE} on its own once a new "
        "head lands on the pull request — the fix you push, or the one the fix "
        "agent pushes on your Operator decision — and the decision alone moves "
        f"nothing. Dropping the change means {_DROP}: the cancel is what lifts "
        "the hold, and no comment on this card or the pull request does it."
    )


def _key(head: str) -> str:
    return f"{REVIEW_NUDGE_CAP_KEY} @{head}"


def compose(*, card, pr_number, head, tag, critic, verifier, spent, hours,
            gate_note_line, cap) -> str:
    """The Linear comment the review-cap park posts on the card."""
    lines = console_escalation.render(escalation(
        card=card, pr_number=pr_number, head=head, tag=tag, critic=critic,
        verifier=verifier, spent=spent, hours=hours,
        gate_note_line=gate_note_line, cap=cap))
    notice = f"🚨 {REVIEW_NUDGE_CAP_KEY} PR #{pr_number} @{head}:"
    return "\n\n".join((notice, lines, _way_back(card), _key(head)))


def pr_blocker(*, card, pr_number, head, tag, critic, verifier, spent, hours,
               gate_note_line, cap) -> str:
    """The fix-loop blocker the same park posts on the pull request."""
    lines = console_escalation.render(escalation(
        card=card, pr_number=pr_number, head=head, tag=tag, critic=critic,
        verifier=verifier, spent=spent, hours=hours,
        gate_note_line=gate_note_line, cap=cap))
    answer = ("To answer here, comment on this pull request opening with: "
              f"{fix_context.DECISION_EXAMPLE}")
    blocker = (f"{fix_context.BLOCKER_PREFIX} {REVIEW_NUDGE_CAP_KEY} "
               f"PR #{pr_number} @{head}:")
    return "\n\n".join((blocker, lines, _way_back(card), answer, _key(head)))
