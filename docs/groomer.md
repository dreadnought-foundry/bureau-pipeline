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
4. **Sequences.** Portico first — the business priority — subject to those
   constraints, deterministically.
5. **Assigns cycles**, using Linear's own primitive.
6. **Proposes**, in plain English: the batch and its order, what is deferred
   and to when, what is recommended dead and what replaced it, and which repos
   are waiting and roughly how long.

## The three outcomes

| Outcome | Means |
| -- | -- |
| `now` | **In the approved batch**. It carries a cycle and a position in it, and it is the only outcome that moves a card. |
| `not-now` | **Wanted, and deliberately not this batch**. It names the cycle it is reconsidered in. This is "later", and it is not "no". |
| `dead` | **Recommended for cancellation, and never cancelled here**. It names the card or merged PR that superseded it; the operator decides and the operator executes. |

`not-now` is first-class on purpose. A card can be well-formed, wanted, and
correctly left alone for a month — without a "later", Intake is a pass/fail
funnel and the only way to say "later" is to say "no".

A `dead` recommendation always names what replaced it. A `Superseded by:` line
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

Four refusals, all of them before any write:

- **no approval** — nothing leaves Intake;
- **an approval written by the pipeline's own Linear identity** — the proposer
  cannot approve its own proposal;
- **an approval naming another batch** — the population moved;
- **a terminal destination** — the drain moves cards to `Planning` and refuses
  `Canceled`, `Duplicate` and `Done` outright.

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

## On cycles

Assigning cards to cycles is not a return to sprint planning. The cycle is the OKR heartbeat — a reporting rhythm, not a capacity commitment — and it still reports what moved. What the groomer needs from it is a native container for an ORDER, which Linear already has and nobody has to build.

`--capacity` is how much is proposed at a time, not a velocity estimate. A unit
larger than the capacity gets a cycle to itself rather than being cut in half.

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

The second critic's cross-epic sight (DRE-2721, D3) catches what the groomer
missed. That is a backstop, not a duplicate: the groomer prevents the collision
when the batch is formed, the critic catches the ones it could not see. If the
critic never finds one the groomer missed, this check can narrow.
