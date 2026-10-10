#!/usr/bin/env python3
"""The hygiene agent's Triage lane (DRE-5410): the broken-card lane, read once,
its mechanical defects fixed.

Discovered by the core's glob (`scripts/hygiene.py`), registered nowhere. It
reads the cards in `Triage` and fixes the defects the standard already names,
returning a card to `Backlog` — never `Todo`, which dispatches — with the
cause named. One action per card, the first of these that applies:

1. **A moot card.** Its parent epic is `Canceled` or `Duplicate` on the board
   read: the person decided on the parent, and the card is canceled with that
   written down. Cause: `parent <DRE-N> is <state>`. A card with children is
   an epic, which the core's guard refuses to close — this lane leaves it as a
   row for a person rather than propose a write the guard would stop the whole
   leg on. A moot card is canceled even when it is held: its parent's
   cancellation outranks any hold.
2. **A card held `no-route`** (DRE-6190). It carries `needs-human` and its
   newest live `🔒 hold:` stamp (`hold.read_stamp`) says `reason=no-route`:
   the sweep parked it because its `repo:` label is not on the dispatch rail.
   It is not a broken card this lane fixes — the holds lane owns its lift, and
   sends it from Triage to Planning once the label names a slug on the rail.
   One `Left` row says what the hold is and what lifts it, and no other rule
   runs on it. A newer stamp of another reason, a spent stamp, or the stamp
   without the label is no such hold, and the card reads on as below.
3. **A card held on a planning reason** (DRE-6451). It carries `needs-human`
   and its newest live stamp says `plan-critic-bound` or `epic-rereview-twice`
   (`PLANNING_REASONS`): a planning exit parked it for a person. Nothing here
   lifts it. One `Left` row names the reason, how long it has waited in Triage
   — from its newest Triage entry on the history read, else the stamp's own
   posting, else `age unknown` — and the park note's first line. Once that
   wait reaches `HYGIENE_TRIAGE_ALARM_HOURS` (default `DEFAULT_ALARM_HOURS`; a
   value that is not a positive number stops the lane), a
   `hygiene-triage-alarm` receipt says so on the card. Cause: `held <reason>
   past the <N>-hour bound in Triage`, which carries no age, so the core's key
   says it once. A newer stamp of another reason, a spent stamp, or the stamp
   without the label is no such hold, as in (2).
4. **A retired-repo card.** Its `repo:<slug>` label names a slug that is not a
   key of `config/repo-map.json` (cause `retired repo <slug>`), or a repo that
   answers `archived: true` to `gh api repos/<owner>/<repo>` (cause `archived
   repo <owner/repo>`). It is marked `routing_verdict.OPERATOR_STEP_LABEL` —
   a repo that is gone is the operator's to re-point, which is an operator step,
   and `hand-built` is the CEO's mark alone (DRE-6228) — and parked in Backlog.
5. **A proof with an open pull request.** The title opens `PROOF:` and
   `card_pr.find` — the one "did this card produce a pull request" seam —
   answers an OPEN pull request in the card's repo. It moves to In Review, the
   lane that pull request says it is in. Cause: `open pull request #<n>`.
6. **A prose blocker with no relation.** `prose_blockers.undeclared_claims`
   names the ids a declaring line claims with no `blockedBy` behind them. Each
   id that resolves — on the board read, or by one `ctx.linear` read per pass —
   gets the relation, which makes the sentence true (a relation to a Done card
   is met and harmless). Cause: `relation added, blocked by <DRE-N[, DRE-M]>`.
   The card returns to Backlog only when every claim resolved AND it carries a
   routing verdict (`routing_verdict.verdict_on`): an id that resolves nowhere
   is a sentence only its author can reword, and a card with no verdict has
   nothing for Backlog to route on. Then the relation is still added, under a
   `hyg-cause-named` receipt, and a `Left` row says what is missing. Nothing
   rewrites a description.
7. **A dependency loop.** The card's `blockedBy` chain returns to itself
   through the relations on the board read — every card's inverse `blocks`
   relations, and its own `blocks` relations read the other way round, so a
   Done card off the board still closes a loop. When a card in the loop is met
   (`prose_blockers.TERMINAL` — the gate's own reading of a blocker that holds
   nothing) the card returns to Backlog, under the same verdict rule as (6).
   Cause: `loop <A → B → A> broken, <DRE-N> is <state>`. When every card in it
   is open, which edge to cut is a person's call.

Everything else — a plan parked with `needs-human`, a card with no `repo:`
label, a proof whose pull request is not open yet — is a `Left` row naming
what the person must do, so every Triage card in this leg's scope is either an
action or a row.

No cause carries a count or the pass's time, and this lane adds no stop of its
own on a repeat: the core's (tag, cause) key suppresses a card back in Triage
for the same defect, and one back for a different relation or a different
parent state is a new cause and is fixed again.

A read that fails — a `gh` read, or a Linear read that answers anything but
"not found" — skips that card for this pass, said on stderr and never guessed
into an action. A read the core refuses is a lane bug and is raised.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import UTC, datetime, timedelta

import card_pr
import hold
import hygiene
import linear_ops
import prose_blockers
import routing_verdict

LANE = "Triage"

#: Where every write here returns a card to: the lane the dependency gate and
#: the routing verdict re-evaluate it in. Never Todo.
RETURN_LANE = prose_blockers.RETURN_LANE

#: The parent states that make a child moot.
MOOT_PARENT = ("Canceled", "Duplicate")

PROOF_PREFIX = "PROOF:"
NEEDS_HUMAN = "needs-human"

#: One card by identifier — selecting `identifier`, `state` and `children`,
#: as a card a lane read for itself must.
ISSUE_QUERY = """query($id: String!) { issue(id: $id) {
  identifier state { name } children(first: 1) { nodes { identifier } }
} }"""

#: Linear's answer for an id that names no card, as opposed to a read that
#: failed.
_NOT_FOUND = re.compile(r"\bnot found\b", re.I)

ARROW = " → "


def _bodies(card: dict) -> list:
    return [(n or {}).get("body") or ""
            for n in ((card.get("comments") or {}).get("nodes") or [])]


def _labels(card: dict) -> list:
    return [(n or {}).get("name") or "" for n in ((card.get("labels") or {}).get("nodes") or [])]


def _repo_slug(card: dict) -> str | None:
    for name in _labels(card):
        if name.startswith("repo:"):
            return name[len("repo:"):].strip().lower()
    return None


def _children(card: dict) -> list:
    return (card.get("children") or {}).get("nodes") or []


def _verdict(card: dict) -> str | None:
    """The card's one routing verdict, or None — two different ones are no
    verdict to route on either."""
    try:
        return routing_verdict.verdict_on(_bodies(card))
    except routing_verdict.ConflictingVerdicts:
        return None


def _action(card: dict, act: str, cause: str, evidence: list, ctx: hygiene.Context,
            before: list, after: list) -> hygiene.Action:
    note = hygiene.linear_comment(card, hygiene.receipt(act, cause, evidence, ctx.now))
    return hygiene.Action(lane=LANE, target=card["identifier"], act=act, cause=cause,
                          evidence=list(evidence), writes=[*before, note, *after])


def _left(card: dict, why: str, recommendation: str) -> hygiene.Left:
    return hygiene.Left(lane=LANE, target=card["identifier"], why=why,
                        recommendation=recommendation)


class _Pass:
    """What one pass reads once and shares across the lane's cards."""

    def __init__(self, board: hygiene.Board, ctx: hygiene.Context):
        self.ctx = ctx
        self.cards = {c.get("identifier"): c for cards in board.lanes.values()
                      for c in cards if c.get("identifier")}
        self.blocked_by, self.states = _graph(board)
        self.resolved: dict = {}
        self.archived: dict = {}

    def resolves(self, ident: str) -> bool:
        """Does `ident` name a card? On the board read, or by one read."""
        if ident in self.cards:
            return True
        if ident not in self.resolved:
            try:
                issue = (self.ctx.linear(ISSUE_QUERY, {"id": ident}) or {}).get("issue")
            except linear_ops.LinearError as e:
                if isinstance(e, linear_ops.LinearRateLimited) or not _NOT_FOUND.search(str(e)):
                    raise
                issue = None
            self.resolved[ident] = bool(issue and issue.get("identifier"))
        return self.resolved[ident]

    def is_archived(self, repo: str) -> bool:
        if repo not in self.archived:
            answer = json.loads(self.ctx.gh(["gh", "api", f"repos/{repo}"]) or "{}")
            self.archived[repo] = (answer or {}).get("archived") is True
        return self.archived[repo]


# --------------------------------------------------------------------------- #
# (1) a moot card                                                              #
# --------------------------------------------------------------------------- #


def moot(card: dict, ctx: hygiene.Context) -> list | None:
    parent = card.get("parent") or {}
    state = (parent.get("state") or {}).get("name")
    if not parent.get("identifier") or state not in MOOT_PARENT:
        return None
    if _children(card):
        return [_left(card, f"its parent {parent['identifier']} is {state}, and it has "
                            "children of its own — an epic is never canceled by this agent",
                      "a person decides whether its children's work goes on under "
                      "another epic, then cancels it or moves it")]
    cause = f"parent {parent['identifier']} is {state}"
    return [_action(card, "hygiene-card-cancel", cause, [f"parent {parent['identifier']}"],
                    ctx, [], [hygiene.linear_state(card, "Canceled")])]


# --------------------------------------------------------------------------- #
# (2) a card held no-route                                                     #
# --------------------------------------------------------------------------- #

NO_ROUTE = "no-route"


def held_no_route(card: dict) -> hygiene.Left | None:
    """One row for a card the sweep held `no-route`, or None. The newest live
    stamp decides; the slug is the stamp's own `at` qualifier."""
    stamp = hold.read_stamp(_bodies(card))
    if NEEDS_HUMAN not in _labels(card) or stamp is None or stamp["reason"] != NO_ROUTE:
        return None
    at = stamp["at"]
    if at == "repo:none":
        why = f"held {NO_ROUTE} — the card wears no repo: label"
    else:
        why = f"held {NO_ROUTE} on {at} — the slug is not on the dispatch rail"
    return _left(card, why,
                 "correct the card's repo: label to a slug on the rail, or add the repo "
                 "to config/repo-map.json if it should route; the holds lane reads the "
                 "card's current label, lifts the hold and sends the card to Planning on "
                 "its next pass, and nothing on the card needs clearing")


# --------------------------------------------------------------------------- #
# (3) a card held on a planning reason                                         #
# --------------------------------------------------------------------------- #

#: The planning exits that park a card in Triage with a stamp (DRE-6451): the
#: plan critic's bound (`plan.yml`) and the re-review watcher's second
#: send-back (`rereview_watch.py`).
PLANNING_REASONS = ("plan-critic-bound", "epic-rereview-twice")

ALARM_ACT = "hygiene-triage-alarm"
ALARM_VAR = "HYGIENE_TRIAGE_ALARM_HOURS"
DEFAULT_ALARM_HOURS = 8

#: How a park note opens — `plan_critic.py` and `rereview_watch.py` both.
PARK_PREFIX = "🛑 Parked"
NOTE_CHARS = 160

PLANNING_WAY_BACK = ("read the park note's own way back — clear needs-human and move the "
                     "card to Planning for a fresh planning attempt, or answer the Green "
                     "Light question when the exit asked one")


def alarm_hours() -> int | float:
    """How long a planning-held card may wait in Triage before it alarms:
    `HYGIENE_TRIAGE_ALARM_HOURS`, else `DEFAULT_ALARM_HOURS`. Anything but a
    positive number is raised — never read as no bound at all."""
    raw = (os.environ.get(ALARM_VAR) or "").strip()
    if not raw:
        return DEFAULT_ALARM_HOURS
    try:
        hours = float(raw)
    except ValueError:
        hours = math.nan
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError(f"{ALARM_VAR}={raw!r} is not a positive number of hours")
    return int(hours) if hours.is_integer() else hours


def _first_line(body: str | None) -> str:
    text = (body or "").strip()
    return text.splitlines()[0] if text else ""


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _held_since(card: dict, stamp: dict) -> tuple:
    """When the card's wait began, and the evidence for it: its newest entry
    into Triage on the history read, else the stamp's own posting. (None,
    None) when neither is read."""
    entered = [_when(n.get("createdAt"))
               for n in ((card.get("history") or {}).get("nodes") or [])
               if ((n or {}).get("toState") or {}).get("name") == LANE]
    entered = [e for e in entered if e is not None]
    if entered:
        newest = max(entered)
        return newest, f"entered Triage {_iso(newest)}"
    for node in reversed((card.get("comments") or {}).get("nodes") or []):
        if _first_line((node or {}).get("body")) == stamp["line"]:
            stamped = _when(node.get("createdAt"))
            return (None, None) if stamped is None else (stamped, f"stamped {_iso(stamped)}")
    return None, None


def _park_note(card: dict) -> str:
    """The newest park note's first line, trimmed for a row."""
    for body in reversed(_bodies(card)):
        if body.startswith(PARK_PREFIX):
            return _first_line(body)[:NOTE_CHARS]
    return "the card carries no park note"


def held_planning(card: dict, ctx: hygiene.Context, bound) -> list | None:
    """One row for a card a planning exit parked, naming the reason, its age
    in Triage and the park note — and, once that age reaches `bound` hours,
    an alarm receipt. The core's (tag, cause) key says the alarm once; an age
    neither read gives is a row and never an alarm."""
    stamp = hold.read_stamp(_bodies(card))
    if (NEEDS_HUMAN not in _labels(card) or stamp is None
            or stamp["reason"] not in PLANNING_REASONS):
        return None
    reason = stamp["reason"]
    note = _park_note(card)
    since, when = _held_since(card, stamp)
    if since is None:
        return [_left(card, f"held {reason} in Triage, age unknown — {note}",
                      PLANNING_WAY_BACK)]
    hours = max(0, int((ctx.now - since).total_seconds() // 3600))
    row = _left(card, f"held {reason} for {hours} hour{'' if hours == 1 else 's'} in "
                      f"Triage — {note}", PLANNING_WAY_BACK)
    if ctx.now - since < timedelta(hours=bound):
        return [row]
    cause = f"held {reason} past the {bound:g}-hour bound in Triage"
    return [_action(card, ALARM_ACT, cause, [stamp["line"], when], ctx, [], []), row]


# --------------------------------------------------------------------------- #
# (4) a retired-repo card                                                      #
# --------------------------------------------------------------------------- #


def retired(card: dict, ctx: hygiene.Context, seen: _Pass) -> list | None:
    slug = _repo_slug(card)
    if slug is None:
        return None
    repo = ctx.repo_map.get(slug)
    if repo is None:
        cause, evidence = f"retired repo {slug}", [f"label repo:{slug}", "config/repo-map.json"]
    elif seen.is_archived(repo):
        cause, evidence = f"archived repo {repo}", [f"label repo:{slug}", f"gh api repos/{repo}"]
    else:
        return None
    return [_action(card, "hygiene-triage-return", cause, evidence, ctx,
                    [hygiene.linear_label(card, routing_verdict.OPERATOR_STEP_LABEL, True)],
                    [hygiene.linear_state(card, RETURN_LANE, park=True)])]


# --------------------------------------------------------------------------- #
# (5) a proof with an open pull request                                        #
# --------------------------------------------------------------------------- #


def proof_in_review(card: dict, ctx: hygiene.Context) -> list | None:
    if not (card.get("title") or "").startswith(PROOF_PREFIX):
        return None
    repo = ctx.repo_map.get(_repo_slug(card) or "")
    if repo is None:
        return None
    pull = card_pr.find(card["identifier"], repo=repo,
                        run=lambda args: ctx.gh(["gh", *args]))
    if card_pr.pr_state(pull) != card_pr.OPEN:
        return None
    cause = f"open pull request #{pull['number']}"
    return [_action(card, "hygiene-review-move", cause, [f"{repo}#{pull['number']}"], ctx,
                    [], [hygiene.linear_state(card, "In Review")])]


# --------------------------------------------------------------------------- #
# (6) a prose blocker with no relation                                         #
# --------------------------------------------------------------------------- #


def _no_verdict(card: dict, what: str) -> hygiene.Left:
    return _left(card, f"{what}, but it carries no routing verdict — Backlog has "
                       "nothing to route it on",
                 "a person moves it to Planning, where the planning exit routes it afresh")


def prose_blocker(card: dict, ctx: hygiene.Context, seen: _Pass) -> list | None:
    claims = sorted(prose_blockers.undeclared_claims(card))
    if not claims:
        return None
    found = [i for i in claims if seen.resolves(i)]
    nowhere = [i for i in claims if i not in found]
    out: list = []
    rows: list = []
    if nowhere:
        rows.append(_left(card, f"its blocker line names {', '.join(nowhere)}, which "
                                "names no card",
                          "the sentence must be reworded by its author — set the real "
                          "relation, or reword the line so it no longer opens with "
                          "a declaring phrase"))
    if found:
        cause = f"relation added, blocked by {', '.join(found)}"
        relations = [hygiene.linear_relation(card, i) for i in found]
        evidence = [f"relation {card['identifier']} blocked by {i}" for i in found]
        if nowhere or _verdict(card) is None:
            out.append(_action(card, "hygiene-cause-name", cause, evidence, ctx, relations, []))
            if not nowhere:
                rows.append(_no_verdict(card, f"its blocker line is now true — blocked by "
                                              f"{', '.join(found)}"))
        else:
            out.append(_action(card, "hygiene-triage-return", cause, evidence, ctx, relations,
                               [hygiene.linear_state(card, RETURN_LANE)]))
    return out + rows


# --------------------------------------------------------------------------- #
# (7) a dependency loop                                                        #
# --------------------------------------------------------------------------- #


def _graph(board: hygiene.Board) -> tuple:
    """`blocked_by[X]` — every card the board read says X waits on — and the
    state of every card it names. A card's inverse `blocks` relations say who
    blocks it; its own `blocks` relations say whom it blocks, which is how an
    edge out of a Done card, never on the board read, is still seen."""
    blocked_by: dict = {}
    states: dict = {}
    for cards in board.lanes.values():
        for card in cards:
            me = card.get("identifier")
            if not me:
                continue
            states[me] = (card.get("state") or {}).get("name")
            for rel in (card.get("inverseRelations") or {}).get("nodes") or []:
                issue = rel.get("issue") or {}
                if rel.get("type") == "blocks" and issue.get("identifier"):
                    blocked_by.setdefault(me, set()).add(issue["identifier"])
                    states.setdefault(issue["identifier"],
                                      (issue.get("state") or {}).get("name"))
            for rel in (card.get("relations") or {}).get("nodes") or []:
                other = (rel.get("relatedIssue") or {}).get("identifier")
                if rel.get("type") == "blocks" and other:
                    blocked_by.setdefault(other, set()).add(me)
    return blocked_by, states


def find_loop(start: str, blocked_by: dict) -> list | None:
    """The shortest `blockedBy` chain from `start` back to itself, as the
    cards along it, `start` first — or None."""
    paths = [[start]]
    seen = {start}
    while paths:
        nxt = []
        for path in paths:
            for after in sorted(blocked_by.get(path[-1]) or ()):
                if after == start:
                    return path
                if after not in seen:
                    seen.add(after)
                    nxt.append(path + [after])
        paths = nxt
    return None


def loop(card: dict, ctx: hygiene.Context, seen: _Pass) -> list | None:
    cycle = find_loop(card["identifier"], seen.blocked_by)
    if cycle is None:
        return None
    named = ARROW.join(cycle + [cycle[0]])
    met = next((i for i in cycle if seen.states.get(i) in prose_blockers.TERMINAL), None)
    if met is None:
        return [_left(card, f"dependency loop {named}, every card in it open",
                      "a person decides which card really waits on which, and removes "
                      "the other relation")]
    if _verdict(card) is None:
        return [_no_verdict(card, f"its loop {named} is broken — {met} is "
                                  f"{seen.states[met]}")]
    cause = f"loop {named} broken, {met} is {seen.states[met]}"
    evidence = [f"relation {a} blocked by {b}" for a, b in zip(cycle, cycle[1:] + cycle[:1])]
    return [_action(card, "hygiene-triage-return", cause, evidence + [f"{met} {seen.states[met]}"],
                    ctx, [], [hygiene.linear_state(card, RETURN_LANE)])]


# --------------------------------------------------------------------------- #
# everything else                                                              #
# --------------------------------------------------------------------------- #


def left_for_a_person(card: dict) -> hygiene.Left:
    if NEEDS_HUMAN in _labels(card):
        return _left(card, f"parked with {NEEDS_HUMAN} — a person owns it",
                     "a person reads the park receipt on the card and decides how it is "
                     f"finished, then clears {NEEDS_HUMAN}")
    if _repo_slug(card) is None:
        return _left(card, "it carries no repo: label, so nothing routes it",
                     "a person sets the repo: label naming the repo its files live in")
    return _left(card, "in Triage with no mechanical defect this agent fixes",
                 "a person reads the card's newest refusal and fixes what it names")


# --------------------------------------------------------------------------- #
# the lane                                                                     #
# --------------------------------------------------------------------------- #


def _plan_card(card: dict, ctx: hygiene.Context, seen: _Pass, bound) -> list:
    held = held_no_route(card)
    for rule in (lambda: moot(card, ctx), lambda: None if held is None else [held],
                 lambda: held_planning(card, ctx, bound),
                 lambda: retired(card, ctx, seen),
                 lambda: proof_in_review(card, ctx), lambda: prose_blocker(card, ctx, seen),
                 lambda: loop(card, ctx, seen)):
        items = rule()
        if items is not None:
            return items
    return [left_for_a_person(card)]


def plan(board: hygiene.Board, ctx: hygiene.Context) -> list:
    out: list = []
    bound = alarm_hours()  # a bad bound stops the lane, never one card
    seen = _Pass(board, ctx)
    for card in board.cards(ctx, LANE):
        try:
            out += _plan_card(card, ctx, seen, bound)
        except hygiene.Forbidden:
            raise  # a refused read is a lane bug, never a skipped card
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            print(f"hygiene: {LANE} — {card.get('identifier')} skipped this pass, "
                  f"a read failed: {e}", file=sys.stderr)
    return out
