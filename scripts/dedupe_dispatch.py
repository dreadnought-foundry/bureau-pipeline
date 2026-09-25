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

The decision (any condition skips; all unreadable → proceed):

(a) LIVE RUN — the card's newest 🧠 model-attempt heartbeat maps the card
    to its Actions run (the DRE-2032 contract reconcile.agent_run_alive
    reads; agent-task posts it at Card → In Progress). If that run is NOT
    this run and GitHub says it has not completed (queued / in_progress /
    waiting / …), another attempt is live — skip. Our own run id never
    counts: a job re-run reuses its id and must not skip on its own
    earlier heartbeat.
(b) THE WORK ALREADY SHIPPED — the card's agent PR (head branch
    agent/<DRE-N>-*, the identifier \\b-anchored, the DRE-1034 vs DRE-10345
    near-miss guard from reconcile.pr_for) is OPEN or MERGED. Open means
    the card is built and in review and a second build could only produce
    the twin. MERGED means it is built and landed, and a second build is
    strictly worse than the twin: the branch it pushes to is a merged
    branch, so the work goes nowhere anybody will read it (DRE-4830, below).
    A CLOSED-unmerged PR is an abandoned attempt and still rebuilds — the
    same OPEN-or-MERGED line card_pr.has_work_pr draws for DRE-2316.
(c) THE CARD IS PAST BUILDING — the card's own lane is the review lane or
    Done. The lanes are sliced from the flow at the review lane rather than
    listed, so a board rename is a change to the contract file and not to
    this guard (DRE-2726).

## WHY (b) GREW A SECOND HALF (DRE-4830, portico, 2026-09-24)

Seven portico cards were promoted to Todo at 14:59 PT and dispatched. The
mini was saturated — 78 jobs waiting at 17:00 — so every run sat `queued`,
posted no heartbeat, and the reconcile sweep's Todo branch read "no run
receipt" as "no run" and re-dispatched six of them sixteen minutes later.
That writer is fixed at its own end (`reconcile.build_run_refusal`); this
is the consumer's half, and it is what catches whatever produces the next
duplicate.

Five of those duplicates skipped here on the OPEN half, hours later. The
sixth did not: DRE-4518's run 36066445856 started at 17:44, after
DRE-4518's PR #700 had merged at 17:24, and this guard's only PR condition
was "is one OPEN" — so it proceeded and rebuilt the same three tools from
scratch onto the merged branch, commits 31eda89a and e4bcd37b. That is the
stranded-commit card DRE-4828, cancelled as a duplicate.

"Closed/merged twin PRs are invisible by design" was the rule until that
happened, and the reasoning was sound for CLOSED and wrong for MERGED: a
closed PR means the attempt was abandoned and the card still owes work,
while a merged one means the card owes nothing at all.

FAIL-OPEN by design, the opposite of the merge gate: this guard gates a
BUILD, not a merge. A missed skip (unreadable status, PR-list blip) is
exactly the pre-DRE-2057 status quo — at worst a twin PR; a false skip
would strand a healthy card with no run at all. So unverifiable data
always proceeds, and the guard itself exits 0 always.

## THE SAME CLASS, ON THE PLANNER (DRE-3409, seed incident DRE-3244)

`plan-gate` is this guard reused for `plan.yml`, and it exists because the
planner rail hit the identical defect with a worse blast radius. On
2026-09-08 DRE-3244 ran the planner FIVE times in 42 minutes and the epic
was pulled out of `Green Light` back into `Planning` eight times, all by the
pipeline's own Linear identity, with no critic and no person sending it
back — the thread carries all eight moves and all five runs:
https://linear.app/dreadnoughtfoundry/issue/DRE-3244

The mover was `plan.yml`'s own `Route — plan or activate` step
(`linear_ops.py state "$EPIC" "Planning"`), running inside a run that had
been QUEUED for a quarter of an hour behind the run ahead of it:

    21:38:22  Green Light → Planning   ← the move
    21:38:27  run 34281711446 created  (the relay, on that Planning entry)
    21:52:45  run 34279893974 completes — the epic is in Green Light
    21:52:50  run 34281711446's job STARTS (14 minutes queued)
    21:53:00  `Route — plan or activate`, FROM="planning"
    21:53:02  `DRE-3244 → Planning`    ← plan.yml, and the loop's next turn

Every queued run undid the `Green Light` the run ahead of it had written,
and the relay turned that `Planning` entry into yet another dispatch. The
loop is self-sustaining and only a person killed it.

TWO CONDITIONS, either of which refuses (both unreadable → proceed):

(c) QUEUED BEHIND A LIVE RUN — another run of this same workflow, on this
    same card, was already in flight when THIS dispatch was created. Read
    against the dispatch's own creation time, not against the clock at
    start: `cancel-in-progress: false` means a queued run only starts once
    the run ahead of it has finished, so "is one live right now" sees
    nothing at exactly the moment that matters. Runs are matched to the
    card by their job name, which plan.yml sets to `bureau-card: <DRE-N>`
    (DRE-3223).
(d) THE LANE MOVED ON — the dispatch names the lane the card ENTERED
    (`client_payload.trigger_state`); if the card is somewhere else by the
    time the run gets a runner, the entry it was fired for has already been
    served. This is the backstop, and it is what keeps an epic a planner run
    left in `Green Light` there.

Contract with agent-task.yml / plan.yml (the validate_card.py gate
convention):
  gate <DRE-N>        exit 0 always; prints `skip=true|false` and
                      `reason=...` to $GITHUB_OUTPUT (and stdout) so every
                      downstream step can skip a duplicate. On skip, posts a
                      🤖 receipt comment to the card (machine-marker prefix —
                      reconcile's blocker gate reads any non-machine comment
                      as a human reply); reporting failures never flip the
                      decision.
  plan-gate <DRE-N>   the same, for a planner dispatch, on (c)/(d) above.
                      Reads the trigger lane from $TRIGGER_STATE, and from
                      $SENT_BY_RUN the planner run that asked for this one,
                      which is never its duplicate (DRE-4573).
  env: GITHUB_RUN_ID (this run), REPO or GITHUB_REPOSITORY, GH_TOKEN,
       LINEAR_API_KEY; TRIGGER_STATE and SENT_BY_RUN for `plan-gate`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import card_pr  # ONE line between "abandoned" and "shipped" (DRE-2316)
import lane_contract
import lane_scope
import linear_ops

# The heartbeat contract, shared with reconcile.py (RUN_MARKER / _RUN_ID) —
# tests/test_dedupe_dispatch.py pins byte-parity so drift can't blind either
# reader.
RUN_MARKER = "🧠 model-attempt:"
_RUN_ID = re.compile(r"/actions/runs/(\d+)\b")

#: The lanes whose occupancy means this card is past being built: the review
#: lane and everything after it. SLICED from the flow at the review lane, never
#: enumerated — a lane's position is part of what the lane IS (DRE-2726), so a
#: board rename is one edit in `config/lane-contract.json` and none here.
SHIPPED_LANES = lane_scope.LANE_FLOW[
    lane_scope.LANE_FLOW.index(lane_contract.lane("In Review")["name"]):
]


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


def agent_pr(prs: list, identifier: str) -> Optional[dict]:
    """The card's newest agent PR, WHATEVER its state: head branch
    agent/<DRE-N>… with the identifier \\b-anchored so DRE-205 never matches
    agent/DRE-2053-*. Repair/* and other non-agent branches never gate a build.

    The state is the caller's to read (`card_pr.pr_state`): since DRE-4830 OPEN
    and MERGED both refuse and CLOSED still rebuilds, so a list filtered to open
    PRs before it got here would hide half the answer."""
    rx = re.compile(rf"^agent/{re.escape(identifier)}\b")
    matches = [pr for pr in prs if rx.match(pr.get("headRefName") or "")]
    return max(matches, key=lambda pr: pr["number"]) if matches else None


def shipped_lane(card_lane: str) -> str:
    """The contract's name for `card_lane` when the card is past being built —
    the review lane or Done — else ''. An unreadable lane is '' (fail-open), and
    the name is resolved through the contract's rename aliases so a board
    mid-rename does not read as a card still in Todo."""
    name = _canonical_lane(card_lane)
    return name if name in SHIPPED_LANES else ""


def decide(
    identifier: str,
    own_run_id: str,
    comment_bodies: list,
    run_status: Callable[[str], str],
    prs: list,
    card_lane: str = "",
) -> Decision:
    """The skip decision. `run_status` answers a run id with GitHub's
    .status ('' when unreadable — proceeds, fail-open); it is consulted only
    when the newest heartbeat names a run that is not this one.

    `prs` is the card's pull requests in EVERY state (`_card_prs`), and
    `card_lane` the card's lane as it is now ('' when unreadable). Both of the
    cheap, already-read conditions run before the API status: a card whose work
    has shipped needs no run status to be refused.
    """
    lane = shipped_lane(card_lane)
    if lane:
        return Decision(
            True,
            f"{identifier} is in {lane} — this card is past being built, so a "
            "build run could only rebuild work that has already shipped — "
            "duplicate dispatch",
        )
    pr = agent_pr(prs, identifier)
    state = card_pr.pr_state(pr)
    if state == card_pr.OPEN:
        return Decision(
            True,
            f"open agent PR #{pr['number']} ({pr['headRefName']}) already "
            f"exists for {identifier} — duplicate dispatch",
        )
    if state == card_pr.MERGED:
        return Decision(
            True,
            f"agent PR #{pr['number']} ({pr['headRefName']}) for {identifier} "
            "has already MERGED — a second build would push to a merged branch "
            "and strand there (DRE-4828) — duplicate dispatch",
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
        False,
        f"no live run, no open or merged agent PR, and {identifier} is not past "
        "being built — proceeding",
    )


# --- the planner's duplicate guard (DRE-3409; pure core) ----------------------

# plan.yml names its job after the card (DRE-3223), and agent-task.yml does too
# since DRE-4830. Through a stub the jobs API reports it as
# `call / bureau-card: DRE-3244`, so the identifier is matched \\b-anchored
# inside the name — the DRE-1034 vs DRE-10345 near-miss guard again. ONE parse of
# the convention, for every reader of it: `reconcile.build_run_refusal` asks this
# module rather than growing a regex of its own.
PLAN_JOB_MARKER = "bureau-card:"


def _moment(stamp: str) -> Optional[datetime]:
    """A GitHub ISO-8601 timestamp, or None when it is not one. Unreadable
    never means 'now' and never means 'forever' — it means the caller has
    nothing to decide on, and the caller fails open."""
    try:
        return datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def in_flight_when_dispatched(runs: list, own_run_id: str,
                              own_created_at: str) -> list:
    """The other runs on this card that were ALREADY GOING when this dispatch
    was created — the question a start-time liveness check cannot answer,
    because a queued run only starts once the run ahead of it has finished.

    `runs` are this workflow's runs for THIS card: dicts with `id`,
    `created_at`, `updated_at` and `status`. A run counts when it was created
    no later than this one and had not finished by the time this one arrived.
    Ties on creation second are broken on run id — the lower one proceeds and
    the higher refuses, so exactly one of a double-fired pair survives.
    """
    mine = _moment(own_created_at)
    if mine is None:
        return []
    live = []
    for run in runs or []:
        run_id = str(run.get("id") or "")
        if not run_id or run_id == str(own_run_id):
            continue
        started = _moment(run.get("created_at") or "")
        if started is None or started > mine:
            continue
        if started == mine and not _earlier_id(run_id, own_run_id):
            continue
        if (run.get("status") or "") != "completed":
            live.append(run)
            continue
        ended = _moment(run.get("updated_at") or "")
        if ended is not None and ended > mine:
            live.append(run)
    return live


def _earlier_id(run_id: str, own_run_id: str) -> bool:
    """Run ids are monotonic, so the lower one is the earlier dispatch. Falls
    back to a string compare when either is not a number."""
    try:
        return int(run_id) < int(own_run_id)
    except (TypeError, ValueError):
        return str(run_id) < str(own_run_id)


def _canonical_lane(lane: str) -> str:
    """The contract's name for a lane, matched case-insensitively and through
    the contract's own rename aliases. The relay lower-cases the lane it
    sends; Linear answers in the board's own casing; and a board mid-rename
    must not read as a card that moved."""
    if not lane:
        return ""
    for known in (*lane_scope.LANE_FLOW, *lane_scope.OFF_FLOW):
        if known.casefold() == lane.casefold():
            return known
    for old, new in lane_scope.LANE_ALIASES.items():
        if old.casefold() == lane.casefold():
            return new
    return lane


def lane_left_behind(trigger_state: str, current_lane: str) -> bool:
    """True when the card is no longer in the lane whose ENTRY fired this
    dispatch — the entry has already been served, by whichever run got a
    runner first. Either side unreadable is not a move (fail-open)."""
    fired_for = _canonical_lane(trigger_state)
    now_in = _canonical_lane(current_lane)
    if not fired_for or not now_in:
        return False
    return fired_for.casefold() != now_in.casefold()


def job_names_this_card(job_names: list, identifier: str) -> bool:
    """Whether a run's jobs say it is this card's run (DRE-3223's job name)."""
    rx = re.compile(rf"{re.escape(PLAN_JOB_MARKER)}\s*{re.escape(identifier)}\b")
    return any(rx.search(name or "") for name in job_names or [])


#: How many of the workflow's recent runs the overlap window reads. It has to
#: cover every planner run that could still have been in flight when this
#: dispatch was created — a repo-wide number bounded by the WIP cap, not by
#: this card.
_RUNS_PAGE = 50

#: How many candidate runs are worth a jobs read. `self-plan.yml` is ONE
#: workflow file shared by every card (only the concurrency GROUP is per-card,
#: `agent-<identifier>`), so the in-flight candidates arrive mixed across every
#: card being planned and THIS card's duplicate can sit anywhere among them.
#: The budget therefore cannot be spent before `job_names_this_card` has
#: narrowed them: truncating first drops the one run the guard exists to find,
#: and the duplicate plan proceeds silently (DRE-3409). It is the window size,
#: so every candidate the window returned is readable; the scan stops at the
#: first match, so a busy repo still cannot turn this guard into an API storm.
_MAX_JOB_READS = _RUNS_PAGE


def sibling_on_this_card(candidates: list, identifier: str,
                         job_names_of) -> list:
    """The first candidate whose jobs say it is THIS card's run, as a 0-or-1
    list. One sibling is the whole of what `plan_decide` needs — and the whole of
    what `reconcile.build_run_refusal` needs of the build workflow (DRE-4830) —
    so the scan short-circuits on it and the read budget is only ever spent in
    full when this card has no duplicate at all.

    `candidates` is every in-flight run of the shared workflow, across every
    card, newest first. `job_names_of` takes a run id and returns its job
    names — injected so the ordering this function fixes is testable without
    GitHub, and so each caller can bring its own token (reconcile's Actions
    reads need the dispatch token; this module's do not). A truncated scan says
    so on stderr rather than reading as a clean
    'no duplicate': "the read ran out" and "there is no duplicate" are
    different facts and get different output (`standards/console-honesty.md`
    rule 2).
    """
    runs = candidates or []
    for read, run in enumerate(runs, start=1):
        if job_names_this_card(job_names_of(run["id"]), identifier):
            return [run]
        if read >= _MAX_JOB_READS and read < len(runs):
            print(f"job-name scan stopped after {read} of {len(runs)} in-flight "
                  f"planner runs without finding one for {identifier} — a "
                  "duplicate past that point would not be seen",
                  file=sys.stderr)
            break
    return []


def window_may_be_short(runs: list, own_created_at: str) -> bool:
    """Whether the run window could have cut a candidate off at its edge.

    The window is the newest `_RUNS_PAGE` runs of the shared workflow, and only
    a run created no later than this dispatch can be a candidate. So a FULL
    page whose OLDEST entry is still newer than this dispatch means the
    candidates begin past the edge and none of them were read at all — an
    answer of 'no sibling' that nothing actually looked for. Anything short of
    a full page reached the end of the workflow's history and is complete.
    """
    if len(runs or []) < _RUNS_PAGE:
        return False
    mine = _moment(own_created_at)
    oldest = _moment((runs[-1] or {}).get("created_at") or "")
    if mine is None or oldest is None:
        return False
    return oldest > mine


def plan_decide(identifier: str, trigger_state: str, current_lane: str,
                in_flight: list, sent_by_run: str = "") -> Decision:
    """The planner's skip decision. `in_flight` is what
    `in_flight_when_dispatched` returned for this card; the lane pair is the
    dispatch's trigger lane against the card's lane as it is NOW.

    `sent_by_run` is the planner run that asked for this one, if one did
    (DRE-4573). A re-review or review-retry is always sent by a planner run on
    the same card before that run ends, so it is always in flight here, and
    counting it dropped every revised plan's round-2 review as a duplicate
    (DRE-4467, 2026-09-21). That ONE run is dropped before deciding; any other
    planner run in flight is still a duplicate, and the lane check is
    unchanged."""
    if sent_by_run:
        in_flight = [r for r in in_flight
                     if str(r.get("id")) != str(sent_by_run)]
    if in_flight:
        names = ", ".join(str(r.get("id")) for r in in_flight)
        return Decision(
            True,
            f"a planner run for {identifier} was already in flight when this "
            f"dispatch arrived (run {names}) — duplicate dispatch",
        )
    if lane_left_behind(trigger_state, current_lane):
        return Decision(
            True,
            f"{identifier} was dispatched on entering {trigger_state!r} and is "
            f"now in {current_lane!r} — that entry has already been planned",
        )
    return Decision(
        False,
        f"no planner run was in flight for {identifier} and it is still in "
        f"{current_lane or trigger_state!r} — proceeding",
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


#: The fields `decide` reads off a pull request. `state` is not optional since
#: DRE-4830: OPEN and MERGED are different decisions, and `gh pr list` defaults
#: to open-only — the DRE-2316 blindness, which is exactly how a merged PR came
#: to be invisible to this guard.
_PR_FIELDS = "number,headRefName,state"


def _pr_list(*args: str) -> list:
    """One `gh pr list --state all` read; [] on any failure (fail-open)."""
    p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", "pr", "list", "--repo", _repo(), "--state", "all",
         "--json", _PR_FIELDS, *args],
        capture_output=True, text=True, check=False,
    )
    if p.returncode != 0:
        print(f"PR read failed (rc={p.returncode}: gh pr list "
              f"{' '.join(args)}) — proceeding on fail-open", file=sys.stderr)
        return []
    try:
        prs = json.loads(p.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return prs if isinstance(prs, list) else []


def _card_prs(identifier: str) -> list:
    """The card's pull requests in EVERY state (number, head branch, state).

    A PLAIN LIST FIRST, not a search query: the twin PR may be seconds old and
    search-index lag would hide exactly the PR this guard exists to see. The
    `head:agent/<DRE-N>` search is the second read, and only when the plain list
    carried nothing for this card — a merged PR for an old card can fall off the
    newest-100 window, which is the half `reconcile.pr_for` covers with the same
    two queries in the other order.

    [] on any failure (fail-open — a blip parsed as no-PR reproduces the status
    quo, never strands the card).
    """
    prs = _pr_list("--limit", "100")
    if any(card_pr.matches_card(pr.get("headRefName"), identifier)
           for pr in prs):
        return prs
    return prs + _pr_list("--limit", "30", "--search", f"head:agent/{identifier}")


def _gh_json(*args: str):
    """`gh` read → parsed JSON, or None on any failure (fail-open)."""
    p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", *args], capture_output=True, text=True, check=False,
    )
    if p.returncode != 0:
        print(f"gh read failed (rc={p.returncode}: {' '.join(args)}) — "
              "proceeding on fail-open", file=sys.stderr)
        return None
    try:
        return json.loads(p.stdout or "null")
    except json.JSONDecodeError:
        return None


def _run_meta(run_id: str) -> dict:
    """This run's `created_at` and `workflow_id`. The workflow id, not the
    workflow NAME: a product stub may call the reusable planner under any
    name, and the run's own workflow is the one thing that is always right."""
    data = _gh_json("api", f"repos/{_repo()}/actions/runs/{run_id}")
    if not isinstance(data, dict):
        return {}
    return {"created_at": data.get("created_at") or "",
            "workflow_id": str(data.get("workflow_id") or "")}


def _workflow_runs(workflow_id: str) -> list:
    """The recent runs of the same workflow, newest first. Not filtered by
    status: a run that finished seconds ago may still have been in flight
    when this dispatch was created, which is exactly the case at issue."""
    data = _gh_json(
        "api",
        f"repos/{_repo()}/actions/workflows/{workflow_id}/runs"
        f"?per_page={_RUNS_PAGE}",
    )
    runs = (data or {}).get("workflow_runs") if isinstance(data, dict) else None
    return [
        {"id": str(r.get("id") or ""), "created_at": r.get("created_at") or "",
         "updated_at": r.get("updated_at") or "", "status": r.get("status") or ""}
        for r in (runs or [])
    ]


def _run_job_names(run_id: str) -> list:
    data = _gh_json("api", f"repos/{_repo()}/actions/runs/{run_id}/jobs")
    jobs = (data or {}).get("jobs") if isinstance(data, dict) else None
    return [j.get("name") or "" for j in (jobs or [])]


def _is_rerun() -> bool:
    """GitHub counts attempts from 1; anything above it was asked for again.
    An unreadable value is attempt 1 — the guard, not the carve-out."""
    try:
        return int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")) > 1
    except (TypeError, ValueError):
        return False


def _current_lane(identifier: str) -> str:
    """The card's lane as it is NOW — `fresh` because the whole question is
    whether it moved while this dispatch sat in the queue."""
    issue = linear_ops.get_issue(identifier, fresh=True)
    return (issue.get("state") or {}).get("name") or ""


def _plan_siblings(identifier: str, own_run_id: str) -> list:
    """The runs of this workflow, on this card, that were in flight when this
    dispatch was created. [] on any unreadable answer — fail-open.

    The workflow is shared by every card, so the in-flight candidates are
    repo-wide and `sibling_on_this_card` is what narrows them to this one.
    """
    meta = _run_meta(own_run_id)
    if not meta.get("created_at") or not meta.get("workflow_id"):
        return []
    runs = _workflow_runs(meta["workflow_id"])
    if window_may_be_short(runs, meta["created_at"]):
        print(f"every one of the {len(runs)} planner runs read is newer than "
              f"this dispatch — {identifier}'s own cohort is past the window's "
              "edge and a sibling there would not be seen", file=sys.stderr)
    candidates = in_flight_when_dispatched(runs, own_run_id, meta["created_at"])
    return sibling_on_this_card(candidates, identifier, _run_job_names)


def _emit(skip: bool, reason: str) -> None:
    lines = [f"skip={'true' if skip else 'false'}", f"reason={reason}"]
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write("\n".join(lines) + "\n")
    for line in lines:
        print(line)


def _receipt(identifier: str, decision: Decision, did_nothing: str) -> None:
    """The ONE skip receipt both gates post — one call site, so the act
    registry's single `🤖 Duplicate dispatch skipped` declaration keeps
    matching exactly one site (check_act_receipts.py refuses a declaration
    that matches two). `did_nothing` is what this rail did not do."""
    run_url = (
        f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}"
        f"/{_repo()}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    )
    try:
        linear_ops.cmd_comment(
            identifier,
            f"🤖 Duplicate dispatch skipped: {decision.reason}. {did_nothing} "
            f"Run: {run_url}",
        )
    except Exception as e:  # noqa: BLE001 — reporting never blocks the skip
        print(f"skip receipt failed ({e}) — skipping anyway", file=sys.stderr)


def cmd_gate(identifier: str) -> None:
    """The build guard. Every read fails open on its own, so a Linear blip costs
    the comment half or the lane half and a GitHub blip costs the PR half, and
    none of them costs a healthy card its build."""
    try:
        bodies = linear_ops.comment_bodies(identifier)
    except Exception as e:  # noqa: BLE001 — an unreadable card proceeds
        print(f"comment read failed ({e}) — proceeding on fail-open",
              file=sys.stderr)
        bodies = []
    try:
        lane = _current_lane(identifier)
    except Exception as e:  # noqa: BLE001 — an unreadable lane proceeds
        print(f"lane read failed ({e}) — proceeding on fail-open",
              file=sys.stderr)
        lane = ""
    decision = decide(
        identifier,
        os.environ.get("GITHUB_RUN_ID", ""),
        bodies,
        _run_status,
        _card_prs(identifier),
        card_lane=lane,
    )
    if decision.skip:
        _receipt(identifier, decision, "This run created no branch or PR.")
    _emit(decision.skip, decision.reason)


def cmd_plan_gate(identifier: str) -> None:
    """The planner's gate (DRE-3409). Every read fails open on its own, so a
    GitHub blip costs the run-overlap half and a Linear blip costs the lane
    half, and neither costs the epic its plan."""
    own_run_id = os.environ.get("GITHUB_RUN_ID", "")
    if _is_rerun():
        # A re-run is somebody ASKING for this run again — a person in the
        # Actions UI, or limit_recovery.py bringing a limit death back by
        # re-running the original run rather than re-planning a card past
        # Planning exit. Both questions below are about the DISPATCH, and a
        # re-run answers neither: it carries the original run's creation time
        # and the original trigger lane, so both checks would read the world
        # having moved on since a dispatch nobody is making again.
        _emit(False, f"run attempt {os.environ.get('GITHUB_RUN_ATTEMPT')} for "
                     f"{identifier} is a re-run — proceeding")
        return
    try:
        in_flight = _plan_siblings(identifier, own_run_id)
    except Exception as e:  # noqa: BLE001 — an unreadable run list proceeds
        print(f"run-overlap read failed ({e}) — proceeding on fail-open",
              file=sys.stderr)
        in_flight = []
    try:
        lane = _current_lane(identifier)
    except Exception as e:  # noqa: BLE001 — an unreadable card proceeds
        print(f"lane read failed ({e}) — proceeding on fail-open",
              file=sys.stderr)
        lane = ""
    decision = plan_decide(
        identifier, os.environ.get("TRIGGER_STATE", ""), lane, in_flight,
        sent_by_run=os.environ.get("SENT_BY_RUN", ""),
    )
    if decision.skip:
        _receipt(identifier, decision,
                 "This run planned nothing and moved no lane.")
    _emit(decision.skip, decision.reason)


def main(argv: list) -> int:
    if len(argv) == 3 and argv[1] == "gate":
        cmd_gate(argv[2])
        return 0
    if len(argv) == 3 and argv[1] == "plan-gate":
        cmd_plan_gate(argv[2])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
