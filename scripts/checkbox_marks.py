#!/usr/bin/env python3
"""The checkbox grammar for acceptance criteria (DRE-3147) — ONE table of
marks, read by every consumer that has to recognise a criterion.

A criterion is a list item whose marker is a checkbox. Markdown spells that
`[ ]` and `[x]`; a good number of this board's older cards spell it with the
Unicode ballot box instead, and the two are the same thing written twice.

## Why this module exists

On 2026-09-04 four real cards — DRE-2902, DRE-2916, DRE-2921 and DRE-2924 —
were classified one-off, passed the pre-approval critic, and were then refused
by the one-off route with "the card states no acceptance criteria, so there is
no exit condition to route on". Every one of them carried four or five under an
`## Acceptance` heading, written with the ballot box. The critic read the prose
and saw one clean pull request; the route read a regex that admitted markdown
and nothing else, and saw no work at all. The refusal then told the CEO to
write criteria that already existed.

They were unblocked by rewriting the cards, which is a workaround: the reader
meets the cards where they are, and the board is not edited to suit the reader.

## Why ONE table and not three

`routing_verdict` routes on these items, `plan_critic` fails a planned card
that carries none, and `plan_footprint` ends a `**Files:**` section at the
first one. Those were three regexes for one line — three answers waiting to
disagree, and a glyph taught to one of them is a glyph the other two still
refuse. Same reason `blocker_prose.py` exists (DRE-2922): the grammar lives in
one module and every consumer reads it.

A mark this table does not carry is RAISED, never guessed. Reading an unknown
mark as unchecked is how a done card reads as outstanding work, and reading it
as checked is how outstanding work reads as done; neither is a default worth
having.
"""

from __future__ import annotations

import re

#: Marks meaning "not done yet". The markdown box, and the ballot box.
UNCHECKED = ("[ ]", "☐")

#: Marks meaning "done". Markdown's `x` in either case, and the ballot box
#: with a check or a cross — a card that crossed an item off wrote `☒`.
CHECKED = ("[x]", "[X]", "☑", "☒")

#: The mark alone, as a regex fragment, for a consumer that needs it inside a
#: larger pattern (`plan_footprint._SECTION_END` ends a section at one).
MARK = r"(?:\[[ xX]\]|[☐☑☒])"

#: A list marker, a mark, and the criterion's text. The list marker is
#: REQUIRED in both spellings: a glyph loose in prose is a mention, not a
#: declaration — the same mention-versus-declaration line DRE-2670 turned on.
ITEM = re.compile(rf"^\s*[-*+]\s*(?P<mark>{MARK})\s*(?P<text>.+?)\s*$")

#: The same item, anchored per line for a caller searching a whole body.
ITEM_MULTILINE = re.compile(rf"^\s*[-*+]\s*{MARK}\s*\S", re.MULTILINE)


def is_checked(mark: str) -> bool:
    """Is this mark a struck-through one?

    Raises `ValueError` on anything the table does not carry — see the module
    docstring for why there is no default.
    """
    text = (mark or "").strip()
    if text in CHECKED:
        return True
    if text in UNCHECKED:
        return False
    raise ValueError(
        f"{mark!r} is not a checkbox mark this reader carries — the table is "
        f"{', '.join(UNCHECKED + CHECKED)}"
    )
