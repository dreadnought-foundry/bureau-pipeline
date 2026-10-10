#!/usr/bin/env python3
"""The `nothing-to-change` class's action module (DRE-6458).

A build agent that finds every acceptance criterion of its card already met on
the default branch has nothing to open a pull request for, so it writes a
`nothing-to-change` blocker. The brief (DRE-6443) has it write one line per
criterion under the stamp,

    - [x] <criterion> — <what on the default branch satisfies it>

and the poster (DRE-6444) quotes those lines in the marker. The agent's stamp
alone is not enough to close a card on, so this module counts the attested
lines against the card's own criteria and decides on the count:

  * every criterion attested — the card is Canceled, the reason quoted in the
    resolver's receipt. The write is conditional on the lane and the label the
    sweep read, so a card a person moved or held since is never canceled
    over them. A card under an epic is canceled the same way: Canceled is
    terminal for a `blockedBy` relation, so the epic's proof card is released,
    and that proof's record must show every criterion met.
  * fewer attested, none (a note written before DRE-6443 asked for the lines —
    DRE-5195's is one), or a card with no criteria to attest against — the
    card goes to Planning to be re-read, and the lane is read back before the
    resolver may say so.
  * fewer attested, and the planner has already had this card for this reason
    (the resolver's earlier `replanned` receipt is on the thread) — `None`, a
    person's call, which the resolver asks in Green Light.

The criteria are read off the card dict the sweep hands in: `backlog_children`
selects `description`, and the resolver re-reads the card live before the call.
`linear_ops.get_issue` selects no description, so it is never asked for them.

The module posts nothing: the resolver (DRE-6508) writes the receipt after a
non-`None` return, and the sweep's resolver imports this module by the name
`config/blocker-classes.json` gives it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blocker_class  # noqa: E402
import checkbox_marks  # noqa: E402
import dead_run  # noqa: E402
import linear_ops  # noqa: E402
import routing_verdict  # noqa: E402

#: The resolver's receipt for this class's hand-off to Planning, matched by
#: its words — the receipt's tag is the resolver's alone to name.
REPLANNED = "class=nothing-to-change action=replanned"

#: How much of the agent's reason a cancel note quotes.
QUOTE = 200


def attested(reason: str) -> int:
    """The lines of `reason` that attest a criterion: a checked list item."""
    count = 0
    for line in (reason or "").splitlines():
        match = checkbox_marks.ITEM.match(line)
        if match and checkbox_marks.is_checked(match.group("mark")):
            count += 1
    return count


def _replanned_before(card: dict) -> bool:
    return any(REPLANNED in (node.get("body") or "")
               for node in linear_ops.window_nodes(card.get("comments")))


def resolve(card: dict, reason: str, *, repo: str) -> tuple[str, str] | None:
    """Cancel a card whose every criterion the agent attested; send any other
    to Planning once. See the module docstring."""
    identifier = card["identifier"]
    criteria = len(routing_verdict.acceptance_criteria(card.get("description") or ""))
    lines = attested(reason)
    if criteria and lines >= criteria:
        if not linear_ops.cmd_state(identifier, "Canceled", expect=("Backlog",),
                                    labels_absent=(dead_run.HOLD_LABEL,)):
            raise blocker_class.NotNow("Linear refused the Canceled write")
        return "canceled", f"the agent attested every criterion: {(reason or '')[:QUOTE]}"
    if _replanned_before(card):
        # The planner has read this card once for this reason; a second note
        # that still attests fewer is a person's call.
        return None
    linear_ops.cmd_advance(identifier, "Planning", "Backlog", held=True)
    lane = (linear_ops.get_issue(identifier, fresh=True).get("state") or {}).get("name")
    if lane != "Planning":
        raise blocker_class.NotNow(f"Planning move did not land — the card is in {lane}")
    return "replanned", f"{lines} of {criteria} criteria attested — sent to Planning to be re-read"
