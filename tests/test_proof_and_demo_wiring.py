"""The proof/demo pair, wired into the plan run (DRE-2746).

`scripts/proof_and_demo.py` is worth nothing unless a real planner is TOLD to
emit the two cards and the run REFUSES an epic that has not. The card's own
reason for existing is that a brief line is not enough: "briefs are guidance a
model can skip, and this wave's own finding is that a convention nothing checks
is a convention that drifts."

So these tests pin both halves, and the second one is the load-bearing one:

  1. The BRIEF and the STANDARD say what the planner owes — the two cards, last,
     blocked by every sibling, neither fleet-buildable.
  2. The RUN checks the PLANNER'S OUTPUT — the cards it created, read out of
     Linear — and an epic missing either card is bounced back to Planning with
     the reason named. The check does not read the brief.
  3. The read seam exists: `linear_ops.py children-detail` hands the gate the
     titles, labels and formal `blocks` relations it needs. Ordering is by
     creation, not by whatever Linear returns first.
"""

import os
import re
import sys
import unittest

import yaml

REPO = os.path.join(os.path.dirname(__file__), "..")
WF = os.path.join(REPO, ".github", "workflows", "plan.yml")
PLANNER_BRIEF = os.path.join(REPO, "briefs", "planner.md")
STANDARD = os.path.join(REPO, "standards", "card-quality.md")
MODULE = os.path.join(REPO, "scripts", "proof_and_demo.py")
SCRIPTS = os.path.join(REPO, "scripts")
sys.path.insert(0, SCRIPTS)

os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import assemble_context as ac  # noqa: E402
import linear_ops  # noqa: E402

GATE = "Proof and demo cards"


def wf_src() -> str:
    return open(WF).read()


def wf_steps() -> list:
    doc = yaml.safe_load(wf_src())
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def step_named(fragment: str) -> dict:
    for step in wf_steps():
        if fragment.lower() in (step.get("name") or "").lower():
            return step
    raise AssertionError(
        f"no plan.yml step whose name contains {fragment!r}; have: "
        + ", ".join(repr(s.get("name")) for s in wf_steps())
    )


class BriefAndStandardTest(unittest.TestCase):
    """1 — the planner is told, in the two documents it is handed."""

    def test_the_planner_brief_names_the_two_cards(self):
        text = open(PLANNER_BRIEF).read()
        self.assertIn("PROOF:", text)
        self.assertIn("DEMO:", text)

    def test_the_brief_names_the_three_conditions(self):
        text = open(PLANNER_BRIEF).read().lower()
        self.assertIn("last two", text)
        self.assertIn("blocked by every", text)
        # `never `FLEET`` — markdown emphasis around the verdict is allowed.
        self.assertRegex(text, r"(never|not|no)\W{0,3}fleet")

    def test_the_card_standard_carries_the_convention(self):
        text = open(STANDARD).read()
        self.assertIn("PROOF:", text)
        self.assertIn("DEMO:", text)

    def test_the_standard_rides_the_context_rail_for_the_planner(self):
        """card-quality.md is already assembled for the planner and both plan
        critics — the convention lands in every context that reads a plan."""
        for role in ("planner", "plan-critic-pre", "plan-critic-post"):
            self.assertIn("card-quality.md", ac.ROLE_STANDARDS[role], role)


class TheRunChecksThePlannersOutputTest(unittest.TestCase):
    """2 — the gate, and what it reads."""

    def test_the_gate_step_exists(self):
        step_named(GATE)

    def test_it_reads_the_cards_the_planner_created(self):
        run = step_named(GATE)["run"]
        self.assertIn("children-detail", run)
        self.assertIn("proof_and_demo.py check", run)

    def test_it_does_not_read_the_brief(self):
        """The check runs on the planner's output, not on the brief's text."""
        run = step_named(GATE)["run"]
        self.assertNotIn("briefs/", run)
        self.assertNotIn("briefs/", open(MODULE).read())

    def test_it_runs_after_the_children_exist_and_only_when_there_are_some(self):
        """A planner that escalated created no cards (DRE-2848) and must not be
        failed for the absence of two."""
        gate = step_named(GATE).get("if", "")
        self.assertIn("steps.kids.outputs.count", gate)
        self.assertIn("!= '0'", gate)
        self.assertIn("steps.route.outputs.mode == 'plan'", gate)

        names = [s.get("name") or "" for s in wf_steps()]
        self.assertLess(
            names.index(next(n for n in names if "Validate created children" in n)),
            names.index(next(n for n in names if GATE in n)),
            "the proof/demo gate must run after the children are validated",
        )

    def test_it_runs_before_the_plan_reaches_the_ceo(self):
        names = [s.get("name") or "" for s in wf_steps()]
        self.assertLess(
            names.index(next(n for n in names if GATE in n)),
            names.index(next(n for n in names if "Epic → Green Light" in n)),
            "an epic missing the two cards must not reach the CEO",
        )

    def test_the_bounce_names_the_reason_and_returns_the_card_to_planning(self):
        run = step_named(GATE)["run"]
        self.assertIn("linear_ops.py comment", run)
        self.assertRegex(run, r'linear_ops\.py state "\$EPIC" "Planning"')
        self.assertIn("exit 1", run)

    def test_a_crash_posts_no_reason(self):
        """console-honesty rule 1: a check that produced no finding decided
        nothing, and must never be the reason a plan is bounced."""
        run = step_named(GATE)["run"]
        self.assertRegex(run, r'\[ -s "\$[A-Z_]*(COMMENT|BOUNCE)[A-Z_]*" \]')

    def test_the_gate_is_not_silently_optional(self):
        step = step_named(GATE)
        self.assertNotIn("continue-on-error", step)

    def test_a_revised_plan_is_checked_again(self):
        """A revision is the planner's output too. The re-check step already
        re-runs the child validation and the artifact check for exactly this
        reason — a re-plan that adds a card after the pair, or drops one of
        them, must not reach the CEO because the first pass was clean."""
        run = step_named("Re-check the revised plan")["run"]
        self.assertIn("children-detail", run)
        self.assertIn("proof_and_demo.py check", run)
        self.assertRegex(run, r'linear_ops\.py state "\$EPIC" "Planning"')


class TheReadSeamTest(unittest.TestCase):
    """3 — `children-detail`, the one query that answers the gate's question."""

    def test_the_command_is_registered(self):
        src = open(os.path.join(SCRIPTS, "linear_ops.py")).read()
        self.assertIn('"children-detail": cmd_children_detail', src)

    def test_it_asks_for_everything_the_gate_reads(self):
        src = open(os.path.join(SCRIPTS, "linear_ops.py")).read()
        body = re.search(
            r"^def cmd_children_detail\(.*?(?=^def )", src, re.S | re.M)
        self.assertIsNotNone(body, "cmd_children_detail must exist")
        query = body.group(0)
        for field in ("identifier", "title", "description", "createdAt",
                      "labels", "inverseRelations"):
            self.assertIn(field, query, field)

    def test_it_orders_by_creation_not_by_whatever_linear_returns(self):
        """`the last two children` is only meaningful against a stable order,
        and the planner creates the pair last."""
        nodes = [
            {"identifier": "DRE-2", "title": "b", "description": "",
             "createdAt": "2026-08-31T10:00:00.000Z",
             "labels": {"nodes": []}, "inverseRelations": {"nodes": []}},
            {"identifier": "DRE-1", "title": "a", "description": "",
             "createdAt": "2026-08-31T09:00:00.000Z",
             "labels": {"nodes": []}, "inverseRelations": {"nodes": []}},
        ]
        records = linear_ops.child_detail_records(nodes)
        self.assertEqual([r["identifier"] for r in records], ["DRE-1", "DRE-2"])

    def test_it_reports_only_formal_blocks_relations(self):
        node = {
            "identifier": "DRE-9", "title": "PROOF: x", "description": "",
            "createdAt": "2026-08-31T09:00:00.000Z",
            "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
            "inverseRelations": {"nodes": [
                {"type": "blocks", "issue": {"identifier": "DRE-1"}},
                {"type": "related", "issue": {"identifier": "DRE-2"}},
            ]},
        }
        record = linear_ops.child_detail_records([node])[0]
        self.assertEqual(record["blocked_by"], ["DRE-1"])
        self.assertEqual(record["labels"], ["repo:bureau-pipeline"])
        self.assertEqual(record["title"], "PROOF: x")


class ThePlannerIsToldInTheRunTest(unittest.TestCase):
    """The prompt the planner actually receives — the brief is guidance a model
    can skip, so the run's own numbered process names the two cards too."""

    def test_the_plan_prompt_names_the_two_cards(self):
        prompt = step_named("Plan epic")["with"]["prompt"]
        self.assertIn("PROOF:", prompt)
        self.assertIn("DEMO:", prompt)
        self.assertIn("proof_and_demo.py check", prompt)
