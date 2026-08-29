"""The two plan critics, wired into the plan run (DRE-2721).

scripts/plan_critic.py is only worth anything if the plan rail actually runs
both passes, in the right places, against the right text. These tests pin the
rail:

  1. ORDER — the first critic runs BEFORE the epic reaches Green Light (it
     protects the CEO's attention, so it cannot run after the CEO has spent
     it), and the second runs AFTER approval and BEFORE the children promote
     (an adversarial pass is only worth much against a fixed target, and after
     promotion the gap is no longer free to fix).
  2. THE BOUND — the plan route carries at most two critic rounds, opens the
     planning cycle those rounds are counted from, and the epic reaches Green
     Light on `always`-style conditions rather than only when the critic
     passed. An unbounded loop is how 17 cards sat in a lane for 27 days; a
     budget counted over the epic's lifetime instead of the current attempt is
     how a re-planned epic loses its revision round.
  3. DIFFERENCE — the two prompts are visibly different: each carries its own
     stage charter, and neither carries the other's question.
  4. SIGHT — the second critic's prompt is handed the cross-epic scope block,
     generated from the epics actually in flight, not the words "consider
     other work".
  5. THE ROSTER — both critics exist in agents.yaml and config/models.yaml as
     advisory roles, so the console can see them and neither lands on the
     build ladder by default.
  6. THE STANDARD — standards/plan-critic.md rides the same assemble_context
     rail as its siblings, for both stages.

A prompt is the one part of this pipeline with no compiler: a step that
disappears in a later whitespace edit fails silently, and the only symptom is
plans reaching the CEO unreviewed again.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_critic_wiring.py -v
"""

from __future__ import annotations

import os
import re
import sys
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")
STANDARD = os.path.join(ROOT, "standards", "plan-critic.md")
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import assemble_context as ac  # noqa: E402
import plan_critic as pc  # noqa: E402

ACTION = "anthropics/claude-code-action"


def wf_src() -> str:
    return open(WF).read()


def steps() -> list[dict]:
    doc = yaml.safe_load(wf_src())
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def index_of(fragment: str) -> int:
    """Position of the first step whose name contains `fragment`."""
    for i, s in enumerate(steps()):
        if fragment.lower() in (s.get("name") or "").lower():
            return i
    raise AssertionError(
        f"no step named like {fragment!r}; have: "
        + ", ".join(repr(s.get("name")) for s in steps())
    )


def step_named(fragment: str) -> dict:
    return steps()[index_of(fragment)]


def agent_steps() -> list[dict]:
    return [s for s in steps() if str(s.get("uses") or "").split("@")[0] == ACTION]


def prompt_of(fragment: str) -> str:
    return str((step_named(fragment).get("with") or {}).get("prompt") or "")


# The step names the rail is pinned to. Renaming a step is fine; renaming it
# without updating this list is what these tests exist to catch.
FIRST_R1 = "first critic — round 1"
FIRST_R2 = "first critic — round 2"
SECOND = "second critic — review"
REPLAN = "re-plan after send-back"
GREEN_LIGHT = "Epic → Green Light"
ACTIVATE = "Activate the approved epic"
SIGHT = "second critic — cross-epic sight"
ROUTE = "Route — plan or activate"


class TheFirstCriticRunsBeforeTheCeo(unittest.TestCase):
    def test_it_runs_after_the_plan_and_before_green_light(self):
        self.assertLess(index_of("Plan epic"), index_of(FIRST_R1))
        self.assertLess(index_of(FIRST_R1), index_of(GREEN_LIGHT))
        self.assertLess(index_of(FIRST_R2), index_of(GREEN_LIGHT))

    def test_it_only_runs_on_the_plan_route(self):
        self.assertIn("mode == 'plan'", str(step_named(FIRST_R1).get("if")))

    def test_it_does_not_run_when_the_planner_asked_questions_instead(self):
        """No children means no plan to review — and the epic goes back to
        Backlog rather than to the CEO."""
        self.assertIn("kids.outputs.count", str(step_named(FIRST_R1).get("if")))

    def test_the_epic_still_reaches_green_light_when_the_critic_held_it(self):
        """AC4 read off the rail: Green Light is not conditioned on the
        critic's verdict, so no verdict can strand the epic short of the CEO."""
        gate = str(step_named(GREEN_LIGHT).get("if") or "")
        self.assertNotIn("critic", gate.lower())
        self.assertNotIn("action", gate.lower())


class TheSecondCriticRunsAfterApproval(unittest.TestCase):
    def test_it_runs_on_the_activate_route(self):
        self.assertIn("mode == 'activate'", str(step_named(SECOND).get("if")))

    def test_it_runs_before_the_children_are_promoted(self):
        """`This is the last point at which a gap is free to fix — after this
        the cards enter Backlog and agents build them.`"""
        self.assertLess(index_of(SECOND), index_of(ACTIVATE))
        activate = str(step_named(ACTIVATE).get("run") or "")
        self.assertIn("--promote-only", activate)

    def test_nothing_promotes_children_before_the_second_critic(self):
        promoters = [
            i for i, s in enumerate(steps())
            if "--promote-only" in str(s.get("run") or "")
        ]
        self.assertTrue(promoters, "no step promotes children at all")
        self.assertTrue(
            all(i > index_of(SECOND) for i in promoters),
            "a step promotes the epic's children before the second critic runs",
        )

    def test_promotion_is_gated_on_the_critics_decision(self):
        self.assertIn("action", str(step_named(ACTIVATE).get("if") or "").lower())

    def test_it_is_handed_the_cross_epic_sight_block(self):
        self.assertLess(index_of(SIGHT), index_of(SECOND))
        self.assertIn("sight", prompt_of(SECOND).lower())


class TheTwoPromptsAreVisiblyDifferent(unittest.TestCase):
    """AC6 — a reviewer can tell which is which without being told."""

    def test_each_prompt_names_its_own_stage(self):
        self.assertIn(f"charter {pc.STAGE_PRE}", prompt_of(FIRST_R1))
        self.assertIn(f"charter {pc.STAGE_POST}", prompt_of(SECOND))

    def test_each_prompt_carries_its_own_question_and_not_the_others(self):
        first, second = prompt_of(FIRST_R1), prompt_of(SECOND)
        self.assertIn(pc.question(pc.STAGE_PRE), first)
        self.assertNotIn(pc.question(pc.STAGE_POST), first)
        self.assertIn(pc.question(pc.STAGE_POST), second)
        self.assertNotIn(pc.question(pc.STAGE_PRE), second)

    def test_the_two_prompts_are_not_the_same_prompt(self):
        self.assertNotEqual(prompt_of(FIRST_R1).strip(), prompt_of(SECOND).strip())

    def test_both_rounds_of_the_first_critic_ask_the_same_question(self):
        """The bound is about rounds, not about changing the question halfway:
        round 2 re-asks round 1's question against the revised plan."""
        self.assertIn(pc.question(pc.STAGE_PRE), prompt_of(FIRST_R2))

    def test_neither_prompt_can_forge_a_merge_credential(self):
        for fragment in (FIRST_R1, FIRST_R2, SECOND):
            for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
                self.assertNotIn(forbidden, prompt_of(fragment),
                                 f"{fragment} emits a verdict-shaped string")

    def test_both_prompts_write_a_result_file_the_run_reads(self):
        for fragment in (FIRST_R1, FIRST_R2, SECOND):
            self.assertIn(pc.RESULT_PREFIX, prompt_of(fragment))

    def test_both_critics_are_told_the_epic_text_is_untrusted(self):
        for fragment in (FIRST_R1, SECOND):
            self.assertIn("UNTRUSTED CARD TEXT", prompt_of(fragment))


class TheBoundIsWired(unittest.TestCase):
    def test_the_plan_route_carries_exactly_two_critic_rounds(self):
        rounds = [s for s in agent_steps()
                  if "first critic" in (s.get("name") or "").lower()]
        self.assertEqual(len(rounds), pc.MAX_ROUNDS)

    def test_the_re_plan_only_happens_after_a_send_back(self):
        self.assertLess(index_of(FIRST_R1), index_of(REPLAN))
        self.assertLess(index_of(REPLAN), index_of(FIRST_R2))
        self.assertIn("hold", str(step_named(REPLAN).get("if") or ""))

    def test_a_planning_attempt_opens_the_cycle_the_bound_is_counted_from(self):
        """The budget is per planning ATTEMPT. The route step is the one place
        that decides an epic is being planned (or RE-planned), so it is where
        the boundary the critics count from has to be written — before either
        critic reads the thread."""
        route = str(step_named(ROUTE).get("run") or "")
        self.assertIn("plan_critic.py cycle-start", route)
        self.assertLess(index_of(ROUTE), index_of(FIRST_R1))

    def test_every_decision_step_routes_through_the_one_decider(self):
        deciders = [s for s in steps()
                    if "plan_critic.py decide" in str(s.get("run") or "")]
        self.assertGreaterEqual(len(deciders), 3,
                                "each critic round must decide through plan_critic.py")

    def test_every_decider_reads_an_author_bound_thread(self):
        """The bound is counted out of markers in the epic's comment thread, so
        those markers are this gate's credential. A decider fed the plain
        `dump-comments` shape believes every commenter on the epic equally —
        which is how two stray comments could spend a budget nobody spent, and
        one could refund a budget that was. The flag is the difference between
        a thread with authors and a thread of anonymous text.
        """
        deciders = [s for s in steps()
                    if "plan_critic.py decide" in str(s.get("run") or "")]
        self.assertTrue(deciders)
        for s in deciders:
            run = str(s.get("run"))
            name = s.get("name")
            self.assertIn("dump-comments", run, name)
            self.assertIn("--with-authors", run, name)
            # ...and the boundary that refunds a budget must name THIS epic,
            # so the standard's own worked example stays inert elsewhere.
            self.assertIn("--epic", run, name)

    def test_the_job_timeout_leaves_room_for_two_planner_runs_and_two_reviews(self):
        doc = yaml.safe_load(wf_src())
        timeout = doc["jobs"]["plan"]["timeout-minutes"]
        turns = [int(m) for m in re.findall(r"--max-turns\s+(\d+)", wf_src())]
        # 7 s/turn is the upper end measured on completed portico runs, plus
        # ~8 minutes of token minting, checkouts, context assembly and Linear
        # calls that the turn arithmetic does not model.
        self.assertGreaterEqual(timeout, sum(turns) * 7 / 60 + 8,
                                "the plan job cannot finish the rounds it now runs")

    def test_the_planning_stall_window_still_exceeds_the_job(self):
        """reconcile flags a Planning card nothing is happening to; it must not
        alarm on a plan run that is simply still going."""
        import lane_contract
        doc = yaml.safe_load(wf_src())
        self.assertGreater(lane_contract.stale_minutes()["Planning"],
                           doc["jobs"]["plan"]["timeout-minutes"])


class TheRoster(unittest.TestCase):
    """agents.yaml is the console's contract; config/models.yaml is the ladder."""

    def setUp(self):
        self.registry = yaml.safe_load(open(os.path.join(ROOT, "agents.yaml")))["agents"]
        self.models = yaml.safe_load(open(os.path.join(ROOT, "config", "models.yaml")))

    def entry(self, name):
        for a in self.registry:
            if a["name"] == name:
                return a
        raise AssertionError(f"no agents.yaml entry named {name!r}")

    def test_both_critics_are_registered(self):
        for name in (pc.AGENT_PRE, pc.AGENT_POST):
            self.assertEqual(self.entry(name)["workflow"],
                             ".github/workflows/plan.yml")

    def test_both_critics_judge_rather_than_build(self):
        """They gate what the CEO's time is spent on and what agents build —
        advisory, like every other role that judges."""
        for name in (pc.AGENT_PRE, pc.AGENT_POST):
            self.assertEqual(self.models["agents"][name], "advisory")
            self.assertEqual(self.entry(name)["kind"], "advisory")

    def test_the_roles_the_workflow_selects_are_the_roles_the_config_names(self):
        for name in (pc.AGENT_PRE, pc.AGENT_POST):
            self.assertIn(f"model_fallback.py select {name}", wf_src())


class TheStandard(unittest.TestCase):
    def test_the_standard_exists(self):
        self.assertTrue(os.path.exists(STANDARD))

    def test_both_stages_read_it(self):
        for role in (pc.AGENT_PRE, pc.AGENT_POST):
            self.assertIn("plan-critic.md", ac.standards_for(role))

    def test_the_two_roles_do_not_read_the_same_context(self):
        """The second critic asks what an agent will get wrong, so it reads the
        engineering floor the first one has no use for."""
        self.assertNotEqual(ac.standards_for(pc.AGENT_PRE),
                            ac.standards_for(pc.AGENT_POST))

    def test_the_standard_names_the_bound_and_the_tripwire(self):
        text = open(STANDARD).read().lower()
        self.assertIn("two failed rounds", text)
        self.assertIn("tripwire", text)
        self.assertIn("send-back rate", text)

    def test_the_standards_index_lists_it(self):
        index = open(os.path.join(ROOT, "standards", "README.md")).read()
        self.assertIn("plan-critic.md", index)


if __name__ == "__main__":
    unittest.main()
