# Roll-up proof — DRE-4753 (epic DRE-4680)

**Status: PARTIAL — the watch window is open, and the first live run did not take the roll-up route.**
- **The card the run classified:** sandbox fixture DRE-5740, a five-piece product across two tiers, with three contracts between the pieces. The planning run stamped it `🧩 planning-shape: **epic**`, not `roll-up`. Under this card's rule, observations 2 to 4 are therefore *not reached*, and that is a finding about the route rather than a failure of the record. The stamp was not changed by hand.
- **Observation 7** (the checks on `main`) is green.
- **Observations 5 and 6** wait: on the sweep (off in the sandbox), on DRE-4669's child epics, and on a hand edit of a production epic that this proof does not make.

**The card that went through the run, and why it is not DRE-4915.** The card names DRE-4915, a production card in Intake. Moving it into Planning is a write to a production card. Under the CEO's rule of 2026-10-02, proofs run in the sandboxes, so a sandbox substitute went through the run instead: **DRE-5740**, filed for this proof in `dreadnought-foundry/agent-bureau-demo`. It arrived at the run with no shape stamp. It qualifies against the size tells in `standards/card-quality.md`:
- five separately shipping pieces across two tiers (a new Node service in `server/` and the Vite app);
- three service-to-app contracts (`GET /api/features`, the profile endpoints, the share snapshot);
- a fourth piece that reads what two others produce;
- its own body says "This is a list for Planning to cut into work. It is not one plan."

DRE-4915 was read at about 14:15 PT: still in Intake, untouched.

**How this was recorded.** A proof-runner session on the operator's instruction recorded this on 2026-10-03, 13:22–14:25 PT. Runs were read from the sandbox's Actions pages. Comments and lanes were read from the console database (`make db-read`). The one hand act, observation 0, was made by a coordinating session with the operator key. Every time is Pacific (PDT). **Pipeline sha:** the sandbox's `plan.yml` rides `stable` = bureau-pipeline `518ac246419734d0bf4f09465c62f5bde4334cef`, as the run's own checkout shows (`HEAD is now at 518ac24 Merge pull request #695 …`). **Watch window:** opened 2026-10-03 13:23 PT and closes 2026-10-10 13:23 PT.

| Criterion | Result |
|---|---|
| The record on `main`, headed by the card it observed, the pipeline sha, the window's opening and closing day, and PT times for all seven observations, each quoted or marked not observed | **Met as a partial record.** Observations 0, 1 and 7 quoted; 2–6 marked, with where they were looked for |
| What the classifier stamped on its own. If `roll-up`: two or more `[EPIC]` children, one `🧩 roll-up-split:` receipt, each child planned. If not: what the run did instead | **Met (not roll-up).** It stamped `epic` and planned nine build cards under one epic (§1) |
| The parent In Progress, skipped by the sweep, nothing dispatched at it; a roll-up parent closed by the sweep, or which one the close waits on | **Not observed.** The sweep (`reconcile.yml`) is off in the sandbox. The close waits on DRE-4669 (§4, §5) |
| DRE-3530's `wave-commitment` block removed by hand, and any other open epic carrying the marker listed | **Not done.** It is a production epic edit. The epics still carrying the marker are listed (§6) |
| The CEO closes this card after reading the record | Open. The window is open |

## Observation 0 — the one hand act

**13:23:13 PT.** DRE-5740 was moved Intake → Planning by the coordinating session with the operator key. The planner run that followed was the pipeline's own, [37151355680](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37151355680) (`repository_dispatch` `agent-plan`, created 13:23:21), started by the lane change.

## Observation 1 — what the classifier stamped

**13:24:10 PT**, on DRE-5740, by the planner:

```
🧩 planning-shape: **epic** — A set of cards that ship separately under one parent, with a plan the CEO approves before any of them run.
**Why:** Five pieces that ship separately: a new backend service plus the frontend, with three service-to-app contracts, and the share link reads what the catalog and profile pieces produce, so it is not one pull request. No seam test trips (no clean-days criterion, no operator step, no second surface joining later, about eight build cards at most), so nothing waits on watching an earlier piece live. The doubt: the card calls itself 'not one plan', but with no observation to cut at, the smaller shape is taken. — size tests checked: 1 contracts between the pieces, 2 two languages or two tiers, 6 cut on file footprint, not only on concern
**Stamped by:** `planner` · **model:** `claude-fable-5-1`
```

**Not `roll-up`.** The classifier weighed the card's own "not one plan" against its seam tests and chose the smaller shape, because "about eight build cards at most" fits one epic. **What the run did instead:**
- it planned DRE-5740 as one epic of nine cards (eight builds and a proof, DRE-5748 to DRE-5756), with all nine checked valid at 13:40:15 (`✅ all 9 child card(s) of DRE-5740 are valid`);
- 13:41:37: the first critic passed it;
- 13:44:33: the second critic sent it back over a collision with another epic;
- the planner revised it, and the re-review passed at 13:58:00;
- it reached Green Light at 13:58:06 and was approved at 14:02:10.

That path is recorded in full in DRE-5346's record, `docs/both-critics-before-green-light-proof.md`, §1.

**The finding.** The classifier's line between "epic" and "roll-up" sits at roughly eight build cards. A card that names five pieces and calls itself "a list for Planning to cut into work" is still classified as one epic. A card big enough to take the route has to be bigger than this, or carry a seam the tests recognize (a clean-days criterion, an operator step, a second surface joining later).

## Observations 2 to 4 — not reached

- **2 (the `Roll-up route — split into child epics` step and the `🧩 roll-up-split:` receipt):** not reached on DRE-5740, because the shape was `epic`. **For reference, read-only from production:** the route has run, on three wave conversions on 2026-10-01:
  - DRE-3530: `🧩 planning-shape: **roll-up**` 08:39 PT, `🧩 roll-up-split: DRE-3530 is split into 5 child epics …` 09:10 PT;
  - DRE-4669: stamp 08:39 PT, split into 6 child epics 09:13 PT;
  - DRE-3681: stamp 09:43 PT, split into 8 child epics 09:55 PT.

  All three already had their children (they were waves), so none is the fresh split this card asks for.
- **3 (each child `[EPIC]`-titled, `agent:planner`, entering Planning and planned on its own):** not reached. DRE-5740's children are build cards (`agent:engineer`, and `agent:ops` for the proof), not child epics.
- **4 (the parent In Progress; the sweep's `promotion: <parent> is an epic … skipping` line):** the parent is In Progress (14:02:10 PT). The activate run 37153686962 recorded `promotion: WIP at cap (0/0) — no card is dispatched this sweep` and `0 card(s) promoted`, so nothing was dispatched at it. The sweep itself, `reconcile.yml`, is **disabled in the sandbox**, so its skip line could not be observed. **BLOCKED by Reconcile.**

## Observation 5 — a roll-up parent closed by the sweep

**Not observed.** It waits on DRE-4669. Read at about 14:15 PT from the console database:

| child epic of DRE-4669 | state |
|---|---|
| DRE-4677 | Done |
| DRE-4679 | Done |
| DRE-4678 | In Progress |
| DRE-5034 | In Progress |
| DRE-5035 | In Progress |
| DRE-5036 | In Progress |

DRE-5740 is not a roll-up parent, so it is not a candidate. The rule is pinned by `tests/test_close_epics.py` (DRE-4700) on `main`. It is not observed live here.

## Observation 6 — DRE-3530's conversion

**Not done.** It is a hand edit of a production epic's description, and this sandbox proof does not make production edits. DRE-3530 stands as the card describes: In Progress, with a `roll-up` stamp and a split receipt, and its child epics DRE-3621 (Done), DRE-3622 (Done), DRE-3623 (Done), DRE-3624 (Planning) and DRE-3650 (Done).

**The board search for the marker** (`description ILIKE '%BEGIN wave-commitment%'`, open cards, console database, about 14:15 PT):

| card | state | note |
|---|---|---|
| DRE-3530 | In Progress | the card this observation names |
| DRE-3681 | In Progress | a converted wave (roll-up split 2026-10-01 09:55 PT) still carrying the block |
| DRE-4666 | Intake | carries the block |
| DRE-4753 | Todo | a false match: this card quotes the marker in its own instructions |

## Observation 7 — the checks on `main`

Run at **13:22:47 PT** on bureau-pipeline `main` at `518ac246419734d0bf4f09465c62f5bde4334cef`, with blank credentials (`GITHUB_ACTIONS=true`, empty `AWS_*`, `LINEAR_API_KEY=test`):

```
--- pytest tests/test_no_wave_markers.py tests/test_epic_split_scenario.py
20 passed, 6 subtests passed in 6.63s
--- python3 scripts/planning_shape.py check
3 shape(s) checked against the lane contract, 0 problem(s)
--- python3 scripts/planning_route.py check
3 route(s) checked against the shape vocabulary and the lane contract, 0 problem(s)
--- python3 scripts/planning_escalation.py check
20 label(s) and the planner workflow checked for a way past Planning, 0 problem(s)
--- python3 scripts/check_workflow_prompts.py
ok: 25 inline agent prompt(s), all lane names live
```

All five exit 0. **Verdict: proven.**

## What is not proven, and why

- **A fresh roll-up split (observations 2 to 4).** The one card sent through the run was classified as an epic. The window stays open until 2026-10-10 13:23 PT for a card the classifier does call a roll-up. If none comes, the record is closed with these observations marked not observed, under the card's own rule.
- **The sweep's half (observations 4 and 5).** `reconcile.yml` is disabled in the sandbox and was not approved to come back on.
- **DRE-3530's hand conversion (observation 6).** It is a production epic edit and is left to the operator session that owns DRE-3530.

## Where each source disagreed, and which was taken

| Fact | Source A | Source B | Taken |
|---|---|---|---|
| The card to observe | The card: DRE-4915, a production card | The CEO's rule of 2026-10-02: proofs run in the sandboxes | The sandbox substitute DRE-5740, named here with why it qualifies |
| "No `🧩 roll-up-split:` receipt on any card, ever" | The operator's comment on this card, 2026-09-30 17:45 PT | The console database: three receipts on 2026-10-01 (DRE-3530, DRE-4669, DRE-3681) | Both true at their time. The three came the next morning, on wave conversions |
