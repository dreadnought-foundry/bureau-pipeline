"""RED-first tests for the dependabot_flow harness scenario (DRE-2100).

dependabot_flow consumes the REAL open Dependabot PR in the sandbox — the
vendor path that produced most of the 2026-07-12 incidents — and asserts:

  * the `pull_request`-triggered qa-review run SELF-SKIPS clean for actor
    dependabot[bot] (DRE-2067: the empty Dependabot secrets store must
    produce a skipped check run, never a red crash) — judged on the
    RUN-attributed checks only, since DRE-2291 put a second review-named
    check on the head whose conclusion reports the verdict;
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
import publish_review_check  # noqa: E402
import reconcile  # noqa: E402
from harness import framework  # noqa: E402
from harness import scenarios  # noqa: E402
from harness.scenarios import dependabot_flow  # noqa: E402
from test_harness_bot_pr_flow import QA, WORKER, FakeGitHub, _FakeTime  # noqa: E402

DEPENDABOT = "dependabot[bot]"
REAL_BRANCH = "dependabot/pip/requests-2.32.5"
PARENT = "0" * 40


def _ctx(gh, run_id="gha-1-1", namespace=framework.DEFAULT_NAMESPACE,
         gh_qa=None, log=None):
    # Composed the way __main__ composes it: the namespace OPENS the run id,
    # so the run's branches sit in the slice its own sweep owns (DRE-3075).
    run_id = framework.namespaced_run_id(namespace, run_id)
    faketime = _FakeTime()
    return framework.HarnessContext(
        gh=gh,
        gh_qa=gh_qa or gh,
        repo="dreadnought-foundry/bureau-harness",
        run_id=run_id,
        namespace=namespace,
        worker_login=WORKER,
        qa_login=QA,
        verdict_timeout=100,
        merge_timeout=100,
        poll_interval=1,
        clock=faketime.clock,
        sleep=faketime.sleep,
        log=log or (lambda *_: None),
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


def _bound_check(conclusion):
    """The head-bound review record publish_review_check.py writes (DRE-2291).

    Its conclusion reports the VERDICT, not the run's liveness: a genuine
    `REQUEST_CHANGES` publishes it RED on a review route that worked
    perfectly.
    """
    return {
        "name": publish_review_check.CHECK_NAME,
        "status": "completed",
        "conclusion": conclusion,
    }


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

    def test_head_bound_check_name_matches_its_publisher(self):
        # The name is the join between the script that WRITES the head-bound
        # record and the scenario that has to tell it apart from a run's own
        # liveness check — imported, never re-typed (DRE-2291).
        self.assertEqual(
            dependabot_flow.HEAD_BOUND_CHECK_NAME, publish_review_check.CHECK_NAME
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

    def test_review_check_runs_excludes_the_head_bound_verdict_record(self):
        # The self-skip clause asks whether the event-driven RUN survived,
        # and only run-attributed checks answer that. The head-bound record
        # answers a different question — what the critic SAID — so it must
        # not be counted among them (DRE-2291).
        runs = [
            {"name": "qa / review", "conclusion": "skipped"},
            _bound_check("failure"),
        ]
        kept = dependabot_flow.review_check_runs(runs)
        self.assertEqual([r["name"] for r in kept], ["qa / review"])


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

    def test_a_request_changes_verdict_check_is_not_read_as_a_crash(self):
        # The red-main class (run 34519112325): the sandbox's settled state
        # is a REQUEST_CHANGES verdict on a major pin, and since DRE-2291
        # that verdict ALSO publishes a red head-bound check. Reading that
        # redness as the DRE-2047/2067 self-skip crash fails a route that
        # worked — the scenario's own contract says any bound verdict token
        # is a working route, and reconcile agrees (recover_crashed_reviews
        # never calls a head with a bound verdict crashed).
        gh = FakeGitHub()
        number, head = _seed_real_pr(gh)
        _skipped_review_runs(gh, head, extra=[_bound_check("failure")])
        gh.comments[number].append(_receipt(head))
        gh.post_verdict(number, "REQUEST_CHANGES", head)

        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))

        self.assertTrue(result.ok, result.errors)

    def test_a_head_bound_red_check_with_no_verdict_still_fails(self):
        # The crash signal survives the clause above: a critic that ran and
        # died publishes the same red record and posts NO verdict, so the
        # head stays frozen and the scenario must still fail — on the
        # verdict wait, which can tell the two apart, not on the check
        # conclusion, which cannot.
        gh = FakeGitHub()
        number, head = _seed_real_pr(gh)
        _skipped_review_runs(gh, head, extra=[_bound_check("failure")])
        gh.comments[number].append(_receipt(head))

        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))

        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")
        self.assertIn("verdict", "\n".join(result.errors).lower())

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


FIXTURE_LABEL = "harness-fixture"
BANNER = (
    "HARNESS FIXTURE — the integration harness needs this PR open. "
    "Do not close or merge."
)
FIXTURE_BODY = "Bumps pytest from 7.0.0 to 9.1.1."


def _seed_closed_fixture(gh, labeled=True, merged=False):
    """bureau-harness #1 as operator sessions left it on 2026-09-24 and
    2026-10-24: closed as a stale major, unmerged, and Dependabot deleted
    its branch ten seconds later. The closed PR still records its last
    head sha — that is all the self-heal needs."""
    number, head = _seed_real_pr(gh)
    pr = gh.prs[number]
    pr["body"] = FIXTURE_BODY
    if labeled:
        pr["labels"] = [{"name": FIXTURE_LABEL}]
    pr["state"] = "closed"
    if merged:
        pr["merged"] = True
        pr["merged_at"] = "2026-10-24T00:49:30Z"
    del gh.branches[REAL_BRANCH]
    return number, head


def _label_names(pr):
    return [label["name"] for label in pr.get("labels") or []]


class FixtureSelfHealTest(unittest.TestCase):
    """DRE-7957: the sandbox's Dependabot PR is a closable object that looks
    like stale debt. Closed twice as a semver-major, it turned main red and
    froze `stable` fleet-wide for four hours on 2026-10-24. setup() now
    reopens its own labeled fixture instead of failing main."""

    def test_closed_labeled_fixture_is_restored_reopened_and_consumed(self):
        gh = FakeGitHub()
        number, head = _seed_closed_fixture(gh)
        logs = []
        ctx = _ctx(gh, log=logs.append)

        dependabot_flow.SCENARIO.setup(ctx)

        self.assertEqual(gh.branches.get(REAL_BRANCH), head)
        self.assertEqual(gh.prs[number]["state"], "open")
        self.assertIn(f"[dependabot_flow] reopened fixture #{number}", logs)
        self.assertEqual(ctx.state["number"], number)
        self.assertEqual(ctx.state["head"], head)
        self.assertIn(FIXTURE_LABEL, _label_names(gh.prs[number]))
        self.assertEqual(gh.prs[number]["body"].splitlines()[0], BANNER)

    def test_reopened_fixture_carries_the_label_and_the_banner(self):
        gh = FakeGitHub()
        number, _ = _seed_closed_fixture(gh)

        dependabot_flow.SCENARIO.setup(_ctx(gh))

        pr = gh.prs[number]
        self.assertIn(FIXTURE_LABEL, _label_names(pr))
        self.assertEqual(pr["body"].splitlines()[0], BANNER)
        self.assertIn(FIXTURE_BODY, pr["body"])
        self.assertIn(("add_labels", number, (FIXTURE_LABEL,)), gh.writes)

    def test_a_reopened_fixture_runs_the_whole_scenario_green(self):
        gh = FakeGitHub()
        number, head = _seed_closed_fixture(gh)
        _skipped_review_runs(gh, head)
        gh.comments[number].append(_receipt(head))
        gh.post_verdict(number, "REQUEST_CHANGES", head)

        result = framework.run_scenario(dependabot_flow.SCENARIO, _ctx(gh))

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(gh.prs[number]["state"], "open")

    def test_every_fixture_write_goes_out_as_ctx_gh_and_no_other_client(self):
        # bureau-harness and bureau-pipeline are public: the reopen is the
        # harness's own identity, never a person's or the qa-bot's.
        gh, qa = FakeGitHub(), FakeGitHub()
        number, head = _seed_closed_fixture(gh)

        dependabot_flow.SCENARIO.setup(_ctx(gh, gh_qa=qa))

        ops = [w[0] for w in gh.writes]
        self.assertIn(("create_ref", REAL_BRANCH, head), gh.writes)
        self.assertIn(("reopen_pr", number), gh.writes)
        self.assertIn("add_labels", ops)
        self.assertLess(ops.index("create_ref"), ops.index("reopen_pr"))
        self.assertEqual(qa.writes, [])

    def test_a_fixture_whose_branch_survived_is_reopened_without_a_ref_write(self):
        gh = FakeGitHub()
        number, head = _seed_closed_fixture(gh)
        gh.branches[REAL_BRANCH] = head

        dependabot_flow.SCENARIO.setup(_ctx(gh))

        self.assertEqual(gh.prs[number]["state"], "open")
        self.assertNotIn("create_ref", [w[0] for w in gh.writes])

    def test_open_pr_without_the_label_is_labeled(self):
        gh = FakeGitHub()
        number, _ = _seed_real_pr(gh)

        dependabot_flow.SCENARIO.setup(_ctx(gh))

        self.assertIn(FIXTURE_LABEL, _label_names(gh.prs[number]))
        self.assertIn(("add_labels", number, (FIXTURE_LABEL,)), gh.writes)

    def test_open_pr_already_labeled_gets_no_label_write(self):
        gh = FakeGitHub()
        number, _ = _seed_real_pr(gh)
        gh.prs[number]["labels"] = [{"name": FIXTURE_LABEL}]
        gh.prs[number]["body"] = f"{BANNER}\n\n{FIXTURE_BODY}"

        dependabot_flow.SCENARIO.setup(_ctx(gh))

        self.assertNotIn("add_labels", [w[0] for w in gh.writes])
        self.assertEqual(gh.writes, [])

    def test_open_pr_without_the_banner_gets_it_on_top_once(self):
        gh = FakeGitHub()
        number, _ = _seed_real_pr(gh)
        gh.prs[number]["body"] = FIXTURE_BODY

        dependabot_flow.SCENARIO.setup(_ctx(gh))
        dependabot_flow.SCENARIO.setup(_ctx(gh))

        body = gh.prs[number]["body"]
        self.assertEqual(body.splitlines()[0], BANNER)
        self.assertEqual(body.count(BANNER), 1)
        self.assertIn(FIXTURE_BODY, body)

    def test_no_labeled_fixture_still_raises_no_pr_guidance(self):
        # An unlabeled closed Dependabot PR is not the harness's to reopen.
        gh = FakeGitHub()
        number, _ = _seed_closed_fixture(gh, labeled=False)

        with self.assertRaises(framework.ScenarioFailure) as raised:
            dependabot_flow.SCENARIO.setup(_ctx(gh))

        self.assertEqual(str(raised.exception), dependabot_flow.NO_PR_GUIDANCE)
        self.assertEqual(gh.prs[number]["state"], "closed")
        self.assertEqual(gh.writes, [])

    def test_a_merged_fixture_is_not_reopened_and_the_failure_names_the_merge(self):
        gh = FakeGitHub()
        number, _ = _seed_closed_fixture(gh, merged=True)

        with self.assertRaises(framework.ScenarioFailure) as raised:
            dependabot_flow.SCENARIO.setup(_ctx(gh))

        self.assertIn("merged", str(raised.exception))
        self.assertIn(f"#{number}", str(raised.exception))
        self.assertEqual(gh.prs[number]["state"], "closed")
        self.assertNotIn(REAL_BRANCH, gh.branches)
        self.assertEqual(gh.writes, [])

    def test_nothing_the_fixture_marking_writes_contains_a_verdict_marker(self):
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(marker, BANNER)
            self.assertNotIn(marker, dependabot_flow.FIXTURE_BANNER)


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
