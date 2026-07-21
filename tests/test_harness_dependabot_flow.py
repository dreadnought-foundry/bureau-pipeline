"""RED-first tests for the dependabot_flow harness scenario (DRE-2100).

dependabot_flow consumes the REAL open Dependabot PR in the sandbox — the
vendor path that produced most of the 2026-07-12 incidents — and asserts:

  * the `pull_request`-triggered qa-review run SELF-SKIPS clean for actor
    dependabot[bot] (DRE-2067: the empty Dependabot secrets store must
    produce a skipped check run, never a red crash);
  * the reconcile sweep's workflow_dispatch review route (DRE-2047/2053)
    produces a REAL verdict bound to the PR's current head sha;
  * the receipt lifecycle (DRE-2049/2071): 1..DEPENDABOT_RECEIPT_CAP
    worker-bot dispatch receipts per head sha — a verdict with zero
    receipts on an untouched head means the receipted route was bypassed,
    and receipts past the cap mean the sweep is looping.

The LIVE scenario mocks nothing GitHub-side; this suite drives its LOGIC
against the shared in-memory FakeGitHub. The receipt tag/cap literals the
scenario matches against are PINNED to reconcile.py's own constants so
producer/consumer can never drift apart silently.

These tests must FAIL before the scenario exists, and PASS after.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import merge_gate  # noqa: E402
import reconcile  # noqa: E402
from harness import framework  # noqa: E402
from harness import scenarios  # noqa: E402
from harness.scenarios import dependabot_flow  # noqa: E402
from test_harness_bot_pr_flow import QA, WORKER, FakeGitHub, _FakeTime  # noqa: E402

DEPENDABOT = "dependabot[bot]"
REAL_BRANCH = "dependabot/pip/requests-2.32.5"
PARENT = "0" * 40


def _ctx(gh, run_id="gha-1-1"):
    faketime = _FakeTime()
    return framework.HarnessContext(
        gh=gh,
        gh_qa=gh,
        repo="dreadnought-foundry/bureau-harness",
        run_id=run_id,
        worker_login=WORKER,
        qa_login=QA,
        verdict_timeout=100,
        merge_timeout=100,
        poll_interval=1,
        clock=faketime.clock,
        sleep=faketime.sleep,
        log=lambda *_: None,
    )


def _receipt(sha, login=WORKER):
    """A comment shaped like reconcile._post_dependabot_receipt emits."""
    return {
        "user": {"login": login},
        "body": (
            f"🔁 {reconcile.DEPENDABOT_DISPATCH_TAG} @{sha}: dependabot-triggered "
            "pull_request runs get GitHub's empty Dependabot secrets store, so "
            "the reconcile sweep dispatched the critic via workflow_dispatch "
            "instead (DRE-2047)."
        ),
    }


def _seed_real_pr(gh, sha=None, dirty=False):
    """The sandbox's genuine Dependabot PR: dependabot-named branch,
    dependabot[bot] author, single-parent head commit."""
    gh.branches[REAL_BRANCH] = sha or gh._new_sha()
    head = gh.branches[REAL_BRANCH]
    gh._record_commit(head, [PARENT], DEPENDABOT)
    number = gh.seed_pr(head=REAL_BRANCH, login=DEPENDABOT)
    if dirty:
        gh.prs[number]["mergeable_state"] = "dirty"
    return number, head


def _skipped_review_runs(gh, sha, extra=()):
    gh.check_runs[sha] = [
        {"name": "qa / review", "status": "completed", "conclusion": "skipped"},
        {"name": "ci / test", "status": "completed", "conclusion": "success"},
        *extra,
    ]


class DiscoveryTest(unittest.TestCase):
    def test_dependabot_flow_is_discovered_by_convention(self):
        self.assertIn("dependabot_flow", scenarios.discover())


class ProducerConsumerParityTest(unittest.TestCase):
    """The scenario matches receipts the reconcile sweep POSTS — the tag
    and cap literals must be reconcile.py's own, or the harness silently
    asserts against comments that no longer exist."""

    def test_dispatch_tag_matches_reconcile(self):
        self.assertEqual(
            dependabot_flow.DISPATCH_TAG, reconcile.DEPENDABOT_DISPATCH_TAG
        )

    def test_receipt_cap_matches_reconcile(self):
        self.assertEqual(
            dependabot_flow.RECEIPT_CAP, reconcile.DEPENDABOT_RECEIPT_CAP
        )


class FindRealDependabotPrTest(unittest.TestCase):
    def _pr(self, number, ref, login):
        return {"number": number, "head": {"ref": ref}, "user": {"login": login}}

    def test_picks_the_oldest_genuine_dependabot_pr(self):
        prs = [
            self._pr(9, "dependabot/npm_and_yarn/lodash-5.0.0", DEPENDABOT),
            self._pr(4, REAL_BRANCH, DEPENDABOT),
            self._pr(7, "agent/harness-run-bot_pr_flow", WORKER),
        ]
        self.assertEqual(framework.find_real_dependabot_pr(prs)["number"], 4)

    def test_spoofed_and_harness_branches_are_not_genuine(self):
        prs = [
            # dependabot-named but worker-authored: branch names are free
            # text — only the reserved dependabot[bot] login counts.
            self._pr(1, "dependabot/pip/spoofed-1.0.0", WORKER),
            # the harness's own gate_paths probe lives in dependabot/harness-
            self._pr(2, "dependabot/harness-gha-1-1-gate_paths-named", WORKER),
            self._pr(3, "agent/DRE-1-x", DEPENDABOT),
        ]
        self.assertIsNone(framework.find_real_dependabot_pr(prs))


class PureLogicTest(unittest.TestCase):
    def test_rebase_command_is_the_documented_vendor_command(self):
        self.assertIn("@dependabot rebase", dependabot_flow.rebase_command())

    def test_nothing_the_scenario_posts_contains_a_verdict_marker(self):
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(marker, dependabot_flow.rebase_command())

    def test_receipt_count_is_sha_bound_and_author_gated(self):
        sha, other = "a" * 40, "b" * 40
        comments = [
            _receipt(sha),
            _receipt(other),  # superseded head — a rebase re-arms the budget
            _receipt(sha, login="mallory"),  # forged — invisible (DRE-1998)
            {"user": {"login": WORKER}, "body": "unrelated"},
        ]
        self.assertEqual(dependabot_flow.receipt_count(comments, WORKER, sha), 1)

    def test_review_check_runs_filters_by_review_name(self):
        runs = [
            {"name": "qa / review", "conclusion": "skipped"},
            {"name": "ci / test", "conclusion": "success"},
        ]
        self.assertEqual(len(dependabot_flow.review_check_runs(runs)), 1)


class SteadyStateTest(unittest.TestCase):
    def test_settled_head_passes_and_never_touches_the_real_pr(self):
        # Back-to-back-runs steady state: the head already carries its
        # skipped self-skip run, exactly one receipt, and a bound verdict
        # (major pins never merge, so the PR persists between runs).
        gh = FakeGitHub()
        number, head = _seed_real_pr(gh)
        _skipped_review_runs(gh, head)
        gh.comments[number].append(_receipt(head))
        gh.post_verdict(number, "REQUEST_CHANGES", head)

        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))

        self.assertTrue(result.ok, result.errors)
        # The vendor's PR and branch are NOT the harness's to clean up.
        self.assertEqual(gh.prs[number]["state"], "open")
        self.assertIn(REAL_BRANCH, gh.branches)

    def test_any_bound_verdict_token_satisfies_the_route(self):
        gh = FakeGitHub()
        number, head = _seed_real_pr(gh)
        _skipped_review_runs(gh, head)
        gh.comments[number].append(_receipt(head))
        gh.post_verdict(number, "APPROVE", head)
        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))
        self.assertTrue(result.ok, result.errors)


class SelfSkipTest(unittest.TestCase):
    def test_red_review_run_on_the_dependabot_head_fails(self):
        # THE incident class (DRE-2047/2067): the review run crashing at
        # the token mint instead of skipping.
        gh = FakeGitHub()
        number, head = _seed_real_pr(gh)
        gh.check_runs[head] = [
            {"name": "qa / review", "status": "completed", "conclusion": "failure"},
        ]
        gh.comments[number].append(_receipt(head))
        gh.post_verdict(number, "APPROVE", head)

        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")
        self.assertIn("red", "\n".join(result.errors).lower())

    def test_reviewed_instead_of_skipped_on_a_dependabot_pushed_head_fails(self):
        # A success-concluded review run on a single-parent (dependabot-
        # pushed) head means the event-driven run REVIEWED with the empty
        # Dependabot secrets store — impossible, so something is miswired.
        gh = FakeGitHub()
        number, head = _seed_real_pr(gh)
        gh.check_runs[head] = [
            {"name": "qa / review", "status": "completed", "conclusion": "success"},
        ]
        gh.comments[number].append(_receipt(head))
        gh.post_verdict(number, "APPROVE", head)

        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")

    def test_gate_updated_head_tolerates_a_success_review_run(self):
        # A 2-parent head was update-branched by the gate (minor/patch PR
        # behind base): its synchronize actor is the qa-bot with NORMAL
        # secrets, so a success review run is the DRE-2037 path — and no
        # dispatch receipt is expected for that head either.
        gh = FakeGitHub()
        number, head = _seed_real_pr(gh)
        gh._record_commit(head, [PARENT, "1" * 40], QA)  # merge commit
        gh.check_runs[head] = [
            {"name": "qa / review", "status": "completed", "conclusion": "success"},
        ]
        gh.post_verdict(number, "APPROVE", head)

        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))
        self.assertTrue(result.ok, result.errors)


class ReceiptLifecycleTest(unittest.TestCase):
    def test_two_receipts_with_a_verdict_is_the_bounded_retry_working(self):
        gh = FakeGitHub()
        number, head = _seed_real_pr(gh)
        _skipped_review_runs(gh, head)
        gh.comments[number].extend([_receipt(head), _receipt(head)])
        gh.post_verdict(number, "APPROVE", head)
        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))
        self.assertTrue(result.ok, result.errors)

    def test_receipts_past_the_cap_fail_as_a_looping_sweep(self):
        gh = FakeGitHub()
        number, head = _seed_real_pr(gh)
        _skipped_review_runs(gh, head)
        gh.comments[number].extend(
            [_receipt(head)] * (reconcile.DEPENDABOT_RECEIPT_CAP + 1)
        )
        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))
        self.assertFalse(result.ok)
        self.assertIn("cap", "\n".join(result.errors).lower())

    def test_verdict_with_zero_receipts_on_an_untouched_head_fails(self):
        # A bound verdict that did NOT come via the receipted dispatch
        # route (and not via the gate-update synchronize path either)
        # means the route under test never ran.
        gh = FakeGitHub()
        number, head = _seed_real_pr(gh)
        _skipped_review_runs(gh, head)
        gh.post_verdict(number, "APPROVE", head)
        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))
        self.assertFalse(result.ok)
        self.assertIn("receipt", "\n".join(result.errors).lower())

    def test_fresh_head_waits_for_receipt_then_verdict(self):
        # The fresh-cycle path: no verdict yet — the sandbox reconcile
        # sweep (cron) posts the receipt, the dispatched review posts the
        # bound verdict, and the scenario observes exactly one receipt.
        gh = FakeGitHub()
        number, head = _seed_real_pr(gh)
        _skipped_review_runs(gh, head)
        polls = {"n": 0}

        def sweep_then_review(fake):
            polls["n"] += 1
            if polls["n"] == 4:
                fake.comments[number].append(_receipt(head))
            if polls["n"] == 8:
                fake.post_verdict(number, "APPROVE", head)

        gh.on_poll = sweep_then_review
        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))
        self.assertTrue(result.ok, result.errors)


class DirtyPrTest(unittest.TestCase):
    def test_dirty_pr_gets_the_rebase_command_and_a_fresh_head(self):
        gh = FakeGitHub()
        number, old_head = _seed_real_pr(gh, dirty=True)
        state = {}

        def dependabot_reacts(fake):
            # Dependabot rebases once it sees the command comment.
            commanded = any(
                "@dependabot rebase" in c["body"]
                for c in fake.comments[number]
            )
            if commanded and "new" not in state:
                new = fake._new_sha()
                fake._record_commit(new, [PARENT], DEPENDABOT)
                fake.branches[REAL_BRANCH] = new
                fake.prs[number]["mergeable_state"] = "clean"
                _skipped_review_runs(fake, new)
                fake.comments[number].append(_receipt(new))
                fake.post_verdict(number, "APPROVE", new)
                state["new"] = new

        gh.on_poll = dependabot_reacts
        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))

        self.assertTrue(result.ok, result.errors)
        self.assertTrue(
            any("@dependabot rebase" in c["body"] for c in gh.comments[number]),
            "the scenario must post the vendor rebase command on a DIRTY PR",
        )

    def test_dirty_pr_that_never_rebases_fails_with_vendor_guidance(self):
        gh = FakeGitHub()
        _seed_real_pr(gh, dirty=True)
        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))
        self.assertFalse(result.ok)
        self.assertIn("dependabot", "\n".join(result.errors).lower())


class MissingPrTest(unittest.TestCase):
    def test_no_open_dependabot_pr_fails_with_operator_guidance(self):
        # A Dependabot PR cannot be conjured by API — honest failure with
        # the regeneration command named beats pretend coverage.
        gh = FakeGitHub()
        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "setup")
        self.assertIn("@dependabot recreate", "\n".join(result.errors))


class RebaseMidFlightTest(unittest.TestCase):
    def test_head_moving_mid_wait_re_targets_the_new_head(self):
        # Dependabot may rebase spontaneously while the scenario waits —
        # the verdict target must follow the CURRENT head, not freeze on
        # the one observed at setup.
        gh = FakeGitHub()
        number, old_head = _seed_real_pr(gh)
        _skipped_review_runs(gh, old_head)
        state = {"polls": 0}

        def rebase_then_settle(fake):
            state["polls"] += 1
            if state["polls"] == 3 and "new" not in state:
                new = fake._new_sha()
                fake._record_commit(new, [PARENT], DEPENDABOT)
                fake.branches[REAL_BRANCH] = new
                _skipped_review_runs(fake, new)
                fake.comments[number].append(_receipt(new))
                fake.post_verdict(number, "APPROVE", new)
                state["new"] = new

        gh.on_poll = rebase_then_settle
        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))
        self.assertTrue(result.ok, result.errors)


if __name__ == "__main__":
    unittest.main()
