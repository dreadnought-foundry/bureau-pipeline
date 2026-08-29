# Plan-critic standard — two passes, two questions, one bound

A plan is read twice before agents build it, and the two readings ask
**different questions**. If they asked the same one, the second would be waste.
The difference is what the plan IS at each moment.

The mechanical form of everything below is `scripts/plan_critic.py`; the rail
that runs it is `.github/workflows/plan.yml`. This is the human form.

## The first critic reviews a moving document

**Question: is this fit to take the CEO's time?**

Does every card carry observable acceptance criteria, a repo, a size? Do the
cards sum to the epic? Is anything plainly ambiguous? Do two cards touch the
same file?

It protects attention, and **it cannot do more than that, because intent is not
settled yet.** It does not redesign the plan, rank the work, or judge whether
the epic is worth doing — that is the CEO's call, and the plan exists to let
them make it.

Its cheap half is mechanical and runs before the critic thinks:

    python3 scripts/linear_ops.py children-json <EPIC> \
      | python3 scripts/plan_critic.py mechanical --surfaces-dir <design dir>

Surfaces accounting reuses `scripts/design_parity.py` — a designed surface is
accounted for only by a card's `**Design:**` ref or an explicit
`deferred: <surface> — <reason>` line (`standards/design-parity.md`). It is not
re-derived here.

**Cross-epic scope: this epic only.**

## The second critic reviews a frozen one

**Question: given this is now the specification, what is missing?**

What will an agent get wrong? Does a card reference something that does not
exist yet? Has every database and infrastructure card got the operator step it
manufactures? Is every external claim about a vendor actually true?

An adversarial pass is only worth much against a **fixed target**, and before
the CEO's approval there isn't one. This is the last point at which a gap is
free to fix — after it the cards enter Backlog and agents build them.

**Cross-epic scope: this epic plus every other epic in flight** (Green Light,
Todo, In Progress). The critic's prompt names those epics one by one and states
what it cannot see — Backlog, Intake and Done epics, other Linear teams,
unmerged branches. Never "consider other work": a critic that does not know
what it was shown cannot tell you what it missed. The cost of this decision is
a vaguer critic, and it was taken deliberately (D3, 2026-08-25) because this is
the cheapest home for collision detection.

## Both loops are bounded

**Two failed rounds at either critic and the plan reaches the CEO regardless**,
with the critic's stated reason attached. Nothing circles a third time. An
unbounded loop is how 17 cards sat in a lane for 27 days.

The two stages count their rounds separately — a send-back before approval does
not spend the budget after it. A round the critic **passed** is not a failure,
and a round it **crashed** on was never a decision: a critic that produced no
result has not rejected anything and never holds a plan
(`standards/console-honesty.md` rule 1).

**The budget belongs to one planning attempt, not to the epic.** An epic sent
back to Triage is re-planned from scratch, and the new plan gets its own
rounds — the earlier ones argued about a plan that no longer exists. The plan
route posts a boundary line when an attempt starts:

    plan-cycle: start epic=DRE-2721

and each critic counts its failed rounds from the last of those. Without it a
re-planned epic inherits a budget it already spent, so its first send-back
reads as the bound and the plan reaches the CEO with no revision round at
all — indistinguishable, from the outside, from a normal pass.

A send-back before approval returns the plan to the planner for one revision.
A send-back after approval returns the epic to `Green Light` with the findings
and **stops the children promoting** — which is the only moment stopping them
is still free.

## The send-back rate is the measurement

**How often the second critic sends a plan back is the honest measure of how
good the first one is.** If it routinely finds significant gaps, the fix is
upstream — a better first critic — not another round.

Every round writes one marker to the epic, and the rate is read back out of
them:

    plan-critic: stage=post round=1 result=SEND_BACK collisions=1 — <reason>

    python3 scripts/linear_ops.py dump-comments <EPIC> \
      | python3 scripts/plan_critic.py rate --stage post

That reads the epic's WHOLE history on purpose: the rate is a measurement
across attempts, while the bound above is scoped to the current one. Two
questions, and the scope is what separates them.

The marker is the record — durable, timestamped, and on the epic it belongs to,
the same convention as the design-parity ledger and the `model-attempt:`
heartbeat. Nothing here is ever spelled like a QA verdict: `VERDICT:`,
`QA Critic` and `QA Verifier` are approval credentials the merge gate reads, and
no plan critic may mint one (`standards/untrusted-content.md`).

## The collision tripwire

Collisions **caught by the second critic** and collisions **found later** are
counted separately, so the tripwire is measurable rather than remembered:

    python3 scripts/linear_ops.py dump-comments <EPIC> \
      | python3 scripts/plan_critic.py collisions
    {"caught_at_review": 1, "found_later": 0}

The first number comes from the critic's own `collisions: <n>` line. The second
is written by whoever finds a collision after the cards reached Backlog — the
engineer who hits it, the fixer who resolves the conflict, the operator who
spots it:

    python3 scripts/plan_critic.py late-collision --epic <EPIC> \
      --with <OTHER-EPIC> --detail "both rewrite scripts/reconcile.py" \
      | xargs -0 python3 scripts/linear_ops.py comment <EPIC>

**A collision reaching Backlog is the signal that this check has to split back
out into its own pass** — rather than the critic being asked to do more.
