#!/usr/bin/env python3
"""One console alert lists every Triage card the repair could not fix
(DRE-8649).

Since DRE-8647 every card that arrives in Triage gets one automatic repair
(`scripts/triage_repair.py`). A card that repair could not fix waited for the
hygiene summary's hourly per-card alarm (`hygiene_triage.held_planning`), and
the CEO had to ask for the Triage cards to be fixed eight or more times. This
module raises ONE standing console alert listing those cards, and resolves it
when none is left. The 06:30 PT morning briefing (DRE-8631, epic DRE-8606)
opens with every `WARN` alert open a day or more, so the alert reaches it as
one row by that card's own rule; until then it is on the console's Alerts tab.
Nothing here waits on DRE-8606.

## Which cards

The Triage lane is read once (`board_snapshot.read_board(("Triage",))`), and
each card's WHOLE thread (`linear_ops.comment_records(card,
whole_thread=True)`). A card is listed when either holds:

* its newest pipeline-authored `🩹 triage-repair:` receipt
  (`triage_repair.receipts`) is `unfinished` or `unrepairable`
  (`LISTED_OUTCOMES`);
* its only receipt is `started` and is older than `RUNNING_HOURS` —
  `hygiene_triage.TRIAGE_REPAIR_RUNNING_HOURS`, the hygiene agent's own "no
  outcome" reading.

The read is complete only when every Triage card's thread was read to the
end; one card whose thread could not be read makes it short.

## What each run does

`settle` follows the hold-deadline alert's shape (`hold_deadline.settle`):

* one or more cards listed, complete or not: raise `NOTICE` at `WARN` through
  `pipeline_notice.raise_notice`, under the fixed `TITLE`. The door answers
  `raised` for a new alert and `repeat` for the one already open, so there is
  never more than one;
* none listed on a complete read: resolve `NOTICE`;
* none listed on a short read: call the door not at all (`UNREAD`).

The body is one line per card, oldest arrival in Triage first — its
identifier and title, why it was parked (the live hold's reason, else the
park note's first line), what the repair tried (its newest receipt's first
line) and how long it has been in Triage — and ends in `SHORT_READ_LINE` on a
short read. The run keeps no state of its own: the alert's open or resolved
state at the door is the state.

**It files no fallback card**, as the hold-deadline alert files none: a door
that cannot be reached answers `lost`, nothing is filed, and the next run —
15 minutes later — raises again. A card filed here would land in Triage as an
arrival the repair takes, and one would be filed every 15 minutes while the
door is down. The hygiene summary's per-card alarm stays as it is meanwhile.

**Its kind names no card.** `KIND`'s last `:` segment is not `DRE-<n>`, so
DRE-8606's Alarm Repair (DRE-8633) reads no card off it and gives it no
attempt: every card it lists has already had its one repair.

## The lines

Under `TAG`, on stdout, one per listed card and one closing line per run:

    triage-unrepaired: <DRE-n> <unfinished|unrepairable|started> in Triage since <YYYY-MM-DD HH:MM PT>
    triage-unrepaired: <n> card(s) listed · complete=<yes|no> · outcome=<word>

The word is the client's outcome (`raised`, `repeat`, `escalated`, `lost`,
`resolved`, `not-open` or `resolve-refused`), or `unread` for a run that
called the door not at all. The client's own `pipeline-notice:` line follows
the closing line whenever the door was called. `config/proof-reach.json`
declares the tag, and `config/notice-writers.json` the alert.

## Usage

    python3 scripts/triage_unrepaired.py

Exit 0 on every run that read the lane, whatever the door said; exit 1 when
the lane itself could not be read, after the closing line.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import board_snapshot
import hold
import hygiene_triage
import linear_ops
import pipeline_notice
import triage_repair

TAG = "triage-unrepaired:"
NOTICE = "triage_repair:fleet:unrepaired"
KIND = f"pipeline_notice:{NOTICE}"
#: Fixed: no count and no date, so every raise names the same alert.
TITLE = "Triage cards the repair could not fix"
SEVERITY = "WARN"
SHORT_READ_LINE = ("Some Triage cards could not be read on this run, so this "
                   "list may be short.")

LANE = triage_repair.LANE
#: The receipts that say the repair is over and the card is still broken.
LISTED_OUTCOMES = (triage_repair.UNFINISHED, triage_repair.UNREPAIRABLE)
#: A `started` receipt older than this recorded no outcome: the run died.
RUNNING_HOURS = hygiene_triage.TRIAGE_REPAIR_RUNNING_HOURS

#: The outcome of a short read with nothing listed: no door call at all.
UNREAD = "unread"
#: The outcome when the client itself failed, as the client names a miss.
LOST = "lost"

_PT = ZoneInfo("America/Los_Angeles")
_EARLIEST = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Unrepaired:
    """One listed card: its identifier and title, the receipt word that lists
    it, why it was parked, what the repair tried, and when it arrived in
    Triage (None when no read gives it)."""

    ident: str
    title: str
    word: str
    parked: str
    tried: str
    arrived: datetime | None


def _now() -> datetime:
    return datetime.now(UTC)


def _first_line(body: str) -> str:
    return triage_repair._first_line(body).strip()


def _pt(when: datetime | None) -> str:
    if when is None:
        return "unknown"
    return when.astimezone(_PT).strftime("%Y-%m-%d %H:%M PT")


# ── which cards ──────────────────────────────────────────────────────────────


def listed_word(records, now: datetime) -> str | None:
    """The receipt word that lists the card, or None. Only the pipeline's
    receipts count (`triage_repair.receipts`), and the newest decides."""
    found = triage_repair.receipts(records)
    if not found:
        return None
    newest = found[-1]
    word = triage_repair.outcome_of(newest)
    if word in LISTED_OUTCOMES:
        return word
    if word == triage_repair.STARTED and len(found) == 1:
        posted = triage_repair._when(newest.get("created_at")
                                     if isinstance(newest, dict) else None)
        # A started receipt of no read time is in flight, as hygiene reads it.
        if posted is not None and now - posted >= timedelta(hours=RUNNING_HOURS):
            return word
    return None


def parked_why(records, row: dict | None) -> str:
    """Why the card was parked: the live hold's reason, else the newest park
    note's (or hold stamp's) first line."""
    ours = [r for r in records or [] if triage_repair._pipeline(r)]
    stamp = hold.read_stamp([triage_repair._body(r) for r in ours])
    if stamp is not None:
        return f"held {stamp['reason']}"
    note = triage_repair.arrival_note(ours, row)
    if note is not None:
        return _first_line(triage_repair._body(note))
    return "the card carries no park note and no hold stamp"


def unrepaired(card: dict, records, now: datetime,
               table: dict | None = None) -> Unrepaired | None:
    """The card as a listed one, or None. Pure, over the card and its whole
    thread."""
    word = listed_word(records, now)
    if word is None:
        return None
    row = triage_repair.arrival_row(card, records, table)
    return Unrepaired(
        ident=card.get("identifier") or "",
        title=" ".join((card.get("title") or "").split()),
        word=word,
        parked=parked_why(records, row),
        tried=_first_line(triage_repair._body(triage_repair.receipts(records)[-1])),
        arrived=triage_repair.arrived_at(card, records, row),
    )


def read(now: datetime) -> tuple[list[Unrepaired], bool]:
    """Every listed card in Triage, and whether every thread was read whole.
    A failed board read raises: the lane was not read at all."""
    cards, _requests = board_snapshot.read_board((LANE,))
    table = triage_repair.load_table()
    listed: list[Unrepaired] = []
    complete = True
    for card in cards:
        if not triage_repair.in_scope(card):
            continue
        ident = card.get("identifier") or ""
        try:
            records = linear_ops.comment_records(ident, whole_thread=True)
        except Exception as exc:  # noqa: BLE001 — one unread card, never the run
            print(f"{TAG} {ident} — could not read its thread ({exc}); this run's "
                  "list may be short", file=sys.stderr, flush=True)
            complete = False
            continue
        found = unrepaired(card, records, now, table)
        if found is not None:
            listed.append(found)
    return listed, complete


# ── the alert ────────────────────────────────────────────────────────────────


def _oldest_first(listed: list[Unrepaired]) -> list[Unrepaired]:
    return sorted(listed, key=lambda u: u.arrived or _EARLIEST)


def _age(arrived: datetime | None, now: datetime) -> str:
    if arrived is None:
        return "in Triage for a time no read gives"
    hours = max(0, int((now - arrived).total_seconds() // 3600))
    return f"in Triage {hours} hour{'' if hours == 1 else 's'}, since {_pt(arrived)}"


def line(item: Unrepaired, now: datetime) -> str:
    return (f"{item.ident} — {item.title} — parked: {item.parked} — "
            f"repair: {item.tried} — {_age(item.arrived, now)}")


def body(listed: list[Unrepaired], *, complete: bool, now: datetime) -> str:
    """One line per card, oldest arrival first, and `SHORT_READ_LINE` on a
    short read."""
    lines = [line(u, now) for u in _oldest_first(listed)]
    if not complete:
        lines.append(SHORT_READ_LINE)
    return "\n".join(lines)


def settle(listed: list[Unrepaired], *, complete: bool, now: datetime) -> str:
    """The one writer of `NOTICE`. Returns the outcome and prints one line per
    listed card, then the closing line, then the client's own line."""
    listed = _oldest_first(listed)
    said = io.StringIO()
    try:
        # The client prints its line during the call; it is held and printed
        # after this run's closing line, which it follows.
        with contextlib.redirect_stdout(said):
            if listed:
                outcome = pipeline_notice.raise_notice(
                    NOTICE, severity=SEVERITY, title=TITLE,
                    body=body(listed, complete=complete, now=now))
            elif complete:
                outcome = pipeline_notice.resolve_notice(NOTICE)
            else:
                outcome = UNREAD
    except Exception as e:  # noqa: BLE001 — a door failure never stops the run
        print(f"{TAG} the door call for {KIND} failed — {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        outcome = LOST
    for item in listed:
        print(f"{TAG} {item.ident} {item.word} in Triage since {_pt(item.arrived)}")
    print(f"{TAG} {len(listed)} card(s) listed · complete={'yes' if complete else 'no'} "
          f"· outcome={outcome}")
    sys.stdout.write(said.getvalue())
    sys.stdout.flush()
    return outcome


def run(now: datetime | None = None) -> int:
    now = now or _now()
    try:
        listed, complete = read(now)
    except Exception as exc:  # noqa: BLE001 — named, then the run goes red
        print(f"{TAG} the {LANE} lane could not be read ({type(exc).__name__}: {exc})",
              file=sys.stderr, flush=True)
        settle([], complete=False, now=now)
        return 1
    settle(listed, complete=complete, now=now)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.parse_args(argv)
    return run()


if __name__ == "__main__":
    sys.exit(main())
