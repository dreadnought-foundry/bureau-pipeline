# Groom proposal `991592d78894` — cycle 12

9 cards of 34 in Intake are proposed for cycle 12, in the order below. Of those, 8 for Planning and 1 for Cancel. Nothing moves until you approve it.

Ranked by the rules only (judgement off) — priority, creation date, file collisions and blocker relations, and nothing about what we are already doing.

**To approve:** comment `🧺 groom-approved: 991592d78894` on this card. Approval moves the Planning list to Planning and the Cancel list to Canceled. Anything else — including a comment that mentions the marker — leaves both lists where they are.

**To say more than yes:** `🧺 groom-declined: 991592d78894 — <reason>` declines the batch (the reason is required); `🧺 groom-excluded: 991592d78894 DRE-N` keeps a card on either list in Intake and out of every later proposal (don't do); `🧺 groom-held: 991592d78894 DRE-N` keeps it in Intake and ranks it last next time (hold); `🧺 groom-added: 991592d78894 DRE-N` pulls one in, after the batch; `🧺 groom-rewrite-accepted: 991592d78894 DRE-N` accepts the draft posted on card DRE-N and sends it to Planning with the new body. One comment each, and the newest one about a card wins. On the console, **Not doing** writes `groom-excluded` (don't do); **Move to Cancel** writes `groom-excluded` with `— move to Cancel` and puts the card on the next proposal's Cancel list; **Hold until** writes `groom-held` with `— until DRE-M` or `— until YYYY-MM-DD` and keeps the card off every proposal until that card is Done or that date (Pacific) has come.

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

## Cancel, with reasons

The rest of the same set: cards that no longer apply — replaced, superseded or already done. Approving cancels each one with its reason written on the card; `🧺 groom-excluded: 991592d78894 DRE-N` keeps one in Intake.

| # | Card | Pri | Repo | Epic | Title | Reason |
| -- | -- | -- | -- | -- | -- | -- |
| 1 | DRE-107 | — | portico | — | DRE-107 does a thing | superseded by DRE-101; the card's Superseded by line — Superseded by: DRE-101 |

## Collisions, and what the order does about them

- DRE-103 before DRE-101 — both touch alpha.ts; newer card first, and the order is recorded

Collision cover: 32 card(s) name no file, so a collision involving one of them is invisible to this read — DRE-102, DRE-104, DRE-105, DRE-106, DRE-107, DRE-108, DRE-109, DRE-110, DRE-111, DRE-112, DRE-113, DRE-114, DRE-115, DRE-116, DRE-117, DRE-118, DRE-119, DRE-120, DRE-121, DRE-122….

## What waits, and roughly how long

- Nothing: every repo has work in this batch.

25 cards are **not now** — wanted, deliberately not this batch. That is 'later', and it is not 'no'. 25 of them carry the cycle they are reconsidered in.

## Not now — and when to come back

Wanted, deliberately not this batch — and each one names what brings it back. That is 'later', and it is not 'no'.

- when cycle 13 opens — 8 cards: DRE-104, DRE-105, DRE-108, DRE-110, DRE-111, DRE-122, DRE-123, DRE-134
- when cycle 14 opens — 8 cards: DRE-112, DRE-113, DRE-114, DRE-115, DRE-124, DRE-125, DRE-126, DRE-127
- when cycle 15 opens — 8 cards: DRE-116, DRE-117, DRE-118, DRE-119, DRE-128, DRE-129, DRE-130, DRE-131
- when cycle 16 opens — 1 card: DRE-106

## On cycles

Assigning cards to cycles is not a return to sprint planning. The cycle is the OKR heartbeat — a reporting rhythm, not a capacity commitment — and it still reports what moved. What the groomer needs from it is a native container for an ORDER, which Linear already has and nobody has to build.
