"""The merge gate holds a proof record that is not proven (DRE-6141).

On 2026-10-07, between 07:33 and 07:56 PT, automatic proof runs opened records
for DRE-5798, DRE-5447 and DRE-5982 in agent-bureau (#3318, #3319, #3320). In
each, nearly every criterion row read `Not observed.` or `Not met.` The critic
approved all three — the records were honest about what they had not seen —
and the gate merged them on green CI, as it merges code.

Condition P, `merge_gate.evaluate_proof_record`: on a proof-record branch
(`agent/DRE-<n>-proof-record`) the record `proof_record.py gather` wrote is
judged by the same table reader the hygiene lane closes proofs with. It holds
when any judged row is not met, when there is no criterion table or no judged
row, and when no record was found or it could not be read. The hold is a
`hold`, never a `wait` (which posts nothing) or `human` (which invites a
person to merge an unproven record by hand). It runs after the critic and
verifier conditions, so a REQUEST_CHANGES still reads as the critic's.

Driven through `merge_gate.py`'s CLI, the way `evaluate_and_merge.sh` calls it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_merge_gate_proof_record.py -v
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 — fixed argv, our own script
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "merge_gate.py"

HEAD = "ab12" * 10
QA = "agent-bureau-qa-bot[bot]"
PROOF_BRANCH = "agent/DRE-5798-proof-record"
CODE_BRANCH = "agent/DRE-5798-planner-fix"
RECORD = "architecture/proofs/planners-at-once.md"
GREEN = [{"name": "unit", "status": "completed", "conclusion": "success"}]
APPROVE = f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}"
REQUEST_CHANGES = f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{HEAD}"

MET = """| Criterion | Result |
|---|---|
| Two planners run at once | Met — runs 1 and 2 overlapped |
| Each plan lands on its own epic | Observed. |
| The record is merged to main | Pending |
"""
NOT_OBSERVED = """| Criterion | Result |
|---|---|
| Two planners run at once | Not observed. |
| Each plan lands on its own epic | Met |
"""
NOT_MET = """| Criterion | Result |
|---|---|
| Each plan lands on its own epic | Not met. |
"""
NO_TABLE = "# Proof\n\nIt worked when we looked.\n"
CLOSING_ONLY = """| Criterion | Result |
|---|---|
| The record is merged to main | Pending |
"""
#: A row that quotes every verdict marker the gate's notes must never carry.
QUOTES_MARKERS = """| Criterion | Result |
|---|---|
| The QA Critic posts VERDICT: APPROVE | Not observed — the QA Verifier said VERDICT: PASS |
"""


def record(text, path=RECORD):
    return {"applies": True, "path": path, "text": text, "detail": None}


def unread(detail, path=None):
    return {"applies": True, "path": path, "text": None, "detail": detail}


def gate(branch, proof, comments=(APPROVE,), raw=None):
    """Run the CLI. `proof` is the record payload (None: no flag passed);
    `raw`, when given, is written to the file verbatim instead."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "cr.json").write_text(json.dumps({"check_runs": GREEN}))
        (td / "cm.json").write_text(json.dumps(
            [{"user": {"login": QA}, "body": b} for b in comments]))
        (td / "wr.json").write_text(json.dumps({"workflow_runs": []}))
        (td / "cp.json").write_text(json.dumps({"status": "ahead"}))
        argv = [sys.executable, str(SCRIPT), "--head-sha", HEAD, "--qa-login", QA,
                "--check-runs-file", str(td / "cr.json"),
                "--comments-file", str(td / "cm.json"),
                "--workflow-runs-file", str(td / "wr.json"),
                "--compare-file", str(td / "cp.json"),
                "--head-branch", branch]
        if proof is not None or raw is not None:
            path = td / "proof-record.json"
            path.write_text(raw if raw is not None else json.dumps(proof))
            argv += ["--proof-record-file", str(path)]
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)  # nosec B603
    fields = dict(ln.split("=", 1) for ln in proc.stdout.splitlines() if "=" in ln)
    return proc, fields


class AProofRecordThatIsNotProvenIsHeldTest(unittest.TestCase):
    def assert_holds(self, proof, *named, raw=None):
        proc, fields = gate(PROOF_BRANCH, proof, raw=raw)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(fields.get("decision"), "hold", proc.stdout)
        for text in named:
            self.assertIn(text, fields["reason"])
        return fields["reason"]

    def test_a_not_observed_row_holds_and_is_named(self):
        self.assert_holds(record(NOT_OBSERVED), RECORD, "Two planners run at once",
                          "Not observed.")

    def test_a_not_met_row_holds_and_is_named(self):
        self.assert_holds(record(NOT_MET), "Each plan lands on its own epic", "Not met.")

    def test_a_record_with_no_criterion_table_holds(self):
        self.assert_holds(record(NO_TABLE), RECORD, "no criterion table")

    def test_a_record_with_no_judged_row_holds(self):
        self.assert_holds(record(CLOSING_ONLY), RECORD, "no row but its closing step")

    def test_no_record_found_holds_and_says_why(self):
        self.assert_holds(unread("the pull request adds 2 records — a.md, b.md"),
                          "a.md, b.md")

    def test_a_record_it_could_not_read_holds(self):
        self.assert_holds(unread("HTTP 502: Bad Gateway", path=RECORD), "502")

    def test_a_record_file_it_cannot_parse_holds(self):
        self.assert_holds(None, raw="not json")

    def test_a_record_file_saying_the_rule_does_not_apply_holds_on_a_proof_branch(self):
        """The gate checks the branch itself: a gatherer that answered for a
        different branch is not a record."""
        self.assert_holds({"applies": False})

    def test_the_reason_never_carries_a_verdict_marker(self):
        reason = self.assert_holds(record(QUOTES_MARKERS))
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(marker.lower(), reason.lower())

    def test_the_reason_is_one_line(self):
        reason = self.assert_holds(unread("HTTP 502\ndecision=merge", path=RECORD))
        self.assertNotIn("\n", reason)
        _, fields = gate(PROOF_BRANCH, unread("HTTP 502\ndecision=merge"))
        self.assertEqual(fields["decision"], "hold")


class WhatStillMergesTest(unittest.TestCase):
    def test_a_fully_met_record_merges(self):
        proc, fields = gate(PROOF_BRANCH, record(MET))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(fields.get("decision"), "merge", proc.stdout)

    def test_a_branch_that_is_not_a_proof_record_branch_is_not_affected(self):
        for proof in ({"applies": False}, record(NOT_OBSERVED)):
            proc, fields = gate(CODE_BRANCH, proof)
            self.assertEqual(fields.get("decision"), "merge", proc.stdout)
        proc, fields = gate(CODE_BRANCH, None, raw="not json")
        self.assertEqual(fields.get("decision"), "merge", proc.stdout)


class TheCriticStillSpeaksFirstTest(unittest.TestCase):
    def test_request_changes_reads_as_the_critics_hold(self):
        """DRE-5931 re-runs a record the critic sent back: that hold must stay
        the critic's, not be pre-empted by this condition."""
        _, fields = gate(PROOF_BRANCH, record(NOT_OBSERVED), comments=(REQUEST_CHANGES,))
        self.assertEqual(fields["decision"], "hold")
        self.assertEqual(fields["reason"], "latest verdict is not APPROVE — holding")

    def test_no_verdict_yet_still_waits(self):
        _, fields = gate(PROOF_BRANCH, record(NOT_OBSERVED), comments=())
        self.assertEqual(fields["decision"], "wait")


if __name__ == "__main__":
    unittest.main()
