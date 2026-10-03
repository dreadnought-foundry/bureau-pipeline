# Both critics before Green Light — proof for DRE-5346 (epic DRE-5268)

**Status: PARTIAL. The new order works on real plans.** One epic came back from the second critic, was revised, re-reviewed on its own and reached Green Light passed. Another passed both critics first time. Each was approved once and stayed approved. Three things were **not observed**:
- a child promoting after the Approve: the sandbox's work-in-progress cap is 0;
- the hand-over of production epics approved under the old rule (part 2): that needs edits to production epics, which this proof does not make;
- the second critic's announce naming a rewritten artifact on a portal: the sandbox has no portal, so the run announced the fresh path instead.

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
| An epic sent back by the second critic before Green Light, revised, re-reviewed with nobody posting the act, reaching Green Light passed, with the run's announce naming the rewritten artifact; epic id, round records verbatim, run ids, PT times | **Met** on DRE-5740 (§1). The announce names the run that holds the rewritten artifact; there is no portal on this repo |
| The single Approve moved it to In Progress and a child promoted; no later comment or lane move returned it to Green Light | **Half met.** One Approve each, both moved to In Progress and activated, and neither returned to Green Light (read through 14:05 PT). **No child promoted**: the sandbox's WIP cap is 0 (§2) |
| Each old-rule epic in Green Light moved to Planning by the operator and through both critics; each old-rule In Progress epic recorded under one of three readings; DRE-2702 took the new order | **Not observed.** It needs a hand move of production epics, which this sandbox proof does not make. The rows still standing are listed in §3 |
| Every Green Light row that day by kind; `green_light_rows.py check` exits 0 on `main` at a named sha | **Met** (§3) |
| The CEO closes this card after reading the record | Open. Under the CEO's rule of 2026-10-02 a proof closes on evidence, and this one is partial |

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

## What is not proven, and why

- **A child promoting after the one Approve.** The sandbox's WIP cap is 0 by the CEO's standing hold on it. Seeing a promotion needs that cap raised, or a production epic.
- **Part 2, the old-rule epics.** Moving DRE-5464 and the others back through Planning is an edit to production epics, which this proof does not make. DRE-5464 is still in Green Light with an old-rule send-back. DRE-3698 holds an escalation that its operator note calls a defect.
- **A portal-hosted artifact.** The sandbox has no portal, so the announce names the run that holds the artifact. That is the "fresh path" the card expects for a repository with no portal.
- **A hand-off to the second critic that dropped, a review waiting in the planner line, or a park at a critic's round bound.** None happened, so none is shown.

## Where each source disagreed, and which was taken

| Fact | Source A | Source B | Taken |
|---|---|---|---|
| DRE-5740's In Progress time | the coordinating session's move, 14:02:10 PT (lane history) | the activate run's own `DRE-5740 → In Progress`, 14:03:05 PT | 14:02:10. The run's write was a no-op on a card already there |
