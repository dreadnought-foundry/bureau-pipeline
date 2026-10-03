# The machinery supplies its own edges — observed, 2026-09-08 to 2026-09-10

The proof for [DRE-3293](https://linear.app/dreadnoughtfoundry/issue/DRE-3293).
Three mechanisms from epic [DRE-3284](https://linear.app/dreadnoughtfoundry/issue/DRE-3284),
each watched on the live board rather than in a test: a post-approval review
that died and re-ran itself, a re-plan that re-reviewed itself, and the
`▶️ re-run the review` act. Every time below is Pacific (PDT, UTC−7) and comes
from the event's own timestamp.

**Read-only.** Nothing here moved a card, pressed a control or dispatched a
run. Every observation is of traffic the fleet produced on its own, read back
out of Linear, GitHub Actions and the relay's CloudWatch log. Two steps this
card asks for could not be observed without a write; they are named as owed at
the end rather than staged.

Two of the three behaved exactly as the epic promised. **The third did not:**
the self-dispatched re-review on DRE-3282 was thrown away by the planner's own
duplicate-dispatch guard, and round 2 never ran. That is written up in full in
§2, and it is the reason this document is not a clean sheet.

---

## 1. A dead review re-ran itself — epic DRE-3420

The post-approval review of [DRE-3420](https://linear.app/dreadnoughtfoundry/issue/DRE-3420)
ran out of turns on the evening of 2026-09-08 and started itself again with a
bigger ceiling, with no lane move and nothing asked of the CEO.

| when (PT) | what | where |
| -- | -- | -- |
| 2026-09-08 17:27:44 | Green Light → In Progress, by Frederick Conklin — the last lane move before the walk | epic history |
| 2026-09-08 17:34:58 | the death note and the tombstone, in two comments | epic thread |
| 2026-09-08 17:35:00 | the retry notice, written from the dispatch's exit status | epic thread |
| 2026-09-08 17:35:01 | retry run [34295626863](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34295626863) created — `Agent Plan`, `repository_dispatch`, conclusion `success` | Actions |
| 2026-09-08 17:42:54 | the second critic passed the plan | epic thread |
| 2026-09-08 17:42:56 | `▶️ Epic activated (5 children)` | epic thread |

The dead run was
[34295113952](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34295113952).
The tombstone, alone in its own comment as the grammar requires:

```
🪦 plan-critic-died: stage=post run=34295113952 attempt=1 step=posta subtype=error_max_turns turns=41 ceiling=40
```

The human half beside it, at 17:34:58 PT:

> 🪦 **The post-approval review of DRE-3420 did not finish** — it ran out of
> turns — 41 turns of its 40-turn ceiling in run 34295113952 (attempt 1), step
> `posta`. […] **Nothing here is yours to decide.** The review is started again
> by the pipeline, on its own, once — a run that ran out of turns gets a higher
> ceiling, because the same run at the same ceiling hits the same wall.

Two seconds later, at 17:35:00 PT:

> 🔁 The review is being run again by the pipeline, with a higher turn ceiling
> — nothing here is yours to decide, and the epic stays where it is. If it runs
> out of turns a second time the epic parks with needs-human for an operator.

**The higher ceiling is real, and the retry run says so itself.** From run
34295626863's log, 2026-09-09T00:35:18Z:

```
post-approval review ceiling: 60 turns — retry after a death at 40: 60
```

That is `review_rerun.retry_ceiling(40)` — `ceil(40 × 1.5)`, under the 180 cap —
arriving on the live rail, not in a test.

**No lane write between them.** The epic's state history carries four moves,
the newest of them 2026-09-08 17:27:44 PT, seven minutes before the death. The
epic sat in `In Progress` through the death, the retry and the pass. Nobody was
asked for anything.

**The retry passed.** At 17:42:54 PT: `✅ Second critic — after the CEO
approves it — round 3 of 2: the critic passed this plan`, with the marker
`plan-critic: stage=post round=3 result=PASS collisions=0` and the epic
activating its five children two seconds later. There was no second death, so
the `needs-human` park was not exercised on this epic and is not proven here.

### Two things worth writing down against this walk

**The medic stamped the same run a Claude limit death, 17 seconds after the
retry had already been dispatched.** At 17:35:17 PT, on the same epic:

```
🪦 limit-death: kind=claude stage=plan reset=unknown run=34295113952
```

The run had exhausted its *turns*, not the account's usage window — the
tombstone written 19 seconds earlier says `subtype=error_max_turns turns=41
ceiling=40`. At 17:35:27 PT the medic then declined to retry (`🩺
medic-retry-declined … Rule applied: card-parked`). Neither comment stopped the
review's own retry, which was already running, but a false `limit-death` marker
now stands on the epic, and the limit-recovery sweep reads those markers. This
is the third of the three misreads that epic
[DRE-3282](https://linear.app/dreadnoughtfoundry/issue/DRE-3282) exists to fix,
recurring on 2026-09-08 after the incident that produced that epic on
2026-09-07. It is that epic's card DRE-3499, not a new finding, and nothing is
filed here.

**"Round 3 of 2" reads oddly and is arithmetically right.** Two real
send-backs had already been spent on this planning attempt; the death spent
nothing of the bound, as the death note says. The retry is therefore round 3 of
a two-round bound. The number is honest; the sentence is not one a
non-technical reader parses on first pass.

---

## 2. A re-plan that re-reviewed itself — epic DRE-3282, today

This is the half of the epic that **did not hold**.

The post-approval review of [DRE-3282](https://linear.app/dreadnoughtfoundry/issue/DRE-3282)
sent the plan back, the re-plan revised it in place without adding or removing
a card, and the workflow correctly chose the "same cards" branch: no Green
Light move, and the run asked for the review itself. Then the review it asked
for was thrown away.

| when (PT) | what | where |
| -- | -- | -- |
| 2026-09-10 06:37:00 | run [34483751603](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34483751603) created — the activate route, started by the re-run act in §3 | Actions |
| 2026-09-10 06:40:32 | second critic, round 1 of 2: **sent back** | epic thread |
| 2026-09-10 06:46:50 | the re-plan's mechanical checks, 5 cards — the same five | epic thread |
| 2026-09-10 06:46:52 | the "same cards, re-reviewing on its own" notice | epic thread |
| 2026-09-10 06:46:53 | run [34484792305](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34484792305) created — the `reason: re-review` dispatch | Actions |
| 2026-09-10 06:46:56 | run 34483751603 completes, `success` | Actions |
| 2026-09-10 06:47:09 | run 34484792305 posts **duplicate dispatch skipped** and plans nothing | epic thread |

The send-back, at 06:40:32 PT:

```
plan-critic: stage=post round=1 result=SEND_BACK collisions=2
```

> 🛑 **Second critic — after the CEO approves it** — round 1 of 2: sent back —
> round 1 of 2. Reason: DRE-3498 is written against constants that no longer
> exist on `main` […]

The re-plan answered it in place, and at 06:46:52 PT the workflow took the
`changed=false` branch — the one this epic added:

> 🔁 The post-approval review found a gap and the plan has been revised to
> answer it — the same cards you approved, none added and none removed. […] The
> review is being run again by the pipeline, on its own, as round 2 of 2 —
> nothing here is yours to decide. If it sends the plan back a second time the
> epic parks in Green Light with needs-human for you.

**The lane never moved, and that half is proven.** DRE-3282's newest state
change is 2026-09-09 13:48:31 PT, Green Light → In Progress. Nothing in this
morning's walk touched it. The CEO was asked for nothing.

### The defect: the re-review deduped itself away

Seventeen seconds after the dispatch, run 34484792305 posted this and stopped:

> 🤖 Duplicate dispatch skipped: a planner run for DRE-3282 was already in
> flight when this dispatch arrived (run 34483751603) — duplicate dispatch.
> This run planned nothing and moved no lane. Run:
> https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34484792305

Run 34483751603 is **the run that sent the dispatch**. It was still alive for
the three seconds between dispatching its own re-review and exiting, and the
plan-gate guard (`scripts/dedupe_dispatch.py`, condition (a) — the card's
newest `🧠 model-attempt` heartbeat maps it to a live Actions run that is not
this run) cannot tell a dispatching parent from a webhook double-fire. So the
guard did exactly what it is written to do, and round 2 of the review never
ran.

**What the epic promised and what happened:**

| DRE-3284 promised | 2026-09-10 |
| -- | -- |
| a `re-review` dispatch | happened, 06:46:53 PT |
| no Green Light move | held — the lane never moved |
| round 2's record in the thread | **not on its own** — it arrived only after a person re-kicked the review by hand, twelve minutes later |

This is not a wording problem. Every self-dispatched re-review on this rail is
issued from inside a run that is still alive, so the race is available every
time; whether it fires depends on how many seconds the parent has left after
the dispatch. Why the §1 retry was not deduped the same way is not established
here — that dispatch also went out from inside a live run, and no log line read
for this record says which of the guard's two conditions let it through.

**It was recovered by hand, not by the machinery.** At 06:47:59 PT the operator
posted the act again (§4, second act), and at 06:48:07 PT run
[34484921221](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34484921221)
started. The operator's own note on the epic at 06:48:07 PT names the cause and
points here:

> Operator note, 06:48 PT: […] the re-plan's own round-2 dispatch was then
> skipped as a duplicate at 06:47 PT because the parent run 34483751603 was
> still alive — the DRE-3284 defect, recorded there.

**What round 2 then said, once it ran.** At 06:52:19 PT run 34484921221 posted
the round-2 record the rail owed twelve minutes earlier:

```
plan-critic: stage=post round=2 result=SEND_BACK collisions=0 — DRE-3504 (DEMO) is not blocked by DRE-3503 (PROOF) on the board …
```

> 🛑 **Second critic — after the CEO approves it** — round 2 of 2: two failed
> rounds at this critic — the bound. This plan has been sent back twice since it
> was approved, so it parks for you with `needs-human` instead of being built as
> it stands.

So this walk ends at the bound, not at a pass: two real send-backs, and the
epic parks for the CEO — the behaviour DRE-3088 kept on purpose. The park's own
lane write had not landed when this record was written at 06:53 PT (the run was
still in its re-plan step and the epic was still `In Progress`), so the park
comment itself is not quoted here.

One counter oddity in the same comment, recorded and not chased: its footer
reads `send-back rate at this critic so far on this planning attempt: 1/1
rounds` on what the same comment calls round 2 of 2. The round number and the
rate are counted from different populations.

**No card is filed for it here.** DRE-3293 records; it does not fix. The fix is
a card of its own and is named in the hand-back at the end of this document.

---

## 3. A re-plan that added cards parked in Green Light and named them — epic DRE-3337

The other half of the send-back rule, and it held exactly as written.
[DRE-3337](https://linear.app/dreadnoughtfoundry/issue/DRE-3337)'s re-plan on
2026-09-09 did add cards, so the epic went back to the CEO rather than
re-reviewing itself.

| when (PT) | what |
| -- | -- |
| 2026-09-09 12:45:00 | In Progress → Green Light, by Agent-Bureau |
| 2026-09-09 12:45:01 | the notice naming the three added cards |
| 2026-09-09 13:00:00 | Green Light → In Progress, by Frederick Conklin — the CEO approved |

The notice, at 12:45:01 PT, tail quoted:

> 🛑 The post-approval review found a gap, so nothing has started building. The
> critic's finding: DRE-3406, DRE-3407: no card in the plan actually applies the
> CEO's "Bureau only, until it is solid" decision through the mechanism this
> epic builds […] **What is yours to decide: the re-plan added
> DRE-3497,DRE-3500,DRE-3502 and removed no cards — cards you have not read.**
> Approve it from here (the console's Approve, or a move to In Progress) and the
> review runs once more; a plan sent back twice parks for you rather than being
> built as it stands.

Three cards added, all three named in the sentence the CEO reads, the epic
parked in the lane he already opens, and the ask stated once. Fifteen minutes
later he approved it. This is the one case in the epic where the machinery is
supposed to ask, and it asked correctly.

---

## 4. The `▶️ re-run the review` act — relay to run

The relay carrying the act has been live since **2026-09-08 16:10:15 PT**:

```
$ AWS_PROFILE=dreadnought aws lambda get-function-configuration \
    --function-name bureau-linear-relay --region us-west-2
CodeSha256   8VHJgWkIo/dnNhikkVRHPi5SCAPqV8SgSdWPXP++TOo=
LastModified 2026-09-08T23:10:15.000+0000        (2026-09-08 16:10:15 PT)
Runtime      python3.14
```

The act fired twice this morning on DRE-3282, an In Progress epic with
children, and produced exactly one plan run each time.

| act posted (PT) | author | relay log | run created (PT) |
| -- | -- | -- | -- |
| 2026-09-10 06:36:52.788 | Frederick Conklin | 06:36:58.666 PT | [34483751603](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34483751603) at 06:37:00 |
| 2026-09-10 06:47:59.160 | Frederick Conklin | — | [34484921221](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34484921221) at 06:48:07 |

Both comments had the act as their **entire** body, which is what
`review_rerun.is_rerun_act` requires. The relay's line for the first, read out
of CloudWatch:

```
$ AWS_PROFILE=dreadnought aws logs filter-log-events \
    --log-group-name /aws/lambda/bureau-linear-relay --region us-west-2 \
    --start-time 1789045200000 --filter-pattern '"re-run"'
2026-09-10 06:36:58 PT  {"dispatched": "agent-plan", "card": "DRE-3282", "reason": "re-run"}
```

**One act, one run, no lane move.** DRE-3282's state history is unchanged
across both acts: its newest move remains 2026-09-09 13:48:31 PT. Linear
delivers `Comment` events to the relay — the 06:36:58 PT log line is the
delivery, six seconds after the comment — which is the question
[DRE-3290](https://linear.app/dreadnoughtfoundry/issue/DRE-3290) was opened to
settle, and the operator closed that card at about 06:55 PT the same morning.

### Not observed: the console's Approve on an In Progress epic

The card asks for the same act from the console's Approve control, and for the
control to read `Review re-run requested` afterwards with no lane move. **This
did not happen and is not proven here.** Pressing Approve is a write, and this
record was made read-only; both of this morning's acts were comments posted by
hand.

What the code says the press will do, so the operator has something to check it
against — `console/backend/schema.py` in agent-bureau, the `approve_plan`
resolver:

```python
if bool(epic.get("is_epic")) and str(epic.get("state") or "") == "In Progress":
    post_receipt(epic_id, RERUN_REVIEW_ACT, …)
    return ApprovePlanResult(
        ok=True, promoted=[],
        message="Review re-run requested. The epic did not move — the "
                "post-approval review is running again.",
        outcome=APPROVE_OUTCOME_RERUN)
```

`RERUN_REVIEW_ACT` there is the same string the pipeline pins
(`console/backend/schema.py`, and `scripts/review_rerun.py`'s
`RERUN_REVIEW_ACT`), and the front end's `RERUN_CONFIRMATION.title` is
`Review re-run requested` (`console/web/src/lib/approvePlan.ts`). That the
deployed console carries this build was not checked from here either.

---

## 5. Did anything ask the CEO to move an epic to Green Light and back?

No — with one designed exception, and one older comment that predates the walk.

* **DRE-3282 (§2, §4).** Through round 1, the re-plan and both acts, nothing
  asked him for anything: the re-review notice says "nothing here is yours to
  decide" and the lane never moved. Round 2 then sent the plan back a second
  time, which is the bound, and the bound parks for him by design.
* **DRE-3420 (§1).** The retry notice says "nothing here is yours to decide,
  and the epic stays where it is". Earlier that evening, at 17:27:23 PT — seven
  minutes *before* the walk begins — the two-send-back bound did park the epic
  in Green Light with `needs-human` and ask him to settle it. That is the bound
  behaving as designed, not the shape this epic removed.
* **DRE-3337 (§3).** The notice did ask for an approval. That is the one case
  the epic keeps on purpose: the re-plan added three cards he had not read, and
  the notice names them.

The shape DRE-3164 collected five approvals of — being asked to re-approve a
plan whose cards had not changed — did not occur once in these three walks.

---

## What is owed

Two observations this card asks for could not be made read-only, and one
finding needs a card:

1. **The console's Approve on an In Progress epic** (§4). Owed to the operator:
   press Approve on DRE-3282 in the live console, record that the control reads
   `Review re-run requested`, that the epic's lane does not move, and that
   exactly one `agent-plan` run follows.
2. **A second death on one plan, parking with `needs-human`** (§1). Not
   exercised: DRE-3420's retry passed. It is the `MAX_DEATHS = 2` branch of
   `review_rerun.after_death` and stays unobserved until a plan dies twice.
3. **The self-dispatched re-review race** (§2) needs a card in bureau-pipeline:
   a run must not be deduped against the run that dispatched it. The guard
   already exempts its own run id; the gap is the *parent* run id, which the
   dispatch could carry on the payload and the guard could ignore the same way.

Written 2026-09-10, from live traffic between 2026-09-08 17:27 PT and
2026-09-10 06:48 PT.
