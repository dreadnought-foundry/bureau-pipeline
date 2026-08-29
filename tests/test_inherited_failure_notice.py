"""A PR can say "this check is red on main too" (DRE-2820, part 2).

Origin (live, 2026-08-29): the Integration Harness went red on `main` and
stayed red. PRs #199 and #201 inherited the failure — both were green on their
own work, #201 was critic-APPROVED — and nothing on either PR distinguished a
failure the PR CAUSED from one it INHERITED. #199's fix agent spent attempts
trying to clear a check that was never its fault, and fourteen hours passed
before a human learned the cause.

A failing check that ALSO fails on the merge base is not the PR's defect.
`scripts/inherited_failures.py` says so, on the PR, in words a fix agent reads
before it spends an attempt:

  * the comparison is the MERGE BASE, the commit the PR branched from — the
    one place "this was already broken" is provable;
  * a base whose check runs cannot be read reports UNEVALUATED, never a pass
    and never a false "your fault" (console-honesty: derive from truth or say
    you could not);
  * the notice is posted to the PR and written into the fix agent's context,
    so both readers meet the same sentence;
  * it is NOT verdict-shaped and NOT blocker-shaped: it must not read as a
    prior 🛑 blocker to fix_context.py, and must never emit a verdict marker
    (standards/untrusted-content.md).
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF_DIR = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))

import inherited_failures as inh  # noqa: E402

BASE_SHA = "b" * 40
HEAD_SHA = "c" * 40


def runs(*pairs):
    """A check-runs payload in the shape `gh api` returns it."""
    return {
        "check_runs": [
            {"name": name, "status": "completed", "conclusion": conclusion}
            for name, conclusion in pairs
        ]
    }


class InheritedSelectionTest(unittest.TestCase):
    def test_a_check_failing_on_both_sides_is_inherited(self):
        head = runs(("harness", "failure"), ("scripts unit tests", "success"))
        base = runs(("harness", "failure"), ("scripts unit tests", "success"))
        self.assertEqual(["harness"], inh.inherited(head, base))

    def test_a_check_the_pr_broke_is_not_inherited(self):
        head = runs(("scripts unit tests", "failure"), ("harness", "failure"))
        base = runs(("scripts unit tests", "success"), ("harness", "failure"))
        self.assertEqual(["harness"], inh.inherited(head, base))

    def test_a_check_green_on_the_head_is_never_inherited(self):
        head = runs(("harness", "success"))
        base = runs(("harness", "failure"))
        self.assertEqual([], inh.inherited(head, base))

    def test_timed_out_counts_as_failing_on_both_sides(self):
        head = runs(("harness", "timed_out"))
        base = runs(("harness", "failure"))
        self.assertEqual(["harness"], inh.inherited(head, base))

    def test_a_cancelled_base_run_proves_nothing(self):
        # Same rule unfixable_checks already applies: a cancelled run never
        # reported anything, so it cannot excuse a red check on the head.
        head = runs(("harness", "failure"))
        base = runs(("harness", "cancelled"))
        self.assertEqual([], inh.inherited(head, base))

    def test_names_are_matched_case_and_whitespace_insensitively(self):
        head = runs(("Integration Harness / harness", "failure"))
        base = runs(("integration harness /  harness", "failure"))
        self.assertEqual(
            ["Integration Harness / harness"], inh.inherited(head, base),
            "the head's spelling is what the PR reader sees",
        )

    def test_paginated_slurp_payloads_are_read(self):
        head = [runs(("harness", "failure")), runs(("lint", "success"))]
        base = [runs(("harness", "failure"))]
        self.assertEqual(["harness"], inh.inherited(head, base))

    def test_an_unreadable_payload_is_loud(self):
        with self.assertRaises(ValueError):
            inh.inherited("not a payload", runs(("harness", "failure")))


class NoticeWordingTest(unittest.TestCase):
    def setUp(self):
        self.names = ["harness"]
        self.comment = inh.pr_comment(self.names, BASE_SHA, pr=201)
        self.note = inh.agent_note(self.names, BASE_SHA)

    def test_the_pr_comment_names_the_check_and_the_merge_base(self):
        self.assertIn("harness", self.comment)
        self.assertIn(BASE_SHA[:8], self.comment)
        self.assertIn(inh.INHERITED_MARKER, self.comment)

    def test_the_pr_comment_says_it_is_not_this_prs_defect(self):
        self.assertIn("not this pull request's defect", self.comment.lower())
        self.assertIn("merge base", self.comment.lower())

    def test_the_pr_comment_tells_a_fix_agent_what_to_do(self):
        lowered = self.comment.lower()
        self.assertIn("do not spend", lowered)

    def test_the_notice_is_not_blocker_shaped(self):
        # fix_context.py reads a bot comment whose FIRST line opens with 🛑 as
        # a prior blocker. This is not one — it must not steer the next
        # attempt into "hold, the operator has not answered".
        self.assertFalse(self.comment.splitlines()[0].startswith("🛑"))

    def test_the_notice_emits_no_verdict_marker(self):
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(marker, self.comment)
            self.assertNotIn(marker, self.note)

    def test_the_agent_note_carries_the_same_facts(self):
        self.assertIn("harness", self.note)
        self.assertIn(BASE_SHA[:8], self.note)
        self.assertIn("merge base", self.note.lower())


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def _file(self, name, payload):
        path = self.tmp / name
        path.write_text(json.dumps(payload))
        return str(path)

    def _run(self, head, base, extra=()):
        import contextlib
        import io

        out = io.StringIO()
        argv = [
            "decide",
            "--checks-file", self._file("head.json", head),
            "--base-checks-file", self._file("base.json", base),
            "--base-sha", BASE_SHA,
            *extra,
        ]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(
            io.StringIO()
        ):
            code = inh.main(argv)
        return code, out.getvalue().strip().splitlines()

    def test_reports_inherited_and_writes_both_artifacts(self):
        comment = str(self.tmp / "comment.md")
        note = str(self.tmp / "note.md")
        code, lines = self._run(
            runs(("harness", "failure")),
            runs(("harness", "failure")),
            extra=["--comment-out", comment, "--agent-note-out", note],
        )
        self.assertEqual(0, code)
        self.assertEqual(inh.INHERITED, lines[0])
        self.assertIn("harness", Path(comment).read_text())
        self.assertIn("harness", Path(note).read_text())

    def test_reports_own_when_the_base_is_green(self):
        comment = str(self.tmp / "comment.md")
        code, lines = self._run(
            runs(("harness", "failure")),
            runs(("harness", "success")),
            extra=["--comment-out", comment],
        )
        self.assertEqual(0, code)
        self.assertEqual(inh.OWN, lines[0])
        self.assertFalse(
            Path(comment).exists(),
            "no notice when the failure really is this PR's",
        )

    def test_an_unreadable_base_is_unevaluated_never_a_pass(self):
        path = self.tmp / "broken.json"
        path.write_text("{not json")
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()):
            code = inh.main([
                "decide",
                "--checks-file", self._file("head.json",
                                            runs(("harness", "failure"))),
                "--base-checks-file", str(path),
                "--base-sha", BASE_SHA,
            ])
        self.assertEqual(0, code, "the fix loop still runs — today's behavior")
        self.assertEqual(inh.UNEVALUATED, out.getvalue().strip().splitlines()[0])

    def test_an_unreadable_head_exits_loud(self):
        path = self.tmp / "broken-head.json"
        path.write_text("{not json")
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = inh.main([
                "decide",
                "--checks-file", str(path),
                "--base-checks-file", self._file("base.json",
                                                 runs(("harness", "failure"))),
                "--base-sha", BASE_SHA,
            ])
        self.assertEqual(2, code)


class FixLoopWiringTest(unittest.TestCase):
    """agent-fix.yml must run this BEFORE it spends an attempt, and the fix
    prompt must send the agent to the notice."""

    def setUp(self):
        self.src = (WF_DIR / "agent-fix.yml").read_text()

    def test_the_script_is_invoked(self):
        self.assertIn("inherited_failures.py", self.src)

    def test_it_runs_before_the_fix_agent(self):
        self.assertLess(
            self.src.index("inherited_failures.py"),
            self.src.index("uses: anthropics/claude-code-action@v1"),
            "the notice must exist before the agent reads its context",
        )

    def test_the_comparison_is_the_merge_base(self):
        self.assertIn("merge_base_commit", self.src)

    def test_the_notice_is_posted_to_the_pull_request(self):
        self.assertIn("/tmp/inherited-comment", self.src)
        self.assertIn("gh pr comment", self.src)

    def test_the_notice_is_idempotent_per_head_sha(self):
        # The fix-convergence-halt receipt pattern (DRE-2024): a re-fired
        # critic on the same commit must not repost it.
        self.assertIn(inh.INHERITED_MARKER, self.src)

    def test_the_fix_prompt_sends_the_agent_to_the_notice(self):
        self.assertIn("inherited-checks.md", self.src)

    def test_it_is_scoped_to_fix_mode_like_the_unfixable_gate(self):
        step = self.src[self.src.index("inherited_failures.py") - 2000:
                        self.src.index("inherited_failures.py")]
        self.assertIn("steps.pr.outputs.mode == 'fix'", step)


if __name__ == "__main__":
    unittest.main()
