"""Condition 1 waits for every workflow run on the head to finish (DRE-5045).

The hole: condition 1 decided "CI green" from the check runs that already
existed on the head. A workflow run that GitHub has queued but not yet given
any jobs contributes NO check runs, so the gate could not see it. Portico
#748, 2026-09-26 22:45 PT (gate run 36297968427): the head's CI run
(36297687714) was still queued with no jobs, the gate counted the two check
runs that did exist — Specimen (success) and prune (skipped) — called that
green, and asked to merge. GitHub refused ("base branch policy prohibits the
merge") because the six required checks did not exist yet. The same
signature hit portico #724 and #739.

The fix: condition 1 also reads GitHub's own workflow-runs record for the
head (the listing merge-gate.yml already gathers for DRE-1994). Any run that
is not the review workflow and not `completed` is "CI still running" — the
same `wait` a pending check gets. A listing the workflow could not read makes
the gate wait; it never reads as green.

Condition 0 (`evaluate_conflict`) is not this bug and is not touched here:
the failure card DRE-4997 blamed it, and changing it would reverse DRE-2416.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "merge_gate.py"
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import merge_gate  # noqa: E402

HEAD = "0887908a" * 5
QA_LOGIN = "agent-bureau-qa-bot[bot]"
APPROVE = [{
    "user": {"login": QA_LOGIN, "type": "Bot"},
    "body": f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}",
}]


def wf_run(run_id, name, path, suite, status="completed",
           conclusion="success", event="pull_request"):
    """One entry of GET actions/runs?head_sha=<sha>, as the REST API
    shapes it."""
    return {
        "id": run_id,
        "name": name,
        "path": path,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "check_suite_id": suite,
    }


def check(name, suite, status="completed", conclusion="success"):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "app": {"id": 15368, "slug": "github-actions"},
        "check_suite": {"id": suite},
    }


# Portico #748 at 22:45 PT: the CI run is queued and has no jobs, so it has
# no check runs; the two check runs that exist are green.
CI_QUEUED = wf_run(36297687714, "CI", ".github/workflows/ci.yml", 101,
                   status="queued", conclusion=None)
SPECIMEN = wf_run(36297687700, "Specimen", ".github/workflows/specimen.yml", 102)
PRUNE = wf_run(36297687701, "Prune", ".github/workflows/prune.yml", 103)
REVIEW = wf_run(36297687702, "QA Review", ".github/workflows/qa-review.yml", 104)
PORTICO_748_RUNS = [CI_QUEUED, SPECIMEN, PRUNE, REVIEW]
PORTICO_748_CHECKS = [
    check("Specimen", 102),
    check("prune", 103, conclusion="skipped"),
    check("call / review", 104),
]


def decide(check_runs, workflow_runs):
    review_suites = merge_gate.review_suite_ids(
        workflow_runs or [], merge_gate.DEFAULT_REVIEW_WORKFLOWS)
    return merge_gate.decide(
        HEAD, QA_LOGIN, check_runs, APPROVE, review_suites,
        compare_status="ahead",
        unfinished_runs=merge_gate.unfinished_runs(workflow_runs),
    )


def run_cli(check_runs, workflow_runs_payload):
    with tempfile.TemporaryDirectory() as td:
        cr = Path(td) / "check-runs.json"
        wr = Path(td) / "workflow-runs.json"
        cm = Path(td) / "comments.json"
        cp = Path(td) / "compare.json"
        cr.write_text(json.dumps(
            {"total_count": len(check_runs), "check_runs": check_runs}))
        wr.write_text(workflow_runs_payload)
        cm.write_text(json.dumps(APPROVE))
        cp.write_text(json.dumps({"status": "ahead"}))
        return subprocess.run(
            [sys.executable, str(SCRIPT),
             "--head-sha", HEAD, "--qa-login", QA_LOGIN,
             "--check-runs-file", str(cr),
             "--comments-file", str(cm),
             "--workflow-runs-file", str(wr),
             "--compare-file", str(cp)],
            capture_output=True, text=True,
        )


def fields(proc):
    return dict(ln.split("=", 1) for ln in proc.stdout.splitlines() if "=" in ln)


def listing(runs):
    return json.dumps({"total_count": len(runs), "workflow_runs": runs})


class QueuedRunWithNoJobsTest(unittest.TestCase):
    """The portico #748 signature: a queued CI run with no check runs, beside
    a successful and a skipped check run, is CI still running — not green."""

    def test_portico_748_reads_ci_still_running(self):
        decision = decide(PORTICO_748_CHECKS, PORTICO_748_RUNS)
        self.assertEqual(decision.action, "wait")
        self.assertIn("CI still running", decision.reason)
        self.assertIn("CI", decision.reason)
        self.assertIn("queued", decision.reason)

    def test_portico_748_end_to_end_never_merges(self):
        proc = run_cli(PORTICO_748_CHECKS, listing(PORTICO_748_RUNS))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = fields(proc)
        self.assertEqual(got.get("decision"), "wait")
        self.assertIn("CI still running", got.get("reason", ""))

    def test_the_same_head_merged_once_its_ci_finished(self):
        # 00:13 PT: the CI run completed with its jobs green, and the later
        # gate run (36302445155) merged the same head.
        done = dict(CI_QUEUED, status="completed", conclusion="success")
        checks = PORTICO_748_CHECKS + [check("build", 101), check("test", 101)]
        proc = run_cli(checks, listing([done, SPECIMEN, PRUNE, REVIEW]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(fields(proc).get("decision"), "merge")

    def test_a_run_missing_its_status_counts_as_unfinished(self):
        # Fail-closed, like a suite-less check run: a run GitHub's record
        # does not call `completed` is not proof that CI finished.
        ci = {k: v for k, v in CI_QUEUED.items() if k != "status"}
        self.assertEqual(merge_gate.unfinished_runs([ci]), [ci])
        decision = decide(PORTICO_748_CHECKS, [ci, SPECIMEN, PRUNE, REVIEW])
        self.assertEqual(decision.action, "wait")

    def test_every_unfinished_status_waits(self):
        for status in ("queued", "in_progress", "waiting", "requested",
                       "pending"):
            with self.subTest(status=status):
                ci = dict(CI_QUEUED, status=status)
                decision = decide(PORTICO_748_CHECKS,
                                  [ci, SPECIMEN, PRUNE, REVIEW])
                self.assertEqual(decision.action, "wait")
                self.assertIn(status, decision.reason)


class WhatIsNotCiTest(unittest.TestCase):
    """The runs that are not CI of the head never make the gate wait: the
    review workflow (its verdict COMMENT is condition 2's record, DRE-1994)
    and any run the commit itself did not trigger (DRE-3263's classifier —
    the gate's own run, a fix agent, the medic)."""

    def test_an_unfinished_review_run_does_not_block(self):
        review = dict(REVIEW, status="in_progress", conclusion=None)
        checks = [check("Specimen", 102),
                  check("call / review", 104, status="in_progress",
                        conclusion=None)]
        decision = decide(checks, [SPECIMEN, review])
        self.assertEqual(decision.action, "merge", decision.reason)

    def test_bureau_pipelines_own_review_workflow_is_excluded_too(self):
        review = wf_run(9, "PR Review", ".github/workflows/pr-review.yml", 105,
                        status="queued", conclusion=None)
        decision = decide([check("Specimen", 102)], [SPECIMEN, review])
        self.assertEqual(decision.action, "merge", decision.reason)

    def test_a_run_the_commit_did_not_trigger_does_not_block(self):
        for event in ("workflow_dispatch", "workflow_run", "issue_comment",
                      "repository_dispatch", "schedule"):
            with self.subTest(event=event):
                gate = wf_run(7, "Merge Gate", ".github/workflows/merge-gate.yml",
                              106, status="in_progress", conclusion=None,
                              event=event)
                decision = decide([check("Specimen", 102)], [SPECIMEN, gate])
                self.assertEqual(decision.action, "merge", decision.reason)

    def test_a_pull_request_target_run_is_ci_of_the_head(self):
        ci = dict(CI_QUEUED, event="pull_request_target")
        decision = decide([check("Specimen", 102)], [SPECIMEN, ci])
        self.assertEqual(decision.action, "wait")

    def test_a_push_run_is_ci_of_the_head(self):
        ci = dict(CI_QUEUED, event="push")
        decision = decide([check("Specimen", 102)], [SPECIMEN, ci])
        self.assertEqual(decision.action, "wait")


class EveryRunCompletedIsTodayTest(unittest.TestCase):
    """Every run on the head completed (the review workflow excluded):
    condition 1 evaluates exactly as it did before DRE-5045 — same action,
    same reason, whatever the check runs say."""

    def assert_same_as_today(self, check_runs, workflow_runs):
        review_suites = merge_gate.review_suite_ids(
            workflow_runs, merge_gate.DEFAULT_REVIEW_WORKFLOWS)
        today = merge_gate.decide(HEAD, QA_LOGIN, check_runs, APPROVE,
                                  review_suites, compare_status="ahead")
        now = decide(check_runs, workflow_runs)
        self.assertEqual((now.action, now.reason, now.notes),
                         (today.action, today.reason, today.notes))
        return now

    def test_all_green_merges_exactly_as_today(self):
        ci = dict(CI_QUEUED, status="completed", conclusion="success")
        now = self.assert_same_as_today(
            PORTICO_748_CHECKS + [check("build", 101)],
            [ci, SPECIMEN, PRUNE, REVIEW])
        self.assertEqual(now.action, "merge")

    def test_a_red_check_waits_exactly_as_today(self):
        ci = dict(CI_QUEUED, status="completed", conclusion="failure")
        now = self.assert_same_as_today(
            PORTICO_748_CHECKS + [check("build", 101, conclusion="failure")],
            [ci, SPECIMEN, PRUNE, REVIEW])
        self.assertEqual(now.action, "wait")
        self.assertIn("not green", now.reason)

    def test_no_checks_waits_exactly_as_today(self):
        now = self.assert_same_as_today([], [])
        self.assertEqual(now.action, "wait")

    def test_an_unfinished_review_run_alone_is_today(self):
        review = dict(REVIEW, status="queued", conclusion=None)
        self.assert_same_as_today([check("Specimen", 102)], [SPECIMEN, review])

    def test_an_empty_readable_listing_is_today(self):
        # A read that SUCCEEDED and found no runs (a repo whose checks come
        # from outside Actions) names nothing unfinished.
        self.assertEqual(merge_gate.unfinished_runs([]), [])
        self.assert_same_as_today([check("external", 900)], [])

    def test_callers_that_never_pass_the_record_are_unchanged(self):
        # The default reproduces the pre-DRE-5045 decision: the release
        # train and every older caller keep their behavior.
        decision = merge_gate.decide(HEAD, QA_LOGIN, PORTICO_748_CHECKS,
                                     APPROVE, frozenset({104}),
                                     compare_status="ahead")
        self.assertEqual(decision.action, "merge")


class UnreadableListingWaitsTest(unittest.TestCase):
    """A workflow-runs read that fails makes the gate wait. It never reads
    as green — the blip is exactly when a queued run would go unseen."""

    ALL_GREEN = [check("Specimen", 102), check("build", 101)]

    def test_unreadable_record_waits_in_decide(self):
        decision = merge_gate.decide(
            HEAD, QA_LOGIN, self.ALL_GREEN, APPROVE, frozenset(),
            compare_status="ahead", unfinished_runs=None)
        self.assertEqual(decision.action, "wait")
        self.assertNotEqual(decision.action, "merge")
        self.assertIn("could not be read", decision.reason)

    def test_unreadable_listing_resolves_to_none(self):
        self.assertIsNone(merge_gate.unfinished_runs(None))

    def test_the_workflows_blip_substitute_waits_end_to_end(self):
        # The exact substitute merge-gate.yml writes when the read fails.
        proc = run_cli(self.ALL_GREEN, merge_gate.UNREADABLE_WORKFLOW_RUNS)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = fields(proc)
        self.assertEqual(got.get("decision"), "wait")
        self.assertNotIn("decision=merge", proc.stdout)
        self.assertIn("could not be read", got.get("reason", ""))

    def test_merge_gate_yml_writes_that_substitute_on_a_failed_read(self):
        line = next(
            ln for ln in WORKFLOW.read_text().splitlines()
            if "|| echo" in ln and "/tmp/workflow-runs.json" in ln
        )
        self.assertIn(f"'{merge_gate.UNREADABLE_WORKFLOW_RUNS}'", line)

    def test_the_substitute_is_not_an_empty_listing(self):
        # `{"workflow_runs":[]}` is a read that succeeded and found nothing;
        # the blip must not look like it.
        payload = json.loads(merge_gate.UNREADABLE_WORKFLOW_RUNS)
        self.assertNotIn("workflow_runs", payload)


if __name__ == "__main__":
    unittest.main()
