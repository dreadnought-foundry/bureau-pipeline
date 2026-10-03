# Verifier FAIL proof — DRE-5232 (epic DRE-2702)

**Status: PARTIAL. A Verifier FAIL is no longer a silent deadlock. The merge gate woke on it and declined the pull request with a note 21 seconds later. The fix agent started 4 seconds after the FAIL, and its fix was approved and passed the Verifier within 5 minutes. Nobody touched the pull request in between. The console's "An approved pull request is not merging" alert was not observed. It cannot fire while the fix agent is on, because the fix moves the head long before the alert's 20-minute threshold.**

**Where this ran, and why.** It ran on the sandbox, `dreadnought-foundry/agent-bureau-demo`, not on agent-bureau. The CEO ruled on 2026-10-01 that proofs use the local and demo environments: "They should be able to run tests on that and not the production one", quoted on DRE-5488. DRE-5488 put the Verifier stub on the sandbox (#27, merged 2026-10-02 16:50 PT). Where the card says agent-bureau or production, read agent-bureau-demo.

**How this was recorded.** A proof-runner session on the operator's instruction ran this on 2026-10-03, 13:15–13:58 PT. The two fixture cards were sandbox fixtures filed for this proof, DRE-5739 and DRE-5757. A coordinating session filed them, and edited DRE-5757 once (round 2, below). Runs and comments were read from the Actions pages and PR threads. Card receipts and console alerts were read from the console database (`make db-read`). Every time is Pacific (PDT).

| Criterion | Result |
|---|---|
| A PR with a critic APPROVE, green CI and a Verifier FAIL bound to its head, with no person intervening between the FAIL and the pipeline's first response | **Met in round 2** (#32). Round 1 (#30) never had APPROVE and FAIL on one head (§2) |
| The record opens with the live check: the four bureau-pipeline merges in `stable`, DRE-5227's merge on agent-bureau `main`, and the console release that carries it | **Met** (§1) |
| Names the PR, the FAIL, the gate run the FAIL woke, the `Merge gate: declined` note, the Agent Fix run, the verify run after the fix and its verdict, and the console alert | **Met except the console alert**, which was not raised (§3, §4) |
| Elapsed times: FAIL → decline note ≤ 15 min, from a gate run the FAIL woke; FAIL → console alert ≤ 35 min; FAIL → first automatic action ≤ 20 min | **Decline note: 21 s**, gate run 37153036068 woken by the FAIL comment. **First action: 4 s**, Agent Fix run 37153036238. **Console alert: not observed** (§4) |
| If the loop fixed it, the Verifier's PASS on the fixed head and the merge | **PASS on the fixed head in both rounds. Merge in round 1** (#30, by the gate). Round 2 was a draft by design and was closed unmerged (§3) |
| The record is merged under `docs/` and the CEO closes the card | Open. Under the CEO's rule of 2026-10-02 a proof closes on evidence, and this one is partial |

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

Every sandbox run below checked out the pipeline at `518ac24` (`HEAD is now at 518ac24 Merge pull request #695 …`), and `518ac24` is `stable`.

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
| **13:51:45** | **`⏸️ Merge gate: declined @25d99553373746112354d3f2c338db85bf889e36 — latest verifier verdict is not PASS — holding`** / "Not merged. The gate looks again on the next CI completion, review or sweep, and merges once nothing holds it." | [comment 5973369771](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/32#issuecomment-5973369771) |
| **13:51:28** | **Agent Fix started** on the FAIL comment: `bureau-card: DRE-5757` / `fix budget: 0 of 2 stop-budget spent, attempt 1 of a 6 ceiling on PR #32` / `fetched a standing Verifier FAIL on 25d99553 (64 lines)` | Agent Fix [37153036238](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37153036238) |
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

**Why the console alert was not raised, and cannot be while the fix agent is on.** `console/backend/monitors/pipeline_stuck.py` calls a PR stuck only after it has waited at the merge gate `MERGE_GATE_STUCK_MINUTES` (20). In round 2 the head moved 1 min 43 s after the FAIL, and the fix passed both reviewers 4 min 51 s after it. Read at 14:00 PT, the console's `system_alert` table holds no alert for DRE-5757 or DRE-5739 after 13:15 PT. The only `not merging` alert on record is `pipeline_stuck:DRE-5401`, raised 2026-10-01 13:54 PT and resolved 15:39 PT. So the alert half of DRE-5227 was not observed live. It is pinned by its tests on agent-bureau `main` and by nothing observed here.

**Budget and hand-off (DRE-5231).** Neither round reached the fix loop's cap. Round 1 used 2 attempts and round 2 used 1, so no `needs-human` hand-off occurred and none is shown.

## What is not proven, and why

- **The console's stuck alert (DRE-5227).** As above: the fix moves the head within minutes, so 20 minutes at the gate never happens while Agent Fix is on. Observing it would need the fix agent off, or out of budget, on a PR with APPROVE and FAIL standing.
- **The sweep's re-dispatch (DRE-5230).** The FAIL's own trigger fired both times, so the sweep's fallback was never needed. The sweep (`reconcile.yml`) is off in the sandbox in any case.
- **Round 2's ordering was arranged.** The FAIL comes from a requirement added to the card after the critic's review, not from code the critic missed. It is a fair stand-in for "the critic answered first", but it is a stand-in.

## Where each source disagreed, and which was taken

| Fact | Source A | Source B | Taken |
|---|---|---|---|
| Where the proof runs | The card's body: "in production on agent-bureau" | The card's plan change of 2026-10-01: agent-bureau-demo | The plan change, the CEO's later ruling |
| Fix receipt time, round 2 | PR comment 13:54:24 | the card's `🔧 Fix agent dispatched` 13:51:51 | Both. They are different receipts: the start (card) and the push (PR) |
