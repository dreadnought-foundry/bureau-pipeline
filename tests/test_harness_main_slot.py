"""RED-first tests for DRE-3075: main gets its own harness slot.

Observed 2026-09-03, 19:49–21:01 PT. `harness.yml` keyed BOTH its
`push: main` runs and its `pull_request` runs to one concurrency group,
`integration-harness-sandbox`. A PR's proving run held that slot for 18
minutes while five pushes to main queued behind it — GitHub keeps one
pending run per group, so each new push replaced the last. Main's first
turn started at 20:07 PT, the fleet ran the afternoon's code until 21:12
PT with eight front-door fixes already merged, and `stable` (which only
advances on a harness-proven commit) fell 50 commits behind.

Two runs cannot simply be let loose on one sandbox, and that is what made
this its own card: `framework.sweep_leftovers` deletes EVERY branch under
`agent/harness-` / `dependabot/harness-` and every file under
`harness-runs/`, whichever run left them — so a second run starting would
tear down the first one's live branches mid-scenario. Separate groups
alone would trade a queue for a demolition.

So both halves are pinned here:

  * **the slots** — a push to main and a pull request resolve to DIFFERENT
    concurrency groups, evaluated through the same expression evaluator
    GitHub uses (`scripts/fix_concurrency.py`), so main never waits on and
    is never evicted by a PR's run;
  * **the namespaces** — every run carries a namespace (`main`, `pr<n>`,
    `local`), every branch/probe file it creates sits under that
    namespace, and the sweep only ever collects its OWN namespace. A
    foreign leftover is collected only once it is older than a whole
    harness run can last, which is the one state in which it cannot belong
    to a live run.

These tests must FAIL against the single shared group + unscoped sweep,
and PASS after.

**Amended by DRE-4149 (2026-09-17).** The harness no longer runs on pull
requests, so there is no PR lane left to keep out of main's way: the
`pr-<number>` arm of the concurrency group went with the trigger, and the
five slot tests that compared a pull_request event against a push to main
went with the arm (they evaluated an expression that no longer exists;
tests/test_harness_proves_main.py pins what replaced them — one lane, and
no way for the PR trigger to come back quietly). What stays is everything
whose code stays: main's lane and the never-cancel rule, and the whole
namespace/sweep half — `framework` still accepts a `pr<n>` namespace and
still refuses to collect a live foreign one, which is what protects main's
run from a by-hand or local run beside it.

Run: python3 -m pytest tests/test_harness_main_slot.py -v
"""

import os
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fix_concurrency  # noqa: E402
from harness import framework  # noqa: E402

WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "harness.yml"
)

#: The one lane every run rides since DRE-4149 — main's.
MAIN_GROUP = "integration-harness-main"

SHA_A = "a" * 40
SHA_B = "b" * 40


def _doc():
    assert WORKFLOW.is_file(), f"missing {WORKFLOW.name}"
    return yaml.safe_load(WORKFLOW.read_text())


def _job(doc):
    return doc["jobs"]["harness"]


def _driver_env(doc):
    """The env of the step that runs the driver — where the namespace the
    sandbox is carved by has to arrive."""
    for step in _job(doc).get("steps") or []:
        if "python3 -m harness" in (step.get("run") or ""):
            return step.get("env") or {}
    raise AssertionError("no step runs the harness driver")


# -- the events harness.yml actually sees ----------------------------------


def push_to_main_event():
    """A merge landing on main: the run whose stamp `stable` rides on."""
    return {
        "github": {
            "event_name": "push",
            "ref": "refs/heads/main",
            "sha": SHA_A,
            "event": {"ref": "refs/heads/main"},
        },
        "inputs": {},
    }


def dispatch_event(pipeline_ref="main"):
    return {
        "github": {
            "event_name": "workflow_dispatch",
            "ref": "refs/heads/main",
            "event": {"inputs": {"pipeline_ref": pipeline_ref}},
        },
        "inputs": {"pipeline_ref": pipeline_ref, "scenarios": ""},
    }


class HarnessSlotTest(unittest.TestCase):
    """The concurrency half, evaluated the way GitHub evaluates it."""

    def test_a_push_to_main_resolves_to_mains_group(self):
        self.assertEqual(
            fix_concurrency.group(_doc(), push_to_main_event()), MAIN_GROUP
        )

    def test_a_hand_dispatch_rides_mains_lane(self):
        # A workflow_dispatch drives the sandbox under the `main` namespace
        # (it tests a pipeline_ref, defaulted to main), so it must SHARE
        # main's slot — two runs in one namespace would sweep each other.
        doc = _doc()
        self.assertEqual(fix_concurrency.group(doc, dispatch_event()), MAIN_GROUP)

    def test_neither_lane_cancels_a_run_in_progress(self):
        self.assertFalse(
            fix_concurrency.cancel_in_progress(_doc()),
            "cancelling mid-scenario strands sandbox state the sweep then owns",
        )


class NamespaceTest(unittest.TestCase):
    """The namespace token itself: what it may be, and why."""

    def test_the_live_namespaces_are_accepted(self):
        for ns in ("main", "pr251", framework.DEFAULT_NAMESPACE):
            self.assertEqual(framework.validate_namespace(ns), ns)

    def test_a_namespace_may_not_carry_the_delimiter(self):
        # `agent/harness-<ns>-…` separates the namespace from the run id
        # with a dash, so a dash INSIDE a namespace would let one
        # namespace's sweep prefix match another's branches. Refused at the
        # source rather than reasoned about per namespace pair.
        for bad in ("pr-251", "", "MAIN", "main/x", "a" * 25, None):
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                framework.validate_namespace(bad)

    def test_branch_prefixes_of_two_namespaces_never_match_each_other(self):
        main = framework.scenario_branch(
            framework.namespaced_run_id("main", "gha-1-1"), "bot_pr_flow"
        )
        pr = framework.scenario_branch(
            framework.namespaced_run_id("pr251", "gha-2-1"), "bot_pr_flow"
        )
        self.assertTrue(framework.is_own_harness_ref(main, "main"))
        self.assertTrue(framework.is_own_harness_ref(pr, "pr251"))
        self.assertFalse(framework.is_own_harness_ref(pr, "main"))
        self.assertFalse(framework.is_own_harness_ref(main, "pr251"))
        # …and both are still harness refs to everything that asks the
        # namespace-agnostic question (find_real_dependabot_pr).
        self.assertTrue(framework.is_harness_ref(main))
        self.assertTrue(framework.is_harness_ref(pr))

    def test_a_run_id_carries_its_namespace_and_the_prefixing_is_idempotent(self):
        self.assertEqual(framework.namespaced_run_id("main", "gha-1-1"), "main-gha-1-1")
        self.assertEqual(
            framework.namespaced_run_id("main", "main-gha-1-1"), "main-gha-1-1"
        )
        # The local default keeps the shape it always had.
        self.assertTrue(
            framework.namespaced_run_id(
                framework.DEFAULT_NAMESPACE, framework.new_run_id()
            ).startswith(f"{framework.DEFAULT_NAMESPACE}-")
        )

    def test_a_dependabot_named_probe_is_namespaced_too(self):
        named = framework.dependabot_scenario_branch(
            "pr251-gha-2-1", "gate_paths-named"
        )
        self.assertTrue(framework.is_own_harness_ref(named, "pr251"))
        self.assertFalse(framework.is_own_harness_ref(named, "main"))


def _iso(seconds_ago: float, now: float) -> str:
    from datetime import datetime, timezone

    return (
        datetime.fromtimestamp(now - seconds_ago, timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


class SweepIsolationTest(unittest.TestCase):
    """Two runs must never clean each other's branches (DRE-3075 AC 3)."""

    NOW = 1_757_000_000.0

    def _sandbox(self):
        """A sandbox holding a live main run's leftovers, a live PR run's
        leftovers, and real work that must survive either sweep."""
        from test_harness_bot_pr_flow import FakeGitHub  # shared fake

        gh = FakeGitHub(default_branch="main")
        fresh = _iso(60, self.NOW)
        for branch in (
            "agent/harness-main-gha-1-1-bot_pr_flow",
            "agent/harness-pr251-gha-2-1-bot_pr_flow",
            "dependabot/harness-pr251-gha-2-1-gate_paths-named",
        ):
            gh.branches[branch] = SHA_A
            gh.dates[branch] = fresh
            gh.seed_pr(head=branch, created_at=fresh)
        gh.branches["agent/DRE-500-real-work"] = SHA_B
        gh.seed_pr(head="agent/DRE-500-real-work", created_at=fresh)
        gh.branches["dependabot/pip/pytest-9.1.1"] = SHA_B
        gh.files[("main", f"{framework.PROBE_DIR}/main-gha-1-1-gate_paths-green.md")] = "x"
        gh.files[("main", f"{framework.PROBE_DIR}/pr251-gha-2-1-gate_paths-green.md")] = "x"
        for path in list(gh.files):
            gh.dates[("main", path[1])] = fresh
        return gh

    def _sweep(self, gh, namespace):
        return framework.sweep_leftovers(
            gh, "o/r", namespace, log=lambda *_: None, now=lambda: self.NOW
        )

    def _open_heads(self, gh):
        return {p["head"]["ref"] for p in gh.prs.values() if p["state"] == "open"}

    def test_a_main_run_leaves_a_live_pr_runs_leftovers_alone(self):
        gh = self._sandbox()
        swept = self._sweep(gh, "main")

        self.assertNotIn("agent/harness-main-gha-1-1-bot_pr_flow", gh.branches)
        self.assertIn("agent/harness-pr251-gha-2-1-bot_pr_flow", gh.branches)
        self.assertIn("dependabot/harness-pr251-gha-2-1-gate_paths-named", gh.branches)
        self.assertIn("agent/harness-pr251-gha-2-1-bot_pr_flow", self._open_heads(gh))
        self.assertIn(
            ("main", f"{framework.PROBE_DIR}/pr251-gha-2-1-gate_paths-green.md"),
            gh.files,
        )
        self.assertNotIn(
            ("main", f"{framework.PROBE_DIR}/main-gha-1-1-gate_paths-green.md"),
            gh.files,
        )
        self.assertEqual(swept["branches_deleted"], 1)
        self.assertEqual(swept["prs_closed"], 1)
        self.assertEqual(swept["files_deleted"], 1)

    def test_a_pr_run_leaves_a_live_main_runs_leftovers_alone(self):
        gh = self._sandbox()
        swept = self._sweep(gh, "pr251")

        self.assertIn("agent/harness-main-gha-1-1-bot_pr_flow", gh.branches)
        self.assertIn("agent/harness-main-gha-1-1-bot_pr_flow", self._open_heads(gh))
        self.assertNotIn("agent/harness-pr251-gha-2-1-bot_pr_flow", gh.branches)
        self.assertNotIn(
            "dependabot/harness-pr251-gha-2-1-gate_paths-named", gh.branches
        )
        self.assertIn(
            ("main", f"{framework.PROBE_DIR}/main-gha-1-1-gate_paths-green.md"),
            gh.files,
        )
        self.assertEqual(swept["branches_deleted"], 2)
        self.assertEqual(swept["prs_closed"], 2)
        self.assertEqual(swept["files_deleted"], 1)

    def test_neither_sweep_ever_touches_real_work(self):
        for namespace in ("main", "pr251"):
            gh = self._sandbox()
            self._sweep(gh, namespace)
            self.assertIn("agent/DRE-500-real-work", gh.branches)
            self.assertIn("dependabot/pip/pytest-9.1.1", gh.branches)
            self.assertIn("agent/DRE-500-real-work", self._open_heads(gh))

    def test_a_foreign_leftover_older_than_any_live_run_is_still_collected(self):
        # Scoping must not make a crashed run's mess immortal: a PR that
        # crashed and then merged leaves a namespace nothing will ever sweep
        # again. Older than a whole harness run can last = it cannot belong
        # to a live one.
        gh = self._sandbox()
        old = _iso(framework.STALE_LEFTOVER_SECONDS + 60, self.NOW)
        branch = "agent/harness-pr999-gha-9-1-bot_pr_flow"
        gh.branches[branch] = SHA_A
        gh.dates[branch] = old
        gh.seed_pr(head=branch, created_at=old)
        path = f"{framework.PROBE_DIR}/pr999-gha-9-1-gate_paths-green.md"
        gh.files[("main", path)] = "x"
        gh.dates[("main", path)] = old

        self._sweep(gh, "main")

        self.assertNotIn(branch, gh.branches)
        self.assertNotIn(branch, self._open_heads(gh))
        self.assertNotIn(("main", path), gh.files)

    def test_a_foreign_leftover_of_unknown_age_is_never_touched(self):
        # An unreadable date is not a licence to delete: the run it belongs
        # to may be live, and deleting its branch is the tear-down this card
        # exists to prevent.
        gh = self._sandbox()
        branch = "agent/harness-pr999-gha-9-1-bot_pr_flow"
        gh.branches[branch] = SHA_A  # no entry in gh.dates
        gh.seed_pr(head=branch)

        self._sweep(gh, "main")

        self.assertIn(branch, gh.branches)
        self.assertIn(branch, self._open_heads(gh))

    def test_the_legacy_probe_dir_is_still_swept_whatever_it_holds(self):
        # LEGACY_PROBE_DIRS are never written any more, so anything there
        # predates namespacing and belongs to no live run.
        gh = self._sandbox()
        gh.files[("main", "harness_runs/gha-old-gate_paths-base-advance.md")] = "x"

        self._sweep(gh, "pr251")

        self.assertNotIn(
            ("main", "harness_runs/gha-old-gate_paths-base-advance.md"), gh.files
        )


class ScenarioCleanupTest(unittest.TestCase):
    """A whole scenario, run while the OTHER lane is live in the sandbox.

    Scoping the sweep is only half of it: every scenario ends by asserting
    the sandbox is clean, and that assertion reading "no open harness PRs
    at all" would fail a healthy run the moment a second lane exists — the
    other run's PRs are open BECAUSE it is still using them.
    """

    def test_a_scenario_passes_beside_another_lanes_live_run(self):
        import test_harness_bot_pr_flow as bpf
        from harness.scenarios import bot_pr_flow

        gh = bpf.FakeGitHub()
        live = "agent/harness-pr251-gha-9-1-bot_pr_flow"
        gh.branches[live] = gh._new_sha()
        live_pr = gh.seed_pr(head=live)
        gh.files[("main", bot_pr_flow.probe_path("pr251-gha-9-1"))] = "live"

        gh.on_create_pr = bpf._happy_pipeline
        result = framework.run_scenario(bot_pr_flow.SCENARIO, bpf._ctx(gh, "gha-2-1"))

        self.assertTrue(result.ok, result.errors)
        self.assertIn(live, gh.branches, "the other lane's branch was deleted")
        self.assertEqual(gh.prs[live_pr]["state"], "open")
        self.assertIn(
            ("main", bot_pr_flow.probe_path("pr251-gha-9-1")),
            gh.files,
            "the other lane's probe file was deleted",
        )

    def test_leftover_pr_numbers_reports_only_this_runs_own(self):
        import test_harness_bot_pr_flow as bpf

        gh = bpf.FakeGitHub()
        mine = gh.seed_pr(head="agent/harness-main-gha-1-1-bot_pr_flow")
        gh.seed_pr(head="agent/harness-pr251-gha-9-1-bot_pr_flow")
        gh.seed_pr(head="agent/DRE-500-real-work")
        self.assertEqual(framework.leftover_pr_numbers(gh, "o/r", "main"), [mine])


class DriverWiringTest(unittest.TestCase):
    """The workflow has to hand the driver the SAME namespace it keys the
    concurrency group on — two expressions, one answer."""

    def test_the_driver_is_given_the_runs_namespace(self):
        env = _driver_env(_doc())
        self.assertIn(
            "HARNESS_NAMESPACE", env,
            "the driver cannot scope its sweep without the run's namespace",
        )

    def test_the_namespace_matches_the_slot_for_every_event(self):
        doc = _doc()
        expr = _driver_env(doc)["HARNESS_NAMESPACE"]
        for event, namespace, group in (
            (push_to_main_event(), "main", MAIN_GROUP),
            (dispatch_event(), "main", MAIN_GROUP),
        ):
            resolved = fix_concurrency.interpolate(expr, event)
            self.assertEqual(framework.validate_namespace(resolved), namespace)
            # The two spellings differ by one dash and nothing else: the
            # group is the card's, the namespace is the sandbox's (which
            # cannot carry the delimiter).
            self.assertEqual(
                fix_concurrency.group(doc, event).replace("-", ""),
                f"integrationharness{resolved}",
            )
            self.assertEqual(fix_concurrency.group(doc, event), group)

    def test_the_run_id_the_workflow_passes_is_still_branch_safe(self):
        env = _driver_env(_doc())
        rendered = fix_concurrency.interpolate(
            env["HARNESS_RUN_ID"],
            {"github": {"run_id": 33274348041, "run_attempt": 1}},
        )
        framework.validate_run_id(
            framework.namespaced_run_id("pr251", rendered)
        )

    def test_the_stale_cutoff_outlives_the_jobs_own_ceiling(self):
        # The one property that makes collecting a foreign leftover safe: a
        # run cannot outlive its own job timeout, so anything older than that
        # ceiling belongs to no live run. Pinned to the workflow, because
        # raising the timeout without this would silently re-open the race.
        cap_seconds = int(_job(_doc())["timeout-minutes"]) * 60
        self.assertGreaterEqual(framework.STALE_LEFTOVER_SECONDS, 2 * cap_seconds)


if __name__ == "__main__":
    unittest.main()
