# Hygiene agent — one working day on the live board — proof for DRE-5412 (epic DRE-5365)

**Status: THE DRY REHEARSAL IS DONE. Going live is ON HOLD for a CEO answer, and the live window is pending.** As the card orders, the agent ran dry before going live (§1):
- two hand dispatches with `dry_run` true, at 10:20 and 11:09 PT;
- two scheduled dry passes: the 10:37 slot, which fired at 11:13 PT, and the 11:37 slot, which fired at 12:41 PT.

None of them wrote anything. Criterion 5 is met.

**The live switch was on for four minutes, then paused (§0).** `HYGIENE_LIVE` was set to `true` at 12:45:36 PT and back to `false` at 12:49:58 PT. No pass ran in between; the newest run at 12:50 PT is still the 12:41 dry pass. The reason for the pause: both scheduled dry passes would have moved DRE-5036 and DRE-3696 from Green Light back to Planning, and those are epics the CEO put back in Green Light that morning. The CEO is being asked which lane they belong in. `HYGIENE_LIVE` returns after his answer.

**GitHub's timer runs late, so no time below is fixed in advance.** It fired the 10:37 slot 36 minutes late (11:13 PT) and the 11:37 slot 64 minutes late (12:41 PT). **The window opens at the first scheduled pass whose log prints `dry_run=false` after `HYGIENE_LIVE` returns.** That is pending; read it with step 1 of §8. **It closes after the 24th live scheduled pass from there.** Count 24 hourly slots from the opening one, and allow for the timer firing each slot up to about an hour late. After that pass finishes, the record is completed from the read-off steps in §8. This pull request stays a **draft** until then, so the merge gate cannot land an unfinished record as the proof.

**Which "working day".** The card defines it as "one working day — twenty-four scheduled passes". So the window is 24 back-to-back hourly passes of the `37 * * * *` schedule, counted from the first live one, not office hours. If `HYGIENE_LIVE` goes on later than planned, the window moves with it: it opens at the first pass whose `Decide dry run` step prints `dry_run=false`. If a lane gives the agent nothing to clear in those 24 passes, the card extends the window to the next working day for that lane, with a board read that shows the lane was empty.

Observed by a proof-helper session on the operator's instruction. Every time below is Pacific Time.

| Criterion | Result |
|---|---|
| Over one working day the agent cleared at least one row in each lane (pull requests, Green Light, Todo, Triage, proofs), each with its `🧹 hygiene:` receipt and PT time, no person acting; a `hyg-decision-needed` note counts as nothing | Pending: the window is not open yet (§3). The 10:20 PT dry dispatch found 0 actions in every lane. The 11:09 and 11:13 PT dry runs each found **2 actions in the Green Light lane** (DRE-5036 and DRE-3696 sent back to Planning) and 0 in every other lane. The 12:41 PT pass found those two plus **1 in the pull-request lane** (the gate re-dispatched on agent-bureau #3139). No dry run found anything in Todo, Triage or proofs (§1) |
| A real business question in Green Light left in place and listed on the standing card's summary with its recommendation, PT time | Pending (§4). The dry dispatch already lists three planner questions in Green Light as left: DRE-5672, DRE-5519 and DRE-3698. Each one reads `recommend: the escalation states no recommendation line` |
| Summary only in hours with change; one hour with no change and no summary; one hour where the same rows stood, nothing cleared, no summary | Pending (§5) |
| Standing card outside Intake with `hand-built`, `no-code`, `agent:ops`, never offered by a groomer proposal, carrying only hygiene summaries; board read in the record | Pending (§2). The card exists as DRE-5774, and `HYGIENE_CARD` names it |
| Two scheduled dry passes before `HYGIENE_LIVE`, plus the hand dispatch with `dry_run` true, each with run id, PT time and `would:` lines; none wrote anything | **Met** (§1). Scheduled dry passes: 37223608815 (the 10:37 slot, fired 11:13 PT) and 37229265771 (the 11:37 slot, fired 12:41 PT). Hand dry dispatches: 37220103263 (10:20 PT) and 37223294890 (11:09 PT). Every log prints `dry_run=true`, and every action appears only as a `would:` line. A board read at 12:46 PT and agent-bureau #3139's comments show none of them wrote anything. `HYGIENE_LIVE` went on at 12:45:36 PT, after all four |
| No epic approved, no PR merged, no default branch pushed, no `break-glass` applied by the agent, against Actions logs and board | Pending (§6) |
| Merged on `main` at `docs/hygiene-agent-proof.md`, opens with this table, PT time on every observation; CEO closes; the agent did not close it (DRE-5411 `hygiene_done.OWN_PROOF`) | Open: this draft first, then the CEO. The dry dispatch already lists DRE-5412 as `this agent's own epic's proof — the CEO closes it` (§1) |

## 0. What runs, and on which code

All eight build cards are merged, and `stable` = `af890aa` contains every merge commit (read 2026-10-04 10:17 PT; `compare/<merge>...af890aa` reports `ahead` for each one). The Hygiene workflow lives in this repo, and its schedule runs from `main`, which was also at `af890aa` at that read.

| card | PR | merged (PT) |
|---|---|---|
| DRE-5367 | agent-bureau #3023 | 2026-10-01 21:40 |
| DRE-5368 | bureau-pipeline #663 | 2026-10-02 12:37 |
| DRE-5369 | bureau-pipeline #669 | 2026-10-02 13:40 |
| DRE-5371 | bureau-pipeline #674 | 2026-10-02 15:01 |
| DRE-5370 | bureau-pipeline #675 | 2026-10-02 17:44 |
| DRE-5410 | bureau-pipeline #707 | 2026-10-03 15:30 |
| DRE-5372 | bureau-pipeline #709 | 2026-10-03 16:17 |
| DRE-5411 | bureau-pipeline #706 | 2026-10-03 16:25 |

The workflow and its switches, as read at 10:23 and 10:26 PT on 2026-10-04:

```
$ gh workflow list --all --repo dreadnought-foundry/bureau-pipeline | grep -i hygiene
Hygiene	active	373462603
$ gh api repos/dreadnought-foundry/bureau-pipeline/actions/workflows/373462603 --jq '{state,updated_at}'
{"state":"active","updated_at":"2026-10-04T10:19:48.000-07:00"}
$ gh variable list --repo dreadnought-foundry/bureau-pipeline | grep HYG      # 10:26 PT
HYGIENE_CARD	DRE-5774	2026-10-04T17:25:48Z
HYGIENE_LIVE	false	2026-10-04T17:25:03Z
```

- The workflow was created 2026-10-02 13:40 PT, disabled at 15:17 PT in the CEO's fleet pause, and re-enabled 2026-10-04 10:19 PT on the CEO's approval.
- `HYGIENE_LIVE` was `true` briefly, from 10:22 PT until it was set back to `false` at 10:25 PT. No pass ran live in those three minutes: the only run between 10:19 and 10:26 PT is the 10:20 PT hand dispatch, which ran with `dry_run=true`.
- `HYGIENE_CARD` has been DRE-5774 since 10:25 PT. `HYGIENE_BUDGET_FLOOR` is unset, so the floor is the default, 100.
- `HYGIENE_LIVE` was set to `true` at 12:45:36 PT, after the four dry runs in §1. Read at 12:46 PT:

```
$ gh variable list --repo dreadnought-foundry/bureau-pipeline | grep HYG
HYGIENE_CARD	DRE-5774	2026-10-04T17:25:48Z
HYGIENE_LIVE	true	2026-10-04T19:45:36Z
```

- **Paused at 12:49:58 PT.** The fullstack session set `HYGIENE_LIVE` back to `false`, before any live pass. Read at 12:50 PT:

```
$ gh variable list --repo dreadnought-foundry/bureau-pipeline | grep HYG
HYGIENE_CARD	DRE-5774	2026-10-04T17:25:48Z
HYGIENE_LIVE	false	2026-10-04T19:49:58Z
$ gh run list --repo dreadnought-foundry/bureau-pipeline --workflow hygiene.yml --limit 4
37229265771  schedule           completed  success  2026-10-04T19:41:54Z
37223608815  schedule           completed  success  2026-10-04T18:13:48Z
37223294890  workflow_dispatch  completed  success  2026-10-04T18:09:17Z
37220103263  workflow_dispatch  completed  success  2026-10-04T17:20:01Z
```

  Why: both scheduled dry passes would have sent DRE-5036 and DRE-3696 from Green Light back to Planning (§1), and those are epics the CEO returned to Green Light that morning. The CEO is being asked which lane they belong in. `HYGIENE_LIVE` returns after his answer, and the window opens at the first live scheduled pass after that.
- `main` moved during the rehearsal. The 11:09 and 11:13 PT runs ran on `0f4bf78`, and the 12:41 PT run on `c4fa502`. Each live pass's head sha goes in §5.

## 1. The dry passes before going live

**Before the pause (does not count).** Scheduled run [37068474780](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37068474780), 14:43 PT on 2026-10-02, `dry_run=true`. It stood down at the budget floor and printed no `would:` lines:

```
hygiene: event=schedule dry_run=true
hygiene: stood down — 30 request(s) left on the fleet key, below the floor of 100
```

**The hand dispatch with `dry_run` true.** Run [37220103263](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37220103263), `workflow_dispatch`, created 10:20:01 PT on 2026-10-04, success. The log, trimmed (`…` marks a cut line):

```
hygiene: event=workflow_dispatch dry_run=true
hygiene: 2499 request(s) left on the fleet key, at or above the floor of 100
hygiene: EveryBite — 0 action(s), 0 failed, 0 left
hygiene: DeltaSolv — 0 action(s), 0 failed, 0 left
hygiene: dreadnought-foundry — 0 action(s), 0 failed, 16 left
🧹 hygiene: summary 56d6f5713f52 · 10:21 PT
Todo and proofs
- left DRE-5724 — #3086 merged 2026-10-03 09:09 PT — the card is no-code, so the merge is not the work — recommend: …
- left DRE-4465 — #885 merged 2026-10-02 17:19 PT — the card is no-code, so the merge is not the work — recommend: …
- left DRE-4463 — #891 merged 2026-10-02 18:37 PT — the card is no-code, so the merge is not the work — recommend: …
- left DRE-5459 — ready for the CEO — record docs/groomer-verify-proof-2026-10.md at #698, 3 row(s) not met: …
- left DRE-5412 — this agent's own epic's proof — the CEO closes it — recommend: the CEO reads the record and closes the card
- left DRE-5346 — ready for the CEO — record docs/both-critics-before-green-light-proof.md at #701, 2 row(s) not met: …
- left DRE-5232 — ready for the CEO — record docs/verifier-fail-proof-2026-10.md at #700, 2 row(s) not met: …
- left DRE-5072 — ready for the CEO — record architecture/proofs/dre-4912-fork-gate.md at #3102, 4 row(s) not met: …
- left DRE-4753 — ready for the CEO — record docs/roll-up-proof-dre4680.md at #702, 2 row(s) not met: …
- left DRE-4453 — #884 merged 2026-10-04 10:19 PT — the card is no-code, so the merge is not the work — recommend: …
Green Light
- left DRE-5765 — it carries no planning receipt this lane reads — recommend: a person reads the card — nothing on it says why it waits here
- left DRE-5672 — the planner asks a question, posted 2026-10-02 17:20 PT — recommend: the escalation states no recommendation line — the question is on the card
- left DRE-5519 — the planner asks a question, posted 2026-10-01 21:42 PT — recommend: the escalation states no recommendation line — the question is on the card
- left DRE-5464 — its newest critic record reads SEND_BACK, posted 2026-10-02 11:11 PT — recommend: a person reads the card — …
- left DRE-3698 — the planner asks a question, posted 2026-10-02 12:33 PT — recommend: the escalation states no recommendation line — the question is on the card
Triage
- left DRE-3624 — in Triage with no mechanical defect this agent fixes — recommend: a person reads the card's newest refusal and fixes what it names
hygiene: HYGIENE_CARD is unset — the summary was printed here and posted nowhere
```

The log has no `would:` lines because the pass found no action to take in any lane: it would leave every row it saw for a person. The dispatch ran at 10:20 PT, before `HYGIENE_CARD` was set at 10:25 PT, so its summary went to the log only, as its last line says.

**The second hand dispatch with `dry_run` true.** Run [37223294890](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37223294890), `workflow_dispatch` by `smeed652` on `main` at `0f4bf78`, created 11:09:17 PT on 2026-10-04, success. The log, trimmed:

```
hygiene: event=workflow_dispatch dry_run=true
hygiene: 2280 request(s) left on the fleet key, at or above the floor of 100
hygiene: DeltaSolv — 0 action(s), 0 failed, 0 left
would: comment DRE-5036: 🧹 hygiene: hyg-resent-to-planning — planner died, limit-death kind=claude stage=plan at 2026-10-01 21:33 PT · 11:09 PT
would: state DRE-5036 → Planning
would: comment DRE-3696: 🧹 hygiene: hyg-resent-to-planning — classifier transport failure at 2026-10-02 08:25 PT · 11:09 PT
would: state DRE-3696 → Planning
hygiene: dreadnought-foundry — 2 action(s), 0 failed, 14 left
hygiene: EveryBite — 0 action(s), 0 failed, 0 left
would: comment DRE-5774:
🧹 hygiene: summary 4effb7806514 · 11:10 PT
```

**The first scheduled dry pass.** Run [37223608815](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37223608815), `schedule` on `main` at `0f4bf78`, created 11:13:48 PT on 2026-10-04 (the 10:37 slot, 36 minutes late), success. The log, trimmed:

```
hygiene: event=schedule dry_run=true
hygiene: 2315 request(s) left on the fleet key, at or above the floor of 100
hygiene: EveryBite — 0 action(s), 0 failed, 0 left
hygiene: DeltaSolv — 0 action(s), 0 failed, 0 left
would: comment DRE-5036: 🧹 hygiene: hyg-resent-to-planning — planner died, limit-death kind=claude stage=plan at 2026-10-01 21:33 PT · 11:14 PT
would: state DRE-5036 → Planning
would: comment DRE-3696: 🧹 hygiene: hyg-resent-to-planning — classifier transport failure at 2026-10-02 08:25 PT · 11:14 PT
would: state DRE-3696 → Planning
hygiene: dreadnought-foundry — 2 action(s), 0 failed, 14 left
would: comment DRE-5774:
🧹 hygiene: summary 680c93735974 · 11:15 PT
```

Both later runs would clear two rows in the **Green Light** lane: each would post a `hyg-resent-to-planning` receipt and move the card back to Planning (the lane in `scripts/hygiene_green_light.py`). So the board is not empty for the agent, at least in Green Light. Their summaries list nothing to do in Todo and proofs or in Triage: every row there is `left` for a person, so those lanes may still need the card's extension rule. In both runs the summary for DRE-5774 is itself a `would:` line, so the log shows no write of any kind. The board read below confirms it.

**The second scheduled dry pass.** Run [37229265771](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37229265771), `schedule` on `main` at `c4fa502`, created 12:41:54 PT on 2026-10-04 (the 11:37 slot, 64 minutes late), success. The log, trimmed:

```
hygiene: event=schedule dry_run=true
hygiene: 2417 request(s) left on the fleet key, at or above the floor of 100
hygiene: EveryBite — 0 action(s), 0 failed, 0 left
hygiene: DeltaSolv — 0 action(s), 0 failed, 0 left
would: comment DRE-5036: 🧹 hygiene: hyg-resent-to-planning — planner died, limit-death kind=claude stage=plan at 2026-10-01 21:33 PT · 12:42 PT
would: state DRE-5036 → Planning
would: comment DRE-3696: 🧹 hygiene: hyg-resent-to-planning — classifier transport failure at 2026-10-02 08:25 PT · 12:42 PT
would: state DRE-3696 → Planning
would: gh workflow run merge-gate.yml --repo dreadnought-foundry/agent-bureau -f pr_number=3139
would: gh pr comment 3139 --repo dreadnought-foundry/agent-bureau --body '🧹 hygiene: hyg-gate-redispatched — hold reason cleared, critic APPROVE at head 8abf374 · 12:42 PT'
hygiene: dreadnought-foundry — 3 action(s), 0 failed, 17 left
would: comment DRE-5774:
🧹 hygiene: summary 0a3dac74d839 · 12:43 PT
```

This pass adds a row in the **pull-request** lane: a gate re-dispatch on agent-bureau #3139, whose hold reason had cleared, with its `hyg-gate-redispatched` receipt.

**None of the dry runs wrote anything.** Two reads, both at 12:46 PT:
- One Linear query over DRE-5036, DRE-3696 and DRE-5774. DRE-5036 and DRE-3696 are still in **Green Light**, and neither thread holds a `🧹` comment (82 and 65 comments, each read in full). DRE-5774 is in Hand-work with `repo:bureau-pipeline`, `agent:ops`, `no-code`, `hand-built` and `initiative:bureau`, and its only comment is the one it was created with; no summary has been posted.
- `gh api repos/dreadnought-foundry/agent-bureau/issues/3139/comments`, filtered to comments containing `🧹`, returns `[]`.

**Criterion 5: met.** Two scheduled dry passes and two hand dry dispatches, each recorded with its run id, PT time and `would:` lines. All of them ran before `HYGIENE_LIVE` went on at 12:45:36 PT, and none wrote anything.

| PT | run id | event | dry_run | actions / `would:` lines | summary |
|---|---|---|---|---|---|
| 10:20 | 37220103263 | workflow_dispatch | true | 0 in every lane; 16 left | log only (`HYGIENE_CARD` unset then) |
| 11:09 | 37223294890 | workflow_dispatch | true | Green Light: 2 (DRE-5036, DRE-3696 → Planning); 0 elsewhere; 14 left | `would:` only (4effb7806514) |
| 11:13 | 37223608815 | schedule (10:37 slot) | true | Green Light: 2 (DRE-5036, DRE-3696 → Planning); 0 elsewhere; 14 left | `would:` only (680c93735974) |
| 12:41 | 37229265771 | schedule (11:37 slot) | true | Green Light: 2 (the same); pull requests: 1 (gate re-dispatch, agent-bureau #3139); 0 elsewhere; 17 left | `would:` only (0a3dac74d839) |

## 2. The standing card

DRE-5774, `Hygiene agent — hourly summary`. Its labels are `repo:bureau-pipeline`, `agent:ops`, `no-code`, `hand-built` and `initiative:bureau`. It sits in Hand-work, and `HYGIENE_CARD` has named it since 10:25 PT. The operator's session created the card, not this helper.

<!-- fill at the end: a board read showing DRE-5774 outside Intake; the day's groomer proposals on DRE-4541 not offering it; every comment on DRE-5774 is a `🧹 hygiene: summary` -->

## 3. One row cleared per lane

| lane | row | `🧹 hygiene:` receipt (verbatim) | PT | run id | what a person would otherwise have done |
|---|---|---|---|---|---|
| pull requests | | | | | |
| Green Light | | | | | |
| Todo | | | | | |
| Triage | | | | | |
| proofs | | | | | |

## 4. The business question left in Green Light

## 5. Every live pass in the window

| # | run id | PT | conclusion | executed | summary (posted / none) | what changed |
|---|---|---|---|---|---|---|
| 1 | | (first `dry_run=false` pass) | | | | |
| … | | | | | | |
| 24 | | (24th live scheduled pass) | | | | |

## 6. What the agent did not do

<!-- fill from §8 step 5 -->

## 7. The console roster's `hygiene` entry

## 8. Read-off steps (step 1 now, to fix the window's start; the rest after the 24th live scheduled pass)

Run these with any operator read token. None of them writes.

1. **Every pass.**
   `gh run list --repo dreadnought-foundry/bureau-pipeline --workflow Hygiene --created '>=2026-10-04T17:30:00Z' --limit 40 --json databaseId,event,createdAt,conclusion`
   Keep every row, `schedule` and `workflow_dispatch` alike. Every run before `HYGIENE_LIVE` went on for good goes in §1. It was on only from 12:45:36 to 12:49:58 PT on 10-04, and no pass ran in that gap; read its new time with `gh variable list --repo dreadnought-foundry/bureau-pipeline | grep HYGIENE_LIVE`. For each later `schedule` row, run `gh run view <id> --repo dreadnought-foundry/bureau-pipeline --log | grep "dry_run="`. The first that prints `dry_run=false` opens the window: write its run id and PT time into the status paragraph as the window's start. From that run on, every run goes in §5: the 24 `schedule` rows are the window, and any `workflow_dispatch` row is listed too, marked as a hand dispatch that does not count toward the 24. Convert each `createdAt` to PT.
2. **What each pass did.** For each run id:
   `gh run view <id> --repo dreadnought-foundry/bureau-pipeline --log | grep -E "hygiene:|would:|executed|refused|stood down|posted nowhere"`
   An `executed` action is a cleared row; a `hyg-decision-needed` note is not.
3. **The receipts on the board.** Find the receipts posted in the window: use the console's activity feed, or run agent-bureau's `scripts/linear-api.py search "🧹 hygiene:"` once from an agent-bureau checkout (one read, not a loop). For each lane, take one row whose receipt is the agent's, and check its thread to confirm no person acted on it between the receipt and the read.
4. **The summaries.** Read DRE-5774's comments once. Every comment opens `🧹 hygiene: summary <digest> · <HH:MM PT>`. Match each comment to its pass in §5. A pass with no comment is a quiet hour. Tell the two kinds of quiet hour apart: one where nothing changed (the same digest as the previous summary, 0 executed), and one where the same rows stood and nothing was cleared. Then read the day's groomer proposals on DRE-4541 once, and confirm DRE-5774 is not offered.
5. **What it did not do.** No run log contains a `merge` call (the guard builds none). In the board read, no epic moved Green Light → In Progress under the agent's identity during the window. `gh api repos/<owner>/<repo>/events` shows no push to a default branch by the bureau App in the window. No PR carries a `break-glass` label the agent applied.
6. **The roster.** Open the console's agent roster and record what the `hygiene` entry shows.
7. Fill in the criterion table, take this PR out of draft, and let the gate merge it. The CEO closes the card.

## What is not proven, and why

<!-- fill at the end of the window -->
