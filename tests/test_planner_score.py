"""Scoring the planner against a plan it has never seen (DRE-3016).

The critic has an audit (DRE-2685). The planner has had none: it decomposes an
epic, the CEO green-lights it, and whether the decomposition was any good is
learned one fix loop at a time. This is the same instrument, pointed at the
planner, and it is built on the same three rules:

  * **The held-out answer is history, and history is blind.** Every planner
    claim in the plan — the file footprint, the collision edges, the size, the
    readiness, the route, the proof/demo pair — has a mechanical answer in what
    the children actually did. None of it needs a human to re-read a card, and
    none of it was visible to the planner when it wrote the plan.
  * **A contaminated dimension is never scored.** `proof-and-demo` is enforced
    inside `plan.yml`: the planner cannot leave the workflow without the pair.
    Scoring it reports a perfect number composed entirely of what the gate
    refused to let through — DRE-2685's `hand-built`, one role over.
  * **Agreement and disagreement are equal results**, and both are printed,
    empty or not.

And the rule the replay adds: a row nobody could read reports **UNKNOWN**,
never `0` and never "clean". A missing PR is the absence of evidence; scoring
it as agreement is the audit lying in its own favour.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planner_score.py -v
"""
from __future__ import annotations

import copy
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import planner_score  # noqa: E402
import routing_verdict  # noqa: E402
import validate_card  # noqa: E402


# --------------------------------------------------------------------------
# fixtures — hand-built, never the shipped reference, so these keep meaning
# after the audit is re-run against a different set of epics
# --------------------------------------------------------------------------
def reference(**overrides) -> dict:
    doc = {
        "version": 1,
        "source": {
            "epics": ["DRE-1000"],
            "audited_on": "2026-09-04",
            "record": "docs/planner-audit.md",
            "why_it_is_blind": "history happened after the plan was written",
        },
        "dimensions": {
            "file-footprint": {
                "question": "did the card touch the files the plan said it would?",
                "scored": True,
                "values": ["within-footprint", "outside-footprint"],
                "why": "the plan's own **Files:** line, against the merged PR",
            },
            "collision": {
                "question": "did the pairs the plan called independent collide?",
                "scored": True,
                "values": ["collides", "independent"],
                "why": "blockedBy edges, against files two merged PRs share",
            },
            "size": {
                "question": "was the card one PR's worth?",
                "scored": True,
                "values": ["one-pr", "too-big"],
                "why": "the turn-cap receipts say when it was not",
            },
            "readiness": {
                "question": "was the card build-ready when it was created?",
                "scored": True,
                "values": ["build-ready", "bounced"],
                "why": "the readiness guard's own return receipt",
            },
            "routing": {
                "question": "did the card need what the routing verdict said?",
                "scored": True,
                "values": ["dispatchable", "needs-a-person"],
                "why": "an escalation from a FLEET card is a mis-route",
            },
            "approval": {
                "question": "was the plan approved as written?",
                "scored": True,
                "values": ["as-written", "revised"],
                "why": "the plan critic's holds and the amendment markers",
            },
            "proof-and-demo": {
                "question": "did the epic end with a proof card and a demo card?",
                "scored": False,
                "values": ["both-present", "missing"],
                "enforced_by": "scripts/proof_and_demo.py",
                "contaminated": (
                    "plan.yml runs the gate and bounces the epic until the pair "
                    "exists, so the planner was handed the answer face-up"
                ),
            },
        },
    }
    doc.update(overrides)
    return doc


def child(identifier, *, title=None, files=("scripts/a.py",), blocked_by=(),
          labels=(), comments=(), pr=("scripts/a.py",), verdict="FLEET"):
    """A planner-created child, with the plan's claim in its body and the
    history that answers it hanging off it."""
    body = f"Build the thing.\n\n**Files:** {', '.join(files)}\n"
    bodies = list(comments)
    if verdict:
        bodies.insert(0, routing_verdict.verdict_comment(verdict, "because"))
    return {
        "identifier": identifier,
        "title": title if title is not None else f"{identifier} · a card",
        "body": body,
        "labels": list(labels),
        "blocked_by": list(blocked_by),
        "comments": bodies,
        "pr": None if pr is None else {"number": 1, "merged": True,
                                       "files": list(pr)},
    }


def epic(identifier="DRE-1000", *, comments=()):
    return {
        "identifier": identifier,
        "title": "[EPIC] a thing",
        "body": "Do the thing.",
        "comments": list(comments),
    }


def outcomes_by_card(result, dimension):
    return {row["card"]: row["outcome"] for row in result["rows"]
            if row["dimension"] == dimension}


# --------------------------------------------------------------------------
# the contaminated dimension
# --------------------------------------------------------------------------
class ContaminationTest(unittest.TestCase):
    def test_the_proof_and_demo_row_is_excluded_not_scored(self):
        doc = reference()
        result = planner_score.score(
            epic(),
            [child("DRE-1"), child("DRE-2", title="PROOF: watch it run"),
             child("DRE-3", title="DEMO: show the CEO")],
            doc=doc,
        )
        rows = [r for r in result["rows"] if r["dimension"] == "proof-and-demo"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "excluded")
        self.assertIn("contaminated", rows[0]["why"].lower())

    def test_the_exclusion_is_what_stops_the_audit_flattering_itself(self):
        """The mutation. The contaminated row agrees by construction — the gate
        made it agree — and every scored row here disagrees. With the exclusion
        the audit reports no agreement at all; without it the same run reports
        an agreement it was handed."""
        doc = reference()
        children = [
            child("DRE-1", files=("a.py",), pr=("a.py", "b.py")),
            child("DRE-2", title="PROOF: watch it run", files=("p.py",),
                  pr=("p.py", "b.py")),
            child("DRE-3", title="DEMO: show the CEO", files=("d.py",),
                  pr=("d.py", "b.py")),
        ]
        honest = planner_score.score(epic(), children, doc=doc)
        self.assertEqual(honest["counts"]["excluded"], 1)

        flattering = copy.deepcopy(doc)
        flattering["dimensions"]["proof-and-demo"]["scored"] = True
        flattering["dimensions"]["proof-and-demo"].pop("contaminated")
        cooked = planner_score.score(epic(), children, doc=flattering)
        self.assertEqual(cooked["counts"]["excluded"], 0)
        self.assertEqual(
            cooked["counts"]["agree"] - honest["counts"]["agree"], 1,
            "the fixture proves nothing — the contaminated row did not agree",
        )

    def test_the_excluded_row_is_named_never_dropped(self):
        doc = reference()
        result = planner_score.score(epic(), [child("DRE-1")], doc=doc)
        report = planner_score.render_report(result, doc=doc)
        self.assertIn("proof-and-demo", report)
        self.assertIn("Excluded as contaminated", report)

    def test_the_contaminated_dimension_must_name_a_gate_the_pipeline_runs(self):
        """The identity check, DRE-2685's rule in this repo's terms: the
        exclusion is only honest while something really does hand the planner
        that answer. Point it at a gate nothing runs and the file is refused."""
        self.assertEqual(planner_score.reference_problems(reference()), [])
        loose = reference()
        loose["dimensions"]["proof-and-demo"]["enforced_by"] = "scripts/nope.py"
        problems = planner_score.reference_problems(loose)
        self.assertTrue(any("nope.py" in p for p in problems), problems)

    def test_the_shipped_gate_is_really_wired_into_the_plan_workflow(self):
        """Not a fixture: the live plan.yml must run the gate the shipped
        reference names, or the exclusion is a claim."""
        self.assertTrue(
            planner_score.gate_is_enforced("scripts/proof_and_demo.py"),
            "plan.yml no longer runs the proof/demo gate — the "
            "`proof-and-demo` dimension is not contaminated any more",
        )


# --------------------------------------------------------------------------
# agreement AND disagreement, per dimension
# --------------------------------------------------------------------------
class FootprintTest(unittest.TestCase):
    def test_a_pr_inside_the_declared_footprint_agrees(self):
        result = planner_score.score(
            epic(), [child("DRE-1", files=("a.py", "b.py"), pr=("a.py",))],
            doc=reference(),
        )
        row = outcomes_by_card(result, "file-footprint")
        self.assertEqual(row["DRE-1"], "agree")

    def test_a_pr_touching_a_file_the_plan_never_named_disagrees(self):
        result = planner_score.score(
            epic(), [child("DRE-1", files=("a.py",), pr=("a.py", "surprise.py"))],
            doc=reference(),
        )
        rows = [r for r in result["rows"] if r["dimension"] == "file-footprint"]
        self.assertEqual(rows[0]["outcome"], "disagree")
        self.assertEqual(rows[0]["observed"], "outside-footprint")
        self.assertIn("surprise.py", rows[0]["evidence"])

    def test_a_card_with_no_files_line_is_unclaimed_never_agreement(self):
        """The planner brief calls the `**Files:**` line the INPUT to the
        ordering. A card without one made no claim, and a claim nobody made
        cannot be right."""
        naked = child("DRE-1")
        naked["body"] = "Build the thing. No footprint anywhere."
        result = planner_score.score(epic(), [naked], doc=reference())
        row = [r for r in result["rows"] if r["dimension"] == "file-footprint"][0]
        self.assertEqual(row["outcome"], "unclaimed")
        self.assertIsNone(row["observed"])

    def test_declared_files_reads_the_planner_template_line(self):
        body = (
            "Some prose.\n\n"
            "**Files:** `scripts/a.py`, tests/test_a.py,\n"
            "           docs/a.md\n\n"
            "## Acceptance criteria\n- [ ] it works\n"
        )
        self.assertEqual(
            planner_score.declared_files(body),
            ["scripts/a.py", "tests/test_a.py", "docs/a.md"],
        )

    def test_a_prose_mention_of_files_is_not_a_declaration(self):
        """Anchored at the start of a line, the same rule every other marker in
        this pipeline follows — `the files: line` in a sentence declares
        nothing."""
        body = "We will decide which files: a.py or b.py, later.\n"
        self.assertEqual(planner_score.declared_files(body), [])


class UnknownTest(unittest.TestCase):
    """A row nobody could read is UNKNOWN — never 0, never 'clean'."""

    def test_an_unreadable_pr_is_unknown_not_agreement(self):
        result = planner_score.score(
            epic(), [child("DRE-1", pr=None)], doc=reference()
        )
        row = [r for r in result["rows"] if r["dimension"] == "file-footprint"][0]
        self.assertEqual(row["outcome"], "unknown")
        self.assertEqual(result["counts"]["agree"], 0)
        self.assertIn("could not", row["why"].lower())

    def test_a_pr_whose_file_list_would_not_load_is_unknown(self):
        blind = child("DRE-1")
        blind["pr"] = {"number": 7, "merged": True, "files": None}
        result = planner_score.score(epic(), [blind], doc=reference())
        row = [r for r in result["rows"] if r["dimension"] == "file-footprint"][0]
        self.assertEqual(row["outcome"], "unknown")

    def test_unknown_rows_are_left_out_of_the_number_and_still_printed(self):
        doc = reference()
        result = planner_score.score(epic(), [child("DRE-1", pr=None)], doc=doc)
        self.assertEqual(result["scored"],
                         result["counts"]["agree"] + result["counts"]["disagree"])
        report = planner_score.render_report(result, doc=doc)
        self.assertIn("Could not be read", report)
        self.assertIn("DRE-1", report)


class CollisionTest(unittest.TestCase):
    def test_two_cards_the_plan_left_parallel_that_shared_a_file_disagree(self):
        """DRE-2837/2838, mechanically: PRs #2206, #2207 and #2213 each passed
        full review and each went DIRTY within an hour of the others, purely on
        merge order, with no defect in any of them."""
        result = planner_score.score(
            epic(),
            [child("DRE-1", files=("console/App.tsx",), pr=("console/App.tsx",)),
             child("DRE-2", files=("console/App.tsx",), pr=("console/App.tsx",))],
            doc=reference(),
        )
        rows = [r for r in result["rows"] if r["dimension"] == "collision"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "disagree")
        self.assertEqual(rows[0]["claimed"], "independent")
        self.assertEqual(rows[0]["observed"], "collides")
        self.assertIn("DRE-1", rows[0]["card"])
        self.assertIn("DRE-2", rows[0]["card"])

    def test_a_serialized_pair_that_really_shared_a_file_agrees(self):
        result = planner_score.score(
            epic(),
            [child("DRE-1", files=("console/App.tsx",), pr=("console/App.tsx",)),
             child("DRE-2", files=("console/App.tsx",), pr=("console/App.tsx",),
                   blocked_by=("DRE-1",))],
            doc=reference(),
        )
        rows = [r for r in result["rows"] if r["dimension"] == "collision"]
        self.assertEqual(rows[0]["outcome"], "agree")
        self.assertEqual(rows[0]["claimed"], "collides")

    def test_a_pair_the_plan_serialized_that_never_touched_disagrees(self):
        """Both directions are results. Serializing two cards that never shared
        a file is three merge waits bought for nothing."""
        result = planner_score.score(
            epic(),
            [child("DRE-1", files=("a.py",), pr=("a.py",)),
             child("DRE-2", files=("b.py",), pr=("b.py",), blocked_by=("DRE-1",))],
            doc=reference(),
        )
        rows = [r for r in result["rows"] if r["dimension"] == "collision"]
        self.assertEqual(rows[0]["outcome"], "disagree")
        self.assertEqual(rows[0]["observed"], "independent")

    def test_pairs_that_neither_side_ever_names_are_not_reported_as_agreement(self):
        """Every uninteresting pair booked as an agreement is the flattery this
        module exists to refuse — n cards would score n²/2 free hits."""
        result = planner_score.score(
            epic(),
            [child("DRE-1", files=("a.py",), pr=("a.py",)),
             child("DRE-2", files=("b.py",), pr=("b.py",))],
            doc=reference(),
        )
        self.assertEqual(
            [r for r in result["rows"] if r["dimension"] == "collision"], []
        )

    def test_a_pair_with_an_edge_and_an_unreadable_pr_is_unknown(self):
        result = planner_score.score(
            epic(),
            [child("DRE-1", files=("a.py",), pr=None),
             child("DRE-2", files=("a.py",), pr=("a.py",), blocked_by=("DRE-1",))],
            doc=reference(),
        )
        rows = [r for r in result["rows"] if r["dimension"] == "collision"]
        self.assertEqual(rows[0]["outcome"], "unknown")


class SizeAndReadinessTest(unittest.TestCase):
    def test_a_card_that_died_at_the_turn_cap_disagrees_on_size(self):
        result = planner_score.score(
            epic(),
            [child("DRE-1", comments=(planner_score.TURN_CAP_RECEIPT_SAMPLE,))],
            doc=reference(),
        )
        row = [r for r in result["rows"] if r["dimension"] == "size"][0]
        self.assertEqual(row["outcome"], "disagree")
        self.assertEqual(row["observed"], "too-big")

    def test_a_card_that_merged_without_a_turn_cap_agrees_on_size(self):
        result = planner_score.score(epic(), [child("DRE-1")], doc=reference())
        row = [r for r in result["rows"] if r["dimension"] == "size"][0]
        self.assertEqual(row["outcome"], "agree")

    def test_a_card_the_readiness_guard_returned_disagrees(self):
        result = planner_score.score(
            epic(),
            [child("DRE-1", comments=(
                planner_score.READINESS_BOUNCE_PREFIX
                + " repo: label. Returned to Planning;",))],
            doc=reference(),
        )
        row = [r for r in result["rows"] if r["dimension"] == "readiness"][0]
        self.assertEqual(row["outcome"], "disagree")
        self.assertEqual(row["observed"], "bounced")

    def test_a_card_that_never_ran_is_unknown_on_size_and_readiness(self):
        result = planner_score.score(
            epic(), [child("DRE-1", pr=None)], doc=reference()
        )
        for dimension in ("size", "readiness"):
            row = [r for r in result["rows"] if r["dimension"] == dimension][0]
            self.assertEqual(row["outcome"], "unknown", dimension)


class RoutingTest(unittest.TestCase):
    def test_a_fleet_card_that_escalated_is_a_mis_route(self):
        result = planner_score.score(
            epic(),
            [child("DRE-1", comments=(
                planner_score.ESCALATION_RECEIPT_PREFIX + " should we charge?",))],
            doc=reference(),
        )
        row = [r for r in result["rows"] if r["dimension"] == "routing"][0]
        self.assertEqual(row["claimed"], "dispatchable")
        self.assertEqual(row["observed"], "needs-a-person")
        self.assertEqual(row["outcome"], "disagree")

    def test_a_fleet_card_that_shipped_agrees(self):
        result = planner_score.score(epic(), [child("DRE-1")], doc=reference())
        row = [r for r in result["rows"] if r["dimension"] == "routing"][0]
        self.assertEqual(row["outcome"], "agree")

    def test_a_card_carrying_no_routing_verdict_is_unclaimed(self):
        result = planner_score.score(
            epic(), [child("DRE-1", verdict=None)], doc=reference()
        )
        row = [r for r in result["rows"] if r["dimension"] == "routing"][0]
        self.assertEqual(row["outcome"], "unclaimed")

    def test_a_hand_built_verdict_claims_a_person_and_agrees_when_one_was_needed(self):
        result = planner_score.score(
            epic(),
            [child("DRE-1", verdict="WORKBENCH", comments=(
                planner_score.ESCALATION_RECEIPT_PREFIX + " which way?",))],
            doc=reference(),
        )
        row = [r for r in result["rows"] if r["dimension"] == "routing"][0]
        self.assertEqual(row["claimed"], "needs-a-person")
        self.assertEqual(row["outcome"], "agree")


class ApprovalTest(unittest.TestCase):
    def test_a_plan_the_critic_sent_back_disagrees(self):
        import plan_critic

        result = planner_score.score(
            epic(comments=(plan_critic.marker(
                "pre", 1, plan_critic.SEND_BACK, "two cards own one file"),)),
            [child("DRE-1")], doc=reference(),
        )
        row = [r for r in result["rows"] if r["dimension"] == "approval"][0]
        self.assertEqual(row["outcome"], "disagree")
        self.assertEqual(row["observed"], "revised")

    def test_a_critic_round_that_passed_is_not_read_as_a_send_back(self):
        """The mutation: the same shape of marker, the other result. Matching
        the prefix rather than the result would book every planned epic as
        revised."""
        import plan_critic

        result = planner_score.score(
            epic(comments=(plan_critic.marker("pre", 1, plan_critic.PASS),)),
            [child("DRE-1")], doc=reference(),
        )
        row = [r for r in result["rows"] if r["dimension"] == "approval"][0]
        self.assertEqual(row["outcome"], "agree")

    def test_an_amended_epic_disagrees(self):
        result = planner_score.score(
            epic(comments=(f"🔁 {planner_score.AMENDMENT_TAG}: the plan no longer "
                           "describes the work",)),
            [child("DRE-1")], doc=reference(),
        )
        row = [r for r in result["rows"] if r["dimension"] == "approval"][0]
        self.assertEqual(row["outcome"], "disagree")

    def test_a_plan_that_ran_untouched_agrees(self):
        result = planner_score.score(epic(), [child("DRE-1")], doc=reference())
        row = [r for r in result["rows"] if r["dimension"] == "approval"][0]
        self.assertEqual(row["outcome"], "agree")

    def test_an_epic_whose_children_never_shipped_is_unknown(self):
        result = planner_score.score(
            epic(), [child("DRE-1", pr=None)], doc=reference()
        )
        row = [r for r in result["rows"] if r["dimension"] == "approval"][0]
        self.assertEqual(row["outcome"], "unknown")


# --------------------------------------------------------------------------
# the report — both halves, empty or not
# --------------------------------------------------------------------------
class ReportTest(unittest.TestCase):
    def test_both_halves_are_printed_even_when_one_is_empty(self):
        doc = reference()
        result = planner_score.score(epic(), [child("DRE-1")], doc=doc)
        report = planner_score.render_report(result, doc=doc)
        self.assertEqual(result["counts"]["disagree"], 0)
        for heading in ("## Agreement", "## Disagreement"):
            self.assertIn(heading, report)
        self.assertIn("*(none)*", report)

    def test_the_report_names_the_epic_it_scored(self):
        doc = reference()
        result = planner_score.score(epic("DRE-1234"), [child("DRE-1")], doc=doc)
        self.assertIn("DRE-1234", planner_score.render_report(result, doc=doc))

    def test_a_disagreement_is_never_printed_under_agreement(self):
        doc = reference()
        result = planner_score.score(
            epic(), [child("DRE-9", files=("a.py",), pr=("a.py", "b.py"))], doc=doc
        )
        report = planner_score.render_report(result, doc=doc)
        agreement, _, rest = report.partition("## Disagreement")
        self.assertIn("| DRE-9 | `file-footprint` |", rest)
        self.assertNotIn("`file-footprint`", agreement)


# --------------------------------------------------------------------------
# the replay harness — what must not happen
# --------------------------------------------------------------------------
class ReplaySafetyTest(unittest.TestCase):
    def test_the_replay_files_its_epic_in_the_demo_repo_and_nowhere_else(self):
        card = planner_score.replay_card("DRE-1000", 3, "the epic as written")
        self.assertIn(f"repo:{planner_score.REPLAY_REPO}", card["labels"])
        self.assertEqual(planner_score.REPLAY_REPO, "agent-bureau-demo")
        self.assertEqual(planner_score.replay_problems(card), [])

    def test_the_replay_title_marks_it_as_a_throwaway(self):
        card = planner_score.replay_card("DRE-1000", 3, "the epic as written")
        self.assertTrue(card["title"].startswith(f"{planner_score.REPLAY_PREFIX}3"))
        self.assertIn("DRE-1000", card["title"])

    def test_a_replay_pointed_at_a_product_repo_is_refused(self):
        """The single hard rule: the harness never writes to a product repo and
        never files a card outside the demo repo."""
        for slug in ("portico", "atlas", "bureau-pipeline"):
            card = planner_score.replay_card("DRE-1000", 1, "text")
            card["labels"] = [f"repo:{slug}", "agent:planner"]
            problems = planner_score.replay_problems(card)
            self.assertTrue(any(slug in p for p in problems), (slug, problems))

    def test_a_replay_with_no_repo_label_at_all_is_refused(self):
        card = planner_score.replay_card("DRE-1000", 1, "text")
        card["labels"] = ["agent:planner"]
        self.assertTrue(planner_score.replay_problems(card))

    def test_the_demo_repo_is_a_slug_the_rail_actually_routes(self):
        self.assertIn(planner_score.REPLAY_REPO, validate_card.VALID_SLUGS)

    def test_a_replay_that_is_not_a_throwaway_title_is_refused(self):
        card = planner_score.replay_card("DRE-1000", 1, "text")
        card["title"] = "[EPIC] ship the real thing"
        self.assertTrue(any(planner_score.REPLAY_PREFIX in p
                            for p in planner_score.replay_problems(card)))


class FreezeTest(unittest.TestCase):
    def test_the_frozen_text_drops_the_planner_written_region(self):
        """The replay gets the epic as the CEO wrote it. The growth record is
        the planner's own output spliced into the description — handing it back
        is handing the plan back."""
        import mid_epic

        description = (
            "Do the thing.\n\n"
            f"{mid_epic.ARTIFACT_BEGIN}\n"
            "Green-lit at 4 cards, running 6.\n"
            f"{mid_epic.ARTIFACT_END}\n"
            "\nAnd nothing else.\n"
        )
        frozen = planner_score.pre_plan_text(description)
        self.assertIn("Do the thing.", frozen)
        self.assertIn("And nothing else.", frozen)
        self.assertNotIn("Green-lit at 4 cards", frozen)
        self.assertNotIn(mid_epic.ARTIFACT_BEGIN, frozen)


class LeakTest(unittest.TestCase):
    """If the historical plan text leaks into the replay's context, that replay
    is discarded and the leak recorded. A replay that can see the answer is not
    a replay."""

    PLAN = (
        "## The cards\n\n"
        "DRE-1001 — extract the tenant-scoped response store behind a façade\n"
        "DRE-1002 — teach the poll to return every group, not one\n"
    )

    def test_a_leaked_plan_line_is_found_in_the_replay_context(self):
        context = (
            "Plan this epic.\n\n"
            "DRE-1001 — extract the tenant-scoped response store behind a façade\n"
        )
        leaks = planner_score.plan_leaks(context, self.PLAN)
        self.assertTrue(leaks)
        self.assertIn("tenant-scoped response store", leaks[0])

    def test_a_clean_context_leaks_nothing(self):
        context = "Plan this epic. Here is the epic as the CEO wrote it: do the thing."
        self.assertEqual(planner_score.plan_leaks(context, self.PLAN), [])

    def test_boilerplate_the_plan_shares_with_every_plan_is_not_a_leak(self):
        """`## The cards` is in every plan artifact and in the planner's brief.
        Reporting it would make every replay a leak, which is the same as
        reporting none."""
        leaks = planner_score.plan_leaks("Write a section called ## The cards.",
                                         self.PLAN)
        self.assertEqual(leaks, [])

    def test_a_leaked_replay_is_discarded_rather_than_scored(self):
        doc = reference()
        replay = {"epic": "DRE-1000", "context": self.PLAN, "plan": self.PLAN}
        with self.assertRaises(planner_score.LeakedPlan):
            planner_score.score(epic(), [child("DRE-1")], doc=doc, replay=replay)

    def test_the_leak_is_recorded_not_just_refused(self):
        record = planner_score.leak_record("DRE-1000", ["a leaked line"])
        self.assertIn("DRE-1000", record)
        self.assertIn("a leaked line", record)
        self.assertIn("discarded", record.lower())

    def test_a_clean_replay_scores(self):
        doc = reference()
        replay = {"epic": "DRE-1000", "context": "the epic as the CEO wrote it",
                  "plan": self.PLAN}
        result = planner_score.score(epic(), [child("DRE-1")], doc=doc,
                                     replay=replay)
        self.assertEqual(result["replay"]["leaks"], [])


# --------------------------------------------------------------------------
# collecting the history — an unreadable repo is never a clean sheet
# --------------------------------------------------------------------------
class FakeLinear:
    """Records every call. `collect` READS; it writes nothing anywhere."""

    def __init__(self, children):
        self.children = children
        self.writes: list = []

    def gql(self, _query, variables):
        return {"issue": {
            "identifier": variables["id"], "title": "[EPIC] a thing",
            "description": "Do the thing.",
            "children": {"nodes": self.children},
        }}

    @staticmethod
    def child_detail_records(nodes):
        import linear_ops

        return linear_ops.child_detail_records(nodes)

    @staticmethod
    def comment_bodies(_identifier):
        return []

    def cmd_comment(self, *a):  # pragma: no cover - must not run
        self.writes.append(a)

    def cmd_state(self, *a):  # pragma: no cover - must not run
        self.writes.append(a)


class CollectTest(unittest.TestCase):
    NODES = [{
        "identifier": "DRE-1", "title": "a card", "createdAt": "2026-01-01",
        "description": "**Files:** a.py",
        "labels": {"nodes": [{"name": "repo:agent-bureau"}]},
        "inverseRelations": {"nodes": []},
    }]

    def test_a_repo_this_token_cannot_see_is_unknown_never_a_clean_sheet(self):
        """The DRE-2034 class, one seam over and measured on this run:
        `gh pr list --repo <invisible> --search head:agent/DRE-N` exits 0 and
        prints `[]`. Nothing failed, so `card_pr`'s rc guard never fires, and
        every child of an unreadable repo would score a perfect footprint on a
        file list nobody ever read."""
        lops = FakeLinear(self.NODES)
        history = planner_score.collect(
            "DRE-1000", lops=lops,
            finder=lambda *a, **k: None,          # what gh really returns
            readable=lambda _repo: False,
        )
        self.assertIsNone(history["children"][0]["pr"])
        self.assertIn("cannot read", history["children"][0]["pr_unreadable"])

        result = planner_score.score(history["epic"], history["children"],
                                     doc=reference())
        row = [r for r in result["rows"] if r["dimension"] == "file-footprint"][0]
        self.assertEqual(row["outcome"], "unknown")
        self.assertEqual(result["counts"]["agree"], 0)

    def test_a_readable_repo_is_looked_up_and_scored(self):
        """The other half of the mutation: with the same fake PR, a repo the
        token CAN see produces a real row rather than an unknown."""
        lops = FakeLinear(self.NODES)
        history = planner_score.collect(
            "DRE-1000", lops=lops,
            finder=lambda *a, **k: {"number": 3, "state": "MERGED",
                                    "files": [{"path": "a.py"}]},
            readable=lambda _repo: True,
        )
        result = planner_score.score(history["epic"], history["children"],
                                     doc=reference())
        row = [r for r in result["rows"] if r["dimension"] == "file-footprint"][0]
        self.assertEqual(row["outcome"], "agree")

    def test_the_repo_is_probed_once_per_repo_not_once_per_card(self):
        nodes = [dict(self.NODES[0], identifier=f"DRE-{n}") for n in (1, 2, 3)]
        probes: list = []
        planner_score.collect(
            "DRE-1000", lops=FakeLinear(nodes),
            finder=lambda *a, **k: None,
            readable=lambda repo: probes.append(repo) or True,
        )
        self.assertEqual(probes, ["dreadnought-foundry/agent-bureau"])

    def test_collect_writes_nothing_to_linear(self):
        lops = FakeLinear(self.NODES)
        planner_score.collect("DRE-1000", lops=lops,
                              finder=lambda *a, **k: None,
                              readable=lambda _repo: True)
        self.assertEqual(lops.writes, [])


# --------------------------------------------------------------------------
# the reference file checks itself
# --------------------------------------------------------------------------
class ReferenceTest(unittest.TestCase):
    def test_the_shipped_reference_is_well_formed(self):
        self.assertEqual(planner_score.reference_problems(), [])

    def test_every_dimension_the_scorer_emits_is_declared(self):
        doc = planner_score.load()
        self.assertEqual(
            sorted(planner_score.DIMENSIONS), sorted(planner_score.dimensions(doc))
        )

    def test_every_value_a_row_can_carry_is_declared_by_its_dimension(self):
        """The DRE-2685 rule that cost the most to learn: a value the reference
        cannot hold comes out as a disagreement neither side ever expressed."""
        doc = planner_score.load()
        for name, values in planner_score.EMITTED_VALUES.items():
            declared = set(planner_score.dimensions(doc)[name]["values"])
            self.assertTrue(
                set(values) <= declared,
                f"{name} can emit {sorted(set(values) - declared)}, which the "
                f"dimension does not carry",
            )

    def test_a_dimension_excluded_without_a_reason_is_a_problem(self):
        doc = reference()
        doc["dimensions"]["size"]["scored"] = False
        self.assertTrue(any("size" in p for p in planner_score.reference_problems(doc)))

    def test_a_dimension_with_no_question_is_a_problem(self):
        doc = reference()
        doc["dimensions"]["size"]["question"] = ""
        self.assertTrue(any("size" in p for p in planner_score.reference_problems(doc)))

    def test_the_source_names_the_epics_it_audited(self):
        doc = planner_score.load()
        epics = planner_score.source(doc)["epics"]
        self.assertTrue(epics, "an audit that names no epic is not evidence")
        for identifier in epics:
            self.assertRegex(identifier, r"^[A-Z]+-\d+$")

    def test_an_epic_identifier_that_is_not_one_is_a_problem(self):
        doc = reference()
        doc["source"]["epics"] = ["the forms epic"]
        self.assertTrue(planner_score.reference_problems(doc))


# --------------------------------------------------------------------------
# the receipts are the pipeline's own, never a second spelling
# --------------------------------------------------------------------------
class ReceiptWiringTest(unittest.TestCase):
    """Every marker this module matches on is written somewhere else in the
    repo. A second spelling here reads as "nothing ever happened", which is the
    silent-zero this audit exists to refuse."""

    def test_the_readiness_bounce_prefix_is_the_guards_own_words(self):
        source = (ROOT / "scripts" / "validate_card.py").read_text(encoding="utf-8")
        self.assertIn(planner_score.READINESS_BOUNCE_PREFIX, source)

    def test_the_escalation_prefix_is_the_workflows_own_words(self):
        source = (ROOT / ".github" / "workflows" / "agent-task.yml").read_text(
            encoding="utf-8")
        self.assertIn(planner_score.ESCALATION_RECEIPT_PREFIX, source)

    def test_the_handback_prefix_is_the_workflows_own_words(self):
        source = (ROOT / ".github" / "workflows" / "agent-task.yml").read_text(
            encoding="utf-8")
        self.assertIn(planner_score.HANDBACK_RECEIPT_PREFIX, source)

    def test_the_turn_cap_tag_is_dead_runs_own_constant(self):
        import dead_run

        self.assertEqual(planner_score.TURN_CAP_TAG, dead_run.TURN_TAG)
        self.assertIn(planner_score.TURN_CAP_TAG,
                      planner_score.TURN_CAP_RECEIPT_SAMPLE)

    def test_the_amendment_tag_is_mid_epics_own_constant(self):
        import mid_epic

        self.assertEqual(planner_score.AMENDMENT_TAG, mid_epic.AMENDMENT_TAG)


# --------------------------------------------------------------------------
# the harness workflow
# --------------------------------------------------------------------------
class WorkflowWiringTest(unittest.TestCase):
    def setUp(self):
        import yaml

        self.workflows = ROOT / ".github" / "workflows"
        self.reusable = yaml.safe_load(
            (self.workflows / "planner-replay.yml").read_text(encoding="utf-8"))
        self.stub = yaml.safe_load(
            (self.workflows / "self-planner-replay.yml").read_text(encoding="utf-8"))

    @staticmethod
    def _on(doc):
        on = doc.get("on", doc.get(True))
        return on if isinstance(on, dict) else {}

    def test_the_harness_runs_on_demand_and_never_on_a_schedule(self):
        """Same decision as the groomer's D5: a replay files cards and spends a
        planner run, so it runs when someone asks for it."""
        self.assertIn("workflow_dispatch", self._on(self.stub))
        self.assertNotIn("schedule", self._on(self.stub))
        self.assertNotIn("schedule", self._on(self.reusable))

    def test_the_stub_calls_the_reusable_at_the_qualified_main_ref(self):
        job = next(iter(self.stub["jobs"].values()))
        self.assertEqual(
            job.get("uses"),
            "dreadnought-foundry/bureau-pipeline/.github/workflows/"
            "planner-replay.yml@main",
        )
        self.assertEqual(job.get("secrets"), "inherit")

    def test_the_harness_asks_for_no_write_access_to_any_repo(self):
        for doc, name in ((self.stub, "stub"), (self.reusable, "reusable")):
            for value in (doc.get("permissions") or {}).values():
                self.assertEqual(value, "read", f"{name} asks for write access")

    def test_the_harness_names_the_demo_repo_and_no_other(self):
        body = (self.workflows / "planner-replay.yml").read_text(encoding="utf-8")
        self.assertIn(planner_score.REPLAY_REPO, body)
        for slug in validate_card.VALID_SLUGS - {planner_score.REPLAY_REPO}:
            # \b-anchored: `repo:agent-bureau-demo` is not a mention of
            # `agent-bureau`, the same substring trap DRE-2025 fixed for card
            # identifiers in head refs.
            self.assertIsNone(
                re.search(rf"repo:{re.escape(slug)}\b(?!-)", body),
                f"the replay workflow names {slug} — it may only file cards in "
                f"{planner_score.REPLAY_REPO}",
            )

    def test_the_harness_runs_this_modules_guard_before_it_files_anything(self):
        body = (self.workflows / "planner-replay.yml").read_text(encoding="utf-8")
        self.assertIn("planner_score.py", body)
        self.assertIn("replay-card", body)


# --------------------------------------------------------------------------
# the run that was actually made
# --------------------------------------------------------------------------
class DocumentationTest(unittest.TestCase):
    def setUp(self):
        self.doc = (ROOT / "docs" / "planner-audit.md").read_text(encoding="utf-8")

    def test_the_document_reports_both_directions(self):
        for heading in ("## Agreement", "## Disagreement"):
            self.assertIn(heading, self.doc)

    def test_the_document_states_the_exclusion(self):
        self.assertIn("proof-and-demo", self.doc)
        self.assertIn("contaminated", self.doc)

    def test_the_document_states_the_unknown_rule(self):
        self.assertIn("UNKNOWN", self.doc)

    def test_the_document_names_the_epics_the_audit_ran_on(self):
        for identifier in planner_score.source()["epics"]:
            self.assertIn(identifier, self.doc)


if __name__ == "__main__":                      # pragma: no cover
    unittest.main()
