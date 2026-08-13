"""RED-first tests for DRE-2435 — a dead agent must say WHY it died.

When the QA critic (or a build agent) dies, the only evidence in the run log
today is the shape of the corpse: `num_turns: 1, total_cost_usd: 0`. People
read that and guess "credentials", and they have been wrong repeatedly — an
overloaded API, a refusal, a bad model id and an expired token all leave the
identical footprint. Days have gone into re-deriving a fact the run already
knew.

The error text is NOT missing. `anthropics/claude-code-action@v1` redacts its
own stdout (it rebuilds the result object from a small field whitelist), but
it separately writes the RAW message array to
`$RUNNER_TEMP/claude-execution-output.json`. Both gates already load that file
and already hold the full unredacted result dict in memory — they print a
fixed string and throw the rest away.

So: on `is_error: true`, print the diagnostic fields we already have.

The security shape matters as much as the feature. The action ships a
`show_full_output` switch that dumps the ENTIRE transcript — every assistant
turn and every tool result, i.e. file contents, command output, and whatever
a hostile PR managed to get echoed. That is not what this does. This prints a
whitelist of ~6 scalar fields from the FINAL RESULT MESSAGE ONLY. The
sentinel tests below are the load-bearing ones: transcript content must never
reach the log.
"""

import glob
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import execution_result  # noqa: E402


SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")

# Anything from the transcript — assistant prose, tool results, env — that
# reaches stdout is a leak. Real logs carry file contents and command output
# here; the sentinel stands in for all of it.
SENTINEL = "SECRET-SENTINEL-DO-NOT-PRINT"


def _transcript(result_message):
    """A realistic claude-execution-output.json: the message array the action
    writes, with the result record last. Every non-result message is loaded
    with the sentinel — none of it may ever be printed."""
    return [
        {"type": "system", "subtype": "init", "session_id": "abc",
         "tools": ["Bash", "Read"], "cwd": f"/home/runner/{SENTINEL}"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": f"I will read the config: {SENTINEL}"},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": False,
             "content": f"ANTHROPIC_API_KEY={SENTINEL}"},
        ]}},
        result_message,
    ]


# The real shape of an API death: the action's result record carries the SDK's
# error string plus the status/stop/terminal reasons, and we log NONE of it.
API_DEATH = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "duration_ms": 634,
    "num_turns": 1,
    "total_cost_usd": 0,
    "result": "API Error: 400 {\"type\":\"error\",\"error\":{\"type\":"
              "\"invalid_request_error\",\"message\":\"model: "
              "claude-does-not-exist\"}}",
    "api_error_status": 400,
    "stop_reason": "error",
    "terminal_reason": "api_error",
    # Never printed: the action puts the whole environment in here.
    "env": {"ANTHROPIC_API_KEY": SENTINEL},
}

# The SDKResultError shape — a list rather than a scalar.
ERRORS_DEATH = {
    "type": "result",
    "subtype": "error",
    "is_error": True,
    "num_turns": 1,
    "errors": [
        {"type": "authentication_error",
         "message": "OAuth token has expired"},
    ],
}

# A healthy run. It carries fields with the same NAMES as the diagnostic ones
# — on a green run none of them belong in the log.
CLEAN_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 24,
    "total_cost_usd": 1.23,
    "result": f"Done. {SENTINEL}",
    "api_error_status": None,
    "stop_reason": "end_turn",
    "terminal_reason": "success",
}

COMPLETE_VERDICT = """VERDICT: APPROVE

## Summary
The change does what the card asked.
"""

DIAGNOSTIC_NAMES = (
    "api_error_status", "stop_reason", "terminal_reason", "errors",
)


def run_critic(payload, verdict_text=None):
    """Run the critic gate CLI over a synthetic execution file."""
    with tempfile.TemporaryDirectory() as td:
        exec_path = os.path.join(td, "claude-execution-output.json")
        verdict_path = os.path.join(td, "qa-verdict.md")
        if payload is not None:
            with open(exec_path, "w") as f:
                json.dump(payload, f)
        if verdict_text is not None:
            with open(verdict_path, "w") as f:
                f.write(verdict_text)
        return subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS, "check_critic_result.py"),
             exec_path, verdict_path],
            capture_output=True, text=True,
        )


def run_agent(payload, branch="agent/DRE-1", pr="http://pr", extra=()):
    """Run the agent-task gate CLI over a synthetic execution file."""
    with tempfile.TemporaryDirectory() as td:
        exec_path = os.path.join(td, "claude-execution-output.json")
        if payload is not None:
            with open(exec_path, "w") as f:
                json.dump(payload, f)
        return subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS, "check_agent_result.py"),
             exec_path, branch, pr, "", *extra],
            capture_output=True, text=True,
        )


class CriticFailureDetailTest(unittest.TestCase):
    """The critic gate must print the death's own explanation."""

    def test_api_death_prints_every_diagnostic_field(self):
        p = run_critic(_transcript(API_DEATH))
        self.assertEqual(p.returncode, 1)
        out = p.stdout + p.stderr
        self.assertIn("error_during_execution", out)
        self.assertIn("api_error_status", out)
        self.assertIn("400", out)
        self.assertIn("stop_reason", out)
        self.assertIn("terminal_reason", out)
        self.assertIn("api_error", out)
        # The SDK's own error string — the sentence that ends the guessing.
        self.assertIn("claude-does-not-exist", out)

    def test_errors_list_is_printed(self):
        p = run_critic(_transcript(ERRORS_DEATH))
        out = p.stdout + p.stderr
        self.assertIn("errors", out)
        self.assertIn("authentication_error", out)
        self.assertIn("OAuth token has expired", out)

    def test_single_result_object_file_also_works(self):
        # The action has written both shapes; the gate reads either.
        p = run_critic(API_DEATH)
        self.assertIn("claude-does-not-exist", p.stdout + p.stderr)

    def test_clean_run_prints_no_diagnostics(self):
        p = run_critic(_transcript(CLEAN_RESULT), COMPLETE_VERDICT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        out = p.stdout + p.stderr
        for name in DIAGNOSTIC_NAMES:
            self.assertNotIn(name, out)

    def test_no_verdict_on_a_healthy_run_prints_no_diagnostics(self):
        # is_error is false: the critic ran and simply left no verdict. There
        # is no death to explain, so nothing extra belongs in the log.
        p = run_critic(_transcript(CLEAN_RESULT))
        self.assertEqual(p.returncode, 1)
        out = p.stdout + p.stderr
        for name in DIAGNOSTIC_NAMES:
            self.assertNotIn(name, out)
        self.assertNotIn(SENTINEL, out)


class AgentFailureDetailTest(unittest.TestCase):
    """agent-task gets the same treatment for free — same loader, same
    silence. This is the path where a death is REQUEUED rather than failed
    (--ignore-is-error), so the log line is the only trace it leaves."""

    def test_api_death_prints_every_diagnostic_field(self):
        p = run_agent(_transcript(API_DEATH))
        out = p.stdout + p.stderr
        self.assertIn("error_during_execution", out)
        self.assertIn("api_error_status", out)
        self.assertIn("claude-does-not-exist", out)

    def test_ignored_is_error_still_explains_itself(self):
        p = run_agent(_transcript(API_DEATH), extra=("--ignore-is-error",))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("claude-does-not-exist", p.stdout + p.stderr)

    def test_clean_run_prints_no_diagnostics(self):
        p = run_agent(_transcript(CLEAN_RESULT))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        out = p.stdout + p.stderr
        for name in DIAGNOSTIC_NAMES:
            self.assertNotIn(name, out)


class TranscriptNeverLeaksTest(unittest.TestCase):
    """THE security test. A whitelist of scalars off the final result message
    — never the transcript. Assistant turns and tool results routinely carry
    file contents, command output, and secrets that a hostile PR can steer."""

    def test_critic_never_prints_transcript_content(self):
        for payload in (_transcript(API_DEATH), _transcript(ERRORS_DEATH),
                        _transcript(CLEAN_RESULT)):
            with self.subTest(subtype=payload[-1].get("subtype")):
                p = run_critic(payload)
                self.assertNotIn(SENTINEL, p.stdout + p.stderr)

    def test_agent_never_prints_transcript_content(self):
        for payload in (_transcript(API_DEATH), _transcript(ERRORS_DEATH),
                        _transcript(CLEAN_RESULT)):
            with self.subTest(subtype=payload[-1].get("subtype")):
                p = run_agent(payload)
                self.assertNotIn(SENTINEL, p.stdout + p.stderr)

    def test_env_on_the_result_message_is_never_printed(self):
        # `env` sits on the result record itself, inside the whitelist's own
        # reach — it is excluded by name, not by luck.
        p = run_critic(API_DEATH)
        out = p.stdout + p.stderr
        self.assertNotIn(SENTINEL, out)
        self.assertNotIn("ANTHROPIC_API_KEY", out)

    def test_unlisted_result_fields_are_not_printed(self):
        payload = dict(API_DEATH)
        payload["permission_denials"] = [{"tool": "Bash", "input": SENTINEL}]
        payload["modelUsage"] = {"claude": {"inputTokens": 1}}
        p = run_critic(payload)
        out = p.stdout + p.stderr
        self.assertNotIn(SENTINEL, out)
        self.assertNotIn("permission_denials", out)
        self.assertNotIn("modelUsage", out)


class TruncationTest(unittest.TestCase):
    """A death can carry a megabyte of provider HTML. Cap it and say so."""

    def test_long_result_is_truncated_and_labelled(self):
        long_result = "x" * 4000 + "TAIL-MARKER"
        payload = {"is_error": True, "subtype": "error",
                   "result": long_result}
        p = run_critic(payload)
        out = p.stdout + p.stderr
        self.assertNotIn("TAIL-MARKER", out)
        self.assertIn("truncated", out)
        # Cap is ~500 chars per value, so the whole line stays log-sized.
        self.assertLess(len(out), 1500)

    def test_short_result_is_not_labelled_truncated(self):
        p = run_critic({"is_error": True, "subtype": "error",
                        "result": "API Error: 529 overloaded_error"})
        out = p.stdout + p.stderr
        self.assertIn("overloaded_error", out)
        self.assertNotIn("truncated", out)

    def test_long_errors_list_is_truncated_too(self):
        payload = {"is_error": True, "errors": [{"message": "y" * 4000}]}
        p = run_critic(payload)
        out = p.stdout + p.stderr
        self.assertIn("truncated", out)
        self.assertLess(len(out), 1500)


class MalformedInputTest(unittest.TestCase):
    """Existing behaviour, pinned: a missing or unreadable execution file is
    not a crash. Action versions move the file; the gates must survive it."""

    def test_missing_execution_file_does_not_crash(self):
        for run in (run_critic, run_agent):
            with self.subTest(run=run.__name__):
                p = run(None)
                self.assertIn(p.returncode, (0, 1))
                self.assertNotIn("Traceback", p.stdout + p.stderr)

    def _run_over_raw(self, raw):
        with tempfile.TemporaryDirectory() as td:
            exec_path = os.path.join(td, "out.json")
            with open(exec_path, "w") as f:
                f.write(raw)
            crit = subprocess.run(
                [sys.executable,
                 os.path.join(SCRIPTS, "check_critic_result.py"),
                 exec_path, os.path.join(td, "missing.md")],
                capture_output=True, text=True)
            agent = subprocess.run(
                [sys.executable,
                 os.path.join(SCRIPTS, "check_agent_result.py"),
                 exec_path, "agent/DRE-1", "http://pr", ""],
                capture_output=True, text=True)
            return crit, agent

    def test_malformed_json_does_not_crash(self):
        for raw in ("", "not json at all", "{", "[]", "null", "[1, 2, 3]"):
            with self.subTest(raw=raw):
                for p in self._run_over_raw(raw):
                    self.assertNotIn("Traceback", p.stdout + p.stderr)
                    self.assertIn(p.returncode, (0, 1))


class FailureDetailUnitTest(unittest.TestCase):
    """The helper both gates share — one implementation, not two copies
    drifting apart."""

    def test_no_lines_for_a_healthy_result(self):
        self.assertEqual(execution_result.failure_detail(CLEAN_RESULT), [])

    def test_no_lines_for_a_missing_result(self):
        self.assertEqual(execution_result.failure_detail(None), [])

    def test_lines_are_field_scoped(self):
        lines = execution_result.failure_detail(API_DEATH)
        self.assertTrue(lines)
        for line in lines:
            self.assertIn(":", line)
        joined = "\n".join(lines)
        self.assertIn("api_error_status", joined)
        self.assertNotIn("env", joined)

    def test_loader_is_shared_by_both_gates(self):
        # Producer/consumer drift is a known house failure: two copies of the
        # same loader is how one gate quietly stops matching the other.
        import check_agent_result
        import check_critic_result
        self.assertIs(check_agent_result._load_execution,
                      execution_result.load_execution)
        self.assertIs(check_critic_result._load_execution,
                      execution_result.load_execution)


class NoShowFullOutputTest(unittest.TestCase):
    """A guard, not a feature. `show_full_output: true` on
    claude-code-action dumps the entire transcript — every tool result and
    every assistant turn — into a log that is public on public repos. The
    whitelist above exists so nobody ever reaches for that switch. If this
    test fails, someone did."""

    def test_no_workflow_enables_show_full_output(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        offenders = []
        for path in glob.glob(os.path.join(root, ".github", "workflows",
                                           "*.yml")):
            with open(path) as f:
                if "show_full_output" in f.read():
                    offenders.append(os.path.basename(path))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
