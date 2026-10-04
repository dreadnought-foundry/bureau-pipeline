"""The two plan critics, wired into the plan run (DRE-2721).

scripts/plan_critic.py is only worth anything if the plan rail actually runs
both passes, in the right places, against the right text. These tests pin the
rail:

  1. ORDER — the first critic runs BEFORE the epic reaches Green Light (it
     protects the CEO's attention, so it cannot run after the CEO has spent
     it), and the second runs after it and ALSO before Green Light (DRE-5280),
     so the activate route runs no review at all: Approve means go (DRE-5281),
     and nothing promotes before the second critic has passed the plan.
  2. THE BOUND — the plan route carries at most two critic rounds, opens the
     planning cycle those rounds are counted from, and a plan still held at
     the bound parks in Triage for an operator (DRE-5284) rather than looping.
     An unbounded loop is how 17 cards sat in a lane for 27 days; a
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

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")
STANDARD = os.path.join(ROOT, "standards", "plan-critic.md")
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import assemble_context as ac  # noqa: E402
import check_act_receipts as car  # noqa: E402
import plan_artifact as pa  # noqa: E402
import plan_critic as pc  # noqa: E402
import review_rerun as rr  # noqa: E402

ACTION = "anthropics/claude-code-action"

# The wording DRE-3292 retired — the two-lane dance the notices asked a person
# for before the relay learned the re-run act. Assembled, never written out:
# the sweep below reads this file too, and a pin spelled in full would be the
# one hit it could never clear.
RETIRED_MOVE = " ".join(("Green", "Light,", "then", "approve"))


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


def assert_app_token_minted_after_review(case: unittest.TestCase, fragment: str) -> None:
    """The step's GH_TOKEN is a GitHub App token, minted AFTER the second
    critic's review — never the one minted at job start, which a review past
    the hour has already outlived (DRE-3940; tests/test_plan_token_remint.py
    owns the general rule)."""
    env = step_named(fragment).get("env") or {}
    ids = re.findall(r"^\$\{\{ steps\.([A-Za-z0-9_-]+)\.outputs\.token \}\}$",
                     str(env.get("GH_TOKEN") or ""))
    case.assertEqual(len(ids), 1, env.get("GH_TOKEN"))
    at = next(i for i, s in enumerate(steps()) if s.get("id") == ids[0])
    case.assertEqual(str(steps()[at].get("uses") or "").split("@")[0],
                     "actions/create-github-app-token")
    case.assertGreater(at, index_of("Second critic — review (before Green Light)"))
    case.assertLess(at, index_of(fragment))


def prompt_of(fragment: str) -> str:
    return str((step_named(fragment).get("with") or {}).get("prompt") or "")


# The step names the rail is pinned to. Renaming a step is fine; renaming it
# without updating this list is what these tests exist to catch.
FIRST_R1 = "first critic — round 1"
FIRST_R2 = "first critic — round 2"
SECOND = "second critic — review"
REPLAN = "re-plan after send-back"
# The plan route's last move (DRE-5284): a passed plan is handed to the second
# critic, never to Green Light, and the first critic's bound parks in Triage.
HANDOFF = "Plan → second critic"
PRE_PARK = "First critic — the bound parks in Triage"
ACTIVATE = "Activate the approved epic"
SIGHT = "second critic — cross-epic sight"
ROUTE = "Route — plan or activate"


class TheFirstCriticRunsBeforeTheCeo(unittest.TestCase):
    def test_it_runs_after_the_plan_and_before_the_hand_off(self):
        self.assertLess(index_of("Plan epic"), index_of(FIRST_R1))
        self.assertLess(index_of(FIRST_R1), index_of(HANDOFF))
        self.assertLess(index_of(FIRST_R2), index_of(HANDOFF))

    def test_it_only_runs_on_the_plan_route(self):
        self.assertIn("mode == 'plan'", str(step_named(FIRST_R1).get("if")))

    def test_it_does_not_run_when_the_planner_asked_questions_instead(self):
        """No children means no plan to review — and the epic goes back to
        Backlog rather than to the CEO."""
        self.assertIn("kids.outputs.count", str(step_named(FIRST_R1).get("if")))

    def test_a_held_plan_is_never_handed_on(self):
        """DRE-5284, read off the rail: the hand-off is conditioned on the first
        critic's last decision being a proceed. Until this card the Green Light
        move carried no verdict at all, so a plan the critic held twice still
        reached the CEO; under DRE-5268 it parks instead."""
        gate = str(step_named(HANDOFF).get("if") or "")
        self.assertIn("steps.pre1.outputs.action == 'proceed' || "
                      "steps.pre2.outputs.action == 'proceed'", gate)


POST_REPLAN = "Re-plan after the second critic sent it back"
# The review route's two hold outcomes (DRE-5280): below the bound the revised
# plan is reviewed again, at it the plan parks in Triage. Since DRE-5281 they
# are the only send-back outcomes — the activate route runs no review.
RE_REVIEW = "Review — the revised plan is reviewed again"
BOUND_PARK = "Review — the bound parks the plan in Triage"
# What DRE-5281 deleted with the activate route's review: the snapshot, the
# card-set diff and the activate-mode send-back that read them, and the
# activate-mode death.
RETIRED_STEPS = ("Children before the re-plan", "Re-plan — did the card set change?",
                 "Second critic sent the plan back", "Second critic — the review died")


class ASendBackRevisesThenParks(unittest.TestCase):
    """DRE-3088, read off the rail as DRE-5281 leaves it. A held plan is
    revised before anyone reads it again; a plan held twice parks for an
    operator in Triage; and no receipt names Todo."""

    def test_the_re_plan_runs_between_the_decision_and_the_outcomes(self):
        self.assertLess(index_of(SECOND), index_of(POST_REPLAN))
        self.assertLess(index_of(POST_REPLAN), index_of(RE_REVIEW))
        self.assertLess(index_of(POST_REPLAN), index_of(BOUND_PARK))
        self.assertLess(index_of(BOUND_PARK), index_of(ACTIVATE))

    def test_the_re_plan_is_gated_on_a_hold_and_cannot_strand_the_epic(self):
        replan = step_named(POST_REPLAN)
        self.assertIn("action == 'hold'", str(replan.get("if")))
        self.assertIn("mode == 'review'", str(replan.get("if")))
        self.assertNotIn("mode == 'activate'", str(replan.get("if")))
        self.assertTrue(replan.get("continue-on-error"),
                        "a re-plan that dies must not stop the outcome after it")
        self.assertIn("RE-plan", str(replan["with"]["prompt"]))
        self.assertIn("post1.outputs.reason", str(replan["with"]["prompt"]))

    def test_the_park_reads_the_bound_and_stamps_needs_human(self):
        park = step_named(BOUND_PARK)
        self.assertIn("steps.post1.outputs.bound == 'true'", str(park.get("if")))
        run = str(park.get("run") or "")
        self.assertIn('add-label "$EPIC" needs-human', run)
        self.assertIn('state "$EPIC" "Triage"', run)
        self.assertNotIn('state "$EPIC" "Green Light"', run)

    def test_no_receipt_on_the_activate_route_names_todo(self):
        run = str(step_named(ACTIVATE).get("run") or "")
        self.assertIn("In Progress", run)
        self.assertNotIn("Todo", run)

    def test_the_activate_step_runs_on_the_activate_route_alone(self):
        """No critic decision gates it any more: it reads the second critic's
        state off the thread itself (`post-state`)."""
        self.assertEqual(str(step_named(ACTIVATE).get("if")).strip(),
                         "steps.route.outputs.mode == 'activate'")

    def test_the_approval_lane_is_a_live_lane_of_the_contract(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import lane_contract  # noqa: E402
        self.assertIn(pc.APPROVAL_LANE, lane_contract.lane_names())
        self.assertNotEqual(pc.APPROVAL_LANE, "Todo")

    def test_the_standard_says_the_post_bound_parks(self):
        text = open(os.path.join(ROOT, "standards", "plan-critic.md")).read()
        self.assertIn("two failed rounds", text.lower())
        self.assertIn("needs-human", text)
        self.assertIn("never Todo", text)


class TheSecondCriticRunsBeforeTheChildrenPromote(unittest.TestCase):
    def test_it_runs_on_the_review_route_alone(self):
        gate = str(step_named(SECOND).get("if"))
        self.assertIn("mode == 'review'", gate)
        self.assertNotIn("mode == 'activate'", gate)

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

    def test_promotion_is_gated_on_the_critics_recorded_pass(self):
        """DRE-5281: the gate is the promoter's own reading of the thread,
        `post-state`, compared with the module's word — never a literal."""
        run = str(step_named(ACTIVATE).get("run") or "")
        self.assertIn("plan_critic.py post-state", run)
        self.assertIn("plan_critic.POST_RELEASED", run)
        self.assertNotIn(f'"{pc.POST_RELEASED}"', run)

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

    def test_every_decider_posts_its_record_as_a_comment_of_its_own(self):
        """Authorship is only half the credential. The same Linear key posts
        the PLANNER's plan write-up to the same epic — freeform prose over
        untrusted card text — so a record has to be a comment that says nothing
        else (`plan_critic._sole_record`). That only holds while the run keeps
        the record out of the note the CEO reads: one decision, two comments.
        """
        deciders = [s for s in steps()
                    if "plan_critic.py decide" in str(s.get("run") or "")]
        self.assertTrue(deciders)
        for s in deciders:
            run, name = str(s.get("run")), s.get("name")
            self.assertIn("--record-file", run, name)
            self.assertEqual(run.count("linear_ops.py comment"), 2, name)

    def test_the_boundary_is_posted_alone_too(self):
        route = str(step_named(ROUTE).get("run") or "")
        self.assertIn("cycle-start --epic \"$EPIC\" --record", route)
        self.assertEqual(route.count("linear_ops.py comment"), 2)

    def test_the_job_timeout_leaves_room_for_two_planner_runs_and_two_reviews(self):
        doc = yaml.safe_load(wf_src())
        timeout = doc["jobs"]["plan"]["timeout-minutes"]
        turns = [int(m) for m in re.findall(r"--max-turns\s+(\d+)", wf_src())]
        # DRE-3970: a planner step's re-run on the next rung only starts after
        # an attempt that did no work — a capacity refusal is one turn and $0,
        # and anything more is vetoed — so the pair's worst case is ONE ceiling.
        # The re-runs are taken back out of the sum, one ceiling each.
        retries = [s for s in agent_steps()
                   if str(s.get("id") or "").endswith("_retry")]
        for step in retries:
            # A re-run whose ceiling is an expression (the re-plans, DRE-5288)
            # was never in the literal scan, so there is nothing to take out.
            literal = re.search(r"--max-turns\s+(\d+)",
                                step["with"]["claude_args"])
            if literal:
                turns.remove(int(literal.group(1)))
        # FIVE of the agent steps carry an EXPRESSION rather than a literal, so
        # the literal scan above cannot see them; each one's worst case is
        # added by name so the arithmetic keeps counting it.
        #
        #   * the post-approval review, sized per plan since DRE-3241 — and
        #     since DRE-3289 its worst case is the RETRY cap, not the first-run
        #     one: a review that died is re-run in a job of its own at
        #     `retry_ceiling` turns, and the arithmetic has to cover the
        #     longest run this workflow can start.
        #   * the one-off read, sized per card since DRE-4381, whose worst case
        #     is likewise its retry cap — the re-read after a turn-ceiling
        #     death.
        #   * the two re-plans — after the first critic and after the second —
        #     sized per plan since DRE-5288, whose worst case is the cap.
        #   * the planner's revision of a one-off card (DRE-5376), which runs at
        #     the one-off read's own ceiling and so shares its worst case.
        #
        # A SIXTH expression would drop out of this arithmetic, which is what
        # the count below refuses.
        revision = pc.ONE_OFF_TURNS_RETRY_CAP
        expressions = (rr.POST_REVIEW_RETRY_CAP, pc.ONE_OFF_TURNS_RETRY_CAP,
                       pc.REPLAN_TURNS_CAP, pc.REPLAN_TURNS_CAP, revision)
        self.assertEqual(
            len(turns), len(agent_steps()) - len(retries) - len(expressions),
            "every agent step but the five sized ones carries a literal "
            "ceiling; a sixth expression would drop out of this arithmetic",
        )
        # The sum below already adds the one-off READ to every epic-route run,
        # which no run ever does: the routes are exclusive. The one-off
        # REVISION is not added on top of that overcount (DRE-5376) — it runs
        # only on the one-off route, which is checked on its own below, and
        # its gate reads the one-off decision, so the exclusivity is read off
        # the step rather than assumed.
        revisers = [s for s in agent_steps()
                    if "steps.oneoff.outputs.action" in str(s.get("if") or "")]
        self.assertEqual(1, len(revisers), revisers)
        turns.extend(expressions[:-1])
        # 7 s/turn is the upper end measured on completed portico runs, plus
        # ~8 minutes of token minting, checkouts, context assembly and Linear
        # calls that the turn arithmetic does not model.
        self.assertGreaterEqual(timeout, sum(turns) * 7 / 60 + 8,
                                "the plan job cannot finish the rounds it now runs")
        # ...and the one-off route alone: the classifier, one read and one
        # revision, each at its worst case.
        self.assertGreaterEqual(
            timeout, (pc.ONE_OFF_TURNS_RETRY_CAP + revision) * 7 / 60 + 8,
            "the one-off route cannot finish a read and a revision")

    def test_the_planning_stall_window_still_exceeds_the_job(self):
        """reconcile flags a Planning card nothing is happening to; it must not
        alarm on a plan run that is simply still going."""
        import lane_contract
        doc = yaml.safe_load(wf_src())
        self.assertGreater(lane_contract.stale_minutes()["Planning"],
                           doc["jobs"]["plan"]["timeout-minutes"])


ONE_OFF_MODEL = "Select model — one-off critic"
ONE_OFF_CTX = "One-off critic — context"
ONE_OFF_CRITIC = "Pre-approval critic — the one-off exit"
ONE_OFF_DECISION = "One-off critic — decision"
ONE_OFF_ESCALATE = "One-off critic — escalate"
ONE_OFF_EXIT = "One-off route — checked on the way out"


class ThePreApprovalCriticReadsTheOneOffExit(unittest.TestCase):
    """DRE-3041. A one-off used to reach an engineer having been read by
    nobody: the shape stamp was the only judgement on the fast path, and the
    first adversarial eye was the code critic on the pull request, after the
    build had been paid for.

    The rail these pin: the SAME first critic reads the card between the stamp
    and the move, and the move happens only if it passed.
    """

    def test_it_runs_only_on_the_one_off_route(self):
        for fragment in (ONE_OFF_MODEL, ONE_OFF_CRITIC, ONE_OFF_DECISION):
            self.assertIn("steps.shape.outputs.route == 'one-off'",
                          str(step_named(fragment).get("if")), fragment)

    def test_it_runs_before_the_card_leaves_planning(self):
        """`before planning_route.py exit moves the card`. After the move the
        card is in the build queue and an agent picks it up."""
        self.assertLess(index_of(ONE_OFF_CRITIC), index_of(ONE_OFF_DECISION))
        self.assertLess(index_of(ONE_OFF_DECISION), index_of(ONE_OFF_EXIT))

    def test_the_exit_moves_the_card_only_on_a_pass(self):
        gate = str(step_named(ONE_OFF_EXIT).get("if") or "")
        self.assertIn("steps.oneoff.outputs.action == 'proceed'", gate)

    def test_a_fail_takes_the_escalation_exit(self):
        """DRE-2848's exit, not a second one — the same seam the classifier's
        refusal uses, so the card parks in the lane a plan waits in."""
        step = step_named(ONE_OFF_ESCALATE)
        self.assertIn("steps.oneoff.outputs.action == 'escalate'",
                      str(step.get("if")))
        self.assertIn("planning_escalation.py escalate", str(step.get("run")))
        self.assertIn("--reason-file", str(step.get("run")))

    def test_the_escalation_step_names_no_lane_of_its_own(self):
        import lane_contract
        body = str(step_named(ONE_OFF_ESCALATE).get("run") or "")
        for lane in lane_contract.lane_names(status="live"):
            self.assertNotIn(lane, body,
                             f"the escalation step names the lane {lane!r}")

    def test_a_critic_that_cannot_run_still_reaches_the_decision(self):
        """AC3, read off the rail. A model that cannot be selected and a critic
        step that died must both land in the decision step, which fails closed —
        so neither may abort the job and take the decision with it."""
        for fragment in (ONE_OFF_MODEL, ONE_OFF_CRITIC):
            self.assertTrue(step_named(fragment).get("continue-on-error"),
                            f"{fragment} must not abort the run")
        gate = str(step_named(ONE_OFF_DECISION).get("if") or "")
        self.assertIn("!cancelled()", gate)

    def test_the_critic_step_is_skipped_when_no_model_was_selected(self):
        self.assertIn("steps.oomodel.outputs.model != ''",
                      str(step_named(ONE_OFF_CRITIC).get("if")))

    def test_the_decision_writes_the_reason_the_escalation_reads(self):
        run = str(step_named(ONE_OFF_DECISION).get("run") or "")
        self.assertIn(f"--stage {pc.STAGE_ONE_OFF}", run)
        self.assertIn("--escalation-file", run)

    def test_it_is_the_existing_critic_and_not_a_third_role(self):
        """`No new role in config/models.yaml.` The step selects the first
        critic's ladder and assembles the first critic's context."""
        self.assertIn(f"model_fallback.py select {pc.AGENT_PRE}",
                      str(step_named(ONE_OFF_MODEL).get("run")))
        self.assertIn(f"assemble_context.py assemble {pc.AGENT_PRE}",
                      str(step_named(ONE_OFF_CTX).get("run")))

    def test_the_prompt_carries_the_one_off_charter_and_question(self):
        prompt = prompt_of(ONE_OFF_CRITIC)
        self.assertIn(f"charter {pc.STAGE_ONE_OFF}", prompt)
        self.assertIn(pc.question(pc.STAGE_ONE_OFF), prompt)
        self.assertNotIn(pc.question(pc.STAGE_PRE), prompt)
        self.assertNotIn(pc.question(pc.STAGE_POST), prompt)

    def test_the_prompt_reads_the_card_and_the_stamps_reason(self):
        """`reads the card and the stamp's --why`."""
        prompt = prompt_of(ONE_OFF_CRITIC)
        self.assertIn("UNTRUSTED CARD TEXT", prompt)
        self.assertIn("${{ steps.card.outputs.description }}", prompt)
        self.assertIn("planning_shape.py read", prompt)

    def test_the_prompt_writes_a_result_file_and_forges_no_credential(self):
        prompt = prompt_of(ONE_OFF_CRITIC)
        self.assertIn(pc.RESULT_PREFIX, prompt)
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, prompt)

    def test_the_call_is_bounded(self):
        """`Cost stays bounded. One call per one-off classification.` One agent
        step on the route, and a ceiling with a cap on it.

        The ceiling stopped being a literal in DRE-4381 — a fixed 20 killed two
        reads of DRE-4378 and told the CEO nothing had checked the card — so
        what "bounded" means here is the module's cap, not a number typed on
        the step. The step is still exactly one call.
        """
        on_route = [s for s in agent_steps()
                    if "one-off" in str(s.get("if") or "")]
        self.assertEqual(1, len(on_route),
                         "a one-off run must ask for exactly one critic call")
        args = str((on_route[0].get("with") or {}).get("claude_args") or "")
        self.assertEqual(1, args.count("--max-turns"))
        self.assertEqual([], re.findall(r"--max-turns\s+(\d+)", args),
                         "the one-off ceiling is sized, never a literal")
        self.assertIn("steps.ooturns.outputs.max_turns", args)
        # ...and what that step can hand it is capped, whatever the card names
        # and however many reads have already run out of turns.
        for files in (0, 3, 5, 40, 1000):
            for deaths in ([], [48], [48, 72]):
                thread = [{"body": pc.death_marker(
                    pc.STAGE_ONE_OFF, str(i), 1, "oocritic",
                    pc.TURN_CAP_SUBTYPE, c, c), "authored_by_pipeline": True}
                    for i, c in enumerate(deaths)]
                self.assertLessEqual(pc.one_off_ceiling(files, thread)[0],
                                     pc.ONE_OFF_TURNS_RETRY_CAP)


ONE_OFF_TURNS = "One-off critic — turn ceiling"
ONE_OFF_RAN_OUT = "One-off critic — the read that ran out of turns"
SHAPE_STEP = "Planning shape — which route this card takes"


class APersonsCardIsRoutedBeforeTheCritic(unittest.TestCase):
    """DRE-5805. The routing rule is strict precedence: an explicit role label
    decides first and no model is asked. On the one-off route the label was
    read only in the exit, which only the critic's pass reaches — so DRE-5349,
    wearing `agent:ops` and `no-code`, was asked whether an agent could build
    it, honestly answered no, and was sent back and parked for nothing.

    The rail these pin: the shape step reads the label first and says when it
    lands on a person; on that answer no model is selected, no read is sized,
    no critic runs and no decision is made, and the exit runs on the answer.
    """

    def setUp(self):
        import planning_route
        self.person = f"steps.shape.outputs.{planning_route.PERSON_OUTPUT}"

    def test_the_shape_step_comes_before_every_one_off_critic_step(self):
        shape = index_of(SHAPE_STEP)
        self.assertEqual("shape", step_named(SHAPE_STEP).get("id"))
        for fragment in (ONE_OFF_MODEL, ONE_OFF_CTX, ONE_OFF_TURNS,
                         ONE_OFF_CRITIC, ONE_OFF_DECISION, ONE_OFF_RAN_OUT):
            self.assertLess(shape, index_of(fragment), fragment)

    def test_the_steps_gated_on_the_route_alone_skip_a_persons_card(self):
        for fragment in (ONE_OFF_MODEL, ONE_OFF_TURNS, ONE_OFF_DECISION):
            gate = str(step_named(fragment).get("if") or "")
            self.assertIn("steps.shape.outputs.route == 'one-off'", gate, fragment)
            self.assertIn(f"{self.person} == ''", gate, fragment)

    def test_the_steps_downstream_of_them_skip_on_their_own(self):
        """Gated on a selected model or on a decision output, so they need no
        gate of their own — and must keep the one they have."""
        for fragment in (ONE_OFF_CTX, ONE_OFF_CRITIC):
            self.assertIn("steps.oomodel.outputs.model != ''",
                          str(step_named(fragment).get("if")), fragment)
        self.assertIn("steps.oneoff.outputs.ran_out == 'true'",
                      str(step_named(ONE_OFF_RAN_OUT).get("if")))

    def test_the_exit_runs_on_a_pass_or_on_a_persons_card(self):
        gate = " ".join(str(step_named(ONE_OFF_EXIT).get("if") or "").split())
        self.assertIn("steps.shape.outputs.route == 'one-off'", gate)
        self.assertRegex(
            gate,
            re.escape("(steps.oneoff.outputs.action == 'proceed' || ")
            + re.escape(f"{self.person} != '')"),
            "the exit must run on the critic's pass OR the person output, "
            "and on nothing else",
        )

    def test_no_gate_names_a_verdict(self):
        """Who is a person is derived from the vocabulary in the script; a
        verdict named in YAML is that answer written twice."""
        import routing_verdict
        for fragment in (ONE_OFF_MODEL, ONE_OFF_TURNS, ONE_OFF_DECISION, ONE_OFF_EXIT):
            gate = str(step_named(fragment).get("if") or "")
            for verdict in routing_verdict.verdicts():
                self.assertNotIn(verdict, gate, f"{fragment} names {verdict}")

    def test_the_comment_over_the_route_states_the_new_order(self):
        src = wf_src()
        start = src.index("# --- The ONE-OFF route")
        block = src[start:src.index(f"- name: {ONE_OFF_MODEL}", start)]
        prose = " ".join(line.strip().lstrip("#").strip() for line in block.splitlines())
        self.assertIn("never read by the critic", prose)
        self.assertIn("DRE-5805", prose)


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

    def test_the_one_off_stage_added_no_role(self):
        """DRE-3041: `No new role in config/models.yaml.` Every stage's agent
        is one of the two the roster already carries."""
        for stage in pc.STAGES:
            self.assertIn(pc.agent(stage), (pc.AGENT_PRE, pc.AGENT_POST))
            self.assertIn(pc.agent(stage), self.models["agents"])


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

    def test_it_names_the_ceiling_formula_that_is_live(self):
        """DRE-3498. The sentence said "fifteen cards get 80 turns" while the
        code said 90 — a document that names a number goes stale the moment
        the number moves, which is why the formula is written out beside it."""
        text = open(STANDARD).read()
        self.assertIn("40 + 4 × children", text)
        self.assertIn("floor 60", text)
        self.assertIn("cap 140", text)
        self.assertIn(f"fifteen cards get {pc.post_review_turns(15)}", text)
        self.assertNotIn("fifteen cards get 80", text)

    def test_its_retry_example_is_the_arithmetic_the_code_does(self):
        text = open(STANDARD).read()
        self.assertIn("100 → 150, 150 → 180", text)
        self.assertNotIn("80 → 120, 120 → 180", text)
        self.assertEqual(rr.retry_ceiling(100), 150)
        self.assertEqual(rr.retry_ceiling(150), 180)

    def test_it_names_the_receipt_the_next_re_tune_is_read_from(self):
        text = open(STANDARD).read()
        self.assertIn("🧮 review-turns:", text)
        self.assertIn("34144302622", text,
                      "the run this card's number was measured from")


# --- DRE-3241: the second critic's review ceiling, and what a dead one leaves ---

CEILING = "Second critic — turn ceiling"
# The death step. Until DRE-5281 the activate route had one of its own; the
# review route's (DRE-5280) is now the only one.
DIED = "Review — the review died"
DECISION = "Second critic — decision"


class TheReviewCeilingIsSizedFromThePlan(unittest.TestCase):
    """The ceiling is computed on the review route — `steps.kids` only
    exists on the plan route — through the one sizer in review_rerun.py, which
    reads the plan for a first run and the epic's own thread for a retry
    (DRE-3289), with the fifteen-card number as the fallback so a bare
    `--max-turns` can never reach the action (qa-review.yml's shape)."""

    def test_the_ceiling_step_runs_before_the_review_on_the_review_route(self):
        self.assertLess(index_of(CEILING), index_of(SECOND))
        step = step_named(CEILING)
        self.assertIn("mode == 'review'", str(step.get("if")))
        run = str(step.get("run") or "")
        self.assertIn("linear_ops.py children", run)
        self.assertIn("review_rerun.py ceiling", run)
        self.assertNotIn("plan_critic.py post-turns", run,
                         "the sizer that cannot see a tombstone was replaced")
        self.assertIn(f"max_turns={pc.post_review_turns(15)}", run,
                      "the fallback must be the fifteen-card number, by value")

    def test_the_ceiling_is_sized_against_the_epics_own_thread(self):
        """A retry is only readable from the thread, and only when the thread
        is read with authors — a tombstone anyone could post is not a death
        (`plan_critic.trusted_bodies`)."""
        run = str(step_named(CEILING).get("run") or "")
        self.assertIn("dump-comments", run)
        self.assertIn("--with-authors", run)
        self.assertIn("--thread-file", run)
        self.assertIn("plan-critic-thread.json", run,
                      "the same thread file the decision step dumps")

    def test_a_retry_gets_the_dead_runs_ceiling_with_headroom(self):
        """The numbers this card writes down, read through the same function
        the workflow step calls: 80 → 120, 120 → 180, and never above the cap."""
        self.assertEqual(rr.retry_ceiling(80), 120)
        self.assertEqual(rr.retry_ceiling(120), 180)
        self.assertEqual(rr.retry_ceiling(180), rr.POST_REVIEW_RETRY_CAP)

    def test_the_review_reads_the_computed_ceiling(self):
        args = str(step_named(SECOND)["with"]["claude_args"])
        self.assertIn("--max-turns ${{ steps.postturns.outputs.max_turns }}", args)
        self.assertNotRegex(args, r"--max-turns\s+\d+")

    def test_fifteen_cards_get_a_hundred_turns_end_to_end(self):
        """The number this card writes down, read through the same function
        the workflow step calls. 80 until DRE-2785 raised the band with the
        web-tool grant, 90 until DRE-3498 raised the base to 40."""
        self.assertEqual(pc.post_review_turns(15), 100)

    def test_the_comments_above_the_step_name_the_numbers_it_uses(self):
        """A change that contradicts a document updates that document — and
        the nearest document to a ceiling is the comment block over the step
        that sets it (standards/engineering.md)."""
        src = wf_src()
        head = src[:src.index("id: postturns")]
        block = head[head.rindex("# The ceiling, sized from the plan"):]
        self.assertIn("fifteen cards → 100", block)
        self.assertIn("100 → 150, 150 → 180", block)
        self.assertNotIn("fifteen cards → 80", block)
        self.assertNotIn("80 → 120, 120 → 180", block)


class ADeadReviewWritesItsTombstone(unittest.TestCase):
    """The step that failed the job on 2026-09-05 wrote nothing. Now a review
    that dies leaves a `🪦` record on the epic, and NOTHING promotes."""

    def test_the_review_is_continue_on_error_and_the_verdict_gates_the_death(self):
        """DRE-3501 replaced the DRE-3241 trap door with a READ. The step
        outcome no longer decides anything on its own: `posta` is
        `continue-on-error`, and what separates a death from a decided round
        is whether the result file holds a verdict.

        The trap stays closed because the decision step is reachable on a
        FAILED review only through a parsed verdict — never through an empty
        result file, which is still `NO_RESULT` and still goes to the
        tombstone."""
        self.assertTrue(step_named(SECOND).get("continue-on-error"))
        died = str(step_named(DIED).get("if") or "")
        self.assertIn("steps.posta.outcome == 'failure'", died)
        self.assertIn("steps.postverdict.outputs.verdict == 'NO_RESULT'", died)

    def test_the_tombstone_step_runs_only_when_the_review_itself_failed(self):
        self.assertLess(index_of(SECOND), index_of(DIED))
        self.assertLess(index_of(DIED), index_of(DECISION))
        gate = str(step_named(DIED).get("if") or "")
        self.assertIn("steps.posta.outcome == 'failure'", gate)
        self.assertIn("mode == 'review'", gate)
        self.assertNotIn("failure()", gate,
                         "`continue-on-error` on the review keeps the job "
                         "green there, so `failure()` would never be true and "
                         "the tombstone would never be written")

    def test_the_job_still_goes_red_on_a_death(self):
        """The review no longer fails the job, so the tombstone step does —
        after its dispatch, so the medic sees exactly what it saw before and
        the retry still happens (DRE-3289's order)."""
        run = str(step_named(DIED).get("run") or "")
        self.assertRegex(run.rstrip().splitlines()[-1].strip(), r"^exit [1-9]")
        self.assertLess(run.index("review_rerun.py after-death"),
                        run.rindex("exit 1"))

    def test_the_decision_and_the_activation_stay_skipped_on_a_dead_review(self):
        """Neither step may carry `always()`/`failure()`: on a failed review the
        implied `success()` is what keeps the children in Backlog."""
        for fragment in (DECISION, ACTIVATE):
            gate = str(step_named(fragment).get("if") or "")
            self.assertNotIn("always()", gate, fragment)
            self.assertNotIn("failure()", gate, fragment)

    def test_the_tombstone_names_the_run_and_reads_the_execution_file(self):
        run = str(step_named(DIED).get("run") or "")
        self.assertIn("plan_critic.py died", run)
        self.assertIn("github.run_id", run)
        self.assertIn("github.run_attempt", run)
        self.assertIn("--step posta", run)
        self.assertIn("steps.postturns.outputs.max_turns", run)
        self.assertIn("steps.posta.outputs.execution_file", run)
        self.assertIn("claude-execution-output.json", run,
                      "qa-review.yml's fallback path, for an action that moved the output")

    def test_the_tombstone_is_posted_alone_after_its_note(self):
        """Two comments, the decider's shape: the record is a credential only
        while nothing else shares its comment (`_sole_record`). Everything
        DRE-3289 added comes AFTER both of them, so a crash between the record
        and the retry still leaves an honest record (premortem Q5)."""
        run = str(step_named(DIED).get("run") or "")
        self.assertIn("--note-file", run)
        self.assertIn("--record-file", run)
        head = run.split("review_rerun.py after-death")[0]
        self.assertEqual(head.count("linear_ops.py comment"), 2,
                         "the note and the tombstone, and nothing else, "
                         "before anything decides what to do about the death")
        self.assertLess(run.index("plan_critic.py died"),
                        run.index("review_rerun.py after-death"))


class ADeadReviewRetriesItselfOnce(unittest.TestCase):
    """DRE-3289. A dead second-critic review used to leave the epic with
    nothing scheduled, and the only way to run the review again
    was a person moving it Green Light → In Progress. Now the same step that
    writes the tombstone asks `review_rerun.py` what to do about it: retry
    once at a higher ceiling, park on the second death, or leave a non-turn
    death to the medic."""

    def test_the_death_is_decided_against_the_thread_it_just_wrote(self):
        run = str(step_named(DIED).get("run") or "")
        self.assertIn("review_rerun.py after-death", run)
        self.assertIn("--with-authors", run)
        self.assertIn("--subtype", run)
        self.assertIn("--note-file", run)
        # The tombstone is on the epic before the thread is read back, or the
        # death being decided is not in it.
        self.assertLess(run.rindex("linear_ops.py comment",
                                   0, run.index("review_rerun.py after-death")),
                        run.index("dump-comments"))

    def test_a_retry_dispatches_the_review_route_and_moves_no_lane(self):
        run = str(step_named(DIED).get("run") or "")
        self.assertIn("review_rerun.py dispatch", run)
        self.assertIn(f"--reason {rr.REASON_REVIEW_RETRY} --trigger-state "
                      f"{rr.TRIGGER_STATE_REVIEW}", run)
        self.assertIn('--repo "$GITHUB_REPOSITORY"', run)
        retry = run.split('"retry"')[1].split('"park"')[0]
        self.assertNotIn("linear_ops.py state", retry,
                         "a retry never writes the epic's lane")
        self.assertNotIn("add-label", retry)

    def test_the_dispatch_runs_under_the_app_token(self):
        """Q1/Q2: `repos/.../dispatches` needs contents:write, which the App
        token holds and the stub's own token does not (`plan_run`'s docstring),
        and the run it starts initiates as the App bot — already in every
        `allowed_bots` list on the reachable workflows."""
        assert_app_token_minted_after_review(self, DIED)

    def test_a_failed_dispatch_is_said_and_never_claimed_as_started(self):
        """`plan_run.fire`'s rc rule: no receipt on an unconfirmed dispatch.

        Both receipts hang off the dispatch's EXIT STATUS, so neither sentence
        is reachable before the vendor call has answered. The first cut of this
        step posted "the review is being run again" ahead of the dispatch and
        corrected it underneath on a 403 — the retraction lands, but the false
        claim stays in the thread above it forever."""
        run = str(step_named(DIED).get("run") or "")
        retry = run.split('"retry"')[1].split('"park"')[0]
        head, _, tail = retry.partition("review_rerun.py dispatch")
        self.assertNotIn("linear_ops.py comment", head,
                         "a retry receipt written before the dispatch is attempted")
        self.assertRegex(retry, r"if\s+python3\s+\S*review_rerun\.py dispatch",
                         "the receipts must branch on the dispatch's rc")
        self.assertIn("🔁", tail)
        self.assertIn("could NOT", tail)
        self.assertLess(tail.index("🔁"), tail.index("could NOT"),
                        "the success claim belongs on the then-branch")

    def test_a_second_death_parks_for_an_operator(self):
        run = str(step_named(DIED).get("run") or "")
        park = run.split('"park"')[1]
        self.assertIn("add-label", park)
        self.assertIn("needs-human", park)
        self.assertIn('state "$EPIC" "Triage"', park)
        self.assertNotIn('state "$EPIC" "Green Light"', park)
        self.assertIn('cat "$PARK_NOTE"', park,
                      "the note after-death wrote, not a rival sentence")
        self.assertIn('--note-file "$PARK_NOTE"', run,
                      "...and that is the file after-death was told to write")

    def test_a_death_still_reaches_this_step_through_the_verdict_gate(self):
        """Re-pinned here because this card is the one that makes a dead
        review recoverable: the retry only ever fires from the tombstone step,
        and since DRE-3501 that step is reached on a failed review with no
        parseable verdict — never on a review that decided.

        The decision and the activation keep their implied `success()`, which
        is what the tombstone's own `exit 1` keeps meaning."""
        gate = str(step_named(DIED).get("if") or "")
        self.assertIn("steps.posta.outcome == 'failure'", gate)
        self.assertIn("steps.postverdict.outputs.verdict == 'NO_RESULT'", gate)
        for fragment in (DECISION, ACTIVATE):
            gate = str(step_named(fragment).get("if") or "")
            self.assertNotIn("always()", gate, fragment)
            self.assertNotIn("failure()", gate, fragment)


# --- DRE-3498: what the review actually spent, recorded per round -----------

RECEIPT = "Second critic — turns receipt"

# The step's own shell, rendered. Everything the runner would substitute, and
# nothing else: an unresolved `${{ }}` reaching bash is asserted, not ignored.
_RECEIPT_EXPRESSIONS = {
    "github.event.client_payload.identifier": "DRE-3257",
    "steps.posta.outputs.execution_file": "",
    "steps.postturns.outputs.max_turns": "48",
    "steps.postmodel.outputs.model": "claude-sonnet-5",
}


def _render(run: str, temp: str, expressions: dict) -> str:
    for expr, value in expressions.items():
        run = re.sub(r"\$\{\{\s*" + re.escape(expr) + r"\s*\}\}", value, run)
    run = re.sub(r"\$\{\{\s*runner\.temp\s*\}\}", temp, run)
    return run


class TheReviewSaysWhatItSpent(unittest.TestCase):
    """DRE-3498. The ceiling has been re-tuned three times off one archaeology
    dig each — DRE-3164's 40-turn wall, the web-tool grant, and run
    34144302622's 51 turns at a ceiling of 48. The receipt puts the number on
    the epic's own thread so the next re-tune is read, not excavated.

    It runs whenever the review ran AT ALL — a review that died at its ceiling
    is precisely the one whose spend matters — and it can never change the
    review's outcome."""

    def test_it_sits_immediately_after_the_review(self):
        self.assertEqual(index_of(RECEIPT), index_of(SECOND) + 1)
        self.assertEqual(step_named(RECEIPT).get("id"), "postreceipt")

    def test_it_runs_on_both_the_verdict_path_and_the_death_path(self):
        """`always()`-style, gated on the review route AND on `posta` having
        RUN: never on an earlier step's failure, where `posta` is `skipped`."""
        gate = str(step_named(RECEIPT).get("if") or "")
        self.assertIn("always()", gate)
        self.assertIn("mode == 'review'", gate)
        self.assertIn("steps.posta.outcome == 'success'", gate)
        self.assertIn("steps.posta.outcome == 'failure'", gate)
        self.assertNotIn("skipped", gate)

    def test_a_receipt_that_cannot_be_written_never_fails_the_review(self):
        self.assertTrue(step_named(RECEIPT).get("continue-on-error"))

    def test_it_reads_the_ceiling_the_child_count_and_the_model(self):
        run = str(step_named(RECEIPT).get("run") or "")
        self.assertIn("plan_critic.py review-turns", run)
        self.assertIn("steps.posta.outputs.execution_file", run)
        self.assertIn("claude-execution-output.json", run,
                      "the tombstone step's fallback, for an action that "
                      "moved the output")
        self.assertIn("steps.postturns.outputs.max_turns", run)
        self.assertIn("steps.postmodel.outputs.model", run)
        self.assertIn("linear_ops.py children", run,
                      "counted again here — `steps.kids` is the plan route's")
        self.assertIn("linear_ops.py comment", run)

    def test_the_posted_body_is_the_receipt_line_and_nothing_else(self):
        """Rendered and RUN, not grepped: the line is only a record while it is
        alone in its comment (`plan_critic._sole_record`)."""
        with tempfile.TemporaryDirectory() as raw:
            temp = os.path.join(raw, "temp")
            os.makedirs(temp)
            posted = os.path.join(raw, "posted.txt")
            stub = os.path.join(raw, "linear_ops.py")
            with open(stub, "w") as f:
                f.write(
                    "import sys\n"
                    "if sys.argv[1] == 'children':\n"
                    "    print(7)\n"
                    "elif sys.argv[1] == 'comment':\n"
                    f"    open({posted!r}, 'a').write(sys.argv[3])\n"
                )
            execution = os.path.join(raw, "claude-execution-output.json")
            with open(execution, "w") as f:
                json.dump([{"type": "result", "subtype": "success",
                            "is_error": False, "num_turns": 51,
                            "total_cost_usd": 2.11, "duration_ms": 421000,
                            "env": {"ANTHROPIC_API_KEY": "never-printed"}}], f)

            expressions = dict(_RECEIPT_EXPRESSIONS,
                               **{"steps.posta.outputs.execution_file": execution})
            run = _render(str(step_named(RECEIPT).get("run") or ""),
                          temp, expressions)
            self.assertNotIn("${{", run,
                             "an unresolved GitHub expression reached the shell")
            run = run.replace(".bureau-pipeline/scripts/linear_ops.py", stub)
            run = run.replace(".bureau-pipeline/scripts/plan_critic.py",
                              os.path.join(SCRIPTS, "plan_critic.py"))
            script = os.path.join(raw, "receipt.sh")
            with open(script, "w") as f:
                f.write("set -e\n" + run)
            proc = subprocess.run(["bash", script], capture_output=True,
                                  text=True, cwd=raw)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            body = open(posted).read().strip()

        self.assertEqual(
            body,
            "🧮 review-turns: spent=51 ceiling=48 children=7 "
            "model=claude-sonnet-5")
        self.assertEqual(len(pc.parse_review_turns([
            {"body": body, "authored_by_pipeline": True}])), 1)

    def test_the_registry_declares_the_receipt_as_a_fact_not_an_act(self):
        """A line about a CALL creates no obligation and hands the work to
        nobody, so it carries no act trailer — and `check_act_receipts.py`
        stays green only because the `unconverted` block says so."""
        rows = [r for r in car.declarations()
                if r.get("step") == RECEIPT
                and r.get("file") == ".github/workflows/plan.yml"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["kind"], "not-an-act")
        self.assertTrue(rows[0]["why"].strip())
        self.assertEqual(car.problems(), [])


class EveryNoticeThatAsksForAReRunNamesTheAct(unittest.TestCase):
    """The relay has two triggers and the notices name them (DRE-3292).

    A transition INTO In Progress activates an epic, and a comment whose whole
    body is `review_rerun.RERUN_REVIEW_ACT` asks for the review directly. Every
    notice on this rail that asks for a re-run says it in the exact words
    `plan_critic.REAPPROVE_HOW` uses — so the rail and the sweep cannot drift —
    and none of them asks for a lane move the epic cannot make."""

    def test_the_sent_back_notices_ask_only_for_the_move_that_is_left(self):
        """Read off the review-mode notices since DRE-5281 deleted the
        activate route's three-way send-back.

        The BOUND park asks for the way back in `REAPPROVE_HOW`'s words, read
        through the module at run time (never a literal): the epic is parked,
        and clearing needs-human then moving it to Planning is a person's job.
        The re-review asks for nothing at all, because nothing there is
        anyone's to decide — the CEO has not seen the plan yet."""
        bound = str(step_named(BOUND_PARK).get("run") or "")
        self.assertIn("plan_critic.REAPPROVE_HOW", bound)
        self.assertNotIn(pc.REAPPROVE_HOW, bound)
        # DRE-5280: the way back from a park is the move to Planning, not the
        # act — the relay ignores the act on an epic that is not In Progress.
        self.assertIn(pc.REVIEW_LANE, pc.REAPPROVE_HOW)
        self.assertNotIn(rr.RERUN_REVIEW_ACT, bound)

        same = str(step_named(RE_REVIEW).get("run") or "")
        self.assertNotIn("REAPPROVE_HOW", same)
        self.assertNotIn(pc.APPROVAL_LANE, same,
                         "a revision asks the CEO for no move at all")
        self.assertNotIn("Approve", same)

        for run in (bound, same):
            self.assertNotIn("by moving the epic to In Progress", run)
            self.assertNotIn("Todo", run)

    def test_the_tombstone_note_comes_from_the_module_that_owns_the_sentence(self):
        """The dead-review note is generated by `plan_critic.py died` and the
        park note by `review_rerun.py after-death` — pinned in
        test_plan_critic.py and test_review_rerun.py; here only that the rail
        hand-writes no rival sentence, and asks for no approval move of its own
        (DRE-3289: the review re-runs itself, so there is nothing to approve)."""
        run = str(step_named(DIED).get("run") or "")
        self.assertNotIn("In Progress", run)
        self.assertNotIn(pc.REAPPROVE_HOW, run)

    def test_the_standard_names_the_act_and_both_relay_triggers(self):
        text = open(STANDARD).read()
        self.assertIn(rr.RERUN_REVIEW_ACT, text, "the act itself")
        self.assertIn("INTO In Progress", text, "the other trigger")
        self.assertNotIn(RETIRED_MOVE, text)
        self.assertNotIn("re-approval\n  by moving it to **In Progress**", text)

    def test_no_file_in_the_repo_still_asks_for_the_two_lane_move(self):
        """The sweep the card asked for, as a test rather than a one-off grep:
        the retired sentence is retired everywhere, or the next notice someone
        writes is copied from the copy that was left behind.

        `.git` is history and `.bureau-pipeline` is a checkout of another ref
        of this same repo that the run assembles its context from — neither is
        a file this repo ships."""
        hits = []
        for base, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs
                       if d not in (".git", ".bureau-pipeline", "__pycache__")]
            for name in names:
                if not name.endswith((".py", ".md", ".yml")):
                    continue
                path = os.path.join(base, name)
                try:
                    with open(path, encoding="utf-8") as f:
                        if RETIRED_MOVE in f.read():
                            hits.append(os.path.relpath(path, ROOT))
                except (OSError, UnicodeDecodeError):
                    continue
        self.assertEqual(hits, [], f"the retired wording still lives in {hits}")


# --- DRE-3501: the result file decides, not the step outcome ----------------

VERDICT = "Second critic — verdict or death?"


class TheResultFileDecidesNotTheStepOutcome(unittest.TestCase):
    """agent-bureau run 34144302622: the review FINISHED — `subtype: success`,
    turn 51 of a 48-turn ceiling — and the action marked its step failed. The
    rail read the step outcome, skipped the decision, and buried a plan the
    critic had already passed under a tombstone reading *died (success)*.

    So the rail now reads the result file. `posta` is `continue-on-error`, one
    step says which of the three verdicts is in the file, and the tombstone and
    the decision are gated on THAT."""

    def test_the_verdict_step_follows_the_turns_receipt(self):
        self.assertEqual(index_of(VERDICT), index_of(RECEIPT) + 1)
        self.assertEqual(step_named(VERDICT).get("id"), "postverdict")
        self.assertLess(index_of(VERDICT), index_of(DIED))
        self.assertLess(index_of(VERDICT), index_of(DECISION))

    def test_it_runs_on_both_the_verdict_path_and_the_death_path(self):
        gate = str(step_named(VERDICT).get("if") or "")
        self.assertIn("always()", gate)
        self.assertIn("mode == 'review'", gate)
        self.assertIn("steps.posta.outcome == 'success'", gate)
        self.assertIn("steps.posta.outcome == 'failure'", gate)

    def test_it_reads_the_file_the_critic_was_told_to_write(self):
        """The same path the prompt names and the decision reads — a second
        spelling of it would answer about a file nobody wrote."""
        run = str(step_named(VERDICT).get("run") or "")
        self.assertIn("plan_critic.py read-result", run)
        self.assertIn("plan-critic-post.md", run)
        self.assertIn("plan-critic-post.md", prompt_of(SECOND))
        self.assertIn("plan-critic-post.md",
                      str(step_named(DECISION).get("run") or ""))

    def test_the_review_step_is_continue_on_error(self):
        self.assertTrue(step_named(SECOND).get("continue-on-error"))

    def test_the_tombstone_needs_a_failed_review_AND_no_verdict(self):
        gate = str(step_named(DIED).get("if") or "")
        self.assertIn("steps.posta.outcome == 'failure'", gate)
        self.assertIn("steps.postverdict.outputs.verdict == 'NO_RESULT'", gate)

    def test_the_tombstone_step_ends_by_failing_the_job(self):
        """The review no longer reddens the job, so the death does — and after
        the dispatch, so the retry still fires and the medic still sees a red
        run."""
        run = str(step_named(DIED).get("run") or "")
        self.assertRegex(run.rstrip().splitlines()[-1].strip(), r"^exit [1-9]")

    def test_the_decision_admits_a_parsed_verdict_on_a_failed_review(self):
        """...and still admits a successful review, whatever the file says —
        a review that ran to its decision and wrote nothing usable is a round
        with NO_RESULT, exactly as before (console-honesty rule 1)."""
        gate = str(step_named(DECISION).get("if") or "")
        self.assertIn("steps.posta.outcome == 'success'", gate)
        self.assertIn("steps.postverdict.outputs.verdict", gate)
        self.assertIn("NO_RESULT", gate)
        self.assertNotIn("always()", gate)
        self.assertNotIn("failure()", gate)

    def test_the_comment_above_the_review_states_the_new_gate(self):
        """A change that contradicts a document updates it, and the nearest
        document to a step is the comment over it (standards/engineering.md).
        It used to say `NO continue-on-error here, on purpose`."""
        src = wf_src()
        head = src[:src.index("id: posta\n")]
        above = head[:head.rindex("      - name: Second critic — review")]
        comment = above[above.rindex("\n\n"):]
        self.assertNotIn("NO `continue-on-error` here", comment)
        self.assertIn("postverdict", comment)
        self.assertIn("DRE-3241", comment, "the trap it still closes")

    def test_the_step_answers_each_of_the_three_verdicts(self):
        """Rendered and RUN, not grepped: the step's own shell, against a
        result file, three times — the PASS run 34144302622 wrote before it
        was cut off, a send-back, and no file at all."""
        run_block = str(step_named(VERDICT).get("run") or "")
        cases = [
            ("PLAN-CRITIC: PASS\n\n1. DRE-3258: nothing blocking\n", "verdict=PASS"),
            ("PLAN-CRITIC: SEND_BACK — DRE-3259 has no operator step\n",
             "verdict=SEND_BACK"),
            (None, "verdict=NO_RESULT"),
        ]
        for text, expected in cases:
            with self.subTest(expected=expected), \
                    tempfile.TemporaryDirectory() as raw:
                temp = os.path.join(raw, "temp")
                os.makedirs(temp)
                if text is not None:
                    with open(os.path.join(temp, "plan-critic-post.md"), "w") as f:
                        f.write(text)
                rendered = _render(run_block, temp, {})
                self.assertNotIn("${{", rendered,
                                 "an unresolved GitHub expression reached the shell")
                rendered = rendered.replace(
                    ".bureau-pipeline/scripts/plan_critic.py",
                    os.path.join(SCRIPTS, "plan_critic.py"))
                gho = os.path.join(raw, "gho")
                script = os.path.join(raw, "verdict.sh")
                with open(script, "w") as f:
                    f.write("set -e\n" + rendered)
                proc = subprocess.run(["bash", script], capture_output=True,
                                      text=True, cwd=raw,
                                      env=dict(os.environ, GITHUB_OUTPUT=gho))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(open(gho).read(), expected + "\n")


# --- DRE-5280: the second critic reads the plan before Green Light ----------
#
# A `review` route of its own. The second critic's steps ran in review mode
# AND in activate mode until DRE-5281 narrowed them to review alone, and review
# mode has six outcomes of its own, each gated on the decision's own outputs —
# never on `action == 'proceed'` alone, because `proceed` covers a critic that
# never decided.

REVIEW_MODE = "steps.route.outputs.mode == 'review'"
ACTIVATE_MODE = "steps.route.outputs.mode == 'activate'"
SLOT_CLAUSE = "steps.slot.outputs.admitted == 'true'"

GREEN_LIGHT_BOTH = "Epic → Green Light — both critics passed"
REVIEW_HELD_PASS = "Review — the second critic passed a plan the first critic held"
REVIEW_NO_RESULT = "Review — the second critic produced no result"
REVIEW_DIED = "Review — the review died"
REVIEW_RE_REVIEW = "Review — the revised plan is reviewed again"
REVIEW_BOUND = "Review — the bound parks the plan in Triage"
REVIEW_OUTCOMES = (GREEN_LIGHT_BOTH, REVIEW_HELD_PASS, REVIEW_NO_RESULT,
                   REVIEW_DIED, REVIEW_RE_REVIEW, REVIEW_BOUND)

# The second critic's own steps: `Select model — second critic` through
# `Second critic — decision`, the re-plan with its turn ceiling and capacity
# retry, the mechanical findings after it, and the mint the re-review dispatch
# spends. DRE-5280 ran them in both modes; DRE-5281 narrowed them to review.
SHARED_STEPS = (
    "Select model — second critic",
    "Second critic — cross-epic sight",
    "Second critic — context",
    "Second critic — turn ceiling",
    "Second critic — the previous round",
    "Re-mint bot token — second critic",
    "Second critic — review (before Green Light)",
    "Second critic — turns receipt",
    "Second critic — verdict or death?",
    "Re-mint bot token — after the second critic",
    "Second critic — decision",
    "Turn ceiling — second critic's re-plan",
    "Re-plan after the second critic sent it back",
    "Re-plan after the second critic sent it back — out of capacity?",
    "Re-mint bot token — Re-plan after the second critic sent it back on the next rung",
    "Re-plan after the second critic sent it back — on the next rung",
    "Re-plan after the second critic sent it back — finished?",
    "Mechanical findings — the revised plan (review)",
    "Re-mint bot token — send-back",
)

# The activate route as DRE-5281 leaves it: a fresh token for the hand-back's
# dispatch, then the activation that reads the second critic's state.
ACTIVATE_MINT = "Re-mint bot token — activate"
ACTIVATE_ONLY_STEPS = (ACTIVATE_MINT, "Activate the approved epic")

# The step ids the wiring tests and the act registry key on.
CARD_STEP_IDS = ("route", "post1", "postmodel", "sight", "postturns", "posta",
                 "postverdict", "postreplan", "postreplan_retry")

GREEN_LIGHT_WRITE = re.compile(r'linear_ops\.py state "\$EPIC" "Green Light"')
TRIAGE_WRITE = re.compile(r'linear_ops\.py state "\$EPIC" "Triage"')


def exact_step(name: str) -> dict:
    """The step called exactly `name` — `step_named` takes the FIRST step whose
    name merely contains a fragment, and the review steps share words with
    the activate ones above them."""
    found = [s for s in steps() if s.get("name") == name]
    if len(found) != 1:
        raise AssertionError(f"{len(found)} steps named exactly {name!r}")
    return found[0]


def exact_index(name: str) -> int:
    return next(i for i, s in enumerate(steps()) if s.get("name") == name)


def gate_of(s: dict) -> str:
    return str(s.get("if") or "")


def run_of(s: dict) -> str:
    return str(s.get("run") or "")


def runs_in_review(s: dict) -> bool:
    return REVIEW_MODE in gate_of(s)


def review_only(s: dict) -> bool:
    return runs_in_review(s) and ACTIVATE_MODE not in gate_of(s)


def token_mint_index(name: str) -> int:
    env = exact_step(name).get("env") or {}
    ids = re.findall(r"^\$\{\{ steps\.([A-Za-z0-9_-]+)\.outputs\.token \}\}$",
                     str(env.get("GH_TOKEN") or ""))
    assert len(ids) == 1, env.get("GH_TOKEN")
    return next(i for i, s in enumerate(steps()) if s.get("id") == ids[0])


class TheReviewRouteRunsTheSecondCriticBeforeGreenLight(unittest.TestCase):
    """DRE-5280, read off the rail."""

    def test_the_route_step_reads_the_reason_words_review_rerun_owns(self):
        run = run_of(exact_step(ROUTE))
        for word in (rr.REASON_REVIEW, rr.REASON_RE_REVIEW,
                     rr.REASON_REVIEW_RETRY, rr.REASON_RERUN_ACT):
            self.assertIn(word, run)
        self.assertIn(rr.TRIGGER_STATE_ACTIVATE, run)
        for mode in ("plan", "review", "activate"):
            self.assertIn(f'echo "mode={mode}" >> "$GITHUB_OUTPUT"', run)
        # Review mode moves the epic to Planning itself, as a literal the
        # writer check reads, and opens an attempt only on `activate-cycle`.
        self.assertGreaterEqual(run.count('state "$EPIC" "Planning"'), 1)
        self.assertIn("activate-cycle", run)
        self.assertIn(SLOT_CLAUSE, gate_of(exact_step(ROUTE)))

    def test_every_step_name_and_id_this_card_found_still_exists(self):
        names = {s.get("name") for s in steps()}
        for name in SHARED_STEPS + ACTIVATE_ONLY_STEPS + (ROUTE,):
            self.assertIn(name, names)
        ids = {s.get("id") for s in steps()}
        for ident in CARD_STEP_IDS:
            self.assertIn(ident, ids)

    def test_the_second_critics_steps_run_in_review_mode_alone(self):
        """DRE-5281: `mode == 'review'` alone — the activate route runs no
        review, and no step of the second critic's runs on it."""
        for name in SHARED_STEPS:
            with self.subTest(step=name):
                gate = gate_of(exact_step(name))
                self.assertIn(REVIEW_MODE, gate)
                self.assertNotIn(ACTIVATE_MODE, gate)

    def test_the_verdict_steps_and_the_decision_share_one_mode_predicate(self):
        """The DRE-3241 trap, guarded in the new mode: were the decision to run
        in review mode while `verdict or death?` did not, a failed review would
        leave `verdict` empty, `'' != 'NO_RESULT'` would admit the decision,
        and an empty result file would be decided as a round."""
        for name in ("Second critic — turns receipt",
                     "Second critic — verdict or death?",
                     "Second critic — decision"):
            with self.subTest(step=name):
                gate = gate_of(exact_step(name))
                self.assertIn(REVIEW_MODE, gate)
                self.assertNotIn(ACTIVATE_MODE, gate)

    def test_the_activate_route_is_two_steps_and_never_runs_in_review_mode(self):
        on_activate = [s.get("name") for s in steps() if ACTIVATE_MODE in gate_of(s)]
        self.assertEqual(on_activate, list(ACTIVATE_ONLY_STEPS))
        for name in ACTIVATE_ONLY_STEPS:
            with self.subTest(step=name):
                self.assertEqual(gate_of(exact_step(name)).strip(), ACTIVATE_MODE)

    def test_the_six_outcomes_are_review_only(self):
        for name in REVIEW_OUTCOMES:
            with self.subTest(step=name):
                self.assertTrue(review_only(exact_step(name)), gate_of(exact_step(name)))

    def test_no_outcome_is_gated_on_proceed_alone(self):
        for name in REVIEW_OUTCOMES:
            with self.subTest(step=name):
                self.assertNotIn("action == 'proceed'", gate_of(exact_step(name)))

    def test_the_only_green_light_write_in_review_mode_needs_both_critics(self):
        writers = [s.get("name") for s in steps()
                   if runs_in_review(s) and GREEN_LIGHT_WRITE.search(run_of(s))]
        self.assertEqual(writers, [GREEN_LIGHT_BOTH])
        gate = gate_of(exact_step(GREEN_LIGHT_BOTH))
        self.assertIn("steps.post1.outputs.result == 'PASS'", gate)
        self.assertIn("steps.post1.outputs.pre_passed == 'true'", gate)

    def test_the_green_light_step_stamps_first_and_quotes_the_first_critics_word(self):
        run = run_of(exact_step(GREEN_LIGHT_BOTH))
        self.assertIn("plan_child_verdicts.py stamp", run)
        self.assertLess(run.index("plan_child_verdicts.py stamp"),
                        GREEN_LIGHT_WRITE.search(run).start())
        self.assertIn("parse_markers", run,
                      "the first critic's word is read off its own record")
        self.assertIn("Approve", run)

    def test_the_only_green_light_step_is_the_one_both_critics_reach(self):
        """DRE-5284 retired the plan route's `Epic → Green Light`, so a lookup
        by that substring now finds exactly one step — this one."""
        named = [s.get("name") for s in steps()
                 if "Epic → Green Light" in (s.get("name") or "")]
        self.assertEqual(named, [GREEN_LIGHT_BOTH])

    def test_a_pass_the_first_critic_held_reaches_exactly_one_step(self):
        reached = [s.get("name") for s in steps()
                   if runs_in_review(s)
                   and "steps.post1.outputs.result == 'PASS'" in gate_of(s)
                   and "steps.post1.outputs.pre_passed != 'true'" in gate_of(s)]
        self.assertEqual(reached, [REVIEW_HELD_PASS])
        run = run_of(exact_step(REVIEW_HELD_PASS))
        self.assertIn("add-label", run)
        self.assertIn("needs-human", run)
        self.assertRegex(run, TRIAGE_WRITE)
        self.assertNotRegex(run, GREEN_LIGHT_WRITE)

    def test_no_result_reaches_one_outcome_that_asks_again_or_parks(self):
        reached = [s.get("name") for s in steps()
                   if runs_in_review(s)
                   and "steps.post1.outputs.result == 'NO_RESULT'" in gate_of(s)]
        self.assertEqual(reached, [REVIEW_NO_RESULT])
        run = run_of(exact_step(REVIEW_NO_RESULT))
        self.assertIn(f"--reason {rr.REASON_REVIEW} --trigger-state "
                      f"{rr.TRIGGER_STATE_REVIEW}", run)
        self.assertIn("plan_critic.MAX_ROUNDS", run, "read, never restated")
        self.assertIn("NO_RESULTS", run)
        self.assertIn("needs-human", run)
        self.assertRegex(run, TRIAGE_WRITE)
        self.assertNotRegex(run, GREEN_LIGHT_WRITE)
        self.assertGreater(token_mint_index(REVIEW_NO_RESULT),
                           exact_index("Second critic — review (before Green Light)"))

    def test_a_send_back_below_the_bound_re_plans_then_dispatches_a_re_review(self):
        # DRE-5299 put the plan artifact on this path: the portal mint, the
        # checkout and the published-source read before the re-plan, the
        # re-check and the upload after it — and the re-review, last, only
        # once the re-check has passed the revision.
        reached = [s.get("name") for s in steps()
                   if review_only(s)
                   and "steps.post1.outputs.action == 'hold'" in gate_of(s)
                   and "steps.post1.outputs.bound != 'true'" in gate_of(s)]
        self.assertEqual(reached, [PORTAL_MINT, PORTAL_CHECKOUT, PUBLISHED_SOURCE,
                                   REVIEW_RECHECK, REVIEW_UPLOAD, REVIEW_RE_REVIEW])
        self.assertIn(RECHECK_OK, gate_of(exact_step(REVIEW_RE_REVIEW)))
        run = run_of(exact_step(REVIEW_RE_REVIEW))
        self.assertIn(f"--reason {rr.REASON_RE_REVIEW} --trigger-state "
                      f"{rr.TRIGGER_STATE_REVIEW}", run)
        self.assertNotIn("linear_ops.py state", run, "a re-review writes no lane")
        self.assertNotIn("add-label", run)
        last_replan = exact_index(
            "Re-plan after the second critic sent it back — on the next rung")
        self.assertGreater(exact_index(REVIEW_RE_REVIEW), last_replan)
        self.assertGreater(token_mint_index(REVIEW_RE_REVIEW), last_replan)

    def test_a_re_review_receipt_hangs_off_the_dispatch(self):
        run = run_of(exact_step(REVIEW_RE_REVIEW))
        head, _, tail = run.partition("review_rerun.py dispatch")
        self.assertNotIn("linear_ops.py comment", head)
        self.assertRegex(run, r"if\s+python3\s+\S*review_rerun\.py dispatch")
        self.assertIn("🔁", tail)
        self.assertIn("could NOT", tail)
        self.assertIn("exit 1", tail)

    def test_the_bound_and_the_second_death_park_in_triage(self):
        bound = exact_step(REVIEW_BOUND)
        self.assertIn("steps.post1.outputs.bound == 'true'", gate_of(bound))
        died = exact_step(REVIEW_DIED)
        self.assertIn("steps.posta.outcome == 'failure'", gate_of(died))
        self.assertIn("steps.postverdict.outputs.verdict == 'NO_RESULT'", gate_of(died))
        for s in (bound, died):
            with self.subTest(step=s.get("name")):
                run = run_of(s)
                self.assertIn("add-label", run)
                self.assertIn("needs-human", run)
                self.assertRegex(run, TRIAGE_WRITE)
                self.assertNotRegex(run, GREEN_LIGHT_WRITE)
        run = run_of(died)
        self.assertIn(f"--reason {rr.REASON_REVIEW_RETRY} --trigger-state "
                      f"{rr.TRIGGER_STATE_REVIEW}", run)
        self.assertIn("review_rerun.py after-death", run)
        self.assertIn("plan_critic.py died", run)
        self.assertTrue(run.rstrip().endswith("exit 1"))
        self.assertGreater(token_mint_index(REVIEW_DIED),
                           exact_index("Second critic — review (before Green Light)"))

    def test_no_review_only_step_but_one_writes_green_light(self):
        for s in steps():
            if review_only(s) and s.get("name") != GREEN_LIGHT_BOTH:
                with self.subTest(step=s.get("name")):
                    self.assertNotRegex(run_of(s), GREEN_LIGHT_WRITE)

    def test_every_triage_park_in_review_mode_labels_before_it_moves(self):
        """The relay dispatches a plan run the moment an `agent:planner` card
        enters Triage, and the plan-gate refuses it only when the card already
        carries `needs-human` (scripts/rereview_watch.py)."""
        parks = [s for s in steps() if review_only(s) and TRIAGE_WRITE.search(run_of(s))]
        self.assertEqual(sorted(s.get("name") for s in parks),
                         sorted((REVIEW_HELD_PASS, REVIEW_NO_RESULT,
                                 REVIEW_DIED, REVIEW_BOUND, REVIEW_RECHECK)))
        for s in parks:
            with self.subTest(step=s.get("name")):
                run = run_of(s)
                self.assertLess(run.index("add-label"), TRIAGE_WRITE.search(run).start())

    def test_the_re_run_sentence_is_read_from_its_module_never_a_literal(self):
        for s in steps():
            if review_only(s):
                with self.subTest(step=s.get("name")):
                    self.assertNotIn(pc.REAPPROVE_HOW, run_of(s))
        for name in (REVIEW_NO_RESULT, REVIEW_BOUND):
            with self.subTest(step=name):
                self.assertIn("plan_critic.REAPPROVE_HOW", run_of(exact_step(name)))
        # DRE-5639: the held park's whole note is the module's
        # (`held_park_note`, which ends in `reapprove_how()`), so the sentence
        # is read through `plan_critic.py held-park` rather than the constant.
        self.assertIn("plan_critic.py held-park", run_of(exact_step(REVIEW_HELD_PASS)))

    def test_the_sight_step_reads_the_sight_states(self):
        run = run_of(exact_step("Second critic — cross-epic sight"))
        self.assertIn("linear_ops.py epics-in-flight --sight", run)
        self.assertIn('plan_critic.py sight --this "$EPIC" --sight', run)

    def test_every_review_mode_model_step_waits_for_its_planner_slot(self):
        models = [s for s in steps()
                  if runs_in_review(s) and str(s.get("uses") or "").split("@")[0] == ACTION]
        self.assertEqual(
            sorted(s.get("id") for s in models),
            ["posta", "postreplan", "postreplan_retry"])
        for s in models:
            with self.subTest(step=s.get("name")):
                self.assertIn(SLOT_CLAUSE, gate_of(s))

    def test_every_review_mode_comment_is_declared_by_its_step(self):
        sites = [site for site in car.sites()
                 if site.path == ".github/workflows/plan.yml"
                 and site.step in REVIEW_OUTCOMES]
        self.assertTrue(sites)
        declared = car.declarations()
        for site in sites:
            with self.subTest(site=site.where):
                hits = [d for d in declared if car._matches(d, site)]
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0].get("step"), site.step)
                self.assertTrue((hits[0].get("why") or "").strip())


# --- DRE-5281: Approve means go ---------------------------------------------
#
# The activate route runs no review. `Activate the approved epic` reads the
# second critic's state off the epic's thread (`plan_critic.py post-state`):
# on POST_RELEASED it activates exactly as before; on anything else it writes
# no lane and asks for the review the plan missed, from In Progress, and the
# review run moves the epic to Planning itself. Read off the rail.

ANY_LANE_WRITE = re.compile(r'linear_ops\.py state "\$EPIC" "([^"]+)"')
QUEUED_LABEL = re.compile(r'linear_ops\.py add-label "\$EPIC" epic-queued')
HAND_BACK = (f"--reason {rr.REASON_REVIEW} "
             f'--trigger-state "{rr.TRIGGER_STATE_ACTIVATE}"')


def activate_branches() -> tuple[str, str]:
    """`(released, handed_back)` — the two bodies of the activation's one
    branch, split on the shell's own `else`, so each can be read alone."""
    run = run_of(exact_step(ACTIVATE))
    head, sep, rest = run.partition('if [ "$POST" = "$RELEASED" ]; then')
    assert sep, "the activation does not branch on the module's released word"
    depth, lines, released, handed = 0, rest.splitlines(), [], []
    target = released
    for line in lines:
        word = line.strip()
        if word.startswith("if ") or word.startswith("if\t"):
            depth += 1
        elif word == "fi":
            if depth == 0:
                break
            depth -= 1
        elif word == "else" and depth == 0:
            target = handed
            continue
        target.append(line)
    return "\n".join(released), "\n".join(handed)


def route_activate_branch() -> str:
    """The route step's activate branch: between `mode=activate` and the
    plan route's `else`."""
    run = run_of(exact_step(ROUTE))
    start = run.index('echo "mode=activate"')
    return run[start:run.index('echo "mode=plan"', start)]


class ApproveMeansGo(unittest.TestCase):

    def test_no_activate_mode_step_runs_a_model(self):
        models = [s.get("name") for s in steps()
                  if ACTIVATE_MODE in gate_of(s)
                  and str(s.get("uses") or "").split("@")[0] == ACTION]
        self.assertEqual(models, [])

    def test_no_activate_mode_step_writes_green_light(self):
        for s in steps():
            if ACTIVATE_MODE in gate_of(s):
                with self.subTest(step=s.get("name")):
                    self.assertNotRegex(run_of(s), GREEN_LIGHT_WRITE)

    def test_the_route_step_writes_green_light_only_after_queueing_the_epic(self):
        """The one form a neighbor may add here (DRE-5136, after this card):
        a Green Light write in the route step that the same shell labels
        `epic-queued` before. Any other Green Light write is refused, and as
        this card leaves `main` there is none at all."""
        run = run_of(exact_step(ROUTE))
        writes = [m.start() for m in GREEN_LIGHT_WRITE.finditer(run)]
        for at in writes:
            with self.subTest(at=at):
                queued = [m.start() for m in QUEUED_LABEL.finditer(run[:at])]
                self.assertTrue(queued, "a Green Light write with no epic-queued label before it")
        self.assertNotRegex(route_activate_branch(), GREEN_LIGHT_WRITE)

    def test_the_only_activate_mode_lane_write_is_in_progress_on_released(self):
        writes = [(s.get("name"), lane) for s in steps() if ACTIVATE_MODE in gate_of(s)
                  for lane in ANY_LANE_WRITE.findall(run_of(s))]
        self.assertEqual(writes, [(ACTIVATE, pc.APPROVAL_LANE)])
        released, handed = activate_branches()
        self.assertEqual(ANY_LANE_WRITE.findall(released), [pc.APPROVAL_LANE])
        self.assertEqual(ANY_LANE_WRITE.findall(handed), [])
        self.assertNotRegex(route_activate_branch(), ANY_LANE_WRITE)

    def test_no_activate_mode_step_writes_planning(self):
        for s in steps():
            if ACTIVATE_MODE in gate_of(s):
                with self.subTest(step=s.get("name")):
                    self.assertNotRegex(run_of(s), PLANNING_WRITE)
        self.assertNotRegex(route_activate_branch(), PLANNING_WRITE)

    def test_the_branch_is_the_promoters_own_word(self):
        run = run_of(exact_step(ACTIVATE))
        self.assertIn("dump-comments", run)
        self.assertIn("--with-authors", run)
        self.assertIn('plan_critic.py post-state --epic "$EPIC"', run)
        self.assertIn("plan_critic.POST_RELEASED", run)
        self.assertLess(run.index("post-state"), run.index('if [ "$POST" = "$RELEASED" ]'))

    def test_released_is_todays_activation(self):
        released, _handed = activate_branches()
        order = ["plan_child_verdicts.py stamp", 'state "$EPIC" "In Progress"',
                 "▶️ Epic activated", "reconcile.py --promote-only"]
        at = [released.index(cmd) for cmd in order]
        self.assertEqual(at, sorted(at))
        self.assertNotIn("review_rerun.py", released)

    def test_anything_else_dispatches_the_review_and_writes_no_lane(self):
        _released, handed = activate_branches()
        self.assertIn("review_rerun.py dispatch", handed)
        self.assertIn(HAND_BACK, handed)
        self.assertIn('--repo "$GITHUB_REPOSITORY"', handed)
        self.assertNotIn("linear_ops.py state", handed)
        self.assertNotIn("add-label", handed)
        self.assertNotIn("--promote-only", handed)
        self.assertNotIn("plan_child_verdicts.py", handed)

    def test_the_hand_back_note_hangs_off_the_dispatch(self):
        """DRE-2034: no receipt on an unconfirmed dispatch."""
        _released, handed = activate_branches()
        head, _, tail = handed.partition("review_rerun.py dispatch")
        self.assertNotIn("linear_ops.py comment", head)
        self.assertRegex(handed, r"if\s+python3\s+\S*review_rerun\.py dispatch")
        self.assertIn("↩️", tail)
        self.assertIn("could NOT", tail)
        self.assertLess(tail.index("↩️"), tail.index("could NOT"))
        self.assertIn("exit 1", tail)

    def test_the_hand_back_runs_under_a_token_minted_on_the_activate_route(self):
        at = token_mint_index(ACTIVATE)
        self.assertEqual(steps()[at].get("name"), ACTIVATE_MINT)
        self.assertEqual(at, exact_index(ACTIVATE) - 1)
        self.assertEqual(gate_of(steps()[at]).strip(), ACTIVATE_MODE)

    def test_the_hand_back_reaches_the_review_route(self):
        """The dispatch's payload is a row of the route table that reads
        `review` — In Progress plus the review reason — so the run it starts
        moves the epic to Planning itself."""
        run = run_of(exact_step(ROUTE))
        self.assertIn('if [ "$FROM" = "in progress" ]; then', run)
        self.assertIn('if [ "$REASON" = "review" ]; then REVIEW=true; fi', run)
        self.assertEqual(rr.TRIGGER_STATE_ACTIVATE, "in progress")
        self.assertEqual(rr.REASON_REVIEW, "review")

    def test_review_mode_steps_are_gated_on_review_alone(self):
        for s in steps():
            if REVIEW_MODE in gate_of(s):
                with self.subTest(step=s.get("name")):
                    self.assertNotIn(ACTIVATE_MODE, gate_of(s))

    def test_the_activate_routes_review_steps_are_gone(self):
        names = {s.get("name") for s in steps()}
        ids = {s.get("id") for s in steps()}
        for name in RETIRED_STEPS:
            with self.subTest(step=name):
                self.assertNotIn(name, names)
        self.assertNotIn("cardset", ids)

    def test_no_prompt_says_the_ceo_approved_the_plan_being_reviewed(self):
        for s in steps():
            prompt = str((s.get("with") or {}).get("prompt") or "")
            with self.subTest(step=s.get("name")):
                self.assertNotRegex(prompt, r"(?i)CEO (has )?approved")
                self.assertNotIn("post-approval", prompt.lower())
                self.assertNotIn("the normal one", prompt,
                                 "review is the only route a re-plan runs on now")

    def test_the_activations_comments_are_declared_by_its_step(self):
        sites = [site for site in car.sites()
                 if site.path == ".github/workflows/plan.yml" and site.step == ACTIVATE]
        self.assertGreaterEqual(len(sites), 4)
        declared = car.declarations()
        for site in sites:
            with self.subTest(site=site.where):
                hits = [d for d in declared if car._matches(d, site)]
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0].get("step"), ACTIVATE)
                self.assertTrue((hits[0].get("why") or "").strip())

    def test_the_standard_carries_no_landing_paragraph(self):
        self.assertNotIn("**Landing**", open(STANDARD).read())

    def test_the_post_approval_order_is_gone_from_the_rail(self):
        """The card's sweep, as a test: the words for the old order survive
        only where they are history."""
        swept = [os.path.join(ROOT, ".github", "workflows", "plan.yml"),
                 os.path.join(ROOT, "standards", "plan-critic.md"),
                 os.path.join(ROOT, "config", "lane-contract.json"),
                 os.path.join(ROOT, "docs", "lane-contract.md"),
                 os.path.join(ROOT, "agents.yaml")]
        swept += [os.path.join(ROOT, "scripts", n)
                  for n in sorted(os.listdir(os.path.join(ROOT, "scripts")))
                  if n.endswith(".py")]
        briefs = os.path.join(ROOT, "briefs")
        swept += [os.path.join(briefs, n) for n in sorted(os.listdir(briefs))]
        hits = []
        for path in swept:
            if not os.path.isfile(path):
                continue
            lines = open(path, encoding="utf-8").read().splitlines()
            for n, line in enumerate(lines):
                if "post-approval" not in line.lower():
                    continue
                # The one dated history note the card keeps: dead_run.py's
                # DRE-3499 comment, which names the epic that retired the order.
                if (path.endswith(os.path.join("scripts", "dead_run.py"))
                        and "DRE-5268" in " ".join(lines[n:n + 3])):
                    continue
                hits.append(f"{os.path.relpath(path, ROOT)}:{n + 1}")
        self.assertEqual(hits, [])
        acts = json.load(open(os.path.join(ROOT, "config", "pipeline-acts.json")))
        rows = acts["unconverted"] + list(acts.get("acts") or [])
        loud = [r.get("anchor") or r.get("means") for r in rows if isinstance(r, dict)
                and any("post-approval" in str(r.get(k) or "").lower()
                        for k in ("means", "anchor"))]
        self.assertEqual(loud, [])


# --- DRE-5299: the review-mode re-plan carries the plan artifact -------------
#
# The re-plan inherited from the activate route lived in the cards alone. A
# review run cannot reach the plan run's upload (an artifact download reads
# the current run only), so the previous text comes from the one place a
# review run CAN read it — the portal, where `plan_artifact.publish` keeps the
# source beside the page — or, with no portal, the artifact is written fresh.
# Then the revision goes through the plan route's own gates, is uploaded, and
# the `publish` job publishes it from this same run.

REVIEW_REPLAN = (f"{REVIEW_MODE} && steps.post1.outputs.action == 'hold' && "
                 "steps.post1.outputs.bound != 'true'")
PORTAL_MINT = "Mint bot token — portal checkout (review)"
PORTAL_CHECKOUT = "Plan artifact — portal checkout (review)"
PUBLISHED_SOURCE = "Plan artifact — published source"
REVIEW_RECHECK = "Re-check the revised plan — review mode"
REVIEW_UPLOAD = "Plan artifact — upload source (review)"
RECHECK_OK = "steps.recheck_review.outputs.ok == 'true'"
POST_REPLAN = "Re-plan after the second critic sent it back"
POST_REPLAN_DONE = "Re-plan after the second critic sent it back — finished?"
POST_MECHANICAL = "Mechanical findings — the revised plan (review)"
ARTIFACT_NAME = "plan-source-${{ github.event.client_payload.identifier }}"
PLANNING_WRITE = re.compile(r'linear_ops\.py state "\$EPIC" "Planning"')


def job(name: str) -> dict:
    return yaml.safe_load(wf_src())["jobs"][name]


def job_step(job_name: str, name: str) -> dict:
    found = [s for s in job(job_name).get("steps") or [] if s.get("name") == name]
    if len(found) != 1:
        raise AssertionError(f"{len(found)} {job_name}-job steps named {name!r}")
    return found[0]


class _Facts:
    """`test_planner_queue_wiring.Walk` resolves only `steps.*.outputs`; the
    `publish` job's gate reads `needs.plan.outputs`, so the facts are handed
    to it by name."""

    @staticmethod
    def walk(known: dict):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from test_planner_queue_wiring import Walk, truth  # noqa: E402

        class Facts(Walk):
            def _name(self, text):
                if text in self.known:
                    return self.known[text]
                return super()._name(text)

        return Facts(known, set()), truth


class TheReviewReplanCarriesThePlanArtifact(unittest.TestCase):
    """DRE-5299, read off the rail."""

    def test_every_new_step_runs_on_the_review_re_plan_only(self):
        for name in (PORTAL_MINT, PORTAL_CHECKOUT, PUBLISHED_SOURCE,
                     REVIEW_RECHECK, REVIEW_UPLOAD):
            with self.subTest(step=name):
                self.assertIn(REVIEW_REPLAN, gate_of(exact_step(name)))
                self.assertNotIn(ACTIVATE_MODE, gate_of(exact_step(name)))

    def test_the_step_ids_the_contract_publishes(self):
        for name, ident in ((PORTAL_MINT, "app_portal"),
                            (PORTAL_CHECKOUT, "pubsrc_checkout"),
                            (PUBLISHED_SOURCE, "pubsrc"),
                            (REVIEW_RECHECK, "recheck_review")):
            with self.subTest(step=name):
                self.assertEqual(exact_step(name).get("id"), ident)

    def test_the_portal_mint_is_the_primary_app_and_feeds_one_checkout(self):
        mint = exact_step(PORTAL_MINT)
        publish_mint = job_step("publish", "Mint bot token")
        self.assertEqual(mint.get("uses"), publish_mint.get("uses"))
        self.assertEqual(mint.get("with"), publish_mint.get("with"))
        self.assertIn("vars.PLAN_PORTAL_REPO != ''", gate_of(mint))
        self.assertFalse(mint.get("continue-on-error"))
        checkout = exact_step(PORTAL_CHECKOUT)
        self.assertEqual(exact_index(PORTAL_CHECKOUT), exact_index(PORTAL_MINT) + 1)
        self.assertIn("vars.PLAN_PORTAL_REPO != ''", gate_of(checkout))
        self.assertEqual(checkout.get("uses"),
                         job_step("publish", "Plan artifact — portal checkout").get("uses"))
        self.assertEqual((checkout.get("with") or {}).get("token"),
                         "${{ steps.app_portal.outputs.token }}")
        self.assertIs(checkout.get("continue-on-error"), True)
        readers = [s.get("name") for s in steps()
                   if "steps.app_portal.outputs.token" in yaml.safe_dump(s)]
        self.assertEqual(readers, [PORTAL_CHECKOUT])
        for s in steps():
            if str(s.get("uses") or "").startswith("actions/checkout@"):
                with self.subTest(step=s.get("name") or s.get("uses")):
                    self.assertNotIn("steps.app_post.outputs.token", yaml.safe_dump(s))

    def test_the_artifact_steps_precede_the_re_plan_and_the_re_check_follows_it(self):
        replan = exact_index(POST_REPLAN)
        self.assertLess(exact_index(PORTAL_MINT), exact_index(PORTAL_CHECKOUT))
        self.assertLess(exact_index(PORTAL_CHECKOUT), exact_index(PUBLISHED_SOURCE))
        self.assertLess(exact_index(PUBLISHED_SOURCE), replan)
        self.assertGreater(exact_index(REVIEW_RECHECK), exact_index(POST_REPLAN_DONE))
        self.assertGreater(exact_index(POST_MECHANICAL), exact_index(REVIEW_RECHECK))
        self.assertGreater(exact_index(REVIEW_UPLOAD), exact_index(REVIEW_RECHECK))
        self.assertGreater(exact_index(REVIEW_RE_REVIEW), exact_index(REVIEW_UPLOAD))

    def test_the_published_source_is_read_through_the_module(self):
        source = exact_step(PUBLISHED_SOURCE)
        run = run_of(source)
        self.assertIn("plan_artifact.py path", run)
        self.assertIn("plan_artifact.SOURCE_NAME", run, "read, never restated")
        self.assertNotIn(pa.SOURCE_NAME, run)
        self.assertIn(".plan-portal", run)
        self.assertIn("plan-artifact.md", run)
        self.assertIn("found=true", run)
        self.assertIn("found=false", run)
        self.assertIn("steps.pubsrc_checkout.outcome", yaml.safe_dump(source))
        self.assertIs(source.get("continue-on-error"), True,
                      "a source that cannot be read is written fresh, never a red run")

    def test_the_re_check_runs_the_plan_routes_gates_in_order(self):
        run = run_of(exact_step(REVIEW_RECHECK))
        order = ["validate_card.py check-children", "plan_artifact.py check",
                 "proof_and_demo.py check", "plan_child_verdicts.py stamp"]
        at = [run.index(cmd) for cmd in order]
        self.assertEqual(at, sorted(at), "the gates run in the plan route's order")
        self.assertIn("child-descriptions", run)
        self.assertIn("plan_artifact.py ui-epic", run)
        self.assertIn("--ui", run)
        self.assertIn('> "$CHILDREN"', run)
        self.assertIn("ok=true", run)
        self.assertIn("ok=false", run)
        self.assertFalse(exact_step(REVIEW_RECHECK).get("continue-on-error"))

    def test_a_refused_revision_parks_in_triage_for_an_operator(self):
        run = run_of(exact_step(REVIEW_RECHECK))
        self.assertIn('add-label "$EPIC" needs-human', run)
        self.assertRegex(run, TRIAGE_WRITE)
        self.assertLess(run.index("add-label"), TRIAGE_WRITE.search(run).start())
        self.assertIn("plan_critic.REAPPROVE_HOW", run)
        self.assertNotIn(pc.REAPPROVE_HOW, run)
        self.assertNotRegex(run, GREEN_LIGHT_WRITE)
        self.assertNotRegex(run, PLANNING_WRITE)
        self.assertTrue(run.rstrip().endswith("exit 1"))

    def test_the_re_review_and_the_upload_wait_for_the_re_check(self):
        self.assertIn(RECHECK_OK, gate_of(exact_step(REVIEW_RE_REVIEW)))
        self.assertIn(RECHECK_OK, gate_of(exact_step(REVIEW_UPLOAD)))

    def test_the_upload_is_the_plan_routes_upload_again(self):
        ours = exact_step(REVIEW_UPLOAD)
        theirs = exact_step("Plan artifact — upload source")
        self.assertEqual(ours.get("uses"), theirs.get("uses"))
        self.assertEqual(ours.get("with"), theirs.get("with"))
        self.assertEqual((ours.get("with") or {}).get("if-no-files-found"), "error")

    def test_the_artifact_name_is_one_literal_at_three_sites(self):
        sites = [s for s in steps()
                 if str((s.get("with") or {}).get("name") or "").startswith("plan-source-")]
        self.assertEqual([s.get("name") for s in sites],
                         ["Plan artifact — upload source", REVIEW_UPLOAD,
                          "Plan artifact — fetch source"])
        self.assertEqual({s["with"]["name"] for s in sites}, {ARTIFACT_NAME})

    def test_the_plan_job_publishes_replanned(self):
        self.assertEqual(job("plan")["outputs"].get("replanned"),
                         "${{ steps.recheck_review.outputs.ok }}")

    def test_the_publish_job_admits_a_review_run_only_once_it_replanned(self):
        gate = str(job("publish").get("if") or "")
        rows = [
            ({"needs.plan.outputs.mode": "plan", "needs.plan.outputs.kids": "3"}, True),
            ({"needs.plan.outputs.mode": "plan", "needs.plan.outputs.kids": "0"}, False),
            ({"needs.plan.outputs.mode": "review", "needs.plan.outputs.replanned": "true"}, True),
            ({"needs.plan.outputs.mode": "review", "needs.plan.outputs.replanned": "false"}, False),
            ({"needs.plan.outputs.mode": "review", "needs.plan.outputs.replanned": ""}, False),
            ({"needs.plan.outputs.mode": "activate", "needs.plan.outputs.replanned": "true"}, False),
        ]
        for known, expected in rows:
            facts = {"needs.plan.outputs.mode": "", "needs.plan.outputs.kids": "",
                     "needs.plan.outputs.replanned": "", **known}
            walk, truth = _Facts.walk(facts)
            with self.subTest(**known):
                self.assertIs(truth(walk.evaluate(gate)), expected)

    def test_nothing_reads_another_runs_artifacts(self):
        # A cross-run download needs `actions: read`, which no plan stub grants.
        src = wf_src()
        self.assertNotIn("gh run " + "download", src)
        self.assertNotIn("actions/" + "artifacts", src)

    def test_both_re_plan_prompts_say_where_the_artifact_is(self):
        for name in (POST_REPLAN, POST_REPLAN + " — on the next rung"):
            with self.subTest(step=name):
                prompt = str((exact_step(name).get("with") or {}).get("prompt") or "")
                self.assertIn("steps.pubsrc.outputs.found", prompt)
                self.assertIn("plan-artifact.md", prompt)
                self.assertIn("plan_artifact.py check", prompt)
                self.assertIn("standards/plan-artifact.md", prompt)
                self.assertIn("ledger-check", prompt)

    def test_the_re_checks_comments_are_declared_by_its_step(self):
        sites = [site for site in car.sites()
                 if site.path == ".github/workflows/plan.yml"
                 and site.step == REVIEW_RECHECK]
        self.assertGreaterEqual(len(sites), 3)
        declared = car.declarations()
        for site in sites:
            with self.subTest(site=site.where):
                hits = [d for d in declared if car._matches(d, site)]
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0].get("step"), REVIEW_RECHECK)
                self.assertTrue((hits[0].get("why") or "").strip())


# --- DRE-5284: the plan route hands a passed plan to the second critic -------
#
# Until this card the plan route ended by moving the epic to Green Light the
# moment the first critic was done, whatever it decided, and the review route
# DRE-5280 built had no caller. Now a proceed is handed to the second critic
# (`review_rerun.py dispatch --reason review --trigger-state planning`), the
# first critic's bound parks in Triage at either round, and nothing on the plan
# route writes Green Light.

PLAN_MODE = "steps.route.outputs.mode == 'plan'"
PRE_PROCEED = ("steps.pre1.outputs.action == 'proceed' || "
               "steps.pre2.outputs.action == 'proceed'")
PRE_BOUND = ("steps.pre1.outputs.bound == 'true' || "
             "steps.pre2.outputs.bound == 'true'")
PRE1_HOLD = "steps.pre1.outputs.action == 'hold'"
PRE1_NOT_BOUND = "steps.pre1.outputs.bound != 'true'"
HANDOFF_MINT = "Re-mint bot token — hand-off to the second critic"
RENAMED_REVIEW = "Second critic — review (before Green Light)"
RENAMED_MECHANICAL = "Mechanical findings — the revised plan (review)"
RETIRED_PROMPT_SENTENCES = ("The CEO has already approved", "The CEO has APPROVED",
                            "The CEO approved epic")


def plan_route_walk(known: dict):
    """The three-valued `if:` reader the planner-slot wiring tests walk plan.yml
    with (DRE-5179), with the job read as still green and the plan route's
    facts handed in by name."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_planner_queue_wiring import Walk, truth  # noqa: E402

    class PlanRoute(Walk):
        def _name(self, text):
            if text in self.known:
                return self.known[text]
            return super()._name(text)

        def _call(self, name, args):
            if name in ("success", "always"):
                return True
            if name in ("cancelled", "failure"):
                return False
            return Walk._call(name, args)

    facts = {"steps.route.outputs.mode": "plan", "steps.kids.outputs.count": "3",
             **known}
    walk = PlanRoute(facts, set())
    return lambda gate: truth(walk.evaluate(gate))


def touches_the_epic(s: dict) -> bool:
    run = run_of(s)
    return ("review_rerun.py dispatch" in run
            or re.search(r'linear_ops\.py (state|add-label|comment) "\$EPIC"', run)
            is not None)


class ThePlanRouteHandsAPassedPlanToTheSecondCritic(unittest.TestCase):
    """DRE-5284, read off the rail."""

    def test_no_plan_route_step_writes_green_light(self):
        writers = [s.get("name") for s in steps()
                   if (PLAN_MODE in gate_of(s) or "steps.pre1.outputs" in gate_of(s)
                       or "steps.pre2.outputs" in gate_of(s))
                   and GREEN_LIGHT_WRITE.search(run_of(s))]
        self.assertEqual(writers, [])

    def test_the_hand_off_is_gated_on_the_first_critics_proceed(self):
        s = exact_step(HANDOFF)
        self.assertIn(PRE_PROCEED, gate_of(s))
        self.assertEqual(s.get("id"), "handoff")
        self.assertFalse(s.get("continue-on-error"),
                         "a hand-off that did not go through must leave the run red")

    def test_the_hand_off_asks_for_the_review_from_planning_and_writes_no_lane(self):
        run = run_of(exact_step(HANDOFF))
        self.assertIn("review_rerun.py dispatch", run)
        self.assertIn(f"--reason {rr.REASON_REVIEW} --trigger-state "
                      f"{rr.TRIGGER_STATE_REVIEW}", run)
        self.assertIn('--repo "$GITHUB_REPOSITORY"', run)
        self.assertNotIn("linear_ops.py state", run, "the hand-off writes no lane")
        self.assertNotIn("add-label", run)
        self.assertIn("with the second critic", run)

    def test_the_hand_off_note_hangs_off_the_dispatch(self):
        """DRE-2034: no receipt on an unconfirmed dispatch. A dispatch that did
        not go through is said, and the step goes red."""
        run = run_of(exact_step(HANDOFF))
        head, _, tail = run.partition("review_rerun.py dispatch")
        self.assertNotIn("linear_ops.py comment", head)
        self.assertRegex(run, r"if\s+python3\s+\S*review_rerun\.py dispatch")
        self.assertIn("could NOT", tail)
        self.assertTrue(run.rstrip().endswith("fi"))
        self.assertIn("exit 1", tail)
        self.assertLess(tail.index("with the second critic"), tail.index("could NOT"))

    def test_the_hand_off_tells_a_no_result_from_a_pass(self):
        """A first critic NO_RESULT takes the same step, and the note says it
        produced no result rather than that it passed. The word is the LAST
        decision's — round 2's when round 2 ran."""
        s = exact_step(HANDOFF)
        self.assertEqual((s.get("env") or {}).get("PRE_RESULT"),
                         "${{ steps.pre2.outputs.result || steps.pre1.outputs.result }}")
        self.assertIn("no result", run_of(s))

    def test_the_hand_off_runs_under_a_token_minted_after_the_first_critic(self):
        at = token_mint_index(HANDOFF)
        self.assertEqual(exact_step(HANDOFF_MINT).get("id"),
                         steps()[at].get("id"))
        self.assertGreater(at, exact_index("First critic — round 2"))
        self.assertLess(at, exact_index(HANDOFF))
        self.assertIn(PRE_PROCEED, gate_of(exact_step(HANDOFF_MINT)))

    def test_the_hand_off_is_the_last_epic_touching_step_of_a_passed_plan_route(self):
        # A round that never ran has empty outputs, which is how Actions reads
        # them — round 2 on a first-round proceed.
        for known in ({"steps.pre1.outputs.action": "proceed",
                       "steps.pre1.outputs.bound": "false",
                       "steps.pre2.outputs.action": "",
                       "steps.pre2.outputs.bound": ""},
                      {"steps.pre1.outputs.action": "hold",
                       "steps.pre1.outputs.bound": "false",
                       "steps.pre2.outputs.action": "proceed",
                       "steps.pre2.outputs.bound": "false"}):
            with self.subTest(**known):
                reads = plan_route_walk(known)
                plan_route = [s.get("name") for s in steps()
                              if (PLAN_MODE in gate_of(s)
                                  or "steps.pre1.outputs" in gate_of(s)
                                  or "steps.pre2.outputs" in gate_of(s))
                              and reads(gate_of(s)) is not False
                              and touches_the_epic(s)]
                self.assertTrue(plan_route)
                self.assertEqual(plan_route[-1], HANDOFF)
                self.assertNotIn(PRE_PARK, plan_route)

    def test_the_hand_off_follows_the_routing_verdicts(self):
        """The children carry their verdicts before the plan goes anywhere, and
        nothing runs between the stamp and the hand-off."""
        at = exact_index("Routing verdicts — the epic's children")
        self.assertEqual(steps()[at + 1].get("name"), HANDOFF)

    def test_the_bound_reaches_exactly_one_step(self):
        reached = [s.get("name") for s in steps() if PRE_BOUND in gate_of(s)]
        self.assertEqual(reached, [PRE_PARK])
        bound_readers = [s.get("name") for s in steps()
                         if re.search(r"steps\.pre[12]\.outputs\.bound == 'true'",
                                      gate_of(s))]
        self.assertEqual(bound_readers, [PRE_PARK])
        for known in ({"steps.pre1.outputs.action": "hold",
                       "steps.pre1.outputs.bound": "true",
                       "steps.pre2.outputs.action": "",
                       "steps.pre2.outputs.bound": ""},
                      {"steps.pre1.outputs.action": "hold",
                       "steps.pre1.outputs.bound": "false",
                       "steps.pre2.outputs.action": "hold",
                       "steps.pre2.outputs.bound": "true"}):
            with self.subTest(**known):
                reads = plan_route_walk(known)
                self.assertIs(reads(gate_of(exact_step(PRE_PARK))), True)
                self.assertIs(reads(gate_of(exact_step(HANDOFF))), False)

    def test_the_park_labels_then_moves_to_triage_and_dispatches_nothing(self):
        s = exact_step(PRE_PARK)
        run = run_of(s)
        self.assertIn('add-label "$EPIC" needs-human', run)
        self.assertRegex(run, TRIAGE_WRITE)
        self.assertLess(run.index("add-label"), TRIAGE_WRITE.search(run).start())
        self.assertNotRegex(run, GREEN_LIGHT_WRITE)
        self.assertNotIn("review_rerun.py", run)
        self.assertIn("plan_critic.REAPPROVE_HOW", run, "read, never restated")
        self.assertNotIn(pc.REAPPROVE_HOW, run)
        self.assertEqual((s.get("env") or {}).get("NOTE"),
                         "${{ steps.pre2.outputs.note || steps.pre1.outputs.note }}")

    def test_every_first_critic_hold_step_stands_down_at_the_bound(self):
        """Discovered off the workflow, never listed: a step added later gated
        on the first critic's hold is caught here, as `Turn ceiling — first
        critic's re-plan` would have been."""
        holds = [s for s in steps() if PRE1_HOLD in gate_of(s)]
        self.assertGreaterEqual(len(holds), 12)
        self.assertIn("Turn ceiling — first critic's re-plan",
                      [s.get("name") for s in holds])
        reads = plan_route_walk({"steps.pre1.outputs.action": "hold",
                                 "steps.pre1.outputs.bound": "true"})
        for s in holds:
            with self.subTest(step=s.get("name")):
                self.assertIn(PRE1_NOT_BOUND, gate_of(s))
                self.assertIs(reads(gate_of(s)), False,
                              "a bound at round 1 buys no third round")

    def test_no_prompt_says_the_ceo_approved_the_plan(self):
        for s in steps():
            prompt = str((s.get("with") or {}).get("prompt") or "")
            for sentence in RETIRED_PROMPT_SENTENCES:
                with self.subTest(step=s.get("name"), sentence=sentence):
                    self.assertNotIn(sentence, prompt)
        self.assertNotIn("The CEO has approved. The text is FROZEN", wf_src())

    def test_the_review_mode_re_plan_says_the_ceo_has_not_seen_the_plan(self):
        for name in (POST_REPLAN, POST_REPLAN + " — on the next rung"):
            with self.subTest(step=name):
                prompt = str((exact_step(name).get("with") or {}).get("prompt") or "")
                self.assertIn("has not seen this plan", prompt)
                self.assertIn("in this attempt", prompt)

    def test_the_two_renamed_steps(self):
        self.assertEqual(exact_step(RENAMED_REVIEW).get("id"), "posta")
        exact_step(RENAMED_MECHANICAL)
        names = [s.get("name") or "" for s in steps()]
        self.assertEqual([n for n in names if "(after approval)" in n], [])
        self.assertEqual(names.count("Mechanical findings — the revised plan"), 1)

    def test_the_new_comments_are_declared_by_their_steps(self):
        sites = [site for site in car.sites()
                 if site.path == ".github/workflows/plan.yml"
                 and site.step in (HANDOFF, PRE_PARK)]
        self.assertEqual(sorted({site.step for site in sites}),
                         sorted((HANDOFF, PRE_PARK)))
        declared = car.declarations()
        for site in sites:
            with self.subTest(site=site.where):
                hits = [d for d in declared if car._matches(d, site)]
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0].get("step"), site.step)
                self.assertTrue((hits[0].get("why") or "").strip())


if __name__ == "__main__":
    unittest.main()
