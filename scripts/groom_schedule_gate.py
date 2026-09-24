#!/usr/bin/env python3
"""The scheduled groomer's gate — is it 06:15 PT, and is there a card to post
to (DRE-4688)?

`self-groomer.yml`'s schedule job calls `groomer.yml`, and **a job with
`uses:` has no steps**. So the two questions a scheduled groom must answer
before it spends a model call cannot be asked inside that job at all; they are
asked by a separate gate job, which runs this one script and hands its answer
down as `go` and `why`. Nothing here decides how to groom — that is the
groomer's own business. This decides only whether this particular run is the
one that does the work.

## Question one: is it the morning?

GitHub's `schedule:` takes UTC only and has no timezone field, so hitting one
LOCAL hour all year needs two cron lines — `15 13 * * *` and `15 14 * * *`.
Every day both fire, exactly one of them is 06:15 on the
`America/Los_Angeles` wall clock, and WHICH one flips at each DST change.
The reading is therefore done with `zoneinfo` on the PT clock and never from
a restated offset, the way `release_train.in_window` reads a release window
and `release_train.wake_crons` derives the fleet's own pair of cron lines. An
offset written down here would be right for half the year and silently an
hour wrong for the other half — which is exactly how Portico's train came to
sleep until 07:03 PT on 2026-09-21 (DRE-4450).

The window is the whole `06:00`–`06:59` PT hour rather than the minute,
because a cron fires when GitHub gets to it: a scheduled run routinely starts
several minutes late, and a gate keyed on `06:15` exactly would skip the
morning for being punctual.

## Question two: is there a card to post to?

The CEO's signed answer on 2026-09-21 (on DRE-3586, absorbed here) was ONE
standing card for every morning's proposal, named by this repo's
`GROOM_PROPOSAL_CARD` variable. A proposal posted to a closed card is a
proposal nobody reads, so the gate refuses when the variable is empty, when
the card it names is in a terminal state (`completed`, `canceled`), or when
the card cannot be read at all.

**The refusal is LOUD — exit 1, a red run the medic sees — and it is loud
ONLY INSIDE THE 06:xx HOUR.** The off-hour cron of the pair fires every
single day and has no opinion about the card; if it went red for an empty
variable it would be red every morning, and a check that is red every morning
is a check nobody reads. Outside the window the gate is a quiet no-op, exit 0,
whatever `--card` says.

## The contract (shared with the sibling workflow card)

    python3 .bureau-pipeline/scripts/groom_schedule_gate.py --card "$CARD"

with `LINEAR_API_KEY` in the environment. It appends exactly two lines to
`--github-output` (defaulting to `$GITHUB_OUTPUT`):

    go=true|false
    why=<one plain sentence>

and prints the sentence to stdout. The schedule job keys on `go == 'true'`
and nothing else. The sentence names the PT time the gate read, and on a
refusal the card and the state it found, so the run log answers the question
without anyone opening Linear.

No model call, no write to Linear, no write to the repo: one card read, a
clock, and two lines of output.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import github_output  # noqa: E402
import linear_ops  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")

#: The hour, on the PT wall clock, a scheduled groom belongs to. The whole
#: hour: see "Question one" above.
MORNING_HOUR = 6

#: Linear's lifecycle buckets that mean "this card is finished" — the same
#: pair `linear_ops._TERMINAL_TYPES` and `break_glass` read, stated on the
#: state TYPE and never on its name, because a board can rename a lane.
TERMINAL_TYPES = ("completed", "canceled")

#: The repository variable that names the standing proposal card. Named in
#: every refusal, because "the gate found no card" is not actionable and
#: "`GROOM_PROPOSAL_CARD` is empty" is.
CARD_VARIABLE = "GROOM_PROPOSAL_CARD"


def in_morning_window(now: datetime) -> bool:
    """Does `now` read between 06:00 and 06:59 inclusive on the PT clock?

    A naive `now` is read as UTC: `--now` is documented UTC and a runner's own
    clock is UTC, so the alternative — `astimezone` silently applying whatever
    local zone the machine has — would make the answer depend on where the
    script ran.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(PT).hour == MORNING_HOUR


def pt_clock(now: datetime) -> str:
    """`now` as the sentence says it: `06:15 PT`."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return f"{now.astimezone(PT):%H:%M} PT"


def card_is_open(lops, identifier: str) -> tuple[bool, str]:
    """Is the standing proposal card somewhere a proposal can still be posted?

    Returns `(True, "")` when it is, and `(False, <a sentence naming what was
    found>)` when the identifier is empty, the card is in a terminal state, or
    Linear could not be read. The refusal sentence is the caller's `why`, so
    it names the card and the state rather than merely reporting a false.

    ONE read, and none at all for an empty identifier — there is nothing to
    ask about.
    """
    identifier = (identifier or "").strip()
    if not identifier:
        return False, (
            f"{CARD_VARIABLE} names no card, so there is nowhere to post this "
            "morning's proposal"
        )
    try:
        issue = lops.get_issue(identifier)
    except Exception as exc:                        # noqa: BLE001 - reported, not raised
        return False, (
            f"{identifier} ({CARD_VARIABLE}) could not be read from Linear: "
            f"{exc}"
        )
    state = (issue or {}).get("state") or {}
    name = state.get("name") or "an unnamed state"
    kind = state.get("type") or ""
    if kind in TERMINAL_TYPES:
        return False, (
            f"{identifier} is {name} ({kind}), and a proposal posted to a "
            "closed card is a proposal nobody reads"
        )
    return True, ""


def decide(lops, card: str, now: datetime) -> tuple[bool, str, int]:
    """`(go, why, exit code)` — the whole gate, over a clock and one card.

    The window is asked FIRST and the card is not read at all outside it: the
    off-hour cron of the pair has no opinion about the card, and a Linear read
    it does not need is a read that can fail for a reason that is not its own.
    """
    clock = pt_clock(now)
    if not in_morning_window(now):
        return False, (
            f"it is {clock}, outside the 06:00-06:59 PT grooming hour — this "
            "is the other cron of the pair and it does nothing"
        ), 0
    ok, refusal = card_is_open(lops, card)
    if not ok:
        return False, f"it is {clock} and {refusal}", 1
    return True, (
        f"it is {clock} and {card.strip()} is open — the groomer runs"
    ), 0


def _one_line(text: str) -> str:
    """The sentence as a single `key=value` line can carry it. A state name
    is card text (`standards/untrusted-content.md`) and `$GITHUB_OUTPUT` is
    read a LINE at a time, so a newline in it would become a second key rather
    than a long value (DRE-4202)."""
    return " ".join(text.split())


def _write(path: str | None, go: bool, why: str) -> None:
    """Append the two lines. No path is an ordinary shape — the gate run from
    a terminal still decides and still prints — so it is not an error."""
    if not path:
        return
    block = github_output.render([("go", "true" if go else "false"), ("why", why)])
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(block)
    except OSError as exc:
        print(f"groom_schedule_gate: could not write step outputs: {exc}",
              file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--card", default="",
                        help=f"the standing proposal card ({CARD_VARIABLE}); "
                             "empty inside the window is a refusal")
    parser.add_argument("--now", default=None,
                        help="an ISO-8601 UTC timestamp, for replaying a real "
                             "run (default: the wall clock)")
    parser.add_argument("--github-output", default=None,
                        help="the step-output file (default: $GITHUB_OUTPUT)")
    args = parser.parse_args(argv)

    now = (
        datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if args.now else datetime.now(timezone.utc)
    )
    go, why, code = decide(linear_ops, args.card, now)
    why = _one_line(why)
    _write(args.github_output or os.environ.get("GITHUB_OUTPUT"), go, why)
    print(why)
    return code


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
