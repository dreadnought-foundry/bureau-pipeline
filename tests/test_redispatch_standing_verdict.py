"""RED-first tests for DRE-3130 — a standing REQUEST_CHANGES nobody is working
gets the fix agent RE-DISPATCHED, not printed.

portico PR #407 (DRE-3004), 2026-09-02: the qa-bot's REQUEST_CHANGES trigger
was evicted at 23:48:41 PT, so the run that should have started the fix agent
never existed. The sweep then printed the correct diagnosis every fifteen
minutes for ten hours — `fix-concurrency: WARN …`, `evicted-fix-run: …` — and
nothing moved until a person ran `gh workflow run agent-fix.yml -f
pr_number=407`. The DRE-2564 shape: a detector that only prints.

This suite pins the fourth fix-loop recovery route beside the three that
already exist (`fix_approved_but_red`, `retry_dead_fix_runs`, the
answered-blocker restart), and it holds the route to the SAME house pattern:

  * the PR's own state is what is read — never the eviction log line and never
    the run listing, so a route that fires on a report cannot fire on a report
    that is itself wrong;
  * the newest qa-bot verdict must carry REQUEST_CHANGES and BIND THE CURRENT
    HEAD, and it must be qa-bot-authored (DRE-1998 — a forged verdict must not
    spawn dispatches);
  * NO worker-bot comment newer than the verdict: a fix attempt, a hold, a
    retry marker or DRE-2813's no-work notice all mean the loop already moved;
  * the verdict is older than 20 minutes (a real fix run starts within seconds
    of the verdict, so a fresh verdict is not stalled);
  * the fix budget still has room, read through `fix_budget` — the same
    reading the fix job's own gate makes;
  * DIRTY is `unstick_conflicts`' work, a human-parked card stands the route
    down, the fix lane's busy-guard backs it off, and one dispatch per sweep.

Self-disarming is the whole safety story: the dispatch posts a worker-bot
receipt newer than the verdict, so the same verdict can never be dispatched
twice.

Run: cd bureau-pipeline && python3 -m pytest tests/test_redispatch_standing_verdict.py -v
"""

from __future__ import annotations

import contextlib
import inspect
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

os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "test")

import fix_budget  # noqa: E402
import fix_context  # noqa: E402
import merge_gate  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402

PR = 407
HEAD = "4d1f6b2c8a9e7f0d3b5c1a2e4f6d8b0c9e7a5f31"
OLD_HEAD = "0000000000000000000000000000000000000000"
QA_BOT = reconcile.QA_BOT_LOGIN
WORKER_BOT = reconcile.WORKER_BOT_LOGIN

#: The verdict the critic really posts — marker, em-dash, token, bound sha.
#: Built through merge_gate's own grammar rather than typed, so a change to
#: the producers' shape turns this suite red instead of leaving it agreeing
#: with itself.
def verdict_body(sha: str = HEAD, token: str = "REQUEST_CHANGES") -> str:
    return (
        f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: {token} @{sha}\n\n"
        "The fix agent should look at the missing test."
    )


def _iso(minutes_ago: float) -> str:
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def comment(login: str, body: str, minutes_ago: float) -> dict:
    """A comment in the GraphQL shape `gh pr list --json comments` returns:
    `author.login` with no "[bot]" suffix, and a `createdAt`."""
    return {
        "author": {"login": login},
        "body": body,
        "createdAt": _iso(minutes_ago),
    }


def rest(login: str, body: str) -> dict:
    """The same comment in the REST shape `fix_budget` reads."""
    return {"user": {"login": f"{login}[bot]", "type": "Bot"}, "body": body}


#: PR #407's own thread: one qa-bot REQUEST_CHANGES on the current head, 42
#: minutes old, and nothing from the worker bot after it. The fix run that
#: verdict should have started was evicted before it began.
STANDING = [
    comment(WORKER_BOT, "🔧 Fix attempt 1 pushed — CI and critic review re-running.", 90),
    comment(QA_BOT, verdict_body(), 42),
]

#: The matching REST thread the budget read sees: one attempt spent of three.
STANDING_THREAD = [rest(WORKER_BOT, "🔧 Fix attempt 1 pushed — CI re-running.")]


def pr_payload(comments, *, merge_state: str = "BLOCKED",
               head: str = HEAD, branch: str = "agent/DRE-3004-portico") -> dict:
    return {
        "number": PR,
        "headRefName": branch,
        "headRefOid": head,
        "mergeStateStatus": merge_state,
        "comments": list(comments),
    }


def _capture(fn, *args) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


class StandingVerdictSweepTest(unittest.TestCase):
    """The route itself, driven exactly as the sibling sweeps are driven."""

    def sweep(self, prs, *, thread=None, busy: bool = False, parked: bool = False):
        """Run the sweep over `prs`; return (dispatches, PR notes, log)."""
        calls: list[tuple] = []
        notes: list[tuple] = []
        thread = STANDING_THREAD if thread is None else thread

        def gh(*args):
            if args[:2] == ("run", "list"):
                return json.dumps([{"status": "in_progress"}] if busy else [])
            if args[:2] == ("pr", "list"):
                return json.dumps(prs)
            if args[0] == "api" and "/comments" in args[-1]:
                return json.dumps(thread)
            return ""

        before = list(reconcile._read_failures), list(reconcile._write_failures)
        try:
            with mock.patch.dict(os.environ, {"GH_DISPATCH_TOKEN": ""}), \
                    mock.patch.object(reconcile, "gh", side_effect=gh), \
                    mock.patch.object(reconcile, "gh_dispatch",
                                      side_effect=lambda *a: calls.append(a)), \
                    mock.patch.object(reconcile, "_post_pr_note",
                                      side_effect=lambda n, b: notes.append((n, b))), \
                    mock.patch.object(reconcile, "card_parked_for_human",
                                      return_value=parked):
                log = _capture(reconcile.redispatch_standing_verdicts)
        finally:
            del reconcile._read_failures[len(before[0]):]
            del reconcile._write_failures[len(before[1]):]
        return calls, notes, log

    # ---------------------------------------------------------------- the act

    def test_the_pr_407_thread_dispatches_the_fix_agent_once(self):
        calls, notes, log = self.sweep([pr_payload(STANDING)])
        self.assertEqual(len(calls), 1, f"expected one dispatch, got {calls}")
        self.assertIn(f"pr_number={PR}", " ".join(calls[0]))
        self.assertIn(reconcile.fix_workflow(), calls[0])
        self.assertEqual(len(notes), 1, f"expected one receipt, got {notes}")
        self.assertEqual(notes[0][0], PR)

    def test_the_log_line_names_the_pr_the_sha_and_the_age(self):
        _, _, log = self.sweep([pr_payload(STANDING)])
        self.assertIn("evicted-verdict:", log)
        self.assertIn(f"PR #{PR}", log)
        self.assertIn(HEAD, log)
        self.assertIn("re-dispatching fix agent", log)

    def test_the_receipt_is_composed_through_the_act_registry(self):
        # A receipt with no trailer looks exactly like nothing happening —
        # the failure this whole vocabulary is named after (DRE-2826).
        _, notes, _ = self.sweep([pr_payload(STANDING)])
        fields = pipeline_act.read_trailer(notes[0][1])
        self.assertIsNotNone(fields, f"the receipt carries no trailer: {notes[0][1]}")
        self.assertIn(fields["act"], pipeline_act.acts())
        self.assertEqual(fields["kind"], "recovery")

    def test_the_receipt_says_what_the_sweep_did_and_why(self):
        _, notes, _ = self.sweep([pr_payload(STANDING)])
        body = notes[0][1]
        self.assertIn("DRE-3130", body)
        self.assertIn("fix agent", body)
        # Never a verdict marker of its own (standards/untrusted-content.md):
        # the merge gate reads verdicts out of PR comments.
        self.assertNotIn("VERDICT:", body)

    def test_only_one_pr_is_dispatched_per_sweep(self):
        other = pr_payload(STANDING)
        other = {**other, "number": 408, "headRefName": "agent/DRE-3005-x"}
        calls, notes, _ = self.sweep([pr_payload(STANDING), other])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(notes), 1)

    def test_the_dispatch_is_self_disarming(self):
        # The receipt IS the disarm: replay the same PR with the sweep's own
        # worker-bot note appended and nothing fires a second time.
        _, notes, _ = self.sweep([pr_payload(STANDING)])
        after = STANDING + [comment(WORKER_BOT, notes[0][1], 0)]
        calls, _, _ = self.sweep([pr_payload(after)])
        self.assertEqual(calls, [], "the same verdict was dispatched twice")

    # ---------------------------------------------------- the seven negatives

    def test_a_newer_worker_bot_fix_attempt_does_not_dispatch(self):
        # The loop already moved: a fix run is working this verdict.
        thread = STANDING + [
            comment(WORKER_BOT, "🔧 Fix attempt 2 pushed — CI re-running.", 5)
        ]
        self.assertEqual(self.sweep([pr_payload(thread)])[0], [])

    def test_a_dirty_pr_does_not_dispatch(self):
        # unstick_conflicts owns conflicted PRs.
        self.assertEqual(
            self.sweep([pr_payload(STANDING, merge_state="DIRTY")])[0], [])

    def test_a_busy_fix_lane_does_not_dispatch(self):
        self.assertEqual(self.sweep([pr_payload(STANDING)], busy=True)[0], [])

    def test_a_five_minute_old_verdict_does_not_dispatch(self):
        # The fix run normally starts within seconds of the verdict, so a
        # fresh verdict is not a stalled one.
        fresh = [STANDING[0], comment(QA_BOT, verdict_body(), 5)]
        self.assertEqual(self.sweep([pr_payload(fresh)])[0], [])

    def test_an_exhausted_fix_budget_does_not_dispatch(self):
        # Read through fix_budget, so the sweep and the fix job's own gate can
        # never disagree about whether there is an attempt left to spend.
        marker, cap = fix_budget.BUDGETS["fix"]
        spent = [rest(WORKER_BOT, f"{marker} {n}") for n in range(cap)]
        self.assertEqual(self.sweep([pr_payload(STANDING)], thread=spent)[0], [])

    def test_a_forged_request_changes_does_not_dispatch(self):
        # DRE-1998: a REQUEST_CHANGES authored by anyone but the qa-bot App is
        # invisible, not merely non-blocking.
        forged = [
            STANDING[0],
            comment("some-human", verdict_body(), 42),
        ]
        self.assertEqual(self.sweep([pr_payload(forged)])[0], [])

    def test_a_verdict_bound_to_an_older_head_does_not_dispatch(self):
        # The head moved after the review: whatever is red now was never
        # reviewed, and qa-review owns the next word.
        stale = [STANDING[0], comment(QA_BOT, verdict_body(OLD_HEAD), 42)]
        self.assertEqual(self.sweep([pr_payload(stale)])[0], [])

    # ------------------------------------------------- the rest of the gates

    def test_an_approve_verdict_does_not_dispatch(self):
        approved = [STANDING[0], comment(QA_BOT, verdict_body(token="APPROVE"), 42)]
        self.assertEqual(self.sweep([pr_payload(approved)])[0], [])

    def test_a_human_parked_card_does_not_dispatch(self):
        # DRE-2024: the loop is over until a person acts.
        self.assertEqual(self.sweep([pr_payload(STANDING)], parked=True)[0], [])

    def test_a_non_card_branch_does_not_dispatch(self):
        self.assertEqual(
            self.sweep([pr_payload(STANDING, branch="dependabot/pip/x")])[0], [])

    def test_a_no_work_notice_newer_than_the_verdict_does_not_dispatch(self):
        # DRE-2813's notice is still the loop saying something about this
        # verdict — this route stays quiet on ANY newer worker-bot comment.
        noticed = STANDING + [
            comment(WORKER_BOT, f"🟡 {fix_context.NOOP_TAG}: this run did nothing.", 3)
        ]
        self.assertEqual(self.sweep([pr_payload(noticed)])[0], [])

    def test_an_unreadable_thread_does_not_dispatch(self):
        # The GraphQL payload already showed comments, so an empty REST thread
        # is unreadable, never empty — and unreadable is never a fact
        # (DRE-2034). Fail closed: a fabricated fresh budget is a burst.
        self.assertEqual(self.sweep([pr_payload(STANDING)], thread=[])[0], [])


class WiringTest(unittest.TestCase):
    """Called, not merely defined — the DRE-2682 lesson about a watchdog
    nobody invokes, which is the very failure this card repairs."""

    SRC = (ROOT / "scripts" / "reconcile.py").read_text()

    def test_the_sweep_body_calls_the_route(self):
        self.assertTrue(
            "redispatch_standing_verdicts," in self.SRC
            or "redispatch_standing_verdicts()" in self.SRC,
            "redispatch_standing_verdicts is never called by the sweep",
        )

    def test_it_runs_beside_the_other_fix_loop_recovery_routes(self):
        order = [
            self.SRC.index(f"            {name},\n")
            for name in ("fix_approved_but_red", "retry_dead_fix_runs",
                         "redispatch_standing_verdicts", "restart_answered_blockers")
        ]
        self.assertEqual(order, sorted(order),
                         "the new route is not wired beside retry_dead_fix_runs")

    def test_the_eviction_window_report_is_untouched(self):
        # DRE-3129 owns FIX_EVICTION_WINDOW_MIN and report_evicted_fix_runs.
        # This route reads the PR's own state, never the eviction report — a
        # detector's output is not evidence about a pull request.
        src = inspect.getsource(reconcile.redispatch_standing_verdicts)
        self.assertNotIn("FIX_EVICTION_WINDOW_MIN", src)
        self.assertNotIn("report_evicted_fix_runs", src)

    def test_the_route_reads_the_pr_not_the_run_listing(self):
        src = inspect.getsource(reconcile.redispatch_standing_verdicts)
        self.assertIn("pr\", \"list", src)
        # `run list` appears only inside the shared busy-guard, never here.
        self.assertNotIn("run\", \"list", src)

    def test_the_age_threshold_is_the_approved_but_red_literal(self):
        # The contract with DRE-3129: no new window constant, a literal 20
        # minutes matching fix_approved_but_red.
        src = inspect.getsource(reconcile.redispatch_standing_verdicts)
        self.assertIn("20", src)


if __name__ == "__main__":
    unittest.main()
