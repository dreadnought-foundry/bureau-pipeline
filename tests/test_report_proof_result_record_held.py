"""DRE-8461: the proof result step reports a record held on its branch.

After DRE-8441 a proof run whose record cannot merge leaves no pull request on
purpose, and the workflow passes `proof_record_pr.decide`'s decision word to
`report_proof_result.sh` as `RECORD_PR_DECISION`. On `held` or
`closed-and-held` the script posts `RECORD_HELD_RECEIPT` and the run, makes
no `advance` or `state` call — the card keeps its lane, and its
`🔬 proof-waiting` hold says what it waits on — and exits 0. It never says
`proof run ended with no record`: the record exists, on its branch. With an
escalation file beside it the park follows exactly as today. Every other
decision, and an unset or empty one, behaves as on `main`.

Each case runs `bash scripts/report_proof_result.sh` by path in a throwaway
working directory whose `.bureau-pipeline/scripts/` is the real `scripts/`
with two files stubbed: `linear_ops.py` (logs each call) and `card_pr.py`
(answers whatever `STUB_PR` holds, and exits 3 on `UNREADABLE`).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "report_proof_result.sh"

CARD = "DRE-9003"
RUN_URL = "https://github.com/acme-org/widgets/actions/runs/4343"
PR_URL = "https://github.com/acme-org/widgets/pull/78"
RECORD_HELD_RECEIPT = (
    "🤖 proof run left its record on its branch — no pull request; the record "
    "cannot merge yet, and the card's 🔬 proof-waiting hold says what it waits on."
)
NO_RECORD = "🤖 proof run ended with no record"

ESCALATION = (
    "Finding: the run read the settings page and saw the old banner.\n"
    "Question: should the criterion be reworded to the banner the page shows now?\n"
    "Recommendation: reword it.\n"
)

LINEAR_OPS_STUB = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(["linear_ops", *sys.argv[1:]]) + "\n")
if sys.argv[1] == "advance":
    print(f"advanced {sys.argv[2]} to {sys.argv[3]}")
'''

CARD_PR_STUB = r'''#!/usr/bin/env python3
import os, sys
if os.environ.get("STUB_PR") == "UNREADABLE":
    sys.exit(3)
print(os.environ.get("STUB_PR", ""))
'''


class _ProofRun(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.td = Path(self._td.name)

    def run_report(self, *, decision, pr="", escalation=None):
        td = self.td
        scripts = td / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        stubs = {"linear_ops.py": LINEAR_OPS_STUB, "card_pr.py": CARD_PR_STUB}
        for path in (ROOT / "scripts").iterdir():
            if path.name not in stubs:
                (scripts / path.name).symlink_to(path)
        for name, body in stubs.items():
            (scripts / name).write_text(body)
        temp = td / "runner-temp"
        temp.mkdir()
        escalation_file = td / "agent-escalation.txt"
        if escalation is not None:
            escalation_file.write_text(escalation)
        self.log = td / "stub.log"
        self.summary = td / "summary.md"
        env = dict(os.environ)
        for key in ("RECORD_PR_DECISION", "NAME_GUARD_LIVE", "RECORD_BASELINE_SHA"):
            env.pop(key, None)
        env.update({
            "STUB_LOG": str(self.log), "STUB_PR": pr,
            "CARD": CARD, "CARD_DESCRIPTION": "## Acceptance criteria\n\n- [ ] the banner reads right\n",
            "RECORD_BRANCH": f"agent/{CARD}-proof-record",
            "ESCALATION_FILE": str(escalation_file),
            "BUREAU_SERVER_URL": "https://github.com",
            "BUREAU_REPOSITORY": "acme-org/widgets",
            "BUREAU_RUN_ID": "4343",
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "RUNNER_TEMP": str(temp),
        })
        if decision is not None:
            env["RECORD_PR_DECISION"] = decision
        proc = subprocess.run(["bash", str(SCRIPT)], cwd=td, env=env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.proc = proc

    def calls(self):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [row[1:] for row in rows if row[0] == "linear_ops"]

    def comments(self):
        return [c[2] for c in self.calls() if c[0] == "comment"]

    def moves(self):
        return [c for c in self.calls() if c[0] in ("advance", "state")]

    def result_line(self):
        lines = [l for l in self.summary.read_text().splitlines()
                 if l.startswith("proof result: ")]
        self.assertEqual(len(lines), 1, self.summary.read_text())
        return lines[0]

    def assert_no_record_not_said(self):
        self.assertEqual([c for c in self.comments() if c.startswith(NO_RECORD)], [],
                         self.calls())


class HeldTest(_ProofRun):
    def assert_held(self):
        self.assertEqual(self.comments(), [f"{RECORD_HELD_RECEIPT} Run: {RUN_URL}"])
        self.assert_no_record_not_said()
        self.assertEqual(self.moves(), [])
        self.assertEqual(self.result_line(), "proof result: record held")

    def test_held_with_no_pull_request_posts_the_receipt_and_moves_nothing(self):
        self.run_report(decision="held")
        self.assert_held()

    def test_closed_and_held_does_the_same(self):
        self.run_report(decision="closed-and-held", pr=f"CLOSED\t{PR_URL}")
        self.assert_held()

    def test_closed_and_held_with_no_pull_request_found(self):
        self.run_report(decision="closed-and-held")
        self.assert_held()

    def test_held_when_github_cannot_be_read_still_says_held(self):
        # card_pr.py exits 3 on an unreadable answer; the decision still
        # names a record on its branch.
        self.run_report(decision="held", pr="UNREADABLE")
        self.assert_held()


class HeldWithAnEscalationTest(_ProofRun):
    def test_it_posts_the_receipt_parks_as_today_and_never_says_no_record(self):
        self.run_report(decision="held", escalation=ESCALATION)
        comments = self.comments()
        self.assertEqual(comments[0], f"{RECORD_HELD_RECEIPT} Run: {RUN_URL}")
        self.assertTrue(any(c.startswith("🙋 The proof run asks a decision only the CEO can make:")
                            for c in comments), comments)
        self.assert_no_record_not_said()
        ops = self.calls()
        self.assertEqual([c[0] for c in ops if c[0] == "proof-waiting"], ["proof-waiting"])
        self.assertEqual(self.moves(), [["advance", CARD, "Green Light", "Hand-work,In Review"]])


class OtherDecisionsTest(_ProofRun):
    def assert_in_review(self):
        self.assertEqual(self.comments(), [
            f"🤖 PR opened: {PR_URL} — CI + critic review running. Run: {RUN_URL}"])
        self.assertEqual(self.moves(), [["advance", CARD, "In Review", "Hand-work"]])
        self.assertEqual(self.result_line(), "proof result: record pull request")

    def test_opened_with_an_open_pull_request_goes_to_in_review(self):
        self.run_report(decision="opened", pr=f"OPEN\t{PR_URL}")
        self.assert_in_review()

    def test_reopened_and_kept_open_go_to_in_review(self):
        for decision in ("reopened", "kept-open"):
            with self.subTest(decision=decision):
                self.setUp()
                self.run_report(decision=decision, pr=f"OPEN\t{PR_URL}")
                self.assert_in_review()

    def assert_no_record(self):
        comments = self.comments()
        self.assertEqual(len(comments), 1, comments)
        self.assertTrue(comments[0].startswith(NO_RECORD), comments)
        self.assertNotIn(RECORD_HELD_RECEIPT, comments[0])
        self.assertEqual(self.moves(), [])
        self.assertEqual(self.result_line(), "proof result: no record")

    def test_no_record_takes_the_no_record_exit(self):
        self.run_report(decision="no-record")
        self.assert_no_record()

    def test_unset_takes_the_no_record_exit_as_on_main(self):
        self.run_report(decision=None)
        self.assert_no_record()

    def test_empty_takes_the_no_record_exit_as_on_main(self):
        self.run_report(decision="")
        self.assert_no_record()


if __name__ == "__main__":
    unittest.main()
