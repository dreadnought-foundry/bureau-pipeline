# The seam rule observed live — a wave stamped with the seam named, three ordinary epics stamped `epic`, and a seam inside one epic sent back at the first critic

**DRE-3399.** Read by hand on 2026-09-12 between 10:20 and 10:26 PT against a
fresh worktree of `dreadnought-foundry/bureau-pipeline` at `origin/main`
(`54ed0d4c539e18613d47c84eca1417dabde82d39`), the live GitHub Actions logs of
`dreadnought-foundry/bureau-pipeline`, and the Linear board. The proof child of
epic [DRE-3244](https://linear.app/dreadnoughtfoundry/issue/DRE-3244): what
[DRE-3391](https://linear.app/dreadnoughtfoundry/issue/DRE-3391) (the rule in
the brief and the standard),
[DRE-3394](https://linear.app/dreadnoughtfoundry/issue/DRE-3394) (the shape
classifier reads the seam tells and stamps `wave`),
[DRE-3395](https://linear.app/dreadnoughtfoundry/issue/DRE-3395) (the seam
reader) and [DRE-3398](https://linear.app/dreadnoughtfoundry/issue/DRE-3398)
(the seam is a mechanical send-back at the pre-approval critic) built, observed
against real cards rather than a green suite.

**Read-only, and no probe card was filed.** The card's procedure allows three
throwaway probes on the demo repo. None was needed: between 2026-09-10 21:13 PT
and 2026-09-11 19:34 PT the fleet planned four real epics on its own, and
among them are all three shapes the card asks to see. Every live line below
came from `gh run view --log` on a planner run that had already happened, or
from a Linear read. Nothing here dispatched a run, moved a card, filed a card,
or touched a variable or a secret. The tests ran locally in the worktree.

Times are Pacific (PDT, UTC−7) and labelled, each converted with `zoneinfo`
from the event's own timestamp. Raw UTC appears only inside verbatim command
output.

## The headline

**All four observations hold, on real cards, by the run and not by hand.**
[DRE-3530](https://linear.app/dreadnoughtfoundry/issue/DRE-3530) was stamped
`🧩 planning-shape: wave` with `observation-gated seam:` in its **Why:** line,
`by: planner`, and the wave route then wrote a wave plan whose `epics` block
names epics with the last depending on the others.
[DRE-3621](https://linear.app/dreadnoughtfoundry/issue/DRE-3621),
[DRE-3622](https://linear.app/dreadnoughtfoundry/issue/DRE-3622) and
[DRE-3623](https://linear.app/dreadnoughtfoundry/issue/DRE-3623) were each
stamped `🧩 planning-shape: epic` with no seam in the reason. On DRE-3622 the
planner then wrote a seam INTO its children, and the first critic sent it back
mechanically with the finding
`DRE-3635: wait on observing DRE-3634 live — that is a second epic, not a later step.`
and the marker `plan-critic: stage=pre round=1 result=SEND_BACK`; the planner
answered by filing the far side as a sibling epic and round 2 passed with
`no seam`. [DRE-3164](https://linear.app/dreadnoughtfoundry/issue/DRE-3164)
carries no shape stamp and no critic round from any of this. The unit suites
that pin the behaviour pass on the same commit.

## What is deployed, and where

| Fact | Value |
| --- | --- |
| DRE-3395 (seam reader) reached `main` | `44891f372381773127f8d6dc1a82205f6565517b` — merge of PR #336, 2026-09-09 16:40 PT |
| DRE-3391 (rule in brief + standard) reached `main` | `9bb5b1839877cc4cacbcc3c6e6faf9dce7df1d95` — merge of PR #360, 2026-09-10 15:39 PT |
| DRE-3398 (seam gate at the first critic) reached `main` | `20f7a1bade506d7bc0039c5654b9b3689eed1c6a` — merge of PR #362, 2026-09-10 16:08 PT |
| DRE-3394 (classifier stamps `wave`) reached `main` | `bf8f59906d6818047458073c4611b9ff83256be2` — merge of PR #363, 2026-09-10 16:11 PT |
| This repo's plan stub | `.github/workflows/self-plan.yml` → `plan.yml@main`, `pipeline_ref: main` — live on merge, no channel wait |
| Head of the run that stamped DRE-3530 (§1) | `3d8aa4b997055ae0374d6a7297c742eb45b1af65` (merge of PR #364, 2026-09-10 21:08 PT) — `git merge-base --is-ancestor` → contains all four merges above |
| Head of the runs that stamped DRE-3621/3622/3623 and sent DRE-3622 back (§2, §3) | `d231ab1c7f6f0e34cdf3a5de95d0fc2afaae7a61` (merge of PR #369, 2026-09-11 00:47 PT) — contains all four |
| The worktree this record's tests ran on | `54ed0d4c539e18613d47c84eca1417dabde82d39` (merge of PR #372, 2026-09-12 10:12 PT) — contains all four |

`git diff 3d8aa4b9..54ed0d4c` and `git diff d231ab1c..54ed0d4c` over
`scripts/plan_seam.py`, `scripts/plan_seam_gate.py`,
`scripts/planning_classify.py`, `scripts/planning_shape.py`,
`scripts/plan_critic.py`, `scripts/planning_route.py`,
`standards/card-quality.md`, `briefs/planner.md`, `standards/plan-critic.md`,
the four seam test files and `.github/workflows/plan.yml` differ in exactly
two files: `scripts/planning_route.py` (DRE-3654 — the escalation re-reads the
lane before it parks; 21 lines in the escalate branch) and
`.github/workflows/plan.yml` (DRE-3659 — the wave commit stops dispatching the
planner; 5 lines). Neither is on the classify, seam-read or gate path. So the
code that stamped and sent back in §1–§3 is, on that path, the code the tests
in §4 ran over.

## 1. The classifier stamps a seam-shaped card `wave` and names the seam — MET

**The card.** DRE-3530, `repo:bureau-pipeline`, `agent:planner` — the Linear
head-room epic the CEO wrote on 2026-09-10. Its body carries a PROOF criterion
that can only be met by watching production ("a busy hour with the bucket
never under ~1,000"), an operator step (the sandbox seat is minted by hand),
and build cards that wait on both. That is the DRE-3164 shape on a real card,
not a probe.

**The run.** [34561374625](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34561374625)
— `Agent Plan`, `repository_dispatch` (`agent-plan`), conclusion `success`,
created 2026-09-10 21:13:10 PT, head `3d8aa4b997055ae0374d6a7297c742eb45b1af65`.

**The stamp, as it stands on the card** — comment
`c8a4d366-78bf-46ba-9bc7-a4a98d855f40`, author `Agent-Bureau`, 2026-09-10
21:13:51 PT, verbatim:

```
🧩 planning-shape: **wave** — A programme of epics — more than one plan: too big for one, or cut at an observation-gated seam — so what it owes first is a decomposition into epics.

**Why:** The card already has children, which outranks everything else; it is also a multi-deliverable set across three repos with a CEO-approved plan, so the body agrees. — observation-gated seam: The PROOF (a busy hour with the bucket never under ~1,000) and the live-observation criteria for the reserve and re-entry wait on watching production after the sandbox seat is minted by hand and the sweep, medic and reserve cards ship; seam tests 2 and 4 trip, but the card's existing children fix its shape as epic rather than wave.; the seam rule (DRE-3244) files this as two epics, the second blocked on the first — size tests checked: 1 contracts between the pieces, 2 two languages or two tiers, 4 an unbounded quantifier, 6 cut on file footprint, not only on concern

**Stamped by:** `planner` · **model:** `claude-fable-5-1`

**Where it goes:** Planning. **Who handles it there:** plan.yml.
**Marked:** `agent:planner`.
The sweep does not promote a card of this shape — it is moved by whoever approves it.
```

What the card asked for, found in that text:

* the shape is **`wave`**;
* `observation-gated seam:` is in the **Why:** line, followed by the seam
  named in the card's own terms (the PROOF and the live-observation criteria
  wait on watching production after the seat is minted by hand);
* `**Stamped by:** \`planner\`` and the model, `claude-fable-5-1`;
* the tail `the seam rule (DRE-3244) files this as two epics, the second
  blocked on the first` — the sentence DRE-3394 appends when it upgrades.

One thing to read correctly, because it is the mechanism working and not a
contradiction: the model's own answer said `epic` ("the card's existing
children fix its shape as epic rather than wave") while reporting that seam
tells 2 and 4 tripped. DRE-3394 (PR #363) is built so that *an `epic` answer
over a seam becomes `wave`* deterministically, with the seam carried into the
**Why:** line — which is exactly what the stamp shows. The upgrade is the
code's, not the model's judgement, and both halves are on the record.

The run's own log lines around the stamp, from
`gh run view 34561374625 --repo dreadnought-foundry/bureau-pipeline --log`,
read 10:23 PT (log lines 282–284 and 306–318):

```
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:52.0930738Z commented on DRE-3530
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:52.0931456Z DRE-3530 already has label 'agent:planner'
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:52.0932152Z DRE-3530 classified wave on claude-fable-5-1
```

```
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:53.8222717Z {
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:53.8223042Z   "shape": "wave",
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:53.8223573Z   "destination": "Planning",
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:53.8223949Z   "actor": "plan.yml",
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:53.8224278Z   "promotable": false,
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:53.8224596Z   "marks": [
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:53.8224892Z     "agent:planner"
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:53.8225198Z   ],
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:53.8225492Z   "plan_artifact": false,
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:53.8225880Z   "green_light": false
call / bureau-card: DRE-3530	UNKNOWN STEP	2026-09-11T04:13:53.8226187Z }
```

**The wave route's note**, 2026-09-10 21:14:01 PT (comment
`0062f4a6-a8b8-4c50-9399-5c2049854eb9`):

```
🚦 planning-route: **wave** — A programme of epics — more than one plan: too big for one, or cut at an observation-gated seam — so what it owes first is a decomposition into epics.

This is too big to approve as one plan: what a green light would be given on is not written yet. So it is handed to the wave route, which owes a decomposition into epics before anyone is asked to approve anything.

**Where it goes:** Planning. **Who takes it from there:** plan.yml.
```

and the log at 2026-09-10 21:14:03 PT:
`DRE-3530 leaves Planning on the wave route → Planning (handled there by plan.yml)`.

**The wave plan, in the same run.** At 21:57:04 PT the planner posted the
plan (comment `784d5ffd-7a6a-46a7-a3f0-e4c5a9bbbaf5`) naming three epics in
order — *1. The fence … 2. The sweep … Starts alongside the fence … 3. The
headroom … Waits on the first two* — and at 21:57:22 PT the log reads
`wave plan: complete` from `wave_plan.py check`, then at 21:57:26 PT the
🌊 note *"The wave plan is written and checked against the standard, and it
names the epics it commits to, in order."* The CEO approved the shape on
2026-09-11 18:50 PT; the approval dispatch ran
[34666012779](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34666012779)
(created 18:50:59 PT, head `d231ab1c…`), which re-planned to four epics and
wrote the `epics` block the card asks to see — from that run's log at
18:51:20 PT, the fields that carry the dependency:

```
  "epics": [
      "key": "fence",
      "title": "The sandbox is fenced off: its own Linear seat, and one sweep owns the age-out",
      "depends_on": [],
      "status": "committed-in-sequence"
      "key": "sweep",
      "title": "The sweep's hour is pinned: thirty a pass, a card-done dispatch reads one card, the medic wakes only for failures",
      "depends_on": [],
      "status": "committed-in-sequence"
      "key": "headroom",
      "title": "A burst reserves its headroom before it starts, and a limit-dead run re-enters on its own",
      "depends_on": [
      "status": "committed-in-sequence"
```

(the `depends_on` of `headroom` lists `fence` and `sweep`; the rendered
ledger on the same lines reads `3. \`headroom\` … · waits for \`fence\`,
\`sweep\``). The committed epics exist as DRE-3621 (quiet), DRE-3622 (fence),
DRE-3623 (sweep) and DRE-3624 (headroom, Backlog, waits for fence and sweep).

## 2. An ordinary epic is still `epic` — MET, three times over

The three epics the wave committed were each moved to `Planning` by the wave
route on 2026-09-11 at 19:01 PT and classified by their own planner run on
head `d231ab1c7f6f0e34cdf3a5de95d0fc2afaae7a61`. Every stamp is
`**Stamped by:** \`planner\` · **model:** \`claude-fable-5-1\`` and none
carries `observation-gated seam:` anywhere in its reason.

| Card | Run | Stamp time (PT) | Log line | Why (verbatim) |
| --- | --- | --- | --- | --- |
| DRE-3621 | [34666480531](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34666480531) | 2026-09-11 19:01:34 | `DRE-3621 classified epic on claude-fable-5-1` | The card names itself an epic committed by wave DRE-3530 and its only deliverable is its own plan artifact for CEO green light, which is a set of cards under one parent, not one pull request. — size tests checked: 1 contracts between the pieces, 2 two languages or two tiers, 3 a criterion counting something never enumerated, 4 an unbounded quantifier, 5 specific is not small, 6 cut on file footprint, not only on concern |
| DRE-3622 | [34666480954](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34666480954) | 2026-09-11 19:01:37 | `DRE-3622 classified epic on claude-fable-5-1` | The card is a wave-committed epic that owes its own plan artifact and green light, and its title names two deliverables in two tiers — a Linear seat provisioned by hand and sweep code that stops touching production — which cannot be one pull request. — size tests checked: 1 contracts between the pieces, 2 two languages or two tiers, 6 cut on file footprint, not only on concern |
| DRE-3623 | [34666483239](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34666483239) | 2026-09-11 19:01:37 | *(same shape; stamp comment `2c77fd35-2685-4c57-b18f-97154d823345`)* | The card is a wave-committed epic whose sole deliverable is its own plan artifact and CEO green light, and the title names three distinct sweep changes (pass cap on a real board, single-card card-done dispatch, single age-out owner) that ship as separate cards under one parent. — size tests checked: 1 contracts between the pieces, 2 two languages or two tiers, 6 cut on file footprint, not only on concern |

The DRE-3621 stamp in full (comment `b6f06cf0-6e1a-4fd3-8362-2981a10fa6b3`):

```
🧩 planning-shape: **epic** — A set of cards that ship separately under one parent, with a plan the CEO approves before any of them run.

**Why:** The card names itself an epic committed by wave DRE-3530 and its only deliverable is its own plan artifact for CEO green light, which is a set of cards under one parent, not one pull request. — size tests checked: 1 contracts between the pieces, 2 two languages or two tiers, 3 a criterion counting something never enumerated, 4 an unbounded quantifier, 5 specific is not small, 6 cut on file footprint, not only on concern

**Stamped by:** `planner` · **model:** `claude-fable-5-1`

**Where it goes:** Green Light. **Who handles it there:** operator.
**Marked:** `agent:planner`.
The sweep does not promote a card of this shape — it is moved by whoever approves it.
```

The seam reader then confirmed the two that stayed ordinary all the way
through — from the run logs, the `plan_seam.py findings` step and the
`plan_seam_gate.py gate` step:

```
call / bureau-card: DRE-3621	UNKNOWN STEP	2026-09-12T02:20:15.1315515Z no seam — 6 card(s)
call / bureau-card: DRE-3621	UNKNOWN STEP	2026-09-12T02:22:16.8008354Z no seam — the result file is unchanged
call / bureau-card: DRE-3621	UNKNOWN STEP	2026-09-12T02:22:16.8465176Z plan-critic: stage=pre round=1 result=PASS collisions=0
```

```
call / bureau-card: DRE-3623	UNKNOWN STEP	2026-09-12T02:20:38.9554318Z no seam — 12 card(s)
call / bureau-card: DRE-3623	UNKNOWN STEP	2026-09-12T02:23:25.2316818Z no seam — the result file is unchanged
call / bureau-card: DRE-3623	UNKNOWN STEP	2026-09-12T02:23:25.2782349Z plan-critic: stage=pre round=1 result=PASS collisions=0
```

`no seam — the result file is unchanged` is DRE-3398's second answer — the
check ran, found nothing, and left the critic's decision byte-identical. The
third card, DRE-3622, is §3.

## 3. The pre-approval critic sends a seam back and names it — MET

The card allows for this observation to be unreachable ("if the planner
correctly files it as a wave, that IS the observation for the classifier").
It was reachable, on a real epic, and the path is the one the epic describes:
the classifier stamped DRE-3622 `epic` because its BODY carries no seam
(§2 — a seat provisioned by hand and sweep code, two tiers, no observation);
the planner then wrote children in which a build card waits on observing an
`[OPERATOR]` sign-off live; and the mechanical check caught it before the
critic's model decided anything.

**The run.** [34666480954](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34666480954)
— `Agent Plan`, created 2026-09-11 19:01:07 PT, head `d231ab1c…`. The two
cards the finding names: DRE-3634 *"SIGN-OFF (OPERATOR): the bureau-sandbox
seat exists — user created, key minted and fanned out …"* and DRE-3635
*"harness.yml runs the driver's one Linear read on the sandbox seat —
secrets.LINEAR_API_KEY_SANDBOX, declared LINEAR_IDENTITY: sandbox"*.

**The 🔎 mechanical note** (comment `6ddba722-616e-452a-b937-729dc0944f6e`,
2026-09-11 19:19:35 PT) ends with the seam block, verbatim:

```
Seam (structural — a send-back at the first critic, not advice):
- DRE-3635: wait on observing DRE-3634 live — that is a second epic, not a later step.
```

The log line that wrote it, from `plan_seam.py findings` in the same step
(19:19:34 PT):

```
call / bureau-card: DRE-3622	UNKNOWN STEP	2026-09-12T02:19:34.7301649Z DRE-3635: wait on observing DRE-3634 live — that is a second epic, not a later step.
```

**The gate**, after the critic's model had written its own result
(19:22:53 PT):

```
call / bureau-card: DRE-3622	UNKNOWN STEP	2026-09-12T02:22:53.0902551Z seam: 1 finding(s) written into /home/runner/work/_temp/plan-critic-pre.md — SEND_BACK: DRE-3635: wait on observing DRE-3634 live — that is a second epic, not a later step.
```

**The 🛑 round note** (comment `6ba271e0-168e-4706-aa63-7469f7242346`,
19:22:53 PT), verbatim:

```
🛑 **First critic — before the CEO reads it** — round 1 of 2: sent back — round 1 of 2

Reason: DRE-3635: wait on observing DRE-3634 live — that is a second epic, not a later step.

Every finding this round (2), ranked — the revision answers all of them, and the next round checks those fixes rather than finding these again:

1. DRE-3635: wait on observing DRE-3634 live — that is a second epic, not a later step.
2. DRE-3635: its own acceptance criteria include a post-merge-only observation ("the first Integration Harness run on main after merge ends with … budget: sandbox on its budget line") that the card itself admits "the build agent cannot observe" — a criterion that can only be checked after merge cannot…

send-back rate at this critic so far on this planning attempt: first round
```

The seam is the headline reason and finding 1; the critic's own finding
(the post-merge-only criterion) is finding 2 under it — DRE-3398's third
answer, where on a critic `SEND_BACK` the seam joins the list rather than
displacing the critic's text.

**The marker** (comment `772b4c69-835c-4b8a-bf8c-f71091299a57`, 19:22:54 PT):

```
plan-critic: stage=pre round=1 result=SEND_BACK collisions=0 — DRE-3635: wait on observing DRE-3634 live — that is a second epic, not a later step.
```

**What the planner did with it**, so the rule is seen closing rather than only
firing. The revision prompt in the log (19:22:54 PT) carries the instruction
*"…far side of the seam here, and file the second epic as a SIBLING of…"*. At
19:28 PT the planner cancelled DRE-3635 with the reason on the card —
*"Canceled at the first critic's send-back on DRE-3622's plan (2026-09-12),
not Done — nothing was built against it. The critic's seam reader named this
card as waiting on observing DRE-3634 live, which makes it a second epic
rather than a later step. Re-created, with its post-merge criterion removed,
as DRE-3651 under the sibling epic DRE-3650, blocked on DRE-3622's PROOF card
DRE-3636."* — and DRE-3650 exists as *"[EPIC] The harness driver rides the
sandbox seat — bureau-pipeline's one Linear read on bureau-sandbox, after the
seat is proven (part 2 of DRE-3622)"*, `agent:planner`, Backlog. That is the
DRE-3164 / DRE-3245 shape filed by the planner at the gate. Round 2 then read
a plan with no seam in it:

```
call / bureau-card: DRE-3622	UNKNOWN STEP	2026-09-12T02:30:35.6439464Z no seam — 11 card(s)
call / bureau-card: DRE-3622	UNKNOWN STEP	2026-09-12T02:34:47.9200394Z no seam — the result file is unchanged
call / bureau-card: DRE-3622	UNKNOWN STEP	2026-09-12T02:34:47.9662750Z plan-critic: stage=pre round=2 result=PASS collisions=0
```

with the ✅ round note at 19:34:48 PT: *"First critic — before the CEO reads
it — round 2 of 2: the critic passed this plan … send-back rate at this critic
so far on this planning attempt: 1/1 rounds"*.

## 4. DRE-3164 itself is untouched — MET, with one number explained

Read from the worktree at 10:23:30 PT with the operator-tools `LINEAR_API_KEY`
from agent-bureau's `.env` (read-only; sourced into the environment, never
printed):

```
python3 scripts/linear_ops.py children DRE-3164
```

```
9
linear-budget: 2499 → 2499 (spent 0 this run; window resets 11:23 PT; budget: undeclared)
```

The card says *seven*, and the board says nine, so here is the whole set with
creation dates, read through the Linear MCP at 10:23 PT: the seven the
2026-09-06 split kept — DRE-3165, DRE-3166, DRE-3167 (created 2026-09-05),
DRE-3210, DRE-3211 (2026-09-05), DRE-3239, DRE-3240 (2026-09-05 20:13 PT) —
plus DRE-3568 and DRE-3569, created 2026-09-10 11:49 PT and both Done by
12:58 PT the same day. Those two were added by hand on the 10th, a little
over four hours before the first of this epic's mechanisms that could touch a
card reached `main` (PR #362, 16:08 PT), and neither carries a stamp or a
critic round. They are not this epic's doing; they are the reason the count is nine.

What the seam runs did to DRE-3164: nothing. Over its 50 comments (first
2026-09-05 11:33 PT, last 2026-09-11 17:50 PT):

* `🧩 planning-shape` stamps on DRE-3164: **0** — it was planned before the
  classifier existed and was never re-classified;
* the last `plan-critic:` marker is `plan-critic: stage=post round=5
  result=PASS collisions=0`, 2026-09-07 19:05 PT — nothing after any of the
  four merges;
* the last comment is the CEO's sign-off closing the epic, 2026-09-11
  17:50 PT: *"Epic closed 2026-09-11 17:49 PT — CEO signed off. Every child is
  Done…"*

DRE-3245 (part 2, *"The fleet joins the release train … (part 2 of
DRE-3164)"*) sits in Intake, `agent:planner`, last updated 2026-09-09
15:46 PT — before any of the four merges — and its body states the split and
cites DRE-3244 as the rule. Read once, since no probe ran, there is no
"after" that differs from the "before".

## 5. The suites that pin this, on the same commit — 365 passed

Run in the worktree at `54ed0d4c539e18613d47c84eca1417dabde82d39`, 10:22:38 PT:

```
python3 -m pytest tests/test_planning_classify_seam.py tests/test_plan_seam.py tests/test_plan_seam_gate.py tests/test_seam_rule_docs.py -q
```

```
134 passed in 1.95s
```

Among them, by name, the two fixtures DRE-3244's acceptance criteria ask for
(`tests/fixtures/dre-3164-epic-2026-09-05.json` is the body as filed):

```
tests/test_planning_classify_seam.py::TestSeamEvidence::test_dre3164_fires_on_the_clean_console_releases
tests/test_planning_classify_seam.py::TestSeamEvidence::test_an_ordinary_epic_reads_as_no_seam
tests/test_planning_classify_seam.py::TestTheStamp::test_the_epic_answer_over_dre3164_stamps_a_wave
tests/test_planning_classify_seam.py::TestTheStamp::test_the_ordinary_epic_why_line_is_what_it_always_was
```

And the critic-side suites, 10:23:33 PT — including
`tests/test_plan_critic_scenario.py::…::test_a_pass_over_a_seam_is_a_send_back_at_the_first_critic`:

```
python3 -m pytest tests/test_plan_critic_scenario.py tests/test_planning_shape.py tests/test_planner_brief_shapes.py tests/test_plan_critic_wiring.py -q
```

```
231 passed, 3 subtests passed in 27.56s
```

## Where this leaves the card

| Acceptance criterion | State |
| --- | --- |
| Observed by hand on the live board and the live plan runs, on a date recorded in PT, with the run URLs and card ids on the card | **Met** — 2026-09-12 10:20–10:26 PT; runs 34561374625, 34666012779, 34666480531, 34666480954, 34666483239; cards DRE-3530, DRE-3621, DRE-3622, DRE-3623, DRE-3634, DRE-3635, DRE-3650, DRE-3164 |
| `docs/seam-proof-dre3244.md` on `main` with the four sections, one per observation, and the stamp, note or marker text quoted as observed | This document — §1–§4, with §5 the tests |
| The seam-shaped card was stamped `wave` with `observation-gated seam:` in its reason, and the ordinary one `epic`, both `by: planner` | **Met** — §1 (DRE-3530) and §2 (DRE-3621, DRE-3622, DRE-3623); all four stamps read `**Stamped by:** \`planner\`` |
| The seam inside one epic was sent back at the pre-approval critic with the finding naming the seam — or the record says the classifier caught it first | **Met on the critic path** — §3, DRE-3622 round 1, finding and marker quoted; the classifier path was not needed to explain it away |
| DRE-3164 has the same children and no new comment from these runs; probes cancelled, not left in a lane | **Met** — §4; nine children, the two beyond seven dated and explained; no probe was ever filed, so none is in a lane |
| Nothing in this card was closed by a merge event: the operator closes it after the last observation | The sibling record DRE-3436 (`no-code` + `hand-built`, the same labels as this card) merged on an `agent/DRE-3436-…` branch at 2026-09-12 10:12 PT and was still in Backlog at 10:25 PT — the guard holds for this shape. The operator closes DRE-3399 |

Where the card's procedure and the record differ, and why: the card's steps
1–3 describe three probes with `PROOF-3244-A/B/C` titles on the demo repo,
"the DRE-3164 intent … with the product names swapped". The card's acceptance
criteria do not require probes — they require the stamps, the send-back and
DRE-3164's untouched state, by the run — and a real card the CEO wrote is
stronger evidence than a body written to trip the read. Filing three planner
runs on the demo repo would also have spent three Claude planner runs and the
fleet's Linear hour on a board under `INTAKE_HOLD`, to reproduce what the
board already showed. Nothing is owed.

One thing this record deliberately does not claim: that the classifier read a
body *worded like DRE-3164* live. The fixture test in §5 covers that body; the
live read in §1 is of DRE-3530's own body, whose seam is a proof criterion and
an operator step rather than "seven clean days". Same tells, different words.

---

Read and written 2026-09-12, 10:20–10:26 PT, from a worktree of
`dreadnought-foundry/bureau-pipeline` at `54ed0d4c539e18613d47c84eca1417dabde82d39`,
the Actions logs of `dreadnought-foundry/bureau-pipeline`, and Linear reads.
Every live observation is a read; nothing on the board was changed.
