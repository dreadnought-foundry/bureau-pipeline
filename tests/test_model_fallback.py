"""RED-first tests for model-fallback on is_error death (DRE-1354).

Origin (2026-06-13): Anthropic's API spent the day intermittently throwing
is_error mid-run. Each agent role pins ONE model; the requeue re-dispatched onto
the SAME model, so cards died 5-18× in a row against a single flaky model while
the alternate sat healthy and idle (DRE-1300/1314/1301). This proves the
selection logic: a simulated is_error death followed by a retry picks the
ALTERNATE model — Opus↔Fable, both directions — driven entirely by comment-
marker fixtures (no real API).
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import model_fallback as mf  # noqa: E402

OPUS = "claude-opus-4-8"
FABLE = "claude-fable-5"


class SelectModelTest(unittest.TestCase):
    def test_no_prior_error_uses_role_primary(self):
        self.assertEqual(mf.select_model("engineer", []), OPUS)
        self.assertEqual(mf.select_model("planner", []), FABLE)

    def test_engineer_falls_back_opus_to_fable_after_error(self):
        # Opus died with is_error -> next engineer attempt rides Fable.
        comments = [
            mf.attempt_marker(OPUS),
            mf.error_marker(OPUS),
        ]
        self.assertEqual(mf.select_model("engineer", comments), FABLE)

    def test_planner_falls_back_fable_to_opus_after_error(self):
        # Both directions: today it was Opus flaking, but a Fable outage must
        # fall the planner back to Opus.
        comments = [
            mf.attempt_marker(FABLE),
            mf.error_marker(FABLE),
        ]
        self.assertEqual(mf.select_model("planner", comments), OPUS)

    def test_last_error_wins_when_both_models_have_died(self):
        # Opus died, we switched to Fable, Fable also died -> back to Opus.
        comments = [
            mf.error_marker(OPUS),
            mf.attempt_marker(FABLE),
            mf.error_marker(FABLE),
        ]
        self.assertEqual(mf.select_model("engineer", comments), OPUS)

    def test_attempt_markers_alone_do_not_trigger_fallback(self):
        # A healthy attempt (no error marker) must not switch the model.
        self.assertEqual(
            mf.select_model("engineer", [mf.attempt_marker(OPUS)]), OPUS
        )

    def test_ignores_unrelated_comments_and_none_bodies(self):
        comments = [None, "🤖 PR opened: https://x/pr/1", "🔎 QA Critic — APPROVE"]
        self.assertEqual(mf.select_model("engineer", comments), OPUS)

    def test_unknown_model_in_marker_is_ignored(self):
        self.assertIsNone(mf.last_error_model([mf.error_marker("gpt-9")]))

    def test_alternate_never_returns_the_dead_model(self):
        self.assertEqual(mf.alternate("engineer", OPUS), FABLE)
        self.assertEqual(mf.alternate("engineer", FABLE), OPUS)
        self.assertEqual(mf.alternate("planner", FABLE), OPUS)
        self.assertEqual(mf.alternate("planner", OPUS), FABLE)


class CliTest(unittest.TestCase):
    SCRIPT = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "model_fallback.py"
    )

    def _select(self, role, bodies):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "c.json")
            with open(path, "w") as f:
                json.dump(bodies, f)
            return subprocess.run(
                [sys.executable, self.SCRIPT, "select", role, path],
                capture_output=True, text=True,
            ).stdout.strip()

    def test_cli_select_primary_with_no_history(self):
        self.assertEqual(self._select("engineer", []), OPUS)

    def test_cli_select_alternate_after_error(self):
        self.assertEqual(self._select("engineer", [mf.error_marker(OPUS)]), FABLE)

    def test_cli_select_missing_file_is_primary(self):
        out = subprocess.run(
            [sys.executable, self.SCRIPT, "select", "planner", "/no/such/file"],
            capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(out, FABLE)

    def test_cli_role_of_labels(self):
        def role_of(labels):
            buf = io.StringIO()
            with redirect_stdout(buf):
                mf.main(["role-of", labels])
            return buf.getvalue().strip()

        self.assertEqual(role_of("agent:planner,repo:atlas"), "planner")
        self.assertEqual(role_of("agent:engineer,size:m"), "engineer")
        self.assertEqual(role_of("repo:atlas"), "engineer")


class AgentsRegistryAlignment(unittest.TestCase):
    """The fallback ladder must agree with agents.yaml so the console roster and
    the runtime selection never drift (the registry contract test, DRE-1335)."""

    def test_role_primaries_match_registry(self):
        import yaml

        root = os.path.join(os.path.dirname(__file__), "..")
        with open(os.path.join(root, "agents.yaml")) as f:
            agents = {a["name"]: a for a in yaml.safe_load(f)["agents"]}
        self.assertEqual(mf.primary_model("engineer"), agents["engineer"]["model"])
        self.assertEqual(mf.primary_model("planner"), agents["planner"]["model"])

    def test_engineer_fallback_listed_in_registry_ladder(self):
        import yaml

        root = os.path.join(os.path.dirname(__file__), "..")
        with open(os.path.join(root, "agents.yaml")) as f:
            engineer = next(
                a for a in yaml.safe_load(f)["agents"] if a["name"] == "engineer"
            )
        ladder_models = {step["model"] for step in engineer.get("ladder", [])}
        self.assertIn(mf.alternate("engineer", OPUS), ladder_models)


if __name__ == "__main__":
    unittest.main()
