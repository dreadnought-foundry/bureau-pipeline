"""The merge gate and the harness — what each owes the other (DRE-4149).

This file was DRE-2103's acceptance evidence: "a red harness run holds the
merge and a green one releases it, with NO branch-protection change". That
was true because harness.yml ran on pull requests, and merge_gate.py
condition 1 counts every check run on the head.

DRE-4149 took the harness off pull requests — it proves `main`, where its
verdict decides whether `stable` advances (tests/test_harness_proves_main.py,
tests/test_promote_channel.py). So the question this file answers changed
from "does a red harness hold a PR?" to the one that matters now:

    **does a PR whose head carries NO harness check merge?**

It does, and not by a special case. The gate has never kept a list of checks
it expects to see; condition 1 reads the check runs that ARE on the head and
requires those to be green. Nothing was required of the harness by name, in
this repo or in GitHub's branch protection (read 2026-09-17: the required
contexts on bureau-pipeline's `main` are `scripts unit tests` and `TDD commit
discipline`), so nothing waits for a check that will never report.

The honest other half is pinned too, because it is what happens to the pull
requests that were open when this landed: a head that ALREADY carries a red
`harness` check run is still held by it. GitHub never deletes a check run,
and the gate does not excuse one by name — that would be a rule saying "this
workflow's red does not count", which is exactly the hole DRE-1994 closed.
Such a head is released by a re-run that goes green, or by any new commit:
the new head gets no harness run at all.

The same rows run against the pre-extraction shell in
tests/test_merge_gate_decision_table.py (`harness_absent_from_the_head_merges`,
`harness_red_already_on_the_head_still_waits`).
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


def _ci_runs():
    # The two contexts branch protection requires on bureau-pipeline's main.
    return [
        {
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "check_suite": {"id": 222},
        }
        for name in ("scripts unit tests", "TDD commit discipline")
    ]


def _verdicts(sha=SHA):
    return [
        {"user": {"login": QA}, "body": f"🔎 QA Critic — VERDICT: APPROVE @{sha}"},
        {"user": {"login": QA}, "body": f"🧪 QA Verifier — VERDICT: PASS @{sha}"},
    ]


def _decide(extra_runs=(), comments=None):
    return merge_gate.decide(
        head_sha=SHA,
        qa_login=QA,
        check_runs=_ci_runs() + list(extra_runs),
        comments=_verdicts() if comments is None else comments,
        review_suites=frozenset(),
        compare_status="ahead",
    )


class AHeadWithNoHarnessCheckMergesTest(unittest.TestCase):
    """The decision table DRE-4149 asks for, one row per test."""

    def test_green_ci_and_both_verdicts_bound_to_the_head_merge(self):
        decision = _decide()
        self.assertEqual(decision.action, "merge", decision.reason)

    def test_the_gate_does_not_wait_for_a_harness_that_will_never_report(self):
        # Not "merge, with a note that the harness is missing": nothing in
        # the decision or its notes names the harness at all.
        decision = _decide()
        said = " ".join([decision.reason, *decision.notes]).lower()
        self.assertNotIn("harness", said)

    def test_the_other_conditions_still_bind_without_it(self):
        # Dropping the harness lowered nothing else. No verdict: wait.
        self.assertEqual(_decide(comments=[]).action, "wait")
        # A verdict for an older commit is not a verdict for this one.
        self.assertNotEqual(_decide(comments=_verdicts("c" * 40)).action, "merge")
        # A red unit suite still holds.
        red = dict(_ci_runs()[0], conclusion="failure")
        self.assertEqual(_decide(extra_runs=[red]).action, "wait")


class AHeadThatAlreadyCarriesOneTest(unittest.TestCase):
    """History is not special-cased. These are the pull requests that were
    open on 2026-09-17, and any head a by-hand run's check ever lands on."""

    def test_a_red_harness_already_on_the_head_still_holds_it(self):
        decision = _decide(extra_runs=[_harness_run("failure")])
        self.assertEqual(decision.action, "wait")
        self.assertIn("not green", decision.reason)

    def test_re_running_it_green_releases_the_head(self):
        self.assertEqual(_decide(extra_runs=[_harness_run("success")]).action, "merge")

    def test_a_re_run_in_flight_holds_until_it_finishes(self):
        in_flight = _harness_run(None, status="in_progress")
        self.assertEqual(_decide(extra_runs=[in_flight]).action, "wait")

    def test_the_gate_excuses_no_workflow_by_name(self):
        # Exclusion by verified origin (DRE-1994) reaches the review
        # workflows and nothing else. Adding harness.yml there would release
        # the held heads — and teach the gate that a red check can be waved
        # through by listing its file, which is the hole DRE-1994 closed.
        self.assertNotIn(
            ".github/workflows/harness.yml", merge_gate.DEFAULT_REVIEW_WORKFLOWS
        )


if __name__ == "__main__":
    unittest.main()
