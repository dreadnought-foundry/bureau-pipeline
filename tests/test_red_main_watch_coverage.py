"""Every workflow that can go red on main has a watcher (DRE-2820).

Origin (live, 2026-08-29): `self-red-main-repair.yml` watched ONE workflow by
name — `Pipeline Tests`. DRE-2726 had shipped the lane-contract harness as its
OWN workflow (`harness.yml` / "Integration Harness"), and nothing put it on the
repair rail. The harness went red on `main` at ~02:55 and stayed red all day;
every Red-Main Repair run that day concluded `skipped`, including the one three
minutes after the harness failed. Two approved PRs (#199, #201) inherited a
failure neither had caused, and the cause — two stale entries in
`config/lane-contract.json`, named in plain words by the harness log the whole
time — was found only because one PR's agent refused to push and read the log.

The one-line fix (add `Integration Harness` to the list) is not the card: a
hardcoded list is what created this, and the next workflow added would repeat
it silently. So the watched set is DERIVED from the workflow files and
ASSERTED — `scripts/check_workflow_watchers.py`, a Pipeline Tests step:

  * every workflow that validates a commit on the default branch (a `push`
    trigger reaching `main`) must be watched by the Red-Main Repair caller;
  * every workflow that can RUN on the default branch at all must be watched
    by SOME watcher — the repair rail for a red commit, the medic for a
    crashed run — so adding an unwatched workflow fails CI rather than
    passing quietly;
  * the medic is the one declared exemption, because it is the terminal
    watcher: a medic that watched itself is the crash-loop the ADR forbids.

The live case is the fixture: `HarnessFailureOnMainTest` replays the 2026-08-29
event — Integration Harness, conclusion=failure, head_branch=main — through the
stub's trigger list, the reusable's job gate, and the real decision code, and
asserts a repair is dispatched instead of skipped.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF_DIR = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))

import check_workflow_watchers as cww  # noqa: E402
import red_main_repair  # noqa: E402

REPAIR_STUB = "self-red-main-repair.yml"
HARNESS_WORKFLOW_NAME = "Integration Harness"

# The 2026-08-29 failure, VERBATIM from run 33233917939's `--log-failed`
# output (Integration Harness, push, main, 04:28:55Z). It named both stale
# lanes and the file to edit for fourteen hours while nothing read it. A real
# config failure, NOT an infra fingerprint, so the classifier must let it
# through to a repair agent rather than back off.
HARNESS_LANE_CONTRACT_LOG = """\
[lane_contract] verify
[lane_contract] 16 asserted, 2 failed, 36 skipped (phase not shipped), 1 unevaluated
  [FAIL       ] board.retired_entry_is_deleted: the contract still carries the retiring entry 'In Design Review', and Linear no longer has that state — the board caught up; delete the entry from config/lane-contract.json
  [FAIL       ] board.retired_entry_is_deleted: the contract still carries the retiring entry 'In QA', and Linear no longer has that state — the board caught up; delete the entry from config/lane-contract.json
  lane_contract: FAIL at verify
    - verify: ScenarioFailure: the live system does not match the lane contract:
"""


def doc(name: str) -> dict:
    return yaml.safe_load((WF_DIR / name).read_text())


def watched_by_stub() -> list:
    on = cww.on_block(doc(REPAIR_STUB))
    return list((on.get("workflow_run") or {}).get("workflows") or [])


class DefaultBranchTriggerTest(unittest.TestCase):
    """The derivation, on synthetic triggers — each violation class."""

    def test_push_to_main_validates_the_default_branch(self):
        self.assertTrue(
            cww.validates_default_branch({"push": {"branches": ["main"]}})
        )

    def test_bare_push_reaches_every_branch_including_main(self):
        self.assertTrue(cww.validates_default_branch({"push": None}))

    def test_push_of_tags_only_does_not_validate_main(self):
        # release-gate.yml: a `v*` tag push carries the TAG as head_branch.
        self.assertFalse(
            cww.validates_default_branch({"push": {"tags": ["v*", "stable"]}})
        )

    def test_push_to_other_branches_only_does_not_validate_main(self):
        self.assertFalse(
            cww.validates_default_branch({"push": {"branches": ["release/*"]}})
        )

    def test_branch_glob_matching_main_validates_it(self):
        self.assertTrue(
            cww.validates_default_branch({"push": {"branches": ["ma*"]}})
        )

    def test_pull_request_alone_never_runs_on_the_default_branch(self):
        # A pull_request run's head_branch is the PR's branch, never main.
        self.assertFalse(cww.runs_on_default_branch({"pull_request": None}))

    def test_schedule_dispatch_and_workflow_run_all_run_on_main(self):
        for trigger in (
            {"schedule": [{"cron": "*/15 * * * *"}]},
            {"workflow_dispatch": None},
            {"repository_dispatch": {"types": ["agent-execute"]}},
            {"workflow_run": {"workflows": ["Pipeline Tests"]}},
            {"issue_comment": {"types": ["created"]}},
        ):
            self.assertTrue(
                cww.runs_on_default_branch(trigger),
                f"{trigger} produces runs on the default branch",
            )

    def test_reusable_workflows_never_run_on_their_own(self):
        reusable = {"workflow_call": {"inputs": {}}}
        self.assertTrue(cww.is_reusable_only(reusable))
        self.assertFalse(cww.runs_on_default_branch(reusable))


class LiveCoverageTest(unittest.TestCase):
    """The repo's own workflows, enumerated — never a remembered list."""

    def setUp(self):
        self.violations, self.stats = cww.check_dir(WF_DIR)

    def test_the_integration_harness_is_on_the_repair_rail(self):
        # The one line the incident turned on.
        self.assertIn(HARNESS_WORKFLOW_NAME, watched_by_stub())

    def test_every_default_branch_validator_is_watched_by_repair(self):
        # Derived from the files: whatever runs on a push to main must be on
        # the rail, whether or not anyone remembered to add it.
        watched = watched_by_stub()
        validators = [
            wf.name for wf in cww.load_workflows(WF_DIR)
            if cww.validates_default_branch(wf.on)
        ]
        self.assertGreaterEqual(
            len(validators), 2,
            "expected at least Pipeline Tests and Integration Harness",
        )
        for name in validators:
            self.assertIn(
                name, watched,
                f"{name} runs on a push to main and can go red there, but "
                f"{REPAIR_STUB} does not watch it",
            )

    def test_the_live_repo_passes_the_checker(self):
        self.assertEqual(
            [], self.violations,
            "workflow watcher coverage is incomplete:\n"
            + "\n".join(self.violations),
        )

    def test_the_checker_is_not_vacuous_on_this_repo(self):
        self.assertGreater(self.stats["workflows"], 10)
        self.assertGreaterEqual(self.stats["default_branch_validators"], 2)
        self.assertGreater(self.stats["main_capable"], 5)
        self.assertGreater(self.stats["watchers"], 1)

    def test_repair_still_does_not_watch_itself(self):
        # ADR guardrail 2 survives the widening: a repair run's own failure is
        # the medic's job, never a second repair.
        self.assertNotIn("Red-Main Repair", watched_by_stub())

    def test_the_medic_is_the_declared_terminal_watcher(self):
        # The single exemption, stated in data with its reason — nothing
        # watches the medic, deliberately, and the checker says why.
        exempt = cww.terminal_watchers(WF_DIR)
        self.assertEqual(["Pipeline Medic"], sorted(exempt))
        self.assertIn("crash", cww.TERMINAL_WATCHER_REASON.lower())

    def test_the_checker_runs_as_a_pipeline_tests_step(self):
        # Criterion: an unwatched workflow fails CI rather than passing
        # quietly — which needs the checker wired into the suite.
        tests_yml = (WF_DIR / "tests.yml").read_text()
        self.assertIn("check_workflow_watchers.py", tests_yml)


class NewWorkflowCannotSlipThroughTest(unittest.TestCase):
    """The regression guard the card asks for: adding a workflow with no
    watcher must FAIL, and the failure must name it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dir = self.tmp / "workflows"
        shutil.copytree(WF_DIR, self.dir)
        self.addCleanup(shutil.rmtree, self.tmp)

    def _write(self, filename: str, body: str):
        (self.dir / filename).write_text(body)

    def test_a_new_push_to_main_workflow_is_reported_unwatched(self):
        self._write(
            "new-gate.yml",
            "name: Brand New Gate\n"
            "on:\n  push:\n    branches: [main]\n"
            "jobs:\n  go:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: 'true'\n",
        )
        violations, _ = cww.check_dir(self.dir)
        self.assertTrue(violations, "an unwatched red-main workflow must fail")
        self.assertTrue(
            any("Brand New Gate" in v for v in violations),
            f"the failure must name the workflow: {violations}",
        )
        self.assertEqual(1, cww.main([str(self.dir)]), "must exit non-zero")

    def test_adding_it_to_the_repair_rail_clears_the_finding(self):
        self._write(
            "new-gate.yml",
            "name: Brand New Gate\n"
            "on:\n  push:\n    branches: [main]\n"
            "jobs:\n  go:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: 'true'\n",
        )
        stub = (self.dir / REPAIR_STUB).read_text().replace(
            f"workflows: [Pipeline Tests, {HARNESS_WORKFLOW_NAME}]",
            f"workflows: [Pipeline Tests, {HARNESS_WORKFLOW_NAME}, "
            "Brand New Gate]",
        )
        (self.dir / REPAIR_STUB).write_text(stub)
        # The medic must also see it — a red commit AND a crashed run.
        medic = (self.dir / "self-medic.yml").read_text().replace(
            "Channel Watch]", "Channel Watch, Brand New Gate]"
        )
        (self.dir / "self-medic.yml").write_text(medic)
        violations, _ = cww.check_dir(self.dir)
        self.assertEqual([], violations, f"unexpected: {violations}")

    def test_a_new_scheduled_workflow_needs_a_watcher_too(self):
        # Not a red-main validator, but it can still go red on main and no
        # rail would carry it — the third instance of the two-gating-layers
        # hazard the card is about.
        self._write(
            "nightly-sweep.yml",
            "name: Nightly Sweep\n"
            "on:\n  schedule:\n    - cron: '0 3 * * *'\n"
            "jobs:\n  go:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: 'true'\n",
        )
        violations, _ = cww.check_dir(self.dir)
        self.assertTrue(
            any("Nightly Sweep" in v for v in violations),
            f"an unwatched scheduled workflow must be reported: {violations}",
        )

    def test_a_new_reusable_workflow_needs_no_watcher(self):
        # It cannot run by itself, so it cannot go red by itself.
        self._write(
            "brand-new-reusable.yml",
            "name: Brand New (reusable)\n"
            "on:\n  workflow_call:\n    inputs:\n      pipeline_ref:\n"
            "        type: string\n        required: true\n"
            "jobs:\n  go:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: 'true'\n",
        )
        violations, _ = cww.check_dir(self.dir)
        self.assertEqual([], violations, f"unexpected: {violations}")


class HarnessFailureOnMainTest(unittest.TestCase):
    """The live case, as the fixture: run 33233917939 — Integration Harness,
    push, main, conclusion=failure at 2026-08-29T04:28:55Z, head sha
    494d943e…, actor agent-bureau-qa-bot[bot] (the merge of PR #200),
    triggering actor github-actions[bot] (a rerun). The Red-Main Repair run
    that followed at 04:31:24Z concluded `skipped`.

    Every joint that event passes through, asserted."""

    FAILING_SHA = "494d943e43e90a29357fde700220101e8647357f"
    ACTORS = ("agent-bureau-qa-bot", "github-actions")

    def _event(self):
        return {
            "workflow_run": {
                "name": HARNESS_WORKFLOW_NAME,
                "conclusion": "failure",
                "head_branch": "main",
                "head_sha": self.FAILING_SHA,
                "event": "push",
            },
            "repository": {"default_branch": "main"},
        }

    def test_the_repair_agent_admits_the_events_actors(self):
        # Premortem Q1 on the widened trigger, against the recorded actors: a
        # trigger that fires into an agent step that refuses the actor would
        # trade a silent skip for a red crash.
        reusable = (WF_DIR / "red-main-repair.yml").read_text()
        line = next(ln for ln in reusable.splitlines()
                    if "allowed_bots:" in ln)
        for actor in self.ACTORS:
            self.assertIn(actor, line, f"{actor} fired this event")

    def test_the_stub_trigger_admits_the_event(self):
        # This is the joint that failed: the event fired, and the stub's
        # workflow_run list did not name it, so the run concluded `skipped`.
        event = self._event()
        self.assertIn(event["workflow_run"]["name"], watched_by_stub())
        self.assertEqual(
            ["completed"],
            (cww.on_block(doc(REPAIR_STUB))["workflow_run"]).get("types"),
        )

    def test_the_reusable_job_gate_opens_for_it(self):
        event = self._event()
        run = event["workflow_run"]
        self.assertEqual("failure", run["conclusion"])
        self.assertEqual(
            event["repository"]["default_branch"], run["head_branch"],
            "the job `if` requires the failure to be on the default branch",
        )

    def test_the_decision_dispatches_a_repair(self):
        event = self._event()
        run = event["workflow_run"]
        decision = red_main_repair.decide(
            conclusion=run["conclusion"],
            head_branch=run["head_branch"],
            default_branch=event["repository"]["default_branch"],
            head_sha=run["head_sha"],
            log_text=HARNESS_LANE_CONTRACT_LOG,
            refs=[],
            pulls=[],
        )
        self.assertTrue(
            decision["go"],
            f"the harness failure must engage the repair loop: {decision}",
        )
        self.assertEqual(f"repair/{self.FAILING_SHA}", decision["branch"])
        self.assertEqual(1, decision["attempt"])


if __name__ == "__main__":
    unittest.main()
