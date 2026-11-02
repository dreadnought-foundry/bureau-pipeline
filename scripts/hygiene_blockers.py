#!/usr/bin/env python3
"""The hygiene agent's blocker-owner lane (DRE-8648): a card parked in
`Intake` or `Hand-work` that holds up open work tells its owner, once.

Discovered by the core's glob (`scripts/hygiene.py`), registered nowhere. A
card qualifies when all three hold:

1. it is in `Intake` or `Hand-work` (`WAITING`), and not one of the standing
   cards `config/hand-work-age.json` exempts from the Hand-work age alarm;
2. it has been there more than `HOURS` — measured from its newest move into
   the lane on the board's history read, or from its creation when no move is
   on it (a card created straight into the lane);
3. a `blocks` relation on it names an open card — open meaning not in
   `prose_blockers.TERMINAL`, by the state this lane's own read returns. Only
   relations count, never prose.

For each such card the lane plans one `hyg-blocker-owner` comment on the
blocker itself. It names the card's owner — the assignee, or the creator when
nobody is assigned — as plain text, never an `@mention`; lists every open card
it holds up, directly and through chains of `blocks` relations, each with its
identifier, title, lane and wait; and says the two ways on: finish or cancel
the blocker, or remove the relation. Hygiene writes as the fleet's own Linear
user, so the comment is by a user who is never the person it names; whether
Linear also notifies that person is their own setting, and nothing here reads
or changes it.

**An owner who is not a person is never addressed.** A user whose `app` is
true, whose `name` or `displayName` is a `display_name` in
`config/linear-identities.json`, or who has neither, or an owner the read did
not return, reads `NO_OWNER` in place of a name. The card still gets its one
notice with the same list, and a `Left` row with the same words.

**The lane reads for itself.** The board read carries no owner and no state
for a card a relation names, and a held card in `Queued` is not on the board
at all. So `CARDS_QUERY` reads cards by number through `ctx.linear`, level by
level: the qualifying blockers first, then together every card the previous
level `blocks` that has not been read yet. A card is read once, so a cycle
ends, and at most `CHAIN_READS` levels are read in one pass. A held card's
lane and wait come from this read, never from its absence on the board. A
closed held card holds nobody up, so the chain is not followed past it.

**An incomplete read is said, not hidden.** When any card on a blocker's chain
answers `hasNextPage` on its relations, a card the read did not return, or the
chain is cut at `CHAIN_READS`, the notice is still posted once, listing every
open card it did read, and it ends with `MAY_BE_SHORT`. A request that fails
plans nothing for any blocker that pass — said on stderr — and the next hourly
pass reads again.

The cause is `<blocker> in <lane> since <YYYY-MM-DD>`, the PT date it entered
the lane, so the core's (tag, cause) key tells a card once per stay, and again
when it leaves the lane and comes back later.

This lane returns only `linear_comment` on the blocker, and `Left` rows. It
moves nothing and removes no relation.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import check_linear_identities
import hygiene
import prose_blockers
import reconcile

ACT = "hygiene-blocker-owner-notice"

#: The lanes a blocker waits in. Hygiene reads both and writes neither.
WAITING = ("Intake", hygiene.HAND_WORK)

#: How long a blocker may wait before its owner is told.
HOURS = 48

#: The most levels of `CARDS_QUERY` one pass reads — the blockers, then the
#: cards they hold up, then the cards those hold up, and so on.
CHAIN_READS = 6

#: Cards per request, and the relations each answers.
PAGE = 50

BLOCKS = "blocks"

MAY_BE_SHORT = "Some cards this one holds up could not be read, so this list may be short."
NO_OWNER = "no owner to tell"

#: How much of a held card's title the notice quotes.
TITLE_CHARS = 120

_PT = ZoneInfo("America/Los_Angeles")

#: The one read this lane makes: cards by number, batched and paged, in the
#: shape of `proof_dispatch.LANE_RELATIONS_QUERY`.
CARDS_QUERY = """query($numbers: [Float!], $after: String) {
     issues(first: %d, after: $after, filter: {team: {key: {eq: "DRE"}}, number: {in: $numbers}}) {
       nodes {
         identifier title createdAt state { name }
         history(last: %d) { nodes { createdAt toState { name } } }
         assignee { id name displayName app }
         creator { id name displayName app }
         relations(first: %d) { pageInfo { hasNextPage } nodes { type relatedIssue { identifier } } }
       }
       pageInfo { hasNextPage endCursor } } }""" % (PAGE, PAGE, PAGE)


# --------------------------------------------------------------------------- #
# reading a card                                                               #
# --------------------------------------------------------------------------- #


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _lane(card: dict) -> str | None:
    return (card.get("state") or {}).get("name")


def entered(card: dict, lane: str) -> tuple:
    """When the card entered `lane`, and the evidence for it: its newest move
    into the lane on the history read, else its creation. (None, None) when
    neither is read."""
    moves = [_when(n.get("createdAt"))
             for n in ((card.get("history") or {}).get("nodes") or [])
             if (((n or {}).get("toState") or {}).get("name")) == lane]
    moves = [m for m in moves if m is not None]
    if moves:
        newest = max(moves)
        return newest, f"entered {lane} {_iso(newest)}"
    created = _when(card.get("createdAt"))
    if created is None:
        return None, None
    return created, f"created {_iso(created)} with no move into {lane} on its history"


def _iso(when: datetime) -> str:
    return when.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(when: datetime) -> str:
    return when.astimezone(_PT).strftime("%Y-%m-%d")


def _wait(since: datetime | None, now: datetime) -> str:
    if since is None:
        return "for a time the read did not give"
    hours = max(0, int((now - since).total_seconds() // 3600))
    if hours < 48:
        return f"{hours} hour{'' if hours == 1 else 's'}"
    return f"{hours // 24} days"


def blocks(card: dict) -> list:
    """The cards a `blocks` relation on this card names, in order, once each."""
    out: list = []
    for rel in ((card.get("relations") or {}).get("nodes") or []):
        ident = ((rel or {}).get("relatedIssue") or {}).get("identifier")
        if (rel or {}).get("type") == BLOCKS and ident and ident not in out:
            out.append(ident)
    return out


def _more(card: dict) -> bool:
    return bool(((card.get("relations") or {}).get("pageInfo") or {}).get("hasNextPage"))


def _closed(card: dict) -> bool:
    return _lane(card) in prose_blockers.TERMINAL


def _number(ident: str) -> int | None:
    team, _, number = (ident or "").partition("-")
    return int(number) if team == "DRE" and number.isdigit() else None


# --------------------------------------------------------------------------- #
# the read                                                                     #
# --------------------------------------------------------------------------- #


def read_cards(ctx: hygiene.Context, idents: list) -> dict:
    """Every card of `idents` Linear answers, by identifier — one request per
    page of `PAGE`. A card it does not answer is absent."""
    numbers = [n for n in (_number(i) for i in idents) if n is not None]
    found: dict = {}
    if not numbers:
        return found
    after = None
    while True:
        data = ctx.linear(CARDS_QUERY, {"numbers": numbers, "after": after}) or {}
        issues = data.get("issues") or {}
        for issue in issues.get("nodes") or []:
            if (issue or {}).get("identifier"):
                found[issue["identifier"]] = issue
        page = issues.get("pageInfo") or {}
        if not page.get("hasNextPage") or not page.get("endCursor"):
            return found
        after = page["endCursor"]


def read_chains(ctx: hygiene.Context, blockers: list) -> tuple:
    """The blockers and every card their chains reach, read level by level:
    what each identifier read as, None when Linear did not answer it. A card
    the chain reached but `CHAIN_READS` left unread is absent."""
    read: dict = {}
    frontier = sorted(set(blockers), key=lambda i: (_number(i) or 0, i))
    for _level in range(CHAIN_READS):
        if not frontier:
            break
        got = read_cards(ctx, frontier)
        nxt: list = []
        for ident in frontier:
            read[ident] = got.get(ident)
        for ident in frontier:
            card = read[ident]
            if card is None or (ident not in blockers and _closed(card)):
                continue
            nxt += [h for h in blocks(card) if h not in read and h not in nxt]
        frontier = sorted(nxt, key=lambda i: (_number(i) or 0, i))
    return read


def held_up(blocker: str, read: dict) -> tuple:
    """Every open card `blocker` holds up, directly and through chains, as
    `(identifier, card, via)` — `via` the held card it is held through, None
    for a direct one — and whether the list may be short."""
    short = _more(read[blocker])
    seen = {blocker}
    queue = [(h, None) for h in blocks(read[blocker])]
    held: list = []
    while queue:
        ident, via = queue.pop(0)
        if ident in seen:
            continue
        seen.add(ident)
        card = read.get(ident)
        if card is None:
            short = True  # not answered, or past the chain's last level
            continue
        if _closed(card):
            continue
        held.append((ident, card, via))
        short = short or _more(card)
        queue += [(h, ident) for h in blocks(card) if h not in seen]
    return held, short


# --------------------------------------------------------------------------- #
# the owner                                                                    #
# --------------------------------------------------------------------------- #


def not_people() -> frozenset:
    """Every `display_name` the identity declaration says is not a person,
    case-folded — read off `config/linear-identities.json`, never listed here."""
    rows = check_linear_identities.identities(check_linear_identities.load())
    return frozenset(str(r["display_name"]).casefold() for r in rows if r.get("display_name"))


def owner(card: dict, machines: frozenset) -> str | None:
    """The name to address: the assignee's, or the creator's when nobody is
    assigned. None when that user is not a person, or was not read."""
    user = card.get("assignee") or card.get("creator")
    if not isinstance(user, dict) or user.get("app"):
        return None
    names = [" ".join(str(v).split()) for v in (user.get("name"), user.get("displayName")) if v]
    names = [n for n in names if n]
    if not names or any(n.casefold() in machines for n in names):
        return None
    return names[0]


def standing() -> set:
    """The standing cards `config/hand-work-age.json` exempts."""
    with open(reconcile.HAND_WORK_AGE_CONFIG, encoding="utf-8") as fh:
        return {row["card"] for row in json.load(fh)["standing"]}


# --------------------------------------------------------------------------- #
# what the lane returns                                                        #
# --------------------------------------------------------------------------- #


def _title(card: dict) -> str:
    text = " ".join(str(card.get("title") or "untitled").split())
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS - 1].rstrip() + "…"


def notes(ident: str, lane: str, since: datetime, who: str | None, held: list,
          short: bool, now: datetime) -> list:
    """The lines a person reads under the receipt's evidence."""
    n = len(held)
    lines = [f"{who or NO_OWNER} — {ident} has waited in {lane} since {_day(since)} "
             f"({_wait(since, now)}) and holds up {n} open card{'' if n == 1 else 's'}:"]
    for held_ident, card, via in held:
        state = _lane(card) or "a lane the read did not give"
        lines.append(f"- {held_ident} — {_title(card)} — {state}, waiting "
                     f"{_wait(entered(card, state)[0], now)}"
                     + (f", held through {via}" if via else ""))
    lines += ["", f"Two ways on: finish or cancel {ident}, or remove the relation from "
                  "any card it does not really hold up."]
    if short:
        lines.append(MAY_BE_SHORT)
    return lines


def notice(card: dict, lane: str, since: datetime, evidence: str, read: dict,
           machines: frozenset, ctx: hygiene.Context) -> list:
    """The one action for a qualifying blocker, and its `Left` row when there
    is no owner to tell — or nothing, when it holds no open card up."""
    ident = card["identifier"]
    direct = [h for h in blocks(read[ident]) if read.get(h) is not None and not _closed(read[h])]
    if not direct:
        return []
    held, short = held_up(ident, read)
    who = owner(read[ident], machines)
    cause = f"{ident} in {lane} since {_day(since)}"
    proof = [evidence, *(f"relation {h} blocked by {ident}" for h in direct)]
    body = hygiene.receipt(ACT, cause, proof, ctx.now,
                           notes(ident, lane, since, who, held, short, ctx.now))
    out: list = [hygiene.Action(lane=lane, target=ident, act=ACT, cause=cause,
                                evidence=proof, writes=[hygiene.linear_comment(card, body)])]
    if who is None:
        out.append(hygiene.Left(
            lane=lane, target=ident, why=NO_OWNER,
            recommendation=(f"a person takes {ident} — finish or cancel it, or remove the "
                            f"relation; it holds up {len(held)} open card(s)")))
    return out


def candidates(board: hygiene.Board, ctx: hygiene.Context, exempt: set) -> list:
    """Every card this leg keeps that waits past `HOURS` in a waiting lane
    with a `blocks` relation on the board read, as `(card, lane, since,
    evidence)`."""
    out: list = []
    for lane in WAITING:
        for card in board.cards(ctx, lane):
            if card.get("identifier") in exempt or not blocks(card):
                continue
            since, evidence = entered(card, lane)
            if since is not None and ctx.now - since > timedelta(hours=HOURS):
                out.append((card, lane, since, evidence))
    return out


def plan(board: hygiene.Board, ctx: hygiene.Context) -> list:
    try:
        exempt = standing()
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"hygiene: blockers — skipped this pass, the standing cards could not be "
              f"read: {e}", file=sys.stderr)
        return []
    found = candidates(board, ctx, exempt)
    if not found:
        return []
    try:
        machines = not_people()
        read = read_chains(ctx, [card["identifier"] for card, *_ in found])
    except hygiene.Forbidden:
        raise  # a refused read is a lane bug, never a skipped pass
    except Exception as e:  # noqa: BLE001 — a failed read plans nothing, said once
        print(f"hygiene: blockers — skipped this pass, a read failed: {e}", file=sys.stderr)
        return []
    out: list = []
    for card, lane, since, evidence in found:
        if read.get(card["identifier"]) is None:
            print(f"hygiene: blockers — {card['identifier']} skipped this pass, Linear "
                  "did not answer for it", file=sys.stderr)
            continue
        out += notice(card, lane, since, evidence, read, machines, ctx)
    return out
