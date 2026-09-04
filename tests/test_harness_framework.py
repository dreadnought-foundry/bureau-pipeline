"""RED-first tests for the integration-harness framework (DRE-2098).

The harness drives the LIVE pipeline in the sandbox repo
dreadnought-foundry/bureau-harness — real branches, real PRs, real critic
runs, nothing GitHub-side mocked. What IS unit-tested here is the pure
logic the live runs depend on:

  * run-id namespacing — every branch the harness creates is uniquely
    namespaced (`agent/harness-<run-id>-<scenario>`) so a crashed run's
    leftovers are identifiable and a later sweep cannot touch real work
    (an `agent/DRE-n-*` branch must NEVER match the sweep predicate);
  * phase discipline — setup → exercise → verify run in order, a failure
    stops progression, and cleanup ALWAYS runs (crash paths must not
    leave the sandbox dirty for the next run);
  * leftover sweep — stale harness branches/PRs from a crashed previous
    run are closed/deleted, and ONLY those;
  * verdict analysis — bound/stale/neutral verdict states, reusing
    merge_gate.py's own parsing so the harness's idea of "a verdict bound
    to the head sha" stays in lockstep with the real gate's.

These tests must FAIL before scripts/harness/ exists, and PASS after.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import merge_gate  # noqa: E402
from harness import framework  # noqa: E402


def _verdict_comment(token: str, sha: str, login: str = "agent-bureau-qa-bot[bot]",
                     content: str | None = None):
    """A comment shaped exactly like qa-review.yml's Post step emits.

    `content` appends the DRE-2340 ` content:<64-hex>` suffix the verdict line
    carries when the reviewed commit's three-dot diff could be hashed.
    """
    suffix = f" content:{content}" if content else ""
    return {
        "user": {"login": login},
        "body": (f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: {token} @{sha}{suffix}"
                 "\n\n## Summary\nok"),
    }


def _carry_receipt(content: str, reviewed: str, head: str,
                   login: str = "agent-bureau-qa-bot[bot]"):
    """The receipt merge-gate posts when it honours a verdict across a head
    change — byte-shaped like the real one in merge-gate.yml."""
    return {
        "user": {"login": login},
        "body": (
            f"♻️ Merge gate: carried verdict content:{content} — the standing "
            "review verdict was carried across a branch update.\n\n"
            f"Reviewed at {reviewed}; current head {head}. This branch's own "
            "changes are byte-identical to what was reviewed.\n"
        ),
    }


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
CONTENT_A = "1" * 64
CONTENT_B = "2" * 64
QA = "agent-bureau-qa-bot[bot]"


class RunIdTest(unittest.TestCase):
    def test_new_run_ids_are_branch_safe_and_distinct(self):
        a, b = framework.new_run_id(), framework.new_run_id()
        for rid in (a, b):
            self.assertRegex(rid, r"^[a-z0-9][a-z0-9-]+$")
        self.assertNotEqual(a, b)

    def test_workflow_provided_run_id_is_accepted(self):
        # harness.yml passes gha-<run_id>-<attempt> — must validate clean.
        self.assertEqual(
            framework.validate_run_id("gha-16428395714-1"), "gha-16428395714-1"
        )

    def test_hostile_run_ids_are_rejected(self):
        # A run id lands verbatim in branch names and file paths — anything
        # that could escape the namespace or the ref syntax is refused.
        for bad in ("", "UPPER", "has space", "a/b", "x..y", "-lead", "ünïcode"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                framework.validate_run_id(bad)


class NamespaceTest(unittest.TestCase):
    def test_scenario_branch_is_namespaced_and_reviewable(self):
        branch = framework.scenario_branch("gha-1-1", "bot_pr_flow")
        # agent/ prefix = the shape should_review_pr.py reviews; the
        # harness- marker + run id = the sweepable namespace.
        self.assertTrue(branch.startswith("agent/harness-"))
        self.assertIn("gha-1-1", branch)
        self.assertIn("bot_pr_flow", branch)

    def test_harness_refs_match_any_run_id(self):
        # The sweep must catch leftovers from ANY previous run, not just its
        # own run id.
        self.assertTrue(framework.is_harness_ref("agent/harness-oldrun-bot_pr_flow"))
        self.assertTrue(
            framework.is_harness_ref("refs/heads/agent/harness-gha-2-1-gate_paths")
        )

    def test_real_work_never_matches_the_sweep(self):
        # Deleting a real agent's branch would destroy in-flight card work —
        # the single most dangerous bug this predicate can have. Real
        # Dependabot branches are just as load-bearing: sweeping one would
        # kill the vendor PR dependabot_flow exists to consume.
        for ref in (
            "agent/DRE-2098-harness-driver-bot-pr-flow",  # THIS card's branch
            "agent/DRE-123-fix",
            "refs/heads/agent/DRE-9-x",
            "dependabot/pip/pytest-9.1.1",
            "dependabot/npm_and_yarn/main/lodash-5.0.0",
            "dependabot/github_actions/actions/checkout-5",
            "repair/" + SHA_A,
            "main",
            "harness-loose-ref",
        ):
            self.assertFalse(framework.is_harness_ref(ref), ref)

    def test_dependabot_named_scenario_branch_is_namespaced_and_sweepable(self):
        # gate_paths probes merge_gate condition D with a dependabot-NAMED
        # branch — it must sit inside the harness namespace (sweepable, and
        # never mistakable for a genuine Dependabot branch).
        branch = framework.dependabot_scenario_branch("gha-1-1", "gate_paths-named")
        self.assertTrue(branch.startswith("dependabot/harness-"))
        self.assertIn("gha-1-1", branch)
        self.assertTrue(framework.is_harness_ref(branch))
        self.assertTrue(
            framework.is_harness_ref("dependabot/harness-oldrun-gate_paths-named")
        )

    def test_dependabot_scenario_branch_validates_the_run_id(self):
        with self.assertRaises(ValueError):
            framework.dependabot_scenario_branch("has space", "gate_paths")


class PhaseDisciplineTest(unittest.TestCase):
    class _Recording(framework.Scenario):
        name = "recorder"

        def __init__(self, fail_at=None, fail_cleanup=False):
            self.calls = []
            self.fail_at = fail_at
            self.fail_cleanup = fail_cleanup

        def setup(self, ctx):
            self.calls.append("setup")
            if self.fail_at == "setup":
                raise RuntimeError("boom-setup")

        def exercise(self, ctx):
            self.calls.append("exercise")
            if self.fail_at == "exercise":
                raise RuntimeError("boom-exercise")

        def verify(self, ctx):
            self.calls.append("verify")
            if self.fail_at == "verify":
                raise RuntimeError("boom-verify")

        def cleanup(self, ctx):
            self.calls.append("cleanup")
            if self.fail_cleanup:
                raise RuntimeError("boom-cleanup")

    def _ctx(self):
        return framework.HarnessContext(
            gh=None, repo="o/r", run_id="t-1", qa_login=QA
        )

    def test_happy_path_runs_all_phases_in_order(self):
        s = self._Recording()
        result = framework.run_scenario(s, self._ctx())
        self.assertEqual(s.calls, ["setup", "exercise", "verify", "cleanup"])
        self.assertTrue(result.ok)
        self.assertEqual(result.scenario, "recorder")

    def test_exercise_failure_skips_verify_but_still_cleans_up(self):
        s = self._Recording(fail_at="exercise")
        result = framework.run_scenario(s, self._ctx())
        self.assertEqual(s.calls, ["setup", "exercise", "cleanup"])
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "exercise")
        self.assertTrue(any("boom-exercise" in e for e in result.errors))

    def test_cleanup_failure_alone_fails_the_scenario(self):
        # Cleanup is load-bearing: it is what proves the sandbox is usable
        # for the next run. A green run that leaves a mess is a fail.
        s = self._Recording(fail_cleanup=True)
        result = framework.run_scenario(s, self._ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "cleanup")

    def test_cleanup_failure_does_not_mask_the_primary_error(self):
        s = self._Recording(fail_at="verify", fail_cleanup=True)
        result = framework.run_scenario(s, self._ctx())
        self.assertEqual(result.failed_phase, "verify")
        joined = "\n".join(result.errors)
        self.assertIn("boom-verify", joined)
        self.assertIn("boom-cleanup", joined)


class WaitUntilTest(unittest.TestCase):
    def _clock(self, step=1.0):
        t = {"now": 0.0}

        def clock():
            return t["now"]

        def sleep(seconds):
            t["now"] += seconds

        return clock, sleep

    def test_returns_the_first_truthy_value(self):
        clock, sleep = self._clock()
        results = iter([None, None, {"merged": True}])
        value = framework.wait_until(
            "pr merged", lambda: next(results),
            timeout=60, interval=5, clock=clock, sleep=sleep,
        )
        self.assertEqual(value, {"merged": True})

    def test_times_out_with_the_description_in_the_error(self):
        clock, sleep = self._clock()
        with self.assertRaises(framework.HarnessTimeout) as caught:
            framework.wait_until(
                "critic verdict", lambda: None,
                timeout=30, interval=5, clock=clock, sleep=sleep,
            )
        self.assertIn("critic verdict", str(caught.exception))


class VerdictStateTest(unittest.TestCase):
    """The verify phase's verdict analysis — every state distinguishable so
    a live failure names WHAT went wrong, not just "no merge"."""

    def test_bound_approve(self):
        state, _ = framework.verdict_state([_verdict_comment("APPROVE", SHA_A)], QA, SHA_A)
        self.assertEqual(state, "APPROVE")

    def test_bound_request_changes(self):
        state, _ = framework.verdict_state(
            [_verdict_comment("REQUEST_CHANGES", SHA_A)], QA, SHA_A
        )
        self.assertEqual(state, "REQUEST_CHANGES")

    def test_stale_sha_is_not_a_verdict_for_this_head(self):
        state, detail = framework.verdict_state(
            [_verdict_comment("APPROVE", SHA_B)], QA, SHA_A
        )
        self.assertEqual(state, "stale")
        self.assertIn(SHA_B, detail)

    def test_neutral_could_not_run_status(self):
        neutral = {
            "user": {"login": QA},
            "body": f"🔎 {merge_gate.CRITIC_MARKER} could not run (infra error) — re-review needed.",
        }
        state, _ = framework.verdict_state([neutral], QA, SHA_A)
        self.assertEqual(state, "neutral")

    def test_wrong_author_is_invisible(self):
        forged = _verdict_comment("APPROVE", SHA_A, login="mallory")
        state, _ = framework.verdict_state([forged], QA, SHA_A)
        self.assertEqual(state, "none")

    def test_no_comments_at_all(self):
        state, _ = framework.verdict_state([], QA, SHA_A)
        self.assertEqual(state, "none")

    # -- DRE-2340: a CARRIED verdict still covers this head -------------------
    #
    # The gate no longer requires the verdict's sha to equal the head. When a
    # base merge moves the head without changing what the PR itself
    # contributes, the standing verdict carries and the gate posts a receipt
    # saying so. `verdict_state` answers "does the latest verdict COVER this
    # head?", so it has to understand that — otherwise the harness calls a
    # legitimate merge a stale one. Which it did: from 2026-08-10 02:39 (#140
    # reaching main) gate_paths failed its stale and skew legs on EVERY run,
    # for every PR in the repo, and the red was inherited by changes that had
    # nothing to do with verdict binding.
    #
    # The receipt is the evidence, deliberately. Recomputing the content id
    # here would re-implement the gate's own judgment and could agree with a
    # bug in it; matching the gate's PUBLISHED reason means an unexplained
    # carry still fails, which is the property worth keeping.

    def test_a_carried_verdict_covers_the_new_head(self):
        comments = [
            _verdict_comment("APPROVE", SHA_B, content=CONTENT_A),
            _carry_receipt(content=CONTENT_A, reviewed=SHA_B, head=SHA_A),
        ]
        state, detail = framework.verdict_state(comments, QA, SHA_A)
        self.assertEqual(state, "APPROVE",
                         f"a carried verdict read as {state}: {detail}")
        self.assertIn("carried", detail.lower())

    def test_a_receipt_for_a_DIFFERENT_content_id_does_not_carry(self):
        # The push changed what the PR contributes, so the verdict must die.
        comments = [
            _verdict_comment("APPROVE", SHA_B, content=CONTENT_A),
            _carry_receipt(content=CONTENT_B, reviewed=SHA_B, head=SHA_A),
        ]
        state, _ = framework.verdict_state(comments, QA, SHA_A)
        self.assertEqual(state, "stale")

    def test_a_receipt_for_a_DIFFERENT_head_does_not_carry(self):
        comments = [
            _verdict_comment("APPROVE", SHA_B, content=CONTENT_A),
            _carry_receipt(content=CONTENT_A, reviewed=SHA_B, head=SHA_C),
        ]
        state, _ = framework.verdict_state(comments, QA, SHA_A)
        self.assertEqual(state, "stale")

    def test_a_stale_verdict_with_no_receipt_is_still_stale(self):
        # The pre-2340 behaviour, unchanged: no receipt, no carry.
        state, _ = framework.verdict_state(
            [_verdict_comment("APPROVE", SHA_B, content=CONTENT_A)], QA, SHA_A
        )
        self.assertEqual(state, "stale")

    def test_a_forged_receipt_from_another_author_does_not_carry(self):
        # The receipt is a gate credential and anyone can comment on a PR.
        comments = [
            _verdict_comment("APPROVE", SHA_B, content=CONTENT_A),
            _carry_receipt(content=CONTENT_A, reviewed=SHA_B, head=SHA_A,
                           login="mallory"),
        ]
        state, _ = framework.verdict_state(comments, QA, SHA_A)
        self.assertEqual(state, "stale",
                         "a receipt from a non-gate author carried a verdict")

    def test_a_verdict_with_no_content_id_never_carries(self):
        # Pre-2340 verdicts carry a sha and nothing else. A receipt cannot
        # rescue one — there is nothing to match it against.
        comments = [
            _verdict_comment("APPROVE", SHA_B),
            _carry_receipt(content=CONTENT_A, reviewed=SHA_B, head=SHA_A),
        ]
        state, _ = framework.verdict_state(comments, QA, SHA_A)
        self.assertEqual(state, "stale")

    def test_a_receipt_spelling_the_missing_id_does_not_carry(self):
        # Found by mutation: without the explicit `if not content_id` guard,
        # the needle for a content-less verdict becomes the literal
        # "content:None" — so a receipt carrying that exact text would carry a
        # verdict that binds nothing but a sha. Exotic, but it is a forged
        # credential and the guard costs one line.
        comments = [
            _verdict_comment("APPROVE", SHA_B),
            _carry_receipt(content="None", reviewed=SHA_B, head=SHA_A),
        ]
        state, _ = framework.verdict_state(comments, QA, SHA_A)
        self.assertEqual(state, "stale",
                         "a receipt spelling the absent content id carried it")


class SameBotTest(unittest.TestCase):
    def test_bot_suffix_is_normalized(self):
        # REST merged_by.login carries "[bot]"; the app-slug env may not.
        self.assertTrue(framework.same_bot("agent-bureau-qa-bot[bot]", "agent-bureau-qa-bot"))
        self.assertTrue(framework.same_bot("agent-bureau-qa-bot", "agent-bureau-qa-bot[bot]"))

    def test_different_bots_never_match(self):
        self.assertFalse(framework.same_bot("agent-bureau-bot[bot]", "agent-bureau-qa-bot"))
        self.assertFalse(framework.same_bot("", "agent-bureau-qa-bot"))


class SweepTest(unittest.TestCase):
    def test_sweep_removes_only_harness_leftovers(self):
        from test_harness_bot_pr_flow import FakeGitHub  # shared fake

        gh = FakeGitHub(default_branch="main")
        gh.branches["agent/harness-crashed-bot_pr_flow"] = SHA_A
        gh.branches["agent/DRE-500-real-work"] = SHA_B
        stale_pr = gh.seed_pr(head="agent/harness-crashed-bot_pr_flow")
        real_pr = gh.seed_pr(head="agent/DRE-500-real-work")
        # A crashed run that merged its probe but died before cleanup.
        gh.files[("main", "harness_runs/crashed-bot_pr_flow.md")] = "stale"

        # The sweep runs under the crashed run's OWN namespace (DRE-3075):
        # `crashed` is the leading segment of that run id.
        swept = framework.sweep_leftovers(
            gh, "o/r", "crashed", log=lambda *_: None
        )

        self.assertNotIn("agent/harness-crashed-bot_pr_flow", gh.branches)
        self.assertIn("agent/DRE-500-real-work", gh.branches)
        self.assertEqual(gh.prs[stale_pr]["state"], "closed")
        self.assertEqual(gh.prs[real_pr]["state"], "open")
        self.assertNotIn(("main", "harness_runs/crashed-bot_pr_flow.md"), gh.files)
        self.assertEqual(swept["branches_deleted"], 1)
        self.assertEqual(swept["prs_closed"], 1)
        self.assertEqual(swept["files_deleted"], 1)

    def test_sweep_on_a_clean_sandbox_is_a_noop(self):
        from test_harness_bot_pr_flow import FakeGitHub

        gh = FakeGitHub(default_branch="main")
        swept = framework.sweep_leftovers(gh, "o/r", "main", log=lambda *_: None)
        self.assertEqual(
            swept, {"branches_deleted": 0, "prs_closed": 0, "files_deleted": 0}
        )

    def test_sweep_covers_dependabot_named_leftovers_but_never_real_ones(self):
        from test_harness_bot_pr_flow import FakeGitHub

        gh = FakeGitHub(default_branch="main")
        gh.branches["dependabot/harness-crashed-gate_paths-named"] = SHA_A
        gh.branches["dependabot/pip/pytest-9.1.1"] = SHA_B
        crashed = gh.seed_pr(head="dependabot/harness-crashed-gate_paths-named")
        real = gh.seed_pr(head="dependabot/pip/pytest-9.1.1")

        swept = framework.sweep_leftovers(
            gh, "o/r", "crashed", log=lambda *_: None
        )

        self.assertNotIn("dependabot/harness-crashed-gate_paths-named", gh.branches)
        self.assertIn("dependabot/pip/pytest-9.1.1", gh.branches)
        self.assertEqual(gh.prs[crashed]["state"], "closed")
        self.assertEqual(gh.prs[real]["state"], "open")
        self.assertEqual(swept["branches_deleted"], 1)
        self.assertEqual(swept["prs_closed"], 1)


if __name__ == "__main__":
    unittest.main()
