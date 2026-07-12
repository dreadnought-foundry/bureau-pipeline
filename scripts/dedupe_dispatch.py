#!/usr/bin/env python3
"""Duplicate-dispatch guard for agent-task (DRE-2057).

A single card's Todo transition sometimes produces TWO agent-execute
dispatches ~60s apart (webhook double-fire around create/state-set, or a
relay retry). The stub's per-card concurrency group serializes them —
cancel-in-progress stays false on purpose (DRE-2032) — so the dup doesn't
die, it QUEUES and builds the card AGAIN after the first run finishes:
twin PRs, one hand-closed after the other merges. Five known occurrences
(bureau-pipeline PRs #76, #83, #90, #97, #103); the pipeline must be
idempotent at the consumer regardless of the dup's source.

The decision (either condition skips; both unreadable → proceed):

(a) LIVE RUN — the card's newest 🧠 model-attempt heartbeat maps the card
    to its Actions run (the DRE-2032 contract reconcile.agent_run_alive
    reads; agent-task posts it at Card → In Progress). If that run is NOT
    this run and GitHub says it has not completed (queued / in_progress /
    waiting / …), another attempt is live — skip. Our own run id never
    counts: a job re-run reuses its id and must not skip on its own
    earlier heartbeat.
(b) OPEN AGENT PR — an open PR whose head branch is agent/<DRE-N>-* (the
    identifier \\b-anchored, the DRE-1034 vs DRE-10345 near-miss guard from
    reconcile.pr_for) means the card is already built and in review — a
    second build could only produce the twin. Only OPEN PRs gate: a
    re-dispatch after the PR closed/merged is the legitimate rebuild case
    and proceeds.

FAIL-OPEN by design, the opposite of the merge gate: this guard gates a
BUILD, not a merge. A missed skip (unreadable status, PR-list blip) is
exactly the pre-DRE-2057 status quo — at worst a twin PR; a false skip
would strand a healthy card with no run at all. So unverifiable data
always proceeds, and the guard itself exits 0 always.

Contract with agent-task.yml (the validate_card.py gate convention):
  gate <DRE-N>   exit 0 always; prints `skip=true|false` and `reason=...`
                 to $GITHUB_OUTPUT (and stdout) so every downstream step
                 can skip a duplicate. On skip, posts a 🤖 receipt comment
                 to the card (machine-marker prefix — reconcile's blocker
                 gate reads any non-machine comment as a human reply);
                 reporting failures never flip the decision.
  env: GITHUB_RUN_ID (this run), REPO or GITHUB_REPOSITORY, GH_TOKEN,
       LINEAR_API_KEY.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

import linear_ops

# The heartbeat contract, shared with reconcile.py (RUN_MARKER / _RUN_ID) —
# tests/test_dedupe_dispatch.py pins byte-parity so drift can't blind either
# reader.
RUN_MARKER = "🧠 model-attempt:"
_RUN_ID = re.compile(r"/actions/runs/(\d+)\b")


@dataclass
class Decision:
    skip: bool
    reason: str


def heartbeat_run_id(comment_bodies: list) -> Optional[str]:
    """Run id from the card's NEWEST 🧠 model-attempt heartbeat (input is
    oldest→newest, per linear_ops.comment_bodies). None when no attempt ever
    posted one, or the newest carries no run URL (legacy heartbeat) — nothing
    verifiable to skip on."""
    for body in reversed(comment_bodies):
        b = (body or "").lstrip()
        if not b.startswith(RUN_MARKER):
            continue
        m = _RUN_ID.search(b)
        return m.group(1) if m else None
    return None


def open_agent_pr(open_prs: list, identifier: str) -> Optional[dict]:
    """The card's open agent PR: head branch agent/<DRE-N>… with the
    identifier \\b-anchored so DRE-205 never matches agent/DRE-2053-*.
    Repair/* and other non-agent branches never gate a build."""
    rx = re.compile(rf"^agent/{re.escape(identifier)}\b")
    matches = [pr for pr in open_prs if rx.match(pr.get("headRefName") or "")]
    return max(matches, key=lambda pr: pr["number"]) if matches else None


def decide(
    identifier: str,
    own_run_id: str,
    comment_bodies: list,
    run_status: Callable[[str], str],
    open_prs: list,
) -> Decision:
    """The skip decision. `run_status` answers a run id with GitHub's
    .status ('' when unreadable — proceeds, fail-open); it is consulted only
    when the newest heartbeat names a run that is not this one. The PR check
    runs first: an open agent PR is the twin vector itself, no API status
    needed."""
    pr = open_agent_pr(open_prs, identifier)
    if pr:
        return Decision(
            True,
            f"open agent PR #{pr['number']} ({pr['headRefName']}) already "
            f"exists for {identifier} — duplicate dispatch",
        )
    run_id = heartbeat_run_id(comment_bodies)
    if run_id and run_id != str(own_run_id):
        status = run_status(run_id)
        if status and status != "completed":
            return Decision(
                True,
                f"agent-task run {run_id} for {identifier} is {status} — "
                "duplicate dispatch",
            )
    return Decision(
        False, f"no live run or open agent PR for {identifier} — proceeding"
    )


# --- GitHub-touching CLI (thin wrapper; the logic above is pure) --------------


def _repo() -> str:
    return os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY", "")


def _run_status(run_id: str) -> str:
    """GitHub's .status for the run — '' on any failure (fail-open)."""
    p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", "api", f"repos/{_repo()}/actions/runs/{run_id}", "--jq", ".status"],
        capture_output=True, text=True, check=False,
    )
    if p.returncode != 0:
        print(f"run-status read failed (rc={p.returncode}) — proceeding "
              "on fail-open", file=sys.stderr)
        return ""
    return p.stdout.strip()


def _open_prs() -> list:
    """All open PRs (number + head branch). A plain list, not a search query:
    the twin PR may be seconds old and search-index lag would hide exactly
    the PR this guard exists to see. [] on any failure (fail-open — a blip
    parsed as no-PR reproduces the status quo, never strands the card)."""
    p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", "pr", "list", "--repo", _repo(), "--state", "open",
         "--limit", "100", "--json", "number,headRefName"],
        capture_output=True, text=True, check=False,
    )
    if p.returncode != 0:
        print(f"open-PR read failed (rc={p.returncode}) — proceeding "
              "on fail-open", file=sys.stderr)
        return []
    try:
        prs = json.loads(p.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return prs if isinstance(prs, list) else []


def _emit(skip: bool, reason: str) -> None:
    lines = [f"skip={'true' if skip else 'false'}", f"reason={reason}"]
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write("\n".join(lines) + "\n")
    for line in lines:
        print(line)


def cmd_gate(identifier: str) -> None:
    try:
        bodies = linear_ops.comment_bodies(identifier)
    except Exception as e:  # noqa: BLE001 — an unreadable card proceeds
        print(f"comment read failed ({e}) — proceeding on fail-open",
              file=sys.stderr)
        bodies = []
    decision = decide(
        identifier,
        os.environ.get("GITHUB_RUN_ID", ""),
        bodies,
        _run_status,
        _open_prs(),
    )
    if decision.skip:
        run_url = (
            f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}"
            f"/{_repo()}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
        )
        try:
            linear_ops.cmd_comment(
                identifier,
                f"🤖 Duplicate dispatch skipped: {decision.reason}. This run "
                f"created no branch or PR. Run: {run_url}",
            )
        except Exception as e:  # noqa: BLE001 — reporting never blocks the skip
            print(f"skip receipt failed ({e}) — skipping anyway", file=sys.stderr)
    _emit(decision.skip, decision.reason)


def main(argv: list) -> int:
    if len(argv) == 3 and argv[1] == "gate":
        cmd_gate(argv[2])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
