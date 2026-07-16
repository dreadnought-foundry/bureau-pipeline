"""RED-first tests for merge-triggered UNKNOWN-mergeable polling (DRE-2121).

Origin (2026-07-16, bp #110): linear-sync fires the conflicts-only sweep the
moment a merge lands, but GitHub computes a sibling PR's mergeable state
LAZILY and asynchronously — bp#109 merged at 00:56:57, the sweep listed PRs
at 00:57:10, and #110's conflict wasn't computed yet (mergeStateStatus
UNKNOWN). The sweep honestly found no DIRTY PRs and exited; #110 sat
"Checks failing — fix agent not engaged" until an operator hand-nudged
reconcile 17 minutes later. The event-driven path exists precisely to beat
the drifting cron, and it defeated itself by racing GitHub's recompute.

The rule: never conclude "nothing dirty" while any agent PR reads UNKNOWN.
Re-read those PRs (bounded: CONFLICT_POLL_TRIES × CONFLICT_POLL_SECONDS;
GitHub recomputes mergeable on read) until each resolves, then act — at most
one dispatch per PR per invocation. Still UNKNOWN at the cap: log loudly
(fail-loudly rail), no dispatch, no crash — the cron backstop owns it.
"""

import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import reconcile  # noqa: E402


def _pr(number, status, branch=None):
    return {
        "number": number,
        "headRefName": branch or f"agent/DRE-2121-pr-{number}",
        "mergeStateStatus": status,
    }


def _fake_gh(listed, views):
    """Fake-API harness: `pr list` returns `listed` once; `pr view <n>`
    walks `views[n]` (a list of successive mergeStateStatus answers, last
    value repeating once exhausted — GitHub recomputes on read)."""
    reads = {n: 0 for n in views}

    def gh(*args):
        if args[:2] == ("run", "list"):
            return "[]"
        if args[:2] == ("pr", "list"):
            return json.dumps(listed)
        if args[:2] == ("pr", "view"):
            n = int(args[2])
            seq = views[n]
            status = seq[min(reads[n], len(seq) - 1)]
            reads[n] += 1
            return json.dumps(_pr(n, status))
        return ""

    gh.reads = reads
    return gh


def _run_sweep(listed, views=None):
    """Run unstick_conflicts against the fake API; return (dispatches,
    sleeps, stderr text)."""
    calls, sleeps = [], []
    err = io.StringIO()
    with mock.patch.object(
        reconcile, "gh", side_effect=_fake_gh(listed, views or {})
    ), mock.patch.object(
        reconcile, "gh_dispatch", side_effect=lambda *a: calls.append(a)
    ), mock.patch.object(
        reconcile, "card_parked_for_human", return_value=False
    ), mock.patch(
        "time.sleep", side_effect=lambda s: sleeps.append(s)
    ), contextlib.redirect_stderr(err):
        reconcile.unstick_conflicts()
    return calls, sleeps, err.getvalue()


class UnknownResolvesTest(unittest.TestCase):
    """The bp #110 race: sibling merge → first list reads UNKNOWN → a
    re-read resolves it → the fix agent is dispatched THIS invocation."""

    def test_unknown_then_dirty_dispatches_fix_exactly_once(self):
        calls, sleeps, _ = _run_sweep(
            [_pr(110, "UNKNOWN")], views={110: ["DIRTY"]}
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("pr_number=110", " ".join(calls[0]))
        self.assertEqual(sleeps, [reconcile.CONFLICT_POLL_SECONDS])

    def test_unknown_across_two_polls_still_dispatches_once(self):
        # No dispatch storm: the PR resolves on the second re-read and is
        # dispatched exactly once, never once-per-poll.
        calls, sleeps, _ = _run_sweep(
            [_pr(110, "UNKNOWN")], views={110: ["UNKNOWN", "DIRTY"]}
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("pr_number=110", " ".join(calls[0]))
        self.assertEqual(len(sleeps), 2)

    def test_unknown_resolving_mergeable_dispatches_nothing(self):
        # The sibling merge didn't conflict this PR after all — the poll
        # must resolve the uncertainty and then stay quiet.
        calls, sleeps, _ = _run_sweep(
            [_pr(110, "UNKNOWN")], views={110: ["CLEAN"]}
        )
        self.assertEqual(calls, [])
        self.assertEqual(len(sleeps), 1)

    def test_dirty_and_unknown_each_dispatched_exactly_once(self):
        # A PR already DIRTY on the first list dispatches immediately; the
        # UNKNOWN sibling dispatches after its poll — one dispatch each.
        calls, _, _ = _run_sweep(
            [_pr(7, "DIRTY"), _pr(110, "UNKNOWN")], views={110: ["DIRTY"]}
        )
        joined = [" ".join(c) for c in calls]
        self.assertEqual(len(calls), 2)
        self.assertEqual(len([c for c in joined if "pr_number=7" in c]), 1)
        self.assertEqual(len([c for c in joined if "pr_number=110" in c]), 1)


class UnknownForeverTest(unittest.TestCase):
    """The bound: still UNKNOWN at the cap → loud log, no dispatch, no
    crash — linear-sync stays fast and the cron backstop owns the PR."""

    def test_unknown_forever_loud_log_no_dispatch_no_crash(self):
        calls, sleeps, err = _run_sweep(
            [_pr(110, "UNKNOWN")], views={110: ["UNKNOWN"]}
        )
        self.assertEqual(calls, [])
        self.assertEqual(
            sleeps,
            [reconcile.CONFLICT_POLL_SECONDS] * reconcile.CONFLICT_POLL_TRIES,
        )
        self.assertIn("#110", err)
        self.assertIn("UNKNOWN", err)


class NoUnknownFastPathTest(unittest.TestCase):
    """Non-vacuous twins: nothing UNKNOWN (or only non-agent UNKNOWNs)
    must mean zero polling — the sweep stays as fast as before."""

    def test_no_unknown_candidates_never_sleeps(self):
        calls, sleeps, _ = _run_sweep(
            [_pr(7, "DIRTY"), _pr(8, "CLEAN"), _pr(9, "BEHIND")]
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("pr_number=7", " ".join(calls[0]))
        self.assertEqual(sleeps, [])

    def test_non_agent_unknown_pr_is_not_polled(self):
        # Only agent/* PRs are fix-agent candidates; a dependabot PR stuck
        # UNKNOWN must not slow the sweep or draw a dispatch.
        calls, sleeps, _ = _run_sweep(
            [_pr(42, "UNKNOWN", branch="dependabot/pip/foo-2.0")]
        )
        self.assertEqual(calls, [])
        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
