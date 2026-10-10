#!/usr/bin/env python3
"""The plan-critic bound is tried without a person first (DRE-6452, epic DRE-6415).

Every bound site in `plan.yml` — the six epic park steps stamping
`plan-critic-bound`, and `One-off critic — park in Triage` — did the same
three things: hold, move to Triage, post the 🛑 note. The card then had no
owner and no next step. DRE-4059 reached the second critic's bound at
2026-10-08 20:22 PT and its seven planned children sat in Backlog until an
operator moved it back by hand at 11:23 PT the next day; DRE-6234 reached the
one-off critic's third send-back at 19:08 PT and waited for an operator to
rewrite it by hand. This module decides what a bound does BEFORE anything
parks the card, and the step that called it parks exactly as today only when
it answers `park`.

## What it reads

The thread is `linear_ops.comment_records(card, whole_thread=True)` — the rows
`dump-comments <CARD> --with-authors` prints — so a marker from any other
author is excluded exactly as `plan_critic.decide` excludes it. The findings
are read with the pipeline's own parsers and never re-derived:
`plan_critic.send_back_findings` returns ONE finding per send-back round, the
worst, read off the round's `plan-critic:` marker. The rest of a round's
findings live in the 🛑 note beside it, which is prose and records nothing,
so nothing here reads them.

  * `pre` / `post` — both stages of the CURRENT planning attempt
    (`plan_critic.current_cycle`, scoped to the newest `plan-cycle: start`
    naming this card), deduplicated with `plan_critic.every_finding_so_far`.
  * `one-off` — the whole trusted thread, because the one-off route never
    posts a boundary; deduplicated the same way, so a round that re-raised a
    finding does not ask the CEO the same thing twice.

## How it decides (`decide`, the pure core)

Each finding is classified with `send_back_class.classify` — REVISION,
DECISION, or None for a finding neither of DRE-6356's phrase lists places.
What an uncertain finding does is this module's one rule of its own,
`UNCERTAIN_BY_STAGE`, with its reason beside it.

  * `question` — at least one finding is DECISION after the stage rule. The
    CEO gets one Green Light question through `planning_escalation.escalate`,
    the ONE declared writer of that row. The question is refused here first
    (`planning_escalation.refusal`): `escalate` never refuses a text, it parks
    the card with `NOT_PLAIN_ENGLISH` and nothing to answer. A refused
    question takes the rewrite when it is still owed — the planner is the one
    reader that can turn a choice written in code into a question the CEO can
    read — and parks when it is spent.
  * `rewrite` — every finding is REVISION, or uncertain on an epic stage, and
    no `🔁 bound-rewrite` receipt the pipeline wrote stands anywhere on the
    card. A fresh planning run is dispatched (`review_rerun._cmd_dispatch`,
    `--route plan --trigger-state planning --reason bound-rewrite`, carrying
    this run's id as `sent_by_run`, DRE-4573) and the receipt is posted. Once
    per card, never once per attempt.
  * `park` — no findings, or the rewrite already granted. Nothing is written;
    the caller's park note names every finding, and the hygiene lane
    (DRE-6451) names the parked card and alarms on it once.

## The receipt is written FROM the dispatch (DRE-2034)

The dispatch goes first and the receipt only on its confirmed return code — the
order every dispatch site in `plan.yml` keeps. A failed dispatch posts nothing
and exits 3, so the caller parks. The run it starts reads the receipt minutes
later (its route step, its critic, its planner), long after the comment has
landed. A receipt that cannot be posted after a confirmed dispatch is a crash:
the caller parks, and the dispatched run finds the card out of Planning and
stands down (`dedupe_dispatch.lane_left_behind`), so no planner works a card
whose grant was never recorded.

## CLI

    plan_bound.py exit <CARD> --stage pre|post|one-off --repo OWNER/NAME
        exit 0 = handled (question asked, or rewrite dispatched)
        exit 3 = park: the caller does today's hold + Triage + note
        any other exit = a crash; the caller parks as today
    plan_bound.py read <CARD> --stage pre|post|one-off [--whole-thread]
        writes nothing: the outcome word, then <class>\\t<why>\\t<finding>
    plan_bound.py context <CARD>
        the planner's section: the findings the newest receipt left open
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import console_escalation  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import plan_critic  # noqa: E402
import planning_escalation  # noqa: E402
import review_rerun  # noqa: E402
import send_back_class  # noqa: E402
from send_back_class import DECISION, REVISION  # noqa: E402

QUESTION = "question"
REWRITE = "rewrite"
PARK = "park"

EXIT_HANDLED = 0
EXIT_PARK = 3
EXIT_CRASH = 1

STAGES = (plan_critic.STAGE_PRE, plan_critic.STAGE_POST,
          plan_critic.STAGE_ONE_OFF)

#: What an uncertain finding — one `classify` returns None for — is on each
#: stage. On the one-off route it is DRE-6356's own collapse (`route_class`,
#: `UNCERTAIN_GOES_TO`): the one-off critic writes for the CEO in plain
#: English, and the classifier's lists were read off exactly those findings.
#: On the epic route the critics write for the PLANNER, in engineer prose —
#: DRE-4059's round 2 names a workflow output and its round 3 a function — and
#: the classifier places none of DRE-4059's three second-critic findings, so
#: collapsing them to the CEO would send every epic bound to him as a question
#: written in code, which `planning_escalation.refusal` then refuses, and the
#: card would park exactly as today under a new name. The planner can act on
#: engineer prose: it re-plans with every finding in front of it, and when one
#: is really a business choice it asks the CEO through its own escalation exit,
#: in plain English and with a recommendation (`briefs/planner.md`).
UNCERTAIN_BY_STAGE = {
    "one-off": DECISION,
    "pre": REVISION,
    "post": REVISION,
}

#: `classify`'s None, in the word DRE-6356's CLI prints for it.
UNCERTAIN = "uncertain"

#: How many decision-class findings the question's Finding lines carry.
MAX_FINDING_LINES = 5

# --- the question ------------------------------------------------------------

QUESTION_TEXT = ("Which way should {card} go on the choice the critic named, "
                 "so the planner can write it in and plan again?")
NO_SIDE = "the critic names the choice, not a side"
ANSWER_EFFECT = ("Answer the question here and send the card back to "
                 "Planning; the planner re-plans with your answer in front of it")
DROP_EFFECT = "Close the card; nothing is built"

# --- the receipt -------------------------------------------------------------

#: The act's idempotency key: a pipeline-authored comment opening with
#: `RECEIPT_KEY` anywhere on the card means the rewrite has been granted.
BOUND_REWRITE_TAG = "bound-rewrite"
RECEIPT_MARK = "🔁"
RECEIPT_KEY = f"{RECEIPT_MARK} {BOUND_REWRITE_TAG}:"

RECEIPT_OPENING = (
    f"{RECEIPT_KEY} the critics held this plan at their bound, and before "
    "anyone parks it the planner gets one more rewrite with the worst finding "
    "of every round in front of it — a fresh planning attempt starts now. This "
    "happens once per card. The findings, oldest first:"
)
REFUSED_SENTENCE = (
    "One finding names a choice, and it was written in terms the CEO does not "
    "read: the planner asks him in plain English through its own escalation "
    "exit, or settles it in the plan."
)
RECEIPT_CLOSING = (
    "If the critics hold it again, the card parks in Triage for an operator."
)

#: The planning run the rewrite asks for. The route step reads any reason
#: outside its review asks as plan mode, writes Planning (a no-op) and opens a
#: fresh attempt, so both critics count from zero.
DISPATCH_ROUTE = "plan"
DISPATCH_REASON = BOUND_REWRITE_TAG
DISPATCH_TRIGGER_STATE = review_rerun.TRIGGER_STATE_PLANNING

# --- the planner's section ----------------------------------------------------

CONTEXT_HEADING = "## Findings the critics left open on this card"
CONTEXT_GRANTED = ("BOUND STATUS: rewrite granted at {at} — answer every "
                   "finding below before anything else")
CONTEXT_NONE = "BOUND STATUS: none — no bound-rewrite receipt on this card"
CONTEXT_CHOICE_LINE = ("One of these is a choice: ask the CEO in plain English "
                       "through the escalation exit, or settle it in the plan.")

_NUMBERED = re.compile(
    rf"^(?P<n>\d+)\. \((?P<cls>{REVISION}|{DECISION}|{UNCERTAIN})\) (?P<text>.+)$")


# --------------------------------------------------------------------------- #
# reading                                                                      #
# --------------------------------------------------------------------------- #


def findings(records, card: str, stage: str, *,
             whole_thread: bool = False) -> list[str]:
    """The worst finding of every send-back round, oldest first, each once.

    `whole_thread` reads every planning attempt instead of the current one —
    `read`'s flag for a record the newest boundary hides; `exit` never sets it,
    because the bound belongs to one attempt. The one-off route reads the whole
    thread either way: it never posts a boundary.
    """
    if stage == plan_critic.STAGE_ONE_OFF:
        found = plan_critic.send_back_findings(
            plan_critic.trusted_bodies(records), stage)
        return plan_critic.every_finding_so_far(found, [])
    bodies = (plan_critic.trusted_bodies(records) if whole_thread
              else plan_critic.current_cycle(records, card))
    return plan_critic.every_finding_so_far(
        plan_critic.send_back_findings(bodies, plan_critic.STAGE_PRE),
        plan_critic.send_back_findings(bodies, plan_critic.STAGE_POST))


def _body(entry) -> str:
    return (entry.get("body") if isinstance(entry, dict) else entry) or ""


def _is_receipt(entry) -> bool:
    """A comment the pipeline wrote whose body opens with the receipt's key.

    Its opening only, so a receipt quoted inside somebody's comment grants
    nothing, and `trusted_bodies`' rule for who wrote it, so a receipt-shaped
    comment from another author grants nothing either.
    """
    if isinstance(entry, dict) and not entry.get("authored_by_pipeline"):
        return False
    return _body(entry).lstrip().startswith(RECEIPT_KEY)


def granted(records) -> bool:
    """Has this card had its one rewrite? Read off the WHOLE thread: once per
    card, never once per attempt."""
    return any(_is_receipt(entry) for entry in records or [])


def class_word(finding: str) -> str:
    """`classify`'s own reading, as DRE-6356's CLI prints it."""
    return send_back_class.classify(finding) or UNCERTAIN


def stage_class(finding: str, stage: str) -> str:
    """REVISION or DECISION: `classify`, with an uncertain finding read by the
    stage rule. On `one-off` this is `send_back_class.route_class`."""
    cls = send_back_class.classify(finding)
    return UNCERTAIN_BY_STAGE[stage] if cls is None else cls


def decision_findings(found: list[str], stage: str) -> list[str]:
    return [f for f in found if stage_class(f, stage) == DECISION]


def decide(found: list[str], is_granted: bool, stage: str) -> tuple[str, str]:
    """`(outcome, reason)` — `question`, `rewrite` or `park`, and why in one line.

    Pure: no reads, no writes. Whether a question's text is fit to show is
    `exit`'s check, made on the reason it composes, never here.
    """
    if stage not in UNCERTAIN_BY_STAGE:
        raise ValueError(f"unknown stage {stage!r} — one of {', '.join(STAGES)}")
    if not found:
        return PARK, ("no send-back round on this attempt recorded a finding — "
                      "a critic that died or decided nothing leaves nothing to "
                      "ask or to rewrite")
    decisions = decision_findings(found, stage)
    if decisions:
        return QUESTION, (f"{len(decisions)} of {len(found)} finding(s) name a "
                          f"choice nobody has made, read on the {stage} stage")
    if is_granted:
        return PARK, ("the planner already had its one bound rewrite on this "
                      "card, and the critics held it again")
    return REWRITE, (f"all {len(found)} finding(s) are for the planner to "
                     f"answer, read on the {stage} stage, and the card has not "
                     "had its one bound rewrite")


# --------------------------------------------------------------------------- #
# the words                                                                    #
# --------------------------------------------------------------------------- #


def _flat(text: str) -> str:
    return " ".join(str(text).split())


def question_reason(card: str, decisions: list[str]) -> str:
    """The Finding, Question and Recommendation lines, prefixes from
    `console_escalation`: one Finding line per decision-class finding,
    verbatim, oldest first, at most `MAX_FINDING_LINES`."""
    lines = [f"{console_escalation.FINDING_PREFIX} {_flat(f)}"
             for f in decisions[:MAX_FINDING_LINES]]
    lines.append(f"{console_escalation.QUESTION_PREFIX} "
                 f"{QUESTION_TEXT.format(card=card)}")
    lines.append(f"{console_escalation.RECOMMENDATION_PREFIX} "
                 f"{console_escalation.NONE_GIVEN}{console_escalation.SEPARATOR}"
                 f"{NO_SIDE}")
    return "\n".join(lines)


def question_choices(card: str, decisions: list[str]) -> dict:
    """The question as buttons, in DRE-6168's shape."""
    return {
        "question": QUESTION_TEXT.format(card=card),
        "context": " ".join(_flat(f) for f in decisions[:MAX_FINDING_LINES]),
        "choices": [
            {"id": "answer", "label": "Answer it and re-plan",
             "effect": ANSWER_EFFECT, "outcome": "replan"},
            {"id": "drop", "label": "Drop the card",
             "effect": DROP_EFFECT, "outcome": "close"},
        ],
        "recommended": "answer",
        "why": f"{NO_SIDE}, so answering it is how the card moves",
    }


def receipt_body(found: list[str], refused: bool = False) -> str:
    """The receipt's text before its trailer: every finding, numbered, each
    with `classify`'s own word in parentheses."""
    parts = [RECEIPT_OPENING]
    if refused:
        parts.append(REFUSED_SENTENCE)
    parts.append("\n".join(f"{n}. ({class_word(f)}) {_flat(f)}"
                           for n, f in enumerate(found, 1)))
    parts.append(RECEIPT_CLOSING)
    return "\n\n".join(parts)


def context_section(records) -> str:
    """The section a fresh planning attempt reads, off the newest receipt."""
    receipts = [entry for entry in records or [] if _is_receipt(entry)]
    lines = [CONTEXT_HEADING, ""]
    if not receipts:
        lines.append(CONTEXT_NONE)
        return "\n".join(lines)
    newest = receipts[-1]
    at = (newest.get("created_at") if isinstance(newest, dict) else None)
    lines.append(CONTEXT_GRANTED.format(at=at or "an unrecorded time"))
    lines.append("")
    body = _body(newest)
    for line in body.splitlines():
        if _NUMBERED.match(line.strip()):
            lines.append(line.strip())
    if REFUSED_SENTENCE in body:
        lines += ["", CONTEXT_CHOICE_LINE]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the writes                                                                   #
# --------------------------------------------------------------------------- #


def _question(card: str, reason: str, choices: dict) -> int:
    """The one function that calls `escalate` — declared in the lane contract
    as a caller of the Green Light `question` arrival. No hold: a card in
    Green Light is the CEO's, and the sweep must not skip it."""
    outcome = planning_escalation.escalate(linear_ops, card, reason,
                                           choices=choices)
    if outcome.parked:
        print(f"{card}: asked the CEO one question in Green Light")
        return EXIT_HANDLED
    print(f"{card}: the question stood down — {outcome.stood_down}. Exit 3: "
          "the caller parks as today")
    return EXIT_PARK


def _rewrite(card: str, repo: str, found: list[str], refused: bool = False) -> int:
    """Dispatch the fresh planning run, then post the receipt — FROM the
    dispatch's return code (DRE-2034)."""
    rc = review_rerun._cmd_dispatch(argparse.Namespace(
        epic=card, repo=repo, route=DISPATCH_ROUTE,
        trigger_state=DISPATCH_TRIGGER_STATE, reason=DISPATCH_REASON))
    if rc != 0:
        print(f"{card}: the rewrite's planning run could not be dispatched, so "
              "no receipt is posted. Exit 3: the caller parks as today")
        return EXIT_PARK
    body = pipeline_act.receipt("plan-bound-rewrite", receipt_body(found, refused))
    try:
        problem = linear_ops.cmd_comment(card, body)
    except Exception as exc:  # noqa: BLE001 — named below, and a crash
        problem = str(exc) or type(exc).__name__
    if problem:
        print(f"ERROR: {card}: the rewrite's planning run was dispatched and "
              f"its receipt could not be posted ({problem}). The caller parks, "
              "and the dispatched run finds the card out of Planning and "
              "stands down", file=sys.stderr)
        return EXIT_CRASH
    print(f"{card}: granted the planner its one bound rewrite — a fresh "
          "planning run is dispatched")
    return EXIT_HANDLED


def run_exit(card: str, stage: str, repo: str) -> int:
    try:
        records = linear_ops.comment_records(card, whole_thread=True)
    except Exception as exc:  # noqa: BLE001 — unread is a crash, never a park
        print(f"ERROR: {card}: could not read the thread: {exc}", file=sys.stderr)
        return EXIT_CRASH
    found = findings(records, card, stage)
    is_granted = granted(records)
    outcome, why = decide(found, is_granted, stage)
    print(f"{card}: {outcome} — {why}")
    if outcome == PARK:
        return EXIT_PARK
    if outcome == REWRITE:
        return _rewrite(card, repo, found)
    decisions = decision_findings(found, stage)
    reason = question_reason(card, decisions)
    refused = planning_escalation.refusal(reason)
    if refused is None:
        return _question(card, reason, question_choices(card, decisions))
    print(f"{card}: the question was refused before it reached the CEO — "
          f"{refused}")
    if is_granted:
        print(f"{card}: the rewrite is already spent. Exit 3: the caller parks, "
              "and its note names every finding")
        return EXIT_PARK
    return _rewrite(card, repo, found, refused=True)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _read_records(card: str):
    try:
        return linear_ops.comment_records(card, whole_thread=True)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {card}: could not read the thread: {exc}", file=sys.stderr)
        return None


def _cmd_exit(args) -> int:
    return run_exit(args.card, args.stage, args.repo)


def _cmd_read(args) -> int:
    records = _read_records(args.card)
    if records is None:
        return EXIT_CRASH
    found = findings(records, args.card, args.stage,
                     whole_thread=args.whole_thread)
    outcome, why = decide(found, granted(records), args.stage)
    print(outcome)
    for finding in found:
        print(f"{class_word(finding)}\t{send_back_class.why(finding)}\t{finding}")
    print(f"{args.card}: {why}", file=sys.stderr)
    return 0


def _cmd_context(args) -> int:
    records = _read_records(args.card)
    if records is None:
        return EXIT_CRASH
    print(context_section(records))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("exit", help="decide the bound, and act on it")
    e.add_argument("card")
    e.add_argument("--stage", required=True, choices=STAGES)
    e.add_argument("--repo", required=True)
    e.set_defaults(fn=_cmd_exit)

    r = sub.add_parser("read", help="what exit would answer; writes nothing")
    r.add_argument("card")
    r.add_argument("--stage", required=True, choices=STAGES)
    r.add_argument("--whole-thread", action="store_true")
    r.set_defaults(fn=_cmd_read)

    c = sub.add_parser("context", help="the planner's section for this card")
    c.add_argument("card")
    c.set_defaults(fn=_cmd_context)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
