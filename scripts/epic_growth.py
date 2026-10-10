#!/usr/bin/env python3
"""The question an epic grown past its green light asks (DRE-6414).

The CEO approves an epic at a size. `mid_epic` keeps the growth record on the
epic ("Green-lit at: N · Now running: M") and the sweep printed it every pass
— `epic-growth: DRE-4721 green-lit at 10 cards, now running 50 cards` — and
nothing compared M with N. This module is that comparison, and the question it
puts back in front of him when the answer is "far past it".

It reads nothing and writes nothing: no Linear, no lane. The sweep
(`reconcile.ask_epic_growth_question`, `reconcile.settle_epic_growth_question`)
does the reads and the writes; everything they say is composed here.

  * `threshold()` reads `config/epic-growth.json`; `crossed()` is the rule —
    more than `ratio` times the approved size AND at least `minimum_added`
    more cards. An approved size that cannot be read never crosses: nothing is
    asked against an approval nobody can read (console-honesty rule 2).
  * `title()` and `body()` are the question card: its body is the one Green
    Light format (DRE-3893), one `console_escalation.Escalation` rendered by
    `console_escalation.render` and nothing else, as the review-cap question
    is (`review_cap_question`, DRE-6189). No `escalation-choices` block: the
    answer is two words he types, not a click, for the reason DRE-5204 and
    DRE-6189 give.
  * `answer()` reads his signed answer off the question card's thread — the
    qualifying rule `green_light_reply` uses — and `read_words()` its first
    line.
  * `closing_comment()` and `split_because()` are what the sweep writes once
    he has answered.

The question card is a `no-code` card of its own in Green Light, and the epic
itself never goes there and keeps running: `reconcile.promote_ready` promotes
the children of an In Progress epic only, and pausing work he already approved
is worse than asking.

Run: python3 scripts/epic_growth.py   (prints the threshold the sweep reads)
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import console_escalation  # noqa: E402
import console_receipt  # noqa: E402
import spoken_thread  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config" / "epic-growth.json"

#: The question card's title, up to the epic. The sweep finds a card it
#: created and could not record by this prefix, so it never asks twice.
TITLE_PREFIX = "Epic grew past its green light: "

#: The tag the closing comment on the question card opens with.
TAG = "epic-growth-question"
CLOSING_MARK = f"✅ {TAG}:"

#: His two answers, and the outcome of words neither one reads.
RE_APPROVE = "re-approve"
SPLIT = "split"
UNREADABLE = "unreadable"

#: At this many times the approved size the recommendation is to split: a plan
#: three times the size he approved is a different plan.
SPLIT_AT = 3

#: The voices that are his: a console answer by its signature, or a comment
#: typed into Linear by a user `config/green-light-reply.json` declares.
_HIS = (spoken_thread.CEO_VIA_CONSOLE, spoken_thread.PERSON)

#: A console answer whose receipt was refused or could not be checked. Either
#: may be his newest word, so neither is read and neither is passed over.
_WITHHELD = (spoken_thread.REFUSED, spoken_thread.UNCHECKED)

#: The console's heading above his words (`console_receipt.ANSWER_SPEC`).
_ANSWER_HEAD = re.compile(r"^Answer from .* PT:\s*$")

#: The console's comment box heading above his words, which no file here
#: declares: agent-bureau `console/backend/card_comment.py` writes
#: `Comment from <name> (signed in to the console), <YYYY-MM-DD HH:MM> PT:`.
_COMMENT_HEAD = re.compile(r"^Comment from .* PT:\s*$")

#: The two words, at the start of a word — `disapprove` is not `approve`.
_APPROVE = re.compile(r"(?<![a-z])(?:re-?)?approve")
_SPLIT = re.compile(r"(?<![a-z])split")


class Threshold(NamedTuple):
    ratio: float
    minimum_added: int


# --------------------------------------------------------------------------- #
# the threshold                                                                #
# --------------------------------------------------------------------------- #


def threshold(path: Path = CONFIG) -> Threshold:
    """The numbers in `config/epic-growth.json`. A file that does not hold two
    positive numbers raises — it is never read as a threshold of nothing."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    ratio = doc.get("ratio") if isinstance(doc, dict) else None
    minimum = doc.get("minimum_added") if isinstance(doc, dict) else None
    for name, value in (("ratio", ratio), ("minimum_added", minimum)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"{path}: `{name}` must be a positive number, got {value!r}")
    return Threshold(ratio=ratio, minimum_added=minimum)


def crossed(approved, running, limit: Threshold | None = None) -> bool:
    """Has an epic approved at `approved` cards and running `running` grown
    past the threshold? Both must hold: more than `ratio` × approved, and at
    least `minimum_added` more. None for either never crosses."""
    if approved is None or running is None:
        return False
    limit = limit or threshold()
    return running > limit.ratio * approved and running - approved >= limit.minimum_added


# --------------------------------------------------------------------------- #
# the question                                                                 #
# --------------------------------------------------------------------------- #


def title_prefix(epic: str) -> str:
    return f"{TITLE_PREFIX}{epic} — "


def title(epic: str, approved: int, running: int) -> str:
    return f"{title_prefix(epic)}approved at {approved} cards, running {running}"


def _joined(card: dict) -> str:
    """`DRE-n (route: reason)`. A card that joined with no discovery record
    already says `unrecorded:` in its reason; the route is said once."""
    because = card.get("because") or ""
    route = card.get("route") or ""
    if because.startswith(f"{route}:"):
        because = because[len(route) + 1:].strip()
    return f"{card['id']} ({route}: {because})" if because else f"{card['id']} ({route})"


def _finding(epic, approved, running, joined) -> str:
    more = running - approved
    head = (f"{epic} was approved at {approved} cards and is now running "
            f"{running}, {more} more than you approved.")
    if not joined:
        return head
    return (f"{head} Joined since its green light: "
            + ", ".join(_joined(c) for c in joined) + ".")


def _recommendation(approved, running) -> tuple[str, str]:
    if running >= SPLIT_AT * approved:
        return SPLIT, (
            f"it is running at least {SPLIT_AT} times the size you approved, and "
            "a plan that size is a different plan from the one you said yes to — "
            "splitting it lets you approve each part on its own, while the work "
            "already under way finishes")
    return RE_APPROVE, (
        f"it is under {SPLIT_AT} times the size you approved, so the extra cards "
        "most likely are more of the same plan — the epic keeps running either "
        "way, and the next question comes only if it grows this far again")


def escalation(epic: str, approved: int, running: int,
               joined) -> console_escalation.Escalation:
    """The one reading of the growth the card's three lines render."""
    pick, why = _recommendation(approved, running)
    return console_escalation.Escalation(
        finding=_finding(epic, approved, running, list(joined or [])),
        question=(f"Is the bigger plan for {epic} still the one you approved? "
                  f"Answer with the word {RE_APPROVE} to keep it running as it "
                  f"is, or the word {SPLIT} to send it back to planning to be "
                  "split into smaller epics."),
        recommendation=pick,
        why=why,
    )


def body(epic: str, approved: int, running: int, joined) -> str:
    """The question card's description: the three lines and nothing else."""
    return console_escalation.render(escalation(epic, approved, running, joined))


# --------------------------------------------------------------------------- #
# the answer                                                                   #
# --------------------------------------------------------------------------- #


def read_words(text: str | None) -> str | None:
    """RE_APPROVE, SPLIT or None, off the first line of his words, lower-cased:
    an approve word and no split is a re-approval, a split and no approve word
    is a split, and anything else is neither. A console answer is read below
    its `Answer from …` or `Comment from …` heading and above its receipt."""
    text = text or ""
    if console_receipt.has_answer_trailer(text):
        text = console_receipt.answer_text(text)
    lines = [line.strip() for line in text.split("\n")]
    if lines and (_ANSWER_HEAD.match(lines[0]) or _COMMENT_HEAD.match(lines[0])):
        lines = lines[1:]
    first = next((line for line in lines if line), "").lower()
    approve, split = bool(_APPROVE.search(first)), bool(_SPLIT.search(first))
    if approve and not split:
        return RE_APPROVE
    if split and not approve:
        return SPLIT
    return None


def _moment(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def answer(nodes, voices, ceo_ids, *, after: str | None = None) -> str | None:
    """His answer on the question card's thread: RE_APPROVE, SPLIT, UNREADABLE
    for his words neither rule reads or a console answer that was refused or
    could not be checked, and None when he has said nothing since `after`.

    `nodes` and `voices` are the thread and `spoken_thread.voices`' reading of
    it, paired one to one; `ceo_ids` the Linear user ids
    `config/green-light-reply.json` declares. His newest comment decides — the
    qualifying rule `green_light_reply` uses."""
    since = _moment(after)
    newest = None
    for node, voice in zip(nodes or [], voices or []):
        at = _moment(voice.created_at)
        if since and at and at <= since:
            continue
        author = (node.get("user") or {}).get("id")
        if voice.kind in _WITHHELD or voice.kind == spoken_thread.CEO_VIA_CONSOLE or (
                voice.kind == spoken_thread.PERSON and author in ceo_ids):
            newest = voice
    if newest is None:
        return None
    if newest.kind in _WITHHELD:
        return UNREADABLE
    return read_words(newest.body) or UNREADABLE


def closing_comment(outcome: str, *, epic: str, running: int, at: str) -> str:
    """The one comment the sweep closes the question card under."""
    if outcome == RE_APPROVE:
        return f"{CLOSING_MARK} re-approved at {running} cards on {at}"
    if outcome == SPLIT:
        return (f"{CLOSING_MARK} split asked at {running} cards on {at} — "
                f"amendment filed on {epic}")
    raise ValueError(f"no closing comment for {outcome!r}")


def split_because(approved: int, running: int, question: str) -> str:
    """The one line the mid-epic amendment carries for his split."""
    return (f"the CEO asked at {running} cards, approved at {approved}, that this "
            f"epic be split along its seam — {question}")


if __name__ == "__main__":
    limit = threshold()
    print(f"epic-growth: crossed past {limit.ratio}× the approved size and at "
          f"least {limit.minimum_added} more cards ({CONFIG})")
