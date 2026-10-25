#!/usr/bin/env python3
"""Every hold kind has a declared due date, and one writer says when one is late
(DRE-7964).

The pipeline's three hold kinds — `proof-waiting`, `hand-work-overdue` and
`unlanded-work-watchdog` — are `kind: hold` acts in `config/pipeline-acts.json`
with `cadence_s: null`, so nothing said when one was overdue, and they sat for
weeks: the weekly agent report DRE-7960 counted 2,126.8, 338.7 and 1,060.6
hours stood still in one week. `config/hold-deadlines.json` declares, per
hold's act tag, when the pipeline chases it (`chase_after_hours`, or `null`
where the chase is the site's own existing notice on its own clock) and when
it is late (`due_hours`), with one sentence each for the chase, the clock and
why. This module is its one reader and the one writer of the late-hold alert.
`docs/hold-deadlines.md` says it in words.

## phase

`phase(tag, started, now)` is `late` from `due_at`, `chase` from `chase_at`
when the row has one, and `waiting` before either.

## settle — the one writer of the alert

A sweep site judges every hold of its kind it is responsible for and hands
`settle` the late ones, with `complete` false when any of them could not be
read on that pass:

* a non-empty list raises `hold-late:<repo>:<tag>` at `WARN` through the
  console notice door, with NO fallback card, complete or not — the title is
  `TITLE` filled in and the body one line per hold, oldest first, ending in
  `UNREAD_LINE` on a short read. Every pass raises again: the console keeps one
  alert per key, so a raise on an open alert answers `repeat` (or `escalated`)
  and refreshes its text with this pass's list;
* an empty list on a complete read resolves the key, with no recovery text;
* an empty list on a short read calls the door not at all, so the alert stays
  as the last complete pass left it, and answers `UNREAD`.

It returns the outcome — the door's word, `lost` when the door could not take
the call, `resolve-refused` when a resolve was refused — never raises out of a
door failure, and prints exactly one line under `TAG`:

    hold-deadline: <tag> repo=<repo> late=<n> cards=<ids|none> complete=<yes|no> outcome=<outcome>

A pass whose whole read did not happen does not call `settle` at all. The
door's own `pipeline-notice:` line is the one a proof reads.

## check

`python3 scripts/hold_deadline.py check` exits 1 naming each problem: a tag
that is not a `kind: hold` act's tag in `config/pipeline-acts.json`, a hold
tag that is not one of `TAGS`, a missing tag, a number that is not positive, a
`chase_after_hours` at or past `due_hours`, a missing sentence, or a
`hand-work-overdue` chase that differs from `config/hand-work-age.json`
`hours`. `tests/test_hold_deadline.py` runs it against the shipped files, which
is how CI runs it. `check` only reads `config/pipeline-acts.json`.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pipeline_notice

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "hold-deadlines.json"
ACTS = ROOT / "config" / "pipeline-acts.json"
HAND_WORK_AGE = ROOT / "config" / "hand-work-age.json"

TAG = "hold-deadline:"
TAGS = ("proof-waiting", "hand-work-overdue", "unlanded-work-watchdog")
#: The hold whose chase is the sweep's Hand-work alarm, held equal to its hours.
HAND_WORK_TAG = "hand-work-overdue"
HOLD_KIND = "hold"
SENTENCES = ("chase", "clock", "why")

WAITING = "waiting"
CHASE = "chase"
LATE = "late"

#: The outcome of a short read with nothing late: no door call at all.
UNREAD = "unread"
#: The outcome when the door could not take the call, as the client names it.
LOST = "lost"
SEVERITY = "WARN"
TITLE = "{n} {tag} hold(s) past their due date in {repo}"
UNREAD_LINE = ("Some holds of this kind could not be read on this pass, "
               "so this list may be short.")

_PT = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class LateHold:
    """One hold a site read as late: its card, its clock's start, and what it
    waits on, in words."""

    ident: str
    started: datetime
    waits_on: str


def load(path: Path = CONFIG) -> dict:
    return json.loads(Path(path).read_text())


# ── the clock ───────────────────────────────────────────────────────────────


def _row(tag: str, doc: dict | None) -> dict:
    if tag not in TAGS:
        raise ValueError(f"{tag!r} is not one of {', '.join(TAGS)}")
    return (load() if doc is None else doc)[tag]


def chase_at(tag: str, started: datetime, doc: dict | None = None) -> datetime | None:
    """When the pipeline chases the hold, or None where the chase is the
    site's own existing notice on its own clock."""
    hours = _row(tag, doc)["chase_after_hours"]
    return None if hours is None else started + timedelta(hours=hours)


def due_at(tag: str, started: datetime, doc: dict | None = None) -> datetime:
    """When the hold is late."""
    return started + timedelta(hours=_row(tag, doc)["due_hours"])


def phase(tag: str, started: datetime, now: datetime, doc: dict | None = None) -> str:
    if doc is None:
        doc = load()
    if now >= due_at(tag, started, doc):
        return LATE
    chase = chase_at(tag, started, doc)
    if chase is not None and now >= chase:
        return CHASE
    return WAITING


# ── the alert ───────────────────────────────────────────────────────────────


def notice_key(repo: str, tag: str) -> str:
    return f"hold-late:{repo}:{tag}"


def _pt(when: datetime) -> str:
    return when.astimezone(_PT).strftime("%Y-%m-%d %H:%M PT")


def _line(tag: str, hold: LateHold, now: datetime, doc: dict | None) -> str:
    due = due_at(tag, hold.started, doc)
    hours = max(0, int((now - due).total_seconds() // 3600))
    return (f"{hold.ident} — since {_pt(hold.started)}, due {_pt(due)}, "
            f"{hours} hours late, waits on {hold.waits_on}")


def body(tag: str, late: list[LateHold], *, complete: bool, now: datetime,
         doc: dict | None = None) -> str:
    """One line per hold, oldest first, and `UNREAD_LINE` on a short read."""
    lines = [_line(tag, h, now, doc) for h in sorted(late, key=lambda h: h.started)]
    if not complete:
        lines.append(UNREAD_LINE)
    return "\n".join(lines)


def settle(tag: str, repo: str, late: list[LateHold], *, complete: bool,
           now: datetime, doc: dict | None = None) -> str:
    """The one writer of `hold-late:<repo>:<tag>`. Returns the outcome and
    prints exactly one `hold-deadline:` line, whatever the door does."""
    late = sorted(late, key=lambda h: h.started)
    key = notice_key(repo, tag)
    try:
        if late:
            outcome = pipeline_notice.raise_notice(
                key, severity=SEVERITY,
                title=TITLE.format(n=len(late), tag=tag, repo=repo),
                body=body(tag, late, complete=complete, now=now, doc=doc))
        elif complete:
            outcome = pipeline_notice.resolve_notice(key)
        else:
            outcome = UNREAD
    except Exception as e:  # noqa: BLE001 — a door failure never stops a sweep
        print(f"{TAG} the door call for {key} failed — {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        outcome = LOST
    cards = ",".join(h.ident for h in late) or "none"
    print(f"{TAG} {tag} repo={repo} late={len(late)} cards={cards} "
          f"complete={'yes' if complete else 'no'} outcome={outcome}", flush=True)
    return outcome


# ── check ───────────────────────────────────────────────────────────────────


def _positive(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value > 0)


def _row_problems(tag: str, row) -> list[str]:
    if not isinstance(row, dict):
        return [f"{tag}: its row is not an object"]
    out: list[str] = []
    chase, due = row.get("chase_after_hours"), row.get("due_hours")
    if not _positive(due):
        out.append(f"{tag}: due_hours must be a positive number (got {due!r})")
    if chase is not None and not _positive(chase):
        out.append(f"{tag}: chase_after_hours must be a positive number or null "
                   f"(got {chase!r})")
    elif _positive(chase) and _positive(due) and chase >= due:
        out.append(f"{tag}: chase_after_hours {chase} is at or past due_hours {due}")
    for field in SENTENCES:
        text = row.get(field)
        if not isinstance(text, str) or not text.strip():
            out.append(f"{tag}: {field} must be a sentence")
    return out


def problems(doc, acts, hand_work_age) -> list[str]:
    """Every problem with `doc` (the hold deadlines) read against `acts` (the
    act registry) and `hand_work_age` (the Hand-work alarm's hours)."""
    if not isinstance(doc, dict):
        return ["config/hold-deadlines.json is not an object keyed by tag"]
    kinds: dict[str, str] = {}
    for act in (acts or {}).get("acts") or []:
        if isinstance(act, dict) and isinstance(act.get("tag"), str):
            kinds[act["tag"]] = act.get("kind")
    out: list[str] = []
    for tag in doc:
        kind = kinds.get(tag)
        if kind is None:
            out.append(f"{tag} is not a kind: hold act's tag in "
                       "config/pipeline-acts.json — no act carries it")
        elif kind != HOLD_KIND:
            out.append(f"{tag} is not a kind: hold act's tag in "
                       f"config/pipeline-acts.json — its act is kind: {kind}")
        elif tag not in TAGS:
            out.append(f"{tag} is a hold, but not one of the three this file "
                       f"declares ({', '.join(TAGS)})")
    for tag in TAGS:
        if tag not in doc:
            out.append(f"{tag} is missing — the file declares every one of "
                       f"{', '.join(TAGS)}")
        else:
            out.extend(_row_problems(tag, doc[tag]))
    if isinstance(doc.get(HAND_WORK_TAG), dict):
        chase = doc[HAND_WORK_TAG].get("chase_after_hours")
        hours = hand_work_age.get("hours") if isinstance(hand_work_age, dict) else None
        if not _positive(hours) or chase != hours:
            out.append(f"{HAND_WORK_TAG}: chase_after_hours {chase!r} differs from "
                       f"config/hand-work-age.json hours {hours!r}")
    return out


def check(config: Path = CONFIG, acts: Path = ACTS,
          hand_work_age: Path = HAND_WORK_AGE) -> int:
    try:
        found = problems(load(config), load(acts), load(hand_work_age))
    except (OSError, ValueError) as e:
        print(f"{TAG} a file is unreadable — {e}")
        return 1
    for p in found:
        print(f"{TAG} {p}")
    print(f"{TAG} {len(TAGS)} hold kind(s) declared, {len(found)} problem(s)")
    return 1 if found else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", nargs="?", default="check", choices=["check"])
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--acts", type=Path, default=ACTS)
    ap.add_argument("--hand-work-age", type=Path, default=HAND_WORK_AGE)
    args = ap.parse_args(argv)
    return check(args.config, args.acts, args.hand_work_age)


if __name__ == "__main__":
    sys.exit(main())
