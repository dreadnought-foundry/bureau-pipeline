# The judged groom — the first two batches, read against the live board

Observed by hand on **2026-09-10**, between 06:50 and 07:30 PT, entirely
read-only. Nothing was moved: every number below comes from a `propose` run
that had already happened, from the proposal artifact that run kept, or from
the live Linear board. The drain was not run for this record.

`propose` writes one comment and moves nothing. `drain` refuses without a CEO
approval naming the exact batch id.

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

**false-done: 0.** No recommendation was contradicted by the board. One
(DRE-3092) is confirmed right by a state the board already reached
independently. Two rest on a sibling card that is genuinely Done and genuinely
covers the ask. **Three rest on evidence that is thin or circular** — the two
throwaway cutover probes offer their own position in Intake as the proof that
the probe passed, and DRE-2657 cites an open defect card as evidence the
harness runs.

Nothing here is a false "done" in the expensive sense: the groomer never
cancels, and all six cards are still where they were. But the three thin ones
are exactly the rows to put in front of the CEO first, because a `likely-done`
whose evidence cannot be checked is one nobody should act on.

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

**Not fixed here.** This record does not change the groomer; it reports what
the two batches did. The fix belongs on its own card.

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

## What this record does not establish

Three of DRE-3260's criteria are **not met**, and no substitute was invented
for them.

1. **The scorer's report is missing, because the scorer does not exist.**
   `scripts/groomer_score.py` (DRE-3155) is **not on `main`**. It cannot be run
   over DRE-3151's audit table, so the agreement rate, false-done count and
   unranked count in the shape that card pins cannot be produced by the tool
   that is supposed to produce them. The three raw numbers are in this document;
   the scorer's verbatim report is owed.
2. **The console batch panel was not seen.** `app.agent-bureau.com` answers 302
   to an unauthenticated read. The panel showing this proposal with the same
   columns needs an authenticated session, so the screenshot is owed to the
   operator.
3. **The CEO's half of the audit is not in.** The 26-row table above is the
   comparison; which call he would have made is his to write on DRE-3151, and
   the agreement rate cannot be computed until he does.

**Two days, but not two matched pairs.** DRE-3151 asks for judged and
rules-only over the same population on two different days. 2026-09-08 has both.
2026-09-09 has the judged run only — no rules-only counterpart was run over the
262-card population, so batch 2 has no side-by-side. Producing one is a single
dispatch and it moves nothing.
