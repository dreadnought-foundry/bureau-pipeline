"""Every step that needs the CEO is agreed before the work starts (DRE-7521).

The CEO, 2026-10-18: every agent in the system asks for the steps only he can
unblock before the work starts. Portico's portals release stood stuck for more
than ten hours (DRE-7494) on a hand step the proof session (DRE-7235) found two
laps in. The rule lands once, in `standards/card-quality.md`, directly after the
CEO's words on "build it" (DRE-6219) and before `## Epics`.

Each assertion is content-shaped on purpose, like its neighbor
`tests/test_ceo_words_standard.py`: a document is the only consumer of this
change, and a rule nothing reads back is a rule that gets edited away.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STANDARD = ROOT / "standards" / "card-quality.md"

HEADING = "## Every step that needs the CEO is agreed before the work starts (DRE-7521)"
BEFORE = "## The CEO's words, and where the card goes (DRE-6219)"
AFTER = "## Epics"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _loose(needle: str) -> re.Pattern:
    """`needle` with every run of whitespace made elastic — the standard
    hard-wraps at 80 columns and a phrase falling across a line break is not a
    document that stopped saying it."""
    return re.compile(r"\s+".join(re.escape(w) for w in needle.split()), re.I)


def _headings() -> list[str]:
    return [line for line in _read(STANDARD).splitlines() if line.startswith("## ")]


def _section() -> str:
    """The section, heading to the next `## `."""
    body = _read(STANDARD)
    at = body.find(HEADING)
    assert at >= 0, f"standards/card-quality.md carries no {HEADING!r} heading"
    rest = body[at:]
    end = re.compile(r"^## ", re.M).search(rest, 1)
    return rest[: end.start()] if end else rest


def test_heading_exists_exactly_once():
    assert _headings().count(HEADING) == 1


def test_section_sits_directly_after_the_ceos_words_and_before_epics():
    headings = _headings()
    at = headings.index(HEADING)
    assert headings[at - 1] == BEFORE, (
        f"{HEADING!r} must come directly after {BEFORE!r}; it follows "
        f"{headings[at - 1]!r}"
    )
    assert headings[at + 1] == AFTER, (
        f"{HEADING!r} must come directly before {AFTER!r}; it precedes "
        f"{headings[at + 1]!r}"
    )


def test_names_every_kind_of_step_and_the_one_question_before_the_work():
    section = _section()
    for kind in (
        "a sign-in of his",
        "a press from his own session",
        "a production apply he has to OK",
        "a name only he can give",
    ):
        assert _loose(kind).search(section), f"the section does not name {kind!r}"
    assert _loose(
        "Every such step is listed in the one question he answers before the work starts"
    ).search(section)
    assert _loose("It is never found partway through").search(section)


def test_the_list_goes_in_the_epics_plan_or_the_cards_own_question():
    section = _section()
    assert _loose(
        "an epic's plan, for an epic and for the operator steps its PROOF card reads"
    ).search(section)
    assert _loose("the card's own question, for a card that escalates").search(section)
    # A proof card's plan is its epic's plan; a separate "plan" bullet for it
    # reads as a duplicate of the epic's.
    assert not _loose("the plan, for a proof's operator steps").search(section)


def test_his_yes_covers_every_step_and_is_recorded_in_pt():
    section = _section()
    assert _loose("His yes covers every step on the list").search(section)
    assert _loose("recorded on the card with its time in PT").search(section)
    assert _loose("no one asks him again").search(section)


def test_anything_an_agent_can_look_up_is_looked_up_never_asked():
    assert _loose("Anything an agent can look up is looked up, never asked").search(
        _section()
    )


def test_a_step_found_late_is_a_defect_in_the_plan():
    section = _section()
    assert _loose("was not on the list is a defect in the plan").search(section)
    assert _loose("never parked silently").search(section)


def test_names_the_incident_as_the_reason():
    section = _section()
    assert "**Why.**" in section
    assert "DRE-7494" in section and "DRE-7235" in section


def test_states_it_is_guidance_nothing_checks():
    section = _section()
    assert _loose("Today this is guidance; nothing in the pipeline checks it").search(
        section
    )
    assert _loose("follow-up work, filed as its own card").search(section)
