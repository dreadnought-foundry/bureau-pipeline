#!/usr/bin/env python3
"""Who spent the fleet's Linear hour? READ-ONLY (DRE-3202, stdlib + `gh`).

The workspace quota is 2,500 requests per hour and it is shared by every run
in every repo. On 2026-09-05 it ran dry twice and nothing could say which run
spent it. Since DRE-3202 every process that talks to Linear through
`linear_ops.gql` prints one line as it exits:

    linear-budget: <first> → <last> (spent <N> this run; window resets <HH:MM> PT)

This script adds those lines up. For every repo in config/repo-map.json it
lists the runs of the last hour (`--hours N` widens the window), fetches each
run's log, and prints one row per (repo, workflow): runs seen, total spent,
the most one run spent — sorted by total, with a grand total at the bottom.

A sweep is read off its own count instead (DRE-5201). The budget line is the
SHARED key's remaining quota read as the process starts and exits, so two
sweeps that overlap each count the other's requests: on 2026-09-28
bureau-pipeline's pass read 59, its own 20 plus agent-bureau's 39. The sweep
also prints `sweep-spend: total <N> request(s) …`, counted by the process
itself, and a run that printed one is charged that and its budget lines are
not added. Every other workflow is still read off its budget lines.

Every process now says its own count (Stage 2 #11), and that outranks both:

    linear-calls: <N> request(s) this run (process: <token>; budget: <bucket>)

N is the requests that process sent on that bucket, counted by `linear_ops`
itself — exact, known even when no rate-limit header came back, and free of
anyone else's requests; a process that fell back mid-run prints one line per
bucket. A run that printed any is charged their sum, per bucket, a line
repeated with the same process token counted once, and its `sweep-spend:
total` lines are not added: the sweep's total is a part of its own process's
count, and it says nothing about the run's other processes. A budget line
covered by the calls lines printed right after it adds nothing; one that no
count covers is reported in its own `[undeclared]` row, never dropped. A log
from before the line existed reads exactly as it did.

A spend on ANOTHER BUCKET is kept in its own row (DRE-5589). The line ends
`budget: <bucket>`, and the planner's OAuth token — the fleet user, metered
apart from the fleet key — prints `budget: planner-oauth`. Adding that to the
fleet key's row would charge the fleet for requests it never paid, so a row is
(repo, workflow, bucket) and prints as `Agent Plan [planner-oauth]`. The fleet
key's own lines (`budget: fleet`, `undeclared`, or no owner at all) stay in
today's row, unlabeled.

Both markers are read only where they OPEN a line — after the job/step/
timestamp prefix `gh run view --log` adds. GitHub echoes a step's script into
the log before running it, and the Sweep step's script names `linear-budget:`
three times; matched anywhere, that added three "unknown/rolled" lines to
every reconcile run.

A repo the token cannot read prints UNKNOWN, never 0: a zero that means
"could not look" would hide exactly the repo that is spending. A run whose
log `gh` cannot return (still in progress, or expired) is read again, up to
3 attempts with a short back-off between them (DRE-3242): a run that has only
just finished usually reads on the second look. One still unreadable after
the last attempt is named in the footer as `<slug> <run id>`, and the last
thing `gh` said about it goes to stderr. An empty log — `gh` answers rc 0
with no output for a completed run whose every job was skipped — is not
retried, since a second look returns the same nothing; it is in no row and
is counted in its own `empty log` note. A line that says `window rolled` or
`unknown` is a run seen with a spend that cannot be known; it counts as a
run, not as 0 spent.

A second table follows the first (DRE-8795): the Linear reads that fell back
from the read door, per pipeline reader. The client prints one line per
fallback,

    read-fallback: reader="<workflow> / <job> / <script>" read=<read> reason=<reason>

and this script counts them out of the same run logs, one row per (repo,
reader, read, reason), sorted by count, then by name. It is reads only:
Linear writes and the console's own Linear sync never print the line. A run
whose processes printed `read-door: mode off` (or `shadow`) and never `mode
on` never asked the door, so it adds nothing to the rows; its repo is named
on a `door off` line with its run count instead, because its silence must
never read as zero fallbacks. With no line in the window the table says so
in words, with the number of runs read in mode `on`, and a repo the token
cannot read prints UNKNOWN there too. The line is read only where it opens a
line, exactly as the markers above are.

A third table follows (DRE-8897): the reads each process sent Linear
directly, per reader and the place in its code that asked. Every process that
sent Linear a read through `linear_ops.py` prints, at exit, one line per
(site, caller, bucket):

    linear-read: reader="<workflow> / <job> / <script>" site=<module>.<function> count=<n> budget=<bucket> process=<token> caller=<module>.<function>

`site` is the place in the calling code that asked for the read and `caller`
the function that sent the query; a write is never on the line. The table,
`direct Linear reads, last <N>h — per reader and site`, has one row per
(repo, reader, site, bucket) with the count summed over callers, sorted by
count, then by name, and then one TOTAL row per (repo, reader). `--by-caller`
prints it with one more key column instead, under `direct Linear reads, last
<N>h — per reader, site and caller`: one row per (repo, reader, site, caller,
bucket), the same TOTAL rows under it.

A fourth follows that, in both forms (DRE-8897): the door's answers, on the
key they were made under. Every process whose read-door mode was `on` prints
one line at exit:

    linear-door: reader="<workflow> / <job> / <script>" served=<n> fallbacks=<read>/<reason>:<n>[,...] budget=<bucket> process=<token>

and `door answers, last <N>h — per reader and bucket` has one row per (repo,
reader, bucket) with `served` summed and one `<read>/<reason>` column per
fallback seen, summed, sorted by served, then by name.

Both lines are read only where they open a line, as every marker above is,
and a line repeated with the same `process=` is counted once. A run with
neither adds nothing to those tables, and changes nothing in the first two:
a log from before them reads exactly as it did. With no line in the window
each table says so in words, and a repo the token cannot read prints UNKNOWN
there too.

Three optional flags make it read less (DRE-9163). Walking every repo and
every run, it can outlast a proof run's tool ceiling and be killed having
printed nothing, since the tables come only after the last log is read.
`--repo <slug>`, repeatable, lists and reads only the named slugs of
config/repo-map.json; a slug the map does not hold exits 2 before any `gh`
call, and stderr names it and the valid ones. `--workflow <name>`,
repeatable, fetches the log of only the runs whose `workflowName` is one of
the names; the others are never fetched. `--deadline <seconds>` reads the
time since the script started before each `gh run list` and each `gh run
view`; once it reaches the deadline nothing more is read, and the four
tables print from the runs that were. The spend table's footer then carries
one more note, `stopped at the deadline (<seconds>s)`, naming per slug how
many of its listed runs — of the named workflows, with `--workflow` — were
not read, and the slugs never reached. A slug never reached prints the
UNKNOWN rows an unreadable repo prints, never 0. With none of the three,
the output is exactly what it was.

Never writes anything: `gh run list` and `gh run view --log` are its only
calls. Usage:

    python3 scripts/check_linear_budget.py [--hours N] [--by-caller] [--repo SLUG ...] [--workflow NAME ...] [--deadline SECONDS]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 — fixed-arg calls to the gh CLI only
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_MAP_PATH = Path(__file__).resolve().parent.parent / "config" / "repo-map.json"

# Where a printed line starts. `gh run view --log` prefixes each line with
# `job\tstep\ttimestamp ` (the timestamp sometimes behind a byte-order mark on
# a job's first line); a log saved without it starts at the line itself. What
# follows the prefix must be the marker — the echoed script that merely names
# it starts with a colour code, a `#` or a command (DRE-5201).
_LINE_START = (
    r"^(?:[^\t\n]*\t[^\t\n]*\t)?\ufeff?"
    r"(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z )?"
)
# The line linear_ops.budget_line() composes. The arrow is the literal U+2192
# the seam prints.
_SPENT_RE = re.compile(
    _LINE_START + r"linear-budget:\s*(\d+)\s*→\s*(\d+)\s*\(spent\s+(\d+)\s+this run"
)
_SEEN_RE = re.compile(_LINE_START + r"linear-budget:")
# The owner part linear_ops.budget_line() ends every line with (DRE-3321), and
# the words that mean the fleet key's own bucket — today's unlabeled row.
_BUCKET_RE = re.compile(r";\s*budget:\s*([A-Za-z0-9_-]+)\)\s*$")
_FLEET_BUCKETS = frozenset({"fleet", "undeclared"})
# The last line of every sweep pass, reconcile.SweepSpend.report_total().
_SWEEP_TOTAL_RE = re.compile(_LINE_START + r"sweep-spend: total (\d+) request")
# The process's own count, linear_ops.calls_lines() (Stage 2 #11), the
# process token that names who printed it (item 28), and the bucket it names
# in the parentheses that close it.
_CALLS_RE = re.compile(_LINE_START + r"linear-calls:\s*(\d+)\s+request")
_CALLS_TOKEN_RE = re.compile(r"\(process:\s*([A-Za-z0-9_.@-]+);")
_CALLS_BUCKET_RE = re.compile(r"[(;]\s*budget:\s*([A-Za-z0-9_-]+)\)\s*$")
# The row a budget line's spend lands in when the run printed calls lines and
# none of them covers it (item 28, K4): reported, never dropped, and never
# folded into an exact count it is not part of.
UNDECLARED = "undeclared"
# The client's line, one per read that fell back from the read door
# (DRE-8795), and the mode line bureau_read.mode() prints once per process.
# The reader is one double-quoted value and may hold spaces.
_FALLBACK_RE = re.compile(
    _LINE_START + r'read-fallback: reader="([^"\n]*)" read=(\S+) reason=(\S+)'
)
_DOOR_MODE_RE = re.compile(_LINE_START + r"read-door: mode (off|shadow|on)\b")
# The lines linear_ops prints at exit (DRE-8897): one per (site, caller,
# bucket) a process read on, and one for the door's answers in mode `on`.
_READ_RE = re.compile(
    _LINE_START + r'linear-read: reader="([^"\n]*)" site=(\S+) count=(\d+) '
    r"budget=(\S+) process=(\S+) caller=(\S+)"
)
_DOOR_RE = re.compile(
    _LINE_START + r'linear-door: reader="([^"\n]*)" served=(\d+) fallbacks=(\S+) '
    r"budget=(\S+) process=(\S+)"
)

UNKNOWN = "UNKNOWN"

# How many times a run's log is asked for before it is named unreadable, and
# the pause between two asks (DRE-3242): two pauses, well under half a minute
# per run. `_sleep` is the one seam the back-off goes through, so a test sets
# it to nothing and never sleeps.
LOG_ATTEMPTS = 3
LOG_BACKOFF_SECONDS = 5.0
_sleep = time.sleep
# The clock `--deadline` is read off (DRE-9163) — the one seam, as `_sleep`
# is the back-off's, so a test moves it and never waits.
_monotonic = time.monotonic


class _DeadlineReached(Exception):
    """`--deadline` passed before a `gh` call: read nothing more."""


def load_repo_map() -> dict[str, str]:
    """slug → "owner/repo", read the way validate_card reads it."""
    return json.loads(REPO_MAP_PATH.read_text())


def spent_from_log(log_text: str) -> list[int | None]:
    """One entry per budget line in `log_text`: the spend as an int, or None
    for a line whose spend cannot be known (`window rolled`, `unknown`)."""
    out: list[int | None] = []
    for line in (log_text or "").splitlines():
        if not _SEEN_RE.match(line):
            continue
        m = _SPENT_RE.match(line)
        out.append(int(m.group(3)) if m else None)
    return out


def bucket_of(line: str) -> str:
    """The bucket a budget line names — `""` for the fleet key's own (DRE-5589)."""
    m = _BUCKET_RE.search(line.rstrip())
    return _fleet_or(m.group(1) if m else "")


def _fleet_or(bucket: str) -> str:
    return "" if bucket in _FLEET_BUCKETS else bucket


def counted_entries(log_text: str) -> list[tuple[str, int | None]] | None:
    """A run's spend as `(bucket, spend)` entries in log order, read off its
    `linear-calls:` lines — or None when it printed none (an older log).

    - Each calls line is one entry, in the bucket it names (`""` for the
      fleet key's own, as `bucket_of` reads a budget line).
    - A line seen again with the same process token and bucket — a step that
      `cat`s a log the process already printed — is counted once (item 28,
      K2). A line with no token cannot be told from a repeat and is counted.
    - A process prints its budget line and then its calls lines, one per
      bucket, back to back. So a budget line whose next marker line is a
      calls line is COVERED by that count and adds nothing. A budget line
      followed by another budget line, or by nothing, is spend no count
      covers — a process killed past any handler, or a script from before
      the calls line — and lands in the `undeclared` row (item 28, K4), its
      `spent N` or, for `window rolled` / `unknown`, an unknown entry.
    """
    markers: list[tuple[str, str]] = []
    for line in (log_text or "").splitlines():
        if _CALLS_RE.match(line):
            markers.append(("calls", line))
        elif _SEEN_RE.match(line):
            markers.append(("budget", line))
    if not any(kind == "calls" for kind, _ in markers):
        return None
    entries: list[tuple[str, int | None]] = []
    seen: set[tuple[str, str]] = set()
    for i, (kind, line) in enumerate(markers):
        if kind == "budget":
            if i + 1 < len(markers) and markers[i + 1][0] == "calls":
                continue
            m = _SPENT_RE.match(line)
            entries.append((UNDECLARED, int(m.group(3)) if m else None))
            continue
        b = _CALLS_BUCKET_RE.search(line.rstrip())
        bucket = _fleet_or(b.group(1) if b else "")
        t = _CALLS_TOKEN_RE.search(line)
        if t:
            if (t.group(1), bucket) in seen:
                continue
            seen.add((t.group(1), bucket))
        entries.append((bucket, int(_CALLS_RE.match(line).group(1))))
    return entries


def run_spend_by_bucket(log_text: str) -> dict[str, list[int | None]]:
    """`run_spend_from_log`, split by the bucket each line names. A sweep's
    own total names none, and a sweep spends the fleet key, so it is `""`."""
    counted = counted_entries(log_text)
    if counted is not None:
        out_counted: dict[str, list[int | None]] = {}
        for bucket, spent in counted:
            out_counted.setdefault(bucket, []).append(spent)
        return out_counted
    lines = (log_text or "").splitlines()
    totals = [int(m.group(1)) for m in map(_SWEEP_TOTAL_RE.match, lines) if m]
    if totals:
        return {"": totals}
    out: dict[str, list[int | None]] = {}
    for line in lines:
        if not _SEEN_RE.match(line):
            continue
        m = _SPENT_RE.match(line)
        out.setdefault(bucket_of(line), []).append(int(m.group(3)) if m else None)
    return out or {"": []}


def run_spend_from_log(log_text: str) -> list[int | None]:
    """What ONE run spent, entry by entry: its `linear-calls:` lines when it
    printed any — each process's own count, plus the spend of any budget line
    no count covers (`counted_entries`) — else its `sweep-spend: total` lines
    — the pass's own count — and otherwise its budget lines
    (`spent_from_log`). A sweep's total is a part of its own process's count,
    and a covered budget line counts the same requests again, plus whatever
    else drew on the shared key meanwhile, so neither is added to a count."""
    counted = counted_entries(log_text)
    if counted is not None:
        return [spent for _bucket, spent in counted]
    totals = [
        int(m.group(1))
        for m in map(_SWEEP_TOTAL_RE.match, (log_text or "").splitlines())
        if m
    ]
    return totals or spent_from_log(log_text)


def aggregate(observations) -> list[dict]:
    """Rows per (repo, workflow) from `(repo, workflow, log_text)` triples —
    one triple per run. Sorted by total spent, descending, then by name so
    equal totals print in a stable order."""
    rows: dict[tuple[str, str, str], dict] = {}
    for repo, workflow, log_text in observations:
        for bucket, spends in run_spend_by_bucket(log_text).items():
            row = rows.setdefault(
                (repo, workflow, bucket),
                {"repo": repo, "workflow": workflow, "budget": bucket, "runs": 0,
                 "total": 0, "max": 0, "unknown_lines": 0},
            )
            row["runs"] += 1
            run_total = 0
            for spent in spends:
                if spent is None:
                    row["unknown_lines"] += 1
                    continue
                run_total += spent
            row["total"] += run_total
            row["max"] = max(row["max"], run_total)
    return sorted(rows.values(),
                  key=lambda r: (-r["total"], r["repo"], r["workflow"], r["budget"]))


def render_table(rows: list[dict], unknown_repos: list[str], *,
                 hours: float = 1, skipped: list[tuple[str, int]] = (),
                 empty: int = 0,
                 stopped: tuple[float, dict[str, int], list[str]] | None = None) -> str:
    """The table, as text: one row per (repo, workflow), UNKNOWN rows for the
    repos the token could not read, a grand total last. `skipped` is each run
    whose log was still unreadable after every attempt, as `(slug, run id)`,
    and is named in the footer; `empty` counts the runs whose log was empty.
    `stopped`, when the deadline cut the reading short, is `(seconds, slug →
    listed runs not read, slugs never reached)`, and is named in the footer."""
    header = f"{'repo':<20} {'workflow':<32} {'runs':>5} {'spent':>7} {'max/run':>8}"
    lines = [f"linear budget, last {hours:g}h", header, "-" * len(header)]
    for r in rows:
        note = f"  ({r['unknown_lines']} line(s) unknown/rolled)" if r["unknown_lines"] else ""
        # The bucket survives the column's cut; the workflow name gives way.
        suffix = f" [{r['budget']}]" if r.get("budget") else ""
        label = r["workflow"][:32 - len(suffix)] + suffix
        lines.append(
            f"{r['repo']:<20} {label[:32]:<32} {r['runs']:>5} "
            f"{r['total']:>7} {r['max']:>8}{note}"
        )
    for repo in sorted(unknown_repos):
        lines.append(f"{repo:<20} {UNKNOWN:<32} {UNKNOWN:>5} {UNKNOWN:>7} {UNKNOWN:>8}")
    lines.append("-" * len(header))
    grand = sum(r["total"] for r in rows)
    runs = sum(r["runs"] for r in rows)
    tail = f"{'TOTAL':<20} {'':<32} {runs:>5} {grand:>7}"
    if unknown_repos:
        tail += f"   (+ {len(unknown_repos)} repo(s) UNKNOWN)"
    if skipped:
        named = ", ".join(f"{slug} {run_id}" for slug, run_id in skipped)
        tail += (f"   ({len(skipped)} run(s) skipped: log not available after "
                 f"{LOG_ATTEMPTS} attempts — {named})")
    if empty:
        tail += f"   ({empty} run(s) empty log: no job ran)"
    if stopped is not None:
        seconds, unread, unreached = stopped
        parts = [f"{slug} {n} run(s) not read" for slug, n in sorted(unread.items())]
        if unreached:
            parts.append("not reached: " + ", ".join(unreached))
        named = ": " + "; ".join(parts) if parts else ""
        tail += f"   (stopped at the deadline ({seconds:g}s){named})"
    lines.append(tail)
    return "\n".join(lines)


def fallbacks_from_log(log_text: str) -> list[tuple[str, str, str]]:
    """One `(reader, read, reason)` per `read-fallback:` line that opens its
    line — an echoed script that merely names the tag is not one (DRE-5201)."""
    out: list[tuple[str, str, str]] = []
    for line in (log_text or "").splitlines():
        m = _FALLBACK_RE.match(line.rstrip())
        if m:
            out.append((m.group(1), m.group(2), m.group(3)))
    return out


def door_modes(log_text: str) -> set[str]:
    """The read-door modes a run's processes printed: none, when no process
    asked whether the door was on."""
    return {m.group(1) for m in map(_DOOR_MODE_RE.match, (log_text or "").splitlines())
            if m}


def aggregate_fallbacks(observations) -> dict:
    """The fallback table from the same `(repo, workflow, log_text)` triples
    the spend table reads (DRE-8795).

    - `rows`: one per (repo, reader, read, reason) with the count of
      `read-fallback:` lines, sorted by count, descending, then by name.
    - `on_runs`: the runs that printed `read-door: mode on`.
    - `door_off`: repo → runs that printed `mode off` or `mode shadow` and
      never `mode on`. Such a run never asked the door, so it adds nothing
      to the rows — its silence is not zero fallbacks.
    """
    counts: dict[tuple[str, str, str, str], int] = {}
    on_runs = 0
    door_off: dict[str, int] = {}
    for repo, _workflow, log_text in observations:
        modes = door_modes(log_text)
        if modes and "on" not in modes:
            door_off[repo] = door_off.get(repo, 0) + 1
            continue
        if "on" in modes:
            on_runs += 1
        for reader, read, reason in fallbacks_from_log(log_text):
            key = (repo, reader, read, reason)
            counts[key] = counts.get(key, 0) + 1
    rows = [{"repo": repo, "reader": reader, "read": read, "reason": reason,
             "count": n} for (repo, reader, read, reason), n in counts.items()]
    rows.sort(key=lambda r: (-r["count"], r["repo"], r["reader"], r["read"],
                             r["reason"]))
    return {"rows": rows, "on_runs": on_runs, "door_off": door_off}


def render_fallbacks(summary: dict, unknown_repos: list[str], *,
                     hours: float = 1) -> str:
    """The fallback table, as text: one row per (repo, reader, read, reason),
    UNKNOWN rows for the repos the token could not read, then a line in words
    when no fallback line was found, and the `door off` line last. Never an
    empty table: with nothing to list there is no header at all."""
    rows = summary["rows"]
    lines = [f"read fallbacks, last {hours:g}h — reads only: Linear writes and "
             "the console's own Linear sync never print the line"]
    if rows or unknown_repos:
        width = max([len("reader"), len(UNKNOWN)] + [len(r["reader"]) for r in rows])
        header = (f"{'repo':<20} {'reader':<{width}} {'read':<16} "
                  f"{'reason':<14} {'count':>6}")
        lines += [header, "-" * len(header)]
        for r in rows:
            lines.append(f"{r['repo']:<20} {r['reader']:<{width}} {r['read']:<16} "
                         f"{r['reason']:<14} {r['count']:>6}")
        for repo in sorted(unknown_repos):
            lines.append(f"{repo:<20} {UNKNOWN:<{width}} {UNKNOWN:<16} "
                         f"{UNKNOWN:<14} {UNKNOWN:>6}")
        lines.append("-" * len(header))
    on_runs = summary["on_runs"]
    if rows:
        lines.append(f"{'TOTAL':<20} {sum(r['count'] for r in rows)} fallback(s) "
                     f"in {on_runs} run(s) read in mode on")
    else:
        lines.append(f"no read-fallback line in the window: {on_runs} run(s) "
                     "read in mode on")
    if summary["door_off"]:
        named = ", ".join(f"{repo} {n} run(s)"
                          for repo, n in sorted(summary["door_off"].items()))
        lines.append(f"door off: {named} — mode off or shadow: those runs never "
                     "asked the door, so their silence is not zero fallbacks")
    return "\n".join(lines)


def reads_from_log(log_text: str) -> list[dict]:
    """One entry per `linear-read:` line that opens its line, a line repeated
    with the same process counted once (DRE-8897): `reader`, `site`,
    `caller`, `budget`, `count`."""
    out: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    for line in (log_text or "").splitlines():
        m = _READ_RE.match(line.rstrip())
        if not m:
            continue
        reader, site, count, budget, process, caller = m.groups()
        key = (process, reader, site, caller, budget)
        if key in seen:
            continue
        seen.add(key)
        out.append({"reader": reader, "site": site, "caller": caller,
                    "budget": budget, "count": int(count)})
    return out


def aggregate_reads(observations, *, by_caller: bool = False) -> dict:
    """The direct-read table from the same `(repo, workflow, log_text)`
    triples the spend table reads (DRE-8897).

    - `rows`: one per (repo, reader, site, bucket) with the count summed over
      callers — with `by_caller`, one per (repo, reader, site, caller,
      bucket) — sorted by count, descending, then by name.
    - `totals`: one per (repo, reader), sorted the same way.
    """
    counts: dict[tuple[str, ...], int] = {}
    totals: dict[tuple[str, str], int] = {}
    for repo, _workflow, log_text in observations:
        for r in reads_from_log(log_text):
            key = ((repo, r["reader"], r["site"], r["caller"], r["budget"]) if by_caller
                   else (repo, r["reader"], r["site"], r["budget"]))
            counts[key] = counts.get(key, 0) + r["count"]
            totals[(repo, r["reader"])] = totals.get((repo, r["reader"]), 0) + r["count"]
    names = (("repo", "reader", "site", "caller", "budget") if by_caller
             else ("repo", "reader", "site", "budget"))
    rows = [dict(zip(names, key), count=n) for key, n in counts.items()]
    rows.sort(key=lambda r: (-r["count"], *(r[name] for name in names)))
    total_rows = [{"repo": repo, "reader": reader, "count": n}
                  for (repo, reader), n in totals.items()]
    total_rows.sort(key=lambda r: (-r["count"], r["repo"], r["reader"]))
    return {"rows": rows, "totals": total_rows, "by_caller": by_caller}


def render_reads(summary: dict, unknown_repos: list[str], *, hours: float = 1) -> str:
    """The direct-read table, as text: its rows, UNKNOWN rows for the repos
    the token could not read, then one TOTAL row per (repo, reader); with
    nothing to list, a line in words instead of a header."""
    rows, by_caller = summary["rows"], summary["by_caller"]
    keys = "reader, site and caller" if by_caller else "reader and site"
    lines = [f"direct Linear reads, last {hours:g}h — per {keys}"]
    if not rows and not unknown_repos:
        lines.append("no linear-read line in the window")
        return "\n".join(lines)
    columns = ["reader", "site"] + (["caller"] if by_caller else []) + ["budget"]
    widths = {c: max([len(c), len(UNKNOWN), len("TOTAL")] + [len(r[c]) for r in rows])
              for c in columns}

    def row(repo: str, cells: dict, count) -> str:
        middle = " ".join(f"{cells.get(c, ''):<{widths[c]}}" for c in columns)
        return f"{repo:<20} {middle} {count:>6}"

    header = row("repo", {c: c for c in columns}, "count")
    lines += [header, "-" * len(header)]
    lines += [row(r["repo"], r, r["count"]) for r in rows]
    lines += [row(repo, {c: UNKNOWN for c in columns}, UNKNOWN)
              for repo in sorted(unknown_repos)]
    lines.append("-" * len(header))
    lines += [row(t["repo"], {"reader": t["reader"], "site": "TOTAL"}, t["count"])
              for t in summary["totals"]]
    if not rows:
        lines.append("no linear-read line in the window")
    return "\n".join(lines)


def doors_from_log(log_text: str) -> list[dict]:
    """One entry per `linear-door:` line that opens its line, a line repeated
    with the same process counted once (DRE-8897): `reader`, `served`,
    `fallbacks` (`<read>/<reason>` → count) and `budget`."""
    out: list[dict] = []
    seen: set[str] = set()
    for line in (log_text or "").splitlines():
        m = _DOOR_RE.match(line.rstrip())
        if not m:
            continue
        reader, served, fallbacks, budget, process = m.groups()
        if process in seen:
            continue
        seen.add(process)
        counted: dict[str, int] = {}
        for part in ([] if fallbacks == "-" else fallbacks.split(",")):
            name, _, n = part.rpartition(":")
            if name and n.isdigit():
                counted[name] = counted.get(name, 0) + int(n)
        out.append({"reader": reader, "served": int(served), "fallbacks": counted,
                    "budget": budget})
    return out


def aggregate_doors(observations) -> dict:
    """The door table (DRE-8897): `rows`, one per (repo, reader, bucket) with
    `served` and each fallback summed, sorted by served, descending, then by
    name; `fallbacks`, every `<read>/<reason>` seen, sorted — one column each."""
    rows: dict[tuple[str, str, str], dict] = {}
    seen: set[str] = set()
    for repo, _workflow, log_text in observations:
        for d in doors_from_log(log_text):
            row = rows.setdefault((repo, d["reader"], d["budget"]), {
                "repo": repo, "reader": d["reader"], "budget": d["budget"],
                "served": 0, "fallbacks": {}})
            row["served"] += d["served"]
            for name, n in d["fallbacks"].items():
                row["fallbacks"][name] = row["fallbacks"].get(name, 0) + n
                seen.add(name)
    out = sorted(rows.values(),
                 key=lambda r: (-r["served"], r["repo"], r["reader"], r["budget"]))
    return {"rows": out, "fallbacks": sorted(seen, key=lambda f: tuple(f.split("/", 1)))}


def render_doors(summary: dict, unknown_repos: list[str], *, hours: float = 1) -> str:
    """The door table, as text: one row per (repo, reader, bucket) and one
    column per fallback, UNKNOWN rows for the repos the token could not read;
    with nothing to list, a line in words instead of a header."""
    rows, fallbacks = summary["rows"], summary["fallbacks"]
    lines = [f"door answers, last {hours:g}h — per reader and bucket"]
    if not rows and not unknown_repos:
        lines.append("no linear-door line in the window")
        return "\n".join(lines)
    rw = max([len("reader"), len(UNKNOWN)] + [len(r["reader"]) for r in rows])
    bw = max([len("budget"), len(UNKNOWN)] + [len(r["budget"]) for r in rows])
    fw = {f: max(len(f), len(UNKNOWN)) for f in fallbacks}

    def row(repo, reader, budget, served, counts) -> str:
        tail = "".join(f" {counts(f):>{fw[f]}}" for f in fallbacks)
        return f"{repo:<20} {reader:<{rw}} {budget:<{bw}} {served:>7}{tail}"

    header = row("repo", "reader", "budget", "served", lambda f: f)
    lines += [header, "-" * len(header)]
    lines += [row(r["repo"], r["reader"], r["budget"], r["served"],
                  lambda f, r=r: r["fallbacks"].get(f, 0)) for r in rows]
    lines += [row(repo, UNKNOWN, UNKNOWN, UNKNOWN, lambda f: UNKNOWN)
              for repo in sorted(unknown_repos)]
    lines.append("-" * len(header))
    if not rows:
        lines.append("no linear-door line in the window")
    return "\n".join(lines)


def _gh(*args: str) -> subprocess.CompletedProcess:
    # B603/B607: args are program-constructed, shell=False, `gh` via PATH.
    return subprocess.run(  # nosec B603 B607
        ["gh", *args], capture_output=True, text=True, check=False
    )


def _runs_since(repo: str, since: datetime) -> list[dict] | None:
    """The repo's runs created at or after `since`, or None when the repo
    cannot be read (that is UNKNOWN, never an empty list)."""
    p = _gh("run", "list", "-R", repo, "--limit", "100",
            "--json", "databaseId,workflowName,createdAt")
    if p.returncode != 0:
        print(f"{repo}: gh run list failed rc={p.returncode}: "
              f"{p.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        runs = json.loads(p.stdout or "[]")
    except json.JSONDecodeError:
        return None
    out = []
    for run in runs:
        created = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
        if created >= since:
            out.append(run)
    return out


def _run_log(repo: str, run_id: int,
             expired=lambda: False) -> tuple[str | None, str]:
    """The run's full log and "", or None and why there is none to read.

    A nonzero rc — `run <id> is still in progress`, or `log not found` for an
    expired one — is asked again, up to LOG_ATTEMPTS reads with a back-off
    between them; still nonzero after the last, the reason is the last thing
    `gh` said. An EMPTY log — rc 0 with no output, a completed run whose
    every job was skipped — is not asked again, since there was never
    anything to read: it is None and "", and the caller counts it apart.
    `expired` is asked before every read, and raises _DeadlineReached when it
    answers True."""
    p = None
    for attempt in range(LOG_ATTEMPTS):
        if attempt:
            _sleep(LOG_BACKOFF_SECONDS)
        if expired():
            raise _DeadlineReached
        p = _gh("run", "view", str(run_id), "-R", repo, "--log")
        if p.returncode == 0:
            return (p.stdout, "") if p.stdout.strip() else (None, "")
    return None, (p.stderr.strip() or p.stdout.strip() or f"rc={p.returncode}")


def main(argv: list[str] | None = None) -> int:
    started = _monotonic()
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Prints four tables: the spend per (repo, workflow), the reads that "
               "fell back from the read door, the reads sent to Linear directly per "
               "reader and site (`linear-read:` lines), and the door's answers per "
               "reader and bucket (`linear-door:` lines).")
    ap.add_argument("--hours", type=float, default=1.0,
                    help="how far back to read runs (default 1)")
    ap.add_argument("--by-caller", action="store_true",
                    help="split the direct-read table by the function that sent "
                         "each read, one more key column (`caller`)")
    ap.add_argument("--repo", action="append", metavar="SLUG",
                    help="read only this slug of config/repo-map.json (repeatable)")
    ap.add_argument("--workflow", action="append", metavar="NAME",
                    help="fetch the log of only this workflow's runs (repeatable)")
    ap.add_argument("--deadline", type=float, metavar="SECONDS",
                    help="read nothing more once this many seconds have passed, "
                         "and print the tables from what was read")
    args = ap.parse_args(argv)
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    repo_map = load_repo_map()
    wanted = set(args.repo or ())
    missing = sorted(wanted - repo_map.keys())
    if missing:
        print(f"--repo: not a slug of config/repo-map.json: {', '.join(missing)} "
              f"(valid: {', '.join(sorted(repo_map))})", file=sys.stderr)
        return 2
    slugs = sorted(slug for slug in repo_map if not wanted or slug in wanted)
    workflows = set(args.workflow or ())

    def expired() -> bool:
        return (args.deadline is not None
                and _monotonic() - started >= args.deadline)

    observations: list[tuple[str, str, str]] = []
    unknown_repos: list[str] = []
    skipped: list[tuple[str, int]] = []
    empty = 0
    unread: dict[str, int] = {}
    unreached: list[str] = []
    for i, slug in enumerate(slugs):
        repo = repo_map[slug]
        if expired():
            unreached = slugs[i:]
            break
        runs = _runs_since(repo, since)
        if runs is None:
            unknown_repos.append(slug)
            continue
        if workflows:
            runs = [run for run in runs if run["workflowName"] in workflows]
        for j, run in enumerate(runs):
            try:
                log, why = _run_log(repo, run["databaseId"], expired)
            except _DeadlineReached:
                unread[slug] = len(runs) - j
                break
            if log is None and why:
                print(f"{repo}: gh run view {run['databaseId']} failed after "
                      f"{LOG_ATTEMPTS} attempts: {why[:200]}", file=sys.stderr)
                skipped.append((slug, run["databaseId"]))
                continue
            if log is None:
                empty += 1
                continue
            observations.append((slug, run["workflowName"], log))
        if slug in unread:
            unreached = slugs[i + 1:]
            break

    stopped = (args.deadline, unread, unreached) if unread or unreached else None
    # A slug never reached was never looked at: UNKNOWN, as an unreadable one.
    unknown_repos += unreached
    print(render_table(aggregate(observations), unknown_repos,
                       hours=args.hours, skipped=skipped, empty=empty,
                       stopped=stopped))
    print()
    print(render_fallbacks(aggregate_fallbacks(observations), unknown_repos,
                           hours=args.hours))
    print()
    print(render_reads(aggregate_reads(observations, by_caller=args.by_caller),
                       unknown_repos, hours=args.hours))
    print()
    print(render_doors(aggregate_doors(observations), unknown_repos, hours=args.hours))
    return 0


if __name__ == "__main__":
    sys.exit(main())
