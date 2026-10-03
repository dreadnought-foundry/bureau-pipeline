"""The critic and the verifier read their card through ONE snapshot step (#14).

`tests/test_card_snapshot.py` pins what the snapshot reads and when it may ask
the door; this file pins that the two workflows actually take it once, before
any step that reads the card, and that no step reads the card from Linear on
its own any more — a second read reintroduced beside the snapshot would pass
every behavioural test and quietly double the spend again.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

PLANNER_FIRST = ("${{ vars.LINEAR_AGENT_BUCKET == 'planner' && "
                 "secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY }}")
DOOR_ENV = {
    "BUREAU_READ": "${{ vars.BUREAU_READ || 'off' }}",
    "BUREAU_READ_URL": "${{ vars.BUREAU_READ_URL }}",
    "BUREAU_READ_AUDIENCE": "${{ vars.BUREAU_READ_AUDIENCE }}",
    "BUREAU_PIPELINE_REF": "${{ inputs.pipeline_ref }}",
}
# workflow → (job, the step that must come after the snapshot, the steps that
# read the card through it)
REVIEWERS = {
    "qa-review.yml": ("review", "vqplan", ("vqplan", "model")),
    "verify.yml": ("verify", "scope", ("scope", "model")),
}


def _steps(wf: str, job: str) -> list[dict]:
    doc = yaml.safe_load((WORKFLOWS / wf).read_text())
    return doc["jobs"][job]["steps"]


def _index(steps: list[dict], step_id: str) -> int:
    for i, step in enumerate(steps):
        if step.get("id") == step_id:
            return i
    raise AssertionError(f"no step with id {step_id!r}")


class OneSnapshotPerJobTest(unittest.TestCase):
    def test_the_snapshot_is_taken_once_before_the_first_reader(self):
        for wf, (job, first_reader, _) in REVIEWERS.items():
            with self.subTest(workflow=wf):
                steps = _steps(wf, job)
                takers = [s for s in steps if "card_snapshot.py take" in (s.get("run") or "")]
                self.assertEqual(len(takers), 1, f"{wf}: the card is snapshotted once per job")
                self.assertEqual(takers[0].get("id"), "cardsnap")
                self.assertLess(_index(steps, "pr"), _index(steps, "cardsnap"),
                                f"{wf}: the snapshot needs the card id Resolve PR reads")
                self.assertLess(_index(steps, "cardsnap"), _index(steps, first_reader),
                                f"{wf}: {first_reader} reads the card before it is taken")

    def test_the_snapshot_step_holds_the_keys_and_the_door_settings(self):
        for wf, (job, _, _) in REVIEWERS.items():
            with self.subTest(workflow=wf):
                step = _steps(wf, job)[_index(_steps(wf, job), "cardsnap")]
                env = step.get("env") or {}
                self.assertEqual(env.get("LINEAR_API_KEY"), PLANNER_FIRST)
                self.assertEqual(env.get("LINEAR_API_KEY_FALLBACK"), "${{ secrets.LINEAR_API_KEY }}")
                for key, value in DOOR_ENV.items():
                    self.assertEqual(env.get(key), value, f"{wf}: {key}")
                self.assertEqual(env.get("CARD"), "${{ steps.pr.outputs.card }}")
                # The script never reads `${{ }}` in its body (DRE-3484).
                self.assertNotIn("${{", step["run"])

    def test_no_step_reads_the_card_from_linear_on_its_own(self):
        for wf in REVIEWERS:
            with self.subTest(workflow=wf):
                text = (WORKFLOWS / wf).read_text()
                self.assertNotIn("linear_ops.py description", text)
                self.assertNotIn("dump-comments", text)

    def test_the_readers_read_the_snapshot(self):
        for wf, (job, _, readers) in REVIEWERS.items():
            steps = _steps(wf, job)
            for reader in readers:
                with self.subTest(workflow=wf, step=reader):
                    run = steps[_index(steps, reader)]["run"]
                    self.assertIn("card-snapshot.json", run)
        qa = _steps("qa-review.yml", "review")
        self.assertIn("card_snapshot.py description", qa[_index(qa, "vqplan")]["run"])
        verify = _steps("verify.yml", "verify")
        self.assertIn("--snapshot", verify[_index(verify, "scope")]["run"])
        for wf, (job, _, _) in REVIEWERS.items():
            steps = _steps(wf, job)
            with self.subTest(workflow=wf, step="model"):
                self.assertIn("card_snapshot.py thread", steps[_index(steps, "model")]["run"])

    def test_the_readers_no_longer_hold_a_linear_key(self):
        """A reader with a key and no read of its own is a key nobody needs —
        and the first place a second read would creep back in."""
        for wf, (job, _, readers) in REVIEWERS.items():
            steps = _steps(wf, job)
            for reader in readers:
                with self.subTest(workflow=wf, step=reader):
                    env = steps[_index(steps, reader)].get("env") or {}
                    self.assertNotIn("LINEAR_API_KEY", env)


class TheSnapshotStepRunsTest(unittest.TestCase):
    """Execute the `cardsnap` step's own shell with no card: it takes nothing,
    writes nothing, and never fails the job."""

    def test_no_card_takes_nothing_and_succeeds(self):
        for wf, (job, _, _) in REVIEWERS.items():
            with self.subTest(workflow=wf), tempfile.TemporaryDirectory() as tmp:
                step = _steps(wf, job)[_index(_steps(wf, job), "cardsnap")]
                env = {"PATH": "/usr/bin:/bin", "HOME": tmp, "RUNNER_TEMP": tmp,
                       "CARD": "", "PIPELINE_DIR": str(ROOT), "GITHUB_ACTIONS": "true",
                       "LINEAR_API_KEY": "test"}
                work = Path(tmp) / "work"
                work.mkdir()
                (work / ".bureau-pipeline").symlink_to(ROOT)
                proc = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=work, env=env,
                                      capture_output=True, text=True, timeout=60)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertFalse((Path(tmp) / "card-snapshot.json").exists())


if __name__ == "__main__":
    unittest.main()
