# The post-approval review's turn ceiling, its receipt, and the quiet medic — observed 2026-09-17

The proof for [DRE-3503](https://linear.app/dreadnoughtfoundry/issue/DRE-3503),
the closing card of epic [DRE-3282](https://linear.app/dreadnoughtfoundry/issue/DRE-3282).

Read by hand on **2026-09-17 between 15:35 and 16:40 PT** against live Linear and
live GitHub Actions on `dreadnought-foundry/bureau-pipeline`. Every time below is
**Pacific**; where the UTC string is itself the evidence it is quoted as it stands
with the Pacific reading beside it.

**Read-only.** Nothing here moved a card, dispatched a run, re-ran a workflow or
commented on a pull request. Every observation is of traffic the pipeline produced
on its own, read back out of the epics' Linear threads and the runs' own logs.

**All four observations hold.** One of them — the medic's limit classifier —
holds and also turned up a separate defect, on a different day and a different
death shape, which is written up in §3b because a proof record that noticed
something and did not say it is worth less than one that did.

---

## The window, and why it opens where it does

The three build cards under this epic merged to `main` on **2026-09-10**:

| card | what it shipped | merged (PT) | PR |
| -- | -- | -- | -- |
| DRE-3499 | a turn-cap result is never a Claude limit death | 2026-09-10 07:36 | [#351](https://github.com/dreadnought-foundry/bureau-pipeline/pull/351) |
| DRE-3498 | ceiling `40 + 4 × children`, floor 60, and a `🧮 review-turns` receipt per round | 2026-09-10 14:13 | [#352](https://github.com/dreadnought-foundry/bureau-pipeline/pull/352) |
| DRE-3501 | a review that finishes over the ceiling is a verdict, not a death | 2026-09-10 14:52 | [#358](https://github.com/dreadnought-foundry/bureau-pipeline/pull/358) |

So the window opens at **2026-09-10 14:52 PT**. bureau-pipeline's own
`self-plan.yml` pins `pipeline_ref: main` and `uses: …/plan.yml@main`, so a
bureau-pipeline epic reviewed after that minute is reviewed by the merged code
with no channel lag to argue about.

**How the candidates were found.** All 109 `Agent Plan` runs in
bureau-pipeline created since 2026-09-10 21:52 UTC were listed, and each was
asked whether its `Second critic — turn ceiling` step actually ran (that step is
gated on `steps.route.outputs.mode == 'activate'`, which is the post-approval
route and nothing else). Ten runs did, across six epics:

```
2026-09-12 10:03:14 PT  34707066435  failure  DRE-3621   (6 children)
2026-09-12 22:26:18 PT  34740295914  success  DRE-3282   (5 children)
2026-09-13 11:33:03 PT  34774971603  success  DRE-3622  (11 children)
2026-09-13 11:33:31 PT  34774996911  success  DRE-3623  (12 children)
2026-09-13 11:42:26 PT  34775458269  success  DRE-3622  (11 children)
2026-09-13 11:42:33 PT  34775463740  success  DRE-3623  (12 children)
2026-09-13 16:25:44 PT  34789674295  success  DRE-3623  (12 children)
2026-09-13 16:51:35 PT  34790899673  success  DRE-3623  (12 children)
2026-09-14 08:59:10 PT  34865716899  success  DRE-3892  (11 children)
2026-09-17 05:19:45 PT  35220484832  success  DRE-3694   (6 children)
```

Three of the six epics have **seven or more children**: DRE-3622 (11),
DRE-3623 (12), DRE-3892 (11).

---

## 1. The first ≥ 7-card post-approval review after the window opened — MET

**The epic.** [DRE-3623](https://linear.app/dreadnoughtfoundry/issue/DRE-3623)
— twelve children, `repo:bureau-pipeline`.

**The run.** [34774996911](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34774996911),
`Agent Plan`, job `call / bureau-card: DRE-3623`, created `2026-09-13T18:33:31Z`
(**2026-09-13 11:33:31 PT**), conclusion `success`.

**Two reviews were in flight within half a minute of each other, and which one
is "first" depends on which end you read.** DRE-3622's round 1 run,
[34774971603](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34774971603),
*started* twenty-eight seconds earlier, at **11:33:03 PT**; DRE-3623's *finished*
first, posting its receipt at 11:39:26 PT against DRE-3622's at 11:40:28 PT.
DRE-3623 is written up here because it is the larger epic and the one that ran
all the way to a PASS; both are recorded, and neither was chosen after the fact
for its result.

**The ceiling the run sized itself to**, from the `Second critic — turn ceiling`
step at `2026-09-13T18:33:48Z` (11:33:48 PT), verbatim:

```
post-approval review ceiling: 88 turns — sized from 12 cards
```

`40 + 4 × 12 = 88`, above the floor of 60 and below the cap of 140.

**The review finished and left a round record, not a tombstone.** The
`plan-critic:` round record on the epic's thread at **2026-09-13 11:39:28 PT**,
verbatim (first line; the finding runs on):

```
plan-critic: stage=post round=1 result=SEND_BACK collisions=2 — DRE-3644 redefines `intake_controls.may_escalate(slug, rail)` with a conflicting contract
```

`result=SEND_BACK` is a decision. There is **no** `🪦 plan-critic-died:`
tombstone anywhere on DRE-3623's thread — the grep for it over the whole thread
returns nothing.

**And the epic reached a PASS.** Four post rounds ran on DRE-3623; the last one,
at **2026-09-13 16:58:13 PT**, reads:

```
plan-critic: stage=post round=4 result=PASS collisions=0
```

**Corroborated twice more.** The same shape on the other two ≥ 7-card epics:

| epic | children | run | round record (PT) |
| -- | -- | -- | -- |
| DRE-3622 | 11 | [34774971603](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34774971603) | `plan-critic: stage=post round=1 result=SEND_BACK collisions=0` — 2026-09-13 11:40:29 |
| DRE-3622 | 11 | [34775458269](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34775458269) | `plan-critic: stage=post round=2 result=SEND_BACK collisions=1` — 2026-09-13 11:47:48 |
| DRE-3892 | 11 | [34865716899](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34865716899) | `plan-critic: stage=post round=1 result=SEND_BACK collisions=0` — 2026-09-14 09:04:03 |

---

## 2. The `🧮 review-turns:` receipt, in production, alone in its comment — MET

Read out of each epic's live Linear thread. Each of these is the **entire body**
of its comment — one line, nothing above it and nothing below it, which is the
`plan_critic._sole_record` rule the card asks to see honoured:

```
🧮 review-turns: spent=20 ceiling=88 children=12 model=claude-sonnet-5     DRE-3623, 2026-09-13 11:39:26 PT
🧮 review-turns: spent=17 ceiling=88 children=12 model=claude-sonnet-5     DRE-3623, 2026-09-13 11:46:19 PT
🧮 review-turns: spent=26 ceiling=100 children=12 model=claude-sonnet-5    DRE-3623, 2026-09-13 16:33:13 PT
🧮 review-turns: spent=42 ceiling=100 children=12 model=claude-sonnet-5    DRE-3623, 2026-09-13 16:58:12 PT
🧮 review-turns: spent=25 ceiling=84 children=11 model=claude-sonnet-5     DRE-3622, 2026-09-13 11:40:28 PT
🧮 review-turns: spent=27 ceiling=84 children=11 model=claude-sonnet-5     DRE-3622, 2026-09-13 11:47:46 PT
🧮 review-turns: spent=26 ceiling=84 children=11 model=claude-sonnet-5     DRE-3892, 2026-09-14 09:04:01 PT
🧮 review-turns: spent=31 ceiling=64 children=6 model=claude-sonnet-5      DRE-3694, 2026-09-17 05:29:00 PT
🧮 review-turns: spent=1 ceiling=64 children=6 model=claude-sonnet-5       DRE-3621, 2026-09-12 10:03:44 PT
```

**Every ceiling matches the new formula for its child count**, floor 60, cap 140:

| children | `40 + 4 × K` | floor/cap applied | ceiling observed |
| -- | -- | -- | -- |
| 12 | 88 | 88 | **88** ✓ |
| 11 | 84 | 84 | **84** ✓ |
| 6 | 64 | 64 | **64** ✓ |

`spent` is at or below `ceiling` in every line. The largest reading is
**42 of 100** and the largest under the formula proper is **27 of 84** — a
twelve-card review has never come close to the wall since the base moved to 40.
The card's own worked example ("seven cards read `ceiling=68`") has no instance
yet, because no seven-child epic has been reviewed in the window; the two child
counts that did occur, 11 and 12, are checked against the same formula above.

**One nuance worth having, because the number is meant to be tuned from this
series.** DRE-3623's rounds 3 and 4 ran at **100, not 88** — and the ceiling step
says why, verbatim from run
[34789674295](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34789674295)
at `2026-09-13T23:26:02Z` (16:26 PT):

```
post-approval review ceiling: 100 turns — sized from an unknown number of cards
```

That is `plan.yml`'s declared fallback (`max_turns=100`, the fifteen-card
ceiling) firing because `linear_ops.py children` could not be read on those two
runs — the Linear hour was exhausted that afternoon (see §3b). The receipt still
printed `children=12`, because the receipt step counts the children again for
itself. So **seven of the nine receipts above were sized by the formula and two
by the documented fallback**, and only the seven belong in a series the constant
is tuned from.

---

## 3. The medic never stamped a false Claude limit death — MET

### 3a. The window's own red plan runs

Every `Agent Plan` run in bureau-pipeline that did not succeed between
2026-09-10 14:52 PT and now:

```
2026-09-12 10:03:14 PT  34707066435  failure   DRE-3621  — the post-approval review died (§3b)
2026-09-12 15:16:10 PT  34722278266  failure
2026-09-13 15:10:01 PT  34785966041  failure   DRE-3837  — died at `Card-validation gate`
2026-09-13 16:25:35 PT  34789667828  failure   DRE-3622  — died at `Classify the card — one-off, epic or wave`
2026-09-13 16:50:27 PT  34790847117  failure   DRE-3711  — died at `Proof and demo cards`
2026-09-14 08:59:50 PT  34865785548  failure
2026-09-17 09:56:47 PT  35249687786  failure
2026-09-11 21:08:03 PT  34672201376  cancelled
```

**There were no turn-cap and no over-ceiling review deaths in the window at all.**
Not one review ran out of turns: the highest reading in §2 is 42 of 100. The
card anticipates this — "expected: zero or one" — so the branch DRE-3499 changed
was not exercised by a turn-cap death, and this record says that plainly rather
than dressing something else up as one.

**What was read by hand instead.** Twelve medic runs, read one at a time out of
their own logs, and the branch each one's `Is this a limit death?` step took.
This is a **superset** of what the criterion asks for, on purpose: it covers
every red plan run in the window — 34707066435 (§3b), 34722278266, 34785966041,
34789667828, 34865785548 — and sweeps up the medics that woke beside them for a
fix, a sync or a test run on the same afternoons, so a `kind=claude` stamp could
not hide in the traffic next door. The first eight are 2026-09-13, the day of the
observed review:

| medic run | watched plan run | card | `class=` | branch taken |
| -- | -- | -- | -- | -- |
| [34785968270](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34785968270) | 34785930776 | — | `linear_ratelimited` | `not a limit death (decision: requeue) — ordinary medic handling` |
| [34785983466](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34785983466) | 34785966041 | DRE-3837 | `linear_ratelimited` | `🪦 limit-death: kind=linear stage=plan reset=unknown run=34785966041` |
| [34786096258](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34786096258) | 34785736949 | DRE-3412 | `environment_crash` | `not a limit death (decision: requeue) — ordinary medic handling` |
| [34786191247](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34786191247) | 34785514000 | DRE-3414 | `environment_crash` | `not a limit death (decision: requeue) — ordinary medic handling` |
| [34789680259](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34789680259) | 34789667828 | DRE-3622 | `linear_ratelimited` | `🪦 limit-death: kind=linear stage=classify reset=unknown run=34789667828` |
| [34789684132](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34789684132) | 34789663752 | — | `linear_ratelimited` | `not a limit death (decision: requeue) — ordinary medic handling` |
| [34789722560](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34789722560) | 34786751507 | — | `linear_ratelimited` | `🪦 limit-death: kind=linear stage=fix reset=unknown run=34786751507` |
| [34790919330](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34790919330) | 34790899146 | — | `linear_ratelimited` | `not a limit death (decision: requeue) — ordinary medic handling` |
| [34722589853](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34722589853) | 34722278266 | DRE-3694 | `normal` | `not a limit death (decision: requeue) — ordinary medic handling` |
| [34722659876](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34722659876) | 34722278266 | DRE-3694 | `normal` | `not a limit death (decision: requeue) — ordinary medic handling` |
| [34865827451](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34865827451) | 34865785548 | DRE-3893 | `linear_ratelimited` | `🪦 limit-death: kind=linear stage=classify reset=unknown run=34865785548` |
| [34866096570](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34866096570) | 34865854325 | DRE-3638 | `linear_ratelimited` | `🪦 limit-death: kind=linear stage=sync reset=unknown run=34865854325` |

(Four more medics woke in the 2026-09-14 burst — 34865846462, 34865860540,
34866009114, 34866224267, watching a build, two sweeps and a sync. All four were
read too; two stamped `kind=linear` and two took the not-a-limit branch. None is
a plan run, so they are named here rather than tabled.)

One red plan run is unaccounted for and says so: **34790847117** (DRE-3711,
2026-09-13 16:50 PT, died at `Proof and demo cards`). No medic run that *ran*
watched it — the only non-skipped medic in that span, 34790919330, watched a
different run — so there is no `Is this a limit death?` output to read for it, and
it therefore stamped nothing.

**Not one `kind=claude` marker among the sixteen.** Six limit deaths were stamped
and all six say **`kind=linear`** — the workspace's 2,500-requests-an-hour wall,
which is a real wall and the correct reading; it is also the same exhaustion that
made DRE-3623's rounds 3 and 4 fall back to the 100-turn ceiling in §2. The other
ten took the not-a-limit branch and printed that sentence verbatim.

**One `kind=claude` marker exists across the six epics' threads, and it is not in
the review's window.** Grepping all six threads for `limit-death: kind=` returns
exactly one `kind=claude`, on **DRE-3621, 2026-09-12** — the day before the
observed review, and not from a turn-cap death. It is written up in full in §3b,
because a proof that quietly declined to mention it would be worth nothing. From
2026-09-13 onward — the day of the observed review and every day since in this
record — there is none.

### 3b. The one `kind=claude` stamp in the neighbourhood, and why it is not this epic's

On **2026-09-12** — after the merges but before the observed review —
[DRE-3621](https://linear.app/dreadnoughtfoundry/issue/DRE-3621)'s post-approval
review died and medic run
[34707095014](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34707095014)
took the **limit** branch and posted, at 2026-09-12 10:04:01 PT:

```
🪦 limit-death: kind=claude stage=plan reset=unknown run=34707066435
```

That review did **not** hit the turn cap — its tombstone reads

```
🪦 plan-critic-died: stage=post run=34707066435 attempt=1 step=posta subtype=success turns=1 ceiling=64
```

— one turn of sixty-four. Its execution record, verbatim from run
[34707066435](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34707066435)
at `2026-09-12T17:03:43Z`:

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": true,
  "duration_ms": 400,
  "num_turns": 1,
  "total_cost_usd": 0,
  "permission_denials_count": 0,
  "modelUsage": {}
}
```

**So this is outside DRE-3499's scope, and inside somebody else's.** DRE-3499
says a *turn-cap* result is never a Claude limit death; this was not a turn-cap
result, so the classifier behaved as this epic shipped it. But the run's log
carries **no account-limit text at all** — no 429, no "you've hit your limit" —
only the `is_error` / one turn / `$0` / sub-second corpse shape, and the medic
read `kind=claude` off that shape alone. Four different things wear that shape
(throttled, out of usage, credential revoked, service briefly unreachable), which
is exactly the defect epic
[DRE-3694](https://linear.app/dreadnoughtfoundry/issue/DRE-3694) was filed for;
its first card, DRE-4129 (*one reader says which wall a run hit*), went Done on
2026-09-17. **This record notes it as a finding, not as a failure of DRE-3499,
and files nothing:** DRE-3694 already owns it.

One more detail on the same run, for whoever tunes the constant: `plan-critic-died
… subtype=success` is the marker's machine field, and the plain-English death
note beside it does **not** say "(success)" — it reads *"it died after 1 turns of
its 64-turn ceiling in run 34707066435 (attempt 1), step `posta`"*, which is
DRE-3501's wording change holding on a real death.

---

## 4. The first data point: DRE-3282's own review, which predates the receipt

The review that released these three cards ran on **2026-09-10, before DRE-3498
merged at 14:13 PT**, so it left no `🧮 review-turns:` receipt — and DRE-3282's
thread confirms it: the epic's rounds 1, 2 and 3 carry no receipt, and the first
receipt on that thread is round 4's, two days later.

| round | when (PT) | record | receipt |
| -- | -- | -- | -- |
| 1 | 2026-09-10 06:40:33 | `plan-critic: stage=post round=1 result=SEND_BACK collisions=2 — DRE-3498 is written against constants that no longer exist on 'main'` | **none — DRE-3498 had not merged** |
| 2 | 2026-09-10 06:52:20 | `plan-critic: stage=post round=2 result=SEND_BACK collisions=0 — DRE-3504 (DEMO) is not blocked by DRE-3503 (PROOF) on the board` | **none** |
| 3 | 2026-09-10 07:11:51 | `plan-critic: stage=post round=3 result=PASS collisions=0` | **none** |
| 4 | 2026-09-12 22:30:46 | `plan-critic: stage=post round=4 result=PASS collisions=0` | `🧮 review-turns: spent=23 ceiling=60 children=5 model=claude-sonnet-5` |

**The ceiling and the turn count were read off the runs themselves.** Each of the
three pre-receipt rounds has its own `Agent Plan` run, identified by matching the
round record's timestamp on the epic to the execution result in the run's log —
in every case the record was posted **two seconds** after the model's result line,
which is what ties them together beyond doubt.

| round | run | ceiling step output, verbatim | execution result | round record |
| -- | -- | -- | -- | -- |
| 1 | [34483751603](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34483751603) | `post-approval review ceiling: 60 turns — sized from 5 cards` (13:37:19Z) | `"num_turns": 25` at 13:40:31Z, `is_error: false`, `$0.793378` | SEND_BACK at 13:40:33Z |
| 2 | [34484921221](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34484921221) | `post-approval review ceiling: 60 turns — sized from 5 cards` (13:48:29Z) | `"num_turns": 42` at 13:52:18Z, `is_error: false`, `$1.0104216` | SEND_BACK at 13:52:20Z |
| 3 | [34486698892](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34486698892) | `post-approval review ceiling: 60 turns — sized from 5 cards` (14:05:11Z) | `"num_turns": 48` at 14:11:49Z, `is_error: false`, `$1.65203` | **PASS** at 14:11:51Z |

(Rounds 1 and 2 each carry a second execution record in the same run — 28 turns
apiece — which is the re-plan agent, not the critic: each lands two to three
seconds before the *"the plan has been revised"* note, six minutes after the
critic's. Attribution is by timestamp, not by guess.)

So, for the record:

* **Ceiling it ran with: 60**, observed as a printed line and not derived. The
  formula in force was `30 + 4 × 5 = 50`, floored to 60 — the floor is what the
  step printed, so this data point cannot distinguish the old formula from the new
  one (`40 + 4 × 5 = 60` gives the same answer for five cards). That is worth
  knowing before anyone reads it as evidence about the base.
* **Turns spent: 25, then 42, then 48** across the three rounds.
* **Receipt: none existed.** DRE-3498 merged at 14:13 PT, two minutes after round
  3's PASS at 14:11:51 PT. The first receipt on DRE-3282's thread is round 4's,
  two days later on 2026-09-12 22:30:42 PT. Observed by absence over the whole
  thread.

**The releasing round spent 48 of 60 — 80% of its ceiling.** That is the tightest
reading in the whole series, and it is the review that let these three cards
build. Twelve more turns and the epic that fixed the ceiling would itself have
died at the ceiling. Round 4, the same five cards reviewed two days later under
the new formula, spent **23 of 60** — `spent=23 ceiling=60 children=5`, quoted in
§2 — so the variance round to round on one unchanged epic is 25 → 42 → 48 → 23.
Whoever re-tunes the constant should read that spread before reading any single
number in the table below.

**What this means for the series the constant is to be tuned from.** Thirteen
readings, nine of them from a `🧮` receipt and four from the run logs of the
pre-receipt rounds above:

| children | ceiling | spent | epic | date |
| -- | -- | -- | -- | -- |
| 5 | 60 | 25 | DRE-3282 round 1 | 2026-09-10 (no receipt — read off the run) |
| 5 | 60 | 42 | DRE-3282 round 2 | 2026-09-10 (no receipt — read off the run) |
| 5 | 60 | **48** | DRE-3282 round 3, the release | 2026-09-10 (no receipt — read off the run) |
| 5 | 60 | 23 | DRE-3282 round 4 | 2026-09-12 |
| 6 | 64 | 1 | DRE-3621 | 2026-09-12 (died at turn 1 — not a turn-cap reading) |
| 6 | 64 | 31 | DRE-3694 | 2026-09-17 |
| 11 | 84 | 25 | DRE-3622 | 2026-09-13 |
| 11 | 84 | 27 | DRE-3622 | 2026-09-13 |
| 11 | 84 | 26 | DRE-3892 | 2026-09-14 |
| 12 | 88 | 20 | DRE-3623 | 2026-09-13 |
| 12 | 88 | 17 | DRE-3623 | 2026-09-13 |
| 12 | *100 (fallback)* | 26 | DRE-3623 | 2026-09-13 |
| 12 | *100 (fallback)* | 42 | DRE-3623 | 2026-09-13 |

Two readings, offered as observations and not as recommendations. First: **turns
spent does not grow with child count** — the five-card epic spent 25, 42 and 48;
the twelve-card epic spent 17 and 20. Whatever a review costs, it is not the
number of cards, so a ceiling linear in children is buying headroom where it is
least needed. Second: **every reading since the new base took effect is
comfortably inside its ceiling** — the worst is 42 of 100, and under the formula
proper 27 of 84. The one tight reading in the table, 48 of 60, is from *before*
DRE-3498, on the old base, and is the near-miss that makes the case for the
change.

---

## What is owed, and to whom

Every observation this card asks for was made. One thing is simply not yet in
existence, and nothing here stages it:

1. **A seven-child review, reading `ceiling=68`.** No seven-child epic has been
   reviewed since the window opened; the child counts that did occur are 5, 6, 11
   and 12, and the formula is checked against all four above. Nothing needs doing
   — the next seven-card epic will record itself.

One thing noticed while reading, which belongs to another epic and is left there:
the `kind=claude` limit stamp of §3b, owned by DRE-3694.

**DRE-3503 is left in Todo for the CEO to close.**

Written 2026-09-17 between 15:35 and 16:40 PT, from the live Linear threads of
DRE-3282, DRE-3621, DRE-3622, DRE-3623, DRE-3694 and DRE-3892, and from the run
logs of every run linked above.
