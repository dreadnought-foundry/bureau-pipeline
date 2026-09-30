# Planner queue proof — DRE-5181 (epic DRE-5167)

**Status: PARTIAL. The cap held and the line worked, but the fleet's Linear key still ran dry, and 8 of the 19 cards never reached a planner.**

- **What held:** four planners at most, the rest waiting with their place written on them, and each finish starting the next within seconds.
- **The key:** it hit 0 at 07:47 PT, earlier than the 2026-09-28 baseline of 08:06 PT, and again at 08:22, 08:28, 08:45 and 13:38–13:40 PT. From 08:05 to 09:23 PT no planner finished.
- **The 8 cards:** seven were built straight from Planning with no plan, and one was canceled. A ninth card, DRE-5213, had its planner run, but its retry was then left in line for six hours (§6).
- **The cap changed mid-window.** It was 4 from the drain until DRE-5326 lowered it to 2, at 08:57 PT on `main` and about 09:06 PT on `stable`. DRE-5326 was filed in response to this morning's drain. Both caps held: at most 4 before, at most 2 after (§3).

The batch is the groom drain of proposal `3677632fc10a` at **2026-09-30 07:05 PT**, which moved **19 cards** into Planning. Every time below is Pacific Time on 2026-09-30.

**How this was recorded:** it is **reconstructed from the live records after the fact**, not watched minute by minute. The inputs were read between 13:45 and 14:25 PT by an operator session on the operator's instruction:

- every `planner-slot:` receipt on the Linear board, from 07:05:58 to 13:46:22;
- the Actions run lists of every repo in `config/repo-map.json`;
- the Reconcile runs' `linear-budget:` lines, from 06:41 to 13:40.

The spot checks against raw run logs are listed in §8.

| Criterion | Result |
|---|---|
| The record exists, from the live board and the live Actions run lists during one real batch, and names the batch, its cards, its run ids and its times | **Met.** Reconstructed from the live records, not watched (§0–§2) |
| At most 4 planners running at once, counted as cards holding an open planner-slot claim | **Met.** The maximum was 4, reached at 07:06:11, 07:19:26, 07:35:28, 07:38:40 and 07:45:41. After the cap dropped to 2 (08:57 PT on `main`, about 09:06 PT on `stable`) the maximum was 2 (§3) |
| …and every card in the batch reached a planner, none canceled, skipped or receipt-less | **Not met.** 11 of 19 cards held a planner slot. 7 were dispatched from the line as builds and built unplanned (DRE-5366). 1 (DRE-4272) was dispatched the same way and then canceled at 10:32. DRE-5213's retry waited from 07:35 until it was escalated at 13:40, and later arrivals were served ahead of it. Every card has receipts (§2, §6) |
| At least three next-in-line dispatches from a finishing run's own end, with measured gaps | **Met.** 30 such dispatches: 29 at 2.3–5.5 s and one at 24.5 s (§4) |
| The `linear-budget:` readings across the window beside the 2026-09-28 baseline | **Met as a reading. The outcome is adverse:** the key drained earlier than the baseline (§7) |
| The CEO closes this card after reading the record | Open: the CEO's step |

## 0. The batch, and the code it ran on

**The drain.** DRE-4541 carries the groom proposal. It records `🧺 groom-approved: 3677632fc10a`, a console-signed decision by Sid Conklin at 07:05 PT (`at=2026-09-30T14:05:14Z`). It then records `🧺 groom-drained: 3677632fc10a — moved: 19 · held back: 0 · added: 0 · cancelled: 1 · refused: 10 → Planning at 2026-09-30 07:05 PT`. The one Cancel was DRE-4633, which was not one of the 19. The 10 refused cards stayed in Intake under an older batch's `groom-excluded`.

**The code: the siblings were live.**

- The planner-queue siblings merged on 2026-09-30 before the batch:
  - DRE-5176: PR #586, 23:27 PT on 09-29;
  - DRE-5177: #587, 00:27;
  - DRE-5178: #588, 01:12;
  - DRE-5179: #589, 02:49;
  - DRE-5180: #590, 03:42, merge commit `2268dc5`.
- agent-bureau's plan run for DRE-3681 ([36726511876](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36726511876)) started at 07:05:58 with `pipeline_ref: stable`. It checked out `HEAD is now at 2268dc5 Merge pull request #590 … DRE-5180-planner-slot-release`. So `stable` carried the last sibling when the batch fired.
- Portico's 07:18 Reconcile shows the same `2268dc5`.
- bureau-pipeline's own plan runs ride `main` (`pipeline_ref: main`), which already held `2268dc5`.

**The cap changed during the window.** `config/planner-queue.json` at `2268dc5` reads `"max_running": 4`. DRE-5326 ("the planner cap drops to 2 and a groom drain releases no more cards than there are free planner slots") merged as PR #593, `b1b7c39`, at **08:57:53 PT**, and set `"max_running": 2`. Promote Channel run [36741785304](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36741785304) moved `stable` from `b722e9a` to `b1b7c39` at about 09:06 PT, and the next promote run reads `tag=b1b7c39`. So:

- bureau-pipeline's plan runs read a cap of 2 from 08:57;
- the `stable` repos (agent-bureau, portico) read a cap of 2 from about 09:06;
- everything before read 4.

The batch is therefore evidence for the CEO's cap of 4 from 07:05 to 08:57, and for the lowered cap of 2 after.

## 1. The 19 cards and where each ended

In the proposal's order. "Held a slot" means the card had an admitted planner-slot claim: a `claimed` receipt that was not answered at once by a `waiting` receipt from the same run.

| # | Card | Held a slot? | Where it ended (state read from Linear) |
|---|---|---|---|
| 1 | DRE-5213 | yes, 07:05:58–07:34:52. The plan critic sent the plan back at 07:18. The run's retry was refused a slot at 07:35:56 and never served (§6) | Green Light, by the six-hour waiting escalation at 13:40:48 |
| 2 | DRE-5247 | yes, 07:06:11–07:09:43 | Done |
| 3 | DRE-3681 | yes, 10:17:06–10:17:17 | Green Light. The planner found it "cannot be classified… already escalated" and parked it |
| 4 | DRE-5240 | yes, 07:12:46–07:47:54 | In Progress |
| 5 | DRE-5201 | **no**: served from the line at 08:07:01, and an engineer started straight from Planning at 08:07:36 | Done (built unplanned) |
| 6 | DRE-4152 | yes, 07:06:10–07:07:07 | Backlog (one-off route) |
| 7 | DRE-4150 | **no**: served from the line at 07:07:10, and an engineer started at 07:07:41 | Done (built unplanned) |
| 8 | DRE-3621 | yes, 07:06:08–07:14:57 | In Progress |
| 9 | DRE-3622 | yes, 08:47:06, but that planner died (`run-gone` 09:06:28) | Green Light. `planning-escalation` parked it at 11:07, 140 min after its planner started |
| 10 | DRE-4666 | yes, 10:17:50–10:18:01 | Green Light. The planner found it "cannot be classified… already escalated" and parked it |
| 11 | DRE-4723 | yes, 11:06:23–11:21:34 | Green Light |
| 12 | DRE-4140 | **no**: served at 10:32:29, and an engineer started at 10:40:19 | Done (built unplanned) |
| 13 | DRE-4914 | **no**: served at 08:29:07 and 08:46:27, and an engineer started at 08:36:12 | Done (built unplanned) |
| 14 | DRE-4676 | **no**: served at 08:29:05, 08:46:25 and 09:06:36, and an engineer started at 08:36:56 | Done (built unplanned) |
| 15 | DRE-4416 | **no**: served at 11:44:10, and an engineer started at 11:54:50 | In Review (built unplanned) |
| 16 | DRE-4324 | **no**: served at 11:21:38 and 11:31:49, and an engineer started at 11:23:01 | Done (built unplanned) |
| 17 | DRE-4267 | yes, 10:18:27–10:58:57 | Green Light |
| 18 | DRE-4271 | yes, 12:22:20–12:33:43 | Green Light |
| 19 | DRE-4272 | **no**: served by the sweep at 08:46:44. That build run died on the Linear quota at "Card → In Progress" | **Canceled at 10:32:29** (§6) |

11 of the 19 held a slot, and 10 of those planners finished. DRE-3622's planner died.

## 2. Every card's planner-slot receipts, in order

Read off Linear. `run` is the run that posted the receipt. For `dispatched`, that is the dispatcher, with the repo it dispatched into.

| # | Card | Receipts |
|---|---|---|
| 1 | DRE-5213 | 07:05:58 claimed · run 36726491495; 07:34:52 released (finished) · run 36726491495; 07:35:55 claimed · run 36726491495; 07:35:56 waiting (place 24 of 24) · run 36726491495 |
| 2 | DRE-5247 | 07:06:11 claimed · run 36726509408; 07:09:43 released (finished) · run 36726509408; 07:24:09 claimed · run 36728794111; 07:24:11 waiting (place 21 of 21) · run 36728794111; 07:58:08 dispatched · run 36733020511 (portico) |
| 3 | DRE-3681 | 07:06:16 claimed · run 36726511876; 07:06:17 waiting (place 5 of 5) · run 36726511876; 07:30:24 dispatched · run 36729467001 (agent-bureau); 07:30:52 claimed · run 36729625807; 07:30:53 waiting (place 4 of 22) · run 36729625807; 08:06:53 dispatched · run 36734117252 (agent-bureau); 08:07:36 claimed · run 36734252350; 08:07:39 waiting (place 1 of 19) · run 36734252350; 08:29:03 dispatched · run 36736973074 (agent-bureau); 08:46:24 dispatched · run 36739124731 (agent-bureau); 08:47:00 claimed · run 36739276283; 08:47:02 waiting (place 1 of 14) · run 36739276283; 09:06:34 dispatched · run 36741642482 (agent-bureau); 10:16:35 dispatched · run 36747727973 (agent-bureau); 10:17:06 claimed · run 36750229919; 10:17:17 released (finished) · run 36750229919 |
| 4 | DRE-5240 | 07:06:13 claimed · run 36726516429; 07:06:14 waiting (place 2 of 3) · run 36726516429; 07:09:45 dispatched · run 36726509408 (bureau-pipeline); 07:12:46 claimed · run 36727337728; 07:47:54 released (finished) · run 36727337728; 09:51:19 claimed · run 36747190368; 09:51:20 waiting (place 1 of 18) · run 36747190368; 09:55:23 dispatched · run 36743892101 (bureau-pipeline); 09:55:44 claimed · run 36747727973; 10:16:32 released (finished) · run 36747727973; 11:54:22 claimed · run 36761850038; 11:54:23 waiting (place 2 of 11) · run 36761850038; 12:17:12 dispatched · run 36763721371 (bureau-pipeline); 12:17:34 claimed · run 36764605686; 12:21:17 released (finished) · run 36764605686 |
| 5 | DRE-5201 | 07:06:15 claimed · run 36726521033; 07:06:16 waiting (place 4 of 4) · run 36726521033; 08:07:01 dispatched · run 36734156568 (bureau-pipeline) |
| 6 | DRE-4152 | 07:06:10 claimed · run 36726513159; 07:07:07 released (finished) · run 36726513159 |
| 7 | DRE-4150 | 07:06:12 claimed · run 36726514930; 07:06:13 waiting (place 1 of 1) · run 36726514930; 07:07:10 dispatched · run 36726513159 (bureau-pipeline); 07:52:11 dispatched · run 36730197529 (bureau-pipeline) |
| 8 | DRE-3621 | 07:06:08 claimed · run 36726516724; 07:14:57 released (finished) · run 36726516724; 07:31:26 claimed · run 36729697602; 07:31:27 waiting (place 2 of 24) · run 36729697602; 07:38:19 dispatched · run 36728195299 (bureau-pipeline); 07:38:40 claimed · run 36730625286; 07:41:38 released (finished) · run 36730625286 |
| 9 | DRE-3622 | 07:06:13 claimed · run 36726519216; 07:06:14 waiting (place 2 of 3) · run 36726519216; 08:07:00 dispatched · run 36734156568 (bureau-pipeline); 08:07:33 claimed · run 36734267229; 08:07:37 waiting (place 1 of 19) · run 36734267229; 08:46:42 dispatched · run 36739170124 (bureau-pipeline); 08:47:06 claimed · run 36739312647; 09:06:28 released (run-gone) · run 36739312647 |
| 10 | DRE-4666 | 07:06:19 claimed · run 36726520725; 07:06:21 waiting (place 6 of 6) · run 36726520725; 08:29:04 dispatched · run 36736973074 (agent-bureau); 08:46:24 dispatched · run 36739124731 (agent-bureau); 08:47:06 claimed · run 36739279285; 08:47:07 waiting (place 3 of 16) · run 36739279285; 09:06:36 dispatched · run 36741642482 (agent-bureau); 10:17:20 dispatched · run 36750229919 (agent-bureau); 10:17:50 claimed · run 36750314999; 10:18:01 released (finished) · run 36750314999 |
| 11 | DRE-4723 | 07:06:53 claimed · run 36726530204; 07:06:54 waiting (place 12 of 12) · run 36726530204; 11:05:51 dispatched · run 36755590637 (agent-bureau); 11:06:23 claimed · run 36756128546; 11:21:34 released (finished) · run 36756128546 |
| 12 | DRE-4140 | 07:06:24 claimed · run 36726525842; 07:06:25 waiting (place 9 of 10) · run 36726525842; 10:32:29 dispatched · run 36750163753 (agent-bureau); 10:59:00 dispatched · run 36750399136 (agent-bureau) |
| 13 | DRE-4914 | 07:06:24 claimed · run 36726528065; 07:06:25 waiting (place 8 of 9) · run 36726528065; 08:29:07 dispatched · run 36736973074 (agent-bureau); 08:46:27 dispatched · run 36739124731 (agent-bureau) |
| 14 | DRE-4676 | 07:06:21 claimed · run 36726528085; 07:06:22 waiting (place 7 of 7) · run 36726528085; 08:29:05 dispatched · run 36736973074 (agent-bureau); 08:46:25 dispatched · run 36739124731 (agent-bureau); 09:06:36 dispatched · run 36741642482 (agent-bureau) |
| 15 | DRE-4416 | 07:06:57 claimed · run 36726533445; 07:06:58 waiting (place 14 of 14) · run 36726533445; 11:44:10 dispatched · run 36757947375 (agent-bureau) |
| 16 | DRE-4324 | 07:06:55 claimed · run 36726535156; 07:06:56 waiting (place 13 of 13) · run 36726535156; 11:21:38 dispatched · run 36756128546 (agent-bureau); 11:31:49 dispatched · run 36759069143 (agent-bureau) |
| 17 | DRE-4267 | 07:06:23 claimed · run 36726536393; 07:06:24 waiting (place 8 of 8) · run 36726536393; 08:46:43 dispatched · run 36739170124 (bureau-pipeline); 09:06:34 dispatched · run 36741677305 (bureau-pipeline); 10:18:04 dispatched · run 36750314999 (bureau-pipeline); 10:18:27 claimed · run 36750399136; 10:58:57 released (finished) · run 36750399136 |
| 18 | DRE-4271 | 07:07:03 claimed · run 36726539025; 07:07:04 waiting (place 15 of 15) · run 36726539025; 12:21:50 dispatched · run 36761845476 (agent-bureau); 12:22:20 claimed · run 36765145444; 12:33:43 released (finished) · run 36765145444 |
| 19 | DRE-4272 | 07:06:28 claimed · run 36726540542; 07:06:29 waiting (place 11 of 11) · run 36726540542; 08:46:44 dispatched · run 36739170124 (bureau-pipeline) |

**Waiting receipts carry their place.** All 46 `waiting` receipts in the window read `waiting for a planner: place k of n`, and none lacks it. Each was posted within 3.5 s of its own run's `claimed` receipt (median 1.0 s). That is the claim-then-verify step refusing the fifth claim.

**The ledger, whole.** 226 comments on the board match `planner-slot:` between 07:05:58 and 13:46:22. One is a critic comment quoting the phrase, so there are 225 receipts: 82 `claimed`, 46 `waiting`, 60 `dispatched` and 37 `released` (32 `finished`, 5 `run-gone`). They cover 34 cards: the 19 above, plus cards that entered Planning later in the day and joined the same line. No `claimed … · from run` hand-over receipt occurred in the window.

## 3. At no moment more than four

**The ledger count.** Pairing every admitted claim with its release gives a maximum of **4** open claims. Admitted claims are the 36 that were not refused at once. A release is the run's `released` receipt, or the sweep's `run-gone` expiry. The maximum was reached at five instants:

| Time | The four cards holding a slot (run) |
|---|---|
| 07:06:11 | DRE-5213 (36726491495), DRE-3621 (36726516724), DRE-4152 (36726513159), DRE-5247 (36726509408) |
| 07:19:26 | DRE-5213 (36726491495), DRE-5240 (36727337728), DRE-5268 (36727677920), DRE-4626 (36728195299) |
| 07:35:28 | DRE-5240 (36727337728), DRE-5268 (36730197529), DRE-4626 (36728195299), DRE-5129 (36729579603) |
| 07:38:40 | DRE-5240 (36727337728), DRE-5268 (36730197529), DRE-5129 (36729579603), DRE-3621 (36730625286) |
| 07:45:41 | DRE-5240 (36727337728), DRE-5268 (36730197529), DRE-5129 (36729579603), DRE-4626 (36731503026) |

All five are before 08:57, under the cap of 4. **From 08:57 on, under the cap of 2, the open-claim count never went above 2.** It was 2 at, among other times, 09:25:09, 10:17:06, 11:21:29, 12:22:20 and 13:10:11.

Counting the refused claims too, during the one to three seconds before each one's `waiting` receipt, the count momentarily reaches 6. None of those runs ran a model step (below).

**The Actions run lists.** Across every repo in `config/repo-map.json` from 06:55 to 14:00:

- **94 `Agent Plan` runs**: 29 in agent-bureau, 45 in bureau-pipeline, 20 in portico.
- **None** in atlas, deltasolv or agent-bureau-demo. Their run lists were readable (the same query returns their other runs), and they simply had no plan runs.

One of the 94, [36726491495](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36726491495) (DRE-5213), ran twice:

- Attempt 1 (07:05:38–07:35:08) held a slot, released it `finished` at 07:34:52, and then failed.
- `github-actions[bot]` re-ran it as attempt 2 at 07:35:38. That attempt's claim was refused (`place 24 of 24`), and it ended at 07:36:01.

That is why DRE-5213 has a second `claimed`/`waiting` pair under the same run id. So there are 95 attempts. Matched to the ledger by run id and attempt:

- **36 admitted attempts** held a slot. They lasted 42 s to 41 min, median 15 min.
- **46 refused attempts** lasted 23 to 71 s. The raw logs of three (36726511876 for DRE-3681, 36726540542 for DRE-4272, 36726533445 for DRE-4416) show the claim step and no `Auto-detected mode: agent` line, so no model ran.
- **13 runs never posted a claim.** Eight fall in the ledger window, and none reached a model step:
  - five died on the Linear quota before the claim (36731836801, 36737082071, 36737081019, 36739307935, 36739316618);
  - three stopped at the dispatch gate with `skip=true` (36739294519, 36747190075, 36763873219).
  - The other five started at 13:55 or later, after the ledger extract. They are outside this record.

**Three moments when the wall clock shows five.** By attempt start and end times alone, the non-refused attempts overlap five deep three times. In each, fewer than four held a claim. The extra runs are either finishing runs that had already released their slot (the case the card exempts) or runs still waiting for a runner before their claim.

| Wall-clock moment | Five attempts running | Holding a claim at that moment | The others |
|---|---|---|---|
| 07:30:05 | 36726491495 #1, 36727337728, 36727677920, 36728195299, 36729579603 | **3**: DRE-5213, DRE-5240, DRE-4626 | 36727677920 released DRE-5268 at 07:29:59 and was finishing. 36729579603 (DRE-5129) was queued for a runner: its job began 07:35:11, it claimed at 07:35:27, and its first model step began at 07:36:06 |
| 07:34:57 | 36726491495 #1, 36727337728, 36728195299, 36729579603, 36730197529 | **2**: DRE-5240, DRE-4626 | 36726491495 #1 released DRE-5213 at 07:34:52 and was finishing. 36729579603 was still queued. 36730197529 (DRE-5268) had just started and claimed at 07:35:17 |
| 07:38:21 | 36727337728, 36728195299, 36729579603, 36730197529, 36730625286 | **3**: DRE-5240, DRE-5268, DRE-5129 | 36728195299 released DRE-4626 at 07:38:16, dispatched DRE-3621 at 07:38:19, and was finishing. 36730625286 (DRE-3621) had started but did not claim until 07:38:40 |

In each case the claim came before any model step. The run that waited longest for a runner, 36729579603, shows the order in its own log: its job began at 07:35:11, `planner_queue.py claim` ran at 07:35:27, and `Auto-detected mode: agent` appeared at 07:36:06.

## 4. The next card starts from the finishing run's own end

**30 times** a planner's end-of-run step released its slot and dispatched the next card in line itself, before any sweep. The gap from the run's `released` receipt to its `dispatched` receipt was **2.3 to 5.5 s** in 29 cases, and **24.5 s** in one (09:54:58 → 09:55:23). Examples:

| Finishing run | Released | Next card dispatched | Gap |
|---|---|---|---|
| DRE-4152, [36726513159](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36726513159) | 07:07:07 | DRE-4150 at 07:07:10 | 2.7 s |
| DRE-5247, [36726509408](https://github.com/dreadnought-foundry/portico/actions/runs/36726509408) | 07:09:43 | DRE-5240 at 07:09:45 | 2.4 s |
| DRE-3621, [36726516724](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36726516724) | 07:14:57 | DRE-5268 at 07:14:59 | 2.4 s |
| DRE-5268, [36727677920](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36727677920) | 07:29:59 | DRE-5129 at 07:30:02 | 3.1 s |
| DRE-5213, [36726491495](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36726491495) | 07:34:52 | DRE-5268 at 07:34:54 | 2.4 s |
| DRE-4626, [36728195299](https://github.com/dreadnought-foundry/portico/actions/runs/36728195299) | 07:38:16 | DRE-3621 at 07:38:19 | 2.8 s |
| DRE-5240, [36727337728](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36727337728) | 07:47:54 | DRE-5034 at 07:47:57 | 3.3 s |
| DRE-5268, [36743892101](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36743892101) | 09:54:58 | DRE-5240 at 09:55:23 | 24.5 s |
| DRE-2702, [36761871985](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36761871985) | 12:09:36 | DRE-4626 at 12:09:41 | 5.5 s |

In the first row's log, the steps run in order at the run's end:

- `planner_queue.py release "$CARD"` at 07:07:07;
- `planner_queue.py next`, which printed `card=DRE-4150` at 07:07:08;
- `planner_queue.py dispatch "$NEXT_CARD"`, which printed `dispatched DRE-4150 at dreadnought-foundry/bureau-pipeline` at 07:07:10.

The run is an `Agent Plan` run on `repository_dispatch`, not a Reconcile run.

## 5. Planners the sweep started

**A killed run, backfilled by the sweep.** At 08:46 bureau-pipeline's Reconcile [36739170124](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36739170124) found every slot free. The last two claims still open after 08:05 belonged to planners that had died on the Linear quota, and earlier sweeps had expired them:

- DRE-5129's claim, `run-gone`, by bureau-pipeline Reconcile [36734156568](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36734156568) at 08:06:59. Its log reads `planner line: released DRE-5129 run 36729579603 — because run-gone`.
- DRE-5034's claim, `run-gone`, by agent-bureau Reconcile [36736973074](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36736973074) at 08:29:02.

The 08:46 sweep served four cards into the four free slots: DRE-5268, DRE-3622, DRE-4267 and DRE-4272 (08:46:40–08:46:44). Its log reads `planner line: DRE-3622 served — dispatched at dreadnought-foundry/bureau-pipeline (trigger planning, reason none)`. DRE-3622's planner, run 36739312647, claimed its slot at 08:47:06.

The same sweep's log reports the line's depth, one line per waiting card. For example: `watchdog: DRE-3622 is waiting for a planner slot (place 1 of 19, waited 100 minutes) — not a strand`, and `watchdog: DRE-5213 is waiting for a planner slot (place 24 of 24, waited 71 minutes) — not a strand`. The 120-minute watchdog did not treat a waiting card as a dead planner (DRE-5177).

**A slot a line dispatch left empty.** At 07:19:01 portico's Reconcile [36728093139](https://github.com/dreadnought-foundry/portico/actions/runs/36728093139) logged `planner line: DRE-4626 served — dispatched at dreadnought-foundry/portico (trigger in progress, reason none)`. DRE-4626's planner, run 36728195299, claimed at 07:19:26. The slot was free because the 07:07:10 end-of-run dispatch had gone to DRE-4150, which was then built, not planned (§6), so it never took the slot. This is the sweep backstopping a different gap from a killed run.

Five claims in the window were expired as `run-gone`: DRE-5129 at 08:06:59, DRE-5034 at 08:29:02, DRE-3622 at 09:06:28, DRE-4626 (run 36747185674) at 09:51:44, and DRE-5327 at 10:16:01.

## 6. Canceled, skipped or left without a receipt

The epic forbids any answer but "none". **The answer is not "none".**

- **Receipt-less: none.** Every one of the 19 cards has at least a `claimed` and a `waiting` or `released` receipt (§2).
- **Built unplanned: seven cards.** DRE-4150, DRE-5201, DRE-4914, DRE-4676, DRE-4324, DRE-4140 and DRE-4416 were each served from the line, by an end-of-run hand-off or by the sweep. Each then moved from Planning to In Progress with an `engineer agent starting` comment within seconds to minutes. No planner, no classification and no plan critic ran on any of them. The cause is DRE-5366, filed today (Urgent, in Planning): the line's dispatch calls `plan_run.fire()`, which picks `agent-execute` whenever a card lacks `agent:planner`.
  - Each such dispatch also spent a next-in-line turn without filling the slot. That is why the 07:19 sweep found a slot free (§5).
  - DRE-4150 was served a second time at 07:52:11, while it was already being built. That run posted `Duplicate dispatch skipped` at 07:55:51 and did nothing.
- **Canceled: DRE-4272.** It was served by the 08:46 sweep and dispatched the same way, as a build. That run died on the quota at "Card → In Progress" (08:47:13). It was canceled at 10:32:29, while its parent epic DRE-4267's planner was running (claim 10:18:27–10:58:57). Its title now reads "Superseded — re-filed as DRE-5355 so the proof card is the epic's last child". So it was a deliberate re-file, not a card lost by the line. The history records no actor, so this record does not say who canceled it.
- **Left in line for six hours: DRE-5213.** Its planner ran from 07:06 to 07:34 on run 36726491495 and posted a plan at 07:16. The first critic sent the plan back (round 1 of 2) at 07:18. The run released its slot at 07:34:52, and attempt 1 then failed at 07:35:08.
  - `github-actions[bot]` re-ran it as attempt 2 at 07:35:38. That attempt claimed at 07:35:55 and was refused at 07:35:56 with `waiting for a planner: place 24 of 24`.
  - **The line never served it after that.** Cards that joined later were served first. DRE-5327, for example, joined at 10:16:22 as `place 18 of 18` and was dispatched at 11:21:02.
  - At 13:40:48 `planning-escalation` parked DRE-5213 in Green Light: "this card has been waiting in line for a planner for about 6 hours and nothing has started it" (the config's `waiting_max_minutes: 360`). That escalation is the designed backstop, and its "two planners" wording matches the cap after DRE-5326.
  - Why the line skipped the card is **not established** by this record. The one visible difference is that its waiting receipt carries the same run id as that run's earlier `released` receipt. **No card tracks this yet.**
- **Planned but not delivered: DRE-3622.** Its planner started at 08:47:06 and died on the quota. `planning-escalation` parked it in Green Light at 11:07 as "planning has produced nothing". That is correct under DRE-5177's rule, which measures from the planner's start, but the card left the batch without a plan.

## 7. The fleet's Linear key across the window

The epic's 2026-09-28 readings: after the 07:07 PT drain the key was **empty by 08:06 PT**, about 59 minutes. In the 13:26 PT batch it drained at **about 130 requests a minute**.

Today, from the `linear-budget:` lines of every Reconcile run: 107 readings, 06:41–13:40 PT, across agent-bureau, bureau-pipeline and portico. All read `budget: fleet`. The lowest reading in each half hour:

| Half hour | Reading at | Repo | Run | Remaining (before → after) | |
|---|---|---|---|---|---|
| 06:30 | 06:53 | portico | 36725001623 | 2488 → 2464 | |
| 07:00 | 07:29 | agent-bureau | 36729467001 | 1656 → 1521 | |
| 07:30 | 07:47 | agent-bureau | 36731760467 | 5 → 0 | refused after 4 calls |
| 08:00 | 08:22 | portico | 36736229552 | 3 → 0 | refused after 5 calls |
| 08:30 | 08:45 | agent-bureau | 36739124731 | 177 → 0 | refused after 53 calls |
| 09:00 | 09:05 | bureau-pipeline | 36741677305 | 273 → 200 | |
| 09:30 | 09:52 | agent-bureau | 36747368893 | 189 → 184 | |
| 10:00 | 10:04 | bureau-pipeline | 36748800448 | 122 → 106 | |
| 10:30 | 10:35 | bureau-pipeline | 36752511624 | 458 → 410 | |
| 11:00 | 11:06 | agent-bureau | 36756165958 | 482 → 429 | |
| 11:30 | 11:30 | bureau-pipeline | 36759115894 | 760 → 731 | |
| 12:00 | 12:23 | bureau-pipeline | 36765376115 | 542 → 520 | |
| 12:30 | 12:46 | bureau-pipeline | 36768011604 | 86 → 28 | |
| 13:00 | 13:26 | agent-bureau | 36772673680 | 271 → 182 | |
| 13:30 | 13:38 | agent-bureau | 36774095507 | 2 → 0 | refused after 10 calls |

**Every reading of 0:**

- 07:47: agent-bureau 36731760467 and bureau-pipeline 36731816360;
- 08:22: portico 36736229552;
- 08:28: agent-bureau 36736973074 and bureau-pipeline 36737013193;
- 08:45: agent-bureau 36739124731;
- 13:38: agent-bureau 36774095507;
- 13:40: bureau-pipeline 36774224458 and portico 36774264810.

**The key drained again, and faster than the baseline.** At 07:05 agent-bureau's sweep read 2,324 remaining (36726473728). By 07:47 the key was at 0: at least 2,324 requests in 42 minutes, against 59 minutes on 2026-09-28. That is a floor of about 55 a minute. The true rate is higher by whatever refilled in the rolling window. This is not the same method as the epic's "about 130 a minute", so the two numbers are not compared.

**The line stalled while the key was dry.** No planner finished between DRE-4626's release at 08:05:11 and DRE-5034's at 09:23:48. The two planners admitted in that stretch, DRE-5034 at 08:05:42 and DRE-3622 at 08:47:06, both died and were expired `run-gone`. Plan runs dispatched at 08:29 and 08:46 died on the quota before they could claim (§3).

**What the cap did and did not do.** It did what DRE-5167 asked: four at a time, a written place in line, and the next starting within seconds. It did **not** keep the key from draining, which the CEO's question also asked for.

- Four planners plus background traffic (sweeps across three repos, merge syncs, the medic, the relay and the console) still emptied 2,500 requests an hour on a busy morning.
- DRE-5326's own commit message measured "four planners spent ~185-260 Linear requests a minute against a ~42/minute refill". This record did not re-derive that number. That measurement is why the cap is now 2.
- **The key still reached 0 at 13:38–13:40 PT under the cap of 2.** Lowering the cap has not by itself stopped the drain.

**The card that owns the drained key** is the epic **DRE-3530**, "The fleet's Linear traffic fits one user's 2,500 an hour". It is in **Intake**. Its remaining child is **DRE-3624**, "A burst reserves its headroom before it starts, and a limit-dead run re-enters on its own", in **Backlog**, blocked by DRE-3622 and DRE-3623. DRE-3622 ("fence", the sandbox's own seat) is one of this batch's cards, and it sits in Green Light after its planner died on this morning's quota. DRE-5201 (sweep spend) is Done and did not stop the drain. On the console's share of the key, DRE-4721 (In Progress) and DRE-4666 (in this batch) apply. This record changes none of them.

## 8. Spot checks against the raw run logs

Checked between 14:15 and 14:25 PT with `gh run view <id> --log`. Each matched the receipts:

1. **The end-of-run hand-off.** [36726513159](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36726513159) (`Agent Plan`, `repository_dispatch`) ran `planner_queue.py release` at 07:07:07, `next` → `card=DRE-4150`, and `dispatch` → `dispatched DRE-4150` at 07:07:10 (§4).
2. **The sweep's line service.** Portico Reconcile [36728093139](https://github.com/dreadnought-foundry/portico/actions/runs/36728093139) logged `planner line: DRE-4626 served` at 07:19:04, checked out at `2268dc5` (§5).
3. **The sweep's backfill.** bureau-pipeline Reconcile [36739170124](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36739170124) logged DRE-5268, DRE-3622, DRE-4267 and DRE-4272 `served` at 08:46, plus the waiting-card watchdog lines (§5).
4. **The expiry.** bureau-pipeline Reconcile [36734156568](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36734156568) logged `released DRE-5129 run 36729579603 — because run-gone` (§5).
5. **The drain.** agent-bureau Reconcile [36731760467](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36731760467) logged `rate limited: 2500 requests/hour exhausted — refused after 4 calls … window resets 08:47 PT; budget: fleet` at 07:48 (§7).
6. **The stable pin.** agent-bureau plan run [36726511876](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36726511876) resolved `pipeline_ref: stable` to `2268dc5` (§0).
7. **The unplanned builds.** DRE-3681's and DRE-4666's 11-second planners (36750229919, 36750314999) logged "cannot be classified" and "already escalated". The seven built cards each carry an `engineer agent starting` comment right after their line dispatch (§1, §6).

## 9. What is left

- **The CEO reads this record and closes DRE-5181.** This record does not claim the epic's goal of "the key is no longer drained". That goal is not met, and it belongs to DRE-3530 / DRE-3624.
- **DRE-5366** has to land before a batch can meet "every card reached a planner". Seven of today's cards were built unplanned because of it.
- **DRE-5213's six-hour wait** (§6) has no card. The line skipped a waiting card whose claim came from a re-run of a run that had already released.
- **The CEO's cap is now 2, not 4** (DRE-5326). The next proof of the line reads against 2.
