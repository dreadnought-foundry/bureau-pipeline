#!/usr/bin/env python3
"""What every hygiene hold is waiting on, in one read (DRE-9274).

    python3 scripts/hygiene.py read --out board.json
    python3 scripts/hold_waits.py --board board.json

The week ending 2026-11-07 (DRE-9269) put two holds at the top of the table —
`hyg-hand-step` and `hyg-blocker-owner`, with `hand-work-overdue` beside them,
128 stops and over 4,683 hours — and could not say what they wait on. This
read answers that for every open hold of the three kinds in `TAGS`, grouped by
the words of the hold's own receipt, so the answer is a short list of kinds
rather than a row per card. It reads and nothing else: no hold is lifted, no
card moved, nothing posted.

## Where a hold is read

The board file `hygiene.py read` writes, `{"taken_at": …, "lanes": {<lane>:
[card, …]}}`, each card in `board_snapshot.CARD_QUERY`'s shape. Each kind's
receipt is a comment on the card that holds it:

* `hyg-blocker-owner` — `hygiene_blockers`, through `hygiene.receipt`. Its
  cause is `<card> in <lane> since <YYYY-MM-DD>`, and its evidence line names
  `relation <held> blocked by <card>` for every card it holds up.
* `hyg-hand-step` — `hygiene_hand_step`, through `hygiene.receipt`. Its cause
  is `<class>, kind <kind>`, and its evidence line is the deciding phrase.
* `hand-work-overdue` — `reconcile.py`'s two sites, through
  `pipeline_act.receipt`, opening `OVERDUE_MARK` — `… has waited in Hand-work
  <h> hours, …` or `… has waited <lane> for the operator <h> hours, …` — with
  `Kind: <clause> — <detail>.` on its second line.

The two hygiene receipts are parsed with `hygiene.read_receipt`; the alarm is
recognized by its opening, the test `reconcile.py` finds its own alarm by.

**Open.** A hold is open when the card is in the lane its kind stands in and
carries that kind's receipt for its current stay there: for
`hyg-blocker-owner` the lane its cause names, and for `hand-work-overdue` the
lane its first line names, with the receipt newer than the card's newest entry
into that lane (`hygiene_blockers.entered`); for `hyg-hand-step` `Hand-work`,
while the card wears the `hand-step:<class>` label its cause names — that
receipt is posted once per card, not once per stay. Of a kind's receipts for
the stay the newest is the hold. Standing hours run from the receipt to the
board's `taken_at`, and for `hyg-hand-step` from the later of the receipt and
the card's newest entry into `Hand-work`.

**What it waits on.** `hyg-blocker-owner` waits on the cards it holds up. Their
states come from `hygiene_blockers.read_cards` through a read-only hygiene
context, because a held-up card is often in a lane the board file does not
hold; a card that read does not answer reads `state unread`, never closed.
The other two wait on the card's own step, unless a `blocks` relation in its
`inverseRelations` names a blocker. A card is closed when its state is in
`prose_blockers.TERMINAL`.

## The groups

Read off the receipt's own words, never invented: `hyg-hand-step` by its cause
as written; `hyg-blocker-owner` by the lane in its cause, then `holds up open
work`, `holds up nothing open` or `state unread`; `hand-work-overdue` by the
`Kind:` line's lead clause, the text before its first `—`, a trailing period
dropped.

**A receipt that does not fit is still shown**, in its kind's `UNREADABLE`
group with its first line quoted: a hygiene receipt `read_receipt` will not
parse, a cause not in its kind's shape, a blocker notice naming no held card,
an alarm with no `Kind:` line or a first line in neither form, and an empty or
whitespace-only cause or clause. With its cause unread, such a receipt stands
in the lane its kind can: `Hand-work` for `hyg-hand-step`, the card's own lane
for the other two — and for `hyg-blocker-owner` only a lane it waits in
(`hygiene_blockers.WAITING`).

**A cut-short window.** A card in `Intake` or `Hand-work`, or a `PROOF:` card
in any lane, whose comment window Linear cut short
(`linear_ops.window_is_partial`) and that shows none of the three receipts is
listed under `CUT_SHORT`, never counted as unheld.

## The output

`hold-waits: taken_at=<PT> lanes=<…> open=<n> hyg-hand-step=<n>
hyg-blocker-owner=<n> hand-work-overdue=<n> unreadable=<n> cut-short=<n>` —
`open` is every hold listed, each kind's count its readable holds, and
`unreadable` the rest. Then the groups, largest total standing hours first:
one header each — kind, group, count, total hours, oldest hours, and how many
of its holds wait on an open card, a closed one, the step itself, or a card
the read did not answer — and one line per hold under it. The cut-short cards
come last.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hold_deadline  # noqa: E402
import hygiene  # noqa: E402
import hygiene_blockers  # noqa: E402
import hygiene_hand_step  # noqa: E402
import linear_ops  # noqa: E402
import proof_and_demo  # noqa: E402
import prose_blockers  # noqa: E402

HAND_STEP = hygiene.TAGS[hygiene_hand_step.ACT]
BLOCKER_OWNER = hygiene.TAGS[hygiene_blockers.ACT]
OVERDUE = hold_deadline.HAND_WORK_TAG
#: The three holds this read lists, in the order the opening line counts them.
TAGS = (HAND_STEP, BLOCKER_OWNER, OVERDUE)

#: The opening of a `hand-work-overdue` alarm — the test `reconcile.py` finds
#: its own by.
OVERDUE_MARK = f"🚨 {OVERDUE}:"

UNREADABLE = "unreadable receipt"
CUT_SHORT = "window cut short — not read"
STATE_UNREAD = "state unread"

#: What a hold waits on, as its group's header counts it.
OPEN, CLOSED, STEP, UNREAD = "open", "closed", "step", "unread"

_PT = ZoneInfo("America/Los_Angeles")

_HYGIENE_HEAD = re.compile(
    rf"^{re.escape(hygiene.RECEIPT_MARK)} (?P<tag>{re.escape(HAND_STEP)}|"
    rf"{re.escape(BLOCKER_OWNER)})(?![a-z0-9-])")
_BLOCKER_CAUSE = re.compile(r"^(?P<card>\S+) in (?P<lane>\S.*?) since \d{4}-\d{2}-\d{2}$")
_HAND_STEP_CAUSE = re.compile(
    rf"^(?P<cls>{'|'.join(map(re.escape, hygiene_hand_step.LABELS))}), kind (?P<kind>[a-z-]+)$")
_HELD = re.compile(r"^relation (?P<held>\S+) blocked by \S+$")
#: The two first-line forms of the alarm — the second site's first, since a
#: lane named in it never opens with the first form's lowercase `in `.
_OVERDUE_FOR_OPERATOR = re.compile(
    rf"^{re.escape(OVERDUE_MARK)} \S+ has waited (?P<lane>\S.*?) for the operator \d+ hours,")
_OVERDUE_IN_LANE = re.compile(rf"^{re.escape(OVERDUE_MARK)} \S+ has waited in (?P<lane>\S.*?) \d+ hours,")
_KIND = "Kind:"


@dataclass
class Receipt:
    kind: str
    at: datetime | None
    first_line: str
    reason: str
    lane: str | None  # the lane it stands in; None when it cannot be read
    group: str  # its group, or UNREADABLE
    held: tuple = ()  # hyg-blocker-owner: the cards it holds up
    label: str | None = None  # hyg-hand-step: the class label it names


@dataclass
class Hold:
    kind: str
    group: str
    card: str
    lane: str
    hours: float
    waits: str  # OPEN, CLOSED, STEP or UNREAD
    waits_on: str
    first_line: str
    reason: str


@dataclass
class CutShort:
    card: str
    lane: str


@dataclass
class Report:
    taken_at: datetime
    lanes: list
    holds: list
    cut_short: list


# --------------------------------------------------------------------------- #
# reading a receipt                                                            #
# --------------------------------------------------------------------------- #


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _lane(card: dict) -> str | None:
    return (card.get("state") or {}).get("name")


def _unreadable(kind: str, at, first: str, reason: str, card: dict) -> Receipt:
    lane = _lane(card)
    if kind == HAND_STEP:
        lane = hygiene.HAND_WORK
    elif kind == BLOCKER_OWNER and lane not in hygiene_blockers.WAITING:
        lane = None
    return Receipt(kind, at, first, reason, lane, UNREADABLE)


def _hygiene_receipt(tag: str, at, first: str, reason: str, card: dict) -> Receipt:
    head = hygiene.read_receipt(first)
    cause = (head or {}).get("cause") or ""
    if head is None or head["tag"] != tag or not cause.strip():
        return _unreadable(tag, at, first, reason, card)
    if tag == HAND_STEP:
        match = _HAND_STEP_CAUSE.match(cause)
        if match is None:
            return _unreadable(tag, at, first, reason, card)
        return Receipt(tag, at, first, reason, hygiene.HAND_WORK, cause,
                       label=hygiene_hand_step.LABELS[match.group("cls")])
    match = _BLOCKER_CAUSE.match(cause)
    items = reason[len("evidence: "):].split(", ") if reason.startswith("evidence: ") else []
    held = tuple(dict.fromkeys(m.group("held") for m in map(_HELD.match, items) if m))
    if match is None or not held:
        return _unreadable(tag, at, first, reason, card)
    return Receipt(tag, at, first, reason, match.group("lane"), match.group("lane"), held=held)


def _overdue_receipt(at, first: str, reason: str, card: dict) -> Receipt:
    match = _OVERDUE_FOR_OPERATOR.match(first) or _OVERDUE_IN_LANE.match(first)
    clause = reason[len(_KIND):] if reason.startswith(_KIND) else ""
    lead = clause.split("—", 1)[0].strip().rstrip(".").strip()
    if match is None or not lead:
        return _unreadable(OVERDUE, at, first, reason, card)
    return Receipt(OVERDUE, at, first, reason, match.group("lane"), lead)


def receipt_of(node: dict, card: dict) -> Receipt | None:
    """The hold a comment records, or None when it is none of the three."""
    lines = (node.get("body") or "").lstrip().splitlines()
    if not lines:
        return None
    first, reason = lines[0], (lines[1] if len(lines) > 1 else "")
    at = _when(node.get("createdAt"))
    head = _HYGIENE_HEAD.match(first)
    if head is not None:
        return _hygiene_receipt(head.group("tag"), at, first, reason, card)
    if first.startswith(OVERDUE_MARK):
        return _overdue_receipt(at, first, reason, card)
    return None


# --------------------------------------------------------------------------- #
# which receipts are open                                                      #
# --------------------------------------------------------------------------- #


def _for_this_stay(receipt: Receipt, card: dict) -> bool:
    lane = _lane(card)
    if receipt.lane is None or receipt.lane != lane:
        return False
    if receipt.kind == HAND_STEP:
        # Posted once per card: open while it wears the label it names.
        if receipt.label is None:
            return True
        return receipt.label.lower() in {n.lower() for n in hygiene._labels(card)}
    since, _ = hygiene_blockers.entered(card, lane)
    if since is None or receipt.at is None:
        return True  # nothing read says the card has moved since
    return receipt.at > since


def _start(receipt: Receipt, card: dict) -> datetime | None:
    if receipt.kind != HAND_STEP:
        return receipt.at
    since, _ = hygiene_blockers.entered(card, hygiene.HAND_WORK)
    return max((t for t in (receipt.at, since) if t is not None), default=None)


def open_receipts(card: dict) -> list:
    """The card's open holds: per kind, the newest receipt of its stay."""
    found = [r for r in (receipt_of(n, card) for n in
                         (card.get("comments") or {}).get("nodes") or []) if r is not None]
    newest: dict = {}
    for r in found:
        if not _for_this_stay(r, card):
            continue
        held = newest.get(r.kind)
        if held is None or (r.at is not None and (held.at is None or r.at > held.at)):
            newest[r.kind] = r
    return [newest[k] for k in TAGS if k in newest]


def _any_receipt(card: dict) -> bool:
    return any(receipt_of(n, card) is not None
               for n in (card.get("comments") or {}).get("nodes") or [])


# --------------------------------------------------------------------------- #
# what it waits on                                                             #
# --------------------------------------------------------------------------- #


def _closed(state: str | None) -> bool:
    return state in prose_blockers.TERMINAL


def held_states(ctx: hygiene.Context, idents: list) -> dict:
    """Each held-up card's state, by identifier — `STATE_UNREAD` for a card
    the read did not answer. A read that fails leaves every card unread."""
    if not idents:
        return {}
    try:
        got = hygiene_blockers.read_cards(ctx, sorted(set(idents)))
    except hygiene.Forbidden:
        raise
    except Exception as e:  # noqa: BLE001 — unread is said, never read as closed
        print(f"hold-waits: the held-up cards could not be read — {e}", file=sys.stderr)
        got = {}
    return {i: (_lane(got[i]) if got.get(i) else None) or STATE_UNREAD for i in idents}


def _blocker_wait(receipt: Receipt, states: dict) -> tuple:
    named = [(h, states.get(h, STATE_UNREAD)) for h in receipt.held]
    waits_on = "waits on " + ", ".join(f"{h} ({s})" for h, s in named)
    if any(s != STATE_UNREAD and not _closed(s) for _, s in named):
        return OPEN, "holds up open work", waits_on
    if all(_closed(s) for _, s in named):
        return CLOSED, "holds up nothing open", waits_on
    return UNREAD, STATE_UNREAD, waits_on


def _step_wait(card: dict) -> tuple:
    blockers = [((n.get("issue") or {}).get("identifier"),
                 ((n.get("issue") or {}).get("state") or {}).get("name"))
                for n in (card.get("inverseRelations") or {}).get("nodes") or []
                if (n or {}).get("type") == hygiene_blockers.BLOCKS]
    blockers = [(i, s) for i, s in blockers if i]
    if not blockers:
        return STEP, "waits on the step itself"
    still = [(i, s) for i, s in blockers if not _closed(s)]
    if still:
        return OPEN, "; ".join(f"waits on open blocker {i} ({s or STATE_UNREAD})"
                               for i, s in still)
    return CLOSED, "; ".join(f"blocker {i} closed" for i, _ in blockers)


# --------------------------------------------------------------------------- #
# the read                                                                     #
# --------------------------------------------------------------------------- #


def _cards(board_doc: dict) -> list:
    return [c for cards in (board_doc.get("lanes") or {}).values() for c in cards or []]


def _cut_short(card: dict) -> bool:
    waits = _lane(card) in hygiene_blockers.WAITING or proof_and_demo.is_proof(card.get("title"))
    return (waits and linear_ops.window_is_partial(card.get("comments"))
            and not _any_receipt(card))


def read(board_doc: dict, ctx: hygiene.Context) -> Report:
    taken_at = _when(board_doc.get("taken_at"))
    if taken_at is None:
        raise ValueError("the board file carries no readable taken_at")
    cards = _cards(board_doc)
    found = [(card, r) for card in cards for r in open_receipts(card)]
    states = held_states(ctx, [h for _, r in found
                               if r.kind == BLOCKER_OWNER and r.group != UNREADABLE
                               for h in r.held])
    holds: list = []
    for card, r in found:
        if r.kind == BLOCKER_OWNER and r.group == UNREADABLE:
            waits, waits_on, group = UNREAD, "the receipt names no held card it can read", r.group
        elif r.kind == BLOCKER_OWNER:
            waits, split, waits_on = _blocker_wait(r, states)
            group = f"{r.group}, {split}"
        else:
            (waits, waits_on), group = _step_wait(card), r.group
        start = _start(r, card)
        hours = 0.0 if start is None else max(0.0, (taken_at - start).total_seconds() / 3600)
        holds.append(Hold(kind=r.kind, group=group, card=card["identifier"], lane=_lane(card),
                          hours=round(hours, 1), waits=waits, waits_on=waits_on,
                          first_line=r.first_line, reason=r.reason))
    cut = [CutShort(card["identifier"], _lane(card)) for card in cards if _cut_short(card)]
    return Report(taken_at=taken_at, lanes=list((board_doc.get("lanes") or {}).keys()),
                  holds=holds, cut_short=cut)


# --------------------------------------------------------------------------- #
# the output                                                                   #
# --------------------------------------------------------------------------- #


def _pt(when: datetime) -> str:
    return when.astimezone(_PT).strftime("%Y-%m-%d %H:%M PT")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def render(report: Report) -> str:
    readable = [h for h in report.holds if h.group != UNREADABLE]
    counts = " ".join(f"{k}={sum(1 for h in readable if h.kind == k)}" for k in TAGS)
    lines = [f"hold-waits: taken_at={_pt(report.taken_at)} lanes={','.join(report.lanes)} "
             f"open={len(report.holds)} {counts} "
             f"unreadable={len(report.holds) - len(readable)} cut-short={len(report.cut_short)}"]
    groups: dict = {}
    for h in report.holds:
        groups.setdefault((h.kind, h.group), []).append(h)
    order = sorted(groups.items(), key=lambda kv: (-sum(h.hours for h in kv[1]), kv[0]))
    for (kind, group), holds in order:
        holds = sorted(holds, key=lambda h: (-h.hours, h.card))
        tally = {w: sum(1 for h in holds if h.waits == w) for w in (OPEN, CLOSED, STEP, UNREAD)}
        lines += ["", f"{kind} — {group}: {_plural(len(holds), 'hold')}, "
                      f"{sum(h.hours for h in holds):.1f} h total, oldest {holds[0].hours:.1f} h"
                      f" — waits on open {tally[OPEN]} · closed {tally[CLOSED]} · "
                      f"step itself {tally[STEP]} · unread {tally[UNREAD]}"]
        for h in holds:
            first = f'"{h.first_line}"' if h.group == UNREADABLE else h.first_line
            lines.append(f"- {h.card} | {h.lane} | {h.hours:.1f} h | {h.waits_on} | "
                         f"{first} | {h.reason}")
    if report.cut_short:
        lines += ["", f"{CUT_SHORT}: {_plural(len(report.cut_short), 'card')}"]
        lines += [f"- {c.card} | {c.lane}" for c in report.cut_short]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--board", required=True,
                        help="the file `python3 scripts/hygiene.py read --out <file>` wrote")
    args = parser.parse_args(argv)
    with open(args.board, encoding="utf-8") as fh:
        board_doc = json.load(fh)
    print(render(read(board_doc, hygiene.make_context(hygiene.HOME_OWNER))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
