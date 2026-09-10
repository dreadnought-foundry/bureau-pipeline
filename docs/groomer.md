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

## The one ranked read

Between steps 1 and 4 the run makes **one model call** over the whole census
(`scripts/groom_judgement.py`, DRE-3150). The rules above are all facts about
the *cards*; the call asks the one question they cannot answer — given what we
are already doing, does this card belong in the next batch — and it asks it
once, for the whole population, because a per-card read structurally cannot see
the set.

The switch is the workflow's `judgement` input, `on` by default. Anything else
passes `--no-judgement` and sequences by the rules alone, which is the
pre-DRE-3150 groomer byte-for-byte — so the two readings can be compared on one
population.

Since DRE-3153 the workflow that runs the groomer actually holds the
credential: `groomer.yml` declares `ANTHROPIC_API_KEY` and
`CLAUDE_CODE_OAUTH_TOKEN` and hands the Groom step whichever one
`CLAUDE_AUTH_MODE` selects, exactly the way `plan.yml`'s classify step does. A
reusable workflow sees only the secrets it declares, so `secrets: inherit` on
the stub was never enough on its own. The `drain` branch is handed neither: it
reads an approval and moves cards, and it does not judge.

After a judged `propose` that had a card, the run posts one line to that card —
the same `🧠 model-attempt:` marker the planning classifier posts, naming the
model that answered, the call count, the output budget the call was sized with
and whether the answer was cut short. It is composed by
`scripts/groomer_receipt.py` over `proposal.json` and printed into the step
summary as well.

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

## The decision vocabulary (DRE-3370)

One marker was a yes/no question, and a batch is not one. A CEO reading a
twenty-card proposal wants to say *yes, except these two* and *yes, and this one
as well* — and the only way to say either used to be to re-run the groomer,
spend another ranked read (~$6, ~8 minutes) and ask for a second approval of a
batch that was right apart from two rows.

**This table is the contract the console's reader and writers mirror.**

| Marker | Written by | Shape |
| -- | -- | -- |
| `🧺 groom-proposal: <id>` | propose | unchanged |
| `🧺 groom-approved: <id>` | the CEO (console or by hand) | unchanged |
| `🧺 groom-declined: <id> — <reason>` | the CEO | reason required |
| `🧺 groom-excluded: <id> DRE-N[ — <reason>]` | the CEO | per card |
| `🧺 groom-added: <id> DRE-N[ — <reason>]` | the CEO | per card |
| `🧺 groom-drained: <id>` + `moved: n · held back: n · added: n · refused: n → Planning at <time PT>` + table | the drain | one per drain |
| `🧺 groom-drain-refused: <id> — <reason>` | the drain | one per refusal |
| `🧺 groom-hold-repo: <slug>` | the CEO (console or by hand) | not bound to a proposal id; newest marker per slug wins; pipeline-authored ignored |
| `🧺 groom-release-repo: <slug>` | the CEO (console or by hand) | same |

`<id>` is the proposal id the approval names, and a decision naming any other
batch says nothing about the approved one.

The last two are the exception that proves the shape: they name a repo **slug**
(`[a-z0-9][a-z0-9-]*`) and no id at all, because a hold outlives the proposal it
was written on. Everything else about them is the same — anchored at the start
of the comment, the emoji optional, ignored when the pipeline wrote it.

Every marker is matched the way the approval has always been matched — **anchored
at the start of the comment**, the emoji optional, the id `[0-9a-f]{6,}` — so a
sentence *about* excluding a card is prose and not an exclusion. The em dash
before a reason is the em dash: `—`, not `-`.

**Authorship is the gate.** A decision marker written by the pipeline's own
Linear identity is ignored and named in the record, exactly as a self-written
approval is refused. A marker the proposer can write is a credential the
proposer can mint, so it decides nothing (DRE-2721).

What each one does:

- **declined** — the batch is not drained, and the refusal quotes the reason.
  The reason is required: a decline without one leaves the CEO having stopped a
  batch and nobody able to fix it, so it is read as absent and reported in the
  record instead. **The next proposal opens by answering it** — see below.
- **excluded** — the card stays in Intake and the record lists it as `held
  back`. An exclusion naming a card that is not in the batch holds nothing back
  and is reported.
- **added** — the card moves *after* the batch, on the batch's own first cycle.
  It must be in the lane already: an addition naming a card that has left the
  lane refuses the whole drain and names the lane it is in now.
- A card named by both **excluded** and **added** follows the **newest
  comment** — the same "last word wins" the approval has always used, which is
  also how an approval that follows a decline still approves.

The newest decision wins, so the CEO can change their mind by commenting again
rather than by re-running anything.

Then:

```
python3 scripts/groomer.py drain --card DRE-2683
```

`drain` **reads the approved batch off the proposal comment on that card** and
moves exactly those cards — **minus every exclusion, plus every addition** — in
that order (DRE-3338, DRE-3370). The approval names a proposal id; the proposal
comment carrying that id is the record; the rows of its batch table are the
batch. It makes **no model call** and never re-reads the population — a drain is
a move, not a judgement — so it takes no shaping flags at all beyond `--lane`,
which is only the fallback for a record whose own lane line cannot be read.

It reads the **whole thread, paginated** — not the newest fifty. One comment per
per-card decision means a proposal card outgrows the fifty-comment window the
moment a CEO excludes a handful of rows, and the approval, posted first, is the
first thing to fall out of it. A drain reading the window would refuse a batch it
was looking at.

**Why the comment and not the run artifact.** `proposal.json` is uploaded as the
`groom-proposal` artifact and carries the same batch, but the comment is the
thing the CEO actually read before approving, and the approval is a reply to it
in the same thread — the record and the consent to it are one object, read with
one credential. The artifact expires after 30 days, needs a second credential
into GitHub Actions, and finding the right run means searching runs for the id,
which is a re-derivation of a different kind.

**What this replaced, and why.** The drain used to rebuild the proposal from
live state and refuse when the id it re-derived differed from the approved one.
That is a real safety property — *the batch on the page is not the batch the
drain would move* — said the wrong way round. It made the drain fail on two
ordinary events: a card entering Intake between propose and drain (DRE-3337 was
filed five minutes after proposal `f673bfefa340` was read), and the model
answering the same census slightly differently, which a 260-card judgement does.
Each failure cost another model call (~$6, ~8 minutes) and another CEO approval,
and threw the CEO's existing approval away for a reason that had nothing to do
with the batch. The property is kept where it belongs: the drain moves the list
the CEO saw, and refuses outright if any card on that list has moved since.

Nine refusals, all of them before any card moves — and every one of them but the
last is **written onto the proposal card** as
`🧺 groom-drain-refused: <id> — <reason>`, then exits 2. A drain that refuses a
batch and says so only in a workflow log is a stall with an alibi.

- **the pen is held** — `intake_hold` is set on the stub, so the operator has
  said "not this week" about the whole lane, and that answer outranks any
  batch's approval. The refusal prints the date the pen was closed and how big
  the approved batch behind it was (DRE-3035, `docs/backlog-cutover.md`);
- **no approval** — nothing leaves Intake;
- **an approval written by the pipeline's own Linear identity** — the proposer
  cannot approve its own proposal;
- **the newest decision is a decline** — `🧺 groom-declined: <id> — <reason>`,
  and the refusal quotes the reason;
- **an approval naming a batch with no record** — the id it names is on no
  proposal comment on that card, so there is no written-down list to move and
  the drain will not reconstruct one;
- **the batch has already been drained** — a `🧺 groom-drained: <id>` record
  stands on the card, and that record is the receipt for cards that have
  already moved, so a second dispatch moves nothing;
- **a card the drain would move is no longer in the lane** — somebody moved it
  by hand since the approval, or an addition names a card that has already
  left. The refusal names the card and the lane it is in now, and the WHOLE
  batch stays put: an approved order half-executed is an order nobody gave;
- **a cycle Linear does not carry** — the record names a cycle number with no
  open cycle behind it. Create the cycle; the groomer will not invent one. (A
  cycle that has since STARTED is fine: the drain resolves the number the CEO
  approved, unlike `propose`, which will not schedule into a period half over.)
- **a terminal destination** — the drain moves cards to `Planning` and refuses
  `Canceled`, `Duplicate` and `Done` outright.

`propose` is deliberately not held: it writes nothing but a comment, and a held
pen still wants a batch prepared for the day it opens.

An approved batch is moved to `Planning`, which is where the classification
happens (DRE-2719), and each card is assigned its cycle.

### What the drain wrote down

Afterwards the drain posts ONE comment to the proposal card (DRE-3326,
DRE-3370). Undoing a bad batch means knowing which cards *this* drain took, and
a run log is not on the card.

```
🧺 groom-drained: <proposal id>

moved: 3 · held back: 2 · added: 1 · refused: 0 → Planning at 2026-09-09 16:21 PT

| # | Card | Outcome | Why |
| -- | -- | -- | -- |
| 1 | DRE-3301 | moved | proposal `f673bfefa340` position 1 |
| — | DRE-3302 | held back | `🧺 groom-excluded` — design is not settled |
| 2 | DRE-3303 | moved | proposal `f673bfefa340` position 3 |
| 3 | DRE-3350 | added | `🧺 groom-added` — Ana needs it this week |
```

The summary line's grammar is fixed, and its four counts answer four different
questions: **moved** is the approved batch that went, **held back** is what the
CEO excluded and is still in Intake, **added** is what the CEO reached into the
lane for, and **refused** counts the *decisions* the drain would not honour — a
marker the pipeline wrote, a decline with no reason, an exclusion naming a card
that was never in the batch. `#` is the order the cards actually moved in; a card
that did not move carries `—`. Each of the four outcomes is one of `moved`,
`held back`, `added`, `refused`, and no card gets two rows: a refused decision
about a card that moved anyway is named on that card's own row.

A refusal is its own record, `🧺 groom-drain-refused: <id> — <reason>`, on one
line, and it is written before the refusal exits 2.

A proposal built with `--batch-cycles 2` or more is not drainable — its batch
table records one position per card and not which of the several cycles each
belongs to, so the drain refuses rather than guessing. Propose one cycle at a
time (the default, and what the workflow passes).

### Switching a repo off (DRE-3403)

Saying "not that repo" and having it happen anyway is a missing switch, not a
communication problem. atlas cards were proposed, approved as part of a batch,
drained, and one was mid-planning before it was pulled back by hand — after the
decision that the Bureau repos are the only ones proposed until the groomer is
solid had been stated three times (CEO decision, 2026-09-08).

```
🧺 groom-hold-repo: atlas          switches atlas off
🧺 groom-release-repo: atlas       switches it back on
```

**What `propose` does.** Every card whose `repo:` label names a held repo is
removed *before* the census the model reads is built, so a held card is never
sent to the model, never ranked, never given a cycle, never counted against
`--capacity`, and never half of a collision pair or a pull-forward. The batch
fills from the other repos instead. The lane's **population and census are
untouched** — the work is still there, it is just not on offer — and the JSON
carries `held_repos: [{"repo": "atlas", "cards": 12}]` beside
`offered: <population minus held>`.

The page gains one section, directly before *On cycles*, and only when
something is held:

```
## Held repos — switched off by you

- held: atlas · 12 cards
```

A slug with no cards in the lane still gets its line (`· 0 cards`): the CEO
switched that repo off, and a hold that rendered nothing would read as a hold
nobody honoured. A proposal with no hold renders exactly as it did before this
existed.

A batched card that Linear says is **blocked by a card in a held repo** stays in
the batch and is named in the collisions section as `blocked by a held repo`.
The hold takes the blocker out of the offer and the ordering constraint goes
with it, but `blockedBy` is the relation the promotion gate reads, so the
dependency gate holds the card later anyway — the groomer names it rather than
silently dropping it.

**What the drain does.** It reads the same markers **at drain time**, so a hold
written after the proposal was posted still holds those cards back: each one is
recorded `held back` with Why `repo held: <slug>` in the `groom-drained` record,
and the rest of the batch moves.

For a dry run with no card to read the markers off, `--hold-repo <slug>`
(repeatable) does exactly what the marker does.

**Not the work-in-progress cap.** That one stops BUILDS after classification.
This stops the cards being proposed at all.

## The next proposal answers your last decline (DRE-3373)

Declining a batch used to be a one-way message. The reason went into the drain's
refusal and nowhere else, so the next `propose` posted a fresh twenty-card page
that read exactly like the one that had just been turned down — with no sign
that anybody had read the objection. Working out whether the thing you
complained about was fixed meant diffing two proposal comments by eye.

Now `propose --post` reads the card first, and when it finds an open decline the
proposal **opens with the answer**, as its first paragraph after the title:

```
# Groom proposal `9c41ab77e0d2` — cycle 12

**Answering your decline of `f673bfefa340`:** two of these cards edit Thread.tsx

Relative to that batch: 1 card in (DRE-3401) and 2 cards out (DRE-3300,
DRE-3302).
```

Two halves, and they come from different places:

- **Your reason, verbatim.** It is quoted back, never acted on. The reason is
  text the pipeline does not authenticate, being rendered into the very kind of
  comment these markers are read from, so a line inside it shaped like a marker
  is rendered with a visible `[defanged] ` prefix and the run's log says how many
  lines it defanged — never what they said (`standards/untrusted-content.md`).
  It gets its own paragraph because verbatim text may end without punctuation:
  running the next sentence onto the end of it would read as one mangled
  sentence in your own words, and inventing a full stop inside quoted text is
  the one thing verbatim forbids.
- **What changed, by id.** **Cards in** and **cards out**, computed from the two
  batch lists: the declined batch read off its own `🧺 groom-proposal:` comment,
  this one off the rows the run just sequenced. **No model is asked what
  changed** — a sentence a model wrote about a difference it did not compute can
  be wrong in the one place you are deciding, and it would be wrong in your own
  words. If the declined proposal has scrolled out of the comments the run read,
  the paragraph says so rather than rendering an empty diff.

A decline is **answered once**. It is skipped when it is older than the newest
`🧺 groom-proposal:` on the card (that proposal already answered it), when the
card carries a `🧺 groom-approved:` for the same batch (you settled it with a
yes), when it was written by the pipeline's own Linear identity, or when it
carries no reason. With nothing to answer the proposal renders **byte for byte**
as it did before this existed, and posting stays idempotent either way — the
paragraph sits below the `🧺 groom-proposal: <id>` marker line, and the id still
digests the batch alone, so answering a decline never retires an approval.

## The cadence, and its stated cost

**On demand, never a schedule** (decision D5, approved 2026-08-23, until the
groomer's judgement has been audited). A groomer running unattended over two
hundred cards before anyone has checked its calls is the same mistake as
trusting a critic's verdicts before comparing them to a held-back set.

Two triggers, both on demand, no cron:

- **`workflow_dispatch`** — a person at Actions. The only route to `propose`,
  and the only one that offers the lane, capacity, priority and `judgement`
  inputs.
- **`repository_dispatch`, type `groom-drain`** — the CEO's Approve on the
  console (DRE-3337, green-lit 2026-09-08), so an approval starts the drain
  without anyone opening Actions. `client_payload` is
  `{"card": "DRE-N", "proposal": "<12-hex id>"}` and only `card` is read. On
  this event `mode` is the literal `drain` whatever the payload carries, and
  `judgement` is `off` — a drain makes no model call, so the judgement D5 wants
  audited is never run by this trigger. It is a `repository_dispatch` rather
  than a `workflow_dispatch` because the dispatching App holds `contents: write`
  and no Actions permission (DRE-3001), which is the same path the relay already
  fires `agent-execute` down.

The cost of "on demand" is stated rather than hidden: for `propose` it still
means it runs when someone remembers, and this programme's whole thesis is that
anything relying on remembering eventually does not happen. Revisit the cadence
once the calls have been checked against a real batch — the first one is written
up in [groomer-first-batch.md](groomer-first-batch.md).

**The audit D5 was waiting for is DRE-3151.** It runs the two readings —
`--no-judgement` and the ranked read — over one population and compares them
card by card, which is the check that decides whether this ever runs on a
schedule. Until it has, nothing here runs on a clock: a groomer ranking two
hundred cards unattended before anyone has read its calls is the same mistake as
trusting a critic's verdicts before comparing them to a held-back set. The
`groom-drain` dispatch above is not an exception to that — it carries no
judgement, and it fires on a person's Approve.

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
- **a context signal nobody could read is UNKNOWN, never a number** (DRE-3329).
  A source that fails — `gh search prs` returning non-zero, say — is named
  unread in the pack, carries no count in the proposal JSON, and reads on the
  page as `UNKNOWN merged PRs (could not be read this run)`. The prompt says
  the same thing to the model, so an unreadable signal never becomes a real
  input with a wrong value. A section that WAS read and held nothing is still
  `0`: "nothing merged" and "we could not ask" are different facts and get
  different renderings (`standards/console-honesty.md` rule 2);
- **a cut answer is said out loud.** The one call is sized off the census, and
  when the answer comes back at that ceiling the receipt line says so, names
  the budget, and counts the cards the cut cost — they appear under "Could not
  rank — needs a person" rather than looking like cards the model declined to
  rank (DRE-3259);
- **a run that answered says how many cards it ranked** (DRE-3331). The first
  proposal a model ever answered read `Ranked by claude-fable-5-1 … in 1 call
  over 260 cards` and put 204 of them under "Could not rank". Nothing in that
  line was false: the model had answered for all 260, Claude Code had carried
  the answer across several API requests when it ran past the output budget, and
  the run had read only the last one — the plain `--output-format json`
  envelope's `result` is the last message's text alone. The transport now reads
  the stream and joins every piece; the receipt line, the proposal JSON and the
  `🧠 model-attempt:` comment all say `N of M cards ranked` with the rest
  accounted for (the model declined, never reached, unreadable, over the
  ceiling); a joined answer says how many pieces it came in; and the raw answer
  is kept as `judgement-answer.txt` in the run artifact, because the run that
  found this kept nothing. The budget also learned that the model's thinking is
  billed against it — 36,402 of 46,640 output tokens on the 263-card lane — so
  it is sized for thinking and text both, and the ceiling is 64,000.

The second critic's cross-epic sight (DRE-2721, D3) catches what the groomer
missed. That is a backstop, not a duplicate: the groomer prevents the collision
when the batch is formed, the critic catches the ones it could not see. If the
critic never finds one the groomer missed, this check can narrow.
