# The split-ledger loop, observed live — 2026-09-17

The proof for [DRE-3363](https://linear.app/dreadnoughtfoundry/issue/DRE-3363),
the closing card of epic [DRE-3022](https://linear.app/dreadnoughtfoundry/issue/DRE-3022).
Five observations, each made against the live board and the live GitHub on
**2026-09-17 between 15:30 and 17:10 PT** by an operator session, and written
down here with the run link, the timestamp and the line read verbatim.

Every time below is **Pacific**. GitHub, Linear and AWS all answer in UTC; where
the UTC string is itself the evidence (a `generated_at` field, a `mergedAt`) it is
quoted as it stands and the Pacific reading given beside it.

**Read-only, with two deliberate writes.** Nothing here moved a card, changed a
label, dispatched a run or re-ran a workflow. The two writes this card's own
acceptance criteria ask for are made and named: the `ledger_injected_at` date in
`config/planner-audit.json` (observation 5), and the split-rate tables posted as
a comment on DRE-3022 (observation 5). The ledger rename in observation 4 was
made in a throwaway clone and undone in the same command.

**Four of the five observations hold as the card describes them. The fifth,
observation 5, holds with one honest qualification** — September straddles the
injection date and is not over, so the "after" side of the comparison is not yet
a clean month. That is said out loud in §5 rather than filled in.

---

## What is deployed, and where

| piece | card | on `main` since |
| -- | -- | -- |
| the ledger derives its own population | DRE-3356 | 2026-09-10 07:25 PT (PR [#344](https://github.com/dreadnought-foundry/bureau-pipeline/pull/344)) |
| the ledger regenerates itself daily | DRE-3357 | 2026-09-09 19:56 PT (PR [#343](https://github.com/dreadnought-foundry/bureau-pipeline/pull/343)) |
| `ledger_context.py` renders the planner block | DRE-3358 | 2026-09-09 16:19 PT (PR [#334](https://github.com/dreadnought-foundry/bureau-pipeline/pull/334)) |
| `plan.yml` injects the block into the planner's context | DRE-3359 | 2026-09-10 15:16 PT (PR [#359](https://github.com/dreadnought-foundry/bureau-pipeline/pull/359)) |
| the plan artifact records the ledger check per child | DRE-3362 | 2026-09-10 15:40 PT (PR [#361](https://github.com/dreadnought-foundry/bureau-pipeline/pull/361)) |
| the plan critic reads the ledger; the scorer gains the split-rate row | DRE-3079 | 2026-09-08 10:55 PT (PR [#311](https://github.com/dreadnought-foundry/bureau-pipeline/pull/311)) |

Two siblings of this epic were **Canceled on 2026-09-09 15:04 PT and never
shipped**: DRE-3360 (a second ledger reader for the pre-approval critic) and
DRE-3361 (the scorer's split-rate row). Both were made redundant by DRE-3079,
which had already landed the same two readers on 2026-09-08. Where this record
reads the critic's ledger citation (§3) or runs `planner_score.py split-rate`
(§5), it is reading **DRE-3079's** code, not a cancelled card's. Nothing in this
record depends on a cancelled card, and no observation is forced to stand in for
one.

---

## 1. The schedule ran — MET, and it was the first one that could

**The run.** [35183214700](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/35183214700),
workflow `Split ledger` (`.github/workflows/split-ledger.yml`), **event
`schedule`** — not `workflow_dispatch`. Started `2026-09-17T04:46:55Z`
(**2026-09-16 21:46 PT**), finished `2026-09-17T04:48:41Z` (**21:48 PT**),
conclusion `success`.

**What reached `main`.** Commit
[`e2a40e6`](https://github.com/dreadnought-foundry/bureau-pipeline/commit/e2a40e6),
`chore(ledger): regenerate the split ledger`, authored by
**`agent-bureau-bot[bot]`**, committed `2026-09-17 04:48:35 +0000`
(**2026-09-16 21:48 PT**).

**How it reached `main`, which is not what the card assumed.** The card asks for
a scheduled run "committing a regenerated `config/split-ledger.json` to `main` as
`agent-bureau-bot`". Since DRE-3879 the job does **not** push to `main` — branch
protection refused it every morning with `GH006: Protected branch update failed`
— so it commits to the one fixed branch `bot/split-ledger` and opens a pull
request. The commit above arrived on `main` through pull request
[#422](https://github.com/dreadnought-foundry/bureau-pipeline/pull/422),
`chore(ledger): regenerate the split ledger`, author `app/agent-bureau-bot`,
head ref `bot/split-ledger`, merged `2026-09-17T04:58:14Z`
(**2026-09-16 21:58 PT**). The observation the card wanted — a scheduled job
regenerating the ledger and the result landing on `main` as the bot, with no
hand edit — holds; the route is a pull request rather than a direct push.

**`generated_at` is later than the previous commit's, read out of the two file
versions:**

```
git show 611fcb6:config/split-ledger.json  →  "generated_at": "2026-09-10T13:42:09Z"   (2026-09-10 06:42 PT)
git show e2a40e6:config/split-ledger.json  →  "generated_at": "2026-09-17T04:48:32Z"   (2026-09-16 21:48 PT)
```

**Row count before and after:**

| | previous commit `611fcb6` | this commit `e2a40e6` |
| -- | -- | -- |
| `rows` | 40 | **41** |
| `seed_cards` | 10 | 10 |
| `monthly` | 4 | 4 |
| `window_days` | 90 | 90 |
| `generated_by` | `scripts/split_ledger.py derive` | `scripts/split_ledger.py derive` |

**Worth the CEO's eye: this was the first scheduled run in eight that succeeded.**
The eight scheduled runs on record are

```
35183214700  2026-09-16 21:46 PT  success
35056932285  2026-09-15 21:46 PT  failure
34930148277  2026-09-14 21:46 PT  failure
34807425603  2026-09-13 21:49 PT  failure
34738610160  2026-09-12 21:44 PT  failure
34673820509  2026-09-11 21:45 PT  failure
34563407278  2026-09-10 21:45 PT  failure
34438436573  2026-09-09 21:45 PT  failure
```

— seven nights of the `GH006` refusal the workflow's own header describes, which
is what DRE-3879 was filed for and fixed. The mechanism this observation proves
therefore has exactly **one** successful production run behind it, and this
record says so rather than implying a settled cadence.

**The gap that cost the loop three and a half days.** `ledger_context.py` calls a ledger older
than `LEDGER_MAX_AGE_HOURS` (72) `UNKNOWN`. With the previous derivation at
2026-09-10 06:42 PT, the ledger was stale to the planner from **2026-09-13
06:42 PT until 2026-09-16 21:48 PT** — three and a half days in which every
planner run read `UNKNOWN` instead of the rates. It is fresh again now, which is
what made observation 2 possible.

---

## 2. The planner was given the ledger — MET

**The epic.** [DRE-3694](https://linear.app/dreadnoughtfoundry/issue/DRE-3694)
— *"A run death's receipt names the wall and quotes the account's own words"* —
a real epic on the live board, six children.

**The run.** [35217665939](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/35217665939),
`Agent Plan`, job `call / bureau-card: DRE-3694`, started `2026-09-17T11:48:47Z`
(**2026-09-17 04:48 PT**), conclusion `success`.

**The two status lines, verbatim, from the `Assemble planner context` step at
`2026-09-17T11:49:15Z` (04:49 PT):**

```
LEDGER STATUS: fresh — 2026-09-17T04:48:32Z, 7.0 hours old
MULCH STATUS: UNKNOWN — no .mulch/expertise/planning.jsonl in this checkout
```

The `2026-09-17T04:48:32Z` in that line is the same `generated_at` §1 read out of
commit `e2a40e6` — the run is demonstrably reading the ledger the scheduled job
had written seven hours earlier, not a copy of it.

`MULCH STATUS: UNKNOWN` is the honest reading and not a defect of this card: the
`.mulch/expertise/` records live in the operator's checkout and reach `main` only
through `make mulch-sync`, so a CI checkout has none. `ledger_context` says
UNKNOWN rather than staying silent, which is the behaviour DRE-3358 shipped.

**The plan artifact's `ledger-check` table, one row per child** (DRE-3362), read
out of the run's own `plan-source-DRE-3694` artifact:

```ledger-check
[
  {"card": "DRE-4129", "tells_checked": ["contracts-between-pieces", "two-languages-or-tiers", "unenumerated-count", "unbounded-quantifier"], "ledger_match": "none", "ledger_status": "fresh"},
  {"card": "DRE-4131", "tells_checked": ["contracts-between-pieces", "two-languages-or-tiers", "unenumerated-count", "unbounded-quantifier"], "ledger_match": "DRE-3101", "ledger_status": "fresh"},
  {"card": "DRE-4134", "tells_checked": ["contracts-between-pieces", "two-languages-or-tiers", "unenumerated-count", "unbounded-quantifier"], "ledger_match": "DRE-3088", "ledger_status": "fresh"},
  {"card": "DRE-4135", "tells_checked": ["contracts-between-pieces", "two-languages-or-tiers", "unenumerated-count", "unbounded-quantifier"], "ledger_match": "DRE-3088", "ledger_status": "fresh"},
  {"card": "DRE-4137", "tells_checked": ["contracts-between-pieces", "two-languages-or-tiers", "unenumerated-count", "unbounded-quantifier"], "ledger_match": "none", "ledger_status": "fresh"},
  {"card": "DRE-4138", "tells_checked": ["contracts-between-pieces", "two-languages-or-tiers", "unenumerated-count", "unbounded-quantifier"], "ledger_match": "none", "ledger_status": "fresh"}
]
```

Six children, six rows, no omission — and the gate agreed. The `Plan artifact —
check` step at `2026-09-17T12:05:45Z` (05:05 PT) printed:

```
plan artifact: complete
```

---

## 3. The critic cited a row — MET, twice, and one of them is this week

The card's first choice was DRE-3022's own thread, and it is there. The second
sighting is four days old and on a different epic, which matters more: it shows
the citation is ordinary traffic rather than one historical comment.

### 3a. On DRE-3022's own thread — 2026-09-09 12:45:19 PT

The **🔎 Mechanical plan checks** comment on
[DRE-3022](https://linear.app/dreadnoughtfoundry/issue/DRE-3022), posted before
the critic's model read the plan. Verbatim, the ledger half of it:

```
Checked against the split ledger (`config/split-ledger.json`): 9 death row(s).

Findings (18):
- DRE-3361: shares 3 file(s) with DRE-3016, which the split ledger records dying 1 time(s) — config/planner-audit.json, scripts/planner_score.py, tests/test_planner_score.py (matched against the files it declared; the row is in config/split-ledger.json)
- DRE-3359: shares 2 file(s) with DRE-2719, which the split ledger records dying 2 time(s) — .github/workflows/plan.yml, briefs/planner.md (matched against the files its split pieces touched; the row is in config/split-ledger.json)
- DRE-3359: shares 2 file(s) with DRE-3022, which the split ledger records dying 2 time(s) — .github/workflows/plan.yml, briefs/planner.md (matched against the files it declared; the row is in config/split-ledger.json)
- DRE-3356: shares 3 file(s) with DRE-3022, which the split ledger records dying 2 time(s) — config/split-ledger.json, scripts/split_ledger.py, tests/test_split_ledger.py (matched against the files it declared; the row is in config/split-ledger.json)
- DRE-3363: carries the two-languages-or-tiers tell (the footprint spans workflow, python) — the split ledger says cards carrying the two-languages-or-tiers tell died 6 of 7 times (config/split-ledger.json)
```

The rows named are **DRE-3016 (1 death), DRE-2719 (2 deaths), DRE-3022 (2
deaths)** and the tell rate quoted is **6 of 7**. The fourth finding above is
transcribed from the thread as it stands; the epic's own row is cited against
three of its children.

### 3b. On a live epic four days later — 2026-09-17 05:05:47 PT

Same comment shape, on [DRE-3694](https://linear.app/dreadnoughtfoundry/issue/DRE-3694),
from run [35217665939](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/35217665939)
— the same run as §2. Verbatim:

```
Checked against the split ledger (`config/split-ledger.json`): 40 death row(s).

Findings (2):
- DRE-4135: carries the two-languages-or-tiers tell (the footprint spans workflow, python) — the split ledger says cards carrying the two-languages-or-tiers tell died 11 of 15 times (config/split-ledger.json)
- DRE-4134: carries the two-languages-or-tiers tell (the footprint spans workflow, python) — the split ledger says cards carrying the two-languages-or-tiers tell died 11 of 15 times (config/split-ledger.json)
```

The population grew from **9 death rows** on 2026-09-09 to **40** on 2026-09-17,
and the same tell's rate moved from **6 of 7** to **11 of 15** — the ledger is
accumulating history, not repeating a seed.

**No throwaway `PROOF-PL-` epic was filed.** The card allows one only if no real
epic carries a citation. Two do, so none was needed and none was created.

---

## 4. UNKNOWN is not silent — MET

Both halves run locally, in the throwaway clone this record was written from,
whose `main` was at
[`22b0de91`](https://github.com/dreadnought-foundry/bureau-pipeline/commit/22b0de9154326425f3c18ad1e8623a37d9fc4a57)
(*Merge pull request #434 …*).

**`ledger_context.py render`, with the ledger present** — the control reading:

```
LEDGER STATUS: fresh — 2026-09-17T04:48:32Z, 18.2 hours old
MULCH STATUS: UNKNOWN — no .mulch/expertise/planning.jsonl in this checkout
```

**The same command with `config/split-ledger.json` renamed out of the way:**

```
LEDGER STATUS: UNKNOWN — missing — there is no ledger at <clone>/config/split-ledger.json
MULCH STATUS: UNKNOWN — no .mulch/expertise/planning.jsonl in this checkout
```

The file was moved back in the same command; `git status` was clean afterwards
apart from this record and the `config/planner-audit.json` edit of §5.

**`plan_artifact.py check` on an artifact whose records say UNKNOWN passes.** The
real DRE-3694 artifact of §2 was copied and its six `"ledger_status": "fresh"`
values rewritten to `"UNKNOWN"` (6 rows rewritten). Both versions were checked:

```
$ python3 scripts/plan_artifact.py check plan-artifact.md            # the real artifact, fresh
plan artifact: complete
exit=0

$ python3 scripts/plan_artifact.py check plan-artifact-unknown.md    # every row UNKNOWN
plan artifact: complete
exit=0
```

An UNKNOWN ledger check is a **pass**, as DRE-3362 requires — the gate refuses an
*omitted* check, never an honest one.

---

## 5. The number — MET, with the "after" side not yet a clean month

### The date the ledger reached the planner

`config/planner-audit.json`, dimension `split-rate`, previously held
`"ledger_injected_at": null` with a reason naming DRE-3078 as unshipped. Read
from the pull request that actually did the wiring:

```
$ gh pr view 359 --json number,headRefName,mergedAt,title
#359 agent/DRE-3359-ledger-into-planner-context mergedAt=2026-09-10T22:16:25Z
     feat(DRE-3359): the planner is handed the ledger, and told to size against it
```

`2026-09-10T22:16:25Z` is **2026-09-10 15:16 PT**. That value is now the field,
and `ledger_injected_why` beside it names PR #359 and this record. The
two-way check passes:

```
$ python3 scripts/planner_score.py check
8 dimension(s) over 3 epic(s), 0 problem(s)
exit=0
```

### The tables, run against the live board

`python3 scripts/planner_score.py split-rate --month <YYYY-MM>` for every month
from 2026-07 to the current one, run 2026-09-17 between 16:55 and 17:09 PT.
Each month is a live read of Linear — the three runs together spent about 1,440
of the workspace's 2,500 requests for the hour, which is why they were run once.

**2026-07**

| Month | Split | Answered | Not yet run | Unreadable | Rate | Side |
| -- | -- | -- | -- | -- | -- | -- |
| 2026-07 | 0 | 159 | 69 | 0 | 0.0% | before |

**2026-08**

| Month | Split | Answered | Not yet run | Unreadable | Rate | Side |
| -- | -- | -- | -- | -- | -- | -- |
| 2026-08 | 16 | 243 | 64 | 0 | 6.6% | before |

**2026-09** (incomplete — read on the 17th)

| Month | Split | Answered | Not yet run | Unreadable | Rate | Side |
| -- | -- | -- | -- | -- | -- | -- |
| 2026-09 | 13 | 412 | 271 | 0 | 3.2% | after |

The header each run printed, verbatim:

```
The ledger reached the planner on 2026-09-10T22:16:25Z; months are marked before and after it.
```

### What the "after" side does and does not say

**It is not yet a clean reading, and this record will not present it as one.**
Two reasons, both structural:

1. **September straddles the injection.** The ledger reached the planner on
   2026-09-10, ten days into the month. The shipped reader labels the whole month
   `after`, so the 3.2% includes every planner-created child cut between
   2026-09-01 and 2026-09-10 — before the planner had ever seen the ledger.
   (DRE-3361, the card that would have added a third `straddles` label for
   exactly this case, was Canceled; DRE-3079's reader has two sides, not three.)
2. **September is not over.** 271 of its 412 children have not yet run, so the
   population that can be split at all is still growing.

The first month that can answer the epic's question cleanly is **2026-10**, read
after 2026-11-01. Until then the comparison worth quoting is the *before* pair —
**0.0% in July, 6.6% in August** — and the note that the after side exists but is
not yet interpretable.

The same caveat is written into the comment posted on DRE-3022.

---

## What was written, and by whom

* `config/planner-audit.json` — `ledger_injected_at` and `ledger_injected_why`,
  in the same pull request as this file, as the card's acceptance criteria
  require.
* One comment on [DRE-3022](https://linear.app/dreadnoughtfoundry/issue/DRE-3022)
  carrying the three tables above, the injection date used, and the "after"
  caveat.

Nothing else. No card was moved, no label changed, no workflow dispatched or
re-run, no repository setting touched. **DRE-3363 itself is left in Todo for the
CEO to close** — this record is what he reads before he does.

---

## Nothing was left out

Every observation the card asks for was made. The two qualifications are stated
where they belong rather than omitted:

* **§1** — the scheduled job reaches `main` through a pull request on
  `bot/split-ledger`, not a direct push, and 2026-09-16 was the first of eight
  scheduled runs to succeed.
* **§5** — the "after" side of the split rate is not yet a clean month, for the
  two reasons above.

Written 2026-09-17, from live Linear, live GitHub Actions and the git history of
`main`, between 15:30 and 17:10 PT.
