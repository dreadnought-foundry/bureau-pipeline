# The judged groom — the first two batches, read against the live board

Observed by hand in two sittings, both entirely read-only: the first on
**2026-09-10** between 06:46 and 06:54 PT, the second on **2026-09-12** between
21:42 and 22:05 PT. Nothing was moved in either: every number below comes from a
`propose` run that had already happened, from the proposal artifact that run
kept, from the live Linear board, or from the scorer run read-only over a
comment thread. The drain was not run for this record, and no new `propose` run
was dispatched for it — the runs being observed are the ones that were already
there.

`propose` writes one comment and moves nothing. `drain` refuses without a CEO
approval naming the exact batch id.

**What the second sitting added.** The first pass named three criteria it could
not meet, and the first of them was the scorer: `scripts/groomer_score.py`
(DRE-3155) was not on `main`, and DRE-3151's hand audit had not been written. By
2026-09-12 21:46 PT both were in — the scorer is on `main` and DRE-3151 is Done,
carrying the CEO's own call on all 26 rows. The scorer's report is pasted below,
and it overturns one of this record's own numbers: the first pass recorded
`false-done: 0` and the audit says two of the six are false. Every number from
the first sitting was re-derived from the artifacts again in the second before
anything was added to it, and all of them held.

## The three runs this record reads

| Run | Started (PT) | Mode | Judgement | Proposal | Population |
| -- | -- | -- | -- | -- | -- |
| [34249466434](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34249466434) | 2026-09-08 09:10 | propose | **on** | `f673bfefa340` | 260 |
| [34251153128](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34251153128) | 2026-09-08 09:27 | propose | off | `4aca7f52ee0b` | 261 |
| [34392979896](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34392979896) | 2026-09-09 12:05 | propose | **on** | `42ed15f7527d` | 262 |

All three were `workflow_dispatch` on `Groomer` in `bureau-pipeline`, lane
`Intake`, capacity 20, posting to **DRE-3327**. Batch 1 is the judged run of
2026-09-08 and batch 2 the judged run of 2026-09-09; the run between them is
the rules-only counterpart batch 1 is compared against below.

Each judged run posted **two** comments to DRE-3327 — the proposal and the
model receipt. Batch 1 at 2026-09-08 09:17:28 PT, batch 2 at 2026-09-09
12:11:11 PT. Both confirmed in the run logs (`commented on DRE-3327`, twice
each).

## One call, sized, and not cut short

The receipt each run posted, regenerated here by the repo's own reader
(`scripts/groomer_receipt.py`) over that run's kept `proposal.json`:

```
🧠 model-attempt: claude-fable-5-1 (asked) / claude-fable-5-1 (answered) — groomer judgement ranked the census in 1 call — 255 of 260 cards ranked, output budget 57700 tokens
🧠 model-attempt: claude-fable-5-1 (asked) / claude-fable-5-1 (answered) — groomer judgement ranked the census in 1 call — 258 of 262 cards ranked, output budget 58140 tokens
```

Read off the two proposals directly:

| | Batch 1 (`f673bfefa340`) | Batch 2 (`42ed15f7527d`) |
| -- | -- | -- |
| model calls | **1** | **1** |
| continuations | 0 | 0 |
| output budget | 57,700 tokens | 58,140 tokens |
| `truncated` | **false** | **false** |
| population | 260 | 262 |
| ranked | 255 | 258 |
| declined by the model | 5 | 4 |
| garbled | 0 | 0 |
| never reached (omitted) | 0 | 0 |
| dropped at the ceiling | 0 | 0 |

**The unranked count is the number that would betray a cut answer, and it does
not.** A truncated read shows up as a wall of "could not rank": here it is 5 of
260 and 4 of 262, every one of them a card the model *declined* explicitly
rather than one it never reached. `garbled`, `omitted` and `ceiling` are all
zero in both runs, and `truncated` is false in both. The budget was sized off
the census (about 58k output tokens for 260-odd cards) and the answer came back
inside it.

## What each run proposed

| | Batch 1 judged | Rules-only, 10 minutes later | Batch 2 judged |
| -- | -- | -- | -- |
| proposal | `f673bfefa340` | `4aca7f52ee0b` | `42ed15f7527d` |
| population | 260 | 261 | 262 |
| **now** (the batch) | 18 | 20 | 20 |
| **not-now** | 238 | 241 | 238 |
| **likely-done** | 4 | **0** | 4 |
| unranked | 5 | — | 4 |

**The rules alone never recommend anything dead.** Both rules-only runs of
2026-09-08 returned zero. Every `likely-done` in this pipeline is the model's
call, which is why the false-done check below is the audit's expensive half.

The judged runs also propose a *smaller* batch than the rules do — 18 against
20 in batch 1 — because judgement moves cards out of the batch as well as in.

## Judged against rules-only, over one population

Batch 1 (09:17 PT) and the rules-only run (09:27 PT) are ten minutes apart.
Their populations differ by exactly one card: **DRE-3337** entered Intake
between the two runs and is in the rules-only population only. The other 260
cards are common to both, and **26 of them were placed differently**:

**Read the second column as "where the batch put it", not "what the model
said".** On ten of these 26 rows the two differ, because the sequencer places a
card the model said wait on. The model's own answer is the column the scorer
grades, and it is in the report further down.

| Card | Rules said | Judgement said | Judgement's reason |
| -- | -- | -- | -- |
| DRE-2348 | now | not-now | Atlas work with no atlas epic in flight |
| DRE-2371 | not-now | now | A lint gate for the client, no epic depends on it |
| DRE-2373 | now | not-now | Atlas work with no atlas epic in flight |
| DRE-2382 | now | not-now | A migration defect hit once |
| DRE-2415 | now | not-now | Member emails shown to fellow project members; a leak, but among members of the same project |
| DRE-2431 | not-now | now | A grants rule to enforce after the audit |
| DRE-2506 | not-now | now | Partner names published to an unserved branch |
| DRE-2583 | not-now | now | A policy decision on dependency bumps |
| DRE-2598 | not-now | **likely-done** | Fleet identity now reads each repo's account from GitHub and marks the active account unknown, which is the split |
| DRE-2730 | not-now | now | A nightly self-clearing warning |
| DRE-2767 | now | not-now | The backstop pushes at conflicted pull requests forever; the red-after-main-fixed epic touches it |
| DRE-2817 | now | not-now | Fix-budget convergence belongs to the gates wave, not yet opened |
| DRE-2836 | now | not-now | The groomer epic in flight now says which cards are already done at intake |
| DRE-2984 | now | not-now | A recurring red run with green tests; costs a cycle each time but no epic depends on it |
| DRE-3020 | not-now | now | **could not rank — needs a person** |
| DRE-3085 | not-now | **likely-done** | A throwaway probe of the cutover; the card was in Backlog and now sits in Intake, which is the probe passing |
| DRE-3092 | now | **likely-done** | The card says its work was folded into a sibling's pull request on the CEO's instruction |
| DRE-3124 | not-now | now | A known one-line fix, but no portico fix loop is stalling today and the batch is full |
| DRE-3125 | not-now | now | An operator read of two repos, not urgent while no fix loop in them is stalling |
| DRE-3126 | not-now | now | Proof that waits on the one-line concurrency change merging |
| DRE-3127 | not-now | now | A walkthrough that needs the concurrency fix merged and observed first |
| DRE-3183 | not-now | **likely-done** | A throwaway probe of the cutover moving one card, and the cutover itself ran on 2026-09-07 |
| DRE-3237 | now | not-now | The critic dies after a fixed number of turns on one card; the limit-death and ceiling work in flight |
| DRE-3242 | not-now | now | Small piece of the in-flight Linear budget epic, split out only because it lives in another repo |
| DRE-3243 | now | not-now | Critics ignoring a child's state parks sound plans, but the re-approval epic in flight is reshaping it |
| DRE-3319 | now | not-now | The board was fixed by hand; the tool stays broken only for the next lane-layout change |

**This table is the audit's input, not its answer.** Which of the 26 the CEO
would have chosen is his to say, and DRE-3151 is where he says it.

The shape of the disagreement is worth naming even before he does: judgement
moves work **out** of the batch when no epic in flight depends on it (DRE-2348,
DRE-2373, DRE-2382, DRE-2984, DRE-3319) and moves work **in** when an epic in
flight is waiting on it (DRE-3126, DRE-3127, DRE-3242). That is the question
the rules structurally cannot ask, and it is the reason the model call exists.

## Recommended dead — every call checked against the live board

Six distinct recommendations across the two batches. Each was read against the
card's live state on 2026-09-10 and against the card its evidence names.

| Card | Batch | Evidence the groomer named | Live state of that evidence | Verdict |
| -- | -- | -- | -- | -- |
| DRE-3092 | 1 | folded into DRE-3088's pull request on the CEO's instruction | **DRE-3088 Done**; DRE-3092 itself already **Canceled** | **Correct, and already actioned** |
| DRE-2598 | 1 | DRE-3181 Done | **DRE-3181 Done**, and its title is the split DRE-2598 asks for | Evidence holds |
| DRE-2619 | 2 | DRE-2817 Done | **DRE-2817 Done** — "the fix budget counts attempts when it should measure convergence" | Evidence holds |
| DRE-2657 | 2 | DRE-3453 and DRE-3486 describe live harness runs | **DRE-3486 Done**; **DRE-3453 in Triage** — a harness defect card | Thin: the harness demonstrably runs, but one of the two cited cards is an open defect against it |
| DRE-3085 | 1 and 2 | the card sits in Intake / DRE-3320 records the 2026-09-07 cutover | **DRE-3320 still in Intake** — a follow-up card, not a run receipt | Self-referential: the card's own position is offered as the proof |
| DRE-3183 | 1 and 2 | DRE-3320 records the 2026-09-07 cutover | as above | Self-referential, same shape |

What the first sitting concluded from that table was **false-done: 0** — no
recommendation was contradicted by the board, three rested on evidence that was
thin or circular, and nothing looked like a false done in the expensive sense.

**That number was wrong, and the hand audit is what corrects it.** Checking a
`likely-done` against the board answers *is the evidence it named real* —
DRE-3181 is Done, DRE-3486 is Done, DRE-3088 is Done. It does not answer *is the
card actually finished*, and only a person reading the work can. On DRE-3151 the
CEO read all six and contradicted two:

| Card | The groomer said | The audit says | Why the board could not see it |
| -- | -- | -- | -- |
| DRE-2598 | likely-done, on DRE-3181 | **false done — not done** | DRE-3181 reads which account each repo is on; it does not split the writer, which is what DRE-2598 asks for. The cited card is Done and the ask is still open. |
| DRE-2657 | likely-done, on DRE-3453 and DRE-3486 | **false done — half done** | The credential half is answered (the harness review job ran green on 2026-09-12). The half it owes — whether the harness joins the credential fan-out or stays outside with a named owner — is undecided, and its own token still overrides the org's. |
| DRE-3092 | likely-done | holds | Folded into DRE-3088, which is Done. The board had already Canceled it. |
| DRE-2619 | likely-done, on DRE-2817 | holds | DRE-2817 is the convergence fix and it is Done. |
| DRE-3085 | likely-done, circular | holds — **obsolete, cancel** | Right call, wrong evidence: the real proof is DRE-3013 and DRE-2840 being Done, not the card's own position in Intake. |
| DRE-3183 | likely-done, circular | holds — **obsolete, cancel** | As DRE-3085. |

**So false-done is 2 of 6, not 0.** Read against the live board on 2026-09-12,
DRE-2598 and DRE-2657 are both still in `Intake` and both still open; DRE-3085,
DRE-3092 and DRE-3183 are `Canceled`; DRE-2619 is still in `Intake`. DRE-3453 —
the one cited card the first sitting found in `Triage` — is **Done** as of this
sitting, so that row's thinness has resolved itself and the card it was evidence
for is a false done anyway.

The lesson the two sittings teach together is the one the scorer is built
around: **the board can check a groomer's evidence and only a person can check
its conclusion.** A record that stops at the board reports zero false dones
every time.

The groomer never cancels, so nothing was lost by either miss. What the two
cost is two real pieces of work left unscheduled with nobody looking for them,
which is exactly the expensive miss the scorer counts on its own line.

The groomer's own guard held in both batches. Batch 2's comment:

> Named nothing: DRE-2402, DRE-2428, DRE-2432, DRE-2629, DRE-2709 say they are
> superseded without naming what replaced them, so they are sequenced normally
> rather than recommended dead.

## Not now — is the trigger an event, or a date nobody watches

**Every `not-now` names a trigger: 238 of 238 in batch 1, 238 of 238 in batch
2.** None was left blank. Batch 1 grouped its 238 under 133 distinct triggers,
batch 2 under 118.

Most are real events someone would notice — "operator pushes the built branch",
"DRE-3179 lands", "the auto-switch epic closes", "the web back-office epic is
approved". Each names a thing that happens in a place we already watch.

Two shapes are weaker, and both are date-shaped triggers dressed as
conditions:

- **"when cycle N opens"** — 28 cards in batch 1, 16 in batch 2. A cycle number
  is a date with extra steps: nothing announces a cycle opening.
- **"seven clean console days after 2026-09-07"** and its variants — 7 cards in
  batch 1, 8 in batch 2. Nothing on the board counts clean days, and no alarm
  fires on day seven. Somebody has to remember. Batch 2 also carries one bare
  date, `2026-09-14`, on a single card.

That is **35 of 238 in batch 1 and 25 of 238 in batch 2** — roughly one in
eight — whose return depends on a person remembering. The other seven-eighths
name something that will happen visibly, in a place we already watch.

## Could not rank — and the one card that was ranked anyway

This is the finding of the audit.

**Batch 2 is clean.** Four cards the model declined — DRE-2366, DRE-2402,
DRE-2432, DRE-2674 — all four stayed in `not-now`, none was quietly ranked, and
the comment printed all four by name under "Could not rank — needs a person".

**Batch 1 is not.** Five cards were declined, and the comment says of them:

> 5 cards the read could not place. They stayed exactly where the rules had
> them, and each one wants a human answer rather than another pass.

One of the five, **DRE-3020**, did not stay where the rules had it. It is in
the proposed batch — `outcomes.now`, sequence position 32, cycle 13 — and the
batch table shows it to the CEO with its reason printed as the literal string
**"could not rank — needs a person"**:

```
| 32 | DRE-3020 | — | agent-bureau-demo | — | PROOF-FD-6 — a card that is a business decision, not work: … | could not rank — needs a person |
```

The same comment then lists it under "Could not rank — needs a person". **A
drain of batch 1 would have moved a card the model explicitly refused to
place**, on the strength of a reason that says nobody could place it.

The rules-only run ten minutes later put DRE-3020 at `not-now`. That is
suggestive but not proof: its population held one extra card (DRE-3337), so the
sequence is not strictly comparable, and the judged proposal does not record
the pre-judgement rules ordering anywhere, so the comparison cannot be settled
from inside one run.

What is settled, from batch 1's own comment and its own JSON, is the
contradiction: a card is simultaneously in the batch and in the list of cards
that could not be placed. In batch 2 the same four-card list behaved exactly as
promised, so this is not a permanent defect — but it is not a display bug
either. The card was in `now`.

### Batch 2 was clean by position, not by a guard

The second sitting read the code rather than the two outcomes, because "batch 2
is clean" is only reassuring if something made it clean.

`scripts/groomer.py:1013` keeps a unit whose only model outcome is `unranked`
whenever the unit is not in `BAND_OLDER` — that is, whenever the newest card in
it was created inside `WINDOW_DAYS` (14). A declined card is therefore sequenced
by the rules exactly as before, batch included, and nothing downstream asks
again whether the model refused it.

Read off the two kept proposals:

| Card | Batch | Band | Sequence position | Cycle | In the batch |
| -- | -- | -- | -- | -- | -- |
| DRE-3020 | 1 | 2 — inside the window | 32 | 13 | **yes** |
| DRE-2366 | 2 | 3 — older | 64 | 14 | no |
| DRE-2402 | 2 | 3 — older | 116 | 17 | no |
| DRE-2432 | 2 | 3 — older | 145 | 19 | no |
| DRE-2674 | 2 | 1 — High priority | 177 | 20 | no |

Three of batch 2's four were excluded by the band rule. The fourth, DRE-2674, was
**not** — it is High priority, so the rule kept it as a candidate exactly as it
kept DRE-3020 — and it stayed out only because its unit sequenced at position
177, far past a 20-card capacity. **Batch 2 was one sequence position away from
the same defect**, which is the difference between a guard and a coincidence.

**Not fixed here, and owned elsewhere.** This record does not change the
groomer; it reports what the two batches did. **DRE-3544** is the card that owns
the fix, and as of 2026-09-12 it is still in `Intake` with the code path above
unchanged on `main`.

## The scorer's report, over DRE-3151's audit table

The hand audit DRE-3151 was filed for is in: the CEO's own call on all 26 rows
where judgement and rules disagreed, accepted on his word at 2026-09-12 21:46
PT, and the card is `Done`. `scripts/groomer_score.py` (DRE-3155) is on `main`.
So the report the first sitting owed can be produced by the tool meant to
produce it.

Run here read-only, in this repo, over the live comment thread and batch 1's own
kept artifact — the scorer fetches nothing, the thread arrives on stdin and the
proposal as a file:

```
python3 scripts/linear_ops.py dump-comments DRE-3151 > thread.json
python3 scripts/groomer_score.py score --card DRE-3151 \
    --proposal proposal.json < thread.json
```

`groomer_score.py check` passes first — 5 dimensions, 2 scored, 0 problems — so
the exclusion of `constraints` is still the live one and not a stale note.

The report, verbatim:

```
# The groomer's judgement, scored against DRE-3151

The held-out answer is the CEO's own, written before he read the model's reasons. Agreement and disagreement are both below, empty or not.

- agreement: 15 of 25 scored row(s) (60.0%)
- false-done: 1 (the audit declared 1) — the judgement called a card done and the CEO says it is live work, the expensive miss, counted on its own line rather than folded into the rate
- unranked: 1 (the audit declared 1) — counted, never scored
- triggers on the not-now rows: 11 name an event a person will notice, 0 name a date nobody watches, 9 name nothing at all
- unanswered: 0 row(s) the CEO left blank or `unsure`, out of the rate

## Agreement

The judgement put the card where the CEO would have put it.

| Card | Rules said | Judgement said | CEO said | Why |
| --- | --- | --- | --- | --- |
| DRE-2348 | now | not-now | not-now | the judgement and the CEO reached the same answer |
| DRE-2371 | not-now | not-now | not-now | the judgement and the CEO reached the same answer |
| DRE-2373 | now | not-now | not-now | the judgement and the CEO reached the same answer |
| DRE-2431 | not-now | not-now | not-now | the judgement and the CEO reached the same answer |
| DRE-2506 | not-now | not-now | not-now | the judgement and the CEO reached the same answer |
| DRE-2583 | not-now | not-now | not-now | the judgement and the CEO reached the same answer |
| DRE-2730 | not-now | not-now | not-now | the judgement and the CEO reached the same answer |
| DRE-2984 | now | not-now | not-now | the judgement and the CEO reached the same answer |
| DRE-3085 | not-now | likely-done | likely-done | the judgement and the CEO reached the same answer |
| DRE-3092 | now | likely-done | likely-done | the judgement and the CEO reached the same answer |
| DRE-3125 | not-now | not-now | not-now | the judgement and the CEO reached the same answer |
| DRE-3126 | not-now | not-now | not-now | the judgement and the CEO reached the same answer |
| DRE-3127 | not-now | not-now | not-now | the judgement and the CEO reached the same answer |
| DRE-3183 | not-now | likely-done | likely-done | the judgement and the CEO reached the same answer |
| DRE-3242 | not-now | now | now | the judgement and the CEO reached the same answer |

## Disagreement

The judgement said one thing and the CEO would have said another. Both halves are printed, empty or not; neither side is assumed right.

| Card | Rules said | Judgement said | CEO said | Why |
| --- | --- | --- | --- | --- |
| DRE-2382 | now | not-now | now | the judgement said `not-now` and the CEO would have said `now` |
| DRE-2415 | now | not-now | now | the judgement said `not-now` and the CEO would have said `now` |
| DRE-2598 | not-now | likely-done | not-now | the judgement said `likely-done` and the CEO would have said `not-now` |
| DRE-2767 | now | not-now | now | the judgement said `not-now` and the CEO would have said `now` |
| DRE-2817 | now | not-now | now | the judgement said `not-now` and the CEO would have said `now` |
| DRE-2836 | now | not-now | now | the judgement said `not-now` and the CEO would have said `now` |
| DRE-3124 | not-now | not-now | likely-done | the judgement said `not-now` and the CEO would have said `likely-done` |
| DRE-3237 | now | not-now | now | the judgement said `not-now` and the CEO would have said `now` |
| DRE-3243 | now | not-now | now | the judgement said `not-now` and the CEO would have said `now` |
| DRE-3319 | now | not-now | now | the judgement said `not-now` and the CEO would have said `now` |

## Unknown

The CEO column is blank or `unsure`. Nobody answered these, so they are named and left out of the rate — the absence of an answer is not an agreement.

*(none)*

## Unranked

The judgement declined to rank these. A refusal is the designed answer, not a miss, so they are counted here and never scored.

| Card | Rules said | Judgement said | CEO said | Why |
| --- | --- | --- | --- | --- |
| DRE-3020 | not-now | unranked | likely-done | the judgement declined to rank this card — a refusal is the designed answer, so it is counted and never scored |

## Excluded as contaminated

`constraints` — groomer.sequence applies the constraints AFTER the model's order and the groomer's own tests hold them there, so every row this dimension could produce is the code's answer rather than the judgement's. The rules constrain the read; they do not re-rank it.

Enforced in code by `collision_report`, `blockers_of`, `units`, `sequence`, `cycle_plan`, and checked by `groomer_score.py check` on every run. Scoring it would grade the code rather than the judgement, so it is named and never counted.
```

### Reading it

**60.0%, on 25 scored rows of 26.** DRE-3020 is the 26th: the model declined it,
the scorer counts a refusal and never scores it, and the CEO's own call on it
(`likely-done` — a throwaway probe for a card that closed on 2026-09-04) is
recorded without moving the rate.

**The table it scores is the model's own call, not the batch's placement.** The
audit carries one correction the first sitting had wrong: this record's
side-by-side table above reads the column *where the batch placed each card*,
and on ten of the 26 rows that is not what the model said. On DRE-2371, 2431,
2506, 2583, 2730, 3124, 3125, 3126 and 3127 the model said `not-now` — the same
as the rules — and on DRE-3020 it declined. The scorer grades the model's answer
(`judgement-answer.txt` in run 34249466434), which is the only column a model
can be held to.

**false-done: 1, and the audit declares 1.** The scorer's number and the audit's
agree, and both are lower than the 2 in the section above, for a reason worth
naming: DRE-2657 is a batch 2 row and the scored table is batch 1's. Across both
batches the count is 2 of 6.

**"9 name nothing at all" is the same inversion, from the scorer's side.** A
card placed `now` carries no trigger, so the scorer finds none — and those nine
are exactly the cards the model said wait on that the batch placed as `now`
anyway. The line reads like a completeness gap and is in fact the finding below.

### The three rates, side by side

The audit computed all three over the same 26 rows, and only the middle one is
the judgement's:

| Whose call | Agreed with the CEO |
| -- | -- |
| The model's own answer | **15 of 25 scored** (60.0%) |
| The batch as it was placed | 7 of 26 |
| The rules alone | 17 of 26 |

On the 16 rows where the model genuinely disagreed with the rules, the CEO sided
with the model on 7 and the rules on 9. One bias the audit names against itself:
four of the rules' wins shipped the same day, partly because the CEO approved
the rules batch that morning, so "it shipped" favours the rules a little.

## The batch is mostly not the model's picks

This is the finding the numbers above force, and it is the reason the batch scores
7 of 26 while the model scores 15 of 25: **the batch is not what the model chose.**

Counted off each run's kept `judgement-answer.txt` against its own
`outcomes.now`:

| | Batch 1 | Batch 2 |
| -- | -- | -- |
| cards the model called `now` | 27 | 19 |
| of those, reached the batch | **4** | **7** |
| batch size | 18 | 20 |
| in the batch, the model said wait | 13 | 13 |
| in the batch, the model declined | 1 | 0 |

**Fourteen of batch 1's eighteen are cards the model did not pick.** And the
picks it did make are not near the front: of the 27, four are in the batch at
positions 1 to 4, and the other 23 sit at positions 68 to 221 — seventeen of them
in cycles 22 and 23, twenty-two weeks out.

Why, as DRE-3151 traces it: the collision rule that puts an older card ahead of a
newer one touching the same file pulls those older cards into the first cycle and
pushes the model's own picks behind them. That path was traced on four cards
(DRE-2371, 2431, 2583, 2730) and is not re-traced here; what is re-derived here is
the outcome table above and the position spread. Batch 2 has the same shape, and
batch 2 is the one that was approved.

So the judged read is running, answering in one call, and costing a model call
per groom — and the batch the CEO approves is still mostly the rules' batch.
**DRE-3737** owns this finding; as of 2026-09-12 it is in `Intake`.

## What the CEO actually reads, checked line by line

Against batch 2's live comment on DRE-3327:

- **A reason on every row** — yes. Zero of 262 outcomes across either batch has
  an empty reason.
- **A trigger on every "not now"** — yes, 238 of 238.
- **Evidence on every "likely done"** — yes, all four name what they point at,
  and the cards that claim supersession without naming anything are listed
  separately rather than guessed at.
- **"Could not rank" printed, not folded** — yes. All four cards named in full
  under their own heading, with the sentence explaining what the list is for.
- **One thing is folded**: the "What the ranked read said" table prints five
  rows and then `| … | and 218 more, each with its reason in the proposal JSON |`.
  Every card's reason exists, but only the batch's reasons and the five sampled
  rows are readable in the comment itself. The rest are in the run artifact,
  which the CEO does not open.

## What the two sittings closed

The first sitting named three things it could not establish. Two are now closed
and one is not.

1. **The scorer's report — closed.** `scripts/groomer_score.py` is on `main` and
   the report is pasted above, verbatim, both halves, run read-only over
   DRE-3151's live thread and batch 1's kept artifact.
2. **The CEO's half of the audit — closed.** DRE-3151 is `Done`. His call on all
   26 rows is recorded there, accepted on his word at 2026-09-12 21:46 PT, with
   the caveat the audit itself states: the calls were made with the model's
   reasons and today's board in view, so they are informed rather than blind.
3. **The console batch panel was not seen — still open.**
   `app.agent-bureau.com` answers 302 to an unauthenticated read, and the panel
   showing this proposal with the same columns needs a logged-in session this
   record has no way to open. `deferred: console batch panel — needs an
   authenticated session; owed to the operator.` It is not one of DRE-3260's
   acceptance criteria, and nothing here stands in for it.

**Two days, and one matched pair, by decision.** DRE-3151 asked for judged and
rules-only over the same population on two different days. 2026-09-08 has both;
2026-09-09 has the judged run only. The CEO dropped the second pair as
unnecessary on 2026-09-12 — "one matched pair plus batch 2 was enough to show the
finding" — so the gap is a closed decision rather than an outstanding dispatch.

## What the epic still owes

Neither of the two findings in this record is fixed by it, and both are cards:

- **DRE-3544** — a card the model declined must never appear in the proposed
  batch. `Intake`, and `scripts/groomer.py:1013` is unchanged on `main`.
- **DRE-3737** — the batch discards the model's picks; a collision rule pulls
  older cards ahead of them. `Intake`.

The judged read works: one call, sized off the census, not cut short, a reason on
every row, every refusal printed. Whether it is yet making the batch better is a
different question, and the honest answer from these two batches is **not
yet** — the model agrees with the CEO 60% of the time and the batch he is shown
agrees 27% of the time, because the batch is mostly not the model's.
