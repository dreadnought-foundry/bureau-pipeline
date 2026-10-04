# Both critics before Green Light — proof for DRE-5346 (epic DRE-5268)

**Status: PARTIAL, two passes.** The new order works on real plans, on the release the fleet runs today.
- **2026-10-03, on `stable` = `518ac24` (§1–§4).** One epic was sent back by the second critic, revised, re-reviewed on its own and reached Green Light passed. Another passed both critics first time. Each was approved once and stayed approved.
- **2026-10-04, on `stable` = `af890aa` (§5).** A fresh sandbox epic was sent back by the first critic, revised and re-read with nobody acting. The run itself handed it to the second critic, which passed it, and it reached Green Light carrying both PASS records. It was never approved.

**Not observed:**
- **a child promoting after an Approve.** It is WAITING for the CEO's next real Approve on the live board; the read-off steps are in §8;
- **the operator moving the 2026-09-30 old-rule Green Light epics to Planning.** They were approved and reviewed under the old rule before DRE-5281 landed, so that window has closed. Where each one stands today is in §7;
- **an artifact on a portal.** The sandbox has no portal, so the run announced the fresh path instead.

**Where this ran, and why.** It ran on the sandbox, `dreadnought-foundry/agent-bureau-demo`, under the CEO's rule of 2026-10-02: a proof runs in the sandboxes and closes on evidence. The epics observed are two sandbox fixtures filed for this proof:
- DRE-5741, a small epic with one deliberately vague child;
- DRE-5740, a five-piece product card filed for DRE-4753.

They were planned by the sandbox's own `plan.yml`, which rides `stable` = bureau-pipeline `518ac24`. That commit carries every sibling: DRE-5281 was Done 2026-10-02 10:45 PT, and DRE-5284 10:14 PT.

**How this was recorded.** A proof-runner session on the operator's instruction recorded this on 2026-10-03, 13:22–14:10 PT. The comment threads were read from the console database (`make db-read`). The runs were read from the Actions pages. The two lane moves a person makes were made by a coordinating session with the operator key, at the times given:
- each epic into Planning;
- each Approve, a scripted move Green Light → In Progress that stands in for the CEO's click.

Every time is Pacific (PDT).

| Criterion | Result |
|---|---|
| An epic sent back by the second critic before Green Light, revised, re-reviewed with nobody posting the act, reaching Green Light passed, with the run's announce naming the rewritten artifact; epic id, round records verbatim, run ids, PT times | **Met** on DRE-5740, 2026-10-03 (§1). The announce names the run that holds the rewritten artifact; this repo has no portal. **Re-observed on 2026-10-04 on `af890aa`** (§5) for everything except the second critic's own send-back: DRE-5776 was sent back by the first critic, revised, re-read with nobody acting, handed to the second critic by the run itself, passed, and reached Green Light |
| The single Approve moved it to In Progress and a child promoted; no later comment or lane move returned it to Green Light | **Half met, the rest WAITING.** On 10-03 one Approve each moved two sandbox epics to In Progress, and neither returned to Green Light (§2). No child promoted, because the sandbox's WIP cap is 0. The promotion is to be read off the CEO's next real Approve; the candidates and the read-off steps are in §8 |
| Each old-rule epic in Green Light moved to Planning by the operator and through both critics; each old-rule In Progress epic recorded under one of three readings; DRE-2702 took the new order | **Not met.** None of the five 09-30 Green Light epics was moved to Planning for the review it missed: four were approved and reviewed under the old rule before DRE-5281 landed, and DRE-4666 went back to Intake. The In Progress epics read as (i) DRE-5129, and (ii) then (i) DRE-5240 and DRE-4626, each with one return to Green Light on 09-30. DRE-2702 took the first critic from Planning, then the old post-approval critic. All of it was read on 10-04 (§7) |
| Every Green Light row that day by kind; `green_light_rows.py check` exits 0 on `main` at a named sha | **Met** for 10-03 (§3, `518ac24`) and for 10-04 (§6; exit 0 at `af890aa`, §5). One 10-04 row, DRE-5765, has no declared kind (§6) |
| The CEO closes this card after reading the record | Open |

## 1. An epic sent back by the second critic, revised, re-reviewed on its own, and passed — DRE-5740

| PT | what happened | source |
|---|---|---|
| 13:23:13 | Moved Intake → Planning (the one hand act) | coordinating session |
| 13:24:10 | `🧩 planning-shape: **epic**`, by the planner | DRE-5740 thread |
| 13:39:57 | The planner's plan: nine cards, eight that build and one proof | DRE-5740 thread, run [37151355680](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37151355680) |
| 13:41:37 | `plan-critic: stage=pre round=1 result=PASS collisions=0` | thread |
| 13:42:02 | `📨 The first critic passed this plan, so it is now with the second critic, which reads it before it can reach Green Light. The epic stays in Planning, and nothing here is yours to decide yet.` | thread |
| 13:44:33 | **Second critic sends it back.** `plan-critic: stage=post round=1 result=SEND_BACK collisions=0 — DRE-5748, DRE-5751 collide with DRE-5741's DRE-5742: both create `src/features.ts` with different exports and both rewrite the same features list in `src/App.tsx` and `src/App.test.tsx`, and nothing orders the two epics.` | thread, run [37152479620](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37152479620) |
| 13:44:32 | `🛑 **Second critic — before the CEO reads it** — round 1: sent back — round 1 of 2: the planner is revising the plan, and the review re-runs on its own — nothing here waits on anyone` | thread |
| 13:55:15 | The revision re-checked against the plan's own gates: `🔎 **Mechanical plan checks** — 9 card(s), run before the critic reads the plan.` | thread |
| 13:55:17 | **The re-review, asked by the run itself:** `asked dreadnought-foundry/agent-bureau-demo for DRE-5740's second critic's review (re-review, trigger state 'planning')` | run 37152479620 log |
| 13:55:18 | `🔁 The second critic sent the plan back and the planner has revised it. … All seven are fixed in this revision. No card was added and none was removed: the same nine cards stand, eight of them rewritten in place, and two new ordering rules were put on the board.` | thread |
| 13:55:41 | **The rewritten artifact, uploaded and announced:** `Artifact plan-artifact-DRE-5740 successfully finalized. Artifact ID 11284911930` / `📄 The plan artifact is built. No document portal URL is configured for this repo, so read it from the run: https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37152479620` | run log, thread |
| 13:56:02 | Re-review run claims its slot: `🎟️ planner-slot: claimed · card DRE-5740 · run 37153263254 · … trigger planning` | thread, run [37153263254](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37153263254) |
| 13:58:00 | **Round 2:** `plan-critic: stage=post round=2 result=PASS collisions=0 open=0` | thread |
| 13:58:06 | **Green Light**, and `✅ Ready for you in Green Light. Both critics passed this plan: the first critic's record says PASS, and so does the second critic's. Approve starts the build; nothing is building until you do.` | thread |

Nobody posted `▶️ re-run the review`, and nobody dispatched a run by hand between the send-back and the pass.

**The other epic, DRE-5741, passed both critics first time.** Moved to Planning 13:23:15. `plan-critic: stage=pre round=1 result=PASS collisions=0` at 13:34:11. `plan-critic: stage=post round=1 result=PASS collisions=0` at 13:36:09. Green Light at 13:36:12. Its one deliberately vague child, DRE-5742 ("make the features list better and also the rest of the page … should be quick"), never reached a critic in that form. The planner rewrote it first: "The epic arrived with this card already under it … with no way to tell when it was done. I rewrote it." So a defective child is repaired by the planner before review. That is why this proof's send-back came from a cross-epic collision rather than from the defect it planted.

## 2. One Approve, and nothing sent it back

| epic | Approve (scripted Green Light → In Progress) | activated | promotion | back to Green Light? |
|---|---|---|---|---|
| DRE-5741 | 13:37:27 PT | 13:38:21 `▶️ Epic activated (2 children)`, run 37152213333 | `promotion: WIP at cap (0/0) — no card is dispatched this sweep` · `0 card(s) promoted` | **No.** Lane history: Intake → Planning → Green Light → In Progress, nothing after |
| DRE-5740 | 14:02:10 PT | 14:03:06 `▶️ Epic activated (9 children)`, run 37153686962 | the same `WIP at cap (0/0)` lines, `0 card(s) promoted` | **No.** Lane history: Intake → Planning → Green Light → In Progress, nothing after |

Lane histories were read from the console database at 14:05 PT. **No child promoted, and that is the sandbox's own setting, not the mechanism.** The sandbox's plan and reconcile stubs pass `max_wip: "0"`, the hold the CEO put on this repo on 2026-09-08 (sandbox PR #11, "hold builds here (max_wip 0) until Bureau is solid"). The activate route read it (`promotion: WIP cap 0, read from the caller's reconcile stub`) and dispatched nothing. One small wording finding: both activation receipts say "the dependency gate will flow them to build in order. Nothing else to do", while at a cap of 0 nothing will flow.

**Approvals since DRE-5281 merged that did not start a build.** Every epic that moved Green Light → In Progress after 2026-10-02 10:45 PT, read from the console database's lane history, with any child moving to Todo or later afterwards:

```
card      moved (UTC)                prev → state             child moves after   first child start
DRE-5741  2026-10-03T20:37:27.976Z   Green Light → In Progress   0
DRE-5740  2026-10-03T21:02:10.249Z   Green Light → In Progress   0
```

Two approvals did not start a build. Both are this proof's scripted sandbox approvals, held by the sandbox's cap of 0. No production epic was approved in that window.

## 3. Every Green Light row on 2026-10-03

Every card that sat in or entered Green Light on 2026-10-03 (from 00:00 PT), read from the console database at 14:07 PT:

| card | repo | kind | why it is there |
|---|---|---|---|
| DRE-5740 | agent-bureau-demo | **passed plan** (sandbox) | both critics passed, 13:58 PT; approved 14:02 PT |
| DRE-5741 | agent-bureau-demo | **passed plan** (sandbox) | both critics passed, 13:36 PT; approved 13:37 PT |
| DRE-3698 | agent-bureau | **the planner's question (escalation)** | `🙋 planning-escalation` 10-02 12:33 PT. The operator's note at 12:35 PT calls it a pipeline defect: the second critic's automatic re-review ran the classifier instead of the critic |
| DRE-5672 | portico | **the planner's question** | `plan-critic: stage=one-off round=1 result=QUESTION` 10-02 17:20 PT. It asks whether no live production portal may be loaded |
| DRE-5519 | bureau-pipeline | **the planner's question (escalation)** | the classifier failed three times on `error_max_turns`. Card text fixed by the operator 10-02 13:51 PT |
| DRE-5464 | portico | **an old-rule epic, bounced after approval** | approved under the old rule. `plan-critic: stage=post round=1 result=SEND_BACK` 10-02 11:11 PT, then `🛑 The post-approval review found a gap, so nothing has started building` at 11:34 PT, and it has sat in Green Light since. This is one of the part 2 epics this proof did not move |

**The writers check, on `main` at `518ac246419734d0bf4f09465c62f5bde4334cef`**, run 13:22:47 PT with blank credentials (`GITHUB_ACTIONS=true`, empty `AWS_*`, `LINEAR_API_KEY=test`):

```
$ python3 scripts/green_light_rows.py check
  plan.yml#Epic → Green Light — both critics passed (.github/workflows/plan.yml:3860): passed-plan
5 write(s) into Green Light discovered; 5 arrival(s) declared; 0 problem(s)
  not checkable from here: operator
exit 0
```

## 4. The numbers

`python3 scripts/plan_critic.py rate --stage <s>`, run on bureau-pipeline `main` at `518ac24` over DRE-5740's three round records (the marker comments above, as a JSON list on stdin):

```
--- rate --stage post
{"rounds": 2, "send_backs": 1, "rate": 0.5}
--- rate --stage pre
{"rounds": 1, "send_backs": 0, "rate": 0.0}
```

DRE-5741: post 1 round, 0 send-backs; pre 1 round, 0 send-backs, read from its markers the same way.

## 5. The 2026-10-04 pass: a fresh epic through both critics to Green Light, on `stable` = `af890aa`

**Why a second pass.** §1–§4 were observed on 2026-10-03 against `stable` = `518ac24`. This pass repeats the plan → first critic → second critic → Green Light path against the release the fleet runs today, `stable` = `af890aa` (the sandbox's plan run logs `Uses: dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@refs/tags/stable (af890aadc1e8ddade86780a370ff0102607e85af)`). Nobody approved the fixture, and the sandbox's WIP cap stayed at 0.

**How this pass was recorded.** A proof-helper session on the operator's instruction recorded it on 2026-10-04, 10:16–11:10 PT. Unlike 10-03, the comment threads were read from Linear directly, once per step, not from the console database. The runs were read with `gh run view --log`. This helper made no lane moves on DRE-5776. The fullstack session made the two hand acts: the move into Planning and the cancel.

**The fixture.**
- DRE-5776, `SANDBOX FIXTURE (proof DRE-5346): [EPIC] the demo playground lists its real features, with a count and a filter`, labeled `repo:agent-bureau-demo`, `agent:planner`, `initiative:bureau`.
- It was filed with one child already under it, DRE-5777. The child is what makes the planner read it as an epic.
- The fullstack session filed it on the CEO's approval and moved it Intake → Planning at 10:32 PT.
- The planner added DRE-5779 (count line and filter) and DRE-5780 (the plan's proof card).

**An earlier attempt that took the wrong route.** Fixture DRE-5769 was filed at 10:18 PT with no child. Plan run [37220025771](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37220025771) read it as a **one-off**, and the one-off critic sent it back at 10:22 PT (`plan-critic: stage=one-off round=1 result=SEND_BACK`). That route ends in Todo and never meets the two plan critics, so the fixture was canceled at 10:26 PT. Its revise run, [37220461531](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37220461531), hit the one-off bound at round 2 and tried to park the card in Triage. The move was refused: `DRE-5769 is 'Canceled' (terminal) — refusing to move it to 'Triage'; a finished card is ground truth and is never reopened by an automated transition.` Lesson for the next fixture: an epic fixture needs a child already filed under it.

**The timeline**, read off DRE-5776's thread (one read at 11:02 PT) and the two runs' logs:

| PT | what happened | source |
|---|---|---|
| 10:32 | Intake → Planning (the one hand act) | fullstack session |
| 10:33:15 | Plan run created; it queued for ten minutes behind other sandbox plan runs | run [37220948282](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37220948282) |
| 10:43:19 | `🎟️ planner-slot: claimed · card DRE-5776 · run 37220948282 · … trigger planning` | thread |
| 10:43:33 | `🧩 planning-shape: **epic**` — "The card already has children, which outranks the body" | thread |
| 10:43:37 | `📋 A fresh planning attempt on DRE-5776 starts here. Both critics count their rounds from this point …` | thread |
| 10:52:46 | The planner's plan: three cards, DRE-5777 → DRE-5779 in sequence (they share `src/FeaturesList.tsx`), and the proof DRE-5780 last | thread |
| 10:53:10 | `🔎 **Mechanical plan checks** — 3 card(s) …` with two findings: `src/FeaturesList.test.tsx` and `src/FeaturesList.tsx` `touched by DRE-5779, DRE-5777 — siblings must own disjoint files` | thread |
| 10:53:50 | **First critic sends it back.** `plan-critic: stage=pre round=1 result=SEND_BACK collisions=0 — the KPI baseline says the page has one automated test, but `src/App.test.tsx` on `main` holds four, so the tracked baseline and the "three become seven" outcome are wrong` | thread, run log 10:53:49 |
| 10:53:49 | `🛑 **First critic — before the CEO reads it** — round 1 of 2: sent back`, with three ranked findings (the test baseline, the KPI built on it, and the existing placeholder test the change breaks) | thread |
| 10:56:30 | The revision, re-run through the mechanical checks (same two sibling-file findings; all three cards now in Backlog) | thread |
| 10:57:09 | **First critic, round 2:** `plan-critic: stage=pre round=2 result=PASS collisions=0` | thread |
| 10:57:14 | `📨 The first critic passed this plan, so it is now with the second critic, which reads it before it can reach Green Light. The epic stays in Planning, and nothing here is yours to decide yet.` | thread |
| 10:57:14 | **The hand-off, asked by the run itself:** `asked dreadnought-foundry/agent-bureau-demo for DRE-5776's second critic's review (review, trigger state 'planning')` | run 37220948282 log |
| 10:57:44 | `📄 The plan artifact is built. No document portal URL is configured for this repo, so read it from the run: …/runs/37220948282` (artifact `plan-artifact-DRE-5776`, ID 11310786045) | thread, run log |
| 10:57:16 / 10:58:38 | Second critic's run created / `🎟️ planner-slot: claimed · card DRE-5776 · run 37222476204 · … trigger planning` | run [37222476204](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37222476204), thread |
| 11:02:14 | `✅ **Second critic — before the CEO reads it** — round 1: the critic passed this plan` | thread |
| 11:02:15 | **Second critic:** `plan-critic: stage=post round=1 result=PASS collisions=0` | thread, run log 11:02:14 |
| 11:02:19 | `DRE-5776 → Green Light` | run 37222476204 log |
| 11:02:20 | `✅ Ready for you in Green Light. Both critics passed this plan: the first critic's record says PASS, and so does the second critic's. Approve starts the build; nothing is building until you do.` | thread |
| 11:03 | Sandbox reset: the fullstack session canceled DRE-5776, DRE-5777, DRE-5779 and DRE-5780, about a minute after the epic arrived. It was never approved | fullstack session's report |

Nobody posted `▶️ re-run the review`, and nobody dispatched a run by hand at any point between 10:32 and 11:02 PT.

**What this pass adds, and what it does not.**
- **It adds:** the whole new order, observed again on today's release. The first critic's send-back was revised and re-read with nobody acting. The plan was then handed to the second critic by the run itself, and reached Green Light only after both PASS records were on the thread, with the artifact announced from the run.
- **It does not add:** a send-back *by the second critic*. The second critic passed DRE-5776 first time. That half of criterion 1 rests on DRE-5740 (§1, 2026-10-03, `stable` = `518ac24`).
- **One observation for the critic's charter, not a defect claim.** The mechanical checks flagged two files shared by sibling cards DRE-5777 and DRE-5779 ("siblings must own disjoint files"). Both critics passed the plan anyway. The plan orders the two cards one after the other, and the checks call themselves "the INPUT to its judgement, not a verdict of their own". So the critics read the finding as answered by the ordering.

**The numbers for DRE-5776.** `python3 scripts/plan_critic.py rate --stage <s>`, run on bureau-pipeline `af890aa` with blank credentials, over the three marker comments above as a JSON list on stdin:

```
--- rate --stage post
{"rounds": 1, "send_backs": 0, "rate": 0.0}
--- rate --stage pre
{"rounds": 2, "send_backs": 1, "rate": 0.5}
```

**The writers check on `af890aa`**, run 10:19 PT with blank credentials (`GITHUB_ACTIONS=true`, empty `AWS_*`, `LINEAR_API_KEY=test`) on a source archive of `af890aadc1e8ddade86780a370ff0102607e85af`:

```
$ python3 scripts/green_light_rows.py check
  code_owner_hold.py#park (scripts/code_owner_hold.py:450): agent-escalation
  planning_escalation.py#escalate (scripts/planning_escalation.py:828): question
  planning_route.py#_cmd_exit (scripts/planning_route.py:818): passed-plan
  agent-task.yml#Report result to Linear (.github/workflows/agent-task.yml:1955): agent-escalation
  plan.yml#Epic → Green Light — both critics passed (.github/workflows/plan.yml:3860): passed-plan
5 write(s) into Green Light discovered; 5 arrival(s) declared; 0 problem(s)
  not checkable from here: operator
exit 0
```

## 6. Every Green Light row on 2026-10-04

The Green Light lane, read once at 11:02 PT (Linear, `state = Green Light`, 11 rows). The five epics returned at 10:24 PT were read once more at 11:03 PT for their critic records and lane history.

| card | repo | kind | why it is there |
|---|---|---|---|
| DRE-5776 | agent-bureau-demo | **passed plan** (sandbox) | both critics passed, 11:02 PT (§5); canceled 11:03 PT |
| DRE-5036 | agent-bureau | **old-rule epic, returned from In Progress** | moved In Progress → Green Light 10:24:37 PT; newest critic record `stage=post round=4 result=SEND_BACK collisions=2` (10-01 20:28 PT); 9 children, all Backlog |
| DRE-5035 | agent-bureau | **old-rule epic, returned from In Progress** | moved In Progress → Green Light 10:24:35 PT; it went Planning → In Progress on 10-01 15:28 PT, and its newest record is `stage=post round=2 result=SEND_BACK collisions=1 open=6` (10-01 17:18 PT); 10 children, all Backlog |
| DRE-3700 | agent-bureau | **old-rule epic, returned from In Progress** | moved In Progress → Green Light 10:24:19 PT; pre round 1 PASS 10-02 00:10 PT, approved 07:57 PT, then `stage=post round=1 result=PASS collisions=0` at 09:16 PT, a post-approval PASS under the old rule; 3 children, all Backlog |
| DRE-3699 | agent-bureau | **old-rule epic, returned from In Progress** | moved In Progress → Green Light 10:24:34 PT; pre round 1 PASS 10-02 01:58 PT, approved 07:57 PT, **no post record**; 8 children, all Backlog |
| DRE-3696 | agent-bureau | **old-rule epic, returned from In Progress** | moved In Progress → Green Light 10:24:33 PT; pre round 1 PASS 10-01 23:29 PT, approved 10-02 07:57 PT, **no post record**; 7 children, all Backlog |
| DRE-3698 | agent-bureau | **the planner's question (escalation)** | the planner asks a question, posted 10-02 12:33 PT (as on 10-03, §3) |
| DRE-5519 | bureau-pipeline | **the planner's question (escalation)** | the planner asks a question, posted 10-01 21:42 PT (as on 10-03) |
| DRE-5672 | portico | **the planner's question** | posted 10-02 17:20 PT (as on 10-03) |
| DRE-5464 | portico | **old-rule epic, bounced after approval** | newest record `stage=post … result=SEND_BACK` 10-02 11:11 PT (as on 10-03) |
| DRE-5765 | agent-bureau | **no declared kind** | `What broke the agents — week ending 2026-10-03 (PT)`, `no-code`, `agent:ops`. The hygiene agent's 10:21 PT dry pass reports it as `it carries no planning receipt this lane reads`. It is none of the four declared kinds, and the writers check (which reads the code's writes, not the board) cannot see who put it there |

DRE-3699, DRE-3696, DRE-3700 and DRE-3698 are children of DRE-3681. The coordinating session reports that the five 10:24 PT returns were the CEO's. This pass did not read the actor of those moves.

## 7. Where each 2026-09-30 old-rule epic stands on 2026-10-04 (criterion 3, read only)

Read at 10:34–10:35 PT in two read-only Linear queries: lane, children by lane, every lane move, and every `plan-critic:` record on the thread. **The histories are complete.** Each epic's `history` returned `hasNextPage: false`, newest first. The longest is DRE-3681 at exactly 50 entries. So no newest move is missing. DRE-5281 merged 2026-10-02 10:45 PT; everything below happened before it unless the row says otherwise.

| epic | lane today | children | the round records and moves that decide it (PT) | reading |
|---|---|---|---|---|
| DRE-5129 | In Progress | 5 Done, 5 Backlog, 1 Hand-work, 2 Canceled | approved 09-29 16:27, bounced 16:36; approved 22:05, post r1 SEND_BACK 22:09, bounced 22:26; approved 09-30 07:10, **post r2 PASS 07:40** | (i) newest post round PASS; children promoted (5 Done) |
| DRE-5240 | In Progress | 11 Done, 1 Backlog, 1 Canceled | In Progress → Planning 09-30 07:12; pre r1 SEND_BACK 07:34, pre r2 SEND_BACK 07:47, Planning → Green Light 07:47; approved 09:50, post r1 SEND_BACK 09:58, **back to Green Light 10:16**, re-approved 11:53, **post r2 PASS 12:21** | (ii) then (i): the old cycle ran on it before DRE-5281; one return to Green Light (10:16 → 11:53) |
| DRE-4626 | In Progress | 12 Done, 3 Backlog, 5 Canceled | post r3 SEND_BACK 09-30 07:51; approved 11:53; In Progress → Planning 12:10; post r1 SEND_BACK 10:22 (fresh attempt); Planning → In Progress 13:55; post r2 SEND_BACK 14:00; **back to Green Light 14:14**, re-approved 14:40, **post r3 PASS 15:26** | (ii) then (i), old cycle; one return to Green Light (14:14 → 14:40) |
| DRE-4723 | In Progress | 9 Done, 1 Backlog, 2 Canceled | pre r1 PASS 09-30 11:21, Green Light 11:21; approved 17:42, post r1 SEND_BACK 17:50, post r2 SEND_BACK 18:21, **back to Green Light 18:32**, re-approved 18:43, **post r3 PASS 18:48** (`collisions=2`) | was in Green Light at the 09-30 read; approved and passed under the old rule, not moved to Planning by the operator |
| DRE-4267 | In Progress | 25 Done, 1 In Progress, 1 Backlog, 2 Canceled | pre r1 SEND_BACK 09-30 10:38, pre r2 PASS 10:58, Green Light 10:58; approved 10-01 12:32, post r1 SEND_BACK 12:38, **post r2 PASS 13:35**, no bounce | in Green Light at the 09-30 read; approved and passed under the old rule |
| DRE-3622 | **Done** (10-01 17:22) | 7 Done, 4 Canceled | Green Light 09-30 16:48; out and back 17:39 → 17:40; approved 10-01 06:12, post r2 SEND_BACK 06:36, **post r3 PASS 07:17** | in Green Light at the 09-30 read; approved, passed and finished under the old rule |
| DRE-4666 | **Intake** (since 09-30 13:57) | 2 In Progress, 2 Done, 1 Backlog | Planning → Green Light 09-30 10:18, Green Light → Intake 13:57; **no `plan-critic:` record on the thread** | in Green Light at the 09-30 read; taken out of Green Light to Intake, not through Planning |
| DRE-3681 | In Progress (since 10-01 14:09) | 4 Green Light, 1 In Progress, 3 Done | Green Light → Planning 10-01 09:43, back 12:35; → Intake → Planning 12:40; Planning → In Progress 14:09; **no `plan-critic:` record on the thread** | in Green Light at the 09-30 read; now a roll-up whose child epics (DRE-3696, 3698, 3699, 3700) sit in Green Light (§6) |
| DRE-2702 | In Progress | 5 Done, 1 Hand-work | Green Light → Planning 09-30 07:14; pre r1 SEND_BACK 11:58, **pre r2 PASS 12:09**, Green Light 12:09; approved 17:42; **post r1 PASS 17:45** | took the first critic from Planning, then the old post-approval critic, which passed |

**What this shows for criterion 3.** None of the five Green Light epics of 2026-09-30 was moved to Planning by the operator for the review it missed. Four were approved and reviewed under the old rule before DRE-5281 landed (DRE-4723, DRE-4267, DRE-3622 and, through its roll-up, DRE-3681). DRE-4666 went back to Intake. The three In Progress epics read as (i) DRE-5129, and (ii) then (i) DRE-5240 and DRE-4626: each has one return to Green Light, and each one happened on 09-30, before DRE-5281. The criterion as written is therefore **not met**, and the record cannot make it met after the fact: the window it describes closed when those epics were approved under the old rule. The new rule's own test case is now on the board, though: the five epics returned to Green Light at 10:24 PT today (§6), three of which have no second-critic PASS on their current attempt.

## 8. Criterion 2 — WAITING for the CEO's next real Approve

The sandbox cannot show a child promoting while its WIP cap is 0, and this proof approves nothing. Criterion 2 is therefore read off the CEO's next real Approve on the live board. **The candidates are the five epics returned to Green Light at 10:24 PT (§6).** What each should do under DRE-5281, from the records above:

- **DRE-3700** carries `stage=post round=1 result=PASS`. Approve should move it to In Progress and promote its first unblocked child. This is the clean case for criterion 2.
- **DRE-3699 and DRE-3696** carry no post record. **DRE-5036 and DRE-5035**'s newest post record is a SEND_BACK. For each of these, Approve should take the hand-back: to Planning, for the review it missed, with the note that says so. The plan returns passed for one more Approve. That bounce is the exception criterion 3 allows; it is not a criterion-2 failure.

**Read-off, after the Approve.** Read once per step:
1. **The In Progress move.** One read of the epic's history: `AWS_PROFILE=dreadnought python3 <scratch>/oldrule.py <n>`, the read-only query used for §7, or the Linear history panel. Record the Green Light → In Progress time (PT), and the actor if the hand-back fired instead.
2. **The first unblocked child promoting.** Read the activate run's `▶️ Epic activated (<k> children)` line on the epic's thread, then the next sweep's promotion lines in the repo's Reconcile run log: `gh run list --repo dreadnought-foundry/agent-bureau --workflow Reconcile --limit 3`, then `gh run view <id> --repo dreadnought-foundry/agent-bureau --log | grep -E "promotion:|promoted"`. Record the child, its Backlog → Todo time, and the run id.
3. **No bounce back to Green Light.** One more history read of the same epic at least one sweep (15–20 minutes) after the promotion, confirming nothing after In Progress returns it to Green Light. Record the time of that read.

## What is not proven, and why

- **A send-back by the second critic, on `af890aa`.** On 10-04 the second critic passed DRE-5776 first time. Its send-back path rests on DRE-5740 (§1), observed on `518ac24`.
- **A child promoting after the one Approve.** The sandbox's WIP cap is 0 by the CEO's standing hold on it. This proof approves no production epic, so the promotion waits for the CEO's next real Approve (§8).
- **Part 2 as the card wrote it.** The operator never moved the 09-30 old-rule Green Light epics to Planning. They were approved under the old rule first, which closed that window (§7). The new rule's hand-back will be seen instead on whichever of the five epics returned to Green Light on 10-04 the CEO approves without a second-critic PASS (§8).
- **A portal-hosted artifact.** The sandbox has no portal, so the announce names the run that holds the artifact (§1, §5). That is the "fresh path" the card expects for a repository with no portal.
- **A hand-off to the second critic that dropped, a review waiting in the planner line, or a park at either plan critic's round bound.** None of these happened, so none is shown. The 10-04 one-off fixture DRE-5769 did hit the one-off critic's bound, and its park in Triage was refused because the card was already canceled (§5). That is a different critic, so it is noted here and not counted.

## Where each source disagreed, and which was taken

| Fact | Source A | Source B | Taken |
|---|---|---|---|
| DRE-5740's In Progress time | the coordinating session's move, 14:02:10 PT (lane history) | the activate run's own `DRE-5740 → In Progress`, 14:03:05 PT | 14:02:10. The run's write was a no-op on a card already there |
