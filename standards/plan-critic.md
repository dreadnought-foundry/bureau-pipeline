# Plan-critic standard — two critics, three moments, one bound

A plan is read twice before agents build it, and the two readings ask
**different questions**. If they asked the same one, the second would be waste.
The difference is what the plan IS at each moment.

There is a third moment and it is **the first critic reading a card that owes
no plan** — a one-off, on its way to the build queue. Two critics, three
moments: the one-off stage is a stage, not a role, and it shares the first
critic's agent, ladder, brief and result grammar.

The mechanical form of everything below is `scripts/plan_critic.py`; the rail
that runs it is `.github/workflows/plan.yml`. This is the human form.

## The first critic reviews a moving document

**Question: is this fit to take the CEO's time?**

Does every card carry observable acceptance criteria, a repo, a size? Do the
cards sum to the epic? Is anything plainly ambiguous? Do two cards touch the
same file?

**"Observable" is not enough on its own — ask WHEN it becomes observable.** A
criterion whose verb is *observed*, *watched* or *seen in production*, or that
names a run id, a deploy, a live account or a tag move that does not exist yet,
can only be met after the change is merged — and a criterion that requires the
merge **cannot gate the merge**. That is a card defect readable here, so send it
back to be written as two cards: the build card keeps what a reviewer can check
against the diff, and the observation becomes a follow-up carrying `needs-human`
and `no-code`, blocked by the build card
(`standards/card-quality.md`). The same question applies on the one-off route
below, which reads cards through this same standard.

DRE-3075 was filed with two such criteria and every agent downstream then
behaved correctly: the build agent met the one provable criterion and said "not
provable before merge" for the rest, the critic blocked on unmet criteria, the
fix agent had no code to change and burned four dispatches establishing that,
and PR #252 sat `CONFLICTING` for eleven hours until an operator decision moved
it. Nobody was wrong; the card was, and the cost landed on the human. Caught
here it costs one send-back.

It protects attention, and **it cannot do more than that, because intent is not
settled yet.** It does not redesign the plan, rank the work, or judge whether
the epic is worth doing — that is the CEO's call, and the plan exists to let
them make it.

Its cheap half is mechanical and runs before the critic thinks — **in a step of
its own, and the findings are posted to the epic before the model reads them**:

    python3 scripts/linear_ops.py children-json <EPIC> \
      | python3 scripts/plan_critic.py mechanical --surfaces-dir <design dir> \
          --note-file <note>

Surfaces accounting reuses `scripts/design_parity.py` — a designed surface is
accounted for only by a card's `**Design:**` ref or an explicit
`deferred: <surface> — <reason>` line (`standards/design-parity.md`). It is not
re-derived here.

**The collision check reads the DECLARED footprint** — the `**Files:**` line
`briefs/planner.md` calls "the INPUT to the ordering" — parsed once in
`scripts/plan_footprint.py` and consumed by nothing else. Root-level files are
files; a card that declares no footprint is a finding, never a silent empty
set; and a **delivered child is not in the input at all** (see the state
section below). **The repo check reads the `repo:<slug>` LABEL**, which is what the
contract requires (`standards/card-quality.md`); the body stamp it replaced is
deprecated and the planner brief forbids writing it.

Each of those three was bought on DRE-3019 (F3, 2026-09-03): the first critic
passed that plan with `collisions=0` and could not have found a collision if
there had been one. Nothing read `Files:`, so four of five children wrote it as
`**Files: **` and nothing noticed. The path regex required a `/`, so
`README.md` and `CHANGELOG.md` were invisible. And the body regex flagged all
five correctly-built children as "names no repo" — five false findings a critic
learns to skip, and the finding it skips next is a real one. **The findings are
posted first because the critic's own turn output is hidden** ("full output
hidden for security"), so a list that exists only inside the turn cannot be
checked against the verdict that followed.

**The same list checks each footprint against the deaths already on the board**
(DRE-3079). `config/split-ledger.json` records every card that did not fit one
run — what it declared, what its split pieces actually touched, how many
turn-cap deaths it cost, which of DRE-2893's tells applied. A child sharing
**two or more** files with a row that DIED is a finding naming that row; a
child carrying a tell the ledger has watched kill cards is another, quoting the
ledger's own rate and the phrase that fired the tell. One shared file is the
ordinary state of this repo, so it is not a match — a check that fires on every
card is the five false findings above wearing a new name.

A ledger that **could not be read is its own finding**, never an empty list:
"checked against nine death rows and matched none" and "never opened the
ledger" are different facts, and only the first clears a plan
(`standards/console-honesty.md` rule 1). The posted note carries the row count
it checked against for that reason.

**Cross-epic scope: this epic only.**

## A child's STATE is part of the plan, and both critics read it

**A card's text and the repository cannot tell you whether the card is
to-build.** Read as text alone, every Done card asks for work that is already
on `main` — because that is what a Done card is. So the children block both
critics are handed (`linear_ops.py children-json`) carries each child's
**state** beside its body, and the mechanical note prints the lane of every
card before it prints a finding.

* **Done, Canceled, Duplicate — a delivered child.** Delivered or dropped,
  never to-build. *"Its deliverable already exists on `main`"* is **not a
  finding** against it, and neither is a collision with a sibling over a file
  it has already merged — the disjoint-files rule is about two OPEN pull
  requests, and a merged one races nobody. The mechanical note names the
  delivered child so the observation cannot be re-raised under another
  heading, and the collision check **drops that card from its input** so the
  note cannot say "not a collision" and then list one two paragraphs below.
* **In Progress, In Review.** A run or a pull request is in flight. Judge the
  card on what it will land, not on whether its files are in the tree yet.
* **Backlog, Todo, Triage — still to build**, and every finding stands,
  "this is already implemented" included. Same card, same files, different
  answer: the state is the whole of the difference.
* **No state at all** is UNKNOWN, never "delivered". The note says *no child
  carried a state* rather than reporting none, because a read that failed
  excuses nothing (`standards/console-honesty.md` rule 1).

The cost of not reading it (DRE-3243, 2026-09-06): the post-approval critic's
round 2 on DRE-3164 sent the plan back with *"DRE-3210's entire deliverable
already exists, fully implemented, on main — the card asks an agent to build
already-shipped work."* DRE-3210 was **Done** — built, reviewed, merged and
closed by that merge the evening before. It was the second of two send-backs,
so the epic hit the bound and parked with `needs-human` on a finding that was
not a gap. A false hold spends one of the two rounds and costs the CEO an
approval; two of them park a sound plan.

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

## The same first critic reads a one-off before it is built

**Question: is this one pull request of work an agent can build unattended,
with nothing in it that is a decision?**

A one-off owes no plan and no green light — its exit from Planning is
mechanical — so before this it was read by **nobody**. The shape stamp was the
only judgement on the fast path, and then an engineer was dispatched: the first
adversarial eye a one-off met was the code critic on its pull request, after
the build had been paid for. On 2026-09-03 a card that was purely a business
decision was stamped `one-off`, routed FLEET, and would have been built.

So the critic reads the card and the stamp's own reason, once, between the
stamp and the move. One bounded call per one-off classification. It checks the
thing nothing else on that route checks — **is any of this a decision** — plus
whether the card is really one pull request and whether an agent can tell it is
done from the card alone.

**It fails CLOSED, and that is the one place this route inverts the epic
route.** A pass moves the card to the build queue; a send-back, a crash, an
unusable result and an unavailable model all take the escalation exit to
`Green Light` with the reason in business terms. That does not contradict "a
crash is not a rejection" — it is the same rule read in a different place.
Before the CEO reads a plan, a critic that decided nothing must not stop a
human from reading it, because something else reads the plan next. On the
one-off route **nothing** reads the card next, so "the critic did not decide"
cannot be spent as "the critic said yes". The cheap outcome is a person
answering a question; the expensive one is a build nobody asked for.

**The reason lands on the card either way** — pass or send-back — so the
planner scorer can grade critic against classifier against outcome.

## Both plan loops are bounded

**Two failed rounds at either critic and the loop ends**, with the critic's
stated reason attached. Nothing circles a third time. An unbounded loop is how
17 cards sat in a lane for 27 days.

What "ends" means depends on which side of the CEO the critic sits (DRE-3088):

* **Before approval**, the plan reaches the CEO regardless. Proceeding here
  costs a person a read, and the CEO can still send it back.
* **After approval**, every send-back first gets one **re-plan** with the
  critic's finding — the planner revises the children in place — and what
  happens next turns on **whether that revision changed the card SET**
  (DRE-3291), because that is the only part of it the CEO has not already
  approved. The run snapshots the children either side of the re-plan and asks
  `review_rerun.py card-set`; a snapshot it could not take reads as *changed*,
  which is the direction that puts the plan in front of a person.

  * **Same cards** — no lane move at all. The run asks for the review ITSELF
    (`repository_dispatch`, ACTIVATE route, `reason: re-review`) and says so on
    the epic: the finding, what the re-plan changed in plain English, and that
    the review is re-running as round N+1 of 2. Nothing is the CEO's to decide,
    so nothing is asked of him. Until this the workflow moved the epic to Green
    Light and asked him to approve a plan whose cards he had already read —
    DRE-3164 collected five approvals that way.
  * **A card added or removed** (or a re-plan that did not finish) — Green
    Light, with the added/removed cards named by identifier. That shape is one
    he has not seen, and the ask is the single move that is left: Approve from
    Green Light (the console's Approve, or a move to **In Progress**), which
    runs the review once more. Not the re-run act — here the plan itself is
    what wants reading, and asking for the review without that read is the
    thing this branch exists to avoid.
  * **Two failed rounds** — the plan **parks** in Green Light with
    `needs-human` and both findings quoted, whatever the card set did. It is
    never activated as it stands: "proceed" on this side means agents build it,
    and a plan the critic held twice is exactly the specification that would
    make them build the wrong thing. Green Light with the hold label is a
    watched queue, not the unread lane the 27-day failure lived in — and the
    sweep's own gate (`plan_critic.post_release`) reads the bound the same way,
    so no cron sweep promotes the children of a parked epic either. To ask for
    the review again after settling it: clear `needs-human`, then post a
    comment on the epic that says exactly `▶️ re-run the review` — or, since a
    parked epic sits in Green Light, approve it (the console's Approve, or a
    move to **In Progress**). That sentence is `plan_critic.REAPPROVE_HOW`,
    and the notice says it in those words.

**The relay has two triggers, and every notice that asks for a re-run names
both** (DRE-3292). One is the move **INTO In Progress** — the approval itself,
which is why it reaches only an epic waiting in Green Light: an epic already
sitting In Progress cannot make that move, and that is where a dead or unread
review leaves it (DRE-3241; never Todo either — an epic in Todo dispatches
nothing). The other is a comment whose **whole body** is `▶️ re-run the review`
(DRE-3287), which asks for the review directly from whichever lane the epic is
in; the console's Approve posts it for an epic already In Progress. Until the
relay learned the act the only way to ask was to move the epic out to Green
Light and approve it back in — a two-lane dance asked of a person for a review
nothing else would start.

The string lives once, in `review_rerun.RERUN_REVIEW_ACT`, and the relay and
the console mirror it byte for byte. Because every notice embeds it in prose, no
notice can BE the act: the relay matches the whole comment body, so a notice
quoting it alone would re-run the review each time the pipeline posted it
(DRE-3286) — and the act quoted on this page is inert for the same reason.

The two stages count their rounds separately — a send-back before approval does
not spend the budget after it. A round the critic **passed** is not a failure,
and a round it **crashed** on was never a decision: a critic that produced no
result has not rejected anything and never holds a plan
(`standards/console-honesty.md` rule 1).

**A review that DIES leaves a tombstone, and holds until it is re-run.** Two
things end without a verdict and they are not the same fact. A critic that ran
to its decision and wrote nothing usable is `NO_RESULT`: the run records it as
a round, says so on the epic with a ⚠️ note, and proceeds. A review that never
reached its decision — the action died at its turn ceiling, or crashed — used
to leave *nothing*: the job went red, and the sweep read the newest marker it
could find, which on 2026-09-05 was a send-back the re-plan had already
answered. So the run now writes a tombstone, in the same shape as a round —
one line, alone in its comment, the pipeline's own:

    🪦 plan-critic-died: stage=post run=34008698027 attempt=2 step=posta subtype=error_max_turns turns=41 ceiling=40

It is not a round: it carries no result, spends nothing of the bound, and the
rate ignores it. The sweep reads it as *the review died — it was not a
rejection* and holds the children under its own tag. Its ceiling is sized from
the plan (`plan_critic.post_review_turns`: fifteen cards get 80 turns), because
the reading is linear in the cards and a fixed 40 had no headroom at fifteen.

**And the review re-runs ITSELF, once, at a higher ceiling** (DRE-3289). The
way forward used to be a move only a person could make, for a plan nobody had
found anything wrong with, so a death left the epic In Progress with nothing
scheduled until someone noticed. The run that writes the tombstone now asks
`review_rerun.after_death` what to do about it, and there are exactly three
answers:

* **retry** — the first turn-cap death since the last round. The run asks for
  its own re-run (`repository_dispatch`, `trigger_state: in progress`,
  `reason: review-retry`) and the next run reads the tombstone off the thread
  and sizes itself at `ceil(ceiling × 1.5)`, capped at 180 — 80 → 120,
  120 → 180. **The epic's lane is never written**: it is already In Progress,
  and moving it would ask the CEO for a decision he does not have to make.
* **park** — the second death. `needs-human`, Green Light, and a note naming
  BOTH dead runs. Two turn-cap deaths on one card is the operator's signal to
  split, not a queue position (`standards/card-quality.md`), and what an
  operator needs to see is why the review cannot finish at either ceiling.
* **leave** — any other subtype. That death belongs to the medic, which
  retries a non-turn death once already and refuses a turn-cap one outright
  (`medic_retry.RULE_TURN_EXHAUSTION`). This rail retries ONLY
  `error_max_turns`, so the two never both act on one death.

The job still goes red on a death — no `continue-on-error` on the review, and
the decision and activation steps keep their implied `success()` — so the
tombstone step is the only thing that runs, and the tombstone lands before the
dispatch: a crash between them leaves an honest record and the sweep's refusal
still names what happens next.

**The budget belongs to one planning attempt, not to the epic.** An epic sent
back to Triage is re-planned from scratch, and the new plan gets its own
rounds — the earlier ones argued about a plan that no longer exists. The plan
route posts a boundary line when an attempt starts:

    plan-cycle: start epic=DRE-2721

and each critic counts its failed rounds from the last of those — the last one
the PIPELINE wrote, naming THIS epic. Without it a re-planned epic inherits a
budget it already spent, so its first send-back reads as the bound and the plan
reaches the CEO with no revision round at all — indistinguishable, from the
outside, from a normal pass.

**A round record is a comment the pipeline wrote that says nothing else.** Two
conditions, and both are needed.

The line above is a real one, and anyone with comment access on an epic can
post it. Left unchecked, two comments carrying a `SEND_BACK` marker made a
critic's real, current rejection read as "the budget is already spent" and
promoted the children to build; one carrying the boundary refunded a budget
that had been spent, so the plan could circle indefinitely. So the thread is
read with authorship (`linear_ops.py dump-comments <EPIC> --with-authors`) and
a record from any other author is ignored.

Authorship is only half of it, because the pipeline's shared Linear key writes
far more to an epic than round decisions — the **planner's own plan write-up**
lands in the same thread through the same call, freeform prose derived from the
epic's untrusted description and expected to explain this gate to the CEO. One
sentence of it quoting the example above used to be a round nobody ran. So a
record has to be **one line, alone in its comment**: every round posts its note
and its record as two separate comments, and a marker or boundary embedded in
prose records nothing.

Both failures are quiet — nothing a person sees looks wrong. Quoting this page,
anywhere, is inert.

A send-back before approval returns the plan to the planner for one revision.
A send-back after approval returns the epic to `Green Light` with the findings
and **stops the children promoting** — which is the only moment stopping them
is still free.

And the marker is what stops them, on both paths (DRE-3059). The reconcile
sweep is the ONE promoter of an epic's children, and it releases a child only
when the epic carries a `stage=post` round for the current planning attempt
that let the plan through — a PASS, or a `NO_RESULT` crash. A send-back holds,
the bound parks, and a tombstone holds until the review is re-run. Until then
the child stays in `Backlog` and the sweep says so, naming the epic. Nothing else
promotes: the activate route runs that same promoter the moment the critic
passes, rather than promoting on its own.

## The send-back rate is the measurement

**How often the second critic sends a plan back is the honest measure of how
good the first one is.** If it routinely finds significant gaps, the fix is
upstream — a better first critic — not another round.

Every round writes one marker to the epic — as its own comment, carrying
nothing else — and the rate is read back out of them:

    plan-critic: stage=post round=1 result=SEND_BACK collisions=1 — <reason>

    python3 scripts/linear_ops.py dump-comments <EPIC> --with-authors \
      | python3 scripts/plan_critic.py rate --stage post

That reads the epic's WHOLE history on purpose: the rate is a measurement
across attempts, while the bound above is scoped to the current one. Two
questions, and the scope is what separates them.

The marker is the record — durable, timestamped, alone in its comment, and on
the epic it belongs to, the same convention as the design-parity ledger and the
`model-attempt:` heartbeat. Nothing here is ever spelled like a QA verdict: `VERDICT:`,
`QA Critic` and `QA Verifier` are approval credentials the merge gate reads, and
no plan critic may mint one (`standards/untrusted-content.md`).

## The collision tripwire

Collisions **caught by the second critic** and collisions **found later** are
counted separately, so the tripwire is measurable rather than remembered:

    python3 scripts/linear_ops.py dump-comments <EPIC> --with-authors \
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
