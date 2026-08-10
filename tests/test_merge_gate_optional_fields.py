"""The gate's OPTIONAL decision fields must not kill the step (2026-08-10).

DRE-2340 added this to the `Evaluate and merge` step:

    CARRIED=$(grep -m1 '^carried=' /tmp/gate-decision | cut -d= -f2-)

`carried=` is optional — merge_gate.py prints it only when a verdict was
actually carried across a head change. On every other evaluation the key is
absent, `grep` exits 1, and under the step's `set -euo pipefail` that exit
status propagates out of the command substitution and kills the whole step.

Live effect: the fleet's merge gate died the moment #140 reached main.
Portico ran green at 02:38:52 and failed on every run from 02:46:16 onward
(bureau-pipeline #140 merged 02:39:41). The run log stops immediately after
`reason=`, before the update/human/merge arms — so a merge-ready PR was
neither merged, nor updated, nor escalated. It just stopped, and the failure
looked like a gate crash rather than a missing optional field.

Why this shipped green: every existing assertion on this step reads the YAML
as TEXT — `assertIn("update-branch", run_block)`, string-position ordering.
The strings were all present and correctly ordered. Nothing executed the
block, so a step that dies two lines earlier passes every one of them.

These tests EXECUTE the real extraction lines, lifted from the real workflow,
under the real `set -euo pipefail`. They are written to generalise: any
future optional key gets the same protection automatically, because the test
discovers the extraction lines rather than hard-coding them.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_merge_gate_wiring import evaluate_step_run  # noqa: E402

# `VAR=$(grep -m1 '^key=' /tmp/gate-decision | ...)` — the gate's decision-field
# extractions, discovered from the workflow rather than hard-coded so a new
# field is covered the day it is added.
_EXTRACTION = re.compile(
    r"^\s*(?P<var>[A-Z_]+)=\$\(grep -m1 '\^(?P<key>[a-z_]+)=' /tmp/gate-decision.*\)\s*$"
)

# Printed by merge_gate.py on EVERY evaluation — the gate is entitled to assume
# these exist. Everything else is conditional and must tolerate absence.
ALWAYS_EMITTED = {"decision", "reason"}


def extraction_lines():
    """Every decision-field extraction in the evaluate step, as (var, key, line)."""
    found = []
    for line in evaluate_step_run().splitlines():
        m = _EXTRACTION.match(line)
        if m:
            found.append((m.group("var"), m.group("key"), line.strip()))
    return found


def run_fragment(line: str, decision_file: Path) -> subprocess.CompletedProcess:
    """Run one extraction line the way the step does: bash, set -euo pipefail."""
    script = (
        "set -euo pipefail\n"
        + line.replace("/tmp/gate-decision", str(decision_file))
        + "\necho SURVIVED\n"
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True
    )


class OptionalFieldExtractionTest(unittest.TestCase):
    """A decision file carrying only the always-emitted keys — the ordinary
    case — must not kill the step."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.decision = Path(self.tmp.name) / "gate-decision"
        # Exactly what merge_gate.py emits for a stale, merge-ready PR with
        # nothing carried: decision + reason, no optional keys.
        self.decision.write_text(
            "decision=update\n"
            "reason=otherwise merge-ready, but the branch is behind relative "
            "to its base\n"
        )
        self.addCleanup(self.tmp.cleanup)

    def test_the_step_extracts_at_least_one_optional_field(self):
        """Guard the guard: if the extractions are renamed or removed this
        suite must fail loudly rather than pass vacuously over nothing."""
        found = extraction_lines()
        self.assertTrue(found, "no decision-field extractions found in the step")
        optional = [k for _, k, _ in found if k not in ALWAYS_EMITTED]
        self.assertTrue(
            optional,
            "no OPTIONAL decision fields found — if merge_gate.py still emits "
            "conditional keys, this test has stopped covering them",
        )

    def test_an_absent_optional_field_does_not_kill_the_step(self):
        """THE regression. `carried=` is absent on an ordinary evaluation;
        grep exits 1; set -e kills the step before the update/human/merge
        arms ever run."""
        for var, key, line in extraction_lines():
            if key in ALWAYS_EMITTED:
                continue
            with self.subTest(field=key):
                proc = run_fragment(line, self.decision)
                self.assertEqual(
                    proc.returncode, 0,
                    f"extracting the optional '{key}' field killed the step "
                    f"(exit {proc.returncode}) when the key is absent — the "
                    f"gate stops before it can merge, update or escalate.\n"
                    f"line: {line}\nstderr: {proc.stderr}",
                )
                self.assertIn("SURVIVED", proc.stdout)

    def test_the_extracted_value_is_empty_when_the_field_is_absent(self):
        """Absence must read as empty, not as a stale or garbage value — the
        arm downstream guards on `[ -n "$CARRIED" ]`."""
        for var, key, line in extraction_lines():
            if key in ALWAYS_EMITTED:
                continue
            with self.subTest(field=key):
                script = (
                    "set -euo pipefail\n"
                    + line.replace("/tmp/gate-decision", str(self.decision))
                    + f'\nprintf "[%s]" "${var}"\n'
                )
                proc = subprocess.run(
                    ["bash", "-c", script], capture_output=True, text=True
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(
                    proc.stdout, "[]",
                    f"absent '{key}' produced {proc.stdout!r}, not empty",
                )

    def test_a_present_optional_field_is_still_read(self):
        """The fix must not blind the gate to the field when it IS emitted."""
        self.decision.write_text(
            "decision=merge\n"
            "reason=all conditions pass\n"
            "carried=abc123\n"
            "carried_content_id=deadbeef\n"
        )
        wanted = {"carried": "abc123", "carried_content_id": "deadbeef"}
        for var, key, line in extraction_lines():
            if key not in wanted:
                continue
            with self.subTest(field=key):
                script = (
                    "set -euo pipefail\n"
                    + line.replace("/tmp/gate-decision", str(self.decision))
                    + f'\nprintf "%s" "${var}"\n'
                )
                proc = subprocess.run(
                    ["bash", "-c", script], capture_output=True, text=True
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, wanted[key])


class DecisionArmsReachedTest(unittest.TestCase):
    """End-to-end over the step's own text: with only the always-emitted keys
    present, execution must reach the arm that acts on the decision.

    `merge` and `wait` had end-to-end coverage; `update` did not — and
    `update` is the arm that broke."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.decision = Path(self.tmp.name) / "gate-decision"
        self.addCleanup(self.tmp.cleanup)

    def _reaches_arm(self, action: str) -> subprocess.CompletedProcess:
        self.decision.write_text(f"decision={action}\nreason=synthetic\n")
        lines = ["set -euo pipefail"]
        for _, _, line in extraction_lines():
            lines.append(line.replace("/tmp/gate-decision", str(self.decision)))
        lines.append('echo "ARM=$DECISION"')
        return subprocess.run(
            ["bash", "-c", "\n".join(lines)], capture_output=True, text=True
        )

    def test_every_decision_reaches_its_arm(self):
        for action in ("merge", "update", "wait", "human"):
            with self.subTest(decision=action):
                proc = self._reaches_arm(action)
                self.assertEqual(
                    proc.returncode, 0,
                    f"decision={action}: the step died during field extraction "
                    f"(exit {proc.returncode}) — its arm is unreachable.\n"
                    f"stderr: {proc.stderr}",
                )
                self.assertIn(f"ARM={action}", proc.stdout)


if __name__ == "__main__":
    unittest.main()
