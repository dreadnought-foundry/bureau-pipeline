#!/usr/bin/env python3
"""The question a spent review budget asks (DRE-6189, DRE-6841).

When the review lane's re-trigger budget is spent on a head (REVIEW_NUDGE_CAP,
DRE-5231), the sweep parks the card in Green Light (DRE-6181) — or, for a
proof record, holds it In Review (below) — and posts the
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

A proof record (`agent/DRE-<n>-proof-record`) is never parked: the sweep
holds it In Review for the operator (DRE-6841), and asks through the variant
— `compose_proof_record(...)` and `pr_blocker_proof_record(...)`, the same
evidence, the same notice, blocker line and key, its own three lines. The
gate's refusal of a record is "proof record not proven", which is the
operator's to answer, so the variant words two cases apart:

  * the gate declined the record at the head as not proven
    (`declined_not_proven`): the Finding quotes the note and asks whether to
    accept the unmet rows by operator decision, record the event with
    `🔬 proof-observed`, or drop the record;
  * any other way to the cap — no critic verdict at the head, a decline for
    another reason, a verdict the carry cannot bind: there is no row to
    accept, so it asks whether to start the review again by hand, push the
    record to a new head, or drop it.

Neither asks a person to read the change, and neither names the CEO.

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


def _blocker(lines, way_back, pr_number, head) -> str:
    answer = ("To answer here, comment on this pull request opening with: "
              f"{fix_context.DECISION_EXAMPLE}")
    blocker = (f"{fix_context.BLOCKER_PREFIX} {REVIEW_NUDGE_CAP_KEY} "
               f"PR #{pr_number} @{head}:")
    return "\n\n".join((blocker, lines, way_back, answer, _key(head)))


def pr_blocker(*, card, pr_number, head, tag, critic, verifier, spent, hours,
               gate_note_line, cap) -> str:
    """The fix-loop blocker the same park posts on the pull request."""
    lines = console_escalation.render(escalation(
        card=card, pr_number=pr_number, head=head, tag=tag, critic=critic,
        verifier=verifier, spent=spent, hours=hours,
        gate_note_line=gate_note_line, cap=cap))
    return _blocker(lines, _way_back(card), pr_number, head)


# --------------------------------------------------------------------------- #
# the proof-record variant (DRE-6841)                                          #
# --------------------------------------------------------------------------- #

#: What the gate's decline reason opens with when a proof record's unmet rows
#: are what hold it — `proof_dispatch.NOT_PROVEN`, restated for the reason the
#: keys above are; the tests hold the two equal.
NOT_PROVEN = "proof record not proven:"

#: The gate's hold note up to the head it names, and what follows the head.
_DECLINED_AT = "Merge gate: declined @"
_REASON_AFTER = " — "

_DROP_RECORD = "closing the pull request and canceling the card by hand"

# What the operator decides about a proof record at the cap: (question, the
# sweep's pick, why). The two questions are the card's contract, word for word.
_NOT_PROVEN_ROWS = (
    "Accept the unmet rows by operator decision, record the event with "
    "proof-observed, or drop the record?",
    "Record the event with proof-observed",
    "the gate declined the record only because rows are unmet, and an "
    "observed event proves a row where an operator decision only accepts it; "
    "accept the rows when the event cannot happen, and drop the record when "
    "it should not ship",
)
_START_AGAIN = (
    "Start the review again by hand, push the record to a new head, or drop "
    "the record?",
    "Start the review again by hand",
    "the budget was spent on reviews that never answered on this head, so "
    "there is no unmet row to accept, and one review started by hand is the "
    "shortest way to an answer",
)
_PUSH_AGAIN = (
    _START_AGAIN[0],
    "Push the record to a new head",
    "the gate declined this head for a reason other than unmet rows, and a "
    "new head is what the gate and the review read again",
)


def declined_not_proven(gate_note_line: str | None, head: str) -> bool:
    """Is `gate_note_line` the gate declining the record AT `head` as not
    proven — `Merge gate: declined @<head> — proof record not proven: …`?
    A decline at an earlier head says nothing about this one."""
    _, found, rest = (gate_note_line or "").partition(_DECLINED_AT)
    sha, after, reason = rest.partition(_REASON_AFTER)
    return bool(found and after and head) and sha.strip() == head \
        and reason.startswith(NOT_PROVEN)


def _ask_record(*, head, critic, gate_note_line) -> tuple:
    if declined_not_proven(gate_note_line, head):
        return _NOT_PROVEN_ROWS
    if _token(critic) == "none":
        return _START_AGAIN
    return _PUSH_AGAIN


def proof_record_escalation(*, card, pr_number, head, tag, critic, verifier,
                            spent, hours, gate_note_line,
                            cap) -> console_escalation.Escalation:
    """The one reading of a proof record's evidence both its texts render."""
    if tag not in _BUDGET:
        raise ValueError(f"unknown re-trigger tag {tag!r}; expected "
                         f"{GATE_NUDGE_KEY!r} or {REVIEW_NUDGE_KEY!r}")
    question, pick, reason = _ask_record(head=head, critic=critic,
                                         gate_note_line=gate_note_line)
    parts = [f"Pull request #{pr_number} is a proof record."]
    if _token(critic) == "none":
        parts.append(f"There is no critic verdict at {head[:7]}.")
    parts.append(_finding(head=head, tag=tag, critic=critic, verifier=verifier,
                          spent=spent, hours=hours,
                          gate_note_line=gate_note_line, cap=cap))
    return console_escalation.Escalation(
        finding=" ".join(parts),
        question=question,
        recommendation=pick,
        why=f"{reason} — the hold lifts only on a new head of the record, "
            f"and {card} stays {REVIEW_LANE} until then",
    )


def _way_back_record(card) -> str:
    return (
        f"{card} is held In Review for the operator, and the sweep "
        "re-triggers nothing more on this head. The hold lifts on its own "
        "once a new head lands on the pull request — a row accepted or "
        "reworded and pushed — and no comment on this card or the pull "
        f"request lifts it. Dropping the record means {_DROP_RECORD}: the "
        "cancel is what lifts the hold."
    )


def compose_proof_record(*, card, pr_number, head, tag, critic, verifier,
                         spent, hours, gate_note_line, cap) -> str:
    """The Linear comment the review cap posts on a proof record's card,
    which it holds In Review instead of parking (DRE-6841)."""
    lines = console_escalation.render(proof_record_escalation(
        card=card, pr_number=pr_number, head=head, tag=tag, critic=critic,
        verifier=verifier, spent=spent, hours=hours,
        gate_note_line=gate_note_line, cap=cap))
    notice = f"🚨 {REVIEW_NUDGE_CAP_KEY} PR #{pr_number} @{head}:"
    return "\n\n".join((notice, lines, _way_back_record(card), _key(head)))


def pr_blocker_proof_record(*, card, pr_number, head, tag, critic, verifier,
                            spent, hours, gate_note_line, cap) -> str:
    """The fix-loop blocker the same hold posts on the proof record's pull
    request, so a person's Operator decision has a blocker to answer."""
    lines = console_escalation.render(proof_record_escalation(
        card=card, pr_number=pr_number, head=head, tag=tag, critic=critic,
        verifier=verifier, spent=spent, hours=hours,
        gate_note_line=gate_note_line, cap=cap))
    return _blocker(lines, _way_back_record(card), pr_number, head)
