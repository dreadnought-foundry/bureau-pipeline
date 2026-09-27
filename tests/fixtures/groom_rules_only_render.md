# Groom proposal `a85999f18b18` — cycle 12

8 cards of 34 in Intake are proposed for cycle 12, in the order below. Of those, 8 for Planning and 0 for Cancel. Nothing moves until you approve it.

Ranked by the rules only (judgement off) — priority, creation date, file collisions and blocker relations, and nothing about what we are already doing.

**To approve:** comment `🧺 groom-approved: a85999f18b18` on this card. Approval moves the Planning list to Planning and the Cancel list to Canceled. Anything else — including a comment that mentions the marker — leaves both lists where they are.

**To say more than yes:** `🧺 groom-declined: a85999f18b18 — <reason>` declines the batch (the reason is required); `🧺 groom-excluded: a85999f18b18 DRE-N` keeps a card on either list in Intake and out of every later proposal (don't do); `🧺 groom-held: a85999f18b18 DRE-N` keeps it in Intake and ranks it last next time (hold); `🧺 groom-added: a85999f18b18 DRE-N` pulls one in, after the batch. One comment each, and the newest one about a card wins.

## The population

| Repo | Cards |
| -- | -- |
| portico | 18 |
| bureau-pipeline | 13 |
| agent-bureau | 3 |

## The batch, in order

The Planning list — approving moves these cards to Planning. Urgent first, then High, then everything else — newest first, whatever repo it is in, and no card is left out for its age. Repo order (portico → agent-bureau → bureau-pipeline) breaks a tie between cards of equal priority created on the same day, and decides nothing else. An epic and its children are one unit — unless a collision or a recorded blocks relation says otherwise, in which case the constraint wins.

| # | Card | Pri | Repo | Epic | Title | Why |
| -- | -- | -- | -- | -- | -- | -- |
| 1 | DRE-103 | — | portico | — | DRE-103 does a thing | in the batch by the rules — newest first, position 1 |
| 2 | DRE-101 | Urgent | portico | — | DRE-101 does a thing | in the batch by the rules — marked Urgent, position 2 |
| 3 | DRE-102 | High | agent-bureau | — | DRE-102 does a thing | in the batch by the rules — marked High, position 3 |
| 4 | DRE-120 | — | bureau-pipeline | — | DRE-120 does a thing | in the batch by the rules — newest first, position 4 |
| 5 | DRE-132 | — | bureau-pipeline | — | DRE-132 does a thing | in the batch by the rules — newest first, position 5 |
| 6 | DRE-121 | — | portico | — | DRE-121 does a thing | in the batch by the rules — newest first, position 6 |
| 7 | DRE-133 | — | portico | — | DRE-133 does a thing | in the batch by the rules — newest first, position 7 |
| 8 | DRE-109 | — | agent-bureau | — | DRE-109 does a thing | in the batch by the rules — newest first, position 8 |

## Collisions, and what the order does about them

- DRE-103 before DRE-101 — both touch alpha.ts; newer card first, and the order is recorded

Collision cover: 32 card(s) name no file, so a collision involving one of them is invisible to this read — DRE-102, DRE-104, DRE-105, DRE-106, DRE-107, DRE-108, DRE-109, DRE-110, DRE-111, DRE-112, DRE-113, DRE-114, DRE-115, DRE-116, DRE-117, DRE-118, DRE-119, DRE-120, DRE-121, DRE-122….

## What waits, and roughly how long

- Nothing: every repo has work in this batch.

26 cards are **not now** — wanted, deliberately not this batch. That is 'later', and it is not 'no'. 26 of them carry the cycle they are reconsidered in.

## Not now — and when to come back

Wanted, deliberately not this batch — and each one names what brings it back. That is 'later', and it is not 'no'.

- when cycle 13 opens — 8 cards: DRE-104, DRE-105, DRE-108, DRE-110, DRE-111, DRE-122, DRE-123, DRE-134
- when cycle 14 opens — 8 cards: DRE-107, DRE-112, DRE-113, DRE-114, DRE-115, DRE-124, DRE-125, DRE-126
- when cycle 15 opens — 8 cards: DRE-116, DRE-117, DRE-118, DRE-119, DRE-127, DRE-128, DRE-129, DRE-130
- when cycle 16 opens — 2 cards: DRE-106, DRE-131

## On cycles

Assigning cards to cycles is not a return to sprint planning. The cycle is the OKR heartbeat — a reporting rhythm, not a capacity commitment — and it still reports what moved. What the groomer needs from it is a native container for an ORDER, which Linear already has and nobody has to build.
