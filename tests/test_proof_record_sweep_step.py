"""The reconcile sweep closes the record pull requests that cannot merge (DRE-8452).

DRE-8437 wrote `proof_record_pr.py sweep`, and DRE-8441 stopped a proof run
opening a pull request for a record that cannot merge. The record pull
requests already open stayed open: three in this repo on 2026-10-30, twelve
fleet-wide the night before. This file pins the step that closes them, on
every full pass, and the standard's two sentences that say so:

  * `Close proof records that cannot merge` comes right after
    `Dispatch proof runs` in `reconcile.yml`, under the same `if:` and the
    same proof-run stub check, and carries the same tokens, key and switch;
  * its script, run against a fake `proof_record_pr.py`, calls nothing where
    the repo has no proof-run stub, and otherwise calls `sweep --repo <repo>`
    through `proof_step_limit.py run` (DRE-9101) and copies its
    `proof-record-sweep:` line to the step summary; a sweep past its limit is
    cut and the step stays green, with the `proof-step-limit:` line in the
    summary;
  * `standards/card-quality.md` says the sweep closes such a pull request,
    keeping its branch, and where the sweep runs.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_record_sweep_step.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reconcile.yml"
STANDARD = ROOT / "standards" / "card-quality.md"

STEP = "Close proof records that cannot merge"
DISPATCH = "Dispatch proof runs"
STUBS = (".github/workflows/self-proof-task.yml", ".github/workflows/proof-task.yml")

#: What the step carries, each exactly as `Dispatch proof runs` carries it.
SHARED_ENV = ("GH_TOKEN", "GH_READ_TOKEN", "LINEAR_API_KEY", "REPO",
              "PROOF_DISPATCH_LIVE", "PROOF_DISPATCH_LIVE_OFF_UNTIL")

#: `proof_record_pr.py`, stubbed: it records its argv and prints what the
#: real `sweep` prints, or exits with `FAKE_EXIT` — a refused read or write —
#: or first sleeps `FAKE_SLEEP` seconds: a read that never answers.
_FAKE = """\
import json, os, sys, time
open(os.environ["CALLS"], "a").write(json.dumps(sys.argv[1:]) + "\\n")
print("would: close #985", flush=True)
time.sleep(float(os.environ.get("FAKE_SLEEP") or 0))
print("proof-record-sweep: read 3 open record pull request(s); would close 1: #985; "
      "kept 1; unread 1")
sys.exit(int(os.environ.get("FAKE_EXIT") or 0))
"""

#: The step's limit (DRE-9101), and the shortened one the cut test runs it at.
LIMIT = "--limit 30"
SHORT = "--limit 1"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _steps() -> list[dict]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["sweep"]["steps"]


def _step(name: str) -> dict:
    found = [s for s in _steps() if s.get("name") == name]
    assert len(found) == 1, f"{name!r} is not one step of reconcile.yml"
    return found[0]


def _stub_check(run: str) -> str:
    """The `if [ ! -f … ]` line that asks whether a proof-run stub exists."""
    lines = [line.strip() for line in run.splitlines()
             if line.strip().startswith("if [ ! -f ")]
    assert len(lines) == 1, run
    return lines[0]


# --------------------------------------------------------------------------- #
# the step's place, gate and environment                                       #
# --------------------------------------------------------------------------- #


class StepShapeTest(unittest.TestCase):

    def test_it_comes_right_after_the_proof_dispatch(self):
        names = [s.get("name") for s in _steps()]
        self.assertIn(STEP, names)
        self.assertEqual(names.index(DISPATCH) + 1, names.index(STEP))

    def test_it_runs_under_the_dispatchs_gate(self):
        self.assertEqual(_step(DISPATCH)["if"], _step(STEP)["if"])
        self.assertIn("inputs.sweep_reason == ''", _step(STEP)["if"])

    def test_it_asks_for_the_proof_run_stub_as_the_dispatch_does(self):
        check = _stub_check(_step(STEP)["run"])
        self.assertEqual(_stub_check(_step(DISPATCH)["run"]), check)
        for stub in STUBS:
            self.assertIn(stub, check)

    def test_it_carries_the_dispatchs_tokens_key_and_switch(self):
        env, dispatch = _step(STEP)["env"], _step(DISPATCH)["env"]
        for name in SHARED_ENV:
            with self.subTest(name=name):
                self.assertEqual(dispatch[name], env[name])
        self.assertEqual("${{ steps.app.outputs.token }}", env["GH_TOKEN"])
        self.assertEqual("${{ steps.reader.outputs.token }}", env["GH_READ_TOKEN"])
        self.assertEqual("${{ github.repository }}", env["REPO"])
        self.assertEqual("${{ vars.PROOF_DISPATCH_LIVE }}", env["PROOF_DISPATCH_LIVE"])
        self.assertEqual("${{ vars.PROOF_DISPATCH_LIVE_OFF_UNTIL }}",
                         env["PROOF_DISPATCH_LIVE_OFF_UNTIL"])

    def test_it_runs_the_sweep_command_through_its_limit(self):
        self.assertIn('python3 .bureau-pipeline/scripts/proof_step_limit.py run '
                      f'--step "{STEP}" {LIMIT} -- '
                      'python3 .bureau-pipeline/scripts/proof_record_pr.py sweep --repo "$REPO"',
                      _step(STEP)["run"])


# --------------------------------------------------------------------------- #
# the step's script, run against a fake proof_record_pr.py                     #
# --------------------------------------------------------------------------- #


class StepRunTest(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        scripts = self.tmp / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "proof_record_pr.py").write_text(_FAKE, encoding="utf-8")
        shutil.copy(ROOT / "scripts" / "proof_step_limit.py", scripts / "proof_step_limit.py")
        (self.tmp / ".github" / "workflows").mkdir(parents=True)
        self.calls = self.tmp / "calls.jsonl"
        self.summary = self.tmp / "summary.md"

    def tearDown(self):
        self._dir.cleanup()

    def _run(self, *, stub: str | None, exit_code: int = 0, sleep: float = 0,
             short: bool = False) -> subprocess.CompletedProcess:
        run = _step(STEP)["run"]
        if short:
            self.assertIn(LIMIT, run)
            run = run.replace(LIMIT, SHORT)
        if stub:
            (self.tmp / stub).write_text("name: proof\n", encoding="utf-8")
        env = {
            "PATH": os.environ["PATH"],
            "HOME": os.environ.get("HOME", str(self.tmp)),
            "CALLS": str(self.calls),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "GITHUB_REPOSITORY": "acme/Widgets",
            "REPO": "acme/Widgets",
            "FAKE_EXIT": str(exit_code),
            "FAKE_SLEEP": str(sleep),
        }
        return subprocess.run(["bash", "-e", "-c", run], cwd=self.tmp,
                              env=env, capture_output=True, text=True, timeout=60)

    def _called(self) -> list[list[str]]:
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines() if line]

    def test_with_no_proof_run_stub_it_calls_nothing(self):
        done = self._run(stub=None)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual([], self._called())
        self.assertIn("proof-record-sweep:", done.stdout)
        self.assertIn("no proof-run stub", done.stdout)

    def test_with_either_stub_it_sweeps_the_repo(self):
        for stub in STUBS:
            with self.subTest(stub=stub):
                for path in STUBS:
                    (self.tmp / path).unlink(missing_ok=True)
                self.calls.unlink(missing_ok=True)
                done = self._run(stub=stub)
                self.assertEqual(0, done.returncode, done.stdout + done.stderr)
                self.assertEqual([["sweep", "--repo", "acme/Widgets"]], self._called())

    def test_it_copies_the_sweep_line_to_the_step_summary(self):
        done = self._run(stub=STUBS[1])
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("proof-record-sweep: read 3 open record pull request(s)", done.stdout)
        text = self.summary.read_text(encoding="utf-8")
        self.assertIn("proof-record-sweep: read 3 open record pull request(s); "
                      "would close 1: #985; kept 1; unread 1", text)
        self.assertIn("would: close #985", text)

    def test_a_refused_sweep_turns_the_step_red_after_the_summary(self):
        done = self._run(stub=STUBS[0], exit_code=1)
        self.assertEqual(1, done.returncode)
        self.assertIn("proof-record-sweep: read 3", self.summary.read_text(encoding="utf-8"))

    def test_a_sweep_past_its_limit_is_cut_and_the_step_stays_green(self):
        done = self._run(stub=STUBS[0], sleep=30, short=True)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("::warning title=Proof step cut at its limit::", done.stdout)
        text = self.summary.read_text(encoding="utf-8")
        self.assertIn(f'proof-step-limit: cut step="{STEP}" limit=1s', text)
        self.assertIn("would: close #985", text)
        self.assertNotIn("proof-record-sweep: read 3", text)

    def test_a_refused_sweep_inside_its_limit_stays_red(self):
        done = self._run(stub=STUBS[0], exit_code=1, short=True)
        self.assertEqual(1, done.returncode)
        self.assertNotIn("proof-step-limit: cut", self.summary.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# the standard                                                                 #
# --------------------------------------------------------------------------- #


def _proof_section() -> str:
    text = STANDARD.read_text(encoding="utf-8")
    found = re.search(r"^## Every epic ends with a proof card.*?(?=^## )", text, re.S | re.M)
    assert found, "card-quality.md must still carry the proof-card section"
    return _flat(found.group(0))


class StandardTest(unittest.TestCase):

    def setUp(self):
        self.section = _proof_section()

    def test_it_says_the_sweep_closes_a_record_that_cannot_merge_and_keeps_its_branch(self):
        self.assertIn("The reconcile sweep closes a record pull request whose record "
                      "cannot merge, keeping its branch, and the card keeps its lane "
                      "under its hold", self.section)

    def test_the_sentence_sits_beside_dre_8441s(self):
        rule = self.section.index("the pull request opens only when the record can merge")
        sweep = self.section.index("The reconcile sweep closes a record pull request")
        self.assertLess(rule, sweep)
        self.assertLess(sweep - rule, 400)

    def test_it_says_where_the_sweep_runs(self):
        self.assertIn("It runs on the canary channel as soon as it merges, in every other "
                      "repository as its `stable` tag advances past it, and only where "
                      "`PROOF_DISPATCH_LIVE` is `true`", self.section)


if __name__ == "__main__":
    unittest.main()
