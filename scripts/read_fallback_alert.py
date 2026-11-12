#!/usr/bin/env python3
"""A full sweep pass that fell back to Linear is a console alert (DRE-8798),
counted over every step of the sweep job (DRE-9251).

A read the door could not answer falls back to Linear and spends its budget,
and until DRE-8798 the only trace was one `read-fallback:` line per read on
a run's stderr (DRE-8794, DRE-8796) — a log nobody reads.

Until DRE-9251 the sweep settled the alert at the end of its own process,
from `bureau_read.fallbacks()` — that process's alone. The five steps after
it run their own processes with the door on, and none of their fallbacks
reached the alert: run 41697018402 resolved it in its Sweep step and printed
two `Proof overdue` fallbacks further down the same log. Now:

* The Sweep step and the five phases after it (`Dispatch proof runs`, `Close
  proof records that cannot merge`, `Proof overdue`, `Reply to the CEO's Green
  Light comments`, `Read the switches`) set `BUREAU_READ_LINES_FILE` to
  `$RUNNER_TEMP/sweep-read-lines.log` (`LINES_FILE`), so `bureau_read` copies
  every `read-fallback:` and `read-door:` line each of their processes prints
  there (DRE-9168). No model session runs in that job, so nothing but the
  pipeline's own processes writes it.
* `reconcile.py` makes no notice call for this alert. Where it settled, a
  full pass that reached its end writes `$RUNNER_TEMP/read-fallback-settle.json`
  (`SETTLE_FILE`, `write_settle`): `{"settle": true}`, or `{"settle": false,
  "why": "idle (<why>)"}` on an idle pass. A card-scoped, `--promote-only` or
  `--close-only` pass writes nothing: those make fewer reads than a full pass,
  so settling there would resolve what the next full pass raises again.
* The job's last step, `Settle the read-fallback alert`, runs
  `read_fallback_alert.py settle` (`cmd_settle`). With no settle file, or one
  that says not to, it prints `not settled this pass — <why>` and makes no
  notice call — a crashed sweep leaves the alert as the last full pass left
  it. A lines file it cannot read is `not settled this pass — no lines file`,
  never a resolve. Otherwise it reads every `read-fallback:` line of the lines
  file into `(reader, read, reason)` (`read_lines`) and hands them, with
  `bureau_read.mode()` and the count of processes, to `settle`.

`settle(fallbacks, repo_slug, mode, processes)` raises one WARN console alert
for the repo with one or more fallbacks, naming the readers; with none it
resolves the repo's alert, so a repo that stops falling back clears.

## The alert

Notice key `read-fallback:<repo>` (console kind
`pipeline_notice:read-fallback:<repo>`), one per repo, severity WARN, raised
through `pipeline_notice.raise_notice` with no fallback card. The title names
the repo and the count of fallbacks; the body has one line per distinct
`(reader, read, reason)` with its count. A standing condition raises the same
key every pass and the door answers `repeat`, which delivers nothing again.

A raise the door cannot take is `lost`, named on the line, and never fails the
pass. It never files a Linear card: the door being down is exactly when a card
would spend the Linear budget the alert is about.

In a mode other than `on` no fallback can be noted (`bureau_read.note_fallback`
notes none), so there is nothing to read and no notice call is made — neither
a raise nor a resolve.

## The line

Exactly one per pass, on stdout, in the `Settle the read-fallback alert`
step's log; `settle` returns it:

    read-fallback-alert: <outcome> repo=<slug> <n> fallback(s) from <p> process(es) — reader="<reader>" read=<read> reason=<reason> ×<k>; …
    read-fallback-alert: <outcome> repo=<slug> 0 fallback(s) from <p> process(es)
    read-fallback-alert: door <mode> repo=<slug> — no fallback is noted; no notice call
    read-fallback-alert: not settled this pass — <why>

`<outcome>` is the client's: `raised`, `repeat`, `escalated` or `lost` for a
raise, `resolved`, `not-open` or `resolve-refused` for a resolve. The Sweep
step's own log carries none.

`<p>` is the number of lines opening `read-door: served` in the lines file:
one per process of the six steps above that read the mode in `shadow` or `on`,
so a reader can see the line counted every step. Two processes of the same job
print a `read-door: served` exit line into the run's log and are outside `<p>`,
because neither writes the lines file:

1. `bureau_read.py check-ref`, in `Refuse a pipeline_ref the stub did not
   call`, before the Sweep step's first line.
2. The Settle process itself. It reads the mode, so it prints
   `read-door: mode on` before its `read-fallback-alert:` line and its own
   `read-door: served 0 …` exit line after it — the last of the run's log. Its
   step sets no `BUREAU_READ_LINES_FILE`, and `__main__` drops one that is set
   anyway, so it never writes the file it reads.

So in mode `on` a run's log holds `<p>` plus 2 lines opening
`read-door: served`.

What it sees: every door-reading step of the sweep job. What it still does
not see: fallbacks made by other workflows (a build, a review) — they are not
in this job. The budget check's fallback table counts them.

## The vendor boundary (`standards/vendor-boundaries.md`)

* Q1, the actor: the reconcile reusable already raises notices
  (`standing-defect:`, `reviewer_outage:fleet`), and the console door admits
  it. The Settle step is the same sweep job and identity.
* Q2, the credentials: the sweep job already holds `id-token: write`, which
  the notice client needs, and the Settle step carries the door variables
  every phase carries.
* Q3, a retry: a re-run is a fresh job with an empty `$RUNNER_TEMP`, raises
  the same key, and the door answers `repeat`.
* Q4, the limits: the door refuses a key outside `^[A-Za-z0-9_.:-]{1,120}$`,
  and `read-fallback:<repo>` is inside it; the client cuts a long body.
* Q5, a crash mid-flow: a pass that never wrote the settle file settles
  nothing, so the alert stays as the last full pass left it, and the next
  full pass settles it.

## Usage

    python3 scripts/read_fallback_alert.py settle --lines-file <path> \\
        --settle-file <path> --repo <slug>

It always exits 0: an alert never decides a run's status.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable

import bureau_read
import pipeline_notice

TAG = "read-fallback-alert:"
NOTICE_PREFIX = "read-fallback"
SEVERITY = "WARN"
#: The two files in `$RUNNER_TEMP` the sweep job shares (DRE-9251): every
#: door-reading step's read lines, and whether the full pass may settle.
LINES_FILE = "sweep-read-lines.log"
SETTLE_FILE = "read-fallback-settle.json"
#: The line `read-door: served …` opens with: one per process of the lines
#: file that read the mode in `shadow` or `on`.
SERVED_PREFIX = "read-door: served"
#: `bureau_read.note_fallback`'s line, read back into its three fields.
_FALLBACK_LINE = re.compile(
    re.escape(bureau_read.FALLBACK_TAG) + r' reader="(.*)" read=(\S+) reason=(\S+)$')
#: A `read-fallback:` line that does not read as one: still a fallback, so it
#: still keeps the alert raised.
UNPARSED = ("?", "?", "unparsed")
RESOLVE_SENTENCE = ("This alert resolves on the first full sweep of this repo that "
                    "reads everything it needs from the read door.")


def notice_key(repo_slug: str) -> str:
    return f"{NOTICE_PREFIX}:{repo_slug}"


def groups(fallbacks: Iterable) -> Counter:
    """Each distinct `(reader, read, reason)` and its count, in first-seen order."""
    return Counter(tuple(f) for f in fallbacks)


def group_text(group: tuple, count: int) -> str:
    reader, read, reason = group
    return f'reader="{reader}" read={read} reason={reason} ×{count}'


def title(repo_slug: str, total: int) -> str:
    return f"The {repo_slug} sweep fell back to Linear on {total} read(s)"


def body(repo_slug: str, counted: Counter) -> str:
    named = "\n".join(f"- {group_text(g, n)}" for g, n in counted.items())
    return (f"The last full {repo_slug} sweep could not get these reads from the read "
            f"door and read Linear instead, spending its budget:\n\n{named}\n\n"
            f"{RESOLVE_SENTENCE}")


def settle(fallbacks, repo_slug: str, mode: str, processes: int | None = None) -> str:
    """Raise the repo's alert when `fallbacks` holds any, resolve it when it
    holds none, call the door not at all when `mode` is not `on` — and return
    the one line printed. `processes`, when given, is the `<p>` the line
    names."""
    counted = groups(fallbacks)
    total = sum(counted.values())
    read_from = "" if processes is None else f" from {processes} process(es)"
    if mode != "on":
        text = f"door {mode} repo={repo_slug} — no fallback is noted; no notice call"
    elif counted:
        outcome = pipeline_notice.raise_notice(
            notice_key(repo_slug), severity=SEVERITY, title=title(repo_slug, total),
            body=body(repo_slug, counted), fallback=None)
        named = "; ".join(group_text(g, n) for g, n in counted.items())
        text = f"{outcome} repo={repo_slug} {total} fallback(s){read_from} — {named}"
    else:
        outcome = pipeline_notice.resolve_notice(notice_key(repo_slug))
        text = f"{outcome} repo={repo_slug} 0 fallback(s){read_from}"
    return _print(text)


def _print(text: str) -> str:
    line = f"{TAG} {text}"
    print(line, flush=True)
    return line


# ── the settle file: whether this pass may settle (DRE-9251) ────────────────


def write_settle(why: str | None = None) -> None:
    """Write `$RUNNER_TEMP/<SETTLE_FILE>`: this full pass reached its end and
    may settle, or — with `why` — why it may not. Without `RUNNER_TEMP` (a
    pass run by hand) it writes nothing, and the Settle step is not there to
    read it. A failure to write is the caller's to swallow."""
    directory = os.environ.get("RUNNER_TEMP")
    if not directory:
        return
    verdict = {"settle": True} if why is None else {"settle": False, "why": why}
    with open(os.path.join(directory, SETTLE_FILE), "w", encoding="utf-8") as f:
        json.dump(verdict, f)


def settle_refusal(path: str) -> str | None:
    """None when the settle file at `path` says this pass may settle, else why
    not: no file, a file that does not read, or the `why` it carries."""
    try:
        with open(path, encoding="utf-8") as f:
            verdict = json.load(f)
    except FileNotFoundError:
        return "no settle file"
    except (OSError, ValueError) as e:
        return f"settle file unreadable ({type(e).__name__}: {e})"
    if not isinstance(verdict, dict):
        return "settle file unreadable (not a JSON object)"
    if verdict.get("settle") is True:
        return None
    return str(verdict.get("why") or "the settle file says not to settle")


# ── the lines file: every door-reading step's read lines (DRE-9251) ─────────


def read_lines(path: str) -> tuple[list[tuple[str, str, str]], int]:
    """Every `read-fallback:` line of the lines file as `(reader, read,
    reason)`, in order, and the number of lines opening `read-door: served`
    — one per process. Raises OSError when the file cannot be read."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    fallbacks: list[tuple[str, str, str]] = []
    processes = 0
    for line in lines:
        if line.startswith(SERVED_PREFIX):
            processes += 1
        elif line.startswith(bureau_read.FALLBACK_TAG):
            match = _FALLBACK_LINE.match(line)
            fallbacks.append(match.groups() if match else UNPARSED)
    return fallbacks, processes


def cmd_settle(lines_file: str, settle_file: str, repo_slug: str) -> str:
    """The `Settle the read-fallback alert` step: settle the repo's alert over
    every step's fallbacks, when the full pass said it may. Returns the line."""
    # Read first, whatever follows: in `shadow` or `on` this process says
    # `read-door: mode …` now and its own `read-door: served` line at exit.
    mode = bureau_read.mode()
    why = settle_refusal(settle_file)
    if why is not None:
        return _print(f"not settled this pass — {why}")
    try:
        fallbacks, processes = read_lines(lines_file)
    except OSError:
        return _print("not settled this pass — no lines file")
    try:
        return settle(fallbacks, repo_slug, mode, processes)
    except Exception as e:  # noqa: BLE001 — an alert never decides the exit
        return _print(f"not settled this pass — {type(e).__name__}: {e}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    settle_parser = commands.add_parser("settle", help="settle the repo's alert")
    settle_parser.add_argument("--lines-file", required=True)
    settle_parser.add_argument("--settle-file", required=True)
    settle_parser.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    cmd_settle(args.lines_file, args.settle_file, args.repo)
    return 0


if __name__ == "__main__":
    # This process reads the lines file and must never add to it: its own
    # `read-door:` lines belong to the run's log, outside `<p>`.
    os.environ.pop(bureau_read.LINES_FILE_ENV, None)
    sys.exit(main())
