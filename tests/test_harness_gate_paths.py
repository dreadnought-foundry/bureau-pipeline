"""RED-first tests for the gate_paths harness scenario (DRE-2100).

gate_paths proves the merge gate's semantics against real GitHub with
three synthesized PRs plus an opportunistic look at the real Dependabot
PR:

  * SKEW-GUARD (DRE-1924/2037): a behind-base PR gets update-branch
    performed AS the qa-bot, and the resulting synchronize actor passes
    the allowlists — the re-review completes with a fresh bound verdict
    and the PR merges (no lockout);
  * HUMAN PATH (DRE-2039): a worker-authored PR on a dependabot-named
    branch is `human` — the gate posts the honest waiting-for-human state
    exactly ONCE and never touches the PR (no update-branch, no merge);
  * VERDICT BINDING (DRE-1990): a push right after a bound APPROVE makes
    that verdict stale — the gate must NOT merge until a fresh verdict
    binds the new head;
  * REAL-PR POSTURE: when the sandbox's genuine Dependabot PR is
    observable, its gate arm (major/unprovable → human once + untouched;
    provable minor/patch + bound APPROVE → auto-merge) is asserted too.

The LIVE scenario mocks nothing GitHub-side; this suite drives its LOGIC
against the shared FakeGitHub, with a per-leg driver standing in for the
sandbox's real critic + gate. The waiting-for-human marker the scenario
greps for is PINNED to merge-gate.yml's own literal.

These tests must FAIL before the scenario exists, and PASS after.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import merge_gate  # noqa: E402
from harness import framework  # noqa: E402
from harness import scenarios  # noqa: E402
from harness.scenarios import gate_paths  # noqa: E402
from test_harness_bot_pr_flow import QA, WORKER, FakeGitHub, _FakeTime  # noqa: E402

DEPENDABOT = "dependabot[bot]"
REAL_BRANCH = "dependabot/pip/requests-2.32.5"
MERGE_GATE_YML = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "merge-gate.yml"
)


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


def _find(gh, fragment):
    """The PR whose head ref contains `fragment` (driver-side lookup)."""
    for pr in gh.prs.values():
        if fragment in pr["head"]["ref"]:
            return pr
    return None


class LegDriver:
    """Stands in for the sandbox's critic + merge gate, one state machine
    per leg. Modes select the behavior under test; 'happy' is what the
    real pipeline promises."""

    def __init__(self, stale="happy", skew="happy", named="happy", real=None):
        self.modes = {"stale": stale, "skew": skew, "named": named, "real": real}
        self.state = {}

    def __call__(self, gh):
        self._drive_named(gh)
        self._drive_stale(gh)
        self._drive_skew(gh)
        self._drive_real(gh)

    # -- named: gate says human, once, hands off --------------------------
    def _drive_named(self, gh):
        mode = self.modes["named"]
        pr = _find(gh, "-named")
        if pr is None or pr["state"] != "open":
            return
        n = pr["number"]
        if not self.state.get("named_wait"):
            gh.post_human_wait(n)
            if mode == "spam":
                gh.post_human_wait(n)
            self.state["named_wait"] = True
            return
        if not self.state.get("named_verdict"):
            # The critic reviews dependabot/** branches too — its comment
            # is the second gate wake.
            gh.post_verdict(n, "REQUEST_CHANGES", pr["head"]["sha"])
            self.state["named_verdict"] = True
            if mode == "touch":
                gh.gate_update_branch(n)
            elif mode == "merge":
                gh.merge_as(n, QA)

    # -- stale: APPROVE, then hold until a fresh bound verdict ------------
    def _drive_stale(self, gh):
        mode = self.modes["stale"]
        pr = _find(gh, "-stale")
        if pr is None or pr["merged"] or pr["state"] != "open":
            return
        n, ref = pr["number"], pr["head"]["ref"]
        if "stale_sha1" not in self.state:
            sha1 = gh.branches[ref]
            gh.post_verdict(n, "APPROVE", sha1)
            self.state["stale_sha1"] = sha1
            return
        sha1 = self.state["stale_sha1"]
        if mode == "insta_merge":
            # The gate wins the race: merge at sha1 before the harness's
            # push lands (branch deleted with it).
            gh.merge_as(n, QA)
            return
        if gh.branches.get(ref, sha1) == sha1:
            return  # waiting for the harness's stale-making push
        if mode == "merge_stale":
            # THE regression: merging on the stale APPROVE after a push.
            gh.merge_as(n, QA)
            return
        if not self.state.get("stale_verdict2"):
            gh.post_verdict(n, "APPROVE", gh.branches[ref])
            self.state["stale_verdict2"] = True
            return
        gh.merge_as(n, QA)

    # -- skew: update as qa-bot, fresh verdict, merge ---------------------
    def _drive_skew(self, gh):
        mode = self.modes["skew"]
        pr = _find(gh, "-skew")
        if pr is None or pr["merged"] or pr["state"] != "open":
            return
        n, ref = pr["number"], pr["head"]["ref"]
        step = self.state.get("skew_step", 0)
        if step == 0:
            updater = WORKER if mode == "wrong_updater" else QA
            gh.gate_update_branch(n, login=updater)
            self.state["skew_step"] = 1
            return
        if mode == "no_verdict":
            return  # lockout: the synchronize review never completes
        if step == 1 and mode == "double_update":
            gh.gate_update_branch(n)
            self.state["skew_step"] = 2
            return
        if not self.state.get("skew_verdict"):
            gh.post_verdict(n, "APPROVE", gh.branches[ref])
            self.state["skew_verdict"] = True
            return
        gh.merge_as(n, QA)

    # -- real dependabot PR: gate posture ---------------------------------
    def _drive_real(self, gh):
        if self.modes["real"] != "auto_merge":
            return
        pr = _find(gh, REAL_BRANCH)
        if pr is None or pr["merged"] or pr["state"] != "open":
            return
        # Only merge once the harness is actually watching (a bound
        # APPROVE was seeded); mirrors the gate acting on its own wake.
        gh.merge_as(pr["number"], QA)


def _run(driver=None, gh=None):
    gh = gh or FakeGitHub()
    gh.on_poll = driver or LegDriver()
    result = framework.run_scenario(gate_paths.SCENARIO, _ctx(gh))
    return result, gh


def _seed_real_pr(gh, level, verdict, human_waits=1):
    sha = gh._new_sha()
    gh.branches[REAL_BRANCH] = sha
    gh._record_commit(sha, ["0" * 40], DEPENDABOT)
    number = gh.seed_pr(head=REAL_BRANCH, login=DEPENDABOT)
    gh.prs[number]["commits_payload"] = [
        {
            "sha": sha,
            "commit": {
                "message": f"bump\n\nupdate-type: version-update:semver-{level}"
            },
        }
    ]
    for _ in range(human_waits):
        gh.post_human_wait(number)
    if verdict:
        gh.post_verdict(number, verdict, sha)
    return number, sha


class DiscoveryTest(unittest.TestCase):
    def test_gate_paths_is_discovered_by_convention(self):
        self.assertIn("gate_paths", scenarios.discover())


class ContentSafetyTest(unittest.TestCase):
    def test_nothing_the_harness_posts_contains_a_gate_credential(self):
        # Verdict markers are approval credentials; the waiting-for-human
        # marker is the gate's idempotence key — emitting it would make
        # the gate think it already posted the state.
        texts = [gate_paths.base_advance_markdown("gha-9-9")]
        for leg in gate_paths.LEGS:
            texts += [
                gate_paths.probe_markdown("gha-9-9", leg),
                gate_paths.pr_title("gha-9-9", leg),
                gate_paths.pr_body("gha-9-9", leg),
            ]
        for text in texts:
            for marker in (
                "VERDICT:",
                "QA Critic",
                "QA Verifier",
                gate_paths.HUMAN_WAIT_MARKER,
            ):
                self.assertNotIn(marker, text)

    def test_probe_artifacts_carry_the_run_id(self):
        for leg in gate_paths.LEGS:
            self.assertIn("gha-9-9", gate_paths.probe_path("gha-9-9", leg))
            self.assertIn("gha-9-9", gate_paths.pr_title("gha-9-9", leg))
        self.assertIn("gha-9-9", gate_paths.base_advance_path("gha-9-9"))


class MarkerParityTest(unittest.TestCase):
    def test_human_wait_marker_is_merge_gates_own_literal(self):
        # The scenario greps for the exact string merge-gate.yml posts
        # (and uses as its own idempotence check) — drift here would make
        # the human-path assertions blind.
        self.assertIn(gate_paths.HUMAN_WAIT_MARKER, MERGE_GATE_YML.read_text())


class BranchShapeTest(unittest.TestCase):
    def test_legs_are_namespaced_and_sweepable(self):
        import should_review_pr

        for leg in ("skew", "stale"):
            branch = gate_paths.leg_branch("gha-1-1", leg)
            self.assertTrue(branch.startswith("agent/harness-"), branch)
            self.assertTrue(framework.is_harness_ref(branch))
            self.assertTrue(should_review_pr.should_review(branch))
            self.assertIsNone(should_review_pr.card_in_branch(branch))
        named = gate_paths.leg_branch("gha-1-1", "named")
        # dependabot-named so merge_gate condition D applies — but inside
        # the harness namespace so the sweep owns it and the genuine-PR
        # detector never mistakes it for Dependabot's.
        self.assertTrue(named.startswith("dependabot/harness-"), named)
        self.assertTrue(framework.is_harness_ref(named))


class HappyPathTest(unittest.TestCase):
    def test_full_flow_passes_and_leaves_the_sandbox_clean(self):
        result, gh = _run()
        self.assertTrue(result.ok, result.errors)

        skew, stale, named = (
            _find(gh, "-skew"), _find(gh, "-stale"), _find(gh, "-named"),
        )
        # Skew and stale merged by the qa-bot; named honestly parked, then
        # closed (not merged) by cleanup.
        for pr in (skew, stale):
            self.assertTrue(pr["merged"])
            self.assertEqual(pr["merged_by"]["login"], QA)
        self.assertFalse(named["merged"])
        self.assertEqual(named["state"], "closed")
        # Everything namespaced is gone: branches, probe files, the
        # base-advance file — and no open harness PRs remain.
        for prefix in framework.HARNESS_BRANCH_PREFIXES:
            self.assertEqual(gh.matching_refs("x", prefix), [])
        leftovers = [
            p for (branch, p) in gh.files
            if branch == "main" and p.startswith(framework.PROBE_DIR)
        ]
        self.assertEqual(leftovers, [])
        self.assertEqual(gh.list_open_prs("x"), [])

    def test_run_after_simulated_crash_sweeps_dependabot_named_leftovers(self):
        gh = FakeGitHub()
        stale_branch = "dependabot/harness-crashed-gate_paths-named"
        gh.branches[stale_branch] = gh._new_sha()
        gh.seed_pr(head=stale_branch)
        result, gh = _run(gh=gh)
        self.assertTrue(result.ok, result.errors)
        self.assertNotIn(stale_branch, gh.branches)


class StaleVerdictTest(unittest.TestCase):
    def test_merging_on_the_stale_approve_is_the_named_failure(self):
        result, _ = _run(LegDriver(stale="merge_stale"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")
        self.assertIn("stale", "\n".join(result.errors).lower())

    def test_gate_winning_the_race_fails_honestly_not_silently(self):
        # If the gate merges before the stale-making push lands, the
        # binding property was NOT exercised — the scenario must say so,
        # never pass on pretend coverage.
        result, _ = _run(LegDriver(stale="insta_merge"))
        self.assertFalse(result.ok)
        self.assertIn("race", "\n".join(result.errors).lower())


class SkewGuardTest(unittest.TestCase):
    def test_update_never_happening_times_out_as_a_failure(self):
        result, _ = _run(LegDriver(skew="no_update"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")

    def test_lockout_after_update_is_a_failure(self):
        # The DRE-2037 class: update-branch fired, but the qa-bot-actor
        # synchronize review never completes — no fresh verdict, no merge.
        result, _ = _run(LegDriver(skew="no_verdict"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")

    def test_update_by_the_wrong_identity_is_a_failure(self):
        result, _ = _run(LegDriver(skew="wrong_updater"))
        self.assertFalse(result.ok)
        self.assertIn("qa-bot", "\n".join(result.errors))

    def test_a_second_update_round_is_tolerated(self):
        # Base can move again mid-flow (the stale leg merges its probe);
        # the gate updates again — the chain walk must follow it.
        result, _ = _run(LegDriver(skew="double_update"))
        self.assertTrue(result.ok, result.errors)


class HumanPathTest(unittest.TestCase):
    def test_spammed_waiting_state_is_a_failure(self):
        result, _ = _run(LegDriver(named="spam"))
        self.assertFalse(result.ok)
        self.assertIn("once", "\n".join(result.errors).lower())

    def test_touching_the_named_pr_is_a_failure(self):
        result, _ = _run(LegDriver(named="touch"))
        self.assertFalse(result.ok)
        self.assertIn("touch", "\n".join(result.errors).lower())

    def test_merging_the_named_pr_is_a_failure(self):
        result, _ = _run(LegDriver(named="merge"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")


class RealPrPostureTest(unittest.TestCase):
    def test_major_arm_untouched_with_one_waiting_state_passes(self):
        gh = FakeGitHub()
        _seed_real_pr(gh, "major", "REQUEST_CHANGES")
        result, gh = _run(gh=gh)
        self.assertTrue(result.ok, result.errors)
        real = _find(gh, REAL_BRANCH)
        self.assertEqual(real["state"], "open")  # never closed by cleanup

    def test_major_arm_with_spammed_waiting_state_fails(self):
        gh = FakeGitHub()
        _seed_real_pr(gh, "major", "REQUEST_CHANGES", human_waits=2)
        result, _ = _run(gh=gh)
        self.assertFalse(result.ok)

    def test_minor_arm_auto_merges_on_bound_approve(self):
        gh = FakeGitHub()
        _seed_real_pr(gh, "patch", "APPROVE", human_waits=0)
        result, gh = _run(LegDriver(real="auto_merge"), gh=gh)
        self.assertTrue(result.ok, result.errors)
        real = _find(gh, REAL_BRANCH)
        self.assertTrue(real["merged"])
        self.assertEqual(real["merged_by"]["login"], QA)

    def test_no_real_pr_degrades_to_a_note_not_a_failure(self):
        # dependabot_flow owns enforcing the real PR's existence; this
        # posture check is opportunistic and must not double-fail.
        result, _ = _run()
        self.assertTrue(result.ok, result.errors)


if __name__ == "__main__":
    unittest.main()
