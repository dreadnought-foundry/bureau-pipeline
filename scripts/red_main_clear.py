#!/usr/bin/env python3
"""Is main green again? Red-main repair's all-clear, decided apart from the YAML (DRE-7454).

Red-main repair raises two CRITICAL console alerts — `red_main:<repo>:budget_exhausted`
when its two attempts at a failing commit are spent, and
`red_main:<repo>:needs_decision` when its agent paused for a person — and it
is the one writer that closes them. The `clear-notices` job in
`.github/workflows/red-main-repair.yml` runs when a watched workflow's run on
the default branch concludes `success`, lists every run on that commit with
`gh run list --commit <sha>`, and asks this script two things.

## decide — go only when nothing on the commit is red or unfinished

`judge` reads the listing and answers go only when every run that judges the
commit has completed, and none concluded `failure`, `timed_out` or
`startup_failure` (`RED`). So a green run of one workflow beside a red or
unfinished one never sends a false all-clear: the last watched workflow to
finish green is the one whose run sends it.

Two kinds of run are not waited on, because neither judges the commit:

* this job's own run, still in progress while it reads the listing
  (`--self-run-id`);
* any run started by `workflow_run` — this repair rail, the medic and every
  other listener. GitHub starts those on the default branch's head sha each
  time a watched run completes, the other watched workflow's Red-Main Repair
  run among them, so waiting on them would never send an all-clear.
  `scripts/check_workflow_watchers.py` keeps the same line: a `workflow_run`
  workflow is never one that validates a commit.

A listing that cannot be read, or holds no run that judges the commit, is a
stop. A missed all-clear costs one more green run; a false one tells the CEO
main is green while it is red.

The commit must also still be the default branch's tip (`judge_tip`). A late
or re-run green run on an older main commit — the medic's retry-once among
them — says nothing about the newest one, which may be the commit that is
red, so a sha that is no longer the tip is a `stale-sha` stop, and a tip that
could not be read is a `tip-unreadable` stop. The next green run on the tip
sends the all-clear.

## recovery-body — the all-clear copy

The contract's red-main copy, with the green run's workflow name, the time it
completed in PT and its URL:

    <workflow name> passed on the default branch of <repo> at <HH:MM PT>, so
    the red main this alert reported has cleared. Run: <run URL>

## Usage

    red_main_clear.py decide --runs-file <gh run list JSON> [--self-run-id <id>] \\
        [--head-sha <green sha> --tip-sha <default branch tip>]
    red_main_clear.py recovery-body --workflow-name <w> --repo <slug> \\
        --run-url <u> [--at <ISO 8601>] --out <file>

`decide` prints `go=true|false` and `reason=<reason>` for `$GITHUB_OUTPUT`,
and one plain line saying why on stderr; it exits 0 on every verdict.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

#: The conclusions that mean the commit is red.
RED = frozenset({"failure", "timed_out", "startup_failure"})
#: The event of a run started by another run's completion — never a run that
#: judges the commit.
LISTENER_EVENT = "workflow_run"


@dataclass(frozen=True)
class Verdict:
    go: bool
    # all-green | red | unfinished | no-runs | unreadable | stale-sha | tip-unreadable
    reason: str
    detail: str


def _name(run: dict) -> str:
    return str(run.get("workflowName") or run.get("name") or "a workflow")


def judge(runs: list[dict], *, self_run_id: str | int | None = None) -> Verdict:
    """Go when every run that judges the commit completed and none is red."""
    me = str(self_run_id) if self_run_id not in (None, "") else None
    judged = [r for r in runs
              if str(r.get("databaseId")) != me
              and r.get("event") != LISTENER_EVENT]
    if not judged:
        return Verdict(False, "no-runs", "no run on this commit judges it")
    red = [r for r in judged
           if r.get("status") == "completed" and r.get("conclusion") in RED]
    if red:
        return Verdict(False, "red", "; ".join(
            f"{_name(r)} concluded {r.get('conclusion')}" for r in red))
    unfinished = [r for r in judged if r.get("status") != "completed"]
    if unfinished:
        return Verdict(False, "unfinished", "; ".join(
            f"{_name(r)} is {r.get('status') or 'in an unknown state'}"
            for r in unfinished))
    return Verdict(True, "all-green",
                   f"{len(judged)} run(s) on this commit, none red or unfinished")


def judge_tip(head_sha: str, tip_sha: str | None) -> Verdict | None:
    """A stop when the green commit is no longer the default branch's tip, or
    the tip could not be read; None when it is still the tip."""
    tip = (tip_sha or "").strip()
    if not tip:
        return Verdict(False, "tip-unreadable",
                       "the default branch's tip could not be read")
    if tip != head_sha.strip():
        return Verdict(False, "stale-sha",
                       f"{head_sha[:7]} is no longer the default branch's tip "
                       f"({tip[:7]} is), so its green run says nothing about main")
    return None


def judge_text(raw: str, *, self_run_id: str | int | None = None) -> Verdict:
    """`judge` over the text `gh run list --json` wrote, or a stop."""
    try:
        runs = json.loads(raw)
    except ValueError:
        runs = None
    if not isinstance(runs, list) or not all(isinstance(r, dict) for r in runs):
        return Verdict(False, "unreadable", "the run listing could not be read")
    return judge(runs, self_run_id=self_run_id)


def pt_clock(when: datetime) -> str:
    """`14:05 PT` — a person reads Pacific time. With no tz database, UTC said
    out loud rather than a wrong PT."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415
        return when.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%H:%M PT")
    except Exception:  # noqa: BLE001 — no tz database: UTC, never a wrong PT
        return when.astimezone(timezone.utc).strftime("%H:%M UTC")


def recovery_body(*, workflow: str, repo: str, run_url: str, at: datetime) -> str:
    return (f"{workflow} passed on the default branch of {repo} at {pt_clock(at)}, "
            f"so the red main this alert reported has cleared. Run: {run_url}")


def _when(value: str | None) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide", help="go=true when main is green on this commit")
    d.add_argument("--runs-file", required=True)
    d.add_argument("--self-run-id")
    d.add_argument("--head-sha", help="the green run's commit")
    d.add_argument("--tip-sha", help="the default branch's tip; empty = unread")
    r = sub.add_parser("recovery-body", help="write the all-clear copy")
    r.add_argument("--workflow-name", required=True)
    r.add_argument("--repo", required=True)
    r.add_argument("--run-url", required=True)
    r.add_argument("--at", help="when the green run completed (default: now)")
    r.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "decide":
        try:
            with open(args.runs_file, encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            raw = ""
        verdict = None
        if args.head_sha is not None:
            verdict = judge_tip(args.head_sha, args.tip_sha)
        if verdict is None:
            verdict = judge_text(raw, self_run_id=args.self_run_id)
        print(f"go={'true' if verdict.go else 'false'}")
        print(f"reason={verdict.reason}")
        word = "main is green" if verdict.go else "no all-clear"
        print(f"red-main-clear: {word} ({verdict.reason}) — {verdict.detail}",
              file=sys.stderr)
        return 0

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(recovery_body(workflow=args.workflow_name, repo=args.repo,
                              run_url=args.run_url, at=_when(args.at)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
