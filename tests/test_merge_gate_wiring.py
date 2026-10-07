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
import step_shell  # noqa: E402


def evaluate_step():
    """The `Evaluate and merge` step, from the parsed YAML."""
    doc = yaml.safe_load(step_shell.workflow_source(WORKFLOW))
    steps = doc["jobs"]["evaluate"]["steps"]
    found = [s for s in steps if s.get("name") == "Evaluate and merge"]
    assert len(found) == 1, "expected exactly one 'Evaluate and merge' step"
    return found[0]


def evaluate_step_run():
    """The `Evaluate and merge` step's run block, from the parsed YAML."""
    return step_shell.step_shell(evaluate_step())


class ScriptInvocationTest(unittest.TestCase):
    def setUp(self):
        self.step = evaluate_step()
        self.run_block = step_shell.step_shell(self.step)

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
        # DRE-4103 moved the derivation into the step's `env:` (the
        # expression-budget remedy, as DRE-4486 did for the repository and
        # token), so it is asserted there, and the script must not shadow it.
        self.assertEqual(
            self.step["env"]["QA_LOGIN"], "${{ steps.qa.outputs.app-slug }}[bot]"
        )
        self.assertNotIn("QA_LOGIN=", self.run_block)

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
        # DRE-4139: the comment record is fetched PAGINATED, so the redirect
        # sits on its own continuation line rather than beside the path.
        self.assertIn(f"> {m.group(1)}", self.run_block)
        self.assertIn("issues/$PR/comments?per_page=100", self.run_block)

    def test_review_origin_record_is_gathered_and_passed(self):
        """DRE-1994: the review-run exclusion needs GitHub's own record of
        which workflow FILE produced each check suite — the workflow-runs
        listing for the head SHA, written to the exact file the script is
        handed, with a fail-CLOSED substitute on an API blip. Since DRE-5045
        the substitute says the read FAILED — it is not an empty listing,
        which would read as "no workflow run is still running"."""
        self.assertIn("actions/runs?head_sha=$SHA", self.run_block)
        m = re.search(r"--workflow-runs-file (\S+)", self.run_block)
        self.assertIsNotNone(m, "workflow does not pass --workflow-runs-file")
        self.assertIn(f"> {m.group(1)}", self.run_block)
        self.assertIn(f"'{merge_gate.UNREADABLE_WORKFLOW_RUNS}'", self.run_block)
        self.assertNotIn('\'{"workflow_runs":[]}\'', self.run_block)

    def test_pr_body_and_creation_time_are_gathered_and_passed(self):
        """DRE-5515: condition W (DRE-5511) reads the pull request's body and
        GitHub's `createdAt`, the body written to the exact file the script is
        handed. Since Stage 2 #19 both ride the gate's ONE `gh pr view` read,
        which is not swallowed — it carries the fields every decision is made
        on, and a failed read of those always killed the step. What stays
        fail-SOFT is the extraction: a body or time the record lacks, or a
        record that will not parse, is an empty body file and an empty
        `CREATED_AT`, which the condition reads as "nothing was read" — the
        flags are still passed, never skipped."""
        m = re.search(r'gh pr view "\$PR" --json (\S+) > /tmp/pr-view\.json\n',
                      self.run_block)
        self.assertIsNotNone(m, "the gate's one pull request read is gone")
        self.assertIn("body", m.group(1).split(","))
        self.assertIn("createdAt", m.group(1).split(","))
        code = step_shell.code_lines(self.run_block)
        self.assertEqual(sum("gh pr view" in ln for ln in code), 2,
                         "one read, plus DRE-2117's re-read after a refused merge")
        self.assertIn(
            "jq -r '.body // \"\"' /tmp/pr-view.json > /tmp/pr-body.txt",
            self.run_block,
        )
        self.assertIn("|| : > /tmp/pr-body.txt", self.run_block)
        self.assertIn(
            "CREATED_AT=$(jq -r '.createdAt // \"\"' /tmp/pr-view.json "
            "2>/dev/null || true)",
            self.run_block,
        )
        # The DECISION's invocation, not `merge_gate.py precheck` (Stage 2 #19).
        decide = self.run_block.find("python3 .bureau-pipeline/scripts/merge_gate.py \\\n")
        self.assertGreater(decide, -1)
        invocation = self.run_block[decide:]
        invocation = invocation[:invocation.find("| tee /tmp/gate-decision")]
        self.assertIn("--pr-body-file /tmp/pr-body.txt", invocation)
        self.assertIn('--pr-created-at "$CREATED_AT"', invocation)
        # The read sits before the decision it feeds.
        self.assertLess(m.start(), decide)
        # The script carries no `${` (tests/test_evaluate_and_merge.py).
        self.assertNotIn("${CREATED_AT}", self.run_block)

    def _run_pr_body_read(self, view_text):
        """Run the script's own body and createdAt extraction lines, verbatim
        but for /tmp, over a written record; return (body file text,
        CREATED_AT)."""
        lines = [
            ln for ln in self.run_block.splitlines()
            if "/tmp/pr-view.json" in ln and not ln.lstrip().startswith("#")
            and ("/tmp/pr-body.txt" in ln or "CREATED_AT=" in ln)
        ]
        self.assertEqual(len(lines), 2, lines)
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "pr-view.json").write_text(view_text)
            snippet = "\n".join(
                ["set -euo pipefail"]
                + [ln.replace("/tmp/", f"{td}/") for ln in lines]
                + ['printf "%s" "$CREATED_AT" > ' + f"{td}/created-at"]
            )
            proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own lines
                ["bash", "-c", snippet], capture_output=True, text=True,
                env={**os.environ, "PR": "7"},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return (td / "pr-body.txt").read_text(), (td / "created-at").read_text()

    def test_pr_body_read_hands_over_what_github_printed(self):
        body, created = self._run_pr_body_read(json.dumps(
            {"body": "What's new: none", "createdAt": "2026-10-02T17:00:00Z"}
        ))
        self.assertEqual(body.strip(), "What's new: none")
        self.assertEqual(created, "2026-10-02T17:00:00Z")

    def test_a_body_the_record_lacks_is_an_empty_file_and_an_empty_time(self):
        """Absent, null or unparseable must not kill the step under pipefail,
        and must leave "nothing was read" — never a stale or partial
        record."""
        for text in (json.dumps({"state": "OPEN"}),
                     json.dumps({"body": None, "createdAt": None}),
                     "not json"):
            with self.subTest(text=text):
                body, created = self._run_pr_body_read(text)
                self.assertEqual(body.strip(), "")
                self.assertEqual(created, "")

    def test_the_proof_record_is_gathered_and_passed(self):
        """DRE-6141: condition P reads the record `proof_record.py gather`
        writes — off the one view (which carries `files`) at the evaluated
        head — and the gate is handed that same file."""
        m = re.search(r'gh pr view "\$PR" --json (\S+) > /tmp/pr-view\.json\n',
                      self.run_block)
        self.assertIsNotNone(m, "the gate's one pull request read is gone")
        self.assertIn("files", m.group(1).split(","))
        code = "\n".join(step_shell.code_lines(self.run_block))
        gather = re.search(
            r"python3 \.bureau-pipeline/scripts/proof_record\.py gather(.*?)(?:\n(?!\s)|$)",
            code, re.S)
        self.assertIsNotNone(gather, "the script no longer runs proof_record.py gather")
        args = gather.group(1)
        self.assertIn("--pr-view-file /tmp/pr-view.json", args)
        self.assertIn('--head-sha "$SHA"', args)
        self.assertIn('--repo "$REPO_FULL"', args)
        out = re.search(r"--out (\S+)", args)
        self.assertIsNotNone(out, "the gather names no output file")
        passed = re.search(r"--proof-record-file (\S+)", self.run_block)
        self.assertIsNotNone(passed, "merge_gate.py is not handed --proof-record-file")
        self.assertEqual(passed.group(1), out.group(1))
        # Gathered before the decision that reads it.
        self.assertLess(self.run_block.find("proof_record.py gather"),
                        self.run_block.find("--proof-record-file"))

    def test_origin_listing_uses_the_workflows_own_token(self):
        """The runs listing needs actions:read, which the qa-bot App
        deliberately lacks — that ONE read must use the workflow's own
        token, not GH_TOKEN (the qa-bot token).

        Since DRE-4486 the token reaches the script through the step's
        `env:` (the expression-budget remedy), so the assertion is made in
        two halves: the line spends WORKFLOW_TOKEN, and WORKFLOW_TOKEN is
        `github.token`. Asserting only the first would pass against an env
        that bound it to the qa-bot's token."""
        line = next(
            ln for ln in self.run_block.splitlines()
            if "actions/runs?head_sha=$SHA" in ln
        )
        self.assertIn('GH_TOKEN="$WORKFLOW_TOKEN"', line)
        self.assertEqual(self.step["env"]["WORKFLOW_TOKEN"], "${{ github.token }}")

    def test_the_fix_lane_read_uses_the_workflows_own_token_too(self):
        """DRE-4486's lane listing is the same API and the same permission —
        a qa-bot token 403s on it and the gate would then wait forever."""
        line = next(
            ln for ln in self.run_block.splitlines()
            if "stranded_fix.py lane" in ln
        )
        self.assertIn('GH_TOKEN="$WORKFLOW_TOKEN"', line)

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
        doc = yaml.safe_load(step_shell.workflow_source(WORKFLOW))
        # DRE-2508 put the filter on the entry job (`resolve`); DRE-4279 made
        # `evaluate` the entry for every event that names its PR, so the
        # filter lives on `evaluate` now — the one job every leg reaches.
        cond = doc["jobs"]["evaluate"]["if"]
        self.assertIn(
            "github.event.comment.user.login == 'agent-bureau-qa-bot[bot]'", cond
        )


class MatchHeadCommitTest(unittest.TestCase):
    """DRE-2117: every gate condition — including the SHA-bound critic
    APPROVE — is evaluated against $SHA captured once. The merge call must
    pin that same SHA (`--match-head-commit`) so a push or rebase landing in
    the race window makes GitHub reject the merge instead of merging a head
    the critic never reviewed (the DRE-1990 skew, closed at the last step)."""

    def setUp(self):
        self.run_block = evaluate_step_run()
        self.merge_line = next(
            ln for ln in self.run_block.splitlines() if "gh pr merge" in ln
        )

    def test_merge_pins_the_evaluated_head_sha(self):
        self.assertIn(
            '--match-head-commit "$SHA"', self.merge_line,
            "gh pr merge does not pin the evaluated head SHA — a head moved "
            "between evaluation and merge would merge unreviewed",
        )

    def test_moved_head_is_a_clean_skip_not_a_red_run(self):
        """Under `set -euo pipefail` a bare failing merge reds the run; the
        raced head is the benign case — the failure must be HANDLED and exit
        0, so the new head's own events re-run the gate (no medic churn)."""
        self.assertIn("if ! gh pr merge", self.run_block,
                      "merge failure is unhandled — a moved head reds the run")
        tail = self.run_block[self.run_block.find("if ! gh pr merge"):]
        self.assertIn("exit 0", tail, "moved-head arm does not exit clean")

    def test_merge_failure_with_unmoved_head_stays_loud(self):
        """Classify-first: only a MOVED head is the benign race. The handler
        re-reads the current headRefOid, and a failure with the head still at
        $SHA is a real failure that must stay a red run."""
        tail = self.run_block[self.run_block.find("if ! gh pr merge"):]
        self.assertIn("headRefOid", tail,
                      "handler never re-reads the head to classify the failure")
        self.assertIn("exit 1", tail,
                      "an unmoved-head merge failure is silently swallowed")


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
