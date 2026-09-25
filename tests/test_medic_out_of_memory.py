"""RED-first (DRE-4847): a run killed with exit 137 is named OUT OF MEMORY, with
the runner and its class — one retry on a light runner, none on a heavy one, and
never a diagnosis agent.

On 2026-09-24 four build and fix runs were SIGKILLed on 2 GB light runners and
every instrument misread them, so a person had to diagnose each one: the medic
said "incomplete work … no action needed on infrastructure", a pipeline-failure
card blamed the stream watchdog "for silence" (the watchdog's threshold is 300s
and the agent had spoken 80s before the kill), and the in-job death receipt said
`died: false, cause: none`. The critic was killed the same way three times
between 09-23 and 09-25 and got the medic's rate-limit sentence, because
`critic_infra_crash` matched qa-review's neutral marker first.

The tell is in the log the medic already fetches: `##[error]Process completed
with exit code 137.` on a step, and `Runner name: 'bureau-mini-light-…'` in the
set-up lines. One exit-137 line is the rule — extra ones corroborate and are
never required (the four runs carry 4, 1, 2 and 1 of them).

What this file pins:

  1. `scripts/out_of_memory.py` is the ONE reader, the way
     `stream_watchdog.stall_from_log` is for stalls: prefix-stripped,
     quotation-skipping, anchored to the WHOLE error line — this card's own
     body quotes that line — and silent when the log carries the watchdog's
     stall line, because the watchdog stops a silent run with exit 124 and
     says so.
  2. `medic_classify` gives the failure its own class, after
     `environment_crash` and `stalled_no_stream` and BEFORE
     `critic_infra_crash` — the ordering lesson DRE-3428 and DRE-3991 both
     learned, a third time: a killed review leaves the neutral marker in the
     same log and would otherwise win.
  3. `medic_retry` keeps the ordinary single retry for a first kill on a LIGHT
     runner and declines it for a heavy, GitHub-hosted or unrecognized one, and
     for the second kill on a light one.
  4. The medic's gates, evaluated: an out-of-memory failure never fires
     `diagnose`, and on attempt 2 it fires `retry_declined`.

THE FIXTURES are short excerpts in the shape `gh run view --log-failed` writes
(`job<TAB>step<TAB><ISO timestamp> <content>`; with gh 2.100.0 every step reads
`UNKNOWN STEP`), trimmed to the runner-name line and the lines around each kill.
bureau-pipeline is public, so they carry no agent output and no card text.

Run: cd bureau-pipeline && python3 -m pytest tests/test_medic_out_of_memory.py -v
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import medic_classify  # noqa: E402
import medic_retry  # noqa: E402
import out_of_memory  # noqa: E402
import stream_watchdog  # noqa: E402

# The gate evaluator DRE-3430 built over medic.yml's own `if:` expressions —
# one replay of the workflow's gates, not a second copy of them.
from test_medic_environment_hold import fired_jobs  # noqa: E402

MEDIC = ROOT / ".github" / "workflows" / "medic.yml"
FIXTURES = ROOT / "tests" / "fixtures" / "medic-oom"

#: The four 2026-09-24 kills, and how many exit-137 lines each run's
#: `--log-failed` carries. ONE is the rule; the extras corroborate.
THE_FOUR = {
    "portico-build-35950845844-attempt1.log": 4,
    "portico-build-35950845844-attempt2.log": 1,
    "portico-fix-35954619710.log": 2,
    "agent-bureau-36093192182-attempt1.log": 1,
}

#: The critic's kill (portico QA Review run 36161433688, 2026-09-25), trimmed to
#: the runner-name line and the kill line because that repository is public.
CRITIC = "portico-qa-review-36161433688.log"

PREFIX = "build\tUNKNOWN STEP\t2026-09-24T18:41:02.7712345Z "
KILL_LINE = "##[error]Process completed with exit code 137."


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def stalled_log() -> str:
    """A run the watchdog stopped — and whose child processes were then killed,
    so the 137 line is in the log too. The stall line is the truth: the watchdog
    exits 124 and says so, and this is the log that proves the reader prefers
    it."""
    return (
        "repair\tRun claude\t2026-09-15T05:04:12.0000000Z "
        '{"type":"system","subtype":"init","session_id":"s1"}\n'
        "repair\tRun claude\t2026-09-15T05:09:24.0000000Z "
        + stream_watchdog.stall_line(stream_watchdog.Stall(
            step="Red-main Repair / repair",
            last_event="2026-09-15T05:04:12Z",
            silence_seconds=312,
        )) + "\n"
        "repair\tRun claude\t2026-09-15T05:09:25.0000000Z " + KILL_LINE + "\n"
    )


def quoting_log() -> str:
    """An agent-task log that QUOTES the kill line — this card's own body in
    prose, and a diff hunk of this card's own fixtures. DRE-2923's lesson: a
    log that repeats a marker must never classify as one."""
    quoted = "agent\tUNKNOWN STEP\t2026-09-25T11:00:00.0000000Z "
    return (
        quoted + "Runner name: 'bureau-mini-light-1'\n"
        + quoted + "Card: DRE-4847 — the tell was `" + KILL_LINE + "` on the "
        "agent step, and then on every later step within seconds.\n"
        + quoted + "+" + KILL_LINE + "\n"
        + quoted + "> " + KILL_LINE + "\n"
        + quoted + "##[error]Process completed with exit code 1.\n"
    )


def runner_line(name: str) -> str:
    return (
        "job\tUNKNOWN STEP\t2026-09-24T18:02:11.1234567Z "
        f"Runner name: '{name}'\n"
    )


# ── 1. the one reader ────────────────────────────────────────────────────────
class ReadingTheLogTest(unittest.TestCase):
    def test_the_agent_bureau_kill_names_the_runner_and_its_class(self):
        # Run 36093192182: ONE exit-137 line, and push-rescue then delivered
        # the branch. One line is the rule.
        kill = out_of_memory.from_log(fixture("agent-bureau-36093192182-attempt1.log"))
        self.assertIsNotNone(kill, "the kill line in this log was not read")
        self.assertEqual("bureau-mini-light-3", kill.runner)
        self.assertEqual(out_of_memory.LIGHT, kill.runner_class)
        self.assertEqual(1, kill.kills)

    def test_all_four_runs_are_read_with_their_own_kill_counts(self):
        for name, kills in THE_FOUR.items():
            with self.subTest(run=name):
                kill = out_of_memory.from_log(fixture(name))
                self.assertIsNotNone(kill)
                self.assertEqual(kills, kill.kills)
                self.assertEqual(out_of_memory.LIGHT, kill.runner_class)

    def test_the_critic_run_is_read_the_same_way(self):
        kill = out_of_memory.from_log(fixture(CRITIC))
        self.assertIsNotNone(kill)
        self.assertEqual(out_of_memory.LIGHT, kill.runner_class)
        self.assertEqual(1, kill.kills)

    def test_a_stopped_run_is_never_an_out_of_memory_kill(self):
        # `stream_watchdog.STALL_EXIT` is 124, "deliberately not 137" — but the
        # watchdog SIGKILLs the process group it stopped, so a 137 can land in
        # the same log. The stall line is the report; the 137 is its wake.
        self.assertEqual(124, stream_watchdog.STALL_EXIT)
        self.assertIsNone(out_of_memory.from_log(stalled_log()))

    def test_a_quoted_kill_line_is_a_quotation_not_a_report(self):
        self.assertIsNone(out_of_memory.from_log(quoting_log()))

    def test_a_log_with_no_kill_line_reads_nothing(self):
        self.assertIsNone(out_of_memory.from_log(
            runner_line("bureau-mini-light-1")
            + PREFIX + "##[error]Process completed with exit code 1.\n"
        ))
        self.assertIsNone(out_of_memory.from_log(""))
        self.assertIsNone(out_of_memory.from_log(None))

    def test_the_runner_names_the_class(self):
        cases = {
            "bureau-mini-light-1": out_of_memory.LIGHT,
            "bureau-mini-heavy-2": out_of_memory.HEAVY,
            "bureau-mini-3": out_of_memory.HEAVY,
            "GitHub Actions 7": out_of_memory.HOSTED,
            "some-other-runner": out_of_memory.UNKNOWN,
            "": out_of_memory.UNKNOWN,
        }
        for name, expected in cases.items():
            with self.subTest(runner=name):
                self.assertEqual(expected, out_of_memory.runner_class(name))

    def test_a_kill_with_no_runner_line_is_unknown_not_light(self):
        # Never guess a class: `unknown` declines the retry, and a wrong guess
        # of `light` would re-run a heavy job into the same wall.
        kill = out_of_memory.from_log(PREFIX + KILL_LINE + "\n")
        self.assertIsNotNone(kill)
        self.assertEqual(out_of_memory.UNKNOWN, kill.runner_class)
        self.assertEqual("", kill.runner)

    def test_the_description_names_the_runner_and_its_class_in_plain_words(self):
        kill = out_of_memory.from_log(fixture("portico-fix-35954619710.log"))
        said = out_of_memory.describe(kill)
        self.assertIn("bureau-mini-light-2", said)
        self.assertIn("light", said)
        self.assertIn("memory", said.lower())


# ── 2. the class ─────────────────────────────────────────────────────────────
class TheClassIsItsOwnTest(unittest.TestCase):
    def test_the_agent_bureau_run_classifies_out_of_memory(self):
        # The card's first criterion: today this run classifies `normal`.
        self.assertEqual(
            "out_of_memory",
            medic_classify.classify(
                "Agent Task (reusable)",
                fixture("agent-bureau-36093192182-attempt1.log"),
            ),
        )

    def test_all_four_runs_classify_out_of_memory(self):
        for name in THE_FOUR:
            with self.subTest(run=name):
                self.assertEqual(
                    "out_of_memory",
                    medic_classify.classify("Agent Task (reusable)", fixture(name)),
                )

    def test_a_stalled_run_is_still_stalled_no_stream(self):
        self.assertEqual(
            "stalled_no_stream",
            medic_classify.classify("Red-main Repair (reusable)", stalled_log()),
        )

    def test_a_log_that_only_quotes_the_kill_line_is_normal(self):
        self.assertEqual(
            "normal",
            medic_classify.classify("Agent Task (reusable)", quoting_log()),
        )

    def test_the_killed_critic_is_not_read_as_a_rate_limit(self):
        # DRE-1921's `critic_infra_crash` matches qa-review's neutral marker,
        # and a killed critic always lands in that fallback branch: it wrote no
        # execution file. The marker is in the SAME log, so the ordering is the
        # whole fix — exactly the lesson DRE-3428 and DRE-3991 each learned.
        log = fixture(CRITIC) + (
            "qa-review\tUNKNOWN STEP\t2026-09-25T09:30:44.0012345Z "
            + medic_classify.CRITIC_NEUTRAL_MARKER + "\n"
        )
        self.assertEqual(
            "out_of_memory", medic_classify.classify("QA Review (reusable)", log)
        )
        self.assertEqual(
            "out_of_memory",
            medic_classify.classify("QA Review (reusable)", fixture(CRITIC)),
        )

    def test_a_real_rate_limit_with_no_kill_is_still_a_critic_infra_crash(self):
        self.assertEqual(
            "critic_infra_crash",
            medic_classify.classify(
                "QA Review (reusable)",
                "review\tx\t2026-09-25T09:30:00.0000000Z "
                "API rate limit exceeded for installation ID 1\n",
            ),
        )

    def test_an_environment_crash_still_outranks_it(self):
        # A runner that cannot start Claude at all is named first, unchanged:
        # the class order is environment_crash, stall, out-of-memory.
        log = (
            runner_line("bureau-mini-light-1")
            + PREFIX + "Error: Claude Code native binary not found\n"
            + PREFIX + KILL_LINE + "\n"
        )
        self.assertEqual(
            "environment_crash",
            medic_classify.classify("Agent Task (reusable)", log),
        )

    def test_the_cli_keeps_the_dre_1921_gate_false_and_prints_key_values(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "medic-log.txt"
            path.write_text(fixture(CRITIC), encoding="utf-8")
            buf = io.StringIO()
            err = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                rc = medic_classify.main(["QA Review (reusable)", str(path)])
        out = buf.getvalue()
        self.assertEqual(0, rc)
        self.assertIn("class=out_of_memory", out)
        self.assertIn("infra_crash=false", out,
                      "a kill on a light runner keeps its one retry")
        for line in out.splitlines():
            self.assertIn("=", line, f"every printed line is a key=value: {line!r}")
        # The human line names the machine and its size, in plain words.
        self.assertIn("bureau-mini-light-2", err.getvalue())
        self.assertIn("light", err.getvalue())


# ── 3. the retry rule ────────────────────────────────────────────────────────
class OneRetryOnALightRunnerTest(unittest.TestCase):
    def kill(self, name: str = "agent-bureau-36093192182-attempt1.log"):
        return out_of_memory.from_log(fixture(name))

    def test_a_first_kill_on_a_light_runner_keeps_its_retry(self):
        self.assertEqual(
            "", medic_retry.out_of_memory_refusal(self.kill(), attempt=1)
        )
        self.assertTrue(medic_retry.decide(out_of_memory="").retry)

    def test_a_second_kill_on_a_light_runner_is_declined(self):
        detail = medic_retry.out_of_memory_refusal(self.kill(), attempt=2)
        self.assertTrue(detail, "attempt 2 has already spent the one retry")
        decision = medic_retry.decide(out_of_memory=detail)
        self.assertEqual(medic_retry.DECLINE, decision.action)
        self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY, decision.rule)

    def test_a_heavy_or_hosted_or_unknown_runner_gets_no_retry_at_all(self):
        for name in ("bureau-mini-heavy-1", "GitHub Actions 3", "mystery-box"):
            with self.subTest(runner=name):
                kill = out_of_memory.from_log(
                    runner_line(name) + PREFIX + KILL_LINE + "\n"
                )
                detail = medic_retry.out_of_memory_refusal(kill, attempt=1)
                self.assertTrue(detail, f"{name} must not get the light retry")
                decision = medic_retry.decide(out_of_memory=detail)
                self.assertEqual(medic_retry.DECLINE, decision.action)
                self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY, decision.rule)

    def test_a_run_that_was_not_killed_is_not_this_rule(self):
        self.assertEqual("", medic_retry.out_of_memory_refusal(None, attempt=2))

    def test_the_receipt_names_the_machine_and_the_two_remedies(self):
        decision = medic_retry.decide(
            out_of_memory=medic_retry.out_of_memory_refusal(
                out_of_memory.from_log(
                    runner_line("bureau-mini-heavy-1") + PREFIX + KILL_LINE + "\n"
                ),
                attempt=1,
            )
        )
        receipt = medic_retry.declined_comment(decision, run_url="https://x/1")
        self.assertIn("bureau-mini-heavy-1", receipt)
        self.assertIn("heavy", receipt)
        self.assertIn("memory", receipt)
        self.assertIn("bigger machine", receipt)
        self.assertIn("smaller card", receipt)
        self.assertIn(medic_retry.RULE_OUT_OF_MEMORY, receipt)
        self.assertIn("https://x/1", receipt)

    def test_a_park_still_outranks_an_out_of_memory_kill(self):
        decision = medic_retry.decide(
            parked_because="the 'needs-human' label is on it",
            out_of_memory=medic_retry.out_of_memory_refusal(self.kill(), attempt=2),
        )
        self.assertEqual(medic_retry.RULE_PARKED, decision.rule)


class TheDecideCliTest(unittest.TestCase):
    """`decide` reads the `--log` it already takes, and still prints exactly
    four keys — they cross a `$GITHUB_OUTPUT` boundary one key per line."""

    def run_decide(self, log_text: str, attempt: str) -> dict:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "medic-log.txt"
            path.write_text(log_text, encoding="utf-8")
            facts = {"state": "In Progress", "labels": [], "comments": []}
            buf = io.StringIO()
            with mock.patch.object(medic_retry, "card_facts", return_value=facts):
                with contextlib.redirect_stdout(buf):
                    rc = medic_retry.main([
                        "decide", "--branch", "agent/DRE-4847-oom",
                        "--log", str(path), "--run-id", "36093192182",
                        "--run-attempt", attempt,
                    ])
        self.assertEqual(0, rc)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertEqual(4, len(lines), f"decide prints four keys: {lines}")
        return dict(ln.split("=", 1) for ln in lines)

    def test_a_first_kill_on_a_light_runner_retries(self):
        out = self.run_decide(
            fixture("agent-bureau-36093192182-attempt1.log"), "1"
        )
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_NONE, out["rule"])
        self.assertEqual("DRE-4847", out["card"])

    def test_a_second_kill_on_a_light_runner_declines(self):
        out = self.run_decide(
            fixture("agent-bureau-36093192182-attempt1.log"), "2"
        )
        self.assertEqual("false", out["retry"])
        self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY, out["rule"])
        self.assertIn("bureau-mini-light-3", out["detail"])

    def test_a_kill_on_a_heavy_runner_declines_on_the_first_attempt(self):
        out = self.run_decide(
            runner_line("bureau-mini-heavy-1") + PREFIX + KILL_LINE + "\n", "1"
        )
        self.assertEqual("false", out["retry"])
        self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY, out["rule"])

    def test_a_kill_on_a_hosted_runner_declines_on_the_first_attempt(self):
        out = self.run_decide(
            runner_line("GitHub Actions 5") + PREFIX + KILL_LINE + "\n", "1"
        )
        self.assertEqual("false", out["retry"])
        self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY, out["rule"])


# ── 4. the medic, wired ──────────────────────────────────────────────────────
class TheMedicIsWiredToItTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = yaml.safe_load(MEDIC.read_text(encoding="utf-8"))["jobs"]

    def test_diagnose_is_gated_off_the_class(self):
        # The diagnosis agent reads a log that already says what happened, and
        # it is a Claude run on the machine that just ran out of memory.
        self.assertIn(
            "needs.classify.outputs.class != 'out_of_memory'",
            self.jobs["diagnose"]["if"],
        )

    def test_retry_declined_admits_the_rule_on_a_later_attempt(self):
        # The medic's retry is `gh run rerun --failed`, which reuses the run
        # id, so the second kill in a row IS attempt 2 — and under the
        # attempt-1 clause alone the refusal would be silent.
        self.assertIn(
            f"rule == '{medic_retry.RULE_OUT_OF_MEMORY}'",
            self.jobs["retry_declined"]["if"],
        )

    def test_the_one_retry_gate_does_not_name_the_class(self):
        # A light runner's first kill keeps its ordinary retry; the refusal for
        # every other case comes from the DRE-2954 gate, not from a new term.
        self.assertNotIn("out_of_memory", self.jobs["retry"]["if"])


class GateReplayTest(unittest.TestCase):
    def test_a_first_kill_retries_and_never_diagnoses(self):
        fired, outputs = fired_jobs(
            workflow_name="Agent Task (reusable)",
            log_text=fixture("agent-bureau-36093192182-attempt1.log"),
            attempt=1, card="DRE-4847",
        )
        self.assertEqual("out_of_memory", outputs["class"])
        self.assertEqual({"classify", "retry"}, fired)

    def test_a_second_kill_says_so_once_and_never_diagnoses(self):
        fired, outputs = fired_jobs(
            workflow_name="Agent Task (reusable)",
            log_text=fixture("agent-bureau-36093192182-attempt1.log"),
            attempt=2, card="DRE-4847",
            retry="false", rule=medic_retry.RULE_OUT_OF_MEMORY,
        )
        self.assertEqual("out_of_memory", outputs["class"])
        self.assertEqual({"classify", "retry_declined"}, fired)

    def test_the_killed_critic_gets_no_rate_limit_sentence(self):
        # `backoff` is the job that posts "an infrastructure rate-limit … NOT
        # retrying", and it fires on `infra_crash == 'true'`. This class keeps
        # that gate false, so the sentence never reaches the card.
        fired, outputs = fired_jobs(
            workflow_name="QA Review (reusable)",
            log_text=fixture(CRITIC) + (
                "qa-review\tUNKNOWN STEP\t2026-09-25T09:30:44.0012345Z "
                + medic_classify.CRITIC_NEUTRAL_MARKER + "\n"
            ),
            attempt=1, card="DRE-4847",
        )
        self.assertEqual("out_of_memory", outputs["class"])
        self.assertEqual("false", outputs["infra_crash"])
        self.assertNotIn("backoff", fired)
        self.assertNotIn("diagnose", fired)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
