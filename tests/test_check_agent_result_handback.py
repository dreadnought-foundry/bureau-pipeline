"""RED-first tests for the hand-back outcome of the agent-result gate (DRE-4376).

Origin (2026-09-19, run 35466962427 on DRE-4322): a build agent decided its
card was an epic's worth of work and handed it back to Planning — the exit its
own brief defines for exactly that case. Everything downstream behaved: the
note was read, a plain-English comment landed on the card, the card moved to
Planning. The run was still marked FAILED, because `check_agent_result.py` had
never been taught the exit: it saw no branch, no PR, no blocker note and no
escalation note, and called a correct hand-back a silent death. A red run wakes
the medic, the medic diagnoses a "pipeline failure", and that diagnosis sat in
the CEO's Green Light queue (DRE-4331) for 20 hours.

The gate accepts a hand-back as the FIFTH legitimate outcome. The rules it is
held to here:

  * a non-empty hand-back note with no branch and no PR ends the gate GREEN,
    and the gate says which outcome it saw;
  * an empty or whitespace-only note is not an outcome — it fails exactly as
    today, the same rule the blocker and escalation notes follow;
  * a hand-back note ALONGSIDE a pull request is a contradiction and fails,
    rather than the gate silently picking one;
  * the four existing outcomes are untouched (tests/test_check_agent_result.py
    passes unmodified).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_agent_result  # noqa: E402


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "scripts", "check_agent_result.py")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-task.yml")

# The exact sentence the gate fails a genuine silent death with. Pinned here
# because the fix must NOT widen it: fixtures and the medic's own log-reading
# carry this string (tests/fixtures/agent-task-turn-exhaustion-2026-09-01.log).
NO_EVIDENCE = "no agent branch, no PR, no blocker note, and no escalation note"


class FailureReasonHandbackTest(unittest.TestCase):
    """The predicate itself — the gate's one decision, read directly."""

    def test_handback_note_alone_is_an_accepted_outcome(self):
        # The DRE-4322 shape: the agent wrote its list of pieces, opened no PR,
        # pushed nothing. That is the designed exit, not a silent death.
        self.assertIsNone(
            check_agent_result.failure_reason(
                {"is_error": False}, branch_exists=False, handback_note=True
            )
        )

    def test_no_handback_note_still_fails_with_the_same_sentence(self):
        self.assertEqual(
            check_agent_result.failure_reason(
                {"is_error": False}, branch_exists=False, handback_note=False
            ),
            NO_EVIDENCE,
        )

    def test_handback_alongside_a_pull_request_is_a_contradiction(self):
        # Both cannot be true: the agent either handed the card back to
        # Planning or it shipped the work. Picking one silently would either
        # send a shipped card back to Planning or bury a hand-back.
        reason = check_agent_result.failure_reason(
            {"is_error": False},
            branch_exists=True,
            pr_exists=True,
            handback_note=True,
        )
        self.assertIsNotNone(reason)
        self.assertIn("hand-back", reason.lower())
        self.assertIn("pull request", reason.lower())

    def test_a_pull_request_without_a_handback_is_untouched(self):
        self.assertIsNone(
            check_agent_result.failure_reason(
                {"is_error": False}, branch_exists=True, pr_exists=True
            )
        )

    def test_is_error_still_beats_a_hand_back(self):
        # A run that died mid-way may have left a half-written note; the death
        # is the louder fact and the flagless gate still reports it.
        self.assertEqual(
            check_agent_result.failure_reason(
                {"is_error": True}, branch_exists=False, handback_note=True
            ),
            "execution result has is_error=true",
        )


class HasHandbackNoteTest(unittest.TestCase):
    """Whitespace is not a list of pieces."""

    def _note(self, text):
        fh = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        fh.write(text)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_real_note_is_a_note(self):
        self.assertTrue(
            check_agent_result.has_handback_note(
                self._note("1. the gate's new outcome\n2. the workflow wiring\n")
            )
        )

    def test_empty_note_is_not_a_note(self):
        self.assertFalse(check_agent_result.has_handback_note(self._note("")))

    def test_whitespace_only_note_is_not_a_note(self):
        self.assertFalse(
            check_agent_result.has_handback_note(self._note("  \n\t\n  \n"))
        )

    def test_missing_path_is_not_a_note(self):
        self.assertFalse(check_agent_result.has_handback_note(""))
        self.assertFalse(
            check_agent_result.has_handback_note("/tmp/definitely-not-here-4376.txt")
        )


class CliHandbackTest(unittest.TestCase):
    """The form agent-task.yml actually calls."""

    def _run(self, payload, branch="", pr="", blocker_file="", extra=()):
        with tempfile.TemporaryDirectory() as td:
            exec_path = os.path.join(td, "out.json")
            if payload is not None:
                with open(exec_path, "w") as f:
                    json.dump(payload, f)
            return subprocess.run(
                [sys.executable, GATE, exec_path, branch, pr, blocker_file, *extra],
                capture_output=True, text=True,
                env={**os.environ, "PATH": os.environ["PATH"]},
            )

    def _note(self, text):
        fh = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        fh.write(text)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_cli_exit_0_and_names_the_outcome_on_a_hand_back(self):
        note = self._note(
            "1. teach the result gate the hand-back exit\n"
            "2. wire the file through agent-task.yml\n"
        )
        p = self._run({"is_error": False}, extra=("--handback-file", note))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("ok", out)
        self.assertIn("hand-back", out.lower())

    def test_cli_empty_hand_back_note_does_not_exempt(self):
        p = self._run({"is_error": False},
                      extra=("--handback-file", self._note("")))
        self.assertEqual(p.returncode, 1)
        self.assertIn(NO_EVIDENCE, p.stdout + p.stderr)

    def test_cli_whitespace_hand_back_note_does_not_exempt(self):
        p = self._run({"is_error": False},
                      extra=("--handback-file", self._note("   \n\n\t")))
        self.assertEqual(p.returncode, 1)
        self.assertIn(NO_EVIDENCE, p.stdout + p.stderr)

    def test_cli_missing_hand_back_file_does_not_exempt(self):
        # The normal run: no hand-back was written, the path simply is not
        # there. It must read as absence, never as an outcome.
        p = self._run({"is_error": False},
                      extra=("--handback-file", "/tmp/nope-4376.txt"))
        self.assertEqual(p.returncode, 1)
        self.assertIn(NO_EVIDENCE, p.stdout + p.stderr)

    def test_cli_hand_back_with_a_pr_exits_1_as_a_contradiction(self):
        note = self._note("1. one piece\n2. another piece\n")
        p = self._run({"is_error": False}, branch="agent/DRE-4376",
                      pr="https://github.com/x/y/pull/1",
                      extra=("--handback-file", note))
        out = (p.stdout + p.stderr).lower()
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("hand-back", out)
        self.assertIn("pull request", out)

    def test_cli_the_four_existing_outcomes_still_exit_0(self):
        blocker = self._note("cannot be built as written")
        escalation = self._note("Should free-tier users see X?")
        for label, kwargs in (
            ("branch", dict(branch="agent/DRE-4376")),
            ("pull request", dict(branch="agent/DRE-4376", pr="http://pr")),
            ("blocker note", dict(blocker_file=blocker)),
            ("escalation note", dict(extra=("--escalation-file", escalation))),
        ):
            with self.subTest(outcome=label):
                p = self._run({"is_error": False}, **kwargs)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


def _step_block(workflow_text, name):
    """The RUNNABLE lines of one named workflow step, up to the next step.

    Comment-only lines are dropped: both steps explain themselves at length,
    and a path named in prose is not a path either step reads.
    """
    start = workflow_text.index(f"- name: {name}")
    rest = workflow_text[start + 1:]
    m = re.search(r"^ {6}- name: ", rest, re.MULTILINE)
    block = rest[: m.start()] if m else rest
    return "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )


class WorkflowWiringTest(unittest.TestCase):
    """The path the gate reads IS the path the Report step reads.

    Two steps naming the same file in two string literals is exactly how the
    gate came to be blind to an exit the workflow already handled. A typo in
    either one silently returns the DRE-4322 failure — green comment on the
    card, red run, medic woken — so the agreement is pinned in source.
    """

    def setUp(self):
        with open(WORKFLOW, encoding="utf-8") as fh:
            self.wf = fh.read()

    def test_the_gate_step_passes_a_hand_back_file(self):
        gate = _step_block(self.wf, "Gate on agent result")
        self.assertRegex(gate, r"--handback-file\s+\S+")

    def test_the_gate_reads_the_path_the_report_step_reads(self):
        gate = _step_block(self.wf, "Gate on agent result")
        report = _step_block(self.wf, "Report result to Linear")

        gate_path = re.search(r"--handback-file\s+(\S+)", gate).group(1)

        # The Report step's hand-back branch is the one that moves the card to
        # Planning — found from that move, never from a path typed twice.
        planning = report.index('advance "$CARD" "Planning"')
        guards = list(re.finditer(r"\[ -f (\S+) \] && \[ -s (\S+) \]",
                                  report[:planning]))
        self.assertTrue(guards, "the Report step guards no file before Planning")
        report_path = guards[-1].group(1)

        self.assertEqual(
            gate_path, report_path,
            "the result gate and the Report step disagree about where the "
            "agent writes its hand-back note",
        )
        self.assertEqual(guards[-1].group(1), guards[-1].group(2))


if __name__ == "__main__":
    unittest.main()
