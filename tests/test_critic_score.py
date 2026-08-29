"""Scoring the critic against a review it has never seen (DRE-2685).

A critic nobody has audited is a critic nobody should trust. The audit is a
per-card comparison against DRE-2649 — the independent Forms review, read by a
human, recorded before any of this existed and deliberately not shown to the
classifier.

Two things the comparison lives or dies on, and both are tested here:

  * **The `hand-built` dimension is excluded as contaminated.** That judgement
    was read during planning and quoted in the plan, so scoring it grades the
    critic on an answer it was handed. It is the difference between a real
    blind comparison and one that flatters itself, and on the real population
    it is the whole of the flattery: every card the mechanical layer resolves
    without a model is resolved by a label or a title convention set during
    planning.
  * **Agreement OR disagreement is the result, and both are reported.** An
    audit that only records the hits is a marketing document.

And the third rule, D3 (approved by the operator 2026-08-23): a card the
critic could not classify **raises an alert naming it and moves**. Never a
silent hold — every expensive failure of 2026-08-22 looked like nothing
happening.

Run: cd bureau-pipeline && python3 -m pytest tests/test_critic_score.py -v
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import critic_score  # noqa: E402
import lane_contract  # noqa: E402
import routing_verdict  # noqa: E402


# --------------------------------------------------------------------------
# fixtures — hand-built, never the shipped reference, so these keep meaning
# after the real audit is re-run against a different population
# --------------------------------------------------------------------------
def reference(*judgements, **overrides) -> dict:
    doc = {
        "version": 1,
        "source": {
            "card": "DRE-0000",
            "title": "a review nobody showed the critic",
            "reviewed_on": "2026-08-22",
            "record": "a comment on the review card",
        },
        "dimensions": {
            "buildability": {
                "question": "can this card be built from what it says?",
                "scored": True,
                "values": ["buildable", "not-buildable"],
            },
            "plan": {
                "question": "does the epic's plan hold together?",
                "scored": True,
                "values": ["plan-test"],
            },
            "hand-built": {
                "question": "must a person look at it?",
                "scored": False,
                "values": ["needs-a-person", "dispatchable"],
                "contaminated": (
                    "read during planning and quoted in the plan, so the "
                    "labels the critic reads it off were set by the answer"
                ),
            },
        },
        "judgements": list(judgements),
    }
    doc.update(overrides)
    return doc


def judgement(card, judgement_, dimension="buildability", **extra) -> dict:
    row = {
        "card": card,
        "dimension": dimension,
        "judgement": judgement_,
        "basis": "named",
        "finding": "1 · BLOCKING",
        "quote": "the review said so, in these words",
    }
    row.update(extra)
    return row


def card(identifier, *, title=None, description=None, labels=(), children=False):
    return {
        "identifier": identifier,
        "title": title if title is not None else f"{identifier} · a card",
        # A card the classifier can actually resolve: a static-visual criterion
        # is FLEET-checkable, so the default fixture routes without a model and
        # the tests that need an UNRESOLVED card say so explicitly.
        "description": description if description is not None else (
            "Some prose.\n\n## Acceptance criteria\n\n"
            "- [ ] the screen matches the design\n"
        ),
        "labels": list(labels),
        "has_children": children,
        "read": critic_score.FULL_READ,
    }


class FakeLinear:
    """Records every write. The audit reads; the escalation writes exactly two
    things per card, and NEVER a description — rewriting a card's acceptance
    criteria is inventing the requirement the card exists to carry."""

    def __init__(self, *, state_raises=False):
        self.comments: list[tuple] = []
        self.states: list[tuple] = []
        self.descriptions: list[tuple] = []
        self.state_raises = state_raises

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))

    def cmd_state(self, identifier, state):
        if self.state_raises:
            raise RuntimeError("Linear said no")
        self.states.append((identifier, state))

    def set_description(self, identifier, body):  # pragma: no cover - must not run
        self.descriptions.append((identifier, body))


# --------------------------------------------------------------------------
# the contaminated dimension
# --------------------------------------------------------------------------
class ContaminationTest(unittest.TestCase):
    def test_a_hand_built_judgement_is_excluded_not_scored(self):
        doc = reference(judgement("DRE-1", "needs-a-person", dimension="hand-built"))
        result = critic_score.score(
            [card("DRE-1", title="DEMO: prove it works end to end")], doc=doc
        )
        row = result["rows"][0]
        self.assertEqual(row["outcome"], "excluded")
        self.assertIn("contaminated", row["why"].lower())
        self.assertEqual(result["counts"]["agree"], 0)

    def test_the_exclusion_is_what_stops_the_audit_flattering_itself(self):
        """The mutation. Contaminated rows that would all AGREE, scored rows
        that all disagree: with the exclusion the audit reports no agreement,
        and without it the same run reports a perfect score it did not earn."""
        doc = reference(
            judgement("DRE-1", "needs-a-person", dimension="hand-built"),
            judgement("DRE-2", "needs-a-person", dimension="hand-built"),
            judgement("DRE-3", "not-buildable"),
        )
        cards = [
            card("DRE-1", title="DEMO: prove it works"),
            card("DRE-2", labels=["agent:ops"]),
            card("DRE-3"),
        ]
        honest = critic_score.score(cards, doc=doc)
        self.assertEqual(honest["counts"]["agree"], 0)
        self.assertEqual(honest["counts"]["excluded"], 2)

        flattering = copy.deepcopy(doc)
        flattering["dimensions"]["hand-built"]["scored"] = True
        flattering["dimensions"]["hand-built"].pop("contaminated")
        cooked = critic_score.score(cards, doc=flattering)
        self.assertEqual(cooked["counts"]["agree"], 2, "the fixture proves nothing")
        self.assertEqual(cooked["counts"]["excluded"], 0)

    def test_the_excluded_rows_are_named_never_dropped(self):
        doc = reference(judgement("DRE-1", "needs-a-person", dimension="hand-built"))
        result = critic_score.score([card("DRE-1", labels=["no-code"])], doc=doc)
        report = critic_score.render_report(result, doc=doc)
        self.assertIn("DRE-1", report)
        self.assertIn("hand-built", report)

    def test_every_verdict_that_marks_hand_built_collapses_with_fleet(self):
        """The exclusion is only honest if the distinction it drops is exactly
        the one the vocabulary marks `hand-built`. A sixth verdict marking it
        while meaning something else would be scored as buildability."""
        marked = [
            name for name in routing_verdict.verdicts()
            if critic_score.CONTAMINATED_MARK in routing_verdict.marks(name)
        ]
        self.assertTrue(marked, "the vocabulary marks no verdict hand-built")
        for name in marked:
            self.assertEqual(
                critic_score.judgement_of(name), critic_score.judgement_of("FLEET"),
                f"{name} marks hand-built but does not collapse with FLEET",
            )
        self.assertEqual(critic_score.judgement_of("NEEDS WORK"), "not-buildable")


# --------------------------------------------------------------------------
# agreement AND disagreement
# --------------------------------------------------------------------------
class ScoringTest(unittest.TestCase):
    def test_agreement_and_disagreement_are_both_results(self):
        doc = reference(
            judgement("DRE-1", "not-buildable"),
            judgement("DRE-2", "not-buildable"),
        )
        cards = [
            # states no acceptance criteria at all — the critic agrees
            card("DRE-1", description="no criteria here at all"),
            # states a criterion the critic can satisfy — the critic does not
            card("DRE-2"),
        ]
        result = critic_score.score(cards, doc=doc)
        outcomes = {row["card"]: row["outcome"] for row in result["rows"]}
        self.assertEqual(outcomes["DRE-1"], "agree")
        self.assertEqual(outcomes["DRE-2"], "disagree")
        report = critic_score.render_report(result, doc=doc)
        self.assertIn("Agreement", report)
        self.assertIn("Disagreement", report)

    def test_a_disagreement_records_both_answers(self):
        doc = reference(judgement("DRE-1", "not-buildable"))
        result = critic_score.score([card("DRE-1")], doc=doc)
        row = result["rows"][0]
        self.assertEqual(row["outcome"], "disagree")
        self.assertEqual(row["reference"], "not-buildable")
        self.assertEqual(row["observed"], "buildable")
        self.assertTrue(row["quote"], "a judgement with no quote cannot be audited")

    def test_an_epic_is_scored_on_the_plan_test_never_buildability(self):
        doc = reference(judgement("DRE-9", "plan-test", dimension="plan"))
        result = critic_score.score(
            [card("DRE-9", title="[EPIC] Forms", labels=["agent:planner"],
                  children=True)],
            doc=doc,
        )
        row = result["rows"][0]
        self.assertEqual(row["outcome"], "agree")
        self.assertEqual(row["observed"], "plan-test")
        self.assertEqual(row["observed_source"], "epic")

    def test_a_stamped_verdict_is_read_before_the_classifier_is_asked(self):
        """What is being audited is what the critic SAID, when it said
        anything. Re-deriving it would score the classifier against itself."""
        doc = reference(judgement("DRE-1", "not-buildable"))
        comment = routing_verdict.verdict_comment("NEEDS WORK", "it names no route")
        result = critic_score.score(
            [card("DRE-1")], doc=doc, comments={"DRE-1": [comment]}
        )
        row = result["rows"][0]
        self.assertEqual(row["observed_source"], "stamped")
        self.assertEqual(row["outcome"], "agree")

    def test_a_card_the_reference_judges_but_nobody_read_is_reported(self):
        doc = reference(judgement("DRE-404", "buildable"))
        result = critic_score.score([], doc=doc)
        self.assertEqual(result["rows"][0]["outcome"], "unread")
        self.assertEqual(result["counts"]["unread"], 1)


# --------------------------------------------------------------------------
# D3 — a card the critic could not classify is never held silently
# --------------------------------------------------------------------------
class UnclassifiedTest(unittest.TestCase):
    def unclassified_result(self):
        doc = reference(judgement("DRE-1", "not-buildable"))
        cards = [card("DRE-1", description=(
            "## Acceptance criteria\n\n- [ ] the store keeps the answer\n"
        ))]
        result = critic_score.score(cards, doc=doc)
        self.assertEqual(result["rows"][0]["outcome"], "unclassified")
        return result

    def test_a_judgement_call_with_no_recorded_verdict_is_unclassified(self):
        result = self.unclassified_result()
        self.assertEqual(result["counts"]["unclassified"], 1)
        self.assertIn("judgement", result["rows"][0]["why"])

    def test_the_alert_names_the_card_and_the_card_moves(self):
        lops = FakeLinear()
        result = self.unclassified_result()
        moved = critic_score.escalate(lops, result)
        self.assertEqual(moved, ["DRE-1"])
        self.assertEqual(len(lops.comments), 1)
        identifier, body = lops.comments[0]
        self.assertEqual(identifier, "DRE-1")
        self.assertIn("DRE-1", body)
        self.assertTrue(body.lstrip().startswith(f"🚨 {critic_score.ESCALATE_TAG}:"))
        self.assertEqual(lops.states, [("DRE-1", critic_score.escalation_lane())])

    def test_the_alert_is_posted_before_the_move_so_a_failure_is_never_silent(self):
        lops = FakeLinear(state_raises=True)
        result = self.unclassified_result()
        with self.assertRaises(RuntimeError):
            critic_score.escalate(lops, result)
        self.assertEqual(len(lops.comments), 1, "the card moved with nothing said")

    def test_the_escalation_lane_is_read_from_the_vocabulary_not_a_literal(self):
        lane = critic_score.escalation_lane()
        self.assertEqual(lane, routing_verdict.destination("NEEDS WORK"))
        self.assertIn(lane, lane_contract.lane_names(status="live"))

    def test_the_audit_never_rewrites_a_card(self):
        """The critic names the missing thing; it never writes the criteria.
        That would invent the requirement the card exists to carry."""
        lops = FakeLinear()
        critic_score.escalate(lops, self.unclassified_result())
        self.assertEqual(lops.descriptions, [])


# --------------------------------------------------------------------------
# the reference file checks itself
# --------------------------------------------------------------------------
class ReferenceTest(unittest.TestCase):
    def test_the_shipped_reference_is_well_formed(self):
        self.assertEqual(critic_score.reference_problems(), [])

    def test_the_shipped_reference_is_the_forms_review(self):
        doc = critic_score.load()
        self.assertEqual(critic_score.source(doc)["card"], "DRE-2649")

    def test_the_contaminated_dimension_says_why(self):
        doc = critic_score.load()
        contaminated = [
            name for name, block in critic_score.dimensions(doc).items()
            if not block.get("scored")
        ]
        self.assertIn(critic_score.CONTAMINATED_MARK, contaminated)
        for name in contaminated:
            self.assertTrue(
                critic_score.dimensions(doc)[name].get("contaminated", "").strip(),
                f"dimension {name!r} is excluded and does not say why",
            )

    def test_every_judgement_cites_the_review(self):
        for row in critic_score.judgements():
            self.assertTrue(row.get("quote", "").strip(),
                            f"{row['card']} carries no quote from the review")
            self.assertIn(row.get("basis"), ("named", "blanket"))

    def test_a_judgement_naming_an_unknown_dimension_is_a_problem(self):
        doc = reference(judgement("DRE-1", "buildable", dimension="vibes"))
        self.assertTrue(any("vibes" in p for p in critic_score.reference_problems(doc)))

    def test_a_judgement_with_no_quote_is_a_problem(self):
        doc = reference(judgement("DRE-1", "buildable", quote=""))
        self.assertTrue(any("quote" in p for p in critic_score.reference_problems(doc)))

    def test_a_judgement_value_the_dimension_does_not_carry_is_a_problem(self):
        doc = reference(judgement("DRE-1", "looks-fine"))
        self.assertTrue(
            any("looks-fine" in p for p in critic_score.reference_problems(doc))
        )

    def test_the_escalation_lane_must_permit_this_writer(self):
        """Binding the destination to the lane contract, the way the routing
        vocabulary binds its own: a card moved to a lane this writer may not
        write is the DRE-2824 dead end, one field over."""
        self.assertIn(
            "critic_score.py",
            lane_contract.lane_writers(critic_score.escalation_lane()),
        )


# --------------------------------------------------------------------------
# the run that was actually made
# --------------------------------------------------------------------------
class DocumentationTest(unittest.TestCase):
    def setUp(self):
        self.doc = (ROOT / "docs" / "critic-score-dre2649.md").read_text(
            encoding="utf-8"
        )

    def test_the_proof_names_the_review_and_reports_both_directions(self):
        self.assertIn("DRE-2649", self.doc)
        for heading in ("## Agreement", "## Disagreement"):
            self.assertIn(heading, self.doc)

    def test_the_proof_states_the_exclusion(self):
        self.assertIn("hand-built", self.doc)
        self.assertIn("contaminated", self.doc)


if __name__ == "__main__":                      # pragma: no cover
    unittest.main()
