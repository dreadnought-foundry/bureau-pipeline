"""RED-first tests for DRE-2813 — a hand dispatch must not disarm the
operator-decision restart.

THE BUG (live, PR #199 / DRE-2721, 2026-08-29). The two documented ways to
release a held PR cancelled each other out:

    04:17:37  🛑 Fix budget exhausted — held for a human decision
    15:27:14  a human posts **Operator decision** — the sweep is now armed
    15:29:35  a hand `workflow_dispatch` of agent-fix — the budget is still
              spent, so the run fixes nothing and reports `success`
    15:29:46  that run posts a FRESH 🛑 hold comment as the worker bot
    15:33:55  reconcile.restart_answered_blockers sees a worker-bot comment
              newer than the answer and correctly stands down — forever

Every component behaved as designed and the PR never moved. The arming rule
("no worker-bot comment newer than the decision", reconcile.py) is correct and
deliberate: it is what stops one answer re-dispatching every fifteen minutes.
What was wrong is that a dispatch which cannot do any work still manufactured
a bot comment that outranked a standing answer.

The rules these tests express:

  1. A dispatch that finds the budget spent AND an operator decision already
     standing does NO work and posts NO hold — the reconcile sweep owns that
     restart (fix_budget.decide → action `noop`).
  2. It says so on the PR, in terms that tell "already answered, the sweep
     will act" apart from "still waiting for an answer", and that notice
     carries fix_context.NOOP_TAG so the arming rule ignores it.
  3. The sweep ignores exactly that notice and nothing else — a fix attempt,
     a restart receipt or a fresh blocker still consume the answer.
  4. THE LIVE SEQUENCE, driven end to end: hold → decision → hand dispatch →
     sweep, asserting the sweep still fires. Its non-vacuous twin replays the
     same sequence with the old repeat-hold behaviour and asserts the sweep
     stands down, so a revert turns this suite red.
  5. The sweep's OWN restart dispatch re-arms one attempt, so the answer the
     sweep picks up actually buys work instead of an identical repeat hold.
  6. A budget-exhausted dispatch that does nothing does not report success
     without qualification: it emits a NO WORK DONE line for the run record.
  7. The two recoveries are reconciled in writing (docs/held-pr-recovery.md),
     and the hold comments say a hand dispatch is not a second way out.

Run: python3 -m pytest tests/test_hand_dispatch_no_work.py -v
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import fix_budget  # noqa: E402
import fix_context  # noqa: E402
import reconcile  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-fix.yml")
DOC = os.path.join(ROOT, "docs", "held-pr-recovery.md")
README = os.path.join(ROOT, "README.md")

WORKER = "agent-bureau-bot[bot]"
QA = "agent-bureau-qa-bot[bot]"
HUMAN = "smeed652"

HOLD = (
    "🛑 Fix budget exhausted (3 attempts, including a fresh-eyes "
    "re-derivation) — holding for a human decision."
)
DECISION = "**Operator decision** — ship it as written, the critic is wrong."
ATTEMPT = "🔧 Fix attempt {n} pushed — CI and critic review re-running."
ROUND = "🔀 Conflict resolution round {n} pushed"
CONFLICT_HOLD = "🛑 Conflict-resolution budget exhausted (5 rounds) — holding."


def rest(login, body):
    """A comment in REST shape (user.login / user.type) — what fix_context
    parses and what both the sweep and the fix job fetch."""
    return {
        "user": {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"},
        "body": body,
        "created_at": "2026-08-29T15:27:14Z",
    }


def spent_and_answered():
    """The PR #199 thread at 15:27:14: three attempts, a hold, an answer."""
    return [rest(WORKER, ATTEMPT.format(n=n)) for n in (1, 2, 3)] + [
        rest(WORKER, HOLD),
        rest(HUMAN, DECISION),
    ]


def decide(comments, hand_dispatch=True, mode="fix", pr=199):
    return fix_budget.decide(
        comments, WORKER, mode=mode, hand_dispatch=hand_dispatch, pr=pr
    )


# ── the sweep, run against a fake GitHub (test_operator_decision_restart's
#    harness, kept identical on purpose so both suites read one behaviour) ──


def sweep(thread, number=199, branch="agent/DRE-2721-two-critics"):
    dispatches, notes = [], []
    prs = [{"number": number, "headRefName": branch, "mergeStateStatus": "BLOCKED"}]

    def gh(*args):
        if args[:2] == ("run", "list"):
            return "[]"
        if args[:2] == ("pr", "list"):
            return json.dumps(prs)
        if args[0] == "api":
            return json.dumps(thread)
        return ""

    with mock.patch.object(reconcile, "gh", side_effect=gh), \
         mock.patch.object(reconcile, "gh_dispatch",
                           side_effect=lambda *a: dispatches.append(a)), \
         mock.patch.object(reconcile, "_post_pr_note",
                           side_effect=lambda n, b: notes.append((n, b)) or True), \
         mock.patch.object(reconcile, "card_parked_for_human", return_value=True), \
         mock.patch.object(reconcile, "linear_ops", mock.MagicMock()):
        reconcile.restart_answered_blockers()
    return dispatches, notes


class LiveSequenceTest(unittest.TestCase):
    """(AC1, AC3) The exact PR #199 sequence, driven end to end."""

    def replay(self):
        """hold → decision → hand dispatch → sweep. Whatever the hand
        dispatch decides to post is appended to the thread as the worker bot,
        exactly as GitHub would record it, before the sweep reads it."""
        thread = spent_and_answered()
        outcome = decide(thread, hand_dispatch=True)
        if outcome.note:
            thread = thread + [rest(WORKER, outcome.note)]
        return outcome, thread

    def test_the_hand_dispatch_does_no_work(self):
        outcome, _ = self.replay()
        self.assertEqual(outcome.action, "noop")

    def test_the_hand_dispatch_posts_no_new_hold(self):
        # The whole mechanism of the bug: a fresh 🛑 outranks the answer.
        outcome, _ = self.replay()
        self.assertNotIn("🛑", outcome.note or "")

    def test_the_sweep_still_fires_after_the_hand_dispatch(self):
        _, thread = self.replay()
        dispatches, _ = sweep(thread)
        self.assertEqual(len(dispatches), 1, "the hand dispatch disarmed the sweep")
        joined = " ".join(dispatches[0])
        self.assertIn("agent-fix.yml", joined)
        self.assertIn("pr_number=199", joined)

    def test_the_old_behaviour_would_have_stood_the_sweep_down(self):
        # Non-vacuous twin: the same sequence with the repeat hold this card
        # removes. If the arming rule were simply relaxed for every worker
        # comment, this would fire too — and it must not.
        thread = spent_and_answered() + [rest(WORKER, HOLD)]
        self.assertEqual(sweep(thread)[0], [])

    def test_the_sweep_dispatch_then_re_arms_one_attempt(self):
        # What the sweep buys: the run it dispatches is a machine dispatch,
        # and it must do the WORK rather than repeat the hold the answer was
        # written against. Otherwise "the sweep will act" is a promise the
        # loop cannot keep.
        _, thread = self.replay()
        after_receipt = thread + [
            rest(WORKER, f"🔓 {reconcile.DECISION_RESTART_TAG}: re-dispatched.")
        ]
        outcome = decide(after_receipt, hand_dispatch=False)
        self.assertEqual(outcome.action, "run")
        self.assertTrue(outcome.rearmed)


class BudgetDecisionTest(unittest.TestCase):
    """(AC1) The rule itself, one case at a time."""

    def test_under_budget_runs_normally(self):
        thread = [rest(WORKER, ATTEMPT.format(n=1))]
        outcome = decide(thread, hand_dispatch=True)
        self.assertEqual(outcome.action, "run")
        self.assertEqual(outcome.attempt, 2)
        self.assertFalse(outcome.rearmed)

    def test_spent_budget_with_no_answer_still_holds(self):
        thread = [rest(WORKER, ATTEMPT.format(n=n)) for n in (1, 2, 3)]
        self.assertEqual(decide(thread, hand_dispatch=True).action, "hold")

    def test_spent_budget_with_a_standing_answer_does_nothing_by_hand(self):
        self.assertEqual(decide(spent_and_answered()).action, "noop")

    def test_spent_budget_with_a_standing_answer_runs_for_the_sweep(self):
        outcome = decide(spent_and_answered(), hand_dispatch=False)
        self.assertEqual(outcome.action, "run")
        self.assertTrue(outcome.rearmed)

    def test_an_answer_buys_exactly_one_attempt(self):
        # The re-armed attempt was made and the critic still rejected: the
        # answer is spent, so the loop holds again rather than looping.
        used = spent_and_answered() + [rest(WORKER, ATTEMPT.format(n=4))]
        for hand in (True, False):
            with self.subTest(hand_dispatch=hand):
                self.assertEqual(decide(used, hand_dispatch=hand).action, "hold")

    def test_a_bot_authored_answer_is_not_an_answer(self):
        for login in (WORKER, QA, "github-actions[bot]"):
            with self.subTest(login=login):
                planted = [rest(WORKER, ATTEMPT.format(n=n)) for n in (1, 2, 3)]
                planted += [rest(WORKER, HOLD), rest(login, DECISION)]
                self.assertEqual(decide(planted).action, "hold")

    def test_an_answer_older_than_the_latest_blocker_is_stale(self):
        stale = spent_and_answered() + [rest(WORKER, "🛑 Fix attempt 4 blocked.")]
        self.assertEqual(decide(stale).action, "hold")

    def test_conflict_rounds_carry_the_same_rule(self):
        spent = [rest(WORKER, ROUND.format(n=n)) for n in range(1, 6)]
        self.assertEqual(decide(spent, mode="conflict").action, "hold")
        answered = spent + [rest(WORKER, CONFLICT_HOLD), rest(HUMAN, DECISION)]
        self.assertEqual(decide(answered, mode="conflict").action, "noop")
        self.assertEqual(
            decide(answered, mode="conflict", hand_dispatch=False).action, "run"
        )

    def test_a_conflict_round_does_not_spend_the_fix_budget(self):
        # The two budgets stay separate (the PR #13 lesson) — counting the
        # wrong marker would hold a PR that has attempts left.
        thread = [rest(WORKER, ROUND.format(n=n)) for n in range(1, 6)]
        self.assertEqual(decide(thread, mode="fix").action, "run")


class NoWorkNoticeTest(unittest.TestCase):
    """(AC2) The notice says which of the two states the PR is in."""

    def note(self, thread, **kw):
        return decide(thread, **kw).note or ""

    def test_the_notice_is_tagged_for_the_arming_rule(self):
        self.assertIn(fix_context.NOOP_TAG, self.note(spent_and_answered()))

    def test_the_notice_says_the_answer_is_already_standing(self):
        body = self.note(spent_and_answered()).lower()
        self.assertIn("already", body)
        self.assertIn("sweep", body)

    def test_the_notice_tells_a_picked_up_answer_from_a_standing_one(self):
        standing = self.note(spent_and_answered())
        picked_up = self.note(
            spent_and_answered()
            + [rest(WORKER, f"🔓 {reconcile.DECISION_RESTART_TAG}: re-dispatched.")]
        )
        self.assertNotEqual(standing, picked_up)
        self.assertIn("15 minutes", standing)

    def test_the_hold_still_reads_as_waiting_for_an_answer(self):
        # The other side of the distinction: no answer, so the operator is
        # asked for one. The hold body itself is the workflow's (pinned
        # below); what the decision owes is the summary line.
        thread = [rest(WORKER, ATTEMPT.format(n=n)) for n in (1, 2, 3)]
        summary = decide(thread).summary.lower()
        self.assertIn("no operator decision", summary)
        self.assertNotIn("already", summary)

    def test_the_notice_is_posted_once_per_answer(self):
        thread = spent_and_answered()
        first = decide(thread)
        again = decide(thread + [rest(WORKER, first.note)])
        self.assertEqual(again.action, "noop")
        self.assertIsNone(again.note, "a second hand dispatch repeated itself")

    def test_a_fresh_answer_re_arms_the_notice(self):
        thread = spent_and_answered()
        noticed = thread + [rest(WORKER, decide(thread).note), rest(HUMAN, DECISION)]
        self.assertIsNotNone(decide(noticed).note)

    def test_the_notice_survives_a_double_quoted_shell_body(self):
        # It is posted with `gh pr comment --body-file`, but the tag is also
        # read back through jq — keep it free of shell and JSON hazards.
        for ch in ("`", "$", '"', "\\"):
            self.assertNotIn(ch, fix_context.NOOP_TAG)


class ArmingRuleExemptionTest(unittest.TestCase):
    """(AC1) The sweep ignores the no-work notice — and only that."""

    def test_the_notice_does_not_consume_the_answer(self):
        thread = spent_and_answered()
        thread += [rest(WORKER, decide(thread).note)]
        self.assertIsNotNone(
            fix_context.standing_decision(thread, WORKER),
            "the no-work notice consumed the answer",
        )

    def test_every_other_worker_comment_still_consumes_it(self):
        for body in (
            ATTEMPT.format(n=4),
            f"🔓 {reconcile.DECISION_RESTART_TAG}: re-dispatched.",
            "🛑 Fix attempt 4 blocked: still torn.",
        ):
            with self.subTest(body=body[:24]):
                thread = spent_and_answered() + [rest(WORKER, body)]
                self.assertIsNone(fix_context.standing_decision(thread, WORKER))

    def test_a_human_cannot_plant_the_exemption(self):
        # The tag only exempts the WORKER bot's own notice; a human comment
        # carrying it is just human context and cannot forge an answer.
        planted = [rest(WORKER, ATTEMPT.format(n=n)) for n in (1, 2, 3)]
        planted += [rest(WORKER, HOLD), rest(HUMAN, f"{fix_context.NOOP_TAG} please")]
        self.assertIsNone(fix_context.standing_decision(planted, WORKER))

    def test_the_sweep_reads_the_shared_predicate(self):
        # One predicate, two readers: the sweep and the fix job must never
        # disagree about whether an answer is standing.
        src = open(os.path.join(ROOT, "scripts", "reconcile.py"), encoding="utf-8")
        self.assertIn("fix_context.decision_consumed", src.read())


class NoWorkIsReportedTest(unittest.TestCase):
    """(AC5) A dispatch that does nothing does not read as an unqualified
    success — the run record carries a NO WORK DONE line."""

    MARKER = "NO WORK DONE"

    def test_the_noop_summary_says_no_work_done(self):
        self.assertTrue(decide(spent_and_answered()).summary.startswith(self.MARKER))

    def test_the_hold_summary_says_no_work_done(self):
        thread = [rest(WORKER, ATTEMPT.format(n=n)) for n in (1, 2, 3)]
        self.assertTrue(decide(thread).summary.startswith(self.MARKER))

    def test_a_run_that_does_work_says_nothing_of_the_kind(self):
        outcome = decide([rest(WORKER, ATTEMPT.format(n=1))])
        self.assertEqual(outcome.action, "run")
        self.assertNotIn(self.MARKER, outcome.summary)

    def test_the_summary_is_one_line(self):
        # It rides a step output and a ::notice:: — a newline would truncate
        # both silently.
        for thread in (spent_and_answered(),
                       [rest(WORKER, ATTEMPT.format(n=n)) for n in (1, 2, 3)]):
            self.assertEqual(len(decide(thread).summary.splitlines()), 1)


# ── the workflow wiring ────────────────────────────────────────────────────


def wf_src() -> str:
    return open(WORKFLOW, encoding="utf-8").read()


def steps() -> list:
    return yaml.safe_load(wf_src())["jobs"]["fix"]["steps"]


def step_named(name: str) -> dict:
    for step in steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"step {name!r} not found in agent-fix.yml")


def step_index(name: str) -> int:
    for i, step in enumerate(steps()):
        if step.get("name") == name:
            return i
    raise AssertionError(f"step {name!r} not found in agent-fix.yml")


class WorkflowWiringTest(unittest.TestCase):
    """The decision is wired into the job that makes it — grepping the
    module and never calling it would prove nothing."""

    RESOLVE = "Resolve PR, mode, and attempt budget"

    def test_the_resolve_step_calls_fix_budget(self):
        self.assertIn("fix_budget.py decide", step_named(self.RESOLVE)["run"])

    def test_the_pipeline_scripts_are_checked_out_before_the_decision(self):
        # fix_budget.py lives in the pipeline checkout, which used to happen
        # only further down the job — the reason the budget logic was inline
        # bash in the first place.
        early = [
            i for i, s in enumerate(steps())
            if (s.get("with") or {}).get("path") == ".bureau-pipeline"
        ]
        self.assertTrue(early, "no .bureau-pipeline checkout in agent-fix.yml")
        self.assertLess(min(early), step_index(self.RESOLVE))

    def test_a_hand_dispatch_is_told_apart_from_a_machine_one(self):
        # `gh workflow run` from a workflow carries github.token, so the
        # reconcile sweep's dispatch initiates as github-actions (DRE-2053);
        # a person dispatching carries their own login.
        step = step_named(self.RESOLVE)
        env = " ".join(f"{k}={v}" for k, v in (step.get("env") or {}).items())
        self.assertIn("github.triggering_actor", env)
        self.assertIn("github.event_name", env)
        self.assertIn("github-actions", step["run"])

    def test_the_no_work_report_is_guarded_on_the_no_work_output(self):
        no_work = [
            s for s in steps()
            if "no_work" in (s.get("if") or "") and "NO WORK DONE" in (s.get("run") or "")
        ]
        self.assertEqual(len(no_work), 1, "no step reports a no-work dispatch")
        self.assertIn("::notice::", no_work[0]["run"])
        self.assertIn("GITHUB_STEP_SUMMARY", no_work[0]["run"])

    def test_both_refusal_paths_set_no_work(self):
        run = step_named(self.RESOLVE)["run"]
        self.assertGreaterEqual(run.count("no_work=true"), 2)

    def test_the_noop_path_never_reaches_the_fixer(self):
        # go=false is what every downstream step is guarded on.
        run = step_named(self.RESOLVE)["run"]
        self.assertRegex(run, r'ACTION.*noop|noop.*ACTION')
        self.assertIn("go=false", run)


class DocumentedRecoveryTest(unittest.TestCase):
    """(AC4) The two recoveries are written down as one procedure."""

    def doc(self) -> str:
        self.assertTrue(os.path.exists(DOC), "docs/held-pr-recovery.md is missing")
        return open(DOC, encoding="utf-8").read()

    def test_the_doc_names_both_recoveries(self):
        body = self.doc()
        self.assertIn("Operator decision", body)
        self.assertIn("workflow run", body)

    def test_the_doc_says_they_are_not_independent(self):
        body = self.doc().lower()
        self.assertIn("not a second", body)
        self.assertIn("dre-2813", body)

    def test_the_doc_states_what_a_hand_dispatch_now_does(self):
        self.assertIn(fix_context.NOOP_TAG, self.doc())

    def test_the_readme_points_at_the_doc(self):
        self.assertIn("docs/held-pr-recovery.md",
                      open(README, encoding="utf-8").read())

    def test_the_hold_comments_say_a_hand_dispatch_is_not_a_way_out(self):
        src = wf_src()
        for marker in ("🛑 Fix budget exhausted",
                       "🛑 Conflict-resolution budget exhausted"):
            with self.subTest(marker=marker):
                m = re.search(re.escape(marker) + r'(.*?)"', src, re.S)
                self.assertIsNotNone(m, f"hold body {marker!r} not found")
                self.assertIn(fix_context.HAND_DISPATCH_NOTICE, m.group(1))

    def test_the_hold_notice_survives_a_double_quoted_shell_body(self):
        for ch in ("`", "$", '"', "\\"):
            self.assertNotIn(ch, fix_context.HAND_DISPATCH_NOTICE)


if __name__ == "__main__":
    unittest.main()
