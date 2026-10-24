#!/usr/bin/env python3
"""The groomer's memory — what was decided about each card, and what that
means this morning (DRE-7822).

The memory today is `groomer.standing_decisions` (DRE-4966). It reads the
markers on ONE standing card, the one the `GROOM_PROPOSAL_CARD` repo variable
names, and knows no reason and no condition. Proposals have been posted on
five standing cards, and on 2026-09-26 that cost four cards: DRE-4054,
DRE-4267, DRE-4271 and DRE-4272 were marked `groom-excluded` on DRE-4488 on
2026-09-22, and every DRE-4541 proposal from 2026-09-26 offered them again,
because nothing read DRE-4488.

This module answers the two questions in one place, and reads nothing to do
it — no Linear, no read door, no clock, no file at run time — and imports
nothing from `groomer.py`, so the groomer imports it without a cycle. The
caller hands it the door's rows, every standing card's markers, the reasons
the door served, today's Pacific date and the states of the cards a hold
names. `groomer.read_memory` calls it once per `propose` run (DRE-7824).

## Every standing card, the current one included

`STANDING_CARDS` declares all five, oldest first, and no card ever leaves it.
`standing_cards(current)` appends `current` — the `GROOM_PROPOSAL_CARD`
variable — when it is not already there, so a new card is read from its first
morning and the outgoing one stays read because it is in the constant. The
pull request that rotates the card adds it to `config/hand-work-age.json`'s
`standing` list so it never alarms; `tests/test_groom_memory.py` makes that
same pull request append it here.

## The marker tail grammar (the console writes it, agent-bureau DRE-6659)

  * excluded, tail `move to Cancel` — `cancel`;
  * excluded, tail a label the door served in `reasons` — `not-doing` with
    that label's key;
  * excluded, any other tail — `not-doing` with no key, the tail kept as its
    words (the 2026-10-10 markers carry free-text reasons);
  * held, tail `until DRE-M` or `until YYYY-MM-DD` — `hold` on that one
    condition; any other tail — `hold` with no condition, its words kept;
  * added or rewrite-accepted — `put-back`, the tail kept as its words.

A tail may arrive with the console's ` — ` separator still on it, and reads
the same either way. A door row's hold date that is not a date is no
condition either, so one bad row cannot take down the morning's answer.

## Which source counts

When the door answered, its rows are the `record`. A marker written before
the oldest row is `history` — the console had not begun storing yet. A marker
at or after it that a row mirrors (same standing card, proposal id, card and
decision) is the same decision and is dropped. One no row mirrors was typed
by hand on the thread and counts as `thread`, so a person's decision is never
dropped in silence. A door that answered with no rows has begun nothing a
marker could be mirrored by, so every marker is `history`. When the door
could not be read, every marker is `fallback`. The newest decision per card
wins across sources, and on an exact tie the row wins.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

# Every card the groomer has posted proposals on, oldest first: DRE-2840 and
# DRE-3013 (2026-09-04), DRE-3327 (from 2026-09-08), DRE-4488 (2026-09-21)
# and DRE-4541 (from 2026-09-26). Append on rotation; never remove one.
STANDING_CARDS = ("DRE-2840", "DRE-3013", "DRE-3327", "DRE-4488", "DRE-4541")

DECISIONS = ("not-doing", "cancel", "hold", "put-back")   # the console's words
SOURCES = ("record", "history", "thread", "fallback")
MARKER_KINDS = ("excluded", "held", "added", "rewrite-accepted")

# The console's word for Cancel, on an excluded marker.
CANCEL_TAIL = "move to Cancel"

_SEPARATOR = re.compile(r"^\s*[—–:-]+\s*")
_UNTIL_CARD = re.compile(r"^until\s+(DRE-\d+)$", re.IGNORECASE)
_UNTIL_DATE = re.compile(r"^until\s+(\d{4}-\d{2}-\d{2})$", re.IGNORECASE)

# Where a met or unmet hold on a card stands, by that card's state.
DONE = "Done"
GONE = ("Canceled", "Duplicate")


def standing_cards(current: str | None) -> tuple[str, ...]:
    """`STANDING_CARDS`, then `current` when it is not already in it."""
    if current and current not in STANDING_CARDS:
        return STANDING_CARDS + (current,)
    return STANDING_CARDS


def remembered(rows: list[dict], markers: list[dict], *, reasons,
               door_problem: str | None) -> dict[str, dict]:
    """The newest decision about each card, `{card: decision}`.

    `rows` are the door's stored decisions (`[]` when `door_problem` says why
    the door was not read); `markers` are a decider's per-card markers on
    every standing card, already vouched; `reasons` are the labels the door
    served, `[{"key", "label"}…]` or `{key: label}`, or None. Each decision
    carries exactly the contract's keys — see `_decision`.
    """
    labels = _labels(reasons)
    keys = {label: key for key, label in labels.items()}
    candidates: list[tuple[datetime, int, int, dict]] = []

    read = door_problem is None
    records = [_from_row(r, labels) for r in rows] if read else []
    records = [r for r in records if r is not None]
    oldest = min((_instant(r["recorded_at"]) for r in records), default=None)
    mirrors = {(r["standing_card"], r["proposal_id"], r["card"], r["decision"])
               for r in records}

    for index, raw in enumerate(markers):
        decision = _from_marker(raw, keys)
        if decision is None:
            continue
        at = _instant(decision["recorded_at"])
        if not read:
            decision["source"] = "fallback"
        elif oldest is None or at < oldest:
            decision["source"] = "history"
        elif (decision["standing_card"], decision["proposal_id"],
              decision["card"], decision["decision"]) in mirrors:
            continue
        else:
            decision["source"] = "thread"
        candidates.append((at, 0, index, decision))

    for index, decision in enumerate(records):
        candidates.append((_instant(decision["recorded_at"]), 1, index, decision))

    newest: dict[str, tuple] = {}
    for candidate in candidates:
        card = candidate[3]["card"]
        if card not in newest or candidate[:3] > newest[card][:3]:
            newest[card] = candidate
    return {card: candidate[3] for card, candidate in newest.items()}


def standing(remembered: dict[str, dict], *, today: str,
             states: dict[str, str | None]) -> dict:
    """Where each remembered card stands this morning.

    `today` is the Pacific date, `YYYY-MM-DD`; `states` maps each card a hold
    names to its state name, None when it could not be read (a card missing
    from it is unread too). Every list is sorted by card number, and a card is
    in at most one:

      * `excluded` — a `not-doing`, off the offer until a later `put-back`;
      * `cancel` — a `cancel`, on the Cancel list;
      * `held` — `{"decision", "why"}`, a hold whose condition is not met;
      * `held_last` — a hold with no condition, offered and ranked last;
      * `back` — `{"decision", "why"}`, a hold whose condition is met, on
        offer and sequenced as any card;
      * `put_back` — a `put-back`, on offer, listed so the page can say why
        it came back.
    """
    out = {"excluded": [], "cancel": [], "held": [], "held_last": [],
           "back": [], "put_back": []}
    for decision in remembered.values():
        kind = decision["decision"]
        if kind == "not-doing":
            out["excluded"].append(decision)
        elif kind == "cancel":
            out["cancel"].append(decision)
        elif kind == "put-back":
            out["put_back"].append(decision)
        elif kind == "hold":
            if not (decision["hold_until_card"] or decision["hold_until_date"]):
                out["held_last"].append(decision)
                continue
            met, why = _hold(decision, today, states)
            out["back" if met else "held"].append({"decision": decision, "why": why})
    for name in ("excluded", "cancel", "held_last", "put_back"):
        out[name].sort(key=lambda d: _card_sort_key(d["card"]))
    for name in ("held", "back"):
        out[name].sort(key=lambda e: _card_sort_key(e["decision"]["card"]))
    return out


def _hold(decision: dict, today: str, states: dict) -> tuple[bool, str]:
    """`(met, why)` for a hold on a condition. A hold naming both a card and
    a date — the console writes one — is met only when each is."""
    whys = []
    card = decision["hold_until_card"]
    if card:
        state = states.get(card)
        if state is None:
            return False, f"held until {card} is Done; its state could not be read"
        if state in GONE:
            return False, (f"held until {card} is Done, but it is {state} — "
                           "the condition can no longer be met")
        if state != DONE:
            return False, f"held until {card} is Done; it is {state}"
        whys.append(f"{card} is Done")
    until = decision["hold_until_date"]
    if until:
        if date.fromisoformat(today) < date.fromisoformat(until):
            return False, f"held until {until}"
        whys.append(f"held until {until}, which has come")
    return True, "; ".join(whys)


def _decision(**fields) -> dict:
    """One decision, with exactly the contract's keys:

    card, decision, reason, reason_label, hold_until_card, hold_until_date,
    actor_name, recorded_at, standing_card, proposal_id, source.
    `reason_label` is the label for a keyed reason, else the row's comment,
    else the marker's own words, else None; `actor_name` is the row's, None
    for a marker.
    """
    base = dict.fromkeys(("card", "decision", "reason", "reason_label",
                          "hold_until_card", "hold_until_date", "actor_name",
                          "recorded_at", "standing_card", "proposal_id",
                          "source"))
    base.update(fields)
    return base


def _from_row(row: dict, labels: dict[str, str]) -> dict | None:
    """A door row as a decision, or None for one naming no card or a word
    that is not one of `DECISIONS`. A hold date that is not a `YYYY-MM-DD`
    date is no condition, as on a marker."""
    if not row.get("card") or row.get("decision") not in DECISIONS:
        return None
    reason = row.get("reason") or None
    until = row.get("hold_until_date") or None
    return _decision(card=row["card"], decision=row["decision"], reason=reason,
                     reason_label=labels.get(reason) or row.get("comment") or None,
                     hold_until_card=row.get("hold_until_card") or None,
                     hold_until_date=until if until and _is_date(until) else None,
                     actor_name=row.get("actor_name") or None,
                     recorded_at=row["recorded_at"],
                     standing_card=row.get("standing_card"),
                     proposal_id=row.get("proposal_id"), source="record")


def _from_marker(marker: dict, keys: dict[str, str]) -> dict | None:
    """A marker read through the tail grammar, or None for one naming no
    card or of a kind not in `MARKER_KINDS`. Its `source` is set by the
    caller."""
    kind = marker.get("kind")
    if not marker.get("card") or kind not in MARKER_KINDS:
        return None
    tail = _SEPARATOR.sub("", marker.get("tail") or "").strip() or None
    fields = {"decision": "not-doing", "reason_label": tail}
    if kind == "excluded":
        if tail and tail.casefold() == CANCEL_TAIL.casefold():
            fields = {"decision": "cancel", "reason_label": None}
        elif tail in keys:
            fields["reason"] = keys[tail]
    elif kind == "held":
        fields["decision"] = "hold"
        on_card = _UNTIL_CARD.match(tail or "")
        on_date = _UNTIL_DATE.match(tail or "")
        if on_card:
            fields.update(hold_until_card=on_card.group(1).upper(), reason_label=None)
        elif on_date and _is_date(on_date.group(1)):
            fields.update(hold_until_date=on_date.group(1), reason_label=None)
    else:
        fields["decision"] = "put-back"
    return _decision(card=marker["card"], recorded_at=marker["created_at"],
                     standing_card=marker.get("standing_card"),
                     proposal_id=marker.get("proposal_id"), **fields)


def _labels(reasons) -> dict[str, str]:
    """`{key: label}` off the reasons the door served, in either shape."""
    if not reasons:
        return {}
    if isinstance(reasons, dict):
        return {str(k): str(v) for k, v in reasons.items() if k and v}
    return {str(r["key"]): str(r["label"]) for r in reasons
            if isinstance(r, dict) and r.get("key") and r.get("label")}


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _instant(stamp: str) -> datetime:
    """An ISO-8601 time as an aware UTC instant, so `Z`, an offset and
    fractional seconds compare as times and never as text."""
    parsed = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _card_sort_key(identifier: str):
    """`DRE-9` before `DRE-11` (as `groomer._card_sort_key`, not imported)."""
    match = re.search(r"(\d+)$", identifier or "")
    return (int(match.group(1)) if match else 0, identifier or "")
