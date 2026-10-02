#!/usr/bin/env python3
"""An Urgent or High priority nobody has re-confirmed in three weeks is ranked
as Medium, and the proposal says so (DRE-5307).

WHY. The groomer's order is Urgent first, then High, then newest first, and
`groomer._priority` read the card's Linear `priority` with no notion of when
it was set. On 2026-09-29 five cards marked Urgent in August — DRE-2702,
DRE-2563, DRE-2564, DRE-3681, DRE-3530, 37 to 40 days old — opened the batch
every morning ahead of that week's work.

WHAT IT READS. For each candidate, the card's history and comments in one
request. The priority dates from the newest history entry setting it, else
from the card's creation. It is stale when that moment is more than
`STALE_DAYS` before `now` and nothing re-confirmed it since the cutoff: a
newer entry setting the same priority again, or a comment whose first line
opens with `priority-confirmed` (any case, an emoji before it allowed),
written by a person.

"BY A PERSON" is `spoken_thread.voices`'s answer, the one reader that tells
the CEO's console-signed answer apart. The console posts his answers on the
pipeline's own key, so "not the viewer" would throw his confirmation away. A
comment counts when its voice is `CEO_VIA_CONSOLE` (a receipt whose signature
verifies) or `PERSON` (another Linear user's own account), and under no other
kind: not the pipeline's key without a receipt, not an integration, not a
receipt refused, and nothing when the viewer is unknown.

AN UNCHECKED CONFIRMATION IS "CANNOT TELL". A console answer whose key could
not be fetched is `UNCHECKED`: the check never ran, so it is neither his voice
nor a forgery (DRE-4153). When such an answer opens with `priority-confirmed`
after the cutoff and nothing counted confirms the priority, the card is not
read as stale — it keeps its band and is named under `unread`.

WHAT IT COSTS. A candidate is an Urgent or High card created more than
`STALE_DAYS` ago — a priority cannot predate its card, and both fields are on
every population row, so the filter costs nothing. Candidates are read oldest
first, at most `MAX_READS` requests a morning, plus one `viewer_id()` read;
zero of either when there is no candidate. A card not read keeps its band and
is named, with why, under `unread`: the budget spent, the read refused, or the
pipeline's own identity unread — and "cannot tell" never demotes.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import console_receipt  # noqa: E402 — the answer text above a receipt
import spoken_thread  # noqa: E402 — ONE reader of who said what (DRE-3785)

STALE_DAYS = 21
CONFIRM_MARKER = "priority-confirmed"
#: The most history requests one morning makes.
MAX_READS = 40

BUDGET_SPENT = "read budget of {n} spent"
#: Byte for byte `groom_verify_agent.VIEWER_UNREAD` (DRE-5306). Duplicated on
#: purpose — this module must not depend on the verify runner — and a test
#: holds the two equal.
VIEWER_UNREAD = "the pipeline's own Linear identity could not be read"

URGENT, HIGH = 1, 2
NAMES = {URGENT: "Urgent", HIGH: "High"}
#: The voices whose `priority-confirmed` counts, and no other.
COUNTED = (spoken_thread.CEO_VIA_CONSOLE, spoken_thread.PERSON)
#: Why a card keeps its band when the only confirmation is a console answer
#: whose check could not run.
UNCHECKED_CONFIRMATION = "a console confirmation could not be checked"

#: `history(first: n)` is the n NEWEST entries, so the newest setting of the
#: priority is in the page whenever any of the last fifty is one.
PRIORITY_QUERY = """query($id: String!) {
  issue(id: $id) {
    history(first: 50) { nodes { createdAt fromPriority toPriority } }
    comments(first: 50) { nodes { body createdAt user { id } botActor { id } } }
  }
}"""

_MARKER = re.compile(rf"^\W*{re.escape(CONFIRM_MARKER)}(?![\w-])", re.I)
#: The console's heading above the CEO's words (`console_receipt.ANSWER_SPEC`).
_ANSWER_HEAD = re.compile(r"^Answer from .* PT:\s*$")


def _moment(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _level(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _one_line(e: Exception) -> str:
    lines = str(e).strip().splitlines()
    return lines[0].strip() if lines else ""


def _nodes(issue: dict, key: str) -> list[dict]:
    return [n for n in ((issue.get(key) or {}).get("nodes") or [])
            if isinstance(n, dict)]


def candidates(cards: list[dict], *, now: datetime) -> list[dict]:
    """The Urgent and High cards created more than `STALE_DAYS` before `now`,
    oldest first — the only cards whose priority could be stale."""
    cutoff = now - timedelta(days=STALE_DAYS)
    found = []
    for card in cards:
        created = _moment(card.get("createdAt"))
        if _level(card.get("priority")) in NAMES and created and created < cutoff:
            found.append((created, card["identifier"], card))
    return [card for _, _, card in sorted(found, key=lambda t: t[:2])]


class CannotTell(Exception):
    """Whether the priority was re-confirmed cannot be known — the card keeps
    its band and is named, never demoted."""


def _first_line(text: str | None, *, console: bool) -> str:
    """The first line the speaker wrote — for a console answer, the first line
    of the CEO's words, under the console's heading."""
    text = text or ""
    if console:
        text = console_receipt.answer_text(text)
    lines = [line for line in text.replace("\r", "").split("\n") if line.strip()]
    if console and lines and _ANSWER_HEAD.match(lines[0].strip()):
        lines = lines[1:]
    return lines[0].strip() if lines else ""


def confirmed_since(nodes: list[dict], viewer: str, *, card: str,
                    cutoff: datetime, verifier=None) -> bool:
    """Did a person say `priority-confirmed` after `cutoff`? Raises
    `CannotTell` when the only such answer is the console's and its check
    could not run.

    `spoken_thread.UNCHECKED` withholds the words, so the marker is read off
    the comment itself — one voice per node, in order. Unverified text is safe
    to read here: it can only keep a card's band, never move one up."""
    ordered = sorted(nodes, key=lambda n: n.get("createdAt") or "")
    unchecked = False
    for node, voice in zip(ordered, spoken_thread.voices(
            ordered, viewer, card=card, verifier=verifier), strict=True):
        at = _moment(voice.created_at)
        if not (at and at > cutoff):
            continue
        if voice.kind in COUNTED and _MARKER.match(_first_line(
                voice.body, console=voice.kind == spoken_thread.CEO_VIA_CONSOLE)):
            return True
        if voice.kind == spoken_thread.UNCHECKED and _MARKER.match(
                _first_line(node.get("body"), console=True)):
            unchecked = True
    if unchecked:
        raise CannotTell(UNCHECKED_CONFIRMATION)
    return False


def staleness(card: dict, issue: dict, viewer: str, *, now: datetime,
              verifier=None) -> dict | None:
    """`{"priority", "set_at", "days"}` when the card's priority is stale,
    else None."""
    priority = _level(card.get("priority"))
    set_at, raw = None, None
    for node in _nodes(issue, "history"):
        at = _moment(node.get("createdAt"))
        if at and _level(node.get("toPriority")) == priority \
                and (set_at is None or at > set_at):
            set_at, raw = at, node["createdAt"]
    if set_at is None:
        set_at, raw = _moment(card.get("createdAt")), card.get("createdAt")
    cutoff = now - timedelta(days=STALE_DAYS)
    if set_at is None or set_at >= cutoff:
        return None
    if confirmed_since(_nodes(issue, "comments"), viewer,
                       card=card["identifier"], cutoff=cutoff,
                       verifier=verifier):
        return None
    return {"priority": NAMES[priority], "set_at": raw,
            "days": (now - set_at).days}


def _viewer(lops) -> str | None:
    try:
        return lops.viewer_id() or None
    except Exception:  # noqa: BLE001 — an unread viewer is said, not fatal
        return None


def annotate(cards: list[dict], *, lops, now, verifier=None) -> dict:
    """Mark each stale candidate in place, `card["priority_stale"]`, and each
    one not read, `card["priority_unread"]` (the why). Returns
    `{"stale": [ids], "unread": {id: why}}`.

    `verifier` goes to `spoken_thread.voices` unchanged: a fake in tests, and
    the console's key fetched once per process in a live run."""
    now = _moment(now)
    for card in cards:
        card.pop("priority_stale", None)
        card.pop("priority_unread", None)
    stale: list[str] = []
    unread: dict[str, str] = {}
    queue = candidates(cards, now=now)
    viewer = _viewer(lops) if queue else None
    for n, card in enumerate(queue):
        identifier = card["identifier"]
        if viewer is None:
            why = VIEWER_UNREAD
        elif n >= MAX_READS:
            why = BUDGET_SPENT.format(n=MAX_READS)
        else:
            try:
                issue = (lops.gql(PRIORITY_QUERY, {"id": identifier})
                         or {}).get("issue")
                if not isinstance(issue, dict):
                    raise ValueError("Linear returned no card")
                found = staleness(card, issue, viewer, now=now,
                                  verifier=verifier)
            except Exception as e:  # noqa: BLE001 — an unread card keeps its band
                why = _one_line(e) or type(e).__name__
            else:
                if found:
                    card["priority_stale"] = found
                    stale.append(identifier)
                continue
        card["priority_unread"] = why
        unread[identifier] = why
    return {"stale": stale, "unread": unread}
