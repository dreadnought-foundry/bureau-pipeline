# Groom proposal `2841a4dde594` — cycle 12

4 cards of 16 in Intake are proposed for cycle 12, in the order below. Of those, 3 for Planning and 1 for Cancel. Nothing moves until you approve it.

Ranked by the rules only (judgement off) — priority, creation date, file collisions and blocker relations, and nothing about what we are already doing.

**To approve:** comment `🧺 groom-approved: 2841a4dde594` on this card. Approval moves the Planning list to Planning and the Cancel list to Canceled. Anything else — including a comment that mentions the marker — leaves both lists where they are.

**To say more than yes:** `🧺 groom-declined: 2841a4dde594 — <reason>` declines the batch (the reason is required); `🧺 groom-excluded: 2841a4dde594 DRE-N` keeps a card on either list in Intake and out of every later proposal (don't do); `🧺 groom-held: 2841a4dde594 DRE-N` keeps it in Intake and ranks it last next time (hold); `🧺 groom-added: 2841a4dde594 DRE-N` pulls one in, after the batch; `🧺 groom-rewrite-accepted: 2841a4dde594 DRE-N` accepts the draft posted on card DRE-N and sends it to Planning with the new body. One comment each, and the newest one about a card wins.

## The population

| Repo | Cards |
| -- | -- |
| portico | 16 |

## The batch, in order

The Planning list — approving moves these cards to Planning. Urgent first, then High, then everything else — newest first, whatever repo it is in, and no card is left out for its age. Repo order (portico) breaks a tie between cards of equal priority created on the same day, and decides nothing else. An epic and its children are one unit — unless a collision or a recorded blocks relation says otherwise, in which case the constraint wins.

| # | Card | Pri | Repo | Epic | Title | Why |
| -- | -- | -- | -- | -- | -- | -- |
| 1 | DRE-101 | — | portico | — | DRE-101 does a thing | in the batch by the rules — newest first, position 1 |
| 2 | DRE-102 | — | portico | — | DRE-102 does a thing | in the batch by the rules — newest first, position 2 |
| 3 | DRE-103 | — | portico | — | DRE-103 does a thing | in the batch by the rules — newest first, position 3 |

## Cancel, with reasons

The rest of the same set: cards that no longer apply — replaced, superseded or already done. Approving cancels each one with its reason written on the card; `🧺 groom-excluded: 2841a4dde594 DRE-N` keeps one in Intake.

| # | Card | Pri | Repo | Epic | Title | Reason |
| -- | -- | -- | -- | -- | -- | -- |
| 1 | DRE-130 | — | portico | — | an old \| thing | superseded by DRE-90; the card's Superseded by line — Superseded by: DRE-90 |

## Collisions, and what the order does about them

- None found in this population.

Collision cover: 16 card(s) name no file, so a collision involving one of them is invisible to this read — DRE-101, DRE-102, DRE-103, DRE-104, DRE-105, DRE-106, DRE-107, DRE-108, DRE-109, DRE-110, DRE-111, DRE-112, DRE-113, DRE-114, DRE-115, DRE-130.

## What waits, and roughly how long

- Nothing: every repo has work in this batch.

12 cards are **not now** — wanted, deliberately not this batch. That is 'later', and it is not 'no'. 12 of them carry the cycle they are reconsidered in.

## Not now — and when to come back

Wanted, deliberately not this batch — and each one names what brings it back. That is 'later', and it is not 'no'.

- when cycle 13 opens — 4 cards: DRE-104, DRE-105, DRE-106, DRE-107
- when cycle 14 opens — 4 cards: DRE-108, DRE-109, DRE-110, DRE-111
- when cycle 15 opens — 4 cards: DRE-112, DRE-113, DRE-114, DRE-115

## On cycles

Assigning cards to cycles is not a return to sprint planning. The cycle is the OKR heartbeat — a reporting rhythm, not a capacity commitment — and it still reports what moved. What the groomer needs from it is a native container for an ORDER, which Linear already has and nobody has to build.
