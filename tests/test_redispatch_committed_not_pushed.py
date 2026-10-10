"""RED-first tests for DRE-6352 — a fix run that ended 'committed, not pushed'
and whose delivery never landed gets the fix loop RESTARTED, once.

DRE-4883, 2026-10-08: the fix run finished its fix, committed it, and GitHub
refused the push. DRE-6351 taught the Report to say so — a worker-bot
`fix-run-committed-not-pushed` comment naming the head the branch is still
at — and to hand the commits to the `deliver-rescue` follow-up. When that
follow-up never lands (its dispatch 403s, the job fails, the patch does not
apply) the pull request sits with a blocking verdict, a worker-bot comment
newer than it, and no run coming. Every one of the four existing fix-loop
recovery routes reads that newer comment as the loop's last word, and the
pull request waited twenty-five minutes for a person to restart it by hand.

This suite pins the fifth route, `redispatch_committed_not_pushed`, to the
house pattern `redispatch_standing_verdicts` set:

  * the newest worker-bot comment carries `fix_dead_run.COMMITTED_NOT_PUSHED_TAG`
    on its first line, and the head it names (`head still at <sha8>`) is
    still the pull request's head — a moved head means the delivery landed;
  * the marker is older than `fix_dead_run.COMMITTED_NOT_PUSHED_WAIT_MINUTES`
    (the delivery's ceiling is ten minutes, so thirty is a finished attempt);
  * no more than `fix_dead_run.COMMITTED_NOT_PUSHED_RESTARTS` worker-bot
    markers stand for that head — the bound, shared with the Report;
  * the fix budget has room (`fix_budget.decide`), the card is not
    human-parked, the fix lane is not busy and the repo has a fix agent;
  * one dispatch, one `fix-loop-restarted` receipt — which is a newer
    worker-bot comment, so the same marker is never dispatched twice;
  * one summary line every sweep, including a sweep that finds nothing.

Run: cd bureau-pipeline && python3 -m pytest tests/test_redispatch_committed_not_pushed.py -v
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "test")

import fix_budget  # noqa: E402
import fix_dead_run  # noqa: E402
import merge_gate  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402

PR = 4883
HEAD = "9e2c4a7b1d3f5e6a8c0b2d4f6a8c0e2b4d6f8a1c"
MOVED_HEAD = "1f3e5d7c9b0a2c4e6f8a0b2d4f6e8c0a2b4d6f8e"
OTHER_HEAD = "7a7a7a7a5b5b5b5b3c3c3c3c1d1d1d1d0e0e0e0e"
LOCAL = "c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00"
QA_BOT = reconcile.QA_BOT_LOGIN
WORKER_BOT = reconcile.WORKER_BOT_LOGIN
TAG = fix_dead_run.COMMITTED_NOT_PUSHED_TAG
WAIT = fix_dead_run.COMMITTED_NOT_PUSHED_WAIT_MINUTES

#: The summary line's shape, read off the card: four counts, once a sweep.
SUMMARY = re.compile(
    r"^committed-not-pushed: (\d+) candidate\(s\), (\d+) waiting on delivery, "
    r"(\d+) at the restart cap, (\d+) dispatched$",
    re.M,
)


def verdict_body(sha: str = HEAD) -> str:
    """The critic's REQUEST_CHANGES, built through merge_gate's grammar."""
    return (
        f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: REQUEST_CHANGES @{sha}\n\n"
        "The retry path still swallows the error."
    )


def marker_body(head: str = HEAD, attempt: int = 2) -> str:
    """The comment DRE-6351's Report really posts, from its own renderer —
    so a change to the producer's grammar turns this suite red instead of
    leaving it agreeing with itself."""
    return fix_budget.committed_not_pushed_body(
        attempt, head, LOCAL, status="401", error="Bad credentials",
        artifact="rescue-fix-4883", run_url="https://github.com/x/y/actions/runs/1",
        delivery=fix_budget.DELIVERY_DISPATCHED, pr_open=True,
    )


def _iso(minutes_ago: float) -> str:
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def comment(login: str, body: str, minutes_ago: float) -> dict:
    """GraphQL shape, as `gh pr list --json comments` returns it."""
    return {"author": {"login": login}, "body": body, "createdAt": _iso(minutes_ago)}


def as_rest(c: dict) -> dict:
    """The same comment in the REST shape `_pr_thread` returns."""
    login = c["author"]["login"]
    bot = login in (QA_BOT, WORKER_BOT)
    return {
        "user": {"login": f"{login}[bot]" if bot else login,
                 "type": "Bot" if bot else "User"},
        "body": c["body"],
        "created_at": c["createdAt"],
    }


def dre_4883() -> list[dict]:
    """The DRE-4883 thread: a fix attempt that landed, the critic's
    REQUEST_CHANGES bound to the head, then the worker-bot marker naming that
    head, 45 minutes old. The head has not moved: the delivery never landed.

    Built per test, never once at import: the ages are read off the clock,
    and a module-level thread stamped at collection was ~61 minutes old by
    the time CI reached `test_the_wait_is_read_off_fix_dead_run` sixteen
    minutes later — past that test's 60-minute wait (DRE-6580)."""
    return [
        comment(WORKER_BOT, "🔧 Fix attempt 1 pushed — CI and critic review re-running.", 120),
        comment(QA_BOT, verdict_body(), 70),
        comment(WORKER_BOT, marker_body(), 45),
    ]


def pr_payload(comments, *, head: str = HEAD, merge_state: str = "BLOCKED",
               number: int = PR, branch: str = "agent/DRE-4883-portico") -> dict:
    return {
        "number": number,
        "headRefName": branch,
        "headRefOid": head,
        "mergeStateStatus": merge_state,
        "comments": list(comments),
    }


def summary(log: str) -> tuple[int, int, int, int] | None:
    found = SUMMARY.findall(log)
    return tuple(int(n) for n in found[-1]) if found else None


class CommittedNotPushedSweepTest(unittest.TestCase):
    """The route, driven exactly as the standing-verdict suite drives its own."""

    def sweep(self, prs, *, thread=None, busy: bool = False, parked: bool = False,
              absent: bool = False):
        """Run the route over `prs`; return (dispatches, PR notes, log).

        The REST thread defaults to each pull request's own comments in REST
        shape — the same comments, read the way `_pr_thread` reads them."""
        calls: list[tuple] = []
        notes: list[tuple] = []

        def gh(*args):
            if args[:2] == ("run", "list"):
                return json.dumps([{"status": "in_progress"}] if busy else [])
            if args[:2] == ("pr", "list"):
                return prs if isinstance(prs, str) else json.dumps(prs)
            if args[0] == "api" and "/comments" in args[-1]:
                if thread is not None:
                    return json.dumps(thread)
                number = int(args[-1].split("/issues/")[1].split("/")[0])
                pr = next(p for p in prs if p["number"] == number)
                return json.dumps([as_rest(c) for c in pr["comments"]])
            return ""

        before = list(reconcile._read_failures), list(reconcile._write_failures)
        # The open-PR listing is the sweep's memo: one sweep, one read.
        reconcile.reset_sweep_cards()
        try:
            with mock.patch.dict(os.environ, {"GH_DISPATCH_TOKEN": ""}), \
                    mock.patch.object(reconcile, "gh", side_effect=gh), \
                    mock.patch.object(reconcile, "gh_dispatch",
                                      side_effect=lambda *a: calls.append(a)), \
                    mock.patch.object(reconcile, "_post_pr_note",
                                      side_effect=lambda n, b: notes.append((n, b))), \
                    mock.patch.object(reconcile, "card_parked_for_human",
                                      return_value=parked), \
                    mock.patch.object(reconcile, "fix_agent_absent",
                                      return_value=absent):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    reconcile.redispatch_committed_not_pushed()
                log = buf.getvalue()
        finally:
            del reconcile._read_failures[len(before[0]):]
            del reconcile._write_failures[len(before[1]):]
            reconcile.reset_sweep_cards()
        return calls, notes, log

    # ---------------------------------------------------------------- the act

    def test_the_dre_4883_thread_dispatches_the_fix_agent_once(self):
        calls, notes, _ = self.sweep([pr_payload(dre_4883())])
        self.assertEqual(len(calls), 1, f"expected one dispatch, got {calls}")
        self.assertIn(reconcile.fix_workflow(), calls[0])
        self.assertIn(f"pr_number={PR}", " ".join(calls[0]))
        self.assertEqual(len(notes), 1, f"expected one receipt, got {notes}")
        self.assertEqual(notes[0][0], PR)

    def test_the_receipt_is_the_existing_fix_loop_restarted_act(self):
        _, notes, _ = self.sweep([pr_payload(dre_4883())])
        fields = pipeline_act.read_trailer(notes[0][1])
        self.assertIsNotNone(fields, f"the receipt carries no trailer: {notes[0][1]}")
        self.assertEqual(fields["act"], "fix-loop-restarted")
        self.assertEqual(fields["kind"], "recovery")

    def test_the_receipt_names_the_lost_push_the_wait_and_the_one_restart(self):
        _, notes, _ = self.sweep([pr_payload(dre_4883())])
        body = notes[0][1]
        self.assertIn("DRE-6352", body)
        self.assertIn("push never reached the branch", body)
        self.assertIn(f"delivery did not land within {WAIT} minutes", body)
        self.assertIn("restarted once", body)
        self.assertIn("redo the fix", body)
        self.assertIn("parks the card", body)
        # Never a verdict marker (standards/untrusted-content.md), and never
        # the tag on its first line — the receipt must not count as a marker.
        self.assertNotIn("VERDICT:", body)
        self.assertNotIn(TAG, body.splitlines()[0])

    def test_the_summary_line_counts_the_dispatch(self):
        _, _, log = self.sweep([pr_payload(dre_4883())])
        self.assertEqual(summary(log), (1, 0, 0, 1), log)
        self.assertEqual(len(SUMMARY.findall(log)), 1, "one summary line a sweep")

    def test_the_log_names_the_pr_and_the_head(self):
        _, _, log = self.sweep([pr_payload(dre_4883())])
        self.assertIn(f"PR #{PR}", log)
        self.assertIn(HEAD[:8], log)

    def test_only_one_pr_is_dispatched_per_sweep(self):
        other = pr_payload(dre_4883(), number=PR + 1, branch="agent/DRE-4884-x")
        calls, notes, _ = self.sweep([pr_payload(dre_4883()), other])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(notes), 1)

    # ---------------------------------------------------------- the three waits

    def test_a_ten_minute_old_marker_waits_on_the_delivery(self):
        fresh = dre_4883()[:2] + [comment(WORKER_BOT, marker_body(), 10)]
        calls, notes, log = self.sweep([pr_payload(fresh)])
        self.assertEqual(calls, [])
        self.assertEqual(notes, [])
        self.assertEqual(summary(log), (1, 1, 0, 0), log)

    def test_the_wait_is_read_off_fix_dead_run(self):
        # Non-vacuous twin of the 45-minute dispatch: raise the shared constant
        # past the marker's age and the same thread waits.
        with mock.patch.object(fix_dead_run, "COMMITTED_NOT_PUSHED_WAIT_MINUTES", 60):
            calls, _, log = self.sweep([pr_payload(dre_4883())])
        self.assertEqual(calls, [])
        self.assertEqual(summary(log), (1, 1, 0, 0), log)

    def test_a_moved_head_means_the_delivery_landed(self):
        calls, notes, _ = self.sweep([pr_payload(dre_4883(), head=MOVED_HEAD)])
        self.assertEqual(calls, [])
        self.assertEqual(notes, [])

    # -------------------------------------------------------------------- the cap

    def test_two_markers_for_the_head_are_at_the_restart_cap(self):
        capped = dre_4883()[:2] + [
            comment(WORKER_BOT, marker_body(attempt=2), 120),
            comment(WORKER_BOT, marker_body(attempt=3), 45),
        ]
        calls, notes, log = self.sweep([pr_payload(capped)])
        self.assertEqual(calls, [])
        self.assertEqual(notes, [])
        self.assertEqual(summary(log), (1, 0, 1, 0), log)

    def test_the_cap_is_read_off_fix_dead_run(self):
        capped = dre_4883()[:2] + [
            comment(WORKER_BOT, marker_body(attempt=2), 120),
            comment(WORKER_BOT, marker_body(attempt=3), 45),
        ]
        with mock.patch.object(fix_dead_run, "COMMITTED_NOT_PUSHED_RESTARTS", 2):
            calls, _, _ = self.sweep([pr_payload(capped)])
        self.assertEqual(len(calls), 1)

    def test_a_marker_for_another_head_does_not_count_toward_the_cap(self):
        mixed = dre_4883()[:2] + [
            comment(WORKER_BOT, marker_body(head=OTHER_HEAD, attempt=1), 200),
            comment(WORKER_BOT, marker_body(attempt=2), 45),
        ]
        calls, notes, log = self.sweep([pr_payload(mixed)])
        self.assertEqual(len(calls), 1, f"expected one dispatch, got {calls}")
        self.assertEqual(len(notes), 1)
        self.assertEqual(summary(log), (1, 0, 0, 1), log)

    # ------------------------------------------------------------- the disarm

    def test_the_receipt_disarms_the_route(self):
        _, notes, _ = self.sweep([pr_payload(dre_4883())])
        after = dre_4883() + [comment(WORKER_BOT, notes[0][1], 0)]
        calls, again, log = self.sweep([pr_payload(after)])
        self.assertEqual(calls, [], "the same marker was dispatched twice")
        self.assertEqual(again, [])
        self.assertEqual(summary(log), (0, 0, 0, 0), log)

    def test_a_newer_worker_bot_comment_means_the_loop_moved(self):
        # The Report's own hold, a fix attempt, a retry marker — any of them.
        moved = dre_4883() + [
            comment(WORKER_BOT, "🔧 Fix attempt 3 pushed — CI re-running.", 5)]
        self.assertEqual(self.sweep([pr_payload(moved)])[0], [])

    # ------------------------------------------------------ the six negatives

    def test_a_dirty_pr_does_not_dispatch(self):
        # unstick_conflicts owns conflicted pull requests.
        calls, _, _ = self.sweep([pr_payload(dre_4883(), merge_state="DIRTY")])
        self.assertEqual(calls, [])

    def test_a_human_parked_card_does_not_dispatch(self):
        # DRE-2024: the loop is over until a person acts.
        self.assertEqual(self.sweep([pr_payload(dre_4883())], parked=True)[0], [])

    def test_a_busy_fix_lane_does_not_dispatch(self):
        calls, notes, log = self.sweep([pr_payload(dre_4883())], busy=True)
        self.assertEqual(calls, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(summary(log), "a busy lane still prints the line")

    def test_an_exhausted_fix_budget_does_not_dispatch(self):
        marker, cap = fix_budget.BUDGETS["fix"]
        spent = [as_rest(comment(WORKER_BOT, f"{marker} {n}", 300)) for n in range(cap)]
        spent += [as_rest(c) for c in dre_4883()]
        self.assertEqual(self.sweep([pr_payload(dre_4883())], thread=spent)[0], [])

    def test_an_unreadable_thread_does_not_dispatch(self):
        # The GraphQL listing already showed comments, so an empty REST read is
        # unreadable, never empty (DRE-2034) — and the cap cannot be counted.
        self.assertEqual(self.sweep([pr_payload(dre_4883())], thread=[])[0], [])

    def test_a_marker_not_authored_by_the_worker_bot_does_not_dispatch(self):
        # DRE-1995: a planted marker must not spawn fix runs.
        forged = dre_4883()[:2] + [comment("some-human", marker_body(), 45)]
        calls, _, log = self.sweep([pr_payload(forged)])
        self.assertEqual(calls, [])
        self.assertEqual(summary(log), (0, 0, 0, 0), log)

    # ------------------------------------------------- the rest of the gates

    def test_a_marker_quoted_below_the_first_line_is_not_one(self):
        quoted = dre_4883()[:2] + [comment(
            WORKER_BOT, f"Some other note\n\n> {marker_body()}", 45)]
        self.assertEqual(self.sweep([pr_payload(quoted)])[0], [])

    def test_a_repo_with_no_fix_agent_does_not_dispatch(self):
        calls, _, log = self.sweep([pr_payload(dre_4883())], absent=True)
        self.assertEqual(calls, [])
        self.assertIsNotNone(summary(log))

    def test_a_non_card_branch_does_not_dispatch(self):
        calls, _, _ = self.sweep([pr_payload(dre_4883(), branch="dependabot/pip/x")])
        self.assertEqual(calls, [])

    def test_an_unreadable_listing_dispatches_nothing_and_still_prints(self):
        # The shared listing answers None, never [], on a failed read — and
        # records it loudly itself; this route says so and counts nothing.
        calls, _, log = self.sweep("not json")
        self.assertEqual(calls, [])
        self.assertIn("could not be read", log)
        self.assertEqual(summary(log), (0, 0, 0, 0), log)

    def test_a_sweep_with_no_candidates_still_prints_the_summary(self):
        _, _, log = self.sweep([])
        self.assertEqual(summary(log), (0, 0, 0, 0), log)
        self.assertEqual(len(SUMMARY.findall(log)), 1)


class ContractTest(unittest.TestCase):
    """The constants live once, in fix_dead_run.py, and no act is added."""

    SRC = inspect.getsource(reconcile.redispatch_committed_not_pushed) \
        if hasattr(reconcile, "redispatch_committed_not_pushed") else ""

    def test_the_route_reads_the_three_constants(self):
        for name in ("fix_dead_run.COMMITTED_NOT_PUSHED_TAG",
                     "fix_dead_run.COMMITTED_NOT_PUSHED_WAIT_MINUTES",
                     "fix_dead_run.COMMITTED_NOT_PUSHED_RESTARTS"):
            self.assertTrue(name in self.SRC, f"the route never reads {name}")

    def test_the_tag_is_never_restated(self):
        self.assertFalse(TAG in self.SRC, "the route restates the tag literal")

    def test_the_receipt_goes_through_the_act_registry(self):
        self.assertTrue('pipeline_act.receipt("fix-loop-restarted"' in self.SRC)

    def test_no_new_act_is_registered(self):
        # The act's meaning — the sweep re-dispatched the fix agent — is this
        # route's, so no row names it as an emitter and the console in
        # agent-bureau needs nothing new.
        self.assertTrue(self.SRC, "the route does not exist")
        for row in pipeline_act.rows():
            emits = row.get("emits") or []
            for site in emits if isinstance(emits, list) else [emits]:
                if site.get("file") != "scripts/reconcile.py":
                    continue
                self.assertFalse(site.get("anchor", "") in self.SRC,
                                 f"act {row['name']} was registered on this route")


class WiringTest(unittest.TestCase):
    """Called, not merely defined (DRE-2682) — and after the rescue delivery."""

    SRC = (ROOT / "scripts" / "reconcile.py").read_text()

    def main_src(self) -> str:
        return inspect.getsource(reconcile.main)

    def test_main_calls_the_route(self):
        self.assertTrue("redispatch_committed_not_pushed()" in self.main_src(),
                        "main() never calls redispatch_committed_not_pushed")

    def test_it_runs_after_the_rescue_delivery_branch(self):
        src = self.main_src()
        self.assertGreater(
            src.index("redispatch_committed_not_pushed()"),
            src.index("redeliver_rescued_work("),
            "the route runs before redeliver_rescued_work's branch",
        )

    def test_the_operator_page_names_the_fifth_route(self):
        page = (ROOT / "docs" / "held-pr-recovery.md").read_text()
        self.assertTrue("committed-not-pushed:" in page,
                        "docs/held-pr-recovery.md never names the fifth route")


class TheMarkerNoLongerSendsAPersonTest(unittest.TestCase):
    """DRE-6351's marker told a person to restart the loop by hand 'until
    DRE-6352 lands'. It has landed, so the marker says what happens now —
    a person dispatching too would put two fix runs on one branch."""

    def test_the_marker_says_the_sweep_restarts_the_loop_once(self):
        body = marker_body()
        self.assertNotIn("by hand", body)
        self.assertIn(f"has not moved in {WAIT} minutes, the reconcile sweep "
                      "restarts the fix loop once", body)


if __name__ == "__main__":
    unittest.main()
