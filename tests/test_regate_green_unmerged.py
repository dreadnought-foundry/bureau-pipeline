"""RED-first tests for DRE-9123 — an approved, green, CLEAN pull request the
merge gate never looked at again goes back to the gate.

2026-11-03, agent-bureau #4639 and #4641: at 7:56 AM PT the gate refreshed
both from `main` for new tests (DRE-6871). Every check on the new heads was
green by about 8:04 with the critic's APPROVE carried and `call / verify`
PASS, and both read CLEAN. No gate run evaluated either again: its next look
hangs on a CI-completion wake that the refreshed heads' green checks did not
raise, and the sweep's own gate nudge waits for the review lane's two-hour
stall window. A person merged both at 9:19, 75 minutes after they went green.

The sweep's new route, `regate_green_unmerged`, sends such a pull request back:

  * on a head the gate REFRESHED — the newest gate note on the thread is a
    refresh (`merge_gate.REFRESH_NOTE_OPENER`) — as soon as every check on
    the head has finished green: the refresh note is the gate's arrangement
    to be asked again, and the sweep keeps it;
  * on any other head, once its last check finished more than
    `REGATE_MINUTES` (15) ago and it is still unmerged;
  * only on an `agent/` branch that is open, not a draft, CLEAN, with the
    critic's APPROVE bound to its head (by sha or carried by content,
    `verdict_bound`), no Verifier verdict standing other than PASS or SKIP,
    and every gating check finished green (`merge_gate.precheck`);
  * once per head: the dispatch posts one `🔁 regate @<head>` line on the
    pull request naming why, and a head carrying it is not dispatched again
    while that gate run is in flight, or after it.

The first class runs the chain for real: the shipped gate step refreshes the
branch, the sweep reads the note it posted and dispatches the gate, and the
shipped gate step run for that dispatch merges.

Run: cd bureau-pipeline && python3 -m pytest tests/test_regate_green_unmerged.py -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "test")

import merge_gate  # noqa: E402
import reconcile  # noqa: E402
import step_shell  # noqa: E402
import verdict_content  # noqa: E402
from test_merge_gate_new_test_recheck_wiring import (  # noqa: E402
    BRANCH,
    HEAD,
    PR,
    PR_FILES,
    QA_LOGIN,
    REFRESHED_HEAD,
    WORKFLOW,
    compare_payload,
    declaration,
    run_shipped_step,
)

QA_BOT = reconcile.QA_BOT_LOGIN
WORKER_BOT = reconcile.WORKER_BOT_LOGIN
OTHER_HEAD = "4639a11ce0de5ea1b2c3d4e5f60718293a4b5c6d"
CI_PATH = ".github/workflows/tests.yml"
VERIFY_PATH = ".github/workflows/verify.yml"


def _iso(minutes_ago: float) -> str:
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def comment(login: str, body: str, minutes_ago: float) -> dict:
    """GraphQL shape, as `gh pr list --json comments` returns it."""
    return {"author": {"login": login}, "body": body,
            "createdAt": _iso(minutes_ago)}


def approve(sha: str, content: str | None = None) -> str:
    tail = f" content:{content}" if content else ""
    return f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: APPROVE @{sha}{tail}"


def critic(token: str, sha: str) -> str:
    return f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: {token} @{sha}"


def verifier(token: str, sha: str) -> str:
    return f"🧪 {merge_gate.VERIFIER_MARKER} — VERDICT: {token} @{sha}"


def check(name: str, finished_minutes_ago: float | None, *,
          conclusion: str = "success", suite: int = 1) -> dict:
    """One check run on the head, REST shape; `None` is still running."""
    done = finished_minutes_ago is not None
    return {
        "name": name,
        "status": "completed" if done else "in_progress",
        "conclusion": conclusion if done else None,
        "completed_at": _iso(finished_minutes_ago) if done else None,
        "check_suite": {"id": suite},
    }


def workflow_run(path: str, suite: int, status: str = "completed") -> dict:
    return {"id": suite * 1000, "path": path, "event": "pull_request",
            "check_suite_id": suite, "status": status,
            "conclusion": "success" if status == "completed" else None}


def green_checks() -> list[dict]:
    """Every check green, the last finished 20 minutes ago — stamped when the
    sweep runs, never at import: a suite that reaches this file late would
    otherwise read an older head than the one written here (DRE-6630)."""
    return [check("scripts unit tests", 25, suite=1),
            check("call / verify", 20, suite=2)]


GREEN_RUNS = [workflow_run(CI_PATH, 1), workflow_run(VERIFY_PATH, 2)]


def pr_payload(comments, *, head: str = OTHER_HEAD, number: int = 4639,
               merge_state: str = "CLEAN", draft: bool = False,
               branch: str = "agent/DRE-9030-terminal-endpoint") -> dict:
    return {
        "number": number,
        "headRefName": branch,
        "headRefOid": head,
        "baseRefName": "main",
        "mergeStateStatus": merge_state,
        "isDraft": draft,
        "comments": list(comments),
    }


#: #4641's thread at 8:30 AM PT, with no refresh in it: the critic's APPROVE
#: and the Verifier's PASS on the head.
APPROVED_VERIFIED = [
    comment(QA_BOT, approve(OTHER_HEAD), 60),
    comment(QA_BOT, verifier("PASS", OTHER_HEAD), 30),
]


class Sweep:
    """Run `regate_green_unmerged` once over `prs` with GitHub stubbed.

    `checks` / `runs` answer the head's check-run and workflow-run reads
    (`None` = the read failed); `compare` / `commits` answer the carry's two
    reads; `gate_busy` is the gate workflow's run listing."""

    def __init__(self, prs, *, checks=..., runs=GREEN_RUNS,
                 compare=None, commits=None, gate_busy=False,
                 dispatch_fails=False):
        if checks is ...:
            checks = green_checks()
        self.dispatches: list[tuple] = []
        self.notes: list[tuple] = []
        self.reads: list[tuple] = []

        def gh(*args):
            self.reads.append(args)
            if args[:2] == ("pr", "list"):
                return json.dumps(prs)
            if args[:2] == ("run", "list"):
                return json.dumps([{"status": "in_progress"}] if gate_busy
                                  else [])
            path = next((a for a in args if a.startswith("repos/")), "")
            if "/check-runs" in path:
                return "" if checks is None else json.dumps(
                    [{"total_count": len(checks), "check_runs": checks}])
            if "/actions/runs" in path:
                return "" if runs is None else json.dumps(
                    {"total_count": len(runs), "workflow_runs": runs})
            if "/compare/" in path:
                return json.dumps(compare) if compare is not None else ""
            if path.endswith("/commits?per_page=100"):
                return json.dumps(commits) if commits is not None else ""
            return ""

        def dispatch(*args):
            if dispatch_fails:
                raise reconcile.ReconcileWriteError("HTTP 403")
            self.dispatches.append(args)

        before = list(reconcile._read_failures), list(reconcile._write_failures)
        reconcile.reset_sweep_cards()
        try:
            with mock.patch.dict(os.environ, {"GH_DISPATCH_TOKEN": ""}), \
                    mock.patch.object(reconcile, "gh", side_effect=gh), \
                    mock.patch.object(reconcile, "gh_dispatch",
                                      side_effect=dispatch), \
                    mock.patch.object(reconcile, "_post_pr_note",
                                      side_effect=lambda n, b:
                                      self.notes.append((n, b)) or True):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    reconcile.regate_green_unmerged()
                self.log = buf.getvalue()
        finally:
            del reconcile._read_failures[len(before[0]):]
            del reconcile._write_failures[len(before[1]):]
            reconcile.reset_sweep_cards()

    @property
    def gate_dispatches(self):
        return [d for d in self.dispatches
                if reconcile.gate_workflow() in d]

    def posted(self):
        """The sweep's receipts as the next sweep's listing shows them."""
        return [comment(WORKER_BOT, body, 0) for _, body in self.notes]


# --------------------------------------------------------------------------- #
# Criterion 1 — the refreshed pull request is evaluated again                  #
# --------------------------------------------------------------------------- #


def as_listing(rest_comments, minutes_ago: float) -> list[dict]:
    """The shipped step's REST thread, as the sweep's listing reads it."""
    return [{"author": {"login": c["user"]["login"].removesuffix("[bot]")},
             "body": c["body"], "createdAt": _iso(minutes_ago)}
            for c in rest_comments]


class RefreshedPrIsEvaluatedAgainTest(unittest.TestCase):
    """The DRE-9030 chain: the gate refreshes for new tests, the new head's
    checks all finish green with the APPROVE carried and the Verifier's
    check passed, and the gate evaluates it again and merges — with no
    CI-completion wake anywhere in the chain."""

    @classmethod
    def setUpClass(cls):
        cls.cid = verdict_content.content_id(compare_payload(PR_FILES, 0))
        cls.verdict = approve(HEAD, cls.cid)
        # 1. The gate's evaluation at 7:56: it refreshes and does not merge.
        cls.first = run_shipped_step(declared=declaration(),
                                     verdict=cls.verdict)
        # 2. The sweep, three minutes after the last check on the new head
        #    finished green — well inside REGATE_MINUTES.
        listing = as_listing(cls.first.comments, 10)
        cls.pr = pr_payload(listing, head=REFRESHED_HEAD, number=PR,
                            branch=BRANCH)
        cls.commits = [{"sha": HEAD}, {"sha": REFRESHED_HEAD}]
        cls.sweep = Sweep(
            [cls.pr],
            checks=[check("scripts unit tests", 3, suite=1),
                    check("call / verify", 4, suite=2)],
            compare=compare_payload(PR_FILES, 0), commits=cls.commits)
        # 3. The gate run that dispatch starts, at the refreshed head.
        cls.again = run_shipped_step(
            declared=declaration(), head=REFRESHED_HEAD, behind_by=0,
            seed_comments=[c for c in cls.first.comments
                           if c["body"] != cls.verdict],
            verdict=cls.verdict,
            pr_commits=[{"sha": HEAD, "commit": {"message": "feat"}},
                        {"sha": REFRESHED_HEAD,
                         "commit": {"message": "Merge main"}}])

    def test_the_first_evaluation_refreshed_and_did_not_merge(self):
        self.assertEqual(self.first.proc.returncode, 0, self.first.explain())
        self.assertEqual(len(self.first.puts), 1, self.first.explain())
        self.assertEqual(self.first.merges, [])
        self.assertEqual(len(self.first.new_tests_notes), 1)

    def test_the_refresh_note_is_the_arrangement_the_sweep_reads(self):
        note = self.first.new_tests_notes[0]
        self.assertTrue(note.startswith(merge_gate.REFRESH_NOTE_OPENER), note)
        self.assertTrue(reconcile.refresh_pending(self.pr))

    def test_the_gate_says_in_one_line_that_it_will_be_asked_again(self):
        lines = [ln for ln in self.first.proc.stdout.splitlines()
                 if ln.startswith("regate: ")]
        self.assertEqual(len(lines), 1, self.first.proc.stdout)
        self.assertIn(f"PR #{PR}", lines[0])
        self.assertIn("refreshed", lines[0])
        self.assertIn("checks finish", lines[0])

    def test_the_sweep_dispatches_the_gate_for_that_pull_request_once(self):
        self.assertEqual(len(self.sweep.gate_dispatches), 1,
                         (self.sweep.dispatches, self.sweep.log))
        self.assertIn(f"pr_number={PR}", " ".join(self.sweep.gate_dispatches[0]))
        self.assertEqual(len(self.sweep.notes), 1, self.sweep.notes)
        number, body = self.sweep.notes[0]
        self.assertEqual(number, PR)
        self.assertEqual(len(body.splitlines()), 1, body)
        self.assertTrue(body.startswith(f"🔁 {reconcile.REGATE_KEY} "
                                        f"@{REFRESHED_HEAD}"), body)
        self.assertIn(f"PR #{PR}", body)
        self.assertIn("refreshed", body)

    def test_the_dispatched_gate_run_merges_it(self):
        self.assertEqual(self.again.proc.returncode, 0, self.again.explain())
        self.assertIn("carried", self.again.proc.stdout)
        self.assertEqual(self.again.puts, [], self.again.explain())
        self.assertEqual(len(self.again.merges), 1, self.again.explain())
        self.assertEqual(self.again.merges[0][-1], REFRESHED_HEAD)

    def test_a_refreshed_head_still_running_is_not_dispatched(self):
        sweep = Sweep([self.pr],
                      checks=[check("scripts unit tests", 3, suite=1),
                              check("call / verify", None, suite=2)],
                      compare=compare_payload(PR_FILES, 0),
                      commits=self.commits)
        self.assertEqual(sweep.gate_dispatches, [], sweep.log)
        self.assertEqual(sweep.notes, [])

    def test_a_decline_after_the_refresh_discharges_it(self):
        """The gate spoke on the new head: only the 15-minute route is left,
        and three minutes is inside it."""
        declined = comment(
            QA_BOT, f"⏸️ Merge gate: declined @{REFRESHED_HEAD} — held", 2)
        pr = dict(self.pr, comments=self.pr["comments"] + [declined])
        self.assertFalse(reconcile.refresh_pending(pr))
        sweep = Sweep([pr],
                      checks=[check("scripts unit tests", 3, suite=1),
                              check("call / verify", 4, suite=2)],
                      compare=compare_payload(PR_FILES, 0),
                      commits=self.commits)
        self.assertEqual(sweep.gate_dispatches, [], sweep.log)

    def test_a_refresh_note_nobody_but_the_gate_wrote_is_not_one(self):
        forged = [c if c["author"]["login"] != QA_BOT or
                  not c["body"].startswith(merge_gate.REFRESH_NOTE_OPENER)
                  else dict(c, author={"login": "someone"})
                  for c in self.pr["comments"]]
        self.assertFalse(reconcile.refresh_pending(dict(self.pr,
                                                        comments=forged)))


class EveryRefreshNoteOpensWithTheOpenerTest(unittest.TestCase):
    """The arrangement is the note's opener, so every branch write the gate
    makes must post a note that opens with it — the two refreshes and the
    out-of-date update (DRE-6195) — and say so in one log line."""

    @classmethod
    def setUpClass(cls):
        cls.body = step_shell.workflow_source(WORKFLOW)

    def test_each_note_opens_with_the_opener(self):
        self.assertEqual(merge_gate.REFRESH_NOTE_OPENER, "♻️ Merge gate: ")
        for mark in ('REFRESH_MARK="Merge gate: refreshed for new tests onto',
                     'REFRESH_MARK="Merge gate: refreshed onto',
                     'UPDATE_MARK="Merge gate: updated onto'):
            self.assertIn(mark, self.body)
        self.assertEqual(self.body.count("printf '♻️ %s — "), 3)

    def test_each_branch_write_logs_one_regate_line(self):
        self.assertEqual(self.body.count('echo "regate: PR #$PR '), 2,
                         "one line for the refresh, one for the update")


# --------------------------------------------------------------------------- #
# Criterion 2 — green, unmerged past the threshold                             #
# --------------------------------------------------------------------------- #


class GreenUnmergedPastThresholdTest(unittest.TestCase):

    def test_it_dispatches_the_gate_once_and_says_why(self):
        sweep = Sweep([pr_payload(APPROVED_VERIFIED)])
        self.assertEqual(len(sweep.gate_dispatches), 1, sweep.log)
        self.assertIn("pr_number=4639", " ".join(sweep.gate_dispatches[0]))
        self.assertEqual(len(sweep.notes), 1)
        number, body = sweep.notes[0]
        self.assertEqual(number, 4639)
        self.assertEqual(len(body.splitlines()), 1, body)
        self.assertTrue(body.startswith(
            f"🔁 {reconcile.REGATE_KEY} @{OTHER_HEAD}: PR #4639 "), body)
        self.assertIn("20 minutes", body)
        for forbidden in merge_gate.VERDICT_MARKERS:
            self.assertNotIn(forbidden, body)
        lines = [ln for ln in sweep.log.splitlines()
                 if ln.startswith("regate: PR #4639")]
        self.assertEqual(len(lines), 1, sweep.log)

    def test_it_does_not_dispatch_again_while_that_gate_run_is_in_flight(self):
        first = Sweep([pr_payload(APPROVED_VERIFIED)])
        self.assertEqual(len(first.gate_dispatches), 1)
        again = Sweep([pr_payload(APPROVED_VERIFIED + first.posted())],
                      gate_busy=True)
        self.assertEqual(again.gate_dispatches, [], again.log)
        self.assertEqual(again.notes, [])

    def test_nor_after_it_on_the_same_head(self):
        first = Sweep([pr_payload(APPROVED_VERIFIED)])
        again = Sweep([pr_payload(APPROVED_VERIFIED + first.posted())])
        self.assertEqual(again.gate_dispatches, [], again.log)

    def test_a_new_head_is_dispatched_again(self):
        first = Sweep([pr_payload(APPROVED_VERIFIED)])
        moved = "5555a11ce0de5ea1b2c3d4e5f60718293a4b5c6d"
        thread = [comment(QA_BOT, approve(moved), 50),
                  comment(QA_BOT, verifier("PASS", moved), 30)]
        again = Sweep([pr_payload(APPROVED_VERIFIED + first.posted() + thread,
                                  head=moved)])
        self.assertEqual(len(again.gate_dispatches), 1, again.log)

    def test_inside_the_threshold_it_waits(self):
        sweep = Sweep([pr_payload(APPROVED_VERIFIED)],
                      checks=[check("scripts unit tests", 12, suite=1),
                              check("call / verify", 5, suite=2)])
        self.assertEqual(sweep.gate_dispatches, [], sweep.log)

    def test_the_threshold_is_regate_minutes(self):
        with mock.patch.object(reconcile, "REGATE_MINUTES", 30):
            sweep = Sweep([pr_payload(APPROVED_VERIFIED)])
        self.assertEqual(sweep.gate_dispatches, [], sweep.log)
        self.assertEqual(reconcile.REGATE_MINUTES, 15)

    def test_a_carried_approve_counts(self):
        files = PR_FILES
        cid = verdict_content.content_id(compare_payload(files, 0))
        thread = [comment(QA_BOT, approve(HEAD, cid), 60)]
        sweep = Sweep([pr_payload(thread)],
                      compare=compare_payload(files, 0),
                      commits=[{"sha": HEAD}, {"sha": OTHER_HEAD}])
        self.assertEqual(len(sweep.gate_dispatches), 1, sweep.log)

    def test_no_verifier_verdict_at_all_counts_as_verified_by_its_check(self):
        sweep = Sweep([pr_payload(APPROVED_VERIFIED[:1])])
        self.assertEqual(len(sweep.gate_dispatches), 1, sweep.log)

    def test_a_failed_dispatch_posts_nothing(self):
        sweep = Sweep([pr_payload(APPROVED_VERIFIED)], dispatch_fails=True)
        self.assertEqual(sweep.notes, [])

    def test_one_summary_line_a_sweep(self):
        sweep = Sweep([])
        self.assertEqual(
            [ln for ln in sweep.log.splitlines() if ln.startswith("regate: ")],
            ["regate: 0 candidate(s), 0 dispatched"])


# --------------------------------------------------------------------------- #
# Criterion 3 — red or unapproved pull requests are left alone                 #
# --------------------------------------------------------------------------- #


class NoDispatchTest(unittest.TestCase):

    def assert_left(self, sweep):
        self.assertEqual(sweep.gate_dispatches, [], sweep.log)
        self.assertEqual(sweep.notes, [])

    def test_a_failing_check(self):
        self.assert_left(Sweep(
            [pr_payload(APPROVED_VERIFIED)],
            checks=[check("scripts unit tests", 25, conclusion="failure",
                          suite=1),
                    check("call / verify", 20, suite=2)]))

    def test_a_check_still_running(self):
        self.assert_left(Sweep(
            [pr_payload(APPROVED_VERIFIED)],
            checks=[check("scripts unit tests", 25, suite=1),
                    check("call / verify", None, suite=2)]))

    def test_a_workflow_run_still_queued(self):
        self.assert_left(Sweep(
            [pr_payload(APPROVED_VERIFIED)],
            runs=GREEN_RUNS + [workflow_run(CI_PATH, 3, status="queued")]))

    def test_unreadable_checks_or_runs(self):
        self.assert_left(Sweep([pr_payload(APPROVED_VERIFIED)], checks=None))
        self.assert_left(Sweep([pr_payload(APPROVED_VERIFIED)], runs=None))

    def test_no_approve_on_the_head(self):
        for thread in (
            [],
            [comment(QA_BOT, critic("REQUEST_CHANGES", OTHER_HEAD), 60)],
            # An APPROVE for an older head that does not carry.
            [comment(QA_BOT, approve(HEAD), 60)],
            # A forged APPROVE from someone who is not the qa-bot.
            [comment("someone", approve(OTHER_HEAD), 60)],
        ):
            with self.subTest(thread=thread):
                self.assert_left(Sweep([pr_payload(thread)],
                                       compare=compare_payload(PR_FILES, 0),
                                       commits=[{"sha": OTHER_HEAD}]))

    def test_a_standing_verifier_fail(self):
        thread = [comment(QA_BOT, approve(OTHER_HEAD), 60),
                  comment(QA_BOT, verifier("FAIL", OTHER_HEAD), 30)]
        self.assert_left(Sweep([pr_payload(thread)]))

    def test_not_clean(self):
        for state in ("BLOCKED", "DIRTY", "BEHIND", "UNKNOWN", "UNSTABLE"):
            with self.subTest(state=state):
                self.assert_left(Sweep([pr_payload(APPROVED_VERIFIED,
                                                   merge_state=state)]))

    def test_a_draft_or_a_branch_that_is_not_an_agent_branch(self):
        self.assert_left(Sweep([pr_payload(APPROVED_VERIFIED, draft=True)]))
        for branch in ("feature/x", "dependabot/pip/x", "repair/main-red"):
            with self.subTest(branch=branch):
                self.assert_left(Sweep([pr_payload(APPROVED_VERIFIED,
                                                   branch=branch)]))


class TheRouteRunsInTheFullSweepTest(unittest.TestCase):

    def test_it_is_one_of_the_backstops(self):
        source = (ROOT / "scripts" / "reconcile.py").read_text()
        body = source[source.index("def main("):]
        self.assertIn("            regate_green_unmerged,\n", body)


if __name__ == "__main__":
    unittest.main()
