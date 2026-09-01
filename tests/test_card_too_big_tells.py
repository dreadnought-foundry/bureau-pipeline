"""The card-quality standard must say when a card is too big for one run (DRE-2893).

`standards/card-quality.md` said a body must be "one-PR-scoped" and stopped
there — a rule with no test attached to it. Three cards filed against that rule
were too big for one run and nothing in the standard told the planner how to
tell: DRE-2719, DRE-2847 and DRE-2838 between them burned six dead runs and
roughly $65 and produced zero pull requests, and every split then shipped within
hours.

The four tells are readable BEFORE a card is filed, so they belong in the
standard the planner writes cards against — not in a brief. Headless CI agents
cannot load Skills; they get the standards by workflow context injection, so a
rule that lives only in a brief reaches the interactive session and not the
fleet.

These assertions are content-shaped, like the rest of the agent-document tests:
a document is the one consumer of a pipeline rule that no import, no schema and
no call site checks. Two of them are NOT content-shaped and are the point of the
module — the stated retry mechanism is read out of `scripts/dead_run.py`, so
changing the cap or the hold label without updating the standard fails here.

Run: cd bureau-pipeline && python3 -m pytest tests/test_card_too_big_tells.py -v
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import assemble_context  # noqa: E402
import dead_run  # noqa: E402

STANDARD = ROOT / "standards" / "card-quality.md"

# The heading the section is filed under. Pinned as a constant because three
# assertions below locate the section by it.
HEADING_RE = re.compile(r"^## .*too big.*$", re.IGNORECASE | re.MULTILINE)

# The one-PR rule the section must sit beside. Verbatim from the Body section,
# so a reword of the existing rule fails here — this card is an ADDITION.
ONE_PR_RULE = (
    "## Body\n"
    "A clear, **one-PR-scoped** description with its own `## Acceptance criteria`\n"
    "(checkable `- [ ]` items)."
)

# How many attempts the standard may claim, written the way prose writes it.
# Keyed off dead_run's real cap so the two cannot drift apart silently.
ATTEMPT_WORD = {1: "once", 2: "twice"}


def _text() -> str:
    return STANDARD.read_text(encoding="utf-8")


def _section() -> str:
    """The too-big section: its heading through the next top-level heading."""
    body = _text()
    match = HEADING_RE.search(body)
    assert match, "standards/card-quality.md has no '## ... too big ...' section"
    start = match.start()
    nxt = re.compile(r"^## ", re.MULTILINE).search(body, match.end())
    return body[start : nxt.start()] if nxt else body[start:]


def _flat(text: str) -> str:
    """The section with its line wrapping collapsed.

    Every phrase assertion below reads this: the standard is prose wrapped at 80
    columns, so "six dead runs" is legitimately "six dead\nruns" in the file and
    a raw-substring test would pin the line breaks rather than the sentence.
    """
    return re.sub(r"\s+", " ", text)


class TestTheFourTells:
    """Every tell is readable before the card is filed, and carries the card it
    was measured on so the next reader can check rather than trust."""

    def test_section_exists(self):
        assert HEADING_RE.search(_text()), (
            "no section on when a card is too big for one run"
        )

    def test_section_sits_beside_the_one_pr_rule(self):
        body = _text()
        assert ONE_PR_RULE in body, (
            "the existing one-PR-scoped rule was reworded; DRE-2893 is an addition"
        )
        one_pr_at = body.index(ONE_PR_RULE)
        section_at = HEADING_RE.search(body).start()
        assert section_at > one_pr_at, "the tells must follow the rule they test"
        between = body[one_pr_at + len(ONE_PR_RULE) : section_at]
        assert not re.search(r"^## ", between, re.MULTILINE), (
            "another section came between the one-PR rule and its tells — "
            "'beside' is the requirement, a reader must meet them together"
        )

    @pytest.mark.parametrize(
        "tell,evidence",
        [
            # 1. contracts between the pieces — the strongest tell
            (r"contract.*between the pieces", "DRE-2719"),
            # 2. two languages or two tiers — bounded is not small
            (r"two languages or two tiers", "DRE-2838"),
            # 3. a criterion counting something never enumerated
            (r"never enumerated", "DRE-2837"),
            # 4. an unbounded quantifier
            (r"unbounded quantifier", "DRE-2838"),
        ],
    )
    def test_each_tell_is_stated_with_its_evidence(self, tell, evidence):
        section = _flat(_section())
        match = re.search(tell, section, re.IGNORECASE)
        assert match, f"the tells do not state {tell!r}"
        # The card id must sit in the tell's OWN item, not somewhere else in the
        # section — an unevidenced tell is the thing this card is fixing. The
        # item ends at the next numbered tell or the next heading, whichever
        # comes first (the text is flattened, so those are the only boundaries).
        ends = [
            m.start()
            for m in (
                re.compile(r"\d+\. \*\*").search(section, match.end()),
                re.compile(r"#+ ").search(section, match.end()),
            )
            if m
        ]
        paragraph = section[match.start() : min(ends) if ends else len(section)]
        assert evidence in paragraph, (
            f"the {tell!r} tell does not name the card it was measured on ({evidence})"
        )

    def test_bounded_is_not_the_same_as_small(self):
        assert re.search(r"bounded is not the same as small", _flat(_section()), re.IGNORECASE), (
            "DRE-2838 was well-bounded and still too big — the tell is useless "
            "without that distinction"
        )


class TestTwoDeathsMeansSplit:
    """Stated as a rule, with the mechanism named, so a reader knows why a third
    attempt never happens on its own."""

    def test_two_turn_cap_deaths_means_split(self):
        section = _flat(_section())
        assert re.search(r"two turn-cap deaths", section, re.IGNORECASE), (
            "the two-deaths rule is not stated as a rule"
        )
        assert re.search(r"no third attempt", section, re.IGNORECASE), (
            "the rule must say there is no third attempt"
        )

    def test_the_requeue_count_matches_the_pipeline(self):
        """Read out of dead_run.py, not restated: the standard's claim about how
        many retries happen is checked against the cap that decides it."""
        word = ATTEMPT_WORD[dead_run.TURN_REQUEUE_CAP]
        assert re.search(rf"requeues the card {word}", _flat(_section())), (
            f"the standard must say the pipeline requeues {word} "
            f"(dead_run.TURN_REQUEUE_CAP={dead_run.TURN_REQUEUE_CAP})"
        )

    def test_the_park_is_named_with_the_label_the_pipeline_writes(self):
        section = _flat(_section())
        assert dead_run.HOLD_LABEL in section, (
            f"the park must name the label the pipeline writes "
            f"({dead_run.HOLD_LABEL!r}) — a reader has to recognise it on the card"
        )
        assert dead_run.TURN_TAG in section, (
            f"the turn-exhaustion budget tag ({dead_run.TURN_TAG!r}) is what the "
            "requeue is counted by; name it so the count can be checked"
        )
        assert "Backlog" in section, "the park lands the card in Backlog; say so"

    def test_the_sweep_skips_a_parked_card(self):
        section = _flat(_section())
        assert re.search(r"sweep skips", section, re.IGNORECASE), (
            "why nothing retries a parked card must be stated, not implied"
        )
        assert re.search(r"signal to split", section, re.IGNORECASE), (
            "the park is the signal to split — that is the operator rule"
        )


class TestEveryClaimCarriesItsEvidence:
    """The measured cost, so the next reader can check rather than trust."""

    @pytest.mark.parametrize(
        "claim",
        [
            "DRE-2719",
            "DRE-2847",
            "DRE-2838",
            "DRE-2837",
            "six dead runs",
            "$65",
            "zero pull requests",
        ],
    )
    def test_the_measurement_is_in_the_section(self, claim):
        assert claim in _flat(_section()), f"the section does not carry {claim!r}"

    def test_the_agents_were_not_the_problem(self):
        section = _flat(_section())
        assert "2/5 failing tests written" in section and "3/5 implementation green" in section, (
            "the runs that died were doing real work — quote the milestones they "
            "reached, or the next reader blames the agent"
        )


class TestHowToSplit:
    def test_cut_on_independence_not_size(self):
        assert re.search(r"independence, not size", _flat(_section()), re.IGNORECASE)

    def test_each_piece_ships_alone_and_names_its_sibling(self):
        section = _flat(_section())
        assert re.search(r"shippable and reviewable alone", section, re.IGNORECASE)
        assert re.search(r"does NOT depend on", section), (
            "each new card must name the sibling it does not depend on"
        )

    def test_read_the_hand_back_first(self):
        section = _flat(_section())
        assert re.search(r"read the hand-back first", section, re.IGNORECASE), (
            "a hand-back is a split two agents already agreed on; deriving one "
            "from the code is slower and less reliable"
        )

    def test_cancel_the_original_never_done(self):
        section = _flat(_section())
        assert re.search(r"cancel the original, never done", section, re.IGNORECASE), (
            "no code shipped — Canceled clears the blocker without claiming delivery"
        )


class TestBothSurfacesGetTheSameText:
    """The fleet reads the standard by context injection; the interactive
    `dreadnought-card-quality` skill is generated from the same file. Putting the
    section anywhere else gives one surface a rule the other has never heard of —
    the drift this programme has already measured."""

    def test_every_role_that_builds_or_plans_cards_receives_it(self):
        heading = HEADING_RE.search(_text()).group(0)
        roles = [
            role
            for role in assemble_context.ROLE_STANDARDS
            if "card-quality.md" in assemble_context.standards_for(role)
        ]
        assert roles, "no role receives card-quality.md — the mapping changed"
        for role in roles:
            context = assemble_context.assemble(
                role, lambda p: (ROOT / Path(p).relative_to(".bureau-pipeline")).read_text(encoding="utf-8")
            )
            assert heading in context, (
                f"role {role!r} does not receive the too-big section in its "
                "assembled context"
            )

    def test_the_section_lives_in_the_file_the_skill_is_generated_from(self):
        """`standards/README.md` names the interactive plugin as the second
        consumer of these files, regenerated from `@main`. The section is in
        `standards/card-quality.md`, so the generated skill carries it verbatim
        — there is no second copy that can drift."""
        readme = (ROOT / "standards" / "README.md").read_text(encoding="utf-8")
        assert "interactive plugin" in readme.lower(), (
            "standards/README.md no longer documents the interactive surface; "
            "the skill's source of truth must stay stated somewhere checkable"
        )
        assert HEADING_RE.search(_text()), "the section is not in the generated file"


class TestNothingElseWasReworded:
    """DRE-2893 is an addition. These are the neighbouring rules verbatim."""

    @pytest.mark.parametrize(
        "untouched",
        [
            ONE_PR_RULE,
            "Any string shared across sibling cards (schema field,\n"
            "route, type, env var) is written **identically** in both",
            "## Dead — do not use\n"
            "The 8-section XML tags, `**Size:**`, and `scripts/orch/v4` references",
        ],
    )
    def test_existing_text_is_unchanged(self, untouched):
        assert untouched in _text()
