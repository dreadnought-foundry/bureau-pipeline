# ADR — Layer 1 of the guard polices the lanes downstream of Planning exit

**Card:** DRE-2754 · **Wave:** 1.5, the intake gate (DRE-2668)
**Decided:** 2026-08-26 by the operator · **Status:** accepted
**Code:** `scripts/lane_scope.py` · **Consumers:** DRE-2725 (the guard),
Wave 1.5 item 12 (the lane contract as data)

## The collision

Two cards, both correct on their own, contradict each other at the seam.

**DRE-2725** — the guard — says `Backlog`'s entrance condition is *a verdict,
and if the card has a parent epic, that epic is In Progress*. That is the point
of the wave: a lane's occupancy must be justified by evidence outside Linear.

**DRE-2719** — everything goes to Planning — says Planning produces the
children. It already does. `plan.yml` creates the sub-issues at `plan.yml:255`
and only afterwards moves the epic to Green Light at `plan.yml:360` (the step is
declared at `plan.yml:352`). **Children therefore exist in `Backlog` while their
epic is still in Planning or awaiting a green light**, and `linear_ops.py`'s
`_create_card` hardcodes that landing.

Apply the guard to that state and a freshly planned epic satisfies neither
clause — no verdict has been written yet, and the epic is not In Progress. Every
child is moved to Intake, and on the second bounce three-strikes sends them to
Triage. **The decomposition is scattered before the CEO reads the plan.**

The defect is in neither card. It is in the assumption that verdicts exist
before cards reach guarded lanes, while the code that creates cards puts them in
a guarded lane before any verdict can exist.

## The decision

> **Layer 1 polices the lanes downstream of Planning exit. Verdicts are written
> at Planning exit, so `Intake`, `Planning` and `Green Light` are not policed.**

Planning exit is the transition out of the planning segment — `Green Light` →
`Backlog` — at which the second critic has written its verdict. Every lane
before it sits before any verdict can exist, so there is nothing there to check.

**This is deliberately not an exception list.** An exception list is arbitrary:
lanes join it because they are awkward, and §5 of the wave plan says that is
where the hole grows. This is derived from one fact — where verdicts are
produced — and it stays one rule as the board changes: a lane added before
Planning exit is covered, a lane added after it is policed, and neither needs
the rule edited.

The same rule has a second clause, because there are two ways a card can be
upstream of the boundary. Its own lane is one. **Its epic's lane is the other:**
the planner's children sit in `Backlog` before their epic is green-lit, and the
second critic writes their verdicts only after approval — so a child whose epic
has not passed Planning exit has no verdict to be judged against either. It
becomes policed the moment its epic does. One fact, applied twice.

## The options rejected

**Option 1 — children created into `Intake` with a pending marker, released to
Backlog when the second critic writes verdicts. Rejected.** It fills the CEO's
"needs you" queue with cards the planner just generated from an epic already
approved. Intake's value is that scanning it tells you something; diluting it
with machine output the operator has implicitly approved costs the one surface
the human actually reads.

**Option 2 — a Planning-scoped holding lane the guard does not police, drained
at Green Light. Rejected, but kept as the fallback.** It protects Intake, but
spends a thirteenth lane in a wave whose central argument is that four of twelve
lanes already do no work. Reconsider it only if a case appears where plan output
genuinely needs policing before approval — today the second critic already reads
the plan before anything promotes.

## The one hazard this carries

**The boundary is only as good as the definition of "where verdicts are
produced."** If that moves, the guard's scope moves silently with it.

So the rule names **Planning exit** explicitly — in `scripts/lane_scope.py`
(`PLANNING_EXIT_FROM` / `PLANNING_EXIT_TO`, and `is_after_planning_exit()`) and
in the lane contract. It is never written as "pre-verdict lanes": that is a
description of one side of the boundary, and a description drifts.

A second guard against drift: a lane the contract has never heard of raises
`UnknownLane` rather than being guessed onto either side. Defaulting an unknown
lane to unpoliced makes the guard silently stop working on a lane somebody added
to the board; defaulting it to policed makes it eat a new flow.

## What does not change

`cmd_subissue` keeps writing children into `Backlog` — the other candidate fix,
and not needed. The children are unguarded until their epic is green-lit, and by
then the second critic's verdicts exist. `plan.yml:255` and the epic move are
untouched, and the `Backlog` landing the card cites at `linear_ops.py:686` still
stands (it now lives in the shared `_create_card()` helper, which the one-off
producer reuses so both create seams run the one `validate_card` gate).

## The other half of the card: the one-off had no producer

`cmd_subissue` requires a parent and derives the child's `repo:` label from
`parent_inherited_labels()`. A one-off has no parent by definition, so there was
no API for the planner to create one and no source for the `repo:` label that
DRE-2744 makes the only routing key. `linear_ops.py oneoff` is that producer:
the same body path-guard, the same `validate_card` gate, the same `**Blocked
by:**` → relation handling and the same `Backlog` landing, with every label
supplied by the plan. `plan.yml` tells the planner about it.

## Consequences

- DRE-2725 builds the guard against `scripts/lane_scope.py` rather than
  restating the boundary. Its test matrix — which covered only the five lanes
  where this collision does not arise — is extended to `Intake`, `Planning` and
  `Green Light`, plus the break-glass cell that DRE-2737 designs.
- DRE-2737 (break-glass) is a sanctioned pass that must be *counted*, not
  undone. It is a Layer 1 outcome, not a lane, so it does not move this
  boundary.
- Wave 1.5 item 12 — the lane contract as data — reads the boundary from this
  module. Onboarding a lane means placing it in `LANE_FLOW` or declaring it in
  `OFF_FLOW` with a reason; there is no third option that silently works.
