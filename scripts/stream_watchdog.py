#!/usr/bin/env python3
"""A Claude run that goes SILENT is stopped and recorded (DRE-3991). Stdlib only.

On 2026-09-15 the red-main repair agent failed twice in a row without ever
reading the build it was sent to fix. Run 34931268093, both attempts:
`claude-code-action` emitted a successful `system/init` and then zero further
stream events, and something outside the run killed the process with SIGKILL
at 8m27s and 11m39s. Nothing was diagnosed, nothing was written, and the card
got a dead run whose cause nobody could name — while three wrong causes were
all available, because a one-turn $0 death is the fingerprint of a stale
credential (DRE-3416) and a death with no result record reads as a flake.

This module is the safety timer that incident asked for, and one sentence
governs it:

    THE CUT-OFF IS SILENCE, NOT TOTAL RUN TIME.

A repair, fix or build run that is working emits stream events continuously
while it reads logs and edits files. A run that has emitted nothing for five
minutes AFTER `system/init` is not working — every further minute it holds
the runner is a minute nobody gets a report for. A long busy run is never cut
off, however long it takes, and a slow COLD START is never cut off either:
the watchdog arms on `system/init` and on nothing else, because before that
event the run has not gone quiet, it has not spoken yet.

## Where it sits

`.github/actions/install-claude-code` — the one shared place the fleet runs
Claude from (DRE-3414) — writes a launcher that runs this module around the
Claude binary it just PROVED, and publishes that launcher as `executable`.
Every caller already hands that output to `claude-code-action` as
`path_to_claude_code_executable`, so every workflow in this repo, and every
product repo riding the channel, gets the watchdog with no stub change at all.

The wrapper is transparent: every stream line is relayed to stdout byte for
byte in the order it arrived, stdin and stderr are the child's own, and the
child's exit code is the wrapper's. The vendor action cannot tell it is there
until a run goes quiet.

## What it leaves behind

ONE line in the run log, on stderr — never stdout, which is the JSON stream
the vendor parses — naming the three facts the next reader needs: the step
that went quiet, the time of the last event, and how long the silence ran. It
also says what this is NOT, in the log itself, because the whole cost of the
2026-09-15 incident was an operator sent to a credential that was working.

`stall_from_log` reads that line back for the medic, line-anchored in the
DRE-2488/2923 discipline — and here the adversary is DRE-3991's own card
body, which writes `stalled-no-stream` in backticks. A quoted mention is a
quotation, never a report.

## What the medic does with it

`medic_classify` gives the class its own name (`stalled_no_stream`), ahead of
DRE-1921's critic infra-crash for the reason DRE-3428 learned the hard way: a
crashed review posts its neutral marker into the same log and would otherwise
win. `medic_retry` gives it the shape DRE-3428 already uses next door — the
FIRST stall keeps the medic's one automatic retry and gets `stall_record` on
the card; a SECOND consecutive stall on the same card gets one plain-English
notice and no third build. A stall retried forever is the DRE-1921 loop with
a new name.

CLI (the launcher):

    stream_watchdog.py --executable <claude> [--silence-seconds 300] \\
        [--step "<workflow> / <job>"] -- <args passed to claude>

CLI (the medic's card record):

    stream_watchdog.py record --card DRE-N --log <file> --run-id N \\
        --run-attempt N [--run-url URL]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import queue
import re
import signal
import subprocess  # nosec B404 — fixed-arg exec of a caller-supplied binary
import sys
import threading
import time
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

#: The silence budget, in seconds. Five minutes, and the number is the card's:
#: the two 2026-09-15 stalls ran 8m27s and 11m39s before something outside
#: killed them, so five minutes stops a stall well inside the window that was
#: being burned while leaving a slow turn — a long file read, a big diff — all
#: the room it has ever needed. `.github/actions/install-claude-code` declares
#: the same number as its `silence-seconds` default and a test holds the two
#: equal, so there is one budget and not two.
SILENCE_SECONDS = 300

#: What a stopped run is called, everywhere: the run log, the medic's class
#: (`stalled_no_stream`, the same words as a workflow output key), and the
#: card. One name, so the log a person greps and the class the medic prints
#: are the same fact.
STALL_SLUG = "stalled-no-stream"

#: The exit code the launcher gives a stopped run. 124 is the conventional
#: "the timer, not the program" code (`timeout(1)`'s), and it is deliberately
#: not 137: SIGKILL-from-outside is exactly the fingerprint the 2026-09-15
#: runs already had, and the point of this module is that the two are told
#: apart at a glance.
STALL_EXIT = 124

#: Returned by a `next_event` source when the stream has CLOSED — an ordinary
#: finished run. Distinct from `None`, which means the wait expired and the
#: stream is still open, which is the stall.
EOF = object()


class Stall(NamedTuple):
    """A stopped run's three facts. `silence_seconds` is whole seconds: it is
    read back off a log line and compared, and a float would not survive the
    round trip."""

    step: str
    last_event: str
    silence_seconds: int


# --------------------------------------------------------------------------- #
# the rule                                                                     #
# --------------------------------------------------------------------------- #


def is_init(line: str) -> bool:
    """Is this the vendor's `system/init` event — the moment the run starts
    being able to go quiet?

    Parsed as JSON rather than matched as a substring: under
    `--output-format stream-json` every event IS a JSON object, and an agent
    that prints the words "system init" in a tool result must not arm a timer.
    Anything that does not parse is still an event (the run said something),
    it is just not the one that arms the watchdog.
    """
    try:
        event = json.loads(line)
    except (TypeError, ValueError):
        return False
    if not isinstance(event, dict):
        return False
    return event.get("type") == "system" and event.get("subtype") == "init"


def _iso(when) -> str:
    """A UTC instant as the log and the card both write it, to the second."""
    if when is None:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pump(
    next_event,
    *,
    step: str = "",
    silence_seconds: int = SILENCE_SECONDS,
    clock=time.monotonic,
    wall=None,
    relay=None,
):
    """Relay a stream until it ends, or until it goes quiet for too long.

    `next_event(timeout)` is the source: a line, `None` when the wait expired
    with the stream still open, or `EOF` when the stream closed. `timeout` is
    `None` before `system/init` — nothing is being timed yet, so the source is
    asked to wait as long as it takes.

    Returns the `Stall` that stopped the run, or None when the stream ended on
    its own. Two clocks on purpose: `clock` is monotonic and measures the
    silence (a wall clock that steps backwards would invent a stall), `wall`
    timestamps the last event for the person reading the record.

    `silence_seconds` of 0 or less disarms the watchdog entirely — the escape
    hatch a fleet-wide safety net needs, and it is provably inert rather than
    merely long.
    """
    wall = wall or (lambda: _dt.datetime.now(_dt.timezone.utc))
    armed = False
    last = clock()
    last_wall = wall()
    while True:
        if armed:
            waited = clock() - last
            if waited >= silence_seconds:
                return Stall(step, _iso(last_wall), int(round(waited)))
            timeout = silence_seconds - waited
        else:
            timeout = None
        event = next_event(timeout)
        if event is EOF:
            return None
        if event is None:
            continue  # the wait expired; the loop above decides what that means
        if relay is not None:
            relay(event)
        last = clock()
        last_wall = wall()
        if not armed and silence_seconds > 0 and is_init(event):
            armed = True


# --------------------------------------------------------------------------- #
# the record, and reading it back                                              #
# --------------------------------------------------------------------------- #


def stall_line(stall: Stall) -> str:
    """The ONE line a stopped run writes into its own log.

    One line because the medic reads its log line by line, and an Actions
    annotation cannot span lines anyway. It carries the three facts as
    `key=value` so the reader below is an anchor rather than a guess, and it
    rules out the three wrong causes in the log itself — that sentence is what
    the 2026-09-15 operator did not have.
    """
    return (
        f"::error title=Claude run stalled::{STALL_SLUG} "
        f'step="{stall.step}" last-event={stall.last_event} '
        f"silence={stall.silence_seconds}s — no stream event arrived after "
        f"system/init, so the run was stopped. This is a stall: not a code "
        f"failure (nothing was reviewed or rejected), not a credential "
        f"failure (the run authenticated and started), and not a usage "
        f"limit (no wall was hit)."
    )


# A GitHub Actions log line is `job\tstep\t<ISO timestamp> <content>`, and the
# prefix is stripped so a quoted diff line inside one is still recognisable as
# a quotation. Anchored to that SHAPE — at most two tab-separated fields, then
# the timestamp — rather than to `.*?` before the first timestamp the line
# happens to contain: the marker below carries an ISO timestamp of its own in
# `last-event=`, so a looser prefix eats the record it is trying to read.
_LOG_PREFIX = re.compile(r"^(?:[^\t]*\t){0,2}\d{4}-\d{2}-\d{2}T[\d:.]+Z\s")

# A line that OPENS with one of these is a quotation of something, not a report
# of it: a diff hunk in an agent log, a quoted comment, a shell trace.
_QUOTATION_LINE = re.compile(r"^\s*[+\->|]")

# The marker, with all three fields required on ONE line. DRE-3991's own card
# body writes the slug in backticks and quotes the record's wording, so the
# slug alone could never be the anchor — `(?<!\`)` refuses the backticked
# mention, and requiring the whole shape refuses the prose.
_STALL = re.compile(
    r"(?<!`)" + re.escape(STALL_SLUG)
    + r'\s+step="(?P<step>[^"]*)"\s+last-event=(?P<last>\S+)\s+'
    r"silence=(?P<silence>\d+)s"
)


def stall_from_log(log_text: str | None) -> Stall | None:
    """The stall a failed run's log reports, or None.

    Line-anchored, prefix-stripped, quotation-skipping — the discipline
    DRE-2488 and DRE-2923 both arrived at, because a log that merely QUOTES a
    marker must never classify as one. The LAST match wins: a re-run's log can
    carry two, and the stall in hand is the one that just happened.
    """
    found = None
    for raw in (log_text or "").splitlines():
        line = _LOG_PREFIX.sub("", raw, count=1)
        if _QUOTATION_LINE.match(line):
            continue
        match = _STALL.search(line)
        if match:
            found = Stall(
                match.group("step"),
                match.group("last"),
                int(match.group("silence")),
            )
    return found


# --------------------------------------------------------------------------- #
# the card's side                                                              #
# --------------------------------------------------------------------------- #

#: What a READER of the card looks for. Deliberately not a `*_TAG` constant:
#: the record is a REPORT about a run, the registry declares no row for it,
#: and `pipeline_act.problems()` reads every `*_TAG = "…"` in an emitting file
#: as a tag that must have one (the `reviewer_environment.EVIDENCE_MARKER`
#: shape, and for the same reason).
RECORD_MARKER = "stalled-no-stream-record"


def report_lines(stall: Stall | None) -> list:
    """The three `key=value` lines a caller reads off a classification.

    One line per key and never a newline inside a value: `medic.yml` appends
    the classifier's stdout straight to `$GITHUB_OUTPUT`, which is one key per
    line, and a value carrying a newline writes a second key nobody declared.
    Every key is printed on every class — an output that sometimes does not
    exist reads in an expression as the empty string, which is right, but only
    by accident. (`reviewer_environment.report_lines`'s shape, deliberately.)
    """
    return [
        "stall_step=" + " ".join((stall.step if stall else "").split()),
        f"stall_last_event={stall.last_event if stall else ''}",
        f"stall_silence={stall.silence_seconds if stall else ''}",
    ]


def stall_record(stall: Stall, *, run_id: str, attempt: str,
                 run_url: str = "") -> str:
    """What the medic leaves on the card after a run is stopped.

    `run=<id> attempt=<n>` is load-bearing twice over: the medic's own step
    greps it so a re-classified attempt never writes a second record for one
    stall, and `prior_stalls` below reads it to tell THIS run's record from
    the last one's. Keying on the run alone would miss every repeat the medic
    itself caused, because `gh run rerun --failed` reuses the run id.
    """
    minutes = stall.silence_seconds / 60.0
    run = f" Run: {run_url}" if (run_url or "").strip() else ""
    return (
        f"\U0001f573 {RECORD_MARKER} run={run_id} attempt={attempt}: the agent "
        f"step “{stall.step}” went silent — no activity at all for "
        f"{stall.silence_seconds} seconds ({minutes:.1f} minutes) after it "
        f"started up, the last sign of life being {stall.last_event}. The "
        f"pipeline stopped it rather than let it hold the machine, and is "
        f"trying once more."
        "\n\n"
        "Nothing is wrong with the work: the agent never got as far as "
        "reading the code, so nothing was reviewed and nothing was rejected. "
        "This is not a password problem — it started up fine — and it is not "
        f"a usage limit.{run}"
    )


def prior_stalls(bodies, *, run_id: str, attempt: str) -> list:
    """This card's stall records from any run-attempt OTHER than this one.

    The exclusion is the whole point: a card carries its own record within
    seconds of the stall being recorded, so counting it would make the FIRST
    stall refuse its own retry and the one retry the card promises would never
    happen.
    """
    mine = f"run={run_id} attempt={attempt}"
    out = []
    for body in bodies or ():
        text = body or ""
        if RECORD_MARKER not in text or mine in text:
            continue
        out.append(text)
    return out


# --------------------------------------------------------------------------- #
# the launcher                                                                 #
# --------------------------------------------------------------------------- #


def _reader(stream, sink: "queue.Queue") -> None:
    """Every line the child writes, onto the queue, then the EOF sentinel."""
    try:
        for line in stream:
            sink.put(line)
    finally:
        sink.put(EOF)


def _stop(child: subprocess.Popen) -> None:
    """End a stalled child, and mean it.

    SIGTERM to the whole process group first — Claude Code spawns helpers, and
    signalling only the launcher leaves them holding the runner — then SIGKILL
    if it is still there. The wait is short because the process has already
    done nothing for five minutes.
    """
    for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(child.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                child.send_signal(sig)
            except (ProcessLookupError, OSError):
                return
        try:
            child.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def launch(executable: str, args, *, step: str = "",
           silence_seconds: int = SILENCE_SECONDS, out=None, err=None) -> int:
    """Run Claude under the watchdog. Returns the exit code for the step.

    stdin and stderr are the child's own — the vendor action feeds the prompt
    in on stdin and reads the model's diagnostics on stderr, and a wrapper
    that intercepted either would change what it is guarding. Only stdout is
    piped, because stdout is the stream.
    """
    out = out or sys.stdout
    err = err or sys.stderr
    child = subprocess.Popen(  # nosec B603 — fixed-arg exec, shell=False
        [executable] + list(args),
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,  # its own process group, so _stop can end it all
    )
    events: "queue.Queue" = queue.Queue()
    pump_thread = threading.Thread(
        target=_reader, args=(child.stdout, events), daemon=True
    )
    pump_thread.start()

    def next_event(timeout):
        try:
            return events.get(timeout=timeout)
        except queue.Empty:
            return None

    def relay(line: str) -> None:
        out.write(line if line.endswith("\n") else line + "\n")
        out.flush()

    stall = pump(
        next_event,
        step=step,
        silence_seconds=silence_seconds,
        relay=relay,
    )
    if stall is None:
        return child.wait()
    print(stall_line(stall), file=err, flush=True)
    _stop(child)
    return STALL_EXIT


def _default_step() -> str:
    """The name of the place that went quiet.

    A composite action cannot see the caller's STEP name, so this is the
    workflow and the job — which is what a person needs to find the run
    anyway. `BUREAU_CLAUDE_STEP` overrides it for a caller that knows better.
    """
    named = (os.environ.get("BUREAU_CLAUDE_STEP") or "").strip()
    if named:
        return named
    workflow = (os.environ.get("GITHUB_WORKFLOW") or "").strip()
    job = (os.environ.get("GITHUB_JOB") or "").strip()
    return " / ".join(part for part in (workflow, job) if part) or "the Claude step"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _linear_ops():
    """The Linear client, imported at the moment of use.

    `medic_classify` imports this module on the medic's critical path, where
    there is no Linear credential and nothing to say to Linear — and so does
    the LAUNCHER, on a runner in the middle of a model run. Keeping the client
    out of that import graph is the same argument
    `reviewer_environment._linear_ops` makes.
    """
    import linear_ops  # noqa: PLC0415 — see the docstring

    return linear_ops


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _cmd_record(args) -> int:
    """Post the stall record on the card. Exit 3 when the log names no stall —
    the caller gated on the classifier's answer and the two disagreeing is
    worth a red step, not a silent comment about nothing."""
    stall = stall_from_log(_read(args.log))
    if stall is None:
        print("stream-watchdog: no stall in this log — nothing to record",
              file=sys.stderr)
        return 3
    body = stall_record(
        stall, run_id=args.run_id, attempt=args.run_attempt,
        run_url=args.run_url,
    )
    _linear_ops().cmd_comment(args.card, body)
    print(f"stream-watchdog: stall recorded on {args.card} "
          f"(run={args.run_id} attempt={args.run_attempt}, "
          f"{stall.silence_seconds}s of silence)")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "record":
        parser = argparse.ArgumentParser(prog="stream_watchdog.py record")
        parser.add_argument("--card", required=True)
        parser.add_argument("--log", required=True)
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--run-attempt", required=True)
        parser.add_argument("--run-url", default="")
        return _cmd_record(parser.parse_args(argv[1:]))

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--executable", required=True)
    parser.add_argument("--silence-seconds", default=str(SILENCE_SECONDS))
    parser.add_argument("--step", default="")
    parser.add_argument("passthrough", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        silence = int(float(args.silence_seconds))
    except (TypeError, ValueError):
        # An unreadable budget takes the DEFAULT, not "off": a typo in a
        # caller's input must not quietly remove the fleet's safety net.
        print(f"::warning::stream-watchdog: unreadable silence budget "
              f"{args.silence_seconds!r} — using {SILENCE_SECONDS}s",
              file=sys.stderr)
        silence = SILENCE_SECONDS
    passthrough = args.passthrough
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    return launch(
        args.executable, passthrough,
        step=args.step or _default_step(),
        silence_seconds=silence,
    )


if __name__ == "__main__":
    sys.exit(main())
