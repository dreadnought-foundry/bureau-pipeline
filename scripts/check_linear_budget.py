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

A repo the token cannot read prints UNKNOWN, never 0: a zero that means
"could not look" would hide exactly the repo that is spending. A run whose
log cannot be fetched (still in progress, or expired) is skipped and counted
in the `skipped` note. A line that says `window rolled` or `unknown` is a run
seen with a spend that cannot be known; it counts as a run, not as 0 spent.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_MAP_PATH = Path(__file__).resolve().parent.parent / "config" / "repo-map.json"

# The line linear_ops.budget_line() composes. `gh run view --log` prefixes each
# line with `job\tstep\ttimestamp `, so the match is anchored on the marker,
# not the line start. The arrow is the literal U+2192 the seam prints.
_SPENT_RE = re.compile(r"linear-budget:\s*(\d+)\s*→\s*(\d+)\s*\(spent\s+(\d+)\s+this run")
_SEEN_RE = re.compile(r"linear-budget:")

UNKNOWN = "UNKNOWN"


def load_repo_map() -> dict[str, str]:
    """slug → "owner/repo", read the way validate_card reads it."""
    return json.loads(REPO_MAP_PATH.read_text())


def spent_from_log(log_text: str) -> list[int | None]:
    """One entry per budget line in `log_text`: the spend as an int, or None
    for a line whose spend cannot be known (`window rolled`, `unknown`)."""
    out: list[int | None] = []
    for line in (log_text or "").splitlines():
        if not _SEEN_RE.search(line):
            continue
        m = _SPENT_RE.search(line)
        out.append(int(m.group(3)) if m else None)
    return out


def aggregate(observations) -> list[dict]:
    """Rows per (repo, workflow) from `(repo, workflow, log_text)` triples —
    one triple per run. Sorted by total spent, descending, then by name so
    equal totals print in a stable order."""
    rows: dict[tuple[str, str], dict] = {}
    for repo, workflow, log_text in observations:
        row = rows.setdefault(
            (repo, workflow),
            {"repo": repo, "workflow": workflow, "runs": 0, "total": 0,
             "max": 0, "unknown_lines": 0},
        )
        row["runs"] += 1
        for spent in spent_from_log(log_text):
            if spent is None:
                row["unknown_lines"] += 1
                continue
            row["total"] += spent
            row["max"] = max(row["max"], spent)
    return sorted(rows.values(), key=lambda r: (-r["total"], r["repo"], r["workflow"]))


def render_table(rows: list[dict], unknown_repos: list[str], *,
                 hours: float = 1, skipped: int = 0) -> str:
    """The table, as text: one row per (repo, workflow), UNKNOWN rows for the
    repos the token could not read, a grand total last."""
    header = f"{'repo':<20} {'workflow':<32} {'runs':>5} {'spent':>7} {'max/run':>8}"
    lines = [f"linear budget, last {hours:g}h", header, "-" * len(header)]
    for r in rows:
        note = f"  ({r['unknown_lines']} line(s) unknown/rolled)" if r["unknown_lines"] else ""
        lines.append(
            f"{r['repo']:<20} {r['workflow'][:32]:<32} {r['runs']:>5} "
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
        tail += f"   ({skipped} run(s) skipped: log not available)"
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


def _run_log(repo: str, run_id: int) -> str | None:
    """The run's full log, or None when there is none to read: a nonzero rc
    (still in progress, expired), or an EMPTY log — `gh` answers rc 0 with no
    output for a run whose every job was skipped or has not started, and that
    is not a run that spent anything, so it is skipped rather than counted."""
    p = _gh("run", "view", str(run_id), "-R", repo, "--log")
    return p.stdout if p.returncode == 0 and p.stdout.strip() else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hours", type=float, default=1.0,
                    help="how far back to read runs (default 1)")
    args = ap.parse_args(argv)
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    observations: list[tuple[str, str, str]] = []
    unknown_repos: list[str] = []
    skipped = 0
    for slug, repo in sorted(load_repo_map().items()):
        runs = _runs_since(repo, since)
        if runs is None:
            unknown_repos.append(slug)
            continue
        for run in runs:
            log = _run_log(repo, run["databaseId"])
            if log is None:
                skipped += 1
                continue
            observations.append((slug, run["workflowName"], log))

    print(render_table(aggregate(observations), unknown_repos,
                       hours=args.hours, skipped=skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
