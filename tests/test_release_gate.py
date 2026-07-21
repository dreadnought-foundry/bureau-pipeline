"""RED-first tests for the vN release gate (DRE-2103).

The promotion contract becomes "agents author, human promotes, harness
proves": the operator cuts vN only after a green harness run against the
candidate sha. Two halves are pinned here:

  * scripts/release_gate.py — the decision: given the combined commit
    status of the tagged sha (GET /repos/{repo}/commits/{sha}/status),
    pass iff the harness's own stamp context reports success. Everything
    else — missing stamp, failure, pending, a blip `{}` substitute — is
    RED, fail-closed, with the exact pre-tag remediation command in the
    output so a red tag is self-explaining.
  * .github/workflows/release-gate.yml — the thin caller on push of v*
    tags: peel the tag to its commit, fetch the combined status, act on
    the script's verdict.

Why the commit-status stamp and never the workflow-run listing: a
workflow_dispatch run's head_sha is the tip of the ref the workflow FILE
was dispatched on, while pipeline_ref governs what was actually checked
out and tested — so the run record can claim main's tip while testing v1.
The stamp harness.yml posts against `git rev-parse HEAD` of its own
checkout is the only honest binding (vendor premortem: what does GitHub
actually record on a dispatch run?).
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import release_gate  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-gate.yml"

SHA = "a" * 40
CONTEXT = release_gate.STATUS_CONTEXT


def _combined(*statuses):
    return {"state": "irrelevant", "sha": SHA, "statuses": list(statuses)}


def _status(state, context=CONTEXT):
    return {"context": context, "state": state}


class EvaluateTest(unittest.TestCase):
    def test_green_stamp_passes(self):
        ok, reason = release_gate.evaluate(_combined(_status("success")), SHA)
        self.assertTrue(ok)
        self.assertIn(SHA, reason)

    def test_missing_stamp_is_red_with_the_pre_tag_command(self):
        ok, reason = release_gate.evaluate(_combined(), SHA)
        self.assertFalse(ok)
        # The remediation must be in the failure output — a red tag is
        # useless if the operator has to go hunting for the command.
        self.assertIn("gh workflow run harness.yml", reason)
        self.assertIn("pipeline_ref", reason)

    def test_failure_stamp_is_red(self):
        ok, _ = release_gate.evaluate(_combined(_status("failure")), SHA)
        self.assertFalse(ok)

    def test_pending_stamp_is_red(self):
        # A run still in flight proved nothing — never promote on pending.
        ok, _ = release_gate.evaluate(_combined(_status("pending")), SHA)
        self.assertFalse(ok)

    def test_success_under_another_context_does_not_count(self):
        ok, _ = release_gate.evaluate(
            _combined(_status("success", context="ci/tests")), SHA
        )
        self.assertFalse(ok)

    def test_blip_substitute_payload_is_red_not_a_crash(self):
        # release-gate.yml substitutes {} when the status fetch blips —
        # fail-closed, same direction as merge_gate's compare blip.
        ok, _ = release_gate.evaluate({}, SHA)
        self.assertFalse(ok)


class MainTest(unittest.TestCase):
    def _run(self, payload, sha=SHA):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            path = f.name
        self.addCleanup(os.unlink, path)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = release_gate.main(["--sha", sha, "--statuses-file", path])
            except SystemExit as e:  # argparse error path
                code = e.code
        return code, out.getvalue() + err.getvalue()

    def test_exit_zero_on_green(self):
        code, out = self._run(_combined(_status("success")))
        self.assertEqual(code, 0)
        self.assertIn(SHA, out)

    def test_exit_one_on_missing_stamp(self):
        code, out = self._run(_combined())
        self.assertEqual(code, 1)
        self.assertIn("gh workflow run harness.yml", out)

    def test_malformed_sha_exits_two(self):
        code, _ = self._run(_combined(_status("success")), sha="v5")
        self.assertEqual(code, 2)

    def test_unreadable_payload_exits_two(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = release_gate.main(
                ["--sha", SHA, "--statuses-file", "/nonexistent.json"]
            )
        self.assertEqual(code, 2)


class WorkflowWiringTest(unittest.TestCase):
    def _doc(self):
        self.assertTrue(WORKFLOW.is_file(), f"missing {WORKFLOW.name}")
        return yaml.safe_load(WORKFLOW.read_text())

    def _on(self):
        doc = self._doc()
        on = doc.get("on", doc.get(True))
        return on if isinstance(on, dict) else {}

    def _runs(self):
        doc = self._doc()
        return "\n".join(
            s.get("run") or ""
            for job in (doc.get("jobs") or {}).values()
            for s in (job.get("steps") or [])
        )

    def test_triggers_on_v_tags(self):
        push = self._on().get("push") or {}
        self.assertEqual(push.get("tags"), ["v*"])

    def test_read_only_permissions(self):
        perms = self._doc().get("permissions") or {}
        self.assertEqual(perms.get("contents"), "read")
        self.assertEqual(perms.get("statuses"), "read")

    def test_peels_the_tag_to_its_commit(self):
        # Annotated vs lightweight tags disagree about what the push event's
        # sha points at — `git rev-parse` on the checked-out tag is the
        # deterministic peel.
        self.assertIn("git rev-parse HEAD", self._runs())

    def test_fetches_the_combined_status_with_a_blip_substitute(self):
        runs = self._runs()
        self.assertIn("/status", runs)
        self.assertIn("echo '{}'", runs)

    def test_acts_on_the_script_verdict(self):
        self.assertIn("python3 scripts/release_gate.py", self._runs())


if __name__ == "__main__":
    unittest.main()
