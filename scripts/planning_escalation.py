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

CLI:

    python3 scripts/planning_escalation.py check
    python3 scripts/planning_escalation.py escalate DRE-N --why "…"
    python3 scripts/planning_escalation.py escalate DRE-N --reason-file <path>
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import break_glass  # noqa: E402
import lane_contract  # noqa: E402
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

# The lane the escalation leaves FROM. Hand-planning is an escalation OUT of
# Planning, so the card has been through Planning by the time it parks — which
# is also the lane `break-glass` repays its skipped classification to, and
# `bypass_problems` binds the two rather than letting them drift apart.
ORIGIN = "Planning"

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


def escalation_comment(identifier: str, reason: str | None) -> str:
    """The note that IS the escalation. One card, one of these.

    Written to `standards/comms.md`: purpose in the first sentence, the reason
    in its own block, and exactly one ask as the closing line.
    """
    lane = destination()
    lines = [
        f"{ESCALATION_MARK} {ESCALATION_TAG}: {identifier} needs a decision from "
        "you before it can be planned — the reasoning itself is the deliverable "
        "here, and that part is not work an agent can do.",
        "",
    ]
    why = refusal(reason)
    if why is None:
        lines += [f"**Why it needs you:** {(reason or '').strip()}", ""]
    elif not (reason or "").strip():
        lines += [f"**Why it needs you:** {NO_REASON_STATED}", ""]
    else:
        lines += [f"**Why it needs you:** {NOT_PLAIN_ENGLISH}", ""]
    lines += [
        f"This card is parked in **{lane}** — your decision queue, the same "
        "place a plan waits for you. It is not broken and it has not failed "
        "anything; it is correct and waiting on judgement.",
        "",
        "Answer it here and move the card back to be picked up, or park it if "
        "we should not do this at all.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the escalation itself                                                        #
# --------------------------------------------------------------------------- #


def escalate(linear_ops, identifier: str, reason: str | None) -> bool:
    """Post the escalation and park the card. True when the note was written.

    The note lands BEFORE the move, always: moving the card without the
    question is a silent park, and the CEO sees something appear in their queue
    with nothing to answer. Posted at most once per card, keyed on the tag —
    a retried run must converge rather than turn one decision into a thread —
    and the move is re-asserted every time, because the crash this guards
    against is the one between the two writes.
    """
    lane = destination()
    already = 0
    try:
        already = linear_ops.count_comments(identifier, ESCALATION_TAG)
    except Exception as exc:  # noqa: BLE001 — a read failure must not strand the card
        print(f"{identifier}: could not read prior escalations ({exc})", file=sys.stderr)
    posted = False
    if already:
        print(f"{identifier}: already escalated, under {ESCALATION_TAG}")
    else:
        linear_ops.cmd_comment(identifier, escalation_comment(identifier, reason))
        posted = True
    linear_ops.cmd_state(identifier, lane)
    return posted


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


def _cmd_escalate(args) -> int:
    import linear_ops

    reason = _read_reason(args)
    why = refusal(reason)
    if why is not None:
        # The raw text goes to the run log and nowhere near the card.
        print(f"the stated reason is not fit for the card: {why}", file=sys.stderr)
        print(f"--- the planner wrote ---\n{reason}", file=sys.stderr)
    escalate(linear_ops, args.identifier, reason)
    print(f"{args.identifier} escalated out of {ORIGIN} → {destination()}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check")

    esc = sub.add_parser("escalate")
    esc.add_argument("identifier")
    esc.add_argument("--why", default=None)
    esc.add_argument("--reason-file", dest="reason_file", default=None)

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

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
