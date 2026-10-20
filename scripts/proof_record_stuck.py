#!/usr/bin/env python3
"""The sweep names a proof record nothing can move (DRE-7742).

On 2026-10-20 four proof records were open, 2.5 to 12 days old, with no part
of the pipeline able to move them. DRE-5357 (#3876) and DRE-5638 (portico
#1023) sat in `Queued`, which the proof dispatcher does not read; DRE-6008
(#3381) carried `needs-human`; and #3876 modified an existing record file
instead of adding one, so the gate read no record at all. Nothing said so.

The sweep's `name_unmovable_proof_records` backstop now reads every open
`agent/DRE-<n>-proof-record` pull request of its repo (`proof_record_branch`)
and the card its branch names, and finds four shapes, each with what moves it:

1. **A lane the dispatcher does not read** — anything but `proof_dispatch`'s
   `FIRST_RUN_LANE`, `RETURN_LANE` and `RERUN_LANE`.
2. **`needs-human`** — except the sweep's own review-cap park in
   `Green Light` (`hold.reason_of` is `review-cap-spent`), which the
   dispatcher's re-run branch reads (DRE-6406).
3. **A draft with no hold** — the critic skips a draft (DRE-5801) and the gate
   never merges one, so a draft waits on a person; with no open
   `🔬 proof-waiting` hold (`proof_record.open_holds`) nothing says which.
4. **No record the gate can read** — `proof_record.find_record` over the pull
   request's files finds no one added `.md` under `docs/` or `architecture/`,
   and its reason is quoted.

One note per pull request, one line per shape, posted on the card at most once
a day: a note for the same pull request in the card's comment window less than
`DAY_HOURS` old is the once. A pull request open past `ALERT_HOURS` also raises
a WARN console alert, `proof_record_stuck:<repo>:<card>`, every pass it stands
— the console keeps one alert per key, so a raise while it is open delivers
nothing again — and the alert is resolved once a record named before reads as
none of the four. Nothing here moves a lane, writes a label or touches the pull
request. A card that cannot be read names nothing, said in the log; a note that
does not land, and an alert the door lost, are failures that turn the sweep red.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import hold
import linear_ops
import pipeline_notice
import proof_dispatch
import proof_record
import reconcile
import spoken_thread

PREFIX = "proof-record-stuck:"
#: Opens the card note, then `#<number>:` — the once-a-day key.
MARK = "🧱 proof-record-stuck"
DAY_HOURS = 24
ALERT_HOURS = 72
#: The lanes the proof dispatcher reads a PROOF card in.
DISPATCHER_LANES = (proof_dispatch.FIRST_RUN_LANE, proof_dispatch.RETURN_LANE,
                    proof_dispatch.RERUN_LANE)
LIST_FIELDS = "number,url,headRefName,isDraft,createdAt,files"
LIST_LIMIT = 100

CARD_QUERY = """query($id: String!) { issue(id: $id) {
             identifier state { name } labels { nodes { name } } %s } }""" % (
    linear_ops.COMMENT_WINDOW_GQL)


def _when(stamp) -> datetime | None:
    try:
        return datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _labels(card: dict) -> set:
    return {(n.get("name") or "").lower() for n in (card.get("labels") or {}).get("nodes") or []}


def _viewer() -> str | None:
    """Who this key is, so the pipeline's own `🔬 proof-observed` discharges a
    hold. Unread, every comment is of unknown author and discharges nothing —
    a draft is then read as held, and is not named."""
    try:
        return linear_ops.viewer_id()
    except Exception:  # noqa: BLE001 — unread is "unknown", never "nobody"
        return None


def _marker(number) -> str:
    return f"{MARK} #{number}:"


def notice_key(card: str) -> str:
    return f"proof_record_stuck:{reconcile.REPO_SLUG}:{card}"


def shapes(card: dict, pr: dict) -> list:
    """The lines naming why nothing moves this record, one per shape, each
    saying what moves it. Empty when the record is one the pipeline moves."""
    lane = (card.get("state") or {}).get("name") or "no lane"
    window = linear_ops.window_nodes(card.get("comments"))
    bodies = [n.get("body") or "" for n in window]
    lines = []
    if lane not in DISPATCHER_LANES:
        lines.append(
            f"The card is in {lane}, a lane the proof dispatcher does not read — "
            f"moving it to {proof_dispatch.RERUN_LANE} puts the record in front of "
            "the critic and the proof run's re-run, and closing the pull request "
            "ends it if the work is finished: either moves it.")
    labels = (card.get("labels") or {}).get("nodes") or []
    if hold.HOLD_LABEL in _labels(card) and not (
            lane == proof_dispatch.RETURN_LANE
            and hold.reason_of(labels, bodies) == proof_dispatch.REVIEW_CAP):
        lines.append(
            f"The card carries {hold.HOLD_LABEL}, which every dispatcher stands "
            "down for — a person answering the hold and taking the label off "
            "moves it.")
    if pr.get("isDraft") and not proof_record.open_holds(
            spoken_thread.voices(window, _viewer(), card=card.get("identifier") or "")):
        lines.append(
            "The pull request is a draft with no hold on the card: the critic "
            "does not review a draft and the merge gate never merges one — "
            "marking it ready for review moves it, or a 🔬 proof-waiting hold "
            "says what it waits for.")
    path, why = proof_record.find_record(pr.get("files"))
    if path is None:
        dirs = " or ".join(proof_record.RECORD_DIRS)
        lines.append(
            f"The gate can read no record: {why}. A proof record is one new .md "
            f"file under {dirs} — adding it on this branch moves it.")
    return lines


def _told_today(card: dict, number, now: datetime) -> bool:
    since = now - timedelta(hours=DAY_HOURS)
    for node in linear_ops.window_nodes(card.get("comments")):
        at = _when(node.get("createdAt"))
        if (node.get("body") or "").lstrip().startswith(_marker(number)) and at and at > since:
            return True
    return False


def _named_before(card: dict, number) -> bool:
    return any((n.get("body") or "").lstrip().startswith(_marker(number))
               for n in linear_ops.window_nodes(card.get("comments")))


def note(pr: dict, lines: list, hours: int) -> str:
    said = "\n".join(f"- {line}" for line in lines)
    alert = (f" It has been open past {ALERT_HOURS} hours, so the operator has a "
             "console alert for it too." if hours >= ALERT_HOURS else "")
    return (
        f"{_marker(pr['number'])} the proof record's pull request #{pr['number']} "
        f"is open and nothing in the pipeline will move it.\n\n{said}\n\n"
        f"Open {hours} hours.{alert} Said at most once a day while it stands."
        + (f"\n\n{pr['url']}" if pr.get("url") else ""))


def _alert(ident: str, pr: dict, lines: list, hours: int) -> str | None:
    """Raise the console alert; the failure line when the door lost it."""
    body = "\n".join([
        f"Proof record #{pr['number']} for {ident} has been open {hours} hours "
        f"in {reconcile.REPO}, past {ALERT_HOURS}, and nothing in the pipeline "
        "will move it:", "", *[f"- {line}" for line in lines]])
    outcome = pipeline_notice.raise_notice(
        notice_key(ident), severity="WARN",
        title=f"Proof record #{pr['number']} ({ident}) is open with nothing to move it",
        body=body, action_url=pr.get("url") or None,
        action_label="Open the pull request" if pr.get("url") else None)
    if outcome == "lost":
        return (f"{PREFIX} the alert for {ident}'s proof record #{pr['number']} "
                "was not raised — the pipeline-notice line says why")
    return None


def _one(pr: dict, now: datetime) -> str | None:
    number = pr.get("number")
    ident = reconcile.branch_card(pr.get("headRefName") or "")
    try:
        card = (linear_ops.gql(CARD_QUERY, {"id": ident}) or {}).get("issue")
        if not card:
            raise LookupError(f"Linear answered no card for {ident}")
    except Exception as error:  # noqa: BLE001 — unread names nothing
        print(f"{PREFIX} #{number}: {ident} could not be read ({error}) — "
              "nothing named this sweep")
        return None
    opened = _when(pr.get("createdAt"))
    hours = int((now - opened).total_seconds() // 3600) if opened else 0
    lines = shapes(card, pr)
    if not lines:
        print(f"{PREFIX} #{number}: {ident} in {(card.get('state') or {}).get('name')} "
              "— the pipeline moves it")
        if hours >= ALERT_HOURS and _named_before(card, number):
            pipeline_notice.resolve_notice(notice_key(ident))
        return None
    failure = None
    if _told_today(card, number, now):
        print(f"{PREFIX} #{number}: {ident} — {len(lines)} shape(s), named on the "
              "card within the day")
    else:
        try:
            linear_ops.cmd_comment(ident, note(pr, lines, hours))
            print(f"{PREFIX} #{number}: {ident} — named {len(lines)} shape(s) on the card")
        except Exception as error:  # noqa: BLE001 — recorded like every failed write
            failure = f"{PREFIX} the note on {ident} for #{number} did not land: {error}"
            print(f"ERROR: {failure}", file=sys.stderr)
    if hours >= ALERT_HOURS:
        failure = _alert(ident, pr, lines, hours) or failure
    return failure


def sweep(now: datetime | None = None) -> list:
    """Name every open proof record of this repo nothing can move; returns
    the failures the sweep records."""
    now = now or datetime.now(timezone.utc)
    out = reconcile.gh("pr", "list", "--repo", reconcile.REPO, "--state", "open",
                       "--limit", str(LIST_LIMIT), "--json", LIST_FIELDS)
    try:
        prs = json.loads(out or "[]")
    except ValueError:
        print(f"{PREFIX} the open pull requests could not be read — nothing named")
        return []
    records = [pr for pr in prs if isinstance(pr, dict)
               and proof_record.proof_record_branch(pr.get("headRefName"))]
    if not records:
        print(f"{PREFIX} no open proof record")
    failures = []
    for pr in records:
        failure = _one(pr, now)
        if failure:
            failures.append(failure)
    return failures
