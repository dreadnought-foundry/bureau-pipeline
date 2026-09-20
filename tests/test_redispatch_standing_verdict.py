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
        # assertFalse, not assertNotIn: a failing assertNotIn on a whole
        # function body dumps it into the report (the house note in
        # test_fix_concurrency_eviction.py).
        src = inspect.getsource(reconcile.redispatch_standing_verdicts)
        for name in ("FIX_EVICTION_WINDOW_MIN", "report_evicted_fix_runs"):
            self.assertFalse(name in src, f"the route reaches for {name}")

    def test_the_route_reads_the_pr_not_the_run_listing(self):
        src = inspect.getsource(reconcile.redispatch_standing_verdicts)
        self.assertTrue("pr\", \"list" in src, "the route never lists the PRs")
        # `run list` appears only inside the shared busy-guard, never here.
        self.assertFalse("run\", \"list" in src, "the route reads the run listing")

    def test_the_operator_page_lists_all_four_recovery_routes(self):
        # A change that contradicts a document updates that document in the
        # SAME PR (standards/engineering.md). The page that tells an operator
        # what the sweep does for a stuck PR now has a fourth answer.
        page = (ROOT / "docs" / "held-pr-recovery.md").read_text()
        for route in ("approved-but-red", "dead-fix-run", "answered-blocker",
                      "standing-verdict", "evicted-verdict:"):
            self.assertTrue(route in page,
                            f"docs/held-pr-recovery.md never names {route}")

    def test_the_age_threshold_is_the_approved_but_red_literal(self):
        # The contract with DRE-3129: no new window constant, a literal 20
        # minutes matching fix_approved_but_red.
        src = inspect.getsource(reconcile.redispatch_standing_verdicts)
        self.assertIn("20", src)



# ==========================================================================
# DRE-4378 — a repo with NO fix agent: the person is told ONCE, and the
# sweep stays green, at all FIVE places that start the fix agent.
# ==========================================================================
#
# bureau-harness #2255 (2026-09-18/19): one held pull request in a sandbox
# that deliberately has no `agent-fix.yml`. `redispatch_standing_verdicts`
# found the standing REQUEST_CHANGES, ran `gh workflow run agent-fix.yml`,
# GitHub answered `HTTP 404: workflow agent-fix.yml not found on the default
# branch`, `gh_dispatch` raised, the sweep recorded a write failure and
# exited 1 — and the receipt that disarms the route is posted only AFTER a
# successful dispatch, so nothing ever stood it down. About 75 consecutive
# failed runs and 75 CEO emails over 18 hours, for one pull request.
#
# DRE-2525 taught the busy-guard that an absent workflow is not an unreadable
# one and left the DISPATCH sites loud on purpose. That was right about the
# problem and wrong about the channel: loud every fifteen minutes with no
# message a person can act on is the same noise arriving by the write path.
#
# What is pinned below, for every one of the five sites and through ONE
# shared helper rather than five copies of one rule:
#
#   * a PROVABLE absence dispatches nothing, tells a person once through the
#     declared `fix-agent-absent` act, and records no write failure;
#   * a second sweep over the same pull request posts nothing and stays green;
#   * an absence that CANNOT be proved (the listing read fails, or comes back
#     empty) behaves exactly as it does today — the dispatch is attempted and
#     a failure is loud;
#   * the workflows listing is read at most once per sweep.

import fix_dead_run  # noqa: E402

#: The stubs a healthy consumer repo carries, and the harness's set — the
#: same shape `gh api repos/<repo>/contents/.github/workflows` answers.
WORKFLOWS_WITH_FIX = ["agent-task.yml", "agent-fix.yml", "qa-review.yml"]
WORKFLOWS_WITHOUT_FIX = ["agent-task.yml", "qa-review.yml"]

ABSENT_PR = 2255
ABSENT_SHA = "b" * 40
ABSENT_BRANCH = "agent/DRE-4378-harness"

QA_REST = f"{QA_BOT}[bot]"
BLOCKER_REST = (
    "🛑 Fix attempt 3 blocked: the critic wants B, the card says A — this "
    "needs the operator's call."
)
DECISION_REST = (
    "**Operator decision — the blocker is answered. Re-arm the fix loop.**"
)


def _rest_comment(login: str, body: str) -> dict:
    """A comment in the REST shape `_pr_thread` returns and fix_context reads."""
    return {
        "user": {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"},
        "body": body,
        "created_at": "2026-09-18T00:00:00Z",
    }


def _absent_pr(comments, sha=ABSENT_SHA, **extra) -> dict:
    """The harness pull request, in the GraphQL shape every site lists."""
    payload = {
        "number": ABSENT_PR,
        "headRefName": ABSENT_BRANCH,
        "headRefOid": sha,
        "mergeStateStatus": "BLOCKED",
        "comments": list(comments),
    }
    payload.update(extra)
    return payload


def _standing_comments(sha=ABSENT_SHA):
    """A standing REQUEST_CHANGES on the current head, 42 minutes old."""
    return [comment(QA_BOT, verdict_body(sha), 42)]


def _approved_comments(sha=ABSENT_SHA):
    return [comment(QA_BOT, verdict_body(sha, token="APPROVE"), 42)]


def _dead_fix_comments():
    return [comment(WORKER_BOT, f"⚡ {fix_dead_run.OUTAGE_TAG}: the fix run died", 42)]


#: The five sites, each with the pull-request state that makes IT dispatch.
#: One table, five rows — the card's "parametrised, not five copies of one
#: test", and the reason the guard is one helper rather than five.
SITES = (
    ("unstick_conflicts", "unstick_conflicts",
     lambda sha=ABSENT_SHA: _absent_pr([], sha, mergeStateStatus="DIRTY"), []),
    ("fix_approved_but_red", "fix_approved_but_red",
     lambda sha=ABSENT_SHA: _absent_pr(_approved_comments(sha), sha), []),
    ("retry_dead_fix_runs", "retry_dead_fix_runs",
     lambda sha=ABSENT_SHA: _absent_pr(_dead_fix_comments(), sha), []),
    ("redispatch_standing_verdicts", "redispatch_standing_verdicts",
     lambda sha=ABSENT_SHA: _absent_pr(_standing_comments(sha), sha),
     [rest(WORKER_BOT, "🔧 Fix attempt 1 pushed — CI re-running.")]),
    ("restart_answered_blockers", "restart_answered_blockers",
     lambda sha=ABSENT_SHA: _absent_pr(
         [comment(WORKER_BOT, BLOCKER_REST, 90)], sha),
     [_rest_comment(reconcile.WORKER_REST_LOGIN, BLOCKER_REST),
      _rest_comment("sid-ceo", DECISION_REST)]),
)


class AbsentFixAgentHarness(unittest.TestCase):
    """One driver for all five sites: same GitHub, same switch, same asserts."""

    def drive(self, route, prs, thread, *, workflows, dispatch=None):
        """Run `route` against a repo whose workflows listing is `workflows`.

        `workflows` is a list of filenames (a listing that READ), or None for
        a listing that could not be read, or [] for one that came back empty.
        Returns (dispatches, notes, log, contents reads, new write failures).
        """
        dispatches: list[tuple] = []
        notes: list[tuple] = []
        contents: list[tuple] = []

        def gh(*args):
            joined = " ".join(args)
            if args[0] == "api" and "/contents/" in joined:
                contents.append(args)
                if workflows is None:
                    return ""
                return json.dumps(
                    [{"name": n, "type": "file"} for n in workflows])
            if args[:2] == ("run", "list"):
                return "[]"
            if args[:2] == ("pr", "list"):
                return json.dumps(prs)
            if args[0] == "api" and "/check-runs" in joined:
                return "2"
            if args[0] == "api" and "/git/commits/" in joined:
                return json.dumps({"committer": {"date": "2026-01-01T00:00:00Z"}})
            if args[0] == "api" and "/comments" in joined:
                return json.dumps(thread)
            return ""

        def dispatcher(*a):
            dispatches.append(a)
            if dispatch is not None:
                dispatch(*a)

        marks = list(reconcile._read_failures), list(reconcile._write_failures)
        reconcile.reset_sweep_cards()
        try:
            with mock.patch.dict(os.environ, {"GH_DISPATCH_TOKEN": ""}), \
                    mock.patch.object(reconcile, "gh", side_effect=gh), \
                    mock.patch.object(reconcile, "gh_dispatch",
                                      side_effect=dispatcher), \
                    mock.patch.object(reconcile, "_post_pr_note",
                                      side_effect=lambda n, b:
                                      notes.append((n, b)) or True), \
                    mock.patch.object(reconcile, "card_parked_for_human",
                                      return_value=False), \
                    mock.patch.object(reconcile, "linear_ops", mock.MagicMock()):
                log = _capture(route)
            failures = reconcile._write_failures[len(marks[1]):]
        finally:
            del reconcile._read_failures[len(marks[0]):]
            del reconcile._write_failures[len(marks[1]):]
            reconcile.reset_sweep_cards()
        return dispatches, notes, log, contents, list(failures)


class FixAgentAbsentHelperTest(AbsentFixAgentHarness):
    """The helper itself — one question, asked once per sweep."""

    def _ask(self, workflows, asks=1, also=()):
        reads: list[tuple] = []

        def gh(*args):
            reads.append(args)
            if workflows is None:
                return ""
            return json.dumps([{"name": n, "type": "file"} for n in workflows])

        reconcile.reset_sweep_cards()
        try:
            with mock.patch.object(reconcile, "gh", side_effect=gh):
                answers = [reconcile.fix_agent_absent() for _ in range(asks)]
                extra = [reconcile.workflow_on_default_branch(w) for w in also]
        finally:
            reconcile.reset_sweep_cards()
        return answers, extra, reads

    def test_a_listing_without_the_fix_stub_proves_the_absence(self):
        answers, _, _ = self._ask(WORKFLOWS_WITHOUT_FIX)
        self.assertEqual(answers, [True])

    def test_a_listing_with_the_fix_stub_proves_nothing_is_absent(self):
        answers, _, _ = self._ask(WORKFLOWS_WITH_FIX)
        self.assertEqual(answers, [False])

    def test_an_unreadable_listing_never_proves_an_absence(self):
        # DRE-2525's discipline, unchanged: unreadable proves NOTHING, so the
        # dispatch goes ahead and its failure is loud.
        self.assertEqual(self._ask(None)[0], [False])

    def test_an_empty_listing_never_proves_an_absence(self):
        # git cannot store an empty directory, so `[]` from a real repo is a
        # failure wearing a success's clothes.
        self.assertEqual(self._ask([])[0], [False])

    def test_the_workflows_listing_is_read_once_per_sweep(self):
        _, _, reads = self._ask(WORKFLOWS_WITHOUT_FIX, asks=4)
        self.assertEqual(len(reads), 1, f"the listing was read {len(reads)} times")

    def test_the_busy_guard_probe_shares_the_sweep_memo(self):
        # The absent repo 404s the Actions read at every site, so the busy
        # guard probes the same listing. One sweep, one read, both readers.
        _, extra, reads = self._ask(WORKFLOWS_WITHOUT_FIX, asks=2,
                                    also=("qa-review.yml", "agent-fix.yml"))
        self.assertEqual(extra, [True, False])
        self.assertEqual(len(reads), 1, f"the listing was read {len(reads)} times")

    def test_a_failed_read_is_never_memoised(self):
        # `_open_pr_listing`'s rule: caching None would turn one transient 403
        # into a silent skip for every later reader in the sweep.
        _, _, reads = self._ask(None, asks=3)
        self.assertEqual(len(reads), 3)


class AbsentFixAgentAtEverySiteTest(AbsentFixAgentHarness):
    """The card's three behaviours, pinned at each of the five sites."""

    def test_a_provable_absence_dispatches_nothing_and_tells_a_person_once(self):
        for label, route, payload, thread in SITES:
            with self.subTest(site=label):
                dispatches, notes, log, _, failures = self.drive(
                    getattr(reconcile, route), [payload()], thread,
                    workflows=WORKFLOWS_WITHOUT_FIX,
                )
                self.assertEqual(dispatches, [], f"{label} ran the fix agent")
                self.assertEqual(len(notes), 1, f"{label} notes: {notes}")
                self.assertEqual(notes[0][0], ABSENT_PR)
                self.assertEqual(failures, [],
                                 f"{label} took the sweep red: {failures}")
                self.assertIn(reconcile.FIX_AGENT_ABSENT_TAG, log)

    def test_the_notice_is_the_declared_act_in_plain_english(self):
        for label, route, payload, thread in SITES:
            with self.subTest(site=label):
                _, notes, _, _, _ = self.drive(
                    getattr(reconcile, route), [payload()], thread,
                    workflows=WORKFLOWS_WITHOUT_FIX,
                )
                body = notes[0][1]
                fields = pipeline_act.read_trailer(body)
                self.assertIsNotNone(fields, f"{label} receipt has no trailer")
                self.assertEqual(fields["kind"], "hold")
                self.assertEqual(
                    pipeline_act.record(fields["act"])["tag"],
                    reconcile.FIX_AGENT_ABSENT_TAG,
                )
                self.assertEqual(
                    pipeline_act.record(fields["act"])["next_actor"], "operator")
                # Plain English a person can act on — and never a verdict
                # marker of its own (standards/untrusted-content.md).
                self.assertIn("no fix agent", body)
                self.assertIn("needs a person", body)
                self.assertNotIn("VERDICT:", body)
                # The idempotency key: the tag AND the head it was said about.
                self.assertIn(reconcile.FIX_AGENT_ABSENT_TAG, body)
                self.assertIn(ABSENT_SHA, body)

    def test_a_second_sweep_posts_nothing_and_stays_green(self):
        for label, route, payload, thread in SITES:
            with self.subTest(site=label):
                _, notes, _, _, _ = self.drive(
                    getattr(reconcile, route), [payload()], thread,
                    workflows=WORKFLOWS_WITHOUT_FIX,
                )
                told = payload()
                told["comments"] = list(told["comments"]) + [
                    comment(WORKER_BOT, notes[0][1], 0)
                ]
                dispatches, again, _, _, failures = self.drive(
                    getattr(reconcile, route), [told], thread,
                    workflows=WORKFLOWS_WITHOUT_FIX,
                )
                self.assertEqual(dispatches, [], f"{label} ran the fix agent")
                self.assertEqual(again, [], f"{label} said it twice: {again}")
                self.assertEqual(failures, [], f"{label} went red: {failures}")

    def test_a_new_head_is_told_again(self):
        # Per (pull request, head sha), like every other receipt this sweep
        # counts: a fresh commit is a fresh hold, not a suppressed one.
        for label, route, payload, thread in SITES:
            with self.subTest(site=label):
                _, notes, _, _, _ = self.drive(
                    getattr(reconcile, route), [payload()], thread,
                    workflows=WORKFLOWS_WITHOUT_FIX,
                )
                # The old hold sits BEHIND the new head's own trigger — a
                # fresh commit brings a fresh verdict or a fresh death
                # marker, and the route reads the newest worker-bot comment.
                moved = payload("c" * 40)
                moved["comments"] = [
                    comment(WORKER_BOT, notes[0][1], 120)
                ] + list(moved["comments"])
                _, again, _, _, _ = self.drive(
                    getattr(reconcile, route), [moved], thread,
                    workflows=WORKFLOWS_WITHOUT_FIX,
                )
                self.assertEqual(len(again), 1, f"{label} stayed silent on a new head")

    def test_an_unprovable_absence_still_dispatches_at_every_site(self):
        # The non-vacuous twin, and the DRE-2525 line that must not move: an
        # unreadable or empty listing proves nothing, so behaviour is today's.
        for label, route, payload, thread in SITES:
            for listing, why in ((None, "unreadable"), ([], "empty")):
                with self.subTest(site=label, listing=why):
                    dispatches, notes, _, _, _ = self.drive(
                        getattr(reconcile, route), [payload()], thread,
                        workflows=listing,
                    )
                    self.assertEqual(len(dispatches), 1,
                                     f"{label} stopped dispatching on an "
                                     f"{why} listing")
                    self.assertIn(reconcile.fix_workflow(), dispatches[0])
                    self.assertEqual(
                        [n for n in notes
                         if reconcile.FIX_AGENT_ABSENT_TAG in n[1]], [],
                        f"{label} claimed an absence it cannot prove")

    def test_a_present_fix_agent_still_dispatches_at_every_site(self):
        for label, route, payload, thread in SITES:
            with self.subTest(site=label):
                dispatches, _, _, _, _ = self.drive(
                    getattr(reconcile, route), [payload()], thread,
                    workflows=WORKFLOWS_WITH_FIX,
                )
                self.assertEqual(len(dispatches), 1,
                                 f"{label} did not dispatch a present fix agent")

    def test_an_unprovable_absence_keeps_the_dispatch_failure_loud(self):
        # The 404 the harness really got. Nothing here swallows it: the route
        # lets ReconcileWriteError out, main() records it, the sweep exits 1.
        def boom(*a):
            raise reconcile.ReconcileWriteError(
                "gh workflow run agent-fix.yml failed rc=1: HTTP 404: workflow "
                "agent-fix.yml not found on the default branch")

        for label, route, payload, thread in SITES:
            with self.subTest(site=label):
                with self.assertRaises(reconcile.ReconcileWriteError):
                    self.drive(getattr(reconcile, route), [payload()], thread,
                               workflows=None, dispatch=boom)


class AbsentFixAgentSweepCostTest(AbsentFixAgentHarness):
    """One sweep, five sites, ONE read of the workflows listing."""

    def test_five_sites_in_one_sweep_read_the_listing_once(self):
        contents: list[tuple] = []
        prs = [payload() for _, _, payload, _ in SITES]
        # One pull request per site, each with the number its row expects to
        # find; the sites filter the listing themselves.
        for index, pr in enumerate(prs):
            pr["number"] = ABSENT_PR + index
            pr["headRefName"] = f"agent/DRE-437{index}-harness"
        thread = [_rest_comment(reconcile.WORKER_REST_LOGIN, BLOCKER_REST),
                  _rest_comment("sid-ceo", DECISION_REST)]

        def gh(*args):
            joined = " ".join(args)
            if args[0] == "api" and "/contents/" in joined:
                contents.append(args)
                return json.dumps([{"name": n, "type": "file"}
                                   for n in WORKFLOWS_WITHOUT_FIX])
            if args[:2] == ("run", "list"):
                return "[]"
            if args[:2] == ("pr", "list"):
                return json.dumps(prs)
            if args[0] == "api" and "/check-runs" in joined:
                return "2"
            if args[0] == "api" and "/git/commits/" in joined:
                return json.dumps({"committer": {"date": "2026-01-01T00:00:00Z"}})
            if args[0] == "api" and "/comments" in joined:
                return json.dumps(thread)
            return ""

        marks = list(reconcile._read_failures), list(reconcile._write_failures)
        reconcile.reset_sweep_cards()
        try:
            with mock.patch.dict(os.environ, {"GH_DISPATCH_TOKEN": ""}), \
                    mock.patch.object(reconcile, "gh", side_effect=gh), \
                    mock.patch.object(reconcile, "gh_dispatch"), \
                    mock.patch.object(reconcile, "_post_pr_note",
                                      return_value=True), \
                    mock.patch.object(reconcile, "card_parked_for_human",
                                      return_value=False), \
                    mock.patch.object(reconcile, "linear_ops", mock.MagicMock()):
                for _, route, _, _ in SITES:
                    _capture(getattr(reconcile, route))
        finally:
            del reconcile._read_failures[len(marks[0]):]
            del reconcile._write_failures[len(marks[1]):]
            reconcile.reset_sweep_cards()
        self.assertEqual(len(contents), 1,
                         f"the workflows listing was read {len(contents)} "
                         "times in one sweep")


class AbsentFixAgentWiringTest(unittest.TestCase):
    """ONE helper, called at all five sites — not five copies of one rule."""

    SRC = (ROOT / "scripts" / "reconcile.py").read_text()

    SITE_FUNCTIONS = (
        "_dispatch_conflict_fix",      # unstick_conflicts' per-PR dispatch
        "fix_approved_but_red",
        "retry_dead_fix_runs",
        "redispatch_standing_verdicts",
        "restart_answered_blockers",
    )

    def test_every_dispatch_site_asks_the_shared_helper(self):
        for name in self.SITE_FUNCTIONS:
            with self.subTest(site=name):
                src = inspect.getsource(getattr(reconcile, name))
                self.assertTrue(
                    "fix_agent_absent_hold" in src,
                    f"{name} dispatches the fix agent without asking whether "
                    "this repo has one",
                )

    def test_no_site_carries_its_own_copy_of_the_rule(self):
        # The card's one deliverable: the absence is decided in one place, so
        # fixing it at a fifth site is two lines and not a fifth reading.
        for name in self.SITE_FUNCTIONS:
            with self.subTest(site=name):
                src = inspect.getsource(getattr(reconcile, name))
                self.assertFalse(
                    "workflow_on_default_branch" in src,
                    f"{name} re-derives the absence instead of asking the helper",
                )

    def test_the_busy_guard_docstring_says_what_is_now_true(self):
        doc = reconcile._actions_runs_busy.__doc__ or ""
        self.assertNotIn("Deliberately NOT extended to the dispatch sites", doc)
        self.assertIn("DRE-4378", doc)
        # DRE-2525's numbers stay beside the new incident's.
        for number in ("61", "18h37m", "DRE-2525", "75"):
            self.assertIn(number, doc,
                          f"the docstring no longer names {number}")

    def test_the_act_is_declared_with_the_contract_the_console_holds(self):
        row = next(a for a in pipeline_act.rows()
                   if a["tag"] == reconcile.FIX_AGENT_ABSENT_TAG)
        self.assertEqual(row["kind"], "hold")
        self.assertEqual(row["next_actor"], "operator")
        self.assertIsNone(row["cadence_s"], "a hold waits on a person, not a clock")

    def test_the_operator_page_names_the_no_fix_agent_answer(self):
        # A change that contradicts a document updates it in the SAME PR.
        page = (ROOT / "docs" / "held-pr-recovery.md").read_text()
        # assertTrue, not assertIn: a failing assertIn on a whole page dumps
        # it into the report (the house note in test_fix_concurrency_eviction).
        self.assertTrue(
            reconcile.FIX_AGENT_ABSENT_TAG in page,
            "docs/held-pr-recovery.md never names the no-fix-agent answer",
        )


if __name__ == "__main__":
    unittest.main()
