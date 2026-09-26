<!--
The pipeline's copy of the two-lists wire contract (DRE-4727): one proposal
comment, exactly as `groomer.proposal_comment` posts it, with three Planning
rows and two Cancel rows, one Cancel reason carrying an escaped pipe.

Its twin is the console's fixture for DRE-4682 in agent-bureau —
`console/backend/tests/fixtures/groom-proposal-two-lists/proposal.md` —
the reader this page is written for. Change the two together: a different
spelling here is a proposal the console reads as having no Cancel list at all.

Regenerate from `fixture_proposal()` in tests/test_groomer_two_lists.py, which
compares everything below this comment byte for byte.
-->

🧺 groom-proposal: 3734d5abc032

# Groom proposal `3734d5abc032` — cycle 12

5 cards of 5 in Intake are proposed for cycle 12, in the order below. Of those, 3 for Planning and 2 for Cancel. Nothing moves until you approve it.

Ranked by test-model (asked) / test-model (answered) in 1 call over 5 cards, against 0 epics in flight, 0 merged PRs and 0 closed cards — 5 of 5 cards ranked.

**To approve:** comment `🧺 groom-approved: 3734d5abc032` on this card. Approval moves the Planning list to Planning and the Cancel list to Canceled. Anything else — including a comment that mentions the marker — leaves both lists where they are.

**To say more than yes:** `🧺 groom-declined: 3734d5abc032 — <reason>` declines the batch (the reason is required); `🧺 groom-excluded: 3734d5abc032 DRE-N` keeps a card on either list in Intake; `🧺 groom-added: 3734d5abc032 DRE-N` pulls one in, after the batch. One comment each, and the newest one about a card wins.

## The population

| Repo | Cards |
| -- | -- |
| portico | 3 |
| agent-bureau | 2 |

## The batch, in order

The Planning list — approving moves these cards to Planning. Urgent first, then High, then everything else — oldest first, whatever repo it is in, and no card is left out for its age. Repo order (portico → agent-bureau) breaks a tie between cards of equal priority created on the same day, and decides nothing else. An epic and its children are one unit — unless a collision or a recorded blocks relation says otherwise, in which case the constraint wins.

| # | Card | Pri | Repo | Epic | Title | Why |
| -- | -- | -- | -- | -- | -- | -- |
| 1 | DRE-4103 | High | agent-bureau | — | show the proposal's two lists on the console | the CEO reads both lists there |
| 2 | DRE-4101 | — | portico | — | retire the nightly sweep's age-out | the pile is only drained oldest first if nothing ages out |
| 3 | DRE-4104 | — | portico | — | write the cancel reason on the card | the drain needs somewhere to put the reason |

## Why each card is in the batch

The same reasons as the table above, in full — this is what the console shows when you open a row. A line the read did not give is left out rather than filled in.

### DRE-4103
- **Why:** the CEO reads both lists there

### DRE-4101
- **Why:** the pile is only drained oldest first if nothing ages out

### DRE-4104
- **Why:** the drain needs somewhere to put the reason

## Cancel, with reasons

The rest of the same set: cards that no longer apply — replaced, superseded or already done. Approving cancels each one with its reason written on the card; `🧺 groom-excluded: 3734d5abc032 DRE-N` keeps one in Intake.

| # | Card | Pri | Repo | Epic | Title | Reason |
| -- | -- | -- | -- | -- | -- | -- |
| 1 | DRE-4102 | — | portico | — | first cut of the intake census | superseded by DRE-4250 |
| 2 | DRE-4105 | — | agent-bureau | — | a probe of the old board cutover | the cutover ran on 2026-09-07 \| the probe is the proof |

## What the ranked read said

## Collisions, and what the order does about them

- None found in this population.

Collision cover: 5 card(s) name no file, so a collision involving one of them is invisible to this read — DRE-4101, DRE-4102, DRE-4103, DRE-4104, DRE-4105.

## What waits, and roughly how long

- Nothing: every repo has work in this batch.

0 cards are **not now** — wanted, deliberately not this batch. That is 'later', and it is not 'no'.

## On cycles

Assigning cards to cycles is not a return to sprint planning. The cycle is the OKR heartbeat — a reporting rhythm, not a capacity commitment — and it still reports what moved. What the groomer needs from it is a native container for an ORDER, which Linear already has and nobody has to build.
