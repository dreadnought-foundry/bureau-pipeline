#!/usr/bin/env python3
"""The operator's control on Intake — one switch, one reader (DRE-3035/DRE-4141).

Intake is a pen, and this module is the gate hardware. ONE reading of it now,
and one thing behind the gate:

  * `INTAKE_HOLD` — the switch. Set, `groomer.py drain` refuses to move the
    approved batch and says so in one line per pass; cleared, the drain
    resumes.

IT USED TO BE FOUR READINGS, and the other three all belonged to the Intake
age-out: the window (how long a card could sit before the sweep escalated it),
the per-sweep cap (how many it could move at once) and `may_escalate` (whether
the sweep's repo was on the routing rail at all, after sandbox sweeps helped
age 130+ cards into the CEO's queue on 2026-09-09/10). DRE-4141 removed the
age-out on the CEO's signed answer of 2026-09-17: no card leaves Intake because
it is old, so there is no move to window, to cap or to fence.
`reconcile.report_intake_depth` counts the lane and prints one line; this
module has nothing to tell it. The two retired variables are named, and their
retirement recorded, in `docs/backlog-cutover.md` — the runbook an operator who
set one would be reading.

THE DRAIN IS THE EXIT. The groomer proposes a batch, the CEO approves it in
Green Light, and `groomer.drain` moves it — the one way out of Intake, and the
one thing this switch holds.

The value arrives as a `workflow_call` input, which is why nothing here uses a
bare `int()` or treats the EMPTY STRING as a setting. On any event where the
`inputs` context is empty the interpolation yields "", and a hold that read
that as "closed" would stop the fleet's intake on every schedule event.

WHY THE HOLD PRINTS. A hold that moved nothing and said nothing would be
indistinguishable from the stall it exists to prevent — the failure DRE-2670
recorded from the other side, where about 480 consecutive green sweeps printed
the exact reason five cards were frozen and nobody read one. Here the reverse
risk applies: a silent pen is a stall with an alibi. So the pen is VISIBLY
closed, once per pass, naming the date it was closed, how much is behind it and
which switch opens it.
"""

from __future__ import annotations

import os

#: The environment variable, named once. The workflow threads it from a
#: `workflow_call` input of the same name in lower case, and
#: tests/test_intake_pen.py fails the build if any workflow assigns it a
#: literal instead — a per-repo value baked into the shared channel is how one
#: repo's hold becomes everyone's.
ENV_HOLD = "INTAKE_HOLD"

#: Opens every hold line, so a held pass is greppable in a run log and the
#: tests can count the lines rather than match the prose.
TAG = "intake-hold"

#: Spellings of "the pen is open". EMPTY IS THE LOAD-BEARING ONE: an unset
#: workflow input is the empty string, and a hold that read that as "closed"
#: would stop the fleet's intake on every schedule event.
_OFF = ("", "false", "0", "no", "off")

#: Spellings of "closed, and I did not give a date". The operator is asked for
#: the date the pen was closed — a bare `true` still holds, because a switch
#: that refused an unexpected value would be a hold that silently is not one.
_ON_WITHOUT_DATE = ("true", "1", "yes", "on")


def hold(raw=None) -> str | None:
    """`None` when the pen is open; otherwise WHEN the operator closed it.

    The value is carried verbatim into the notice, so an operator who sets
    `INTAKE_HOLD: "2026-09-03"` gets a line saying since when. A bare switch
    holds just as hard and returns `""` — closed, date unstated, and the notice
    renders that absence as absence rather than inventing one.
    """
    text = str(os.environ.get(ENV_HOLD, "") if raw is None else (raw or "")).strip()
    if text.lower() in _OFF:
        return None
    return "" if text.lower() in _ON_WITHOUT_DATE else text


def notice(since: str, waiting: int, detail: str) -> str:
    """The one line a held reader prints per pass.

    `detail` is the caller's own measure of what is behind the pen — the drain
    counts the batch it was about to move — because the reader knows a true
    number this module does not, and a shared sentence with it guessed would be
    worse than an accurate one. One caller since DRE-4141, and the parameter
    stays a parameter: the sentence is still not this module's to write.
    """
    when = f"since {since}" if since else "(no date set on the switch)"
    return (
        f"{TAG}: Intake held by the operator {when}; "
        f"{_plural(waiting, 'card')} waiting, {detail} — nothing moves out of "
        f"Intake until {ENV_HOLD} is cleared"
    )


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")
