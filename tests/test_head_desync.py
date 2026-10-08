"""RED-first tests: the sweep resyncs a pull request whose head GitHub never
moved to its branch's newest commit — once per (pull request, branch commit)
(DRE-6217).

THE INCIDENT (bureau-pipeline #780, 2026-10-07). At 9:58 AM PT
`agent-bureau-bot-2` pushed `d1754c31` to `agent/DRE-6124-nightly-watch-head-
branch` while GitHub Actions was failing to start runs fleet-wide. GitHub
recorded the push — `git/ref/heads/<branch>` read `d1754c31` — and never
synchronized the pull request: `pulls/780` kept `head.sha = 79764952`. Every
check, the critic, the fix loop and the merge gate kept reading a commit the
branch had already left. The fix agent was dispatched against the stale head
and answered "a new commit on the branch is needed" while the branch already
held one. `retrigger_dead_heads` never fired: it reads the pull request's own
`headRefOid`, and that commit HAD check runs. `update-branch` (GraphQL and
REST) refused with `head sha didn't match the current head ref`. Six hours
later the operator closed and reopened #780; GitHub moved the head within a
minute and CI and the critic started.

FIX UNDER TEST — `reconcile.resync_desynced_heads`, beside
`retrigger_dead_heads`:
  * per open card-branch pull request it reads `git/ref/heads/<headRefName>`
    and compares that commit with the pull request's `headRefOid`;
  * differ, and the branch commit is over 15 minutes old: close and reopen
    the pull request ONCE per (pull request, branch commit), print one
    `head-desync` line naming both commits, and post one notice carrying the
    same tag and both commits — the notice is the idempotency key;
  * an unreadable ref or pull request is UNKNOWN: no action, never "in sync"
    (DRE-2034);
  * still desynced after the one resync: no repeat, one notice saying so.

Run: cd bureau-pipeline && python3 -m pytest tests/test_head_desync.py -v
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import head_desync  # noqa: E402
import reconcile  # noqa: E402

#: #780's two commits — the pull request's stale head and its branch's real
#: tip, a child of it.
STALE = "79764952" + "a" * 32
TIP = "d1754c31" + "b" * 32
BRANCH = "agent/DRE-6124-nightly-watch-head-branch"
WORKER = {"login": "agent-bureau-bot"}


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _pr(number=780, branch=BRANCH, head=STALE, comments=()):
    return {
        "number": number,
        "headRefName": branch,
        "headRefOid": head,
        "comments": list(comments),
    }


class _GitHub:
    """One `resync_desynced_heads` run against a fake GitHub — #780's shape
    by default: the listing says 79764952, the branch ref says d1754c31, a
    child of it, committed 30 minutes ago.

    `ref` maps a branch name to its tip sha, or to an exception the read
    raises; `views` maps a PR number to what a fresh `pr view` answers (the
    listing's own head when absent); `compare` is the compare status.
    Every `gh pr close` / `pr reopen` / `pr comment` is recorded in `writes`.
    """

    def __init__(self, prs, ref=None, age=30.0, compare="ahead", views=None,
                 commit_error=None, view_error=None, fail_writes=()):
        self.prs = prs
        self.ref = ref if ref is not None else {BRANCH: TIP}
        self.age = age
        self.compare = compare
        self.views = views or {}
        self.commit_error = commit_error
        self.view_error = view_error
        self.fail_writes = set(fail_writes)
        self.writes = []
        self.reads = []
        self.stdout = ""

    # -- reads ---------------------------------------------------------------
    def gh(self, *args):
        self.reads.append(args)
        if args[:2] == ("pr", "list"):
            return json.dumps(self.prs)
        return ""

    def gh_read(self, *args):
        self.reads.append(args)
        if args[:2] == ("pr", "list"):
            return json.dumps(self.prs)
        if args[:2] == ("pr", "view"):
            if self.view_error:
                raise self.view_error
            n = int(args[2])
            listed = next(p for p in self.prs if p["number"] == n)
            return json.dumps({
                "headRefOid": self.views.get(n, listed["headRefOid"]),
                "state": "OPEN",
            })
        path = args[1] if len(args) > 1 else ""
        if "/git/ref/heads/" in path:
            name = path.split("/git/ref/heads/", 1)[1]
            tip = self.ref.get(name)
            if isinstance(tip, Exception):
                raise tip
            if isinstance(tip, list):  # partial matches, not this branch
                return json.dumps(tip)
            return json.dumps({"ref": f"refs/heads/{name}",
                               "object": {"sha": tip, "type": "commit"}})
        if "/git/commits/" in path:
            if self.commit_error:
                raise self.commit_error
            sha = path.rsplit("/", 1)[1]
            return json.dumps({
                "sha": sha,
                "parents": [{"sha": STALE}],
                "committer": {"date": _iso(self.age)},
            })
        if "/compare/" in path:
            return json.dumps({"status": self.compare})
        return ""

    # -- writes --------------------------------------------------------------
    def run_proc(self, cmd, *a, **k):
        if list(cmd[:2]) == ["gh", "pr"] and cmd[2] in ("close", "reopen", "comment"):
            verb, number = cmd[2], int(cmd[3])
            body = cmd[cmd.index("--body") + 1] if "--body" in cmd else None
            self.writes.append((verb, number, body))
            rc = 1 if verb in self.fail_writes else 0
            return subprocess.CompletedProcess(cmd, rc, "", "boom" if rc else "")
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    def verbs(self, verb, number=780):
        return [w for w in self.writes if w[0] == verb and w[1] == number]

    def notices(self, number=780):
        return [w[2] for w in self.verbs("comment", number)]

    def run(self):
        out = io.StringIO()
        with mock.patch.object(reconcile, "gh", side_effect=self.gh), \
                mock.patch.object(reconcile, "gh_read", side_effect=self.gh_read), \
                mock.patch.object(reconcile.subprocess, "run", side_effect=self.run_proc), \
                mock.patch.object(reconcile, "_write_failures", []) as wf, \
                mock.patch.object(reconcile, "_read_failures", []) as rf, \
                mock.patch.object(reconcile, "_degraded", []) as dg, \
                contextlib.redirect_stdout(out):
            reconcile.resync_desynced_heads()
            self.write_failures, self.read_failures, self.degraded = (
                list(wf), list(rf), list(dg))
        self.stdout = out.getvalue()
        return self


def _worker(body):
    return {"author": WORKER, "body": body}


class Replay780Test(unittest.TestCase):
    """The incident, replayed: the sweep does what the operator did, once."""

    def test_780_is_closed_and_reopened_exactly_once(self):
        gh = _GitHub([_pr()]).run()
        self.assertIn("head-desync: PR #780 79764952 → d1754c31", gh.stdout)
        self.assertEqual(len(gh.verbs("close")), 1, gh.writes)
        self.assertEqual(len(gh.verbs("reopen")), 1, gh.writes)
        verbs = [w[0] for w in gh.writes]
        self.assertLess(verbs.index("close"), verbs.index("reopen"),
                        "the reopen must follow the close")
        self.assertEqual(gh.write_failures, [])
        self.assertEqual(gh.read_failures, [])

    def test_the_notice_names_the_tag_and_both_commits(self):
        gh = _GitHub([_pr()]).run()
        notices = gh.notices()
        self.assertEqual(len(notices), 1, gh.writes)
        body = notices[0]
        self.assertIn(head_desync.TAG, body)
        self.assertIn(STALE, body)
        self.assertIn(TIP, body)
        self.assertIn(head_desync.marker(TIP), body)
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, body)

    def test_the_second_sweep_with_the_notice_standing_resyncs_nothing(self):
        first = _GitHub([_pr()]).run()
        posted = first.notices()[0]
        # GitHub still has not moved the head: the same fixture, with the
        # notice the first sweep posted now on the pull request.
        second = _GitHub([_pr(comments=[_worker(posted)])]).run()
        self.assertEqual(second.verbs("close"), [], second.writes)
        self.assertEqual(second.verbs("reopen"), [], second.writes)

    def test_still_desynced_after_the_resync_is_said_once_and_never_repeated(self):
        first = _GitHub([_pr()]).run()
        resync = first.notices()[0]
        second = _GitHub([_pr(comments=[_worker(resync)])]).run()
        told = second.notices()
        self.assertEqual(len(told), 1, second.writes)
        self.assertIn(head_desync.unresolved_marker(TIP), told[0])
        self.assertIn(STALE, told[0])
        self.assertIn(TIP, told[0])
        self.assertIn("UNRESOLVED", second.stdout.upper())
        third = _GitHub([_pr(comments=[_worker(resync), _worker(told[0])])]).run()
        self.assertEqual(third.writes, [], "the sweep repeated itself")

    def test_a_newer_branch_commit_earns_its_own_resync(self):
        first = _GitHub([_pr()]).run()
        posted = first.notices()[0]
        newer = "e" * 40
        gh = _GitHub([_pr(comments=[_worker(posted)])],
                     ref={BRANCH: newer}).run()
        self.assertEqual(len(gh.verbs("close")), 1, gh.writes)
        self.assertEqual(len(gh.verbs("reopen")), 1, gh.writes)

    def test_a_notice_from_anyone_but_the_worker_bot_is_not_the_key(self):
        # A planted notice must not be able to freeze a desynced PR (DRE-1998).
        planted = {"author": {"login": "someone"},
                   "body": head_desync.resync_notice(780, STALE, TIP, 30)}
        gh = _GitHub([_pr(comments=[planted])]).run()
        self.assertEqual(len(gh.verbs("close")), 1, gh.writes)
        self.assertEqual(len(gh.verbs("reopen")), 1, gh.writes)


class InSyncTest(unittest.TestCase):

    def test_a_head_that_equals_its_branch_ref_is_left_alone(self):
        gh = _GitHub([_pr(head=TIP)]).run()
        self.assertEqual(gh.writes, [], "no close, no reopen, no notice")
        self.assertNotIn("head-desync: PR #780", gh.stdout)

    def test_the_resync_receipt_does_not_read_as_the_unresolved_one(self):
        # The two markers must not contain each other, or one notice would
        # silence the other.
        resync = head_desync.resync_notice(780, STALE, TIP, 30)
        self.assertNotIn(head_desync.unresolved_marker(TIP), resync)
        for why in ("after", "diverged"):
            told = head_desync.unresolved_notice(780, STALE, TIP, why)
            self.assertNotIn(head_desync.marker(TIP), told)

    def test_a_non_card_branch_is_not_read(self):
        gh = _GitHub([_pr(branch="hotfix/readme")],
                     ref={"hotfix/readme": TIP}).run()
        self.assertEqual(gh.writes, [])
        self.assertFalse(any("/git/ref/heads/" in " ".join(r) for r in gh.reads))


class UnknownTest(unittest.TestCase):
    """Unreadable is never "in sync" and never a licence to act (DRE-2034)."""

    def _assert_unknown(self, gh):
        self.assertEqual(gh.verbs("close"), [], gh.writes)
        self.assertEqual(gh.verbs("reopen"), [], gh.writes)
        self.assertEqual(gh.notices(), [], gh.writes)
        self.assertIn("UNKNOWN", gh.stdout)
        self.assertNotIn("in sync", gh.stdout.lower())

    def test_a_403_on_the_branch_ref_is_unknown(self):
        err = reconcile.ReconcileReadError(
            "gh api failed rc=1: gh: Resource not accessible by integration (HTTP 403)")
        gh = _GitHub([_pr()], ref={BRANCH: err}).run()
        self._assert_unknown(gh)
        self.assertTrue(gh.read_failures, "an unreadable ref must be recorded")

    def test_a_5xx_on_the_branch_ref_is_unknown(self):
        err = reconcile.ReconcileReadError(
            "gh api failed rc=1: gh: HTTP 502 (bad gateway)")
        gh = _GitHub([_pr()], ref={BRANCH: err}).run()
        self._assert_unknown(gh)
        self.assertTrue(gh.read_failures, "an unreadable ref must be recorded")

    def test_a_rate_limited_branch_ref_is_unknown_and_degraded(self):
        err = reconcile.ReconcileRateLimited(
            "gh api failed rc=1: API rate limit exceeded (HTTP 403)")
        gh = _GitHub([_pr()], ref={BRANCH: err}).run()
        self._assert_unknown(gh)
        self.assertTrue(gh.degraded)
        self.assertEqual(gh.read_failures, [])

    def test_a_ref_read_that_answers_partial_matches_is_unknown(self):
        partial = [{"ref": f"refs/heads/{BRANCH}-2", "object": {"sha": TIP}}]
        gh = _GitHub([_pr()], ref={BRANCH: partial}).run()
        self._assert_unknown(gh)

    def test_an_unreadable_branch_commit_is_unknown(self):
        err = reconcile.ReconcileReadError("gh api failed rc=1: HTTP 500")
        gh = _GitHub([_pr()], commit_error=err).run()
        self._assert_unknown(gh)

    def test_an_unreadable_pull_request_is_unknown(self):
        err = reconcile.ReconcileReadError("gh pr view failed rc=1: HTTP 502")
        gh = _GitHub([_pr()], view_error=err).run()
        self._assert_unknown(gh)

    def test_one_unreadable_pr_does_not_cost_the_rest(self):
        other = "agent/DRE-9-other"
        err = reconcile.ReconcileReadError("gh api failed rc=1: HTTP 502")
        gh = _GitHub([_pr(number=779, branch=other), _pr()],
                     ref={other: err, BRANCH: TIP}).run()
        self.assertEqual(gh.verbs("close", 779), [])
        self.assertEqual(len(gh.verbs("close", 780)), 1, gh.writes)


class SettlingTest(unittest.TestCase):

    def test_a_branch_commit_younger_than_15_minutes_is_left_alone(self):
        gh = _GitHub([_pr()], age=10.0).run()
        self.assertEqual(gh.writes, [], "GitHub's normal synchronize lag")

    def test_a_pull_request_github_synced_meanwhile_is_left_alone(self):
        # The fresh read of the pull request is the second witness: the
        # listing was stale, GitHub has moved the head since.
        gh = _GitHub([_pr()], views={780: TIP}).run()
        self.assertEqual(gh.writes, [])


class SafetyTest(unittest.TestCase):

    def test_a_diverged_branch_is_not_closed_and_a_person_is_told_once(self):
        # A force-pushed branch: GitHub can refuse to reopen a pull request
        # whose head was rewritten, so the sweep never closes it.
        gh = _GitHub([_pr()], compare="diverged").run()
        self.assertEqual(gh.verbs("close"), [], gh.writes)
        self.assertEqual(gh.verbs("reopen"), [], gh.writes)
        told = gh.notices()
        self.assertEqual(len(told), 1)
        self.assertIn(head_desync.unresolved_marker(TIP), told[0])
        again = _GitHub([_pr(comments=[_worker(told[0])])], compare="diverged").run()
        self.assertEqual(again.writes, [])

    def test_a_failed_close_reopens_nothing_and_fails_the_run(self):
        gh = _GitHub([_pr()], fail_writes={"close"}).run()
        self.assertEqual(gh.verbs("reopen"), [])
        self.assertEqual(gh.notices(), [])
        self.assertTrue(gh.write_failures)

    def test_a_failed_reopen_is_retried_and_then_said_loudly(self):
        gh = _GitHub([_pr()], fail_writes={"reopen"}).run()
        self.assertEqual(len(gh.verbs("reopen")), 2, "one retry, no more")
        self.assertTrue(any("CLOSED" in f for f in gh.write_failures),
                        gh.write_failures)


class SweepWiringTest(unittest.TestCase):

    def test_the_step_runs_in_the_full_sweep_beside_the_dead_head_backstop(self):
        import inspect
        src = inspect.getsource(reconcile.main)
        self.assertIn("retrigger_dead_heads,\n", src)
        dead = src.index("retrigger_dead_heads,\n")
        mine = src.index("resync_desynced_heads,\n")
        self.assertLess(abs(mine - dead), 600,
                        "resync_desynced_heads sits beside retrigger_dead_heads")


if __name__ == "__main__":
    unittest.main()
