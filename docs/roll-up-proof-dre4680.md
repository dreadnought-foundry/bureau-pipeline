# Roll-up proof — DRE-4753 (epic DRE-4680)

**Status: PARTIAL — two passes. The second pass (2026-10-04) saw the roll-up route run end to end on a fresh card. It also found a defect: each child epic was sent back into the roll-up route instead of being planned.** The second pass is in [§ Pass 2](#pass-2--2026-10-04-a-fresh-card-takes-the-roll-up-route).
- **Pass 1 (2026-10-03).** Sandbox fixture DRE-5740 is a five-piece product across two tiers. It was stamped `epic`, not `roll-up`, so observations 2 to 4 were *not reached* in that pass. The finding: a size of "about eight build cards" alone stays below the line.
- **Pass 2 (2026-10-04).** Sandbox fixture DRE-5770 carries the seam tells: a supervised release, then a second surface after seven clean days.
  - **Observations 1 and 2 are proven on bureau-pipeline `af890aa`.** The run stamped it `roll-up` on its own, split it into two `[EPIC]` children, posted one `🧩 roll-up-split:` receipt naming them in blockedBy order, and moved the parent to In Progress.
  - **Observation 3 is not met.** Each child entered Planning and got its own planner run. The deterministic seam floor then restamped each child `roll-up`, even though the model's own reading said it was not one. Both children were sent back into the split route and both planner runs died. No child reached a plan in Green Light.
- **Observations 4 and 5 (the sweep's skip line and a parent closed by the sweep) are NOT OBSERVABLE in the sandbox while its WIP cap is 0** (§ Pass 2, observations 4 and 5). Observation 5 also still waits on DRE-4669's four open child epics in production.
- **Observation 6** waits on a hand edit of a production epic, which is the operator's.
- **Observation 7** (the checks on `main`) is green in both passes.

**The card that went through the run, and why it is not DRE-4915.** The card names DRE-4915, a production card in Intake. Moving it into Planning is a write to a production card. Under the CEO's rule of 2026-10-02, proofs run in the sandboxes, so a sandbox substitute went through the run instead: **DRE-5740**, filed for this proof in `dreadnought-foundry/agent-bureau-demo`. It arrived at the run with no shape stamp. It qualifies against the size tells in `standards/card-quality.md`:
- five separately shipping pieces across two tiers (a new Node service in `server/` and the Vite app);
- three service-to-app contracts (`GET /api/features`, the profile endpoints, the share snapshot);
- a fourth piece that reads what two others produce;
- its own body says "This is a list for Planning to cut into work. It is not one plan."

DRE-4915 was read at 14:08 PT: still in Intake, untouched.

**How this was recorded.** A proof-runner session on the operator's instruction recorded this on 2026-10-03, 13:22–14:10 PT. Runs were read from the sandbox's Actions pages. Comments and lanes were read from the console database (`make db-read`). The one hand act, observation 0, was made by a coordinating session with the operator key. Every time is Pacific (PDT). **Pipeline sha:** the sandbox's `plan.yml` rides `stable` = bureau-pipeline `518ac246419734d0bf4f09465c62f5bde4334cef`, as the run's own checkout shows (`HEAD is now at 518ac24 Merge pull request #695 …`). **Watch window:** opened 2026-10-03 13:23 PT and closes 2026-10-10 13:23 PT.

| Criterion | Result |
|---|---|
| The record on `main`, headed by the card it observed, the pipeline sha, the window's opening and closing day, and PT times for all seven observations, each quoted or marked not observed | **Met as a partial record.** Pass 2 quotes observations 0, 1, 2 and 7, and the parts of 3 and 4 that happened. The rest are marked with where they were looked for |
| What the classifier stamped on its own. If `roll-up`: two or more `[EPIC]` children, one `🧩 roll-up-split:` receipt, each child planned. If not: what the run did instead | **Partly met (pass 2).** It was stamped `roll-up`, with two `[EPIC]` children carrying `agent:planner` and one receipt, and each child entered Planning. **Not met:** neither child reached its own plan or escalation in Green Light. Each was restamped `roll-up` and its planner run died (§ Pass 2, observation 3). Pass 1 stamped `epic` (§1) |
| The parent In Progress, skipped by the sweep, nothing dispatched at it; a roll-up parent closed by the sweep, or which one the close waits on | **Partly met.** The parent was In Progress at 10:25:56 PT, and the one dispatch at it stood down as a duplicate. **Not observable in the sandbox at WIP cap 0:** the sweep's skip line and the close (§ Pass 2, observations 4 and 5). The production close still waits on DRE-4669 |
| DRE-3530's `wave-commitment` block removed by hand, and any other open epic carrying the marker listed | **Not done.** It is a production epic edit, left to the operator. The epics still carrying the marker are listed (§6) |
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

That path is recorded in full in DRE-5346's record, `docs/both-critics-before-green-light-proof.md`, §1 (on `main` since bureau-pipeline #701 merged, 2026-10-03 16:16 PT).

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

**Not observed.** It waits on DRE-4669. Read at 14:08 PT from the console database:

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

**The board search for the marker** (`description ILIKE '%BEGIN wave-commitment%'`, open cards, console database, 14:08 PT):

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

## Pass 2 — 2026-10-04: a fresh card takes the roll-up route

**How this was recorded.** A proof-helper session recorded this pass on 2026-10-04, 10:16–10:45 PT, on the operator's instruction.
- Runs were read from the sandbox's Actions logs (`gh run view --log`).
- Stamps and receipts were read once per card from Linear.
- The fixture and its observation-0 move were made by this session, in the sandbox only. Nothing in production was written.
- **Pipeline sha:** every run in this pass rode `stable` = bureau-pipeline `af890aadc1e8ddade86780a370ff0102607e85af`, which is also `main`'s head at the time. The run's checkout shows it: `Uses: …/plan.yml@refs/tags/stable (af890aa…)` and `HEAD is now at af890aa Merge pull request #712 …`.

**Why a second fixture, and why this one.** Pass 1 showed that size alone (five pieces, about eight build cards) stays under the roll-up line. The shape vocabulary (`config/planning-shapes.json`, `roll-up.why`) names the seam as the second way in: "a plan whose later cards depend on OBSERVING its earlier cards live". So **DRE-5770** was filed in `agent-bureau-demo` with the seam the standard describes. It arrived with no shape stamp, and its title carries no shape word.
- **Part A:** a changelog service, a "What's new" panel, an unread marker, a tag filter, and CI. It ends at "one supervised release of the panel … watched by a person".
- **Part B:** a feed, an email digest, a send job, and opt-in. It "joins after the first is proven", planned only "after the panel has run for seven clean days live".
- Eleven items in all. Its body says "This is a list for Planning to cut into work. It is not one plan."

### Observation 0 — the one hand act

**10:20:14 PT.** DRE-5770 was moved Intake → Planning by this session (filed at 10:20:04 PT). The planner run that followed was the pipeline's own: [37220127053](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37220127053) (`repository_dispatch` `agent-plan`, created 10:20:19 PT).

### Observation 1 — what the classifier stamped: `roll-up`

**10:20:50 PT**, on DRE-5770, by the planner (run log, 10:20:51: `DRE-5770 classified roll-up on claude-fable-5-1`):

```
🧩 planning-shape: **roll-up** — A plan too big for one epic. …
**Why:** Eleven separately shipping pieces across a new backend service, the frontend, CI and email, with Part B explicitly held until a supervised live release and seven clean days of the panel are observed, so it is two epics cut at that observation rather than one. — observation-gated seam: Cards 7–11 (feed, digest renderer, send job, opt-in/unsubscribe, their CI and README) wait on watching the operator's supervised release of the panel (card 6) and then seven clean days of it live.; the seam rule (DRE-3244) splits this into child epics under this card, the second blocked on the first — size tests checked: 1, 2, 5, 6
**Stamped by:** `planner` · **model:** `claude-fable-5-1`
```

The route decision followed at 10:20:53 PT (`🚦 planning-route: **roll-up** … Who takes it from there: plan.yml`). **Verdict: proven.**

### Observation 2 — the split step and the receipt

The `Roll-up route — split into child epics` step ran on `plan.yml` in run 37220127053 and filed two children. Then `Roll-up route — check the split` and `Roll-up route — activate the split` ran. From the run log:

```
10:25:51 PT  DRE-5770: a roll-up split into 2 child epic(s), in blockedBy order:
               1. DRE-5771
               2. DRE-5773 (after DRE-5771)
10:25:56 PT  epic split: DRE-5770 — receipt posted
             DRE-5771 → Planning
             DRE-5773 → Planning
             DRE-5770 → In Progress
             epic split: DRE-5770 active — 2 child epic(s) in order: DRE-5771, DRE-5773
```

The receipt on the parent, **10:25:52 PT**:

```
🧩 roll-up-split: DRE-5770 is split into 2 child epics, each planned and green-lit on its own, in this order:
1. **DRE-5771** — The in-app half of DRE-5770: … — nothing ahead of it
2. **DRE-5773** — The second surface of DRE-5770: … — after DRE-5771
…
📎 pipeline-act: roll-up-activated · kind: progress · state: unchanged · next: plan.yml · discharges: nothing · subscriber: reconcile.yml · tag: roll-up-split
```

The children: **DRE-5771** `[EPIC] whats-new-panel: the in-app What's new panel, through one supervised release` and **DRE-5773** `[EPIC] whats-new-digest-feed: the feed and the weekly email digest, after seven clean days live`. Both are native sub-issues of DRE-5770, both carry `agent:planner`, and neither has a build role. A plain-English plan comment was posted on the parent at 10:25:43 PT. **Verdict: proven.**

### Observation 3 — each child planned on its own: NOT MET (a defect)

**What happened.**
- **Each child entered Planning and got its own planner run:** DRE-5771 in [37220487230](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37220487230) and DRE-5773 in [37220492522](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37220492522), both created at about 10:26 PT. That part is met.
- **Neither child reached a plan or an escalation in Green Light.** Each was classified `roll-up` again, sent back into the split route, and its planner run died.

The two child stamps carry the model's reading *against* the stamp, and then the floor's evidence:

- **DRE-5771, 10:26:46 PT:** `**Why:** … it is already the first half of a roll-up cut at the release observation, with the seven-clean-days gate and the second surface explicitly pushed to the sibling, so nothing inside it waits on observing something live mid-chain. — observation-gated seam: …seven clean days live after the supervised release, and no card can be planned against seven days nobody has watched yet…`
- **DRE-5773, 10:26:52 PT:** `**Why:** … the seven-clean-days wait sits in front of the whole card (already cut by the parent as the DRE-5771 blocker), not between its own pieces, so it is not a roll-up. … — observation-gated seam: …nel from DRE-5771 has run seven clean days live following its supervised release, and it is planned in detail only then.`

**The mechanism.**
- `scripts/planning_classify.py` reads the seam off the body deterministically: `seam_evidence` (about line 770) matches `_SEAM_PHRASES` (about line 290; the first phrase is "N clean days"). By the rule in the module's docstring (about line 65), "where either says seam, an `epic` becomes a `roll-up`".
- The sentence the floor matched in each child is the one the split planner itself wrote to describe the gate *between the siblings*. So a roll-up's children carry the parent's seam in their own descriptions, the floor reads it as a seam inside them, and they re-enter the roll-up route.
- The writer (the roll-up planner brief and `epic_split`) and the reader (the seam floor) disagree about whether a child's description may name the gate in front of it.

**The two child runs.**

| child | stamp | planner step | ended |
|---|---|---|---|
| DRE-5771 | `roll-up` (10:26:46) | `Roll-up route — split into child epics`, 14 turns, $1.79 | `is_error: true` at 10:30:54 PT |
| DRE-5773 | `roll-up` (10:26:52) | the same step, 13 turns, $1.77 | `is_error: true` at 10:30:49 PT |

In both runs `Roll-up route — split into child epics — finished?` failed with `the planner step did not succeed (outcome failure on claude-fable-5-1)`, and neither filed a sub-issue: both children had no children when read at about 10:33 PT.

**Why they died is not determined here.** Three facts disagree:
- each run's own receipt says `Run death: unknown — the run died and no execution record was written`;
- the medic wrote `🪦 limit-death: kind=claude stage=plan reset=unknown` on both, at 10:31:16 PT (DRE-5773) and 10:31:28 PT (DRE-5771);
- another card's planner run in the same sandbox, in the same minutes (37220461531, DRE-5769), finished green.

At 10:33:33 PT the next sweep's limit recovery handed both children to a human: `limit-recovery: DRE-5771 handed to a human — the marker names no reset time and no account …`, and the same for DRE-5773.

**Verdict: not met.** It is a defect in the route, not a gap in the watch. Under the current seam floor, a roll-up's child epics are reclassified `roll-up` and are not planned on their own.

### Observation 4 — the parent held: partly observed; the sweep's skip line is NOT OBSERVABLE at WIP cap 0

**Observed:**
- The parent has been In Progress since 10:25:56 PT (`DRE-5770 → In Progress`, run log).
- Nothing was dispatched at it. The one dispatch that arrived, [37220490371](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37220490371) at 10:26:02 PT, stood down: `skip=true` / `reason=a planner run for DRE-5770 was already in flight when this dispatch arrived (run 37220127053) — duplicate dispatch`. Its comment on the parent (10:26:33 PT) reads "This run planned nothing and moved no lane".

**Not observable in the sandbox while its WIP cap is 0.** The sandbox's sweep, `reconcile.yml`, has been active since 2026-10-04 07:40 PT. Its passes after the split, [37220724970](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37220724970) at 10:30:28 PT and [37220932655](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37220932655) at 10:33:33 PT, each print:

```
idle: agent-bureau-demo — the WIP cap is 0, so nothing here is built or promoted; skipped promotion and this repo's work-lane phases this pass (the fleet-wide Planning and Intake phases still run)
```

and neither names DRE-5770. This is what `scripts/reconcile.py` at `af890aa` does at cap 0:
1. `sweep_idle()` returns at lines 10595–10596 (`if MAX_WIP <= 0: return "the WIP cap is …"`).
2. `board_read` therefore raises `BoardIdle` at line 10965 before `epics = repo_epics(mine)`.
3. `promote_ready` raises `BoardIdle` at line 11003.

So the skip line at 5643–5646 (`promotion: <card> is an epic — epics are promoted by humans, never by the sweep; skipping`) cannot print. The parent's absence from promotion is true, but only because promotion did not run at all.

**What would make it observable:** a WIP cap above 0 in the sandbox's `reconcile.yml` stub (`max_wip: "0"`). The stub's own comment says the cap was "HELD at 0 on 2026-09-08 by CEO decision", so lifting it is the CEO's call. This proof did not change it.

### Observation 5 — a parent closed by the sweep: NOT OBSERVABLE in the sandbox at WIP cap 0, and waiting on DRE-4669 in production

**Sandbox:**
- On the same idle path, `epics` stays empty, so `close_finished_epics(epics)` (lines 10979–10980) evaluates nothing.
- Even with a cap, the close in `_close_epic_if_finished` (lines 6035–6039) needs every child terminal **and** at least one `Done`.
- DRE-5770's children were never planned, let alone built, so no Done child could exist in this window.
- Canceling the fixture cannot fake the close: an all-Canceled parent does not satisfy line 6038.

**Production:** DRE-4669's child epics, read from Linear at about 10:23 PT:

| child epic of DRE-4669 | state |
|---|---|
| DRE-4677 | Done |
| DRE-4679 | Done |
| DRE-4678 | In Progress |
| DRE-5034 | In Progress |
| DRE-5035 | In Progress |
| DRE-5036 | In Progress |

These are unchanged since pass 1. The rule stays pinned by `tests/test_close_epics.py` (DRE-4700). **Verdict: not observed.**

### Observation 6 — DRE-3530's conversion

**Not done; the operator's edit.** This pass made no production writes. The list of epics still carrying the `wave-commitment` marker stands as in §6. Linear's full-text search for the marker in this pass is fuzzy and confirms nothing new.

### Observation 7 — the checks on `main` at `af890aa`

Run at **10:20:49 PT** on bureau-pipeline `af890aadc1e8ddade86780a370ff0102607e85af` (the `main` head and the `stable` tag, read-only tarball extract), with blank credentials (`GITHUB_ACTIONS=true`, empty `AWS_*`, `LINEAR_API_KEY=test`):

```
--- pytest tests/test_no_wave_markers.py tests/test_epic_split_scenario.py
20 passed, 6 subtests passed in 6.44s
--- python3 scripts/planning_shape.py check
3 shape(s) checked against the lane contract, 0 problem(s)
--- python3 scripts/planning_route.py check
3 route(s) checked against the shape vocabulary and the lane contract, 0 problem(s)
--- python3 scripts/planning_escalation.py check
20 label(s) and the planner workflow checked for a way past Planning, 0 problem(s)
--- python3 scripts/check_workflow_prompts.py
ok: 25 inline agent prompt(s), all lane names live
```

All five exit 0. **Verdict: proven.** These checks are green while observation 3 fails live. Neither the scenario test nor the planning checks run a split child back through the classifier.

### Cleanup

At **10:34:51–10:34:57 PT**, DRE-5771, DRE-5773 and DRE-5770 were canceled together, each with a reason comment. No grandchild cards existed. Nothing else in the sandbox was touched.

## What is not proven, and why

- **Each child planned on its own (observation 3).** The defect above stops it: the seam floor reclassifies a roll-up's child epics `roll-up`. Until the classifier and the split planner agree on how a child names the gate in front of it, no child reaches its own plan. A fix belongs on a card of its own; this proof files none.
- **Why the two child planner runs died.** The receipt says unknown, the medic says limit, and a sibling run was green.
- **The sweep's half (observations 4 and 5) in the sandbox.** Not observable while the sandbox's WIP cap is 0 (`reconcile.py` lines 10595–10596, 10965, 11003; and 6035–6039 for the close). Raising the cap is the CEO's call. In production, the close still waits on DRE-4669.
- **DRE-3530's hand conversion (observation 6).** It is a production epic edit and is left to the operator session that owns DRE-3530.

## Where each source disagreed, and which was taken

| Fact | Source A | Source B | Taken |
|---|---|---|---|
| The card to observe | The card: DRE-4915, a production card | The CEO's rule of 2026-10-02: proofs run in the sandboxes | The sandbox substitute DRE-5740, named here with why it qualifies |
| "No `🧩 roll-up-split:` receipt on any card, ever" | The operator's comment on this card, 2026-09-30 17:45 PT | The console database: three receipts on 2026-10-01 (DRE-3530, DRE-4669, DRE-3681) | Both true at their time. The three came the next morning, on wave conversions |
| Is the sandbox's sweep on? | Pass 1 (2026-10-03): `reconcile.yml` disabled | `gh api …/actions/workflows` on 2026-10-04: `active`, with passes at 10:30 and 10:33 PT | Both true at their time. In pass 2 it is on, but idle at cap 0 |
| Is a child epic a roll-up? | The model's own **Why:** on DRE-5771 and DRE-5773: "nothing inside it waits …", "so it is not a roll-up" | The seam floor's evidence on the same stamps, which made the stamp `roll-up` | The stamp, as written. The disagreement is the observation-3 finding |
| Why the child planner runs died | Run receipt: `Run death: unknown` | Medic: `limit-death: kind=claude … reset=unknown` | Neither; recorded as not determined |
