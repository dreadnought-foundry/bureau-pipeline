#!/usr/bin/env python3
"""The hygiene agent's Green Light lane (DRE-5372): the CEO's decision queue,
cleared of the rows that were never a decision.

Discovered by the core's glob (`scripts/hygiene.py`), registered nowhere. It
reads each card in `Green Light` once and sorts it by the newest planning
receipt on it — a limit-death marker, a plan-critic record, a transport
receipt or a planning escalation; this agent's own receipts and every other
comment are read past. A receipt counts only when the pipeline's own Linear
user wrote it — `viewer { id }`, read once a pass through `ctx.linear` — since
anyone with comment access can post a receipt's line (DRE-2721,
`plan_critic.trusted_bodies`). Five kinds are mechanical, and each goes back
to `Planning` with one `hyg-resent-to-planning` receipt naming the cause:

1. **A dead planner.** The newest receipt is `dead_run`'s `🪦 limit-death:`
   marker with `stage=plan` — the card was never planned, its planner hit a
   wall. A `plan-critic: … result=PASS` record newer than the marker would be
   the newest receipt instead, so a plan that passed after the death is never
   read as one. Cause: `planner died, limit-death kind=<kind> stage=plan at
   <the marker's time>`. `scripts/death_receipt.py` writes its receipt to a
   run artifact and never to a card, so the marker is the one death this lane
   can read off a card.
2. **A classifier transport failure.** The newest receipt is
   `🔌 planning-classify-transport` — a 429 or a 5xx, not a question. Cause:
   `classifier transport failure at <the receipt's time>`. The SECOND
   failure on a card is not this receipt but a `🙋 planning-escalation`
   written with `transport=True`; this kind is keyed on the `🔌` receipt
   alone, so that escalation is left for a person like any other.
3. **A passed plan with a child missing criteria.** The newest receipt is a
   plan-critic PASS on an epic, and `plan_critic.cards_without_acceptance`
   names one of its children. The children's bodies are not on the board, so
   they are read once per such epic through `ctx.linear`. Cause: `child
   <DRE-N> has no acceptance criteria`.
4. **A split or access escalation.** A `🙋 planning-escalation` whose stated
   reason says the work is too big for one pull request, or that the work
   needs a credential, a grant or an access no agent has — the planner splits
   a card and routes an operator card, not the CEO. The words must be about
   the work: "split this work into two phases", "too large for mobile" or "an
   agent lacks the finance context" is a business question, and stays one.
   The reason is quoted in the receipt's evidence. Cause: `escalation asks for
   <split | access>, posted <the escalation's time>`.
5. **A stall park.** A `🙋 planning-escalation` whose reason is the Planning
   stall watchdog's ("planning has produced nothing") or the planner line's
   ("waiting in line for a planner") — a run that died or a slot that never
   came. Cause: `stall park at <the park's time>`. Since DRE-5286 both parks
   land in Triage under their own tag, so this kind stops matching once the
   parks made before it have cleared.

Every time in a cause is the receipt's own `createdAt`, never the pass's, so
the same receipt reads as the same cause every hour and the core's (tag,
cause) key gives it one re-send.

**One stop of this lane's own, stricter than the key.** A card already
carrying a stall re-send younger than `STOP_HOURS`, parked again by a newer
receipt, is not re-sent: the new park is a new cause the key would pass, and
a second stall in a row is a pipeline fault to look at rather than a card to
keep bouncing. The row is `Left`, naming both parks.

**Everything else is left.** An escalation that is a real question gets no
write and one `Left` row whose recommendation is the escalation's own
recommendation line: the note's declared Recommendation line when it
carries the three lines (`console_escalation`, DRE-3908 and DRE-6196), read
off the whole note since those lines sit above the `**Why it needs you:**`
block, and otherwise a recommendation line in that block. A plan both critics
passed, with every child carrying criteria, yields nothing: it waits on the CEO's approval, which is his act
(DRE-5268) and never this agent's. Any other row is `Left` for a person.

This lane returns only `linear_comment` and `linear_state(card, "Planning")`.
It never approves a plan and never writes a build lane — the core's guard
refuses that write whatever a lane returns.

A `ctx.linear` read that fails skips that epic for this pass — said on stderr,
never guessed into an action. A viewer read that fails, or names nobody,
skips the whole lane for this pass the same way: with no author to vouch for
a receipt, no receipt is read.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta

import board_snapshot
import dead_run
import hygiene
import linear_ops
import plan_critic
import planning_escalation

LANE = "Green Light"

ACT = "hygiene-resend-to-planning"
DESTINATION = "Planning"

#: The lane's own stop: a stall re-send younger than this holds the next one.
STOP_HOURS = 6

#: How the two stall parks' reasons open — `reconcile.stalled_planning_reason`
#: and `reconcile.waiting_too_long_reason`, before and since DRE-5286. Pinned
#: to those functions by the lane's tests.
STALL_OPENINGS = (
    "planning has produced nothing",
    "this card has been waiting in line for a planner",
)
STALL_CAUSE = "stall park at "

#: A reason asking for a split: the work is too big for one pull request. Every
#: alternative names that size — "split this work into two phases" or "split
#: by region" says nothing about it and is a rollout question, and so is "too
#: large for mobile" or "too broad a pilot".
SPLIT = re.compile(
    r"\btoo (?:big|large|broad|much) for (?:one|a single) (?:pull request|card)\b"
    r"|\b(?:does not|doesn't|will not|won't|cannot|can't|would not|wouldn't) fit "
    r"(?:in|into) (?:one|a single) pull request\b"
    r"|\bmore than one pull request\b"
    r"|\b(?:two|three|four|five|several|multiple|separate) pull requests\b",
    re.I,
)
_ACCESS = r"(?:credentials?|grants?|access|permissions?|tokens?)"
#: A reason asking for access: the work needs a credential, grant or access …
ACCESS_NOUN = re.compile(
    rf"\b(?:needs?|requires?)\s+(?:an?\s+|the\s+|its\s+)?(?:[\w'-]+\s+){{0,3}}?{_ACCESS}\b",
    re.I,
)
#: … that no agent has — said of that same thing, by name or as "one", "it" or
#: "that". Both are required: "should partners get access" is a business
#: question, not a missing grant, and so is "an agent lacks the finance
#: context" or "no agent has met the partner" with nothing the work needs.
AGENT_LACKS = re.compile(
    r"(?:\bno agent (?:has|holds|is granted|can \w+)"
    r"|\b(?:an agent|agents|the fleet|the pipeline) (?:lacks?|does not have|doesn't have"
    r"|do not have|don't have|has no|have no|is not granted|are not granted)"
    r"|\bonly (?:the|an) operator (?:has|holds|can \w+))"
    r"\s+(?:(?:one|it|them|that|those|this|these)"
    r"(?=\s*(?:[.,;:!?—–-]|$|\s(?:and|or|so|but|yet|today|now)\b))"
    rf"|(?:an?\s+|the\s+|its\s+|any\s+)?(?:[\w'-]+\s+){{0,3}}?{_ACCESS}\b)",
    re.I,
)
#: `Recommendation: …`, bold or not, and `Recommended — …`: the label and any
#: markup around it are not part of what is recommended.
_RECOMMEND_LINE = re.compile(
    r"^[\W_]*recommend(?:ation|ed)?[*_\s]*[:—-][*_\s]*(?P<text>\S.*)$", re.I)
_RECOMMEND_WORD = re.compile(r"recommend", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WHY = re.compile(r"\*\*Why it needs you:\*\*\s*(?P<reason>.*?)(?:\n\nThis card is parked in |\Z)",
                  re.S)

#: How much of an escalation's reason the receipt quotes.
QUOTE_CHARS = 300

#: Who the key this pass runs under is — the fleet user in the workflow, the
#: author of every receipt this lane reads (`linear_ops.comment_records`).
VIEWER_QUERY = "query { viewer { id } }"

CHILDREN_QUERY = (
    "query($id: String!) { issue(id: $id) { identifier "
    "children(first: %d) { nodes { identifier description state { name } } } } }"
    % board_snapshot.CHILD_PAGE
)
#: A child in one of these lanes is not part of the plan any more.
GONE = ("Canceled", "Duplicate")

NO_RECOMMENDATION = ("the escalation states no recommendation line — the question "
                     "is on the card")


# --------------------------------------------------------------------------- #
# reading the card                                                             #
# --------------------------------------------------------------------------- #


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _stamp(when: datetime) -> str:
    """A receipt's own time, as a person reads it: `2026-09-30 05:41 PT`."""
    return dead_run.pacific(when)


def escalation_reason(body: str) -> str:
    """The reason an escalation note states, as `escalation_comment` wrote it."""
    match = _WHY.search(body or "")
    return (match.group("reason") if match else "").strip()


def _is_escalation(first: str) -> bool:
    tag = f" {planning_escalation.ESCALATION_TAG}:"
    return first.startswith((f"{planning_escalation.ESCALATION_MARK}{tag}",
                             f"{planning_escalation.REWRITE_MARK}{tag}"))


def pipeline_user(ctx: hygiene.Context) -> str | None:
    """The pipeline's own Linear user id — one `ctx.linear` read."""
    data = ctx.linear(VIEWER_QUERY, {}) or {}
    return (data.get("viewer") or {}).get("id") or None


def pipeline_nodes(card: dict, me: str) -> list:
    """The card's comments the pipeline's own user wrote, oldest→newest. An
    integration's comment has no user and is nobody's."""
    return [node for node in linear_ops.window_nodes(card.get("comments"))
            if ((node.get("user") or {}).get("id")) == me]


def classify(body: str) -> dict | None:
    """What a comment is to this lane, or None when it is not a planning
    receipt this lane reads. Handed only comments the pipeline wrote."""
    first = (body or "").split("\n", 1)[0].strip()
    marker = dead_run.parse_limit_marker(body)
    if marker is not None:
        return {"kind": "limit-death", "marker": marker}
    records = plan_critic.parse_markers([body])
    if records:
        return {"kind": "critic", "result": records[-1]["result"]}
    if first.startswith(f"{planning_escalation.TRANSPORT_MARK} "
                        f"{planning_escalation.TRANSPORT_TAG}:"):
        return {"kind": "transport"}
    if _is_escalation(first):
        return {"kind": "escalation",
                "question": first.startswith(planning_escalation.ESCALATION_MARK),
                "reason": escalation_reason(body)}
    return None


def newest_receipt(card: dict, me: str) -> dict | None:
    """The newest planning receipt the pipeline wrote on the card, with its
    time and the body it was classified from, or None."""
    newest = None
    for node in pipeline_nodes(card, me):
        body = node.get("body") or ""
        found = classify(body)
        when = _when(node.get("createdAt"))
        if found is not None and when is not None:
            newest = {**found, "at": when, "iso": node.get("createdAt"), "body": body}
    return newest


def stall_resend(card: dict, me: str) -> dict | None:
    """This agent's newest stall re-send on the card: its cause and time."""
    newest = None
    for node in pipeline_nodes(card, me):
        head = hygiene.read_receipt(node.get("body") or "")
        when = _when(node.get("createdAt"))
        if (head and head["tag"] == hygiene.TAGS[ACT]
                and head["cause"].startswith(STALL_CAUSE) and when is not None):
            newest = {"cause": head["cause"], "at": when}
    return newest


def asks_for(reason: str) -> str | None:
    """`split` or `access` when the escalation asks for one, else None."""
    if SPLIT.search(reason):
        return "split"
    if ACCESS_NOUN.search(reason) and AGENT_LACKS.search(reason):
        return "access"
    return None


def is_stall(reason: str) -> bool:
    return reason.lower().startswith(STALL_OPENINGS)


def _declared(note: str | None) -> str | None:
    """The note's declared Recommendation line as the line carries it —
    `<answer> — <why>`, or `none given — <why>` — or None when the note
    declares no lines. `console_escalation` is the one parser of the lines
    (DRE-3908), imported here so a checkout without it reads today's way."""
    if not note:
        return None
    try:
        import console_escalation
    except ImportError:
        return None
    esc = console_escalation.parse(note)
    if esc is None:
        return None
    answer = (esc.recommendation if esc.recommendation is not None
              else console_escalation.NONE_GIVEN)
    if not esc.why:
        return answer
    return f"{answer}{console_escalation.SEPARATOR}{esc.why}".strip()


def recommendation(reason: str, note: str | None = None) -> str:
    """The escalation's recommendation. A `note` — the whole comment body —
    that declares the three lines answers with its Recommendation line
    (DRE-6196). Otherwise the reason's own recommendation line — the planner
    ends its reason with one (`briefs/planner.md`) — or a sentence saying it
    has none."""
    declared = _declared(note)
    if declared is not None:
        return declared
    lines = [line.strip() for line in reason.splitlines() if line.strip()]
    for line in reversed(lines):
        match = _RECOMMEND_LINE.match(line)
        if match:
            return match.group("text").strip()
    for line in reversed(lines):
        sentences = [s for s in _SENTENCE.split(line) if _RECOMMEND_WORD.search(s)]
        if sentences:
            return sentences[-1].strip()
    return NO_RECOMMENDATION


def _quote(reason: str) -> str:
    """The reason on one line, fit for an evidence item."""
    text = " ".join(reason.split())
    text = re.sub(r",(?:\s*,)+", ",", text)
    if len(text) > QUOTE_CHARS:
        text = text[:QUOTE_CHARS - 1].rstrip() + "…"
    return f'reason "{text}"'


# --------------------------------------------------------------------------- #
# what the lane returns                                                        #
# --------------------------------------------------------------------------- #


def resend(card: dict, cause: str, evidence: list, ctx: hygiene.Context) -> hygiene.Action:
    """The one action this lane takes: a receipt naming the cause, then the
    card back to Planning."""
    note = hygiene.linear_comment(card, hygiene.receipt(ACT, cause, evidence, ctx.now))
    return hygiene.Action(lane=LANE, target=card["identifier"], act=ACT, cause=cause,
                          evidence=list(evidence),
                          writes=[note, hygiene.linear_state(card, DESTINATION)])


def left(card: dict, why: str, recommend: str) -> hygiene.Left:
    return hygiene.Left(lane=LANE, target=card["identifier"], why=why,
                        recommendation=recommend)


def children_without_criteria(card: dict, ctx: hygiene.Context) -> list:
    """The epic's children `plan_critic` says carry no acceptance criteria —
    one `ctx.linear` read."""
    data = ctx.linear(CHILDREN_QUERY, {"id": card["identifier"]}) or {}
    nodes = (((data.get("issue") or {}).get("children") or {}).get("nodes")) or []
    live = [{"identifier": n.get("identifier"), "body": n.get("description") or ""}
            for n in nodes if ((n.get("state") or {}).get("name")) not in GONE]
    return sorted(plan_critic.cards_without_acceptance(live),
                  key=lambda ident: (len(ident or ""), ident or ""))


def _stall(card: dict, receipt: dict, ctx: hygiene.Context,
           me: str) -> hygiene.Action | hygiene.Left:
    cause = f"{STALL_CAUSE}{_stamp(receipt['at'])}"
    prior = stall_resend(card, me)
    if (prior is not None and prior["cause"] != cause
            and ctx.now - prior["at"] < timedelta(hours=STOP_HOURS)):
        return left(
            card,
            f"parked for a stall twice inside {STOP_HOURS} hours — {prior['cause']}, "
            f"re-sent to Planning at {_stamp(prior['at'])}, then {cause}",
            "look at why planning stalls on this card before it goes back — a second "
            "stall in a row is a pipeline fault, not a card to keep bouncing")
    return resend(card, cause, [f"stall park comment {receipt['iso']}"], ctx)


def _escalation(card: dict, receipt: dict, ctx: hygiene.Context, me: str):
    reason = receipt["reason"]
    stamp = _stamp(receipt["at"])
    if receipt["question"] and is_stall(reason):
        return _stall(card, receipt, ctx, me)
    wants = asks_for(reason) if receipt["question"] else None
    if wants is not None:
        return resend(card, f"escalation asks for {wants}, posted {stamp}",
                      [f"escalation comment {receipt['iso']}", _quote(reason)], ctx)
    return left(card, f"the planner asks a question, posted {stamp}",
                recommendation(reason, note=receipt.get("body")))


def plan_card(card: dict, ctx: hygiene.Context, me: str):
    """What this lane does about one card: an action, a left row, or None.
    `me` is the pipeline's own user, the one author whose receipts count."""
    receipt = newest_receipt(card, me)
    if receipt is None:
        return left(card, "it carries no planning receipt this lane reads",
                    "a person reads the card — nothing on it says why it waits here")
    kind = receipt["kind"]
    stamp = _stamp(receipt["at"])
    if kind == "limit-death":
        marker = receipt["marker"]
        if marker["stage"] == "plan":
            cause = (f"planner died, {dead_run.LIMIT_TAG} kind={marker['kind']} "
                     f"stage=plan at {stamp}")
            return resend(card, cause, [f"run {marker['run']}",
                                        f"limit-death comment {receipt['iso']}"], ctx)
        return left(card, f"its newest receipt is a {marker['stage']}-stage limit death, "
                          f"posted {stamp}",
                    "a person reads the card — a death outside planning is not this "
                    "lane's to re-send")
    if kind == "transport":
        return resend(card, f"classifier transport failure at {stamp}",
                      [f"transport comment {receipt['iso']}"], ctx)
    if kind == "critic":
        if receipt["result"] != "PASS":
            return left(card, f"its newest critic record reads {receipt['result']}, "
                              f"posted {stamp}",
                        "a person reads the card — a plan the critics did not pass "
                        "should not be waiting here")
        if not (card.get("children") or {}).get("nodes"):
            return None
        missing = children_without_criteria(card, ctx)
        if not missing:
            return None  # a passed plan: the CEO's approval, never this agent's
        cause = (f"child {missing[0]} has no acceptance criteria" if len(missing) == 1
                 else f"children {', '.join(missing)} have no acceptance criteria")
        return resend(card, cause, [*(f"child {m}" for m in missing),
                                    f"plan-critic PASS {receipt['iso']}"], ctx)
    return _escalation(card, receipt, ctx, me)


def plan(board: hygiene.Board, ctx: hygiene.Context) -> list:
    cards = list(board.cards(ctx, LANE))
    if not cards:
        return []
    try:
        me = pipeline_user(ctx)
    except hygiene.Forbidden:
        raise
    except (RuntimeError, ValueError, KeyError, TypeError) as e:
        me, why = None, f"the pipeline's Linear user could not be read: {e}"
    else:
        why = "Linear named no viewer for the pipeline's key"
    if me is None:
        print(f"hygiene: {LANE} — skipped this pass, {why}; no receipt can be "
              f"told from a stranger's", file=sys.stderr)
        return []
    out: list = []
    for card in cards:
        try:
            item = plan_card(card, ctx, me)
        except hygiene.Forbidden:
            raise  # a refused read is a lane bug, never a skipped card
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            print(f"hygiene: {LANE} — {card.get('identifier')} skipped this pass, "
                  f"a read failed: {e}", file=sys.stderr)
            continue
        if item is not None:
            out.append(item)
    return out
