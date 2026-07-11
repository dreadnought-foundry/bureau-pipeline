"""Self-host consumer stubs (DRE-1929 — "agents author, human promotes").

bureau-pipeline goes on the dispatch rail like any product repo: thin trigger
stubs call this repo's OWN reusable workflows at the fully qualified @main ref
(this repo IS the canary channel — the fleet consumes human-promoted tags, so
a merge to main here changes nothing live; see adr-bureau-pipeline-self-host).

These tests LIVE-EXTRACT the stub YAML (no fixtures, no copies) and pin:

  * every expected stub exists, calls exactly its reusable workflow via the
    FULLY QUALIFIED dreadnought-foundry/... @main form (a local `./` ref would
    resolve from the PR's merge ref, letting a PR choose the logic that
    reviews/merges it — the pr-review.yml security note generalized), and
    carries `secrets: inherit`;
  * stub trigger shapes match the fleet reference (agent-bureau's stub set);
  * the DRE-2028 lesson, structurally: every workflow that runs checks on
    open PRs is named in BOTH the merge-gate and medic watch lists, every
    watched name resolves to a real workflow in this repo (no dangling
    entries after a rename), and the medic also watches every pipeline stage;
  * exactly ONE workflow reviews PRs with the critic (pr-review.yml is the
    qa-review stub — the new stub set must not double-review);
  * stub `name:` values never collide with the reusable definitions' names
    (workflow_run matches on names — a collision poisons the watch lists).
"""

import unittest
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PIPELINE = "dreadnought-foundry/bureau-pipeline"


def _load(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.is_file(), f"missing stub {path.name}"
    return yaml.safe_load(path.read_text())


def _on(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _all_workflows() -> dict[str, dict]:
    return {p.name: yaml.safe_load(p.read_text()) for p in sorted(WORKFLOWS.glob("*.yml"))}


def _call_job(doc: dict) -> dict:
    jobs = doc.get("jobs") or {}
    assert len(jobs) == 1, "a stub has exactly one job"
    return next(iter(jobs.values()))


# stub file -> (reusable workflow file it must call, required workflow name)
EXPECTED_STUBS = {
    "self-agent-task.yml": ("agent-task.yml", "Agent Task"),
    "self-plan.yml": ("plan.yml", "Agent Plan"),
    "self-merge-gate.yml": ("merge-gate.yml", "Merge Gate"),
    "self-medic.yml": ("medic.yml", "Pipeline Medic"),
    "self-reconcile.yml": ("reconcile.yml", "Reconcile"),
    "self-linear-sync.yml": ("linear-sync.yml", "Linear Sync"),
    "self-agent-fix.yml": ("agent-fix.yml", "Agent Fix"),
    "self-red-main-repair.yml": ("red-main-repair.yml", "Red-Main Repair"),
    "pr-review.yml": ("qa-review.yml", "QA Review"),
    # verify.yml deliberately has NO stub: its scope gate targets UI cards
    # (**Design:** lines) / multi-system app diffs, and bureau-pipeline has no
    # runnable app surface to verify behaviorally.
}


class StubCallsReusableTest(unittest.TestCase):
    def test_every_stub_calls_its_reusable_at_qualified_main(self):
        for stub, (reusable, _) in EXPECTED_STUBS.items():
            doc = _load(stub)
            job = _call_job(doc)
            self.assertEqual(
                job.get("uses"),
                f"{PIPELINE}/.github/workflows/{reusable}@main",
                f"{stub} must call its reusable workflow via the fully "
                f"qualified @main ref (never a local ./ reference)",
            )

    def test_every_stub_inherits_secrets(self):
        for stub in EXPECTED_STUBS:
            job = _call_job(_load(stub))
            self.assertEqual(
                job.get("secrets"), "inherit",
                f"{stub} must carry `secrets: inherit` — the reusable "
                f"workflows read LINEAR_API_KEY / bot app keys from the "
                f"caller's secrets",
            )

    def test_stub_names_match_the_fleet_reference(self):
        for stub, (_, name) in EXPECTED_STUBS.items():
            self.assertEqual(
                _load(stub).get("name"), name,
                f"{stub} must be named {name!r} — the medic/merge-gate "
                f"workflow_run watch lists match on this name fleet-wide",
            )

    def test_stub_names_do_not_collide_with_reusable_names(self):
        stub_names = {_load(s).get("name") for s in EXPECTED_STUBS}
        for fname, doc in _all_workflows().items():
            if "workflow_call" in _on(doc):
                self.assertNotIn(
                    doc.get("name"), stub_names,
                    f"reusable {fname} shares a workflow name with a stub — "
                    f"workflow_run watch lists would match both",
                )


class TriggerShapeTest(unittest.TestCase):
    def test_agent_task_listens_for_agent_execute_dispatch(self):
        on = _on(_load("self-agent-task.yml"))
        self.assertEqual(on.get("repository_dispatch", {}).get("types"), ["agent-execute"])

    def test_plan_listens_for_agent_plan_dispatch(self):
        on = _on(_load("self-plan.yml"))
        self.assertEqual(on.get("repository_dispatch", {}).get("types"), ["agent-plan"])

    def test_reconcile_runs_on_schedule(self):
        on = _on(_load("self-reconcile.yml"))
        self.assertTrue(on.get("schedule"), "reconcile stub must carry a cron schedule")

    def test_linear_sync_fires_on_pr_close(self):
        on = _on(_load("self-linear-sync.yml"))
        self.assertEqual(on.get("pull_request", {}).get("types"), ["closed"])

    def test_merge_gate_hears_checks_comments_and_dispatch(self):
        on = _on(_load("self-merge-gate.yml"))
        self.assertIn("workflow_run", on)
        self.assertIn("issue_comment", on)
        self.assertIn("workflow_dispatch", on)

    def test_merge_gate_can_dispatch_agent_fix(self):
        # The qa-bot App has no Actions perms; the stub's github.token does the
        # DIRTY-branch agent-fix dispatch — same as the fleet reference.
        doc = _load("self-merge-gate.yml")
        self.assertEqual((doc.get("permissions") or {}).get("actions"), "write")


class WatchListTest(unittest.TestCase):
    """DRE-2028: a pull_request check workflow absent from the merge-gate /
    medic watch lists means the gate never re-evaluates when that check
    finishes and the medic never sees it fail — silently, per repo."""

    @staticmethod
    def _watch(stub: str) -> list[str]:
        wr = _on(_load(stub)).get("workflow_run") or {}
        return wr.get("workflows") or []

    @staticmethod
    def _pr_check_workflow_names() -> set[str]:
        """Names of every workflow that runs checks on OPEN PRs: a
        pull_request trigger whose types (default = opened/synchronize/
        reopened) include any open-PR activity. closed-only workflows (the
        linear-sync stub) never gate a merge."""
        names = set()
        for fname, doc in _all_workflows().items():
            on = _on(doc)
            if "pull_request" not in on:
                continue
            pr = on.get("pull_request") or {}
            types = pr.get("types") if isinstance(pr, dict) else None
            if types and set(types) <= {"closed"}:
                continue
            names.add(doc.get("name") or fname)
        return names

    def test_merge_gate_watches_every_pr_check_workflow(self):
        watched = set(self._watch("self-merge-gate.yml"))
        for name in self._pr_check_workflow_names():
            self.assertIn(
                name, watched,
                f"pull_request workflow {name!r} is missing from the "
                f"merge-gate watch list (DRE-2028) — the gate would never "
                f"re-evaluate when it completes",
            )

    def test_medic_watches_every_pr_check_workflow(self):
        watched = set(self._watch("self-medic.yml"))
        for name in self._pr_check_workflow_names():
            self.assertIn(
                name, watched,
                f"pull_request workflow {name!r} is missing from the medic "
                f"watch list (DRE-2028) — its failures would never be "
                f"diagnosed",
            )

    def test_medic_watches_every_pipeline_stage(self):
        watched = set(self._watch("self-medic.yml"))
        for stage in ("Agent Task", "Agent Plan", "Merge Gate", "Linear Sync"):
            self.assertIn(stage, watched, f"medic must watch the {stage} stage")

    def test_medic_does_not_watch_itself(self):
        self.assertNotIn("Pipeline Medic", self._watch("self-medic.yml"))

    def test_no_dangling_watch_entries(self):
        """Every watched name must be a real workflow name here — a rename
        that forgets the watch lists is the DRE-2028 failure in reverse."""
        real = {doc.get("name") for doc in _all_workflows().values()}
        for stub in ("self-merge-gate.yml", "self-medic.yml"):
            for name in self._watch(stub):
                self.assertIn(
                    name, real,
                    f"{stub} watches {name!r}, which no workflow in this "
                    f"repo is named — stale after a rename?",
                )


class SingleCriticTest(unittest.TestCase):
    def test_exactly_one_workflow_reviews_prs(self):
        """pr-review.yml IS the qa-review stub. The self-host stub set must
        not add a second caller on pull_request, or every PR gets two
        competing critic runs and verdicts."""
        reviewers = []
        for fname, doc in _all_workflows().items():
            on = _on(doc)
            if "workflow_call" in on or "pull_request" not in on:
                continue
            job = (doc.get("jobs") or {}).get("call") or {}
            if (job.get("uses") or "").startswith(
                f"{PIPELINE}/.github/workflows/qa-review.yml"
            ):
                reviewers.append(fname)
        self.assertEqual(
            reviewers, ["pr-review.yml"],
            f"exactly pr-review.yml must run the critic on PRs, got {reviewers}",
        )


if __name__ == "__main__":
    unittest.main()
