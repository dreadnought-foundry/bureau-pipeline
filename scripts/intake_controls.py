#!/usr/bin/env python3
"""The operator's controls on Intake — one reading of them, two readers (DRE-3035).

Intake is a pen, and this module is the gate hardware. FOUR readings of it, and
the whole point of putting them here rather than in `reconcile.py` is that the
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
  * `may_escalate(slug, rail)` — the fourth, and the only one that is not an
    operator knob at all (DRE-3629). It asks whether the repo the sweep is
    RUNNING AS is on the routing rail, and a sweep that is not moves no board
    card it does not own: it prints `off_rail_notice` and stops. The first
    three are the operator's dial; this one is the fence behind the dial, and
    it needs no operator at all.

The first three arrive as `workflow_call` inputs, which is why nothing here
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

AND WHY THE OFF-RAIL REFUSAL PRINTS, for the same reason and with the same
shape: one `off-rail` line per pass, naming the repo and what it declined. The
difference is where it lands — the hold is read AFTER the walk so it can say
how much is behind the pen, and the fence is read BEFORE it, because a sweep
that may move nothing should not spend a read finding out what it would have
moved.
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

#: Opens every off-rail line, for the same reason (DRE-3629). One constant,
#: because the sibling card that fences the sweep's OTHER writers prints this
#: same notice and nothing else — two modules spelling a tag separately is how
#: a grep for "what did the sandbox refuse tonight" comes back half-empty.
TAG_OFF_RAIL = "off-rail"

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


def may_escalate(slug: str, rail) -> bool:
    """May a sweep running as repo `slug` move a board card it does not own?

    The answer is `slug in rail`, and `rail` is the routing snapshot's slug set
    — `validate_card.VALID_SLUGS`, the bundled `config/repo-map.json` keys.
    Deterministic and network-free on purpose: a sweep must not spend a request
    to decide whether it may spend requests, which is why the caller passes this
    and not `live_rail_slugs()` (a network read with a `None` answer).

    WHY IT EXISTS. Intake cards carry no `repo:` label, so the age-out reads the
    whole lane and every repo's sweep is entitled to move the oldest three.
    That was true of production sweeps and was never meant to be true of the
    sandboxes, which run the same reusable `reconcile.yml` — on the night of
    2026-09-09/10 sandbox sweeps helped age 130+ cards into the CEO's queue.
    Being ON the rail is what makes a sweep one of the board's owners.

    `agent-bureau-demo` IS on the rail — cards route to it — so this predicate
    does not fence it, and the operator's dated `INTAKE_HOLD` on the demo stub
    is what holds it until the wave's third epic narrows this same function to
    "the declared age-out owner". That epic adds a clause HERE; it does not add
    a second predicate.

    Pure: it reads no environment, so it can be asked about a repo other than
    the one this process is running as. An empty or `None` rail returns `False`
    — the caller passes the bundled snapshot, which is never empty, so that is
    a caller defect and the safe answer is the one that moves no card.
    """
    if not rail:
        return False
    return str(slug or "").strip().lower() in rail


def off_rail_notice(slug: str, detail: str) -> str:
    """The one line a refused pass prints, opening with the `off-rail` tag.

    `detail` is the caller's own words for what it declined — the age-out
    declines the Intake walk, the sibling card's writers each decline something
    else — because a shared sentence with one of them guessed would be worse
    than four accurate ones. Same rule `notice` already follows.
    """
    return (
        f"{TAG_OFF_RAIL}: this sweep runs as {slug!r}, which is not on the "
        f"routing rail — it moves no board card it does not own, so it "
        f"declined {detail}"
    )


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
