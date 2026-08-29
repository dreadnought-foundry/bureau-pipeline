# The first groom, and what it agrees with the Forms review about

Run live on **2026-08-29** against the real workspace, read-only:

```
python3 scripts/groomer.py propose --lane Backlog --capacity 20
```

Proposal `cea5e295fe9c`. Nothing was moved — `propose` writes nothing, and
`drain` refuses without a CEO approval naming this exact batch.

**Why `--lane Backlog`.** `groomer.py census --lane Intake` returns 0 cards
today: the cutover that moves the Backlog into Intake has not happened yet. The
population it will move is the Backlog, so that is what was groomed. The lane is
a flag; the default is Intake and nothing else changes.

## The population, as it actually is

220 cards, re-derived live rather than quoted from the card:

| Repo | Cards |
| -- | -- |
| agent-bureau | 73 |
| **portico** | **71** |
| bureau-pipeline | 44 |
| deltasolv | 29 |
| atlas | 2 |
| *(no repo label)* | 1 |

Portico's 71 split **47 under nine epics, 24 with no parent at all** — the loose
population the card said was where the sequencing judgement actually is, at
exactly the size it said.

## What it proposed

18 cards into **cycle 12** (the next cycle that has not started; cycle 11 is
running). 202 cards are **not now**, each carrying the cycle it is reconsidered
in. No card was recommended dead: nothing in the population carries a
`Superseded by:` line naming a card or a merged PR.

What waits, stated in the proposal rather than discovered later:

- **agent-bureau** — 73 cards, first one in cycle 13, roughly 2 weeks out
- **bureau-pipeline** — 44 cards, first one in cycle 14, roughly 4 weeks out
- **atlas** — 2 cards, cycle 15, roughly 6 weeks out
- **deltasolv** — 29 cards, cycle 16, roughly 8 weeks out

**One thing the first batch shows immediately**: ordering Portico by age puts the
oldest *loose* cards first, and several of them are session notes and operator
milestones rather than build work. That is the right division of labour — the
groomer sequences, Planning (DRE-2719) decides whether a card is still wanted —
but it means the first real batch should be read with that question in mind. It
is also the argument for the approval gate: the first batch is exactly the one
nobody should have let through unread.

## The comparison against DRE-2649

The independent Forms review read the two Forms epics, the link-out epic and
their children by hand, and recorded eight file collisions between cards that
were concurrent by the graph — three already known, five nobody had spotted. The
groomer read the same cards with no knowledge of that review. Both results are
below.

Card labels are the review's: A2 = DRE-2632, A4 = DRE-2634, A5 = DRE-2635,
A6 = DRE-2636, A7 = DRE-2637, B1 = DRE-2640, B2 = DRE-2641, B3 = DRE-2642,
B4 = DRE-2643, B5 = DRE-2644, B6 = DRE-2645, B7 = DRE-2654.

### Agreement

Four of the review's pairs, found independently, each turned into an order the
sequence honours:

| Pair | File | Groomer's order | Review |
| -- | -- | -- | -- |
| DRE-2496 ↔ B3 | `Thread.tsx` | 2496 (pos 116) before 2642 (pos 139) | recorded chain 2496 → B3 → B7 |
| B3 ↔ B7 | `Thread.tsx` | 2642 (pos 139) before 2654 (pos 143) | recorded chain, "B3 → B7 is real" |
| DRE-2496 ↔ B7 | `Thread.tsx` (review: `Composer.tsx`) | 2496 before 2654 | found by the review |
| A4 ↔ DRE-2497 | `LibraryScreen.tsx`, `linkLogic.ts`, `linkTransport.ts` | 2497 (pos 117) before 2634 (pos 129) | recorded, and flagged as the one held by prose rather than a relation |

The last one matters most: the review's finding was that A4 ↔ 2497 is real but
enforced only by prose and a `related` link, so nothing in the pipeline holds
it. The groomer does not read the relation at all — it read the two cards' own
file lists and put 2497 first regardless.

### Disagreement

**Five pairs the review found and the groomer did not**, and the reason is the
same in every case — the shared file is not written on both cards:

| Pair | Why it was missed |
| -- | -- |
| A5 ↔ B1 | B1 (DRE-2640) names no file at all — **reported as unreadable** |
| A7 ↔ B6 | A7 (DRE-2637) names no file at all — **reported as unreadable** |
| B5 ↔ B7 (`Composer.tsx`) | B5 names `notify_lib.ts` only; the review derived `Composer.tsx` from what the card *would have to* touch |
| A5 ↔ A6 | neither card names the other's file |
| B2 ↔ B4 | B2 names no file but the branch banner |

Two of the five are **visible misses**: DRE-2640 and DRE-2637 are on the
proposal's unreadable list (36 cards across the whole population), so a reader
can see exactly where the check has no cover. The other three are **invisible
misses** — the cards name files, just not the colliding one. That is the honest
limit of reading declared files: it finds collisions cards admit to, and a human
reading the code finds the ones they do not.

**One pair is out of scope rather than missed**: A2 ↔ DRE-2494
(`routes/library.ts`, `model_types.ts`) — DRE-2494 is Done, so it is not in the
population any groom reads.

**Thirteen pairs the groomer found that the review did not record**, including
`items_lib.ts` between DRE-2497, F1 and A2; `comments_lib.ts` between A2 and B3;
`content.ts` between A2 and A6; `ingest_lib.ts` between A6 and B7;
`responses_lib.ts` between A5, B4 and the B-epic demo card; and `Composer.tsx`
between DRE-2497 and B7. These are candidates, not verdicts — the groomer's
claim is only "both cards name this file". One class is a clear false positive
and worth naming: three cards share `docs/design/forms/forms-prototype.html`,
which they *read* rather than own.

### What the comparison says

The groomer is a **floor, not a substitute**. On this set it reproduced half the
review's findings in seconds, at no judgement cost, and it told the truth about
where it could not see. It did not reproduce the findings that needed someone to
read the code and infer what a card must touch — and the second critic's
cross-epic sight (DRE-2721, D3) is the backstop for exactly those.

Two changes fall straight out of it:

1. **A card that names no files is a cheap thing to fix.** Two of the five
   misses would have been found if DRE-2640 and DRE-2637 named the files they
   touch. The unreadable list is the work item.
2. **The threshold is tuned, and the tuning is visible.** At a boilerplate
   threshold of 5, `responses_lib.ts` (8 cards) was discarded as reference — and
   that is a file the review named as a real collision. It is 12, and the two
   paths it still discards on this population are `linear-sync.yml` (19 cards,
   a branch-rule banner) and `reconcile.py` (20 cards).

## Reproducing this

```
python3 scripts/groomer.py propose --lane Backlog --capacity 20 --out proposal.json
```

The sequence is deterministic for a given population, so a re-run on a moved
population yields a different proposal id — which is the point: an approval
binds to a batch, not to the groomer.
