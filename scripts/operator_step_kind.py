#!/usr/bin/env python3
"""What kind of operator step a card is — one a machine could do, or one a
person must (DRE-8650).

    classify(title, description) -> (class, kind, evidence)

`class` is `machine` or `person`. `kind` is one of `MACHINE_KINDS`, or
`person`. `evidence` is the text of the card that decided it, as the card
writes it, or `NO_MATCH`.

The rule is data, `config/operator-step-kinds.json`: the phrases that name a
person acting, and an ordered list of machine kinds, each with the phrases
that identify it. A phrase matches case-insensitively and whole — never inside
a longer word, with any run of whitespace standing for a space — in the title
or the description, and a curly apostrophe reads as a straight one.

Precedence:

1. A person phrase anywhere wins, because a card with one person step cannot
   be finished by a machine.
2. Otherwise the first machine kind in the list with a phrase in the text wins.
3. With no match the class is `person`, and the evidence is `NO_MATCH`:
   defaulting to a person never claims more automation than exists.

Pure: it reads the file and nothing else, and it writes nothing. The one
caller is the hygiene lane `scripts/hygiene_hand_step.py`, which labels the
card with the class; the class changes nobody's job.

    python3 scripts/operator_step_kind.py "<title>" "<description>"
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "operator-step-kinds.json"

MACHINE = "machine"
PERSON = "person"
MACHINE_KINDS = ("deploy", "script", "variable", "linear-admin", "bot-pr")
NO_MATCH = "no machine kind matched"

_APOSTROPHES = str.maketrans({"‘": "'", "’": "'"})


def _phrase_re(phrase: str) -> re.Pattern:
    """A whole-phrase matcher. An edge that is a word character may not touch
    another word character or a hyphen — `signs in` does not fire on
    `designs into`, nor `deploy` on `portico-deploy-role`; an edge that is
    not (`.sh`) is its own boundary."""
    phrase = phrase.translate(_APOSTROPHES)
    body = re.escape(phrase).replace("\\ ", r"\s+")
    head = r"(?<![\w-])" if re.match(r"\w", phrase) else ""
    tail = r"(?![\w-])" if re.search(r"\w$", phrase) else ""
    return re.compile(f"{head}{body}{tail}", re.IGNORECASE)


def load(path: Path | str | None = None) -> tuple:
    """`(person patterns, ((kind, patterns), ...))` off the config, in its
    order. A kind outside `MACHINE_KINDS`, or one with no phrase, is refused."""
    with open(path or CONFIG_PATH, encoding="utf-8") as fh:
        doc = json.load(fh)
    person = tuple(_phrase_re(p) for p in doc["person"]["phrases"])
    kinds = []
    for row in doc["kinds"]:
        kind = row["kind"]
        if kind not in MACHINE_KINDS:
            raise ValueError(f"{kind!r} is not a machine kind — the kinds are "
                             f"{', '.join(MACHINE_KINDS)}")
        if not row.get("phrases"):
            raise ValueError(f"the {kind} kind names no phrase")
        kinds.append((kind, tuple(_phrase_re(p) for p in row["phrases"])))
    return person, tuple(kinds)


def _first(text: str, patterns) -> str | None:
    """The text of the first pattern, in list order, that matches."""
    for pattern in patterns:
        found = pattern.search(text)
        if found:
            return found.group(0)
    return None


def classify(title: str | None, description: str | None, rules: tuple | None = None) -> tuple:
    """`(class, kind, evidence)` for one card — see the module docstring."""
    person, kinds = rules if rules is not None else load()
    text = f"{title or ''}\n{description or ''}".translate(_APOSTROPHES)
    said = _first(text, person)
    if said is not None:
        return PERSON, PERSON, said
    for kind, patterns in kinds:
        said = _first(text, patterns)
        if said is not None:
            return MACHINE, kind, said
    return PERSON, PERSON, NO_MATCH


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not 1 <= len(args) <= 2:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    cls, kind, evidence = classify(args[0], args[1] if len(args) > 1 else "")
    print(f"{cls}, kind {kind} — {evidence}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
