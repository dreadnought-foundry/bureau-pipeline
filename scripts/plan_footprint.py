#!/usr/bin/env python3
"""The declared file footprint, parsed (DRE-3040).

`briefs/planner.md` calls the `**Files:**` line "the INPUT to the ordering":
every card the planner creates declares the files it will create or edit, the
contention pre-flight reads those lists, and where two intersect the cards are
serialized rather than run in parallel. That is the rule
`standards/engineering.md` states as "Each card/agent owns DISJOINT files, and
that is checked at PLAN TIME".

Until this module existed **no script read the line**. The consequences were
measured on DRE-3019's plan, which the pre-approval critic passed with
`collisions=0`:

  * Four of the five children wrote the label as `**Files: **` — a stray space
    inside the bold markers — and nothing noticed, because nothing was looking.
  * The collision check scanned whole card BODIES with a path regex that
    required a `/`, so `README.md`, `CHANGELOG.md`, `package.json` and
    `tsconfig.json` — exactly the hot files the standard warns about — could
    not be seen at all, while every path mentioned in an acceptance criterion
    counted as a footprint.

So: ONE parser, consumed by `plan_critic.shared_files()` and by anything else
that needs the ordering input, because two regexes for one line are two answers
waiting to disagree.

Three decisions worth stating, each of which the tests pin:

  * **A missing section is a REFUSAL, not an empty set.** "This card declares
    no files" and "this card forgot to say" are different facts with different
    next actions, and collapsing them is how a collision check passes with
    nothing to check (standards/console-honesty.md rule 1). An explicitly
    declared "none" IS an answer — DRE-3032, a DEMO card that commits nothing,
    wrote exactly that and was right.
  * **Root-level files are files.** The `/` requirement was the whole of the
    old blindness.
  * **Only the declared section counts.** The footprint is what the planner
    committed to, not every path the card happens to mention.

Pure functions over strings with a thin CLI seam — no Linear client, no GitHub
calls — so the critic, the workflow and the tests all run the same code.

CLI:
  footprints    cards on stdin (JSON array) → {"declared": {...}, "missing": [...]}
  collisions    cards on stdin (JSON array) → {path: [identifiers]}
"""

from __future__ import annotations

import argparse
import json
import re
import sys


class FootprintMissing(ValueError):
    """A card body carries no `Files:` section at all.

    Raised rather than returning an empty set, because a silent empty set is
    indistinguishable from a card that owns nothing — and the caller that
    cannot tell them apart reports `collisions=0` on a plan it never read.
    """


# The declaration line, in every spelling a real card has used. The bold
# markers are markdown decoration around a label, so they are tolerated
# wherever an author put them — before the colon (`**Files**:`), after it
# (`**Files:**`), or with the stray space four of DRE-3019's five children
# wrote (`**Files: **`) — and their absence is tolerated too.
_HEADING = re.compile(
    r"^\s*(?:[-*+]\s+)?\*{0,2}\s*Files\s*\*{0,2}\s*:\s*\*{0,2}\s*(?P<rest>.*?)\s*$",
    re.IGNORECASE,
)

# What ENDS the section: a blank line, a markdown heading, a code fence, a
# checkable acceptance item, or the next `**Label:**` declaration. Anything
# else on the following lines is the declaration wrapping, which is exactly
# what the brief's own template does.
_SECTION_END = re.compile(
    r"^\s*(?:$|#{1,6}\s|```|[-*+]\s*\[[ xX]\]|(?:[-*+]\s+)?\*\*[^*\n]{1,60}\*\*)"
)

# A path, root-level ones included — dropping the old `/` requirement is the
# whole of the fix. The extension is 1-5 alphanumerics and nothing word-like
# may follow it, so a sentence's trailing punctuation is not swallowed into the
# name and a directory is not read as a file.
_FILE = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]{1,5})(?![\w/])"
)

# `e.g`, `i.e` — the cost of admitting root-level names: a dotted prose
# abbreviation has no slash to disqualify it. A single letter either side of a
# single dot is prose in every case; a real file is `a.md` or `src/x.c`, never
# `x.y`.
_PROSE_ABBREVIATION = re.compile(r"^[A-Za-z]\.[A-Za-z]$")


def footprint_section(body: str) -> str | None:
    """The text of the card's `Files:` declaration, or None if it has none.

    The FIRST declaration wins. A card declares ONE footprint — the ordering
    was derived from it — so a second line further down cannot widen what the
    planner committed to, the same way `plan_critic.read_result` lets no second
    header overturn a decision already recorded.
    """
    lines = (body or "").splitlines()
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if not m:
            continue
        section = [m.group("rest")]
        for nxt in lines[i + 1:]:
            if _SECTION_END.match(nxt):
                break
            section.append(nxt)
        return "\n".join(section)
    return None


def declared_files(body: str) -> set[str]:
    """The set of files a card declares. Raises `FootprintMissing` with no
    section — see the class docstring for why that is not an empty set."""
    section = footprint_section(body)
    if section is None:
        raise FootprintMissing(
            "no `**Files:**` section — the declared footprint is the input to "
            "the ordering, and a card without one cannot be checked for "
            "collisions"
        )
    out = set()
    for path in _FILE.findall(section):
        while path.startswith("./"):
            path = path[2:]
        if "/" not in path and _PROSE_ABBREVIATION.match(path):
            continue
        out.add(path)
    return out


def footprints(cards: list[dict]) -> dict[str, set[str]]:
    """`identifier → declared files`, for the cards that declared a footprint.

    A card with no section is ABSENT from the mapping rather than present with
    an empty set: it contributes no files because nobody knows what its files
    are, and `cards_without_footprint` is how the caller learns it exists.
    """
    out: dict[str, set[str]] = {}
    for card in cards or []:
        try:
            out[card.get("identifier")] = declared_files(card.get("body") or "")
        except FootprintMissing:
            continue
    return out


def cards_without_footprint(cards: list[dict]) -> list[str]:
    """The cards that declared no footprint at all."""
    out = []
    for card in cards or []:
        try:
            declared_files(card.get("body") or "")
        except FootprintMissing:
            out.append(card.get("identifier"))
    return out


def collisions(cards: list[dict]) -> dict[str, list[str]]:
    """Files declared by more than one card, path → the cards declaring it.

    The ordering question, mechanically: `standards/engineering.md`, "Don't
    fight over shared files" — a shared file edited by two open PRs conflicts
    every sibling, and PRs #2206, #2207 and #2213 all went DIRTY within an hour
    of each other on exactly this, with no defect in any of them.
    """
    owners: dict[str, list[str]] = {}
    for ident, files in footprints(cards).items():
        for path in sorted(files):
            owners.setdefault(path, []).append(ident)
    return {p: ids for p, ids in owners.items() if len(ids) > 1}


# --- CLI --------------------------------------------------------------------

def _stdin_json(default):
    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def _cmd_footprints(_args) -> int:
    cards = _stdin_json([])
    print(json.dumps({
        "declared": {k: sorted(v) for k, v in footprints(cards).items()},
        "missing": cards_without_footprint(cards),
    }))
    return 0


def _cmd_collisions(_args) -> int:
    print(json.dumps(collisions(_stdin_json([]))))
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("footprints", help="declared footprints; cards on stdin")
    f.set_defaults(fn=_cmd_footprints)

    c = sub.add_parser("collisions", help="shared files; cards on stdin")
    c.set_defaults(fn=_cmd_collisions)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
