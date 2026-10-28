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

## The rule the medic applies (DRE-8239)

A run's FIRST out-of-memory kill keeps the medic's one retry of the failed
jobs, on any runner; the run's second kill is refused. "First" is counted per
run, not by attempt number: on attempt 2 the medic reads attempt 1's failed
log with this same reader, and an attempt 1 that was not killed leaves attempt
2's kill its one retry. This replaced DRE-4847's rule, under which only a
light runner's first kill was retried. On 10-27 and 10-28 the medic refused
the kills of agent-bureau CI runs 40596656325 and 40616890320 on 4 GB heavy
runners, and a plain re-run of the failed jobs on the same class of machine
passed every time: a suite whose peak sits near the cap is killed on some runs
and not others. A wasted retry costs one CI run, and a refused one cost a stall
of hours. The rule lives in `medic_retry.out_of_memory_refusal`.

## The machine, and its class

The receipt names the machine off the Jobs API first (`machine_from_jobs`, the
killed job's `runner_name` and its class label, `bureau-heavy`, `bureau-light`
or `bureau-tiny`), then off the `Runner name:` line the log carries in its
set-up steps, and failing both says "an unnamed runner" (`named`, DRE-8239).
The Jobs API comes first because the log is not a reliable witness: the
medic's fetch on the bureau pool's own light runners, whose image installs
`gh` from Ubuntu's apt, carried the kill lines and no `Runner name:` line.

The class read off the name — `bureau-mini-light-*` is `light` (the 2 GB
pool), any other `bureau-mini-*` is `heavy`, `GitHub Actions *` is `hosted`,
and anything else, a log with no runner-name line included, is `unknown` —
is wording on the receipt now, and decides nothing.

With `gh` 2.100.0 `--log-failed` returns the whole job log, because GitHub's
log is no longer step-mapped (every line reads `UNKNOWN STEP`). That is what
puts the set-up lines and the kill line in one fetch, and the shape depends on
the `gh` version. If `gh` ever narrows it so the medic sees no 137 line, it
classifies `normal`, which is never a false out-of-memory.

CLI:

    python3 out_of_memory.py <log-file>

prints the runner, its class and the kill count, or says there was no kill;
exit 0 either way (a classifier that fails its caller has turned one broken run
into two).
"""

from __future__ import annotations

import json
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

#: The classes of machine, as the runner's name says them. Wording on the
#: receipt since DRE-8239: a run's first kill keeps its retry on every class.
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


#: The runner labels that name a class of machine in the bureau pool — the
#: one label the receipt names beside the runner (DRE-8239).
CLASS_LABELS = ("bureau-heavy", "bureau-light", "bureau-tiny")


class Kill(NamedTuple):
    """A killed run's facts: which machine, what size, and how many of its
    processes the kernel ended — and, since DRE-8239, the job the first kill
    was in and the machine's class label off the Jobs API."""

    runner: str
    runner_class: str
    kills: int
    job: str = ""
    label: str = ""


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
    runner, job, kills = "", "", 0
    for raw in (log_text or "").splitlines():
        prefix = _LOG_PREFIX.match(raw)
        line = raw[prefix.end():] if prefix else raw
        if _QUOTATION_LINE.match(line):
            continue
        if _KILL.match(line):
            kills += 1
            # The job field of the first kill line: the name the Jobs API
            # knows the killed job by (`machine_from_jobs`).
            if kills == 1 and prefix and "\t" in prefix.group(0):
                job = prefix.group(0).split("\t", 1)[0].strip()
            continue
        if not runner:
            found = _RUNNER.match(line)
            if found:
                runner = found.group("name").strip()
    if not kills:
        return None
    return Kill(runner, runner_class(runner), kills, job)


def machine_from_jobs(reading, *, job: str = "") -> tuple:
    """`(runner_name, class label)` off a Jobs API reading, or `("", "")`.

    `reading` is the text `repos/<repo>/actions/runs/<id>/attempts/<n>/jobs`
    answered. The job is the one named `job` — the job the first kill line
    was in — and, when no job carries that name, the first failed job that
    names a runner. The label is the first of `CLASS_LABELS` the job asked
    for, or "". Anything that is not that answer — an empty file, an error
    message, a shape it does not know — names nothing, so the caller falls
    back to the log.
    """
    try:
        payload = json.loads(reading or "")
    except (TypeError, ValueError):
        return "", ""
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return "", ""
    named = [j for j in jobs if isinstance(j, dict)
             and str(j.get("runner_name") or "").strip()]
    chosen = next((j for j in named if job and j.get("name") == job), None)
    if chosen is None:
        chosen = next((j for j in named if j.get("conclusion") == "failure"), None)
    if chosen is None:
        return "", ""
    labels = chosen.get("labels") if isinstance(chosen.get("labels"), list) else []
    label = next((str(l) for l in labels if str(l) in CLASS_LABELS), "")
    return str(chosen["runner_name"]).strip(), label


def named(kill: Kill | None, runner: str = "", label: str = "") -> Kill | None:
    """The kill, with its machine named off the Jobs API where that read
    answered (DRE-8239).

    The Jobs API's runner wins over the log's `Runner name:` line, and the
    log's line over nothing — `describe` says "an unnamed runner" when both
    are empty.
    """
    if kill is None:
        return None
    runner = (runner or "").strip()
    if not runner:
        return kill._replace(label=(label or "").strip())
    return kill._replace(runner=runner, runner_class=runner_class(runner),
                         label=(label or "").strip())


def describe(kill: Kill | None) -> str:
    """The kill in one plain-English clause, naming the machine and its size.

    Written once here because both readers need the same words: the classifier
    says it on the run, and the retry gate says it on the card.
    """
    if kill is None:
        return ""
    where = kill.runner or "an unnamed runner"
    size = f"a {kill.label} runner" if kill.label else _CLASS_WORDS[kill.runner_class]
    times = "" if kill.kills == 1 else f", {kill.kills} times in this run"
    return (
        f"{where} ({size}) ran out of memory: the "
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
