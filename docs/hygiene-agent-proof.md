# Hygiene agent — one working day on the live board — proof for DRE-5412 (epic DRE-5365)

**Status: THE WINDOW IS CLOSED, AND THE PROOF IS NOT YET COMPLETE.** The day ran from the first live scheduled pass, run [37235338544](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37235338544) at 14:15 PT on 2026-10-04, to the 24th scheduled pass, run [37365222413](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37365222413) at 12:43 PT on 2026-10-05. The record was read off at 17:50–18:00 PT on 2026-10-05, after 28 live scheduled passes.

**Four of the seven criteria are met, and three are not:**
- **Criterion 1, not met.** The agent cleared rows on its own in four lanes: pull requests, Todo, Triage and proofs. It cleared nothing in Green Light during the day. Every Green Light row in the window was a planner question, a plan the critics sent back, or a card with no planning receipt, and the agent rightly leaves each of those for a person. By the card's rule, the Green Light lane's window extends to the next working day (§3).
- **Criterion 4, not met.** The standing card DRE-5774 carries one comment that is not a hygiene summary: the pipeline's unlanded-work watchdog posted a notice on it at 19:56 PT on 10-04 (§2). Everything else about the standing card holds.
- **Criterion 7, not met yet.** It is met when this pull request merges. The agent did not close DRE-5412 at any point in the day (§7).

Observed by a proof-helper session on the operator's instruction (the CEO's order of 2026-10-05 at about 17:45 PT: "close the waiting proofs now, don't wait"). Every time below is Pacific Time. GitHub and Linear answer in UTC, and every time was converted at UTC−7.

| Criterion | Result |
|---|---|
| Over one working day the agent cleared at least one row in each lane (pull requests, Green Light, Todo, Triage, proofs), each with its `🧹 hygiene:` receipt and PT time, no person acting; a `hyg-decision-needed` note counts as nothing | **Not met: four of five lanes (§3).** Pull requests: bureau-pipeline #724, `hyg-check-rerun`, 15:41 PT on 10-04 (pass 3). Todo: DRE-5808, `hyg-card-closed`, 15:41 PT on 10-04 (pass 3). Triage: DRE-4968, `hyg-moved-to-review`, 08:47 PT on 10-05 (pass 20). Proofs: DRE-4973, `hyg-proof-closed`, 08:47 PT on 10-05 (pass 20). For each of these, no person acted on the row between the receipt and the read. **Green Light: nothing cleared** in the 24 passes, or in passes 25–28. The board read in §3 shows every Green Light row the agent saw and why it left each one, so the card's extension rule applies to that lane. The 13:28 PT hand pass on 10-04 did clear two Green Light rows (§1b), but it came before the window and a person started it, so it does not count. |
| A real business question in Green Light left in place and listed on the standing card's summary with its recommendation, PT time | **Met (§4).** DRE-5887 (DeltaSolv required status checks, a CEO decision) was listed `left` on DRE-5774's 10:44 PT summary on 10-05, with its recommendation, option A. It was still in Green Light at the 17:58 PT read, and nobody had acted on it. |
| Summary only in hours with change; one hour with no change and no summary; one hour where the same rows stood, nothing cleared, no summary | **Met (§5).** Ten summaries were posted in the window, and each one followed a pass whose left-row digest differed from the last summary's. Thirteen passes printed `hygiene: nothing changed — no action ran and the left set is 1d83d41b3ed5` and posted nothing: passes 5–14 (18:20 PT on 10-04 to 02:53 PT on 10-05) and 17–19 (05:56–07:48 PT on 10-05). Pass 5, at 18:20 PT, had no change and no summary. In pass 17, at 05:56 PT, the same left rows stood as in the 04:45 PT summary (digest `1d83d41b3ed5`), nothing was cleared, and no summary was posted. |
| Standing card outside Intake with `hand-built`, `no-code`, `agent:ops`, never offered by a groomer proposal, carrying only hygiene summaries; board read in the record | **Not met (§2).** DRE-5774 sits in Hand-work with `repo:bureau-pipeline`, `agent:ops`, `no-code`, `hand-built` and `initiative:bureau`. The day's one groomer proposal (DRE-4541, 06:18 PT on 10-05) does not name it. But besides its creation comment and the hygiene summaries, it carries one other comment: `🚨 unlanded-work-watchdog no branch`, posted by the pipeline's sweep at 19:56 PT on 10-04. The watchdog does not exclude the standing card. It says it posts "once per card", so it should not happen again. |
| Two scheduled dry passes before `HYGIENE_LIVE`, plus the hand dispatch with `dry_run` true, each with run id, PT time and `would:` lines; none wrote anything | **Met (§1).** Scheduled dry passes: 37223608815 (fired 11:13 PT) and 37229265771 (fired 12:41 PT). Hand dry dispatches: 37220103263 (10:20 PT) and 37223294890 (11:09 PT). Every log prints `dry_run=true`, and every action appears only as a `would:` line. A board read at 12:46 PT and agent-bureau #3139's comments show none of them wrote anything. `HYGIENE_LIVE` went on for good at 12:58:43 PT, after all four. It had been on briefly from 12:45:36 to 12:49:58 PT, and no pass ran in that time (§0). |
| No epic approved, no PR merged, no default branch pushed, no `break-glass` applied by the agent, against Actions logs and board | **Met (§6).** Every write the agent made in the window is listed in its ledgers. There are four kinds: a Linear comment, a Linear lane move to Done, In Review or Planning, `gh pr comment`, and `gh run rerun` / `gh workflow run <gate>`. There is no merge call, push, approval or label write. No lane move went into In Progress. The workflow's own token is `contents: read`. The five PRs the agent nudged were all merged later by `agent-bureau-qa-bot` through the merge gate. |
| Merged on `main` at `docs/hygiene-agent-proof.md`, opens with this table, PT time on every observation; CEO closes; the agent did not close it (DRE-5411 `hygiene_done.OWN_PROOF`) | **Not met yet: this pull request is the merge (§7).** The record opens with this table and gives a PT time for every observation. The agent did not close DRE-5412: every summary in the window lists it as `left DRE-5412 — this agent's own epic's proof — the CEO closes it`. The card stays open for the Green Light extension and the CEO. |

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

- **History: the switch was paused at 12:49:58 PT, before any live pass.** The fullstack session set `HYGIENE_LIVE` back to `false`. Read at 12:50 PT:

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

  Why: both scheduled dry passes would have sent DRE-5036 and DRE-3696 from Green Light back to Planning (§1), and those are epics the CEO returned to Green Light that morning. The CEO was asked which lane they belong in.
- **Then production mode.** The CEO briefly chose to keep DRE-5036 and DRE-3696 in Green Light as they were, with `HYGIENE_LIVE` left `false`. He then chose production mode, "Let hygiene finish their planning / put hygiene into production mode", relayed by the fullstack session. An earlier draft of this record stamped the keep-them decision "12:59 PT". That was a clock-label error in the relaying session: the decision came before the 12:58:43 PT switch. Read at 12:59 PT:

```
$ gh variable list --repo dreadnought-foundry/bureau-pipeline | grep HYG
HYGIENE_CARD	DRE-5774	2026-10-04T17:25:48Z
HYGIENE_LIVE	true	2026-10-04T19:58:43Z
```

- **The first live pass ran by hand at 13:28 PT** (run 37232211037, `dry_run=false`, started by `smeed652`). It cleared DRE-5036 and DRE-3696 to Planning and posted the first summary on DRE-5774 (§1b). It is a pre-window pass, because the card counts the day in scheduled passes.
- **The window opened at 14:15 PT** with run [37235338544](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37235338544): `schedule`, `dry_run=false`, success, on `c4fa502`. No scheduled run appeared between 12:41 and 14:15 PT. Which `37 * * * *` slot a run belongs to is inferred, because GitHub does not record it, and the card counts passes, not slots. This pass cleared nothing, because the 13:28 PT hand pass had already moved both Green Light epics. It posted a summary because its left-row digest moved: Triage now left DRE-5782 where the 13:29 summary had DRE-5781.
- **`HYGIENE_LIVE` stayed `true` all day.** Read at 17:50 PT on 10-05:

```
$ gh variable list --repo dreadnought-foundry/bureau-pipeline | grep HYG
HYGIENE_CARD	DRE-5774	2026-10-04T17:25:48Z
HYGIENE_LIVE	true	2026-10-04T19:58:43Z
```

  Every one of the 28 scheduled passes listed in §5 prints `hygiene: event=schedule dry_run=false`, except pass 25, whose first job never got a runner and so never logged.
- **`main` moved during the window, but the agent's code did not.** The passes ran on ten `main` heads, from `c4fa502` to `1fc325b` (§5). `gh api repos/dreadnought-foundry/bureau-pipeline/compare/c4fa502...1fc325b` lists 86 changed files across 104 commits, and none of them has `hygiene` in its name. A path-filtered commit list for `scripts/hygiene.py` and for `.github/workflows/hygiene.yml` since the window opened returns 0 commits for each (read 17:59–18:02 PT on 10-05).

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

**The first scheduled dry pass.** Run [37223608815](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37223608815), `schedule` on `main` at `0f4bf78`, created 11:13:48 PT on 2026-10-04, success. The log, trimmed:

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

Both of these runs would clear two rows in the **Green Light** lane: each would post a `hyg-resent-to-planning` receipt and move the card back to Planning (the lane in `scripts/hygiene_green_light.py`). In both runs the summary for DRE-5774 is itself a `would:` line, so the log shows no write of any kind. The board read below confirms it.

**The second scheduled dry pass.** Run [37229265771](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37229265771), `schedule` on `main` at `c4fa502`, created 12:41:54 PT on 2026-10-04, success. The log, trimmed:

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

**Criterion 5: met.** Two scheduled dry passes and two hand dry dispatches, each recorded with its run id, PT time and `would:` lines. All of them ran before `HYGIENE_LIVE` first went on at 12:45:36 PT, and so before it went on for good at 12:58:43 PT. None of them wrote anything.

| PT | run id | event | dry_run | actions / `would:` lines | summary |
|---|---|---|---|---|---|
| 10:20 | 37220103263 | workflow_dispatch | true | 0 in every lane; 16 left | log only (`HYGIENE_CARD` unset then) |
| 11:09 | 37223294890 | workflow_dispatch | true | Green Light: 2 (DRE-5036, DRE-3696 → Planning); 0 elsewhere; 14 left | `would:` only (4effb7806514) |
| 11:13 | 37223608815 | schedule | true | Green Light: 2 (DRE-5036, DRE-3696 → Planning); 0 elsewhere; 14 left | `would:` only (680c93735974) |
| 12:41 | 37229265771 | schedule | true | Green Light: 2 (the same); pull requests: 1 (gate re-dispatch, agent-bureau #3139); 0 elsewhere; 17 left | `would:` only (0a3dac74d839) |

## 1b. The first live pass, by hand, before the window (13:28 PT on 10-04)

**The run.** [37232211037](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37232211037), `workflow_dispatch` by `smeed652` (the CEO's "use it normally", relayed by the fullstack session), created 13:28:15 PT on 2026-10-04, success, on `main` at `c4fa502`. The log, trimmed:

```
hygiene: event=workflow_dispatch dry_run=false
hygiene: 2499 request(s) left on the fleet key, at or above the floor of 100
commented on DRE-5036
DRE-5036 → Planning
commented on DRE-3696
DRE-3696 → Planning
hygiene: dreadnought-foundry — 2 action(s), 0 failed, 15 left
hygiene: DeltaSolv — 0 action(s), 0 failed, 0 left
hygiene: EveryBite — 0 action(s), 0 failed, 0 left
commented on DRE-5774
```

**What it wrote, read once from Linear at 13:38 PT** (one query over the three cards):

- **DRE-5036**: moved `Green Light → Planning` at 13:29:02 PT by Agent-Bureau. Its receipt, at 13:29:02 PT:
  ```
  🧹 hygiene: hyg-resent-to-planning — planner died, limit-death kind=claude stage=plan at 2026-10-01 21:33 PT · 13:28 PT
  evidence: run 36964491515, limit-death comment 2026-10-02T04:33:25.996Z
  📎 pipeline-act: hygiene-resend-to-planning · kind: recovery · state: unchanged · next: plan.yml · discharges: nothing · subscriber: hygiene.yml · tag: hyg-resent-to-planning
  ```
- **DRE-3696**: moved `Green Light → Planning` at 13:29:03 PT by Agent-Bureau. Its receipt, at 13:29:03 PT:
  ```
  🧹 hygiene: hyg-resent-to-planning — classifier transport failure at 2026-10-02 08:25 PT · 13:28 PT
  evidence: transport comment 2026-10-02T15:25:58.792Z
  📎 pipeline-act: hygiene-resend-to-planning · kind: recovery · state: unchanged · next: plan.yml · discharges: nothing · subscriber: hygiene.yml · tag: hyg-resent-to-planning
  ```
  Before that, each card's last move was `In Progress → Green Light` at 10:24 PT by `bureau-tools`, the operator identity.
- **DRE-5774** (the standing card), still in Hand-work. Its first summary, at 13:29:13 PT, opens `🧹 hygiene: summary 37612657c36b · 13:29 PT`. It lists DRE-5036 and DRE-3696 as `cleared` in Green Light and every other row as `left`.

**This pass does not open the window, and it does not count toward criterion 1.** The card counts the day in scheduled passes: "let the clock run live for one working day — twenty-four scheduled passes". A hand dispatch is not the clock. This pass also came before the window, and a person (`smeed652`) started it, which is exactly what "no person acting" excludes. It is recorded because it is the only live evidence that the Green Light lane clears end to end: receipt, lane move and summary. It does not replace the Green Light extension in §3.

## 2. The standing card

DRE-5774, `Hygiene agent — hourly summary`. The operator's session created it, not this helper. Read once from Linear at 17:52 PT on 10-05:

- **Lane and labels:** Hand-work, not Intake. Labels: `repo:bureau-pipeline`, `agent:ops`, `no-code`, `hand-built` and `initiative:bureau`. `HYGIENE_CARD` has named it since 10:25 PT on 10-04.
- **History:** two entries, at 10:29:47 PT on 10-04 (by Frederick Conklin, the operator) and at 00:07:42 PT on 10-05 (by Linear's own system actor). Neither is a lane move; the second carries no field change the history API returns.
- **Comments: 16.** They are:
  - the creation comment, at 10:25:47 PT on 10-04, by `bureau-tools`: "Standing card for the hygiene agent's hourly summaries (DRE-5412's operator step). Hand-built, no-code; never built.";
  - 14 hygiene summaries, each opening `🧹 hygiene: summary <digest> · <HH:MM PT>`: one before the window (13:29 PT), ten in it, and three after it (§5);
  - **one comment that is not a hygiene summary:** `🚨 unlanded-work-watchdog no branch: this card has sat in Hand-work for 193 minutes with nothing to point at …`, posted at 19:56:12 PT on 10-04 by Agent-Bureau, tagged `subscriber: reconcile.yml · tag: unlanded-work-watchdog`.
- **Groomer.** DRE-4541 (`[OPERATOR] Daily groomer proposals`) received one proposal in the window, `🧺 groom-proposal: d50db9746997` at 06:18:07 PT on 10-05. It is 41,188 characters long and does not contain `DRE-5774`. The comments after it (`groom-held` at 11:07 and 11:08 PT, `groom-declined` at 11:10 PT) do not contain it either.

**Criterion 4: not met.** The card is outside Intake, is labeled as the card requires, and was never offered by a groomer proposal. But it does not carry "nothing but the hourly summary": the pipeline sweep's unlanded-work watchdog (`reconcile.yml`) posted on it, because that watchdog does not exclude the standing card. The notice says it is "posted once per card", so it should not recur on DRE-5774. The fix belongs to the watchdog, not to the hygiene agent: it should skip the card that `HYGIENE_CARD` names.

## 3. One row cleared per lane

Every row below was read three ways at 17:50–17:58 PT on 10-05:
- the pass's log (`gh run view <id> --repo dreadnought-foundry/bureau-pipeline --log`);
- the pass's `ledger-dreadnought-foundry` artifact (`gh run download <id> -n ledger-dreadnought-foundry`), for passes 5–28. The artifacts for passes 1–4 had already expired, so those rows rest on the log and the summary;
- the target's own thread: the Linear card's comments and history, or the pull request's timeline.

"No person acting" was checked on that thread, from the receipt to the read.

| lane | row | `🧹 hygiene:` receipt (verbatim first line) | PT | pass / run id | what a person would otherwise have done |
|---|---|---|---|---|---|
| pull requests | bureau-pipeline #724 | `🧹 hygiene: hyg-check-rerun — scripts unit tests red in run 37239311407, linear_ratelimited at head c43f860 · 15:41 PT` | 15:42:28 on 10-04 | 3 / 37240975688 | Seen the red check was a Linear rate-limit flake, not a code failure, and re-run it by hand. Between the receipt and the merge (16:59 PT, by `agent-bureau-qa-bot`), the timeline holds only bot events. |
| Green Light | **none in the window** | — | — | — | See "Green Light" below. |
| Todo | DRE-5808 | `🧹 hygiene: hyg-card-closed — #3156 merged 2026-10-04 14:35 PT · 15:41 PT` | 15:42:26 on 10-04 (moved `Hand-work → Done` at 15:42:27 by Agent-Bureau) | 3 / 37240975688 | Noticed the card's pull request had merged and closed the card by hand. No person moved or commented on the card after the receipt. The card sat in Hand-work, which the agent's Todo module reads alongside Todo, In Progress and In Review (`READ_LANES` in `scripts/hygiene_done.py`). No card sitting in the Todo state itself was cleared in the window. |
| Triage | DRE-4968 | `🧹 hygiene: hyg-moved-to-review — open pull request #715 · 08:47 PT` | 08:47:47 on 10-05 (moved `Triage → In Review` at 08:47:47 by Agent-Bureau) | 20 / 37335575695 | Seen that the card's pull request was still open, and moved it back to review. No person moved it after the receipt; it is still In Review at the read. |
| proofs | DRE-4973 | `🧹 hygiene: hyg-proof-closed — record docs/groomer-verify-proof.md at #714, 3 rows met · 08:47 PT` | 08:47:43 on 10-05 (moved `In Review → Done` at 08:47:44 by Agent-Bureau) | 20 / 37335575695 | Read the merged record and closed the proof card by hand. No person moved or commented on the card after the receipt. |

**Every other row the agent cleared in the window.** None of them changes a verdict above:
- **Pull requests:**
  - agent-bureau #3157, `hyg-gate-redispatched` (14:46 PT on 10-04, pass 2). **A person acted on this row:** `smeed652` dismissed a review at 14:47 PT and marked the PR ready at 15:03 PT, before the gate merged it. So it is not cited above.
  - bureau-pipeline #731, `hyg-check-rerun` (08:47 PT on 10-05, pass 20). Only bot events until the merge at 09:27 PT.
  - agent-bureau #3171, `hyg-gate-redispatched` (09:48 PT on 10-05, pass 21). A person acted on it about two hours later: `smeed652` dismissed a review at 11:40 PT.
- **Proofs:** DRE-5459, `hyg-proof-closed` (09:48 PT on 10-05, pass 21).
- **Triage:**
  - DRE-5627 and DRE-3447, `hyg-moved-to-review` (09:48 PT on 10-05, pass 21). The operator later closed both by hand at 11:33 and 14:54 PT.
  - DRE-5591, `hyg-moved-to-review` (11:51 PT on 10-05, pass 23).
- **Not counted:** the `hyg-decision-needed` note on bureau-pipeline #715 (08:47 PT, pass 20) is not a cleared row, as the card says. The two `suppressed` entries in the ledgers of passes 22 and 27 are not cleared rows either: each is the agent declining to repeat a gate re-dispatch it had already made.

**A pattern worth watching in Triage.** Each of the four cards the agent moved Triage → In Review had been moved In Review → Triage by Agent-Bureau, the pipeline's own identity, shortly before:

| card | moved into Triage | moved back by the agent | gap |
|---|---|---|---|
| DRE-4968 | 08:42 | 08:47 | 5 minutes |
| DRE-5627 | 08:48 | 09:49 | 61 minutes |
| DRE-3447 | 09:32 | 09:49 | 17 minutes |
| DRE-5591 | 11:49 | 11:51 | 2 minutes |

None of the four bounced back into Triage after the agent moved it. So the two automations did not loop within the day, but they disagree about where these cards belong.

**Green Light: nothing cleared, so the lane extends.** No pass in the window, nor passes 25–28 after it, cleared a Green Light row. The board read is the `Green Light` block of each summary on DRE-5774, which lists every Green Light row the agent saw and why it left it.

| summary | Green Light rows left | why each was left |
|---|---|---|
| 14:16 PT, 10-04 | DRE-5765, DRE-5519, DRE-5464, DRE-5035, DRE-3698 | no planning receipt; planner question; critic SEND_BACK; critic SEND_BACK; planner question |
| 08:47 PT, 10-05 | DRE-5849 added | planner question |
| 10:44 PT, 10-05 | DRE-5887, DRE-5886, DRE-5883 added | three new planner questions |
| 11:51 PT, 10-05 | DRE-5887, DRE-5886, DRE-5883, DRE-5849 | DRE-5765, DRE-5519, DRE-5464, DRE-5035 and DRE-3698 had left Green Light |

A planner question, a critic SEND_BACK and a missing planning receipt are each a person's call by the lane's design. So the board held nothing in Green Light that the agent is built to clear. The lane read at 17:58 PT on 10-05 shows nine cards in Green Light: DRE-5887, DRE-5886, DRE-5883, DRE-5849, DRE-5036, DRE-5035, DRE-3700, DRE-3699 and DRE-3624. **Under the card's rule, the Green Light lane's window extends to the next working day**, and criterion 1 stays not met until a scheduled pass clears a Green Light row on its own.

## 4. The business question left in Green Light

**DRE-5887**, `deltasolv: required status checks on main — make the CI jobs required, or keep the merge gate's CI-green rule as the only guard (CEO decision, split out of DRE-5869)`.
- **How it reached Green Light.** The planner escalated it at 09:57:55 PT on 10-05 (`🙋 planning-escalation: DRE-5887 needs a decision from you before it can be planned`).
- **What the agent posted.** It was listed on DRE-5774's summary `c3f065a9fbee · 10:44 PT`, and again at 11:51, 14:44, 15:42 and 16:41 PT, as:

  ```
  - left DRE-5887 — the planner asks a question, posted 2026-10-05 09:57 PT — recommend: Should DeltaSolv's main line refuse every merge, including ones a person makes by hand, until its automated checks have passed, the way agent-bureau and portico already do (option A, recommended), or should it stay as it is, where only the pipeline's own merges are held to passing checks and a by-hand merge is not (option B)?
  ```

- **Left in place.** At the 17:58 PT read it is still in Green Light. Every comment on it is from Agent-Bureau, and every one of them predates the agent's first listing.

The other planner questions in Green Light (DRE-5886, DRE-5883, DRE-5849, DRE-5519, DRE-3698) were also left. Each one's recommendation reads `the escalation states no recommendation line — the question is on the card`, because the planner's escalation carried none.

## 5. Every live pass in the window

How this was read, at 17:50–17:59 PT on 10-05:
- `gh run list --repo dreadnought-foundry/bureau-pipeline --workflow hygiene.yml --created '>=2026-10-04T21:00:00Z' --limit 60 --json databaseId,event,createdAt,conclusion,headSha`;
- each run's `--log`;
- DRE-5774's comments, read once.

**Every row is `schedule`.** There was no `workflow_dispatch` after 13:28 PT on 10-04.

Terms in the table:
- **"Executed"** counts the ledger entries whose outcome is `executed`. Before pass 5 it is the log's action count, because those ledgers had expired.
- **"Nothing changed"** means the log line `hygiene: nothing changed — no action ran and the left set is 1d83d41b3ed5`.

| # | run id | PT | head | conclusion | executed | summary on DRE-5774 | what changed |
|---|---|---|---|---|---|---|---|
| 1 | 37235338544 | 14:15 on 10-04 | c4fa502 | success | 0 | posted `8b30511228b2 · 14:16` | left set moved: DRE-5782 newly left in Triage, DRE-5781 gone |
| 2 | 37237387233 | 14:45 | c4fa502 | success | 1 | posted `553437befccf · 14:47` | cleared agent-bureau #3157 (gate re-dispatch) |
| 3 | 37240975688 | 15:41 | eacd249 | success | 2 | posted `ad32c3d97a1e · 15:42` | cleared DRE-5808 (card closed), bureau-pipeline #724 (check re-run) |
| 4 | 37244704652 | 16:42 | d612f38 | success | 0 | posted `1d83d41b3ed5 · 16:43` | left set moved: DRE-3624 gone from Triage |
| 5 | 37251059873 | 18:20 | 1800714 | success | 0 | none | nothing changed |
| 6 | 37253962144 | 19:05 | 1800714 | success | 0 | none | nothing changed |
| 7 | 37257547785 | 19:59 | 1800714 | success | 0 | none | nothing changed |
| 8 | 37261426669 | 20:56 | 1800714 | success | 0 | none | nothing changed |
| 9 | 37266510956 | 22:09 | 1800714 | success | 0 | none | nothing changed |
| 10 | 37269846508 | 22:54 | 111a5e7 | success | 0 | none | nothing changed |
| 11 | 37276817222 | 00:16 on 10-05 | 111a5e7 | success | 0 | none | nothing changed |
| 12 | 37281141797 | 01:02 | 111a5e7 | success | 0 | none | nothing changed |
| 13 | 37286948047 | 01:58 | 111a5e7 | success | 0 | none | nothing changed |
| 14 | 37293039403 | 02:53 | 111a5e7 | success | 0 | none | nothing changed |
| 15 | 37298766253 | 03:47 | 111a5e7 | success | 0 | posted `0084d615da6f · 03:48` | left set moved, by a failed read: `DRE-5072 skipped this pass, a read failed: gh pr list … GraphQL: Something went wrong` |
| 16 | 37304809404 | 04:43 | 111a5e7 | success | 0 | posted `1d83d41b3ed5 · 04:45` | left set back to `1d83d41b3ed5` (DRE-5072 read again) |
| 17 | 37313046827 | 05:56 | 111a5e7 | success | 0 | none | nothing changed |
| 18 | 37319866870 | 06:50 | 111a5e7 | success | 0 | none | nothing changed |
| 19 | 37327722533 | 07:48 | 111a5e7 | success | 0 | none | nothing changed |
| 20 | 37335575695 | 08:46 | 442dc2c | success | 4 | posted `3af906eb611d · 08:47` | cleared DRE-4973 (proof), DRE-4968 (Triage), bureau-pipeline #731 (re-run); `hyg-decision-needed` on bureau-pipeline #715 |
| 21 | 37343555633 | 09:47 | bc5ce65 | success | 4 | posted `9ca5185df62b · 09:49` | cleared DRE-5459 (proof), DRE-5627 and DRE-3447 (Triage), agent-bureau #3171 (gate re-dispatch) |
| 22 | 37350554090 | 10:43 | 6a321ed | success | 0 (1 suppressed) | posted `c3f065a9fbee · 10:44` | left set moved: DRE-5887, DRE-5886, DRE-5883 newly in Green Light |
| 23 | 37359039176 | 11:50 | 601899e | success | 1 | posted `33d4edb95f15 · 11:51` | cleared DRE-5591 (Triage) |
| 24 | 37365222413 | 12:43 | 601899e | **failure** | 0 | none | see below |

**Pass 24 failed, and so did the next pass.**
- **Pass 24's lane jobs never ran.** Its three `Keep <owner>` jobs never got a runner. Each was canceled after 15 minutes, with the annotation `The job was not acquired by Runner of type hosted even after multiple attempts`. Its `Summarize` job then failed on `FileNotFoundError: … 'ledgers/ledger-*.json'`, without writing anything. The summary step should treat "no ledgers" as "nothing to summarize" rather than crash; that is a small defect in `scripts/hygiene.py` `cmd_summarize`.
- **Pass 25 never started.** Run 37371810501, at 13:46 PT, failed the same way at its first job, `Read the board`.
- **The count holds either way.** If pass 24 is not counted because its lanes never ran, the 24th scheduled pass that ran its lanes is pass 26 (run 37377761515, 14:43 PT). Nothing in this record's verdicts changes.

**After the window (not counted toward the 24):**
- Pass 26, run 37377761515, 14:43 PT: cleared bureau-pipeline #740 (gate re-dispatch); summary `2b6931feda51 · 14:44`.
- Pass 27, run 37384077325, 15:41 PT: 0 executed, 1 suppressed; summary `8e3a24fc10ae · 15:42` (the left set moved: DRE-5898, DRE-5893 and DRE-5152 newly left in Triage).
- Pass 28, run 37389869140 on `1fc325b`, 16:40 PT: cleared DRE-5591 (`hyg-proof-closed`); summary `2043d5157a69 · 16:41`.

None of these cleared a Green Light row either.

**Criterion 3: met.**
- **Summaries only when something changed.** Each of the ten summaries in the window has a digest different from the one before it, and each followed a pass that either cleared a row or saw a different set of left rows.
- **Hours with no summary.** Thirteen passes (5–14 and 17–19) changed nothing and posted nothing.
- **"No change, no summary".** Pass 5, at 18:20 PT on 10-04.
- **"The same left rows stood, nothing cleared, no summary".** Pass 17, at 05:56 PT on 10-05. Its left set `1d83d41b3ed5` is the one the 04:45 PT summary had just listed.
- **One noisy summary.** Pass 15's summary was caused by a failed read (DRE-5072 dropped out of the left set for one pass), not by a real change on the board. Pass 16 posted another summary when it came back. That is two summaries for no real change: a small noise source, not a breach of the rule as written.

## 6. What the agent did not do

Read at 17:55–18:00 PT on 10-05:
- **Its own record of every write.** I took the union of the `writes` lists in every ledger artifact of passes 5–28: 22 runs × 3 owners, read with `gh run download <id> -n ledger-<owner>`. The only writes are:
  - `comment DRE-<n>: 🧹 hygiene: …`;
  - `state DRE-<n> → Done` or `→ In Review`;
  - `gh pr comment <n> … '🧹 hygiene: …'`;
  - `gh run rerun <run> --failed`;
  - `gh workflow run merge-gate.yml` / `self-merge-gate.yml -f pr_number=<n>`.

  There is no merge, push, review approval or label write in any ledger.
- **What the logs show for passes 1–4.** Their artifacts had expired. Their logs show only `commented on <card>`, `DRE-5808 → Done` and the summary comment. Their summaries name one gate re-dispatch (#3157) and one check re-run (#724).
- **No epic approved.** No lane move the agent made went into In Progress. Its moves went to Done (DRE-5808, DRE-4973, DRE-5459, DRE-5591), In Review (DRE-4968, DRE-5627, DRE-3447, DRE-5591), and, before the window, Planning (DRE-5036, DRE-3696). The Green Light → In Progress move is the CEO's Approve, and the agent never made it.
- **No pull request merged by the agent.** The five PRs it nudged were all merged by `app/agent-bureau-qa-bot` through the merge gate: agent-bureau #3157 (15:03 PT on 10-04), bureau-pipeline #724 (16:59 PT on 10-04), bureau-pipeline #731 (09:27 PT on 10-05), agent-bureau #3171 (11:49 PT on 10-05) and bureau-pipeline #740 (16:10 PT on 10-05). A gate re-dispatch asks the gate to look again; the gate decides.
- **No default branch pushed.** The Hygiene workflow's own token is `permissions: contents: read, actions: read` (`.github/workflows/hygiene.yml`, line 68). The App token it mints is used only for the `gh` calls listed above.
- **No `break-glass`.** No ledger write and no log line applies a label of any kind.

**Criterion 6: met.**

## 7. The console roster's `hygiene` entry

The registry the console's roster reads, `agents.yaml` on bureau-pipeline's `main` (read 17:59 PT on 10-05), carries the entry DRE-5369 added:

```
  - name: hygiene
    role: Hygiene Agent
    category: operations
    kind: scripted
    model: null
    trigger: "schedule — hourly at :37, over every repo in config/repo-map.json"
    maxBudget: 0
    workflow: .github/workflows/hygiene.yml
```

**Not observed:** what the production console's roster page renders for this entry. This session did not open the console. That is a below-the-table item, not one of the seven criteria.

**DRE-5412 itself.** The agent never closed it. Every summary in the window lists it as `left DRE-5412 — this agent's own epic's proof — the CEO closes it — recommend: the CEO reads the record and closes the card`, the exclusion DRE-5411 builds (`OWN_PROOF = "DRE-5412"` in `scripts/hygiene_done.py`).

## 8. Read-off steps

These are the steps this record was completed with. Run them with any operator read token; none of them writes.

1. **Every pass.** `gh run list --repo dreadnought-foundry/bureau-pipeline --workflow hygiene.yml --created '>=2026-10-04T21:00:00Z' --limit 60 --json databaseId,event,createdAt,conclusion,headSha`. Convert each `createdAt` to PT (UTC−7).
2. **What each pass did.** `gh run view <id> --repo dreadnought-foundry/bureau-pipeline --log | grep -E "hygiene:|commented on|→"`. Then `gh run download <id> --repo dreadnought-foundry/bureau-pipeline -n ledger-dreadnought-foundry` for the ledger's `actions[].outcome` and `writes`. A pull-request action prints nothing in the log, so only its ledger and the PR comment show it. An `executed` action is a cleared row, except a `hyg-decision-needed` note; a `suppressed` one is a repeat the agent declined. Ledger artifacts expire about a day after their run.
3. **The receipts on the board.** For each cleared row, read the card's comments and history once, or the PR's `issues/<n>/timeline`. Check for any person's event between the receipt and the read.
4. **The summaries.** Read DRE-5774's comments once, with `comments(first:100)`, and match each summary to its pass by time. A pass with a `nothing changed` log line and no comment is a quiet hour. Read DRE-4541's comments in the window once, and search each for `DRE-5774`.
5. **What it did not do.** Take the union of every ledger's `writes`; check the workflow's `permissions:`; and run `gh pr view <n> --json mergedBy` for each PR the agent nudged.
6. **The roster.** Open the console's agent roster and record what the `hygiene` entry shows. This was not done in this read (§7).
7. **The Green Light extension (still to do).** Read the next working day's scheduled passes for a Green Light row the agent clears on its own, with its receipt, and add it to §3. Criterion 1 is met only then.

## What is not proven, and why

- **Green Light, criterion 1.** No scheduled pass in the day cleared a Green Light row, because the lane held nothing the agent is built to clear (§3). The card extends that lane's window to the next working day. The only live Green Light clears so far are the two from the 13:28 PT hand pass on 10-04, which do not count.
- **The standing card, criterion 4.** The unlanded-work watchdog posted one notice on DRE-5774 at 19:56 PT on 10-04 (§2). The fix is a skip in that watchdog for the card `HYGIENE_CARD` names. It is not a hygiene-agent defect, and the record does not hide it.
- **The merge, criterion 7.** It is met only when this pull request merges. The CEO, or the operator under the CEO's 2026-10-02 proof-by-system rule, closes DRE-5412 after the Green Light extension. The agent never does.
- **Passes 24 and 25 failed for want of a GitHub-hosted runner** (§5). The count of 24 does not depend on them.
- **Small defects seen, none affecting a verdict:**
  - `cmd_summarize` crashes when no ledger arrives (pass 24);
  - a failed read changes the left set, which posts a summary for no real change (passes 15 and 16);
  - two automations disagree about In Review vs. Triage for cards with an open pull request (§3).
- **The console roster's rendering of the `hygiene` entry** was not observed (§7).
