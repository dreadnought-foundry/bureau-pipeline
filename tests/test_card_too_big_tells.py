"""The card-quality standard must say when a card is too big for one run
(DRE-2893, extended by DRE-2913).

`standards/card-quality.md` said a body must be "one-PR-scoped" and stopped
there — a rule with no test attached to it. Three cards filed against that rule
were too big for one run and nothing in the standard told the planner how to
tell: DRE-2719, DRE-2847 and DRE-2838 between them burned six dead runs and
roughly $65 and produced zero pull requests, and every split then shipped within
hours.

The tells are readable BEFORE a card is filed, so they belong in the
standard the planner writes cards against — not in a brief. Headless CI agents
cannot load Skills; they get the standards by workflow context injection, so a
rule that lives only in a brief reaches the interactive session and not the
fleet.

DRE-2913 adds two more tells learned the same night DRE-2893 merged — specific
is not small (DRE-2871: eight precisely-named sites, six dead runs, $17.44) and
cut on file footprint, not only on concern (three cleanly-split PRs that went
`DIRTY` on merge order alone) — plus the three-question arithmetic that catches
all six, and the rule that a split product is re-checked like anything else.
They go in the SAME section, so both surfaces still read one text.

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

# How prose spells a count of tells. The section states its own count and
# `standards/README.md` restates it, so both are checked against the number of
# tells actually listed rather than against a literal.
COUNT_WORD = {4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}


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


def _tell_numbers() -> list[int]:
    """The numbers of the tells listed in the section, in file order."""
    return [
        int(n)
        for n in re.findall(r"^(\d+)\. \*\*", _section(), re.MULTILINE)
    ]


class TestTheTells:
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
            # 5. specific is not small — countable, named, and still four
            #    deliverables in two languages (DRE-2913)
            (r"specific is not small", "DRE-2871"),
            # 6. cut on file footprint, not only on concern (DRE-2913)
            (r"file footprint", "DRE-2837"),
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

    def test_specific_is_not_small_either(self):
        """DRE-2871 was the best-written card of the night and died six times.
        Both halves are stated, because a planner who has learned only the
        bounded half writes DRE-2871 again."""
        section = _flat(_section())
        assert re.search(r"bounded is not small\.? +specific is not small", section, re.IGNORECASE), (
            "the fifth tell must say both — bounded and specific are each "
            "necessary and neither is sufficient"
        )
        assert re.search(r"neither is sufficient", section, re.IGNORECASE)

    def test_the_fifth_tell_carries_what_the_card_cost(self):
        section = _flat(_section())
        assert re.search(r"six runs died", section, re.IGNORECASE), (
            "DRE-2871's six dead runs are the measurement; state them"
        )
        assert "$17.44" in section, "the last run's measured cost is the evidence"
        assert re.search(r"four deliverables in two languages", section, re.IGNORECASE), (
            "the arithmetic of WHY a precisely-named card was still too big"
        )

    def test_the_fifth_tell_names_the_other_direction(self):
        """DRE-2838 reached the same conclusion from an audit that had already
        bounded it — 57 sites down to five, and it still died twice."""
        section = _flat(_section())
        match = re.search(r"specific is not small", section, re.IGNORECASE)
        assert match, "the fifth tell is missing"
        end = re.compile(r"#+ ").search(section, match.end())
        item = section[match.start() : end.start() if end else len(section)]
        assert "DRE-2838" in item and re.search(r"57 sites to five", item), (
            "the fifth tell must cite the audit that bounded DRE-2838 to five "
            "sites and did not save it"
        )

    def test_the_sixth_tell_is_about_files_not_concerns(self):
        section = _flat(_section())
        assert re.search(r"not only on concern", section, re.IGNORECASE), (
            "the sixth tell is a SECOND cut, made after the concern cut"
        )
        for pr in ("#2206", "#2207", "#2213"):
            assert pr in section, f"the sixth tell does not cite {pr}"
        assert "DIRTY" in section, (
            "what happened to those PRs is the evidence — they went DIRTY on "
            "merge order with no defect in any of them"
        )
        assert re.search(r"no defect in any of them", section, re.IGNORECASE)

    def test_the_sixth_tell_says_the_rule_already_existed(self):
        section = _flat(_section())
        assert re.search(r"a file footprint per card", section), (
            "the wave plan already required a file footprint per card — quote it"
        )
        assert re.search(r"was not applied", section, re.IGNORECASE), (
            "the failure was application, not absence; say so or the reader "
            "writes the rule a third time"
        )
        assert re.search(r"wire `blockedBy` rather than", section), (
            "the remedy for an overlap is a blockedBy relation, not a hope"
        )


class TestTheArithmetic:
    """Three counted questions, answerable at filing time with nothing run.
    The tells are what you notice; the arithmetic is what you run."""

    def test_the_three_questions_are_stated(self):
        section = _flat(_section())
        for question in (
            "independent deliverables",
            "languages or tiers",
            "CONTRACT the others read",
        ):
            assert question in section, f"the arithmetic does not ask {question!r}"

    def test_the_arithmetic_needs_nothing_run(self):
        assert re.search(r"before filing, with nothing run", _flat(_section())), (
            "the whole value is that the count is available at filing time — "
            "an arithmetic that needs a repo sweep is the tell it is replacing"
        )

    def test_the_arithmetic_is_stated_for_every_tell(self):
        """The section says how many tells its own list holds, so a seventh
        tell added without touching the arithmetic fails here."""
        word = COUNT_WORD[len(_tell_numbers())]
        assert re.search(rf"catches all {word} tells", _flat(_section()), re.IGNORECASE), (
            f"the arithmetic must claim all {word} tells, matching the list"
        )

    def test_the_worked_example(self):
        section = _flat(_section())
        assert re.search(r"DRE-2871 scored \*\*4 / 2 / yes\*\*", section), (
            "the arithmetic without a worked example is a slogan"
        )
        assert re.search(r"that is three cards", section, re.IGNORECASE), (
            "say what the score MEANS — 4 / 2 / yes is three cards"
        )
        assert re.search(r"visible at filing time", section, re.IGNORECASE)


class TestContractFirst:
    """A shared rule is split out first and the siblings are blocked on it —
    otherwise each sibling invents the answer, which is measurable."""

    def test_the_shared_rule_splits_out_first(self):
        section = _flat(_section())
        assert re.search(r"splits that rule out FIRST", section), (
            "the contract-first rule must be explicit, and FIRST is the point"
        )
        assert re.search(r"siblings.{0,40}`blockedBy`", section), (
            "the siblings are blockedBy the contract card — a real relation, "
            "the same one the dependency gate reads"
        )
        assert re.search(r"cite a declared answer", section, re.IGNORECASE), (
            "state WHY: a declared answer instead of each piece inventing one"
        )

    def test_the_two_sites_that_answered_in_opposite_directions(self):
        section = _flat(_section())
        assert "alerts._advanced_recently" in section and "project_rollups.is_stale" in section, (
            "the evidence for contract-first is two sites resolving the same "
            "absence in opposite directions; name them"
        )
        assert re.search(r"opposite directions", section, re.IGNORECASE)
        assert re.search(r"same field, in the same codebase", section, re.IGNORECASE)


class TestASplitIsNotAOneTimeAct:
    """Two of the cards in this section were themselves split products, so the
    arithmetic runs on every replacement card too."""

    def test_run_the_arithmetic_on_every_replacement(self):
        assert re.search(
            r"arithmetic on every replacement card", _flat(_section()), re.IGNORECASE
        ), "the re-check rule is not stated"

    def test_the_evidence_is_a_split_product_that_was_still_too_big(self):
        section = _flat(_section())
        assert re.search(
            r"DRE-2871 was itself one third of an earlier split of DRE-2837", section
        ), (
            "the rule needs its evidence: a card that came OUT of a split and "
            "was still too big"
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
            # DRE-2913's own measurements
            "DRE-2871",
            "$17.44",
            "151 turns",
            "#2206",
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


class TestTheCountIsStatedConsistently:
    """The number of tells is written down in two places outside the list —
    the section's own arithmetic heading and the standards index. Both are
    derived from the list here, so adding a tell without updating them fails."""

    def test_the_tells_are_numbered_without_a_gap(self):
        numbers = _tell_numbers()
        assert numbers == list(range(1, len(numbers) + 1)), (
            f"the tells are numbered {numbers} — a reader cites them by number"
        )

    def test_the_standards_index_names_the_same_count(self):
        word = COUNT_WORD[len(_tell_numbers())]
        readme = (ROOT / "standards" / "README.md").read_text(encoding="utf-8")
        assert re.search(rf"the {word} tells that a card is too big", readme), (
            f"standards/README.md must say '{word} tells' — the index is where "
            "an agent decides whether it has already read this section"
        )

    def test_the_index_credits_the_card_that_added_them(self):
        readme = (ROOT / "standards" / "README.md").read_text(encoding="utf-8")
        assert "DRE-2893" in readme and "DRE-2913" in readme, (
            "both cards that wrote this section are cited in the index"
        )


class TestBothSurfacesGetTheSameText:
    """The fleet reads the standard by context injection; the interactive
    `dreadnought-card-quality` skill is generated from the same file. Putting the
    section anywhere else gives one surface a rule the other has never heard of —
    the drift this programme has already measured."""

    def test_the_new_tells_went_into_the_section_that_already_existed(self):
        """AC of DRE-2913: added to DRE-2893's section, not a new one beside
        it. A second '## ... too big ...' heading is the failure mode."""
        assert len(HEADING_RE.findall(_text())) == 1, (
            "there is more than one too-big section — the tells must live "
            "together or a reader meets half of them"
        )
        section = _flat(_section())
        for phrase in ("specific is not small", "file footprint", "4 / 2 / yes"):
            assert re.search(re.escape(phrase), section, re.IGNORECASE), (
                f"{phrase!r} is in the standard but outside the too-big section"
            )

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
            # The whole section travels, not just its heading: the fleet and
            # the generated skill must read the same six tells.
            assert "4 / 2 / yes" in context, (
                f"role {role!r} receives the section heading but not the "
                "arithmetic under it"
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
