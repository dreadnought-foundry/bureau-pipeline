"""DRE-2584: a crashed critic holds the merge and is not a rejection.

The one behaviour that WORKED in both token outages (2026-08-19 and
2026-08-20) had no test. A critic execution that ended `is_error: true`
with no verdict produced

    "🔎 QA Critic could not run (infra error) — re-review needed, this is
     NOT a code rejection."

and the merge gate held. That is what stopped a dead credential from
reading as a code rejection — and equally from being waved through. Wave 0
found five mechanisms that never worked; the inverse risk is a mechanism
that works today and is quietly broken by tomorrow's refactor. A corpus
that only captures failures leaves every working behaviour undefended.

WHAT IS PINNED, AND WHAT IS NOT. These tests assert BEHAVIOUR — the notice
a crashed review posts, the decision the merge gate reaches on it, and the
fact that a real rejection reaches a DIFFERENT one. They call no private
helper and assert nothing about how either side reaches its answer, so the
refactor this fixture exists to protect stays possible.

The chain runs for real, from fixtures, with no network:

    tests/fixtures/critic-crash-401-2026-08-20.json   the captured crash
      → scripts/check_critic_result.py                 did a review run?
      → qa-review.yml's own `post` step shell          what gets posted
      → scripts/merge_gate.py --comments-file …        does it merge?

THE TRAP THE FIXTURE ENCODES: the crash reports `subtype: "success"`
alongside `is_error: true`. A test — or a refactor — that keys on
`subtype` alone reads a dead 401 as a healthy run.

The post-step harness (`gate_outputs`/`run_post`) is reused from
tests/test_qa_review_no_verdict_message.py rather than re-derived: it
extracts and executes the shipped workflow's real shell, and one copy of
that is the point.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import publish_review_check  # noqa: E402
from test_qa_review_no_verdict_message import run_post  # noqa: E402

GATE = ROOT / "scripts" / "check_critic_result.py"
MERGE_GATE = ROOT / "scripts" / "merge_gate.py"
AGENT_FIX = ROOT / ".github" / "workflows" / "agent-fix.yml"

#: Both outages produced this execution record: authenticated with a
#: rotated-away token, one turn, nothing billed, no verdict file — and a
#: `subtype` that calls itself a success.
CRASH = json.loads(
    (ROOT / "tests" / "fixtures" / "critic-crash-401-2026-08-20.json").read_text()
)

#: A review that genuinely ran and genuinely rejected the code. Same shape
#: the critic writes: the `cause:` tag (DRE-2489) and the mandated summary.
REJECTION_VERDICT = (
    "VERDICT: REQUEST_CHANGES cause:defect\n"
    "\n"
    "## Summary\n"
    "The retry loop swallows the 500 and reports success.\n"
)
APPROVAL_VERDICT = "VERDICT: APPROVE\n\n## Summary\nReads clean.\n"
CLEAN_RUN = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 34,
    "total_cost_usd": 2.71,
    "result": "I reviewed the diff.",
}

#: The commit the post step binds a verdict to (`REVIEWED_SHA` in the
#: reused harness). The gate only honours a verdict bound to the head, so
#: the merge-gate rows below use it as the head — asserted, not assumed.
HEAD = "a" * 40
QA_LOGIN = "agent-bureau-qa-bot[bot]"


class Review:
    """What one critic run produced: did a genuine review happen, and what
    landed on the pull request."""

    def __init__(self, real: bool, comment: str, log: str):
        self.real = real
        self.comment = comment
        self.log = log


def run_review(execution, verdict_text=None) -> Review:
    """Drive one critic attempt exactly as qa-review.yml wires it.

    The gate's EXIT STATUS is what the workflow turns into `real=`, and
    `real` is what the post step branches on — so running the real script
    and the real shell reproduces the decision the pull request sees.
    """
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        exec_path = td / "claude-execution-output.json"
        verdict_path = td / "qa-verdict.md"
        if execution is not None:
            exec_path.write_text(json.dumps(execution))
        if verdict_text is not None:
            verdict_path.write_text(verdict_text)
        out_path = td / "github_output"
        out_path.touch()
        gate = subprocess.run(
            [sys.executable, str(GATE), str(exec_path), str(verdict_path),
             "--github-output", str(out_path)],
            capture_output=True, text=True, check=False,
        )
        real = gate.returncode == 0
        outputs = {}
        for line in out_path.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                outputs[key] = value
        proc, comment = run_post(td, outputs,
                                 real="true" if real else "false")
        assert proc.returncode == 0, proc.stderr
        return Review(real, comment, gate.stdout)


def comment_json(*bodies) -> list:
    """A `GET issues/{pr}/comments` payload, all authored by the qa-bot —
    the only author the gate counts."""
    return [{"user": {"login": QA_LOGIN}, "body": b} for b in bodies]


def gate_decision(*bodies) -> tuple[str, str]:
    """(decision, reason) from the real merge gate, fed from files.

    Everything else about the pull request is merge-ready: CI green, head
    current. The only variable across these rows is what the critic said,
    so a `merge` that does not happen is the critic condition's doing —
    `test_an_approve_on_this_same_pr_does_merge` proves the row is live.
    """
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        files = {
            "checks.json": {"check_runs": [{
                "name": "tests", "status": "completed",
                "conclusion": "success", "check_suite": {"id": 41},
            }]},
            "comments.json": comment_json(*bodies),
            "runs.json": {"workflow_runs": []},
            "compare.json": {"status": "ahead", "files": []},
        }
        for name, payload in files.items():
            (td / name).write_text(json.dumps(payload))
        proc = subprocess.run(
            [sys.executable, str(MERGE_GATE),
             "--head-sha", HEAD, "--qa-login", QA_LOGIN,
             "--check-runs-file", str(td / "checks.json"),
             "--comments-file", str(td / "comments.json"),
             "--workflow-runs-file", str(td / "runs.json"),
             "--compare-file", str(td / "compare.json")],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0, proc.stderr
        parsed = dict(
            line.split("=", 1) for line in proc.stdout.splitlines()
            if "=" in line
        )
        return parsed.get("decision", ""), parsed.get("reason", "")


def fix_agent_trigger_phrase() -> str:
    """The comment text that wakes the fix agent, read from agent-fix.yml's
    own job condition — so a rename moves this test with it."""
    m = re.search(
        r"contains\(github\.event\.comment\.body,\s*'([^']+)'\)",
        AGENT_FIX.read_text(),
    )
    assert m, "agent-fix.yml no longer triggers on a comment body phrase"
    return m.group(1)


def wakes_the_fix_agent(comment: str) -> bool:
    return fix_agent_trigger_phrase() in comment


class TheCrashCallsItselfASuccessTest(unittest.TestCase):
    """The captured signature, and the trap inside it."""

    def test_the_record_reports_success_and_an_error_at_once(self):
        self.assertEqual(CRASH["subtype"], "success")
        self.assertIs(CRASH["is_error"], True)
        self.assertEqual(CRASH["num_turns"], 1)
        self.assertEqual(CRASH["total_cost_usd"], 0)

    def test_a_success_shaped_crash_is_still_not_a_review(self):
        # Anything reading `subtype` alone calls this healthy. It ran one
        # turn, billed nothing, and produced no verdict: no review happened.
        self.assertFalse(run_review(CRASH).real)

    def test_a_stale_verdict_file_does_not_rescue_it(self):
        # The trap with teeth. A crash leaves whatever the runner already
        # had on disk; read `subtype` alone and this run looks healthy, so
        # a stale APPROVE from an earlier head becomes THIS head's verdict
        # and the pull request merges on a review that never happened.
        crashed = run_review(CRASH, APPROVAL_VERDICT)
        self.assertFalse(crashed.real)
        self.assertIn("could not run (infra error)", crashed.comment)
        self.assertNotIn("VERDICT: APPROVE", crashed.comment)
        self.assertNotEqual(gate_decision(crashed.comment)[0], "merge")

    def test_the_run_log_says_why_it_died(self):
        # The operator's only surviving record — both outages were diagnosed
        # from it. A gate that fails silently sends people credential-hunting.
        log = run_review(CRASH).log
        self.assertIn("401", log)


class ACrashedCriticPostsTheInfraNoticeTest(unittest.TestCase):
    """Acceptance 1: the message, and what it is not."""

    def setUp(self):
        self.comment = run_review(CRASH).comment

    def test_it_says_the_critic_could_not_run(self):
        self.assertIn("could not run (infra error)", self.comment)
        self.assertIn("re-review needed", self.comment)

    def test_it_says_out_loud_that_this_is_not_a_code_rejection(self):
        # The whole point: a dead credential must not read as a verdict on
        # the code. The CEO reads this line; it is not decoration.
        self.assertIn("not a code rejection", self.comment.lower())
        self.assertIn("not a request for changes", self.comment.lower())

    def test_it_is_not_recorded_as_a_request_for_changes(self):
        self.assertNotIn("VERDICT: REQUEST_CHANGES", self.comment)

    def test_it_does_not_wake_the_fix_agent(self):
        # A false reject churns a good PR into the fix loop — the
        # #1441/#1442 / DRE-1330 damage this branch was written to stop.
        self.assertFalse(wakes_the_fix_agent(self.comment))

    def test_the_head_bound_check_records_a_crash_not_a_rejection(self):
        conclusion, title, summary = publish_review_check.decide(
            real=False, verdict="")
        self.assertEqual(conclusion, "failure")
        self.assertNotIn("REQUEST_CHANGES", title)
        self.assertIn("NOT a code rejection", summary)


class ACrashedCriticHoldsTheMergeTest(unittest.TestCase):
    """Acceptance 1 and 2: it holds, and nothing about it merges."""

    def test_the_gate_does_not_merge_on_the_crash_notice(self):
        decision, _ = gate_decision(run_review(CRASH).comment)
        self.assertNotEqual(decision, "merge")

    def test_it_does_not_merge_even_over_an_earlier_bound_approve(self):
        # The dangerous shape: an APPROVE bound to this very head, then the
        # crash. The notice is the latest word and it supersedes the
        # approval — otherwise a crashed re-review rides a stale APPROVE in.
        approve = f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}"
        decision, _ = gate_decision(approve, run_review(CRASH).comment)
        self.assertNotEqual(decision, "merge")

    def test_the_notice_carries_no_approval_of_any_kind(self):
        self.assertNotIn("VERDICT: APPROVE", run_review(CRASH).comment)

    def test_an_approve_on_this_same_pr_does_merge(self):
        # The control. Everything else in these rows is merge-ready, so the
        # holds above are the crash's doing and not a broken fixture.
        decision, _ = gate_decision(f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}")
        self.assertEqual(decision, "merge")


class ARealRejectionStaysDistinguishableTest(unittest.TestCase):
    """Acceptance 3: fixing one must not collapse them."""

    def setUp(self):
        self.rejection = run_review(CLEAN_RUN, REJECTION_VERDICT)
        self.crash = run_review(CRASH)

    def test_a_real_rejection_is_a_genuine_review(self):
        self.assertTrue(self.rejection.real)
        self.assertIn("VERDICT: REQUEST_CHANGES", self.rejection.comment)
        self.assertIn(f"@{HEAD}", self.rejection.comment)

    def test_a_real_rejection_wakes_the_fix_agent_and_the_crash_does_not(self):
        self.assertTrue(wakes_the_fix_agent(self.rejection.comment))
        self.assertFalse(wakes_the_fix_agent(self.crash.comment))

    def test_the_gate_reaches_a_different_decision_for_each(self):
        rejected, rejected_why = gate_decision(self.rejection.comment)
        crashed, crashed_why = gate_decision(self.crash.comment)
        self.assertNotEqual(rejected, "merge")
        self.assertNotEqual(crashed, "merge")
        # A rejection is a judgement on the code and holds; a crash is an
        # absence of one and waits for a review to actually happen. Collapse
        # them and either the fix loop runs on findings nobody made, or a
        # crash starts to look like a decision.
        self.assertNotEqual(
            rejected, crashed,
            "a crashed critic and a real rejection now reach the same "
            f"decision ({rejected!r}): {rejected_why} / {crashed_why}",
        )

    def test_the_head_bound_check_states_the_rejection_as_a_rejection(self):
        _, title, _ = publish_review_check.decide(
            real=True, verdict=REJECTION_VERDICT)
        self.assertIn("REQUEST_CHANGES", title)

    def test_a_genuine_approval_still_merges_and_the_crash_still_does_not(self):
        approved = run_review(CLEAN_RUN, APPROVAL_VERDICT)
        self.assertTrue(approved.real)
        self.assertEqual(gate_decision(approved.comment)[0], "merge")
        self.assertNotEqual(gate_decision(self.crash.comment)[0], "merge")


if __name__ == "__main__":
    unittest.main()
