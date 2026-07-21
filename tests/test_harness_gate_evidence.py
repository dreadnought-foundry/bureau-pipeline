"""DRE-2103 acceptance evidence: the harness holds and releases the merge.

The card requires proof that a red harness run holds the merge gate and a
green one releases it, with NO branch-protection change. The mechanism is
merge_gate.py condition 1 — every check run on the PR's head sha must
complete green — plus one fact these tests pin so it cannot silently rot:
harness.yml is NOT a review workflow, so its check run is COUNTED (adding
it to the review-workflow allowlist would exclude it and un-wire the gate).

The dependabot arm matters just as much: a dependabot-triggered
pull_request gets the empty Dependabot secrets store, so harness.yml
self-skips at the job level (tests/test_harness_wiring.py) — the skipped
check run concludes `skipped`, which condition 1 treats as green. The skip
must never hold a dependency PR hostage.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import merge_gate  # noqa: E402

SHA = "b" * 40
QA = "agent-bureau-qa-bot[bot]"


def _harness_run(conclusion, status="completed"):
    return {
        "name": "harness",
        "status": status,
        "conclusion": conclusion,
        "check_suite": {"id": 111},
    }


def _ci_run():
    return {
        "name": "scripts unit tests",
        "status": "completed",
        "conclusion": "success",
        "check_suite": {"id": 222},
    }


def _approve():
    return [
        {
            "user": {"login": QA},
            "body": f"🔎 QA Critic — VERDICT: APPROVE @{SHA}",
        }
    ]


def _decide(harness_conclusion, status="completed"):
    return merge_gate.decide(
        head_sha=SHA,
        qa_login=QA,
        check_runs=[_ci_run(), _harness_run(harness_conclusion, status)],
        comments=_approve(),
        review_suites=frozenset(),
        compare_status="ahead",
    )


class HarnessHoldsTheGateTest(unittest.TestCase):
    def test_red_harness_holds_the_merge_even_with_a_bound_approve(self):
        decision = _decide("failure")
        self.assertEqual(decision.action, "wait")
        self.assertIn("not green", decision.reason)

    def test_in_flight_harness_holds_the_merge(self):
        decision = _decide(None, status="in_progress")
        self.assertEqual(decision.action, "wait")

    def test_green_harness_releases_the_merge(self):
        self.assertEqual(_decide("success").action, "merge")

    def test_dependabot_self_skip_reads_green_never_a_hold(self):
        # The job-level skip on dependabot-triggered PR events concludes
        # `skipped` — condition 1's GREEN_CONCLUSIONS admits it, so the
        # empty-secrets skip can never wedge a dependency PR.
        self.assertEqual(_decide("skipped").action, "merge")

    def test_harness_is_not_an_excluded_review_workflow(self):
        # The load-bearing fact: exclusion by verified origin (DRE-1994)
        # only reaches the review workflows. If harness.yml ever joined
        # that allowlist its red would become invisible to the gate.
        self.assertNotIn(
            ".github/workflows/harness.yml", merge_gate.DEFAULT_REVIEW_WORKFLOWS
        )


if __name__ == "__main__":
    unittest.main()
