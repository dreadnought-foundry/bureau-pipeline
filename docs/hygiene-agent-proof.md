# Hygiene agent — one working day on the live board — proof for DRE-5412 (epic DRE-5365)

**Status: THE REHEARSAL IS RUNNING. The live window is not open yet, and nothing below is a result.** As the card orders, the agent runs dry before it goes live: one hand dispatch with `dry_run` true (10:20 PT, §1), then two scheduled dry passes, due 10:37 and 11:37 PT on 2026-10-04. `HYGIENE_LIVE` is set to `true` after the 11:37 pass if both are clean.

**The window opens at the first live pass, the 12:37 PT scheduled pass on 2026-10-04. It closes after the 24th live pass, due 11:37 PT on 2026-10-05.** After that pass finishes, the record is completed from the read-off steps in §8. GitHub's timer runs late, so read at about 12:00 PT. This pull request stays a **draft** until then, so the merge gate cannot land an empty record as the proof.

**Which "working day".** The card defines it as "one working day — twenty-four scheduled passes". So the window is 24 back-to-back hourly passes of the `37 * * * *` schedule, counted from the first live one, not office hours. If `HYGIENE_LIVE` goes on later than planned, the window moves with it: it opens at the first pass whose `Decide dry run` step prints `dry_run=false`. If a lane gives the agent nothing to clear in those 24 passes, the card extends the window to the next working day for that lane, with a board read that shows the lane was empty.

Observed by a proof-helper session on the operator's instruction. Every time below is Pacific Time.

| Criterion | Result |
|---|---|
| Over one working day the agent cleared at least one row in each lane (pull requests, Green Light, Todo, Triage, proofs), each with its `🧹 hygiene:` receipt and PT time, no person acting; a `hyg-decision-needed` note counts as nothing | Pending: the window is not open yet (§3). Note that the 10:20 PT dry dispatch found **0 actions in every lane** and 16 rows it would leave alone (§1) |
| A real business question in Green Light left in place and listed on the standing card's summary with its recommendation, PT time | Pending (§4). The dry dispatch already lists three planner questions in Green Light as left: DRE-5672, DRE-5519 and DRE-3698. Each one reads `recommend: the escalation states no recommendation line` |
| Summary only in hours with change; one hour with no change and no summary; one hour where the same rows stood, nothing cleared, no summary | Pending (§5) |
| Standing card outside Intake with `hand-built`, `no-code`, `agent:ops`, never offered by a groomer proposal, carrying only hygiene summaries; board read in the record | Pending (§2). The card exists as DRE-5774, and `HYGIENE_CARD` names it |
| Two scheduled dry passes before `HYGIENE_LIVE`, plus the hand dispatch with `dry_run` true, each with run id, PT time and `would:` lines; none wrote anything | **Partly recorded** (§1): the hand dry dispatch, run 37220103263 at 10:20 PT. The two scheduled dry passes (10:37 and 11:37 PT) are still to be added |
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

The log has no `would:` lines because the pass found **no action to take in any lane**: it would leave every row it saw for a person. That is the first warning for criterion 1. If the live passes see the same board, the agent clears nothing, and the card's own rule extends the window for each empty lane. The dispatch ran at 10:20 PT, before `HYGIENE_CARD` was set at 10:25 PT, so its summary went to the log only, as its last line says. It wrote nothing to the board.

**The two scheduled dry passes.** Still to be added: the 10:37 and 11:37 PT passes, each with its run id, its `dry_run=true` line, its `would:` lines (or `0 action(s)`), and a board read showing it wrote nothing except the summary comment the card allows on DRE-5774.

| PT | run id | event | dry_run | actions / `would:` lines | summary |
|---|---|---|---|---|---|
| 10:20 | 37220103263 | workflow_dispatch | true | 0 in every leg; 16 left | log only (`HYGIENE_CARD` unset then) |
| ~10:37 | | schedule | true | | |
| ~11:37 | | schedule | true | | |

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
| 1 | | ~12:37 on 10-04 (first live) | | | | |
| … | | | | | | |
| 24 | | ~11:37 on 10-05 | | | | |

## 6. What the agent did not do

<!-- fill from §8 step 5 -->

## 7. The console roster's `hygiene` entry

## 8. Read-off steps (the 10:37 and 11:37 dry passes now; the rest after the 24th live pass, due 11:37 PT 2026-10-05)

Run these with any operator read token. None of them writes.

1. **Every pass.**
   `gh run list --repo dreadnought-foundry/bureau-pipeline --workflow Hygiene --created '>=2026-10-04T17:30:00Z' --limit 40 --json databaseId,event,createdAt,conclusion`
   Keep the `schedule` rows. The two before `HYGIENE_LIVE` went on go in §1. From the first one whose log prints `dry_run=false` there should be 24, and they go in §5. Convert each `createdAt` to PT.
2. **What each pass did.** For each run id:
   `gh run view <id> --repo dreadnought-foundry/bureau-pipeline --log | grep -E "hygiene:|would:|executed|refused|stood down|posted nowhere"`
   An `executed` action is a cleared row; a `hyg-decision-needed` note is not.
3. **The receipts on the board.** Find the receipts posted in the window: use the console's activity feed, or run `python3 scripts/linear-api.py search "🧹 hygiene:"` once (one read, not a loop). For each lane, take one row whose receipt is the agent's, and check its thread to confirm no person acted on it between the receipt and the read.
4. **The summaries.** Read DRE-5774's comments once. Every comment opens `🧹 hygiene: summary <digest> · <HH:MM PT>`. Match each comment to its pass in §5. A pass with no comment is a quiet hour. Tell the two kinds of quiet hour apart: one where nothing changed (the same digest as the previous summary, 0 executed), and one where the same rows stood and nothing was cleared. Then read the day's groomer proposals on DRE-4541 once, and confirm DRE-5774 is not offered.
5. **What it did not do.** No run log contains a `merge` call (the guard builds none). In the board read, no epic moved Green Light → In Progress under the agent's identity during the window. `gh api repos/<owner>/<repo>/events` shows no push to a default branch by the bureau App in the window. No PR carries a `break-glass` label the agent applied.
6. **The roster.** Open the console's agent roster and record what the `hygiene` entry shows.
7. Fill in the criterion table, take this PR out of draft, and let the gate merge it. The CEO closes the card.

## What is not proven, and why

<!-- fill at the end of the window -->
