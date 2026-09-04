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
| A send-back means | One revision round with the planner | One revision round with the planner, then the epic returns to Green Light with a receipt saying what changed; nothing promotes (DRE-3088) |
| At the bound (two failed rounds) | The plan proceeds to the CEO regardless, reason attached | The epic PARKS in Green Light with `needs-human` and both findings; it is never activated as it stands (DRE-3088) |

**Both loops are bounded at two failed rounds, and what the bound does depends
on which side of the CEO the critic sits (amended by DRE-3088, 2026-09-04).**
Before approval, the second failed round sends the plan to the CEO regardless,
with the critic's stated reason attached — "proceed" there costs a person a
read. After approval, "proceed" means agents build it, so the second failed
round **parks** the epic in Green Light with `needs-human` and both findings,
and the sweep's own gate (`plan_critic.post_release`) reads the bound the same
way, so no cron sweep promotes the children either. Every post-approval
send-back first gets one re-plan with the critic's finding, so the CEO
re-approves a revised plan, never the same one; on DRE-3060 (2026-09-04) the
original rule sent the identical plan back three times and then activated it.
Nothing circles a third time on either side — an unbounded loop is how 17
cards sat in a lane for 27 days, and Green Light with the hold label is a
watched queue, not that lane. A round the critic passed is not a failure and
a round it crashed on was never a decision, so neither spends the budget.

**The bound is scoped to one planning attempt.** An epic sent back to Triage is
re-planned from scratch, and the new plan gets its own rounds; the plan route
posts a `plan-cycle:` boundary when an attempt starts and each critic counts
from the last one. Counting over the epic's lifetime instead would push a
re-planned epic to the CEO on its first send-back with no revision round — and
nothing about the note would say so, which makes it a silent failure of the
one promise this feature makes.

**The second critic's send-back rate is the measurement of the first one.** If
the post-approval pass routinely finds significant gaps, the fix is upstream —
a better first critic — not another round. Each round writes one
`plan-critic:` marker to the epic and the rate is read back out of them
(`plan_critic.py rate`), the same convention as the design-parity ledger.

**A record is a comment the pipeline wrote that says nothing else.** Because the
bound is enforced entirely out of those markers, they are this gate's
credential, and two things have to be true of one.

It has to be **the pipeline's**: a Linear comment thread is writable by anyone
with access to the card, so the thread is read with authorship
(`dump-comments --with-authors`), with the cycle boundary additionally scoped to
the epic being decided. Without that, two ordinary comments overrode a real
critic rejection and promoted the children to build, and one refunded a spent
budget so the plan could circle indefinitely.

And it has to be **the whole comment**. Authorship alone resolves to "posted
with the shared Linear key", which the planner also holds: its plan write-up
lands in the same thread, freeform prose over the epic's untrusted description,
and `briefs/planner.md` now asks it to explain this gate. Read line by line, one
sentence quoting the worked example was a round nobody ran. So every round posts
its CEO-facing note and its record as **two comments**, and a marker embedded in
prose is prose.

Neither failure needs a hostile actor — this standard's own worked example is a
literal boundary line — and neither leaves anything a reader would see as wrong
(`standards/untrusted-content.md`).

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
- **That gate reached only one of the two promoters, and the other one won
  (DRE-3059).** The reconcile sweep runs every fifteen minutes and released a
  child on the epic's LANE alone, so on 2026-09-03 it promoted DRE-3026 and
  DRE-3027 eighty-two seconds after the CEO approved their epic, with no
  post-critic round on it at all — DRE-3058 is why none had run. There is now
  ONE promoter: `reconcile.promote_ready()`, which reads the same
  `stage=post` marker this ADR describes and refuses a child whose epic has no
  released plan for the current planning attempt. The activate route triggers
  that promoter rather than promoting itself, so the fast path and the cron
  sweep cannot disagree about the same epic. Epics green-lit before
  `plan_critic.GATED_FROM` are not re-gated: they have no marker and never
  will, and freezing them was not the point.
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
