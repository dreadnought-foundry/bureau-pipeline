# Two Linear budgets, one drained on purpose — observed 2026-09-10

The proof for [DRE-3176](https://linear.app/dreadnoughtfoundry/issue/DRE-3176),
under epic [DRE-3168](https://linear.app/dreadnoughtfoundry/issue/DRE-3168).
Every time below is Pacific (PDT, UTC−7) and comes from the event's own clock.

The claim under proof is the epic's headline: **an operator-tools session that
reads the board as hard as it likes can no longer take a sweep or a merge sync
down**, and a pass that does hit a limit stops and says whose budget is spent.

**Two readings, and they must not be confused.**

1. **The isolation held.** Draining `operator-tools` to zero cost the fleet
   nothing. The two users are two buckets, every refusal line in the hour named
   `budget: fleet` or `budget: operator-tools` correctly, and no run anywhere in
   the fleet spent an `operator-tools` request.
2. **The frugality premise did not, and the card's first acceptance criterion
   fails on it.** The `fleet` user was at the floor of its own 2,500 before this
   proof touched anything — 39 remaining at 07:40:20 PT, five minutes before the
   drain loop started, and a fleet sweep had already died on it at 07:34:08 PT.
   Inside the observation hour **two reconcile runs failed** on `RATELIMITED`
   (08:10:21 PT on bureau-pipeline and 08:23:45 PT on agent-bureau, both exit
   75, both `budget: fleet`), nine more sweeps were refused mid-pass while still
   reporting `success`, and a planner run died half way through parking a card
   for the CEO. None of that was the operator's doing. §7 is the write-up, and
   it is why this document is not a clean sheet.

---

## 1. The two keys are two users — 07:39:21 PT

`scripts/check_linear_identities.py check`, with all three key homes exported
(the relay's read out of Secrets Manager in-process, never to a file):

```
  [OK] fleet: LINEAR_API_KEY_FLEET resolves to 'Agent-Bureau' (id cebc4c53-fad2-410f-be31-f920b6ad773f), not an admin
  [OK] fleet/relay: LINEAR_API_KEY_RELAY resolves to 'Agent-Bureau' (id cebc4c53-fad2-410f-be31-f920b6ad773f), not an admin
  [OK] operator-tools: LINEAR_API_KEY resolves to 'bureau-tools' (id 0913a8db-839c-42d0-b2e8-70eda1b64617), not an admin
3 key homes declared; 0 failed, 0 unknown
```

Exit 0. Three `[OK]` lines, both fleet homes on one id, `operator-tools` on a
different id, none an admin.

### The relay's standing

| fact | reading |
| -- | -- |
| `bureau/relay/linear-api-key` last changed | 2026-09-08 16:09:58 PT |
| agent-bureau PR [#2299](https://github.com/dreadnought-foundry/agent-bureau/pull/2299) (DRE-3169) merged | 2026-09-05 11:24:31 PT |
| a relay deploy since that merge? | **yes** — the `bureau-linear-relay` Lambda was last modified 2026-09-08 16:10:15 PT, one minute after the secret was written |

So the deploy that DRE-3169 taught to refuse a non-fleet key has run once since
the guard landed, and the secret it wrote resolves to `Agent-Bureau` — the
`[OK] fleet/relay` line above is the outcome of that deploy, not of a hand fix.

The credential doctor's `linear-identity` row from the same session
(`AWS_PROFILE=dreadnought AWS_REGION=us-west-2 python3 scripts/claude_cred_doctor.py`,
run from agent-bureau):

```
[PASS] linear-identity
    observed: .env LINEAR_API_KEY: bureau-tools (admin: no)
              .env LINEAR_API_KEY_FLEET: Agent-Bureau (admin: no)
              bureau/relay/linear-api-key: Agent-Bureau (admin: no)
    means:    two different non-admin users, and bureau/relay/linear-api-key is the fleet one (Agent-Bureau). A relay deploy from this machine cannot re-identify the relay.
    remedy:   none.
```

That row is the one piece PR #2299's review said only the operator could close.
It is closed. Note the failure mode next to it: run without `AWS_PROFILE` and
the same row reads `[UNKNOWN] … Secrets Manager can't find the specified
secret`, which is the tool refusing to call a thing it could not look at.

## 2. The starting state, read at one instant — 07:40:20 PT

This is the reading the rest of the document depends on. Both keys were asked
`{ viewer { id name } }` in the same second and their rate-limit headers read
off the response:

| key | user | remaining | reset the header named |
| -- | -- | -- | -- |
| `LINEAR_API_KEY` (operator-tools) | bureau-tools | **2499 / 2500** | 2026-09-10 08:40:20 PT |
| `LINEAR_API_KEY_FLEET` (fleet) | Agent-Bureau | **39 / 2500** | 2026-09-10 08:40:20 PT |

Two facts fall straight out of it.

**The buckets are separate, and this is the measurement that says so.** 2499
against 39 at the same instant, on one workspace, is not a shared quota.

**The fleet was already at the floor before the proof began.** This procedure
had spent about five fleet-user requests by 07:40:20 PT — the identity check in
§1 resolves the fleet key and the relay key, and the two credential-doctor runs
resolve them again — against roughly 2,461 already gone. Three more readings
over the next minute — 42 at 07:40:30, 41 at 07:40:51, 45 at 07:41:11 — show it
pinned there and trickling back a few at a time, which is what a leaky bucket
at its limit looks like.

**It had already killed a sweep, eleven minutes before the drain loop started.**
The scheduled `reconcile.yml` run on agent-bureau at **07:34:08 PT**
([34489912207](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/34489912207))
exited **75** with `linear-budget: 57 → 0 (spent 57 this run; window resets
08:35 PT; refused after 22 calls; budget: fleet)`. That is outside the
observation window and it is the cleanest independence evidence in this
document: the fleet was failing on its own budget before anything here touched
a key.

**How the fleet's bucket went over the hour**, every reading from a bare
`viewer` query on the fleet key:

| when (PT) | fleet remaining |
| -- | -- |
| 07:40:20 | 39 |
| 07:41:11 | 45 |
| 07:54:24 | **0** — the query itself is answered `HTTP 400` |
| 08:20:07 | 42 |
| 08:40:02 | 86 |

Against it, the operator's: 2,499 at 07:40:20, **0** at 07:53:54, 1,042 at
08:20:07 — refilling steadily at roughly the documented 2,500/hour while the
fleet stayed pinned at its floor for the whole hour.

**What the instrument cost.** About **twelve** fleet-user requests across the
hour, eleven of them answered: five from §1's identity check and the two
credential-doctor runs, and seven bare `viewer` reads for the table above (four
in the first minute, one refused at 07:54:24, one at 08:20:07, one at
08:40:02). Roughly eleven of the fleet's spend is this document's own;
everything else below is traffic the fleet produced without any help.

**The reset header is rolling, not a wall clock.** Every reading returned
exactly one hour ahead of the moment it was taken. `window resets HH:MM PT` in
the budget lines below is therefore "an hour from the last call this process
made", not a fixed boundary at the top of the hour; a drained bucket starts
trickling back immediately rather than staying dead until a stroke of the
clock.

## 3. The operator's bucket, drained on purpose — 07:45:10 to 07:53:54 PT

One Python loop of `{ viewer { id } }` through `linear_ops.gql`, with
`LINEAR_IDENTITY=operator-tools`, nothing else in the process:

```
loop start: 2026-09-10 07:45:10 PT
  250 calls ok at 2026-09-10 07:46:22 PT (72s)
  ...
  2500 calls ok at 2026-09-10 07:53:48 PT (518s)
FIRST RATELIMITED at 2026-09-10 07:53:54 PT after 2535 successful calls
exception: Linear API returned 400 from https://api.linear.app/graphql: rate limited: 2500 requests/hour exhausted — body: '{"errors":[{"message":"Rate limit exceeded. Only 2500 requests are allowed per 1 hour. …","extensions":{"type":"ratelimited","code":"RATELIMITED","statusCode":429,…}}]}'; budget: operator-tools
loop end: 2026-09-10 07:53:54 PT (524s elapsed)
linear-budget: 2499 → 0 (spent 2499 this run (refilled mid-run); window resets 08:53 PT; refused after 2539 calls; budget: operator-tools)
```

**The first `RATELIMITED` arrived at 07:53:54 PT**, 8 minutes 44 seconds in,
after 2,535 calls had been answered. More than 2,500 went through because the
bucket leaks back while a loop is draining it — the same reason the budget line
says `refilled mid-run`.

Three transient faults surfaced on the way and each was retried once, exactly
as DRE-3087 built it: one read timeout, one `HTTP 503`, one TLS handshake
timeout. None of them ended the loop and none was mistaken for a limit.

The line names the owner: `budget: operator-tools`. That is DRE-3321's
sentence, and it is the half that makes the rest of this document readable.

## 4. A reconcile pass run by hand as the exhausted user — 07:53:54 PT

The command, straight from the card, run from a worktree of `bureau-pipeline`:

```
LINEAR_API_KEY=<operator-tools> LINEAR_IDENTITY=operator-tools \
  REPO=dreadnought-foundry/bureau-pipeline REPO_SLUG=bureau-pipeline \
  python3 scripts/reconcile.py
```

Started 07:53:54 PT, ended 07:54:24 PT. **Exit code 75.** The stderr that
matters:

```
epic-close: could not close DRE-3421 (Linear API returned 400 from https://api.linear.app/graphql: rate limited: 2500 requests/hour exhausted — body: '{"errors":[{"message":"Rate limit exceeded. Only 2500 requests are allowed per 1 hour. …"}]}'; budget: operator-tools) — skipped this sweep
reconcile: linear error from https://api.linear.app/graphql: rate limited: 2500 requests/hour exhausted — refused after 16 calls, no request sent; window resets 08:54 PT; budget: operator-tools
reconcile: the operator-tools user's Linear quota is exhausted — this is a transient, self-healing condition, NOT a defect in the estate. Nothing to retry: the quota refills on its own and the next scheduled sweep reconciles the board.
linear-budget: 1 → 0 (spent 1 this run (refilled mid-run); window resets 08:54 PT; refused after 16 calls; budget: operator-tools)
```

Everything the card asked for is on those four lines: exit 75, `refused after
16 calls, no request sent`, `window resets 08:54 PT`, the plain sentence naming
the `operator-tools` user, and the `linear-budget:` line.

Two details worth having in the record rather than rounded off.

**`refused after 16` is not `refused after 0`, and that is honest.** The bucket
had trickled 16 requests back in the seconds between the drain loop stopping
and this pass starting, so the sweep spent them before Linear said no again.
The refusal armed on the first `RATELIMITED` and every call after it was
refused locally with no request sent — which is the behaviour under proof.

**The whole pass took 30 seconds, not the several minutes a healthy sweep
takes.** It did not walk the board. What it did keep doing is everything that
does not need Linear — `stale-merge-ref`, `park-gate`, `approved-but-red`,
`crashed-review` and the watchdogs all still reported off GitHub — so a dry
Linear bucket costs the sweep its board half and nothing else.

## 5. Every sweep, merge sync and planner run in the hour

The window is 07:39 to 08:40 PT — from the identity check to the end of the
observation. `reconcile.yml` runs on its `*/15` cron across the six repos in
`config/repo-map.json` plus `bureau-harness`; `linear-sync.yml` fires on merges,
of which three happened on their own inside the window (nothing was merged to
manufacture one); `plan.yml` and `self-plan.yml` fired on cards that were in
Planning. `portico` and `atlas` have `reconcile.yml` **disabled manually**, so
neither can appear below; `deltasolv` produced no run in the window.

Every `linear-budget:` line below is read out of the run's own log
(`gh run view <id> --log`).

| start (PT) | repo | workflow | trigger | run | conclusion | `linear-budget:` |
| -- | -- | -- | -- | -- | -- | -- |
| 07:46:17 | agent-bureau | reconcile | schedule | [34491250516](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/34491250516) | success | `48 → 0 (spent 48 … refused after 62 calls; budget: fleet)` |
| 07:46:22 | agent-bureau-demo | reconcile | schedule | 34491259301 | success | `15 → 18 (window rolled; budget: fleet)` |
| 07:46:32 | bureau-harness | reconcile | schedule | 34491276591 | success | `46 → 46 (spent 0; budget: fleet)` |
| 07:47:52 | bureau-pipeline | self-reconcile | schedule | 34491419767 | success | `20 → 0 (spent 20 … refused after 33 calls; budget: fleet)` |
| 07:53:29 | bureau-pipeline | self-plan | repository_dispatch | [34492032942](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34492032942) | **failure** | `2 → 0 (spent 2 … refused after 4 calls; budget: fleet)` |
| 07:53:45 | agent-bureau-demo | plan | repository_dispatch | 34492062060 | success | `133 → 124 (spent 9; budget: fleet)` |
| 08:03:47 | agent-bureau | linear-sync | pull_request | 34493166711 | success | `116 → 116 (spent 0; budget: fleet)` |
| 08:04:10 | agent-bureau | reconcile | repository_dispatch | 34493209823 | success | `168 → 69 (spent 99; budget: fleet)` |
| 08:04:10 | agent-bureau-demo | reconcile | schedule | 34493210209 | success | `75 → 67 (spent 8; budget: fleet)` |
| 08:04:23 | bureau-harness | reconcile | schedule | 34493234962 | success | `169 → 157 (spent 12; budget: fleet)` |
| 08:04:24 | agent-bureau | reconcile | schedule | 34493237821 | cancelled | — superseded by the dispatch 14 s earlier |
| 08:04:44 | agent-bureau | linear-sync | pull_request | 34493275231 | success | `161 → 161 (spent 0; budget: fleet)` |
| 08:05:29 | agent-bureau | reconcile | repository_dispatch | 34493361008 | success | `73 → 0 (spent 73 … refused after 37 calls; budget: fleet)` |
| 08:08:32 | agent-bureau | linear-sync | pull_request | 34493694403 | success | `40 → 40 (spent 0; budget: fleet)` |
| 08:08:55 | agent-bureau | reconcile | repository_dispatch | 34493733873 | success | `41 → 0 (spent 41 … refused after 79 calls; budget: fleet)` |
| 08:10:21 | bureau-pipeline | self-reconcile | schedule | [34493863377](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34493863377) | **failure** | `34 → 0 (spent 34 … refused after 8 calls; budget: fleet)` |
| 08:13:28 | agent-bureau | linear-sync | pull_request | 34494186005 | success | `16 → 16 (spent 0; budget: fleet)` |
| 08:13:59 | agent-bureau | reconcile | repository_dispatch | 34494251619 | success | `103 → 16 (spent 87; budget: fleet)` |
| 08:18:21 | agent-bureau-demo | plan | repository_dispatch | 34494762705 | success | `30 → 22 (spent 8; budget: fleet)` |
| 08:22:10 | bureau-pipeline | self-linear-sync | pull_request | 34495178741 | success | `63 → 53 (spent 10; budget: fleet)` |
| 08:22:22 | bureau-pipeline | self-reconcile | repository_dispatch | 34495201707 | success | `37 → 0 (spent 37 … refused after 28 calls; budget: fleet)` |
| 08:23:45 | agent-bureau | reconcile | schedule | [34495354416](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/34495354416) | **failure** | `27 → 0 (spent 27 … refused after 36 calls; budget: fleet)` |
| 08:23:48 | agent-bureau-demo | reconcile | schedule | 34495360161 | success | `31 → 26 (spent 5; budget: fleet)` |
| 08:23:50 | bureau-harness | reconcile | schedule | 34495362483 | success | `22 → 20 (spent 2; budget: fleet)` |
| 08:25:42 | bureau-pipeline | self-reconcile | schedule | 34495564478 | success | `30 → 0 (spent 30 … refused after 33 calls; budget: fleet)` |
| 08:27:24 | agent-bureau | linear-sync | pull_request | 34495746873 | success | `24 → 17 (spent 7; budget: fleet)` |
| 08:27:42 | agent-bureau | reconcile | repository_dispatch | 34495778466 | success | `27 → 0 (spent 27 … refused after 31 calls; budget: fleet)` |
| 08:31:01 | agent-bureau | linear-sync | pull_request | 34496147383 | success | `25 → 25 (spent 0; budget: fleet)` |
| 08:33:33 | agent-bureau | reconcile | schedule | 34496425978 | success | `92 → 0 (spent 92 … refused after 87 calls; budget: fleet)` |
| 08:33:43 | bureau-harness | reconcile | schedule | 34496445403 | success | `91 → 86 (spent 5; budget: fleet)` |
| 08:33:44 | agent-bureau-demo | reconcile | schedule | 34496446879 | success | `19 → 23 (window rolled; budget: fleet)` |
| 08:36:51 | bureau-pipeline | self-reconcile | schedule | 34496785358 | success | `61 → 0 (spent 61 … refused after 46 calls; budget: fleet)` |
| 08:36:54 | bureau-pipeline | self-plan | repository_dispatch | 34496791309 | success | `48 → 46 (spent 2; budget: fleet)` |

**The last sweep in the hour was 08:36:51 PT** (bureau-pipeline
`self-reconcile` 34496785358); the last run of any kind was the 08:36:54 PT
self-plan. **bureau-pipeline's own sweep and merge sync are `self-reconcile.yml`
and `self-linear-sync.yml`**, not the `reconcile.yml`/`linear-sync.yml` names
every other repo uses — those files there are the reusable workflows other repos
call. A search by file name misses this repo's sweeps entirely, which is worth
knowing before anyone reads a table like this one again.

Read the `budget:` word on every line: **`fleet`, every time.** Not one run in
the hour spent an `operator-tools` request. That is the mechanical statement of
the isolation the epic asked for.

Four things in the table are worth naming.

**Seven merge syncs ran and every one is green**, five of them spending nothing
at all and none more than 10 — DRE-3236's frugality holding exactly where it was
aimed.

**Three runs failed on `RATELIMITED`, all on the fleet's budget**: the 07:53:29
self-plan, the 08:10:21 bureau-pipeline sweep and the 08:23:45 agent-bureau
sweep.

**Nine more sweeps carry `refused after N calls` on a `success` conclusion** —
five on agent-bureau, four on bureau-pipeline. Every one of bureau-pipeline's
five sweeps in the hour was refused; four of them still reported green.

**`reconcile.yml` on agent-bureau is the fleet's heaviest reader, and it is not
running on the `*/15` cron.** Nine runs on that repo in 54 minutes — four on the
schedule, five on `repository_dispatch`, one scheduled run cancelled 14 seconds
after a dispatch had already started. Its spend per run was 27, 41, 48, 73, 87,
92 and 99, against the card's ≤ 30. What dispatches it that often is a question
this document does not answer.

## 6. What the hour cost — `check_linear_budget.py --hours 1`, 08:41:15 PT

```
linear budget, last 1h
repo                 workflow                          runs   spent  max/run
----------------------------------------------------------------------------
agent-bureau         Reconcile                            4     233       92
bureau-pipeline      Reconcile                            4     162       61
agent-bureau-demo    Agent Plan                           2      27        9
bureau-pipeline      Red-Main Repair                      3      19       12
bureau-pipeline      Agent Fix                            2      15        6
agent-bureau         Linear Sync                          3      14        7
bureau-pipeline      Linear Sync                          1      14       10
agent-bureau-demo    Reconcile                            4      13        8  (2 line(s) unknown/rolled)
bureau-pipeline      Agent Plan                           2      11        6
agent-bureau         Merge Gate                          13       7        4
agent-bureau         QA Review                            2       5        4
agent-bureau         Agent Fix                            3       2        1
agent-bureau         Verify                               2       1        1
bureau-pipeline      Merge Gate                           9       1        1
bureau-pipeline      Pipeline Medic                      11       1        1
bureau-pipeline      QA Review                            1       1        1
agent-bureau         CI                                   3       0        0
agent-bureau         Console Release                      2       0        0
agent-bureau         Release train                        5       0        0
agent-bureau-demo    Agent Fix                            2       0        0
agent-bureau-demo    CI                                   2       0        0
agent-bureau-demo    Merge Gate                           2       0        0
agent-bureau-demo    Pipeline Medic                       3       0        0
agent-bureau-demo    QA Review                            2       0        0
bureau-pipeline      Integration Harness                  1       0        0
bureau-pipeline      Pipeline Tests                       2       0        0
bureau-pipeline      Promote Channel                      4       0        0
----------------------------------------------------------------------------
TOTAL                                                    94     526   (145 run(s) skipped: log not available)
```

Against the four readings the card expects:

| expected | observed | |
| -- | -- | -- |
| Reconcile ≤ 30 per run on every repo | **92** on agent-bureau, **61** on bureau-pipeline | ✗ |
| Linear Sync ≤ 15 per run on every repo | 7 on agent-bureau, 10 on bureau-pipeline | ✓ |
| the hour's total under 600 | 526 | ✓, but see below |
| zero `unknown/rolled` lines | **2**, both on agent-bureau-demo Reconcile | ✗ |

**The 526 is a floor, not a census, and the two `unknown/rolled` lines say why
the ceiling matters.** 145 runs in the hour were skipped because their logs
could not be read, and the fleet's own rate-limit header says the user spent far
more than 526 in the same window. The `window rolled` readings are the demo
sweeps at 07:46:22 and 08:33:44 — both ended with more `remaining` than they
started with, which at the floor of a leaky bucket is ordinary refill rather
than a real window roll, and a spend that reads as unknown either way.

## 7. The finding: the FLEET user was dry all hour, and it cost real work

The operator's drain proved the isolation. It also, by accident, put a
stopwatch on something else: **the `fleet` user was at the floor of its own
2,500 for the entire hour, with no help from the operator at all**, and real
work paid for it.

### 7.1 Two reconcile runs failed — 08:10:21 and 08:23:45 PT

`self-reconcile.yml` on bureau-pipeline, run
[34493863377](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34493863377),
at 08:10:21 PT, **exit 75**, `refused after 8 calls, no request sent; window
resets 09:10 PT; budget: fleet`. Then `reconcile.yml` on agent-bureau, run
[34495354416](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/34495354416),
at 08:23:45 PT, fired by the `*/15` schedule, **conclusion `failure`, exit 75**:

```
reconcile: linear error from https://api.linear.app/graphql: rate limited: 2500 requests/hour exhausted — refused after 36 calls, no request sent; window resets 09:24 PT; budget: fleet
reconcile: the fleet user's Linear quota is exhausted — this is a transient, self-healing condition, NOT a defect in the estate. Nothing to retry: the quota refills on its own and the next scheduled sweep reconciles the board.
linear-budget: 27 → 0 (spent 27 this run (refilled mid-run); window resets 09:24 PT; refused after 36 calls; budget: fleet)
```

Everything DRE-3202 and DRE-3321 promised is on those lines — the named
condition, the call count, the roll time, the owner, and a distinct exit code
rather than a traceback. The mechanism worked perfectly. What it reported is
that the board went unreconciled for those two passes because the fleet had
nothing left to spend. Both are `budget: fleet`; neither is the operator's.

### 7.2 A sweep was refused and still reported `success` — 07:46:17 PT

Run [34491250516](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/34491250516)
took a real `RATELIMITED` 400 at 07:47:20 PT, inside `report_epic_growth`:

```
epic-growth: DRE-2530 unknown — Linear did not answer (Linear API returned 400 … rate limited: 2500 requests/hour exhausted …; budget: fleet)
epic-growth: DRE-2668 unknown — Linear did not answer (linear error … refused after 62 calls, no request sent; window resets 08:47 PT; budget: fleet)
… nine more epics, all unknown …
linear-budget: 48 → 0 (spent 48 this run (refilled mid-run); window resets 08:47 PT; refused after 62 calls; budget: fleet)
```

That catch is deliberate and it is the right call — `report_epic_growth` says
so in as many words: *"a KPI is never worth failing a sweep for, and one
epic's unreadable history must not cost the others theirs."* The sweep's real
work had already been done in its 62 calls; only the growth tail was blind. The
consequence is not a bug, it is a blind spot: the run's conclusion is `success`,
the medic only ever looks at failures, so a sweep that Linear refused is
invisible to everything except somebody reading its log. **Nine sweeps in this
hour carry `refused after N calls` on a `success` conclusion** — five of
agent-bureau's nine reconcile runs and four of bureau-pipeline's five.

### 7.3 A planner run died half way through parking a card — 07:53:29 PT

`self-plan.yml` on bureau-pipeline, run
[34492032942](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34492032942),
classified [DRE-3551](https://linear.app/dreadnoughtfoundry/issue/DRE-3551) as
un-plannable and went to park it for the CEO. It got half way:

```
commented on DRE-3551
Traceback (most recent call last):
  File ".bureau-pipeline/scripts/planning_escalation.py", line 454, in escalate
    linear_ops.cmd_state(identifier, lane)
  File ".bureau-pipeline/scripts/linear_ops.py", line 853, in cmd_state
    issue = get_issue(identifier)
…
linear_ops.LinearRateLimited: Linear API returned 400 … rate limited: 2500 requests/hour exhausted …; budget: fleet
linear-budget: 2 → 0 (spent 2 this run; window resets 08:54 PT; refused after 4 calls; budget: fleet)
##[error]Process completed with exit code 1.
```

The escalation comment was written. The lane move was not. **DRE-3551 was still
in `Planning` when this was checked at 07:56 PT** — it holds a question for the
CEO in a lane where nobody is looking for one. And unlike §7.1 this run is a
red X with a raw urllib traceback, exit 1: `planning_escalation.py` has no
back-off path, so a quota exhaustion reads there as a code defect.

### 7.4 Where the fleet's hour goes — localized, not diagnosed

The run table in §5 and the spend table in §6 say most of it out loud. **The two
sweeps are the whole bill**: agent-bureau Reconcile 233 and bureau-pipeline
Reconcile 162 out of a 526 total — 75% of everything `check_linear_budget.py`
could see, from two workflows. `reconcile.yml` on agent-bureau ran **nine times
in 54 minutes** — four on the `*/15` schedule, five on `repository_dispatch`,
spending 27, 41, 48, 73, 87, 92 and 99 per run against a target of 30. Nothing
else is close: every merge sync spent 0–10, the demo and harness sweeps 0–12,
the planners 2–9.

It is not the whole story. The `check_linear_budget.py` reading taken at
07:42:46 PT for the hour *before* the proof totalled **612** across 72 runs and
**skipped 133 runs whose logs it could not read**, against roughly 2,461
requests the fleet header says were spent in that same hour. The gap is about
1,850 requests, and the candidates for it are those unread runs plus the
consumers that leave no Actions log at all — the console backend and the relay.
Which of them it is belongs on another card. Note too that a run at the floor
under-reports: `spent 48` is what that sweep *got*, not what it asked for, so
every number in these tables is a lower bound.

## 8. Did the claim hold?

**The epic's claim held and the card's first criterion did not, and they are
not the same sentence.** Draining the `operator-tools` user to zero at 07:53:54
PT caused no sweep, no merge sync and no planner run to fail — nothing in the
fleet spent an operator request all hour, and the three runs that did fail
failed on the **fleet's** own exhausted budget, which had already killed a sweep
at 07:34:08 PT, eleven minutes before the drain loop started.

Criterion by criterion:

| criterion | reading |
| -- | -- |
| zero `reconcile.yml` and zero `linear-sync.yml` runs failed on `RATELIMITED` during the hour | **NO.** Two sweeps failed, both exit 75 on `budget: fleet`: `self-reconcile.yml` run 34493863377 on bureau-pipeline at 08:10:21 PT, and `reconcile.yml` run 34495354416 on agent-bureau at 08:23:45 PT. All seven merge syncs passed. Nine further sweeps were refused mid-pass and still reported `success` |
| the PT time the `operator-tools` bucket hit `RATELIMITED`, and the last sweep of the hour | **yes** — 07:53:54 PT and 08:33:44 PT (§3, §5) |
| exit 75 and the stderr of a hand-run reconcile as the exhausted user | **yes** — §4 |
| a passing `check_linear_identities.py check` from the same hour | **yes** — §1 |
| the relay's standing | **yes** — §1 |
| `check_linear_budget.py --hours 1`: Reconcile ≤ 30/run, Linear Sync ≤ 15/run, total < 600, zero `unknown/rolled` | **NO** on the reconcile ceiling and on `unknown/rolled` — §6 |
| the same PT times posted on epic DRE-3168 | **yes** |

### What this document says is owed

1. **`planning_escalation.py` has no back-off path.** A quota exhaustion escapes
   `escalate()` as a raw urllib traceback and exit 1, so an escalation can be
   left half written — the comment posted, the lane move lost. Every other
   Linear consumer got DRE-3202's treatment; this one did not. **DRE-3551 is
   the live example and it is still in `Planning`** with a question for the CEO
   in it; putting it in the right lane is a person's decision, not this
   document's, so nothing here moved it.
2. **The two big sweeps are not frugal, and agent-bureau's is not on the cadence
   the epic assumed.** agent-bureau Reconcile and bureau-pipeline Reconcile are
   395 of the hour's 526 accounted requests; agent-bureau ran nine times in 54
   minutes, five of them `repository_dispatch`, spending up to 99 each. DRE-3236
   brought a sweep from 143 to about 20; these two are back at 27–99 and one of
   them runs three times as often as the cron says.
3. **A refused-but-`success` sweep is counted by nothing.** The catch in
   `report_epic_growth` is right — a KPI must not fail a sweep — but nothing
   downstream counts a run that carries `refused after N calls` on a green
   conclusion. The medic only looks at failures. Nine such runs happened in this
   hour and not one of them raised anything.
4. **The fleet's hour cannot be accounted for.** `check_linear_budget.py` saw
   612 of roughly 2,461 requests and skipped 133 runs whose logs it could not
   read. Until the unread runs and the consumers that leave no Actions log — the
   console backend, the relay — are in the same table, "the fleet stays inside
   its bucket" is not a measurable claim.
