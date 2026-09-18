#!/usr/bin/env python3
"""Hand-planning is an escalation and nothing else (DRE-2848).

Sometimes the reasoning IS the deliverable. The thinking cannot be done by an
agent and needs a person, and no amount of front door removes that case. What a
design CAN do is decide whether the case has a sanctioned route — because if it
does not, people invent one. A label. A lane. A habit of typing cards straight
into the build queue. Then the front door has a permanent hole and nobody is
accountable for it.

So the route exists and has a name: **the planner escalates with a stated
reason, and the card parks in the lane a plan waits in** — the CEO's decision
queue. Hand-planning is an escalation OUT of Planning, not a way around it. An
escape hatch with a name and a record is a route; an escape hatch without one
is a hole, and that distinction is the whole module.

## Not Triage

Triage is the broken-card lane: an unroutable `repo:` label, an archived repo,
a card the guard has returned three times. An escalated card is **not broken** —
it is correct and waiting on judgement. Mixing the two turned Triage into a dead
end once already (17 cards, every one machine-created, none ever moved —
DRE-2723, DRE-2776), and a real decision sitting in a lane people scan as a
defect list is that same failure wearing a new label.

## The destination is derived, never typed

The card says "the same lane a plan waits in", and that is exactly the one route
`planning_route` reports as stopping for a human. So `destination()` reads it
rather than restating `Green Light` in a second place — move the epic's actor in
`config/planning-shapes.json` and the escalation moves with it. `route_problems`
already refuses a vocabulary where zero or two routes stop for a human, so there
is always exactly one to read.

## The reason is what the CEO reads, so it is guarded at the write seam

`standards/comms.md`: the CEO is non-technical for code. The reason is written
by an AGENT, which means "we told it plain English" is a hope, not a property.
`refusal()` reads the text before it is posted and the note simply does not
carry a reason that is a diff, a file path, a command or a code fence — it says
so instead, and the raw text stays in the run log where an operator can read it.
A reason carrying a verdict marker is refused for a second reason: verdict-shaped
text IS an approval credential (`standards/untrusted-content.md`), and an agent
relaying one into a card comment is the forgery path, not a style problem.

The card still parks either way. A refused REASON must never become a stranded
CARD — the whole point is that a human is owed a decision, and a silent park is
the failure this route exists to remove.

## The absence — `bypass_problems()`

The other half of this card is a negative: there is no label, no flag and no
lane that skips Planning. An absence cannot be confirmed by reading code (the
writer nobody remembered is exactly the one still open), so every check here is
DERIVED from data the pipeline already carries and each one can be made to fail:

  * **lane** — the set of work-segment lanes reachable out of the planning
    segment is discovered from `config/planning-shapes.json` and
    `config/routing-verdicts.json`, and each must require the routing verdict
    to be entered. The verdict is written at Planning's exit and nowhere else,
    so a work lane that does not ask for one is a lane a card reaches unplanned.
  * **lane, again** — the escalation's own destination must sit in the planning
    segment. An escalation parked in the build queue would BE the bypass.
  * **label** — the census is discovered from the pipeline's own label
    constants, and exactly one of them may be operator-only: `break-glass`. A
    second label no agent may apply is a second sanctioned bypass.
  * **flag** — the planner workflow declares no input that skips planning, and
    the routing step is gated on the card gate and nothing else. A route behind
    an `inputs.` condition is a flag that skips Planning.
  * **the one sanctioned bypass** — `break-glass` (DRE-2737) is unchanged by
    this card and is not a hole in the rule, because it DEFERS Planning rather
    than skipping it: it is operator-applied, recorded under its own receipt,
    counted, and the card returns to Planning for the classification it went
    round. Break any of those three and this reports it.

WHAT IT DOES NOT PROVE, said plainly rather than left to be discovered: this
reads the pipeline's own DECLARATIONS — the vocabularies, the labels, the
planner workflow's inputs. It says nothing about the WRITERS that act on them,
which is the other half and is `ready_lane_writers.py`'s (DRE-2859, splitting
DRE-2847 with DRE-2858): that module discovers every writer that can put a card
in a ready-work lane and checks each against the contract, and it reads its
definition of "ready work" from `work_lanes_reachable_from_planning` below so
the two checks cannot end up policing different lanes. Between them a writer
outside this repository is still out of reach — the relay, a Linear automation,
a person dragging a card — and both modules say so. What this module guarantees
is that nothing HERE declares a way past
Planning.

## The card may have moved on (DRE-3654)

`escalate()` posts its note and then re-asserts the move every time, so a crash
between the two writes converges on the retry. On 2026-09-11 that rule parked a
card that was no longer Planning's to park: DRE-3604's classifier could not
reach its model at 18:05:32 PT, a routing verdict was stamped by hand at
18:05:33, the card was moved to In Progress by hand at 18:05:38 and its build
started — and at 18:06:23 the escalation dragged it In Progress → Green Light,
45 seconds after it had left, with a PR on the way. The move never re-read the
lane; it wrote from the state the run began in.

So the lane is read LIVE, once, immediately before both writes, and the card is
parked only if it is still in the segment the escalation is about — the one
`ORIGIN` sits in, read from the lane contract rather than named here — and
carries no routing verdict. A verdict means Planning has already answered, so
the card is past this step whatever lane the board shows it in; the verdict is
read by `routing_verdict`'s own reader, never matched here. A card that has
moved on gets NO state write. It gets one note saying it had moved on and
where to (or a follow-up withdrawing the ask, if the escalation note had landed
before the hand move), and a retry converges on "left alone" the way it
converges on "parked". The read sits before the note as well as the move, so
the first note is the honest one rather than an escalation and a retraction;
the residual window is the comment write itself. The "re-assert every time"
rule stands for the case it was written for: a card still in the segment.

## "Already escalated" means on THIS attempt (DRE-4223)

The note is posted once and the move re-asserted every time, so a retry
converges. "Once" used to be read over the whole comment window, and on
2026-09-18 09:24 PT that moved DRE-2428 Planning → Green Light with nothing
posted: its first escalation (20:42 PT the night before) had carried a reason,
the CEO answered it, the card went back to Planning, a limit death left it
standing, and when the stall watchdog fired again `escalate()` found the old
receipt, printed `already escalated`, skipped the note and ran the move. The
CEO's decision queue held a card with nothing to answer.

Every planning attempt opens with a boundary the card carries — plan.yml
posts `plan_critic.cycle_marker` as its own comment the moment a card is
routed to plan, first attempt and every re-plan alike — so the receipt count
is scoped to the comments AFTER the newest boundary (`_this_attempt`), read
through `plan_critic.CYCLE_PREFIX` and never a copied string. A card with no
boundary keeps the once-per-card reading. The scope is the thread's ORDER,
which is how `plan_critic.current_cycle` and `linear_ops.count_comments(since=)`
already read it: a receipt whose time cannot be read is still somewhere in
the thread, and "treat it as this attempt's" would be the silent move again.
The two rules resolve one way only — when they disagree, a duplicate question
is the cheap failure and a card in the queue with nothing to answer is not.

CLI:

    python3 scripts/planning_escalation.py check
    python3 scripts/planning_escalation.py escalate DRE-N --why "…"
    python3 scripts/planning_escalation.py escalate DRE-N --reason-file <path>
    python3 scripts/planning_escalation.py requeue  DRE-N --reason-file <path>

The last one is DRE-3074's: a classification that never reached a model records
that and stays where it is, instead of asking a human to decide our plumbing.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import break_glass  # noqa: E402
import lane_contract  # noqa: E402
import plan_critic  # noqa: E402 — the attempt boundary is its record (DRE-4223)
import planning_route  # noqa: E402
import planning_shape  # noqa: E402
import routing_verdict  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
PLAN_WORKFLOW = os.path.join(ROOT, ".github", "workflows", "plan.yml")

# The record this module writes on a card it escalates. Same grammar as
# `planning_route.ROUTE_TAG` and `routing_verdict.VERDICT_TAG`, and
# deliberately neither of them: a note opening with either marker would be read
# back as a stamp the card never earned.
ESCALATION_TAG = "planning-escalation"
ESCALATION_MARK = "🙋"

# The mark a REWRITE park opens with (DRE-4058), and the reason it is not
# `ESCALATION_MARK`: 🙋 is a question the reader can answer where they sit, 📝
# is a card that has to be rewritten before any answer means anything. A reader
# scanning the thread should be able to tell the two apart without reading
# either. ONE definition — `plan_critic` reads it for the note it posts just
# above this park, because a note and the park that follows it opening with
# different icons is exactly the drift the icon was for. The TAG is unchanged
# on purpose: counting is keyed on it (`escalate`), so a second tag here would
# hand the same card a second escalation budget.
REWRITE_MARK = "📝"

# The lane the escalation leaves FROM. Hand-planning is an escalation OUT of
# Planning, so the card has been through Planning by the time it parks — which
# is also the lane `break-glass` repays its skipped classification to, and
# `bypass_problems` binds the two rather than letting them drift apart.
ORIGIN = "Planning"

#: When this process started, which is the default answer to "when did the
#: current planning attempt begin" (`attempt_started_at`, DRE-4124). Captured
#: at import so every read inside one run agrees, and so a verdict posted while
#: this run was working still reads as current.
_PROCESS_STARTED_AT = datetime.now(UTC).isoformat()

# What the note says when the planner stated no reason at all, and when it
# stated one the CEO must not be handed. Named constants because the tests and
# the workflow both read for them, and because a reader of the card deserves the
# same sentence every time.
NO_REASON_STATED = (
    "The planner ended without creating any cards and without stating a reason."
)
NOT_PLAIN_ENGLISH = (
    "The planner's reason was written in technical terms, so it is not repeated "
    "here — it is in the run's own log."
)

# --------------------------------------------------------------------------- #
# the OTHER reason a classification produced nothing (DRE-3074)                #
# --------------------------------------------------------------------------- #
#
# Until this card there was one reason, and it was said for two different facts.
# On 2026-09-03 21:14 three planner runs escalated to the CEO inside twenty
# seconds with "We could not get an answer from the system that reads new
# cards" — and the truth was that the classifier's call was a raw POST to
# `/v1/messages` made with a subscription token, which answers 429 to every
# request whatever the load. Nothing had read a card, nothing could, and the
# CEO was being asked to decide something no human decision would fix.
#
# So the two are separated at the source:
#
#   * the MODEL read the card and could not tell — a judgement, and the CEO's.
#     `escalation_comment` above, unchanged, parking the card in their queue.
#   * the TRANSPORT never reached a model — 429, 401, a 5xx, a CLI that did not
#     run. That is our plumbing, it names itself as such, and it buys ONE more
#     run rather than a place in a human's queue.
#
# The requeue is the run's own failure: `Agent Plan` is on the medic's watch
# list, so a red run IS the retry, and `planning_classify` spends the second
# failure on the escalation above rather than looping.
TRANSPORT_TAG = "planning-classify-transport"
TRANSPORT_MARK = "🔌"

#: How many transport failures a card absorbs before the question does go to a
#: human. One: an infrastructure failure that survives a retry has stopped being
#: transient, and a card nobody can classify still owes somebody an answer.
TRANSPORT_CAP = 1

# The record a card gets when the escalation reaches it too late (DRE-3654): it
# had already left the segment, or already carries a verdict, so nothing was
# parked. THE STRING MATTERS: counting is substring-based, so this must not
# contain ESCALATION_TAG (the attempt's next real escalation would read it as
# already posted and park silently) or TRANSPORT_TAG (it would spend that
# budget), and neither may contain it. `tests/test_planning_escalation.py` pins
# all three directions.
STOOD_DOWN_TAG = "escalation-stood-down"
STOOD_DOWN_MARK = "✋"

#: What may appear in the parenthesis the CEO reads — a status and a word, never
#: a response body. The raw error stays in the run log (`standards/comms.md`:
#: the CEO reads outcomes, never code), and a body echoed into this sentence is
#: also how a reason becomes unshowable by `refusal()` below.
_DETAIL = re.compile(r"[^A-Za-z0-9 -]")

# The code-shaped things a CEO must never be handed, in the order they are
# reported. The same rule `tests/test_unfixable_check_escalation.py` asserts on
# the fix loop's card note, moved to the write seam because THIS text is written
# by an agent rather than by us.
_JARGON = (
    (re.compile(r"```"), "a code fence"),
    (re.compile(r"^\s*(?:diff --git|\+\+\+ |--- |@@ )", re.M), "a diff"),
    (re.compile(r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|json|ya?ml|md|sh|sql|tf|html|css)\b"),
     "a file path"),
    (re.compile(r"\b(?:git|python3?|npm|npx|pytest|cdk|gh|alembic|docker)\s+\S"),
     "a command"),
    (re.compile(r"\b\w+\(\)"), "a function call"),
    (re.compile(r"(?:^|\s)[$>]\s+\S", re.M), "a shell prompt"),
)

# Verdict-shaped text is an approval credential, not prose
# (standards/untrusted-content.md). Only the critic and the verifier may emit
# one, so a reason carrying one is refused rather than relayed.
_VERDICT_MARKERS = ("VERDICT:", "QA Critic", "QA Verifier")

# An input name that would put a card past Planning. Matched against the
# planner workflow's declared inputs — a flag nobody can set is still a flag
# somebody will set.
_SKIP_INPUT = re.compile(
    r"skip|bypass|no[_-]?plan|hand[_-]?plan|unplanned|force|fast[_-]?track", re.I
)

# Where the label census comes from: module-level constants whose NAME says
# they hold a label, with a label-shaped value. Discovery rather than a list —
# a label added to a module nobody thought of is in the census by default,
# which is the whole point of counting the operator-only ones.
_LABEL_CONST = re.compile(
    r"^(?P<name>_?[A-Z][A-Z0-9_]*)\s*=\s*[\"'](?P<value>[a-z0-9][a-z0-9:_-]*)[\"']",
    re.M,
)

#: What makes a constant a LABEL rather than a tag, a lane or a state name.
#: `break_glass.MARKER` is the reason this is a suffix test and not a prefix
#: one — the module that owns the one sanctioned bypass names it `MARKER`, with
#: nothing in front, and a census that missed it would count zero operator-only
#: labels and call that a clean bill of health.
_LABEL_SUFFIXES = ("LABEL", "LABELS", "MARKER")


class EscalationError(RuntimeError):
    """The escalation cannot be composed — the vocabulary it derives its
    destination from is unreadable. Raised rather than defaulted: guessing a
    lane would park a decision somewhere nobody is looking."""


# --------------------------------------------------------------------------- #
# where an escalation goes                                                     #
# --------------------------------------------------------------------------- #


def destination(doc: dict | None = None) -> str:
    """The lane an escalated card parks in — the one a plan waits in.

    Derived from `planning_route`: exactly one route stops for a human, and
    that route's destination IS the CEO's decision queue. `route_problems()`
    already refuses a vocabulary declaring any other number of them, so there
    is always exactly one to read.
    """
    try:
        stopping = [r for r in planning_route.routes(doc) if r.owes_green_light]
    except (planning_shape.ShapeError, planning_shape.UnknownShape) as e:
        raise EscalationError(
            f"the shape vocabulary could not be read, so an escalation has "
            f"nowhere to park: {e}"
        ) from e
    if len(stopping) != 1:
        raise EscalationError(
            "an escalation parks in the lane a plan waits in, and the shape "
            f"vocabulary declares {[r.shape for r in stopping] or 'no'} route(s) "
            "that stop for a human — so there is no such lane to read"
        )
    return stopping[0].destination


# --------------------------------------------------------------------------- #
# what the CEO reads                                                           #
# --------------------------------------------------------------------------- #


def jargon(reason: str | None) -> tuple:
    """The code-shaped things this reason leaks, in the order reported.

    Empty when the text is plain English. Deliberately a LIST of what was
    found, not a boolean: the planner is told what to rewrite, and the operator
    reading the run log can tell a file path from a forged verdict marker.
    """
    text = reason or ""
    found = [what for pattern, what in _JARGON if pattern.search(text)]
    for marker in _VERDICT_MARKERS:
        if marker in text:
            found.append("a verdict marker")
            break
    return tuple(dict.fromkeys(found))


def refusal(reason: str | None) -> str | None:
    """Why this reason must not be put in front of the CEO, or None to post it.

    Refusing the TEXT is never refusing the escalation — see `escalate()`. The
    card still parks; the note simply says the reason was not fit to show.
    """
    if not (reason or "").strip():
        return "no reason was stated"
    leaks = jargon(reason)
    if leaks:
        return (
            "the reason was written in technical terms — it carries "
            + ", ".join(leaks)
            + ". The CEO reads outcomes and risk, never code"
        )
    return None


def escalation_comment(identifier: str, reason: str | None,
                       transport: bool = False, rewrite: bool = False) -> str:
    """The note that IS the escalation. One card, one of these.

    Written to `standards/comms.md`: purpose in the first sentence, the reason
    in its own block, and exactly one ask as the closing line.

    `transport` is DRE-3074's second arrival at this door, and it changes what
    the note CLAIMS rather than what it does. The sentences below were written
    for hand-planning, where the reasoning is genuinely the deliverable; said
    over a classifier that failed to reach a model twice running they would be
    plainly untrue, and a confident wrong answer is worse than none
    (`standards/console-honesty.md`). The card still parks — an infrastructure
    failure that outlived its retry needs a person — it just parks saying what
    happened.

    `rewrite` is DRE-4058's third arrival, and it is the same lesson a third
    time. The one-off route spends its bound and then asks for a REWRITTEN card
    rather than another answer (`plan_critic.one_off_rewrite_request`) — and
    this wrapper, written for the question case, closed that request with
    "Answer it here and move the card back to be picked up" over the words "it
    is correct and waiting on judgement". Read quickly, that is an instruction
    to do the one thing the bound exists to stop. So the wrapper branches where
    it makes a claim, on the flag the decision already published, rather than
    re-reading the text to guess which kind of park this is.
    """
    lane = destination()
    if rewrite:
        opening = (
            f"{REWRITE_MARK} {ESCALATION_TAG}: {identifier} has been sent back "
            "as many times as this route allows, and what it needs now is a "
            "rewrite rather than another answer — the card itself is what has "
            "to change."
        )
    elif transport:
        opening = (
            f"{ESCALATION_MARK} {ESCALATION_TAG}: {identifier} could not be "
            "classified and needs you to look — the step that reads new cards "
            "has now failed twice to reach the model it asks, so nothing has "
            "read this card at all."
        )
    else:
        opening = (
            f"{ESCALATION_MARK} {ESCALATION_TAG}: {identifier} needs a decision "
            "from you before it can be planned — the reasoning itself is the "
            "deliverable here, and that part is not work an agent can do."
        )
    lines = [opening, ""]
    why = refusal(reason)
    if why is None:
        lines += [f"**Why it needs you:** {(reason or '').strip()}", ""]
    elif not (reason or "").strip():
        lines += [f"**Why it needs you:** {NO_REASON_STATED}", ""]
    else:
        lines += [f"**Why it needs you:** {NOT_PLAIN_ENGLISH}", ""]
    if rewrite:
        lines += [
            f"This card is parked in **{lane}** — your decision queue, the "
            "same place a plan waits for you. It is not waiting on an answer: "
            "the questions it could ask have all been asked, and it still is "
            "not something an agent can build as it stands.",
            "",
            "Rewrite it and move the card back to be picked up, or park it if "
            "we should not do this at all.",
        ]
    elif transport:
        lines += [
            f"This card is parked in **{lane}** — your decision queue, the "
            "same place a plan waits for you. There is nothing wrong with the "
            "card and no judgement is being asked of you: something on our "
            "side is down, and it is here so it is not forgotten while we fix "
            "it.",
            "",
            "Move it back to be picked up once we tell you the classifier is "
            "reading cards again.",
        ]
    else:
        lines += [
            f"This card is parked in **{lane}** — your decision queue, the "
            "same place a plan waits for you. It is not broken and it has not "
            "failed anything; it is correct and waiting on judgement.",
            "",
            "Answer it here and move the card back to be picked up, or park it "
            "if we should not do this at all.",
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the transport failure — a requeue, not a park (DRE-3074)                     #
# --------------------------------------------------------------------------- #


def transport_detail(detail: str | None) -> str:
    """The status, made safe to put inside the CEO's sentence.

    A 429 body is JSON and a 401 body sometimes carries a URL; either one echoed
    into the reason would trip `refusal()` and the CEO would be shown nothing at
    all. So only a status and a word survive here — the rest is in the run log,
    which is where an operator reads it.
    """
    cleaned = " ".join(_DETAIL.sub(" ", str(detail or "")).split())[:60]
    return cleaned or "no answer"


def transport_reason(detail: str | None) -> str:
    """What a transport failure says. Plain English, and it names ITSELF as
    plumbing — the whole point of the split is that the reader can tell an
    infrastructure failure from a question about the work."""
    return (
        f"The classifier could not reach its model ({transport_detail(detail)}, "
        "transport), so nothing has read this card yet. That is our own plumbing "
        "failing, not a question about the work."
    )


def transport_comment(identifier: str, reason: str | None) -> str:
    """The receipt a requeued card carries. It is also the COUNTER: the cap on
    how many times this may happen is read back off these comments, so the note
    and the budget are one thing rather than two that can disagree."""
    lines = [
        f"{TRANSPORT_MARK} {TRANSPORT_TAG}: {identifier} was not classified this "
        "run — the step that reads new cards could not reach its model.",
        "",
    ]
    why = refusal(reason)
    lines += [
        f"**What happened:** {(reason or '').strip()}" if why is None
        else f"**What happened:** {NOT_PLAIN_ENGLISH}",
        "",
        "**What happens next:** this run is failed on purpose so it runs again. "
        "The card has not moved and nothing has been decided about it. If the "
        "next run cannot reach a model either, this comes to you as a question.",
        "",
        "Nothing is needed from you yet.",
    ]
    return "\n".join(lines)


def requeue(linear_ops, identifier: str, reason: str | None) -> bool:
    """Record the transport failure on the card. True when the note was written.

    Deliberately writes NO state: a requeue is not a park, and a card moved into
    the CEO's queue by our own plumbing is the failure DRE-3074 removes. Posted
    at most once per card, keyed on the tag, exactly as `escalate` is — the count
    is the budget, so a note posted twice would spend it twice.
    """
    already = 0
    try:
        already = linear_ops.count_comments(identifier, TRANSPORT_TAG)
    except Exception as exc:  # noqa: BLE001 — a read failure must not strand the card
        print(f"{identifier}: could not read prior transport failures ({exc})",
              file=sys.stderr)
    if already:
        print(f"{identifier}: already recorded, under {TRANSPORT_TAG}")
        return False
    linear_ops.cmd_comment(identifier, transport_comment(identifier, reason))
    return True


# --------------------------------------------------------------------------- #
# the escalation itself                                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Outcome:
    """What `escalate()` did, so a caller can say it in its own words.

    `parked` — the move to the decision queue was written (or re-asserted).
    `posted` — a note was written THIS call: the escalation, or the stand-down.
    `stood_down` — why the card was left where it is, in the words the note
    used, or None when it parked. Exactly one of `parked` / `stood_down` holds.
    """

    parked: bool
    posted: bool
    stood_down: str | None


def in_escalation_segment(name: str, contract: dict | None = None) -> bool:
    """Is this lane in the segment the escalation is about?

    The segment is the one `ORIGIN` sits in, read from the lane contract — no
    lane is named here, so a lane added to or moved out of that segment moves
    this answer with it. A retired board name resolves through the contract's
    aliases first. A lane the contract does not carry is NOT in the segment:
    unknown is never a pass, because the only thing this answer licenses is
    moving a card, and a card nobody can place is left where it is.
    """
    name = lane_contract.aliases(contract).get(name, name)
    try:
        ours = lane_contract.lane(ORIGIN, contract=contract)["segment"]
        return lane_contract.lane(name, contract=contract).get("segment") == ours
    except lane_contract.UnknownLane:
        return False


def attempt_started_at() -> str:
    """When the attempt THIS process is making began, ISO-8601.

    The card's own planning history is not readable from here — Linear's issue
    query carries no lane-entry time — so the honest answer to "when did the
    current planning attempt begin" is the one fact this process can state
    about itself: when it started. A caller that knows better (a workflow that
    can hand over its run's start) passes `attempt_since` explicitly.
    """
    return _PROCESS_STARTED_AT


def _body(record) -> str:
    """One comment's text, whether it arrived as a string or as a
    `{"body", "createdAt"}` record (`linear_ops.comment_timeline`'s shape)."""
    if isinstance(record, dict):
        return record.get("body") or ""
    return record or ""


def _this_attempt(records) -> list:
    """The comments that belong to the card's CURRENT planning attempt.

    Everything after the newest fresh-attempt boundary — the `plan-cycle:`
    record plan.yml posts as its own comment when a card is routed to plan,
    recognised through `plan_critic.CYCLE_PREFIX`. A thread with no boundary
    is one attempt: a card that has never been re-planned keeps the reading it
    always had.

    The scope is POSITIONAL, the same reading `plan_critic.current_cycle` and
    `linear_ops.count_comments(since=)` make: both readers that hand comments
    here (`linear_ops.window_nodes`, `comment_timeline`) order them oldest →
    newest, so the newest boundary's index is the attempt's start and nothing
    has to parse a clock. That is deliberate (DRE-4223): the only thing this
    scope licenses is SKIPPING the question before a move, and a receipt whose
    time cannot be read, treated as this attempt's, is a card moved with
    nothing to answer — the exact failure the scope exists to end. A prefix
    test rather than `plan_critic`'s sole-record shape, because the failure a
    forged boundary can buy here is one duplicate question, never a silent
    move, and a reading that follows the constant is what the tests pin.
    """
    records = list(records or ())
    start = 0
    for i, record in enumerate(records):
        if _body(record).startswith(plan_critic.CYCLE_PREFIX):
            start = i + 1
    return records[start:]


def _stale(record, attempt_since: str | None) -> bool:
    """Was this comment posted BEFORE the current planning attempt began?

    With no attempt stated, nothing is stale — the two seams that predate
    DRE-4124 keep the reading they have always had. With one stated, a comment
    whose time cannot be read is stale: unknown is never a pass
    (`standards/console-honesty.md` rule 2), and the only thing a fresh verdict
    licenses is leaving a card standing in the lane it is stuck in.
    """
    if not attempt_since:
        return False
    when = record.get("createdAt") if isinstance(record, dict) else None
    try:
        posted = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        began = datetime.fromisoformat(str(attempt_since).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    return posted < began


def moved_on(issue: dict, comment_bodies, contract: dict | None = None,
             *, attempt_since: str | None = None) -> str | None:
    """Why this card is past the escalation, or None when it is still ours.

    Two facts, and both are said when both hold: the lane the board shows the
    card in is outside the segment (`in_escalation_segment`), and/or the card
    carries a routing verdict FROM THE CURRENT PLANNING ATTEMPT — read by
    `routing_verdict`'s own reader, never matched here, so a note that merely
    quotes one carries none.

    THE VERDICT USED TO BE READ "whatever lane it is in" (DRE-4124). On
    2026-09-16 23:42 PT that stood DRE-2415 down on a verdict from 09-08 while
    the board plainly showed the card still in Planning, where it had then sat
    for thirty-five days. A verdict is the answer to one planning attempt, and
    an attempt that ended thirty-five days ago is not evidence about this one —
    the card is still here, which is the fact the lane reports and the verdict
    cannot overrule. So the lane is the load-bearing read, and a verdict older
    than `attempt_since` is stale.

    It is still a real fact when it is CURRENT: a verdict stamped since this
    attempt began is this attempt's answer, and a card that has just been
    classified is past this step even if the move has not landed yet — the
    five-second window DRE-3654 was written for. `attempt_since=None` states no
    attempt, and then nothing is stale and the reading is exactly the one the
    two pre-DRE-4124 callers have always had.
    """
    lane = ((issue or {}).get("state") or {}).get("name") or ""
    facts: list[str] = []
    if not in_escalation_segment(lane, contract):
        facts.append(f"it is in {lane or 'no lane the board reports'}")
    current = [_body(c) for c in (comment_bodies or ())
               if not _stale(c, attempt_since)]
    verdicts = routing_verdict.verdicts_on(current)
    if verdicts:
        facts.append(
            "it already carries a routing verdict (" + ", ".join(verdicts) + ")"
        )
    return " and ".join(facts) or None


def stood_down_comment(identifier: str, where: str, reason: str | None,
                       withdrawn: bool = False, transport: bool = False,
                       rewrite: bool = False) -> str:
    """The record a card gets when the escalation reached it too late.

    `where` is `moved_on()`'s sentence. `withdrawn` is the crash-between-the-
    writes case with a hand move in the gap: the escalation note is already on
    the card, so this one withdraws the ask instead of leaving a question
    standing over a card someone is building. Same plain-English rule as the
    escalation note — the reason is shown only if `refusal()` lets it through.
    """
    lane = destination()
    if withdrawn:
        lines = [
            f"{STOOD_DOWN_MARK} {STOOD_DOWN_TAG}: {identifier} — the ask above "
            f"is withdrawn. Since that note was posted the card has moved on "
            f"from {ORIGIN}: {where}. It has been left there and was not moved "
            f"back to **{lane}**.",
            "",
        ]
    else:
        lines = [
            f"{STOOD_DOWN_MARK} {STOOD_DOWN_TAG}: {identifier} was not parked — "
            f"by the time this run went to escalate it, the card had already "
            f"moved on from {ORIGIN}: {where}. It has been left there.",
            "",
        ]
    why = refusal(reason)
    # `rewrite` sits with `transport` rather than with the question: neither one
    # ASKED anything, so "what this run had to ask" over either of them is the
    # same wrong sentence the rewrite park was flagged for (DRE-4058).
    heading = "**What this run had found:**" if (transport or rewrite) else \
        "**What this run had to ask:**"
    if why is None:
        lines += [f"{heading} {(reason or '').strip()}", ""]
    elif not (reason or "").strip():
        lines += [f"{heading} {NO_REASON_STATED}", ""]
    else:
        lines += [f"{heading} {NOT_PLAIN_ENGLISH}", ""]
    lines.append(
        "Nothing is needed from you. A card past this step is being handled by "
        "whoever moved it on, and parking it now would only have dragged it "
        "back out of their hands."
    )
    return "\n".join(lines)


def escalate(linear_ops, identifier: str, reason: str | None,
             transport: bool = False, rewrite: bool = False, *,
             issue: dict | None = None, comments=None,
             attempt_since: str | None = None) -> Outcome:
    """Post the escalation and park the card — if the card is still ours.

    `issue` and `comments` let a caller that has ALREADY read the card hand
    over what it read instead of paying for it again (DRE-4124). The reconcile
    sweep is that caller: its board read returns every Planning card's lane and
    its comment window inline, and a second per-card read here would put back
    exactly the request DRE-2929 took out — one per card, per sweep, per repo,
    the term that exhausted the workspace quota for seven hours. The state
    write below is guarded on its own live re-read (`linear_ops.cmd_state` →
    `guarded_state_write`), so the move is no less careful; what is traded is
    the freshness of the STAND-DOWN decision, against a snapshot taken seconds
    earlier in the same pass.

    Otherwise the lane is read LIVE first (`fresh=True`, never the command's
    memo — the memo is the state the card was in when the run began, and that
    is the read DRE-3604 was wrong by), and the comments come from
    `comment_timeline` — WITH their `createdAt`, because since DRE-4124 a
    verdict is only a stand-down while it is CURRENT and `_stale` reads a time
    it cannot parse as stale. `comment_bodies` is the same one window with the
    times thrown away, so reading it here made EVERY verdict stale and parked
    cards a fresh verdict had just stood down — the two non-sweep callers
    (`planning_route._cmd_exit`, `_cmd_escalate`) lost DRE-3604/DRE-3654's
    five-second window entirely. Both read the same `_THREAD_QUERY`, so the
    switch costs no extra request inside a pass; outside one it is the same
    read `comment_bodies` would have made. Nothing is written until it has
    been. A card
    that has moved on — out of the segment, or already carrying a verdict —
    gets NO state write and one stand-down note, keyed on its own tag so a
    retry converges on "left alone"; if the escalation note had already
    landed, the stand-down withdraws it. A failed read raises: nothing has
    been written yet, the run goes red, and the retry converges — the same
    shape as the pre-write re-read in `linear_ops.guarded_state_write`.

    For a card still ours the rule is unchanged: the note lands BEFORE the
    move, always — moving the card without the question is a silent park, and
    the CEO sees something appear in their queue with nothing to answer.
    Posted at most once per PLANNING ATTEMPT, keyed on the tag and scoped to
    the comments after the card's newest fresh-attempt boundary
    (`_this_attempt`, DRE-4223) — a retried run must converge rather than turn
    one decision into a thread, and a card re-planned after the CEO answered
    must be asked again rather than parked silently on the answered receipt —
    and the move is re-asserted every time, because the crash this guards
    against is the one between the two writes.
    """
    lane = destination()
    handed = comments is not None
    if issue is None:
        issue = linear_ops.get_issue(identifier, fresh=True)
    bodies = list(comments) if handed else linear_ops.comment_timeline(identifier)
    elsewhere = moved_on(
        issue, bodies, attempt_since=attempt_since or attempt_started_at())

    def _count(tag: str, what: str) -> int:
        """How many of `tag` the card already carries. Counted off the handed
        comments when there are any — same substring test `count_comments`
        makes, for no request."""
        if handed:
            return sum(1 for record in bodies if tag in _body(record))
        try:
            return linear_ops.count_comments(identifier, tag)
        except Exception as exc:  # noqa: BLE001 — a read failure must not strand the card
            print(f"{identifier}: could not read prior {what} ({exc})",
                  file=sys.stderr)
            return 0

    # The escalation count is scoped to THIS attempt (DRE-4223), and it is
    # read off `bodies` on both paths: handed, that is the window the sweep
    # read; otherwise it is `comment_timeline`, the same window
    # `count_comments` would read, already in hand — so the CLI callers get the
    # same scope rather than an unscoped count through a second reader.
    already = sum(
        1 for record in _this_attempt(bodies) if ESCALATION_TAG in _body(record)
    )
    posted = False
    if elsewhere is not None:
        recorded = _count(STOOD_DOWN_TAG, "stand-downs")
        if recorded:
            print(f"{identifier}: already recorded, under {STOOD_DOWN_TAG}")
        else:
            linear_ops.cmd_comment(identifier, stood_down_comment(
                identifier, elsewhere, reason, withdrawn=bool(already),
                transport=transport, rewrite=rewrite))
            posted = True
        return Outcome(parked=False, posted=posted, stood_down=elsewhere)
    if already:
        print(f"{identifier}: already escalated, under {ESCALATION_TAG}")
    else:
        linear_ops.cmd_comment(
            identifier,
            escalation_comment(identifier, reason, transport, rewrite))
        posted = True
    linear_ops.cmd_state(identifier, lane)
    return Outcome(parked=True, posted=posted, stood_down=None)


# --------------------------------------------------------------------------- #
# the absence: nothing here skips Planning                                     #
# --------------------------------------------------------------------------- #


def label_census(paths=None) -> tuple:
    """Every label the pipeline's own modules declare, discovered not listed.

    A module-level constant whose NAME says it holds a label and whose value is
    label-shaped. Discovery is the point: the check that matters counts how
    many of these are operator-only, and a hand-written list would count only
    the ones somebody remembered.
    """
    sources = paths if paths is not None else sorted(
        glob.glob(os.path.join(_HERE, "*.py"))
    )
    found: list[str] = []
    for path in sources:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        for match in _LABEL_CONST.finditer(text):
            if not match.group("name").endswith(_LABEL_SUFFIXES):
                continue
            value = match.group("value")
            if value not in found:
                found.append(value)
    for name in planning_shape.shapes():
        for label in planning_shape.marks(name):
            if label not in found:
                found.append(label)
    for name in routing_verdict.verdicts():
        for label in routing_verdict.marks(name):
            if label not in found:
                found.append(label)
    return tuple(found)


def work_lanes_reachable_from_planning(
    contract: dict | None = None, doc: dict | None = None
) -> tuple:
    """The work-segment lanes the planning segment can send a card to.

    Discovered from the two vocabularies rather than named here: a new shape or
    a new routing verdict pointing at a lane nothing gates is caught without
    anybody remembering to widen a list.

    Public because it is the definition of "a lane the pipeline treats as ready
    work" and `ready_lane_writers.py` (DRE-2859) asks the same question of the
    writers. Two derivations of one set is how the two checks end up policing
    different lanes.
    """
    wanted: list[str] = []
    for route in planning_route.routes(doc):
        wanted.append(route.destination)
    for verdict in routing_verdict.verdicts():
        wanted.append(routing_verdict.destination(verdict))
    out: list[str] = []
    for name in dict.fromkeys(wanted):
        try:
            entry = lane_contract.lane(name, contract=contract)
        except lane_contract.UnknownLane:
            continue  # a destination that is not a live lane is planning_route's finding
        if entry.get("segment") == "work" and name not in out:
            out.append(name)
    return tuple(out)


def workflow_problems(text: str) -> list:
    """Everything in the planner workflow that would let a card skip Planning.

    Two questions: does it declare a flag that means "do not plan this", and is
    the routing step gated on anything other than the card gate. A route behind
    an `inputs.` condition is a flag that skips Planning even when no input is
    named for it.
    """
    problems: list[str] = []
    for name in re.findall(r"^ {6}([a-z][a-z0-9_]*):\s*$", text or "", re.M):
        if _SKIP_INPUT.search(name):
            problems.append(
                f"the planner workflow declares the input {name!r}, which reads "
                "as a way past Planning — hand-planning is an escalation, and "
                "there is no flag that skips the lane"
            )
    match = re.search(
        r"\n( +)- name: [^\n]*\n(?:\1  [^\n]*\n)*?\1  if: ([^\n]*)\n"
        r"(?:\1  [^\n]*\n|\1    [^\n]*\n| *\n)*?[^\n]*planning_route\.py decide",
        text or "",
    )
    if match is None:
        problems.append(
            "no step in the planner workflow routes the card out of Planning — "
            "a card that is never routed is a card that skipped the lane"
        )
    else:
        condition = match.group(2).strip()
        if "inputs." in condition or "label" in condition.lower():
            problems.append(
                f"the routing step is gated on {condition!r} — every card that "
                "passes the card gate is routed, and a condition naming an "
                "input or a label is a flag that skips Planning"
            )
    return problems


def bypass_problems(
    *,
    contract: dict | None = None,
    doc: dict | None = None,
    workflow_text: str | None = None,
    census=None,
) -> list:
    """Everything in this repository that would let a card skip Planning.

    An empty list is the claim this card makes. Every entry names the thing it
    found and why it is a bypass — see the module header for what this does and
    does not reach.
    """
    problems = list(planning_route.route_problems(doc))

    # The escalation itself is a route OUT of Planning, so both ends have to be
    # in the planning segment. One in the build queue would BE the bypass.
    try:
        lane_contract.lane(ORIGIN, contract=contract)
    except lane_contract.UnknownLane as e:
        problems.append(
            f"an escalation leaves from {ORIGIN!r}, which is not a live lane: {e}"
        )
    try:
        parked = destination(doc)
        entry = lane_contract.lane(parked, contract=contract)
        if entry.get("segment") != "planning":
            problems.append(
                f"an escalation would park in {parked!r}, which is in the "
                f"{entry.get('segment')!r} segment — a decision parked in the "
                "build queue is picked up rather than decided on, which is the "
                "bypass this card closes"
            )
    except (EscalationError, lane_contract.UnknownLane) as e:
        problems.append(str(e))

    # The lane half: the routing verdict is written at Planning's exit and
    # nowhere else, so a work lane that does not require one can be entered by
    # a card that was never planned.
    for name in work_lanes_reachable_from_planning(contract, doc):
        clauses = lane_contract.lane(name, contract=contract)["clauses"]
        stated = " ".join(
            (clauses.get(kind) or {}).get("text") or "" for kind in ("entrance", "evidence")
        )
        if "verdict" not in stated.lower():
            problems.append(
                f"the lane {name!r} can be reached out of the planning segment "
                "and its entrance asks for no routing verdict — the verdict is "
                "what Planning's exit writes, so a lane that does not require "
                "one is a lane that skips Planning"
            )

    # The label half: exactly one label may be operator-only, and it is the one
    # sanctioned bypass.
    try:
        import linear_ops

        operator_only = [
            label for label in (census if census is not None else label_census())
            if linear_ops.agent_label_refusal(label) is not None
        ]
    except Exception as e:  # noqa: BLE001 — an unreadable seam is a problem, not a crash
        problems.append(
            f"the label write seam could not be read, so no label can be "
            f"checked against it: {e}"
        )
        operator_only = None
    if operator_only is not None and operator_only != [break_glass.MARKER]:
        problems.append(
            "exactly one label may be operator-only — the one sanctioned "
            f"bypass, {break_glass.MARKER!r}. These are: "
            f"{', '.join(operator_only) or 'none'}. A second label no agent may "
            "apply is a second way past the front door, with nobody accountable "
            "for it"
        )

    # The one sanctioned bypass, unchanged by this card: recorded, counted, and
    # it still owes the classification it skipped.
    if break_glass.REVIEW_STATE != ORIGIN:
        problems.append(
            f"{break_glass.MARKER!r} returns a bypassed card to "
            f"{break_glass.REVIEW_STATE!r}, not to {ORIGIN!r} — the one "
            "sanctioned bypass is only a deferral while the card still comes "
            "back for the classification it skipped. Sent anywhere else it "
            "becomes a skip"
        )
    if not (break_glass.RECEIPT_LABEL or "").strip():
        problems.append(
            f"{break_glass.MARKER!r} writes no receipt, so nothing recorded the "
            "bypass — an escape hatch with a name and a record is a route, one "
            "without them is a hole"
        )

    # The flag half.
    text = workflow_text
    if text is None:
        try:
            with open(PLAN_WORKFLOW, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            problems.append(f"the planner workflow could not be read: {e}")
            text = None
    if text is not None:
        problems.extend(workflow_problems(text))
    return problems


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _read_reason(args) -> str | None:
    if args.why is not None:
        return args.why
    if not args.reason_file:
        return None
    try:
        with open(args.reason_file, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        # Reported, never fatal: a card whose reason file went missing still
        # needs a human, and a stranded card is the failure this route removes.
        print(f"could not read {args.reason_file}: {exc}", file=sys.stderr)
        return None


def _cmd_requeue(args) -> int:
    import linear_ops

    reason = _read_reason(args)
    why = refusal(reason)
    if why is not None:
        print(f"the stated reason is not fit for the card: {why}", file=sys.stderr)
        print(f"--- the classifier wrote ---\n{reason}", file=sys.stderr)
    requeue(linear_ops, args.identifier, reason)
    print(f"{args.identifier} stays in {ORIGIN} — recorded under {TRANSPORT_TAG}")
    return 0


def _cmd_escalate(args) -> int:
    import linear_ops

    reason = _read_reason(args)
    why = refusal(reason)
    if why is not None:
        # The raw text goes to the run log and nowhere near the card.
        print(f"the stated reason is not fit for the card: {why}", file=sys.stderr)
        print(f"--- the planner wrote ---\n{reason}", file=sys.stderr)
    outcome = escalate(linear_ops, args.identifier, reason, args.transport,
                       args.rewrite)
    if outcome.parked:
        print(f"{args.identifier} escalated out of {ORIGIN} → {destination()}")
    else:
        print(
            f"{args.identifier} left where it is — by the time this run went to "
            f"escalate it, the card had moved on from {ORIGIN}: {outcome.stood_down}"
        )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check")

    esc = sub.add_parser("escalate")
    esc.add_argument("identifier")
    esc.add_argument("--why", default=None)
    esc.add_argument("--reason-file", dest="reason_file", default=None)
    # DRE-3074: the same park, said honestly. A transport failure that outlived
    # its one retry still needs a person, and telling that person the reasoning
    # is the deliverable would be a confident wrong answer.
    esc.add_argument("--transport", action="store_true")
    # DRE-4058: the same park again, said honestly again. The one-off route's
    # bound asks for a rewritten CARD, and the note that parks it must not close
    # by telling the reader to answer it and move it back — that is the round
    # trip the bound was added to end. Passed by the step that already knows,
    # off `plan_critic`'s published action, never guessed from the reason text.
    esc.add_argument("--rewrite", action="store_true")

    req = sub.add_parser("requeue")
    req.add_argument("identifier")
    req.add_argument("--why", default=None)
    req.add_argument("--reason-file", dest="reason_file", default=None)

    args = parser.parse_args(argv)
    command = args.command or "check"

    if command == "check":
        problems = bypass_problems()
        for problem in problems:
            print(f"  [FAIL] {problem}")
        print(
            f"{len(label_census())} label(s) and the planner workflow checked "
            f"for a way past {ORIGIN}, {len(problems)} problem(s)"
        )
        return 1 if problems else 0

    if command == "escalate":
        return _cmd_escalate(args)

    if command == "requeue":
        return _cmd_requeue(args)

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
