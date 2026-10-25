#!/usr/bin/env python3
"""The groomer's week, counted and posted on the standing groom card (DRE-7970).

The only scoring instrument before this was `groomer_score.py`: it grades the
ranked read against one hand audit (DRE-3151) and ran once, by hand, on
2026-09-12. Nothing counted what a person took off a proposal, or why. The
parent epic's baseline had to be counted by hand from the console database:
28 of 191 proposed cards removed over the 13 proposals of 2026-09-26 to
2026-10-10, and 7 of the 10 Cancel rows in that time agreed. This module
counts the same numbers every week. It leaves `groomer_score.py` alone.

## What it reads

Every read goes through a reader that already exists, and none is copied:

  * the standing card's WHOLE thread, through `groomer.decision_records(...,
    whole_thread=True)`, so every decision is vouched as the groomer vouches
    it. In it: each proposal (`groomer.parse_proposal_comment` — its Planning
    `batch` and its `cancel` list), each per-card marker
    (`groomer.per_card_markers`), each `groom-approved` and `groom-declined`
    marker (`groomer.decision_match`, a decider's only), and each
    `groom-drained` record. `groomer.py` has no reader for that record's
    rows, so `drained_rows` here reads them in the grammar
    `groomer.drained_record` writes, and the tests pin it to that output;
  * the console's decisions, through `bureau_read.groom_removals()` with its
    `reasons`. On `ReadUnknown` the score is taken from the thread's markers
    alone, and the comment says so in a line naming the reason, as the
    proposal page does.

## The week, and what each number means

  * The week is seven Pacific days, Sunday to Saturday, ending on
    `--week-ending` — by default the Saturday before today, Pacific. The
    week's proposals are those posted in it.
  * A proposal is ANSWERED when a `groom-drained` record, a `groom-declined`
    marker or any per-card decision (a door row or a marker) names its id.
  * `proposed` is the Planning rows plus the Cancel rows of the answered
    proposals.
  * `removed` counts the rows of answered proposals a person took off, once
    per proposal and card. A Planning row is taken off by a `not-doing`,
    `hold` or `cancel` decision, or a `groom-excluded` or `groom-held` marker,
    naming that proposal; a Cancel row by a `not-doing` or `hold`, or one of
    those two markers. A door row and the marker the console mirrors it with
    are one decision about one row, so the row counts once. Where a row has
    several decisions, the newest is the one that stands — the same rule the
    drain follows — so a `put-back` or `groom-added` after an exclusion is no
    removal, and on an exact tie the door's row wins (`groom_memory`).
  * Each removal's reason is the door row's label for its key; failing that,
    the row's `comment`, then the marker's tail, then `no reason recorded`.
    `removed_by_reason` groups the removals by reason, count first.
  * `removed_share` is `removed / proposed` to four places, or null when
    nothing was proposed.
  * `cancel` reads every Cancel row of every proposal in the week, and puts
    each in exactly one of three: `agreed` when its proposal's drained record
    reads the row `cancelled` or `already gone` (nothing held it back; the
    drain found it out of the lane already); `kept` when the record reads it
    any other way (`held back`, `added`, `refused`) or names no row for it,
    or when the proposal is answered and no record exists (declined, or
    decided card by card and not drained); `unanswered` when the proposal is
    not answered, so `unanswered` is the Cancel rows of exactly the proposals
    marked `answered: false`. `rows` is every Cancel row, so the three always
    sum to it.

## Posting

`--post` writes the comment through `linear_ops.cmd_comment`, as the
pipeline's own Linear identity. The thread it scored is read first: a comment
for the same week is never written twice, and a second run prints
`groom-scorecard: already posted <start>..<end>` on stdout and writes nothing.
When the card's thread is full, `cmd_comment` returns Linear's comment-cap
condition rather than raising: the run prints a `not posted:` line naming it
on stderr and exits 1.
The comment opens with `📊 groom-scorecard:`, which no decision reader in
`groomer.py` matches, so it can never be read as a decision.

Every run prints exactly one `groom-scorecard:` line on stderr.

CLI:

    python3 scripts/groom_scorecard.py score --card DRE-4541 \\
        [--week-ending YYYY-MM-DD] [--post] [--out score.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bureau_read  # noqa: E402 — the console's record of groom decisions (DRE-7821)
import dead_run  # noqa: E402 — ONE Pacific clock for a time a person reads
import groom_memory  # noqa: E402 — the console's decision words and tail grammar
import groomer  # noqa: E402 — every reader of the standing card's thread
import linear_ops  # noqa: E402

MARK = "📊"
SCORECARD_TAG = "groom-scorecard"
LINE_TAG = "groom-scorecard:"
SCHEMA = "groom-scorecard/1"

NO_REASON = "no reason recorded"
#: The opening of the comment's line when the console's record was not read —
#: the proposal page's words (`groomer.DOOR_UNREAD`), said of a week.
DOOR_UNREAD = "The console's record of your decisions could not be read"

#: The decisions that take a row off, by the list it is on. A `cancel` on a
#: Cancel row is agreement with it, so it takes nothing off.
REMOVING_DECISIONS = {"planning": ("not-doing", "hold", "cancel"),
                      "cancel": ("not-doing", "hold")}
#: The markers that take a row off, on either list.
REMOVING_MARKERS = ("excluded", "held")

#: The drained outcomes of a Cancel row the person agreed with: nothing held
#: it back, and the drain cancelled it or found it out of the lane already.
#: Every other outcome — `held back`, `added`, `refused`, or no row — left the
#: card uncancelled: kept.
AGREED = ("cancelled", "already gone")

# A row of a drained record's table: `| <order or —> | DRE-N | <outcome> |
# <why> |`, as `groomer.drained_record` writes it. The header and the divider
# name no card, so neither matches.
_DRAINED_ROW = re.compile(r"^\|[^|]*\|\s*(DRE-\d+)\s*\|\s*([^|]*?)\s*\|")


# --------------------------------------------------------------------------- #
# the week                                                                     #
# --------------------------------------------------------------------------- #

def week_of(week_ending: str) -> tuple[str, str]:
    """`(Sunday, Saturday)` of the week ending on `week_ending`, a Saturday."""
    end = date.fromisoformat(week_ending)
    if end.weekday() != 5:
        raise ValueError(f"{week_ending} is a {end.strftime('%A')}, and a "
                         f"week ends on a Saturday")
    return (end - timedelta(days=6)).isoformat(), end.isoformat()


def default_week_ending(now: datetime | None = None) -> str:
    """The Saturday before today, Pacific — on a Saturday, the one before."""
    today = date.fromisoformat(groomer.pacific_day(
        (now or datetime.now(timezone.utc)).isoformat()))
    return (today - timedelta(days=(today.weekday() - 5) % 7 or 7)).isoformat()


def _saturday(text: str) -> str:
    try:
        week_of(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    return text


def _instant(stamp: str | None) -> datetime:
    return groom_memory._instant(stamp or groomer.NO_STAMP)


# --------------------------------------------------------------------------- #
# the thread                                                                   #
# --------------------------------------------------------------------------- #

def drained_rows(body: str | None) -> dict | None:
    """A `groom-drained` record read back: `{"proposal", "rows": {card:
    outcome}}`, or None when `body` is not one. A card on two rows keeps its
    first (the drain gives no card two)."""
    found = groomer.decision_match(groomer.DRAINED_TAG, body)
    if not found:
        return None
    rows: dict[str, str] = {}
    for line in (body or "").splitlines():
        row = _DRAINED_ROW.match(line.strip())
        if row:
            rows.setdefault(row.group(1), row.group(2))
    return {"proposal": found.group(1), "rows": rows}


def read_thread(records: list[dict], standing_card: str) -> dict:
    """The standing card's vouched thread, read once: `proposals` (each a
    parsed proposal with its `posted_at`, first comment per id), `drained`
    (`{pid: {card: outcome}}`, first record per id), `declined` and
    `approved` (the ids a decider's marker names), `markers`
    (`groomer.per_card_markers`) and `scorecards` (each comment's first line
    that opens with this module's marker)."""
    proposals: dict[str, dict] = {}
    drained: dict[str, dict] = {}
    declined, approved, scorecards = set(), set(), []
    for record in records:
        body = record.get("body") or ""
        parsed = groomer.parse_proposal_comment(body)
        if parsed:
            proposals.setdefault(parsed["id"], {
                **parsed, "posted_at": record.get("created_at")})
            continue
        read = drained_rows(body)
        if read:
            drained.setdefault(read["proposal"], read["rows"])
            continue
        if body.strip().startswith(f"{MARK} {SCORECARD_TAG}"):
            scorecards.append(body.strip().splitlines()[0])
            continue
        if not groomer._decides(record):
            continue
        for tag, ids in ((groomer.DECLINE_TAG, declined),
                         (groomer.APPROVAL_TAG, approved)):
            match = groomer.decision_match(tag, body)
            if match:
                ids.add(match.group(1))
    return {"proposals": list(proposals.values()), "drained": drained,
            "declined": declined, "approved": approved,
            "markers": groomer.per_card_markers(records, standing_card),
            "scorecards": scorecards}


# --------------------------------------------------------------------------- #
# the score                                                                    #
# --------------------------------------------------------------------------- #

def score(records: list[dict], *, standing_card: str, week_ending: str,
          door: dict | None, door_problem: str | None = None,
          thread: dict | None = None) -> dict:
    """The week's scorecard, the dict the contract names.

    `records` is the standing card's whole thread, vouched; `door` is
    `bureau_read.groom_removals()`'s answer, or None when `door_problem` says
    why it could not be read. `thread` is `read_thread(records,
    standing_card)` when the caller has read it already.
    """
    start, end = week_of(week_ending)
    thread = thread or read_thread(records, standing_card)
    week = [p for p in thread["proposals"] if p["posted_at"]
            and start <= groomer.pacific_day(p["posted_at"]) <= end]
    ids = {p["id"] for p in week}

    reasons = (door or {}).get("reasons") if door_problem is None else None
    labels = groom_memory._labels(reasons)
    keys = {label: key for key, label in labels.items()}
    rows = [r for r in ((door or {}).get("decisions") or [])
            if door_problem is None and r.get("standing_card") == standing_card
            and r.get("proposal_id") in ids and r.get("card")
            and r.get("decision") in groom_memory.DECISIONS]
    markers = [m for m in thread["markers"] if m["proposal_id"] in ids]

    answered = (set(thread["drained"]) | thread["declined"]
                | {m["proposal_id"] for m in markers}
                | {r["proposal_id"] for r in rows}) & ids

    # Every decision about a (proposal, card), oldest first; a door row sorts
    # after a marker of the same instant, so it wins the tie.
    decided: dict[tuple[str, str], list] = {}
    for index, m in enumerate(markers):
        decided.setdefault((m["proposal_id"], m["card"]), []).append(
            (_instant(m["created_at"]), 0, index, {"marker": m}))
    for index, r in enumerate(rows):
        decided.setdefault((r["proposal_id"], r["card"]), []).append(
            (_instant(r.get("recorded_at")), 1, index, {"row": r}))

    def removes(entry: dict, listed: str) -> bool:
        if "row" in entry:
            return entry["row"]["decision"] in REMOVING_DECISIONS[listed]
        return entry["marker"]["kind"] in REMOVING_MARKERS

    removals: list[tuple[str | None, str]] = []
    proposed = 0
    for p in week:
        if p["id"] not in answered:
            continue
        listed = {r["identifier"]: "planning" for r in p["batch"]}
        listed.update({r["identifier"]: "cancel" for r in p["cancel"]})
        proposed += len(p["batch"]) + len(p["cancel"])
        for card, which in listed.items():
            entries = sorted(decided.get((p["id"], card), []),
                             key=lambda e: e[:3])
            if not entries or not removes(entries[-1][3], which):
                continue
            taking = [e[3] for e in entries if removes(e[3], which)]
            removals.append(_reason(taking, labels, keys))

    groups: dict[tuple, int] = Counter(removals)
    by_reason = sorted(({"reason": key, "label": label, "count": count}
                        for (key, label), count in groups.items()),
                       key=lambda g: (-g["count"], g["label"]))

    cancel = {"rows": 0, "agreed": 0, "kept": 0, "unanswered": 0}
    for p in week:
        outcomes = thread["drained"].get(p["id"])
        for r in p["cancel"]:
            cancel[_cancel_bucket(outcomes, r["identifier"],
                                  p["id"] in answered)] += 1
    cancel["rows"] = sum(len(p["cancel"]) for p in week)
    assert cancel["agreed"] + cancel["kept"] + cancel["unanswered"] \
        == cancel["rows"], cancel

    return {
        "schema": SCHEMA, "week_start": start, "week_end": end,
        "standing_card": standing_card,
        "proposals": [{"id": p["id"], "posted_at": p["posted_at"],
                       "planning": len(p["batch"]), "cancel": len(p["cancel"]),
                       "answered": p["id"] in answered} for p in week],
        "proposed": proposed, "removed": len(removals),
        "removed_share": round(len(removals) / proposed, 4) if proposed else None,
        "removed_by_reason": by_reason,
        "cancel": cancel,
        "memory_source": "fallback" if door_problem else "record",
        "door_problem": door_problem,
    }


def _cancel_bucket(outcomes: dict | None, card: str, answered: bool) -> str:
    """Which of `agreed`, `kept` and `unanswered` one Cancel row is. An
    answered proposal with no drained record — declined, or decided card by
    card and not drained — cancelled nothing: kept."""
    if outcomes is None:
        return "kept" if answered else "unanswered"
    return "agreed" if outcomes.get(card) in AGREED else "kept"


def _reason(taking: list[dict], labels: dict, keys: dict) -> tuple[str | None, str]:
    """`(key, label)` for one removal, off the decisions that took it off,
    oldest first: the newest door row's label for its key, else the newest
    row comment, else the newest marker tail, else `NO_REASON`."""
    rows = [e["row"] for e in taking if "row" in e]
    tails = [groom_memory._SEPARATOR.sub("", e["marker"].get("tail") or "").strip()
             for e in taking if "marker" in e]
    for r in reversed(rows):
        if r.get("reason") and labels.get(r["reason"]):
            return r["reason"], labels[r["reason"]]
    for r in reversed(rows):
        if (r.get("comment") or "").strip():
            return r.get("reason") or None, " ".join(r["comment"].split())
    for tail in reversed(tails):
        if tail:
            return keys.get(tail), " ".join(tail.split())
    return None, NO_REASON


# --------------------------------------------------------------------------- #
# the comment and the line                                                     #
# --------------------------------------------------------------------------- #

def header(start: str, end: str) -> str:
    """The comment's first line, exactly."""
    return f"{MARK} {SCORECARD_TAG}: {start}..{end}"


def _words(text: str) -> str:
    return " ".join(str(text).split()).replace("`", "'")


def render_comment(result: dict, *, approved: set | None = None) -> str:
    """The comment: the header, plain-English lines, one fenced JSON block."""
    start, end = result["week_start"], result["week_end"]
    proposals = result["proposals"]
    answered = [p for p in proposals if p["answered"]]
    share = result["removed_share"]
    w = [header(start, end), "",
         f"The groomer's week on {result['standing_card']}, Sunday {start} to "
         f"Saturday {end} (Pacific).", ""]
    w.append(f"- Proposals posted: {len(proposals)} — {len(answered)} answered.")
    w.append(f"- The answered proposals put {result['proposed']} cards in front "
             f"of you, Planning and Cancel rows together.")
    w.append(f"- You removed {result['removed']}"
             + (f" — {share:.2%} of them." if share is not None else "."))
    for group in result["removed_by_reason"]:
        w.append(f"  - {_words(group['label'])}: {group['count']}")
    c = result["cancel"]
    w.append(f"- Cancel rows: {c['rows']} — {c['agreed']} agreed, {c['kept']} "
             f"kept, {c['unanswered']} never answered.")
    silent = [p for p in proposals if not p["answered"]]
    if silent:
        named = ", ".join(
            f"`{p['id']}` (posted {_pacific(p['posted_at'])}"
            + ("; approved, never drained" if p["id"] in (approved or ()) else "")
            + ")" for p in silent)
        w.append(f"- Not answered, so in neither the cards put in front of you "
                 f"nor the ones removed: {named}.")
    if result["door_problem"]:
        w += ["", f"{DOOR_UNREAD} (`{_words(result['door_problem'])}`), so this "
                  f"score is taken from the decisions on this card's thread "
                  f"alone."]
    w += ["", "```json", json.dumps(result, indent=2, ensure_ascii=False), "```"]
    return "\n".join(w) + "\n"


def _pacific(stamp: str | None) -> str:
    try:
        return dead_run.pacific(_instant(stamp))
    except ValueError:
        return "at an unknown time"


_FENCED = re.compile(r"^```json\n(.*?)\n```$", re.S | re.M)


def parse_comment(body: str | None) -> dict | None:
    """The scorecard dict a comment carries, or None when it is not one."""
    text = (body or "").strip()
    if not text.startswith(f"{MARK} {SCORECARD_TAG}:"):
        return None
    found = _FENCED.search(text)
    if not found:
        return None
    try:
        parsed = json.loads(found.group(1))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("schema") == SCHEMA else None


def score_line(result: dict) -> str:
    """The one tagged line, in the contract's grammar."""
    c = result["cancel"]
    share = result["removed_share"]
    return (f"{LINE_TAG} week={result['week_start']}..{result['week_end']} "
            f"proposals={len(result['proposals'])} "
            f"answered={sum(p['answered'] for p in result['proposals'])} "
            f"proposed={result['proposed']} removed={result['removed']} "
            f"share={'none' if share is None else f'{share:.4f}'} "
            f"cancel_rows={c['rows']} agreed={c['agreed']} kept={c['kept']} "
            f"unanswered={c['unanswered']} source={result['memory_source']}")


# --------------------------------------------------------------------------- #
# the run                                                                      #
# --------------------------------------------------------------------------- #

def read_door(since: str) -> tuple[dict | None, str | None]:
    """`bureau_read.groom_removals(since)`, or `(None, "<reason>: <detail>")`."""
    try:
        return bureau_read.groom_removals(since), None
    except bureau_read.ReadUnknown as e:
        return None, f"{e.reason}: {e.detail}" if e.detail else e.reason


def run(lops, *, card: str, week_ending: str, post: bool = False,
        out: str | None = None) -> int:
    start, end = week_of(week_ending)
    records = groomer.decision_records(lops, card, whole_thread=True)
    # From the week's first Pacific instant: no decision about the week's
    # proposals is older than the proposal it names.
    since = datetime.combine(date.fromisoformat(start), datetime.min.time(),
                             tzinfo=_pacific_zone()).astimezone(timezone.utc)
    answer, problem = read_door(since.strftime("%Y-%m-%dT%H:%M:%SZ"))
    thread = read_thread(records, card)
    result = score(records, standing_card=card, week_ending=week_ending,
                   door=answer, door_problem=problem, thread=thread)
    body = render_comment(result, approved=thread["approved"])
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    # `cmd_comment` returns the comment-cap condition rather than raising
    # (DRE-3343): a scorecard that did not land is a failed run.
    refused = None
    if post:
        if header(start, end) in thread["scorecards"]:
            print(f"{LINE_TAG} already posted {start}..{end}")
        else:
            refused = lops.cmd_comment(card, body)
    else:
        print(body)
    print(score_line(result), file=sys.stderr)
    if refused:
        print(f"not posted: the scorecard for {start}..{end} could not be "
              f"written on {card} — {refused}", file=sys.stderr)
        return 1
    return 0


def _pacific_zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Los_Angeles")
    except Exception:  # noqa: BLE001 — no tz database: UTC, a few hours early
        return timezone.utc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_score = sub.add_parser("score", help="count one week on the standing card")
    p_score.add_argument("--card", required=True,
                         help="the standing groom card")
    p_score.add_argument("--week-ending", dest="week_ending", type=_saturday,
                         help="the Saturday the week ends on, YYYY-MM-DD "
                              "(default: the Saturday before today, Pacific)")
    p_score.add_argument("--post", action="store_true",
                         help="write the comment on the card, once per week")
    p_score.add_argument("--out", help="write the scorecard JSON here")
    args = parser.parse_args(argv)
    return run(linear_ops, card=args.card,
               week_ending=args.week_ending or default_week_ending(),
               post=args.post, out=args.out)


if __name__ == "__main__":
    sys.exit(main())
