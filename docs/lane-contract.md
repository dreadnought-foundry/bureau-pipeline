# The lane contract

<!-- GENERATED FILE — do not edit. Source: config/lane-contract.json.
     Regenerate with `python3 scripts/lane_contract.py render`. -->

Per lane: the entrance condition, the exit condition, the permitted writers, and the evidence that justifies occupancy. This document is rendered from the same file the guard, the sweep and the integration harness read, so it cannot drift from the enforcement.

Wave phase reached: **2** — the lane contract asserted by the harness. A clause marked **live** is asserted by the harness on every trunk commit. A clause marked **promised** is skipped until its phase ships, and fails the harness the moment its phase has passed with nothing enforcing it.

## The flow

| # | Lane | Segment | Stall window |
| --- | --- | --- | --- |
| 1 | Intake | planning | — |
| 2 | Planning | planning | 120 min |
| 3 | Green Light | planning | — |
| 4 | Backlog | work | — |
| 5 | Todo | work | 15 min |
| 6 | In Progress | work | 60 min |
| 7 | In Review | work | 120 min |
| 8 | Done | work | — |

Planning exit is the transition **Green Light → Backlog** — where the second critic writes its verdict, and the boundary the guard's scope is derived from.

## Off the flow

| Lane | Why it is off the flow |
| --- | --- |
| Triage | the guard's own destination for a card returned three times — policing it would bounce the sink straight back to Intake and loop |
| Canceled | terminal and off the path; the card makes no claim to justify |
| Duplicate | terminal and off the path; the card makes no claim to justify |

## The lanes

### Intake

| Clause | What it requires | Enforcement |
| --- | --- | --- |
| **entrance** | Every writer that creates work writes here first — the relay, the planner, a mid-epic discovery, a human. There is no other valid first lane.  
_Waiting on: DRE-2680 points every writer at Intake._ | Phase 5 — promised |
| **exit** | A classification exists: one-off, epic, or wave.  
_Waiting on: DRE-2719 makes Planning decide the classification._ | Phase 5 — promised |
| **writers** | Anything that creates a card, and nothing that moves one onward.  
Permitted writers: `relay`, `plan.yml`, `mid_epic.py`, `linear_ops.py`, `operator` | Phase 2 — live |
| **evidence** | The card exists and carries no classification yet.  
_Waiting on: DRE-2719._ | Phase 5 — promised |

### Planning

| Clause | What it requires | Enforcement |
| --- | --- | --- |
| **entrance** | The card has been classified as needing a plan.  
_Waiting on: DRE-2719._ | Phase 5 — promised |
| **exit** | A plan artifact exists. No artifact, no exit.  
_Waiting on: DRE-2720 writes the artifact; the gate that refuses exit without one is Phase 5._ | Phase 5 — promised |
| **writers** | The planner, the sweep that re-triggers it, and a human.  
Permitted writers: `plan.yml`, `reconcile.py`, `operator` | Phase 2 — live |
| **evidence** | The plan artifact on the card: business case, KPIs as data, risk, outcome.  
_Waiting on: DRE-2720._ | Phase 5 — promised |

### Green Light

| Clause | What it requires | Enforcement |
| --- | --- | --- |
| **entrance** | A plan is waiting on the CEO, or an agent has escalated a question only the CEO can answer.  
_Waiting on: DRE-2721's second critic decides what may wait here._ | Phase 5 — promised |
| **exit** | The CEO answers: an approved plan activates, an answered escalation returns to Todo, a rejected one goes to Backlog.  
_Waiting on: DRE-2721._ | Phase 5 — promised |
| **writers** | The planner, the two agent runs that escalate, and a human.  
Permitted writers: `plan.yml`, `agent-task.yml`, `agent-fix.yml`, `operator` | Phase 2 — live |
| **evidence** | A plan artifact, or an escalation comment written in business terms — never code, never a diff.  
_Waiting on: DRE-2721._ | Phase 5 — promised |

### Backlog

| Clause | What it requires | Enforcement |
| --- | --- | --- |
| **entrance** | It carries a routing verdict — one of the five in config/routing-verdicts.json, machine-readable on the card.  
_Waiting on: the vocabulary exists (DRE-2724); what is still missing is a writer that puts a verdict on EVERY card at planning exit — DRE-2721's second critic._ | Phase 5 — promised |
| **exit** | The verdict's destination is reached. The sweep promotes a FLEET card to Todo once the dependency gate clears and the WIP cap has room; a human moves a WORKBENCH or OPERATOR card to Todo and works it there; a PARKED card does not leave, and is never reported as stalled.  
_Waiting on: the sweep already refuses to promote a non-FLEET card (DRE-2724); asserting the transitions needs the history Phase 5 records._ | Phase 5 — promised |
| **writers** | The planner and mid-epic discovery create here; the sweep and the dead-run cap park here.  
Permitted writers: `plan.yml`, `mid_epic.py`, `reconcile.py`, `dead_run.py`, `linear_ops.py` | Phase 2 — live |
| **evidence** | A routing verdict comment; and, for a child of an epic, an epic that has passed Planning exit.  
_Waiting on: the marker exists and the sweep reads it (DRE-2724); asserting that every Backlog card carries one needs the writer at planning exit (DRE-2721)._ | Phase 5 — promised |

### Todo

| Clause | What it requires | Enforcement |
| --- | --- | --- |
| **entrance** | Its verdict names who builds it here: FLEET for a dispatched agent run, WORKBENCH or OPERATOR for a person — marked `hand-built`, which is what already stops the sweep dispatching a competing run or reporting the card as stranded.  
_Waiting on: the sweep promotes only a FLEET card (DRE-2724); asserting occupancy needs the transition history Phase 5 records._ | Phase 5 — promised |
| **exit** | A dispatched run posts its start receipt and the card moves to In Progress — or, on a hand-built card, the person working it opens the pull request.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |
| **writers** | The sweep promotes into it; the build run takes cards out of it.  
Permitted writers: `reconcile.py`, `agent-task.yml`, `linear_ops.py`, `operator` | Phase 2 — live |
| **evidence** | A verdict whose destination is this lane, no unmet blocking relation, and — for a FLEET card — room under the WIP cap.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |

### In Progress

| Clause | What it requires | Enforcement |
| --- | --- | --- |
| **entrance** | A dispatched agent run exists for this card.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |
| **exit** | A pull request exists on the card's own branch — or the run died and the card is requeued.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |
| **writers** | The build run, the fix run, and the sweep that requeues a dead one.  
Permitted writers: `agent-task.yml`, `agent-fix.yml`, `reconcile.py` | Phase 2 — live |
| **evidence** | The run's start receipt on the card, and a live or completed run behind it.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |

### In Review

> Folded from the two review lanes DRE-2726 retired (DRE-2818 deleted their entries once Linear archived the states). Both meant 'a pull request is open and being checked'; the sweep now keys off the evidence — a verdict bound to the head — rather than off which of two lanes the card sat in.

| Clause | What it requires | Enforcement |
| --- | --- | --- |
| **entrance** | A pull request is open on the card's branch and is being checked: by the critic, by the verifier, or by the merge gate.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |
| **exit** | GitHub merges the pull request and linear-sync writes Done — or the pull request is gone and the card is requeued under the dead-run cap.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |
| **writers** | The build run puts cards here; the critic, the gate, the fix run and the sweep move them on.  
Permitted writers: `agent-task.yml`, `qa-review.yml`, `merge-gate.yml`, `agent-fix.yml`, `reconcile.py` | Phase 2 — live |
| **evidence** | An open pull request; and, once the critic has run, a verdict bound to the head sha.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |

### Done

| Clause | What it requires | Enforcement |
| --- | --- | --- |
| **entrance** | GitHub merged the card's pull request. The merge event, never an agent's claim.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |
| **exit** | None. Done is terminal, and the guarded write layer refuses to un-complete a card that reached it.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |
| **writers** | The merge seam, the sweep's merged-PR backstop, and a human closing an operator card.  
Permitted writers: `linear-sync.yml`, `reconcile.py`, `linear_ops.py`, `operator` | Phase 2 — live |
| **evidence** | The merge commit on the card's own branch.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |

### Triage

| Clause | What it requires | Enforcement |
| --- | --- | --- |
| **entrance** | The card itself is malformed and cannot proceed as written: an unroutable repo label, an archived repo, a card the readiness guard has returned three times.  
_Waiting on: DRE-2723 defines what may be parked here._ | Phase 5 — promised |
| **exit** | Somebody fixes the card, and it re-enters at Intake. Triage is not a decision queue — a card waiting on the CEO belongs in Green Light.  
_Waiting on: DRE-2723._ | Phase 5 — promised |
| **writers** | Whatever found the defect: the relay, the sweep, either agent run, or a human.  
Permitted writers: `relay`, `reconcile.py`, `agent-task.yml`, `agent-fix.yml`, `operator` | Phase 2 — live |
| **evidence** | The defect, named on the card by whatever bounced it.  
_Waiting on: DRE-2723._ | Phase 5 — promised |

### Canceled

| Clause | What it requires | Enforcement |
| --- | --- | --- |
| **entrance** | A human decided the work will not be done.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |
| **exit** | None. Terminal.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |
| **writers** | A human, only. The pipeline never cancels a card.  
Permitted writers: `operator` | Phase 2 — live |
| **evidence** | A human's decision, on the card.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |

### Duplicate

| Clause | What it requires | Enforcement |
| --- | --- | --- |
| **entrance** | The card restates work another card already carries.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |
| **exit** | None. Terminal.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |
| **writers** | A human, only.  
Permitted writers: `operator` | Phase 2 — live |
| **evidence** | The card it duplicates, named on it.  
_Waiting on: needs the transition history Phase 5 records._ | Phase 5 — promised |

## The rules the harness asserts

| Rule | What it means | Enforcement |
| --- | --- | --- |
| `board.every_state_is_named` | No state exists in Linear that the contract does not name.  
_A leftover design-review state sat on the board with one card ever and zero code references, and nobody noticed for four months (DRE-2726 retired it; DRE-2818 deleted the last entry naming it)._ | Phase 2 — live |
| `board.every_lane_exists` | No lane is named that does not exist in Linear.  
_The other half of the same pair: a contract that names a lane the board dropped is a contract nothing can satisfy._ | Phase 2 — live |
| `board.retiring_lane_is_empty` | A retiring lane holds no cards.  
_A lane the pipeline no longer writes to is a lane nothing will move a card out of. An occupied one is a stranded card._  
_Waiting on: Phase 3 is when the wave asserts it. Nothing is retiring today (DRE-2818 deleted the last two entries once Linear archived their states), so the clause is vacuously satisfied and skipped; it earns its keep on the NEXT retirement, where the sweep's drain (reconcile.drain_retiring_lanes) empties the lane and this is what proves the drain finished._ | Phase 3 — promised |
| `board.retired_entry_is_deleted` | A retiring entry whose Linear state is already gone is deleted from this file.  
_A retirement that never finishes is drift too. Once the board catches up, the entry is the last copy of a lane that no longer exists._ | Phase 2 — live |
| `console.state_lists_carry_every_lane` | Every lane the pipeline knows appears in the console's state lists, and the console names no state the contract does not carry.  
_The console's vocabulary carried Proposed and HOLD, neither of which is a Linear state, and it renders an Intake column that can never fill._ | Phase 2 — live |
| `pipeline.vocabulary_is_contract_lanes` | The pipeline's own scripts and workflows name no state the contract does not carry.  
_A workflow that advances a card into an archived state fails its Linear call at 2am, on a card nobody is watching._ | Phase 2 — live |
| `transition.permitted_writer` | Every transition observed was made by a writer the destination lane permits.  
_A lane's writer list is only a contract if something reads the transition log against it._  
_Waiting on: needs the transition history the Phase-5 front door records._ | Phase 5 — promised |
| `transition.required_evidence` | Every transition observed carried the evidence the destination lane requires.  
_The evidence clause is the one that makes occupancy justifiable rather than assumed._  
_Waiting on: the routing verdict exists and is readable (DRE-2724); still needed is the plan artifact gate (DRE-2720) and a writer that stamps every card at planning exit (DRE-2721)._ | Phase 5 — promised |

## Writers

| Writer | What it is | Where it lives |
| --- | --- | --- |
| `relay` | the dispatch Lambda — creates a card's first lane from an inbound event | lives in agent-bureau (cloud/relay); no path to check from here |
| `operator` | a human, working in Linear or running a script by hand | — |
| `plan.yml` | the planner — writes an epic's children and moves the epic for approval | `.github/workflows/plan.yml` |
| `agent-task.yml` | the build run — takes a card from Todo and reports where it landed | `.github/workflows/agent-task.yml` |
| `agent-fix.yml` | the fix run — REQUEST_CHANGES or a merge conflict | `.github/workflows/agent-fix.yml` |
| `qa-review.yml` | the adversarial critic — writes the verdict the gate reads | `.github/workflows/qa-review.yml` |
| `merge-gate.yml` | the gate — merges on CI green plus a bound APPROVE | `.github/workflows/merge-gate.yml` |
| `linear-sync.yml` | the merge seam — closes the card off GitHub's merge event | `.github/workflows/linear-sync.yml` |
| `reconcile.py` | the ~15-minute sweep — promotion, staleness nudges, unsticking | `scripts/reconcile.py` |
| `linear_ops.py` | the guarded write layer every other writer goes through | `scripts/linear_ops.py` |
| `mid_epic.py` | mid-epic discovery — files a sibling into an approved epic | `scripts/mid_epic.py` |
| `dead_run.py` | the dead-run cap — parks a card that keeps dying | `scripts/dead_run.py` |
| `guard` | the lane guard — returns a card whose occupancy is unjustified | DRE-2725, built in agent-bureau; reads its scope from lane_scope.py |

