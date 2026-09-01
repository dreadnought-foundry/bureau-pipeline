#!/usr/bin/env python3
"""The ONE anchored blocker-prose parser (DRE-2922).

A dependency declared in a card's body — `**Blocked by:** DRE-N`,
`Depends on: DRE-N`, `Serialize after: DRE-N` — is read by three places, and
until this module existed each of them read it differently:

  * `linear_ops.parse_blocked_by` — the PRODUCER, which materialises a
    declaration into a real Linear `blocks` relation at card creation. Its
    grammar was `blocked by` only, so a card written `Depends on DRE-N` got no
    relation at all.
  * `reconcile.blockers_of` — the promotion gate, on the full grammar below.
  * `groomer.blockers_of` — a third consumer, importing the narrow producer.

That disagreement is not a tidiness problem, it is the machine that manufactures
a prose-only blocker: the sweep honours a sentence the door never turned into a
relation, and the card is then held by its own documentation with nothing in
Linear saying so. One grammar in one module, imported by all three, is what
closes it — and widening the producer to this grammar is the point, not a side
effect.

THE GRAMMAR IS `reconcile._BLOCKER_LINE`, MOVED VERBATIM. Do not re-derive it;
every clause was paid for, in days:

ANCHORED (DRE-2670). A bare substring match read a blocker out of any sentence
that merely MENTIONED one, so epic DRE-2492 — zero formal `blockedBy`
relations, a well-written plan — was jammed by its own prose: "B3 is formally
blocked by it" and "neither depends on the other" each named one of the epic's
own CHILDREN, the epic-level gate then held those children, and the children
were what would have unblocked it. Five cards, five days, ~480 consecutive
GREEN sweeps. A sentence whose literal meaning is "there are no dependencies
here" was parsed as declaring one. So the phrase must OPEN the line (after
list/quote/heading/emphasis markup) and be followed by a colon or the ids
themselves. Both sentences are must-not-match fixtures in `FIXTURES` below.

ORDERED-LIST MARKERS (`1.` / `2)`) are accepted on purpose. `standards/
card-quality.md` promises the declaration may sit "inside a list item" without
naming a style, and numbered acceptance-criteria lists are common on these
cards — dropping them failed UNSAFE (the opposite of DRE-2492): a card with a
real, undone dependency would have read as free to promote. The marker only
widens what may PRECEDE the phrase, so a numbered line that merely mentions a
dependency mid-sentence still declares nothing.

Ids are read from the WHOLE declaring line, not just the tail after the phrase:
on an anchored line nothing but markup can precede the phrase, so the two are
equivalent, and the whole-line read is the one the gate has always used.
"""
from __future__ import annotations

import re

# A DECLARATION opens its own line and names its target. A mention is not a
# declaration — see the module docstring for what each clause costs.
BLOCKER_LINE = re.compile(
    r"^[\s>*_`~+#-]*"                             # -, *, >, #, **bold**, `code`
    r"(?:\d+[.)][\s>*_`~+#-]*)?"                  # 1. / 2) ordered list item
    r"(?:blocked by|serialize after|depends on)"
    r"[\s*_`]*"                                   # closing emphasis markers
    r"(?::|(?=\s*DRE-\d+))",                      # a colon, or the ids directly
    re.IGNORECASE,
)

# Case-insensitive, and every id is UPPERCASED on the way out: the producer has
# always accepted "Blocked by: dre-9", and a consumer that did not would be a
# fourth answer to the question this module exists to have one answer to.
CARD_REF = re.compile(r"\bDRE-\d+\b", re.IGNORECASE)


def blocker_ids(text: str | None) -> list[str]:
    """Every card id DECLARED as a blocker in `text` — uppercased, de-duplicated,
    order-preserving. Empty when the body declares nothing.

    A list, not a set: the producer creates relations in the order the card
    declares them, and the set-returning consumers wrap it themselves.
    """
    found: list[str] = []
    for line in (text or "").splitlines():
        if not BLOCKER_LINE.search(line):
            continue
        for ref in CARD_REF.findall(line):
            up = ref.upper()
            if up not in found:
                found.append(up)
    return found


# --------------------------------------------------------------------------- #
# the conformance corpus                                                       #
# --------------------------------------------------------------------------- #
# It lives HERE, beside the grammar, because it is the grammar's definition in
# examples and every reader is held to it. `tests/test_one_blocker_prose_parser
# .py` drives ONE fixture set through all three consumers and fails if any two
# disagree — a test per consumer cannot see drift BETWEEN them, which is the
# only failure that mattered.
#
# `(text, expected ids)`. An empty tuple means MUST NOT MATCH.

DECLARING: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("**Blocked by:** DRE-9", ("DRE-9",)),
    ("Blocked by: DRE-9", ("DRE-9",)),
    ("Blocked by DRE-9", ("DRE-9",)),
    ("BLOCKED BY: DRE-9", ("DRE-9",)),
    ("Blocked by: dre-9", ("DRE-9",)),
    ("- **Blocked by:** DRE-9", ("DRE-9",)),
    ("* Blocked by: DRE-9", ("DRE-9",)),
    ("> **Blocked by:** DRE-9", ("DRE-9",)),
    ("**Depends on:** DRE-9", ("DRE-9",)),
    ("Depends on DRE-9", ("DRE-9",)),
    ("Serialize after: DRE-9", ("DRE-9",)),
    ("Serialize after DRE-9", ("DRE-9",)),
    # Ordered list items: dropping these failed UNSAFE (see the docstring).
    ("1. Blocked by: DRE-9", ("DRE-9",)),
    ("2) Depends on: DRE-9", ("DRE-9",)),
    ("3. **Blocked by:** DRE-9", ("DRE-9",)),
    ("10) Serialize after: DRE-9", ("DRE-9",)),
    # Several ids on one line, and the DRE-1233 form whose ids sit in the prose
    # tail of a declaring line.
    ("**Blocked by:** DRE-100, DRE-101", ("DRE-100", "DRE-101")),
    ("**Blocked by:** DRE-5, DRE-5, DRE-3", ("DRE-5", "DRE-3")),
    ("Serialize after: all other DRE-1200 work", ("DRE-1200",)),
    # A whole body: a real declaration plus prose that denies one. The anchor is
    # per line, never all-or-nothing per body.
    (
        "**Blocked by:** DRE-9\n"
        "\n"
        "Note that DRE-11 depends on nothing here, and neither depends on the other.\n",
        ("DRE-9",),
    ),
    (
        "Do the work.\n\n**Blocked by:** DRE-9\n\n## Acceptance criteria\n- [ ] x",
        ("DRE-9",),
    ),
)

NOT_DECLARING: tuple[str, ...] = (
    "",
    "totally independent card",
    # The two DRE-2492 sentences, verbatim in the parts that matter. These are
    # the five-day freeze; they must never match again.
    "Both depend on DRE-2494 only - neither depends on the other, and neither "
    "blocks anything in wave 4.",
    "DRE-2496 lands first. B3 is formally blocked by it, so it cannot start "
    "before the rail exists.",
    # …and the epic's real body around them.
    "**Repo:** agent-bureau\n"
    "\n"
    "Wave 2 sequencing: DRE-2496 **lands first.** B3 is formally blocked by it, "
    "so it cannot start before the rail exists.\n"
    "\n"
    "Both depend on DRE-2494 only - neither depends on the other, and neither "
    "blocks anything in wave 4.\n"
    "\n"
    "DRE-2494 slipping delays everything in wave 3.\n",
    "We should probably serialize after DRE-2494 ships, but not yet.",
    # Accepting ordered-list markers must not re-open the mention hole.
    "1. Ship the rail. B3 is formally blocked by DRE-2496, per the plan.",
    # A declaring phrase that names nothing declares nothing.
    "Blocked by: the design review",
)

FIXTURES: tuple[tuple[str, tuple[str, ...]], ...] = DECLARING + tuple(
    (text, ()) for text in NOT_DECLARING
)
