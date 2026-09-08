#!/usr/bin/env python3
"""Is the fix loop getting somewhere? (DRE-2817)

THE DEFECT, stated as narrowly as it can be: the fix budget counted attempts
when it should have measured convergence. Three attempts and stop, whether
the loop was closing in or going round in circles.

PR #199 (DRE-2721, 2026-08-29) was closing in. Four review rounds, four
DIFFERENT real findings, and the critic said so itself on round four:

    "This is the fourth round of review on this change. The three earlier
    rounds each found a real way the review-tracking record could be
    tampered with… and each time the team fixed it and added tests proving
    the fix holds. Those three fixes are solid and verified."

    "The part I'm blocking on is new to this round."

Nothing re-found, nothing regressed, no scope creep. That is the signature
of a loop closing in, and it is exactly what an attempt counter cannot see.
The cap fired, the PR went to a human hold overnight, and four separate
defects in the hold's exit path (DRE-2810, DRE-2813) ate most of the next
day. Fourteen hours after the last real defect was found.

THREE IS NOT WRONG, IT IS THE WRONG QUESTION. A fix that needs a fourth
swing usually does mean the agent has not understood the problem, and that
reasoning is kept: the ceiling below is still there and a genuinely stuck
loop now stops SOONER than it used to. What changed is what is measured.
Raising the cap to four or five was considered and rejected — it trades one
arbitrary number for another and would merely have delayed this.

WHERE THE JUDGEMENT COMES FROM, and why not from the obvious place. The
fixing agent cannot be asked whether it is making progress: the doer
asserting it is getting somewhere is precisely the claim the critic exists
to check. So the classification is read off the CRITIC's verdict, as one
machine-readable line the critic writes on a re-review (both prompts in
qa-review.yml; the format is `MARKER` + one token per axis below):

    convergence: new-finding prior-fixes-held in-scope

Three axes, because those are the three things the critic already had to
decide in order to write the verdict at all:

  * is the finding NEW, or one an earlier round already named
  * did the previous fixes HOLD, or has one regressed
  * is the diff still IN SCOPE

New + held + in-scope is progress. Anything else is circling.

FAIL-CLOSED, AND WHICH DIRECTION THAT IS. A round whose verdict carries no
readable line is NOT converging. It is not accused of circling either — the
reason it records is `unclassified` — but it cannot be read as progress,
because the whole point is that the claim comes from the verdict. That is
also the compatible direction: a fleet still on an older release cuts
verdicts with no line, and a loop of unreadable rounds behaves as the old
counter did rather than running to the ceiling on nobody's word.

The FIRST review is converging by construction: there is no earlier round
for it to repeat and no earlier fix for it to have regressed.

THE BUDGET, in one sentence: stop after `STOP_BUDGET` CONSECUTIVE
non-converging rounds, with `CEILING` total attempts as the runaway
backstop. Consecutive matters — one round of real progress resets it,
which is what lets a long-but-converging loop finish.

WHAT THIS MODULE DOES NOT TOUCH. The CONFLICT budget (five rounds, counted
by attempt) is a different failure class and stays exactly as it was: main
moving under a branch is not a question about convergence, and the two
budgets have been deliberately separate since PR #13.

Read by scripts/fix_budget.py, which owns the decision and the CLI; this
module is pure and has no I/O.
"""

from __future__ import annotations

import re

from fix_concurrency import QA_BOT_LOGIN
from merge_gate import verdict_sha
from verdict_cause import verdict_decision

#: Who may write a verdict. Imported rather than re-typed: agent-fix.yml
#: mints only the worker App's token, so nothing in the fix loop can derive
#: the critic's login from an app-slug, and DRE-2120's roster covers the one
#: place the literal lives (fix_concurrency.py, whose rename procedure names
#: agent-fix.yml).
CRITIC_LOGIN = QA_BOT_LOGIN

#: The stop budget: CONSECUTIVE non-converging rounds. Two, not three —
#: a loop that re-finds a defect it has already been told about twice in a
#: row is circling, and the card is explicit that circling should stop
#: sooner than the retired cap, not later.
STOP_BUDGET = 2

#: The runaway backstop, in total fix attempts. Generous on purpose: #199
#: needed four and would have got there, so a ceiling that stops it is the
#: same defect with a bigger number. Six is twice the retired cap, and
#: hitting it is REPORTED DIFFERENTLY from stopping on non-convergence — a
#: loop that reaches here has been producing new work all along and never
#: finishing, which is a different thing for a human to look at.
CEILING = 6

#: The machine-readable line, and the structured position it is read in.
MARKER = "convergence:"

#: One token per axis, in this order, and nothing else on the line. The
#: FIRST token of each pair is the converging answer.
AXES = (
    ("new-finding", "repeat-finding"),
    ("prior-fixes-held", "prior-fix-regressed"),
    ("in-scope", "scope-creep"),
)
CONVERGING_TOKENS = tuple(axis[0] for axis in AXES)

#: The two reasons that are not a token: a first review, and a re-review
#: whose verdict said nothing this module can read.
FIRST_REVIEW = "first-review"
PROGRESS = "progress"
UNCLASSIFIED = "unclassified"

#: Plain English for each reason, for the receipt a person reads. No code,
#: no file paths, no jargon — this line lands on the pull request and, when
#: it stops the loop, in front of whoever is asked to decide.
REASONS = {
    FIRST_REVIEW: "this is the first review, so there is nothing earlier to "
                  "repeat and no earlier fix to have come undone",
    PROGRESS: "the finding is new, every earlier fix still holds, and the "
              "change is still what the card asked for",
    "repeat-finding": "it re-finds a problem an earlier round already named",
    "prior-fix-regressed": "it reports that a fix from an earlier round has "
                           "come undone",
    "scope-creep": "the change has grown past what the card asked for",
    UNCLASSIFIED: "the review did not say whether this round is new ground "
                  "or a repeat, so it cannot be read as progress",
}

#: What the receipt line opens with. Distinct from `fix-convergence-halt`
#: (DRE-2024, a different refusal counted by its own key) — this string is
#: not a substring of that one and that one is not a substring of this.
RECEIPT_TAG = "📊 fix-convergence"

#: The stop names, as they appear in the sourced env and in the receipt.
NON_CONVERGENCE = "non-convergence"
RUNAWAY = "ceiling"

#: A heading ends the header region. The line is read ABOVE the first one
#: and nowhere else: the critic writes the middle of a verdict having just
#: read a diff anyone can author, and a `convergence:` sentence quoted into
#: the findings must not be able to buy the loop another round
#: (verdict_cause.py's structured-position rule, verdict_content.py's
#: end-anchor — the same discipline).
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_LINE_RE = re.compile(r"^\s{0,3}" + re.escape(MARKER) + r"\s*(.+?)\s*$", re.M)

#: The DRE-2466 stub: a verdict file the review never finished rewriting.
#: The workflow's gates turn it into a neutral hold rather than a rejection,
#: so it is not a round here either.
_INCOMPLETE = "QA-REVIEW-INCOMPLETE"


def _header(body: str) -> str:
    """Everything above the verdict's first markdown heading."""
    out = []
    for line in (body or "").splitlines():
        if _HEADING_RE.match(line):
            break
        out.append(line)
    return "\n".join(out)


def read(body):
    """The three tokens a verdict declares, or None when it declares none.

    None is the answer for a pre-DRE-2817 verdict, for a first review, for a
    line with the wrong number of tokens, for a token outside its axis, and
    for tokens out of axis order. Every one of those reads as "said nothing"
    rather than as a fourth category — a fork shows up as unclassified
    rounds, which are visible and countable, instead of as a silent new
    meaning (verdict_cause.py's fail-closed rule).
    """
    m = _LINE_RE.search(_header(body or ""))
    if not m:
        return None
    tokens = tuple(m.group(1).lower().split())
    if len(tokens) != len(AXES):
        return None
    if any(token not in axis for token, axis in zip(tokens, AXES)):
        return None
    return tokens


def _verdict_word(body: str):
    for line in (body or "").splitlines():
        if "VERDICT:" in line:
            return verdict_decision(line)
    return None


def is_round(comment: dict, critic_login: str = CRITIC_LOGIN) -> bool:
    """Is this comment a REVIEW ROUND — a blocking verdict the fix loop was
    dispatched to answer?

    Author-gated (DRE-1988/1995: authorship decides meaning). A planted
    verdict must be able neither to keep a circling loop running nor to stop
    a healthy one. An APPROVE is not a round (nothing was asked of the fix
    loop) and neither is a neutral hold, which carries the `QA Critic`
    marker and deliberately no `VERDICT:` line.
    """
    if (comment.get("user") or {}).get("login") != critic_login:
        return False
    body = comment.get("body") or ""
    if "QA Critic" not in body or _INCOMPLETE in body:
        return False
    return _verdict_word(body) == "REQUEST_CHANGES"


class Round:
    """One review round, and whether it moved the work forward."""

    def __init__(self, index: int, converging: bool, reason: str):
        self.index = index          # 1-based, oldest first
        self.converging = converging
        self.reason = reason        # a key of REASONS

    def __eq__(self, other) -> bool:  # so a test can compare lists
        return (isinstance(other, Round)
                and (self.index, self.converging, self.reason)
                == (other.index, other.converging, other.reason))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Round({self.index}, {self.converging}, {self.reason!r})"


def classify(body: str, first: bool) -> tuple:
    """(converging, reason) for one verdict body."""
    if first:
        return True, FIRST_REVIEW
    tokens = read(body)
    if tokens is None:
        return False, UNCLASSIFIED
    if tokens == CONVERGING_TOKENS:
        return True, PROGRESS
    # The reason IS the first token that differs from the converging answer,
    # so the vocabulary needs no second list to drift from.
    reason = next(got for got, want in zip(tokens, CONVERGING_TOKENS)
                  if got != want)
    return False, reason


def round_bodies(comments, critic_login: str = CRITIC_LOGIN) -> list:
    """The blocking verdicts on this thread, ONE PER COMMIT, oldest first.

    A round is a review OF A COMMIT, so two verdicts bound to the same head
    are one round re-stated and the newest statement is the one that stands.
    The fix loop really does produce that pair: a refuted finding
    (DRE-3084) dispatches a fresh review of the same head without spending
    an attempt, and counting the answer as a second round would spend
    budget on a round the loop was never given a chance to fix.

    A verdict carrying no readable `@<sha>` is never folded into another:
    an unreadable binding is not evidence of sameness (merge_gate's rule,
    read through its own parser so the two cannot disagree about where a
    verdict's sha is).
    """
    out: list = []
    for comment in comments:
        if not is_round(comment, critic_login):
            continue
        body = comment.get("body") or ""
        # The header line only — a 40-hex string quoted in the findings is
        # not this verdict's binding.
        sha = verdict_sha(body.splitlines()[0] if body else "")
        at = next((i for i in range(len(out) - 1, -1, -1)
                   if sha and out[i][0] == sha), None)
        if at is None:
            out.append((sha, body))
        else:
            out[at] = (sha, body)
    return [body for _sha, body in out]


def rounds(comments, critic_login: str = CRITIC_LOGIN, attempts: int = 0) -> list:
    """Every review round on this thread, oldest first.

    `attempts` PADS: an attempt with no verdict behind it gets an
    unclassified slot. The live loop keeps rounds and attempts in step, so
    this is the defensive edge rather than the normal case — but "three
    attempts and no verdict anywhere" must read as three unproven rounds,
    not as an empty history that buys the loop a fresh start.
    """
    out = []
    for i, body in enumerate(round_bodies(comments, critic_login)):
        converging, reason = classify(body, first=(i == 0))
        out.append(Round(i + 1, converging, reason))
    for i in range(len(out), attempts):
        out.append(Round(i + 1, False, UNCLASSIFIED))
    return out


def streak(rounds_: list) -> int:
    """CONSECUTIVE non-converging rounds at the tail. One round of real
    progress resets it — that is what lets a long-but-converging loop
    finish, and it is the whole difference from a counter."""
    count = 0
    for round_ in reversed(rounds_):
        if round_.converging:
            break
        count += 1
    return count


class State:
    """The convergence answer for one pull request, at one moment."""

    def __init__(self, rounds_: list, attempts: int):
        self.rounds = rounds_
        self.attempts = attempts
        self.streak = streak(rounds_)
        # The ceiling is read FIRST: it is the unconditional limit, and a
        # loop that got this far was converging round after round, which is
        # a different thing for a human to look at than a stuck one.
        if attempts >= CEILING:
            self.stopped_by = RUNAWAY
        elif self.streak >= STOP_BUDGET:
            self.stopped_by = NON_CONVERGENCE
        else:
            self.stopped_by = None

    @property
    def latest(self):
        return self.rounds[-1] if self.rounds else None

    def receipt(self) -> str:
        """The one line the pull request carries every round, so "why did
        this stop" is answerable without reading four verdicts.

        ONE LINE, and free of `"`, `$`, backtick and backslash: it is
        interpolated into a double-quoted `gh pr comment --body` in
        agent-fix.yml (the fix_context.DECISION_EXAMPLE discipline), and it
        carries no verdict-shaped text — verdict markers are an approval
        credential and only the critic may emit one
        (standards/untrusted-content.md).
        """
        tail = (f"Non-converging rounds in a row: {self.streak} of "
                f"{STOP_BUDGET}. Attempts so far: {self.attempts} of a "
                f"{CEILING}-attempt ceiling.")
        latest = self.latest
        if latest is None:
            return (f"{RECEIPT_TAG}: no review round has landed here yet, so "
                    f"there is nothing to measure. {tail}")
        verdict = "CONVERGING" if latest.converging else "NOT CONVERGING"
        return (f"{RECEIPT_TAG}: round {latest.index} is {verdict} — "
                f"{REASONS[latest.reason]}. {tail}")

    def hold_reason(self) -> str:
        """Why the loop stopped, in the words the hold comment uses. The two
        stops read differently on purpose (AC4): the human needs to know
        which one happened, because the remedies are not the same."""
        if self.stopped_by == RUNAWAY:
            return (f"it reached the hard ceiling of {CEILING} fix attempts. "
                    "That is the runaway backstop, not a judgement that the "
                    "loop stopped making progress — it kept finding new work "
                    "and never finished")
        return (f"{self.streak} review rounds in a row made no progress "
                f"(the stop budget is {STOP_BUDGET}). A round that finds "
                "something new, leaves the earlier fixes working and stays "
                "in scope does not spend that budget; this one did")


def state(comments, attempts: int = 0,
          critic_login: str = CRITIC_LOGIN) -> State:
    """The whole reading, from the thread and the attempts already made."""
    return State(rounds(comments, critic_login, attempts), attempts)
