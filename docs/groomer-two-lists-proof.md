# One real groomer morning ran end to end on 2026-09-26, 1 minute 55 seconds late, and the exclusions left Intake by the CEO's own hand — the proof record

The proof for [DRE-4736](https://linear.app/dreadnoughtfoundry/issue/DRE-4736),
from epic [DRE-4677](https://linear.app/dreadnoughtfoundry/issue/DRE-4677).
Every time below is Pacific (PDT, UTC−7). Observed by hand, read-only, against
the live Actions runs in this repository and the live Linear board, on
2026-09-26 between 08:53 and 08:58 PT.

**What happened.** The clock started the groomer with nobody dispatching it.
The groomer posted a 14-card proposal (13 Planning, 1 Cancel) on the standing
card DRE-4541 at **06:31:55 PT**. The CEO took 10 cards out of the batch from
the console between 07:40 and 08:38 PT and approved at 08:38:34 PT. The
approval started the drain 2 seconds later. The drain moved exactly the 3
remaining Planning cards to `Planning`, canceled the 1 Cancel card with its
reason written on it, and held all 10 excluded cards back. It refused nothing
and found nothing already gone.

**Two criteria do not hold as written.**

- The proposal was **not** posted before 06:30 PT. It was 1 minute 55 seconds
  late: GitHub started the 06:15 cron 6 minutes 36 seconds late, and the groom
  step took 10 minutes against the eight the workflow budgets (§2).
- The excluded cards are **not** still in Intake. The drain did not move any of
  them. The CEO used the console's "Don't do this card" button, which writes the
  exclusion and then moves the card to Backlog in the same click. The card
  assumed the "Hold this card" button, which writes only the exclusion (§3).

---

## 1. The schedule fired itself

```
$ gh run list -R dreadnought-foundry/bureau-pipeline --workflow self-groomer.yml -L 15 \
    --json databaseId,event,createdAt,startedAt,updatedAt,conclusion,status,displayTitle
{"conclusion":"success","createdAt":"2026-09-26T15:38:36Z","databaseId":36252648554,"displayTitle":"groom-drain","event":"repository_dispatch",...,"updatedAt":"2026-09-26T15:38:58Z"}
{"conclusion":"success","createdAt":"2026-09-26T14:23:35Z","databaseId":36248347405,"displayTitle":"Groomer","event":"schedule",...,"updatedAt":"2026-09-26T14:23:44Z"}
{"conclusion":"success","createdAt":"2026-09-26T13:21:36Z","databaseId":36244896914,"displayTitle":"Groomer","event":"schedule",...,"updatedAt":"2026-09-26T13:32:02Z"}
{"conclusion":"success","createdAt":"2026-09-22T02:50:38Z","databaseId":35680949296,"displayTitle":"Groomer","event":"workflow_dispatch",...}
```

No `workflow_dispatch` run of `self-groomer.yml` exists after 2026-09-21 19:50 PT.
The morning's two runs are both `schedule`.

| | The run that did the work | The other cron |
|---|---|---|
| Run | [36244896914](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36244896914) | [36248347405](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36248347405) |
| Event | `schedule` | `schedule` |
| Cron line | `15 13 * * *` (06:15 PT in summer) | `15 14 * * *` (07:15 PT in summer) |
| Started | 06:21:36 PT | 07:23:35 PT |
| Gate `why` | `it is 06:21 PT and DRE-4541 is open — the groomer runs` | `it is 07:23 PT, outside the 06:00-06:59 PT grooming hour — this is the other cron of the pair and it does nothing` |
| `schedule` job | `schedule / groom` ran 06:21:46 → 06:32:02 PT, success | skipped (`go=false`), run green |

The gate step, from each run's log (`gh run view <id> -R dreadnought-foundry/bureau-pipeline --log`):

```
gate  2026-09-26T13:21:41.1142772Z ##[group]Run python3 .bureau-pipeline/scripts/groom_schedule_gate.py --card "DRE-4541"
gate  2026-09-26T13:21:42.2341966Z it is 06:21 PT and DRE-4541 is open — the groomer runs

gate  2026-09-26T14:23:42.0268022Z ##[group]Run python3 .bureau-pipeline/scripts/groom_schedule_gate.py --card "DRE-4541"
gate  2026-09-26T14:23:42.1899810Z it is 07:23 PT, outside the 06:00-06:59 PT grooming hour — this is the other cron of the pair and it does nothing
```

`--card "DRE-4541"` is `vars.GROOM_PROPOSAL_CARD` as the workflow rendered it,
so DRE-4541 is the standing card this morning's proposal belonged on.

**Which cron did the work is an inference.** The runs API does not carry the
cron string. Run 36244896914 started at 13:21:36 UTC, and the `15 14` line
cannot fire before 14:15 UTC, so it was the `15 13` line. The gate's printed
hour agrees.

**The `actor` on both scheduled runs reads `agent-bureau-qa-bot[bot]`. That is
not a dispatch.** GitHub credits a scheduled run to whoever last changed the
cron on the default branch. The cron came in with PR #518 (DRE-4724), and the
QA bot merged it:

```
$ gh pr view 518 -R dreadnought-foundry/bureau-pipeline --json number,mergedBy,mergedAt,mergeCommit
518	app/agent-bureau-qa-bot	2026-09-26T00:22:49Z	aa39b0e29
```

That merge was at 2026-09-25 17:22 PT. The run's `event` is `schedule`, and
that is the field that says the clock started it.

## 2. The proposal

The proposal is comment `25966c8e-464f-4f64-899e-a5a757eba626` on DRE-4541,
posted by Agent-Bureau at **06:31:55 PT**. Its first lines:

```
🧺 groom-proposal: b9eecae64787

# Groom proposal `b9eecae64787` — cycle 14

14 cards of 264 in Intake are proposed for cycle 14, in the order below. Of those, 13 for Planning and 1 for Cancel. Nothing moves until you approve it.
```

The run log shows the same post, one second before the model-attempt receipt:

```
schedule / groom  2026-09-26T13:31:54.8682525Z groomer: the one ranked read was made with an output budget of 58580 token(s)
schedule / groom  2026-09-26T13:31:55.6392001Z wrote proposal.json (b9eecae64787)
schedule / groom  2026-09-26T13:31:55.6392416Z commented on DRE-4541
```

**Counts.** 13 Planning rows and 1 Cancel row, 14 in all, under the cap of 20.

**Every Cancel reason, quoted.** There is one:

```
| 1 | DRE-3526 | — | bureau-pipeline | — | … | evidence: DRE-4630 supersedes it |
```

**Creation dates.** Every card on the proposal was created before 2026-09-12,
so every one was more than 14 days old on the morning.

| # | Card | List | Created |
|---|---|---|---|
| 1 | DRE-2348 | Planning | 2026-08-09 22:25 PT (the oldest) |
| 2 | DRE-2373 | Planning | 2026-08-10 12:11 PT |
| 3 | DRE-2382 | Planning | 2026-08-11 12:19 PT |
| 4 | DRE-2441 | Planning | 2026-08-13 21:16 PT |
| 5 | DRE-2456 | Planning | 2026-08-13 21:33 PT |
| 6 | DRE-2457 | Planning | 2026-08-13 21:33 PT |
| 7 | DRE-2458 | Planning | 2026-08-13 21:34 PT |
| 8 | DRE-2897 | Planning | 2026-08-31 18:02 PT |
| 9 | DRE-2459 | Planning | 2026-08-14 16:28 PT |
| 10 | DRE-2462 | Planning | 2026-08-14 16:29 PT |
| 11 | DRE-2463 | Planning | 2026-08-14 16:29 PT |
| 12 | DRE-2957 | Planning | 2026-09-01 18:53 PT |
| 13 | DRE-2959 | Planning | 2026-09-01 18:54 PT |
| — | DRE-3526 | Cancel | 2026-09-09 20:13 PT |

**Left out for age?** The proposal says of its own order: "Urgent first, then
High, then everything else — oldest first, whatever repo it is in, and no card
is left out for its age." It also says 250 cards are "not now" and 246 of them
carry the cycle they come back in. That is the groomer's own statement. This
record did not check the 250 one by one (see "What is not proven").

**Why it was late.** The proposal missed 06:30 PT by 1 minute 55 seconds. The
run log shows where the time went:

| Step | UTC in the log | PT |
|---|---|---|
| The `15 13` cron is due | — | 06:15:00 |
| GitHub creates the run | 13:21:36 | 06:21:36 (6 min 36 s after the cron) |
| Gate says go | 13:21:42 | 06:21:42 |
| `schedule / groom` job starts | 13:21:46 | 06:21:46 |
| Groom step starts | 13:21:53 | 06:21:53 |
| The ranked read returns | 13:31:54 | 06:31:54 (10 min 1 s after the step started) |
| Proposal comment posted | 13:31:55 | 06:31:55 |

`self-groomer.yml` says why the cron sits at 06:15: "The run takes about eight
minutes, so DRE-4677 moved it to 06:15 PT." This morning both halves ran long.
The queue delay alone ate 6 of the 15 minutes of margin. The groom step ran 2 minutes
over its eight. Either one alone would have fit. Together they did not. The
other cron ran 8 minutes 35 seconds late the same morning, so the delay was
GitHub's scheduler and not this workflow.

## 3. The decision

Every decision was made in the console and posted to DRE-4541 with a signed
console receipt. The user id `88c1b3e0-7031-70df-dae2-99dc7efea1ec` is the
CEO's console login. Signatures are trimmed here; the full text is on the card.

**The approval**, comment `99997f50-4d98-4d07-bcf8-ee5a0e5ae0c4`, 08:38:34 PT:

```
🧺 groom-approved: b9eecae64787

Decided in the console by Sid Conklin (tenant owner) at 2026-09-26 08:38 PT.
🔏 console-receipt: v1 card=DRE-4541 proposal=b9eecae64787 user=88c1b3e0-7031-70df-dae2-99dc7efea1ec at=2026-09-26T15:38:33Z kid=7d1f6e507b7bc4ad sig=-1jshNw_…
```

**Ten exclusions**, not one. All ten were on the Planning list. None was on the
Cancel list.

| Excluded | Posted | Receipt `at=` | Then, from the console |
|---|---|---|---|
| DRE-2348 | 07:40:58 PT | 2026-09-26T14:40:58Z | Intake → Backlog 07:41:01 PT |
| DRE-2456 | 08:35:03 PT | 2026-09-26T15:35:02Z | Intake → Backlog 08:35:04 PT |
| DRE-2457 | 08:35:08 PT | 2026-09-26T15:35:07Z | Intake → Backlog 08:35:13 PT |
| DRE-2458 | 08:35:20 PT | 2026-09-26T15:35:19Z | Intake → Backlog 08:35:24 PT |
| DRE-2897 | 08:35:28 PT | 2026-09-26T15:35:27Z | Intake → Backlog 08:35:34 PT |
| DRE-2957 | 08:35:45 PT | 2026-09-26T15:35:44Z | Intake → Backlog 08:35:46 PT |
| DRE-2959 | 08:37:03 PT | 2026-09-26T15:37:03Z | Intake → Backlog 08:37:08 PT |
| DRE-2441 | 08:37:20 PT | 2026-09-26T15:37:19Z | Intake → Backlog 08:37:24 PT |
| DRE-2382 | 08:37:34 PT | 2026-09-26T15:37:34Z | Intake → Backlog 08:37:39 PT |
| DRE-2373 | 08:38:18 PT | 2026-09-26T15:38:17Z | **Canceled** → Backlog 08:38:22 PT |

The first one, as posted:

```
🧺 groom-excluded: b9eecae64787 DRE-2348

Decided in the console by Sid Conklin (tenant owner) at 2026-09-26 07:40 PT.
🔏 console-receipt: v1 card=DRE-4541 proposal=b9eecae64787 user=88c1b3e0-7031-70df-dae2-99dc7efea1ec at=2026-09-26T14:40:58Z kid=7d1f6e507b7bc4ad sig=iBTzJv_d…
```

**Each exclusion was followed, 1 to 5 seconds later, by a move to Backlog.**
Each moved card carries `Moved to Backlog by Sid Conklin (88c1b3e0-…) via the
console`. That is the console's "Don't do this card" button. `drop()` in
agent-bureau's `console/web/src/components/oneriver/GroomProposalRow.tsx`
(DRE-3757) posts the exclusion first, then moves the card to Backlog. The
other button, "Hold this card", posts the exclusion and nothing else, and
leaves the card in Intake. This morning the CEO pressed "Don't do" ten times.

## 4. The drain, with no hand start

| | |
|---|---|
| Run | [36252648554](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36252648554), title `groom-drain` |
| Event | `repository_dispatch` (type `groom-drain`), not `workflow_dispatch` |
| Actor | `agent-bureau-bot[bot]` (the bureau bot, App 3350400) |
| Started | 08:38:36 PT, 2 seconds after the approval |
| Finished | 08:38:58 PT, success |

The drain checked the approval's receipt before it moved anything:

```
drain / groom  2026-09-26T15:38:47.2261213Z groomer: `🧺 groom-approved: b9eecae64787` on DRE-4541 is honoured on a console receipt — console user 88c1b3e0-7031-70df-dae2-99dc7efea1ec, signed 2026-09-26T15:38:33Z
drain / groom  2026-09-26T15:38:51.8112678Z DRE-2459 → Planning
drain / groom  2026-09-26T15:38:51.8113071Z DRE-2462 → Planning
drain / groom  2026-09-26T15:38:51.8113367Z DRE-2463 → Planning
drain / groom  2026-09-26T15:38:51.8113902Z DRE-3526 → Canceled
drain / groom  2026-09-26T15:38:51.8120642Z   "already_gone": [],
drain / groom  2026-09-26T15:38:51.8121076Z   "refused": [],
```

The `🧺 groom-drained` record, comment `d503b824-bb19-49c8-a1af-50b18b750efe` on
DRE-4541, 08:38:51 PT, quoted whole:

```
🧺 groom-drained: b9eecae64787

moved: 3 · held back: 10 · added: 0 · cancelled: 1 · refused: 0 → Planning at 2026-09-26 08:38 PT

Out of Intake, into cycle 14, in the order proposal `b9eecae64787` was approved in.

| # | Card | Outcome | Why |
| -- | -- | -- | -- |
| — | DRE-2348 | held back | `🧺 groom-excluded` |
| — | DRE-2373 | held back | `🧺 groom-excluded` |
| — | DRE-2382 | held back | `🧺 groom-excluded` |
| — | DRE-2441 | held back | `🧺 groom-excluded` |
| — | DRE-2456 | held back | `🧺 groom-excluded` |
| — | DRE-2457 | held back | `🧺 groom-excluded` |
| — | DRE-2458 | held back | `🧺 groom-excluded` |
| — | DRE-2897 | held back | `🧺 groom-excluded` |
| 1 | DRE-2459 | moved | proposal `b9eecae64787` position 9 |
| 2 | DRE-2462 | moved | proposal `b9eecae64787` position 10 |
| 3 | DRE-2463 | moved | proposal `b9eecae64787` position 11 |
| — | DRE-2957 | held back | `🧺 groom-excluded` |
| — | DRE-2959 | held back | `🧺 groom-excluded` |
| — | DRE-3526 | cancelled | proposal `b9eecae64787` Cancel position 1 — evidence: DRE-4630 supersedes it |
```

The five clauses add up: 3 moved + 10 held back + 1 canceled = the 14 cards
proposed. There is no `already gone` row. Nothing was added and nothing was
refused.

## 5. The board, read afterwards

Read at **2026-09-26 08:58 PT**, from each card's Linear state and state
history (a GraphQL read through `scripts/linear-api.py`'s `gql()` in
agent-bureau). Titles are left out on purpose: this repository is public, and
the lane is what the card asks for.

| Card | List | What the drain did | Lane now | Who moved it since the drain |
|---|---|---|---|---|
| DRE-2459 | Planning | moved to `Planning` 08:38:48 | **Planning** | nobody — the planner is working it as an epic |
| DRE-2462 | Planning | moved to `Planning` 08:38:49 | Backlog | the planner, 08:43:46: one-off, FLEET, plan critic PASS |
| DRE-2463 | Planning | moved to `Planning` 08:38:50 | **Planning** | nobody — the planner is working it as an epic |
| DRE-3526 | Cancel | moved to `Canceled` 08:38:51 | **Canceled** | nobody |
| DRE-2348 | Planning | held back | Canceled | console → Backlog 07:41; hand cancel 08:05:55 (Atlas clear-out) |
| DRE-2373 | Planning | held back | Backlog | hand cancel 08:05:53 (Atlas clear-out); console → Backlog 08:38:22 |
| DRE-2382 | Planning | held back | Backlog | console → Backlog 08:37:39 |
| DRE-2441 | Planning | held back | Backlog | console → Backlog 08:37:24 |
| DRE-2456 | Planning | held back | Backlog | console → Backlog 08:35:04 |
| DRE-2457 | Planning | held back | Backlog | console → Backlog 08:35:13 |
| DRE-2458 | Planning | held back | Backlog | console → Backlog 08:35:24 |
| DRE-2897 | Planning | held back | Canceled | console → Backlog 08:35:34; promoter → Todo 08:40:27; agent run; → Backlog 08:49:01; hand cancel ~08:58 (already fixed under DRE-3230) |
| DRE-2957 | Planning | held back | Backlog | console → Backlog 08:35:46 |
| DRE-2959 | Planning | held back | Backlog | console → Backlog 08:37:08 |

**One Planning card is in `Planning`.** DRE-2459 and DRE-2463 are both there.
DRE-2462 also reached `Planning` from the drain. The planner then classified
it as one card and one pull request and moved it on to Backlog at 08:43:46.
That later move is the planner's work, not the drain's.

**One Cancel card is in `Canceled`, not `Done`, with its reason.** DRE-3526 went
Intake → Canceled at 08:38:51, and at 08:38:50 it received:

```
🧺 groom-cancelled: b9eecae64787 — evidence: DRE-4630 supersedes it
```

**No excluded card is in Intake.** The drain held all ten back and moved none
of them. Every one had already left Intake through the console's "Don't do"
move before the approval, as §3 shows. So the criterion "every excluded card
is still in Intake" does not hold this morning. What the board does prove is
narrower: the drain did not touch an excluded card.

**DRE-2348 was not moved by the drain.** The CEO excluded it at 07:40:58 and
the console moved it to Backlog at 07:41:01. At 08:05:55 it was canceled by
hand for an unrelated reason. The comment reads: "CEO decision 2026-09-26:
Atlas is old and we are clearing every open Atlas card from the board." The
drain record lists it as `held back`.

## 6. What did not go to plan

**The proposal was late.** It posted at 06:31:55 PT against a 06:30 PT
deadline. §2 has the timing. A second morning needs to run on time before this
criterion can hold.

**The console reopened a card the CEO had just canceled.** DRE-2373 was
canceled at 08:05:53 PT in the Atlas clear-out. At 08:38:18 PT it was excluded
with "Don't do", and the console's move put it back in Backlog at 08:38:22. It
is an open card again now. The "Don't do" button moves a card to Backlog from
whatever lane it is in, `Canceled` included. The drain was not involved. It
read DRE-2373 as `held back` because the exclusion was newer than the cancel,
so it is not an `already gone` row.

**A card the CEO declined was built on five minutes later.** DRE-2897 went to
Backlog by "Don't do" at 08:35:34. It carried a FLEET routing verdict and no
open blockers, so the reconcile promoter moved it to Todo at 08:40:27
("🧹 Auto-promoted Backlog → Todo: no parent epic, a FLEET verdict, and all
blockers Done"). A build agent ran in portico
([run 36252765166](https://github.com/dreadnought-foundry/portico/actions/runs/36252765166)),
found the defect already fixed under DRE-3230, and stopped. The card was then
canceled by hand at about 08:58. It cost one agent run and no code. In Backlog,
a card with a FLEET verdict is one sweep away from a build, and "Don't do"
leaves it there.

**Not faults, recorded so they are not misread.** DRE-2957 and DRE-2959 each
received `🚨 routing-no-verdict` at 08:40:28. That is the promoter declining to
promote Backlog cards that have no verdict. The evening before, at 20:05 PT on
2026-09-25, DRE-2458 went Intake → Done → Intake within 9 seconds, by
`bureau-sandbox`. That was before the proposal. It did not change the card's
lane on the morning.

## What is not proven, and why

- **The 06:30 deadline.** Missed by 1 minute 55 seconds (§2). This record
  proves the proposal was posted by a `schedule` run and not that it arrives
  before 06:30 PT. GitHub's scheduler delay (6 min 36 s this morning) is not
  under our control and not observable before it happens. One morning cannot
  show how often it pushes the post past 06:30.
- **Which cron fired.** Inferred from the start time and the gate's printed
  hour, not read from a field (§1).
- **"No card older than 14 days was left out for age."** Only the proposal's
  own sentence says so. Every card in the batch was older than 14 days, but 250
  others were "not now", and this record did not check each one's reason.
- **"Every excluded card still in Intake."** Does not hold (§5). The drain's
  side of it — it moved no excluded card — does hold.
- **The `go=false` value itself.** The gate writes `go` to `$GITHUB_OUTPUT`,
  which the log does not print. The evidence is the printed `why` line and the
  `schedule` job being skipped in run 36248347405.
- **Who pressed the console buttons.** The receipts prove the CEO's console
  login signed each decision. They do not prove who was at the keyboard.
- **The later lanes.** The planner, the promoter and the hand cancels moved
  cards after the drain. Their correctness is not part of this proof.

## The card's criteria

- [ ] **The record is merged on `main` with all six sections.** Six sections
      written here. Held once this pull request merges.
- [ ] **Posted before 06:30 PT by a run started by `schedule`, with nobody
      dispatching it.** `schedule`, no dispatch: held. Before 06:30 PT: missed,
      06:31:55 PT.
- [ ] **At least one card to `Planning`, one to `Canceled` with its reason, and
      every excluded card still in Intake.** Planning: held (DRE-2459, DRE-2463).
      Canceled with reason: held (DRE-3526). Excluded still in Intake: not held —
      all ten left Intake through the console's "Don't do" move, not the drain.
- [ ] **The CEO closes the card after reading the record.** His call.
