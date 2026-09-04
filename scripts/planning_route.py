#!/usr/bin/env python3
"""Planning branches three ways on the shape (DRE-2844).

Every card arriving in Planning was planned as if it were an epic: it owed a
full plan artifact and it stopped in Green Light for the CEO. For a one-line
config change that is a planner run, a document, and a human decision spent on
something nobody needed to decide.

This module is the branch. It reads the shape stamped by `planning_shape.py`
(DRE-2843) and answers *which route does this card take out of Planning*:

  * **one-off** — checked, then straight to the build queue. No plan artifact,
    no green light, and it never reaches the CEO. Unless the check cannot route
    it at all, which is the one case where it does — see below.
  * **epic** — the existing path, unchanged: plan artifact, children, green
    light. `plan.yml` performs it; nothing here re-implements it.
  * **wave** — handed to the wave route, which owes a decomposition into epics
    before anything can be approved (DRE-2845 builds the far side).

## The routing is the file's, not this module's

`config/planning-shapes.json` already declares every shape's destination, the
actor accountable for it there, whether the sweep may promote it and the marks
the stamp applies — all of it bound to `config/lane-contract.json`. Repeating
any of that here would give the pipeline two answers to the same question, and
the one that drifts is always the copy. So `Route` READS the file, and the two
gates this card adds are DERIVED from it rather than tabulated per shape:

    a route stops for a green light  ⟺  the file names a HUMAN as the actor
                                        accountable at its destination

and the plan artifact is what that green light is given on, so a route with no
green light has nothing to give one on. Move `epic`'s actor to an agent in the
file and the CEO stop moves with it — which is what "changing the file changes
the routing" has to mean to be worth claiming.

## The trap this route must not walk back into

A one-off has no parent epic, and `reconcile.promote_ready` skips a card whose
parent epic is not in an active state. DRE-2735 made the gate verdict-driven
where there is no parent: the one-off's ROUTING VERDICT is its approval,
"written at Planning exit" — and this module is that exit. So the one-off route
carries the mechanical routing check with it and stamps what it decides. Absent
that verdict the most common card this design produces would sit in Backlog
forever, re-planned by the relevance-decay rule and landed right back in the
same hole, burning a planner run each cycle.

`tests/test_planning_route.py` exercises the promotion rather than trusting
2735 being closed.

## The verdict is READ OFF THE CARD, never defaulted (DRE-3038)

The check routes a one-off exactly as `routing_verdict.route()` routes a child:
role label, then the anchored title convention, then the acceptance criteria,
then NEEDS WORK when the card states none. It used to answer with one fixed
FLEET sentence for every one-off, because `mid_epic.is_epic()` read
`agent:planner` as "this is an epic" — and every card the relay dispatches here
from Planning carries that label, so the precedence never ran. DRE-3018 and
DRE-3020 were both stamped FLEET with zero `- [ ]` items between them; a card
whose criteria said "by hand" would have been too. That label says who OWNS the
card; the shape stamp says what it is, and the stamp is what this passes on.

A one-off nothing can route does not leave Planning quietly with a verdict
saying it is unbuildable. It takes the escalation exit with the reason — the
one exit that is not a plan (DRE-2848) — so the CEO, or the classifier, gets it
back. Nothing is stamped on that path: the card has not left the planning
segment, so it is not carrying a verdict out of it.

CLI:

    python3 scripts/planning_route.py check           # validate the routes
    python3 scripts/planning_route.py decide DRE-N [--github-output F]
    python3 scripts/planning_route.py exit DRE-N      # the one-off / wave exit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_contract  # noqa: E402
import planning_shape  # noqa: E402
import routing_verdict  # noqa: E402

# The record this module writes on a card it routes. Same grammar as
# `planning_shape.SHAPE_TAG` and `routing_verdict.VERDICT_TAG`, and
# deliberately NOT either of them: a note that opened with the shape marker
# would be read back as a second stamp on the card it is describing.
ROUTE_TAG = "planning-route"
ROUTE_MARK = "🚦"

# The lane contract's writer glossary calls `operator` "a human, working in
# Linear or running a script by hand". A shape whose accountable actor is one
# of these is a shape that waits for a person — which is the green light.
# Named here rather than inferred from prose, and checked against the glossary
# by `route_problems()`: rename the human in the contract and this fails
# loudly instead of quietly deciding that nothing stops for anyone.
HUMAN_ACTORS = ("operator",)


class Unroutable(Exception):
    """The card's shape cannot be read, so Planning has no route for it.

    Carries the notice `planning_shape.fault()` wrote and the tag it was
    surfaced under, so a caller posts it once per card without inferring which
    fault it is holding. Deliberately no route on it: defaulting an
    unclassified card would dispatch a one-off nobody classified, or spend the
    CEO's time on an epic nobody declared.
    """

    def __init__(self, notice: str, tag: str | None):
        super().__init__(notice)
        self.notice = notice
        self.tag = tag
        self.route = None


@dataclass(frozen=True)
class Route:
    """One shape's way out of Planning. Every field except the two gates is
    read straight from `config/planning-shapes.json`."""

    shape: str
    destination: str
    actor: str
    promotable: bool
    marks: tuple
    owes_green_light: bool
    owes_plan_artifact: bool

    @property
    def reaches_ceo(self) -> bool:
        """Does this card stop for the human? The whole point of the one-off
        is that the answer is no."""
        return self.owes_green_light


@dataclass(frozen=True)
class Exit:
    """What Planning does with a card it is NOT planning.

    `verdict` is the routing verdict to stamp on the way out, or None — which
    happens in two different cases the `reason` tells apart: the card already
    carries one, and the mechanical check could not decide.

    `escalation` is set when the card does not leave the planning segment at
    all: the check routed it back here, so a person is owed the reason instead
    of the card owing a verdict (DRE-2848). Nothing is stamped on that path —
    the card has not left Planning, so it is not carrying a verdict out of it.
    """

    route: Route
    destination: str
    verdict: str | None
    reason: str
    note: str
    escalation: str | None = None


# --------------------------------------------------------------------------- #
# the routes                                                                   #
# --------------------------------------------------------------------------- #


def _stops_for_a_human(actor: str) -> bool:
    return actor in HUMAN_ACTORS


def route_for(shape: str, doc: dict | None = None) -> Route:
    """The route a card of this shape takes. Raises `UnknownShape` for a word
    the vocabulary does not carry — never a default."""
    green_light = _stops_for_a_human(planning_shape.actor(shape, doc))
    return Route(
        shape=shape,
        destination=planning_shape.destination(shape, doc),
        actor=planning_shape.actor(shape, doc),
        promotable=planning_shape.is_promotable(shape, doc),
        marks=planning_shape.marks(shape, doc),
        owes_green_light=green_light,
        # The artifact IS what the green light is given on (plan.yml: "the plan
        # artifact — what the CEO green-lights"), so a route that stops for
        # nobody has nothing to give one on.
        owes_plan_artifact=green_light,
    )


def routes(doc: dict | None = None) -> tuple:
    """Every route, in the vocabulary's own order."""
    return tuple(route_for(name, doc) for name in planning_shape.shapes(doc))


def destinations(doc: dict | None = None) -> tuple:
    """Every lane this module can put a card in.

    `_cmd_exit` writes `plan.destination` — a lane the vocabulary picked, which
    means the destination is not readable at the call site. This is where it is
    readable, and `ready_lane_writers.py` (DRE-2859) asks for it by name: a
    writer whose lane is computed says which lanes it can reach, or it is
    reported as a writer nothing can check. Read from the file like everything
    else here, so a shape re-pointed at a new lane is covered by saying so once.

    The escalation lane is one of them (DRE-3038): a one-off the mechanical
    check cannot route does not leave Planning, it parks for a person — so this
    module can put a card there, and a census that did not say so would be
    wrong about a writer rather than merely quiet about it.
    """
    lanes = [route.destination for route in routes(doc)]
    try:
        lanes.append(_escalation_lane(doc))
    except Exception as exc:  # noqa: BLE001 — a census must not become a crash
        print(f"planning route: no escalation lane to report ({exc})", file=sys.stderr)
    return tuple(dict.fromkeys(lanes))


def route_problems(doc: dict | None = None) -> list:
    """Everything wrong with the routing, or an empty list.

    The vocabulary's own checks first — a destination that is not a lane, or an
    actor that lane does not permit, is a dead end whichever question you are
    asking. Then the one this module adds: exactly one route stops for the
    human. Every route agentic means no epic ever reaches the CEO; two means
    the front door is expensive again for whichever shape should have been
    cheap.
    """
    problems = list(planning_shape.config_problems(doc))

    glossary = set()
    try:
        glossary = set(lane_contract.writers())
    except Exception as e:  # noqa: BLE001 — an unreadable contract is a problem, not a crash
        problems.append(
            f"the lane contract could not be read, so no actor can be checked "
            f"against its writer glossary: {e}"
        )
    for human in HUMAN_ACTORS:
        if glossary and human not in glossary:
            problems.append(
                f"this module treats {human!r} as the human whose accountability "
                "IS the green light, and the lane contract's writer glossary "
                "does not carry that name — so no shape would stop for anyone"
            )

    try:
        fleet_verdict()
    except routing_verdict.RoutingError as e:
        problems.append(str(e))

    try:
        stopping = [r.shape for r in routes(doc) if r.owes_green_light]
    except planning_shape.ShapeError as e:
        problems.append(str(e))
        return problems
    if len(stopping) != 1:
        problems.append(
            "exactly one shape may stop for a green light; "
            f"{stopping or 'none'} do — a plan is what the CEO approves, and a "
            "vocabulary where every shape waits for a human is the expensive "
            "front door this branch removes"
        )
    return problems


# --------------------------------------------------------------------------- #
# reading a card                                                               #
# --------------------------------------------------------------------------- #


def decide(identifier: str, comment_bodies, doc: dict | None = None) -> Route:
    """The route this card takes out of Planning.

    Raises `Unroutable` when the shape cannot be read — no shape, two shapes,
    or a word the vocabulary does not carry. Those are three different faults
    with three different next actions, and `planning_shape.fault()` already
    writes the message for each; this only decides that none of them may be
    turned into a route.
    """
    notice = planning_shape.fault(identifier, comment_bodies, doc)
    try:
        shape = planning_shape.shape_on(comment_bodies, doc)
    except (planning_shape.ConflictingShapes, planning_shape.UnknownShape):
        shape = None
    if shape is None:
        raise Unroutable(notice or "", planning_shape.fault_tag(notice))
    return route_for(shape, doc)


# --------------------------------------------------------------------------- #
# leaving Planning                                                             #
# --------------------------------------------------------------------------- #


def fleet_verdict() -> str:
    """The one routing verdict the sweep promotes, derived rather than named.

    `config/routing-verdicts.json` declares exactly one promotable verdict, and
    `route_problems()` refuses a vocabulary that declares any other number — so
    this reads the file instead of restating FLEET in a second place.
    """
    promotable = [v for v in routing_verdict.verdicts() if routing_verdict.is_promotable(v)]
    if len(promotable) != 1:
        raise routing_verdict.RoutingError(
            "the routing vocabulary declares "
            f"{promotable or 'no'} promotable verdict(s) — a one-off leaves "
            "Planning carrying the one the sweep promotes, and there must be "
            "exactly one of those"
        )
    return promotable[0]


def _one_off_check(card: dict, comment_bodies, shape: str,
                   doc: dict | None = None) -> tuple:
    """`(verdict to stamp, why)` for a card leaving on the one-off route.

    The CARD decides, and there is no default (DRE-3038). This routes exactly
    as `routing_verdict.route()` routes a child of an epic — an explicit role
    label, then the anchored title convention, then the acceptance criteria,
    then NEEDS WORK when the card states none — and the shape is passed through
    so the stamp, not the `agent:planner` label, is what answers "is this an
    epic". Reading that label as epic-ness is what sent every one-off down the
    epic branch with no verdict, leaving this function to stamp one fixed
    sentence: about one dead build per misrouted one-off, observed on DRE-3018
    and DRE-3020.

    The one branch the shape still answers is the JUDGEMENT call — criteria
    that exist and name neither an interactive flow nor a live-state
    observation. There the card has stated an exit condition and nothing in it
    asks for a person, and a one-off is one card and one pull request, so an
    unattended agent can satisfy it. The reason names the criteria that were
    read, so the verdict comment says what decided rather than reciting a
    sentence. No model is asked: this run is the cheap route, and asking one
    would put the planner cost back into the shape that exists to avoid it.
    """
    carried = routing_verdict.verdicts_on(comment_bodies)
    if carried:
        return None, (
            f"the card already carries {' and '.join(carried)} — a card leaving "
            "Planning carries exactly one verdict"
        )
    description = card.get("description") or ""
    decision = routing_verdict.route(
        card.get("title") or "",
        description,
        card.get("labels") or (),
        bool(card.get("has_children")),
        doc,
        shape=shape,
    )
    if decision.verdict is not None:
        return decision.verdict, decision.reason
    return fleet_verdict(), _judgement_reason(description, doc)


def _judgement_reason(description: str, doc: dict | None = None) -> str:
    """Why a one-off whose criteria name neither signal is built by the fleet.

    Names the criteria it read — the branch that decided, on this card, in this
    card's own words. `criteria_verdict()` already reports the judgement call in
    the abstract; what a reader of the verdict comment needs is which criterion
    was weighed.
    """
    criteria = routing_verdict.acceptance_criteria(description)
    return (
        f"the card states {len(criteria)} acceptance "
        f"{'criterion' if len(criteria) == 1 else 'criteria'} and none of them "
        "names an interactive flow or a live-state observation, so an "
        "unattended agent can satisfy the exit condition as written — read on: "
        + "; ".join(f"“{c}”" for c in criteria)
    )


def _comes_back_to_planning(verdict: str, doc: dict | None = None) -> bool:
    """Does this verdict send the card back into the planning segment?

    DERIVED from the lane contract, not a list of verdict names: NEEDS WORK
    routes to Planning today, and a vocabulary that added another route home
    would be covered without anybody remembering to widen a condition. Every
    other verdict names a lane in the work segment, where somebody — the fleet,
    or an operator — picks the card up.
    """
    try:
        lane = lane_contract.lane(routing_verdict.destination(verdict, doc))
    except (lane_contract.UnknownLane, routing_verdict.UnknownVerdict):
        return False
    return lane.get("segment") == "planning"


def _escalation_lane(doc: dict | None = None) -> str:
    """Where a card that cannot be routed parks — the lane a plan waits in.

    Imported late on purpose: `planning_escalation` reads THIS module to derive
    that lane (exactly one route stops for a human), so importing it at the top
    would close a cycle. Late is still one source: the derivation stays there.
    """
    import planning_escalation

    return planning_escalation.destination(doc)


def _escalation_reason(reason: str) -> str:
    """The plain-English reason the CEO reads on a one-off nothing can route.

    Hand-planning is an escalation and nothing else (DRE-2848), and a one-off
    the mechanical check sends back to Planning is that case: it is one card
    and one pull request, and it still cannot be handed to anyone as written.
    The check's own reason carries the missing thing, so it is repeated rather
    than summarised — `planning_escalation.refusal()` reads the text before it
    reaches the card, so a reason that is not fit to show never shows.
    """
    return (
        "This card is one card and one pull request, so there is no plan to "
        "write for it — but it cannot go to the build queue as written: "
        f"{reason} Until that is settled nobody can tell whether the work has "
        "been done, so it is with you rather than in the queue."
    )


def _one_off_note(route: Route, verdict: str | None, reason: str) -> str:
    """What the card says about itself afterwards. Plain English: the person
    who filed it reads this, and they are owed an outcome, not a diff."""
    lines = [
        f"{ROUTE_MARK} {ROUTE_TAG}: **{route.shape}** — {planning_shape.means(route.shape)}",
        "",
        "This card needs no plan document and no green light. It is one card "
        "and one pull request, so there is nothing for anyone to approve "
        "before it is built.",
        "",
        f"**Where it goes:** {route.destination}. "
        f"**Who takes it from there:** {route.actor}.",
    ]
    if verdict:
        lines += ["", f"**Checked on the way out:** routed **{verdict}** — {reason}"]
        if not routing_verdict.is_promotable(verdict):
            # The check said no. Say it plainly here too: the card is leaving
            # Planning either way, and the person reading it should not have to
            # infer from silence that nothing will pick it up.
            lines.append(
                "So this one does not go to the build queue after all — "
                f"{routing_verdict.record(verdict)['means']} "
                f"**Who handles it:** {routing_verdict.actor(verdict)}."
            )
    else:
        lines += ["", f"**Checked on the way out:** {reason}."]
    return "\n".join(lines)


def _wave_note(route: Route) -> str:
    return "\n".join([
        f"{ROUTE_MARK} {ROUTE_TAG}: **{route.shape}** — {planning_shape.means(route.shape)}",
        "",
        "This is too big to approve as one plan: what a green light would be "
        "given on is not written yet. So it is handed to the wave route, which "
        "owes a decomposition into epics before anyone is asked to approve "
        "anything.",
        "",
        f"**Where it goes:** {route.destination}. "
        f"**Who takes it from there:** {route.actor}.",
    ])


def exit_plan(card: dict, comment_bodies, doc: dict | None = None) -> Exit:
    """How this card leaves Planning, for the two routes that leave here.

    The EPIC route is refused: its exit is the existing path in `plan.yml` —
    artifact, children, green light — and a second implementation of it here is
    exactly the quiet alteration this card must not make.
    """
    identifier = card.get("identifier") or ""
    route = decide(identifier, comment_bodies, doc)
    if route.owes_plan_artifact:
        raise ValueError(
            f"{identifier or 'this card'} is shaped {route.shape}, whose exit is "
            "the plan run itself — artifact, children, green light. There is "
            "nothing for this module to perform."
        )
    if route.shape == "one-off":
        verdict, reason = _one_off_check(card, comment_bodies, route.shape, doc)
        if verdict is not None and _comes_back_to_planning(verdict, doc):
            return Exit(
                route=route,
                destination=_escalation_lane(doc),
                verdict=verdict,
                reason=reason,
                note="",
                escalation=_escalation_reason(reason),
            )
        note = _one_off_note(route, verdict, reason)
    else:
        verdict, reason = None, "a wave owes a decomposition before anything is built"
        note = _wave_note(route)
    return Exit(
        route=route,
        destination=route.destination,
        verdict=verdict,
        reason=reason,
        note=note,
    )


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _write_outputs(path: str | None, pairs) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for key, value in pairs:
                fh.write(f"{key}={' '.join(str(value).split())}\n")
    except OSError as exc:
        print(f"planning route: could not write step outputs: {exc}")


def _say_once(lops, identifier: str, tag: str | None, body: str) -> None:
    """Post `body` at most once per card, keyed on its own tag. A run that is
    retried must not turn one fault into a thread."""
    if tag and lops.count_comments(identifier, tag):
        print(f"{identifier}: already told, under {tag}")
        return
    lops.cmd_comment(identifier, body)


def _cmd_decide(identifier: str, github_output: str | None) -> int:
    import linear_ops

    bodies = linear_ops.comment_bodies(identifier)
    try:
        route = decide(identifier, bodies)
    except Unroutable as refusal:
        # Refused, not defaulted, and not a red run: a card nobody has
        # classified is owed a message, not a failed workflow that summons the
        # medic. The notice names the missing stamp and how to write it.
        _say_once(linear_ops, identifier, refusal.tag, refusal.notice)
        _write_outputs(github_output, [("refused", "true"), ("route", "")])
        print(refusal.notice, file=sys.stderr)
        return 0

    _write_outputs(github_output, [
        ("refused", "false"),
        ("route", route.shape),
        ("destination", route.destination),
        ("actor", route.actor),
        ("plan_artifact", "true" if route.owes_plan_artifact else "false"),
        ("green_light", "true" if route.owes_green_light else "false"),
    ])
    print(json.dumps({
        "shape": route.shape,
        "destination": route.destination,
        "actor": route.actor,
        "promotable": route.promotable,
        "marks": list(route.marks),
        "plan_artifact": route.owes_plan_artifact,
        "green_light": route.owes_green_light,
    }, indent=2))
    return 0


def _cmd_exit(identifier: str) -> int:
    import critic_score
    import linear_ops

    # Read the card WHOLE: the routing check reads the acceptance criteria, and
    # the list API truncates a description without saying so.
    card = critic_score.read_card(linear_ops, identifier)
    bodies = linear_ops.comment_bodies(identifier)
    plan = exit_plan(card, bodies)

    if plan.escalation is not None:
        # The card does not leave Planning, so it is stamped with nothing and
        # marked with nothing: it takes the one exit that is not a plan, and a
        # person gets it back with the reason (DRE-2848). Posted once and the
        # move re-asserted, by the module that owns that route.
        import planning_escalation

        planning_escalation.escalate(linear_ops, identifier, plan.escalation)
        print(
            f"{identifier} does not leave Planning on the {plan.route.shape} "
            f"route — {plan.reason} It is escalated to {plan.destination}."
        )
        return 0

    if plan.verdict:
        refusal = routing_verdict.stamp_refusal(plan.verdict, bodies)
        if refusal is None:
            linear_ops.cmd_comment(
                identifier, routing_verdict.verdict_comment(plan.verdict, plan.reason)
            )
        else:
            print(f"not stamping {plan.verdict}: {refusal}")
    for label in plan.route.marks:
        linear_ops.add_label(identifier, label)
    _say_once(linear_ops, identifier, ROUTE_TAG, plan.note)
    linear_ops.cmd_state(identifier, plan.destination)
    print(
        f"{identifier} leaves Planning on the {plan.route.shape} route → "
        f"{plan.destination} (handled there by {plan.route.actor})"
    )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check")

    decide_cmd = sub.add_parser("decide")
    decide_cmd.add_argument("identifier")
    decide_cmd.add_argument("--github-output", default=None)

    exit_cmd = sub.add_parser("exit")
    exit_cmd.add_argument("identifier")

    args = parser.parse_args(argv)
    command = args.command or "check"

    if command == "check":
        problems = route_problems()
        for problem in problems:
            print(f"  [FAIL] {problem}")
        print(
            f"{len(routes())} route(s) checked against the shape vocabulary and "
            f"the lane contract, {len(problems)} problem(s)"
        )
        return 1 if problems else 0

    if command == "decide":
        return _cmd_decide(args.identifier, args.github_output)

    if command == "exit":
        return _cmd_exit(args.identifier)

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
