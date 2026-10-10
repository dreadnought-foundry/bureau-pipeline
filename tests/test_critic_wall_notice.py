"""DRE-6701: when the reviewer cannot run, its notice names the wall it hit.

agent-bureau PR #3363, QA Review run 37713749781. Attempt 1's log printed
`api_error_status: 429` at 18:37 PT; the notice posted at 18:39 PT said

    The adversarial reviewer crashed twice (startup/auth failure, no
    inference) … an instant, one-turn, $0 run IS the stale-credential
    signature …

and the retry at 18:50 PT reviewed the pull request. A rate limit clears by
itself; the sentence sent its reader to rotate a token that was fine. 21
notices carried that sentence between 2026-10-01 and the card.

The reader that tells the walls apart already existed — `death_cause.py`
(DRE-4129) answers `throttled`, `capped` or `revoked` — and nothing on the
reviewer's path asked it. These tests run the REAL gate over an execution
record, feed its outputs to the REAL notice script and the REAL `Fail if
critic never really ran` shell through the env the workflow itself declares,
and read what each one says.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_qa_review_no_verdict_message as harness  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

import step_shell  # noqa: E402
import death_cause  # noqa: E402

# The account's rate limit, as death_cause's own suite spells it.
RATE_LIMITED = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "num_turns": 1,
    "total_cost_usd": 0,
    "api_error_status": 429,
    "result": (
        'API Error: 429 {"type":"error","error":{"type":"rate_limit_error",'
        '"message":"This request would exceed your account\'s rate limit. '
        'Please try again later."}}'
    ),
}

# The credential refused: a 401 that carries an authentication signature.
REVOKED = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "num_turns": 1,
    "total_cost_usd": 0,
    "api_error_status": 401,
    "result": (
        'API Error: 401 {"type":"error","error":{"type":'
        '"authentication_error","message":"OAuth token has been revoked"}}'
    ),
}

# A death no wall signature explains: a 500 with nothing else to go on.
UNREADABLE = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "num_turns": 1,
    "total_cost_usd": 0,
    "api_error_status": 500,
    "result": "API Error: 500 Internal server error",
}


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


# The account's usage wall, saved from a real record: a 429 that says
# "resets 8:30pm (UTC)". death_cause reads it `capped`, not `throttled`.
CAPPED = _fixture("usage-limit-death.json")

# Run 37713749781 attempt 1, the shape the card records: one turn, $0.00,
# `api_error_status: 429`. Nothing else from that record was saved, so
# nothing else is invented here.
RUN_37713749781 = _fixture("critic-crash-429-37713749781.json")


def _step(name: str) -> dict:
    doc = yaml.safe_load(step_shell.workflow_source(QA_REVIEW))
    found = [s for s in doc["jobs"]["review"]["steps"] if s.get("name") == name]
    if len(found) != 1:
        raise AssertionError(f"qa-review.yml has {len(found)} steps named {name!r}")
    return found[0]


_GATE_OUTPUT = re.compile(r"^\$\{\{\s*steps\.gate([12])\.outputs\.(\w+)\s*\}\}$")


def _gate_env(step: dict, gate1: dict, gate2: dict) -> dict:
    """Every env var the step takes from a gate's output, valued from those
    outputs — so a wire the workflow does not declare carries nothing."""
    env = {}
    for key, value in (step.get("env") or {}).items():
        found = _GATE_OUTPUT.match(str(value).strip())
        if found:
            gate = gate1 if found.group(1) == "1" else gate2
            env[key] = gate.get(found.group(2), "")
    return env


def _gates(first, second=None) -> tuple[dict, dict]:
    """The real gate's outputs for each attempt's execution record."""
    second = first if second is None else second
    outputs = []
    for execution in (first, second):
        with tempfile.TemporaryDirectory() as raw:
            outputs.append(harness.gate_outputs(Path(raw), execution))
    return outputs[0], outputs[1]


def notice(first, second=None) -> str:
    gate1, gate2 = _gates(first, second)
    env = _gate_env(_step("Post verdict or neutral status"), gate1, gate2)
    with tempfile.TemporaryDirectory() as raw:
        proc, body = harness.run_post(Path(raw), gate2, gate1=gate1, extra_env=env)
    assert proc.returncode == 0, proc.stderr
    return body


def error_line(first, second=None) -> str:
    gate1, gate2 = _gates(first, second)
    step = _step("Fail if critic never really ran")
    env = dict(os.environ, A1_OUTCOME="", A2_OUTCOME="")
    env.update(_gate_env(step, gate1, gate2))
    env["RUN_URL"] = "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/37713749781"
    run = step_shell.step_shell(step)
    assert "${{" not in run, "an unresolved expression reached the fail step's shell"
    with tempfile.TemporaryDirectory() as raw:
        script = Path(raw) / "fail.sh"
        script.write_text("set -uo pipefail\n" + run)
        proc = subprocess.run(["bash", str(script)], env=env,
                              capture_output=True, text=True)
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines()
             if ln.startswith("::error::")]
    assert len(lines) == 1, proc.stdout + proc.stderr
    return lines[0]


class TheGateCarriesTheWallTest(unittest.TestCase):
    """The gate asks death_cause for each crashed attempt and passes the
    answer on as a step output — one word from death_cause's own vocabulary."""

    def test_each_wall_is_death_causes_own_word(self):
        for record, word in ((RATE_LIMITED, death_cause.THROTTLED),
                             (CAPPED, death_cause.CAPPED),
                             (REVOKED, death_cause.REVOKED)):
            with self.subTest(word=word):
                gate, _ = _gates(record)
                self.assertEqual(gate.get("outcome"), "crash")
                self.assertEqual(gate.get("wall"), word)

    def test_an_unreadable_death_carries_no_wall(self):
        gate, _ = _gates(UNREADABLE)
        self.assertEqual(gate.get("outcome"), "crash")
        self.assertNotIn("wall", gate)
        self.assertEqual(gate.get("api_status"), "500")

    def test_a_capped_record_carries_its_reset(self):
        gate, _ = _gates(CAPPED)
        self.assertRegex(gate.get("wall_reset", ""),
                         r"^\d{4}-\d{2}-\d{2} 20:30 UTC$")

    def test_a_finished_run_is_never_given_a_wall(self):
        gate, _ = _gates(harness.RAN_NO_VERDICT)
        self.assertNotIn("wall", gate)


class ARateLimitReadsAsARateLimitTest(unittest.TestCase):
    """AC 1: two failed attempts carrying `api_error_status: 429`."""

    def setUp(self):
        self.body = notice(RATE_LIMITED)

    def test_it_says_rate_limit(self):
        self.assertIn("rate limit", self.body)
        self.assertIn("clears by itself", self.body)
        self.assertIn("will be retried", self.body)

    def test_it_names_no_stale_credential(self):
        self.assertNotIn("stale-credential", self.body)
        self.assertNotIn("startup/auth failure", self.body)
        self.assertNotIn("DRE-2924", self.body)

    def test_it_keeps_the_neutral_marker_the_medic_and_gate_read(self):
        import medic_classify  # noqa: E402

        self.assertIn(medic_classify.CRITIC_NEUTRAL_MARKER, self.body.splitlines()[0])
        self.assertNotIn("VERDICT:", self.body)


class AUsageLimitReadsAsAUsageLimitTest(unittest.TestCase):
    """AC 2: `capped` names the account's usage limit and its reset."""

    def setUp(self):
        self.body = notice(CAPPED)

    def test_it_names_the_usage_limit_and_when_it_resets(self):
        self.assertIn("usage limit", self.body)
        self.assertRegex(self.body, r"resets at \d{4}-\d{2}-\d{2} 20:30 UTC")
        self.assertNotIn("stale-credential", self.body)

    def test_a_record_with_no_stated_reset_says_so(self):
        silent = dict(CAPPED[-1], result="You've hit your limit")
        self.assertEqual(death_cause.cause(silent), death_cause.CAPPED)
        body = notice(silent)
        self.assertIn("usage limit", body)
        self.assertIn("does not say when it resets", body)


class ARefusedCredentialKeepsTheCredentialWordingTest(unittest.TestCase):
    """AC 2: `revoked` is the one case that keeps the stale-credential
    wording and its pointer."""

    def test_it_keeps_the_stale_credential_wording(self):
        body = notice(REVOKED)
        self.assertIn("stale-credential", body)
        self.assertIn("DRE-2924", body)
        self.assertNotIn("rate limit", body)


class AnUnreadableCauseIsNotGuessedTest(unittest.TestCase):
    """AC 3: say the cause was not read, say what was, name no credential."""

    def setUp(self):
        self.body = notice(UNREADABLE)

    def test_it_says_the_cause_could_not_be_read(self):
        self.assertIn("could not be read", self.body)

    def test_it_names_no_credential(self):
        lowered = self.body.lower()
        for guess in ("credential", "stale", "auth", "token", "dre-2924"):
            with self.subTest(guess=guess):
                self.assertNotIn(guess, lowered)

    def test_it_says_what_was_read(self):
        self.assertIn("1 turns", self.body)
        self.assertIn("0.00", self.body)
        self.assertIn("API error status 500", self.body)

    def test_an_expired_token_with_no_wall_signature_is_not_guessed_either(self):
        # The saved 401 critic crash: an expired OAuth token, which carries
        # none of death_cause's refused-credential signatures. death_cause
        # does not read it, so neither does the notice.
        expired = _fixture("critic-crash-401-2026-08-20.json")
        self.assertIsNone(death_cause.cause(expired))
        body = notice(expired)
        self.assertIn("could not be read", body)
        self.assertIn("API error status 401", body)
        self.assertNotIn("stale-credential", body)


class TheReplayOfRun37713749781Test(unittest.TestCase):
    """AC 4: the saved shape of run 37713749781 attempt 1 — one turn,
    $0.00, `api_error_status: 429` — reads as a rate limit."""

    def test_the_card_record_reads_throttled(self):
        self.assertEqual(death_cause.cause(RUN_37713749781), death_cause.THROTTLED)

    def test_its_notice_reads_as_a_rate_limit(self):
        body = notice(RUN_37713749781, RATE_LIMITED)
        self.assertIn("rate limit", body)
        self.assertNotIn("stale-credential", body)
        self.assertNotIn("startup/auth failure", body)
        self.assertIn("1 turns", body)
        self.assertIn("0.00", body)

    def test_both_attempts_in_that_shape_read_as_a_rate_limit(self):
        body = notice(RUN_37713749781)
        self.assertIn("rate limit", body)
        self.assertNotIn("stale-credential", body)


class TheRedLineAgreesWithTheNoticeTest(unittest.TestCase):
    """AC 5: the `::error::` line carries the same cause word as the notice."""

    CASES = (
        ("throttled", RATE_LIMITED, "rate limit"),
        ("capped", CAPPED, "usage limit"),
        ("revoked", REVOKED, "stale-credential"),
        ("unreadable", UNREADABLE, "could not be read"),
    )

    def test_the_same_word_on_both(self):
        for word, record, phrase in self.CASES:
            with self.subTest(word=word):
                body, line = notice(record), error_line(record)
                self.assertIn(phrase, body)
                self.assertIn(phrase, line)
                if word != "unreadable":
                    self.assertIn(word, body)
                    self.assertIn(word, line)

    def test_a_rate_limit_is_not_annotated_as_a_crash(self):
        line = error_line(RUN_37713749781)
        self.assertNotIn("crashed on both attempts", line)
        self.assertNotIn("stale-credential", line)

    def test_an_unreadable_cause_annotation_names_no_credential(self):
        line = error_line(UNREADABLE).lower()
        for guess in ("credential", "stale", "auth"):
            with self.subTest(guess=guess):
                self.assertNotIn(guess, line)

    def test_the_capped_reset_reaches_the_red_line(self):
        self.assertRegex(error_line(CAPPED), r"\d{4}-\d{2}-\d{2} 20:30 UTC")


class OneReaderTest(unittest.TestCase):
    """AC 6: the classification comes from death_cause.py — the notice, the
    red line and the gate carry none of its signatures themselves."""

    # `revoked` is a signature AND death_cause's answer word, and the answer
    # word is what the notice is meant to carry.
    SIGNATURES = tuple(
        sig for sig in (
            death_cause._THROTTLED_SIGNATURES + death_cause._CAPPED_SIGNATURES
            + death_cause._REVOKED_SIGNATURES
        )
        if sig not in (death_cause.THROTTLED, death_cause.CAPPED, death_cause.REVOKED)
    )

    def _texts(self) -> dict[str, str]:
        return {
            "scripts/post_verdict.sh": (ROOT / "scripts" / "post_verdict.sh").read_text(),
            "scripts/check_critic_result.py": (ROOT / "scripts" / "check_critic_result.py").read_text(),
            "Fail if critic never really ran": step_shell.step_shell(
                _step("Fail if critic never really ran")),
        }

    def test_the_gate_asks_death_cause(self):
        self.assertIn("death_cause.cause(", self._texts()["scripts/check_critic_result.py"])

    def test_no_second_copy_of_its_rules(self):
        for where, text in self._texts().items():
            lowered = text.lower()
            for sig in self.SIGNATURES:
                with self.subTest(where=where, sig=sig):
                    self.assertNotIn(sig.lower(), lowered)
            if where != "scripts/check_critic_result.py":
                for status in death_cause._THROTTLED_STATUSES + death_cause._REVOKED_STATUSES:
                    with self.subTest(where=where, status=status):
                        self.assertNotRegex(text, rf"\b{status}\b")


if __name__ == "__main__":
    unittest.main()
