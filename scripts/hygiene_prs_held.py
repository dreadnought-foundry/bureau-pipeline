#!/usr/bin/env python3
"""The hygiene agent's first pull-request lane (DRE-5370): pull requests the
pipeline's own state is holding.

Discovered by the core's glob (`scripts/hygiene.py`), registered nowhere. It
reads the open card-branch pull requests of this leg's repos and returns the
one unsticking action for three mechanical holds, each with its cause read
from what the pipeline wrote rather than guessed:

1. **A gate `wait` on a fix run that has finished.** The newest completed
   Merge Gate run on the head printed `decision=wait` with the reason
   `stranded_fix.lane_refusal` words for an Agent Fix run in flight for this
   pull request; that fix run has since completed, and no gate run was
   created after it finished. The action dispatches the repo's gate stub
   again — the one workflow the core's guard lets this agent dispatch — and
   the gate decides for itself, under its own rules, what happens next.
   Cause: `wait on fix run <id> (<conclusion>) at head <sha7>`.
2. **Behind a changed workflow file.** The pull request is `BEHIND` its base
   and a red REQUIRED check's workflow file changed on the base since the
   merge base — a time limit raised on `main`, say, that the branch's own
   copy predates. The action refreshes the branch at its current head (the
   `expected_head_sha` PUT, never a push). Cause: `behind base, <file>
   changed since the merge base, head <sha7>`.
3. **A fix-loop hold waiting on nothing.** The fix loop's newest `🛑` hold
   stands (no fix-loop comment after it), no operator decision answers it
   (`fix_context.operator_decision` — the sweep's own reading), and its named
   reason no longer holds on the head: every check it names in bold or
   backticks is green on `statusCheckRollup`, or, for a hold that names no
   check, the critic's newest verdict is APPROVE bound to the head
   (`merge_gate.evaluate_critic`, the gate's own predicate). The action is the
   same gate re-dispatch as (1). Cause: `hold reason cleared, <check or
   verdict> at head <sha7>`.

**A hold only a person can answer** — an acceptance criterion that cannot be
met before merge, a business choice — gets no decision from this agent:
`fix_context.operator_decision` reads only a person's comment as one, and a
decision the agent posted would be the loop taking its own word for a
person's. It posts one `hyg-decision-needed` note carrying the copy-pasteable
`fix_context.DECISION_EXAMPLE` line and returns a `Left` row; the sweep
restarts the loop the hour after a person pastes that line. A hold whose
reason is neither cleared nor a person's is left to the loop and the other
lanes — this lane names nothing it did not read.

Every cause line ends in the head's short sha, so the core's (tag, cause) key
gives each case one action per head; a push is a new head and a new cause.
This lane adds no stop of its own, never closes anything, and returns only
`gh_dispatch`, `gh_update_branch` and `gh_pr_comment`.

One action per pull request, in this order: a standing hold decides the pull
request outright (and a hold a person already answered yields nothing — the
sweep owns that restart); then a refresh; then a gate wait.

The branch-refresh reading compares `{head}...{base}`: three-dot compare
lists what the SECOND ref changed since the merge base, and what changed on
the base is the question. `{base}...{head}` lists the pull request's own
changes, which is not.

A `gh` read that fails, or answers something unreadable, skips that pull
request for this pass — said on stderr, never guessed into an action.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime

import fix_concurrency
import fix_context
import hygiene
import merge_gate
import reconcile

LANE = "Pull requests"

#: The fix loop's REST identity, and the critic's — the logins the sweep's
#: restart and the gate read a thread with.
WORKER_LOGIN = reconcile.WORKER_REST_LOGIN
CRITIC_LOGIN = f"{reconcile.QA_BOT_LOGIN}[bot]"

#: The gate's wait on a fix run, in `stranded_fix.lane_refusal`'s own words.
_FIX_WAIT = re.compile(r"Agent Fix run (\d+) is queued or running FOR #(\d+)\b")
_DECISION_LINE = re.compile(r"(?:^|\s)decision=([a-z_]+)\s*$")
_REASON_LINE = re.compile(r"(?:^|\s)reason=(.*)$")
_RUN_ID = re.compile(r"/actions/runs/(\d+)")

#: `statusCheckRollup` reads in GraphQL's upper case; the gate's green set is
#: lower. Skipped is green — every narrowed suite relies on it.
GREEN = frozenset(c.upper() for c in merge_gate.GREEN_CONCLUSIONS)
RED = frozenset({"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED",
                 "STARTUP_FAILURE", "ERROR"})

#: The hold reasons only a person can answer, and the words the cause names
#: each by. Read off the hold's text, in this order; the first that matches.
PERSON_REASONS = (
    (re.compile(r"\b(?:before|until after|after) (?:the |it is |it's )?merged?\b", re.I),
     "a criterion that cannot be met before merge"),
    (re.compile(r"\bbusiness (?:choice|decision|call|question)\b|\bA[- ]vs\.?[- ]B\b", re.I),
     "a business choice"),
)

GATE_RUNS = 20
FIX_RUNS = 100


def _sha7(pull: dict) -> str:
    return (pull.get("headRefOid") or "")[:7]


def _target(repo: str, pull: dict) -> str:
    return f"{repo}#{pull['number']}"


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _json(ctx: hygiene.Context, argv: list):
    return json.loads(ctx.gh(argv) or "null")


def _label(name: str) -> str:
    """A check name fit for a cause line: `·` separates a receipt's clock."""
    return name.replace("·", "-")


def fix_stub(repo: str) -> str:
    """The repo's Agent Fix stub, by `reconcile.fix_workflow()`'s rule — runs
    exist only under the stub's filename."""
    with hygiene._sweeping(repo.split("/", 1)[-1].lower()):
        return reconcile.fix_workflow()


def fix_run_waited_on(reason: str, number: int) -> int | None:
    """The Agent Fix run a gate `reason=` line says it waited on for THIS
    pull request, or None."""
    match = _FIX_WAIT.search(reason or "")
    if match is None or int(match.group(2)) != int(number):
        return None
    return int(match.group(1))


def _receipt_action(act: str, repo: str, pull: dict, cause: str, evidence: list,
                    ctx: hygiene.Context, *writes) -> hygiene.Action:
    note = hygiene.gh_pr_comment(repo, pull["number"],
                                 hygiene.receipt(act, cause, evidence, ctx.now))
    return hygiene.Action(lane=LANE, target=_target(repo, pull), act=act, cause=cause,
                          evidence=list(evidence), writes=[*writes, note])


def _redispatch(repo: str, pull: dict, cause: str, evidence: list,
                ctx: hygiene.Context) -> hygiene.Action:
    dispatch = hygiene.gh_dispatch(repo, hygiene.gate_stub(repo),
                                   {"pr_number": pull["number"]})
    return _receipt_action("hygiene-gate-redispatch", repo, pull, cause, evidence, ctx,
                           dispatch)


# --------------------------------------------------------------------------- #
# (1) a gate wait on a fix run that has finished                               #
# --------------------------------------------------------------------------- #


def _gate_log_decision(log: str) -> tuple:
    """The last `decision=` and `reason=` the gate printed."""
    decision = reason = None
    for line in (log or "").splitlines():
        if (m := _DECISION_LINE.search(line)):
            decision = m.group(1)
        elif (m := _REASON_LINE.search(line)):
            reason = m.group(1).strip()
    return decision, reason


def gate_wait(repo: str, pull: dict, ctx: hygiene.Context) -> hygiene.Action | None:
    gate = hygiene.gate_stub(repo)
    runs = _json(ctx, ["gh", "run", "list", "--repo", repo, "--workflow", gate,
                       "--branch", pull["headRefName"], "--json",
                       "databaseId,status,conclusion,createdAt", "--limit", str(GATE_RUNS)])
    runs = sorted(runs or [], key=lambda r: r.get("createdAt") or "", reverse=True)
    newest = next((r for r in runs if r.get("status") == "completed"), None)
    if newest is None:
        return None
    log = ctx.gh(["gh", "run", "view", str(newest["databaseId"]), "--repo", repo, "--log"])
    decision, reason = _gate_log_decision(log)
    fix_id = fix_run_waited_on(reason, pull["number"]) if decision == "wait" else None
    if fix_id is None:
        return None
    fix = _json(ctx, ["gh", "run", "view", str(fix_id), "--repo", repo, "--json",
                      "status,conclusion,updatedAt"]) or {}
    finished = _when(fix.get("updatedAt"))
    if fix.get("status") != "completed" or finished is None:
        return None
    if any((_when(r.get("createdAt")) or finished) > finished for r in runs):
        return None  # a gate run started after the fix finished — it has looked
    cause = (f"wait on fix run {fix_id} ({fix.get('conclusion') or 'none'}) "
             f"at head {_sha7(pull)}")
    return _redispatch(repo, pull, cause,
                       [f"gate run {newest['databaseId']}", f"fix run {fix_id}"], ctx)


# --------------------------------------------------------------------------- #
# (2) behind a changed workflow file                                           #
# --------------------------------------------------------------------------- #


def _rollup_name(check: dict) -> str:
    return check.get("name") or check.get("context") or ""


def _rollup_state(check: dict) -> str:
    return (check.get("conclusion") or check.get("state") or "").upper()


def branch_refresh(repo: str, pull: dict, ctx: hygiene.Context) -> hygiene.Action | None:
    if (pull.get("mergeStateStatus") or "").upper() != "BEHIND":
        return None
    if not any(_rollup_state(c) in RED for c in pull.get("statusCheckRollup") or []):
        return None  # all green: behind is not a hold
    required = _json(ctx, ["gh", "pr", "checks", str(pull["number"]), "--repo", repo,
                           "--required", "--json", "name,bucket,link,workflow"]) or []
    red = [c for c in required if c.get("bucket") == "fail"]
    if not red:
        return None
    changed = _json(ctx, ["gh", "api",
                          f"repos/{repo}/compare/{pull['headRefOid']}...{pull['baseRefName']}"])
    files = {f.get("filename") for f in (changed or {}).get("files") or []}
    paths: dict = {}
    for check in red:
        match = _RUN_ID.search(check.get("link") or "")
        if match is None:
            continue
        run_id = match.group(1)
        if run_id not in paths:
            paths[run_id] = (_json(ctx, ["gh", "api", f"repos/{repo}/actions/runs/{run_id}"])
                             or {}).get("path")
        path = paths[run_id]
        if path and path.startswith(".github/workflows/") and path in files:
            cause = (f"behind base, {path} changed since the merge base, "
                     f"head {_sha7(pull)}")
            write = hygiene.gh_update_branch(repo, pull["number"], pull["headRefOid"])
            return _receipt_action("hygiene-branch-refresh", repo, pull, cause,
                                   [f"check {check.get('name')}", f"run {run_id}",
                                    f"file {path}"], ctx, write)
    return None


# --------------------------------------------------------------------------- #
# (3) a fix-loop hold                                                          #
# --------------------------------------------------------------------------- #


def _hold_visible(pull: dict) -> bool:
    """The cheap reading off the comments the list already carries: a fix-loop
    `🛑` is on the thread. Only then is the whole thread read."""
    return any(reconcile.is_worker_bot_comment(c)
               and (c.get("body") or "").lstrip().startswith(fix_context.BLOCKER_PREFIX)
               for c in pull.get("comments") or [])


def _thread(repo: str, pull: dict, ctx: hygiene.Context) -> list:
    raw = _json(ctx, ["gh", "api", "--paginate", "--slurp",
                      f"repos/{repo}/issues/{pull['number']}/comments?per_page=100"])
    return merge_gate.flatten_pages(raw if raw is not None else [])


def standing_hold(thread: list) -> dict | None:
    """The fix loop's newest `🛑` hold, when nothing of the loop's came after
    it — its no-work notice and this agent's own receipts aside."""
    holds = fix_context.prior_blockers(thread, WORKER_LOGIN)
    if not holds:
        return None
    hold = holds[-1]
    at = next(i for i, c in enumerate(thread) if c is hold)
    for c in thread[at + 1:]:
        if ((c.get("user") or {}).get("login") == WORKER_LOGIN
                and not fix_context.is_noop_notice(c, WORKER_LOGIN)
                and hygiene.read_receipt(c.get("body") or "") is None):
            return None
    return hold


def _named_checks(hold: dict, pull: dict) -> list:
    """The head's checks the hold names, in bold or in backticks."""
    body = hold.get("body") or ""
    return [c for c in pull.get("statusCheckRollup") or []
            if _rollup_name(c) and (f"**{_rollup_name(c)}**" in body
                                    or f"`{_rollup_name(c)}`" in body)]


def _holding_run(repo: str, pull: dict, hold: dict, ctx: hygiene.Context,
                 cache: dict) -> str | None:
    """The Agent Fix run that posted the hold: the newest one for this pull
    request created before it, read off the stub's run-names."""
    if repo not in cache:
        cache[repo] = _json(ctx, ["gh", "run", "list", "--repo", repo, "--workflow",
                                  fix_stub(repo), "--json", "databaseId,displayTitle,createdAt",
                                  "--limit", str(FIX_RUNS)]) or []
    posted = hold.get("created_at") or ""
    mine = [r for r in cache[repo]
            if fix_concurrency.pr_of_run_name(r.get("displayTitle")) == pull["number"]
            and (r.get("createdAt") or "") <= posted]
    if not mine:
        return None
    return str(max(mine, key=lambda r: r.get("createdAt") or "")["databaseId"])


def person_reason(hold: dict) -> str | None:
    body = hold.get("body") or ""
    for pattern, words in PERSON_REASONS:
        if pattern.search(body):
            return words
    return None


def held(repo: str, pull: dict, ctx: hygiene.Context, cache: dict) -> list | None:
    """None when no fix-loop hold stands; otherwise what this lane does about
    it, which may be nothing."""
    if not _hold_visible(pull):
        return None
    thread = _thread(repo, pull, ctx)
    hold = standing_hold(thread)
    if hold is None:
        return None
    if fix_context.operator_decision(thread, WORKER_LOGIN) is not None:
        return []  # answered — the sweep restarts the loop on it
    sha7 = _sha7(pull)
    named = _named_checks(hold, pull)
    if named:
        if not all(_rollup_state(c) in GREEN for c in named):
            return []
        cleared = f"check {', '.join(sorted(_label(_rollup_name(c)) for c in named))} green"
        evidence = [f"check {_rollup_name(c)}" for c in named]
    else:
        line = merge_gate.first_line(merge_gate.latest_verdict_comment(
            thread, CRITIC_LOGIN, merge_gate.CRITIC_MARKER))
        if line and merge_gate.evaluate_critic(line, pull["headRefOid"]) is None:
            cleared, evidence = "critic APPROVE", [f"critic verdict at head {sha7}"]
        else:
            cleared = None
    run_id = _holding_run(repo, pull, hold, ctx, cache) if (
        cleared or person_reason(hold)) else None
    held_by = [f"fix run {run_id}"] if run_id else []
    if cleared:
        return [_redispatch(repo, pull, f"hold reason cleared, {cleared} at head {sha7}",
                            evidence + held_by + [f"hold comment {hold.get('id')}"], ctx)]
    reason = person_reason(hold)
    if reason is None:
        return []
    cause = f"hold needs a person, {reason} at head {sha7}"
    note = _receipt_action(
        "hygiene-decision-needed", repo, pull, cause,
        [*held_by, f"hold comment {hold.get('id')}",
         f"a person answers here with a first line reading {fix_context.DECISION_EXAMPLE}"],
        ctx)
    return [note, hygiene.Left(
        lane=LANE, target=_target(repo, pull),
        why=f"the fix loop holds for {reason}, at head {sha7}",
        recommendation=(f"a person answers on the pull request with a comment whose first "
                        f"line reads {fix_context.DECISION_EXAMPLE} — the sweep restarts "
                        f"the fix loop on it"))]


# --------------------------------------------------------------------------- #
# the lane                                                                     #
# --------------------------------------------------------------------------- #


def _plan_pull(repo: str, pull: dict, ctx: hygiene.Context, cache: dict) -> list:
    held_items = held(repo, pull, ctx, cache)
    if held_items is not None:
        return held_items
    for case in (branch_refresh, gate_wait):
        action = case(repo, pull, ctx)
        if action is not None:
            return [action]
    return []


def plan(board: hygiene.Board, ctx: hygiene.Context) -> list:
    out: list = []
    cache: dict = {}
    for repo in sorted(ctx.repos):
        for pull in board.prs.get(repo) or []:
            if not reconcile.card_branch(pull.get("headRefName")):
                continue
            try:
                out += _plan_pull(repo, pull, ctx, cache)
            except hygiene.Forbidden:
                raise  # a refused read is a lane bug, never a skipped pull request
            except (RuntimeError, ValueError, KeyError, TypeError) as e:
                print(f"hygiene: {LANE} — {_target(repo, pull)} skipped this pass, "
                      f"a read failed: {e}", file=sys.stderr)
    return out
