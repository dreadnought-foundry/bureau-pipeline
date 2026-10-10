"""RED-first tests for DRE-6583: one approved pull request the fix agent
cannot fix no longer starves every other approved-but-red pull request.

Observed in bureau-pipeline itself on 2026-10-10 (PT). `fix_approved_but_red`
dispatches for the FIRST eligible pull request and returns, and `gh pr list`
lists newest first. #918 (DRE-3072, head d01bc04c) is approved and red on
`act registry consumers`, a check whose fix lives in agent-bureau, so the fix
agent cannot make it green — it ran twice (00:51:38, 01:07:11) and both runs
ended on the worker bot's quiet `🔕 … pushed no new commit (head `d01bc04c`)`
line. #912 (DRE-6571, head 8d34bc1f) is older, approved at its head and red on
two of its own tests, and no fix run was ever dispatched for it: at 01:07:32
the sweep printed `approved-but-red: PR #918 … dispatching fix agent` and
nothing for #912. An operator ran #912's fix by hand at 01:12:37.

The rule: a pull request whose last fix run at the CURRENT head pushed no
commit is passed over — named in the log, nothing posted — and the pass goes
on to the next eligible pull request in listing order. ONE such line at the
head is enough; the sweep does not wait for the fix run's own two-line
convergence halt (`fix_convergence.HALT_AFTER`), which this card leaves alone.
"""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import fix_convergence  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402

# The two heads as they stood at 01:07 PT, read off GitHub.
SHA_918 = "d01bc04c7d9372bb70b272463b0af779f2640e52"
SHA_912 = "8d34bc1f3215ee79036e3de545a1f0dcf520857c"
MOVED = "e" * 40

# The fix run's quiet line (scripts/fix_exit.py), verbatim as posted on #918
# at 00:52:56 and 01:09:05 PT.
QUIET_918 = (
    "🔕 Fix attempt 1 pushed no new commit (head `d01bc04c`) and none is "
    "needed: the fix agent found nothing to fix, and no blocking finding is "
    "open on this commit. No person is needed, and the card stays where it "
    "is.\n\n🧷 answers: dreadnought-foundry/bureau-pipeline#918 · card "
    "DRE-3072 · verdict d01bc04c"
)

#: The failed check runs at each head, by name.
FAILED = {
    SHA_918: ["act registry consumers"],
    SHA_912: [
        "tests/test_fix_convergence_halt.py::HaltSourcePinTest::"
        "test_the_thread_read_is_paginated_in_full",
        "tests/test_redispatch_committed_not_pushed.py::"
        "CommittedNotPushedSweepTest::test_the_wait_is_read_off_fix_dead_run",
    ],
}


def _critic(sha: str) -> dict:
    return {
        "author": {"login": "agent-bureau-qa-bot"},
        "body": f"🔎 QA Critic — VERDICT: APPROVE @{sha} content:abc",
    }


def _worker(body: str) -> dict:
    return {"author": {"login": "agent-bureau-bot"}, "body": body}


def _pr_918(head: str = SHA_918, quiet: int = 2) -> dict:
    return {
        "number": 918,
        "headRefName": "agent/DRE-3072-canceled-check-rerun",
        "headRefOid": head,
        "mergeStateStatus": "BLOCKED",
        "comments": [_critic(SHA_918)] + [_worker(QUIET_918)] * quiet,
    }


def _pr_912() -> dict:
    return {
        "number": 912,
        "headRefName": "agent/DRE-6571-proof-record-red-check-owner",
        "headRefOid": SHA_912,
        "mergeStateStatus": "BLOCKED",
        "comments": [
            _worker("🔧 Fix attempt 1 pushed — CI and critic review re-running."),
            _worker(
                "🔀 Conflict resolution round 1 pushed — CI and critic review "
                "re-running."
            ),
            _critic(SHA_912),
        ],
    }


def _check_runs(sha: str) -> str:
    names = FAILED.get(sha, ["act registry consumers"])
    runs = [{"name": n, "status": "completed", "conclusion": "failure"} for n in names]
    runs.append({"name": "lint", "status": "completed", "conclusion": "success"})
    return json.dumps([{"total_count": len(runs), "check_runs": runs}])


class _Pass:
    """One `fix_approved_but_red` pass over a `gh pr list` payload, with
    every gh call, dispatch, pull request note and Linear write recorded."""

    def __init__(self, prs: list[dict]):
        self.gh_calls: list[tuple] = []
        self.dispatches: list[tuple] = []
        self.notes: list[tuple] = []
        self.linear: list[tuple] = []
        self.acts: list[tuple] = []
        out = io.StringIO()

        def gh(*args):
            self.gh_calls.append(args)
            if args[:2] == ("run", "list"):
                return "[]"  # no fix run in flight
            if args[:2] == ("pr", "list"):
                return json.dumps(prs)
            if args[0] == "api" and any("/check-runs" in a for a in args[1:]):
                sha = next(a for a in args if "/check-runs" in a).split("/")[-2]
                return _check_runs(sha)
            if args[0] == "api" and "/git/commits/" in args[1]:
                # Both heads comfortably past the 20-minute age.
                return json.dumps({"committer": {"date": "2026-10-10T06:00:00Z"}})
            return ""

        def record(store):
            return lambda *a, **k: store.append(a)

        with mock.patch.object(reconcile, "gh", side_effect=gh), \
                mock.patch.object(reconcile, "gh_dispatch",
                                  side_effect=record(self.dispatches)), \
                mock.patch.object(reconcile, "_post_pr_note",
                                  side_effect=record(self.notes)), \
                mock.patch.object(reconcile, "card_parked_for_human",
                                  return_value=False), \
                mock.patch.object(linear_ops, "gql",
                                  side_effect=record(self.linear)), \
                mock.patch.object(linear_ops, "cmd_comment",
                                  side_effect=record(self.linear)), \
                mock.patch.object(pipeline_act, "receipt",
                                  side_effect=lambda *a, **k: self.acts.append(a) or ""), \
                redirect_stdout(out):
            reconcile.fix_approved_but_red()
        self.log = out.getvalue()

    def dispatched_prs(self) -> list[str]:
        return [a[-1] for a in self.dispatches]


class ReplayTest(unittest.TestCase):
    """The 01:07 PT listing: #918 newer and unfixable, #912 older and red."""

    def test_the_pass_dispatches_912_and_not_918(self):
        run = _Pass([_pr_918(), _pr_912()])
        self.assertEqual(run.dispatched_prs(), ["pr_number=912"])

    def test_one_pass_dispatches_exactly_one_fix_run(self):
        run = _Pass([_pr_918(), _pr_912()])
        self.assertEqual(len(run.dispatches), 1)

    def test_a_moved_head_is_eligible_again(self):
        # The same two `d01bc04c` lines, but the branch has a new commit since:
        # those lines answer a head that no longer stands.
        run = _Pass([_pr_918(head=MOVED), _pr_912()])
        self.assertEqual(run.dispatched_prs(), ["pr_number=918"])

    def test_one_no_commit_line_is_enough(self):
        # The sweep's rule is stricter than the fix run's halt: ONE quiet line
        # at the head and no `🧯 fix-convergence-halt` receipt still passes over.
        pr = _pr_918(quiet=1)
        self.assertFalse(any(
            "fix-convergence-halt" in (c.get("body") or "") for c in pr["comments"]
        ))
        run = _Pass([pr, _pr_912()])
        self.assertEqual(run.dispatched_prs(), ["pr_number=912"])

    def test_the_fix_run_halt_is_unchanged(self):
        self.assertEqual(fix_convergence.HALT_AFTER, 2)

    def test_the_escalation_line_counts_too(self):
        # scripts/fix_budget.py / report_fix_result.sh write this form.
        pr = _pr_918(quiet=0)
        pr["comments"].append(_worker(
            "🛑 Fix attempt 2 pushed no new commit (branch still at `d01bc04c`) "
            "— a person is needed."
        ))
        run = _Pass([pr, _pr_912()])
        self.assertEqual(run.dispatched_prs(), ["pr_number=912"])

    def test_a_line_by_anyone_but_the_worker_bot_does_not_count(self):
        pr = _pr_918(quiet=0)
        pr["comments"].append({"author": {"login": "someone"}, "body": QUIET_918})
        run = _Pass([pr, _pr_912()])
        self.assertEqual(run.dispatched_prs(), ["pr_number=918"])

    def test_a_worker_line_without_the_phrase_does_not_count(self):
        pr = _pr_918(quiet=0)
        pr["comments"].append(_worker(
            "🔧 Fix attempt 1 pushed (head `d01bc04c`) — CI re-running."
        ))
        run = _Pass([pr, _pr_912()])
        self.assertEqual(run.dispatched_prs(), ["pr_number=918"])

    def test_only_skipped_prs_leave_nothing_dispatched(self):
        run = _Pass([_pr_918()])
        self.assertEqual(run.dispatches, [])


class SkipIsLoggedNotPostedTest(unittest.TestCase):
    """The skip is a print, never a post: no act is declared or emitted."""

    def setUp(self):
        self.run = _Pass([_pr_918(), _pr_912()])
        self.skips = [
            line for line in self.run.log.splitlines()
            if line.startswith("approved-but-red:") and "#918" in line
        ]

    def test_one_line_names_the_skipped_pull_request(self):
        self.assertEqual(len(self.skips), 1, self.run.log)
        line = self.skips[0]
        self.assertIn("PR #918", line)
        self.assertIn("d01bc04c", line)
        self.assertNotIn(SHA_918, line)  # the head's first eight, not all forty
        self.assertIn("act registry consumers", line)
        self.assertIn("pushed no commit", line)

    def test_nothing_is_posted_on_the_pull_request(self):
        self.assertEqual(self.run.notes, [])
        self.assertFalse([
            a for a in self.run.gh_calls + self.run.dispatches
            if a[:2] == ("pr", "comment")
        ])

    def test_nothing_is_written_to_linear(self):
        self.assertEqual(self.run.linear, [])

    def test_no_act_receipt_is_emitted(self):
        self.assertEqual(self.run.acts, [])

    def test_the_dispatch_line_still_names_912(self):
        self.assertIn(
            "approved-but-red: PR #912 has APPROVE + 2 failed check(s) — "
            "dispatching fix agent",
            self.run.log,
        )


if __name__ == "__main__":
    unittest.main()
