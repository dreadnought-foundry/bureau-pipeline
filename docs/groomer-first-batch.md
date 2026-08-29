# The first groom, and what it agrees with the Forms review about

Run live on **2026-08-29** against the real workspace, read-only:

```
python3 scripts/groomer.py propose --lane Backlog --capacity 20
```

Proposal `fbcbad48a115`. Nothing was moved — `propose` writes nothing, and
`drain` refuses without a CEO approval naming this exact batch.

**Why `--lane Backlog`.** `groomer.py census --lane Intake` returns 0 cards
today: the cutover that moves the Backlog into Intake has not happened yet. The
population it will move is the Backlog, so that is what was groomed. The lane is
a flag, the default is Intake, and nothing else about the run changes.

## The population, as it actually is

219 cards, re-derived live rather than quoted:

| Repo | Cards |
| -- | -- |
| agent-bureau | 72 |
| **portico** | **71** |
| bureau-pipeline | 44 |
| deltasolv | 29 |
| atlas | 2 |
| *(no repo label)* | 1 |

Portico's 71 split **47 under nine epics and 24 with no parent at all** — the
loose population the card said was where the sequencing judgement actually is,
at exactly the size it said. All 71 come out at positions 1–71; the first
non-Portico card is position 72.

## What it proposed

20 cards into **cycle 12** (the next cycle that has not started; cycle 11 is
running). 199 cards are **not now**, each carrying the cycle it is reconsidered
in. Nothing was recommended dead: no card in the population carries a
`Superseded by:` line naming a card or a merged PR. Eight cards talk about being
superseded without naming anything checkable, and those are listed rather than
guessed at.

What waits, stated in the proposal rather than discovered later:

- **agent-bureau** — 72 cards, first one in cycle 15, roughly 6 weeks out
- **bureau-pipeline** — 44 cards, first one in cycle 16, roughly 8 weeks out
- **atlas** — 2 cards, cycle 17, roughly 10 weeks out
- **deltasolv** — 29 cards, cycle 23, roughly 22 weeks out

**One thing the first batch shows immediately**: ordering Portico by age puts the
oldest *loose* cards first, and several of them are session notes and operator
milestones rather than build work. That is the right division of labour — the
groomer sequences, Planning (DRE-2719) decides whether a card is still wanted —
but the first real batch should be read with that question in mind. It is also
the argument for the approval gate: the first batch is exactly the one nobody
should have let through unread.

## The comparison against DRE-2649

The independent Forms review read the two Forms epics, the link-out epic and
their children by hand, and recorded eight file collisions between cards that
were concurrent by the graph — three already known, five nobody had spotted. The
groomer read the same cards with no knowledge of that review, as part of
sequencing the whole population. Both results are below.

Card labels are the review's: A2 = DRE-2632, A4 = DRE-2634, A5 = DRE-2635,
A6 = DRE-2636, A7 = DRE-2637, B1 = DRE-2640, B2 = DRE-2641, B3 = DRE-2642,
B4 = DRE-2643, B5 = DRE-2644, B6 = DRE-2645, B7 = DRE-2654.

### Agreement

Four of the review's pairs, found independently:

| Pair | File | Groomer's positions | Review |
| -- | -- | -- | -- |
| DRE-2496 ↔ B3 | `Thread.tsx` | 53 → 64 | the recorded chain 2496 → B3 → B7 |
| B3 ↔ B7 | `Thread.tsx` | 64 → 68 | recorded, and "B3 → B7 is real" |
| DRE-2496 ↔ B7 | `Thread.tsx` (review: `Composer.tsx`) | 53 → 68 | found by the review |
| A4 ↔ DRE-2497 | `LibraryScreen.tsx`, `linkLogic.ts`, `linkTransport.ts` | 46 and 54 | recorded, and flagged as the one held by prose rather than a relation |

The `Thread.tsx` chain comes out in the review's order, three cards apart in one
sequence, without reading a single relation — the file lists alone produced it.

The last row is an agreement on the PAIR and a disagreement on the DIRECTION,
and the proposal says so: the collision wants DRE-2497 first, three Portico
epics (DRE-2492, DRE-2628, DRE-2629) constrain each other in a loop, and the
sort had to drop one edge to order them at all. That dropped edge is one of the
eight reported under *constraints that point both ways*. Two things that each
have to go first is a planning question, and the groomer's job is to surface it,
not to answer it silently.

### Disagreement

**Five pairs the review found and the groomer did not**, with the same cause
every time — the shared file is not written on both cards:

| Pair | Why it was missed |
| -- | -- |
| A5 ↔ B1 | B1 (DRE-2640) names no file at all — **reported as unreadable** |
| A7 ↔ B6 | A7 (DRE-2637) names no file at all — **reported as unreadable** |
| B5 ↔ B7 (`Composer.tsx`) | B5 names `notify_lib.ts` only; the review derived `Composer.tsx` from what the card *would have to* touch |
| A5 ↔ A6 | neither card names the other's file |
| B2 ↔ B4 | B2 names no file but the branch banner |

Two of the five are **visible misses**: DRE-2640 and DRE-2637 are on the
proposal's unreadable list (36 cards across the population), so a reader can see
exactly where the check has no cover. The other three are **invisible misses** —
the cards name files, just not the colliding one. That is the honest limit of
reading declared files: it finds the collisions cards admit to, and a human
reading the code finds the ones they do not.

**One pair is out of scope rather than missed**: A2 ↔ DRE-2494
(`routes/library.ts`, `model_types.ts`) — DRE-2494 is Done, so it is in no
groom's population.

**Pairs the groomer found that the review did not record**, in this set alone:
`items_lib.ts` between DRE-2497, F1 and A2; `comments_lib.ts` between A2 and B3;
`content.ts` between A2 and A6; `ingest_lib.ts` between A6 and B7;
`responses_lib.ts` between A5, B4 and the B-epic demo card; and `Composer.tsx`
between DRE-2497 and B7 — a pair the review found in the other direction
(2496 ↔ B7) but not this one. These are candidates, not verdicts: the claim is
only "both cards name this file". One class is a clear false positive and is
worth naming — three cards share `docs/design/forms/forms-prototype.html`, which
they *read* rather than own.

### What the comparison says

The groomer is a **floor, not a substitute**. On this set it reproduced four of
the nine in-population pairs in seconds, at no judgement cost, and it told the
truth about where it could not see. It did not reproduce the findings that
needed someone to read the code and infer what a card must touch — and the
second critic's cross-epic sight (DRE-2721, D3) is the backstop for exactly
those.

Three things fall straight out of it:

1. **A card that names no files is a cheap thing to fix.** Two of the five
   misses would have been found if DRE-2640 and DRE-2637 named the files they
   touch. The unreadable list is the work item.
2. **The boilerplate threshold is tuned, and the tuning is visible.** At 5,
   `responses_lib.ts` (8 cards) was discarded as reference — a file the review
   named as a real collision. It is 12, and the only two paths it discards on
   this population are `linear-sync.yml` (19 cards, a branch-rule banner) and
   `reconcile.py` (20 cards).
3. **Two bugs were found by running it for real, not by the unit tests.** A
   shared BASENAME across two repositories (`CLAUDE.md`, `repo-map.json`,
   `deploy.sh`) is not a collision, and it had pulled an agent-bureau card to
   position 30. And a constraint loop resolved only when the sort ran out of
   ready work put the highest-priority epics in the population at position 118
   of 147. Both are fixed and both now have tests naming this run.

## Reproducing this

```
python3 scripts/groomer.py propose --lane Backlog --capacity 20 --out proposal.json
```

The sequence is deterministic for a given population, so a re-run on a moved
population yields a different proposal id — which is the point: an approval
binds to a batch, not to the groomer.
