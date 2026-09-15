"""A Claude run that goes silent is STOPPED and RECORDED (DRE-3991).

On 2026-09-15 the red-main repair agent failed twice in a row without ever
reading the build it was sent to fix. Run 34931268093, both attempts:
`claude-code-action` emitted a successful `system/init` event and then zero
further stream events, and something outside the run killed the process with
SIGKILL at 8m27s and 11m39s. Nothing was diagnosed, nothing was written, and
nothing said what had happened — the card got a dead run and no cause.

The remedy is a watchdog in the one shared place the fleet runs Claude from:
`.github/actions/install-claude-code` publishes a launcher that relays the
model's stream and stops the run when the stream goes QUIET.

**The cut-off is silence, not total run time**, and that distinction is the
whole card. A repair, fix or build run that is working emits stream events
continuously while it reads logs and edits files, so a long busy run is never
cut off however long it takes; a run that has said nothing for five minutes
after `system/init` is not working, and every further minute it holds the
runner is a minute nobody gets a report for.

Two halves, and both are executed rather than described:

  * **the rule**, driven against a scripted stream and a fake clock, so "five
    minutes of silence" means the pump really waited that long and "a busy
    run is never stopped" means the stream really ran for thirty minutes;
  * **the launcher**, run as a real subprocess against a stub `claude`, so
    "it stops a silent child" means a process really died and "it relays a
    busy one" means the vendor action really got its stream back.
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import stream_watchdog  # noqa: E402

ACTION = ROOT / ".github" / "actions" / "install-claude-code" / "action.yml"

# The vendor's own first stream event, as claude emits it under
# `--output-format stream-json`. The watchdog arms on this and on nothing
# else: everything before it is install and start-up, which is not the run
# being quiet.
INIT = json.dumps({"type": "system", "subtype": "init", "session_id": "s1"})
TOOL = json.dumps({"type": "assistant", "message": {"content": "reading"}})

BASE = _dt.datetime(2026, 9, 15, 5, 4, 0, tzinfo=_dt.timezone.utc)


class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def wall(self) -> _dt.datetime:
        return BASE + _dt.timedelta(seconds=self.t)


class Stream:
    """A scripted stream: `(gap_seconds, line)` pairs, then silence forever.

    It is the pump's `next_event` — handed a timeout, it either delivers the
    next line (advancing the clock to it) or reports that the wait expired
    (advancing the clock by the whole timeout). After the last scripted event
    it goes QUIET, which is the DRE-3991 shape; `then_eof` instead closes the
    stream, which is an ordinary finished run.

    A wait with no timeout and nothing left to deliver is a watchdog that
    would hang forever, so it fails the test loudly rather than deadlocking it.
    """

    def __init__(self, events, clock: FakeClock, *, then_eof: bool = False):
        self.pending = list(events)
        self.clock = clock
        self.then_eof = then_eof
        self.due = clock.t + (self.pending[0][0] if self.pending else 0.0)

    def __call__(self, timeout):
        if not self.pending:
            if self.then_eof:
                return stream_watchdog.EOF
            assert timeout is not None, (
                "the watchdog waited with no deadline on a stream that had "
                "gone quiet — that is the hang this card exists to end"
            )
            self.clock.advance(timeout)
            return None
        wait = self.due - self.clock.t
        if timeout is not None and timeout < wait:
            self.clock.advance(timeout)
            return None
        self.clock.advance(max(wait, 0.0))
        _, line = self.pending.pop(0)
        if self.pending:
            self.due = self.clock.t + self.pending[0][0]
        return line


def drive(events, *, then_eof=False, silence=stream_watchdog.SILENCE_SECONDS,
          step="Agent Task / build"):
    """Run the real pump over a scripted stream. Returns (stall, relayed, clock)."""
    clock = FakeClock()
    relayed: list[str] = []
    stall = stream_watchdog.pump(
        Stream(events, clock, then_eof=then_eof),
        step=step,
        silence_seconds=silence,
        clock=clock,
        wall=clock.wall,
        relay=relayed.append,
    )
    return stall, relayed, clock


class TheSilenceRuleTest(unittest.TestCase):
    """The two cases the card names, and they are opposites."""

    def test_a_stream_that_says_nothing_after_init_is_stopped_at_five_minutes(self):
        # THE RED CASE. `system/init` arrives and then nothing ever does —
        # run 34931268093, exactly. Today nothing stops it; the runner holds
        # the job until something outside kills it with nothing recorded.
        stall, _, clock = drive([(0.0, INIT)])
        self.assertIsNotNone(
            stall, "a stream silent since system/init was never stopped"
        )
        self.assertEqual(stall.silence_seconds, 300)
        self.assertAlmostEqual(clock.t, 300.0, places=3,
                               msg="the run was stopped at the wrong moment")
        self.assertEqual(stall.last_event, "2026-09-15T05:04:00Z",
                         "the record must name WHEN the stream last spoke")
        self.assertEqual(stall.step, "Agent Task / build")

    def test_a_busy_thirty_minute_run_is_never_stopped(self):
        # The cut-off is SILENCE, not total run time. Thirty minutes of steady
        # work — a repair agent reading logs and editing files — is a healthy
        # run, and a watchdog that killed it would be worse than none.
        events = [(0.0, INIT)] + [(60.0, TOOL)] * 30
        stall, relayed, clock = drive(events, then_eof=True)
        self.assertIsNone(
            stall,
            "a run emitting an event every 60 seconds for 30 minutes was "
            "stopped — the cut-off is silence, not elapsed time",
        )
        self.assertAlmostEqual(clock.t, 1800.0, places=3,
                               msg="the scripted run did not actually last 30 minutes")
        self.assertEqual(len(relayed), 31)

    def test_a_gap_just_under_the_limit_is_not_a_stall(self):
        # The boundary, from the safe side: 299 seconds of quiet followed by a
        # tool call is a slow turn, not a stall, and the clock restarts on it.
        stall, _, _ = drive([(0.0, INIT), (299.0, TOOL)], then_eof=True)
        self.assertIsNone(stall, "a 299-second gap was read as five minutes")

    def test_a_slow_start_before_init_is_not_a_stall(self):
        # Before `system/init` the run is installing, checking out and
        # starting up. It has not gone quiet — it has not spoken yet — and a
        # watchdog armed there would cut off every cold start.
        stall, _, clock = drive([(600.0, INIT)], then_eof=True)
        self.assertIsNone(stall, "ten minutes of start-up was read as a stall")
        self.assertAlmostEqual(clock.t, 600.0, places=3)

    def test_every_event_is_relayed_in_order(self):
        # The wrapper sits in the vendor action's stdout. A watchdog that
        # swallowed or reordered a line would break the thing it is guarding.
        lines = [INIT, TOOL, json.dumps({"type": "result", "is_error": False})]
        _, relayed, _ = drive(
            [(0.0, lines[0]), (1.0, lines[1]), (2.0, lines[2])], then_eof=True
        )
        self.assertEqual(relayed, lines)

    def test_the_silence_is_measured_from_the_last_event_not_from_init(self):
        # Four minutes of quiet, one tool call, then silence: the stall is
        # timed from the tool call, so the run gets its full five minutes
        # again and the recorded last event is the tool call's.
        stall, _, clock = drive([(0.0, INIT), (240.0, TOOL)])
        self.assertIsNotNone(stall)
        self.assertEqual(stall.silence_seconds, 300)
        self.assertEqual(stall.last_event, "2026-09-15T05:08:00Z")
        self.assertAlmostEqual(clock.t, 540.0, places=3)


class TheRecordTest(unittest.TestCase):
    """What a stopped run leaves behind, and what reads it back."""

    def setUp(self):
        self.stall = stream_watchdog.Stall(
            step="Agent Task / build",
            last_event="2026-09-15T05:04:00Z",
            silence_seconds=300,
        )

    def test_the_run_log_line_carries_the_three_facts(self):
        line = stream_watchdog.stall_line(self.stall)
        self.assertIn(stream_watchdog.STALL_SLUG, line)
        self.assertIn('step="Agent Task / build"', line)
        self.assertIn("last-event=2026-09-15T05:04:00Z", line)
        self.assertIn("silence=300s", line)
        self.assertEqual(len(line.splitlines()), 1,
                         "the marker is one line: the medic reads it per line")

    def test_the_line_says_what_this_is_NOT(self):
        # The whole cost of the 2026-09-15 incident was a dead run whose cause
        # nobody could name, and three wrong causes were available. The line
        # rules them out in the log itself, where the first operator looks.
        line = stream_watchdog.stall_line(self.stall).lower()
        for wrong in ("code", "credential", "limit"):
            self.assertIn(wrong, line,
                          f"the marker must say it is not a {wrong} failure")

    def test_the_medic_reads_the_facts_back_out_of_an_actions_log(self):
        # `gh run view --log-failed` prefixes every line with
        # `job\tstep\t<ISO timestamp> `. The reader must see through it.
        log = (
            "build\tRun claude\t2026-09-15T05:03:59.1234567Z starting\n"
            "build\tRun claude\t2026-09-15T05:09:00.1234567Z "
            + stream_watchdog.stall_line(self.stall) + "\n"
        )
        found = stream_watchdog.stall_from_log(log)
        self.assertEqual(found, self.stall)

    def test_a_log_with_no_stall_reads_as_no_stall(self):
        self.assertIsNone(stream_watchdog.stall_from_log("all fine\n"))
        self.assertIsNone(stream_watchdog.stall_from_log(""))
        self.assertIsNone(stream_watchdog.stall_from_log(None))

    def test_a_quoted_mention_of_the_slug_is_not_a_stall(self):
        # DRE-2488/2923's discipline, and here the adversary is THIS CARD: its
        # own body writes `stalled-no-stream` in backticks, and an agent log
        # quoting a card body must never classify as one.
        for quoted in (
            "the medic classifies `stalled-no-stream` as its own kind",
            '+ a run is recorded as stalled-no-stream step="x" '
            "last-event=2026-09-15T05:04:00Z silence=300s",
            "> stalled-no-stream — the diagnosis quoted back",
        ):
            self.assertIsNone(
                stream_watchdog.stall_from_log(quoted),
                f"quoted prose classified as a stall: {quoted!r}",
            )


class ThePriorStallsOnTheCardTest(unittest.TestCase):
    """The medic's "is this the second one" read, off the card's own records."""

    def body(self, run="123", attempt="1"):
        return stream_watchdog.stall_record(
            stream_watchdog.Stall("Agent Task / build",
                                  "2026-09-15T05:04:00Z", 300),
            run_id=run, attempt=attempt, run_url="https://x/runs/" + run,
        )

    def test_the_record_names_the_run_attempt_it_belongs_to(self):
        text = self.body(run="99", attempt="2")
        self.assertIn(stream_watchdog.RECORD_MARKER, text)
        self.assertIn("run=99", text)
        self.assertIn("attempt=2", text)
        self.assertIn("300", text, "the record must carry the silence length")
        self.assertIn("2026-09-15T05:04:00Z", text,
                      "the record must carry the time of the last event")
        self.assertIn("Agent Task / build", text,
                      "the record must carry the step that went quiet")

    def test_this_runs_own_record_is_not_a_prior_stall(self):
        # Otherwise the FIRST stall refuses its own retry the moment it is
        # written, and the card's one retry never happens.
        self.assertEqual(
            stream_watchdog.prior_stalls([self.body(run="99", attempt="1")],
                                         run_id="99", attempt="1"),
            [],
        )

    def test_an_earlier_attempt_of_the_same_run_is_a_prior_stall(self):
        # The medic retries with `gh run rerun --failed`, so the second stall
        # is attempt 2 of the SAME run id. Keying on the run alone would miss
        # every repeat the medic itself caused.
        found = stream_watchdog.prior_stalls(
            [self.body(run="99", attempt="1")], run_id="99", attempt="2"
        )
        self.assertEqual(len(found), 1)

    def test_an_earlier_run_of_the_same_card_is_a_prior_stall(self):
        found = stream_watchdog.prior_stalls(
            ["unrelated", self.body(run="98", attempt="1")],
            run_id="99", attempt="1",
        )
        self.assertEqual(len(found), 1)

    def test_no_records_means_no_prior_stall(self):
        self.assertEqual(
            stream_watchdog.prior_stalls(["hello", ""], run_id="1", attempt="1"),
            [],
        )
        self.assertEqual(stream_watchdog.prior_stalls(None, run_id="1",
                                                      attempt="1"), [])


# --------------------------------------------------------------------------- #
# the launcher, as a real process                                              #
# --------------------------------------------------------------------------- #


WATCHDOG = ROOT / "scripts" / "stream_watchdog.py"


def _stub_claude(path: Path, script: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(script))
    path.chmod(0o755)


def _run_watchdog(stub_body: str, *, silence="1", args=("-p",)):
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        stub = td / "claude"
        _stub_claude(stub, stub_body)
        return subprocess.run(
            [sys.executable, str(WATCHDOG), "--executable", str(stub),
             "--silence-seconds", silence, "--step", "Agent Task / build",
             "--"] + list(args),
            capture_output=True, text=True, timeout=60,
        )


class TheLauncherTest(unittest.TestCase):
    """The wrapper the vendor action actually runs. Real processes, real pipes."""

    def test_a_child_that_goes_silent_after_init_is_killed_and_reported(self):
        proc = _run_watchdog(
            f"""
            import sys, time
            print({INIT!r}, flush=True)
            time.sleep(120)
            """
        )
        self.assertNotEqual(proc.returncode, 0,
                            "a stalled run must fail its step, not pass it")
        self.assertIn(stream_watchdog.STALL_SLUG, proc.stderr)
        found = stream_watchdog.stall_from_log(proc.stderr)
        self.assertIsNotNone(found, f"no readable stall record:\n{proc.stderr}")
        self.assertGreaterEqual(found.silence_seconds, 1)
        self.assertEqual(found.step, "Agent Task / build")
        self.assertIn(INIT, proc.stdout,
                      "the events that DID arrive are still the vendor's")

    def test_a_busy_child_keeps_its_stream_and_its_exit_code(self):
        proc = _run_watchdog(
            f"""
            import sys, time
            print({INIT!r}, flush=True)
            for _ in range(4):
                time.sleep(0.25)
                print({TOOL!r}, flush=True)
            sys.exit(7)
            """,
            silence="1",
        )
        self.assertEqual(proc.returncode, 7,
                         f"the child's exit code was not relayed:\n{proc.stderr}")
        self.assertEqual(proc.stdout.count(TOOL), 4)
        self.assertIsNone(stream_watchdog.stall_from_log(proc.stderr),
                          "a busy child was reported as a stall")

    def test_silence_before_init_does_not_kill_the_child(self):
        # A cold start that takes longer than the silence budget must survive:
        # the watchdog arms on `system/init`, not on launch.
        proc = _run_watchdog(
            f"""
            import time
            time.sleep(2)
            print({INIT!r}, flush=True)
            """,
            silence="1",
        )
        self.assertEqual(proc.returncode, 0,
                         f"a slow start was killed as a stall:\n{proc.stderr}")

    def test_silence_seconds_zero_turns_the_watchdog_off(self):
        # The escape hatch. A fleet-wide safety net needs one switch that
        # takes it out without a release, and it must be provably inert.
        proc = _run_watchdog(
            f"""
            import time
            print({INIT!r}, flush=True)
            time.sleep(2)
            """,
            silence="0",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(stream_watchdog.stall_from_log(proc.stderr))


# --------------------------------------------------------------------------- #
# the shared place it is wired into                                            #
# --------------------------------------------------------------------------- #


def _action() -> dict:
    return yaml.safe_load(ACTION.read_text())


def _install_script() -> str:
    steps = _action()["runs"]["steps"]
    runs = [s["run"] for s in steps if "run" in s]
    assert len(runs) == 1
    return runs[0]


class TheSharedRunStepTest(unittest.TestCase):
    """Every repo gets this through `stable`, or it protects nobody."""

    def test_the_watchdog_sits_next_to_the_action_that_publishes_it(self):
        # The action resolves the watchdog off its OWN directory
        # (`$GITHUB_ACTION_PATH/../../../scripts`), because it may not check
        # bureau-pipeline out — an action cannot receive `pipeline_ref`, so a
        # checkout in there could never be pinned. That path only resolves if
        # the script really is three levels up from the action.
        self.assertTrue(WATCHDOG.exists(), f"{WATCHDOG} is missing")
        self.assertEqual(
            (ACTION.parent / ".." / ".." / ".." / "scripts"
             / "stream_watchdog.py").resolve(),
            WATCHDOG.resolve(),
        )

    def test_the_action_resolves_the_watchdog_off_its_own_path(self):
        script = _install_script()
        self.assertIn("GITHUB_ACTION_PATH", script,
                      "the action must find the watchdog beside itself")
        self.assertIn("stream_watchdog.py", script)

    def test_the_published_executable_is_the_watched_one(self):
        # `executable` is what every caller hands the vendor action as
        # `path_to_claude_code_executable`. Publishing the bare binary there
        # would leave the watchdog wired to nothing.
        outputs = _action().get("outputs") or {}
        self.assertIn("executable", outputs)
        self.assertIn("proved-executable", outputs,
                      "the binary the install step PROVED still needs a name")

    def test_it_takes_a_silence_budget_a_caller_can_set(self):
        inputs = _action().get("inputs") or {}
        self.assertIn("silence-seconds", inputs)
        self.assertEqual(
            str((inputs["silence-seconds"] or {}).get("default", "")),
            str(stream_watchdog.SILENCE_SECONDS),
            "the action's default and the watchdog's must be one number",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
