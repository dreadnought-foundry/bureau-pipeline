# Verifier FAIL proof — DRE-5232 (epic DRE-2702)

**Status: PROVEN on the sandbox, after three rounds. A Verifier FAIL is no longer a silent deadlock. In rounds 2 and 3 the merge gate woke on the FAIL and declined with a note within 21 seconds. In round 1 it woke and waited, because the critic had not answered yet (§2). When the FAIL's own fix trigger fired, the fix agent started within 4 seconds (rounds 1 and 2). When that trigger was evicted (round 3, 2026-10-04), the console raised "An approved pull request is not merging" 25 minutes after the FAIL. The reconcile sweep re-dispatched the fix agent at 23 minutes 44 seconds, on its first pass after the FAIL was 20 minutes old. The fix then passed the Verifier and, after a second fix round, the critic. Two things are arranged, not natural, and §5 states both: the order (critic first, then Verifier), and the eviction (the FAIL's queued fix run was canceled by hand). The `needs-human` cap hand-off never arose, because no round ran out of fix budget.**

**Where this ran, and why.** It ran on the sandbox, `dreadnought-foundry/agent-bureau-demo`, not on agent-bureau. The CEO ruled on 2026-10-01 that proofs use the local and demo environments: "They should be able to run tests on that and not the production one", quoted on DRE-5488. DRE-5488 put the Verifier stub on the sandbox (#27, merged 2026-10-02 16:50 PT). Where the card says agent-bureau or production, read agent-bureau-demo.

**How this was recorded.** A proof-runner session on the operator's instruction ran this on 2026-10-03, 13:15–13:58 PT. The two fixture cards were sandbox fixtures filed for this proof, DRE-5739 and DRE-5757. A coordinating session filed them, and edited DRE-5757 once (round 2, below). Runs and comments were read from the Actions pages and PR threads. Card receipts and console alerts were read from the console database (`make db-read`). Every time is Pacific (PDT).

**Round 3** (§5) was run by a proof helper on 2026-10-04, 10:25–11:51 PT, to observe the two things rounds 1 and 2 could not: the console alert and the sweep's re-dispatch. The helper filed its own fixture card, DRE-5772, and edited it once. It kept a ledger of every sandbox object it made and removed them all at the end. The console reads in §5 come from `make db-read` against the production console database. That is a read-only, SELECT-only door, and nothing was written.

| Criterion | Result |
|---|---|
| A PR with a critic APPROVE, green CI and a Verifier FAIL bound to its head, with no person intervening between the FAIL and the pipeline's first response | **Met in rounds 2 and 3** (#32, #33). Round 1 (#30) never had APPROVE and FAIL on one head (§2). In round 3 the gate run and the fix trigger were both created before the eviction was staged (§5) |
| The record opens with the live check: the four bureau-pipeline merges in `stable`, DRE-5227's merge on agent-bureau `main`, and the console release that carries it | **Met** (§1, read again for round 3) |
| Names the PR, the FAIL, the gate run the FAIL woke, the `Merge gate: declined` note, the Agent Fix run (or the reconcile run that re-dispatched it), the verify run after the fix and its verdict, and the console alert | **Met.** Rounds 2 and 3 name each one. Round 3 adds the reconcile run that re-dispatched the fix (37223808660) and the console alert row (§5) |
| Elapsed times: FAIL → decline note ≤ 15 min, from a gate run the FAIL woke; FAIL → console alert ≤ 35 min; FAIL → first automatic action ≤ 20 min (trigger fired) or ≤ 35 min (sweep re-dispatch) | **Met.** Decline note: 21 s (round 2) and 17 s (round 3). First action: 4 s on the FAIL's own trigger (round 2), or **23 min 44 s by the sweep's re-dispatch** when the trigger was evicted (round 3). **Console alert: 25 min 16 s** (round 3). See §4 and §6 |
| If the loop fixed it, the Verifier's PASS on the fixed head and the merge | **PASS on the fixed head in all three rounds. Merge in round 1** (#30, by the gate). Rounds 2 and 3 were held as drafts on purpose and closed unmerged (§3, §5) |
| If the loop ran out of budget, `needs-human` with the cap notice | **Did not arise.** No round used more than 2 of the 6 attempts (§4) |
| The record is merged under `docs/` and the CEO closes the card | Open. Under the CEO's rule of 2026-10-02, a proof closes on evidence |

## 1. The changes are live where the proof looks

Read at 13:15:45 PT on 2026-10-03:

```
bureau-pipeline stable = 518ac246419734d0bf4f09465c62f5bde4334cef
  DRE-5228 #615 merge 2e5aefaf7 at 2026-09-30 18:36:07 PT: compare 2e5aefa...stable = ahead (ahead 373, behind 0)
  DRE-5229 #618 merge 8934600da at 2026-10-01 00:47:50 PT: compare 8934600...stable = ahead (ahead 367, behind 0)
  DRE-5230 #619 merge 833e08d87 at 2026-10-01 01:34:42 PT: compare 833e08d...stable = ahead (ahead 364, behind 0)
  DRE-5231 #620 merge db6adf6d6 at 2026-10-01 02:22:09 PT: compare db6adf6...stable = ahead (ahead 360, behind 0)
  DRE-5227 agent-bureau #2970 merge 3371ee557 at 2026-09-30 23:56:39 PT into main: compare ...main = ahead
  first console tag carrying it: agent-bureau-console-v1.6.243 at commit 5a2436464 (2026-10-02 21:41:38 PT)
  newest console tag: agent-bureau-console-v1.6.252 at commit c06075d97 (2026-10-03 12:48:14 PT)
```

Every sandbox run in rounds 1 and 2 checked out the pipeline at `518ac24` (`HEAD is now at 518ac24 Merge pull request #695 …`), and `518ac24` is `stable`.

Read again for round 3, at 10:27:07 PT on 2026-10-04, by a script that asks the GitHub API (`compare` for each merge, and the `console` deployment's statuses):

```
bureau-pipeline stable = af890aadc1e8ddade86780a370ff0102607e85af (2026-10-04 10:06:10 PT, Merge pull request #712)
  DRE-5228 #615 merge 2e5aefaf7: compare ...stable = ahead (ahead 444, behind 0)
  DRE-5229 #618 merge 8934600da: compare ...stable = ahead (ahead 438, behind 0)
  DRE-5230 #619 merge 833e08d87: compare ...stable = ahead (ahead 435, behind 0)
  DRE-5231 #620 merge db6adf6d6: compare ...stable = ahead (ahead 431, behind 0)
  DRE-5227 agent-bureau #2970 merge 3371ee557: compare ...main = ahead
  console deployment 6843743197, sha e10077832 (agent-bureau-console-v1.6.265), status "live" at 2026-10-04 09:48:45 PT
    compare 3371ee557...e10077832 = ahead  (the deployed console carries DRE-5227)
```

`stable` moved once during round 3, at 11:06:12 PT, to `0f4bf78` (#717). The gate and Verify runs before then checked out `af890aa`. The sweep that re-dispatched the fix, and the fix run itself, checked out `0f4bf78` (`HEAD is now at 0f4bf78 Merge pull request #717 …`). Both shas were `stable` when the run read them, and both carry all four merges.

## 2. Round 1: a broken pull request, with the critic slower than the Verifier

Fixture card DRE-5739: "the demo footer shows the app version". It had a `**Design:**` line and `agent:frontend`, so it was in Verifier scope. The fixture PR was [#30](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/30), branch `agent/DRE-5739-footer-version`, opened 13:20:43 PT. The break was deliberate: the footer read `process.env.npm_package_version`, which typechecks and tests green under npm but does not exist in a browser.

| PT | what happened | source |
|---|---|---|
| 13:21:08 | CI `Typecheck · Test · Build` green | run 37151205405 |
| 13:23:46 | **Verifier FAIL** @`13484b14`: "a completely blank white screen" in dev, and "a lone 'v'" in the build | [comment 5973155037](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/30#issuecomment-5973155037), Verify run [37151205907](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37151205907) |
| 13:23:49 | The gate **woke on the FAIL comment** (`issue_comment`): `stranded_fix: 1 Agent Fix run(s) in flight` / `decision=wait` / `reason=no critic verdict yet — wait`. No decline note, because the critic had not answered yet | Merge Gate [37151383191](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37151383191) |
| 13:23:49 | **Agent Fix started** on the FAIL comment: `fix budget: 0 of 2 stop-budget spent, attempt 1 of a 6 ceiling on PR #30` / `fetched a standing Verifier FAIL on 13484b14 (86 lines)` | Agent Fix [37151383236](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37151383236) |
| 13:26:45 | Fix commit `97ccd41e`: "Read the version from package.json via a JSON import so Vite inlines it" | branch history |
| 13:27:03 | The first critic run is canceled by the new head, so no verdict on `13484b14` | QA Review 37151205939 |
| 13:28:04 | `🔧 Fix attempt 1 pushed — CI and critic review re-running.` | [comment 5973188582](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/30#issuecomment-5973188582) |
| 13:29:22 | Critic REQUEST_CHANGES @`97ccd41e`, only for the PR body's `What's new: none` (the work itself "is right") | comment 5973198425 |
| 13:29:38 | **Verifier PASS** @`97ccd41e` | comment 5973200464, Verify run 37151568393 |
| 13:30:44 | Fix attempt 2 pushed (`684e7c59`, What's new line corrected) | comment 5973209054, Agent Fix 37151714101 |
| 13:32:21 | Critic APPROVE @`684e7c59` | comment 5973221686 |
| 13:34:55 | Verifier PASS @`684e7c59` | comment 5973241350, Verify run 37151790658 |
| 13:35:16 | **Merged by the gate:** `decision=merge` / `reason=CI green + critic APPROVE bound to 684e7c594… — merge as qa-bot` / `merged PR #30` | Merge Gate [37152055265](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37152055265) |

**What round 1 shows, and what it does not.** The FAIL was acted on at once, and nobody touched anything. But the state the card describes, critic APPROVE and Verifier FAIL on the same head, never existed. The Verifier answered in 3 minutes. The critic needs 6–8 minutes, and its run was canceled when the fix agent moved the head. So the gate never had an APPROVE to decline, and it waited instead. That is the finding of round 1. While the fix loop admits a Verifier FAIL (DRE-5229), the "approved but failed" standstill arises only when the critic finishes before the Verifier.

## 3. Round 2: the order forced, so the gate's decline note is observed

**How the order was forced (the arrangement, stated plainly).** Fixture card DRE-5757, "the demo footer says it is the sandbox", started **out of Verifier scope**: `agent:engineer`, no `**Design:**` line, and acceptance criteria the change fully met. The fixture PR [#32](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/32), branch `agent/DRE-5757-footer-sandbox`, was opened **as a draft** at 13:43:30 PT, so the gate could not merge it. The gate checks the Verifier (condition 3) before the draft flag (condition 4), so a draft still gets a Verifier decline. After the critic's APPROVE, the card was edited to its second version: a `**Design:**` line, `agent:frontend`, and one new criterion the code did not meet ("`sandbox` … is a link"). Verify was then dispatched once, by hand. From the FAIL onward, nobody touched the pull request.

| PT | what happened | source |
|---|---|---|
| 13:43:51 | Verify (on open) skipped: `scope decision: in_scope=false (design=false ui_role=false multi=false card=read buckets=frontend)` | Verify run 37152567740 |
| 13:43:57 | CI green | run 37152567387 |
| 13:44:56 | **Critic APPROVE** @`25d99553` | [comment 5973317333](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/32#issuecomment-5973317333) |
| 13:45:15 | Gate: `⏸️ Merge gate: waiting for human merge — the pull request is still a draft …` | comment 5973319868 |
| 13:45:31 | Card DRE-5757 edited to its second version (description, `agent:engineer` → `agent:frontend`) | coordinating session |
| 13:48:07 | Verify dispatched by hand: `gh workflow run verify.yml -R dreadnought-foundry/agent-bureau-demo -f pr_number=32`. Scope: `in_scope=true (design=true ui_role=true multi=false card=read buckets=frontend)` | Verify run [37152836603](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37152836603) |
| **13:51:24** | **Verifier FAIL** @`25d99553`, with APPROVE and green CI standing on the same head: "the link is missing … 'sandbox' looks like plain gray text, and clicking it does nothing" | [comment 5973367105](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/32#issuecomment-5973367105) |
| 13:51:28 | The gate **woke on the FAIL comment** (`issue_comment`): `stranded_fix: 1 Agent Fix run(s) in flight` / `decision=hold` / `reason=latest verifier verdict is not PASS — holding` | Merge Gate [37153036068](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37153036068) |
| **13:51:28** | **Agent Fix started** on the FAIL comment: `bureau-card: DRE-5757` / `fix budget: 0 of 2 stop-budget spent, attempt 1 of a 6 ceiling on PR #32` / `fetched a standing Verifier FAIL on 25d99553 (64 lines)` | Agent Fix [37153036238](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37153036238) |
| **13:51:45** | **`⏸️ Merge gate: declined @25d99553373746112354d3f2c338db85bf889e36 — latest verifier verdict is not PASS — holding`** / "Not merged. The gate looks again on the next CI completion, review or sweep, and merges once nothing holds it." | [comment 5973369771](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/32#issuecomment-5973369771) |
| 13:51:51 | Card receipt: `🔧 Fix agent dispatched (attempt 1, 0/2 rounds without progress)` | DRE-5757, console database |
| 13:53:07 | Fix commit `b943ccc0` ("address review findings (attempt 1)") | branch history |
| 13:54:24 | `🔧 Fix attempt 1 pushed — CI and critic review re-running.` | [comment 5973390361](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/32#issuecomment-5973390361) |
| 13:55:03 | Critic APPROVE @`b943ccc0`: "that word is a working link" | comment 5973395508 |
| 13:56:15 | **Verifier PASS** @`b943ccc0` | [comment 5973404997](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/32#issuecomment-5973404997), Verify run 37153139076 |
| 13:57:12 | Closed unmerged by the proof runner, branch deleted. It was a draft fixture, never meant to merge | comment 5973412034 |

A second Agent Fix run, 37153056452, started at 13:51:48 on the gate's own comment. It was canceled by the first run's concurrency group, so only one fix ran.

## 4. The timing

| from the round-2 FAIL (13:51:24) to | elapsed | bound | verdict |
|---|---|---|---|
| the gate's `Merge gate: declined` note, from a gate run the FAIL comment woke | **21 s** | 15 min | **within** |
| the first automatic action (Agent Fix run start, on the FAIL comment) | **4 s** | 20 min | **within** |
| the fix commit on the branch | 1 min 43 s | — | — |
| the console's `An approved pull request is not merging` alert | **not raised** | 35 min | **not observed** |

**Why the console alert was not raised in round 2, and cannot be while the FAIL's own fix trigger fires.** `console/backend/monitors/pipeline_stuck.py` calls a PR stuck only after it has waited at the merge gate `MERGE_GATE_STUCK_MINUTES` (20). In round 2 the head moved 1 min 43 s after the FAIL, and the fix passed both reviewers 4 min 51 s after it. Read at 14:00 PT, the console's `system_alert` table holds no alert for DRE-5757 or DRE-5739 after 13:15 PT. The only `not merging` alert on record is `pipeline_stuck:DRE-5401`, raised 2026-10-01 13:54 PT and resolved 15:39 PT. So round 2 did not observe the alert half of DRE-5227. Round 3 (§5) observed it, on the case where the alert matters: the FAIL's fix trigger is evicted and nothing else moves the pull request.

**Budget and hand-off (DRE-5231).** No round reached the fix loop's cap. Round 1 used 2 attempts, round 2 used 1 and round 3 used 2, so no `needs-human` hand-off occurred and none is shown.

## 5. Round 3: the FAIL's fix trigger evicted, so the sweep and the console have to act

**Why a third round.** Rounds 1 and 2 left two parts of the card unobserved: the console alert (DRE-5227) and the sweep's re-dispatch (DRE-5230). Both exist for one case, when GitHub cancels the FAIL's own Agent Fix trigger while it is still pending (the DRE-2810 eviction). The sandbox's sweep (`reconcile.yml`) has been back on since the morning of 2026-10-04. Its first run after the pause was at 07:52 PT. It runs with a WIP cap of 0, which skips promotion and the work-lane phases. The re-dispatch route `redispatch_standing_verdicts` still runs on such a pass: it is a backstop, and it is not in `OFF_RAIL_SKIPPED`.

**What was arranged, stated plainly.** Four things were done by hand. Each is in the table below with its time.
1. **The order**, as in round 2. Fixture card DRE-5772, "the demo footer says it was built by agents", started out of Verifier scope (`agent:engineer`, no `**Design:**` line). The fixture PR [#33](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/33), branch `agent/DRE-5772-footer-built-by-agents`, opened as a draft. After the critic's APPROVE, the card was edited to its second version. That version added a `**Design:**` line, changed `agent:engineer` to `agent:frontend`, and added one criterion the code did not meet: "`built by agents` … is a link". Verify was then dispatched by hand.
2. **The eviction.** A watcher script polled the Agent Fix runs once a second. It canceled the run that the FAIL comment created, 3 seconds after it was created, while it was still `queued`. The job never reached a runner and ran zero steps. That is the state a DRE-2810 eviction leaves behind. The sweep's own eviction detector describes such runs as "CANCELLED … without starting a single job". Both of the pipeline's first responses happened before the cancel and without it: the gate run was created at the same second as the fix trigger, and the gate posted its decline on its own.
3. **A red check left by a credential outage, cleared.** The PR's first critic run died on `401 OAuth access token has been revoked`. The sandbox's `CLAUDE_CODE_OAUTH_TOKEN` was replaced at 10:30:41 PT, while that run was in flight, and the run's retry still carried the old token. The helper dispatched a fresh critic run, which gave the APPROVE. The dead run's failed `call / review` check stayed on the head, though. The console then read the PR as `APPROVED_BLOCKED` with no `checks_green_at`, not as at the merge gate. So after the FAIL, the helper re-ran that original run (10:55:36). It turned green, and the console's row moved to `MERGE_GATE` (§7).
4. **Draft, then ready, then draft again.** The PR was marked ready at 10:54:08, after the FAIL and the decline. The console's merge-gate reading does not apply to a draft (`DRAFT_REWRITTEN_REASONS`). It went back to draft at 11:20:24. By then the alert and the re-dispatch had both been observed, and the draft kept the fixed head from merging into the sandbox's `main`.

From 10:55:36 until the sweep's re-dispatch at 11:17:20, nobody touched the PR, the card or any run.

| PT (2026-10-04) | what happened | source |
|---|---|---|
| 10:25:57 | Draft PR #33 opened: `b699f9c5` test (RED), `bb6a75fb` feat (the footer gains `· built by agents`, plain text) | PR #33 |
| 10:26:24 | CI `Typecheck · Test · Build` green | run 37220488786 |
| 10:31:21 | Verify (on open) skipped: `scope decision: in_scope=false (design=false ui_role=false multi=false card=read buckets=frontend)` | Verify run 37220489109 |
| 10:33:39 | `🔎 QA Critic could not run (infra error)`. Both attempts ended `api_error_status: 401` / `OAuth access token has been revoked` | comment 5982617563, QA Review run 37220489170 |
| 10:34:32 | Critic dispatched by hand: `gh workflow run qa-review.yml -R dreadnought-foundry/agent-bureau-demo -f pr_number=33` | QA Review run 37221029980 |
| 10:43:20 | **Critic APPROVE** @`bb6a75fb` | [comment 5982696197](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/33#issuecomment-5982696197) |
| 10:43:39 | Gate: `⏸️ Merge gate: waiting for human merge — the pull request is still a draft …` | comment 5982698531 |
| 10:43:48 | Card DRE-5772 edited to its second version | the helper |
| 10:44:02 | Verify dispatched by hand: `gh workflow run verify.yml -R dreadnought-foundry/agent-bureau-demo -f pr_number=33`. It queued for a runner until 10:48:34. Scope: `in_scope=true (design=true ui_role=true multi=false card=read buckets=frontend)` | Verify run [37221632596](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37221632596) |
| **10:53:36** | **Verifier FAIL** @`bb6a75fb`, with APPROVE and green CI standing on the same head: "'built by agents' is plain text, not a link. A visitor who clicks it gets nothing." | [comment 5982778131](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/33#issuecomment-5982778131) |
| 10:53:40 | The FAIL comment's Agent Fix run is created, `status=queued` | Agent Fix 37222247836 |
| 10:53:40 | The gate **woke on the FAIL comment** (`issue_comment`): `stranded_fix: 0 Agent Fix run(s) in flight` / `decision=hold` / `reason=latest verifier verdict is not PASS — holding` | Merge Gate [37222247865](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37222247865) |
| 10:53:43 | **Eviction staged:** run 37222247836 canceled while `queued`. Afterward: `conclusion=cancelled`, job `call / fix PR #33` `cancelled`, `runner=` (none), `steps=[]` | watcher log |
| **10:53:53** | **`⏸️ Merge gate: declined @bb6a75fb64f7208b541c2f800188790938c1e620 — latest verifier verdict is not PASS — holding`** | [comment 5982780429](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/33#issuecomment-5982780429) |
| 10:53:56 | The decline note's own Agent Fix run: `skipped` (a qa-bot comment with no verdict does not pass the job's `if:`) | Agent Fix 37222266034 |
| 10:54:08 | PR marked ready for review (`gh pr ready 33`) | the helper |
| 10:55:22 | Console row: `waiting_reason=APPROVED_BLOCKED`, `checks_green_at` empty. The dead critic run's `call / review` check was still red on the head | `make db-read` |
| 10:55:32 | Sweep run, 2 minutes after the FAIL. Too early for the route's 20-minute rule; it printed only `stale-merge-ref: PR #33 — no-failure` | Reconcile 37222367615 |
| 10:55:36 | The dead critic run re-run (`gh run rerun 37220489170`). Attempt 2 green at 10:58:16, with no new verdict comment | QA Review 37220489170 |
| 11:06:04 | Console row: `waiting_reason=MERGE_GATE`, `checks_green_at=17:58:16Z` (10:58:16 PT) | `make db-read` |
| 11:16:42 | Sweep run starts (`schedule`) | Reconcile [37223808660](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37223808660) |
| **11:17:20** | **The sweep re-dispatched the fix agent:** `evicted-verdict: PR #33 has a standing FAIL on bb6a75fb64f7208b541c2f800188790938c1e620 from 23m ago with no fix run — re-dispatching fix agent` | Reconcile 37223808660 log; Agent Fix [37223852030](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37223852030) (`workflow_dispatch`) |
| 11:17:21 | Receipt: `🔁 The reconcile sweep re-dispatched the fix agent (DRE-3130). This pull request has carried a blocking verdict (the critic's REQUEST_CHANGES or the Verifier's FAIL) on its current head for 23 minutes with no fix run working it …` | [comment 5982972400](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/33#issuecomment-5982972400) |
| **11:18:52** | **Console alert raised:** `pipeline_stuck:DRE-5772`, `WARN`, **"An approved pull request is not merging"**: "DRE-5772's pull request has been approved with green checks for 20 minutes and has not merged. The merge gate is holding it; open the pull request, where the gate's note says why." | `system_alert`, `make db-read` |
| 11:19:52 | The re-dispatched fix job starts (it had queued for a runner): `bureau-card: DRE-5772` / `fix budget: 0 of 2 stop-budget spent, attempt 1 of a 6 ceiling on PR #33` / `fetched a standing Verifier FAIL on bb6a75fb (63 lines)` | Agent Fix 37223852030 |
| 11:20:24 | PR converted back to draft (`gh pr ready 33 --undo`) | the helper |
| 11:23:47 | Fix commit `47e365f0` ("address review findings (attempt 1)"); `🔧 Fix attempt 1 pushed` at 11:24:03 | comment 5983027107 |
| 11:24:24 | Console alert resolved (the head moved, so the monitor no longer calls it stuck) | `system_alert.resolved_at` |
| 11:30:15 | **Verifier PASS** @`47e365f0` | [comment 5983077273](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/33#issuecomment-5983077273), Verify run 37224284663 |
| 11:31:50 | Critic REQUEST_CHANGES @`47e365f0` (`cause:unmet-criteria`). The link was right. The PR body's What's-new line used the phrase "pull requests", and the new test did not catch the link losing its gray color | comment 5983090513 |
| 11:35:43 | Fix commit `ca50d0bc` (attempt 2); `🔧 Fix attempt 2 pushed` at 11:35:58 | comment 5983125327 |
| 11:39:51 | Critic APPROVE @`ca50d0bc` | comment 5983157085 |
| 11:42:14 | **Verifier PASS** @`ca50d0bc` | [comment 5983177019](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/33#issuecomment-5983177019) |
| 11:50:43 | Closed unmerged by the helper, branch deleted. Card DRE-5772 canceled at 11:50:46 | PR #33 |

A natural eviction also happened during round 3, on a run that did no work. The sweep's receipt comment queued Agent Fix run 37223858776 behind the re-dispatched run. The `Fix attempt 1 pushed` comment's run then canceled it while it was still pending. That is the DRE-2810 mechanism the sweep warns about on every pass (`fix-concurrency: WARN — agent-fix.yml's concurrency group puts a bot notice and a qa-bot REQUEST_CHANGES verdict in the same group`). The sandbox's stub keys its group on the PR number only, while agent-bureau's stub keys its group on the comment body (DRE-5227).

## 6. Round 3 timing

| from the round-3 FAIL (10:53:36) to | elapsed | bound | verdict |
|---|---|---|---|
| the gate's `Merge gate: declined` note, from a gate run the FAIL comment woke | **17 s** | 15 min | **within** |
| the FAIL's own Agent Fix trigger (created; then evicted) | 4 s | — | — |
| the sweep's re-dispatch of the fix agent (first automatic action after the eviction) | **23 min 44 s** | 35 min (sweep re-dispatch) | **within** |
| the console's `An approved pull request is not merging` alert | **25 min 16 s** | 35 min | **within** |
| the re-dispatched fix job starting work (runner queue) | 26 min 16 s | — | — |
| the Verifier's PASS on the fixed head | 36 min 39 s | — | — |

**Why the sweep took 23 minutes 44 seconds.** The route waits until a verdict is 20 minutes old, so the earliest it could act was 11:13:36. The sweep before that ran at 10:55:32, when the FAIL was 2 minutes old. The next sweep ran at 11:16:42 and re-dispatched on that pass, 3 minutes 44 seconds after the FAIL became eligible. GitHub's 15-minute timer was firing late that morning. The sandbox's sweeps came as a mix of `schedule` runs and `workflow_dispatch` runs started by `smeed652`, between 3 and 23 minutes apart (10:08:58, 10:29:49, 10:33:00, 10:55:32, 11:16:42).

**Why the alert took 25 minutes 16 seconds, and what it is measured from.** The monitor counts 20 minutes from the moment the PR reached the gate (`enrich.gate_reached_at`, the later of green checks and the critic). It does not count from the FAIL. Here the gate was reached at 10:58:16, when the re-run cleared the red check, and the alert came 20 minutes 36 seconds after that, on the monitor's next 60-second tick. Without the credential outage, the head would have had green checks since 10:26:24 and the APPROVE since 10:43:20. The 20 minutes would then have run out at 11:03:20, about 10 minutes after the FAIL.

## 7. Findings

- **The fix loop's eviction fallback works end to end.** A Verifier FAIL whose fix trigger never runs is surfaced on the console, with the right words, while the FAIL stands. The sweep then restarts the fix within one sweep of the FAIL turning 20 minutes old. The fix converged in two attempts, and nobody acted on the pull request.
- **A dead critic run's red check hides a PR from the stuck monitor.** While the head carried the dead run's failed `call / review` check, the console read the PR as `APPROVED_BLOCKED` with no `checks_green_at`, even though a dispatched re-review had approved it. `_gate_stalled_minutes` answers only for `MERGE_GATE`, so the "not merging" alert could not have fired. A re-review dispatched with `gh workflow run` does not replace that check on the head. The helper took that route first, and the sweep's own crashed-review recovery takes it too: `recover_crashed_reviews` → `_nudge` → `gh workflow run`. Re-running the original run does. This is recorded as observed. No card has been filed for it yet.

## What is not proven, and why

- **The `needs-human` hand-off at the cap (DRE-5231).** No round ran the fix loop out of budget. That would need a FAIL the fix agent cannot fix six times over.
- **A natural eviction of a verdict trigger, followed by recovery.** Round 3's eviction was staged by canceling the queued run. The resulting state matches what DRE-2810 leaves behind: canceled, no job started, zero steps. The sweep's eviction detector lists two verdict triggers on the sandbox that were canceled before starting on 2026-10-03: Agent Fix 37151729722 and 37153056452. In both cases another fix run was already working the pull request, so neither left a verdict standing with nothing working it.
- **The ordering was arranged (rounds 2 and 3).** The FAIL comes from a requirement added to the card after the critic's review, not from code the critic missed. It is a fair stand-in for "the critic answered first", but it is a stand-in.
- **Production.** All three rounds ran on the sandbox, per the CEO's ruling of 2026-10-01.

## Where each source disagreed, and which was taken

| Fact | Source A | Source B | Taken |
|---|---|---|---|
| Where the proof runs | The card's body: "in production on agent-bureau" | The card's plan change of 2026-10-01: agent-bureau-demo | The plan change, the CEO's later ruling |
| Fix receipt time, round 2 | PR comment 13:54:24 | the card's `🔧 Fix agent dispatched` 13:51:51 | Both. They are different receipts: the start (card) and the push (PR) |
| Whether the sandbox's sweep is on (round 3) | The proof brief: `reconcile.yml` disabled for the fleet pause | `gh workflow list --all` at 10:17 PT: `Reconcile active`, with runs from 07:52 PT | The workflow list. The coordinator confirmed it at 10:24 PT and gave 07:40 PT as the time it was turned back on |
| The re-dispatch time, round 3 | The sweep's log line, stamped 11:17:31 | Agent Fix run 37223852030 created at 11:17:20, receipt at 11:17:21 | The run's creation time. The log line is stamped when the step's output is flushed, after the dispatch |
| When the round-3 PR reached the gate | The critic's APPROVE, 10:43:20 | The console's `checks_green_at`, 10:58:16, after the red check cleared | The console's, because the monitor reads that one (§6) |
