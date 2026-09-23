"""The repair loop's memory, fetched rather than assumed (DRE-4674).

`scripts/repair_history.py` is the gather half of the repeated-timeout rule:
before `red_main_repair.py decide` runs, it writes `/tmp/repair-history.json`
with the runs `decide` is allowed to compare this one against —

  * the 3 most recent COMPLETED runs of the same workflow on the default
    branch, older than this one;
  * this run's PREVIOUS ATTEMPT, when `run_attempt > 1`;
  * for each, the jobs, their steps, and that run's failed-step log.

Two properties carry the whole design:

  * **A failed read is "no history", never "repeated".** Everything here is
    best effort — main is already red, and a gather that cannot answer must
    land the decision exactly where it lands today (`infra-backoff`), never
    invent a repeat. Nothing in this module raises.
  * **The event's own facts describe the current run.** Its workflow path,
    head sha and attempt come from the workflow_run payload the caller was
    handed; only the jobs and the log are fetched.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

import repair_history  # noqa: E402

REPO = "dreadnought-foundry/portico"
WF_ID = 90210
RUN_ID = 35400000001
SHA = "a" * 40
PRIOR_SHAS = ["b" * 40, "c" * 40, "d" * 40, "e" * 40]
WF_PATH = ".github/workflows/ci.yml"

JOBS = {"jobs": [{
    "id": 1,
    "name": "infra — typecheck & test",
    "conclusion": "failure",
    "steps": [
        {"name": "Checkout", "conclusion": "success", "number": 1},
        {"name": "Test", "conclusion": "failure", "number": 2},
    ],
}]}

LOG = ("infra — typecheck & test\tTest\t2026-09-22T20:04:07Z "
       "##[error]The action 'Test' has timed out after 12 minutes\n")


class FakeGh:
    """The GitHub reads this module makes, and nothing else."""

    def __init__(self, *, runs=None, jobs=None, logs=None, raises=()):
        self.runs = runs if runs is not None else [
            {"id": RUN_ID - 1 - n, "path": WF_PATH, "head_sha": sha,
             "run_attempt": 1, "status": "completed", "conclusion": "failure"}
            for n, sha in enumerate(PRIOR_SHAS)
        ]
        self.jobs = jobs if jobs is not None else {}
        self.logs = logs if logs is not None else {}
        self.raises = set(raises)
        self.calls = []

    def completed_runs(self, workflow_id, branch, per_page):
        self.calls.append(("runs", workflow_id, branch, per_page))
        if "runs" in self.raises:
            raise RuntimeError("gh api failed rc=1")
        return self.runs

    def jobs_of(self, run_id, attempt=None):
        self.calls.append(("jobs", run_id, attempt))
        if ("jobs", run_id, attempt) in self.raises or "jobs" in self.raises:
            raise RuntimeError("gh api failed rc=1")
        return self.jobs.get((run_id, attempt), JOBS)

    def failed_log(self, run_id, attempt=None):
        self.calls.append(("log", run_id, attempt))
        if "log" in self.raises:
            raise RuntimeError("gh run view failed rc=1")
        return self.logs.get((run_id, attempt), LOG)


def _gather(gh, **overrides):
    kwargs = dict(
        repo=REPO, workflow_id=WF_ID, run_id=RUN_ID, run_attempt=1,
        branch="main", workflow_path=WF_PATH, head_sha=SHA,
        current_log=LOG, gh=gh,
    )
    kwargs.update(overrides)
    return repair_history.gather(**kwargs)


class DocumentShapeTest(unittest.TestCase):
    def test_the_current_run_is_described_by_the_event_and_its_jobs(self):
        doc = _gather(FakeGh())
        current = doc["current"]
        self.assertEqual(current["run_id"], RUN_ID)
        self.assertEqual(current["workflow_path"], WF_PATH)
        self.assertEqual(current["head_sha"], SHA)
        self.assertEqual(current["log"], LOG)
        self.assertEqual(
            [job["name"] for job in current["jobs"]],
            ["infra — typecheck & test"],
        )
        self.assertEqual(
            [step["name"] for step in current["jobs"][0]["steps"]],
            ["Checkout", "Test"],
        )

    def test_three_prior_main_runs_at_most_newest_first(self):
        doc = _gather(FakeGh())
        self.assertEqual(
            [entry["head_sha"] for entry in doc["prior"]], PRIOR_SHAS[:3])
        for entry in doc["prior"]:
            self.assertEqual(entry["workflow_path"], WF_PATH)
            self.assertEqual(entry["log"], LOG)
            self.assertTrue(entry["jobs"])

    def test_the_listing_is_scoped_to_this_workflow_and_the_default_branch(self):
        gh = FakeGh()
        _gather(gh, branch="trunk")
        self.assertIn(("runs", WF_ID, "trunk", repair_history.LIST_PER_PAGE),
                      gh.calls)
        # 4 asked for, 3 kept — the current run can be in the listing and must
        # never be compared against itself.
        self.assertEqual(repair_history.LIST_PER_PAGE,
                         repair_history.PRIOR_RUNS + 1)

    def test_the_current_run_is_never_its_own_history(self):
        gh = FakeGh()
        gh.runs = [{"id": RUN_ID, "path": WF_PATH, "head_sha": SHA,
                    "status": "completed", "conclusion": "failure"}] + gh.runs
        doc = _gather(gh)
        self.assertEqual(
            [entry["head_sha"] for entry in doc["prior"]], PRIOR_SHAS[:3])

    def test_an_unfinished_run_is_not_history(self):
        # `status=completed` is asked for, but a listing that answers with an
        # in-progress run must not have its half-written jobs compared.
        gh = FakeGh()
        gh.runs = [{"id": RUN_ID - 99, "path": WF_PATH, "head_sha": "f" * 40,
                    "status": "in_progress", "conclusion": None}] + gh.runs
        doc = _gather(gh)
        self.assertNotIn("f" * 40, [e["head_sha"] for e in doc["prior"]])

    def test_the_previous_attempt_of_this_run_leads_the_history(self):
        gh = FakeGh()
        doc = _gather(gh, run_attempt=3)
        first = doc["prior"][0]
        self.assertEqual(first["run_id"], RUN_ID)
        self.assertEqual(first["run_attempt"], 2)
        self.assertEqual(first["head_sha"], SHA)
        self.assertEqual(first["workflow_path"], WF_PATH)
        self.assertIn(("jobs", RUN_ID, 2), gh.calls)
        self.assertIn(("log", RUN_ID, 2), gh.calls)

    def test_a_first_attempt_asks_for_no_previous_one(self):
        gh = FakeGh()
        _gather(gh, run_attempt=1)
        self.assertNotIn(("jobs", RUN_ID, 0), gh.calls)


class FailedReadsTest(unittest.TestCase):
    """Best effort in one direction only: less history, never a false repeat."""

    def test_the_current_runs_jobs_being_unreadable_is_no_history(self):
        doc = _gather(FakeGh(raises=[("jobs", RUN_ID, None)]))
        self.assertIsNone(doc)

    def test_an_unreadable_listing_still_keeps_the_previous_attempt(self):
        doc = _gather(FakeGh(raises=["runs"]), run_attempt=2)
        self.assertEqual(len(doc["prior"]), 1)
        self.assertEqual(doc["prior"][0]["run_attempt"], 1)

    def test_one_unreadable_prior_run_drops_only_that_run(self):
        gh = FakeGh(raises=[("jobs", RUN_ID - 2, None)])
        doc = _gather(gh)
        shas = [entry["head_sha"] for entry in doc["prior"]]
        self.assertNotIn(PRIOR_SHAS[1], shas)
        self.assertIn(PRIOR_SHAS[0], shas)

    def test_an_unreadable_log_leaves_the_run_with_its_job_conclusions(self):
        # A job GitHub concluded `timed_out` still says so without its log.
        doc = _gather(FakeGh(raises=["log"]))
        self.assertEqual(doc["prior"][0]["log"], "")
        self.assertTrue(doc["prior"][0]["jobs"])

    def test_gather_never_raises(self):
        self.assertIsNone(_gather(FakeGh(raises=["jobs"])))


class CliTest(unittest.TestCase):
    """The workflow's half: one file, written whatever happens, exit 0."""

    def _main(self, gh, *extra):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "red-main-log.txt")
            out = os.path.join(td, "repair-history.json")
            with open(log, "w") as f:
                f.write(LOG)
            argv = [
                "gather", "--repo", REPO, "--workflow-id", str(WF_ID),
                "--run-id", str(RUN_ID), "--run-attempt", "1",
                "--branch", "main", "--workflow-path", WF_PATH,
                "--head-sha", SHA, "--current-log", log, "--out", out,
                *extra,
            ]
            with mock.patch.object(repair_history, "_Gh", lambda repo: gh):
                code = repair_history.main(argv)
            with open(out) as f:
                return code, f.read()

    def test_a_good_gather_writes_the_document(self):
        code, written = self._main(FakeGh())
        self.assertEqual(code, 0)
        doc = json.loads(written)
        self.assertEqual(doc["current"]["head_sha"], SHA)
        self.assertEqual(len(doc["prior"]), 3)

    def test_a_failed_gather_writes_the_marker_and_still_exits_zero(self):
        # A red main must never be made redder by its own bookkeeping, and
        # `decide` reads a non-JSON file as "no history".
        code, written = self._main(FakeGh(raises=["jobs"]))
        self.assertEqual(code, 0)
        self.assertEqual(written.strip(), repair_history.FETCH_FAILED)
        with self.assertRaises(ValueError):
            json.loads(written)

    def test_a_missing_current_log_is_not_fatal(self):
        code, written = self._main(FakeGh(), "--current-log",
                                   "/tmp/does-not-exist-4674.txt")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(written)["current"]["log"], "")


class DecideReadsWhatThisWritesTest(unittest.TestCase):
    """The two halves of the rule meet on this document and nowhere else."""

    def test_a_gathered_repeat_is_a_repair(self):
        import red_main_repair

        doc = _gather(FakeGh())
        decision = red_main_repair.decide(
            conclusion="failure", head_branch="main", default_branch="main",
            head_sha=SHA, log_text=LOG, refs=[], pulls=[], history=doc,
        )
        self.assertTrue(decision["go"])
        self.assertEqual(decision["reason"], "repair")
        self.assertEqual(decision["timeout_step"], "Test")
        self.assertEqual(decision["timeout_job"], "infra — typecheck & test")
        self.assertEqual(decision["timeout_limit"], "12 minutes")

    def test_the_markers_document_is_no_history(self):
        import red_main_repair

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "repair-history.json")
            with open(path, "w") as f:
                f.write(repair_history.FETCH_FAILED + "\n")
            self.assertIsNone(red_main_repair.load_history(path))


if __name__ == "__main__":
    unittest.main()
