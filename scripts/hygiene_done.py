#!/usr/bin/env python3
"""The hygiene agent's Todo-and-proofs lane (DRE-5411): close what the
evidence says is done.

Discovered by the core's glob (`scripts/hygiene.py`), registered nowhere. It
reads the cards in `Todo`, `In Progress`, `In Review` and `Hand-work` once and
returns four kinds of action, each closing receipt quoting the CEO's standing
rule — "if the cards are proven with evidence then just approve them":

1. **Merged but never closed.** A card that is not an epic, not `no-code` and
   not `PROOF:`-titled, whose newest counting pull request
   (`card_pr.find`, through `ctx.gh`) is MERGED and which
   `linear_ops.auto_done_skip_reason` does not refuse: a `hyg-card-closed`
   receipt, then Done. `hand-built` work whose pull request merged is done
   too. A `no-code` card is a `Left` row instead — the merge is not the work.
   Cause: `#<n> merged <date> <HH:MM PT>`, the merge's own time.
2. **A proof proven.** A `PROOF:` card whose merged pull request touched a file
   under `docs/` or `architecture/` — the record. The record is read at the
   default branch and judged on the one shape a machine can read without
   judgment, a criterion table (`criterion_rows`): every row but the record's
   own merge and the CEO's closing step (`CLOSING_ROW_WORDS`) must open with
   one of `MET_WORDS`. Then a `hyg-proof-closed` receipt and Done. Cause:
   `record <path> at #<n>, <k> rows met` — k is the record's, fixed once
   merged. Anything else is a `Left` row "ready for the CEO" naming the record
   and each row not met, or saying it has no criterion table. Checkboxes are
   never read: they are the card's own criteria copied in. `OWN_PROOF`, this
   agent's own epic's proof, is a `Left` row before anything is looked up —
   the CEO reads that record and closes that card.
3. **A superseded proof.** A `PROOF:` card whose parent epic is `Canceled` or
   `Duplicate`: Canceled, under a `hyg-card-canceled` receipt. Cause:
   `parent <DRE-N> is <state>`.
4. **A Todo build card with no run.** No `hand-built`, no `⏳`/`🧠` heartbeat in
   the comment window, at least two sweep re-dispatch receipts, the newest
   older than the Todo stale window. A run of the repo's build stub created
   within ten minutes after that receipt is the card's — a minute before it
   counts too, because the sweep writes its receipt only after the dispatch
   returns. A run in flight is a build about to start and yields nothing; a
   failed run names its failing step off `--log-failed`; no run names the
   re-dispatch. Cause: `run <id> failed at step <name>` or `no run after the
   re-dispatch at <date> <HH:MM PT>`, the receipt's own time; the count of
   re-dispatches goes in the evidence, never the cause. One `hyg-cause-named`
   comment and a `Left` row — and this lane's one stop on top of the key: at
   most one such comment per card per PT calendar day, the `Left` row alone
   after the first, since every fresh re-dispatch is a new cause.

A proof with no merged pull request, or whose pull request touched no record,
is still being proven and yields nothing. A card whose `repo:` label the map
does not name is never looked up: its pull request could be in any repo.

The lane returns only `linear_comment` and `linear_state` to `Done` or
`Canceled`; the core's guard refuses either for a card with children, and the
epic check here stops one before anything is read. A `gh` read that fails, or
answers something unreadable, skips that card for this pass — said on stderr,
never guessed into an action.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from collections import namedtuple
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import card_pr
import hygiene
import lane_contract
import linear_ops
import proof_and_demo
import reconcile

LANE = "Todo and proofs"

#: The lanes read, once each. `Hand-work` is absent until the contract
#: declares it, and an absent lane is skipped.
READ_LANES = ("Todo", "In Progress", "In Review", hygiene.HAND_WORK)

#: This agent's own epic's proof card: the CEO closes it.
OWN_PROOF = "DRE-5412"

#: A criterion row naming one of these is the record's own merge or the CEO's
#: closing step, and is skipped.
CLOSING_ROW_WORDS = ("merged", "on main", "the ceo", "close")
#: A result cell opening with one of these, as a whole word, is met.
MET_WORDS = ("met", "holds", "observed", "proven", "pass", "yes")

RECORD_ROOTS = ("docs/", "architecture/")
SUPERSEDED = ("Canceled", "Duplicate")
HAND_BUILT = reconcile.HAND_BUILT_LABEL
NO_CODE = linear_ops.NO_CODE_LABEL

CEO_RULE = "if the cards are proven with evidence then just approve them"
RULE_EVIDENCE = f'the CEO\'s standing rule: "{CEO_RULE}"'

PR_FIELDS = "number,url,headRefName,state,mergedAt,files"
RUN_FIELDS = "databaseId,status,conclusion,createdAt,event"
RUN_LIMIT = 50
MIN_REDISPATCHES = 2
TIE_AFTER = timedelta(minutes=10)
TIE_BEFORE = timedelta(minutes=1)
FAILED = ("failure", "timed_out", "startup_failure")

_PT = ZoneInfo("America/Los_Angeles")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SEPARATOR_CELL = re.compile(r":?-+:?")
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_MET = re.compile(rf"(?:{'|'.join(MET_WORDS)})\b")

#: `rows` is None when the record holds no criterion table; `met` and `unmet`
#: are the (criterion, result) pairs that are not closing rows.
Reading = namedtuple("Reading", "rows met unmet")


# --------------------------------------------------------------------------- #
# the criterion table                                                          #
# --------------------------------------------------------------------------- #


def _cells(line: str) -> list:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [c.strip().replace("\\|", "|") for c in _UNESCAPED_PIPE.split(row)]


def _is_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def _is_separator(line: str) -> bool:
    return _is_row(line) and all(_SEPARATOR_CELL.fullmatch(c) for c in _cells(line))


def _cell(row: list, at: int) -> str:
    return row[at] if at < len(row) else ""


def criterion_rows(text: str) -> list | None:
    """The first markdown table whose header row has a cell containing
    `criteri`, as (criterion cell, the cell after it) pairs — or None when the
    record holds no such table. Tables inside fenced code are not tables."""
    lines = (text or "").splitlines()
    fenced = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            fenced = not fenced
            i += 1
            continue
        if fenced or not _is_row(line) or i + 1 >= len(lines) or not _is_separator(lines[i + 1]):
            i += 1
            continue
        header = _cells(line)
        body, i = [], i + 2
        while i < len(lines) and _is_row(lines[i]):
            body.append(_cells(lines[i]))
            i += 1
        at = next((k for k, c in enumerate(header) if "criteri" in c.lower()), None)
        if at is not None:
            return [(_cell(r, at), _cell(r, at + 1)) for r in body]
    return None


def is_closing_row(criterion: str) -> bool:
    text = (criterion or "").replace("`", "").lower()
    return any(word in text for word in CLOSING_ROW_WORDS)


def _plain(cell: str) -> str:
    return (cell or "").replace("*", "").replace("_", "").strip()


def row_met(result: str) -> bool:
    return _MET.match(_plain(result).lower()) is not None


def reading(text: str) -> Reading:
    rows = criterion_rows(text)
    judged = [r for r in rows or [] if not is_closing_row(r[0])]
    return Reading(rows=rows, met=[r for r in judged if row_met(r[1])],
                   unmet=[r for r in judged if not row_met(r[1])])


# --------------------------------------------------------------------------- #
# reads                                                                        #
# --------------------------------------------------------------------------- #


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _moment(when: datetime) -> str:
    """A moment as a cause line names it: its PT date and `HH:MM PT`."""
    return when.astimezone(_PT).strftime("%Y-%m-%d %H:%M PT")


def _pt_day(when: datetime) -> str:
    return when.astimezone(_PT).strftime("%Y-%m-%d")


def _repo(card: dict, ctx: hygiene.Context) -> str | None:
    slug = hygiene._repo_slug(card)
    repo = ctx.repo_map.get(slug) if slug else None
    return repo if repo in ctx.repos else None


def _merged_pr(ident: str, repo: str, ctx: hygiene.Context) -> dict | None:
    pr = card_pr.find(ident, repo=repo, fields=PR_FIELDS,
                      run=lambda args: ctx.gh(["gh", *args]))
    return pr if card_pr.pr_state(pr) == card_pr.MERGED else None


def _default_branch(repo: str, ctx: hygiene.Context, cache: dict) -> str:
    key = ("branch", repo)
    if key not in cache:
        branch = (json.loads(ctx.gh(["gh", "api", f"repos/{repo}"]) or "null")
                  or {}).get("default_branch")
        if not branch:
            raise ValueError(f"{repo} named no default branch")
        cache[key] = branch
    return cache[key]


def _record_text(repo: str, path: str, branch: str, ctx: hygiene.Context) -> str:
    doc = json.loads(ctx.gh(["gh", "api", f"repos/{repo}/contents/{quote(path)}?ref={branch}"])
                     or "null") or {}
    if doc.get("encoding") != "base64":
        raise ValueError(f"{path} came back without its content")
    return base64.b64decode(doc.get("content") or "").decode("utf-8")


def build_stub(repo: str) -> str:
    """The repo's build stub, by `reconcile.build_workflow()`'s rule — runs
    exist only under the stub's filename."""
    with hygiene._sweeping(repo.split("/", 1)[-1].lower()):
        return reconcile.build_workflow()


def _runs(repo: str, ctx: hygiene.Context, cache: dict) -> list:
    key = ("runs", repo)
    if key not in cache:
        cache[key] = json.loads(ctx.gh(["gh", "run", "list", "--repo", repo, "--workflow",
                                        build_stub(repo), "--json", RUN_FIELDS,
                                        "--limit", str(RUN_LIMIT)]) or "[]") or []
    return cache[key]


def failing_step(log: str) -> str | None:
    """The step `gh run view --log-failed` names: each line is
    `<job>\\t<step>\\t<timestamp> <text>`."""
    for line in (log or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[1].strip():
            return parts[1].strip().replace("·", "-")
    return None


# --------------------------------------------------------------------------- #
# the four rules                                                               #
# --------------------------------------------------------------------------- #


def _left(ident: str, why: str, recommendation: str) -> hygiene.Left:
    return hygiene.Left(lane=LANE, target=ident, why=why, recommendation=recommendation)


def _action(card: dict, act: str, cause: str, evidence: list, ctx: hygiene.Context,
            state: str | None = None) -> hygiene.Action:
    writes = [hygiene.linear_comment(card, hygiene.receipt(act, cause, evidence, ctx.now))]
    if state is not None:
        writes.append(hygiene.linear_state(card, state))
    return hygiene.Action(lane=LANE, target=card["identifier"], act=act, cause=cause,
                          evidence=list(evidence), writes=writes)


def merged(card: dict, repo: str, pr: dict, labels: list, has_children: bool,
           ctx: hygiene.Context) -> list:
    """(1) a card whose pull request merged and never closed."""
    ident, number = card["identifier"], pr["number"]
    at = _when(pr.get("mergedAt"))
    if at is None:
        raise ValueError(f"#{number} is MERGED with no merge time")
    said = f"#{number} merged {_moment(at)}"
    if NO_CODE in [name.lower() for name in labels]:
        return [_left(ident, f"{said} — the card is {NO_CODE}, so the merge is not the work",
                      "the person who owns the operator work confirms it was done "
                      "and closes the card")]
    reason = linear_ops.auto_done_skip_reason(card.get("title") or "", labels, has_children)
    if reason is not None:
        return [_left(ident, f"{said} — not closed on the merge: {reason}",
                      "a person reads the card's evidence and closes it")]
    return [_action(card, "hygiene-card-close", said,
                    [f"pull request {repo}#{number}", RULE_EVIDENCE], ctx, "Done")]


def _row(criterion: str, result: str) -> str:
    def cut(text: str, n: int) -> str:
        text = " ".join(text.split())
        return text if len(text) <= n else text[:n - 1].rstrip() + "…"
    return f"“{cut(criterion, 70)}” reads “{cut(result.replace('*', ''), 50)}”"


def proven(card: dict, repo: str, ctx: hygiene.Context, cache: dict) -> list:
    """(2) a `PROOF:` card whose merged record is a criterion table, all met."""
    ident = card["identifier"]
    pr = _merged_pr(ident, repo, ctx)
    if pr is None:
        return []
    number = pr["number"]
    records = [f.get("path") or "" for f in pr.get("files") or []]
    records = [p for p in records if p.startswith(RECORD_ROOTS)]
    if not records:
        return []
    branch = _default_branch(repo, ctx, cache)
    found = None
    for path in records:
        if path.endswith(".md"):
            read = reading(_record_text(repo, path, branch, ctx))
            if read.rows is not None:
                found = (path, read)
                break
    recommend = "the CEO reads the record and closes the card, or names what it still owes"
    if found is None:
        return [_left(ident, f"ready for the CEO — record {records[0]} at #{number} has no "
                             "criterion table", recommend)]
    path, read = found
    if read.unmet:
        rows = "; ".join(_row(c, r) for c, r in read.unmet)
        return [_left(ident, f"ready for the CEO — record {path} at #{number}, "
                             f"{len(read.unmet)} row(s) not met: {rows}", recommend)]
    if not read.met:
        return [_left(ident, f"ready for the CEO — record {path} at #{number} has a criterion "
                             "table with no row but its closing step", recommend)]
    cause = f"record {path} at #{number}, {len(read.met)} rows met"
    return [_action(card, "hygiene-proof-close", cause,
                    [f"pull request {repo}#{number}", f"record {path} at {branch}",
                     RULE_EVIDENCE], ctx, "Done")]


def superseded(card: dict, ctx: hygiene.Context) -> list:
    """(3) a `PROOF:` card whose parent epic is Canceled or Duplicate."""
    parent = card.get("parent") or {}
    state = (parent.get("state") or {}).get("name")
    if not parent.get("identifier") or state not in SUPERSEDED:
        return []
    cause = f"parent {parent['identifier']} is {state}"
    return [_action(card, "hygiene-card-cancel", cause,
                    [f"parent {parent['identifier']} in {state}"], ctx, "Canceled")]


def _named_today(nodes: list, ctx: hygiene.Context) -> bool:
    tag = hygiene.TAGS["hygiene-cause-name"]
    today = _pt_day(ctx.now)
    for node in nodes:
        head = hygiene.read_receipt(node.get("body") or "")
        at = _when(node.get("createdAt"))
        if head and head["tag"] == tag and at is not None and _pt_day(at) == today:
            return True
    return False


def no_run(card: dict, repo: str, labels: list, ctx: hygiene.Context, cache: dict) -> list:
    """(4) a Todo build card re-dispatched with nothing to show for it."""
    if HAND_BUILT in [name.lower() for name in labels]:
        return []
    nodes = linear_ops.window_nodes(card.get("comments"))  # oldest → newest
    if any((n.get("body") or "").lstrip().startswith(reconcile._LIFE_PREFIXES) for n in nodes):
        return []
    receipts = [n for n in nodes if reconcile._TODO_REDISPATCH_NOTE in (n.get("body") or "")]
    if len(receipts) < MIN_REDISPATCHES:
        return []
    at = _when(receipts[-1].get("createdAt"))
    if at is None or ctx.now - at < timedelta(minutes=lane_contract.stale_minutes()["Todo"]):
        return []
    tied = sorted((r for r in _runs(repo, ctx, cache)
                   if (created := _when(r.get("createdAt"))) is not None
                   and at - TIE_BEFORE <= created <= at + TIE_AFTER),
                  key=lambda r: r.get("createdAt") or "")
    if any(r.get("status") != "completed" for r in tied):
        return []  # queued or running: the card is about to build
    ident, stub, count = card["identifier"], build_stub(repo), len(receipts)
    evidence = [f"re-dispatched {count} times", f"newest re-dispatch at {_moment(at)}",
                f"runs of {stub} in {repo}"]
    failed = next((r for r in tied if r.get("conclusion") in FAILED), None)
    if failed is not None:
        run_id = failed["databaseId"]
        step = failing_step(ctx.gh(["gh", "run", "view", str(run_id), "--repo", repo,
                                    "--log-failed"]))
        if step is None:
            return [_left(ident, f"in Todo, re-dispatched {count} times — run {run_id} "
                                 f"{failed.get('conclusion')} and its log names no failing step",
                          f"a person reads run {run_id} in {repo}")]
        cause = f"run {run_id} failed at step {step}"
        evidence.append(f"run {run_id}")
        recommendation = (f"a person reads run {run_id}'s failing step, {step}, and fixes "
                          "it — every re-dispatch fails there until then")
    elif tied:
        run_id = tied[0]["databaseId"]
        return [_left(ident, f"in Todo, re-dispatched {count} times — run {run_id} "
                             f"{tied[0].get('conclusion')} and posted no heartbeat",
                      f"a person reads run {run_id} in {repo}")]
    else:
        cause = f"no run after the re-dispatch at {_moment(at)}"
        recommendation = (f"a person checks why a dispatch starts no {stub} run in {repo} "
                          "— the relay's delivery, the repo's Actions budget or the stub")
    why = f"in Todo, re-dispatched {count} times with no heartbeat — {cause}"
    row = _left(ident, why, recommendation)
    if _named_today(nodes, ctx):
        return [_left(ident, f"{why}; hyg-cause-named already said on this card today, "
                             "and this lane says it once per PT day", recommendation)]
    return [_action(card, "hygiene-cause-name", cause, evidence, ctx), row]


# --------------------------------------------------------------------------- #
# the lane                                                                     #
# --------------------------------------------------------------------------- #


def _plan_card(card: dict, lane: str, ctx: hygiene.Context, cache: dict) -> list:
    ident = card["identifier"]
    if ident == OWN_PROOF:
        return [_left(ident, "this agent's own epic's proof — the CEO closes it",
                      "the CEO reads the record and closes the card")]
    title = card.get("title") or ""
    labels = hygiene._labels(card)
    has_children = bool((card.get("children") or {}).get("nodes"))
    if linear_ops.epic_branch_refusal(title, labels, has_children) is not None:
        return []
    proof = proof_and_demo.is_proof(title)
    if proof:
        items = superseded(card, ctx)
        if items:
            return items
    repo = _repo(card, ctx)
    if repo is None:
        return []
    if proof:
        return proven(card, repo, ctx, cache)
    pr = _merged_pr(ident, repo, ctx)
    if pr is not None:
        return merged(card, repo, pr, labels, has_children, ctx)
    if lane == "Todo":
        return no_run(card, repo, labels, ctx, cache)
    return []


def plan(board: hygiene.Board, ctx: hygiene.Context) -> list:
    out: list = []
    cache: dict = {}
    seen: set = set()
    for lane in READ_LANES:
        for card in board.cards(ctx, lane):
            ident = card.get("identifier")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            try:
                out += _plan_card(card, lane, ctx, cache)
            except hygiene.Forbidden:
                raise  # a refused read is a lane bug, never a skipped card
            except (RuntimeError, ValueError, KeyError, TypeError) as e:
                print(f"hygiene: {LANE} — {ident} skipped this pass, a read failed: {e}",
                      file=sys.stderr)
    return out
