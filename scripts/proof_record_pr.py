#!/usr/bin/env python3
"""One reader decides whether a proof record may have a pull request (DRE-8437).

On 2026-10-29 at 9:01 PM PT, 12 of the fleet's 19 open pull requests were
proof records waiting on a date, a fix, an operator's step or a re-run. The
CEO decided that a proof's record pull request opens only when its record can
merge. This module is that decision, in one place.

## `can_merge`

A record can merge when its criterion table (`proof_record.reading`) has at
least one judged row and each unmet row is a gap row (`proof_record.gap_rows`,
DRE-7952) or a wording-only candidate (`proof_record.row_wording`). **A
wording-only row counts as able to merge, on purpose.** Only the record's
critic accepts one, in its verdict on the pull request (DRE-7949), so the
pull request has to open for the row to be accepted at all. The merge gate
still holds that pull request until the critic's `🔎 proof-row-accepted:`
line stands, exactly as it holds every record. Every other unmet row is
`blocking`. A record with no text, no table or no judged row cannot be judged:
`ok` is False with nothing blocking, and both commands read it as a record
not read — they write nothing for it.

## `decide`

Reads the record on `agent/<card>-proof-record` at its head
(`proof_record.record_blob`, then `proof_record.record_text` at that commit)
and prints one line, `proof-record-pr: <word> <card> — <why>`:

  * `opened` — the record can merge and no pull request is open or closed
    unmerged: `gh pr create --head <branch>`, titled `PROOF record: <title>`.
  * `reopened` — the record can merge and the branch's newest pull request is
    closed and unmerged: `gh pr reopen`. A refused reopen opens a new one, and
    the word is `opened`.
  * `kept-open` — the record can merge and its pull request is open.
  * `closed-and-held` — the record cannot merge and its pull request was open:
    the hold, then `gh pr close <n> --comment CLOSE_COMMENT`, never
    `--delete-branch`. A hold refused leaves the pull request open, so the
    close comment never points at a card that says nothing.
  * `held` — the record cannot merge and no pull request is open: the hold.
  * `no-record` — no branch, or no record that can be read or judged. Nothing
    is written.

With no open pull request `record_blob` finds the record through the card's
`**Files:**` line, so the CLI reads the description from `CARD_DESCRIPTION`
as `proof_record.py blob` does.

## The hold

The card's one `🔬 proof-waiting` (`hold_line`, posted as
`linear_ops.proof_waiting_line(observed, needs)`). When every blocking row
waits on an event (`proof_record.row_waiting`) it is exactly the hold the
proof dispatcher writes for those rows (`proof_dispatch._hold_waiting`):
time-only, lifted by the dispatcher itself. Otherwise its observed half opens
`RECORD_HOLD_OPENING` and its needs half names what each row waits on — the
event, a fix card shipped, or a re-run. `is_record_hold` reads that second
kind; it tells the card's reader what each row waits on, and never decides a
re-run: the record at the branch head does (DRE-8451). A hold whose first line
is already the card's newest open hold is not posted again — nor one that
differs only by the later recheck time an unstamped row is read as, while the
standing hold has not lapsed. A thread that cannot be read posts no hold and
is a refused read: a card whose thread is unknown is never read as one with no
hold. `--no-hold`
posts none — the proof run passes it when it escalated a CEO press, whose park
posts the card's one hold.

## `sweep`

Lists the repository's open pull requests on record branches
(`proof_record.proof_record_branch`), reads each record at its head with
`proof_record.fetch`, and closes each that `can_merge` refuses, with
`CLOSE_COMMENT` and the hold on its card, keeping the branch — the hold first,
so a refused hold leaves its pull request open for the next sweep. One line:
`proof-record-sweep: read <N> open record pull request(s); closed <K>: #a;
kept <M>; unread <U>`. A record not read is counted unread and never closed.
Not live — `PROOF_DISPATCH_LIVE` is not exactly `true` — it prints
`would: close #<n>` and writes nothing. A list of `LIST_LIMIT` open pull
requests is full, and the sweep says so.

A LEAF, like `proof_record`: nothing here imports `proof_dispatch` or
`reconcile`, which reads `os.environ["REPO"]` when it is imported. Nothing
calls it yet: DRE-8441 wires `decide` into the proof run, DRE-8452 `sweep`
into the reconcile workflow.

CLI:

    python3 scripts/proof_record_pr.py decide --repo R --card DRE-N --card-url U --title T [--no-hold]
    python3 scripts/proof_record_pr.py sweep --repo R

`decide` writes `decision=<word>` to `$GITHUB_OUTPUT` when it is set. Either
exits 1 when a read or write it needed was refused.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import linear_ops  # noqa: E402 — the hold line, its writer and the thread read
import proof_cause_park  # noqa: E402 — how a press-named criterion is reworded
import proof_record  # noqa: E402 — the record at the head, read as the gate reads it
import spoken_thread  # noqa: E402 — the Pacific time a hold names

#: Opens every line `decide` prints.
TAG = "proof-record-pr:"
#: Opens the one line `sweep` prints.
SWEEP_TAG = "proof-record-sweep:"

#: How long after the pass that posts it a waiting hold over a row nobody
#: stamped — a prose event, or the operator's press — lifts itself (DRE-7951).
WAITING_RECHECK_HOURS = 12
#: A waiting row's event that names the operator's press (DRE-6837).
OPERATOR_PRESS = "the operator's press"

#: Opens the observed half of a hold over a record whose blocking rows do not
#: all wait on an event.
RECORD_HOLD_OPENING = "not ready to merge:"

#: What closes an open record pull request whose record cannot merge.
CLOSE_COMMENT = ("Closed while the proof waits — the record on {branch} cannot merge "
                 "yet: {n} row(s) wait. The card says what each waits on: {card_url}. "
                 "The branch is kept, and this pull request reopens when the record "
                 "can merge.")

#: A card's page, for the close comment `sweep` writes.
CARD_URL = "https://linear.app/dreadnoughtfoundry/issue/{identifier}"
#: The switch `sweep` is live under, the proof dispatcher's own.
LIVE_VARIABLE = "PROOF_DISPATCH_LIVE"
#: The most open pull requests `sweep` lists; a full list is said so.
LIST_LIMIT = 200

OPENED, REOPENED, KEPT_OPEN = "opened", "reopened", "kept-open"
CLOSED_AND_HELD, HELD, NO_RECORD = "closed-and-held", "held", "no-record"

_CARD = re.compile(r"agent/(DRE-\d+)-proof-record")


class Verdict(NamedTuple):
    ok: bool
    blocking: list
    why: str


# --------------------------------------------------------------------------- #
# the waiting rows — the proof dispatcher's, moved here                       #
# --------------------------------------------------------------------------- #

def waiting_criterion(criterion: str, event: str) -> str:
    """The criterion as a waiting hold names it: one waiting on the
    operator's press never says `proof_record.CEO_PRESS`, which
    `proof_record.open_holds` would keep through a `🔬 proof-observed`."""
    if event.startswith(OPERATOR_PRESS):
        return criterion.replace(proof_record.CEO_PRESS, proof_cause_park.PRESS_NAMED)
    return criterion


def waiting_rows(unmet: list) -> list:
    """`(criterion, event, stamp)` for each unmet row waiting on an event, in
    the record's order; `stamp` is the row's `[not-before=<T>]` or None."""
    return [(criterion, event, proof_record.row_not_before(result))
            for criterion, result in unmet
            if (event := proof_record.row_waiting(result))]


def waiting_observed(waiting: list) -> str:
    """The observed half of the waiting rows' hold: their criteria."""
    return "; ".join(proof_record._cut(waiting_criterion(c, event), 70)
                     for c, event, _ in waiting)


def not_before(when: datetime) -> str:
    """`not-before=<T>`, keeping `when`'s own offset, and its seconds only
    when it has any."""
    exact = when.second or when.microsecond
    return (f"{linear_ops.NOT_BEFORE}"
            f"{when.isoformat(timespec='auto' if exact else 'minutes')}")


# --------------------------------------------------------------------------- #
# the judgment and the hold                                                    #
# --------------------------------------------------------------------------- #

def _gap(read, row: tuple) -> bool:
    return bool(proof_record.gap_rows(read._replace(unmet=[row])))


def can_merge(text: str | None) -> Verdict:
    """Could the merge gate merge this record, once its critic has read it?
    See the module docstring."""
    if text is None:
        return Verdict(False, [], "no record text was read")
    read = proof_record.reading(text)
    if read.rows is None:
        return Verdict(False, [], "the record has no criterion table")
    if not (read.met or read.accepted or read.unmet):
        return Verdict(False, [], "the record's criterion table has no row but its "
                                  "closing step")
    blocking = [(c, r) for c, r in read.unmet
                if not _gap(read, (c, r)) and not proof_record.row_wording(r)]
    if blocking:
        return Verdict(False, blocking, f"{len(blocking)} row(s) of the record wait")
    return Verdict(True, [], "every judged row is met, accepted, a gap or wording-only")


def is_record_hold(line: str) -> bool:
    """Is `line`, a comment's first line, the hold `hold_line` composes for a
    record whose blocking rows do not all wait on an event?"""
    return (line or "").startswith(f"{proof_record.HOLD_MARK} {RECORD_HOLD_OPENING} ")


def _pt(when: datetime) -> str:
    return spoken_thread.pacific_label(when.isoformat())


def _row_needs(result: str) -> str:
    event = proof_record.row_waiting(result)
    if event:
        stamp = proof_record.row_not_before(result)
        return f"{event} (not before {_pt(stamp)})" if stamp else event
    fixes = proof_record.row_fixes(result)
    if fixes:
        return f"{', '.join(fixes)} shipped"
    return "a re-run"


def hold_line(blocking: list, now: datetime) -> tuple:
    """`(observed, needs)` of the card's hold for these blocking rows. All
    waiting: the proof dispatcher's waiting hold, a time-only hold for the
    latest stamp — an unstamped row read as `WAITING_RECHECK_HOURS` from now —
    or, naming the CEO's press, the prose events. Otherwise
    `RECORD_HOLD_OPENING` and what each row waits on."""
    waiting = waiting_rows(blocking)
    if blocking and len(waiting) == len(blocking):
        observed = waiting_observed(waiting)
        recheck = (now + timedelta(hours=WAITING_RECHECK_HOURS)).replace(
            second=0, microsecond=0)
        needs = not_before(max(stamp or recheck for _, _, stamp in waiting))
        if proof_record.time_only(linear_ops.proof_waiting_line(observed, needs)) is None:
            needs = "; ".join(event for _, event, _ in waiting)
        return observed, needs
    named = []
    for criterion, result in blocking:
        event = proof_record.row_waiting(result)
        named.append(proof_record._cut(waiting_criterion(criterion, event) if event
                                       else criterion, 70))
    observed = f"{RECORD_HOLD_OPENING} " + "; ".join(named)
    return observed, "; ".join(_row_needs(result) for _, result in blocking)


def _observed_half(line: str) -> str:
    return line.rpartition(" — needs")[0]


def _already_held(newest: str, blocking: list, line: str, now: datetime) -> bool:
    """Is `newest`, the card's newest open hold, the hold `line` would post?
    A row nobody stamped is read as `WAITING_RECHECK_HOURS` from each pass,
    so the same hold names a later time on every pass: a time-only hold over
    the same rows, not yet lapsed and no earlier than any row's own stamp,
    is that hold."""
    if newest == line:
        return True
    waiting = waiting_rows(blocking)
    if len(waiting) != len(blocking) or all(stamp for _, _, stamp in waiting):
        return False
    until = proof_record.time_only(newest)
    if until is None or until <= now or _observed_half(newest) != _observed_half(line):
        return False
    return all(until >= stamp for _, _, stamp in waiting if stamp)


def _post_hold(card: str, blocking: list, now: datetime, comment: Callable,
               voices: Callable) -> bool:
    """Post the hold unless the card's newest open hold is already it.
    True when it was posted. RuntimeError when the card's thread could not
    be read: an unknown thread is never read as one with no hold."""
    observed, needs = hold_line(blocking, now)
    line = linear_ops.proof_waiting_line(observed, needs)
    thread = voices(card)
    if thread is None:
        raise RuntimeError(f"the thread of {card} could not be read, so no hold was posted")
    held = proof_record.open_holds(thread)
    if held and _already_held(held[-1], blocking, line, now):
        return False
    comment(card, observed, needs)
    return True


# --------------------------------------------------------------------------- #
# the pull request                                                             #
# --------------------------------------------------------------------------- #

def _pulls(repo: str, branch: str, gh: Callable) -> list:
    """The branch's pull requests, newest first. RuntimeError when unread."""
    pulls = json.loads(gh(["pr", "list", "--repo", repo, "--head", branch, "--state", "all",
                           "--json", "number,state,mergedAt"]) or "null")
    if not isinstance(pulls, list):
        raise RuntimeError(f"the pull requests of {branch} could not be read")
    return sorted(pulls, key=lambda p: -int(p["number"]))


def _body(card: str, card_url: str) -> str:
    return "\n".join([f"Proof record for {card}", "What's new: none", card_url, "",
                      "The proof run rebuilds this body from the record."]) + "\n"


def _open(repo: str, card: str, card_url: str, title: str, branch: str,
          gh: Callable) -> str:
    gh(["pr", "create", "--repo", repo, "--head", branch, "--title",
        f"PROOF record: {title}", "--body", _body(card, card_url)])
    return OPENED


def _close(repo: str, number, branch: str, n: int, card_url: str, gh: Callable) -> None:
    gh(["pr", "close", str(number), "--repo", repo, "--comment",
        CLOSE_COMMENT.format(branch=branch, n=n, card_url=card_url)])


def _say(word: str, card: str, why: str) -> str:
    print(f"{TAG} {word} {card} — {proof_record._one_line(why)}")
    return word


def decide(repo: str, card: str, card_url: str, title: str, *, gh: Callable | None = None,
           comment: Callable | None = None, now: datetime | None = None, hold: bool = True,
           voices: Callable | None = None, description: str = "") -> str:
    """Open, reopen or keep the record's pull request, or close it and hold
    the card — see the module docstring. Returns the decision word. A read or
    write GitHub refused raises RuntimeError."""
    gh = gh or proof_record._gh
    comment = comment or linear_ops.cmd_proof_waiting
    voices = voices or linear_ops.read_card_voices
    now = now or datetime.now(timezone.utc)
    branch = f"agent/{card}-proof-record"
    blob, why = proof_record.record_blob(repo, card, description, gh)
    if blob is None:
        return _say(NO_RECORD, card, why)
    try:
        text = proof_record.record_text(repo, blob.path, blob.commit, gh)
    except (RuntimeError, ValueError, TypeError, UnicodeDecodeError) as exc:
        return _say(NO_RECORD, card, f"{blob.path} could not be read at {blob.commit}: {exc}")
    verdict = can_merge(text)
    if not verdict.ok and not verdict.blocking:
        return _say(NO_RECORD, card, f"{blob.path} at {blob.commit[:7]}: {verdict.why}")
    at = f"{blob.path} at {blob.commit[:7]}"
    pulls = _pulls(repo, branch, gh)
    opened = next((p for p in pulls if p.get("state") == "OPEN"), None)
    if verdict.ok:
        if opened is not None:
            return _say(KEPT_OPEN, card, f"{at} can merge — #{opened['number']} stays open")
        newest = pulls[0] if pulls else None
        if newest is not None and newest.get("state") == "CLOSED" and not newest.get("mergedAt"):
            try:
                gh(["pr", "reopen", str(newest["number"]), "--repo", repo])
                return _say(REOPENED, card, f"{at} can merge — #{newest['number']} reopened")
            except RuntimeError as exc:
                word = _open(repo, card, card_url, title, branch, gh)
                return _say(word, card, f"{at} can merge — #{newest['number']} could not be "
                                        f"reopened ({exc}), so a new one was opened")
        word = _open(repo, card, card_url, title, branch, gh)
        return _say(word, card, f"{at} can merge — {verdict.why}")
    n = len(verdict.blocking)
    posted = hold and _post_hold(card, verdict.blocking, now, comment, voices)
    if opened is not None:
        _close(repo, opened["number"], branch, n, card_url, gh)
    said = "held" if posted else "already held" if hold else "no hold posted"
    if opened is not None:
        return _say(CLOSED_AND_HELD, card, f"{at}: {n} row(s) wait — "
                                           f"#{opened['number']} closed, {said}")
    return _say(HELD, card, f"{at}: {n} row(s) wait — no pull request open, {said}")


# --------------------------------------------------------------------------- #
# the sweep                                                                    #
# --------------------------------------------------------------------------- #

def is_live(env=None) -> bool:
    return (os.environ if env is None else env).get(LIVE_VARIABLE) == "true"


def sweep(repo: str, *, gh: Callable | None = None, comment: Callable | None = None,
          live: bool, voices: Callable | None = None, now: datetime | None = None) -> int:
    """Close every open record pull request whose record cannot merge, with
    the hold on its card — see the module docstring. Returns the exit code:
    1 when the list or a write was refused."""
    gh = gh or proof_record._gh
    comment = comment or linear_ops.cmd_proof_waiting
    voices = voices or linear_ops.read_card_voices
    now = now or datetime.now(timezone.utc)
    try:
        pulls = json.loads(gh(["pr", "list", "--repo", repo, "--state", "open", "--limit",
                               str(LIST_LIMIT), "--json", "number,headRefName,headRefOid"])
                           or "null")
        if not isinstance(pulls, list):
            raise ValueError("the answer was not a list")
    except (RuntimeError, ValueError, TypeError) as exc:
        print(f"{SWEEP_TAG} ERROR: the open pull requests of {repo} could not be read: "
              f"{proof_record._one_line(exc)}")
        return 1
    if len(pulls) >= LIST_LIMIT:
        print(f"{SWEEP_TAG} the list reached its limit of {LIST_LIMIT} open pull requests — "
              "any past it were not read")
    records = sorted((p for p in pulls if proof_record.proof_record_branch(p.get("headRefName"))),
                     key=lambda p: int(p["number"]))
    closed, kept, unread, failed = [], 0, 0, False
    for pr in records:
        number, branch = pr["number"], pr["headRefName"]
        card = _CARD.fullmatch(branch).group(1)
        try:
            view = json.loads(gh(["pr", "view", str(number), "--repo", repo, "--json", "files"])
                              or "null")
            files = view.get("files") if isinstance(view, dict) else None
        except (RuntimeError, ValueError) as exc:
            files = None
            print(f"{SWEEP_TAG} #{number}: its files could not be read: "
                  f"{proof_record._one_line(exc)}")
        found = proof_record.fetch(repo, files, pr.get("headRefOid") or "", gh)
        verdict = can_merge(found.text)
        if verdict.ok:
            kept += 1
            continue
        if not verdict.blocking:
            unread += 1
            continue
        if not live:
            print(f"would: close #{number}")
            closed.append(number)
            continue
        url = CARD_URL.format(identifier=card)
        try:
            _post_hold(card, verdict.blocking, now, comment, voices)
            _close(repo, number, branch, len(verdict.blocking), url, gh)
            closed.append(number)
        except Exception as exc:  # noqa: BLE001 — one refused write stops no other
            failed = True
            print(f"{SWEEP_TAG} ERROR: #{number} ({card}): {proof_record._one_line(exc)}")
    verb = "closed" if live else "would close"
    numbers = f": {', '.join(f'#{n}' for n in closed)}" if closed else ""
    print(f"{SWEEP_TAG} read {len(records)} open record pull request(s); "
          f"{verb} {len(closed)}{numbers}; kept {kept}; unread {unread}")
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# the CLI                                                                      #
# --------------------------------------------------------------------------- #

def _cmd_decide(args) -> int:
    try:
        word = decide(args.repo, args.card, args.card_url, args.title, hold=not args.no_hold,
                      description=os.environ.get("CARD_DESCRIPTION", ""))
    except (RuntimeError, ValueError, TypeError, linear_ops.LinearError) as exc:
        print(f"{TAG} ERROR {args.card} — {proof_record._one_line(exc)}")
        return 1
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"decision={word}\n")
    return 0


def _cmd_sweep(args) -> int:
    return sweep(args.repo, live=is_live())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    cmd = sub.add_parser("decide", help="open, reopen or keep the record's pull request, "
                                        "or close it and hold the card")
    cmd.add_argument("--repo", required=True)
    cmd.add_argument("--card", required=True)
    cmd.add_argument("--card-url", required=True)
    cmd.add_argument("--title", required=True)
    cmd.add_argument("--no-hold", action="store_true",
                     help="post no hold — the run's CEO-press park posts the card's one")
    cmd.set_defaults(fn=_cmd_decide)
    cmd = sub.add_parser("sweep", help="close every open record pull request whose record "
                                       f"cannot merge; live only when {LIVE_VARIABLE}=true")
    cmd.add_argument("--repo", required=True)
    cmd.set_defaults(fn=_cmd_sweep)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
