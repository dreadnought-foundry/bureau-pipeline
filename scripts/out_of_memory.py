#!/usr/bin/env python3
""""The machine ran out of memory" — the one reader (DRE-4847). Stdlib only.

On 2026-09-24 four build and fix runs were SIGKILLed on 2 GB light runners, and
every instrument the pipeline owns misread them:

  * the in-job death receipt said `died: false, cause: none` (portico run
    35950845844, attempt 2) — it runs as a step INSIDE the job, before
    push-rescue and everything after it, and it writes before the job has a
    final status;
  * the medic said "incomplete work … no action needed on infrastructure";
  * a pipeline-failure card (DRE-4748) blamed the stream watchdog "for
    silence", although the watchdog's threshold is 300s and the agent had
    posted progress 80s before the kill.

So a person diagnosed each one by hand, off the one thing that was in the log
all along: `##[error]Process completed with exit code 137.` — a container at
its memory limit, with the kernel's out-of-memory killer ending each new
process it starts.

This module is the reader that fact needed, the way `stream_watchdog
.stall_from_log` is the reader for a stall: ONE place that knows the shape, so
`medic_classify` (which names the class) and `medic_retry` (which decides
whether a rerun can help) cite one answer instead of inventing two.

## The rule is ONE kill line

Counting `##[error]Process completed with exit code 137.` in each run's
`--log-failed`: portico 35950845844 attempt 1 has **4**, attempt 2 has **1**,
fix run 35954619710 has **2**, and agent-bureau 36093192182 attempt 1 has
**1** — and in that last one push-rescue then delivered the branch and opened a
pull request. So "every later step died too" is NOT the signature: one line is
the rule, extra ones corroborate and are never required.

## What it is not

`stream_watchdog` stops a SILENT run with exit **124** (`STALL_EXIT`,
commented "deliberately not 137") and always writes its `stalled-no-stream
step=… silence=…s` line. It then SIGKILLs the process group it stopped, so a
137 can land in that same log — which is why the stall line WINS here: a log
carrying it is a stall, and this reader says nothing. A 137 with no stall line
is a kill from outside the program, and on a memory-capped runner that is the
out-of-memory killer.

## The discipline

DRE-2488 / DRE-2923, third time: prefix-stripped, quotation-skipping, and
anchored to the WHOLE error line. The adversary is this card's own body, which
quotes `##[error]Process completed with exit code 137` in prose — and its own
fixtures, which a diff hunk in an agent log would carry with a `+` in front.
A whole-line anchor refuses both: the quotation is never the line.

## The class of machine

Off the `Runner name:` line the log already carries in its set-up steps, which
was checked on all four runs. `bureau-mini-light-*` is `light` (the 2 GB pool),
any other `bureau-mini-*` is `heavy` (the class DRE-4846 routes build and fix
jobs to), `GitHub Actions *` is `hosted`, and anything else — including a log
with no runner-name line at all — is `unknown`. Never guessed: the class is what
decides whether the medic's one retry can help, and guessing `light` would
re-run a heavy job straight into the same wall.

With today's `gh` (2.100.0) `--log-failed` returns the whole job log, because
GitHub's log is no longer step-mapped (every line reads `UNKNOWN STEP`). That
is what puts the set-up lines and the kill line in one fetch. If `gh` ever
narrows it again the medic sees no 137 line and classifies `normal`, which is
today's behavior and never a false out-of-memory.

CLI:

    python3 out_of_memory.py <log-file>

prints the runner, its class and the kill count, or says there was no kill;
exit 0 either way (a classifier that fails its caller has turned one broken run
into two).
"""

from __future__ import annotations

import os
import re
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stream_watchdog  # noqa: E402

#: The exit code a process gets when something outside it sends SIGKILL —
#: 128 + 9. On a memory-capped runner that something is the kernel's
#: out-of-memory killer. Deliberately the number `stream_watchdog.STALL_EXIT`
#: is not: the watchdog exits 124 so the two are told apart at a glance.
KILL_EXIT = 137

#: The classes of machine, and the only one the medic's single retry can help.
#: A light runner's kill can be a one-off — a heavier turn, a bigger diff — so
#: it keeps the ordinary retry; every other class is a machine that is already
#: as big as it gets here, and a rerun walks into the same wall.
LIGHT = "light"
HEAVY = "heavy"
HOSTED = "hosted"
UNKNOWN = "unknown"

#: What each class means in plain words, for the sentence the medic writes on
#: the card. One phrasing, two readers (`medic_classify` and `medic_retry`).
_CLASS_WORDS = {
    LIGHT: "a light runner",
    HEAVY: "a heavy runner",
    HOSTED: "a GitHub-hosted runner",
    UNKNOWN: "a runner this pipeline does not recognize",
}


class Kill(NamedTuple):
    """A killed run's three facts: which machine, what size, and how many of
    its processes the kernel ended."""

    runner: str
    runner_class: str
    kills: int


# A GitHub Actions log line is `job\tstep\t<ISO timestamp> <content>`, and the
# prefix is stripped so a quoted diff line inside one is still recognisable as
# a quotation. The same anchored shape `stream_watchdog._LOG_PREFIX` reads —
# at most two tab-separated fields, then the timestamp — rather than `.*?`
# before the first timestamp on the line.
_LOG_PREFIX = re.compile(r"^(?:[^\t]*\t){0,2}\d{4}-\d{2}-\d{2}T[\d:.]+Z\s")

# A line that OPENS with one of these is a quotation of something, not a report
# of it: a diff hunk in an agent log, a quoted comment, a shell trace.
_QUOTATION_LINE = re.compile(r"^\s*[+\->|]")

# The kill line, anchored to the WHOLE line. This card's own body writes the
# line inside backticks in the middle of a sentence, so a substring search
# would classify an agent-task log that merely quoted the card — DRE-2923's
# lesson, and the reason the anchor is the whole line rather than the phrase.
_KILL = re.compile(
    r"^##\[error\]Process completed with exit code %d\.\s*$" % KILL_EXIT
)

# The runner's name, as the set-up lines of every job write it.
_RUNNER = re.compile(r"^Runner name:\s*'(?P<name>[^']*)'\s*$")


def runner_class(name: str | None) -> str:
    """Which class of machine that runner name is.

    Prefix-matched and case-insensitive: the pools are named
    `bureau-mini-light-N` and `bureau-mini-heavy-N`, and GitHub's own runners
    report `GitHub Actions N`. Anything else is `unknown`, which is a decision
    and not a gap — see the module docstring.
    """
    text = (name or "").strip().lower()
    if text.startswith("bureau-mini-light"):
        return LIGHT
    if text.startswith("bureau-mini"):
        return HEAVY
    if text.startswith("github actions"):
        return HOSTED
    return UNKNOWN


def from_log(log_text: str | None) -> Kill | None:
    """The out-of-memory kill a failed run's log reports, or None.

    None when the log carries the watchdog's stall line: that run was stopped
    from INSIDE the pipeline and says so, and the 137 its process group leaves
    behind is the wake of that decision rather than a kill from outside.

    The FIRST runner-name line wins. `--log-failed` opens with the failed job's
    set-up steps, so that line is the machine the kill happened on; a later one
    belongs to another job's log appended after it.
    """
    if stream_watchdog.stall_from_log(log_text) is not None:
        return None
    runner, kills = "", 0
    for raw in (log_text or "").splitlines():
        line = _LOG_PREFIX.sub("", raw, count=1)
        if _QUOTATION_LINE.match(line):
            continue
        if _KILL.match(line):
            kills += 1
            continue
        if not runner:
            found = _RUNNER.match(line)
            if found:
                runner = found.group("name").strip()
    if not kills:
        return None
    return Kill(runner, runner_class(runner), kills)


def describe(kill: Kill | None) -> str:
    """The kill in one plain-English clause, naming the machine and its size.

    Written once here because both readers need the same words: the classifier
    says it on the run, and the retry gate says it on the card.
    """
    if kill is None:
        return ""
    where = kill.runner or "an unnamed runner"
    times = "" if kill.kills == 1 else f", {kill.kills} times in this run"
    return (
        f"{where} ({_CLASS_WORDS[kill.runner_class]}) ran out of memory: the "
        f"kernel killed the job with exit {KILL_EXIT}{times}"
    )


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("usage: out_of_memory.py <log-file>", file=sys.stderr)
        return 2
    kill = from_log(_read(argv[0]))
    if kill is None:
        print("no out-of-memory kill in this log", file=sys.stderr)
        return 0
    print(f"runner={kill.runner}")
    print(f"runner_class={kill.runner_class}")
    print(f"kills={kill.kills}")
    print(describe(kill), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
