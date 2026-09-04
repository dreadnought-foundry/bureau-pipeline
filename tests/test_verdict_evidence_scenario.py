"""DRE-3005, end to end: the two branches a defective verdict actually takes.

tests/test_verdict_evidence.py proves the RULES. This file proves the WIRING
by executing the real `post` step out of qa-review.yml, fed by the real gate
scripts, exactly as the workflow wires them — the same harness
test_qa_review_no_verdict_message.py uses, for the same reason: nothing about
what lands on a pull request is asserted by grepping YAML.

Unit-green is not live-working here, and the failure mode is expensive in
both directions:

  * If the hold branch never fires, the pipeline posts a REQUEST_CHANGES
    verdict asserting a run it did not do — the two overnight blocks this
    card exists to stop.
  * If it fires and composes the WRONG comment, a merge either hangs on a
    notice merge-gate cannot read, or — far worse — the neutral status
    becomes a merge credential. Two rules bound it, both load-bearing:
    the comment keeps `QA Critic` (or a stale APPROVE stays the latest
    word), and it never carries a `VERDICT:` line (or the hold IS an
    approval).

The body-snapshot footer is walked here too, because the only thing that
can prove it does not disturb the verdict header is running the shell that
composes both: `head -1` of that comment is what merge_gate's `VERDICT:`
match and verdict_content's end-anchored `content:` read both parse.
"""

from __future__ import annotations

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
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
CRITIC_GATE = ROOT / "scripts" / "check_critic_result.py"
EVIDENCE_GATE = ROOT / "scripts" / "verdict_evidence.py"

#: A completed review — the gate's `ok` path, so the ONLY thing that can
#: make the verdict unreal is the evidence check.
COMPLETED = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 31,
    "total_cost_usd": 2.14,
    "duration_ms": 214000,
    "result": "I reviewed the diff.",
}

#: agent-bureau #2247's blocker, in the shape the critic wrote it.
UNEVIDENCED = """VERDICT: REQUEST_CHANGES cause:defect

## Summary

The change does not keep the discipline the card asked for.

## For the fixing agent

I ran `python3 .bureau-pipeline/scripts/check_tdd_commits.py origin/main \
HEAD` myself against the current head and it exits 1 with "no test commit
precedes the implementation".
"""

#: The same finding with the run attached — what the standard asks for.
EVIDENCED = UNEVIDENCED + """
```
$ python3 .bureau-pipeline/scripts/check_tdd_commits.py origin/main HEAD
b60c929 [test] test(DRE-2832): RED
b8a801a [code] feat(DRE-2832): the planner brief learns
no test commit precedes the implementation — commit the RED test first
```
"""

APPROVE = "VERDICT: APPROVE\n\n## Summary\n\nIt does what the card asked.\n"


def _post_step_run() -> str:
    doc = yaml.safe_load(QA_REVIEW.read_text())
    for step in doc["jobs"]["review"]["steps"]:
        if step.get("id") == "post":
            return step["run"]
    raise AssertionError("qa-review.yml has no step with id 'post'")


def _resolve_expressions(run: str) -> str:
    for expr, value in {
        "github.repository": "dreadnought-foundry/agent-bureau",
        "github.run_id": "33724409256",
    }.items():
        run = re.sub(r"\$\{\{\s*" + re.escape(expr) + r"\s*\}\}", value, run)
    return run


def _gate(td: Path, verdict_text: str) -> dict:
    """Run BOTH real gate scripts the way the workflow's gate step does —
    check_critic_result.py, then verdict_evidence.py only if it passed —
    and return the step outputs plus the `real` flag they produce."""
    exec_path = td / "claude-execution-output.json"
    exec_path.write_text(json.dumps(COMPLETED))
    verdict_path = td / "qa-verdict.md"
    verdict_path.write_text(verdict_text)
    out_path = td / "github_output"
    out_path.write_text("")

    first = subprocess.run(
        [sys.executable, str(CRITIC_GATE), str(exec_path), str(verdict_path),
         "--github-output", str(out_path)],
        capture_output=True, text=True,
    )
    real = first.returncode == 0
    if real:
        second = subprocess.run(
            [sys.executable, str(EVIDENCE_GATE), "check", str(verdict_path),
             "--github-output", str(out_path),
             "--hold-file", str(td / "qa-evidence-hold.md")],
            capture_output=True, text=True,
        )
        real = second.returncode == 0
    parsed = {"real": "true" if real else "false"}
    for line in out_path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key] = value
    return parsed


def _gh_stub(td: Path, last_edited_at: str = "", fail: bool = False) -> Path:
    """A `gh` that answers the ONE query the post step makes of it.

    `gh pr comment` must still succeed — the step retries it four times with
    sleeps, and a stub that failed would make every test in this file take
    a minute.
    """
    bin_dir = td / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    if fail:
        body = 'if [ "$1" = "api" ]; then exit 1; fi\nexit 0\n'
    else:
        body = (f'if [ "$1" = "api" ]; then printf "%s\\n" '
                f'"{last_edited_at}"; fi\nexit 0\n')
    gh.write_text("#!/usr/bin/env bash\n" + body)
    gh.chmod(0o755)
    return bin_dir


def run_post(verdict_text: str, *, body_read_at: str = "",
             last_edited_at: str = "", gh_fails: bool = False):
    """Execute the post step's real shell over a real gate result."""
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        outputs = _gate(td, verdict_text)
        bin_dir = _gh_stub(td, last_edited_at, gh_fails)
        # The workflow addresses the pipeline's scripts through the
        # checkout agent-task.yml plants beside the product repo.
        (td / ".bureau-pipeline").symlink_to(ROOT)

        run = _resolve_expressions(_post_step_run())
        assert "${{" not in run, "an unresolved expression reached the shell"
        run = run.replace("/tmp/", str(td) + "/")
        script = td / "post.sh"
        script.write_text("set -euo pipefail\n" + run)

        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env.update({
            "CARD": "", "PR": "2247", "REAL": outputs["real"],
            "REVIEWED_SHA": "f" * 40, "CONTENT_ID": "e" * 64,
            "MODEL_ID": "claude-opus-5", "MODEL_WHY": "advisory ladder top",
            "GITHUB_REPOSITORY": "dreadnought-foundry/agent-bureau",
            "A1_OUTCOME": outputs.get("outcome", ""),
            "A1_TURNS": outputs.get("turns", ""),
            "A1_COST": outputs.get("cost", ""),
            "A2_OUTCOME": "", "A2_TURNS": "", "A2_COST": "",
            "A1_EVIDENCE": outputs.get("evidence", ""), "A2_EVIDENCE": "",
            "BODY_READ_AT": body_read_at,
        })
        proc = subprocess.run(["bash", str(script)], cwd=td, env=env,
                              capture_output=True, text=True)
        comment = td / "qa-comment.md"
        return proc, (comment.read_text() if comment.exists() else "")


class UnevidencedVerdictIsHeldTest(unittest.TestCase):
    """#2247's blocker, driven through the real gates and the real shell."""

    def setUp(self):
        self.proc, self.body = run_post(UNEVIDENCED)
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def test_the_rejection_never_reaches_the_pull_request(self):
        # The whole card in one assertion: a finding that asserts an unrun
        # command does not become a REQUEST_CHANGES that spends a fix-loop
        # attempt and an operator decision.
        self.assertNotIn("VERDICT: REQUEST_CHANGES", self.body)

    def test_the_hold_is_not_a_merge_credential(self):
        self.assertNotIn("VERDICT: APPROVE", self.body)
        self.assertNotIn("VERDICT:", self.body)

    def test_merge_gate_still_reads_it_as_the_latest_word(self):
        # Without the marker a stale APPROVE on an earlier commit stays the
        # latest verdict and the pull request merges unreviewed.
        self.assertIn("QA Critic", self.body)

    def test_it_names_the_claim_that_was_not_shown(self):
        self.assertIn("check_tdd_commits.py", self.body)

    def test_it_blames_no_credential(self):
        low = self.body.lower()
        for word in ("auth", "credential", "token", "startup", "crash"):
            self.assertNotIn(word, low)


class EvidencedVerdictPostsTest(unittest.TestCase):
    """The same finding WITH its run attached is a normal rejection. The
    gate must cost a correct review nothing."""

    def setUp(self):
        self.proc, self.body = run_post(EVIDENCED)
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def test_the_verdict_posts(self):
        self.assertIn("VERDICT: REQUEST_CHANGES", self.body)

    def test_the_header_still_binds_the_sha_and_the_content(self):
        head = self.body.splitlines()[0]
        self.assertIn("@" + "f" * 40, head)
        self.assertTrue(head.endswith("content:" + "e" * 64), head)

    def test_the_findings_survive_verbatim(self):
        self.assertIn("b60c929 [test]", self.body)


class ApproveIsUntouchedTest(unittest.TestCase):
    def test_an_approve_posts_unchanged(self):
        proc, body = run_post(APPROVE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("VERDICT: APPROVE", body.splitlines()[0])


class BodySnapshotFooterTest(unittest.TestCase):
    """portico #407's race, walked end to end."""

    READ = "2026-09-03T06:41:40Z"
    EDIT = "2026-09-03T06:43:21Z"

    def test_the_verdict_states_the_snapshot_it_read(self):
        proc, body = run_post(APPROVE, body_read_at=self.READ)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(self.READ, body)

    def test_an_edit_during_the_review_is_flagged_on_the_verdict(self):
        proc, body = run_post(APPROVE, body_read_at=self.READ,
                              last_edited_at=self.EDIT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(self.EDIT, body)
        self.assertIn("no longer exists", body)

    def test_the_footer_never_disturbs_the_parsed_header(self):
        # Every consumer parses `head -1`. The footer rides at the foot.
        proc, body = run_post(EVIDENCED, body_read_at=self.READ,
                              last_edited_at=self.EDIT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        head = body.splitlines()[0]
        self.assertIn("VERDICT: REQUEST_CHANGES", head)
        self.assertTrue(head.endswith("content:" + "e" * 64), head)
        self.assertIn(self.READ, body.splitlines()[-1])

    def test_a_failed_api_read_costs_the_verdict_nothing(self):
        # The footer is a courtesy on a verdict that is already composed.
        # A GitHub blip must never cost a review that finished.
        proc, body = run_post(EVIDENCED, body_read_at=self.READ,
                              gh_fails=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("VERDICT: REQUEST_CHANGES", body)

    def test_no_snapshot_time_stamps_nothing(self):
        proc, body = run_post(APPROVE, last_edited_at=self.EDIT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("PR description read at", body)


if __name__ == "__main__":
    unittest.main()
