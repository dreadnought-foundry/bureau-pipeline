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
  3. `medic_retry` keeps the medic's one retry for a run's FIRST kill, on any
     runner, and declines the run's second kill (DRE-8239, which dropped
     DRE-4847's light-runner-only rule). "First" is counted per run: an
     attempt-2 kill whose attempt 1 was not killed keeps the retry.
  4. The medic's gates, evaluated: an out-of-memory failure never fires
     `diagnose`, a second kill fires `retry_declined`, the `retry` job admits
     a first kill on attempt 2, and nothing retries attempt 3 or later.
  5. The receipt names the machine off the Jobs API, then the log's
     `Runner name:` line, then "an unnamed runner" (DRE-8239).

THE FIXTURES are short excerpts in the shape `gh run view --log-failed` writes
(`job<TAB>step<TAB><ISO timestamp> <content>`; with gh 2.100.0 every step reads
`UNKNOWN STEP`), trimmed to the runner-name line and the lines around each kill.
bureau-pipeline is public, so they carry no agent output and no card text.

Run: cd bureau-pipeline && python3 -m pytest tests/test_medic_out_of_memory.py -v
"""

from __future__ import annotations

import contextlib
import io
import itertools
import json
import os
import re
import subprocess
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
from test_medic_environment_hold import fired_jobs, fires  # noqa: E402

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


#: The runner that killed agent-bureau #4248's shards (DRE-8215, CI run
#: 40616890320) — a `bureau-heavy` machine, capped at 4 GB.
SHARD_RUNNER = "bureau-mini-PQk8rgNMXNXGn"
SHARD_JOB = "Console backend shard 1"


def shard_log(runner: str | None = SHARD_RUNNER) -> str:
    """Run 40616890320's attempt-1 shape: all three `Console backend shard N`
    jobs killed with exit 137, between 6:59 and 7:07 PM PT on 10-27. With
    `runner=None` the log carries no `Runner name:` line at all — the shape
    the medic's own fetch had on the bureau pool's light runners."""
    text = ""
    for n, minute in ((1, "01:59"), (2, "02:03"), (3, "02:07")):
        prefix = f"Console backend shard {n}\tUNKNOWN STEP\t2026-10-28T{minute}:10.0000000Z "
        if runner is not None:
            name = runner if n == 1 else f"bureau-mini-shard{n}"
            text += prefix + f"Runner name: '{name}'\n"
        text += prefix + "Run the backend suite, shard %d of 3\n" % n
        text += prefix + KILL_LINE + "\n"
    return text


def cancelled_log() -> str:
    """Run 40596656325's attempt 1 (agent-bureau #4240, DRE-8022): it failed on
    a cancellation, with no kill line anywhere."""
    prefix = "Console backend\tUNKNOWN STEP\t2026-10-27T22:59:10.0000000Z "
    return (
        prefix + "Runner name: 'bureau-studio-light-2'\n"
        + prefix + "##[error]backend shards concluded 'cancelled'\n"
        + prefix + "##[error]Process completed with exit code 1.\n"
    )


def jobs_reading(runner: str = SHARD_RUNNER, label: str = "bureau-heavy") -> str:
    """`repos/<repo>/actions/runs/<id>/attempts/<n>/jobs` as the API answers
    it: the killed shard among jobs that passed and an aggregator that failed
    because the shards did."""
    return json.dumps({"total_count": 4, "jobs": [
        {"name": "Console frontend", "conclusion": "success",
         "runner_name": "bureau-studio-light-1",
         "labels": ["self-hosted", "bureau-light"]},
        {"name": "Console backend", "conclusion": "failure",
         "runner_name": "bureau-studio-light-2",
         "labels": ["self-hosted", "bureau-light"]},
        {"name": SHARD_JOB, "conclusion": "failure", "runner_name": runner,
         "labels": ["self-hosted", label, "machine-mini-1"]},
        {"name": "Console backend shard 2", "conclusion": "failure",
         "runner_name": "bureau-mini-shard2",
         "labels": ["self-hosted", "bureau-heavy", "machine-mini-1"]},
    ]})


def decide_cli(log_text: str, attempt: str, *, prior: str | None = None,
               prior_missing: bool = False, jobs: str | None = None,
               branch: str = "agent/DRE-4847-oom") -> tuple:
    """`medic_retry.py decide`, as medic.yml calls it, with the card read
    faked. `prior` is attempt 1's failed log; `prior_missing` names a file
    that is not there, the shape an attempt-1 read that failed leaves.
    Returns the four printed keys and what was said on stderr."""
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "medic-log.txt"
        path.write_text(log_text, encoding="utf-8")
        argv = ["decide", "--branch", branch, "--log", str(path),
                "--run-id", "40616890320", "--run-attempt", attempt]
        if prior is not None or prior_missing:
            earlier = Path(raw) / "medic-log-attempt1.txt"
            if prior is not None:
                earlier.write_text(prior, encoding="utf-8")
            argv += ["--prior-log", str(earlier)]
        if jobs is not None:
            reading = Path(raw) / "medic-jobs.json"
            reading.write_text(jobs, encoding="utf-8")
            argv += ["--jobs", str(reading)]
        facts = {"state": "In Progress", "labels": [], "comments": []}
        buf, err = io.StringIO(), io.StringIO()
        with mock.patch.object(medic_retry, "card_facts", return_value=facts):
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                rc = medic_retry.main(argv)
    assert rc == 0, rc
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 4, f"decide prints four keys: {lines}"
    return dict(ln.split("=", 1) for ln in lines), err.getvalue()


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

    def test_the_reader_names_the_job_of_the_first_kill(self):
        # The job field of the first kill line is what picks the killed job
        # out of the Jobs API reading (DRE-8239).
        kill = out_of_memory.from_log(shard_log())
        self.assertEqual(3, kill.kills)
        self.assertEqual(SHARD_RUNNER, kill.runner)
        self.assertEqual(SHARD_JOB, kill.job)


# ── 1b. the machine, off the Jobs API (DRE-8239) ─────────────────────────────
class TheMachineFromTheJobsApiTest(unittest.TestCase):
    def test_the_killed_job_is_picked_by_name(self):
        self.assertEqual(
            (SHARD_RUNNER, "bureau-heavy"),
            out_of_memory.machine_from_jobs(jobs_reading(), job=SHARD_JOB),
        )

    def test_with_no_job_name_the_first_failed_job_with_a_runner_is_named(self):
        self.assertEqual(
            ("bureau-studio-light-2", "bureau-light"),
            out_of_memory.machine_from_jobs(jobs_reading(), job=""),
        )

    def test_only_a_class_label_is_named(self):
        for label in ("bureau-heavy", "bureau-light", "bureau-tiny"):
            with self.subTest(label=label):
                self.assertEqual(
                    label,
                    out_of_memory.machine_from_jobs(
                        jobs_reading(label=label), job=SHARD_JOB)[1],
                )
        self.assertEqual(
            (SHARD_RUNNER, ""),
            out_of_memory.machine_from_jobs(
                jobs_reading(label="linux"), job=SHARD_JOB),
        )

    def test_a_reading_that_failed_names_nothing(self):
        for text in ("", "not json", "gh: HTTP 403", "[]", '{"jobs": "x"}', None):
            with self.subTest(text=text):
                self.assertEqual(
                    ("", ""), out_of_memory.machine_from_jobs(text, job=SHARD_JOB))

    def test_the_jobs_api_wins_over_the_log_and_the_log_over_nothing(self):
        named = out_of_memory.from_log(shard_log(runner="bureau-mini-from-log"))
        self.assertEqual(
            (SHARD_RUNNER, "bureau-heavy"),
            (out_of_memory.named(named, SHARD_RUNNER, "bureau-heavy").runner,
             out_of_memory.named(named, SHARD_RUNNER, "bureau-heavy").label),
        )
        self.assertEqual(
            "bureau-mini-from-log", out_of_memory.named(named, "", "").runner)
        unnamed = out_of_memory.from_log(shard_log(runner=None))
        self.assertEqual("", out_of_memory.named(unnamed, "", "").runner)
        self.assertIn("an unnamed runner",
                      out_of_memory.describe(out_of_memory.named(unnamed, "", "")))
        self.assertIsNone(out_of_memory.named(None, SHARD_RUNNER, "bureau-heavy"))

    def test_the_description_names_the_runner_and_its_label(self):
        kill = out_of_memory.named(
            out_of_memory.from_log(shard_log(runner=None)),
            SHARD_RUNNER, "bureau-heavy")
        said = out_of_memory.describe(kill)
        self.assertIn(SHARD_RUNNER, said)
        self.assertIn("bureau-heavy", said)
        self.assertIn("3 times in this run", said)


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
class TheFirstKillKeepsItsRetryTest(unittest.TestCase):
    """DRE-8239: the run's FIRST out-of-memory kill keeps the medic's one
    retry on any runner. A 4 GB heavy runner's kill is not deterministic —
    the green re-runs of runs 40596656325 and 40616890320 landed on the same
    class of machine — and a refused retry has cost a stall of hours."""

    def kill(self, name: str = "agent-bureau-36093192182-attempt1.log"):
        return out_of_memory.from_log(fixture(name))

    def test_a_first_kill_keeps_its_retry_on_every_class_of_runner(self):
        for name in ("bureau-mini-light-1", "bureau-mini-heavy-1",
                     SHARD_RUNNER, "bureau-studio-Xk2", "GitHub Actions 3",
                     "mystery-box"):
            with self.subTest(runner=name):
                kill = out_of_memory.from_log(
                    runner_line(name) + PREFIX + KILL_LINE + "\n")
                self.assertEqual(
                    "", medic_retry.out_of_memory_refusal(kill, attempt=1))
                decision = medic_retry.decide(first_kill=True)
                self.assertTrue(decision.retry)
                self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY_FIRST,
                                 decision.rule)

    def test_a_second_kill_in_the_same_run_is_declined(self):
        detail = medic_retry.out_of_memory_refusal(
            self.kill(), attempt=2, earlier_log=shard_log())
        self.assertIn("second out-of-memory kill", detail)
        decision = medic_retry.decide(out_of_memory=detail, first_kill=False)
        self.assertEqual(medic_retry.DECLINE, decision.action)
        self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY, decision.rule)

    def test_an_attempt_two_kill_after_an_attempt_one_without_one_keeps_it(self):
        self.assertEqual("", medic_retry.out_of_memory_refusal(
            self.kill(), attempt=2, earlier_log=cancelled_log()))

    def test_a_stopped_attempt_one_is_not_a_kill_either(self):
        # The watchdog's stall line wins in attempt 1's log too: a stall is
        # never counted as the run's first out-of-memory kill.
        self.assertEqual("", medic_retry.out_of_memory_refusal(
            self.kill(), attempt=2, earlier_log=stalled_log()))

    def test_an_unread_attempt_one_keeps_the_retry(self):
        # Fail open, the way an unreadable card does: a wasted retry costs one
        # CI run, and the retry job never runs past attempt 2.
        self.assertEqual("", medic_retry.out_of_memory_refusal(
            self.kill(), attempt=2, earlier_log=None))

    def test_a_kill_on_attempt_three_or_later_is_declined(self):
        for attempt in (3, 4, 9):
            with self.subTest(attempt=attempt):
                detail = medic_retry.out_of_memory_refusal(
                    self.kill(), attempt=attempt, earlier_log=cancelled_log())
                self.assertIn(f"attempt {attempt}", detail)

    def test_a_run_that_was_not_killed_is_not_this_rule(self):
        self.assertEqual("", medic_retry.out_of_memory_refusal(
            None, attempt=2, earlier_log=shard_log()))
        self.assertEqual(medic_retry.RULE_NONE, medic_retry.decide().rule)

    def test_the_receipt_names_the_machine_and_the_two_remedies(self):
        decision = medic_retry.decide(
            out_of_memory=medic_retry.out_of_memory_refusal(
                out_of_memory.from_log(
                    runner_line("bureau-mini-heavy-1") + PREFIX + KILL_LINE + "\n"
                ),
                attempt=2, earlier_log=shard_log(),
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
        # The premise DRE-8239 found wrong three times running is gone.
        self.assertNotIn("same size of machine dies the same way", receipt)

    def test_a_park_still_outranks_an_out_of_memory_kill(self):
        decision = medic_retry.decide(
            parked_because="the 'needs-human' label is on it",
            out_of_memory=medic_retry.out_of_memory_refusal(
                self.kill(), attempt=2, earlier_log=shard_log()),
        )
        self.assertEqual(medic_retry.RULE_PARKED, decision.rule)
        parked = medic_retry.decide(parked_because="x", first_kill=True)
        self.assertEqual(medic_retry.RULE_PARKED, parked.rule)
        self.assertFalse(parked.retry)

    def test_a_turn_cap_still_outranks_a_first_kill(self):
        turned = {"is_error": True, "subtype": "error_max_turns", "num_turns": "60"}
        decision = medic_retry.decide(execution=turned, first_kill=True)
        self.assertEqual(medic_retry.RULE_TURN_EXHAUSTION, decision.rule)


class TheDecideCliTest(unittest.TestCase):
    """`decide` reads the `--log` it already takes — and, for a kill, attempt
    1's log and the Jobs API reading — and still prints exactly four keys:
    they cross a `$GITHUB_OUTPUT` boundary one key per line."""

    def test_run_40616890320_attempt_1_retries(self):
        out, _ = decide_cli(shard_log(), "1", jobs=jobs_reading())
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY_FIRST, out["rule"])
        self.assertEqual("DRE-4847", out["card"])

    def test_the_same_log_on_attempt_2_after_a_killed_attempt_1_declines(self):
        out, _ = decide_cli(shard_log(), "2", prior=shard_log())
        self.assertEqual("false", out["retry"])
        self.assertEqual("out-of-memory", out["rule"])
        self.assertIn(SHARD_RUNNER, out["detail"])
        self.assertIn("second out-of-memory kill", out["detail"])

    def test_run_40596656325_attempt_2_after_a_cancellation_retries(self):
        out, err = decide_cli(shard_log(), "2", prior=cancelled_log(),
                              jobs=jobs_reading())
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY_FIRST, out["rule"])
        self.assertNotIn("::warning", err)

    def test_an_attempt_2_kill_whose_attempt_1_could_not_be_read_retries_out_loud(self):
        out, err = decide_cli(shard_log(), "2", prior_missing=True)
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY_FIRST, out["rule"])
        self.assertIn("::warning::", err)
        self.assertIn("attempt 1", err)

    def test_an_attempt_3_kill_declines(self):
        out, _ = decide_cli(shard_log(), "3")
        self.assertEqual("false", out["retry"])
        self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY, out["rule"])

    def test_a_first_kill_retries_on_hosted_studio_and_light_runners(self):
        for name in ("GitHub Actions 5", "bureau-studio-CrmoqTRwqCvXF",
                     "bureau-mini-light-3", "bureau-mini-heavy-1"):
            with self.subTest(runner=name):
                out, _ = decide_cli(
                    runner_line(name) + PREFIX + KILL_LINE + "\n", "1")
                self.assertEqual("true", out["retry"])
                self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY_FIRST, out["rule"])

    def test_a_failure_with_no_kill_keeps_rule_none(self):
        out, _ = decide_cli(cancelled_log(), "1")
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_NONE, out["rule"])

    def test_a_stopped_run_is_still_a_stall_and_never_a_first_kill(self):
        out, _ = decide_cli(stalled_log(), "1")
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_NONE, out["rule"])


# ── 3b. the receipt names the machine (DRE-8239) ─────────────────────────────
class TheReceiptNamesTheMachineTest(unittest.TestCase):
    def receipt(self, **kwargs) -> str:
        out, _ = decide_cli(shard_log(runner=None), "2", prior=shard_log(), **kwargs)
        self.assertEqual("false", out["retry"])
        return medic_retry.declined_comment(
            medic_retry.Decision(medic_retry.DECLINE, out["rule"], out["detail"]),
            run_url="https://github.com/o/r/actions/runs/40616890320")

    def test_a_log_with_no_runner_line_is_named_off_the_jobs_api(self):
        receipt = self.receipt(jobs=jobs_reading())
        self.assertIn(SHARD_RUNNER, receipt)
        self.assertIn("bureau-heavy", receipt)
        self.assertNotIn("an unnamed runner", receipt)

    def test_with_neither_the_receipt_still_reads_an_unnamed_runner(self):
        for jobs in (None, "", "gh: HTTP 403"):
            with self.subTest(jobs=jobs):
                self.assertIn("an unnamed runner", self.receipt(jobs=jobs))

    def test_a_jobs_api_that_failed_falls_back_to_the_logs_runner_line(self):
        out, _ = decide_cli(shard_log(runner="bureau-mini-from-log"), "2",
                            prior=shard_log(), jobs="gh: HTTP 502")
        self.assertIn("bureau-mini-from-log", out["detail"])


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
        # Whether a kill keeps its retry is the DRE-2954 gate's answer, not a
        # class term here: the job reads only the gate's rule.
        self.assertNotIn("out_of_memory", self.jobs["retry"]["if"])

    def test_the_retry_job_reruns_the_failed_jobs_and_names_the_attempt(self):
        (step,) = self.jobs["retry"]["steps"]
        self.assertIn("gh run rerun", step["run"])
        self.assertIn("--failed", step["run"])
        self.assertNotIn("(attempt 1 failed)", step["run"])


# The facts the `retry` job's `if:` reads, and every value each can carry.
# `rule` is every rule the retry gate names, and the empty answer of a gate
# that never ran.
def _rules() -> tuple:
    return ("", medic_retry.RULE_NONE, medic_retry.RULE_PARKED,
            medic_retry.RULE_TURN_EXHAUSTION, medic_retry.RULE_STALLED_REPEAT,
            medic_retry.RULE_OUT_OF_MEMORY, medic_retry.RULE_OUT_OF_MEMORY_FIRST,
            medic_retry.RULE_REFUSED_BY_DESIGN, medic_retry.RULE_SUPERSEDED)


_CLASSES = ("normal", "out_of_memory", "stalled_no_stream", "environment_crash",
            "critic_infra_crash", "upstream_5xx", "linear_ratelimited",
            "standing_defect", "")


def _retry_contexts(attempts):
    for attempt, conclusion, crash, kind, limit, retry, rule in itertools.product(
            attempts, ("failure", "timed_out"), ("true", "false", ""), _CLASSES,
            ("true", "false", ""), ("true", "false", ""), _rules()):
        yield {
            "github.event.workflow_run.conclusion": conclusion,
            "github.event.workflow_run.run_attempt": attempt,
            "needs.classify.outputs.infra_crash": crash,
            "needs.classify.outputs.class": kind,
            "needs.classify.outputs.limit": limit,
            "needs.classify.outputs.retry": retry,
            "needs.classify.outputs.rule": rule,
            "needs.classify.outputs.card": "DRE-8239",
        }


def _retrying_step() -> dict:
    jobs = yaml.safe_load(MEDIC.read_text(encoding="utf-8"))["jobs"]
    (step,) = [s for s in jobs["stall_record"]["steps"]
               if s.get("name") == "Record the stall on the card"]
    return step


def _unwrapped(value: str) -> str:
    value = " ".join((value or "").split())
    assert value.startswith("${{") and value.endswith("}}"), value
    return " ".join(value[3:-2].split())


class TheRetryJobsAttemptsTest(unittest.TestCase):
    """DRE-8239: the `retry` job admits a first kill on attempt 2, and no
    condition at all on attempt 3 or later."""

    @classmethod
    def setUpClass(cls):
        cls.jobs = yaml.safe_load(MEDIC.read_text(encoding="utf-8"))["jobs"]
        cls.gate = cls.jobs["retry"]["if"]

    def test_no_condition_retries_attempt_3_or_later(self):
        fired = [c for c in _retry_contexts((3, 4, 5, 10)) if fires(self.gate, c)]
        self.assertEqual([], fired[:3])

    def test_on_attempt_2_only_a_first_kill_is_admitted(self):
        admitted = {c["needs.classify.outputs.rule"]
                    for c in _retry_contexts((2,)) if fires(self.gate, c)}
        self.assertEqual({medic_retry.RULE_OUT_OF_MEMORY_FIRST}, admitted)

    def test_attempt_1_is_unchanged(self):
        # Every rule but a refusal still retries attempt 1 — the gate's
        # `!= 'false'` and the class terms decide, exactly as before.
        context = next(c for c in _retry_contexts((1,))
                       if c["needs.classify.outputs.retry"] == "true"
                       and c["needs.classify.outputs.class"] == "normal"
                       and c["needs.classify.outputs.infra_crash"] == "false"
                       and c["needs.classify.outputs.limit"] == "false"
                       and c["needs.classify.outputs.rule"] == "")
        self.assertTrue(fires(self.gate, context))

    def test_stall_records_retrying_is_the_retry_jobs_condition(self):
        retrying = _unwrapped(_retrying_step()["env"]["RETRYING"])
        self.assertEqual(retrying, " ".join(self.gate.split()))
        for context in _retry_contexts((1, 2, 3)):
            if context["needs.classify.outputs.class"] not in ("out_of_memory", "normal"):
                continue
            self.assertEqual(fires(self.gate, context), fires(retrying, context))

    def test_run_40596656325s_attempt_2_kill_is_retried_and_nothing_else_fires(self):
        out, _ = decide_cli(shard_log(), "2", prior=cancelled_log())
        fired, outputs = fired_jobs(
            workflow_name="CI", log_text=shard_log(), attempt=2,
            card="DRE-8022", retry=out["retry"], rule=out["rule"],
        )
        self.assertEqual("out_of_memory", outputs["class"])
        self.assertEqual({"classify", "retry"}, fired)


class GateReplayTest(unittest.TestCase):
    def test_a_first_kill_retries_and_never_diagnoses(self):
        fired, outputs = fired_jobs(
            workflow_name="Agent Task (reusable)",
            log_text=fixture("agent-bureau-36093192182-attempt1.log"),
            attempt=1, card="DRE-4847", rule=medic_retry.RULE_OUT_OF_MEMORY_FIRST,
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


# ── 5. the gate step, executed (DRE-8239) ────────────────────────────────────
GH_SHIM = r"""#!/bin/bash
printf '%s\n' "$*" >> "$SHIM_LOG"
case "$1" in
  api) [ "${FAKE_JOBS_RC:-0}" = 0 ] || exit "$FAKE_JOBS_RC"; cat "$FAKE_JOBS" ;;
  run)
    case "$*" in
      *--attempt*) [ "${FAKE_PRIOR_RC:-0}" = 0 ] || exit "$FAKE_PRIOR_RC"; cat "$FAKE_PRIOR" ;;
      *) printf '%s\n' "Run the backend suite" ;;
    esac ;;
esac
exit 0
"""

STEP_RUN_ID = "40596656325"


def _render(text: str, values: dict) -> str:
    def sub(match):
        key = match.group(1).strip()
        if key not in values:
            raise AssertionError(f"the step reads {key!r}, which this test does not fake")
        return values[key]

    return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, text)


def run_gate_step(log_text: str, *, attempt: str, prior: str = "",
                  prior_rc: int = 0, jobs: str = "", jobs_rc: int = 0) -> tuple:
    """Execute the classify job's gate step under the workflow's shell flags,
    with a fake `gh` and the real scripts. Returns the process, the `gh`
    calls it made and the `$GITHUB_OUTPUT` it wrote."""
    step = next(s for s in yaml.safe_load(MEDIC.read_text(encoding="utf-8"))
                ["jobs"]["classify"]["steps"] if s.get("id") == "r")
    values = {
        "secrets.LINEAR_API_KEY": "test-key",
        # No card in the branch or the log, so no Linear read is made.
        "github.event.workflow_run.head_branch": "agent/console-shards",
        "github.event.workflow_run.run_started_at": "2026-10-27T22:20:02Z",
        "github.event.workflow_run.id": STEP_RUN_ID,
        "github.event.workflow_run.run_attempt": attempt,
        "github.event.workflow_run.name": "CI",
        "github.event.workflow_run.event": "pull_request",
        "github.event.workflow_run.head_sha": "a" * 40,
        "github.event.repository.default_branch": "main",
        "github.token": "test-token",
    }
    env = {k: _render(str(v), values) for k, v in (step.get("env") or {}).items()}
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        (td / "bin" / "gh").write_text(GH_SHIM)
        os.chmod(td / "bin" / "gh", 0o755)
        (td / ".bureau-pipeline").symlink_to(ROOT)
        log = td / "medic-log.txt"
        log.write_text(log_text)
        (td / "prior.txt").write_text(prior)
        (td / "jobs.json").write_text(jobs)
        calls = td / "calls.log"
        calls.touch()
        output = td / "output"
        output.touch()
        script = step["run"].replace("/tmp/medic-log.txt", str(log))
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
            cwd=td, capture_output=True, text=True, check=False,
            env={**os.environ, **env, "PATH": f"{td / 'bin'}:{os.environ['PATH']}",
                 "SHIM_LOG": str(calls),
                 "FAKE_PRIOR": str(td / "prior.txt"), "FAKE_PRIOR_RC": str(prior_rc),
                 "FAKE_JOBS": str(td / "jobs.json"), "FAKE_JOBS_RC": str(jobs_rc),
                 "GITHUB_REPOSITORY": "o/r", "GITHUB_OUTPUT": str(output),
                 "RUNNER_TEMP": str(td)},
        )
        written = dict(line.split("=", 1) for line in output.read_text().splitlines()
                       if "=" in line)
        return proc, [c for c in calls.read_text().splitlines() if c], written


JOBS_READ = f"api repos/o/r/actions/runs/{STEP_RUN_ID}/attempts/"
PRIOR_READ = f"run view {STEP_RUN_ID} --attempt 1 --log-failed"


class TheGateStepReadsTest(unittest.TestCase):
    def test_an_attempt_2_kill_after_a_cancellation_is_retried(self):
        proc, calls, out = run_gate_step(
            shard_log(), attempt="2", prior=cancelled_log(), jobs=jobs_reading())
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(any(c.startswith(JOBS_READ + "2/jobs") for c in calls), calls)
        self.assertTrue(any(c.startswith(PRIOR_READ) for c in calls), calls)
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY_FIRST, out["rule"])

    def test_a_second_kill_is_declined_and_named_off_the_jobs_api(self):
        proc, _calls, out = run_gate_step(
            shard_log(runner=None), attempt="2", prior=shard_log(),
            jobs=jobs_reading())
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("false", out["retry"])
        self.assertEqual(medic_retry.RULE_OUT_OF_MEMORY, out["rule"])
        self.assertIn(SHARD_RUNNER, out["detail"])
        self.assertIn("bureau-heavy", out["detail"])

    def test_reads_that_fail_never_fail_the_step(self):
        proc, _calls, out = run_gate_step(
            shard_log(runner=None), attempt="2", prior_rc=1, jobs_rc=1)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("true", out["retry"])
        self.assertIn("::warning::", proc.stderr)

    def test_attempt_1_reads_the_jobs_api_and_no_earlier_log(self):
        proc, calls, out = run_gate_step(shard_log(), attempt="1", jobs=jobs_reading())
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(any(c.startswith(JOBS_READ + "1/jobs") for c in calls), calls)
        self.assertFalse(any("--attempt" in c for c in calls), calls)
        self.assertEqual("true", out["retry"])

    def test_a_failure_with_no_kill_makes_neither_read(self):
        proc, calls, out = run_gate_step(cancelled_log(), attempt="2")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertFalse(any(c.startswith("api ") for c in calls), calls)
        self.assertFalse(any("--attempt" in c for c in calls), calls)
        self.assertEqual(medic_retry.RULE_NONE, out["rule"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
