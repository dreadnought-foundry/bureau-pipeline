"""RED-first tests for DRE-2810 — the fix loop must not evict its own next attempt.

A PR with a standing REQUEST_CHANGES sat 24 minutes with no fix agent working
it, and every run in the sequence reported an honest conclusion. Observed live
on PR #199 (DRE-2721) on 2026-08-29, read from the run records this suite keeps
as `tests/fixtures/agent-fix-runs-2026-08-29.json`:

    03:26:47  Agent Fix queued from a REQUEST_CHANGES verdict — PENDING,
              because the 03:02 fix run is still finishing
    03:32:16  the 03:02 run posts "🔧 Fix attempt 2 pushed" and succeeds —
              that comment is an issue_comment event on the same PR, so it
              queues an Agent Fix run in the SAME concurrency group
    03:32:17  the 03:26 run is CANCELLED — GitHub keeps at most ONE pending
              run per group, and the newly queued run evicts it
    03:32:18  the evicting run SKIPS: wrong author, no verdict in the body

The skip happens at the JOB level, and the run has already claimed the
concurrency slot by then. A run that will do nothing evicted the one that
would have done the work. No run failed, so no medic, no red-main repair, no
alarm — the only symptom is a `cancelled` Agent Fix run, which is also the
signature of a harmless duplicate dispatch.

The rule these tests express:

  1. A stub's concurrency group keys on the COMMENTER as well as the PR, so a
     bot notice and a qa-bot verdict queue in different groups and neither can
     evict the other. `cancel-in-progress` stays false.
  2. The verdict-triggered run survives the notice AND still reaches the fix
     agent (the reusable workflow's job gate admits it) — the whole point is
     the work, not merely the run.
  3. Two REQUEST_CHANGES verdicts in a row on one PR each reach the fix agent.
  4. EVERY agent-fix stub in this repo carries the grouping — audited by
     scanning the workflows directory, not by naming one file from memory.
  5. An Agent Fix run cancelled before it started a single job is reported,
     and a lost verdict trigger is told apart from a duplicate dispatch.
  6. That report remembers LONGER THAN THE STALL it exists to catch, and the
     run listing it reads is deep enough to hold the whole window (DRE-3129).

The group and the job gate are LIVE-EXTRACTED from the shipped YAML and
evaluated as GitHub would evaluate them (scripts/fix_concurrency.py), so a
revert of either turns this suite red rather than passing against a copy.

Run: cd bureau-pipeline && python3 -m pytest tests/test_fix_concurrency_eviction.py -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
FIXTURE = ROOT / "tests" / "fixtures" / "agent-fix-runs-2026-08-29.json"

sys.path.insert(0, str(ROOT / "scripts"))

import fix_concurrency as fc  # noqa: E402

PR = 199
VERDICT_BODY = (
    "## QA verdict\n\nVERDICT: REQUEST_CHANGES\n\nThe head sha is 47ef2102."
)
NOTICE_BODY = "🔧 Fix attempt 2 pushed — CI and critic review re-running."

# The group every agent-fix stub shipped until DRE-2810 — the one whose
# eviction this card is about. Kept here so the tests below can show the bug
# happening, not just the fix holding.
PRE_FIX_GROUP = "agent-fix-${{ github.event.issue.number || inputs.pr_number }}"


def _doc(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _capture(fn, *args) -> str:
    """Run a sweep report and return what it said. The reports print — the
    house pattern for a KPI that must be visible without anyone remembering
    to look (report_break_glass, report_epic_growth)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


STUB = _doc("self-agent-fix.yml")
REUSABLE = _doc("agent-fix.yml")

VERDICT = fc.comment_event(PR, fc.QA_BOT_LOGIN, VERDICT_BODY)
NOTICE = fc.comment_event(PR, fc.WORKER_BOT_LOGIN, NOTICE_BODY)
DISPATCH = fc.dispatch_event(PR)


class StubGroupingTest(unittest.TestCase):
    """The narrowest fix: the group keys on the commenter as well as the PR."""

    def test_group_keys_on_the_pr_and_the_commenter(self):
        group = fc.group_of(STUB)
        self.assertIsNotNone(group, "self-agent-fix.yml has no concurrency group")
        self.assertIn("github.event.issue.number", group)
        self.assertIn(fc.COMMENTER_KEY, group)

    def test_cancel_in_progress_stays_false(self):
        # Grouping runs apart is the fix; cancelling a LIVE fix agent
        # mid-work would be a new and worse bug.
        self.assertIs(fc.cancel_in_progress(STUB), False)

    def test_a_hand_dispatch_still_resolves_to_a_group(self):
        # workflow_dispatch carries no comment: the group must still be a
        # stable string, not "agent-fix-199-".
        self.assertEqual(fc.group(STUB, DISPATCH), f"agent-fix-{PR}-work")

    def test_a_bot_notice_gets_a_lane_of_its_own(self):
        self.assertEqual(
            fc.group(STUB, NOTICE), f"agent-fix-{PR}-{fc.WORKER_BOT_LOGIN}")

    def test_the_audit_passes_the_shipped_stub(self):
        self.assertEqual(fc.audit(STUB, name="self-agent-fix.yml"), [])


class EvictionScenarioTest(unittest.TestCase):
    """The live PR #199 sequence, driven directly."""

    def test_the_pre_fix_group_is_what_evicted_the_pending_run(self):
        # Guard the guard: with the group that shipped until DRE-2810 the
        # verdict trigger and the bot notice land in ONE group, which is
        # exactly how GitHub cancelled the pending run. If this ever stops
        # being true the tests below prove nothing.
        self.assertEqual(
            fc.resolve_group(PRE_FIX_GROUP, VERDICT),
            fc.resolve_group(PRE_FIX_GROUP, NOTICE),
        )

    def test_the_notice_cannot_evict_the_pending_verdict_run(self):
        # THE card's acceptance criterion, against the shipped stub.
        self.assertFalse(
            fc.evicts(STUB, pending=VERDICT, arriving=NOTICE),
            "a 🔧 Fix attempt notice still shares the verdict run's "
            "concurrency group — GitHub will cancel the pending run",
        )

    def test_the_surviving_verdict_run_still_reaches_the_fix_agent(self):
        # Surviving is only half of it: the run must still pass the reusable
        # workflow's identity gate and start the fix agent.
        self.assertTrue(fc.reaches_fix_agent(REUSABLE, VERDICT))

    def test_the_notice_run_still_does_nothing(self):
        # The evictor was harmless-by-design and stays that way: it is
        # skipped at the job gate. Only its concurrency slot was the problem.
        self.assertFalse(fc.reaches_fix_agent(REUSABLE, NOTICE))

    def test_a_dispatch_shares_the_verdict_lane_so_two_agents_never_race(self):
        # Deliberately NOT separated. merge-gate routes a merge conflict to
        # this workflow by workflow_dispatch and does it BEFORE it looks at
        # the verdict (merge_gate.evaluate_conflict), so a conflicted PR that
        # draws REQUEST_CHANGES fires both triggers at once. In one lane they
        # serialize, as they always have. In two they would put two fix
        # agents on one branch, and the loser's push would report "no new
        # commit" and park the card for a human.
        self.assertTrue(fc.evicts(STUB, pending=VERDICT, arriving=DISPATCH))
        self.assertEqual(fc.group(STUB, VERDICT), fc.group(STUB, DISPATCH))
        self.assertTrue(fc.reaches_fix_agent(REUSABLE, DISPATCH))

    def test_two_verdicts_in_a_row_each_reach_the_fix_agent(self):
        # The case that failed on #199: the critic returns REQUEST_CHANGES
        # while a fix run is still finishing, twice running. Both verdicts
        # share a group (they are the same work, serialized behind
        # cancel-in-progress: false) and both pass the gate.
        second = fc.comment_event(PR, fc.QA_BOT_LOGIN, VERDICT_BODY + " attempt 3")
        self.assertEqual(fc.group(STUB, VERDICT), fc.group(STUB, second))
        self.assertIs(fc.cancel_in_progress(STUB), False)
        for event in (VERDICT, second):
            self.assertTrue(fc.reaches_fix_agent(REUSABLE, event))

    def test_a_comment_on_another_pr_never_shared_the_group(self):
        # Sanity on the PR key itself — the group must stay per-PR.
        other = fc.comment_event(197, fc.QA_BOT_LOGIN, VERDICT_BODY)
        self.assertNotEqual(fc.group(STUB, VERDICT), fc.group(STUB, other))


class FleetStubAuditTest(unittest.TestCase):
    """Checked rather than remembered: every stub in the tree, every run."""

    def test_every_agent_fix_stub_in_this_repo_is_clean(self):
        problems = fc.audit_workflows_dir(WORKFLOWS)
        self.assertTrue(problems, "no agent-fix stub found to audit")
        self.assertEqual(
            {name: p for name, p in problems.items() if p}, {},
            "an agent-fix stub can still evict its own next attempt",
        )

    def test_the_audit_finds_a_stub_that_lacks_the_grouping(self):
        # The audit has to be able to FAIL, or running it everywhere proves
        # nothing. The pre-DRE-2810 group is the worked example.
        drifted = json.loads(json.dumps(STUB))
        drifted["concurrency"]["group"] = PRE_FIX_GROUP
        problems = fc.audit(drifted, name="agent-fix.yml")
        self.assertTrue(problems)
        self.assertTrue(
            any(fc.COMMENTER_KEY in p for p in problems),
            f"the problem must name the missing key, got {problems}",
        )

    def test_the_audit_finds_a_stub_that_splits_the_work_lane(self):
        # The first expression this card considered — keyed on the commenter
        # alone — fixes the eviction and breaks the serialization: a
        # workflow_dispatch lands in its own lane and can start a second fix
        # agent on a branch one is already working.
        split = json.loads(json.dumps(STUB))
        split["concurrency"]["group"] = (
            PRE_FIX_GROUP + "-${{ github.event.comment.user.login || 'dispatch' }}"
        )
        problems = fc.audit(split, name="agent-fix.yml")
        self.assertTrue(
            any("workflow_dispatch" in p for p in problems),
            f"a split work lane must be a finding, got {problems}",
        )

    def test_the_audit_rejects_cancel_in_progress(self):
        risky = json.loads(json.dumps(STUB))
        risky["concurrency"]["cancel-in-progress"] = True
        self.assertTrue(fc.audit(risky, name="agent-fix.yml"))

    def test_the_cli_audits_a_directory_and_exits_nonzero_on_drift(self):
        clean = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "fix_concurrency.py"),
             "audit", str(WORKFLOWS)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        self.assertIn("self-agent-fix.yml", clean.stdout)


class CancelledRunReportTest(unittest.TestCase):
    """A run cancelled before it started is reported, and told apart from a
    harmless duplicate dispatch — read off the real 2026-08-29 run records."""

    def setUp(self):
        self.runs = json.loads(FIXTURE.read_text())
        self.by_id = {r["id"]: r for r in self.runs}

    def test_the_evicted_verdict_run_is_a_lost_trigger(self):
        run = self.by_id[33231413617]  # queued 03:26:47, cancelled 03:32:17
        self.assertTrue(fc.is_cancelled(run))
        self.assertTrue(fc.never_started(0))  # GitHub listed ZERO jobs for it
        self.assertEqual(fc.trigger_kind(run), fc.TRIGGER_VERDICT)

    def test_the_report_names_the_run_that_took_the_slot(self):
        run = self.by_id[33231413617]
        evictor = fc.evictor_of(run, self.runs)
        self.assertIsNotNone(evictor, "the evicting run was not identified")
        self.assertEqual(evictor["id"], 33231646316)
        line = fc.eviction_report(run, evictor)
        self.assertIn("33231413617", line)
        self.assertIn("33231646316", line)
        self.assertIn(fc.WORKER_BOT_LOGIN, line)
        self.assertIn("DRE-2810", line)

    def test_the_sibling_pr_lost_a_verdict_trigger_the_same_way(self):
        # #197 (DRE-2682) an hour earlier: the card read its cancelled run as
        # the harmless duplicate-dispatch signature. The records say the same
        # eviction — a qa-bot verdict trigger cancelled the second a worker-bot
        # notice queued (02:56:14 queued, 02:56:15 cancelled).
        run = self.by_id[33230096718]
        self.assertEqual(fc.trigger_kind(run), fc.TRIGGER_VERDICT)
        evictor = fc.evictor_of(run, self.runs)
        self.assertEqual(evictor["id"], 33230150506)

    def test_a_cancelled_hand_dispatch_is_not_a_lost_verdict(self):
        run = self.by_id[33229394509]  # duplicate `gh workflow run` dispatches
        self.assertTrue(fc.is_cancelled(run))
        self.assertEqual(fc.trigger_kind(run), fc.TRIGGER_DISPATCH)

    def test_a_cancelled_notice_run_is_not_a_lost_verdict(self):
        run = self.by_id[33230448792]  # the worker bot's own notice, evicted
        self.assertTrue(fc.is_cancelled(run))
        self.assertEqual(fc.trigger_kind(run), fc.TRIGGER_NOTICE)

    def test_a_run_that_started_jobs_is_not_an_eviction(self):
        # Cancelled WHILE RUNNING is a different event (a timeout, an
        # operator cancel) and must not be reported as a dropped trigger.
        self.assertFalse(fc.never_started(1))

    def test_an_unreadable_job_count_is_not_read_as_never_started(self):
        # Unreadable is never a fact (the DRE-2034 discipline).
        self.assertFalse(fc.never_started(None))

    def test_a_successful_run_is_never_reported(self):
        self.assertFalse(fc.is_cancelled(self.by_id[33230398428]))


os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "test")

import reconcile  # noqa: E402


class SweepStubAuditTest(unittest.TestCase):
    """The stub audit rides the sweep that already runs, in EVERY repo — the
    only way "every stub in the fleet carries the grouping" is checked rather
    than remembered, since each stub lives in its own repository."""

    def test_the_sweep_passes_this_repos_own_stub(self):
        out = _capture(reconcile.report_fix_concurrency, WORKFLOWS)
        self.assertIn("self-agent-fix.yml", out)
        self.assertNotIn("WARN", out)

    def test_the_sweep_warns_when_a_stub_lacks_the_grouping(self):
        with tempfile.TemporaryDirectory() as tmp:
            drifted = json.loads(json.dumps(STUB))
            drifted["concurrency"]["group"] = PRE_FIX_GROUP
            Path(tmp, "agent-fix.yml").write_text(yaml.safe_dump(drifted))
            out = _capture(reconcile.report_fix_concurrency, tmp)
        self.assertIn("WARN", out)
        self.assertIn("agent-fix.yml", out)
        self.assertIn("DRE-2810", out)

    def test_a_repo_with_no_fix_stub_is_not_a_finding(self):
        # bureau-harness has no agent-fix stub (DRE-2525): absence is a third
        # answer, never a failure.
        with tempfile.TemporaryDirectory() as tmp:
            out = _capture(reconcile.report_fix_concurrency, tmp)
        self.assertNotIn("WARN", out)


class SweepEvictionReportTest(unittest.TestCase):
    """A cancelled-while-pending Agent Fix run is named in the sweep log."""

    def setUp(self):
        self.runs = json.loads(FIXTURE.read_text())

    def _sweep(self, reads):
        # `now` an hour after the sequence: the window is a parameter, so this
        # test does not rot as the fixture ages (the age_minutes pattern).
        # _actions_read, not gh_actions_read: the sweep must adjudicate a
        # missing stub BEFORE the failure is recorded (DRE-2525).
        before = list(reconcile._read_failures)
        try:
            with mock.patch.object(reconcile, "_actions_read", side_effect=reads), \
                    mock.patch.object(
                        reconcile, "workflow_on_default_branch", return_value=True):
                return _capture(
                    reconcile.report_evicted_fix_runs, "2026-08-29T04:00:00Z")
        finally:
            del reconcile._read_failures[len(before):]

    def _reads(self, jobs="0"):
        def read(args):
            if "/jobs" in args[1]:
                return jobs, None
            return json.dumps(self.runs), None
        return read

    def test_the_lost_verdict_trigger_is_reported(self):
        out = self._sweep(self._reads())
        self.assertIn("33231413617", out)
        self.assertIn("DRE-2810", out)

    def test_duplicate_dispatches_are_not_reported_as_lost_verdicts(self):
        out = self._sweep(self._reads())
        for line in out.splitlines():
            if "33229394509" in line:  # a cancelled duplicate hand dispatch
                self.assertNotIn("REQUEST_CHANGES", line)

    def test_a_run_that_started_a_job_is_not_reported(self):
        out = self._sweep(self._reads(jobs="1"))
        self.assertNotIn("33231413617", out)

    def test_an_unreadable_listing_never_reads_as_nothing_evicted(self):
        out = self._sweep(lambda args: (None, "rc=1: HTTP 403"))
        self.assertIn("unknown", out.lower())
        self.assertNotIn("0 verdict trigger(s)", out)

    def test_both_reports_run_on_every_full_sweep(self):
        src = (ROOT / "scripts" / "reconcile.py").read_text()
        # Called, not merely defined — the DRE-2682 lesson about a watchdog
        # nobody invokes. (assertTrue, not assertIn: a failing assertIn on a
        # 4,000-line source dumps the whole file into the report.)
        for call in ("report_fix_concurrency()", "report_evicted_fix_runs()"):
            self.assertTrue(call in src, f"{call} is never called by the sweep")


# --------------------------------------------------------------------------
# DRE-3129 — the detector's memory has to outlast the stall it exists to catch
# --------------------------------------------------------------------------

#: portico PR #407 (DRE-3004). The qa-bot's REQUEST_CHANGES trigger was evicted
#: at 23:49 PT on 2026-09-02 — 06:49Z the next morning — and the PR then sat
#: ten hours. `NOW` is ten hours after the eviction: still inside the stall,
#: and long past the 180 minutes the report shipped with, which is why for
#: seven of those hours the sweep printed `0 verdict trigger(s)` while the only
#: other signal was the generic `fix-concurrency: WARN`.
NOW = "2026-09-03T16:49:00Z"
LOST_RUN = 34_070_407_001  # the evicted verdict trigger
EVICTOR_RUN = 34_070_407_002  # the notice run that took its slot


def _at(hours: float, seconds: float = 0.0, now: str = NOW) -> str:
    when = (datetime.fromisoformat(now.replace("Z", "+00:00"))
            - timedelta(hours=hours, seconds=seconds))
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(run_id: int, hours_before: float, seconds_before: float = 0.0, *,
         actor: str = fc.QA_BOT_LOGIN, conclusion: str = "cancelled",
         event: str = "issue_comment") -> dict:
    """One Agent Fix run record in the shape `RUNS_JQ` hands the sweep."""
    when = _at(hours_before, seconds_before)
    return {
        "id": run_id,
        "event": event,
        "status": "completed",
        "conclusion": conclusion,
        "created_at": when,
        "updated_at": when,
        "actor": actor,
        "display_title": "QA verdict: REQUEST_CHANGES",
        "html_url": (
            f"https://github.com/dreadnought-foundry/portico/actions/runs/{run_id}"),
    }


class _FakeActions:
    """The two Actions reads this report makes, answered off ONE newest-first
    sequence of runs and paged the way GitHub pages it.

    Deliberately not a list of pre-split pages: the sweep chooses `per_page`,
    and a fake that ignored it would let a listing depth that misses runs look
    complete. It records what was asked, so a test can hold the sweep to its
    cost as well as to its answer.
    """

    def __init__(self, runs: list[dict], jobs: str = "0"):
        self.runs = runs
        self.jobs = jobs
        self.listing_reads: list[tuple[int, int]] = []  # (page, per_page)
        self.jobs_reads: list[int] = []

    def __call__(self, args):
        url = args[1]
        job = re.search(r"/actions/runs/(\d+)/jobs", url)
        if job:
            self.jobs_reads.append(int(job.group(1)))
            return self.jobs, None
        per_page = int(re.search(r"[?&]per_page=(\d+)", url).group(1))
        page_match = re.search(r"[?&]page=(\d+)", url)
        page = int(page_match.group(1)) if page_match else 1
        self.listing_reads.append((page, per_page))
        start = (page - 1) * per_page
        return json.dumps(self.runs[start:start + per_page]), None


class EvictionWindowTest(unittest.TestCase):
    """The window outlasts the stall, and the listing covers the window.

    Both halves are ONE decision (DRE-3129): a 24-hour window read off a
    30-run listing is the same blindness the 180-minute window had, wearing a
    bigger number — on a busy repo 30 Agent Fix runs is a few hours.
    """

    def _sweep(self, actions, now: str = NOW) -> str:
        before = list(reconcile._read_failures)
        try:
            with mock.patch.object(
                    reconcile, "_actions_read", side_effect=actions), \
                    mock.patch.object(
                        reconcile, "workflow_on_default_branch", return_value=True):
                return _capture(reconcile.report_evicted_fix_runs, now)
        finally:
            del reconcile._read_failures[len(before):]

    def _across_the_window(self, count: int = 300) -> list[dict]:
        """`count` uneventful runs spread newest-first across the window — a
        busy repo's ordinary traffic, and the reason 30 runs is not a day."""
        span = reconcile.FIX_EVICTION_WINDOW_MIN / 60
        return [
            _run(9_000_000 + n, hours_before=span * n / count,
                 actor=fc.WORKER_BOT_LOGIN, conclusion="success")
            for n in range(count)
        ]

    def test_a_ten_hour_old_eviction_is_still_named(self):
        # THE card's criterion, and PR #407's own timeline: at ten hours the
        # stall was still running and the report had already forgotten it.
        lost = _run(LOST_RUN, hours_before=10)
        evictor = _run(EVICTOR_RUN, hours_before=10, seconds_before=-1,
                       actor=fc.WORKER_BOT_LOGIN, conclusion="skipped")
        out = self._sweep(_FakeActions([evictor, lost]))
        self.assertIn(str(LOST_RUN), out,
                      "a verdict trigger evicted ten hours ago was not named")
        self.assertIn(str(EVICTOR_RUN), out)
        self.assertIn("DRE-2810", out)
        self.assertIn("1 verdict trigger(s)", out)

    def test_the_window_covers_a_full_day(self):
        self.assertGreaterEqual(
            reconcile.FIX_EVICTION_WINDOW_MIN, 1440,
            "the eviction window must remember a stall of a full day",
        )

    def test_the_window_is_still_bounded(self):
        # Bounded on purpose: the jobs read is paid per cancelled run in the
        # window, so an unbounded memory is an unbounded sweep. A day is the
        # decision; 25 hours is outside it.
        out = self._sweep(_FakeActions([_run(LOST_RUN, hours_before=25)]))
        self.assertNotIn(str(LOST_RUN), out)
        self.assertIn("0 verdict trigger(s)", out)

    def test_the_summary_line_keeps_its_prefix_and_its_shape(self):
        # The contract the re-dispatch follow-up reads: only <N> changes.
        out = self._sweep(_FakeActions([]))
        self.assertRegex(
            out,
            r"(?m)^evicted-fix-run: \d+ verdict trigger\(s\) and \d+ no-op "
            r"trigger\(s\) were cancelled before starting in the last "
            rf"{reconcile.FIX_EVICTION_WINDOW_MIN} minutes\.$",
        )

    def test_a_run_at_the_far_edge_of_the_window_is_inside_the_listing(self):
        # The two cannot drift: a run half an hour inside the window's far
        # edge, under 300 runs of ordinary traffic, is still read.
        edge = reconcile.FIX_EVICTION_WINDOW_MIN / 60 - 0.5
        lost = _run(LOST_RUN, hours_before=edge)
        stale = _run(EVICTOR_RUN, hours_before=edge + 6)  # past the window
        actions = _FakeActions(self._across_the_window() + [lost, stale])
        out = self._sweep(actions)
        self.assertIn(str(LOST_RUN), out,
                      "the run listing does not reach the edge of the window")
        self.assertNotIn(str(EVICTOR_RUN), out)
        self.assertEqual(
            actions.jobs_reads, [LOST_RUN],
            "the per-run jobs read is paid only for cancelled runs inside the "
            "window — the age gate runs before it",
        )

    def test_the_listing_depth_follows_the_window(self):
        # Widening the window widens the listing by itself. A depth pinned
        # beside the window is what made 180-over-30 and 1440-over-30 the
        # same detector.
        listing = self._across_the_window()
        deep = _FakeActions(listing)
        self._sweep(deep)
        with mock.patch.object(reconcile, "FIX_EVICTION_WINDOW_MIN", 180):
            shallow = _FakeActions(listing)
            self._sweep(shallow)
        self.assertGreater(
            len(deep.listing_reads), len(shallow.listing_reads),
            "the listing read the same depth for a 3-hour and a 24-hour "
            "window, so one of them does not cover its window",
        )

    def test_a_listing_that_never_reaches_the_window_edge_says_so(self):
        # More runs inside the window than the sweep will ever read. The read
        # stays bounded, and a bounded read that did not cover the window is
        # UNKNOWN — never "nothing was evicted" (the DRE-2034 discipline).
        flood = [
            _run(9_500_000 + n, hours_before=0.0, actor=fc.WORKER_BOT_LOGIN,
                 conclusion="success")
            for n in range(5_000)
        ]
        actions = _FakeActions(flood)
        out = self._sweep(actions)
        self.assertIn("UNKNOWN", out)
        self.assertLessEqual(
            len(actions.listing_reads), reconcile.FIX_RUNS_MAX_PAGES,
            "widening the window must not make one sweep an unbounded read",
        )

    def test_the_jobs_endpoint_is_read_only_for_cancelled_runs(self):
        runs = [
            _run(9_600_000 + n, hours_before=float(n),
                 actor=fc.WORKER_BOT_LOGIN, conclusion="success")
            for n in range(20)
        ]
        runs.insert(5, _run(LOST_RUN, hours_before=5.0))
        actions = _FakeActions(runs)
        self._sweep(actions)
        self.assertEqual(actions.jobs_reads, [LOST_RUN])


if __name__ == "__main__":
    unittest.main()
