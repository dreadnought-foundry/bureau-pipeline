# A re-plan's round 2 runs on its own, and the sweep names one that never did — recorded and observed, 2026-09-22 to 2026-09-24

The proof for [DRE-4495](https://linear.app/dreadnoughtfoundry/issue/DRE-4495),
the proof card of epic [DRE-4112](https://linear.app/dreadnoughtfoundry/issue/DRE-4112).
Three observations: one **recorded** from a run that already happened, one
**live**, and one **recorded** reading of the release channel. Every time in a
sentence or a table is Pacific (PDT, UTC−7) and comes from the event's own
timestamp. Times inside quoted log lines are left as the log printed them (UTC).

**Read-only.** Nothing here moved a card, posted a comment, pressed a control
or dispatched a run. Every Linear read used `scripts/rereview_watch.py check`,
which writes nothing (`_cmd_check`, lines 386–396), or a plain GraphQL query.
The detector was read at `stable` =
`4f7c2c932ba37813c16c7ccead51a6544d77ddcd`, the same sha as `main` when this
was written (2026-09-24 13:00 PT).

**Short version.** Observation 1 holds: round 2 of a same-cards re-plan ran
with nobody posting the act. Observation 3 holds: `stable` carries the race fix
and the detector, and all four product repos' plan stubs ride `@stable`. The
sweep half of Observation 2 holds: the live sweep names epics whose promised
re-review never ran. **The replay half of Observation 2 does not hold as the
card wrote it.** All three `check` replays print `quiet`, not the `overdue` the
card expects for two of them. §2 explains why. The main cause is a defect in
the detector's `--now` option. A second cause is an error in the card itself,
and a third is which Linear key runs the command.

---

## 1. A same-cards re-plan's round 2 ran with nobody posting the act — RECORDED

This happened on DRE-4112's own thread on 2026-09-22, on bureau-pipeline at
`@main`. PR #476 had merged the evening before. Nothing here was re-run; it is
read back off the two Actions runs and the epic's thread.

| when (PT) | what | where |
| -- | -- | -- |
| 2026-09-21 20:52:09 | PR #476 (DRE-4573) merged, merge commit `5f9bace188a1a70a56887731e69eb0f76a578de0` | GitHub |
| 2026-09-22 08:29:33 | parent run [35747727884](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/35747727884) created (`Agent Plan`, `repository_dispatch`) | Actions |
| 2026-09-22 08:34:15 | `plan-critic: stage=post round=2 result=SEND_BACK collisions=3 open=0 …` | epic thread |
| 2026-09-22 08:38:26 | parent asks for the review: `asked dreadnought-foundry/bureau-pipeline for DRE-4112's post-approval review (re-review)` | parent log |
| 2026-09-22 08:38:27 | the 🔁 receipt, "The post-approval review found a gap and the plan has been revised to answer it — the same cards you approved, none added and none removed. …" | epic thread (Linear `createdAt` 15:38:27.641Z) |
| 2026-09-22 08:38:28 | continuation run [35748774241](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/35748774241) created | Actions |
| 2026-09-22 08:38:45 | parent run completes, conclusion `success` | Actions |
| 2026-09-22 08:38:59 | continuation's Duplicate-dispatch guard environment: `SENT_BY_RUN: 35747727884` | continuation log |
| 2026-09-22 08:39:02 | guard decides `skip=false` | continuation log |
| 2026-09-22 08:44:38 | `plan-critic: stage=post round=3 result=SEND_BACK collisions=4 open=1 …` — round 3 ran | epic thread |
| 2026-09-22 08:49:52 | continuation run completes, conclusion `success` | Actions |

The guard lines, verbatim from run 35748774241:

```
2026-09-22T15:38:59.6370550Z   SENT_BY_RUN: 35747727884
2026-09-22T15:39:02.4520221Z skip=false
2026-09-22T15:39:02.4522275Z linear-budget: 2300 → 2300 (spent 0 this run; window resets 09:39 PT; budget: fleet)
2026-09-22T15:39:02.4524181Z reason=no planner run was in flight for DRE-4112 and it is still in 'In Progress' — proceeding
```

**Nobody posted the act.** Between the 🔁 receipt (08:38:27) and the round-3
record (08:44:38) the thread holds exactly two comments, and the fleet user
wrote both of them: `🧮 review-turns: spent=22 …` at 08:44:36 and the 🛑
round-3 human note at 08:44:38. No comment whose whole body is
`▶️ re-run the review` sits between them. The whole thread was read with
`linear_ops.comment_records(…, whole_thread=True)` under the fleet key.

**The exemption did the work. It was not a lucky gap.** The guard's reason
line cannot show this by itself. `dedupe_dispatch.plan_decide` prints the same
"no planner run was in flight" sentence whether the sender was filtered out or
was never there (lines 348–368). The timestamps settle it.
`in_flight_when_dispatched` (lines 194–224) counts any run on the card that
was created no later than this dispatch and had not finished when it arrived.
The parent was created at 08:29:33 and finished at 08:38:45. That is 17
seconds *after* the continuation was created at 08:38:28, so the parent was in
flight at dispatch. Before PR #476 that alone meant `skip=true`, the drop that
stalled DRE-4025, DRE-3964 and DRE-4083. What changed the answer is lines
348–350: the run named in `sent_by_run` is removed from the in-flight list
before the decision.

**The mechanism** is DRE-4573 / PR #476. `review_rerun.py dispatch` puts the
parent's run id on the payload (`sent_by_run=os.environ.get("GITHUB_RUN_ID")`,
`scripts/review_rerun.py` line 406). `plan.yml` passes it to the guard as
`SENT_BY_RUN: ${{ github.event.client_payload.sent_by_run }}` (line 288). The
guard drops that one run. This epic's cancelled cards (DRE-4490, DRE-4491,
DRE-4493) contributed nothing to this.

The same thread shows the same thing again that afternoon, after the re-plan.
The 🔁 receipt came at 14:56:48 PT, round 2 at 15:05:23, the next 🔁 at
15:12:52, and round 3 (`result=PASS`) at 15:20:44. There was no act in either
window.

---

## 2. The detector reads real silence as silence — LIVE, and only half met

DRE-4492 merged as PR #484 at 2026-09-22 16:26:13 PT (merge commit
`22ab96f88fd2b34f6a65043fde3413f6b0f120d3`). Nothing else has changed the
detector since. `git log` of `scripts/rereview_watch.py` at `stable` shows only
that PR's own three commits: `157d1610`, `e055aeb4`, `58259c97`.

### 2a. The live sweep names the silence — holds

bureau-pipeline's own Reconcile sweep runs the `report_rereview_missing` phase
and names the epics whose promised re-review never ran. Two passes on
2026-09-24, one on each side of today's channel move:

Run [36014543246](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36014543246),
`schedule`, head `2dba51c5`, lines at 07:42:39 PT:

```
2026-09-24T14:42:39.6999653Z rereview-missing: DRE-3622 round 1 sent back 5650 min ago — no round 2 and no tombstone
2026-09-24T14:42:39.7000529Z rereview-missing: DRE-3694 round 1 sent back 10213 min ago — no round 2 and no tombstone
2026-09-24T14:42:39.7001463Z rereview-missing: DRE-3711 round 1 sent back 8381 min ago — no round 2 and no tombstone
2026-09-24T14:42:39.7002330Z rereview-missing: DRE-3892 round 1 sent back 14318 min ago — no round 2 and no tombstone
2026-09-24T14:42:39.7003204Z rereview-missing: DRE-3893 round 2 sent back 5902 min ago — no round 3 and no tombstone
2026-09-24T14:42:39.7003902Z sweep-spend: report_rereview_missing 2 request(s)
```

Run [36051074511](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36051074511),
head `4f7c2c93`, lines at 12:55:31 PT. It shows the same five, plus one new
epic:

```
2026-09-24T19:55:31.6578162Z rereview-missing: DRE-4680 round 1 sent back 309 min ago — no round 2 and no tombstone
2026-09-24T19:55:31.6579557Z sweep-spend: report_rereview_missing 2 request(s)
```

These are real silences, not false alarms. On DRE-3622, round 1 was sent back
at 2026-09-20 09:32:00 PT and the 🔁 receipt followed at 09:39:27. No round 2
and no tombstone came after. The detector's CEO-facing notice
`🚨 plan-critic-rereview-missing: DRE-3622's plan was sent back …` was posted
on the epic at 2026-09-22 16:32:52 PT, six minutes after PR #484 merged. That
send-back came before PR #476 merged, so it is the old race's leftover. The
detector is now pointing at it.

### 2b. The three replays — not met as written

The card asks for three outputs: DRE-4025 at `--now 2026-09-15T18:45:00Z`
`overdue`, DRE-4025 with no `--now` `quiet`, and DRE-4425 at
`--now 2026-09-21T06:00:00Z` `overdue`. Here they are verbatim, run at
2026-09-24 12:55 PT under both keys.

Under the **operator key** (`LINEAR_API_KEY` resolves to Linear user
`bureau-tools`):

```
$ python3 scripts/rereview_watch.py check DRE-4025 --now 2026-09-15T18:45:00Z
quiet: DRE-4025 — the epic is in Done, not In Progress — nothing here is waiting on a re-review
$ python3 scripts/rereview_watch.py check DRE-4025
quiet: DRE-4025 — the epic is in Done, not In Progress — nothing here is waiting on a re-review
$ python3 scripts/rereview_watch.py check DRE-4425 --now 2026-09-21T06:00:00Z
quiet: DRE-4425 — the second critic's state on this plan is `not-run`, not `held` — no send-back is waiting on a re-review
```

Under the **fleet key** (read-only; resolves to Linear user `Agent-Bureau`,
the key the sweep runs under):

```
$ python3 scripts/rereview_watch.py check DRE-4025 --now 2026-09-15T18:45:00Z
quiet: DRE-4025 — the epic is in Done, not In Progress — nothing here is waiting on a re-review
$ python3 scripts/rereview_watch.py check DRE-4025
quiet: DRE-4025 — the epic is in Done, not In Progress — nothing here is waiting on a re-review
$ python3 scripts/rereview_watch.py check DRE-4425 --now 2026-09-21T06:00:00Z
quiet: DRE-4425 — the second critic's state on this plan is `released`, not `held` — no send-back is waiting on a re-review
```

So one of the three outputs matches the card (DRE-4025 with no `--now` prints
`quiet`), and even that one is quiet for a different reason from the one the
card gives. The card says quiet "because round 2 exists"; the detector says
quiet because the epic is in Done. Three separate things produce this.

**(i) The detector's `--now` does not replay the thread. This is a defect.**
The module promises otherwise. Its docstring (lines 40–41) says "`--now`
replays a real thread as it stood at a moment", and the flag's help text
(line 418) says "replay the thread as it stood at this ISO instant". The code
uses `--now` in exactly one place: the clock in `_minutes_since`
(`minutes = _minutes_since(created, now)`, line 218). Everything else is read
as it is today:

```python
# scripts/rereview_watch.py, _cmd_check, lines 390–391
    reading = read(linear_ops.comment_records(args.epic), args.epic,
                   _lane(args.epic), args.now)
```

* `_lane` (lines 361–369) asks Linear for the card's lane **now**. DRE-4025
  has been in Done since 2026-09-21 11:50 PT. `read` returns at its first check
  (lines 189–194), before it reads a single comment.
* `comment_records` returns the **whole** thread, including comments posted
  after `--now`. DRE-4425 got past the lane check only because it is In
  Progress today, as it was then. `plan_critic.post_release` (line 195) then
  saw round 3 `result=PASS` (2026-09-21 08:05:23 PT), which is nine hours after
  the `--now` instant, and answered `released`.

To show that only the replay plumbing is broken, a scratch harness fed the
detector's own `read()` the thread and lane **as they stood** at `--now`. The
harness is not on the tree. It keeps only comments whose `created_at` is at or
before `--now`, and it takes the lane from the issue's state history. Under the
fleet key it gives:

```
# DRE-4025 at 2026-09-15T18:45:00Z: 17 of 28 comments existed; lane then='In Progress', today='Done'
overdue: rereview-missing: DRE-4025 round 1 sent back 65 min ago — no round 2 and no tombstone — round 1 was sent back 65 min ago, there is no round 2 and no tombstone, and the critic's reason was: DRE-4027 and DRE-4028 both build an acceptance criterion ("draws no count"/"draws — instead of 0") on a claim about `TaskCard`/`ProgressTable` that is false against the real components, and neither card may edit `components/` to fix it
```

That is the `overdue`, with the critic's reason quoted, that the card
expected. The reading logic is sound. The CLI does not hand it the past.

**(ii) The card's DRE-4425 instant falls inside the grace window. This is an
error in the card.** The card calls `2026-09-21T06:00:00Z` (2026-09-20
23:00 PT) "mid-way through the 8h43m gap". The thread says otherwise:

| when (PT) | what |
| -- | -- |
| 2026-09-20 22:33:39 | `plan-critic: stage=post round=1 result=SEND_BACK collisions=1 …` |
| 2026-09-20 22:41:40 | 🔁 receipt |
| 2026-09-20 22:42:17 | `🤖 Duplicate dispatch skipped: a planner run for DRE-4425 was already in flight … (run 35564666758)`. This is the race PR #476 later closed. |
| 2026-09-21 07:24:45 | `▶️ re-run the review`, posted by a person |
| 2026-09-21 07:31:16 | `plan-critic: stage=post round=2 result=SEND_BACK …` |

The gap is 22:41:40 → 07:24:45, which is 8h43m. 23:00 PT is only 26 minutes
after the send-back, and `REREVIEW_GRACE_MINUTES` is 45 (line 89). So at that
instant the correct answer is `quiet`, even with a perfect replay. The scratch
harness confirms it:

```
quiet: DRE-4425 — round 1 was sent back 26 min ago and the review it promised has 45 minutes to run
```

At an instant that really is mid-gap, `2026-09-21T10:00:00Z` (2026-09-21
03:00 PT), the same harness prints:

```
overdue: rereview-missing: DRE-4425 round 1 sent back 266 min ago — no round 2 and no tombstone — round 1 was sent back 266 min ago, there is no round 2 and no tombstone, and the critic's reason was: DRE-4437 and DRE-4114 (DRE-4062, In Progress) both edit console/web/src/components/oneriver/CapacityStrip.tsx with no ordering between them
```

The real CLI at that same instant still says
`quiet: DRE-4425 — the second critic's state on this plan is `released`, …`,
for reason (i).

**(iii) The key decides whose markers count. This is designed behaviour, but
the card did not account for it.** `linear_ops.comment_records` marks a
comment `authored_by_pipeline` only when its author is the user the running
key belongs to (lines 2710–2713 and 2745–2748). On the operator's machine that
user is `bureau-tools`. Every fleet-written `plan-critic:` record then reads as
someone else's, and `post_release` finds no trusted round at all, so it
reports `not-run`. That explains the `not-run` in the operator-key output
above. It is the safe direction, and it is documented, but it means a replay
only means something under the fleet key. The card did not say which key to
use.

**Verdict on the discrepancy.** The detector is defective. Its `--now` option
promises a replay and delivers only a moved clock. That defect alone accounts
for both unexpected `quiet`s under the fleet key. Separately, the card's
DRE-4425 instant is wrong, and it would print `quiet` even with the defect
fixed. The key explains the difference between the `not-run` and `released`
readings, not the `quiet` itself. None of this touches the live sweep. The
sweep asks about the present, where today's lane and today's thread are the
right inputs, and §2a shows it naming real silences.

---

## 3. The fleet is riding the race fix — RECORDED

**`stable` on the day.** Read 2026-09-24 13:00 PT:

```
$ gh api repos/dreadnought-foundry/bureau-pipeline/git/ref/tags/stable --jq .object.sha
4f7c2c932ba37813c16c7ccead51a6544d77ddcd
$ gh api repos/dreadnought-foundry/bureau-pipeline/compare/5f9bace188a1a70a56887731e69eb0f76a578de0...4f7c2c932ba37813c16c7ccead51a6544d77ddcd --jq .status,.behind_by
ahead
0
$ gh api repos/dreadnought-foundry/bureau-pipeline/compare/22ab96f88fd2b34f6a65043fde3413f6b0f120d3...4f7c2c932ba37813c16c7ccead51a6544d77ddcd --jq .status,.behind_by
ahead
0
```

`behind_by 0` means each merge commit is an ancestor of `stable`: `stable`
carries PR #476's merge commit `5f9bace1…` (and is 71 commits ahead of it)
and PR #484's `22ab96f8…` (33 commits ahead). `stable` was `2dba51c5` earlier
that morning.
The Reconcile run in §2a ran on that sha. Promote Channel run
[36024056698](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36024056698)
moved `stable` at 08:58 PT with no operator:

```
2026-09-24T15:58:36.9865815Z harness-passed-promoting: promoting stable to 4f7c2c932ba37813c16c7ccead51a6544d77ddcd — harness green, strictly ahead.
```

**The channel receipt,** printed by that same promote run. The newest channel
receipt on the day came from promote-channel, not release-train:

```
2026-09-24T15:58:39.0428323Z   CHANNEL_RECEIPT: pipeline-channel: state=current behind=0 since=none tag=4f7c2c9 head=4f7c2c9 gate=none scenarios=none
```

**Has `stable` advanced past DRE-4492's merge?** Yes. PR #484 merged on
2026-09-22 16:26 PT, and both of the day's `stable` shas (`2dba51c5` and
`4f7c2c93`) carry it. No hold and no red harness run stopped it.

**Portico's plan stub**, `.github/workflows/plan.yml` on `main` (head
`986d0a96`):

```yaml
jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@stable
    with:
      pipeline_ref: stable
    secrets: inherit
```

Both halves pin `stable`.

**The four product repos in `config/repo-map.json`.** Each repo's plan stub on
its default branch (`main`):

* **atlas** (`EveryBite/atlas`): `plan.yml@stable`, `pipeline_ref: stable`. Carries the race fix.
* **deltasolv** (`DeltaSolv/deltasolv`): `plan.yml@stable`, `pipeline_ref: stable`. Carries the race fix.
* **agent-bureau-demo** (`dreadnought-foundry/agent-bureau-demo`): `plan.yml@stable`, `pipeline_ref: stable`. Carries the race fix.
* **portico** (`dreadnought-foundry/portico`): `plan.yml@stable`, `pipeline_ref: stable`. Carries the race fix.

**None of the four pins a ref without the race fix.** Outside the four,
`agent-bureau`'s stub also pins `@stable` / `pipeline_ref: stable`, and
`bureau-pipeline` rides `@main`, which carries both merges.

---

## The epic's four acceptance criteria, one by one

DRE-4112's four criteria, as written on the epic:

1. **"A re-plan that changes no cards results in a round-2 review that
   actually runs."** **Holds, live.** DRE-4573 / PR #476 made it hold. §1 shows
   it on DRE-4112's own thread: the parent was still in flight when the
   continuation arrived, the guard dropped it by `sent_by_run`, and round 3 ran
   six minutes later with no act.
2. **"The 🔁 comment is not posted unless the re-review is genuinely
   scheduled."** **Holds for the race this epic was about**, again by PR #476.
   The receipt is posted after `review_rerun.py dispatch` returns 0 (parent log,
   08:38:26 then 08:38:27), and the dispatched run is no longer thrown away as
   its sender's duplicate, so the receipt is now true in that case. The receipt
   is still posted before the dispatched run's guard decides. If some other
   cause discarded the run, the receipt would be posted anyway. That leftover
   case is covered by criterion 4's detector, not prevented.
3. **"A regression test drives the race … and fails on today's code."**
   **Holds.** PR #476's `tests/test_plan_gate_sender_is_not_a_duplicate.py`
   made it hold, and the file is on `stable` (`4f7c2c93`).
4. **"Something notices an epic carrying a `SEND_BACK` record with a 🔁
   receipt and no round-2 verdict after N minutes, and says so."** **Holds for
   the live sweep; the replay tool is defective.** DRE-4492 / PR #484 made the
   sweep half hold. §2a shows it naming six silent epics on 2026-09-24 and
   posting the notice on DRE-3622. The `check --now` replay does not replay
   (§2b, reason i), so the card's replay readings could not be reproduced as
   written.

**Which repos were riding the race fix on 2026-09-24:** atlas, deltasolv,
agent-bureau-demo and portico (all `@stable` = `4f7c2c93`), plus
agent-bureau (`@stable`) and bureau-pipeline itself (`@main`). That is every
repo in `config/repo-map.json`.

---

## What is owed

1. **A card for the detector's `--now` option** (bureau-pipeline).
   `rereview_watch.py check --now` must read the thread as it stood (only
   comments with `created_at` ≤ `--now`) and the lane as it stood (from the
   issue's state history), or its docstring and help text must stop promising
   a replay. Until then, a `--now` replay of any epic that has since moved on
   or gained later rounds prints `quiet`.
2. **The card's DRE-4425 instant.** `2026-09-21T06:00:00Z` is 26 minutes after
   the send-back, inside the 45-minute grace window. A mid-gap instant is
   around `2026-09-21T10:00:00Z` (03:00 PT). The criterion should name that
   instant, and it should say the replay runs under the fleet key.
3. **Observation 2's replay criterion is not met.** Whether to close this card
   on the sweep half plus this account, or to hold it for item 1, is the CEO's
   decision.

Written 2026-09-24 13:00 PT, from live traffic between 2026-09-15 10:39 PT and
2026-09-24 12:55 PT.
