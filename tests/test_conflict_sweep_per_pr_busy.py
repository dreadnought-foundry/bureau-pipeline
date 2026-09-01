"""RED-first tests for the per-PR conflict busy guard (DRE-2908).

Measured live on agent-bureau PR #2206, 2026-09-01: a conflicted PR carrying
an APPROVING verdict sat 1h40m and would not have moved on its own.

    01:16  "🛑 Fix attempt 1 pushed no new commit (branch still at 847c5dcd)"
    01:16 → 02:57  every sweep: "conflict sweep: fix agent busy — retry next
                   sweep"
    02:57  hand dispatch

Every agent behaved correctly. The defect is one line: `unstick_conflicts`
opened with `_actions_runs_busy(fix_workflow())`, which asks "is ANY run of
Agent Fix queued or in progress, anywhere in this repo?" — while
`_dispatch_conflict_fix` dispatches per PR number. A fix run on PR A therefore
blocked the sweep from ever reaching conflicted PR B, and the honest log line
never said which PR was the one being starved. The sibling PR #2207 resolved
itself only because the lane happened to clear: luck, not state.

What this file pins:

  * a conflicted PR is dispatched while a fix run works a DIFFERENT PR;
  * a fix run in flight FOR THAT PR still suppresses a second dispatch, so
    two agents never push one branch;
  * an unreadable Actions read still answers BUSY and defers the WHOLE
    sweep — the quota protection from the 2026-06-28 App-burn, untouched;
  * every deferral log line names the PR it is deferring for;
  * `fix_dispatch_blocked`'s human-park gate (DRE-2024) still runs per PR;
  * unattributable in-flight runs are CAPPED, never blocking — one busy
    branch cannot starve the queue indefinitely.

Attribution is read from the run's job name, which the reusable agent-fix
workflow names after the PR it is working. The producer/consumer pair is
pinned here too: the shipped YAML and the parser must not drift apart.
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

import fix_concurrency  # noqa: E402
import reconcile  # noqa: E402

_WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-fix.yml"
)


def _pr(number, status="DIRTY", branch=None):
    return {
        "number": number,
        "headRefName": branch or f"agent/DRE-2908-pr-{number}",
        "mergeStateStatus": status,
    }


def _fake_gh(listed, runs, jobs):
    """Fake API: `pr list` returns `listed`; `run list` returns `runs`
    (Agent Fix run records); the jobs endpoint returns `jobs[run_id]`, the
    job-name list GitHub would answer with."""

    def gh(*args):
        if args[:2] == ("run", "list"):
            # A str is a raw (possibly unparseable) payload, not a run list.
            return runs if isinstance(runs, str) else json.dumps(runs)
        if args[0] == "api" and args[1].endswith("/jobs"):
            return json.dumps(jobs.get(int(args[1].rsplit("/", 2)[1]), []))
        if args[:2] == ("pr", "list"):
            return json.dumps(listed)
        return ""

    return gh


def _sweep(listed, runs=(), jobs=None, parked=()):
    """Run unstick_conflicts against the fake API; return (dispatched PR
    numbers, stdout)."""
    calls = []
    out = io.StringIO()
    with mock.patch.object(
        reconcile, "gh",
        side_effect=_fake_gh(
            listed, runs if isinstance(runs, str) else list(runs), jobs or {}
        ),
    ), mock.patch.object(
        reconcile, "gh_dispatch", side_effect=lambda *a: calls.append(a)
    ), mock.patch.object(
        reconcile, "card_parked_for_human",
        side_effect=lambda card: card in set(parked),
    ), mock.patch(
        "time.sleep"
    ), contextlib.redirect_stdout(out):
        reconcile.unstick_conflicts()
    dispatched = [
        int(a.split("=", 1)[1])
        for call in calls for a in call if a.startswith("pr_number=")
    ]
    return dispatched, out.getvalue()


def _run(run_id, status="in_progress"):
    return {"databaseId": run_id, "status": status}


def _job_for(number):
    return [f"call / {fix_concurrency.JOB_PR_PREFIX}{number}"]


class OtherPrDoesNotBlockTest(unittest.TestCase):
    """The bug: a fix run on PR A stranded conflicted PR B forever."""

    def test_conflicted_pr_dispatched_while_a_fix_run_works_another_pr(self):
        dispatched, log = _sweep(
            [_pr(2206), _pr(2207)],
            runs=[_run(90001)],
            jobs={90001: _job_for(2206)},
        )
        # #2206 is being worked; #2207 must not wait on it.
        self.assertEqual(dispatched, [2207])
        self.assertIn("#2206", log)

    def test_two_conflicted_prs_with_an_idle_lane_both_dispatch(self):
        dispatched, _ = _sweep([_pr(2206), _pr(2207)])
        self.assertEqual(sorted(dispatched), [2206, 2207])


class SamePrStillSuppressedTest(unittest.TestCase):
    """Never two fix agents pushing one branch."""

    def test_run_in_flight_for_this_pr_suppresses_a_second_dispatch(self):
        dispatched, log = _sweep(
            [_pr(2206)], runs=[_run(90001)], jobs={90001: _job_for(2206)}
        )
        self.assertEqual(dispatched, [])
        self.assertIn("#2206", log)

    def test_a_queued_run_for_this_pr_suppresses_it_too(self):
        dispatched, _ = _sweep(
            [_pr(2206)],
            runs=[_run(90001, status="queued")],
            jobs={90001: _job_for(2206)},
        )
        self.assertEqual(dispatched, [])

    def test_a_completed_run_for_this_pr_does_not_suppress_it(self):
        # Non-vacuous twin: only queued/in_progress runs are in flight.
        dispatched, _ = _sweep(
            [_pr(2206)],
            runs=[_run(90001, status="completed")],
            jobs={90001: _job_for(2206)},
        )
        self.assertEqual(dispatched, [2206])


class UnreadableStillBusyTest(unittest.TestCase):
    """The quota protection this card must not weaken (2026-06-28 App burn):
    an unreadable Actions read answers BUSY for the WHOLE sweep."""

    def test_unreadable_run_listing_defers_every_pr(self):
        calls, out = [], io.StringIO()
        before = list(reconcile._read_failures)

        def gh(*args):  # `run list` unreadable; everything else healthy
            if args[:2] == ("run", "list"):
                return None
            if args[:2] == ("pr", "list"):
                return json.dumps([_pr(2206), _pr(2207)])
            return ""

        try:
            with mock.patch.object(reconcile, "gh", side_effect=gh), \
                 mock.patch.object(
                     reconcile, "gh_dispatch",
                     side_effect=lambda *a: calls.append(a)), \
                 mock.patch.object(
                     reconcile, "card_parked_for_human", return_value=False), \
                 contextlib.redirect_stdout(out):
                reconcile.unstick_conflicts()
        finally:
            del reconcile._read_failures[len(before):]
        self.assertEqual(calls, [])
        self.assertIn("unreadable", out.getvalue().lower())

    def test_unparseable_run_listing_defers_every_pr(self):
        before = list(reconcile._read_failures)
        try:
            dispatched, log = _sweep([_pr(2206)], runs="not json")
        finally:
            del reconcile._read_failures[len(before):]
        self.assertEqual(dispatched, [])
        self.assertIn("unreadable", log.lower())


class AbsentStubIsNotUnreadableTest(unittest.TestCase):
    """DRE-2525 survives the rewrite: a repo with no agent-fix stub has an
    EMPTY lane, not an unreadable one — 61 consecutive red sweeps in 18h37m
    came from conflating the two."""

    def test_absent_workflow_leaves_the_lane_empty_and_dispatches(self):
        from types import SimpleNamespace

        calls = []
        before = list(reconcile._read_failures)

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            if argv[1:3] == ["run", "list"]:
                return SimpleNamespace(
                    returncode=1, stdout="",
                    stderr="HTTP 404: Not Found (workflow does not exist)")
            if argv[1:3] == ["pr", "list"]:
                return SimpleNamespace(
                    returncode=0, stdout=json.dumps([_pr(2206)]), stderr="")
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")

        try:
            with mock.patch.dict(
                os.environ, {"GH_DISPATCH_TOKEN": "ghs_dispatch"}
            ), mock.patch.object(
                reconcile.subprocess, "run", side_effect=fake_run
            ), mock.patch.object(
                reconcile, "workflow_on_default_branch", return_value=False
            ), mock.patch.object(
                reconcile, "card_parked_for_human", return_value=False
            ), contextlib.redirect_stdout(io.StringIO()):
                reconcile.unstick_conflicts()
            dispatched = [c for c in calls if c[1:3] == ["workflow", "run"]]
            self.assertEqual(len(dispatched), 1)
            self.assertEqual(reconcile._read_failures[len(before):], [])
        finally:
            del reconcile._read_failures[len(before):]


class UnattributedRunsAreCappedTest(unittest.TestCase):
    """GitHub attributes no PR to a `workflow_dispatch` run's listing, so an
    unattributable run is COUNTED, never blocking — one busy branch cannot
    starve the queue."""

    def test_one_unattributed_run_does_not_block_a_conflicted_pr(self):
        dispatched, _ = _sweep(
            [_pr(2207)], runs=[_run(90001)], jobs={90001: ["call / fix"]}
        )
        self.assertEqual(dispatched, [2207])

    def test_at_the_cap_the_sweep_defers_and_names_the_pr(self):
        runs = [_run(90000 + i) for i in range(reconcile.CONFLICT_FIX_CAP)]
        dispatched, log = _sweep(
            [_pr(2207)],
            runs=runs,
            jobs={r["databaseId"]: ["call / fix"] for r in runs},
        )
        self.assertEqual(dispatched, [])
        self.assertIn("#2207", log)

    def test_the_sweep_never_bursts_past_the_cap_in_one_pass(self):
        listed = [_pr(2200 + i) for i in range(reconcile.CONFLICT_FIX_CAP + 3)]
        dispatched, _ = _sweep(listed)
        self.assertEqual(len(dispatched), reconcile.CONFLICT_FIX_CAP)


class DeferralLogNamesThePrTest(unittest.TestCase):
    """"this PR is being worked" and "some OTHER PR is being worked" must read
    differently — the old line said neither."""

    def test_same_pr_and_other_pr_deferrals_read_differently(self):
        _, same = _sweep(
            [_pr(2206)], runs=[_run(90001)], jobs={90001: _job_for(2206)}
        )
        runs = [_run(90000 + i) for i in range(reconcile.CONFLICT_FIX_CAP)]
        _, capped = _sweep(
            [_pr(2207)],
            runs=runs,
            jobs={r["databaseId"]: ["call / fix"] for r in runs},
        )
        self.assertIn("#2206", same)
        self.assertIn("#2207", capped)
        self.assertNotEqual(
            [ln for ln in same.splitlines() if "#2206" in ln],
            [ln for ln in capped.splitlines() if "#2207" in ln],
        )


class HumanParkGateUnchangedTest(unittest.TestCase):
    """DRE-2024's gate still runs, per PR, inside the dispatch."""

    def test_parked_card_is_not_dispatched_even_with_an_idle_lane(self):
        dispatched, log = _sweep([_pr(2206)], parked={"DRE-2908"})
        self.assertEqual(dispatched, [])
        self.assertIn("park-gate", log)

    def test_a_parked_pr_does_not_park_its_conflicted_sibling(self):
        dispatched, _ = _sweep(
            [_pr(2206, branch="agent/DRE-2908-parked"),
             _pr(2207, branch="agent/DRE-2907-healthy")],
            parked={"DRE-2908"},
        )
        self.assertEqual(dispatched, [2207])


class RunAttributionContractTest(unittest.TestCase):
    """Producer and consumer of the job name, pinned together."""

    def test_parser_reads_the_pr_out_of_a_job_name(self):
        self.assertEqual(
            fix_concurrency.pr_of_job_names(["call / fix PR #2206"]), 2206
        )

    def test_parser_answers_none_for_an_unattributed_run(self):
        self.assertIsNone(fix_concurrency.pr_of_job_names(["call / fix"]))
        self.assertIsNone(fix_concurrency.pr_of_job_names([]))
        self.assertIsNone(fix_concurrency.pr_of_job_names(None))

    def test_the_shipped_workflow_names_its_job_after_the_pr(self):
        import yaml

        job = yaml.safe_load(open(_WORKFLOW))["jobs"]["fix"]
        self.assertIn(fix_concurrency.JOB_PR_PREFIX, job["name"])
        # The same expression the concurrency group and the Resolve step use.
        self.assertIn("github.event.issue.number", job["name"])
        self.assertIn("pr_number", job["name"])

    def test_the_shipped_job_name_renders_into_something_the_parser_reads(self):
        import re

        import yaml

        job = yaml.safe_load(open(_WORKFLOW))["jobs"]["fix"]
        rendered = re.sub(r"\$\{\{.*?\}\}", "2206", job["name"])
        self.assertEqual(
            fix_concurrency.pr_of_job_names([f"call / {rendered}"]), 2206
        )


if __name__ == "__main__":
    unittest.main()
