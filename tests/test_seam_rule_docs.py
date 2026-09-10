"""The observation-gated seam rule, as the documents must carry it (DRE-3391).

The CEO's rule on 2026-09-06: a plan whose later cards depend on OBSERVING its
earlier cards live is not one epic but two — a wave. This card lands that rule
in `standards/card-quality.md`, the planner brief and the shape vocabulary, and
three sibling cards build the mechanism against the text, so the heading, the
tell grammar and the answer key are contracts rather than prose choices.

Every assertion here binds a document to something that is NOT that document:
the regex `planning_classify` already uses to parse the size tells, the shape
vocabulary's own destination for `wave`, and `planning_classify.problems()` —
the check that refuses a brief which has stopped naming a key the parser reads.
A test that only read the standard back to itself would pass on a section the
classifier can no longer find, which is the whole thing the siblings depend on.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import planning_classify  # noqa: E402
import planning_shape  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_planner_brief_shapes import _row, _shapes  # noqa: E402

STANDARD = ROOT / "standards" / "card-quality.md"
BRIEF = ROOT / "briefs" / "planner.md"
README = ROOT / "standards" / "README.md"

# The contract the classifier card locates the section by (DRE-3395 reads the
# same phrase). Written out in full because "exactly this heading" is what the
# card promises its siblings.
SEAM_HEADING = "## When a plan is two epics — the observation-gated seam (DRE-3244)"

# The sentence the pre-approval critic emits, verbatim in the standard and in
# the seam reader's code. Whitespace-insensitive: the standard hard-wraps.
FINDING = r"wait\s+on\s+observing\b.*?\blive\s+—\s+that\s+is\s+a\s+second\s+epic,\s+not\s+a\s+later\s+step\."


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _loose(needle: str) -> re.Pattern:
    """`needle` with every run of whitespace made elastic — the documents
    hard-wrap at 80 columns and a phrase falling across a line break is not a
    document that stopped saying it."""
    return re.compile(r"\s+".join(re.escape(w) for w in needle.split()), re.I)


def _seam_section() -> str:
    """The seam section of the standard, heading to the next `## `."""
    body = _read(STANDARD)
    at = body.find(SEAM_HEADING)
    assert at >= 0, (
        f"standards/card-quality.md carries no {SEAM_HEADING!r} heading — the "
        "classifier and both critics locate the seam rule by that exact string"
    )
    rest = body[at:]
    end = re.compile(r"^## ", re.M).search(rest, 1)
    return rest[: end.start()] if end else rest


def _seam_tells() -> str:
    """The numbered tells, bounded at the first `### ` — the same bound
    `planning_classify._tells_section` applies to the size tells, because the
    sibling classifier reuses that reader."""
    section = _seam_section()
    sub = re.search(r"^### ", section, re.M)
    return section[: sub.start()] if sub else section


class TestTheSectionTheSiblingsRead:
    """Three sibling cards build against this text. If the heading moves or the
    grammar changes, they break somewhere far from here."""

    def test_the_heading_is_exactly_the_contract_string(self):
        assert SEAM_HEADING in _read(STANDARD)

    def test_the_heading_is_findable_by_the_phrase_the_classifier_greps(self):
        # DRE-3395 locates the section by `observation-gated seam` in a `## `
        # heading, not by the card number.
        headings = re.findall(r"^## .*$", _read(STANDARD), re.M)
        matched = [h for h in headings if "observation-gated seam" in h.lower()]
        assert len(matched) == 1, (
            "exactly one `## ` heading in card-quality.md must carry the phrase "
            f"'observation-gated seam'; found {matched}"
        )

    def test_it_does_not_steal_the_size_section_from_the_classifier(self):
        # `planning_classify._TOO_BIG_HEADING` is `^## .*too big.*$` and takes
        # the FIRST match. A seam heading carrying "too big" would silently
        # feed the seam tells to every card classification in the fleet.
        assert not re.search(r"too big", SEAM_HEADING, re.I), (
            "the seam heading must not contain 'too big' — the classifier "
            "locates the SIZE tells by those words"
        )
        found = planning_classify._TOO_BIG_HEADING.search(_read(STANDARD))
        assert found and "DRE-2893" in found.group(0), (
            "the first `## ...too big...` heading is no longer the DRE-2893 "
            "size section, so the classifier is reading the wrong tells"
        )
        assert len(planning_classify.size_tells()) >= 6, (
            "the size tells the classifier reads have changed count — the seam "
            "section has been absorbed into the size section"
        )

    def test_it_carries_at_least_four_tells_in_the_size_tell_grammar(self):
        # The sibling classifier parses these with the regex already used for
        # the size tells, so that regex is what this test uses.
        tells = planning_classify._TELL.findall(_seam_tells())
        assert len(tells) >= 4, (
            f"the seam section lists {len(tells)} numbered tell(s) before its "
            "first `### `; the rule needs four, in the `N. **Headline.**` "
            "grammar the classifier parses"
        )
        assert [int(n) for n, _ in tells][:4] == [1, 2, 3, 4], (
            "the seam tells must be numbered from 1 like the size tells"
        )

    def test_the_tells_end_before_the_first_subheading(self):
        section = _seam_section()
        sub = re.search(r"^### ", section, re.M)
        assert sub, (
            "the seam section carries no `### ` subheading, so the reader that "
            "bounds the tell list has nothing to stop at"
        )
        after = section[sub.start():]
        assert not planning_classify._TELL.search(after), (
            "a `N. **…**` line appears after the first `### ` — the classifier "
            "bounds the tell list there and would read a worked-example line "
            "as a tell"
        )

    def test_each_tell_carries_reasoning_after_its_headline(self):
        # A headline alone is a phrase; the sentence under it is what makes a
        # classifier count instead of skim (planning_classify.tells_block).
        for line in _seam_tells().splitlines():
            m = re.match(r"^\d+\. \*\*(.+?)\*\*(.*)$", line)
            if m:
                assert m.group(2).strip(), (
                    f"seam tell {m.group(1)!r} is a headline with no reasoning"
                )


class TestTheWorkedExample:
    """DRE-3164 is the example the rule was learned from, and a rule with no
    worked example is a rule everybody agrees with and nobody applies."""

    def test_it_names_the_epic_the_rule_was_learned_from(self):
        assert "DRE-3164" in _seam_section()

    def test_it_names_the_split_as_it_landed_on_the_board(self):
        assert "DRE-3245" in _seam_section(), (
            "the worked example never names DRE-3245, the second epic the "
            "split actually produced"
        )

    def test_it_names_the_cards_on_both_sides_of_the_seam(self):
        section = _seam_section()
        # A — the engine and its first rider.
        for card in ("DRE-3167", "DRE-3165", "DRE-3210", "DRE-3211", "DRE-3166"):
            assert card in section, f"the A card set does not name {card}"
        # B — the fleet, blocked on A.
        for card in ("DRE-3212", "DRE-3216", "DRE-3213", "DRE-3238",
                     "DRE-3214", "DRE-3215", "DRE-3217", "DRE-3218"):
            assert card in section, f"the B card set does not name {card}"

    def test_it_says_what_the_undivided_epic_cost(self):
        section = _seam_section()
        assert "DRE-3060" in section, (
            "the worked example does not name the collision the undivided "
            "epic hit at the second critic"
        )


class TestWhatThePlannerFilesInstead:
    """The remedy, in the words the sibling critics emit."""

    def test_the_shape_is_a_wave(self):
        assert re.search(r"\bwave\b", _seam_section(), re.I), (
            "the seam section never says the shape is `wave`"
        )

    def test_the_second_epic_is_filed_at_the_gate(self):
        section = _seam_section()
        assert (_loose("filed at the gate").search(section)
                or _loose("at the same time").search(section)), (
            "the section must say the second epic is FILED at the same gate — "
            "a second epic nobody files is a second epic nobody builds"
        )

    def test_the_second_epic_is_blocked_on_the_first(self):
        assert _loose("blocked on the first").search(_seam_section())

    def test_the_second_epic_is_planned_only_when_the_first_is_done(self):
        assert _loose("planned in detail only when the first is Done").search(
            _seam_section()
        ), (
            "the section must say the second epic is planned in detail only "
            "when the first is Done — that is the whole point of the seam"
        )

    def test_it_carries_the_send_back_sentence_the_critics_emit(self):
        assert re.search(FINDING, _seam_section(), re.I | re.S), (
            "the mechanical send-back sentence is a string shared with the "
            "critic cards and the seam reader; it must appear verbatim here"
        )

    def test_it_distinguishes_the_seam_from_the_DRE_3075_case(self):
        section = _seam_section()
        assert "DRE-3075" in section, (
            "the section must say a seam is NOT the DRE-3075 case — one "
            "criterion unprovable before merge is two cards, a whole half of a "
            "plan unprovable before the other half runs is two EPICS"
        )


class TestThePlannerBrief:
    """The brief's classification section IS the prompt `planning_classify`
    sends, so what it stops saying is what the run stops asking."""

    def _classification(self) -> str:
        return planning_classify.brief_prompt()

    def test_the_prompt_names_the_seam_answer_key(self):
        assert "`seam`" in self._classification(), (
            "the classification prompt never names the `seam` key the sibling "
            "classifier parses"
        )

    def test_the_prompt_asks_the_observation_question(self):
        assert re.search(r"observing", self._classification(), re.I), (
            "the classification prompt must ask whether anything later waits "
            "on OBSERVING something earlier live"
        )

    def test_the_seam_answer_is_null_when_there_is_no_seam(self):
        prompt = self._classification()
        seam_line = next(
            (ln for ln in prompt.splitlines() if ln.strip().startswith("* `seam`")),
            "",
        )
        assert seam_line, "the `seam` key has no answer-key bullet of its own"
        assert "null" in seam_line.lower(), (
            "the `seam` bullet must say what to answer when there is no seam, "
            "or a model invents one"
        )

    def test_a_seam_makes_it_a_wave_not_an_epic(self):
        assert re.search(r"`?wave`?", self._classification(), re.I)
        assert _loose("not an `epic`").search(self._classification()) or _loose(
            "not an epic"
        ).search(self._classification()), (
            "the fourth rule must say a tripped seam test means `wave`, not "
            "`epic`"
        )

    def test_every_existing_answer_key_survives(self):
        # planning_classify.problems() refuses a brief that stops naming one.
        prompt = self._classification()
        for key in planning_classify.ANSWER_KEYS:
            assert f"`{key}`" in prompt, f"the prompt stopped naming {key!r}"

    def test_the_prompt_and_its_sources_still_compose(self):
        assert planning_classify.problems() == []

    def test_the_wave_row_carries_the_destination_the_vocabulary_declares(self):
        body = _read(BRIEF)
        wave = next(s for s in _shapes() if s["name"] == "wave")
        row = _row(body, "wave")
        assert row, "planner.md has no table row for the `wave` shape"
        assert wave["destination"] in row, (
            f"the wave row does not name its destination "
            f"{wave['destination']!r}"
        )
        assert wave["actor"] in row or "plan.yml" in row, (
            "the wave row lost the actor accountable for it"
        )

    def test_the_wave_row_names_the_seam(self):
        row = _row(_read(BRIEF), "wave")
        assert re.search(r"seam", row, re.I), (
            "the wave row must say a wave is ALSO the shape for a plan cut at "
            "an observation-gated seam"
        )

    def test_the_brief_points_at_the_section_by_name(self):
        assert "observation-gated seam" in _read(BRIEF), (
            "planner.md must point at the card-quality section by name rather "
            "than restating it — the copy is what drifts"
        )

    def test_the_artifact_section_carries_the_seam_wave(self):
        body = _read(BRIEF)
        headings = [m for m in re.finditer(r"^## .*$", body, re.M)]
        section = ""
        for i, m in enumerate(headings):
            if "wave plan" in m.group(0).lower():
                end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
                section = body[m.start():end]
        assert section, "planner.md no longer has a plan-artifact/wave section"
        assert re.search(r"seam", section, re.I), (
            "the artifact section says nothing about the seam-wave's two epics"
        )
        assert "depends_on" in section, (
            "the artifact section must say the wave plan's `epics` block gives "
            "the second epic `depends_on` the first"
        )
        assert "wave_commitment" in section, (
            "the artifact section must name the filer that turns the epics "
            "block into blocked-in-sequence epics (DRE-2846)"
        )


class TestTheVocabulary:
    """`config/planning-shapes.json` is what every reader means by `wave`."""

    def test_the_wave_means_names_the_seam(self):
        means = planning_shape.means("wave")
        assert _loose("observation-gated seam").search(means), (
            "the wave record's `means` must say a wave is also a plan cut at "
            "an observation-gated seam"
        )
        assert _loose("more than one plan").search(means), (
            "the wave record's `means` must still say a wave is more than one "
            "plan"
        )

    def test_the_why_names_the_card_the_rule_came_from(self):
        assert "DRE-3244" in planning_shape.record("wave")["why"]

    def test_the_briefs_copy_of_means_matches_the_vocabulary(self):
        # The table in a document is a copy, and the copy is what drifts.
        row = _row(_read(BRIEF), "wave")
        means = planning_shape.means("wave").rstrip(".")
        assert _loose(means).search(row), (
            "planner.md's wave row no longer carries the `means` the "
            "vocabulary declares:\n"
            f"  vocabulary: {means}\n  brief:      {row}"
        )

    def test_the_destination_actor_and_marks_are_untouched(self):
        record = planning_shape.record("wave")
        assert record["destination"] == "Planning"
        assert record["actor"] == "plan.yml"
        assert record["promotable"] is False
        assert record["marks"] == ["agent:planner"]

    def test_the_vocabulary_still_checks_against_the_lane_contract(self):
        assert planning_shape.config_problems() == []


class TestTheStandardsIndex:
    """`standards/README.md` is the map; a rule missing from it is a rule
    nobody finds."""

    def test_the_card_quality_row_mentions_the_seam(self):
        row = next(
            (ln for ln in _read(README).splitlines()
             if ln.startswith("| `card-quality.md`")),
            "",
        )
        assert row, "standards/README.md has no `card-quality.md` row"
        assert re.search(r"seam", row, re.I), (
            "the card-quality row does not mention the seam rule"
        )
