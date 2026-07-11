"""Red-main auto-repair — the stale-assertion hand-walk, codified (DRE-1927).

Today's reproduction (2026-07-11): a merged change made an assertion in the
product's suite stale, main went red, and a human found it first. This
scenario test walks the whole detect → diagnose → fix → merge loop through
the REAL decision code (red_main_repair, repair_context, merge_gate) so the
end-to-end path the card demands is proven at every mechanical joint:

  1. the failure event dispatches exactly one repair (attempt 1, branch
     repair/<failing-sha>);
  2. the medic's auto-retry of the same red run does NOT double-dispatch
     (debounce + in-flight lock);
  3. the repair PR — which updates the stale test — reaches the critic WITH
     the original failing log and the mechanical test-touch flag demanding a
     verified stale-test justification;
  4. the merge gate merges it only on green CI + a qa-bot APPROVE bound to
     the head (author != merger);
  5. after the merge, a re-run event for the old failing SHA is inert; and
  6. the infra twin (a rate-limited run) never dispatches at all.

The LLM joints (the fix agent writing the diff, the critic judging the
justification) are steered by prompts pinned in test_red_main_repair_wiring
and test_repair_review_gate; everything deterministic is exercised here.
"""

import os
import sys
import unittest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

import merge_gate  # noqa: E402
import red_main_repair  # noqa: E402
import repair_context  # noqa: E402

FAILING_SHA = "d" * 40
FIX_HEAD_SHA = "e" * 40
QA_LOGIN = "agent-bureau-qa-bot[bot]"

# The reproduction's shape: a stale assertion — code moved to 4, test pinned 3.
STALE_ASSERTION_LOG = """\
tests/test_widgets.py::test_widget_count FAILED
    def test_widget_count():
>       assert count_widgets() == 3
E       assert 4 == 3
==== 1 failed, 87 passed in 12.31s ====
"""

RATE_LIMIT_LOG = "API rate limit exceeded for installation ID 4266537"


class StaleAssertionEndToEndTest(unittest.TestCase):
    def test_step_1_red_main_dispatches_one_repair(self):
        d = red_main_repair.decide(
            conclusion="failure", head_branch="main", default_branch="main",
            head_sha=FAILING_SHA, log_text=STALE_ASSERTION_LOG,
            refs=[], pulls=[],
        )
        self.assertTrue(d["go"])
        self.assertEqual(d["branch"], f"repair/{FAILING_SHA}")
        self.assertEqual(d["attempt"], 1)

    def test_step_2_the_medic_retry_does_not_double_dispatch(self):
        # The medic reruns the failed main run once; its second failure event
        # arrives while the repair is being built. Branch exists, PR open →
        # in-flight lock; branch exists, PR not yet open → SHA debounce.
        in_flight = red_main_repair.decide(
            conclusion="failure", head_branch="main", default_branch="main",
            head_sha=FAILING_SHA, log_text=STALE_ASSERTION_LOG,
            refs=[f"repair/{FAILING_SHA}"],
            pulls=[{"head_ref": f"repair/{FAILING_SHA}", "state": "open",
                    "merged": False}],
        )
        self.assertFalse(in_flight["go"])
        self.assertEqual(in_flight["reason"], "repair-in-flight")

        pre_pr = red_main_repair.decide(
            conclusion="failure", head_branch="main", default_branch="main",
            head_sha=FAILING_SHA, log_text=STALE_ASSERTION_LOG,
            refs=[f"repair/{FAILING_SHA}"], pulls=[],
        )
        self.assertFalse(pre_pr["go"])
        self.assertEqual(pre_pr["reason"], "duplicate-event")

    def test_step_3_the_critic_sees_the_log_and_the_test_touch_flag(self):
        # The fix updates the stale test — a test-touching diff, exactly the
        # shape that must carry a verified stale-test justification.
        ctx = repair_context.build_context(
            f"repair/{FAILING_SHA}",
            ["tests/test_widgets.py"],
            STALE_ASSERTION_LOG,
        )
        self.assertIn("assert 4 == 3", ctx)          # the original evidence
        self.assertIn("tests/test_widgets.py", ctx)  # the flagged edit
        self.assertIn("stale", ctx.lower())          # the claim to verify
        self.assertIn("VERDICT: REQUEST_CHANGES", ctx)

    def test_step_4_the_merge_gate_merges_only_on_green_plus_approve(self):
        # compare_status="ahead" satisfies branch currency (DRE-1924
        # condition 0) — the repair branch was cut from the failing head, so
        # a live repair PR is current by construction.
        checks = [{"status": "completed", "conclusion": "success",
                   "check_suite": {"id": 7}}]
        approve = [{
            "user": {"login": QA_LOGIN},
            "body": f"🔎 QA Critic — VERDICT: APPROVE @{FIX_HEAD_SHA}\n\nok",
        }]
        self.assertEqual(
            merge_gate.decide(FIX_HEAD_SHA, QA_LOGIN, checks, approve,
                              compare_status="ahead").action,
            "merge",
        )
        # A test-gutting rejection stands as a hold — the fix loop, not the
        # merge, is the only way forward.
        reject = [{
            "user": {"login": QA_LOGIN},
            "body": (f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{FIX_HEAD_SHA}"
                     "\n\nweakened assertion, no stale justification"),
        }]
        self.assertEqual(
            merge_gate.decide(FIX_HEAD_SHA, QA_LOGIN, checks, reject,
                              compare_status="ahead").action,
            "hold",
        )

    def test_step_5_after_the_merge_the_old_failure_event_is_inert(self):
        d = red_main_repair.decide(
            conclusion="failure", head_branch="main", default_branch="main",
            head_sha=FAILING_SHA, log_text=STALE_ASSERTION_LOG,
            refs=[],
            pulls=[{"head_ref": f"repair/{FAILING_SHA}", "state": "closed",
                    "merged": True}],
        )
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "already-repaired")

    def test_step_6_the_infra_twin_backs_off_entirely(self):
        d = red_main_repair.decide(
            conclusion="failure", head_branch="main", default_branch="main",
            head_sha=FAILING_SHA, log_text=RATE_LIMIT_LOG,
            refs=[], pulls=[],
        )
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "infra-backoff")


class FindOpenDedupTest(unittest.TestCase):
    """linear_ops find-open — the budget-exhausted escalation must not mint a
    new triage card per duplicate event; the workflow looks up an existing
    open card by exact title first."""

    def _with_fake_gql(self, nodes):
        import linear_ops

        calls = {}

        def fake_gql(query, variables=None):
            calls["query"] = query
            calls["variables"] = variables
            return {"issues": {"nodes": nodes}}

        return linear_ops, fake_gql, calls

    def test_prints_existing_open_card(self):
        linear_ops, fake, calls = self._with_fake_gql(
            [{"identifier": "DRE-9999"}]
        )
        original = linear_ops.gql
        linear_ops.gql = fake
        try:
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                linear_ops.cmd_find_open("Red main needs a human")
            self.assertEqual(buf.getvalue().strip(), "DRE-9999")
            # Done/canceled cards must not suppress a fresh escalation.
            self.assertIn("completed", calls["query"])
            self.assertIn("canceled", calls["query"])
        finally:
            linear_ops.gql = original

    def test_prints_nothing_when_no_open_card(self):
        linear_ops, fake, _ = self._with_fake_gql([])
        original = linear_ops.gql
        linear_ops.gql = fake
        try:
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                linear_ops.cmd_find_open("Red main needs a human")
            self.assertEqual(buf.getvalue().strip(), "")
        finally:
            linear_ops.gql = original


if __name__ == "__main__":
    unittest.main()
