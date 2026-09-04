# ADR: mid-epic discovery — a finding made while building joins an approved epic

- **Status:** Accepted — 2026-08-26. Built by
  [DRE-2739](https://linear.app/dreadnoughtfoundry/issue/DRE-2739):
  `scripts/mid_epic.py`, wired into `scripts/reconcile.py` (the promotion gate
  and the sweep's growth report) and `scripts/linear_ops.py` (the create seam).
- **Date:** 2026-08-26
- **Cards:** DRE-2739 (this design and its build), DRE-2719 / DRE-2725 /
  DRE-2754 (the intake front door it plugs into)

## Context

Everything in the intake design describes work arriving from OUTSIDE: idea →
Intake → Planning → green light → build. The highest-value findings this system
produces are made **mid-build**, by whoever is already in the code, about an epic
that is already approved and already running.

The worked example, 2026-08-25: while proving the deploy rollback, an agent found
that `deploy-console.sh:547` records a mutable *tag* as the rollback target, and
that a fix landed earlier the same day had made the rollback **trust** that
annotation as its authority. The morning's fix made the flaw more load-bearing,
not less — and the finding changed the recommendation for a card inside an
approved epic.

Under the design as written that finding has nowhere to go. Send it to Intake and
it queues behind a planning cycle, losing the context that made it findable. Add
it silently and the approved plan no longer describes the work. **People choose
the second one**, because they are mid-flow and the first is a wall.

## Decision — two kinds, told apart by one question

The distinguishing question is not size, effort or urgency. It is: **does the
approved plan still describe what we are doing?**

| | Addition | Amendment |
| -- | -- | -- |
| What happened | The plan holds; there is more of it than we knew | The plan no longer describes the work — a recommendation changed |
| Example | A second call site needs the same fix | The fix must be split and its order reversed |
| Route | Into the epic with a verdict. **No new green light** | Back to Planning; the epic is re-green-lit |
| Who decides | Whoever found it, in one line of justification | Same |

Guessing wrong is cheap: the artifact update catches an amendment mislabelled as
an addition, because the plan will not absorb it. So the classification is one
required line at creation, not a form and not a review step.

## Decision — an addition carries a verdict, not a green light

`reconcile.promote_ready` auto-promotes Backlog children of an active epic once
their blockers clear. **A card added mid-epic dispatches an agent on the next
sweep — within fifteen minutes — whether or not anyone has read it.** Right for a
card the plan anticipated; wrong for one nobody has seen.

So an addition carries a **verdict** before it joins the epic (the
`mid-epic-verdict` comment). Layer 1 is not waived. What is waived is the **green
light** — the human decision — because that was already made for this epic, and
re-asking on every second call site is how the queue becomes the bottleneck
again.

## Decision — "added mid-epic" is derived, never marked

A child is a mid-epic addition when its `createdAt` is later than the epic's most
recent entry into an active lane, both read live from Linear. Nothing has to
remember to stamp anything, which is the point: the hazard IS the card nobody
stamped — the hand-add straight into Linear.

Two consequences, both deliberate:

- **An unreadable green light abstains.** Refusing every child because Linear
  could not answer would freeze the fleet, and claiming "green-lit at 0 cards"
  invents an approval that never happened (console-honesty rules 1 and 2).
- **Re-approval after an amendment is an observation**, not an assumption: the
  epic is seen in an active lane again. A return to Planning is not evidence that
  anyone answered it.

## Decision — growth is legible, not policed

The epic shows what it was green-lit at and what it is now: approved at nine
cards, running at fourteen. The numbers live in a managed region of the epic's
own description (`ARTIFACT_BEGIN`/`ARTIFACT_END`), refreshed by every mid-epic
motion and by every full reconcile sweep, so the CEO reads them where the plan
is. Nobody polices the number; it just has to be visible, because silent
accretion turns an approved scope into an unapproved one with no single decision
being wrong. A card added without the artifact changing is named on the epic,
once.

## Decision — a card has no children

`validate_card.infer_agent_label` decides what a card *is* from whether it has
children (`if "[epic]" in t or has_children: return "agent:planner"`), and
`reconcile.promote_ready` skips every `agent:planner` card because epics are
promoted by humans and never by the sweep. **Giving a card sub-issues silently
converts it into an epic and stops it ever being promoted.**

So `linear_ops.cmd_subissue` refuses a parent that is not already an epic, and
says why — the reclassification, the permanent non-promotion, and the route that
works. A mid-epic discovery becomes a new **sibling card under the same epic**,
with its own number. Not a `2716a`/`2716b` suffix, not a child.

## Consequences

- One extra Linear read per epic per sweep (the green light), cached across that
  epic's children the way the epic-level blocker gate already is.
- The `subissue` create seam buys one read of the parent's planning shape stamp
  when its title does not already say `[EPIC]`, and a children read only when
  the stamp does not settle it either. **Amended 2026-09-04 (DRE-3038):** the
  epic test used to read `agent:planner` as a third leg, so the planner's own
  path bought nothing — but that label says the planner *owns* the card, every
  card dispatched to `plan.yml` from Planning carries it, and reading it as
  epic-ness answered "epic, no verdict" for every one-off. The shape stamp
  (DRE-2843) is what says what a card *is*, so the planner's path now costs one
  comment read per child and a one-off wearing the label can no longer be given
  children.
- A card hand-added to a running epic now sits in Backlog until somebody files it
  properly. That is the intended trade: fifteen minutes of an unread agent run is
  more expensive than a refusal that names its own remedy.

## Rejected alternatives

- **Route the finding through Intake.** It is the wall people already route
  around, and it discards the context that made the finding possible.
- **Mark additions with a label at creation.** Only catches the cards that came
  through the route — i.e. every card except the one the design is about.
- **Require a fresh green light per addition.** Re-asking the CEO on every second
  call site rebuilds the queue the wave exists to drain.
- **Cap epic growth.** Nobody polices the number; a cap would turn a visible fact
  into an argument, and the failure mode is invisibility, not size.
