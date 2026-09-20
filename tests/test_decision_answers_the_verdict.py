"""An operator decision is read against the VERDICT it answers, not against
the bot's own later receipt (DRE-3412).

The fix loop escalates in two acts that are not simultaneous: the critic posts
`VERDICT: REQUEST_CHANGES`, the loop runs, and — seconds to minutes later — the
worker bot posts its 🛑 receipt saying it is holding for a human. An operator
watching the PR reads the verdict and answers it. When the answer lands in the
gap between the two, `operator_decision` threw it away: the rule was "newer
than the latest 🛑", so a receipt posted six seconds AFTER a correct answer
cancelled it. The person answered; the pipeline waited for a second answer,
and nothing said so.

The rule this suite pins:

  * a decision counts when it is newer than the VERDICT the escalation is
    about, whether the 🛑 receipt landed before or after it;
  * a decision older than that verdict still does not count — it answered an
    earlier round, and the loop has since been told something new;
  * the 🛑 receipt for the round being answered does not CONSUME the answer
    (it is the question, not the loop moving on the answer), while DRE-2813's
    rule is otherwise untouched: every other worker-bot comment newer than a
    standing answer still consumes it, a fresh escalation included;
  * the comment-triggered restart (DRE-3451, `--decision-trigger`) and the
    reconcile sweep (DRE-2409, `restart_answered_blockers`) read the same
    predicates, so the two routes cannot disagree about the same thread.

Nothing here touches where an escalated card parks — that is settled policy
(DRE-2776) and `tests/test_escalations_park_in_green_light.py` owns it.
"""

import json
import os
import subprocess  # nosec B404 — fixed-arg CLI invocation of our own script
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(REPO_ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import fix_context  # noqa: E402
import merge_gate  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402

WORKER = reconcile.WORKER_REST_LOGIN
QA = "agent-bureau-qa-bot[bot]"
HUMAN = "sid-ceo"

SHA = "a" * 40

# The live sequence from the card, to the second: the verdict, the answer ten
# seconds later, the bot's receipt six seconds after THAT.
T_VERDICT = "2026-09-13T18:00:00Z"
T_DECISION = "2026-09-13T18:00:10Z"
T_RECEIPT = "2026-09-13T18:00:16Z"

VERDICT_BODY = (
    f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: REQUEST_CHANGES cause:defect "
    f"@{SHA}"
)
BLOCKER_BODY = (
    "🛑 Fix budget exhausted — 2 review rounds in a row made no progress. "
    "Holding for a human decision."
)
DECISION_BODY = (
    "**Operator decision** — the critic is right, drop the A path and keep "
    "the sweep's own validation."
)


def comment(login, body, created="2026-09-13T18:00:00Z"):
    """A GitHub issue comment in REST shape — what fix_context parses and the
    sweep fetches (the helper shape tests/test_fix_context.py uses)."""
    return {
        "user": {
            "login": login,
            "type": "Bot" if login.endswith("[bot]") else "User",
        },
        "body": body,
        "created_at": created,
    }


def answered_before_the_receipt() -> list:
    """The card's thread: verdict at T, decision at T+10s, 🛑 at T+16s."""
    return [
        comment(QA, VERDICT_BODY, T_VERDICT),
        comment(HUMAN, DECISION_BODY, T_DECISION),
        comment(WORKER, BLOCKER_BODY, T_RECEIPT),
    ]


def answered_the_earlier_round() -> list:
    """The stale shape: the answer predates the verdict that caused THIS
    escalation, so it answered a round the loop has already moved past."""
    return [
        comment(QA, VERDICT_BODY, "2026-09-13T17:00:00Z"),
        comment(WORKER, "🔧 Fix attempt 1 pushed — CI and critic re-running.",
                "2026-09-13T17:10:00Z"),
        comment(HUMAN, DECISION_BODY, "2026-09-13T17:20:00Z"),
        comment(QA, VERDICT_BODY.replace(SHA, "b" * 40), T_VERDICT),
        comment(WORKER, BLOCKER_BODY, T_RECEIPT),
    ]


def decide(comments: list) -> tuple:
    """Run the CLI agent-fix's first step runs. Returns (verdict, reason)."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "thread.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(comments, fh)
        proc = subprocess.run(  # nosec B603 — fixed argv, shell=False
            [sys.executable, os.path.join(SCRIPTS, "fix_context.py"),
             "--decision-trigger", "--comments-file", path,
             "--worker-login", WORKER],
            capture_output=True, text=True, check=False,
        )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    return lines[0], "\n".join(lines[2:])


def sweep(comments: list) -> list:
    """Run the answered-blocker sweep over a fake GitHub holding one open
    agent PR whose thread is `comments`. Returns the dispatches it made."""
    dispatches = []
    prs = [{"number": 3412, "headRefName": "agent/DRE-3412-x",
            "mergeStateStatus": "BLOCKED"}]

    def gh(*args):
        if args[:2] == ("run", "list"):
            return "[]"
        if args[:2] == ("pr", "list"):
            return json.dumps(prs)
        if args[0] == "api" and args[-1].endswith("/contents/.github/workflows"):
            # This repo HAS its fix stub. Since DRE-4378 the sweep asks once
            # per pass before dispatching; a repo whose listing provably lacks
            # it is held for a person instead.
            return json.dumps([{"name": reconcile.fix_workflow(), "type": "file"}])
        if args[0] == "api":
            return json.dumps(comments)
        return ""

    with mock.patch.object(reconcile, "gh", side_effect=gh), \
         mock.patch.object(reconcile, "gh_dispatch",
                           side_effect=lambda *a: dispatches.append(a)), \
         mock.patch.object(reconcile, "_post_pr_note", return_value=True), \
         mock.patch.object(reconcile, "card_parked_for_human", return_value=True), \
         mock.patch.object(reconcile, "linear_ops", mock.MagicMock()):
        reconcile.restart_answered_blockers()
    return dispatches


class DecisionBeforeTheReceiptCountsTest(unittest.TestCase):
    """AC1: verdict at T, decision at T+10s, 🛑 receipt at T+16s — the answer
    counts and the loop proceeds on it."""

    def test_the_decision_is_selected(self):
        thread = answered_before_the_receipt()
        self.assertIs(
            fix_context.operator_decision(thread, WORKER), thread[1],
            "an answer posted before the bot's receipt was thrown away",
        )

    def test_the_decision_is_standing_not_consumed(self):
        # The 🛑 is the QUESTION, not the loop moving on the answer. Reading it
        # as consumption is the same bug one layer down.
        thread = answered_before_the_receipt()
        self.assertFalse(
            fix_context.decision_consumed(thread, thread[1], WORKER)
        )
        self.assertIs(fix_context.standing_decision(thread, WORKER), thread[1])

    def test_the_comment_trigger_proceeds(self):
        # AC3: the DRE-3451 comment-triggered restart reads the same rule.
        verdict, reason = decide(answered_before_the_receipt())
        self.assertEqual(verdict, fix_context.TRIGGER_PROCEED, reason)
        self.assertIn(fix_context.TRIGGER_STANDING, reason)

    def test_the_sweep_restarts_the_loop(self):
        # And so does the DRE-2409 backstop — one reading, both routes.
        dispatches = sweep(answered_before_the_receipt())
        self.assertEqual(len(dispatches), 1,
                         "the sweep left a correct answer unanswered")
        self.assertIn("pr_number=3412", " ".join(dispatches[0]))

    def test_the_render_tells_the_fixer_the_blocker_is_answered(self):
        # The fix prompt carries exactly one status line, and it must be the
        # override — a fixer told UNANSWERED re-derives the blocker it was
        # just given the answer to.
        rendered = fix_context.render(answered_before_the_receipt(), WORKER)
        self.assertIn(fix_context.STATUS_OVERRIDE, rendered)
        self.assertNotIn(fix_context.STATUS_UNANSWERED, rendered)


class DecisionOlderThanItsVerdictDoesNotCountTest(unittest.TestCase):
    """AC2: an answer posted BEFORE the verdict it would answer is stale — it
    spoke to an earlier round."""

    def test_no_decision_is_selected(self):
        self.assertIsNone(
            fix_context.operator_decision(answered_the_earlier_round(), WORKER)
        )

    def test_the_comment_trigger_skips_and_names_the_predicate(self):
        verdict, reason = decide(answered_the_earlier_round())
        self.assertEqual(verdict, fix_context.TRIGGER_SKIP)
        self.assertIn(fix_context.SKIP_BEFORE_VERDICT, reason)

    def test_the_sweep_dispatches_nothing(self):
        self.assertEqual(sweep(answered_the_earlier_round()), [])


class TheConsumptionRuleIsOtherwiseUntouchedTest(unittest.TestCase):
    """DRE-2813 stands: every worker-bot comment newer than a standing answer
    still consumes it, once the escalation it answers has been posted."""

    def test_a_fresh_escalation_after_the_answer_consumes_it(self):
        # The loop ran again and blocked again — that IS the loop moving.
        thread = answered_before_the_receipt() + [
            comment(WORKER, "🛑 Fix attempt 4 blocked: this still needs a call.",
                    "2026-09-13T19:00:00Z")
        ]
        self.assertIsNone(fix_context.standing_decision(thread, WORKER))
        self.assertEqual(decide(thread)[0], fix_context.TRIGGER_SKIP)

    def test_the_restart_receipt_consumes_it(self):
        # Idempotence: one answer buys exactly one dispatch.
        thread = answered_before_the_receipt() + [
            comment(WORKER, pipeline_act.receipt(
                "fix-loop-restarted",
                f"🔓 {pipeline_act.tag('fix-loop-restarted')}: picked up."),
                "2026-09-13T18:01:00Z")
        ]
        self.assertIsNone(fix_context.standing_decision(thread, WORKER))
        self.assertEqual(sweep(thread), [])

    def test_the_no_work_notice_still_does_not_consume_it(self):
        thread = answered_before_the_receipt() + [
            comment(WORKER,
                    f"ℹ️ {fix_context.NOOP_TAG}: an answer is already standing.",
                    "2026-09-13T18:01:00Z")
        ]
        self.assertIsNotNone(fix_context.standing_decision(thread, WORKER))

    def test_a_bot_authored_answer_is_still_refused(self):
        # DRE-1988/1995: authorship decides meaning, and widening the window
        # must not widen who may speak into it.
        thread = [
            comment(QA, VERDICT_BODY, T_VERDICT),
            comment("github-actions[bot]", DECISION_BODY, T_DECISION),
            comment(WORKER, BLOCKER_BODY, T_RECEIPT),
        ]
        self.assertIsNone(fix_context.operator_decision(thread, WORKER))
        self.assertIn(fix_context.SKIP_BOT_AUTHOR, decide(thread)[1])


class TheVerdictAnchorCannotBeForgedTest(unittest.TestCase):
    """The anchor decides which answers are live, so it is read with the same
    grammar the merge gate reads a verdict with (DRE-1992/1998)."""

    def test_a_human_cannot_plant_a_verdict_to_move_the_anchor(self):
        # A person writing the critic's words is not the critic. If the plant
        # anchored, the genuine 17:20 answer would read as stale and the
        # operator would be asked to answer a second time — this card's own
        # defect, handed to anyone who can leave a comment.
        thread = [
            comment(QA, VERDICT_BODY, "2026-09-13T17:00:00Z"),
            comment(HUMAN, DECISION_BODY, "2026-09-13T17:20:00Z"),
            comment(HUMAN, VERDICT_BODY, "2026-09-13T17:30:00Z"),
            comment(WORKER, BLOCKER_BODY, T_RECEIPT),
        ]
        self.assertIs(fix_context.operator_decision(thread, WORKER), thread[1])

    def test_a_quoted_verdict_is_inert(self):
        # merge_gate's anchor: a comment that MENTIONS a verdict is not one.
        thread = [
            comment(QA, VERDICT_BODY, "2026-09-13T17:00:00Z"),
            comment(HUMAN, DECISION_BODY, "2026-09-13T17:20:00Z"),
            comment(QA, f"> {VERDICT_BODY}\n\nquoting the last round.",
                    "2026-09-13T17:30:00Z"),
            comment(WORKER, BLOCKER_BODY, T_RECEIPT),
        ]
        # The quote is not a verdict, so the anchor is still the real one at
        # 17:00 and the 17:20 answer is live.
        self.assertIs(fix_context.operator_decision(thread, WORKER), thread[1])

    def test_a_could_not_run_notice_is_not_a_verdict(self):
        # The critic's neutral infra notice opens with the same marker and
        # carries no VERDICT: token — it decided nothing, so it anchors
        # nothing (scripts/medic_classify.py CRITIC_NEUTRAL_MARKER).
        thread = [
            comment(QA, VERDICT_BODY, "2026-09-13T17:00:00Z"),
            comment(HUMAN, DECISION_BODY, "2026-09-13T17:20:00Z"),
            comment(QA, f"🔎 {merge_gate.CRITIC_MARKER} could not run (infra "
                        "error) — re-review needed.", "2026-09-13T17:30:00Z"),
            comment(WORKER, BLOCKER_BODY, T_RECEIPT),
        ]
        self.assertIs(fix_context.operator_decision(thread, WORKER), thread[1])

    def test_a_thread_with_no_verdict_falls_back_to_the_blocker(self):
        # A fix loop started by a merge conflict has no critic verdict to
        # anchor on. The pre-DRE-3412 reading is what is left, unchanged.
        thread = [
            comment(HUMAN, DECISION_BODY, "2026-09-13T17:20:00Z"),
            comment(WORKER, BLOCKER_BODY, T_RECEIPT),
        ]
        self.assertIsNone(fix_context.operator_decision(thread, WORKER))
        self.assertIn(fix_context.SKIP_BEFORE_VERDICT, decide(thread)[1])

    def test_a_thread_with_no_blocker_still_has_no_decision_scope(self):
        # No escalation, nothing to answer — a verdict alone opens no window.
        thread = [
            comment(QA, VERDICT_BODY, T_VERDICT),
            comment(HUMAN, DECISION_BODY, T_DECISION),
        ]
        self.assertIsNone(fix_context.operator_decision(thread, WORKER))
        self.assertIn(fix_context.SKIP_NO_BLOCKER, decide(thread)[1])


if __name__ == "__main__":
    unittest.main()
