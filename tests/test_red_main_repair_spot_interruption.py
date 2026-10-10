"""A Spot machine taken away is infrastructure to the red-main repair (DRE-6575).

On 2026-10-09 21:14 PT agent-bureau run 38022768296 went red on `main`: AWS
took back the Spot machine job 114128397483 (`Toolkit (pytest)`) was running
on, and the job died with exit code 137. No test failed. Attempt 2 of the same
run, same commit, went green at 21:34. The repair did not know RunsOn's
sentence, so it sent an agent at a `main` that was never broken, and the agent
left a "Red main needs a decision" card in Triage (DRE-6563) — the second in
three days, after DRE-6262 on 2026-10-07.

This file pins that:

  * the job's two error lines, replayed as `gh run view --log-failed` prints
    them, answer `infra-backoff` with `go` false and no escalation, and name
    the line that matched;
  * the sentence is read only on lines the run itself said
    (`medic_classify.run_lines`, DRE-6522) — a passing test id that quotes it
    still dispatches;
  * `exit code 137` alone is not the signature — it is also an out-of-memory
    kill, which a fix agent can act on;
  * the sentence is DRE-6572's, read from `fix_dead_run`, never a second copy.

No test here is parametrized with a log line, on purpose: its id would be one
more passing-test line carrying the very text the repair must ignore.
"""

import contextlib
import io
import os
import re
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

import fix_dead_run  # noqa: E402
import red_main_repair  # noqa: E402

SHA = "d" * 40

# How `gh run view --log-failed` attributes a line of job 114128397483.
_PREFIX = "Toolkit (pytest)\tRun tests\t2026-10-10T04:14:40.1234567Z "

# The job's last two errors, as RunsOn and the runner wrote them.
SPOT_LINE = (
    "##[error]AWS interrupted the EC2 Spot instance running this job. "
    "RunsOn can retry it after the workflow run finishes."
)
EXIT_137_LINE = "##[error]Process completed with exit code 137."

SPOT_LOG = (
    _PREFIX + "tests/test_toolkit.py::test_one PASSED [ 12%]\n"
    + _PREFIX + SPOT_LINE + "\n"
    + _PREFIX + EXIT_137_LINE + "\n"
)

# A plain failing test, with no Spot line anywhere.
PLAIN_FAILURE_LOG = (
    _PREFIX + "=== FAILURES ===\n"
    + _PREFIX + "    def test_widget_count():\n"
    + _PREFIX + ">       assert count_widgets() == 3\n"
    + _PREFIX + "E       assert 4 == 3\n"
    + _PREFIX + "FAILED tests/test_widgets.py::test_widget_count - assert 4 == 3\n"
    + _PREFIX + "=== 1 failed, 41 passed ===\n"
    + _PREFIX + "##[error]Process completed with exit code 1.\n"
)

# That plain failure, beside a PASSING test whose id quotes the sentence.
QUOTED_BY_A_PASSING_TEST_LOG = (
    _PREFIX + f"tests/test_runner_lost.py::test_reads[{SPOT_LINE}] "
    "PASSED [ 43%]\n"
    + PLAIN_FAILURE_LOG
)


def _decide(log_text, workflow_name="Toolkit"):
    return red_main_repair.decide(
        conclusion="failure",
        head_branch="main",
        default_branch="main",
        head_sha=SHA,
        log_text=log_text,
        refs=[],
        pulls=[],
        workflow_name=workflow_name,
    )


class TheSpotInterruptionOf20261009Test(unittest.TestCase):
    """Job 114128397483's two error lines back the repair off."""

    def test_it_answers_infra_backoff(self):
        d = _decide(SPOT_LOG)
        self.assertEqual(d["reason"], "infra-backoff")
        self.assertFalse(d["go"])
        self.assertFalse(d["escalate"])

    def test_it_names_the_spot_line(self):
        d = _decide(SPOT_LOG)
        self.assertEqual(d["infra_line"], SPOT_LINE)
        self.assertIn("AWS interrupted the EC2 Spot instance",
                      d["infra_signature"])

    def test_the_step_log_names_the_line(self):
        with tempfile.TemporaryDirectory() as td:
            paths = {}
            for key, body in (("log", SPOT_LOG), ("refs", "[]"),
                              ("pulls", "[]")):
                paths[key] = os.path.join(td, key)
                with open(paths[key], "w", encoding="utf-8") as f:
                    f.write(body)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = red_main_repair.main([
                    "decide", "--conclusion", "failure",
                    "--head-branch", "main", "--default-branch", "main",
                    "--head-sha", SHA, "--log-file", paths["log"],
                    "--refs-file", paths["refs"],
                    "--pulls-file", paths["pulls"],
                    "--workflow-name", "Toolkit",
                ])
        self.assertEqual(rc, 0)
        self.assertIn("go=false", out.getvalue())
        self.assertIn("escalate=false", out.getvalue())
        self.assertIn("reason=infra-backoff", out.getvalue())
        said = re.findall(r"^repair decide: infra-backoff matched .+? "
                          r"on the line: (.*)$", err.getvalue(), re.M)
        self.assertEqual(said, [SPOT_LINE[:red_main_repair.INFRA_LINE_CHARS]])

    def test_exit_code_137_alone_still_dispatches(self):
        # 137 is also an out-of-memory kill — something a fix agent can act on.
        d = _decide(_PREFIX + EXIT_137_LINE + "\n")
        self.assertTrue(d["go"])
        self.assertEqual(d["reason"], "dispatch")


class OnlyWhatTheRunSaidTest(unittest.TestCase):
    """The sentence is read through `medic_classify.run_lines` (DRE-6522)."""

    def test_a_passing_test_id_quoting_it_does_not_match(self):
        self.assertIsNone(
            red_main_repair.infra_match(QUOTED_BY_A_PASSING_TEST_LOG, "Toolkit"))

    def test_a_passing_test_id_quoting_it_still_dispatches(self):
        d = _decide(QUOTED_BY_A_PASSING_TEST_LOG)
        self.assertTrue(d["go"])
        self.assertEqual(d["reason"], "dispatch")

    def test_a_plain_test_failure_still_dispatches(self):
        d = _decide(PLAIN_FAILURE_LOG)
        self.assertTrue(d["go"])
        self.assertEqual(d["reason"], "dispatch")
        self.assertFalse(d["escalate"])


class OneCopyOfTheSentenceTest(unittest.TestCase):
    """DRE-6572 wrote the sentence first; the repair reads that one."""

    def test_the_signature_is_fix_dead_runs_wording(self):
        patterns = [s.pattern for s in red_main_repair.INFRA_SIGNATURES]
        self.assertIn(fix_dead_run.SPOT_INTERRUPTED, patterns)
        self.assertIn(fix_dead_run.SPOT_INTERRUPTED,
                      fix_dead_run.RUNNER_LOST_WORDINGS)

    def test_the_wording_is_a_plain_sentence(self):
        # Compiled as written, so it must hold no regex metacharacter.
        words = fix_dead_run.SPOT_INTERRUPTED.split(" ")
        self.assertEqual([re.escape(w) for w in words], words)


if __name__ == "__main__":
    unittest.main()
