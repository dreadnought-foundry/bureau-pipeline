# Groom proposal `f02e12cea3b8` — cycle 12

8 cards of 34 in Intake are proposed for cycle 12, in the order below. Nothing moves until you approve it.

Ranked by the rules only (judgement off) — priority, creation date, file collisions and blocker relations, and nothing about what we are already doing.

**To approve:** comment `🧺 groom-approved: f02e12cea3b8` on this card. Anything else — including a comment that mentions the marker — leaves the batch where it is.

**To say more than yes:** `🧺 groom-declined: f02e12cea3b8 — <reason>` declines the batch (the reason is required); `🧺 groom-excluded: f02e12cea3b8 DRE-N` holds one card back; `🧺 groom-added: f02e12cea3b8 DRE-N` pulls one in, after the batch. One comment each, and the newest one about a card wins.

## The population

| Repo | Cards |
| -- | -- |
| portico | 18 |
| bureau-pipeline | 13 |
| agent-bureau | 3 |

## The batch, in order

Urgent first, then High, then everything else — oldest first, whatever repo it is in, and no card is left out for its age. Repo order (portico → agent-bureau → bureau-pipeline) breaks a tie between cards of equal priority created on the same day, and decides nothing else. An epic and its children are one unit — unless a collision or a recorded blocks relation says otherwise, in which case the constraint wins.

| # | Card | Pri | Repo | Epic | Title | Why |
| -- | -- | -- | -- | -- | -- | -- |
| 1 | DRE-101 | Urgent | portico | — | DRE-101 does a thing | in the batch by the rules — marked Urgent, position 1 |
| 2 | DRE-102 | High | agent-bureau | — | DRE-102 does a thing | in the batch by the rules — marked High, position 2 |
| 3 | DRE-106 | — | portico | — | DRE-106 does a thing | in the batch by the rules — oldest first, position 3 |
| 4 | DRE-109 | — | agent-bureau | — | DRE-109 does a thing | in the batch by the rules — oldest first, position 4 |
| 5 | DRE-119 | — | portico | — | DRE-119 does a thing | in the batch by the rules — oldest first, position 5 |
| 6 | DRE-131 | — | portico | — | DRE-131 does a thing | in the batch by the rules — oldest first, position 6 |
| 7 | DRE-118 | — | bureau-pipeline | — | DRE-118 does a thing | in the batch by the rules — oldest first, position 7 |
| 8 | DRE-130 | — | bureau-pipeline | — | DRE-130 does a thing | in the batch by the rules — oldest first, position 8 |

## Collisions, and what the order does about them

- DRE-101 before DRE-103 — both touch alpha.ts; older card first, and the order is recorded

Collision cover: 31 card(s) name no file, so a collision involving one of them is invisible to this read — DRE-102, DRE-104, DRE-105, DRE-106, DRE-108, DRE-109, DRE-110, DRE-111, DRE-112, DRE-113, DRE-114, DRE-115, DRE-116, DRE-117, DRE-118, DRE-119, DRE-120, DRE-121, DRE-122, DRE-123….

## What waits, and roughly how long

- Nothing: every repo has work in this batch.

25 cards are **not now** — wanted, deliberately not this batch. That is 'later', and it is not 'no'. 25 of them carry the cycle they are reconsidered in.

## Not now — and when to come back

Wanted, deliberately not this batch — and each one names what brings it back. That is 'later', and it is not 'no'.

- when cycle 13 opens — 8 cards: DRE-114, DRE-115, DRE-116, DRE-117, DRE-126, DRE-127, DRE-128, DRE-129
- when cycle 14 opens — 8 cards: DRE-104, DRE-105, DRE-111, DRE-112, DRE-113, DRE-123, DRE-124, DRE-125
- when cycle 15 opens — 8 cards: DRE-103, DRE-108, DRE-110, DRE-120, DRE-121, DRE-122, DRE-133, DRE-134
- when cycle 16 opens — 1 card: DRE-132

## Recommended dead — your call, not ours

**Declared on the card** — its own description says so:

- DRE-107 — superseded by DRE-101 · DRE-107 does a thing

The groomer never cancels. Cancelling is destructive and stays yours, as a separate step.

## On cycles

Assigning cards to cycles is not a return to sprint planning. The cycle is the OKR heartbeat — a reporting rhythm, not a capacity commitment — and it still reports what moved. What the groomer needs from it is a native container for an ORDER, which Linear already has and nobody has to build.
