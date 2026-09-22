"""RED-first: an approved epic's children carry their routing verdicts (DRE-4593).

An epic the CEO approves has its children created by the PLANNER, which runs
`linear_ops.py subissue`. Nothing then wrote each child's routing verdict. The
only automatic verdict writer was `proof_and_demo.py check --stamp`, and it
stamps the PROOF card alone — so every BUILD child reached Backlog carrying no
verdict at all, and the sweep's promotion gate refused each one:

    🚨 routing-no-verdict: <CARD> carries no routing verdict, so nothing has
    said who builds it

Measured: epic DRE-4425 (approved 2026-09-20 22:28 PT) had five cards frozen
for about 37 hours. Epic DRE-4467, the round robin (approved 2026-09-21
20:03 PT), had all seven build cards frozen until a person stamped them by hand
on 2026-09-22 — four at 12:11 PT by an assistant session, three at 20:47 PT by
the CEO running the script, because the assistant may not write them.
DRE-3270 and DRE-3301 show the same refusal.

WHAT THIS PINS, one section per acceptance criterion:

  1. A build child gets its verdict, computed by the vocabulary's OWN
     mechanical precedence — no new classifier. The one-off planning exit and
     this batch read the SAME function, so they cannot disagree about a card.
  2. The PROOF child is never stamped FLEET by this step. It is `proof_and_demo`'s
     card and this step leaves it alone, whichever of the two runs first — so
     the ordering is safe from both ends, not only from the one we chose.
  3. A child that already carries a verdict is not re-stamped, and its siblings
     in the same run still are.
  4. The undecidable case is not a silent FLEET. A judgement call takes the
     one-off route's own answer and says which criteria decided it; a card the
     vocabulary sends back to Planning (NEEDS WORK) is stamped with nothing,
     exactly as `planning_route.exit_plan` stamps nothing there.
  5. The replay of DRE-4467's eight children: seven FLEET, and DRE-4475 left to
     the proof check.
  6. ONE write path. Everything goes through `routing_verdict.stamp_card`.
  7. It is wired into `plan.yml` at every place a plan's children are finished.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planner_stamps_children.py -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import plan_child_verdicts  # noqa: E402
import planning_route  # noqa: E402
import proof_and_demo  # noqa: E402
import routing_verdict  # noqa: E402

WF = ROOT / ".github" / "workflows" / "plan.yml"
FIXTURE = ROOT / "tests" / "fixtures" / "round-robin-children-2026-09-21.json"
MODULE = SCRIPTS / "plan_child_verdicts.py"

EPIC = "DRE-4593"

LABELS = ("repo:bureau-pipeline", "agent:engineer", "initiative:pipeline")
OPS_LABELS = ("repo:bureau-pipeline", "agent:ops", "initiative:pipeline")
NO_ROLE_LABELS = ("repo:bureau-pipeline", "initiative:pipeline")

# A build card the way a planner writes one: checkbox criteria naming neither
# an interactive flow nor a rendered outcome. That is the JUDGEMENT branch, and
# it is not a corner case — it is how all seven of DRE-4467's build cards read.
WORK_BODY = (
    "Add the batch stamper.\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] every build child leaves the plan run carrying one verdict\n"
    "- [ ] the sweep promotes the first unblocked child with no hand stamp\n"
)

# Criteria that name an interactive flow — precedence 3 routes this WORKBENCH,
# and the batch must honour that rather than sending the fleet at it.
LIVE_BODY = (
    "Turn it on.\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] the mode is switched in production and the first moves are read\n"
)

PROOF_BODY = (
    "Record what was observed, where it was read, and when.\n\n"
    f"{proof_and_demo.CLOSING_LINE}\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] the stamper is observed on a real epic in production\n"
    "- [ ] the observation is written to docs/proof/DRE-4593.md and merged\n"
    "- [ ] the CEO closes this card after reading the record\n"
)

# No checkbox criteria at all — the vocabulary's NEEDS WORK, which routes back
# to Planning rather than to anybody who builds.
NO_CRITERIA_BODY = "Make the thing better.\n\nWe will know it when we see it.\n"


def _card(identifier, title, body=WORK_BODY, labels=LABELS, blocked_by=()):
    return {
        "identifier": identifier,
        "title": title,
        "body": body,
        "labels": list(labels),
        "blocked_by": list(blocked_by),
    }


def _plan(work_bodies=(WORK_BODY, WORK_BODY), proof=True, demo=False):
    """One planner output: the work cards, then the card that closes the epic."""
    cards = [
        _card(f"DRE-90{i}", f"the {i}th piece", body=body)
        for i, body in enumerate(work_bodies, 1)
    ]
    siblings = [c["identifier"] for c in cards]
    if demo:
        cards.append(_card("DRE-9098", "DEMO: walk the CEO through it",
                           labels=OPS_LABELS, blocked_by=siblings))
    if proof:
        cards.append(_card("DRE-9099", "PROOF: the sweep promoted it with no "
                                       "hand stamp",
                           body=PROOF_BODY, labels=OPS_LABELS,
                           blocked_by=siblings + (["DRE-9098"] if demo else [])))
    return cards


def _written(children, existing=None):
    """Drive the write seam against a fake Linear. `existing` maps an
    identifier to the comment bodies already on that card."""
    existing = existing or {}
    posted: list[tuple[str, str]] = []
    labelled: list[tuple[str, str]] = []
    with patch.object(linear_ops, "comment_bodies",
                      side_effect=lambda i: list(existing.get(i, []))), \
            patch.object(linear_ops, "cmd_comment",
                         side_effect=lambda i, b: posted.append((i, b))), \
            patch.object(linear_ops, "add_label",
                         side_effect=lambda i, l: labelled.append((i, l))):
        count = plan_child_verdicts.write_verdicts(children)
    return count, posted, labelled


def _stamped(children):
    """`{identifier: verdict}` for the children this step would write."""
    return {r.identifier: r.verdict
            for r in plan_child_verdicts.verdicts_for(children) if r.write}


# ===========================================================================
# 1 — a build child gets its verdict, off the vocabulary's own precedence
# ===========================================================================
class ABuildChildGetsItsVerdictTest(unittest.TestCase):

    def test_every_build_child_is_given_one(self):
        """The whole defect: these cards used to leave the plan run with
        nothing on them, and the sweep refused every one."""
        self.assertEqual(_stamped(_plan()),
                         {"DRE-901": "FLEET", "DRE-902": "FLEET"})

    def test_the_verdict_is_the_card_s_own_routing_decision(self):
        """Not a constant. Criteria that name a live flow route WORKBENCH, and
        the batch honours that — FLEET is what dispatches an agent."""
        self.assertEqual(_stamped(_plan(work_bodies=(WORK_BODY, LIVE_BODY))),
                         {"DRE-901": "FLEET", "DRE-902": "WORKBENCH"})

    def test_a_role_label_still_wins_first(self):
        """Precedence 1, unchanged: an explicit role label decides before any
        criteria are read."""
        plan = _plan()
        plan[0]["labels"] = list(OPS_LABELS)
        self.assertEqual(_stamped(plan)["DRE-901"], "OPERATOR")

    def test_it_is_the_same_classifier_the_one_off_exit_uses(self):
        """No second classifier (the card says so in as many words). The
        planning exit for a ONE-OFF and this batch read one function, so a card
        cannot be routed two ways depending on which door it came through."""
        for body in (WORK_BODY, LIVE_BODY, PROOF_BODY):
            card = {"identifier": "DRE-1", "title": "a card",
                    "description": body, "labels": list(NO_ROLE_LABELS)}
            one_off, _ = planning_route._one_off_check(card, [], "one-off")
            batch = plan_child_verdicts.verdicts_for(
                [_card("DRE-1", "a card", body=body, labels=NO_ROLE_LABELS)]
            )[0]
            self.assertEqual(batch.verdict, one_off, body[:40])

    def test_every_verdict_says_why(self):
        """`verdict_comment` refuses a verdict with no reason."""
        for record in plan_child_verdicts.verdicts_for(_plan()):
            self.assertTrue(record.why.strip(), record.identifier)
            if record.write:
                routing_verdict.verdict_comment(record.verdict, record.why)

    def test_the_comment_is_the_one_every_other_verdict_uses(self):
        """`promotion_refusal` reads it, so it has to BE that comment."""
        _, posted, _ = _written(_plan())
        self.assertEqual([i for i, _ in posted], ["DRE-901", "DRE-902"])
        for identifier, body in posted:
            self.assertEqual(routing_verdict.verdict_on([body]), "FLEET",
                             identifier)

    def test_it_applies_the_marks_the_verdict_declares(self):
        """FLEET declares none; WORKBENCH declares `hand-built`, and the sweep
        already reads that to keep a competing run off the card."""
        _, _, labelled = _written(_plan(work_bodies=(WORK_BODY, LIVE_BODY)))
        self.assertEqual(labelled, [("DRE-902", "hand-built")])


# ===========================================================================
# 2 — the PROOF child is never this step's to stamp
# ===========================================================================
class TheProofChildIsLeftToTheProofCheckTest(unittest.TestCase):

    def test_the_proof_child_is_not_stamped_here(self):
        self.assertNotIn("DRE-9099", _stamped(_plan()))

    def test_a_legacy_demo_child_is_not_either(self):
        """`proof_and_demo` deliberately declines to stamp a demo card whose
        verdict is not one a human acts on — writing FLEET there would send the
        fleet at it. This step must not undo that decision from the side."""
        self.assertNotIn("DRE-9098", _stamped(_plan(demo=True)))

    def test_it_would_refuse_a_proof_card_even_where_the_criteria_say_fleet(self):
        """The ordering is safe from BOTH ends. Strip the proof card's role
        label and its criteria route it exactly as a build card's do — a
        judgement call, whose answer is FLEET. This step still writes nothing on
        it, so running before the proof check could not dispatch an agent at the
        card that is supposed to confirm the fleet's own work."""
        plan = _plan()
        plan[-1]["labels"] = list(NO_ROLE_LABELS)
        record = next(r for r in plan_child_verdicts.verdicts_for(plan)
                      if r.identifier == "DRE-9099")
        self.assertFalse(record.write)
        self.assertIsNone(record.verdict)
        self.assertIn("proof_and_demo", record.why)

    def test_no_verdict_it_writes_is_ever_fleet_on_a_closing_card(self):
        for plan in (_plan(), _plan(demo=True)):
            for record in plan_child_verdicts.verdicts_for(plan):
                if proof_and_demo.is_proof(
                        next(c["title"] for c in plan
                             if c["identifier"] == record.identifier)):
                    self.assertNotEqual(record.verdict, "FLEET")

    def test_the_proof_card_keeps_the_proof_check_s_verdict(self):
        """Run both, in the order the plan run runs them, against one fake
        board: `proof_and_demo` stamps OPERATOR on the proof card, and this step
        then adds the two build cards and leaves the proof card alone."""
        plan = _plan()
        posted: list[tuple[str, str]] = []
        seen: dict[str, list[str]] = {}
        with patch.object(linear_ops, "comment_bodies",
                          side_effect=lambda i: list(seen.get(i, []))), \
                patch.object(linear_ops, "cmd_comment",
                             side_effect=lambda i, b: (
                                 posted.append((i, b)),
                                 seen.setdefault(i, []).append(b))), \
                patch.object(linear_ops, "add_label"):
            proof_and_demo.write_stamps(plan)
            plan_child_verdicts.write_verdicts(plan)
        self.assertEqual([i for i, _ in posted],
                         ["DRE-9099", "DRE-901", "DRE-902"])
        by_card = {i: routing_verdict.verdict_on([b]) for i, b in posted}
        self.assertEqual(by_card,
                         {"DRE-9099": "OPERATOR", "DRE-901": "FLEET",
                          "DRE-902": "FLEET"})


# ===========================================================================
# 3 — a child that already carries a verdict is untouched
# ===========================================================================
class AnAlreadyStampedChildIsUntouchedTest(unittest.TestCase):

    def test_it_is_not_re_stamped_and_its_siblings_still_are(self):
        already = routing_verdict.verdict_comment(
            "WORKBENCH", "a person drives the flow")
        count, posted, labelled = _written(
            _plan(), existing={"DRE-901": [already]})
        self.assertEqual(count, 1)
        self.assertEqual([i for i, _ in posted], ["DRE-902"])
        self.assertEqual([i for i, _ in labelled], [])

    def test_a_plan_already_stamped_all_through_writes_nothing(self):
        already = routing_verdict.verdict_comment("FLEET", "an agent builds it")
        count, posted, labelled = _written(
            _plan(), existing={"DRE-901": [already], "DRE-902": [already]})
        self.assertEqual((count, posted, labelled), (0, [], []))

    def test_the_step_is_safe_to_run_twice(self):
        """A re-planned epic runs the plan gates again, and the activate route
        runs this once more before promotion — the second pass must add nothing.
        """
        plan = _plan()
        seen: dict[str, list[str]] = {}
        posted: list[tuple[str, str]] = []
        with patch.object(linear_ops, "comment_bodies",
                          side_effect=lambda i: list(seen.get(i, []))), \
                patch.object(linear_ops, "cmd_comment",
                             side_effect=lambda i, b: (
                                 posted.append((i, b)),
                                 seen.setdefault(i, []).append(b))), \
                patch.object(linear_ops, "add_label"):
            first = plan_child_verdicts.write_verdicts(plan)
            second = plan_child_verdicts.write_verdicts(plan)
        self.assertEqual((first, second), (2, 0))
        self.assertEqual(len(posted), 2)


# ===========================================================================
# 4 — what happens where the vocabulary cannot decide mechanically
# ===========================================================================
class TheUndecidableCaseTest(unittest.TestCase):

    def test_a_judgement_call_takes_the_one_off_route_s_answer(self):
        """`routing_verdict.route()` returns no verdict when the criteria name
        neither signal. That is not a new question: `planning_route` already
        answers it for a one-off — a card that is one card and one pull request,
        which is exactly what the planner is told to decompose into — and the
        answer is the promotable verdict, with the criteria that were read
        NAMED. Six of DRE-4467's seven build cards were this case."""
        record = plan_child_verdicts.verdicts_for(_plan())[0]
        self.assertEqual(record.verdict, planning_route.fleet_verdict())
        self.assertIn("acceptance criteria", record.why)
        for criterion in routing_verdict.acceptance_criteria(WORK_BODY):
            self.assertIn(criterion, record.why)

    def test_needs_work_is_stamped_with_nothing(self):
        """The vocabulary sends a card with no stated exit condition back to
        Planning, and `planning_route.exit_plan` stamps nothing on that card —
        it escalates instead. The batch does the same: it writes no verdict and
        says why, and the sweep's own `routing-no-verdict` refusal holds the
        card where a person will read it. Writing FLEET here would dispatch an
        agent at a card nobody can tell is finished."""
        plan = _plan(work_bodies=(NO_CRITERIA_BODY, WORK_BODY))
        record = plan_child_verdicts.verdicts_for(plan)[0]
        self.assertFalse(record.write)
        self.assertIsNone(record.verdict)
        self.assertIn("NEEDS WORK", record.why)
        self.assertEqual(_stamped(plan), {"DRE-902": "FLEET"})

    def test_a_needs_work_child_does_not_fail_the_step(self):
        """`validate_card.py check-children` runs first and fails the plan for a
        child with no criteria; if one reaches here anyway, this step reports it
        and lets the rest of the plan's children through."""
        plan = _plan(work_bodies=(NO_CRITERIA_BODY, WORK_BODY))
        with patch.object(sys, "stdin", io.StringIO(json.dumps(plan))), \
                patch.object(linear_ops, "comment_bodies", return_value=[]), \
                patch.object(linear_ops, "cmd_comment"), \
                patch.object(linear_ops, "add_label"):
            self.assertEqual(
                plan_child_verdicts.main(["stamp", "--epic", EPIC]), 0)


# ===========================================================================
# 5 — the replay: DRE-4467's eight children
# ===========================================================================
class TheRoundRobinReplayTest(unittest.TestCase):
    """The epic that cost the most: approved 2026-09-21 20:03 PT, all seven
    build cards refused, and nothing moved until a person stamped them on
    2026-09-22."""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.children = cls.doc["children"]

    def test_the_fixture_is_the_epic_s_eight_children(self):
        self.assertEqual(self.doc["epic"], "DRE-4467")
        self.assertEqual(len(self.children), 8)

    def test_the_seven_build_cards_are_stamped_fleet(self):
        self.assertEqual(
            _stamped(self.children),
            {f"DRE-{n}": "FLEET" for n in range(4468, 4475)},
        )

    def test_the_proof_card_is_left_to_the_proof_check(self):
        self.assertNotIn("DRE-4475", _stamped(self.children))

    def test_and_the_proof_check_routes_it_operator(self):
        """The two steps between them cover all eight, with no card getting two
        verdicts and none getting none."""
        proof = proof_and_demo.stamps(self.children)
        self.assertEqual([(i, v) for i, v, _ in proof], [("DRE-4475", "OPERATOR")])
        covered = set(_stamped(self.children)) | {i for i, _, _ in proof}
        self.assertEqual(covered, {c["identifier"] for c in self.children})

    def test_every_one_of_the_seven_was_a_judgement_call(self):
        """Why this replay is the one that matters: not one of the seven was
        decided by a label, a title or a criteria signal. Had the undecidable
        case been left unstamped, the fix would have changed nothing for this
        epic."""
        for child in self.children[:7]:
            decision = routing_verdict.route(
                child["title"], child["body"], child["labels"])
            self.assertIsNone(decision.verdict, child["identifier"])
            self.assertTrue(decision.needs_model, child["identifier"])

    def test_the_run_writes_seven_comments_and_no_marks(self):
        count, posted, labelled = _written(self.children)
        self.assertEqual(count, 7)
        self.assertEqual([i for i, _ in posted],
                         [f"DRE-{n}" for n in range(4468, 4475)])
        self.assertEqual(labelled, [])


# ===========================================================================
# 6 — one write path
# ===========================================================================
class OneWritePathTest(unittest.TestCase):

    def test_the_module_writes_only_through_stamp_card(self):
        """`routing_verdict.stamp_card` is the comment AND the marks, for every
        caller. A second implementation would be two writers of one record, free
        to disagree about which labels a verdict applies."""
        src = MODULE.read_text(encoding="utf-8")
        self.assertIn("routing_verdict.stamp_card", src)
        for forbidden in ("cmd_comment", "add_label"):
            self.assertNotIn(forbidden, src, forbidden)

    def test_a_refused_write_is_counted_as_not_written(self):
        already = routing_verdict.verdict_comment("FLEET", "an agent builds it")
        count, _, _ = _written(_plan(), existing={"DRE-901": [already]})
        self.assertEqual(count, 1)


# ===========================================================================
# 7 — wired into the plan run, everywhere a plan's children are finished
# ===========================================================================
def wf_steps() -> list:
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def step_named(fragment: str) -> dict:
    for step in wf_steps():
        if fragment.lower() in (step.get("name") or "").lower():
            return step
    raise AssertionError(
        f"no plan.yml step whose name contains {fragment!r}; have: "
        + ", ".join(repr(s.get("name")) for s in wf_steps()))


GATE = "Routing verdicts"


class WiredIntoThePlanRunTest(unittest.TestCase):

    def test_the_step_exists_and_reads_the_cards_the_planner_created(self):
        run = step_named(GATE)["run"]
        self.assertIn("children-detail", run)
        self.assertIn("plan_child_verdicts.py stamp", run)

    def test_it_can_reach_linear(self):
        step = step_named(GATE)
        self.assertEqual((step.get("env") or {}).get("LINEAR_API_KEY"),
                         "${{ secrets.LINEAR_API_KEY }}")

    def test_it_runs_only_when_the_planner_created_children(self):
        gate = step_named(GATE).get("if", "")
        self.assertIn("steps.kids.outputs.count", gate)
        self.assertIn("!= '0'", gate)
        self.assertIn("steps.route.outputs.mode == 'plan'", gate)

    def test_it_runs_after_the_proof_check(self):
        """The order we chose, and the reason: the proof gate BOUNCES the epic
        back to Planning and exits 1, and an epic on its way back to Planning is
        not an epic whose cards get routing decisions written on them — the same
        rule `proof_and_demo.stamps()` already applies to its own card. Running
        after also means the proof card already carries its verdict, so nothing
        here has to be careful about it."""
        names = [s.get("name") or "" for s in wf_steps()]
        self.assertLess(
            names.index(next(n for n in names if "Proof card" in n)),
            names.index(next(n for n in names if GATE in n)),
            "the routing-verdict stamper runs after the proof card gate")

    def test_it_runs_before_the_plan_reaches_the_ceo(self):
        """The card's own criterion: the children carry their verdicts BEFORE
        the epic can be approved."""
        names = [s.get("name") or "" for s in wf_steps()]
        self.assertLess(
            names.index(next(n for n in names if GATE in n)),
            names.index(next(n for n in names if "Epic → Green Light" in n)))

    def test_a_revised_plan_is_stamped_too(self):
        """A re-plan is the planner's output too: cards it added after round 1
        would otherwise reach Backlog with nothing on them."""
        run = step_named("Re-check the revised plan")["run"]
        self.assertIn("plan_child_verdicts.py stamp", run)

    def test_the_activate_route_stamps_before_it_promotes(self):
        """The backstop, and the one that closes the last gap: the post-approval
        re-plan (after the second critic sends a plan back) re-runs no proof
        gate, so a card it changed would reach the promoter unstamped. The
        stamper runs before the epic is moved to In Progress and before
        `reconcile.py --promote-only`, so nothing can be promoted ahead of it."""
        run = step_named("Activate the approved epic")["run"]
        self.assertIn("plan_child_verdicts.py stamp", run)
        self.assertLess(run.index("plan_child_verdicts.py stamp"),
                        run.index('state "$EPIC" "In Progress"'))
        self.assertLess(run.index("plan_child_verdicts.py stamp"),
                        run.index("--promote-only"))

    def test_the_gate_is_not_silently_optional(self):
        self.assertNotIn("continue-on-error", step_named(GATE))

    def test_the_planner_is_told_it_does_not_stamp_its_own_children(self):
        """Both planner prompts — the first run and the re-plan — say the run
        writes the verdicts, so a planner does not invent its own and leave the
        cards carrying two."""
        prompts = [s["with"]["prompt"] for s in wf_steps()
                   if isinstance(s.get("with"), dict)
                   and "prompt" in s["with"]
                   and "linear_ops.py subissue" in s["with"]["prompt"]]
        self.assertGreaterEqual(len(prompts), 2, "both planner blocks")
        for prompt in prompts:
            self.assertIn("You do NOT stamp the children's routing verdicts",
                          prompt)


if __name__ == "__main__":
    unittest.main()
