# ADR: two plan critics — one before the CEO reads a plan, one after they approve it

- **Status:** Accepted — 2026-08-29. Built by
  [DRE-2721](https://linear.app/dreadnoughtfoundry/issue/DRE-2721):
  `scripts/plan_critic.py`, `standards/plan-critic.md`, wired into
  `.github/workflows/plan.yml` on both routes.
- **Date:** 2026-08-29
- **Cards:** DRE-2721 (this design and its build), DRE-2712 (web search for the
  planner and both critics — a hard prerequisite, not yet shipped), DRE-2720
  (the plan artifact both critics read), DRE-2726 (the lane contract that
  already named this critic at the Green Light boundary)

## Context

A plan reached the CEO with whatever the planner produced, and then reached
Backlog with whatever the CEO approved. Two different failures came out of the
same gap:

- Plans that were not worth the CEO's time — a card with no acceptance
  criteria, cards that did not sum to the epic, two cards editing one file.
  The CEO's attention is the scarcest thing in the loop and nothing protected
  it.
- Plans that were fine to approve and wrong to build — a card referencing a
  table nothing creates, a migration with no operator step, and, in the first
  version of the Wave 1.5 plan, three false claims about a vendor that reached
  `main`.

One critic cannot catch both, and not because of effort. **The two failures are
questions you can only ask at different moments.** Before approval intent is
not settled, so "what is missing from the specification" has no fixed target to
be adversarial about. After approval the text is frozen and that is exactly the
question worth asking — but by then, protecting the CEO's attention is moot,
because it has already been spent.

## Decision — two passes, two questions, one bound

| | First critic | Second critic |
| -- | -- | -- |
| When | After the planner, before Green Light | After the CEO approves, before the children promote |
| Reviews | A moving document | A frozen specification |
| Question | Is this fit to take the CEO's time? | Given this is now the specification, what is missing? |
| Cross-epic scope | This epic only | This epic plus every epic in Green Light / Todo / In Progress, named one by one |
| A send-back means | One revision round with the planner | The epic returns to Green Light and nothing promotes |

**Both loops are bounded at two failed rounds.** After the second, the plan
proceeds to the CEO regardless with the critic's stated reason attached.
Nothing circles a third time — an unbounded loop is how 17 cards sat in a lane
for 27 days. A round the critic passed is not a failure and a round it crashed
on was never a decision, so neither spends the budget.

**The second critic's send-back rate is the measurement of the first one.** If
the post-approval pass routinely finds significant gaps, the fix is upstream —
a better first critic — not another round. Each round writes one
`plan-critic:` marker to the epic and the rate is read back out of them
(`plan_critic.py rate`), the same convention as the design-parity ledger.

### Why the second critic gets cross-epic sight (D3, answered 2026-08-25)

It is the cheapest home for collision detection: that critic is already reading
a full plan with fresh eyes, and two epics that would edit the same interface,
schema field or route is exactly what a reviewer notices and a planner working
inside one epic cannot.

**The cost is a vaguer critic**, and it is paid deliberately. Two things bound
it. The scope is stated exactly — the prompt names the epics the critic can see
and says what it cannot, never "consider other work" — and collisions caught at
review are counted separately from collisions found later
(`plan_critic.py collisions`). **A collision reaching Backlog is the tripwire:
the signal that this check has to split back out into its own pass, rather than
the critic being asked to do more.**

## Consequences

- The plan job runs up to four agent passes on the plan route (plan, critic,
  re-plan, critic) and its timeout moved 30 → 45 minutes. The contract's
  Planning stall window (120 minutes) still exceeds it, so the sweep does not
  alarm on a run that is simply still going.
- The activate route no longer promotes inside the route step. Promotion is its
  own step, gated on the second critic's decision — which is the only moment
  stopping the children is still free.
- A crashed critic never holds a plan (`standards/console-honesty.md` rule 1).
  The result reads `NO_RESULT`, the plan proceeds, and the round is not counted
  against the bound.
- **Web search is still missing.** DRE-2712 gives the planner and both critics
  web search in one change, deliberately never separately. Until it lands, the
  second critic can find a claim about a vendor *suspicious* but cannot check
  it — which is precisely the failure that put three false claims on `main`.
- The two critics are roster entries (`agents.yaml`) on the advisory ladder,
  with their own assembled context: the pre stage deliberately does NOT read
  the engineering floor or the system shape, because reading them is how it
  starts doing the second critic's job.
