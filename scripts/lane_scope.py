#!/usr/bin/env python3
"""Which lanes Layer 1 of the guard polices (DRE-2754), read from the contract.

Wave 1.5's guard (DRE-2725) undoes an unjustified lane occupancy: a card with no
verdict, in any working lane, goes back to Intake. That rule collides head-on
with the planner (DRE-2719), which creates an epic's children into `Backlog`
while the epic is still pre-approval — `plan.yml:255` creates the sub-issues and
`plan.yml:360` only afterwards moves the epic to Green Light. At that moment no
verdict exists for either card, so an un-scoped guard bounces the planner's own
output to Intake, and to Triage on the second strike.

**The rule, decided by the operator on 2026-08-26 and recorded in
`architecture/decisions/adr-layer-1-guard-scope.md`:**

    Layer 1 polices the lanes DOWNSTREAM OF PLANNING EXIT.

Planning exit is the transition out of the planning segment — the point at which
the second critic has written its verdict and the card enters `Backlog`. Every
lane before it sits before any verdict can exist, so there is nothing there to
check.

This is deliberately NOT an exception list. It is derived from one fact — where
verdicts are produced — and it survives the board changing: a lane added before
Planning exit is unpoliced, a lane added after it is policed, and the rule is not
edited either way. The boundary is named after the transition ("Planning exit"),
never after a description of the lanes on one side of it, because a description
drifts and takes the guard's scope with it.

**Where the lanes come from (DRE-2726).** This module used to carry its own copy
of the flow, the off-flow lanes and their reasons, and the rename aliases. It
does not any more: every one of them is read from `config/lane-contract.json`,
the single file the harness asserts the live board against, the sweep takes its
stall windows from, and `docs/lane-contract.md` is rendered from. A guard with
its own copy of the rules is a second document, and a second document drifts —
which is the finding this whole wave keeps arriving at.

Consumers: the guard itself (DRE-2725, built in agent-bureau) and every pipeline
script that needs the boundary read it from here rather than restating it.
"""

import lane_contract

# The board's lanes in flow order: the planning segment, then the work segment.
# Order is load-bearing — classification is derived from POSITION, so onboarding
# a lane is one entry in the contract file and no edit here.
LANE_FLOW = lane_contract.flow_lanes()

# PLANNING EXIT — the transition a card makes leaving the planning segment. The
# second critic writes its verdict here, which is exactly why the boundary sits
# here and not somewhere describable. Declared as a pair of lanes in the
# contract, so the transition itself is the thing pinned, not a lane index.
PLANNING_EXIT_FROM, PLANNING_EXIT_TO = lane_contract.planning_exit()

# Retired board names accepted on INPUT, so a board mid-rename still resolves to
# the right lane. `classify()` normalizes a board name INTO the contract;
# `linear_ops._LANE_RENAME_FALLBACKS` inverts it to write a contract name OUT to
# a board that does not carry it yet. Both read the same entry, and an entry is
# deleted from the contract once no board carries the old name.
LANE_ALIASES = lane_contract.aliases()

# Lanes that are not on the flow at all, each with the reason Layer 1 leaves it
# alone. A lane belongs here only when it is genuinely off the path a card
# takes — not when policing it is merely awkward. The contract refuses an
# off-flow lane that gives no reason.
OFF_FLOW = lane_contract.off_flow()


class UnknownLane(Exception):
    """A lane the contract has never heard of.

    Raised rather than guessed. Defaulting an unknown lane to unpoliced makes the
    guard silently stop working on a lane somebody added to the board; defaulting
    it to policed makes the guard eat a new flow. Console-honesty rule 1: derive
    from truth, never infer from an adjacent signal.
    """


def canonical_lane(lane: str) -> str:
    """The contract's name for `lane`, resolving a rename alias."""
    return LANE_ALIASES.get(lane, lane)


def classify(lane: str) -> str:
    """Where `lane` sits relative to Planning exit.

    One of `"before-planning-exit"`, `"after-planning-exit"`, `"off-flow"`.
    Raises `UnknownLane` for anything the contract does not carry.
    """
    name = canonical_lane(lane)
    if name in LANE_FLOW:
        exit_at = LANE_FLOW.index(PLANNING_EXIT_TO)
        return "after-planning-exit" if LANE_FLOW.index(name) >= exit_at else "before-planning-exit"
    if name in OFF_FLOW:
        return "off-flow"
    raise UnknownLane(
        f"lane {lane!r} is not in the lane contract — declare it in "
        f"config/lane-contract.json, on the flow (before or after "
        f"{PLANNING_EXIT_FROM!r} -> {PLANNING_EXIT_TO!r}) or off it with its reason"
    )


def is_after_planning_exit(lane: str) -> bool:
    """True when `lane` is downstream of Planning exit — i.e. a verdict can
    exist for a card sitting there."""
    return classify(lane) == "after-planning-exit"


def is_policed(lane: str, *, parent_epic_lane: str | None = None) -> bool:
    """Does Layer 1 of the guard apply to a card in `lane`?

    Two clauses, both the same rule applied to the two ways a card can still be
    upstream of the point where verdicts are produced:

      1. **The card's own lane.** Before Planning exit → not policed.
      2. **Its epic's lane**, when it has a parent epic. The planner creates
         children into the first work lane before the epic is green-lit, and the
         second critic writes their verdicts only after approval — so a child
         whose epic has not passed Planning exit has no verdict to be judged
         against either. It becomes policed the moment its epic does.

    `parent_epic_lane=None` means a parentless card (a one-off), for which
    clause 2 has nothing to say.
    """
    if not is_after_planning_exit(lane):
        return False
    if parent_epic_lane is not None and not is_after_planning_exit(parent_epic_lane):
        return False
    return True


def policed_lanes() -> tuple:
    """The lanes Layer 1 polices, in flow order — sliced from the flow at
    Planning exit, never enumerated."""
    return LANE_FLOW[LANE_FLOW.index(PLANNING_EXIT_TO):]


def unpoliced_lanes() -> tuple:
    """Every other lane the contract carries: the planning segment, then the
    off-flow lanes."""
    return LANE_FLOW[: LANE_FLOW.index(PLANNING_EXIT_TO)] + tuple(OFF_FLOW)
