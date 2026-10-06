#!/usr/bin/env python3
"""Is the run on this proof card alive, dead, never started, or somebody
else's? (DRE-5922)

A dispatch receipt on a card says a run was asked for; it does not say what
became of it. The proof dispatcher needs one answer it can act on, covering
the run that died after its first heartbeat (turn cap, job timeout,
rate-limit exit, crash), the one that never started, and the card a person's
helper is already working. This module is that reader: the card's comments
plus three GitHub facts through an injected `read(path)` (`gh api <path>`,
raising on failure), answered as one of seven states, each with its evidence
as printable lines.

THE NEWEST RECEIPT IS THE ANCHOR. A receipt is the dispatcher's line,

    🔬 proof-run: dispatched a proof run at <PT time> — <reason> (<count>)

posted by the pipeline's own key; `<count>` is `dispatch <n> of 2`,
`re-run <n> of 2` or `after the CEO's answer`. Comments by anyone else never
count as receipts or heartbeats (the authorship rule `standards/plan-critic.md`
applies to round records).

  none           no receipt, no open pull request or remote branch in the
                 card's family `agent/DRE-<n>-`, no `⏳`/`🧠 model-attempt`
                 line in the last 24 hours. Nobody is on it.
  someone-else   no receipt, but one of those signs — the line names it.
  running        the newest `🧠 model-attempt` after the receipt names a run
                 GitHub has not completed, or the receipt is younger than 30
                 minutes and nothing has followed it (a queued run).
  never-started  the receipt is 30 minutes old or older, and no
                 `🧠 model-attempt` follows it.
  finished       a `⏳ 5/5` line follows the receipt: the run opened or
                 amended the record, and the gates own the card.
  dead           the run the newest `🧠 model-attempt` names is `completed`
                 and no `⏳ 5/5` followed — whatever ended it.
  unknown        a read failed. Never `none`: the dispatcher treats it as
                 `running`.

`record_pr` — the pull request on `agent/DRE-<n>-proof-record`, open or
merged — is a fact BESIDE the state and never folded into it: a re-run that
died on a card whose record is open still reads `dead`. `dispatches` is the
first-run budget only: receipts whose reason opens `first proof run` or
`second dispatch`.

CLI:
    proof_run_state.py check --repo OWNER/NAME --card DRE-N

reads the card's thread through `linear_ops` and prints
`proof-run-state: <state> — …` per line; exit 0, or 2 for unknown.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Callable, NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proof_release import gh_read  # noqa: E402

#: Opens every line the CLI prints.
TAG = "proof-run-state"

STATES = ("none", "someone-else", "running", "never-started", "dead",
          "finished", "unknown")

RECEIPT_MARKER = "🔬 proof-run:"
RUN_MARKER = "🧠 model-attempt:"
HEARTBEAT = "⏳"
FINISHED = re.compile(r"^⏳ 5/5\b")

_RECEIPT = re.compile(
    r"^🔬 proof-run: dispatched a proof run at (?P<at>.+?) — (?P<reason>.+) "
    r"\((?P<count>dispatch \d+ of 2|re-run \d+ of 2|after the CEO's answer)\)$")
_RUN_ID = re.compile(r"Run: \S*/actions/runs/(\d+)\b")

#: The reasons that spend the first-run budget (DRE-5926). A re-run after the
#: critic's findings and the return after the CEO's answer never do.
FIRST_RUN = ("first proof run", "second dispatch")

#: How long a receipt nothing has followed is a queued run, not a dead ask.
QUEUED = timedelta(minutes=30)
#: How recent a heartbeat must be to say somebody is on an unreceipted card.
FRESH = timedelta(hours=24)


class Receipt(NamedTuple):
    reason: str
    count: str
    at: str


class State(NamedTuple):
    state: str              # one of STATES
    lines: list
    dispatches: int
    run_id: str | None
    receipts: list
    record_pr: dict | None


def _when(stamp) -> datetime | None:
    try:
        return datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _clock(when: datetime | None) -> str:
    """`09:14 PT` — every time a person reads is Pacific."""
    if when is None:
        return "an unknown time"
    try:
        from zoneinfo import ZoneInfo

        return when.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%H:%M PT")
    except Exception:  # noqa: BLE001 — no tz database: say UTC, never a wrong PT
        return when.astimezone(timezone.utc).strftime("%H:%M UTC")


def _why(error) -> str:
    return " ".join(str(error or "").split())[:200] or "no reason given"


def _body(comment: dict) -> str:
    return (comment.get("body") or "").strip()


def receipt(body: str) -> Receipt | None:
    """The dispatcher's receipt line, parsed — or None."""
    first = (body or "").strip().split("\n", 1)[0].strip()
    found = _RECEIPT.match(first)
    if not found:
        return None
    return Receipt(found["reason"], found["count"], found["at"])


def run_id(body: str) -> str | None:
    """The run a `🧠 model-attempt` line names, or None."""
    found = _RUN_ID.search(body or "")
    return found.group(1) if found else None


def _record_pr(repo: str, identifier: str, read) -> dict | None:
    """The pull request on `agent/DRE-<n>-proof-record`: the open one, else
    the merged one, else None."""
    owner = repo.split("/", 1)[0]
    pulls = read(f"repos/{repo}/pulls?head={owner}:agent/{identifier}-"
                 "proof-record&state=all&per_page=100") or []
    open_ = [p for p in pulls if p.get("state") == "open"]
    merged = [p for p in pulls if p.get("merged_at")]
    chosen = (open_ or merged or [None])[0]
    if chosen is None:
        return None
    return {"number": chosen.get("number"),
            "state": "open" if chosen in open_ else "merged",
            "url": chosen.get("html_url"),
            "head": (chosen.get("head") or {}).get("ref")}


def _record_clause(record: dict | None) -> str:
    return (f"; the record pull request #{record['number']} is {record['state']}"
            if record else "")


def _unknown(what: str, error, receipts, dispatches, record=None) -> State:
    return State("unknown", [f"unknown — {what}: {_why(error)}; read as "
                             "running until it can be read"],
                 dispatches, None, receipts, record)


def _unclaimed(repo, identifier, own, now, read, receipts, record) -> State:
    """No receipt: is anybody else on the card?"""
    family = f"agent/{identifier}-"
    try:
        pulls = [p for p in read(f"repos/{repo}/pulls?state=open&per_page=100") or []
                 if str((p.get("head") or {}).get("ref") or "").startswith(family)]
        branches = [str(r.get("ref") or "").removeprefix("refs/heads/")
                    for r in read(f"repos/{repo}/git/matching-refs/heads/{family}") or []]
    except Exception as error:  # noqa: BLE001 — never read as nobody
        return _unknown(f"the {family} branches and pull requests on {repo} "
                        "could not be read", error, receipts, 0, record)
    lines, run = [], None
    named = set()
    for pull in pulls:
        ref = pull["head"]["ref"]
        named.add(ref)
        lines.append(f"someone-else — pull request #{pull.get('number')} "
                     f"({ref}) is open and no proof-run receipt names it")
    for branch in branches:
        if branch.startswith(family) and branch not in named:
            lines.append(f"someone-else — branch {branch} exists on the remote "
                         "and no proof-run receipt names it")
    fresh = [c for c in own
             if _body(c).startswith((HEARTBEAT, RUN_MARKER))
             and (when := _when(c.get("createdAt"))) and now - when < FRESH]
    if fresh:
        newest = fresh[-1]
        kind = ("🧠 model-attempt" if _body(newest).startswith(RUN_MARKER)
                else "⏳ heartbeat")
        lines.append(f"someone-else — a {kind} line was posted at "
                     f"{_clock(_when(newest.get('createdAt')))}, within 24 hours, "
                     "and no proof-run receipt names it")
        attempts = [run_id(_body(c)) for c in fresh
                    if _body(c).startswith(RUN_MARKER)]
        run = next((r for r in reversed(attempts) if r), None)
    if lines:
        return State("someone-else", lines, 0, run, receipts, record)
    return State("none", [f"none — no proof-run receipt, no open pull request "
                          f"or branch in {family}, and no heartbeat from the "
                          "pipeline in the last 24 hours"],
                 0, None, receipts, record)


def reading(repo: str, identifier: str, comments: list, viewer: str | None, *,
            read: Callable = gh_read, now: datetime | None = None) -> State:
    """The state of the run on `identifier`'s proof card."""
    now = now or datetime.now(timezone.utc)
    if not viewer:
        return _unknown("the pipeline's own Linear user could not be read, so "
                        "its receipts cannot be told from a person's comments",
                        "no viewer", [], 0)
    own = [c for c in comments or []
           if ((c.get("user") or {}).get("id")) == viewer]
    anchored = [(i, r) for i, c in enumerate(own)
                if (r := receipt(_body(c))) is not None]
    receipts = [r for _, r in anchored]
    dispatches = sum(1 for r in receipts if r.reason.startswith(FIRST_RUN))
    try:
        record = _record_pr(repo, identifier, read)
    except Exception as error:  # noqa: BLE001
        return _unknown(f"the proof-record pull request on {repo} could not be "
                        "read", error, receipts, dispatches)
    if not anchored:
        return _unclaimed(repo, identifier, own, now, read, receipts, record)

    index, newest = anchored[-1]
    after = [_body(c) for c in own[index + 1:]]
    attempts = [body for body in after if body.startswith(RUN_MARKER)]
    run = run_id(attempts[-1]) if attempts else None

    def state(name: str, line: str) -> State:
        return State(name, [line], dispatches, run, receipts, record)

    if any(FINISHED.match(body) for body in after):
        return state("finished", f"finished — a ⏳ 5/5 line followed the "
                                 f"proof-run receipt of {newest.at}: the run "
                                 f"opened or amended the record"
                                 f"{_record_clause(record)}, and the gates own "
                                 "the card")
    if attempts and not run:
        return _unknown(f"the 🧠 model-attempt line after the proof-run receipt "
                        f"of {newest.at} names no run", "no Run: link",
                        receipts, dispatches, record)
    if not attempts:
        posted = _when(own[index].get("createdAt"))
        if posted is None:
            return _unknown(f"the proof-run receipt of {newest.at} has no time",
                            own[index].get("createdAt"), receipts, dispatches,
                            record)
        minutes = int((now - posted).total_seconds() // 60)
        if now - posted < QUEUED:
            return state("running", f"running — the proof-run receipt of "
                                    f"{newest.at} is {minutes} minutes old and "
                                    "no 🧠 model-attempt has followed it yet: "
                                    "a queued run")
        return state("never-started", f"never-started — the proof-run receipt "
                                      f"of {newest.at} is {minutes} minutes old "
                                      "and no 🧠 model-attempt has followed it")
    try:
        answer = read(f"repos/{repo}/actions/runs/{run}") or {}
    except Exception as error:  # noqa: BLE001
        return _unknown(f"run {run} could not be read", error, receipts,
                        dispatches, record)._replace(run_id=run)
    status = answer.get("status")
    if not status:
        return _unknown(f"run {run} answered no status", repr(answer)[:80],
                        receipts, dispatches, record)._replace(run_id=run)
    if status != "completed":
        return state("running", f"running — run {run} is {status}, started "
                                f"after the proof-run receipt of {newest.at}")
    return state("dead", f"dead — run {run} ended {answer.get('conclusion') or 'with no conclusion'} at "
                         f"{_clock(_when(answer.get('updated_at')))} with no "
                         f"record{_record_clause(record)}")


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _linear_thread(card: str):
    import linear_ops

    return linear_ops._thread_and_viewer(card, "body", "user", "createdAt",
                                         whole=True)


def main(argv=None, *, read: Callable | None = None,
         thread: Callable | None = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="read the state of the proof run")
    check.add_argument("--repo", required=True)
    check.add_argument("--card", required=True)
    args = parser.parse_args(argv)
    try:
        comments, viewer = (thread or _linear_thread)(args.card)
    except Exception as error:  # noqa: BLE001 — an unread thread is unknown
        got = _unknown(f"the thread on {args.card} could not be read", error,
                       [], 0)
    else:
        got = reading(args.repo, args.card, comments, viewer,
                      read=read or gh_read, now=now)
    for line in got.lines:
        print(f"{TAG}: {line}")
    return 2 if got.state == "unknown" else 0


if __name__ == "__main__":
    sys.exit(main())
