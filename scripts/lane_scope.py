#!/usr/bin/env python3
"""Which lanes Layer 1 of the guard polices (DRE-2754).

Wave 1.5's guard (DRE-2725) undoes an unjustified lane occupancy: a card with no
verdict, in any working lane, goes back to Intake. That rule collides head-on
with the planner (DRE-2719), which creates an epic's children into `Backlog`
while the epic is still pre-approval — `plan.yml:255` creates the sub-issues and
`plan.yml:345` only afterwards moves the epic to Plan Review. At that moment no
verdict exists for either card, so an un-scoped guard bounces the planner's own
output to Intake, and to Triage on the second strike.

**The rule, decided by the operator on 2026-08-26 and recorded in
`architecture/decisions/adr-layer-1-guard-scope.md`:**

    Layer 1 polices the lanes DOWNSTREAM OF PLANNING EXIT.

Planning exit is the transition out of the planning segment — the point at which
the second critic has written its verdict and the card enters `Backlog`. Every
lane before it (`Intake`, `Planning`, `Green Light`) sits before any verdict can
exist, so there is nothing there to check.

This is deliberately NOT an exception list. It is derived from one fact — where
verdicts are produced — and it survives the board changing: a lane added before
Planning exit is unpoliced, a lane added after it is policed, and the rule is not
edited either way. The boundary is named after the transition ("Planning exit"),
never after a description of the lanes on one side of it, because a description
drifts and takes the guard's scope with it.

Consumers: the guard itself (DRE-2725, built in agent-bureau) and the lane
contract-as-data (Wave 1.5 item 12) both read the boundary from here rather than
restating it — one definition, one place to move it if it ever moves.
"""

# The board's lanes in flow order: the planning segment, then the work segment.
# Order is load-bearing — classification is derived from POSITION, so inserting
# a lane here is the whole act of onboarding it.
LANE_FLOW = (
    # --- planning segment: no verdict can exist yet, so nothing to check -----
    "Intake",
    "Planning",
    "Green Light",
    # --- work segment: a verdict exists, so the lane's claim is checkable ----
    "Backlog",
    "Todo",
    "In Progress",
    "In Review",
    "Done",
)

# PLANNING EXIT — the transition a card makes leaving the planning segment. The
# second critic writes its verdict here, which is exactly why the boundary sits
# here and not somewhere describable. Named as a pair of lanes so the transition
# itself is the thing pinned, not a lane index.
PLANNING_EXIT_FROM = "Green Light"
PLANNING_EXIT_TO = "Backlog"

# Wave 1.5 §5 renames `Plan Review` → `Green Light`, and that rename ships in its
# own card (it has to move `reconcile.py` and the relay's escalation string with
# it). Until it lands the live board still says `Plan Review`, so both names
# resolve to the same position — a rename must never move the boundary.
LANE_ALIASES = {
    "Plan Review": "Green Light",
}

# Lanes that are not on the flow at all, each with the reason Layer 1 leaves it
# alone. A lane belongs here only when it is genuinely off the path a card
# takes — not when policing it is merely awkward.
OFF_FLOW = {
    "Triage": (
        "the guard's own destination for a card returned three times — policing "
        "it would bounce the sink straight back to Intake and loop"
    ),
    "Canceled": "terminal and off the path; the card makes no claim to justify",
    "Duplicate": "terminal and off the path; the card makes no claim to justify",
}


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
        f"lane {lane!r} is not in the lane contract — place it in LANE_FLOW "
        f"(before or after {PLANNING_EXIT_FROM!r} → {PLANNING_EXIT_TO!r}) or in "
        "OFF_FLOW with its reason, in scripts/lane_scope.py"
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
         children into `Backlog` before the epic is green-lit, and the second
         critic writes their verdicts only after approval — so a child whose
         epic has not passed Planning exit has no verdict to be judged against
         either. It becomes policed the moment its epic does.

    `parent_epic_lane=None` means a parentless card (a one-off), for which
    clause 2 has nothing to say.
    """
    if not is_after_planning_exit(lane):
        return False
    if parent_epic_lane is not None and not is_after_planning_exit(parent_epic_lane):
        return False
    return True


def policed_lanes() -> tuple[str, ...]:
    """The lanes Layer 1 polices, in flow order — sliced from the flow at
    Planning exit, never enumerated."""
    return LANE_FLOW[LANE_FLOW.index(PLANNING_EXIT_TO):]


def unpoliced_lanes() -> tuple[str, ...]:
    """Every other lane the contract carries: the planning segment, then the
    off-flow lanes."""
    return LANE_FLOW[: LANE_FLOW.index(PLANNING_EXIT_TO)] + tuple(OFF_FLOW)
