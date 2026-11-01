#!/usr/bin/env python3
"""Once a day, each proof open more than seven days says what it waits on (DRE-8626).

`scripts/proof_overdue.py` (DRE-8625) reads what an overdue `PROOF:` card
waits on, in one word from a fixed list, and nothing called it. This is the
pass that does. It SAYS; it takes no action — the action step
(`scripts/proof_overdue_act.py`) reads the comment this writes. It never
moves a lane, writes a label, posts a hold or touches a pull request.

## When it runs

The reconcile workflow's `Proof overdue` step, on every full pass, under the
proof dispatcher's gates — only where the repo carries a proof-run stub — and
its switch: with `PROOF_DISPATCH_LIVE` anything but exactly `true` the pass is
dry, prints a `would:` line for every write and writes nothing.

## The comment — at most once per PT day per card

For each of the repo's open `PROOF:` cards it calls `proof_overdue.reading`,
and on each overdue one it posts one comment whose first line is

    ⏳ proof-overdue: <YYYY-MM-DD> · class=<cls> · action=<action> · waits-on=<waits_on> · age=<age_days>d

followed by plain English for whoever opens the card. `<YYYY-MM-DD>` is the
day in Pacific time, daylight saving included. The once-a-day key is a
comment on the card that `linear_ops.comment_records` marks
`authored_by_pipeline` and whose first line opens `MARK` followed by today's
PT day: with one, the card is told already and nothing is posted. The same
line from anyone else counts for nothing.

It never comments on a card in `Green Light`, the CEO's queue: such a card is
counted in the note and named there as in Green Light, and nothing is posted
on it.

## The note — printed on every pass, raised once per PT day per repo

    proof-overdue: note · <slug> · <N> proof record(s) older than 7 days · oldest <DRE-n> (<age> days, class=<cls>)

or `· oldest none` when N is 0, then ` · in Green Light: <DRE-n>, …` when an
overdue card is there, then ` · not read (rate limit): <DRE-n>, …` when a
card's read was skipped (below) — N counts only the cards read. The oldest
is the card with the largest `age_days`, a tie going to the lower card number. The note is printed on every pass, live
or dry. When N is more than 0 a live pass raises it as one WARN console alert
keyed `proof_overdue:<slug>:<YYYY-MM-DD>` — the day is in the key, the console
holds one alert per key, and a raise of a key already open is the door's
`repeat`, which delivers nothing, so the alert is delivered once per PT day.
Every live pass then resolves the previous day's key, and on a day N is 0 it
raises nothing and resolves that key alone — but only a pass that read every
card: one that skipped a card leaves the previous day's key open, since the
card it was raised for may still be overdue (`standards/console-honesty.md`).

## Failures

A card `proof_overdue.reading` cannot read, or whose thread cannot be read, is
named on its own line and gets nothing posted; a comment or an alert that
does not land is named too. Each turns the step red, at the end of the pass,
after every other card has been handled. A list of cards that cannot be read
writes nothing at all.

One read failure is not red (DRE-8696): a card whose read GitHub refused for
its rate limit — `gh_read_retry.is_rate_limit_refusal` on the error's whole
text — is named as skipped on its own line and read on the next pass, the way
the main sweep records a throttled read as degraded. No retry: an empty hourly
bucket does not refill in the minute a retry waits, and the next pass is the
retry. The note names it as not read, and the previous day's alert stays open.

CLI (the step's own call; reads `REPO` and `PROOF_DISPATCH_LIVE`):

    python3 scripts/proof_overdue_sweep.py --repo <slug>
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_read_retry  # noqa: E402 — the one classifier of a rate-limit refusal
import linear_ops  # noqa: E402 — the comment, and who wrote each one
import pipeline_notice  # noqa: E402 — the console alert
import proof_overdue  # noqa: E402 — the one reader of what an overdue proof waits on

#: Opens every line this module prints. `config/proof-reach.json` reads it
#: here as a literal; it is `proof_overdue.TAG`, held equal below.
TAG = "proof-overdue:"
if TAG != proof_overdue.TAG:
    raise ImportError(f"TAG {TAG!r} is not proof_overdue.TAG {proof_overdue.TAG!r}")
#: Opens the comment's first line; then the PT day, the once-a-day key.
MARK = f"⏳ {TAG}"
#: The switch this pass is live under, the proof dispatcher's own.
LIVE_VARIABLE = "PROOF_DISPATCH_LIVE"
#: The CEO's queue: a card here is counted and named, never commented on.
GREEN_LIGHT = "Green Light"
PT = ZoneInfo("America/Los_Angeles")

#: What each class waits on, and what the pipeline does next, in plain words.
WAITS = {
    "release": ("It waits on {w}: something it needs has not shipped yet.",
                "Next, the pipeline runs the proof again once that ships."),
    "date": ("It waits on a time: it cannot be observed before {w} (UTC).",
             "Next, the pipeline runs the proof again after that time."),
    "press": ("It waits on a press on the sandbox: {w}.",
              "Next, the pipeline makes that press on the sandbox and reads what it shows."),
    "deploy": ("It waits on a deploy: {w}.",
               "Next, the pipeline runs the proof again once that deploy is out."),
    "unrepeatable": ("It waits on something that will not happen again by itself: {w}.",
                     "Next, the pipeline splits that check out of the proof, so the rest "
                     "of it can finish."),
}


def is_live(env=None) -> bool:
    return (os.environ if env is None else env).get(LIVE_VARIABLE) == "true"


def pt_day(now: datetime) -> str:
    """The Pacific calendar day of `now`, as `YYYY-MM-DD`."""
    return now.astimezone(PT).date().isoformat()


def previous_day(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def note_key(slug: str, day: str) -> str:
    return f"proof_overdue:{slug}:{day}"


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def first_line(d: proof_overdue.Diagnosis, day: str) -> str:
    return (f"{MARK} {day} · class={d.cls} · action={d.action} · "
            f"waits-on={_flat(d.waits_on)} · age={d.age_days:.1f}d")


def comment(d: proof_overdue.Diagnosis, day: str) -> str:
    """The whole comment: the contract's first line, then plain English."""
    waits, then = WAITS[d.cls]
    said = [f"This proof has been open {d.age_days:.1f} days, more than the "
            f"{proof_overdue.AGE_DAYS} after which a proof is overdue.",
            waits.format(w=_flat(d.waits_on)).replace("`", ""), then,
            "No person needs to act on this card."]
    if d.by_elimination:
        said.append("This was read by elimination: nothing on the card names a release, "
                    "a time, a sandbox press or a deploy, so it is read as something "
                    "that will not happen again by itself.")
    evidence = _flat(d.evidence)
    quoted = (f"What it was read from:\n\n> {evidence}" if evidence
              else "Nothing on the card names what it waits on.")
    return f"{first_line(d, day)}\n\n{' '.join(said)}\n\n{quoted}"


def told_today(records, day: str) -> bool:
    """Whether the pipeline already said it on the card today (PT)."""
    opening = f"{MARK} {day} "
    return any(r.get("authored_by_pipeline")
               and ((r.get("body") or "").strip().split("\n", 1)[0] + " ").startswith(opening)
               for r in records or ())


def _number(card: str) -> int:
    try:
        return int(card.rsplit("-", 1)[-1])
    except ValueError:
        return sys.maxsize


def oldest(diagnoses):
    """The card with the largest `age_days`, a tie going to the lower number."""
    return max(diagnoses, key=lambda d: (d.age_days, -_number(d.card)), default=None)


def note_line(slug: str, overdue: list, green: list, skipped=()) -> str:
    head = (f"{TAG} note · {slug} · {len(overdue)} proof record(s) older than "
            f"{proof_overdue.AGE_DAYS} days · ")
    top = oldest(overdue)
    head += (f"oldest {top.card} ({top.age_days:.1f} days, class={top.cls})" if top
             else "oldest none")
    if green:
        head += f" · in {GREEN_LIGHT}: {', '.join(green)}"
    if skipped:
        head += f" · not read (rate limit): {', '.join(skipped)}"
    return head


def _why(error) -> str:
    return _flat(error)[:300] or type(error).__name__


class _Lanes:
    """`proof_overdue.LinearReads`, keeping the lane of each card off the one
    card read `reading` makes, so the lane costs no read of its own."""

    def __init__(self, inner):
        self.inner, self.lanes = inner, {}

    def proofs(self, slug):
        return self.inner.proofs(slug)

    def card(self, identifier):
        issue = self.inner.card(identifier)
        self.lanes[identifier] = ((issue or {}).get("state") or {}).get("name")
        return issue

    def thread(self, identifier):
        return self.inner.thread(identifier)

    def states(self, identifiers):
        return self.inner.states(identifiers)


def _tell(d, day: str, live: bool) -> str | None:
    """Post the day's comment on one overdue card; the failure, or None."""
    try:
        records = linear_ops.comment_records(d.card)
    except Exception as error:  # noqa: BLE001 — unread posts nothing, and is red
        print(f"{TAG} {d.card} · its comments could not be read — {_why(error)} — "
              "nothing posted")
        return f"{d.card}'s comments could not be read"
    if told_today(records, day):
        print(f"{TAG} {d.card} · told on {day} already")
        return None
    line = first_line(d, day)
    if not live:
        print(f"would: comment {d.card} — {line}")
        return None
    try:
        capped = linear_ops.cmd_comment(d.card, comment(d, day))
    except Exception as error:  # noqa: BLE001 — recorded like every failed write
        capped = _why(error)
    if capped:
        print(f"{TAG} {d.card} · the comment did not land — {capped}")
        return f"the comment on {d.card} did not land"
    print(f"{TAG} {d.card} · told — {line}")
    return None


def _alert(slug: str, day: str, note: str, count: int, live: bool,
           skipped=()) -> str | None:
    """Raise the day's alert when anything is overdue, then resolve the
    previous day's unless a card was not read; the failure, or None."""
    key, previous = note_key(slug, day), note_key(slug, previous_day(day))
    kept = (f"{TAG} {previous} left open — {len(skipped)} card(s) not read"
            if skipped else None)
    if not live:
        if count:
            print(f"would: raise {key} — {note}")
        print(kept or f"would: resolve {previous}")
        return None
    failure = None
    if count:
        outcome = pipeline_notice.raise_notice(
            key, severity="WARN",
            title=f"{count} proof record(s) in {slug} open more than "
                  f"{proof_overdue.AGE_DAYS} days",
            body=note)
        if outcome == "lost":
            print(f"{TAG} the alert {key} was not raised — the pipeline-notice line says why")
            failure = f"the alert {key} was not raised"
    if kept:
        print(kept)
    else:
        pipeline_notice.resolve_notice(previous)
    return failure


def sweep(slug: str, *, now: datetime | None = None, live: bool | None = None,
          linear=None, gh=None) -> int:
    """One pass over the repo's open PROOF cards. 0, or 1 when anything
    could not be read or did not land."""
    now = now or datetime.now(timezone.utc)
    live = is_live() if live is None else live
    day = pt_day(now)
    print(f"{TAG} {LIVE_VARIABLE} is {'true — live' if live else 'not true — dry run'}; "
          f"PT day {day}")
    lanes = _Lanes(linear or proof_overdue.LinearReads())
    try:
        cards = lanes.proofs(slug)
    except Exception as error:  # noqa: BLE001 — nothing known, nothing written
        print(f"{TAG} the open PROOF cards of {slug} could not be read — {_why(error)}")
        return 1
    failures, overdue, skipped = [], [], []
    for card in cards:
        try:
            got = proof_overdue.reading(card, now=now, gh=gh, linear=lanes)
        except Exception as error:  # noqa: BLE001 — named on its own line
            # The whole text, not `_why`'s first 300 characters (DRE-8696).
            if gh_read_retry.is_rate_limit_refusal(str(error)):
                print(f"{TAG} {card} · skipped — GitHub refused the read for its rate "
                      "limit; it is read on the next pass")
                skipped.append(card)
                continue
            print(f"{TAG} {card} · could not be read — {_why(error)}")
            failures.append(f"{card} could not be read")
            continue
        if got is not None:
            overdue.append(got)
    overdue.sort(key=lambda d: _number(d.card))
    green = []
    for d in overdue:
        if lanes.lanes.get(d.card) == GREEN_LIGHT:
            print(f"{TAG} {d.card} · in {GREEN_LIGHT} — counted in the note, nothing posted")
            green.append(d.card)
            continue
        failure = _tell(d, day, live)
        if failure:
            failures.append(failure)
    note = note_line(slug, overdue, green, skipped)
    print(note)
    failure = _alert(slug, day, note, len(overdue), live, skipped)
    if failure:
        failures.append(failure)
    for failure in failures:
        print(f"ERROR: {TAG} {failure}", file=sys.stderr)
    return 1 if failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="a slug of config/repo-map.json")
    args = parser.parse_args(argv)
    try:
        # `proof_overdue` reaches `reconcile`, which reads `REPO` on import.
        os.environ.setdefault("REPO", proof_overdue._full(args.repo))
    except LookupError as error:
        print(f"{TAG} {error}")
        return 1
    return sweep(args.repo)


if __name__ == "__main__":
    sys.exit(main())
