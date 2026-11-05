"""RED-first tests for the harness's commit-status writes surviving one bad answer (DRE-9233).

WHAT HAPPENED (2026-11-05, 08:40 PT). Main's harness run 42065210067 proved
6af0f8c5 — all six scenarios PASS — and then went red on its last step. The
verdict stamp's one `gh api .../statuses/<sha>` POST got an empty body back
from GitHub, `gh` printed `unexpected end of JSON input` and exited 1, and
under `bash -e` that was the job. The success status never landed, so main read
red, Red-Main Repair fired, and the channel waited on a re-run that passed the
same commit untouched 22 minutes later. Nothing about the commit was wrong.

WHAT IS PINNED HERE: both steps that post the `integration-harness` status —
the pending stamp that opens the attempt and the verdict stamp that closes it —
retry a failed write a bounded number of times, and a write that keeps failing
still fails the step. These run the REAL step scripts out of harness.yml under
`bash -e`, against a stub `gh` and a stub `sleep` on PATH, so the test proves
the shell the runner executes, not a copy of it.
"""

import os
import stat
import subprocess  # nosec B404 — fixed argv, bash on a script read from the repo
import tempfile
import unittest
from pathlib import Path

import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "harness.yml"
)

# The stub `gh`: counts its calls in $GH_CALLS, and fails the first
# $GH_FAIL_FIRST of them the way run 42065210067's POST failed.
_GH_STUB = """#!/usr/bin/env bash
n=$(( $(cat "$GH_CALLS" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$GH_CALLS"
if [ "$n" -le "$GH_FAIL_FIRST" ]; then
  echo "unexpected end of JSON input" >&2
  exit 1
fi
echo '{"state":"ok"}'
"""

# The stub `sleep`: records the wait and returns at once.
_SLEEP_STUB = """#!/usr/bin/env bash
echo "$1" >> "$SLEPT"
"""


def _stamps():
    """Every step in the harness job that posts a commit status, in order."""
    doc = yaml.safe_load(WORKFLOW.read_text())
    steps = doc["jobs"]["harness"].get("steps") or []
    return [s for s in steps if "/statuses/" in (s.get("run") or "")]


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class StampRetryTest(unittest.TestCase):
    def setUp(self):
        self.stamps = _stamps()
        self.assertEqual(
            len(self.stamps), 2,
            "one pending stamp at the top, one verdict at the end",
        )

    def _run(self, step, fail_first):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        _write_exe(bin_dir / "gh", _GH_STUB)
        _write_exe(bin_dir / "sleep", _SLEEP_STUB)
        env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GH_CALLS": str(tmp / "calls"),
            "GH_FAIL_FIRST": str(fail_first),
            "SLEPT": str(tmp / "slept"),
            "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "42065210067",
            "TESTED_SHA": "6af0f8c5bbcf7a30e577f7762af30332ad7e2210",
            "ATTEMPT": "1",
            "STATE": "success",
            "BLOCKED_REASON": "",
        }
        # `bash -e` is the shell the runner gives a `run:` step.
        done = subprocess.run(  # nosec B603 B607
            ["bash", "-e", "-c", step["run"]],
            env=env, capture_output=True, text=True, timeout=60,
        )
        calls_file = tmp / "calls"
        calls = int(calls_file.read_text()) if calls_file.exists() else 0
        slept_file = tmp / "slept"
        slept = slept_file.read_text().split() if slept_file.exists() else []
        return done, calls, slept

    def test_one_bad_answer_is_retried_and_the_stamp_lands(self):
        for step in self.stamps:
            with self.subTest(step=step.get("name")):
                done, calls, slept = self._run(step, fail_first=1)
                self.assertEqual(
                    done.returncode, 0,
                    "one empty answer from GitHub must not turn a proved "
                    f"commit red (run 42065210067): {done.stderr}",
                )
                self.assertEqual(calls, 2, "the write is tried again, once")
                self.assertEqual(len(slept), 1, "with a wait before the retry")

    def test_a_first_time_success_writes_once_and_never_waits(self):
        for step in self.stamps:
            with self.subTest(step=step.get("name")):
                done, calls, slept = self._run(step, fail_first=0)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(calls, 1)
                self.assertEqual(slept, [])

    def test_a_write_that_keeps_failing_still_fails_the_step(self):
        """The retry is bounded and never swallows the failure: a stamp that
        cannot land leaves the step red, which is the honest record."""
        for step in self.stamps:
            with self.subTest(step=step.get("name")):
                done, calls, _ = self._run(step, fail_first=99)
                self.assertNotEqual(done.returncode, 0)
                self.assertEqual(calls, 3, "three tries, then stop")


if __name__ == "__main__":
    unittest.main()
