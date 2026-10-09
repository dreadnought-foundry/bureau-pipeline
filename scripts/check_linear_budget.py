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

Never writes anything: `gh run list` and `gh run view --log` are its only
calls. Usage:

    python3 scripts/check_linear_budget.py [--hours N]
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

UNKNOWN = "UNKNOWN"

# How many times a run's log is asked for before it is named unreadable, and
# the pause between two asks (DRE-3242): two pauses, well under half a minute
# per run. `_sleep` is the one seam the back-off goes through, so a test sets
# it to nothing and never sleeps.
LOG_ATTEMPTS = 3
LOG_BACKOFF_SECONDS = 5.0
_sleep = time.sleep


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
                 empty: int = 0) -> str:
    """The table, as text: one row per (repo, workflow), UNKNOWN rows for the
    repos the token could not read, a grand total last. `skipped` is each run
    whose log was still unreadable after every attempt, as `(slug, run id)`,
    and is named in the footer; `empty` counts the runs whose log was empty."""
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
    lines.append(tail)
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


def _run_log(repo: str, run_id: int) -> tuple[str | None, str]:
    """The run's full log and "", or None and why there is none to read.

    A nonzero rc — `run <id> is still in progress`, or `log not found` for an
    expired one — is asked again, up to LOG_ATTEMPTS reads with a back-off
    between them; still nonzero after the last, the reason is the last thing
    `gh` said. An EMPTY log — rc 0 with no output, a completed run whose
    every job was skipped — is not asked again, since there was never
    anything to read: it is None and "", and the caller counts it apart."""
    p = None
    for attempt in range(LOG_ATTEMPTS):
        if attempt:
            _sleep(LOG_BACKOFF_SECONDS)
        p = _gh("run", "view", str(run_id), "-R", repo, "--log")
        if p.returncode == 0:
            return (p.stdout, "") if p.stdout.strip() else (None, "")
    return None, (p.stderr.strip() or p.stdout.strip() or f"rc={p.returncode}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hours", type=float, default=1.0,
                    help="how far back to read runs (default 1)")
    args = ap.parse_args(argv)
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    observations: list[tuple[str, str, str]] = []
    unknown_repos: list[str] = []
    skipped: list[tuple[str, int]] = []
    empty = 0
    for slug, repo in sorted(load_repo_map().items()):
        runs = _runs_since(repo, since)
        if runs is None:
            unknown_repos.append(slug)
            continue
        for run in runs:
            log, why = _run_log(repo, run["databaseId"])
            if log is None and why:
                print(f"{repo}: gh run view {run['databaseId']} failed after "
                      f"{LOG_ATTEMPTS} attempts: {why[:200]}", file=sys.stderr)
                skipped.append((slug, run["databaseId"]))
                continue
            if log is None:
                empty += 1
                continue
            observations.append((slug, run["workflowName"], log))

    print(render_table(aggregate(observations), unknown_repos,
                       hours=args.hours, skipped=skipped, empty=empty))
    return 0


if __name__ == "__main__":
    sys.exit(main())
