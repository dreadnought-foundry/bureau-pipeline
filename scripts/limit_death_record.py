#!/usr/bin/env python3
"""A limit death whose marker Linear refused is still brought back (DRE-5837,
stdlib only).

`limit_recovery.py` brings a limit death back by reading ONE thing: the
`🪦 limit-death:` marker the medic writes on the card. On 2026-10-02 epic
DRE-3624's planner run died on the Claude usage limit, the medic composed the
marker, and Linear refused the write. Nothing else recorded the death, so the
sweep had nothing to bring back; the medic printed a warning asking for a
by-hand re-entry, and the card sat for two days until a person diagnosed it.

Two halves, one record:

## The medic: retry, then keep the death on the run itself

`post` writes the marker, retried (`MARKER_ATTEMPTS`, waiting `MARKER_WAITS`
between attempts). A transient refusal clears on a later attempt; an exhausted
quota will not clear inside one run, which is why there is a second place to
put the death. When every attempt is refused, `post` writes the death as a
JSON record (`make_record`), and medic.yml uploads it as the run's
`RECORD_NAME` artifact. That is a write to GitHub, not Linear: the wall that
refused the marker cannot refuse it.

The medic's own line (`medic_line`) names the card and says whether recovery
is automatic or needs a person. It is automatic when the marker landed or the
record was kept, unless the sweep would hand the marker to a person anyway
(`limit_recovery.handoff_reason`, or a `needs-human` hold the sweep honours
for that stage) — and it needs a person when neither the marker nor the record
could be kept. Only that last case asks for a re-entry by hand.

## The sweep: read the records, write the marker once Linear answers

Once per pass `reconcile.recover_limit_deaths()` lists the repo's
`RECORD_NAME` artifacts over the Actions API and hands them to `backfill()`
before limit recovery runs. For a record whose card is on the board:

  * the card already carries a marker for the record's run → nothing to do;
  * the stage has run again since the death — a `⏳` heartbeat, a `🧠`
    model-attempt, another limit-death marker, or limit recovery's own
    receipt, newer than the death — → nothing to do. The sweep's other notes
    (`🧹` and the rest) are deliberately NOT that evidence: they say the card
    is stuck, not that it moved, and DRE-3624 sat two days collecting them;
  * otherwise the marker is written now, with a line saying why it is late,
    and put on the card this pass too, so `limit_recovery.recover()` reads it
    in the same pass and re-enters the stage once the limit lifts.

A record that was settled either way is deleted. A marker write that does not
land is an `ERROR:` line (the sweep goes red, like every other write) and the
record is kept for the next pass. A record whose card is not on the board is
left alone; it expires with the artifact's retention.

The writes are injected — the comment through `lops`, the artifact delete
through `delete` — so the decision is testable without Linear or GitHub, the
same shape `limit_recovery.recover()` has.

CLI (medic.yml):

    python3 limit_death_record.py post <CARD> <MARKER BODY> --run-id <id> \\
        --died-at <iso> --record <path> [--snapshot <path>]
    python3 limit_death_record.py say <CARD> --outcome recorded|lost \\
        --record <path> [--snapshot <path>]

`post` prints `marker=written` or `marker=refused` on stdout for
`$GITHUB_OUTPUT`, and its line for the run log on stderr. `say` prints the
line for a refused marker once the upload's outcome is known.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dead_run  # noqa: E402 — the marker's one definition
import limit_recovery  # noqa: E402 — what the sweep will do with the marker

#: The artifact the medic keeps a refused marker in. FIXED, because the
#: Actions API filters artifacts by exact name, and that filter is how the
#: sweep finds every record in one listing.
RECORD_NAME = "limit-death-record"
RECORD_FILE = "record.json"

#: How many times the marker write is attempted, and the waits between them.
#: Three, like `dead_run.PARK_ATTEMPTS`: enough to clear a blip, and no
#: pretence that an exhausted quota clears inside one run.
MARKER_ATTEMPTS = 3
MARKER_WAITS = (10, 30)

WRITTEN = "written"
RECORDED = "recorded"
LOST = "lost"

_CARD = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")

#: What says the stage ran again after the death: a run's heartbeat, its
#: model-attempt receipt, and limit recovery's own two receipts. Another
#: limit-death marker counts too, and is checked separately.
RESTARTED = ("⏳", "🧠", limit_recovery.RECOVERY_MARK, limit_recovery.HANDOFF_MARK)


# --------------------------------------------------------------------------- #
# the medic's half                                                             #
# --------------------------------------------------------------------------- #


def post_marker(card: str, body: str, *, comment, attempts: int = MARKER_ATTEMPTS,
                waits=None, sleep=time.sleep, log=print) -> bool:
    """Write the marker, retried. True iff it landed.

    `comment(card, body)` is `linear_ops.cmd_comment`: it raises on a refusal
    and answers a condition string, rather than raising, when the card is full
    (DRE-3343). Both are a marker that did not land."""
    waits = MARKER_WAITS if waits is None else waits
    for n in range(1, attempts + 1):
        try:
            answer = comment(card, body)
        except Exception as exc:  # noqa: BLE001 — any Linear failure: blip or quota
            log(f"limit-death marker on {card}: attempt {n}/{attempts} refused: {exc}")
        else:
            if not isinstance(answer, str):
                return True
            log(f"limit-death marker on {card}: attempt {n}/{attempts} refused: {answer}")
        if n < attempts:
            sleep(waits[min(n - 1, len(waits) - 1)] if waits else 0)
    return False


def make_record(card: str, run_id: str, died_at: str, body: str) -> dict:
    """The death as the run keeps it: the card, the run, when it died, and the
    marker exactly as the medic composed it."""
    return {"card": card, "run": str(run_id), "died_at": died_at, "marker": body}


def read_record(text: str) -> dict | None:
    """A record off its JSON, or None when it is not one: the card must be a
    card id, the marker must parse, and the marker must name the record's run."""
    try:
        rec = json.loads(text or "")
    except ValueError:
        return None
    if not isinstance(rec, dict):
        return None
    card = rec.get("card")
    body = rec.get("marker")
    if not isinstance(card, str) or not _CARD.match(card) or not isinstance(body, str):
        return None
    parsed = dead_run.parse_limit_marker(body)
    if parsed is None or parsed["run"] != str(rec.get("run")):
        return None
    return {"card": card, "run": str(rec["run"]), "died_at": str(rec.get("died_at") or ""),
            "marker": body}


def needs_a_person(marker: dict | None, labels=()) -> str | None:
    """Why the sweep will NOT bring this card back on its own, or None when it
    will. The sweep's own rules, asked rather than restated."""
    if marker is None:
        return "the marker could not be read, so nothing says which stage to re-enter"
    held = any((label or "").lower() == dead_run.HOLD_LABEL for label in labels or ())
    if held and limit_recovery.hold_blocks_stage(marker.get("stage") or ""):
        return (f"the card carries {dead_run.HOLD_LABEL}, and the sweep leaves a "
                f"held card's {marker.get('stage')} stage alone")
    reason = limit_recovery.handoff_reason({}, marker)
    return reason.split(". ")[0] if reason else None


def medic_line(card: str, outcome: str, marker: dict | None, *, labels=(),
               attempts: int = MARKER_ATTEMPTS) -> str:
    """The medic's one line for the run log: names the card, and says whether
    recovery is automatic or needs a person."""
    m = marker or {}
    what = (f"{card} hit the {m.get('kind') or 'unknown'} limit in the "
            f"{m.get('stage') or 'unknown'} stage on run {m.get('run') or 'unknown'}")
    if outcome == WRITTEN:
        title, where = "Limit death marked", "The marker is on the card."
    elif outcome == RECORDED:
        title = "Limit marker refused, death kept on this run"
        where = (f"Linear refused the marker write {attempts} times, so the death "
                 f"is kept on this run as the `{RECORD_NAME}` artifact; the "
                 f"reconcile sweep reads it and writes the marker once Linear "
                 f"answers.")
    else:
        title = "Limit death not recorded"
        where = ("Linear refused the marker write and the run's own record could "
                 "not be kept either, so nothing tells the sweep about this death.")
        return (f"::warning title={title}::{what}. {where} Recovery needs a person: "
                f"re-enter the {m.get('stage') or 'failed'} stage on {card} once the "
                f"limit lifts.")
    blocked = needs_a_person(marker, labels)
    if blocked:
        return (f"::warning title={title}::{what}. {where} Recovery needs a person: "
                f"{blocked}.")
    level = "notice" if outcome == WRITTEN else "warning"
    return (f"::{level} title={title}::{what}. {where} Recovery is automatic: the "
            f"reconcile sweep re-enters the stage once the limit lifts, and nobody "
            f"needs to touch the card.")


def _labels(snapshot: str, card: str) -> list[str]:
    """The card's labels off the retry gate's snapshot (medic_retry), when it
    is this card's — no second Linear read."""
    if not snapshot:
        return []
    try:
        with open(snapshot, encoding="utf-8") as f:
            facts = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(facts, dict) or facts.get("card") != card:
        return []
    return [str(label) for label in facts.get("labels") or []]


def _post_cli(args) -> int:
    import linear_ops  # local: only the medic's write needs the Linear seam

    marker = dead_run.parse_limit_marker(args.body)
    labels = _labels(args.snapshot, args.card)
    landed = post_marker(args.card, args.body,
                         comment=lambda card, body: linear_ops.cmd_comment(card, body),
                         log=lambda line: print(line, file=sys.stderr))
    # One place per death: a landed marker is what limit_recovery reads, and
    # the record exists only for a death Linear would not let the medic mark.
    if landed:
        print(f"marker={WRITTEN}")
        print(medic_line(args.card, WRITTEN, marker, labels=labels), file=sys.stderr)
        return 0
    rec = make_record(args.card, args.run_id, args.died_at, args.body)
    os.makedirs(os.path.dirname(os.path.abspath(args.record)), exist_ok=True)
    with open(args.record, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    print("marker=refused")
    return 0


def _say_cli(args) -> int:
    try:
        with open(args.record, encoding="utf-8") as f:
            rec = read_record(f.read())
    except OSError:
        rec = None
    outcome = args.outcome if rec is not None else LOST
    marker = dead_run.parse_limit_marker(rec["marker"]) if rec else None
    print(medic_line(args.card, outcome, marker, labels=_labels(args.snapshot, args.card)))
    return 0


# --------------------------------------------------------------------------- #
# the sweep's half                                                             #
# --------------------------------------------------------------------------- #


def records_from_listing(listing_text: str, download) -> list[dict]:
    """The records an Actions artifact listing names. `download(run_id)` is the
    record file's text from that run, or None when it could not be read; an
    expired artifact is skipped, and so is anything that is not a record."""
    try:
        artifacts = (json.loads(listing_text or "") or {}).get("artifacts") or []
    except (ValueError, AttributeError):
        return []
    records = []
    for artifact in artifacts:
        if artifact.get("expired") or artifact.get("name") != RECORD_NAME:
            continue
        run_id = str((artifact.get("workflow_run") or {}).get("id") or "")
        if not run_id.isdigit():
            continue
        rec = read_record(download(run_id) or "")
        if rec is not None:
            records.append({**rec, "artifact": str(artifact.get("id"))})
    return records


def _moment(value: str) -> datetime | None:
    try:
        when = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def settled(card: dict, rec: dict) -> str | None:
    """Why the card needs no marker from this record, or None when it does."""
    died = _moment(rec.get("died_at") or "")
    for n in (card.get("comments") or {}).get("nodes") or []:
        body = n.get("body") or ""
        marker = dead_run.parse_limit_marker(body)
        if marker is not None and marker["run"] == rec["run"]:
            return f"already carries the marker for run {rec['run']}"
        at = _moment(n.get("createdAt") or "")
        if died is None or at is None or at <= died:
            continue
        if marker is not None:
            return "a later limit death is already marked"
        if body.lstrip().startswith(RESTARTED):
            return "the stage has run again since the death"
    return None


def late_marker(rec: dict) -> str:
    """The medic's marker, with one line saying why the sweep wrote it."""
    died = _moment(rec.get("died_at") or "")
    when = dead_run.pacific(died) if died else "when the run died"
    return (f"{rec['marker']}\n\nWritten by the reconcile sweep from run "
            f"{rec['run']}'s own record: Linear refused the medic's write at "
            f"{when}, so the run kept the death itself (DRE-5837).")


def backfill(lops, cards, records, *, delete) -> list[str]:
    """One pass over the records. Returns the lines the sweep prints; `ERROR:`
    lines are marker writes that did not land. `delete(artifact_id)` removes a
    settled record; a failed delete only costs a re-read next pass."""
    lines: list[str] = []
    board = {c.get("identifier"): c for c in cards or []}
    for rec in records or []:
        ident = rec["card"]
        card = board.get(ident)
        if card is None:
            continue
        why = settled(card, rec)
        if why is None:
            body = late_marker(rec)
            try:
                answer = lops.cmd_comment(ident, body)
            except Exception as exc:  # noqa: BLE001 — one card's failure never costs the next its turn
                answer = f"{type(exc).__name__}: {exc}"
            else:
                answer = answer if isinstance(answer, str) else None
            if answer is not None:
                lines.append(f"ERROR: {RECORD_NAME} {ident}: the marker for run "
                             f"{rec['run']} did not land: {answer}")
                continue
            nodes = (card.setdefault("comments", {}) or {}).setdefault("nodes", [])
            nodes.insert(0, {"body": body,
                             "createdAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")})
            why = f"marker written from run {rec['run']}'s own record"
        lines.append(f"{RECORD_NAME}: {ident} {why}")
        try:
            delete(rec.get("artifact"))
        except Exception as exc:  # noqa: BLE001 — the record is re-read next pass
            lines.append(f"{RECORD_NAME}: {ident} record {rec.get('artifact')} not "
                         f"deleted ({exc}) — it is settled again next pass")
    return lines


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    post = sub.add_parser("post")
    post.add_argument("card")
    post.add_argument("body")
    post.add_argument("--run-id", required=True)
    post.add_argument("--died-at", default="")
    post.add_argument("--record", required=True)
    post.add_argument("--snapshot", default="")
    say = sub.add_parser("say")
    say.add_argument("card")
    say.add_argument("--outcome", choices=(RECORDED, LOST), required=True)
    say.add_argument("--record", required=True)
    say.add_argument("--snapshot", default="")
    args = parser.parse_args(argv)
    if args.command == "post":
        return _post_cli(args)
    return _say_cli(args)


if __name__ == "__main__":
    sys.exit(main())
