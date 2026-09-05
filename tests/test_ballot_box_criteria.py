"""RED-first tests: the criterion reader meets the cards where they are
(DRE-3147).

On 2026-09-04 four real cards — DRE-2902, DRE-2916, DRE-2921 and DRE-2924 —
were classified one-off, passed the pre-approval critic, and were then refused
by the one-off route with "the card states no acceptance criteria, so there is
no exit condition to route on". Every one of them carried four or five, written
`* ☐ …` with the Unicode ballot box, which is how a good number of this board's
older cards were authored. The critic read the prose and saw one clean pull
request; the route read `^\\s*[-*+]\\s*\\[[ xX]\\]` and saw no work at all, and
the refusal told the CEO to write criteria that already existed.

WHAT THIS PINS, one section per acceptance criterion:

  1. A card whose criteria are written `* ☐ …` routes exactly as one written
     `- [ ] …` — same verdict, same source, same destination, no escalation.
  2. `☑` and `☒` count as checked and `☐` as unchecked, with the existing
     `[ ]` / `[x]` behaviour unchanged — and the table of marks is ONE
     definition, not three, so the route, the critic's mechanical check and the
     footprint parser cannot disagree about what a criterion looks like.
  3. DRE-2924's `## Acceptance` section, exactly as it was written before the
     workaround, reads back as four criteria.
  4. The "no acceptance criteria" refusal quotes what it found under
     `## Acceptance`, or says the section is absent.

Run: cd bureau-pipeline && python3 -m pytest tests/test_ballot_box_criteria.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import checkbox_marks  # noqa: E402
import plan_critic  # noqa: E402
import plan_footprint  # noqa: E402
import planning_escalation  # noqa: E402
import planning_route  # noqa: E402
import planning_shape  # noqa: E402
import routing_verdict  # noqa: E402


# DRE-2924's `## Acceptance` section exactly as it was written before the
# workaround rewrote it, reproduced from the card that reported this defect.
# Four criteria, none of them a markdown checkbox.
DRE_2924_ACCEPTANCE = """## Acceptance

* ☐ A PR the size of #364 routes to multi-pass, not "standard"
* ☐ The threshold constant carries the measurement that justifies it
* ☐ A no-verdict run whose turns were spent reports turn exhaustion, never "startup/auth failure"
* ☐ Proven against #364's actual head sha, which is preserved on `agent/DRE-2888-one-definition-of-answered`
"""


def _stamp(name: str = "one-off") -> str:
    """The comment that IS a shape, written by the module that owns it."""
    return planning_shape.shape_comment(name, "because the plan says so")


def _card(description: str, identifier: str = "DRE-2924") -> dict:
    """One card in the shape `critic_score.read_card` returns."""
    return {
        "identifier": identifier,
        "title": "The PR size router calls a PR one-pass",
        "description": description,
        "labels": ["repo:bureau-pipeline", "agent:engineer"],
        "has_children": False,
    }


# ===========================================================================
# 1. `* ☐ …` routes exactly as `- [ ] …`
# ===========================================================================
class TestTheBallotBoxCardRoutesLikeTheCheckboxCard:
    BODY = (
        "The router reads the wrong number.\n\n"
        "## Acceptance\n\n"
        "{a} The threshold constant carries the measurement that justifies it\n"
        "{a} A no-verdict run reports turn exhaustion\n"
    )

    def test_the_same_card_in_two_spellings_gets_the_same_decision(self):
        ballot = routing_verdict.route(
            "PR size router", self.BODY.format(a="* ☐"), []
        )
        checkbox = routing_verdict.route(
            "PR size router", self.BODY.format(a="- [ ]"), []
        )
        assert ballot.verdict == checkbox.verdict
        assert ballot.source == checkbox.source
        assert ballot.source == "judgement"
        assert ballot.verdict is None  # a judgement call, not NEEDS WORK

    def test_the_ballot_box_card_leaves_planning_for_the_build_queue(self):
        """The four cards' actual outcome: classified one-off, critic passed,
        and then parked in Green Light for the CEO. It goes to the queue."""
        plan = planning_route.exit_plan(
            _card(self.BODY.format(a="* ☐")), [_stamp()]
        )
        assert plan.verdict == planning_route.fleet_verdict()
        assert plan.escalation is None
        assert plan.destination == plan.route.destination

    def test_it_lands_where_the_checkbox_card_lands(self):
        ballot = planning_route.exit_plan(
            _card(self.BODY.format(a="* ☐")), [_stamp()]
        )
        checkbox = planning_route.exit_plan(
            _card(self.BODY.format(a="- [ ]")), [_stamp()]
        )
        assert ballot.verdict == checkbox.verdict
        assert ballot.destination == checkbox.destination
        assert (ballot.escalation is None) == (checkbox.escalation is None)

    def test_a_ballot_box_criterion_naming_a_flow_still_routes_to_a_person(self):
        """The glyph changes what the reader can SEE, never what the rule
        decides — an interactive criterion is WORKBENCH in either spelling."""
        body = "## Acceptance\n\n* ☐ sign in and walk the wizard by hand\n"
        assert routing_verdict.route("Wizard", body, []).verdict == "WORKBENCH"

    def test_a_ballot_box_criterion_inside_a_fence_is_still_an_example(self):
        """A criterion inside a fence is an example of a criterion — this
        card's own body quotes DRE-2924's section inside one."""
        body = "## Acceptance\n\n```\n* ☐ sign in and walk the wizard\n```\n"
        assert routing_verdict.acceptance_criteria(body) == []


# ===========================================================================
# 2. One table of marks: ☑/☒ checked, ☐ unchecked, `[ ]`/`[x]` unchanged
# ===========================================================================
class TestTheTableOfMarks:
    @pytest.mark.parametrize("mark", ["[x]", "[X]", "☑", "☒"])
    def test_a_checked_mark_reads_as_checked(self, mark):
        assert checkbox_marks.is_checked(mark) is True

    @pytest.mark.parametrize("mark", ["[ ]", "☐"])
    def test_an_unchecked_mark_reads_as_unchecked(self, mark):
        assert checkbox_marks.is_checked(mark) is False

    def test_a_mark_the_table_does_not_carry_is_raised_never_guessed(self):
        with pytest.raises(ValueError):
            checkbox_marks.is_checked("(x)")

    @pytest.mark.parametrize("mark", ["[ ]", "[x]", "[X]", "☐", "☑", "☒"])
    @pytest.mark.parametrize("bullet", ["-", "*", "+"])
    def test_every_mark_after_every_list_marker_is_a_criterion(self, mark, bullet):
        body = f"## Acceptance\n\n{bullet} {mark} the queue drains\n"
        assert routing_verdict.acceptance_criteria(body) == ["the queue drains"]

    def test_a_glyph_with_no_list_marker_is_not_a_criterion(self):
        """`after a list marker`, exactly as the markdown form requires — a
        glyph loose in prose is a mention, not a declaration."""
        body = "## Acceptance\n\n☐ the queue drains\n"
        assert routing_verdict.acceptance_criteria(body) == []

    def test_the_marks_are_defined_in_exactly_one_module(self):
        """One definition, not three. The route, the critic's mechanical check
        and the footprint parser all read `checkbox_marks`; a fourth reader
        that spells the table out again is two answers waiting to disagree,
        which is the defect `blocker_prose.py` exists to have ended."""
        glyph_owners, bracket_owners = [], []
        for path in sorted((ROOT / "scripts").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if any(glyph in text for glyph in ("☐", "☑", "☒")):
                glyph_owners.append(path.name)
            if r"\[[ xX]\]" in text:
                bracket_owners.append(path.name)
        assert glyph_owners == ["checkbox_marks.py"], (
            f"the ballot-box marks are spelled out in {glyph_owners} — they "
            "belong in checkbox_marks.py and nowhere else"
        )
        assert bracket_owners == ["checkbox_marks.py"], (
            f"the markdown checkbox is spelled out in {bracket_owners} — a "
            "second copy is the reader that never learns the next glyph"
        )

    def test_the_critics_mechanical_check_reads_the_same_table(self):
        """`plan_critic.cards_without_acceptance` is the critic's cheap half —
        a card carrying ballot-box criteria has acceptance criteria there too,
        or the same card is refused twice for the same non-reason."""
        card = {
            "identifier": "DRE-2924",
            "body": "## Acceptance criteria\n\n* ☐ the queue drains\n",
            "labels": ["repo:bureau-pipeline"],
        }
        assert plan_critic.cards_without_acceptance([card]) == []

    def test_the_footprint_section_ends_at_a_ballot_box_item(self):
        """`**Files:**` runs until the next structural line, and a checkable
        acceptance item is one of them — in either spelling, or the criteria
        are swallowed into the declared footprint."""
        body = (
            "**Files:** scripts/routing_verdict.py\n"
            "* ☐ tests/test_routing_verdict.py is not a declared file\n"
        )
        assert plan_footprint.declared_files(body) == {"scripts/routing_verdict.py"}


# ===========================================================================
# 3. DRE-2924's section, exactly as filed, reads back as four criteria
# ===========================================================================
class TestTheCardThatReportedThis:
    def test_the_section_as_filed_yields_four_criteria(self):
        criteria = routing_verdict.acceptance_criteria(DRE_2924_ACCEPTANCE)
        assert len(criteria) == 4, criteria
        assert criteria[0] == (
            'A PR the size of #364 routes to multi-pass, not "standard"'
        )
        assert criteria[-1].startswith("Proven against #364's actual head sha")

    def test_the_card_as_filed_is_not_refused_for_stating_no_criteria(self):
        verdict, reason = routing_verdict.criteria_verdict(DRE_2924_ACCEPTANCE)
        assert verdict != "NEEDS WORK", reason
        assert "no acceptance criteria" not in reason


# ===========================================================================
# 4. The refusal quotes what it found under `## Acceptance`
# ===========================================================================
class TestTheRefusalShowsItsEvidence:
    NO_CRITERIA = (
        "The router reads the wrong number.\n\n"
        "## Acceptance\n\n"
        "It should route the big ones to multi-pass.\n"
        "It should not say startup failure.\n"
        "It should be proven against a real head sha.\n"
        "It should say so in the log.\n\n"
        "## Not in scope\n\n"
        "Rewriting existing cards.\n"
    )

    def test_the_refusal_quotes_the_first_three_lines_it_found(self):
        verdict, reason = routing_verdict.criteria_verdict(self.NO_CRITERIA)
        assert verdict == "NEEDS WORK"
        assert "It should route the big ones to multi-pass." in reason
        assert "It should not say startup failure." in reason
        assert "It should be proven against a real head sha." in reason

    def test_it_quotes_three_lines_and_no_more(self):
        _, reason = routing_verdict.criteria_verdict(self.NO_CRITERIA)
        assert "It should say so in the log." not in reason

    def test_it_stops_at_the_next_heading(self):
        _, reason = routing_verdict.criteria_verdict(
            "## Acceptance\n\nIt should route the big ones.\n\n"
            "## Not in scope\n\nRewriting existing cards.\n"
        )
        assert "Rewriting existing cards." not in reason

    def test_it_says_so_when_there_is_no_section_at_all(self):
        verdict, reason = routing_verdict.criteria_verdict("Search is slow.")
        assert verdict == "NEEDS WORK"
        assert "no `## Acceptance` section" in reason

    def test_an_acceptance_criteria_heading_counts_as_the_section(self):
        """`## Acceptance` and `## Acceptance criteria` are the same section —
        the reader shows a person what is there, it does not grade the
        heading."""
        _, reason = routing_verdict.criteria_verdict(
            "## Acceptance criteria\n\nIt should route the big ones.\n"
        )
        assert "It should route the big ones." in reason
        assert "no `## Acceptance` section" not in reason

    def test_the_reason_still_names_the_missing_thing(self):
        """The quote is added evidence, never a replacement for saying what
        the card owes."""
        _, reason = routing_verdict.criteria_verdict(self.NO_CRITERIA)
        assert "no acceptance criteria" in reason
        assert "- [ ]" in reason

    def test_the_escalation_the_ceo_reads_carries_the_quote(self):
        """The point of the quote: a person reading the escalation sees at
        once whether the card is empty or merely written in a shape the reader
        does not know."""
        plan = planning_route.exit_plan(_card(self.NO_CRITERIA), [_stamp()])
        assert plan.verdict == "NEEDS WORK"
        assert "It should route the big ones to multi-pass." in plan.escalation
        assert planning_escalation.refusal(plan.escalation) is None

    def test_a_quote_that_is_not_fit_for_the_ceo_degrades_to_a_count(self):
        """The escalation gate refuses code-shaped text, and it is right to —
        but a card whose acceptance lines name files must not cost the CEO the
        whole reason. Quote what can be shown; otherwise say what is there."""
        body = (
            "## Acceptance\n\n"
            "The reader in scripts/routing_verdict.py should accept the glyph.\n"
        )
        _, reason = routing_verdict.criteria_verdict(body)
        assert planning_escalation.jargon(reason) == ()
        assert "1 line" in reason
