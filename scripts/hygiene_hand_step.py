#!/usr/bin/env python3
"""The hygiene agent's Hand-work class lane (DRE-8650): every operator step in
`Hand-work` is marked as one a machine can do or one a person must.

Discovered by the core's glob (`scripts/hygiene.py`), registered nowhere. The
core's board read covers `Hand-work` (`hygiene.board_lanes`), and hygiene runs
from bureau-pipeline's own hourly workflow as one leg per owner, so the lane
classes the Hand-work cards of every repo from its merge; each leg keeps its
own cards by `hygiene.in_scope`. A card is classed when all of these hold:

1. it is in `Hand-work`;
2. it carries `operator-step`, or its newest `🧭 routing-verdict` in the board
   read's comment window is one whose actor is the operator
   (`routing_verdict.actor`);
3. it wears neither class label, `MACHINE_LABEL` or `PERSON_LABEL`;
4. it is not a `PROOF:` card, and not a standing card in
   `config/hand-work-age.json`;
5. it does not wear `hand-built`, the CEO's own mark for work a named session
   is building.

The class is `operator_step_kind.classify` over the card's title and its
description read whole: the board read's `description` is the list API's, cut
at 500 characters (`board_snapshot`), and the phrase that decides a card often
sits past the cut. So each card that qualifies, and only such a card, is read
once more through the read-only `ctx.linear` (`ISSUE_QUERY`). A read that fails
skips that card for this pass — said on stderr — and it is never classed from
the cut text, which could miss a person phrase and claim a machine step. Each
classed card is one `hygiene.Action` of two writes: the label
`hand-step:<class>`, then hygiene's own receipt, whose cause is `<class>, kind
<kind>` and whose evidence is the phrase that decided it — with any `·` or line
break in it read as a space, because the receipt refuses both.

The core's (tag, cause) key and the label's presence both keep a card from
being classed twice. The lane reads nothing beyond the board and each such
card's description, moves no card,
dispatches nothing, and changes no lane: the class changes nobody's job.
"""

from __future__ import annotations

import json
import re
import sys

import hygiene
import linear_ops
import operator_step_kind
import proof_and_demo
import reconcile
import routing_verdict

LANE = hygiene.HAND_WORK

ACT = "hygiene-hand-step-class"

MACHINE_LABEL = "hand-step:machine"
PERSON_LABEL = "hand-step:person"
#: Class → the label that marks it. Exactly one is on a classed card.
LABELS = {operator_step_kind.MACHINE: MACHINE_LABEL, operator_step_kind.PERSON: PERSON_LABEL}

#: One card's whole description — the list API's is cut at 500 characters.
ISSUE_QUERY = "query($id: String!) { issue(id: $id) { identifier description } }"

#: What the receipt cannot carry in a quoted phrase: its clock separator, and
#: a line break.
_UNQUOTABLE = re.compile(r"[·\r\n]+")


def standing() -> set:
    """The standing cards `config/hand-work-age.json` exempts."""
    with open(reconcile.HAND_WORK_AGE_CONFIG, encoding="utf-8") as fh:
        return {row["card"] for row in json.load(fh)["standing"]}


def _marks(card: dict) -> set:
    return {name.lower() for name in hygiene._labels(card)}


def operators(card: dict) -> bool:
    """`operator-step`, or a newest live verdict whose actor is the operator."""
    if routing_verdict.OPERATOR_STEP_LABEL in _marks(card):
        return True
    bodies = [n.get("body") or "" for n in
              hygiene.linear_ops.window_nodes(card.get("comments"))]
    # Newest first, so the first verdict seen is the newest.
    found = routing_verdict.verdicts_on(list(reversed(bodies)))
    return bool(found) and routing_verdict.actor(found[0]) == "operator"


def qualifies(card: dict, exempt: set) -> bool:
    marks = _marks(card)
    return (card.get("identifier") not in exempt
            and not marks & {label.lower() for label in LABELS.values()}
            and routing_verdict.HAND_BUILT_LABEL.lower() not in marks
            and not proof_and_demo.is_proof(card.get("title"))
            and operators(card))


def quoted(evidence: str) -> str:
    """The phrase as a receipt can carry it."""
    return " ".join(_UNQUOTABLE.sub(" ", evidence).split())


def description(card: dict, ctx: hygiene.Context) -> str | None:
    """The card's whole description — one `ctx.linear` read. None when the read
    fails, said on stderr: the card waits for the next pass."""
    ident = card["identifier"]
    try:
        data = ctx.linear(ISSUE_QUERY, {"id": ident}) or {}
    except (linear_ops.LinearError, OSError) as e:  # LinearRateLimited is one too
        print(f"hygiene: hand-step — {ident} skipped this pass, its description could "
              f"not be read: {e}", file=sys.stderr)
        return None
    issue = data.get("issue")
    if not isinstance(issue, dict):
        print(f"hygiene: hand-step — {ident} skipped this pass, Linear returned no "
              f"card for it", file=sys.stderr)
        return None
    return issue.get("description") or ""


def classed(card: dict, text: str, ctx: hygiene.Context) -> hygiene.Action:
    cls, kind, evidence = operator_step_kind.classify(card.get("title"), text)
    label = MACHINE_LABEL
    if cls == operator_step_kind.PERSON:
        label = PERSON_LABEL
    cause = f"{cls}, kind {kind}"
    evidence = [quoted(evidence) or operator_step_kind.NO_MATCH]
    return hygiene.Action(
        lane=LANE, target=card["identifier"], act=ACT, cause=cause, evidence=evidence,
        writes=[hygiene.linear_label(card, label, add=True),
                hygiene.linear_comment(card, hygiene.receipt(ACT, cause, evidence,
                                                             ctx.now))])


def plan(board: hygiene.Board, ctx: hygiene.Context) -> list:
    try:
        exempt = standing()
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"hygiene: hand-step — skipped this pass, the standing cards could not be "
              f"read: {e}", file=sys.stderr)
        return []
    actions = []
    for card in board.cards(ctx, LANE):
        if not qualifies(card, exempt):
            continue
        text = description(card, ctx)
        if text is not None:
            actions.append(classed(card, text, ctx))
    return actions
