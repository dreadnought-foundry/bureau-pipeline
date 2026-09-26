# Release-train decisions reach GitHub as deployment messages, but held and no-op decisions never arrive as `deployment_status` — DRE-4773 (partial)

**Two of the three runs are recorded, and one finding changes what the Record must read.** A real agent-bureau release (console `v1.6.149`, 2026-09-25 21:08 PT) and a real no-op (console outside its window, 2026-09-26 02:28 PT) each wrote their decision to GitHub. The code in the log equals the code in the deployment payload equals the code GitHub delivered. **GitHub delivered the `deployment_status` for the release but not for the no-op.** A status whose state is `inactive` is never delivered, and `docs/release-decision.md` maps both `held` and `no-op` to `inactive`. So the Record will only ever see held and no-op decisions through the `deployment` message, whose payload carries the whole decision. The held run was not provoked, because it needs a repository-variable write and a hand dispatch, which this read-only pass does not make. The Record step is not yet readable: agent-bureau began storing deployment messages at 08:08 PT today, and no train run has happened since. **This card stays open.**

Read on **2026-09-26 between 08:20 and 08:55 PT** by the operator's session: GitHub through the operator's `gh` login, App 3350400's delivery log through its own App JWT (minted locally, never printed), and agent-bureau's production database through `make db-read` (a `READ ONLY` transaction reporting `transaction_read_only = on`). Nothing was written anywhere: no variable set, no workflow dispatched, no deployment created. Every time is Pacific. UTC appears only inside commands and quoted output.

| # | step | verdict |
| --- | --- | --- |
| 1 | three real runs: released, held, no-op | **2 of 3** — released run `36216605642`, no-op run `36232844966`. **Held: not taken** (needs `RELEASE_HOLD` set and a hand dispatch) |
| 2 | each run's receipt line with its `decision recorded` line right before it | **proven** for both runs |
| 3 | the deployment in `release-train` / `release-train-decision`, and the App's `deployment_status` delivery with the same payload | **released: proven.** **No-op: the deployment is proven, and GitHub made no `deployment_status` delivery** — the finding above |
| 4 | the Record holds each as a recorded decision | **not yet readable** — read at 08:47 PT: the Record holds 2 deployment messages, both a console release's stages, and no decision |
| — | the console's own stage deployment still `active` | **proven** — `v1.6.150` and `v1.6.151` carry no `inactive` status; 95 `inactive` decisions were written after `v1.6.150` went live |
| — | no `decision not recorded: caller stub lacks deployments: write` line | **proven** across 100 runs, 48 of which carry decision lines |

---

## Preconditions — the build cards are Done and on `stable`

Read at **about 08:20 PT**.

```
python3 scripts/linear-api.py get DRE-4768   # State: Done
python3 scripts/linear-api.py get DRE-4771   # State: Done
python3 scripts/linear-api.py get DRE-4770   # State: Done
python3 scripts/linear-api.py get DRE-4761   # State: In Progress  (the sibling epic step 4 waits on)
```

| card | pull request | merge commit | merged (PT) | on `stable` (`35b74b383`) |
| --- | --- | --- | --- | --- |
| DRE-4768 `release_decision.py` | bureau-pipeline #500 | `ee4e43144` | 2026-09-24 18:23 | yes — `stable` is 77 commits ahead, 0 behind |
| DRE-4771 one decision per surface per run | bureau-pipeline #501 | `a49a231ca` | 2026-09-24 19:20 | yes — 74 ahead, 0 behind |
| DRE-4770 portico stub grants `deployments: write` | portico #709 | `8db337917` | 2026-09-24 18:34 | on portico `main`, 162 ahead, 0 behind |

```
gh api repos/dreadnought-foundry/bureau-pipeline/compare/ee4e4314486f4dac1bc56dd9fb43b52043303ab4...35b74b383fcb81633ff1660c1611f5f0532fce61
gh api repos/dreadnought-foundry/bureau-pipeline/compare/a49a231caadf9a0865459132d3002e915ef8c570...35b74b383fcb81633ff1660c1611f5f0532fce61
```

```
DRE-4768 #500 ee4e43144 -> stable ahead ahead_by 77 behind_by 0
DRE-4771 #501 a49a231ca -> stable ahead ahead_by 74 behind_by 0
stable 35b74b383 2026-09-26T05:06:09Z Merge pull request #524 from dreadnought-foundry/bot/split-ledger
```

Both runs below executed that `stable`. Their logs open with `Uses: dreadnought-foundry/bureau-pipeline/.github/workflows/release-train.yml@refs/tags/stable (35b74b383fcb81633ff1660c1611f5f0532fce61)` and `pipeline_ref: stable`. The first agent-bureau run to print a `decision recorded` line was `36093449279`, at 2026-09-24 21:11 PT, about two hours after DRE-4771 merged.

---

## Step 1 — the runs

```
gh run list -R dreadnought-foundry/agent-bureau --workflow release-train.yml --limit 100 \
  --json databaseId,createdAt,event,conclusion,headSha
gh run view <id> -R dreadnought-foundry/agent-bureau --log      # for each of the 100
```

The 100 runs span 2026-09-23 09:39 PT to 2026-09-26 06:15 PT. None has run since. 48 carry decision lines: 3 `(success)`, 136 `(inactive)`, 0 `(failure)`. None carries `decision not recorded`. None was held.

| kind | run | started (PT) | event | surface | code |
| --- | --- | --- | --- | --- | --- |
| released | [`36216605642`](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36216605642) | 2026-09-25 21:01:55 | `workflow_dispatch` | console | `released` |
| no-op | [`36232844966`](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36232844966) | 2026-09-26 02:27:47 | `workflow_run` | console | `window` |
| held | **not taken** | — | — | — | — |

**Why no held run.** The card's method provokes one: set `RELEASE_HOLD` on agent-bureau, dispatch `release-train.yml` by hand, watch every surface exit `held`, delete the variable. That is a variable write and a workflow dispatch on production. This pass was scoped to reads, so it did neither. No held run happened on its own in the 100 runs read.

## Step 2 — the receipt and decision lines

**Released, run `36216605642`, job `Release console`** (the decision line comes right before the receipt):

```
2026-09-26T04:08:35.6587376Z release-train: [console] ==> release: console at e735a87ee would become agent-bureau-console-v1.6.149 (last released: agent-bureau-console-v1.6.148)
2026-09-26T04:08:35.6761380Z release-train: [console]     completedAt: 2026-09-26T04:08:33.576Z  stage: Released
2026-09-26T04:08:35.6785531Z release-train: [console] decision recorded: deployment 6674469130 (success)
2026-09-26T04:08:35.6786256Z release-train: released dreadnought-foundry/agent-bureau console as agent-bureau-console-v1.6.149 at e735a87
```

That is 21:08:35 PT. The tag the run cut, `agent-bureau-console-v1.6.149`, points at `e735a87ee`.

**No-op, run `36232844966`, job `Plan the surfaces`:**

```
2026-09-26T09:28:07.5458737Z release-train: [console] decision recorded: deployment 6677155740 (inactive)
2026-09-26T09:28:07.5462681Z release-train: no-op dreadnought-foundry/agent-bureau console — the clock reads 02:28 PT and console's window is 05:00-00:00 PT (its own, overriding the fleet default 05:00-21:00 PT) — this defers to 05:00 PT — not re-armed: 05:00 PT is more than the 32-minute wait a re-armed run may hold a runner for — the next CI completion on the default branch or the fleet wake-up at 05:00 PT wakes the train
```

That is 02:28:07 PT. The same run also recorded `website` (`6677155878`, `current`) and `relay` (`6677155984`, `current`), one decision per surface.

## Step 3 — what GitHub holds and what it delivered

### The deployments

```
gh api repos/dreadnought-foundry/agent-bureau/deployments/<id>
gh api repos/dreadnought-foundry/agent-bureau/deployments/<id>/statuses
```

```
== deployment 6674469130  env=release-train task=release-train-decision created_at=2026-09-26T04:08:35Z sha=e735a87ee creator=github-actions[bot]
   payload: {"schema": "release-train-decision/1", "repo": "dreadnought-foundry/agent-bureau", "surface": "console", "act": "release", "code": "released", "reason": "console released at e735a87 as agent-bureau-console-v1.6.149", "phase": "release", "sha": "e735a87ee306fa89f978025add6ffda5cb4010e2", "head": "a7fa3aefede4a97c7b52a426a8aa5fb283733753", "deployed": "agent-bureau-console-v1.6.148", "version": "agent-bureau-console-v1.6.149", "hand_act": null, "re_arm_at": null, "run_id": 36216605642, "run_attempt": 1, "run_url": "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36216605642", "event": "workflow_dispatch", "decided_at": "2026-09-26T04:03:04Z"}
   status 18865344886 success 2026-09-26T04:08:35Z [release-train-decision/1 release released console]
== deployment 6677155740  env=release-train task=release-train-decision created_at=2026-09-26T09:28:05Z sha=fea81c8b6 creator=github-actions[bot]
   payload: {"schema": "release-train-decision/1", "repo": "dreadnought-foundry/agent-bureau", "surface": "console", "act": "no-op", "code": "window", "reason": "the clock reads 02:28 PT and console's window is 05:00-00:00 PT (its own, overriding the fleet default 05:00-21:00 PT) — this defers to 05:00 PT — not re-armed: 05:00 PT is more than the 32-minute wait a re-armed run may hold a runner for — the next CI completion on the default branch or the fleet wake-up at 05:00 PT wakes the train", "phase": "plan", "sha": "fea81c8b64fc85031415e959e9136793b1deca43", "head": "fea81c8b64fc85031415e959e9136793b1deca43", "deployed": "agent-bureau-console-v1.6.150", "version": null, "hand_act": null, "re_arm_at": "2026-09-26T12:00:00Z", "run_id": 36232844966, "run_attempt": 1, "run_url": "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36232844966", "event": "workflow_run", "decided_at": "2026-09-26T09:28:05Z"}
   status 18871407793 inactive 2026-09-26T09:28:06Z [release-train-decision/1 no-op window console]
```

Released: decided 21:03:04 PT, deployment and status at 21:08:35 PT. No-op: decided 02:28:05 PT, deployment 02:28:05 PT, status 02:28:06 PT. The released payload's `version` is `agent-bureau-console-v1.6.149`, the tag the run cut.

### The deliveries

App 3350400 (`agent-bureau-bot`) subscribes to both events:

```
GET /app  (App JWT)
app 3350400 agent-bureau-bot
events: check_run, check_suite, create, delete, deployment, deployment_status, issue_comment, pull_request, pull_request_review, push, workflow_job, workflow_run
```

Its delivery log, which covers every repo the App is installed on, was walked from about 08:45 PT back to 2026-09-25 20:59 PT: 35,700 deliveries, **134 `deployment` and 39 `deployment_status`**, every one `200 OK`, none a redelivery.

```
GET /app/hook/deliveries?per_page=100&cursor=<next>      # every page, kept: event in (deployment, deployment_status)
GET /app/hook/deliveries/<id>                            # the body of each one quoted below
```

**Released.** Both messages were delivered, and each carries the payload the run recorded, byte for byte on every field:

| delivery | delivered (PT) | event | deployment | status |
| --- | --- | --- | --- | --- |
| `3844845523915776000` (GUID `f19d0ff0-b95f-11f1-8d61-1e23a943a9ad`) | 21:08:36.175 | `deployment` `created` | `6674469130`, `release-train` / `release-train-decision` | — |
| `3844845524620435458` (GUID `f1d42580-b95f-11f1-81ed-9a72d8244a6a`) | 21:08:36.527 | `deployment_status` `created` | `6674469130` | `success` "release-train-decision/1 release released console" |

**No-op.** The `deployment` was delivered with the full payload. **No `deployment_status` was delivered:**

| delivery | delivered (PT) | event | deployment |
| --- | --- | --- | --- |
| `3844886692391550976` (GUID `942c7ea0-b98c-11f1-9b44-8ea471dbaa8b`) | 02:28:06.783 | `deployment` `created` | `6677155740` (console, `window`) |
| `3844886694478217220` | 02:28:07.742 | `deployment` `created` | `6677155878` (website) |
| `3844886695474364418` | 02:28:08.177 | `deployment` `created` | `6677155984` (relay) |
| — | — | `deployment_status` | **none, for any of the three** |

**This is the pattern, not a lost message.** The 06:15 PT run shows the same thing on agent-bureau's own console stage deployment `6679294767`. Its `in_progress` "cut" status was delivered (`3844916081500110848`, 06:16:12 PT). Its `inactive` "deferred" status one second later was not. Every one of the 39 status deliveries was opened and read. **Not one is `inactive`:**

```
(deployment.task, deployment_status.state)   deliveries
('release', 'in_progress')                   32
('release', 'success')                       4
('release-train-decision', 'success')        3
```

In the same hours agent-bureau's train alone wrote 98 `inactive` decision statuses (counted from its run logs), and its release script wrote 19 `inactive` "deferred" stage statuses between 05:01 and 06:16 PT.

**What that means for the card and for the Record.**

- The card asks for "the App's `deployment_status` delivery carrying the same payload" for all three runs. **That cannot happen for the held run or the no-op**, because `docs/release-decision.md` maps both to `inactive`. The criterion needs rewording to "the `deployment` delivery", which does carry the whole payload, or the decision states need to change.
- **For DRE-4761:** a Record that files decisions from `deployment_status` will never see a held or a no-op. It has to read the decision from the `deployment` message. The `deployment` alone carries `act`, `code`, `reason`, `hand_act` and `decided_at`.
- The card warned that a delivery GitHub made and the Record dropped must be recorded as a gap, never as "no decision". This is a third case: **a message GitHub never sends.** It is not a gap in the Record, and an operator looking for it on the Recent Deliveries page will not find it.

### Code equals code equals code

- **Released:** the log says `released`, the deployment payload says `"code": "released"`, and both delivered messages say `"code": "released"`. Equal.
- **No-op:** the log says `window` ("the clock reads 02:28 PT and console's window is 05:00-00:00 PT"), the deployment payload says `"code": "window"`, and the delivered `deployment` says `"code": "window"`. Equal. There is no delivered status to compare.
- **Held:** not taken.

## Step 4 — the Record

**Not yet readable.** agent-bureau's Record began accepting `deployment` and `deployment_status` with DRE-4785 (agent-bureau #2811), merged 2026-09-26 04:14 PT and live in `agent-bureau-console-v1.6.151` at **08:08:37 PT today**. The two runs above happened before that, and no train run has happened since. Read at **08:47 PT**:

```
make db-read SQL=<file>      # agent-bureau, the read-only door
```

```sql
SELECT event, action, count(*) AS messages,
       min(received_at) AS first_received, max(received_at) AS last_received
  FROM event_record
 WHERE event IN ('deployment', 'deployment_status')
 GROUP BY event, action
 ORDER BY event, action;
```

```
transaction_read_only = on
event	action	messages	first_received	last_received
deployment_status	created	2	2026-09-26T15:08:33Z	2026-09-26T15:08:38Z
(1 rows)
```

Both are console `v1.6.151`'s own stage statuses ("roll out" at 08:08:32 PT and "live" at 08:08:37 PT, deployment `6680351271`). **The Record holds no decision message yet.** That is expected, not a gap: GitHub made no decision delivery after 08:08 PT. The next train run that reaches its plan job will be the first one the Record can hold. When the operator reads it, that date goes in this section.

## The console's own stage deployment is still `active`

**Proven.** After `v1.6.150` went live, the train recorded 95 `inactive` decisions, and neither release's own deployment in environment `console` gained an `inactive` status:

```
== deployment 6674531315  env=console task=release created_at=2026-09-26T04:16:13Z sha=8003e072f creator=smeed652
   status 18865640690 success 2026-09-26T04:24:49Z [live]
   status 18865639350 in_progress 2026-09-26T04:24:44Z [roll out]
   status 18865486692 in_progress 2026-09-26T04:16:14Z [build]
   status 18865486508 in_progress 2026-09-26T04:16:13Z [cut]
== deployment 6680351271  env=console task=release created_at=2026-09-26T14:59:01Z sha=9410b5e23 creator=smeed652
   status 18878775402 success 2026-09-26T15:08:37Z [live]
   status 18878773454 in_progress 2026-09-26T15:08:32Z [roll out]
   status 18878548099 in_progress 2026-09-26T14:59:01Z [build]
   status 18878547948 in_progress 2026-09-26T14:59:01Z [cut]
```

`v1.6.150` (live 21:24:49 PT) and `v1.6.151` (live 08:08:37 PT) both end on `success`. `auto_inactive: false` held for every decision in between.

---

## What is not proven, and why

- **The held run.** Not provoked. It needs `gh variable set RELEASE_HOLD` on agent-bureau and a hand `gh workflow run release-train.yml`, both writes to production, and then `gh variable delete RELEASE_HOLD --repo dreadnought-foundry/agent-bureau` to clear it. The operator can run it at any time inside the console's window (05:00–00:00 PT). Three things will follow from it, and none can be taken before: its log lines, its `hand_act` compared with the clearing command actually used, and its `deployment` delivery. Given the finding above, it will produce no `deployment_status` delivery.
- **The `deployment_status` delivery for held and no-op decisions.** It will never exist while those decisions are `inactive`. The criterion needs a decision: read the `deployment` delivery instead, or change the state.
- **Step 4, the Record.** Not readable until a train run happens after 08:08 PT today, and until DRE-4761 files decisions as decisions. Today it only indexes them in `event_record`, and `record_releases` deliberately ignores `release-train-decision`.
- **Both runs predate the Record's recording,** so neither can ever appear in it. Step 4 will need three new runs, or a redelivery from the App's log within its three-day window. The released run's delivery ages out of that window around 2026-09-28 21:08 PT.
- **The working files are not in this repo.** The scan script, the 100 run logs and the delivery listing are in the operator's session scratch space. Every command is quoted so a reader can rebuild them.
