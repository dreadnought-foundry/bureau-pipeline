#!/usr/bin/env python3
"""A card defect that keeps the sweep red is a console alert (DRE-8632).

From 10-30 10:46 AM PDT to about 5:36 PM PDT on 10-31, 120 scheduled
agent-bureau Reconcile runs went red for one cause: a prose blocker on DRE-7305
with no relation behind it. The only signal was the medic's `standing_defect`
class (DRE-6467), one `::notice::` on a run nobody opens, so there was nothing
to read the defect off and nothing to act on.

`settle(entries, repo_slug, unread)` is the full sweep's last step before its
red or green exit, handed `reconcile._stale_defects` as it stands. It raises
one WARN console alert per card the ledger names and resolves this repo's
alerts whose card has left it. The `--promote-only` gate never calls it: the
scheduled full sweep is the one place that raises and resolves the alert, so
the event-driven gate cannot flap it.

## The card an entry names

The `DRE-<n>` the entry opens with, followed by `:`. The three appenders that
name a card write it there, two behind one tag word, which is read past:

* `_report_epic_prose_defect` — `DRE-7305: a prose blocker declaring …`;
* `promotion_stall.ledger_line` — `promotion-stalled DRE-41: refused …` —
  and its epic's twin, `epic_ledger_line` — `promotion-stalled DRE-40: 9
  children wait on it …` (DRE-8815);
* limit recovery's `STOOD_PHRASE` line, its `ERROR: ` prefix stripped —
  `limit-recovery DRE-42: the limit-death marker has stood …`.

An entry that names no card that way — the idle board's — raises nothing and
is counted on one line.

## The alert

Notice key `standing-defect:<repo>:<card>` (console kind
`pipeline_notice:standing-defect:<repo>:<card>`), severity WARN, raised through
`pipeline_notice.raise_notice` with no fallback card. The title is
`<card> is keeping the <repo> sweep red`; the body quotes the card's ledger
entries and says what turns the sweep green and what happens after two hours.
A repeat raise costs nothing: the door refreshes the alert's text and delivers
nothing again. A raise the door cannot take is `lost`, logged, and never fails
the sweep — the red run is still there as the backstop.

Every open standing-defect alert of this repo, read off
`bureau_read.open_alerts()`, whose card is not on this pass's ledger is
resolved. An unreadable read resolves nothing this pass and says so. A green
sweep, with an empty ledger, resolves every one of them.

## A ledger the pass did not finish

A card off the ledger is fixed only when the pass asked about it. `unread`
is `reconcile._ledger_unread`: each gate that appends to the ledger and was
skipped or stopped this pass — promotion on a held board, a limit recovery
that was held or failed partway — and each defect found whose age could not
be read. When it is not empty the empty half of the ledger is absence, not
green (`standards/console-honesty.md`), so nothing is resolved, the open
alerts are not read, and one line names what was missed. The raise half runs
as ever: what the pass did find is standing.

An idle pass skips promotion too, and still resolves. It is idle because
nothing of this repo's is in motion or waiting to be promoted, so no gate of
its can be standing, and its empty ledger is the true one.

## The lines

Each opens with `TAG`, on stdout, and `settle` returns them in order:

    standing-defect-alert: <raised|repeat|lost> card=<DRE-n>
    standing-defect-alert: resolved card=<DRE-n>
    standing-defect-alert: none standing
    standing-defect-alert: <k> entry(ies) name no card
    standing-defect-alert: open alerts unreadable — <reason>
    standing-defect-alert: ledger not evaluated — <what>; nothing resolved this pass

The epic's proof does not read them: it reads the console alert and the
`pipeline-notice:` line the notice client prints for every raise and resolve,
which `config/proof-reach.json` already keeps. A raise or resolve outcome other
than these words (the door's `escalated`, `not-open`, a `resolve-refused`) is
printed as the client returned it.

## The vendor boundary (`standards/vendor-boundaries.md`)

* Q1, the actor: the reconcile reusable already raises notices
  (`reviewer_outage:fleet`), and the console door admits it.
* Q2, the credentials: the sweep job already holds `id-token: write` and
  `BUREAU_READ_URL`, the two the notice client and the read door need.
* Q3, a retry: a re-run repeats the same raise, and the door answers `repeat`
  — and a pass that could not evaluate the ledger leaves the alert open rather
  than resolving it, so the next full pass's raise is a `repeat` too.
* Q4, the limits: the door refuses a key outside `^[A-Za-z0-9_.:-]{1,120}$`,
  and `standing-defect:<repo>:DRE-<n>` is inside it.
* Q5, a crash mid-flow: a crash between the raise and the resolve leaves an
  open alert, which the next green sweep resolves.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

import bureau_read
import pipeline_notice

TAG = "standing-defect-alert:"
NOTICE_PREFIX = "standing-defect"
SEVERITY = "WARN"
#: The card an entry opens with: an optional `ERROR: `, an optional tag word
#: (`promotion-stalled`, `limit-recovery`), then `DRE-<n>:`.
_ENTRY_CARD = re.compile(r"^(?:ERROR:\s+)?(?:[a-z][a-z-]*\s+)?(DRE-\d+):")
_CARD = re.compile(r"^DRE-\d+$")
REPAIR_SENTENCE = ("The sweep turns green when this defect is fixed. If it is still "
                   "open after 2 hours, the Alarm Repair makes one attempt on it.")


def card_of(entry: str) -> str | None:
    """The card `entry` opens with, or None."""
    found = _ENTRY_CARD.match(entry or "")
    return found.group(1) if found else None


def notice_key(repo_slug: str, card: str) -> str:
    return f"{NOTICE_PREFIX}:{repo_slug}:{card}"


def title(repo_slug: str, card: str) -> str:
    return f"{card} is keeping the {repo_slug} sweep red"


def body(entries: Iterable[str]) -> str:
    quoted = "\n".join(f"> {entry}" for entry in entries)
    return f"{quoted}\n\n{REPAIR_SENTENCE}"


def _open_cards(answer: dict, repo_slug: str) -> list[str]:
    """The cards of this repo's open standing-defect alerts, in the read's order."""
    prefix = f"pipeline_notice:{notice_key(repo_slug, '')}"
    cards = []
    for row in answer.get("alerts") or []:
        kind = row.get("kind") if isinstance(row, dict) else None
        if isinstance(kind, str) and kind.startswith(prefix):
            card = kind[len(prefix):]
            if _CARD.match(card) and card not in cards:
                cards.append(card)
    return cards


def settle(entries, repo_slug: str, unread: Iterable[str] = ()) -> list[str]:
    """Raise one alert per card `entries` names, resolve this repo's alerts
    whose card it no longer names — none when `unread` says the ledger is
    short — and return the lines printed."""
    lines: list[str] = []

    def say(text: str) -> None:
        line = f"{TAG} {text}"
        print(line, flush=True)
        lines.append(line)

    by_card: dict[str, list[str]] = {}
    no_card = 0
    for entry in entries:
        card = card_of(entry)
        if card is None:
            no_card += 1
        else:
            by_card.setdefault(card, []).append(entry)

    for card, quoted in by_card.items():
        outcome = pipeline_notice.raise_notice(
            notice_key(repo_slug, card), severity=SEVERITY,
            title=title(repo_slug, card), body=body(quoted), fallback=None)
        say(f"{outcome} card={card}")
    if not by_card:
        say("none standing")
    if no_card:
        say(f"{no_card} entry(ies) name no card")

    unread = list(unread)
    if unread:
        say(f"ledger not evaluated — {', '.join(unread)}; nothing resolved this pass")
        return lines
    try:
        answer = bureau_read.open_alerts()
    except bureau_read.ReadUnknown as e:
        say(f"open alerts unreadable — {e}; nothing resolved this pass")
        return lines
    for card in _open_cards(answer, repo_slug):
        if card in by_card:
            continue
        outcome = pipeline_notice.resolve_notice(notice_key(repo_slug, card))
        say(f"{outcome} card={card}")
    return lines
