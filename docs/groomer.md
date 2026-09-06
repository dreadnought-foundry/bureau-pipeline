# The groomer — one batch at a time, and you approve it

`scripts/groomer.py`, run on demand by `.github/workflows/self-groomer.yml`.
DRE-2683.

The critic answers whether one card can be built. Planning answers whether one
card is still wanted. Both read a single card, and **a batch is not a list of
individually-good cards** — the facts that decide a batch only exist *between*
cards: two cards editing one file, eleven children of one epic that belong in
one cycle, a repo that waits two months because another repo goes first.

The groomer is the reader that sees the set. It proposes; the CEO approves;
only then does anything leave Intake.

## What one run does

1. **Reads the whole lane**, paginated. Page one is not the population — the
   sweep's `issues(first: 100)` with no cursor made its world the first 100 rows
   of a 226-card Backlog, and which rows it never saw was decided by Linear's
   default ordering (DRE-2681). Completeness is asserted: every card in the
   population comes out carrying exactly one outcome.
2. **Groups into units.** An epic and its children are one unit, because
   classifying eleven children of one Forms epic in three separate batches
   spreads one deliverable across three cycles for no reason. The epic, not the
   card, is the atom of cycle assignment.
3. **Finds the collisions.** Two cards citing the same file become an explicit
   order between those two cards, reported with the file that caused it.
4. **Sequences.** Urgent, then High, then the last 14 days newest first —
   subject to those constraints, deterministically. The rules are below.
5. **Assigns cycles**, using Linear's own primitive.
6. **Reads once, ranked.** One model call over the whole population and the
   context pack — what is in flight, what merged, what closed — asking the one
   question the cards cannot answer between them: given what we are already
   doing, does this card belong in the next batch? The read fills the batch; it
   never breaks it, because every rule above still constrains the order.
   `--no-judgement` makes no call at all and is exactly the groomer that ran
   before DRE-3150.
7. **Proposes**, in plain English: the batch and its order with the **why** on
   every row, what is deferred and what brings each one back, what is
   recommended dead and on whose word, what could not be ranked at all, and
   which repos are waiting and roughly how long.

## The order, applied top to bottom

CEO decision, 2026-09-04: **14 days, creation date** (DRE-3096). The batch is
this week's work, not the oldest work.

1. **Urgent first, every repo.** Linear priority `Urgent` opens the batch,
   newest created first among them. This is the production-issue lane: a card
   raised while debugging goes ahead of everything.
2. **High next**, newest first. Otherwise High means nothing.
3. **Then the window**: created in the last 14 days, newest first.
   `WINDOW_DAYS` is a constant and `--window-days` is a flag, so the drain of
   the old Backlog runs at 14 and the steady state widens to 30 without a code
   change.
4. **Repo order is a tie-break inside a band, never the master key.** Portico
   first only among cards of equal priority created on the same day. It used to
   be the first element of the key, which put months-old Portico work at the
   head of a 200-card Intake and made a card raised Urgent this morning wait its
   turn.
5. **Older than the window is "not now" by default.** Those cards stay in
   Intake, ungroomed, and the proposal reports them as one line: *"N cards older
   than 14 days, not batched — raise a card's priority to High or Urgent to pull
   it in."* They are not aged out, not cancelled and not moved, and the
   operator's Intake hold (DRE-3035) is untouched by any of this.
6. **Two things still pull an old card forward**, whatever its age: a file
   collision with a batched card — the old card is ordered *before* it — and
   being a Linear blocker of a batched card. Both are constraints the sequence
   already carries, and the pull is transitive.
7. **The date is the creation date**, never the last update. A stray agent
   comment must not bump a card up the batch; the population query does not even
   read `updatedAt`. The way to resurrect an old card is to raise its
   priority — rules 1 and 2 — which is a deliberate human act.

The epic is still the unit, so an epic's band is the highest priority and the
newest creation among the epic and its children: one Urgent child pulls the
whole unit into the batch. Inside a unit the order is unchanged — oldest child
first, with collisions and blocks relations on top.

## The outcomes, and what each one owes the reader

Since DRE-3150 the groomer makes ONE ranked read of the whole population
(`groom_judgement.py`) alongside the rules, so every outcome carries the words
that put a card there. DRE-3152 puts those words in the proposal itself: one
line per card, before anything moves.

| Outcome | Means | What it must name |
| -- | -- | -- |
| `now` | **In the approved batch**. It carries a cycle and a position in it, and it is the only outcome that moves a card. | a **reason** — the read's own line, or the rule that placed it ("Urgent", "created inside the window"). |
| `not-now` | **Wanted, and deliberately not this batch**. Either it names the cycle it is reconsidered in, or it is older than the window and stays in Intake ungroomed. This is "later", and it is not "no". | a **trigger** — what brings it back. Cards sharing one are grouped under it, with the count. |
| `dead` (the read calls it `likely-done`) | **Recommended for cancellation, and never cancelled here**. Two readers propose one and the proposal says which: a `Superseded by:` line a person wrote on the card, or the ranked read's judgement. | **evidence** — the card, merged PR or decision it points at. A recommendation nobody can check is one nobody should act on. |
| `could not rank` | **The read could not place it.** The rules kept it exactly where they had it. | **itself** — its own section in the proposal, never folded into "not now". |

`not-now` is first-class on purpose. A card can be well-formed, wanted, and
correctly left alone for a month — without a "later", Intake is a pass/fail
funnel and the only way to say "later" is to say "no". `could not rank` is
first-class for the same reason from the other end: a refusal that renders as
a deferral is a refusal nobody reads, and refusal is not a default.

A reason written in technical terms never reaches the page. It goes through
the same plain-English guard the planner's escalation goes through, is replaced
with one sentence saying so, and the count of withheld reasons is printed in
the proposal's receipt line.

A dead recommendation always names what replaced it, and the proposal keeps the
two sources apart — **declared on the card** (a person wrote the line) and
**judged by the ranked read** (a call, with what it points at) — so the CEO
knows which of the two is being read before deciding. A `Superseded by:` line
that names nothing checkable is reported as a gap and the card is sequenced
normally: a recommendation nobody can check is one nobody should act on. The
groomer never cancels — in the 2026-08-22 sweep the recommendation, the decision
and the execution were three separate steps, and the executing agent caught an
error in its own brief precisely because it was working from an explicit list
rather than its own judgement.

## The approval gate

```
python3 scripts/groomer.py propose --lane Intake --capacity 20 --post DRE-2683
```

`propose` writes nothing but a comment carrying the proposal, and it writes that
one at most once: before posting it reads the card and skips a proposal already
there, so re-running it after a crash or a transient failure leaves the thread as
it was rather than adding a duplicate copy. A population that MOVED has a
different id and does post — the retry is silent, the groomer is not.

To approve, the CEO comments on that card, with the marker opening the comment:

```
🧺 groom-approved: <proposal id>
```

Then:

```
python3 scripts/groomer.py drain --card DRE-2683 --lane Intake --capacity 20
```

`drain` re-derives the proposal from live state and acts only if the approval
names the batch it just derived. The proposal id is a digest of the batch's own
contents, so an approval binds to a batch the way a critic verdict binds to a
head sha: once the population moves, the id changes and the old approval stops
authorising anything. Pass `drain` the same shaping flags the proposal was built
with, or it will derive a different batch and refuse.

Five refusals, all of them before any write:

- **the pen is held** — `intake_hold` is set on the stub, so the operator has
  said "not this week" about the whole lane, and that answer outranks any
  batch's approval. The refusal prints the date the pen was closed and how big
  the batch was (DRE-3035, `docs/backlog-cutover.md`);
- **no approval** — nothing leaves Intake;
- **an approval written by the pipeline's own Linear identity** — the proposer
  cannot approve its own proposal;
- **an approval naming another batch** — the population moved;
- **a terminal destination** — the drain moves cards to `Planning` and refuses
  `Canceled`, `Duplicate` and `Done` outright.

`propose` is deliberately not held: it writes nothing but a comment, and a held
pen still wants a batch prepared for the day it opens.

An approved batch is moved to `Planning`, which is where the classification
happens (DRE-2719), and each card is assigned its cycle.

## The cadence, and its stated cost

**On demand — a manual `workflow_dispatch`, never a schedule** (decision D5,
approved 2026-08-23, until the groomer's judgement has been audited). A groomer
running unattended over two hundred cards before anyone has checked its calls is
the same mistake as trusting a critic's verdicts before comparing them to a
held-back set.

The cost is stated rather than hidden: on demand means it runs when someone
remembers, and this programme's whole thesis is that anything relying on
remembering eventually does not happen. Revisit the cadence once the calls have
been checked against a real batch — the first one is written up in
[groomer-first-batch.md](groomer-first-batch.md).

**The audit D5 was waiting for is DRE-3151.** It runs the two readings —
`--no-judgement` and the ranked read — over one population and compares them
card by card, which is the check that decides whether this ever runs on a
schedule. Until it has, the cadence stays manual: a groomer ranking two hundred
cards unattended before anyone has read its calls is the same mistake as
trusting a critic's verdicts before comparing them to a held-back set.

## On cycles

Assigning cards to cycles is not a return to sprint planning. The cycle is the OKR heartbeat — a reporting rhythm, not a capacity commitment — and it still reports what moved. What the groomer needs from it is a native container for an ORDER, which Linear already has and nobody has to build.

`--capacity` is how much is proposed at a time, not a velocity estimate. A unit
larger than the capacity gets a cycle to itself rather than being cut in half.

A card older than the window is given no cycle at all. "Not now" for those means
ungroomed and still in Intake — inventing a cycle for one would say the groomer
had made a plan for a card it deliberately did not look at.

## What it cannot see

Collision detection reads the files a card *names*, in backticks, and compares
them by basename within ONE repo — `package.json` in portico and `package.json`
in agent-bureau are different files that can never conflict. Three limits come
out of that, and all three are printed in every proposal rather than left
implicit:

- **a card that names no files** is listed as unreadable — five of the eight
  collisions the Forms review (DRE-2649) found are invisible for exactly this
  reason;
- **a path cited by more than twelve cards** is read as reference, not
  ownership. Nineteen live cards carry a branch-rule banner naming
  `linear-sync.yml`; none of them edits it. The threshold is tuned against the
  live population — at 5 it discarded a file the Forms review named as a real
  collision;
- **constraints that point both ways** (a collision says A first, a relation
  says B first) cannot both be honoured. The ranked order wins and every
  dropped constraint is reported, because two cards that each have to go first
  is a planning question, not an ordering one.

The ranked read has its own stated limits, and they are on the page too:

- **it reads a census, not the cards.** Each card travels as its id, title,
  labels, priority, age in days and the first two lines of its description —
  not bodies, not comments, not linked documents. The whole population goes in
  ONE prompt, and what decides a batch is what a card is FOR, which is on its
  first lines or is not written down anywhere;
- **the receipt line says what it ran against** — the model that answered, the
  number of cards, and the counts of epics in flight, merged PRs and closed
  cards in the context pack. A pack section that was capped is named in the
  proposal JSON;
- **a cut answer is said out loud.** The one call is sized off the census, and
  when the answer comes back at that ceiling the receipt line says so, names
  the budget, and counts the cards the cut cost — they appear under "Could not
  rank — needs a person" rather than looking like cards the model declined to
  rank (DRE-3259).

The second critic's cross-epic sight (DRE-2721, D3) catches what the groomer
missed. That is a backstop, not a duplicate: the groomer prevents the collision
when the batch is formed, the critic catches the ones it could not see. If the
critic never finds one the groomer missed, this check can narrow.
