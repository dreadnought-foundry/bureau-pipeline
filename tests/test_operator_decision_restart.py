"""Answering a held fix loop must RESTART it, and the question must state the
answer format (DRE-2409).

Both live incidents (portico PR #132 / DRE-2199, agent-bureau PR #2034 /
DRE-2399) needed TWO human acts: write the decision in the exact shape the
parser wanted, then hand-`workflow_dispatch` the fix loop. Nothing re-fires
agent-fix once the REQUEST_CHANGES event is consumed — the same dead-zone
retry_dead_fix_runs and fix_approved_but_red already cover for other causes.

This suite pins the two halves that close the door:

  * reconcile.restart_answered_blockers() — an open agent PR whose latest
    fix-loop blocker now has a human operator decision after it gets ONE
    re-dispatch, receipted on the PR, and its card released from the human
    queue. It deliberately IGNORES the DRE-2024 human-park gate: the card is
    parked *because* of the blocker, and the operator's answer is exactly the
    human act that gate waits for.
  * a human comment that mentions "operator decision" but does not parse gets
    a visible near-miss notice on the PR instead of silence.
  * every blocker comment that parks a PR quotes the copy-pasteable answer
    format, so the operator never needs to know a parser exists.
"""

import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import fix_context  # noqa: E402
import reconcile  # noqa: E402

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-fix.yml"
)

WORKER = "agent-bureau-bot[bot]"
QA = "agent-bureau-qa-bot[bot]"
HUMAN = "sid-ceo"

BLOCKER = (
    "🛑 Fix attempt 3 blocked: the critic wants B, the card says A — this "
    "needs the operator's call."
)
# The body that failed live on portico #132: closing markers at the end.
DECISION = "**Operator decision — the blocker is answered. Re-arm the fix loop.**"
NEAR_MISS = "I read the thread — an operator decision here is really Sid's call."


def rest(login, body):
    """A comment in REST shape (user.login / user.type) — the shape
    fix_context parses, and the shape the sweep fetches."""
    return {
        "user": {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"},
        "body": body,
        "created_at": "2026-08-12T00:00:00Z",
    }


def _pr(number=7, branch="agent/DRE-2409-x", mstate="BLOCKED"):
    return {"number": number, "headRefName": branch, "mergeStateStatus": mstate}


def sweep(prs, comments, busy="[]", parked=True):
    """Run the backstop against a fake GitHub. `comments` maps PR number →
    REST comment list."""
    dispatches, notes = [], []

    def gh(*args):
        if args[:2] == ("run", "list"):
            return busy
        if args[:2] == ("pr", "list"):
            return json.dumps(prs)
        if args[0] == "api":
            m = re.search(r"issues/(\d+)/comments", " ".join(args))
            return json.dumps(comments.get(int(m.group(1)), [])) if m else "[]"
        return ""

    linear = mock.MagicMock()
    with mock.patch.object(reconcile, "gh", side_effect=gh), \
         mock.patch.object(reconcile, "gh_dispatch",
                           side_effect=lambda *a: dispatches.append(a)), \
         mock.patch.object(reconcile, "_post_pr_note",
                           side_effect=lambda n, b: notes.append((n, b)) or True), \
         mock.patch.object(reconcile, "card_parked_for_human", return_value=parked), \
         mock.patch.object(reconcile, "linear_ops", linear):
        reconcile.restart_answered_blockers()
    return dispatches, notes, linear


class AnsweredBlockerRestartsTest(unittest.TestCase):
    """(AC5) answered blocker → dispatch, without a hand dispatch."""

    ANSWERED = {7: [rest(WORKER, BLOCKER), rest(HUMAN, DECISION)]}

    def test_answered_blocker_dispatches_the_fix_agent(self):
        dispatches, _, _ = sweep([_pr()], self.ANSWERED)
        self.assertEqual(len(dispatches), 1)
        joined = " ".join(dispatches[0])
        self.assertIn("agent-fix.yml", joined)
        self.assertIn("pr_number=7", joined)

    def test_dispatch_ignores_the_human_park_gate(self):
        # The card IS parked — that is the state an answered blocker leaves it
        # in. Honouring DRE-2024's gate here would keep the door shut forever.
        dispatches, _, _ = sweep([_pr()], self.ANSWERED, parked=True)
        self.assertEqual(len(dispatches), 1)

    def test_a_receipt_lands_on_the_pr(self):
        _, notes, _ = sweep([_pr()], self.ANSWERED)
        self.assertEqual(len(notes), 1)
        number, body = notes[0]
        self.assertEqual(number, 7)
        self.assertIn(reconcile.DECISION_RESTART_TAG, body)

    def test_the_card_is_released_from_the_human_queue(self):
        _, _, linear = sweep([_pr()], self.ANSWERED)
        linear.remove_label.assert_called_once_with("DRE-2409", reconcile.HOLD_LABEL)
        linear.cmd_advance.assert_called_once_with(
            "DRE-2409", "In QA", reconcile.PARKED_STATE
        )
        self.assertTrue(linear.cmd_comment.called)

    def test_dispatch_precedes_the_receipt(self):
        # A receipt posted before a dispatch that 403s would silence the
        # retry forever (the DRE-1254 false-receipt class).
        order = []
        prs, comments = [_pr()], self.ANSWERED

        def gh(*args):
            if args[:2] == ("run", "list"):
                return "[]"
            if args[:2] == ("pr", "list"):
                return json.dumps(prs)
            if args[0] == "api":
                return json.dumps(comments[7])
            return ""

        with mock.patch.object(reconcile, "gh", side_effect=gh), \
             mock.patch.object(reconcile, "gh_dispatch",
                               side_effect=lambda *a: order.append("dispatch")), \
             mock.patch.object(reconcile, "_post_pr_note",
                               side_effect=lambda n, b: order.append("receipt")), \
             mock.patch.object(reconcile, "card_parked_for_human", return_value=True), \
             mock.patch.object(reconcile, "linear_ops", mock.MagicMock()):
            reconcile.restart_answered_blockers()
        self.assertEqual(order, ["dispatch", "receipt"])


class NoRestartTest(unittest.TestCase):
    """The sweep stays quiet on everything that is not a fresh answer."""

    def dispatches(self, comments, **kw):
        return sweep([_pr()], {7: comments}, **kw)[0]

    def test_no_decision_no_dispatch(self):
        self.assertEqual(
            self.dispatches([rest(WORKER, BLOCKER),
                             rest(HUMAN, "any news on this?")]), []
        )

    def test_no_blocker_no_dispatch(self):
        # A stray decision on a healthy PR steers nothing (unchanged rule).
        self.assertEqual(self.dispatches([rest(HUMAN, DECISION)]), [])

    def test_decision_already_consumed_is_not_re_dispatched(self):
        # Any worker-bot comment newer than the decision means the loop
        # already moved — including this sweep's own receipt.
        consumed = [
            rest(WORKER, BLOCKER),
            rest(HUMAN, DECISION),
            rest(WORKER, f"🔓 {reconcile.DECISION_RESTART_TAG}: re-dispatched."),
        ]
        self.assertEqual(self.dispatches(consumed), [])

    def test_decision_older_than_the_latest_blocker_is_not_dispatched(self):
        stale = [
            rest(WORKER, BLOCKER),
            rest(HUMAN, DECISION),
            rest(WORKER, "🛑 Fix attempt 4 blocked: still torn."),
        ]
        self.assertEqual(self.dispatches(stale), [])

    def test_bot_authored_decision_is_not_a_decision(self):
        for login in (WORKER, QA, "github-actions[bot]"):
            with self.subTest(login):
                planted = [rest(WORKER, BLOCKER), rest(login, DECISION)]
                self.assertEqual(self.dispatches(planted), [])

    def test_skips_non_agent_branches(self):
        prs = [_pr(branch="feature/manual")]
        answered = {7: [rest(WORKER, BLOCKER), rest(HUMAN, DECISION)]}
        self.assertEqual(sweep(prs, answered)[0], [])

    def test_backs_off_while_a_fix_run_is_busy(self):
        busy = json.dumps([{"status": "in_progress"}])
        answered = [rest(WORKER, BLOCKER), rest(HUMAN, DECISION)]
        self.assertEqual(self.dispatches(answered, busy=busy), [])

    def test_one_dispatch_per_sweep(self):
        prs = [_pr(number=7), _pr(number=8, branch="agent/DRE-2410-y")]
        answered = [rest(WORKER, BLOCKER), rest(HUMAN, DECISION)]
        self.assertEqual(len(sweep(prs, {7: answered, 8: answered})[0]), 1)


class DirtyPrTest(unittest.TestCase):
    """A conflicted PR belongs to unstick_conflicts — but that sweep is
    park-gated, so the answer still has to open the door for it."""

    def test_dirty_pr_is_released_but_not_dispatched_here(self):
        answered = {7: [rest(WORKER, BLOCKER), rest(HUMAN, DECISION)]}
        dispatches, notes, linear = sweep([_pr(mstate="DIRTY")], answered)
        self.assertEqual(dispatches, [])
        self.assertEqual(len(notes), 1)  # receipted, so it happens once
        linear.remove_label.assert_called_once_with("DRE-2409", reconcile.HOLD_LABEL)


class NearMissNoticeTest(unittest.TestCase):
    """(AC6) A near miss is announced on the PR, not swallowed."""

    NEAR = {7: [rest(WORKER, BLOCKER), rest(HUMAN, NEAR_MISS)]}

    def test_near_miss_posts_a_notice_and_does_not_dispatch(self):
        dispatches, notes, _ = sweep([_pr()], self.NEAR)
        self.assertEqual(dispatches, [])
        self.assertEqual(len(notes), 1)
        number, body = notes[0]
        self.assertEqual(number, 7)
        self.assertIn(fix_context.NEAR_MISS_TAG, body)
        self.assertIn(fix_context.DECISION_EXAMPLE, body)

    def test_near_miss_is_announced_once(self):
        already = {7: self.NEAR[7] + [
            rest(WORKER, fix_context.near_miss_notice(
                [rest(HUMAN, NEAR_MISS)]
            )),
        ]}
        self.assertEqual(sweep([_pr()], already)[1], [])

    def test_a_newer_near_miss_is_announced_again(self):
        again = {7: self.NEAR[7] + [
            rest(WORKER, fix_context.near_miss_notice([rest(HUMAN, NEAR_MISS)])),
            rest(HUMAN, "still waiting — is this not an operator decision?"),
        ]}
        self.assertEqual(len(sweep([_pr()], again)[1]), 1)

    def test_a_real_decision_beats_a_near_miss_on_the_same_pr(self):
        both = {7: [rest(WORKER, BLOCKER), rest(HUMAN, NEAR_MISS),
                    rest(HUMAN, DECISION)]}
        dispatches, notes, _ = sweep([_pr()], both)
        self.assertEqual(len(dispatches), 1)
        self.assertNotIn(fix_context.NEAR_MISS_TAG, notes[0][1])

    def test_no_near_miss_no_noise(self):
        quiet = {7: [rest(WORKER, BLOCKER), rest(HUMAN, "any news?")]}
        self.assertEqual(sweep([_pr()], quiet)[1], [])


class PreFilterTest(unittest.TestCase):
    """The per-PR thread fetch is the sweep's only cost — a PR with nothing
    decision-shaped in it must not pay it."""

    def api_calls(self, pr):
        seen = []

        def gh(*args):
            if args[:2] == ("run", "list"):
                return "[]"
            if args[:2] == ("pr", "list"):
                return json.dumps([pr])
            if args[0] == "api":
                seen.append(args)
                return json.dumps([rest(WORKER, BLOCKER), rest(HUMAN, DECISION)])
            return ""

        with mock.patch.object(reconcile, "gh", side_effect=gh), \
             mock.patch.object(reconcile, "gh_dispatch"), \
             mock.patch.object(reconcile, "_post_pr_note"), \
             mock.patch.object(reconcile, "linear_ops", mock.MagicMock()):
            reconcile.restart_answered_blockers()
        return seen

    def graphql(self, *bodies):
        # gh pr list --json comments is GraphQL-backed: author.login, no
        # "[bot]" suffix (the shape is_worker_bot_comment documents).
        return [{"author": {"login": login}, "body": body} for login, body in bodies]

    def test_quiet_pr_is_not_fetched(self):
        pr = dict(_pr(), comments=self.graphql(
            ("agent-bureau-bot", "🔧 Fix attempt 1 pushed — CI re-running."),
        ))
        self.assertEqual(self.api_calls(pr), [])

    def test_blocked_pr_is_fetched(self):
        pr = dict(_pr(), comments=self.graphql(("agent-bureau-bot", BLOCKER)))
        self.assertEqual(len(self.api_calls(pr)), 1)

    def test_decision_mention_is_fetched(self):
        pr = dict(_pr(), comments=self.graphql((HUMAN, DECISION)))
        self.assertEqual(len(self.api_calls(pr)), 1)

    def test_missing_comments_field_fails_open(self):
        # "We could not see" is not "there is nothing there".
        self.assertEqual(len(self.api_calls(_pr())), 1)


class SweepWiringTest(unittest.TestCase):
    def test_full_sweep_runs_the_backstop(self):
        with mock.patch.object(reconcile, "unstick_conflicts"), \
             mock.patch.object(reconcile, "retrigger_dead_heads"), \
             mock.patch.object(reconcile, "flag_no_checks_prs"), \
             mock.patch.object(reconcile, "fix_approved_but_red"), \
             mock.patch.object(reconcile, "retry_dead_fix_runs"), \
             mock.patch.object(reconcile, "review_dependabot_prs"), \
             mock.patch.object(reconcile, "recover_crashed_reviews"), \
             mock.patch.object(reconcile, "check_dependabot_capacity"), \
             mock.patch.object(reconcile, "restart_answered_blockers") as r, \
             mock.patch.object(reconcile, "flag_stranded", return_value=set()), \
             mock.patch.object(reconcile, "active_cards", return_value=[]), \
             mock.patch.object(reconcile, "promote_ready"), \
             mock.patch.object(reconcile, "backlog_children", return_value=[]):
            reconcile.main()
        r.assert_called_once_with()

    def test_conflicts_only_mode_does_not_run_it(self):
        with mock.patch.object(reconcile, "unstick_conflicts"), \
             mock.patch.object(reconcile, "restart_answered_blockers") as r:
            reconcile.main(conflicts_only=True)
        r.assert_not_called()


def wf_src() -> str:
    return open(WORKFLOW).read()


def body_after(marker: str) -> str:
    """The rest of a `gh pr comment --body "..."` string that starts with
    `marker` (the format carries no double quote — pinned in
    tests/test_operator_decision_intent.py — so the next one ends the body)."""
    m = re.search(re.escape(marker) + r'(.*?)"', wf_src(), re.S)
    assert m, f"comment body starting {marker!r} not found in agent-fix.yml"
    return m.group(1)


class ParkedBlockerStatesTheFormatTest(unittest.TestCase):
    """(AC4) The question states the answer. Every comment that parks a PR
    for a human decision carries the copy-pasteable format."""

    def report_step(self) -> str:
        m = re.search(r"name:\s*Report\b(.*?)(?:\n      - name:|\Z)", wf_src(), re.S)
        self.assertIsNotNone(m, "'Report' step not found in agent-fix.yml")
        return m.group(1)

    def test_report_step_reads_the_format_from_the_one_source(self):
        step = self.report_step()
        self.assertIn("fix_context.py --answer-format", step)

    def test_every_holding_report_body_carries_the_format(self):
        # The dispute blocker, the no-progress blocker and the dead-run hold
        # all park the card; all three must show the operator the way out.
        step = self.report_step()
        self.assertGreaterEqual(step.count("$FORMAT"), 3)

    def test_budget_exhausted_holds_carry_the_format(self):
        for marker in ("🛑 Fix budget exhausted",
                       "🛑 Conflict-resolution budget exhausted"):
            with self.subTest(marker):
                self.assertIn(fix_context.DECISION_EXAMPLE, body_after(marker))

    def test_the_dispute_card_comment_points_at_the_pr_answer(self):
        # DRE-2307 interaction: the card tells the operator there is a
        # dispute, so the card comment must say where an answer goes.
        self.assertIn(fix_context.DECISION_EXAMPLE,
                      body_after("🙋 The fix agent disagrees"))

    def test_prompt_tells_the_fixer_to_surface_a_near_miss(self):
        self.assertRegex(wf_src(), r"(?i)near[- ]miss")


if __name__ == "__main__":
    unittest.main()
