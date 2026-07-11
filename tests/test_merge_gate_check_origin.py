"""Review-run exclusion by VERIFIED ORIGIN (DRE-1994, child of DRE-1978).

The hole: condition 1 (all CI check runs green) excluded any check run whose
NAME ends in "review", so the critic's own pending/crashed check could not
deadlock the gate. But check names come from PR-authored workflow files — an
agent PR could add a failing job named `sneaky-review` and its failure was
invisible to the all-green rule. The exclusion was attacker-nameable.

The fix: exclude by the check run's PRODUCING WORKFLOW FILE, per GitHub's
own records, never by name. The invariant (verified live on agent-bureau
PR #1899, head 62b73729eba16b3348d2f8cbcbcd660e84e73f42):

  • GitHub gives every workflow RUN its own check suite; all the run's jobs
    surface as check runs inside that suite (`check_suite.id` on the
    check-runs payload).
  • `GET /repos/{r}/actions/runs?head_sha=<sha>` lists the runs with GitHub's
    record of the producing workflow file (`path`) and the run's
    `check_suite_id`. Empirically: check run "call / review" (id 86251660121)
    sits in suite 78612234136 ↔ the run whose path is
    .github/workflows/qa-review.yml; the CI run has its own suite 78612233475.
  • The app id can NOT discriminate — every Actions-created check run is
    authored by the github-actions App (id 15368, verified on the same PR).
  • A PR-authored workflow (whatever its file name or job names) gets its
    own run → its own check suite. It can never place a check run inside the
    review workflow's suite.

HONEST RESIDUAL (documented, accepted): qa-review stubs trigger on
`pull_request`, so the workflow FILE CONTENT comes from the PR merge ref — a
PR that MODIFIES .github/workflows/qa-review.yml in its own branch still
produces a run recorded at that path, and that run stays excluded. This
grants no new power: that run is exactly the class the exclusion was built
to ignore, and exclusion only ever REMOVES a check from the all-green rule —
condition 2 (a qa-bot-App-authored, SHA-bound APPROVE comment, DRE-1987 +
DRE-1990) is what admits a merge, and a sabotaged review workflow cannot
mint the qa-bot's token. What the attacker LOSES is the old ability to
exempt arbitrary failing checks by naming them `*review`. Second residual:
the path allowlist must track the real stub paths — a renamed review stub
loses its exclusion and a crashed review run would then BLOCK (fail-closed:
the gate waits; it never merges past a red check).

The decision-table rows (test_merge_gate_decision_table.py, the
`verified-origin exclusion` delta rows) pin decide()'s outcomes; this file
pins the origin-resolution helper and the CLI seam the workflow drives.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "merge_gate.py"

sys.path.insert(0, str(ROOT / "scripts"))

import merge_gate  # noqa: E402

HEAD = "aa11" * 10
QA_LOGIN = "agent-bureau-qa-bot[bot]"
APPROVE = [{
    "user": {"login": QA_LOGIN, "type": "Bot"},
    "body": f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}",
}]

# The workflow-runs payload exactly as the REST API shapes it (fields
# observed live on agent-bureau PR #1899).
REVIEW_RUN = {
    "id": 29057391640,
    "name": "QA Review",
    "path": ".github/workflows/qa-review.yml",
    "event": "pull_request",
    "check_suite_id": 78612234136,
}
CI_RUN = {
    "id": 29057391428,
    "name": "CI",
    "path": ".github/workflows/ci.yml",
    "event": "pull_request",
    "check_suite_id": 78612233475,
}
EVIL_RUN = {
    "id": 29057399999,
    "name": "review",  # names are attacker-chosen — must not matter
    "path": ".github/workflows/sneaky.yml",
    "event": "pull_request",
    "check_suite_id": 78612239999,
}


def check(name, suite, status="completed", conclusion="success"):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "app": {"id": 15368, "slug": "github-actions"},
        "check_suite": {"id": suite},
    }


class ReviewSuiteIdsTest(unittest.TestCase):
    """The origin resolver: workflow-runs record + path allowlist → the
    check-suite ids whose runs are the review stage."""

    PATHS = frozenset({".github/workflows/qa-review.yml"})

    def test_review_path_yields_its_suite(self):
        got = merge_gate.review_suite_ids([CI_RUN, REVIEW_RUN, EVIL_RUN], self.PATHS)
        self.assertEqual(got, frozenset({78612234136}))

    def test_name_is_ignored_only_path_matters(self):
        # EVIL_RUN is literally NAMED "review" — its path is not allowlisted,
        # so its suite must not come back.
        got = merge_gate.review_suite_ids([EVIL_RUN], self.PATHS)
        self.assertEqual(got, frozenset())

    def test_empty_record_yields_nothing(self):
        self.assertEqual(merge_gate.review_suite_ids([], self.PATHS), frozenset())

    def test_null_suite_id_never_matches(self):
        # A run with no check_suite_id must not produce a None entry that a
        # suite-less check run could then "match" and get excluded by.
        crippled = dict(REVIEW_RUN, check_suite_id=None)
        got = merge_gate.review_suite_ids([crippled], self.PATHS)
        self.assertEqual(got, frozenset())

    def test_default_paths_cover_the_known_review_stubs(self):
        self.assertIn(".github/workflows/qa-review.yml",
                      merge_gate.DEFAULT_REVIEW_WORKFLOWS)
        self.assertIn(".github/workflows/pr-review.yml",
                      merge_gate.DEFAULT_REVIEW_WORKFLOWS)


class SuitelessCheckRunTest(unittest.TestCase):
    """A check run carrying no suite id must always COUNT (fail-closed),
    even when the origin record is empty."""

    def test_suiteless_red_check_blocks(self):
        bare = {"name": "unit", "status": "completed", "conclusion": "failure"}
        decision = merge_gate.decide(HEAD, QA_LOGIN, [bare], APPROVE,
                                     review_suites=frozenset(),
                                     compare_status="ahead")
        self.assertEqual(decision.action, "wait")
        self.assertIn("not green", decision.reason)


class CliOriginContractTest(unittest.TestCase):
    """The CLI exactly as merge-gate.yml drives it: raw REST payloads on
    disk, --workflow-runs-file carrying the origin record."""

    def run_cli(self, check_runs, workflow_runs, comments=APPROVE, extra=()):
        with tempfile.TemporaryDirectory() as td:
            cr = Path(td) / "check-runs.json"
            wr = Path(td) / "workflow-runs.json"
            cm = Path(td) / "comments.json"
            cr.write_text(json.dumps(
                {"total_count": len(check_runs), "check_runs": check_runs}))
            wr.write_text(workflow_runs if isinstance(workflow_runs, str)
                          else json.dumps(
                              {"total_count": len(workflow_runs),
                               "workflow_runs": workflow_runs}))
            cm.write_text(json.dumps(comments))
            # A current branch — condition 0 (DRE-1924) has its own suite.
            cp = Path(td) / "compare.json"
            cp.write_text(json.dumps({"status": "ahead"}))
            return subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--head-sha", HEAD, "--qa-login", QA_LOGIN,
                 "--check-runs-file", str(cr),
                 "--comments-file", str(cm),
                 "--workflow-runs-file", str(wr),
                 "--compare-file", str(cp), *extra],
                capture_output=True, text=True,
            )

    def decision(self, proc):
        fields = dict(ln.split("=", 1)
                      for ln in proc.stdout.splitlines() if "=" in ln)
        return fields.get("decision")

    def test_sneaky_review_failure_blocks_end_to_end(self):
        # THE DRE-1994 hole, driven through the real CLI: a failing check
        # named "review" from a PR-authored workflow must block the merge.
        proc = self.run_cli(
            [check("unit", 78612233475),
             check("review", 78612239999, conclusion="failure")],
            [CI_RUN, REVIEW_RUN, EVIL_RUN],
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "wait")

    def test_genuine_pending_review_does_not_deadlock_end_to_end(self):
        proc = self.run_cli(
            [check("unit", 78612233475),
             check("call / review", 78612234136,
                   status="in_progress", conclusion=None)],
            [CI_RUN, REVIEW_RUN],
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "merge")

    def test_review_workflows_flag_overrides_the_default_allowlist(self):
        # Same inputs as the deadlock test, but the review stub path is NOT
        # on the allowlist → its pending run counts → wait, never merge.
        proc = self.run_cli(
            [check("unit", 78612233475),
             check("call / review", 78612234136,
                   status="in_progress", conclusion=None)],
            [CI_RUN, REVIEW_RUN],
            extra=("--review-workflows", ".github/workflows/other.yml"),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "wait")

    def test_empty_origin_record_fails_closed(self):
        # The workflow substitutes {"workflow_runs":[]} on an API blip: no
        # exclusions, the review run counts, the gate WAITS — never merges.
        proc = self.run_cli(
            [check("unit", 78612233475),
             check("call / review", 78612234136, conclusion="failure")],
            [],
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "wait")

    def test_malformed_origin_record_fails_loud_and_never_merges(self):
        proc = self.run_cli([check("unit", 78612233475)], "not json")
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("decision=merge", proc.stdout)

    def test_workflow_runs_file_is_required(self):
        # The origin record is a required input — a workflow drift that
        # drops it must fail the job loudly (argparse exit 2), not silently
        # revert to some weaker rule.
        with tempfile.TemporaryDirectory() as td:
            cr = Path(td) / "check-runs.json"
            cm = Path(td) / "comments.json"
            cr.write_text(json.dumps({"check_runs": []}))
            cm.write_text("[]")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--head-sha", HEAD, "--qa-login", QA_LOGIN,
                 "--check-runs-file", str(cr), "--comments-file", str(cm)],
                capture_output=True, text=True,
            )
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("decision=merge", proc.stdout)


if __name__ == "__main__":
    unittest.main()
