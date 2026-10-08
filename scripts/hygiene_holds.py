#!/usr/bin/env python3
"""The hygiene agent's holds lane (DRE-6180, DRE-6273): a hold lifts itself
when its reason clears, and the card goes back where the pipeline resumes it.

Discovered by the core's glob (`scripts/hygiene.py`), registered nowhere. The
`needs-human` label is the pipeline's hold, and `scripts/hold.py` (DRE-6173)
is its vocabulary: the newest live `🔒 hold:` stamp on a card names why it is
held and what lifts it. This lane finds every held card in the leg's scope and
takes the label off the ones whose reason has cleared.

## The candidates

Holds outlive the five lanes the core's board read covers, so the lane reads
its own: ONE paged query through the read-only `ctx.linear`, `PAGE` issues a
page, every issue carrying the label in any lane — **archived issues
included**. Linear's issue queries leave an archived issue out unless asked
(`includeArchived: true`), and a Done card the team's auto-archive put away is
exactly the kind the stale labels sit on. Each issue carries the comment
window the board read carries (`linear_ops.COMMENT_WINDOW_GQL`); a window
Linear cut short is read whole for that card, as `hygiene._receipts_on` does.
Each candidate is scoped with `hygiene.in_scope` like every other lane's.

## What lifts, and what does not

1. **Done or Canceled — the universal `card-closed` lift.** Whatever the stamp
   says, and whether there is one: cause `reason=<code> because=card-closed`,
   `reason=manual` for a card with no live stamp. This is how the 207 stale
   labels counted on 2026-10-07 come off.
2. **`run-started` and `unpark-marker`** — the live stamp's reason lifts by
   one of them, and `hold.lift_due` answers that it is met: a 🧠 or ⏳ run
   receipt newer than a `stranded-no-run` stamp, or a budget reset newer than
   a `dead-run-cap` or `turn-cap-park` stamp. Nothing moves the card: the run
   already started, and an operator's `unpark` already moved it. This lane
   reads the run receipt and never starts a run.
3. **`new-head` and `repo-on-rail`** (DRE-6273) — asked of `hold.lift_due`
   with the facts each reads. For `new-head`, the head sha of the card's open
   pull request off the leg's own listing (`Board.prs`): the newest whose
   head branch carries the card's identifier (`card_pr.matches_card`), and no
   pull request means not lifted. For `repo-on-rail`, the card's current
   labels off the candidate read and the slugs of `config/repo-map.json` —
   the live `repo:` label decides, never the stamp's qualifier (DRE-6173).
   These lifts leave a card in a lane nothing resumes, so each takes a third
   write after the label and the receipt (`RETURNS`): a `review-cap-spent`
   card in Green Light (DRE-6181) and a `fix-dispute` or `unfixable-check`
   card in Triage (DRE-6179) go to In Review, the lane their open pull
   request says they are in; a `no-route` card in Triage (DRE-6177, left
   there by the Triage lane, DRE-6190) goes to Planning, whose exit routes it
   afresh. A card in any other lane is two writes and no move. The label is
   off before the move, so the run the move fires is not refused for it.
4. **A `manual` hold** — `epic-rereview-twice`, `plan-critic-bound`, `manual`,
   or the label with no stamp — is a person's, and nothing here lifts it
   outside Done or Canceled. A label over a SPENT stamp (one a newer lift
   line, `hyg-hold-cleared` receipt or budget reset retired, `hold.read_stamp`)
   is the same person's hold, and is reported as a `Left` row naming `manual`
   so somebody sees the label came back; `hold.lift_due` is never asked about
   a spent stamp.
5. **An archived card outside Done or Canceled** is left alone like any other
   open hold.

A lift is two writes, in order: the label off, then the agent's own receipt
(`hygiene.receipt`, act `hygiene-hold-clear`), whose evidence names what was
read — then the lane move, for a lift `RETURNS` names. An archived card is
unarchived for them and re-archived after them — four writes — so the lane
never depends on whether Linear takes a label write on an archived issue, and
the card ends archived as it began; an archived card is never moved, because
only a closed one is lifted. This lane proposes no lane write but In Review
and Planning, and no `gh` write. The core's (tag, cause) key and the label's
absence both keep a lift from repeating: a lifted card is not a candidate on
the next pass.

## The per-pass cap

At most `HYGIENE_HOLDS_MAX_LIFTS` lifts a pass (default `DEFAULT_MAX_LIFTS`,
read before anything else; a value that is not a positive integer raises).
Order inside the cap: the lifts met on an open card first (the four kinds
above, moving or not), oldest stamp first; then the `card-closed` lifts,
oldest stamp first, and the cards with no live stamp last by identifier. Every lift over the cap is a `Left` row
reading `over the per-pass cap of <n> — carried to the next pass`, so the
summary names the carry and an operator can watch it drain.

A read that fails — the candidate query, or a whole-thread read — skips that
read for this pass, said on stderr and never guessed into an action. A read
the core refuses is a lane bug and is raised.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime

import card_pr
import dead_run
import hold
import hygiene
import linear_ops

LANE = "Holds"

ACT = "hygiene-hold-clear"

MAX_LIFTS_VAR = "HYGIENE_HOLDS_MAX_LIFTS"
DEFAULT_MAX_LIFTS = 40

#: Issues a page of the candidate read.
PAGE = 50

#: The lift kinds this lane asks `hold.lift_due` about on an open card.
OPEN_LIFTS = ("run-started", "unpark-marker", "new-head", "repo-on-rail")

#: Where a lift sends the card (DRE-6273): the hold's reason and the lane it
#: parked the card in → the lane the pipeline resumes it in. The third write,
#: after the label and the receipt; any other pairing moves nothing.
RETURNS = {
    ("review-cap-spent", "Green Light"): "In Review",
    ("fix-dispute", "Triage"): "In Review",
    ("unfixable-check", "Triage"): "In Review",
    ("no-route", "Triage"): "Planning",
}

#: Every issue carrying the hold, in any lane, archived ones included.
CANDIDATES_QUERY = """query($after: String) {
  issues(first: %d, after: $after, includeArchived: true,
         filter: { labels: { name: { eqIgnoreCase: "%s" } } }) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id identifier title archivedAt
      state { name }
      labels { nodes { name } }
      children(first: 1) { nodes { identifier } }
      %s
    }
  }
}""" % (PAGE, hold.HOLD_LABEL, linear_ops.COMMENT_WINDOW_GQL)

#: The evidence an archived card's receipt carries beside what was read.
ARCHIVED = "archived — unarchived for this write and re-archived after it"

_IDENT = re.compile(r"^([A-Za-z]+)-([0-9]+)$")
_POSITIVE = re.compile(r"[0-9]+")


def max_lifts() -> int:
    """`HYGIENE_HOLDS_MAX_LIFTS`, or the default when it is unset or empty."""
    raw = (os.environ.get(MAX_LIFTS_VAR) or "").strip()
    if not raw:
        return DEFAULT_MAX_LIFTS
    if not _POSITIVE.fullmatch(raw) or int(raw) < 1:
        raise ValueError(f"{MAX_LIFTS_VAR}={raw!r} is not a positive integer — "
                         "it is the most holds one pass lifts")
    return int(raw)


# --------------------------------------------------------------------------- #
# the read                                                                     #
# --------------------------------------------------------------------------- #


def read_candidates(ctx: hygiene.Context) -> list:
    """Every issue carrying the hold, one paged query through `ctx.linear`,
    read to the end before anything is ordered."""
    nodes: list = []
    after = None
    seen: set = set()
    while True:
        data = ctx.linear(CANDIDATES_QUERY, {"after": after}) or {}
        connection = data.get("issues") or {}
        nodes += connection.get("nodes") or []
        info = connection.get("pageInfo") or {}
        cursor = info.get("endCursor")
        # A server that claims another page without advancing must not hang.
        if not info.get("hasNextPage") or not cursor or cursor in seen:
            return nodes
        seen.add(cursor)
        after = cursor


def _thread(card: dict) -> list:
    """The card's comments oldest→newest, as `{"body", "createdAt"}` — the
    window off the read, or the whole thread where Linear cut it short."""
    window = card.get("comments")
    if linear_ops.window_is_partial(window):
        return [{"body": r.get("body") or "", "createdAt": r.get("created_at")}
                for r in linear_ops.comment_records(card["identifier"], whole_thread=True)]
    return [{"body": n.get("body") or "", "createdAt": n.get("createdAt")}
            for n in linear_ops.window_nodes(window)]


def _first_line(body: str) -> str:
    text = (body or "").strip()
    return text.splitlines()[0] if text else ""


def _labels(card: dict) -> list:
    return [(n or {}).get("name") or "" for n in ((card.get("labels") or {}).get("nodes") or [])]


def _held(card: dict) -> bool:
    return any(name.lower() == hold.HOLD_LABEL for name in _labels(card))


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _stamped_at(stamp: dict, thread: list) -> datetime | None:
    """When the live stamp was posted: its newest posting's `createdAt`."""
    for comment in reversed(thread):
        if _first_line(comment["body"]) == stamp["line"]:
            return _when(comment["createdAt"])
    return None


def _ident_key(ident: str) -> tuple:
    match = _IDENT.match(ident or "")
    return (match.group(1), int(match.group(2))) if match else (ident or "", 0)


# --------------------------------------------------------------------------- #
# one card                                                                     #
# --------------------------------------------------------------------------- #


def _lift(card: dict, ctx: hygiene.Context, reason: str, because: str,
          evidence: list, move_to: str | None = None) -> hygiene.Action:
    """The label off and the receipt, then the lane move when there is one —
    between an unarchive and a re-archive for a card the read said was
    archived."""
    archived = bool(card.get("archivedAt"))
    evidence = [*evidence, ARCHIVED] if archived else list(evidence)
    cause = f"reason={reason} because={because}"
    writes = [hygiene.linear_label(card, hold.HOLD_LABEL, add=False),
              hygiene.linear_comment(card, hygiene.receipt(ACT, cause, evidence, ctx.now))]
    if move_to:
        writes.append(hygiene.linear_state(card, move_to))
    if archived:
        writes = [hygiene.linear_archived(card, archived=False), *writes,
                  hygiene.linear_archived(card, archived=True)]
    return hygiene.Action(lane=LANE, target=card["identifier"], act=ACT, cause=cause,
                          evidence=evidence, writes=writes)


def _left(card: dict, why: str, recommendation: str) -> hygiene.Left:
    return hygiene.Left(lane=LANE, target=card["identifier"], why=why,
                        recommendation=recommendation)


def _open_pull(card: dict, board: hygiene.Board, ctx: hygiene.Context):
    """`(repo, pull request)` for the card's open pull request off the leg's
    listing — the newest whose head branch carries the card's identifier, in
    the repo its `repo:` label maps to, or in any listed repo when it maps to
    none — else None."""
    slug = hygiene._repo_slug(card)
    mapped = ctx.repo_map.get(slug) if slug else None
    repos = [mapped] if mapped else sorted(board.prs)
    found = [(repo, pull) for repo in repos for pull in board.prs.get(repo) or []
             if card_pr.matches_card(pull.get("headRefName"), card["identifier"])]
    newest = card_pr.newest([pull for _, pull in found])
    return next(((repo, pull) for repo, pull in found if pull is newest), None)


def _newer_fact(stamp: dict, thread: list, kind: str) -> str:
    """The live fact that met an open lift, for the receipt's evidence."""
    newer = thread
    for index in range(len(thread) - 1, -1, -1):
        if _first_line(thread[index]["body"]) == stamp["line"]:
            newer = thread[index + 1:]
            break
    if kind == "run-started":
        hit = next((c for c in newer
                    if (c["body"] or "").lstrip().startswith(hold.RUN_RECEIPT_PREFIXES)), None)
        what = "run receipt newer than the stamp"
    else:
        hit = next((c for c in newer if dead_run.RESET_TAG in (c["body"] or "")), None)
        what = "budget reset newer than the stamp"
    when = (hit or {}).get("createdAt")
    return f"{what} at {when}" if when else what


def plan_card(card: dict, ctx: hygiene.Context, board: hygiene.Board):
    """`(order key, Action)` for a lift, a `Left` row, or None."""
    lane = (card.get("state") or {}).get("name")
    thread = _thread(card)
    bodies = [c["body"] for c in thread]
    stamp = hold.read_stamp(bodies)

    if lane in hold.CLOSED_LANES:
        if stamp is None:
            action = _lift(card, ctx, "manual", hold.CARD_CLOSED,
                           [f"lane {lane}", "no live stamp"])
            return (2, None, _ident_key(card["identifier"])), action
        action = _lift(card, ctx, stamp["reason"], hold.CARD_CLOSED,
                       [f"lane {lane}", f"stamp at={stamp['at']}"])
        return (1, _stamped_at(stamp, thread), _ident_key(card["identifier"])), action

    if card.get("archivedAt"):
        return None

    if stamp is None:
        spent = any(_first_line(b).startswith(hold.STAMP_PREFIX) for b in bodies)
        if not spent:
            return None
        return _left(card, "held manual — its newest stamp was spent by a newer lift, "
                           "so the label standing over it is a person's",
                     "a person reads why the label went back on and takes it off when the "
                     "card is free to move; nothing lifts it outside Done or Canceled")

    kind = hold.CONTRACT_LIFTS.get(stamp["reason"])
    if kind not in OPEN_LIFTS:
        return None
    found = _open_pull(card, board, ctx) if kind == "new-head" else None
    if kind == "new-head" and found is None:
        return None
    pr_head = (found[1].get("headRefOid") or None) if found else None
    labels = _labels(card)
    due = hold.lift_due(stamp, lane=lane, labels=labels, pr_head=pr_head,
                        rail_slugs=set(ctx.repo_map), bodies=bodies)
    if due != kind:
        return None
    evidence = [f"stamp at={stamp['at']}"]
    if due == "new-head":
        repo, pull = found
        evidence += [f"open pull request {repo}#{pull.get('number')} head {pr_head}",
                     f"lane {lane}"]
    elif due == "repo-on-rail":
        live = " ".join(n for n in labels if n.lower().startswith("repo:"))
        evidence += [f"label {live} on the rail", f"lane {lane}"]
    else:
        evidence.append(_newer_fact(stamp, thread, due))
    action = _lift(card, ctx, stamp["reason"], due, evidence,
                   RETURNS.get((stamp["reason"], lane)))
    return (0, _stamped_at(stamp, thread), _ident_key(card["identifier"])), action


# --------------------------------------------------------------------------- #
# the lane                                                                     #
# --------------------------------------------------------------------------- #


def _order(key: tuple) -> tuple:
    group, at, ident = key
    # A lift whose stamp time could not be read sorts after the dated ones.
    return (group, at is None, at.timestamp() if at else 0.0, ident)


def plan(board: hygiene.Board, ctx: hygiene.Context) -> list:
    cap = max_lifts()
    try:
        candidates = read_candidates(ctx)
    except hygiene.Forbidden:
        raise  # a refused read is a lane bug, never a skipped pass
    except (RuntimeError, ValueError, KeyError, TypeError) as e:
        print(f"hygiene: {LANE} — the candidate read failed, skipped this pass: {e}",
              file=sys.stderr)
        return []
    lifts: list = []
    rows: list = []
    for card in candidates:
        if not card.get("identifier") or not _held(card) or not hygiene.in_scope(ctx, card):
            continue
        try:
            item = plan_card(card, ctx, board)
        except hygiene.Forbidden:
            raise
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            print(f"hygiene: {LANE} — {card.get('identifier')} skipped this pass, "
                  f"a read failed: {e}", file=sys.stderr)
            continue
        if isinstance(item, hygiene.Left):
            rows.append(item)
        elif item is not None:
            lifts.append(item)
    lifts.sort(key=lambda pair: _order(pair[0]))
    carried = [hygiene.Left(lane=LANE, target=action.target,
                            why=f"over the per-pass cap of {cap} — carried to the next pass",
                            recommendation="wait for the next pass — it lifts the oldest "
                                           "holds first")
               for _, action in lifts[cap:]]
    return [action for _, action in lifts[:cap]] + rows + carried
