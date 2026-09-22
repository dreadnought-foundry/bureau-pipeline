#!/usr/bin/env python3
"""An approved epic's children carry their routing verdicts (DRE-4593).

`standards/card-quality.md` says every card leaving the planning segment carries
exactly one routing verdict, written by the planning-exit writer. For a
planner-created epic's children, nothing wrote one. The planner creates them
with `linear_ops.py subissue`; the only verdict-writer that ran on its own was
`proof_and_demo.py check --stamp`, which stamps the PROOF card and nothing else.
So every BUILD child reached Backlog verdictless and the sweep refused it:

    🚨 routing-no-verdict: <CARD> carries no routing verdict, so nothing has
    said who builds it

What that cost, measured on the board:

  * DRE-4425, approved 2026-09-20 22:28 PT — five cards, none of them started
    as of 2026-09-22 09:25 PT, about 35 hours later.
  * DRE-4467, the round robin, approved 2026-09-21 20:03 PT — all seven build
    cards frozen until a person stamped them by hand: four by an assistant
    session, three by the CEO running the script, because the assistant
    session may not write them.
  * DRE-3270 and DRE-3301 refused the same way in the 2026-09-22 sweep logs.

This module is the batch stamper the plan run calls once the children exist.

## It classifies nothing of its own

The answer comes from `planning_route.mechanical_verdict()` — the vocabulary's
own strict precedence (role label, anchored title convention, acceptance
criteria) and, for a judgement call, the one-off exit's own answer. That
function is SHARED with the one-off planning exit rather than copied, so a card
cannot be routed two ways depending on which door it came through. Reading the
precedence a second time here is how two readings drift apart, and the
vocabulary already carries the incident that taught it (DRE-2831).

Sharing the function is not enough on its own: it has to be called the same way
(DRE-4593 review). The one-off door passes `shape="one-off"`; this one passed no
shape at all, so `mid_epic.is_epic` fell back to an UNANCHORED `[epic]` in the
title, and a child carrying that literal took the epic branch and was stamped
FLEET past its own `agent:ops` label — precedence 1, the level `route()`
guarantees decides first. A planner's child IS one card and one pull request
(below), so the shape is passed, and the two doors answer identically.

## The judgement call is the load-bearing case, and it is not silent

`routing_verdict.route()` returns no verdict when the criteria name neither an
interactive flow nor a rendered outcome. That is not a corner: EVERY ONE of
DRE-4467's seven build cards read that way, so a batch that left the undecidable
case unstamped would have changed nothing for the epic that cost the most. The
answer is the one `planning_route` already gives a one-off — the promotable
verdict, with the criteria it weighed NAMED in the reason, so the comment says
what decided rather than reciting a sentence. A planner's child is one card and
one pull request by construction (the plan prompt decomposes an epic into
exactly that), which is the same premise the one-off branch rests on.

A card the vocabulary sends back to Planning (NEEDS WORK — it states no exit
condition at all) is stamped with NOTHING, exactly as `planning_route.exit_plan`
stamps nothing there: the card has not left the planning segment, so it is not
carrying a verdict out of it. FLEET dispatches an agent, so the wrong default
there is the expensive one.

NOTHING UPSTREAM CATCHES THAT CHILD, and this module used to say it did
(DRE-4593 review). `validate_card.child_problems` reads the repo label, the role
label, the title/repo match and `linear_ops.body_problem` — it never looks for a
`- [ ]` criterion, so a child with a heading, some prose and no criteria passes
`check-children` and arrives here unstampable. Silence then costs exactly what
this card exists to stop: a line in a run log nobody reads, a green step, an
approved epic, and a card frozen in Backlog on `routing-no-verdict`. So every
child this step will not stamp — and that nobody else writes either — is named
in `withheld_comment()`, which the plan run posts ON THE EPIC, in the same run,
before the CEO is asked to approve it. The card still stops; what changes is
that it stops somewhere a person is already looking.

## The closing cards are not this step's to write

A `PROOF:` child — and a legacy `DEMO:` child — belong to `proof_and_demo.py`,
which computes their verdicts from the same vocabulary and refuses to stamp one
the fleet could pick up. This step skips them BY TITLE, whichever of the two
runs first, so the ordering is safe from both ends rather than only from the one
the workflow chose. In `plan.yml` it runs AFTER the proof gate, because that
gate bounces a malformed plan back to Planning and an epic on its way back is
not an epic whose cards get routing decisions written on them — the same rule
`proof_and_demo.stamps()` applies to its own card. Running it first would also
have to be careful about a proof card whose criteria route FLEET; skipping by
title means it does not have to be.

## One write path

`routing_verdict.stamp_card` is the whole write — the comment and the marks the
verdict declares — and it REFUSES a card that already carries a verdict, so a
re-planned epic, a second pass, and the activation backstop all cost a read and
write nothing. A second implementation of "post the comment and apply the marks"
would be two writers of one record, free to disagree about the labels.

CLI:

    python3 scripts/linear_ops.py children-detail DRE-N > children.json
    python3 scripts/plan_child_verdicts.py stamp --epic DRE-N \\
      [--comment-file note.md] [--no-stamp] < children.json

The read goes into a FILE and the stamper is fed from it — never a pipe. A
GitHub Actions step is `bash -e {0}` and NOT `pipefail` unless it asks, so a
pipe hands the step the stamper's exit status alone and a crashed read reaches
it as empty stdin. That is why empty stdin is refused rather than defaulted
(`_cmd_stamp`), and why all three call sites in `plan.yml` redirect.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import lane_contract  # noqa: E402
import planning_route  # noqa: E402
import proof_and_demo  # noqa: E402
import routing_verdict  # noqa: E402


@dataclass(frozen=True)
class ChildVerdict:
    """One child's routing decision, and whether this step writes it.

    `write` is False in three cases and they are different, which is why `why`
    is carried either way: a closing card belongs to another writer, a NEEDS
    WORK card has not left the planning segment, and a card the vocabulary
    reads as an epic gets no buildability verdict at all. All three are
    printed, so a run's log says what happened to every child rather than to
    the ones it stamped.

    `held` splits those three by who is owed the news. A closing card is
    another writer's and nothing is wrong with it. The other two leave a card
    with NO verdict and nobody scheduled to write one, which freezes it in
    Backlog — so they go in the note posted on the epic (`withheld_comment`).
    """

    identifier: str
    verdict: str | None
    why: str
    write: bool
    held: bool = False


def _ident(card: dict) -> str:
    return (card or {}).get("identifier") or "(unidentified card)"


def _comes_back_to_planning(verdict: str, doc: dict | None = None) -> bool:
    """Does this verdict send the card back into the planning segment?

    Derived from the lane contract, never a list of verdict names — the same
    read `planning_route._comes_back_to_planning` makes, for the same reason: a
    vocabulary that added another route home is covered without anybody
    remembering to widen a condition.
    """
    try:
        lane = lane_contract.lane(routing_verdict.destination(verdict, doc))
    except (lane_contract.UnknownLane, routing_verdict.UnknownVerdict):
        return False
    return lane.get("segment") == "planning"


def verdicts_for(children, doc: dict | None = None) -> tuple:
    """A `ChildVerdict` for every child, in the order the planner created them.

    Pure: no Linear read and no write, so the plan run, the scenario walk and
    the tests all decide the same way. `children` are the records
    `linear_ops.py children-detail` prints — `identifier`, `title`, `body`,
    `labels`, `blocked_by`.
    """
    out: list[ChildVerdict] = []
    for card in children or []:
        identifier = _ident(card)
        title = card.get("title") or ""
        if proof_and_demo.is_proof(title) or proof_and_demo.is_demo(title):
            out.append(ChildVerdict(
                identifier, None,
                "the epic's closing card — proof_and_demo.py computes and "
                "writes its verdict, and deliberately writes none where the "
                "answer would let the fleet pick the card up. Nothing here "
                "touches it, whichever of the two runs first.",
                False,
            ))
            continue
        verdict, reason = planning_route.mechanical_verdict(
            title, card.get("body") or "", card.get("labels") or (), doc=doc,
            shape=planning_route.ONE_OFF_SHAPE,
        )
        if verdict is None:
            # The epic branch. The shape above puts it out of reach from here,
            # and this is what happens if that ever stops being true: the
            # vocabulary has said the card is not a unit of build, so there is
            # no verdict to invent for it and nothing downstream routes it
            # either. Falling through to the judgement default instead is how a
            # card carrying `[epic]` in its title was stamped FLEET past an
            # explicit role label (DRE-4593 review).
            out.append(ChildVerdict(
                identifier, None,
                f"not routed — {reason} A child of an epic that reads as an "
                "epic itself is a planning defect, so it is stamped with "
                "nothing and named on the parent.",
                False, held=True,
            ))
            continue
        if _comes_back_to_planning(verdict, doc):
            out.append(ChildVerdict(
                identifier, None,
                f"routed {verdict} — {reason} That is a card going back to "
                "Planning, not out of it, so it is stamped with nothing and "
                "the sweep's own refusal holds it where a person reads it.",
                False, held=True,
            ))
            continue
        out.append(ChildVerdict(
            identifier, verdict,
            f"a child of the epic, routed by the planning run off the card "
            f"itself: {reason}",
            True,
        ))
    return tuple(out)


def writes(children, doc: dict | None = None, records=None):
    """`(record, written)` for every child, in order — the one write path.

    `written` is None for a record this step does not write, True where
    `stamp_card` wrote the verdict, and False where it refused a card that
    already carries one. The CLI prints from this rather than from its
    intention: on a second and third pass the two differ, and the run log used
    to read as though verdicts had been written when nothing was.

    `records` lets a caller that has already computed them hand them over
    rather than have `verdicts_for` run twice. Both are pure, so it changes
    nothing but the work.
    """
    for record in (verdicts_for(children, doc) if records is None else records):
        if not record.write:
            yield record, None
            continue
        yield record, routing_verdict.stamp_card(
            record.identifier, record.verdict, record.why) == 0


def write_verdicts(children, doc: dict | None = None, records=None) -> int:
    """Write each computed verdict onto its card. Returns how many were written.

    A card that already carries one is refused by `stamp_card`, which says so on
    stderr and returns 1 — not an error: a re-planned epic and the activation
    backstop both run this again, and the gate that actually holds a card is
    `promotion_refusal`, which refuses two verdicts as loudly as none.

    A failed WRITE is different and does propagate, for the reason
    `proof_and_demo.write_stamps` gives: a Linear write that did not land
    decided nothing about this plan (standards/console-honesty.md rule 1).
    """
    return sum(1 for _, written in writes(children, doc, records) if written)


# --------------------------------------------------------------------------- #
# the children nobody will stamp                                               #
# --------------------------------------------------------------------------- #

WITHHELD_TAG = "children-without-a-verdict"
WITHHELD_MARK = "🚧"


def withheld_comment(epic: str, held) -> str:
    """The note posted on the epic for every child no writer will stamp.

    Raises on an empty list, for the reason `proof_and_demo.bounce_comment`
    gives: a note with nothing to say is noise on a card a person is about to
    read.

    Plain English, because the CEO reads the epic before he approves it: what
    it means, which cards, and the one thing that fixes them. No verdict name
    in the first line — a reader should not have to know the vocabulary to know
    that some of the cards will not start.
    """
    if not held:
        raise ValueError(
            "refusing to write a note with no card in it — the epic is only "
            "told about children that are actually stuck"
        )
    count = len(held)
    headline = (
        "one of this epic's cards will not start on its own" if count == 1
        else f"{count} of this epic's cards will not start on their own"
    )
    lines = [
        f"{WITHHELD_MARK} {WITHHELD_TAG}: {headline}.",
        "",
        "Every card leaving planning carries one line saying who builds it — "
        "the fleet, or a person. The sweep refuses a card that has none, and "
        "nothing here can write one, so this work will sit in Backlog until "
        "somebody looks at it:",
        "",
    ]
    lines += [f"- **{record.identifier}** — {record.why}" for record in held]
    lines += [
        "",
        "What fixes it: give the card acceptance criteria — checkbox lines "
        "saying what must be true for it to be done. The next plan run writes "
        "the verdict from those, and nothing already written has to be undone.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _cmd_stamp(args) -> int:
    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not raw:
        # "Nothing arrived" and "the epic has no children" are different facts,
        # and this step is only ever invoked on an epic that HAS children — the
        # plan-time gate is `steps.kids.outputs.count != '0'` and the backstop
        # runs on an epic being activated. Defaulting to `[]` turned a crashed
        # `children-detail` into `0 of 0 stamped` and a green step, which is
        # standards/console-honesty.md rule 1 exactly: the absence was inferred
        # from a read that failed rather than read off an answer Linear gave.
        print(
            f"{args.epic}: nothing arrived on stdin. Refusing to read that as "
            "an epic with no children — the read that should have produced "
            "them did not, and this step will not report a plan stamped when "
            "it has written nothing.",
            file=sys.stderr,
        )
        return 1
    children = json.loads(raw)
    records = verdicts_for(children)
    if args.stamp:
        written = 0
        for record, wrote in writes(children, records=records):
            if wrote is None:
                print(f"{record.identifier}: not stamped here — {record.why}")
            elif wrote:
                written += 1
                print(f"{record.identifier}: {record.verdict} — {record.why}")
            else:
                print(f"{record.identifier}: already carries a verdict — this "
                      "step wrote nothing")
        print(
            f"{args.epic}: {written} of {len(records)} child card(s) stamped "
            f"({sum(1 for r in records if not r.write)} left to another writer "
            "or held in Planning, the rest already carrying a verdict)"
        )
    else:
        for record in records:
            if record.write:
                print(f"{record.identifier}: would stamp {record.verdict} — "
                      f"{record.why}")
            else:
                print(f"{record.identifier}: not stamped here — {record.why}")
    held = [record for record in records if record.held]
    if held and args.comment_file:
        with open(args.comment_file, "w", encoding="utf-8") as fh:
            fh.write(withheld_comment(args.epic, held))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")

    stamp = sub.add_parser("stamp", help="the epic's children, on stdin")
    stamp.add_argument("--epic", required=True)
    stamp.add_argument("--comment-file", default=None,
                       help="where to write the note naming the children no "
                            "writer will stamp, when there are any")
    stamp.add_argument("--no-stamp", dest="stamp", action="store_false",
                       default=True,
                       help="read only — print the decisions, write nothing")
    stamp.set_defaults(fn=_cmd_stamp)

    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_usage(sys.stderr)
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
