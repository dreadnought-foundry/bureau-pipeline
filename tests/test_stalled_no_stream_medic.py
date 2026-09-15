"""A stall is its own kind of failure: one retry, then one notice (DRE-3991).

`scripts/stream_watchdog.py` stops a Claude run that has said nothing for five
minutes and writes one line into the run log. This file is the other half —
what the MEDIC does when that run lands on it.

Three things it must not be mistaken for, because all three were already
available on 2026-09-15 and each sends the next reader somewhere useless:

  * **a code failure** — the agent never read the failing build, let alone
    wrote a line, so there is nothing about the work to reject;
  * **a credential failure** — `system/init` succeeded, which is exactly the
    fingerprint DRE-3428 exists to tell apart from a refused token;
  * **a limit death** — no wall was hit; the run simply went quiet.

And the shape of the answer, which is the shape DRE-3428 already uses for the
class next door: the FIRST stall gets the medic's one automatic retry and a
record on the card saying what went quiet and for how long; a SECOND
consecutive stall on the same card gets one plain-English notice and no third
build. A stall that retried forever would be the DRE-1921 loop with a new
name.
"""

from __future__ import annotations

import io
import contextlib
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import dead_run  # noqa: E402
import medic_classify  # noqa: E402
import medic_retry  # noqa: E402
import reviewer_environment  # noqa: E402
import stream_watchdog  # noqa: E402

MEDIC = ROOT / ".github" / "workflows" / "medic.yml"

STALL = stream_watchdog.Stall(
    step="Red-main Repair / repair",
    last_event="2026-09-15T05:04:12Z",
    silence_seconds=312,
)


def stalled_log(extra: str = "") -> str:
    """A failed run's log as `gh run view --log-failed` prints it."""
    return (
        "repair\tRun claude\t2026-09-15T05:04:12.0000000Z " + INIT_LINE + "\n"
        + extra
        + "repair\tRun claude\t2026-09-15T05:09:24.0000000Z "
        + stream_watchdog.stall_line(STALL) + "\n"
    )


INIT_LINE = '{"type":"system","subtype":"init","session_id":"s1"}'


class LogFileMixin:
    """The classifier and the retry gate both take a log FILE, as medic.yml
    hands them one. Writing a real file keeps the CLI under test."""

    def write_log(self, text: str) -> str:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "medic-log.txt"
        path.write_text(text)
        return str(path)


class TheClassIsItsOwnTest(LogFileMixin, unittest.TestCase):
    def test_a_stalled_run_classifies_as_stalled_no_stream(self):
        self.assertEqual(
            "stalled_no_stream",
            medic_classify.classify("Red-main Repair (reusable)", stalled_log()),
        )

    def test_it_is_not_a_credential_failure(self):
        # `system/init` succeeded. DRE-3428's table must stay silent, or the
        # card gets an evidence note sending an operator to `make cred-doctor`
        # for a token that is working.
        self.assertIsNone(reviewer_environment.detect(stalled_log()))

    def test_it_is_not_a_limit_death(self):
        # A limit death is a WAIT (DRE-3171) and is never retried. A stall is
        # retried once, so reading one as the other loses the retry — and the
        # marker would tell the sweep to re-enter a stage once a wall that was
        # never hit comes down.
        self.assertIsNone(dead_run.limit_kind(stalled_log()))

    def test_a_stalled_REVIEW_is_not_read_as_a_rate_limit(self):
        # The DRE-3428 ordering lesson, exactly: a crashed qa-review posts its
        # neutral marker into the SAME log, and on 2026-09-08 that made
        # `critic_infra_crash` win and the real cause was never named. A stall
        # must out-rank it for the same reason.
        log = stalled_log(
            extra="review\tPost verdict\t2026-09-15T05:05:00.0000000Z "
                  + medic_classify.CRITIC_NEUTRAL_MARKER + "\n"
        )
        self.assertEqual(
            "stalled_no_stream", medic_classify.classify("QA Review (reusable)", log)
        )

    def test_a_real_rate_limit_is_still_a_critic_infra_crash(self):
        # The anti-regression half: nothing about DRE-1921's class changes for
        # a log with no stall in it.
        self.assertEqual(
            "critic_infra_crash",
            medic_classify.classify(
                "QA Review (reusable)",
                "review\tx\t2026-09-15T05:05:00.0000000Z "
                "API rate limit exceeded for installation ID 1\n",
            ),
        )

    def test_the_cli_prints_the_class_and_the_stall_facts(self):
        # medic.yml appends this stdout straight to `$GITHUB_OUTPUT`, so every
        # value is one line and every key is one the workflow declares.
        path = self.write_log(stalled_log())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = medic_classify.main(["Red-main Repair (reusable)", path])
        out = buf.getvalue()
        self.assertEqual(0, rc)
        self.assertIn("class=stalled_no_stream", out)
        self.assertIn("infra_crash=false", out,
                      "a stall is retried once — it must not take DRE-1921's gate")
        self.assertIn("stall_step=Red-main Repair / repair", out)
        self.assertIn("stall_last_event=2026-09-15T05:04:12Z", out)
        self.assertIn("stall_silence=312", out)
        for line in out.splitlines():
            self.assertIn("=", line, f"every printed line is a key=value: {line!r}")

    def test_the_stall_keys_are_empty_for_every_other_class(self):
        # `$GITHUB_OUTPUT` is one key per line and the workflow declares all
        # of them: a key that only sometimes exists reads as the LAST run's
        # value in an expression, never as absent.
        buf = io.StringIO()
        path = self.write_log("nothing to see\n")
        with contextlib.redirect_stdout(buf):
            medic_classify.main(["Agent Task (reusable)", path])
        out = buf.getvalue()
        self.assertIn("class=normal", out)
        self.assertIn("stall_step=", out)
        self.assertIn("stall_silence=", out)
        self.assertNotIn("stall_step=Red", out)


class OneRetryThenOneNoticeTest(LogFileMixin, unittest.TestCase):
    """The card's rule, at the gate that actually issues the retry."""

    def test_a_first_stall_keeps_its_one_retry(self):
        decision = medic_retry.decide(repeat_stall="")
        self.assertTrue(decision.retry)

    def test_a_second_consecutive_stall_is_not_retried(self):
        decision = medic_retry.decide(
            repeat_stall="this run went quiet for 312s, and so did the last one"
        )
        self.assertEqual(medic_retry.DECLINE, decision.action)
        self.assertEqual(medic_retry.RULE_STALLED_REPEAT, decision.rule)

    def test_the_notice_is_plain_english_and_names_the_stall(self):
        decision = medic_retry.decide(
            repeat_stall="this run went quiet for 312s, and so did the last one"
        )
        notice = medic_retry.declined_comment(decision, run_url="https://x/1")
        self.assertIn("quiet", notice)
        self.assertIn(medic_retry.RULE_STALLED_REPEAT, notice)
        self.assertIn("https://x/1", notice)

    def test_a_park_still_outranks_a_stall(self):
        # A card a human already owns is never re-dispatched, whatever the
        # failure was (DRE-2954).
        decision = medic_retry.decide(
            parked_because="the 'needs-human' label is on it",
            repeat_stall="this run went quiet",
        )
        self.assertEqual(medic_retry.RULE_PARKED, decision.rule)

    def test_the_repeat_is_read_off_the_cards_own_records(self):
        earlier = stream_watchdog.stall_record(
            STALL, run_id="349", attempt="1", run_url="https://x/349")
        self.assertEqual(
            "",
            medic_retry.repeat_stall(STALL, [earlier], run_id="349", attempt="1"),
            "a run's own record must not decline that run's retry",
        )
        detail = medic_retry.repeat_stall(
            STALL, [earlier], run_id="349", attempt="2")
        self.assertTrue(detail, "attempt 2 after a stall on attempt 1 is the repeat")
        self.assertIn("312", detail)
        self.assertEqual(
            "", medic_retry.repeat_stall(None, [earlier], run_id="349", attempt="2"),
            "a run that did not stall is not a repeat stall",
        )

    def test_the_cli_declines_a_repeat_and_names_the_rule(self):
        path = self.write_log(stalled_log())
        facts = {
            "state": "In Progress",
            "labels": [],
            "comments": [{
                "body": stream_watchdog.stall_record(
                    STALL, run_id="349", attempt="1", run_url="https://x/349"),
                "created_at": "2026-09-15T05:10:00.000Z",
            }],
        }
        buf = io.StringIO()
        with mock.patch.object(medic_retry, "card_facts", return_value=facts):
            with contextlib.redirect_stdout(buf):
                rc = medic_retry.main([
                    "decide", "--branch", "repair/DRE-3991-main",
                    "--log", path, "--run-id", "349", "--run-attempt", "2",
                ])
        out = buf.getvalue()
        self.assertEqual(0, rc)
        self.assertIn("retry=false", out)
        self.assertIn(f"rule={medic_retry.RULE_STALLED_REPEAT}", out)
        self.assertEqual(4, len([ln for ln in out.splitlines() if ln.strip()]),
                         "decide prints four keys and no more")

    def test_the_cli_retries_the_first_stall(self):
        path = self.write_log(stalled_log())
        facts = {"state": "In Progress", "labels": [], "comments": []}
        buf = io.StringIO()
        with mock.patch.object(medic_retry, "card_facts", return_value=facts):
            with contextlib.redirect_stdout(buf):
                medic_retry.main([
                    "decide", "--branch", "repair/DRE-3991-main",
                    "--log", path, "--run-id", "349", "--run-attempt", "1",
                ])
        self.assertIn("retry=true", buf.getvalue())


class TheMedicIsWiredToItTest(unittest.TestCase):
    """The YAML half — a rule nothing runs is a rule nobody has."""

    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(MEDIC.read_text())
        cls.jobs = cls.doc["jobs"]
        cls.text = MEDIC.read_text()

    def test_classify_publishes_the_stall_facts(self):
        outputs = self.jobs["classify"]["outputs"]
        for key in ("stall_step", "stall_last_event", "stall_silence"):
            self.assertIn(key, outputs,
                          f"the stall's {key} never leaves the classify job")

    def test_the_decide_step_is_told_which_run_attempt_this_is(self):
        # Without it the gate cannot tell its own record from the last one's,
        # and the first stall declines its own retry.
        self.assertIn("--run-id", self.text)
        self.assertIn("--run-attempt", self.text)

    def test_a_stall_gets_a_record_on_the_card(self):
        job = self.jobs["stall_record"]
        condition = job["if"]
        self.assertIn("stalled_no_stream", condition)
        self.assertIn("card", condition)

    def test_the_record_is_written_once_per_run_attempt(self):
        # The classify job runs on EVERY failed attempt, so an unguarded post
        # writes a second record for the same stall and the card's count
        # becomes the medic's own echo.
        job = yaml.safe_dump(self.jobs["stall_record"])
        self.assertIn("dump-comments", job,
                      "the step must read the card before it writes to it")

    def test_the_second_stall_still_gets_its_notice_on_attempt_two(self):
        # The medic retries with `gh run rerun --failed`, so the SECOND stall
        # is attempt 2 of the same run — and `retry_declined` was gated on
        # attempt 1 only. Without this the repeat is silent, which is the
        # complaint the card opens with.
        condition = self.jobs["retry_declined"]["if"]
        self.assertIn(medic_retry.RULE_STALLED_REPEAT, condition)

    def test_one_voice_per_stall(self):
        # The record and the notice are alternatives, never both: a stall that
        # posted two comments is noise on the card the CEO reads.
        self.assertIn(medic_retry.RULE_STALLED_REPEAT,
                      self.jobs["stall_record"]["if"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
