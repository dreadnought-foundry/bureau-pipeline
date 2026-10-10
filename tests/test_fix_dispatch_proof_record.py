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

DRE-6571 cut one exception into both, and only one: a record red on a CHECK —
a failed non-review check run at the head, with no `proof record not proven`
decline from the gate there (`proof_dispatch.record_red_checks`) — is the fix
agent's, for the check only. The approved-but-red sweep dispatches it once per
head and says so on the card; the Resolve step admits that dispatch and no
other start; the Report step names a fix commit that changes the record. The
replay below is bureau-pipeline #896 on 2026-10-09, check runs as GitHub
returned them for a89e0ba1.
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

# --------------------------------------------------------------------------
# bureau-pipeline #896, 2026-10-09 (DRE-6571)
# --------------------------------------------------------------------------
REPLAY_CARD = "DRE-6381"
REPLAY_BRANCH = "agent/DRE-6381-proof-record"
REPLAY_HEAD = "a89e0ba19a4acd323dba23242352b3d0e1015875"
REPLAY_RECORD = "docs/send-back-class-proof-2026-10.md"
REPLAY_FILES = [{"path": REPLAY_RECORD, "changeType": "ADDED"},
                {"path": "tests/test_split_ledger_retired.py", "changeType": "MODIFIED"}]
RED = "scripts unit tests (part 2)"
QA_BOT_REST = "agent-bureau-qa-bot[bot]"
APPROVE = f"🔎 QA Critic — VERDICT: APPROVE @{REPLAY_HEAD}\n\nApproved."
NOT_PROVEN = (f"⏸️ Merge gate: declined @{REPLAY_HEAD} — proof record not proven: "
              f"{REPLAY_RECORD} has 1 row(s) not met (DRE-6141)\n\nNot merged.")


def _check_runs(**override) -> list:
    """The eleven check runs GitHub returned for a89e0ba1, in its order."""
    runs = [
        ("scripts unit tests", "failure"), ("act registry consumers", "success"),
        ("TDD commit discipline", "success"), ("scripts unit tests (part 4)", "success"),
        ("scripts unit tests (part 1)", "success"), ("scripts unit tests (part 3)", "success"),
        (RED, "failure"), ("call / evaluate", "skipped"), ("call / resolve", "skipped"),
        ("QA critic review", "success"), ("call / review", "success"),
    ]
    return [{"name": name, "status": "completed",
             "conclusion": override.get(name, conclusion)} for name, conclusion in runs]


def _green_runs() -> list:
    return _check_runs(**{"scripts unit tests": "success", RED: "success"})


def _cancelled_runs() -> list:
    return _check_runs(**{"scripts unit tests": "cancelled", RED: "cancelled"})


#: The shared `gh` stub, taught one more read: the head's check runs, every
#: page, from GH_CHECKS. Every other call is answered exactly as before.
_API = 'elif args[0] == "api":'
assert _API in GH_STUB
RECORD_GH_STUB = GH_STUB.replace(_API, (
    'elif args[0] == "api" and any("/check-runs" in a for a in args):\n'
    '    runs = json.load(open(os.environ["GH_CHECKS"]))\n'
    '    print(json.dumps([{"total_count": len(runs), "check_runs": runs}]))\n'
    + _API), 1)


def _resolve(branch: str, event: str, actor: str, merge_state: str = "CLEAN", *,
             checks=None, thread=None, files=None, head="d9f2c1ab" + "0" * 32,
             number="911"):
    """Run the shipped Resolve script by path against a stubbed `gh`.

    `checks` are the head's check runs — every one green unless given — and
    `thread` the pull request's comments after the fix loop's own marker.

    Returns (step outputs, the fix-no-work.txt line or None, the files the
    script left in its temp dir, the PR comments it posted)."""
    outputs, line, left, posted, _ = _resolve_full(
        branch, event, actor, merge_state, checks=checks, thread=thread,
        files=files, head=head, number=number)
    return outputs, line, left, posted


def _resolve_full(branch, event, actor, merge_state="CLEAN", *, checks=None,
                  thread=None, files=None, head="d9f2c1ab" + "0" * 32, number="911"):
    """`_resolve`, plus the record file the run was told about, or None."""
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(RECORD_GH_STUB)
        stub.chmod(0o755)
        # The pipeline checkout the job takes before this step.
        (td / ".bureau-pipeline").symlink_to(ROOT)
        info = td / "pr-info.json"
        info.write_text(json.dumps({
            "state": "OPEN", "headRefName": branch,
            "headRefOid": head, "mergeStateStatus": merge_state,
            "files": files if files is not None else REPLAY_FILES,
        }))
        comments = td / "comments.json"
        comments.write_text(json.dumps([rest(WORKER, ATTEMPT.format(n=1)), *(thread or ())]))
        runs = td / "check-runs.json"
        runs.write_text(json.dumps(_green_runs() if checks is None else checks))
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
                GH_CHECKS=str(runs),
                GH_TOKEN="test", WRITER_TOKEN="test-writer",
                WORKER_LOGIN=WORKER, EVENT_NAME=event, TRIGGERING_ACTOR=actor,
                PR_NUMBER=number, REPO="dreadnought-foundry/portico",
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
        told = td / "proof-record-fix.txt"
        record = told.read_text().strip() if told.exists() else None
    return outputs, line, left, posted, record


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


# --------------------------------------------------------------------------
# DRE-6571 — a record red on a check is the fix agent's, for the check only
# --------------------------------------------------------------------------
def _graphql(login: str, body: str) -> dict:
    """A comment as `gh pr list --json comments` gives it: no `[bot]`."""
    return {"author": {"login": login.removesuffix("[bot]")}, "body": body,
            "createdAt": "2026-10-10T02:56:37Z"}


def _replay_pr(*extra) -> dict:
    return {"number": 896, "headRefName": REPLAY_BRANCH, "headRefOid": REPLAY_HEAD,
            "mergeStateStatus": "BLOCKED",
            "comments": [_graphql(QA_BOT_REST, APPROVE), *extra]}


class _Sweep:
    """`reconcile.fix_approved_but_red` against #896: GitHub's answers off
    fixtures, the card's thread a list both sweeps share."""

    def __init__(self, pr=None, runs=None):
        self.pr = pr or _replay_pr()
        self.runs = _check_runs() if runs is None else runs
        self.thread: list = []
        self.dispatched: list = []
        self.parked_reads: list = []

    def gh(self, *args):
        if args[:2] == ("pr", "list"):
            return json.dumps([self.pr])
        if args[0] == "api" and any("/check-runs" in a for a in args):
            return json.dumps([{"total_count": len(self.runs), "check_runs": self.runs}])
        if args[0] == "api" and any("/git/commits/" in a for a in args):
            # Opened 19:54 PT; the sweep reads it long after the 20-minute grace.
            return json.dumps({"committer": {"date": "2026-10-10T02:54:42Z"}})
        return ""

    def count(self, card, needle, **_):
        return sum(1 for c, body in self.thread if c == card and needle in body)

    def comment(self, card, body, *a, **kw):
        self.thread.append((card, body))

    def parked(self, card):
        self.parked_reads.append(card)
        return False

    def run(self) -> str:
        out = io.StringIO()
        with mock.patch.object(reconcile, "gh", side_effect=self.gh), \
                mock.patch.object(reconcile, "gh_dispatch",
                                  side_effect=lambda *a: self.dispatched.append(a)), \
                mock.patch.object(reconcile, "_actions_runs_busy", return_value=False), \
                mock.patch.object(reconcile, "card_parked_for_human", side_effect=self.parked), \
                mock.patch.object(reconcile, "fix_agent_absent_hold", return_value=False), \
                mock.patch.object(linear_ops, "count_comments", side_effect=self.count), \
                mock.patch.object(linear_ops, "cmd_comment", side_effect=self.comment), \
                mock.patch.object(linear_ops, "gql") as gql, \
                redirect_stdout(out):
            reconcile.fix_approved_but_red()
        gql.assert_not_called()
        return out.getvalue()


class TheReplayOf896Test(unittest.TestCase):
    """Acceptance 1: tonight's exact case, through two sweeps."""

    def test_one_sweep_one_fix_run_and_one_card_comment_naming_the_check(self):
        sweep = _Sweep()
        log = sweep.run()
        self.assertEqual(len(sweep.dispatched), 1, log)
        self.assertIn("pr_number=896", sweep.dispatched[0])
        self.assertEqual(len(sweep.thread), 1, log)
        card, body = sweep.thread[0]
        self.assertEqual(card, REPLAY_CARD)
        self.assertIn(RED, body)
        self.assertIn("fix agent", body)
        self.assertIn(REPLAY_HEAD[:8], body.split("\n", 1)[0])
        # The human park is still read for it: only the record refusal lifts.
        self.assertEqual(sweep.parked_reads, [REPLAY_CARD])
        self.assertNotIn("the proof run re-observes it, not the fix agent", log)

    def test_a_second_sweep_on_the_same_head_dispatches_and_writes_nothing(self):
        sweep = _Sweep()
        sweep.run()
        log = sweep.run()
        self.assertEqual(len(sweep.dispatched), 1, log)
        self.assertEqual(len(sweep.thread), 1, log)
        self.assertIn(REPLAY_HEAD[:8], log)

    def test_a_new_head_is_told_again(self):
        sweep = _Sweep()
        sweep.run()
        sweep.pr = dict(_replay_pr(), headRefOid="b" * 40)
        sweep.pr["comments"] = [_graphql(QA_BOT_REST, APPROVE.replace(REPLAY_HEAD, "b" * 40))]
        sweep.run()
        self.assertEqual(len(sweep.dispatched), 2)
        self.assertEqual(len(sweep.thread), 2)

    def test_the_comment_is_plain_english(self):
        sweep = _Sweep()
        sweep.run()
        body = sweep.thread[0][1]
        self.assertIn("#896", body)
        self.assertIn("scripts unit tests", body)
        self.assertNotIn(";", body.split("\n\n")[0])

    def test_an_unreadable_card_thread_dispatches_nothing(self):
        sweep = _Sweep()
        sweep.count = mock.Mock(side_effect=RuntimeError("Linear 503"))
        log = sweep.run()
        self.assertEqual(sweep.dispatched, [])
        self.assertEqual(sweep.thread, [])
        self.assertIn("Linear 503", log)


class WhatStaysTheProofRunsTest(unittest.TestCase):
    """Acceptance 3 and 4: the cases the fix agent is still kept off."""

    def test_a_not_proven_decline_at_the_head_is_never_the_fix_agents(self):
        for red in (True, False):
            with self.subTest(red=red):
                sweep = _Sweep(pr=_replay_pr(_graphql(QA_BOT_REST, NOT_PROVEN)),
                               runs=_check_runs() if red else _green_runs())
                log = sweep.run()
                self.assertEqual(sweep.dispatched, [])
                self.assertEqual(sweep.thread, [])
                if red:
                    self.assertIn("the proof run re-observes it, not the fix agent", log)

    def test_a_decline_on_an_earlier_head_does_not_keep_the_fix_agent_off(self):
        older = NOT_PROVEN.replace(REPLAY_HEAD, "c" * 40)
        sweep = _Sweep(pr=_replay_pr(_graphql(QA_BOT_REST, older)))
        sweep.run()
        self.assertEqual(len(sweep.dispatched), 1)

    def test_a_decline_quoted_by_someone_else_is_not_the_gates(self):
        sweep = _Sweep(pr=_replay_pr(_graphql("mallory", NOT_PROVEN)))
        sweep.run()
        self.assertEqual(len(sweep.dispatched), 1)

    def test_a_record_whose_only_non_green_check_is_cancelled(self):
        sweep = _Sweep(runs=_cancelled_runs())
        log = sweep.run()
        self.assertEqual(sweep.dispatched, [])
        self.assertEqual(sweep.thread, [])
        self.assertIn("the proof run re-observes it, not the fix agent", log)

    def test_a_failed_review_check_is_not_a_red_check(self):
        sweep = _Sweep(runs=_green_runs() + [
            {"name": "qa-review / review", "status": "completed", "conclusion": "failure"}])
        sweep.run()
        self.assertEqual(sweep.dispatched, [])

    def test_the_predicate_answers_the_cases_by_name(self):
        import proof_dispatch
        pr = {"headRefName": REPLAY_BRANCH, "headRefOid": REPLAY_HEAD, "comments": []}
        self.assertEqual(proof_dispatch.record_red_checks(pr, _check_runs()),
                         ["scripts unit tests", RED])
        self.assertEqual(proof_dispatch.record_red_checks(pr, _cancelled_runs()), [])
        self.assertEqual(proof_dispatch.record_red_checks(
            dict(pr, headRefName=ORDINARY), _check_runs()), [])
        self.assertEqual(proof_dispatch.record_red_checks(
            dict(pr, comments=[_graphql(QA_BOT_REST, NOT_PROVEN)]), _check_runs()), [])
        # The REST thread the Resolve step reads, `[bot]` and all, is the same.
        self.assertEqual(proof_dispatch.record_red_checks(
            dict(pr, comments=[rest(QA_BOT_REST, NOT_PROVEN)]), _check_runs()), [])
        for conclusion in ("timed_out", "action_required", "startup_failure"):
            self.assertEqual(proof_dispatch.record_red_checks(
                pr, _check_runs(**{"scripts unit tests": "success", RED: conclusion})), [RED])

    def test_every_other_caller_keeps_todays_refusal(self):
        # Only the approved-but-red sweep passes the red checks in.
        tree = ast.parse((ROOT / "scripts" / "reconcile.py").read_text())
        passing = set()
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "fix_dispatch_blocked" and node.keywords):
                    passing.add(fn.name)
        self.assertEqual(passing, {"fix_approved_but_red"})


class TheResolveStepAdmitsTheSweepsRunTest(unittest.TestCase):
    """Acceptance 2: the sweep's dispatch at #896 runs; every other start on
    a record still ends at the refusal."""

    def _replay(self, event="workflow_dispatch", actor="github-actions", **kw):
        kw.setdefault("checks", _check_runs())
        kw.setdefault("thread", [rest(QA_BOT_REST, APPROVE)])
        return _resolve_full(REPLAY_BRANCH, event, actor, head=REPLAY_HEAD,
                             number="896", **kw)

    def test_the_sweeps_dispatch_at_896_runs(self):
        outputs, line, left, posted, record = self._replay()
        self.assertEqual(outputs.get("go"), "true")
        self.assertEqual(outputs.get("mode"), "fix")
        self.assertEqual(outputs.get("card"), REPLAY_CARD)
        self.assertNotIn("no_work", outputs)
        self.assertFalse(line and line.startswith("fix-refused:"))
        self.assertEqual(posted, "")
        # The run is told the record's path and that it is not its file.
        self.assertEqual(record, REPLAY_RECORD)
        escalation = outputs.get("escalation") or ""
        self.assertIn(REPLAY_RECORD, escalation)
        self.assertIn(RED, escalation)
        self.assertIn("not your file", escalation)

    def test_a_record_with_no_failed_check_is_refused_on_both_events(self):
        for event, actor in (("issue_comment", "agent-bureau-qa-bot[bot]"),
                             ("workflow_dispatch", "github-actions")):
            for checks in (_green_runs(), _cancelled_runs()):
                with self.subTest(event=event, cancelled=checks == _cancelled_runs()):
                    outputs, line, _, posted, record = self._replay(event, actor, checks=checks)
                    self.assertEqual(outputs.get("go"), "false")
                    self.assertEqual(outputs.get("no_work"), "true")
                    self.assertTrue(line.startswith("fix-refused: #896 is a proof record"), line)
                    self.assertIsNone(record)
                    self.assertEqual(posted, "")

    def test_a_comment_triggered_start_on_a_red_record_is_refused(self):
        outputs, line, _, _, _ = self._replay("issue_comment", "agent-bureau-qa-bot[bot]")
        self.assertEqual(outputs.get("go"), "false")
        self.assertTrue(line.startswith("fix-refused:"), line)

    def test_a_conflicted_red_record_is_refused(self):
        outputs, line, _, _, _ = self._replay(merge_state="DIRTY")
        self.assertEqual(outputs.get("go"), "false")
        self.assertNotIn("mode", outputs)
        self.assertTrue(line.startswith("fix-refused:"), line)

    def test_a_not_proven_decline_at_the_head_is_refused(self):
        outputs, line, _, _, _ = self._replay(
            thread=[rest(QA_BOT_REST, APPROVE), rest(QA_BOT_REST, NOT_PROVEN)])
        self.assertEqual(outputs.get("go"), "false")
        self.assertTrue(line.startswith("fix-refused:"), line)

    def test_an_ordinary_branch_never_reads_the_record_rule(self):
        outputs, _, left, _, record = _resolve_full(ORDINARY, "workflow_dispatch",
                                                    "github-actions", checks=_check_runs())
        self.assertEqual(outputs.get("go"), "true")
        self.assertNotIn(REPLAY_RECORD, outputs.get("escalation") or "")
        self.assertNotIn("record-checks.json", left)
        self.assertIsNone(record)


class OnePredicateTest(unittest.TestCase):
    """Acceptance 5: the sweep and the Resolve step read one rule."""

    RULE_WORDS = ("timed_out", "startup_failure", "action_required",
                  "FAILED_CONCLUSIONS", "not proven", "NOT_PROVEN",
                  "GATE_HOLD_NOTE_MARKER", "Merge gate: declined", "proof_record_branch")

    def test_the_sweep_calls_the_predicate_and_holds_no_copy(self):
        source = textwrap.dedent(inspect.getsource(reconcile.fix_approved_but_red))
        self.assertIn("proof_dispatch.record_red_checks(", source)
        for word in self.RULE_WORDS:
            self.assertNotIn(word, source, word)

    def test_the_resolve_step_calls_the_predicate_and_holds_no_copy(self):
        text = SCRIPT.read_text()
        self.assertIn("proof_dispatch.record_red_checks_read(", text)
        code = "\n".join(line for line in text.splitlines()
                         if not line.lstrip().startswith("#"))
        for word in self.RULE_WORDS[:-1]:
            self.assertNotIn(word, code, word)

    def test_the_resolve_reader_is_the_predicate(self):
        import proof_dispatch
        source = inspect.getsource(proof_dispatch.record_red_checks_read)
        self.assertIn("record_red_checks(", source)
        with mock.patch.object(proof_dispatch, "record_red_checks", return_value=["x"]) as rule, \
                tempfile.TemporaryDirectory() as raw:
            checks, thread = Path(raw) / "c.json", Path(raw) / "t.json"
            checks.write_text(json.dumps([{"check_runs": _green_runs()}]))
            thread.write_text(json.dumps([[rest(QA_BOT_REST, APPROVE)]]))
            got = proof_dispatch.record_red_checks_read(REPLAY_BRANCH, REPLAY_HEAD,
                                                        str(checks), str(thread))
        self.assertEqual(got, ["x"])
        rule.assert_called_once()

    def test_the_sweep_goes_where_the_predicate_says(self):
        import proof_dispatch
        with mock.patch.object(proof_dispatch, "record_red_checks", return_value=[]):
            sweep = _Sweep()
            sweep.run()
        self.assertEqual(sweep.dispatched, [])


def _report(touch: str, *, told: str | None = REPLAY_RECORD) -> list:
    """Run the real report_fix_result.sh after a fix run on a record branch
    whose one commit changes `touch`, delivered to the pull request. `told`
    is the record the Resolve step named for the run, None for a run on any
    other branch. Returns the bodies it posted on the pull request."""
    from test_act_emission_scenario import _checkout, _report_env
    from test_fix_committed_not_pushed import GH_STUB as REPORT_GH, LINEAR_STUB, _git

    import fix_handoff

    with tempfile.TemporaryDirectory() as td:
        _git(td, "init", "-q")
        (Path(td) / "docs").mkdir()
        (Path(td) / "tests").mkdir()
        (Path(td) / REPLAY_RECORD).write_text("# PROOF record\n\n| Criterion | Result |\n")
        (Path(td) / "tests" / "test_split_ledger_retired.py").write_text("HISTORICAL = ()\n")
        _git(td, "add", "docs", "tests")
        _git(td, "commit", "-q", "-m", "the record the proof run opened")
        pre = _git(td, "rev-parse", "HEAD")
        with open(Path(td) / touch, "a") as fh:
            fh.write("# the fix\n")
        _git(td, "commit", "-q", "-am", "the fix")
        local = _git(td, "rev-parse", "HEAD")

        base = _checkout(td)
        linear_log = os.path.join(td, "linear.jsonl")
        Path(base, "scripts", "linear_ops.py").write_text(LINEAR_STUB.format(log=linear_log))
        Path(base, "critic-verdict.md").write_text(f"🔎 QA Critic — VERDICT: APPROVE @{pre}\n")
        binary = Path(td) / "bin"
        binary.mkdir()
        (binary / "gh").write_text(REPORT_GH)
        (binary / "gh").chmod(0o755)
        thread = Path(td) / "thread.json"
        thread.write_text(json.dumps([[]]))
        env = _report_env(td, "fix")
        fix_handoff.open_handoff(td, env["REPO"], env["PR"], pre,
                                 legacy_dir=os.path.join(td, "legacy"))
        if told is not None:
            Path(td, "proof-record-fix.txt").write_text(told + "\n")
        gh_log = os.path.join(td, "gh.jsonl")
        env = dict(os.environ, PATH=f"{binary}{os.pathsep}{os.environ['PATH']}",
                   CLASSIFICATION="", DISPATCH_TOKEN="the-workflow-token",
                   GH_LOG=gh_log, GH_HEAD=local, GH_THREAD=str(thread),
                   GH_STATE="OPEN", **env)
        env.update(PRE_SHA=pre, RUN_ID="1234", RESCUE_LOCAL_WORK="true",
                   RESCUE_PUSHED="false", RESCUE_PATCH="", RESCUE_ARTIFACT="x.patch",
                   RESCUE_PUSH_STATUS="", RESCUE_ERROR="", RESCUE_TARGET_BRANCH=REPLAY_BRANCH)
        proc = subprocess.run(["bash", str(ROOT / "scripts" / "report_fix_result.sh")],
                              cwd=td, env=env, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise AssertionError(f"report_fix_result.sh exited {proc.returncode}: "
                                 f"{proc.stdout}{proc.stderr}")
        rows = [json.loads(line) for line in Path(gh_log).read_text().splitlines()] \
            if os.path.exists(gh_log) else []
    return [r["body"] for r in rows if r["kind"] == "comment"]


RECORD_LINE = "proof-record-changed:"


class TheReportNamesAChangeToTheRecordTest(unittest.TestCase):
    """Acceptance 6: one named line when the fix commit changes the record,
    none when it leaves the record alone."""

    def test_a_fix_commit_that_changes_the_record_is_named_once(self):
        posted = _report(REPLAY_RECORD)
        self.assertEqual(len(posted), 1, posted)
        self.assertTrue(posted[0].startswith("🔧 Fix attempt 2 pushed"), posted[0])
        named = [line for line in posted[0].splitlines() if line.startswith(RECORD_LINE)]
        self.assertEqual(len(named), 1, posted[0])
        self.assertIn(REPLAY_RECORD, named[0])
        self.assertIn("critic", named[0])

    def test_a_fix_commit_that_leaves_the_record_alone_says_nothing_of_it(self):
        posted = _report("tests/test_split_ledger_retired.py")
        self.assertEqual(len(posted), 1, posted)
        self.assertTrue(posted[0].startswith("🔧 Fix attempt 2 pushed"), posted[0])
        self.assertNotIn(RECORD_LINE, posted[0])

    def test_a_run_no_record_was_named_for_says_nothing_of_one(self):
        posted = _report(REPLAY_RECORD, told=None)
        self.assertEqual(len(posted), 1, posted)
        self.assertNotIn(RECORD_LINE, posted[0])


if __name__ == "__main__":
    unittest.main()
