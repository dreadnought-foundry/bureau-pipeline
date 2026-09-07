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
import review_rerun as rr  # noqa: E402

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


POST_REPLAN = "Re-plan after the second critic sent it back"
SENT_BACK = "Second critic sent the plan back"
SNAPSHOT = "Children before the re-plan"
CARD_SET = "Re-plan — did the card set change?"

# The three branches of the sent-back step, each opened by a comment of its
# own so a test can read one branch without guessing at shell indentation.
BOUND_BRANCH = "# THE BOUND"
CHANGED_BRANCH = "# THE CARD SET CHANGED"
SAME_BRANCH = "# THE SAME CARDS"


def sent_back_branches() -> tuple[str, str, str]:
    """`(bound, changed, same)` — the three bodies, in the order they branch."""
    run = str(step_named(SENT_BACK).get("run") or "")
    for marker in (BOUND_BRANCH, CHANGED_BRANCH, SAME_BRANCH):
        assert marker in run, f"{marker!r} is not in the sent-back step"
    bound, rest = run.split(BOUND_BRANCH, 1)[1].split(CHANGED_BRANCH, 1)
    changed, same = rest.split(SAME_BRANCH, 1)
    return bound, changed, same


class ASendBackAfterApprovalRevisesThenParks(unittest.TestCase):
    """DRE-3088, read off the rail. A held plan is revised once before it goes
    back to the CEO; a plan held twice parks; and no receipt names Todo."""

    def test_the_re_plan_runs_between_the_decision_and_the_park(self):
        self.assertLess(index_of(SECOND), index_of(POST_REPLAN))
        self.assertLess(index_of(POST_REPLAN), index_of(SENT_BACK))
        self.assertLess(index_of(SENT_BACK), index_of(ACTIVATE))

    def test_the_re_plan_is_gated_on_a_hold_and_cannot_strand_the_epic(self):
        replan = step_named(POST_REPLAN)
        self.assertIn("action == 'hold'", str(replan.get("if")))
        self.assertIn("mode == 'activate'", str(replan.get("if")))
        self.assertTrue(replan.get("continue-on-error"),
                        "a re-plan that dies must not stop the Green Light move")
        self.assertIn("RE-plan", str(replan["with"]["prompt"]))
        self.assertIn("post1.outputs.reason", str(replan["with"]["prompt"]))

    def test_the_park_reads_the_bound_and_stamps_needs_human(self):
        run = str(step_named(SENT_BACK).get("run") or "")
        self.assertIn('"$BOUND" = "true"', run)
        bound, _changed, _same = sent_back_branches()
        self.assertIn("add-label", bound)
        self.assertIn("needs-human", bound)
        self.assertIn("Green Light", bound)

    def test_no_receipt_on_the_activate_route_names_todo(self):
        for fragment in (SENT_BACK,):
            run = str(step_named(fragment).get("run") or "")
            self.assertIn("In Progress", run)
            self.assertNotIn("Todo", run)

    def test_the_activate_step_is_reached_only_on_proceed(self):
        self.assertIn("action == 'proceed'", str(step_named(ACTIVATE).get("if")))

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


class OnlyAnAddedOrRemovedCardParksAfterASendBack(unittest.TestCase):
    """DRE-3291, read off the rail. A re-plan that changed no card SET is not a
    decision the CEO has to make — he approved those cards already — so the run
    re-reviews the plan itself instead of asking him for a fifth approval."""

    def test_the_snapshots_bracket_the_re_plan(self):
        self.assertLess(index_of(SNAPSHOT), index_of(POST_REPLAN))
        self.assertLess(index_of(POST_REPLAN), index_of(CARD_SET))
        self.assertLess(index_of(CARD_SET), index_of(SENT_BACK))

    def test_neither_snapshot_can_strand_a_held_epic(self):
        """Both are gated exactly like the re-plan and both are best-effort:
        the park below must never depend on either of them running."""
        for fragment in (SNAPSHOT, CARD_SET):
            s = step_named(fragment)
            self.assertIn("action == 'hold'", str(s.get("if")), fragment)
            self.assertIn("mode == 'activate'", str(s.get("if")), fragment)
            self.assertTrue(s.get("continue-on-error"), fragment)

    def test_the_before_snapshot_is_taken_from_the_live_children(self):
        run = str(step_named(SNAPSHOT).get("run") or "")
        self.assertIn("children-json", run)
        self.assertIn("children-before.json", run)

    def test_the_diff_is_the_shared_contract_and_unknown_reads_as_changed(self):
        """`review_rerun.py card-set` owns the answer (DRE-3286), and a
        snapshot that is missing or empty is CHANGED — unknown is unknown, and
        the CEO reads it (standards/console-honesty.md rule 2)."""
        s = step_named(CARD_SET)
        self.assertEqual(s.get("id"), "cardset")
        run = str(s.get("run") or "")
        self.assertIn("review_rerun.py card-set", run)
        self.assertIn("--before", run)
        self.assertIn("--after", run)
        self.assertIn("children-after.json", run)
        self.assertIn("changed=true", run)

    def test_the_bound_is_read_before_the_card_set(self):
        """"Two REAL send-backs still park" is out of this card's scope, so the
        bound branch is first and nothing below it can reach a parked plan."""
        run = str(step_named(SENT_BACK).get("run") or "")
        self.assertLess(run.index('"$BOUND" = "true"'), run.index("$SET_CHANGED"))
        bound, _changed, _same = sent_back_branches()
        self.assertNotIn("review_rerun.py dispatch", bound)

    def test_a_changed_card_set_parks_and_names_the_cards(self):
        _bound, changed, _same = sent_back_branches()
        self.assertIn('state "$EPIC" "Green Light"', changed)
        self.assertIn("$ADDED", changed)
        self.assertIn("$REMOVED", changed)
        self.assertNotIn("review_rerun.py dispatch", changed,
                         "a shape the CEO has not seen is not re-reviewed behind him")

    def test_a_re_plan_that_did_not_finish_takes_the_same_branch(self):
        run = str(step_named(SENT_BACK).get("run") or "")
        self.assertIn('"$REPLAN_OUTCOME" != "success"', run)

    def test_the_same_card_set_dispatches_a_re_review_and_moves_no_lane(self):
        _bound, _changed, same = sent_back_branches()
        self.assertIn("review_rerun.py dispatch", same)
        self.assertIn(f"--reason {rr.REASON_RE_REVIEW}", same)
        self.assertIn('--repo "$GITHUB_REPOSITORY"', same)
        self.assertNotIn("linear_ops.py state", same,
                         "a re-review never writes the epic's lane")
        self.assertNotIn("add-label", same)

    def test_the_dispatch_runs_under_the_app_token(self):
        """Q1/Q2: `repos/.../dispatches` needs contents:write, which the App
        token holds and the stub's own `github.token` does not."""
        env = step_named(SENT_BACK).get("env") or {}
        self.assertEqual(env.get("GH_TOKEN"), "${{ steps.app.outputs.token }}")

    def test_a_failed_re_review_dispatch_is_said_and_never_claimed_as_started(self):
        """DRE-2034: no receipt on an unconfirmed dispatch. Both receipts hang
        off the dispatch's exit status, and the failure leaves the step red so
        the medic picks the run up — nothing else would, because no lane was
        written."""
        _bound, _changed, same = sent_back_branches()
        head, _, tail = same.partition("review_rerun.py dispatch")
        self.assertNotIn("linear_ops.py comment", head,
                         "a re-review receipt written before the dispatch is attempted")
        self.assertRegex(same, r"if\s+python3\s+\S*review_rerun\.py dispatch")
        self.assertIn("🔁", tail)
        self.assertIn("could NOT", tail)
        self.assertLess(tail.index("🔁"), tail.index("could NOT"))
        self.assertIn("exit 1", tail)


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
        # The post-approval review's ceiling is an EXPRESSION since DRE-3241
        # (sized per plan), so the literal scan above cannot see it; its worst
        # case is the cap, added by name so the arithmetic keeps counting it.
        # Since DRE-3289 that worst case is the RETRY cap, not the first-run
        # one: a review that died is re-run in a job of its own at
        # `retry_ceiling` turns, and the arithmetic has to cover the longest
        # run this workflow can start.
        self.assertEqual(
            len(turns), len(agent_steps()) - 1,
            "every agent step but the post-approval review carries a literal "
            "ceiling; a second expression would drop out of this arithmetic",
        )
        turns.append(rr.POST_REVIEW_RETRY_CAP)
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
        step on the route, and a turn budget the roster already carries."""
        on_route = [s for s in agent_steps()
                    if "one-off" in str(s.get("if") or "")]
        self.assertEqual(1, len(on_route),
                         "a one-off run must ask for exactly one critic call")
        args = str((on_route[0].get("with") or {}).get("claude_args") or "")
        turns = [int(m) for m in re.findall(r"--max-turns\s+(\d+)", args)]
        self.assertEqual(1, len(turns))
        self.assertLessEqual(turns[0], 40)


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


# --- DRE-3241: the post-approval review's ceiling, and what a dead one leaves ---

CEILING = "Second critic — turn ceiling"
DIED = "Second critic — the review died"
DECISION = "Second critic — decision"


class TheReviewCeilingIsSizedFromThePlan(unittest.TestCase):
    """The ceiling is computed on the activate route — `steps.kids` only
    exists on the plan route — through the one sizer in review_rerun.py, which
    reads the plan for a first run and the epic's own thread for a retry
    (DRE-3289), with the fifteen-card number as the fallback so a bare
    `--max-turns` can never reach the action (qa-review.yml's shape)."""

    def test_the_ceiling_step_runs_before_the_review_on_the_activate_route(self):
        self.assertLess(index_of(CEILING), index_of(SECOND))
        step = step_named(CEILING)
        self.assertIn("mode == 'activate'", str(step.get("if")))
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

    def test_fifteen_cards_get_eighty_turns_end_to_end(self):
        """The number this card writes down, read through the same function
        the workflow step calls."""
        self.assertEqual(pc.post_review_turns(15), 80)


class ADeadReviewWritesItsTombstone(unittest.TestCase):
    """The step that failed the job on 2026-09-05 wrote nothing. Now a review
    that dies leaves a `🪦` record on the epic, and NOTHING promotes."""

    def test_the_review_is_not_continue_on_error(self):
        """The trap: `continue-on-error` on the review would let the decision
        step read an empty result file as NO_RESULT — "a crash is not a
        rejection" — and ACTIVATE the epic with no review at all."""
        self.assertFalse(step_named(SECOND).get("continue-on-error"))

    def test_the_tombstone_step_runs_only_when_the_review_itself_failed(self):
        self.assertLess(index_of(SECOND), index_of(DIED))
        self.assertLess(index_of(DIED), index_of(DECISION))
        gate = str(step_named(DIED).get("if") or "")
        self.assertIn("failure()", gate)
        self.assertIn("steps.posta.outcome == 'failure'", gate)
        self.assertIn("mode == 'activate'", gate)

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
    """DRE-3289. A dead post-approval review used to leave the epic In
    Progress with nothing scheduled, and the only way to run the review again
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

    def test_a_retry_dispatches_the_activate_route_and_moves_no_lane(self):
        run = str(step_named(DIED).get("run") or "")
        self.assertIn("review_rerun.py dispatch", run)
        self.assertIn(f"--reason {rr.REASON_REVIEW_RETRY}", run)
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
        env = step_named(DIED).get("env") or {}
        self.assertEqual(env.get("GH_TOKEN"), "${{ steps.app.outputs.token }}")

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
        self.assertIn('state "$EPIC" "Green Light"', park)
        self.assertIn('cat "$PARK_NOTE"', park,
                      "the note after-death wrote, not a rival sentence")
        self.assertIn('--note-file "$PARK_NOTE"', run,
                      "...and that is the file after-death was told to write")

    def test_the_review_step_still_has_no_continue_on_error(self):
        """The DRE-3241 trap, re-pinned here because this card is the one that
        makes a dead review recoverable: with `continue-on-error` the decision
        step would read the empty result file as NO_RESULT and activate the
        epic with no review at all."""
        self.assertFalse(step_named(SECOND).get("continue-on-error"))
        for fragment in (DECISION, ACTIVATE):
            gate = str(step_named(fragment).get("if") or "")
            self.assertNotIn("always()", gate, fragment)
            self.assertNotIn("failure()", gate, fragment)


class EveryReapprovalNoticeOnTheRailNamesGreenLightFirst(unittest.TestCase):
    """The relay's activation fires on a transition INTO In Progress. Every
    notice that asks the CEO to approve again says the two-step move, in the
    exact words plan_critic.py uses, so the rail and the sweep cannot drift."""

    def test_the_sent_back_notices_ask_only_for_the_move_that_is_left(self):
        """DRE-3291 split this three ways, and each branch asks for a different
        thing — or for nothing.

        The BOUND still says the two-step move in `REAPPROVE_HOW`'s exact
        words: the epic is parked, and clearing needs-human then re-approving
        is a person's job. The CHANGED branch has just moved the epic to Green
        Light itself, so the two-step sentence would name a move that is
        already made — it asks for the single one that is left. The SAME-CARDS
        branch asks for nothing at all, because nothing there is the CEO's."""
        run = str(step_named(SENT_BACK).get("run") or "")
        bound, changed, same = sent_back_branches()
        self.assertEqual(run.count(pc.REAPPROVE_HOW), 1,
                         "only the parked notice names the two-step move")
        self.assertIn(pc.REAPPROVE_HOW, bound)

        self.assertNotIn(pc.REAPPROVE_HOW, changed)
        self.assertIn("Green Light", changed)
        self.assertIn(pc.APPROVAL_LANE, changed,
                      "the changed-set notice still names the approval move")

        self.assertNotIn(pc.REAPPROVE_HOW, same)
        self.assertNotIn(pc.APPROVAL_LANE, same,
                         "an unchanged card set asks the CEO for no move at all")
        self.assertNotIn("Approve", same)

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

    def test_the_standard_says_green_light_then_approve(self):
        text = open(STANDARD).read()
        self.assertIn("Green Light, then approve", text)
        self.assertNotIn("re-approval\n  by moving it to **In Progress**", text)


if __name__ == "__main__":
    unittest.main()
