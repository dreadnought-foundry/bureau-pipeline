"""DRE-5927: the fix agent is kept off a proof record.

A proof run opens its record on `agent/DRE-<n>-proof-record` (DRE-5921,
DRE-5924), and `proof_dispatch.proof_record_branch` is true for exactly that
shape (DRE-5926). A record the critic sends back holds rows that were not
observed, or were observed wrongly, and only a run holding the proof
identities can repair those. On portico #911 (DRE-5591) the fix agent was
dispatched at one anyway, found it had no AWS credentials and stopped: a run
spent establishing that nothing could be fixed.

Two refusals, and these tests pin both:

1. `scripts/resolve_fix_pr.sh`, the Resolve step every Agent Fix run passes
   on every leg (the comment-triggered start and every `workflow_dispatch`),
   ends a proof-record run with go=false, no_work=true and a `fix-refused:`
   line before any mode or budget is resolved.
2. `reconcile.fix_dispatch_blocked`, the sweep's gate, refuses a proof-record
   head ref before any Linear read, so the conflict backstop dispatches
   nothing for it.

The operator-decision restart (`restart_answered_blockers`) is left alone on
purpose: it never consults the gate, and its one dispatch per answer ends at
refusal 1.
"""

from __future__ import annotations

import ast
import inspect
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "resolve_fix_pr.sh"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import linear_ops  # noqa: E402
import reconcile  # noqa: E402
from test_hand_dispatch_no_work import ATTEMPT, GH_STUB, WORKER, rest  # noqa: E402

RECORD = "agent/DRE-123-proof-record"
ORDINARY = "agent/DRE-123-slug"


def _resolve(branch: str, event: str, actor: str, merge_state: str = "CLEAN"):
    """Run the shipped Resolve script by path against a stubbed `gh`.

    Returns (step outputs, the fix-no-work.txt line or None, the files the
    script left in its temp dir, the PR comments it posted)."""
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)
        # The pipeline checkout the job takes before this step.
        (td / ".bureau-pipeline").symlink_to(ROOT)
        info = td / "pr-info.json"
        info.write_text(json.dumps({
            "state": "OPEN", "headRefName": branch,
            "headRefOid": "d9f2c1ab" + "0" * 32, "mergeStateStatus": merge_state,
        }))
        comments = td / "comments.json"
        comments.write_text(json.dumps([rest(WORKER, ATTEMPT.format(n=1))]))
        out_file = td / "step-output"
        out_file.write_text("")
        log = td / "gh-writes.jsonl"
        proc = subprocess.run(
            ["bash", str(SCRIPT)], cwd=td,
            env=dict(
                os.environ,
                PATH=f"{td}/bin:{os.environ['PATH']}",
                GITHUB_OUTPUT=str(out_file), RUNNER_TEMP=str(td),
                GH_PR_INFO=str(info), GH_COMMENTS=str(comments), GH_LOG=str(log),
                GH_TOKEN="test", WRITER_TOKEN="test-writer",
                WORKER_LOGIN=WORKER, EVENT_NAME=event, TRIGGERING_ACTOR=actor,
                PR_NUMBER="911", REPO="dreadnought-foundry/portico",
            ),
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            raise AssertionError(f"resolve_fix_pr.sh exited {proc.returncode}: {proc.stderr}")
        outputs = dict(
            line.partition("=")[::2]
            for line in out_file.read_text().splitlines() if "=" in line
        )
        note = td / "fix-no-work.txt"
        line = note.read_text().strip() if note.exists() else None
        left = {p.name for p in td.iterdir()}
        posted = log.read_text() if log.exists() else ""
    return outputs, line, left, posted


class TheResolveStepRefusesARecordTest(unittest.TestCase):
    """Refusal 1 — the step every leg of agent-fix.yml reaches."""

    LEGS = (
        # The qa-bot's REQUEST_CHANGES or a person's Operator decision.
        ("issue_comment", "agent-bureau-qa-bot[bot]"),
        # The merge gate's DIRTY arm, the sweep's backstop and restart.
        ("workflow_dispatch", "github-actions"),
        # A person dispatching by hand.
        ("workflow_dispatch", "smeed652"),
    )

    def test_every_leg_ends_at_the_refusal(self):
        for event, actor in self.LEGS:
            for merge_state in ("CLEAN", "DIRTY"):
                with self.subTest(event=event, actor=actor, merge_state=merge_state):
                    outputs, line, left, posted = _resolve(RECORD, event, actor, merge_state)
                    self.assertEqual(outputs.get("go"), "false")
                    self.assertEqual(outputs.get("no_work"), "true")
                    self.assertEqual(
                        line,
                        f"fix-refused: #911 is a proof record ({RECORD}) — the proof "
                        "run re-observes it, the fix agent does not patch it",
                    )
                    # Before any mode or budget is resolved: no mode written,
                    # the thread never read, the budget never decided.
                    self.assertNotIn("mode", outputs)
                    self.assertNotIn("attempt", outputs)
                    self.assertNotIn("thread.json", left)
                    self.assertNotIn("fix-budget.env", left)
                    # The refusal is the run's notice, never a PR comment.
                    self.assertEqual(posted, "")

    def test_an_ordinary_card_branch_is_unchanged(self):
        for event, actor in self.LEGS[:2]:
            with self.subTest(event=event, actor=actor):
                outputs, line, left, _ = _resolve(ORDINARY, event, actor)
                self.assertEqual(outputs.get("go"), "true")
                self.assertEqual(outputs.get("mode"), "fix")
                self.assertEqual(outputs.get("card"), "DRE-123")
                self.assertNotIn("no_work", outputs)
                self.assertIn("thread.json", left)
                self.assertFalse(line and line.startswith("fix-refused:"))

    def test_a_near_miss_is_an_ordinary_branch(self):
        # Only the exact shape is a record; a slug that merely contains the
        # words keeps today's path.
        for branch in ("agent/DRE-123-proof-record-notes", "agent/DRE-123-the-proof-record-fix"):
            with self.subTest(branch=branch):
                outputs, line, _, _ = _resolve(branch, "issue_comment", "agent-bureau-qa-bot[bot]")
                self.assertEqual(outputs.get("go"), "true")
                self.assertFalse(line and line.startswith("fix-refused:"))

    def test_the_branch_reaches_python_as_an_argument_never_as_code(self):
        # The head ref is author-controlled text; the predicate reads it from
        # argv, so the script never splices it into a python -c program.
        text = SCRIPT.read_text()
        self.assertTrue("proof_record_branch(sys.argv[1])" in text,
                        "the Resolve script does not pass the head ref as argv")
        self.assertFalse('proof_record_branch("$BRANCH")' in text,
                         "the Resolve script splices the head ref into code")


def _pr(branch=RECORD, state="DIRTY"):
    return {"number": 911, "headRefName": branch, "mergeStateStatus": state,
            "headRefOid": "d9f2c1ab" + "0" * 32, "comments": []}


class TheSweepGateRefusesARecordTest(unittest.TestCase):
    """Refusal 2 — `fix_dispatch_blocked`, which the sweep's dispatch sites call."""

    def test_a_record_is_blocked_with_no_linear_read(self):
        out = io.StringIO()
        with mock.patch.object(linear_ops, "gql") as gql, \
                mock.patch.object(reconcile, "card_parked_for_human") as parked, \
                redirect_stdout(out):
            blocked = reconcile.fix_dispatch_blocked(_pr())
        self.assertTrue(blocked)
        gql.assert_not_called()
        parked.assert_not_called()
        self.assertIn(
            "park-gate: PR #911 is a proof record — the proof run re-observes it, "
            "not the fix agent",
            out.getvalue(),
        )

    def test_an_ordinary_branch_still_asks_linear(self):
        out = io.StringIO()
        with mock.patch.object(reconcile, "card_parked_for_human", return_value=False) as parked, \
                redirect_stdout(out):
            blocked = reconcile.fix_dispatch_blocked(_pr(branch=ORDINARY))
        self.assertFalse(blocked)
        parked.assert_called_once_with("DRE-123")
        self.assertNotIn("proof record", out.getvalue())


class TheConflictBackstopDispatchesNothingTest(unittest.TestCase):
    def _dispatch(self, branch):
        calls = []
        lane = reconcile.FixLane({}, 0)
        with mock.patch.object(reconcile, "gh_dispatch", side_effect=lambda *a: calls.append(a)), \
                mock.patch.object(reconcile, "card_parked_for_human", return_value=False), \
                mock.patch.object(reconcile, "fix_agent_absent_hold", return_value=False), \
                redirect_stdout(io.StringIO()):
            reconcile._dispatch_conflict_fix(_pr(branch=branch), lane)
        return calls

    def test_a_dirty_record_dispatches_nothing(self):
        self.assertEqual(self._dispatch(RECORD), [])

    def test_a_dirty_ordinary_pr_still_dispatches(self):
        # The non-vacuous twin: the identical PR on a card branch dispatches.
        calls = self._dispatch(ORDINARY)
        self.assertEqual(len(calls), 1)
        self.assertIn("pr_number=911", calls[0])


class TheOperatorRestartIsUnchangedTest(unittest.TestCase):
    """An operator's answer is new input: the restart never consults the gate,
    and this card does not teach it about records either."""

    def test_the_restart_neither_calls_the_gate_nor_reads_the_suffix(self):
        source = textwrap.dedent(inspect.getsource(reconcile.restart_answered_blockers))
        tree = ast.parse(source)
        called = {
            node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        self.assertNotIn("fix_dispatch_blocked", called)
        self.assertNotIn("proof_record_branch", source)


if __name__ == "__main__":
    unittest.main()
