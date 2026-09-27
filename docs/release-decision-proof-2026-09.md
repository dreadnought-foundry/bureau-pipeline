# A released, a held and a no-op release-train decision each reach GitHub and the Record — DRE-4773

The proof for [DRE-4773](https://linear.app/dreadnoughtfoundry/issue/DRE-4773),
under epic [DRE-4762](https://linear.app/dreadnoughtfoundry/issue/DRE-4762).
Every time below is Pacific (PDT, UTC−7). UTC appears only inside commands and quoted output.

**All three kinds of decision are now observed end to end, and the Record holds each one.** On 2026-09-27 three real agent-bureau release-train runs made the three decisions the card names. A scheduled run found nothing new to release (06:16 PT, `current`). A run released console `agent-bureau-console-v1.6.165` (07:17 PT). A run the operator provoked with the brake was held on every surface (08:04 PT). Each one wrote its decision line to the log, a deployment in environment `release-train`, and a row in the Record. The code in the log equals the code in the deployment payload equals the code in the Record, for all three. The held run's `hand_act` names the command the operator then used to clear the brake. The released run's `version` is the tag the run cut. Console's own release record stayed `success` after the four held decisions landed on top of it.

**One wording in the card cannot be met, and it stays named here.** The card asks for "the App's `deployment_status` delivery carrying the same payload" for all three runs. For held and no-op decisions that delivery never happens. Both are written with status `inactive`, and in the 2026-09-26 read of App 3350400's delivery log, none of the 39 status deliveries was `inactive` (details in the 2026-09-26 section below). That was observed for a no-op. For the held run it is still inferred: today's four held decisions each carry one `inactive` status, which was read from GitHub, but the App's delivery log was not re-read today (see "How it was read"). The Record does not need that delivery anyway. It reads each decision from the `deployment` message, whose payload carries the whole decision, and that is how all twelve of today's rows below reached it.

## How it was read

**2026-09-27, the held run and the Record.** On the CEO's explicit go ("Do the release bake 4773", about 08:03 PT), the operator's session provoked the held run in production. It set the brake at 08:03:56 PT, dispatched the train by hand, and cleared the brake at 08:04:54 PT, so the brake stood for 58 seconds. At about 08:07 PT it read the Record in agent-bureau's production database through `make db-read`, the read-only door. Between 08:08 and 08:15 PT the session writing this record re-read every GitHub object quoted below through the operator's `gh` login: the three runs' logs, the decision deployments and their statuses, console's release deployment, and the tag. It also read the card and its parent epic from Linear. Those were read-only calls. It did **not** re-read App 3350400's delivery log. That read needs an App JWT minted from the App's private key, and the session's permission guard refused the key read, so no delivery for today's runs is quoted here. Apart from the brake the CEO approved, nothing was written: no deployment was created by hand, and nothing was redelivered.

**2026-09-26, the first pass.** Between 08:20 and 08:55 PT the operator's session read GitHub through the operator's `gh` login, read App 3350400's delivery log through its own App JWT (minted locally, never printed), and read agent-bureau's production database through `make db-read` (a `READ ONLY` transaction reporting `transaction_read_only = on`). Nothing was written. That pass recorded the 09-25 release and the 09-26 no-op. Its sections are kept below, apart from the edits listed at the start of that section.

## Which runs satisfy which criterion

| # | criterion | verdict | rests on |
| --- | --- | --- | --- |
| 1a | three real runs: released, held (provoked with `RELEASE_HOLD`, then cleared), no-op | **proven** | released [`36324293496`](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36324293496) (09-27 07:17 PT) and [`36216605642`](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36216605642) (09-25 21:08 PT); held [`36328177968`](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36328177968) (09-27 08:04 PT); no-op [`36321812820`](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36321812820) (09-27 06:16 PT, `current`) and [`36232844966`](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36232844966) (09-26 02:28 PT, `window`) |
| 1b | each run's receipt line with its `decision recorded: deployment <id>` line | **proven** for all five runs | the log excerpts below |
| 1c | the deployment GitHub holds in environment `release-train` | **proven** for all five runs | today's three runs made 12 decision deployments in `release-train`, listed in the Record section. Each run's console decision is quoted in full, and so are the two 09-25/26 decisions |
| 1d | the App's `deployment_status` delivery carrying the same payload | **released: proven** on the 09-25 run (delivery log, byte for byte). Not re-read for today's release. **Held and no-op: cannot be met.** | The status is `inactive`, and an `inactive` status was never delivered in the 09-26 read. That is observed for the 09-26 no-op and inferred for the held run. The decision reaches the Record through the `deployment` message instead |
| 2 | the held run's `hand_act` is the command actually used; the released run's `version` is the tag the run cut | **proven** | held `36328177968`: the first clause of `hand_act` is the command used, word for word. Released `36324293496`: `version` = `agent-bureau-console-v1.6.165`, the tag the run pushed, on `03e082e39`. The 09-25 run matches the same way (`v1.6.149`) |
| 3 | each is read from the Record in production with a PT time, the date of the read named, and any delivery the Record lacks recorded as a gap | **proven**, read **2026-09-27 at about 08:07 PT** | released `6693321502` (07:17:23 PT), held `6693834152`/`286`/`451`/`618` (08:04:23–26 PT), no-op `6692682060`/`145`/`251`/`347` (06:16:33–35 PT). The 12 newest decisions GitHub holds are exactly the 12 rows the Record returned: **no gap** |
| 4 | console's own last stage deployment still `active` after the decisions were recorded | **proven** by the held run | console release `6693267619` (`v1.6.165`) ends on `success` at 07:17:20 PT. The four held `inactive` decisions came 47 minutes later, and it gained no `inactive` status |
| 5 | the record is merged to `main`, and the CEO closes the card after reading it | **pending** | this pull request. The card carries `no-code`, so merging does not close it automatically |
| — | no `decision not recorded: caller stub lacks deployments: write` line | **proven** | none in today's three logs; none in the 100 runs read on 09-26 |

---

## 2026-09-27 — the three runs of the day

All three runs executed `stable` at `5aaa5ac1de` (bureau-pipeline #535, committed 2026-09-26 23:31 PT), 105 commits ahead of DRE-4771's merge and 0 behind. Each log opens with `Uses: dreadnought-foundry/bureau-pipeline/.github/workflows/release-train.yml@refs/tags/stable (5aaa5ac1de73117528c79dfd07f98de9d0ed6ee0)`.

```
gh run view <id> -R dreadnought-foundry/agent-bureau --json databaseId,createdAt,event,conclusion,headSha,jobs
gh run view <id> -R dreadnought-foundry/agent-bureau --log | grep 'release-train:'
gh api repos/dreadnought-foundry/agent-bureau/deployments/<id> --jq .payload
gh api repos/dreadnought-foundry/agent-bureau/deployments/<id>/statuses
```

| kind | run | started (PT) | event | jobs | decisions |
| --- | --- | --- | --- | --- | --- |
| no-op | [`36321812820`](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36321812820) | 06:16:15 | `schedule` | Plan the surfaces: success. Wait and Release: skipped | 4 × `current` |
| released | [`36324293496`](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36324293496) | 06:59:23 | `workflow_run` | Plan the surfaces: success. Wait: skipped. Release console: success (07:12:08–07:17:25) | console `released` + 3 × `current` |
| held | [`36328177968`](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36328177968) | 08:03:59 | `workflow_dispatch` | Plan the surfaces: success. Wait and Release: skipped | 4 × `held` |

### The held run — provoked by hand, on the CEO's go

The CEO gave the go at about 08:03 PT ("Do the release bake 4773"). The operator's session then ran:

```
08:03:56 PT  gh variable set RELEASE_HOLD -R dreadnought-foundry/agent-bureau --body "2026-09-27"
             gh workflow run release-train.yml -R dreadnought-foundry/agent-bureau      # → run 36328177968
08:04:54 PT  gh variable delete RELEASE_HOLD --repo dreadnought-foundry/agent-bureau
             gh variable list                                                            # repository scope only: no RELEASE_HOLD left
```

The brake stood for 58 seconds. Every surface exited `held`, and the Release job was skipped. The log's decision lines and receipts (08:04:25 PT):

```
2026-09-27T15:04:25.9861303Z release-train: [console] decision recorded: deployment 6693834152 (inactive)
2026-09-27T15:04:25.9867100Z release-train: held dreadnought-foundry/agent-bureau console — the fleet brake RELEASE_HOLD is set (set 2026-09-27) — console releases nothing until it is cleared
2026-09-27T15:04:25.9868674Z release-train: [website] decision recorded: deployment 6693834286 (inactive)
2026-09-27T15:04:25.9879007Z release-train: held dreadnought-foundry/agent-bureau website — the fleet brake RELEASE_HOLD is set (set 2026-09-27) — website releases nothing until it is cleared
2026-09-27T15:04:25.9880621Z release-train: [relay] decision recorded: deployment 6693834451 (inactive)
2026-09-27T15:04:25.9882380Z release-train: held dreadnought-foundry/agent-bureau relay — the fleet brake RELEASE_HOLD is set (set 2026-09-27) — relay releases nothing until it is cleared
2026-09-27T15:04:25.9883997Z release-train: [relay-gh] decision recorded: deployment 6693834618 (inactive)
2026-09-27T15:04:25.9885835Z release-train: held dreadnought-foundry/agent-bureau relay-gh — the fleet brake RELEASE_HOLD is set (set 2026-09-27) — relay-gh releases nothing until it is cleared
```

The console decision, as GitHub holds it:

```
== deployment 6693834152  env=release-train task=release-train-decision created_at=2026-09-27T15:04:22Z sha=03e082e39 creator=github-actions[bot]
   payload: {"act":"held","code":"held","decided_at":"2026-09-27T15:04:19Z","deployed":"agent-bureau-console-v1.6.165","event":"workflow_dispatch","hand_act":"gh variable delete RELEASE_HOLD --repo dreadnought-foundry/agent-bureau — or, when the fleet-wide brake is the one that is set, gh variable delete RELEASE_HOLD --org dreadnought-foundry","head":"03e082e399b364158c7cb087bdc5def05067c03b","phase":"plan","re_arm_at":null,"reason":"the fleet brake RELEASE_HOLD is set (set 2026-09-27) — console releases nothing until it is cleared","repo":"dreadnought-foundry/agent-bureau","run_attempt":1,"run_id":36328177968,"run_url":"https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36328177968","schema":"release-train-decision/1","sha":"03e082e399b364158c7cb087bdc5def05067c03b","surface":"console","version":null}
   status 18908594345 inactive 2026-09-27T15:04:22Z [release-train-decision/1 held held console]
```

The other three have the same shape. Each one's `surface`, `deployed` and `reason` name its own surface, and each has one `inactive` status:

```
6693834286  created 15:04:23Z  surface=website   deployed=agent-bureau-website-v1.0.4  code=held  status 18908594720 inactive 15:04:23Z
6693834451  created 15:04:24Z  surface=relay     deployed=agent-bureau-relay-v14       code=held  status 18908595084 inactive 15:04:24Z
6693834618  created 15:04:25Z  surface=relay-gh  deployed=agent-bureau-relay-gh-v4     code=held  status 18908595453 inactive 15:04:25Z
```

The train decided at 08:04:19 PT (`decided_at`), inside the 58 seconds the brake stood.

**The `hand_act` against the command actually used.** The recorded `hand_act` has two clauses:

> `gh variable delete RELEASE_HOLD --repo dreadnought-foundry/agent-bureau` — or, when the fleet-wide brake is the one that is set, `gh variable delete RELEASE_HOLD --org dreadnought-foundry`

The operator cleared the brake with `gh variable delete RELEASE_HOLD --repo dreadnought-foundry/agent-bureau`. That is the first clause, word for word. The second clause covers an org-level brake. The org scope was not read: `gh variable list` without `--org` lists repository variables only, and reading organization variables returns `HTTP 403: Resource not accessible by integration` to the token that checked this record. What rules out an org-level brake is the day's earlier runs. The 06:08, 06:16 and 06:59 PT runs were not held, and the released run's last decision came at 07:12:21 PT (`decided_at`). An org-level brake set before then would have held them. So the message told the reader the right command for the brake that was actually set. One case this does not exclude: an org-level brake set in the 52 minutes between 07:12 PT and the operator's brake at 08:03:56 PT. No train run after 08:04:54 PT was read for this record, so nothing here shows a run released unheld once the repository brake was gone.

### The released run

The plan job recorded the three surfaces it did not release (06:59:47 PT):

```
2026-09-27T13:59:47.4492076Z release-train: [website] decision recorded: deployment 6693129468 (inactive)
2026-09-27T13:59:47.4493872Z release-train: no-op dreadnought-foundry/agent-bureau website — website reads current: nothing under website/ has changed since its newest tag
2026-09-27T13:59:47.4508816Z release-train: [relay] decision recorded: deployment 6693129664 (inactive)
2026-09-27T13:59:47.4511144Z release-train: no-op dreadnought-foundry/agent-bureau relay — relay reads current: nothing under cloud/relay/ has changed since its newest tag
2026-09-27T13:59:47.4512817Z release-train: [relay-gh] decision recorded: deployment 6693129849 (inactive)
2026-09-27T13:59:47.4515445Z release-train: no-op dreadnought-foundry/agent-bureau relay-gh — relay-gh reads current: nothing under cloud/relay-gh/ has changed since its newest tag
```

The `Release console` job cut and pushed the tag, then recorded the decision right before its receipt (07:17:22 PT):

```
2026-09-27T14:17:22.6650825Z release-train: [console] ==> release: console at 03e082e39 would become agent-bureau-console-v1.6.165 (last released: agent-bureau-console-v1.6.164)
2026-09-27T14:17:22.6762356Z release-train: [console] ==> DONE -- version agent-bureau-console-v1.6.165 (sha 03e082e39) deployed at 2026-09-27T14:17:18Z
2026-09-27T14:17:22.6769752Z release-train: [console]  * [new tag]             agent-bureau-console-v1.6.165 -> agent-bureau-console-v1.6.165
2026-09-27T14:17:22.6770481Z release-train: [console] decision recorded: deployment 6693321502 (success)
2026-09-27T14:17:22.6771395Z release-train: released dreadnought-foundry/agent-bureau console as agent-bureau-console-v1.6.165 at 03e082e
```

```
== deployment 6693321502  env=release-train task=release-train-decision created_at=2026-09-27T14:17:22Z sha=03e082e39 creator=github-actions[bot]
   payload: {"act":"release","code":"released","decided_at":"2026-09-27T14:12:21Z","deployed":"agent-bureau-console-v1.6.164","event":"workflow_run","hand_act":null,"head":"03e082e399b364158c7cb087bdc5def05067c03b","phase":"release","re_arm_at":null,"reason":"console released at 03e082e as agent-bureau-console-v1.6.165","repo":"dreadnought-foundry/agent-bureau","run_attempt":1,"run_id":36324293496,"run_url":"https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36324293496","schema":"release-train-decision/1","sha":"03e082e399b364158c7cb087bdc5def05067c03b","surface":"console","version":"agent-bureau-console-v1.6.165"}
   status 18907459843 success 2026-09-27T14:17:22Z [release-train-decision/1 release released console]
```

**The `version` against the tag the run cut.** `agent-bureau-console-v1.6.165` is an annotated tag object (`7b0d55c51`). Its target is commit `03e082e39` and its tagger time is 07:17:18 PT. That matches the log's `[new tag]` line, the payload's `sha` and its `version`. The train decided at 07:12:21 PT, and the decision was recorded once the release was live, at 07:17:22 PT.

```
gh api repos/dreadnought-foundry/agent-bureau/git/ref/tags/agent-bureau-console-v1.6.165 --jq .object
gh api repos/dreadnought-foundry/agent-bureau/git/tags/7b0d55c51a33b1167b331712d3d9bbc92ff262c4
agent-bureau-console-v1.6.165 -> 03e082e39 commit tagger=2026-09-27T14:17:18Z
```

### The no-op run

A scheduled run found nothing new on any surface (06:16:35 PT):

```
2026-09-27T13:16:35.0467693Z release-train: [console] decision recorded: deployment 6692682060 (inactive)
2026-09-27T13:16:35.0469236Z release-train: no-op dreadnought-foundry/agent-bureau console — console reads current: nothing under console/, infra/ has changed since its newest tag
2026-09-27T13:16:35.0470318Z release-train: [website] decision recorded: deployment 6692682145 (inactive)
2026-09-27T13:16:35.0471428Z release-train: no-op dreadnought-foundry/agent-bureau website — website reads current: nothing under website/ has changed since its newest tag
2026-09-27T13:16:35.0472430Z release-train: [relay] decision recorded: deployment 6692682251 (inactive)
2026-09-27T13:16:35.0473506Z release-train: no-op dreadnought-foundry/agent-bureau relay — relay reads current: nothing under cloud/relay/ has changed since its newest tag
2026-09-27T13:16:35.0474509Z release-train: [relay-gh] decision recorded: deployment 6692682347 (inactive)
2026-09-27T13:16:35.0475695Z release-train: no-op dreadnought-foundry/agent-bureau relay-gh — relay-gh reads current: nothing under cloud/relay-gh/ has changed since its newest tag
```

```
== deployment 6692682060  env=release-train task=release-train-decision created_at=2026-09-27T13:16:32Z sha=a5bf7b3ef creator=github-actions[bot]
   payload: {"act":"no-op","code":"current","decided_at":"2026-09-27T13:16:32Z","deployed":"agent-bureau-console-v1.6.164","event":"schedule","hand_act":null,"head":"a5bf7b3efa33667cd4ea7269f500a46e50b18094","phase":"plan","re_arm_at":null,"reason":"console reads current: nothing under console/, infra/ has changed since its newest tag","repo":"dreadnought-foundry/agent-bureau","run_attempt":1,"run_id":36321812820,"run_url":"https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36321812820","schema":"release-train-decision/1","sha":"a5bf7b3efa33667cd4ea7269f500a46e50b18094","surface":"console","version":null}
   status 18906041172 inactive 2026-09-27T13:16:32Z [release-train-decision/1 no-op current console]
```

### The Record — read 2026-09-27 at about 08:07 PT

Read by the operator's session in agent-bureau (read-only):

```
AWS_PROFILE=dreadnought make db-read SQL=<file>
```

```sql
SELECT subject_id, received_at, left(payload_trim::text, 900) AS payload
  FROM event_record
 WHERE source = 'github' AND event = 'deployment'
   AND payload_trim::text LIKE '%release-train-decision%'
   AND received_at >= '2026-09-27T13:00:00Z'
 ORDER BY received_at DESC
 LIMIT 12;
```

The rows, parsed (subject_id is the deployment id; received_at is UTC as stored):

```
6693834618 2026-09-27T15:04:26Z held     relay-gh  -                              36328177968  gh variable delete RELEASE_HOLD --repo dreadnought-foundry/…
6693834451 2026-09-27T15:04:25Z held     relay     -                              36328177968  (same)
6693834286 2026-09-27T15:04:24Z held     website   -                              36328177968  (same)
6693834152 2026-09-27T15:04:23Z held     console   -                              36328177968  (same)
6693321502 2026-09-27T14:17:23Z released console   agent-bureau-console-v1.6.165  36324293496  null
6693129849 2026-09-27T13:59:47Z current  relay-gh  null                           36324293496
6693129664 2026-09-27T13:59:46Z current  relay     null                           36324293496
6693129468 2026-09-27T13:59:45Z current  website   null                           36324293496
6692682347..6692682060 2026-09-27T13:16:33–35Z current (console, website, relay, relay-gh) null 36321812820
```

(The last line is a hand-written summary of four rows, not parsed output: one `current` row per surface from run `36321812820`, received between 13:16:33Z and 13:16:35Z.)

| kind | run | Record row | recorded (PT) | run's own time in the log (PT) |
| --- | --- | --- | --- | --- |
| released | `36324293496` | `6693321502`: console, `released`, `agent-bureau-console-v1.6.165` | 07:17:23 | 07:17:22 |
| held | `36328177968` | `6693834152`: console, `held`, `hand_act` = the clearing command | 08:04:23 | 08:04:25 |
| no-op | `36321812820` | `6692682060`: console, `current` | 06:16:33 | 06:16:35 |

(The held and no-op rows reach the Record a second or two before the log line that reports them. In the plan job, all four decision lines carry one timestamp, taken after the last of its four deployments was created: 15:04:25.98Z against deployments created 15:04:22–25Z.)

GitHub's own list of decision deployments, newest first, read at about 08:10 PT:

```
gh api 'repos/dreadnought-foundry/agent-bureau/deployments?environment=release-train&per_page=16' \
  --jq '.[]|"\(.id) \(.created_at) \(.payload.surface) \(.payload.code) \(.payload.run_id)"'
6693834618 2026-09-27T15:04:25Z relay-gh held 36328177968
6693834451 2026-09-27T15:04:24Z relay held 36328177968
6693834286 2026-09-27T15:04:23Z website held 36328177968
6693834152 2026-09-27T15:04:22Z console held 36328177968
6693321502 2026-09-27T14:17:22Z console released 36324293496
6693129849 2026-09-27T13:59:46Z relay-gh current 36324293496
6693129664 2026-09-27T13:59:45Z relay current 36324293496
6693129468 2026-09-27T13:59:44Z website current 36324293496
6692682347 2026-09-27T13:16:34Z relay-gh current 36321812820
6692682251 2026-09-27T13:16:34Z relay current 36321812820
6692682145 2026-09-27T13:16:33Z website current 36321812820
6692682060 2026-09-27T13:16:32Z console current 36321812820
6692600849 2026-09-27T13:08:39Z relay-gh current 36321348313
6692600689 2026-09-27T13:08:38Z relay current 36321348313
6692600543 2026-09-27T13:08:37Z website current 36321348313
6692600404 2026-09-27T13:08:37Z console current 36321348313
```

**Twelve created, twelve held — no gap.** GitHub's twelve newest decision deployments in `release-train` are the four held (`6693834152`, `…286`, `…451`, `…618`), the release and three `current` from the released run (`6693321502`, `6693129468`, `…664`, `…849`), and four `current` from the no-op run (`6692682060`, `…145`, `…251`, `…347`). The Record returned exactly those twelve ids. The query's time window also holds four earlier decisions, from run `36321348313` at 06:08 PT. They fall outside `LIMIT 12`, so their absence from these rows is not a gap. Whether the Record holds them was not checked.

**How the rows got there.** The Record files these as `deployment` messages. They apply on Postgres since the release-history fix DRE-4976 (2026-09-26 18:14 PT). Nine earlier messages were redriven between 19:55 and 20:01 PT that evening. As the 2026-09-26 read found, held and no-op decisions produce no delivered `deployment_status`, so the Record reads each decision from the `deployment` message itself. Production's Train Yard tab, live since console `v1.6.165` (07:17 PT today), shows these decisions.

### Code equals code equals code

- **Released (`36324293496`):** the log says `released … as agent-bureau-console-v1.6.165`, the payload says `"code":"released"`, and the Record row says `released`. Equal.
- **Held (`36328177968`):** the log says `held` on all four surfaces, the four payloads say `"code":"held"`, and the four Record rows say `held`. Equal.
- **No-op (`36321812820`):** the log says `no-op … reads current` on all four surfaces, the four payloads say `"code":"current"`, and the four Record rows say `current`. Equal.

### The console's own release record is still live

Console's own release deployment for `v1.6.165`, in environment `console`:

```
gh api repos/dreadnought-foundry/agent-bureau/deployments/6693267619/statuses
== deployment 6693267619  env=console task=release created_at=2026-09-27T14:12:24Z sha=03e082e39 creator=github-actions[bot]
   status 18907459025 success 2026-09-27T14:17:20Z [live]
   status 18907438846 in_progress 2026-09-27T14:16:30Z [verify]
   status 18907368895 in_progress 2026-09-27T14:13:35Z [roll out]
   status 18907341590 in_progress 2026-09-27T14:12:25Z [build]
   status 18907341388 in_progress 2026-09-27T14:12:24Z [cut]
```

It went live at 07:17:20 PT. The four held decisions were written at 08:04:22–25 PT, and each carries an `inactive` status. When this record was written it was still the newest deployment in environment `console`, and its latest status was still `success`. **No decision inactivated it.** The held run is the one that proves this, because its decisions came after the release. The no-op run's decisions (06:16 PT) came before it.

---

## 2026-09-26 — the first pass (the 09-25 release and the 09-26 no-op)

Kept as written on 2026-09-26, apart from three edits. The headings moved down one level. In Step 4, "about 08:08 PT on 2026-09-26" replaces an exact time, a note adds that the first stored message arrived at 08:08:33 PT, and a closing note points to the 2026-09-27 read of the Record. The old "What is not proven" list is replaced by the final section of this record.

### Preconditions — the build cards are Done and on `stable`

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

### Step 1 — the runs

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

### Step 2 — the receipt and decision lines

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

### Step 3 — what GitHub holds and what it delivered

#### The deployments

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

#### The deliveries

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

#### Code equals code equals code

- **Released:** the log says `released`, the deployment payload says `"code": "released"`, and both delivered messages say `"code": "released"`. Equal.
- **No-op:** the log says `window` ("the clock reads 02:28 PT and console's window is 05:00-00:00 PT"), the deployment payload says `"code": "window"`, and the delivered `deployment` says `"code": "window"`. Equal. There is no delivered status to compare.
- **Held:** not taken.

### Step 4 — the Record

**Not yet readable.** agent-bureau's Record began accepting `deployment` and `deployment_status` with DRE-4785 (agent-bureau #2811), merged 2026-09-26 04:14 PT and live in `agent-bureau-console-v1.6.151` at **about 08:08 PT on 2026-09-26** (its first stored message arrived at 08:08:33 PT, during the rollout). The two runs above happened before that, and no train run has happened since. Read at **08:47 PT**:

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

Both are console `v1.6.151`'s own stage statuses ("roll out" at 08:08:32 PT and "live" at 08:08:37 PT, deployment `6680351271`). **The Record holds no decision message yet.** That is expected, not a gap: GitHub made no decision delivery after 08:08 PT. The next train run that reaches its plan job will be the first one the Record can hold. When the operator reads it, that date goes in this section. **Read on 2026-09-27 at about 08:07 PT. See "The Record" in the 2026-09-27 section above.**

### The console's own stage deployment is still `active`

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

## What cannot be met, and what is left

- **The App's `deployment_status` delivery for a held or a no-op decision.** It cannot exist while those decisions are written `inactive`, and `docs/release-decision.md` maps both `held` and `no-op` to `inactive`. For the no-op this was observed on 2026-09-26: the `deployment` was delivered, no status was delivered, and none of the 39 status deliveries in those hours was `inactive`. For the held run it is inferred. Today's four held decisions each carry one `inactive` status, read from GitHub, but the delivery log was not re-read today. Criterion 1 names this delivery. What proves each decision instead is the `deployment` message, which carries the whole payload and is what the Record files. Keeping or rewording that criterion is the CEO's call when he closes the card.
- **The App's delivery log was not re-read on 2026-09-27.** Reading it needs an App JWT minted from App 3350400's private key, and this session's permission guard refused the key read. The delivery evidence for criterion 1 therefore rests on the 09-25 release (`36216605642`). Today's three runs rest on the log, the deployment GitHub holds, and the Record's row.
- **The organization's variables were not read.** Reading them returns `HTTP 403` to the token that checked this record. That no org-level `RELEASE_HOLD` was set rests on the day's unheld runs up to 07:12 PT, as the held-run section explains.
- **Two follow-ups this record points to, not filed as cards.** A Linear search at 08:12 PT found no card for either.
  1. `docs/release-decision.md` says "The Record's App receives every `deployment` and `deployment_status` message the repository produces". For `inactive` statuses that is not true. The file is generated from `scripts/release_decision.py`, so correcting it is a code change for its own card.
  2. The same file defines `re_arm_at` as "the UTC minute the run re-armed itself for". The 09-26 no-op payload carries `"re_arm_at": "2026-09-26T12:00:00Z"`, but its own `reason` says "not re-armed". In code (`Decision.re_arm_at`), the field is the minute the surface may next be released, and it is set whether or not a re-armed run was dispatched. A consumer that reads it as "a re-armed run is waiting" (DRE-4761's Record, or the Train Yard tab) will be wrong. The field's description needs a fix.
- **The working files are not in this repository.** The run logs are in the session's scratch space. Every command is quoted, so a reader can rebuild them.
