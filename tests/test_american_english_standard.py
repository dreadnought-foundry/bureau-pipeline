"""Every agent writes American English (DRE-4832).

The CEO reads American English, and much of what the fleet writes — and much
of the text agents learn the house voice from — came out British. Agents
imitate the prose around them, so the rule has to be written down where they
read it. Headless build, review and planning agents never open anyone's
`~/.claude/CLAUDE.md`; they get `standards/` by workflow context injection, and
the `dreadnought-standards` plugin is generated from the same files. So the
rule lives ONCE, in `standards/comms.md`, and `standards/engineering.md` points
code comments, docstrings, commits and docs at it rather than restating it.

Each assertion is content-shaped on purpose: a document is the only consumer
of this change, and a rule nothing reads back is a rule that gets edited away.

No sweep of existing text is claimed or checked here. Existing British
spellings elsewhere in the repo — identifiers, filenames, quoted comments —
stay as they are, so the "written in American English" assertions are scoped
to the NEW rule text and nothing else.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STANDARDS = ROOT / "standards"
COMMS = STANDARDS / "comms.md"
ENGINEERING = STANDARDS / "engineering.md"

# The swap list the rule must actually name, American side only. Naming the
# British side would put British spellings inside the rule that forbids them.
SWAPS = (
    "canceled",
    "color",
    "behavior",
    "organization",
    "analyze",
    "center",
    "license",
    "program",
    "catalog",
)

# What the rule's own text may not contain. The British counterparts of the
# swaps above plus the forms that keep turning up in fleet prose.
BRITISH = (
    "cancelled",
    "cancelling",
    "colour",
    "colours",
    "behaviour",
    "behaviours",
    "organisation",
    "organisations",
    "organise",
    "organised",
    "analyse",
    "analysed",
    "analysing",
    "centre",
    "centres",
    "licence",
    "licences",
    "programme",
    "programmes",
    "catalogue",
    "catalogues",
    "honour",
    "honoured",
    "favour",
    "favourite",
    "recognise",
    "recognised",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(body: str, heading: str) -> str:
    """The text under `heading`, up to the next `## ` heading."""
    start = body.index(heading)
    rest = body[start + len(heading) :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def _bullet_naming(body: str, word: str) -> str:
    """The one top-level `- ` bullet that names `word`, continuation included.

    The pointer is a bullet in a bulleted document; reading it back as a bullet
    is what lets the "does not restate the list" assertion below be scoped to
    the new text instead of to the whole file.
    """
    bullets: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.startswith("- "):
            if current:
                bullets.append("\n".join(current))
            current = [line]
        elif current and (line.startswith("  ") or not line.strip()):
            current.append(line)
        elif current:
            bullets.append("\n".join(current))
            current = []
    if current:
        bullets.append("\n".join(current))
    matches = [b for b in bullets if word in b]
    assert matches, f"no bullet naming {word!r}"
    assert len(matches) == 1, f"{len(matches)} bullets name {word!r}; expected one"
    return matches[0]


def _british_in(text: str) -> list[str]:
    return [w for w in BRITISH if re.search(rf"\b{w}\b", text, re.I)]


class TestTheCommsStandardCarriesTheRule:
    """The rule lives in `comms.md` Mechanics, with the swap list and the
    carve-out. Mechanics is where the voice's mechanical rules already are —
    em-dashes, no emoji, no corporate-speak — and spelling is one of those."""

    def test_the_rule_is_in_the_mechanics_section(self):
        mechanics = _section(_read(COMMS), "## Mechanics")
        assert re.search(r"American (English|spelling)", mechanics), (
            "standards/comms.md Mechanics never states the American English "
            "rule — the one place headless agents read it"
        )

    def test_it_names_the_surfaces_it_governs(self):
        mechanics = _section(_read(COMMS), "## Mechanics")
        rule = _bullet_naming(mechanics, "American")
        for surface in ("CEO", "critic verdict", "PR description", "Linear"):
            assert re.search(surface, rule, re.I), (
                f"the rule never says it covers {surface!r}; a rule that names "
                "no surfaces is one every writer reads as somebody else's"
            )

    def test_it_lists_the_common_swaps(self):
        rule = _bullet_naming(_section(_read(COMMS), "## Mechanics"), "American")
        missing = [w for w in SWAPS if not re.search(rf"\b{w}\b", rule)]
        assert not missing, (
            f"the swap list omits {missing} — the list is what makes the rule "
            "actionable rather than a preference"
        )

    def test_it_carves_out_text_that_must_stay_verbatim(self):
        rule = _bullet_naming(_section(_read(COMMS), "## Mechanics"), "American")
        assert re.search(r"quot", rule, re.I), (
            "the rule does not exempt quoted text, so an agent could 'correct' "
            "somebody's words while quoting them"
        )
        for thing in ("identifier", "filename", "label", "Linear state"):
            assert re.search(thing, rule, re.I), (
                f"the rule does not exempt {thing}s — renaming one is a code "
                "change, not a spelling change"
            )

    def test_it_says_existing_british_text_is_not_a_pattern_to_follow(self):
        rule = _bullet_naming(_section(_read(COMMS), "## Mechanics"), "American")
        assert re.search(r"imitat", rule, re.I), (
            "the rule never says not to imitate the British text already in "
            "the repo — imitation is exactly how the fleet got here"
        )


class TestTheEngineeringStandardPointsAtIt:
    """Code comments, docstrings, commit messages and docs follow the same
    rule. A pointer, not a second copy: two statements of one rule drift."""

    def test_it_names_the_four_code_surfaces(self):
        pointer = _bullet_naming(_read(ENGINEERING), "American")
        for surface in ("comment", "docstring", "commit", "doc"):
            assert re.search(surface, pointer, re.I), (
                f"engineering.md's spelling line never names {surface}s"
            )

    def test_it_points_at_the_comms_standard(self):
        pointer = _bullet_naming(_read(ENGINEERING), "American")
        assert "comms.md" in pointer, (
            "engineering.md states a spelling rule without saying where the "
            "rule lives; the next edit updates one copy and not the other"
        )

    def test_it_does_not_restate_the_swap_list(self):
        pointer = _bullet_naming(_read(ENGINEERING), "American")
        restated = [w for w in SWAPS if re.search(rf"\b{w}\b", pointer)]
        assert len(restated) < 3, (
            f"engineering.md restates the swap list ({restated}); the list "
            "belongs in comms.md only"
        )


class TestTheRuleIsWrittenInAmericanEnglish:
    """A spelling rule spelled the other way teaches the other way. Scoped to
    the rule's own text — nothing here sweeps the rest of the repo."""

    def test_the_comms_mechanics_section_is_american(self):
        offenders = _british_in(_section(_read(COMMS), "## Mechanics"))
        assert not offenders, (
            f"standards/comms.md Mechanics uses British spelling {offenders}"
        )

    def test_the_engineering_pointer_is_american(self):
        offenders = _british_in(_bullet_naming(_read(ENGINEERING), "American"))
        assert not offenders, (
            f"engineering.md's spelling line uses British spelling {offenders}"
        )
