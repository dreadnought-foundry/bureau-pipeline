"""The critic and the fix agent read each pull request record once per job (#21).

`tests/test_read_once.py` pins the seam. This file pins the two workflows:
the review's own step shells, executed in order against a stub `gh` that
counts its calls, read the PR record, the compare record, the comment thread
and the changed-file list ONCE each where they used to read them five, two,
two and two times; and the fix agent shares its base branch, its head's check
runs and its verdict thread across the steps that read the same thing.

The BODY is the exception, on purpose: the critic is handed the CURRENT body,
read live where it is used (tests/test_fix_body_before_push.py), so its two
reads stay.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
REPO = "dreadnought-foundry/portico"

GH_STUB = r"""#!/bin/sh
echo "$*" >> "$GH_CALLS"
case "$*" in
  "pr view"*) cat "$GH_PR" ;;
  "pr diff"*) printf 'scripts/x.py\nweb/src/App.tsx\n' ;;
  *"/compare/"*) printf '{"files": [], "merge_base_commit": {"sha": "%s"}}\n' "$(printf 'b%.0s' $(seq 40))" ;;
  *"/comments"*) printf '[[]]\n' ;;
  *"/commits?"*) printf '[]\n' ;;
  *) printf '{}\n' ;;
esac
"""
RECORD = {"headRefName": "agent/DRE-7-x", "headRefOid": "a" * 40, "baseRefName": "main",
          "body": "Implements DRE-7.", "changedFiles": 2, "additions": 10, "deletions": 2,
          "isDraft": False}


def _steps(wf: str, job: str) -> list[dict]:
    return yaml.safe_load((WORKFLOWS / wf).read_text())["jobs"][job]["steps"]


def _step(wf: str, job: str, step_id: str) -> dict:
    return next(s for s in _steps(wf, job) if s.get("id") == step_id)


def _job_env(wf: str, job: str) -> dict:
    return yaml.safe_load((WORKFLOWS / wf).read_text())["jobs"][job].get("env") or {}


def _shell_lines(run: str) -> list[str]:
    return [line for line in run.splitlines() if not line.strip().startswith("#")]


class TheReviewReadsEachRecordOnceTest(unittest.TestCase):
    """Decide review → Resolve PR → Plan visual QA → repair context → card
    context, in that order, sharing one RUNNER_TEMP the way one job does."""

    ORDER = ("decide", "pr", "vqplan", "repair", "cardctx")

    def _run_review(self, event: str) -> list[str]:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            (td / "bin").mkdir()
            gh = td / "bin" / "gh"
            gh.write_text(GH_STUB)
            gh.chmod(0o755)
            (td / "pr.json").write_text(json.dumps(RECORD))
            work = td / "work"
            work.mkdir()
            temp = td / "runner-temp"
            temp.mkdir()
            out = td / "github-output"
            env = {
                "PATH": f"{td / 'bin'}:{os.environ['PATH']}", "HOME": raw,
                "GH_CALLS": str(td / "calls"), "GH_PR": str(td / "pr.json"),
                "RUNNER_TEMP": str(temp), "GITHUB_OUTPUT": str(out),
                "GITHUB_REPOSITORY": REPO, "PIPELINE_DIR": str(ROOT),
                "GH_TOKEN": "t", "RUNS_TOKEN": "t", "PR": "7", "CARD": "",
                "EVENT": event, "HEAD_REF": RECORD["headRefName"],
                "QA_LOGIN": "agent-bureau-qa-bot[bot]", "HEAD_SHA": RECORD["headRefOid"],
                "DEFAULT_BRANCH": "main", "GITHUB_ACTIONS": "true",
                "PR_RECORD_FIELDS": _job_env("qa-review.yml", "review")["PR_RECORD_FIELDS"],
            }
            for step_id in self.ORDER:
                run = _step("qa-review.yml", "review", step_id)["run"]
                run = (run.replace("${{ github.repository }}", REPO)
                          .replace("${{ github.event.pull_request.number || "
                                   "github.event.inputs.pr_number }}", "7"))
                self.assertNotIn("${{", run, f"{step_id}: an expression this test does not expand")
                out.write_text("")
                proc = subprocess.run(["bash", "-e", "-c", run], cwd=work, env=env,
                                      capture_output=True, text=True, timeout=120)
                self.assertEqual(proc.returncode, 0, f"{step_id}: {proc.stderr}")
            calls = td / "calls"
            return calls.read_text().splitlines() if calls.exists() else []

    @staticmethod
    def _record_reads(calls):
        return sum(c.startswith("pr view") and "--json body" not in c for c in calls)

    def test_a_pull_request_review_reads_each_record_once(self):
        calls = self._run_review("pull_request")
        self.assertEqual(self._record_reads(calls), 1, calls)
        self.assertEqual(sum("--json body" in c for c in calls), 2, calls)
        self.assertEqual(sum("/compare/" in c for c in calls), 1, calls)
        self.assertEqual(sum("/comments" in c for c in calls), 1, calls)
        self.assertEqual(sum(c.startswith("pr diff") for c in calls), 1, calls)
        self.assertEqual(sum("/commits?" in c for c in calls), 1, calls)

    def test_a_dispatched_re_review_reads_each_record_once(self):
        calls = self._run_review("workflow_dispatch")
        self.assertEqual(self._record_reads(calls), 1, calls)
        self.assertEqual(sum("/compare/" in c for c in calls), 1, calls)
        # Decide reads no thread on a dispatch: the card context's read is the
        # first and only one, so a refutation posted before the dispatch is seen.
        self.assertEqual(sum("/comments" in c for c in calls), 1, calls)


class TheReviewWiringTest(unittest.TestCase):
    def test_no_review_step_reads_the_record_with_its_own_pr_view(self):
        for step in _steps("qa-review.yml", "review"):
            for line in _shell_lines(step.get("run") or ""):
                if "gh pr view" in line:
                    # The two different reads: the files listing (DRE-3091)
                    # and the live body the critic is handed.
                    self.assertTrue("--json files" in line or "--json body" in line,
                                    f"{step.get('name')}: {line}")

    def test_the_job_names_the_record_s_fields_once(self):
        fields = set(_job_env("qa-review.yml", "review")["PR_RECORD_FIELDS"].split(","))
        # isDraft (DRE-5801): Decide review skips a draft off the same record.
        self.assertEqual(fields, {"headRefName", "headRefOid", "baseRefName",
                                  "changedFiles", "additions", "deletions",
                                  "isDraft"})

    def test_the_content_id_binds_the_sha_the_verdict_binds(self):
        """DRE-2340: the compare record is keyed by BASE...SHA, so whatever the
        decide step read, Resolve's content id describes Resolve's sha."""
        for step_id in ("decide", "pr"):
            run = _step("qa-review.yml", "review", step_id)["run"]
            self.assertIn('read_once.py once "compare-$BASE...$SHA"', run, step_id)

    def test_the_body_is_never_taken_from_the_record(self):
        """A copy read earlier in the job is strictly worse than the live read
        (DRE-3005): the body stays out of the shared record."""
        self.assertNotIn("body", _job_env("qa-review.yml", "review")["PR_RECORD_FIELDS"].split(","))
        for step_id in ("vqplan", "cardctx"):
            run = _step("qa-review.yml", "review", step_id)["run"]
            self.assertIn('gh pr view "$PR" --json body', run, step_id)


class TheFixAgentSharesWhatItReadsTwiceTest(unittest.TestCase):
    def test_resolve_hands_the_base_branch_to_the_inherited_check(self):
        script = (ROOT / "scripts" / "resolve_fix_pr.sh").read_text()
        self.assertRegex(script, r"--json state,headRefName,headRefOid,mergeStateStatus,baseRefName")
        self.assertIn("base_ref=", script)
        step = _step("agent-fix.yml", "fix", "inherited")
        self.assertEqual((step.get("env") or {}).get("BASE_REF"), "${{ steps.pr.outputs.base_ref }}")
        # Only when Resolve could not say does the step read it itself.
        self.assertIn('[ -n "$BASE_REF" ] || BASE_REF=$(gh api "repos/$REPO/pulls/$PR"',
                      step["run"])

    def test_the_head_s_check_runs_are_read_once(self):
        for step_id in ("unfixable", "inherited"):
            run = _step("agent-fix.yml", "fix", step_id)["run"]
            self.assertIn('read_once.py once "check-runs-$HEAD_SHA"', run, step_id)

    def test_the_verdict_and_the_fix_loop_read_one_thread(self):
        steps = _steps("agent-fix.yml", "fix")
        verdict = next(s for s in steps if s.get("name") == "Fetch critic verdict (qa-bot authored only)")
        loop = next(s for s in steps if s.get("name") == "Fetch fix-loop thread (blockers + operator decisions)")
        key = re.search(r'read_once\.py once "([^"]+)"', verdict["run"])
        self.assertIsNotNone(key, "the verdict read does not go through read_once")
        self.assertIn(f'read_once.py once "{key.group(1)}"', loop["run"])
        # The verdict is the spec: its read keeps the retry seam and fails loud.
        self.assertIn("gh_read_retry.py", verdict["run"])


if __name__ == "__main__":
    unittest.main()
