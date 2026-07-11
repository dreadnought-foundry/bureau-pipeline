"""Wiring tests: merge-gate.yml must invoke scripts/merge_gate.py with
exactly the fields the script expects, and must only merge on an explicit
`decision=merge` (DRE-1992 — schema-drift guard for the extraction).

The decision LOGIC is covered by tests/test_merge_gate_decision_table.py
(old-shell parity) and the migrated authorship / SHA-binding suites. This
file guards the seam: the workflow gathers the inputs, the script decides,
the workflow acts — a diff that renames a flag, drops an input, or merges
on anything but the machine-readable verdict turns these red.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"
SCRIPT = ROOT / "scripts" / "merge_gate.py"

sys.path.insert(0, str(ROOT / "scripts"))

import merge_gate  # noqa: E402


def evaluate_step_run():
    """The `Evaluate and merge` step's run block, from the parsed YAML."""
    doc = yaml.safe_load(WORKFLOW.read_text())
    steps = doc["jobs"]["evaluate"]["steps"]
    runs = [s["run"] for s in steps if s.get("name") == "Evaluate and merge"]
    assert len(runs) == 1, "expected exactly one 'Evaluate and merge' step"
    return runs[0]


class ScriptInvocationTest(unittest.TestCase):
    def setUp(self):
        self.run_block = evaluate_step_run()

    def test_workflow_calls_the_extracted_script(self):
        self.assertIn(
            "python3 .bureau-pipeline/scripts/merge_gate.py",
            self.run_block,
            "merge-gate.yml no longer calls the extracted decision script "
            "(same shared-checkout path pattern as linear_ops.py)",
        )

    def test_workflow_passes_every_flag_the_script_requires(self):
        """Drift guard in BOTH directions: every required argparse option of
        merge_gate.py appears in the workflow's invocation, so a flag rename
        on either side turns this red."""
        parser = merge_gate.build_parser()
        required = [
            a.option_strings[0]
            for a in parser._actions
            if a.required and a.option_strings
        ]
        self.assertGreaterEqual(len(required), 4, "script lost its input flags")
        for flag in required:
            self.assertIn(flag, self.run_block, f"workflow does not pass {flag}")

    def test_head_sha_and_qa_login_are_the_live_values(self):
        self.assertIn('--head-sha "$SHA"', self.run_block)
        self.assertIn('--qa-login "$QA_LOGIN"', self.run_block)
        # The trusted login is DERIVED from the same App key the gate merges
        # with (#57) — never a hardcoded literal that could drift on rename.
        self.assertIn(
            'QA_LOGIN="${{ steps.qa.outputs.app-slug }}[bot]"', self.run_block
        )

    def test_inputs_come_from_githubs_own_records(self):
        """Check runs from the REST check-runs API on the head SHA; comments
        from the PR's issue comments — both written to the exact files the
        script is handed."""
        self.assertIn("commits/$SHA/check-runs", self.run_block)
        self.assertIn("issues/$PR/comments", self.run_block)
        m = re.search(r"--check-runs-file (\S+)", self.run_block)
        self.assertIsNotNone(m)
        self.assertIn(f"check-runs\" > {m.group(1)}", self.run_block)
        m = re.search(r"--comments-file (\S+)", self.run_block)
        self.assertIsNotNone(m)
        self.assertIn(f"comments\" > {m.group(1)}", self.run_block)

    def test_review_origin_record_is_gathered_and_passed(self):
        """DRE-1994: the review-run exclusion needs GitHub's own record of
        which workflow FILE produced each check suite — the workflow-runs
        listing for the head SHA, written to the exact file the script is
        handed, with a fail-CLOSED empty substitute on an API blip."""
        self.assertIn("actions/runs?head_sha=$SHA", self.run_block)
        m = re.search(r"--workflow-runs-file (\S+)", self.run_block)
        self.assertIsNotNone(m, "workflow does not pass --workflow-runs-file")
        self.assertIn(f"> {m.group(1)}", self.run_block)
        self.assertIn('\'{"workflow_runs":[]}\'', self.run_block)

    def test_origin_listing_uses_the_workflows_own_token(self):
        """The runs listing needs actions:read, which the qa-bot App
        deliberately lacks — that ONE read must use the workflow's own
        token, not GH_TOKEN (the qa-bot token)."""
        line = next(
            ln for ln in self.run_block.splitlines()
            if "actions/runs?head_sha=$SHA" in ln
        )
        self.assertIn('GH_TOKEN="${{ github.token }}"', line)

    def test_review_workflow_allowlist_is_passed_explicitly(self):
        """The exclusion allowlist is PATHS of pipeline-owned review stubs —
        visible in the workflow, so a stub rename is a reviewable diff here,
        not silent drift inside the script's default."""
        self.assertIn("--review-workflows", self.run_block)
        self.assertIn(".github/workflows/qa-review.yml", self.run_block)

    def test_merge_only_on_explicit_decision(self):
        """`gh pr merge` must be reachable only behind the machine-readable
        `decision=merge` — fail-closed if the script output ever changes
        shape (grep finds nothing → DECISION empty → exit before merging)."""
        guard = self.run_block.find('[ "$DECISION" = "merge" ] || exit 0')
        merge = self.run_block.find("gh pr merge")
        self.assertGreater(guard, -1, "decision guard missing")
        self.assertGreater(merge, guard, "gh pr merge not behind the guard")
        self.assertIn("grep -m1 '^decision='", self.run_block)
        self.assertIn("set -euo pipefail", self.run_block)

    def test_issue_comment_leg_still_requires_qa_bot_author(self):
        """The #57 event-leg filter is workflow territory (not the script):
        only a qa-bot-authored verdict comment wakes the gate at all."""
        doc = yaml.safe_load(WORKFLOW.read_text())
        cond = doc["jobs"]["evaluate"]["if"]
        self.assertIn(
            "github.event.comment.user.login == 'agent-bureau-qa-bot[bot]'", cond
        )


class CliContractTest(unittest.TestCase):
    """Run the real CLI the way the workflow does and assert the grep-able
    contract: `decision=` and `reason=` lines on stdout, exit 0."""

    HEAD = "aa11" * 10
    QA = "agent-bureau-qa-bot[bot]"

    def run_cli(self, check_runs, comments, workflow_runs=()):
        with tempfile.TemporaryDirectory() as td:
            cr = Path(td) / "check-runs.json"
            cm = Path(td) / "comments.json"
            wr = Path(td) / "workflow-runs.json"
            cp = Path(td) / "compare.json"
            # The workflow hands the RAW REST payloads over — objects with
            # check_runs / workflow_runs keys, not bare lists.
            cr.write_text(json.dumps({"total_count": len(check_runs), "check_runs": check_runs}))
            cm.write_text(json.dumps(comments))
            wr.write_text(json.dumps({"workflow_runs": list(workflow_runs)}))
            # A current branch — condition 0 (DRE-1924) has its own suite.
            cp.write_text(json.dumps({"status": "ahead"}))
            return subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--head-sha", self.HEAD, "--qa-login", self.QA,
                 "--check-runs-file", str(cr), "--comments-file", str(cm),
                 "--workflow-runs-file", str(wr), "--compare-file", str(cp)],
                capture_output=True, text=True,
            )

    def parse(self, stdout):
        fields = dict(
            ln.split("=", 1) for ln in stdout.splitlines() if "=" in ln
        )
        return fields

    def test_merge_decision_end_to_end(self):
        proc = self.run_cli(
            [{"name": "unit", "status": "completed", "conclusion": "success"}],
            [{"user": {"login": self.QA, "type": "Bot"},
              "body": f"🔎 QA Critic — VERDICT: APPROVE @{self.HEAD}"}],
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = self.parse(proc.stdout)
        self.assertEqual(fields.get("decision"), "merge")
        self.assertTrue(fields.get("reason"))

    def test_wait_decision_end_to_end(self):
        proc = self.run_cli(
            [{"name": "unit", "status": "completed", "conclusion": "success"}], []
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.parse(proc.stdout).get("decision"), "wait")

    def test_malformed_input_fails_loud_and_never_merges(self):
        """A crash must be a red job, not a silent skip — and certainly not
        a merge. Exit 2, nothing decision=merge on stdout."""
        with tempfile.TemporaryDirectory() as td:
            cr = Path(td) / "check-runs.json"
            cm = Path(td) / "comments.json"
            wr = Path(td) / "workflow-runs.json"
            cp = Path(td) / "compare.json"
            cr.write_text("not json")
            cm.write_text("[]")
            wr.write_text(json.dumps({"workflow_runs": []}))
            cp.write_text(json.dumps({"status": "ahead"}))
            proc = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--head-sha", self.HEAD, "--qa-login", self.QA,
                 "--check-runs-file", str(cr), "--comments-file", str(cm),
                 "--workflow-runs-file", str(wr), "--compare-file", str(cp)],
                capture_output=True, text=True,
            )
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("decision=merge", proc.stdout)

    def test_bad_head_sha_fails_loud(self):
        with tempfile.TemporaryDirectory() as td:
            cr = Path(td) / "check-runs.json"
            cm = Path(td) / "comments.json"
            wr = Path(td) / "workflow-runs.json"
            cp = Path(td) / "compare.json"
            cr.write_text(json.dumps({"check_runs": []}))
            cm.write_text("[]")
            wr.write_text(json.dumps({"workflow_runs": []}))
            cp.write_text(json.dumps({"status": "ahead"}))
            proc = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--head-sha", "not-a-sha", "--qa-login", self.QA,
                 "--check-runs-file", str(cr), "--comments-file", str(cm),
                 "--workflow-runs-file", str(wr), "--compare-file", str(cp)],
                capture_output=True, text=True,
            )
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("decision=merge", proc.stdout)


if __name__ == "__main__":
    unittest.main()
