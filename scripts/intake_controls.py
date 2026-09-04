#!/usr/bin/env python3
"""The operator's controls on Intake — one reading of them, two readers (DRE-3035).

Intake is a pen, and this module is the gate hardware. Three knobs, and the
whole point of putting them here rather than in `reconcile.py` is that the
sweep's age-out and `groomer.py drain` are the two things that move a card OUT
of Intake, and an operator who closes the pen has closed it against both. A
second reading of the same switch is a pen with a hole in it.

  * `INTAKE_HOLD` — the switch. Set, nothing moves and each reader prints one
    line per pass saying so. Cleared, both resume.
  * `INTAKE_MAX_AGE_MINUTES` — how long a card may sit in Intake before the
    sweep escalates it. Absent means the lane contract's own window for Intake,
    so the number a reader finds in `docs/lane-contract.md` is the number that
    runs (the PLANNING_MINUTES rule).
  * `INTAKE_ESCALATION_CAP` — how many aged cards ONE sweep may move.

Every one of them arrives as a `workflow_call` input, which is why nothing here
uses a bare `int()`. On any event where the `inputs` context is empty the
interpolation yields the EMPTY STRING, and `int("")` raises — that would turn a
window question into a red sweep across the fleet. Unset, empty and unparseable
all mean "the default", exactly as `reconcile.resolve_max_wip` already decided
for the WIP cap.

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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_contract  # noqa: E402

#: The environment variables, named once. The workflows thread each from a
#: `workflow_call` input of the same name in lower case, and
#: tests/test_intake_pen.py fails the build if any workflow assigns one a
#: literal instead — a per-repo value baked into the shared channel is how one
#: repo's cutover window becomes everyone's.
ENV_HOLD = "INTAKE_HOLD"
ENV_MAX_AGE = "INTAKE_MAX_AGE_MINUTES"
ENV_CAP = "INTAKE_ESCALATION_CAP"

#: What one sweep may move when the operator has set no cap. Three, because the
#: cutover puts ~220 cards into Intake at once and they cross the window within
#: minutes of each other: uncapped, the first sweep past the window empties the
#: lot into the CEO's queue, which is the failure the mechanism exists to
#: prevent achieved from the other side.
DEFAULT_CAP = 3

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


def max_age_minutes(raw=None) -> int:
    """How long a card may sit in Intake, from the environment or the contract.

    The default is READ from the lane contract rather than restated: this is
    the same number `docs/lane-contract.md` publishes for the lane, and a
    second copy here would be the one that drifts.
    """
    default = lane_contract.stale_minutes()["Intake"]
    return _int(os.environ.get(ENV_MAX_AGE) if raw is None else raw, default)


def escalation_cap(raw=None) -> int:
    """How many aged cards one sweep may move."""
    return _int(os.environ.get(ENV_CAP) if raw is None else raw, DEFAULT_CAP)


def notice(since: str, waiting: int, detail: str) -> str:
    """The one line a held reader prints per pass.

    `detail` is the caller's own measure of what is behind the pen — the sweep
    counts cards past the window, the drain counts the batch it was about to
    move — because each reader knows a different true number and a shared
    sentence with one of them guessed would be worse than two accurate ones.
    """
    when = f"since {since}" if since else "(no date set on the switch)"
    return (
        f"{TAG}: Intake held by the operator {when}; "
        f"{_plural(waiting, 'card')} waiting, {detail} — nothing moves out of "
        f"Intake until {ENV_HOLD} is cleared"
    )


def _int(raw, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")
