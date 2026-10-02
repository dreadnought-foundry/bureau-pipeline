#!/usr/bin/env python3
"""The hygiene agent's second pull-request lane (DRE-5371): checks that flaked,
and pull requests that no longer matter.

A lane module of `hygiene.py` (DRE-5368), found by its glob and registered
nowhere. It reads the open pull requests the core hands every lane and returns
two acts, never a third:

1. **A check proven flaky is re-run once, with the evidence named.** A check
   on the head is red, and EITHER the same check concluded green on the same
   head sha in another run (`gh run list`), OR the failed run's log
   (`gh run view --log-failed`) carries a transient signature
   `medic_classify` already knows — `upstream_5xx`, `linear_ratelimited`.
   A real red test is never re-run. The cause names the check, the run ids
   and the head: `<check> red in run <id>, green in run <id2> at head <sha7>`
   or `<check> red in run <id>, <signature> at head <sha7>`.

   The lane's own stop, stricter than the core's key (DRE-1921 — a retry at a
   vendor boundary is bounded): a head that already carries ANY
   `hyg-check-rerun` receipt gets no second rerun. A later red run on the same
   head is a new run id, so a new cause, and the key alone would re-run it —
   the row is `Left` instead.

2. **A moot or superseded pull request is closed, with the evidence.** The
   card its branch names has a newer pull request that merged (`card_pr.find`,
   through `ctx.gh`), or the card is `Canceled` or `Duplicate`. Newer means a
   higher number: the card search sees only `agent/<card>` branches, so a
   rework on any other branch finds the card's OLDER merged pull request, and
   that supersedes nothing. A `Done` card with no other merged pull request is
   LEFT: closing it could discard shipped work, and a person decides.

A read that fails — a 502, an expired run log, a payload that is not JSON —
is not "no evidence". The pull request it was for gets a `Left` row naming
what could not be read, nothing is rerun or closed on it, and every other pull
request still gets its rows. A refusal by the core's read-only wrapper is not
a failed read: it is a lane bug, and it still stops the pass.

It never merges. The only `gh` argv this module builds are reads handed to
`ctx.gh`; its writes are the core's constructors' argv, and the core's shape
guard is what keeps anything else out.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card_pr  # noqa: E402 — the one "which pull request is this card's" seam
import hygiene  # noqa: E402
import linear_ops  # noqa: E402
import medic_classify  # noqa: E402 — the transient signatures, not a second copy

LANE = "Pull requests"

#: A check conclusion that is red. `statusCheckRollup` spells them upper case.
RED = ("FAILURE", "TIMED_OUT")
#: The conclusion `gh run list` gives a green run.
GREEN = "success"

#: The transient signatures a failed log may carry, by the class name
#: `medic_classify.classify` gives each — checked in this order.
SIGNATURES = (
    ("upstream_5xx", medic_classify.is_upstream_5xx),
    ("linear_ratelimited", medic_classify.is_linear_rate_limited),
)

#: The states that make a card's open pull request moot.
MOOT_STATES = ("Canceled", "Duplicate")
DONE = "Done"

RUN_FIELDS = "databaseId,name,conclusion,headSha"
RUN_LIMIT = "50"

_CARD = re.compile(r"\b(DRE-\d+)\b")
_RUN_URL = re.compile(r"/actions/runs/(\d+)\b")
#: The head a `hyg-check-rerun` cause names — the last words of every one.
_SPENT_HEAD = re.compile(r" at head ([0-9a-f]{7})$")

_ISSUE_STATE = "query($id: String!) { issue(id: $id) { identifier state { name } } }"


def run_list_argv(repo: str, branch: str) -> list:
    return ["gh", "run", "list", "--repo", repo, "--branch", branch,
            "--json", RUN_FIELDS, "--limit", RUN_LIMIT]


def run_log_argv(repo: str, run_id: int | str) -> list:
    return ["gh", "run", "view", str(run_id), "--repo", repo, "--log-failed"]


class Unreadable(Exception):
    """A read a decision rests on could not be made. Never "no evidence"."""

    def __init__(self, what: str, error: BaseException):
        super().__init__(what)
        self.what = what
        self.error = error


def plan(board: hygiene.Board, ctx: hygiene.Context) -> list:
    out: list = []
    for repo in sorted(board.prs):
        if repo not in ctx.repos:
            continue
        for pull in board.prs[repo]:
            rows: list = []
            try:
                closing = _moot(board, ctx, repo, pull)
                if closing is not None:
                    rows.append(closing)
                if not isinstance(closing, hygiene.Action):  # a closed one is not also re-run
                    rerun = _flaky(ctx, repo, pull)
                    if rerun is not None:
                        rows.append(rerun)
            except Unreadable as e:
                rows.append(_unreadable(repo, pull, e))
            out.extend(rows)
    return out


def _target(repo: str, pull: dict) -> str:
    return f"{repo}#{pull['number']}"


def _read(ctx: hygiene.Context, argv: list, what: str) -> str:
    """`ctx.gh`, a failed read raised as `Unreadable` — a refusal is not one."""
    try:
        return ctx.gh(argv)
    except hygiene.Forbidden:
        raise
    except (RuntimeError, OSError) as e:
        raise Unreadable(what, e) from e


def _unreadable(repo: str, pull: dict, e: Unreadable) -> hygiene.Left:
    error = (str(e.error).splitlines() or [type(e.error).__name__])[0][:200]
    return hygiene.Left(
        lane=LANE, target=_target(repo, pull),
        why=f"could not read {e.what}",
        recommendation=f"{error} — nothing was rerun or closed on it; the next pass "
                       "reads it again, so read it by hand only if this row repeats",
    )


# --------------------------------------------------------------------------- #
# (1) a check proven flaky                                                     #
# --------------------------------------------------------------------------- #


def _red_checks(pull: dict) -> list:
    """(check name, run id) for each red Actions check on the head, one per
    run, in the rollup's order."""
    seen: set = set()
    out = []
    for check in pull.get("statusCheckRollup") or []:
        if (check.get("conclusion") or "").upper() not in RED:
            continue
        match = _RUN_URL.search(check.get("detailsUrl") or "")
        if match is None or match.group(1) in seen:
            continue
        seen.add(match.group(1))
        out.append((check.get("name") or check.get("workflowName") or "check",
                    match.group(1), check.get("workflowName")))
    return out


def _spent(pull: dict, sha7: str) -> bool:
    """Does this head already carry a `hyg-check-rerun` receipt, whatever its
    cause?"""
    tag = hygiene.TAGS["hygiene-check-rerun"]
    for comment in pull.get("comments") or []:
        head = hygiene.read_receipt((comment or {}).get("body") or "")
        if head and head["tag"] == tag:
            match = _SPENT_HEAD.search(head["cause"])
            if match and match.group(1) == sha7:
                return True
    return False


def _proof(ctx: hygiene.Context, repo: str, head: str, runs: list,
           check: str, run_id: str, workflow: str | None) -> tuple | None:
    """(cause tail, evidence) proving this red run a flake, or None."""
    own = next((r for r in runs if str(r.get("databaseId")) == run_id), None)
    name = (own or {}).get("name") or workflow
    for r in runs:
        if (str(r.get("databaseId")) != run_id and r.get("name") == name
                and r.get("headSha") == head and r.get("conclusion") == GREEN):
            green = str(r["databaseId"])
            return f"green in run {green}", [f"run {run_id}", f"run {green}"]
    log = _read(ctx, run_log_argv(repo, run_id), f"the log of run {run_id}")
    for signature, matches in SIGNATURES:
        if matches(log):
            return signature, [f"run {run_id}", f"log {signature}"]
    return None


def _flaky(ctx: hygiene.Context, repo: str, pull: dict):
    red = _red_checks(pull)
    head = pull.get("headRefOid") or ""
    if not red or not head:
        return None
    sha7 = head[:7]
    branch = pull["headRefName"]
    what = f"the runs of {branch}"
    try:
        runs = json.loads(_read(ctx, run_list_argv(repo, branch), what) or "[]")
    except ValueError as e:  # a payload that is not JSON is unreadable, not empty
        raise Unreadable(what, e) from e
    for check, run_id, workflow in red:
        proof = _proof(ctx, repo, head, runs, check, run_id, workflow)
        if proof is None:
            continue  # a real red test — never re-run
        if _spent(pull, sha7):
            return hygiene.Left(
                lane=LANE, target=_target(repo, pull),
                why=f"rerun already spent on head {sha7}",
                recommendation=f"read the run — {check} is red again in run {run_id} "
                               "after this head's one rerun",
            )
        tail, evidence = proof
        cause = f"{check} red in run {run_id}, {tail} at head {sha7}"
        evidence = [*evidence, f"check {check}", f"head {sha7}"]
        act = "hygiene-check-rerun"
        return hygiene.Action(
            lane=LANE, target=_target(repo, pull), act=act, cause=cause, evidence=evidence,
            writes=[hygiene.gh_rerun(repo, run_id),
                    hygiene.gh_pr_comment(repo, pull["number"],
                                          hygiene.receipt(act, cause, evidence, ctx.now))],
        )
    return None


# --------------------------------------------------------------------------- #
# (2) a moot or superseded pull request                                        #
# --------------------------------------------------------------------------- #


def _card_state(board: hygiene.Board, ctx: hygiene.Context, ident: str) -> str | None:
    """The card's lane: off the board read when the card is on it, else one
    counted `ctx.linear` read. None when Linear does not know the card."""
    for cards in board.lanes.values():
        for card in cards:
            if card.get("identifier") == ident:
                return (card.get("state") or {}).get("name")
    try:
        data = ctx.linear(_ISSUE_STATE, {"id": ident})
    except linear_ops.LinearError:
        return None  # an unknown card is not a state — nothing is closed on it
    return (((data or {}).get("issue") or {}).get("state") or {}).get("name")


def _moot(board: hygiene.Board, ctx: hygiene.Context, repo: str, pull: dict):
    match = _CARD.search(pull.get("headRefName") or "")
    if match is None:
        return None
    ident = match.group(1)
    number = pull["number"]
    what = f"the pull requests of {ident}"
    try:
        newest = card_pr.find(ident, repo=repo,
                              run=lambda args: _read(ctx, ["gh", *args], what))
    except card_pr.PrLookupError as e:  # its bad-JSON answer — never "none"
        raise Unreadable(what, e) from e
    if card_pr.pr_state(newest) == card_pr.MERGED and (newest.get("number") or 0) > number:
        merged = newest["number"]
        return _close(ctx, repo, pull, f"superseded by merged #{merged}",
                      [f"{repo}#{merged}", ident, f"{repo}#{number}"])
    state = _card_state(board, ctx, ident)
    if state in MOOT_STATES:
        return _close(ctx, repo, pull, f"card {ident} is {state}",
                      [ident, f"{repo}#{number}", pull.get("headRefName")])
    if state == DONE:
        return hygiene.Left(
            lane=LANE, target=_target(repo, pull),
            why="Done card, open pull request — a person decides",
            recommendation=f"{ident} is Done and no other pull request of it merged — "
                           "read this one: close it if the work shipped elsewhere, "
                           "or reopen the card if it still carries work",
        )
    return None


def _close(ctx: hygiene.Context, repo: str, pull: dict, cause: str, evidence: list):
    act = "hygiene-pr-close"
    return hygiene.Action(
        lane=LANE, target=_target(repo, pull), act=act, cause=cause, evidence=evidence,
        writes=[hygiene.gh_pr_close(repo, pull["number"],
                                    hygiene.receipt(act, cause, evidence, ctx.now))],
    )
