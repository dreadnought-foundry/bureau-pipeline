#!/usr/bin/env python3
"""The hygiene agent's Triage lane (DRE-5410): the broken-card lane, read once,
its mechanical defects fixed.

Discovered by the core's glob (`scripts/hygiene.py`), registered nowhere. It
reads the cards in `Triage` and fixes the defects the standard already names,
returning a card to the queue — never `Todo`, which dispatches — with the
cause named. The queue is `lane_contract.QUEUE_LANE` (DRE-6748), read at each
write, so it follows the planning exit to Queued when DRE-6742 flips it. One
action per card, the first of these that applies:

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
   in Triage since <ts> past the <N>-hour bound`, where `<ts>` is when the
   wait began — the same on every pass over one hold, and new each time the
   card is parked again — and no age, so the core's key says it once per
   hold. A newer stamp of another reason, a spent stamp, or the stamp without
   the label is no such hold, as in (2).

   **A card whose one automatic repair is spent** (DRE-7183) has a shorter
   bound. The card's newest `🩹 triage-repair:` receipt in the board read's
   window — pipeline-authored, by `viewer { id }` read once a pass and only
   when some comment opens the key, and newest by its own `createdAt` —
   decides: none is the 8-hour bound above, and the row says the Triage
   Repair run (`triage_repair.py`, DRE-7178) takes the card on its next pass.
   A `started` younger than `TRIAGE_REPAIR_RUNNING_HOURS` is a repair in
   flight — a row naming when it started, never an alarm. Anything else is
   spent: `started` that old (the run died with no outcome), `unfinished`, or
   a finished outcome (`rewritten`, `asked`, `replanned`, `split`), which is
   posted only once the card left Triage, so it has parked again. A spent
   card alarms once its wait reaches `HYGIENE_TRIAGE_REPAIRED_ALARM_HOURS`
   (default `DEFAULT_REPAIRED_ALARM_HOURS`, 0 — the first hourly pass that
   sees it; a value that is not zero or positive stops the lane). Causes:
   `held <reason> in Triage since <ts> after its one automatic repair`, `in
   Triage since <ts>: its one automatic repair did not finish`, and `in
   Triage since <ts>: its one automatic repair started <started> and recorded
   no outcome`. The last two are read on a card that no longer wears
   `needs-human`: the repair lifts the hold before it moves the card, so a
   failed move or a stood-down escalation leaves it in Triage unheld. A
   finished receipt scrolled out of the window reads as none — a later alarm,
   never a missed one.
4. **A retired-repo card.** Its `repo:<slug>` label names a slug that is not a
   key of `config/repo-map.json` (cause `retired repo <slug>`), or a repo that
   answers `archived: true` to `gh api repos/<owner>/<repo>` (cause `archived
   repo <owner/repo>`). It is marked `routing_verdict.OPERATOR_STEP_LABEL` —
   a repo that is gone is the operator's to re-point, which is an operator step,
   and `hand-built` is the CEO's mark alone (DRE-6228) — and parked in Queued.
5. **A proof with an open pull request.** The title opens `PROOF:` and
   `card_pr.find` — the one "did this card produce a pull request" seam —
   answers an OPEN pull request in the card's repo. It moves to In Review, the
   lane that pull request says it is in. Cause: `open pull request #<n>`.
6. **A prose blocker with no relation.** `prose_blockers.undeclared_claims`
   names the ids a declaring line claims with no `blockedBy` behind them. Each
   id that resolves — on the board read, or by one `ctx.linear` read per pass —
   gets the relation, which makes the sentence true (a relation to a Done card
   is met and harmless). Cause: `relation added, blocked by <DRE-N[, DRE-M]>`.
   The card returns to Queued only when every claim resolved AND it carries a
   routing verdict (`routing_verdict.verdict_on`): an id that resolves nowhere
   is a sentence only its author can reword, and a card with no verdict has
   nothing for Queued to route on. Then the relation is still added, under a
   `hyg-cause-named` receipt, and a `Left` row says what is missing. Nothing
   rewrites a description.
7. **A dependency loop.** The card's `blockedBy` chain returns to itself
   through the relations on the board read — every card's inverse `blocks`
   relations, and its own `blocks` relations read the other way round, so a
   Done card off the board still closes a loop. When a card in the loop is met
   (`prose_blockers.TERMINAL` — the gate's own reading of a blocker that holds
   nothing) the card returns to Queued, under the same verdict rule as (6).
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
import lane_contract
import linear_ops
import prose_blockers
import routing_verdict
import triage_repair

LANE = "Triage"


def return_lane() -> str:
    """Where every write here returns a card to: the queue, the lane the
    dependency gate and the routing verdict re-evaluate it in. Never Todo.
    `lane_contract.QUEUE_LANE`, read at the write so it follows the planning
    exit (DRE-6748)."""
    return lane_contract.QUEUE_LANE


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
        self.me: str | None = None

    def pipeline_user(self) -> str:
        """The pipeline's own Linear user — one `ctx.linear` read a pass, made
        only when a card carries a repair receipt's line. A viewer Linear does
        not name raises: with no author to vouch for a receipt, none is read."""
        if self.me is None:
            data = self.ctx.linear(VIEWER_QUERY, {}) or {}
            me = (data.get("viewer") or {}).get("id")
            if not me:
                raise ValueError("Linear named no viewer for the pipeline's key")
            self.me = me
        return self.me

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

#: The bound for a card whose one automatic repair is spent (DRE-7183): 0 is
#: the first hourly pass that sees it.
REPAIRED_ALARM_VAR = "HYGIENE_TRIAGE_REPAIRED_ALARM_HOURS"
DEFAULT_REPAIRED_ALARM_HOURS = 0
#: A `started` receipt younger than this is a repair in flight: the `repair`
#: job's 100-minute timeout plus margin.
TRIAGE_REPAIR_RUNNING_HOURS = 2

REPAIR_WAY_BACK = (f"read the {triage_repair.RECEIPT_KEY} receipt, fix what it left open, "
                   "clear needs-human and move the card to Planning")
PLANNING_WAY_BACK = ("read the park note's own way back — clear needs-human and move the "
                     "card to Planning for a fresh planning attempt, or answer the Green "
                     "Light question when the exit asked one; after the card's one "
                     f"automatic repair, {REPAIR_WAY_BACK}")
#: An unrepaired card the Triage Repair run is in scope for.
REPAIR_NEXT_PASS = ("the Triage Repair run takes it on its next pass, the card's one "
                    "automatic repair, while TRIAGE_REPAIR_LIVE is true; otherwise "
                    + PLANNING_WAY_BACK)
REPAIR_RUNNING = ("nothing yet — the Triage Repair run is answering the park note, and "
                  "this agent alarms when the repair has recorded no outcome "
                  f"{TRIAGE_REPAIR_RUNNING_HOURS} hours after it started")

VIEWER_QUERY = "query { viewer { id } }"


def _hours(var: str, default: int, positive: bool) -> int | float:
    """`var`'s number of hours, else `default`. Anything but a finite number
    — positive, or not negative when zero is allowed — is raised, never read
    as no bound at all."""
    raw = (os.environ.get(var) or "").strip()
    if not raw:
        return default
    try:
        hours = float(raw)
    except ValueError:
        hours = math.nan
    if not math.isfinite(hours) or hours < 0 or (positive and hours == 0):
        kind = "positive" if positive else "zero or positive"
        raise ValueError(f"{var}={raw!r} is not a {kind} number of hours")
    return int(hours) if hours.is_integer() else hours


def alarm_hours() -> int | float:
    """How long a planning-held card may wait in Triage before it alarms:
    `HYGIENE_TRIAGE_ALARM_HOURS`, else `DEFAULT_ALARM_HOURS`. Anything but a
    positive number is raised — never read as no bound at all."""
    return _hours(ALARM_VAR, DEFAULT_ALARM_HOURS, positive=True)


def repaired_alarm_hours() -> int | float:
    """How long a card whose one automatic repair is spent may wait in
    Triage: `HYGIENE_TRIAGE_REPAIRED_ALARM_HOURS`, else
    `DEFAULT_REPAIRED_ALARM_HOURS`. Zero or positive; anything else is
    raised."""
    return _hours(REPAIRED_ALARM_VAR, DEFAULT_REPAIRED_ALARM_HOURS, positive=False)


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


def _held_since(card: dict, stamp: dict | None) -> tuple:
    """When the card's wait began, and the evidence for it: its newest entry
    into Triage on the history read, else the stamp's own posting. (None,
    None) when neither is read, or there is no live stamp to fall back on."""
    entered = [_when(n.get("createdAt"))
               for n in ((card.get("history") or {}).get("nodes") or [])
               if ((n or {}).get("toState") or {}).get("name") == LANE]
    entered = [e for e in entered if e is not None]
    if entered:
        newest = max(entered)
        return newest, f"entered Triage {_iso(newest)}"
    if stamp is None:
        return None, None
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


#: What the newest repair receipt makes of the card (DRE-7183): a repair in
#: flight, or one that is spent three ways.
IN_FLIGHT = "in-flight"
REPARKED = "reparked"
DID_NOT_FINISH = "did-not-finish"
NO_OUTCOME = "no-outcome"
#: The spent states rule 3 reads on a card that no longer wears the hold: the
#: repair lifts it before the move, so a move that failed, or an escalation
#: that stood down, after the lift leaves the card in Triage unheld.
SPENT_UNHELD = (DID_NOT_FINISH, NO_OUTCOME)
SPENT_ROW = "so the repair is spent and a person owns it"

_EARLIEST = datetime.min.replace(tzinfo=UTC)


def newest_repair(card: dict, seen: _Pass) -> tuple | None:
    """The card's newest `🩹 triage-repair:` receipt in the board read's
    window, as `(outcome, posted)`, or None. Only a comment the pipeline's
    own user wrote counts, as `triage_repair.is_receipt` reads its own, and
    the newest is the one posted last, whichever way the window runs."""
    nodes = [n for n in ((card.get("comments") or {}).get("nodes") or [])
             if ((n or {}).get("body") or "").lstrip().startswith(triage_repair.RECEIPT_KEY)]
    if not nodes:
        return None
    me = seen.pipeline_user()
    found = [(_when(n.get("createdAt")), i, triage_repair.outcome_of(n.get("body")))
             for i, n in enumerate(nodes) if (n.get("user") or {}).get("id") == me]
    found = [f for f in found if f[2] is not None]
    if not found:
        return None
    posted, _i, outcome = max(found, key=lambda f: (f[0] or _EARLIEST, f[1]))
    return outcome, posted


def repair_state(repair: tuple | None, now: datetime) -> str | None:
    """None for a card never repaired; else what its newest receipt says. A
    `started` younger than `TRIAGE_REPAIR_RUNNING_HOURS` — or of no read
    time — is in flight; older, the run died with no outcome. Every finished
    outcome is posted only once the card has left Triage, so one on a card in
    Triage means it parked again."""
    if repair is None:
        return None
    outcome, posted = repair
    if outcome == triage_repair.STARTED:
        running = timedelta(hours=TRIAGE_REPAIR_RUNNING_HOURS)
        return IN_FLIGHT if posted is None or now - posted < running else NO_OUTCOME
    return DID_NOT_FINISH if outcome == triage_repair.UNFINISHED else REPARKED


def held_planning(card: dict, ctx: hygiene.Context, bound, repaired_bound,
                  seen: _Pass) -> list | None:
    """One row for a card a planning exit parked, naming the reason, its age
    in Triage and the park note — and, once that age reaches its bound, an
    alarm receipt. The bound is `bound` hours for a card never repaired and
    `repaired_bound` for one whose one automatic repair is spent; a repair in
    flight is a row and never an alarm. The cause names when the wait began,
    so the core's (tag, cause) key says the alarm once per park, and again
    when the card is parked anew; an age neither read gives is a row and never
    an alarm."""
    stamp = hold.read_stamp(_bodies(card))
    held = (NEEDS_HUMAN in _labels(card) and stamp is not None
            and stamp["reason"] in PLANNING_REASONS)
    repair = newest_repair(card, seen)
    state = repair_state(repair, ctx.now)
    if not held and state not in SPENT_UNHELD:
        return None
    note = _park_note(card)
    outcome, posted = repair or (None, None)
    started = "at an unknown time" if posted is None else _iso(posted)
    held_as = f"held {stamp['reason']}" if held else ""
    if state == IN_FLIGHT:
        return [_left(card, f"{held_as} in Triage — its one automatic repair started "
                            f"{started} and is still running — {note}", REPAIR_RUNNING)]
    if state is None:
        tail, alarm_after = f" — {note}", bound
        way_back = (REPAIR_NEXT_PASS if stamp["reason"] == triage_repair.IN_SCOPE_REASON
                    else PLANNING_WAY_BACK)
    else:
        what = {REPARKED: "parked again after its one automatic repair",
                DID_NOT_FINISH: "its one automatic repair did not finish",
                NO_OUTCOME: f"its one automatic repair started {started} and recorded "
                            "no outcome"}[state]
        tail = f" — {what}, {SPENT_ROW} — {note}"
        alarm_after, way_back = repaired_bound, REPAIR_WAY_BACK
    since, when = _held_since(card, stamp)
    if since is None:
        place = f"{held_as} in Triage" if held else "in Triage, no longer held"
        return [_left(card, f"{place}, age unknown{tail}", way_back)]
    hours = max(0, int((ctx.now - since).total_seconds() // 3600))
    span = f"{hours} hour{'' if hours == 1 else 's'}"
    place = f"{held_as} for {span} in Triage" if held else f"in Triage for {span}, no longer held"
    row = _left(card, place + tail, way_back)
    if max(ctx.now - since, timedelta(0)) < timedelta(hours=alarm_after):
        return [row]
    ts = _iso(since)
    cause = {None: f"{held_as} in Triage since {ts} past the {alarm_after:g}-hour bound",
             REPARKED: f"{held_as} in Triage since {ts} after its one automatic repair",
             DID_NOT_FINISH: f"in Triage since {ts}: its one automatic repair did not finish",
             NO_OUTCOME: f"in Triage since {ts}: its one automatic repair started {started} "
                         "and recorded no outcome"}[state]
    evidence = [stamp["line"]] if stamp is not None else []
    if outcome is not None:
        evidence.append(f"repair {outcome}" + ("" if posted is None else f" {_iso(posted)}"))
    return [_action(card, ALARM_ACT, cause, [*evidence, when], ctx, [], []), row]


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
                    [hygiene.linear_state(card, return_lane(), park=True)])]


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
    return _left(card, f"{what}, but it carries no routing verdict — "
                       f"{return_lane()} has nothing to route it on",
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
                               [hygiene.linear_state(card, return_lane())]))
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
                    ctx, [], [hygiene.linear_state(card, return_lane())])]


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


def _plan_card(card: dict, ctx: hygiene.Context, seen: _Pass, bound, repaired_bound) -> list:
    held = held_no_route(card)
    for rule in (lambda: moot(card, ctx), lambda: None if held is None else [held],
                 lambda: held_planning(card, ctx, bound, repaired_bound, seen),
                 lambda: retired(card, ctx, seen),
                 lambda: proof_in_review(card, ctx), lambda: prose_blocker(card, ctx, seen),
                 lambda: loop(card, ctx, seen)):
        items = rule()
        if items is not None:
            return items
    return [left_for_a_person(card)]


def plan(board: hygiene.Board, ctx: hygiene.Context) -> list:
    out: list = []
    # A bad bound stops the lane, never one card.
    bound, repaired_bound = alarm_hours(), repaired_alarm_hours()
    seen = _Pass(board, ctx)
    for card in board.cards(ctx, LANE):
        try:
            out += _plan_card(card, ctx, seen, bound, repaired_bound)
        except hygiene.Forbidden:
            raise  # a refused read is a lane bug, never a skipped card
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            print(f"hygiene: {LANE} — {card.get('identifier')} skipped this pass, "
                  f"a read failed: {e}", file=sys.stderr)
    return out
