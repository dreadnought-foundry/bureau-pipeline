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
    inside `plan.yml`: the planner cannot leave the workflow without the proof card.
    Scoring it reports a perfect number composed entirely of what the gate
    refused to let through — DRE-2685's `hand-built`, one role over.
  * **Agreement and disagreement are equal results**, and both are printed,
    empty or not.

DRE-3079 adds the seventh dimension and the one number DRE-3022 asked to be
measured by: the **split rate of planner-created children, month by month**,
before and after the split ledger reached the planner. Its population is the
ledger's own (`split_ledger.reasons`) rather than a second definition of "did
not fit one run", and a month nothing could be read for reports UNKNOWN.

And one more rule: a row nobody could read reports **UNKNOWN**,
never `0` and never "clean". A missing PR is the absence of evidence; scoring
it as agreement is the audit lying in its own favour.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planner_score.py -v
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import planner_score  # noqa: E402
import routing_verdict  # noqa: E402
import step_shell  # noqa: E402


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
            "split-rate": {
                "question": "how often did a child have to be split?",
                "scored": True,
                "values": ["one-card", "split"],
                "why": "the split ledger's own population — a turn-cap death, "
                       "a split or a hand-back",
                "ledger": "config/split-ledger.json",
                "ledger_injected_at": None,
                "ledger_injected_why": "DRE-3078 injects it; until then every "
                                       "month is before",
            },
            "proof-and-demo": {
                "question": "did the epic end with a proof card?",
                "scored": False,
                "values": ["present", "missing"],
                "enforced_by": "scripts/proof_and_demo.py",
                "contaminated": (
                    "plan.yml runs the gate and bounces the epic until the card "
                    "exists, so the planner was handed the answer face-up"
                ),
            },
        },
    }
    doc.update(overrides)
    return doc


def child(identifier, *, title=None, files=("scripts/a.py",), blocked_by=(),
          labels=(), comments=(), pr=("scripts/a.py",), verdict="FLEET",
          created_at="2026-08-15T09:00:00Z", state=None, successors=None):
    """A planner-created child, with the plan's claim in its body and the
    history that answers it hanging off it.

    `created_at` and `state` are what the split-rate row reads (DRE-3079): the
    month the card was created, and — for a Canceled or Backlog card — whether
    anything cites it as the card it was cut from."""
    body = f"Build the thing.\n\n**Files:** {', '.join(files)}\n"
    bodies = list(comments)
    if verdict:
        bodies.insert(0, routing_verdict.verdict_comment(verdict, "because"))
    record = {
        "identifier": identifier,
        "title": title if title is not None else f"{identifier} · a card",
        "body": body,
        "labels": list(labels),
        "blocked_by": list(blocked_by),
        "comments": bodies,
        "created_at": created_at,
        "pr": None if pr is None else {"number": 1, "merged": True,
                                       "files": list(pr)},
    }
    if state is not None:
        record["state"] = state
        record["state_type"] = {"Done": "completed", "Canceled": "canceled",
                                "Backlog": "backlog"}.get(state, "started")
    if successors is not None:
        record["successors"] = list(successors)
    return record


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
            [child("DRE-1"), child("DRE-2", title="PROOF: watch it run")],
            doc=doc,
            ledger=NO_SPLITS,
        )
        rows = [r for r in result["rows"] if r["dimension"] == "proof-and-demo"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "excluded")
        self.assertIn("contaminated", rows[0]["why"].lower())

    def test_the_row_reads_a_proof_card_alone_as_present(self):
        """INVERTED (DRE-3669): the row used to say `both-present` and needed a
        `DEMO:` child to say it. The CEO's decision of 2026-09-12 is one
        closing child, so a proof card on its own is the whole answer."""
        doc = reference()
        result = planner_score.score(
            epic(),
            [child("DRE-1"), child("DRE-2", title="PROOF: watch it run")],
            doc=doc,
            ledger=NO_SPLITS,
        )
        row = next(r for r in result["rows"] if r["dimension"] == "proof-and-demo")
        self.assertEqual(row["claimed"], "present")
        self.assertEqual(row["observed"], "present")
        self.assertNotIn("DEMO:", row["evidence"])

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
        honest = planner_score.score(epic(), children, doc=doc, ledger=NO_SPLITS)
        self.assertEqual(honest["counts"]["excluded"], 1)

        flattering = copy.deepcopy(doc)
        flattering["dimensions"]["proof-and-demo"]["scored"] = True
        flattering["dimensions"]["proof-and-demo"].pop("contaminated")
        cooked = planner_score.score(epic(), children, doc=flattering, ledger=NO_SPLITS)
        self.assertEqual(cooked["counts"]["excluded"], 0)
        self.assertEqual(
            cooked["counts"]["agree"] - honest["counts"]["agree"], 1,
            "the fixture proves nothing — the contaminated row did not agree",
        )

    def test_the_excluded_row_is_named_never_dropped(self):
        doc = reference()
        result = planner_score.score(epic(), [child("DRE-1")], doc=doc, ledger=NO_SPLITS)
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
            ledger=NO_SPLITS,
        )
        row = outcomes_by_card(result, "file-footprint")
        self.assertEqual(row["DRE-1"], "agree")

    def test_a_pr_touching_a_file_the_plan_never_named_disagrees(self):
        result = planner_score.score(
            epic(), [child("DRE-1", files=("a.py",), pr=("a.py", "surprise.py"))],
            doc=reference(),
            ledger=NO_SPLITS,
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
        result = planner_score.score(epic(), [naked], doc=reference(), ledger=NO_SPLITS)
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
            epic(), [child("DRE-1", pr=None)], doc=reference(),
            ledger=NO_SPLITS,
        )
        row = [r for r in result["rows"] if r["dimension"] == "file-footprint"][0]
        self.assertEqual(row["outcome"], "unknown")
        self.assertEqual(result["counts"]["agree"], 0)
        self.assertIn("could not", row["why"].lower())

    def test_a_pr_whose_file_list_would_not_load_is_unknown(self):
        blind = child("DRE-1")
        blind["pr"] = {"number": 7, "merged": True, "files": None}
        result = planner_score.score(epic(), [blind], doc=reference(), ledger=NO_SPLITS)
        row = [r for r in result["rows"] if r["dimension"] == "file-footprint"][0]
        self.assertEqual(row["outcome"], "unknown")

    def test_unknown_rows_are_left_out_of_the_number_and_still_printed(self):
        doc = reference()
        result = planner_score.score(epic(), [child("DRE-1", pr=None)], doc=doc, ledger=NO_SPLITS)
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
            ledger=NO_SPLITS,
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
            ledger=NO_SPLITS,
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
            ledger=NO_SPLITS,
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
            ledger=NO_SPLITS,
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
            ledger=NO_SPLITS,
        )
        rows = [r for r in result["rows"] if r["dimension"] == "collision"]
        self.assertEqual(rows[0]["outcome"], "unknown")


class SizeAndReadinessTest(unittest.TestCase):
    def test_a_card_that_died_at_the_turn_cap_disagrees_on_size(self):
        result = planner_score.score(
            epic(),
            [child("DRE-1", comments=(planner_score.TURN_CAP_RECEIPT_SAMPLE,))],
            doc=reference(),
            ledger=NO_SPLITS,
        )
        row = [r for r in result["rows"] if r["dimension"] == "size"][0]
        self.assertEqual(row["outcome"], "disagree")
        self.assertEqual(row["observed"], "too-big")

    def test_a_card_that_merged_without_a_turn_cap_agrees_on_size(self):
        result = planner_score.score(epic(), [child("DRE-1")], doc=reference(), ledger=NO_SPLITS)
        row = [r for r in result["rows"] if r["dimension"] == "size"][0]
        self.assertEqual(row["outcome"], "agree")

    def test_a_card_the_readiness_guard_returned_disagrees(self):
        result = planner_score.score(
            epic(),
            [child("DRE-1", comments=(
                planner_score.READINESS_BOUNCE_PREFIX
                + " repo: label. Returned to Planning;",))],
            doc=reference(),
            ledger=NO_SPLITS,
        )
        row = [r for r in result["rows"] if r["dimension"] == "readiness"][0]
        self.assertEqual(row["outcome"], "disagree")
        self.assertEqual(row["observed"], "bounced")

    def test_a_card_that_never_ran_is_unknown_on_size_and_readiness(self):
        result = planner_score.score(
            epic(), [child("DRE-1", pr=None)], doc=reference(),
            ledger=NO_SPLITS,
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
            ledger=NO_SPLITS,
        )
        row = [r for r in result["rows"] if r["dimension"] == "routing"][0]
        self.assertEqual(row["claimed"], "dispatchable")
        self.assertEqual(row["observed"], "needs-a-person")
        self.assertEqual(row["outcome"], "disagree")

    def test_a_fleet_card_that_shipped_agrees(self):
        result = planner_score.score(epic(), [child("DRE-1")], doc=reference(), ledger=NO_SPLITS)
        row = [r for r in result["rows"] if r["dimension"] == "routing"][0]
        self.assertEqual(row["outcome"], "agree")

    def test_a_card_carrying_no_routing_verdict_is_unclaimed(self):
        result = planner_score.score(
            epic(), [child("DRE-1", verdict=None)], doc=reference(),
            ledger=NO_SPLITS,
        )
        row = [r for r in result["rows"] if r["dimension"] == "routing"][0]
        self.assertEqual(row["outcome"], "unclaimed")

    def test_a_hand_built_verdict_claims_a_person_and_agrees_when_one_was_needed(self):
        result = planner_score.score(
            epic(),
            [child("DRE-1", verdict="WORKBENCH", comments=(
                planner_score.ESCALATION_RECEIPT_PREFIX + " which way?",))],
            doc=reference(),
            ledger=NO_SPLITS,
        )
        row = [r for r in result["rows"] if r["dimension"] == "routing"][0]
        self.assertEqual(row["claimed"], "needs-a-person")
        self.assertEqual(row["outcome"], "agree")

    def test_the_claimed_route_survives_the_operator_marker_flip(self):
        """DRE-6225: once OPERATOR marks `operator-step` instead of
        `hand-built`, a reader asking for the string would call it
        dispatchable. The route is read off who the verdict's actor is."""
        flipped = copy.deepcopy(routing_verdict.load())
        for record in flipped["verdicts"]:
            if record["name"] == "OPERATOR":
                record["marks"] = ["operator-step", "no-code"]
            if record["name"] == "WORKBENCH":
                record["marks"] = []
        for doc in (routing_verdict.load(), flipped):
            with unittest.mock.patch.object(routing_verdict, "load",
                                            lambda path=None, d=doc: d):
                self.assertEqual(planner_score.claimed_route("OPERATOR"), "needs-a-person")
                self.assertEqual(planner_score.claimed_route("WORKBENCH"), "needs-a-person")
                self.assertEqual(planner_score.claimed_route("FLEET"), "dispatchable")


class ApprovalTest(unittest.TestCase):
    def test_a_plan_the_critic_sent_back_disagrees(self):
        import plan_critic

        result = planner_score.score(
            epic(comments=(plan_critic.marker(
                "pre", 1, plan_critic.SEND_BACK, "two cards own one file"),)),
            [child("DRE-1")], doc=reference(),
            ledger=NO_SPLITS,
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
            ledger=NO_SPLITS,
        )
        row = [r for r in result["rows"] if r["dimension"] == "approval"][0]
        self.assertEqual(row["outcome"], "agree")

    def test_an_amended_epic_disagrees(self):
        result = planner_score.score(
            epic(comments=(f"🔁 {planner_score.AMENDMENT_TAG}: the plan no longer "
                           "describes the work",)),
            [child("DRE-1")], doc=reference(),
            ledger=NO_SPLITS,
        )
        row = [r for r in result["rows"] if r["dimension"] == "approval"][0]
        self.assertEqual(row["outcome"], "disagree")

    def test_a_plan_that_ran_untouched_agrees(self):
        result = planner_score.score(epic(), [child("DRE-1")], doc=reference(), ledger=NO_SPLITS)
        row = [r for r in result["rows"] if r["dimension"] == "approval"][0]
        self.assertEqual(row["outcome"], "agree")

    def test_an_epic_whose_children_never_shipped_is_unknown(self):
        result = planner_score.score(
            epic(), [child("DRE-1", pr=None)], doc=reference(),
            ledger=NO_SPLITS,
        )
        row = [r for r in result["rows"] if r["dimension"] == "approval"][0]
        self.assertEqual(row["outcome"], "unknown")


# --------------------------------------------------------------------------
# the split rate, month by month (DRE-3079)
# --------------------------------------------------------------------------
def ledger(*cards):
    """A split ledger carrying one row per named card, each of them a card the
    ledger says did not fit one run."""
    return {"rows": [{"card": c, "reasons": ["turn-cap-death", "split"],
                      "deaths": 2, "declared_files": "UNKNOWN",
                      "piece_files": "UNKNOWN", "tells": []} for c in cards],
            "rates": {"by_tell": []}}


#: The ledger every test passes when the split ledger is not what it is
#: testing (DRE-5314). `config/split-ledger.json` is regenerated every night,
#: and a row naming a fixture card would score it `split` — so a test that
#: read the live file asserted whatever last night's run wrote.
#: `TheLiveLedgerCannotChangeTheseScores` holds every test here to it.
NO_SPLITS = ledger()


HANDBACK = (planner_score.HANDBACK_RECEIPT_PREFIX
            + " six independently shippable pieces")


class SplitRateTest(unittest.TestCase):
    """DRE-3079. DRE-3022 asks to be measured by one number: how often does a
    planner-created child have to be split, month by month, before and after
    the ledger reached the planner. This is the reader that answers it — and
    the population it reads is the split ledger's own (`split_ledger.reasons`),
    never a second definition of "did not fit one run"."""

    def test_a_child_the_ledger_names_is_split(self):
        self.assertEqual(
            planner_score.split_outcome(child("DRE-1", state="Done"),
                                        planner_score.split_ledger_cards(ledger("DRE-1"))),
            "split")

    def test_a_child_that_handed_itself_back_is_split(self):
        """Not the same question as `size`: a hand-back leaves no turn-cap
        receipt, so the size dimension reads it as one PR's worth."""
        handed = child("DRE-1", state="Done", comments=(HANDBACK,))
        self.assertEqual(planner_score.split_outcome(handed, {}), "split")
        self.assertFalse(planner_score.died_at_the_turn_cap(handed["comments"]))

    def test_a_card_cancelled_with_pieces_citing_it_is_split(self):
        cut = child("DRE-1", state="Canceled", pr=None,
                    successors=[{"identifier": "DRE-2"}])
        self.assertEqual(planner_score.split_outcome(cut, {}), "split")

    def test_a_card_that_finished_with_no_split_signal_is_one_card(self):
        self.assertEqual(
            planner_score.split_outcome(child("DRE-1", state="Done"), {}),
            "one-card")

    def test_a_card_that_never_finished_is_pending_never_one_card(self):
        """The question was never put to it. Counting it as one-card would
        report a rate that improves every time the board grows."""
        self.assertEqual(
            planner_score.split_outcome(child("DRE-1", state="Todo", pr=None), {}),
            "pending")

    def test_a_card_whose_record_could_not_be_read_is_unknown(self):
        blind = child("DRE-1", state="Done")
        blind["comments"] = None
        self.assertEqual(planner_score.split_outcome(blind, {}), "unknown")

    # -- the row itself -------------------------------------------------------

    def test_the_rate_is_reported_for_the_month_the_cards_were_created_in(self):
        """The acceptance criterion: a month of known fixture data reports its
        row. Four cards created in August, one of them split."""
        result = planner_score.split_rate(
            [child("DRE-1", created_at="2026-08-03T09:00:00Z", state="Done"),
             child("DRE-2", created_at="2026-08-11T09:00:00Z", state="Done"),
             child("DRE-3", created_at="2026-08-20T09:00:00Z", state="Done"),
             child("DRE-4", created_at="2026-08-28T09:00:00Z", state="Done",
                   comments=(HANDBACK,))],
            ledger=ledger(), injected_at=None)
        row = {r["month"]: r for r in result["months"]}["2026-08"]
        self.assertEqual((row["cards"], row["split"], row["one_card"]), (4, 1, 3))
        self.assertEqual(row["rate"], 0.25)
        self.assertIn("1 of 4", row["sentence"])

    def test_each_month_gets_its_own_row(self):
        result = planner_score.split_rate(
            [child("DRE-1", created_at="2026-07-02T09:00:00Z", state="Done"),
             child("DRE-2", created_at="2026-08-02T09:00:00Z", state="Done",
                   comments=(HANDBACK,))],
            ledger=ledger(), injected_at=None)
        self.assertEqual([r["month"] for r in result["months"]],
                         ["2026-07", "2026-08"])
        self.assertEqual([r["rate"] for r in result["months"]], [0.0, 1.0])

    def test_a_month_with_nothing_answerable_reports_unknown_never_zero(self):
        """standards/console-honesty.md rule 2, and the ledger's own rule: an
        unread month is not a clean one."""
        result = planner_score.split_rate(
            [child("DRE-1", created_at="2026-08-02T09:00:00Z", state="Todo",
                   pr=None)],
            ledger=ledger(), injected_at=None)
        row = result["months"][0]
        self.assertEqual(row["rate"], planner_score.UNKNOWN)
        self.assertEqual(row["pending"], 1)

    def test_a_card_with_no_creation_date_is_named_not_bucketed(self):
        result = planner_score.split_rate(
            [child("DRE-1", created_at="", state="Done")],
            ledger=ledger(), injected_at=None)
        self.assertEqual(result["months"], [])
        self.assertTrue(any("DRE-1" in u for u in result["unreadable"]),
                        result["unreadable"])

    # -- before and after the ledger reached the planner ----------------------

    def test_the_months_are_split_before_and_after_the_injection(self):
        cards = [child("DRE-1", created_at="2026-08-02T09:00:00Z", state="Done",
                       comments=(HANDBACK,)),
                 child("DRE-2", created_at="2026-08-04T09:00:00Z", state="Done"),
                 child("DRE-3", created_at="2026-10-02T09:00:00Z", state="Done")]
        result = planner_score.split_rate(cards, ledger=ledger(),
                                          injected_at="2026-09-15T00:00:00Z")
        self.assertEqual({r["month"]: r["side"] for r in result["months"]},
                         {"2026-08": "before", "2026-10": "after"})
        self.assertEqual(result["before"]["split"], 1)
        self.assertEqual(result["after"]["split"], 0)

    def test_with_no_injection_date_the_after_half_is_unknown_never_zero(self):
        """DRE-3078 has not injected anything yet. "Nothing has happened after"
        and "nothing happened after" are different facts."""
        result = planner_score.split_rate(
            [child("DRE-1", created_at="2026-08-02T09:00:00Z", state="Done")],
            ledger=ledger(), injected_at=None)
        self.assertEqual(result["after"], planner_score.UNKNOWN)
        self.assertEqual([r["side"] for r in result["months"]], ["before"])
        self.assertTrue(any("inject" in u for u in result["unreadable"]),
                        result["unreadable"])

    # -- it is a dimension of the audit, and it is not the size dimension -----

    def test_the_scorer_emits_a_split_rate_row_for_every_child(self):
        result = planner_score.score(
            epic(), [child("DRE-1", state="Done"),
                     child("DRE-2", state="Done", comments=(HANDBACK,))],
            doc=reference(), ledger=NO_SPLITS)
        self.assertEqual(outcomes_by_card(result, "split-rate"),
                         {"DRE-1": "agree", "DRE-2": "disagree"})

    def test_a_hand_back_disagrees_on_split_rate_and_agrees_on_size(self):
        """The two dimensions are not one dimension written twice: `size` reads
        the turn-cap receipt, `split-rate` reads the ledger's whole
        population."""
        result = planner_score.score(
            epic(), [child("DRE-2", state="Done", comments=(HANDBACK,))],
            doc=reference(), ledger=NO_SPLITS)
        self.assertEqual(outcomes_by_card(result, "size"), {"DRE-2": "agree"})
        self.assertEqual(outcomes_by_card(result, "split-rate"), {"DRE-2": "disagree"})

    def test_the_shipped_reference_declares_the_dimension(self):
        self.assertIn("split-rate", planner_score.dimensions())
        self.assertEqual(planner_score.reference_problems(), [])

    def test_the_reference_names_the_ledger_and_when_it_was_injected(self):
        block = planner_score.dimensions()["split-rate"]
        self.assertEqual(block["ledger"], "config/split-ledger.json")
        self.assertTrue((ROOT / block["ledger"]).exists())
        self.assertIn("ledger_injected_at", block)

    def test_a_reference_that_names_no_ledger_is_refused(self):
        loose = reference()
        loose["dimensions"]["split-rate"]["ledger"] = "config/nope.json"
        problems = planner_score.reference_problems(loose)
        self.assertTrue(any("nope.json" in p for p in problems), problems)

    def test_an_injection_date_that_is_not_a_date_is_refused(self):
        loose = reference()
        loose["dimensions"]["split-rate"]["ledger_injected_at"] = "soon"
        problems = planner_score.reference_problems(loose)
        self.assertTrue(any("soon" in p for p in problems), problems)

    def test_the_unknown_literal_is_the_one_the_ledger_uses(self):
        import split_ledger

        self.assertEqual(planner_score.UNKNOWN, split_ledger.UNKNOWN)

    def test_the_population_is_the_ledgers_own_reader(self):
        """`split_ledger.reasons` decides what "did not fit one run" means —
        once, for the ledger and for this rate."""
        import split_ledger

        handed = child("DRE-1", state="Done", comments=(HANDBACK,))
        self.assertEqual(split_ledger.reasons(handed), ["handed-back"])

    def test_the_split_reasons_are_the_ledgers_own_and_read_at_call_time(self):
        """A copy of the three strings in this file would keep matching the OLD
        spellings after `split_ledger` renamed one, and shrink the split
        population with no error at all — the one number DRE-3022 is measured
        by, quietly wrong."""
        import split_ledger
        from unittest import mock

        self.assertEqual(planner_score._split_reasons(),
                         split_ledger.DEATH_REASONS)

        renamed = "turn-cap-death-v2"
        doc = {"rows": [{"card": "DRE-1", "reasons": [renamed]}]}
        self.assertEqual(planner_score.split_ledger_cards(doc), {})
        with mock.patch.object(split_ledger, "DEATH_REASONS", (renamed,)):
            self.assertEqual(list(planner_score.split_ledger_cards(doc)),
                             ["DRE-1"])

    # -- the report -----------------------------------------------------------

    def test_the_report_prints_the_month_row_and_both_sides(self):
        result = planner_score.split_rate(
            [child("DRE-1", created_at="2026-08-02T09:00:00Z", state="Done",
                   comments=(HANDBACK,))],
            ledger=ledger(), injected_at=None)
        report = planner_score.render_split_rate(result)
        self.assertIn("2026-08", report)
        self.assertIn("1 of 1", report)
        self.assertIn("UNKNOWN", report)

    def test_the_cli_reports_a_month_from_children_on_stdin(self):
        import subprocess
        import tempfile

        payload = json.dumps({"children": [
            child("DRE-1", created_at="2026-08-02T09:00:00Z", state="Done",
                  comments=(HANDBACK,)),
            child("DRE-2", created_at="2026-08-03T09:00:00Z", state="Done"),
        ]})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "split-ledger.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(NO_SPLITS, fh)
            out = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "planner_score.py"),
                 "split-rate", "--ledger", path],
                input=payload, capture_output=True, text=True, check=True,
            ).stdout
        self.assertIn("2026-08", out)
        self.assertIn("1 of 2", out)

    def test_the_cli_reads_the_ledger_it_is_given(self):
        """`--ledger` is read, not merely accepted: a row naming DRE-2 makes
        it the month's second split."""
        import subprocess
        import tempfile

        payload = json.dumps({"children": [
            child("DRE-1", created_at="2026-08-02T09:00:00Z", state="Done",
                  comments=(HANDBACK,)),
            child("DRE-2", created_at="2026-08-03T09:00:00Z", state="Done"),
        ]})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "split-ledger.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(ledger("DRE-2"), fh)
            out = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "planner_score.py"),
                 "split-rate", "--ledger", path],
                input=payload, capture_output=True, text=True, check=True,
            ).stdout
        self.assertIn("2 of 2", out)

    def test_the_scorer_reads_the_ledger_it_is_given(self):
        result = planner_score.score(epic(), [child("DRE-1", state="Done")],
                                     doc=reference(), ledger=ledger("DRE-1"))
        self.assertEqual(outcomes_by_card(result, "split-rate"),
                         {"DRE-1": "disagree"})


#: The calls that read the split ledger when no ledger is passed. A test whose
#: source makes one of them is a test whose expectations the ledger can change.
LEDGER_READERS = ("planner_score.score(", "split_rate(", "split_ledger_cards(")


def _runs_the_cli(source: str) -> bool:
    """A test that runs a `planner_score.py` command that reads the ledger, in
    a subprocess — out of reach of any patch this process makes."""
    return ("sys.executable" in source and "planner_score.py" in source
            and any(f'"{command}"' in source for command in ("split-rate", "score")))


class TheLiveLedgerCannotChangeTheseScores(unittest.TestCase):
    """DRE-5314. `config/split-ledger.json` is regenerated every night, and a
    row naming a card makes `split-rate` score that card `split`. So a test
    that scored against the live file asserted whatever last night's run
    wrote. This class writes a row for every card this module's fixtures name
    into a temporary ledger, points the default read at it, and runs every
    test that scores again."""

    def setUp(self):
        import tempfile
        from unittest import mock

        import split_ledger

        with open(__file__, encoding="utf-8") as fh:
            cards = sorted(set(re.findall(r"DRE-\d+", fh.read())))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "split-ledger.json")
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(ledger(*cards), fh)
        self.default_reads = []
        real_load = split_ledger.load

        def load(path=None):
            if path is None:
                self.default_reads.append(path)
            return real_load(path)

        for patch in (mock.patch.object(split_ledger, "LEDGER_PATH", self.path),
                      mock.patch.object(split_ledger, "load", load)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_regenerated_rows_reach_a_caller_that_passes_no_ledger(self):
        """The premise, so the test below cannot pass vacuously."""
        result = planner_score.score(epic(), [child("DRE-1", state="Done")],
                                     doc=reference())
        self.assertEqual(outcomes_by_card(result, "split-rate"),
                         {"DRE-1": "disagree"})
        self.assertTrue(self.default_reads)

    def test_no_test_in_this_module_scores_against_the_live_ledger(self):
        """Discovered off each test's source, never listed. The CLI test runs
        in a subprocess, which this patch cannot reach — it has to name its
        ledger on the command line, and the discovery holds it to that."""
        import inspect

        loader = unittest.TestLoader()
        tests = []
        for case in list(globals().values()):
            if not (isinstance(case, type) and issubclass(case, unittest.TestCase)
                    and case is not type(self)):
                continue
            for name in loader.getTestCaseNames(case):
                source = inspect.getsource(getattr(case, name))
                if _runs_the_cli(source):
                    self.assertIn('"--ledger"', source,
                                  f"{case.__name__}.{name} runs the CLI "
                                  "against the live ledger")
                if any(call in source for call in LEDGER_READERS):
                    tests.append(case(name))
        self.assertGreater(len(tests), 30, "the discovery found nothing to run")
        result = unittest.TestResult()
        unittest.TestSuite(tests).run(result)
        self.assertEqual(
            [f"{test.id()}: " + next(
                (line for line in trace.splitlines() if "Error:" in line),
                trace.splitlines()[-1])
             for test, trace in result.failures + result.errors], [])
        self.assertEqual(self.default_reads, [],
                         "a test in this module still scores against the live ledger")


# --------------------------------------------------------------------------
# the report — both halves, empty or not
# --------------------------------------------------------------------------
class ReportTest(unittest.TestCase):
    def test_both_halves_are_printed_even_when_one_is_empty(self):
        doc = reference()
        result = planner_score.score(epic(), [child("DRE-1")], doc=doc, ledger=NO_SPLITS)
        report = planner_score.render_report(result, doc=doc)
        self.assertEqual(result["counts"]["disagree"], 0)
        for heading in ("## Agreement", "## Disagreement"):
            self.assertIn(heading, report)
        self.assertIn("*(none)*", report)

    def test_the_report_names_the_epic_it_scored(self):
        doc = reference()
        result = planner_score.score(epic("DRE-1234"), [child("DRE-1")], doc=doc, ledger=NO_SPLITS)
        self.assertIn("DRE-1234", planner_score.render_report(result, doc=doc))

    def test_a_disagreement_is_never_printed_under_agreement(self):
        doc = reference()
        result = planner_score.score(
            epic(), [child("DRE-9", files=("a.py",), pr=("a.py", "b.py"))], doc=doc,
            ledger=NO_SPLITS,
        )
        report = planner_score.render_report(result, doc=doc)
        agreement, _, rest = report.partition("## Disagreement")
        self.assertIn("| DRE-9 | `file-footprint` |", rest)
        self.assertNotIn("`file-footprint`", agreement)


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
                                     doc=reference(), ledger=NO_SPLITS)
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
                                     doc=reference(), ledger=NO_SPLITS)
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
        source = step_shell.workflow_source(
            ROOT / ".github" / "workflows" / "agent-task.yml")
        self.assertIn(planner_score.ESCALATION_RECEIPT_PREFIX, source)

    def test_the_handback_prefix_is_the_workflows_own_words(self):
        source = step_shell.workflow_source(
            ROOT / ".github" / "workflows" / "agent-task.yml")
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
# the replay harness is retired (DRE-6051)
# --------------------------------------------------------------------------
class TheReplayIsRetiredTest(unittest.TestCase):
    """DRE-6051, the CEO's decision of 2026-10-06. The on-demand replay job
    never ran once, so it is gone from the code rather than left startable:
    both of its workflows, and the half of this module only they used. The
    scorer and the split rate stay. Each absence is proved by an exact set or
    a word, so nothing here spells the retired names."""

    #: The commands the scorer keeps — the reference check, the two readers
    #: and the two reports the split ledger and a hand run use.
    COMMANDS = {"check", "collect", "score", "collect-month", "split-rate"}

    def test_the_cli_offers_exactly_the_scoring_commands(self):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            planner_score.main(["--help"])
        offered = re.search(r"\{([^}]*)\}", out.getvalue())
        self.assertIsNotNone(offered, out.getvalue())
        self.assertEqual(set(offered.group(1).split(",")), self.COMMANDS)

    def test_score_takes_no_replay_payload(self):
        import inspect

        self.assertNotIn("replay",
                         inspect.signature(planner_score.score).parameters)
        result = planner_score.score(epic(), [child("DRE-1")], doc=reference(),
                                     ledger=NO_SPLITS)
        self.assertNotIn("replay", result)

    def test_nothing_in_the_module_is_named_for_the_replay(self):
        retired = ("replay", "leak", "shape", "historical", "pre_plan",
                   "diff", "normalise")
        named = sorted(name for name in vars(planner_score)
                       if any(word in name.lower() for word in retired))
        self.assertEqual(named, [])

    def test_the_module_no_longer_mentions_a_replay(self):
        source = (ROOT / "scripts" / "planner_score.py").read_text(
            encoding="utf-8")
        mentions = [line.strip() for line in source.splitlines()
                    if "replay" in line.lower()]
        self.assertEqual(mentions, [])

    def test_only_the_split_ledger_workflow_runs_the_scorer(self):
        workflows = ROOT / ".github" / "workflows"
        callers = sorted(
            path.name for path in workflows.glob("*.yml")
            if "planner_score" in step_shell.workflow_source(path))
        self.assertEqual(callers, ["split-ledger.yml"])


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

    def test_the_document_no_longer_describes_a_replay_harness(self):
        """DRE-6051 retired the harness, so the page stops telling a reader
        how to run it."""
        mentions = [line for line in self.doc.splitlines()
                    if "replay harness" in line.lower()]
        self.assertEqual(mentions, [])


if __name__ == "__main__":                      # pragma: no cover
    unittest.main()
