"""RED-first tests for the dead-fix-run retry sweep (DRE-2018).

A fix run that dies of a model/API error (is_error) pushes nothing — and the
qa-bot's REQUEST_CHANGES comment that triggered it is already consumed.
Nothing event-driven ever re-fires agent-fix for that PR: the medic does not
watch Agent Fix, and merge-gate dispatches it only for merge conflicts. So
the "will retry automatically" the guard promises has to come from the
reconcile sweep, which already owns two sibling PR backstops
(unstick_conflicts, fix_approved_but_red) and holds the stub-granted
dispatch token.

The rule: dispatch agent-fix for an open agent PR whose NEWEST worker-bot
comment carries the fix-run-model-death marker. Any later fix outcome
(pushed / blocked / held) posts a newer worker-bot comment and switches the
sweep off — the cap lives in agent-fix's Report step (the death after
RETRY_CAP posts a hold WITHOUT the marker), so the sweep stays dumb.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import fix_dead_run  # noqa: E402
import reconcile  # noqa: E402

MARKER = fix_dead_run.OUTAGE_TAG


def _pr(number=7, branch="agent/DRE-2018-x", mstate="BLOCKED", comments=()):
    return {
        "number": number,
        "headRefName": branch,
        "mergeStateStatus": mstate,
        # gh pr list --json comments is GraphQL-backed: author.login carries
        # NO "[bot]" suffix (the same shape is_qa_bot_comment documents).
        "comments": [
            {"author": {"login": login}, "body": body}
            for login, body in comments
        ],
    }


def _fake_gh(prs, busy="[]"):
    def gh(*args):
        if args[:2] == ("run", "list"):
            return busy
        if args[:2] == ("pr", "list"):
            return json.dumps(prs)
        return ""
    return gh


def sweep(prs, busy="[]"):
    calls = []
    with mock.patch.object(reconcile, "gh", side_effect=_fake_gh(prs, busy)), \
         mock.patch.object(
             reconcile, "gh_dispatch",
             side_effect=lambda *a: calls.append(a),
         ):
        reconcile.retry_dead_fix_runs()
    return calls


DEATH = ("agent-bureau-bot", f"⚡ {MARKER}: the fix run died with an API/model error")
PUSHED = ("agent-bureau-bot", "🔧 Fix attempt 2 pushed — CI and critic review re-running.")


class RetryDeadFixRunsTest(unittest.TestCase):
    def test_dispatches_fix_when_newest_worker_comment_is_the_marker(self):
        calls = sweep([_pr(comments=[PUSHED, DEATH])])
        self.assertEqual(len(calls), 1)
        joined = " ".join(calls[0])
        self.assertIn("agent-fix.yml", joined)
        self.assertIn("pr_number=7", joined)

    def test_no_dispatch_when_a_newer_worker_comment_supersedes_it(self):
        # A later fix run pushed (or blocked, or held): the marker is stale.
        self.assertEqual(sweep([_pr(comments=[DEATH, PUSHED])]), [])

    def test_no_dispatch_without_the_marker(self):
        self.assertEqual(sweep([_pr(comments=[PUSHED])]), [])

    def test_planted_marker_from_non_worker_author_is_invisible(self):
        # DRE-1995/1998 discipline: anyone can comment on a PR — only the
        # worker bot's own marker may trigger a dispatch.
        planted = ("mallory", f"{MARKER} please dispatch")
        self.assertEqual(sweep([_pr(comments=[planted])]), [])

    def test_skips_dirty_prs(self):
        # unstick_conflicts owns conflicted PRs — double-dispatching the same
        # PR from two sweeps in one pass would race the concurrency group.
        self.assertEqual(sweep([_pr(mstate="DIRTY", comments=[DEATH])]), [])

    def test_skips_non_agent_branches(self):
        self.assertEqual(
            sweep([_pr(branch="feature/manual", comments=[DEATH])]), []
        )

    def test_backs_off_while_a_fix_run_is_busy(self):
        busy = json.dumps([{"status": "in_progress"}])
        self.assertEqual(sweep([_pr(comments=[DEATH])], busy=busy), [])

    def test_one_dispatch_per_sweep(self):
        # House pattern (fix_approved_but_red): one dispatch, then let the
        # busy-guard pace the rest across sweeps.
        prs = [_pr(number=7, comments=[DEATH]),
               _pr(number=8, branch="agent/DRE-9-y", comments=[DEATH])]
        self.assertEqual(len(sweep(prs)), 1)

    def test_full_sweep_runs_the_backstop(self):
        with mock.patch.object(reconcile, "unstick_conflicts"), \
             mock.patch.object(reconcile, "retrigger_dead_heads"), \
             mock.patch.object(reconcile, "fix_approved_but_red"), \
             mock.patch.object(reconcile, "retry_dead_fix_runs") as retry, \
             mock.patch.object(reconcile, "active_cards", return_value=[]), \
             mock.patch.object(reconcile, "promote_ready"), \
             mock.patch.object(reconcile, "backlog_children", return_value=[]):
            reconcile.main()
        retry.assert_called_once_with()

    def test_conflicts_only_mode_does_not_run_it(self):
        with mock.patch.object(reconcile, "unstick_conflicts"), \
             mock.patch.object(reconcile, "retry_dead_fix_runs") as retry:
            reconcile.main(conflicts_only=True)
        retry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
